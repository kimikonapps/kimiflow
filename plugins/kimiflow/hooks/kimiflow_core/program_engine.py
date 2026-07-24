"""Optional deterministic scheduler for multi-run Kimiflow programs."""

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import PurePosixPath

from memory_router import store


_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_REF = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
_COMMIT = re.compile(r"^[a-f0-9]{40,64}$")
_TASK_STATUSES = {"pending", "ready", "active", "completed", "failed", "parked"}
_CHECK_STATUSES = {"pending", "passed", "failed"}
_TOP_KEYS = {
    "schema_version", "id", "goal", "status", "acceptance", "tasks", "checks", "activation",
}
_TASK_KEYS = {
    "id", "goal", "order", "depends_on", "run", "intent_sha256", "status",
    "completion_evidence",
}
_CHECK_KEYS = {"id", "acceptance_refs", "argv", "status", "receipt"}
_COMPLETION_KEYS = {
    "run", "claim_sha256", "intent_sha256", "state_sha256", "verification_sha256", "commit",
}
_RECEIPT_KEYS = {
    "program_id", "check_id", "contract_sha256", "argv_sha256",
    "task_evidence_sha256", "head", "exit_code", "output_sha256",
}
_VERIFICATION = (
    "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->"
)
_CONFORMANCE = re.compile(
    r"<!-- kimiflow:conformance contract=1 status=converged "
    r"diff=passed strategy=passed architecture=(?:passed|not_applicable) "
    r"research=(?:stable|not_applicable) "
    r"scope=passed decisions=[0-9]+ checks=[0-9]+ "
    r"verifier=(?:folded|independent) source=current-run -->"
)


class ProgramError(ValueError):
    pass


class ClaimConflict(ProgramError):
    pass


class SimulatedProgramCrash(RuntimeError):
    """Test-only fault injection after one durable activation boundary."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _git(root, *args):
    try:
        return subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ProgramError("git unavailable") from exc


def _root_for_program(program_path):
    program_path = os.path.abspath(program_path)
    proc = _git(os.path.dirname(program_path), "rev-parse", "--show-toplevel")
    if proc.returncode:
        raise ProgramError("Program is not inside a Git workspace")
    root = os.path.realpath(proc.stdout.decode("utf-8", "strict").strip())
    try:
        relative = os.path.relpath(program_path, root)
    except ValueError as exc:
        raise ProgramError("Program path escapes workspace") from exc
    parts = PurePosixPath(relative.replace(os.sep, "/")).parts
    if (
        len(parts) != 4
        or parts[0] != ".kimiflow"
        or parts[1] != "programs"
        or not _ID.fullmatch(parts[2])
        or parts[3] != "PROGRAM.json"
    ):
        raise ProgramError("Program must be .kimiflow/programs/<name>/PROGRAM.json")
    store.require_local_path(root, program_path)
    return root, program_path, parts[2]


def _run_path(root, value):
    if not isinstance(value, str) or "\\" in value:
        raise ProgramError("task run invalid")
    path = PurePosixPath(value)
    if (
        len(path.parts) != 2
        or path.parts[0] != ".kimiflow"
        or not _ID.fullmatch(path.parts[1])
        or value != path.as_posix()
    ):
        raise ProgramError("task run must be .kimiflow/<slug>")
    target = os.path.join(root, *path.parts)
    try:
        store.require_local_path(root, target)
    except ValueError as exc:
        raise ProgramError("unsafe task Run path") from exc
    return target


def _bounded_string(value, label, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ProgramError("%s invalid" % label)
    return value.strip()


def _completion_valid(value, status):
    if not isinstance(value, dict) or set(value) != _COMPLETION_KEYS:
        return False
    if not isinstance(value.get("run"), str):
        return False
    for key in ("claim_sha256", "intent_sha256", "state_sha256"):
        if not isinstance(value.get(key), str) or not _SHA.fullmatch(value[key]):
            return False
    verification = value.get("verification_sha256")
    if status == "completed":
        if not isinstance(verification, str) or not _SHA.fullmatch(verification):
            return False
    elif verification is not None and (not isinstance(verification, str) or not _SHA.fullmatch(verification)):
        return False
    return isinstance(value.get("commit"), str) and bool(_COMMIT.fullmatch(value["commit"]))


def _receipt_valid(value):
    if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
        return False
    for key in (
        "contract_sha256", "argv_sha256", "task_evidence_sha256", "output_sha256"
    ):
        if not isinstance(value.get(key), str) or not _SHA.fullmatch(value[key]):
            return False
    return (
        isinstance(value.get("program_id"), str)
        and isinstance(value.get("check_id"), str)
        and isinstance(value.get("head"), str)
        and bool(_COMMIT.fullmatch(value["head"]))
        and isinstance(value.get("exit_code"), int)
        and not isinstance(value.get("exit_code"), bool)
    )


def validate_program(program, directory_name=None):
    if not isinstance(program, dict) or set(program) != _TOP_KEYS:
        raise ProgramError("Program properties invalid")
    if program.get("schema_version") != 1:
        raise ProgramError("Program schema_version must be 1")
    program_id = program.get("id")
    if not isinstance(program_id, str) or not _ID.fullmatch(program_id):
        raise ProgramError("Program id invalid")
    if directory_name is not None and program_id != directory_name:
        raise ProgramError("Program id must match its directory")
    _bounded_string(program.get("goal"), "Program goal", 2000)
    if program.get("status") not in {"active", "completed", "failed", "parked"}:
        raise ProgramError("Program status invalid")

    acceptance = program.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance or len(acceptance) > 128:
        raise ProgramError("Program acceptance invalid")
    acceptance_ids = []
    for row in acceptance:
        if not isinstance(row, dict) or set(row) != {"id", "description"}:
            raise ProgramError("acceptance row invalid")
        if not isinstance(row.get("id"), str) or not _REF.fullmatch(row["id"]):
            raise ProgramError("acceptance id invalid")
        _bounded_string(row.get("description"), "acceptance description", 1000)
        acceptance_ids.append(row["id"])
    if len(set(acceptance_ids)) != len(acceptance_ids):
        raise ProgramError("duplicate acceptance id")

    tasks = program.get("tasks")
    if not isinstance(tasks, list) or not tasks or len(tasks) > 512:
        raise ProgramError("Program tasks invalid")
    task_ids = []
    orders = []
    runs = []
    for task in tasks:
        if not isinstance(task, dict) or set(task) != _TASK_KEYS:
            raise ProgramError("task properties invalid")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ProgramError("task id invalid")
        _bounded_string(task.get("goal"), "task goal", 2000)
        order = task.get("order")
        if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 1000000:
            raise ProgramError("task order invalid")
        dependencies = task.get("depends_on")
        if (
            not isinstance(dependencies, list)
            or len(dependencies) > 128
            or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in dependencies)
            or len(set(dependencies)) != len(dependencies)
            or task_id in dependencies
        ):
            raise ProgramError("task dependencies invalid")
        run = task.get("run")
        if not isinstance(run, str):
            raise ProgramError("task run invalid")
        run_path = PurePosixPath(run)
        if (
            len(run_path.parts) != 2
            or run_path.parts[0] != ".kimiflow"
            or not _ID.fullmatch(run_path.parts[1])
            or run != run_path.as_posix()
        ):
            raise ProgramError("task run invalid")
        if not isinstance(task.get("intent_sha256"), str) or not _SHA.fullmatch(task["intent_sha256"]):
            raise ProgramError("task intent digest invalid")
        status = task.get("status")
        if status not in _TASK_STATUSES:
            raise ProgramError("task status invalid")
        evidence = task.get("completion_evidence")
        if status in {"completed", "failed", "parked"}:
            if not _completion_valid(evidence, status) or evidence.get("run") != run:
                raise ProgramError("terminal task evidence invalid")
        elif evidence is not None:
            raise ProgramError("nonterminal task cannot have completion evidence")
        task_ids.append(task_id)
        orders.append(order)
        runs.append(run)
    if len(set(task_ids)) != len(task_ids) or len(set(orders)) != len(orders):
        raise ProgramError("duplicate task id or order")
    if len(set(runs)) != len(runs):
        raise ProgramError("duplicate task run")
    known = set(task_ids)
    for task in tasks:
        if any(item not in known for item in task["depends_on"]):
            raise ProgramError("unknown task dependency")

    visiting = set()
    visited = set()
    by_id = {task["id"]: task for task in tasks}

    def visit(task_id):
        if task_id in visiting:
            raise ProgramError("task dependency cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in by_id[task_id]["depends_on"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_ids:
        visit(task_id)
    for task in tasks:
        if task["status"] != "pending" and any(
            by_id[dependency]["status"] != "completed" for dependency in task["depends_on"]
        ):
            raise ProgramError("non-pending task has unmet dependency")
    active_tasks = [task for task in tasks if task["status"] == "active"]
    if len(active_tasks) > 1:
        raise ProgramError("more than one active task")

    checks = program.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) > 64:
        raise ProgramError("Program checks invalid")
    check_ids = []
    covered = set()
    for check in checks:
        if not isinstance(check, dict) or set(check) != _CHECK_KEYS:
            raise ProgramError("check properties invalid")
        check_id = check.get("id")
        if not isinstance(check_id, str) or not _ID.fullmatch(check_id):
            raise ProgramError("check id invalid")
        refs = check.get("acceptance_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or any(item not in acceptance_ids for item in refs)
            or len(set(refs)) != len(refs)
        ):
            raise ProgramError("check acceptance coverage invalid")
        argv = check.get("argv")
        if (
            not isinstance(argv, list)
            or not 1 <= len(argv) <= 32
            or any(not isinstance(item, str) or not item or len(item) > 2000 or "\x00" in item for item in argv)
        ):
            raise ProgramError("check argv invalid")
        status = check.get("status")
        if status not in _CHECK_STATUSES:
            raise ProgramError("check status invalid")
        receipt = check.get("receipt")
        if status == "pending":
            if receipt is not None:
                raise ProgramError("pending check cannot have receipt")
        elif not _receipt_valid(receipt) or receipt["program_id"] != program_id or receipt["check_id"] != check_id:
            raise ProgramError("check receipt invalid")
        check_ids.append(check_id)
        covered.update(refs)
    if len(set(check_ids)) != len(check_ids):
        raise ProgramError("duplicate check id")
    if covered != set(acceptance_ids):
        raise ProgramError("acceptance is not fully covered")

    activation = program.get("activation")
    if activation is not None:
        if (
            not isinstance(activation, dict)
            or set(activation)
            != {"task_id", "claim_digest", "linearized", "acknowledged"}
            or activation.get("task_id") not in known
            or not isinstance(activation.get("claim_digest"), str)
            or not _SHA.fullmatch(activation["claim_digest"])
            or not isinstance(activation.get("linearized"), bool)
            or not isinstance(activation.get("acknowledged"), bool)
            or (activation["acknowledged"] and not activation["linearized"])
        ):
            raise ProgramError("activation journal invalid")
        if by_id[activation["task_id"]]["status"] not in {
            "pending", "ready", "active", "completed", "failed", "parked"
        }:
            raise ProgramError("activation task state invalid")
        activation_task = by_id[activation["task_id"]]
        if activation_task["status"] in {"pending", "ready"} and (
            activation["linearized"] or activation["acknowledged"]
        ):
            raise ProgramError("pending activation cannot be linearized")
        if activation_task["status"] in {"completed", "failed", "parked"} and not (
            activation["linearized"] and activation["acknowledged"]
        ):
            raise ProgramError("terminal activation must be acknowledged")
        if any(
            by_id[dependency]["status"] != "completed"
            for dependency in activation_task["depends_on"]
        ):
            raise ProgramError("activation task has unmet dependency")
    if active_tasks and (activation is None or activation["task_id"] != active_tasks[0]["id"]):
        raise ProgramError("active task requires matching claim binding")
    if program["status"] == "completed":
        if any(task["status"] != "completed" for task in tasks) or any(
            check["status"] != "passed" for check in checks
        ):
            raise ProgramError("completed Program has unfinished work")
    return program


def _read_program_locked(root, program_path, directory_name):
    snapshot = store.stable_file_snapshot(program_path, max_bytes=2 * 1024 * 1024)
    try:
        text = snapshot[1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProgramError("Program encoding invalid") from exc
    program = store.parse_json_object_strict(text)
    if program is None:
        raise ProgramError("Program JSON malformed or duplicated")
    return validate_program(program, directory_name), text, snapshot


def load_program(program_path):
    root, program_path, directory_name = _root_for_program(program_path)
    with store.local_path_guard(root, os.path.dirname(program_path)):
        program, _text, _snapshot = _read_program_locked(root, program_path, directory_name)
    return program


def contract_digest(program):
    contract = {
        "schema_version": program["schema_version"],
        "id": program["id"],
        "goal": program["goal"],
        "acceptance": copy.deepcopy(program["acceptance"]),
        "tasks": [
            {
                key: copy.deepcopy(task[key])
                for key in ("id", "goal", "order", "depends_on", "run", "intent_sha256")
            }
            for task in program["tasks"]
        ],
        "checks": [
            {
                key: copy.deepcopy(check[key])
                for key in ("id", "acceptance_refs", "argv")
            }
            for check in program["checks"]
        ],
    }
    return _sha(_canonical(contract).encode("utf-8"))


def _next_from(program):
    if (
        program["activation"] is not None
        and not program["activation"]["acknowledged"]
    ):
        task = next(
            task
            for task in program["tasks"]
            if task["id"] == program["activation"]["task_id"]
        )
        return {"status": "recovering", "task": copy.deepcopy(task)}
    active = [task for task in program["tasks"] if task["status"] == "active"]
    if active:
        return {"status": "active", "task": copy.deepcopy(active[0])}
    if program["activation"] is not None:
        task = next(task for task in program["tasks"] if task["id"] == program["activation"]["task_id"])
        return {"status": "recovering", "task": copy.deepcopy(task)}
    by_id = {task["id"]: task for task in program["tasks"]}
    eligible = [
        task
        for task in program["tasks"]
        if task["status"] in {"pending", "ready"}
        and all(by_id[dependency]["status"] == "completed" for dependency in task["depends_on"])
    ]
    eligible.sort(key=lambda task: (task["order"], task["id"]))
    return {"status": "ready", "task": copy.deepcopy(eligible[0])} if eligible else {
        "status": "blocked", "task": None
    }


def _next_with_current_evidence(root, program):
    if program["activation"] is not None:
        task = next(
            task
            for task in program["tasks"]
            if task["id"] == program["activation"]["task_id"]
        )
        if not program["activation"]["acknowledged"] or (
            task["status"] == "active"
            and not _active_binding_current(root, program, task)
        ):
            return {"status": "recovering", "task": copy.deepcopy(task)}
    if any(task["status"] == "active" for task in program["tasks"]) or (
        program["activation"] is not None
    ):
        return _next_from(program)
    terminal = sorted(
        (
            task
            for task in program["tasks"]
            if task["status"] in {"completed", "failed", "parked"}
            and not _task_evidence_current(root, program, task)
        ),
        key=lambda task: (task["order"], task["id"]),
    )
    if terminal:
        return {"status": "recovering", "task": copy.deepcopy(terminal[0])}
    return _next_from(program)


def next_ready(program_path):
    root, program_path, directory_name = _root_for_program(program_path)
    with store.local_path_guard(root, os.path.dirname(program_path)):
        program, _text, _snapshot = _read_program_locked(
            root, program_path, directory_name
        )
        return _next_with_current_evidence(root, program)


def _write_program(program_path, program, expected_text, expected_snapshot):
    payload = json.dumps(program, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    store.atomic_write(
        program_path,
        payload,
        expected=expected_text,
        expected_snapshot=expected_snapshot,
        max_bytes=2 * 1024 * 1024,
        durable=True,
    )


def _claim_payload(program, task):
    return {
        "schema_version": 1,
        "program_id": program["id"],
        "task_id": task["id"],
        "run": task["run"],
        "intent_sha256": task["intent_sha256"],
        "contract_sha256": contract_digest(program),
    }


def _claim_bytes(program, task):
    return (_canonical(_claim_payload(program, task)) + "\n").encode("utf-8")


def _anchor_is_current(anchor):
    try:
        pinned = os.fstat(anchor["descriptor"])
        current = os.stat(anchor["path"], follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISDIR(current.st_mode) and (
        pinned.st_dev, pinned.st_ino
    ) == (current.st_dev, current.st_ino)


def _read_claim(anchor):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("PROGRAM-CLAIM.json", flags, dir_fd=anchor["descriptor"])
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProgramError("unsafe Program claim") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
            raise ProgramError("unsafe Program claim")
        chunks = []
        while True:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(map(len, chunks)) > 8192:
                raise ProgramError("Program claim too large")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _require_run_intent(anchor, task):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("INTENT.md", flags, dir_fd=anchor["descriptor"])
    except OSError as exc:
        raise ProgramError("Run Intent missing or unsafe") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024 * 1024:
            raise ProgramError("Run Intent missing or unsafe")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                raise ProgramError("Run Intent changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ProgramError("Run Intent changed while reading")
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_identity != after_identity or _sha(b"".join(chunks)) != task["intent_sha256"]:
            raise ProgramError("Run Intent does not match task")
    finally:
        os.close(descriptor)


def _ensure_claim(anchor, expected):
    existing = _read_claim(anchor)
    if existing is not None:
        if existing != expected:
            raise ClaimConflict("Run already belongs to another Program task")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("PROGRAM-CLAIM.json", flags, 0o600, dir_fd=anchor["descriptor"])
    except FileExistsError:
        existing = _read_claim(anchor)
        if existing != expected:
            raise ClaimConflict("Run already belongs to another Program task")
        return
    except OSError as exc:
        raise ProgramError("cannot create Program claim") from exc
    try:
        offset = 0
        while offset < len(expected):
            offset += os.write(descriptor, expected[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(anchor["descriptor"])


def _remove_claim_if_matches(anchor, expected):
    existing = _read_claim(anchor)
    if existing is None:
        return
    if existing != expected:
        raise ClaimConflict("Run already belongs to another Program task")
    os.unlink("PROGRAM-CLAIM.json", dir_fd=anchor["descriptor"])
    os.fsync(anchor["descriptor"])


def _active_binding_current(root, program, task):
    run_dir = _run_path(root, task["run"])
    expected = _claim_bytes(program, task)
    try:
        with store.local_path_guard(root, run_dir) as anchor:
            return (
                _read_claim(anchor) == expected
                and _anchor_is_current(anchor)
            )
    except (OSError, ValueError, ProgramError):
        return False


def _clear_journal(root, program_path, directory_name):
    program, text, snapshot = _read_program_locked(root, program_path, directory_name)
    journal = program["activation"]
    if journal is not None:
        task = next(row for row in program["tasks"] if row["id"] == journal["task_id"])
        if task["status"] in {"active", "completed", "failed", "parked"}:
            return
    program = copy.deepcopy(program)
    program["activation"] = None
    _write_program(program_path, program, text, snapshot)


def _dependencies_current(root, program, task):
    by_id = {row["id"]: row for row in program["tasks"]}
    return all(
        _task_evidence_current(root, program, by_id[dependency])
        for dependency in task["depends_on"]
    )


def _rollback_unacknowledged(
    root, program_path, directory_name, anchor, expected, journal
):
    """Release the Run claim before clearing its durable recovery journal."""
    _remove_claim_if_matches(anchor, expected)
    current, current_text, current_snapshot = _read_program_locked(
        root, program_path, directory_name
    )
    if current["activation"] != journal or journal["acknowledged"]:
        raise ProgramError("activation changed during rollback")
    current_task = next(
        row for row in current["tasks"] if row["id"] == journal["task_id"]
    )
    if current_task["status"] not in {"pending", "ready", "active"}:
        raise ProgramError("activation task changed during rollback")
    rollback = copy.deepcopy(current)
    rollback["activation"] = None
    rollback_task = next(
        row for row in rollback["tasks"] if row["id"] == journal["task_id"]
    )
    if rollback_task["status"] == "active":
        rollback_task["status"] = "pending"
    _write_program(program_path, rollback, current_text, current_snapshot)
    return rollback


def _linearize_activation(root, program_path, directory_name, journal):
    """Durably mark the activation point before its confirming evidence read."""
    current, current_text, current_snapshot = _read_program_locked(
        root, program_path, directory_name
    )
    if (
        current["activation"] != journal
        or journal["linearized"]
        or journal["acknowledged"]
    ):
        raise ProgramError("activation changed before linearization")
    current_task = next(
        row for row in current["tasks"] if row["id"] == journal["task_id"]
    )
    if current_task["status"] != "active":
        raise ProgramError("activation task changed before linearization")
    linearized = copy.deepcopy(current)
    linearized["activation"]["linearized"] = True
    _write_program(program_path, linearized, current_text, current_snapshot)
    return linearized


def _acknowledge_activation(root, program_path, directory_name, journal):
    """Confirm the linearized activation after its post-CAS evidence read."""
    current, current_text, current_snapshot = _read_program_locked(
        root, program_path, directory_name
    )
    if (
        current["activation"] != journal
        or not journal["linearized"]
        or journal["acknowledged"]
    ):
        raise ProgramError("activation changed before acknowledgement")
    current_task = next(
        row for row in current["tasks"] if row["id"] == journal["task_id"]
    )
    if current_task["status"] != "active":
        raise ProgramError("activation task changed before acknowledgement")
    acknowledged = copy.deepcopy(current)
    acknowledged["activation"]["acknowledged"] = True
    _write_program(program_path, acknowledged, current_text, current_snapshot)
    return acknowledged


def _reconcile_locked(root, program_path, directory_name):
    program, text, snapshot = _read_program_locked(root, program_path, directory_name)
    journal = program["activation"]
    if journal is None:
        return program
    task = next(task for task in program["tasks"] if task["id"] == journal["task_id"])
    expected = _claim_bytes(program, task)
    if _sha(expected) != journal["claim_digest"]:
        raise ProgramError("activation journal claim digest mismatch")
    run_dir = _run_path(root, task["run"])
    try:
        with store.local_path_guard(root, run_dir) as anchor:
            _require_run_intent(anchor, task)
            if not journal["acknowledged"] and not _dependencies_current(
                root, program, task
            ):
                _rollback_unacknowledged(
                    root, program_path, directory_name, anchor, expected, journal
                )
                raise ProgramError("activation task dependency evidence stale")
            if journal["linearized"] and not journal["acknowledged"]:
                if _read_claim(anchor) != expected:
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, expected, journal
                    )
                    raise ProgramError("Program claim changed during activation")
            else:
                _ensure_claim(anchor, expected)
            if not _anchor_is_current(anchor):
                raise ProgramError("Run parent changed during activation")
            current, current_text, current_snapshot = _read_program_locked(
                root, program_path, directory_name
            )
            if current["activation"] != journal:
                raise ProgramError("activation journal changed")
            current_task = next(row for row in current["tasks"] if row["id"] == task["id"])
            if current_task["status"] in {"pending", "ready"}:
                if not _dependencies_current(root, current, current_task):
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, expected, journal
                    )
                    raise ProgramError("activation task dependency evidence stale")
                current = copy.deepcopy(current)
                current_task = next(
                    row for row in current["tasks"] if row["id"] == task["id"]
                )
                current_task["status"] = "active"
                _write_program(program_path, current, current_text, current_snapshot)
            if not journal["acknowledged"]:
                current_task = next(
                    row for row in current["tasks"] if row["id"] == task["id"]
                )
                if not _dependencies_current(root, current, current_task):
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, expected, journal
                    )
                    raise ProgramError(
                        "dependency evidence changed during activation"
                    )
                if _read_claim(anchor) != expected:
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, expected, journal
                    )
                    raise ProgramError("Program claim changed during activation")
                if not _anchor_is_current(anchor):
                    raise ProgramError("Run parent changed during activation")
                if not journal["linearized"]:
                    current = _linearize_activation(
                        root, program_path, directory_name, journal
                    )
                    journal = current["activation"]
                    current_task = next(
                        row for row in current["tasks"] if row["id"] == task["id"]
                    )
                if not _dependencies_current(root, current, current_task):
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, expected, journal
                    )
                    raise ProgramError(
                        "dependency evidence changed during activation"
                    )
                if _read_claim(anchor) != expected:
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, expected, journal
                    )
                    raise ProgramError("Program claim changed during activation")
                if not _anchor_is_current(anchor):
                    raise ProgramError("Run parent changed during activation")
                current = _acknowledge_activation(
                    root, program_path, directory_name, journal
                )
            if not _anchor_is_current(anchor):
                raise ProgramError("Run parent changed during activation")
            return current
    except ClaimConflict:
        _clear_journal(root, program_path, directory_name)
        raise


def activate(program_path, task_id, *, write=False, _crash_after=None):
    root, program_path, directory_name = _root_for_program(program_path)
    with store.path_lock(program_path), store.local_path_guard(root, os.path.dirname(program_path)):
        program, text, snapshot = _read_program_locked(root, program_path, directory_name)
        if not write:
            selected = _next_with_current_evidence(root, program)
            if selected["task"] is None or selected["task"]["id"] != task_id:
                raise ProgramError("task is not the deterministic next-ready task")
            return {"status": "preview", "task": selected["task"]}
        if write and program["activation"] is not None:
            program = _reconcile_locked(root, program_path, directory_name)
            program, text, snapshot = _read_program_locked(root, program_path, directory_name)
            bound_task = next(
                task
                for task in program["tasks"]
                if task["id"] == program["activation"]["task_id"]
            )
            if bound_task["status"] in {"completed", "failed", "parked"}:
                raise ProgramError("terminal task claim requires close retry")
        active = [task for task in program["tasks"] if task["status"] == "active"]
        if active:
            if active[0]["id"] == task_id:
                return {"status": "active", "task": copy.deepcopy(active[0])}
            raise ProgramError("another Program task is active")
        selected = _next_with_current_evidence(root, program)
        if selected["task"] is None or selected["task"]["id"] != task_id:
            raise ProgramError("task is not the deterministic next-ready task")
        if selected["status"] == "recovering":
            raise ProgramError("terminal task evidence requires close retry")
        task = next(task for task in program["tasks"] if task["id"] == task_id)
        claim = _claim_bytes(program, task)
        journal = {
            "task_id": task_id,
            "claim_digest": _sha(claim),
            "linearized": False,
            "acknowledged": False,
        }
        pending = copy.deepcopy(program)
        pending["activation"] = journal
        _write_program(program_path, pending, text, snapshot)
        if _crash_after == "journal":
            raise SimulatedProgramCrash("after journal")
        run_dir = _run_path(root, task["run"])
        try:
            with store.local_path_guard(root, run_dir) as anchor:
                _require_run_intent(anchor, task)
                _ensure_claim(anchor, claim)
                if _crash_after == "claim":
                    raise SimulatedProgramCrash("after claim")
                if not _anchor_is_current(anchor):
                    raise ProgramError("Run parent changed during activation")
                current, current_text, current_snapshot = _read_program_locked(
                    root, program_path, directory_name
                )
                if current["activation"] != journal:
                    raise ProgramError("activation journal changed")
                current_task = next(
                    row for row in current["tasks"] if row["id"] == task_id
                )
                if not _dependencies_current(root, current, current_task):
                    _remove_claim_if_matches(anchor, claim)
                    _clear_journal(root, program_path, directory_name)
                    raise ProgramError("activation task dependency evidence stale")
                current = copy.deepcopy(current)
                current_task = next(row for row in current["tasks"] if row["id"] == task_id)
                current_task["status"] = "active"
                _write_program(program_path, current, current_text, current_snapshot)
                if _crash_after == "active":
                    raise SimulatedProgramCrash("after active Program CAS")
                if not _dependencies_current(root, current, current_task):
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, claim, journal
                    )
                    raise ProgramError(
                        "dependency evidence changed during activation"
                    )
                if _read_claim(anchor) != claim:
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, claim, journal
                    )
                    raise ProgramError("Program claim changed during activation")
                if not _anchor_is_current(anchor):
                    raise ProgramError("Run parent changed during activation")
                current = _linearize_activation(
                    root, program_path, directory_name, journal
                )
                journal = current["activation"]
                current_task = next(
                    row for row in current["tasks"] if row["id"] == task_id
                )
                if _crash_after == "linearized":
                    raise SimulatedProgramCrash("after activation linearization")
                if not _dependencies_current(root, current, current_task):
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, claim, journal
                    )
                    raise ProgramError(
                        "dependency evidence changed during activation"
                    )
                if _read_claim(anchor) != claim:
                    _rollback_unacknowledged(
                        root, program_path, directory_name, anchor, claim, journal
                    )
                    raise ProgramError("Program claim changed during activation")
                if not _anchor_is_current(anchor):
                    raise ProgramError("Run parent changed during activation")
                current = _acknowledge_activation(
                    root, program_path, directory_name, journal
                )
                if not _anchor_is_current(anchor):
                    raise ProgramError("Run parent changed during activation")
                current_task = next(
                    row for row in current["tasks"] if row["id"] == task_id
                )
                return {"status": "active", "task": copy.deepcopy(current_task)}
        except ClaimConflict:
            _clear_journal(root, program_path, directory_name)
            raise


def _state_value(text, key):
    found = []
    for raw in text.splitlines():
        line = raw.strip().removeprefix("-").strip().replace("**", "")
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip().lower() == key.lower():
            found.append(value.strip().lower().split(" ", 1)[0])
    return found[0] if len(found) == 1 else ""


def _terminal_evidence(root, program, task, target_status):
    run_dir = _run_path(root, task["run"])
    expected_claim = _claim_bytes(program, task)
    try:
        with store.local_path_guard(root, run_dir) as anchor:
            claim = _read_claim(anchor)
            if claim != expected_claim:
                raise ProgramError("matching Run claim required")
            intent = store.stable_local_file_bytes(root, os.path.join(run_dir, "INTENT.md"))
            state_bytes = store.stable_local_file_bytes(root, os.path.join(run_dir, "STATE.md"))
            verification = store.stable_local_file_bytes(
                root, os.path.join(run_dir, "VERIFICATION.md"), missing_ok=True
            )
            if not _anchor_is_current(anchor):
                raise ProgramError("Run parent changed during completion")
    except ValueError as exc:
        raise ProgramError("terminal Run evidence missing or unsafe") from exc
    if _sha(intent) != task["intent_sha256"]:
        raise ProgramError("Run Intent does not match task")
    state_text = state_bytes.decode("utf-8", "strict")
    if target_status == "completed":
        if (
            _state_value(state_text, "Status") != "done"
            or _state_value(state_text, "Phase 6") != "done"
            or _state_value(state_text, "Phase 7") != "done"
            or verification is None
        ):
            raise ProgramError("completed task requires terminal Run")
        verification_text = verification.decode("utf-8", "strict")
        if _VERIFICATION not in verification_text or not _CONFORMANCE.search(verification_text):
            raise ProgramError("completed task requires converged verification")
    else:
        if _state_value(state_text, "Status") != target_status:
            raise ProgramError("terminal task state mismatch")
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode:
        raise ProgramError("HEAD unavailable")
    return {
        "run": task["run"],
        "claim_sha256": _sha(claim),
        "intent_sha256": _sha(intent),
        "state_sha256": _sha(state_bytes),
        "verification_sha256": _sha(verification) if verification is not None else None,
        "commit": head.stdout.decode("ascii", "strict").strip(),
    }


def _task_evidence_current(root, program, task):
    evidence = task.get("completion_evidence")
    if task["status"] not in {"completed", "failed", "parked"} or not isinstance(
        evidence, dict
    ):
        return False
    run_dir = _run_path(root, task["run"])
    expected_claim = _claim_bytes(program, task)
    try:
        with store.local_path_guard(root, run_dir) as anchor:
            claim = _read_claim(anchor)
            intent = store.stable_local_file_bytes(
                root, os.path.join(run_dir, "INTENT.md")
            )
            state_bytes = store.stable_local_file_bytes(
                root, os.path.join(run_dir, "STATE.md")
            )
            verification = store.stable_local_file_bytes(
                root,
                os.path.join(run_dir, "VERIFICATION.md"),
                missing_ok=True,
            )
            if not _anchor_is_current(anchor):
                return False
    except (OSError, ValueError, ProgramError):
        return False
    if (
        claim != expected_claim
        or evidence.get("claim_sha256") != _sha(claim)
        or evidence.get("intent_sha256") != _sha(intent)
        or evidence.get("state_sha256") != _sha(state_bytes)
        or evidence.get("verification_sha256")
        != (_sha(verification) if verification is not None else None)
    ):
        return False
    try:
        state_text = state_bytes.decode("utf-8", "strict")
        verification_text = (
            verification.decode("utf-8", "strict")
            if verification is not None
            else ""
        )
    except UnicodeError:
        return False
    if task["status"] == "completed":
        if (
            _state_value(state_text, "Status") != "done"
            or _state_value(state_text, "Phase 6") != "done"
            or _state_value(state_text, "Phase 7") != "done"
            or _VERIFICATION not in verification_text
            or not _CONFORMANCE.search(verification_text)
        ):
            return False
    elif _state_value(state_text, "Status") != task["status"]:
        return False
    commit = evidence.get("commit")
    return (
        isinstance(commit, str)
        and _git(root, "merge-base", "--is-ancestor", commit, "HEAD").returncode == 0
    )


def _all_task_evidence_current(root, program):
    return all(_task_evidence_current(root, program, task) for task in program["tasks"])


def _terminal_program_status(tasks):
    statuses = {task["status"] for task in tasks}
    if "failed" in statuses:
        return "failed"
    if "parked" in statuses:
        return "parked"
    return "active"


def close_task(program_path, task_id, target_status, *, write=False):
    if target_status not in {"completed", "failed", "parked"}:
        raise ProgramError("terminal task status invalid")
    root, program_path, directory_name = _root_for_program(program_path)
    with store.path_lock(program_path), store.local_path_guard(root, os.path.dirname(program_path)):
        program, text, snapshot = _read_program_locked(root, program_path, directory_name)
        if write and program["activation"] is not None:
            program = _reconcile_locked(root, program_path, directory_name)
            program, text, snapshot = _read_program_locked(root, program_path, directory_name)
        task = next((row for row in program["tasks"] if row["id"] == task_id), None)
        if (
            task is not None
            and task["status"] == target_status
            and program["activation"] is None
        ):
            if _task_evidence_current(root, program, task):
                return {
                    "status": target_status if write else "preview",
                    "task": copy.deepcopy(task),
                }
            if not write:
                return {"status": "preview", "task": copy.deepcopy(task)}
            claim = _claim_bytes(program, task)
            rebound = copy.deepcopy(program)
            rebound["activation"] = {
                "task_id": task_id,
                "claim_digest": _sha(claim),
                "linearized": True,
                "acknowledged": True,
            }
            _write_program(program_path, rebound, text, snapshot)
            program = _reconcile_locked(root, program_path, directory_name)
            program, text, snapshot = _read_program_locked(
                root, program_path, directory_name
            )
            task = next(row for row in program["tasks"] if row["id"] == task_id)
        terminal_retry = (
            write
            and task is not None
            and task["status"] == target_status
            and program["activation"] is not None
            and program["activation"]["task_id"] == task_id
        )
        if task is None or (task["status"] != "active" and not terminal_retry):
            raise ProgramError("task is not active")
        evidence = _terminal_evidence(root, program, task, target_status)
        result = copy.deepcopy(program)
        result_task = next(row for row in result["tasks"] if row["id"] == task_id)
        result_task["status"] = target_status
        result_task["completion_evidence"] = evidence
        result["status"] = _terminal_program_status(result["tasks"])
        if write:
            _write_program(program_path, result, text, snapshot)
            if not _task_evidence_current(root, result, result_task):
                raise ProgramError("terminal Run evidence changed during completion")
            current, current_text, current_snapshot = _read_program_locked(
                root, program_path, directory_name
            )
            if (
                current["activation"] != result["activation"]
                or current["activation"] is None
                or current["activation"]["task_id"] != task_id
            ):
                raise ProgramError("terminal claim binding changed")
            current = copy.deepcopy(current)
            current["activation"] = None
            _write_program(program_path, current, current_text, current_snapshot)
            current_task = next(
                row for row in current["tasks"] if row["id"] == task_id
            )
            if not _task_evidence_current(root, current, current_task):
                raise ProgramError("terminal Run evidence changed during finalization")
        return {
            "status": target_status if write else "preview",
            "task": copy.deepcopy(result_task),
        }


def complete_task(program_path, task_id, *, write=False):
    return close_task(program_path, task_id, "completed", write=write)


def _head(root):
    proc = _git(root, "rev-parse", "HEAD")
    if proc.returncode:
        raise ProgramError("HEAD unavailable")
    return proc.stdout.decode("ascii", "strict").strip()


def _clean(root):
    proc = _git(root, "status", "--porcelain=v1", "--untracked-files=normal")
    if proc.returncode:
        raise ProgramError("worktree status unavailable")
    return not proc.stdout


def _task_evidence_digest(program):
    rows = [
        {"id": task["id"], "evidence": task["completion_evidence"]}
        for task in sorted(program["tasks"], key=lambda row: row["id"])
    ]
    return _sha(_canonical(rows).encode("utf-8"))


def _expected_check_receipt(root, program, check, exit_code=None, output_sha256=None):
    receipt = {
        "program_id": program["id"],
        "check_id": check["id"],
        "contract_sha256": contract_digest(program),
        "argv_sha256": _sha(_canonical(check["argv"]).encode("utf-8")),
        "task_evidence_sha256": _task_evidence_digest(program),
        "head": _head(root),
    }
    if exit_code is not None:
        receipt["exit_code"] = exit_code
        receipt["output_sha256"] = output_sha256
    return receipt


def _check_current(root, program, check, task_evidence_current=None):
    receipt = check.get("receipt")
    if task_evidence_current is None:
        task_evidence_current = _all_task_evidence_current(root, program)
    if (
        check.get("status") != "passed"
        or not _receipt_valid(receipt)
        or not task_evidence_current
        or not _clean(root)
    ):
        return False
    expected = _expected_check_receipt(root, program, check)
    return all(receipt.get(key) == value for key, value in expected.items()) and receipt["exit_code"] == 0


def _derived_status(root, program):
    evidence_current = _all_task_evidence_current(root, program)
    current_checks = [
        check["id"]
        for check in program["checks"]
        if _check_current(root, program, check, evidence_current)
    ]
    completed = (
        all(task["status"] == "completed" for task in program["tasks"])
        and len(current_checks) == len(program["checks"])
    )
    return "completed" if completed else "active", current_checks


def run_check(program_path, check_id, *, write=False, timeout=300):
    root, program_path, directory_name = _root_for_program(program_path)
    with store.path_lock(program_path), store.local_path_guard(root, os.path.dirname(program_path)):
        program, text, snapshot = _read_program_locked(root, program_path, directory_name)
        if write and program["activation"] is not None:
            program = _reconcile_locked(root, program_path, directory_name)
            program, text, snapshot = _read_program_locked(root, program_path, directory_name)
            if program["activation"] is not None:
                raise ProgramError("terminal task claim requires close retry")
        if any(task["status"] != "completed" for task in program["tasks"]):
            raise ProgramError("Program checks require all tasks completed")
        if not _all_task_evidence_current(root, program):
            raise ProgramError("Program checks require current terminal task evidence")
        if not _clean(root):
            raise ProgramError("Program checks require a clean worktree")
        check = next((row for row in program["checks"] if row["id"] == check_id), None)
        if check is None:
            raise ProgramError("check not found")
        if _check_current(root, program, check, True):
            return {"status": "passed", "check": copy.deepcopy(check)}
        if not write:
            return {"status": "preview", "check": copy.deepcopy(check)}
        before_head = _head(root)
        try:
            proc = subprocess.run(
                check["argv"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
            )
            output = proc.stdout[: 1024 * 1024]
            exit_code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b"")[: 1024 * 1024]
            exit_code = 124
        except OSError as exc:
            output = str(exc).encode("utf-8")[: 1024 * 1024]
            exit_code = 127
        if (
            _head(root) != before_head
            or not _clean(root)
            or not _all_task_evidence_current(root, program)
        ):
            raise ProgramError("Program check changed the clean verification basis")
        result = copy.deepcopy(program)
        result_check = next(row for row in result["checks"] if row["id"] == check_id)
        receipt = _expected_check_receipt(
            root, result, result_check, exit_code=exit_code, output_sha256=_sha(output)
        )
        result_check["status"] = "passed" if exit_code == 0 else "failed"
        result_check["receipt"] = receipt
        result["status"], _current = _derived_status(root, result)
        _write_program(program_path, result, text, snapshot)
        return {"status": result_check["status"], "check": copy.deepcopy(result_check)}


def program_status(program_path):
    root, program_path, _directory_name = _root_for_program(program_path)
    program = load_program(program_path)
    derived, current_checks = _derived_status(root, program)
    return {
        "status": derived,
        "stored_status": program["status"],
        "current_checks": current_checks,
        "next": _next_with_current_evidence(root, program),
        "contract_sha256": contract_digest(program),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="program-engine")
    parser.add_argument(
        "command", choices=("validate", "next-ready", "activate", "complete", "close", "run-check", "status")
    )
    parser.add_argument("--program", required=True)
    parser.add_argument("--task")
    parser.add_argument("--check")
    parser.add_argument("--task-status", choices=("failed", "parked"))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            program = load_program(args.program)
            result = {"status": "valid", "program_id": program["id"], "contract_sha256": contract_digest(program)}
        elif args.command == "next-ready":
            result = next_ready(args.program)
        elif args.command == "activate":
            result = activate(args.program, args.task, write=args.write)
        elif args.command == "complete":
            result = complete_task(args.program, args.task, write=args.write)
        elif args.command == "close":
            result = close_task(args.program, args.task, args.task_status, write=args.write)
        elif args.command == "run-check":
            result = run_check(args.program, args.check, write=args.write, timeout=args.timeout)
        else:
            result = program_status(args.program)
    except (ProgramError, store.ConcurrentWriteError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
