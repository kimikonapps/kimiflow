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
check grep -q 'persist-credentials: false' "$WORKFLOW"
check grep -qE '^[[:space:]]+branches:$' "$WORKFLOW"
check grep -qE '^[[:space:]]+- main$' "$WORKFLOW"
check grep -qE '^[[:space:]]+pull_request:$' "$WORKFLOW"
check grep -q 'os: ubuntu-latest' "$WORKFLOW"
check grep -q 'lane: full' "$WORKFLOW"
check grep -q 'os: macos-latest' "$WORKFLOW"
check grep -q 'lane: portability' "$WORKFLOW"
check grep -q 'uses: actions/setup-node@v6' "$WORKFLOW"
check grep -q 'node-version: "22.19.0"' "$WORKFLOW"
check grep -q 'Install tested Pi runtime — hard gate' "$WORKFLOW"
check grep -q 'npm install --global --ignore-scripts --no-audit --no-fund @earendil-works/pi-coding-agent@0.83.0' "$WORKFLOW"
check grep -qE '^permissions:$' "$WORKFLOW"
check grep -qE '^[[:space:]]+contents: read$' "$WORKFLOW"
check grep -q 'security ci-artifact --diff' "$WORKFLOW"
check grep -q 'uses: actions/upload-artifact@v7' "$WORKFLOW"
check grep -q 'path: artifacts/security-deep-portable.json' "$WORKFLOW"
check grep -q 'if: always() && matrix.lane == '\''full'\''' "$WORKFLOW"
check grep -q 'include-hidden-files: false' "$WORKFLOW"
check grep -q 'retention-days: 7' "$WORKFLOW"
if grep -qE '\$\{\{[[:space:]]*secrets\.' "$WORKFLOW"; then
  printf 'FAIL: advisory CI must not reference repository secrets\n' >&2
  fail=$((fail + 1))
else
  pass=$((pass + 1))
fi
security_block="$(sed -n '/Deep Security diff evidence/,/ShellCheck (errors)/p' "$WORKFLOW")"
if [ "$(printf '%s\n' "$security_block" | grep -c 'continue-on-error: true')" -ge 2 ]; then
  pass=$((pass + 1))
else
  printf 'FAIL: scan and archive must remain advisory\n' >&2
  fail=$((fail + 1))
fi
if grep -qE '^[[:space:]]+tags:' "$WORKFLOW"; then
  printf 'FAIL: release tags must not trigger duplicate full CI\n' >&2
  fail=$((fail + 1))
else
  pass=$((pass + 1))
fi

printf 'ci workflow: %s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
