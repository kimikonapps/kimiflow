#!/usr/bin/env bash
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
GATE="$ROOT/hooks/pi-subagent-gate.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
REPO="$WORK/repo"
RUN="$REPO/.kimiflow/run-7"

mkdir -p "$RUN"
chmod 700 "$REPO/.kimiflow" "$RUN"
git -C "$REPO" init -q

binding="$(python3 - "$REPO" <<'PY'
import json
import os
import sys
print(json.dumps({
    "schema_version": 1,
    "root": os.path.realpath(sys.argv[1]),
    "captain_session_id": "captain-00000001",
    "worker_id": "worker-00000001",
}, separators=(",", ":")))
PY
)"

run_gate() {
  KIMIFLOW_SESSION_HOST=pi \
  KIMIFLOW_PI_BRIDGE_BINDING="$binding" \
    "$GATE" "$RUN" --phase 4 --role plan_review --round 1 --min "$1"
}

write_receipt() {
  seat="$1"
  marker="$2"
  mkdir -p "$RUN/PI-SUBAGENTS"
  chmod 700 "$RUN/PI-SUBAGENTS"
  python3 - "$RUN/PI-SUBAGENTS" "$REPO" "$seat" "$marker" <<'PY'
import hashlib
import json
import os
import sys

directory, root, seat, marker = sys.argv[1:]
worker_id = "worker-00000001"
worker_session_id = "pi-worker-00000001"
subagent_session_id = "subagent-session-" + marker * 16
receipt_id = "sha256:" + hashlib.sha256("\0".join((
    worker_id, worker_session_id, subagent_session_id,
    "4", "plan_review", "1", seat,
)).encode("utf-8")).hexdigest()
target = os.path.join(
    directory,
    "4-1-plan_review-%s-%s.json" % (seat, receipt_id[7:31]),
)
value = {
    "schema_version": 1,
    "receipt_id": receipt_id,
    "root": os.path.realpath(root),
    "run": ".kimiflow/run-7",
    "worker_id": worker_id,
    "worker_session_id": worker_session_id,
    "subagent_session_id": subagent_session_id,
    "phase": 4,
    "role": "plan_review",
    "round": 1,
    "seat": seat,
    "slot": 1,
    "backend": "herdr",
    "status": "completed",
    "task_digest": "sha256:" + "a" * 64,
    "result_digest": "sha256:" + "b" * 64,
}
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump(value, handle, separators=(",", ":"))
    handle.write("\n")
PY
}

out="$(KIMIFLOW_SESSION_HOST=codex "$GATE" "$RUN" \
  --phase 4 --role plan_review --round 1 --min 1)"
printf '%s\n' "$out" | grep -q $'^PI_SUBAGENT_GATE\tOPEN\tcount=0\treason=not_required$'

out="$(run_gate 1)"
printf '%s\n' "$out" | grep -q $'^PI_SUBAGENT_GATE\tCLOSED\tcount=0\treason=receipts_missing$'

write_receipt plan-review-1 c
out="$(run_gate 1)"
printf '%s\n' "$out" | grep -q $'^PI_SUBAGENT_GATE\tOPEN\tcount=1\treason=clean$'
out="$(run_gate 2)"
printf '%s\n' "$out" | grep -q $'^PI_SUBAGENT_GATE\tCLOSED\tcount=1\treason=seats_missing$'

write_receipt plan-review-2 d
out="$(run_gate 2)"
printf '%s\n' "$out" | grep -q $'^PI_SUBAGENT_GATE\tOPEN\tcount=2\treason=clean$'

chmod 644 "$RUN/PI-SUBAGENTS/"*plan-review-2*.json
out="$(run_gate 2)"
printf '%s\n' "$out" | grep -q $'^PI_SUBAGENT_GATE\tCLOSED\tcount=0\treason=receipt_unsafe$'

printf 'OK (pi subagent gate)\n'
