#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$ROOT/hooks${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m kimiflow_core.work_units "$@"
