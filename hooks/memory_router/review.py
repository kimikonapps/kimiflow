"""`review-run` subcommand. Behavioral port of the Bash cmd_review_run (3281-3415) @
kimiflow--v0.1.50 - the run-completion learning gate. It scans a finished run's artifacts
for four reusable-learning candidates (one per question/kind), runs each through a quality
gate, and on --write records them as learnings, refreshes bounded memory + curate + index +
propose, records run economics, and writes LEARNING-REVIEW.md + RUN-LIFECYCLE.json/.md.
--skip <reason> short-circuits to a skipped review. The last + largest subcommand (wires
13/13). Heavy deps already ported (economics Plan 25; propose/curate/index/append_learning_row/
write_bounded_memory/status_json/classify_text/resolve_run_dir earlier)."""
import contextlib
from datetime import date
import io
import json
import os
import re
import string
import sys

from . import (classify, clock, contracts, economics, index as index_mod, memory_md,
               paths, propose, rows, status as status_mod, store, text, writes)
from .cli import die, resolve_root, usage
from .curate import run as curate_run
from .runs import resolve_run_dir

_SPACE = r"[ \t\r\f\v]"

# cmd_review_run candidate tuples (Bash 3340-3347): question, kind, topic, file-list.
_CANDIDATE_SPECS = (
    ("what_was_learned", "learned", "run-learning",
     ("RESEARCH.md", "DIAGNOSIS.md", "VERIFICATION.md")),
    ("which_project_rule_was_confirmed", "project_rule_confirmed", "project-rules",
     ("ACCEPTANCE.md", "STANDARDS.md", "PLAN.md")),
    ("which_trap_or_pitfall_appeared", "trap_or_pitfall", "pitfalls",
     ("CODE-REVIEW.md", "ADVISORIES.md", "CURRENT-STATE.md")),
    ("which_decision_remains_important", "important_decision", "decisions",
     ("PLAN.md", "RESEARCH.md", "DIAGNOSIS.md")),
)

# structured_learning_tsv kind anchors (Bash 2685-2696): matched against ascii_lower(line).
_STRUCT_KIND_RE = {
    "learned": re.compile(
        r"^(what was learned|learned|learning|lesson learned|gelernt|was gelernt wurde|"
        r"erkenntnis)" + _SPACE + r"*:"),
    "project_rule_confirmed": re.compile(
        r"^(which project rule was confirmed|project rule confirmed|rule confirmed|"
        r"confirmed rule|project rule|projektregel|bestaetigte regel)" + _SPACE + r"*:"),
    "trap_or_pitfall": re.compile(
        r"^(which trap or pitfall appeared|pitfall|trap|risk|avoid|falle|risiko|achtung)"
        + _SPACE + r"*:"),
    "important_decision": re.compile(
        r"^(which decision remains important|important decision|decision|decided|"
        r"entscheidung|wichtige entscheidung)" + _SPACE + r"*:"),
}

# quality_gate_json regexes (Bash 2352, 2361-2372): searched against ascii_lower(summary).
_GENERIC_RE = re.compile(
    r"^(done|fixed|updated|changed|implemented|cleanup|misc|note|todo)["
    + re.escape(string.punctuation) + r" \t\r\f\v]*$"
    r"|(^|" + _SPACE + r")(various|several|stuff|things|something|some files)("
    + _SPACE + r"|$)")
_QUALITY_KIND_RE = {
    "project_rule_confirmed": re.compile(
        r"(rule|confirmed|every|must|always|convention|standard|should|regel|"
        "best\u00e4tigt|bestaetigt|muss|immer|jede|jedes|konvention)"),  # umlaut form of bestaetigt
    "trap_or_pitfall": re.compile(
        r"(pitfall|trap|avoid|risk|do not|don't|never|falle|risiko|vermeiden|nicht|"
        r"niemals|achtung|surprise)"),
    "important_decision": re.compile(
        r"(decision|decided|choose|chosen|keep|use|because|trade-off|instead|"
        r"entscheidung|entschieden|bleibt|nutzen|beibehalten)"),
}
_QUALITY_KIND_REASON = {
    "project_rule_confirmed": "project_rule_without_rule",
    "trap_or_pitfall": "pitfall_without_avoidance",
    "important_decision": "decision_without_decision",
}

# Durable `learned` rows use one tiny universal context contract. Requiring it
# for the kind avoids an incomplete ecosystem list or unreliable name guessing.
_SCOPE_MARKER_RE = re.compile(
    r"(?:^|;)\s*(?:scope|applies(?:[ \t]+to)?)\s*[:=]\s*([^;]{3,})",
    re.IGNORECASE,
)
_VERIFIED_MARKER_RE = re.compile(
    r"(?:^|;)\s*(?:verified(?:[ \t]+by)?|basis)\s*[:=]\s*([^;@]{3,}?)"
    r"[ \t]*@[ \t]*(\d{4}-\d{2}-\d{2})(?=[ \t]*(?:;|$))",
    re.IGNORECASE,
)


def _jq_or(value, default):
    # jq `value // default`: substitute only when value is null (None) or false.
    return default if value is None or value is False else value


def _nav(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _read_lines(path):
    # newline="" preserves \r like awk (which splits records on \n only).
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read().split("\n")
    except (OSError, UnicodeDecodeError):
        return []


def quality_gate_json(kind, summary, evidence):
    # Bash quality_gate_json (2339-2387).
    lower = text.ascii_lower(summary)
    words = sum(1 for token in re.split(r"[^A-Za-z0-9_-]+", summary) if token)
    security = rows.memory_security_json(summary)
    reasons = []
    if words < 7:
        reasons.append("too_short")
    if security.get("ok") is not True:
        reasons.append("security_scan_failed")
    if _GENERIC_RE.search(lower):
        reasons.append("too_generic")
    if len(evidence) == 0 or any(item == "NOT VERIFIED" for item in evidence):
        reasons.append("missing_verified_evidence")
    kind_re = _QUALITY_KIND_RE.get(kind)
    if kind_re is not None and not kind_re.search(lower):
        reasons.append(_QUALITY_KIND_REASON[kind])
    if kind == "learned":
        context_summary = summary
        structured_prefix = _STRUCT_KIND_RE["learned"].match(lower)
        if structured_prefix:
            context_summary = summary[structured_prefix.end():]
        scope_match = _SCOPE_MARKER_RE.search(context_summary)
        verification_match = _VERIFIED_MARKER_RE.search(context_summary)
        if (not scope_match or not scope_match.group(1).strip()
                or scope_match.group(1).strip().lower() in ("n/a", "none", "unknown")):
            reasons.append("learning_context_missing_scope")
        if not verification_match or not verification_match.group(1).strip():
            reasons.append("learning_context_missing_verification")
        else:
            try:
                date.fromisoformat(verification_match.group(2))
            except ValueError:
                reasons.append("learning_context_invalid_date")
    return {
        "ok": len(reasons) == 0,
        "words": words,
        "reasons": reasons,
        "security": security,
    }


def first_substantive_tsv(file):
    # Bash first_substantive_tsv (2656-2673): first non-empty, non-heading, non-fence line.
    if not os.path.isfile(file):
        return None
    for nr, raw in enumerate(_read_lines(file), start=1):
        line = raw.replace("\r", "").strip(" \t\r\f\v")
        if line == "":
            continue
        if re.match(r"^#{1,6}" + _SPACE, line):
            continue
        if line.startswith("```"):
            continue
        line = re.sub(_SPACE + r"+", " ", line)
        return "%d\t%s" % (nr, line)
    return None


def structured_learning_tsv(file, kind):
    # Bash structured_learning_tsv (2675-2717): first line whose ascii-lowered, bullet-/
    # blockquote-stripped form matches the kind anchor; keeps the original-case line.
    kind_re = _STRUCT_KIND_RE.get(kind)
    if kind_re is None or not os.path.isfile(file):
        return None
    for nr, raw in enumerate(_read_lines(file), start=1):
        line = raw.replace("\r", "").replace("**", "").strip(" \t\r\f\v")
        line = re.sub(r"^[-*]" + _SPACE + r"+", "", line)
        line = re.sub(r"^>" + _SPACE + r"+", "", line)
        if kind_re.match(text.ascii_lower(line)):
            if line != "":
                return "%d\t%s" % (nr, re.sub(_SPACE + r"+", " ", line))
    return None


def learning_summary_json(file, kind):
    # Bash learning_summary_json (2719-2736): structured first, else first-substantive.
    row = structured_learning_tsv(file, kind)
    source = "structured"
    if not row:
        row = first_substantive_tsv(file)
        source = "fallback"
    if not row:
        return None
    line, _, rest = row.partition("\t")
    summary = rest[:320]   # Bash `cut -f2- | cut -c1-320` (codepoint slice, spec 12 row 196)
    if not summary:
        return None
    return {"line": int(line), "summary": summary, "source": source}


def review_candidate_json(root, run_dir, question, kind, topic, files):
    # Bash review_candidate_json (2738-2787): the FIRST file that yields a non-skip summary.
    for name in files:
        path = run_dir + "/" + name
        if not os.path.isfile(path):
            continue
        info = learning_summary_json(path, kind)
        if info is None:
            continue
        summary = info["summary"]
        if not summary:
            continue
        rel = paths.rel_path(root, path)
        evidence = [rel + ":" + str(info["line"])]
        classification = classify.classify_text(summary)["classification"]
        target = classification["target"]
        sensitivity = classification["sensitivity"]
        confidence = classification["confidence"]
        if target == "skip":
            continue
        if target == "run_only":
            target = "project_memory"
        quality = quality_gate_json(kind, summary, evidence)
        return {
            "question": question,
            "kind": kind,
            "scope": "project",
            "topic": topic,
            "summary": summary,
            "evidence": evidence,
            "extraction_source": info["source"],
            "target": target,
            "sensitivity": sensitivity,
            "confidence": confidence,
            "quality": quality,
        }
    return None


def write_learning_review_markdown(path, run_rel, status, entries, skip_reason):
    # Bash write_learning_review_markdown (3144-3168).
    parts = ["# Learning Review\n\n"]
    parts.append("Run: %s\n" % run_rel)
    parts.append("Status: %s\n" % status)
    parts.append("Generated: %s\n\n" % clock.iso_now())
    if status == "skipped":
        parts.append("Skip reason: %s\n" % skip_reason)
    else:
        parts.append("## Four Questions\n\n")
        for entry in entries:
            quality_ok = _jq_or(_nav(entry, "quality", "ok"), False)
            quality_text = ("passed" if quality_ok
                            else "failed:" + ",".join(_jq_or(_nav(entry, "quality", "reasons"), [])))
            evidence = "\n".join("- " + item for item in _jq_or(entry.get("evidence"), []))
            block = (
                "### " + entry["question"] + "\n"
                + "Summary: " + _jq_or(entry.get("summary"), "") + "\n"
                + "Kind: " + _jq_or(entry.get("kind"), "") + "\n"
                + "Target: " + _jq_or(entry.get("target"), "") + "\n"
                + "Sensitivity: " + _jq_or(entry.get("sensitivity"), "") + "\n"
                + "Quality: " + quality_text + "\n"
                + "Evidence:\n" + evidence + "\n"
                + "Recorded: " + _jq_or(entry.get("recorded_id"), "pending") + "\n"
            )
            parts.append(block + "\n")   # jq -r appends a newline after each entry's string
    store.atomic_write(path, "".join(parts))


def run_lifecycle_json(root, run_dir, learning_status, review_path, recorded_count,
                       memory_updated, economics_update, notification):
    # Bash run_lifecycle_json (3171-3242).
    run_rel = paths.rel_path(root, run_dir)
    review_rel = paths.rel_path(root, review_path)
    snapshot = status_mod.status_json(root)
    curation_reasons = _jq_or(_nav(snapshot, "curation", "reasons"), [])
    sync_pending = _jq_or(_nav(snapshot, "provider", "sync", "pending_count"), 0)
    notif_pending = _jq_or(_nav(notification, "pending"), 0)
    next_actions = list(curation_reasons)
    if sync_pending > 0:
        next_actions.append("provider_sync_pending")
    if notif_pending > 0:
        next_actions.append("review_learning_proposals")
    return {
        "schema_version": 1,
        "run": run_rel,
        "generated_at": clock.iso_now(),
        "written": True,
        "status": learning_status,
        "paths": {
            "learning_review": review_rel,
            "lifecycle_json": paths.rel_path(root, run_dir + "/RUN-LIFECYCLE.json"),
            "lifecycle_markdown": paths.rel_path(root, run_dir + "/RUN-LIFECYCLE.md"),
            "provider_sync": ".kimiflow/project/VAULT-SYNC.md",
        },
        "learning": {
            "status": learning_status,
            "recorded_count": recorded_count,
            "memory_updated": memory_updated,
            "review_path": review_rel,
        },
        "usefulness": _jq_or(snapshot.get("usefulness"), {}),
        "economics": {
            "recorded": _nav(economics_update, "recorded") is True,
            "result": _jq_or(_nav(economics_update, "row", "result"), "unknown"),
            "confidence": _jq_or(_nav(economics_update, "row", "confidence"), "none"),
            "net_estimated_tokens_saved": _jq_or(
                _nav(economics_update, "row", "net_estimated_tokens_saved"), 0),
            "estimated_avoided_scan_tokens": _jq_or(
                _nav(economics_update, "row", "estimated_avoided_scan_tokens"), 0),
            "basis": "directional_estimate_only",
        },
        "curation": {
            "recommended": _nav(snapshot, "curation", "recommended") is True,
            "reasons": curation_reasons,
        },
        "provider_sync": {
            "status": _jq_or(_nav(snapshot, "provider", "sync", "status"), "unknown"),
            "pending_count": sync_pending,
            "direct_write_ready": _nav(snapshot, "provider", "sync", "direct_write_ready") is True,
            "path": ".kimiflow/project/VAULT-SYNC.md",
        },
        "proposals": {"notification": notification},
        "external_writes": {
            "performed": False,
            "reason": "review-run records local lifecycle state only; provider sync/write stays explicit",
        },
        "next_actions": sorted(set(next_actions)),
    }


def write_run_lifecycle_json(path, obj):
    # Bash write_run_lifecycle_json (3244-3248): `jq . > path` (pretty + trailing newline).
    store.atomic_write(path, contracts.dumps(obj, pretty=True) + "\n")


def _jq_bool(value):
    # jq -r rendering of a boolean.
    return "true" if value else "false"


def write_run_lifecycle_markdown(path, obj):
    # Bash write_run_lifecycle_markdown (3250-3279).
    learning = obj.get("learning", {})
    usefulness = obj.get("usefulness", {})
    eco = obj.get("economics", {})
    curation = obj.get("curation", {})
    provider_sync = obj.get("provider_sync", {})
    next_actions = _jq_or(obj.get("next_actions"), [])
    parts = ["# Run Lifecycle\n\n"]
    parts.append("Run: %s\n" % obj.get("run", ""))
    parts.append("Status: %s\n" % obj.get("status", ""))
    parts.append("Generated: %s\n\n" % obj.get("generated_at", ""))
    parts.append("## Learning\n\n")
    parts.append("- Recorded count: %s\n" % learning.get("recorded_count", ""))
    parts.append("- Memory updated: %s\n\n" % _jq_bool(learning.get("memory_updated")))
    parts.append("## Usefulness\n\n")
    parts.append("- Hot: %s\n" % _jq_or(_nav(usefulness, "hot", "count"), 0))
    parts.append("- Warm: %s\n" % _jq_or(_nav(usefulness, "warm", "count"), 0))
    parts.append("- Cold: %s\n" % _jq_or(_nav(usefulness, "cold", "count"), 0))
    parts.append("- Stale: %s\n\n" % _jq_or(_nav(usefulness, "stale", "count"), 0))
    parts.append("## Economics\n\n")
    parts.append("- Result: %s\n" % eco.get("result", ""))
    parts.append("- Confidence: %s\n" % eco.get("confidence", ""))
    parts.append("- Net estimated tokens saved: %s\n\n" % eco.get("net_estimated_tokens_saved", ""))
    parts.append("## Curation\n\n")
    parts.append("- Reasons: %s\n\n" % ", ".join(_jq_or(curation.get("reasons"), [])))
    parts.append("## Provider Sync\n\n")
    parts.append("- Status: %s\n" % provider_sync.get("status", ""))
    parts.append("- Pending: %s\n" % provider_sync.get("pending_count", ""))
    parts.append("- Direct write ready: %s\n\n" % _jq_bool(provider_sync.get("direct_write_ready")))
    parts.append("## Next Actions\n\n")
    if not next_actions:
        parts.append("- none\n")
    else:
        parts.append("\n".join("- " + action for action in next_actions) + "\n")
    store.atomic_write(path, "".join(parts))


def _capture_propose(root):
    # Bash `proposal_update="$(cmd_propose --root "$root" --write)"` (no set -e): captures
    # propose's compact stdout (stderr flows through). The reachable path - freshly-recorded
    # learnings, no prior proposal state - returns valid JSON carrying `.notification`.
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        propose.run(["--root", root, "--write"])
    try:
        parsed = json.loads(buffer.getvalue())
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def run(argv):
    root = ""
    run_arg = ""
    pretty = False
    write = False
    skip_reason = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--run":
            i += 1
            run_arg = argv[i] if i < len(argv) else ""
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg == "--skip":
            i += 1
            skip_reason = argv[i] if i < len(argv) else ""
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("review-run: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    run_dir = resolve_run_dir(root, run_arg)   # die-in-$() quirk: may be "" (spec 12 row 197)
    run_rel = paths.rel_path(root, run_dir)
    review = run_dir + "/LEARNING-REVIEW.md"
    memory_updated = False
    proposal_update = {}
    notification = {}
    economics_update = {"recorded": False}
    lifecycle_update = {"written": False}

    if skip_reason:
        if write:
            write_learning_review_markdown(review, run_rel, "skipped", [], skip_reason)
            economics_update = economics.record_run_economics_json(root, run_dir)
            lifecycle_update = run_lifecycle_json(root, run_dir, "skipped", review, 0, False,
                                                  economics_update, {})
            write_run_lifecycle_json(run_dir + "/RUN-LIFECYCLE.json", lifecycle_update)
            write_run_lifecycle_markdown(run_dir + "/RUN-LIFECYCLE.md", lifecycle_update)
        out = {
            "schema_version": 1,
            "status": "skipped",
            "run": run_rel,
            "review_path": paths.rel_path(root, review),
            "skip_reason": skip_reason,
            "written": write,
            "entries": [],
            "recorded_count": 0,
            "memory_updated": False,
            "economics": economics_update,
            "lifecycle": lifecycle_update,
        }
        contracts.json_print(out, pretty)
        return 0

    entries = []
    for question, kind, topic, files in _CANDIDATE_SPECS:
        candidate = review_candidate_json(root, run_dir, question, kind, topic, files)
        if candidate is not None:
            entries.append(candidate)

    count = len(entries)
    if count == 0:
        return die("review-run found no reusable learning candidates; pass --skip <reason> "
                   "if this run is intentionally trivial", 1)

    quality_failures = [e for e in entries if _jq_or(_nav(e, "quality", "ok"), False) is not True]
    if quality_failures:
        summary = ";".join(
            e["question"] + ":" + ",".join(_jq_or(_nav(e, "quality", "reasons"), []))
            for e in quality_failures)
        return die("review-run quality gate closed: %s" % summary, 1)

    if write:
        recorded = []
        for entry in entries:
            try:
                rid = writes.append_learning_row(
                    root, entry["kind"], entry["scope"], entry["topic"], entry["summary"],
                    entry["evidence"], entry["confidence"], entry["sensitivity"], "current")
            except writes.SecurityGateError as exc:
                # Bash append_learning_row prints this itself + returns 1; the port raises.
                sys.stderr.write("memory-router: memory security gate closed: %s\n"
                                 % ",".join(exc.reasons))
                return 1
            recorded.append(dict(entry, recorded_id=rid))
        entries = recorded
        memory_md.write_bounded_memory(root)
        memory_updated = True
        with contextlib.redirect_stdout(io.StringIO()):
            curate_run(["--root", root, "--write"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                index_mod.run(["--root", root, "--write"])
            except Exception:
                pass
        proposal_update = _capture_propose(root)
        notification = _jq_or(proposal_update.get("notification"), {})
        economics_update = economics.record_run_economics_json(root, run_dir)
        write_learning_review_markdown(review, run_rel, "recorded", entries, "")
        lifecycle_update = run_lifecycle_json(root, run_dir, "recorded", review, count, True,
                                              economics_update, notification)
        write_run_lifecycle_json(run_dir + "/RUN-LIFECYCLE.json", lifecycle_update)
        write_run_lifecycle_markdown(run_dir + "/RUN-LIFECYCLE.md", lifecycle_update)

    out = {
        "schema_version": 1,
        "status": ("recorded" if write else "preview"),
        "run": run_rel,
        "review_path": paths.rel_path(root, review),
        "written": write,
        "entries": entries,
        "recorded_count": sum(1 for e in entries if e.get("recorded_id") is not None),
        "memory_updated": memory_updated,
        "proposal_update": proposal_update,
        "notification": notification,
        "economics": economics_update,
        "lifecycle": lifecycle_update,
    }
    contracts.json_print(out, pretty)
    return 0
