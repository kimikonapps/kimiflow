#!/usr/bin/env bash
# kimiflow — focused evidence-bound Phase-5 replan tests.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
python3 -m unittest kimiflow_core.tests.test_build_replan kimiflow_core.tests.test_flow_graph
