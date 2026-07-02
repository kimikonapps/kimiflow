#!/usr/bin/env bash
# Shared Bash helpers for the small kimiflow gates that remain shell-native.

kimiflow_state_value() {
  local state_file="$1" key="$2" key_lower
  [ -f "$state_file" ] || return 0
  key_lower="$(printf '%s' "$key" | tr '[:upper:]' '[:lower:]')"
  awk -v key="$key_lower" '
    {
      line = $0
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      lower = tolower(line)
      pattern = "^" key "[[:space:]]*:"
      if (lower ~ pattern) {
        sub(/^[^:]*:[[:space:]]*/, "", line)
        print line
        exit
      }
    }
  ' "$state_file"
}

kimiflow_resolve_root() {
  local root="$1"
  if [ -n "$root" ]; then
    (cd "$root" 2>/dev/null && pwd -P) || return 1
  else
    git rev-parse --show-toplevel 2>/dev/null || pwd -P
  fi
}

kimiflow_run_root() {
  local run_dir="$1" abs
  abs="$(cd "$run_dir" 2>/dev/null && pwd -P)" || return 1
  case "$abs" in
    */.kimiflow/*) printf '%s\n' "${abs%%/.kimiflow/*}" ;;
    *) return 1 ;;
  esac
}

kimiflow_run_rel() {
  local root="$1" run_dir="$2" abs_root abs_run
  abs_root="$(cd "$root" 2>/dev/null && pwd -P)" || return 1
  abs_run="$(cd "$run_dir" 2>/dev/null && pwd -P)" || return 1
  case "$abs_run" in
    "$abs_root"/*) printf '%s\n' "${abs_run#"$abs_root"/}" ;;
    *) return 1 ;;
  esac
}
