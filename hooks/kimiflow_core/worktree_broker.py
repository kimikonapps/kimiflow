"""Deterministic three-slot Fleet routing, leasing and integration."""

import hashlib
import json
import os
import re
import stat
import subprocess

from . import workspace_preflight as wp


BROKER_NAME = "FLEET.json"
LEGACY_BROKER_NAME = "WORKTREE_BROKER.json"
LEGACY_ARCHIVE_NAME = "WORKTREE_BROKER.schema1.migrated.json"
BROKER_SCHEMA = 2
LEGACY_BROKER_SCHEMA = 1
MAX_WORKTREES = 3
MAX_TASKS = 32
MAX_PATHS = 100
MAX_CONTRACTS = 32
MAX_CHECKS = 10
MAX_ARGV = 32
MAX_ARG_BYTES = 2048
MAX_PATH_BYTES = 4096
MAX_PLAN_BYTES = 1048576
MAX_BROKER_BYTES = 262144
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
BRANCH_RE = re.compile(r"^codex/[A-Za-z0-9][A-Za-z0-9._-]*(?:-[0-9]+)?$")
CONTRACT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
TASK_STATES = {
    "queued",
    "allocating",
    "allocated",
    "waiting",
    "ready-to-integrate",
    "reconciling",
    "integrating",
    "integrated",
    "verification-failed",
    "needs-reconcile",
    "retired",
}
JOURNAL_KINDS = {"allocation", "reconcile", "fast-forward", "retirement"}
CRITICAL_CONTRACTS = {
    "build-config",
    "data-contract",
    "generated",
    "lockfile",
    "migration",
    "public-api",
    "schema",
    "shared-token",
}
TASK_KEYS = {
    "run",
    "identity",
    "branch",
    "primary_ref",
    "path",
    "base",
    "state",
    "paths",
    "contracts",
    "collision",
    "action",
    "basis",
    "declared_main",
    "task_head",
    "integrated_head",
    "check_commands",
    "check_results",
    "journal",
    "archive",
    "blocked_by",
    "primary_owner",
    "primary_paths",
    "primary_contracts",
    "primary_basis",
    "verified_main",
    "verified_task",
}
LEGACY_TASK_KEYS = TASK_KEYS - {
    "blocked_by",
    "primary_owner",
    "primary_paths",
    "primary_contracts",
    "primary_basis",
    "verified_main",
    "verified_task",
}


def _git(root, args, check=True):
    return wp.run_git(root, args, check=check)


def _git_text(root, args):
    return _git(root, args).stdout.decode("utf-8", "surrogateescape").strip()


def _kimiflow_only_ignored(root, status):
    if "ignored_only_kimiflow" in status:
        return status["ignored_only_kimiflow"]
    return wp.kimiflow_only_ignored_at(root, status)


def _head(root):
    value = _git_text(root, ["rev-parse", "HEAD"])
    if not SHA_RE.fullmatch(value):
        raise wp.WorkspaceError("cannot resolve commit identity")
    return value


def _ancestor(root, older, newer):
    return _git(root, ["merge-base", "--is-ancestor", older, newer], check=False).returncode == 0


def _primary_ref(root):
    value = _git_text(root, ["symbolic-ref", "-q", "HEAD"])
    name = value.removeprefix("refs/heads/")
    if (
        value == name
        or not name
        or len(value.encode("utf-8", "surrogateescape")) > 300
        or _git(root, ["check-ref-format", "--branch", name], check=False).returncode != 0
    ):
        raise wp.WorkspaceError("primary worktree must have a valid branch")
    return value


def _valid_branch_ref(value):
    if not isinstance(value, str) or not value.startswith("refs/heads/"):
        return False
    name = value[len("refs/heads/") :]
    if (
        not name
        or len(value.encode("utf-8", "surrogateescape")) > 300
        or name.startswith("/")
        or name.endswith(("/", "."))
        or ".." in name
        or "//" in name
        or "@{" in name
        or re.search(r"[\x00-\x20\x7f~^:?*\\[]", name)
    ):
        return False
    return all(
        component
        and not component.startswith(".")
        and not component.endswith(".lock")
        for component in name.split("/")
    )


def _ref_head(root, ref):
    value = _git_text(root, ["rev-parse", "--verify", ref])
    if not SHA_RE.fullmatch(value):
        raise wp.WorkspaceError("cannot resolve branch identity")
    return value


def _slug(run):
    if not wp.RUN_RE.fullmatch(run or ""):
        raise wp.WorkspaceError("run must be .kimiflow/<slug>")
    return run.split("/", 1)[1]


def _normalize_path(value):
    if (
        not isinstance(value, str)
        or not value
        or "\0" in value
        or len(value.encode("utf-8", "surrogateescape")) > MAX_PATH_BYTES
    ):
        raise wp.WorkspaceError("broker path must be a non-empty project-relative path")
    value = value.replace("\\", "/")
    normalized = os.path.normpath(value).replace(os.sep, "/")
    if (
        normalized in {".", ".."}
        or normalized.startswith("../")
        or normalized.startswith("/")
        or normalized == ".git"
        or normalized.startswith(".git/")
        or normalized == ".kimiflow/session"
        or normalized.startswith(".kimiflow/session/")
    ):
        raise wp.WorkspaceError("unsafe broker path")
    return normalized


def normalize_paths(values):
    result = sorted({_normalize_path(value) for value in values})
    if len(result) > MAX_PATHS:
        raise wp.WorkspaceError("too many broker paths")
    return result


def infer_contracts(paths, values=()):
    contracts = set()
    for value in values:
        if not isinstance(value, str) or not CONTRACT_RE.fullmatch(value):
            raise wp.WorkspaceError("invalid broker contract")
        contracts.add(value)
    for path in paths:
        lowered = path.lower()
        name = lowered.rsplit("/", 1)[-1]
        if name in {
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "poetry.lock",
            "pipfile.lock",
            "cargo.lock",
            "gemfile.lock",
        }:
            contracts.add("lockfile")
        if "/migrations/" in "/%s/" % lowered or lowered.startswith("migrations/"):
            contracts.add("migration")
        if "schema" in name or lowered.endswith((".proto", ".graphql")):
            contracts.add("schema")
        if "generated" in lowered or name.endswith((".generated.ts", ".generated.py")):
            contracts.add("generated")
        if name in {"tokens.json", "design-tokens.json", "theme.json"}:
            contracts.add("shared-token")
    if len(contracts) > MAX_CONTRACTS:
        raise wp.WorkspaceError("too many broker contracts")
    return sorted(contracts)


def _path_relation(left, right):
    if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
        return "serialize"
    left_parts = left.split("/")
    right_parts = right.split("/")
    if len(left_parts) >= 2 and len(right_parts) >= 2 and left_parts[:2] == right_parts[:2]:
        return "semantic-review"
    return "disjoint"


def classify_collision(paths, contracts, peer_paths, peer_contracts):
    paths = normalize_paths(paths)
    peer_paths = normalize_paths(peer_paths) if peer_paths else []
    contracts = infer_contracts(paths, contracts)
    peer_contracts = infer_contracts(peer_paths, peer_contracts)
    reasons = []
    verdict = "disjoint"
    if not paths:
        verdict = "semantic-review"
        reasons.append("unknown-task-path-envelope")
    for left in paths:
        for right in peer_paths:
            relation = _path_relation(left, right)
            if relation == "serialize":
                verdict = "serialize"
                reasons.append("path:%s=%s" % (left, right))
            elif relation == "semantic-review" and verdict == "disjoint":
                verdict = "semantic-review"
                reasons.append("boundary:%s~%s" % (left, right))
    shared = sorted(set(contracts) & set(peer_contracts))
    if shared:
        verdict = "serialize"
        reasons.extend("contract:%s" % value for value in shared)
    critical = sorted((set(contracts) | set(peer_contracts)) & CRITICAL_CONTRACTS)
    if critical and not shared and verdict == "semantic-review":
        reasons.extend("critical:%s" % value for value in critical)
    return {
        "status": verdict,
        "action": "disjoint" if verdict == "disjoint" else "serialize",
        "reasons": sorted(set(reasons))[:20],
    }


def _validate_argv(value):
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_ARGV
        or any(
            not isinstance(item, str)
            or not item
            or "\0" in item
            or len(item.encode("utf-8", "surrogateescape")) > MAX_ARG_BYTES
            for item in value
        )
    ):
        raise wp.WorkspaceError("invalid broker check argv")
    return list(value)


def parse_check_arguments(values):
    checks = []
    for raw in values or []:
        try:
            checks.append(_validate_argv(json.loads(raw)))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise wp.WorkspaceError("invalid --check-json") from exc
    if len(checks) > MAX_CHECKS:
        raise wp.WorkspaceError("too many broker checks")
    return checks


def _validate_task(task, legacy=False):
    expected_keys = LEGACY_TASK_KEYS if legacy else TASK_KEYS
    if not isinstance(task, dict) or set(task) != expected_keys:
        raise wp.WorkspaceError("malformed worktree broker task")
    if legacy:
        task = {
            **task,
            "blocked_by": [],
            "primary_owner": "",
            "primary_paths": [],
            "primary_contracts": [],
            "primary_basis": "",
            "verified_main": "",
            "verified_task": "",
        }
    run = task["run"]
    _slug(run)
    if not isinstance(task["identity"], str) or not wp.IDENTITY_RE.fullmatch(task["identity"]):
        raise wp.WorkspaceError("malformed worktree broker task")
    if task["branch"] and not BRANCH_RE.fullmatch(task["branch"]):
        raise wp.WorkspaceError("malformed worktree broker task")
    if task["primary_ref"] and not _valid_branch_ref(task["primary_ref"]):
        raise wp.WorkspaceError("malformed worktree broker task")
    if task["path"] and (
        not os.path.isabs(task["path"]) or os.path.realpath(task["path"]) != task["path"]
    ):
        raise wp.WorkspaceError("malformed worktree broker task")
    if task["base"] and not SHA_RE.fullmatch(task["base"]):
        raise wp.WorkspaceError("malformed worktree broker task")
    if task["state"] not in TASK_STATES:
        raise wp.WorkspaceError("malformed worktree broker task")
    paths = normalize_paths(task["paths"])
    contracts = infer_contracts(paths, task["contracts"])
    if task["collision"] not in {"unknown", "disjoint", "serialize", "semantic-review"}:
        raise wp.WorkspaceError("malformed worktree broker task")
    if task["action"] not in {"pending", "disjoint", "serialize"}:
        raise wp.WorkspaceError("malformed worktree broker task")
    if task["basis"] and not re.fullmatch(r"[0-9a-f]{64}", task["basis"]):
        raise wp.WorkspaceError("malformed worktree broker task")
    for key in (
        "declared_main",
        "task_head",
        "integrated_head",
        "verified_main",
        "verified_task",
    ):
        if task[key] and not SHA_RE.fullmatch(task[key]):
            raise wp.WorkspaceError("malformed worktree broker task")
    if (
        not isinstance(task["blocked_by"], list)
        or len(task["blocked_by"]) > MAX_TASKS
        or task["blocked_by"] != sorted(set(task["blocked_by"]))
        or any(
            not isinstance(value, str) or not wp.IDENTITY_RE.fullmatch(value)
            for value in task["blocked_by"]
        )
    ):
        raise wp.WorkspaceError("malformed worktree broker task")
    if task["primary_owner"] and not re.fullmatch(r"[0-9a-f]{64}", task["primary_owner"]):
        raise wp.WorkspaceError("malformed worktree broker task")
    primary_paths = normalize_paths(task["primary_paths"])
    primary_contracts = infer_contracts(primary_paths, task["primary_contracts"])
    if task["primary_basis"] and not re.fullmatch(r"[0-9a-f]{64}", task["primary_basis"]):
        raise wp.WorkspaceError("malformed worktree broker task")
    commands = [_validate_argv(value) for value in task["check_commands"]]
    if len(commands) > MAX_CHECKS:
        raise wp.WorkspaceError("malformed worktree broker task")
    results = []
    if not isinstance(task["check_results"], list) or len(task["check_results"]) > MAX_CHECKS * 2:
        raise wp.WorkspaceError("malformed worktree broker task")
    for row in task["check_results"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"argv", "stage", "exit_code"}
            or row["stage"] not in {"pre", "post"}
            or not isinstance(row["exit_code"], int)
        ):
            raise wp.WorkspaceError("malformed worktree broker task")
        results.append(
            {
                "argv": _validate_argv(row["argv"]),
                "stage": row["stage"],
                "exit_code": row["exit_code"],
            }
        )
    journal = task["journal"]
    if journal is not None:
        if (
            not isinstance(journal, dict)
            or set(journal) != {"kind", "main", "task"}
            or journal["kind"] not in JOURNAL_KINDS
            or not SHA_RE.fullmatch(journal["main"])
            or not SHA_RE.fullmatch(journal["task"])
        ):
            raise wp.WorkspaceError("malformed worktree broker task")
    archive = task["archive"]
    if archive is not None:
        if (
            not isinstance(archive, dict)
            or set(archive) != {"checkout", "metadata", "admin"}
            or not all(isinstance(archive[key], str) and os.path.isabs(archive[key]) for key in archive)
        ):
            raise wp.WorkspaceError("malformed worktree broker task")
    return {
        "run": run,
        "identity": task["identity"],
        "branch": task["branch"],
        "primary_ref": task["primary_ref"],
        "path": task["path"],
        "base": task["base"],
        "state": task["state"],
        "paths": paths,
        "contracts": contracts,
        "collision": task["collision"],
        "action": task["action"],
        "basis": task["basis"],
        "declared_main": task["declared_main"],
        "task_head": task["task_head"],
        "integrated_head": task["integrated_head"],
        "check_commands": commands,
        "check_results": results,
        "journal": journal,
        "archive": archive,
        "blocked_by": list(task["blocked_by"]),
        "primary_owner": task["primary_owner"],
        "primary_paths": primary_paths,
        "primary_contracts": primary_contracts,
        "primary_basis": task["primary_basis"],
        "verified_main": task["verified_main"],
        "verified_task": task["verified_task"],
    }


def validate_broker(data):
    if not isinstance(data, dict) or set(data) != {"schema_version", "tasks"}:
        raise wp.WorkspaceError("malformed worktree broker state")
    if data["schema_version"] != BROKER_SCHEMA or not isinstance(data["tasks"], list):
        raise wp.WorkspaceError("malformed worktree broker state")
    if len(data["tasks"]) > MAX_TASKS:
        raise wp.WorkspaceError("malformed worktree broker state")
    tasks = [_validate_task(task) for task in data["tasks"]]
    runs = [task["run"] for task in tasks]
    identities = [task["identity"] for task in tasks]
    paths = [task["path"] for task in tasks if task["path"]]
    branches = [task["branch"] for task in tasks if task["branch"]]
    if (
        len(runs) != len(set(runs))
        or len(identities) != len(set(identities))
        or len(paths) != len(set(paths))
        or len(branches) != len(set(branches))
    ):
        raise wp.WorkspaceError("malformed worktree broker state")
    return {"schema_version": BROKER_SCHEMA, "tasks": tasks}


def validate_legacy_broker(data):
    if (
        not isinstance(data, dict)
        or set(data) != {"schema_version", "tasks"}
        or data["schema_version"] != LEGACY_BROKER_SCHEMA
        or not isinstance(data["tasks"], list)
        or len(data["tasks"]) > MAX_TASKS
    ):
        raise wp.WorkspaceError("malformed legacy worktree broker state")
    tasks = [_validate_task(task, legacy=True) for task in data["tasks"]]
    return validate_broker({"schema_version": BROKER_SCHEMA, "tasks": tasks})


def _read_state_file(descriptor, name, validator):
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    handle = None
    try:
        handle = os.open(name, flags, dir_fd=descriptor)
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode):
            raise wp.WorkspaceError("unsafe worktree broker state")
        payload = os.read(handle, MAX_BROKER_BYTES + 1)
        if len(payload) > MAX_BROKER_BYTES:
            raise wp.WorkspaceError("malformed worktree broker state")
        return validator(json.loads(payload.decode("utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, wp.WorkspaceError):
            raise
        raise wp.WorkspaceError("malformed worktree broker state") from exc
    finally:
        if handle is not None:
            os.close(handle)


def _exists_regular(descriptor, name):
    try:
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise wp.WorkspaceError("cannot inspect worktree broker state") from exc
    if not stat.S_ISREG(info.st_mode):
        raise wp.WorkspaceError("unsafe worktree broker state")
    return True


def _single_backup_name(descriptor, name):
    backups = sorted(
        candidate
        for candidate in os.listdir(descriptor)
        if candidate.startswith(".kimiflow-backup-%s-" % name)
    )
    if len(backups) > 1:
        raise wp.WorkspaceError("ambiguous worktree broker recovery state")
    if backups:
        _exists_regular(descriptor, backups[0])
    return backups[0] if backups else ""


def _validate_registry_alignment(descriptor, state):
    registry = wp.read_registry_descriptor(descriptor)
    for entry in registry["entries"]:
        if not any(
            task["run"] == entry["run"]
            and task["identity"] == entry["identity"]
            and task["path"] == entry["path"]
            and task["state"] != "retired"
            for task in state["tasks"]
        ):
            raise wp.WorkspaceError(
                "canonical Fleet state does not own live Registry entry"
            )
    return state


def _read_broker_descriptor(directory_descriptor):
    descriptor = wp.registry_descriptor(directory_descriptor)
    if descriptor is None:
        return {"schema_version": BROKER_SCHEMA, "tasks": []}
    fleet = _read_state_file(descriptor, BROKER_NAME, validate_broker)
    if fleet is not None:
        return _validate_registry_alignment(descriptor, fleet)
    fleet_backup = _single_backup_name(descriptor, BROKER_NAME)
    if fleet_backup:
        recovered = _read_state_file(descriptor, fleet_backup, validate_broker)
        if recovered is None:
            raise wp.WorkspaceError("worktree broker recovery state disappeared")
        return _validate_registry_alignment(descriptor, recovered)
    if _exists_regular(descriptor, LEGACY_ARCHIVE_NAME):
        raise wp.WorkspaceError("canonical Fleet state missing after legacy migration")
    legacy = _read_state_file(descriptor, LEGACY_BROKER_NAME, validate_legacy_broker)
    if legacy is not None:
        return _validate_registry_alignment(descriptor, legacy)
    legacy_backup = _single_backup_name(descriptor, LEGACY_BROKER_NAME)
    if legacy_backup:
        recovered = _read_state_file(
            descriptor,
            legacy_backup,
            validate_legacy_broker,
        )
        if recovered is None:
            raise wp.WorkspaceError("legacy broker recovery state disappeared")
        return _validate_registry_alignment(descriptor, recovered)
    if wp.read_registry_descriptor(descriptor)["entries"]:
        raise wp.WorkspaceError("canonical Fleet state missing for live Registry")
    return {"schema_version": BROKER_SCHEMA, "tasks": []}


def read_broker(primary, directory_descriptor=None):
    if directory_descriptor is not None:
        return _read_broker_descriptor(directory_descriptor)
    with wp.registry_directory(primary, create=False) as descriptor:
        return _read_broker_descriptor(descriptor)


def _receipt_repository(task):
    if task["state"] == "integrated":
        return task["path"] if os.path.isdir(task["path"]) else ""
    if task["state"] == "retired" and task["archive"]:
        metadata = task["archive"]["metadata"]
        return os.path.dirname(os.path.dirname(metadata))
    return ""


def _validate_terminal_ref_receipt(task):
    repository = _receipt_repository(task)
    if (
        not repository
        or not os.path.isdir(repository)
        or not task["integrated_head"]
        or not task["primary_ref"]
        or _task_branch_head(repository, task) != task["integrated_head"]
        or not _ancestor(
            repository,
            task["integrated_head"],
            _ref_head(repository, task["primary_ref"]),
        )
    ):
        raise wp.WorkspaceError(
            "worktree broker terminal receipt no longer matches Git refs"
        )
    return repository


def _validate_terminal_receipts(data):
    for task in data["tasks"]:
        if task["state"] not in {"integrated", "retired"}:
            continue
        repository = _validate_terminal_ref_receipt(task)
        if task["state"] == "integrated":
            primary = wp.worktree_records(repository)[0]["path"]
            tree = wp.worktree_status(task["path"])
            collision = _current_peer_collision(
                primary,
                data,
                task,
                None,
                task["paths"],
                infer_contracts(task["paths"], task["contracts"]),
                include_primary=False,
            )
            if (
                tree["dirty"]
                or not _kimiflow_only_ignored(task["path"], tree)
                or collision["action"] != "disjoint"
                or _active_run(primary)
            ):
                raise wp.WorkspaceError(
                    "worktree broker terminal receipt no longer matches workspace"
                )


def _write_broker(
    directory_descriptor,
    data,
    publication_guard=None,
    publication_error="worktree broker publication authority changed",
):
    descriptor = wp.registry_descriptor(directory_descriptor)
    wp.recover_atomic_directory_name(descriptor, BROKER_NAME)
    previous = _read_broker_descriptor(descriptor)
    legacy_exists = _exists_regular(descriptor, LEGACY_BROKER_NAME)
    legacy_backup = _single_backup_name(descriptor, LEGACY_BROKER_NAME)
    archive_exists = _exists_regular(descriptor, LEGACY_ARCHIVE_NAME)
    if archive_exists and (legacy_exists or legacy_backup):
        raise wp.WorkspaceError("ambiguous legacy Fleet migration markers")
    validated = validate_broker(data)
    _validate_terminal_receipts(validated)
    if publication_guard is not None and not publication_guard():
        raise wp.WorkspaceError(publication_error)

    def receipt_guard():
        try:
            _validate_terminal_receipts(validated)
        except wp.WorkspaceError:
            return False
        return publication_guard is None or publication_guard()

    payload = (
        json.dumps(validated, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_BROKER_BYTES:
        raise wp.WorkspaceError("worktree broker state is too large")
    try:
        wp.atomic_directory_write(
            descriptor,
            BROKER_NAME,
            payload,
            _commit_guard=receipt_guard,
        )
        if legacy_exists or legacy_backup:
            wp.recover_atomic_directory_name(descriptor, LEGACY_BROKER_NAME)
            if not _exists_regular(descriptor, LEGACY_BROKER_NAME):
                raise wp.WorkspaceError(
                    "legacy broker recovery state disappeared before migration"
                )
            os.rename(
                LEGACY_BROKER_NAME,
                LEGACY_ARCHIVE_NAME,
                src_dir_fd=descriptor,
                dst_dir_fd=descriptor,
            )
            os.fsync(descriptor)
        try:
            _validate_terminal_receipts(validated)
            if publication_guard is not None and not publication_guard():
                raise wp.WorkspaceError(publication_error)
        except wp.WorkspaceError:
            previous_payload = (
                json.dumps(
                    previous,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            wp.atomic_directory_write(
                descriptor,
                BROKER_NAME,
                previous_payload,
            )
            raise
    except OSError as exc:
        raise wp.WorkspaceError("cannot write worktree broker state") from exc


def _new_task(run):
    return {
        "run": run,
        "identity": os.urandom(32).hex(),
        "branch": "",
        "primary_ref": "",
        "path": "",
        "base": "",
        "state": "queued",
        "paths": [],
        "contracts": [],
        "collision": "unknown",
        "action": "pending",
        "basis": "",
        "declared_main": "",
        "task_head": "",
        "integrated_head": "",
        "check_commands": [],
        "check_results": [],
        "journal": None,
        "archive": None,
        "blocked_by": [],
        "primary_owner": "",
        "primary_paths": [],
        "primary_contracts": [],
        "primary_basis": "",
        "verified_main": "",
        "verified_task": "",
    }


def _task_for(state, run):
    return next((task for task in state["tasks"] if task["run"] == run), None)


def _active_run(primary):
    handle = None
    with wp.registry_directory(primary, create=False) as directory_descriptor:
        descriptor = wp.registry_descriptor(directory_descriptor)
        if descriptor is None:
            return None
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        try:
            handle = os.open("ACTIVE_RUN.json", flags, dir_fd=descriptor)
            before = os.fstat(handle)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 65536:
                raise wp.WorkspaceError("unsafe primary active-run state")
            payload = os.read(handle, 65537)
            after = os.fstat(handle)
            named = os.stat(
                "ACTIVE_RUN.json",
                dir_fd=descriptor,
                follow_symlinks=False,
            )
            if (
                len(payload) > 65536
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise wp.WorkspaceError("unsafe primary active-run state")
            data = json.loads(payload.decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            if isinstance(exc, wp.WorkspaceError):
                raise
            raise wp.WorkspaceError("malformed primary active-run state") from exc
        finally:
            if handle is not None:
                os.close(handle)
    if not isinstance(data, dict) or not isinstance(data.get("run"), str):
        raise wp.WorkspaceError("malformed primary active-run state")
    return data


def _active_paths(primary, active):
    if not active:
        return []
    run = active.get("run", "")
    if not wp.RUN_RE.fullmatch(run):
        return []
    source = wp.safe_run_source(primary, run)
    value = wp.state_value_from_text(source, "Affected files")
    if not value:
        value = wp.state_value_from_text(source, "Affected paths")
    try:
        return normalize_paths(part.strip() for part in value.split(",") if part.strip())
    except wp.WorkspaceError:
        return []


def _digest_json(value):
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _active_owner_digest(active):
    return _digest_json(
        {
            "run": active.get("run", "") if active else "",
            "owner": active.get("owner") if active else None,
        }
    )


def _primary_envelope_snapshot(primary, active, base=""):
    status = wp.worktree_status(primary)
    if status["dirty_paths_truncated"]:
        raise wp.WorkspaceError("primary path envelope is truncated")
    paths = list(_active_paths(primary, active))
    paths.extend(status["dirty_paths"])
    head = _head(primary)
    if base and base != head:
        paths.extend(_changed_paths(primary, base, head))
    paths = normalize_paths(paths)
    contracts = infer_contracts(paths)
    owner = _active_owner_digest(active)
    basis = _digest_json(
        {
            "owner": owner,
            "paths": paths,
            "contracts": contracts,
            "head": head,
        }
    )
    return owner, paths, contracts, basis


def _branch_name(primary, slug):
    candidate = "codex/%s" % slug
    for index in range(1, 100):
        name = candidate if index == 1 else "%s-%s" % (candidate, index)
        exists = _git(primary, ["show-ref", "--verify", "--quiet", "refs/heads/%s" % name], check=False)
        if exists.returncode != 0:
            return name
    raise wp.WorkspaceError("cannot allocate a unique codex branch")


def _worktree_path(primary, branch):
    repo = os.path.basename(primary.rstrip(os.sep)) or "repo"
    leaf = branch.split("/", 1)[1]
    return os.path.realpath(os.path.join(os.path.dirname(primary), ".kimiflow-worktrees", repo, leaf))


def _lock_reason(task):
    return "kimiflow:%s:%s" % (task["identity"], task["run"])


def _matching_owned_tree(primary, task, expected_head, require_lock=True):
    for record in wp.worktree_records(primary):
        if record["path"] != task["path"]:
            continue
        return bool(
            record.get("branch") == "refs/heads/%s" % task["branch"]
            and record.get("head") == expected_head
            and (
                record.get("locked") == _lock_reason(task)
                if require_lock
                else not record.get("locked")
            )
            and not wp.codex_managed(record["path"])
        )
    return False


def _matching_tree(primary, task):
    return _matching_owned_tree(primary, task, task["base"])


def _registered_task_entry(primary, descriptor, task):
    return next(
        (
            entry
            for entry in wp.read_registry(primary, descriptor)["entries"]
            if entry["run"] == task["run"]
            and entry["identity"] == task["identity"]
            and entry["path"] == task["path"]
        ),
        None,
    )


def _task_ownership_proven(primary, descriptor, task, expected_head):
    try:
        entry = _registered_task_entry(primary, descriptor, task)
        return bool(
            entry
            and wp.owner_receipt_matches(entry["path"], entry)
            and _matching_owned_tree(
                primary,
                task,
                expected_head,
            )
        )
    except (OSError, wp.WorkspaceError):
        return False


def _foreign_worktree_present(status):
    return any(
        not tree["primary"] and not tree["kimiflow_owned"]
        for tree in status["worktrees"]
    )


def _allocation_ownership_proven(primary, task, entry):
    journal = task["journal"]
    if not (
        entry
        and entry["run"] == task["run"]
        and entry["identity"] == task["identity"]
        and entry["path"] == task["path"]
        and journal
        and journal["kind"] == "allocation"
        and journal["main"] == task["base"]
        and journal["task"] == task["base"]
    ):
        return False
    try:
        admin_dir = os.path.dirname(wp.owner_receipt_path(task["path"]))
        with wp.pinned_worktree(
            task["path"], entry, admin_dir
        ) as pinned_values:
            pinned, pinned_admin, _ = pinned_values
            if not _matching_tree(primary, task):
                return False
            current = os.lstat(task["path"])
            current_admin = os.lstat(admin_dir)
            return (
                current.st_dev,
                current.st_ino,
                current_admin.st_dev,
                current_admin.st_ino,
            ) == (
                pinned.st_dev,
                pinned.st_ino,
                pinned_admin.st_dev,
                pinned_admin.st_ino,
            )
    except (OSError, wp.WorkspaceError):
        return False


def _paths_within_declaration(actual, declared):
    return all(
        any(path == allowed or path.startswith(allowed + "/") for allowed in declared)
        for path in actual
    )


def _recover_allocation(primary, state, task, descriptor, write):
    journal = task["journal"]
    if (
        not journal
        or journal["kind"] != "allocation"
        or journal["main"] != task["base"]
        or journal["task"] != task["base"]
    ):
        raise wp.WorkspaceError(
            "allocation recovery refused: journal identity mismatch"
        )
    registry = wp.read_registry(primary, descriptor)
    owned = next(
        (
            entry
            for entry in registry["entries"]
            if entry["run"] == task["run"]
            and entry["identity"] == task["identity"]
            and entry["path"] == task["path"]
        ),
        None,
    )
    if owned:
        if not _allocation_ownership_proven(primary, task, owned):
            raise wp.WorkspaceError(
                "allocation recovery refused: ownership proof mismatch"
            )
        task["state"] = "allocated"
        task["journal"] = None
        task["task_head"] = _head(task["path"])
        if write:
            _write_broker(descriptor, state)
        return True
    if not task["path"] or not os.path.isdir(task["path"]):
        return False
    if not _matching_tree(primary, task):
        raise wp.WorkspaceError("allocation recovery refused: reserved worktree identity mismatch")
    recovery_entry = {
        "path": task["path"],
        "run": task["run"],
        "identity": task["identity"],
    }
    if _allocation_ownership_proven(primary, task, recovery_entry):
        if write:
            entries = list(registry["entries"])
            if len(entries) >= MAX_WORKTREES:
                raise wp.WorkspaceError("allocation recovery refused: Fleet capacity reached")
            wp.write_registry(
                primary,
                {"schema_version": 1, "entries": entries + [recovery_entry]},
                descriptor,
            )
        task["state"] = "allocated"
        task["journal"] = None
        task["task_head"] = _head(task["path"])
        if write:
            _write_broker(descriptor, state)
        return True
    wp.register(
        primary,
        task["path"],
        task["run"],
        write=write,
        _registry_descriptor=descriptor,
        _allow_locked=True,
        _require_active=False,
        _identity=task["identity"],
    )
    task["state"] = "allocated"
    task["journal"] = None
    task["task_head"] = _head(task["path"])
    if write:
        _write_broker(descriptor, state)
    return True


def _allocate(primary, state, task, descriptor, write):
    if task["state"] == "allocating" and _recover_allocation(primary, state, task, descriptor, write):
        return
    if _foreign_worktree_present(
        wp.build_status(primary, descriptor)
    ):
        raise wp.WorkspaceError(
            "Fleet allocation refused: foreign worktree present"
        )
    if len(wp.read_registry(primary, descriptor)["entries"]) >= MAX_WORKTREES:
        task["state"] = "queued"
        if write:
            _write_broker(descriptor, state)
        return
    if not task["branch"]:
        task["branch"] = _branch_name(primary, _slug(task["run"]))
    if not task["path"]:
        task["path"] = _worktree_path(primary, task["branch"])
    if not task["base"]:
        task["base"] = _head(primary)
    task["state"] = "allocating"
    task["journal"] = {"kind": "allocation", "main": task["base"], "task": task["base"]}
    if not write:
        return
    _write_broker(descriptor, state)
    os.makedirs(os.path.dirname(task["path"]), mode=0o700, exist_ok=True)
    branch_exists = (
        _git(primary, ["show-ref", "--verify", "--quiet", "refs/heads/%s" % task["branch"]], check=False).returncode
        == 0
    )
    if branch_exists:
        if _git_text(primary, ["rev-parse", "refs/heads/%s" % task["branch"]]) != task["base"]:
            raise wp.WorkspaceError("reserved broker branch moved before allocation")
        args = ["worktree", "add", "--lock", "--reason", _lock_reason(task), task["path"], task["branch"]]
    else:
        args = [
            "worktree",
            "add",
            "--lock",
            "--reason",
            _lock_reason(task),
            "-b",
            task["branch"],
            task["path"],
            task["base"],
        ]
    _git(primary, args)
    _recover_allocation(primary, state, task, descriptor, write=True)


def route(root, run, write=False, force_worktree=False):
    _slug(run)
    current = wp.repo_root(root)
    with wp.registry_operation(current, write) as descriptor:
        status = wp.build_status(current, descriptor)
        primary = status["primary_root"]
        state = read_broker(primary, descriptor)
        task = _task_for(state, run)
        for pending in state["tasks"]:
            if pending["state"] == "allocating":
                if not _recover_allocation(
                    primary, state, pending, descriptor, write
                ):
                    _allocate(primary, state, pending, descriptor, write)
            elif pending["journal"] and pending["journal"]["kind"] == "retirement":
                _recover_retirement(primary, state, pending, descriptor, write)
        active = _active_run(primary)
        active_run = active.get("run") if active else None
        primary_tree = next(tree for tree in status["worktrees"] if tree["primary"])
        main_free = active_run == run or (
            not primary_tree["dirty"]
            and active_run is None
        )
        if current != primary:
            registration = next(
                (
                    entry
                    for entry in wp.read_registry(primary, descriptor)["entries"]
                    if entry["run"] == run and entry["path"] == current
                ),
                None,
            )
            if not registration:
                raise wp.WorkspaceError("broker routing from an unowned linked worktree is forbidden")
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "allocated",
                "route": "current-owned",
                "root": current,
                "run": run,
            }
        if task and task["state"] in {
            "allocating",
            "allocated",
            "waiting",
            "ready-to-integrate",
            "needs-reconcile",
            "integrated",
            "verification-failed",
        }:
            return {
                "schema_version": BROKER_SCHEMA,
                "status": task["state"],
                "route": "worktree",
                "root": task["path"],
                "branch": task["branch"],
                "run": run,
                "queue_position": None,
            }
        if task and task["state"] == "retired":
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "retired",
                "route": "terminal",
                "root": None,
                "branch": task["branch"],
                "run": run,
                "queue_position": None,
            }
        queued = [item for item in state["tasks"] if item["state"] == "queued"]
        if active_run == run or (
            main_free and task is None and not queued and not force_worktree
        ):
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "direct",
                "route": "main",
                "root": primary,
                "run": run,
            }
        if task is None:
            while len(state["tasks"]) >= MAX_TASKS:
                retired = next(
                    (item for item in state["tasks"] if item["state"] == "retired"),
                    None,
                )
                if retired is None:
                    raise wp.WorkspaceError("worktree broker queue capacity reached")
                state["tasks"].remove(retired)
            task = _new_task(run)
            state["tasks"].append(task)
            if write:
                _write_broker(descriptor, state)
        queued = [item for item in state["tasks"] if item["state"] == "queued"]
        if main_free and not force_worktree:
            first_queued = queued[0] if queued else None
            if first_queued is not None and first_queued is not task:
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "queued",
                    "route": "queue",
                    "root": None,
                    "branch": task["branch"] or None,
                    "run": run,
                    "queue_position": queued.index(task) + 1,
                }
            if task["state"] == "queued" and write:
                state["tasks"].remove(task)
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "direct",
                "route": "main",
                "root": primary,
                "run": run,
            }
        first_queued = queued[0] if queued else None
        capacity = MAX_WORKTREES - len(wp.read_registry(primary, descriptor)["entries"])
        queued_index = queued.index(task) if task in queued else 0
        if capacity <= 0 or queued_index >= capacity:
            task["state"] = "queued"
            route_status = "queued"
            if write:
                _write_broker(descriptor, state)
        else:
            foreign = [
                tree
                for tree in status["worktrees"]
                if not tree["primary"] and not tree["kimiflow_owned"]
            ]
            if foreign:
                raise wp.WorkspaceError(
                    "Fleet allocation refused: foreign worktree present"
                )
            _allocate(primary, state, task, descriptor, write)
            route_status = task["state"] if write else "would-allocate"
        return {
            "schema_version": BROKER_SCHEMA,
            "status": route_status,
            "route": "worktree" if route_status not in {"queued"} else "queue",
            "root": task["path"] or None,
            "branch": task["branch"] or None,
            "run": run,
            "queue_position": (
                [item["run"] for item in state["tasks"] if item["state"] == "queued"].index(run) + 1
                if task["state"] == "queued"
                else None
            ),
        }


def _changed_paths(root, older, newer):
    if not older or older == newer:
        return []
    proc = _git(root, ["diff", "--name-only", "-z", "--no-renames", older, newer])
    return normalize_paths(
        item.decode("utf-8", "surrogateescape")
        for item in proc.stdout.split(b"\0")
        if item
    )


def _untracked_ignored_path(root, relative):
    tracked = _git(
        root,
        ["ls-files", "--error-unmatch", "--", relative],
        check=False,
    )
    if tracked.returncode == 0:
        return False
    if tracked.returncode != 1:
        raise wp.WorkspaceError("cannot inspect primary path tracking")
    ignored = _git(
        root,
        ["check-ignore", "--quiet", "--no-index", "--", relative],
        check=False,
    )
    if ignored.returncode not in (0, 1):
        raise wp.WorkspaceError("cannot inspect ignored primary path")
    return ignored.returncode == 0


def _ignored_delivery_conflicts(root, delivered_paths):
    conflicts = set()
    for relative in normalize_paths(delivered_paths):
        absolute = os.path.join(root, *relative.split("/"))
        if os.path.lexists(absolute) and _untracked_ignored_path(root, relative):
            conflicts.add(relative)
        parent = os.path.dirname(relative).replace(os.sep, "/")
        while parent:
            absolute_parent = os.path.join(root, *parent.split("/"))
            if (
                os.path.lexists(absolute_parent)
                and (os.path.islink(absolute_parent) or not os.path.isdir(absolute_parent))
                and _untracked_ignored_path(root, parent)
            ):
                conflicts.add(parent)
                break
            parent = os.path.dirname(parent).replace(os.sep, "/")
    return sorted(conflicts)


def _plan_digest(root, run):
    _slug(run)
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    directory_flags = flags | (os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0)
    base_path = os.path.join(root, ".kimiflow")
    run_name = run.split("/", 1)[1]
    descriptors = []
    try:
        base_descriptor = os.open(base_path, directory_flags)
        descriptors.append(base_descriptor)
        run_descriptor = os.open(run_name, directory_flags, dir_fd=base_descriptor)
        descriptors.append(run_descriptor)
        plan_descriptor = os.open("PLAN.md", flags, dir_fd=run_descriptor)
        descriptors.append(plan_descriptor)
        base_info = os.fstat(base_descriptor)
        run_info = os.fstat(run_descriptor)
        plan_info = os.fstat(plan_descriptor)
        if (
            not stat.S_ISDIR(base_info.st_mode)
            or not stat.S_ISDIR(run_info.st_mode)
            or not stat.S_ISREG(plan_info.st_mode)
        ):
            raise wp.WorkspaceError("unsafe broker PLAN.md")
        payload = os.read(plan_descriptor, MAX_PLAN_BYTES + 1)
        if len(payload) > MAX_PLAN_BYTES:
            raise wp.WorkspaceError("broker PLAN.md is too large")
        named_base = os.lstat(base_path)
        named_run = os.stat(run_name, dir_fd=base_descriptor, follow_symlinks=False)
        named_plan = os.stat("PLAN.md", dir_fd=run_descriptor, follow_symlinks=False)
        final_plan = os.fstat(plan_descriptor)
        if (
            (named_base.st_dev, named_base.st_ino) != (base_info.st_dev, base_info.st_ino)
            or (named_run.st_dev, named_run.st_ino) != (run_info.st_dev, run_info.st_ino)
            or (named_plan.st_dev, named_plan.st_ino) != (plan_info.st_dev, plan_info.st_ino)
            or (
                final_plan.st_dev,
                final_plan.st_ino,
                final_plan.st_size,
                final_plan.st_mtime_ns,
                final_plan.st_ctime_ns,
            )
            != (
                plan_info.st_dev,
                plan_info.st_ino,
                plan_info.st_size,
                plan_info.st_mtime_ns,
                plan_info.st_ctime_ns,
            )
        ):
            raise wp.WorkspaceError("broker PLAN.md changed while it was read")
        return hashlib.sha256(payload).hexdigest()
    except FileNotFoundError as exc:
        raise wp.WorkspaceError("broker declaration requires PLAN.md") from exc
    except OSError as exc:
        raise wp.WorkspaceError("cannot read broker PLAN.md safely") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _plan_matches(root, run, basis):
    try:
        return _plan_digest(root, run) == basis
    except wp.WorkspaceError:
        return False


def _peer_envelope(primary, state, task, descriptor, include_primary=True):
    status = wp.build_status(primary, descriptor)
    active = _active_run(primary)
    known_tree_paths = {
        other["path"]
        for other in state["tasks"]
        if other is not task
        and other["state"] != "retired"
        and other["path"]
    }
    peer_paths = list(status["dirty_paths"]) if include_primary else []
    if include_primary:
        peer_paths.extend(_changed_paths(primary, task["base"], _head(primary)))
    peer_contracts = infer_contracts(peer_paths)
    unknown_peer = False
    for tree in status["worktrees"]:
        if tree["primary"] or tree["path"] == task["path"]:
            continue
        tree_paths = list(tree["dirty_paths"])
        if tree["head"] and SHA_RE.fullmatch(tree["head"]):
            common = _git(
                primary,
                ["merge-base", _head(primary), tree["head"]],
                check=False,
            )
            if common.returncode == 0:
                base = common.stdout.decode("ascii").strip()
                tree_paths.extend(_changed_paths(primary, base, tree["head"]))
        peer_paths.extend(tree_paths)
        peer_contracts.extend(infer_contracts(tree_paths))
        if (
            tree["dirty_paths_truncated"]
            or (
                tree["ignored_paths_truncated"]
                and (
                    tree["path"] not in known_tree_paths
                    or not _kimiflow_only_ignored(tree["path"], tree)
                )
            )
            or (
                tree["active"]
                and not tree_paths
                and tree["path"] not in known_tree_paths
            )
        ):
            unknown_peer = True
    if include_primary and active and active.get("run") != task["run"]:
        active_paths = _active_paths(primary, active)
        peer_paths.extend(active_paths)
        if not active_paths:
            unknown_peer = True
    for other in state["tasks"]:
        if (
            other is task
            or other["state"] in {"integrated", "retired"}
            or other["action"] != "disjoint"
        ):
            continue
        peer_paths.extend(other["paths"])
        peer_contracts.extend(other["contracts"])
    unique_paths = sorted(set(peer_paths))
    if len(unique_paths) > MAX_PATHS:
        unknown_peer = True
        unique_paths = unique_paths[:MAX_PATHS]
    return unique_paths, sorted(set(peer_contracts)), unknown_peer


def _current_peer_collision(
    primary,
    state,
    task,
    descriptor,
    paths,
    contracts,
    include_primary=True,
):
    peer_paths, peer_contracts, unknown_peer = _peer_envelope(
        primary,
        state,
        task,
        descriptor,
        include_primary=include_primary,
    )
    receipt = classify_collision(paths, contracts, peer_paths, peer_contracts)
    if unknown_peer and receipt["status"] == "disjoint":
        return {
            "status": "semantic-review",
            "action": "serialize",
            "reasons": ["active-run-path-envelope-unknown"],
        }
    if unknown_peer:
        receipt["reasons"] = sorted(
            set(receipt["reasons"]) | {"active-run-path-envelope-unknown"}
        )
    return receipt


def _lease_tasks(state):
    return [
        task
        for task in state["tasks"]
        if task["state"] not in {"integrated", "retired"}
    ]


def _lease_collision(paths, contracts, peer_paths, peer_contracts):
    if not peer_paths and not peer_contracts:
        return None
    return classify_collision(paths, contracts, peer_paths, peer_contracts)


def _recompute_leases(primary, state, descriptor):
    """Recompute worktree lease winners in stable Fleet order."""
    tasks = _lease_tasks(state)
    for index, task in enumerate(tasks):
        if not task["paths"] or not task["basis"]:
            task["blocked_by"] = []
            task["collision"] = "unknown"
            task["action"] = "pending"
            if task["state"] not in {"queued", "allocating", "needs-reconcile"}:
                task["state"] = "waiting"
            continue
        blockers = []
        reasons = []
        verdict = "disjoint"
        primary_receipt = _lease_collision(
            task["paths"],
            task["contracts"],
            task["primary_paths"],
            task["primary_contracts"],
        )
        if (
            not task["primary_paths"]
            and task["primary_owner"] != _active_owner_digest(None)
        ):
            blockers.append(task["primary_owner"])
            reasons.append("primary-lease-envelope-unknown")
            verdict = "semantic-review"
        if primary_receipt and primary_receipt["action"] != "disjoint":
            blockers.append(task["primary_owner"])
            reasons.extend(primary_receipt["reasons"])
            verdict = primary_receipt["status"]
        for earlier in tasks[:index]:
            if not earlier["paths"] or not earlier["basis"]:
                blockers.append(earlier["identity"])
                reasons.append("earlier-lease-envelope-unknown")
                verdict = "semantic-review" if verdict == "disjoint" else verdict
                continue
            receipt = _lease_collision(
                task["paths"],
                task["contracts"],
                earlier["paths"],
                earlier["contracts"],
            )
            if receipt and receipt["action"] != "disjoint":
                blockers.append(earlier["identity"])
                reasons.extend(receipt["reasons"])
                if receipt["status"] == "serialize" or verdict == "disjoint":
                    verdict = receipt["status"]
        task["blocked_by"] = sorted(set(value for value in blockers if value))
        if task["blocked_by"]:
            task["collision"] = verdict
            task["action"] = "serialize"
            if task["state"] not in {"queued", "allocating", "needs-reconcile"}:
                task["state"] = "waiting"
        else:
            task["collision"] = "disjoint"
            task["action"] = "disjoint"
            if task["state"] == "waiting":
                task["state"] = "allocated"
    return state


def _primary_snapshot_valid(task):
    return bool(
        task["primary_owner"]
        and task["primary_basis"]
        and task["primary_basis"]
        == _digest_json(
            {
                "owner": task["primary_owner"],
                "paths": task["primary_paths"],
                "contracts": task["primary_contracts"],
                "head": task["declared_main"],
            }
        )
    )


def _refresh_delivery_collision(
    primary, state, task, descriptor, delivered_paths
):
    receipt = _current_peer_collision(
        primary,
        state,
        task,
        descriptor,
        delivered_paths,
        infer_contracts(delivered_paths, task["contracts"]),
    )
    task["collision"] = receipt["status"]
    task["action"] = receipt["action"]
    return receipt


def _peer_head_snapshot(primary, task):
    snapshot = []
    primary_path = os.path.realpath(primary)
    task_path = os.path.realpath(task["path"])
    for record in wp.worktree_records(primary):
        path = os.path.realpath(record["path"])
        if path in {primary_path, task_path}:
            continue
        head = record.get("head", "")
        branch = record.get("branch", "")
        if not SHA_RE.fullmatch(head):
            raise wp.WorkspaceError("cannot bind peer worktree identity")
        if branch and not _valid_branch_ref(branch):
            raise wp.WorkspaceError("cannot bind peer worktree branch")
        snapshot.append((path, branch, head))
    return sorted(snapshot)


def _peer_snapshot_matches(primary, task_path, snapshot):
    excluded = {os.path.realpath(primary), os.path.realpath(task_path)}
    current = {
        os.path.realpath(record["path"]): (
            record.get("branch", ""),
            record.get("head", ""),
        )
        for record in wp.worktree_records(primary)
        if os.path.realpath(record["path"]) not in excluded
    }
    expected = {path: (branch, head) for path, branch, head in snapshot}
    return current == expected


def _post_cas_delivery_reason(
    primary,
    state,
    task,
    descriptor,
    delivered_paths,
    expected_primary_indexes,
    task_before,
):
    if isinstance(expected_primary_indexes, str):
        expected_primary_indexes = (expected_primary_indexes,)
    primary_index_matches = any(
        _git(
            primary,
            ["diff", "--cached", "--quiet", expected, "--"],
            check=False,
        ).returncode
        == 0
        for expected in expected_primary_indexes
    )
    if (
        _git(primary, ["diff", "--quiet", "--"], check=False).returncode != 0
        or not primary_index_matches
    ):
        return "primary-mutated-at-ref-boundary"
    _, primary_untracked = wp.stream_nul_git_paths(
        primary,
        ["ls-files", "--others", "--exclude-standard", "-z"],
        0,
    )
    if primary_untracked:
        return "primary-mutated-at-ref-boundary"
    if _ignored_delivery_conflicts(primary, delivered_paths):
        return "ignored-path-collision-at-ref-boundary"
    if _foreign_worktree_present(
        wp.build_status(primary, descriptor)
    ):
        return "foreign-worktree-at-ref-boundary"
    entry = _registered_task_entry(primary, descriptor, task)
    tree = wp.worktree_status(task["path"])
    if (
        _head(task["path"]) != task_before
        or tree["dirty"]
        or not _kimiflow_only_ignored(task["path"], tree)
        or not _matching_owned_tree(primary, task, task_before)
        or not entry
        or not wp.owner_receipt_matches(entry["path"], entry)
        or not _plan_matches(task["path"], task["run"], task["basis"])
    ):
        return "task-mutated-at-ref-boundary"
    collision = _current_peer_collision(
        primary,
        state,
        task,
        descriptor,
        delivered_paths,
        infer_contracts(delivered_paths, task["contracts"]),
        include_primary=False,
    )
    if collision["action"] != "disjoint":
        return "peer-collision-at-ref-boundary"
    if _active_run(primary):
        return "primary-busy-at-ref-boundary"
    return ""


def _rollback_primary_ref(primary, primary_ref, main_before, task_before):
    rollback = _git(
        primary,
        ["update-ref", primary_ref, main_before, task_before],
        check=False,
    )
    if rollback.returncode != 0:
        raise wp.WorkspaceError(
            "primary ref changed after delivery CAS; recovery required"
        )


def _atomic_primary_fast_forward(
    primary,
    primary_ref,
    main_before,
    task_ref,
    task_before,
    task_path,
    peer_snapshot,
    post_cas_guard,
):
    commands = [
        "start",
        "update %s %s %s" % (primary_ref, task_before, main_before),
        "verify %s %s" % (task_ref, task_before),
    ]
    verified = {primary_ref, task_ref}
    for _, branch, head in peer_snapshot:
        if branch and branch not in verified:
            commands.append("verify %s %s" % (branch, head))
            verified.add(branch)
    commands.extend(("prepare", "commit"))
    proc = subprocess.run(
        ["git", "-C", primary, "update-ref", "--stdin"],
        input=("\n".join(commands) + "\n").encode("ascii"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return "delivery-ref-cas-changed"
    if (
        _ref_head(primary, primary_ref) != task_before
        or _task_branch_head(primary, {"branch": task_ref.removeprefix("refs/heads/")})
        != task_before
        or not _peer_snapshot_matches(primary, task_path, peer_snapshot)
    ):
        _rollback_primary_ref(primary, primary_ref, main_before, task_before)
        return "delivery-ref-cas-changed"
    boundary_reason = post_cas_guard(main_before)
    if boundary_reason:
        _rollback_primary_ref(primary, primary_ref, main_before, task_before)
        return boundary_reason
    if _primary_ref(primary) == primary_ref:
        sync = _git(
            primary,
            ["read-tree", "-m", "-u", main_before, task_before],
            check=False,
        )
        if sync.returncode != 0:
            raise wp.WorkspaceError(
                "primary ref delivered but worktree synchronization requires recovery"
            )
        boundary_reason = post_cas_guard(task_before)
        if boundary_reason:
            _rollback_primary_ref(
                primary,
                primary_ref,
                main_before,
                task_before,
            )
            rollback_sync = _git(
                primary,
                ["read-tree", "-m", "-u", task_before, main_before],
                check=False,
            )
            if rollback_sync.returncode != 0:
                raise wp.WorkspaceError(
                    "primary ref rolled back but worktree synchronization requires recovery"
                )
            return boundary_reason
    return ""


def declare(root, run, basis, paths=(), contracts=(), write=False):
    if not re.fullmatch(r"[0-9a-f]{64}", basis or ""):
        raise wp.WorkspaceError("declare requires a sha256 plan basis")
    current = wp.repo_root(root)
    with wp.registry_operation(current, write) as descriptor:
        primary = wp.worktree_records(current)[0]["path"]
        state = read_broker(primary, descriptor)
        task = _task_for(state, run)
        if not task or task["state"] in {"queued", "allocating", "retired"}:
            raise wp.WorkspaceError("run has no allocated broker worktree")
        if current != task["path"]:
            raise wp.WorkspaceError("broker declaration must run from its owned task worktree")
        declaration_head = _head(current)
        if not _task_ownership_proven(
            primary,
            descriptor,
            task,
            declaration_head,
        ):
            raise wp.WorkspaceError("broker declaration ownership unproven")
        if _plan_digest(current, run) != basis:
            raise wp.WorkspaceError("declare plan basis does not match PLAN.md")
        normalized = normalize_paths(paths)
        normalized_contracts = infer_contracts(normalized, contracts)
        established = (
            task["action"] == "disjoint"
            and task["primary_owner"]
            and task["primary_basis"]
        )
        persisted = bool(task["basis"])
        redeclared = persisted and (
            normalized != task["paths"]
            or normalized_contracts != task["contracts"]
            or basis != task["basis"]
        )
        if established and task["declared_main"] != _head(primary):
            raise wp.WorkspaceError(
                "Primary advanced; use revalidate before redeclaration"
            )
        if not established or redeclared:
            active = _active_run(primary)
            owner, primary_paths, primary_contracts, primary_basis = (
                _primary_envelope_snapshot(primary, active, task["base"])
            )
            if not _task_ownership_proven(
                primary,
                descriptor,
                task,
                declaration_head,
            ):
                raise wp.WorkspaceError(
                    "broker declaration ownership changed during operation"
                )
            task["primary_owner"] = owner
            task["primary_paths"] = primary_paths
            task["primary_contracts"] = primary_contracts
            task["primary_basis"] = primary_basis
        if redeclared:
            state["tasks"].remove(task)
            state["tasks"].append(task)
        task["paths"] = normalized
        task["contracts"] = normalized_contracts
        task["basis"] = basis
        task["declared_main"] = _head(primary)
        task["primary_ref"] = _primary_ref(primary)
        task["verified_main"] = ""
        task["verified_task"] = ""
        _recompute_leases(primary, state, descriptor)
        receipt = {
            "status": task["collision"],
            "action": task["action"],
            "reasons": (
                ["blocked-by:%s" % value for value in task["blocked_by"]]
                if task["blocked_by"]
                else []
            ),
        }
        if write:
            _write_broker(
                descriptor,
                state,
                publication_guard=lambda: _task_ownership_proven(
                    primary,
                    descriptor,
                    task,
                    declaration_head,
                ),
                publication_error=(
                    "broker declaration ownership changed during publication"
                ),
            )
        return {
            "schema_version": BROKER_SCHEMA,
            "status": receipt["status"],
            "action": receipt["action"],
            "basis": basis,
            "reasons": receipt["reasons"],
            "blocked_by": task["blocked_by"],
            "written": bool(write),
        }


def write_gate(root, run, basis):
    if not re.fullmatch(r"[0-9a-f]{64}", basis or ""):
        raise wp.WorkspaceError("write-gate requires a sha256 plan basis")
    current = wp.repo_root(root)
    primary = wp.worktree_records(current)[0]["path"]
    with wp.registry_lock(primary) as descriptor:
        state = read_broker(primary, descriptor)
        task = _task_for(state, run)
        if not task:
            active = _active_run(primary)
            if current != primary or not active or active.get("run") != run:
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "CLOSED",
                    "reason": "direct-authority-unproven",
                }
            if not _plan_matches(primary, run, basis):
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "CLOSED",
                    "reason": "stale-plan-basis",
                }
            try:
                _, active_paths, active_contracts, _ = (
                    _primary_envelope_snapshot(primary, active)
                )
            except wp.WorkspaceError:
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "CLOSED",
                    "reason": "primary-envelope-unknown",
                }
            status = wp.build_status(primary, descriptor)
            blockers = []
            for other in _lease_tasks(state):
                if other["state"] == "queued":
                    continue
                if (
                    not other["paths"]
                    or not other["basis"]
                    or not _primary_snapshot_valid(other)
                ):
                    blockers.append(other["identity"])
                    continue
                receipt = _lease_collision(
                    active_paths,
                    active_contracts,
                    other["paths"],
                    other["contracts"],
                )
                if not active_paths or (receipt and receipt["action"] != "disjoint"):
                    blockers.append(other["identity"])
            foreign = [
                tree
                for tree in status["worktrees"]
                if not tree["primary"] and not tree["kimiflow_owned"]
            ]
            if foreign:
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "CLOSED",
                    "reason": "foreign-worktree-ambiguous",
                    "blocked_by": sorted(set(blockers)),
                }
            if blockers:
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "CLOSED",
                    "reason": "lease-conflict",
                    "blocked_by": sorted(set(blockers)),
                }
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "OPEN",
                "reason": "direct-main",
                "blocked_by": [],
            }
        if current != task["path"]:
            return {"schema_version": BROKER_SCHEMA, "status": "CLOSED", "reason": "wrong-worktree"}
        if task["basis"] != basis or not _plan_matches(task["path"], run, basis):
            return {"schema_version": BROKER_SCHEMA, "status": "CLOSED", "reason": "stale-plan-basis"}
        entry = _registered_task_entry(primary, descriptor, task)
        if not entry or not wp.owner_receipt_matches(entry["path"], entry):
            return {"schema_version": BROKER_SCHEMA, "status": "CLOSED", "reason": "ownership-unproven"}
        if not _matching_owned_tree(primary, task, _head(current)):
            return {"schema_version": BROKER_SCHEMA, "status": "CLOSED", "reason": "identity-drift"}
        if not _primary_snapshot_valid(task):
            return {"schema_version": BROKER_SCHEMA, "status": "CLOSED", "reason": "primary-snapshot-invalid"}
        active = _active_run(primary)
        if active and _active_owner_digest(active) != task["primary_owner"]:
            return {"schema_version": BROKER_SCHEMA, "status": "CLOSED", "reason": "primary-owner-drift"}
        if _head(primary) != task["declared_main"]:
            return {"schema_version": BROKER_SCHEMA, "status": "CLOSED", "reason": "revalidation-required"}
        snapshot = json.loads(json.dumps(state))
        latest = _task_for(snapshot, run)
        _recompute_leases(primary, snapshot, descriptor)
        if (
            latest["state"] != "allocated"
            or latest["action"] != "disjoint"
            or latest["blocked_by"]
        ):
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "CLOSED",
                "reason": "serialize",
                "blocked_by": latest["blocked_by"],
            }
        status = wp.build_status(primary, descriptor)
        foreign = [
            tree
            for tree in status["worktrees"]
            if not tree["primary"]
            and tree["path"] != task["path"]
            and not tree["kimiflow_owned"]
        ]
        if foreign:
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "CLOSED",
                "reason": "foreign-worktree-ambiguous",
            }
        primary_tree = next(
            tree for tree in status["worktrees"] if tree["primary"]
        )
        if primary_tree["dirty_paths_truncated"]:
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "CLOSED",
                "reason": "primary-envelope-unknown",
            }
        primary_dirty = classify_collision(
            task["paths"],
            task["contracts"],
            primary_tree["dirty_paths"],
            infer_contracts(primary_tree["dirty_paths"]),
        )
        if primary_tree["dirty_paths"] and primary_dirty["action"] != "disjoint":
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "CLOSED",
                "reason": "primary-dirty-collision",
            }
        runtime_collision = _current_peer_collision(
            primary,
            snapshot,
            latest,
            descriptor,
            latest["paths"],
            latest["contracts"],
            include_primary=False,
        )
        if runtime_collision["action"] != "disjoint":
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "CLOSED",
                "reason": "peer-collision",
            }
        return {
            "schema_version": BROKER_SCHEMA,
            "status": "OPEN",
            "reason": "declared-disjoint",
            "blocked_by": [],
        }


def revalidate(root, run, basis, write=False):
    if not re.fullmatch(r"[0-9a-f]{64}", basis or ""):
        raise wp.WorkspaceError("revalidate requires a sha256 plan basis")
    current = wp.repo_root(root)
    with wp.registry_operation(current, write) as descriptor:
        primary = wp.worktree_records(current)[0]["path"]
        state = read_broker(primary, descriptor)
        task = _task_for(state, run)
        if not task or current != task["path"]:
            raise wp.WorkspaceError("revalidation requires its owned task worktree")
        if task["basis"] != basis or not _plan_matches(current, run, basis):
            raise wp.WorkspaceError("revalidation plan basis does not match declaration")
        entry = _registered_task_entry(primary, descriptor, task)
        main_now = _head(primary)
        task_now = _head(current)
        if (
            not entry
            or not wp.owner_receipt_matches(entry["path"], entry)
            or not _matching_owned_tree(primary, task, task_now)
        ):
            raise wp.WorkspaceError("revalidation ownership unproven")
        if (
            task["state"] == "needs-reconcile"
            and task["declared_main"] == main_now
            and not _ancestor(primary, main_now, task_now)
        ):
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "needs-reconcile",
                "reason": "manual-reconciliation-required",
            }
        if task["declared_main"] and task["declared_main"] != main_now:
            changed = _changed_paths(primary, task["declared_main"], main_now)
            task_delta = _changed_paths(primary, task["base"], task_now)
            collision = classify_collision(
                task["paths"],
                infer_contracts(task_delta, task["contracts"]),
                changed,
                infer_contracts(changed),
            )
            clean = wp.worktree_status(current)
            untouched = task_now == task["base"] and not clean["dirty"]
            already_reconciled = (
                _ancestor(primary, main_now, task_now)
                and not clean["dirty"]
                and _paths_within_declaration(
                    _changed_paths(primary, main_now, task_now),
                    task["paths"],
                )
            )
            if (
                collision["action"] != "disjoint"
                and not untouched
                and not already_reconciled
            ):
                task["collision"] = collision["status"]
                task["action"] = "serialize"
                task["state"] = "needs-reconcile"
                task["blocked_by"] = []
                if write:
                    _write_broker(
                        descriptor,
                        state,
                        publication_guard=lambda: _task_ownership_proven(
                            primary,
                            descriptor,
                            task,
                            task_now,
                        ),
                        publication_error=(
                            "revalidation ownership changed during publication"
                        ),
                    )
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "needs-reconcile",
                    "reason": "primary-advance-collision",
                }
            if untouched:
                if not write:
                    return {
                        "schema_version": BROKER_SCHEMA,
                        "status": "preview",
                        "action": "fast-forward-and-revalidate",
                    }
                advanced = _git(current, ["merge", "--ff-only", main_now], check=False)
                if advanced.returncode != 0:
                    task["state"] = "needs-reconcile"
                    failed_head = _head(current)
                    _write_broker(
                        descriptor,
                        state,
                        publication_guard=lambda: _task_ownership_proven(
                            primary,
                            descriptor,
                            task,
                            failed_head,
                        ),
                        publication_error=(
                            "revalidation ownership changed during publication"
                        ),
                    )
                    return {
                        "schema_version": BROKER_SCHEMA,
                        "status": "needs-reconcile",
                        "reason": "fast-forward-failed",
                    }
                task["base"] = main_now
                task["task_head"] = main_now
            elif already_reconciled:
                task["task_head"] = task_now
        publication_head = _head(current)
        if not _task_ownership_proven(
            primary,
            descriptor,
            task,
            publication_head,
        ):
            raise wp.WorkspaceError(
                "revalidation ownership changed during operation"
            )
        owner, primary_paths, primary_contracts, primary_basis = (
            _primary_envelope_snapshot(primary, _active_run(primary), main_now)
        )
        if not _task_ownership_proven(
            primary,
            descriptor,
            task,
            publication_head,
        ):
            raise wp.WorkspaceError(
                "revalidation ownership changed during operation"
            )
        task["primary_owner"] = owner
        task["primary_paths"] = primary_paths
        task["primary_contracts"] = primary_contracts
        task["primary_basis"] = primary_basis
        task["declared_main"] = main_now
        task["verified_main"] = ""
        task["verified_task"] = ""
        if task["state"] in {"needs-reconcile", "verification-failed"}:
            task["state"] = "waiting"
        _recompute_leases(primary, state, descriptor)
        if write:
            _write_broker(
                descriptor,
                state,
                publication_guard=lambda: _task_ownership_proven(
                    primary,
                    descriptor,
                    task,
                    publication_head,
                ),
                publication_error=(
                    "revalidation ownership changed during publication"
                ),
            )
        return {
            "schema_version": BROKER_SCHEMA,
            "status": task["state"],
            "action": task["action"],
            "blocked_by": task["blocked_by"],
            "written": bool(write),
        }


def _run_checks(root, commands, stage):
    results = []
    for argv in commands:
        try:
            proc = subprocess.run(
                argv,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            exit_code = proc.returncode
        except OSError:
            exit_code = 127
        results.append({"argv": list(argv), "stage": stage, "exit_code": exit_code})
        if exit_code != 0:
            return False, results
    return True, results


def _delivery_results(root, expected, expected_ref):
    commands = [
        ["git", "merge-base", "--is-ancestor", expected, "HEAD"],
        ["git", "merge-base", "--is-ancestor", expected, expected_ref],
        ["git", "symbolic-ref", "--quiet", "HEAD"],
        ["git", "diff", "--quiet", "--"],
        ["git", "diff", "--cached", "--quiet", "--"],
    ]
    results = []
    for argv in commands:
        proc = subprocess.run(
            argv,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        exit_code = proc.returncode
        if argv[1:3] == ["symbolic-ref", "--quiet"]:
            value = proc.stdout.decode("utf-8", "surrogateescape").strip()
            if value != expected_ref:
                exit_code = 1
        results.append({"argv": argv, "stage": "post", "exit_code": exit_code})
    return all(row["exit_code"] == 0 for row in results), results


def _recover_integration(primary, state, task, descriptor, write):
    journal = task["journal"]
    if not journal or journal["kind"] in {"allocation", "retirement"}:
        return None
    if not task["primary_ref"] or _primary_ref(primary) != task["primary_ref"]:
        raise wp.WorkspaceError("integration recovery refused: primary ref mismatch")
    main_now = _head(primary)
    task_now = _head(task["path"])
    if journal["kind"] == "reconcile":
        if task_now == journal["task"]:
            task["state"] = "ready-to-integrate"
            task["journal"] = None
        elif _ancestor(primary, journal["main"], task_now) and _ancestor(primary, journal["task"], task_now):
            task["task_head"] = task_now
            task["state"] = "ready-to-integrate"
            task["journal"] = None
        else:
            raise wp.WorkspaceError("reconciliation recovery refused: task ref mismatch")
        if write:
            _write_broker(descriptor, state)
        return None
    if (
        task["verified_main"] != journal["main"]
        or task["verified_task"] != journal["task"]
        or not task["check_results"]
        or any(row["exit_code"] != 0 for row in task["check_results"])
    ):
        raise wp.WorkspaceError(
            "integration recovery refused: verification receipt mismatch"
        )
    if task_now != journal["task"] or not _matching_owned_tree(
        primary, task, journal["task"]
    ):
        raise wp.WorkspaceError("fast-forward recovery refused: task ref mismatch")
    if main_now == journal["main"]:
        task["state"] = "ready-to-integrate"
        task["journal"] = None
        if write:
            _write_broker(descriptor, state)
        return None
    if main_now == journal["task"] or _ancestor(primary, journal["task"], main_now):
        if main_now == journal["task"]:
            delivered_paths = _changed_paths(
                primary,
                journal["main"],
                journal["task"],
            )
            boundary_reason = _post_cas_delivery_reason(
                primary,
                state,
                task,
                descriptor,
                delivered_paths,
                (journal["main"], journal["task"]),
                journal["task"],
            )
            if boundary_reason:
                _rollback_primary_ref(
                    primary,
                    task["primary_ref"],
                    journal["main"],
                    journal["task"],
                )
                if (
                    _primary_ref(primary) == task["primary_ref"]
                    and _git(
                        primary,
                        ["diff", "--cached", "--quiet", "--"],
                        check=False,
                    ).returncode
                    != 0
                ):
                    sync = _git(
                        primary,
                        [
                            "read-tree",
                            "-m",
                            "-u",
                            journal["task"],
                            journal["main"],
                        ],
                        check=False,
                    )
                    if sync.returncode != 0:
                        raise wp.WorkspaceError(
                            "fast-forward recovery rollback left a dirty primary"
                        )
                task["state"] = "ready-to-integrate"
                task["journal"] = None
                if write:
                    _write_broker(descriptor, state)
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "ready-to-integrate",
                    "reason": boundary_reason,
                    "recovered": True,
                }
        if (
            main_now == journal["task"]
            and _git(primary, ["diff", "--cached", "--quiet", "--"], check=False).returncode
            != 0
        ):
            sync = _git(
                primary,
                ["read-tree", "-m", "-u", journal["main"], journal["task"]],
                check=False,
            )
            if sync.returncode != 0:
                raise wp.WorkspaceError(
                    "fast-forward recovery refused: primary worktree changed"
                )
        ok, results = _delivery_results(
            primary,
            journal["task"],
            task["primary_ref"],
        )
        task["check_results"] = [
            row for row in task["check_results"] if row["stage"] != "post"
        ] + results
        task["integrated_head"] = journal["task"]
        task["task_head"] = journal["task"]
        task["journal"] = None
        task["state"] = "integrated" if ok else "verification-failed"
        if write:
            _write_broker(descriptor, state)
        return {
            "schema_version": BROKER_SCHEMA,
            "status": task["state"],
            "recovered": True,
            "integrated_head": task["integrated_head"],
            "checks": results,
        }
    raise wp.WorkspaceError("fast-forward recovery refused: primary ref mismatch")


def integrate(root, run, checks=(), write=False):
    commands = parse_check_arguments(checks)
    current = wp.repo_root(root)
    with wp.registry_operation(current, write) as descriptor:
        status = wp.build_status(current, descriptor)
        primary = status["primary_root"]
        if current != primary:
            raise wp.WorkspaceError("integration must run from the primary worktree")
        state = read_broker(primary, descriptor)
        task = _task_for(state, run)
        if not task or task["state"] in {"queued", "allocating", "retired"}:
            raise wp.WorkspaceError("run is not ready for integration")
        if task["state"] == "integrated":
            _validate_terminal_ref_receipt(task)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "integrated",
                "integrated_head": task["integrated_head"],
                "checks": [
                    row
                    for row in task["check_results"]
                    if row["stage"] == "post"
                ],
                "idempotent": True,
            }
        if task["state"] == "needs-reconcile":
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "needs-reconcile",
                "reason": "revalidation-required",
            }
        recovered = _recover_integration(primary, state, task, descriptor, write)
        if recovered is not None:
            return recovered
        active = _active_run(primary)
        if active:
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {"schema_version": BROKER_SCHEMA, "status": "ready-to-integrate", "reason": "primary-busy"}
        if task["action"] != "disjoint" or not task["basis"]:
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "declaration-does-not-permit-integration",
            }
        if not _plan_matches(task["path"], run, task["basis"]):
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "stale-plan-basis",
            }
        if not task["primary_ref"] or _primary_ref(primary) != task["primary_ref"]:
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "primary-ref-changed",
            }
        if not _primary_snapshot_valid(task):
            task["state"] = "needs-reconcile"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "needs-reconcile",
                "reason": "primary-snapshot-invalid",
            }
        if _foreign_worktree_present(status):
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "foreign-worktree-ambiguous",
            }
        primary_tree = next(tree for tree in status["worktrees"] if tree["primary"])
        if primary_tree["dirty"]:
            return {"schema_version": BROKER_SCHEMA, "status": "ready-to-integrate", "reason": "primary-dirty"}
        entry = _registered_task_entry(primary, descriptor, task)
        if not entry or not wp.owner_receipt_matches(entry["path"], entry):
            raise wp.WorkspaceError("integration ownership unproven")
        tree = wp.find_tree(status, entry["path"])
        if (
            tree["dirty"]
            or not _kimiflow_only_ignored(entry["path"], tree)
            or not tree["kimiflow_owned"]
            or not _matching_owned_tree(primary, task, tree["head"])
        ):
            raise wp.WorkspaceError("task worktree is not clean and owned")
        if commands:
            task["check_commands"] = commands
        if not task["check_commands"]:
            raise wp.WorkspaceError("integration requires at least one no-shell check")
        main_before = _head(primary)
        task_before = _head(entry["path"])
        if task["declared_main"] != main_before:
            task["state"] = "needs-reconcile"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "needs-reconcile",
                "reason": "revalidation-required",
            }
        preflight = _git(
            primary,
            ["merge-tree", "--write-tree", "--quiet", main_before, task_before],
            check=False,
        )
        if preflight.returncode != 0:
            task["state"] = "needs-reconcile"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "needs-reconcile",
                "reason": "merge-conflict" if preflight.returncode == 1 else "merge-preflight-error",
            }
        if not _ancestor(primary, main_before, task_before):
            task["state"] = "reconciling"
            task["journal"] = {"kind": "reconcile", "main": main_before, "task": task_before}
            if not write:
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "preview",
                    "action": "reconcile-and-integrate",
                }
            _write_broker(descriptor, state)
            merged = _git(entry["path"], ["merge", "--no-edit", main_before], check=False)
            if merged.returncode != 0:
                _git(entry["path"], ["merge", "--abort"], check=False)
                task["state"] = "needs-reconcile"
                task["journal"] = None
                _write_broker(descriptor, state)
                return {
                    "schema_version": BROKER_SCHEMA,
                    "status": "needs-reconcile",
                    "reason": "reconciliation-failed",
                }
            task_before = _head(entry["path"])
            task["task_head"] = task_before
            task["state"] = "ready-to-integrate"
            task["journal"] = None
            _write_broker(descriptor, state)
        if not _matching_owned_tree(primary, task, task_before):
            raise wp.WorkspaceError("task worktree identity changed before verification")
        delivered_paths = _changed_paths(primary, main_before, task_before)
        if not _paths_within_declaration(delivered_paths, task["paths"]):
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "undeclared-task-delta",
                "paths": delivered_paths,
            }
        ignored_conflicts = _ignored_delivery_conflicts(primary, delivered_paths)
        if ignored_conflicts:
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "ignored-path-collision",
                "paths": ignored_conflicts,
            }
        current_collision = _refresh_delivery_collision(
            primary,
            state,
            task,
            descriptor,
            delivered_paths,
        )
        if current_collision["action"] != "disjoint":
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "peer-collision",
                "collision": current_collision["status"],
            }
        ok, pre_results = _run_checks(entry["path"], task["check_commands"], "pre")
        task["check_results"] = pre_results
        task["verified_main"] = ""
        task["verified_task"] = ""
        if not ok:
            task["state"] = "verification-failed"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "verification-failed",
                "stage": "pre",
                "checks": pre_results,
            }
        checked_tree = wp.worktree_status(entry["path"])
        checked_entry = _registered_task_entry(primary, descriptor, task)
        if (
            _head(entry["path"]) != task_before
            or checked_tree["dirty"]
            or not _kimiflow_only_ignored(entry["path"], checked_tree)
            or not _matching_owned_tree(primary, task, task_before)
            or not checked_entry
            or not wp.owner_receipt_matches(checked_entry["path"], checked_entry)
            or not _plan_matches(entry["path"], run, task["basis"])
        ):
            task["state"] = "verification-failed"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "verification-failed",
                "stage": "pre",
                "reason": "task-mutated-during-check",
                "checks": pre_results,
            }
        if _active_run(primary):
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {"schema_version": BROKER_SCHEMA, "status": "ready-to-integrate", "reason": "primary-busy"}
        if _head(primary) != main_before:
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {"schema_version": BROKER_SCHEMA, "status": "ready-to-integrate", "reason": "primary-advanced"}
        latest = wp.worktree_status(primary)
        if latest["dirty"]:
            return {"schema_version": BROKER_SCHEMA, "status": "ready-to-integrate", "reason": "primary-dirty"}
        if _ignored_delivery_conflicts(primary, delivered_paths):
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "ignored-path-collision-after-check",
            }
        current_collision = _refresh_delivery_collision(
            primary,
            state,
            task,
            descriptor,
            delivered_paths,
        )
        if current_collision["action"] != "disjoint":
            task["state"] = "ready-to-integrate"
            if write:
                _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": "post-check-peer-collision",
                "collision": current_collision["status"],
            }
        task["verified_main"] = main_before
        task["verified_task"] = task_before
        task["state"] = "integrating"
        task["journal"] = {"kind": "fast-forward", "main": main_before, "task": task_before}
        if not write:
            return {"schema_version": BROKER_SCHEMA, "status": "preview", "action": "fast-forward"}
        _write_broker(descriptor, state)
        boundary_collision = _refresh_delivery_collision(
            primary,
            state,
            task,
            descriptor,
            delivered_paths,
        )
        peer_snapshot = _peer_head_snapshot(primary, task)
        boundary_entry = _registered_task_entry(primary, descriptor, task)
        boundary_tree = wp.worktree_status(entry["path"])
        boundary_workspace = wp.build_status(primary, descriptor)
        boundary_reason = ""
        boundary_status = "ready-to-integrate"
        if (
            _primary_ref(primary) != task["primary_ref"]
            or _head(primary) != main_before
        ):
            boundary_reason = "primary-changed-at-delivery-boundary"
        elif (
            _head(entry["path"]) != task_before
            or boundary_tree["dirty"]
            or not _kimiflow_only_ignored(entry["path"], boundary_tree)
            or not _matching_owned_tree(primary, task, task_before)
            or not boundary_entry
            or not wp.owner_receipt_matches(boundary_entry["path"], boundary_entry)
            or not _plan_matches(entry["path"], run, task["basis"])
        ):
            boundary_reason = "task-mutated-at-delivery-boundary"
            boundary_status = "verification-failed"
        elif _active_run(primary):
            boundary_reason = "primary-busy-at-delivery-boundary"
        elif _foreign_worktree_present(boundary_workspace):
            boundary_reason = "foreign-worktree-at-delivery-boundary"
        elif boundary_collision["action"] != "disjoint":
            boundary_reason = "peer-collision-at-delivery-boundary"
        else:
            boundary_primary = wp.worktree_status(primary)
            if boundary_primary["dirty"]:
                boundary_reason = "primary-dirty-at-delivery-boundary"
            elif _ignored_delivery_conflicts(primary, delivered_paths):
                boundary_reason = "ignored-path-collision-at-delivery-boundary"
        if boundary_reason:
            task["state"] = boundary_status
            task["journal"] = None
            _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": boundary_status,
                "reason": boundary_reason,
                "collision": boundary_collision["status"],
            }
        task_ref = "refs/heads/%s" % task["branch"]
        delivery_reason = _atomic_primary_fast_forward(
            primary,
            task["primary_ref"],
            main_before,
            task_ref,
            task_before,
            entry["path"],
            peer_snapshot,
            lambda expected_primary_index: _post_cas_delivery_reason(
                primary,
                state,
                task,
                descriptor,
                delivered_paths,
                expected_primary_index,
                task_before,
            ),
        )
        if delivery_reason:
            task["state"] = "ready-to-integrate"
            task["journal"] = None
            _write_broker(descriptor, state)
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "ready-to-integrate",
                "reason": delivery_reason,
            }
        ok, post_results = _delivery_results(
            primary,
            task_before,
            task["primary_ref"],
        )
        task["check_results"] = pre_results + post_results
        task["integrated_head"] = task_before
        task["task_head"] = task_before
        task["journal"] = None
        task["state"] = "integrated" if ok else "verification-failed"
        _write_broker(descriptor, state)
        return {
            "schema_version": BROKER_SCHEMA,
            "status": task["state"],
            "integrated_head": task_before,
            "checks": post_results,
        }


def _safe_archive_directory(path):
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)


def _retirement_entry_matches(entry, task):
    return bool(
        entry
        and entry["run"] == task["run"]
        and entry["identity"] == task["identity"]
        and entry["path"] == task["path"]
    )


def _task_branch_head(primary, task):
    proc = _git(
        primary,
        ["rev-parse", "--verify", "refs/heads/%s" % task["branch"]],
        check=False,
    )
    value = proc.stdout.decode("ascii", "ignore").strip()
    return value if proc.returncode == 0 and SHA_RE.fullmatch(value) else ""


def _retirement_checkout_matches(primary, task, require_lock=True):
    if (
        not task["integrated_head"]
        or _task_branch_head(primary, task) != task["integrated_head"]
        or not _matching_owned_tree(
            primary,
            task,
            task["integrated_head"],
            require_lock=require_lock,
        )
    ):
        return False
    tree = wp.worktree_status(task["path"])
    return not tree["dirty"] and _kimiflow_only_ignored(task["path"], tree)


def _archived_checkout_status(path, admin_dir, common_dir=None):
    environment = os.environ.copy()
    environment["GIT_DIR"] = admin_dir
    environment["GIT_WORK_TREE"] = path
    if common_dir:
        environment["GIT_COMMON_DIR"] = common_dir

    def run(args):
        return subprocess.run(
            ["git"] + list(args),
            cwd=path,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def paths(args, limit):
        proc = run(args)
        if proc.returncode != 0:
            raise wp.WorkspaceError("cannot inspect archived worktree paths")
        values = [
            raw.decode("utf-8", "surrogateescape")
            for raw in proc.stdout.split(b"\0")
            if raw
        ]
        return values[:limit], len(values)

    proc = run(
        ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=no"]
    )
    if proc.returncode != 0:
        raise wp.WorkspaceError("cannot inspect archived worktree status")
    result = wp.parse_status_v2(proc.stdout)
    remaining = max(
        0,
        wp.UNTRACKED_PATH_SAMPLE_LIMIT - len(result["dirty_paths"]),
    )
    untracked_paths, untracked_count = paths(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        remaining,
    )
    result["dirty_paths"].extend(untracked_paths)
    result["untracked"] = untracked_count
    result["dirty"] = result["dirty"] or untracked_count > 0
    result["dirty_path_count"] = result.pop("tracked_path_count") + untracked_count
    result["dirty_paths_truncated"] = (
        result["dirty_path_count"] > len(result["dirty_paths"])
    )
    ignored = run(
        ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"]
    )
    if ignored.returncode != 0:
        raise wp.WorkspaceError("cannot inspect archived worktree paths")
    ignored_values = [
        raw.decode("utf-8", "surrogateescape")
        for raw in ignored.stdout.split(b"\0")
        if raw
    ]
    result["ignored_paths"] = ignored_values[: wp.IGNORED_PATH_SAMPLE_LIMIT]
    result["ignored_count"] = len(ignored_values)
    result["ignored_paths_truncated"] = (
        len(ignored_values) > len(result["ignored_paths"])
    )
    result["ignored_only_kimiflow"] = all(
        path == ".kimiflow" or path.startswith(".kimiflow/")
        for path in ignored_values
    )
    return result


def _recover_retirement(primary, state, task, descriptor, write):
    journal = task["journal"]
    if not journal or journal["kind"] != "retirement":
        return False
    registry = wp.read_registry(primary, descriptor)
    entry = next(
        (
            item
            for item in registry["entries"]
            if item["run"] == task["run"] and item["identity"] == task["identity"]
        ),
        None,
    )
    archive = task["archive"]
    if not archive:
        raise wp.WorkspaceError("retirement recovery refused: archive receipt missing")
    common_dir = wp.git_path(primary, ["rev-parse", "--git-common-dir"])
    expected_checkout = wp.retirement_paths(task["path"], task["identity"])[1]
    expected_metadata = wp.metadata_retirement_paths(common_dir, task["identity"])[1]
    worktrees_dir = os.path.realpath(os.path.join(common_dir, "worktrees"))
    admin_dir = os.path.realpath(archive["admin"])
    if (
        os.path.realpath(archive["checkout"]) != os.path.realpath(expected_checkout)
        or os.path.realpath(archive["metadata"]) != os.path.realpath(expected_metadata)
        or admin_dir == worktrees_dir
        or not wp.is_within(admin_dir, worktrees_dir)
    ):
        raise wp.WorkspaceError("retirement recovery refused: archive path mismatch")
    checkout_archived = _safe_archive_directory(archive["checkout"])
    metadata_archived = _safe_archive_directory(archive["metadata"])
    if os.path.isdir(task["path"]):
        if not _retirement_entry_matches(entry, task):
            raise wp.WorkspaceError("retirement recovery refused: ownership mismatch")
        if not _retirement_checkout_matches(primary, task):
            raise wp.WorkspaceError("retirement recovery refused: task ref mismatch")
        if checkout_archived or metadata_archived:
            raise wp.WorkspaceError("retirement recovery refused: ambiguous archive state")
        task["journal"] = None
        task["archive"] = None
        if write:
            _write_broker(descriptor, state)
        return False
    if checkout_archived:
        archived_status = _archived_checkout_status(
            archive["checkout"],
            archive["metadata"] if metadata_archived else archive["admin"],
            common_dir if metadata_archived else None,
        )
        if archived_status["dirty"] or not _kimiflow_only_ignored(
            archive["checkout"], archived_status
        ):
            if metadata_archived:
                raise wp.WorkspaceError(
                    "retirement recovery refused: archived content changed"
                )
            archived_info = os.stat(archive["checkout"], follow_symlinks=False)
            wp.restore_archived_worktree(
                task["path"],
                os.path.dirname(archive["checkout"]),
                archive["checkout"],
                (archived_info.st_dev, archived_info.st_ino),
            )
            _git(
                primary,
                ["worktree", "lock", "--reason", _lock_reason(task), task["path"]],
                check=False,
            )
            task["state"] = "verification-failed"
            task["journal"] = None
            task["archive"] = None
            if write:
                _write_broker(descriptor, state)
            raise wp.WorkspaceError(
                "retirement recovery restored checkout with late content"
            )
    if (
        not checkout_archived
        or not task["integrated_head"]
        or _task_branch_head(primary, task) != task["integrated_head"]
        or not task["primary_ref"]
        or not _ancestor(
            primary,
            task["integrated_head"],
            _ref_head(primary, task["primary_ref"]),
        )
    ):
        raise wp.WorkspaceError("retirement recovery refused: archive receipt mismatch")
    if metadata_archived:
        if entry and not _retirement_entry_matches(entry, task):
            raise wp.WorkspaceError("retirement recovery refused: registry mismatch")
        if entry:
            if not write:
                return False
            wp.write_registry(
                primary,
                {
                    "schema_version": 1,
                    "entries": [
                        item
                        for item in registry["entries"]
                        if not _retirement_entry_matches(item, task)
                    ],
                },
                descriptor,
            )
    else:
        if not _retirement_entry_matches(entry, task):
            raise wp.WorkspaceError("retirement recovery refused: metadata receipt missing")
        receipt_path = os.path.join(admin_dir, wp.OWNER_RECEIPT_NAME)
        if not wp.receipt_file_matches(receipt_path, entry):
            raise wp.WorkspaceError("retirement recovery refused: ownership receipt mismatch")
        if not write:
            return False
        with wp.safe_directory(admin_dir) as admin_descriptor:
            wp.detach_admin_record(
                admin_dir,
                admin_descriptor,
                common_dir,
                task["identity"],
            )
        wp.write_registry(
            primary,
            {
                "schema_version": 1,
                "entries": [
                    item
                    for item in registry["entries"]
                    if not _retirement_entry_matches(item, task)
                ],
            },
            descriptor,
        )
    task["state"] = "retired"
    task["journal"] = None
    if write:
        _write_broker(descriptor, state)
    return True


def retire(root, run, write=False):
    current = wp.repo_root(root)
    primary = wp.worktree_records(current)[0]["path"]
    with wp.registry_operation(primary, write) as descriptor:
        state = read_broker(primary, descriptor)
        task = _task_for(state, run)
        if not task:
            raise wp.WorkspaceError("retirement requires a complete integrated receipt")
        if _recover_retirement(primary, state, task, descriptor, write):
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "retired",
                "recovered": True,
                "integrated_head": task["integrated_head"],
                "archive_path": task["archive"]["checkout"],
                "metadata_archive_path": task["archive"]["metadata"],
            }
        if task["state"] != "integrated" or task["journal"] is not None:
            raise wp.WorkspaceError("retirement requires a complete integrated receipt")
        if (
            not task["integrated_head"]
            or not task["primary_ref"]
            or not _ancestor(
                primary,
                task["integrated_head"],
                _ref_head(primary, task["primary_ref"]),
            )
        ):
            raise wp.WorkspaceError("retirement refused: task head is not contained in primary")
        if not task["check_results"] or any(row["exit_code"] != 0 for row in task["check_results"]):
            raise wp.WorkspaceError("retirement refused: green checks missing")
        if wp.run_status(primary, run, task["path"]) not in wp.TERMINAL_RUN_STATUS:
            raise wp.WorkspaceError("retirement requires a terminal run")
        registry = wp.read_registry(primary, descriptor)
        entry = next((item for item in registry["entries"] if item["run"] == run), None)
        if not entry or entry["path"] != task["path"] or entry["identity"] != task["identity"]:
            raise wp.WorkspaceError("retirement ownership unproven")
        if not wp.owner_receipt_matches(task["path"], entry):
            raise wp.WorkspaceError("retirement ownership receipt mismatch")
        if not _retirement_checkout_matches(primary, task):
            raise wp.WorkspaceError("retirement refused: task ref mismatch")
        if not write:
            return {
                "schema_version": BROKER_SCHEMA,
                "status": "preview",
                "path": task["path"],
                "integrated_head": task["integrated_head"],
            }
        common_dir = wp.git_path(primary, ["rev-parse", "--git-common-dir"])
        admin_path = os.path.dirname(wp.owner_receipt_path(task["path"]))
        archive_path = wp.retirement_paths(task["path"], task["identity"])[1]
        metadata_archive_path = wp.metadata_retirement_paths(
            common_dir, task["identity"]
        )[1]
        task["archive"] = {
            "checkout": archive_path,
            "metadata": metadata_archive_path,
            "admin": admin_path,
        }
        task["journal"] = {
            "kind": "retirement",
            "main": _ref_head(primary, task["primary_ref"]),
            "task": task["integrated_head"],
        }
        _write_broker(descriptor, state)
    unlock = _git(primary, ["worktree", "unlock", task["path"]], check=False)
    if unlock.returncode != 0:
        record = next(
            (
                item
                for item in wp.worktree_records(primary)
                if item["path"] == task["path"]
            ),
            None,
        )
        if not record or record.get("locked"):
            raise wp.WorkspaceError("cannot unlock owned broker worktree")
    try:
        def revalidate_archive_boundary(candidate_path, candidate_admin=None):
            primary_head = _ref_head(primary, task["primary_ref"])
            if (
                not _ancestor(primary, task["integrated_head"], primary_head)
                or _task_branch_head(primary, task) != task["integrated_head"]
            ):
                raise wp.WorkspaceError(
                    "retirement refused: delivery refs changed before archive"
                )
            if os.path.realpath(candidate_path) == os.path.realpath(task["path"]):
                matches = _retirement_checkout_matches(
                    primary,
                    task,
                    require_lock=False,
                )
            elif os.path.realpath(candidate_path) == os.path.realpath(
                task["archive"]["checkout"]
            ):
                archived = _archived_checkout_status(
                    candidate_path,
                    candidate_admin or task["archive"]["admin"],
                    (
                        wp.git_path(primary, ["rev-parse", "--git-common-dir"])
                        if candidate_admin
                        and os.path.realpath(candidate_admin)
                        == os.path.realpath(task["archive"]["metadata"])
                        else None
                    ),
                )
                matches = not archived["dirty"] and _kimiflow_only_ignored(
                    candidate_path, archived
                )
            else:
                matches = False
            if not matches:
                raise wp.WorkspaceError(
                    "retirement refused: task content changed before archive"
                )

        result = wp.remove(
            primary,
            task["path"],
            write=True,
            _allow_kimiflow_ignored=True,
            _archive_guard=revalidate_archive_boundary,
        )
        if (
            _task_branch_head(primary, task) != task["integrated_head"]
            or not _ancestor(
                primary,
                task["integrated_head"],
                _ref_head(primary, task["primary_ref"]),
            )
        ):
            raise wp.WorkspaceError(
                "retirement refused: delivery refs changed during archive"
            )
    except Exception as retirement_error:
        _git(
            primary,
            ["worktree", "lock", "--reason", _lock_reason(task), task["path"]],
            check=False,
        )
        try:
            with wp.registry_operation(primary, True) as descriptor:
                state = read_broker(primary, descriptor)
                latest = _task_for(state, run)
                if (
                    latest
                    and latest["identity"] == task["identity"]
                    and _recover_retirement(primary, state, latest, descriptor, True)
                ):
                    return {
                        "schema_version": BROKER_SCHEMA,
                        "status": "retired",
                        "recovered": True,
                        "integrated_head": latest["integrated_head"],
                        "archive_path": latest["archive"]["checkout"],
                        "metadata_archive_path": latest["archive"]["metadata"],
                    }
        except wp.WorkspaceError:
            pass
        raise retirement_error
    with wp.registry_operation(primary, True) as descriptor:
        state = read_broker(primary, descriptor)
        latest = _task_for(state, run)
        if latest and latest["identity"] == task["identity"]:
            latest["state"] = "retired"
            latest["journal"] = None
            latest["archive"] = {
                "checkout": result["archive_path"],
                "metadata": result["metadata_archive_path"],
                "admin": task["archive"]["admin"],
            }
            _write_broker(descriptor, state)
        return {
            "schema_version": BROKER_SCHEMA,
            "status": "retired",
            "integrated_head": task["integrated_head"],
            "archive_path": result["archive_path"],
            "metadata_archive_path": result["metadata_archive_path"],
        }


def broker_status(root=None):
    current = wp.repo_root(root)
    primary = wp.worktree_records(current)[0]["path"]
    state = read_broker(primary)
    return {
        "schema_version": BROKER_SCHEMA,
        "status": "current",
        "primary_root": primary,
        "tasks": state["tasks"],
    }


def add_parsers(sub):
    route_parser = sub.add_parser("route")
    route_parser.add_argument("--root")
    route_parser.add_argument("--run", required=True)
    route_parser.add_argument("--write", action="store_true")
    route_parser.add_argument("--force-worktree", action="store_true")
    route_parser.add_argument("--pretty", action="store_true")

    declare_parser = sub.add_parser("declare")
    declare_parser.add_argument("--root")
    declare_parser.add_argument("--run", required=True)
    declare_parser.add_argument("--basis", required=True)
    declare_parser.add_argument("--path", action="append", default=[])
    declare_parser.add_argument("--contract", action="append", default=[])
    declare_parser.add_argument("--write", action="store_true")
    declare_parser.add_argument("--pretty", action="store_true")

    revalidate_parser = sub.add_parser("revalidate")
    revalidate_parser.add_argument("--root")
    revalidate_parser.add_argument("--run", required=True)
    revalidate_parser.add_argument("--basis", required=True)
    revalidate_parser.add_argument("--write", action="store_true")
    revalidate_parser.add_argument("--pretty", action="store_true")

    gate_parser = sub.add_parser("write-gate")
    gate_parser.add_argument("--root")
    gate_parser.add_argument("--run", required=True)
    gate_parser.add_argument("--basis", required=True)
    gate_parser.add_argument("--pretty", action="store_true")

    integration_parser = sub.add_parser("integrate")
    integration_parser.add_argument("--root")
    integration_parser.add_argument("--run", required=True)
    integration_parser.add_argument("--check-json", action="append", default=[])
    integration_parser.add_argument("--write", action="store_true")
    integration_parser.add_argument("--pretty", action="store_true")

    retirement_parser = sub.add_parser("retire")
    retirement_parser.add_argument("--root")
    retirement_parser.add_argument("--run", required=True)
    retirement_parser.add_argument("--write", action="store_true")
    retirement_parser.add_argument("--pretty", action="store_true")

    status_parser = sub.add_parser("broker-status")
    status_parser.add_argument("--root")
    status_parser.add_argument("--pretty", action="store_true")


def dispatch(command, args):
    if command == "route":
        return route(
            args.root, args.run, args.write,
            force_worktree=args.force_worktree,
        )
    if command == "declare":
        return declare(args.root, args.run, args.basis, args.path, args.contract, args.write)
    if command == "revalidate":
        return revalidate(args.root, args.run, args.basis, args.write)
    if command == "write-gate":
        return write_gate(args.root, args.run, args.basis)
    if command == "integrate":
        return integrate(args.root, args.run, args.check_json, args.write)
    if command == "retire":
        return retire(args.root, args.run, args.write)
    return broker_status(args.root)
