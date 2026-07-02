#!/usr/bin/env bash
# kimiflow — local Background Handles registry and collect gate.
# Python (stdlib >= 3.9) port: implementation lives in hooks/kimiflow_core/.
command -v python3 >/dev/null 2>&1 || { echo "background-run: python3 is required" >&2; exit 2; }
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.background_run "$@"
