"""Fail-closed portable projection for local memory export handoffs."""
import base64
import hashlib
import ipaddress
import json
import os
import re
import unicodedata
from datetime import date

from . import clock, contracts, rows, store, summaries
from .cli import die, resolve_root, usage

_PATH = ".kimiflow/project/PRIVACY-CAPSULE.json"
_MAX_ROWS = 20
_SAFE_KIND = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_SAFE_TOPIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_URL = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://|\bwww\.|\b(?:[a-z0-9-]+\.)+[a-z]{2,63}\b"
)
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+")
_PORTABLE_CREDENTIAL = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{20,}"
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"
    r"|\bAIza[A-Za-z0-9_-]{35}\b"
    r"|\bgh[opusr]_[A-Za-z0-9]{36}\b"
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{8,})\."
    r"([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]*)(?![A-Za-z0-9_-])"
)
_PATHISH = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Z]:[\\/]|/[^\s]+|\.{1,2}/|~[/\\]|\\\\[^\s\\]+\\[^\s]+|"
    r"(?:[^\s/\\]+[/\\])+[^\s/\\]+(?:\:[0-9]+)?)"
)
_SENSITIVE_DOTFILE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])\.(?:env|npmrc|pypirc|netrc|aws|ssh)(?:\.[A-Za-z0-9_.-]+)?(?=$|[\s),:])"
)
_LOCAL_ENDPOINT = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:localhost(?::[0-9]{1,5})?|"
    r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::[0-9]{1,5})?|"
    r"\[[0-9a-f:]+\](?::[0-9]{1,5})?)(?=$|[\s),.;!?])"
)
_IPV6_TOKEN = re.compile(
    r"(?i)(?<![0-9a-f:])([0-9a-f:]*:[0-9a-f:]+)(?:%[A-Za-z0-9_.-]+)?(?![0-9a-f:])"
)
_IDNA_DOT_TRANSLATION = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})


def _jq_or(value, default):
    return default if value is None or value is False else value


def _fresh_evidence(root, row):
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
    snapshots = []
    current = []
    for ref in evidence:
        sanitized = rows.sanitize_evidence_ref(root, ref)
        if sanitized in ("NOT VERIFIED", "OUTSIDE_REPO"):
            return False
        path = rows.evidence_file_path(root, sanitized)
        snapshot = store.local_file_snapshot(root, path)
        if snapshot is None:
            return False
        snapshots.append((path, snapshot))
        digest = hashlib.sha256(snapshot[1]).hexdigest()
        current.append({
            "ref": sanitized,
            "path": os.path.relpath(path, root),
            "sha256": digest,
            "digest": digest,
            "digest_algorithm": "sha256",
            "status": "current",
        })
    if any(snapshot != store.local_file_snapshot(root, path)
           for path, snapshot in snapshots):
        return False
    return contracts.dumps(stored) == contracts.dumps(current)


def _has_control(text):
    return any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029"
               for char in text)


def _contains_provenance(root, row, text):
    lowered = unicodedata.normalize("NFKC", text).casefold()
    values = []
    rid = row.get("id")
    if isinstance(rid, str) and rid:
        values.append(rid)
    root_name = os.path.basename(os.path.normpath(root))
    if root_name:
        values.append(root_name)
    evidence = row.get("evidence")
    if isinstance(evidence, list):
        values.extend(ref for ref in evidence if isinstance(ref, str) and ref)
    return any(unicodedata.normalize("NFKC", value).casefold() in lowered
               for value in values)


def _strip_domain_edges(value):
    def label_char(char):
        return char.isalnum() or unicodedata.category(char).startswith("M")

    start = 0
    end = len(value)
    while start < end and not label_char(value[start]):
        start += 1
    while end > start and not label_char(value[end - 1]):
        end -= 1
    return value[start:end]


def _contains_idn_domain(text):
    for raw in text.split():
        candidate = raw
        if "@" in candidate:
            candidate = candidate.rsplit("@", 1)[1]
        candidate = candidate.split("/", 1)[0]
        candidate = _strip_domain_edges(candidate)
        if candidate.count(":") == 1:
            host, port = candidate.rsplit(":", 1)
            if port.isdigit():
                candidate = host
        if candidate.isascii() or "." not in candidate:
            continue
        try:
            ascii_domain = candidate.encode("idna").decode("ascii")
        except UnicodeError:
            continue
        labels = ascii_domain.split(".")
        if len(labels) >= 2 and all(labels) and len(labels[-1]) >= 2:
            return True
    return False


def _contains_bare_ipv6(text):
    for match in _IPV6_TOKEN.finditer(text):
        try:
            if ipaddress.ip_address(match.group(1)).version == 6:
                return True
        except ValueError:
            continue
    return False


def _contains_jwt(text):
    """Recognize compact JWTs by a bounded base64url JSON header with `alg`."""
    for match in _JWT.finditer(text):
        encoded = match.group(1)
        try:
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            header = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            continue
        if isinstance(header, dict) and isinstance(header.get("alg"), str) and header["alg"]:
            return True
    return False


def portable_entry(root, row):
    """Return the six-field portable entry, or one aggregate omission reason."""
    if not isinstance(row, dict):
        return None, "malformed"
    source_id = row.get("id")
    if not isinstance(source_id, str) or not source_id:
        return None, "invalid_source_id"
    if _jq_or(row.get("status"), "current") != "current":
        return None, "not_current"
    if _jq_or(row.get("sensitivity"), "normal") not in ("normal", "public"):
        return None, "sensitive"
    confidence = row.get("confidence")
    if confidence not in ("medium", "high"):
        return None, "confidence"
    kind = row.get("kind")
    topic = row.get("topic")
    summary = row.get("summary")
    verified = row.get("last_verified")
    if (not isinstance(kind, str) or not _SAFE_KIND.fullmatch(kind)
            or not isinstance(topic, str) or not _SAFE_TOPIC.fullmatch(topic)
            or not isinstance(summary, str) or not summary.strip() or len(summary) > 500
            or not isinstance(verified, str) or not _DATE.fullmatch(verified)):
        return None, "invalid_field"
    try:
        date.fromisoformat(verified)
    except ValueError:
        return None, "invalid_field"
    if summaries.learning_is_stale(row):
        return None, "stale"
    fields = (kind, topic, summary)
    combined = "\n".join(fields)
    canonical_fields = tuple(unicodedata.normalize("NFKC", value) for value in fields)
    classifier_inputs = list(canonical_fields)
    for separator in ("\n", " ", ""):
        for start in range(len(canonical_fields)):
            for end in range(start + 2, len(canonical_fields) + 1):
                classifier_inputs.append(separator.join(canonical_fields[start:end]))
    canonical_inputs = tuple(
        value.translate(_IDNA_DOT_TRANSLATION) for value in classifier_inputs
    )
    if any(_has_control(value) for value in fields):
        return None, "control_character"
    security_inputs = canonical_fields + (" ".join(canonical_fields),)
    if any(not rows.memory_security_json(value)["ok"] for value in security_inputs):
        return None, "unsafe_content"
    if any(rows.has_secret_value(value) or _PORTABLE_CREDENTIAL.search(value)
           or _contains_jwt(value)
           for value in canonical_inputs):
        return None, "secret"
    if any(_URL.search(value) or _contains_idn_domain(value)
           or _LOCAL_ENDPOINT.search(value) or _contains_bare_ipv6(value)
           for value in canonical_inputs):
        return None, "url"
    if any(_EMAIL.search(value) for value in canonical_inputs):
        return None, "email"
    if any(_PATHISH.search(value) or _SENSITIVE_DOTFILE.search(value)
           for value in canonical_inputs):
        return None, "path"
    if any(_contains_provenance(root, row, value) for value in canonical_inputs):
        return None, "local_provenance"
    if not _fresh_evidence(root, row):
        return None, "evidence_stale"

    content = {
        "kind": kind,
        "topic": topic,
        "summary": summary,
        "confidence": confidence,
        "last_verified": verified,
    }
    canonical = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return dict({"capsule_id": "cap_" + hashlib.sha256(canonical).hexdigest()}, **content), None


def capsule_json(root):
    learnings = os.path.join(root, ".kimiflow", "project", "LEARNINGS.jsonl")
    store.require_local_path(root, learnings)
    exported = []
    reason_counts = {}
    omitted = 0
    try:
        with open(learnings, "r", encoding="utf-8", newline="") as handle:
            raw_lines = handle.read().split("\n")
    except (OSError, UnicodeDecodeError):
        raw_lines = []
    for raw in raw_lines:
        if not raw.strip():
            continue
        row = store.parse_json_object_strict(raw.strip())
        if row is None:
            omitted += 1
            reason_counts["malformed"] = reason_counts.get("malformed", 0) + 1
            continue
        entry, reason = portable_entry(root, row)
        if reason is not None:
            omitted += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        elif len(exported) < _MAX_ROWS:
            exported.append(entry)
        else:
            omitted += 1
            reason_counts["limit"] = reason_counts.get("limit", 0) + 1
    return {
        "schema_version": 1,
        "generated_at": clock.iso_now(),
        "policy": "portable_allowlist_export_only",
        "exported_count": len(exported),
        "omitted_count": omitted,
        "reason_counts": reason_counts,
        "rows": exported,
    }


def run(argv):
    root = ""
    pretty = False
    write = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("capsule: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    try:
        payload = capsule_json(root)
    except ValueError as exc:
        return die("capsule: %s" % exc, 1)
    if write:
        path = os.path.join(root, ".kimiflow", "project", "PRIVACY-CAPSULE.json")
        try:
            store.require_local_path(root, path)
        except ValueError as exc:
            return die("capsule: %s" % exc, 1)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        store.atomic_write(path, contracts.dumps(payload, pretty=True) + "\n", mode=0o600)
    out = dict(payload)
    out["status"] = "written" if write else "preview"
    out["written"] = write
    out["path"] = _PATH
    contracts.json_print(out, pretty)
    return 0
