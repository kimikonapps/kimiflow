#!/usr/bin/env bash
# kimiflow — review-gate resolver (read-only). Single tested source of truth for the binary
# Phase-4/Phase-7 review gate. Reads a round's findings files, echoes a machine verdict, FAILS
# CLOSED on any incompleteness/malformation. Orchestrator-invoked (not a Claude Code event hook).
#
# LANGUAGE-AGNOSTIC: operates only on the findings abstraction `FINDING <SEVERITY> <ref> :: <reason>`
# — no source, no file-extension/keyword/per-language logic. The ONLY fixed marker is the keyword
# `FINDING <SEVERITY>` at column 0; <ref> and <reason> may be arbitrary UTF-8. Output is stable
# reason-codes (the orchestrator localizes for display).
#
# Usage: resolve-review-gate.sh <findings-dir> --round <N> --expect <lensA,lensB> [--cap 3]
# Output (one TAB line, exit 0): <VERDICT>\t<open_count|->\t<reason_code>\t<detail>
#   VERDICT ∈ {OPEN,CLOSED}; reason_code ∈ {clean,open-findings,incomplete,malformed,oscillation,reappeared,cap-reached}
set -u
emit() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}"; exit 0; }

dir=""; round=""; expect=""; cap=3
while [ "$#" -gt 0 ]; do
  case "$1" in
    --round)  round="${2:-}"; shift 2 || shift ;;
    --expect) expect="${2:-}"; shift 2 || shift ;;
    --cap)    cap="${2:-3}";   shift 2 || shift ;;
    -*)       shift ;;
    *)        [ -z "$dir" ] && dir="$1"; shift ;;
  esac
done
case "$round" in ''|*[!0-9]*) emit CLOSED - malformed "bad-or-missing --round" ;; esac
case "$cap"   in ''|*[!0-9]*) emit CLOSED - malformed "bad --cap" ;; esac
[ -n "$dir" ]    || emit CLOSED - malformed "missing findings-dir"
[ -n "$expect" ] || emit CLOSED - malformed "missing --expect"

FINDING_RE='^FINDING (BLOCKER|HIGH|MEDIUM|LOW) .+ :: .+$'

open_count=0
cur_ids=""   # newline-list of "<SEV> <ref>" identities for open findings this round
OLDIFS="$IFS"; IFS=','; set -- $expect; IFS="$OLDIFS"
for lens in "$@"; do
  f="$dir/r${round}-${lens}.md"
  [ -f "$f" ] || emit CLOSED - incomplete "missing r${round}-${lens}.md"
  [ -s "$f" ] || emit CLOSED - incomplete "empty r${round}-${lens}.md"
  if [ "$(grep -c '' "$f")" -eq 1 ] && [ "$(head -n1 "$f")" = "NONE" ]; then
    continue
  fi
  lineno=0
  while IFS= read -r ln || [ -n "$ln" ]; do
    lineno=$((lineno + 1))
    printf '%s\n' "$ln" | grep -qE "$FINDING_RE" || emit CLOSED - malformed "r${round}-${lens}.md:${lineno}"
    case "$ln" in
      'FINDING BLOCKER '*|'FINDING HIGH '*)
        open_count=$((open_count + 1))
        id="${ln#FINDING }"; id="${id%% :: *}"
        cur_ids="${cur_ids}${id}
"
        ;;
    esac
  done < "$f"
done

[ "$open_count" -eq 0 ] && emit OPEN 0 clean

# ---- open_count > 0: anti-oscillation (cap → oscillation → reappeared → open-findings) ----
[ "$round" -gt "$cap" ] && emit CLOSED "$open_count" cap-reached "round ${round} > cap ${cap}"

prev=$((round - 1))
prev_exists=false
for pf in "$dir"/r${prev}-*.md; do [ -e "$pf" ] && { prev_exists=true; break; }; done

if [ "$prev" -ge 1 ] && [ "$prev_exists" = true ]; then
  prev_open="$(grep -hE '^FINDING (BLOCKER|HIGH) ' "$dir"/r${prev}-*.md 2>/dev/null | grep -c '' )"
  [ "$open_count" -ge "$prev_open" ] && emit CLOSED "$open_count" oscillation "${prev_open}->${open_count}"
  if [ "$prev" -ge 2 ]; then
    while IFS= read -r id; do
      [ -n "$id" ] || continue
      grep -qF "FINDING $id :: " "$dir"/r${prev}-*.md 2>/dev/null && continue
      k=1
      while [ "$k" -le $((prev - 1)) ]; do
        if grep -qF "FINDING $id :: " "$dir"/r${k}-*.md 2>/dev/null; then
          emit CLOSED "$open_count" reappeared "$id"
        fi
        k=$((k + 1))
      done
    done <<EOF
$cur_ids
EOF
  fi
fi

emit CLOSED "$open_count" open-findings
