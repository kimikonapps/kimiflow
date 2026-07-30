#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/hooks${PYTHONPATH:+:$PYTHONPATH}"

python3 -m unittest \
  kimiflow_core.tests.test_pi_host \
  kimiflow_core.tests.test_model_adapter \
  kimiflow_core.tests.test_runtime_release \
  kimiflow_core.tests.test_active_run.ActiveRunContractTests

node --test \
  "$ROOT/hosts/pi/tests/worker.test.mjs" \
  "$ROOT/hosts/pi/tests/captain.test.mjs"
