#!/usr/bin/env bash
# kimiflow — read-only launcher status snapshot. Orchestrator-invoked, not a hook.
# Python (stdlib >= 3.9) port: implementation lives in hooks/kimiflow_core/.
command -v python3 >/dev/null 2>&1 || { echo "launcher-status: python3 is required" >&2; exit 2; }
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.launcher_status "$@"
