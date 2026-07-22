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

kimiflow_phase_reads_required() {
  local run_dir="$1" state_file="$2" marker root run_rel active
  marker="$(kimiflow_state_value "$state_file" "Phase reads required" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
  case "$marker" in yes|true|1|required) return 0 ;; esac
  command -v jq >/dev/null 2>&1 || return 1
  root="$(kimiflow_run_root "$run_dir" 2>/dev/null || true)"
  [ -n "$root" ] || return 1
  run_rel="$(kimiflow_run_rel "$root" "$run_dir" 2>/dev/null || true)"
  [ -n "$run_rel" ] || return 1
  active="$root/.kimiflow/session/ACTIVE_RUN.json"
  [ -f "$active" ] || return 1
  jq -e --arg run "$run_rel" '.run == $run and .phase_reads_required == true' "$active" >/dev/null 2>&1
}
