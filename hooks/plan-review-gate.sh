#!/usr/bin/env bash
# kimiflow — structured contract matrix and three-round plan-review saturation gate.
set -u
command -v python3 >/dev/null 2>&1 || { echo "plan-review-gate: python3 is required" >&2; exit 2; }
DIR="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.plan_review "$@"
