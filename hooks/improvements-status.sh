#!/usr/bin/env bash
# kimiflow — local workqueue close-back helper.
# Python (stdlib >= 3.9) port: implementation lives in hooks/kimiflow_core/.
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.improvements_status "$@"
