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
# Usage: resolve-review-gate.sh <findings-dir> --round <N> --expect <lensA,lensB> [--epoch-start 1] [--cap 3]
# Output (one TAB line, exit 0): <VERDICT>\t<open_count|->\t<reason_code>\t<detail>
#   VERDICT ∈ {OPEN,CLOSED}; reason_code ∈ {clean,open-findings,incomplete,malformed,oscillation,reappeared,cap-reached}
# R2 invariant targets: hooks/resolve-review-gate.sh; --round <N> --expect <lensCSV>; --expect code-verified
set -u
emit() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}"; exit 0; }

dir=""; round=""; expect=""; cap=3; epoch_start=1; epoch_arg=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --round)       round="${2:-}";       shift 2 || shift ;;
    --expect)      expect="${2:-}";      shift 2 || shift ;;
    --cap)         cap="${2:-3}";         shift 2 || shift ;;
    --epoch-start) epoch_start="${2:-}"; epoch_arg=true; shift 2 || shift ;;
    -*)            shift ;;
    *)             [ -z "$dir" ] && dir="$1"; shift ;;
  esac
done
case "$round"       in ''|*[!0-9]*) emit CLOSED - malformed "bad-or-missing --round" ;; esac
case "$cap"         in ''|*[!0-9]*) emit CLOSED - malformed "bad --cap" ;; esac
case "$epoch_start" in ''|*[!0-9]*) emit CLOSED - malformed "bad --epoch-start" ;; esac
[ -n "$dir" ]    || emit CLOSED - malformed "missing findings-dir"
[ -n "$expect" ] || emit CLOSED - malformed "missing --expect"
# Normalize base-10 so a zero-padded round (e.g. 08) can't trip octal arithmetic later.
round=$((10#$round)); cap=$((10#$cap)); epoch_start=$((10#$epoch_start))
if [ "$epoch_arg" = true ]; then
  [ "$epoch_start" -ge 1 ] && [ "$epoch_start" -le "$round" ] && [ "$epoch_start" -le "$cap" ] \
    || emit CLOSED - malformed "invalid --epoch-start ${epoch_start} for round ${round}, cap ${cap}"
fi

# The round ledger is global for the caller's expected lens set. A caller cannot bypass the
# revision budget by submitting a clean file after the cap; stop before reading/counting it.
[ "$round" -gt "$cap" ] && emit CLOSED - cap-reached "round ${round} > cap ${cap}"

# List existing findings files for the --expect lens set at a given round (newline-delimited).
# Phase 4 (lenses A/B) and Phase 7 (code-verified) share the findings dir with overlapping
# round numbers; every cross-round check MUST be scoped to --expect, never a bare r<N>-*.md glob.
expected_round_files() {
  local rnum="$1" lens f OLDIFS
  OLDIFS="$IFS"; IFS=','; set -- $expect; IFS="$OLDIFS"
  for lens in "$@"; do
    f="$dir/r${rnum}-${lens}.md"
    [ -f "$f" ] && printf '%s\n' "$f"
  done
}
# True (return 0) iff <id> appears as an open/any FINDING line in any --expect file of <round>.
id_in_round() {
  local target="$1" rnum="$2" f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    grep -qF "FINDING $target :: " "$f" 2>/dev/null && return 0
  done <<EOF
$(expected_round_files "$rnum")
EOF
  return 1
}

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
[ "$round" -ge "$cap" ] && emit CLOSED "$open_count" cap-reached "round ${round} >= cap ${cap}"

prev=$((round - 1))
prev_files="$(expected_round_files "$prev")"
prev_exists=false
[ -n "$prev_files" ] && prev_exists=true

if [ "$prev" -ge "$epoch_start" ] && [ "$prev_exists" = true ]; then
  prev_open=0
  while IFS= read -r pf; do
    [ -n "$pf" ] || continue
    n="$(grep -cE '^FINDING (BLOCKER|HIGH) ' "$pf" 2>/dev/null || true)"
    prev_open=$((prev_open + n))
  done <<EOF
$prev_files
EOF
  [ "$open_count" -ge "$prev_open" ] && emit CLOSED "$open_count" oscillation "${prev_open}->${open_count}"
  if [ "$prev" -gt "$epoch_start" ]; then
    while IFS= read -r id; do
      [ -n "$id" ] || continue
      id_in_round "$id" "$prev" && continue
      k="$epoch_start"
      while [ "$k" -le $((prev - 1)) ]; do
        id_in_round "$id" "$k" && emit CLOSED "$open_count" reappeared "$id"
        k=$((k + 1))
      done
    done <<EOF
$cur_ids
EOF
  fi
fi

emit CLOSED "$open_count" open-findings
