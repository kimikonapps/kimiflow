#!/usr/bin/env bash
# kimiflow — current-state risk and source-freshness gate.
set -u
command -v python3 >/dev/null 2>&1 || { echo "current-state-gate: python3 is required" >&2; exit 2; }
DIR="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.current_state "$@"
