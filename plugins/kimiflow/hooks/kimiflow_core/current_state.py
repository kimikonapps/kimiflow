"""Risk-shaped, version-aware current-source evidence gate."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from urllib.parse import urlsplit


MAX_JSON_BYTES = 256 * 1024
MAX_RECALL_BYTES = 1024 * 1024
FUTURE_TOLERANCE = timedelta(minutes=5)
ALLOWED_SOURCE_TYPES = (
    "official_docs",
    "release_notes",
    "schema_or_manifest",
    "official_github",
)
FRESHNESS_BASES = {
    "current_official_page",
    "release_notes",
    "version_manifest",
    "stable_standard",
}
TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
    r"[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
SHA_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
SOURCE_REQUIRED = {
    "source_type",
    "source_url",
    "retrieved_at",
    "applies_to",
    "version_or_release",
    "freshness_basis",
    "status",
}
SOURCE_OPTIONAL = {
    "published_or_updated_at",
    "etag",
    "last_modified",
    "content_digest",
}
RECEIPT_KEYS = {
    "schema_version",
    "status",
    "checked_at",
    "research_subject_sha256",
    "sources",
}
TERM_STOPWORDS = {
    "about", "aktuell", "aktuelle", "aktuellen", "also", "and", "auch",
    "build", "darf", "dass", "den", "der", "die", "dies", "diese", "dürfen",
    "eine", "einen", "einer", "findings", "for", "from", "implement", "in",
    "ist", "mit", "methods", "methoden", "nicht", "oder", "research",
    "recherchiere", "soll", "the", "und", "veraltet", "werden", "with",
}


class CurrentStateError(ValueError):
    """Invalid command input rather than a closed evidence verdict."""


def _emit(status, risk, reason, detail=""):
    print(
        "CURRENT_STATE_GATE\t%s\trisk=%s\treason=%s\tdetail=%s"
        % (status, risk, reason, detail)
    )
    return 0


def _bounded_regular(path, maximum):
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    if info.st_size <= 0 or info.st_size > maximum:
        return None
    try:
        with open(path, "rb") as handle:
            payload = handle.read(maximum + 1)
    except OSError:
        return None
    if len(payload) > maximum:
        return None
    return payload


def _read_json(path, maximum):
    payload = _bounded_regular(path, maximum)
    if payload is None:
        raise CurrentStateError("unsafe-json")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CurrentStateError("malformed-json") from exc
    return value


def _read_text(path, maximum, allow_empty=False):
    if not path:
        return ""
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise CurrentStateError("missing-text") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CurrentStateError("unsafe-text")
    if info.st_size > maximum or (not allow_empty and info.st_size <= 0):
        raise CurrentStateError("unsafe-text")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read(maximum + 1)
    except (OSError, UnicodeError) as exc:
        raise CurrentStateError("unsafe-text") from exc


def _timestamp(value):
    if not isinstance(value, str) or TIMESTAMP_RE.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def _bounded_string(value, maximum=240):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def _subject(text):
    normalized = " ".join(
        unicodedata.normalize("NFKC", text).casefold().split()
    )
    digest = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    terms = []
    for token in re.findall(r"[^\W_][\w.+-]{2,}", normalized, re.UNICODE):
        token = token.strip(".+-_")
        if (
            len(token) < 3
            or token in TERM_STOPWORDS
            or token.isdigit()
            or token in terms
        ):
            continue
        terms.append(token)
        if len(terms) == 12:
            break
    if not terms:
        terms = [digest[-12:]]
    return digest, terms


def _https_url(value):
    if not _bounded_string(value, 2048):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not any(char.isspace() for char in value)
    )


def _risk_for_text(text):
    high_patterns = (
        r"\b(?:codex|claude[ -]?code|cursor|windsurf|plugins?|marketplace|"
        r"hooks?|skills?|mcp|model context protocol)\b",
        r"\b(?:security|auth|oauth|payments?|stripe|privacy|deployment|"
        r"deploy|ci/cd|app store|external service|hosted api|sdk)\b",
    )
    medium_pattern = (
        r"\b(?:library|libraries|framework|dependencies?|packages?|version|api|"
        r"tooling|typescript|react|vite|node|python|swift|xcode|npm|pip|"
        r"coding|programming|programmierung|architecture|architectural|architektur)\b"
    )
    reasons = []
    if re.search(high_patterns[0], text, re.IGNORECASE):
        reasons.append("host_or_plugin_surface")
    if re.search(high_patterns[1], text, re.IGNORECASE):
        reasons.append("security_or_external_platform")
    if reasons:
        return "high", reasons
    if re.search(medium_pattern, text, re.IGNORECASE):
        return "medium", ["possibly_changing_tooling_or_api"]
    return "low", []


def _assess_text(text):
    risk, reasons = _risk_for_text(text)
    subject_sha256, research_terms = _subject(text)
    if risk == "high":
        horizon = 30
        status = "required"
        sources = list(ALLOWED_SOURCE_TYPES)
        minimum = 1
    elif risk == "medium":
        horizon = 90
        status = "recommended"
        sources = list(ALLOWED_SOURCE_TYPES)
        minimum = 1
    else:
        horizon = None
        status = "not_required"
        sources = []
        minimum = 0
    return {
        "schema_version": 2,
        "current_state_risk": risk,
        "current_state_reasons": reasons,
        "freshness_horizon": ("%sd" % horizon) if horizon is not None else None,
        "freshness_horizon_days": horizon,
        "minimum_source_count": minimum,
        "acceptable_source_types": sources,
        "research_subject_sha256": subject_sha256,
        "research_terms": research_terms,
        # Retained as a schema-1 reader hint. Schema 2 uses acceptable_source_types.
        "required_source_types": sources,
        "status": status,
    }


def assess(path):
    return _assess_text(_read_text(path, MAX_RECALL_BYTES))


def _assessment(value):
    if not isinstance(value, dict):
        raise CurrentStateError("malformed-assessment")
    schema = value.get("schema_version", 1)
    if isinstance(schema, bool) or not isinstance(schema, int) or schema not in (1, 2):
        raise CurrentStateError("malformed-assessment")
    risk = value.get("current_state_risk")
    if risk not in ("low", "medium", "high"):
        raise CurrentStateError("malformed-assessment")
    if schema == 1:
        return schema, risk, None, None, None, None, None
    horizon = value.get("freshness_horizon_days")
    minimum = value.get("minimum_source_count")
    acceptable = value.get("acceptable_source_types")
    subject_sha256 = value.get("research_subject_sha256")
    research_terms = value.get("research_terms")
    if (
        not isinstance(subject_sha256, str)
        or SHA_RE.fullmatch(subject_sha256) is None
        or not isinstance(research_terms, list)
        or not 1 <= len(research_terms) <= 12
        or len(research_terms) != len(set(research_terms))
        or any(
            not isinstance(term, str)
            or not 3 <= len(term) <= 80
            or term != unicodedata.normalize("NFKC", term).casefold()
            or re.fullmatch(r"[^\W_][\w.+-]{2,79}", term, re.UNICODE) is None
            for term in research_terms
        )
    ):
        raise CurrentStateError("malformed-assessment")
    if risk == "low":
        if horizon is not None or minimum != 0 or acceptable != []:
            raise CurrentStateError("malformed-assessment")
        return schema, risk, None, None, None, subject_sha256, tuple(research_terms)
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, int)
        or not 1 <= horizon <= 3650
        or isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or not 1 <= minimum <= 8
        or not isinstance(acceptable, list)
        or not acceptable
        or len(acceptable) != len(set(acceptable))
        or any(item not in ALLOWED_SOURCE_TYPES for item in acceptable)
    ):
        raise CurrentStateError("malformed-assessment")
    return (
        schema,
        risk,
        horizon,
        minimum,
        tuple(acceptable),
        subject_sha256,
        tuple(research_terms),
    )


def _legacy_verify(risk, recall_path):
    if not recall_path:
        return _emit("CLOSED", risk, "missing-recall", "missing --recall")
    try:
        text = _read_text(recall_path, MAX_RECALL_BYTES, allow_empty=True)
    except CurrentStateError:
        return _emit("CLOSED", risk, "missing-recall", recall_path)
    checked = re.search(
        r'(^|[\s-])Status:\s*checked(?:\s|$)|"status"\s*:\s*"checked"',
        text,
        re.IGNORECASE,
    )
    if not checked:
        return _emit("CLOSED", risk, "not-checked", "recall lacks Status: checked")
    source = re.search(
        r'source_type:\s*(?:official_docs|release_notes|schema_or_manifest|official_github)'
        r'|"source_type"\s*:\s*"(?:official_docs|release_notes|schema_or_manifest|official_github)"',
        text,
        re.IGNORECASE,
    )
    if not source:
        return _emit("CLOSED", risk, "missing-primary-source", "recall lacks a primary source_type")
    url = re.search(
        r'source_url:\s*https?://|"source_url"\s*:\s*"https?://',
        text,
        re.IGNORECASE,
    )
    if not url:
        return _emit("CLOSED", risk, "missing-source-url", "recall lacks source_url")
    count = len(
        re.findall(
            r'source_type:\s*(?:official_docs|release_notes|schema_or_manifest|official_github)'
            r'|"source_type"\s*:\s*"(?:official_docs|release_notes|schema_or_manifest|official_github)"',
            text,
            re.IGNORECASE,
        )
    )
    return _emit("OPEN", risk, "checked", "primary_sources=%s" % count)


def _schema2_verify(
    risk,
    horizon,
    minimum,
    acceptable,
    subject_sha256,
    research_terms,
    sources_path,
):
    if not sources_path:
        return _emit("CLOSED", risk, "missing-sources", "missing --sources")
    try:
        receipt = _read_json(sources_path, MAX_JSON_BYTES)
    except CurrentStateError as exc:
        reason = "unsafe-sources" if str(exc) == "unsafe-json" else "malformed-sources"
        return _emit("CLOSED", risk, reason, sources_path)
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        return _emit("CLOSED", risk, "malformed-sources", "receipt shape")
    if receipt.get("schema_version") != 2 or receipt.get("status") != "checked":
        return _emit("CLOSED", risk, "not-checked", "schema/status")
    if receipt.get("research_subject_sha256") != subject_sha256:
        return _emit("CLOSED", risk, "research-subject-mismatch", "assessment")
    checked_at = _timestamp(receipt.get("checked_at"))
    if checked_at is None:
        return _emit("CLOSED", risk, "timestamp-invalid", "checked_at")
    now = datetime.now(timezone.utc)
    if checked_at > now + FUTURE_TOLERANCE:
        return _emit("CLOSED", risk, "future-timestamp", "checked_at")
    if now - checked_at > timedelta(days=horizon):
        return _emit("CLOSED", risk, "stale-source", "checked_at")
    sources = receipt.get("sources")
    if not isinstance(sources, list) or len(sources) < minimum or len(sources) > 16:
        return _emit("CLOSED", risk, "source-count", "minimum=%s" % minimum)
    seen = set()
    for index, source in enumerate(sources):
        detail = "sources[%s]" % index
        if (
            not isinstance(source, dict)
            or not SOURCE_REQUIRED.issubset(source)
            or not set(source).issubset(SOURCE_REQUIRED | SOURCE_OPTIONAL)
        ):
            return _emit("CLOSED", risk, "source-shape-invalid", detail)
        source_type = source.get("source_type")
        if source_type not in acceptable:
            return _emit("CLOSED", risk, "source-type-not-acceptable", detail)
        if not _https_url(source.get("source_url")):
            return _emit("CLOSED", risk, "source-url-invalid", detail)
        if source.get("status") != "current":
            return _emit("CLOSED", risk, "source-not-current", detail)
        retrieved_at = _timestamp(source.get("retrieved_at"))
        if retrieved_at is None:
            return _emit("CLOSED", risk, "timestamp-invalid", detail)
        if retrieved_at > now + FUTURE_TOLERANCE:
            return _emit("CLOSED", risk, "future-timestamp", detail)
        if retrieved_at > checked_at + FUTURE_TOLERANCE:
            return _emit("CLOSED", risk, "source-after-check", detail)
        if now - retrieved_at > timedelta(days=horizon):
            return _emit("CLOSED", risk, "stale-source", detail)
        if not _bounded_string(source.get("applies_to")):
            return _emit("CLOSED", risk, "source-applicability-missing", detail)
        _, applies_terms = _subject(source["applies_to"])
        if not set(applies_terms).intersection(research_terms):
            return _emit("CLOSED", risk, "source-applicability-mismatch", detail)
        if not _bounded_string(source.get("version_or_release")):
            return _emit("CLOSED", risk, "source-version-missing", detail)
        if source.get("freshness_basis") not in FRESHNESS_BASES:
            return _emit("CLOSED", risk, "freshness-basis-invalid", detail)
        published = source.get("published_or_updated_at")
        if published is not None:
            published_at = _timestamp(published)
            if published_at is None:
                return _emit("CLOSED", risk, "timestamp-invalid", detail)
            if published_at > now + FUTURE_TOLERANCE:
                return _emit("CLOSED", risk, "future-timestamp", detail)
        for name in ("etag", "last_modified"):
            if name in source and not _bounded_string(source[name], 500):
                return _emit("CLOSED", risk, "source-validator-invalid", detail)
        if "content_digest" in source and (
            not isinstance(source["content_digest"], str)
            or SHA_RE.fullmatch(source["content_digest"]) is None
        ):
            return _emit("CLOSED", risk, "source-digest-invalid", detail)
        identity = (
            source["source_url"].rstrip("/"),
            source["applies_to"].strip().casefold(),
            source["version_or_release"].strip().casefold(),
        )
        if identity in seen:
            return _emit("CLOSED", risk, "duplicate-source", detail)
        seen.add(identity)
    return _emit("OPEN", risk, "checked", "current_sources=%s" % len(sources))


def verify(assessment_path, recall_path, sources_path, input_path):
    try:
        value = _read_json(assessment_path, MAX_JSON_BYTES)
        (
            schema,
            risk,
            horizon,
            minimum,
            acceptable,
            subject_sha256,
            research_terms,
        ) = _assessment(value)
    except CurrentStateError as exc:
        reason = "unsafe-assessment" if str(exc) == "unsafe-json" else "malformed-assessment"
        return _emit("CLOSED", "unknown", reason, assessment_path)
    if schema == 2:
        if not input_path:
            return _emit("CLOSED", risk, "missing-subject-input", "missing --input")
        try:
            current_input = _read_text(input_path, MAX_RECALL_BYTES)
        except CurrentStateError:
            return _emit("CLOSED", risk, "unsafe-subject-input", input_path)
        if value != _assess_text(current_input):
            return _emit("CLOSED", risk, "research-policy-mismatch", input_path)
    if risk == "low":
        return _emit("OPEN", risk, "not-required", "current-state check not required")
    if schema == 1:
        return _legacy_verify(risk, recall_path)
    return _schema2_verify(
        risk,
        horizon,
        minimum,
        acceptable,
        subject_sha256,
        research_terms,
        sources_path,
    )


def _parser():
    parser = argparse.ArgumentParser(prog="current-state-gate.sh")
    subparsers = parser.add_subparsers(dest="command", required=True)
    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("--input", required=True)
    assess_parser.add_argument("--pretty", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--assessment", required=True)
    verify_parser.add_argument("--input")
    verify_parser.add_argument("--recall")
    verify_parser.add_argument("--sources")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "assess":
        try:
            value = assess(args.input)
        except CurrentStateError as exc:
            print("current-state-gate: %s" % exc, file=sys.stderr)
            return 2
        print(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2 if args.pretty else None,
                separators=None if args.pretty else (",", ":"),
            )
        )
        return 0
    return verify(args.assessment, args.recall, args.sources, args.input)


if __name__ == "__main__":
    raise SystemExit(main())
