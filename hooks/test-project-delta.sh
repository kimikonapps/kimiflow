#!/usr/bin/env bash
# kimiflow — focused local Project Delta tests.
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
python3 -m unittest memory_router.tests.test_project_delta
