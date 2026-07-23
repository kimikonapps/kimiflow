"""Build and verify deterministic Kimiflow runtime releases.

The offline verifier proves artifact integrity only.  The published verifier
owns its GitHub API reads so caller-supplied metadata can never impersonate an
official compatible release.
"""

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile


OFFICIAL_REPOSITORY = "kimikonapps/kimiflow"
OFFICIAL_API = "https://api.github.com/repos/%s" % OFFICIAL_REPOSITORY
UPDATE_MANIFEST_NAME = "kimiflow-update-v1.json"
MANIFEST_SCHEMA_VERSION = 1
RUNTIME_MANIFEST = "RUNTIME-FINGERPRINT.json"
MAX_ENTRIES = 512
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
HEX_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_ID = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
HOST_PROFILES = {
    "embedded": [],
    "app_host": [
        "model_roles",
        "root_confinement",
        "structured_events",
        "workflow_context",
    ],
}
ADVERTISED_FEATURES = sorted(
    {feature for features in HOST_PROFILES.values() for feature in features}
)
RUNTIME_REQUIRED_FILES = {
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    "SKILL.md",
    "hooks/active-run.sh",
    "hooks/hooks.json",
    "phases/PHASES.json",
    "reference.md",
}


class ReleaseError(Exception):
    """A fail-closed release validation error."""


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _read_json(path, label):
    try:
        with open(path, "rb") as handle:
            payload = handle.read()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ReleaseError("%s is not valid JSON: %s" % (label, exc))
    if not isinstance(value, dict):
        raise ReleaseError("%s must be a JSON object" % label)
    return value, payload


def _decode_json_object(payload, label):
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ReleaseError("%s is not valid JSON: %s" % (label, exc))
    if not isinstance(value, dict):
        raise ReleaseError("%s must be a JSON object" % label)
    return value


def _sha256(payload):
    return "sha256:%s" % hashlib.sha256(payload).hexdigest()


def _exact_int(value, expected=None):
    return type(value) is int and (expected is None or value == expected)


def _safe_relative(path):
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        return False
    if path.startswith("/") or posixpath.normpath(path) != path:
        return False
    return not any(part in ("", ".", "..") for part in path.split("/"))


def _file_mode(path):
    return "0755" if os.lstat(path).st_mode & 0o111 else "0644"


def _candidate_inventory(candidate):
    files = []
    for base, dirs, names in os.walk(candidate, followlinks=False):
        dirs.sort()
        names.sort()
        for name in dirs:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, candidate).replace(os.sep, "/")
            mode = os.lstat(full).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ReleaseError("unsafe candidate directory: %s" % rel)
        for name in names:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, candidate).replace(os.sep, "/")
            mode = os.lstat(full).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ReleaseError("unsafe candidate file: %s" % rel)
            files.append(rel)
    return sorted(files)


def _validate_candidate(candidate):
    original_candidate = os.path.abspath(candidate)
    if os.path.islink(original_candidate):
        raise ReleaseError("runtime candidate is missing or unsafe")
    candidate = os.path.realpath(original_candidate)
    if not os.path.isdir(candidate):
        raise ReleaseError("runtime candidate is missing or unsafe")
    fingerprint, fingerprint_payload = _read_json(
        os.path.join(candidate, RUNTIME_MANIFEST), RUNTIME_MANIFEST
    )
    if not _exact_int(fingerprint.get("schema_version"), 1):
        raise ReleaseError("unsupported runtime fingerprint schema")
    if set(fingerprint) != {"schema_version", "runtime_fingerprint", "file_count", "files"}:
        raise ReleaseError("runtime fingerprint keys are invalid")
    if len(fingerprint_payload) > MAX_FILE_BYTES:
        raise ReleaseError("runtime fingerprint exceeds size budget")
    rows = fingerprint.get("files")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ENTRIES:
        raise ReleaseError("runtime fingerprint file list is invalid")
    if not _exact_int(fingerprint.get("file_count"), len(rows)):
        raise ReleaseError("runtime fingerprint file count is invalid")

    expected = []
    total = 0
    seen = set()
    validated_rows = []
    digest = hashlib.sha256()
    for row in rows:
        if not isinstance(row, dict):
            raise ReleaseError("runtime fingerprint row must be an object")
        if set(row) != {"path", "mode", "bytes", "sha256"}:
            raise ReleaseError("runtime fingerprint row keys are invalid")
        rel = row.get("path")
        mode = row.get("mode")
        size = row.get("bytes")
        expected_hash = row.get("sha256")
        if (
            not _safe_relative(rel)
            or rel == RUNTIME_MANIFEST
            or rel.startswith(RUNTIME_MANIFEST + "/")
            or rel in seen
        ):
            raise ReleaseError("unsafe or duplicate runtime path: %r" % rel)
        if mode not in ("0644", "0755"):
            raise ReleaseError("invalid runtime mode: %s" % rel)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > MAX_FILE_BYTES:
            raise ReleaseError("invalid runtime size: %s" % rel)
        if not isinstance(expected_hash, str) or not HEX_DIGEST.fullmatch(expected_hash):
            raise ReleaseError("invalid runtime digest: %s" % rel)
        full = os.path.join(candidate, *rel.split("/"))
        try:
            info = os.lstat(full)
        except OSError:
            raise ReleaseError("runtime file is missing: %s" % rel)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ReleaseError("runtime file is unsafe: %s" % rel)
        with open(full, "rb") as handle:
            payload = handle.read()
        if len(payload) != size or _sha256(payload) != expected_hash:
            raise ReleaseError("runtime content drift: %s" % rel)
        if _file_mode(full) != mode:
            raise ReleaseError("runtime executable-bit drift: %s" % rel)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ReleaseError("runtime candidate exceeds size budget")
        seen.add(rel)
        expected.append(rel)
        validated_rows.append((rel, mode, payload))
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(mode.encode("ascii") + b"\0")
        digest.update(str(size).encode("ascii") + b"\0" + payload + b"\0")
    computed_fingerprint = "sha256:%s" % digest.hexdigest()
    if fingerprint.get("runtime_fingerprint") != computed_fingerprint:
        raise ReleaseError("runtime fingerprint digest is invalid")
    if expected != sorted(expected):
        raise ReleaseError("runtime fingerprint inventory is not sorted")
    if not RUNTIME_REQUIRED_FILES.issubset(seen):
        raise ReleaseError("runtime candidate is missing required runtime files")
    archive_directories = {"kimiflow/"}
    for rel in expected:
        current = "kimiflow"
        for part in rel.split("/")[:-1]:
            current += "/" + part
            archive_directories.add(current + "/")
    if len(archive_directories) + len(expected) + 1 > MAX_ENTRIES:
        raise ReleaseError("runtime candidate exceeds archive entry budget")
    actual = _candidate_inventory(candidate)
    if actual != sorted(expected + [RUNTIME_MANIFEST]):
        raise ReleaseError("runtime candidate inventory does not match its fingerprint")
    if total + len(fingerprint_payload) > MAX_TOTAL_BYTES:
        raise ReleaseError("runtime candidate exceeds size budget")
    if _file_mode(os.path.join(candidate, RUNTIME_MANIFEST)) != "0644":
        raise ReleaseError("runtime fingerprint manifest must not be executable")

    claude, _ = _read_json(os.path.join(candidate, ".claude-plugin", "plugin.json"), "Claude manifest")
    codex, _ = _read_json(os.path.join(candidate, ".codex-plugin", "plugin.json"), "Codex manifest")
    version = claude.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ReleaseError("runtime version is invalid")
    if codex.get("version") != version:
        raise ReleaseError("runtime host manifest versions disagree")
    return {
        "candidate": candidate,
        "fingerprint": fingerprint,
        "fingerprint_payload": fingerprint_payload,
        "rows": validated_rows,
        "version": version,
    }


def _zip_info(name, mode, directory=False):
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_STORED
    type_bits = stat.S_IFDIR if directory else stat.S_IFREG
    info.external_attr = (type_bits | mode) << 16
    if directory:
        info.external_attr |= 0x10
    return info


def _build_archive(candidate_info, archive_path):
    entries = []
    directories = {"kimiflow/"}
    for rel, mode, payload in candidate_info["rows"]:
        parts = rel.split("/")[:-1]
        current = "kimiflow"
        for part in parts:
            current += "/" + part
            directories.add(current + "/")
        entries.append(("kimiflow/" + rel, int(mode, 8), payload))
    entries.append(("kimiflow/" + RUNTIME_MANIFEST, 0o644, candidate_info["fingerprint_payload"]))
    with zipfile.ZipFile(archive_path, "w", allowZip64=False) as archive:
        for directory in sorted(directories):
            archive.writestr(_zip_info(directory, 0o755, directory=True), b"")
        for name, mode, payload in sorted(entries):
            archive.writestr(_zip_info(name, mode), payload)


def _prepare_output_file(path):
    if not os.path.lexists(path):
        return
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ReleaseError("release output file is unsafe: %s" % os.path.basename(path))


def _manifest(candidate_info, archive_name, archive_payload, source_commit):
    version = candidate_info["version"]
    if not COMMIT_ID.fullmatch(source_commit):
        raise ReleaseError("source commit must be a full lowercase Git object id")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "product": "kimiflow",
        "version": version,
        "channel": "stable",
        "source": {"commit": source_commit},
        "update_source": {
            "type": "github_releases",
            "repository": OFFICIAL_REPOSITORY,
            "api": OFFICIAL_API + "/releases",
            "manifest_asset": UPDATE_MANIFEST_NAME,
        },
        "release": {
            "tag": "kimiflow--v%s" % version,
            "immutable_required": True,
        },
        "contracts": {
            "runtime_manifest": 1,
            "adapter_protocol": {"min": 1, "max": 1},
            "features": ADVERTISED_FEATURES,
            "host_profiles": {
                name: {"required_features": features}
                for name, features in sorted(HOST_PROFILES.items())
            },
        },
        "artifact": {
            "name": archive_name,
            "content_type": "application/zip",
            "root": "kimiflow",
            "bytes": len(archive_payload),
            "sha256": _sha256(archive_payload),
            "runtime_fingerprint": candidate_info["fingerprint"]["runtime_fingerprint"],
            "runtime_manifest": RUNTIME_MANIFEST,
        },
    }


def build(candidate, output, source_commit):
    info = _validate_candidate(candidate)
    if not isinstance(source_commit, str) or not COMMIT_ID.fullmatch(source_commit):
        raise ReleaseError("source commit must be a full lowercase Git object id")
    output = os.path.abspath(output)
    os.makedirs(output, exist_ok=True)
    if os.path.islink(output) or not os.path.isdir(output):
        raise ReleaseError("release output is unsafe")
    archive_name = "kimiflow-runtime-%s.zip" % info["version"]
    archive_path = os.path.join(output, archive_name)
    manifest_path = os.path.join(output, UPDATE_MANIFEST_NAME)
    _prepare_output_file(archive_path)
    _prepare_output_file(manifest_path)
    staging = tempfile.mkdtemp(prefix=".kimiflow-runtime-", dir=output)
    staged_archive = os.path.join(staging, archive_name)
    staged_manifest = os.path.join(staging, UPDATE_MANIFEST_NAME)
    try:
        _build_archive(info, staged_archive)
        with open(staged_archive, "rb") as handle:
            archive_payload = handle.read()
        if len(archive_payload) > MAX_TOTAL_BYTES:
            raise ReleaseError("runtime archive exceeds size budget")
        manifest = _manifest(info, archive_name, archive_payload, source_commit)
        with open(staged_manifest, "wb") as handle:
            handle.write(_json_bytes(manifest))
        backups = []
        for current, name in (
            (archive_path, ".previous-archive"),
            (manifest_path, ".previous-manifest"),
        ):
            if os.path.lexists(current):
                backup = os.path.join(staging, name)
                shutil.copyfile(current, backup, follow_symlinks=False)
                os.chmod(backup, stat.S_IMODE(os.lstat(current).st_mode))
            else:
                backup = None
            backups.append((current, backup))
        try:
            os.replace(staged_archive, archive_path)
            os.replace(staged_manifest, manifest_path)
        except OSError as install_error:
            rollback_errors = []
            for current, backup in backups:
                try:
                    if backup is not None:
                        os.replace(backup, current)
                    elif os.path.lexists(current):
                        os.unlink(current)
                except OSError as rollback_error:
                    rollback_errors.append(str(rollback_error))
            if rollback_errors:
                raise ReleaseError(
                    "runtime output installation and rollback failed: %s"
                    % "; ".join(rollback_errors)
                )
            raise ReleaseError(
                "runtime output installation failed; previous outputs restored: %s"
                % install_error
            )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return manifest, manifest_path, archive_path


def _validate_manifest_shape(manifest):
    required = {
        "schema_version",
        "product",
        "version",
        "channel",
        "source",
        "update_source",
        "release",
        "contracts",
        "artifact",
    }
    if set(manifest) != required:
        raise ReleaseError("release manifest keys are invalid")
    if not _exact_int(manifest.get("schema_version"), 1) or manifest.get("product") != "kimiflow":
        raise ReleaseError("release manifest identity is invalid")
    if manifest.get("channel") != "stable":
        raise ReleaseError("release channel is invalid")
    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ReleaseError("release version is invalid")
    source = manifest.get("source")
    commit = source.get("commit") if isinstance(source, dict) else None
    if (
        not isinstance(source, dict)
        or set(source) != {"commit"}
        or not isinstance(commit, str)
        or not COMMIT_ID.fullmatch(commit)
    ):
        raise ReleaseError("release source identity is invalid")
    update = manifest.get("update_source")
    expected_update = {
        "type": "github_releases",
        "repository": OFFICIAL_REPOSITORY,
        "api": OFFICIAL_API + "/releases",
        "manifest_asset": UPDATE_MANIFEST_NAME,
    }
    if update != expected_update:
        raise ReleaseError("release update origin is invalid")
    release = manifest.get("release")
    if (
        not isinstance(release, dict)
        or set(release) != {"tag", "immutable_required"}
        or release.get("tag") != "kimiflow--v%s" % version
        or release.get("immutable_required") is not True
    ):
        raise ReleaseError("release tag contract is invalid")
    contracts = manifest.get("contracts")
    expected_profiles = {
        name: {"required_features": features}
        for name, features in sorted(HOST_PROFILES.items())
    }
    if not isinstance(contracts, dict):
        raise ReleaseError("release contracts are invalid")
    if set(contracts) != {
        "runtime_manifest",
        "adapter_protocol",
        "features",
        "host_profiles",
    }:
        raise ReleaseError("release contract keys are invalid")
    if not _exact_int(contracts.get("runtime_manifest"), 1):
        raise ReleaseError("unsupported runtime manifest contract")
    protocol = contracts.get("adapter_protocol")
    if (
        not isinstance(protocol, dict)
        or set(protocol) != {"min", "max"}
        or not _exact_int(protocol.get("min"), 1)
        or not _exact_int(protocol.get("max"), 1)
    ):
        raise ReleaseError("unsupported adapter protocol contract")
    if contracts.get("host_profiles") != expected_profiles:
        raise ReleaseError("host profile contract is invalid")
    if contracts.get("features") != ADVERTISED_FEATURES:
        raise ReleaseError("advertised feature contract is invalid")
    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict):
        raise ReleaseError("release artifact contract is invalid")
    expected_artifact_keys = {
        "name",
        "content_type",
        "root",
        "bytes",
        "sha256",
        "runtime_fingerprint",
        "runtime_manifest",
    }
    if set(artifact) != expected_artifact_keys:
        raise ReleaseError("release artifact keys are invalid")
    if artifact.get("name") != "kimiflow-runtime-%s.zip" % version:
        raise ReleaseError("release artifact name is invalid")
    if artifact.get("content_type") != "application/zip" or artifact.get("root") != "kimiflow":
        raise ReleaseError("release artifact type is invalid")
    if artifact.get("runtime_manifest") != RUNTIME_MANIFEST:
        raise ReleaseError("release runtime manifest name is invalid")
    if not isinstance(artifact.get("bytes"), int) or isinstance(artifact.get("bytes"), bool):
        raise ReleaseError("release artifact byte count is invalid")
    if artifact["bytes"] < 1 or artifact["bytes"] > MAX_TOTAL_BYTES:
        raise ReleaseError("release artifact byte count exceeds bounds")
    for key in ("sha256", "runtime_fingerprint"):
        if not isinstance(artifact.get(key), str) or not HEX_DIGEST.fullmatch(artifact[key]):
            raise ReleaseError("release artifact %s is invalid" % key)
    return artifact


def _validate_zip(archive_path, manifest):
    artifact = _validate_manifest_shape(manifest)
    try:
        archive_info = os.lstat(archive_path)
        if stat.S_ISLNK(archive_info.st_mode) or not stat.S_ISREG(archive_info.st_mode):
            raise ReleaseError("runtime archive is unsafe")
        if archive_info.st_size != artifact["bytes"]:
            raise ReleaseError("runtime archive digest or size mismatch")
        with open(archive_path, "rb") as handle:
            payload = handle.read(MAX_TOTAL_BYTES + 1)
    except ReleaseError:
        raise
    except OSError as exc:
        raise ReleaseError("runtime archive is unreadable: %s" % exc)
    if len(payload) != artifact["bytes"] or _sha256(payload) != artifact["sha256"]:
        raise ReleaseError("runtime archive digest or size mismatch")
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            if archive.comment:
                raise ReleaseError("runtime archive comment is not canonical")
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES or len({item.filename for item in infos}) != len(infos):
                raise ReleaseError("runtime archive entry list is invalid")
            total = 0
            fingerprint_payload = None
            rows = {}
            directories = set()
            for info in infos:
                name = info.filename
                if "\\" in name or name.startswith("/") or posixpath.normpath(name.rstrip("/")) != name.rstrip("/"):
                    raise ReleaseError("unsafe runtime archive path: %s" % name)
                if not name.startswith("kimiflow/"):
                    raise ReleaseError("runtime archive root is invalid")
                mode = (info.external_attr >> 16) & 0o177777
                if stat.S_ISLNK(mode):
                    raise ReleaseError("runtime archive contains a symlink")
                if (
                    info.compress_type != zipfile.ZIP_STORED
                    or info.date_time != ZIP_TIMESTAMP
                    or info.create_system != 3
                    or info.flag_bits != 0
                    or info.extra
                    or info.comment
                ):
                    raise ReleaseError("runtime archive encoding is not canonical")
                if name.endswith("/"):
                    if (
                        mode != (stat.S_IFDIR | 0o755)
                        or info.file_size != 0
                        or info.compress_size != 0
                    ):
                        raise ReleaseError("runtime archive directory mode is invalid")
                    directories.add(name)
                    continue
                if mode not in (stat.S_IFREG | 0o644, stat.S_IFREG | 0o755):
                    raise ReleaseError("runtime archive file mode is invalid")
                if info.file_size > MAX_FILE_BYTES:
                    raise ReleaseError("runtime archive file exceeds size budget")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ReleaseError("runtime archive exceeds size budget")
                content = archive.read(info)
                rel = name[len("kimiflow/") :]
                if rel == RUNTIME_MANIFEST:
                    if mode != (stat.S_IFREG | 0o644):
                        raise ReleaseError("runtime fingerprint manifest mode is invalid")
                    fingerprint_payload = content
                else:
                    rows[rel] = ((mode & 0o777), content)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ReleaseError("runtime archive is invalid: %s" % exc)
    if fingerprint_payload is None:
        raise ReleaseError("runtime archive has no fingerprint manifest")
    fingerprint = _decode_json_object(
        fingerprint_payload, "archived runtime fingerprint"
    )
    if not _exact_int(fingerprint.get("schema_version"), 1):
        raise ReleaseError("unsupported archived runtime fingerprint schema")
    if (
        not isinstance(fingerprint.get("runtime_fingerprint"), str)
        or not HEX_DIGEST.fullmatch(fingerprint["runtime_fingerprint"])
    ):
        raise ReleaseError("archived runtime fingerprint digest is invalid")
    if fingerprint.get("runtime_fingerprint") != artifact["runtime_fingerprint"]:
        raise ReleaseError("archived runtime fingerprint does not match release manifest")
    expected_rows = fingerprint.get("files")
    if (
        not isinstance(expected_rows, list)
        or not expected_rows
        or len(expected_rows) > MAX_ENTRIES
        or not _exact_int(fingerprint.get("file_count"), len(expected_rows))
    ):
        raise ReleaseError("archived runtime inventory is invalid")
    if set(fingerprint) != {"schema_version", "runtime_fingerprint", "file_count", "files"}:
        raise ReleaseError("archived runtime fingerprint keys are invalid")
    expected_paths = []
    expected_path_set = set()
    for row in expected_rows:
        if not isinstance(row, dict) or set(row) != {"path", "mode", "bytes", "sha256"}:
            raise ReleaseError("archived runtime fingerprint row is invalid")
        rel = row.get("path")
        mode_text = row.get("mode")
        size = row.get("bytes")
        digest_text = row.get("sha256")
        if (
            not _safe_relative(rel)
            or rel == RUNTIME_MANIFEST
            or rel.startswith(RUNTIME_MANIFEST + "/")
            or rel in expected_path_set
            or mode_text not in ("0644", "0755")
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > MAX_FILE_BYTES
            or not isinstance(digest_text, str)
            or not HEX_DIGEST.fullmatch(digest_text)
        ):
            raise ReleaseError("archived runtime fingerprint row is invalid")
        expected_paths.append(rel)
        expected_path_set.add(rel)
    if expected_paths != sorted(expected_paths):
        raise ReleaseError("archived runtime inventory is not sorted")
    if not RUNTIME_REQUIRED_FILES.issubset(expected_path_set):
        raise ReleaseError("archived runtime is missing required runtime files")
    for rel in expected_path_set:
        parts = rel.split("/")
        if any("/".join(parts[:index]) in expected_path_set for index in range(1, len(parts))):
            raise ReleaseError("archived runtime has a file/directory path conflict")
    if set(rows) != expected_path_set:
        raise ReleaseError("archived runtime inventory mismatch")
    expected_directories = {"kimiflow/"}
    for row in expected_rows:
        rel = row.get("path") if isinstance(row, dict) else None
        if not _safe_relative(rel):
            raise ReleaseError("archived runtime path is invalid")
        current = "kimiflow"
        for part in rel.split("/")[:-1]:
            current += "/" + part
            expected_directories.add(current + "/")
    if directories != expected_directories:
        raise ReleaseError("archived runtime directory inventory mismatch")
    expected_order = sorted(expected_directories) + sorted(
        ["kimiflow/" + rel for rel in rows] + ["kimiflow/" + RUNTIME_MANIFEST]
    )
    if [info.filename for info in infos] != expected_order:
        raise ReleaseError("runtime archive entry order is not canonical")
    digest = hashlib.sha256()
    for row in expected_rows:
        rel = row.get("path")
        if not _safe_relative(rel) or rel not in rows:
            raise ReleaseError("archived runtime path is invalid")
        mode, content = rows[rel]
        expected_mode = int(row.get("mode", "0"), 8)
        if mode != expected_mode or len(content) != row.get("bytes") or _sha256(content) != row.get("sha256"):
            raise ReleaseError("archived runtime file mismatch: %s" % rel)
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(row["mode"].encode("ascii") + b"\0")
        digest.update(str(len(content)).encode("ascii") + b"\0" + content + b"\0")
    if "sha256:%s" % digest.hexdigest() != artifact["runtime_fingerprint"]:
        raise ReleaseError("archived runtime fingerprint digest is invalid")
    claude = _decode_json_object(
        rows[".claude-plugin/plugin.json"][1], "archived Claude manifest"
    )
    codex = _decode_json_object(
        rows[".codex-plugin/plugin.json"][1], "archived Codex manifest"
    )
    if claude.get("version") != manifest["version"] or codex.get("version") != manifest["version"]:
        raise ReleaseError("archived runtime host manifest version drift")
    return artifact


def _asset_map(release):
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ReleaseError("release assets are missing")
    result = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise ReleaseError("release asset metadata is invalid")
        if asset["name"] in result:
            raise ReleaseError("release asset names must be unique")
        result[asset["name"]] = asset
    return result


def _validate_release_metadata(release, manifest, manifest_payload, stage):
    if not isinstance(release, dict):
        raise ReleaseError("release metadata must be an object")
    if release.get("tag_name") != manifest["release"]["tag"]:
        raise ReleaseError("release tag metadata mismatch")
    if release.get("prerelease") is not False:
        raise ReleaseError("release prerelease metadata is invalid")
    if stage == "draft":
        if release.get("draft") is not True or release.get("immutable") is not False:
            raise ReleaseError("draft verification requires a draft release")
    else:
        if release.get("draft") is not False or release.get("immutable") is not True:
            raise ReleaseError("published release must be immutable and non-draft")
    assets = _asset_map(release)
    expected = {
        UPDATE_MANIFEST_NAME: (len(manifest_payload), _sha256(manifest_payload)),
        manifest["artifact"]["name"]: (
            manifest["artifact"]["bytes"],
            manifest["artifact"]["sha256"],
        ),
    }
    if set(assets) != set(expected):
        raise ReleaseError("release asset inventory mismatch")
    for name, (size, digest) in expected.items():
        asset = assets.get(name)
        if not asset:
            raise ReleaseError("release asset is missing: %s" % name)
        if not _exact_int(asset.get("size"), size):
            raise ReleaseError("release asset size mismatch: %s" % name)
        if asset.get("digest") != digest:
            raise ReleaseError("release asset digest mismatch: %s" % name)
        if asset.get("state") != "uploaded":
            raise ReleaseError("release asset is not fully uploaded: %s" % name)


def _compatibility(manifest, host_profile, supported_protocol, host_features):
    if host_profile is None and supported_protocol is None and not host_features:
        return None
    if (
        host_profile not in HOST_PROFILES
        or not _exact_int(supported_protocol)
    ):
        raise ReleaseError("host compatibility requires a profile and supported adapter protocol")
    protocol = manifest["contracts"]["adapter_protocol"]
    available = set(host_features or [])
    missing = sorted(set(HOST_PROFILES[host_profile]) - available)
    compatible = protocol["min"] <= supported_protocol <= protocol["max"] and not missing
    return {
        "profile": host_profile,
        "supported_adapter_protocol": supported_protocol,
        "missing_features": missing,
        "compatible": compatible,
    }


def verify_artifact(
    manifest_path,
    archive_path,
    release_json=None,
    stage=None,
    host_profile=None,
    supported_protocol=None,
    host_features=None,
):
    manifest, manifest_payload = _read_json(manifest_path, "release manifest")
    _validate_zip(archive_path, manifest)
    if release_json is not None:
        if stage != "draft":
            raise ReleaseError("caller-supplied release metadata is allowed only for explicit draft verification")
        release, _ = _read_json(release_json, "draft release metadata")
        _validate_release_metadata(release, manifest, manifest_payload, "draft")
        status = "draft_verified"
    else:
        if stage is not None:
            raise ReleaseError("verification stage requires release metadata")
        status = "artifact_verified"
    compatibility = _compatibility(manifest, host_profile, supported_protocol, host_features)
    if compatibility is not None and not compatibility["compatible"]:
        status = "incompatible"
    return status, manifest, compatibility


def _api_json(path, opener=None):
    expected = urllib.parse.urlsplit(OFFICIAL_API + path)
    request = urllib.request.Request(
        urllib.parse.urlunsplit(expected),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "kimiflow-runtime-verifier/1",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        response = open_url(request, timeout=30)
        final = urllib.parse.urlsplit(response.geturl())
        if (
            final.scheme != "https"
            or final.netloc != "api.github.com"
            or final.path != expected.path
            or final.query
            or final.fragment
        ):
            raise ReleaseError("official API request changed origin or path")
        payload = response.read(MAX_FILE_BYTES + 1)
        if len(payload) > MAX_FILE_BYTES:
            raise ReleaseError("official API response exceeds size budget")
        value = json.loads(payload.decode("utf-8"))
    except ReleaseError:
        raise
    except (OSError, urllib.error.URLError, UnicodeDecodeError, ValueError) as exc:
        raise ReleaseError("official API request failed: %s" % exc)
    if not isinstance(value, dict):
        raise ReleaseError("official API response must be an object")
    return value


def verify_published(
    manifest_path,
    archive_path,
    tag,
    host_profile=None,
    supported_protocol=None,
    host_features=None,
    opener=None,
):
    manifest, manifest_payload = _read_json(manifest_path, "release manifest")
    _validate_zip(archive_path, manifest)
    if tag != manifest["release"]["tag"]:
        raise ReleaseError("requested tag does not match release manifest")
    quoted_tag = urllib.parse.quote(tag, safe="")
    release = _api_json("/releases/tags/%s" % quoted_tag, opener=opener)
    _validate_release_metadata(release, manifest, manifest_payload, "published")
    commit = _api_json("/commits/%s" % quoted_tag, opener=opener)
    if commit.get("sha") != manifest["source"]["commit"]:
        raise ReleaseError("official tag does not resolve to the pinned source commit")
    compatibility = _compatibility(manifest, host_profile, supported_protocol, host_features)
    return ("compatible" if compatibility is not None and compatibility["compatible"] else
            "incompatible" if compatibility is not None else "published_verified"), compatibility


def _result(status, manifest, compatibility=None):
    result = {
        "schema_version": 1,
        "status": status,
        "version": manifest["version"],
        "tag": manifest["release"]["tag"],
        "source_commit": manifest["source"]["commit"],
        "artifact": manifest["artifact"]["name"],
    }
    if compatibility is not None:
        result["compatibility"] = compatibility
    return result


def _parser():
    parser = argparse.ArgumentParser(prog="runtime_release")
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--candidate", required=True)
    build_parser.add_argument("--output", required=True)
    build_parser.add_argument("--source-commit", required=True)

    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--archive", required=True)
    verify_parser.add_argument("--release-json")
    verify_parser.add_argument("--stage", choices=("draft",))

    published_parser = commands.add_parser("verify-published")
    published_parser.add_argument("--manifest", required=True)
    published_parser.add_argument("--archive", required=True)
    published_parser.add_argument("--tag", required=True)

    for target in (verify_parser, published_parser):
        target.add_argument("--host-profile", choices=sorted(HOST_PROFILES))
        target.add_argument("--supported-adapter-protocol", type=int)
        target.add_argument("--host-feature", action="append", default=[])
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            manifest, manifest_path, archive_path = build(
                args.candidate, args.output, args.source_commit
            )
            result = _result("built", manifest)
            result["manifest_path"] = manifest_path
            result["archive_path"] = archive_path
        elif args.command == "verify":
            status, manifest, compatibility = verify_artifact(
                args.manifest,
                args.archive,
                release_json=args.release_json,
                stage=args.stage,
                host_profile=args.host_profile,
                supported_protocol=args.supported_adapter_protocol,
                host_features=args.host_feature,
            )
            result = _result(status, manifest, compatibility)
            if status == "incompatible":
                print(json.dumps(result, sort_keys=True))
                return 3
        else:
            status, compatibility = verify_published(
                args.manifest,
                args.archive,
                args.tag,
                host_profile=args.host_profile,
                supported_protocol=args.supported_adapter_protocol,
                host_features=args.host_feature,
            )
            manifest, _ = _read_json(args.manifest, "release manifest")
            result = _result(status, manifest, compatibility)
            if status == "incompatible":
                print(json.dumps(result, sort_keys=True))
                return 3
        print(json.dumps(result, sort_keys=True))
        return 0
    except ReleaseError as exc:
        print("runtime-release: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
