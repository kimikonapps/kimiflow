#!/usr/bin/env bash
# Shared Kimiflow root resolver for host adapters and smoke tests.

kimiflow_root() {
  if [ -n "${KIMIFLOW_PLUGIN_ROOT:-}" ]; then
    printf '%s\n' "$KIMIFLOW_PLUGIN_ROOT"
    return 0
  fi
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    printf '%s\n' "$CLAUDE_PLUGIN_ROOT"
    return 0
  fi
  if [ -n "${CLAUDE_SKILL_DIR:-}" ]; then
    (cd "$CLAUDE_SKILL_DIR/.." 2>/dev/null && pwd) && return 0
  fi
  (cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)
}
