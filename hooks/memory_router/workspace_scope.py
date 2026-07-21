"""Bounded, fail-safe workspace locality for Recall.

This module deliberately models only proven nested package boundaries.  It does not
build a dependency graph, walk the repository, create worktrees, or persist state.
"""
import hashlib
import json
import os
import re
import stat
import subprocess

from . import global_metrics


MAX_SCOPE_PATHS = 32
MAX_UNITS = 8
MAX_CANDIDATE_DIRECTORIES = 128
MAX_FOREIGN_IDENTITIES = 256
MAX_SCOPED_INDEX_CANDIDATES = 2048
MAX_UNIT_PATH_BYTES = 512
MAX_TOTAL_UNIT_PATH_BYTES = 1024
_MAX_STATE_BYTES = 64 * 1024
_MAX_STATE_LINES = 512
_MARKERS = (
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Package.swift",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "mix.exs",
    "pubspec.yaml",
)
_LINE_SUFFIX = re.compile(r":[0-9]+$")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_AFFECTED = re.compile(r"^Affected files:\s*(.*)$", re.IGNORECASE)
_PHASE = re.compile(r"^(?:#{1,6}\s*)?Phase\s+\d+\b", re.IGNORECASE)
_SENTINELS = frozenset(("not verified", "none", "n/a", "unknown"))


def _bounded_path_values(paths):
    values = []
    try:
        iterator = iter(() if paths is None else paths)
        for _ in range(MAX_SCOPE_PATHS + 1):
            try:
                values.append(next(iterator))
            except StopIteration:
                return values, False
    except (OSError, RuntimeError, TypeError, ValueError):
        return values, True
    return values, True


def _fallback(root, paths, reason, source="explicit"):
    return {
        "status": "fallback",
        "reason": reason,
        "requested_path_count": len(paths),
        "units": [],
        "_root": os.path.abspath(root),
        "_paths": tuple(paths),
        "_selected_results": (),
        "_unit_identities": (),
        "_observed": {},
        "_candidate_cache": {},
        "_overflow_reason": None,
        "_source": source,
        "_foreign_hits_omitted": 0,
        "_foreign_hits_truncated": False,
    }


def _normalized_local_path(root, value):
    if not isinstance(value, str) or not value or "\0" in value:
        return None, "invalid_scope_path"
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None, "invalid_scope_path"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None, "invalid_scope_path"
    root = os.path.abspath(root)
    candidate = os.path.normpath(value if os.path.isabs(value) else os.path.join(root, value))
    try:
        if os.path.commonpath((root, candidate)) != root:
            return None, "path_outside_root"
    except ValueError:
        return None, "path_outside_root"
    return candidate, None


def _markers_at_descriptor(descriptor, chain_receipt):
    """Inspect one already pinned directory without following any path component."""
    found = []
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow or os.open not in os.supports_dir_fd:
        return None, "nofollow_unavailable"
    for marker in _MARKERS:
        try:
            marker_descriptor = os.open(
                marker, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
                dir_fd=descriptor)
        except FileNotFoundError:
            continue
        except OSError:
            return None, "unreadable_boundary"
        try:
            before = os.fstat(marker_descriptor)
            anchored = os.stat(marker, dir_fd=descriptor, follow_symlinks=False)
            after = os.fstat(marker_descriptor)
        except OSError:
            os.close(marker_descriptor)
            return None, "unreadable_boundary"
        os.close(marker_descriptor)
        if not stat.S_ISREG(before.st_mode):
            return None, "unsafe_boundary"
        identity = (before.st_dev, before.st_ino)
        if (identity != (after.st_dev, after.st_ino)
                or identity != (anchored.st_dev, anchored.st_ino)
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (anchored.st_size, anchored.st_mtime_ns, anchored.st_ctime_ns)):
            return None, "boundary_changed"
        receipt = (
            identity, before.st_size, before.st_mtime_ns, before.st_ctime_ns,
            stat.S_IMODE(before.st_mode), chain_receipt,
        )
        found.append((marker, receipt))
    if len(found) > 1:
        return None, "ambiguous_boundary"
    return found, None


def _path_scope(root, value, assume_file=False):
    """Resolve a path using one descriptor-pinned, no-follow directory chain."""
    path, error = _normalized_local_path(root, value)
    if error:
        return ("unsafe", error)
    root = os.path.abspath(root)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow or os.open not in os.supports_dir_fd:
        return ("unsafe", "nofollow_unavailable")
    directory_flags = os.O_RDONLY | nofollow | getattr(os, "O_DIRECTORY", 0)
    descriptors = []
    directories = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        root_info = os.fstat(current)
        directories.append(("", current, (root_info.st_dev, root_info.st_ino)))
        relative = os.path.relpath(path, root)
        parts = [] if relative == os.curdir else relative.split(os.sep)
        walk_parts = parts[:-1] if assume_file and parts else parts
        consumed = []
        for index, part in enumerate(walk_parts):
            try:
                child = os.open(part, directory_flags, dir_fd=current)
            except FileNotFoundError:
                break
            except OSError:
                try:
                    info = os.stat(part, dir_fd=current, follow_symlinks=False)
                except FileNotFoundError:
                    break
                except OSError:
                    return ("unsafe", "unreadable_scope_path")
                if stat.S_ISLNK(info.st_mode):
                    return ("unsafe", "unsafe_scope_path")
                if index == len(walk_parts) - 1 and stat.S_ISREG(info.st_mode):
                    break
                return ("unsafe", "unsafe_scope_path")
            descriptors.append(child)
            current = child
            consumed.append(part)
            info = os.fstat(current)
            directories.append((
                "/".join(consumed), current, (info.st_dev, info.st_ino)
            ))

        chain_receipt = tuple((relative, identity) for relative, _, identity in directories)
        for relative, descriptor, _ in reversed(directories[1:]):
            markers, marker_error = _markers_at_descriptor(descriptor, chain_receipt)
            if marker_error:
                return ("unsafe", marker_error)
            if markers:
                marker, receipt = markers[0]
                return ("unit", relative, marker, receipt)
        return ("shared", "project_root", chain_receipt)
    except OSError:
        return ("unsafe", "unreadable_scope_path")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _unit_identity(resolved):
    if not resolved or resolved[0] != "unit":
        return None
    try:
        for relative, identity in resolved[3][-1]:
            if relative == resolved[1]:
                return identity
    except (IndexError, TypeError, ValueError):
        return None
    return None


def resolve_scope(root, paths, source="explicit", state_receipt=None):
    """Resolve at most 32 input paths to at most eight proven nested units."""
    root = os.path.abspath(root)
    values, overflow = _bounded_path_values(paths)
    if not values:
        return _fallback(root, values, "no_scope_paths", source)
    if overflow:
        return _fallback(root, values, "too_many_paths", source)
    selection = {
        "status": "active",
        "reason": "nested_units",
        "requested_path_count": len(values),
        "units": [],
        "_root": root,
        "_paths": tuple(values),
        "_selected_results": [],
        "_unit_identities": (),
        "_observed": {},
        "_candidate_cache": {},
        "_overflow_reason": None,
        "_source": source,
        "_state_receipt": state_receipt,
        "_foreign_hits_omitted": 0,
        "_foreign_hits_truncated": False,
    }
    units = {}
    project_wide = False
    for value in values:
        resolved = _path_scope(root, value)
        selection["_selected_results"].append(resolved)
        if resolved[0] == "unsafe":
            return _fallback(root, values, resolved[1], source)
        if resolved[0] == "shared":
            project_wide = True
            continue
        _, unit, marker, _ = resolved
        if any(ord(character) < 32 or ord(character) == 127 for character in unit):
            return _fallback(root, values, "unsafe_unit_path", source)
        try:
            encoded_unit = unit.encode("utf-8")
        except UnicodeEncodeError:
            return _fallback(root, values, "unsafe_unit_path", source)
        if len(encoded_unit) > MAX_UNIT_PATH_BYTES:
            return _fallback(root, values, "unit_path_too_long", source)
        identity = _unit_identity(resolved)
        if identity is None:
            return _fallback(root, values, "invalid_boundary_receipt", source)
        previous = units.get(identity)
        if previous is not None and previous[1] != marker:
            return _fallback(root, values, "ambiguous_boundary", source)
        if previous is None:
            units[identity] = (unit, marker)
        if len(units) > MAX_UNITS:
            return _fallback(root, values, "too_many_units", source)
    if project_wide and units:
        return _fallback(root, values, "mixed_project_scope", source)
    if project_wide or not units:
        return _fallback(root, values, "no_nested_boundary", source)
    selection["units"] = [
        {"path": path, "marker": marker}
        for path, marker in sorted(units.values())
    ]
    selection["_unit_identities"] = tuple(sorted(units))
    try:
        metadata_bytes = len(json.dumps(
            selection["units"], ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"))
    except UnicodeEncodeError:
        return _fallback(root, values, "unsafe_unit_path", source)
    if metadata_bytes > MAX_TOTAL_UNIT_PATH_BYTES:
        return _fallback(root, values, "unit_metadata_too_large", source)
    return selection


def fallback_scope(root, paths, reason, source="explicit"):
    """Build explicit public fallback metadata for a discarded scoped pass."""
    values, _ = _bounded_path_values(paths)
    return _fallback(os.path.abspath(root), values, reason, source)


def _evidence_path(value, file_hint=False):
    if not isinstance(value, str):
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    value = value.strip().strip("`")
    if not value or value.lower() in _SENTINELS:
        return None
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "manual:", "command:")):
        return None
    had_line = bool(_LINE_SUFFIX.search(value))
    path = _LINE_SUFFIX.sub("", value)
    if not had_line and not file_hint:
        return None
    return path


def _classify_path(root, selection, value, file_hint=False):
    if selection.get("_overflow_reason"):
        return "shared"
    path = _evidence_path(value, file_hint=file_hint)
    if path is None:
        return "shared"
    normalized = path.replace("\\", "/")
    if normalized == ".kimiflow" or normalized.startswith(".kimiflow/"):
        return "shared"
    absolute, error = _normalized_local_path(root, path)
    if error:
        return "shared"
    cache_key = os.path.relpath(
        os.path.dirname(absolute), os.path.abspath(root)
    ).replace(os.sep, "/")
    cached = selection["_candidate_cache"].get(cache_key)
    if cached is None:
        if len(selection["_candidate_cache"]) >= MAX_CANDIDATE_DIRECTORIES:
            selection["_overflow_reason"] = "scope_classification_limit"
            return "shared"
        resolved = _path_scope(root, path, assume_file=True)
        selection["_candidate_cache"][cache_key] = resolved
        selection["_observed"][cache_key] = (path, resolved, True)
    else:
        resolved = cached
    if resolved[0] != "unit":
        return "shared"
    selected_units = set(selection.get("_unit_identities", ()))
    return "local" if _unit_identity(resolved) in selected_units else "foreign"


def classify_hit(root, selection, source, hit):
    """Classify one Recall candidate as local, shared, or safely omittable foreign."""
    if selection.get("status") != "active" or not isinstance(hit, dict):
        return "shared"
    if source == "learnings":
        evidence = hit.get("evidence")
        values = evidence if isinstance(evidence, list) else []
        fingerprints = hit.get("evidence_fingerprints")
        verified_refs = {
            item.get("ref")
            for item in fingerprints
            if isinstance(item, dict)
            and item.get("status") == "current"
            and item.get("digest_algorithm") == "sha256"
            and isinstance(item.get("digest"), str)
            and _SHA256.fullmatch(item.get("digest"))
            and item.get("sha256") == item.get("digest")
            and isinstance(item.get("ref"), str)
            and isinstance(item.get("path"), str)
            and _LINE_SUFFIX.sub("", item.get("ref")) == item.get("path")
            and _evidence_path(item.get("ref"), file_hint=True) == item.get("path")
            and not os.path.isabs(item.get("path"))
            and os.path.normpath(item.get("path")) == item.get("path")
            and item.get("path") not in (os.curdir, os.pardir)
            and not item.get("path").startswith(os.pardir + os.sep)
        } if isinstance(fingerprints, list) else set()
        classes = []
        has_unbound = False
        for value in values:
            if not isinstance(value, str):
                has_unbound = True
                continue
            file_hint = value in verified_refs
            if _evidence_path(value, file_hint=file_hint) is None:
                has_unbound = True
            else:
                classes.append(_classify_path(
                    root, selection, value, file_hint=file_hint
                ))
        if "local" in classes:
            return "local"
        if has_unbound or not classes or "shared" in classes:
            return "shared"
        return "foreign"
    if source == "facts":
        line = hit.get("line")
        typed_file = isinstance(line, int) and not isinstance(line, bool) and line > 0
        return _classify_path(
            root, selection, hit.get("path"), file_hint=typed_file
        )
    return "shared"


def hit_identity(source, hit):
    """A bounded stable identity used only for aggregate omission counting."""
    if not isinstance(hit, dict):
        return (source, "")
    digest = hashlib.sha256()
    for value in (
        hit.get("id", ""),
        hit.get("ref", hit.get("path", "")),
        hit.get("summary", hit.get("strategy", "")),
    ):
        try:
            encoded = str(value).encode("utf-8", "surrogatepass")
        except (RecursionError, TypeError, ValueError):
            encoded = type(value).__name__.encode("ascii", "replace")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return source, digest.digest()


def revalidate_scope(root, selection):
    """Re-resolve selected and observed paths and compare anchored receipts."""
    if selection.get("status") != "active":
        return True
    if selection.get("_overflow_reason"):
        return False
    state_receipt = selection.get("_state_receipt")
    if state_receipt is not None:
        _, current_receipt = _read_state(state_receipt[0], state_receipt[1])
        if current_receipt != state_receipt:
            return False
    fresh = resolve_scope(root, selection.get("_paths", ()), selection.get("_source", "explicit"))
    if (fresh.get("status") != "active"
            or fresh.get("units") != selection.get("units")
            or fresh.get("_selected_results") != selection.get("_selected_results")):
        return False
    for path, expected, assume_file in selection.get("_observed", {}).values():
        if _path_scope(root, path, assume_file=assume_file) != expected:
            return False
    return True


def _anchored_bounded_file(path, max_bytes, anchor=os.path.sep):
    """Read an absolute regular file through a no-follow chain with a hard cap."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow or os.open not in os.supports_dir_fd:
        return None
    path = os.path.abspath(path)
    anchor = os.path.abspath(anchor)
    try:
        if os.path.commonpath((anchor, path)) != anchor:
            return None
    except ValueError:
        return None
    relative = os.path.relpath(path, anchor)
    parts = relative.split(os.sep)
    if relative == os.curdir or any(part in ("", os.curdir, os.pardir) for part in parts):
        return None
    directory_flags = os.O_RDONLY | nofollow | getattr(os, "O_DIRECTORY", 0)
    descriptors = []
    try:
        current = os.open(anchor, directory_flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(
            parts[-1], os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
            dir_fd=current,
        )
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            return None
        chunks = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        anchored = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
    except OSError:
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    data = b"".join(chunks)
    identity = (before.st_dev, before.st_ino)
    if (len(data) > max_bytes
            or identity != (after.st_dev, after.st_ino)
            or identity != (anchored.st_dev, anchored.st_ino)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (anchored.st_size, anchored.st_mtime_ns, anchored.st_ctime_ns)):
        return None
    return data, stat.S_IMODE(before.st_mode)


def _existing_salt():
    directory = global_metrics.base_dir()
    if not directory:
        return None
    path = os.path.join(directory, "salt")
    snapshot = _anchored_bounded_file(path, 129, anchor=os.path.sep)
    if snapshot is None:
        return None
    data, mode = snapshot
    if mode & 0o077:
        return None
    value = data.rstrip(b"\n")
    if len(value) != 64 or any(byte not in b"0123456789abcdefABCDEF" for byte in value):
        return None
    return value.decode("ascii").lower()


def _git_directories(root):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", root, "rev-parse", "--path-format=absolute",
             "--git-common-dir", "--absolute-git-dir"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or len(result.stdout) > 4096:
        return None
    try:
        values = result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    if len(values) != 2 or any(not value or not os.path.isabs(value) for value in values):
        return None
    canonical = tuple(os.path.realpath(value) for value in values)
    if any(not os.path.isabs(value) or not os.path.isdir(value) for value in canonical):
        return None
    return canonical


def worktree_identity(root):
    """Return path-free pseudonyms from existing salt and read-only Git metadata."""
    unavailable = {
        "status": "unavailable",
        "reason": "salt_unavailable",
        "repository_id": None,
        "worktree_id": None,
    }
    salt = _existing_salt()
    if salt is None:
        return unavailable
    directories = _git_directories(root)
    if directories is None:
        unavailable["reason"] = "git_unavailable"
        return unavailable
    common, private = directories
    return {
        "status": "available",
        "reason": None,
        "repository_id": "repo_" + global_metrics.anonymous_hash_id(salt, common),
        "worktree_id": "wt_" + global_metrics.anonymous_hash_id(salt, private),
    }


def scope_json(selection, foreign_hits_omitted=None):
    """Serialize bounded public metadata; internal paths and receipts never escape."""
    if foreign_hits_omitted is None:
        foreign_hits_omitted = selection.get("_foreign_hits_omitted", 0)
    identity = selection.get("_worktree")
    if identity is None:
        identity = worktree_identity(selection.get("_root", ""))
        selection["_worktree"] = identity
    return {
        "schema_version": 1,
        "status": selection.get("status", "fallback"),
        "reason": selection.get("reason", "scope_unavailable"),
        "source": selection.get("_source", "explicit"),
        "requested_path_count": min(
            MAX_SCOPE_PATHS + 1, max(0, int(selection.get("requested_path_count", 0)))
        ),
        "units": list(selection.get("units", ()))[:MAX_UNITS],
        "foreign_hits_omitted": max(0, int(foreign_hits_omitted)),
        "foreign_hits_omitted_truncated": bool(
            selection.get("_foreign_hits_truncated", False)
        ),
        "worktree": identity,
    }


def _read_state(path, anchor):
    snapshot = _anchored_bounded_file(path, _MAX_STATE_BYTES, anchor=anchor)
    if snapshot is None:
        return ("" if os.path.lexists(path) else None), None
    data, mode = snapshot
    receipt = (
        os.path.abspath(path), os.path.abspath(anchor), hashlib.sha256(data).hexdigest(), mode
    )
    try:
        return data.decode("utf-8"), receipt
    except UnicodeDecodeError:
        return "", receipt


def _split_paths(value):
    paths = []
    for item in value.split(","):
        item = item.strip().strip("`")
        if item:
            paths.append(item)
    return paths


def scope_paths_for_query_file(query_file, root=None, include_receipt=False):
    """Read a sibling run STATE.md Affected-files block without scanning the repo."""
    query_file = os.path.abspath(query_file)
    run_dir = os.path.dirname(query_file)

    def result(paths, reason, receipt=None):
        if include_receipt:
            return paths, reason, receipt
        return paths, reason

    if root is not None:
        expected = os.path.join(os.path.abspath(root), ".kimiflow")
        try:
            if os.path.commonpath((expected, query_file)) != expected:
                return result(None, "foreign_run_artifact")
        except ValueError:
            return result(None, "foreign_run_artifact")
    elif ".kimiflow" not in run_dir.split(os.sep):
        return result(None, "not_run_artifact")
    content, receipt = _read_state(
        os.path.join(run_dir, "STATE.md"),
        anchor=os.path.abspath(root) if root is not None else run_dir,
    )
    if content is None:
        return result([], "state_missing")
    if not content:
        return result([], "state_unreadable_or_overflow", receipt)
    lines = content.splitlines()
    if len(lines) > _MAX_STATE_LINES:
        return result([], "state_unreadable_or_overflow", receipt)
    headers = [line for line in lines if _AFFECTED.match(line.strip())]
    if len(headers) > 1:
        return result([], "state_affected_files_malformed", receipt)
    paths = []
    active = False
    for line in lines:
        if not active:
            match = _AFFECTED.match(line.strip())
            if not match:
                continue
            active = True
            paths.extend(_split_paths(match.group(1)))
            if len(paths) > MAX_SCOPE_PATHS:
                return result(
                    paths[:MAX_SCOPE_PATHS + 1],
                    "state_affected_files_overflow",
                    receipt,
                )
            continue
        stripped = line.strip()
        if _AFFECTED.match(stripped):
            return result([], "state_affected_files_malformed", receipt)
        if _PHASE.match(stripped) or stripped.startswith("#"):
            break
        if not stripped:
            if paths:
                break
            continue
        if stripped.startswith(("- ", "* ")):
            paths.extend(_split_paths(stripped[2:]))
        elif re.match(r"^[A-Za-z][A-Za-z _-]+:\s*", stripped):
            break
        else:
            return result([], "state_affected_files_malformed", receipt)
        if len(paths) > MAX_SCOPE_PATHS:
            return result(
                paths[:MAX_SCOPE_PATHS + 1], "state_affected_files_overflow", receipt
            )
    if not active or not paths:
        return result([], "state_affected_files_missing", receipt)
    return result(paths, "state_affected_files", receipt)
