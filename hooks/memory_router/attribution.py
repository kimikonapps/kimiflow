"""Deterministic, privacy-bounded attribution from selected recall hits to outcomes."""
import hashlib
import json
import os
import re
import stat

from . import rows

CONTRACT = 1
SOURCE_ORDER = ("facts", "learnings", "strategies", "history", "index")
_ID_RE = re.compile(r"^rec_[0-9a-f]{64}$")
_MARKER = "<!-- kimiflow:recall-attribution contract=1 -->"
_MARKER_TOKEN = "kimiflow:recall-attribution"
_APPLIED_RE = re.compile(r"^Applied recall IDs: (.+)$")
_DECISION_RE = re.compile(r"^Decision D([1-9][0-9]*): .+$")
_RECALL_RE = re.compile(r"^Recall D([1-9][0-9]*): (.+)$")
_CHECK_RE = re.compile(r"^Decision check D([1-9][0-9]*): (passed|failed) :: .+$")
_CONTRADICTION_RE = re.compile(
    r"^Recall contradiction (rec_[0-9a-f]{64}): ([^:]+):([1-9][0-9]*)$"
)
_VERIFY_RE = re.compile(
    r"^<!-- kimiflow:verification outcome=(passed|failed) "
    r"criteria=(passed|failed|not_run) regression=(passed|failed|not_run) -->$"
)


class AttributionError(ValueError):
    """The active recall-attribution contract is malformed or ungrounded."""


def _without_recall_id(hit):
    if not isinstance(hit, dict):
        raise AttributionError("recall hit must be an object")
    return {key: value for key, value in hit.items() if key != "recall_id"}


def recall_id(source, reference, hit):
    """Seal source, evidence identity, and canonical final-hit content into one ID."""
    if source not in SOURCE_ORDER or not isinstance(reference, str):
        raise AttributionError("recall identity source or reference is invalid")
    try:
        basis = json.dumps(
            {
                "source": source,
                "reference": reference,
                "hit": _without_recall_id(hit),
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AttributionError("recall hit is not canonical JSON") from exc
    return "rec_" + hashlib.sha256(basis).hexdigest()


def hit_reference(hit):
    if not isinstance(hit, dict):
        return ""
    value = hit.get("ref")
    if value not in (None, False, ""):
        return str(value)
    evidence = hit.get("evidence")
    if isinstance(evidence, list) and evidence and evidence[0] not in (None, False, ""):
        return str(evidence[0])
    value = hit.get("path")
    if value not in (None, False, ""):
        return "%s:%s" % (value, hit.get("line") or 1)
    value = hit.get("id")
    return "" if value in (None, False, "") else str(value)


def attach_ids(selected):
    """Copy final packed hits and add IDs without changing packer inputs or ranking."""
    result = {}
    seen = set()
    for source in SOURCE_ORDER:
        copied = []
        for hit in selected.get(source, []):
            item = dict(hit)
            identifier = recall_id(source, hit_reference(item), item)
            if identifier in seen:
                raise AttributionError("derived recall identity collision")
            seen.add(identifier)
            item["recall_id"] = identifier
            copied.append(item)
        result[source] = copied
    return result


def _read_bounded_nofollow(path, limit):
    """Read one absolute file through a descriptor-pinned, no-symlink path walk."""
    absolute = os.path.abspath(path)
    parts = [part for part in absolute.split(os.sep) if part]
    if not parts:
        raise AttributionError("bounded file path has no filename")
    directory_fds = []
    file_fd = None
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory_fds.append(os.open(os.path.sep, directory_flags))
        for part in parts[:-1]:
            directory_fds.append(os.open(
                part, directory_flags, dir_fd=directory_fds[-1],
            ))
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fds[-1])
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise AttributionError("bounded file is not a regular file within the size limit")
        chunks = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(65536, limit + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise AttributionError("bounded file grew beyond the size limit")
        return b"".join(chunks)
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        for descriptor in reversed(directory_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_regular(path, required=False, limit=2 * 1024 * 1024):
    try:
        data = _read_bounded_nofollow(path, limit)
    except FileNotFoundError as exc:
        if required:
            raise AttributionError(
                "required attribution artifact is missing: %s" % os.path.basename(path)
            ) from exc
        return ""
    except AttributionError:
        raise
    except OSError as exc:
        raise AttributionError("unsafe attribution artifact: %s" % os.path.basename(path)) from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AttributionError("attribution artifact is unreadable: %s" % os.path.basename(path)) from exc


def _fingerprint_bytes(root, path, data):
    reference = os.path.relpath(path, root).replace(os.sep, "/")
    digest = hashlib.sha256(data).hexdigest()
    return {
        "ref": reference,
        "path": reference,
        "sha256": digest,
        "digest": digest,
        "digest_algorithm": "sha256",
        "status": "current",
    }


def _parse_ids(value, field):
    if value == "none":
        return []
    values = [part.strip() for part in value.split(",")]
    if not values or any(not _ID_RE.fullmatch(item) for item in values):
        raise AttributionError("%s contains an invalid recall ID" % field)
    if len(values) != len(set(values)):
        raise AttributionError("%s contains duplicate recall IDs" % field)
    return values


def parse_plan(content):
    lines = content.splitlines()
    marker_lines = [line for line in lines if _MARKER_TOKEN in line]
    if not marker_lines:
        return {"contract": 0, "applied_ids": [], "decisions": {}}
    if marker_lines != [_MARKER]:
        raise AttributionError("recall attribution marker must be one exact supported marker")

    applied_candidates = [line for line in lines if line.startswith("Applied recall IDs")]
    if len(applied_candidates) != 1:
        raise AttributionError("Applied recall IDs must be declared exactly once")
    applied_match = _APPLIED_RE.fullmatch(applied_candidates[0])
    if not applied_match:
        raise AttributionError("Applied recall IDs declaration is malformed")
    applied = _parse_ids(applied_match.group(1), "Applied recall IDs")

    decisions = {}
    for line in lines:
        if not line.startswith("Decision D"):
            continue
        match = _DECISION_RE.fullmatch(line)
        if not match:
            raise AttributionError("Decision declaration is malformed")
        number = match.group(1)
        if number in decisions:
            raise AttributionError("Decision D%s is duplicated" % number)
        decisions[number] = None
    if not decisions:
        raise AttributionError("active recall attribution requires at least one Decision")

    recalls = {}
    for line in lines:
        if not line.startswith("Recall D"):
            continue
        match = _RECALL_RE.fullmatch(line)
        if not match:
            raise AttributionError("Recall decision declaration is malformed")
        number = match.group(1)
        if number in recalls:
            raise AttributionError("Recall D%s is duplicated" % number)
        recalls[number] = _parse_ids(match.group(2), "Recall D%s" % number)
    if set(recalls) != set(decisions):
        raise AttributionError("every Decision must have exactly one matching Recall declaration")
    return {"contract": CONTRACT, "applied_ids": applied, "decisions": recalls}


def recall_hit_map(value):
    if not isinstance(value, dict):
        raise AttributionError("RECALL.json must contain an object")
    header = value.get("attribution")
    if not isinstance(header, dict) or header.get("contract") != CONTRACT:
        raise AttributionError("RECALL.json does not support recall attribution contract 1")
    sources = value.get("sources")
    if not isinstance(sources, dict):
        raise AttributionError("RECALL.json sources are malformed")
    result = {}
    for source in SOURCE_ORDER:
        section = sources.get(source, {})
        hits = section.get("hits", []) if isinstance(section, dict) else []
        if not isinstance(hits, list):
            raise AttributionError("RECALL.json %s hits are malformed" % source)
        for hit in hits:
            if not isinstance(hit, dict):
                raise AttributionError("RECALL.json contains a non-object hit")
            identifier = hit.get("recall_id")
            if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
                raise AttributionError("RECALL.json contains a hit without a valid recall_id")
            expected = recall_id(source, hit_reference(hit), hit)
            if identifier != expected:
                raise AttributionError("RECALL.json recall_id does not match current hit content")
            if identifier in result:
                raise AttributionError("RECALL.json contains a recall_id collision")
            result[identifier] = {"source": source, "reference": hit_reference(hit)}
            learning_id = hit.get("id")
            if (source == "learnings" and isinstance(learning_id, str)
                    and 0 < len(learning_id) <= 128):
                result[identifier]["learning_id"] = learning_id
                try:
                    result[identifier]["learning_fingerprint"] = (
                        rows.learning_content_fingerprint(hit)
                    )
                except ValueError as exc:
                    raise AttributionError(
                        "RECALL.json learning hit is not canonical JSON"
                    ) from exc
    return result


def _resolve(root, run_dir):
    root = os.path.realpath(root)
    if run_dir == "." and os.environ.get("KIMIFLOW_PINNED_RUN_CWD") == "1":
        try:
            info = os.stat(".", follow_symlinks=False)
            expected = (
                int(os.environ.get("KIMIFLOW_PINNED_RUN_DEVICE", "")),
                int(os.environ.get("KIMIFLOW_PINNED_RUN_INODE", "")),
            )
        except (OSError, ValueError) as exc:
            raise AttributionError("pinned attribution run is no longer reachable") from exc
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != expected:
            raise AttributionError("pinned attribution run identity changed")
        run_dir = os.path.abspath(".")
    elif not os.path.isabs(run_dir):
        run_dir = os.path.join(root, run_dir)
    run_dir = os.path.realpath(run_dir)
    if os.path.commonpath((root, run_dir)) != root:
        raise AttributionError("attribution run is outside the repository")
    return root, run_dir


def sealed_recall_hit_map(root, run_dir, expected_sha256, limit=2 * 1024 * 1024):
    """Re-open the exact bounded RECALL artifact sealed into an outcome."""
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise AttributionError("sealed RECALL.json digest is invalid")
    root, run_dir = _resolve(root, run_dir)
    recall_path = os.path.join(run_dir, "RECALL.json")
    try:
        data = _read_bounded_nofollow(recall_path, limit)
    except FileNotFoundError as exc:
        raise AttributionError("sealed RECALL.json is missing") from exc
    except AttributionError:
        raise
    except OSError as exc:
        raise AttributionError("sealed RECALL.json is unsafe") from exc
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise AttributionError("sealed RECALL.json no longer matches the outcome")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttributionError("sealed RECALL.json is malformed") from exc
    return recall_hit_map(value), len(data)


def _validate_evidence_path(root, relative, line_number):
    if (not relative or os.path.isabs(relative) or "\\" in relative
            or ":" in relative or relative.startswith("./")):
        raise AttributionError("recall contradiction path is not a safe repo-relative path")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise AttributionError("recall contradiction path is not normalized")
    root = os.path.realpath(root)
    try:
        data = _read_bounded_nofollow(os.path.join(root, *parts), 8 * 1024 * 1024)
    except AttributionError:
        raise
    except OSError as exc:
        raise AttributionError("recall contradiction evidence is unreadable") from exc
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise AttributionError("recall contradiction evidence is unreadable") from exc
    if line_number > len(lines) or not lines[line_number - 1].strip():
        raise AttributionError("recall contradiction evidence line is missing or empty")
    reference = "%s:%d" % (relative, line_number)
    fingerprint = _fingerprint_bytes(root, os.path.join(root, *parts), data)
    fingerprint["ref"] = reference
    return fingerprint


def _parse_verification(root, content):
    checks = {}
    contradictions = {}
    verify_markers = []
    for line in content.splitlines():
        if "kimiflow:verification" in line:
            match = _VERIFY_RE.fullmatch(line)
            if not match:
                raise AttributionError("verification marker is malformed")
            verify_markers.append(match.groups())
        if line.startswith("Decision check D"):
            match = _CHECK_RE.fullmatch(line)
            if not match:
                raise AttributionError("Decision check declaration is malformed")
            number, status = match.groups()
            if number in checks:
                raise AttributionError("Decision check D%s is duplicated" % number)
            checks[number] = status
        if line.startswith("Recall contradiction"):
            match = _CONTRADICTION_RE.fullmatch(line)
            if not match:
                raise AttributionError("Recall contradiction declaration is malformed")
            identifier, relative, raw_line = match.groups()
            if identifier in contradictions:
                raise AttributionError("Recall contradiction is duplicated")
            contradictions[identifier] = _validate_evidence_path(root, relative, int(raw_line))
    if len(verify_markers) > 1:
        raise AttributionError("verification marker is duplicated")
    marker = verify_markers[0] if verify_markers else None
    return {"checks": checks, "contradictions": contradictions, "marker": marker}


def _load_contract(root, run_dir):
    root, run_dir = _resolve(root, run_dir)
    plan_path = os.path.join(run_dir, "PLAN.md")
    plan_text = _read_regular(plan_path, required=False)
    plan = parse_plan(plan_text)
    if plan["contract"] == 0:
        return root, run_dir, plan, {}, {"checks": {}, "contradictions": {}, "marker": None}
    recall_path = os.path.join(run_dir, "RECALL.json")
    recall_text = _read_regular(recall_path, required=True)
    try:
        recall_value = json.loads(recall_text)
    except json.JSONDecodeError as exc:
        raise AttributionError("RECALL.json is malformed") from exc
    hits = recall_hit_map(recall_value)
    verification_path = os.path.join(run_dir, "VERIFICATION.md")
    verification_text = _read_regular(verification_path, required=False)
    verification = _parse_verification(root, verification_text)
    verification["artifact_fingerprints"] = [
        _fingerprint_bytes(root, plan_path, plan_text.encode("utf-8")),
        _fingerprint_bytes(root, recall_path, recall_text.encode("utf-8")),
        _fingerprint_bytes(root, verification_path, verification_text.encode("utf-8")),
    ]
    verification["_context"] = {
        "plan": plan_text,
        "recall": recall_value,
        "verification": verification_text,
    }
    applied = set(plan["applied_ids"])
    if any(identifier not in hits for identifier in applied):
        raise AttributionError("Applied recall IDs contains an unknown current recall ID")
    linked = set()
    for number, identifiers in plan["decisions"].items():
        for identifier in identifiers:
            if identifier not in applied:
                raise AttributionError("Recall D%s references an ID not declared as applied" % number)
            linked.add(identifier)
    for number in verification["checks"]:
        if number not in plan["decisions"]:
            raise AttributionError("Decision check has no matching plan Decision")
    for identifier in verification["contradictions"]:
        if identifier not in applied:
            raise AttributionError("Recall contradiction references an ID not declared as applied")
    if applied - linked - set(verification["contradictions"]):
        raise AttributionError("an applied recall ID is neither decision-linked nor contradicted")
    return root, run_dir, plan, hits, verification


def usage_json(root, run_dir):
    _root, _run_dir, plan, hits, verification = _load_contract(root, run_dir)
    if plan["contract"] == 0:
        return {"contract": 0, "applied_ids": [], "hit_count": 0, "method": "legacy_heuristic"}
    return {
        "contract": CONTRACT,
        "applied_ids": list(plan["applied_ids"]),
        "hit_count": len(hits),
        "method": "declared_recall_ids",
        "contradiction_evidence": [
            item["ref"] for item in verification["contradictions"].values()
        ],
    }


def _legacy_receipt():
    return {
        "contract": 0,
        "status": "legacy",
        "classification": "neutral",
        "applied_ids": [],
        "items": [],
        "contradiction_evidence": [],
    }


def evaluate_json(root, run_dir, terminal, include_context=False):
    if terminal not in ("done", "failed", "aborted", "parked"):
        raise AttributionError("terminal must be done|failed|aborted|parked")
    _root, _run_dir, plan, hits, verification = _load_contract(root, run_dir)
    if plan["contract"] == 0:
        return _legacy_receipt()
    marker = verification["marker"]
    if terminal == "done" and marker is None:
        raise AttributionError("done attribution requires an exact verification marker")
    global_green = marker == ("passed", "passed", "passed") and all(
        verification["checks"].get(number) == "passed" for number in plan["decisions"]
    )
    items = []
    for identifier in plan["applied_ids"]:
        decision_numbers = [
            number for number, identifiers in plan["decisions"].items()
            if identifier in identifiers
        ]
        check_status = {
            "D%s" % number: verification["checks"].get(number, "missing")
            for number in decision_numbers
        }
        contradiction_fingerprint = verification["contradictions"].get(identifier)
        contradiction = contradiction_fingerprint["ref"] if contradiction_fingerprint else None
        if contradiction or "failed" in check_status.values():
            classification = "contradicted"
        elif terminal == "done" and global_green and decision_numbers:
            classification = "helpful"
        else:
            classification = "neutral"
        item = {
            "recall_id": identifier,
            "source": hits[identifier]["source"],
            "classification": classification,
            "decision_checks": check_status,
            "evidence": [contradiction] if contradiction else [],
        }
        if "learning_id" in hits[identifier]:
            item["learning_id"] = hits[identifier]["learning_id"]
        items.append(item)
    classifications = [item["classification"] for item in items]
    overall = "contradicted" if "contradicted" in classifications else (
        "helpful" if "helpful" in classifications else "neutral"
    )
    complete = terminal == "done" and global_green and all(
        value != "neutral" for value in classifications
    )
    result = {
        "contract": CONTRACT,
        "status": "complete" if complete else "inconclusive",
        "classification": overall,
        "applied_ids": list(plan["applied_ids"]),
        "items": items,
        "contradiction_evidence": [
            item["ref"] for item in verification["contradictions"].values()
        ],
        "contradiction_fingerprints": list(verification["contradictions"].values()),
        "artifact_fingerprints": list(verification["artifact_fingerprints"]),
        "terminal": terminal,
    }
    if include_context:
        result["_context"] = verification["_context"]
    return result
