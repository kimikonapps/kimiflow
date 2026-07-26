"""Deterministic, privacy-bounded Kimiflow behavior evaluation envelopes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys

from .atomic import atomic_write


SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 128 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_DIFF_BYTES = 32 * 1024 * 1024
MAX_UNTRACKED_BYTES = 16 * 1024 * 1024
MAX_CASES = 16
MAX_MARKERS = 12
MAX_MODEL_GROUPS = 8
MAX_SCENARIOS = 12
CASE_TIMEOUT_SECONDS = 300

ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
SCRIPT_RE = re.compile(r"^hooks/test-[a-z0-9][a-z0-9-]*\.sh$")
MARKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.:/+()=-]{0,127}$")
SCENARIO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
SKIP_PATTERNS = (
    re.compile(r"(?m)^SKIP:"),
    re.compile(r"OK \([^\r\n)]*\bskipped=[1-9][0-9]*\b"),
    re.compile(r"(?im)^\s*ok\s+[0-9]+(?:\s+-[^\r\n]*?)?\s+#\s*SKIP(?:\s|$)"),
)
TRACE_KINDS = {"run", "phase", "model", "tool", "gate", "handoff", "recovery"}
TRACE_STATUSES = {"planned", "passed", "failed", "skipped"}
PRIVACY_CONTRACT = {
    "local_only": True,
    "stores_output": False,
    "stores_prompts": False,
    "stores_answers": False,
    "stores_code": False,
    "stores_absolute_paths": False,
    "stores_secrets": False,
}
EVALUATION_KEYS = {
    "schema_version",
    "artifact_type",
    "suite",
    "manifest_sha256",
    "source_commit",
    "source_snapshot_sha256",
    "summary",
    "cases",
    "trace",
    "privacy",
    "seal",
}
CASE_RESULT_KEYS = {
    "id",
    "phase",
    "passed",
    "exit_code",
    "marker_count",
    "marker_pass_count",
    "output_bytes",
    "output_sha256",
    "skip_detected",
}
TRACE_KEYS = {"trace_id", "spans"}
SPAN_KEYS = {
    "trace_id",
    "span_id",
    "parent_span_id",
    "kind",
    "name",
    "status",
    "phase",
}
COMPARISON_KEYS = {
    "schema_version",
    "artifact_type",
    "suite",
    "manifest_sha256",
    "baseline_seal",
    "candidate_seal",
    "case_count",
    "regression_count",
    "regressions",
    "verdict",
    "privacy",
    "seal",
}
MODEL_PLAN_KEYS = {
    "schema_version",
    "artifact_type",
    "suite",
    "manifest_sha256",
    "policy",
    "executed",
    "model_calls",
    "network_calls",
    "groups",
    "trace",
    "privacy",
    "seal",
}


class EvidenceEvalError(ValueError):
    pass


def _reject_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sealed(value):
    result = dict(value)
    result.pop("seal", None)
    result["seal"] = _sha(_canonical(result))
    return result


def _read_json(path, maximum, label):
    descriptor = None
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise EvidenceEvalError("%s_unsafe" % label)
        if before.st_size > maximum:
            raise EvidenceEvalError("%s_oversize" % label)
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise EvidenceEvalError("%s_exchanged" % label)
        payload = b""
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        if len(payload) > maximum:
            raise EvidenceEvalError("%s_oversize" % label)
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except EvidenceEvalError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise EvidenceEvalError("%s_malformed" % label) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise EvidenceEvalError("%s_malformed" % label)
    return value, payload


def _safe_relative(value):
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and "\x00" not in value
        and "\\" not in value
        and not value.startswith("/")
        and os.path.normpath(value) == value
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def _valid_scenarios(value):
    return (
        isinstance(value, list)
        and 1 <= len(value) <= MAX_SCENARIOS
        and all(
            isinstance(item, str)
            and SCENARIO_RE.fullmatch(item) is not None
            and ".." not in item.split("/")
            for item in value
        )
        and len(set(value)) == len(value)
    )


def load_manifest(path):
    value, payload = _read_json(path, MAX_MANIFEST_BYTES, "manifest")
    if set(value) != {"schema_version", "suite", "cases", "model_release"}:
        raise EvidenceEvalError("manifest_shape_invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise EvidenceEvalError("manifest_version_invalid")
    if not isinstance(value.get("suite"), str) or ID_RE.fullmatch(value["suite"]) is None:
        raise EvidenceEvalError("manifest_suite_invalid")
    cases = value.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise EvidenceEvalError("manifest_cases_invalid")
    seen = set()
    normalized_cases = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
            "id", "phase", "command", "required_markers", "trace_recovery"
        }:
            raise EvidenceEvalError("manifest_case_shape_invalid")
        case_id = case.get("id")
        phase = case.get("phase")
        command = case.get("command")
        markers = case.get("required_markers")
        if (
            not isinstance(case_id, str)
            or ID_RE.fullmatch(case_id) is None
            or case_id in seen
            or isinstance(phase, bool)
            or not isinstance(phase, int)
            or not 0 <= phase <= 7
            or not isinstance(command, list)
            or len(command) != 2
            or command[0] != "bash"
            or not isinstance(command[1], str)
            or SCRIPT_RE.fullmatch(command[1]) is None
            or not isinstance(markers, list)
            or len(markers) > MAX_MARKERS
            or any(
                not isinstance(marker, str)
                or MARKER_RE.fullmatch(marker) is None
                for marker in markers
            )
            or not isinstance(case.get("trace_recovery"), bool)
        ):
            raise EvidenceEvalError("manifest_case_invalid")
        seen.add(case_id)
        normalized_cases.append(case)
    release = value.get("model_release")
    if not isinstance(release, dict) or set(release) != {"policy", "groups"}:
        raise EvidenceEvalError("manifest_model_release_invalid")
    if release.get("policy") != "release_only":
        raise EvidenceEvalError("manifest_model_policy_invalid")
    groups = release.get("groups")
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_MODEL_GROUPS:
        raise EvidenceEvalError("manifest_model_groups_invalid")
    group_ids = set()
    normalized_groups = []
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"id", "scenarios"}:
            raise EvidenceEvalError("manifest_model_group_shape_invalid")
        group_id = group.get("id")
        scenarios = group.get("scenarios")
        if (
            not isinstance(group_id, str)
            or ID_RE.fullmatch(group_id) is None
            or group_id in group_ids
            or not _valid_scenarios(scenarios)
        ):
            raise EvidenceEvalError("manifest_model_group_invalid")
        group_ids.add(group_id)
        normalized_groups.append(group)
    return {
        "schema_version": SCHEMA_VERSION,
        "suite": value["suite"],
        "cases": normalized_cases,
        "model_release": {
            "policy": "release_only",
            "groups": normalized_groups,
        },
        "digest": _sha(_canonical(value)),
        "source_bytes": len(payload),
    }


def _git(repo, *args):
    try:
        return subprocess.run(
            ["git", "-C", repo] + list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise EvidenceEvalError("git_unavailable") from exc


def repo_root(path):
    proc = _git(os.path.abspath(path), "rev-parse", "--show-toplevel")
    if proc.returncode:
        raise EvidenceEvalError("repo_root_unavailable")
    try:
        return os.path.realpath(proc.stdout.decode("utf-8", "strict").strip())
    except UnicodeError as exc:
        raise EvidenceEvalError("repo_root_invalid") from exc


def _source_snapshot(repo, manifest_digest):
    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode:
        raise EvidenceEvalError("source_commit_unavailable")
    commit = head.stdout.decode("ascii", "strict").strip()
    if COMMIT_RE.fullmatch(commit) is None:
        raise EvidenceEvalError("source_commit_invalid")
    diff = _git(repo, "diff", "--binary", "HEAD", "--")
    if diff.returncode or len(diff.stdout) > MAX_DIFF_BYTES:
        raise EvidenceEvalError("source_diff_unavailable")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked.returncode or len(untracked.stdout) > MAX_MANIFEST_BYTES:
        raise EvidenceEvalError("source_untracked_inventory_invalid")
    digest = hashlib.sha256()
    digest.update(commit.encode("ascii"))
    digest.update(manifest_digest.encode("ascii"))
    digest.update(diff.stdout)
    total = 0
    paths = [item for item in untracked.stdout.split(b"\0") if item]
    if len(paths) > 4096:
        raise EvidenceEvalError("source_untracked_inventory_oversize")
    for encoded in sorted(paths):
        try:
            relative = encoded.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise EvidenceEvalError("source_untracked_path_invalid") from exc
        if not _safe_relative(relative):
            raise EvidenceEvalError("source_untracked_path_invalid")
        path = os.path.join(repo, *relative.split("/"))
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise EvidenceEvalError("source_untracked_file_unsafe")
        if total + info.st_size > MAX_UNTRACKED_BYTES:
            raise EvidenceEvalError("source_untracked_content_oversize")
        with open(path, "rb") as handle:
            payload = handle.read(MAX_UNTRACKED_BYTES + 1)
        total += len(payload)
        digest.update(encoded)
        digest.update(hashlib.sha256(payload).digest())
    return commit, "sha256:" + digest.hexdigest()


def _safe_environment():
    environment = {}
    for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT"):
        if os.environ.get(key):
            environment[key] = os.environ[key]
    environment["CI"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _skip_detected(text):
    return any(pattern.search(text) for pattern in SKIP_PATTERNS)


def _span(trace_id, kind, name, status, phase=None, parent=None):
    return {
        "trace_id": trace_id,
        "span_id": secrets.token_hex(8),
        "parent_span_id": parent,
        "kind": kind,
        "name": name,
        "status": status,
        "phase": phase,
    }


def _new_trace():
    trace_id = secrets.token_hex(16)
    root = _span(trace_id, "run", "evaluation-suite", "planned")
    return trace_id, [root], root["span_id"]


def _run_case(repo, case):
    script = os.path.join(repo, *case["command"][1].split("/"))
    if not os.path.isfile(script) or os.path.islink(script):
        return {
            "id": case["id"],
            "phase": case["phase"],
            "passed": False,
            "exit_code": 127,
            "marker_count": len(case["required_markers"]),
            "marker_pass_count": 0,
            "output_bytes": 0,
            "output_sha256": _sha(b""),
            "skip_detected": False,
        }
    try:
        completed = subprocess.run(
            case["command"],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=CASE_TIMEOUT_SECONDS,
            env=_safe_environment(),
            check=False,
        )
        output = completed.stdout or b""
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        exit_code = 124
    except OSError:
        output = b""
        exit_code = 126
    if exit_code < 0:
        exit_code = min(255, 128 + abs(exit_code))
    oversized = len(output) > MAX_OUTPUT_BYTES
    bounded = output[:MAX_OUTPUT_BYTES]
    if oversized and exit_code == 0:
        exit_code = 125
    try:
        text = bounded.decode("utf-8", "replace")
    except UnicodeError:
        text = ""
    marker_pass_count = sum(marker in text for marker in case["required_markers"])
    skipped = _skip_detected(text)
    passed = (
        exit_code == 0
        and not oversized
        and not skipped
        and marker_pass_count == len(case["required_markers"])
    )
    return {
        "id": case["id"],
        "phase": case["phase"],
        "passed": passed,
        "exit_code": exit_code,
        "marker_count": len(case["required_markers"]),
        "marker_pass_count": marker_pass_count,
        "output_bytes": min(len(output), MAX_OUTPUT_BYTES),
        "output_sha256": _sha(output),
        "skip_detected": skipped,
    }


def run_evaluation(repo, manifest, artifact_type="candidate"):
    if artifact_type not in ("baseline", "candidate"):
        raise EvidenceEvalError("artifact_type_invalid")
    root = repo_root(repo)
    source_commit, source_snapshot = _source_snapshot(root, manifest["digest"])
    trace_id, spans, root_span = _new_trace()
    results = []
    for case in manifest["cases"]:
        phase_span = _span(
            trace_id,
            "phase",
            "phase-%s" % case["phase"],
            "planned",
            phase=case["phase"],
            parent=root_span,
        )
        spans.append(phase_span)
        tool_span = _span(
            trace_id,
            "tool",
            "test-command",
            "planned",
            phase=case["phase"],
            parent=phase_span["span_id"],
        )
        spans.append(tool_span)
        result = _run_case(root, case)
        results.append(result)
        gate_status = "passed" if result["passed"] else "failed"
        gate_span = _span(
            trace_id,
            "gate",
            case["id"],
            gate_status,
            phase=case["phase"],
            parent=tool_span["span_id"],
        )
        spans.append(gate_span)
        if case["trace_recovery"]:
            spans.append(
                _span(
                    trace_id,
                    "recovery",
                    "recovery-check",
                    gate_status,
                    phase=case["phase"],
                    parent=gate_span["span_id"],
                )
            )
    final_commit, final_snapshot = _source_snapshot(root, manifest["digest"])
    if final_commit != source_commit or final_snapshot != source_snapshot:
        raise EvidenceEvalError("source_changed_during_evaluation")
    failure_count = sum(not item["passed"] for item in results)
    spans.append(
        _span(
            trace_id,
            "handoff",
            "candidate-result",
            "passed" if failure_count == 0 else "failed",
            parent=root_span,
        )
    )
    value = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": artifact_type,
        "suite": manifest["suite"],
        "manifest_sha256": manifest["digest"],
        "source_commit": source_commit,
        "source_snapshot_sha256": source_snapshot,
        "summary": {
            "case_count": len(results),
            "pass_count": len(results) - failure_count,
            "failure_count": failure_count,
            "verdict": "PASS" if failure_count == 0 else "BLOCK",
        },
        "cases": results,
        "trace": {"trace_id": trace_id, "spans": spans},
        "privacy": dict(PRIVACY_CONTRACT),
    }
    return _sealed(value)


def _validate_trace(value):
    if not isinstance(value, dict) or set(value) != TRACE_KEYS:
        raise EvidenceEvalError("trace_shape_invalid")
    if not isinstance(value.get("trace_id"), str) or TRACE_ID_RE.fullmatch(value["trace_id"]) is None:
        raise EvidenceEvalError("trace_id_invalid")
    spans = value.get("spans")
    if not isinstance(spans, list) or not 2 <= len(spans) <= 128:
        raise EvidenceEvalError("trace_spans_invalid")
    seen = set()
    roots = 0
    for index, span in enumerate(spans):
        if not isinstance(span, dict) or set(span) != SPAN_KEYS:
            raise EvidenceEvalError("trace_span_shape_invalid")
        span_id = span.get("span_id")
        parent = span.get("parent_span_id")
        phase = span.get("phase")
        if (
            not isinstance(span_id, str)
            or SPAN_ID_RE.fullmatch(span_id) is None
            or span_id in seen
            or span.get("trace_id") != value["trace_id"]
            or (parent is not None and (not isinstance(parent, str) or parent not in seen))
            or span.get("kind") not in TRACE_KINDS
            or not isinstance(span.get("name"), str)
            or ID_RE.fullmatch(span["name"]) is None
            or span.get("status") not in TRACE_STATUSES
            or (
                phase is not None
                and (
                    isinstance(phase, bool)
                    or not isinstance(phase, int)
                    or not 0 <= phase <= 7
                )
            )
        ):
            raise EvidenceEvalError("trace_span_invalid")
        if parent is None:
            roots += 1
            if index != 0 or span["kind"] != "run":
                raise EvidenceEvalError("trace_root_invalid")
        seen.add(span_id)
    if roots != 1:
        raise EvidenceEvalError("trace_root_invalid")


def _validate_evaluation(value):
    if not isinstance(value, dict) or set(value) != EVALUATION_KEYS:
        raise EvidenceEvalError("artifact_shape_invalid")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") not in ("baseline", "candidate")
        or not isinstance(value.get("suite"), str)
        or ID_RE.fullmatch(value["suite"]) is None
        or not isinstance(value.get("manifest_sha256"), str)
        or SHA_RE.fullmatch(value["manifest_sha256"]) is None
        or not isinstance(value.get("source_commit"), str)
        or COMMIT_RE.fullmatch(value["source_commit"]) is None
        or not isinstance(value.get("source_snapshot_sha256"), str)
        or SHA_RE.fullmatch(value["source_snapshot_sha256"]) is None
        or value.get("privacy") != PRIVACY_CONTRACT
    ):
        raise EvidenceEvalError("artifact_contract_invalid")
    cases = value.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise EvidenceEvalError("artifact_cases_invalid")
    seen = set()
    pass_count = 0
    for case in cases:
        if not isinstance(case, dict) or set(case) != CASE_RESULT_KEYS:
            raise EvidenceEvalError("artifact_case_shape_invalid")
        case_id = case.get("id")
        if (
            not isinstance(case_id, str)
            or ID_RE.fullmatch(case_id) is None
            or case_id in seen
            or isinstance(case.get("phase"), bool)
            or not isinstance(case.get("phase"), int)
            or not 0 <= case["phase"] <= 7
            or not isinstance(case.get("passed"), bool)
            or isinstance(case.get("exit_code"), bool)
            or not isinstance(case.get("exit_code"), int)
            or not 0 <= case["exit_code"] <= 255
            or any(
                isinstance(case.get(key), bool)
                or not isinstance(case.get(key), int)
                or not 0 <= case[key] <= MAX_MARKERS
                for key in ("marker_count", "marker_pass_count")
            )
            or isinstance(case.get("output_bytes"), bool)
            or not isinstance(case.get("output_bytes"), int)
            or not 0 <= case["output_bytes"] <= MAX_OUTPUT_BYTES
            or case["marker_pass_count"] > case["marker_count"]
            or not isinstance(case.get("output_sha256"), str)
            or SHA_RE.fullmatch(case["output_sha256"]) is None
            or not isinstance(case.get("skip_detected"), bool)
        ):
            raise EvidenceEvalError("artifact_case_invalid")
        derived_passed = (
            case["exit_code"] == 0
            and not case["skip_detected"]
            and case["marker_pass_count"] == case["marker_count"]
        )
        if case["passed"] is not derived_passed:
            raise EvidenceEvalError("artifact_case_verdict_invalid")
        seen.add(case_id)
        pass_count += int(case["passed"])
    summary = value.get("summary")
    failure_count = len(cases) - pass_count
    if summary != {
        "case_count": len(cases),
        "pass_count": pass_count,
        "failure_count": failure_count,
        "verdict": "PASS" if failure_count == 0 else "BLOCK",
    }:
        raise EvidenceEvalError("artifact_summary_invalid")
    _validate_trace(value.get("trace"))
    expected = _sealed({key: item for key, item in value.items() if key != "seal"})
    if value.get("seal") != expected["seal"]:
        raise EvidenceEvalError("artifact_seal_invalid")
    return value


def load_artifact(path):
    value, _ = _read_json(path, MAX_ARTIFACT_BYTES, "artifact")
    artifact_type = value.get("artifact_type")
    if artifact_type in ("baseline", "candidate"):
        return _validate_evaluation(value)
    if artifact_type == "comparison":
        return _validate_comparison(value)
    if artifact_type == "model-plan":
        return _validate_model_plan(value)
    raise EvidenceEvalError("artifact_type_invalid")


def compare_artifacts(baseline, candidate):
    baseline = _validate_evaluation(baseline)
    candidate = _validate_evaluation(candidate)
    if baseline["artifact_type"] != "baseline" or candidate["artifact_type"] != "candidate":
        raise EvidenceEvalError("comparison_artifact_roles_invalid")
    if (
        baseline["suite"] != candidate["suite"]
        or baseline["manifest_sha256"] != candidate["manifest_sha256"]
    ):
        raise EvidenceEvalError("comparison_suite_mismatch")
    baseline_cases = {item["id"]: item for item in baseline["cases"]}
    candidate_cases = {item["id"]: item for item in candidate["cases"]}
    if set(baseline_cases) != set(candidate_cases):
        raise EvidenceEvalError("comparison_case_set_mismatch")
    regressions = sorted(
        case_id
        for case_id, prior in baseline_cases.items()
        if prior["passed"] and not candidate_cases[case_id]["passed"]
    )
    for case_id, current in candidate_cases.items():
        if not current["passed"] and case_id not in regressions:
            regressions.append(case_id)
    regressions = sorted(set(regressions))
    value = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "comparison",
        "suite": baseline["suite"],
        "manifest_sha256": baseline["manifest_sha256"],
        "baseline_seal": baseline["seal"],
        "candidate_seal": candidate["seal"],
        "case_count": len(baseline_cases),
        "regression_count": len(regressions),
        "regressions": regressions,
        "verdict": "PASS" if not regressions else "BLOCK",
        "privacy": dict(PRIVACY_CONTRACT),
    }
    return _sealed(value)


def _validate_comparison(value):
    if not isinstance(value, dict) or set(value) != COMPARISON_KEYS:
        raise EvidenceEvalError("comparison_shape_invalid")
    regressions = value.get("regressions")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != "comparison"
        or not isinstance(value.get("suite"), str)
        or ID_RE.fullmatch(value["suite"]) is None
        or any(
            not isinstance(value.get(key), str) or SHA_RE.fullmatch(value[key]) is None
            for key in ("manifest_sha256", "baseline_seal", "candidate_seal")
        )
        or isinstance(value.get("case_count"), bool)
        or not isinstance(value.get("case_count"), int)
        or not 1 <= value["case_count"] <= MAX_CASES
        or not isinstance(regressions, list)
        or regressions != sorted(set(regressions))
        or any(not isinstance(item, str) or ID_RE.fullmatch(item) is None for item in regressions)
        or len(regressions) > value["case_count"]
        or isinstance(value.get("regression_count"), bool)
        or not isinstance(value.get("regression_count"), int)
        or value.get("regression_count") != len(regressions)
        or value.get("verdict") != ("PASS" if not regressions else "BLOCK")
        or value.get("privacy") != PRIVACY_CONTRACT
    ):
        raise EvidenceEvalError("comparison_contract_invalid")
    expected = _sealed({key: item for key, item in value.items() if key != "seal"})
    if value.get("seal") != expected["seal"]:
        raise EvidenceEvalError("comparison_seal_invalid")
    return value


def build_model_plan(manifest):
    trace_id, spans, root_span = _new_trace()
    for group in manifest["model_release"]["groups"]:
        spans.append(
            _span(
                trace_id,
                "model",
                group["id"],
                "planned",
                parent=root_span,
            )
        )
    spans.append(
        _span(
            trace_id,
            "handoff",
            "release-runner",
            "planned",
            parent=root_span,
        )
    )
    value = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "model-plan",
        "suite": manifest["suite"],
        "manifest_sha256": manifest["digest"],
        "policy": "release_only",
        "executed": False,
        "model_calls": 0,
        "network_calls": 0,
        "groups": manifest["model_release"]["groups"],
        "trace": {"trace_id": trace_id, "spans": spans},
        "privacy": dict(PRIVACY_CONTRACT),
    }
    return _sealed(value)


def _validate_model_plan(value):
    if not isinstance(value, dict) or set(value) != MODEL_PLAN_KEYS:
        raise EvidenceEvalError("model_plan_shape_invalid")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != "model-plan"
        or not isinstance(value.get("suite"), str)
        or ID_RE.fullmatch(value["suite"]) is None
        or not isinstance(value.get("manifest_sha256"), str)
        or SHA_RE.fullmatch(value["manifest_sha256"]) is None
        or value.get("policy") != "release_only"
        or value.get("executed") is not False
        or isinstance(value.get("model_calls"), bool)
        or value.get("model_calls") != 0
        or isinstance(value.get("network_calls"), bool)
        or value.get("network_calls") != 0
        or value.get("privacy") != PRIVACY_CONTRACT
    ):
        raise EvidenceEvalError("model_plan_contract_invalid")
    groups = value.get("groups")
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_MODEL_GROUPS:
        raise EvidenceEvalError("model_plan_groups_invalid")
    seen = set()
    for group in groups:
        group_id = group.get("id") if isinstance(group, dict) else None
        scenarios = group.get("scenarios") if isinstance(group, dict) else None
        if (
            not isinstance(group, dict)
            or set(group) != {"id", "scenarios"}
            or not isinstance(group_id, str)
            or ID_RE.fullmatch(group_id) is None
            or group_id in seen
            or not _valid_scenarios(scenarios)
        ):
            raise EvidenceEvalError("model_plan_group_invalid")
        seen.add(group_id)
    _validate_trace(value.get("trace"))
    expected = _sealed({key: item for key, item in value.items() if key != "seal"})
    if value.get("seal") != expected["seal"]:
        raise EvidenceEvalError("model_plan_seal_invalid")
    return value


def replay(repo, manifest, artifact):
    artifact = _validate_evaluation(artifact)
    root = repo_root(repo)
    _, current_snapshot = _source_snapshot(root, manifest["digest"])
    if (
        artifact["manifest_sha256"] != manifest["digest"]
        or artifact["source_snapshot_sha256"] != current_snapshot
    ):
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "BLOCK",
            "reason": "snapshot_mismatch",
            "changed_cases": [],
        }
    candidate = run_evaluation(root, manifest, artifact_type="candidate")
    expected = {
        item["id"]: (
            item["passed"],
            item["exit_code"],
            item["marker_count"],
            item["marker_pass_count"],
            item["skip_detected"],
        )
        for item in artifact["cases"]
    }
    actual = {
        item["id"]: (
            item["passed"],
            item["exit_code"],
            item["marker_count"],
            item["marker_pass_count"],
            item["skip_detected"],
        )
        for item in candidate["cases"]
    }
    changed = sorted(
        set(expected) ^ set(actual)
        | {case_id for case_id in set(expected) & set(actual) if expected[case_id] != actual[case_id]}
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not changed else "BLOCK",
        "reason": "replayed" if not changed else "semantic_drift",
        "changed_cases": changed,
    }


def _write(path, value):
    absolute = os.path.abspath(path)
    directory = os.path.dirname(absolute)
    if not os.path.isdir(directory) or os.path.islink(directory):
        raise EvidenceEvalError("output_directory_unsafe")
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if len(payload.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise EvidenceEvalError("output_oversize")
    try:
        atomic_write(absolute, payload, mode=0o600, refuse_symlink=True)
    except (OSError, ValueError) as exc:
        raise EvidenceEvalError("output_write_failed") from exc


def _emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")


def parser():
    result = argparse.ArgumentParser(prog="evidence-eval.sh")
    sub = result.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--manifest", required=True)
    run_parser.add_argument("--repo", default=".")
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument(
        "--artifact-type", choices=("baseline", "candidate"), default="candidate"
    )

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--artifact", required=True)

    compare_parser = sub.add_parser("compare")
    compare_parser.add_argument("--baseline", required=True)
    compare_parser.add_argument("--candidate", required=True)
    compare_parser.add_argument("--output")

    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("--manifest", required=True)
    replay_parser.add_argument("--artifact", required=True)
    replay_parser.add_argument("--repo", default=".")

    model_parser = sub.add_parser("model-plan")
    model_parser.add_argument("--manifest", required=True)
    model_parser.add_argument("--output")

    check_parser = sub.add_parser("check")
    check_parser.add_argument("--manifest", required=True)
    check_parser.add_argument("--baseline", required=True)
    check_parser.add_argument("--repo", default=".")
    check_parser.add_argument("--output")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "run":
            manifest = load_manifest(args.manifest)
            value = run_evaluation(args.repo, manifest, args.artifact_type)
            _write(args.output, value)
            _emit(value["summary"])
            return 0 if value["summary"]["verdict"] == "PASS" else 1
        if args.command == "verify":
            value = load_artifact(args.artifact)
            _emit({
                "schema_version": SCHEMA_VERSION,
                "status": "PASS",
                "artifact_type": value["artifact_type"],
                "seal": value["seal"],
            })
            return 0
        if args.command == "compare":
            value = compare_artifacts(
                load_artifact(args.baseline), load_artifact(args.candidate)
            )
            if args.output:
                _write(args.output, value)
            _emit(value)
            return 0 if value["verdict"] == "PASS" else 1
        if args.command == "replay":
            value = replay(
                args.repo, load_manifest(args.manifest), load_artifact(args.artifact)
            )
            _emit(value)
            return 0 if value["status"] == "PASS" else 1
        if args.command == "model-plan":
            value = build_model_plan(load_manifest(args.manifest))
            if args.output:
                _write(args.output, value)
            _emit(value)
            return 0
        if args.command == "check":
            manifest = load_manifest(args.manifest)
            candidate = run_evaluation(args.repo, manifest, "candidate")
            if args.output:
                _write(args.output, candidate)
            comparison = compare_artifacts(load_artifact(args.baseline), candidate)
            _emit({
                "schema_version": SCHEMA_VERSION,
                "status": comparison["verdict"],
                "candidate_verdict": candidate["summary"]["verdict"],
                "regression_count": comparison["regression_count"],
                "regressions": comparison["regressions"],
                "candidate_seal": candidate["seal"],
            })
            return 0 if comparison["verdict"] == "PASS" else 1
        raise EvidenceEvalError("command_invalid")
    except EvidenceEvalError as exc:
        sys.stderr.write("evidence-eval: %s\n" % exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
