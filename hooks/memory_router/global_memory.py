"""Provider-free, human-readable cross-project memory notes."""
import hashlib
import json
import os
import re
import stat
import unicodedata
from datetime import date

from . import capsule, paths, rows, store, summaries

MAX_NOTE_BYTES = 32 * 1024
MAX_NOTES = 500
MAX_DIRECTORY_ENTRIES = MAX_NOTES + 1
MAX_BINDINGS = 2000
MAX_BINDING_DIRECTORY_ENTRIES = MAX_BINDINGS + 1
MAX_INDEX_BYTES = 128 * 1024
MAX_RELATED = 5
DISPLAY_PATH = "~/.kimiflow/memory"
_ID_RE = re.compile(r"^cap_[0-9a-f]{64}$")
_NAME_RE = re.compile(r"^(cap_[0-9a-f]{64})\.md$")
_BINDING_ID_RE = re.compile(r"^bind_[0-9a-f]{64}$")
_BINDING_NAME_RE = re.compile(
    r"^(cap_[0-9a-f]{64})--(bind_[0-9a-f]{64})\.md$"
)
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_RELATED_RE = re.compile(r"^- \[([^\]\r\n]{1,80})\]\((\.\./INDEX\.md|cap_[0-9a-f]{64}\.md)\)$")
_BINDING_LINK_RE = re.compile(
    r"^- \[([^\]\r\n]{1,80})\]\(\.\./notes/(cap_[0-9a-f]{64})\.md\)$"
)
_TERM_RE = re.compile(r"[a-z0-9_-]{3,}")
_CORE_FIELDS = ("kind", "topic", "summary", "confidence", "last_verified")


def _valid_date(value):
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def entry_id(value):
    content = {field: value.get(field) for field in _CORE_FIELDS}
    canonical = json.dumps(
        content, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "cap_" + hashlib.sha256(canonical).hexdigest()


def capsule_id(row):
    try:
        return entry_id(row)
    except (AttributeError, TypeError, ValueError):
        return ""


def binding_id(root, row, identifier, learning_fingerprint):
    if (not isinstance(root, str) or not root
            or not isinstance(row, dict)
            or not isinstance(row.get("id"), str) or not row["id"]
            or not isinstance(identifier, str) or _ID_RE.fullmatch(identifier) is None
            or not isinstance(learning_fingerprint, str)
            or _FINGERPRINT_RE.fullmatch(learning_fingerprint) is None):
        return ""
    payload = {
        "capsule_id": identifier,
        "learning_fingerprint": learning_fingerprint,
        "learning_id": row["id"],
        "project_root": os.path.realpath(os.path.abspath(root)),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "bind_" + hashlib.sha256(canonical).hexdigest()


def _safe_text(values):
    canonical = tuple(unicodedata.normalize("NFKC", value) for value in values)
    combined = canonical + (" ".join(canonical),)
    return not (
        any(not rows.memory_security_json(value)["ok"] for value in combined)
        or any(rows.has_secret_value(value) or capsule._PORTABLE_CREDENTIAL.search(value)
               or capsule._contains_jwt(value) for value in combined)
        or any(capsule._URL.search(value) or capsule._contains_idn_domain(value)
               or capsule._LOCAL_ENDPOINT.search(value)
               or capsule._contains_bare_ipv6(value) for value in combined)
        or any(capsule._EMAIL.search(value) for value in combined)
        or any(capsule._PATHISH.search(value) or capsule._SENSITIVE_DOTFILE.search(value)
               for value in combined)
    )


def _valid_entry(entry):
    if not isinstance(entry, dict):
        return False
    expected = set(_CORE_FIELDS) | {"capsule_id"}
    if set(entry) != expected:
        return False
    kind, topic, summary = (entry.get(name) for name in ("kind", "topic", "summary"))
    confidence = entry.get("confidence")
    verified = entry.get("last_verified")
    if (
        not isinstance(kind, str) or capsule._SAFE_KIND.fullmatch(kind) is None
        or not isinstance(topic, str) or capsule._SAFE_TOPIC.fullmatch(topic) is None
        or not isinstance(summary, str) or not summary.strip() or len(summary) > 500
        or confidence not in ("medium", "high")
        or not _valid_date(verified)
        or summaries.learning_is_stale({"last_verified": verified})
        or not _safe_text((kind, topic, summary))
    ):
        return False
    try:
        if (
            not isinstance(entry.get("capsule_id"), str)
            or _ID_RE.fullmatch(entry["capsule_id"]) is None
            or entry["capsule_id"] != entry_id(entry)
        ):
            return False
    except (TypeError, ValueError):
        return False
    return True


def pending_promotion(root, row):
    if not isinstance(row, dict) or row.get("maturity") != rows.MATURITY_DURABLE:
        return None
    entry, reason = capsule.portable_entry(root, row)
    if reason is not None or not _valid_entry(entry):
        return None
    try:
        learning_fingerprint = rows.learning_content_fingerprint(row)
    except ValueError:
        return None
    association = binding_id(root, row, entry["capsule_id"], learning_fingerprint)
    if not association:
        return None
    return {
        "contract": 1,
        "operation": "promote",
        "entry": entry,
        "learning_fingerprint": learning_fingerprint,
        "binding_id": association,
    }


def pending_revocation(root, row):
    curation = row.get("curation") if isinstance(row, dict) else None
    identifier = (
        curation.get("global_memory_capsule_id")
        if isinstance(curation, dict) else None
    )
    learning_fingerprint = (
        curation.get("learning_fingerprint")
        if isinstance(curation, dict) else None
    )
    if (not isinstance(identifier, str) or _ID_RE.fullmatch(identifier) is None
            or not isinstance(learning_fingerprint, str)
            or _FINGERPRINT_RE.fullmatch(learning_fingerprint) is None):
        return None
    association = binding_id(root, row, identifier, learning_fingerprint)
    if not association:
        return None
    return {
        "contract": 1,
        "operation": "revoke",
        "capsule_id": identifier,
        "learning_fingerprint": learning_fingerprint,
        "binding_id": association,
    }


def apply_pending(root, row):
    curation = row.get("curation") if isinstance(row, dict) else None
    pending = curation.get("global_memory_pending") if isinstance(curation, dict) else None
    if not isinstance(pending, dict) or pending.get("contract") != 1:
        raise ValueError("invalid global memory pending operation")
    operation = pending.get("operation")
    if operation == "promote" and set(pending) == {
            "contract", "operation", "entry", "learning_fingerprint", "binding_id"}:
        if pending_promotion(root, row) != pending:
            raise ValueError("global promotion no longer matches its transition")
        return promote_entry(pending["entry"], pending["binding_id"])
    if operation == "revoke" and set(pending) == {
            "contract", "operation", "capsule_id", "learning_fingerprint", "binding_id"}:
        if (row.get("maturity") != rows.MATURITY_PROBATIONARY
                or pending_revocation(root, row) != pending):
            raise ValueError("global revocation no longer matches its transition")
        return revoke(pending["capsule_id"], pending["binding_id"])
    raise ValueError("invalid global memory pending operation")


def _home(create=False):
    home = paths.global_memory_home()
    if home is None:
        raise ValueError("global memory home is unavailable")
    if create:
        os.makedirs(home, mode=0o700, exist_ok=True)
    try:
        info = os.lstat(home)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("unsafe global memory home")
    return home


def _directories(create=False):
    home = _home(create=create)
    if home is None:
        return None
    memory = os.path.join(home, "memory")
    notes = os.path.join(memory, "notes")
    bindings = os.path.join(memory, "bindings")
    if create:
        store.ensure_local_directory(home, notes)
        store.ensure_local_directory(home, bindings)
    elif (not os.path.lexists(memory) or not os.path.lexists(notes)
          or not os.path.lexists(bindings)):
        return None
    return home, memory, notes, bindings


def _anchor_current(anchor):
    try:
        pinned = os.fstat(anchor["descriptor"])
        named = os.stat(anchor["path"], follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISDIR(named.st_mode) and (
        pinned.st_dev, pinned.st_ino
    ) == (named.st_dev, named.st_ino)


def _exclusive_create(anchor, name, content, mode=0o600):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, mode, dir_fd=anchor["descriptor"])
    try:
        payload = content.encode("utf-8")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.fsync(anchor["descriptor"])
        if not _anchor_current(anchor):
            raise store.ConcurrentWriteError("global memory parent changed")
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        try:
            os.unlink(name, dir_fd=anchor["descriptor"])
            os.fsync(anchor["descriptor"])
        except OSError:
            pass
        raise


def _terms(value):
    return set(_TERM_RE.findall(unicodedata.normalize("NFKC", value).casefold()))


def _related(entries, entry):
    wanted = _terms(entry["topic"] + " " + entry["summary"])
    ranked = []
    for item in entries:
        score = len(wanted & _terms(item["topic"] + " " + item["summary"]))
        if score:
            ranked.append((-score, item["id"], item))
    return [item for _score, _identifier, item in sorted(ranked)[:MAX_RELATED]]


def _render_note(entry, related):
    lines = [
        "---",
        "kimiflow-global-memory: 1",
        "id: %s" % entry["capsule_id"],
        "status: current",
        "kind: %s" % entry["kind"],
        "topic: %s" % entry["topic"],
        "confidence: %s" % entry["confidence"],
        "last-verified: %s" % entry["last_verified"],
        "---",
        "# %s" % entry["topic"],
        "",
        "## Learning",
        "",
        entry["summary"],
        "",
        "## Related",
        "",
        "- [Global memory index](../INDEX.md)",
    ]
    lines.extend(
        "- [%s](%s.md)" % (item["topic"], item["id"])
        for item in related
    )
    return "\n".join(lines) + "\n"


def _render_binding(entry, association):
    return "\n".join([
        "---",
        "kimiflow-global-memory-binding: 1",
        "id: %s" % association,
        "capsule-id: %s" % entry["capsule_id"],
        "---",
        "# Global memory binding",
        "",
        "- [%s](../notes/%s.md)" % (entry["topic"], entry["capsule_id"]),
        "",
    ])


def _parse_binding(name, data):
    match = _BINDING_NAME_RE.fullmatch(name)
    if match is None:
        return None, "malformed_binding_name"
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None, "binding_encoding"
    if (len(lines) != 8 or lines[0] != "---"
            or lines[1] != "kimiflow-global-memory-binding: 1"
            or lines[2] != "id: " + match.group(2)
            or lines[3] != "capsule-id: " + match.group(1)
            or lines[4:7] != ["---", "# Global memory binding", ""]):
        return None, "malformed_binding"
    link = _BINDING_LINK_RE.fullmatch(lines[7])
    if link is None or link.group(2) != match.group(1):
        return None, "malformed_binding"
    return {"capsule_id": match.group(1), "binding_id": match.group(2)}, None


def _parse_note(name, data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, "encoding"
    lines = text.splitlines()
    if len(lines) < 18 or lines[0] != "---" or lines[8] != "---":
        return None, "malformed"
    expected_prefixes = (
        (1, "kimiflow-global-memory: "), (2, "id: "), (3, "status: "),
        (4, "kind: "), (5, "topic: "), (6, "confidence: "),
        (7, "last-verified: "),
    )
    if any(not lines[index].startswith(prefix) for index, prefix in expected_prefixes):
        return None, "malformed"
    values = {prefix[:-2]: lines[index][len(prefix):] for index, prefix in expected_prefixes}
    identifier = values["id"]
    if values["kimiflow-global-memory"] != "1" or name != identifier + ".md":
        return None, "identity"
    if values["status"] != "current":
        return None, "inactive"
    if lines[9] != "# " + values["topic"] or lines[10:13] != ["", "## Learning", ""]:
        return None, "malformed"
    try:
        related_heading = lines.index("## Related", 13)
    except ValueError:
        return None, "malformed"
    if related_heading <= 13 or lines[related_heading - 1] != "" or lines[related_heading + 1] != "":
        return None, "malformed"
    summary = "\n".join(lines[13:related_heading - 1]).strip()
    related_lines = lines[related_heading + 2:]
    matches = [_RELATED_RE.fullmatch(line) for line in related_lines]
    if (
        not related_lines or matches[0] is None or matches[0].group(2) != "../INDEX.md"
        or len(related_lines) > MAX_RELATED + 1 or any(match is None for match in matches)
        or len({match.group(2) for match in matches}) != len(matches)
    ):
        return None, "relationships"
    entry = {
        "id": identifier,
        "kind": values["kind"],
        "topic": values["topic"],
        "summary": summary,
        "confidence": values["confidence"],
        "last_verified": values["last-verified"],
        "related": [match.group(2)[:-3] for match in matches[1:]],
    }
    if (
        _ID_RE.fullmatch(identifier) is None
        or capsule._SAFE_KIND.fullmatch(entry["kind"]) is None
        or capsule._SAFE_TOPIC.fullmatch(entry["topic"]) is None
        or not summary or len(summary) > 500
        or entry["confidence"] not in ("medium", "high")
        or not _valid_date(entry["last_verified"])
    ):
        return None, "malformed"
    if summaries.learning_is_stale(entry):
        return None, "stale"
    if not _safe_text((entry["kind"], entry["topic"], summary)):
        return None, "unsafe"
    return entry, None


def _scan_bindings_locked(bindings, anchor):
    active = set()
    reasons = {}
    names = []
    overflow = False
    try:
        with os.scandir(anchor["descriptor"]) as directory:
            for offset, item in enumerate(directory):
                if offset >= MAX_BINDING_DIRECTORY_ENTRIES:
                    overflow = True
                    break
                names.append(item.name)
    except OSError:
        return set(), {"unsafe_binding_parent": 1}, 0
    markdown_names = sorted(name for name in names if name.endswith(".md"))
    overflow = overflow or len(names) > MAX_BINDINGS or len(markdown_names) > MAX_BINDINGS
    if overflow:
        reasons["binding_limit"] = 1
    selected = markdown_names[:MAX_BINDINGS]
    for name in selected:
        if _BINDING_NAME_RE.fullmatch(name) is None:
            reasons["malformed_binding_name"] = (
                reasons.get("malformed_binding_name", 0) + 1
            )
            continue
        path = os.path.join(bindings, name)
        try:
            snapshot = store.stable_file_snapshot(path, max_bytes=MAX_NOTE_BYTES)
        except store.FileTooLargeError:
            reasons["binding_oversize"] = reasons.get("binding_oversize", 0) + 1
            continue
        except (ValueError, store.ConcurrentWriteError, OSError):
            reasons["unsafe_binding_file"] = (
                reasons.get("unsafe_binding_file", 0) + 1
            )
            continue
        binding, reason = _parse_binding(name, snapshot[1])
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        active.add(binding["capsule_id"])
    if not _anchor_current(anchor):
        raise store.ConcurrentWriteError("global bindings parent changed")
    return active, reasons, len(selected)


def _scan_locked(notes, anchor, active_ids=None):
    hits = []
    reasons = {}
    names = []
    overflow = False
    try:
        with os.scandir(anchor["descriptor"]) as directory:
            for offset, item in enumerate(directory):
                if offset >= MAX_DIRECTORY_ENTRIES:
                    overflow = True
                    break
                names.append(item.name)
    except OSError:
        return [], {"unsafe_parent": 1}
    markdown_names = sorted(name for name in names if name.endswith(".md"))
    overflow = overflow or len(names) > MAX_NOTES or len(markdown_names) > MAX_NOTES
    if overflow:
        reasons["limit"] = 1
    for name in markdown_names[:MAX_NOTES]:
        if _NAME_RE.fullmatch(name) is None:
            reasons["malformed_name"] = reasons.get("malformed_name", 0) + 1
            continue
        path = os.path.join(notes, name)
        try:
            snapshot = store.stable_file_snapshot(path, max_bytes=MAX_NOTE_BYTES)
        except store.FileTooLargeError:
            reasons["oversize"] = reasons.get("oversize", 0) + 1
            continue
        except (ValueError, store.ConcurrentWriteError, OSError):
            reasons["unsafe_file"] = reasons.get("unsafe_file", 0) + 1
            continue
        entry, reason = _parse_note(name, snapshot[1])
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        if active_ids is not None and entry["id"] not in active_ids:
            reasons["unbound"] = reasons.get("unbound", 0) + 1
            continue
        hit = dict(entry)
        hit["ref"] = "%s/notes/%s" % (DISPLAY_PATH, name)
        hit["source_link"] = path
        hits.append(hit)
    if not _anchor_current(anchor):
        raise store.ConcurrentWriteError("global notes parent changed")
    return hits, reasons


def _index_text(entries):
    lines = [
        "# Kimiflow Global Memory",
        "",
        "Canonical human-readable notes. This index is generated.",
        "",
    ]
    if entries:
        lines.extend("- [%s](notes/%s.md)" % (item["topic"], item["id"])
                     for item in sorted(entries, key=lambda item: (item["topic"].casefold(), item["id"])))
    else:
        lines.append("- No current notes.")
    return "\n".join(lines) + "\n"


def _write_index(memory, memory_anchor, notes_anchor, bindings_anchor, entries):
    anchors = (memory_anchor, notes_anchor, bindings_anchor)
    if not all(_anchor_current(anchor) for anchor in anchors):
        raise store.ConcurrentWriteError("global memory parent changed")
    content = _index_text(entries)
    if len(content.encode("utf-8")) > MAX_INDEX_BYTES:
        raise ValueError("global memory index exceeds size limit")
    path = os.path.join(memory, "INDEX.md")
    previous = store.stable_file_snapshot(
        path, missing_ok=True, max_bytes=MAX_INDEX_BYTES,
    )
    try:
        store.atomic_write(path, content, mode=0o600, durable=True)
        if not all(_anchor_current(anchor) for anchor in anchors):
            raise store.ConcurrentWriteError("global memory parent changed")
    except BaseException:
        try:
            installed = store.stable_file_snapshot(
                path, missing_ok=True, max_bytes=MAX_INDEX_BYTES,
            )
            if installed is not None and installed[1] == content.encode("utf-8"):
                if previous is None:
                    os.unlink("INDEX.md", dir_fd=memory_anchor["descriptor"])
                    os.fsync(memory_anchor["descriptor"])
                else:
                    store.atomic_write(
                        path, previous[1].decode("utf-8"), mode=previous[2],
                        expected=content, expected_snapshot=installed, durable=True,
                        allow_detached=True, max_bytes=MAX_INDEX_BYTES,
                    )
        except (OSError, UnicodeDecodeError, ValueError,
                store.ConcurrentWriteError, store.FileTooLargeError):
            pass
        raise


def _binding_name(identifier, association):
    return "%s--%s.md" % (identifier, association)


def _binding_present_locked(bindings, anchor, identifier, association):
    name = _binding_name(identifier, association)
    try:
        info = os.stat(name, dir_fd=anchor["descriptor"], follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            return False
        snapshot = store.stable_file_snapshot(
            os.path.join(bindings, name), max_bytes=MAX_NOTE_BYTES,
        )
        value, reason = _parse_binding(name, snapshot[1])
        return (reason is None and value["capsule_id"] == identifier
                and value["binding_id"] == association)
    except (FileNotFoundError, ValueError, store.ConcurrentWriteError, OSError):
        return False


def promote_entry(entry, association):
    if (not _valid_entry(entry)
            or not isinstance(association, str)
            or _BINDING_ID_RE.fullmatch(association) is None):
        raise ValueError("unsafe global memory entry")
    home = _home(create=True)
    memory = os.path.join(home, "memory")
    lock = os.path.join(memory, ".global-memory.lock")
    with store.path_lock(lock):
        home, memory, notes, bindings = _directories(create=True)
        with store.local_path_guard(home, memory) as memory_anchor, \
                store.local_path_guard(home, notes) as notes_anchor, \
                store.local_path_guard(home, bindings) as bindings_anchor:
            active, binding_reasons, binding_count = _scan_bindings_locked(
                bindings, bindings_anchor,
            )
            existing, reasons = _scan_locked(notes, notes_anchor, active)
            name = entry["capsule_id"] + ".md"
            path = os.path.join(notes, name)
            binding_name = _binding_name(entry["capsule_id"], association)
            binding_path = os.path.join(bindings, binding_name)
            note_created = False
            binding_created = False
            content = None
            binding_content = None
            try:
                if not os.path.lexists(path):
                    if (reasons.get("limit")
                            or len(existing) + sum(reasons.values()) >= MAX_NOTES):
                        raise ValueError("global memory capacity reached")
                    content = _render_note(entry, _related(existing, entry))
                    try:
                        _exclusive_create(notes_anchor, name, content)
                        note_created = True
                    except FileExistsError:
                        pass
                if not os.path.lexists(binding_path):
                    if (binding_reasons.get("binding_limit")
                            or binding_count >= MAX_BINDINGS):
                        raise ValueError("global memory binding capacity reached")
                    binding_content = _render_binding(entry, association)
                    try:
                        _exclusive_create(
                            bindings_anchor, binding_name, binding_content,
                        )
                        binding_created = True
                    except FileExistsError:
                        pass
                if not _binding_present_locked(
                        bindings, bindings_anchor, entry["capsule_id"], association):
                    raise ValueError("unsafe global memory binding")
                active, _binding_reasons, _binding_count = _scan_bindings_locked(
                    bindings, bindings_anchor,
                )
                current, _reasons = _scan_locked(notes, notes_anchor, active)
                _write_index(
                    memory, memory_anchor, notes_anchor, bindings_anchor, current,
                )
            except BaseException:
                if binding_created:
                    try:
                        snapshot = store.stable_file_snapshot(
                            binding_path, max_bytes=MAX_NOTE_BYTES,
                        )
                        if snapshot[1] == binding_content.encode("utf-8"):
                            os.unlink(
                                binding_name, dir_fd=bindings_anchor["descriptor"],
                            )
                            os.fsync(bindings_anchor["descriptor"])
                    except (OSError, ValueError, store.ConcurrentWriteError):
                        pass
                if note_created:
                    try:
                        snapshot = store.stable_file_snapshot(path, max_bytes=MAX_NOTE_BYTES)
                        if snapshot[1] == content.encode("utf-8"):
                            os.unlink(name, dir_fd=notes_anchor["descriptor"])
                            os.fsync(notes_anchor["descriptor"])
                    except (OSError, ValueError, store.ConcurrentWriteError):
                        pass
                raise
    return {
        "status": "created" if note_created else "exists",
        "capsule_id": entry["capsule_id"],
    }


def revoke(identifier, association):
    if (not isinstance(identifier, str) or _ID_RE.fullmatch(identifier) is None
            or not isinstance(association, str)
            or _BINDING_ID_RE.fullmatch(association) is None):
        raise ValueError("invalid global memory id")
    directories = _directories(create=False)
    if directories is None:
        return {"status": "absent", "capsule_id": identifier}
    home, memory, notes, bindings = directories
    lock = os.path.join(memory, ".global-memory.lock")
    with store.local_path_guard(home, memory) as memory_anchor, \
            store.path_lock(lock), \
            store.local_path_guard(home, notes) as notes_anchor, \
            store.local_path_guard(home, bindings) as bindings_anchor:
        binding_name = _binding_name(identifier, association)
        binding_removed = False
        note_removed = False
        binding_snapshot = None
        note_snapshot = None
        name = identifier + ".md"
        try:
            try:
                info = os.stat(
                    binding_name, dir_fd=bindings_anchor["descriptor"],
                    follow_symlinks=False,
                )
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("unsafe global memory binding")
                binding_snapshot = store.stable_file_snapshot(
                    os.path.join(bindings, binding_name), max_bytes=MAX_NOTE_BYTES,
                )
                binding, reason = _parse_binding(
                    binding_name, binding_snapshot[1],
                )
                if (reason is not None or binding["capsule_id"] != identifier
                        or binding["binding_id"] != association):
                    raise ValueError("global memory binding does not match transition")
                os.unlink(binding_name, dir_fd=bindings_anchor["descriptor"])
                binding_removed = True
                os.fsync(bindings_anchor["descriptor"])
            except FileNotFoundError:
                pass
            active, _binding_reasons, _binding_count = _scan_bindings_locked(
                bindings, bindings_anchor,
            )
            if identifier not in active:
                try:
                    info = os.stat(
                        name, dir_fd=notes_anchor["descriptor"],
                        follow_symlinks=False,
                    )
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("unsafe global memory note")
                    note_snapshot = store.stable_file_snapshot(
                        os.path.join(notes, name), max_bytes=MAX_NOTE_BYTES,
                    )
                    _entry, reason = _parse_note(name, note_snapshot[1])
                    if reason is not None:
                        raise ValueError("unsafe global memory note")
                    os.unlink(name, dir_fd=notes_anchor["descriptor"])
                    note_removed = True
                    os.fsync(notes_anchor["descriptor"])
                except FileNotFoundError:
                    pass
            current, _reasons = _scan_locked(notes, notes_anchor, active)
            _write_index(
                memory, memory_anchor, notes_anchor, bindings_anchor, current,
            )
        except BaseException:
            if note_removed and note_snapshot is not None:
                try:
                    _exclusive_create(
                        notes_anchor, name, note_snapshot[1].decode("utf-8"),
                        mode=note_snapshot[2],
                    )
                except (FileExistsError, OSError, UnicodeDecodeError,
                        store.ConcurrentWriteError):
                    pass
            if binding_removed and binding_snapshot is not None:
                try:
                    _exclusive_create(
                        bindings_anchor, binding_name,
                        binding_snapshot[1].decode("utf-8"),
                        mode=binding_snapshot[2],
                    )
                except (FileExistsError, OSError, UnicodeDecodeError,
                        store.ConcurrentWriteError):
                    pass
            raise
    return {
        "status": "revoked" if binding_removed or note_removed else "absent",
        "capsule_id": identifier,
    }


def binding_matches(identifier, association):
    if (not isinstance(identifier, str) or _ID_RE.fullmatch(identifier) is None
            or not isinstance(association, str)
            or _BINDING_ID_RE.fullmatch(association) is None):
        return False
    try:
        directories = _directories(create=False)
        if directories is None:
            return False
        home, memory, _notes, bindings = directories
        lock = os.path.join(memory, ".global-memory.lock")
        with store.local_path_guard(home, memory), store.path_lock(lock), \
                store.local_path_guard(home, bindings) as bindings_anchor:
            return _binding_present_locked(
                bindings, bindings_anchor, identifier, association,
            )
    except (FileNotFoundError, ValueError, store.ConcurrentWriteError, OSError):
        return False


def scan(query_terms=None, matcher=None):
    base = {
        "path": DISPLAY_PATH,
        "present": False,
        "valid_count": 0,
        "ignored_count": 0,
        "reason_counts": {},
        "hits": [],
    }
    try:
        directories = _directories(create=False)
        if directories is None:
            return base
        home, memory, notes, bindings = directories
        lock = os.path.join(memory, ".global-memory.lock")
        with store.local_path_guard(home, memory), store.path_lock(lock), \
                store.local_path_guard(home, notes) as notes_anchor, \
                store.local_path_guard(home, bindings) as bindings_anchor:
            active, binding_reasons, _binding_count = _scan_bindings_locked(
                bindings, bindings_anchor,
            )
            all_hits, reasons = _scan_locked(notes, notes_anchor, active)
            for reason, count in binding_reasons.items():
                reasons[reason] = reasons.get(reason, 0) + count
            if query_terms:
                hits = [
                    hit for hit in all_hits
                    if matcher(hit["topic"] + " " + hit["summary"], query_terms)
                ]
            else:
                hits = all_hits
            hits = [
                {key: value for key, value in hit.items()
                 if key != "related"}
                for hit in hits
            ]
        base.update({
            "present": True,
            "valid_count": len(all_hits),
            "ignored_count": sum(reasons.values()),
            "reason_counts": dict(sorted(reasons.items())),
            "hits": hits,
        })
    except (ValueError, store.ConcurrentWriteError, OSError):
        base.update({
            "present": True,
            "ignored_count": 1,
            "reason_counts": {"unsafe_parent": 1},
        })
    return base
