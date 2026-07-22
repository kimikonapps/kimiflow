#!/usr/bin/env bash
# Print one exact level-2 section from the canonical reference without loading the full file.
set -eu

[ "$#" -eq 1 ] || { printf 'Usage: hooks/reference-section.sh <exact-section-name>\n' >&2; exit 2; }
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" python3 - "$1" <<'PY'
import sys
from kimiflow_core import phase_reads

try:
    sys.stdout.buffer.write(phase_reads.reference_section_bytes(sys.argv[1]))
except phase_reads.PhaseReadError as exc:
    print("reference-section: %s" % exc, file=sys.stderr)
    raise SystemExit(1)
PY
