#!/usr/bin/env bash
# kimiflow - token-cheap local memory router. Orchestrator-invoked, not a hook.
# Python (stdlib >= 3.9) port: the implementation lives in hooks/memory_router/. This thin
# shim preserves the historical `memory-router.sh <cmd> ...` entrypoint by pointing
# PYTHONPATH at this directory and exec'ing the package. The former 4400-line Bash runtime
# was ported byte-for-byte (grounded vs tag kimiflow--v0.1.50); see docs/superpowers/.
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m memory_router "$@"
