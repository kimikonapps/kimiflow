"""Run-local proof that on-demand Kimiflow phase files were read freshly."""

import hashlib
import json
import os

from . import state
from .atomic import atomic_write


class PhaseReadError(ValueError):
    pass


def plugin_root():
    env_root = os.environ.get("KIMIFLOW_PLUGIN_ROOT")
    if env_root:
        return os.path.abspath(env_root)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def manifest_path(root=None):
    return os.path.join(plugin_root(), "phases", "PHASES.json")


def reads_path(run_dir):
    return os.path.join(run_dir, "PHASE-READS.json")


def manifest_exists(root=None):
    return os.path.isfile(manifest_path(root))


def _safe_phase_file(rel):
    if not rel or os.path.isabs(rel):
        raise PhaseReadError("phase file must be a relative phases/ path")
    norm = os.path.normpath(rel)
    if norm != rel or norm == ".." or norm.startswith("..%s" % os.sep):
        raise PhaseReadError("phase file must not contain traversal")
    if not norm.startswith("phases%s" % os.sep):
        raise PhaseReadError("phase file must be under phases/")
    return norm


def resolve_phase_file(root, rel):
    norm = _safe_phase_file(rel)
    base = plugin_root()
    path = os.path.join(base, norm)
    root_real = os.path.realpath(base)
    file_real = os.path.realpath(path)
    if not (file_real == root_real or file_real.startswith(root_real + os.sep)):
        raise PhaseReadError("phase file must stay inside the plugin")
    if os.path.islink(path):
        raise PhaseReadError("phase file must not be a symlink")
    if not os.path.isfile(path):
        raise PhaseReadError("phase file missing: %s" % norm)
    return path


def _phase_int(value):
    try:
        phase = int(str(value), 10)
    except (TypeError, ValueError):
        raise PhaseReadError("phase must be an integer 0-7")
    if phase < 0 or phase > 7:
        raise PhaseReadError("phase must be between 0 and 7")
    return phase


def load_manifest(root):
    path = manifest_path(root)
    if not os.path.isfile(path):
        raise PhaseReadError("phase manifest missing: phases/PHASES.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PhaseReadError("phase manifest invalid: %s" % exc)
    phases = value.get("phases") if isinstance(value, dict) else None
    if not isinstance(phases, list):
        raise PhaseReadError("phase manifest invalid: phases list missing")
    out = []
    seen = set()
    for row in phases:
        if not isinstance(row, dict):
            raise PhaseReadError("phase manifest invalid: phase row must be object")
        phase = _phase_int(row.get("id"))
        if phase in seen:
            raise PhaseReadError("phase manifest invalid: duplicate phase %s" % phase)
        seen.add(phase)
        rel = _safe_phase_file(str(row.get("file", "")))
        out.append({"id": phase, "file": rel, "name": str(row.get("name", ""))})
    return sorted(out, key=lambda item: item["id"])


def phase_entry(root, phase):
    wanted = _phase_int(phase)
    for entry in load_manifest(root):
        if entry["id"] == wanted:
            return entry
    raise PhaseReadError("phase %s missing from phases/PHASES.json" % wanted)


def required_entries(root, through_phase):
    through = _phase_int(through_phase)
    return [entry for entry in load_manifest(root) if entry["id"] <= through]


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:%s" % digest.hexdigest()


def load_records(run_dir):
    path = reads_path(run_dir)
    if not os.path.isfile(path):
        return {"schema_version": 1, "reads": {}}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PhaseReadError("phase-read records invalid: %s" % exc)
    if not isinstance(value, dict) or not isinstance(value.get("reads"), dict):
        raise PhaseReadError("phase-read records invalid: reads object missing")
    return value


def write_records(run_dir, records):
    path = reads_path(run_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, json.dumps(records, ensure_ascii=False, indent=2) + "\n", mode=0o600, refuse_symlink=True)


def phase_reads_required(root, run_dir, active=None):
    if active and active.get("phase_reads_required") is True:
        return True
    marker = state.state_value(os.path.join(run_dir, "STATE.md"), "Phase reads required").strip().lower()
    return marker in ("yes", "true", "1", "required")


def record_read(root, run_dir, phase, rel_file, now, write=False):
    entry = phase_entry(root, phase)
    rel = _safe_phase_file(rel_file)
    if rel != entry["file"]:
        raise PhaseReadError("phase %s requires %s, got %s" % (entry["id"], entry["file"], rel))
    path = resolve_phase_file(root, rel)
    stat = os.stat(path)
    record = {
        "phase": entry["id"],
        "file": rel,
        "sha256": file_hash(path),
        "size": stat.st_size,
        "read_at": now,
    }
    records = load_records(run_dir)
    records["schema_version"] = 1
    records.setdefault("reads", {})[str(entry["id"])] = record
    records["updated_at"] = now
    if write:
        write_records(run_dir, records)
    return record


def gate(root, run_dir, through_phase, active=None):
    if not phase_reads_required(root, run_dir, active=active):
        return {"status": "OPEN", "blockers": 0, "reason": "legacy", "detail": "phase_reads_not_required"}

    blockers = []
    try:
        entries = required_entries(root, through_phase)
        records = load_records(run_dir)
    except PhaseReadError as exc:
        return {"status": "CLOSED", "blockers": 1, "reason": "phase-read-blockers", "detail": str(exc)}

    reads = records.get("reads", {})
    for entry in entries:
        phase = entry["id"]
        rec = reads.get(str(phase))
        if not isinstance(rec, dict):
            blockers.append("phase_%s_read_missing" % phase)
            continue
        if rec.get("file") != entry["file"]:
            blockers.append("phase_%s_file_mismatch" % phase)
            continue
        try:
            current_hash = file_hash(resolve_phase_file(root, entry["file"]))
        except PhaseReadError:
            blockers.append("phase_%s_file_missing" % phase)
            continue
        if rec.get("sha256") != current_hash:
            blockers.append("phase_%s_read_stale" % phase)

    if blockers:
        return {
            "status": "CLOSED",
            "blockers": len(blockers),
            "reason": "phase-read-blockers",
            "detail": ",".join(blockers),
        }
    return {"status": "OPEN", "blockers": 0, "reason": "clean", "detail": ""}


def status_payload(root, run_dir, active=None):
    required = phase_reads_required(root, run_dir, active=active)
    try:
        records = load_records(run_dir)
    except PhaseReadError as exc:
        records = {"schema_version": 1, "reads": {}, "error": str(exc)}
    return {
        "schema_version": 1,
        "phase_reads_required": required,
        "manifest_path": "phases/PHASES.json",
        "phase_reads_path": os.path.join(os.path.relpath(run_dir, root), "PHASE-READS.json"),
        "records": records,
    }
