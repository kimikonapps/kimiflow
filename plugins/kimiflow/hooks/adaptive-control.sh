#!/usr/bin/env bash
# kimiflow — local deterministic scope/context/model policy.
set -u
dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.adaptive_control "$@"
