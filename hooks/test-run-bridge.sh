#!/usr/bin/env bash
# kimiflow — focused local bridge/readiness/context/scorecard contracts.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
PYTHONPATH=hooks python3 -m unittest \
  kimiflow_core.tests.test_readiness \
  kimiflow_core.tests.test_run_bridge \
  kimiflow_core.tests.test_phase_context \
  kimiflow_core.tests.test_scorecard
