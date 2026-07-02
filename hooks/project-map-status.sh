#!/usr/bin/env bash
# kimiflow — project-map staleness resolver. Orchestrator-invoked, not a hook.
# Python (stdlib >= 3.9) port: implementation lives in hooks/kimiflow_core/.
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.project_map_status "$@"
