#!/usr/bin/env bash
# kimiflow — Python unit tests for the memory_router package.
# Runs the FULL suite via test discovery (the package is the active runtime after the
# cutover), following the repo's ok/bad idiom. Parity cases that shell to the pinned Bash
# self-skip when bash/jq/git or the tag are unavailable. Run: bash hooks/test-memory-router-unit.sh
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"

# Ensure jq is reachable — parity cases shell to the pinned Bash which needs jq
[ -d /opt/homebrew/bin ] && export PATH="/opt/homebrew/bin:$PATH"

FAILS=0

cd "$DIR"
if python3 -m unittest discover -s memory_router/tests -p 'test_*.py'; then
  : # all green
else
  FAILS=$((FAILS + 1))
fi

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
