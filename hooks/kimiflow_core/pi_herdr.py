"""Visible Herdr transport for Kimiflow's optional Pi host."""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from . import model_adapter


STATE_NAME = "PI-HERDR-ENDPOINTS-v1"
STATE_VERSION = 1
SESSION_VERSION = 3
MAX_JSON = 256 * 1024
MAX_SESSION_DELTA = 16 * 1024 * 1024
SETTLED = {"idle", "blocked", "done"}
ENDPOINT_SETTLE_TIMEOUT = 30
BRIDGE_ENV = "KIMIFLOW_PI_BRIDGE_BINDING"
HERDR_FLAG_ENV = "HERDR_ENV"
HERDR_KEYS = ("HERDR_WORKSPACE_ID", "HERDR_TAB_ID", "HERDR_PANE_ID")
WORKER_RE = re.compile(r"^worker-[A-Za-z0-9]{8,64}$")
HERDR_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{2,128}$")
SUBAGENT_SEAT_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
SUBAGENT_ROLES = {
    "research": True,
    "plan_review": True,
    "implementation": False,
    "verification": True,
    "code_review": True,
}


class HerdrError(ValueError):
    def __init__(self, status, message, code=1):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def requested(environ):
    value = environ.get(HERDR_FLAG_ENV)
    return value in {"1", "true", "TRUE"} and BRIDGE_ENV in environ


def _herdr(environ):
    command = shutil.which(
        environ.get("KIMIFLOW_HERDR_COMMAND") or "herdr",
        path=environ.get("PATH"),
    )
    if command is None:
        raise HerdrError("herdr_unavailable", "Herdr is not available", 1)
    return os.path.realpath(command)


def _invoke(args, environ, timeout=None):
    try:
        result = subprocess.run(
            [_herdr(environ), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
            env=environ,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HerdrError("herdr_command_failed", "Herdr command failed: %s" % exc, 1)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Herdr error"
        raise HerdrError(
            "herdr_command_failed",
            "Herdr command failed: %s" % detail[:1000],
            1,
        )
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise HerdrError(
            "herdr_protocol_invalid",
            "Herdr returned invalid JSON: %s" % exc,
            2,
        )
    if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
        raise HerdrError("herdr_protocol_invalid", "Herdr result is incomplete", 2)
    return value["result"]


def _binding(environ, root):
    try:
        value = json.loads(environ.get(BRIDGE_ENV, ""))
    except (TypeError, ValueError):
        raise HerdrError("herdr_binding_invalid", "Pi bridge binding is invalid", 2)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or not isinstance(value.get("root"), str)
        or os.path.realpath(value["root"]) != root
        or not WORKER_RE.fullmatch(value.get("worker_id", ""))
        or model_adapter.SESSION_RE.fullmatch(
            value.get("captain_session_id", ""),
        ) is None
    ):
        raise HerdrError("herdr_binding_invalid", "Pi bridge binding is invalid", 2)
    return value


def _pane(pane_id, environ):
    value = _invoke(["pane", "get", pane_id], environ, timeout=10)
    pane = value.get("pane")
    if not isinstance(pane, dict):
        raise HerdrError("herdr_protocol_invalid", "Herdr pane result is missing", 2)
    return pane


def _context(environ, root):
    values = {key: environ.get(key) for key in HERDR_KEYS}
    if any(
        not isinstance(value, str) or HERDR_ID_RE.fullmatch(value) is None
        for value in values.values()
    ):
        raise HerdrError(
            "herdr_context_invalid",
            "Kimiflow requires the exact Captain Herdr workspace, tab, and pane",
            2,
        )
    pane = _pane(values["HERDR_PANE_ID"], environ)
    if (
        pane.get("workspace_id") != values["HERDR_WORKSPACE_ID"]
        or pane.get("tab_id") != values["HERDR_TAB_ID"]
        or pane.get("pane_id") != values["HERDR_PANE_ID"]
        or pane.get("agent") != "pi"
    ):
        raise HerdrError(
            "herdr_context_invalid",
            "Kimiflow could not verify the exact Captain Herdr identity",
            2,
        )
    return {
        "workspace_id": values["HERDR_WORKSPACE_ID"],
        "tab_id": values["HERDR_TAB_ID"],
        "pane_id": values["HERDR_PANE_ID"],
    }


def _safe_directory(path):
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or os.path.realpath(path) != path
    ):
        raise HerdrError("herdr_state_unsafe", "Herdr endpoint state is unsafe", 2)
    os.chmod(path, 0o700)


def _state_directory(root):
    kimiflow = os.path.join(root, ".kimiflow")
    session = os.path.join(kimiflow, "session")
    state = os.path.join(session, STATE_NAME)
    for path in (kimiflow, session, state):
        _safe_directory(path)
    return state


def _state_paths(root, worker_id):
    if WORKER_RE.fullmatch(worker_id) is None:
        raise HerdrError("herdr_binding_invalid", "Herdr worker identity is invalid", 2)
    directory = _state_directory(root)
    return (
        os.path.join(directory, worker_id + ".json"),
        os.path.join(directory, worker_id),
    )


def _write_state(path, value):
    payload = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    temporary = "%s.tmp-%s" % (path, uuid.uuid4().hex)
    descriptor = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        os.write(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _load_state(path):
    descriptor = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_JSON
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise OSError("invalid endpoint state file")
        payload = os.read(descriptor, MAX_JSON + 1)
        value = json.loads(payload.decode("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as exc:
        raise HerdrError(
            "herdr_state_unsafe",
            "Cannot read Herdr endpoint state: %s" % exc,
            2,
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != STATE_VERSION
        or model_adapter.SESSION_RE.fullmatch(value.get("session_id", "")) is None
        or any(
            HERDR_ID_RE.fullmatch(value.get(key, "")) is None
            for key in ("workspace_id", "tab_id", "pane_id")
        )
        or (
            "cleanup_pending" in value
            and not isinstance(value["cleanup_pending"], bool)
        )
        or (
            value.get("session_path") is not None
            and (
                not isinstance(value.get("session_path"), str)
                or not os.path.isabs(value["session_path"])
            )
        )
    ):
        raise HerdrError("herdr_state_unsafe", "Herdr endpoint state is invalid", 2)
    return value


def _copy_extension(source, expected_digest, directory, filename):
    with open(source, "rb") as handle:
        content = handle.read(MAX_JSON * 4 + 1)
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if digest != expected_digest or len(content) > MAX_JSON * 4:
        raise HerdrError("herdr_extension_invalid", "Pi worker extension changed", 2)
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        info = os.lstat(directory)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise HerdrError("herdr_state_unsafe", "Herdr extension state is unsafe", 2)
    target = os.path.join(directory, filename)
    temporary = target + ".tmp-" + uuid.uuid4().hex
    descriptor = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o400)
        os.write(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, target)
        os.chmod(target, 0o400)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return target


def _session_header(path, root, expected_session):
    try:
        with open(path, "rb") as handle:
            raw = handle.readline(MAX_JSON + 1)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise HerdrError(
            "herdr_session_invalid",
            "Cannot verify Pi's native session: %s" % exc,
            2,
        )
    if (
        not isinstance(value, dict)
        or value.get("type") != "session"
        or value.get("version") != SESSION_VERSION
        or value.get("id") != expected_session
        or os.path.realpath(value.get("cwd", "")) != root
    ):
        raise HerdrError(
            "herdr_session_invalid",
            "Pi's native session does not match the Herdr endpoint",
            2,
        )
    return os.path.realpath(path)


def _endpoint_pane(state, root, environ, require_settled=True):
    pane = _pane(state["pane_id"], environ)
    session = pane.get("agent_session")
    status_value = pane.get("agent_status")
    expected_path = state.get("session_path")
    if (
        pane.get("workspace_id") != state["workspace_id"]
        or pane.get("tab_id") != state["tab_id"]
        or pane.get("pane_id") != state["pane_id"]
        or pane.get("agent") != "pi"
        or os.path.realpath(pane.get("cwd", "")) != root
    ):
        raise HerdrError(
            "herdr_endpoint_invalid",
            "The exact Kimiflow Herdr endpoint identity is invalid",
            1,
        )
    if expected_path is not None:
        if session is None:
            raise HerdrError(
                "herdr_endpoint_busy",
                "The exact Kimiflow Herdr endpoint session is still settling",
                1,
            )
        if (
            not isinstance(session, dict)
            or session.get("kind") != "path"
            or not isinstance(session.get("value"), str)
            or os.path.realpath(session["value"]) != expected_path
        ):
            raise HerdrError(
                "herdr_endpoint_invalid",
                "The exact Kimiflow Herdr endpoint session is invalid",
                1,
            )
    if require_settled and status_value not in SETTLED:
        raise HerdrError(
            "herdr_endpoint_busy",
            "The exact Kimiflow Herdr endpoint is still settling",
            1,
        )
    if expected_path is not None:
        _session_header(expected_path, root, state["session_id"])
    return pane


def _wait_for_settled_endpoint(state, root, environ, timeout=None):
    timeout = ENDPOINT_SETTLE_TIMEOUT if timeout is None else timeout
    deadline = time.time() + timeout
    while True:
        try:
            return _endpoint_pane(state, root, environ, require_settled=True)
        except HerdrError as exc:
            if exc.status != "herdr_endpoint_busy" or time.time() >= deadline:
                raise
            time.sleep(0.05)


def _close_exact_ids(workspace_id, tab_id, pane_id, environ):
    try:
        pane = _pane(pane_id, environ)
    except HerdrError as exc:
        return "pane_not_found" in exc.message
    if (
        pane.get("workspace_id") != workspace_id
        or pane.get("tab_id") != tab_id
        or pane.get("pane_id") != pane_id
    ):
        return False
    try:
        _invoke(["tab", "close", tab_id], environ, timeout=10)
        _pane(pane_id, environ)
    except HerdrError as exc:
        return "pane_not_found" in exc.message
    return False


def _remove_state(state_path, endpoint_directory):
    try:
        os.unlink(state_path)
    except FileNotFoundError:
        pass
    shutil.rmtree(endpoint_directory, ignore_errors=True)


def _cleanup_tracked_endpoint(state, state_path, endpoint_directory, environ):
    pending = dict(state)
    pending["cleanup_pending"] = True
    _write_state(state_path, pending)
    if not _close_exact_ids(
        state["workspace_id"], state["tab_id"], state["pane_id"], environ,
    ):
        return False
    _remove_state(state_path, endpoint_directory)
    return True


def _tab(root, workspace_id, label, environment, environ):
    args = [
        "tab", "create",
        "--workspace", workspace_id,
        "--cwd", root,
        "--label", label,
        "--no-focus",
    ]
    for key, value in sorted(environment.items()):
        if isinstance(value, str):
            args += ["--env", "%s=%s" % (key, value)]
    value = _invoke(args, environ, timeout=30)
    tab = value.get("tab")
    pane = value.get("root_pane")
    if (
        not isinstance(tab, dict)
        or not isinstance(pane, dict)
        or tab.get("workspace_id") != workspace_id
        or pane.get("workspace_id") != workspace_id
        or tab.get("tab_id") != pane.get("tab_id")
        or HERDR_ID_RE.fullmatch(tab.get("tab_id", "")) is None
        or HERDR_ID_RE.fullmatch(pane.get("pane_id", "")) is None
    ):
        raise HerdrError("herdr_protocol_invalid", "Herdr created an invalid tab", 2)
    return tab["tab_id"], pane["pane_id"]


def _stored_session(root, session_id, environ):
    base = environ.get("PI_CODING_AGENT_SESSION_DIR")
    if not isinstance(base, str) or not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), ".pi", "agent", "sessions")
    suffix = "_%s.jsonl" % session_id
    matches = []
    try:
        with os.scandir(base) as projects:
            for project in projects:
                if not project.is_dir(follow_symlinks=False):
                    continue
                with os.scandir(project.path) as sessions:
                    for candidate in sessions:
                        if (
                            candidate.is_file(follow_symlinks=False)
                            and candidate.name.endswith(suffix)
                        ):
                            matches.append(candidate.path)
    except OSError:
        return None
    verified = []
    for candidate in matches:
        try:
            verified.append(_session_header(candidate, root, session_id))
        except HerdrError:
            continue
    if len(verified) > 1:
        raise HerdrError(
            "herdr_session_invalid",
            "Pi exposed more than one native session for the exact identity",
            2,
        )
    return verified[0] if verified else None


def _wait_for_native_session(pane_id, root, session_id, environ):
    deadline = time.time() + 10
    last = None
    while time.time() < deadline:
        try:
            pane = _pane(pane_id, environ)
            native = pane.get("agent_session")
            if isinstance(native, dict) and native.get("kind") == "path":
                return _session_header(native.get("value", ""), root, session_id)
            stored = _stored_session(root, session_id, environ)
            if stored is not None:
                return stored
        except HerdrError as exc:
            last = exc
        time.sleep(0.05)
    raise last or HerdrError(
        "herdr_session_invalid",
        "Herdr did not expose Pi's native session",
        2,
    )


def _start_agent(
    name,
    pane_id,
    selection,
    session_id,
    environ,
    extensions=(),
    resume=False,
    read_only=False,
):
    agent_args = ["--no-extensions"]
    for extension in extensions:
        agent_args += ["--extension", extension]
    if read_only:
        agent_args += ["--tools", "read,grep,find,ls"]
    agent_args += [
        "--provider", selection["provider"],
        "--model", selection["model"],
        "--thinking", selection["thinking"],
        "--session" if resume else "--session-id",
        session_id,
    ]
    command = [
        "agent", "start", name,
        "--kind", "pi",
        "--pane", pane_id,
        "--timeout", "300000",
        "--",
        *agent_args,
    ]
    deadline = time.time() + 5
    while True:
        try:
            _invoke(command, environ, timeout=310)
            return
        except HerdrError as exc:
            if "agent_pane_busy" not in exc.message or time.time() >= deadline:
                raise
            time.sleep(0.05)


def _create_endpoint(
    root,
    context,
    binding,
    selection,
    material,
    environ,
    session_id=None,
):
    state_path, endpoint_directory = _state_paths(root, binding["worker_id"])
    resume = session_id is not None
    session_id = session_id or str(uuid.uuid4())
    calm_extension = _copy_extension(
        material["calm_extension"],
        material["calm_extension_digest"],
        endpoint_directory,
        "calm.js",
    )
    worker_extension = _copy_extension(
        material["worker_extension"],
        material["worker_extension_digest"],
        endpoint_directory,
        "worker.js",
    )
    herdr_extension = _copy_extension(
        material["herdr_extension"],
        material["herdr_extension_digest"],
        endpoint_directory,
        "herdr-agent-state.ts",
    )
    worker_env = {
        BRIDGE_ENV: environ[BRIDGE_ENV],
        "KIMIFLOW_HOST": "codex",
        "KIMIFLOW_PLUGIN_ROOT": os.path.realpath(os.path.join(
            os.path.dirname(material["active_run_hook"]), "..",
        )),
        "KIMIFLOW_PI_EXECUTABLE": material["command"],
        "KIMIFLOW_PI_ACTIVE_RUN": material["active_run_hook"],
        "KIMIFLOW_PI_SELECTION": json.dumps(
            selection, sort_keys=True, separators=(",", ":"),
        ),
        "KIMIFLOW_PI_HERDR": "1",
        "KIMIFLOW_PI_CALM_EXTENSION": calm_extension,
        "KIMIFLOW_PI_CALM_EXTENSION_DIGEST": material["calm_extension_digest"],
        "KIMIFLOW_PI_VERBOSITY": environ.get(
            "KIMIFLOW_PI_VERBOSITY", "balanced",
        ),
    }
    tab_id = pane_id = None
    try:
        tab_id, pane_id = _tab(
            root,
            context["workspace_id"],
            "kimiflow · main",
            worker_env,
            environ,
        )
        _start_agent(
            "kimiflow-main",
            pane_id,
            selection,
            session_id,
            environ,
            extensions=(herdr_extension, calm_extension, worker_extension),
            resume=resume,
        )
        session_path = (
            _wait_for_native_session(pane_id, root, session_id, environ)
            if resume
            else None
        )
        state = {
            "schema_version": STATE_VERSION,
            "root": root,
            "worker_id": binding["worker_id"],
            "session_id": session_id,
            "workspace_id": context["workspace_id"],
            "tab_id": tab_id,
            "pane_id": pane_id,
            "session_path": session_path,
            "cleanup_pending": False,
        }
        _write_state(state_path, state)
        return state
    except Exception:
        if tab_id is not None and pane_id is not None:
            state = {
                "schema_version": STATE_VERSION,
                "root": root,
                "worker_id": binding["worker_id"],
                "session_id": session_id,
                "workspace_id": context["workspace_id"],
                "tab_id": tab_id,
                "pane_id": pane_id,
                "session_path": None,
                "cleanup_pending": True,
            }
            if not _close_exact_ids(
                context["workspace_id"], tab_id, pane_id, environ,
            ):
                _write_state(state_path, state)
            else:
                _remove_state(state_path, endpoint_directory)
        else:
            _remove_state(state_path, endpoint_directory)
        raise


def _message_text(message):
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return None
    return "".join(
        item["text"]
        for item in message["content"]
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        )
    )


def _turn_result(session_path, offset, prompt):
    try:
        size = os.path.getsize(session_path)
        if size < offset or size - offset > MAX_SESSION_DELTA:
            raise OSError("session delta is invalid")
        with open(session_path, "rb") as handle:
            handle.seek(offset)
            payload = handle.read(MAX_SESSION_DELTA + 1)
        lines = [
            json.loads(line)
            for line in payload.decode("utf-8").splitlines()
            if line
        ]
    except (OSError, UnicodeError, ValueError) as exc:
        raise HerdrError(
            "herdr_turn_invalid",
            "Cannot correlate Pi's native turn: %s" % exc,
            2,
        )
    user_seen = False
    final = None
    for entry in lines:
        if not isinstance(entry, dict):
            raise HerdrError(
                "herdr_turn_invalid",
                "Pi's native turn contains an invalid entry",
                2,
            )
        if entry.get("type") != "message":
            continue
        message = entry.get("message")
        role = message.get("role") if isinstance(message, dict) else None
        if role == "user":
            if user_seen or _message_text(message) != prompt:
                raise HerdrError(
                    "herdr_turn_invalid",
                    "Pi's native turn does not contain the exact submitted prompt",
                    2,
                )
            user_seen = True
            final = None
        elif role == "assistant" and user_seen:
            final = message
    if (
        not user_seen
        or not isinstance(final, dict)
        or final.get("stopReason") != "stop"
    ):
        raise HerdrError(
            "herdr_turn_incomplete",
            "Pi did not append a clean final assistant result",
            1,
        )
    return _message_text(final) or ""


def _sentinel(
    control_fd,
    ready_fd,
    workspace_id,
    tab_id,
    pane_id,
    root=None,
    worker_id=None,
):
    try:
        os.write(ready_fd, b"ready\n")
    finally:
        os.close(ready_fd)
    command = os.read(control_fd, 64)
    os.close(control_fd)
    if command == b"keep\n":
        return 0
    environ = os.environ.copy()
    closed = _close_exact_ids(workspace_id, tab_id, pane_id, environ)
    if root is not None and worker_id is not None:
        try:
            state_path, endpoint_directory = _state_paths(
                os.path.realpath(root), worker_id,
            )
            state = _load_state(state_path)
            if (
                state is not None
                and state["workspace_id"] == workspace_id
                and state["tab_id"] == tab_id
                and state["pane_id"] == pane_id
            ):
                if closed:
                    _remove_state(state_path, endpoint_directory)
                else:
                    pending = dict(state)
                    pending["cleanup_pending"] = True
                    _write_state(state_path, pending)
        except HerdrError:
            return 1
    return 0 if closed else 1


def _start_sentinel(state, environ):
    control_read, control_write = os.pipe()
    ready_read, ready_write = os.pipe()
    child_env = dict(environ)
    child_env.pop("__KIMIFLOW_PI_SUBAGENT_TOKEN", None)
    tracking = (
        [state["root"], state["worker_id"]]
        if "worker_id" in state
        else []
    )
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "kimiflow_core.pi_herdr",
                "_sentinel",
                str(control_read),
                str(ready_write),
                state["workspace_id"],
                state["tab_id"],
                state["pane_id"],
                *tracking,
            ],
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(control_read, ready_write),
            start_new_session=True,
        )
    finally:
        os.close(control_read)
        os.close(ready_write)
    ready, _, _ = select.select([ready_read], [], [], 5)
    payload = os.read(ready_read, 64) if ready else b""
    os.close(ready_read)
    if payload != b"ready\n":
        os.close(control_write)
        process.kill()
        process.wait()
        raise HerdrError(
            "herdr_cleanup_unavailable",
            "Herdr turn cleanup sentinel did not become ready",
            1,
        )
    return {"control": control_write, "process": process}


def _finish_sentinel(sentinel, keep):
    if keep:
        os.write(sentinel["control"], b"keep\n")
    os.close(sentinel["control"])
    sentinel["process"].wait(timeout=10)


def _watch_parent(environ):
    if environ.get("KIMIFLOW_RUNNER_CONTROLLER") != "1":
        return
    parent = os.getppid()

    def watch():
        while True:
            if os.getppid() != parent:
                os._exit(137)
            time.sleep(0.02)

    threading.Thread(
        target=watch,
        name="kimiflow-herdr-runner-watchdog",
        daemon=True,
    ).start()


def _prompt(state, prompt, environ):
    _wait_for_settled_endpoint(state, state["root"], environ)
    session_path = state.get("session_path")
    offset = os.path.getsize(session_path) if session_path is not None else 0
    sentinel = _start_sentinel(state, environ)
    keep = False
    try:
        _invoke(
            [
                "agent", "prompt", state["pane_id"], prompt,
                "--wait",
                "--until", "idle",
                "--until", "blocked",
                "--until", "done",
                "--timeout", "21600000",
            ],
            environ,
            timeout=21610,
        )
        # Herdr has already completed the exact prompt boundary. From here on,
        # correlation errors must remain recoverable and must not make the
        # cleanup sentinel delete a visible, resumable Pi worker.
        keep = True
        if session_path is None:
            session_path = _wait_for_native_session(
                state["pane_id"],
                state["root"],
                state["session_id"],
                environ,
            )
            state["session_path"] = session_path
            if "worker_id" in state:
                state_path, _directory = _state_paths(
                    state["root"], state["worker_id"],
                )
                _write_state(state_path, state)
        result = _turn_result(session_path, offset, prompt)
        return result
    except Exception as exc:
        if keep:
            setattr(exc, "preserve_herdr_endpoint", True)
        raise
    finally:
        _finish_sentinel(sentinel, keep)


def run_turn(payload, material, selection, prompt, environ, emit):
    root = os.path.realpath(payload["root"])
    binding = _binding(environ, root)
    context = _context(environ, root)
    state_path, endpoint_directory = _state_paths(root, binding["worker_id"])
    state = _load_state(state_path)
    expected = payload.get("session_id")
    if state is not None and (
        state.get("root") != root
        or state.get("worker_id") != binding["worker_id"]
        or expected is not None and state.get("session_id") != expected
    ):
        raise HerdrError(
            "herdr_session_mismatch",
            "Herdr endpoint belongs to another Pi session",
            2,
        )
    if state is not None and state.get("cleanup_pending", False):
        if not _cleanup_tracked_endpoint(
            state, state_path, endpoint_directory, environ,
        ):
            raise HerdrError(
                "herdr_cleanup_pending",
                "The previous Kimiflow Herdr endpoint is still closing",
                1,
            )
        state = None
    if state is not None:
        try:
            _wait_for_settled_endpoint(state, root, environ)
        except HerdrError as exc:
            if exc.status == "herdr_endpoint_busy":
                raise
            if not _cleanup_tracked_endpoint(
                state, state_path, endpoint_directory, environ,
            ):
                raise HerdrError(
                    "herdr_cleanup_pending",
                    "The invalid Kimiflow Herdr endpoint is still closing",
                    1,
                )
            state = None
    if state is None:
        state = _create_endpoint(
            root,
            context,
            binding,
            selection,
            material,
            environ,
            session_id=expected,
        )
    if expected is not None and state["session_id"] != expected:
        raise HerdrError(
            "herdr_session_mismatch",
            "Herdr resumed a different Pi session",
            2,
        )
    emit({
        "schema_version": 1,
        "type": "session.started",
        "session_id": state["session_id"],
    })
    _watch_parent(environ)
    try:
        text = _prompt(state, prompt, environ)
    except Exception as exc:
        if not getattr(exc, "preserve_herdr_endpoint", False):
            _cleanup_tracked_endpoint(
                state, state_path, endpoint_directory, environ,
            )
        raise
    if text:
        emit({"schema_version": 1, "type": "message", "text": text})
    emit({"schema_version": 1, "type": "turn.completed"})
    return 0


def terminate(root, session_id, environ):
    root = os.path.realpath(root)
    binding = _binding(environ, root)
    state_path, endpoint_directory = _state_paths(root, binding["worker_id"])
    state = _load_state(state_path)
    if state is None:
        return False
    if state["session_id"] != session_id:
        raise HerdrError(
            "herdr_session_mismatch",
            "Refusing to close another Pi session",
            2,
        )
    closed = _close_exact_ids(
        state["workspace_id"], state["tab_id"], state["pane_id"], environ,
    )
    if closed:
        _remove_state(state_path, endpoint_directory)
    else:
        pending = dict(state)
        pending["cleanup_pending"] = True
        _write_state(state_path, pending)
    return closed


def run_subagent(payload, environ, emit):
    root = os.path.realpath(payload.get("root", ""))
    calm_extension = payload.get("calm_extension", "")
    herdr_extension = payload.get("herdr_extension", "")
    if (
        payload.get("schema_version") != 1
        or not os.path.isabs(root)
        or model_adapter.SESSION_RE.fullmatch(payload.get("session_id", "")) is None
        or not isinstance(payload.get("task"), str)
        or not payload["task"].strip()
        or len(payload["task"].encode("utf-8")) > 64 * 1024
        or isinstance(payload.get("slot"), bool)
        or not isinstance(payload.get("slot"), int)
        or payload["slot"] not in {1, 2, 3}
        or payload.get("role") not in SUBAGENT_ROLES
        or isinstance(payload.get("round"), bool)
        or not isinstance(payload.get("round"), int)
        or payload["round"] < 1
        or payload["round"] > 99
        or SUBAGENT_SEAT_RE.fullmatch(payload.get("seat", "")) is None
        or not isinstance(calm_extension, str)
        or not os.path.isabs(calm_extension)
        or os.path.realpath(calm_extension) != calm_extension
        or os.path.basename(calm_extension) != "calm.js"
        or re.fullmatch(r"sha256:[0-9a-f]{64}", payload.get("calm_extension_digest", "")) is None
        or not isinstance(herdr_extension, str)
        or not os.path.isabs(herdr_extension)
        or os.path.realpath(herdr_extension) != herdr_extension
        or os.path.basename(herdr_extension) != "herdr-agent-state.ts"
        or re.fullmatch(r"sha256:[0-9a-f]{64}", payload.get("herdr_extension_digest", "")) is None
        or payload.get("verbosity") not in {"quiet", "balanced", "verbose"}
        or not isinstance(payload.get("selection"), dict)
    ):
        raise HerdrError("herdr_subagent_invalid", "Herdr subagent request is invalid", 2)
    try:
        calm_info = os.lstat(calm_extension)
        with open(calm_extension, "rb") as handle:
            calm_content = handle.read(MAX_JSON * 4 + 1)
        herdr_info = os.lstat(herdr_extension)
        with open(herdr_extension, "rb") as handle:
            herdr_content = handle.read(MAX_JSON * 4 + 1)
    except OSError as exc:
        raise HerdrError("herdr_subagent_invalid", "Herdr Pi extensions are unavailable", 2) from exc
    if (
        not stat.S_ISREG(calm_info.st_mode)
        or stat.S_ISLNK(calm_info.st_mode)
        or len(calm_content) > MAX_JSON * 4
        or "sha256:" + hashlib.sha256(calm_content).hexdigest()
        != payload["calm_extension_digest"]
    ):
        raise HerdrError("herdr_subagent_invalid", "Herdr Calm extension is invalid", 2)
    if (
        not stat.S_ISREG(herdr_info.st_mode)
        or stat.S_ISLNK(herdr_info.st_mode)
        or len(herdr_content) > MAX_JSON * 4
        or "sha256:" + hashlib.sha256(herdr_content).hexdigest()
        != payload["herdr_extension_digest"]
        or b"HERDR_INTEGRATION_ID=pi" not in herdr_content
        or b"pane.report_agent_session" not in herdr_content
    ):
        raise HerdrError("herdr_subagent_invalid", "Herdr agent-state extension is invalid", 2)
    selection = payload["selection"]
    if (
        set(selection) != {"provider", "model", "thinking"}
        or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", selection.get("provider", ""))
        is None
        or re.fullmatch(
            r"[A-Za-z0-9@][A-Za-z0-9._/@:-]{0,191}",
            selection.get("model", ""),
        ) is None
        or selection.get("thinking")
        not in {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
    ):
        raise HerdrError("herdr_subagent_invalid", "Herdr subagent model is invalid", 2)
    context = _context(environ, root)
    session_id = payload["session_id"]
    tab_id = pane_id = None
    state = None
    sentinel = None
    extension_directory = tempfile.mkdtemp(prefix="kimiflow-herdr-subagent-")
    copied_herdr = _copy_extension(
        herdr_extension,
        payload["herdr_extension_digest"],
        extension_directory,
        "herdr-agent-state.ts",
    )
    try:
        tab_id, pane_id = _tab(
            root,
            context["workspace_id"],
            "kimiflow · %s · %s" % (
                payload["role"].replace("_", " "), payload["seat"],
            ),
            {"KIMIFLOW_PI_VERBOSITY": payload["verbosity"]},
            environ,
        )
        _start_agent(
            "kimiflow-%s-%s" % (payload["role"].replace("_", "-"), payload["slot"]),
            pane_id,
            selection,
            session_id,
            environ,
            extensions=(copied_herdr, calm_extension),
            read_only=SUBAGENT_ROLES[payload["role"]],
        )
        state = {
            "root": root,
            "session_id": session_id,
            "workspace_id": context["workspace_id"],
            "tab_id": tab_id,
            "pane_id": pane_id,
            "session_path": None,
        }
        sentinel = _start_sentinel(state, environ)
        offset = 0
        prompt = "Kimiflow bounded %s subagent task (phase %s, round %s, seat %s):\n%s" % (
            payload["role"],
            {
                "research": 2,
                "plan_review": 4,
                "implementation": 5,
                "verification": 6,
                "code_review": 7,
            }[payload["role"]],
            payload["round"],
            payload["seat"],
            payload["task"].strip(),
        )
        _invoke(
            [
                "agent", "prompt", pane_id, prompt,
                "--wait",
                "--until", "idle",
                "--until", "blocked",
                "--until", "done",
                "--timeout", "21600000",
            ],
            environ,
            timeout=21610,
        )
        session_path = _wait_for_native_session(
            pane_id, root, session_id, environ,
        )
        state["session_path"] = session_path
        _wait_for_settled_endpoint(state, root, environ)
        result = _turn_result(session_path, offset, prompt)
        emit({"type": "session", "version": 3, "id": session_id, "cwd": root})
        emit({"type": "agent_start"})
        emit({
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": result}],
                "stopReason": "stop",
            },
        })
        emit({"type": "agent_end"})
        emit({"type": "agent_settled"})
        return 0
    finally:
        closed = False
        if state is not None:
            closed = _close_exact_ids(
                state["workspace_id"], state["tab_id"], state["pane_id"], environ,
            )
        if sentinel is not None:
            _finish_sentinel(sentinel, closed)
        elif tab_id is not None and pane_id is not None:
            _close_exact_ids(context["workspace_id"], tab_id, pane_id, environ)
        shutil.rmtree(extension_directory, ignore_errors=True)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) in (6, 8) and args[0] == "_sentinel":
        return _sentinel(
            int(args[1]),
            int(args[2]),
            args[3],
            args[4],
            args[5],
            *(args[6:] if len(args) == 8 else []),
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
