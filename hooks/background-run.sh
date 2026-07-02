#!/usr/bin/env bash
# kimiflow — local Background Handles registry and collect gate.
# Python (stdlib >= 3.9) port: implementation lives in hooks/kimiflow_core/.
# R2 invariant target: hooks/background-run.sh
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.background_run "$@"
