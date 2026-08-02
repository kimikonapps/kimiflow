"""Provider-neutral transport boundary for tool-capable Kimiflow coding hosts."""

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass


PROTOCOL_VERSION = 1
CAPABILITY_KEYS = ("files", "shell", "tests", "resume", "gates")
FEATURE_KEYS = (
    "workflow_context", "model_roles", "structured_events", "root_confinement",
    "context_rollover", "adaptive_model_routes", "adaptive_execution_profiles",
    "work_unit_policy",
)
MODEL_ROLE_KEYS = ("top", "balanced", "cheap", "cross_family_top")
USAGE_KEYS = ("model_calls", "tool_calls", "input_tokens", "output_tokens")
USAGE_V2_COUNTER_KEYS = (
    "model_calls", "tool_calls", "uncached_input_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "logical_input_tokens", "output_tokens",
    "active_context_tokens", "peak_context_tokens", "max_input_tokens",
)
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
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TURN_ID_RE = re.compile(r"^turn_[A-Za-z0-9._:-]{1,96}$")
PROVIDER_ERROR_CODES = (
    "turn_cancelled", "refusal", "quota_exceeded", "turn_timeout", "provider_crash",
)
WORK_UNIT_POLICY_KEYS = (
    "schema_version", "unit_kind", "context_scope", "filesystem_access",
    "allowed_tools", "settings_sources", "mcp_servers", "hooks", "input_digest",
)
WORK_UNIT_KINDS = (
    "research", "review", "solution_candidate", "solution_selector",
)
READ_ONLY_WORK_UNIT_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")


class AdapterError(ValueError):
    pass


def validate_work_unit_policy(value):
    """Validate the exact provider-facing isolation contract."""
    if not isinstance(value, dict) or set(value) != set(WORK_UNIT_POLICY_KEYS):
        raise AdapterError("work_unit_policy_invalid")
    if (
        value.get("schema_version") != 1
        or value.get("unit_kind") not in WORK_UNIT_KINDS
        or value.get("context_scope") not in ("project_root", "sealed_input")
        or value.get("filesystem_access") not in ("read_only", "none")
        or value.get("hooks") is not False
        or not isinstance(value.get("input_digest"), str)
        or DIGEST_RE.fullmatch(value["input_digest"]) is None
    ):
        raise AdapterError("work_unit_policy_invalid")
    for key in ("allowed_tools", "settings_sources", "mcp_servers"):
        items = value.get(key)
        if (
            not isinstance(items, list)
            or len(items) != len(set(items))
            or any(
                not isinstance(item, str)
                or not item
                or len(item) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in item)
                for item in items
            )
        ):
            raise AdapterError("work_unit_policy_invalid")
    if value["settings_sources"] or value["mcp_servers"]:
        raise AdapterError("work_unit_policy_invalid")
    if value["context_scope"] == "sealed_input" and (
        value["filesystem_access"] != "none" or value["allowed_tools"]
    ):
        raise AdapterError("work_unit_policy_invalid")
    if value["context_scope"] == "project_root" and value["filesystem_access"] != "read_only":
        raise AdapterError("work_unit_policy_invalid")
    if value["unit_kind"] in ("solution_candidate", "solution_selector"):
        if value["context_scope"] != "sealed_input" or value["allowed_tools"]:
            raise AdapterError("work_unit_policy_invalid")
    elif (
        value["context_scope"] != "project_root"
        or any(tool not in READ_ONLY_WORK_UNIT_TOOLS for tool in value["allowed_tools"])
    ):
        raise AdapterError("work_unit_policy_invalid")
    return {key: value[key] for key in WORK_UNIT_POLICY_KEYS}


def normalize_provider_error(value=None, returncode=1, cancelled=False, timed_out=False):
    """Collapse provider-specific failures to the public five-code matrix."""
    if cancelled:
        return "turn_cancelled"
    if timed_out:
        return "turn_timeout"
    if isinstance(value, dict):
        explicit = value.get("error_code") or value.get("code") or value.get("subtype")
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        explicit = value
        text = "" if value is None else str(value)
    if explicit in PROVIDER_ERROR_CODES:
        return explicit
    if not explicit and not text and returncode == 0:
        return ""
    folded = ("%s %s" % (explicit or "", text)).lower()
    if any(token in folded for token in ("cancelled", "canceled", "interrupt")):
        return "turn_cancelled"
    if any(token in folded for token in ("refusal", "refused", "policy_reject")):
        return "refusal"
    if any(token in folded for token in (
        "quota", "rate_limit", "rate limit", "usage limit", "credit",
        "billing", "budget",
    )):
        return "quota_exceeded"
    if any(token in folded for token in ("timeout", "timed out")):
        return "turn_timeout"
    if (
        isinstance(value, dict)
        or explicit in ("turn.failed", "error")
        or (returncode != 0 and not explicit)
    ):
        return "provider_crash"
    # Validation/stream/controller errors are part of the existing adapter
    # contract and remain distinguishable from provider failures.
    return str(explicit) if explicit else ""


@dataclass(init=False)
class TurnResult:
    returncode: int
    session_id: str = ""
    error_code: str = ""
    usage: dict = None
    context_compaction: dict = None
    model_route: dict = None
    usage_v2: dict = None
    output: object = None

    def __init__(
        self, returncode, session_id="", error_code="", usage=None, thread_id="",
        context_compaction=None, model_route=None, usage_v2=None, output=None,
        diagnostic_code="",
    ):
        self.returncode = returncode
        self.session_id = session_id or thread_id
        self.error_code = error_code
        self.usage = usage
        self.context_compaction = context_compaction
        self.model_route = model_route
        self.usage_v2 = usage_v2
        self.output = output
        self.diagnostic_code = diagnostic_code

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
        if (
            features.get("context_rollover") is True
            and features.get("structured_events") is not True
        ):
            raise AdapterError(
                "adapter_features_invalid:context_rollover_requires_structured_events"
            )
        if (
            features.get("adaptive_model_routes") is True
            and features.get("model_roles") is not True
        ):
            raise AdapterError(
                "adapter_features_invalid:adaptive_model_routes_requires_model_roles"
            )
        result["features"] = {key: features[key] for key in FEATURE_KEYS if key in features}
    profile = value.get("execution_profile")
    profile_enabled = result.get("features", {}).get("adaptive_execution_profiles") is True
    if profile_enabled:
        result["execution_profile"] = normalize_execution_profile(profile)
    elif profile is not None:
        raise AdapterError("adapter_execution_profile_without_feature")
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


def _bounded_token_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TOKEN_COUNT:
        raise AdapterError("execution_profile_invalid")
    return value


def normalize_execution_profile(value):
    required = {
        "schema_version", "model_fingerprint", "max_input_tokens", "max_output_tokens",
        "execution_variants", "controls",
    }
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
        raise AdapterError("execution_profile_invalid")
    fingerprint = value.get("model_fingerprint")
    if not isinstance(fingerprint, str) or DIGEST_RE.fullmatch(fingerprint) is None:
        raise AdapterError("execution_profile_invalid")
    max_input = _bounded_token_count(value.get("max_input_tokens"))
    max_output = _bounded_token_count(value.get("max_output_tokens"))
    variants = value.get("execution_variants")
    if not isinstance(variants, list) or not 1 <= len(variants) <= 32:
        raise AdapterError("execution_profile_invalid")
    normalized_variants = []
    seen = set()
    default_count = 0
    for variant in variants:
        allowed = {"id", "default", "cost_rank", "depth_rank"}
        if not isinstance(variant, dict) or set(variant) - allowed or set(variant) < {"id", "default"}:
            raise AdapterError("execution_profile_invalid")
        variant_id = variant.get("id")
        is_default = variant.get("default")
        if (
            not isinstance(variant_id, str)
            or IDENTITY_RE.fullmatch(variant_id) is None
            or variant_id in seen
            or not isinstance(is_default, bool)
        ):
            raise AdapterError("execution_profile_invalid")
        row = {"id": variant_id, "default": is_default}
        for key in ("cost_rank", "depth_rank"):
            if key in variant:
                rank = variant[key]
                if isinstance(rank, bool) or not isinstance(rank, int) or not 0 <= rank <= 100:
                    raise AdapterError("execution_profile_invalid")
                row[key] = rank
        seen.add(variant_id)
        default_count += int(is_default)
        normalized_variants.append(row)
    if default_count != 1:
        raise AdapterError("execution_profile_invalid")
    controls = value.get("controls")
    control_keys = {
        "thinking", "task_budget", "prompt_cache", "compaction", "structured_failures",
    }
    if not isinstance(controls, dict) or set(controls) != control_keys:
        raise AdapterError("execution_profile_invalid")
    if controls.get("thinking") not in (
        "unavailable", "fixed_on", "fixed_off", "selectable", "adaptive_default",
    ) or any(not isinstance(controls.get(key), bool) for key in control_keys - {"thinking"}):
        raise AdapterError("execution_profile_invalid")
    return {
        "schema_version": 1,
        "model_fingerprint": fingerprint,
        "max_input_tokens": max_input,
        "max_output_tokens": max_output,
        "execution_variants": normalized_variants,
        "controls": {key: controls[key] for key in sorted(control_keys)},
    }


def execution_profile_fingerprint(value):
    profile = normalize_execution_profile(value)
    payload = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:%s" % hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_execution_variant(value):
    profile = normalize_execution_profile(value)
    return next(row["id"] for row in profile["execution_variants"] if row["default"])


def runtime_fingerprint():
    plugin_root = os.path.realpath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    candidates = (
        os.path.join(plugin_root, "RUNTIME-FINGERPRINT.json"),
        os.path.join(plugin_root, "plugins", "kimiflow", "RUNTIME-FINGERPRINT.json"),
    )
    for path in candidates:
        try:
            info = os.stat(path, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                continue
            with open(path, "rb") as handle:
                payload = handle.read(MAX_CAPABILITIES_BYTES + 1)
            value = json.loads(payload.decode("utf-8"))
            fingerprint = value.get("runtime_fingerprint")
            if (
                len(payload) <= MAX_CAPABILITIES_BYTES
                and isinstance(fingerprint, str)
                and DIGEST_RE.fullmatch(fingerprint) is not None
            ):
                return fingerprint
        except (OSError, UnicodeError, ValueError, AttributeError):
            continue
    with open(__file__, "rb") as handle:
        return "sha256:" + hashlib.sha256(handle.read()).hexdigest()


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
        result = {"type": event_type}
        if "error_code" in value:
            if value.get("error_code") not in PROVIDER_ERROR_CODES:
                raise AdapterError("invalid_event")
            result["error_code"] = value["error_code"]
        if "diagnostic_code" in value:
            diagnostic = value.get("diagnostic_code")
            if not isinstance(diagnostic, str) or IDENTITY_RE.fullmatch(diagnostic) is None:
                raise AdapterError("invalid_event")
            result["diagnostic_code"] = diagnostic
        return result
    if event_type == "turn.completed":
        result = {"type": event_type}
        if "usage" in value:
            usage = normalize_usage(value.get("usage"))
            if usage is None:
                raise AdapterError("invalid_usage")
            result["usage"] = usage
        if "usage_v2" in value:
            usage_v2 = normalize_usage_v2(value.get("usage_v2"))
            result["usage_v2"] = usage_v2
            derived = legacy_usage_from_v2(usage_v2)
            if "usage" in result and derived is not None and result["usage"] != derived:
                raise AdapterError("invalid_usage_v2:legacy_mismatch")
            if "usage" not in result and derived is not None:
                result["usage"] = derived
        if "model_route" in value:
            route = value.get("model_route")
            if (
                not isinstance(route, dict)
                or set(route) != {"role", "model", "baseline"}
                or route.get("role") not in ("balanced", "cheap")
            ):
                raise AdapterError("invalid_model_route")
            result["model_route"] = {
                "role": route["role"],
                "model": normalize_model(route.get("model")),
                "baseline": normalize_model(route.get("baseline")),
            }
            if (
                result["model_route"]["model"] is None
                or result["model_route"]["baseline"] is None
                or result["model_route"]["model"] == result["model_route"]["baseline"]
            ):
                raise AdapterError("invalid_model_route")
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
        rollover_id = value.get("rollover_id")
        current_digest = value.get("current_digest")
        if rollover_id is None and current_digest is None:
            return {
                "type": event_type,
                "before_tokens": before,
                "after_tokens": after,
            }
        if (
            not isinstance(rollover_id, str)
            or re.fullmatch(r"roll_[0-9a-f]{32}", rollover_id) is None
            or not isinstance(current_digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", current_digest) is None
        ):
            raise AdapterError("invalid_event")
        return {
            "type": event_type,
            "rollover_id": rollover_id,
            "current_digest": current_digest,
            "before_tokens": before,
            "after_tokens": after,
        }
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


def normalize_usage_v2(value):
    required = {
        "schema_version", "turn_id", "session_id", "model_fingerprint",
        "execution_variant", *USAGE_V2_COUNTER_KEYS,
    }
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 2:
        raise AdapterError("invalid_usage_v2")
    if (
        not isinstance(value.get("turn_id"), str)
        or TURN_ID_RE.fullmatch(value["turn_id"]) is None
        or not isinstance(value.get("session_id"), str)
        or SESSION_RE.fullmatch(value["session_id"]) is None
        or not isinstance(value.get("model_fingerprint"), str)
        or DIGEST_RE.fullmatch(value["model_fingerprint"]) is None
        or not isinstance(value.get("execution_variant"), str)
        or IDENTITY_RE.fullmatch(value["execution_variant"]) is None
    ):
        raise AdapterError("invalid_usage_v2")
    normalized = {
        "schema_version": 2,
        "turn_id": value["turn_id"],
        "session_id": value["session_id"],
        "model_fingerprint": value["model_fingerprint"],
        "execution_variant": value["execution_variant"],
    }
    available = True
    for key in USAGE_V2_COUNTER_KEYS:
        item = value.get(key)
        if item == "unavailable":
            available = False
        elif isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_TOKEN_COUNT:
            raise AdapterError("invalid_usage_v2")
        normalized[key] = item
    normalized["status"] = "available" if available else "unavailable"
    if available:
        if normalized["logical_input_tokens"] != (
            normalized["uncached_input_tokens"]
            + normalized["cache_read_input_tokens"]
            + normalized["cache_creation_input_tokens"]
        ):
            raise AdapterError("invalid_usage_v2:logical_input_mismatch")
        if not (
            normalized["active_context_tokens"]
            <= normalized["peak_context_tokens"]
            <= normalized["max_input_tokens"]
        ):
            raise AdapterError("invalid_usage_v2:context_bounds")
    return normalized


def legacy_usage_from_v2(value):
    normalized = normalize_usage_v2({
        key: item for key, item in value.items() if key != "status"
    }) if value.get("status") in ("available", "unavailable") else normalize_usage_v2(value)
    if normalized["status"] != "available":
        return None
    return {
        "model_calls": normalized["model_calls"],
        "tool_calls": normalized["tool_calls"],
        "input_tokens": normalized["logical_input_tokens"],
        "output_tokens": normalized["output_tokens"],
    }


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
        self._process = None
        self._process_lock = threading.Lock()
        self._cancelled = threading.Event()
        timeout_value = (os.environ if environ is None else environ).get(
            "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS", str(DEFAULT_TURN_TIMEOUT_SECONDS),
        )
        try:
            self.turn_timeout_seconds = int(timeout_value)
        except (TypeError, ValueError):
            raise AdapterError("adapter_turn_timeout_invalid")
        if not 1 <= self.turn_timeout_seconds <= MAX_TURN_TIMEOUT_SECONDS:
            raise AdapterError("adapter_turn_timeout_invalid")

    def info(self):
        return {
            "schema_version": 1,
            "name": "codex-exec",
            "host": "codex",
            "capabilities": {key: True for key in CAPABILITY_KEYS},
            "features": {"work_unit_policy": True},
        }

    def child_environment(self, source=None):
        env = dict(os.environ if source is None else source)
        for key in ("CODEX_THREAD_ID", "KIMIFLOW_SESSION_ID", "KIMIFLOW_SESSION_HOST"):
            env.pop(key, None)
        env["KIMIFLOW_HOST"] = "codex"
        env["KIMIFLOW_RUNNER_CONTROLLER"] = "1"
        return env

    @contextmanager
    def sealed_runtime(self):
        """Create a content-empty Codex home/workspace with auth only."""
        source = dict(os.environ if self.environ is None else self.environ)
        keep = {
            "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP",
            "SSL_CERT_FILE", "SSL_CERT_DIR",
            "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY",
            "https_proxy", "http_proxy", "all_proxy", "no_proxy",
            "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN",
            "OPENAI_ORGANIZATION", "OPENAI_ORG_ID",
            "OPENAI_PROJECT", "OPENAI_PROJECT_ID",
        }
        env = {
            key: value for key, value in source.items()
            if key in keep and isinstance(value, str)
        }
        env["KIMIFLOW_HOST"] = "codex"
        env["KIMIFLOW_RUNNER_CONTROLLER"] = "1"
        with tempfile.TemporaryDirectory(
            prefix="kimiflow-codex-sealed-",
        ) as temporary:
            home = os.path.join(temporary, "home")
            workspace = os.path.join(temporary, "workspace")
            os.mkdir(home, 0o700)
            os.mkdir(workspace, 0o700)
            source_home = source.get("CODEX_HOME")
            if not source_home:
                user_home = source.get("HOME") or os.path.expanduser("~")
                source_home = os.path.join(user_home, ".codex")
            source_auth = os.path.join(
                os.path.realpath(source_home), "auth.json",
            )
            try:
                auth_info = os.stat(source_auth, follow_symlinks=True)
            except OSError:
                auth_info = None
            if (
                auth_info is not None
                and stat.S_ISREG(auth_info.st_mode)
                and auth_info.st_size <= 4 * 1024 * 1024
            ):
                os.symlink(
                    os.path.realpath(source_auth),
                    os.path.join(home, "auth.json"),
                )
            env["HOME"] = temporary
            env["CODEX_HOME"] = home
            env["CODEX_SQLITE_HOME"] = home
            yield workspace, env

    def start_argv(self, root, prompt, work_unit_policy=None):
        policy = validate_work_unit_policy(work_unit_policy) if work_unit_policy is not None else None
        sealed = policy is not None and policy["context_scope"] == "sealed_input"
        sandbox = "read-only" if policy is not None else "workspace-write"
        argv = [
            self.codex, "exec", "--json", "--sandbox", sandbox, "-C", root,
            "-c", 'approval_policy="never"',
        ]
        if policy is not None:
            argv.extend([
                "--ignore-user-config", "--ignore-rules",
                "-c", "mcp_servers={}", "-c", "hooks={}",
            ])
        if sealed:
            argv.extend([
                "--ephemeral", "--skip-git-repo-check",
                "--disable", "shell_tool",
                "--disable", "unified_exec",
                "--disable", "apps",
                "--disable", "plugins",
                "--disable", "remote_plugin",
                "--disable", "browser_use",
                "--disable", "in_app_browser",
                "--disable", "computer_use",
                "--disable", "image_generation",
                "--disable", "multi_agent",
                "--disable", "tool_suggest",
                "--disable", "workspace_dependencies",
                "--disable", "skill_mcp_dependency_install",
                "--disable", "memories",
                "-c", 'web_search="disabled"',
            ])
        return argv + ["--", prompt]

    def resume_argv(self, session_id, prompt, work_unit_policy=None):
        policy = validate_work_unit_policy(work_unit_policy) if work_unit_policy is not None else None
        if policy is not None and policy["context_scope"] == "sealed_input":
            raise AdapterError("work_unit_resume_forbidden")
        sandbox = "read-only" if policy is not None else "workspace-write"
        argv = [
            self.codex, "exec", "resume", "--json", "-c", 'approval_policy="never"',
            "-c", 'sandbox_mode="%s"' % sandbox,
        ]
        if policy is not None:
            argv.extend([
                "--ignore-user-config", "--ignore-rules",
                "-c", "mcp_servers={}", "-c", "hooks={}",
            ])
        return argv + ["--", session_id, prompt]

    def _invoke(
        self, argv, root, on_session, sealed=False, child_environment=None,
    ):
        self._cancelled.clear()
        timed_out = threading.Event()
        try:
            process = subprocess.Popen(
                argv, cwd=root,
                env=(
                    self.child_environment(self.environ)
                    if child_environment is None else child_environment
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL if sealed else None, bufsize=0,
                start_new_session=True,
            )
            with self._process_lock:
                self._process = process
        except OSError as exc:
            return TurnResult(returncode=127, error_code="provider_crash")
        session_id = ""
        failed_event = ""
        usage = None
        messages = []

        def expire():
            timed_out.set()
            _stop_process(process)

        timer = threading.Timer(self.turn_timeout_seconds, expire)
        timer.daemon = True
        timer.start()
        try:
            for raw, read_error in _bounded_binary_lines(process.stdout):
                if read_error:
                    failed_event = read_error
                    _stop_process(process)
                    break
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
                    failed_event = {
                        "type": event.get("type"),
                        "error_code": event.get("error_code"),
                    }
                elif event.get("type") == "item.completed":
                    item = event.get("item")
                    if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                        messages.append(item["text"])
                        if not sealed:
                            self.stderr.write(item["text"].rstrip() + "\n")
                candidate = event.get("usage")
                if candidate is None and isinstance(event.get("turn"), dict):
                    candidate = event["turn"].get("usage")
                usage = add_usage(usage, normalize_usage(candidate))
            returncode = process.wait()
            timer.cancel()
            _stop_process(process)
        except BaseException:
            _stop_process(process)
            raise
        finally:
            timer.cancel()
            with self._process_lock:
                self._process = None
        if timed_out.is_set():
            returncode = 1
        if returncode == 0 and failed_event:
            returncode = 1
        error = normalize_provider_error(
            failed_event, returncode=returncode, cancelled=self._cancelled.is_set(),
            timed_out=timed_out.is_set(),
        )
        if error and returncode == 0:
            returncode = 1
        return TurnResult(
            returncode=returncode,
            session_id=session_id,
            error_code=error,
            usage=usage,
            output={"messages": messages},
        )

    def cancel(self):
        self._cancelled.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            _stop_process(process)
            return True
        return False

    def start(self, root, prompt, on_session, work_unit_policy=None):
        self.stderr.write("kimiflow: starting Codex headless turn\n")
        policy = (
            validate_work_unit_policy(work_unit_policy)
            if work_unit_policy is not None else None
        )
        sealed = (
            policy is not None
            and policy["context_scope"] == "sealed_input"
        )
        if sealed:
            with self.sealed_runtime() as (sealed_root, environment):
                return self._invoke(
                    self.start_argv(sealed_root, prompt, policy),
                    sealed_root,
                    on_session,
                    sealed=True,
                    child_environment=environment,
                )
        return self._invoke(
            self.start_argv(root, prompt, policy),
            root,
            on_session,
            sealed=False,
        )

    def resume(self, root, session_id, prompt, on_session, work_unit_policy=None):
        self.stderr.write("kimiflow: continuing Codex thread %s\n" % session_id)
        policy = (
            validate_work_unit_policy(work_unit_policy)
            if work_unit_policy is not None else None
        )
        return self._invoke(
            self.resume_argv(session_id, prompt, policy), root, on_session,
            sealed=policy is not None and policy["context_scope"] == "sealed_input",
        )


class ClaudeCodeAdapter:
    """Native fixture-lockable transport for Claude Code's stream-json CLI."""

    READ_ONLY_TOOLS = READ_ONLY_WORK_UNIT_TOOLS

    def __init__(
        self, claude="claude", model=None, event_sink=None, environ=None, stderr=None,
    ):
        self.claude = claude
        self.model = normalize_model(model)
        self.environ = environ
        self.stderr = stderr or sys.stderr
        self.event_sink = event_sink
        self._process = None
        self._process_lock = threading.Lock()
        self._cancelled = threading.Event()
        timeout_value = (os.environ if environ is None else environ).get(
            "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS", str(DEFAULT_TURN_TIMEOUT_SECONDS),
        )
        try:
            self.turn_timeout_seconds = int(timeout_value)
        except (TypeError, ValueError):
            raise AdapterError("adapter_turn_timeout_invalid")
        if not 1 <= self.turn_timeout_seconds <= MAX_TURN_TIMEOUT_SECONDS:
            raise AdapterError("adapter_turn_timeout_invalid")

    def info(self):
        return {
            "schema_version": 1,
            "name": "claude-code",
            "host": "claude",
            "capabilities": {key: True for key in CAPABILITY_KEYS},
            "features": {"work_unit_policy": True},
        }

    def child_environment(self, source=None):
        env = dict(os.environ if source is None else source)
        for key in ("CODEX_THREAD_ID", "KIMIFLOW_SESSION_ID", "KIMIFLOW_SESSION_HOST"):
            env.pop(key, None)
        env["KIMIFLOW_HOST"] = "claude"
        env["KIMIFLOW_RUNNER_CONTROLLER"] = "1"
        return env

    def contract_fingerprint(self):
        source = os.environ if self.environ is None else self.environ
        resolved = shutil.which(self.claude, path=source.get("PATH")) or self.claude
        material = {
            "schema_version": PROTOCOL_VERSION,
            "adapter": self.info(),
            "command": os.path.realpath(resolved),
            "model": self.model,
        }
        return "sha256:" + hashlib.sha256(
            json.dumps(
                material, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _emit(self, event):
        if self.event_sink is None:
            return True
        try:
            self.event_sink({"schema_version": PROTOCOL_VERSION, **event})
        except (BrokenPipeError, OSError):
            return False
        return True

    def _base_argv(self, work_unit_policy=None):
        policy = validate_work_unit_policy(work_unit_policy) if work_unit_policy is not None else None
        argv = [self.claude, "-p", "--output-format", "stream-json", "--verbose"]
        if self.model is not None:
            argv.extend(["--model", self.model])
        if policy is None:
            argv.extend(["--permission-mode", "bypassPermissions"])
        else:
            allowed = tuple(policy["allowed_tools"])
            if any(tool not in self.READ_ONLY_TOOLS for tool in allowed):
                raise AdapterError("work_unit_policy_unbound_tool")
            argv.append("--safe-mode")
            if policy["context_scope"] == "sealed_input":
                argv.append("--no-session-persistence")
            argv.extend([
                "--permission-mode", "plan",
                "--tools", ",".join(allowed),
                "--allowedTools", ",".join(allowed),
                "--setting-sources", "",
                "--strict-mcp-config",
                "--mcp-config", '{"mcpServers":{}}',
            ])
        return argv

    def start_argv(self, root, prompt, work_unit_policy=None):
        del root
        return self._base_argv(work_unit_policy) + ["--", prompt]

    def resume_argv(self, session_id, prompt, work_unit_policy=None):
        policy = (
            validate_work_unit_policy(work_unit_policy)
            if work_unit_policy is not None else None
        )
        if policy is not None and policy["context_scope"] == "sealed_input":
            raise AdapterError("work_unit_resume_forbidden")
        return self._base_argv(policy) + ["--resume", session_id, "--", prompt]

    @staticmethod
    def _usage(event):
        message = event.get("message") if isinstance(event, dict) else None
        raw = message.get("usage") if isinstance(message, dict) else event.get("usage")
        if not isinstance(raw, dict):
            return None
        content = message.get("content") if isinstance(message, dict) else ()
        tool_calls = sum(
            1 for item in content
            if isinstance(item, dict) and item.get("type") == "tool_use"
        ) if isinstance(content, list) else 0
        input_tokens = raw.get("input_tokens", raw.get("inputTokens"))
        output_tokens = raw.get("output_tokens", raw.get("outputTokens"))
        if (
            isinstance(input_tokens, bool) or not isinstance(input_tokens, int)
            or isinstance(output_tokens, bool) or not isinstance(output_tokens, int)
            or input_tokens < 0 or output_tokens < 0
        ):
            return None
        return {
            "model_calls": 1,
            "tool_calls": tool_calls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    def _invoke(self, argv, root, on_session, sealed=False):
        self._cancelled.clear()
        timed_out = threading.Event()
        try:
            process = subprocess.Popen(
                argv, cwd=root, env=self.child_environment(self.environ),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL if sealed else None,
                bufsize=0,
                start_new_session=True,
            )
            with self._process_lock:
                self._process = process
        except OSError:
            return TurnResult(returncode=127, error_code="provider_crash")

        def expire():
            timed_out.set()
            _stop_process(process)

        timer = threading.Timer(self.turn_timeout_seconds, expire)
        timer.daemon = True
        timer.start()
        session_id = ""
        failure = None
        usage = None
        completed = False
        messages = []
        try:
            for raw, read_error in _bounded_binary_lines(process.stdout):
                if read_error:
                    failure = read_error
                    _stop_process(process)
                    break
                try:
                    event = json.loads(raw)
                except (TypeError, ValueError):
                    failure = "provider_crash"
                    continue
                if not isinstance(event, dict):
                    failure = "provider_crash"
                    continue
                event_type = event.get("type")
                if event_type == "system" and event.get("subtype") == "init":
                    observed = event.get("session_id")
                    if isinstance(observed, str) and SESSION_RE.fullmatch(observed):
                        session_id = observed
                        on_session(observed)
                        if not self._emit({
                            "type": "session.started", "session_id": observed,
                        }):
                            failure = "event_sink_failed"
                            _stop_process(process)
                            break
                    else:
                        failure = "provider_crash"
                elif event_type == "assistant":
                    candidate = self._usage(event)
                    usage = add_usage(usage, candidate)
                    message = event.get("message")
                    content = message.get("content") if isinstance(message, dict) else None
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text = item.get("text")
                                if isinstance(text, str) and text:
                                    messages.append(text)
                                    if self.event_sink is None and not sealed:
                                        self.stderr.write(text.rstrip() + "\n")
                                    elif self.event_sink is not None:
                                        if not self._emit({
                                            "type": "message", "text": text,
                                        }):
                                            failure = "event_sink_failed"
                                            _stop_process(process)
                                            break
                    if failure == "event_sink_failed":
                        break
                    if isinstance(message, dict) and message.get("stop_reason") == "refusal":
                        failure = "refusal"
                elif event_type == "result":
                    completed = True
                    observed = event.get("session_id")
                    if not session_id and isinstance(observed, str) and SESSION_RE.fullmatch(observed):
                        session_id = observed
                        on_session(observed)
                    if usage is None:
                        usage = self._usage(event)
                    if event.get("is_error") is True or event.get("subtype") != "success":
                        failure = normalize_provider_error(event, returncode=1)
            returncode = process.wait()
            timer.cancel()
            _stop_process(process)
        except BaseException:
            _stop_process(process)
            raise
        finally:
            timer.cancel()
            if process.stdout is not None:
                process.stdout.close()
            with self._process_lock:
                self._process = None
        error = normalize_provider_error(
            failure,
            returncode=returncode,
            cancelled=self._cancelled.is_set(),
            timed_out=timed_out.is_set(),
        )
        if returncode == 0 and (not completed or error):
            returncode = 1
            error = error or "provider_crash"
        if returncode == 0 and self.event_sink is not None:
            completion = {"type": "turn.completed"}
            if usage is not None:
                completion["usage"] = usage
            if not self._emit(completion):
                returncode = 1
                error = "event_sink_failed"
        return TurnResult(
            returncode=returncode,
            session_id=session_id,
            error_code=error,
            usage=usage,
            output={"messages": messages},
        )

    def cancel(self):
        self._cancelled.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            _stop_process(process)
            return True
        return False

    def start(self, root, prompt, on_session, work_unit_policy=None):
        policy = (
            validate_work_unit_policy(work_unit_policy)
            if work_unit_policy is not None else None
        )
        return self._invoke(
            self.start_argv(root, prompt, policy), root, on_session,
            sealed=policy is not None and policy["context_scope"] == "sealed_input",
        )

    def resume(self, root, session_id, prompt, on_session, work_unit_policy=None):
        policy = (
            validate_work_unit_policy(work_unit_policy)
            if work_unit_policy is not None else None
        )
        return self._invoke(
            self.resume_argv(session_id, prompt, policy), root, on_session,
            sealed=policy is not None and policy["context_scope"] == "sealed_input",
        )


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
        self._context_rollover = None
        self._execution_selection = None
        self._execution_profile_fingerprint = None
        self._usage_turn_ids = set()
        self._process = None
        self._process_lock = threading.Lock()
        self._cancelled = threading.Event()
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
            "execution_profile": info.get("execution_profile"),
        }
        search_path = (os.environ if self.environ is None else self.environ).get("PATH")
        resolved = shutil.which(self.executable, path=search_path)
        material["adapter_command"] = os.path.realpath(resolved or self.executable)
        payload = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:%s" % hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def set_context_rollover(self, value):
        from . import adaptive_control

        try:
            adaptive_control.validate_pending_rollover(value)
        except adaptive_control.AdaptiveControlError:
            raise AdapterError("context_rollover_invalid")
        self._context_rollover = {
            "schema_version": 1,
            "rollover_id": value["rollover_id"],
            "current_digest": value["current_digest"],
            "phase": value.get("phase"),
            "reason": value.get("reason"),
            "retained": value.get("retained", []),
        }

    def _select_execution_variant(self, root, profile):
        from . import adaptive_control

        profile_fingerprint = execution_profile_fingerprint(profile)
        if self._execution_selection is not None:
            if self._execution_profile_fingerprint != profile_fingerprint:
                raise AdapterError("execution_profile_drift")
            return dict(self._execution_selection)
        risk = adaptive_control.routing_risk(root)
        task_class = "%s-code" % risk
        contract = self.contract_fingerprint()
        if contract is None:
            contract = "sha256:" + hashlib.sha256(
                b"kimiflow-adapter-prompt-gate-v1"
            ).hexdigest()
        try:
            route = adaptive_control.resolve_execution_variant(
                root,
                profile,
                "implementation",
                task_class,
                runtime_fingerprint(),
                profile_fingerprint,
                contract,
                risk=risk,
            )
        except (OSError, ValueError, adaptive_control.AdaptiveControlError):
            route = {
                "execution_variant": default_execution_variant(profile),
                "reason": "routing_policy_invalid",
            }
        self._execution_profile_fingerprint = profile_fingerprint
        self._execution_selection = {
            "schema_version": 1,
            "profile_fingerprint": profile_fingerprint,
            "model_fingerprint": profile["model_fingerprint"],
            "execution_variant": route["execution_variant"],
            "max_input_tokens": profile["max_input_tokens"],
            "max_output_tokens": profile["max_output_tokens"],
            "controls": dict(profile["controls"]),
        }
        return dict(self._execution_selection)

    def _invoke(
        self, action, root, session_id, prompt, on_session, work_unit_policy=None,
    ):
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
        policy = (
            validate_work_unit_policy(work_unit_policy)
            if work_unit_policy is not None else None
        )
        sealed = (
            policy is not None and policy["context_scope"] == "sealed_input"
        )
        if policy is not None:
            if features.get("work_unit_policy") is not True:
                raise AdapterError("work_unit_policy_unavailable")
            payload["work_unit_policy"] = policy
        profile = info.get("execution_profile")
        execution_selection = None
        if not sealed and features.get("adaptive_execution_profiles") is True:
            execution_selection = self._select_execution_variant(root, profile)
            payload["execution_profile"] = execution_selection
        if not sealed and features.get("workflow_context") is True:
            payload["workflow_context"] = workflow_context()
        if not sealed and self.model_roles:
            if features.get("adaptive_model_routes") is True:
                from . import adaptive_control

                risk = adaptive_control.routing_risk(root)
                try:
                    policy = adaptive_control.resolve_model_roles(
                        root, self.model_roles, risk=risk, write=True,
                    )
                except (OSError, ValueError, adaptive_control.AdaptiveControlError):
                    baseline = self.model_roles.get("top")
                    roles = dict(self.model_roles)
                    if baseline:
                        for role in ("balanced", "cheap"):
                            if role in roles:
                                roles[role] = baseline
                    policy = {
                        "schema_version": 1,
                        "status": "fallback",
                        "reason": "routing_policy_invalid",
                        "roles": roles,
                        "decisions": {},
                        "risk": risk,
                        "user_gate": False,
                    }
                payload["model_routing"] = {"roles": dict(policy["roles"])}
                payload["model_routing"].update({
                    "candidates": dict(self.model_roles),
                    "policy": {
                        "status": policy["status"],
                        "risk": policy["risk"],
                        "decisions": policy["decisions"],
                    },
                })
            else:
                payload["model_routing"] = {"roles": dict(self.model_roles)}
            if self.model is not None:
                payload["model_routing"]["default_model"] = self.model
        if (
            not sealed
            and action == "resume"
            and self._context_rollover is not None
        ):
            if features.get("context_rollover") is True:
                payload["context_rollover"] = self._context_rollover
            self._context_rollover = None
        argv = [self.executable, action, "--json"]
        proc = None
        timer = None
        timed_out = threading.Event()
        self._cancelled.clear()

        def expire_turn():
            if proc is not None:
                timed_out.set()
                _signal_process_group(proc, signal.SIGKILL)

        try:
            env = self._environment(info["host"], session_id)
            env["KIMIFLOW_RUNNER_CONTROLLER"] = "1"
            proc = subprocess.Popen(
                argv, cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=(
                    subprocess.DEVNULL
                    if sealed
                    else None
                ),
                bufsize=0, start_new_session=True,
            )
            with self._process_lock:
                self._process = proc
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
            with self._process_lock:
                self._process = None
            if timed_out.is_set():
                return TurnResult(returncode=1, error_code="turn_timeout")
            return TurnResult(returncode=127, error_code="provider_crash")
        observed = session_id or ""
        failed = ""
        diagnostic_code = ""
        usage = None
        completed = False
        completion_event = None
        context_compaction = None
        model_route = None
        usage_v2 = None
        messages = []
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
                    failed = public.get("error_code") or event_type
                    diagnostic_code = public.get("diagnostic_code", "")
                elif (
                    event_type == "message"
                    and self.event_sink is None
                    and not sealed
                ):
                    messages.append(public["text"])
                    self.stderr.write(public["text"].rstrip() + "\n")
                elif event_type == "message":
                    messages.append(public["text"])
                elif event_type == "turn.completed":
                    if completed:
                        failed = "duplicate_turn_completed"
                    completed = True
                    usage = public.get("usage")
                    usage_v2 = public.get("usage_v2")
                    if usage_v2 is not None:
                        if execution_selection is None:
                            failed = "usage_v2_without_execution_profile"
                        elif (
                            usage_v2["session_id"] != observed
                            or usage_v2["model_fingerprint"]
                            != execution_selection["model_fingerprint"]
                            or usage_v2["execution_variant"]
                            != execution_selection["execution_variant"]
                            or (
                                usage_v2["status"] == "available"
                                and usage_v2["max_input_tokens"]
                                != execution_selection["max_input_tokens"]
                            )
                        ):
                            failed = "usage_v2_profile_mismatch"
                        elif usage_v2["turn_id"] in self._usage_turn_ids:
                            failed = "usage_v2_turn_replayed"
                        else:
                            self._usage_turn_ids.add(usage_v2["turn_id"])
                    model_route = public.get("model_route")
                    if model_route is not None:
                        routing = payload.get("model_routing")
                        roles = routing.get("roles") if isinstance(routing, dict) else None
                        candidates = (
                            routing.get("candidates") if isinstance(routing, dict) else None
                        )
                        if (
                            not isinstance(roles, dict)
                            or not isinstance(candidates, dict)
                            or model_route["model"] != candidates.get(model_route["role"])
                            or model_route["baseline"] != roles.get("top")
                        ):
                            failed = "model_route_mismatch"
                    completion_event = public
                elif event_type == "context.compacted":
                    context_compaction = public
                if self.event_sink is not None and event_type != "turn.completed":
                    try:
                        self.event_sink({"schema_version": PROTOCOL_VERSION, **public})
                    except (BrokenPipeError, OSError):
                        failed = "event_sink_failed"
                        _stop_process(proc)
                        break
            returncode = proc.wait()
            timer.cancel()
            _stop_process(proc)
        except BaseException:
            _stop_process(proc)
            raise
        finally:
            timer.cancel()
            proc.stdout.close()
            with self._process_lock:
                self._process = None
        if timed_out.is_set():
            failed = "turn_timeout"
            returncode = 1
        if returncode == 0 and not completed and not failed:
            failed = "missing_turn_completed"
        if returncode == 0 and failed:
            returncode = 1
        if failed.startswith("usage_v2_"):
            usage = None
            usage_v2 = None
        if returncode == 0 and not failed and self.event_sink is not None:
            try:
                self.event_sink({"schema_version": PROTOCOL_VERSION, **completion_event})
            except (BrokenPipeError, OSError):
                failed = "event_sink_failed"
                returncode = 1
        failed = normalize_provider_error(
            failed, returncode=returncode, cancelled=self._cancelled.is_set(),
            timed_out=timed_out.is_set(),
        )
        if failed and returncode == 0:
            returncode = 1
        return TurnResult(
            returncode=returncode,
            session_id=observed,
            error_code=failed,
            usage=usage,
            context_compaction=context_compaction,
            model_route=model_route,
            usage_v2=usage_v2,
            output={"messages": messages},
            diagnostic_code=diagnostic_code,
        )

    def cancel(self):
        self._cancelled.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            _stop_process(process)
            return True
        return False

    def start(self, root, prompt, on_session, work_unit_policy=None):
        return self._invoke(
            "start", root, "", prompt, on_session,
            work_unit_policy=work_unit_policy,
        )

    def resume(self, root, session_id, prompt, on_session, work_unit_policy=None):
        policy = (
            validate_work_unit_policy(work_unit_policy)
            if work_unit_policy is not None else None
        )
        if policy is not None and policy["context_scope"] == "sealed_input":
            raise AdapterError("work_unit_resume_forbidden")
        return self._invoke(
            "resume", root, session_id, prompt, on_session,
            work_unit_policy=policy,
        )
