#!/usr/bin/env bash
# kimiflow — active session contract helper and hooks.
# Python (stdlib >= 3.9) port: implementation lives in hooks/kimiflow_core/.
# R2 invariant targets: hooks/active-run.sh start --run .kimiflow/<slug>; refresh-baseline --write
case "${1:-}" in
  prompt-context|stop-gate)
    command -v jq >/dev/null 2>&1 || exit 0
    ;;
  *)
    command -v jq >/dev/null 2>&1 || { echo "active-run: jq is required" >&2; exit 2; }
    ;;
esac
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.active_run "$@"
