#!/usr/bin/env bash
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
WORKFLOW="$ROOT/.github/workflows/ci.yml"
grep -q 'uses: actions/setup-python@v6' "$WORKFLOW"
grep -q 'python: "3.9"' "$WORKFLOW"
grep -q 'python: "3.14"' "$WORKFLOW"
grep -q 'references/runtime-release-v1.schema.json' "$WORKFLOW"
exec env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$ROOT/hooks${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m unittest hooks.kimiflow_core.tests.test_runtime_release
