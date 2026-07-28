"""Local-first, provider-neutral actionable security evidence."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import posixpath
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from . import phase_reads, workspace_preflight


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_SCOPE_BYTES = 64 * 1024 * 1024
MAX_PROVIDER_OUTPUT = 4 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT = 30
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SCAN_RE = re.compile(r"^scan_[0-9a-f]{32}$")
ACCEPTANCE_RE = re.compile(r"^accept_[0-9a-f]{32}$")
SAFE_RUN_RE = re.compile(r"^\.kimiflow/[A-Za-z0-9][A-Za-z0-9._-]*$")
SECURITY_ARTIFACTS = (
    "SECURITY-SCAN-MANIFEST.json",
    "SECURITY-COVERAGE.json",
    "SECURITY-FINDINGS.json",
    "SECURITY-REPORT.md",
)
THREAT_FIELDS = (
    "assets",
    "entry_points",
    "untrusted_inputs",
    "data_flows",
    "trust_boundaries",
    "auth_assumptions",
    "privileged_actions",
    "security_invariants",
)
FINDING_FIELDS = {
    "finding_id",
    "rule_id",
    "title",
    "severity",
    "confidence",
    "cwe",
    "source",
    "sink",
    "reachability",
    "impact",
    "counterevidence",
    "proof_gaps",
    "remediation",
    "provenance",
    "occurrences",
}
DEPENDENCY_MANIFESTS = {
    "conan.lock",
    "pubspec.lock",
    "mix.lock",
    "go.mod",
    "cabal.project.freeze",
    "stack.yaml.lock",
    "buildscript-gradle.lockfile",
    "pom.xml",
    "bun.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "deps.json",
    "packages.config",
    "packages.lock.json",
    "composer.lock",
    "Pipfile.lock",
    "poetry.lock",
    "requirements.txt",
    "pdm.lock",
    "pylock.toml",
    "uv.lock",
    "renv.lock",
    "Gemfile.lock",
    "gems.locked",
    "Cargo.lock",
    "gradle.lockfile",
}
VERIFICATION_MARKER = (
    "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->"
)
PROVIDER_COMPLETE = {"complete", "findings", "not_applicable"}
PROVIDER_GAP = {
    "missing",
    "failed",
    "refused",
    "quota_limited",
    "timeout",
    "stale",
    "unauthorized",
    "output_limit",
    "unsupported",
}
_LOCAL_STATE_LOCKS = {}
_LOCAL_STATE_LOCKS_GUARD = threading.Lock()


class SecurityError(ValueError):
    def __init__(self, code, message="", exit_code=2):
        super().__init__(code)
        self.code = code
        self.message = message or code
        self.exit_code = exit_code


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    error_code: str = ""


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SecurityError("json_duplicate_key")
        result[key] = value
    return result


def _decode_json(payload):
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except SecurityError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SecurityError("json_invalid") from exc
    if not isinstance(value, dict):
        raise SecurityError("json_invalid")
    return value


def load_json_file(path):
    descriptor = None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > MAX_JSON_BYTES
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise SecurityError("json_invalid")
        payload = bytearray()
        while len(payload) <= MAX_JSON_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_JSON_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > MAX_JSON_BYTES:
            raise SecurityError("json_invalid")
    except SecurityError:
        raise
    except OSError as exc:
        raise SecurityError("json_invalid") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return _decode_json(bytes(payload))


def _read_bytes_at(directory, name, maximum=MAX_JSON_BYTES):
    descriptor = None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory)
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > maximum
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise SecurityError("unsafe_state_path")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > maximum:
            raise SecurityError("unsafe_state_path")
    except SecurityError:
        raise
    except OSError as exc:
        raise SecurityError("unsafe_state_path") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return bytes(payload)


def _read_json_at(directory, name):
    return _decode_json(_read_bytes_at(directory, name))


def _read_text_at(directory, name):
    try:
        return _read_bytes_at(directory, name).decode("utf-8")
    except UnicodeError as exc:
        raise SecurityError("child_evidence_missing") from exc


def _canonical(value):
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SecurityError("json_invalid") from exc


def digest(value):
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_text(value, fallback):
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.replace("\x00", " ").split())
    return text[:500] if text else fallback


def _safe_relative(path):
    if not isinstance(path, str) or not path or "\x00" in path:
        raise SecurityError("path_invalid")
    portable = path.replace("\\", "/")
    normalized = posixpath.normpath(portable)
    if (
        normalized in ("", "..")
        or normalized.startswith("../")
        or portable.startswith("/")
        or re.match(r"^[A-Za-z]:", portable)
    ):
        raise SecurityError("path_invalid")
    return "." if normalized == "." else normalized


def _within(path, root):
    try:
        return os.path.commonpath((os.path.realpath(path), os.path.realpath(root))) == os.path.realpath(root)
    except ValueError:
        return False


def _git_candidate(path):
    current = os.path.realpath(path)
    while True:
        marker = os.path.join(current, ".git")
        try:
            info = os.lstat(marker)
        except FileNotFoundError:
            info = None
        except OSError:
            return ""
        if info is not None and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def _git_root(path):
    if not _git_candidate(path):
        return ""
    try:
        probe = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return ""
    if probe.returncode != 0:
        return ""
    return os.path.realpath(probe.stdout.decode("utf-8", "surrogateescape").strip())


def resolve_scan_root(path, mode):
    requested = os.path.abspath(path)
    if os.path.islink(requested):
        raise SecurityError("scope_symlink")
    target = os.path.realpath(requested)
    if not os.path.exists(target):
        raise SecurityError("scope_missing")
    base = target if os.path.isdir(target) else os.path.dirname(target)
    git_root = _git_root(base)
    if mode in ("diff", "staged"):
        if not git_root:
            raise SecurityError("git_required")
        root = git_root
    else:
        root = git_root or base
    if not _within(target, root):
        raise SecurityError("scope_escape")
    scope = os.path.relpath(target, root).replace(os.sep, "/")
    scope = "." if scope == "." else _safe_relative(scope)
    if scope != "." and _is_internal(scope):
        raise SecurityError("scope_internal")
    return root, scope, target


def _is_internal(rel):
    parts = rel.replace(os.sep, "/").split("/")
    return ".git" in parts or ".kimiflow" in parts


def _read_regular_bytes(path, expected=None, maximum=MAX_FILE_BYTES):
    descriptor = None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > maximum:
            raise OSError("unsafe regular file")
        if expected is not None and (
            opened.st_dev,
            opened.st_ino,
        ) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise OSError("file identity changed")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > maximum:
            raise OSError("file exceeds limit")
        return bytes(payload)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _file_inventory(root, target):
    records = []
    text_rows = []
    skipped = []
    total = 0
    candidates = []
    if os.path.isfile(target):
        candidates.append(target)
    else:
        for current, directories, files in os.walk(target, followlinks=False):
            safe_directories = []
            for name in sorted(directories):
                path = os.path.join(current, name)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                if _is_internal(rel):
                    continue
                if os.path.islink(path):
                    skipped.append({"path": rel, "reason": "symlink"})
                    continue
                safe_directories.append(name)
            directories[:] = safe_directories
            for name in sorted(files):
                candidates.append(os.path.join(current, name))
    for candidate in candidates:
        rel = os.path.relpath(candidate, root).replace(os.sep, "/")
        if _is_internal(rel):
            continue
        try:
            info = os.lstat(candidate)
        except OSError:
            skipped.append({"path": rel, "reason": "unreadable"})
            continue
        if stat.S_ISLNK(info.st_mode):
            skipped.append({"path": rel, "reason": "symlink"})
            continue
        if not stat.S_ISREG(info.st_mode):
            skipped.append({"path": rel, "reason": "non_regular"})
            continue
        if info.st_size > MAX_FILE_BYTES or total + info.st_size > MAX_SCOPE_BYTES:
            skipped.append({"path": rel, "reason": "size_limit"})
            continue
        try:
            payload = _read_regular_bytes(candidate, expected=info)
        except OSError:
            skipped.append({"path": rel, "reason": "unreadable"})
            continue
        total += len(payload)
        record = {
            "path": rel,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        records.append(record)
        if b"\x00" in payload:
            skipped.append({"path": rel, "reason": "binary"})
            continue
        text_rows.append((rel, payload.decode("utf-8", "replace")))
    return records, text_rows, skipped


def _synthetic_stream(rows):
    chunks = []
    line_map = {}
    synthetic_line = 0
    for path, text in rows:
        chunks.append("@@FILE %s\n" % path)
        synthetic_line += 1
        source_line = 0
        for line in text.splitlines():
            source_line += 1
            chunks.append(line + "\n")
            synthetic_line += 1
            line_map[synthetic_line] = (path, source_line)
    return "".join(chunks).encode("utf-8"), line_map


def _git_revision(root, content_digest):
    if not _git_candidate(root):
        return "unversioned:" + content_digest.split(":", 1)[1]
    try:
        probe = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return "unversioned:" + content_digest.split(":", 1)[1]
    if probe.returncode == 0:
        return probe.stdout.decode("ascii", "replace").strip()
    return "unversioned:" + content_digest.split(":", 1)[1]


def snapshot_scope(root, target):
    root = os.path.realpath(root)
    target = os.path.realpath(target)
    if not _within(target, root):
        raise SecurityError("scope_escape")
    records, rows, skipped = _file_inventory(root, target)
    stream, line_map = _synthetic_stream(rows)
    content_digest = digest({"files": records, "skipped": skipped})
    return {
        "scope": "." if target == root else os.path.relpath(target, root).replace(os.sep, "/"),
        "content_digest": content_digest,
        "revision": _git_revision(root, content_digest),
        "files": records,
        "skipped": skipped,
        "_stream": stream,
        "_line_map": line_map,
    }


def _decode_git_path(value):
    if not isinstance(value, str) or not value:
        raise SecurityError("diff_failed")
    if not value.startswith('"'):
        decoded = value
    else:
        if len(value) < 2 or not value.endswith('"'):
            raise SecurityError("diff_failed")
        output = bytearray()
        index = 1
        escapes = {
            "a": 7,
            "b": 8,
            "t": 9,
            "n": 10,
            "v": 11,
            "f": 12,
            "r": 13,
            "\\": 92,
            '"': 34,
        }
        while index < len(value) - 1:
            character = value[index]
            if character != "\\":
                output.extend(character.encode("utf-8", "surrogateescape"))
                index += 1
                continue
            index += 1
            if index >= len(value) - 1:
                raise SecurityError("diff_failed")
            character = value[index]
            if character in "01234567":
                digits = character
                index += 1
                while index < len(value) - 1 and len(digits) < 3 and value[index] in "01234567":
                    digits += value[index]
                    index += 1
                output.append(int(digits, 8))
                continue
            if character not in escapes:
                raise SecurityError("diff_failed")
            output.append(escapes[character])
            index += 1
        decoded = output.decode("utf-8", "surrogateescape")
    if decoded == "/dev/null":
        return ""
    if decoded.startswith(("a/", "b/")):
        decoded = decoded[2:]
    return _safe_relative(decoded)


def _parse_diff(root, scope, staged=False):
    args = [
        "git", "-C", root, "diff", "--no-ext-diff", "--no-textconv",
        "--no-renames", "--unified=0",
    ]
    if staged:
        args.append("--cached")
    else:
        args.append("HEAD")
    args.extend(["--", scope])
    probe = _run_bounded(
        args,
        cwd=root,
        timeout=DEFAULT_TIMEOUT,
        max_output=MAX_SCOPE_BYTES,
    )
    if probe.returncode != 0 or probe.error_code:
        raise SecurityError("diff_failed")
    current_path = ""
    current_line = 0
    rows = {}
    skipped = []
    for raw in probe.stdout.decode("utf-8", "surrogateescape").splitlines():
        if raw.startswith("+++ "):
            current_path = _decode_git_path(raw[4:])
            if current_path:
                rows.setdefault(current_path, [])
        elif raw.startswith("Binary files ") and raw.endswith(" differ"):
            endpoints = raw[len("Binary files "):-len(" differ")].rsplit(" and ", 1)
            if len(endpoints) == 2:
                binary_path = _decode_git_path(endpoints[1])
                if binary_path:
                    skipped.append({"path": binary_path, "reason": "binary"})
        elif raw.startswith("@@ "):
            match = re.search(r"\+([0-9]+)(?:,[0-9]+)?", raw)
            current_line = int(match.group(1)) - 1 if match else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            current_line += 1
            if current_path and not _is_internal(current_path):
                rows.setdefault(current_path, []).append((current_line, raw[1:]))
        elif not raw.startswith("-") and not raw.startswith("\\"):
            current_line += 1
    if not staged:
        other = _run_bounded(
            ["git", "-C", root, "ls-files", "--others", "--exclude-standard", "-z", "--", scope],
            cwd=root,
            timeout=DEFAULT_TIMEOUT,
            max_output=4 * 1024 * 1024,
        )
        if other.returncode != 0 or other.error_code:
            raise SecurityError("diff_failed")
        for raw_path in other.stdout.split(b"\0"):
            if not raw_path:
                continue
            rel = raw_path.decode("utf-8", "surrogateescape")
            if _is_internal(rel):
                continue
            full = os.path.join(root, rel)
            try:
                info = os.lstat(full)
            except OSError:
                skipped.append({"path": rel, "reason": "unreadable"})
                continue
            if stat.S_ISLNK(info.st_mode):
                skipped.append({"path": rel, "reason": "symlink"})
                continue
            if not stat.S_ISREG(info.st_mode):
                skipped.append({"path": rel, "reason": "non_regular"})
                continue
            if info.st_size > MAX_FILE_BYTES:
                skipped.append({"path": rel, "reason": "size_limit"})
                continue
            try:
                payload = _read_regular_bytes(full, expected=info)
            except OSError:
                skipped.append({"path": rel, "reason": "unreadable"})
                continue
            if b"\x00" in payload:
                skipped.append({"path": rel, "reason": "binary"})
                continue
            rows[rel] = [
                (index, line)
                for index, line in enumerate(payload.decode("utf-8", "replace").splitlines(), 1)
            ]
    chunks = []
    line_map = {}
    synthetic_line = 0
    records = []
    total = 0
    skipped_paths = {row["path"] for row in skipped}
    for rel in sorted(rows):
        if rel in skipped_paths:
            continue
        full = os.path.join(root, rel)
        if os.path.lexists(full):
            try:
                info = os.lstat(full)
            except OSError:
                skipped.append({"path": rel, "reason": "unreadable"})
                continue
            if stat.S_ISLNK(info.st_mode):
                skipped.append({"path": rel, "reason": "symlink"})
                continue
            if not stat.S_ISREG(info.st_mode):
                skipped.append({"path": rel, "reason": "non_regular"})
                continue
            if info.st_size > MAX_FILE_BYTES:
                skipped.append({"path": rel, "reason": "size_limit"})
                continue
        content = ["%d:%s" % (source_line, line) for source_line, line in rows[rel]]
        content_bytes = "\n".join(content).encode("utf-8")
        if len(content_bytes) > MAX_FILE_BYTES or total + len(content_bytes) > MAX_SCOPE_BYTES:
            skipped.append({"path": rel, "reason": "size_limit"})
            continue
        total += len(content_bytes)
        chunks.append("@@FILE %s\n" % json.dumps(rel, ensure_ascii=True))
        synthetic_line += 1
        for source_line, line in rows[rel]:
            chunks.append(line + "\n")
            synthetic_line += 1
            line_map[synthetic_line] = (rel, source_line)
        records.append({
            "path": rel,
            "size": len(content_bytes),
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
        })
    content_digest = digest({
        "files": records,
        "skipped": sorted(skipped, key=lambda row: (row["path"], row["reason"])),
        "mode": "staged" if staged else "diff",
    })
    return {
        "scope": scope,
        "content_digest": content_digest,
        "revision": _git_revision(root, content_digest),
        "files": records,
        "skipped": sorted(skipped, key=lambda row: (row["path"], row["reason"])),
        "_stream": "".join(chunks).encode("utf-8"),
        "_line_map": line_map,
    }


def _snapshot(root, scope, target, mode):
    if mode == "scan":
        return snapshot_scope(root, target)
    return _parse_diff(root, scope, staged=mode == "staged")


def _is_manifest_path(path):
    return (
        os.path.basename(path) in DEPENDENCY_MANIFESTS
        or path == "gradle/verification-metadata.xml"
        or path.endswith("/gradle/verification-metadata.xml")
    )


def _dependency_snapshot(root, scope, target, mode, before):
    if mode == "scan":
        return before
    inventory = _run_bounded(
        [
            "git", "-C", root, "ls-files", "-co", "--exclude-standard",
            "-z", "--", scope,
        ],
        cwd=root,
        timeout=DEFAULT_TIMEOUT,
        max_output=4 * 1024 * 1024,
    )
    if inventory.returncode != 0 or inventory.error_code:
        raise SecurityError("diff_failed")
    records = []
    skipped = []
    for raw_path in inventory.stdout.split(b"\0"):
        if not raw_path:
            continue
        rel = _safe_relative(raw_path.decode("utf-8", "surrogateescape"))
        if _is_internal(rel) or not _is_manifest_path(rel):
            continue
        full = os.path.join(root, rel)
        try:
            info = os.lstat(full)
        except OSError:
            skipped.append({"path": rel, "reason": "unreadable"})
            continue
        if stat.S_ISLNK(info.st_mode):
            skipped.append({"path": rel, "reason": "symlink"})
            continue
        if not stat.S_ISREG(info.st_mode):
            skipped.append({"path": rel, "reason": "non_regular"})
            continue
        if info.st_size > MAX_FILE_BYTES:
            skipped.append({"path": rel, "reason": "size_limit"})
            continue
        try:
            payload = _read_regular_bytes(full, expected=info)
        except OSError:
            skipped.append({"path": rel, "reason": "unreadable"})
            continue
        records.append({
            "path": rel,
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    records.sort(key=lambda row: row["path"])
    skipped.sort(key=lambda row: (row["path"], row["reason"]))
    return {
        "scope": scope,
        "content_digest": digest({"files": records, "skipped": skipped}),
        "files": records,
        "skipped": skipped,
    }


def _empty_threat_model():
    model = {field: [] for field in THREAT_FIELDS}
    model.update({"status": "incomplete", "proof_gaps": list(THREAT_FIELDS)})
    return model


def _validate_guidance(payload):
    if set(payload) != {"schema_version", "scopes"} or payload.get("schema_version") != 1:
        raise SecurityError("guidance_invalid")
    scopes = payload.get("scopes")
    if not isinstance(scopes, list):
        raise SecurityError("guidance_invalid")
    normalized = []
    expected = {"path", *THREAT_FIELDS}
    for row in scopes:
        if not isinstance(row, dict) or set(row) != expected:
            raise SecurityError("guidance_invalid")
        path = _safe_relative(row["path"])
        clean = {"path": path}
        for field in THREAT_FIELDS:
            values = row[field]
            if (
                not isinstance(values, list)
                or any(not isinstance(item, str) or not item.strip() for item in values)
            ):
                raise SecurityError("guidance_invalid")
            clean[field] = [_safe_text(item, "redacted") for item in values]
        normalized.append(clean)
    return {"schema_version": 1, "scopes": normalized}


def load_guidance(root, scope):
    state_root = os.path.join(root, ".kimiflow")
    if os.path.lexists(state_root) and (os.path.islink(state_root) or not os.path.isdir(state_root)):
        raise SecurityError("unsafe_state_path")
    security_root = os.path.join(state_root, "security")
    if os.path.lexists(security_root) and (
        os.path.islink(security_root) or not os.path.isdir(security_root)
    ):
        raise SecurityError("unsafe_state_path")
    path = os.path.join(root, ".kimiflow", "security", "GUIDANCE.json")
    empty = {"schema_version": 1, "scopes": []}
    if not os.path.lexists(path):
        payload = empty
    else:
        if os.path.islink(path):
            raise SecurityError("unsafe_state_path")
        payload = _validate_guidance(load_json_file(path))
    scope = _safe_relative(scope)
    applicable = []
    for row in payload["scopes"]:
        prefix = row["path"]
        if prefix == "." or scope == prefix or scope.startswith(prefix + "/"):
            applicable.append(row)
    selected = max(applicable, key=lambda item: len(item["path"]), default=None)
    return {
        "digest": digest(payload),
        "directives": selected or {},
        "source": ".kimiflow/security/GUIDANCE.json" if payload != empty else "",
    }


def _nearest_security_policy(root, scope):
    target = root if scope == "." else os.path.join(root, scope)
    directory = target if os.path.isdir(target) else os.path.dirname(target)
    candidates = []
    while _within(directory, root):
        candidate = os.path.join(directory, "SECURITY.md")
        if os.path.isfile(candidate) and not os.path.islink(candidate):
            candidates.append(candidate)
        if os.path.realpath(directory) == os.path.realpath(root):
            break
        directory = os.path.dirname(directory)
    if not candidates:
        return {"policy_path": "", "policy_digest": ""}
    selected = candidates[0]
    try:
        policy_payload = _read_regular_bytes(selected)
    except OSError as exc:
        raise SecurityError("policy_unreadable") from exc
    return {
        "policy_path": os.path.relpath(selected, root).replace(os.sep, "/"),
        "policy_digest": digest(policy_payload),
    }


def security_context(root, scope):
    guidance = load_guidance(root, scope)
    directives = guidance["directives"]
    threat = _empty_threat_model()
    if directives:
        for field in THREAT_FIELDS:
            threat[field] = list(directives[field])
        threat["proof_gaps"] = [field for field in THREAT_FIELDS if not threat[field]]
        threat["status"] = "complete" if not threat["proof_gaps"] else "incomplete"
    return {
        **_nearest_security_policy(root, scope),
        "guidance_digest": guidance["digest"],
        "guidance_source": guidance["source"],
        "directives": directives,
        "threat_model": threat,
    }


def provider_receipt(lane, provider, version, status, freshness, side_effects, scope):
    if lane not in ("secrets", "dependencies", "sarif"):
        raise SecurityError("provider_receipt_invalid")
    allowed = PROVIDER_COMPLETE | PROVIDER_GAP
    if status not in allowed:
        raise SecurityError("provider_receipt_invalid")
    return {
        "schema_version": 1,
        "lane": lane,
        "provider": provider,
        "capability": lane,
        "version": version or "",
        "data_freshness": freshness,
        "scope": scope,
        "side_effects": side_effects,
        "status": status,
        "coverage": "complete" if status in PROVIDER_COMPLETE else "gap",
    }


def _level(level):
    return {
        "error": "high",
        "warning": "medium",
        "note": "low",
        "none": "info",
    }.get(str(level or "").lower(), "medium")


def _occurrence(provider, rule_id, path, line, fingerprint):
    path = _safe_relative(path or "unlocated")
    line = line if isinstance(line, int) and line > 0 else 1
    fp = digest(_safe_text(fingerprint, "%s:%s:%d" % (provider, path, line)))
    return {
        "occurrence_id": "occ_" + digest({
            "provider": provider,
            "rule": rule_id,
            "path": path,
            "line": line,
            "fingerprint": fp,
        }).split(":", 1)[1][:32],
        "path": path,
        "start_line": line,
        "fingerprint": fp,
    }


def _finding(
    provider,
    version,
    rule_id,
    title,
    severity,
    path,
    line,
    fingerprint,
    *,
    cwe=None,
    remediation="manual_review_required",
    impact="potential_security_impact",
    source="untrusted_input",
    sink="security_sensitive_operation",
    reachability="unproven",
    confidence="low",
    proof_gaps=None,
):
    occurrence = _occurrence(provider, rule_id, path, line, fingerprint)
    finding_id = "finding_" + digest({
        "provider": provider,
        "rule": rule_id,
        "path": occurrence["path"],
    }).split(":", 1)[1][:32]
    value = {
        "finding_id": finding_id,
        "rule_id": _safe_text(rule_id, "unclassified"),
        "title": _safe_text(title, rule_id or "security finding"),
        "severity": severity if severity in ("critical", "high", "medium", "low", "info") else "medium",
        "confidence": confidence if confidence in ("high", "medium", "low") else "low",
        "cwe": sorted(set(cwe or [])),
        "source": _safe_text(source, "untrusted_input"),
        "sink": _safe_text(sink, "security_sensitive_operation"),
        "reachability": _safe_text(reachability, "unproven"),
        "impact": _safe_text(impact, "potential_security_impact"),
        "counterevidence": [],
        "proof_gaps": list(proof_gaps or ["reachability_not_proven"]),
        "remediation": _safe_text(remediation, "manual_review_required"),
        "provenance": {
            "provider": provider,
            "provider_version": version or "",
            "normalizer": "kimiflow-security-v1",
        },
        "occurrences": [occurrence],
    }
    validate_finding(value)
    return value


def validate_finding(value):
    if not isinstance(value, dict) or set(value) != FINDING_FIELDS:
        raise SecurityError("finding_invalid")
    if (
        not isinstance(value["cwe"], list)
        or not isinstance(value["counterevidence"], list)
        or not isinstance(value["proof_gaps"], list)
        or not isinstance(value["provenance"], dict)
        or set(value["provenance"]) != {"provider", "provider_version", "normalizer"}
        or not isinstance(value["occurrences"], list)
        or not value["occurrences"]
    ):
        raise SecurityError("finding_invalid")
    required_text = FINDING_FIELDS - {
        "cwe", "counterevidence", "proof_gaps", "provenance", "occurrences",
    }
    if any(not isinstance(value[field], str) or not value[field] for field in required_text):
        raise SecurityError("finding_invalid")
    for occurrence in value["occurrences"]:
        if (
            not isinstance(occurrence, dict)
            or set(occurrence) != {"occurrence_id", "path", "start_line", "fingerprint"}
            or not isinstance(occurrence["start_line"], int)
            or occurrence["start_line"] < 1
        ):
            raise SecurityError("finding_invalid")
    return value


def _sarif_uri(value):
    if not isinstance(value, str) or not value:
        return "unlocated"
    parsed = urlparse(value)
    if parsed.scheme not in ("", "file"):
        return "external-artifact"
    path = unquote(parsed.path if parsed.scheme == "file" else value)
    if parsed.scheme == "file" or os.path.isabs(path):
        return "external-artifact"
    try:
        return _safe_relative(path)
    except SecurityError:
        return "unlocated"


def normalize_sarif(payload):
    if not isinstance(payload, dict) or payload.get("version") != "2.1.0":
        raise SecurityError("sarif_invalid")
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise SecurityError("sarif_invalid")
    findings = []
    for run in runs:
        if not isinstance(run, dict):
            raise SecurityError("sarif_invalid")
        driver = ((run.get("tool") or {}).get("driver") or {})
        provider = "sarif-provider-" + hashlib.sha256(
            str(driver.get("name") or "sarif").encode("utf-8", "replace")
        ).hexdigest()[:16]
        version = "sarif-version-" + hashlib.sha256(
            str(driver.get("version") or "unknown").encode("utf-8", "replace")
        ).hexdigest()[:16]
        rules = {
            rule.get("id"): rule
            for rule in driver.get("rules", [])
            if isinstance(rule, dict) and isinstance(rule.get("id"), str)
        }
        results = run.get("results", [])
        if not isinstance(results, list):
            raise SecurityError("sarif_invalid")
        for result in results:
            if not isinstance(result, dict):
                raise SecurityError("sarif_invalid")
            raw_rule_id = _safe_text(result.get("ruleId"), "unclassified")
            rule = rules.get(raw_rule_id, {})
            rule_id = "sarif-rule-" + hashlib.sha256(
                raw_rule_id.encode("utf-8", "replace")
            ).hexdigest()[:16]
            locations = result.get("locations") or []
            physical = (
                locations[0].get("physicalLocation", {})
                if locations and isinstance(locations[0], dict)
                else {}
            )
            artifact = physical.get("artifactLocation") or {}
            region = physical.get("region") or {}
            partial = result.get("partialFingerprints") or {}
            fingerprint = digest(partial) if isinstance(partial, dict) and partial else ""
            tags = ((rule.get("properties") or {}).get("tags") or []) if isinstance(rule, dict) else []
            cwe = [
                str(tag).upper()
                for tag in tags
                if re.fullmatch(r"(?i)CWE-[0-9]+", str(tag))
            ]
            findings.append(_finding(
                provider,
                version,
                rule_id,
                rule_id,
                _level(result.get("level")),
                _sarif_uri(artifact.get("uri")),
                region.get("startLine", 1),
                fingerprint,
                cwe=cwe,
                remediation="review_provider_rule_and_apply_minimal_verified_fix",
                impact="provider_reported_security_impact",
                proof_gaps=["reachability_not_proven", "source_sink_not_proven"],
            ))
    return _merge_findings(findings)


def _sarif_provider_identity(payload):
    if not isinstance(payload, dict) or payload.get("version") != "2.1.0":
        raise SecurityError("sarif_invalid")
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise SecurityError("sarif_invalid")
    identities = []
    for run in runs:
        if not isinstance(run, dict):
            raise SecurityError("sarif_invalid")
        driver = ((run.get("tool") or {}).get("driver") or {})
        driver_rules = driver.get("rules", [])
        results = run.get("results", [])
        invocations = run.get("invocations", [])
        if (
            not isinstance(driver, dict)
            or not isinstance(driver_rules, list)
            or not isinstance(results, list)
            or not isinstance(invocations, list)
        ):
            raise SecurityError("sarif_invalid")
        rule_ids = {
            rule["id"]
            for rule in driver_rules
            if isinstance(rule, dict) and isinstance(rule.get("id"), str)
        }
        for result in results:
            if not isinstance(result, dict):
                raise SecurityError("sarif_invalid")
            if isinstance(result.get("ruleId"), str):
                rule_ids.add(result["ruleId"])
        execution_attested = bool(invocations) and all(
            isinstance(invocation, dict)
            and invocation.get("executionSuccessful") is True
            for invocation in invocations
        )
        identities.append({
            "provider": "sarif-provider-" + hashlib.sha256(
                str(driver.get("name") or "sarif").encode("utf-8", "replace")
            ).hexdigest()[:16],
            "version": "sarif-version-" + hashlib.sha256(
                str(driver.get("version") or "unknown").encode("utf-8", "replace")
            ).hexdigest()[:16],
            "rule_set_digest": digest(sorted(rule_ids)),
            "execution_attested": execution_attested,
        })
    return identities


def _merge_findings(findings):
    merged = {}
    for finding in findings:
        validate_finding(finding)
        key = finding["finding_id"]
        if key not in merged:
            merged[key] = finding
            continue
        known = {row["occurrence_id"] for row in merged[key]["occurrences"]}
        for occurrence in finding["occurrences"]:
            if occurrence["occurrence_id"] not in known:
                merged[key]["occurrences"].append(occurrence)
        merged[key]["occurrences"].sort(key=lambda row: (row["path"], row["start_line"], row["occurrence_id"]))
    return [merged[key] for key in sorted(merged)]


def _default_which(name):
    return shutil.which(name)


def _kill_process(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def _run_bounded(argv, *, cwd, input_data=b"", timeout=DEFAULT_TIMEOUT, max_output=MAX_PROVIDER_OUTPUT):
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE if input_data else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return CommandResult(127, b"", b"", "failed")
    def write_input():
        if not input_data or proc.stdin is None:
            return
        try:
            view = memoryview(input_data)
            while view:
                written = proc.stdin.write(view[:65536])
                if not written:
                    break
                view = view[written:]
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    writer = threading.Thread(target=write_input, daemon=True)
    writer.start()
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    chunks = {"stdout": [], "stderr": []}
    sizes = {"stdout": 0, "stderr": 0}
    deadline = time.monotonic() + timeout
    error_code = ""
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error_code = "timeout"
                _kill_process(proc)
                break
            events = selector.select(min(remaining, 0.2))
            if not events and proc.poll() is not None:
                events = [(key, selectors.EVENT_READ) for key in list(selector.get_map().values())]
            for key, _ in events:
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                label = key.data
                sizes[label] += len(data)
                if sizes[label] > max_output:
                    error_code = "output_limit"
                    _kill_process(proc)
                    break
                chunks[label].append(data)
            if error_code:
                break
    finally:
        selector.close()
        _kill_process(proc) if error_code else None
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_process(proc)
            proc.wait()
        writer.join(timeout=1)
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
    return CommandResult(
        proc.returncode or 0,
        b"".join(chunks["stdout"]),
        b"".join(chunks["stderr"]),
        error_code,
    )


def _execute(executor, argv, **kwargs):
    return (executor or _run_bounded)(argv, **kwargs)


def _execution_gap(error_code):
    if error_code in {"refused", "quota_limited", "timeout", "output_limit"}:
        return error_code
    return "failed"


def _version(binary, root, executor):
    result = _execute(
        executor,
        [binary, "--version"],
        cwd=root,
        timeout=5,
        max_output=65536,
    )
    if result.error_code:
        return "", _execution_gap(result.error_code)
    if result.returncode != 0:
        return "", "failed"
    match = re.search(r"([0-9]+(?:\.[0-9]+){1,3})", result.stdout.decode("utf-8", "replace"))
    return (match.group(1), "") if match else ("", "failed")


def _opaque_provider_label(prefix, value):
    raw = _safe_text(value, "unclassified")
    return prefix + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _gitleaks_findings(payload, version, line_map):
    try:
        rows = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeError, json.JSONDecodeError, SecurityError) as exc:
        raise SecurityError("provider_output_invalid") from exc
    if not isinstance(rows, list):
        raise SecurityError("provider_output_invalid")
    findings = []
    for row in rows:
        if not isinstance(row, dict):
            raise SecurityError("provider_output_invalid")
        synthetic_line = row.get("StartLine", 1)
        path, line = line_map.get(synthetic_line, ("unlocated", 1))
        rule = _opaque_provider_label("gitleaks-rule-", row.get("RuleID"))
        findings.append(_finding(
            "gitleaks",
            version,
            rule,
            "Gitleaks secret finding",
            "high",
            path,
            line,
            row.get("Fingerprint") or "%s:%s:%d" % (rule, path, line),
            cwe=["CWE-798"],
            source=path,
            sink="credential_exposure",
            reachability="file_present",
            impact="potential_secret_exposure",
            remediation="remove_secret_and_rotate_if_valid",
            confidence="medium",
            proof_gaps=["credential_validity_not_checked"],
        ))
    return _merge_findings(findings)


def _trufflehog_findings(payload, version, line_map):
    findings = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line, object_pairs_hook=_no_duplicates)
        except (UnicodeError, json.JSONDecodeError, SecurityError) as exc:
            raise SecurityError("provider_output_invalid") from exc
        if not isinstance(row, dict):
            raise SecurityError("provider_output_invalid")
        source = row.get("SourceMetadata")
        if not isinstance(source, dict):
            raise SecurityError("provider_output_invalid")
        data = source.get("Data")
        if not isinstance(data, dict):
            raise SecurityError("provider_output_invalid")
        filesystem = data.get("Filesystem")
        if not isinstance(filesystem, dict):
            raise SecurityError("provider_output_invalid")
        synthetic_line = filesystem.get("line") or 1
        path, line_number = line_map.get(synthetic_line, ("unlocated", 1))
        detector = _opaque_provider_label(
            "trufflehog-detector-",
            row.get("DetectorName") or row.get("DetectorType"),
        )
        findings.append(_finding(
            "trufflehog",
            version,
            detector,
            "TruffleHog secret finding",
            "high",
            path,
            line_number,
            row.get("SourceID") or "%s:%s:%s" % (detector, path, line_number),
            cwe=["CWE-798"],
            source=path,
            sink="credential_exposure",
            reachability="file_present",
            impact="potential_secret_exposure",
            remediation="remove_secret_and_rotate_if_valid",
            confidence="medium",
            proof_gaps=["credential_validity_not_checked"],
        ))
    return _merge_findings(findings)


def scan_secrets(
    root,
    snapshot,
    mode,
    *,
    which=None,
    command_executor=None,
):
    which = which or _default_which
    scope = snapshot["scope"]
    gitleaks = which("gitleaks")
    if gitleaks:
        version, version_gap = _version(gitleaks, root, command_executor)
        if version_gap:
            return provider_receipt(
                "secrets", "gitleaks", "", version_gap, "unavailable", "none", scope,
            ), []
        major_minor = tuple(int(part) for part in version.split(".")[:2])
        if major_minor < (8, 19):
            return provider_receipt("secrets", "gitleaks", version, "unsupported", "stale", "none", scope), []
        argv = [
            gitleaks,
            "stdin",
            "--no-banner",
            "--no-color",
            "--redact=100",
            "--report-format",
            "json",
            "--report-path",
            "-",
            "--exit-code",
            "1",
            "--timeout",
            str(DEFAULT_TIMEOUT),
        ]
        result = _execute(
            command_executor,
            argv,
            cwd=root,
            input_data=snapshot["_stream"],
            timeout=DEFAULT_TIMEOUT + 5,
            max_output=MAX_PROVIDER_OUTPUT,
        )
        if result.error_code:
            status = _execution_gap(result.error_code)
            return provider_receipt("secrets", "gitleaks", version, status, "fresh", "none", scope), []
        if result.returncode not in (0, 1):
            return provider_receipt("secrets", "gitleaks", version, "failed", "fresh", "none", scope), []
        try:
            findings = _gitleaks_findings(result.stdout or b"[]", version, snapshot["_line_map"])
        except SecurityError:
            return provider_receipt("secrets", "gitleaks", version, "failed", "fresh", "none", scope), []
        if result.returncode == 1 and not findings:
            return provider_receipt(
                "secrets", "gitleaks", version, "failed", "fresh", "none", scope,
            ), []
        status = "findings" if findings else "complete"
        return provider_receipt("secrets", "gitleaks", version, status, "fresh", "none", scope), findings
    trufflehog = which("trufflehog")
    if trufflehog and mode == "scan":
        version, version_gap = _version(trufflehog, root, command_executor)
        if version_gap:
            return provider_receipt(
                "secrets", "trufflehog", "", version_gap, "unavailable", "none", scope,
            ), []
        if int(version.split(".", 1)[0]) < 3:
            return provider_receipt("secrets", "trufflehog", version, "unsupported", "stale", "none", scope), []
        argv = [
            trufflehog,
            "stdin",
            "--json",
            "--no-verification",
            "--no-update",
            "--fail",
            "--fail-on-scan-errors",
        ]
        result = _execute(
            command_executor,
            argv,
            cwd=root,
            input_data=snapshot["_stream"],
            timeout=DEFAULT_TIMEOUT,
            max_output=MAX_PROVIDER_OUTPUT,
        )
        if result.error_code:
            status = _execution_gap(result.error_code)
            return provider_receipt("secrets", "trufflehog", version, status, "fresh", "none", scope), []
        if result.returncode not in (0, 183):
            return provider_receipt("secrets", "trufflehog", version, "failed", "fresh", "none", scope), []
        try:
            findings = _trufflehog_findings(result.stdout, version, snapshot["_line_map"])
        except SecurityError:
            return provider_receipt("secrets", "trufflehog", version, "failed", "fresh", "none", scope), []
        if result.returncode == 183 and not findings:
            return provider_receipt(
                "secrets", "trufflehog", version, "failed", "fresh", "none", scope,
            ), []
        return provider_receipt(
            "secrets", "trufflehog", version,
            "findings" if findings else "complete", "fresh", "none", scope,
        ), findings
    status = "unsupported" if trufflehog and mode != "scan" else "missing"
    provider = "trufflehog" if trufflehog else "gitleaks|trufflehog"
    return provider_receipt("secrets", provider, "", status, "unavailable", "none", scope), []

def _manifest_paths(snapshot):
    result = []
    for row in snapshot["files"]:
        if _is_manifest_path(row["path"]):
            result.append(row["path"])
    return sorted(result)


def scan_dependencies(root, snapshot, mode="scan", *, which=None, command_executor=None, now=None):
    which = which or _default_which
    scope = snapshot["scope"]
    manifests = _manifest_paths(snapshot)
    if not manifests:
        return provider_receipt(
            "dependencies", "manifest-inventory", "1", "not_applicable",
            "inventory:" + snapshot["content_digest"], "none", scope,
        ), []
    if mode != "scan":
        return provider_receipt(
            "dependencies", "osv-scanner", "", "unsupported", "unavailable",
            "inventory:" + snapshot["content_digest"], scope,
        ), []
    binary = which("osv-scanner")
    if not binary:
        return provider_receipt(
            "dependencies", "osv-scanner", "", "missing", "unavailable", "none", scope,
        ), []
    version, version_gap = _version(binary, root, command_executor)
    if version_gap:
        return provider_receipt(
            "dependencies", "osv-scanner", "", version_gap, "unavailable", "none", scope,
        ), []
    if int(version.split(".", 1)[0]) < 2:
        return provider_receipt(
            "dependencies", "osv-scanner", version, "unsupported", "stale", "none", scope,
        ), []
    fresh_until = os.environ.get("KIMIFLOW_OSV_OFFLINE_FRESH_UNTIL", "")
    if os.environ.get("KIMIFLOW_OSV_OFFLINE") != "1" or not fresh_until:
        return provider_receipt(
            "dependencies", "osv-scanner", version, "unauthorized", "unavailable",
            "network_disabled", scope,
        ), []
    try:
        fresh_deadline = _parse_time(fresh_until)
    except SecurityError:
        return provider_receipt(
            "dependencies", "osv-scanner", version, "stale", "invalid",
            "network_disabled", scope,
        ), []
    current = now or datetime.now(timezone.utc)
    if fresh_deadline <= current:
        return provider_receipt(
            "dependencies", "osv-scanner", version, "stale", fresh_until,
            "network_disabled", scope,
        ), []
    argv = [
        binary,
        "scan",
        "--offline",
        "--offline-vulnerabilities",
        "--no-resolve",
        "--format",
        "json",
    ]
    for path in manifests:
        argv.extend(["-L", os.path.join(root, path)])
    result = _execute(
        command_executor,
        argv,
        cwd=root,
        timeout=DEFAULT_TIMEOUT,
        max_output=MAX_PROVIDER_OUTPUT,
    )
    if result.error_code:
        status = _execution_gap(result.error_code)
        return provider_receipt(
            "dependencies", "osv-scanner", version, status, "fresh", "none", scope,
        ), []
    if result.returncode not in (0, 1):
        return provider_receipt(
            "dependencies", "osv-scanner", version, "failed", "fresh", "none", scope,
        ), []
    try:
        if not result.stdout:
            raise SecurityError("provider_output_invalid")
        payload = json.loads(result.stdout, object_pairs_hook=_no_duplicates)
    except (json.JSONDecodeError, UnicodeError, SecurityError):
        return provider_receipt(
            "dependencies", "osv-scanner", version, "failed", "fresh", "none", scope,
        ), []
    findings = []
    if not isinstance(payload, dict) or "results" not in payload:
        return provider_receipt(
            "dependencies", "osv-scanner", version, "failed", fresh_until,
            "read_local_cache", scope,
        ), []
    results = payload["results"]
    if not isinstance(results, list):
        return provider_receipt(
            "dependencies", "osv-scanner", version, "failed", fresh_until,
            "read_local_cache", scope,
        ), []
    for result_index, row in enumerate(results):
        if not isinstance(row, dict) or not isinstance(row.get("packages"), list):
            return provider_receipt(
                "dependencies", "osv-scanner", version, "failed", fresh_until,
                "read_local_cache", scope,
            ), []
        source = row.get("source")
        if source is not None and not isinstance(source, dict):
            return provider_receipt(
                "dependencies", "osv-scanner", version, "failed", fresh_until,
                "read_local_cache", scope,
            ), []
        source_path = ((source or {}).get("path") or "")
        source_rel = ""
        if isinstance(source_path, str):
            candidate = (
                os.path.realpath(source_path)
                if os.path.isabs(source_path)
                else os.path.realpath(os.path.join(root, source_path))
            )
            if _within(candidate, root):
                relative = os.path.relpath(candidate, root).replace(os.sep, "/")
                if relative in manifests:
                    source_rel = relative
        path = source_rel or manifests[min(result_index, len(manifests) - 1)]
        for package_index, package_row in enumerate(row["packages"]):
            if not isinstance(package_row, dict):
                return provider_receipt(
                    "dependencies", "osv-scanner", version, "failed", fresh_until,
                    "read_local_cache", scope,
                ), []
            package = package_row.get("package")
            vulnerabilities = package_row.get("vulnerabilities")
            if not isinstance(package, dict) or not isinstance(vulnerabilities, list):
                return provider_receipt(
                    "dependencies", "osv-scanner", version, "failed", fresh_until,
                    "read_local_cache", scope,
                ), []
            for vulnerability_index, vulnerability in enumerate(vulnerabilities):
                if not isinstance(vulnerability, dict):
                    return provider_receipt(
                        "dependencies", "osv-scanner", version, "failed", fresh_until,
                        "read_local_cache", scope,
                    ), []
                vuln_id = _safe_text(
                    vulnerability.get("id"),
                    "OSV-%d-%d-%d" % (result_index, package_index, vulnerability_index),
                )
                findings.append(_finding(
                    "osv-scanner",
                    version,
                    vuln_id,
                    vuln_id,
                    "high",
                    path,
                    1,
                    "%s:%s" % (package.get("name", ""), vuln_id),
                    cwe=[],
                    source=path,
                    sink="vulnerable_dependency",
                    reachability="dependency_present",
                    impact="known_dependency_vulnerability",
                    remediation="update_dependency_after_compatibility_review",
                    confidence="high",
                    proof_gaps=["application_reachability_not_proven"],
                ))
    if result.returncode == 1 and not findings:
        return provider_receipt(
            "dependencies", "osv-scanner", version, "failed", fresh_until,
            "read_local_cache", scope,
        ), []
    return provider_receipt(
        "dependencies", "osv-scanner", version,
        "findings" if findings else "complete", fresh_until, "read_local_cache", scope,
    ), _merge_findings(findings)


def _parse_time(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SecurityError("authorization_invalid")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SecurityError("authorization_invalid") from exc


def validate_authorization(payload, provider, scope_digest, action, *, now=None):
    expected = {"schema_version", "action", "provider", "scope_digest", "expires_at", "nonce"}
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("nonce"), str)
        or len(payload["nonce"]) < 8
        or not SHA_RE.fullmatch(str(payload.get("scope_digest") or ""))
    ):
        raise SecurityError("authorization_invalid")
    if payload["action"] != action:
        raise SecurityError("authorization_action_mismatch")
    if payload["provider"] != provider:
        raise SecurityError("authorization_provider_mismatch")
    if payload["scope_digest"] != scope_digest:
        raise SecurityError("authorization_scope_mismatch")
    current = now or datetime.now(timezone.utc)
    if _parse_time(payload["expires_at"]) <= current:
        raise SecurityError("authorization_expired")
    return digest(payload)


def build_coverage(scan_id, threat_status, receipts, findings):
    if not SCAN_RE.fullmatch(scan_id) or threat_status not in ("complete", "incomplete"):
        raise SecurityError("coverage_invalid")
    by_lane = {row["lane"]: row for row in receipts}
    gaps = []
    for lane in ("secrets", "dependencies"):
        row = by_lane.get(lane)
        if row is None or row.get("status") not in PROVIDER_COMPLETE:
            gaps.append(lane)
    if threat_status != "complete":
        gaps.append("threat_model")
    verdict = "incomplete" if gaps else ("findings" if findings else "clean")
    return {
        "schema_version": 1,
        "scan_id": scan_id,
        "status": "complete" if not gaps else "incomplete",
        "verdict": verdict,
        "required_lanes": ["secrets", "dependencies"],
        "receipts": receipts,
        "gaps": sorted(gaps),
    }


def _report_model(scan_id, coverage, findings, reason_codes):
    return {
        "schema_version": 1,
        "scan_id": scan_id,
        "verdict": coverage["verdict"],
        "summary": {
            "finding_count": len(findings),
            "coverage_gap_count": len(coverage["gaps"]),
        },
        "gaps": list(coverage["gaps"]),
        "reason_codes": list(reason_codes),
    }


def _validate_manifest(value):
    expected = {
        "schema_version", "scan_id", "mode", "scope", "scope_digest",
        "content_digest", "revision", "guidance_digest", "policy_digest",
        "authorization_digest", "threat_model", "provider_plan",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema_version") != 1
        or not SCAN_RE.fullmatch(str(value.get("scan_id") or ""))
        or value.get("mode") not in ("scan", "diff")
        or not isinstance(value.get("scope"), str)
        or not isinstance(value.get("revision"), str)
        or not isinstance(value.get("policy_digest"), str)
        or not isinstance(value.get("authorization_digest"), str)
        or not SHA_RE.fullmatch(str(value.get("scope_digest") or ""))
        or not SHA_RE.fullmatch(str(value.get("content_digest") or ""))
        or not SHA_RE.fullmatch(str(value.get("guidance_digest") or ""))
        or not isinstance(value.get("threat_model"), dict)
        or not isinstance(value.get("provider_plan"), list)
    ):
        raise SecurityError("manifest_invalid")
    _safe_relative(value["scope"])
    if value["scope"] != "." and _is_internal(value["scope"]):
        raise SecurityError("manifest_invalid")
    if value["policy_digest"] and not SHA_RE.fullmatch(value["policy_digest"]):
        raise SecurityError("manifest_invalid")
    if value["authorization_digest"] and not SHA_RE.fullmatch(value["authorization_digest"]):
        raise SecurityError("manifest_invalid")
    threat = value["threat_model"]
    if set(threat) != {*THREAT_FIELDS, "status", "proof_gaps"}:
        raise SecurityError("manifest_invalid")
    if (
        threat.get("status") not in ("complete", "incomplete")
        or any(not isinstance(threat.get(field), list) for field in THREAT_FIELDS)
        or not isinstance(threat.get("proof_gaps"), list)
        or value["provider_plan"] != ["secrets", "dependencies"]
    ):
        raise SecurityError("manifest_invalid")


def _validate_coverage(value):
    expected = {
        "schema_version", "scan_id", "status", "verdict",
        "required_lanes", "receipts", "gaps",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema_version") != 1
        or not SCAN_RE.fullmatch(str(value.get("scan_id") or ""))
        or value.get("status") not in ("complete", "incomplete")
        or value.get("verdict") not in ("clean", "findings", "incomplete")
        or value.get("required_lanes") != ["secrets", "dependencies"]
        or not isinstance(value.get("receipts"), list)
        or not isinstance(value.get("gaps"), list)
    ):
        raise SecurityError("coverage_invalid")
    seen = set()
    receipt_fields = {
        "schema_version", "lane", "provider", "capability", "version",
        "data_freshness", "scope", "side_effects", "status", "coverage",
    }
    for receipt in value["receipts"]:
        if (
            not isinstance(receipt, dict)
            or set(receipt) != receipt_fields
            or receipt.get("schema_version") != 1
            or receipt.get("lane") not in ("secrets", "dependencies", "sarif")
            or receipt.get("capability") != receipt.get("lane")
            or receipt.get("status") not in PROVIDER_COMPLETE | PROVIDER_GAP
            or receipt.get("coverage") not in ("complete", "gap")
            or receipt["lane"] in seen
        ):
            raise SecurityError("coverage_invalid")
        if (receipt["status"] in PROVIDER_COMPLETE) != (receipt["coverage"] == "complete"):
            raise SecurityError("coverage_invalid")
        for field in ("provider", "version", "data_freshness", "scope", "side_effects"):
            if not isinstance(receipt.get(field), str):
                raise SecurityError("coverage_invalid")
        seen.add(receipt["lane"])
    if any(not isinstance(gap, str) or not gap for gap in value["gaps"]):
        raise SecurityError("coverage_invalid")
    required_complete = all(
        any(
            receipt["lane"] == lane and receipt["status"] in PROVIDER_COMPLETE
            for receipt in value["receipts"]
        )
        for lane in value["required_lanes"]
    )
    if (
        (value["status"] == "complete") != (not value["gaps"])
        or (value["verdict"] == "clean" and (not required_complete or value["status"] != "complete"))
        or (bool(value["gaps"]) != (value["verdict"] == "incomplete"))
        or any(
            receipt["lane"] in value["required_lanes"]
            and receipt["status"] not in PROVIDER_COMPLETE
            and receipt["lane"] not in value["gaps"]
            for receipt in value["receipts"]
        )
    ):
        raise SecurityError("coverage_invalid")


def _validate_findings_artifact(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "scan_id", "findings"}
        or value.get("schema_version") != 1
        or not SCAN_RE.fullmatch(str(value.get("scan_id") or ""))
        or not isinstance(value.get("findings"), list)
    ):
        raise SecurityError("findings_invalid")
    for finding in value["findings"]:
        validate_finding(finding)


def _validate_report(value):
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema_version", "scan_id", "verdict", "summary", "gaps", "reason_codes",
        }
        or value.get("schema_version") != 1
        or not SCAN_RE.fullmatch(str(value.get("scan_id") or ""))
        or value.get("verdict") not in ("clean", "findings", "incomplete")
        or not isinstance(value.get("summary"), dict)
        or set(value.get("summary") or {}) != {"finding_count", "coverage_gap_count"}
        or any(
            isinstance(value["summary"].get(field), bool)
            or not isinstance(value["summary"].get(field), int)
            or value["summary"][field] < 0
            for field in ("finding_count", "coverage_gap_count")
        )
        or not isinstance(value.get("gaps"), list)
        or not isinstance(value.get("reason_codes"), list)
    ):
        raise SecurityError("report_invalid")


def _render_report(report, findings):
    lines = [
        "<!-- kimiflow:security-report schema_version=1 -->",
        "# Kimiflow Security Report",
        "",
        "Scan: `%s`" % report["scan_id"],
        "Verdict: **%s**" % report["verdict"],
        "Findings: %d" % len(findings),
        "Coverage gaps: %d" % len(report["gaps"]),
        "",
    ]
    if report["gaps"]:
        lines.append("## Coverage gaps")
        lines.extend("- `%s`" % gap for gap in report["gaps"])
        lines.append("")
    if findings:
        lines.append("## Findings")
        for finding in findings:
            occurrence = finding["occurrences"][0]
            lines.append(
                "- `%s` · %s · `%s:%d` · proof gaps: %s"
                % (
                    finding["finding_id"],
                    finding["severity"],
                    occurrence["path"],
                    occurrence["start_line"],
                    ", ".join(finding["proof_gaps"]) or "none",
                )
            )
        lines.append("")
    lines.append("Raw secrets, exploit payloads, identities and absolute local paths are intentionally omitted.")
    lines.append("")
    return "\n".join(lines)


@contextlib.contextmanager
def _directory_chain(root, components, create):
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptors = []
    current = os.open(root, flags)
    descriptors.append(current)
    try:
        for component in components:
            if component in ("", ".", "..") or "/" in component:
                raise SecurityError("unsafe_state_path")
            try:
                child = os.open(component, flags, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise SecurityError("state_missing")
                try:
                    os.mkdir(component, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
                child = os.open(component, flags, dir_fd=current)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                raise SecurityError("unsafe_state_path")
            descriptors.append(child)
            current = child
        yield current
    except OSError as exc:
        raise SecurityError("unsafe_state_path") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _ensure_state_ignored(root):
    if not _git_root(root):
        return
    probe = subprocess.run(
        ["git", "-C", root, "check-ignore", "-q", ".kimiflow/security/probe"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode != 0:
        raise SecurityError("state_not_ignored")


def _artifact_paths(scan_id):
    directory = ".kimiflow/security/scans/%s" % scan_id
    return {
        "directory": directory,
        "manifest": directory + "/SECURITY-SCAN-MANIFEST.json",
        "coverage": directory + "/SECURITY-COVERAGE.json",
        "findings": directory + "/SECURITY-FINDINGS.json",
        "report": directory + "/SECURITY-REPORT.md",
    }


def _existing_scan_matches(scans, scan_id, payloads):
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory = os.open(scan_id, flags, dir_fd=scans)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SecurityError("unsafe_state_path") from exc
    try:
        directory_info = os.fstat(directory)
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or stat.S_IMODE(directory_info.st_mode) != 0o700
            or set(os.listdir(directory)) != set(payloads)
        ):
            raise SecurityError("artifact_conflict")
        for name, expected in payloads.items():
            file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(name, file_flags, dir_fd=directory)
            except OSError as exc:
                raise SecurityError("artifact_conflict") from exc
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size != len(expected)
                ):
                    raise SecurityError("artifact_conflict")
                actual = bytearray()
                while len(actual) < len(expected):
                    chunk = os.read(descriptor, min(65536, len(expected) - len(actual)))
                    if not chunk:
                        break
                    actual.extend(chunk)
                if bytes(actual) != expected:
                    raise SecurityError("artifact_conflict")
            finally:
                os.close(descriptor)
        return True
    finally:
        os.close(directory)


def _remove_staging_directory(scans, temporary, names):
    try:
        directory = os.open(
            temporary,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=scans,
        )
    except OSError:
        return
    try:
        for name in names:
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory)
    try:
        os.rmdir(temporary, dir_fd=scans)
    except OSError:
        pass


def _write_scan_artifacts(root, scan_id, manifest, coverage, findings_artifact, report_text):
    _ensure_state_ignored(root)
    payloads = {
        "SECURITY-SCAN-MANIFEST.json": (_canonical(manifest) + "\n").encode("utf-8"),
        "SECURITY-COVERAGE.json": (_canonical(coverage) + "\n").encode("utf-8"),
        "SECURITY-FINDINGS.json": (_canonical(findings_artifact) + "\n").encode("utf-8"),
        "SECURITY-REPORT.md": report_text.encode("utf-8"),
    }
    with _directory_chain(root, [".kimiflow", "security", "scans"], True) as scans:
        if _existing_scan_matches(scans, scan_id, payloads):
            return _artifact_paths(scan_id)
        temporary = ".scan-%s-%d-%d" % (
            scan_id.split("_", 1)[1],
            os.getpid(),
            time.time_ns(),
        )
        try:
            os.mkdir(temporary, 0o700, dir_fd=scans)
            directory = os.open(
                temporary,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=scans,
            )
        except OSError as exc:
            raise SecurityError("artifact_write_failed") from exc
        try:
            for name, payload in payloads.items():
                workspace_preflight.atomic_directory_write(directory, name, payload)
            os.fsync(directory)
        except (OSError, workspace_preflight.WorkspaceError) as exc:
            _remove_staging_directory(scans, temporary, payloads)
            raise SecurityError("artifact_write_failed") from exc
        finally:
            os.close(directory)
        try:
            os.rename(temporary, scan_id, src_dir_fd=scans, dst_dir_fd=scans)
            os.fsync(scans)
        except OSError as exc:
            if _existing_scan_matches(scans, scan_id, payloads):
                _remove_staging_directory(scans, temporary, payloads)
            else:
                _remove_staging_directory(scans, temporary, payloads)
                raise SecurityError("artifact_write_failed") from exc
    return _artifact_paths(scan_id)


def run_scan(
    mode,
    path,
    *,
    sarif_paths=None,
    authorization_path=None,
    now=None,
    which=None,
    command_executor=None,
):
    if mode not in ("scan", "diff"):
        raise SecurityError("mode_invalid")
    root, scope, target = resolve_scan_root(path, mode)
    before = _snapshot(root, scope, target, mode)
    context = security_context(root, scope)
    scope_digest = digest({
        "mode": mode,
        "scope": scope,
        "guidance_digest": context["guidance_digest"],
        "policy_digest": context["policy_digest"],
    })
    authorization_digest = ""
    if authorization_path:
        authorization = load_json_file(authorization_path)
        provider = authorization.get("provider")
        if not isinstance(provider, str) or not provider:
            raise SecurityError("authorization_invalid")
        authorization_digest = validate_authorization(
            authorization,
            provider,
            scope_digest,
            "external_validation",
            now=now,
        )
    secret_receipt, secret_findings = scan_secrets(
        root,
        before,
        mode,
        which=which,
        command_executor=command_executor,
    )
    dependency_snapshot = _dependency_snapshot(root, scope, target, mode, before)
    dependency_receipt, dependency_findings = scan_dependencies(
        root,
        dependency_snapshot,
        mode,
        which=which,
        command_executor=command_executor,
        now=now,
    )
    findings = list(secret_findings) + list(dependency_findings)
    receipts = [secret_receipt, dependency_receipt]
    sarif_inputs = []
    sarif_providers = []
    sarif_imported = []
    for sarif_path in sorted(set(sarif_paths or ())):
        sarif_payload = load_json_file(sarif_path)
        imported = normalize_sarif(sarif_payload)
        sarif_providers.extend(_sarif_provider_identity(sarif_payload))
        findings.extend(imported)
        sarif_imported.extend(imported)
        sarif_inputs.append(digest(sarif_payload))
    if sarif_inputs:
        sarif_identity = sorted(
            [
                {
                    "provider": row["provider"],
                    "version": row["version"],
                    "rule_set_digest": row["rule_set_digest"],
                }
                for row in sarif_providers
            ],
            key=lambda row: (
                row["provider"],
                row["version"],
                row["rule_set_digest"],
            ),
        )
        rule_set_digest = digest(sarif_identity)
        execution_attested = bool(sarif_providers) and all(
            row["execution_attested"] for row in sarif_providers
        )
        receipts.append(provider_receipt(
            "sarif",
            "sarif-2.1.0-" + rule_set_digest.split(":", 1)[1][:16],
            "2.1.0",
            "findings" if sarif_imported else "complete",
            "input_bound:" + digest(sorted(sarif_inputs)),
            "rule_set:%s;execution:%s"
            % (rule_set_digest, "attested" if execution_attested else "unattested"),
            scope,
        ))
    findings = _merge_findings(findings)
    after = _snapshot(root, scope, target, mode)
    after_context = security_context(root, scope)
    after_scope_digest = digest({
        "mode": mode,
        "scope": scope,
        "guidance_digest": after_context["guidance_digest"],
        "policy_digest": after_context["policy_digest"],
    })
    reason_codes = []
    if before["skipped"]:
        reason_codes.append("scope_skipped")
    if (
        after["content_digest"] != before["content_digest"]
        or after_scope_digest != scope_digest
    ):
        reason_codes.append("scope_changed")
    scan_id = "scan_" + digest({
        "mode": mode,
        "scope_digest": scope_digest,
        "content_digest": before["content_digest"],
        "revision": before["revision"],
        "guidance_digest": context["guidance_digest"],
        "authorization_digest": authorization_digest,
        "provider_receipts": receipts,
        "finding_ids": [row["finding_id"] for row in findings],
        "after_scope_digest": after_scope_digest,
        "after_content_digest": after["content_digest"],
        "reason_codes": reason_codes,
    }).split(":", 1)[1][:32]
    threat_status = context["threat_model"]["status"]
    coverage = build_coverage(scan_id, threat_status, receipts, findings)
    if reason_codes:
        coverage["status"] = "incomplete"
        coverage["verdict"] = "incomplete"
        coverage["gaps"] = sorted(set(coverage["gaps"] + reason_codes))
    manifest = {
        "schema_version": 1,
        "scan_id": scan_id,
        "mode": mode,
        "scope": scope,
        "scope_digest": scope_digest,
        "content_digest": before["content_digest"],
        "revision": before["revision"],
        "guidance_digest": context["guidance_digest"],
        "policy_digest": context["policy_digest"],
        "authorization_digest": authorization_digest,
        "threat_model": context["threat_model"],
        "provider_plan": ["secrets", "dependencies"],
    }
    findings_artifact = {
        "schema_version": 1,
        "scan_id": scan_id,
        "findings": findings,
    }
    report = _report_model(scan_id, coverage, findings, reason_codes)
    _validate_manifest(manifest)
    _validate_coverage(coverage)
    _validate_findings_artifact(findings_artifact)
    _validate_report(report)
    artifacts = _write_scan_artifacts(
        root,
        scan_id,
        manifest,
        coverage,
        findings_artifact,
        _render_report(report, findings),
    )
    return {
        "schema_version": 1,
        "status": coverage["verdict"],
        "scan_id": scan_id,
        "reason_codes": reason_codes,
        "finding_count": len(findings),
        "coverage_gaps": coverage["gaps"],
        "artifacts": artifacts,
        "model_calls": 0,
    }

def load_scan_artifact(root, scan_id, name):
    if not SCAN_RE.fullmatch(scan_id) or name not in SECURITY_ARTIFACTS[:3]:
        raise SecurityError("scan_artifact_invalid")
    with _directory_chain(
        root,
        [".kimiflow", "security", "scans", scan_id],
        False,
    ) as directory:
        return _read_json_at(directory, name)


def _state_write(root, subdir, name, payload):
    _ensure_state_ignored(root)
    with _directory_chain(root, [".kimiflow", "security", subdir], True) as directory:
        try:
            workspace_preflight.atomic_directory_write(
                directory,
                name,
                (_canonical(payload) + "\n").encode("utf-8"),
            )
        except (OSError, workspace_preflight.WorkspaceError) as exc:
            raise SecurityError("artifact_write_failed") from exc


def _load_acceptance(root, acceptance_id):
    if not ACCEPTANCE_RE.fullmatch(acceptance_id):
        raise SecurityError("acceptance_invalid")
    with _directory_chain(root, [".kimiflow", "security", "acceptances"], False) as directory:
        return _read_json_at(directory, acceptance_id + ".json")


def _has_closure(root, acceptance_id):
    try:
        with _directory_chain(root, [".kimiflow", "security", "closures"], False) as directory:
            try:
                info = os.stat(
                    acceptance_id + ".json",
                    dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return False
            if not stat.S_ISREG(info.st_mode):
                raise SecurityError("unsafe_state_path")
            return True
    except SecurityError as exc:
        if exc.code == "state_missing":
            return False
        raise


def _open_acceptances(root):
    directory = os.path.join(root, ".kimiflow", "security", "acceptances")
    if not os.path.isdir(directory) or os.path.islink(directory):
        return []
    values = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        acceptance_id = name[:-5]
        if ACCEPTANCE_RE.fullmatch(acceptance_id) and not _has_closure(root, acceptance_id):
            values.append(acceptance_id)
    return values


@contextlib.contextmanager
def _state_lock(root, name):
    with _LOCAL_STATE_LOCKS_GUARD:
        local_lock = _LOCAL_STATE_LOCKS.setdefault(
            (root, name),
            threading.Lock(),
        )
    with local_lock:
        _ensure_state_ignored(root)
        with _directory_chain(root, [".kimiflow", "security", "locks"], True) as directory:
            flags = (
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = None
            try:
                descriptor = os.open(name + ".lock", flags, 0o600, dir_fd=directory)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    raise SecurityError("unsafe_state_path")
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            except SecurityError:
                raise
            except OSError as exc:
                raise SecurityError("unsafe_state_path") from exc
            finally:
                if descriptor is not None:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)


def _finding_provider_binding(selected, coverage):
    provider = selected["provenance"]["provider"]
    if provider in ("gitleaks", "trufflehog"):
        lane = "secrets"
    elif provider == "osv-scanner":
        lane = "dependencies"
    elif provider.startswith("sarif-provider-"):
        lane = "sarif"
    else:
        raise SecurityError("finding_provider_invalid")
    receipt = next(
        (
            row for row in coverage.get("receipts", [])
            if row.get("lane") == lane and row.get("status") in PROVIDER_COMPLETE
        ),
        None,
    )
    if receipt is None:
        raise SecurityError("finding_provider_invalid")
    rule_set_digest = ""
    if lane == "sarif":
        match = re.fullmatch(
            r"rule_set:(sha256:[0-9a-f]{64});execution:(?:attested|unattested)",
            receipt["side_effects"],
        )
        if match is None:
            raise SecurityError("finding_provider_invalid")
        rule_set_digest = match.group(1)
    return {
        "lane": lane,
        "finding_provider": provider,
        "finding_provider_version": selected["provenance"]["provider_version"],
        "receipt_provider": receipt["provider"],
        "receipt_version": receipt["version"],
        "scope": receipt["scope"],
        "side_effects": receipt["side_effects"],
        "rule_set_digest": rule_set_digest,
    }


def accept_finding(root, scan_id, finding_id):
    root = os.path.realpath(root)
    with _state_lock(root, "acceptance"):
        return _accept_finding_locked(root, scan_id, finding_id)


def _manifest_is_current(root, manifest):
    _validate_manifest(manifest)
    scope = manifest["scope"]
    target = root if scope == "." else os.path.join(root, scope)
    if not os.path.exists(target):
        return False
    current = _snapshot(root, scope, target, manifest["mode"])
    context = security_context(root, scope)
    current_scope_digest = digest({
        "mode": manifest["mode"],
        "scope": scope,
        "guidance_digest": context["guidance_digest"],
        "policy_digest": context["policy_digest"],
    })
    return (
        current["content_digest"] == manifest["content_digest"]
        and current["revision"] == manifest["revision"]
        and current_scope_digest == manifest["scope_digest"]
        and context["policy_digest"] == manifest["policy_digest"]
    )


def _accept_finding_locked(root, scan_id, finding_id):
    if _open_acceptances(root):
        raise SecurityError("acceptance_conflict")
    manifest = load_scan_artifact(root, scan_id, "SECURITY-SCAN-MANIFEST.json")
    if not _manifest_is_current(root, manifest):
        raise SecurityError("finding_not_current")
    findings = load_scan_artifact(root, scan_id, "SECURITY-FINDINGS.json")["findings"]
    selected = next((row for row in findings if row["finding_id"] == finding_id), None)
    if selected is None:
        raise SecurityError("finding_not_current")
    coverage = load_scan_artifact(root, scan_id, "SECURITY-COVERAGE.json")
    _validate_coverage(coverage)
    provider_binding = _finding_provider_binding(selected, coverage)
    acceptance_id = "accept_" + digest({
        "scan_id": scan_id,
        "finding_id": finding_id,
        "scope_digest": manifest["scope_digest"],
    }).split(":", 1)[1][:32]
    child_slug = "security-fix-" + acceptance_id.split("_", 1)[1][:16]
    child_run = ".kimiflow/" + child_slug
    value = {
        "schema_version": 1,
        "status": "fix_child_ready",
        "acceptance_id": acceptance_id,
        "scan_id": scan_id,
        "finding_id": finding_id,
        "scope_digest": manifest["scope_digest"],
        "provider_binding": provider_binding,
        "child_run": child_run,
        "child_contract": {
            "flow_schema": 5,
            "mode": "fix",
            "required_phases": ["plan", "build", "verify", "conformance", "code_review"],
            "parent_receipt": ".kimiflow/security/acceptances/%s.json" % acceptance_id,
            "task": "Fix one accepted local security finding %s from scan %s" % (finding_id, scan_id),
        },
    }
    parent = {
        "schema_version": 1,
        "acceptance_id": acceptance_id,
        "scan_id": scan_id,
        "finding_id": finding_id,
        "scope_digest": manifest["scope_digest"],
        "parent_receipt_digest": digest(value),
    }
    state_text = (
        "# Kimiflow Security Fix\n\n"
        "Flow schema: 5\n"
        "Feature: Accepted local security finding\n"
        "Slug: %s\n"
        "Mode: fix\n"
        "Scope: small\n"
        "Discovery required: no\n"
        "Architecture contract: 1\n"
        "Architecture deliberation: pending\n"
        "Conformance contract: 1\n"
        "Convergence contract: 1\n"
        "Execution contract: 1\n"
        "Conformance basis: pending\n"
        "Build risk: none\n"
        "Recovery: clean\n"
        "Review gate: code\n"
        "Review epoch: 1\n"
        "Review epoch start: 1\n"
        "Review epoch cap: 2\n"
        "Status: backlog\n"
        "Phase reads required: yes\n"
        "Affected files:\n"
        "Phase 0: open\n"
        + "".join("Phase %d: open\n" % phase for phase in range(1, 8))
    ) % child_slug
    problem_text = (
        "# Problem\n\n"
        "Fix exactly finding `%s` accepted from security scan `%s`.\n\n"
        "The parent receipt is `.kimiflow/security/acceptances/%s.json`. "
        "Do not broaden the child run beyond this finding. Complete the normal "
        "schema-5 fix lifecycle and write its terminal verification, outcome "
        "evaluation, phase-read and closure-evidence receipts before closure.\n"
    ) % (finding_id, scan_id, acceptance_id)
    _ensure_state_ignored(root)
    with _directory_chain(root, [".kimiflow"], True) as state_directory:
        try:
            os.mkdir(child_slug, 0o700, dir_fd=state_directory)
        except FileExistsError as exc:
            raise SecurityError("child_run_conflict") from exc
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        child_directory = os.open(child_slug, flags, dir_fd=state_directory)
        try:
            workspace_preflight.atomic_directory_write(
                child_directory,
                "STATE.md",
                state_text.encode("utf-8"),
            )
            workspace_preflight.atomic_directory_write(
                child_directory,
                "PROBLEM.md",
                problem_text.encode("utf-8"),
            )
            workspace_preflight.atomic_directory_write(
                child_directory,
                "SECURITY-PARENT.json",
                (_canonical(parent) + "\n").encode("utf-8"),
            )
            os.fsync(child_directory)
        except (OSError, workspace_preflight.WorkspaceError) as exc:
            for name in ("STATE.md", "PROBLEM.md", "SECURITY-PARENT.json"):
                try:
                    os.unlink(name, dir_fd=child_directory)
                except FileNotFoundError:
                    pass
            os.close(child_directory)
            os.rmdir(child_slug, dir_fd=state_directory)
            raise SecurityError("artifact_write_failed") from exc
        else:
            os.close(child_directory)
    try:
        _state_write(root, "acceptances", acceptance_id + ".json", value)
    except SecurityError:
        with _directory_chain(root, [".kimiflow", child_slug], False) as child_directory:
            for name in ("STATE.md", "PROBLEM.md", "SECURITY-PARENT.json"):
                os.unlink(name, dir_fd=child_directory)
        with _directory_chain(root, [".kimiflow"], False) as state_directory:
            os.rmdir(child_slug, dir_fd=state_directory)
        raise
    return value


def _state_values(text):
    values = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values.setdefault(key.strip(), []).append(value.strip())
    return values


def _regular_child(root, child_run):
    state_root = os.path.join(root, ".kimiflow")
    if os.path.islink(state_root) or not os.path.isdir(state_root):
        raise SecurityError("child_run_invalid")
    original = child_run if os.path.isabs(child_run) else os.path.join(root, child_run)
    if os.path.islink(original):
        raise SecurityError("child_run_invalid")
    if os.path.isabs(child_run):
        child = os.path.realpath(child_run)
        rel = os.path.relpath(child, root).replace(os.sep, "/")
    else:
        rel = child_run.replace(os.sep, "/")
        child = os.path.realpath(os.path.join(root, rel))
    if not SAFE_RUN_RE.fullmatch(rel) or not _within(child, os.path.join(root, ".kimiflow")):
        raise SecurityError("child_run_invalid")
    if not os.path.isdir(child):
        raise SecurityError("child_run_invalid")
    return child, rel


def _terminal_lifecycle_gate(root, child, child_rel, state_text):
    state = _state_values(state_text)
    selectors = (
        state.get("Scope"),
        state.get("Convergence contract"),
        state.get("Review gate"),
        state.get("Review epoch"),
        state.get("Review epoch start"),
        state.get("Review epoch cap"),
    )
    if any(len(values or []) != 1 for values in selectors):
        raise SecurityError("child_lifecycle_invalid")
    scope, convergence, review_gate, review_epoch, epoch_start, epoch_cap = (
        values[0] for values in selectors
    )
    if (
        scope != "small"
        or convergence != "1"
        or review_gate.lower() != "code"
        or not review_epoch.isdigit()
        or not epoch_start.isdigit()
        or not epoch_cap.isdigit()
    ):
        raise SecurityError("child_lifecycle_invalid")
    round_number = int(review_epoch)
    start_number = int(epoch_start)
    cap_number = int(epoch_cap)
    if (
        not 1 <= start_number <= round_number <= cap_number
        or cap_number != start_number + 1
    ):
        raise SecurityError("child_lifecycle_invalid")
    try:
        with _directory_chain(
            root,
            child_rel.split("/") + ["review-saturation"],
            False,
        ) as saturation_directory:
            saturation = _read_json_at(
                saturation_directory,
                "r%d.json" % round_number,
            )
    except SecurityError as exc:
        raise SecurityError("child_lifecycle_invalid") from exc
    axes = saturation.get("axes") if isinstance(saturation, dict) else None
    if (
        not isinstance(axes, list)
        or not axes
        or len(axes) != len(set(axes))
        or any(not isinstance(axis, str) or not axis for axis in axes)
    ):
        raise SecurityError("child_lifecycle_invalid")
    hooks = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    conformance_gate = os.path.join(hooks, "conformance-gate.sh")
    review_resolver = os.path.join(hooks, "resolve-review-gate.sh")
    if any(
        not os.path.isfile(path) or not os.access(path, os.X_OK)
        for path in (conformance_gate, review_resolver)
    ):
        raise SecurityError("child_lifecycle_invalid")
    conformance = _execute(
        None,
        [conformance_gate, child, "--finish"],
        cwd=root,
        timeout=DEFAULT_TIMEOUT,
        max_output=65536,
    )
    try:
        lines = conformance.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise SecurityError("child_lifecycle_invalid") from exc
    fields = lines[0].split("\t") if len(lines) == 1 else []
    basis = (state.get("Conformance basis") or [""])[0]
    if (
        conformance.error_code
        or conformance.returncode != 0
        or fields != [
            "CONFORMANCE_GATE",
            "OPEN",
            "blockers=0",
            "reason=clean",
            "detail=basis=" + basis,
        ]
    ):
        raise SecurityError("child_lifecycle_invalid")
    review = _execute(
        None,
        [
            review_resolver,
            os.path.join(child, "findings"),
            "--round",
            str(round_number),
            "--expect",
            "code-verified",
            "--finding-contract",
            "1",
            "--review-axes",
            ",".join(axes),
            "--gate",
            "code",
            "--epoch-start",
            str(start_number),
            "--cap",
            str(cap_number),
        ],
        cwd=root,
        timeout=DEFAULT_TIMEOUT,
        max_output=65536,
    )
    try:
        lines = review.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise SecurityError("child_lifecycle_invalid") from exc
    fields = lines[0].split("\t") if len(lines) == 1 else []
    if (
        review.error_code
        or review.returncode != 0
        or len(fields) != 4
        or fields[:3] != ["OPEN", "0", "clean"]
    ):
        raise SecurityError("child_lifecycle_invalid")


def close_finding(root, acceptance_id, child_run):
    root = os.path.realpath(root)
    if _has_closure(root, acceptance_id):
        raise SecurityError("closure_conflict")
    acceptance = _load_acceptance(root, acceptance_id)
    _child, child_rel = _regular_child(root, child_run)
    if child_rel != acceptance.get("child_run"):
        raise SecurityError("child_run_mismatch")
    with _directory_chain(root, child_rel.split("/"), False) as child_directory:
        parent = _read_json_at(child_directory, "SECURITY-PARENT.json")
        state_text = _read_text_at(child_directory, "STATE.md")
        outcome = _read_json_at(child_directory, "SESSION-OUTCOME.json")
        outcome_evaluation = _read_json_at(child_directory, "OUTCOME-EVALUATION.json")
        phase_read_receipt = _read_json_at(child_directory, "PHASE-READS.json")
        verification_text = _read_text_at(child_directory, "VERIFICATION.md")
        evidence = _read_json_at(child_directory, "SECURITY-CLOSURE-EVIDENCE.json")
    expected_parent = {
        "schema_version": 1,
        "acceptance_id": acceptance["acceptance_id"],
        "scan_id": acceptance["scan_id"],
        "finding_id": acceptance["finding_id"],
        "scope_digest": acceptance["scope_digest"],
        "parent_receipt_digest": digest(acceptance),
    }
    if parent != expected_parent:
        raise SecurityError("child_parent_mismatch")
    state = _state_values(state_text)
    if (
        state.get("Flow schema") != ["5"]
        or state.get("Mode") != ["fix"]
        or state.get("Status") != ["done"]
        or state.get("Phase reads required") != ["yes"]
        or state.get("Conformance contract") != ["1"]
        or state.get("Convergence contract") != ["1"]
        or not re.fullmatch(r"[0-9a-f]{64}", (state.get("Conformance basis") or [""])[0])
        or any(state.get("Phase %d" % phase) != ["done"] for phase in range(8))
    ):
        raise SecurityError("child_not_terminal")
    phase_gate = phase_reads.gate(root, _child, 7)
    if phase_gate.get("status") != "OPEN" or phase_gate.get("reason") != "clean":
        raise SecurityError("child_not_terminal")
    learning_review = outcome.get("learning_review")
    outcome_summary = outcome.get("outcome_evaluation")
    expected_review_path = child_rel + "/LEARNING-REVIEW.md"
    learning_verify = outcome.get("learning_verify")
    verify_parts = learning_verify.split("\t") if isinstance(learning_verify, str) else []
    recorded_verify = (
        len(verify_parts) == 5
        and verify_parts[:4] == [
            "LEARNING_REVIEW", "OPEN", "status=recorded", "freshness=current",
        ]
        and verify_parts[4] == "path=" + expected_review_path
    )
    skipped_verify = (
        len(verify_parts) == 5
        and verify_parts[:3] == ["LEARNING_REVIEW", "OPEN", "status=skipped"]
        and verify_parts[3].startswith("reason=")
        and len(verify_parts[3]) > len("reason=")
        and verify_parts[4] == "path=" + expected_review_path
    )
    if (
        outcome.get("schema_version") != 1
        or outcome.get("outcome") != "done"
        or outcome.get("reason") is not None
        or not isinstance(learning_review, dict)
        or learning_review.get("schema_version") != 1
        or learning_review.get("status") not in ("recorded", "skipped")
        or learning_review.get("run") != child_rel
        or learning_review.get("review_path") != expected_review_path
        or learning_review.get("written") is not True
        or not (recorded_verify or skipped_verify)
        or not isinstance(outcome_summary, dict)
        or outcome_summary.get("status") != "evaluated"
    ):
        raise SecurityError("child_not_terminal")
    try:
        _parse_time(outcome.get("completed_at"))
        _parse_time(outcome_evaluation.get("evaluated_at"))
    except SecurityError as exc:
        raise SecurityError("child_not_terminal") from exc
    if (
        outcome_evaluation.get("schema_version") != 1
        or not re.fullmatch(r"out_[0-9a-f]{64}", str(outcome_evaluation.get("id") or ""))
        or outcome_evaluation.get("run") != child_rel
        or outcome_evaluation.get("terminal") != "done"
        or outcome_evaluation.get("classification") != "verified_success"
        or outcome_evaluation.get("promotable") is not True
        or outcome_evaluation.get("mode") != "fix"
        or any(
            outcome_summary.get(field) != outcome_evaluation.get(field)
            for field in ("id", "terminal", "classification", "promotable")
        )
    ):
        raise SecurityError("child_not_terminal")
    marker_lines = [
        line for line in verification_text.splitlines()
        if "kimiflow:verification" in line
    ]
    if marker_lines != [VERIFICATION_MARKER]:
        raise SecurityError("child_verification_invalid")
    _terminal_lifecycle_gate(
        root,
        _child,
        child_rel,
        state_text,
    )
    expected = {
        "schema_version",
        "acceptance_id",
        "scan_id",
        "finding_id",
        "scope_digest",
        "original_reproduction",
        "regression",
        "legitimate_behavior",
        "bypass",
        "rescan_scan_id",
        "rescan_coverage_digest",
    }
    if set(evidence) != expected or evidence.get("schema_version") != 1:
        raise SecurityError("closure_evidence_invalid")
    for field in ("acceptance_id", "scan_id", "finding_id", "scope_digest"):
        if evidence.get(field) != acceptance.get(field):
            raise SecurityError("closure_evidence_mismatch")
    if (
        evidence.get("original_reproduction") != "negative"
        or any(evidence.get(field) != "passed" for field in ("regression", "legitimate_behavior", "bypass"))
    ):
        raise SecurityError("closure_evidence_incomplete")
    rescan_id = evidence.get("rescan_scan_id")
    manifest = load_scan_artifact(root, rescan_id, "SECURITY-SCAN-MANIFEST.json")
    coverage = load_scan_artifact(root, rescan_id, "SECURITY-COVERAGE.json")
    findings = load_scan_artifact(root, rescan_id, "SECURITY-FINDINGS.json")["findings"]
    if not _manifest_is_current(root, manifest):
        raise SecurityError("closure_stale")
    _validate_coverage(coverage)
    if manifest.get("scope_digest") != acceptance["scope_digest"]:
        raise SecurityError("closure_stale")
    if evidence.get("rescan_coverage_digest") != digest(coverage):
        raise SecurityError("closure_stale")
    if coverage.get("status") != "complete" or coverage.get("verdict") != "clean":
        raise SecurityError("closure_incomplete")
    provider_binding = acceptance.get("provider_binding")
    expected_binding_fields = {
        "lane",
        "finding_provider",
        "finding_provider_version",
        "receipt_provider",
        "receipt_version",
        "scope",
        "side_effects",
        "rule_set_digest",
    }
    binding_shape = (
        isinstance(provider_binding, dict)
        and set(provider_binding) == expected_binding_fields
    )
    matching_receipt = None
    if binding_shape:
        matching_receipt = next(
            (
                receipt for receipt in coverage.get("receipts", [])
                if receipt.get("lane") == provider_binding.get("lane")
                and receipt.get("provider") == provider_binding.get("receipt_provider")
                and receipt.get("version") == provider_binding.get("receipt_version")
                and receipt.get("scope") == provider_binding.get("scope")
                and receipt.get("status") in PROVIDER_COMPLETE
            ),
            None,
        )
    provider_match = False
    if matching_receipt is not None:
        if provider_binding["lane"] == "sarif":
            side_effects = re.fullmatch(
                r"rule_set:(sha256:[0-9a-f]{64});execution:(attested|unattested)",
                matching_receipt.get("side_effects", ""),
            )
            provider_match = bool(
                side_effects
                and side_effects.group(1) == provider_binding["rule_set_digest"]
                and side_effects.group(2) == "attested"
            )
        else:
            provider_match = (
                matching_receipt.get("side_effects") == provider_binding["side_effects"]
                and provider_binding["rule_set_digest"] == ""
            )
    if (
        not binding_shape
        or not provider_match
    ):
        raise SecurityError("closure_provider_mismatch")
    if any(row.get("finding_id") == acceptance["finding_id"] for row in findings):
        raise SecurityError("finding_still_present")
    child_evidence_digest = digest({
        "parent": digest(parent),
        "state": digest(state_text.encode("utf-8")),
        "outcome": digest(outcome),
        "outcome_evaluation": digest(outcome_evaluation),
        "phase_reads": digest(phase_read_receipt),
        "verification": digest(verification_text.encode("utf-8")),
        "closure_evidence": digest(evidence),
    })
    closure = {
        "schema_version": 1,
        "status": "closed",
        "acceptance_id": acceptance_id,
        "scan_id": acceptance["scan_id"],
        "finding_id": acceptance["finding_id"],
        "rescan_scan_id": rescan_id,
        "child_run": child_rel,
        "child_evidence_digest": child_evidence_digest,
    }
    _state_write(root, "closures", acceptance_id + ".json", closure)
    return closure


def advisory(root, *, which=None, command_executor=None):
    root, scope, _ = resolve_scan_root(root, "staged")
    snapshot = _parse_diff(root, scope, staged=True)
    if snapshot["skipped"]:
        return {
            "schema_version": 1,
            "status": "output_limit",
            "provider": "scope-inventory",
            "finding_count": 0,
        }
    if not snapshot["files"]:
        return {"schema_version": 1, "status": "empty", "provider": "", "finding_count": 0}
    receipt, findings = scan_secrets(
        root,
        snapshot,
        "staged",
        which=which,
        command_executor=command_executor,
    )
    return {
        "schema_version": 1,
        "status": receipt["status"],
        "provider": receipt["provider"],
        "finding_count": len(findings),
    }


def add_runner_parser(subparsers):
    parser = subparsers.add_parser("security", help="run local read-only security evidence flows")
    commands = parser.add_subparsers(dest="security_command", required=True)
    for name in ("scan", "diff"):
        command = commands.add_parser(name)
        command.add_argument("path", nargs="?", default=".")
        command.add_argument("--sarif", action="append", default=[])
        command.add_argument("--authorization")
        command.add_argument("--pretty", action="store_true")
    accept = commands.add_parser("accept")
    accept.add_argument("scan_id")
    accept.add_argument("finding_id")
    accept.add_argument("--root", default=".")
    accept.add_argument("--pretty", action="store_true")
    close = commands.add_parser("close")
    close.add_argument("acceptance_id")
    close.add_argument("--child-run", required=True)
    close.add_argument("--root", default=".")
    close.add_argument("--pretty", action="store_true")
    return parser


def run_from_args(args):
    if args.security_command in ("scan", "diff"):
        return run_scan(
            args.security_command,
            args.path,
            sarif_paths=args.sarif,
            authorization_path=args.authorization,
        )
    root = os.path.realpath(args.root)
    if args.security_command == "accept":
        return accept_finding(root, args.scan_id, args.finding_id)
    return close_finding(root, args.acceptance_id, args.child_run)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kimiflow-security")
    commands = parser.add_subparsers(dest="command", required=True)
    advisory_parser = commands.add_parser("advisory")
    advisory_parser.add_argument("--root", default=".")
    advisory_parser.add_argument("--pretty", action="store_true")
    advisory_parser.add_argument("--text", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = advisory(args.root)
        code = 0
    except SecurityError as exc:
        result = {"schema_version": 1, "status": exc.code, "error": exc.message}
        code = exc.exit_code
    if args.text:
        provider = result.get("provider") or "local scanner"
        if result.get("finding_count", 0) > 0:
            print(
                "- [FLAG] staged content — %s reported a potential in-source secret; "
                "commit-secret-gate checks paths only — review the staged diff." % provider
            )
        elif result.get("status") in ("missing", "unsupported"):
            print(
                "kimiflow secret-content-scan: no compatible local scanner on PATH — "
                "in-source secret scan SKIPPED (advisory only).",
                file=sys.stderr,
            )
        elif result.get("status") not in ("empty", "complete"):
            print(
                "- [FLAG] staged content — %s could not complete the local secret scan "
                "(%s); review the staged diff." % (provider, result.get("status")),
            )
    else:
        print(json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        ))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
