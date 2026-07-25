"""Mechanical contracts that make semantic code-review rounds converge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat

from . import build_replan


MAX_TEXT_BYTES = 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
COMMIT_RE = re.compile(r"^[a-f0-9]{40,64}$")
EVIDENCE_SPEC_RE = re.compile(
    r"^(review-evidence/[A-Za-z0-9._/-]+)@([a-f0-9]{64})$"
)
CANDIDATE_RE = re.compile(
    r"^CANDIDATE (?P<severity>BLOCKER|HIGH|MEDIUM|LOW) "
    r"(?P<ref>.+?) :: (?P<claim>.+?) :: "
    r"verify=(?P<verify>(?:command|verifier):[^\x00-\x1f\x7f]+)$"
)
FINDING_RE = re.compile(
    r"^FINDING (?P<severity>BLOCKER|HIGH|MEDIUM|LOW) "
    r"(?P<ref>.+?) :: (?P<reason>.+) :: "
    r"class=(?P<class>[a-z0-9][a-z0-9-]{0,63}) :: "
    r"verify=(?P<verify>(?:command|verifier):[^\x00-\x1f\x7f]+) :: "
    r"evidence=(?P<evidence>review-evidence/[A-Za-z0-9._/-]+@[a-f0-9]{64})$"
)
RESOLVED_RE = re.compile(
    r"^RESOLVED class=(?P<class>[a-z0-9][a-z0-9-]{0,63}) :: "
    r"verify=(?P<verify>(?:command|verifier):[^\x00-\x1f\x7f]+) :: "
    r"evidence=(?P<evidence>review-evidence/[A-Za-z0-9._/-]+@[a-f0-9]{64})$"
)
BASELINE_RE = re.compile(
    r"^<!-- kimiflow:strategy gate=code epoch-start=1 "
    r"fingerprint=(?P<fingerprint>[a-f0-9]{64}) -->$"
)
RECOVERY_RE = re.compile(
    r"^<!-- kimiflow:recovery gate=code "
    r"source-round=(?P<source>[0-9]+) "
    r"epoch-start=(?P<start>[0-9]+) "
    r"cap=(?P<cap>[0-9]+) "
    r"before=(?P<before>[a-f0-9]{64}) "
    r"after=(?P<after>[a-f0-9]{64}) -->$"
)
SATURATION_KEYS = {
    "schema_version",
    "round",
    "plan_sha256",
    "review_base_sha",
    "review_target_sha",
    "review_snapshot_sha256",
    "axes",
    "candidate_files",
    "dispositions",
    "carried_classes",
}
DISPOSITION_KEYS = {
    "candidate_id",
    "outcome",
    "stable_class",
    "verify",
    "evidence",
}
REPAIR_KEYS = {
    "schema_version",
    "round",
    "plan_sha256",
    "findings_sha256",
    "groups",
}
GROUP_KEYS = {"id", "classes", "root_cause", "depends_on", "repair", "checks"}
CHECK_KEYS = {"kind", "method"}
TRAJECTORY_KEYS = {
    "schema_version",
    "source_round",
    "plan_sha256",
    "failed_source_rounds",
    "recovery_receipt_sha256s",
    "prior_trajectory_sha256s",
    "hypothesis",
    "action",
    "changed_assumption",
    "checks",
}
TRAJECTORY_ACTIONS = {
    "replan",
    "decompose",
    "architecture_reset",
    "new_falsifier",
}


class GateError(ValueError):
    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _emit(status, reason, detail="", blockers=None):
    if blockers is None:
        blockers = 0 if status == "OPEN" else 1
    print(
        "REVIEW_CONVERGENCE_GATE\t%s\tblockers=%s\treason=%s\tdetail=%s"
        % (status, blockers, reason, detail)
    )
    return 0


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _regular_bytes(path, maximum=MAX_TEXT_BYTES):
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise GateError("missing-artifact", path) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise GateError("unsafe-artifact", path)
    if info.st_size <= 0 or info.st_size > maximum:
        raise GateError("unsafe-artifact", path)
    try:
        with open(path, "rb") as handle:
            payload = handle.read(maximum + 1)
    except OSError as exc:
        raise GateError("unsafe-artifact", path) from exc
    if len(payload) > maximum:
        raise GateError("unsafe-artifact", path)
    return payload


def _text(path, maximum=MAX_TEXT_BYTES):
    try:
        return _regular_bytes(path, maximum).decode("utf-8")
    except UnicodeError as exc:
        raise GateError("malformed-artifact", path) from exc


def _json(path):
    try:
        return json.loads(_regular_bytes(path, MAX_JSON_BYTES).decode("utf-8"))
    except GateError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("malformed-artifact", path) from exc


def _run_dir(path):
    path = os.path.abspath(path)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise GateError("run-missing", path) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise GateError("run-unsafe", path)
    return os.path.realpath(path)


def _artifact_path(run, *parts):
    cursor = run
    for part in parts[:-1]:
        cursor = os.path.join(cursor, part)
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            return os.path.join(run, *parts)
        except OSError as exc:
            raise GateError("unsafe-artifact", cursor) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise GateError("unsafe-artifact", cursor)
    return os.path.join(run, *parts)


def _round(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 999999:
        raise GateError("round-invalid", str(value))
    return value


def _bounded(value, maximum=1000, minimum=1):
    return (
        isinstance(value, str)
        and minimum <= len(value.strip()) <= maximum
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _meaningful_method(kind, method):
    if kind not in ("command", "verifier") or not _bounded(method, 1000, 4):
        return False
    normalized = " ".join(method.split())
    lowered = normalized.casefold()
    if kind == "verifier":
        return len(normalized) >= 12 and lowered not in {
            "check it",
            "inspect code",
            "looks good",
            "manual check",
            "review code",
        }
    try:
        tokens = shlex.split(normalized)
    except ValueError:
        return False
    if not tokens:
        return False
    executable = os.path.basename(tokens[0]).casefold()
    if executable == "command":
        return len(tokens) > 1 and _meaningful_method(kind, shlex.join(tokens[1:]))
    if executable in {"true", ":", "exit", "return"}:
        return False
    if executable in {"echo", "printf"} and not any(
        token in {"|", "&&", "||"} for token in tokens
    ):
        return False
    if executable in {"sh", "bash", "zsh", "dash", "ksh", "fish"}:
        if len(tokens) < 2 or any(token in {"-c", "--command"} for token in tokens[1:]):
            return False
        script = next((token for token in tokens[1:] if not token.startswith("-")), "")
        return bool(script) and (
            "/" in script
            or script.endswith((".sh", ".bash", ".zsh"))
            or any(word in script.casefold() for word in ("test", "check", "verify"))
        )
    if executable.startswith(("python", "pypy")):
        if any(token in {"-c", "--command"} for token in tokens[1:]):
            return False
        return len(tokens) >= 3 and (
            tokens[1] == "-m"
            or "/" in tokens[1]
            or tokens[1].endswith(".py")
        )
    if executable in {"test", "["}:
        args = [token for token in tokens[1:] if token not in {"!", "]"}]
        return len(args) == 2 and args[0] in {
            "-b", "-c", "-d", "-e", "-f", "-g", "-h", "-k", "-L", "-p",
            "-r", "-S", "-s", "-u", "-w", "-x",
        }
    return True


def _typed_verify(value):
    if not isinstance(value, str) or ":" not in value:
        return None
    kind, method = value.split(":", 1)
    if not _meaningful_method(kind, method):
        return None
    return kind, " ".join(method.split())


def _axes(value):
    if not isinstance(value, str):
        raise GateError("axes-invalid")
    axes = value.split(",")
    if (
        not axes
        or len(axes) > 8
        or len(axes) != len(set(axes))
        or any(SLUG_RE.fullmatch(axis) is None for axis in axes)
    ):
        raise GateError("axes-invalid")
    return axes


def _plan(run):
    payload = _regular_bytes(_artifact_path(run, "PLAN.md"))
    return payload, _sha(payload)


def _review_basis(run, base):
    try:
        root, _ = build_replan._root_for(run)
        state_text = _text(_artifact_path(run, "STATE.md"))
        paths = sorted(
            {
                build_replan._normalize_path(path)
                for path in build_replan._state_affected_paths(state_text)
            }
        )
        if not paths:
            raise GateError("review-basis-invalid", "affected-paths")
        target_proc = build_replan._git(root, "rev-parse", "HEAD")
        if target_proc.returncode:
            raise GateError("review-basis-invalid", "HEAD")
        target = target_proc.stdout.decode("ascii", "strict").strip().casefold()
        base_proc = build_replan._git(root, "rev-parse", "--verify", "%s^{commit}" % base)
        if base_proc.returncode:
            raise GateError("review-basis-invalid", "base")
        resolved_base = base_proc.stdout.decode("ascii", "strict").strip().casefold()
        if COMMIT_RE.fullmatch(resolved_base) is None or COMMIT_RE.fullmatch(target) is None:
            raise GateError("review-basis-invalid", "commit")
        ancestor = build_replan._git(root, "merge-base", resolved_base, target)
        if (
            ancestor.returncode
            or ancestor.stdout.decode("ascii", "strict").strip().casefold()
            != resolved_base
        ):
            raise GateError("review-basis-invalid", "not-ancestor")
        snapshot = build_replan._snapshot(root, paths)
    except GateError:
        raise
    except (build_replan.BuildReplanError, OSError, UnicodeError, ValueError) as exc:
        raise GateError("review-basis-invalid", str(exc)) from exc
    return {
        "review_base_sha": resolved_base,
        "review_target_sha": target,
        "review_snapshot_sha256": snapshot["sha256"],
    }


def basis(run, base):
    run = _run_dir(run)
    value = _review_basis(run, base)
    print(
        json.dumps(
            {"schema_version": 1, **value},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _safe_evidence_path(run, relative):
    if not isinstance(relative, str) or not relative.startswith("review-evidence/"):
        raise GateError("evidence-path-invalid", str(relative))
    normalized = os.path.normpath(relative).replace(os.sep, "/")
    if normalized != relative or normalized.startswith("../") or "/../" in relative:
        raise GateError("evidence-path-invalid", relative)
    return _artifact_path(run, *relative.split("/"))


def _evidence(run, spec, stable_class, verify, outcome):
    match = EVIDENCE_SPEC_RE.fullmatch(spec) if isinstance(spec, str) else None
    if match is None:
        raise GateError("evidence-spec-invalid", str(spec))
    relative, expected = match.groups()
    payload = _regular_bytes(_safe_evidence_path(run, relative), 8192)
    if _sha(payload) != expected:
        raise GateError("evidence-digest-mismatch", relative)
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise GateError("evidence-malformed", relative) from exc
    if text.count("\n") != 1 or not text.endswith("\n"):
        raise GateError("evidence-malformed", relative)
    prefix = (
        "REVIEW_EVIDENCE class=%s :: verify=%s :: outcome=%s :: "
        % (stable_class, verify, outcome)
    )
    if not text.startswith(prefix) or len(text.strip()) <= len(prefix):
        raise GateError("evidence-mismatch", relative)
    return spec


def _aggregate(run, round_number):
    path = _artifact_path(
        run, "findings", "r%s-code-verified.md" % round_number
    )
    payload = _regular_bytes(path)
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise GateError("aggregate-malformed", path) from exc
    lines = text.splitlines()
    if lines == ["NONE"]:
        return path, payload, {}
    if not lines or "NONE" in lines:
        raise GateError("aggregate-malformed", path)
    material = {}
    for index, line in enumerate(lines, 1):
        finding = FINDING_RE.fullmatch(line)
        resolved = RESOLVED_RE.fullmatch(line)
        if finding is None and resolved is None:
            raise GateError("aggregate-malformed", "%s:%s" % (path, index))
        if resolved is not None:
            if _typed_verify(resolved.group("verify")) is None:
                raise GateError("aggregate-verify-invalid", resolved.group("class"))
            _evidence(
                run,
                resolved.group("evidence"),
                resolved.group("class"),
                resolved.group("verify"),
                "not_reproduced",
            )
            continue
        if _typed_verify(finding.group("verify")) is None:
            raise GateError("aggregate-verify-invalid", finding.group("class"))
        _evidence(
            run,
            finding.group("evidence"),
            finding.group("class"),
            finding.group("verify"),
            "reproduced",
        )
        if finding.group("severity") not in ("BLOCKER", "HIGH"):
            continue
        stable_class = finding.group("class")
        if stable_class in material:
            raise GateError("aggregate-class-duplicate", stable_class)
        material[stable_class] = {
            "verify": finding.group("verify"),
            "evidence": finding.group("evidence"),
        }
    return path, payload, material


def _candidate_rows(path, axis):
    payload = _regular_bytes(path)
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise GateError("candidate-malformed", path) from exc
    lines = text.splitlines()
    if lines == ["NONE"]:
        return payload, {}
    if not lines or "NONE" in lines:
        raise GateError("candidate-malformed", path)
    result = {}
    for index, line in enumerate(lines, 1):
        match = CANDIDATE_RE.fullmatch(line)
        if match is None or len(line.encode("utf-8")) > 4096:
            raise GateError("candidate-malformed", "%s:%s" % (path, index))
        if _typed_verify(match.group("verify")) is None:
            raise GateError("candidate-verify-invalid", "%s:%s" % (path, index))
        candidate_id = "cand_" + _sha((axis + "\0" + line).encode("utf-8"))
        if candidate_id in result:
            raise GateError("candidate-duplicate", candidate_id)
        result[candidate_id] = {
            "axis": axis,
            "severity": match.group("severity"),
            "verify": match.group("verify"),
        }
    return payload, result


def saturation(run, round_number, axes):
    run = _run_dir(run)
    round_number = _round(round_number)
    axes = _axes(axes)
    plan_payload, plan_sha = _plan(run)
    candidates = {}
    file_rows = []
    for axis in axes:
        path = _artifact_path(
            run,
            "code-review-candidates",
            "r%s-%s.md" % (round_number, axis),
        )
        try:
            payload, rows = _candidate_rows(path, axis)
        except GateError as exc:
            if exc.reason == "missing-artifact":
                raise GateError("missing-axis", axis)
            raise
        file_rows.append({"axis": axis, "sha256": _sha(payload)})
        candidates.update(rows)
    receipt_path = _artifact_path(
        run, "review-saturation", "r%s.json" % round_number
    )
    try:
        receipt = _json(receipt_path)
    except GateError as exc:
        if exc.reason == "missing-artifact":
            raise GateError("missing-saturation", receipt_path)
        raise
    if not isinstance(receipt, dict) or set(receipt) != SATURATION_KEYS:
        raise GateError("saturation-malformed")
    if receipt.get("schema_version") != 1 or receipt.get("round") != round_number:
        raise GateError("saturation-malformed")
    if receipt.get("plan_sha256") != plan_sha:
        raise GateError("stale-plan")
    base = receipt.get("review_base_sha")
    target = receipt.get("review_target_sha")
    snapshot = receipt.get("review_snapshot_sha256")
    if (
        not isinstance(base, str)
        or COMMIT_RE.fullmatch(base) is None
        or not isinstance(target, str)
        or COMMIT_RE.fullmatch(target) is None
        or not isinstance(snapshot, str)
        or SHA_RE.fullmatch(snapshot) is None
    ):
        raise GateError("review-basis-invalid", "receipt")
    if _review_basis(run, base) != {
        "review_base_sha": base,
        "review_target_sha": target,
        "review_snapshot_sha256": snapshot,
    }:
        raise GateError("stale-review-basis")
    if receipt.get("axes") != axes:
        raise GateError("axis-receipt-mismatch")
    if receipt.get("candidate_files") != file_rows:
        raise GateError("candidate-digest-mismatch")
    carried = receipt.get("carried_classes")
    if (
        not isinstance(carried, list)
        or len(carried) != len(set(carried))
        or any(SLUG_RE.fullmatch(value) is None for value in carried)
    ):
        raise GateError("carried-classes-invalid")
    previous_aggregate = {}
    if carried:
        if round_number == 1:
            raise GateError("carried-class-unproven", carried[0])
        try:
            _, _, previous_aggregate = _aggregate(run, round_number - 1)
        except GateError as exc:
            if exc.reason == "missing-artifact":
                raise GateError("carried-class-unproven", carried[0]) from exc
            raise
        for stable_class in carried:
            if stable_class not in previous_aggregate:
                raise GateError("carried-class-unproven", stable_class)
    dispositions = receipt.get("dispositions")
    if not isinstance(dispositions, list) or len(dispositions) > 256:
        raise GateError("dispositions-malformed")
    seen_candidates = set()
    promoted_by_class = {}
    class_outcomes = {}
    for row in dispositions:
        if not isinstance(row, dict) or set(row) != DISPOSITION_KEYS:
            raise GateError("disposition-malformed")
        candidate_id = row.get("candidate_id")
        candidate = candidates.get(candidate_id)
        if candidate is None or candidate["severity"] not in ("BLOCKER", "HIGH"):
            raise GateError("disposition-candidate-invalid", str(candidate_id))
        if candidate_id in seen_candidates:
            raise GateError("disposition-duplicate", candidate_id)
        seen_candidates.add(candidate_id)
        outcome = row.get("outcome")
        stable_class = row.get("stable_class")
        verify = row.get("verify")
        if outcome not in ("promoted", "refuted"):
            raise GateError("disposition-outcome-invalid", candidate_id)
        if not isinstance(stable_class, str) or SLUG_RE.fullmatch(stable_class) is None:
            raise GateError("disposition-class-invalid", candidate_id)
        if verify != candidate["verify"]:
            raise GateError("disposition-verify-mismatch", candidate_id)
        expected_evidence_outcome = (
            "reproduced" if outcome == "promoted" else "not_reproduced"
        )
        _evidence(
            run,
            row.get("evidence"),
            stable_class,
            verify,
            expected_evidence_outcome,
        )
        prior = class_outcomes.get(stable_class)
        if prior is not None and prior != outcome:
            raise GateError("class-outcome-conflict", stable_class)
        class_outcomes[stable_class] = outcome
        if outcome == "promoted":
            expected = {"verify": verify, "evidence": row["evidence"]}
            prior_promoted = promoted_by_class.get(stable_class)
            if prior_promoted is not None and prior_promoted != expected:
                raise GateError("promoted-class-conflict", stable_class)
            promoted_by_class[stable_class] = expected
    required_candidates = {
        candidate_id
        for candidate_id, row in candidates.items()
        if row["severity"] in ("BLOCKER", "HIGH")
    }
    missing = sorted(required_candidates - seen_candidates)
    if missing:
        raise GateError("undisposed-candidate", missing[0])
    _, _, aggregate = _aggregate(run, round_number)
    expected_classes = set(promoted_by_class) | set(carried)
    if set(aggregate) != expected_classes:
        raise GateError(
            "aggregate-coverage-mismatch",
            "expected=%s actual=%s"
            % (",".join(sorted(expected_classes)), ",".join(sorted(aggregate))),
        )
    for stable_class, expected in promoted_by_class.items():
        if aggregate.get(stable_class) != expected:
            raise GateError("aggregate-promotion-mismatch", stable_class)
    for stable_class in carried:
        if aggregate.get(stable_class) != previous_aggregate[stable_class]:
            raise GateError("carried-class-drift", stable_class)
    return _emit(
        "OPEN",
        "saturated",
        "axes=%s material=%s"
        % (len(axes), len(required_candidates)),
    )


def _checks(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise GateError("repair-check-invalid")
    normalized = []
    for row in value:
        if (
            not isinstance(row, dict)
            or set(row) != CHECK_KEYS
            or not _meaningful_method(row.get("kind"), row.get("method"))
        ):
            raise GateError("repair-check-invalid")
        normalized.append((row["kind"], " ".join(row["method"].split())))
    if len(normalized) != len(set(normalized)):
        raise GateError("repair-check-duplicate")
    return normalized


def _acyclic(groups):
    graph = {row["id"]: row["depends_on"] for row in groups}
    visiting = set()
    visited = set()

    def visit(node):
        if node in visiting:
            raise GateError("dependency-cycle", node)
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            if dependency not in graph:
                raise GateError("dependency-missing", dependency)
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def repair(run, round_number):
    run = _run_dir(run)
    round_number = _round(round_number)
    _, plan_sha = _plan(run)
    _, findings_payload, aggregate = _aggregate(run, round_number)
    if not aggregate:
        return _emit("OPEN", "not-required", "no material findings")
    receipt_path = _artifact_path(
        run, "review-repairs", "r%s.json" % round_number
    )
    try:
        receipt = _json(receipt_path)
    except GateError as exc:
        if exc.reason == "missing-artifact":
            raise GateError("missing-repair", receipt_path)
        raise
    if not isinstance(receipt, dict) or set(receipt) != REPAIR_KEYS:
        raise GateError("repair-malformed")
    if receipt.get("schema_version") != 1 or receipt.get("round") != round_number:
        raise GateError("repair-malformed")
    if receipt.get("plan_sha256") != plan_sha:
        raise GateError("stale-plan")
    if receipt.get("findings_sha256") != _sha(findings_payload):
        raise GateError("stale-findings")
    groups = receipt.get("groups")
    if not isinstance(groups, list) or not 1 <= len(groups) <= 32:
        raise GateError("repair-groups-invalid")
    group_ids = set()
    covered = []
    for group in groups:
        if not isinstance(group, dict) or set(group) != GROUP_KEYS:
            raise GateError("repair-group-malformed")
        group_id = group.get("id")
        if not isinstance(group_id, str) or SLUG_RE.fullmatch(group_id) is None:
            raise GateError("repair-group-id-invalid")
        if group_id in group_ids:
            raise GateError("repair-group-duplicate", group_id)
        group_ids.add(group_id)
        classes = group.get("classes")
        if (
            not isinstance(classes, list)
            or not classes
            or len(classes) != len(set(classes))
            or any(
                not isinstance(value, str) or SLUG_RE.fullmatch(value) is None
                for value in classes
            )
        ):
            raise GateError("repair-classes-invalid", group_id)
        covered.extend(classes)
        dependencies = group.get("depends_on")
        if (
            not isinstance(dependencies, list)
            or len(dependencies) != len(set(dependencies))
            or any(
                not isinstance(value, str) or SLUG_RE.fullmatch(value) is None
                for value in dependencies
            )
            or group_id in dependencies
        ):
            raise GateError("repair-dependencies-invalid", group_id)
        if not _bounded(group.get("root_cause"), 1000, 8):
            raise GateError("repair-root-cause-invalid", group_id)
        if not _bounded(group.get("repair"), 1000, 8):
            raise GateError("repair-action-invalid", group_id)
        checks = _checks(group.get("checks"))
        covered_methods = {
            _typed_verify(aggregate[stable_class]["verify"])
            for stable_class in classes
        }
        if not set(checks).intersection(covered_methods):
            raise GateError("repair-check-unbound", group_id)
    if len(covered) != len(set(covered)) or set(covered) != set(aggregate):
        raise GateError(
            "incomplete-repair",
            "expected=%s actual=%s"
            % (",".join(sorted(aggregate)), ",".join(sorted(set(covered)))),
        )
    _acyclic(groups)
    return _emit(
        "OPEN",
        "repair-ready",
        "groups=%s classes=%s" % (len(groups), len(covered)),
    )


def _recovery(run):
    path = _artifact_path(run, "RECOVERY.md")
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise GateError("unsafe-artifact", path) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise GateError("unsafe-artifact", path)
    lines = _text(path).splitlines()
    baselines = [BASELINE_RE.fullmatch(line) for line in lines]
    baselines = [match for match in baselines if match is not None]
    markers = []
    malformed_code_marker = False
    for line in lines:
        if line.startswith("<!-- kimiflow:recovery gate=code "):
            match = RECOVERY_RE.fullmatch(line)
            if match is None:
                malformed_code_marker = True
            else:
                row = {name: match.group(name) for name in match.groupdict()}
                row.update(
                    {
                        "line": line,
                        "source": int(row["source"]),
                        "start": int(row["start"]),
                        "cap": int(row["cap"]),
                    }
                )
                markers.append(row)
    if malformed_code_marker or (markers and len(baselines) != 1):
        raise GateError("recovery-malformed")
    if not markers:
        return []
    expected_before = baselines[0].group("fingerprint")
    last_start = 1
    for row in markers:
        if (
            row["start"] <= last_start
            or row["source"] != row["start"] - 1
            or row["cap"] < row["start"]
            or row["before"] != expected_before
            or row["before"] == row["after"]
        ):
            raise GateError("recovery-malformed")
        expected_before = row["after"]
        last_start = row["start"]
    plan_payload, plan_sha = _plan(run)
    if markers[-1]["after"] != plan_sha:
        raise GateError("stale-plan")
    return markers


def _trajectory_semantics(receipt):
    if not _bounded(receipt.get("hypothesis"), 1000, 12):
        raise GateError("trajectory-hypothesis-invalid")
    if receipt.get("action") not in TRAJECTORY_ACTIONS:
        raise GateError("trajectory-action-invalid")
    if not _bounded(receipt.get("changed_assumption"), 1000, 12):
        raise GateError("trajectory-assumption-invalid")
    hypothesis = " ".join(receipt["hypothesis"].split())
    assumption = " ".join(receipt["changed_assumption"].split())
    if hypothesis.casefold() == assumption.casefold():
        raise GateError("trajectory-assumption-invalid")
    checks = _checks(receipt.get("checks"))
    return (
        receipt["action"],
        hypothesis.casefold(),
        assumption.casefold(),
        tuple((kind, method.casefold()) for kind, method in checks),
    ), hypothesis, assumption, checks


def preflight(run, round_number):
    run = _run_dir(run)
    round_number = _round(round_number)
    markers = _recovery(run)
    if len(markers) < 2:
        return _emit("OPEN", "below-threshold", "failed_strategies=%s" % len(markers))
    latest = markers[-2:]
    source_round = latest[-1]["source"]
    if round_number <= source_round:
        raise GateError("round-invalid", str(round_number))
    receipt_path = _artifact_path(
        run, "review-trajectories", "source-r%s.json" % source_round
    )
    try:
        receipt = _json(receipt_path)
    except GateError as exc:
        if exc.reason == "missing-artifact":
            raise GateError("trajectory-required", receipt_path)
        raise
    if not isinstance(receipt, dict) or set(receipt) != TRAJECTORY_KEYS:
        raise GateError("trajectory-malformed")
    if receipt.get("schema_version") != 1 or receipt.get("source_round") != source_round:
        raise GateError("trajectory-malformed")
    plan_payload, plan_sha = _plan(run)
    if receipt.get("plan_sha256") != plan_sha:
        raise GateError("stale-plan")
    failed_rounds = [row["source"] for row in latest]
    if receipt.get("failed_source_rounds") != failed_rounds:
        raise GateError("trajectory-receipts-mismatch")
    receipt_hashes = [_sha(row["line"].encode("utf-8")) for row in latest]
    if receipt.get("recovery_receipt_sha256s") != receipt_hashes:
        raise GateError("trajectory-receipts-mismatch")
    prior_payloads = []
    prior_semantics = []
    for row in markers[1:-1]:
        prior_path = _artifact_path(
            run, "review-trajectories", "source-r%s.json" % row["source"]
        )
        payload = _regular_bytes(prior_path, MAX_JSON_BYTES)
        try:
            prior = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise GateError("trajectory-malformed", prior_path) from exc
        if (
            not isinstance(prior, dict)
            or set(prior) != TRAJECTORY_KEYS
            or prior.get("schema_version") != 1
            or prior.get("source_round") != row["source"]
        ):
            raise GateError("trajectory-malformed", prior_path)
        prior_payloads.append(_sha(payload))
        prior_semantics.append(_trajectory_semantics(prior)[0])
    if receipt.get("prior_trajectory_sha256s") != prior_payloads:
        raise GateError("trajectory-receipts-mismatch")
    semantics, hypothesis, assumption, checks = _trajectory_semantics(receipt)
    if semantics in prior_semantics:
        raise GateError("trajectory-repeated")
    try:
        plan_text = plan_payload.decode("utf-8")
    except UnicodeError as exc:
        raise GateError("trajectory-plan-mismatch") from exc
    required_lines = {
        "Trajectory action: %s" % receipt["action"],
        "Trajectory hypothesis: %s" % hypothesis,
        "Changed assumption: %s" % assumption,
    }
    required_lines.update(
        "Trajectory check: %s :: %s" % check for check in checks
    )
    if not required_lines.issubset(set(plan_text.splitlines())):
        raise GateError("trajectory-plan-mismatch")
    return _emit(
        "OPEN",
        "trajectory-ready",
        "source_round=%s action=%s" % (source_round, receipt["action"]),
    )


def _parser():
    parser = argparse.ArgumentParser(prog="review-convergence-gate.sh")
    subparsers = parser.add_subparsers(dest="command", required=True)
    basis_parser = subparsers.add_parser("basis")
    basis_parser.add_argument("--run", required=True)
    basis_parser.add_argument("--base", required=True)
    saturation_parser = subparsers.add_parser("saturation")
    saturation_parser.add_argument("--run", required=True)
    saturation_parser.add_argument("--round", required=True, type=int)
    saturation_parser.add_argument("--axes", required=True)
    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--run", required=True)
    repair_parser.add_argument("--round", required=True, type=int)
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--run", required=True)
    preflight_parser.add_argument("--round", required=True, type=int)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "basis":
            return basis(args.run, args.base)
        if args.command == "saturation":
            return saturation(args.run, args.round, args.axes)
        if args.command == "repair":
            return repair(args.run, args.round)
        return preflight(args.run, args.round)
    except GateError as exc:
        return _emit("CLOSED", exc.reason, exc.detail)


if __name__ == "__main__":
    raise SystemExit(main())
