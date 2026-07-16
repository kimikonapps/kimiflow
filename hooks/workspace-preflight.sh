#!/usr/bin/env bash
# kimiflow — read-only-first workspace/worktree inventory and guarded cleanup.
command -v python3 >/dev/null 2>&1 || { echo "workspace-preflight: python3 is required" >&2; exit 2; }
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.workspace_preflight "$@"
