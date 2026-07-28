#!/usr/bin/env bash
# kimiflow — non-gating staged-content advisory. Provider selection, version checks,
# bounded execution and redaction live in the shared local security facade.
set -u

command -v python3 >/dev/null 2>&1 || {
  printf 'kimiflow secret-content-scan: python3 unavailable — advisory SKIPPED.\n' >&2
  exit 0
}
dir="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m kimiflow_core.security advisory --root . --text || true
