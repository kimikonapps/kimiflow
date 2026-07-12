#!/usr/bin/env bash
# kimiflow — Build Preview risk-policy resolver (read + write + decision).
# PROJECT-LOCAL ONLY (.kimiflow/build-gate at the git root). Default RISK: normal
# reversible work continues after the preview; named product/irreversibility risks stop.
# Legacy `on` reads as `always`. This is control-flow only and never weakens internal
# plan, review, test, evidence, or commit gates.
#
# Usage:
#   resolve-build-gate.sh [get] -> echo risk|always|off
#   resolve-build-gate.sh set <risk|always|off|on>
#   resolve-build-gate.sh decide --state <STATE.md> --interactive <yes|no> [--risk <none|required>] [--alias full]
# Legacy callers may omit --state and provide --risk directly.
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hooks/kimiflow-lib.sh
. "$SCRIPT_DIR/kimiflow-lib.sh"

VALID="risk always off on"
is_valid() { case " $VALID " in *" ${1:-} "*) return 0 ;; *) return 1 ;; esac; }
normalize() { if [ "${1:-}" = "on" ]; then printf 'always'; else printf '%s' "${1:-}"; fi; }

project_file() {
  local root
  root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  printf '%s/.kimiflow/build-gate' "$root"
}

# Echo the first line of $1 (trimmed) iff it is a valid value; else return 1.
read_value() {
  local f="$1" line
  [ -f "$f" ] || return 1
  IFS= read -r line < "$f" 2>/dev/null || return 1
  line="${line#"${line%%[![:space:]]*}"}"   # ltrim
  line="${line%"${line##*[![:space:]]}"}"   # rtrim
  is_valid "$line" || return 1
  normalize "$line"
}

mode="get"
case "${1:-}" in
  get|set|decide) mode="$1"; shift ;;
esac

if [ "$mode" = "set" ]; then
  val="${1:-}"
  if ! is_valid "$val"; then
    printf 'resolve-build-gate: set: value must be risk|always|off (legacy on accepted; got "%s")\n' "$val" >&2; exit 1
  fi
  val="$(normalize "$val")"
  target="$(project_file)"
  git rev-parse --show-toplevel >/dev/null 2>&1 \
    || printf 'resolve-build-gate: not in a git repo; writing to %s\n' "$target" >&2
  dir="${target%/*}"
  if ! mkdir -p "$dir" 2>/dev/null; then
    printf 'resolve-build-gate: set: cannot create %s\n' "$dir" >&2; exit 1
  fi
  if ! printf '%s\n' "$val" > "$target" 2>/dev/null; then
    printf 'resolve-build-gate: set: cannot write %s\n' "$target" >&2; exit 1
  fi
  if [ "$(read_value "$target" || true)" != "$val" ]; then
    printf 'resolve-build-gate: set: write verification failed for %s\n' "$target" >&2; exit 1
  fi
  printf '%s\n' "$target"
  exit 0
fi

policy="$(read_value "$(project_file)" || printf 'risk')"

if [ "$mode" = "decide" ]; then
  risk=""
  interactive=""
  alias_value=""
  state_file=""
  state_requested=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --risk) shift; risk="${1:-}" ;;
      --interactive) shift; interactive="${1:-}" ;;
      --alias) shift; alias_value="${1:-}" ;;
      --state) state_requested=1; shift; state_file="${1:-}" ;;
      *) printf 'resolve-build-gate: decide: unknown argument "%s"\n' "$1" >&2; exit 2 ;;
    esac
    shift
  done

  if [ "$state_requested" -eq 1 ]; then
    if [ -z "$state_file" ] || [ ! -f "$state_file" ]; then
      printf 'BUILD_GATE\tPARK\tpolicy=%s\trisk=%s\treason=state-missing\n' "$policy" "${risk:-missing}"
      exit 0
    fi
    state_risk="$(kimiflow_state_value "$state_file" "Build risk" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
    case "$state_risk" in
      none|required) ;;
      "") printf 'BUILD_GATE\tPARK\tpolicy=%s\trisk=missing\treason=state-risk-missing\n' "$policy"; exit 0 ;;
      *) printf 'BUILD_GATE\tPARK\tpolicy=%s\trisk=%s\treason=state-risk-invalid\n' "$policy" "$state_risk"; exit 0 ;;
    esac
    if [ -n "$risk" ] && [ "$risk" != "$state_risk" ]; then
      printf 'BUILD_GATE\tPARK\tpolicy=%s\trisk=%s\treason=risk-state-mismatch\n' "$policy" "$risk"
      exit 0
    fi
    risk="$state_risk"
  fi

  case "$risk" in
    none|required) ;;
    *) printf 'BUILD_GATE\tPARK\tpolicy=%s\trisk=%s\treason=malformed-risk\n' "$policy" "${risk:-missing}"; exit 0 ;;
  esac
  case "$interactive" in
    yes|no) ;;
    *) printf 'BUILD_GATE\tPARK\tpolicy=%s\trisk=%s\treason=malformed-interactive\n' "$policy" "$risk"; exit 0 ;;
  esac

  stop_reason=""
  if [ "$alias_value" = "full" ]; then
    stop_reason="full"
  elif [ "$policy" = "always" ]; then
    stop_reason="policy-always"
  elif [ "$policy" = "risk" ] && [ "$risk" = "required" ]; then
    stop_reason="risk-required"
  fi

  if [ -z "$stop_reason" ]; then
    printf 'BUILD_GATE\tCONTINUE\tpolicy=%s\trisk=%s\treason=preview-only\n' "$policy" "$risk"
  elif [ "$interactive" = "yes" ]; then
    printf 'BUILD_GATE\tSTOP\tpolicy=%s\trisk=%s\treason=%s\n' "$policy" "$risk" "$stop_reason"
  else
    printf 'BUILD_GATE\tPARK\tpolicy=%s\trisk=%s\treason=%s-headless\n' "$policy" "$risk" "$stop_reason"
  fi
  exit 0
fi

# get: project value or default risk
printf '%s\n' "$policy"
exit 0
