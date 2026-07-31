"""Deterministic current-byte basis for a Kimiflow run's affected paths."""

import hashlib
import json
import os
import stat
import subprocess
import sys

from . import active_run, project_map_status
from .atomic import atomic_write


BASIS_NAME = "CODEBASE-BASIS.json"
BASIS_KEYS = {"head", "affected_paths", "snapshot_sha256", "map_coverage"}


class BasisError(ValueError):
    pass


def _digest(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe_paths(paths):
    clean = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            raise BasisError("affected path is empty")
        if any(character in raw for character in ("\x00", "\n", "\r")):
            raise BasisError("affected path contains control characters")
        value = raw.strip().replace("\\", "/")
        normalized = os.path.normpath(value).replace(os.sep, "/")
        if (
            os.path.isabs(value)
            or normalized in (".", "..")
            or normalized.startswith("../")
            or normalized == ".git"
            or normalized.startswith(".git/")
        ):
            raise BasisError("affected path is unsafe: %s" % raw)
        clean.append(normalized)
    if len(clean) != len(set(clean)):
        raise BasisError("affected paths contain duplicates")
    return sorted(clean)


def _parents_exist_without_symlinks(root, relative):
    current = root
    for component in relative.split("/")[:-1]:
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise BasisError("affected path parent is unreadable: %s" % relative) from exc
        if stat.S_ISLNK(info.st_mode):
            raise BasisError("affected path parent is a symlink: %s" % relative)
        if not stat.S_ISDIR(info.st_mode):
            raise BasisError("affected path parent is not a directory: %s" % relative)
    return True


def _directory_digest(path):
    rows = []
    for base, directories, files in os.walk(path, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        retained = []
        for name in directories:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            info = os.lstat(full)
            if stat.S_ISLNK(info.st_mode):
                rows.append({
                    "path": rel,
                    "type": "symlink",
                    "sha256": _digest(os.readlink(full).encode("utf-8", "surrogateescape")),
                })
            elif stat.S_ISDIR(info.st_mode):
                rows.append({"path": rel, "type": "directory", "sha256": None})
                retained.append(name)
            else:
                raise BasisError("unsupported directory entry: %s" % rel)
        directories[:] = retained
        for name in files:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            info = os.lstat(full)
            if stat.S_ISREG(info.st_mode):
                rows.append({"path": rel, "type": "file", "sha256": _file_digest(full)})
            elif stat.S_ISLNK(info.st_mode):
                rows.append({
                    "path": rel,
                    "type": "symlink",
                    "sha256": _digest(os.readlink(full).encode("utf-8", "surrogateescape")),
                })
            else:
                raise BasisError("unsupported directory entry: %s" % rel)
    return _digest(_canonical(sorted(rows, key=lambda row: row["path"])))


def _path_row(root, relative):
    if not _parents_exist_without_symlinks(root, relative):
        return {"path": relative, "type": "missing", "sha256": None}
    path = os.path.join(root, *relative.split("/"))
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"path": relative, "type": "missing", "sha256": None}
    if stat.S_ISREG(info.st_mode):
        kind, digest = "file", _file_digest(path)
    elif stat.S_ISDIR(info.st_mode):
        kind, digest = "directory", _directory_digest(path)
    elif stat.S_ISLNK(info.st_mode):
        kind = "symlink"
        digest = _digest(os.readlink(path).encode("utf-8", "surrogateescape"))
    else:
        raise BasisError("unsupported affected path type: %s" % relative)
    return {"path": relative, "type": kind, "sha256": digest}


def _head(root):
    result = subprocess.run(
        ["git", "-C", root, "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    if result.returncode or not value:
        raise BasisError("git HEAD is unavailable")
    return value


def _map_coverage(root, paths):
    index = os.path.join(root, ".kimiflow", "project", "INDEX.json")
    base = {
        "status": "missing",
        "affected": len(paths),
        "mapped": 0,
        "unmapped": len(paths),
        "affected_stale": 0,
        "affected_unknown": 0,
        "phase2_depth": "full",
        "reason": "missing-index",
    }
    if not os.path.isfile(index) or os.path.islink(index):
        return base
    try:
        data = project_map_status.load_index(index)
        if not isinstance(data, dict):
            raise ValueError
        map_files, map_prefixes = project_map_status.build_map_scope(data)
        mapped = sum(
            1 for path in paths
            if project_map_status.path_is_mapped(path, map_files, map_prefixes)
        )
        stale = 0
        unknown = 0
        for section in project_map_status.section_names_sorted(data):
            line = project_map_status.section_status(root, data, section, paths)
            if "affected=yes" not in line:
                continue
            if "\tstale\t" in line or "\tpotentially_stale\t" in line:
                stale += 1
            elif "\tunknown\t" in line:
                unknown += 1
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError):
        value = dict(base)
        value.update({"status": "unknown", "reason": "invalid-index"})
        return value
    unmapped = len(paths) - mapped
    if unmapped:
        status, depth, reason = "partial", "full", "unmapped-affected-paths"
    elif stale:
        status, depth, reason = "stale", "targeted", "mapped-but-stale"
    elif unknown:
        status, depth, reason = "unknown", "targeted", "mapped-but-unknown"
    else:
        status, depth, reason = "covered", "compressed", "affected-paths-covered-current"
    return {
        "status": status,
        "affected": len(paths),
        "mapped": mapped,
        "unmapped": unmapped,
        "affected_stale": stale,
        "affected_unknown": unknown,
        "phase2_depth": depth,
        "reason": reason,
    }


def capture(root, affected_paths):
    root = os.path.realpath(root)
    paths = _safe_paths(affected_paths)
    if not paths:
        raise BasisError("affected paths are empty")
    rows = [_path_row(root, path) for path in paths]
    return {
        "head": _head(root),
        "affected_paths": rows,
        "snapshot_sha256": _digest(_canonical(rows)),
        "map_coverage": _map_coverage(root, paths),
    }


def verify(root, basis, affected_paths):
    if not isinstance(basis, dict) or set(basis) != BASIS_KEYS:
        return ["basis_keys_invalid"]
    try:
        current = capture(root, affected_paths)
    except BasisError:
        return ["basis_capture_failed"]
    details = []
    stored_rows = basis.get("affected_paths")
    if not isinstance(stored_rows, list):
        return ["affected_paths_invalid"]
    stored_names = [row.get("path") for row in stored_rows if isinstance(row, dict)]
    current_names = [row["path"] for row in current["affected_paths"]]
    if stored_names != current_names:
        details.append("affected_paths_changed")
    stored_by_path = {
        row.get("path"): row for row in stored_rows if isinstance(row, dict)
    }
    for row in current["affected_paths"]:
        if stored_by_path.get(row["path"]) != row:
            details.append("affected_path_drift:%s" % row["path"])
    if basis.get("head") != current["head"]:
        details.append("head_drift")
    if basis.get("snapshot_sha256") != _digest(_canonical(stored_rows)):
        details.append("snapshot_digest_invalid")
    return list(dict.fromkeys(details))


def _basis_path(run_dir):
    return os.path.join(run_dir, BASIS_NAME)


def create_for_run(root, run_dir, write=False):
    paths = active_run.run_affected_paths(run_dir)
    basis = capture(root, paths)
    if write:
        atomic_write(
            _basis_path(run_dir),
            json.dumps(basis, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            mode=0o600,
            refuse_symlink=True,
        )
    return {
        "status": "OPEN",
        "action": "create",
        "written": bool(write),
        "snapshot_sha256": basis["snapshot_sha256"],
        "basis": basis,
    }


def verify_for_run(root, run_dir):
    path = _basis_path(run_dir)
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            raise OSError
        with open(path, "r", encoding="utf-8") as handle:
            basis = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "CLOSED", "action": "verify", "details": ["basis_missing_or_invalid"]}
    details = verify(root, basis, active_run.run_affected_paths(run_dir))
    return {
        "status": "CLOSED" if details else "OPEN",
        "action": "verify",
        "details": details,
        "snapshot_sha256": basis.get("snapshot_sha256"),
    }


def _root(value):
    if value:
        root = os.path.realpath(value)
    else:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        root = os.path.realpath(result.stdout.strip() or os.getcwd())
    return root


def _run_dir(root, value):
    if not value:
        raise BasisError("--run is required")
    path = os.path.realpath(value if os.path.isabs(value) else os.path.join(root, value))
    if os.path.commonpath((root, path)) != root or not os.path.isdir(path):
        raise BasisError("run directory is outside the repository or missing")
    return path


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    action = args.pop(0) if args and args[0] in ("create", "verify") else "verify"
    root_value = ""
    run_value = ""
    write = False
    while args:
        item = args.pop(0)
        if item == "--root" and args:
            root_value = args.pop(0)
        elif item == "--run" and args:
            run_value = args.pop(0)
        elif item == "--write":
            write = True
        elif item in ("-h", "--help"):
            sys.stdout.write("Usage: codebase-basis.sh create|verify --run <path> [--root <path>] [--write]\n")
            return 0
        else:
            sys.stderr.write("codebase-basis: unknown or incomplete argument: %s\n" % item)
            return 2
    try:
        root = _root(root_value)
        run_dir = _run_dir(root, run_value)
        verdict = (
            create_for_run(root, run_dir, write=write)
            if action == "create" else verify_for_run(root, run_dir)
        )
    except (BasisError, OSError, ValueError) as exc:
        verdict = {"status": "CLOSED", "action": action, "details": [str(exc)]}
    details = ",".join(verdict.get("details", [])) or "clean"
    sys.stdout.write(
        "CODEBASE_BASIS\t%s\taction=%s\tdetail=%s\n"
        % (verdict["status"], action, details)
    )
    return 0 if verdict["status"] == "OPEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
