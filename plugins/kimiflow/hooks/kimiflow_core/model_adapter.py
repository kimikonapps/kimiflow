"""Provider-neutral transport boundary for tool-capable Kimiflow coding hosts."""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass


PROTOCOL_VERSION = 1
CAPABILITY_KEYS = ("files", "shell", "tests", "resume", "gates")
USAGE_KEYS = ("model_calls", "tool_calls", "input_tokens", "output_tokens")
MAX_CAPABILITIES_BYTES = 64 * 1024
CAPABILITIES_TIMEOUT_SECONDS = 10
IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


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
    return {"schema_version": 1, "name": name, "host": host, "capabilities": dict(capabilities)}


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

    def __init__(self, executable, model=None, environ=None, stderr=None):
        if not isinstance(executable, str) or not executable or os.path.basename(executable) != executable and not os.path.isabs(executable):
            raise AdapterError("adapter_command_invalid")
        self.executable = executable
        self.model = model
        self.environ = environ
        self.stderr = stderr or sys.stderr
        self._info = None

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
            except (OSError, ValueError, subprocess.TimeoutExpired):
                value = None
            self._info = validate_info(value)
        return dict(self._info)

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
        argv = [self.executable, action, "--json"]
        proc = None
        try:
            env = self._environment(info["host"], session_id)
            env["KIMIFLOW_RUNNER_CONTROLLER"] = "1"
            proc = subprocess.Popen(
                argv, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=None, text=True, bufsize=1,
            )
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.close()
        except OSError as exc:
            if proc is not None:
                proc.kill()
                proc.wait()
            return TurnResult(returncode=127, error_code="spawn_failed:%s" % exc.__class__.__name__)
        observed = session_id or ""
        failed = ""
        usage = None
        completed = False
        try:
            for raw in proc.stdout:
                try:
                    event = json.loads(raw)
                except ValueError:
                    failed = "invalid_jsonl"
                    continue
                if not isinstance(event, dict):
                    failed = "invalid_event"
                    continue
                if event.get("type") == "session.started" and isinstance(event.get("session_id"), str):
                    observed = event["session_id"]
                    on_session(observed)
                elif event.get("type") in ("turn.failed", "error"):
                    failed = str(event.get("type"))
                elif event.get("type") == "message" and isinstance(event.get("text"), str):
                    self.stderr.write(event["text"].rstrip() + "\n")
                elif event.get("type") == "turn.completed":
                    if completed:
                        failed = "duplicate_turn_completed"
                    completed = True
                    raw_usage = event.get("usage")
                    usage = normalize_usage(raw_usage)
                    if raw_usage is not None and usage is None:
                        failed = "invalid_usage"
                elif event.get("type") not in ("session.started", "turn.failed", "error"):
                    failed = "invalid_event"
        except BaseException:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
        finally:
            proc.stdout.close()
        returncode = proc.wait()
        if returncode == 0 and not completed and not failed:
            failed = "missing_turn_completed"
        if returncode == 0 and failed:
            returncode = 1
        return TurnResult(returncode=returncode, session_id=observed, error_code=failed, usage=usage)

    def start(self, root, prompt, on_session):
        return self._invoke("start", root, "", prompt, on_session)

    def resume(self, root, session_id, prompt, on_session):
        return self._invoke("resume", root, session_id, prompt, on_session)
