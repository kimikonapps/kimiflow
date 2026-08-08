"""Local, fail-closed validation and summaries for paired outcome evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


SCHEMA_VERSION = 1
CLAIM_THRESHOLD = 10
MAX_DATASET_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
TASK_KINDS = {"bug", "feature"}
ARM_NAMES = {"plain", "kimiflow"}
MEASUREMENT_SOURCES = {"actual", "estimated", "missing"}
EVIDENCE_KEYS = {"kind", "path", "sha256"}
EXECUTION_KEYS = {
    "model_id",
    "reasoning_effort",
    "toolset_fingerprint",
    "permission_fingerprint",
    "execution_fingerprint",
    "time_budget_seconds",
    "token_budget",
}
ROW_KEYS = {
    "schema_version",
    "comparison_id",
    "task_id",
    "task",
    "repository_id",
    "source_commit",
    "source_snapshot_sha256",
    "arm_order",
    "execution_contract",
    "task_evidence",
    "source_snapshot_evidence",
    "oracle_evidence",
    "blind_review_evidence",
    "arms",
    "post_merge",
}
ARM_KEYS = {
    "name",
    "measurement_source",
    "session_id",
    "source_commit",
    "source_snapshot_sha256",
    "execution",
    "started_at",
    "completed_at",
    "completed",
    "accepted",
    "internal_defects",
    "residual_defects",
    "input_tokens",
    "output_tokens",
    "model_calls",
    "tool_calls",
    "user_interactions",
    "rework_rounds",
    "confounds",
    "added_loc",
    "deleted_loc",
    "session_evidence",
    "outcome_evidence",
    "diff_evidence",
    "merge_status",
    "merged_at",
    "merge_commit",
    "window_ends_at",
    "merge_evidence",
    "post_merge_defects",
    "post_merge_evidence",
}
COUNTER_FIELDS = (
    "input_tokens",
    "output_tokens",
    "model_calls",
    "tool_calls",
    "user_interactions",
    "rework_rounds",
    "added_loc",
    "deleted_loc",
)


class OutcomeComparisonError(ValueError):
    pass


class _DuplicateKey(ValueError):
    pass


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_output(value, pretty=False, level=0):
    """Serialize summary values while retaining exact Decimal JSON numbers."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        items = sorted(value.items())
        if not items:
            return "{}"
        if not pretty:
            return "{" + ",".join(
                json.dumps(key, ensure_ascii=True) + ":" + _json_output(item)
                for key, item in items
            ) + "}"
        padding = " " * (2 * (level + 1))
        closing = " " * (2 * level)
        return "{\n" + ",\n".join(
            padding
            + json.dumps(key, ensure_ascii=True)
            + ": "
            + _json_output(item, pretty=True, level=level + 1)
            for key, item in items
        ) + "\n" + closing + "}"
    if isinstance(value, list):
        if not value:
            return "[]"
        if not pretty:
            return "[" + ",".join(_json_output(item) for item in value) + "]"
        padding = " " * (2 * (level + 1))
        closing = " " * (2 * level)
        return "[\n" + ",\n".join(
            padding + _json_output(item, pretty=True, level=level + 1)
            for item in value
        ) + "\n" + closing + "]"
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _shape(value, keys, label, invalid):
    if not isinstance(value, dict) or set(value) != keys:
        invalid.append("%s_shape_invalid" % label)
        return False
    return True


def _is_counter(value):
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _timestamp(value):
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


def _safe_relative(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 512
        and "\x00" not in value
        and "\\" not in value
        and not value.startswith("/")
        and os.path.normpath(value) == value
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def _read_evidence(root, relative_path):
    root_descriptor = None
    current_descriptor = None
    final_descriptor = None
    try:
        root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        current_descriptor = root_descriptor
        parts = relative_path.split("/")
        nofollow = os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0
        for part in parts[:-1]:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | nofollow,
                dir_fd=current_descriptor,
            )
            if current_descriptor != root_descriptor:
                os.close(current_descriptor)
            current_descriptor = next_descriptor
        final_descriptor = os.open(
            parts[-1], os.O_RDONLY | nofollow, dir_fd=current_descriptor
        )
        opened = os.fstat(final_descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OutcomeComparisonError("evidence_not_regular")
        if opened.st_size > MAX_EVIDENCE_BYTES:
            raise OutcomeComparisonError("evidence_oversize")
        digest = hashlib.sha256()
        payload = bytearray()
        total = 0
        while True:
            chunk = os.read(final_descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_EVIDENCE_BYTES:
                raise OutcomeComparisonError("evidence_oversize")
            digest.update(chunk)
            payload.extend(chunk)
        return "sha256:" + digest.hexdigest(), bytes(payload)
    except OutcomeComparisonError:
        raise
    except OSError as exc:
        raise OutcomeComparisonError("evidence_unsafe_or_missing") from exc
    finally:
        if final_descriptor is not None:
            os.close(final_descriptor)
        if current_descriptor is not None and current_descriptor != root_descriptor:
            os.close(current_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)


def _validate_evidence(reference, expected_kind, label, root, invalid, ineligible):
    if reference is None:
        ineligible.append("%s_missing" % label)
        return None
    if not _shape(reference, EVIDENCE_KEYS, label, invalid):
        return None
    if reference.get("kind") != expected_kind:
        invalid.append("%s_kind_invalid" % label)
    path = reference.get("path")
    digest = reference.get("sha256")
    if not _safe_relative(path):
        invalid.append("%s_path_unsafe" % label)
        return None
    if not isinstance(digest, str) or SHA_RE.fullmatch(digest) is None:
        invalid.append("%s_sha256_invalid" % label)
        return None
    try:
        actual, payload = _read_evidence(root, path)
    except OutcomeComparisonError as exc:
        invalid.append("%s_%s" % (label, str(exc)))
        return None
    if actual != digest:
        invalid.append("%s_fingerprint_mismatch" % label)
        return None
    return digest, payload


def _validate_typed_evidence(
    reference, expected_kind, label, projection, root, invalid, ineligible
):
    result = _validate_evidence(
        reference, expected_kind, label, root, invalid, ineligible
    )
    if result is None:
        return None
    digest, payload = result
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicates
        )
    except (json.JSONDecodeError, UnicodeError, ValueError, _DuplicateKey):
        invalid.append("%s_payload_malformed" % label)
        return result
    if not _shape(value, set(projection), "%s_payload" % label, invalid):
        return result
    canonical_payload = _canonical(value)
    if payload != canonical_payload:
        invalid.append("%s_payload_noncanonical" % label)
    if canonical_payload != _canonical(projection):
        invalid.append("%s_payload_mismatch" % label)
    return digest, payload


def _validate_snapshot_manifest(payload, source_commit, invalid):
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (json.JSONDecodeError, UnicodeError, ValueError, _DuplicateKey):
        invalid.append("source_snapshot_manifest_invalid")
        return
    if not _shape(
        value,
        {"source_commit", "tracked_binary_diff_sha256", "untracked"},
        "source_snapshot_manifest",
        invalid,
    ):
        return
    if value.get("source_commit") != source_commit:
        invalid.append("source_snapshot_manifest_commit_mismatch")
    tracked = value.get("tracked_binary_diff_sha256")
    if not isinstance(tracked, str) or SHA_RE.fullmatch(tracked) is None:
        invalid.append("source_snapshot_manifest_tracked_diff_invalid")
    untracked = value.get("untracked")
    if not isinstance(untracked, list):
        invalid.append("source_snapshot_manifest_untracked_invalid")
        return
    paths = []
    for item in untracked:
        if not _shape(
            item,
            {"path", "mode", "sha256"},
            "source_snapshot_manifest_untracked_item",
            invalid,
        ):
            continue
        path = item.get("path")
        mode = item.get("mode")
        digest = item.get("sha256")
        if not _safe_relative(path):
            invalid.append("source_snapshot_manifest_untracked_path_invalid")
        else:
            paths.append(path)
        if not isinstance(mode, str) or re.fullmatch(r"[0-7]{6}", mode) is None:
            invalid.append("source_snapshot_manifest_untracked_mode_invalid")
        if not isinstance(digest, str) or SHA_RE.fullmatch(digest) is None:
            invalid.append("source_snapshot_manifest_untracked_sha256_invalid")
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        invalid.append("source_snapshot_manifest_untracked_order_invalid")
    canonical = _canonical(value)
    if payload != canonical:
        invalid.append("source_snapshot_manifest_not_canonical")


def _validate_execution(value, label, invalid):
    if not _shape(value, EXECUTION_KEYS, label, invalid):
        return False
    for field in ("model_id", "reasoning_effort"):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            invalid.append("%s_%s_invalid" % (label, field))
    for field in (
        "toolset_fingerprint",
        "permission_fingerprint",
        "execution_fingerprint",
    ):
        item = value.get(field)
        if not isinstance(item, str) or SHA_RE.fullmatch(item) is None:
            invalid.append("%s_%s_invalid" % (label, field))
    for field in ("time_budget_seconds", "token_budget"):
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            invalid.append("%s_%s_invalid" % (label, field))
    return True


def _validate_nullable_counter(value, label, invalid, ineligible):
    if value is None:
        ineligible.append("%s_missing" % label)
    elif not _is_counter(value):
        invalid.append("%s_invalid" % label)


def _validate_defects(value, label, invalid, ineligible):
    if value is None:
        ineligible.append("%s_missing" % label)
        return
    if not _shape(value, {"blocker", "high"}, label, invalid):
        return
    for field in ("blocker", "high"):
        _validate_nullable_counter(
            value.get(field), "%s_%s" % (label, field), invalid, ineligible
        )


def _validate_confounds(value, invalid, ineligible):
    if value is None:
        return
    if not isinstance(value, list):
        invalid.append("confounds_invalid")
        return
    if not value:
        invalid.append("confounds_empty_invalid")
        return
    if any(
        not isinstance(item, str) or not item.strip()
        for item in value
    ):
        invalid.append("confounds_invalid")
        return
    ineligible.append("confounds_present")


def _validate_arm(arm, expected, row, root, invalid, ineligible):
    label = "arm_%s" % expected
    if not _shape(arm, ARM_KEYS, label, invalid):
        return {}
    if arm.get("name") != expected:
        invalid.append("%s_name_invalid" % label)
    source = arm.get("measurement_source")
    if not isinstance(source, str) or source not in MEASUREMENT_SOURCES:
        invalid.append("%s_measurement_source_invalid" % label)
    elif source != "actual":
        ineligible.append("measurement_source_not_actual")
    session_id = arm.get("session_id")
    if session_id is None:
        ineligible.append("session_id_missing")
    elif not isinstance(session_id, str) or ID_RE.fullmatch(session_id) is None:
        invalid.append("%s_session_id_invalid" % label)
    if arm.get("source_commit") != row.get("source_commit"):
        invalid.append("%s_source_commit_mismatch" % label)
    if arm.get("source_snapshot_sha256") != row.get("source_snapshot_sha256"):
        invalid.append("%s_source_snapshot_mismatch" % label)
    if _validate_execution(arm.get("execution"), "%s_execution" % label, invalid):
        if arm.get("execution") != row.get("execution_contract"):
            invalid.append("%s_execution_mismatch" % label)

    times = {}
    for field in ("started_at", "completed_at"):
        value = arm.get(field)
        if value is None:
            ineligible.append("%s_missing" % field)
            times[field] = None
        else:
            times[field] = _timestamp(value)
            if times[field] is None:
                invalid.append("%s_%s_invalid" % (label, field))
    if times.get("started_at") and times.get("completed_at"):
        if times["started_at"] >= times["completed_at"]:
            invalid.append("%s_duration_invalid" % label)

    for field in ("completed", "accepted"):
        value = arm.get(field)
        if value is None:
            ineligible.append("%s_missing" % field)
        elif not isinstance(value, bool):
            invalid.append("%s_%s_invalid" % (label, field))
    _validate_defects(arm.get("internal_defects"), "internal_defects", invalid, ineligible)
    _validate_defects(arm.get("residual_defects"), "residual_defects", invalid, ineligible)
    for field in COUNTER_FIELDS:
        _validate_nullable_counter(arm.get(field), field, invalid, ineligible)
    _validate_confounds(arm.get("confounds"), invalid, ineligible)

    session_projection = {
        field: arm.get(field)
        for field in (
            "session_id",
            "source_commit",
            "source_snapshot_sha256",
            "execution",
            "started_at",
            "completed_at",
            "input_tokens",
            "output_tokens",
            "model_calls",
            "tool_calls",
            "user_interactions",
        )
    }
    session_result = _validate_typed_evidence(
        arm.get("session_evidence"),
        "session",
        "%s_session_evidence" % label,
        session_projection,
        root,
        invalid,
        ineligible,
    )
    outcome_projection = {
        field: arm.get(field)
        for field in (
            "completed",
            "accepted",
            "internal_defects",
            "residual_defects",
            "rework_rounds",
            "confounds",
        )
    }
    _validate_typed_evidence(
        arm.get("outcome_evidence"),
        "outcome",
        "%s_outcome_evidence" % label,
        outcome_projection,
        root,
        invalid,
        ineligible,
    )
    _validate_typed_evidence(
        arm.get("diff_evidence"),
        "diff",
        "%s_diff_evidence" % label,
        {field: arm.get(field) for field in ("added_loc", "deleted_loc")},
        root,
        invalid,
        ineligible,
    )

    merge_status = arm.get("merge_status")
    if not isinstance(merge_status, str) or merge_status not in {
        "merged",
        "not_merged",
    }:
        invalid.append("%s_merge_status_invalid" % label)
    merged_at = arm.get("merged_at")
    merged_time = None
    if merge_status == "merged":
        if merged_at is None:
            ineligible.append("merged_at_missing")
        else:
            merged_time = _timestamp(merged_at)
            if merged_time is None:
                invalid.append("%s_merged_at_invalid" % label)
            elif times.get("completed_at") and merged_time <= times["completed_at"]:
                invalid.append("%s_merge_time_invalid" % label)
        merge_commit = arm.get("merge_commit")
        if merge_commit is None:
            ineligible.append("merge_commit_missing")
        elif not isinstance(merge_commit, str) or COMMIT_RE.fullmatch(merge_commit) is None:
            invalid.append("%s_merge_commit_invalid" % label)
        _validate_typed_evidence(
            arm.get("merge_evidence"),
            "merge",
            "%s_merge_evidence" % label,
            {
                field: arm.get(field)
                for field in ("merge_commit", "merged_at", "window_ends_at")
            },
            root,
            invalid,
            ineligible,
        )
    elif merge_status == "not_merged":
        for field in ("merged_at", "merge_commit", "window_ends_at", "merge_evidence"):
            if arm.get(field) is not None:
                invalid.append("%s_%s_must_be_null" % (label, field))

    return {
        "started_at": times.get("started_at"),
        "completed_at": times.get("completed_at"),
        "merged_at": merged_time,
        "session_evidence_sha256": session_result[0] if session_result else None,
    }


def _validate_lifecycle(row, arms, arm_state, root, invalid, ineligible):
    post_merge = row.get("post_merge")
    if not _shape(post_merge, {"status", "window_days", "observed_at"}, "post_merge", invalid):
        return
    status_value = post_merge.get("status")
    if not isinstance(status_value, str) or status_value not in {
        "pending",
        "observed",
        "not_applicable",
    }:
        invalid.append("post_merge_status_invalid")
        return
    both_merged = all(arms[name].get("merge_status") == "merged" for name in ARM_NAMES)
    if both_merged:
        if status_value not in {"pending", "observed"}:
            invalid.append("post_merge_status_inconsistent")
        window_days = post_merge.get("window_days")
        if isinstance(window_days, bool) or not isinstance(window_days, int) or not 7 <= window_days <= 30:
            invalid.append("post_merge_window_days_invalid")
            window_days = None
        observed_at = _timestamp(post_merge.get("observed_at"))
        if observed_at is None:
            invalid.append("post_merge_observed_at_invalid")
        window_ends = []
        for name in ("plain", "kimiflow"):
            arm = arms[name]
            declared = arm.get("window_ends_at")
            declared_time = _timestamp(declared)
            if declared_time is None:
                if declared is None:
                    ineligible.append("window_ends_at_missing")
                else:
                    invalid.append("arm_%s_window_ends_at_invalid" % name)
                continue
            merged_time = arm_state[name].get("merged_at")
            if merged_time is not None and window_days is not None:
                try:
                    expected_boundary = merged_time + timedelta(days=window_days)
                except OverflowError:
                    invalid.append("arm_%s_window_boundary_overflow" % name)
                else:
                    if declared_time != expected_boundary:
                        invalid.append("arm_%s_window_boundary_invalid" % name)
            window_ends.append(declared_time)
        merge_times = [
            state.get("merged_at") for state in arm_state.values() if state.get("merged_at")
        ]
        if observed_at is not None and merge_times:
            if observed_at < max(merge_times):
                invalid.append("post_merge_observation_before_merge")
        if observed_at is not None and len(window_ends) == 2:
            later_end = max(window_ends)
            if status_value == "pending" and observed_at >= later_end:
                invalid.append("post_merge_pending_boundary_invalid")
            if status_value == "observed" and observed_at < later_end:
                invalid.append("post_merge_observed_boundary_invalid")
        for name in ("plain", "kimiflow"):
            arm = arms[name]
            if status_value == "pending":
                if arm.get("post_merge_defects") is not None:
                    invalid.append("arm_%s_pending_defects_must_be_null" % name)
                if arm.get("post_merge_evidence") is not None:
                    invalid.append("arm_%s_pending_evidence_must_be_null" % name)
            elif status_value == "observed":
                _validate_defects(
                    arm.get("post_merge_defects"),
                    "post_merge_defects",
                    invalid,
                    ineligible,
                )
                _validate_typed_evidence(
                    arm.get("post_merge_evidence"),
                    "post_merge",
                    "arm_%s_post_merge_evidence" % name,
                    {
                        "observed_at": post_merge.get("observed_at"),
                        "window_ends_at": arm.get("window_ends_at"),
                        "post_merge_defects": arm.get("post_merge_defects"),
                    },
                    root,
                    invalid,
                    ineligible,
                )
    else:
        if status_value != "not_applicable":
            invalid.append("post_merge_status_inconsistent")
        if post_merge.get("window_days") is not None or post_merge.get("observed_at") is not None:
            invalid.append("post_merge_not_applicable_fields_must_be_null")
        for name in ("plain", "kimiflow"):
            arm = arms[name]
            for field in ("window_ends_at", "post_merge_defects", "post_merge_evidence"):
                if arm.get(field) is not None:
                    invalid.append("arm_%s_%s_must_be_null" % (name, field))


def _pair_metrics(row):
    arms = {arm["name"]: arm for arm in row["arms"]}
    result = {}
    for name, arm in arms.items():
        started = _timestamp(arm["started_at"])
        completed = _timestamp(arm["completed_at"])
        result[name] = {
            "tokens": arm["input_tokens"] + arm["output_tokens"],
            "time_seconds": int((completed - started).total_seconds()),
            "interactions": arm["user_interactions"],
            "rework": arm["rework_rounds"],
            "loc": arm["added_loc"] + arm["deleted_loc"],
        }
    return result


def _verdict(row):
    arms = {arm["name"]: arm for arm in row["arms"]}
    plain = arms["plain"]
    kimiflow = arms["kimiflow"]

    def decide(kimiflow_value, plain_value, higher_wins):
        if kimiflow_value == plain_value:
            return None
        if higher_wins:
            return "win" if kimiflow_value > plain_value else "loss"
        return "win" if kimiflow_value < plain_value else "loss"

    for field in ("completed", "accepted"):
        decision = decide(kimiflow[field], plain[field], True)
        if decision:
            return decision
    for field in ("blocker", "high"):
        decision = decide(
            kimiflow["residual_defects"][field], plain["residual_defects"][field], False
        )
        if decision:
            return decision
    lifecycle = row["post_merge"]["status"]
    if lifecycle == "pending":
        return "pending"
    if lifecycle == "observed":
        decision = decide(
            sum(kimiflow["post_merge_defects"].values()),
            sum(plain["post_merge_defects"].values()),
            False,
        )
        if decision:
            return decision
    metrics = _pair_metrics(row)
    for metric in ("tokens", "time_seconds", "interactions", "rework", "loc"):
        decision = decide(metrics["kimiflow"][metric], metrics["plain"][metric], False)
        if decision:
            return decision
    return "tie"


def _validate_row(row, line_number, root):
    invalid = []
    ineligible = []
    comparison_id = row.get("comparison_id") if isinstance(row, dict) else None
    if not _shape(row, ROW_KEYS, "row", invalid):
        return {
            "line": line_number,
            "comparison_id": comparison_id,
            "status": "invalid",
            "reasons": sorted(set(invalid)),
            "verdict": None,
            "_row": row if isinstance(row, dict) else None,
        }
    schema_version = row.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SCHEMA_VERSION
    ):
        invalid.append("schema_version_invalid")
    if not isinstance(comparison_id, str) or ID_RE.fullmatch(comparison_id) is None:
        invalid.append("comparison_id_invalid")
    task_id = row.get("task_id")
    if not isinstance(task_id, str) or ID_RE.fullmatch(task_id) is None:
        invalid.append("task_id_invalid")
    task = row.get("task")
    if _shape(task, {"kind", "text_sha256"}, "task", invalid):
        task_kind = task.get("kind")
        if not isinstance(task_kind, str) or task_kind not in TASK_KINDS:
            invalid.append("task_kind_invalid")
        if not isinstance(task.get("text_sha256"), str) or SHA_RE.fullmatch(task["text_sha256"]) is None:
            invalid.append("task_text_sha256_invalid")
    repository_id = row.get("repository_id")
    if not isinstance(repository_id, str) or not repository_id.strip() or repository_id != repository_id.strip() or len(repository_id) > 128:
        invalid.append("repository_id_invalid")
    source_commit = row.get("source_commit")
    if not isinstance(source_commit, str) or COMMIT_RE.fullmatch(source_commit) is None:
        invalid.append("source_commit_invalid")
    snapshot_digest = row.get("source_snapshot_sha256")
    if not isinstance(snapshot_digest, str) or SHA_RE.fullmatch(snapshot_digest) is None:
        invalid.append("source_snapshot_sha256_invalid")
    arm_order = row.get("arm_order")
    if arm_order not in (["plain", "kimiflow"], ["kimiflow", "plain"]):
        invalid.append("arm_order_invalid")
    _validate_execution(row.get("execution_contract"), "execution_contract", invalid)

    task_result = _validate_evidence(
        row.get("task_evidence"), "task", "task_evidence", root, invalid, ineligible
    )
    if task_result is not None and isinstance(task, dict) and task_result[0] != task.get("text_sha256"):
        invalid.append("task_evidence_digest_mismatch")
    source_result = _validate_evidence(
        row.get("source_snapshot_evidence"),
        "source_snapshot",
        "source_snapshot_evidence",
        root,
        invalid,
        ineligible,
    )
    if source_result is not None:
        if source_result[0] != snapshot_digest:
            invalid.append("source_snapshot_evidence_digest_mismatch")
        _validate_snapshot_manifest(source_result[1], source_commit, invalid)
    _validate_evidence(
        row.get("oracle_evidence"), "oracle", "oracle_evidence", root, invalid, ineligible
    )
    _validate_evidence(
        row.get("blind_review_evidence"),
        "blind_review",
        "blind_review_evidence",
        root,
        invalid,
        ineligible,
    )

    arms_value = row.get("arms")
    arms = {}
    arm_state = {}
    if not isinstance(arms_value, list) or len(arms_value) != 2:
        invalid.append("arms_invalid")
    else:
        names = [item.get("name") if isinstance(item, dict) else None for item in arms_value]
        if (
            any(not isinstance(name, str) for name in names)
            or set(names) != ARM_NAMES
            or len(set(names)) != 2
        ):
            invalid.append("arms_invalid")
        else:
            arms = {item["name"]: item for item in arms_value}
            for name in ("plain", "kimiflow"):
                arm_state[name] = _validate_arm(
                    arms[name], name, row, root, invalid, ineligible
                )
            plain_session = arms["plain"].get("session_id")
            kimiflow_session = arms["kimiflow"].get("session_id")
            if plain_session is not None and plain_session == kimiflow_session:
                invalid.append("duplicate_session_id")
            plain_evidence = arm_state["plain"].get("session_evidence_sha256")
            kimiflow_evidence = arm_state["kimiflow"].get("session_evidence_sha256")
            if plain_evidence is not None and plain_evidence == kimiflow_evidence:
                invalid.append("duplicate_session_evidence_sha256")
            if arm_order in (["plain", "kimiflow"], ["kimiflow", "plain"]):
                first = arm_state[arm_order[0]].get("started_at")
                second = arm_state[arm_order[1]].get("started_at")
                if first is not None and second is not None and first > second:
                    invalid.append("arm_start_order_invalid")
            _validate_lifecycle(row, arms, arm_state, root, invalid, ineligible)

    if invalid:
        status = "invalid"
        reasons = sorted(set(invalid))
        verdict = None
    elif ineligible:
        status = "field_note"
        reasons = sorted(set(ineligible))
        verdict = None
    else:
        status = "primary_eligible"
        reasons = []
        verdict = _verdict(row)
    return {
        "line": line_number,
        "comparison_id": comparison_id,
        "status": status,
        "reasons": reasons,
        "verdict": verdict,
        "_row": row,
    }


def _mark_dataset_duplicates(rows):
    identity_maps = {
        "duplicate_comparison_id": {},
        "duplicate_session_id": {},
        "duplicate_session_evidence_sha256": {},
    }
    for index, result in enumerate(rows):
        row = result.get("_row")
        if not isinstance(row, dict):
            continue
        comparison_id = row.get("comparison_id")
        if isinstance(comparison_id, str):
            identity_maps["duplicate_comparison_id"].setdefault(comparison_id, []).append(index)
        arms = row.get("arms")
        if not isinstance(arms, list):
            continue
        for arm in arms:
            if not isinstance(arm, dict):
                continue
            session_id = arm.get("session_id")
            if isinstance(session_id, str):
                identity_maps["duplicate_session_id"].setdefault(session_id, []).append(index)
            evidence = arm.get("session_evidence")
            if isinstance(evidence, dict):
                digest = evidence.get("sha256")
                if isinstance(digest, str):
                    identity_maps["duplicate_session_evidence_sha256"].setdefault(digest, []).append(index)
    for reason, values in identity_maps.items():
        for indices in values.values():
            if len(indices) <= 1:
                continue
            for index in set(indices):
                result = rows[index]
                result["status"] = "invalid"
                result["verdict"] = None
                result["reasons"] = sorted(set(result["reasons"] + [reason]))


def _read_dataset(path):
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise OutcomeComparisonError("dataset_unsafe")
        if before.st_size > MAX_DATASET_BYTES:
            raise OutcomeComparisonError("dataset_oversize")
        return Path(path).read_text(encoding="utf-8")
    except OutcomeComparisonError:
        raise
    except (OSError, UnicodeError) as exc:
        raise OutcomeComparisonError("dataset_unreadable") from exc


def evaluate_dataset(dataset_path, repository_root):
    root = os.fspath(Path(repository_root).resolve())
    text = _read_dataset(os.fspath(dataset_path))
    rows = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line, object_pairs_hook=_reject_duplicates)
            if not isinstance(value, dict):
                raise ValueError("row_not_object")
        except (json.JSONDecodeError, UnicodeError, ValueError, _DuplicateKey):
            rows.append(
                {
                    "line": line_number,
                    "comparison_id": None,
                    "status": "invalid",
                    "reasons": ["json_invalid_or_duplicate_key"],
                    "verdict": None,
                    "_row": None,
                }
            )
            continue
        rows.append(_validate_row(value, line_number, root))
    _mark_dataset_duplicates(rows)
    valid = sum(row["status"] == "primary_eligible" for row in rows)
    field_notes = sum(row["status"] == "field_note" for row in rows)
    invalid = sum(row["status"] == "invalid" for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_pairs": len(rows),
        "valid_pairs": valid,
        "field_note_pairs": field_notes,
        "excluded_pairs": field_notes + invalid,
        "invalid_pairs": invalid,
        "claim_status": "evidence_available" if valid >= CLAIM_THRESHOLD else "insufficient_evidence",
        "rows": rows,
    }


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    total = ordered[middle - 1] + ordered[middle]
    quotient, remainder = divmod(total, 2)
    if remainder == 0:
        return quotient
    whole = abs(total) // 2
    sign = "-" if total < 0 else ""
    return Decimal("%s%s.5" % (sign, whole))


def _count_boolean(rows, field):
    result = {}
    for name in ("plain", "kimiflow"):
        passed = sum(
            next(arm for arm in row["arms"] if arm["name"] == name)[field] for row in rows
        )
        total = len(rows)
        result[name] = {
            "passed": passed,
            "total": total,
            "rate": (passed / total) if total else None,
        }
    return result


def _defect_totals(rows, field):
    result = {}
    for name in ("plain", "kimiflow"):
        matching = [next(arm for arm in row["arms"] if arm["name"] == name) for row in rows]
        result[name] = {
            severity: sum(arm[field][severity] for arm in matching)
            for severity in ("blocker", "high")
        }
    return result


def summarize(evaluation):
    rows = [
        result["_row"]
        for result in evaluation["rows"]
        if result["status"] == "primary_eligible"
    ]
    results = [result for result in evaluation["rows"] if result["status"] == "primary_eligible"]
    differences = {name: [] for name in ("tokens", "time_seconds", "interactions", "rework", "loc")}
    for row in rows:
        metrics = _pair_metrics(row)
        for name in differences:
            differences[name].append(metrics["kimiflow"][name] - metrics["plain"][name])
    verdicts = Counter(result["verdict"] for result in results)
    observed_rows = [row for row in rows if row["post_merge"]["status"] == "observed"]
    post_merge_totals = {
        name: {
            severity: sum(
                next(arm for arm in row["arms"] if arm["name"] == name)["post_merge_defects"][severity]
                for row in observed_rows
            )
            for severity in ("blocker", "high")
        }
        for name in ("plain", "kimiflow")
    }
    remaining_differences = {
        severity: [
            next(arm for arm in row["arms"] if arm["name"] == "kimiflow")[
                "residual_defects"
            ][severity]
            - next(arm for arm in row["arms"] if arm["name"] == "plain")[
                "residual_defects"
            ][severity]
            for row in rows
        ]
        for severity in ("blocker", "high")
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "recorded_pairs": evaluation["recorded_pairs"],
        "valid_pairs": evaluation["valid_pairs"],
        "field_note_pairs": evaluation["field_note_pairs"],
        "excluded_pairs": evaluation["excluded_pairs"],
        "invalid_pairs": evaluation["invalid_pairs"],
        "claim_status": evaluation["claim_status"],
        "per_task": [
            {
                "comparison_id": row["comparison_id"],
                "task_id": row["task_id"],
                "task_kind": row["task"]["kind"],
                "verdict": result["verdict"],
            }
            for row, result in zip(rows, results)
        ],
        "task_kind_distribution": dict(sorted(Counter(row["task"]["kind"] for row in rows).items())),
        "repository_id_distribution": dict(sorted(Counter(row["repository_id"] for row in rows).items())),
        "completion": _count_boolean(rows, "completed"),
        "acceptance": _count_boolean(rows, "accepted"),
        "internal_defects": _defect_totals(rows, "internal_defects"),
        "remaining_defects": _defect_totals(rows, "residual_defects"),
        "remaining_defect_differences": {
            severity: {"sample_count": len(values), "median": _median(values)}
            for severity, values in remaining_differences.items()
        },
        "post_merge_defects": {
            "sample_count": len(observed_rows),
            "plain": post_merge_totals["plain"],
            "kimiflow": post_merge_totals["kimiflow"],
        },
        "pairwise_differences": {
            name: {"sample_count": len(values), "median": _median(values)}
            for name, values in differences.items()
        },
        "verdicts": {
            "win": verdicts.get("win", 0),
            "tie": verdicts.get("tie", 0),
            "loss": verdicts.get("loss", 0),
            "pending": verdicts.get("pending", 0),
        },
        "post_merge": {
            "pending_windows": sum(row["post_merge"]["status"] == "pending" for row in rows),
            "observed_pairs": len(observed_rows),
            "not_applicable_pairs": sum(
                row["post_merge"]["status"] == "not_applicable" for row in rows
            ),
        },
    }


def _public_evaluation(evaluation):
    result = dict(evaluation)
    result["rows"] = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in evaluation["rows"]
    ]
    return result


def _parser():
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(prog="outcome-comparisons.sh")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "summary"):
        child = subparsers.add_parser(command)
        child.add_argument(
            "--data",
            default=os.fspath(repository / "evals" / "outcome-comparisons.jsonl"),
        )
        child.add_argument("--repo-root", default=os.fspath(repository))
        child.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        evaluation = evaluate_dataset(args.data, args.repo_root)
    except OutcomeComparisonError as exc:
        print(_json_output({"schema_version": SCHEMA_VERSION, "error": str(exc)}))
        return 2
    value = _public_evaluation(evaluation) if args.command == "validate" else summarize(evaluation)
    print(_json_output(value, pretty=args.pretty))
    if args.command == "validate" and evaluation["invalid_pairs"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
