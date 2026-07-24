#!/usr/bin/env bash
# kimiflow — optional local project release-profile control plane.
set -u
command -v python3 >/dev/null 2>&1 || { echo "release-profile: python3 is required" >&2; exit 2; }
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m kimiflow_core.release_profile "$@"
