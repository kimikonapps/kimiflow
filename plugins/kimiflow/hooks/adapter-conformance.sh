#!/usr/bin/env bash
set -eu
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PYTHONPATH="$ROOT/hooks${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m kimiflow_core.adapter_conformance "$@"
