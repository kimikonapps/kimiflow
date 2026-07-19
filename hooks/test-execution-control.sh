#!/usr/bin/env bash
# kimiflow — focused tests for the bounded adaptive execution controller.
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$DIR"
python3 -m unittest \
  kimiflow_core.tests.test_execution_control \
  kimiflow_core.tests.test_flow_graph.TestFlowGraph.test_execution_contract_is_bounded_without_changing_selector_free_output \
  kimiflow_core.tests.test_active_run.TestExecutionControlIntegration \
  kimiflow_core.tests.test_runner.RunnerTests.test_continuation_prompt_carries_bounded_execution_decision
