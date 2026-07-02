#!/usr/bin/env bash
# kimiflow — read-only launcher status snapshot. Orchestrator-invoked, not a hook.
# Python (stdlib >= 3.9) port: implementation lives in hooks/kimiflow_core/.
# R2 invariant target: hooks/launcher-status.sh --pretty
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.launcher_status "$@"
