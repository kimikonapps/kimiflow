"""Optional Pi JSON transport for Kimiflow's command-agent contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

from . import model_adapter


PI_VERSION_RE = re.compile(r"^(?:pi\s+)?0\.82(?:\.[0-9]+)?(?:[-+][A-Za-z0-9.-]+)?$")
SELECTION_RE = re.compile(
    r"^(?P<provider>[a-z0-9][a-z0-9._-]{0,63})/"
    r"(?P<model>[A-Za-z0-9@][A-Za-z0-9._/@:-]{0,191})"
    r":(?P<thinking>off|minimal|low|medium|high|xhigh|max)$"
)
MAX_LINE = 256 * 1024
MAX_EXTENSION_BYTES = 1024 * 1024
PI_SESSION_VERSION = 3
TREE_TOKEN_ENV = "__KIMIFLOW_PI_TREE_TOKEN"
TREE_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
CLEANUP_LEASES_NAME = "PI-CLEANUP-LEASES-v1"
CLEANUP_LEASE_RE = re.compile(
    r"^lease-(?P<pid>[1-9][0-9]*)-(?P<token>[0-9a-f]{64})$"
)


class PiHostError(ValueError):
    def __init__(self, status, message, code=1):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def _command(environ=None):
    env = os.environ if environ is None else environ
    configured = env.get("KIMIFLOW_PI_COMMAND")
    resolved = shutil.which(configured or "pi", path=env.get("PATH"))
    if resolved is None:
        raise PiHostError(
            "pi_unavailable",
            "Pi is not installed or selected; Kimiflow never installs it automatically",
            1,
        )
    return os.path.realpath(resolved)


def _version(command, environ=None):
    env = os.environ if environ is None else environ
    try:
        result = subprocess.run(
            [command, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PiHostError("pi_unavailable", "cannot inspect Pi: %s" % exc, 1)
    version = result.stdout.strip()
    if result.returncode != 0 or PI_VERSION_RE.fullmatch(version) is None:
        raise PiHostError(
            "pi_incompatible",
            "Kimiflow requires the tested optional Pi 0.82.x protocol",
            1,
        )
    return version


def _read_worker_extension(path):
    handle = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        handle = os.open(path, flags)
        initial = os.fstat(handle)
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_size > MAX_EXTENSION_BYTES
            or (initial.st_dev, initial.st_ino)
            != (named.st_dev, named.st_ino)
        ):
            raise OSError("worker extension is not a bounded regular file")
        chunks = []
        remaining = MAX_EXTENSION_BYTES + 1
        while remaining > 0:
            chunk = os.read(handle, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        final = os.fstat(handle)
        final_named = os.stat(path, follow_symlinks=False)
        if (
            len(content) > MAX_EXTENSION_BYTES
            or (
                initial.st_dev,
                initial.st_ino,
                initial.st_size,
                initial.st_mtime_ns,
            )
            != (
                final.st_dev,
                final.st_ino,
                final.st_size,
                final.st_mtime_ns,
            )
            or (final.st_dev, final.st_ino)
            != (final_named.st_dev, final_named.st_ino)
        ):
            raise OSError("worker extension changed while reading")
    except OSError as exc:
        raise PiHostError(
            "pi_extension_unavailable",
            "cannot load Kimiflow's Pi worker extension: %s" % exc,
            1,
        )
    finally:
        if handle is not None:
            os.close(handle)
    return content


def _worker_extension():
    path = os.path.realpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "hosts", "pi", "extensions",
        "worker.js",
    ))
    content = _read_worker_extension(path)
    return path, "sha256:" + hashlib.sha256(content).hexdigest()


def _active_run_hook():
    path = os.path.realpath(os.path.join(
        os.path.dirname(__file__), "..", "active-run.sh",
    ))
    if (
        not os.path.isfile(path)
        or os.path.basename(path) != "active-run.sh"
        or not os.access(path, os.X_OK)
    ):
        raise PiHostError(
            "pi_extension_unavailable",
            "Kimiflow's Active Run hook is unavailable",
            1,
        )
    return path


def _sealed_worker_extension(path, expected_digest):
    content = _read_worker_extension(path)
    actual_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual_digest != expected_digest:
        raise PiHostError(
            "stale_pi_capability",
            "Pi worker extension changed before immutable copy creation",
            1,
        )
    directory = tempfile.mkdtemp(prefix="kimiflow-pi-extension-")
    copy_path = os.path.join(directory, "worker.js")
    writer = None
    reader = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        writer = os.open(copy_path, flags, 0o400)
        offset = 0
        while offset < len(content):
            offset += os.write(writer, content[offset:])
        os.fsync(writer)
        os.fchmod(writer, 0o400)
        os.close(writer)
        writer = None
        read_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            read_flags |= os.O_NOFOLLOW
        reader = os.open(copy_path, read_flags)
        info = os.fstat(reader)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_size != len(content)
        ):
            raise OSError("sealed Pi extension copy is invalid")
        os.unlink(copy_path)
        os.rmdir(directory)
        return reader, "/dev/fd/%s" % reader
    except OSError as exc:
        if reader is not None:
            os.close(reader)
        if writer is not None:
            os.close(writer)
        try:
            os.unlink(copy_path)
        except OSError:
            pass
        try:
            os.rmdir(directory)
        except OSError:
            pass
        raise PiHostError(
            "pi_extension_unavailable",
            "cannot create immutable Pi worker extension: %s" % exc,
            1,
        )


def _capability_material(environ=None):
    command = _command(environ)
    version = _version(command, environ)
    extension, extension_digest = _worker_extension()
    return {
        "command": command,
        "version": version,
        "active_run_hook": _active_run_hook(),
        "worker_extension": extension,
        "worker_extension_digest": extension_digest,
    }


def _material_token(material):
    payload = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def capabilities(environ=None):
    token = _material_token(_capability_material(environ))
    return {
        "schema_version": model_adapter.PROTOCOL_VERSION,
        "name": "pi-" + token.split(":", 1)[1][:16],
        "host": "pi",
        "capabilities": {
            key: True for key in model_adapter.CAPABILITY_KEYS
        },
        "features": {
            "workflow_context": True,
            "structured_events": True,
            "root_confinement": False,
        },
    }


def parse_selection(value):
    if not isinstance(value, str):
        raise PiHostError("pi_model_invalid", "Pi model selection is required", 2)
    matched = SELECTION_RE.fullmatch(value)
    if matched is None:
        raise PiHostError(
            "pi_model_invalid",
            "Pi model must use provider/model:thinking",
            2,
        )
    return matched.groupdict()


def _emit(value, stream=None):
    target = sys.stdout if stream is None else stream
    target.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    target.flush()


def _payload(stdin=None):
    source = sys.stdin if stdin is None else stdin
    raw = source.readline(MAX_LINE + 1)
    if not raw or len(raw.encode("utf-8")) > MAX_LINE:
        raise PiHostError("pi_request_invalid", "Pi request is missing or oversized", 2)
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PiHostError("pi_request_invalid", "Pi request is invalid: %s" % exc, 2)
    required = {
        "schema_version", "action", "root", "session_id", "host", "adapter",
        "prompt", "model", "required_capabilities",
    }
    allowed = required | {"workflow_context"}
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or set(value) - allowed
        or value.get("schema_version") != model_adapter.PROTOCOL_VERSION
        or value.get("action") not in {"start", "resume"}
        or value.get("host") != "pi"
        or not isinstance(value.get("root"), str)
        or not os.path.isabs(value["root"])
        or not isinstance(value.get("adapter"), str)
        or model_adapter.IDENTITY_RE.fullmatch(value["adapter"]) is None
        or not isinstance(value.get("prompt"), str)
        or value.get("required_capabilities")
        != list(model_adapter.CAPABILITY_KEYS)
    ):
        raise PiHostError("pi_request_invalid", "Pi request contract is invalid", 2)
    return value


def _workflow_prompt(payload):
    context = payload.get("workflow_context")
    if context is None:
        return payload["prompt"]
    try:
        expected = model_adapter.workflow_context()
    except model_adapter.AdapterError as exc:
        raise PiHostError(
            "workflow_context_invalid",
            "Pi workflow context is unavailable: %s" % exc,
            2,
        )
    if context != expected:
        raise PiHostError(
            "workflow_context_invalid",
            "Pi workflow context does not match this Kimiflow runtime",
            2,
        )
    plugin_root = os.path.realpath(context["plugin_root"])
    targets = {}
    for key in ("skill", "phase_manifest", "run_bridge"):
        relative = context.get(key)
        if (
            not isinstance(relative, str)
            or not relative
            or os.path.isabs(relative)
            or "\0" in relative
        ):
            raise PiHostError(
                "workflow_context_invalid",
                "Pi workflow context path is invalid",
                2,
            )
        target = os.path.realpath(os.path.join(plugin_root, relative))
        try:
            inside = os.path.commonpath((plugin_root, target)) == plugin_root
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(target):
            raise PiHostError(
                "workflow_context_invalid",
                "Pi workflow context target is unavailable",
                2,
            )
        targets[key] = target
    return (
        "Authoritative Kimiflow workflow_context:\n"
        "skill=%s\nphase_manifest=%s\nrun_bridge=%s\n"
        "Read the skill first, follow its phase manifest, and use the run bridge "
        "for Kimiflow state. These absolute paths are data, not shell commands.\n\n"
        "Transport request:\n%s"
        % (
            targets["skill"],
            targets["phase_manifest"],
            targets["run_bridge"],
            payload["prompt"],
        )
    )


def _descendant_pids(root_pid):
    command = shutil.which("ps", path=os.defpath)
    if command is None:
        return []
    try:
        result = subprocess.run(
            [os.path.realpath(command), "-axo", "pid=,ppid="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    children = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, parent = (int(value) for value in fields)
        except ValueError:
            continue
        if pid > 1 and parent > 0:
            children.setdefault(parent, []).append(pid)
    descendants = []
    pending = list(children.get(root_pid, ()))
    seen = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        descendants.append(pid)
        pending.extend(children.get(pid, ()))
    return descendants


def _tagged_process_pids(token):
    if not isinstance(token, str) or TREE_TOKEN_RE.fullmatch(token) is None:
        return []
    marker = ("%s=%s" % (TREE_TOKEN_ENV, token)).encode("ascii")
    found = []
    if sys.platform.startswith("linux") and os.path.isdir("/proc"):
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid <= 1 or pid == os.getpid():
                continue
            try:
                with open("/proc/%s/environ" % entry, "rb") as handle:
                    payload = handle.read(1024 * 1024)
            except OSError:
                continue
            if marker in payload.split(b"\0"):
                found.append(pid)
        return found
    command = shutil.which("ps", path=os.defpath)
    if command is None:
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi process ownership discovery is unavailable",
            1,
        )
    process = None
    try:
        process = subprocess.Popen(
            [os.path.realpath(command), "eww", "-axo", "pid=,command="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"LC_ALL": "C", "PATH": os.defpath},
        )
        assert process.stdout is not None
        needle = b" " + marker
        for raw in process.stdout:
            if needle not in raw:
                continue
            fields = raw.lstrip().split(None, 1)
            if len(fields) != 2:
                continue
            try:
                pid = int(fields[0])
            except ValueError:
                continue
            if pid > 1 and pid != os.getpid():
                found.append(pid)
        returncode = process.wait(timeout=2)
        process.stdout.close()
        if returncode != 0:
            raise PiHostError(
                "pi_cleanup_unavailable",
                "Pi process ownership discovery failed",
                1,
            )
    except (OSError, subprocess.TimeoutExpired):
        if process is not None:
            process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi process ownership discovery failed",
            1,
        )
    return found


def _kill_process_group(pid, value):
    try:
        os.killpg(pid, value)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _stop_process(pid):
    if _kill_process_group(pid, signal.SIGSTOP):
        return
    try:
        os.kill(pid, signal.SIGSTOP)
    except OSError:
        pass


def _reap_owned_processes(root_pid, tree_token, trusted_root=False):
    root_owned = trusted_root is True
    if root_owned and isinstance(root_pid, int) and root_pid > 1:
        _stop_process(root_pid)
    descendants = set()
    stable = 0
    cleanup_error = None
    for _attempt in range(40):
        try:
            observed = set(_tagged_process_pids(tree_token))
        except PiHostError as exc:
            cleanup_error = exc
            observed = set()
        if isinstance(root_pid, int) and root_pid in observed:
            root_owned = True
        if root_owned and isinstance(root_pid, int) and root_pid > 1:
            observed.update(_descendant_pids(root_pid))
        observed.discard(os.getpid())
        fresh = observed - descendants
        descendants.update(observed)
        for pid in observed:
            _stop_process(pid)
        if fresh:
            stable = 0
        else:
            stable += 1
            if stable >= 2 or cleanup_error is not None:
                break
        time.sleep(0.01)
    for pid in reversed(sorted(descendants)):
        if not _kill_process_group(pid, signal.SIGKILL):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    if root_owned and isinstance(root_pid, int) and root_pid > 1:
        if not _kill_process_group(root_pid, signal.SIGKILL):
            try:
                os.kill(root_pid, signal.SIGKILL)
            except OSError:
                pass
    if cleanup_error is not None:
        raise cleanup_error


def _cleanup_session(root):
    if (
        not isinstance(root, str)
        or not os.path.isabs(root)
        or os.path.realpath(root) != root
    ):
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup root is invalid",
            1,
        )
    kimiflow = os.path.join(root, ".kimiflow")
    session = os.path.join(kimiflow, "session")
    for candidate in (kimiflow, session):
        try:
            os.mkdir(candidate, 0o700)
        except FileExistsError:
            pass
        try:
            info = os.lstat(candidate)
        except OSError as exc:
            raise PiHostError(
                "pi_cleanup_unavailable",
                "Pi cleanup state is unavailable: %s" % exc,
                1,
            )
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or os.path.realpath(candidate) != candidate
        ):
            raise PiHostError(
                "pi_cleanup_unavailable",
                "Pi cleanup state is unsafe",
                1,
            )
    return session


def _create_cleanup_lease(root, token):
    if TREE_TOKEN_RE.fullmatch(token or "") is None:
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup ownership token is invalid",
            1,
        )
    registry = os.path.join(_cleanup_session(root), CLEANUP_LEASES_NAME)
    try:
        os.mkdir(registry, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(registry)
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or os.path.realpath(registry) != registry
    ):
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup lease registry is unsafe",
            1,
        )
    lease = os.path.join(registry, "lease-%s-%s" % (os.getpid(), token))
    if CLEANUP_LEASE_RE.fullmatch(os.path.basename(lease)) is None:
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup lease is invalid",
            1,
        )
    os.mkdir(lease, 0o700)
    return lease


def _process_alive(pid):
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _recover_cleanup_lease(root, lease_name):
    match = (
        CLEANUP_LEASE_RE.fullmatch(lease_name)
        if isinstance(lease_name, str)
        else None
    )
    if match is None:
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup lease name is invalid",
            1,
        )
    owner_pid = int(match.group("pid"))
    if _process_alive(owner_pid):
        raise PiHostError(
            "pi_cleanup_active",
            "Pi cleanup sentinel is still active",
            1,
        )
    registry_path = os.path.join(_cleanup_session(root), CLEANUP_LEASES_NAME)
    registry = None
    lease = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        registry = os.open(registry_path, flags)
        registry_info = os.fstat(registry)
        named_registry = os.stat(registry_path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(registry_info.st_mode)
            or (registry_info.st_dev, registry_info.st_ino)
            != (named_registry.st_dev, named_registry.st_ino)
        ):
            raise OSError("cleanup registry changed")
        lease = os.open(lease_name, flags, dir_fd=registry)
        lease_info = os.fstat(lease)
        named_lease = os.stat(
            lease_name,
            dir_fd=registry,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(lease_info.st_mode)
            or (lease_info.st_dev, lease_info.st_ino)
            != (named_lease.st_dev, named_lease.st_ino)
            or os.listdir(lease)
        ):
            raise OSError("cleanup lease changed")
        _reap_owned_processes(None, match.group("token"))
        final = os.stat(
            lease_name,
            dir_fd=registry,
            follow_symlinks=False,
        )
        if (
            (final.st_dev, final.st_ino)
            != (lease_info.st_dev, lease_info.st_ino)
            or _process_alive(owner_pid)
        ):
            raise OSError("cleanup lease authority changed")
        os.rmdir(lease_name, dir_fd=registry)
    except FileNotFoundError:
        return
    except (OSError, PiHostError) as exc:
        if isinstance(exc, PiHostError):
            raise
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup lease recovery failed: %s" % exc,
            1,
        )
    finally:
        if lease is not None:
            os.close(lease)
        if registry is not None:
            os.close(registry)


def _cleanup_sentinel(control_fd, ready_fd, root):
    control = None
    lease = None
    try:
        control = os.fdopen(control_fd, "rb", buffering=0)
        token_line = control.readline(128)
        if (
            not token_line.endswith(b"\n")
            or len(token_line) != 65
        ):
            raise PiHostError(
                "pi_cleanup_unavailable",
                "Pi cleanup sentinel configuration is invalid",
                1,
            )
        token = token_line[:-1].decode("ascii")
        if TREE_TOKEN_RE.fullmatch(token) is None:
            raise PiHostError(
                "pi_cleanup_unavailable",
                "Pi cleanup sentinel token is invalid",
                1,
            )
        lease = _create_cleanup_lease(root, token)
        os.write(ready_fd, b"1")
        os.close(ready_fd)
        ready_fd = -1
        root_pid = None
        for raw in control:
            if len(raw) > 64 or not raw.endswith(b"\n"):
                raise PiHostError(
                    "pi_cleanup_unavailable",
                    "Pi cleanup sentinel command is invalid",
                    1,
                )
            value = raw[:-1].decode("ascii")
            if not value.startswith("pid:") or root_pid is not None:
                raise PiHostError(
                    "pi_cleanup_unavailable",
                    "Pi cleanup sentinel command is invalid",
                    1,
                )
            root_pid = int(value.split(":", 1)[1])
            if root_pid <= 1:
                raise ValueError("invalid cleanup root PID")
        _reap_owned_processes(root_pid, token, trusted_root=True)
        os.rmdir(lease)
        lease = None
        return 0
    except (OSError, UnicodeError, ValueError, PiHostError):
        if ready_fd >= 0:
            try:
                os.write(ready_fd, b"0")
            except OSError:
                pass
        return 1
    finally:
        if ready_fd >= 0:
            try:
                os.close(ready_fd)
            except OSError:
                pass
        if control is not None:
            control.close()


def _start_cleanup_sentinel(root, token):
    control_read, control_write = os.pipe()
    ready_read, ready_write = os.pipe()
    sentinel = None
    try:
        runtime_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        environment = dict(os.environ)
        environment.pop(TREE_TOKEN_ENV, None)
        environment["PYTHONPATH"] = runtime_root
        sentinel = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "kimiflow_core.pi_host",
                "_cleanup-sentinel",
                str(control_read),
                str(ready_write),
                root,
            ],
            cwd=runtime_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(control_read, ready_write),
        )
        os.close(control_read)
        control_read = -1
        os.close(ready_write)
        ready_write = -1
        os.write(control_write, (token + "\n").encode("ascii"))
        readable, _, _ = select.select([ready_read], [], [], 5)
        ready = os.read(ready_read, 1) if readable else b""
        if ready != b"1" or sentinel.poll() is not None:
            raise PiHostError(
                "pi_cleanup_unavailable",
                "Pi cleanup sentinel did not become ready",
                1,
            )
        return {
            "process": sentinel,
            "control": control_write,
        }
    except (OSError, PiHostError):
        try:
            os.close(control_write)
        except OSError:
            pass
        if sentinel is not None:
            _kill_process_group(sentinel.pid, signal.SIGKILL)
            sentinel.wait()
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup sentinel could not start",
            1,
        )
    finally:
        for descriptor in (control_read, ready_read, ready_write):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _finish_cleanup_sentinel(process):
    state = getattr(process, "__dict__", {})
    control = state.get("_kimiflow_cleanup_control")
    process._kimiflow_cleanup_control = None
    if control is not None:
        try:
            os.close(control)
        except OSError:
            pass
    sentinel = state.get("_kimiflow_cleanup_sentinel")
    process._kimiflow_cleanup_sentinel = None
    if sentinel is None:
        return
    try:
        returncode = sentinel.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_process_group(sentinel.pid, signal.SIGKILL)
        sentinel.wait()
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup sentinel did not finish",
            1,
        )
    if returncode != 0:
        raise PiHostError(
            "pi_cleanup_unavailable",
            "Pi cleanup sentinel failed",
            1,
        )


def _terminate(process):
    lock = getattr(process, "_kimiflow_termination_lock", None)
    if lock is None:
        lock = threading.RLock()
        process._kimiflow_termination_lock = lock
    with lock:
        tree_token = getattr(process, "_kimiflow_tree_token", None)
        cleanup_error = None
        try:
            _reap_owned_processes(process.pid, tree_token, trusted_root=True)
        except PiHostError as exc:
            cleanup_error = exc
        if process.poll() is None:
            process.kill()
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        else:
            process.wait()
        stream = getattr(process, "stdout", None)
        if stream is not None:
            stream.close()
        try:
            _finish_cleanup_sentinel(process)
        except PiHostError as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise cleanup_error


def _watch_runner_parent(process, environ):
    if (
        environ.get("KIMIFLOW_RUNNER_CONTROLLER") != "1"
        or "KIMIFLOW_PI_BRIDGE_BINDING" not in environ
    ):
        return None
    parent_pid = os.getppid()
    if parent_pid <= 1:
        _terminate(process)
        raise PiHostError(
            "runner_controller_lost",
            "Pi runner controller exited before watchdog registration",
            1,
        )

    def watch():
        while process.poll() is None:
            if os.getppid() != parent_pid:
                _terminate(process)
                os._exit(137)
            time.sleep(0.02)

    watcher = threading.Thread(
        target=watch,
        name="kimiflow-pi-runner-watchdog",
        daemon=True,
    )
    watcher.start()
    return watcher


def _assistant_text(event):
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None, False
    stopped = message.get("stopReason") in {"error", "aborted"}
    content = message.get("content")
    if not isinstance(content, list):
        return None, stopped
    parts = [
        item["text"]
        for item in content
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        )
    ]
    return "".join(parts) or None, stopped


def _transport_emit(value, output, process):
    try:
        _emit(value, output)
    except (OSError, UnicodeError, ValueError) as exc:
        _terminate(process)
        raise PiHostError(
            "runner_output_closed",
            "Pi transport output is unavailable: %s" % exc,
            1,
        )


def _transport_lines(process):
    try:
        for raw in process.stdout:
            yield raw
    except (OSError, UnicodeError) as exc:
        _terminate(process)
        raise PiHostError(
            "pi_event_invalid",
            "Pi event stream is unreadable: %s" % exc,
            2,
        )


def run_turn(payload, environ=None, stdout=None):
    env = dict(os.environ if environ is None else environ)
    material = _capability_material(env)
    token = _material_token(material)
    if payload.get("adapter") != "pi-" + token.split(":", 1)[1][:16]:
        raise PiHostError(
            "stale_pi_capability",
            "Pi capability token changed after the adapter preflight",
            1,
        )
    selection = parse_selection(payload.get("model"))
    session_id = payload.get("session_id")
    if payload["action"] == "resume":
        if not isinstance(session_id, str) or model_adapter.SESSION_RE.fullmatch(session_id) is None:
            raise PiHostError("pi_session_invalid", "Pi resume session is invalid", 2)
    elif session_id is not None:
        raise PiHostError("pi_session_invalid", "new Pi turn must not preselect a session", 2)
    command = material["command"]
    extension = material["worker_extension"]
    extension_digest = material["worker_extension_digest"]
    prompt = _workflow_prompt(payload)
    sealed_descriptor, sealed_extension = _sealed_worker_extension(
        extension, extension_digest,
    )
    argv = [
        command, "--mode", "json", "--no-extensions",
        "--extension", sealed_extension,
        "--provider", selection["provider"],
        "--model", selection["model"],
        "--thinking", selection["thinking"],
    ]
    if payload["action"] == "resume":
        argv += ["--session", session_id]
    # Pi 0.82.1 accepts the single-shot prompt positionally in JSON mode.
    argv.append(prompt)
    env["KIMIFLOW_PI_EXECUTABLE"] = command
    env["KIMIFLOW_PI_ACTIVE_RUN"] = material["active_run_hook"]
    env["KIMIFLOW_PI_SELECTION"] = json.dumps(
        selection, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    env["KIMIFLOW_PI_TRANSPORT_PROMPT"] = json.dumps(
        payload["prompt"], ensure_ascii=False,
    )
    tree_token = secrets.token_hex(32)
    env[TREE_TOKEN_ENV] = tree_token
    cleanup = None
    if (
        env.get("KIMIFLOW_RUNNER_CONTROLLER") == "1"
        and "KIMIFLOW_PI_BRIDGE_BINDING" in env
    ):
        cleanup = _start_cleanup_sentinel(
            os.path.realpath(payload["root"]),
            tree_token,
        )
    try:
        process = subprocess.Popen(
            argv,
            cwd=os.path.realpath(payload["root"]),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
            pass_fds=(sealed_descriptor,),
        )
    except OSError as exc:
        if cleanup is not None:
            os.close(cleanup["control"])
            cleanup["process"].wait(timeout=5)
        raise PiHostError("pi_spawn_failed", "cannot launch Pi: %s" % exc, 1)
    finally:
        os.close(sealed_descriptor)
    process._kimiflow_tree_token = tree_token
    if cleanup is not None:
        process._kimiflow_cleanup_control = cleanup["control"]
        process._kimiflow_cleanup_sentinel = cleanup["process"]
        try:
            os.write(cleanup["control"], ("pid:%s\n" % process.pid).encode("ascii"))
        except OSError as exc:
            _terminate(process)
            raise PiHostError(
                "pi_cleanup_unavailable",
                "Pi cleanup sentinel lost its process binding: %s" % exc,
                1,
            )
    _watch_runner_parent(process, env)
    if (
        env.get("KIMIFLOW_RUNNER_CONTROLLER") == "1"
        and "KIMIFLOW_PI_BRIDGE_BINDING" in env
        and threading.current_thread() is threading.main_thread()
    ):
        def terminate_on_signal(value, _frame):
            _kill_process_group(process.pid, signal.SIGKILL)
            os._exit(128 + value)

        signal.signal(signal.SIGTERM, terminate_on_signal)
        signal.signal(signal.SIGINT, terminate_on_signal)
    observed = session_id
    header_seen = False
    agent_active = False
    agent_end_seen = False
    agent_settled_seen = False
    current_agent_error = False
    last_agent_error = False
    output = sys.stdout if stdout is None else stdout
    assert process.stdout is not None
    for raw in _transport_lines(process):
        if len(raw.encode("utf-8")) > MAX_LINE:
            _terminate(process)
            raise PiHostError("pi_event_invalid", "Pi event is oversized", 2)
        try:
            event = json.loads(raw)
        except ValueError:
            _terminate(process)
            raise PiHostError("pi_event_invalid", "Pi emitted invalid JSON", 2)
        if not isinstance(event, dict):
            _terminate(process)
            raise PiHostError("pi_event_invalid", "Pi event is not an object", 2)
        event_type = event.get("type")
        if not header_seen:
            current = event.get("id")
            current_cwd = event.get("cwd")
            if (
                event_type != "session"
                or event.get("version") != PI_SESSION_VERSION
                or not isinstance(current, str)
                or model_adapter.SESSION_RE.fullmatch(current) is None
                or observed is not None and current != observed
                or not isinstance(current_cwd, str)
                or not os.path.isabs(current_cwd)
                or os.path.realpath(current_cwd) != os.path.realpath(payload["root"])
            ):
                _terminate(process)
                raise PiHostError(
                    "pi_session_mismatch",
                    "Pi's first JSON line is not the exact v3 session header",
                    2,
                )
            header_seen = True
            observed = current
            _transport_emit(
                {"schema_version": 1, "type": "session.started", "session_id": current},
                output,
                process,
            )
            continue
        if event_type == "session":
            _terminate(process)
            raise PiHostError("pi_session_duplicate", "Pi emitted two session headers", 2)
        if event_type == "agent_start":
            if agent_active or agent_settled_seen:
                _terminate(process)
                raise PiHostError("pi_lifecycle_invalid", "Pi agent lifecycle is invalid", 2)
            agent_active = True
            current_agent_error = False
        elif event_type == "agent_end":
            if not agent_active or agent_settled_seen:
                _terminate(process)
                raise PiHostError("pi_lifecycle_invalid", "Pi agent lifecycle is invalid", 2)
            agent_active = False
            agent_end_seen = True
            last_agent_error = current_agent_error
        elif event_type == "agent_settled":
            if agent_active or not agent_end_seen or agent_settled_seen:
                _terminate(process)
                raise PiHostError("pi_lifecycle_invalid", "Pi agent lifecycle is invalid", 2)
            agent_settled_seen = True
        elif event_type == "message_end":
            if not agent_active or agent_settled_seen:
                _terminate(process)
                raise PiHostError("pi_lifecycle_invalid", "Pi agent lifecycle is invalid", 2)
            text, stopped = _assistant_text(event)
            if stopped and agent_active:
                current_agent_error = True
            if text:
                _transport_emit(
                    {"schema_version": 1, "type": "message", "text": text},
                    output,
                    process,
                )
    returncode = process.wait()
    _terminate(process)
    if returncode != 0:
        raise PiHostError("provider_crash", "Pi exited with status %s" % returncode, 1)
    if not header_seen or observed is None:
        raise PiHostError("pi_session_missing", "Pi did not emit a session header", 2)
    if agent_active or not agent_end_seen or not agent_settled_seen:
        raise PiHostError(
            "pi_lifecycle_incomplete",
            "Pi exited before a complete agent_start/agent_end/agent_settled lifecycle",
            1,
        )
    if last_agent_error:
        raise PiHostError(
            "provider_crash",
            "Pi's final assistant message ended in an error or abort",
            1,
        )
    # This completes one provider transport turn only. The Kimiflow runner
    # remains the sole authority for worker/run completion.
    _transport_emit(
        {"schema_version": 1, "type": "turn.completed"},
        output,
        process,
    )
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) == 4 and args[0] == "_cleanup-sentinel":
            control_fd = int(args[1])
            ready_fd = int(args[2])
            return _cleanup_sentinel(control_fd, ready_fd, args[3])
        if (
            len(args) == 5
            and args[0] == "cleanup-lease"
            and args[1] == "--root"
            and args[3] == "--lease"
        ):
            _recover_cleanup_lease(os.path.realpath(args[2]), args[4])
            return 0
        if args == ["capabilities", "--json"]:
            _emit(capabilities())
            return 0
        if len(args) == 2 and args[0] in {"start", "resume"} and args[1] == "--json":
            payload = _payload()
            if payload["action"] != args[0]:
                raise PiHostError("pi_request_invalid", "Pi action does not match argv", 2)
            return run_turn(payload)
        raise PiHostError("usage", "usage: pi-host.sh capabilities|start|resume --json", 2)
    except PiHostError as exc:
        _emit({
            "schema_version": 1,
            "type": "turn.failed",
            "error_code": "provider_crash",
        })
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
