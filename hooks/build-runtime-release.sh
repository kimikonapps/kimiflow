#!/usr/bin/env bash
# Build or verify the deterministic, host-neutral Kimiflow runtime release.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m kimiflow_core.runtime_release "$@"
