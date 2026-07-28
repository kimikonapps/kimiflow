#!/usr/bin/env bash
# kimiflow — hermetic CLI, privacy and checkout-immutability checks.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REAL_PYTHON="$(command -v python3)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/bin"
PROJECT="$WORK/non-git-project"
OUTSIDE="$WORK/outside"
CALLS="$WORK/external-calls.log"
mkdir -p "$BIN" "$PROJECT" "$OUTSIDE"
ln -s "$REAL_PYTHON" "$BIN/python3"

for command in git codex claude gh; do
  {
    printf '#!/bin/sh\n'
    printf 'printf "%%s\\n" "%s $*" >> "%s"\n' "$command" "$CALLS"
    printf 'exit 99\n'
  } > "$BIN/$command"
  chmod +x "$BIN/$command"
done

printf 'print("CANARY-RAW-SECURITY")\n' > "$PROJECT/app.py"
before="$("$REAL_PYTHON" - "$PROJECT" <<'PY'
import hashlib
import os
import sys

root = sys.argv[1]
rows = []
for current, directories, files in os.walk(root):
    directories[:] = sorted(name for name in directories if name != ".kimiflow")
    for name in sorted(files):
        path = os.path.join(current, name)
        rel = os.path.relpath(path, root)
        rows.append(rel.encode() + b"\0" + open(path, "rb").read())
print(hashlib.sha256(b"\0".join(rows)).hexdigest())
PY
)"

PATH="$BIN:/usr/bin:/bin" "$ROOT/hooks/kimiflow-runner.sh" \
  security scan "$PROJECT" > "$WORK/scan.json"
"$REAL_PYTHON" - "$WORK/scan.json" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["schema_version"] == 1
assert value["status"] == "incomplete"
assert value["model_calls"] == 0
assert value["artifacts"]["directory"].startswith(".kimiflow/security/scans/scan_")
PY
[ ! -s "$CALLS" ] || {
  printf 'security_cli_is_local_and_provider_neutral: external command invoked\n' >&2
  cat "$CALLS" >&2
  exit 1
}
printf 'ok   security_cli_is_local_and_provider_neutral\n'

after="$("$REAL_PYTHON" - "$PROJECT" <<'PY'
import hashlib
import os
import sys

root = sys.argv[1]
rows = []
for current, directories, files in os.walk(root):
    directories[:] = sorted(name for name in directories if name != ".kimiflow")
    for name in sorted(files):
        path = os.path.join(current, name)
        rel = os.path.relpath(path, root)
        rows.append(rel.encode() + b"\0" + open(path, "rb").read())
print(hashlib.sha256(b"\0".join(rows)).hexdigest())
PY
)"
[ "$before" = "$after" ]
if grep -R -F -q 'CANARY-RAW-SECURITY' "$PROJECT/.kimiflow/security"; then
  printf 'security_scan_preserves_checkout_and_refuses_symlink_escape: raw canary persisted\n' >&2
  exit 1
fi
if grep -R -F -q "$WORK" "$PROJECT/.kimiflow/security"; then
  printf 'security_scan_preserves_checkout_and_refuses_symlink_escape: absolute path persisted\n' >&2
  exit 1
fi
scan_id="$("$REAL_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["scan_id"])' "$WORK/scan.json")"
printf 'private internal state\n' > "$PROJECT/.kimiflow/private.txt"
PATH="$BIN:/usr/bin:/bin" "$ROOT/hooks/kimiflow-runner.sh" \
  security scan "$PROJECT" > "$WORK/repeated.json"
repeat_id="$("$REAL_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["scan_id"])' "$WORK/repeated.json")"
[ "$scan_id" = "$repeat_id" ]

set +e
PATH="$BIN:/usr/bin:/bin" "$ROOT/hooks/kimiflow-runner.sh" \
  security diff "$PROJECT" > "$WORK/diff.json"
diff_rc=$?
set -e
[ "$diff_rc" -ne 0 ]
"$REAL_PYTHON" - "$WORK/diff.json" <<'PY'
import json
import sys

assert json.load(open(sys.argv[1], encoding="utf-8"))["status"] == "git_required"
PY

HOSTILE="$WORK/hostile"
mkdir -p "$HOSTILE"
printf 'safe\n' > "$HOSTILE/app.txt"
printf 'outside-canary\n' > "$OUTSIDE/canary.txt"
ln -s "$OUTSIDE" "$HOSTILE/.kimiflow"
set +e
PATH="$BIN:/usr/bin:/bin" "$ROOT/hooks/kimiflow-runner.sh" \
  security scan "$HOSTILE" > "$WORK/hostile.json"
hostile_rc=$?
set -e
[ "$hostile_rc" -ne 0 ]
grep -q '^outside-canary$' "$OUTSIDE/canary.txt"
"$REAL_PYTHON" - "$WORK/hostile.json" <<'PY'
import json
import sys

assert json.load(open(sys.argv[1], encoding="utf-8"))["status"] == "unsafe_state_path"
PY
printf 'ok   security_scan_preserves_checkout_and_refuses_symlink_escape\n'

for schema in \
  security-scan-manifest-v1.schema.json \
  security-coverage-v1.schema.json \
  security-findings-v1.schema.json \
  security-report-v1.schema.json
do
  "$REAL_PYTHON" -m json.tool "$ROOT/references/$schema" >/dev/null
done
printf 'ok   security_schema_documents_valid\n'
