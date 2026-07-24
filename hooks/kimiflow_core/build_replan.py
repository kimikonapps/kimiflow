"""Evidence-bound Phase-5 returns to planning."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import PurePosixPath

from memory_router import store


REPLAN_EVENTS = frozenset({"strategy_drift", "architecture_falsified", "research_stale"})
RECEIPT_NAME = "BUILD-REPLAN-EVIDENCE.json"
_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,63}$")


class BuildReplanError(ValueError):
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
        raise BuildReplanError("git unavailable") from exc


def _root_for(run_dir):
    lexical_run = os.path.abspath(run_dir)
    proc = _git(lexical_run, "rev-parse", "--show-toplevel")
    if proc.returncode:
        raise BuildReplanError("run is not inside a Git workspace")
    root = os.path.realpath(os.path.abspath(proc.stdout.decode("utf-8", "strict").strip()))
    lexical_root = os.path.dirname(os.path.dirname(lexical_run))
    if os.path.realpath(lexical_root) != root:
        raise BuildReplanError("run must stay inside the Git workspace")
    try:
        store.require_local_path(lexical_root, lexical_run)
    except ValueError as exc:
        raise BuildReplanError("unsafe run path") from exc
    run_dir = os.path.realpath(lexical_run)
    if os.path.dirname(run_dir) != os.path.join(root, ".kimiflow"):
        raise BuildReplanError("run must be a direct .kimiflow child")
    return root, run_dir


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _state_affected_paths(text):
    paths = []
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        line = raw.strip().removeprefix("-").strip().replace("**", "")
        if not line.lower().startswith("affected files:"):
            continue
        inline = line.split(":", 1)[1].strip()
        if inline:
            paths.extend(part.strip() for part in inline.split(",") if part.strip())
            continue
        for candidate in lines[index + 1 :]:
            stripped = candidate.strip()
            if not stripped.startswith("- "):
                break
            paths.append(stripped[2:].strip().strip("`"))
        break
    return paths


def _phase_five_active(text):
    values = []
    for raw in text.splitlines():
        line = raw.strip().removeprefix("-").strip().replace("**", "")
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == "phase 5":
            values.append(value.strip().lower().split(" ", 1)[0])
    return values == ["in-progress"]


def _normalize_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise BuildReplanError("affected path invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise BuildReplanError("affected path invalid")
    if path.parts[0] == ".git":
        raise BuildReplanError("affected path invalid")
    return path.as_posix()


def _blob_digest(root, spec):
    proc = _git(root, "show", spec)
    return None if proc.returncode else _sha(proc.stdout)


def _worktree_digest(root, rel):
    path = os.path.join(root, *PurePosixPath(rel).parts)
    if not os.path.lexists(path):
        return None
    if os.path.islink(path) or not os.path.isfile(path):
        raise BuildReplanError("affected path must be a regular file")
    return _sha(store.stable_local_file_bytes(root, path))


def _snapshot(root, paths):
    rows = []
    for rel in sorted(paths):
        status = _git(root, "status", "--porcelain=v1", "-z", "--", rel)
        if status.returncode:
            raise BuildReplanError("cannot inspect affected path")
        path = os.path.join(root, *PurePosixPath(rel).parts)
        mode = None
        if os.path.isfile(path) and not os.path.islink(path):
            mode = os.stat(path, follow_symlinks=False).st_mode & 0o777
        rows.append(
            {
                "path": rel,
                "status_sha256": _sha(status.stdout),
                "worktree_sha256": _worktree_digest(root, rel),
                "index_sha256": _blob_digest(root, ":" + rel),
                "head_sha256": _blob_digest(root, "HEAD:" + rel),
                "mode": mode,
            }
        )
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"sha256": _sha(canonical.encode("utf-8")), "files": rows}


def _validate_text(value, label, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise BuildReplanError("%s invalid" % label)
    return value.strip()


def _evidence_path(root, run_dir, relative):
    relative = _normalize_path(relative)
    target = os.path.abspath(os.path.join(run_dir, *PurePosixPath(relative).parts))
    try:
        if os.path.commonpath((run_dir, target)) != run_dir:
            raise BuildReplanError("evidence must stay inside the run")
    except ValueError as exc:
        raise BuildReplanError("evidence must stay inside the run") from exc
    if os.path.basename(target) in (RECEIPT_NAME, "PLAN.md", "STATE.md"):
        raise BuildReplanError("evidence file is not independent")
    store.require_local_path(root, target)
    return target, relative


def _current_basis(run_dir):
    root, run_dir = _root_for(run_dir)
    with store.local_path_guard(root, run_dir):
        state_bytes = store.stable_local_file_bytes(root, os.path.join(run_dir, "STATE.md"))
        plan_bytes = store.stable_local_file_bytes(root, os.path.join(run_dir, "PLAN.md"))
    state_text = state_bytes.decode("utf-8", "strict")
    if not _phase_five_active(state_text):
        raise BuildReplanError("phase_not_build")
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode:
        raise BuildReplanError("HEAD unavailable")
    return root, run_dir, state_text, plan_bytes, head.stdout.decode("ascii", "strict").strip()


def record_receipt(
    run_dir,
    *,
    event,
    decision,
    acceptance,
    assumption,
    falsifier,
    evidence,
    paths,
    write=False
):
    if event not in REPLAN_EVENTS:
        raise BuildReplanError("unsupported replan event")
    if not _TOKEN.fullmatch(decision or "") or not _TOKEN.fullmatch(acceptance or ""):
        raise BuildReplanError("decision or acceptance invalid")
    assumption = _validate_text(assumption, "assumption")
    falsifier = _validate_text(falsifier, "falsifier")
    root, run_dir, state_text, plan_bytes, head = _current_basis(run_dir)
    normalized = sorted({_normalize_path(path) for path in paths or []})
    governed = {_normalize_path(path) for path in _state_affected_paths(state_text)}
    if not normalized or not set(normalized).issubset(governed):
        raise BuildReplanError("affected paths must be declared by STATE")
    plan_text = plan_bytes.decode("utf-8", "strict")
    if not re.search(r"(?m)^Decision %s:" % re.escape(decision), plan_text):
        raise BuildReplanError("decision is not present in PLAN")
    if not re.search(r"(?m)^AC %s:.*\b%s\b" % (re.escape(decision), re.escape(acceptance)), plan_text):
        raise BuildReplanError("acceptance is not bound to the decision")
    evidence_path, evidence_rel = _evidence_path(root, run_dir, evidence)
    evidence_bytes = store.stable_local_file_bytes(root, evidence_path)
    receipt = {
        "schema_version": 1,
        "event": event,
        "decision": decision,
        "acceptance": acceptance,
        "disproven_assumption": assumption,
        "falsifier": falsifier,
        "plan_sha256": _sha(plan_bytes),
        "head": head,
        "affected_paths": normalized,
        "worktree_snapshot": _snapshot(root, normalized),
        "evidence": {"path": evidence_rel, "sha256": _sha(evidence_bytes)},
    }
    if write:
        target = os.path.join(run_dir, RECEIPT_NAME)
        payload = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        with store.local_path_guard(root, run_dir):
            store.atomic_write(target, payload, mode=0o600)
    return receipt


def _invalid(reason):
    return {"valid": False, "reason": reason}


def verify_receipt(run_dir, event):
    if event not in REPLAN_EVENTS:
        return _invalid("unsupported_event")
    try:
        root, run_dir, state_text, plan_bytes, head = _current_basis(run_dir)
        with store.local_path_guard(root, run_dir):
            raw = store.stable_local_file_bytes(
                root, os.path.join(run_dir, RECEIPT_NAME), missing_ok=True
            )
        if raw is None:
            return _invalid("receipt_missing")
        receipt = store.parse_json_object_strict(raw.decode("utf-8"))
        if receipt is None or receipt.get("schema_version") != 1:
            return _invalid("receipt_malformed")
        expected_keys = {
            "schema_version", "event", "decision", "acceptance", "disproven_assumption",
            "falsifier", "plan_sha256", "head", "affected_paths", "worktree_snapshot", "evidence",
        }
        if set(receipt) != expected_keys or receipt.get("event") != event:
            return _invalid("receipt_mismatch")
        if receipt.get("plan_sha256") != _sha(plan_bytes) or receipt.get("head") != head:
            return _invalid("plan_or_head_changed")
        paths = receipt.get("affected_paths")
        if (
            not isinstance(paths, list)
            or any(not isinstance(path, str) for path in paths)
            or paths != sorted(set(paths))
        ):
            return _invalid("affected_paths_invalid")
        governed = {_normalize_path(path) for path in _state_affected_paths(state_text)}
        normalized = [_normalize_path(path) for path in paths]
        if not normalized or not set(normalized).issubset(governed):
            return _invalid("affected_paths_invalid")
        if receipt.get("worktree_snapshot") != _snapshot(root, normalized):
            return _invalid("worktree_changed")
        evidence = receipt.get("evidence")
        if not isinstance(evidence, dict) or set(evidence) != {"path", "sha256"}:
            return _invalid("evidence_invalid")
        evidence_path, _ = _evidence_path(root, run_dir, evidence.get("path"))
        evidence_bytes = store.stable_local_file_bytes(root, evidence_path)
        if evidence.get("sha256") != _sha(evidence_bytes):
            return _invalid("evidence_changed")
        decision = receipt.get("decision")
        acceptance = receipt.get("acceptance")
        plan_text = plan_bytes.decode("utf-8", "strict")
        if (
            not isinstance(decision, str)
            or not isinstance(acceptance, str)
            or not re.search(r"(?m)^Decision %s:" % re.escape(decision), plan_text)
            or not re.search(
                r"(?m)^AC %s:.*\b%s\b" % (re.escape(decision), re.escape(acceptance)),
                plan_text,
            )
        ):
            return _invalid("decision_binding_changed")
        _validate_text(receipt.get("disproven_assumption"), "assumption")
        _validate_text(receipt.get("falsifier"), "falsifier")
        return {"valid": True, "reason": "current"}
    except BuildReplanError as exc:
        return _invalid(str(exc))
    except (OSError, UnicodeError, ValueError):
        return _invalid("receipt_malformed")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="build-replan")
    parser.add_argument("command", choices=("record", "verify"))
    parser.add_argument("--run", required=True)
    parser.add_argument("--event", required=True, choices=sorted(REPLAN_EVENTS))
    parser.add_argument("--decision")
    parser.add_argument("--acceptance")
    parser.add_argument("--assumption")
    parser.add_argument("--falsifier")
    parser.add_argument("--evidence")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            result = record_receipt(
                args.run,
                event=args.event,
                decision=args.decision,
                acceptance=args.acceptance,
                assumption=args.assumption,
                falsifier=args.falsifier,
                evidence=args.evidence,
                paths=args.path,
                write=args.write,
            )
            result = {"status": "recorded" if args.write else "preview", "receipt": result}
        else:
            result = verify_receipt(args.run, args.event)
    except BuildReplanError as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("valid", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
