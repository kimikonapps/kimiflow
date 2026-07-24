#!/usr/bin/env bash
# kimiflow — verified, path-selective local project architecture deltas.
set -u
command -v python3 >/dev/null 2>&1 || { echo "project-delta: python3 is required" >&2; exit 2; }
DIR="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m memory_router.project_delta "$@"
