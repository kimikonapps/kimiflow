#!/usr/bin/env bash
# kimiflow — display-verbosity helper (read + write). The single tested place for
# verbosity precedence + persistence. This is an OUTPUT-ONLY feature: it never
# affects gates, on-disk artifacts, evidence, subagents or thresholds — only how
# much the orchestrator prints. Orchestrator-invoked (not a Claude Code event hook).
#
# Usage:
#   resolve-verbosity.sh [get] [--flag <level>]      -> echo resolved level word
#   resolve-verbosity.sh onboard-check [--flag <level>] -> echo ASK iff nothing set anywhere, else SKIP
#   resolve-verbosity.sh set <project|global> <level> -> validate, mkdir -p, write, verify, echo path
#
# Precedence (get): flag > project (.kimiflow/verbosity) > global host config > balanced
# Self-contained rule: only a single valid level word is ever read/written — a
# gate/cost line placed in a file is not a valid level and is ignored.
set -u

VALID="quiet balanced verbose"

is_valid_level() {
  case " $VALID " in *" ${1:-} "*) return 0 ;; *) return 1 ;; esac
}

project_file() {
  local root
  root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  printf '%s/.kimiflow/verbosity' "$root"
}

global_file() {
  case "${KIMIFLOW_HOST:-claude}" in
    codex)
      printf '%s/kimiflow/verbosity' "${CODEX_HOME:-$HOME/.codex}"
      ;;
    *)
      printf '%s/kimiflow/verbosity' "${CLAUDE_HOME:-$HOME/.claude}"
      ;;
  esac
}

# Echo the first line of $1 (trimmed) iff it is a valid level word; else return 1.
read_level() {
  local f="$1" line
  [ -f "$f" ] || return 1
  IFS= read -r line < "$f" 2>/dev/null || return 1
  line="${line#"${line%%[![:space:]]*}"}"   # ltrim
  line="${line%"${line##*[![:space:]]}"}"   # rtrim
  is_valid_level "$line" || return 1
  printf '%s' "$line"
}

mode="get"
case "${1:-}" in
  get|set|onboard-check) mode="$1"; shift ;;
esac

# ---- set <project|global> <level> ----
if [ "$mode" = "set" ]; then
  scope="${1:-}"; level="${2:-}"
  case "$scope" in
    project|global) ;;
    *) printf 'resolve-verbosity: set: scope must be project|global (got "%s")\n' "$scope" >&2; exit 1 ;;
  esac
  if ! is_valid_level "$level"; then
    printf 'resolve-verbosity: set: level must be quiet|balanced|verbose (got "%s")\n' "$level" >&2; exit 1
  fi
  if [ "$scope" = "project" ]; then
    target="$(project_file)"
    git rev-parse --show-toplevel >/dev/null 2>&1 \
      || printf 'resolve-verbosity: not in a git repo; writing project default to %s\n' "$target" >&2
  else
    target="$(global_file)"
  fi
  dir="${target%/*}"
  if ! mkdir -p "$dir" 2>/dev/null; then
    printf 'resolve-verbosity: set: cannot create %s\n' "$dir" >&2; exit 1
  fi
  if ! printf '%s\n' "$level" > "$target" 2>/dev/null; then
    printf 'resolve-verbosity: set: cannot write %s\n' "$target" >&2; exit 1
  fi
  # Verify the write actually took (never a false success).
  if [ "$(read_level "$target" || true)" != "$level" ]; then
    printf 'resolve-verbosity: set: write verification failed for %s\n' "$target" >&2; exit 1
  fi
  printf '%s\n' "$target"
  exit 0
fi

# ---- get / onboard-check : parse optional --flag <level>, robust to missing/garbage ----
flag=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --flag)
      if [ "$#" -ge 2 ] && is_valid_level "${2:-}"; then flag="$2"; shift 2; else shift; fi
      ;;
    *) shift ;;   # ignore unrecognized args — degrade, never crash under set -u
  esac
done

if [ -n "$flag" ]; then
  src="flag"; level="$flag"
elif level="$(read_level "$(project_file)")"; then
  src="project"
elif level="$(read_level "$(global_file)")"; then
  src="global"
else
  src="default"; level="balanced"
fi

# onboard-check: turn the precedence decision into an imperative token, so the
# orchestrator never has to evaluate "is it set?" itself — ASK only when nothing
# is set anywhere (winning source is the built-in default); any project/global config
# OR a one-off flag → SKIP.
if [ "$mode" = "onboard-check" ]; then
  [ "$src" = "default" ] && printf 'ASK\n' || printf 'SKIP\n'
else
  printf '%s\n' "$level"
fi
exit 0
