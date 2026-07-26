#!/usr/bin/env bash
# kimiflow — focused tests for deterministic evidence evaluation.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
python3 -m unittest kimiflow_core.tests.test_evidence_eval
