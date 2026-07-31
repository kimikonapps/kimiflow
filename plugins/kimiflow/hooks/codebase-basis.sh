#!/usr/bin/env bash
# Deterministic current-byte basis for one Kimiflow run.
set -eu
command -v python3 >/dev/null 2>&1 || { echo "codebase-basis: python3 is required" >&2; exit 2; }
dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.codebase_basis "$@"
