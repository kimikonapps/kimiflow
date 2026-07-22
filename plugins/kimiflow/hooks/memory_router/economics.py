"""Economics-record writer subsystem. Behavioral port of the Bash economics writers @
kimiflow--v0.1.50: run_artifact_corpus (2886), recall_hits_for_economics_json (2903),
economics_hits_tokens (2919), economics_used_hits_count (2933), run_economics_row_json
(2949), write_economics_row (3024), write_global_economics_row (3045),
record_run_economics_json (3124), plus project_size_bucket (434) + run_type_from_state
(447). The single public entry point is record_run_economics_json(root, run_dir), which
review-run calls to append a per-run token-economics row to the project ledger and (when
global metrics are enabled) an anonymized row to ~/.kimiflow/metrics/token-economics.jsonl.
No subcommand: pure library code consumed by review-run (Plan 26)."""
import math
import os
import re
import subprocess

from . import attribution, clock, contracts, global_metrics, paths, store, summaries, text

_CORPUS_FILES = (
    "RESEARCH.md", "DIAGNOSIS.md", "PLAN.md", "ACCEPTANCE.md",
    "REVIEW.md", "CODE-REVIEW.md", "VERIFICATION.md", "ADVISORIES.md",
)
_NONWORD = re.compile(r"[^A-Za-z0-9_]+")
_HEURISTIC = ("avoided_scan_tokens = used_hit_count * "
              "KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT (default 1200); directional only")
_RESULTS = ("unknown", "saving", "neutral", "waste")
_CONFIDENCES = ("none", "low", "medium", "high")


def _jq_or(value, default):
    # jq `value // default`: substitute only when value is null (None) or false.
    return default if value is None or value is False else value


def _read_artifact(path):
    # newline="" preserves \r like sed (which splits on \n only).
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _sed_head(content, count):
    # sed -n '1,Np': the first `count` \n-delimited lines. sed splits on \n ONLY and does
    # NOT add a trailing newline -- the final line keeps its terminator iff the input had
    # one (verified: `printf 'a\nb\nc' | sed -n '1,220p'` -> `a\nb\nc`, no trailing \n).
    lines = content.split("\n")
    if len(lines) <= count:
        return content
    return "\n".join(lines[:count]) + "\n"


def run_artifact_corpus(run_dir):
    # Bash run_artifact_corpus (2886-2901): first 220 lines of each fixed artifact + "\n",
    # then first 120 lines of each findings/*.md (recursive, path-sorted) + "\n". Used ONLY
    # as the haystack for economics_used_hits_count's substring `contains` -- never emitted
    # -- and the Bash caller wraps it in `$()` (which strips trailing newlines), so the
    # trailing-newline exactness is immaterial (needles are single-line).
    parts = []
    for name in _CORPUS_FILES:
        path = run_dir + "/" + name
        if not os.path.isfile(path):
            continue
        parts.append(_sed_head(_read_artifact(path), 220))
        parts.append("\n")
    findings = run_dir + "/findings"
    if os.path.isdir(findings):
        md_files = []
        for dirpath, _dirnames, filenames in os.walk(findings):
            for filename in filenames:
                if not filename.endswith(".md"):
                    continue
                full = os.path.join(dirpath, filename)
                # Bash `find -type f`: regular files only (a symlink is -type l, excluded).
                if os.path.isfile(full) and not os.path.islink(full):
                    md_files.append(full)
        for path in sorted(md_files):
            parts.append(_sed_head(_read_artifact(path), 120))
            parts.append("\n")
    return "".join(parts)


def recall_hits_for_economics_json(recall_json, include_strategies=False):
    # Bash recall_hits_for_economics_json (2903-2917): gated on `jq -e .` (fails for
    # top-level null/false), so guard with isinstance(dict). Concatenate, in order,
    # learnings/facts/index/history hits, each tagged with _economics_source.
    data = store.read_json(recall_json)
    if not isinstance(data, dict):
        return []
    sources = data.get("sources")
    if not isinstance(sources, dict):
        sources = {}
    out = []
    source_tags = [("learnings", "learning"), ("facts", "fact"),
                   ("index", "index"), ("history", "history")]
    if include_strategies:
        source_tags.append(("strategies", "strategy"))
    for key, tag in source_tags:
        section = sources.get(key)
        hits = section.get("hits") if isinstance(section, dict) else None
        if not isinstance(hits, list):
            continue
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            merged = dict(hit)
            merged["_economics_source"] = tag
            out.append(merged)
    return out


def economics_hits_tokens(hits):
    # Bash economics_hits_tokens (2919-2931): per hit, join title/summary/body/text with
    # spaces, collapse non-word runs to a space, count non-empty tokens; sum (// 0).
    total = 0
    for hit in hits:
        joined = "%s %s %s %s" % (
            _jq_or(hit.get("title"), ""),
            _jq_or(hit.get("summary"), ""),
            _jq_or(hit.get("body"), ""),
            _jq_or(hit.get("text"), ""),
        )
        cleaned = _NONWORD.sub(" ", joined)
        total += sum(1 for token in cleaned.split(" ") if token)
    return total


def economics_used_hits_count(hits, corpus):
    # Bash economics_used_hits_count (2933-2947): count hits where ANY non-empty needle
    # (id/ref/path/title/evidence[0]) is a substring of corpus.
    count = 0
    for hit in hits:
        evidence = hit.get("evidence")
        first_evidence = ""
        if isinstance(evidence, list) and evidence:
            first_evidence = _jq_or(evidence[0], "")
        needles = [
            _jq_or(hit.get("id"), ""),
            _jq_or(hit.get("ref"), ""),
            _jq_or(hit.get("path"), ""),
            _jq_or(hit.get("title"), ""),
            first_evidence,
        ]
        needles = [str(needle) for needle in needles if str(needle)]
        if any(needle in corpus for needle in needles):
            count += 1
    return count


def project_size_bucket(root):
    # Bash project_size_bucket (434-445): git ls-files line count -> small(<200)/
    # medium(<1000)/large; non-git or failure -> 0 -> small.
    try:
        proc = subprocess.run(
            ["git", "-C", root, "ls-files"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        listing = proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else ""
    except OSError:
        listing = ""
    count = sum(1 for _ in listing.splitlines())
    if count < 200:
        return "small"
    if count < 1000:
        return "medium"
    return "large"


def run_type_from_state(run_dir):
    # Bash run_type_from_state (447-481): parse the first `mode:` line of STATE.md
    # (strip \r, **, a leading bullet; lowercase the value), map by keyword; else fall
    # back on PROBLEM/DIAGNOSIS -> bugfix, AUDIT/AUDIT-INTENT -> audit, else unknown.
    mode = ""
    state = run_dir + "/STATE.md"
    if os.path.isfile(state):
        for raw in _read_artifact(state).split("\n"):
            line = raw.replace("\r", "").replace("**", "")
            line = re.sub(r"^[ \t\r\f\v]*-[ \t\r\f\v]*", "", line, count=1)
            if line.lower().startswith("mode:"):
                mode = re.sub(r"^[Mm]ode:[ \t\r\f\v]*", "", line, count=1).lower()
                break
    if "fix" in mode or "bug" in mode:
        return "bugfix"
    if "audit" in mode:
        return "audit"
    if "doc" in mode:
        return "docs"
    if "refactor" in mode:
        return "refactor"
    if "feature" in mode:
        return "feature"
    if os.path.isfile(run_dir + "/PROBLEM.md") or os.path.isfile(run_dir + "/DIAGNOSIS.md"):
        return "bugfix"
    if os.path.isfile(run_dir + "/AUDIT.md") or os.path.isfile(run_dir + "/AUDIT-INTENT.md"):
        return "audit"
    return "unknown"


def run_scope_from_state(run_dir):
    # Port-era addition (no Bash counterpart): parse the first `Scope:` line of STATE.md
    # with the same line normalization as run_type_from_state and map it onto the
    # kimiflow scope tiers, so the global ledger can answer the recall-value-per-scope
    # question. Absent/unrecognized -> "unknown".
    scope = ""
    state = run_dir + "/STATE.md"
    if os.path.isfile(state):
        for raw in _read_artifact(state).split("\n"):
            line = raw.replace("\r", "").replace("**", "")
            line = re.sub(r"^[ \t\r\f\v]*-[ \t\r\f\v]*", "", line, count=1)
            if line.lower().startswith("scope:"):
                scope = re.sub(r"^[Ss]cope:[ \t\r\f\v]*", "", line, count=1).lower()
                break
    if "trivial" in scope:
        return "trivial"
    if "small" in scope:
        return "small"
    if "large" in scope:
        return "large"
    return "unknown"


def run_economics_row_json(root, run_dir):
    # Bash run_economics_row_json (2949-3022).
    project = root + "/.kimiflow/project"
    run_rel = paths.rel_path(root, run_dir)
    memory = project + "/MEMORY.md"
    user_memory = project + "/USER.md"
    recall_json = run_dir + "/RECALL.json"

    data = store.read_json(recall_json)
    if isinstance(data, dict):
        sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
        mem = sources.get("memory") if isinstance(sources.get("memory"), dict) else {}
        usr = sources.get("user_profile") if isinstance(sources.get("user_profile"), dict) else {}
        always_tokens = _to_int(_jq_or(mem.get("tokens_estimate"), 0))
        user_tokens = _to_int(_jq_or(usr.get("tokens_estimate"), 0))
    else:
        always_tokens = text.word_count_file(memory)
        user_tokens = text.word_count_file(user_memory)

    usage = attribution.usage_json(root, run_dir)
    hits = recall_hits_for_economics_json(
        recall_json, include_strategies=usage["contract"] == 1,
    )
    corpus = run_artifact_corpus(run_dir)
    recall_tokens = economics_hits_tokens(hits)
    hit_count = len(hits)
    used_hits = (
        len(usage["applied_ids"])
        if usage["contract"] == 1
        else economics_used_hits_count(hits, corpus)
    )
    avoided_per_hit = summaries._avoided_per_hit()
    avoided = used_hits * avoided_per_hit
    net = avoided - always_tokens - user_tokens - recall_tokens

    if hit_count == 0:
        result = "unknown"
    elif used_hits > 0 and net > 0:
        result = "saving"
    elif net < 0:
        result = "waste"
    else:
        result = "neutral"

    if hit_count == 0:
        confidence = "none"
    elif used_hits > 0:
        confidence = "medium"
    else:
        confidence = "low"

    basis = {
        "recall_json": paths.rel_path(root, recall_json),
        "heuristic": _HEURISTIC,
    }
    if usage["contract"] == 1:
        basis["usage_method"] = usage["method"]
    return {
        "schema_version": 1,
        "run": run_rel,
        "recorded_at": clock.iso_now(),
        "always_on_tokens": always_tokens,
        "user_memory_tokens": user_tokens,
        "recall_tokens": recall_tokens,
        "recall_hit_count": hit_count,
        "used_hit_count": used_hits,
        "estimated_avoided_scan_tokens": avoided,
        "net_estimated_tokens_saved": net,
        "result": result,
        "confidence": confidence,
        "basis": basis,
    }


def _to_int(value):
    # Bash reads tokens via `jq -r '... // 0'` then feeds `$(( ))` integer arithmetic, so
    # the value is always an integer; coerce defensively.
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _gnum(value):
    # jq `(value // 0) | tonumber? // 0`: null/false -> 0; number passthrough; numeric
    # string parsed; non-numeric string / array / object / true -> 0.
    value = _jq_or(value, 0)
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        try:
            if re.fullmatch(r"[+-]?[0-9]+", stripped):
                return int(stripped)
            return float(stripped)
        except ValueError:
            return 0
    return 0


def _dedupe_append(file, row, key):
    # Bash write_*_row existing-file branch: read JSONL (skip unparseable), drop rows whose
    # `key` matches row[key] (// "" both sides), append row; one compact line each + "\n".
    target = _jq_or(row.get(key), "")
    if os.path.exists(file):
        kept = [existing for existing in store.read_jsonl(file)
                if not (isinstance(existing, dict) and _jq_or(existing.get(key), "") == target)]
        rows_out = kept + [row]
    else:
        rows_out = [row]
    return "".join(contracts.dumps(existing) + "\n" for existing in rows_out)


def write_economics_row(root, row):
    # Bash write_economics_row (3024-3043): dedupe-by-.run append to the project ledger.
    project = root + "/.kimiflow/project"
    file = project + "/MEMORY-ECONOMICS.jsonl"
    os.makedirs(project, exist_ok=True)
    # Bash mktemp+mv is atomic and replaces a symlink target -> refuse_symlink=False; mktemp
    # creates the temp at 0600 and mv preserves it, so the ledger is 0600 (mode=0o600).
    store.atomic_write(file, _dedupe_append(file, row, "run"), mode=0o600, refuse_symlink=False)


def write_global_economics_row(root, run_dir, local_row):
    # Bash write_global_economics_row (3045-3122): the anonymized global row writer.
    if not global_metrics.enabled():
        return {"recorded": False, "reason": "disabled"}
    directory = global_metrics.base_dir()
    if not directory:
        return {"recorded": False, "reason": "home_unavailable"}
    salt = global_metrics.ensure_global_metrics_salt(directory)
    if not salt:
        return {"recorded": False, "reason": "salt_unavailable"}
    project_hash = global_metrics.anonymous_hash_id(salt, root)
    run_hash = global_metrics.anonymous_hash_id(salt, root + ":" + paths.rel_path(root, run_dir))
    if not project_hash or not run_hash:
        return {"recorded": False, "reason": "hash_unavailable"}

    host_env = os.environ.get("KIMIFLOW_HOST", "unknown")
    host = host_env if host_env in ("codex", "claude") else "unknown"
    avoided = _gnum(local_row.get("estimated_avoided_scan_tokens"))
    net = _gnum(local_row.get("net_estimated_tokens_saved"))
    result = local_row.get("result") if local_row.get("result") in _RESULTS else "unknown"
    confidence = local_row.get("confidence") if local_row.get("confidence") in _CONFIDENCES else "low"
    row = {
        "schema_version": 1,
        "recorded_day": clock.date_now(),
        "host": host,
        "run_type": run_type_from_state(run_dir),
        # scope is a port-era addition absent from the pinned Bash row (intentional
        # divergence, spec section 12) — the parity harness strips it before comparing.
        "scope": run_scope_from_state(run_dir),
        "project_size_bucket": project_size_bucket(root),
        "project_id": project_hash,
        "run_id": run_hash,
        "always_on_tokens": _gnum(local_row.get("always_on_tokens")),
        "user_memory_tokens": _gnum(local_row.get("user_memory_tokens")),
        "recall_tokens": _gnum(local_row.get("recall_tokens")),
        "recall_hit_count": _gnum(local_row.get("recall_hit_count")),
        "used_hit_count": _gnum(local_row.get("used_hit_count")),
        "estimated_avoided_scan_tokens": avoided,
        "net_estimated_tokens_saved": net,
        "estimated_savings_percent": (math.floor(net * 100 / avoided) if avoided > 0 else None),
        "result": result,
        "confidence": confidence,
        "basis": {
            "heuristic": "directional_estimate_only",
            "stores_content": False,
            "stores_paths": False,
            "local_only": True,
        },
    }

    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return {"recorded": False, "reason": "mkdir_failed"}
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    file = directory + "/token-economics.jsonl"
    try:
        # Bash distinguishes mktemp_failed/write_failed/move_failed; the stdlib atomic
        # writer collapses those into one failure surface (write_failed). The reachable
        # reasons (disabled/home_unavailable/salt_unavailable/success) match byte-for-byte.
        store.atomic_write(file, _dedupe_append(file, row, "run_id"), mode=0o600, refuse_symlink=False)
    except OSError:
        return {"recorded": False, "reason": "write_failed"}
    return {
        "recorded": True,
        "path": global_metrics.display_path(),
        "summary": summaries.global_efficiency_summary_json(),
    }


def record_run_economics_json(root, run_dir):
    # Bash record_run_economics_json (3124-3142): build the row, append it to the project
    # ledger, summarize the ledger, mirror to the global ledger, return the composite.
    row = run_economics_row_json(root, run_dir)
    write_economics_row(root, row)
    summary = summaries.economics_summary_json(root + "/.kimiflow/project/MEMORY-ECONOMICS.jsonl")
    global_update = write_global_economics_row(root, run_dir, row)
    return {
        "recorded": True,
        "path": ".kimiflow/project/MEMORY-ECONOMICS.jsonl",
        "row": row,
        "summary": summary,
        "global": global_update,
    }
