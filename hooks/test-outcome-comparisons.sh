#!/usr/bin/env bash
# kimiflow — focused tests for paired outcome evidence.
set -u
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$DIR" || exit 1
python3 -m unittest kimiflow_core.tests.test_outcome_comparisons
