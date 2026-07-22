"""Optional provider-neutral controller over the existing Kimiflow active-run core."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timezone

from . import active_run, model_adapter, workspace_preflight
from .paths import RootResolutionError, resolve_root


RECEIPT_RELATIVE = ".kimiflow/session/HEADLESS_RUN.json"
MAX_RECEIPT_BYTES = 64 * 1024
TRANSPORT_RETRIES = 2
DEFAULT_AUTONOMOUS_TURN_LIMIT = 48
TERMINAL_OUTCOMES = {"done", "parked", "failed", "aborted"}
RESUMABLE_WAIT_STATES = {"awaiting_user", "parked"}
RESUMABLE_STATES = RESUMABLE_WAIT_STATES | {"running", "interrupted", "transport_error", "exhausted"}
RECEIPT_STATES = RESUMABLE_STATES | {
    "done", "failed", "aborted", "ownership_conflict", "no_kimiflow_run"
}


class RunnerError(Exception):
    def __init__(self, status, message, code=1):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


TurnResult = model_adapter.TurnResult
CodexExecAdapter = model_adapter.CodexExecAdapter


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def receipt_path(root):
    return os.path.join(root, *RECEIPT_RELATIVE.split("/"))


def _reject_duplicate_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key: %s" % key)
        value[key] = item
    return value


def _validate_receipt(root, value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RunnerError("invalid_receipt", "runner receipt has an unsupported schema", 2)
    value = dict(value)
    if not value.get("session_id") and value.get("thread_id"):
        value["session_id"] = value["thread_id"]
    if not value.get("thread_id") and value.get("session_id"):
        value["thread_id"] = value["session_id"]
    if not value.get("adapter") and value.get("host") == "codex":
        value["adapter"] = "codex-exec"
    required_strings = ("host", "adapter", "root", "session_id", "thread_id", "status", "started_at", "updated_at")
    if any(not isinstance(value.get(key), str) or not value.get(key) for key in required_strings):
        raise RunnerError("invalid_receipt", "runner receipt is incomplete", 2)
    if (
        not os.path.isabs(value.get("root"))
        or os.path.realpath(value.get("root")) != os.path.realpath(root)
    ):
        raise RunnerError("receipt_mismatch", "runner receipt belongs to another host or project", 1)
    if value.get("session_id") != value.get("thread_id") or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", value.get("session_id")):
        raise RunnerError("invalid_receipt", "runner receipt has an invalid session ID", 2)
    if value.get("status") not in RECEIPT_STATES:
        raise RunnerError("invalid_receipt", "runner receipt has an invalid status", 2)
    if not isinstance(value.get("turns"), int) or value.get("turns") < 0:
        raise RunnerError("invalid_receipt", "runner receipt has an invalid turn count", 2)
    if "turn_limit" in value and (
        isinstance(value.get("turn_limit"), bool) or not isinstance(value.get("turn_limit"), int)
        or value.get("turn_limit") < 1
    ):
        raise RunnerError("invalid_receipt", "runner receipt has an invalid turn limit", 2)
    if "final_recovery_used" in value and not isinstance(value.get("final_recovery_used"), bool):
        raise RunnerError("invalid_receipt", "runner receipt has an invalid recovery marker", 2)
    adapter_contract = value.get("adapter_contract")
    if adapter_contract is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", adapter_contract):
        raise RunnerError("invalid_receipt", "runner receipt has an invalid adapter contract", 2)
    usage = value.get("usage")
    if usage is not None:
        if not isinstance(usage, dict) or usage.get("status") not in ("available", "unavailable"):
            raise RunnerError("invalid_receipt", "runner receipt has invalid usage", 2)
        for key in model_adapter.USAGE_KEYS:
            item = usage.get(key)
            if usage["status"] == "unavailable":
                if item is not None:
                    raise RunnerError("invalid_receipt", "unavailable usage must use null counters", 2)
            elif isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise RunnerError("invalid_receipt", "runner receipt has invalid usage counters", 2)
    active = value.get("active_run")
    if active is not None:
        normalized = os.path.normpath(active).replace(os.sep, "/") if isinstance(active, str) else ""
        if not normalized.startswith(".kimiflow/") or normalized != active or "/../" in "/%s/" % active:
            raise RunnerError("invalid_receipt", "runner receipt has an invalid active run", 2)
    return value


def write_receipt(root, value):
    value = _validate_receipt(root, value)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with workspace_preflight.registry_directory(root, create=True) as descriptor:
            try:
                target = os.stat("HEADLESS_RUN.json", dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                target = None
            if target is not None and not stat.S_ISREG(target.st_mode):
                raise RunnerError("unsafe_receipt", "runner receipt is not a safe regular file", 2)
            workspace_preflight.atomic_directory_write(descriptor, "HEADLESS_RUN.json", payload.encode("utf-8"))
    except RunnerError:
        raise
    except (OSError, UnicodeError, workspace_preflight.WorkspaceError) as exc:
        raise RunnerError("unsafe_receipt", "cannot write runner receipt: %s" % exc, 2)


def load_receipt(root, required=True):
    descriptor = None
    file_descriptor = None
    try:
        with workspace_preflight.registry_directory(root, create=False) as descriptor:
            if descriptor is None:
                if required:
                    raise RunnerError("receipt_missing", "no resumable Kimiflow terminal run exists", 1)
                return None
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                file_descriptor = os.open("HEADLESS_RUN.json", flags, dir_fd=descriptor)
            except FileNotFoundError:
                if required:
                    raise RunnerError("receipt_missing", "no resumable Kimiflow terminal run exists", 1)
                return None
            except OSError as exc:
                raise RunnerError("unsafe_receipt", "cannot safely open runner receipt: %s" % exc, 2)
            info = os.fstat(file_descriptor)
            named = os.stat("HEADLESS_RUN.json", dir_fd=descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_RECEIPT_BYTES
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise RunnerError("unsafe_receipt", "runner receipt is not a safe regular file", 2)
            payload = os.read(file_descriptor, MAX_RECEIPT_BYTES + 1)
            if len(payload) > MAX_RECEIPT_BYTES:
                raise RunnerError("unsafe_receipt", "runner receipt is too large", 2)
            value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except RunnerError:
        raise
    except workspace_preflight.WorkspaceError as exc:
        raise RunnerError("unsafe_receipt", "cannot safely open runner state directory: %s" % exc, 2)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RunnerError("invalid_receipt", "cannot read runner receipt: %s" % exc, 2)
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
    return _validate_receipt(root, value)


def _resolve_project_root(root=None):
    try:
        resolved = resolve_root(root=root, mode="strict")
    except RootResolutionError as exc:
        raise RunnerError("root_invalid", str(exc), 2)
    proc = subprocess.run(
        ["git", "-C", resolved, "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RunnerError("root_not_git", "Kimiflow terminal runs require a Git repository", 2)
    canonical = proc.stdout.strip()
    if not canonical:
        raise RunnerError("root_not_git", "Git did not return a repository root", 2)
    return os.path.realpath(canonical)


def _active_status(root):
    try:
        status = active_run.status_json(root)
    except (active_run.ActiveError, OSError, ValueError) as exc:
        raise RunnerError("active_state_invalid", "cannot inspect active Kimiflow state: %s" % exc, 2)
    if status.get("status") == "invalid":
        raise RunnerError("active_state_invalid", "active Kimiflow state is invalid", 2)
    return status


def _safe_outcome_bytes(root, relative):
    parts = relative.replace(os.sep, "/").split("/")
    if len(parts) != 3 or parts[0] != ".kimiflow" or parts[2] != "SESSION-OUTCOME.json":
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    base_descriptor = None
    run_descriptor = None
    file_descriptor = None
    try:
        base_descriptor = os.open(os.path.join(root, ".kimiflow"), flags)
        run_descriptor = os.open(parts[1], flags, dir_fd=base_descriptor)
        file_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        file_descriptor = os.open(parts[2], file_flags, dir_fd=run_descriptor)
        info = os.fstat(file_descriptor)
        named = os.stat(parts[2], dir_fd=run_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_RECEIPT_BYTES
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            return None
        payload = os.read(file_descriptor, MAX_RECEIPT_BYTES + 1)
        final = os.fstat(file_descriptor)
        if len(payload) > MAX_RECEIPT_BYTES or (info.st_size, info.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            return None
        return payload, final
    except OSError:
        return None
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if run_descriptor is not None:
            os.close(run_descriptor)
        if base_descriptor is not None:
            os.close(base_descriptor)


def _outcome_fingerprints(root):
    base = os.path.join(root, ".kimiflow")
    values = {}
    if not os.path.isdir(base) or os.path.islink(base):
        return values
    try:
        entries = list(os.scandir(base))
    except OSError:
        return values
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        relative = ".kimiflow/%s/SESSION-OUTCOME.json" % entry.name
        safe = _safe_outcome_bytes(root, relative)
        if safe is None:
            continue
        payload, info = safe
        values[relative] = (info.st_mtime_ns, info.st_size, hashlib.sha256(payload).hexdigest())
    return values


def _read_changed_outcome(root, baseline, run_hint=None):
    current = _outcome_fingerprints(root)
    candidates = [path for path, fingerprint in current.items() if baseline.get(path) != fingerprint]
    hinted = os.path.join(run_hint, "SESSION-OUTCOME.json") if run_hint else ""
    candidates.sort(key=lambda path: (path != hinted, path))
    for relative in candidates:
        try:
            safe = _safe_outcome_bytes(root, relative)
            if safe is None:
                continue
            value = json.loads(safe[0].decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeError, ValueError):
            continue
        outcome = value.get("outcome") if isinstance(value, dict) else None
        if outcome in TERMINAL_OUTCOMES:
            run = os.path.dirname(relative).replace(os.sep, "/")
            return {"status": outcome, "run": run, "reason": value.get("reason")}
    return None


def _initial_prompt(task, workflow_aware=False):
    if workflow_aware:
        return (
            "Execute the canonical Kimiflow workflow supplied by this turn's workflow_context. "
            "Run it autonomously through its mechanical finish. Do not ask for routine continuation or "
            "confirmation; pause only for a material decision through Kimiflow's typed wait/park contract."
            "\n\nRequest:\n" + task.strip()
        )
    return (
        "Use $kimiflow for the request below. Run it autonomously through its mechanical finish. "
        "Do not ask for routine continuation or confirmation; pause only for a material decision through "
        "Kimiflow's typed wait/park contract.\n\nRequest:\n" + task.strip()
    )


def _continuation_prompt(status):
    transition = status.get("transition") if isinstance(status.get("transition"), dict) else {}
    parts = []
    for key in ("action", "target_node", "reason"):
        value = transition.get(key)
        if isinstance(value, str) and value:
            parts.append("%s=%s" % (key, value[:240]))
    execution = transition.get("execution") if isinstance(transition.get("execution"), dict) else {}
    for key in ("profile", "profile_reason", "strategy_mode", "budget_pressure", "directive"):
        value = execution.get(key)
        if isinstance(value, str) and value:
            parts.append("%s=%s" % (key, value[:80]))
    if not parts:
        action = status.get("next_action")
        if isinstance(action, str) and action:
            parts.append("action=%s" % action[:240])
    exact = ", ".join(parts) or "continue_current_phase"
    return (
        "Continue the active Kimiflow run autonomously. Exact next action: %s. "
        "Do not stop for routine confirmation. Use a typed material wait only for a real user decision; "
        "otherwise complete the work and close the active run mechanically." % exact
    )


def _parked_resume_prompt(run, message, workflow_aware=False):
    slug = os.path.basename(run.rstrip("/"))
    if workflow_aware:
        return "Resume the canonical Kimiflow run %s using the supplied workflow_context.\n\nUser decision/input: %s" % (
            slug, message.strip(),
        )
    return "$kimiflow --resume %s\n\nUser decision/input: %s" % (slug, message.strip())


def _interrupted_resume_prompt(workflow_aware=False):
    start_instruction = (
        "start the canonical Kimiflow workflow supplied by workflow_context for the original request now"
        if workflow_aware else "start $kimiflow for the original request now"
    )
    return (
        "Continue the explicit Kimiflow task already present in this coding-agent session. If interruption happened "
        "before its active run was created, %s; otherwise continue the existing run autonomously. "
        "Do not ask for routine confirmation." % start_instruction
    )


def _turn_limit():
    raw = os.environ.get("KIMIFLOW_RUNNER_TURN_LIMIT", "")
    try:
        value = int(raw) if raw else DEFAULT_AUTONOMOUS_TURN_LIMIT
    except ValueError:
        raise RunnerError("turn_limit_invalid", "KIMIFLOW_RUNNER_TURN_LIMIT must be an integer", 2)
    if not 1 <= value <= 10000:
        raise RunnerError("turn_limit_invalid", "runner turn limit must be between 1 and 10000", 2)
    return value


def _unavailable_usage():
    return {"status": "unavailable", **{key: None for key in model_adapter.USAGE_KEYS}}


def _merge_usage(current, delta, initialize=False):
    normalized = model_adapter.normalize_usage(delta)
    if normalized is None:
        return _unavailable_usage()
    if initialize:
        return {"status": "available", **normalized}
    if not isinstance(current, dict) or current.get("status") != "available":
        return _unavailable_usage()
    return {"status": "available", **{
        key: current[key] + normalized[key] for key in model_adapter.USAGE_KEYS
    }}


def _new_receipt(root, thread_id, adapter_info, adapter_contract=None):
    now = iso_now()
    result = {
        "schema_version": 1,
        "host": adapter_info["host"],
        "adapter": adapter_info["name"],
        "root": root,
        "session_id": thread_id,
        "thread_id": thread_id,
        "status": "running",
        "turns": 0,
        "active_run": None,
        "turn_limit": _turn_limit(),
        "final_recovery_used": False,
        "usage": _unavailable_usage(),
        "started_at": now,
        "updated_at": now,
    }
    if adapter_contract is not None:
        result["adapter_contract"] = adapter_contract
    return result


def _update_receipt(root, receipt, status=None, **updates):
    value = dict(receipt)
    if status:
        value["status"] = status
    value.update(updates)
    value["updated_at"] = iso_now()
    write_receipt(root, value)
    return value


def _public_result(receipt, status=None, outcome=None, wait=None):
    result = {
        "schema_version": 1,
        "status": status or receipt.get("status"),
        "host": receipt.get("host"),
        "adapter": receipt.get("adapter"),
        "root": receipt.get("root"),
        "thread_id": receipt.get("thread_id"),
        "session_id": receipt.get("session_id") or receipt.get("thread_id"),
        "active_run": receipt.get("active_run"),
        "turns": receipt.get("turns", 0),
        "usage": receipt.get("usage", _unavailable_usage()),
    }
    if isinstance(outcome, dict) and outcome.get("reason"):
        result["reason"] = outcome["reason"]
    if isinstance(wait, dict) and wait.get("awaiting_kind"):
        result["awaiting_kind"] = wait["awaiting_kind"]
    if receipt.get("exhaustion_reason"):
        result["exhaustion_reason"] = receipt["exhaustion_reason"]
    return result


def _owner_matches(status, receipt):
    owner = status.get("owner")
    return (
        isinstance(owner, dict)
        and owner.get("host") == receipt.get("host")
        and owner.get("session_id") == receipt.get("session_id", receipt.get("thread_id"))
    )


def _record_interruption(root, receipt):
    if receipt is None:
        return {"schema_version": 1, "status": "interrupted", "host": "unknown", "root": root, "turns": 0}
    receipt = _update_receipt(root, receipt, "interrupted")
    return _public_result(receipt)


def _execution_hard(status):
    transition = status.get("transition") if isinstance(status.get("transition"), dict) else {}
    execution = transition.get("execution") if isinstance(transition.get("execution"), dict) else {}
    return execution.get("budget_pressure") == "hard"


def _record_turn_usage(root, status, usage):
    normalized = model_adapter.normalize_usage(usage)
    try:
        active_run.record_host_usage(root, status, normalized)
    except (active_run.ActiveError, OSError, ValueError) as exc:
        raise RunnerError("usage_record_failed", "cannot persist adapter usage receipt: %s" % exc, 2)
    if normalized is None or not isinstance(status.get("execution_control"), dict):
        return
    try:
        active_run._observe_execution(
            root, status, "turn_completed", "progress", "", normalized, True,
            coalesce_pending_stop=True,
        )
    except (active_run.ActiveError, OSError, ValueError) as exc:
        raise RunnerError("usage_record_failed", "cannot persist adapter usage: %s" % exc, 2)


def _final_recovery_prompt(status):
    return (
        "This is the single final bounded recovery turn before the terminal controller becomes resumable-exhausted. "
        "Reassess the current blocker, choose a materially different safe strategy, run the decisive verification, "
        "and mechanically finish if every required gate is open. Never weaken or skip a gate. "
        + _continuation_prompt(status)
    )


def _drive(root, adapter, receipt, turn, baseline, workflow_aware=False):
    retries = 0
    while True:
        receipt = _update_receipt(
            root, receipt, turns=receipt["turns"] + 1,
            usage=_merge_usage(receipt.get("usage"), turn.usage, initialize=receipt["turns"] == 0),
        )
        while turn.returncode != 0:
            if turn.error_code == "event_sink_failed":
                _update_receipt(root, receipt, "transport_error", error_code=turn.error_code)
                raise RunnerError("transport_error", "coding-agent event consumer closed", 1)
            if retries >= TRANSPORT_RETRIES:
                receipt = _update_receipt(root, receipt, "transport_error", error_code=turn.error_code or "adapter_exit_%s" % turn.returncode)
                raise RunnerError("transport_error", "coding-agent transport failed after automatic retries", 1)
            retries += 1
            try:
                turn = adapter.resume(
                    root,
                    receipt["thread_id"],
                    (
                        "Recover the explicit Kimiflow task after a transport/tool failure. If no active run exists yet, "
                        + (
                            "start the canonical workflow supplied by workflow_context for the original request; "
                            if workflow_aware else "start $kimiflow for the original request; "
                        )
                        + "otherwise choose another safe in-scope strategy and continue autonomously."
                    ),
                    lambda value: _ensure_same_thread(value, receipt["thread_id"]),
                )
            except KeyboardInterrupt:
                return _record_interruption(root, receipt)
            receipt = _update_receipt(
                root, receipt, turns=receipt["turns"] + 1,
                usage=_merge_usage(receipt.get("usage"), turn.usage, initialize=receipt["turns"] == 0),
            )
        retries = 0
        status = _active_status(root)
        if status.get("present") is True and status.get("terminal") is False:
            if not _owner_matches(status, receipt):
                receipt = _update_receipt(root, receipt, "ownership_conflict")
                raise RunnerError("ownership_conflict", "active Kimiflow run is not owned by this adapter session", 1)
            receipt = _update_receipt(root, receipt, active_run=status.get("run"))
            _record_turn_usage(root, status, turn.usage)
            if status.get("awaiting_user") is True:
                receipt = _update_receipt(root, receipt, "awaiting_user")
                return _public_result(receipt, wait=status)
            limit_reached = receipt["turns"] >= receipt.get("turn_limit", DEFAULT_AUTONOMOUS_TURN_LIMIT)
            if limit_reached or _execution_hard(status):
                reason = "turn_limit" if limit_reached else "execution_budget_hard"
                if receipt.get("final_recovery_used") is True:
                    receipt = _update_receipt(root, receipt, "exhausted", exhaustion_reason=reason)
                    return _public_result(receipt)
                receipt = _update_receipt(
                    root, receipt, final_recovery_used=True, exhaustion_reason=reason,
                )
                try:
                    turn = adapter.resume(
                        root, receipt["thread_id"], _final_recovery_prompt(status),
                        lambda value: _ensure_same_thread(value, receipt["thread_id"]),
                    )
                except KeyboardInterrupt:
                    return _record_interruption(root, receipt)
                continue
            try:
                turn = adapter.resume(
                    root,
                    receipt["thread_id"],
                    _continuation_prompt(status),
                    lambda value: _ensure_same_thread(value, receipt["thread_id"]),
                )
            except KeyboardInterrupt:
                return _record_interruption(root, receipt)
            continue
        outcome = _read_changed_outcome(root, baseline, receipt.get("active_run"))
        if outcome:
            receipt = _update_receipt(root, receipt, outcome["status"], active_run=outcome["run"])
            return _public_result(receipt, outcome=outcome)
        receipt = _update_receipt(root, receipt, "no_kimiflow_run")
        raise RunnerError("no_kimiflow_run", "coding agent completed without creating or finishing a Kimiflow run", 1)


def _ensure_same_thread(value, expected):
    if value and value != expected:
        raise RunnerError("thread_mismatch", "coding-agent adapter resumed a different session", 1)


def _adapter_contract(adapter):
    if not hasattr(adapter, "contract_fingerprint"):
        return None
    value = adapter.contract_fingerprint()
    if value is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RunnerError("adapter_incompatible", "adapter returned an invalid contract fingerprint", 2)
    return value


def _workflow_aware(adapter_info):
    features = adapter_info.get("features") if isinstance(adapter_info.get("features"), dict) else {}
    return features.get("workflow_context") is True


def run_task(root, task, adapter=None):
    root = _resolve_project_root(root)
    if not isinstance(task, str) or not task.strip():
        raise RunnerError("task_missing", "run requires a non-empty task", 2)
    adapter = adapter or CodexExecAdapter()
    try:
        adapter_info = model_adapter.info_for(adapter)
    except model_adapter.AdapterError as exc:
        raise RunnerError("adapter_incompatible", str(exc), 2)
    try:
        adapter_contract = _adapter_contract(adapter)
    except model_adapter.AdapterError as exc:
        raise RunnerError("adapter_incompatible", str(exc), 2)
    workflow_aware = _workflow_aware(adapter_info)
    current = _active_status(root)
    if current.get("present") is True:
        raise RunnerError("active_run_exists", "an active Kimiflow run already exists; use its owning session or terminal resume", 1)
    baseline = _outcome_fingerprints(root)
    holder = {"receipt": None}

    def capture(thread_id):
        if holder["receipt"] is not None:
            _ensure_same_thread(thread_id, holder["receipt"]["thread_id"])
            return
        holder["receipt"] = _new_receipt(root, thread_id, adapter_info, adapter_contract)
        write_receipt(root, holder["receipt"])

    try:
        turn = adapter.start(root, _initial_prompt(task, workflow_aware=workflow_aware), capture)
    except KeyboardInterrupt:
        return _record_interruption(root, holder["receipt"])
    if holder["receipt"] is None and turn.thread_id:
        capture(turn.thread_id)
    if holder["receipt"] is None:
        raise RunnerError("thread_missing", "coding-agent adapter did not emit a resumable session ID", 1)
    return _drive(root, adapter, holder["receipt"], turn, baseline, workflow_aware=workflow_aware)


def resume_task(root, message=None, adapter=None):
    root = _resolve_project_root(root)
    receipt = load_receipt(root)
    if receipt.get("status") not in RESUMABLE_STATES:
        raise RunnerError("not_resumable", "terminal run is not resumable", 1)
    adapter = adapter or CodexExecAdapter()
    try:
        adapter_info = model_adapter.info_for(adapter)
    except model_adapter.AdapterError as exc:
        raise RunnerError("adapter_incompatible", str(exc), 2)
    if adapter_info["name"] != receipt.get("adapter") or adapter_info["host"] != receipt.get("host"):
        raise RunnerError("adapter_mismatch", "resume requires the same adapter and host", 2)
    try:
        adapter_contract = _adapter_contract(adapter)
    except model_adapter.AdapterError as exc:
        raise RunnerError("adapter_incompatible", str(exc), 2)
    if adapter_contract != receipt.get("adapter_contract"):
        raise RunnerError("adapter_mismatch", "resume requires the same negotiated adapter contract", 2)
    workflow_aware = _workflow_aware(adapter_info)
    current = _active_status(root)
    waiting = current.get("awaiting_user") is True or receipt.get("status") == "parked"
    if waiting and (not isinstance(message, str) or not message.strip()):
        raise RunnerError("message_required", "this material wait requires --message", 2)
    if current.get("present") is True and current.get("terminal") is False:
        if not _owner_matches(current, receipt):
            raise RunnerError("ownership_conflict", "active Kimiflow run belongs to another session", 1)
        if receipt.get("active_run") and current.get("run") != receipt.get("active_run"):
            raise RunnerError("receipt_mismatch", "receipt and active run do not match", 1)
        prompt = message.strip() if waiting else _continuation_prompt(current)
    elif receipt.get("status") == "parked":
        prompt = _parked_resume_prompt(
            receipt.get("active_run") or "", message, workflow_aware=workflow_aware,
        )
    elif receipt.get("status") in {"running", "interrupted", "transport_error"}:
        prompt = _interrupted_resume_prompt(workflow_aware=workflow_aware)
    elif receipt.get("status") == "exhausted" and current.get("present") is True:
        prompt = _continuation_prompt(current)
    else:
        raise RunnerError("active_run_missing", "resumable receipt has no matching active run", 1)
    baseline = _outcome_fingerprints(root)
    updates = {}
    if receipt.get("status") == "exhausted":
        updates = {
            "turn_limit": max(receipt.get("turn_limit", 0), receipt.get("turns", 0)) + _turn_limit(),
            "final_recovery_used": False,
            "exhaustion_reason": "",
        }
    receipt = _update_receipt(root, receipt, "running", **updates)
    try:
        turn = adapter.resume(
            root,
            receipt["thread_id"],
            prompt,
            lambda value: _ensure_same_thread(value, receipt["thread_id"]),
        )
    except KeyboardInterrupt:
        return _record_interruption(root, receipt)
    return _drive(root, adapter, receipt, turn, baseline, workflow_aware=workflow_aware)


def runner_status(root):
    root = _resolve_project_root(root)
    receipt = load_receipt(root, required=False)
    active = _active_status(root)
    status = receipt.get("status") if receipt else ("embedded_active" if active.get("present") else "idle")
    return {"schema_version": 1, "status": status, "runner": receipt, "active_run": active}


def exit_code(result):
    status = result.get("status") if isinstance(result, dict) else "error"
    if status in ("done", "idle", "embedded_active", "compatible"):
        return 0
    if status in RESUMABLE_WAIT_STATES:
        return 3
    if status == "interrupted":
        return 130
    return 1


def _add_adapter_arguments(parser):
    parser.add_argument("--adapter", choices=("codex", "command"), default="codex")
    parser.add_argument("--adapter-command")
    parser.add_argument("--model")
    parser.add_argument("--model-role", action="append", default=[])
    parser.add_argument("--require-feature", action="append", default=[])
    parser.add_argument("--events-jsonl", action="store_true")


def _model_roles(values):
    result = {}
    for value in values or ():
        if not isinstance(value, str) or "=" not in value:
            raise RunnerError("model_role_invalid", "--model-role requires ROLE=MODEL", 2)
        role, model = value.split("=", 1)
        if role in result:
            raise RunnerError("model_role_invalid", "--model-role may declare each role only once", 2)
        result[role] = model
    try:
        return model_adapter.normalize_model_roles(result)
    except model_adapter.AdapterError as exc:
        raise RunnerError("model_role_invalid", str(exc), 2)


def _adapter_from_args(args, event_sink=None):
    if args.adapter == "codex":
        if args.adapter_command or args.model_role or args.require_feature or args.events_jsonl:
            raise RunnerError(
                "adapter_ambiguous",
                "--adapter-command, --model-role, --require-feature, and --events-jsonl require --adapter command",
                2,
            )
        return CodexExecAdapter()
    if not args.adapter_command:
        raise RunnerError("adapter_command_missing", "--adapter command requires --adapter-command", 2)
    try:
        return model_adapter.CommandAgentAdapter(
            args.adapter_command,
            model=args.model,
            model_roles=_model_roles(args.model_role),
            required_features=args.require_feature,
            event_sink=event_sink,
        )
    except model_adapter.AdapterError as exc:
        raise RunnerError("adapter_incompatible", str(exc), 2)


def _parser():
    parser = argparse.ArgumentParser(prog="kimiflow", description="Optional embedded-first Kimiflow terminal runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="start and autonomously drive an explicit Kimiflow task")
    run.add_argument("task", nargs="?")
    run.add_argument("--prompt", dest="prompt")
    run.add_argument("--root")
    run.add_argument("--pretty", action="store_true")
    _add_adapter_arguments(run)
    resume = subparsers.add_parser("resume", help="resume an interrupted or material-wait terminal run")
    resume.add_argument("--message")
    resume.add_argument("--root")
    resume.add_argument("--pretty", action="store_true")
    _add_adapter_arguments(resume)
    status = subparsers.add_parser("status", help="show runner and shared active-run status")
    status.add_argument("--root")
    status.add_argument("--pretty", action="store_true")
    check = subparsers.add_parser("adapter-check", help="validate a command adapter without starting a model turn")
    check.add_argument("--adapter-command", required=True)
    check.add_argument("--model")
    check.add_argument("--model-role", action="append", default=[])
    check.add_argument("--require-feature", action="append", default=[])
    check.add_argument("--pretty", action="store_true")
    check.set_defaults(adapter="command", events_jsonl=False)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    events_jsonl = getattr(args, "events_jsonl", False)

    def emit_event(event):
        sys.stdout.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    try:
        if args.command == "run":
            if args.task and args.prompt:
                raise RunnerError("task_ambiguous", "use either the positional task or --prompt", 2)
            result = run_task(
                args.root, args.prompt or args.task or "",
                adapter=_adapter_from_args(args, emit_event if events_jsonl else None),
            )
        elif args.command == "resume":
            result = resume_task(
                args.root, message=args.message,
                adapter=_adapter_from_args(args, emit_event if events_jsonl else None),
            )
        elif args.command == "adapter-check":
            adapter = _adapter_from_args(args)
            info = model_adapter.info_for(adapter)
            result = {
                "schema_version": 1,
                "status": "compatible",
                "adapter": info,
                "adapter_contract": _adapter_contract(adapter),
            }
        else:
            result = runner_status(args.root)
        code = exit_code(result)
    except RunnerError as exc:
        result = {"schema_version": 1, "status": exc.status, "error": exc.message}
        code = exc.code
    except model_adapter.AdapterError as exc:
        result = {"schema_version": 1, "status": "adapter_incompatible", "error": str(exc)}
        code = 2
    if events_jsonl:
        result = {"schema_version": 1, "type": "run.result", "result": result}
    output = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":"))
    try:
        sys.stdout.write(output + "\n")
    except BrokenPipeError:
        return code or 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
