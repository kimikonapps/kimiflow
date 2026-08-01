"""Private FirstMate-style project registry and Pi Fleet allocation.

The registry is navigation metadata only. Kimiflow's per-project Active Run,
worktree broker, gates, and receipts remain authoritative.
"""

from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile

from . import model_adapter
from . import worktree_broker
from . import workspace_preflight as wp


REGISTRY_SCHEMA = 1
REGISTRY_NAME = "kimiflow-projects-v1.json"
MAX_REGISTRY_BYTES = 256 * 1024
MAX_PROJECTS = 128
PROJECT_ID_RE = re.compile(r"^project-[0-9a-f]{16}$")
PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
WORKER_RE = re.compile(r"^worker-[A-Za-z0-9]{8,64}$")
EXPLICIT_RUN_RE = re.compile(r"(?<![A-Za-z0-9._/-])(?P<run>\.kimiflow/[A-Za-z0-9][A-Za-z0-9._-]*)")
NUMBERED_RUN_RE = re.compile(r"\b(?:run|lauf)\s+(?P<number>[0-9]+(?:\.[0-9]+)?)\b", re.IGNORECASE)


class ProjectError(ValueError):
    def __init__(self, status, message, code=1):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def _config_root(environ=None):
    env = os.environ if environ is None else environ
    configured = env.get("PI_CODING_AGENT_DIR")
    root = configured if isinstance(configured, str) and os.path.isabs(configured) else os.path.join(
        os.path.expanduser("~"), ".pi", "agent",
    )
    return os.path.realpath(root)


def _registry_path(environ=None):
    return os.path.join(_config_root(environ), REGISTRY_NAME)


def _safe_parent(path):
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    info = os.lstat(parent)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ProjectError("project_registry_unsafe", "Kimiflow project registry directory is unsafe", 2)
    os.chmod(parent, 0o700)
    return parent


@contextlib.contextmanager
def _locked_registry(environ=None):
    path = _registry_path(environ)
    parent = _safe_parent(path)
    lock_path = path + ".lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("registry lock is not regular")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield path, parent
    except OSError as exc:
        raise ProjectError("project_registry_unsafe", "Cannot lock Kimiflow project registry: %s" % exc, 2)
    finally:
        if "descriptor" in locals():
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _validate(value):
    if not isinstance(value, dict) or set(value) != {"schema_version", "projects"}:
        raise ProjectError("project_registry_invalid", "Kimiflow project registry is invalid", 2)
    projects = value.get("projects")
    if value.get("schema_version") != REGISTRY_SCHEMA or not isinstance(projects, list) or len(projects) > MAX_PROJECTS:
        raise ProjectError("project_registry_invalid", "Kimiflow project registry is invalid", 2)
    clean = []
    for project in projects:
        if not isinstance(project, dict) or set(project) != {"id", "name", "root", "registered_at"}:
            raise ProjectError("project_registry_invalid", "Kimiflow project registry is invalid", 2)
        project_id = project.get("id")
        name = project.get("name")
        root = project.get("root")
        registered_at = project.get("registered_at")
        if (
            PROJECT_ID_RE.fullmatch(project_id or "") is None
            or PROJECT_NAME_RE.fullmatch(name or "") is None
            or not isinstance(root, str)
            or not os.path.isabs(root)
            or os.path.realpath(root) != root
            or not isinstance(registered_at, str)
            or len(registered_at) > 64
        ):
            raise ProjectError("project_registry_invalid", "Kimiflow project registry is invalid", 2)
        clean.append({"id": project_id, "name": name, "root": root, "registered_at": registered_at})
    for key in ("id", "name", "root"):
        values = [item[key] for item in clean]
        if len(values) != len(set(values)):
            raise ProjectError("project_registry_invalid", "Kimiflow project registry contains duplicates", 2)
    return {"schema_version": REGISTRY_SCHEMA, "projects": clean}


def _read_path(path):
    try:
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return {"schema_version": REGISTRY_SCHEMA, "projects": []}
    try:
        info = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_REGISTRY_BYTES
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OSError("unsafe registry file")
        payload = os.read(descriptor, MAX_REGISTRY_BYTES + 1)
        return _validate(json.loads(payload.decode("utf-8")))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError("project_registry_invalid", "Cannot read Kimiflow project registry: %s" % exc, 2)
    finally:
        os.close(descriptor)


def _write_path(path, parent, value):
    payload = (json.dumps(_validate(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".kimiflow-projects-", dir=parent)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise ProjectError("project_registry_unsafe", "Cannot write Kimiflow project registry: %s" % exc, 2)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _exact_git_root(root):
    if not isinstance(root, str) or not root or "\0" in root:
        raise ProjectError("project_root_invalid", "Project root is invalid", 2)
    candidate = os.path.realpath(os.path.abspath(root))
    try:
        proc = subprocess.run(
            ["git", "-C", candidate, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectError("project_root_invalid", "Cannot inspect project root: %s" % exc, 2)
    resolved = os.path.realpath(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else ""
    if not resolved or resolved != candidate:
        raise ProjectError("project_root_invalid", "Project must be an exact Git root", 2)
    return resolved


def _git_root(root):
    resolved = _exact_git_root(root)
    try:
        return os.path.realpath(wp.worktree_records(resolved)[0]["path"])
    except (OSError, wp.WorkspaceError) as exc:
        raise ProjectError("project_root_invalid", "Cannot resolve the primary project checkout: %s" % exc, 2)


def _slug(value, fallback="project"):
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return (normalized or fallback)[:64].rstrip("-._") or fallback


def _project_id(root):
    return "project-" + hashlib.sha256(root.encode("utf-8")).hexdigest()[:16]


def list_projects(environ=None):
    with _locked_registry(environ) as (path, _parent):
        registry = _read_path(path)
    projects = []
    for item in registry["projects"]:
        current = True
        try:
            _git_root(item["root"])
        except ProjectError:
            current = False
        projects.append({**item, "current": current})
    return {"schema_version": REGISTRY_SCHEMA, "status": "ok", "projects": projects}


def register(root, name=None, environ=None):
    root = _git_root(root)
    requested_name = _slug(name or os.path.basename(root))
    with _locked_registry(environ) as (path, parent):
        registry = _read_path(path)
        existing = next((item for item in registry["projects"] if item["root"] == root), None)
        if existing is not None:
            return {"schema_version": REGISTRY_SCHEMA, "status": "registered", "project": existing}
        names = {item["name"] for item in registry["projects"]}
        project_name = requested_name
        for index in range(2, MAX_PROJECTS + 2):
            if project_name not in names:
                break
            suffix = "-%s" % index
            project_name = requested_name[: 64 - len(suffix)].rstrip("-._") + suffix
        if project_name in names or len(registry["projects"]) >= MAX_PROJECTS:
            raise ProjectError("project_registry_full", "Kimiflow project registry capacity reached", 1)
        project = {
            "id": _project_id(root),
            "name": project_name,
            "root": root,
            "registered_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        registry["projects"].append(project)
        registry["projects"].sort(key=lambda item: (item["name"], item["id"]))
        _write_path(path, parent, registry)
    return {"schema_version": REGISTRY_SCHEMA, "status": "registered", "project": project}


def clone(source, name, environ=None):
    if (
        not isinstance(source, str)
        or not source.strip()
        or source.startswith("-")
        or "\0" in source
        or len(source.encode("utf-8")) > 4096
    ):
        raise ProjectError("project_source_invalid", "Project clone source is invalid", 2)
    project_name = _slug(name or "project")
    projects_root = os.path.join(_config_root(environ), "kimiflow-projects")
    _safe_parent(os.path.join(projects_root, "placeholder"))
    try:
        os.mkdir(projects_root, 0o700)
    except FileExistsError:
        info = os.lstat(projects_root)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ProjectError("project_registry_unsafe", "Managed project directory is unsafe", 2)
    target = os.path.realpath(os.path.join(projects_root, project_name))
    if os.path.commonpath((projects_root, target)) != projects_root or os.path.lexists(target):
        raise ProjectError("project_target_exists", "Managed project target already exists", 1)
    try:
        result = subprocess.run(
            ["git", "clone", "--", source.strip(), target],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise ProjectError("project_clone_failed", "Cannot clone project: %s" % exc, 1)
    if result.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        detail = result.stderr.strip() or "git clone failed"
        raise ProjectError("project_clone_failed", detail[:1000], 1)
    registered = register(target, name=project_name, environ=environ)
    return {**registered, "status": "cloned"}


def resolve(selector=None, cwd=None, environ=None, auto_register=True):
    if isinstance(selector, str) and selector.strip() and os.path.isabs(selector.strip()):
        return register(selector.strip(), environ=environ)["project"]
    with _locked_registry(environ) as (path, _parent):
        projects = _read_path(path)["projects"]
    if isinstance(selector, str) and selector.strip():
        selected = selector.strip()
        matches = [item for item in projects if item["id"] == selected or item["name"] == selected]
        if len(matches) != 1:
            raise ProjectError("project_not_found", "Kimiflow project selector is unknown or ambiguous", 1)
        _git_root(matches[0]["root"])
        return matches[0]
    if isinstance(cwd, str) and cwd:
        candidate = os.path.realpath(os.path.abspath(cwd))
        proc = subprocess.run(
            ["git", "-C", candidate, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            root = os.path.realpath(proc.stdout.strip())
            existing = next((item for item in projects if item["root"] == root), None)
            if existing is not None:
                return existing
            if auto_register:
                return register(root, environ=environ)["project"]
    current = [item for item in projects if os.path.isdir(item["root"])]
    if len(current) == 1:
        _git_root(current[0]["root"])
        return current[0]
    raise ProjectError("project_required", "Select a registered Kimiflow project", 1)


def remove(selector, environ=None):
    if not isinstance(selector, str) or not selector.strip():
        raise ProjectError("project_required", "Project selector is required", 2)
    selected = selector.strip()
    with _locked_registry(environ) as (path, parent):
        registry = _read_path(path)
        matches = [item for item in registry["projects"] if item["id"] == selected or item["name"] == selected]
        if len(matches) != 1:
            raise ProjectError("project_not_found", "Kimiflow project selector is unknown or ambiguous", 1)
        registry["projects"] = [item for item in registry["projects"] if item["id"] != matches[0]["id"]]
        _write_path(path, parent, registry)
    return {"schema_version": REGISTRY_SCHEMA, "status": "removed", "project": matches[0]}


def _numbered_run(root, number):
    prefix = "run-" + number.replace(".", "-")
    run_root = os.path.join(root, ".kimiflow")
    try:
        candidates = sorted(
            entry.name for entry in os.scandir(run_root)
            if entry.is_dir(follow_symlinks=False)
            and (entry.name == prefix or entry.name.startswith(prefix + "-"))
        )
    except OSError:
        candidates = []
    if len(candidates) == 1:
        return ".kimiflow/" + candidates[0]
    return None


def derive_run(root, request, worker_id):
    if not isinstance(request, str) or not request.strip() or len(request.encode("utf-8")) > 64 * 1024:
        raise ProjectError("project_task_invalid", "Kimiflow task request is missing or oversized", 2)
    if WORKER_RE.fullmatch(worker_id or "") is None:
        raise ProjectError("project_worker_invalid", "Kimiflow worker identity is invalid", 2)
    explicit = EXPLICIT_RUN_RE.search(request)
    if explicit is not None:
        return explicit.group("run")
    numbered = NUMBERED_RUN_RE.search(request)
    if numbered is not None:
        existing = _numbered_run(root, numbered.group("number"))
        if existing is not None:
            return existing
    words = re.findall(r"[A-Za-z0-9]+", request)[:10]
    base = _slug("-".join(words), fallback="task")[:48].rstrip("-._")
    suffix = hashlib.sha256(request.strip().encode("utf-8")).hexdigest()[:8]
    return ".kimiflow/%s-%s" % (base, suffix)


def adopt(
    root,
    captain_session_id,
    expected_captain_id=None,
    expected_worker_id=None,
):
    """Transfer a dead Pi Captain bridge without changing run/session ownership."""
    if model_adapter.SESSION_RE.fullmatch(captain_session_id or "") is None:
        raise ProjectError("project_captain_invalid", "Captain session identity is invalid", 2)
    if expected_captain_id is not None and model_adapter.SESSION_RE.fullmatch(expected_captain_id or "") is None:
        raise ProjectError("project_captain_invalid", "Expected Captain identity is invalid", 2)
    if expected_worker_id is not None and WORKER_RE.fullmatch(expected_worker_id or "") is None:
        raise ProjectError("project_worker_invalid", "Expected worker identity is invalid", 2)
    root = _exact_git_root(root)
    from . import runner

    try:
        receipt = runner.load_receipt(root)
    except (OSError, ValueError, runner.RunnerError) as exc:
        raise ProjectError("project_adoption_unavailable", "Cannot inspect Fleet runner receipt: %s" % exc, 1)
    bridge = receipt.get("bridge")
    controller = receipt.get("controller_pid")
    if (
        receipt.get("status") not in {
            "starting", "parked", "interrupted", "transport_error", "exhausted",
        }
        or not isinstance(bridge, dict)
        or bridge.get("schema_version") != 1
        or WORKER_RE.fullmatch(bridge.get("worker_id", "")) is None
        or (
            expected_captain_id is not None
            and bridge.get("captain_session_id") != expected_captain_id
        )
        or (
            expected_worker_id is not None
            and bridge.get("worker_id") != expected_worker_id
        )
        or not isinstance(controller, int)
        or isinstance(controller, bool)
        or controller <= 1
    ):
        raise ProjectError("project_adoption_unavailable", "Fleet runner is not at a safe adoption boundary", 1)
    try:
        os.kill(controller, 0)
    except ProcessLookupError:
        pass
    except PermissionError:
        raise ProjectError("project_adoption_busy", "Fleet runner controller ownership is ambiguous", 1)
    else:
        raise ProjectError("project_adoption_busy", "Fleet runner controller is still alive", 1)
    rebound = dict(receipt)
    rebound["bridge"] = {
        "schema_version": 1,
        "captain_session_id": captain_session_id,
        "worker_id": bridge["worker_id"],
    }
    try:
        runner.write_receipt(root, rebound)
    except (OSError, ValueError, runner.RunnerError) as exc:
        raise ProjectError("project_adoption_failed", "Cannot transfer Fleet bridge: %s" % exc, 1)
    return {
        "schema_version": REGISTRY_SCHEMA,
        "status": "adopted",
        "root": root,
        "run": rebound.get("active_run"),
        "worker_id": bridge["worker_id"],
        "provider_session_id": rebound.get("session_id"),
    }


def allocate(root, request, worker_id, write=True, environ=None):
    project = register(root, environ=environ)["project"]
    run = derive_run(project["root"], request, worker_id)
    try:
        routed = worktree_broker.route(
            project["root"], run, write=write, force_worktree=True,
        )
    except wp.WorkspaceError as exc:
        raise ProjectError("project_allocation_failed", str(exc), 1)
    worker_root = routed.get("root")
    if routed.get("route") == "queue" or not isinstance(worker_root, str):
        return {
            "schema_version": REGISTRY_SCHEMA,
            "status": "queued",
            "project": project,
            "run": run,
            "root": None,
            "queue_position": routed.get("queue_position"),
        }
    worker_root = os.path.realpath(worker_root)
    if routed.get("route") == "worktree" and worker_root == project["root"]:
        raise ProjectError("project_allocation_failed", "Fleet allocation did not isolate the worker", 2)
    return {
        "schema_version": REGISTRY_SCHEMA,
        "status": "allocated" if routed.get("route") == "worktree" else "resuming",
        "project": project,
        "run": run,
        "root": worker_root,
        "route": routed.get("route"),
        "branch": routed.get("branch"),
        "queue_position": routed.get("queue_position"),
    }
