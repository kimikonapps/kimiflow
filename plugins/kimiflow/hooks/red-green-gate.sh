#!/usr/bin/env bash
# kimiflow - red/green fix gate. Orchestrator-invoked, not a hook.
#
# Usage:
#   red-green-gate.sh <run-dir> [--mode <feature|fix|audit|feature-check>] [--pretty]
#
# Output:
#   RED_GREEN_GATE<TAB>OPEN|CLOSED<TAB>blockers=<n><TAB>reason=<code><TAB>detail=<codes>
#
# Fix runs must prove RED before GREEN in BUG-REPRO.md. The gate verifies the
# evidence contract; it intentionally does not execute the recorded commands.
# R2 invariant target: hooks/red-green-gate.sh
set -u

emit() {
  printf 'RED_GREEN_GATE\t%s\tblockers=%s\treason=%s\tdetail=%s\n' "$1" "$2" "$3" "${4:-}"
  exit 0
}

run_dir=""
mode_override=""
mode_explicit=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      shift
      [ "$#" -gt 0 ] || emit CLOSED 1 malformed "missing_mode_value"
      case "${1:-}" in -*) emit CLOSED 1 malformed "missing_mode_value" ;; esac
      mode_override="${1:-}"
      mode_explicit=1
      shift
      ;;
    --pretty)
      shift
      ;;
    --help|-h)
      sed -n '1,12p' "$0"
      exit 0
      ;;
    -*)
      shift
      ;;
    *)
      [ -z "$run_dir" ] && run_dir="$1"
      shift
      ;;
  esac
done

[ -n "$run_dir" ] || emit CLOSED 1 malformed "missing_run_dir"
[ -d "$run_dir" ] || emit CLOSED 1 malformed "run_dir_missing"

state="$run_dir/STATE.md"
evidence="$run_dir/BUG-REPRO.md"

lower() {
  printf '%s\n' "$1" | tr '[:upper:]' '[:lower:]'
}

state_mode() {
  [ -f "$state" ] || return 1
  awk '
    {
      line = $0
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      key = line
      sub(/:.*/, "", key)
      value = line
      sub(/^[^:]*:[[:space:]]*/, "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (tolower(key) == "mode" && length(value) > 0) {
        print tolower(value)
        exit
      }
    }
  ' "$state" 2>/dev/null
}

mode="$(lower "$mode_override")"
if [ -z "$mode" ]; then
  mode="$(state_mode || true)"
fi
[ -n "$mode" ] || mode="unknown"

case "$mode" in
  fix|bug|bugfix)
    ;;
  feature|audit|feature-check|featurecheck)
    emit OPEN 0 not-required "mode=$mode"
    ;;
  *)
    if [ "$mode_explicit" -eq 1 ]; then
      emit CLOSED 1 malformed "invalid_mode=$mode"
    fi
    emit OPEN 0 not-required "mode=$mode"
    ;;
esac

blockers=0
details=""
add_blocker() {
  blockers=$((blockers + 1))
  if [ -z "$details" ]; then details="$1"; else details="$details,$1"; fi
}

has_line() {
  local pattern="$1"
  grep -Eiq "$pattern" "$evidence" 2>/dev/null
}

first_line() {
  local pattern="$1"
  grep -Ein "$pattern" "$evidence" 2>/dev/null | head -1 | cut -d: -f1
}

if [ ! -f "$evidence" ]; then
  emit CLOSED 1 red-green-missing "bug_repro_missing"
fi
if [ ! -s "$evidence" ]; then
  emit CLOSED 1 red-green-missing "bug_repro_empty"
fi

red_command_re='^[[:space:]]*(-[[:space:]]*)?red[ _-]*(command|test|check|repro)[[:space:]]*:'
red_failure_re='^[[:space:]]*(-[[:space:]]*)?red[ _-]*(status|result)[[:space:]]*:[[:space:]]*(failed|failing|fails|red|reproduced|reproducible|expected[ _-]*failure|exit[ _-]*code[[:space:]]*[:=]?[[:space:]]*[1-9][0-9]*|non[ _-]*zero)'
red_output_re='^[[:space:]]*(-[[:space:]]*)?red[ _-]*(output|evidence|decisive[ _-]*output)[[:space:]]*:[[:space:]]*[^[:space:]]'
green_command_re='^[[:space:]]*(-[[:space:]]*)?green[ _-]*(command|test|check)[[:space:]]*:'
green_success_re='^[[:space:]]*(-[[:space:]]*)?green[ _-]*(status|result)[[:space:]]*:[[:space:]]*(passed|passing|passes|green|fixed|success|succeeded|exit[ _-]*code[[:space:]]*[:=]?[[:space:]]*0)'
green_output_re='^[[:space:]]*(-[[:space:]]*)?green[ _-]*(output|evidence|decisive[ _-]*output)[[:space:]]*:[[:space:]]*[^[:space:]]'
regression_na_re='^[[:space:]]*(-[[:space:]]*)?regression[ _-]*(status|result)[[:space:]]*:[[:space:]]*(not[ _-]*applicable|n/a|none)'
regression_na_reason_re='^[[:space:]]*(-[[:space:]]*)?regression[ _-]*(reason|rationale|why|note)[[:space:]]*:[[:space:]]*[^[:space:]]'

red_command_line="$(first_line "$red_command_re")"
red_failure_line="$(first_line "$red_failure_re")"
red_output_line="$(first_line "$red_output_re")"
green_command_line="$(first_line "$green_command_re")"
green_success_line="$(first_line "$green_success_re")"
green_output_line="$(first_line "$green_output_re")"

if [ -z "$red_command_line" ]; then
  add_blocker "red_command_missing"
fi

if [ -z "$red_failure_line" ]; then
  add_blocker "red_failure_missing"
fi

if [ -z "$red_output_line" ]; then
  add_blocker "red_output_missing"
fi

if [ -z "$green_command_line" ]; then
  add_blocker "green_command_missing"
fi

if [ -z "$green_success_line" ]; then
  add_blocker "green_success_missing"
fi

if [ -z "$green_output_line" ]; then
  add_blocker "green_output_missing"
fi

if [ -n "$red_command_line" ] && [ -n "$red_failure_line" ] && [ -n "$red_output_line" ] && [ -n "$green_command_line" ] && [ -n "$green_success_line" ] && [ -n "$green_output_line" ]; then
  if [ "$red_command_line" -gt "$red_failure_line" ] \
    || [ "$red_failure_line" -gt "$red_output_line" ] \
    || [ "$red_output_line" -gt "$green_command_line" ] \
    || [ "$green_command_line" -gt "$green_success_line" ] \
    || [ "$green_success_line" -gt "$green_output_line" ]; then
    add_blocker "red_green_order_invalid"
  fi
fi

if has_line "$regression_na_re"; then
  if ! has_line "$regression_na_reason_re"; then
    add_blocker "regression_na_reason_missing"
  fi
elif has_line '^[[:space:]]*(-[[:space:]]*)?regression[ _-]*(command|test|check)[[:space:]]*:' \
  && has_line '^[[:space:]]*(-[[:space:]]*)?regression[ _-]*(status|result)[[:space:]]*:[[:space:]]*(passed|passing|passes|green|success|succeeded|exit[ _-]*code[[:space:]]*[:=]?[[:space:]]*0)'; then
  :
else
  add_blocker "regression_evidence_missing"
fi

if [ "$blockers" -eq 0 ]; then
  emit OPEN 0 clean ""
fi

emit CLOSED "$blockers" red-green-blockers "$details"
