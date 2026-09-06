#!/usr/bin/env bash
# Execute explicit argv checks against one unchanged Git worktree; no run state.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
exec python3 "$ROOT/hooks/kimiflow_core/check_change.py" "$@"
