#!/usr/bin/env bash
# kimiflow — build-gate resolver (read + write). The single tested place for the
# pre-build summary-gate toggle. PROJECT-LOCAL ONLY (.kimiflow/build-gate at the git
# root) — the self-contained rule forbids gate-related config in ~/.claude. Default ON
# (fail-safe toward more control). This is CONTROL-FLOW only: it never affects gates,
# artifacts, evidence, subagents or thresholds — only whether the orchestrator stops
# for approval before Phase 5. Orchestrator-invoked (not a Claude Code event hook).
#
# Usage:
#   resolve-build-gate.sh [get]        -> echo on|off  (project file, else on)
#   resolve-build-gate.sh set <on|off> -> validate, mkdir -p, write, verify, echo path
set -u

VALID="on off"
is_valid() { case " $VALID " in *" ${1:-} "*) return 0 ;; *) return 1 ;; esac; }

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
  printf '%s' "$line"
}

mode="get"
case "${1:-}" in
  get|set) mode="$1"; shift ;;
esac

if [ "$mode" = "set" ]; then
  val="${1:-}"
  if ! is_valid "$val"; then
    printf 'resolve-build-gate: set: value must be on|off (got "%s")\n' "$val" >&2; exit 1
  fi
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

# get: project value or default on
if value="$(read_value "$(project_file)")"; then
  printf '%s\n' "$value"
else
  printf 'on\n'
fi
exit 0
