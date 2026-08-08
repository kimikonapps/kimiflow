"""Mechanical breadth and convergence checks for three-round plan review."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_TEXT_BYTES = 1024 * 1024
MAX_JSON_BYTES = 256 * 1024
MAX_DIMENSIONS = 12
MAX_CASES = 128
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
CLASS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
AC_RE = re.compile(r"^AC-[1-9][0-9]*$")
EVIDENCE_RE = re.compile(r"^(review-evidence/[A-Za-z0-9._/-]+)@([a-f0-9]{64})$")
BASIS_RE = re.compile(r"^BASIS plan_sha256=([a-f0-9]{64})$")
COVERAGE_RE = re.compile(
    r"^COVERAGE (?P<domain>[a-z][a-z0-9-]*) :: "
    r"status=(?P<status>checked|not_applicable) :: evidence=(?P<evidence>.+)$"
)
CANDIDATE_RE = re.compile(
    r"^CANDIDATE (?P<severity>BLOCKER|HIGH|MEDIUM|LOW) "
    r"(?P<ref>.+?) :: (?P<claim>.+?) :: "
    r"family=(?P<family>[a-z][a-z0-9-]{0,63}) :: "
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
REVIEW_EVIDENCE_RE = re.compile(
    r"^REVIEW_EVIDENCE class=(?P<class>[a-z0-9][a-z0-9-]{0,63}) :: "
    r"verify=(?P<verify>(?:command|verifier):[^\x00-\x1f\x7f]+) :: "
    r"outcome=(?P<outcome>reproduced|not_reproduced) :: (?P<detail>\S.*)$"
)
PLAN_SATURATION_MARKER_RE = re.compile(
    r"^<!-- kimiflow:plan-saturation round=(?P<round>[1-3]) "
    r"receipt=(?P<receipt>[a-f0-9]{64}) plan=(?P<plan>[a-f0-9]{64}) "
    r"previous=(?P<previous>none|[a-f0-9]{64}) -->$"
)
PLAN_STRATEGY_RE = re.compile(
    r"^<!-- kimiflow:strategy gate=plan epoch-start=[1-9][0-9]* "
    r"fingerprint=(?P<plan>[a-f0-9]{64}) -->$"
)
PLAN_RECOVERY_RE = re.compile(
    r"^<!-- kimiflow:recovery gate=plan .+ after=(?P<plan>[a-f0-9]{64}) -->$"
)

REQUIRED_DOMAINS = {
    "intent-trace",
    "state-space",
    "identity-binding",
    "evidence-safety",
    "time-lifecycle",
    "aggregation-verdict",
    "scope-subtraction",
}
REQUIRED_FAMILY_CHECKS = {
    "upstream-requirements",
    "sibling-states",
    "downstream-outputs",
    "boundary-values",
    "failure-classification",
}
MATRIX_KEYS = {"schema_version", "plan_sha256", "dimensions", "invariants", "cases"}
DIMENSION_KEYS = {"name", "values"}
INVARIANT_KEYS = {"id", "acceptance", "statement", "accept_case", "reject_case"}
CASE_KEYS = {"id", "kind", "values", "classification", "acceptance", "expected"}
SATURATION_KEYS = {
    "schema_version",
    "round",
    "plan_sha256",
    "lenses",
    "candidate_files",
    "finding_files",
    "dispositions",
    "families",
}
FILE_KEYS = {"lens", "sha256"}
DISPOSITION_KEYS = {"candidate_id", "outcome", "stable_class", "evidence"}
FAMILY_KEYS = {"id", "classes", "root_cause", "checks"}
CHECK_KEYS = {"status", "evidence"}


@dataclass(frozen=True)
class GateResult:
    is_open: bool
    reason: str
    detail: str = ""


class ValidationError(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _closed(reason: str, detail: str = "") -> GateResult:
    return GateResult(False, reason, detail)


def _digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_regular(run: Path, relative: str, limit: int) -> bytes:
    if not relative or relative.startswith("/") or "\\" in relative:
        raise ValidationError("unsafe-artifact", relative)
    parts = Path(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValidationError("unsafe-artifact", relative)
    current = run
    for part in parts[:-1]:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise ValidationError("artifact-missing", relative) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValidationError("unsafe-artifact", relative)
    path = run.joinpath(*parts)
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ValidationError("artifact-missing", relative) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValidationError("unsafe-artifact", relative)
    if info.st_size > limit:
        raise ValidationError("artifact-too-large", relative)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValidationError("artifact-unreadable", relative) from exc


def _load_json(run: Path, relative: str) -> dict:
    payload = _read_regular(run, relative, MAX_JSON_BYTES)

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValidationError("duplicate-json-key", key)
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except ValidationError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise ValidationError("invalid-json", relative) from exc
    if not isinstance(value, dict):
        raise ValidationError("invalid-json-root", relative)
    return value


def _exact_keys(value: dict, expected: set[str], reason: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValidationError(reason, f"missing={missing},extra={extra}")


def _nonempty_text(value: object, *, maximum: int = 1000) -> bool:
    return isinstance(value, str) and 1 <= len(value.strip()) <= maximum and not any(
        ord(char) < 32 and char not in "\t" for char in value
    )


def _plan_digest(run: Path) -> str:
    return _digest_bytes(_read_regular(run, "PLAN.md", MAX_TEXT_BYTES))


def _markdown_lines(payload: bytes, artifact: str) -> list[str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise ValidationError("artifact-encoding-invalid", artifact) from exc
    lines: list[str] = []
    fence_char = ""
    fence_len = 0
    html_comment = False
    for line in text.splitlines():
        cleaned = "" if html_comment else line
        rest = line
        chunks: list[str] = []
        while rest:
            if html_comment:
                end = rest.find("-->")
                if end < 0:
                    rest = ""
                    break
                html_comment = False
                rest = rest[end + 3 :]
                continue
            start = rest.find("<!--")
            if start < 0:
                chunks.append(rest)
                break
            chunks.append(rest[:start])
            rest = rest[start + 4 :]
            html_comment = True
        cleaned = "".join(chunks)
        if not fence_char and re.match(r"^(?: {4}|\t)", cleaned):
            continue
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", cleaned)
        if match:
            marker = match.group(1)
            if not fence_char:
                fence_char = marker[0]
                fence_len = len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_len and not cleaned[match.end() :].strip():
                fence_char = ""
                fence_len = 0
                continue
        if not fence_char:
            lines.append(cleaned)
    return lines


def _declared_acceptance_criteria(run: Path) -> set[str]:
    lines = _markdown_lines(
        _read_regular(run, "ACCEPTANCE.md", MAX_TEXT_BYTES), "ACCEPTANCE.md"
    )
    declared: set[str] = set()
    for index, line in enumerate(lines):
        plain = line.replace("**", "")
        heading = re.fullmatch(
            r"\s{0,3}#{1,6}\s+(AC-[0-9]+)(?:\s*(?:[-–—]{1,2}|:)\s+(\S.*))?\s*#*\s*",
            plain,
        )
        if heading:
            ident, inline = heading.groups()
            if inline:
                declared.add(ident)
                continue
            for candidate in lines[index + 1 :]:
                candidate_plain = candidate.replace("**", "").strip()
                if re.match(r"^#{1,6}\s+", candidate_plain):
                    break
                if not candidate_plain:
                    continue
                if re.match(r"^AC-[0-9]+(?:\s|:|->)", candidate_plain):
                    break
                if re.match(
                    r"^(?:verification|trace|example|check|evidence|test|mapping)\s*:",
                    candidate_plain,
                    flags=re.IGNORECASE,
                ):
                    continue
                declared.add(ident)
                break
            continue
        direct = re.match(
            r"^\s{0,3}(?:[-*+]\s+)?(AC-[0-9]+)(?:\s*:\s*\S|\s+[-–—]{1,2}\s+\S|\s*->\s*[A-Za-z0-9_.-]+\s*:\s*\S)",
            plain,
        )
        if direct:
            declared.add(direct.group(1))
    if not declared:
        raise ValidationError("acceptance-criteria-missing")
    return declared


def _state_values(run: Path, label: str) -> list[str]:
    lines = _markdown_lines(_read_regular(run, "STATE.md", MAX_TEXT_BYTES), "STATE.md")
    wanted = label.lower()
    values: list[str] = []
    for raw in lines:
        line = raw.replace("\r", "").replace("**", "")
        line = re.sub(r"^\s*-\s*", "", line)
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == wanted:
            values.append(value.strip())
    return values


def _expected_lenses(run: Path) -> tuple[str, ...]:
    contract = _state_values(run, "Plan review contract")
    profile = _state_values(run, "Plan review profile")
    mode = _state_values(run, "Mode")
    scope = _state_values(run, "Scope")
    if contract != ["1"]:
        raise ValidationError("plan-review-contract-invalid")
    if len(profile) != 1 or profile[0].lower() not in {"standard", "contract"}:
        raise ValidationError("plan-review-profile-invalid")
    if len(mode) != 1 or len(scope) != 1:
        raise ValidationError("plan-review-topology-state-invalid")
    if profile[0].lower() == "contract" or mode[0].lower() == "audit" or scope[0].lower() == "large":
        return ("a", "b", "c")
    if scope[0].lower() != "small":
        raise ValidationError("plan-review-scope-invalid", scope[0])
    return ("a", "b")


def validate_matrix(run: Path) -> GateResult:
    run = Path(run)
    try:
        value = _load_json(run, "CONTRACT-MATRIX.json")
        _exact_keys(value, MATRIX_KEYS, "matrix-shape-invalid")
        if value["schema_version"] != 1:
            raise ValidationError("matrix-schema-invalid")
        if value["plan_sha256"] != _plan_digest(run):
            raise ValidationError("stale-plan")
        declared_acceptance = _declared_acceptance_criteria(run)

        dimensions = value["dimensions"]
        if not isinstance(dimensions, list) or not 1 <= len(dimensions) <= MAX_DIMENSIONS:
            raise ValidationError("dimension-count-invalid")
        dimension_values: dict[str, tuple[str, ...]] = {}
        for row in dimensions:
            if not isinstance(row, dict):
                raise ValidationError("dimension-shape-invalid")
            _exact_keys(row, DIMENSION_KEYS, "dimension-shape-invalid")
            name = row["name"]
            values = row["values"]
            if not isinstance(name, str) or not SLUG_RE.fullmatch(name) or name in dimension_values:
                raise ValidationError("dimension-name-invalid", str(name))
            if (
                not isinstance(values, list)
                or not 2 <= len(values) <= 8
                or any(not _nonempty_text(item, maximum=80) for item in values)
                or len(set(values)) != len(values)
            ):
                raise ValidationError("dimension-values-invalid", name)
            dimension_values[name] = tuple(values)

        cases = value["cases"]
        if not isinstance(cases, list) or not 3 <= len(cases) <= MAX_CASES:
            raise ValidationError("case-count-invalid")
        case_by_id: dict[str, dict] = {}
        kinds: set[str] = set()
        for row in cases:
            if not isinstance(row, dict):
                raise ValidationError("case-shape-invalid")
            _exact_keys(row, CASE_KEYS, "case-shape-invalid")
            case_id = row["id"]
            if not isinstance(case_id, str) or not SLUG_RE.fullmatch(case_id) or case_id in case_by_id:
                raise ValidationError("case-id-invalid", str(case_id))
            if row["kind"] not in {"legal", "invalid", "boundary"}:
                raise ValidationError("case-kind-invalid", case_id)
            kinds.add(row["kind"])
            assignment = row["values"]
            if not isinstance(assignment, dict) or set(assignment) != set(dimension_values):
                raise ValidationError("case-assignment-incomplete", case_id)
            for name, selected in assignment.items():
                if selected not in dimension_values[name]:
                    raise ValidationError("case-value-invalid", f"{case_id}:{name}")
            if row["classification"] not in {
                "eligible",
                "field-note",
                "invalid",
                "not-applicable",
            }:
                raise ValidationError("case-classification-invalid", case_id)
            acceptance = row["acceptance"]
            if (
                not isinstance(acceptance, list)
                or not acceptance
                or any(not isinstance(item, str) or not AC_RE.fullmatch(item) for item in acceptance)
                or len(set(acceptance)) != len(acceptance)
            ):
                raise ValidationError("case-acceptance-invalid", case_id)
            unknown_acceptance = set(acceptance) - declared_acceptance
            if unknown_acceptance:
                raise ValidationError(
                    "matrix-acceptance-unknown", sorted(unknown_acceptance)[0]
                )
            if not _nonempty_text(row["expected"]):
                raise ValidationError("case-expected-invalid", case_id)
            case_by_id[case_id] = row
        if kinds != {"legal", "invalid", "boundary"}:
            raise ValidationError("case-kinds-incomplete", ",".join(sorted(kinds)))

        for name, allowed_values in dimension_values.items():
            for allowed_value in allowed_values:
                if not any(row["values"][name] == allowed_value for row in cases):
                    raise ValidationError(
                        "dimension-value-uncovered", f"{name}={allowed_value}"
                    )

        for left, right in itertools.combinations(dimension_values, 2):
            for left_value, right_value in itertools.product(
                dimension_values[left], dimension_values[right]
            ):
                if not any(
                    row["values"][left] == left_value
                    and row["values"][right] == right_value
                    for row in cases
                ):
                    raise ValidationError(
                        "pairwise-coverage-missing",
                        f"{left}={left_value},{right}={right_value}",
                    )

        invariants = value["invariants"]
        if not isinstance(invariants, list) or not 1 <= len(invariants) <= 20:
            raise ValidationError("invariant-count-invalid")
        invariant_ids: set[str] = set()
        for row in invariants:
            if not isinstance(row, dict):
                raise ValidationError("invariant-shape-invalid")
            _exact_keys(row, INVARIANT_KEYS, "invariant-shape-invalid")
            invariant_id = row["id"]
            if (
                not isinstance(invariant_id, str)
                or not SLUG_RE.fullmatch(invariant_id)
                or invariant_id in invariant_ids
            ):
                raise ValidationError("invariant-id-invalid", str(invariant_id))
            invariant_ids.add(invariant_id)
            if not isinstance(row["acceptance"], str) or not AC_RE.fullmatch(row["acceptance"]):
                raise ValidationError("invariant-acceptance-invalid", invariant_id)
            if row["acceptance"] not in declared_acceptance:
                raise ValidationError(
                    "matrix-acceptance-unknown", row["acceptance"]
                )
            if not _nonempty_text(row["statement"]):
                raise ValidationError("invariant-statement-invalid", invariant_id)
            accept_case = case_by_id.get(row["accept_case"])
            reject_case = case_by_id.get(row["reject_case"])
            if accept_case is None or reject_case is None or accept_case is reject_case:
                raise ValidationError("invariant-cases-invalid", invariant_id)
            if accept_case["kind"] == "invalid" or reject_case["kind"] == "legal":
                raise ValidationError("invariant-case-polarity-invalid", invariant_id)
            if accept_case["values"] == reject_case["values"]:
                raise ValidationError("invariant-cases-identical", invariant_id)
            if (
                row["acceptance"] not in accept_case["acceptance"]
                or row["acceptance"] not in reject_case["acceptance"]
            ):
                raise ValidationError("invariant-case-acceptance-mismatch", invariant_id)
        return GateResult(True, "matrix-complete")
    except ValidationError as exc:
        return _closed(exc.reason, exc.detail)
    except (KeyError, TypeError) as exc:
        return _closed("matrix-shape-invalid", str(exc))


def _parse_candidate(payload: bytes, lens: str, plan_sha256: str) -> tuple[list[dict], set[str]]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValidationError("candidate-encoding-invalid", lens) from exc
    if not lines or len(lines) > 512:
        raise ValidationError("candidate-line-count-invalid", lens)
    basis = BASIS_RE.fullmatch(lines[0])
    if basis is None:
        raise ValidationError("candidate-basis-missing", lens)
    if basis.group(1) != plan_sha256:
        raise ValidationError("stale-candidate-basis", lens)
    candidates: list[dict] = []
    coverage: set[str] = set()
    saw_none = False
    for line in lines[1:]:
        match = COVERAGE_RE.fullmatch(line)
        if match:
            domain = match.group("domain")
            if domain not in REQUIRED_DOMAINS or domain in coverage:
                raise ValidationError("coverage-invalid", f"{lens}:{domain}")
            if not _nonempty_text(match.group("evidence"), maximum=500):
                raise ValidationError("coverage-evidence-invalid", f"{lens}:{domain}")
            coverage.add(domain)
            continue
        if line == "NONE":
            if saw_none or candidates:
                raise ValidationError("candidate-none-mixed", lens)
            saw_none = True
            continue
        candidate = CANDIDATE_RE.fullmatch(line)
        if candidate is None or saw_none:
            raise ValidationError("candidate-grammar-invalid", lens)
        row = candidate.groupdict()
        row["line"] = line
        row["candidate_id"] = "cand_" + hashlib.sha256(
            lens.encode("utf-8") + b"\0" + line.encode("utf-8")
        ).hexdigest()
        candidates.append(row)
    if not saw_none and not candidates:
        raise ValidationError("candidate-verdict-missing", lens)
    if not coverage:
        raise ValidationError("coverage-missing", lens)
    return candidates, coverage


def _parse_findings(payload: bytes, lens: str) -> tuple[dict[str, dict], dict[str, dict]]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValidationError("finding-encoding-invalid", lens) from exc
    if not lines or len(lines) > 512:
        raise ValidationError("finding-line-count-invalid", lens)
    findings: dict[str, dict] = {}
    resolved: dict[str, dict] = {}
    saw_none = False
    for line in lines:
        if line == "NONE":
            if saw_none or findings or resolved:
                raise ValidationError("finding-none-mixed", lens)
            saw_none = True
            continue
        match = FINDING_RE.fullmatch(line)
        if match:
            if match.group("severity") not in {"BLOCKER", "HIGH"}:
                raise ValidationError("nonmaterial-finding-forbidden", lens)
            if saw_none or match.group("class") in findings or match.group("class") in resolved:
                raise ValidationError("finding-class-duplicate", match.group("class"))
            findings[match.group("class")] = match.groupdict()
            continue
        match = RESOLVED_RE.fullmatch(line)
        if match:
            if saw_none or match.group("class") in findings or match.group("class") in resolved:
                raise ValidationError("finding-class-duplicate", match.group("class"))
            resolved[match.group("class")] = match.groupdict()
            continue
        raise ValidationError("finding-grammar-invalid", lens)
    return findings, resolved


def _candidate_evidence_class(candidate_id: str) -> str:
    return "candidate-" + candidate_id.removeprefix("cand_")[:54]


def _verify_evidence(
    run: Path,
    spec: object,
    *,
    expected_class: str,
    expected_verify: str,
    expected_outcome: str,
) -> None:
    if not isinstance(spec, str):
        raise ValidationError("evidence-invalid")
    match = EVIDENCE_RE.fullmatch(spec)
    if match is None or ".." in Path(match.group(1)).parts:
        raise ValidationError("evidence-invalid", spec)
    payload = _read_regular(run, match.group(1), MAX_TEXT_BYTES)
    if _digest_bytes(payload) != match.group(2):
        raise ValidationError("evidence-digest-mismatch", match.group(1))
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValidationError("evidence-encoding-invalid", match.group(1)) from exc
    if len(lines) != 1:
        raise ValidationError("evidence-receipt-invalid", match.group(1))
    receipt = REVIEW_EVIDENCE_RE.fullmatch(lines[0])
    if receipt is None:
        raise ValidationError("evidence-receipt-invalid", match.group(1))
    if receipt.group("class") != expected_class:
        raise ValidationError("evidence-class-mismatch", expected_class)
    if receipt.group("verify") != expected_verify:
        raise ValidationError("evidence-verify-mismatch", expected_class)
    if receipt.group("outcome") != expected_outcome:
        raise ValidationError("evidence-outcome-mismatch", expected_class)


def _validate_file_rows(
    run: Path,
    rows: object,
    lenses: tuple[str, ...],
    relative_for_lens,
    reason: str,
) -> dict[str, bytes]:
    if not isinstance(rows, list) or len(rows) != len(lenses):
        raise ValidationError(reason)
    result: dict[str, bytes] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValidationError(reason)
        _exact_keys(row, FILE_KEYS, reason)
        lens = row["lens"]
        if lens != lenses[index] or lens in result or not isinstance(row["sha256"], str):
            raise ValidationError(reason, str(lens))
        relative = relative_for_lens(lens)
        payload = _read_regular(run, relative, MAX_TEXT_BYTES)
        if not SHA_RE.fullmatch(row["sha256"]) or _digest_bytes(payload) != row["sha256"]:
            raise ValidationError(f"{reason}-digest", lens)
        result[lens] = payload
    return result


def _validate_saturation_receipt(
    run: Path,
    round_number: int,
    lenses: tuple[str, ...],
    *,
    current_plan: bool,
) -> dict[str, str]:
    relative = f"plan-review-saturation/r{round_number}.json"
    value = _load_json(run, relative)
    _exact_keys(value, SATURATION_KEYS, "saturation-shape-invalid")
    if value["schema_version"] != 1:
        raise ValidationError("saturation-schema-invalid")
    if value["round"] != round_number:
        raise ValidationError("saturation-round-mismatch")
    plan_sha256 = value["plan_sha256"]
    if not isinstance(plan_sha256, str) or not SHA_RE.fullmatch(plan_sha256):
        raise ValidationError("saturation-plan-digest-invalid")
    if current_plan and plan_sha256 != _plan_digest(run):
        raise ValidationError("stale-plan")
    if value["lenses"] != list(lenses):
        raise ValidationError("lenses-mismatch")

    candidate_payloads = _validate_file_rows(
        run,
        value["candidate_files"],
        lenses,
        lambda lens: f"plan-review-candidates/r{round_number}-{lens}.md",
        "candidate-files-invalid",
    )
    finding_payloads = _validate_file_rows(
        run,
        value["finding_files"],
        lenses,
        lambda lens: f"findings/r{round_number}-{lens}.md",
        "finding-files-invalid",
    )

    candidates: dict[str, dict] = {}
    coverage: set[str] = set()
    for lens in lenses:
        parsed, lens_coverage = _parse_candidate(candidate_payloads[lens], lens, plan_sha256)
        coverage.update(lens_coverage)
        for candidate in parsed:
            candidate["lens"] = lens
            candidates[candidate["candidate_id"]] = candidate
    if coverage != REQUIRED_DOMAINS:
        missing = ",".join(sorted(REQUIRED_DOMAINS - coverage))
        raise ValidationError("coverage-missing", missing)
    material_candidates = {
        candidate_id: row
        for candidate_id, row in candidates.items()
        if row["severity"] in {"BLOCKER", "HIGH"}
    }
    if round_number == 3 and material_candidates:
        raise ValidationError("closeout-new-candidate")

    findings: dict[str, dict] = {}
    resolved: dict[str, dict] = {}
    for lens in lenses:
        current_findings, current_resolved = _parse_findings(finding_payloads[lens], lens)
        for stable_class, row in current_findings.items():
            if stable_class in findings or stable_class in resolved:
                raise ValidationError("finding-class-duplicate", stable_class)
            row["lens"] = lens
            findings[stable_class] = row
        for stable_class, row in current_resolved.items():
            if stable_class in findings or stable_class in resolved:
                raise ValidationError("finding-class-duplicate", stable_class)
            row["lens"] = lens
            resolved[stable_class] = row
    for stable_class, row in findings.items():
        _verify_evidence(
            run,
            row["evidence"],
            expected_class=stable_class,
            expected_verify=row["verify"],
            expected_outcome="reproduced",
        )
    for stable_class, row in resolved.items():
        _verify_evidence(
            run,
            row["evidence"],
            expected_class=stable_class,
            expected_verify=row["verify"],
            expected_outcome="not_reproduced",
        )

    dispositions = value["dispositions"]
    if not isinstance(dispositions, list):
        raise ValidationError("dispositions-invalid")
    disposition_by_candidate: dict[str, dict] = {}
    promoted_classes: set[str] = set()
    for row in dispositions:
        if not isinstance(row, dict):
            raise ValidationError("disposition-shape-invalid")
        _exact_keys(row, DISPOSITION_KEYS, "disposition-shape-invalid")
        candidate_id = row["candidate_id"]
        if (
            not isinstance(candidate_id, str)
            or candidate_id not in material_candidates
            or candidate_id in disposition_by_candidate
        ):
            raise ValidationError("disposition-candidate-invalid", str(candidate_id))
        outcome = row["outcome"]
        if outcome not in {"promoted", "refuted", "non_blocking"}:
            raise ValidationError("disposition-outcome-invalid", candidate_id)
        candidate = material_candidates[candidate_id]
        if outcome == "promoted":
            stable_class = row["stable_class"]
            if (
                not isinstance(stable_class, str)
                or not CLASS_RE.fullmatch(stable_class)
                or stable_class not in findings
            ):
                raise ValidationError("promoted-class-invalid", str(stable_class))
            finding = findings[stable_class]
            if row["evidence"] != finding["evidence"]:
                raise ValidationError("promoted-evidence-mismatch", stable_class)
            if candidate["verify"] != finding["verify"]:
                raise ValidationError("promoted-verify-mismatch", stable_class)
            evidence_class = stable_class
            evidence_outcome = "reproduced"
            promoted_classes.add(stable_class)
        else:
            if row["stable_class"] is not None:
                raise ValidationError("nonpromoted-class-forbidden", candidate_id)
            evidence_class = _candidate_evidence_class(candidate_id)
            evidence_outcome = "not_reproduced" if outcome == "refuted" else "reproduced"
        _verify_evidence(
            run,
            row["evidence"],
            expected_class=evidence_class,
            expected_verify=candidate["verify"],
            expected_outcome=evidence_outcome,
        )
        disposition_by_candidate[candidate_id] = row
    if set(disposition_by_candidate) != set(material_candidates):
        raise ValidationError("undisposed-candidate")
    if promoted_classes != set(findings):
        raise ValidationError("aggregate-findings-mismatch")

    families = value["families"]
    if not isinstance(families, list):
        raise ValidationError("families-invalid")
    class_to_family: dict[str, str] = {}
    family_ids: set[str] = set()
    for row in families:
        if not isinstance(row, dict):
            raise ValidationError("family-shape-invalid")
        _exact_keys(row, FAMILY_KEYS, "family-shape-invalid")
        family_id = row["id"]
        if (
            not isinstance(family_id, str)
            or not SLUG_RE.fullmatch(family_id)
            or family_id in family_ids
        ):
            raise ValidationError("family-id-invalid", str(family_id))
        family_ids.add(family_id)
        classes = row["classes"]
        if (
            not isinstance(classes, list)
            or not classes
            or any(not isinstance(item, str) or not CLASS_RE.fullmatch(item) for item in classes)
            or len(set(classes)) != len(classes)
        ):
            raise ValidationError("family-classes-invalid", family_id)
        if not _nonempty_text(row["root_cause"], maximum=1000):
            raise ValidationError("family-root-cause-invalid", family_id)
        checks = row["checks"]
        if not isinstance(checks, dict) or set(checks) != REQUIRED_FAMILY_CHECKS:
            raise ValidationError("family-checks-incomplete", family_id)
        for check_name, check in checks.items():
            if not isinstance(check, dict):
                raise ValidationError("family-check-invalid", f"{family_id}:{check_name}")
            _exact_keys(check, CHECK_KEYS, "family-check-invalid")
            if check["status"] not in {"checked", "not_applicable"} or not _nonempty_text(
                check["evidence"], maximum=500
            ):
                raise ValidationError("family-check-invalid", f"{family_id}:{check_name}")
        for stable_class in classes:
            if stable_class in class_to_family:
                raise ValidationError("family-class-duplicate", stable_class)
            class_to_family[stable_class] = family_id
    current_classes = set(findings) | set(resolved)
    if set(class_to_family) != current_classes:
        raise ValidationError("family-class-mismatch")
    for candidate_id, disposition in disposition_by_candidate.items():
        if disposition["outcome"] != "promoted":
            continue
        stable_class = disposition["stable_class"]
        if class_to_family.get(stable_class) != material_candidates[candidate_id]["family"]:
            raise ValidationError("candidate-family-mismatch", stable_class)
    return class_to_family


def _validate_plan_saturation_ledger(
    run: Path, round_number: int, *, require_active_pin: bool
) -> list[dict[str, object]]:
    payload = _read_regular(run, "RECOVERY.md", MAX_TEXT_BYTES)
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValidationError("plan-saturation-ledger-encoding") from exc
    markers: list[dict[str, str]] = []
    plan_basis_hashes: set[str] = set()
    for line in lines:
        marker = PLAN_SATURATION_MARKER_RE.fullmatch(line)
        if marker:
            markers.append(marker.groupdict())
            continue
        if line.startswith("<!-- kimiflow:plan-saturation"):
            raise ValidationError("plan-saturation-ledger-marker-invalid")
        strategy = PLAN_STRATEGY_RE.fullmatch(line)
        if strategy:
            plan_basis_hashes.add(strategy.group("plan"))
            continue
        recovery = PLAN_RECOVERY_RE.fullmatch(line)
        if recovery:
            plan_basis_hashes.add(recovery.group("plan"))
    expected_rounds = [str(number) for number in range(1, round_number + 1)]
    if [marker["round"] for marker in markers] != expected_rounds:
        raise ValidationError("plan-saturation-ledger-sequence-invalid")
    previous_receipt = "none"
    expected_pins: list[dict[str, object]] = []
    for marker in markers:
        number = int(marker["round"])
        relative = f"plan-review-saturation/r{number}.json"
        receipt_payload = _read_regular(run, relative, MAX_JSON_BYTES)
        receipt_digest = _digest_bytes(receipt_payload)
        if marker["receipt"] != receipt_digest:
            raise ValidationError("plan-saturation-ledger-digest", f"r{number}")
        receipt = _load_json(run, relative)
        if marker["plan"] != receipt.get("plan_sha256"):
            raise ValidationError("plan-saturation-ledger-plan", f"r{number}")
        if marker["plan"] not in plan_basis_hashes:
            raise ValidationError("plan-saturation-ledger-basis", f"r{number}")
        if marker["previous"] != previous_receipt:
            raise ValidationError("plan-saturation-ledger-chain", f"r{number}")
        expected_pins.append(
            {
                "round": number,
                "receipt_sha256": receipt_digest,
                "plan_sha256": marker["plan"],
                "previous_sha256": marker["previous"],
            }
        )
        previous_receipt = receipt_digest
    if require_active_pin:
        if run.parent.name != ".kimiflow":
            raise ValidationError("plan-saturation-active-run-root-invalid")
        root = run.parent.parent
        active = _load_json(root, ".kimiflow/session/ACTIVE_RUN.json")
        try:
            run_relative = run.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValidationError("plan-saturation-active-run-root-invalid") from exc
        if active.get("run") != run_relative or active.get("status") != "active":
            raise ValidationError("plan-saturation-active-run-mismatch")
        if active.get("plan_saturation_receipts") != expected_pins:
            raise ValidationError("plan-saturation-active-pin-mismatch")
    return expected_pins


def validate_saturation(
    run: Path,
    round_number: int,
    lenses: tuple[str, ...],
    *,
    require_active_pin: bool = True,
) -> GateResult:
    run = Path(run)
    try:
        if round_number not in (1, 2, 3):
            raise ValidationError("review-limit-reached", str(round_number))
        expected_lenses = _expected_lenses(run)
        if lenses != expected_lenses:
            raise ValidationError(
                "review-topology-mismatch",
                f"expected={','.join(expected_lenses)},actual={','.join(lenses)}",
            )
        _validate_plan_saturation_ledger(
            run, round_number, require_active_pin=require_active_pin
        )
        prior_families: dict[str, str] = {}
        for historical_round in range(1, round_number + 1):
            try:
                current_families = _validate_saturation_receipt(
                    run,
                    historical_round,
                    lenses,
                    current_plan=historical_round == round_number,
                )
            except ValidationError as exc:
                if historical_round < round_number:
                    raise ValidationError(
                        "historical-saturation-invalid",
                        f"r{historical_round}:{exc.reason}:{exc.detail}",
                    ) from exc
                raise
            for stable_class in set(prior_families) & set(current_families):
                if prior_families[stable_class] != current_families[stable_class]:
                    raise ValidationError("family-identity-drift", stable_class)
            prior_families = current_families
        return GateResult(True, "saturated")
    except ValidationError as exc:
        return _closed(exc.reason, exc.detail)
    except (KeyError, TypeError) as exc:
        return _closed("saturation-shape-invalid", str(exc))


def prepare_saturation_pins(
    run: Path, round_number: int, lenses: tuple[str, ...]
) -> tuple[GateResult, list[dict[str, object]] | None]:
    result = validate_saturation(
        run, round_number, lenses, require_active_pin=False
    )
    if not result.is_open:
        return result, None
    try:
        pins = _validate_plan_saturation_ledger(
            Path(run), round_number, require_active_pin=False
        )
    except ValidationError as exc:
        return _closed(exc.reason, exc.detail), None
    return result, pins


def _emit(result: GateResult) -> int:
    status = "OPEN" if result.is_open else "CLOSED"
    print(
        "PLAN_REVIEW_GATE\t%s\treason=%s\tdetail=%s"
        % (status, result.reason, result.detail)
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    matrix = subparsers.add_parser("matrix")
    matrix.add_argument("--run", required=True)
    saturation = subparsers.add_parser("saturation")
    saturation.add_argument("--run", required=True)
    saturation.add_argument("--round", required=True, type=int)
    saturation.add_argument("--expect", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "matrix":
        return _emit(validate_matrix(Path(args.run)))
    lenses = tuple(part for part in args.expect.split(",") if part)
    return _emit(validate_saturation(Path(args.run), args.round, lenses))


if __name__ == "__main__":
    raise SystemExit(main())
