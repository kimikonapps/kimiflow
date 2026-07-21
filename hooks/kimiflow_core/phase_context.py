"""Deterministic, content-free shadow metadata for phase context candidates."""

import hashlib
import json
import os
import re
import stat

from . import phase_reads, workspace_preflight


class PhaseContextError(ValueError):
    pass


SHADOW_NAME = "PHASE-CONTEXT-SHADOW.json"
MAX_SHADOW_BYTES = 512 * 1024


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(payload):
    return "sha256:%s" % hashlib.sha256(payload).hexdigest()


def _same_file_snapshot(before, opened):
    return all(
        getattr(before, field) == getattr(opened, field)
        for field in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def _open_run(root, run_dir, active=None):
    expected_parent = os.path.realpath(os.path.join(root, ".kimiflow"))
    if os.path.realpath(os.path.dirname(run_dir)) != expected_parent:
        raise PhaseContextError("run_outside_kimiflow")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(run_dir, flags)
        info = os.fstat(descriptor)
    except OSError as exc:
        raise PhaseContextError("run_unreadable:%s" % exc.__class__.__name__)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        raise PhaseContextError("run_not_directory")
    expected = ((active or {}).get("run_device"), (active or {}).get("run_inode"))
    if None not in expected and (info.st_dev, info.st_ino) != expected:
        os.close(descriptor)
        raise PhaseContextError("run_identity_changed")
    return descriptor


def _read_descriptor_file(directory, name, cap, required=True):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
            raise PhaseContextError("unsafe_artifact:%s" % name)
        if named.st_size > cap:
            raise PhaseContextError("artifact_oversize:%s" % name)
        descriptor = os.open(name, flags, dir_fd=directory)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_snapshot(named, opened):
            raise PhaseContextError("artifact_exchanged:%s" % name)
        chunks = []
        total = 0
        while total <= cap:
            chunk = os.read(descriptor, min(65536, cap + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > cap:
            raise PhaseContextError("artifact_oversize:%s" % name)
        return b"".join(chunks)
    except FileNotFoundError:
        if required:
            raise PhaseContextError("artifact_missing:%s" % name)
        return None
    except OSError as exc:
        raise PhaseContextError("artifact_unreadable:%s:%s" % (name, exc.__class__.__name__))
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _require_named_run_identity(run_dir, descriptor):
    try:
        named = os.stat(run_dir, follow_symlinks=False)
        opened = os.fstat(descriptor)
    except OSError as exc:
        raise PhaseContextError("run_identity_unreadable:%s" % exc.__class__.__name__)
    if not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        raise PhaseContextError("run_identity_changed")


def _read_phase_file(root, rel, cap):
    path = phase_reads.resolve_phase_file(root, rel)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        named = os.stat(path, follow_symlinks=False)
        if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode) or named.st_size > cap:
            raise PhaseContextError("unsafe_phase_file")
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not _same_file_snapshot(named, opened):
            raise PhaseContextError("phase_file_exchanged")
        payload = os.read(descriptor, cap + 1)
        if len(payload) > cap:
            raise PhaseContextError("phase_file_oversize")
        return payload
    except OSError as exc:
        if isinstance(exc, PhaseContextError):
            raise
        raise PhaseContextError("phase_file_unreadable:%s" % exc.__class__.__name__)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _mode_from_state(payload):
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        raise PhaseContextError("artifact_encoding:STATE.md")
    for raw in text.splitlines():
        line = re.sub(r"^[ \t]*-[ \t]*", "", raw.replace("**", ""))
        match = re.match(r"^Mode:[ \t]*(.*)$", line, re.IGNORECASE)
        if match:
            value = match.group(1).strip().lower().split(" ", 1)[0]
            return value if value in ("feature", "fix", "audit") else "feature"
    return "feature"


def _selection_names(policy, mode):
    return list(policy["required"]) + list(policy.get(mode, [])) + list(policy["optional"])


def _compile_descriptor(root, run_descriptor, phase):
    entry = phase_reads.phase_entry(root, phase)
    policy = entry["context"]
    state_payload = _read_descriptor_file(run_descriptor, "STATE.md", policy["max_file_bytes"], required=True)
    mode = _mode_from_state(state_payload)
    phase_payload = _read_phase_file(root, entry["file"], policy["max_file_bytes"])
    reads_payload = _read_descriptor_file(run_descriptor, "PHASE-READS.json", policy["max_file_bytes"], required=True)
    selected = [
        {"kind": "phase", "name": entry["file"], "bytes": len(phase_payload), "sha256": _digest(phase_payload)}
    ]
    total = len(phase_payload) + len(reads_payload)
    if total > policy["max_total_bytes"]:
        raise PhaseContextError("context_total_oversize")
    required = set(policy["required"])
    for name in _selection_names(policy, mode):
        payload = state_payload if name == "STATE.md" else _read_descriptor_file(
            run_descriptor, name, policy["max_file_bytes"], required=name in required
        )
        if payload is None:
            continue
        total += len(payload)
        if total > policy["max_total_bytes"]:
            raise PhaseContextError("context_total_oversize")
        selected.append({"kind": "artifact", "name": name, "bytes": len(payload), "sha256": _digest(payload)})
    basis = {
        "phase": entry["id"],
        "mode": mode,
        "policy": policy,
        "phase_reads_sha256": _digest(reads_payload),
        "selected": selected,
    }
    composite = _digest(_canonical(basis))
    return {
        "schema_version": 1,
        "status": "current",
        "authoritative": False,
        "stores_content": False,
        "phase": entry["id"],
        "phase_name": entry["name"],
        "mode": mode,
        "selection": selected,
        "selected_count": len(selected),
        "basis_bytes": len(reads_payload),
        "total_bytes": total,
        "estimated_tokens": (total + 3) // 4,
        "composite_basis": composite,
        "candidate_digest": composite,
    }


def compile_shadow(root, run_dir, phase, active=None):
    run_descriptor = _open_run(root, run_dir, active=active)
    try:
        return _compile_descriptor(root, run_descriptor, phase)
    finally:
        os.close(run_descriptor)


def compile_shadow_descriptor(root, run_descriptor, phase):
    """Compile a fresh projection from an already pinned run directory."""
    return _compile_descriptor(root, run_descriptor, phase)


def write_shadow(root, run_dir, phase, active=None):
    run_descriptor = _open_run(root, run_dir, active=active)
    try:
        value = _compile_descriptor(root, run_descriptor, phase)
        _require_named_run_identity(run_dir, run_descriptor)
        workspace_preflight.atomic_directory_write(
            run_descriptor,
            SHADOW_NAME,
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        )
        try:
            descriptor = os.open(SHADOW_NAME, os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0), dir_fd=run_descriptor)
            try:
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise PhaseContextError("shadow_mode_failed:%s" % exc.__class__.__name__)
        _require_named_run_identity(run_dir, run_descriptor)
    finally:
        os.close(run_descriptor)
    return value


def write_invalid_shadow(root, run_dir, phase, reason, active=None):
    value = {
        "schema_version": 1,
        "status": "invalid",
        "authoritative": False,
        "stores_content": False,
        "phase": int(phase),
        "reason": str(reason)[:160],
    }
    try:
        descriptor = _open_run(root, run_dir, active=active)
        try:
            workspace_preflight.atomic_directory_write(
                descriptor,
                SHADOW_NAME,
                json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
            )
        finally:
            os.close(descriptor)
    except (OSError, ValueError, PhaseContextError):
        pass
    return value


def load_current_shadow(root, run_dir, phase, active=None, run_descriptor=None):
    descriptor = run_descriptor if run_descriptor is not None else _open_run(root, run_dir, active=active)
    close_descriptor = run_descriptor is None
    try:
        payload = _read_descriptor_file(descriptor, SHADOW_NAME, MAX_SHADOW_BYTES, required=False)
        if payload is None:
            return {"schema_version": 1, "status": "missing", "authoritative": False, "stores_content": False}
        try:
            stored = json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return {"schema_version": 1, "status": "invalid", "authoritative": False, "stores_content": False, "reason": "shadow_malformed"}
        if not isinstance(stored, dict) or stored.get("schema_version") != 1 or stored.get("status") != "current":
            return stored if isinstance(stored, dict) else {"schema_version": 1, "status": "invalid"}
        try:
            current = _compile_descriptor(root, descriptor, phase)
        except PhaseContextError as exc:
            return {"schema_version": 1, "status": "invalid", "authoritative": False, "stores_content": False, "reason": str(exc)}
        if stored.get("composite_basis") != current.get("composite_basis"):
            value = dict(stored)
            value["status"] = "stale"
            return value
        return stored
    finally:
        if close_descriptor:
            os.close(descriptor)
