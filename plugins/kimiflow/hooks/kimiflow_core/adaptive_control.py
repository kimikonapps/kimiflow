"""Deterministic, local-first adaptive policy for Kimiflow runs.

The module deliberately does not call a model or a network service.  It turns
already-persisted run evidence into small fail-closed receipts.
"""

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone

from memory_router import store as memory_store

from . import paths

try:
    import fcntl
except ImportError:  # pragma: no cover - supported hosts are POSIX.
    fcntl = None


class AdaptiveControlError(ValueError):
    pass


CLASSIFICATION_NAME = "ADAPTIVE-CLASSIFICATION.json"
ROLLOVER_NAME = "CONTEXT-ROLLOVER.json"
MODEL_LEDGER_NAME = "MODEL-OUTCOMES.jsonl"
MODEL_POLICY_NAME = "MODEL-ROUTING-POLICY.json"
MODEL_ROUTE_EVIDENCE_NAME = "MODEL-ROUTE-EVIDENCE.json"
RETRIEVAL_LEDGER_NAME = "RETRIEVAL-OUTCOMES.jsonl"
REVIEW_LEDGER_NAME = "REVIEW-OUTCOMES.jsonl"
REVIEW_CASCADE_LEDGER_NAME = "REVIEW-CASCADE-OUTCOMES.jsonl"
REVIEW_CASCADE_ROUTE_NAME = "REVIEW-CASCADE-ROUTE.json"
VARIANT_LEDGER_NAME = "EXECUTION-VARIANT-OUTCOMES.jsonl"
HOST_USAGE_NAME = "HOST-USAGE.json"
EXECUTION_TRACE_NAME = "EXECUTION-TRACE.json"
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_LEDGER_ROWS = 4096
MODEL_MIN_SAMPLES = 5
SCOPE_RANK = {"trivial": 0, "small": 1, "large": 2}
ROLE_KEYS = ("top", "balanced", "cheap", "cross_family_top")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ROLLOVER_ID_RE = re.compile(r"^roll_[0-9a-f]{32}$")
SAMPLE_ID_RE = re.compile(r"^sample_[0-9a-f]{24}$")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_bytes(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _read_bytes(path, maximum=MAX_ARTIFACT_BYTES, missing_ok=False):
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise AdaptiveControlError("artifact_missing:%s" % os.path.basename(path))
    except OSError as exc:
        raise AdaptiveControlError("artifact_unreadable:%s" % exc.__class__.__name__)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise AdaptiveControlError("artifact_unsafe:%s" % os.path.basename(path))
    if info.st_size > maximum:
        raise AdaptiveControlError("artifact_oversize:%s" % os.path.basename(path))
    try:
        with open(path, "rb") as handle:
            payload = handle.read(maximum + 1)
    except OSError as exc:
        raise AdaptiveControlError("artifact_unreadable:%s" % exc.__class__.__name__)
    if len(payload) > maximum:
        raise AdaptiveControlError("artifact_oversize:%s" % os.path.basename(path))
    return payload


def _read_text(path, missing_ok=False):
    payload = _read_bytes(path, missing_ok=missing_ok)
    if payload is None:
        return ""
    try:
        return payload.decode("utf-8")
    except UnicodeError:
        raise AdaptiveControlError("artifact_encoding:%s" % os.path.basename(path))


def _read_json(path, maximum=MAX_ARTIFACT_BYTES, missing_ok=False):
    payload = _read_bytes(path, maximum=maximum, missing_ok=missing_ok)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise AdaptiveControlError("json_malformed:%s" % os.path.basename(path))


def _write_run_receipt(root, run_dir, name, value):
    try:
        with memory_store.local_path_guard(os.path.realpath(root), run_dir):
            memory_store.atomic_write(
                os.path.join(run_dir, name),
                _json_bytes(value).decode("utf-8"),
                mode=0o600,
                refuse_symlink=True,
                durable=True,
            )
    except (memory_store.ConcurrentWriteError, OSError, ValueError) as exc:
        raise AdaptiveControlError(
            "run_receipt_unsafe:%s:%s" % (name, exc.__class__.__name__)
        )


def _safe_run(root, run):
    root = os.path.realpath(root)
    run_dir = os.path.realpath(run if os.path.isabs(run) else os.path.join(root, run))
    parent = os.path.realpath(os.path.join(root, ".kimiflow"))
    if os.path.dirname(run_dir) != parent or not os.path.isdir(run_dir):
        raise AdaptiveControlError("run_invalid")
    return run_dir


def _state_value(text, key):
    match = re.search(r"^%s:[ \t]*(.+?)\s*$" % re.escape(key), text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _affected_paths(state):
    result = []
    active = False
    for line in state.splitlines():
        if re.match(r"^Affected (files|paths):\s*$", line, re.IGNORECASE):
            active = True
            continue
        if active:
            match = re.match(r"^[ \t]*-[ \t]+(.+?)\s*$", line)
            if not match:
                if line.strip():
                    break
                continue
            rel = match.group(1).strip().replace("\\", "/")
            if rel and not rel.startswith("/") and ".." not in rel.split("/"):
                result.append(rel)
    return sorted(set(result))


def _prefixes(affected):
    return sorted({path.split("/", 1)[0] for path in affected if path})


def _semantic_state(state, affected):
    """Keep classifier freshness independent from phase/status bookkeeping."""
    rows = []
    for key in (
        "Mode",
        "Scope",
        "Feature",
        "Target",
        "Product decision",
        "Product choice",
        "Business choice",
        "Policy choice",
    ):
        value = _state_value(state, key)
        if value:
            rows.append("%s: %s" % (key, value))
    rows.append("Affected files:")
    rows.extend("- %s" % path for path in affected)
    return "\n".join(rows)


def _positive_signal_text(text):
    """Remove explicit non-goals before conservative keyword classification."""
    kept = []
    excluded_block = False
    exclusion = re.compile(
        r"^(?:#{1,6}[ \t]+)?(?:out[ -]of[ -]scope|non[- ]?goals?|excluded|"
        r"außerhalb des (?:umfangs|scopes)|nicht im (?:umfang|scope))"
        r"(?:[ \t]*:|[ \t]*$)",
        re.IGNORECASE,
    )
    negation = re.compile(
        r"\b(?:without|no|not|never|kein(?:e|en|er|es)?|ohne)\b",
        re.IGNORECASE,
    )
    for raw in text.splitlines():
        line = raw.strip()
        if re.match(r"^#{1,6}[ \t]+", line):
            excluded_block = False
        match = exclusion.match(line)
        if match:
            tail = line[match.end():].strip()
            excluded_block = not tail
            continue
        if excluded_block:
            continue
        for clause in re.split(r"(?:[.;]|[ \t]+(?:but|aber|jedoch)[ \t]+)", raw):
            if clause.strip() and negation.search(clause) is None:
                kept.append(clause)
    return "\n".join(kept)


def _has(text, patterns):
    return any(re.search(pattern, text, re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def classify(root, run):
    """Classify only observable run signals; never choose a product answer."""
    run_dir = _safe_run(root, run)
    state = _read_text(os.path.join(run_dir, "STATE.md"))
    intent = _read_text(os.path.join(run_dir, "INTENT.md"), missing_ok=True)
    problem = _read_text(os.path.join(run_dir, "PROBLEM.md"), missing_ok=True)
    affected = _affected_paths(state)
    combined = "\n".join((_semantic_state(state, affected), intent, problem))
    signal_text = _positive_signal_text(combined)
    prefixes = _prefixes(affected)
    declared = _state_value(state, "Scope").lower().split(" ", 1)[0]
    if declared not in SCOPE_RANK:
        declared = "small"

    data_signal = _has(
        signal_text,
        (
            r"\b(schema|migration|database|durable data|data loss|retention)\b",
            r"(migrations?|schemas?|database|storage|persistence)/",
        ),
    )
    public_signal = _has(
        signal_text, (r"\b(public api|breaking change|permission|security|authentication|authorization)\b",)
    )
    irreversible_signal = _has(
        signal_text, (r"\b(irreversible|destructive|delete production|hard[- ]to[- ]reverse)\b",)
    )
    subsystem_signal = len(prefixes) >= 3
    derived = "large" if any((data_signal, public_signal, irreversible_signal, subsystem_signal)) else "small"
    scope = declared if SCOPE_RANK[declared] >= SCOPE_RANK[derived] else derived

    product_open = _has(
        signal_text,
        (
            r"^Product decision:[ \t]*(open|pending)\b",
            r"\b(user_required|needs user decision|unresolved product decision)\b",
            r"\b(product|business|policy) choice:[ \t]*(open|pending)\b",
        ),
    )
    domain_reasons = []
    if _has(signal_text, (r"\b(bounded context|ubiquitous language|domain rule|business rule)\b",)):
        domain_reasons.append("domain_language_or_rules")
    if data_signal and len(prefixes) >= 2:
        domain_reasons.append("cross_boundary_data_contract")
    if _has(signal_text, (r"\b(invariant|state machine|workflow transition)\b",)) and len(prefixes) >= 2:
        domain_reasons.append("cross_boundary_invariant")

    operation_reasons = []
    operation_patterns = (
        (r"\b(production|deploy|release pipeline|ci/cd|runtime)\b", "production_or_deploy"),
        (r"\b(network|webhook|external integration|provider)\b", "network_or_integration"),
        (r"\b(background|queue|worker|scheduled|cron|job)\b", "background_execution"),
        (r"\b(observability|metric|trace|alert|rollback)\b", "operational_control"),
        (r"\b(security|privacy|secret|permission|authentication|authorization)\b", "security_or_privacy"),
    )
    for pattern, reason in operation_patterns:
        if re.search(pattern, signal_text, re.IGNORECASE):
            operation_reasons.append(reason)
    if data_signal:
        operation_reasons.append("durable_data")

    reasons = []
    if subsystem_signal:
        reasons.append("multiple_subsystems")
    if data_signal:
        reasons.append("data_contract")
    if public_signal:
        reasons.append("public_or_security_contract")
    if irreversible_signal:
        reasons.append("irreversibility")
    result = {
        "schema_version": 1,
        "status": "classified",
        "scope": scope,
        "declared_scope": declared,
        "scope_reasons": sorted(set(reasons)),
        "affected_path_count": len(affected),
        "subsystem_count": len(prefixes),
        "product_decision_open": product_open,
        "intent_action": "return_to_intake" if product_open else "continue",
        "domain_complexity": "active" if domain_reasons else "off",
        "domain_reasons": sorted(set(domain_reasons)),
        "operational_impact": "active" if operation_reasons else "off",
        "operational_reasons": sorted(set(operation_reasons)),
        "content_digest": "sha256:" + hashlib.sha256(combined.encode("utf-8")).hexdigest(),
    }
    return result


def write_classification(root, run):
    run_dir = _safe_run(root, run)
    value = classify(root, run_dir)
    _write_run_receipt(root, run_dir, CLASSIFICATION_NAME, value)
    return value


def load_classification(root, run):
    run_dir = _safe_run(root, run)
    value = _read_json(os.path.join(run_dir, CLASSIFICATION_NAME))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("status") != "classified"
        or value.get("scope") not in SCOPE_RANK
        or value.get("domain_complexity") not in ("active", "off")
        or value.get("operational_impact") not in ("active", "off")
        or not isinstance(value.get("product_decision_open"), bool)
        or SHA_RE.fullmatch(str(value.get("content_digest", ""))) is None
    ):
        raise AdaptiveControlError("classification_receipt_invalid")
    current = classify(root, run_dir)
    if value != current:
        raise AdaptiveControlError("classification_receipt_stale")
    return value


def verify_conditional_contract(root, run, stage):
    if stage not in ("plan", "verify"):
        raise AdaptiveControlError("contract_stage_invalid")
    run_dir = _safe_run(root, run)
    classification_path = os.path.join(run_dir, CLASSIFICATION_NAME)
    classification = (
        load_classification(root, run_dir)
        if os.path.exists(classification_path)
        else classify(root, run_dir)
    )
    research = _read_text(os.path.join(run_dir, "RESEARCH.md"), missing_ok=True)
    plan = _read_text(os.path.join(run_dir, "PLAN.md"), missing_ok=True)
    verification = _read_text(os.path.join(run_dir, "VERIFICATION.md"), missing_ok=True)
    blockers = []
    contracts = (
        (
            "domain",
            classification.get("domain_complexity"),
            r"^Domain evidence: context=\S.+; language=\S.+; invariant=\S.+$",
            r"^Domain check: (AC-[0-9]+) -> \S.+$",
            r"^Domain verification: passed; AC=(AC-[0-9]+)$",
        ),
        (
            "operations",
            classification.get("operational_impact"),
            r"^Operational evidence: signals=\S.+; rollback=\S.+; privacy=\S.+$",
            r"^Operational check: (AC-[0-9]+) -> \S.+$",
            r"^Operational verification: passed; AC=(AC-[0-9]+)$",
        ),
    )
    for name, status, evidence_pattern, plan_pattern, verify_pattern in contracts:
        evidence = re.findall(evidence_pattern, research, re.MULTILINE)
        checks = re.findall(plan_pattern, plan, re.MULTILINE)
        verified = re.findall(verify_pattern, verification, re.MULTILINE)
        if status == "active":
            if len(evidence) != 1:
                blockers.append("%s_evidence_missing" % name)
            if len(checks) != 1:
                blockers.append("%s_check_missing" % name)
            if stage == "verify" and (len(verified) != 1 or not checks or verified[0] != checks[0]):
                blockers.append("%s_verification_missing" % name)
        elif evidence or checks or verified:
            blockers.append("%s_unexpected_when_off" % name)
    return {
        "schema_version": 1,
        "status": "OPEN" if not blockers else "CLOSED",
        "stage": stage,
        "blockers": blockers,
        "classification_digest": classification.get("content_digest"),
    }


def _selection_map(shadow):
    result = {}
    for row in shadow.get("selection", []) if isinstance(shadow, dict) else []:
        if not isinstance(row, dict):
            continue
        name = row.get("name")
        digest = row.get("sha256")
        if isinstance(name, str) and SHA_RE.fullmatch(str(digest or "")):
            result[name] = {
                "kind": row.get("kind"),
                "name": name,
                "bytes": row.get("bytes"),
                "sha256": digest,
            }
    return result


def decide_rollover(previous, current, scope, pressure="normal", cumulative_input_tokens=0):
    if not isinstance(previous, dict) or not isinstance(current, dict):
        raise AdaptiveControlError("rollover_shadow_invalid")
    if scope not in SCOPE_RANK or pressure not in ("normal", "soft", "hard"):
        raise AdaptiveControlError("rollover_policy_invalid")
    before = _selection_map(previous)
    after = _selection_map(current)
    all_names = set(before) | set(after)
    changed = sum(1 for name in all_names if before.get(name) != after.get(name))
    change_ratio = changed / max(1, len(all_names))
    phase_changed = previous.get("phase") != current.get("phase")
    estimated = int(current.get("estimated_tokens") or 0)
    measured_pressure = pressure == "hard" or int(cumulative_input_tokens or 0) >= 120000
    material_boundary = (
        scope == "large"
        and phase_changed
        and change_ratio >= 0.60
        and estimated >= 20000
    )
    trigger = measured_pressure or material_boundary
    basis = {
        "previous": previous.get("composite_basis"),
        "current": current.get("composite_basis"),
        "phase": current.get("phase"),
        "pressure": pressure,
    }
    digest = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "status": "pending" if trigger else "off",
        "rollover_id": "roll_" + digest[:32],
        "reason": (
            "measured_context_pressure"
            if measured_pressure
            else "material_phase_context_change"
            if material_boundary
            else "below_threshold"
        ),
        "scope": scope,
        "pressure": pressure,
        "phase": current.get("phase"),
        "previous_digest": previous.get("composite_basis"),
        "current_digest": current.get("composite_basis"),
        "changed_ratio": round(change_ratio, 4),
        "estimated_tokens": estimated,
        "cumulative_input_tokens": int(cumulative_input_tokens or 0),
        "retained": [after[name] for name in sorted(after)],
        "user_gate": False,
    }


def write_rollover(root, run, value):
    run_dir = _safe_run(root, run)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise AdaptiveControlError("rollover_receipt_invalid")
    if value.get("status") == "pending":
        validate_pending_rollover(value)
    _write_run_receipt(root, run_dir, ROLLOVER_NAME, value)
    return value


def validate_pending_rollover(value):
    retained = value.get("retained") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("status") != "pending"
        or ROLLOVER_ID_RE.fullmatch(str(value.get("rollover_id", ""))) is None
        or SHA_RE.fullmatch(str(value.get("current_digest", ""))) is None
        or not isinstance(retained, list)
        or len(retained) > 128
        or value.get("user_gate") is not False
    ):
        raise AdaptiveControlError("rollover_receipt_invalid")
    for row in retained:
        if (
            not isinstance(row, dict)
            or set(row) != {"kind", "name", "bytes", "sha256"}
            or row.get("kind") not in ("phase", "reference", "artifact")
            or not isinstance(row.get("name"), str)
            or not 1 <= len(row["name"]) <= 500
            or any(ord(char) < 32 or ord(char) == 127 for char in row["name"])
            or isinstance(row.get("bytes"), bool)
            or not isinstance(row.get("bytes"), int)
            or not 0 <= row["bytes"] <= MAX_ARTIFACT_BYTES
            or SHA_RE.fullmatch(str(row.get("sha256", ""))) is None
        ):
            raise AdaptiveControlError("rollover_manifest_invalid")
    return value


def retarget_rollover(value, current):
    """Keep an unacknowledged rollover bound to the latest selected context."""
    if (
        not isinstance(value, dict)
        or value.get("status") != "pending"
        or ROLLOVER_ID_RE.fullmatch(str(value.get("rollover_id", ""))) is None
        or not isinstance(current, dict)
        or SHA_RE.fullmatch(str(current.get("composite_basis", ""))) is None
    ):
        raise AdaptiveControlError("rollover_refresh_invalid")
    current_digest = current["composite_basis"]
    if value.get("current_digest") == current_digest:
        return value
    selected = _selection_map(current)
    material = "%s\0%s" % (value["rollover_id"], current_digest)
    updated = dict(value)
    updated.update(
        {
            "status": "pending",
            "rollover_id": "roll_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32],
            "reason": "pending_context_updated",
            "phase": current.get("phase"),
            "previous_digest": value.get("current_digest"),
            "current_digest": current_digest,
            "estimated_tokens": int(current.get("estimated_tokens") or 0),
            "retained": [selected[name] for name in sorted(selected)],
        }
    )
    return updated


def pending_rollover(root, run):
    run_dir = _safe_run(root, run)
    value = _read_json(os.path.join(run_dir, ROLLOVER_NAME), missing_ok=True)
    if not isinstance(value, dict) or value.get("status") != "pending":
        return None
    return validate_pending_rollover(value)


def acknowledge_rollover(root, run, rollover_id, current_digest, before_tokens, after_tokens):
    value = pending_rollover(root, run)
    if value is None:
        raise AdaptiveControlError("rollover_not_pending")
    if value["rollover_id"] != rollover_id or value["current_digest"] != current_digest:
        return {
            "schema_version": 1,
            "status": "stale_acknowledgement",
            "rollover_id": rollover_id,
            "current_digest": current_digest,
            "pending_rollover_id": value["rollover_id"],
            "pending_current_digest": value["current_digest"],
        }
    if (
        isinstance(before_tokens, bool)
        or isinstance(after_tokens, bool)
        or not isinstance(before_tokens, int)
        or not isinstance(after_tokens, int)
        or not 0 <= after_tokens <= before_tokens
    ):
        raise AdaptiveControlError("rollover_token_counts_invalid")
    updated = dict(value)
    updated.update(
        {
            "status": "acknowledged",
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "acknowledged_at": _now(),
        }
    )
    return write_rollover(root, run, updated)


def fallback_rollover(root, run, reason="capability_unavailable"):
    value = pending_rollover(root, run)
    if value is None:
        return None
    if not isinstance(reason, str) or re.fullmatch(r"[a-z0-9_-]{1,64}", reason) is None:
        raise AdaptiveControlError("rollover_fallback_reason_invalid")
    updated = dict(value)
    updated.update(
        {
            "status": "bounded_fallback",
            "fallback_reason": reason,
            "fallback_action": "continue_existing_context_with_bounded_manifest",
            "acknowledged_at": _now(),
        }
    )
    return write_rollover(root, run, updated)


def handoff_rollover(root, run, rollover_id, current_digest):
    value = pending_rollover(root, run)
    if value is None:
        raise AdaptiveControlError("rollover_not_pending")
    if value["rollover_id"] != rollover_id or value["current_digest"] != current_digest:
        raise AdaptiveControlError("rollover_handoff_stale")
    updated = dict(value)
    updated.update(
        {
            "status": "fresh_context_handoff",
            "handoff": "embedded_phase_worker",
            "acknowledged_at": _now(),
        }
    )
    return write_rollover(root, run, updated)


def _model_project(root):
    return os.path.join(root, ".kimiflow", "project")


def _read_project_file(directory_descriptor, name, maximum):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode):
            raise AdaptiveControlError("model_ledger_unsafe")
        if named.st_size > maximum:
            raise AdaptiveControlError("model_ledger_oversize")
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise AdaptiveControlError("model_ledger_unsafe")
        chunks = []
        total = 0
        while total <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > maximum:
            raise AdaptiveControlError("model_ledger_oversize")
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        snapshots = (
            named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns, named.st_ctime_ns,
        ), (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns,
        ), (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        ), (
            current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns,
        )
        if len(set(snapshots)) != 1:
            raise AdaptiveControlError("model_ledger_changed")
        return b"".join(chunks)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AdaptiveControlError(
            "model_ledger_unreadable:%s" % exc.__class__.__name__
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextlib.contextmanager
def _model_ledger_lock(root):
    if fcntl is None:
        raise AdaptiveControlError("model_ledger_lock_unavailable")
    root = os.path.realpath(root)
    project = memory_store.ensure_local_directory(
        root, _model_project(root), mode=0o700,
    )
    descriptor = None
    with memory_store.local_path_guard(root, project) as anchor:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                ".model-outcomes.lock",
                flags,
                0o600,
                dir_fd=anchor["descriptor"],
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise AdaptiveControlError("model_ledger_lock_unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


def _ledger_rows(root):
    root = os.path.realpath(root)
    project = _model_project(root)
    if not os.path.lexists(project):
        return []
    try:
        with memory_store.local_path_guard(root, project) as anchor:
            payload = _read_project_file(
                anchor["descriptor"], MODEL_LEDGER_NAME, MAX_LEDGER_BYTES,
            )
    except (memory_store.ConcurrentWriteError, OSError, ValueError) as exc:
        raise AdaptiveControlError(
            "model_project_unsafe:%s" % exc.__class__.__name__
        )
    if payload is None:
        return []
    rows = []
    for raw in payload.splitlines():
        if not raw.strip():
            continue
        if len(rows) >= MAX_LEDGER_ROWS:
            raise AdaptiveControlError("model_ledger_too_many_rows")
        try:
            row = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise AdaptiveControlError("model_ledger_malformed")
        common_keys = {
            "schema_version", "sample_id", "recorded_at", "role", "model",
            "baseline", "risk", "outcome", "high_findings", "retries",
            "input_tokens", "output_tokens",
        }
        version = row.get("schema_version") if isinstance(row, dict) else None
        expected_keys = common_keys if version == 1 else common_keys | {"evidence_digest"}
        if (
            not isinstance(row, dict)
            or version not in (1, 2)
            or set(row) != expected_keys
            or (
                version == 2
                and SHA_RE.fullmatch(str(row.get("evidence_digest", ""))) is None
            )
            or SAMPLE_ID_RE.fullmatch(str(row.get("sample_id", ""))) is None
            or row.get("role") not in ("balanced", "cheap")
            or row.get("risk") not in ("routine", "critical")
            or row.get("outcome") not in ("passed", "failed")
            or any(
                not isinstance(row.get(key), str)
                or not row.get(key)
                or len(row.get(key)) > 128
                for key in ("model", "baseline")
            )
            or any(
                isinstance(row.get(key), bool)
                or not isinstance(row.get(key), int)
                or row.get(key) < 0
                for key in ("high_findings", "retries", "input_tokens", "output_tokens")
            )
        ):
            raise AdaptiveControlError("model_ledger_contract_invalid")
        rows.append(row)
    return rows


def record_model_outcome(
    root, sample_id, role, model, baseline, risk, outcome, high_findings=0, retries=0,
    input_tokens=0, output_tokens=0, evidence_digest=None,
):
    if not isinstance(sample_id, str) or SAMPLE_ID_RE.fullmatch(sample_id) is None:
        raise AdaptiveControlError("model_outcome_sample_invalid")
    if role not in ("balanced", "cheap") or risk not in ("routine", "critical"):
        raise AdaptiveControlError("model_outcome_class_invalid")
    for value in (model, baseline):
        if not isinstance(value, str) or not value or len(value) > 128:
            raise AdaptiveControlError("model_outcome_identity_invalid")
    if outcome not in ("passed", "failed"):
        raise AdaptiveControlError("model_outcome_invalid")
    if evidence_digest is not None and SHA_RE.fullmatch(str(evidence_digest)) is None:
        raise AdaptiveControlError("model_outcome_evidence_invalid")
    integers = (high_findings, retries, input_tokens, output_tokens)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in integers):
        raise AdaptiveControlError("model_outcome_metrics_invalid")
    row = {
        "schema_version": 2 if evidence_digest is not None else 1,
        "sample_id": sample_id,
        "recorded_at": _now(),
        "role": role,
        "model": model,
        "baseline": baseline,
        "risk": risk,
        "outcome": outcome,
        "high_findings": high_findings,
        "retries": retries,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if evidence_digest is not None:
        row["evidence_digest"] = evidence_digest
    with _model_ledger_lock(root):
        rows = _ledger_rows(root)
        same_sample = [
            item for item in rows
            if item.get("sample_id") == sample_id and item.get("role") == role
        ]
        if same_sample:
            previous = dict(same_sample[-1])
            previous.pop("recorded_at", None)
            candidate = dict(row)
            candidate.pop("recorded_at", None)
            if previous != candidate:
                raise AdaptiveControlError("model_outcome_sample_conflict")
            return same_sample[-1]
        rows.append(row)
        rows = rows[-MAX_LEDGER_ROWS:]
        project = _model_project(os.path.realpath(root))
        payload = "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for item in rows
        )
        if len(payload.encode("utf-8")) > MAX_LEDGER_BYTES:
            raise AdaptiveControlError("model_ledger_oversize")
        memory_store.atomic_write(
            os.path.join(project, MODEL_LEDGER_NAME),
            payload,
            mode=0o600,
            refuse_symlink=True,
            durable=True,
        )
    return row


def record_model_route_usage(root, run, route, usage):
    """Persist only adapter-attested candidate usage for later outcome evaluation."""
    run_dir = _safe_run(root, run)
    if (
        not isinstance(route, dict)
        or set(route) != {"role", "model", "baseline"}
        or route.get("role") not in ("balanced", "cheap")
        or any(
            not isinstance(route.get(key), str)
            or not route.get(key)
            or len(route.get(key)) > 128
            for key in ("model", "baseline")
        )
        or route.get("model") == route.get("baseline")
    ):
        raise AdaptiveControlError("model_route_evidence_invalid")
    if (
        not isinstance(usage, dict)
        or any(
            isinstance(usage.get(key), bool)
            or not isinstance(usage.get(key), int)
            or usage.get(key) < 0
            for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens")
        )
        or usage.get("input_tokens", 0) + usage.get("output_tokens", 0) <= 0
    ):
        raise AdaptiveControlError("model_route_usage_invalid")
    try:
        with memory_store.local_path_guard(os.path.realpath(root), run_dir):
            return _record_model_route_usage_pinned(run_dir, route, usage)
    except (memory_store.ConcurrentWriteError, OSError, ValueError) as exc:
        raise AdaptiveControlError(
            "model_route_evidence_unsafe:%s" % exc.__class__.__name__
        )


def _record_model_route_usage_pinned(run_dir, route, usage):
    path = os.path.join(run_dir, MODEL_ROUTE_EVIDENCE_NAME)
    existing = _read_json(path, maximum=64 * 1024, missing_ok=True)
    if existing is None:
        existing = {"schema_version": 1, "routes": []}
    if (
        not isinstance(existing, dict)
        or set(existing) != {"schema_version", "routes"}
        or existing.get("schema_version") != 1
        or not isinstance(existing.get("routes"), list)
        or len(existing["routes"]) > 8
    ):
        raise AdaptiveControlError("model_route_evidence_invalid")
    routes = []
    matched = False
    for row in existing["routes"]:
        if (
            not isinstance(row, dict)
            or set(row) != {
                "role", "model", "baseline", "turns", "model_calls",
                "tool_calls", "input_tokens", "output_tokens",
            }
            or row.get("role") not in ("balanced", "cheap")
            or any(
                not isinstance(row.get(key), str)
                or not row.get(key)
                or len(row.get(key)) > 128
                for key in ("model", "baseline")
            )
            or any(
                isinstance(row.get(key), bool)
                or not isinstance(row.get(key), int)
                or row.get(key) < 0
                for key in ("turns", "model_calls", "tool_calls", "input_tokens", "output_tokens")
            )
        ):
            raise AdaptiveControlError("model_route_evidence_invalid")
        candidate = dict(row)
        if all(row.get(key) == route[key] for key in ("role", "model", "baseline")):
            if matched:
                raise AdaptiveControlError("model_route_evidence_invalid")
            matched = True
            candidate["turns"] += 1
            for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens"):
                candidate[key] += usage[key]
        routes.append(candidate)
    if not matched:
        if len(routes) >= 8:
            raise AdaptiveControlError("model_route_evidence_limit")
        routes.append({
            **route,
            "turns": 1,
            **{key: usage[key] for key in (
                "model_calls", "tool_calls", "input_tokens", "output_tokens",
            )},
        })
    value = {"schema_version": 1, "routes": sorted(
        routes, key=lambda row: (row["role"], row["model"], row["baseline"]),
    )}
    memory_store.atomic_write(
        path,
        _json_bytes(value).decode("utf-8"),
        mode=0o600,
        refuse_symlink=True,
        durable=True,
    )
    return value


def _model_route_evidence(run_dir, role, model, baseline):
    value = _read_json(
        os.path.join(run_dir, MODEL_ROUTE_EVIDENCE_NAME),
        maximum=64 * 1024,
    )
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "routes"}
        or value.get("schema_version") != 1
        or not isinstance(value.get("routes"), list)
    ):
        raise AdaptiveControlError("model_route_evidence_invalid")
    matching = [
        row for row in value["routes"]
        if isinstance(row, dict)
        and row.get("role") == role
        and row.get("model") == model
        and row.get("baseline") == baseline
    ]
    if len(matching) != 1:
        raise AdaptiveControlError("model_route_evidence_mismatch")
    row = matching[0]
    if (
        set(row) != {
            "role", "model", "baseline", "turns", "model_calls",
            "tool_calls", "input_tokens", "output_tokens",
        }
        or any(
            isinstance(row.get(key), bool)
            or not isinstance(row.get(key), int)
            or row.get(key) < 0
            for key in ("turns", "model_calls", "tool_calls", "input_tokens", "output_tokens")
        )
        or row.get("turns", 0) < 1
        or row.get("input_tokens", 0) + row.get("output_tokens", 0) <= 0
    ):
        raise AdaptiveControlError("model_route_evidence_invalid")
    return row, value


def _model_run_evidence(root, run, role, model, baseline):
    """Derive quality and usage metrics from the completed run, never CLI claims."""
    run_dir = _safe_run(root, run)
    state = _read_text(os.path.join(run_dir, "STATE.md"), missing_ok=True)
    status = _state_value(state, "Status").lower().split(" ", 1)[0]
    phase_6 = _state_value(state, "Phase 6").lower().split(" ", 1)[0]
    phase_7 = _state_value(state, "Phase 7").lower().split(" ", 1)[0]
    recovery = _state_value(state, "Recovery").lower().split(" ", 1)[0]
    review_gate = _state_value(state, "Review gate").lower().split(" ", 1)[0]
    success_ready = (
        status == "done"
        and phase_6 == "done"
        and phase_7 == "done"
        and recovery == "clean"
        and review_gate == "code"
    )
    terminal = _read_json(
        os.path.join(run_dir, "SESSION-OUTCOME.json"),
        maximum=64 * 1024,
        missing_ok=True,
    )
    successful_terminal = (
        isinstance(terminal, dict)
        and terminal.get("schema_version") == 1
        and terminal.get("outcome") == "done"
    )
    success_ready = success_ready and successful_terminal
    failed_terminal = (
        status in ("failed", "aborted")
        and isinstance(terminal, dict)
        and terminal.get("schema_version") == 1
        and terminal.get("outcome") in ("failed", "aborted")
    )
    if not success_ready and not failed_terminal:
        raise AdaptiveControlError("model_outcome_run_not_verified")

    verification = _read_text(
        os.path.join(run_dir, "VERIFICATION.md"), missing_ok=failed_terminal,
    )
    verification_marker = (
        "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->"
    )
    if success_ready and verification.count(verification_marker) != 1:
        raise AdaptiveControlError("model_outcome_verification_invalid")
    code_review = _read_text(
        os.path.join(run_dir, "CODE-REVIEW.md"), missing_ok=failed_terminal,
    )
    if success_ready and not code_review.strip():
        raise AdaptiveControlError("model_outcome_review_missing")

    findings_dir = os.path.join(run_dir, "findings")
    try:
        findings_info = os.stat(findings_dir, follow_symlinks=False)
    except OSError:
        findings_info = None
    if findings_info is not None and not stat.S_ISDIR(findings_info.st_mode):
        raise AdaptiveControlError("model_outcome_findings_unsafe")
    rounds = []
    try:
        entries = list(os.scandir(findings_dir)) if findings_info is not None else []
    except OSError:
        raise AdaptiveControlError("model_outcome_findings_unreadable")
    for entry in entries:
        match = re.fullmatch(r"r([1-9][0-9]*)-code-verified\.md", entry.name)
        if match is None:
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                raise AdaptiveControlError("model_outcome_findings_unsafe")
        except OSError:
            raise AdaptiveControlError("model_outcome_findings_unreadable")
        text = _read_text(entry.path)
        material = len(re.findall(r"^FINDING (?:BLOCKER|HIGH)\b", text, re.MULTILINE))
        rounds.append((int(match.group(1)), text, material))
    if success_ready and not rounds:
        raise AdaptiveControlError("model_outcome_findings_missing")
    rounds.sort(key=lambda item: item[0])
    if success_ready and rounds[-1][2] != 0:
        raise AdaptiveControlError("model_outcome_review_open")
    high_findings = sum(item[2] for item in rounds)

    route_usage, route_receipt = _model_route_evidence(
        run_dir, role, model, baseline,
    )
    usage = _read_json(os.path.join(run_dir, HOST_USAGE_NAME), maximum=64 * 1024)
    if (
        not isinstance(usage, dict)
        or usage.get("schema_version") != 1
        or usage.get("status") != "available"
        or any(
            isinstance(usage.get(key), bool)
            or not isinstance(usage.get(key), int)
            or usage.get(key) < 0
            for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens")
        )
        or usage.get("input_tokens", 0) + usage.get("output_tokens", 0) <= 0
        or route_usage["input_tokens"] > usage.get("input_tokens", 0)
        or route_usage["output_tokens"] > usage.get("output_tokens", 0)
    ):
        raise AdaptiveControlError("model_outcome_usage_unavailable")

    trace = _read_json(os.path.join(run_dir, EXECUTION_TRACE_NAME))
    if (
        not isinstance(trace, dict)
        or trace.get("schema_version") != 1
        or trace.get("contract") != 1
        or not isinstance(trace.get("entries"), list)
    ):
        raise AdaptiveControlError("model_outcome_trace_invalid")
    trace_failures = sum(
        isinstance(entry, dict) and entry.get("outcome") in ("failed", "no_progress")
        for entry in trace["entries"]
    )
    retries = (
        trace_failures
        + sum(1 for _, _, material in rounds if material)
        + (1 if failed_terminal else 0)
    )
    architecture = _state_value(state, "Architecture deliberation").lower().split(" ", 1)[0]
    build_risk = _state_value(state, "Build risk").lower().split(" ", 1)[0]
    risk = "critical" if architecture == "active" or build_risk == "required" else "routine"
    evidence_material = {
        "run": os.path.basename(run_dir),
        "state": hashlib.sha256(state.encode("utf-8")).hexdigest(),
        "verification": hashlib.sha256(verification.encode("utf-8")).hexdigest(),
        "code_review": hashlib.sha256(code_review.encode("utf-8")).hexdigest(),
        "latest_findings": (
            hashlib.sha256(rounds[-1][1].encode("utf-8")).hexdigest()
            if rounds else None
        ),
        "usage": hashlib.sha256(_json_bytes(usage)).hexdigest(),
        "route": hashlib.sha256(_json_bytes(route_receipt)).hexdigest(),
        "trace": hashlib.sha256(_json_bytes(trace)).hexdigest(),
    }
    evidence_digest = "sha256:" + hashlib.sha256(
        json.dumps(evidence_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "risk": risk,
        "outcome": "failed" if failed_terminal else "passed",
        "high_findings": high_findings,
        "retries": retries,
        "input_tokens": route_usage["input_tokens"],
        "output_tokens": route_usage["output_tokens"],
        "evidence_digest": evidence_digest,
    }


def record_verified_model_outcome(root, run, role, model, baseline):
    evidence = _model_run_evidence(root, run, role, model, baseline)
    return record_model_outcome(
        root,
        model_sample_id(root, run),
        role,
        model,
        baseline,
        evidence["risk"],
        evidence["outcome"],
        evidence["high_findings"],
        evidence["retries"],
        evidence["input_tokens"],
        evidence["output_tokens"],
        evidence["evidence_digest"],
    )


def record_observed_model_outcomes(root, run):
    """Record every adapter-attested route after a terminal run, best-effort."""
    run_dir = _safe_run(root, run)
    value = _read_json(
        os.path.join(run_dir, MODEL_ROUTE_EVIDENCE_NAME),
        maximum=64 * 1024,
        missing_ok=True,
    )
    if not isinstance(value, dict) or not isinstance(value.get("routes"), list):
        return []
    recorded = []
    for row in value["routes"]:
        if not isinstance(row, dict):
            continue
        try:
            recorded.append(record_verified_model_outcome(
                root, run, row.get("role"), row.get("model"), row.get("baseline"),
            ))
        except (AdaptiveControlError, OSError, ValueError):
            continue
    return recorded


def model_sample_id(root, run):
    run_dir = _safe_run(root, run)
    material = "kimiflow-model-outcome:1\0%s" % os.path.basename(run_dir)
    return "sample_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _candidate_eligible(rows, role, model, baseline, risk):
    matching = [
        row for row in rows
        if row.get("role") == role
        and row.get("model") == model
        and row.get("baseline") == baseline
        and row.get("risk") == risk
    ][-MODEL_MIN_SAMPLES:]
    input_tokens = sum(row.get("input_tokens", 0) for row in matching)
    output_tokens = sum(row.get("output_tokens", 0) for row in matching)
    usage_samples = sum(
        row.get("input_tokens", 0) + row.get("output_tokens", 0) > 0
        for row in matching
    )
    clean = (
        len(matching) >= MODEL_MIN_SAMPLES
        and usage_samples >= MODEL_MIN_SAMPLES
        and all(
            row.get("schema_version") == 2
            and SHA_RE.fullmatch(str(row.get("evidence_digest", ""))) is not None
            and row.get("outcome") == "passed"
            and row.get("high_findings") == 0
            and row.get("retries") == 0
            for row in matching
        )
    )
    return clean, {
        "samples": len(matching),
        "usage_samples": usage_samples,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "average_total_tokens": (
            (input_tokens + output_tokens) // len(matching)
            if matching else 0
        ),
    }


def resolve_model_roles(root, configured, risk="routine", write=False):
    if not isinstance(configured, dict) or any(key not in ROLE_KEYS for key in configured):
        raise AdaptiveControlError("model_roles_invalid")
    if risk not in ("routine", "critical"):
        raise AdaptiveControlError("model_risk_invalid")
    baseline = configured.get("top")
    if not isinstance(baseline, str) or not baseline:
        return {
            "schema_version": 1,
            "status": "fallback",
            "reason": "top_baseline_missing",
            "risk": risk,
            "roles": dict(configured),
            "decisions": {},
            "user_gate": False,
        }
    rows = _ledger_rows(root)
    resolved = dict(configured)
    decisions = {}
    for role in ("balanced", "cheap"):
        candidate = configured.get(role)
        if not candidate or candidate == baseline:
            continue
        eligible, evidence = _candidate_eligible(rows, role, candidate, baseline, risk)
        # Critical semantic work never auto-downgrades. It can still be explicitly
        # delegated by the host; the adaptive selector itself remains conservative.
        selected = candidate if eligible and risk == "routine" else baseline
        resolved[role] = selected
        decisions[role] = {
            "eligible": eligible and risk == "routine",
            **evidence,
            "reason": "verified_equivalence" if selected == candidate else "top_default",
        }
    value = {
        "schema_version": 1,
        "status": "resolved",
        "risk": risk,
        "roles": resolved,
        "decisions": decisions,
        "user_gate": False,
    }
    if write:
        root = os.path.realpath(root)
        project = memory_store.ensure_local_directory(
            root, _model_project(root), mode=0o700,
        )
        with memory_store.local_path_guard(root, project):
            memory_store.atomic_write(
                os.path.join(project, MODEL_POLICY_NAME),
                _json_bytes(value).decode("utf-8"),
                mode=0o600,
                refuse_symlink=True,
                durable=True,
            )
    return value


def _adaptive_rows(root, name):
    root = os.path.realpath(root)
    project = _model_project(root)
    if not os.path.lexists(project):
        return []
    try:
        with memory_store.local_path_guard(root, project) as anchor:
            payload = _read_project_file(anchor["descriptor"], name, MAX_LEDGER_BYTES)
    except (memory_store.ConcurrentWriteError, OSError, ValueError) as exc:
        raise AdaptiveControlError("adaptive_ledger_unsafe:%s" % exc.__class__.__name__)
    if payload is None:
        return []
    rows = []
    for raw in payload.splitlines():
        if not raw.strip():
            continue
        if len(rows) >= MAX_LEDGER_ROWS:
            raise AdaptiveControlError("adaptive_ledger_too_many_rows")
        try:
            row = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise AdaptiveControlError("adaptive_ledger_malformed")
        if not isinstance(row, dict):
            raise AdaptiveControlError("adaptive_ledger_invalid")
        rows.append(row)
    return rows


def _append_adaptive_row(root, name, row, identity_keys):
    with _model_ledger_lock(root):
        rows = _adaptive_rows(root, name)
        matching = [item for item in rows if all(item.get(key) == row.get(key) for key in identity_keys)]
        if matching:
            previous = dict(matching[-1])
            previous.pop("recorded_at", None)
            candidate = dict(row)
            candidate.pop("recorded_at", None)
            if previous != candidate:
                raise AdaptiveControlError("adaptive_sample_conflict")
            return matching[-1]
        rows = (rows + [row])[-MAX_LEDGER_ROWS:]
        payload = "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for item in rows
        )
        if len(payload.encode("utf-8")) > MAX_LEDGER_BYTES:
            raise AdaptiveControlError("adaptive_ledger_oversize")
        project = _model_project(os.path.realpath(root))
        memory_store.atomic_write(
            os.path.join(project, name), payload, mode=0o600,
            refuse_symlink=True, durable=True,
        )
    return row


def record_retrieval_outcome(
    root, sample_id, provider_fingerprint, task_class, stage, quality_passed,
    verification_passed, high_findings=0, retries=0, logical_input_tokens=0,
    provider_latency_ms=0, token_waste=False, snapshot_status="current",
):
    if SAMPLE_ID_RE.fullmatch(str(sample_id)) is None or SHA_RE.fullmatch(str(provider_fingerprint)) is None:
        raise AdaptiveControlError("retrieval_outcome_identity_invalid")
    if not isinstance(task_class, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", task_class) is None:
        raise AdaptiveControlError("retrieval_outcome_class_invalid")
    if stage not in ("holdout", "shadow", "canary") or snapshot_status not in ("current", "stale"):
        raise AdaptiveControlError("retrieval_outcome_stage_invalid")
    if not isinstance(quality_passed, bool) or not isinstance(verification_passed, bool) or not isinstance(token_waste, bool):
        raise AdaptiveControlError("retrieval_outcome_status_invalid")
    counters = (high_findings, retries, logical_input_tokens, provider_latency_ms)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
        raise AdaptiveControlError("retrieval_outcome_metrics_invalid")
    row = {
        "schema_version": 1,
        "sample_id": sample_id,
        "recorded_at": _now(),
        "provider_fingerprint": provider_fingerprint,
        "task_class": task_class,
        "stage": stage,
        "quality_passed": quality_passed,
        "verification_passed": verification_passed,
        "high_findings": high_findings,
        "retries": retries,
        "logical_input_tokens": logical_input_tokens,
        "provider_latency_ms": provider_latency_ms,
        "token_waste": token_waste,
        "snapshot_status": snapshot_status,
    }
    return _append_adaptive_row(
        root, RETRIEVAL_LEDGER_NAME, row,
        ("sample_id", "provider_fingerprint", "task_class", "stage"),
    )


def _valid_retrieval_row(row):
    required = {
        "schema_version", "sample_id", "recorded_at", "provider_fingerprint", "task_class",
        "stage", "quality_passed", "verification_passed", "high_findings", "retries",
        "logical_input_tokens", "provider_latency_ms", "token_waste", "snapshot_status",
    }
    return (
        isinstance(row, dict) and set(row) == required and row.get("schema_version") == 1
        and SAMPLE_ID_RE.fullmatch(str(row.get("sample_id", ""))) is not None
        and SHA_RE.fullmatch(str(row.get("provider_fingerprint", ""))) is not None
        and row.get("stage") in ("holdout", "shadow", "canary")
        and row.get("snapshot_status") in ("current", "stale")
        and all(isinstance(row.get(key), bool) for key in ("quality_passed", "verification_passed", "token_waste"))
        and all(not isinstance(row.get(key), bool) and isinstance(row.get(key), int) and row.get(key) >= 0 for key in ("high_findings", "retries", "logical_input_tokens", "provider_latency_ms"))
    )


def resolve_retrieval_route(root, provider_fingerprint, task_class):
    if SHA_RE.fullmatch(str(provider_fingerprint)) is None:
        raise AdaptiveControlError("retrieval_route_identity_invalid")
    rows = _adaptive_rows(root, RETRIEVAL_LEDGER_NAME)
    if any(not _valid_retrieval_row(row) for row in rows):
        raise AdaptiveControlError("retrieval_ledger_contract_invalid")
    matching = [
        row for row in rows
        if row["provider_fingerprint"] == provider_fingerprint and row["task_class"] == task_class
    ]
    if any(
        row["snapshot_status"] != "current" or not row["quality_passed"]
        or row["high_findings"] > 0 or row["retries"] > 0 or row["token_waste"]
        for row in matching[-1:]
    ):
        route, reason = "off", "quality_regression"
    else:
        holdout = any(row["stage"] == "holdout" and row["quality_passed"] for row in matching)
        shadow = any(row["stage"] == "shadow" and row["quality_passed"] for row in matching)
        canaries = [row for row in matching if row["stage"] == "canary"][-5:]
        clean_canaries = (
            len(canaries) == 5
            and all(
                row["quality_passed"] and row["verification_passed"]
                and row["high_findings"] == 0 and row["retries"] == 0
                and not row["token_waste"] and row["snapshot_status"] == "current"
                for row in canaries
            )
        )
        if holdout and shadow and clean_canaries:
            route, reason = "active", "five_clean_canaries"
        elif holdout and shadow:
            route, reason = "canary", "holdout_and_shadow_clean"
        else:
            route, reason = "shadow", "evidence_pending"
    return {
        "schema_version": 1,
        "status": "resolved",
        "route": route,
        "reason": reason,
        "samples": len(matching),
        "user_gate": False,
    }


def _review_key(
    model_fingerprint, execution_variant, role, task_class, runtime_fingerprint,
    policy_fingerprint, prompt_gate_fingerprint,
):
    fingerprints = (model_fingerprint, runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint)
    if any(SHA_RE.fullmatch(str(value)) is None for value in fingerprints):
        raise AdaptiveControlError("review_identity_invalid")
    identities = (execution_variant, role, task_class)
    if any(not isinstance(value, str) or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value) is None for value in identities):
        raise AdaptiveControlError("review_identity_invalid")
    return {
        "model_fingerprint": model_fingerprint,
        "execution_variant": execution_variant,
        "role": role,
        "task_class": task_class,
        "runtime_fingerprint": runtime_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "prompt_gate_fingerprint": prompt_gate_fingerprint,
    }


def record_review_outcome(
    root, sample_id, model_fingerprint, execution_variant, role, task_class,
    runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    quality_passed, high_findings=0, retries=0, audit_finding=False,
):
    if SAMPLE_ID_RE.fullmatch(str(sample_id)) is None:
        raise AdaptiveControlError("review_sample_invalid")
    key = _review_key(
        model_fingerprint, execution_variant, role, task_class, runtime_fingerprint,
        policy_fingerprint, prompt_gate_fingerprint,
    )
    if not isinstance(quality_passed, bool) or not isinstance(audit_finding, bool):
        raise AdaptiveControlError("review_outcome_invalid")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (high_findings, retries)):
        raise AdaptiveControlError("review_metrics_invalid")
    row = {
        "schema_version": 1, "sample_id": sample_id, "recorded_at": _now(), **key,
        "quality_passed": quality_passed,
        "high_findings": high_findings,
        "retries": retries,
        "audit_finding": audit_finding,
    }
    return _append_adaptive_row(
        root, REVIEW_LEDGER_NAME, row,
        ("sample_id", *key.keys()),
    )


def _valid_review_row(row):
    try:
        key = _review_key(
            row.get("model_fingerprint"), row.get("execution_variant"), row.get("role"),
            row.get("task_class"), row.get("runtime_fingerprint"),
            row.get("policy_fingerprint"), row.get("prompt_gate_fingerprint"),
        )
    except (AttributeError, AdaptiveControlError):
        return False
    required = {"schema_version", "sample_id", "recorded_at", *key.keys(), "quality_passed", "high_findings", "retries", "audit_finding"}
    return (
        set(row) == required and row.get("schema_version") == 1
        and SAMPLE_ID_RE.fullmatch(str(row.get("sample_id", ""))) is not None
        and isinstance(row.get("quality_passed"), bool)
        and isinstance(row.get("audit_finding"), bool)
        and all(not isinstance(row.get(name), bool) and isinstance(row.get(name), int) and row.get(name) >= 0 for name in ("high_findings", "retries"))
    )


def resolve_review_mode(
    root, model_fingerprint, execution_variant, role, task_class,
    runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    risk="routine", repeated_failure=False, regression=False,
):
    key = _review_key(
        model_fingerprint, execution_variant, role, task_class, runtime_fingerprint,
        policy_fingerprint, prompt_gate_fingerprint,
    )
    if risk not in ("routine", "critical") or not isinstance(repeated_failure, bool) or not isinstance(regression, bool):
        raise AdaptiveControlError("review_route_invalid")
    rows = _adaptive_rows(root, REVIEW_LEDGER_NAME)
    if any(not _valid_review_row(row) for row in rows):
        raise AdaptiveControlError("review_ledger_contract_invalid")
    matching = [row for row in rows if all(row.get(name) == value for name, value in key.items())]
    clean = [
        row for row in matching
        if row["quality_passed"] and row["high_findings"] == 0
        and row["retries"] == 0 and not row["audit_finding"]
    ]
    revoked = any(row["audit_finding"] or row["high_findings"] > 0 or not row["quality_passed"] for row in matching[-1:])
    if risk == "critical":
        mode, reason, audit = "ensemble", "critical_risk", False
    elif repeated_failure or regression or revoked:
        mode, reason, audit = "single-independent", "regression_or_failure", False
    elif len(clean) < 5:
        mode, reason, audit = "single-independent", "calibration_pending", False
    else:
        material = json.dumps(key, sort_keys=True, separators=(",", ":"))
        offset = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:8], 16) % 10
        audit = (offset + len(matching)) % 10 == 0
        mode = "single-independent" if audit else "embedded"
        reason = "deterministic_audit" if audit else "calibrated_self_verification"
    return {
        "schema_version": 1,
        "status": "resolved",
        "review_mode": mode,
        "reason": reason,
        "audit_sample": audit,
        "samples": len(matching),
        "user_gate": False,
    }


def record_review_cascade_outcome(
    root, sample_id, model_fingerprint, execution_variant, role, task_class,
    runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint, stage,
    quality_passed, verification_passed, missed_material_findings=0, retries=0,
    full_input_tokens=0, full_output_tokens=0, cascade_input_tokens=0,
    cascade_output_tokens=0, full_rounds=1, cascade_rounds=1,
    audit_finding=False,
):
    if SAMPLE_ID_RE.fullmatch(str(sample_id)) is None:
        raise AdaptiveControlError("review_cascade_sample_invalid")
    key = _review_key(
        model_fingerprint, execution_variant, role, task_class,
        runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    )
    if stage not in ("holdout", "shadow", "canary"):
        raise AdaptiveControlError("review_cascade_stage_invalid")
    if any(
        not isinstance(value, bool)
        for value in (quality_passed, verification_passed, audit_finding)
    ):
        raise AdaptiveControlError("review_cascade_outcome_invalid")
    counters = (
        missed_material_findings,
        retries,
        full_input_tokens,
        full_output_tokens,
        cascade_input_tokens,
        cascade_output_tokens,
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in counters
    ):
        raise AdaptiveControlError("review_cascade_metrics_invalid")
    rounds = (full_rounds, cascade_rounds)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in rounds
    ):
        raise AdaptiveControlError("review_cascade_rounds_invalid")
    row = {
        "schema_version": 1,
        "sample_id": sample_id,
        "recorded_at": _now(),
        **key,
        "stage": stage,
        "quality_passed": quality_passed,
        "verification_passed": verification_passed,
        "missed_material_findings": missed_material_findings,
        "retries": retries,
        "full_input_tokens": full_input_tokens,
        "full_output_tokens": full_output_tokens,
        "cascade_input_tokens": cascade_input_tokens,
        "cascade_output_tokens": cascade_output_tokens,
        "full_rounds": full_rounds,
        "cascade_rounds": cascade_rounds,
        "audit_finding": audit_finding,
    }
    return _append_adaptive_row(
        root,
        REVIEW_CASCADE_LEDGER_NAME,
        row,
        ("sample_id", *key.keys()),
    )


def _valid_review_cascade_row(row):
    if not isinstance(row, dict):
        return False
    try:
        key = _review_key(
            row.get("model_fingerprint"),
            row.get("execution_variant"),
            row.get("role"),
            row.get("task_class"),
            row.get("runtime_fingerprint"),
            row.get("policy_fingerprint"),
            row.get("prompt_gate_fingerprint"),
        )
    except (AttributeError, AdaptiveControlError):
        return False
    required = {
        "schema_version",
        "sample_id",
        "recorded_at",
        *key.keys(),
        "stage",
        "quality_passed",
        "verification_passed",
        "missed_material_findings",
        "retries",
        "full_input_tokens",
        "full_output_tokens",
        "cascade_input_tokens",
        "cascade_output_tokens",
        "full_rounds",
        "cascade_rounds",
        "audit_finding",
    }
    counters = (
        "missed_material_findings",
        "retries",
        "full_input_tokens",
        "full_output_tokens",
        "cascade_input_tokens",
        "cascade_output_tokens",
    )
    rounds = ("full_rounds", "cascade_rounds")
    return (
        set(row) == required
        and row.get("schema_version") == 1
        and SAMPLE_ID_RE.fullmatch(str(row.get("sample_id", ""))) is not None
        and row.get("stage") in ("holdout", "shadow", "canary")
        and all(
            isinstance(row.get(name), bool)
            for name in ("quality_passed", "verification_passed", "audit_finding")
        )
        and all(
            not isinstance(row.get(name), bool)
            and isinstance(row.get(name), int)
            and row.get(name) >= 0
            for name in counters
        )
        and all(
            not isinstance(row.get(name), bool)
            and isinstance(row.get(name), int)
            and row.get(name) >= 1
            for name in rounds
        )
    )


def _review_cascade_clean(row):
    full_tokens = row["full_input_tokens"] + row["full_output_tokens"]
    cascade_tokens = row["cascade_input_tokens"] + row["cascade_output_tokens"]
    return (
        row["quality_passed"]
        and row["verification_passed"]
        and row["missed_material_findings"] == 0
        and row["retries"] == 0
        and not row["audit_finding"]
        and full_tokens > 0
        and 0 < cascade_tokens < full_tokens
        and row["cascade_rounds"] <= row["full_rounds"]
        and row["cascade_rounds"] <= 2
    )


def resolve_review_cascade(
    root, model_fingerprint, execution_variant, role, task_class,
    runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    risk="routine", repeated_failure=False, regression=False,
):
    key = _review_key(
        model_fingerprint, execution_variant, role, task_class,
        runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    )
    if (
        risk not in ("routine", "critical")
        or not isinstance(repeated_failure, bool)
        or not isinstance(regression, bool)
    ):
        raise AdaptiveControlError("review_cascade_route_invalid")
    rows = _adaptive_rows(root, REVIEW_CASCADE_LEDGER_NAME)
    if any(not _valid_review_cascade_row(row) for row in rows):
        raise AdaptiveControlError("review_cascade_ledger_contract_invalid")
    matching = [
        row
        for row in rows
        if all(row.get(name) == value for name, value in key.items())
    ]
    bad_indexes = [
        index
        for index, row in enumerate(matching)
        if not _review_cascade_clean(row)
    ]
    latest_regressed = bool(bad_indexes) and bad_indexes[-1] == len(matching) - 1
    calibration = (
        matching[bad_indexes[-1] + 1 :]
        if bad_indexes
        else matching
    )
    audit = False
    if risk == "critical":
        route, stage, reason = "full", "off", "critical_risk"
    elif repeated_failure or regression:
        route, stage, reason = "full", "off", "regression_or_failure"
    elif latest_regressed:
        route, stage, reason = "full", "off", "quality_or_token_regression"
    else:
        holdout_index = next(
            (
                index
                for index, row in enumerate(calibration)
                if row["stage"] == "holdout" and _review_cascade_clean(row)
            ),
            None,
        )
        shadow_index = next(
            (
                index
                for index, row in enumerate(calibration)
                if holdout_index is not None
                and index > holdout_index
                and row["stage"] == "shadow"
                and _review_cascade_clean(row)
            ),
            None,
        )
        holdout = holdout_index is not None
        shadow = shadow_index is not None
        canaries = [
            row
            for index, row in enumerate(calibration)
            if shadow_index is not None
            and index > shadow_index
            and row["stage"] == "canary"
        ][-5:]
        clean_canaries = len(canaries) == 5 and all(
            _review_cascade_clean(row) for row in canaries
        )
        if holdout and shadow and clean_canaries:
            material = json.dumps(key, sort_keys=True, separators=(",", ":"))
            offset = int(
                hashlib.sha256(material.encode("utf-8")).hexdigest()[:8], 16
            ) % 10
            audit = (offset + len(matching)) % 10 == 0
            route = "full" if audit else "selective"
            stage = "active"
            reason = "deterministic_audit" if audit else "verified_ab_equivalence"
        elif holdout and shadow:
            route, stage, reason = "full", "canary", "canary_evidence_pending"
        else:
            route, stage, reason = "full", "shadow", "ab_evidence_pending"
    return {
        "schema_version": 1,
        "status": "resolved",
        "route": route,
        "stage": stage,
        "reason": reason,
        "audit_sample": audit,
        "samples": len(matching),
        "user_gate": False,
    }


def _review_cascade_run_signals(run_dir):
    recovery = _read_text(
        os.path.join(run_dir, "RECOVERY.md"),
        missing_ok=True,
    )
    verification = _read_text(
        os.path.join(run_dir, "VERIFICATION.md"),
        missing_ok=True,
    )
    repeated_failure = re.search(
        r"^<!-- kimiflow:recovery gate=code\b",
        recovery,
        re.MULTILINE,
    ) is not None
    regression = re.search(
        r"^<!-- kimiflow:verification [^\n]*"
        r"regression=(?:failed|not_run) -->$",
        verification,
        re.MULTILINE,
    ) is not None
    return repeated_failure, regression


def write_review_cascade_route(
    root, run, model_fingerprint, execution_variant, role, task_class,
    runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    risk="routine", repeated_failure=False, regression=False,
):
    run_dir = _safe_run(root, run)
    run_repeated_failure, run_regression = _review_cascade_run_signals(run_dir)
    repeated_failure = repeated_failure or run_repeated_failure
    regression = regression or run_regression
    key = _review_key(
        model_fingerprint, execution_variant, role, task_class,
        runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    )
    resolved = resolve_review_cascade(
        root,
        **key,
        risk=risk,
        repeated_failure=repeated_failure,
        regression=regression,
    )
    receipt = {
        **resolved,
        "binding": key,
        "risk": risk,
        "repeated_failure": repeated_failure,
        "regression": regression,
    }
    _write_run_receipt(root, run_dir, REVIEW_CASCADE_ROUTE_NAME, receipt)
    return receipt


def verify_review_cascade_route(root, run_dir, risk):
    path = os.path.join(run_dir, REVIEW_CASCADE_ROUTE_NAME)
    receipt = _read_json(path)
    required = {
        "schema_version",
        "status",
        "route",
        "stage",
        "reason",
        "audit_sample",
        "samples",
        "user_gate",
        "binding",
        "risk",
        "repeated_failure",
        "regression",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise AdaptiveControlError("review_cascade_route_receipt_invalid")
    binding = receipt.get("binding")
    if not isinstance(binding, dict) or receipt.get("risk") != risk:
        raise AdaptiveControlError("review_cascade_route_receipt_invalid")
    run_repeated_failure, run_regression = _review_cascade_run_signals(run_dir)
    if (
        run_repeated_failure and not receipt.get("repeated_failure")
        or run_regression and not receipt.get("regression")
    ):
        raise AdaptiveControlError("review_cascade_route_receipt_stale")
    expected = {
        **resolve_review_cascade(
            root,
            **binding,
            risk=risk,
            repeated_failure=receipt.get("repeated_failure"),
            regression=receipt.get("regression"),
        ),
        "binding": binding,
        "risk": risk,
        "repeated_failure": receipt.get("repeated_failure"),
        "regression": receipt.get("regression"),
    }
    if receipt != expected:
        raise AdaptiveControlError("review_cascade_route_receipt_stale")
    return receipt, path


def record_execution_variant_outcome(
    root, sample_id, model_fingerprint, execution_variant, role, task_class,
    runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    quality_passed, verification_passed, high_findings=0, retries=0,
    logical_input_tokens=0, output_tokens=0, scope_creep=False,
):
    if SAMPLE_ID_RE.fullmatch(str(sample_id)) is None:
        raise AdaptiveControlError("variant_sample_invalid")
    key = _review_key(
        model_fingerprint, execution_variant, role, task_class,
        runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    )
    if any(not isinstance(value, bool) for value in (
        quality_passed, verification_passed, scope_creep,
    )):
        raise AdaptiveControlError("variant_outcome_invalid")
    metrics = (high_findings, retries, logical_input_tokens, output_tokens)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in metrics
    ):
        raise AdaptiveControlError("variant_metrics_invalid")
    row = {
        "schema_version": 1,
        "sample_id": sample_id,
        "recorded_at": _now(),
        **key,
        "quality_passed": quality_passed,
        "verification_passed": verification_passed,
        "high_findings": high_findings,
        "retries": retries,
        "logical_input_tokens": logical_input_tokens,
        "output_tokens": output_tokens,
        "scope_creep": scope_creep,
    }
    return _append_adaptive_row(
        root, VARIANT_LEDGER_NAME, row, ("sample_id", *key.keys()),
    )


def _valid_variant_row(row):
    try:
        key = _review_key(
            row.get("model_fingerprint"), row.get("execution_variant"),
            row.get("role"), row.get("task_class"),
            row.get("runtime_fingerprint"), row.get("policy_fingerprint"),
            row.get("prompt_gate_fingerprint"),
        )
    except (AttributeError, AdaptiveControlError):
        return False
    required = {
        "schema_version", "sample_id", "recorded_at", *key.keys(),
        "quality_passed", "verification_passed", "high_findings", "retries",
        "logical_input_tokens", "output_tokens", "scope_creep",
    }
    return (
        isinstance(row, dict) and set(row) == required
        and row.get("schema_version") == 1
        and SAMPLE_ID_RE.fullmatch(str(row.get("sample_id", ""))) is not None
        and all(isinstance(row.get(name), bool) for name in (
            "quality_passed", "verification_passed", "scope_creep",
        ))
        and all(
            not isinstance(row.get(name), bool)
            and isinstance(row.get(name), int)
            and row.get(name) >= 0
            for name in (
                "high_findings", "retries", "logical_input_tokens",
                "output_tokens",
            )
        )
    )


def _clean_variant_samples(rows, key):
    matching = [
        row for row in rows
        if all(row.get(name) == value for name, value in key.items())
    ][-MODEL_MIN_SAMPLES:]
    clean = (
        len(matching) == MODEL_MIN_SAMPLES
        and all(
            row["quality_passed"] and row["verification_passed"]
            and row["high_findings"] == 0 and row["retries"] == 0
            and not row["scope_creep"]
            and row["logical_input_tokens"] + row["output_tokens"] > 0
            for row in matching
        )
    )
    average = (
        sum(
            row["logical_input_tokens"] + row["output_tokens"]
            for row in matching
        ) // len(matching)
        if matching else 0
    )
    return clean, average, len(matching)


def resolve_execution_variant(
    root, profile, role, task_class, runtime_fingerprint,
    policy_fingerprint, prompt_gate_fingerprint, risk="routine",
):
    if risk not in ("routine", "critical"):
        raise AdaptiveControlError("variant_risk_invalid")
    if (
        not isinstance(profile, dict)
        or not isinstance(profile.get("model_fingerprint"), str)
        or SHA_RE.fullmatch(profile["model_fingerprint"]) is None
        or not isinstance(profile.get("execution_variants"), list)
    ):
        raise AdaptiveControlError("variant_profile_invalid")
    variants = profile["execution_variants"]
    defaults = [
        row for row in variants
        if isinstance(row, dict) and row.get("default") is True
    ]
    if len(defaults) != 1:
        raise AdaptiveControlError("variant_profile_invalid")
    default = defaults[0]
    base_key = {
        "model_fingerprint": profile["model_fingerprint"],
        "role": role,
        "task_class": task_class,
        "runtime_fingerprint": runtime_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "prompt_gate_fingerprint": prompt_gate_fingerprint,
    }
    _review_key(
        base_key["model_fingerprint"], default.get("id"), role, task_class,
        runtime_fingerprint, policy_fingerprint, prompt_gate_fingerprint,
    )
    rows = _adaptive_rows(root, VARIANT_LEDGER_NAME)
    if any(not _valid_variant_row(row) for row in rows):
        raise AdaptiveControlError("variant_ledger_contract_invalid")
    default_key = {**base_key, "execution_variant": default["id"]}
    default_clean, default_average, default_samples = _clean_variant_samples(
        rows, default_key,
    )
    selected = default
    selected_average = default_average
    reason = "critical_default" if risk == "critical" else "evidence_pending"
    if risk == "routine" and default_clean:
        candidates = []
        default_cost = default.get("cost_rank", 100)
        for variant in variants:
            if not isinstance(variant, dict) or variant.get("id") == default["id"]:
                continue
            clean, average, samples = _clean_variant_samples(
                rows, {**base_key, "execution_variant": variant.get("id")},
            )
            if (
                clean
                and variant.get("cost_rank", 100) < default_cost
                and average < default_average
            ):
                candidates.append((
                    average, variant.get("cost_rank", 100),
                    variant.get("id"), variant, samples,
                ))
        if candidates:
            selected_average, _cost, _ident, selected, _samples = min(candidates)
            reason = "verified_lower_cost_equivalence"
    return {
        "schema_version": 1,
        "status": "resolved",
        "execution_variant": selected["id"],
        "default_variant": default["id"],
        "reason": reason,
        "samples": default_samples,
        "average_total_tokens": selected_average,
        "user_gate": False,
    }


def routing_risk(root):
    """Return the conservative current risk bucket without exposing run content."""
    active = _read_json(
        os.path.join(root, ".kimiflow", "session", "ACTIVE_RUN.json"),
        missing_ok=True,
    )
    if not isinstance(active, dict) or active.get("status") != "active":
        return "routine"
    run = active.get("run")
    try:
        run_dir = _safe_run(root, run)
        state = _read_text(os.path.join(run_dir, "STATE.md"))
    except (AdaptiveControlError, TypeError):
        return "critical"
    architecture = _state_value(state, "Architecture deliberation").lower().split(" ", 1)[0]
    build_risk = _state_value(state, "Build risk").lower().split(" ", 1)[0]
    return "critical" if architecture == "active" or build_risk == "required" else "routine"


def _emit(value, pretty):
    print(json.dumps(value, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty))


def _parser():
    parser = argparse.ArgumentParser(prog="adaptive-control")
    commands = parser.add_subparsers(dest="command", required=True)
    classify_parser = commands.add_parser("classify")
    classify_parser.add_argument("--root")
    classify_parser.add_argument("--run", required=True)
    classify_parser.add_argument("--write", action="store_true")
    classify_parser.add_argument("--pretty", action="store_true")
    contract = commands.add_parser("contract")
    contract.add_argument("--root")
    contract.add_argument("--run", required=True)
    contract.add_argument("--stage", choices=("plan", "verify"), required=True)
    contract.add_argument("--pretty", action="store_true")
    handoff = commands.add_parser("rollover-handoff")
    handoff.add_argument("--root")
    handoff.add_argument("--run", required=True)
    handoff.add_argument("--rollover-id", required=True)
    handoff.add_argument("--current-digest", required=True)
    handoff.add_argument("--pretty", action="store_true")
    fallback = commands.add_parser("rollover-fallback")
    fallback.add_argument("--root")
    fallback.add_argument("--run", required=True)
    fallback.add_argument(
        "--reason",
        choices=("capability_unavailable", "fresh_worker_unavailable"),
        default="capability_unavailable",
    )
    fallback.add_argument("--pretty", action="store_true")
    record = commands.add_parser("model-record")
    record.add_argument("--root")
    record.add_argument("--run", required=True)
    record.add_argument("--role")
    record.add_argument("--model")
    record.add_argument("--baseline")
    record.add_argument("--pretty", action="store_true")
    resolve = commands.add_parser("model-resolve")
    resolve.add_argument("--root")
    resolve.add_argument("--roles-json", required=True)
    resolve.add_argument("--risk", choices=("routine", "critical"), default="routine")
    resolve.add_argument("--write", action="store_true")
    resolve.add_argument("--pretty", action="store_true")
    retrieval_record = commands.add_parser("retrieval-record")
    retrieval_record.add_argument("--root")
    retrieval_record.add_argument("--sample-id", required=True)
    retrieval_record.add_argument("--provider-fingerprint", required=True)
    retrieval_record.add_argument("--task-class", required=True)
    retrieval_record.add_argument("--stage", choices=("holdout", "shadow", "canary"), required=True)
    retrieval_record.add_argument("--quality", choices=("passed", "failed"), required=True)
    retrieval_record.add_argument("--verification", choices=("passed", "failed", "not-applicable"), default="not-applicable")
    retrieval_record.add_argument("--high-findings", type=int, default=0)
    retrieval_record.add_argument("--retries", type=int, default=0)
    retrieval_record.add_argument("--logical-input-tokens", type=int, default=0)
    retrieval_record.add_argument("--provider-latency-ms", type=int, default=0)
    retrieval_record.add_argument("--token-waste", action="store_true")
    retrieval_record.add_argument("--snapshot-status", choices=("current", "stale"), default="current")
    retrieval_record.add_argument("--pretty", action="store_true")
    retrieval_resolve = commands.add_parser("retrieval-resolve")
    retrieval_resolve.add_argument("--root")
    retrieval_resolve.add_argument("--provider-fingerprint", required=True)
    retrieval_resolve.add_argument("--task-class", required=True)
    retrieval_resolve.add_argument("--pretty", action="store_true")
    variant_record = commands.add_parser("variant-record")
    variant_record.add_argument("--root")
    variant_record.add_argument("--sample-id", required=True)
    variant_record.add_argument("--model-fingerprint", required=True)
    variant_record.add_argument("--execution-variant", required=True)
    variant_record.add_argument("--role", required=True)
    variant_record.add_argument("--task-class", required=True)
    variant_record.add_argument("--runtime-fingerprint", required=True)
    variant_record.add_argument("--policy-fingerprint", required=True)
    variant_record.add_argument("--prompt-gate-fingerprint", required=True)
    variant_record.add_argument("--quality", choices=("passed", "failed"), required=True)
    variant_record.add_argument("--verification", choices=("passed", "failed"), required=True)
    variant_record.add_argument("--high-findings", type=int, default=0)
    variant_record.add_argument("--retries", type=int, default=0)
    variant_record.add_argument("--logical-input-tokens", type=int, default=0)
    variant_record.add_argument("--output-tokens", type=int, default=0)
    variant_record.add_argument("--scope-creep", action="store_true")
    variant_record.add_argument("--pretty", action="store_true")
    variant_resolve = commands.add_parser("variant-resolve")
    variant_resolve.add_argument("--root")
    variant_resolve.add_argument("--profile-json", required=True)
    variant_resolve.add_argument("--role", required=True)
    variant_resolve.add_argument("--task-class", required=True)
    variant_resolve.add_argument("--runtime-fingerprint", required=True)
    variant_resolve.add_argument("--policy-fingerprint", required=True)
    variant_resolve.add_argument("--prompt-gate-fingerprint", required=True)
    variant_resolve.add_argument("--risk", choices=("routine", "critical"), default="routine")
    variant_resolve.add_argument("--pretty", action="store_true")
    for name in ("review-record", "review-resolve"):
        review = commands.add_parser(name)
        review.add_argument("--root")
        review.add_argument("--model-fingerprint", required=True)
        review.add_argument("--execution-variant", required=True)
        review.add_argument("--role", required=True)
        review.add_argument("--task-class", required=True)
        review.add_argument("--runtime-fingerprint", required=True)
        review.add_argument("--policy-fingerprint", required=True)
        review.add_argument("--prompt-gate-fingerprint", required=True)
        review.add_argument("--pretty", action="store_true")
        if name == "review-record":
            review.add_argument("--sample-id", required=True)
            review.add_argument("--quality", choices=("passed", "failed"), required=True)
            review.add_argument("--high-findings", type=int, default=0)
            review.add_argument("--retries", type=int, default=0)
            review.add_argument("--audit-finding", action="store_true")
        else:
            review.add_argument("--risk", choices=("routine", "critical"), default="routine")
            review.add_argument("--repeated-failure", action="store_true")
            review.add_argument("--regression", action="store_true")
    for name in ("review-cascade-record", "review-cascade-resolve"):
        cascade = commands.add_parser(name)
        cascade.add_argument("--root")
        cascade.add_argument("--model-fingerprint", required=True)
        cascade.add_argument("--execution-variant", required=True)
        cascade.add_argument("--role", required=True)
        cascade.add_argument("--task-class", required=True)
        cascade.add_argument("--runtime-fingerprint", required=True)
        cascade.add_argument("--policy-fingerprint", required=True)
        cascade.add_argument("--prompt-gate-fingerprint", required=True)
        cascade.add_argument("--pretty", action="store_true")
        if name == "review-cascade-record":
            cascade.add_argument("--sample-id", required=True)
            cascade.add_argument(
                "--stage", choices=("holdout", "shadow", "canary"), required=True
            )
            cascade.add_argument(
                "--quality", choices=("passed", "failed"), required=True
            )
            cascade.add_argument(
                "--verification", choices=("passed", "failed"), required=True
            )
            cascade.add_argument("--missed-material-findings", type=int, default=0)
            cascade.add_argument("--retries", type=int, default=0)
            cascade.add_argument("--full-input-tokens", type=int, required=True)
            cascade.add_argument("--full-output-tokens", type=int, required=True)
            cascade.add_argument("--cascade-input-tokens", type=int, required=True)
            cascade.add_argument("--cascade-output-tokens", type=int, required=True)
            cascade.add_argument("--full-rounds", type=int, required=True)
            cascade.add_argument("--cascade-rounds", type=int, required=True)
            cascade.add_argument("--audit-finding", action="store_true")
        else:
            cascade.add_argument("--run")
            cascade.add_argument(
                "--risk", choices=("routine", "critical"), default="routine"
            )
            cascade.add_argument("--repeated-failure", action="store_true")
            cascade.add_argument("--regression", action="store_true")
            cascade.add_argument("--write", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        root = paths.resolve_root(args.root, mode="strict")
        if args.command == "classify":
            value = write_classification(root, args.run) if args.write else classify(root, args.run)
        elif args.command == "contract":
            value = verify_conditional_contract(root, args.run, args.stage)
        elif args.command == "rollover-handoff":
            value = handoff_rollover(
                root, args.run, args.rollover_id, args.current_digest,
            )
        elif args.command == "rollover-fallback":
            value = fallback_rollover(root, args.run, args.reason)
            if value is None:
                value = {
                    "schema_version": 1,
                    "status": "no_pending_rollover",
                    "user_gate": False,
                }
        elif args.command == "model-record":
            identity = (args.role, args.model, args.baseline)
            if any(item is not None for item in identity) and not all(
                item is not None for item in identity
            ):
                raise AdaptiveControlError("model_outcome_identity_incomplete")
            if all(item is not None for item in identity):
                value = record_verified_model_outcome(
                    root, args.run, args.role, args.model, args.baseline,
                )
            else:
                rows = record_observed_model_outcomes(root, args.run)
                value = {
                    "schema_version": 1,
                    "status": "recorded" if rows else "no_attested_route",
                    "count": len(rows),
                    "sample_ids": sorted({
                        row["sample_id"] for row in rows
                        if isinstance(row, dict) and isinstance(row.get("sample_id"), str)
                    }),
                    "user_gate": False,
                }
        elif args.command == "model-resolve":
            configured = json.loads(args.roles_json, object_pairs_hook=_reject_duplicates)
            value = resolve_model_roles(root, configured, args.risk, write=args.write)
        elif args.command == "retrieval-record":
            value = record_retrieval_outcome(
                root, args.sample_id, args.provider_fingerprint, args.task_class,
                args.stage, args.quality == "passed", args.verification == "passed",
                args.high_findings, args.retries, args.logical_input_tokens,
                args.provider_latency_ms, args.token_waste, args.snapshot_status,
            )
        elif args.command == "retrieval-resolve":
            value = resolve_retrieval_route(
                root, args.provider_fingerprint, args.task_class,
            )
        elif args.command == "variant-record":
            value = record_execution_variant_outcome(
                root, args.sample_id, args.model_fingerprint,
                args.execution_variant, args.role, args.task_class,
                args.runtime_fingerprint, args.policy_fingerprint,
                args.prompt_gate_fingerprint, args.quality == "passed",
                args.verification == "passed", args.high_findings,
                args.retries, args.logical_input_tokens, args.output_tokens,
                args.scope_creep,
            )
        elif args.command == "variant-resolve":
            profile = json.loads(
                args.profile_json, object_pairs_hook=_reject_duplicates
            )
            value = resolve_execution_variant(
                root, profile, args.role, args.task_class,
                args.runtime_fingerprint, args.policy_fingerprint,
                args.prompt_gate_fingerprint, args.risk,
            )
        elif args.command == "review-record":
            value = record_review_outcome(
                root, args.sample_id, args.model_fingerprint, args.execution_variant,
                args.role, args.task_class, args.runtime_fingerprint,
                args.policy_fingerprint, args.prompt_gate_fingerprint,
                args.quality == "passed", args.high_findings, args.retries,
                args.audit_finding,
            )
        elif args.command == "review-resolve":
            value = resolve_review_mode(
                root, args.model_fingerprint, args.execution_variant, args.role,
                args.task_class, args.runtime_fingerprint, args.policy_fingerprint,
                args.prompt_gate_fingerprint, args.risk, args.repeated_failure,
                args.regression,
            )
        elif args.command == "review-cascade-record":
            value = record_review_cascade_outcome(
                root,
                args.sample_id,
                args.model_fingerprint,
                args.execution_variant,
                args.role,
                args.task_class,
                args.runtime_fingerprint,
                args.policy_fingerprint,
                args.prompt_gate_fingerprint,
                args.stage,
                args.quality == "passed",
                args.verification == "passed",
                args.missed_material_findings,
                args.retries,
                args.full_input_tokens,
                args.full_output_tokens,
                args.cascade_input_tokens,
                args.cascade_output_tokens,
                args.full_rounds,
                args.cascade_rounds,
                args.audit_finding,
            )
        elif args.write:
            if not args.run:
                raise AdaptiveControlError("review_cascade_run_required")
            value = write_review_cascade_route(
                root,
                args.run,
                args.model_fingerprint,
                args.execution_variant,
                args.role,
                args.task_class,
                args.runtime_fingerprint,
                args.policy_fingerprint,
                args.prompt_gate_fingerprint,
                args.risk,
                args.repeated_failure,
                args.regression,
            )
        else:
            value = resolve_review_cascade(
                root,
                args.model_fingerprint,
                args.execution_variant,
                args.role,
                args.task_class,
                args.runtime_fingerprint,
                args.policy_fingerprint,
                args.prompt_gate_fingerprint,
                args.risk,
                args.repeated_failure,
                args.regression,
            )
        _emit(value, args.pretty)
        return 0 if value.get("status") != "CLOSED" else 1
    except (AdaptiveControlError, ValueError, json.JSONDecodeError) as exc:
        print("adaptive-control: %s" % exc, file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
