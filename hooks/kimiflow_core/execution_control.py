"""Bounded, run-local progress, budget, and graph-trace control."""

import contextlib
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone

from . import flow_graph

try:
    import fcntl
except ImportError:  # pragma: no cover - Kimiflow's supported hosts are POSIX.
    fcntl = None


class ExecutionControlError(ValueError):
    pass


TRACE_NAME = "EXECUTION-TRACE.json"
LOCK_NAME = ".execution-control.lock"
MAX_TRACE_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_COUNTER = 1000000000000
MAX_ACCEPTED_EVIDENCE = 2048
OUTCOMES = {"neutral", "progress", "passed", "failed", "no_progress"}
EVENTS = {"turn_completed", "phase_read", "verification", "review", "build", "plan", "research", "finish", "recovery", "observation"}
ACCEPTED_EVIDENCE_OUTCOMES = {"progress", "passed", "failed"}
USAGE_KEYS = ("model_calls", "tool_calls", "input_tokens", "output_tokens")
STATE_PROGRESS_KEYS = (
    "Build risk",
    "Recovery",
    "Review gate",
    "Review epoch",
    "Strategy fingerprint",
    "Conformance basis",
    "Frontend quality basis",
)
SUMMARY_KEYS = {
    "sequence",
    "semantic_fingerprint",
    "last_evidence_fingerprint",
    "accepted_evidence_fingerprints",
    "no_progress_streak",
    "profile",
    "profile_reason",
    "strategy_mode",
    "budget_pressure",
    "directive",
    "pending_stop_coverage",
    "work_units",
    "budget_score",
    "dropped_entries",
    "usage",
    "last_node",
}
ENTRY_KEYS = {
    "sequence",
    "at",
    "kind",
    "event",
    "outcome",
    "from_node",
    "current_node",
    "action",
    "target_node",
    "reason",
    "profile",
    "profile_reason",
    "strategy_mode",
    "budget_pressure",
    "directive",
    "no_progress_streak",
    "work_units",
    "budget_score",
    "usage",
    "evidence_fingerprint",
    "executed",
}


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trace_path(run_dir):
    return os.path.join(run_dir, TRACE_NAME)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha(value):
    if not isinstance(value, bytes):
        value = _json_bytes(value)
    return hashlib.sha256(value).hexdigest()


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _regular_file(run_descriptor, name, missing_ok=False):
    try:
        info = os.stat(name, dir_fd=run_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ExecutionControlError("execution_trace_missing")
    except OSError as exc:
        raise ExecutionControlError("execution_trace_unreadable: %s" % exc)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ExecutionControlError("execution_trace_not_regular")
    if info.st_size > MAX_TRACE_BYTES:
        raise ExecutionControlError("execution_trace_oversize")
    return info


def _open_run(root, run_dir, active):
    expected_parent = os.path.realpath(os.path.join(root, ".kimiflow"))
    if os.path.realpath(os.path.dirname(run_dir)) != expected_parent:
        raise ExecutionControlError("execution_run_outside_kimiflow")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(run_dir, flags)
        info = os.fstat(descriptor)
    except OSError as exc:
        raise ExecutionControlError("execution_run_unreadable: %s" % exc)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise ExecutionControlError("execution_run_not_directory")
    expected_device = (active or {}).get("run_device")
    expected_inode = (active or {}).get("run_inode")
    if expected_device is not None or expected_inode is not None:
        if (info.st_dev, info.st_ino) != (expected_device, expected_inode):
            os.close(descriptor)
            raise ExecutionControlError("execution_run_identity_changed")
    return descriptor


@contextlib.contextmanager
def _locked(root, run_dir, active, create=True):
    if fcntl is None:
        raise ExecutionControlError("execution_control_requires_posix_locking")
    run_descriptor = _open_run(root, run_dir, active)
    try:
        info = _regular_file(run_descriptor, LOCK_NAME, missing_ok=True)
    except BaseException:
        os.close(run_descriptor)
        raise
    if info is not None:
        if info.st_size:
            os.close(run_descriptor)
            raise ExecutionControlError("execution_lock_malformed")
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(LOCK_NAME, flags, 0o600, dir_fd=run_descriptor)
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ExecutionControlError("execution_lock_not_regular")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield run_descriptor
    except OSError as exc:
        raise ExecutionControlError("execution_lock_failed: %s" % exc)
    finally:
        if "descriptor" in locals():
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        os.close(run_descriptor)


def _read_document(run_descriptor, missing_ok=False):
    info = _regular_file(run_descriptor, TRACE_NAME, missing_ok=missing_ok)
    if info is None:
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(TRACE_NAME, flags, dir_fd=run_descriptor)
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_size > MAX_TRACE_BYTES
            or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise ExecutionControlError("execution_trace_unsafe")
        payload = b""
        while len(payload) <= MAX_TRACE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_TRACE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        if len(payload) > MAX_TRACE_BYTES:
            raise ExecutionControlError("execution_trace_oversize")
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        if isinstance(exc, ExecutionControlError):
            raise
        raise ExecutionControlError("execution_trace_malformed: %s" % exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _validate_document(value)
    return value


def _atomic_document(run_descriptor, value):
    if _regular_file(run_descriptor, TRACE_NAME, missing_ok=True) is not None:
        _regular_file(run_descriptor, TRACE_NAME)
    _validate_document(value)
    payload = _json_bytes(value)
    if len(payload) > MAX_TRACE_BYTES:
        raise ExecutionControlError("execution_trace_oversize")
    temporary = ".%s.tmp.%s" % (TRACE_NAME, os.urandom(12).hex())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=run_descriptor)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, TRACE_NAME, src_dir_fd=run_descriptor, dst_dir_fd=run_descriptor)
        # The replace is the commit point. A directory-fsync failure after it is
        # ambiguous to callers and must not invite a retry that double-counts an
        # observation already visible on disk.
        try:
            os.fsync(run_descriptor)
        except OSError:
            pass
    except (OSError, ValueError) as exc:
        try:
            os.unlink(temporary, dir_fd=run_descriptor)
        except OSError:
            pass
        raise ExecutionControlError("execution_trace_write_failed: %s" % exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validate_document(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("contract") != 1:
        raise ExecutionControlError("execution_trace_contract_invalid")
    if set(value) != {"schema_version", "contract", "summary", "entries"}:
        raise ExecutionControlError("execution_trace_shape_invalid")
    summary = value.get("summary")
    entries = value.get("entries")
    if not isinstance(summary, dict) or set(summary) != SUMMARY_KEYS or not isinstance(entries, list):
        raise ExecutionControlError("execution_trace_shape_invalid")
    if summary.get("profile") not in ("compact", "standard", "critical"):
        raise ExecutionControlError("execution_profile_invalid")
    profile_reasons = {
        "small_scope",
        "medium_or_large_scope",
        "material_build_risk",
        "hard_budget_pressure",
    }
    if summary.get("profile_reason") not in profile_reasons:
        raise ExecutionControlError("execution_profile_reason_invalid")
    if summary.get("strategy_mode") not in ("normal", "recovery"):
        raise ExecutionControlError("execution_strategy_mode_invalid")
    if summary.get("budget_pressure") not in ("normal", "soft", "hard"):
        raise ExecutionControlError("execution_budget_pressure_invalid")
    if summary.get("directive") not in ("normal", "prune_optional_work"):
        raise ExecutionControlError("execution_directive_invalid")
    if summary.get("pending_stop_coverage") is not True and summary.get("pending_stop_coverage") is not False:
        raise ExecutionControlError("execution_stop_coverage_invalid")
    sequence = summary.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= MAX_COUNTER:
        raise ExecutionControlError("execution_sequence_invalid")
    if len(entries) > 2048 or not entries or len(entries) > sequence:
        raise ExecutionControlError("execution_trace_sequence_invalid")
    for key in ("no_progress_streak", "work_units", "budget_score", "dropped_entries"):
        item = summary.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_COUNTER:
            raise ExecutionControlError("execution_summary_counter_invalid")
    for key in ("semantic_fingerprint", "last_evidence_fingerprint"):
        item = summary.get(key)
        if item is not None and re.fullmatch(r"[0-9a-f]{64}", str(item)) is None:
            raise ExecutionControlError("execution_summary_fingerprint_invalid")
    usage = summary.get("usage")
    if not isinstance(usage, dict) or set(usage) != set(USAGE_KEYS):
        raise ExecutionControlError("execution_usage_invalid")
    if any(
        isinstance(usage.get(key), bool)
        or not isinstance(usage.get(key), int)
        or not 0 <= usage.get(key) <= MAX_COUNTER
        for key in USAGE_KEYS
    ):
        raise ExecutionControlError("execution_usage_invalid")
    accepted_evidence = summary.get("accepted_evidence_fingerprints")
    if (
        not isinstance(accepted_evidence, list)
        or len(accepted_evidence) > MAX_ACCEPTED_EVIDENCE
        or len(set(accepted_evidence)) != len(accepted_evidence)
        or any(re.fullmatch(r"[0-9a-f]{64}", str(item)) is None for item in accepted_evidence)
    ):
        raise ExecutionControlError("execution_accepted_evidence_invalid")
    first_sequence = sequence - len(entries) + 1
    if summary.get("dropped_entries") != first_sequence - 1:
        raise ExecutionControlError("execution_trace_rollup_invalid")
    for expected, entry in enumerate(entries, first_sequence):
        if (
            not isinstance(entry, dict)
            or set(entry) != ENTRY_KEYS
            or isinstance(entry.get("sequence"), bool)
            or entry.get("sequence") != expected
            or entry.get("kind") not in {"start", "observation", "node_transition", "state_transition"}
            or entry.get("profile") not in ("compact", "standard", "critical")
            or entry.get("profile_reason") not in profile_reasons
            or entry.get("strategy_mode") not in ("normal", "recovery")
            or entry.get("budget_pressure") not in ("normal", "soft", "hard")
            or entry.get("directive") not in ("normal", "prune_optional_work")
            or entry.get("executed") is not (entry.get("kind") == "node_transition")
        ):
            raise ExecutionControlError("execution_trace_entry_invalid")
        event = entry.get("event")
        allowed_events = EVENTS | {"start", "node_transition", "state_transition"}
        if event not in allowed_events or entry.get("outcome") not in OUTCOMES:
            raise ExecutionControlError("execution_trace_entry_invalid")
        evidence = entry.get("evidence_fingerprint")
        if evidence is not None and re.fullmatch(r"[0-9a-f]{64}", str(evidence)) is None:
            raise ExecutionControlError("execution_evidence_fingerprint_invalid")
        entry_usage = entry.get("usage")
        if not isinstance(entry_usage, dict) or set(entry_usage) != set(USAGE_KEYS):
            raise ExecutionControlError("execution_trace_entry_invalid")
        if any(
            isinstance(entry_usage.get(key), bool)
            or not isinstance(entry_usage.get(key), int)
            or not 0 <= entry_usage.get(key) <= MAX_COUNTER
            for key in USAGE_KEYS
        ):
            raise ExecutionControlError("execution_trace_entry_invalid")
        entry_budget = entry.get("budget_score")
        if isinstance(entry_budget, bool) or not isinstance(entry_budget, int) or not 0 <= entry_budget <= MAX_COUNTER:
            raise ExecutionControlError("execution_trace_entry_invalid")
    latest = entries[-1]
    rollup_keys = (
        "profile",
        "profile_reason",
        "strategy_mode",
        "budget_pressure",
        "directive",
        "no_progress_streak",
        "work_units",
        "budget_score",
        "usage",
    )
    if any(latest.get(key) != summary.get(key) for key in rollup_keys):
        raise ExecutionControlError("execution_trace_rollup_invalid")
    if latest.get("current_node") != summary.get("last_node"):
        raise ExecutionControlError("execution_trace_rollup_invalid")
    if summary.get("budget_score") != _budget_score(summary["work_units"], summary["usage"]):
        raise ExecutionControlError("execution_trace_rollup_invalid")


def _config():
    try:
        graph = flow_graph.load_graph()
    except flow_graph.FlowGraphError as exc:
        raise ExecutionControlError(str(exc))
    config = graph.get("execution_control")
    if graph.get("schema_version") != 1 or not isinstance(config, dict):
        raise ExecutionControlError("execution_control_manifest_missing")
    return config


def _state_values(run_descriptor):
    wanted = {key.lower(): key for key in STATE_PROGRESS_KEYS}
    wanted.update({("phase %s" % index): "Phase %s" % index for index in range(8)})
    values = {label: "" for label in wanted.values()}
    descriptor = None
    try:
        info = os.stat("STATE.md", dir_fd=run_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TRACE_BYTES:
            raise ExecutionControlError("execution_state_unsafe")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open("STATE.md", flags, dir_fd=run_descriptor)
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ExecutionControlError("execution_state_unsafe")
        seen = set()
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = None
            for raw in handle:
                plain = re.sub(r"^[ \t]*-[ \t]*", "", raw.replace("**", "").strip())
                label, separator, content = plain.partition(":")
                normalized = label.strip().lower()
                if separator and normalized in wanted:
                    if normalized in seen:
                        raise ExecutionControlError("execution_state_duplicate_key: %s" % normalized)
                    seen.add(normalized)
                    values[wanted[normalized]] = content.strip()
    except (OSError, UnicodeError) as exc:
        raise ExecutionControlError("execution_state_unreadable: %s" % exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return values


def _semantic_fingerprint(run_descriptor, active, item_counts, active_status=None, phase_overrides=None):
    values = _state_values(run_descriptor)
    for key, value in (phase_overrides or {}).items():
        values[key] = value
    payload = {
        "active_status": str(active_status if active_status is not None else (active or {}).get("status", "active")),
        "phases": [values["Phase %s" % index] for index in range(8)],
        "workflow": {key: values[key] for key in STATE_PROGRESS_KEYS},
        "items": {
            key: int((item_counts or {}).get(key, 0) or 0)
            for key in ("pending", "built", "accepted", "rejected", "dropped", "open")
        },
    }
    return _sha(payload), values


def _evidence_fingerprint(root, run_dir, run_descriptor, evidence):
    if not evidence:
        return None
    candidate = evidence if os.path.isabs(evidence) else os.path.join(root, evidence)
    candidate = os.path.normpath(candidate)
    run_real = os.path.realpath(run_dir)
    parent = os.path.dirname(candidate)
    if os.path.realpath(parent) != run_real:
        raise ExecutionControlError("evidence_must_be_a_run_artifact")
    name = os.path.basename(candidate)
    try:
        info = os.stat(name, dir_fd=run_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise ExecutionControlError("evidence_unreadable: %s" % exc)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > MAX_EVIDENCE_BYTES:
        raise ExecutionControlError("evidence_must_be_a_small_regular_file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(name, flags, dir_fd=run_descriptor)
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_size > MAX_EVIDENCE_BYTES
            or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise ExecutionControlError("evidence_must_be_a_small_regular_file")
        payload = b""
        while len(payload) <= MAX_EVIDENCE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_EVIDENCE_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        if len(payload) > MAX_EVIDENCE_BYTES:
            raise ExecutionControlError("evidence_oversize")
        try:
            text = payload.decode("utf-8")
        except UnicodeError:
            normalized = payload
        else:
            if name.lower().endswith(".json"):
                try:
                    normalized = _json_bytes(json.loads(text, object_pairs_hook=_reject_duplicate_pairs))
                except (ValueError, json.JSONDecodeError):
                    without_comments = _strip_html_comments(text)
                    normalized = re.sub(r"\s+", " ", without_comments).strip().encode("utf-8")
                except RecursionError as exc:
                    raise ExecutionControlError("evidence_json_too_deep: %s" % exc)
            else:
                without_comments = _strip_html_comments(text)
                normalized = re.sub(r"\s+", " ", without_comments).strip().encode("utf-8")
        return _sha(normalized)
    except OSError as exc:
        raise ExecutionControlError("evidence_unreadable: %s" % exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _usage(values=None, previous=None):
    totals = {key: int((previous or {}).get(key, 0) or 0) for key in USAGE_KEYS}
    for key in USAGE_KEYS:
        value = int((values or {}).get(key, 0) or 0)
        if value < 0 or value > MAX_COUNTER or totals[key] > MAX_COUNTER - value:
            raise ExecutionControlError("usage values must be bounded non-negative integers")
        totals[key] += value
    return totals


def _strip_html_comments(text):
    pieces = []
    cursor = 0
    while True:
        start = text.find("<!--", cursor)
        if start < 0:
            pieces.append(text[cursor:])
            break
        pieces.append(text[cursor:start])
        end = text.find("-->", start + 4)
        if end < 0:
            break
        cursor = end + 3
    return "".join(pieces)


def _budget_score(work_units, usage):
    tokens = usage["input_tokens"] + usage["output_tokens"]
    return work_units + (usage["model_calls"] * 2) + (usage["tool_calls"] // 4) + (tokens // 8000)


def _profile(scope, state_values, budget_pressure="normal"):
    risk = state_values.get("Build risk", "").strip().lower()
    if risk and not (risk == "none" or risk.startswith("none ") or risk.startswith("none—") or risk.startswith("none —")):
        return "critical", "material_build_risk"
    if budget_pressure == "hard":
        return "compact", "hard_budget_pressure"
    if str(scope).strip().lower() == "small":
        return "compact", "small_scope"
    return "standard", "medium_or_large_scope"


def _pressure(config, scope, score):
    scope = str(scope).strip().lower()
    if scope not in config["budgets"]:
        scope = "medium"
    budget = config["budgets"][scope]
    if score >= budget["hard_work_units"]:
        return "hard"
    if score >= budget["soft_work_units"]:
        return "soft"
    return "normal"


def _entry(sequence, kind, transition, summary, event, outcome, evidence=None, from_node=None):
    transition = transition or {}
    return {
        "sequence": sequence,
        "at": iso_now(),
        "kind": kind,
        "event": event,
        "outcome": outcome,
        "from_node": from_node,
        "current_node": transition.get("current_node"),
        "action": transition.get("action"),
        "target_node": transition.get("target_node"),
        "reason": transition.get("reason"),
        "profile": summary["profile"],
        "profile_reason": summary["profile_reason"],
        "strategy_mode": summary["strategy_mode"],
        "budget_pressure": summary["budget_pressure"],
        "directive": summary["directive"],
        "no_progress_streak": summary["no_progress_streak"],
        "work_units": summary["work_units"],
        "budget_score": summary["budget_score"],
        "usage": dict(summary["usage"]),
        "evidence_fingerprint": evidence,
        "executed": kind == "node_transition",
    }


def _observed_node_changes(previous_node, current_node):
    if not previous_node or not current_node or previous_node == current_node:
        return []
    try:
        graph = flow_graph.load_graph()
    except flow_graph.FlowGraphError as exc:
        raise ExecutionControlError(str(exc))

    previous_match = re.fullmatch(r"phase_([0-7])", str(previous_node))
    current_match = re.fullmatch(r"phase_([0-7])", str(current_node))
    rows = []
    if previous_match and current_match and int(previous_match.group(1)) < int(current_match.group(1)):
        for index in range(int(previous_match.group(1)), int(current_match.group(1))):
            edge = graph["edge_index"].get(("phase_%s" % index, "phase_done"))
            if edge is None:
                rows = []
                break
            rows.append(edge)
    else:
        direct = [
            row
            for row in graph["transitions"]
            if row["from"] == previous_node and row["to"] == current_node
        ]
        if direct and len({row["action"] for row in direct}) == 1:
            rows = [direct[0]]

    if rows:
        return [
            (
                "node_transition",
                {
                    "current_node": row["from"],
                    "action": row["action"],
                    "target_node": row["to"],
                    "reason": "observed_durable_state",
                },
            )
            for row in rows
        ]
    return [
        (
            "state_transition",
            {
                "current_node": current_node,
                "action": "observe_state_transition",
                "target_node": current_node,
                "reason": "observed_durable_state_without_unique_edge",
            },
        )
    ]


def _append_observation(summary, entries, config, transition, event, outcome, evidence_hash=None):
    previous_node = summary.get("last_node")
    current_node = (transition or {}).get("current_node")
    sequence = int(summary.get("sequence", 0))
    for node_kind, node_transition in _observed_node_changes(previous_node, current_node):
        if sequence >= MAX_COUNTER:
            raise ExecutionControlError("execution_sequence_exhausted")
        sequence += 1
        node_summary = dict(summary)
        node_summary["sequence"] = sequence
        entries.append(
            _entry(
                sequence,
                node_kind,
                node_transition,
                node_summary,
                node_kind,
                "progress",
                from_node=node_transition.get("current_node") if node_kind == "node_transition" else previous_node,
            )
        )
    if sequence >= MAX_COUNTER:
        raise ExecutionControlError("execution_sequence_exhausted")
    sequence += 1
    summary["sequence"] = sequence
    summary["last_node"] = current_node
    entries.append(_entry(sequence, "observation", transition, summary, event, outcome, evidence_hash))
    overflow = max(0, len(entries) - config["max_trace_entries"])
    if overflow:
        entries = entries[overflow:]
        dropped = int(summary.get("dropped_entries", 0))
        if dropped > MAX_COUNTER - overflow:
            raise ExecutionControlError("execution_counter_exhausted")
        summary["dropped_entries"] = dropped + overflow
    return entries


def _selector(active):
    value = str((active or {}).get("execution_contract", "")).strip()
    return value if value else None


def state_contract(run_dir):
    path = os.path.join(run_dir, "STATE.md")
    value = ""
    seen = False
    descriptor = None
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TRACE_BYTES:
            return ""
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            return ""
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = None
            for raw in handle:
                plain = re.sub(r"^[ \t]*-[ \t]*", "", raw.replace("**", "").strip())
                label, separator, content = plain.partition(":")
                if separator and label.strip().lower() == "execution contract":
                    if seen:
                        return "invalid_duplicate"
                    seen = True
                    value = content.strip().split(" ", 1)[0]
    except (OSError, UnicodeError):
        return ""
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return value


def contract_enabled(active):
    return _selector(active) == "1"


def trace_present(root, run_dir, active):
    run_descriptor = _open_run(root, run_dir, active)
    try:
        return _regular_file(run_descriptor, TRACE_NAME, missing_ok=True) is not None
    finally:
        os.close(run_descriptor)


def _require_selector(run_dir, active):
    active_value = _selector(active)
    state_value = state_contract(run_dir)
    if active_value != "1" or state_value != "1":
        raise ExecutionControlError("execution_contract_selector_mismatch")


def initialize(root, run_dir, active, item_counts, transition, write=False):
    _require_selector(run_dir, active)
    config = _config()
    if not write:
        run_descriptor = _open_run(root, run_dir, active)
        try:
            existing = _read_document(run_descriptor, missing_ok=True)
            if existing is not None:
                if (
                    len(existing["entries"]) > config["max_trace_entries"]
                    or len(existing["summary"]["accepted_evidence_fingerprints"])
                    > MAX_ACCEPTED_EVIDENCE
                ):
                    raise ExecutionControlError("execution_trace_entry_cap_exceeded")
                return existing
            semantic, state_values = _semantic_fingerprint(run_descriptor, active, item_counts)
            usage = _usage()
            profile, profile_reason = _profile(active.get("scope"), state_values)
            summary = {
                "sequence": 1,
                "semantic_fingerprint": semantic,
                "last_evidence_fingerprint": None,
                "accepted_evidence_fingerprints": [],
                "no_progress_streak": 0,
                "profile": profile,
                "profile_reason": profile_reason,
                "strategy_mode": "normal",
                "budget_pressure": "normal",
                "directive": "normal",
                "pending_stop_coverage": False,
                "work_units": 0,
                "budget_score": 0,
                "dropped_entries": 0,
                "usage": usage,
                "last_node": (transition or {}).get("current_node"),
            }
            return {
                "status": "preview",
                "schema_version": 1,
                "contract": 1,
                "summary": summary,
                "entries": [_entry(1, "start", transition, summary, "start", "neutral")],
            }
        finally:
            os.close(run_descriptor)
    with _locked(root, run_dir, active) as run_descriptor:
        existing = _read_document(run_descriptor, missing_ok=True)
        if existing is not None:
            if (
                len(existing["entries"]) > config["max_trace_entries"]
                or len(existing["summary"]["accepted_evidence_fingerprints"]) > MAX_ACCEPTED_EVIDENCE
            ):
                raise ExecutionControlError("execution_trace_entry_cap_exceeded")
            if existing["summary"].get("pending_stop_coverage") is True:
                existing = {
                    "schema_version": 1,
                    "contract": 1,
                    "summary": {**existing["summary"], "pending_stop_coverage": False},
                    "entries": list(existing["entries"]),
                }
                _atomic_document(run_descriptor, existing)
            return existing
        semantic, state_values = _semantic_fingerprint(run_descriptor, active, item_counts)
        usage = _usage()
        profile, profile_reason = _profile(active.get("scope"), state_values)
        summary = {
            "sequence": 1,
            "semantic_fingerprint": semantic,
            "last_evidence_fingerprint": None,
            "accepted_evidence_fingerprints": [],
            "no_progress_streak": 0,
            "profile": profile,
            "profile_reason": profile_reason,
            "strategy_mode": "normal",
            "budget_pressure": "normal",
            "directive": "normal",
            "pending_stop_coverage": False,
            "work_units": 0,
            "budget_score": 0,
            "dropped_entries": 0,
            "usage": usage,
            "last_node": (transition or {}).get("current_node"),
        }
        value = {
            "schema_version": 1,
            "contract": 1,
            "summary": summary,
            "entries": [_entry(1, "start", transition, summary, "start", "neutral")],
        }
        _atomic_document(run_descriptor, value)
        return value


def inspect(root, run_dir, active):
    if not contract_enabled(active):
        return None
    _require_selector(run_dir, active)
    config = _config()
    with _locked(root, run_dir, active, create=False) as run_descriptor:
        value = _read_document(run_descriptor)
    if (
        len(value["entries"]) > config["max_trace_entries"]
        or len(value["summary"]["accepted_evidence_fingerprints"]) > MAX_ACCEPTED_EVIDENCE
    ):
        raise ExecutionControlError("execution_trace_entry_cap_exceeded")
    return value


def observe(
    root,
    run_dir,
    active,
    item_counts,
    transition,
    recovery_transition,
    event="turn_completed",
    outcome="neutral",
    evidence=None,
    model_calls=0,
    tool_calls=0,
    input_tokens=0,
    output_tokens=0,
    coalesce_pending_stop=False,
    write=False,
):
    _require_selector(run_dir, active)
    if event not in EVENTS:
        raise ExecutionControlError("execution_event_invalid")
    if outcome not in OUTCOMES:
        raise ExecutionControlError("execution_outcome_invalid")
    config = _config()
    usage_delta = {
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    with _locked(root, run_dir, active, create=False) as run_descriptor:
        current = _read_document(run_descriptor)
        evidence_hash = _evidence_fingerprint(root, run_dir, run_descriptor, evidence)
        summary = dict(current["summary"])
        entries = list(current["entries"])
        if len(entries) > config["max_trace_entries"] or len(
            summary.get("accepted_evidence_fingerprints", [])
        ) > MAX_ACCEPTED_EVIDENCE:
            raise ExecutionControlError("execution_trace_entry_cap_exceeded")
        semantic, state_values = _semantic_fingerprint(run_descriptor, active, item_counts)
        if coalesce_pending_stop and summary.get("pending_stop_coverage") is True:
            chosen = recovery_transition if summary["strategy_mode"] == "recovery" else transition
            semantic_progress = semantic != summary.get("semantic_fingerprint")
            node_progress = (chosen or {}).get("current_node") != summary.get("last_node")
            summary["pending_stop_coverage"] = False
            if semantic_progress or node_progress:
                summary["semantic_fingerprint"] = semantic
                summary["no_progress_streak"] = 0
                summary["strategy_mode"] = "normal"
                summary["profile"], summary["profile_reason"] = _profile(
                    active.get("scope"), state_values, summary["budget_pressure"]
                )
                chosen = transition
                entries = _append_observation(
                    summary,
                    entries,
                    config,
                    chosen,
                    "turn_completed",
                    "progress",
                )
            value = {
                "schema_version": 1,
                "contract": 1,
                "summary": summary,
                "entries": entries,
                "transition": chosen,
            }
            if write:
                stored = dict(value)
                stored.pop("transition", None)
                _atomic_document(run_descriptor, stored)
            return value
        semantic_progress = semantic != summary.get("semantic_fingerprint")
        accepted_evidence = list(summary.get("accepted_evidence_fingerprints", []))
        evidence_progress = (
            evidence_hash is not None
            and outcome in ACCEPTED_EVIDENCE_OUTCOMES
            and evidence_hash not in accepted_evidence
            and len(accepted_evidence) < MAX_ACCEPTED_EVIDENCE
        )
        progressed = semantic_progress or evidence_progress
        previous_streak = int(summary.get("no_progress_streak", 0))
        if not progressed and previous_streak >= MAX_COUNTER:
            raise ExecutionControlError("execution_counter_exhausted")
        summary["no_progress_streak"] = 0 if progressed else previous_streak + 1
        summary["strategy_mode"] = (
            "recovery" if summary["no_progress_streak"] >= config["no_progress_limit"] else "normal"
        )
        summary["semantic_fingerprint"] = semantic
        if evidence_progress:
            summary["last_evidence_fingerprint"] = evidence_hash
            accepted_evidence.append(evidence_hash)
        summary["accepted_evidence_fingerprints"] = accepted_evidence
        previous_work_units = int(summary.get("work_units", 0))
        if previous_work_units >= MAX_COUNTER:
            raise ExecutionControlError("execution_counter_exhausted")
        summary["work_units"] = previous_work_units + 1
        summary["usage"] = _usage(usage_delta, summary.get("usage"))
        summary["budget_score"] = _budget_score(summary["work_units"], summary["usage"])
        if summary["budget_score"] > MAX_COUNTER:
            raise ExecutionControlError("execution_budget_counter_exhausted")
        summary["budget_pressure"] = _pressure(config, active.get("scope"), summary["budget_score"])
        summary["profile"], summary["profile_reason"] = _profile(
            active.get("scope"), state_values, summary["budget_pressure"]
        )
        summary["directive"] = "prune_optional_work" if summary["budget_pressure"] == "hard" else "normal"
        summary["pending_stop_coverage"] = event != "turn_completed"
        chosen = recovery_transition if summary["strategy_mode"] == "recovery" else transition
        entries = _append_observation(summary, entries, config, chosen, event, outcome, evidence_hash)
        value = {"schema_version": 1, "contract": 1, "summary": summary, "entries": entries, "transition": chosen}
        if write:
            stored = dict(value)
            stored.pop("transition", None)
            _atomic_document(run_descriptor, stored)
        return value


def require_finishable(root, run_dir, active, item_counts):
    _require_selector(run_dir, active)
    value = inspect(root, run_dir, active)
    if not value or not value.get("entries"):
        raise ExecutionControlError("execution_control_evidence_missing")
    run_descriptor = _open_run(root, run_dir, active)
    try:
        semantic, _ = _semantic_fingerprint(run_descriptor, active, item_counts)
        preterminal_semantic, _ = _semantic_fingerprint(
            run_descriptor,
            active,
            item_counts,
            phase_overrides={"Phase 7": "in-progress"},
        )
        terminal_semantic, _ = _semantic_fingerprint(
            run_descriptor,
            active,
            item_counts,
            active_status="done",
        )
    finally:
        os.close(run_descriptor)
    transition = flow_graph.resolve_transition(
        run_dir,
        active=active,
        stale={"risk": "current"},
        item_counts=item_counts,
    )
    current_node = transition.get("current_node")
    stored_semantic = value["summary"].get("semantic_fingerprint")
    stored_node = value["summary"].get("last_node")
    if stored_semantic == semantic and stored_node == current_node:
        return value
    terminal_ready = current_node == "phase_7" and transition.get("target_node") == "done"
    if terminal_ready and (
        (stored_node == "phase_7" and stored_semantic == preterminal_semantic)
        or (stored_node == "done" and stored_semantic == terminal_semantic)
    ):
        return value
    raise ExecutionControlError("execution_control_requires_current_observation")


def finalize(root, run_dir, active, item_counts, write=False):
    """Record the successful phase_7 -> done edge without charging another work unit."""
    _require_selector(run_dir, active)
    config = _config()
    terminal_active = dict(active or {})
    terminal_active["status"] = "done"
    terminal_transition = flow_graph.resolve_transition(
        run_dir,
        active=terminal_active,
        stale={"risk": "current"},
        item_counts=item_counts,
    )
    if terminal_transition.get("current_node") != "done":
        raise ExecutionControlError("execution_terminal_transition_invalid")
    with _locked(root, run_dir, active, create=False) as run_descriptor:
        current = _read_document(run_descriptor)
        summary = dict(current["summary"])
        entries = list(current["entries"])
        semantic, state_values = _semantic_fingerprint(
            run_descriptor,
            active,
            item_counts,
            active_status="done",
        )
        if summary.get("last_node") == "done" and summary.get("semantic_fingerprint") == semantic:
            value = {
                "schema_version": 1,
                "contract": 1,
                "summary": summary,
                "entries": entries,
                "transition": terminal_transition,
            }
            return value
        summary["semantic_fingerprint"] = semantic
        summary["no_progress_streak"] = 0
        summary["strategy_mode"] = "normal"
        summary["pending_stop_coverage"] = False
        summary["profile"], summary["profile_reason"] = _profile(
            active.get("scope"), state_values, summary["budget_pressure"]
        )
        entries = _append_observation(
            summary,
            entries,
            config,
            terminal_transition,
            "finish",
            "passed",
        )
        value = {
            "schema_version": 1,
            "contract": 1,
            "summary": summary,
            "entries": entries,
            "transition": terminal_transition,
        }
        if write:
            stored = dict(value)
            stored.pop("transition", None)
            _atomic_document(run_descriptor, stored)
        return value


def annotate_transition(transition, control):
    if control is None:
        return transition
    result = dict(transition)
    summary = control["summary"]
    result["execution"] = {
        "contract": 1,
        "profile": summary["profile"],
        "profile_reason": summary["profile_reason"],
        "strategy_mode": summary["strategy_mode"],
        "budget_pressure": summary["budget_pressure"],
        "directive": summary["directive"],
        "no_progress_streak": summary["no_progress_streak"],
        "work_units": summary["work_units"],
        "usage": dict(summary["usage"]),
    }
    return result
