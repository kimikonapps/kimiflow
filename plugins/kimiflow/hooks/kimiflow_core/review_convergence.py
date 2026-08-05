"""Mechanical contracts that make semantic code-review rounds converge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat

from . import adaptive_control, build_replan


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
SATURATION_V2_KEYS = SATURATION_KEYS | {
    "scheduled_axes",
    "review_files",
    "delta_receipt",
}
SATURATION_V3_KEYS = SATURATION_V2_KEYS
DELTA_KEYS = {
    "schema_version",
    "source_round",
    "round",
    "plan_sha256",
    "source_saturation_sha256",
    "repair_sha256",
    "scheduled_axes",
    "rerun_axes",
    "carried_axes",
    "review_files",
    "changed_paths",
    "route_receipt_sha256",
}
REVIEW_FILE_KEYS = {
    "path",
    "status_sha256",
    "worktree_sha256",
    "index_sha256",
    "head_sha256",
    "mode",
}
DELTA_SPEC_RE = re.compile(
    r"^(review-deltas/r[1-9][0-9]*\.json)@([a-f0-9]{64})$"
)
MAX_SELECTIVE_CHANGED_PATHS = 8
MAX_SEMANTIC_REVIEW_ROUNDS = 3
REVIEW_CLOSEOUT_ROUND = MAX_SEMANTIC_REVIEW_ROUNDS + 1
DISPOSITION_KEYS = {
    "candidate_id",
    "outcome",
    "stable_class",
    "verify",
    "evidence",
}
DISPOSITION_V3_KEYS = DISPOSITION_KEYS | {
    "contract_status",
    "support_status",
    "impact_class",
    "proportionality",
}
DISPOSITION_OUTCOMES = {
    "promoted",
    "refuted",
    "non_blocking",
    "material_decision",
}
CONTRACT_STATUSES = {"violated", "not_violated"}
SUPPORT_STATUSES = {"supported", "unsupported"}
IMPACT_CLASSES = {
    "none",
    "correctness",
    "runtime",
    "security",
    "privacy",
    "data_loss",
    "paid",
    "scope",
    "breaking",
    "irreversible",
}
PROTECTED_IMPACTS = {"security", "privacy", "data_loss", "irreversible"}
USER_BOUNDARY_IMPACTS = {"paid", "privacy", "scope", "breaking", "irreversible"}
REPAIR_KEYS = {
    "schema_version",
    "round",
    "plan_sha256",
    "findings_sha256",
    "groups",
}
REPAIR_V2_KEYS = REPAIR_KEYS | {"source_saturation_sha256"}
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
TRAJECTORY_V2_KEYS = TRAJECTORY_KEYS | {"stable_class"}
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


def _axis_list(value, reason="axes-invalid"):
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 8
        or len(value) != len(set(value))
        or any(not isinstance(axis, str) or SLUG_RE.fullmatch(axis) is None for axis in value)
    ):
        raise GateError(reason)
    return value


def _review_files(value):
    if not isinstance(value, list) or not value or len(value) > 256:
        raise GateError("review-files-invalid")
    paths = []
    for row in value:
        if not isinstance(row, dict) or set(row) != REVIEW_FILE_KEYS:
            raise GateError("review-files-invalid")
        try:
            normalized = build_replan._normalize_path(row.get("path"))
        except build_replan.BuildReplanError as exc:
            raise GateError("review-files-invalid") from exc
        if normalized != row.get("path"):
            raise GateError("review-files-invalid")
        for name in (
            "status_sha256",
            "worktree_sha256",
            "index_sha256",
            "head_sha256",
        ):
            digest = row.get(name)
            if digest is not None and (
                not isinstance(digest, str) or SHA_RE.fullmatch(digest) is None
            ):
                raise GateError("review-files-invalid")
        mode = row.get("mode")
        if mode is not None and (
            isinstance(mode, bool)
            or not isinstance(mode, int)
            or not 0 <= mode <= 0o777
        ):
            raise GateError("review-files-invalid")
        paths.append(normalized)
    if paths != sorted(set(paths)):
        raise GateError("review-files-invalid")
    return value


def _required_delta_axes(changed_paths, scheduled_axes):
    required = {"spec-correctness"}
    security_pattern = re.compile(
        r"(?:^|[/_.-])(?:"
        r"auth|authn|authz|oauth|authentication|authorization|authenticator|"
        r"security|secure|secret|secrets|credential|credentials|token|tokens|"
        r"session|sessions|privacy|permission|permissions|acl|rbac|payment|"
        r"payments|billing|migration|migrations|crypto"
        r")(?:[/_.-]|$)"
    )
    integration_prefixes = (
        ".github/",
        "docs/render/",
        "hooks/",
        "phases/",
        "plugins/",
        "skills/",
    )
    integration_names = {
        "package.json",
        "pyproject.toml",
        "cargo.toml",
        "go.mod",
        "plugin.json",
        "skill.md",
        "reference.md",
        "hooks.json",
        "agents.md",
        "claude.md",
        "readme.md",
    }
    if any(security_pattern.search(path.casefold()) for path in changed_paths):
        required.add("failure-security")
    if any(
        path.casefold().startswith(integration_prefixes)
        or os.path.basename(path).casefold() in integration_names
        for path in changed_paths
    ):
        required.add("standards-integration")
    return required.intersection(scheduled_axes)


def _plan(run):
    payload = _regular_bytes(_artifact_path(run, "PLAN.md"))
    return payload, _sha(payload)


def _review_basis(run, base, details=False):
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
    value = {
        "review_base_sha": resolved_base,
        "review_target_sha": target,
        "review_snapshot_sha256": snapshot["sha256"],
    }
    if details:
        value["review_files"] = snapshot["files"]
    return value


def _historical_review_basis(run, base, target, snapshot, review_files):
    canonical = json.dumps(
        review_files,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if _sha(canonical) != snapshot:
        raise GateError("stale-source-review-basis")
    try:
        root, _ = build_replan._root_for(run)
        head = build_replan._git(root, "rev-parse", "HEAD")
        base_commit = build_replan._git(
            root, "rev-parse", "--verify", "%s^{commit}" % base
        )
        target_commit = build_replan._git(
            root, "rev-parse", "--verify", "%s^{commit}" % target
        )
        if head.returncode or base_commit.returncode or target_commit.returncode:
            raise GateError("source-review-basis-invalid")
        resolved_head = head.stdout.decode("ascii", "strict").strip().casefold()
        resolved_base = base_commit.stdout.decode("ascii", "strict").strip().casefold()
        resolved_target = target_commit.stdout.decode("ascii", "strict").strip().casefold()
        ancestor = build_replan._git(root, "merge-base", resolved_base, resolved_target)
        if (
            resolved_target != resolved_head
            or ancestor.returncode
            or ancestor.stdout.decode("ascii", "strict").strip().casefold()
            != resolved_base
        ):
            raise GateError("source-review-basis-invalid")
    except GateError:
        raise
    except (build_replan.BuildReplanError, OSError, UnicodeError, ValueError) as exc:
        raise GateError("source-review-basis-invalid", str(exc)) from exc
    return {
        "review_base_sha": resolved_base,
        "review_target_sha": resolved_target,
        "review_snapshot_sha256": snapshot,
        "review_files": review_files,
    }


def basis(run, base, details=False):
    run = _run_dir(run)
    value = _review_basis(run, base, details=details)
    print(
        json.dumps(
            {"schema_version": 2 if details else 1, **value},
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


def _saturation_keys(schema_version):
    if schema_version == 1:
        return SATURATION_KEYS
    if schema_version == 2:
        return SATURATION_V2_KEYS
    if schema_version == 3:
        return SATURATION_V3_KEYS
    return set()


def _schema3_disposition(row, candidate_id):
    contract_status = row.get("contract_status")
    support_status = row.get("support_status")
    impact_class = row.get("impact_class")
    proportionality = row.get("proportionality")
    if contract_status not in CONTRACT_STATUSES:
        raise GateError("disposition-contract-status-invalid", candidate_id)
    if support_status not in SUPPORT_STATUSES:
        raise GateError("disposition-support-status-invalid", candidate_id)
    if impact_class not in IMPACT_CLASSES:
        raise GateError("disposition-impact-invalid", candidate_id)
    if not _bounded(proportionality, 1000, 8):
        raise GateError("disposition-proportionality-invalid", candidate_id)
    outcome = row["outcome"]
    if outcome == "promoted":
        if contract_status != "violated" or support_status != "supported":
            raise GateError("promoted-relevance-unproven", candidate_id)
        if impact_class == "none":
            raise GateError("promoted-impact-unproven", candidate_id)
    elif outcome == "refuted":
        if contract_status != "not_violated" or impact_class != "none":
            raise GateError("refuted-materiality-invalid", candidate_id)
    elif outcome == "non_blocking":
        if impact_class in PROTECTED_IMPACTS:
            raise GateError("protected-impact-required", candidate_id)
        if (
            contract_status != "not_violated"
            or impact_class != "none"
        ):
            raise GateError("non-blocking-relevance-invalid", candidate_id)
    elif outcome == "material_decision":
        if (
            contract_status != "violated"
            or support_status != "supported"
            or impact_class not in USER_BOUNDARY_IMPACTS
        ):
            raise GateError("material-decision-boundary-invalid", candidate_id)


def _prior_text_runtime_rounds(run, round_number, stable_class):
    count = 0
    for prior_round in range(1, round_number):
        path = _artifact_path(
            run, "review-saturation", "r%s.json" % prior_round
        )
        try:
            receipt = _json(path)
        except GateError as exc:
            if exc.reason == "missing-artifact":
                continue
            raise
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema_version") != 3
            or set(receipt) != SATURATION_V3_KEYS
        ):
            continue
        for row in receipt.get("dispositions", []):
            if (
                isinstance(row, dict)
                and set(row) == DISPOSITION_V3_KEYS
                and row.get("stable_class") == stable_class
                and row.get("impact_class") == "runtime"
                and isinstance(row.get("verify"), str)
                and row["verify"].startswith("verifier:")
            ):
                count += 1
                break
    return count


def saturation(run, round_number, axes, historical_plan_sha=None):
    run = _run_dir(run)
    round_number = _round(round_number)
    if round_number > REVIEW_CLOSEOUT_ROUND:
        raise GateError("review-limit-reached")
    axes = _axes(axes)
    _, current_plan_sha = _plan(run)
    if historical_plan_sha is not None and (
        not isinstance(historical_plan_sha, str)
        or SHA_RE.fullmatch(historical_plan_sha) is None
    ):
        raise GateError("source-plan-invalid")
    plan_sha = historical_plan_sha or current_plan_sha
    plan_recovery_closeout = False
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
    if round_number == REVIEW_CLOSEOUT_ROUND and candidates:
        raise GateError("closeout-candidates-forbidden")
    receipt_path = _artifact_path(
        run, "review-saturation", "r%s.json" % round_number
    )
    try:
        receipt = _json(receipt_path)
    except GateError as exc:
        if exc.reason == "missing-artifact":
            raise GateError("missing-saturation", receipt_path)
        raise
    if not isinstance(receipt, dict):
        raise GateError("saturation-malformed")
    schema_version = receipt.get("schema_version")
    expected_keys = _saturation_keys(schema_version)
    if set(receipt) != expected_keys or receipt.get("round") != round_number:
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
    expected_basis = {
        "review_base_sha": base,
        "review_target_sha": target,
        "review_snapshot_sha256": snapshot,
    }
    if historical_plan_sha is not None and schema_version == 1:
        raise GateError("source-saturation-schema-invalid")
    if schema_version == 1:
        if _review_basis(run, base) != expected_basis:
            raise GateError("stale-review-basis")
    else:
        scheduled_axes = _axis_list(
            receipt.get("scheduled_axes"), "scheduled-axes-invalid"
        )
        review_files = _review_files(receipt.get("review_files"))
        expected_basis["review_files"] = review_files
        if historical_plan_sha is None:
            if _review_basis(run, base, details=True) != expected_basis:
                raise GateError("stale-review-basis")
        elif _historical_review_basis(
            run, base, target, snapshot, review_files
        ) != expected_basis:
            raise GateError("stale-source-review-basis")
        delta_receipt = receipt.get("delta_receipt")
        if delta_receipt is None:
            if scheduled_axes != axes:
                raise GateError("selective-review-unproven")
            if round_number == REVIEW_CLOSEOUT_ROUND and not _recovery(run):
                raise GateError("closeout-delta-required")
            if round_number > 1:
                previous_path = _artifact_path(
                    run,
                    "review-saturation",
                    "r%s.json" % (round_number - 1),
                )
                try:
                    previous = _json(previous_path)
                except GateError as exc:
                    if exc.reason == "missing-artifact":
                        raise GateError("missing-source-saturation") from exc
                    raise
                previous_schema = (
                    previous.get("schema_version")
                    if isinstance(previous, dict)
                    else None
                )
                previous_keys = _saturation_keys(previous_schema)
                if (
                    not isinstance(previous, dict)
                    or set(previous) != previous_keys
                    or previous.get("round") != round_number - 1
                ):
                    raise GateError("source-saturation-malformed")
                if round_number == REVIEW_CLOSEOUT_ROUND:
                    plan_recovery_closeout = _plan_recovery_closeout(
                        run, round_number, previous, plan_sha, scheduled_axes
                    )
                    if not plan_recovery_closeout:
                        raise GateError("closeout-delta-required")
                elif previous.get("plan_sha256") == plan_sha:
                    raise GateError("incremental-review-required")
        else:
            _validate_delta(
                run,
                round_number,
                scheduled_axes,
                axes,
                delta_receipt,
            )
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
    material_decisions = []
    for row in dispositions:
        disposition_keys = (
            DISPOSITION_V3_KEYS if schema_version == 3 else DISPOSITION_KEYS
        )
        if not isinstance(row, dict) or set(row) != disposition_keys:
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
        allowed_outcomes = (
            DISPOSITION_OUTCOMES
            if schema_version == 3
            else {"promoted", "refuted", "non_blocking"}
        )
        if outcome not in allowed_outcomes:
            raise GateError("disposition-outcome-invalid", candidate_id)
        if not isinstance(stable_class, str) or SLUG_RE.fullmatch(stable_class) is None:
            raise GateError("disposition-class-invalid", candidate_id)
        if verify != candidate["verify"]:
            raise GateError("disposition-verify-mismatch", candidate_id)
        if schema_version == 3:
            _schema3_disposition(row, candidate_id)
            if (
                row["impact_class"] == "runtime"
                and verify.startswith("verifier:")
                and _prior_text_runtime_rounds(run, round_number, stable_class) >= 2
            ):
                raise GateError("runtime-evidence-required", stable_class)
        expected_evidence_outcome = (
            "not_reproduced" if outcome == "refuted" else "reproduced"
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
        elif outcome == "material_decision":
            material_decisions.append(stable_class)
    required_candidates = {
        candidate_id
        for candidate_id, row in candidates.items()
        if row["severity"] in ("BLOCKER", "HIGH")
    }
    missing = sorted(required_candidates - seen_candidates)
    if missing:
        raise GateError("undisposed-candidate", missing[0])
    _, _, aggregate = _aggregate(run, round_number)
    if plan_recovery_closeout:
        _, _, source_aggregate = _aggregate(run, round_number - 1)
        resolved = _resolved_findings(run, round_number)
        missing_resolutions = sorted(
            set(source_aggregate) - set(resolved)
        )
        if missing_resolutions:
            raise GateError(
                "closeout-resolution-incomplete", missing_resolutions[0]
            )
        mismatched_resolutions = sorted(
            stable_class
            for stable_class, source in source_aggregate.items()
            if resolved[stable_class]["verify"] != source["verify"]
        )
        if mismatched_resolutions:
            raise GateError(
                "closeout-resolution-verifier-mismatch",
                mismatched_resolutions[0],
            )
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
    if material_decisions:
        if historical_plan_sha is not None:
            raise GateError("source-material-decision-pending")
        return _emit(
            "CLOSED",
            "material-decision-required",
            "classes=%s" % ",".join(sorted(set(material_decisions))),
            blockers=len(set(material_decisions)),
        )
    if historical_plan_sha is not None:
        return True
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


def _resolved_findings(run, round_number):
    path = _artifact_path(
        run, "findings", "r%s-code-verified.md" % round_number
    )
    resolved = {}
    for line in _text(path).splitlines():
        match = RESOLVED_RE.fullmatch(line)
        if match is None:
            continue
        stable_class = match.group("class")
        if stable_class in resolved:
            raise GateError("resolved-class-duplicate", stable_class)
        resolved[stable_class] = {
            "verify": match.group("verify"),
            "evidence": match.group("evidence"),
        }
    return resolved


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


def _repair_details(run, round_number):
    run = _run_dir(run)
    round_number = _round(round_number)
    _, plan_sha = _plan(run)
    _, findings_payload, aggregate = _aggregate(run, round_number)
    if not aggregate:
        return {
            "required": False,
            "source_bound": False,
            "groups": 0,
            "classes": 0,
            "path": None,
            "payload": None,
        }
    receipt_path = _artifact_path(
        run, "review-repairs", "r%s.json" % round_number
    )
    try:
        receipt_payload = _regular_bytes(receipt_path, MAX_JSON_BYTES)
        receipt = json.loads(receipt_payload.decode("utf-8"))
    except GateError as exc:
        if exc.reason == "missing-artifact":
            raise GateError("missing-repair", receipt_path)
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("malformed-artifact", receipt_path) from exc
    schema_version = receipt.get("schema_version") if isinstance(receipt, dict) else None
    expected_keys = (
        REPAIR_KEYS
        if schema_version == 1
        else REPAIR_V2_KEYS if schema_version == 2 else set()
    )
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        raise GateError("repair-malformed")
    if receipt.get("round") != round_number:
        raise GateError("repair-malformed")
    if receipt.get("plan_sha256") != plan_sha:
        raise GateError("stale-plan")
    if receipt.get("findings_sha256") != _sha(findings_payload):
        raise GateError("stale-findings")
    source_bound = schema_version == 2
    if source_bound:
        source_payload = _regular_bytes(
            _artifact_path(
                run, "review-saturation", "r%s.json" % round_number
            ),
            MAX_JSON_BYTES,
        )
        if receipt.get("source_saturation_sha256") != _sha(source_payload):
            raise GateError("stale-source-saturation")
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
    return {
        "required": True,
        "source_bound": source_bound,
        "groups": len(groups),
        "classes": len(covered),
        "path": receipt_path,
        "payload": receipt_payload,
    }


def _plan_recovery_closeout(
    run, round_number, previous, plan_sha, scheduled_axes
):
    if (
        round_number != REVIEW_CLOSEOUT_ROUND
        or previous.get("schema_version") not in (2, 3)
        or previous.get("plan_sha256") == plan_sha
    ):
        return False
    previous_scheduled_axes = _axis_list(
        previous.get("scheduled_axes"), "source-scheduled-axes-invalid"
    )
    if previous_scheduled_axes != scheduled_axes:
        return False
    markers = _recovery(run)
    if not markers:
        return False
    latest = markers[-1]
    if (
        latest["source"] != round_number - 1
        or latest["before"] != previous.get("plan_sha256")
        or latest["after"] != plan_sha
    ):
        return False
    repair = _repair_details(run, round_number - 1)
    if not (repair["required"] and repair["source_bound"]):
        return False
    return saturation(
        run,
        round_number - 1,
        ",".join(previous_scheduled_axes),
        historical_plan_sha=previous.get("plan_sha256"),
    )


def _material_decision_pending(run, round_number):
    path = _artifact_path(run, "review-saturation", "r%s.json" % round_number)
    try:
        receipt = _json(path)
    except GateError as exc:
        if exc.reason == "missing-artifact":
            return []
        raise
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != 3
        or set(receipt) != SATURATION_V3_KEYS
        or receipt.get("round") != round_number
    ):
        return []
    classes = []
    for row in receipt.get("dispositions", []):
        if (
            isinstance(row, dict)
            and set(row) == DISPOSITION_V3_KEYS
            and row.get("outcome") == "material_decision"
            and isinstance(row.get("stable_class"), str)
        ):
            classes.append(row["stable_class"])
    return sorted(set(classes))


def repair(run, round_number):
    if _round(round_number) >= REVIEW_CLOSEOUT_ROUND:
        raise GateError("review-limit-reached")
    pending = _material_decision_pending(_run_dir(run), _round(round_number))
    if pending:
        return _emit(
            "CLOSED",
            "material-decision-required",
            "classes=%s" % ",".join(pending),
            blockers=len(pending),
        )
    details = _repair_details(run, round_number)
    if not details["required"]:
        return _emit("OPEN", "not-required", "no material findings")
    return _emit(
        "OPEN",
        "repair-ready",
        "groups=%s classes=%s" % (details["groups"], details["classes"]),
    )


def _validate_delta(run, round_number, scheduled_axes, rerun_axes, spec=None):
    run = _run_dir(run)
    round_number = _round(round_number)
    if round_number > REVIEW_CLOSEOUT_ROUND:
        raise GateError("review-limit-reached")
    if round_number == 1:
        raise GateError("selective-review-first-round")
    scheduled_axes = _axis_list(scheduled_axes, "scheduled-axes-invalid")
    rerun_axes = _axis_list(rerun_axes, "rerun-axes-invalid")
    if not set(rerun_axes).issubset(scheduled_axes):
        raise GateError("rerun-axes-invalid")
    delta_path = _artifact_path(
        run, "review-deltas", "r%s.json" % round_number
    )
    delta_payload = _regular_bytes(delta_path, MAX_JSON_BYTES)
    if spec is not None:
        match = DELTA_SPEC_RE.fullmatch(spec) if isinstance(spec, str) else None
        expected_relative = "review-deltas/r%s.json" % round_number
        if (
            match is None
            or match.group(1) != expected_relative
            or match.group(2) != _sha(delta_payload)
        ):
            raise GateError("delta-receipt-mismatch")
    try:
        receipt = json.loads(delta_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("delta-malformed") from exc
    if not isinstance(receipt, dict) or set(receipt) != DELTA_KEYS:
        raise GateError("delta-malformed")
    source_round = round_number - 1
    if (
        receipt.get("schema_version") != 1
        or receipt.get("source_round") != source_round
        or receipt.get("round") != round_number
    ):
        raise GateError("delta-malformed")
    _, plan_sha = _plan(run)
    if receipt.get("plan_sha256") != plan_sha:
        raise GateError("stale-plan")
    source_saturation_path = _artifact_path(
        run, "review-saturation", "r%s.json" % source_round
    )
    source_saturation_payload = _regular_bytes(
        source_saturation_path, MAX_JSON_BYTES
    )
    if receipt.get("source_saturation_sha256") != _sha(
        source_saturation_payload
    ):
        raise GateError("stale-source-saturation")
    try:
        source_saturation = json.loads(source_saturation_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GateError("source-saturation-malformed") from exc
    source_schema = (
        source_saturation.get("schema_version")
        if isinstance(source_saturation, dict)
        else None
    )
    if (
        not isinstance(source_saturation, dict)
        or source_schema not in (2, 3)
        or set(source_saturation) != _saturation_keys(source_schema)
        or source_saturation.get("round") != source_round
        or source_saturation.get("plan_sha256") != plan_sha
    ):
        raise GateError("source-saturation-malformed")
    source_scheduled = _axis_list(
        source_saturation.get("scheduled_axes"), "source-scheduled-axes-invalid"
    )
    if source_scheduled != scheduled_axes:
        raise GateError("scheduled-axis-drift")
    source_files = _review_files(source_saturation.get("review_files"))

    repair_details = _repair_details(run, source_round)
    if not repair_details["required"]:
        raise GateError("repair-not-required")
    if receipt.get("repair_sha256") != _sha(repair_details["payload"]):
        raise GateError("stale-repair")

    base = source_saturation.get("review_base_sha")
    current_basis = _review_basis(run, base, details=True)
    current_files = _review_files(current_basis["review_files"])
    if receipt.get("review_files") != current_files:
        raise GateError("delta-snapshot-mismatch")
    source_by_path = {row["path"]: row for row in source_files}
    current_by_path = {row["path"]: row for row in current_files}
    if set(source_by_path) != set(current_by_path):
        raise GateError("delta-path-drift")
    changed_paths = []
    for path in sorted(source_by_path):
        before = source_by_path[path]
        after = current_by_path[path]
        if (
            (before["worktree_sha256"] is None)
            != (after["worktree_sha256"] is None)
            or before["mode"] != after["mode"]
        ):
            raise GateError("delta-boundary-change", path)
        if before["worktree_sha256"] != after["worktree_sha256"]:
            changed_paths.append(path)
    if not changed_paths:
        raise GateError("delta-empty")
    if len(changed_paths) > MAX_SELECTIVE_CHANGED_PATHS:
        raise GateError("delta-too-broad", str(len(changed_paths)))
    if receipt.get("changed_paths") != changed_paths:
        raise GateError("changed-paths-mismatch")

    carried_axes = receipt.get("carried_axes")
    if (
        receipt.get("scheduled_axes") != scheduled_axes
        or receipt.get("rerun_axes") != rerun_axes
        or not isinstance(carried_axes, list)
        or carried_axes != [
            axis for axis in scheduled_axes if axis not in set(rerun_axes)
        ]
        or not carried_axes
        or sorted(rerun_axes + carried_axes) != sorted(scheduled_axes)
    ):
        raise GateError("axis-partition-invalid")
    required_axes = _required_delta_axes(changed_paths, scheduled_axes)
    missing_required = sorted(required_axes - set(rerun_axes))
    if missing_required:
        raise GateError("required-axis-missing", missing_required[0])

    route_digest = receipt.get("route_receipt_sha256")
    if route_digest is not None:
        if not isinstance(route_digest, str) or SHA_RE.fullmatch(route_digest) is None:
            raise GateError("review-cascade-route-invalid")
        try:
            root, _ = build_replan._root_for(run)
            route, route_path = adaptive_control.verify_review_cascade_route(
                root, run, "routine"
            )
        except (
            adaptive_control.AdaptiveControlError,
            build_replan.BuildReplanError,
        ) as exc:
            raise GateError("review-cascade-route-invalid", str(exc)) from exc
        route_payload = _regular_bytes(route_path, MAX_JSON_BYTES)
        if (
            route.get("route") != "selective"
            or route.get("stage") != "active"
            or route.get("audit_sample")
            or route_digest != _sha(route_payload)
        ):
            raise GateError("review-cascade-route-inactive")
    return {
        "changed_paths": changed_paths,
        "rerun_axes": rerun_axes,
        "carried_axes": carried_axes,
        "digest": _sha(delta_payload),
    }


def delta(run, round_number, scheduled_axes, rerun_axes):
    run = _run_dir(run)
    details = _validate_delta(
        run,
        round_number,
        _axes(scheduled_axes),
        _axes(rerun_axes),
    )
    return _emit(
        "OPEN",
        "selective-review-ready",
        "changed=%s rerun=%s carried=%s"
        % (
            len(details["changed_paths"]),
            len(details["rerun_axes"]),
            len(details["carried_axes"]),
        ),
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


def _schema3_failure_classes(run, source_round):
    path = _artifact_path(run, "review-saturation", "r%s.json" % source_round)
    try:
        receipt = _json(path)
    except GateError as exc:
        if exc.reason == "missing-artifact":
            return None
        raise
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema_version") != 3
        or set(receipt) != SATURATION_V3_KEYS
        or receipt.get("round") != source_round
    ):
        return None
    _, _, aggregate = _aggregate(run, source_round)
    return set(aggregate)


def _prior_class_trajectories(run, markers, source_round, stable_class):
    payloads = []
    semantics = []
    for row in markers:
        prior_source = row["source"]
        if prior_source >= source_round:
            continue
        path = _artifact_path(
            run, "review-trajectories", "source-r%s.json" % prior_source
        )
        try:
            payload = _regular_bytes(path, MAX_JSON_BYTES)
        except GateError as exc:
            if exc.reason == "missing-artifact":
                continue
            raise
        try:
            prior = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise GateError("trajectory-malformed", path) from exc
        if not isinstance(prior, dict):
            raise GateError("trajectory-malformed", path)
        if prior.get("schema_version") != 2:
            continue
        if (
            set(prior) != TRAJECTORY_V2_KEYS
            or prior.get("source_round") != prior_source
        ):
            raise GateError("trajectory-malformed", path)
        if prior.get("stable_class") != stable_class:
            continue
        payloads.append(_sha(payload))
        semantics.append(_trajectory_semantics(prior)[0])
    return payloads, semantics


def _class_scoped_preflight(run, round_number, markers, latest, common_classes):
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
    if (
        not isinstance(receipt, dict)
        or set(receipt) != TRAJECTORY_V2_KEYS
        or receipt.get("schema_version") != 2
        or receipt.get("source_round") != source_round
    ):
        raise GateError("trajectory-malformed")
    stable_class = receipt.get("stable_class")
    if stable_class not in common_classes:
        raise GateError("trajectory-class-mismatch", str(stable_class))
    plan_payload, plan_sha = _plan(run)
    if receipt.get("plan_sha256") != plan_sha:
        raise GateError("stale-plan")
    failed_rounds = [row["source"] for row in latest]
    if receipt.get("failed_source_rounds") != failed_rounds:
        raise GateError("trajectory-receipts-mismatch")
    receipt_hashes = [_sha(row["line"].encode("utf-8")) for row in latest]
    if receipt.get("recovery_receipt_sha256s") != receipt_hashes:
        raise GateError("trajectory-receipts-mismatch")
    prior_payloads, prior_semantics = _prior_class_trajectories(
        run, markers, source_round, stable_class
    )
    if receipt.get("prior_trajectory_sha256s") != prior_payloads:
        raise GateError("trajectory-receipts-mismatch")
    semantics, hypothesis, assumption, checks = _trajectory_semantics(receipt)
    if semantics in prior_semantics:
        raise GateError("trajectory-repeated")
    current_strategy = (semantics[0], semantics[3])
    if prior_semantics and current_strategy == (
        prior_semantics[-1][0],
        prior_semantics[-1][3],
    ):
        raise GateError("trajectory-strategy-unchanged", stable_class)
    try:
        plan_text = plan_payload.decode("utf-8")
    except UnicodeError as exc:
        raise GateError("trajectory-plan-mismatch") from exc
    required_lines = {
        "Trajectory class: %s" % stable_class,
        "Trajectory action: %s" % receipt["action"],
        "Trajectory hypothesis: %s" % hypothesis,
        "Changed assumption: %s" % assumption,
    }
    required_lines.update("Trajectory check: %s :: %s" % check for check in checks)
    if not required_lines.issubset(set(plan_text.splitlines())):
        raise GateError("trajectory-plan-mismatch")
    return _emit(
        "OPEN",
        "trajectory-ready",
        "source_round=%s class=%s action=%s"
        % (source_round, stable_class, receipt["action"]),
    )


def preflight(run, round_number):
    run = _run_dir(run)
    round_number = _round(round_number)
    if round_number > REVIEW_CLOSEOUT_ROUND:
        raise GateError("review-limit-reached")
    markers = _recovery(run)
    if len(markers) < 2:
        return _emit("OPEN", "below-threshold", "failed_strategies=%s" % len(markers))
    latest = markers[-2:]
    latest_classes = [
        _schema3_failure_classes(run, row["source"]) for row in latest
    ]
    if all(classes is not None for classes in latest_classes):
        common_classes = set.intersection(*latest_classes)
        if not common_classes:
            return _emit(
                "OPEN",
                "below-threshold",
                "failed_strategies=class-scoped",
            )
        return _class_scoped_preflight(
            run, round_number, markers, latest, common_classes
        )
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
    basis_parser.add_argument("--details", action="store_true")
    saturation_parser = subparsers.add_parser("saturation")
    saturation_parser.add_argument("--run", required=True)
    saturation_parser.add_argument("--round", required=True, type=int)
    saturation_parser.add_argument("--axes", required=True)
    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--run", required=True)
    repair_parser.add_argument("--round", required=True, type=int)
    delta_parser = subparsers.add_parser("delta")
    delta_parser.add_argument("--run", required=True)
    delta_parser.add_argument("--round", required=True, type=int)
    delta_parser.add_argument("--scheduled-axes", required=True)
    delta_parser.add_argument("--rerun-axes", required=True)
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--run", required=True)
    preflight_parser.add_argument("--round", required=True, type=int)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "basis":
            return basis(args.run, args.base, args.details)
        if args.command == "saturation":
            return saturation(args.run, args.round, args.axes)
        if args.command == "repair":
            return repair(args.run, args.round)
        if args.command == "delta":
            return delta(
                args.run,
                args.round,
                args.scheduled_axes,
                args.rerun_axes,
            )
        return preflight(args.run, args.round)
    except GateError as exc:
        return _emit("CLOSED", exc.reason, exc.detail)


if __name__ == "__main__":
    raise SystemExit(main())
