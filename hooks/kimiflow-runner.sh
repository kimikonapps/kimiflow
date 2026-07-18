#!/usr/bin/env bash
# kimiflow — optional Codex headless controller over the shared Kimiflow core.
set -u

command -v python3 >/dev/null 2>&1 || { echo "kimiflow: python3 is required" >&2; exit 2; }
dir="$(cd "$(dirname "$0")" && pwd)"
export KIMIFLOW_HOST="${KIMIFLOW_HOST:-codex}"
export KIMIFLOW_PLUGIN_ROOT="${KIMIFLOW_PLUGIN_ROOT:-$(cd "$dir/.." && pwd)}"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.runner "$@"
