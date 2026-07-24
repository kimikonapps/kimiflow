"""Verified, path-selective project architecture deltas."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import PurePosixPath

from . import store


LOG_REL = os.path.join(".kimiflow", "project", "PROJECT-DELTAS.jsonl")
CONTEXT_NAME = "PROJECT-DELTA-CONTEXT.md"
MAX_ROW_PATHS = 4096
MAX_ROW_EVIDENCE = 16
MAX_LOG_BYTES = 4 * 1024 * 1024
_CONTEXT_RECEIPT = re.compile(
    r"\A<!--kimiflow:project-delta-context;schema=1;"
    r"rows=([a-f0-9]{24}(?:,[a-f0-9]{24})*);"
    r"paths_sha256=([a-f0-9]{64});"
    r"max_words=([0-9]{1,4})-->\n"
)
_CONFORMANCE = re.compile(
    r"<!-- kimiflow:conformance contract=1 status=converged "
    r"diff=passed strategy=passed architecture=(?:passed|not_applicable) "
    r"research=(?:stable|not_applicable) "
    r"scope=passed decisions=[0-9]+ checks=[0-9]+ "
    r"verifier=(?:folded|independent) source=current-run -->"
)
_VERIFICATION = (
    "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->"
)


class ProjectDeltaError(ValueError):
    pass


def _git(root, *args):
    try:
        return subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ProjectDeltaError("git unavailable") from exc


def _root_for(run_dir):
    lexical_run = os.path.abspath(run_dir)
    proc = _git(lexical_run, "rev-parse", "--show-toplevel")
    if proc.returncode:
        raise ProjectDeltaError("run is not inside a Git workspace")
    root = os.path.realpath(proc.stdout.decode("utf-8", "strict").strip())
    lexical_root = os.path.dirname(os.path.dirname(lexical_run))
    if os.path.realpath(lexical_root) != root:
        raise ProjectDeltaError("run must stay inside the Git workspace")
    try:
        store.require_local_path(lexical_root, lexical_run)
    except ValueError as exc:
        raise ProjectDeltaError("unsafe run path") from exc
    run_dir = os.path.realpath(lexical_run)
    if os.path.dirname(run_dir) != os.path.join(root, ".kimiflow"):
        raise ProjectDeltaError("run must be a direct .kimiflow child")
    return root, run_dir


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _state_values(text, key):
    values = []
    wanted = key.lower()
    for raw in text.splitlines():
        line = raw.strip().removeprefix("-").strip().replace("**", "")
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip().lower() == wanted:
            values.append(value.strip())
    return values


def _one_state(text, key):
    values = _state_values(text, key)
    if len(values) != 1 or not values[0]:
        raise ProjectDeltaError("%s missing or duplicated" % key)
    return values[0]


def _affected_paths(text):
    lines = text.splitlines()
    result = []
    for index, raw in enumerate(lines):
        line = raw.strip().removeprefix("-").strip().replace("**", "")
        if not line.lower().startswith("affected files:"):
            continue
        inline = line.split(":", 1)[1].strip()
        if inline:
            result.extend(part.strip() for part in inline.split(",") if part.strip())
        else:
            for candidate in lines[index + 1 :]:
                candidate = candidate.strip()
                if not candidate.startswith("- "):
                    break
                result.append(candidate[2:].strip().strip("`"))
        break
    return result


def _normalize_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProjectDeltaError("path invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise ProjectDeltaError("path invalid")
    if path.parts[0] in (".git", ".kimiflow"):
        raise ProjectDeltaError("project deltas govern repository product paths only")
    return path.as_posix()


def _clean_text(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ProjectDeltaError("%s invalid" % label)
    return " ".join(value.split())


def _blob_digest(root, commit, path):
    tree = _git(root, "ls-tree", "-z", commit, "--", path)
    if tree.returncode:
        return None
    entries = [entry for entry in tree.stdout.split(b"\0") if entry]
    if len(entries) != 1:
        return None
    try:
        metadata, encoded_path = entries[0].split(b"\t", 1)
        mode, kind, _object_id = metadata.split(b" ", 2)
        listed_path = encoded_path.decode("utf-8", "strict")
    except (UnicodeDecodeError, ValueError):
        return None
    if listed_path != path or kind != b"blob" or mode not in {b"100644", b"100755"}:
        return None
    proc = _git(root, "show", "%s:%s" % (commit, path))
    return None if proc.returncode else _sha(proc.stdout)


def _head(root):
    proc = _git(root, "rev-parse", "HEAD")
    if proc.returncode:
        raise ProjectDeltaError("HEAD unavailable")
    return proc.stdout.decode("ascii", "strict").strip()


def _terminal_basis(root, run_dir):
    with store.local_path_guard(root, run_dir):
        state_bytes = store.stable_local_file_bytes(root, os.path.join(run_dir, "STATE.md"))
        verification = store.stable_local_file_bytes(
            root, os.path.join(run_dir, "VERIFICATION.md")
        )
    state_text = state_bytes.decode("utf-8", "strict")
    verification_text = verification.decode("utf-8", "strict")
    if _one_state(state_text, "Status").lower().split(" ", 1)[0] != "done":
        raise ProjectDeltaError("run_not_done")
    for phase in ("Phase 6", "Phase 7"):
        if _one_state(state_text, phase).lower().split(" ", 1)[0] != "done":
            raise ProjectDeltaError("run_not_terminal")
    if _VERIFICATION not in verification_text or not _CONFORMANCE.search(verification_text):
        raise ProjectDeltaError("verification_not_converged")
    started = _one_state(state_text, "Run started head").split(" ", 1)[0]
    if not re.fullmatch(r"[a-f0-9]{40,64}", started):
        raise ProjectDeltaError("started_head_invalid")
    return state_text, verification, started


def _changed_paths(root, started, commit, paths):
    proc = _git(root, "diff", "--name-only", "-z", started, commit, "--", *paths)
    if proc.returncode:
        raise ProjectDeltaError("cannot prove committed delta")
    return [part.decode("utf-8", "strict") for part in proc.stdout.split(b"\0") if part]


def _canonical(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_rows(root, log_path):
    data = store.stable_local_file_bytes(root, log_path, missing_ok=True)
    if data is None:
        return []
    if len(data) > MAX_LOG_BYTES:
        raise ProjectDeltaError("project delta log too large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectDeltaError("project delta log encoding invalid") from exc
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = store.parse_json_object_strict(line)
        if row is None:
            raise ProjectDeltaError("project delta log malformed")
        rows.append(row)
    return rows


def record_delta(run_dir, *, summary, invariants, paths, write=False):
    root, run_dir = _root_for(run_dir)
    summary = _clean_text(summary, "summary", 600)
    if not isinstance(invariants, list) or not 1 <= len(invariants) <= 12:
        raise ProjectDeltaError("invariants invalid")
    invariants = [_clean_text(item, "invariant", 500) for item in invariants]
    paths = sorted({_normalize_path(item) for item in paths or []})
    if not paths:
        raise ProjectDeltaError("paths required")
    if len(paths) > MAX_ROW_PATHS:
        raise ProjectDeltaError("path limit exceeded")
    state_text, verification, started = _terminal_basis(root, run_dir)
    governed = {_normalize_path(item) for item in _affected_paths(state_text)}
    if not set(paths).issubset(governed):
        raise ProjectDeltaError("paths must be declared by STATE")
    commit = _head(root)
    changed = set(_changed_paths(root, started, commit, paths))
    if commit == started or changed != set(paths):
        raise ProjectDeltaError("no committed governed change")
    path_digests = {path: _blob_digest(root, commit, path) for path in paths}
    if any(digest is None for digest in path_digests.values()):
        raise ProjectDeltaError("governed path absent from commit")
    row = {
        "schema_version": 1,
        "status": "verified",
        "summary": summary,
        "paths": paths,
        "invariants": invariants,
        "commit": commit,
        "path_digests": path_digests,
        "evidence": [
            {"path": os.path.relpath(os.path.join(run_dir, "VERIFICATION.md"), root), "sha256": _sha(verification)}
        ],
        "source_run": os.path.relpath(run_dir, root),
    }
    row["id"] = _sha(_canonical(row).encode("utf-8"))[:24]
    serialized = _canonical(row) + "\n"
    if len(serialized.encode("utf-8")) > MAX_LOG_BYTES:
        raise ProjectDeltaError("project delta row too large")
    if write:
        project_dir = os.path.join(root, ".kimiflow", "project")
        store.ensure_local_directory(root, project_dir)
        log_path = os.path.join(root, LOG_REL)
        with store.local_path_guard(root, project_dir), store.path_lock(log_path):
            rows = _read_rows(root, log_path)
            if not any(existing.get("id") == row["id"] for existing in rows):
                rows.append(row)
            if len(rows) > 2048:
                raise ProjectDeltaError("project delta log row limit reached")
            payload = "".join(
                serialized if item.get("id") == row["id"] else _canonical(item) + "\n"
                for item in rows
            )
            if len(payload.encode("utf-8")) > MAX_LOG_BYTES:
                raise ProjectDeltaError("project delta log too large")
            store.atomic_write(log_path, payload, mode=0o600)
    return row


def _intersects(left, right):
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _row_current(root, row, head):
    required = {
        "schema_version", "status", "summary", "paths", "invariants", "commit",
        "path_digests", "evidence", "source_run", "id",
    }
    if not isinstance(row, dict) or set(row) != required:
        return False
    if row.get("schema_version") != 1 or row.get("status") != "verified":
        return False
    try:
        if _clean_text(row.get("summary"), "summary", 600) != row["summary"]:
            return False
        invariants = row.get("invariants")
        if (
            not isinstance(invariants, list)
            or not 1 <= len(invariants) <= 12
            or [
                _clean_text(item, "invariant", 500)
                for item in invariants
            ] != invariants
        ):
            return False
    except ProjectDeltaError:
        return False
    content = dict(row)
    row_id = content.pop("id")
    if not isinstance(row_id, str) or _sha(
        _canonical(content).encode("utf-8")
    )[:24] != row_id:
        return False
    commit = row.get("commit")
    if (
        not isinstance(commit, str)
        or re.fullmatch(r"[a-f0-9]{40,64}", commit) is None
        or _git(root, "merge-base", "--is-ancestor", commit, head).returncode
    ):
        return False
    paths = row.get("paths")
    digests = row.get("path_digests")
    if (
        not isinstance(paths, list)
        or not 1 <= len(paths) <= MAX_ROW_PATHS
        or any(not isinstance(path, str) for path in paths)
        or not isinstance(digests, dict)
        or any(
            not isinstance(path, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            for path, digest in digests.items()
        )
        or set(paths) != set(digests)
    ):
        return False
    try:
        normalized = [_normalize_path(path) for path in paths]
    except ProjectDeltaError:
        return False
    if normalized != sorted(set(paths)):
        return False
    for path in paths:
        if _blob_digest(root, head, path) != digests.get(path):
            return False
    evidence = row.get("evidence")
    if (
        not isinstance(evidence, list)
        or not 1 <= len(evidence) <= MAX_ROW_EVIDENCE
    ):
        return False
    seen_evidence = set()
    for item in evidence:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "sha256"}
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None
        ):
            return False
        try:
            rel = _normalize_evidence_path(item.get("path"))
            data = store.stable_local_file_bytes(root, os.path.join(root, *PurePosixPath(rel).parts))
        except (OSError, ValueError, ProjectDeltaError):
            return False
        evidence_key = (rel, item["sha256"])
        if evidence_key in seen_evidence:
            return False
        seen_evidence.add(evidence_key)
        if _sha(data) != item.get("sha256"):
            return False
    return True


def _normalize_evidence_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProjectDeltaError("evidence path invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise ProjectDeltaError("evidence path invalid")
    if len(path.parts) < 3 or path.parts[0] != ".kimiflow":
        raise ProjectDeltaError("evidence path invalid")
    return path.as_posix()


def _bounded_markdown(rows, affected_paths, max_words):
    marker = (
        "<!--kimiflow:project-delta-context;schema=1;rows=%s;"
        "paths_sha256=%s;max_words=%d-->"
        % (
            ",".join(row["id"] for row in rows),
            _sha(_canonical(affected_paths).encode("utf-8")),
            max_words,
        )
    )
    lines = ["# Relevant Project Deltas", ""]
    for row in rows:
        lines.extend(
            [
                "## %s" % row["id"],
                "Paths: %s" % ", ".join(row["paths"]),
                "Change: %s" % row["summary"],
                "Invariants: %s" % "; ".join(row["invariants"]),
                "Evidence: commit %s" % row["commit"],
                "",
            ]
        )
    words = "\n".join(lines).split()[: max_words - 1]
    byte_budget = max_words * 32
    bounded = []
    used = 0
    for word in words:
        separator = 1 if bounded else 0
        remaining = byte_budget - used - separator
        if remaining <= 0:
            break
        encoded = word.encode("utf-8")
        if len(encoded) > remaining:
            word = encoded[:remaining].decode("utf-8", "ignore")
            if not word:
                break
            encoded = word.encode("utf-8")
        bounded.append(word)
        used += separator + len(encoded)
        if len(encoded) == remaining:
            break
    body = " ".join(bounded)
    return marker + "\n" + body + ("\n" if body else "")


def affected_paths_from_state(payload):
    try:
        text = payload.decode("utf-8", "strict")
        return sorted({_normalize_path(path) for path in _affected_paths(text)})
    except (UnicodeError, ProjectDeltaError):
        return []


def context_payload_current(root, payload, current_affected_paths=None):
    """Return whether a rendered context still matches current verified rows."""
    try:
        text = payload.decode("utf-8", "strict")
        match = _CONTEXT_RECEIPT.match(text)
        if match is None:
            return False
        row_ids = match.group(1).split(",")
        affected_basis = match.group(2)
        max_words = int(match.group(3))
        if (
            not 1 <= len(row_ids) <= 32
            or len(set(row_ids)) != len(row_ids)
            or not 8 <= max_words <= 2000
        ):
            return False
        if current_affected_paths is None:
            return False
        current = sorted({_normalize_path(path) for path in current_affected_paths})
        if (
            not current
            or _sha(_canonical(current).encode("utf-8")) != affected_basis
        ):
            return False
        log_path = os.path.join(root, LOG_REL)
        project_dir = os.path.dirname(log_path)
        if not os.path.isdir(project_dir) or os.path.islink(project_dir):
            return False
        with store.local_path_guard(root, project_dir):
            rows = _read_rows(root, log_path)
        by_id = {
            row.get("id"): row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        selected = [by_id[row_id] for row_id in row_ids]
        head = _head(root)
        if not all(_row_current(root, row, head) for row in selected):
            return False
        return _bounded_markdown(selected, current, max_words).encode("utf-8") == payload
    except (KeyError, OSError, UnicodeError, ValueError, ProjectDeltaError):
        return False


def _remove_context(root, run_dir):
    target = os.path.join(run_dir, CONTEXT_NAME)
    with store.local_path_guard(root, run_dir) as anchor:
        try:
            info = os.stat(CONTEXT_NAME, dir_fd=anchor["descriptor"], follow_symlinks=False)
        except FileNotFoundError:
            return
        if not os.path.isfile(target) or os.path.islink(target):
            raise ProjectDeltaError("unsafe existing context path")
        os.unlink(CONTEXT_NAME, dir_fd=anchor["descriptor"])


def render_context(run_dir, affected_paths, *, write=False, max_rows=8, max_words=600):
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not 1 <= max_rows <= 32:
        raise ProjectDeltaError("max_rows invalid")
    if isinstance(max_words, bool) or not isinstance(max_words, int) or not 8 <= max_words <= 2000:
        raise ProjectDeltaError("max_words invalid")
    root, run_dir = _root_for(run_dir)
    affected = sorted({_normalize_path(path) for path in affected_paths or []})
    if not affected:
        raise ProjectDeltaError("affected paths required")
    log_path = os.path.join(root, LOG_REL)
    project_dir = os.path.dirname(log_path)
    rows = []
    if os.path.isdir(project_dir) and not os.path.islink(project_dir):
        with store.local_path_guard(root, project_dir):
            rows = _read_rows(root, log_path)
    head = _head(root)
    selected = [
        row for row in reversed(rows)
        if _row_current(root, row, head)
        and any(_intersects(path, wanted) for path in row["paths"] for wanted in affected)
    ][:max_rows]
    markdown = _bounded_markdown(selected, affected, max_words) if selected else ""
    if write:
        target = os.path.join(run_dir, CONTEXT_NAME)
        if selected:
            with store.local_path_guard(root, run_dir):
                store.atomic_write(target, markdown, mode=0o600)
        else:
            _remove_context(root, run_dir)
    return {"selected": len(selected), "markdown": markdown}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="project-delta")
    parser.add_argument("command", choices=("record", "context"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--summary")
    parser.add_argument("--invariant", action="append", default=[])
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--max-rows", type=int, default=8)
    parser.add_argument("--max-words", type=int, default=600)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            row = record_delta(
                args.run,
                summary=args.summary,
                invariants=args.invariant,
                paths=args.path,
                write=args.write,
            )
            result = {"status": "recorded" if args.write else "preview", "delta": row}
        else:
            result = render_context(
                args.run,
                args.path,
                write=args.write,
                max_rows=args.max_rows,
                max_words=args.max_words,
            )
            result["status"] = "written" if args.write else "preview"
    except ProjectDeltaError as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
