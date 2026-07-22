"""`verify-run` subcommand + the shared `resolve_run_dir`. Behavioral port of the Bash
cmd_verify_run (3417-3505) + resolve_run_dir (2646-2654) at kimiflow--v0.1.50. verify-run
reads a run's LEARNING-REVIEW.md and emits a tab-separated LEARNING_REVIEW gate line with
exit 0 (OPEN) / 1 (CLOSED); arg/run errors exit 2. (stdout here is TEXT, not JSON.)"""
import os
import re
import sys

from . import contracts, paths, rows, store
from .cli import die, resolve_root, usage

_RECORDED_RE = re.compile(r"Recorded:[ \t\r\f\v]+learn_")


def resolve_run_dir(root, run):
    # Bash resolve_run_dir (2646-2654). CRUCIAL: every caller invokes it inside `$( )`
    # command substitution (Bash 3298/3431), so its `die ... 2` kills only the SUBSHELL --
    # the message reaches stderr but the exit code is DISCARDED and the caller receives an
    # EMPTY run_dir, then limps on. We replicate that exactly: write the die line to stderr
    # and return "" (NOT a clean exit 2). See spec 12.
    if not run:
        die("run path required", 2)
        return ""
    if run == "." and os.environ.get("KIMIFLOW_PINNED_RUN_CWD") == "1":
        try:
            info = os.stat(".", follow_symlinks=False)
            expected = (
                int(os.environ.get("KIMIFLOW_PINNED_RUN_DEVICE", "")),
                int(os.environ.get("KIMIFLOW_PINNED_RUN_INODE", "")),
            )
        except (OSError, ValueError):
            die("pinned run directory is no longer reachable", 2)
            return ""
        if not os.path.isdir(".") or (info.st_dev, info.st_ino) != expected:
            die("pinned run directory identity changed", 2)
            return ""
        return "."
    if not run.startswith("/"):
        run = root + "/" + run
    if not os.path.isdir(run):
        die("run directory not found: %s" % run, 2)
        return ""
    return os.path.abspath(run)


def _jq_or(value, default):
    return default if value is None or value is False else value


def _review_lines(path):
    # Read \r-faithfully (newline="") and split on \n, matching awk's record split: awk does
    # not strip \r, so a CRLF review would keep the \r in field values. Unreachable (the
    # writer emits \n), but kept consistent with the package's \r-faithful readers.
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read().split("\n")


def _first_colonspace_value(lines, prefix):
    # awk -F': ' '/^<prefix>/ {print $2; exit}': first line starting with `prefix`, value =
    # the second ": "-delimited field (or "" when the line has no ": ").
    for line in lines:
        if line.startswith(prefix):
            parts = line.split(": ")
            return parts[1] if len(parts) > 1 else ""
    return ""


def _recorded_ids(lines):
    # awk '/^Recorded:[[:space:]]+learn_/ {print $2}': field 2 (default FS = space/tab runs,
    # trimmed) of each matching line, in file order.
    ids = []
    for line in lines:
        if _RECORDED_RE.match(line):
            fields = re.split(r"[ \t]+", line.strip(" \t"))
            ids.append(fields[1] if len(fields) > 1 else "")
    return ids


def run(argv):
    root = ""
    run_arg = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--run":
            i += 1
            run_arg = argv[i] if i < len(argv) else ""
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("verify-run: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    run_dir = resolve_run_dir(root, run_arg)   # may be "" (Bash subshell-die quirk; see resolve_run_dir)

    # Bash `review="$run_dir/LEARNING-REVIEW.md"` -- a plain string concat, so an empty
    # run_dir yields "/LEARNING-REVIEW.md" (NOT os.path.join's "LEARNING-REVIEW.md").
    review = run_dir + "/LEARNING-REVIEW.md"
    rel_review = paths.rel_path(root, review)
    if not os.path.isfile(review):
        sys.stdout.write("LEARNING_REVIEW\tCLOSED\treason=missing_review\tpath=%s\n" % rel_review)
        return 1

    lines = _review_lines(review)
    status = _first_colonspace_value(lines, "Status:")

    if status == "recorded":
        ids = _recorded_ids(lines)
        if len(ids) == 0:
            sys.stdout.write("LEARNING_REVIEW\tCLOSED\treason=missing_recorded_ids\tpath=%s\n" % rel_review)
            return 1
        learnings = os.path.join(root, ".kimiflow", "project", "LEARNINGS.jsonl")
        if not os.path.isfile(learnings):
            sys.stdout.write("LEARNING_REVIEW\tCLOSED\treason=missing_learnings\tpath=%s\n" % rel_review)
            return 1
        current_rows = [r for r in store.read_jsonl(learnings)
                        if isinstance(r, dict) and _jq_or(r.get("status"), "current") == "current"]
        current_ids = [r.get("id") for r in current_rows]
        missing = [hid for hid in ids if hid not in current_ids]
        if missing:
            sys.stdout.write(
                "LEARNING_REVIEW\tCLOSED\treason=recorded_ids_missing_or_not_current\tids=%s\tpath=%s\n"
                % (",".join(missing), rel_review))
            return 1
        failures = []
        for hid in ids:
            row = next((r for r in current_rows if r.get("id") == hid), {})
            evidence = _jq_or(row.get("evidence"), [])
            stored = _jq_or(row.get("evidence_fingerprints"), [])
            if not isinstance(stored, list):
                stored = []   # non-list is unreachable (recorder writes a list); avoid a TypeError
            if len(stored) == 0:
                failures.append((hid, "missing_evidence_fingerprints"))
                continue
            current_fp = rows.evidence_fingerprints_json(root, evidence)
            # Bash compares the jq -c serializations (order-sensitive), not the objects.
            if contracts.dumps(stored) != contracts.dumps(current_fp):
                failures.append((hid, "evidence_changed_or_missing"))
        if not failures:
            sys.stdout.write("LEARNING_REVIEW\tOPEN\tstatus=recorded\tfreshness=current\tpath=%s\n" % rel_review)
            return 0
        csv = ",".join("%s:%s" % (hid, reason) for hid, reason in failures)
        sys.stdout.write("LEARNING_REVIEW\tCLOSED\treason=evidence_stale\tids=%s\tpath=%s\n" % (csv, rel_review))
        return 1

    if status == "skipped":
        reason = _first_colonspace_value(lines, "Skip reason:")
        if reason:
            sys.stdout.write("LEARNING_REVIEW\tOPEN\tstatus=skipped\treason=%s\tpath=%s\n" % (reason, rel_review))
            return 0
        sys.stdout.write("LEARNING_REVIEW\tCLOSED\treason=missing_skip_reason\tpath=%s\n" % rel_review)
        return 1

    sys.stdout.write("LEARNING_REVIEW\tCLOSED\treason=invalid_status\tstatus=%s\tpath=%s\n"
                     % (status if status else "missing", rel_review))
    return 1
