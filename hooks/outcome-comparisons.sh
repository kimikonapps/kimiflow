#!/usr/bin/env bash
# kimiflow — local validation and summaries for paired outcome evidence.
set -u
command -v python3 >/dev/null 2>&1 || { echo "outcome-comparisons: python3 is required" >&2; exit 2; }
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec env PYTHONDONTWRITEBYTECODE=1 \
  python3 "$DIR/kimiflow_core/outcome_comparisons.py" "$@"
