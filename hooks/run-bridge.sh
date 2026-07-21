#!/usr/bin/env bash
# kimiflow — single-shot local JSON-stdio bridge.
set -u
command -v python3 >/dev/null 2>&1 || { echo "run-bridge: python3 is required" >&2; exit 2; }
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.run_bridge "$@"
