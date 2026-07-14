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
# Usage: resolve-review-gate.sh <findings-dir> --round <N> --expect <lensA,lensB> [--gate plan|code] [--epoch-start 1] [--cap 3]
# Output (one TAB line, exit 0): <VERDICT>\t<open_count|->\t<reason_code>\t<detail>
#   VERDICT ∈ {OPEN,CLOSED}; reason_code ∈ {clean,open-findings,incomplete,malformed,oscillation,reappeared,cap-reached}
# R2 invariant targets: hooks/resolve-review-gate.sh; --round <N> --expect <lensCSV>; --expect code-verified
set -u
emit() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}"; exit 0; }
FINDING_RE='^FINDING (BLOCKER|HIGH|MEDIUM|LOW) .+ :: .+$'

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    return 1
  fi
}

state_value() {
  local file="$1" wanted="$2"
  awk -v wanted="$wanted" '
    {
      line=$0
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      pos=index(line, ":")
      if (!pos) next
      label=substr(line, 1, pos-1)
      value=substr(line, pos+1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", label)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (tolower(label) == tolower(wanted)) { print value; exit }
    }
  ' "$file" 2>/dev/null
}

dir=""; round=""; expect=""; gate=""; cap=3; epoch_start=1; epoch_arg=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --round)       round="${2:-}";       shift 2 || shift ;;
    --expect)      expect="${2:-}";      shift 2 || shift ;;
    --gate)        gate="${2:-}";        shift 2 || shift ;;
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
case "$gate" in ''|plan|code) ;; *) emit CLOSED - malformed "bad --gate" ;; esac
# Normalize base-10 so a zero-padded round (e.g. 08) can't trip octal arithmetic later.
round=$((10#$round)); cap=$((10#$cap)); epoch_start=$((10#$epoch_start))
if [ "$epoch_arg" = true ]; then
  [ "$epoch_start" -ge 1 ] && [ "$epoch_start" -le "$round" ] && [ "$epoch_start" -le "$cap" ] \
    || emit CLOSED - malformed "invalid --epoch-start ${epoch_start} for round ${round}, cap ${cap}"
fi

if [ "$epoch_arg" = true ] && [ -n "$gate" ]; then
  run_dir="$(dirname "${dir%/}")"
  recovery="$run_dir/RECOVERY.md"
  basis="$run_dir/PLAN.md"
  [ -s "$basis" ] || emit CLOSED - malformed "missing ${gate} strategy basis"
  basis_fingerprint="$(sha256_file "$basis")" \
    || emit CLOSED - malformed "sha256 unavailable"
  baseline_lines="$(grep -E "^<!-- kimiflow:strategy gate=${gate} epoch-start=1 fingerprint=[A-Fa-f0-9]{64} -->$" "$recovery" 2>/dev/null || true)"
  [ "$(printf '%s\n' "$baseline_lines" | grep -c .)" -eq 1 ] \
    || emit CLOSED - malformed "missing or duplicate ${gate} strategy baseline"
  baseline="$baseline_lines"
  baseline_fingerprint="${baseline##* fingerprint=}"
  baseline_fingerprint="${baseline_fingerprint% -->}"
  baseline_fingerprint="$(printf '%s' "$baseline_fingerprint" | tr '[:upper:]' '[:lower:]')"
  if [ "$epoch_start" -eq 1 ]; then
    [ "$baseline_fingerprint" = "$basis_fingerprint" ] \
      || emit CLOSED - malformed "stale ${gate} strategy baseline"
  fi
fi

if [ "$epoch_arg" = true ] && [ "$epoch_start" -gt 1 ]; then
  [ -n "$gate" ] || emit CLOSED - malformed "later epoch requires --gate"
  state="$run_dir/STATE.md"
  [ -s "$state" ] || emit CLOSED - malformed "missing recovery STATE.md"
  [ -s "$recovery" ] || emit CLOSED - malformed "missing RECOVERY.md"
  marker_lines="$(grep -E "^<!-- kimiflow:recovery gate=${gate} source-round=[0-9]+ epoch-start=${epoch_start} cap=${cap} before=[A-Fa-f0-9]{64} after=[A-Fa-f0-9]{64} -->$" "$recovery" 2>/dev/null || true)"
  [ "$(printf '%s\n' "$marker_lines" | grep -c .)" -eq 1 ] \
    || emit CLOSED - malformed "missing or duplicate matching recovery receipt"
  marker="$marker_lines"

  marker_body="${marker#<!-- kimiflow:recovery }"
  marker_body="${marker_body% -->}"
  marker_source=""; marker_start=""; marker_cap=""; marker_before=""; marker_after=""
  OLDIFS="$IFS"; IFS=' '; set -- $marker_body; IFS="$OLDIFS"
  for field in "$@"; do
    case "$field" in
      source-round=*) marker_source="${field#source-round=}" ;;
      epoch-start=*) marker_start="${field#epoch-start=}" ;;
      cap=*) marker_cap="${field#cap=}" ;;
      before=*) marker_before="${field#before=}" ;;
      after=*) marker_after="${field#after=}" ;;
    esac
  done
  marker_source=$((10#$marker_source)); marker_start=$((10#$marker_start)); marker_cap=$((10#$marker_cap))
  [ "$marker_source" -eq $((epoch_start - 1)) ] \
    || emit CLOSED - malformed "non-contiguous recovery source round"
  [ "$marker_start" -eq "$epoch_start" ] && [ "$marker_cap" -eq "$cap" ] \
    || emit CLOSED - malformed "recovery receipt bounds mismatch"
  marker_before="$(printf '%s' "$marker_before" | tr '[:upper:]' '[:lower:]')"
  marker_after="$(printf '%s' "$marker_after" | tr '[:upper:]' '[:lower:]')"
  [ "$marker_before" != "$marker_after" ] || emit CLOSED - malformed "unchanged strategy fingerprint"
  [ "$marker_after" = "$basis_fingerprint" ] \
    || emit CLOSED - malformed "stale ${gate} strategy fingerprint"

  previous_marker="$(grep -E "^<!-- kimiflow:recovery gate=${gate} source-round=[0-9]+ epoch-start=[0-9]+ cap=[0-9]+ before=[A-Fa-f0-9]{64} after=[A-Fa-f0-9]{64} -->$" "$recovery" 2>/dev/null \
    | awk -v current_start="$epoch_start" -v current_cap="$cap" -v baseline="$baseline_fingerprint" '
        BEGIN { last_start=0; current_count=0; bad=0; expected=tolower(baseline) }
        {
          source=$4; sub(/^source-round=/, "", source); source += 0
          start=$5; sub(/^epoch-start=/, "", start); start += 0
          receipt_cap=$6; sub(/^cap=/, "", receipt_cap); receipt_cap += 0
          before=$7; sub(/^before=/, "", before); before=tolower(before)
          after=$8; sub(/^after=/, "", after); after=tolower(after)
          if (start <= 1 || start <= last_start || start > current_start) bad=1
          if (source != start - 1 || receipt_cap < start || before == after || before != expected) bad=1
          if (start < current_start) previous=$0
          if (start == current_start) {
            current_count++
            if (receipt_cap != current_cap) bad=1
          }
          expected=after
          last_start=start
        }
        END {
          if (bad || current_count != 1) print "__MALFORMED__"
          else print previous
        }
      ')"
  [ "$previous_marker" != "__MALFORMED__" ] \
    || emit CLOSED - malformed "non-monotonic or duplicate ${gate} recovery receipts"
  expected_before="$baseline_fingerprint"
  if [ -n "$previous_marker" ]; then
    expected_before="${previous_marker##* after=}"
    expected_before="${expected_before% -->}"
    expected_before="$(printf '%s' "$expected_before" | tr '[:upper:]' '[:lower:]')"
  fi
  [ "$marker_before" = "$expected_before" ] \
    || emit CLOSED - malformed "broken ${gate} strategy fingerprint chain"

  OLDIFS="$IFS"; IFS=','; set -- $expect; IFS="$OLDIFS"
  for lens in "$@"; do
    source_file="$dir/r${marker_source}-${lens}.md"
    [ -f "$source_file" ] \
      || emit CLOSED - malformed "missing recovery source r${marker_source}-${lens}.md"
    [ -s "$source_file" ] \
      || emit CLOSED - malformed "empty recovery source r${marker_source}-${lens}.md"
    if [ "$(grep -c '' "$source_file")" -eq 1 ] && [ "$(head -n1 "$source_file")" = "NONE" ]; then
      continue
    fi
    source_lineno=0
    while IFS= read -r source_line || [ -n "$source_line" ]; do
      source_lineno=$((source_lineno + 1))
      printf '%s\n' "$source_line" | grep -qE "$FINDING_RE" \
        || emit CLOSED - malformed "recovery source r${marker_source}-${lens}.md:${source_lineno}"
    done < "$source_file"
  done

  state_gate="$(state_value "$state" "Review gate" | tr '[:upper:]' '[:lower:]')"
  state_start="$(state_value "$state" "Review epoch start")"
  state_cap="$(state_value "$state" "Review epoch cap")"
  state_fingerprint="$(state_value "$state" "Strategy fingerprint" | tr '[:upper:]' '[:lower:]')"
  state_recovery="$(state_value "$state" "Recovery" | tr '[:upper:]' '[:lower:]')"
  case "$state_start" in ''|*[!0-9]*) emit CLOSED - malformed "bad STATE review epoch start" ;; esac
  case "$state_cap" in ''|*[!0-9]*) emit CLOSED - malformed "bad STATE review epoch cap" ;; esac
  state_start=$((10#$state_start)); state_cap=$((10#$state_cap))
  [ "$state_gate" = "$gate" ] && [ "$state_start" -eq "$epoch_start" ] && [ "$state_cap" -eq "$cap" ] \
    && [ "$state_fingerprint" = "$marker_after" ] && [ "$state_recovery" = "active" ] \
    || emit CLOSED - malformed "recovery STATE mismatch"
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
