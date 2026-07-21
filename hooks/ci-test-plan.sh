#!/usr/bin/env bash
# kimiflow — deterministic CI test inventory and lane runner.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec env PYTHONPATH="$ROOT/hooks${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.ci_test_plan "$@"
