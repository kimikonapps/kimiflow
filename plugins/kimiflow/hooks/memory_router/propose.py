"""`propose` subcommand: the learning-proposal lifecycle (preview / approve / reject /
apply). Behavioral port of the Bash cmd_propose (3822-3929) + its helpers (3555-3820) at
kimiflow--v0.1.50. Reuses rows.evidence_fingerprints_json, store.read_jsonl, clock, paths."""
import os

from . import clock, contracts, paths, store
from .cli import die, resolve_root, usage
from .rows import evidence_fingerprints_json

_PROPOSAL_KINDS = ("project_rule_confirmed", "important_decision", "learned", "trap_or_pitfall")


def _jq_or(value, default):
    return default if value is None or value is False else value


def _has_value(value):
    # jq `((value // "") | length) > 0`. The fields here (applied_at/apply_note/...) are
    # strings, where jq `length` == Python len; the number branch is unreachable.
    v = _jq_or(value, "")
    return len(v) > 0 if hasattr(v, "__len__") else v != 0


def _jq_join(items, sep):
    # jq `join(sep)`: null -> "", strings verbatim (non-string is unreachable for evidence).
    return sep.join("" if e is None else e if isinstance(e, str) else str(e) for e in items)


def _merge(obj, updates):
    # jq `obj + updates`: left key order preserved, shared keys take the new value in place.
    out = dict(obj)
    out.update(updates)
    return out


def current_evidence_backed_rows(learnings):
    # Bash 3555-3563: current, non-security, has-evidence, no NOT-VERIFIED/OUTSIDE_REPO.
    out = []
    for r in store.read_jsonl(learnings):
        if not isinstance(r, dict):
            continue
        if _jq_or(r.get("status"), "current") != "current":
            continue
        if _jq_or(r.get("sensitivity"), "normal") == "security":
            continue
        evidence = _jq_or(r.get("evidence"), [])
        if not (isinstance(evidence, list) and len(evidence) > 0):
            continue
        if any(e == "NOT VERIFIED" or e == "OUTSIDE_REPO" for e in evidence):
            continue
        out.append(r)
    return out


def _proposal_type(kind):
    if kind == "project_rule_confirmed":
        return "standard"
    if kind == "important_decision":
        return "decision"
    return "skill"


def _target_path(ptype):
    if ptype == "standard":
        return ".kimiflow/STANDARDS.md"
    if ptype == "decision":
        return ".kimiflow/DECISIONS.md"
    return ".kimiflow/project/PENDING-PROPOSALS.md"


def proposal_candidates_json(rows, state, now):
    # Bash proposal_candidates_json (3565-3609).
    out = []
    for row in rows:
        kind = _jq_or(row.get("kind"), "")
        if kind not in _PROPOSAL_KINDS:
            continue
        rid = _jq_or(row.get("id"), "")
        prev = {}
        for s in state:   # previous($id): LAST state row with (.id // "") == id
            if isinstance(s, dict) and _jq_or(s.get("id"), "") == rid:
                prev = s
        ptype = _proposal_type(kind)
        proposal = {
            "id": rid,
            "learning_id": rid,
            "type": ptype,
            "kind": _jq_or(row.get("kind"), "learning"),
            "target_path": _target_path(ptype),
            "summary": _jq_or(row.get("summary"), ""),
            "evidence": _jq_or(row.get("evidence"), []),
            "evidence_fingerprints": _jq_or(row.get("evidence_fingerprints"), []),
            "status": _jq_or(prev.get("status"), "pending"),
            "reason": _jq_or(prev.get("reason"), ""),
            "created_at": _jq_or(prev.get("created_at"), now),
            "updated_at": _jq_or(prev.get("updated_at"), now),
        }
        if _has_value(prev.get("applied_at")):
            proposal["applied_at"] = prev.get("applied_at")
        if _has_value(prev.get("apply_note")):
            proposal["apply_note"] = prev.get("apply_note")
        if _has_value(prev.get("skill_draft_path")):
            proposal["skill_draft_path"] = prev.get("skill_draft_path")
        out.append(proposal)
    return out


def proposal_freshness_failures_json(root, proposals):
    # Bash 3612-3637: per proposal, empty stored fingerprints -> missing; else compact-JSON
    # compare vs recomputed -> evidence_changed_or_missing. Order preserved.
    failures = []
    for prop in proposals:
        rid = _jq_or(prop.get("id"), "")
        evidence = _jq_or(prop.get("evidence"), [])
        stored = _jq_or(prop.get("evidence_fingerprints"), [])
        if not isinstance(stored, list):
            stored = []
        if len(stored) == 0:
            failures.append({"id": rid, "reason": "missing_evidence_fingerprints"})
            continue
        current = evidence_fingerprints_json(root, evidence)
        if contracts.dumps(stored) != contracts.dumps(current):
            failures.append({"id": rid, "reason": "evidence_changed_or_missing"})
    return failures


def mark_proposals_need_revalidation(proposals, failures, now):
    # Bash 3639-3656: mark failure-id proposals needs_revalidation (from_entries last-wins reason).
    ids = [f["id"] for f in failures]
    reasons = {}
    for f in failures:
        reasons[f["id"]] = f["reason"]
    out = []
    for p in proposals:
        if p.get("id") in ids:
            out.append(_merge(p, {
                "status": "needs_revalidation",
                "reason": _jq_or(reasons.get(p.get("id")), "evidence_changed_or_missing"),
                "updated_at": now,
            }))
        else:
            out.append(p)
    return out


def proposal_counts_json(proposals):
    # Bash 3658-3669.
    by_type = {}
    for p in proposals:
        t = _jq_or(p.get("type"), "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "total": len(proposals),
        "pending": sum(1 for p in proposals if _jq_or(p.get("status"), "pending") == "pending"),
        "approved": sum(1 for p in proposals if _jq_or(p.get("status"), "") == "approved"),
        "applied": sum(1 for p in proposals if _jq_or(p.get("status"), "") == "applied"),
        "rejected": sum(1 for p in proposals if _jq_or(p.get("status"), "") == "rejected"),
        "needs_revalidation": sum(1 for p in proposals if _jq_or(p.get("status"), "") == "needs_revalidation"),
        "by_type": by_type,
    }


def proposal_notification_json(proposals):
    # Bash 3671-3697.
    c = proposal_counts_json(proposals)
    return {
        "kind": "learning_proposals",
        "path": ".kimiflow/project/PENDING-PROPOSALS.md",
        "state_path": ".kimiflow/project/PROPOSALS.jsonl",
        "pending": c["pending"],
        "approved": c["approved"],
        "applied": c["applied"],
        "rejected": c["rejected"],
        "needs_revalidation": c["needs_revalidation"],
        "message": ("Learning proposals: %s pending, %s approved, %s applied, %s rejected, "
                    "%s need revalidation." % (c["pending"], c["approved"], c["applied"],
                                               c["rejected"], c["needs_revalidation"])),
    }


def write_proposals_state(path, proposals):
    # Bash 3699-3703: `jq -c '.[]' > path`. atomic_write (vs the bash `>` redirect; spec 12).
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    store.atomic_write(path, "".join(contracts.dumps(p) + "\n" for p in proposals))


def _candidate_lines(props, with_draft=False):
    if not props:
        return "No candidates."
    lines = []
    for p in props:
        line = "- [%s] %s (id: %s; evidence: %s" % (
            _jq_or(p.get("status"), "pending"), _jq_or(p.get("summary"), ""),
            _jq_or(p.get("id"), ""), _jq_join(_jq_or(p.get("evidence"), []), ", "))
        if with_draft and _has_value(p.get("skill_draft_path")):
            line += "; draft: " + _jq_or(p.get("skill_draft_path"), "")
        lines.append(line + ")")
    return "\n".join(lines)


def write_proposals_markdown(path, proposals):
    # Bash 3705-3732: header + Commands + 3 candidate sections (jq -r appends a trailing \n).
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    standards = [p for p in proposals if _jq_or(p.get("type"), "") == "standard"]
    decisions = [p for p in proposals if _jq_or(p.get("type"), "") == "decision"]
    skills = [p for p in proposals if _jq_or(p.get("type"), "") == "skill"]
    parts = [
        "# Pending Kimiflow Proposals\n\n",
        "Generated: %s\n" % clock.iso_now(),
        ("Policy: review-only proposals derived from current, evidence-backed local learnings. "
         "Standards and decisions may be applied to local `.kimiflow/` files after approval. "
         "Skill/workflow candidates remain manual-review only.\n\n"),
        "Commands:\n\n",
        "- Approve: `memory-router.sh propose --approve <id>`\n",
        "- Reject: `memory-router.sh propose --reject <id> --reason \"<why>\"`\n",
        "- Apply approved standards/decisions: `memory-router.sh propose --apply`\n\n",
        "## Standards Candidates\n\n",
        _candidate_lines(standards) + "\n",
        "\n## Decision Candidates\n\n",
        _candidate_lines(decisions) + "\n",
        "\n## Skill/Workflow Candidates\n\n",
        _candidate_lines(skills, with_draft=True) + "\n",
    ]
    store.atomic_write(path, "".join(parts))


def append_project_line(file, title, summary, line):
    # Bash 3734-3744: create `# <title>\n\n` if absent; grep -Fq dup -> no append (return
    # False); else append `line\n` (return True).
    os.makedirs(os.path.dirname(file) or ".", exist_ok=True)
    if not os.path.isfile(file):
        with open(file, "w", encoding="utf-8") as handle:
            handle.write("# %s\n\n" % title)
    try:
        with open(file, "r", encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError):
        content = ""
    if summary in content:   # grep -Fq (a single-line summary can't span a \n)
        return False
    store.append_line(file, line + "\n")
    return True


def write_skill_draft(root, prop):
    # Bash 3746-3770: write SKILL-DRAFTS/<id>.md, return its rel_path (no trailing newline).
    pid = prop.get("id")
    summary = prop.get("summary")
    evidence = _jq_join(_jq_or(prop.get("evidence"), []), ", ")
    draft_dir = os.path.join(root, ".kimiflow", "project", "SKILL-DRAFTS")
    draft_file = os.path.join(draft_dir, "%s.md" % pid)
    os.makedirs(draft_dir, exist_ok=True)
    body = (
        "# Skill Draft: %s\n\n" % pid
        + "Generated: %s\n" % clock.iso_now()
        + "Status: review-only\n"
        + "Source learning: %s\n" % pid
        + "Evidence: %s\n\n" % evidence
        + "## Candidate Behavior\n\n"
        + "%s\n\n" % summary
        + "## Review Instructions\n\n"
        + "- Verify the evidence is still current before editing any skill file.\n"
        + "- Keep the skill change small and specific to the repeated workflow lesson.\n"
        + "- Do not publish private, security, or local-path details.\n"
    )
    store.atomic_write(draft_file, body)
    return paths.rel_path(root, draft_file)


def apply_approved_proposals(root, proposals):
    # Bash 3772-3820: apply approved standards/decisions, draft skills.
    standards = os.path.join(root, ".kimiflow", "STANDARDS.md")
    decisions = os.path.join(root, ".kimiflow", "DECISIONS.md")
    applied = []
    manual = []
    skill_drafts = []
    appended_standards = 0
    appended_decisions = 0
    for prop in proposals:
        if _jq_or(prop.get("status"), "") != "approved":
            continue
        pid = prop.get("id")
        ptype = prop.get("type")
        summary = prop.get("summary")
        evidence = _jq_join(_jq_or(prop.get("evidence"), []), ", ")
        if ptype == "standard":
            line = "- %s (evidence: %s; learning: %s)" % (summary, evidence, pid)
            if append_project_line(standards, "Kimiflow Standards", summary, line):
                appended_standards += 1
            applied.append(pid)
        elif ptype == "decision":
            line = "- %s: %s (evidence: %s; learning: %s)" % (clock.date_now(), summary, evidence, pid)
            if append_project_line(decisions, "Kimiflow Decisions", summary, line):
                appended_decisions += 1
            applied.append(pid)
        else:
            draft_path = write_skill_draft(root, prop)
            manual.append(pid)
            skill_drafts.append({"id": pid, "path": draft_path})
    return {
        "applied_ids": applied,
        "manual_ids": manual,
        "skill_drafts": skill_drafts,
        "appended": {"standards": appended_standards, "decisions": appended_decisions},
    }


def run(argv):
    root = ""
    pretty = False
    write = False
    apply_flag = False
    reason = ""
    approve_ids = []
    reject_ids = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--write":
            write = True
        elif arg == "--approve":
            i += 1
            approve_ids.append(argv[i] if i < len(argv) else "")
            write = True
        elif arg == "--reject":
            i += 1
            reject_ids.append(argv[i] if i < len(argv) else "")
            write = True
        elif arg == "--reason":
            i += 1
            reason = argv[i] if i < len(argv) else ""
        elif arg == "--apply":
            apply_flag = True
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("propose: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    project = os.path.join(root, ".kimiflow", "project")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    proposal_md = os.path.join(project, "PENDING-PROPOSALS.md")
    proposal_state = os.path.join(project, "PROPOSALS.jsonl")
    rows = current_evidence_backed_rows(learnings)
    state = store.read_jsonl(proposal_state)
    now = clock.iso_now()
    proposals = proposal_candidates_json(rows, state, now)

    # NOTE: Bash 3851-3854 has an unknown-id gate, but it is DEAD CODE: `($known | index(.))`
    # rebinds `.` to $known itself, making it a subarray-self-search that always returns 0
    # (never null), so `missing` is always [] and the gate never fires. Bash silently accepts
    # an unknown --approve/--reject id (written, but no proposal matches it). The port omits
    # the gate to match (an exit-2 here would diverge). See spec 12.

    if approve_ids:
        targets = [p for p in proposals if p.get("id") in approve_ids]
        failures = proposal_freshness_failures_json(root, targets)
        if failures:
            proposals = mark_proposals_need_revalidation(proposals, failures, now)
            write_proposals_state(proposal_state, proposals)
            write_proposals_markdown(proposal_md, proposals)
            csv = ",".join("%s:%s" % (f["id"], f["reason"]) for f in failures)
            return die("propose: evidence stale; refresh learning review before approval: %s" % csv, 1)
        proposals = [_merge(p, {"status": "approved", "reason": "", "updated_at": now})
                     if p.get("id") in approve_ids else p for p in proposals]
    if reject_ids:
        proposals = [_merge(p, {"status": "rejected", "reason": reason, "updated_at": now})
                     if p.get("id") in reject_ids else p for p in proposals]

    apply_result = {"applied_ids": [], "manual_ids": [], "appended": {"standards": 0, "decisions": 0}}
    if apply_flag:
        targets = [p for p in proposals if _jq_or(p.get("status"), "") == "approved"]
        failures = proposal_freshness_failures_json(root, targets)
        if failures:
            proposals = mark_proposals_need_revalidation(proposals, failures, now)
            write_proposals_state(proposal_state, proposals)
            write_proposals_markdown(proposal_md, proposals)
            csv = ",".join("%s:%s" % (f["id"], f["reason"]) for f in failures)
            return die("propose: evidence stale; refresh learning review before apply: %s" % csv, 1)
        apply_result = apply_approved_proposals(root, proposals)
        applied_ids = apply_result["applied_ids"]
        manual_ids = apply_result["manual_ids"]
        draft_paths = {d["id"]: d["path"] for d in apply_result["skill_drafts"]}
        updated = []
        for p in proposals:
            pid = p.get("id")
            if pid in applied_ids:
                updated.append(_merge(p, {"status": "applied", "applied_at": now, "updated_at": now}))
            elif pid in manual_ids:
                updated.append(_merge(p, {
                    "status": "approved", "apply_note": "skill_draft_review",
                    "skill_draft_path": _jq_or(draft_paths.get(pid), ""), "updated_at": now}))
            else:
                updated.append(p)
        proposals = updated

    if write:
        write_proposals_state(proposal_state, proposals)
        write_proposals_markdown(proposal_md, proposals)

    out = {
        "schema_version": 1,
        "status": ("applied" if apply_flag else "written" if write else "preview"),
        "path": ".kimiflow/project/PENDING-PROPOSALS.md",
        "state_path": ".kimiflow/project/PROPOSALS.jsonl",
        "written": write,
        "proposals": proposal_counts_json(proposals),
        "apply_result": apply_result,
        "notification": proposal_notification_json(proposals),
    }
    contracts.json_print(out, pretty)
    return 0
