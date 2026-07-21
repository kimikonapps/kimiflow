#!/usr/bin/env bash
# kimiflow — offline validator for attribution-clean behavioral eval receipts.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec env PYTHONPATH="$ROOT/hooks${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.eval_receipt "$@"
