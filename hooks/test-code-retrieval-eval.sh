#!/usr/bin/env bash
set -eu
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
PYTHONPATH="$ROOT/hooks${PYTHONPATH:+:$PYTHONPATH}" python3 -m unittest -v kimiflow_core.tests.test_code_retrieval_eval
