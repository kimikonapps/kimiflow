"""Deterministic preview, quarantine, and restore for project learnings."""
import contextlib
import io
import os
import stat

from . import contracts, curate, memory_md, rows, store, summaries
from .cli import die, resolve_root, usage

_MAX_ROWS = 20


def _segments(path):
    """Read JSONL by its LF delimiter while retaining every original terminator."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return []
    result = []
    start = 0
    while True:
        end = text.find("\n", start)
        if end < 0:
            if start < len(text):
                result.append((text[start:], ""))
            break
        raw = text[start:end]
        if raw.endswith("\r"):
            result.append((raw[:-1], "\r\n"))
        else:
            result.append((raw, "\n"))
        start = end + 1
    return result


def _parsed(segment):
    return store.parse_json_object_strict(segment)


def _id_counts(entries):
    counts = {}
    for raw, _ending, _row in entries:
        for rid in store.top_level_string_values(raw, "id"):
            counts[rid] = counts.get(rid, 0) + 1
    return counts


def _evidence_is_current(root, row):
    evidence = row.get("evidence")
    stored = row.get("evidence_fingerprints")
    if not isinstance(evidence, list) or not evidence:
        return False
    if any(not isinstance(ref, str) or not ref
           or ref in ("NOT VERIFIED", "OUTSIDE_REPO") for ref in evidence):
        return False
    if not isinstance(stored, list) or not stored:
        return False
    if not all(isinstance(fp, dict) and fp.get("status") == "current" for fp in stored):
        return False
    current = rows.evidence_fingerprints_json(root, evidence)
    if not current or not all(isinstance(fp, dict) and fp.get("status") == "current"
                              for fp in current):
        return False
    return contracts.dumps(stored) == contracts.dumps(current)


def _refresh_derivatives(root):
    memory_md.write_bounded_memory(root)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        curate.refresh(root, allow_network=False)


def _file_mode(path):
    try:
        return stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    except OSError:
        return 0o644


def _write_and_refresh(root, path, original_entries, output_entries):
    original = "".join(raw + ending for raw, ending, _row in original_entries)
    mode = _file_mode(path)
    content = "".join(
        (contracts.dumps(row) if changed else raw) + ending
        for raw, ending, row, changed in output_entries
    )
    store.atomic_write(path, content, mode=mode, expected=original)
    try:
        _refresh_derivatives(root)
    except Exception as refresh_error:
        try:
            store.atomic_write(path, original, mode=mode, expected=content)
        except store.ConcurrentWriteError:
            latest = [(raw, ending, _parsed(raw)) for raw, ending in _segments(path)]
            rollback_rows = {}
            for (original_raw, _original_ending, original_row), (_raw, _ending, changed_row, changed) in zip(
                    original_entries, output_entries):
                if changed and isinstance(original_row, dict) and isinstance(changed_row, dict):
                    rollback_rows[changed_row.get("id")] = (changed_row, original_raw)
            latest_text = "".join(raw + ending for raw, ending, _row in latest)
            merged = []
            for raw, ending, row in latest:
                replacement = rollback_rows.get(row.get("id")) if isinstance(row, dict) else None
                if replacement is not None and row == replacement[0]:
                    raw = replacement[1]
                merged.append(raw + ending)
            store.atomic_write(path, "".join(merged), mode=mode, expected=latest_text)
        try:
            _refresh_derivatives(root)
        except Exception:
            pass
        raise refresh_error


def _restore(root, learnings, requested, write, pretty):
    entries = [(raw, ending, _parsed(raw)) for raw, ending in _segments(learnings)]
    counts = _id_counts(entries)
    matches = [row for _raw, _ending, row in entries
               if isinstance(row, dict) and row.get("id") == requested]
    quarantined = [row for row in matches if row.get("status") == "quarantined"]
    if not matches or not quarantined:
        reason = "not_quarantined"
    elif counts.get(requested, 0) != 1 or len(matches) != 1 or len(quarantined) != 1:
        reason = "duplicate_id"
    elif not _evidence_is_current(root, quarantined[0]):
        reason = "evidence_drift"
    else:
        reason = None

    if reason:
        contracts.json_print({
            "schema_version": 1,
            "status": "refused",
            "written": False,
            "restored_id": None,
            "reason": reason,
        }, pretty)
        return 1

    if write:
        output = []
        for raw, ending, row in entries:
            changed = row is quarantined[0]
            if changed:
                row = dict(row)
                row["status"] = "current"
            output.append((raw, ending, row, changed))
        try:
            _write_and_refresh(root, learnings, entries, output)
        except (store.ConcurrentWriteError, OSError) as exc:
            return die("lifecycle: %s; retry" % exc, 1)
    contracts.json_print({
        "schema_version": 1,
        "status": "restored" if write else "restore_preview",
        "written": write,
        "restored_id": requested,
        "reason": None,
    }, pretty)
    return 0


def _operate(root, learnings, usage_file, restore, write, pretty):
    if restore is not None:
        if not restore:
            return die("lifecycle: --restore requires a nonempty id", 2)
        return _restore(root, learnings, restore, write, pretty)

    raw_entries = [(raw, ending, _parsed(raw)) for raw, ending in _segments(learnings)]
    counts = _id_counts(raw_entries)
    utility = summaries.learning_utility_rows(learnings, usage_file, max_rows=None)
    eligible = [item["id"] for item in utility
                if item["quarantine_eligible"]
                and isinstance(item["id"], str) and item["id"]
                and counts.get(item["id"]) == 1]

    quarantined = []
    if write and eligible:
        selected = set(eligible)
        output = []
        for raw, ending, row in raw_entries:
            changed = (
                isinstance(row, dict) and row.get("id") in selected
                and (row.get("status") if row.get("status") not in (None, False)
                     else "current") == "current"
            )
            if changed:
                row = dict(row)
                row["status"] = "quarantined"
                quarantined.append(row["id"])
            output.append((raw, ending, row, changed))
        try:
            _write_and_refresh(root, learnings, raw_entries, output)
        except (store.ConcurrentWriteError, OSError) as exc:
            return die("lifecycle: %s; retry" % exc, 1)
    elif write:
        _refresh_derivatives(root)

    contracts.json_print({
        "schema_version": 1,
        "status": "quarantined" if quarantined else ("unchanged" if write else "preview"),
        "written": bool(quarantined),
        "utility_max_points": 5,
        "rows": utility[:_MAX_ROWS],
        "rows_omitted": max(0, len(utility) - _MAX_ROWS),
        "candidate_count": len(eligible),
        "candidate_ids": eligible[:_MAX_ROWS],
        "candidate_ids_omitted": max(0, len(eligible) - _MAX_ROWS),
        "quarantined_count": len(quarantined),
        "quarantined_ids": quarantined[:_MAX_ROWS],
        "quarantined_ids_omitted": max(0, len(quarantined) - _MAX_ROWS),
    }, pretty)
    return 0


def run(argv):
    root = ""
    pretty = False
    write = False
    restore = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--restore":
            i += 1
            restore = argv[i] if i < len(argv) else ""
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("lifecycle: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    project = os.path.join(root, ".kimiflow", "project")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    usage_file = os.path.join(project, "MEMORY-USAGE.json")
    try:
        store.require_local_path(root, learnings)
        store.require_local_path(root, usage_file)
    except ValueError as exc:
        return die("lifecycle: %s" % exc, 1)
    with store.path_lock(learnings):
        return _operate(root, learnings, usage_file, restore, write, pretty)
