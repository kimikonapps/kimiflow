#!/usr/bin/env bash
# Structural regression tests for the GitHub Actions workflow.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORKFLOW="$ROOT/.github/workflows/ci.yml"

pass=0
fail=0
check() {
  if "$@"; then pass=$((pass + 1)); else printf 'FAIL: %s\n' "$*" >&2; fail=$((fail + 1)); fi
}

check grep -q 'uses: actions/checkout@v7' "$WORKFLOW"
check grep -qE '^[[:space:]]+branches:$' "$WORKFLOW"
check grep -qE '^[[:space:]]+- main$' "$WORKFLOW"
check grep -qE '^[[:space:]]+pull_request:$' "$WORKFLOW"
check grep -q 'os: ubuntu-latest' "$WORKFLOW"
check grep -q 'lane: full' "$WORKFLOW"
check grep -q 'os: macos-latest' "$WORKFLOW"
check grep -q 'lane: portability' "$WORKFLOW"
if grep -qE '^[[:space:]]+tags:' "$WORKFLOW"; then
  printf 'FAIL: release tags must not trigger duplicate full CI\n' >&2
  fail=$((fail + 1))
else
  pass=$((pass + 1))
fi

printf 'ci workflow: %s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
