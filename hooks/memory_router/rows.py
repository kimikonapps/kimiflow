"""Row-validation helpers: the prompt-injection/exfiltration security gate and the
evidence sanitization/fingerprinting helpers. Behavioral ports of the Bash at
kimiflow--v0.1.50, with intentional divergences recorded in spec §12 (path-traversal
fix in sanitize_evidence_ref, secret-value scan). These return Python objects
(dict/list); serialization to stdout stays at the contracts.dumps boundary in the
calling subcommand."""
import hashlib
import json
import os
import re

from .paths import rel_path

MATURITY_PROBATIONARY = "probationary"
MATURITY_DURABLE = "durable"
MATURITY_INVALID = "invalid"

_LEARNING_LIFECYCLE_FIELDS = frozenset((
    "maturity",
    "status",
    "curation",
    "last_verified",
    "recall_id",
))


def learning_maturity(row):
    """Missing means durable for pre-tier rows; explicit invalid values fail closed."""
    if "maturity" not in row:
        return MATURITY_DURABLE
    candidate = row.get("maturity")
    return candidate if candidate in (
        MATURITY_PROBATIONARY, MATURITY_DURABLE
    ) else MATURITY_INVALID


def learning_is_durable(row):
    return learning_maturity(row) == MATURITY_DURABLE


def learning_content_fingerprint(row):
    """Bind verified use to meaning/provenance, excluding mutable lifecycle state."""
    if not isinstance(row, dict):
        raise ValueError("learning content fingerprint requires an object")
    projection = {
        field: value for field, value in row.items()
        if field not in _LEARNING_LIFECYCLE_FIELDS
    }
    try:
        encoded = json.dumps(
            projection,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("learning content is not canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


# memory_security_json patterns, lifted from Bash grep -E @ v0.1.50. The Bash lowercases
# the text first (tr [:upper:][:lower:]); we lower() then search. Default re flags keep
# "." from crossing newlines, matching grep's line-by-line .{0,N} semantics.
_INSTRUCTION_OVERRIDE = re.compile(
    r"(ignore|disregard|override).{0,40}(previous|prior|above|system|developer|instructions)"
    r"|system prompt|developer message|hidden instruction|prompt injection|jailbreak"
)
# NOTE: `\\.env` is a faithful port of a Bash latent quirk — the single-quoted grep string
# `\\.env` is ERE for "literal backslash + any char + env", NOT the intended ".env". So plain
# ".env" is not flagged but "\Xenv" is. Kept as-is for parity; fixing the gate is a separate,
# explicitly-blessed change (it would alter security-gate matching).
_EXFILTRATION = re.compile(
    r"(exfiltrat|send|post|upload|leak|reveal).{0,80}"
    r"(secret|token|credential|password|private key|api key|\\.env)"
    r"|credential harvesting|ssh backdoor"
)
# hidden_unicode is checked against the ORIGINAL text (not lowercased). Bash gates this on
# `command -v perl`; Python's stdlib always has it, so we always check — like the classify
# jq-absent divergence, no diff on targets that have perl. See spec §12.
_HIDDEN_UNICODE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f]")


def memory_security_json(text):
    lower = text.lower()
    reasons = []
    if _INSTRUCTION_OVERRIDE.search(lower):
        reasons.append("instruction_override")
    if _EXFILTRATION.search(lower):
        reasons.append("exfiltration_or_credential_request")
    if _HIDDEN_UNICODE.search(text):
        reasons.append("hidden_unicode")
    return {"ok": len(reasons) == 0, "reasons": reasons}


# Minimal secret-VALUE pattern class (audit fix B3-P3, spec §12): high-precision token
# shapes only — AWS access-key ids, PEM private-key headers, GitHub/Slack tokens, and
# key=value assignments with a long literal. A match never closes the gate (the learning
# may legitimately describe a leak); the write path forces sensitivity=security instead,
# which quarantines the row from vault-sync candidacy.
_SECRET_VALUES = re.compile(
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bghp_[A-Za-z0-9]{36}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    r"|\bxox[baprs]-[A-Za-z0-9-]{10,}\b"
    r"|(?i:api[_-]?key|secret|token|passwd|password)\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+.]{16,}"
)


def has_secret_value(text):
    return _SECRET_VALUES.search(text) is not None


def file_digest_json(path):
    # Bash prefers shasum/sha256sum (sha256), then cksum, then unavailable. Python's stdlib
    # always provides sha256, so we always use it (identical hex to shasum on targets). The
    # cksum/unavailable fallbacks are unreachable here; see spec §12.
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        # Mirrors Bash: branch chosen by tool availability (sha256), but the digest is empty
        # on read failure -> caller maps that to "unverified".
        return {"algorithm": "sha256", "digest": "", "sha256": ""}
    return {"algorithm": "sha256", "digest": digest, "sha256": digest}


def evidence_file_path(root, ref):
    ref_path = re.sub(r":[0-9]+$", "", ref)   # sed -E 's/:[0-9]+$//'
    if ref_path.startswith("/"):
        return ref_path
    return "%s/%s" % (root, ref_path)


def evidence_line_suffix(ref):
    match = re.match(r"^.*(:[0-9]+)$", ref)   # sed -nE 's/^.*(:[0-9]+)$/\1/p' (greedy -> last)
    return match.group(1) if match else ""


def sanitize_evidence_ref(root, ref):
    # Intentional divergence from the Bash `case "$root"/*|"$root"` (spec §12): the raw
    # prefix check let `../`-refs escape the root (`/r/../etc/passwd` passed as in-repo).
    # normpath is lexical only — no symlink resolution, so symlinked roots (e.g. /tmp on
    # macOS) keep matching their own prefix.
    if ref in ("NOT VERIFIED", "OUTSIDE_REPO"):
        return ref
    root = os.path.normpath(root)
    path = os.path.normpath(evidence_file_path(root, ref))
    suffix = evidence_line_suffix(ref)
    if path == root or path.startswith(root + "/"):
        return rel_path(root, path) + suffix
    return "OUTSIDE_REPO"


def sanitize_evidence_json(root, evidence):
    out = []
    for ref in evidence:
        if not ref:   # [ -n "$ref" ] || continue
            continue
        out.append(sanitize_evidence_ref(root, ref))
    return out


def evidence_fingerprints_json(root, evidence):
    out = []
    for ref in evidence:
        if not ref:
            continue
        ref = sanitize_evidence_ref(root, ref)
        if ref == "NOT VERIFIED":
            out.append({"ref": ref, "path": ref, "sha256": "", "digest": "",
                        "digest_algorithm": "none", "status": "unverified"})
            continue
        if ref == "OUTSIDE_REPO":
            out.append({"ref": ref, "path": ref, "sha256": "", "digest": "",
                        "digest_algorithm": "none", "status": "outside_root"})
            continue
        path = evidence_file_path(root, ref)
        rel = rel_path(root, path)
        status = "missing"
        sha = ""
        digest = ""
        algorithm = "none"
        if os.path.isfile(path):
            status = "current"
            info = file_digest_json(path)
            sha = info["sha256"]
            digest = info["digest"]
            algorithm = info["algorithm"]
            if not digest:
                status = "unverified"
        out.append({"ref": ref, "path": rel, "sha256": sha, "digest": digest,
                    "digest_algorithm": algorithm, "status": status})
    return out
