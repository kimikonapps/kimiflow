#!/usr/bin/env bash
# kimiflow — compact three-round plan-review sealing and saturation gate.
set -u
command -v python3 >/dev/null 2>&1 || { echo "plan-review-gate: python3 is required" >&2; exit 2; }
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = "seal" ]; then
  run=""; round=""; expect=""; write=false
  previous=""
  for argument in "$@"; do
    case "$previous" in
      --run) run="$argument"; previous=""; continue ;;
      --round) round="$argument"; previous=""; continue ;;
      --expect) expect="$argument"; previous=""; continue ;;
    esac
    case "$argument" in
      --run|--round|--expect) previous="$argument" ;;
      --write) write=true ;;
    esac
  done
  seal_out="$(env PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m kimiflow_core.plan_review "$@")"
  printf '%s\n' "$seal_out"
  seal_status="$(printf '%s\n' "$seal_out" | awk -F '\t' 'NR == 1 { print $2 }')"
  [ "$seal_status" = OPEN ] || exit 0
  [ "$write" = true ] && [ -n "$run" ] && [ -n "$round" ] && [ -n "$expect" ] || exit 0

  pin_out="$("$DIR/active-run.sh" pin-plan-saturation \
    --run "$run" --round "$round" --expect "$expect" --write 2>&1)" || {
      printf '%s\n' "$pin_out"
      exit 1
    }
  "$DIR/resolve-review-gate.sh" "$run/findings" \
    --round "$round" --expect "$expect" --gate plan \
    --epoch-start "$round" --cap 3 --finding-contract 1
  exit $?
fi

exec env PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.plan_review "$@"
