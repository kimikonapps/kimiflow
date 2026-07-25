#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHONPATH="$ROOT/hooks${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m unittest hooks.kimiflow_core.tests.test_worktree_broker
