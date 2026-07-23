"""Deterministic preview, quarantine, and restore for project learnings."""
import contextlib
import io
import os
import re
import signal
import stat
import threading
from datetime import datetime

from . import attribution as attribution_module
from . import contracts, curate, memory_md, rows, store, summaries, usage_metrics
from .cli import die, resolve_root, usage

_MAX_ROWS = 20
_MAX_LEARNING_BYTES = 8 * 1024 * 1024
_MAX_LEARNING_ROWS = 4096
_MAX_DERIVATIVE_BYTES = 8 * 1024 * 1024
_MAX_USAGE_BYTES = 8 * 1024 * 1024
_MAX_OUTCOME_BYTES = 8 * 1024 * 1024
_MAX_OUTCOME_ROWS = 4096
_MAX_RECALL_TOTAL_BYTES = 8 * 1024 * 1024
_NO_USAGE_SNAPSHOT = object()
_OUTCOME_ID = re.compile(r"^out_[0-9a-f]{64}$")
_RECALL_ID = re.compile(r"^rec_[0-9a-f]{64}$")
_SOURCE_HEAD = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_PATH = re.compile(r"^\.kimiflow/[A-Za-z0-9][A-Za-z0-9._-]*$")
_ISO_TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class LifecycleDeadlineError(BaseException):
    """Cooperative deadline that preserves enough time for transactional rollback."""


@contextlib.contextmanager
def _deadline_guard():
    raw = os.environ.get("KIMIFLOW_LIFECYCLE_DEADLINE_SECONDS", "")
    if not raw:
        yield
        return
    if not raw.isascii() or not raw.isdigit() or int(raw) <= 0:
        raise ValueError("invalid lifecycle deadline")
    if not hasattr(signal, "setitimer") or threading.current_thread() is not threading.main_thread():
        yield
        return
    seconds = int(raw)
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def deadline(_signum, _frame):
        raise LifecycleDeadlineError("lifecycle deadline exceeded")

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer != (0.0, 0.0):
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def _disarm_lifecycle_deadline():
    if (
        os.environ.get("KIMIFLOW_LIFECYCLE_DEADLINE_SECONDS", "")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    ):
        signal.setitimer(signal.ITIMER_REAL, 0)


def _segments_text(text, max_rows=None):
    """Split JSONL by LF while retaining every original terminator."""
    result = []
    start = 0
    while True:
        end = text.find("\n", start)
        if end < 0:
            if start < len(text):
                result.append((text[start:], ""))
                if max_rows is not None and len(result) > max_rows:
                    raise ValueError("LEARNINGS.jsonl exceeds lifecycle row limit")
            break
        raw = text[start:end]
        if raw.endswith("\r"):
            result.append((raw[:-1], "\r\n"))
        else:
            result.append((raw, "\n"))
        if max_rows is not None and len(result) > max_rows:
            raise ValueError("LEARNINGS.jsonl exceeds lifecycle row limit")
        start = end + 1
    return result


def _segments_snapshot(snapshot):
    if snapshot is None:
        return []
    try:
        return _segments_text(snapshot[1].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("LEARNINGS.jsonl is not valid UTF-8") from exc


def _bounded_learning_segments(snapshot):
    if snapshot is not None and len(snapshot[1]) > _MAX_LEARNING_BYTES:
        raise ValueError("LEARNINGS.jsonl exceeds lifecycle size limit")
    if snapshot is None:
        return []
    try:
        content = snapshot[1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("LEARNINGS.jsonl is not valid UTF-8") from exc
    return _segments_text(content, max_rows=_MAX_LEARNING_ROWS)


def _segments(path, allow_detached=False):
    snapshot = store.stable_file_snapshot(
        path, missing_ok=True, allow_detached=allow_detached,
        max_bytes=_MAX_LEARNING_BYTES,
    )
    return _bounded_learning_segments(snapshot)


def _parsed(segment):
    return store.parse_json_object_strict(segment)


def _id_counts(entries):
    counts = {}
    for raw, _ending, _row in entries:
        for rid in store.top_level_string_values(raw, "id"):
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def _evidence_is_current(root, row):
    evidence = row.get("evidence")
    stored = row.get("evidence_fingerprints")
    if not isinstance(evidence, list) or not evidence:
        return False
    if any(not isinstance(ref, str) or not ref
           or ref in ("NOT VERIFIED", "OUTSIDE_REPO") for ref in evidence):
        return False
    if not isinstance(stored, list) or not stored:
        return False
    if not all(isinstance(fp, dict) and fp.get("status") == "current" for fp in stored):
        return False
    current = rows.evidence_fingerprints_json(root, evidence)
    if not current or not all(isinstance(fp, dict) and fp.get("status") == "current"
                              for fp in current):
        return False
    return contracts.dumps(stored) == contracts.dumps(current)


def _evidence_drifted(root, row):
    stored = row.get("evidence_fingerprints")
    return (
        isinstance(stored, list) and bool(stored)
        and all(isinstance(fp, dict) and fp.get("status") == "current" for fp in stored)
        and not _evidence_is_current(root, row)
    )


def _safe_relative_path(value):
    return (
        isinstance(value, str)
        and bool(value)
        and "\\" not in value
        and not value.startswith("/")
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def _valid_fingerprint(item):
    if not isinstance(item, dict):
        return False
    reference = item.get("ref")
    path = item.get("path")
    digest = item.get("sha256")
    if (
        not isinstance(reference, str)
        or not isinstance(path, str)
        or not _safe_relative_path(path)
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or item.get("digest") != digest
        or item.get("digest_algorithm") != "sha256"
        or item.get("status") != "current"
    ):
        return False
    if reference == path:
        return True
    if not reference.startswith(path + ":"):
        return False
    line = reference[len(path) + 1:]
    return line.isdigit() and int(line) > 0


def _fingerprints_by_ref(values):
    if not isinstance(values, list):
        return None
    result = {}
    for item in values:
        if not _valid_fingerprint(item):
            return None
        reference = item["ref"]
        if reference in result and result[reference] != item:
            return None
        result[reference] = item
    return result


def _require_verified_outcome(outcome):
    """Validate the minimum immutable proof carried by one outcome-ledger row."""
    classification = outcome.get("classification")
    terminal = outcome.get("terminal")
    expected_terminal = {
        "verified_success": "done",
        "verified_failure": "failed",
    }.get(classification)
    run = outcome.get("run")
    strategy = outcome.get("strategy")
    signals = outcome.get("signals")
    verification = signals.get("verification") if isinstance(signals, dict) else None
    affected = outcome.get("affected_paths")
    evidence = outcome.get("evidence")
    fingerprints = outcome.get("evidence_fingerprints")
    fingerprints_by_ref = _fingerprints_by_ref(fingerprints)
    valid = (
        type(outcome.get("schema_version")) is int
        and outcome.get("schema_version") == 1
        and isinstance(run, str)
        and _RUN_PATH.fullmatch(run) is not None
        and expected_terminal is not None
        and terminal == expected_terminal
        and outcome.get("promotable") is True
        and isinstance(strategy, dict)
        and isinstance(strategy.get("summary"), str)
        and bool(strategy.get("summary").strip())
        and isinstance(outcome.get("source_head"), str)
        and _SOURCE_HEAD.fullmatch(outcome["source_head"]) is not None
        and isinstance(affected, list)
        and all(_safe_relative_path(path) for path in affected)
        and isinstance(signals, dict)
        and isinstance(verification, dict)
        and isinstance(evidence, list)
        and all(isinstance(ref, str) for ref in evidence)
        and run + "/PLAN.md" in evidence
        and isinstance(fingerprints_by_ref, dict)
        and bool(fingerprints_by_ref)
        and set(fingerprints_by_ref) == set(evidence)
    )
    if not valid:
        raise ValueError("strategy outcome ledger row violates verified outcome contract")
    if classification == "verified_success":
        if (
            not affected
            or signals.get("phase6") != "done"
            or signals.get("recovery") != "clean"
            or type(signals.get("items_open")) is not int
            or signals.get("items_open") != 0
            or verification != {
                "outcome": "passed",
                "criteria": "passed",
                "regression": "passed",
            }
            or signals.get("code_review") != "clean"
            or signals.get("learning_review") != "open"
            or run + "/VERIFICATION.md" not in evidence
        ):
            raise ValueError("strategy outcome success proof is inconsistent")
    else:
        explicit_failure = (
            verification.get("outcome") == "failed"
            and "failed" in (
                verification.get("criteria"),
                verification.get("regression"),
            )
        ) or signals.get("code_review") == "blocking"
        if not explicit_failure:
            raise ValueError("strategy outcome failure proof is inconsistent")


def _require_attribution_contract(outcome, attribution_value, sealed_hits):
    """Validate recall linkage before any item can become a learning signal."""
    applied = attribution_value.get("applied_ids")
    items = attribution_value.get("items")
    artifact_fingerprints = attribution_value.get("artifact_fingerprints")
    contradiction_fingerprints = attribution_value.get("contradiction_fingerprints")
    contradiction_evidence = attribution_value.get("contradiction_evidence")
    artifact_by_ref = _fingerprints_by_ref(artifact_fingerprints)
    contradiction_by_ref = _fingerprints_by_ref(contradiction_fingerprints)
    outer_by_ref = _fingerprints_by_ref(outcome.get("evidence_fingerprints"))
    required_artifacts = {
        outcome["run"] + "/PLAN.md",
        outcome["run"] + "/RECALL.json",
        outcome["run"] + "/VERIFICATION.md",
    }
    if (
        attribution_value.get("contract") != 1
        or attribution_value.get("terminal") != outcome.get("terminal")
        or attribution_value.get("status") not in ("complete", "inconclusive")
        or attribution_value.get("classification") not in (
            "helpful", "contradicted", "neutral",
        )
        or not isinstance(applied, list)
        or not all(isinstance(value, str) and _RECALL_ID.fullmatch(value)
                   for value in applied)
        or len(set(applied)) != len(applied)
        or not isinstance(items, list)
        or len(items) != len(applied)
        or not isinstance(sealed_hits, dict)
        or not isinstance(artifact_by_ref, dict)
        or len(artifact_fingerprints) != 3
        or set(artifact_by_ref) != required_artifacts
        or not isinstance(contradiction_by_ref, dict)
        or not isinstance(contradiction_evidence, list)
        or not all(isinstance(ref, str) for ref in contradiction_evidence)
        or set(contradiction_by_ref) != set(contradiction_evidence)
        or not isinstance(outer_by_ref, dict)
        or any(
            outer_by_ref.get(reference) != fingerprint
            for sealed in (artifact_by_ref, contradiction_by_ref)
            for reference, fingerprint in sealed.items()
        )
    ):
        raise ValueError("strategy outcome recall attribution is malformed")
    item_ids = []
    item_classifications = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("strategy outcome recall item is malformed")
        recall_id = item.get("recall_id")
        classification = item.get("classification")
        decision_checks = item.get("decision_checks")
        evidence = item.get("evidence")
        sealed_hit = sealed_hits.get(recall_id)
        if (
            not isinstance(recall_id, str)
            or not _RECALL_ID.fullmatch(recall_id)
            or recall_id not in applied
            or not isinstance(sealed_hit, dict)
            or item.get("source") != sealed_hit.get("source")
            or classification not in ("helpful", "contradicted", "neutral")
            or not isinstance(item.get("source"), str)
            or not isinstance(decision_checks, dict)
            or not all(
                isinstance(key, str)
                and re.fullmatch(r"D[1-9][0-9]*", key)
                and value in ("passed", "failed", "missing")
                for key, value in decision_checks.items()
            )
            or not isinstance(evidence, list)
            or not all(isinstance(ref, str) for ref in evidence)
            or not set(evidence).issubset(set(contradiction_evidence))
        ):
            raise ValueError("strategy outcome recall item is malformed")
        if "learning_id" in item and (
            item.get("source") != "learnings"
            or item.get("learning_id") != sealed_hit.get("learning_id")
            or not isinstance(sealed_hit.get("learning_fingerprint"), str)
            or _SHA256.fullmatch(sealed_hit["learning_fingerprint"]) is None
        ):
            raise ValueError("strategy outcome learning id is detached from sealed recall")
        contradicted = bool(evidence) or "failed" in decision_checks.values()
        helpful = (
            outcome.get("terminal") == "done"
            and bool(decision_checks)
            and all(value == "passed" for value in decision_checks.values())
            and not evidence
        )
        expected_item = "contradicted" if contradicted else (
            "helpful" if helpful else "neutral"
        )
        if classification != expected_item:
            raise ValueError("strategy outcome recall item classification is inconsistent")
        item_ids.append(recall_id)
        item_classifications.append(classification)
    if len(set(item_ids)) != len(item_ids) or set(item_ids) != set(applied):
        raise ValueError("strategy outcome recall attribution linkage is malformed")
    expected = (
        "contradicted" if "contradicted" in item_classifications
        else "helpful" if "helpful" in item_classifications
        else "neutral"
    )
    if attribution_value.get("classification") != expected:
        raise ValueError("strategy outcome recall attribution classification is inconsistent")
    if outcome.get("terminal") != "done" and expected == "helpful":
        raise ValueError("non-success outcome cannot carry helpful recall attribution")
    expected_status = (
        "complete"
        if outcome.get("classification") == "verified_success"
        else "inconclusive"
    )
    if attribution_value.get("status") != expected_status:
        raise ValueError("strategy outcome recall attribution status is inconsistent")
    signals = outcome["signals"]
    if signals.get("recall_attribution") != expected:
        raise ValueError("strategy outcome recall signal is inconsistent")


def _outcome_signals(root, snapshot):
    """Derive per-learning verified-use signals from one strict bounded ledger."""
    if snapshot is None:
        return {}
    data = snapshot[1]
    if len(data) > _MAX_OUTCOME_BYTES:
        raise ValueError("strategy outcome ledger exceeds lifecycle size limit")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("strategy outcome ledger is not valid UTF-8") from exc
    raw_rows = [line for line in content.split("\n") if line.strip()]
    if len(raw_rows) > _MAX_OUTCOME_ROWS:
        raise ValueError("strategy outcome ledger exceeds lifecycle row limit")
    seen_outcomes = set()
    seen_runs = set()
    events = []
    recall_cache = {}
    recall_bytes = 0
    for sequence, raw in enumerate(raw_rows):
        outcome = store.parse_json_object_strict(raw)
        if outcome is None:
            raise ValueError("strategy outcome ledger is malformed")
        outcome_id = outcome.get("id")
        evaluated_at = outcome.get("evaluated_at")
        if (not isinstance(outcome_id, str) or not _OUTCOME_ID.fullmatch(outcome_id)
                or outcome_id in seen_outcomes):
            raise ValueError("strategy outcome ledger has an invalid or duplicate id")
        seen_outcomes.add(outcome_id)
        if not isinstance(evaluated_at, str) or not _ISO_TIME.fullmatch(evaluated_at):
            raise ValueError("strategy outcome ledger has an invalid evaluated_at")
        try:
            datetime.strptime(evaluated_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise ValueError("strategy outcome ledger has an invalid evaluated_at") from exc
        _require_verified_outcome(outcome)
        if outcome["run"] in seen_runs:
            raise ValueError("strategy outcome ledger repeats one run")
        seen_runs.add(outcome["run"])
        attribution = outcome.get("recall_attribution")
        if attribution is None:
            continue
        if not isinstance(attribution, dict):
            raise ValueError("strategy outcome recall attribution is malformed")
        recall_ref = outcome["run"] + "/RECALL.json"
        sealed_by_ref = _fingerprints_by_ref(
            attribution.get("artifact_fingerprints")
        )
        recall_seal = sealed_by_ref.get(recall_ref) if isinstance(sealed_by_ref, dict) else None
        if not isinstance(recall_seal, dict):
            raise ValueError("strategy outcome recall seal is missing")
        cache_key = (outcome["run"], recall_seal["sha256"])
        if cache_key not in recall_cache:
            try:
                sealed_hits, byte_count = attribution_module.sealed_recall_hit_map(
                    root, outcome["run"], recall_seal["sha256"],
                )
            except attribution_module.AttributionError as exc:
                raise ValueError(
                    "sealed recall artifact is unavailable"
                ) from exc
            recall_bytes += byte_count
            if recall_bytes > _MAX_RECALL_TOTAL_BYTES:
                raise ValueError("sealed recall artifacts exceed lifecycle size limit")
            recall_cache[cache_key] = sealed_hits
        _require_attribution_contract(
            outcome, attribution, recall_cache[cache_key]
        )
        items = attribution["items"]
        seen_learning_ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("strategy outcome recall item is malformed")
            if item.get("source") != "learnings":
                continue
            recall_id = item.get("recall_id")
            classification = item.get("classification")
            sealed_hit = recall_cache[cache_key].get(recall_id)
            learning_id = (
                item.get("learning_id")
                if "learning_id" in item
                else sealed_hit.get("learning_id")
                if isinstance(sealed_hit, dict)
                else None
            )
            if (not isinstance(learning_id, str) or not 0 < len(learning_id) <= 128
                    or not isinstance(sealed_hit, dict)
                    or item.get("learning_id", learning_id) != learning_id
                    or not isinstance(sealed_hit.get("learning_fingerprint"), str)
                    or not isinstance(recall_id, str) or not _RECALL_ID.fullmatch(recall_id)
                    or classification not in ("helpful", "contradicted", "neutral")):
                raise ValueError("strategy outcome learning attribution is malformed")
            if learning_id in seen_learning_ids:
                raise ValueError("strategy outcome repeats one learning id")
            seen_learning_ids.add(learning_id)
            if classification != "neutral":
                events.append((
                    sequence, evaluated_at, outcome_id, recall_id, learning_id, classification,
                    sealed_hit["learning_fingerprint"],
                ))
    signals = {}
    ordered_events = sorted(
        events,
        key=lambda event: (
            event[0],
            1 if event[5] == "contradicted" else 0,
            event[2],
            event[3],
        ),
    )
    for (_sequence, evaluated_at, _outcome_id, _recall_id, learning_id, classification,
         learning_fingerprint) in ordered_events:
        value = signals.get(learning_id)
        if value is None or value["learning_fingerprint"] != learning_fingerprint:
            value = {
                "helpful_count": 0,
                "contradicted_count": 0,
                "helpful_streak": 0,
                "last_classification": None,
                "last_evaluated_at": None,
                "learning_fingerprint": learning_fingerprint,
            }
            signals[learning_id] = value
        if classification == "helpful":
            value["helpful_count"] += 1
            value["helpful_streak"] += 1
        else:
            value["contradicted_count"] += 1
            value["helpful_streak"] = 0
        value["last_classification"] = classification
        value["last_evaluated_at"] = evaluated_at
    return signals


def _curation_metadata(signal, reason):
    value = {
        "contract": 1,
        "helpful_count": signal["helpful_count"],
        "contradicted_count": signal["contradicted_count"],
        "helpful_streak": signal["helpful_streak"],
        "last_classification": signal["last_classification"],
        "last_evaluated_at": signal["last_evaluated_at"],
        "reason": reason,
    }
    if isinstance(signal.get("learning_fingerprint"), str):
        value["learning_fingerprint"] = signal["learning_fingerprint"]
    return value


def _refresh_derivatives(root):
    memory_md.write_bounded_memory(root)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        curate.refresh(root, allow_network=False)


def _file_mode(path):
    try:
        return stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    except OSError:
        return 0o644


def _restore_text_snapshot(path, snapshot):
    if snapshot is None:
        try:
            store._unlink_path(path)
        except FileNotFoundError:
            pass
        return
    try:
        content = snapshot[1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("derived memory is not valid UTF-8") from exc
    store.atomic_write(path, content, mode=snapshot[2], allow_detached=True)


def _validate_text_snapshot(snapshot):
    if snapshot is None:
        return
    if len(snapshot[1]) > _MAX_DERIVATIVE_BYTES:
        raise ValueError("derived memory exceeds lifecycle size limit")
    try:
        snapshot[1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("derived memory is not valid UTF-8") from exc


def _bounded_output_id(value):
    return value if isinstance(value, str) and len(value) <= 256 else ""


def _restore_unsafe_concurrent_source(path, original, mode):
    """Restore bounded original state and retain an unsafe concurrent source.

    This path deliberately avoids reading the oversized or over-row-budget
    source into memory. The atomic exchange keeps the concurrent bytes in a
    recovery file while restoring the pre-curation content.
    """
    descriptor, temporary = store._temporary_file(path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(original)
        anchor = store._active_anchor(temporary)
        if anchor is not None:
            os.chmod(os.path.basename(temporary), mode, dir_fd=anchor["descriptor"])
        else:
            os.chmod(temporary, mode)
        desired = store._file_snapshot(temporary, max_bytes=_MAX_LEARNING_BYTES)
        if desired is None:
            raise OSError("cannot snapshot bounded lifecycle recovery")
        store._exchange_paths(temporary, path)
        try:
            installed = store._file_snapshot(path, max_bytes=_MAX_LEARNING_BYTES)
        except store.FileTooLargeError:
            recovery = store._retain_conflict_copy(temporary)
            temporary = ""
            raise store.ConcurrentWriteError(
                "source changed during bounded recovery; concurrent copy retained at %s"
                % os.path.basename(recovery)
            )
        recovery = store._retain_conflict_copy(temporary)
        temporary = ""
        if not store._same_snapshot(installed, desired):
            raise store.ConcurrentWriteError(
                "source changed during bounded recovery; concurrent copy retained at %s"
                % os.path.basename(recovery)
            )
        raise store.ConcurrentWriteError(
            "unsafe concurrent source retained at %s"
            % os.path.basename(recovery)
        )
    finally:
        if temporary:
            try:
                store._unlink_path(temporary)
            except OSError:
                pass


def _write_and_refresh(root, path, original_entries, output_entries, source_snapshot,
                       usage_file=None, usage_snapshot=_NO_USAGE_SNAPSHOT,
                       outcome_file=None, outcome_snapshot=None):
    original = "".join(raw + ending for raw, ending, _row in original_entries)
    mode = source_snapshot[2] if source_snapshot is not None else _file_mode(path)
    content = "".join(
        (contracts.dumps(row) if changed else raw) + ending
        for raw, ending, row, changed in output_entries
    )
    if len(content.encode("utf-8")) > _MAX_LEARNING_BYTES:
        raise ValueError("curated LEARNINGS.jsonl exceeds lifecycle size limit")
    if usage_snapshot is not _NO_USAGE_SNAPSHOT:
        current_usage = store.stable_file_snapshot(
            usage_file, missing_ok=True, max_bytes=_MAX_USAGE_BYTES
        )
        if current_usage != usage_snapshot:
            raise store.ConcurrentWriteError("usage state changed during lifecycle evaluation")
    if outcome_file is not None:
        current_outcome = store.stable_file_snapshot(
            outcome_file, missing_ok=True, max_bytes=_MAX_OUTCOME_BYTES
        )
        if current_outcome != outcome_snapshot:
            raise store.ConcurrentWriteError("outcome ledger changed during lifecycle evaluation")
    project = os.path.join(root, ".kimiflow", "project")
    memory_path = os.path.join(project, "MEMORY.md")
    index_path = os.path.join(project, "MEMORY-INDEX.json")
    database_path = os.path.join(project, "RECALL.sqlite")
    memory_snapshot = store.stable_file_snapshot(
        memory_path, missing_ok=True, max_bytes=_MAX_DERIVATIVE_BYTES
    )
    index_snapshot = store.stable_file_snapshot(
        index_path, missing_ok=True, max_bytes=_MAX_DERIVATIVE_BYTES
    )
    _validate_text_snapshot(memory_snapshot)
    _validate_text_snapshot(index_snapshot)
    try:
        store.atomic_write(
            path, content, mode=mode, expected=original,
            expected_snapshot=source_snapshot,
            max_bytes=_MAX_LEARNING_BYTES,
        )
        _refresh_derivatives(root)
        store.stable_file_snapshot(
            memory_path, missing_ok=True, max_bytes=_MAX_DERIVATIVE_BYTES
        )
        store.stable_file_snapshot(
            index_path, missing_ok=True, max_bytes=_MAX_DERIVATIVE_BYTES
        )
        _disarm_lifecycle_deadline()
    except BaseException as refresh_error:
        rollback_errors = []
        try:
            try:
                store.atomic_write(
                    path, original, mode=mode, expected=content, allow_detached=True,
                    max_bytes=_MAX_LEARNING_BYTES,
                )
            except (store.ConcurrentWriteError, store.FileTooLargeError):
                try:
                    latest = [(raw, ending, _parsed(raw))
                              for raw, ending in _segments(path, allow_detached=True)]
                except ValueError:
                    _restore_unsafe_concurrent_source(path, original, mode)
                rollback_rows = {}
                for (original_raw, _original_ending, original_row), (_raw, _ending, changed_row, changed) in zip(
                        original_entries, output_entries):
                    if changed and isinstance(original_row, dict) and isinstance(changed_row, dict):
                        rollback_rows[changed_row.get("id")] = (changed_row, original_raw)
                latest_text = "".join(raw + ending for raw, ending, _row in latest)
                merged = []
                restored_ids = set()
                for raw, ending, row in latest:
                    rid = row.get("id") if isinstance(row, dict) else None
                    replacement = rollback_rows.get(rid)
                    if (
                        replacement is not None
                        and rid not in restored_ids
                        and row == replacement[0]
                    ):
                        raw = replacement[1]
                        restored_ids.add(rid)
                    merged.append(raw + ending)
                merged_text = "".join(merged)
                if len(merged_text.encode("utf-8")) > _MAX_LEARNING_BYTES:
                    _restore_unsafe_concurrent_source(path, original, mode)
                store.atomic_write(
                    path, merged_text, mode=mode, expected=latest_text,
                    allow_detached=True, max_bytes=_MAX_LEARNING_BYTES,
                )
        except BaseException as exc:
            rollback_errors.append(exc)
        for derivative_path, derivative_snapshot in (
            (memory_path, memory_snapshot),
            (index_path, index_snapshot),
        ):
            try:
                _restore_text_snapshot(derivative_path, derivative_snapshot)
            except BaseException as exc:
                rollback_errors.append(exc)
        try:
            store._unlink_path(database_path)
        except FileNotFoundError:
            pass
        except BaseException as exc:
            rollback_errors.append(exc)
        if rollback_errors:
            raise rollback_errors[0]
        raise refresh_error


def _restore(root, learnings, entries, source_snapshot, requested, write, pretty):
    counts = _id_counts(entries)
    matches = [row for _raw, _ending, row in entries
               if isinstance(row, dict) and row.get("id") == requested]
    quarantined = [row for row in matches if row.get("status") == "quarantined"]
    if not matches or not quarantined:
        reason = "not_quarantined"
    elif counts.get(requested, 0) != 1 or len(matches) != 1 or len(quarantined) != 1:
        reason = "duplicate_id"
    elif not _evidence_is_current(root, quarantined[0]):
        reason = "evidence_drift"
    else:
        reason = None

    if reason:
        contracts.json_print({
            "schema_version": 1,
            "status": "refused",
            "written": False,
            "restored_id": None,
            "reason": reason,
        }, pretty)
        return 1

    if write:
        output = []
        for raw, ending, row in entries:
            changed = row is quarantined[0]
            if changed:
                row = dict(row)
                row["status"] = "current"
            output.append((raw, ending, row, changed))
        try:
            _write_and_refresh(
                root, learnings, entries, output, source_snapshot
            )
        except (store.ConcurrentWriteError, OSError) as exc:
            return die("lifecycle: %s; retry" % exc, 1)
    contracts.json_print({
        "schema_version": 1,
        "status": "restored" if write else "restore_preview",
        "written": write,
        "restored_id": _bounded_output_id(requested),
        "reason": None,
    }, pretty)
    return 0


def _operate(root, learnings, usage_file, outcome_file, entries, source_snapshot,
             usage_snapshot, outcome_snapshot, restore, write, pretty):
    if restore is not None:
        if not restore:
            return die("lifecycle: --restore requires a nonempty id", 2)
        return _restore(
            root, learnings, entries, source_snapshot, restore, write, pretty
        )

    raw_entries = entries
    counts = _id_counts(raw_entries)
    utility = summaries.learning_utility_rows(
        learnings,
        usage_file,
        max_rows=None,
        learning_rows=[row for _raw, _ending, row in raw_entries if isinstance(row, dict)],
        usage_snapshot=usage_snapshot,
    )
    signals = _outcome_signals(root, outcome_snapshot)
    row_by_id = {
        row.get("id"): row for _raw, _ending, row in raw_entries
        if isinstance(row, dict) and isinstance(row.get("id"), str)
        and counts.get(row.get("id")) == 1
    }
    promoted_candidates = []
    demoted_candidates = []
    protected_ids = []
    metadata = {}
    content_fingerprints = {}
    for rid, row in row_by_id.items():
        if (row.get("status") if row.get("status") not in (None, False)
                else "current") != "current":
            continue
        tier = rows.learning_maturity(row)
        if (
            tier == rows.MATURITY_INVALID
            or row.get("scope", "project") != "project"
            or row.get("sensitivity", "normal") not in ("normal", "public")
            or (
                "security_scan" in row
                and (
                    not isinstance(row.get("security_scan"), dict)
                    or set(row["security_scan"]) != {"ok", "reasons"}
                    or row["security_scan"].get("ok") is not True
                    or row["security_scan"].get("reasons") != []
                )
            )
        ):
            protected_ids.append(rid)
            continue
        signal = signals.get(rid)
        evidence_current = _evidence_is_current(root, row)
        evidence_drift = _evidence_drifted(root, row)
        try:
            current_fingerprint = rows.learning_content_fingerprint(row)
            content_fingerprints[rid] = current_fingerprint
            content_matches = (
                signal is not None
                and signal["learning_fingerprint"]
                == current_fingerprint
            )
        except ValueError:
            protected_ids.append(rid)
            continue
        reason = None
        if signal is not None and not content_matches:
            reason = "content_drift"
            if tier == rows.MATURITY_DURABLE:
                demoted_candidates.append(rid)
        elif signal is not None and signal["last_classification"] == "contradicted":
            reason = "verified_contradiction"
            if tier == rows.MATURITY_DURABLE:
                demoted_candidates.append(rid)
        elif evidence_drift:
            reason = "evidence_drift"
            if tier == rows.MATURITY_DURABLE:
                demoted_candidates.append(rid)
        elif (signal is not None and signal["last_classification"] == "helpful"
              and signal["helpful_streak"] >= 2 and evidence_current):
            reason = "verified_helpful_streak"
            if (tier == rows.MATURITY_PROBATIONARY
                    and row.get("confidence", "medium") in ("medium", "high")
                    and row.get("sensitivity", "normal") in ("normal", "public")):
                promoted_candidates.append(rid)
        elif signal is not None:
            reason = "awaiting_repeat_evidence"
        if signal is not None:
            metadata[rid] = _curation_metadata(signal, reason or "observed")
        elif evidence_drift:
            metadata[rid] = _curation_metadata({
                "helpful_count": 0,
                "contradicted_count": 0,
                "helpful_streak": 0,
                "last_classification": None,
                "last_evaluated_at": None,
            }, "evidence_drift")

    changed_trust = set(promoted_candidates + demoted_candidates)
    eligible = [
        item["id"] for item in utility
        if item["quarantine_eligible"]
        and isinstance(item["id"], str) and item["id"]
        and counts.get(item["id"]) == 1
        and item["id"] not in changed_trust
        and item["id"] not in protected_ids
        and (
            signals.get(item["id"]) is None
            or (
                signals[item["id"]]["learning_fingerprint"]
                == content_fingerprints.get(item["id"])
                and signals[item["id"]]["helpful_count"] == 0
            )
        )
        and rows.learning_maturity(row_by_id.get(item["id"], {})) != rows.MATURITY_INVALID
        and row_by_id.get(item["id"], {}).get("scope", "project") == "project"
        and row_by_id.get(item["id"], {}).get(
            "sensitivity", "normal"
        ) in ("normal", "public")
    ]
    empty_signal = {
        "helpful_count": 0,
        "contradicted_count": 0,
        "helpful_streak": 0,
        "last_classification": None,
        "last_evaluated_at": None,
    }
    for rid in eligible:
        metadata[rid] = _curation_metadata(
            signals.get(rid, empty_signal),
            "stale_unused_quarantine",
        )

    quarantined = []
    promoted = []
    demoted = []
    metadata_updated = []
    if write and (eligible or promoted_candidates or demoted_candidates or metadata):
        selected = set(eligible)
        promote_selected = set(promoted_candidates)
        demote_selected = set(demoted_candidates)
        output = []
        for raw, ending, row in raw_entries:
            changed = False
            rid = row.get("id") if isinstance(row, dict) else None
            current = (
                isinstance(row, dict)
                and (row.get("status") if row.get("status") not in (None, False)
                     else "current") == "current"
            )
            if current and rid in selected:
                row = dict(row)
                row["status"] = "quarantined"
                quarantined.append(rid)
                changed = True
            elif current and rid in promote_selected:
                row = dict(row)
                row["maturity"] = rows.MATURITY_DURABLE
                promoted.append(rid)
                changed = True
            elif current and rid in demote_selected:
                row = dict(row)
                row["maturity"] = rows.MATURITY_PROBATIONARY
                demoted.append(rid)
                changed = True
            if current and rid in metadata and row.get("curation") != metadata[rid]:
                if not changed:
                    row = dict(row)
                row["curation"] = metadata[rid]
                metadata_updated.append(rid)
                changed = True
            output.append((raw, ending, row, changed))
        if any(item[3] for item in output):
            try:
                _write_and_refresh(
                    root, learnings, raw_entries, output, source_snapshot,
                    usage_file=usage_file, usage_snapshot=usage_snapshot,
                    outcome_file=outcome_file, outcome_snapshot=outcome_snapshot,
                )
            except (store.ConcurrentWriteError, OSError) as exc:
                return die("lifecycle: %s; retry" % exc, 1)

    utility_rows = []
    for item in utility:
        enriched = dict(item)
        row = row_by_id.get(item["id"], {})
        enriched["maturity"] = rows.learning_maturity(row)
        signal = signals.get(item["id"])
        if signal is not None:
            enriched["verified_use"] = dict(signal)
        utility_rows.append(enriched)
    changed_count = len(set(quarantined + promoted + demoted + metadata_updated))
    changed_ids = set(quarantined + promoted + demoted + metadata_updated)
    reason_counts = {}
    for rid in changed_ids:
        reason = metadata.get(rid, {}).get("reason")
        if isinstance(reason, str) and reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    status = "preview"
    if write:
        status = "curated" if promoted or demoted or metadata_updated else (
            "quarantined" if quarantined else "unchanged"
        )
    contracts.json_print({
        "schema_version": 1,
        "status": status,
        "written": changed_count > 0,
        "changed_count": changed_count,
        "reason_counts": dict(sorted(reason_counts.items())),
        "utility_max_points": 5,
        "rows": utility_rows[:_MAX_ROWS],
        "rows_omitted": max(0, len(utility) - _MAX_ROWS),
        "candidate_count": len(eligible),
        "candidate_ids": [
            _bounded_output_id(rid) for rid in eligible[:_MAX_ROWS]
        ],
        "candidate_ids_omitted": max(0, len(eligible) - _MAX_ROWS),
        "quarantined_count": len(quarantined),
        "quarantined_ids": [
            _bounded_output_id(rid) for rid in quarantined[:_MAX_ROWS]
        ],
        "quarantined_ids_omitted": max(0, len(quarantined) - _MAX_ROWS),
        "promotion_candidate_count": len(promoted_candidates),
        "promotion_candidate_ids": [
            _bounded_output_id(rid) for rid in promoted_candidates[:_MAX_ROWS]
        ],
        "promotion_candidate_ids_omitted": max(
            0, len(promoted_candidates) - _MAX_ROWS
        ),
        "promoted_count": len(promoted),
        "promoted_ids": [
            _bounded_output_id(rid) for rid in promoted[:_MAX_ROWS]
        ],
        "promoted_ids_omitted": max(0, len(promoted) - _MAX_ROWS),
        "demotion_candidate_count": len(demoted_candidates),
        "demotion_candidate_ids": [
            _bounded_output_id(rid) for rid in demoted_candidates[:_MAX_ROWS]
        ],
        "demotion_candidate_ids_omitted": max(
            0, len(demoted_candidates) - _MAX_ROWS
        ),
        "demoted_count": len(demoted),
        "demoted_ids": [
            _bounded_output_id(rid) for rid in demoted[:_MAX_ROWS]
        ],
        "demoted_ids_omitted": max(0, len(demoted) - _MAX_ROWS),
        "metadata_updated_count": len(set(metadata_updated)),
        "metadata_updated_ids": [
            _bounded_output_id(rid)
            for rid in list(dict.fromkeys(metadata_updated))[:_MAX_ROWS]
        ],
        "metadata_updated_ids_omitted": max(
            0, len(set(metadata_updated)) - _MAX_ROWS
        ),
        "protected_count": len(protected_ids),
        "protected_ids": [
            _bounded_output_id(rid) for rid in protected_ids[:_MAX_ROWS]
        ],
        "protected_ids_omitted": max(0, len(protected_ids) - _MAX_ROWS),
        "outcome_signal_count": len(signals),
    }, pretty)
    return 0


def run(argv):
    root = ""
    pretty = False
    write = False
    restore = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--restore":
            i += 1
            restore = argv[i] if i < len(argv) else ""
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("lifecycle: unknown argument: %s" % arg, 2)
        i += 1

    root = os.path.realpath(resolve_root(root))
    project = os.path.join(root, ".kimiflow", "project")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    usage_file = os.path.join(project, "MEMORY-USAGE.json")
    outcome_file = os.path.join(project, "STRATEGY-OUTCOMES.jsonl")
    memory_file = os.path.join(project, "MEMORY.md")
    index_file = os.path.join(project, "MEMORY-INDEX.json")
    database_file = os.path.join(project, "RECALL.sqlite")
    try:
        store.require_local_path(root, learnings)
        store.require_local_path(root, usage_file)
        store.require_local_path(root, outcome_file)
        store.require_local_path(root, memory_file)
        store.require_local_path(root, index_file)
        store.require_local_path(root, database_file)
        if not os.path.exists(project) and not write:
            return _operate(
                root, learnings, usage_file, outcome_file, [], None, None, None,
                restore, write, pretty,
            )
        store.ensure_local_directory(root, project)
        with _deadline_guard():
            with store.local_path_guard(root, project), \
                    store.path_lock(usage_metrics.usage_lock_path(root)), \
                    store.path_lock(outcome_file), \
                    store.path_lock(learnings), store.path_lock(memory_file), \
                    store.path_lock(index_file), store.path_lock(database_file):
                source_snapshot = store.stable_file_snapshot(
                    learnings, missing_ok=True, max_bytes=_MAX_LEARNING_BYTES
                )
                usage_snapshot = store.stable_file_snapshot(
                    usage_file, missing_ok=True, max_bytes=_MAX_USAGE_BYTES
                )
                outcome_snapshot = store.stable_file_snapshot(
                    outcome_file, missing_ok=True, max_bytes=_MAX_OUTCOME_BYTES
                )
                entries = [
                    (raw, ending, _parsed(raw))
                    for raw, ending in _bounded_learning_segments(source_snapshot)
                ]
                return _operate(
                    root, learnings, usage_file, outcome_file, entries, source_snapshot,
                    usage_snapshot, outcome_snapshot, restore, write, pretty,
                )
    except LifecycleDeadlineError as exc:
        return die("lifecycle: %s" % exc, 124)
    except (ValueError, store.ConcurrentWriteError) as exc:
        return die("lifecycle: %s; retry" % exc, 1)
