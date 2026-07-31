#!/usr/bin/env bash
# kimiflow — verifies role-bound Pi subagent receipts for phase gates.
# Usage: pi-subagent-gate.sh <run-dir> --phase N --role ROLE --round N --min N
set -u

if ! command -v python3 >/dev/null 2>&1; then
  printf 'PI_SUBAGENT_GATE\tCLOSED\tcount=0\treason=python3_missing\n'
  exit 0
fi

exec python3 - "$@" <<'PY'
import hashlib
import json
import os
import re
import stat
import subprocess
import sys


def emit(status, count, reason):
    print("PI_SUBAGENT_GATE\t%s\tcount=%s\treason=%s" % (status, count, reason))
    raise SystemExit(0)


args = sys.argv[1:]
run_dir = ""
options = {"--phase": "", "--role": "", "--round": "", "--min": ""}
index = 0
while index < len(args):
    value = args[index]
    if value in options:
        if index + 1 >= len(args):
            emit("CLOSED", 0, "argument_missing")
        options[value] = args[index + 1]
        index += 2
    elif value.startswith("-"):
        emit("CLOSED", 0, "argument_unknown")
    elif not run_dir:
        run_dir = value
        index += 1
    else:
        emit("CLOSED", 0, "argument_extra")

host = os.environ.get("KIMIFLOW_SESSION_HOST") or os.environ.get("KIMIFLOW_HOST") or ""
if host != "pi":
    emit("OPEN", 0, "not_required")

roles = {
    "research": 2,
    "plan_review": 4,
    "implementation": 5,
    "verification": 6,
    "code_review": 7,
}
try:
    phase = int(options["--phase"])
    round_number = int(options["--round"])
    minimum = int(options["--min"])
except ValueError:
    emit("CLOSED", 0, "argument_invalid")
role = options["--role"]
if (
    role not in roles
    or roles[role] != phase
    or not 1 <= round_number <= 99
    or not 1 <= minimum <= 3
):
    emit("CLOSED", 0, "argument_invalid")

run_dir = os.path.realpath(os.path.abspath(run_dir))
if not os.path.isdir(run_dir) or os.path.islink(run_dir):
    emit("CLOSED", 0, "run_invalid")
root_probe = subprocess.run(
    ["git", "-C", run_dir, "rev-parse", "--show-toplevel"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    check=False,
)
if root_probe.returncode != 0:
    emit("CLOSED", 0, "root_invalid")
root = os.path.realpath(root_probe.stdout.strip())
expected_run = os.path.relpath(run_dir, root).replace(os.sep, "/")
if not re.fullmatch(r"\.kimiflow/(?!session(?:/|$))[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", expected_run):
    emit("CLOSED", 0, "run_invalid")

try:
    binding = json.loads(os.environ.get("KIMIFLOW_PI_BRIDGE_BINDING", ""))
except (TypeError, json.JSONDecodeError):
    binding = None
if (
    not isinstance(binding, dict)
    or set(binding) != {"schema_version", "root", "captain_session_id", "worker_id"}
    or binding.get("schema_version") != 1
    or os.path.realpath(str(binding.get("root", ""))) != root
    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{7,63}", str(binding.get("worker_id", ""))) is None
):
    emit("CLOSED", 0, "binding_invalid")

directory = os.path.join(run_dir, "PI-SUBAGENTS")
try:
    directory_info = os.lstat(directory)
except OSError:
    emit("CLOSED", 0, "receipts_missing")
if (
    not stat.S_ISDIR(directory_info.st_mode)
    or stat.S_ISLNK(directory_info.st_mode)
    or directory_info.st_mode & 0o077
):
    emit("CLOSED", 0, "receipts_unsafe")

digest = re.compile(r"sha256:[0-9a-f]{64}")
identity = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
seat_re = re.compile(r"[a-z][a-z0-9-]{0,47}")
expected_keys = {
    "schema_version", "receipt_id", "root", "run", "worker_id",
    "worker_session_id", "subagent_session_id", "phase", "role", "round",
    "seat", "slot", "backend", "status", "task_digest", "result_digest",
}
seats = set()
receipt_ids = set()
try:
    entries = sorted(os.scandir(directory), key=lambda item: item.name)
except OSError:
    emit("CLOSED", 0, "receipts_unsafe")
for entry in entries:
    if not entry.name.endswith(".json") or entry.is_symlink() or not entry.is_file(follow_symlinks=False):
        emit("CLOSED", 0, "receipt_unsafe")
    info = entry.stat(follow_symlinks=False)
    if info.st_size < 2 or info.st_size > 16384 or info.st_mode & 0o077:
        emit("CLOSED", 0, "receipt_unsafe")
    try:
        with open(entry.path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        emit("CLOSED", 0, "receipt_invalid")
    if not isinstance(value, dict) or set(value) != expected_keys:
        emit("CLOSED", 0, "receipt_invalid")
    if (
        value.get("schema_version") != 1
        or value.get("root") != root
        or value.get("run") != expected_run
        or value.get("worker_id") != binding["worker_id"]
        or identity.fullmatch(str(value.get("worker_session_id", ""))) is None
        or identity.fullmatch(str(value.get("subagent_session_id", ""))) is None
        or digest.fullmatch(str(value.get("receipt_id", ""))) is None
        or digest.fullmatch(str(value.get("task_digest", ""))) is None
        or digest.fullmatch(str(value.get("result_digest", ""))) is None
        or type(value.get("phase")) is not int
        or value.get("role") not in roles
        or roles[value["role"]] != value["phase"]
        or type(value.get("round")) is not int
        or not 1 <= value["round"] <= 99
        or value.get("backend") not in {"herdr", "process"}
        or value.get("status") != "completed"
        or isinstance(value.get("slot"), bool)
        or value.get("slot") not in {1, 2, 3}
        or seat_re.fullmatch(str(value.get("seat", ""))) is None
    ):
        emit("CLOSED", 0, "receipt_invalid")
    expected_receipt_id = "sha256:" + hashlib.sha256("\0".join((
        value["worker_id"],
        value["worker_session_id"],
        value["subagent_session_id"],
        str(value["phase"]),
        value["role"],
        str(value["round"]),
        value["seat"],
    )).encode("utf-8")).hexdigest()
    expected_name = "%s-%s-%s-%s-%s.json" % (
        value["phase"], value["round"], value["role"], value["seat"],
        expected_receipt_id[7:31],
    )
    if value["receipt_id"] != expected_receipt_id or entry.name != expected_name:
        emit("CLOSED", 0, "receipt_invalid")
    receipt_id = value["receipt_id"]
    if receipt_id in receipt_ids:
        emit("CLOSED", 0, "receipt_duplicate")
    receipt_ids.add(receipt_id)
    if (
        value.get("phase") == phase
        and value.get("role") == role
        and value.get("round") == round_number
    ):
        seats.add(value["seat"])

if len(seats) < minimum:
    emit("CLOSED", len(seats), "seats_missing")
emit("OPEN", len(seats), "clean")
PY
