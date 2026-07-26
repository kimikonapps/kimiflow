#!/usr/bin/env bash
# kimiflow — deterministic behavior-evaluation envelopes and comparisons.
set -u
command -v python3 >/dev/null 2>&1 || { echo "evidence-eval: python3 is required" >&2; exit 2; }
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m kimiflow_core.evidence_eval "$@"
