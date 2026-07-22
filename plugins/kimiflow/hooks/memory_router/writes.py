"""Learning-row write path: append_learning_row (security gate -> dedup ->
supersession -> append/rewrite). Behavioral port of the Bash append_learning_row
at kimiflow--v0.1.50 (2405-2512), with intentional divergences recorded in spec §12
(gate scope, secret-value quarantine, verbatim-preserving rewrite). Composes the
row-validation helpers (rows.py), path/scope helpers (paths.py), slugify (text.py),
the UTC clock (clock.py), jq-faithful serialization (contracts.py), and IO (store.py)."""
import os
import subprocess

from . import clock, contracts, paths, rows, store, text


class SecurityGateError(Exception):
    """Raised when the memory security gate is closed for a status=='current' write.
    Carries the gate reasons; the calling subcommand formats the stderr line + exit."""

    def __init__(self, reasons):
        self.reasons = reasons
        super().__init__("memory security gate closed: " + ",".join(reasons))


def source_commit(root):
    # Bash: git -C "$root" rev-parse --short HEAD 2>/dev/null || printf 'NOT VERIFIED'
    try:
        result = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "NOT VERIFIED"
    return result.stdout.strip()


def _identity_match(row, kind, scope, topic, summary, evidence):
    # Shared by dedup and supersession: everything except the fingerprint test.
    # The `(.X // default)` defaults mirror the Bash jq filters.
    return (
        row.get("kind", "") == kind
        and row.get("scope", "") == scope
        and row.get("topic", "") == topic
        and row.get("summary", "") == summary
        and row.get("evidence", []) == evidence
    )


def append_learning_row(root, kind, scope, topic, summary, evidence,
                        confidence, sensitivity, status):
    learnings = paths.rows_path_for_scope(root, scope)
    with store.path_lock(learnings):
        return _append_learning_row_locked(
            root, kind, scope, topic, summary, evidence,
            confidence, sensitivity, status,
        )


def _append_learning_row_locked(root, kind, scope, topic, summary, evidence,
                                confidence, sensitivity, status):
    project = os.path.join(root, ".kimiflow", "project")
    learnings = paths.rows_path_for_scope(root, scope)
    os.makedirs(project, exist_ok=True)

    # Gate scope widened from summary-only to summary+topic+evidence (audit fix B3-P3,
    # spec §12). Fields are newline-joined: the phrase windows use `.` (no DOTALL), so
    # patterns cannot match across field boundaries. Bare secret VALUES never close the
    # gate — they force sensitivity=security, which blocks vault-sync candidacy.
    scan_text = "\n".join([summary, topic] + [ref for ref in evidence if ref])
    security_scan = rows.memory_security_json(scan_text)
    if status == "current" and not security_scan["ok"]:
        raise SecurityGateError(security_scan["reasons"])
    if sensitivity != "security" and rows.has_secret_value(scan_text):
        sensitivity = "security"

    stored_evidence = rows.sanitize_evidence_json(root, evidence)
    fingerprints = rows.evidence_fingerprints_json(root, stored_evidence)

    raw_lines = store.read_jsonl_with_lines(learnings)
    existing = [row for _, row in raw_lines if row is not None]
    file_exists = os.path.isfile(learnings)

    # Dedup: first row matching identity + identical fingerprints + current status.
    # Bash: `... | .[0].id // ""` then `[ -n "$existing_id" ]` (empty id -> no dedup).
    dedup = next(
        (r for r in existing
         if _identity_match(r, kind, scope, topic, summary, stored_evidence)
         and r.get("evidence_fingerprints", []) == fingerprints
         and r.get("status", "current") == "current"),
        None,
    )
    if dedup is not None:
        existing_id = dedup.get("id", "")
        if existing_id:
            return existing_id

    src_commit = source_commit(root)
    new_id = "%s_%s_%s_%d" % (
        paths.id_prefix_for_scope(scope), clock.date_compact(),
        text.slugify(topic), os.getpid(),
    )

    # Supersession: only when status==current AND the file existed. Mark every
    # current identity-match whose fingerprints DIFFER (content changed under the
    # same evidence refs). Bash sets {status, superseded_by, superseded_at}.
    if status == "current" and file_exists:
        superseded_at = clock.date_now()
        for r in existing:
            if (
                r.get("status", "current") == "current"
                and _identity_match(r, kind, scope, topic, summary, stored_evidence)
                and r.get("evidence_fingerprints", []) != fingerprints
            ):
                r["status"] = "superseded"
                r["superseded_by"] = new_id
                r["superseded_at"] = superseded_at

    new_row = {
        "id": new_id,
        "kind": kind,
        "scope": scope,
        "topic": topic,
        "summary": summary,
        "evidence": stored_evidence,
        "evidence_fingerprints": fingerprints,
        "security_scan": security_scan,
        "confidence": confidence,
        "sensitivity": sensitivity,
        "last_verified": clock.date_now(),
        "source_commit": src_commit,
        "status": status,
    }

    if status == "current" and file_exists:
        # Rewrite path: re-serialize parsed rows (with supersession marks) in place,
        # keep blank/malformed lines verbatim (spec §12 — the Bash `jq` rewrite dropped
        # them while the append path kept them), then add the new row. Atomic like
        # Bash's `jq ... > tmp; mv tmp learnings`. refuse_symlink=False matches Bash
        # `mv` (replaces a symlinked rows file rather than writing through it).
        lines = [raw if row is None else contracts.dumps(row) for raw, row in raw_lines]
        lines.append(contracts.dumps(new_row))
        store.atomic_write(
            learnings,
            "".join(line + "\n" for line in lines),
            refuse_symlink=False,
        )
    else:
        # Append path: Bash `printf '%s\n' "$row" >> "$learnings"`. Preserves the
        # existing (incl. malformed) bytes and creates the file when absent.
        store.append_line(learnings, contracts.dumps(new_row) + "\n")

    return new_id
