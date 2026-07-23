"""Evidence-gated strategy outcome evaluation and bounded strategy recall."""
import contextlib
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import unicodedata

from . import attribution, clock, contracts, rows, runs, store, text
from .cli import die, resolve_root, usage


LEDGER_REL = ".kimiflow/project/STRATEGY-OUTCOMES.jsonl"
ARTIFACT_NAME = "OUTCOME-EVALUATION.json"
_ID_RE = re.compile(r"^out_[0-9a-f]{64}$")
_HEAD_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REVIEW_RE = re.compile(r"^r([1-9][0-9]*)-code-verified\.md$")
_FINDING_RE = re.compile(r"^FINDING (BLOCKER|HIGH) .+ :: .+$", re.MULTILINE)
_VERIFY_RE = re.compile(
    r"^<!-- kimiflow:verification outcome=(passed|failed) "
    r"criteria=(passed|failed|not_run) regression=(passed|failed|not_run) -->$"
)
_TERM_SPLIT = re.compile(r"[^a-z0-9_-]+")
_STOPWORDS = frozenset((
    "the", "and", "for", "mit", "und", "der", "die", "das", "ein", "eine", "ist",
    "sind", "was", "wie", "this", "that", "from", "into", "zur", "zum", "auf", "von",
))
_ECONOMIC_FIELDS = (
    "result", "confidence", "gross_estimated_tokens_saved", "review_cost_tokens",
    "net_estimated_tokens_saved", "observed_input_tokens", "observed_output_tokens",
    "observed_total_tokens",
)
_LEDGER_RETAIN_BYTES = 4 * 1024 * 1024
_LEDGER_RETAIN_ROWS = 2048


class OutcomeError(ValueError):
    pass


def _safe_read(path, limit=1048576):
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return ""
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read(limit + 1)
    except (OSError, UnicodeDecodeError):
        return ""


def _state_values(state_text, label):
    wanted = label.lower()
    values = []
    for raw in state_text.split("\n"):
        plain = re.sub(r"^[ \t]*-[ \t]*", "", raw.replace("**", "")).strip()
        if ":" not in plain:
            continue
        key, value = plain.split(":", 1)
        if key.strip().lower() == wanted:
            values.append(value.strip())
    return values


def _state_value(state_text, label):
    values = _state_values(state_text, label)
    return values[0] if values else ""


def _exact_plan_value(plan_text, label):
    pattern = re.compile(r"^%s:[ \t]*(.*)$" % re.escape(label), re.MULTILINE)
    hits = pattern.findall(plan_text)
    return hits[0].strip() if len(hits) == 1 else ""


def _safe_strategy(plan_text):
    raw_hits = re.findall(r"^Strategy:[ \t]*(.*)$", plan_text, re.MULTILINE)
    if len(raw_hits) != 1:
        return "", "strategy_missing_or_duplicate"
    value = re.sub(r"[ \t]+", " ", raw_hits[0].strip())
    if not 12 <= len(value) <= 240:
        return "", "strategy_unsafe"
    if any(unicodedata.category(char).startswith("C") for char in value):
        return "", "strategy_unsafe"
    if not rows.memory_security_json(value).get("ok") or rows.has_secret_value(value):
        return "", "strategy_unsafe"
    return value, ""


def _query_terms(value):
    lowered = text.ascii_lower(value)
    out = []
    seen = set()
    for token in _TERM_SPLIT.split(lowered):
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) == 30:
            break
    return out


def _git(root, args):
    try:
        return subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None


def _valid_commit(root, value):
    if not isinstance(value, str) or not _HEAD_RE.fullmatch(value):
        return False
    proc = _git(root, ["cat-file", "-e", value + "^{commit}"])
    return proc is not None and proc.returncode == 0


def _head(root):
    proc = _git(root, ["rev-parse", "--verify", "HEAD"])
    if proc is None or proc.returncode != 0:
        return ""
    value = proc.stdout.decode("ascii", "replace").strip().lower()
    return value if _valid_commit(root, value) else ""


def _valid_rel_path(value):
    if not isinstance(value, str) or not value or "\0" in value or os.path.isabs(value):
        return False
    normalized = os.path.normpath(value).replace(os.sep, "/")
    return normalized == value and normalized not in (".", "..") and not normalized.startswith("../")


def _changed_paths(root, started, source):
    if not (_valid_commit(root, started) and _valid_commit(root, source)):
        return []
    proc = _git(root, ["diff", "--name-only", "--no-renames", "-z", started, source])
    if proc is None or proc.returncode != 0:
        return []
    paths = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            return []
        if value.startswith(".kimiflow/"):
            continue
        if not _valid_rel_path(value):
            return []
        paths.append(value)
    return sorted(set(paths))


def _resolve_run(root, run):
    pinned_rel = os.environ.get("KIMIFLOW_PINNED_RUN_REL", "")
    if run == "." and os.environ.get("KIMIFLOW_PINNED_RUN_CWD") == "1":
        try:
            info = os.stat(".", follow_symlinks=False)
            expected = (
                int(os.environ.get("KIMIFLOW_PINNED_RUN_DEVICE", "")),
                int(os.environ.get("KIMIFLOW_PINNED_RUN_INODE", "")),
            )
        except (OSError, ValueError) as exc:
            raise OutcomeError("pinned run directory is no longer reachable") from exc
        if not _valid_rel_path(pinned_rel) or not pinned_rel.startswith(".kimiflow/"):
            raise OutcomeError("unsafe pinned run path")
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != expected:
            raise OutcomeError("pinned run directory identity changed")
        return os.path.realpath(os.path.abspath(root)), ".", pinned_rel
    root_abs = os.path.abspath(root)
    root = os.path.realpath(root_abs)
    if os.path.isabs(run):
        run_abs = os.path.abspath(run)
        try:
            if os.path.commonpath((run_abs, root_abs)) != root_abs:
                raise OutcomeError("unsafe run path")
        except ValueError as exc:
            raise OutcomeError("unsafe run path") from exc
        candidate = os.path.join(root, os.path.relpath(run_abs, root_abs))
    else:
        candidate = os.path.join(root, run)
    candidate = os.path.abspath(candidate)
    real = os.path.realpath(candidate)
    kimiflow = os.path.join(root, ".kimiflow")
    try:
        contained = os.path.commonpath((real, kimiflow)) == kimiflow
    except ValueError:
        contained = False
    if real != candidate or not contained:
        raise OutcomeError("unsafe run path")
    try:
        info = os.lstat(candidate)
    except OSError as exc:
        raise OutcomeError("run directory not found: %s" % exc) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OutcomeError("unsafe run directory")
    return root, candidate, os.path.relpath(candidate, root).replace(os.sep, "/")


def _verification_content(content):
    matches = []
    for line in content.split("\n"):
        found = _VERIFY_RE.fullmatch(line)
        if found:
            matches.append(found.groups())
    if len(matches) != 1:
        return {"outcome": "missing", "criteria": "missing", "regression": "missing"}
    outcome, criteria, regression = matches[0]
    return {"outcome": outcome, "criteria": criteria, "regression": regression}


def _verification(run_dir):
    return _verification_content(_safe_read(os.path.join(run_dir, "VERIFICATION.md")))


def _review_gate_clean(run_dir, round_number):
    resolver = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "resolve-review-gate.sh",
    )
    if not os.path.isfile(resolver) or not os.access(resolver, os.X_OK):
        return False
    state_text = _safe_read(os.path.join(run_dir, "STATE.md"))
    args = [
        resolver,
        os.path.join(run_dir, "findings"),
        "--round",
        str(round_number),
        "--expect",
        "code-verified",
    ]
    convergence_values = _state_values(state_text, "Convergence contract")
    contracted = convergence_values == ["1"]
    if contracted:
        args.extend(("--finding-contract", "1"))
    selector_values = (
        _state_values(state_text, "Review gate"),
        _state_values(state_text, "Review epoch start"),
        _state_values(state_text, "Review epoch cap"),
    )
    if any(selector_values):
        if any(len(values) != 1 for values in selector_values):
            return False
        review_gate = selector_values[0][0].lower()
        epoch_start = selector_values[1][0]
        epoch_cap = selector_values[2][0]
        if (
            review_gate != "code"
            or not epoch_start.isdigit()
            or not epoch_cap.isdigit()
        ):
            return False
        start_number = int(epoch_start)
        cap_number = int(epoch_cap)
        if not 1 <= start_number <= round_number <= cap_number:
            return False
        args.extend((
            "--gate",
            "code",
            "--epoch-start",
            str(start_number),
            "--cap",
            str(cap_number),
        ))
    elif contracted:
        return False
    else:
        args.extend(("--cap", str(max(3, round_number))))
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    try:
        lines = proc.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return False
    fields = lines[0].split("\t") if len(lines) == 1 else []
    return proc.returncode == 0 and fields[:3] == ["OPEN", "0", "clean"]


def _latest_review(run_dir):
    findings = os.path.join(run_dir, "findings")
    candidates = []
    try:
        names = os.listdir(findings)
    except OSError:
        names = []
    for name in names:
        match = _REVIEW_RE.fullmatch(name)
        if match:
            candidates.append((int(match.group(1)), name))
    if not candidates:
        return {"status": "missing", "round": 0, "path": "", "content": ""}
    round_number, name = max(candidates)
    content = _safe_read(os.path.join(findings, name))
    lines = [line for line in content.splitlines() if line]
    resolution_only = bool(lines) and all(line.startswith("RESOLVED ") for line in lines)
    clean_candidate = content.strip() == "NONE" or resolution_only
    status = "clean" if clean_candidate and _review_gate_clean(run_dir, round_number) else (
        "blocking" if _FINDING_RE.search(content) else "advisory"
    )
    return {
        "status": status,
        "round": round_number,
        "path": "findings/" + name,
        "content": content,
    }


def _items_open(run_dir):
    path = os.path.join(run_dir, "ITEMS.jsonl")
    if not os.path.exists(path):
        return 0
    content = _safe_read(path)
    if not content and os.path.getsize(path) > 0:
        return 1
    count = 0
    for line in content.split("\n"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            count += 1
            continue
        if not isinstance(row, dict) or row.get("status") in ("pending", "built", "rejected"):
            count += 1
        elif row.get("status") not in ("accepted", "dropped"):
            count += 1
    return count


def _learning_review(root, run_rel):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = runs.run(["--root", root, "--run", run_rel])
    line = out.getvalue().strip()
    return {"status": "open" if code == 0 else "closed", "line": line}


def _economics(run_dir):
    lifecycle = store.read_json(os.path.join(run_dir, "RUN-LIFECYCLE.json"), {})
    source = lifecycle.get("economics", {}) if isinstance(lifecycle, dict) else {}
    if not isinstance(source, dict):
        return {}
    result = {}
    for key in _ECONOMIC_FIELDS:
        value = source.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            result[key] = value
        elif key in ("result", "confidence") and isinstance(value, str) and re.fullmatch(
            r"[a-z0-9_-]{1,32}", value
        ):
            result[key] = value
    return result


def _recall_signals_value(recall, evidence_id):
    sources = recall.get("sources", {}) if isinstance(recall, dict) else {}
    strategies = sources.get("strategies", {}) if isinstance(sources, dict) else {}
    hits = strategies.get("hits", []) if isinstance(strategies, dict) else []
    hits = [item for item in hits if isinstance(item, dict)] if isinstance(hits, list) else []
    ids = {item.get("id") for item in hits}
    return len(hits), evidence_id is not None and evidence_id in ids


def _recall_signals(run_dir, evidence_id):
    return _recall_signals_value(
        store.read_json(os.path.join(run_dir, "RECALL.json"), {}), evidence_id,
    )


def evaluate_json(root, run, terminal):
    if terminal not in ("done", "failed", "aborted", "parked"):
        raise OutcomeError("terminal must be done|failed|aborted|parked")
    root, run_dir, run_rel = _resolve_run(root, run)
    try:
        recall_attribution = attribution.evaluate_json(
            root, run_dir, terminal, include_context=True,
        )
    except attribution.AttributionError as exc:
        raise OutcomeError("recall attribution: %s" % exc) from exc
    attribution_context = recall_attribution.pop("_context", None)
    if recall_attribution["contract"] == 1:
        if not isinstance(attribution_context, dict):
            raise OutcomeError("recall attribution context is missing")
        plan = attribution_context["plan"]
        recall_value = attribution_context["recall"]
        verify = _verification_content(attribution_context["verification"])
    else:
        plan = _safe_read(os.path.join(run_dir, "PLAN.md"))
        recall_value = None
        verify = _verification(run_dir)
    state = _safe_read(os.path.join(run_dir, "STATE.md"))
    strategy, strategy_error = _safe_strategy(plan)
    evidence_raw = _exact_plan_value(plan, "Strategy evidence")
    evidence_id = evidence_raw if _ID_RE.fullmatch(evidence_raw) else None
    source_head = _head(root)
    started_head = _state_value(state, "Run started head").lower()
    affected_paths = _changed_paths(root, started_head, source_head)
    review = _latest_review(run_dir)
    open_items = _items_open(run_dir)
    learning = _learning_review(root, run_rel)
    recovery = _state_value(state, "Recovery").lower()
    phase6 = _state_value(state, "Phase 6").lower()
    recovery_text = _safe_read(os.path.join(run_dir, "RECOVERY.md"))
    first_plan_success = "kimiflow:recovery" not in recovery_text
    recall_hits, recall_used = (
        _recall_signals_value(recall_value, evidence_id)
        if recall_attribution["contract"] == 1
        else _recall_signals(run_dir, evidence_id)
    )
    attribution_complete = (
        recall_attribution["contract"] == 0
        or recall_attribution["status"] == "complete"
    )
    positive = all((
        terminal == "done",
        bool(strategy),
        bool(source_head),
        bool(affected_paths),
        phase6 == "done",
        recovery == "clean",
        open_items == 0,
        verify == {"outcome": "passed", "criteria": "passed", "regression": "passed"},
        review.get("status") == "clean",
        learning.get("status") == "open",
        attribution_complete,
    ))
    explicit_failure = (
        verify.get("outcome") == "failed"
        and "failed" in (verify.get("criteria"), verify.get("regression"))
    ) or review.get("status") == "blocking"
    negative = terminal == "failed" and bool(strategy) and bool(source_head) and explicit_failure

    classification = "verified_success" if positive else (
        "verified_failure" if negative else "inconclusive"
    )
    reasons = []
    if strategy_error:
        reasons.append(strategy_error)
    if not source_head:
        reasons.append("source_head_invalid")
    if not affected_paths:
        reasons.append("project_delta_missing")
    if classification == "inconclusive":
        reasons.append("insufficient_outcome_evidence")

    evidence = [run_rel + "/PLAN.md"]
    for relative in ("VERIFICATION.md", "ITEMS.jsonl", "LEARNING-REVIEW.md"):
        if os.path.isfile(os.path.join(run_dir, relative)) and not os.path.islink(os.path.join(run_dir, relative)):
            evidence.append(run_rel + "/" + relative)
    if review.get("path"):
        evidence.append(run_rel + "/" + review["path"])
    if recall_attribution["contract"] == 1:
        evidence.append(run_rel + "/RECALL.json")
        evidence.extend(recall_attribution["contradiction_evidence"])
    sealed_fingerprints = (
        recall_attribution.get("artifact_fingerprints", [])
        + recall_attribution.get("contradiction_fingerprints", [])
    )
    sealed_refs = {item["ref"] for item in sealed_fingerprints}
    evidence_fingerprints = rows.evidence_fingerprints_json(
        root, [ref for ref in evidence if ref not in sealed_refs],
    )
    by_ref = {item["ref"]: item for item in sealed_fingerprints}
    evidence_fingerprints.extend(by_ref[ref] for ref in evidence if ref in by_ref)

    identifier_basis = (run_rel + "\0" + strategy).encode("utf-8")
    identifier = "out_" + hashlib.sha256(identifier_basis).hexdigest()
    terms = _query_terms(strategy)
    mode = _state_value(state, "Mode").lower()
    scope = _state_value(state, "Scope").lower()
    if mode not in ("feature", "fix", "audit", "feature-check", "review"):
        mode = ""
    if scope not in ("trivial", "small", "large"):
        scope = ""
    return {
        "schema_version": 1,
        "id": identifier,
        "run": run_rel,
        "evaluated_at": clock.iso_now(),
        "terminal": terminal,
        "classification": classification,
        "promotable": classification in ("verified_success", "verified_failure"),
        "mode": mode,
        "scope": scope,
        "terms": terms,
        "strategy": {"summary": strategy, "evidence_id": evidence_id},
        "source_head": source_head,
        "affected_paths": affected_paths,
        "signals": {
            "phase6": phase6,
            "recovery": recovery,
            "items_open": open_items,
            "verification": verify,
            "code_review": review.get("status"),
            "learning_review": learning.get("status"),
            "first_plan_success": first_plan_success,
            "strategy_recall_hits": recall_hits,
            "strategy_recall_used": recall_used,
            **({"recall_attribution": recall_attribution["classification"]}
               if recall_attribution["contract"] == 1 else {}),
        },
        **({"recall_attribution": recall_attribution}
           if recall_attribution["contract"] == 1 else {}),
        "economics": _economics(run_dir),
        "evidence": evidence,
        "evidence_fingerprints": evidence_fingerprints,
        "reasons": reasons,
    }


def _safe_directory(path):
    if os.path.lexists(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OutcomeError("unsafe outcome directory: %s" % path)
        return
    os.mkdir(path, 0o700)


def _snapshot_file(path):
    if not os.path.lexists(path):
        return {"exists": False, "data": b"", "mode": 0o600}
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OutcomeError("unsafe outcome destination: %s" % path)
    with open(path, "rb") as handle:
        data = handle.read()
    return {"exists": True, "data": data, "mode": stat.S_IMODE(info.st_mode)}


def _atomic_restore(path, snapshot):
    if not snapshot["exists"]:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(prefix=os.path.basename(path) + ".restore.", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(snapshot["data"])
        os.chmod(temporary, snapshot["mode"])
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _strict_ledger(path):
    if not os.path.exists(path):
        return []
    content = _safe_read(path, limit=8388608)
    if not content and os.path.getsize(path) > 0:
        raise OutcomeError("strategy outcome ledger is unreadable or oversized")
    result = []
    for line in content.split("\n"):
        if not line.strip():
            continue
        value = store.parse_json_object_strict(line)
        if value is None:
            raise OutcomeError("strategy outcome ledger is malformed")
        result.append(value)
    return result


def _retained_ledger(rows_in):
    encoded = [contracts.dumps(row) + "\n" for row in rows_in]
    if encoded and len(encoded[-1].encode("utf-8")) > _LEDGER_RETAIN_BYTES:
        raise OutcomeError("new strategy outcome exceeds ledger retention limit")
    start = max(0, len(encoded) - _LEDGER_RETAIN_ROWS)
    retained_bytes = sum(
        len(line.encode("utf-8")) for line in encoded[start:]
    )
    while start < len(encoded) and retained_bytes > _LEDGER_RETAIN_BYTES:
        retained_bytes -= len(encoded[start].encode("utf-8"))
        start += 1
    return rows_in[start:], "".join(encoded[start:])


def persist_evaluation(root, run, evaluation):
    root, run_dir, run_rel = _resolve_run(root, run)
    if evaluation.get("run") != run_rel:
        raise OutcomeError("evaluation run does not match destination")
    kimiflow = os.path.join(root, ".kimiflow")
    _safe_directory(kimiflow)
    project = os.path.join(kimiflow, "project")
    _safe_directory(project)
    ledger_path = os.path.join(root, LEDGER_REL)
    artifact_path = os.path.join(run_dir, ARTIFACT_NAME)
    with store.path_lock(ledger_path):
        snapshots = {
            ledger_path: _snapshot_file(ledger_path),
            artifact_path: _snapshot_file(artifact_path),
        }
        current = [
            row for row in _strict_ledger(ledger_path)
            if row.get("run") != run_rel
        ]
        if evaluation.get("promotable") is True:
            current.append(evaluation)
        current, ledger_text = _retained_ledger(current)
        artifact_text = contracts.dumps(evaluation, pretty=True) + "\n"
        try:
            store.atomic_write(ledger_path, ledger_text, mode=0o600, refuse_symlink=True)
            store.atomic_write(artifact_path, artifact_text, mode=0o600, refuse_symlink=True)
        except BaseException:
            rollback_error = None
            for path in (ledger_path, artifact_path):
                try:
                    _atomic_restore(path, snapshots[path])
                except BaseException as exc:
                    rollback_error = rollback_error or exc
            if rollback_error is not None:
                raise OutcomeError("outcome write failed and rollback was incomplete") from rollback_error
            raise
    return {"written": True, "ledger_count": len(current)}


def _evidence_current(root, row):
    evidence = row.get("evidence")
    stored = row.get("evidence_fingerprints")
    if not isinstance(evidence, list) or not evidence or not isinstance(stored, list) or not stored:
        return False
    current = rows.evidence_fingerprints_json(root, evidence)
    return contracts.dumps(current) == contracts.dumps(stored) and all(
        item.get("status") == "current" for item in current
    )


def _project_fit(root, row):
    source = row.get("source_head")
    affected = row.get("affected_paths")
    if not _valid_commit(root, source) or not isinstance(affected, list) or not affected:
        return False
    if len(affected) > 512 or not all(_valid_rel_path(path) for path in affected):
        return False
    diff = _git(root, ["diff", "--quiet", source, "HEAD", "--"] + affected)
    if diff is None or diff.returncode != 0:
        return False
    dirty = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--"] + affected)
    return dirty is not None and dirty.returncode == 0 and dirty.stdout == b""


def _recall_candidate(root, row, terms, mode):
    if not isinstance(row, dict) or row.get("promotable") is not True:
        return None
    classification = row.get("classification")
    if classification not in ("verified_success", "verified_failure"):
        return None
    if not _ID_RE.fullmatch(str(row.get("id", ""))):
        return None
    strategy = row.get("strategy")
    summary = strategy.get("summary", "") if isinstance(strategy, dict) else ""
    safe_summary, _ = _safe_strategy("Strategy: %s\n" % summary)
    if not safe_summary:
        return None
    row_terms = _query_terms(safe_summary)
    overlap = len(set(terms).intersection(row_terms))
    if overlap == 0 or not _evidence_current(root, row) or not _project_fit(root, row):
        return None
    signals = row.get("signals") if isinstance(row.get("signals"), dict) else {}
    score = (
        overlap,
        1 if mode and row.get("mode") == mode else 0,
        1 if signals.get("first_plan_success") is True else 0,
        str(row.get("evaluated_at", "")),
        str(row.get("id", "")),
    )
    return score, {
        "id": row["id"],
        "kind": "strategy_outcome",
        "classification": classification,
        "strategy": safe_summary,
        "mode": row.get("mode", ""),
        "scope": row.get("scope", ""),
        "source_run": row.get("run", ""),
        "first_plan_success": signals.get("first_plan_success") is True,
    }


def strategy_recall_json(root, terms, mode="", max_hits=2):
    path = os.path.join(root, LEDGER_REL)
    rows_in = store.read_jsonl(path)
    buckets = {"verified_success": [], "verified_failure": []}
    for row in rows_in:
        candidate = _recall_candidate(root, row, terms, mode)
        if candidate is not None:
            buckets[row["classification"]].append(candidate)
    hits = []
    for classification in ("verified_success", "verified_failure"):
        if buckets[classification]:
            _, card = max(buckets[classification], key=lambda item: item[0])
            hits.append(card)
    hits = hits[:max(0, min(int(max_hits), 2))]
    return {
        "path": LEDGER_REL,
        "status": "used" if hits else ("available_no_hits" if os.path.isfile(path) else "missing"),
        "count": len(hits),
        "hits": hits,
    }


def mode_for_artifact(path):
    return _state_value(_safe_read(os.path.join(os.path.dirname(path), "STATE.md")), "Mode").lower()


def run(argv):
    root = ""
    run_arg = ""
    terminal = ""
    write = False
    pretty = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--root", "--run", "--terminal"):
            i += 1
            value = argv[i] if i < len(argv) else ""
            if arg == "--root":
                root = value
            elif arg == "--run":
                run_arg = value
            else:
                terminal = value
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("evaluate-run: unknown argument: %s" % arg, 2)
        i += 1
    if not run_arg:
        return die("evaluate-run requires --run", 2)
    if terminal not in ("done", "failed", "aborted", "parked"):
        return die("evaluate-run --terminal must be done|failed|aborted|parked", 2)
    root = resolve_root(root)
    try:
        evaluation = evaluate_json(root, run_arg, terminal)
        persistence = persist_evaluation(root, run_arg, evaluation) if write else {"written": False}
    except (OSError, OutcomeError, ValueError) as exc:
        return die("evaluate-run: %s" % exc, 1)
    result = {
        "schema_version": 1,
        "status": "evaluated",
        "written": write,
        "evaluation": evaluation,
        "persistence": persistence,
    }
    contracts.json_print(result, pretty)
    return 0
