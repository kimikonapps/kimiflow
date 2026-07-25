#!/usr/bin/env bash
# kimiflow — deterministic review saturation, repair, and trajectory gate.
set -u
command -v python3 >/dev/null 2>&1 || { echo "review-convergence-gate: python3 is required" >&2; exit 2; }
DIR="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.review_convergence "$@"
