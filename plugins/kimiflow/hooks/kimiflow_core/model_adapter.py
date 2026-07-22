"""Provider-neutral transport boundary for tool-capable Kimiflow coding hosts."""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass


PROTOCOL_VERSION = 1
CAPABILITY_KEYS = ("files", "shell", "tests", "resume", "gates")
FEATURE_KEYS = ("workflow_context", "model_roles", "structured_events", "root_confinement")
MODEL_ROLE_KEYS = ("top", "balanced", "cheap", "cross_family_top")
USAGE_KEYS = ("model_calls", "tool_calls", "input_tokens", "output_tokens")
MAX_CAPABILITIES_BYTES = 64 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_EVENT_TEXT = 64 * 1024
MAX_EVENT_STREAM_BYTES = 16 * 1024 * 1024
MAX_EVENTS_PER_TURN = 10_000
MAX_DURATION_MS = 24 * 60 * 60 * 1000
MAX_PROGRESS_VALUE = 1_000_000_000
MAX_TOKEN_COUNT = 10_000_000_000
CAPABILITIES_TIMEOUT_SECONDS = 10
DEFAULT_TURN_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_TURN_TIMEOUT_SECONDS = 24 * 60 * 60
IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class AdapterError(ValueError):
    pass


@dataclass(init=False)
class TurnResult:
    returncode: int
    session_id: str = ""
    error_code: str = ""
    usage: dict = None

    def __init__(self, returncode, session_id="", error_code="", usage=None, thread_id=""):
        self.returncode = returncode
        self.session_id = session_id or thread_id
        self.error_code = error_code
        self.usage = usage

    @property
    def thread_id(self):
        return self.session_id


def validate_info(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise AdapterError("adapter_info_invalid")
    name = value.get("name")
    host = value.get("host")
    capabilities = value.get("capabilities")
    if (
        not isinstance(name, str)
        or not IDENTITY_RE.fullmatch(name)
        or not isinstance(host, str)
        or not IDENTITY_RE.fullmatch(host)
    ):
        raise AdapterError("adapter_identity_invalid")
    if not isinstance(capabilities, dict) or set(capabilities) != set(CAPABILITY_KEYS):
        raise AdapterError("adapter_capabilities_invalid")
    missing = [key for key in CAPABILITY_KEYS if capabilities.get(key) is not True]
    if missing:
        raise AdapterError("adapter_capabilities_missing:%s" % ",".join(missing))
    result = {"schema_version": 1, "name": name, "host": host, "capabilities": dict(capabilities)}
    if "features" in value:
        features = value.get("features")
        if (
            not isinstance(features, dict)
            or any(key not in FEATURE_KEYS for key in features)
            or any(not isinstance(item, bool) for item in features.values())
        ):
            raise AdapterError("adapter_features_invalid")
        result["features"] = {key: features[key] for key in FEATURE_KEYS if key in features}
    return result


def normalize_model_roles(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or any(key not in MODEL_ROLE_KEYS for key in value):
        raise AdapterError("model_roles_invalid")
    result = {}
    for key in MODEL_ROLE_KEYS:
        if key not in value:
            continue
        model = value[key]
        if (
            not isinstance(model, str)
            or not model
            or len(model) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in model)
        ):
            raise AdapterError("model_role_invalid:%s" % key)
        result[key] = model
    return result


def normalize_model(value):
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise AdapterError("model_invalid")
    return value


def workflow_context():
    plugin_root = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    descriptor = {
        "schema_version": 1,
        "name": "kimiflow",
        "plugin_root": plugin_root,
        "skill": "SKILL.md",
        "phase_manifest": "phases/PHASES.json",
        "run_bridge": "hooks/run-bridge.sh",
    }
    for key in ("skill", "phase_manifest", "run_bridge"):
        target = os.path.realpath(os.path.join(plugin_root, descriptor[key]))
        if os.path.commonpath((plugin_root, target)) != plugin_root or not os.path.isfile(target):
            raise AdapterError("workflow_context_invalid:%s" % key)
    return descriptor


def _event_text(value, maximum=MAX_EVENT_TEXT, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value) or len(value) > maximum:
        raise AdapterError("invalid_event")
    return value


def _event_integer(value, maximum=MAX_DURATION_MS):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise AdapterError("invalid_event")
    return value


def normalize_event(value, structured=False):
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise AdapterError("invalid_event")
    event_type = value["type"]
    if event_type == "session.started":
        session_id = value.get("session_id")
        if not isinstance(session_id, str) or not SESSION_RE.fullmatch(session_id):
            raise AdapterError("invalid_event")
        return {"type": event_type, "session_id": session_id}
    if event_type == "message":
        return {"type": event_type, "text": _event_text(value.get("text"))}
    if event_type in ("turn.failed", "error"):
        return {"type": event_type}
    if event_type == "turn.completed":
        result = {"type": event_type}
        if "usage" in value:
            usage = normalize_usage(value.get("usage"))
            if usage is None:
                raise AdapterError("invalid_usage")
            result["usage"] = usage
        return result
    if not structured:
        raise AdapterError("invalid_event")
    if event_type == "phase.changed":
        phase = value.get("phase")
        status = value.get("status")
        if isinstance(phase, bool) or not isinstance(phase, int) or not 0 <= phase <= 7:
            raise AdapterError("invalid_event")
        if status not in ("started", "completed", "blocked"):
            raise AdapterError("invalid_event")
        result = {"type": event_type, "phase": phase, "status": status}
        if "label" in value:
            result["label"] = _event_text(value.get("label"), 240)
        return result
    if event_type == "progress":
        current = _event_integer(value.get("current"), MAX_PROGRESS_VALUE)
        result = {"type": event_type, "current": current}
        if "total" in value:
            total = _event_integer(value.get("total"), MAX_PROGRESS_VALUE)
            if total < current:
                raise AdapterError("invalid_event")
            result["total"] = total
        if "label" in value:
            result["label"] = _event_text(value.get("label"), 240)
        return result
    if event_type in ("tool.started", "tool.completed"):
        tool = value.get("tool")
        if not isinstance(tool, str) or not IDENTITY_RE.fullmatch(tool):
            raise AdapterError("invalid_event")
        result = {"type": event_type, "tool": tool}
        if event_type == "tool.completed":
            if value.get("status") not in ("passed", "failed", "cancelled"):
                raise AdapterError("invalid_event")
            result["status"] = value["status"]
        if "duration_ms" in value:
            result["duration_ms"] = _event_integer(value.get("duration_ms"))
        if "label" in value:
            result["label"] = _event_text(value.get("label"), 240)
        return result
    if event_type == "test.completed":
        if value.get("status") not in ("passed", "failed", "skipped"):
            raise AdapterError("invalid_event")
        result = {
            "type": event_type,
            "name": _event_text(value.get("name"), 240),
            "status": value["status"],
        }
        if "duration_ms" in value:
            result["duration_ms"] = _event_integer(value.get("duration_ms"))
        return result
    if event_type == "user_input.requested":
        kind = value.get("kind")
        if not isinstance(kind, str) or not IDENTITY_RE.fullmatch(kind):
            raise AdapterError("invalid_event")
        return {
            "type": event_type,
            "kind": kind,
            "summary": _event_text(value.get("summary"), 500),
        }
    if event_type == "context.compacted":
        before = _event_integer(value.get("before_tokens"), MAX_TOKEN_COUNT)
        after = _event_integer(value.get("after_tokens"), MAX_TOKEN_COUNT)
        if after > before:
            raise AdapterError("invalid_event")
        return {"type": event_type, "before_tokens": before, "after_tokens": after}
    raise AdapterError("invalid_event")


def _bounded_binary_lines(stream):
    count = 0
    total = 0
    while True:
        raw = stream.readline(MAX_EVENT_BYTES + 1)
        if not raw:
            return
        count += 1
        total += len(raw)
        if len(raw) > MAX_EVENT_BYTES:
            yield None, "event_too_large"
            return
        if count > MAX_EVENTS_PER_TURN or total > MAX_EVENT_STREAM_BYTES:
            yield None, "event_stream_too_large"
            return
        try:
            yield raw.decode("utf-8"), None
        except UnicodeDecodeError:
            yield "", None


def _signal_process_group(process, value):
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, value)
            return
        except OSError:
            pass
    try:
        process.send_signal(value)
    except OSError:
        pass


def _stop_process(process):
    _signal_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        process.wait()
    else:
        # The group may still contain descendants that inherited our pipes.
        _signal_process_group(process, signal.SIGKILL)


def info_for(adapter):
    if hasattr(adapter, "info"):
        return validate_info(adapter.info())
    # Test/backward-compatible injected adapters historically represented Codex.
    return validate_info({
        "schema_version": 1,
        "name": "codex-exec",
        "host": "codex",
        "capabilities": {key: True for key in CAPABILITY_KEYS},
    })


def normalize_usage(value):
    if not isinstance(value, dict):
        return None
    aliases = {
        "model_calls": ("model_calls", "modelCalls"),
        "tool_calls": ("tool_calls", "toolCalls"),
        "input_tokens": ("input_tokens", "inputTokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "outputTokens", "completion_tokens"),
    }
    result = {}
    for key, names in aliases.items():
        found = None
        for name in names:
            if name in value:
                found = value[name]
                break
        if isinstance(found, bool) or not isinstance(found, int) or found < 0:
            return None
        result[key] = found
    return result


def add_usage(total, delta):
    if delta is None:
        return total
    if total is None:
        return dict(delta)
    return {key: total[key] + delta[key] for key in USAGE_KEYS}


class CodexExecAdapter:
    def __init__(self, codex="codex", environ=None, stderr=None):
        self.codex = codex
        self.environ = environ
        self.stderr = stderr or sys.stderr

    def info(self):
        return {
            "schema_version": 1,
            "name": "codex-exec",
            "host": "codex",
            "capabilities": {key: True for key in CAPABILITY_KEYS},
        }

    def child_environment(self, source=None):
        env = dict(os.environ if source is None else source)
        for key in ("CODEX_THREAD_ID", "KIMIFLOW_SESSION_ID", "KIMIFLOW_SESSION_HOST"):
            env.pop(key, None)
        env["KIMIFLOW_HOST"] = "codex"
        env["KIMIFLOW_RUNNER_CONTROLLER"] = "1"
        return env

    def start_argv(self, root, prompt):
        return [
            self.codex, "exec", "--json", "--sandbox", "workspace-write", "-C", root,
            "-c", 'approval_policy="never"', "--", prompt,
        ]

    def resume_argv(self, session_id, prompt):
        return [
            self.codex, "exec", "resume", "--json", "-c", 'approval_policy="never"',
            "-c", 'sandbox_mode="workspace-write"', "--", session_id, prompt,
        ]

    def _invoke(self, argv, root, on_session):
        try:
            process = subprocess.Popen(
                argv, cwd=root, env=self.child_environment(self.environ), stdout=subprocess.PIPE,
                stderr=None, text=True, bufsize=1,
            )
        except OSError as exc:
            return TurnResult(returncode=127, error_code="spawn_failed:%s" % exc.__class__.__name__)
        session_id = ""
        failed_event = ""
        usage = None
        try:
            for raw in process.stdout:
                try:
                    event = json.loads(raw)
                except (TypeError, ValueError):
                    failed_event = "invalid_jsonl"
                    continue
                if not isinstance(event, dict):
                    failed_event = "invalid_event"
                    continue
                if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                    session_id = event["thread_id"]
                    on_session(session_id)
                elif event.get("type") in ("turn.failed", "error"):
                    failed_event = str(event.get("type"))
                elif event.get("type") == "item.completed":
                    item = event.get("item")
                    if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                        self.stderr.write(item["text"].rstrip() + "\n")
                candidate = event.get("usage")
                if candidate is None and isinstance(event.get("turn"), dict):
                    candidate = event["turn"].get("usage")
                usage = add_usage(usage, normalize_usage(candidate))
            returncode = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        if returncode == 0 and failed_event:
            returncode = 1
        return TurnResult(returncode=returncode, session_id=session_id, error_code=failed_event, usage=usage)

    def start(self, root, prompt, on_session):
        self.stderr.write("kimiflow: starting Codex headless turn\n")
        return self._invoke(self.start_argv(root, prompt), root, on_session)

    def resume(self, root, session_id, prompt, on_session):
        self.stderr.write("kimiflow: continuing Codex thread %s\n" % session_id)
        return self._invoke(self.resume_argv(session_id, prompt), root, on_session)


class CommandAgentAdapter:
    """JSON-stdio bridge for an existing tool-capable local/remote coding-agent harness."""

    def __init__(
        self, executable, model=None, model_roles=None, required_features=None,
        event_sink=None, environ=None, stderr=None,
    ):
        if not isinstance(executable, str) or not executable or os.path.basename(executable) != executable and not os.path.isabs(executable):
            raise AdapterError("adapter_command_invalid")
        self.executable = executable
        self.model = normalize_model(model)
        self.model_roles = normalize_model_roles(model_roles)
        required = tuple(required_features or ())
        if any(feature not in FEATURE_KEYS for feature in required) or len(set(required)) != len(required):
            raise AdapterError("adapter_required_features_invalid")
        self.required_features = required
        self.event_sink = event_sink
        self.environ = environ
        self.stderr = stderr or sys.stderr
        self._info = None
        timeout_value = (os.environ if environ is None else environ).get(
            "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS", str(DEFAULT_TURN_TIMEOUT_SECONDS),
        )
        try:
            self.turn_timeout_seconds = int(timeout_value)
        except (TypeError, ValueError):
            raise AdapterError("adapter_turn_timeout_invalid")
        if not 1 <= self.turn_timeout_seconds <= MAX_TURN_TIMEOUT_SECONDS:
            raise AdapterError("adapter_turn_timeout_invalid")

    def _environment(self, host=None, session_id=None):
        env = dict(os.environ if self.environ is None else self.environ)
        for key in ("CODEX_THREAD_ID", "KIMIFLOW_SESSION_ID", "KIMIFLOW_SESSION_HOST"):
            env.pop(key, None)
        if host:
            env["KIMIFLOW_HOST"] = host
        if session_id:
            env["KIMIFLOW_SESSION_ID"] = session_id
        return env

    def info(self):
        if self._info is None:
            try:
                proc = subprocess.run(
                    [self.executable, "capabilities", "--json"], env=self._environment(),
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
                    timeout=CAPABILITIES_TIMEOUT_SECONDS,
                )
                value = (
                    json.loads(proc.stdout)
                    if proc.returncode == 0 and len(proc.stdout.encode("utf-8")) <= MAX_CAPABILITIES_BYTES
                    else None
                )
            except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
                value = None
            validated = validate_info(value)
            features = validated.get("features", {})
            missing = [key for key in self.required_features if features.get(key) is not True]
            if missing:
                raise AdapterError("adapter_features_missing:%s" % ",".join(missing))
            if self.model_roles and features.get("model_roles") is not True:
                raise AdapterError("adapter_features_missing:model_roles")
            self._info = validated
        return dict(self._info)

    def contract_fingerprint(self):
        info = self.info()
        features = info.get("features", {})
        if not features and not self.required_features and not self.model_roles:
            return None
        material = {
            "schema_version": PROTOCOL_VERSION,
            "adapter": info["name"],
            "host": info["host"],
            "features": features,
            "required_features": sorted(self.required_features),
            "model": self.model,
            "model_roles": self.model_roles,
        }
        search_path = (os.environ if self.environ is None else self.environ).get("PATH")
        resolved = shutil.which(self.executable, path=search_path)
        material["adapter_command"] = os.path.realpath(resolved or self.executable)
        payload = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:%s" % hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _invoke(self, action, root, session_id, prompt, on_session):
        info = self.info()
        payload = {
            "schema_version": PROTOCOL_VERSION,
            "action": action,
            "root": root,
            "session_id": session_id or None,
            "host": info["host"],
            "adapter": info["name"],
            "prompt": prompt,
            "model": self.model,
            "required_capabilities": list(CAPABILITY_KEYS),
        }
        features = info.get("features", {})
        if features.get("workflow_context") is True:
            payload["workflow_context"] = workflow_context()
        if self.model_roles:
            payload["model_routing"] = {"roles": dict(self.model_roles)}
            if self.model is not None:
                payload["model_routing"]["default_model"] = self.model
        argv = [self.executable, action, "--json"]
        proc = None
        timer = None
        timed_out = threading.Event()

        def expire_turn():
            if proc is not None and proc.poll() is None:
                timed_out.set()
                _signal_process_group(proc, signal.SIGKILL)

        try:
            env = self._environment(info["host"], session_id)
            env["KIMIFLOW_RUNNER_CONTROLLER"] = "1"
            proc = subprocess.Popen(
                argv, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=None, bufsize=0, start_new_session=True,
            )
            timer = threading.Timer(self.turn_timeout_seconds, expire_turn)
            timer.daemon = True
            timer.start()
            proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            proc.stdin.close()
        except OSError as exc:
            if timer is not None:
                timer.cancel()
            if proc is not None:
                _stop_process(proc)
                for stream in (proc.stdin, proc.stdout):
                    if stream is not None and not stream.closed:
                        try:
                            stream.close()
                        except OSError:
                            pass
            if timed_out.is_set():
                return TurnResult(returncode=1, error_code="turn_timeout")
            return TurnResult(returncode=127, error_code="spawn_failed:%s" % exc.__class__.__name__)
        observed = session_id or ""
        failed = ""
        usage = None
        completed = False
        completion_event = None
        try:
            for raw, read_error in _bounded_binary_lines(proc.stdout):
                if read_error:
                    failed = read_error
                    _stop_process(proc)
                    break
                try:
                    event = json.loads(raw)
                except ValueError:
                    failed = "invalid_jsonl"
                    continue
                try:
                    public = normalize_event(
                        event, structured=features.get("structured_events") is True,
                    )
                except AdapterError as exc:
                    failed = str(exc)
                    continue
                event_type = public["type"]
                if event_type == "session.started":
                    observed = public["session_id"]
                    on_session(observed)
                elif event_type in ("turn.failed", "error"):
                    failed = event_type
                elif event_type == "message" and self.event_sink is None:
                    self.stderr.write(public["text"].rstrip() + "\n")
                elif event_type == "turn.completed":
                    if completed:
                        failed = "duplicate_turn_completed"
                    completed = True
                    usage = public.get("usage")
                    completion_event = public
                if self.event_sink is not None and event_type != "turn.completed":
                    try:
                        self.event_sink({"schema_version": PROTOCOL_VERSION, **public})
                    except (BrokenPipeError, OSError):
                        failed = "event_sink_failed"
                        _stop_process(proc)
                        break
        except BaseException:
            _stop_process(proc)
            raise
        finally:
            timer.cancel()
            proc.stdout.close()
        returncode = proc.wait()
        if timed_out.is_set():
            failed = "turn_timeout"
            returncode = 1
        if returncode == 0 and not completed and not failed:
            failed = "missing_turn_completed"
        if returncode == 0 and failed:
            returncode = 1
        if returncode == 0 and not failed and self.event_sink is not None:
            try:
                self.event_sink({"schema_version": PROTOCOL_VERSION, **completion_event})
            except (BrokenPipeError, OSError):
                failed = "event_sink_failed"
                returncode = 1
        return TurnResult(returncode=returncode, session_id=observed, error_code=failed, usage=usage)

    def start(self, root, prompt, on_session):
        return self._invoke("start", root, "", prompt, on_session)

    def resume(self, root, session_id, prompt, on_session):
        return self._invoke("resume", root, session_id, prompt, on_session)
