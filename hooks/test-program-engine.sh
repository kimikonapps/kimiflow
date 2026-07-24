#!/usr/bin/env bash
# kimiflow — focused optional Program Engine tests.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
python3 -m unittest kimiflow_core.tests.test_program_engine
