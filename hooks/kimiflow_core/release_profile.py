"""Local, provider-neutral project release profiles.

The model audits and proposes a profile. This module owns the mechanical
boundary: bounded discovery, exact validation, evidence binding, drift checks,
serialized execution, and non-replayable effects.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import posixpath
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile

from .atomic import atomic_write
from . import release_memory


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_DISCOVERY_FILES = 128
MAX_EXPLICIT_SOURCES = 32
MAX_SOURCE_BYTES = 1024 * 1024
MAX_DISCOVERY_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_RELATIVE_INPUT_ENTRIES = 100000
MAX_RELEASE_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024
ARCHIVE_SUFFIXES = (
    ".7z", ".apk", ".bz2", ".gz", ".ipa", ".jar", ".rar", ".tar",
    ".tar.bz2", ".tar.gz", ".tar.xz", ".tgz", ".whl", ".xz", ".zip",
)
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SECRET_VALUE_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
    r"(?:token|password|passwd|secret|api[_-]?key)=[^$<][^\s]{7,})",
    re.IGNORECASE,
)
SECRET_CONTENT_RE = re.compile(
    rb"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    rb"gh[pousr]_[A-Za-z0-9]{20,}|"
    rb"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
    rb"(?:token|password|passwd|secret|api[_-]?key)"
    rb"\s*[:=]\s*[^\s\"']{8,})",
    re.IGNORECASE,
)
SECRET_INPUT_NAME_RE = re.compile(
    r"(?:^|_)(?:auth|credential|key|password|secret|token)(?:_|$)",
    re.IGNORECASE,
)
HIGH_ENTROPY_INPUT_RE = re.compile(
    r"(?=.{32,}$)(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9+/=_-])[A-Za-z0-9+/=_-]+$"
)
PLACEHOLDER_RE = re.compile(r"\{\{([a-z][a-z0-9_]{0,63})\}\}")
V2_INPUT_TYPES = {"git_oid", "tag", "semver", "repository", "relative_path"}
V2_STAGES = {"kimiflow_control", "project_checks", "build", "provider"}
SECRET_PATH_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.|$)|\.npmrc$|\.pypirc$|\.netrc$|"
    r"\.git/config$|.*\.(?:pem|key|p12|pfx)$|"
    r"(?:secrets?|credentials?)(?:[./_-]|$))",
    re.IGNORECASE,
)
MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "pubspec.yaml",
    "fastfile",
    "appfile",
    "matchfile",
}
CONTEXT_NAMES = {
    "changelog.md",
    "changes.md",
    "readme.md",
    "readme.de.md",
    "version",
    "version.txt",
}
CONTROL_WORDS = ("release", "publish", "deploy", "distribution", "fastlane", "changeset")
SHELL_EXECUTABLES = {
    "bash", "cmd", "dash", "fish", "powershell", "pwsh", "sh", "zsh",
}
LOADER_ENVIRONMENT = {
    "BASH_ENV", "CLASSPATH", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "ENV", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS",
    "LD_LIBRARY_PATH", "LD_PRELOAD", "NODE_OPTIONS", "NODE_PATH",
    "PERL5LIB", "PERL5OPT", "PYTHONHOME", "PYTHONPATH",
    "PYTHONSTARTUP", "RUBYLIB", "RUBYOPT", "ZDOTDIR", "_JAVA_OPTIONS",
}
PACKAGE_CONTEXT_ENVIRONMENT = {
    "NPM_CONFIG_GLOBAL", "NPM_CONFIG_GLOBALCONFIG", "NPM_CONFIG_PREFIX",
    "NPM_CONFIG_USERCONFIG", "NPM_CONFIG_WORKSPACE",
    "NPM_CONFIG_WORKSPACES", "YARN_PROJECT_CWD", "YARN_RC_FILENAME",
}
UNSAFE_EXECUTION_ENVIRONMENT = LOADER_ENVIRONMENT | PACKAGE_CONTEXT_ENVIRONMENT | {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_SYSTEM",
    "GIT_DIFF_OPTS", "GIT_DIR", "GIT_EXEC_PATH", "GIT_EXTERNAL_DIFF",
    "GIT_INDEX_FILE", "GIT_NO_LAZY_FETCH", "GIT_OBJECT_DIRECTORY",
    "GIT_OPTIONAL_LOCKS", "GIT_PAGER", "GIT_WORK_TREE", "PAGER",
}
PROBE_UNSAFE_ENVIRONMENT = {
    "GIT_ALLOW_PROTOCOL", "GIT_ASKPASS", "GIT_PROXY_COMMAND", "GIT_SSH",
    "GIT_SSH_COMMAND", "SSH_ASKPASS",
}
DYNAMIC_EXECUTION_WRAPPERS = {
    "bunx", "conda", "corepack", "direnv", "hatch", "mise", "npx",
    "pipenv", "poetry", "rye", "uv",
}
READ_ONLY_GIT_SUBCOMMANDS = {
    "check-ref-format", "describe", "diff", "diff-files", "diff-index",
    "diff-tree", "merge-base", "rev-parse", "show-ref", "status",
}
_AUDIT_FAILURE_ANY = object()


class ReleaseProfileError(ValueError):
    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _reject_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _sha(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _value_sha(value):
    return _sha(_canonical(value))


def _read_json(path, label):
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ReleaseProfileError(label + "_unsafe")
        if info.st_size > MAX_JSON_BYTES:
            raise ReleaseProfileError(label + "_oversize")
        with open(path, "rb") as handle:
            payload = handle.read(MAX_JSON_BYTES + 1)
        if len(payload) > MAX_JSON_BYTES:
            raise ReleaseProfileError(label + "_oversize")
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicates
        )
    except ReleaseProfileError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseProfileError(label + "_malformed") from exc
    if not isinstance(value, dict):
        raise ReleaseProfileError(label + "_malformed")
    return value


def _git(root, *args):
    try:
        executable = release_memory.internal_git_executable()
        return subprocess.run(
            [executable, "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=release_memory.scrub_environment(os.environ),
            check=False,
        )
    except (OSError, release_memory.ReleaseMemoryError) as exc:
        raise ReleaseProfileError("git_unavailable") from exc


def workspace_root(path="."):
    proc = _git(os.path.abspath(path), "rev-parse", "--show-toplevel")
    if proc.returncode:
        raise ReleaseProfileError("not_git_workspace")
    return os.path.realpath(proc.stdout.decode("utf-8", "strict").strip())


def _safe_relative(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        return False
    if "\\" in value or "\x00" in value or value.startswith("/"):
        return False
    return posixpath.normpath(value) == value and all(
        part not in ("", ".", "..") for part in value.split("/")
    )


def _valid_cwd(value):
    return value == "." or _safe_relative(value)


def _safe_cwd(root, value):
    if not _valid_cwd(value):
        raise ReleaseProfileError("cwd_invalid")
    target = root if value == "." else os.path.realpath(
        os.path.join(root, *value.split("/"))
    )
    try:
        if os.path.commonpath((root, target)) != root:
            raise ReleaseProfileError("cwd_invalid")
    except ValueError as exc:
        raise ReleaseProfileError("cwd_invalid") from exc
    if not os.path.isdir(target):
        raise ReleaseProfileError("cwd_missing")
    return target


def _state_paths(root):
    directory = os.path.join(root, ".kimiflow", "release")
    return {
        "directory": directory,
        "discovery": os.path.join(directory, "DISCOVERY.json"),
        "profile": os.path.join(directory, "PROFILE.json"),
        "failure": os.path.join(directory, "FAILURE.json"),
        "run": os.path.join(directory, "RUN.json"),
        "lock": os.path.join(directory, ".lock"),
    }


def _ensure_state_dir(root):
    kimiflow = os.path.join(root, ".kimiflow")
    directory = os.path.join(kimiflow, "release")
    for path in (kimiflow, directory):
        if os.path.lexists(path):
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ReleaseProfileError("state_path_unsafe")
        else:
            os.mkdir(path, 0o700)
        os.chmod(path, 0o700)
    return directory


@contextlib.contextmanager
def _release_lock(root):
    paths = _state_paths(root)
    _ensure_state_dir(root)
    descriptor = os.open(
        paths["lock"],
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        operation = fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as exc:
            raise ReleaseProfileError("release_locked") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _write_local(path, value):
    payload = _json_text(value)
    if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
        raise ReleaseProfileError("state_oversize")
    atomic_write(path, payload, mode=0o600)


def _persist_local(path, value, error):
    _write_local(path, value)
    descriptor = None
    directory_descriptor = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(descriptor)
        directory_descriptor = os.open(
            os.path.dirname(path),
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(directory_descriptor)
    except OSError as exc:
        raise ReleaseProfileError(error) from exc
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        if descriptor is not None:
            os.close(descriptor)


def _durable_unlink(path, error):
    try:
        os.unlink(path)
        descriptor = os.open(
            os.path.dirname(path),
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ReleaseProfileError(error) from exc


def _tracked_files(root):
    proc = _git(root, "ls-files", "-z", "--cached")
    if proc.returncode:
        raise ReleaseProfileError("git_inventory_failed")
    return sorted(
        item.decode("utf-8", "surrogateescape")
        for item in proc.stdout.split(b"\0")
        if item
    )


def _source_kind(path):
    lower = path.lower()
    name = posixpath.basename(lower)
    if SECRET_PATH_RE.search(lower) or lower.startswith(".kimiflow/"):
        return None
    if lower.startswith(".github/workflows/"):
        if any(word in lower for word in CONTROL_WORDS):
            return "workflow", "control_candidate"
        return None
    if name in MANIFEST_NAMES:
        return "manifest", "audit_context"
    if name in CONTEXT_NAMES or (
        lower.startswith(("docs/", ".claude/skills/", ".agents/skills/"))
        and any(word in lower for word in CONTROL_WORDS)
    ):
        return "documentation", "audit_context"
    if any(word in lower for word in CONTROL_WORDS):
        if name.endswith((".sh", ".py", ".js", ".mjs", ".cjs", ".rb", ".pl")):
            return "script", "control_candidate"
        if name in ("makefile", "justfile") or name.endswith(
            (".yml", ".yaml", ".toml", ".json")
        ):
            return "configuration", "control_candidate"
    return None


def _package_scripts_digest(payload):
    try:
        value = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicates
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return None
    scripts = value.get("scripts", {}) if isinstance(value, dict) else None
    if (
        not isinstance(scripts, dict)
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(command, str)
            for key, command in scripts.items()
        )
    ):
        return None
    return _value_sha({"scripts": scripts})


def _source_control(path, payload, role):
    if posixpath.basename(path).lower() == "package.json":
        scripts_digest = _package_scripts_digest(payload)
        if scripts_digest is not None:
            return "control_candidate", "package-scripts", scripts_digest
    if role == "control_candidate":
        return role, "file", _sha(payload)
    return role, "none", None


def _stable_source(root, relative):
    if not _safe_relative(relative):
        raise ReleaseProfileError("source_path_invalid")
    full = os.path.join(root, *relative.split("/"))
    try:
        before = os.lstat(full)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ReleaseProfileError("source_unsafe")
        if before.st_size > MAX_SOURCE_BYTES:
            raise ReleaseProfileError("source_oversize")
        with open(full, "rb") as handle:
            payload = handle.read(MAX_SOURCE_BYTES + 1)
        after = os.lstat(full)
    except ReleaseProfileError:
        raise
    except OSError as exc:
        raise ReleaseProfileError("source_missing") from exc
    if (
        len(payload) > MAX_SOURCE_BYTES
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise ReleaseProfileError("source_changed")
    return payload


def build_discovery(root, includes=None):
    root = workspace_root(root)
    include_rows = [] if includes is None else list(includes)
    if (
        len(include_rows) > MAX_EXPLICIT_SOURCES
        or len(set(include_rows)) != len(include_rows)
        or any(
            not _safe_relative(path)
            or SECRET_PATH_RE.search(path)
            or path.startswith(".kimiflow/")
            for path in include_rows
        )
    ):
        raise ReleaseProfileError("discovery_include_invalid")
    tracked = _tracked_files(root)
    tracked_set = set(tracked)
    if any(path not in tracked_set for path in include_rows):
        raise ReleaseProfileError("discovery_include_untracked")
    explicit = set(include_rows)
    rows = []
    total = 0
    for relative in tracked:
        classified = _source_kind(relative)
        if classified is None and relative not in explicit:
            continue
        payload = _stable_source(root, relative)
        total += len(payload)
        if total > MAX_DISCOVERY_BYTES:
            raise ReleaseProfileError("discovery_size_budget")
        kind, role = classified or ("explicit", "control_candidate")
        role, control_mode, control_sha256 = _source_control(
            relative, payload, role
        )
        rows.append(
            {
                "path": relative,
                "sha256": _sha(payload),
                "control_mode": control_mode,
                "control_sha256": control_sha256,
                "bytes": len(payload),
                "kind": kind,
                "role": role,
            }
        )
        if len(rows) > MAX_DISCOVERY_FILES:
            raise ReleaseProfileError("discovery_file_budget")
    if not rows:
        raise ReleaseProfileError("release_sources_not_found")
    result = {
        "schema_version": SCHEMA_VERSION,
        "document_type": "release_discovery",
        "sources": rows,
    }
    result["inventory_sha256"] = _value_sha(rows)
    return result


def discover(root, write=False, includes=None):
    root = workspace_root(root)
    value = build_discovery(root, includes=includes)
    if write:
        with _release_lock(root):
            _write_local(_state_paths(root)["discovery"], value)
    return value


def _bounded_string(value, label, maximum):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ReleaseProfileError(label + "_invalid")
    return value


def _validate_argv(value, label, probe=False, sealed=False):
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 32
        or any(
            not isinstance(item, str)
            or not item
            or len(item) > 2000
            or "\x00" in item
            for item in value
        )
    ):
        raise ReleaseProfileError(label + "_argv_invalid")
    for item in value:
        if SECRET_VALUE_RE.search(item):
            raise ReleaseProfileError("credential_like_value")
    if _inline_code_forbidden(value):
        raise ReleaseProfileError("inline_code_forbidden")
    if _loader_construct_forbidden(value):
        raise ReleaseProfileError("command_loader_unsupported")
    if _interpreter_option_unsupported(value):
        raise ReleaseProfileError("interpreter_option_unsupported")
    normalized = _unwrap_env(value)
    if (
        normalized
        and posixpath.basename(normalized[0]).lower()
        in DYNAMIC_EXECUTION_WRAPPERS
    ):
        raise ReleaseProfileError("command_wrapper_unsupported")
    if probe and _probe_environment_forbidden(value):
        raise ReleaseProfileError("mutating_probe_forbidden")
    if sealed:
        for name in _env_assignment_names(value):
            if not release_memory.valid_environment_name(
                name, credential=False
            ):
                raise ReleaseProfileError(
                    "command_environment_override_forbidden"
                )
    if posixpath.basename(value[0]).lower() == "env":
        for item in value[1:]:
            if item.upper().startswith("PATH="):
                raise ReleaseProfileError("command_path_override_forbidden")
            if item in ("-C", "--chdir") or item.startswith("--chdir="):
                raise ReleaseProfileError("command_chdir_wrapper_forbidden")
    for index, item in enumerate(value[:-1]):
        executable = posixpath.basename(item).lower()
        if executable in SHELL_EXECUTABLES:
            shell_flags = {
                "-c", "-command", "-encodedcommand", "-lc", "/c",
            }
            if any(arg.lower() in shell_flags for arg in value[index + 1:]):
                raise ReleaseProfileError("shell_string_forbidden")
            if executable in {"bash", "dash", "fish", "sh", "zsh"} and any(
                len(arg) > 2
                and arg.startswith("-")
                and not arg.startswith("--")
                and "c" in arg[1:]
                for arg in value[index + 1:]
            ):
                raise ReleaseProfileError("shell_string_forbidden")
    if probe and _known_mutating_probe(value):
        raise ReleaseProfileError("mutating_probe_forbidden")
    return list(value)


def _unwrap_env(argv):
    current = list(argv)
    for _ in range(4):
        if not current or posixpath.basename(current[0]).lower() != "env":
            return current
        index = 1
        while index < len(current):
            item = current[index]
            if item == "--":
                index += 1
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", item):
                index += 1
                continue
            if item in ("-i", "--ignore-environment", "-0", "--null", "-v", "--debug"):
                index += 1
                continue
            if item in ("-u", "--unset", "-C", "--chdir"):
                index += 2
                continue
            if item.startswith(("--unset=", "--chdir=")):
                index += 1
                continue
            if item.startswith("-"):
                return None
            break
        if index >= len(current):
            return None
        current = current[index:]
    return None


def _env_assignment_names(argv):
    current = list(argv)
    names = []
    for _ in range(4):
        if not current or posixpath.basename(current[0]).lower() != "env":
            break
        index = 1
        while index < len(current):
            item = current[index]
            if item == "--":
                index += 1
                continue
            match = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)=.*", item
            )
            if match:
                names.append(match.group(1))
                index += 1
                continue
            if item.startswith("-"):
                raise ReleaseProfileError(
                    "command_environment_override_forbidden"
                )
            break
        current = current[index:]
    return names


def _loader_construct_forbidden(argv):
    for item in argv:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=.*", item)
        if match and (
            match.group(1).upper() in UNSAFE_EXECUTION_ENVIRONMENT
            or match.group(1).upper().startswith(
                ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
            )
        ):
            return True
    normalized = _unwrap_env(argv)
    if not normalized:
        return False
    executable = _interpreter_kind(normalized[0])
    args = normalized[1:]
    if executable == "python":
        return _python_cluster_has_selector(args, "m")
    if executable == "node":
        return any(
            item in (
                "-r", "--experimental-loader", "--import", "--loader",
                "--require",
            )
            or item.startswith(
                (
                    "--experimental-loader=", "--import=", "--loader=",
                    "--require=",
                )
            )
            or (len(item) > 2 and item.startswith("-r"))
            for item in args
        )
    if executable == "ruby":
        return any(
            item in ("-r", "--require")
            or item.startswith(("-r", "--require="))
            for item in args
        ) or _short_cluster_has_selector(args, {"r"}, {"e", "r"})
    if executable == "perl":
        return any(
            item in ("-d", "-m", "-M")
            or (
                len(item) > 2
                and item[:2] in ("-d", "-m", "-M")
            )
            for item in args
        ) or _short_cluster_has_selector(
            args, {"d", "m", "M"}, {"d", "e", "E", "m", "M"}
        )
    return False


def _probe_environment_forbidden(argv):
    for item in argv:
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=.*", item)
        if match and (
            match.group(1).upper() in PROBE_UNSAFE_ENVIRONMENT
            or match.group(1).upper().startswith("GIT_TRACE")
        ):
            return True
    current = list(argv)
    for _ in range(4):
        if not current or posixpath.basename(current[0]).lower() != "env":
            break
        next_command = None
        for index, item in enumerate(current[1:], 1):
            if item in ("-i", "--ignore-environment", "-u", "--unset"):
                return True
            if item.startswith(("-u", "--unset=")):
                return True
            if item == "--":
                next_command = index + 1
                break
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", item):
                continue
            if not item.startswith("-"):
                next_command = index
                break
        if next_command is None:
            break
        current = current[next_command:]
    return False


def _interpreter_kind(value):
    executable = posixpath.basename(value).lower()
    if re.fullmatch(r"python(?:[23])?(?:\.\d+)*", executable):
        return "python"
    if re.fullmatch(r"node(?:js)?(?:\d+(?:\.\d+)*)?", executable):
        return "node"
    if re.fullmatch(r"ruby(?:\d+(?:\.\d+)*)?", executable):
        return "ruby"
    if re.fullmatch(r"perl(?:\d+(?:\.\d+)*)?", executable):
        return "perl"
    if executable in {"bash", "dash", "fish", "sh", "zsh"}:
        return executable
    return None


def _inline_code_forbidden(argv):
    normalized = _unwrap_env(argv)
    if not normalized:
        return False
    executable = _interpreter_kind(normalized[0])
    args = normalized[1:]
    if executable == "python":
        return _python_cluster_has_selector(args, "c")
    if executable == "node":
        return any(
            item in ("-e", "-p", "--eval", "--print")
            or (len(item) > 2 and item.startswith(("-e", "-p")))
            or item.startswith(("--eval=", "--print="))
            for item in args
        )
    if executable == "ruby":
        return any(
            item == "-e" or (len(item) > 2 and item.startswith("-e"))
            for item in args
        ) or _short_cluster_has_selector(args, {"e"}, {"e", "r"})
    if executable == "perl":
        return any(
            item in ("-e", "-E")
            or (
                len(item) > 2
                and item[:2] in ("-e", "-E")
            )
            for item in args
        ) or _short_cluster_has_selector(
            args, {"e", "E"}, {"d", "e", "E", "m", "M"}
        )
    return False


def _interpreter_option_unsupported(argv):
    normalized = _unwrap_env(argv)
    if not normalized:
        return False
    executable = _interpreter_kind(normalized[0])
    if executable not in {"python", "node", "ruby", "perl"}:
        return False
    allowed = set()
    if executable == "python":
        allowed = {"-B", "-E", "-I", "-O", "-OO", "-P", "-S", "-s", "-u"}
    for item in normalized[1:]:
        if item == "--" or item == "-" or not item.startswith("-"):
            return False
        if item not in allowed:
            return True
    return False


def _short_cluster_has_selector(args, selectors, consuming_selectors):
    for item in args:
        if item == "--":
            break
        if item == "-" or not item.startswith("-"):
            break
        if item.startswith("--"):
            continue
        for option in item[1:]:
            if option in consuming_selectors:
                return option in selectors
    return False


def _python_cluster_has_selector(args, selector):
    no_value_options = set("BdEhiIOPqSsuvVx")
    for item in args:
        if item == "-" + selector:
            return True
        if not item.startswith("-") or item.startswith("--") or len(item) < 3:
            continue
        body = item[1:]
        for option in body:
            if option == selector:
                return True
            if option in no_value_options:
                continue
            break
    return False


def _git_subcommand(args):
    safe_flags = {
        "--glob-pathspecs", "--icase-pathspecs",
        "--literal-pathspecs", "--no-optional-locks", "--no-pager",
        "--no-replace-objects", "--noglob-pathspecs",
    }
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--":
            index += 1
            break
        if item in safe_flags:
            index += 1
            continue
        if item.startswith("-"):
            return None
        return item
    return args[index] if index < len(args) else None


def _git_probe_arguments_safe(args):
    safe_global = {
        "--glob-pathspecs", "--icase-pathspecs", "--literal-pathspecs",
        "--no-optional-locks", "--no-pager", "--no-replace-objects",
        "--noglob-pathspecs",
    }
    index = 0
    while index < len(args) and args[index] in safe_global:
        index += 1
    if index >= len(args):
        return False
    subcommand = args[index]
    allowed = {
        "check-ref-format": {"--allow-onelevel", "--branch", "--normalize"},
        "describe": {
            "--all", "--always", "--contains", "--dirty", "--exact-match",
            "--first-parent", "--long", "--tags",
        },
        "diff": {
            "--cached", "--check", "--exit-code", "--minimal",
            "--name-only", "--name-status", "--no-ext-diff", "--no-renames",
            "--no-textconv", "--quiet", "--staged", "--stat", "--summary",
        },
        "diff-files": {
            "--check", "--exit-code", "--name-only", "--name-status",
            "--no-ext-diff", "--no-renames", "--no-textconv", "--quiet",
            "--stat", "--summary",
        },
        "diff-index": {
            "--cached", "--check", "--exit-code", "--name-only",
            "--name-status", "--no-ext-diff", "--no-renames",
            "--no-textconv", "--quiet", "--stat", "--summary",
        },
        "diff-tree": {
            "--check", "--exit-code", "--name-only", "--name-status",
            "--no-commit-id", "--no-ext-diff", "--no-renames",
            "--no-textconv", "--quiet", "--root", "--stat", "--summary",
        },
        "merge-base": {
            "--all", "--fork-point", "--independent", "--is-ancestor",
            "--octopus",
        },
        "rev-parse": {
            "--abbrev-ref", "--git-dir", "--is-bare-repository",
            "--is-inside-work-tree", "--quiet", "--short", "--show-cdup",
            "--show-prefix", "--show-superproject-working-tree",
            "--show-toplevel", "--symbolic-full-name", "--verify", "-q",
        },
        "show-ref": {
            "--dereference", "--exists", "--head", "--heads", "--quiet",
            "--tags", "--verify", "-d", "-q",
        },
        "status": {
            "--ahead-behind", "--branch", "--ignored", "--no-ahead-behind",
            "--no-renames", "--porcelain", "--renames", "--short",
            "--show-stash", "-b", "-s", "-uno", "-unormal", "-uall",
        },
    }[subcommand]
    for item in args[index + 1:]:
        if item == "--":
            return True
        if not item.startswith("-") or item == "-":
            continue
        if item in allowed:
            continue
        if subcommand == "status" and item.startswith(
            ("--ignore-submodules=", "--ignored=", "--porcelain=", "--untracked-files=")
        ):
            continue
        if subcommand == "describe" and item.startswith(
            ("--abbrev=", "--dirty=", "--exclude=", "--match=")
        ):
            continue
        if subcommand == "rev-parse" and item.startswith("--short="):
            continue
        return False
    return True


def _known_mutating_probe(argv):
    normalized = _unwrap_env(argv)
    if not normalized:
        return True
    executable = posixpath.basename(normalized[0]).lower()
    args = [item.lower() for item in normalized[1:]]
    if executable in {"rm", "mv", "cp", "mkdir", "rmdir", "touch", "tee", "install"}:
        return True
    if executable == "sed" and any(item == "-i" or item.startswith("-i.") for item in args):
        return True
    if executable == "git":
        subcommand = _git_subcommand(normalized[1:])
        if subcommand not in READ_ONLY_GIT_SUBCOMMANDS:
            return True
        if not _git_probe_arguments_safe(normalized[1:]):
            return True
        arguments = normalized[1:]
        if any(
            _git_option_abbreviates(item, "--ext-diff")
            or _git_option_abbreviates(item, "--textconv")
            for item in arguments
        ):
            return True
        option_arguments = arguments
        if "--" in arguments:
            option_arguments = arguments[:arguments.index("--")]
        if subcommand in {
            "diff", "diff-files", "diff-index", "diff-tree", "log", "show",
        } and not {
            "--no-ext-diff", "--no-textconv",
        }.issubset(option_arguments):
            return True
        return False
    if executable == "gh":
        if any(
            args[index:index + 2]
            in (
                ["release", "create"],
                ["release", "delete"],
                ["release", "edit"],
                ["release", "upload"],
            )
            for index in range(max(0, len(args) - 1))
        ):
            return True
        if "api" in args:
            for index, item in enumerate(args):
                if item in ("-x", "--method") and index + 1 < len(args):
                    return args[index + 1] != "get"
                if item.startswith("--method="):
                    return item.split("=", 1)[1] != "get"
    if executable == "kubectl" and any(
        item in {
            "annotate", "apply", "autoscale", "cordon", "create", "delete",
            "drain", "edit", "label", "patch", "replace", "rollout",
            "scale", "set", "taint", "uncordon",
        }
        for item in args
    ):
        return True
    mutating_tokens = {
        "npm": {"publish", "unpublish"},
        "pnpm": {"publish"},
        "cargo": {"publish", "install"},
        "twine": {"upload"},
        "docker": {"push"},
    }
    if executable in mutating_tokens and any(
        item in mutating_tokens[executable] for item in args
    ):
        return True
    if executable == "yarn":
        return "publish" in args or any(
            args[index:index + 2] == ["npm", "publish"]
            for index in range(max(0, len(args) - 1))
        )
    return False


def _git_option_abbreviates(value, option):
    name = value.split("=", 1)[0]
    return len(name) >= 3 and option.startswith(name)


def _validate_probe(value, label, sealed=False):
    if not isinstance(value, dict) or set(value) != {
        "id", "argv", "cwd", "timeout_seconds"
    }:
        raise ReleaseProfileError(label + "_shape_invalid")
    probe_id = value.get("id")
    if not isinstance(probe_id, str) or ID_RE.fullmatch(probe_id) is None:
        raise ReleaseProfileError(label + "_id_invalid")
    cwd = value.get("cwd")
    if not _valid_cwd(cwd):
        raise ReleaseProfileError(label + "_cwd_invalid")
    timeout = value.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
        raise ReleaseProfileError(label + "_timeout_invalid")
    _validate_argv(
        value.get("argv"), label, probe=True, sealed=sealed
    )
    return value


def _validate_v2_policy(value, label, *, effect=False):
    if not isinstance(value, dict) or set(value) != {
        "auth", "stage", "failure", "reuse", "affected_paths",
        "declared_env",
    }:
        raise ReleaseProfileError(label + "_policy_shape_invalid")
    if value.get("auth") not in ("none", "provider"):
        raise ReleaseProfileError(label + "_auth_invalid")
    if value.get("stage") not in V2_STAGES:
        raise ReleaseProfileError(label + "_stage_invalid")
    if value.get("failure") not in ("semantic", "operational"):
        raise ReleaseProfileError(label + "_failure_invalid")
    reuse = value.get("reuse")
    if reuse not in ("never", "kimiflow_verification") or (
        effect and reuse != "never"
    ) or (
        reuse == "kimiflow_verification"
        and value.get("auth") != "none"
    ):
        raise ReleaseProfileError(label + "_reuse_invalid")
    paths = value.get("affected_paths")
    if (
        not isinstance(paths, list)
        or len(paths) > 128
        or any(
            not _safe_relative(path) or SECRET_PATH_RE.search(path)
            for path in paths
        )
        or len(set(paths)) != len(paths)
        or (reuse == "kimiflow_verification" and not paths)
    ):
        raise ReleaseProfileError(label + "_affected_paths_invalid")
    names = value.get("declared_env")
    if (
        not isinstance(names, list)
        or len(names) > 32
        or any(
            not release_memory.valid_environment_name(
                name, credential=False
            )
            for name in names
        )
        or len(set(names)) != len(names)
    ):
        raise ReleaseProfileError(label + "_declared_env_invalid")
    return value


def _validate_v2_probe(value, label):
    if not isinstance(value, dict) or set(value) != {
        "id", "argv", "cwd", "timeout_seconds", "policy",
    }:
        raise ReleaseProfileError(label + "_shape_invalid")
    _validate_probe(
        {
            "id": value.get("id"),
            "argv": value.get("argv"),
            "cwd": value.get("cwd"),
            "timeout_seconds": value.get("timeout_seconds"),
        },
        label,
        sealed=True,
    )
    _validate_v2_policy(value.get("policy"), label)
    return value


def _validate_v2_profile(profile):
    required = {
        "schema_version", "document_type", "id", "control_sources",
        "inputs", "identity", "steps", "final_checks",
    }
    if not isinstance(profile, dict) or set(profile) != required:
        raise ReleaseProfileError("profile_shape_invalid")
    if (
        profile.get("schema_version") != 2
        or profile.get("document_type") != "release_profile"
    ):
        raise ReleaseProfileError("profile_version_invalid")
    if (
        not isinstance(profile.get("id"), str)
        or ID_RE.fullmatch(profile["id"]) is None
    ):
        raise ReleaseProfileError("profile_id_invalid")
    declarations = profile.get("inputs")
    if not isinstance(declarations, list) or len(declarations) > 32:
        raise ReleaseProfileError("profile_inputs_invalid")
    names = []
    for row in declarations:
        if not isinstance(row, dict) or set(row) != {
            "name", "type", "publication_target",
        }:
            raise ReleaseProfileError("profile_input_shape_invalid")
        name = row.get("name")
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is None
            or SECRET_INPUT_NAME_RE.search(name)
            or row.get("type") not in V2_INPUT_TYPES
            or not isinstance(row.get("publication_target"), bool)
        ):
            raise ReleaseProfileError("profile_input_invalid")
        names.append(name)
    if len(names) != len(set(names)):
        raise ReleaseProfileError("profile_input_duplicate")
    if not any(row["publication_target"] for row in declarations):
        raise ReleaseProfileError("profile_publication_target_missing")

    identity = profile.get("identity")
    if not isinstance(identity, dict) or identity.get("provider") not in (
        "environment", "github",
    ):
        raise ReleaseProfileError("profile_identity_invalid")
    if identity["provider"] == "environment":
        if set(identity) != {"provider", "environment"}:
            raise ReleaseProfileError("profile_identity_invalid")
        environment = identity.get("environment")
        if (
            not isinstance(environment, list)
            or not environment
            or len(environment) > 16
            or any(
                not release_memory.valid_environment_name(
                    name, credential=True
                )
                or name in {"GH_TOKEN", "GITHUB_TOKEN"}
                for name in environment
            )
            or len(environment) != len(set(environment))
        ):
            raise ReleaseProfileError("profile_identity_invalid")
    else:
        if set(identity) != {"provider"}:
            raise ReleaseProfileError("profile_identity_invalid")
        repository_inputs = [
            row for row in declarations if row["type"] == "repository"
        ]
        if (
            len(repository_inputs) != 1
            or repository_inputs[0]["publication_target"] is not True
        ):
            raise ReleaseProfileError("profile_identity_target_invalid")

    # Reuse the v1 control-source contract.
    controls = profile.get("control_sources")
    if not isinstance(controls, list) or not 1 <= len(controls) <= 64:
        raise ReleaseProfileError("control_sources_invalid")
    control_paths = []
    for row in controls:
        if not isinstance(row, dict) or set(row) != {
            "path", "digest_mode", "sha256",
        }:
            raise ReleaseProfileError("control_source_shape_invalid")
        if (
            not _safe_relative(row.get("path"))
            or SECRET_PATH_RE.search(row["path"])
            or row.get("digest_mode") not in ("file", "package-scripts")
            or DIGEST_RE.fullmatch(row.get("sha256", "")) is None
        ):
            raise ReleaseProfileError("control_source_path_invalid")
        control_paths.append(row["path"])
    if len(control_paths) != len(set(control_paths)):
        raise ReleaseProfileError("control_source_duplicate")

    steps = profile.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ReleaseProfileError("steps_invalid")
    ids = []
    probe_ids = []
    audit_ids = []
    commands = []
    for step in steps:
        if not isinstance(step, dict):
            raise ReleaseProfileError("step_shape_invalid")
        step_id = step.get("id")
        if not isinstance(step_id, str) or ID_RE.fullmatch(step_id) is None:
            raise ReleaseProfileError("step_id_invalid")
        if step.get("kind") == "check":
            if set(step) != {
                "id", "kind", "argv", "cwd", "timeout_seconds", "policy",
            }:
                raise ReleaseProfileError("check_step_shape_invalid")
            _validate_v2_probe(
                {key: value for key, value in step.items() if key != "kind"},
                "check_step",
            )
            audit_ids.append(step_id)
            commands.append(step)
        elif step.get("kind") == "effect":
            if set(step) != {
                "id", "kind", "scope", "argv", "cwd", "timeout_seconds",
                "policy", "precondition", "postcondition",
            }:
                raise ReleaseProfileError("effect_step_shape_invalid")
            if step.get("scope") not in ("local", "remote"):
                raise ReleaseProfileError("effect_scope_invalid")
            if not _valid_cwd(step.get("cwd")):
                raise ReleaseProfileError("effect_cwd_invalid")
            timeout = step.get("timeout_seconds")
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or not 1 <= timeout <= 3600
            ):
                raise ReleaseProfileError("effect_timeout_invalid")
            _validate_argv(
                step.get("argv"),
                "effect",
                probe=False,
                sealed=True,
            )
            _validate_v2_policy(step.get("policy"), "effect", effect=True)
            _validate_v2_probe(step.get("precondition"), "precondition")
            _validate_v2_probe(step.get("postcondition"), "postcondition")
            probe_ids.extend(
                [step["precondition"]["id"], step["postcondition"]["id"]]
            )
            audit_ids.extend(
                [step["precondition"]["id"], step["postcondition"]["id"]]
            )
            commands.extend(
                [step, step["precondition"], step["postcondition"]]
            )
        else:
            raise ReleaseProfileError("step_kind_invalid")
        ids.append(step_id)
    final_checks = profile.get("final_checks")
    if not isinstance(final_checks, list) or not 1 <= len(final_checks) <= 16:
        raise ReleaseProfileError("final_checks_invalid")
    for probe in final_checks:
        _validate_v2_probe(probe, "final_check")
        probe_ids.append(probe["id"])
        audit_ids.append(probe["id"])
        commands.append(probe)
    if (
        len(set(ids)) != len(ids)
        or len(set(probe_ids)) != len(probe_ids)
        or set(ids).intersection(probe_ids)
        or len(set(audit_ids)) != len(audit_ids)
    ):
        raise ReleaseProfileError("profile_id_duplicate")
    declared = set(names)
    used = set()
    for command in commands:
        for token in command["argv"]:
            used.update(PLACEHOLDER_RE.findall(token))
            if "{{" in token and PLACEHOLDER_RE.sub("", token).find("{{") >= 0:
                raise ReleaseProfileError("profile_placeholder_invalid")
    if not used.issubset(declared):
        raise ReleaseProfileError("profile_placeholder_undeclared")
    publication_targets = {
        row["name"] for row in declarations if row["publication_target"]
    }
    effect_targets = set()
    for step in steps:
        if step["kind"] == "effect":
            for token in step["argv"]:
                effect_targets.update(PLACEHOLDER_RE.findall(token))
    if not publication_targets.issubset(effect_targets):
        raise ReleaseProfileError("profile_publication_target_unused")
    return profile


def validate_profile(profile):
    if isinstance(profile, dict) and profile.get("schema_version") == 2:
        return _validate_v2_profile(profile)
    required = {
        "schema_version", "document_type", "id", "control_sources",
        "steps", "final_checks",
    }
    if not isinstance(profile, dict) or set(profile) != required:
        raise ReleaseProfileError("profile_shape_invalid")
    if profile.get("schema_version") != 1 or profile.get("document_type") != "release_profile":
        raise ReleaseProfileError("profile_version_invalid")
    if not isinstance(profile.get("id"), str) or ID_RE.fullmatch(profile["id"]) is None:
        raise ReleaseProfileError("profile_id_invalid")
    controls = profile.get("control_sources")
    if not isinstance(controls, list) or not 1 <= len(controls) <= 64:
        raise ReleaseProfileError("control_sources_invalid")
    control_paths = []
    for row in controls:
        if not isinstance(row, dict) or set(row) != {
            "path", "digest_mode", "sha256"
        }:
            raise ReleaseProfileError("control_source_shape_invalid")
        if not _safe_relative(row.get("path")) or SECRET_PATH_RE.search(row["path"]):
            raise ReleaseProfileError("control_source_path_invalid")
        if not isinstance(row.get("sha256"), str) or DIGEST_RE.fullmatch(row["sha256"]) is None:
            raise ReleaseProfileError("control_source_digest_invalid")
        if row.get("digest_mode") not in ("file", "package-scripts"):
            raise ReleaseProfileError("control_source_mode_invalid")
        control_paths.append(row["path"])
    if len(set(control_paths)) != len(control_paths):
        raise ReleaseProfileError("control_source_duplicate")

    steps = profile.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise ReleaseProfileError("steps_invalid")
    ids = []
    probe_ids = []
    audit_ids = []
    for step in steps:
        if not isinstance(step, dict):
            raise ReleaseProfileError("step_shape_invalid")
        kind = step.get("kind")
        step_id = step.get("id")
        if not isinstance(step_id, str) or ID_RE.fullmatch(step_id) is None:
            raise ReleaseProfileError("step_id_invalid")
        if kind == "check":
            if set(step) != {"id", "kind", "argv", "cwd", "timeout_seconds"}:
                raise ReleaseProfileError("check_step_shape_invalid")
            _validate_probe(
                {
                    "id": step_id,
                    "argv": step.get("argv"),
                    "cwd": step.get("cwd"),
                    "timeout_seconds": step.get("timeout_seconds"),
                },
                "check_step",
            )
            audit_ids.append(step_id)
        elif kind == "effect":
            if set(step) != {
                "id", "kind", "scope", "argv", "cwd", "timeout_seconds",
                "precondition", "postcondition",
            }:
                raise ReleaseProfileError("effect_step_shape_invalid")
            if step.get("scope") not in ("local", "remote"):
                raise ReleaseProfileError("effect_scope_invalid")
            if not _valid_cwd(step.get("cwd")):
                raise ReleaseProfileError("effect_cwd_invalid")
            timeout = step.get("timeout_seconds")
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
                raise ReleaseProfileError("effect_timeout_invalid")
            _validate_argv(step.get("argv"), "effect", probe=False)
            _validate_probe(step.get("precondition"), "precondition")
            _validate_probe(step.get("postcondition"), "postcondition")
            probe_ids.extend(
                [step["precondition"]["id"], step["postcondition"]["id"]]
            )
            audit_ids.extend(
                [step["precondition"]["id"], step["postcondition"]["id"]]
            )
        else:
            raise ReleaseProfileError("step_kind_invalid")
        ids.append(step_id)
    final_checks = profile.get("final_checks")
    if not isinstance(final_checks, list) or not 1 <= len(final_checks) <= 16:
        raise ReleaseProfileError("final_checks_invalid")
    for probe in final_checks:
        _validate_probe(probe, "final_check")
        probe_ids.append(probe["id"])
        audit_ids.append(probe["id"])
    if len(set(ids)) != len(ids) or len(set(probe_ids)) != len(probe_ids):
        raise ReleaseProfileError("profile_id_duplicate")
    if set(ids).intersection(probe_ids):
        raise ReleaseProfileError("profile_id_collision")
    if len(set(audit_ids)) != len(audit_ids):
        raise ReleaseProfileError("profile_probe_duplicate")
    return profile


def _control_set_digest(controls):
    return _value_sha(
        sorted(
            [
                {
                    "path": row["path"],
                    "digest_mode": row["digest_mode"],
                    "sha256": row["sha256"],
                }
                for row in controls
            ],
            key=lambda row: row["path"],
        )
    )


def _expected_probe_ids(profile):
    result = []
    for step in profile["steps"]:
        if step["kind"] == "check":
            result.append(step["id"])
        else:
            result.extend(
                [step["precondition"]["id"], step["postcondition"]["id"]]
            )
    result.extend(probe["id"] for probe in profile["final_checks"])
    return sorted(result)


def validate_audit(
    audit, profile, discovery, expected_failure_sha256=_AUDIT_FAILURE_ANY
):
    required = {
        "schema_version", "document_type", "profile_sha256",
        "discovery_sha256", "control_set_sha256", "verdict",
        "failure_sha256", "probe_attestations", "findings",
    }
    if not isinstance(audit, dict) or set(audit) != required:
        raise ReleaseProfileError("audit_shape_invalid")
    if (
        audit.get("schema_version") != 1
        or audit.get("document_type") != "release_audit"
        or audit.get("verdict") != "passed"
    ):
        raise ReleaseProfileError("audit_verdict_invalid")
    if audit.get("profile_sha256") != _value_sha(profile):
        raise ReleaseProfileError("audit_profile_mismatch")
    if audit.get("discovery_sha256") != _value_sha(discovery):
        raise ReleaseProfileError("audit_discovery_mismatch")
    if audit.get("control_set_sha256") != _control_set_digest(profile["control_sources"]):
        raise ReleaseProfileError("audit_control_mismatch")
    failure_sha256 = audit.get("failure_sha256")
    if (
        failure_sha256 is not None
        and (
            not isinstance(failure_sha256, str)
            or DIGEST_RE.fullmatch(failure_sha256) is None
        )
    ):
        raise ReleaseProfileError("audit_failure_invalid")
    if (
        expected_failure_sha256 is not _AUDIT_FAILURE_ANY
        and failure_sha256 != expected_failure_sha256
    ):
        raise ReleaseProfileError("audit_failure_mismatch")
    sources = {
        row["path"]: row
        for row in discovery.get("sources", [])
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    attestations = audit.get("probe_attestations")
    if not isinstance(attestations, list) or not 1 <= len(attestations) <= 80:
        raise ReleaseProfileError("audit_attestations_invalid")
    attested_ids = []
    for row in attestations:
        if not isinstance(row, dict) or set(row) != {
            "probe_id", "read_only", "evidence_path"
        }:
            raise ReleaseProfileError("audit_attestation_shape_invalid")
        if (
            not isinstance(row.get("probe_id"), str)
            or ID_RE.fullmatch(row["probe_id"]) is None
            or row.get("read_only") is not True
            or row.get("evidence_path") not in sources
        ):
            raise ReleaseProfileError("audit_attestation_invalid")
        attested_ids.append(row["probe_id"])
    if sorted(attested_ids) != _expected_probe_ids(profile):
        raise ReleaseProfileError("audit_probe_coverage_invalid")

    findings = audit.get("findings")
    if not isinstance(findings, list) or len(findings) > 64:
        raise ReleaseProfileError("audit_findings_invalid")
    finding_ids = []
    for row in findings:
        if not isinstance(row, dict) or set(row) != {
            "id", "severity", "evidence_path", "claim", "recommendation",
            "disposition",
        }:
            raise ReleaseProfileError("audit_finding_shape_invalid")
        if (
            not isinstance(row.get("id"), str)
            or ID_RE.fullmatch(row["id"]) is None
            or row.get("severity") not in ("high", "medium", "low")
            or row.get("evidence_path") not in sources
            or row.get("disposition") not in (
                "accepted", "deferred", "not_applicable"
            )
        ):
            raise ReleaseProfileError("audit_finding_invalid")
        _bounded_string(row.get("claim"), "audit_finding_claim", 1000)
        _bounded_string(
            row.get("recommendation"), "audit_finding_recommendation", 2000
        )
        finding_ids.append(row["id"])
    if len(set(finding_ids)) != len(finding_ids):
        raise ReleaseProfileError("audit_finding_duplicate")
    return audit


def _validate_discovery(discovery):
    if not isinstance(discovery, dict) or set(discovery) != {
        "schema_version", "document_type", "sources", "inventory_sha256"
    }:
        raise ReleaseProfileError("discovery_shape_invalid")
    if (
        discovery.get("schema_version") != 1
        or discovery.get("document_type") != "release_discovery"
    ):
        raise ReleaseProfileError("discovery_version_invalid")
    sources = discovery.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_DISCOVERY_FILES:
        raise ReleaseProfileError("discovery_sources_invalid")
    paths = []
    total = 0
    for row in sources:
        if not isinstance(row, dict) or set(row) != {
            "path", "sha256", "control_mode", "control_sha256", "bytes",
            "kind", "role",
        }:
            raise ReleaseProfileError("discovery_source_shape_invalid")
        if (
            not _safe_relative(row.get("path"))
            or not isinstance(row.get("sha256"), str)
            or DIGEST_RE.fullmatch(row["sha256"]) is None
            or isinstance(row.get("bytes"), bool)
            or not isinstance(row.get("bytes"), int)
            or not 0 <= row["bytes"] <= MAX_SOURCE_BYTES
            or row.get("kind") not in (
                "workflow", "script", "configuration", "manifest",
                "documentation", "explicit",
            )
            or row.get("role") not in ("control_candidate", "audit_context")
        ):
            raise ReleaseProfileError("discovery_source_invalid")
        control_mode = row.get("control_mode")
        control_sha256 = row.get("control_sha256")
        if row["role"] == "audit_context":
            if control_mode != "none" or control_sha256 is not None:
                raise ReleaseProfileError("discovery_control_invalid")
        elif (
            control_mode not in ("file", "package-scripts")
            or not isinstance(control_sha256, str)
            or DIGEST_RE.fullmatch(control_sha256) is None
            or (control_mode == "file" and control_sha256 != row["sha256"])
            or (
                control_mode == "package-scripts"
                and posixpath.basename(row["path"]).lower() != "package.json"
            )
        ):
            raise ReleaseProfileError("discovery_control_invalid")
        paths.append(row["path"])
        total += row["bytes"]
    if (
        len(set(paths)) != len(paths)
        or total > MAX_DISCOVERY_BYTES
        or discovery.get("inventory_sha256") != _value_sha(sources)
    ):
        raise ReleaseProfileError("discovery_inventory_invalid")
    return discovery


def _verify_discovery_current(root, discovery):
    _validate_discovery(discovery)
    tracked = set(_tracked_files(root))
    for row in discovery["sources"]:
        if row["path"] not in tracked:
            raise ReleaseProfileError("discovery_source_untracked")
        payload = _stable_source(root, row["path"])
        if len(payload) != row["bytes"] or _sha(payload) != row["sha256"]:
            raise ReleaseProfileError("discovery_stale")


def _profile_commands(profile):
    for step in profile["steps"]:
        if step["kind"] == "check":
            yield step
        else:
            yield step["precondition"]
            yield step
            yield step["postcondition"]
    yield from profile["final_checks"]


def _interpreter_script(argv):
    normalized = _unwrap_env(argv)
    if not normalized:
        return None
    executable = _interpreter_kind(normalized[0])
    if executable is None:
        return None
    args = normalized[1:]
    if _loader_construct_forbidden(normalized):
        raise ReleaseProfileError("command_loader_unsupported")
    options_with_value = {
        "-L", "-W", "-X", "--conditions",
    }
    if executable == "python":
        options_with_value = {"-W", "-X"}
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--":
            return args[index + 1] if index + 1 < len(args) else None
        if item in options_with_value:
            index += 2
            continue
        if item.startswith("-"):
            index += 1
            continue
        return item
    return None


def _command_input_path(root, cwd, value, required, allow_absolute=False):
    if not isinstance(value, str) or not value:
        raise ReleaseProfileError("command_local_input_unsafe")
    if posixpath.isabs(value):
        if not allow_absolute:
            raise ReleaseProfileError("command_local_input_unsafe")
        root = os.path.realpath(root)
        resolved = os.path.realpath(value)
        try:
            inside = os.path.commonpath((root, resolved)) == root
        except ValueError:
            inside = False
        if not inside:
            if required:
                raise ReleaseProfileError("command_local_input_unsafe")
            return None
        relative = os.path.relpath(resolved, root).replace(os.sep, "/")
        if not _safe_relative(relative):
            raise ReleaseProfileError("command_local_input_unsafe")
        if os.path.lexists(resolved):
            return relative
        if required:
            raise ReleaseProfileError("command_local_input_missing")
        return None
    base = "" if cwd == "." else cwd
    relative = posixpath.normpath(posixpath.join(base, value))
    if not _safe_relative(relative):
        raise ReleaseProfileError("command_local_input_unsafe")
    full = os.path.join(root, *relative.split("/"))
    if os.path.lexists(full):
        return relative
    if required:
        raise ReleaseProfileError("command_local_input_missing")
    return None


def _direct_local_inputs(root, command, allow_absolute=False):
    normalized = _unwrap_env(command["argv"])
    if not normalized:
        raise ReleaseProfileError("command_wrapper_invalid")
    candidates = []
    executable = normalized[0]
    if "/" in executable:
        path = _command_input_path(
            root,
            command["cwd"],
            executable,
            required=True,
            allow_absolute=allow_absolute,
        )
        if path is not None:
            candidates.append(path)
    else:
        command_cwd = _safe_cwd(root, command["cwd"])
        path_entries = []
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
            entry = entry or command_cwd
            if not os.path.isabs(entry):
                entry = os.path.join(command_cwd, entry)
            path_entries.append(entry)
        resolved = shutil.which(
            executable, path=os.pathsep.join(path_entries)
        )
        if resolved is not None:
            resolved = os.path.realpath(resolved)
            try:
                inside = os.path.commonpath((root, resolved)) == root
            except ValueError:
                inside = False
            if inside:
                relative = os.path.relpath(resolved, root).replace(
                    os.sep, "/"
                )
                if not _safe_relative(relative):
                    raise ReleaseProfileError("command_local_input_unsafe")
                candidates.append(relative)
    script = _interpreter_script(normalized)
    if script is not None:
        path = _command_input_path(
            root,
            command["cwd"],
            script,
            required=True,
            allow_absolute=allow_absolute,
        )
        if path is not None:
            candidates.append(path)
    return sorted(set(candidates))


def _package_manifest_path(root, command):
    normalized = _unwrap_env(command["argv"])
    if not normalized:
        return None
    executable = posixpath.basename(normalized[0]).lower()
    if executable not in {
        "bun", "npm", "pnpm", "yarn",
    }:
        return None
    if _package_context_changes(normalized):
        raise ReleaseProfileError("package_command_dynamic_context")
    command_cwd = _safe_cwd(root, command["cwd"])
    current = command_cwd
    while True:
        manifest = os.path.realpath(os.path.join(current, "package.json"))
        try:
            inside = os.path.commonpath((root, manifest)) == root
        except ValueError:
            inside = False
        if not inside:
            raise ReleaseProfileError("package_command_dynamic_context")
        if os.path.isfile(manifest):
            relative = os.path.relpath(manifest, root).replace(os.sep, "/")
            if not _safe_relative(relative):
                raise ReleaseProfileError("package_command_dynamic_context")
            return relative
        if current == root:
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _package_context_changes(argv):
    if not argv:
        return False
    executable = posixpath.basename(argv[0]).lower()
    for item in argv[1:]:
        if item in (
            "-C", "-F", "-w", "--cwd", "--dir", "--filter", "--prefix",
            "--workspace", "--workspaces",
        ):
            return True
        if item.startswith(
            (
                "--cwd=", "--dir=", "--filter=", "--prefix=",
                "--workspace=",
            )
        ):
            return True
    if executable == "yarn" and any(
        item in ("workspace", "workspaces") for item in argv[1:]
    ):
        return True
    return False


def _package_script_names_argv(argv):
    normalized = _unwrap_env(argv)
    if not normalized:
        return None
    executable = posixpath.basename(normalized[0]).lower()
    args = normalized[1:]
    command_index = next(
        (index for index, item in enumerate(args) if not item.startswith("-")),
        None,
    )
    if command_index is None:
        return None
    command_name = args[command_index]
    trailing = args[command_index + 1:]
    run_aliases = {"r", "run", "run-script", "rum", "urn"}
    if command_name in run_aliases:
        name = trailing[0] if trailing and not trailing[0].startswith("-") else None
    elif executable in {"bun", "yarn"}:
        if command_name in {
            "add", "config", "exec", "install", "link", "npm", "publish",
            "remove", "set", "upgrade", "why",
        }:
            return None
        name = command_name
    elif executable in {"npm", "pnpm"}:
        aliases = {
            "t": "test",
            "test": "test",
            "tst": "test",
        }
        name = aliases.get(command_name, command_name)
        if name not in {
            "pack", "publish", "test", "version",
        }:
            return None
    else:
        return None
    if not isinstance(name, str) or not name:
        return None
    if name == "publish":
        return [
            "prepublishOnly", "prepack", "prepare", "postpack", "publish",
            "postpublish",
        ]
    if name == "pack":
        return ["prepack", "prepare", "postpack"]
    if name == "version":
        return ["preversion", "version", "postversion"]
    return ["pre" + name, name, "post" + name]


def _package_script_names(command):
    return _package_script_names_argv(command["argv"])


def _package_requested_script(argv):
    normalized = _unwrap_env(argv)
    if not normalized:
        return None
    args = normalized[1:]
    command_index = next(
        (index for index, item in enumerate(args) if not item.startswith("-")),
        None,
    )
    if command_index is None or args[command_index] not in {
        "r", "run", "run-script", "rum", "urn",
    }:
        return None
    trailing = args[command_index + 1:]
    return trailing[0] if trailing and not trailing[0].startswith("-") else None


def _package_local_inputs(root, package_path, command):
    payload = _stable_source(root, package_path)
    try:
        package = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicates
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseProfileError("package_control_malformed") from exc
    scripts = package.get("scripts", {}) if isinstance(package, dict) else None
    if not isinstance(scripts, dict):
        return []
    package_dir = posixpath.dirname(package_path)
    result = set()
    selected = _package_script_names(command)
    if selected is None:
        raise ReleaseProfileError("package_command_unsupported")
    requested = _package_requested_script(command["argv"])
    if requested is not None and not isinstance(scripts.get(requested), str):
        raise ReleaseProfileError("package_command_unsupported")
    visited = set()

    def visit(name, stack):
        if name in stack:
            raise ReleaseProfileError("package_script_dynamic_input")
        if name in visited:
            return
        visited.add(name)
        script = scripts.get(name)
        if not isinstance(script, str):
            return
        dynamic_markers = ("$", "`", "*", "?", "[", "]", "{", "}")
        if any(marker in script for marker in dynamic_markers):
            raise ReleaseProfileError("package_script_dynamic_input")
        try:
            lexer = shlex.shlex(
                script, posix=True, punctuation_chars="();<>|&"
            )
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError as exc:
            raise ReleaseProfileError("package_script_malformed") from exc
        if any(
            token == "cd" or token.upper().startswith("PATH=")
            for token in tokens
        ):
            raise ReleaseProfileError("package_script_dynamic_input")
        if _loader_construct_forbidden(tokens):
            raise ReleaseProfileError("package_script_dynamic_input")
        for index, token in enumerate(tokens):
            if posixpath.basename(token).lower() in DYNAMIC_EXECUTION_WRAPPERS:
                raise ReleaseProfileError("package_script_dynamic_input")
            if (
                posixpath.basename(token).lower()
                in {"bun", "npm", "pnpm", "yarn"}
                and _package_context_changes(tokens[index:])
            ):
                raise ReleaseProfileError("package_script_dynamic_input")
        punctuation = {"(", ")", ";", "<", ">", "|", "||", "&", "&&"}
        for index, token in enumerate(tokens):
            if posixpath.basename(token).lower() not in {
                "bun", "npm", "pnpm", "yarn",
            }:
                continue
            end = index + 1
            while end < len(tokens) and tokens[end] not in punctuation:
                end += 1
            nested = _package_script_names_argv(tokens[index:end])
            if nested is None:
                raise ReleaseProfileError("package_script_dynamic_input")
            for nested_name in nested:
                visit(nested_name, stack + (name,))
        for token in tokens:
            candidate = posixpath.normpath(token)
            if candidate.startswith("./"):
                candidate = candidate[2:]
            if not _safe_relative(candidate):
                continue
            relative = (
                candidate
                if not package_dir
                else posixpath.normpath(package_dir + "/" + candidate)
            )
            if not _safe_relative(relative):
                continue
            full = os.path.join(root, *relative.split("/"))
            if os.path.isfile(full):
                result.add(relative)

    for name in selected:
        visit(name, ())
    return sorted(result)


def _validate_command_bindings(root, profile, discovery):
    tracked = set(_tracked_files(root))
    controls = {
        row["path"]: row["digest_mode"] for row in profile["control_sources"]
    }
    discovered = {row["path"]: row for row in discovery["sources"]}
    for command in _profile_commands(profile):
        for path in _direct_local_inputs(
            root,
            command,
            allow_absolute=profile["schema_version"] == 2,
        ):
            if path not in tracked:
                raise ReleaseProfileError("command_local_input_untracked")
            if controls.get(path) != "file":
                raise ReleaseProfileError("command_control_unbound")
        package_path = _package_manifest_path(root, command)
        if package_path is None:
            continue
        source = discovered.get(package_path)
        if (
            source is None
            or source.get("control_mode") != "package-scripts"
            or controls.get(package_path) != "package-scripts"
        ):
            raise ReleaseProfileError("package_control_unbound")
        for path in _package_local_inputs(root, package_path, command):
            if path not in tracked:
                raise ReleaseProfileError("command_local_input_untracked")
            if controls.get(path) != "file":
                raise ReleaseProfileError("command_control_unbound")


def validate_candidate(root, candidate_path, audit_path):
    root = workspace_root(root)
    paths = _state_paths(root)
    discovery = _read_json(paths["discovery"], "discovery")
    _verify_discovery_current(root, discovery)
    profile = validate_profile(_read_json(candidate_path, "candidate"))
    source_rows = {row["path"]: row for row in discovery["sources"]}
    for row in profile["control_sources"]:
        source = source_rows.get(row["path"])
        if (
            source is None
            or source["role"] != "control_candidate"
            or source["control_mode"] != row["digest_mode"]
            or source["control_sha256"] != row["sha256"]
        ):
            raise ReleaseProfileError("profile_control_not_discovered")
    _validate_command_bindings(root, profile, discovery)
    failure = _load_optional(_state_paths(root)["failure"], "failure")
    failure_sha256 = (
        None
        if failure is None
        else _value_sha(
            _validate_failure_for_adoption(root, failure, profile)
        )
    )
    audit = validate_audit(
        _read_json(audit_path, "audit"),
        profile,
        discovery,
        expected_failure_sha256=failure_sha256,
    )
    return profile, audit, discovery


def _command_tool_fingerprints(root, command):
    try:
        environment = release_memory.sealed_environment(os.environ)
        return release_memory.tool_fingerprints(
            command["argv"],
            environment,
            cwd=_safe_cwd(root, command["cwd"]),
        )
    except release_memory.ReleaseMemoryError as exc:
        raise ReleaseProfileError("profile_tool_unavailable") from exc


def _profile_tool_fingerprints(root, profile):
    try:
        result = {}
        for command in _profile_commands(profile):
            try:
                result[command["id"]] = _command_tool_fingerprints(
                    root, command
                )
            except ReleaseProfileError:
                if any(
                    isinstance(item, str) and PLACEHOLDER_RE.search(item)
                    for item in command["argv"]
                ):
                    continue
                raise
        if profile.get("identity", {}).get("provider") == "github":
            result["identity:github"] = _command_tool_fingerprints(
                root,
                {"argv": ["gh"], "cwd": "."},
            )
        return result
    except ReleaseProfileError:
        raise


def _bundle(
    root,
    profile,
    audit,
    discovery,
    failure=None,
    prior_failure_audits=None,
):
    value = {
        "schema_version": 1,
        "document_type": "adopted_release_profile",
        "profile_sha256": _value_sha(profile),
        "audit_sha256": _value_sha(audit),
        "discovery_sha256": _value_sha(discovery),
        "control_set_sha256": _control_set_digest(profile["control_sources"]),
        "profile": profile,
        "audit": audit,
        "discovery": discovery,
    }
    if profile["schema_version"] == 2:
        value["tool_sha256"] = _profile_tool_fingerprints(root, profile)
        history = list(prior_failure_audits or [])
        if failure is not None:
            history.append({"failure": failure, "audit": audit})
        unique = {}
        for row in history:
            unique[_value_sha(row)] = row
        if len(unique) > 128:
            raise ReleaseProfileError("failure_audit_history_full")
        value["failure_audits"] = list(unique.values())
    return value


def _load_bundle(root):
    value = _read_json(_state_paths(root)["profile"], "adopted_profile")
    base_keys = {
        "schema_version", "document_type", "profile_sha256", "audit_sha256",
        "discovery_sha256", "control_set_sha256", "profile", "audit",
        "discovery",
    }
    if (
        not isinstance(value, dict)
        or set(value) not in (
            base_keys,
            base_keys | {"tool_sha256", "failure_audits"},
        )
    ):
        raise ReleaseProfileError("adopted_profile_shape_invalid")
    if (
        value.get("schema_version") != 1
        or value.get("document_type") != "adopted_release_profile"
    ):
        raise ReleaseProfileError("adopted_profile_version_invalid")
    profile = validate_profile(value.get("profile"))
    if (
        profile["schema_version"] == 2
        and (
            set(value)
            != base_keys | {"tool_sha256", "failure_audits"}
            or value.get("tool_sha256")
            != _profile_tool_fingerprints(root, profile)
        )
    ):
        raise ReleaseProfileError("adopted_tool_drift")
    if profile["schema_version"] == 1 and set(value) != base_keys:
        raise ReleaseProfileError("adopted_profile_shape_invalid")
    if value.get("profile_sha256") != _value_sha(profile):
        raise ReleaseProfileError("adopted_profile_digest_invalid")
    if value.get("control_set_sha256") != _control_set_digest(profile["control_sources"]):
        raise ReleaseProfileError("adopted_control_digest_invalid")
    discovery = _validate_discovery(value.get("discovery"))
    if value.get("discovery_sha256") != _value_sha(discovery):
        raise ReleaseProfileError("adopted_discovery_digest_invalid")
    audit = validate_audit(value.get("audit"), profile, discovery)
    if value.get("audit_sha256") != _value_sha(audit):
        raise ReleaseProfileError("adopted_audit_digest_invalid")
    if profile["schema_version"] == 2:
        history = value.get("failure_audits")
        if (
            not isinstance(history, list)
            or len(history) > 128
            or any(
                not isinstance(row, dict)
                or set(row) != {"failure", "audit"}
                for row in history
            )
        ):
            raise ReleaseProfileError("failure_audit_history_invalid")
        digests = []
        valid_step_ids = {
            row["id"]
            for row in profile["steps"] + profile["final_checks"]
        }
        for row in history:
            failure = _validate_failure_for_profile(
                row["failure"], profile
            )
            if (
                failure["profile_sha256"] != value["profile_sha256"]
                or failure["step_id"] not in valid_step_ids
            ):
                raise ReleaseProfileError(
                    "failure_audit_history_invalid"
                )
            historical_audit = validate_audit(
                row["audit"],
                profile,
                discovery,
                expected_failure_sha256=_value_sha(failure),
            )
            if historical_audit["failure_sha256"] is None:
                raise ReleaseProfileError(
                    "failure_audit_history_invalid"
                )
            digests.append(_value_sha(row))
        if len(digests) != len(set(digests)):
            raise ReleaseProfileError("failure_audit_history_invalid")
    _validate_command_bindings(root, profile, discovery)
    return value


def _require_profile_tools_current(root, bundle):
    if (
        bundle["profile"]["schema_version"] == 2
        and bundle["tool_sha256"]
        != _profile_tool_fingerprints(root, bundle["profile"])
    ):
        raise ReleaseProfileError("adopted_tool_drift")


def _require_command_tools_current(root, command, expected):
    if _command_tool_fingerprints(root, command) != expected:
        raise ReleaseProfileError("adopted_tool_drift")


def _controls_current(root, profile):
    tracked = set(_tracked_files(root))
    for row in profile["control_sources"]:
        if row["path"] not in tracked:
            return False
        try:
            payload = _stable_source(root, row["path"])
            if row["digest_mode"] == "file":
                digest = _sha(payload)
            elif row["digest_mode"] == "package-scripts":
                digest = _package_scripts_digest(payload)
            else:
                return False
            if digest != row["sha256"]:
                return False
        except ReleaseProfileError:
            return False
    return True


def _load_optional(path, label):
    if not os.path.exists(path):
        return None
    return _read_json(path, label)


def _validate_failure(value):
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "event_id", "profile_sha256", "step_id",
        "evidence",
    }:
        raise ReleaseProfileError("failure_shape_invalid")
    if (
        value.get("schema_version") != 1
        or not isinstance(value.get("event_id"), str)
        or re.fullmatch(r"[0-9a-f]{32}", value["event_id"]) is None
        or not isinstance(value.get("profile_sha256"), str)
        or DIGEST_RE.fullmatch(value["profile_sha256"]) is None
        or not isinstance(value.get("step_id"), str)
        or ID_RE.fullmatch(value["step_id"]) is None
        or not isinstance(value.get("evidence"), dict)
    ):
        raise ReleaseProfileError("failure_contract_invalid")
    return value


def _validate_failure_for_profile(value, profile):
    failure = _validate_failure(value)
    if (
        profile["schema_version"] == 2
        and not _validate_v2_evidence(failure["evidence"])
    ):
        raise ReleaseProfileError("failure_evidence_invalid")
    return failure


def _validate_failure_for_adoption(root, value, profile):
    failure = _validate_failure(value)
    if failure["profile_sha256"] == _value_sha(profile):
        return _validate_failure_for_profile(failure, profile)
    try:
        current = _load_bundle(root)
    except ReleaseProfileError as exc:
        raise ReleaseProfileError("failure_profile_invalid") from exc
    if failure["profile_sha256"] != current["profile_sha256"]:
        raise ReleaseProfileError("failure_profile_invalid")
    return _validate_failure_for_profile(
        failure, current["profile"]
    )


def status(root, prefer_v2=False):
    root = workspace_root(root)
    paths = _state_paths(root)
    if not os.path.exists(paths["profile"]):
        return {"schema_version": 1, "status": "import_required"}
    try:
        bundle = _load_bundle(root)
    except ReleaseProfileError as exc:
        return {
            "schema_version": 1,
            "status": "invalid",
            "reason": exc.code,
        }
    if not _controls_current(root, bundle["profile"]):
        return {
            "schema_version": 1,
            "status": "audit_required",
            "reason": "control_drift",
            "profile_sha256": bundle["profile_sha256"],
        }
    failure = _load_optional(paths["failure"], "failure")
    if failure is not None:
        failure = _validate_failure_for_profile(
            failure, bundle["profile"]
        )
        return {
            "schema_version": 1,
            "status": "audit_required",
            "reason": "release_failure",
            "profile_sha256": bundle["profile_sha256"],
            "failure_sha256": _value_sha(failure),
        }
    result = {
        "schema_version": 1,
        "status": "ready",
        "profile_sha256": bundle["profile_sha256"],
        "control_set_sha256": bundle["control_set_sha256"],
        "profile_schema_version": bundle["profile"]["schema_version"],
        "migration_status": (
            "current"
            if bundle["profile"]["schema_version"] == 2
            else "v2_available"
        ),
    }
    receipt = _load_optional(paths["run"], "run")
    if receipt is not None:
        try:
            _validate_run(receipt, bundle)
        except ReleaseProfileError as exc:
            if _superseded_run(receipt, bundle):
                receipt = None
            else:
                return {
                    "schema_version": 1,
                    "status": "invalid",
                    "reason": exc.code,
                }
    if receipt is not None:
        if receipt["status"] == "audit_required":
            return {
                "schema_version": 1,
                "status": "invalid",
                "reason": "failure_evidence_missing",
            }
        result["run_status"] = receipt["status"]
        result["generation"] = receipt["generation"]
    else:
        result["run_status"] = "none"
        result["generation"] = 0
    if (
        prefer_v2
        and bundle["profile"]["schema_version"] == 1
        and result["run_status"] in ("none", "completed")
    ):
        result["status"] = "upgrade_required"
        result["reason"] = "profile_v2_migration"
    elif (
        prefer_v2
        and bundle["profile"]["schema_version"] == 1
    ):
        result["migration_status"] = "deferred_active_v1"
    return result


def _run_has_started_effect(receipt):
    return any(
        row.get("kind") == "effect"
        and row.get("status") in ("started", "effect_failed", "completed")
        for row in receipt.get("steps", [])
    )


def _completed_run_self_valid(receipt):
    if (
        not isinstance(receipt, dict)
        or receipt.get("status") != "completed"
        or not isinstance(receipt.get("steps"), list)
        or not isinstance(receipt.get("final_checks"), list)
        or any(
            not isinstance(row, dict) or row.get("status") != "completed"
            for row in receipt["steps"] + receipt["final_checks"]
        )
    ):
        return False
    try:
        if receipt.get("schema_version") == 1:
            basis = _completion_basis(receipt)
        elif receipt.get("schema_version") == 2:
            basis = {
                key: receipt[key]
                for key in (
                    "profile_sha256", "resolved_profile_sha256",
                    "input_sha256", "control_set_sha256", "binding", "head",
                    "generation", "identity", "steps", "final_checks",
                )
            }
        else:
            return False
    except (KeyError, TypeError):
        return False
    return receipt.get("completion_sha256") == _value_sha(basis)


def _superseded_run(receipt, bundle):
    if (
        not isinstance(receipt, dict)
        or receipt.get("profile_sha256") == bundle["profile_sha256"]
        or DIGEST_RE.fullmatch(receipt.get("profile_sha256", "")) is None
        or isinstance(receipt.get("generation"), bool)
        or not isinstance(receipt.get("generation"), int)
        or receipt["generation"] < 1
    ):
        return False
    return _completed_run_self_valid(receipt) or (
        receipt.get("status") in (
            "active", "retryable_failure", "audit_required",
        )
        and not _run_has_started_effect(receipt)
    )


def adopt(root, candidate_path, audit_path, write=False):
    root = workspace_root(root)
    if not write:
        profile, audit, discovery = validate_candidate(
            root, candidate_path, audit_path
        )
        return {
            "schema_version": 1,
            "status": "valid",
            "profile_sha256": _value_sha(profile),
            "audit_sha256": _value_sha(audit),
            "discovery_sha256": _value_sha(discovery),
        }
    with _release_lock(root):
        paths = _state_paths(root)
        if (
            os.path.exists(paths["profile"])
            and not os.path.exists(paths["failure"])
        ):
            try:
                current_bundle = _load_bundle(root)
                candidate = _read_json(candidate_path, "candidate")
                candidate_audit = _read_json(audit_path, "audit")
                existing_run = _load_optional(paths["run"], "run")
                if existing_run is not None:
                    _validate_run(existing_run, current_bundle)
                if (
                    candidate == current_bundle["profile"]
                    and candidate_audit == current_bundle["audit"]
                ):
                    _verify_discovery_current(
                        root, current_bundle["discovery"]
                    )
                    if not _controls_current(
                        root, current_bundle["profile"]
                    ):
                        raise ReleaseProfileError("control_drift")
                    return {
                        "schema_version": 1,
                        "status": "adopted",
                        "profile_sha256": current_bundle["profile_sha256"],
                        "audit_sha256": current_bundle["audit_sha256"],
                        "finding_count": len(
                            current_bundle["audit"]["findings"]
                        ),
                    }
            except ReleaseProfileError:
                pass
        profile, audit, discovery = validate_candidate(
            root, candidate_path, audit_path
        )
        current_failure = _load_optional(paths["failure"], "failure")
        if current_failure is not None:
            current_failure = _validate_failure_for_adoption(
                root, current_failure, profile
            )
        prior_failure_audits = []
        if os.path.exists(paths["profile"]):
            try:
                prior_bundle = _load_bundle(root)
                if (
                    prior_bundle["profile_sha256"]
                    == _value_sha(profile)
                ):
                    prior_failure_audits = prior_bundle.get(
                        "failure_audits", []
                    )
            except ReleaseProfileError:
                pass
        new_bundle = _bundle(
            root,
            profile,
            audit,
            discovery,
            failure=(
                current_failure
                if (
                    current_failure is not None
                    and current_failure["profile_sha256"]
                    == _value_sha(profile)
                )
                else None
            ),
            prior_failure_audits=prior_failure_audits,
        )
        existing_run = _load_optional(paths["run"], "run")
        if existing_run is not None:
            try:
                current_bundle = _load_bundle(root)
            except ReleaseProfileError as exc:
                raise ReleaseProfileError("existing_run_invalid") from exc
            try:
                _validate_run(existing_run, current_bundle)
            except ReleaseProfileError as exc:
                if not _superseded_run(existing_run, current_bundle):
                    raise ReleaseProfileError(
                        "existing_run_invalid"
                    ) from exc
            if (
                existing_run["status"] != "completed"
                and existing_run["profile_sha256"] != new_bundle["profile_sha256"]
                and _run_has_started_effect(existing_run)
            ):
                raise ReleaseProfileError("uncertain_run_profile_conflict")
        replace_run = (
            existing_run is not None
            and existing_run["profile_sha256"] != new_bundle["profile_sha256"]
        )
        _persist_local(
            paths["profile"], new_bundle, "profile_persist_failed"
        )
        if replace_run:
            _durable_unlink(paths["run"], "run_retire_failed")
            existing_run = None
        if existing_run is not None:
            changed = False
            for row in existing_run.get("steps", []) + existing_run.get("final_checks", []):
                if row.get("status") in ("failed", "precondition_failed"):
                    row["status"] = "pending"
                    row["evidence"] = None
                    if existing_run.get("schema_version") == 2:
                        row["failure_class"] = None
                        row["failure_audit_sha256s"] = []
                        row["resume_context_sha256"] = None
                    changed = True
                elif row.get("status") == "effect_failed":
                    row["status"] = "completed"
                    changed = True
            if existing_run.get("status") == "audit_required":
                if (
                    existing_run.get("schema_version") == 2
                    and current_failure is not None
                ):
                    matching = [
                        row
                        for row in existing_run.get("steps", [])
                        + existing_run.get("final_checks", [])
                        if row.get("id") == current_failure["step_id"]
                    ]
                    if (
                        len(matching) == 1
                        and matching[0].get("status") == "started"
                    ):
                        marker = _value_sha(
                            {
                                "failure": current_failure,
                                "audit": audit,
                            }
                        )
                        if marker not in matching[0][
                            "failure_audit_sha256s"
                        ]:
                            matching[0][
                                "failure_audit_sha256s"
                            ].append(marker)
                existing_run["status"] = "active"
                changed = True
            if changed:
                _persist_run(root, existing_run)
        if os.path.exists(paths["failure"]):
            _durable_unlink(
                paths["failure"], "failure_retire_failed"
            )
        return {
            "schema_version": 1,
            "status": "adopted",
            "profile_sha256": new_bundle["profile_sha256"],
            "audit_sha256": _value_sha(audit),
            "finding_count": len(audit["findings"]),
        }


def _command_digest(command):
    basis = {
        "argv": command["argv"],
        "cwd": command["cwd"],
        "timeout_seconds": command["timeout_seconds"],
    }
    if "policy" in command:
        basis["policy"] = command["policy"]
    return _value_sha(basis)


def _parse_inputs(items):
    result = {}
    for item in items or ():
        if not isinstance(item, str) or "=" not in item:
            raise ReleaseProfileError("input_shape_invalid")
        name, value = item.split("=", 1)
        if name in result:
            raise ReleaseProfileError("input_duplicate")
        result[name] = value
    return result


def _validate_input_value(kind, value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or "\x00" in value
        or "\n" in value
        or SECRET_VALUE_RE.search(value)
        or (
            kind != "git_oid"
            and HIGH_ENTROPY_INPUT_RE.fullmatch(value)
        )
    ):
        return False
    if kind == "git_oid":
        return re.fullmatch(r"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?", value) is not None
    if kind == "tag":
        return (
            len(value) <= 255
            and not value.startswith(("-", "."))
            and not value.endswith((".", "/"))
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+~-]*", value) is not None
            and ".." not in value
            and "@{" not in value
        )
    if kind == "semver":
        return re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\."
            r"(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?",
            value,
        ) is not None
    if kind == "repository":
        return (
            release_memory.canonical_github_repository(value) is not None
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*"
                r"(?:/[A-Za-z0-9][A-Za-z0-9_.-]*){1,7}",
                value,
            )
            is not None
        )
    if kind == "relative_path":
        return _safe_relative(value) and SECRET_PATH_RE.search(value) is None
    return False


def _relative_input_path_safe(root, value):
    if not _safe_relative(value) or SECRET_PATH_RE.search(value):
        return False
    root = os.path.realpath(root)
    current = root
    for part in value.split("/"):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode):
            return False
    try:
        if os.path.commonpath((root, os.path.realpath(current))) != root:
            return False
    except ValueError:
        return False
    if not os.path.isdir(current):
        return True
    entries = 0
    try:
        for directory, directories, files in os.walk(
            current, followlinks=False
        ):
            for name in directories + files:
                entries += 1
                if entries > MAX_RELATIVE_INPUT_ENTRIES:
                    return False
                info = os.lstat(os.path.join(directory, name))
                if stat.S_ISLNK(info.st_mode):
                    return False
    except OSError:
        return False
    return True


def _require_relative_inputs_current(root, profile, inputs):
    for row in profile["inputs"]:
        if (
            row["type"] == "relative_path"
            and not _relative_input_path_safe(root, inputs[row["name"]])
        ):
            raise ReleaseProfileError("input_path_drift")


def _artifact_stat(info):
    return (
        info.st_mode,
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _effect_artifact_roots(root, profile, inputs, effect):
    source_effects = [
        step
        for step in profile["steps"]
        if step["kind"] == "effect" and step["id"] == effect["id"]
    ]
    if len(source_effects) != 1:
        raise ReleaseProfileError("release_artifact_contract_invalid")
    source_effect = source_effects[0]
    relative_names = {
        row["name"]
        for row in profile["inputs"]
        if row["type"] == "relative_path"
    }
    paths = {
            inputs[name]
            for name in relative_names
            if any(
                "{{" + name + "}}" in token
                for token in source_effect["argv"]
            )
        }
    paths.update(effect["policy"]["affected_paths"])
    normalized = _unwrap_env(effect["argv"])
    ignored = {normalized[0]} if normalized else set()
    script = _interpreter_script(normalized)
    if script is not None:
        ignored.add(script)
    cwd = _safe_cwd(root, effect["cwd"])
    for token in normalized[1:] if normalized else ():
        candidates = [token]
        if "=" in token and token.startswith("-"):
            candidates.append(token.split("=", 1)[1])
        for candidate in candidates:
            candidate = candidate[1:] if candidate.startswith("@") else candidate
            if (
                not candidate
                or candidate in ignored
                or "://" in candidate
                or "\x00" in candidate
            ):
                continue
            lexical = os.path.abspath(
                candidate
                if os.path.isabs(candidate)
                else os.path.join(cwd, candidate)
            )
            if not os.path.lexists(lexical):
                continue
            try:
                inside = os.path.commonpath((root, lexical)) == root
            except ValueError:
                inside = False
            if not inside:
                raise ReleaseProfileError("release_artifact_unsafe")
            relative = os.path.relpath(lexical, root).replace(os.sep, "/")
            if (
                relative != "."
                and not _relative_input_path_safe(root, relative)
            ):
                raise ReleaseProfileError("release_artifact_unsafe")
            full = os.path.realpath(lexical)
            try:
                canonical_inside = os.path.commonpath((root, full)) == root
            except ValueError:
                canonical_inside = False
            if not canonical_inside:
                raise ReleaseProfileError("release_artifact_unsafe")
            if relative == "." or _safe_relative(relative):
                paths.add(relative)
    return sorted(paths, key=lambda value: (value.count("/"), value))


def _release_artifact_entries(root, profile, inputs, effect):
    roots = _effect_artifact_roots(root, profile, inputs, effect)
    selected = []
    for relative in roots:
        if any(
            parent == "."
            or relative == parent
            or relative.startswith(parent + "/")
            for parent in selected
        ):
            continue
        selected.append(relative)
    entries_by_path = {}
    entries = 0
    for relative in selected:
        full = (
            root
            if relative == "."
            else os.path.join(root, *relative.split("/"))
        )
        try:
            info = os.lstat(full)
        except OSError as exc:
            raise ReleaseProfileError(
                "release_artifact_unavailable"
            ) from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or SECRET_PATH_RE.search(relative)
            or relative == ".kimiflow"
            or relative.startswith((".git/", ".kimiflow/"))
        ):
            raise ReleaseProfileError("release_artifact_unsafe")
        if stat.S_ISREG(info.st_mode):
            entries_by_path[relative] = (full, "file")
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ReleaseProfileError("release_artifact_unsafe")
        entries_by_path[relative] = (full, "directory")
        for directory, directories, names in os.walk(
            full, followlinks=False
        ):
            directories.sort()
            names.sort()
            for name in directories:
                descendant = os.path.join(directory, name)
                descendant_info = os.lstat(descendant)
                entries += 1
                descendant_relative = os.path.relpath(
                    descendant, root
                ).replace(os.sep, "/")
                if (
                    entries > MAX_RELATIVE_INPUT_ENTRIES
                    or stat.S_ISLNK(descendant_info.st_mode)
                    or not stat.S_ISDIR(descendant_info.st_mode)
                    or SECRET_PATH_RE.search(descendant_relative)
                    or descendant_relative == ".git"
                    or descendant_relative.startswith(
                        (".git/", ".kimiflow/")
                    )
                ):
                    raise ReleaseProfileError(
                        "release_artifact_unsafe"
                    )
            for name in names:
                descendant = os.path.join(directory, name)
                descendant_info = os.lstat(descendant)
                entries += 1
                descendant_relative = os.path.relpath(
                    descendant, root
                ).replace(os.sep, "/")
                if (
                    entries > MAX_RELATIVE_INPUT_ENTRIES
                    or not stat.S_ISREG(descendant_info.st_mode)
                    or SECRET_PATH_RE.search(descendant_relative)
                    or descendant_relative.startswith(
                        (".git/", ".kimiflow/")
                    )
                ):
                    raise ReleaseProfileError(
                        "release_artifact_unsafe"
                    )
                entries_by_path[descendant_relative] = (
                    descendant, "file"
                )
            for name in directories:
                descendant = os.path.join(directory, name)
                descendant_relative = os.path.relpath(
                    descendant, root
                ).replace(os.sep, "/")
                entries_by_path[descendant_relative] = (
                    descendant, "directory"
                )
    return [
        (relative, *entries_by_path[relative])
        for relative in sorted(entries_by_path)
    ]


def _scan_secret_stream(handle, known_credentials, overlap, budget):
    tail = b""
    prefix = b""
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        if len(prefix) < 512:
            prefix += chunk[:512 - len(prefix)]
        budget[0] += len(chunk)
        if budget[0] > MAX_RELEASE_ARTIFACT_BYTES:
            raise ReleaseProfileError("release_artifact_scan_oversize")
        window = tail + chunk
        if SECRET_CONTENT_RE.search(window) or any(
            value in window for value in known_credentials
        ):
            raise ReleaseProfileError(
                "release_artifact_secret_detected"
            )
        tail = window[-overlap:]
    return prefix


def _archive_like(name, prefix):
    lower = name.lower()
    return (
        lower.endswith(ARCHIVE_SUFFIXES)
        or prefix.startswith((b"PK\x03\x04", b"7z\xbc\xaf\x27\x1c"))
        or prefix.startswith((b"Rar!\x1a\x07", b"\x1f\x8b", b"BZh"))
        or prefix.startswith((b"\xfd7zXZ\x00", b"xar!"))
    )


def _scan_archive(path, known_credentials, overlap, budget):
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                rows = archive.infolist()
                if len(rows) > MAX_RELATIVE_INPUT_ENTRIES:
                    raise ReleaseProfileError(
                        "release_artifact_scan_oversize"
                    )
                for row in rows:
                    name = row.filename.replace("\\", "/")
                    normalized = posixpath.normpath(name)
                    mode = (row.external_attr >> 16) & 0o170000
                    if (
                        name.startswith("/")
                        or normalized in (".", "..")
                        or normalized.startswith("../")
                        or SECRET_PATH_RE.search(normalized)
                        or row.flag_bits & 0x1
                        or mode == stat.S_IFLNK
                    ):
                        raise ReleaseProfileError(
                            "release_artifact_archive_unsafe"
                        )
                    if row.is_dir():
                        continue
                    if budget[0] + row.file_size > MAX_RELEASE_ARTIFACT_BYTES:
                        raise ReleaseProfileError(
                            "release_artifact_scan_oversize"
                        )
                    with archive.open(row) as member:
                        prefix = _scan_secret_stream(
                            member,
                            known_credentials,
                            overlap,
                            budget,
                        )
                    if _archive_like(normalized, prefix):
                        raise ReleaseProfileError(
                            "release_artifact_nested_archive_unsupported"
                        )
            return
        if tarfile.is_tarfile(path):
            with tarfile.open(path, mode="r:*") as archive:
                count = 0
                for row in archive:
                    count += 1
                    normalized = posixpath.normpath(
                        row.name.replace("\\", "/")
                    )
                    if (
                        count > MAX_RELATIVE_INPUT_ENTRIES
                        or row.name.startswith("/")
                        or normalized in (".", "..")
                        or normalized.startswith("../")
                        or SECRET_PATH_RE.search(normalized)
                        or not (row.isdir() or row.isfile())
                    ):
                        raise ReleaseProfileError(
                            "release_artifact_archive_unsafe"
                        )
                    if row.isdir():
                        continue
                    if budget[0] + row.size > MAX_RELEASE_ARTIFACT_BYTES:
                        raise ReleaseProfileError(
                            "release_artifact_scan_oversize"
                        )
                    member = archive.extractfile(row)
                    if member is None:
                        raise ReleaseProfileError(
                            "release_artifact_archive_unsafe"
                        )
                    with member:
                        prefix = _scan_secret_stream(
                            member,
                            known_credentials,
                            overlap,
                            budget,
                        )
                    if _archive_like(normalized, prefix):
                        raise ReleaseProfileError(
                            "release_artifact_nested_archive_unsupported"
                        )
            return
        with open(path, "rb") as handle:
            prefix = handle.read(8)
        if _archive_like(os.path.basename(path), prefix):
            raise ReleaseProfileError(
                "release_artifact_archive_unsupported"
            )
    except ReleaseProfileError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise ReleaseProfileError(
            "release_artifact_archive_unsafe"
        ) from exc


def _scan_release_artifacts(root, profile, inputs, effect, credentials):
    known_credentials = tuple(
        value.encode("utf-8")
        for value in credentials.values()
        if isinstance(value, str)
    )
    overlap = max(
        [4096] + [len(value) - 1 for value in known_credentials]
    )
    snapshot = {}
    budget = [0]
    for relative, path, kind in _release_artifact_entries(
        root, profile, inputs, effect
    ):
        try:
            before = os.lstat(path)
            if kind == "directory":
                snapshot[relative] = _artifact_stat(before)
                continue
            with open(path, "rb") as handle:
                _scan_secret_stream(
                    handle, known_credentials, overlap, budget
                )
                after = os.fstat(handle.fileno())
            _scan_archive(
                path, known_credentials, overlap, budget
            )
        except ReleaseProfileError:
            raise
        except OSError as exc:
            raise ReleaseProfileError(
                "release_artifact_unavailable"
            ) from exc
        if _artifact_stat(before) != _artifact_stat(after):
            raise ReleaseProfileError("release_artifact_drift")
        snapshot[relative] = _artifact_stat(after)
    return snapshot


def _release_artifact_snapshot_current(
    root, profile, inputs, effect, snapshot
):
    try:
        current = {
            relative: _artifact_stat(os.lstat(path))
            for relative, path, _kind in _release_artifact_entries(
                root, profile, inputs, effect
            )
        }
        return current == snapshot
    except (OSError, ReleaseProfileError):
        return False


def _resolved_v2_profile(root, profile, inputs, discovery=None):
    absolute_root = os.path.realpath(root)
    declarations = {row["name"]: row for row in profile["inputs"]}
    if set(inputs) != set(declarations):
        raise ReleaseProfileError("input_coverage_invalid")
    for name, row in declarations.items():
        if SECRET_INPUT_NAME_RE.search(name) or not _validate_input_value(
            row["type"], inputs[name]
        ):
            raise ReleaseProfileError("input_value_invalid")
        if (
            row["type"] == "relative_path"
            and not _relative_input_path_safe(root, inputs[name])
        ):
            raise ReleaseProfileError("input_value_invalid")

    def expand(value):
        if isinstance(value, str):
            def replace(match):
                name = match.group(1)
                if name not in inputs:
                    raise ReleaseProfileError("input_coverage_invalid")
                return inputs[name]

            expanded = PLACEHOLDER_RE.sub(replace, value)
            if "{{" in expanded or "}}" in expanded:
                raise ReleaseProfileError("profile_placeholder_invalid")
            return expanded
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        return value

    resolved = expand(profile)
    relative_names = {
        row["name"] for row in profile["inputs"]
        if row["type"] == "relative_path"
    }
    for source_command, resolved_command in zip(
        _profile_commands(profile), _profile_commands(resolved)
    ):
        argv = []
        for source, _value in zip(
            source_command["argv"], resolved_command["argv"]
        ):
            runtime_value = source
            for name in relative_names:
                runtime_value = runtime_value.replace(
                    "{{" + name + "}}",
                    os.path.join(absolute_root, *inputs[name].split("/")),
                )
            argv.append(expand(runtime_value))
        resolved_command["argv"] = argv
    validate_profile(profile)
    for command in _profile_commands(resolved):
        _validate_argv(
            command["argv"],
            "resolved_command",
            probe=command.get("kind") != "effect",
            sealed=True,
        )
    if discovery is not None:
        _validate_command_bindings(root, resolved, discovery)
    return resolved, _value_sha(inputs), _value_sha(resolved)


def _command_relative_input_paths(profile, inputs, command_id):
    source_command = _v2_command_by_id(profile, command_id)
    relative_names = {
        row["name"]
        for row in profile["inputs"]
        if row["type"] == "relative_path"
    }
    consumed = []
    for name in sorted(relative_names):
        placeholder = "{{" + name + "}}"
        if any(placeholder in item for item in source_command["argv"]):
            consumed.append(inputs[name])
    return consumed


def _command_evidence_paths(profile, inputs, command):
    return sorted(
        set(command["policy"]["affected_paths"])
        | set(
            _command_relative_input_paths(
                profile, inputs, command["id"]
            )
        )
    )


def _execute(root, command, probe=False):
    _validate_argv(command["argv"], "command", probe=probe)
    cwd = _safe_cwd(root, command["cwd"])
    environment = os.environ.copy()
    for name in list(environment):
        if (
            name.upper() in UNSAFE_EXECUTION_ENVIRONMENT
            or (probe and name.upper() in PROBE_UNSAFE_ENVIRONMENT)
            or (probe and name.upper().startswith("GIT_TRACE"))
            or name.upper().startswith(
                ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
            )
            or name.upper().startswith("BASH_FUNC_")
        ):
            environment.pop(name, None)
    if probe:
        environment.update(
            {
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_KEY_0": "core.fsmonitor",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_CONFIG_VALUE_0": "false",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_OPTIONAL_LOCKS": "0",
            }
        )
    with tempfile.TemporaryFile() as output:
        try:
            completed = subprocess.run(
                command["argv"],
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=command["timeout_seconds"],
                check=False,
            )
            exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            exit_code = 124
        except OSError:
            exit_code = 126
        output.seek(0)
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = output.read(65536)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    if size > MAX_OUTPUT_BYTES:
        exit_code = 125
    return {
        "exit_code": exit_code,
        "output_sha256": "sha256:" + digest.hexdigest(),
        "output_bytes": size,
        "command_sha256": _command_digest(command),
    }


def _empty_run(root, bundle, generation):
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode:
        raise ReleaseProfileError("head_unavailable")
    profile = bundle["profile"]
    return {
        "schema_version": 1,
        "profile_sha256": bundle["profile_sha256"],
        "control_set_sha256": bundle["control_set_sha256"],
        "head": head.stdout.decode("ascii", "strict").strip(),
        "generation": generation,
        "status": "active",
        "steps": [
            {
                "id": step["id"],
                "kind": step["kind"],
                "status": "pending",
                "evidence": None,
            }
            for step in profile["steps"]
        ],
        "final_checks": [
            {
                "id": check["id"],
                "kind": "final_check",
                "status": "pending",
                "evidence": None,
            }
            for check in profile["final_checks"]
        ],
        "completion_sha256": None,
    }


def _command_receipt_valid(value, command):
    if not isinstance(value, dict) or set(value) != {
        "exit_code", "output_sha256", "output_bytes", "command_sha256"
    }:
        return False
    exit_code = value.get("exit_code")
    output_bytes = value.get("output_bytes")
    return (
        isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and isinstance(output_bytes, int)
        and not isinstance(output_bytes, bool)
        and 0 <= output_bytes <= 2**63 - 1
        and isinstance(value.get("output_sha256"), str)
        and DIGEST_RE.fullmatch(value["output_sha256"]) is not None
        and value.get("command_sha256") == _command_digest(command)
    )


def _missing_effect_receipt(command):
    return {
        "receipt_status": "unavailable_after_interruption",
        "command_sha256": _command_digest(command),
    }


def _effect_receipt_valid(value, command):
    return _command_receipt_valid(value, command) or value == _missing_effect_receipt(
        command
    )


def _completion_basis(receipt):
    return {
        "profile_sha256": receipt["profile_sha256"],
        "control_set_sha256": receipt["control_set_sha256"],
        "head": receipt["head"],
        "generation": receipt["generation"],
        "steps": receipt["steps"],
        "final_checks": receipt["final_checks"],
    }


def _validate_run(receipt, bundle, resolved_profile=None):
    if bundle.get("profile", {}).get("schema_version") == 2:
        return _validate_run_v2(receipt, bundle, resolved_profile)
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version", "profile_sha256", "control_set_sha256", "head",
        "generation", "status", "steps", "final_checks", "completion_sha256",
    }:
        raise ReleaseProfileError("run_shape_invalid")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("profile_sha256") != bundle["profile_sha256"]
        or receipt.get("control_set_sha256") != bundle["control_set_sha256"]
        or receipt.get("status") not in ("active", "audit_required", "completed")
        or isinstance(receipt.get("generation"), bool)
        or not isinstance(receipt.get("generation"), int)
        or receipt["generation"] < 1
        or not isinstance(receipt.get("head"), str)
        or re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", receipt["head"]) is None
    ):
        raise ReleaseProfileError("run_contract_invalid")
    rows = receipt.get("steps")
    final_rows = receipt.get("final_checks")
    if (
        not isinstance(rows, list)
        or len(rows) != len(bundle["profile"]["steps"])
        or not isinstance(final_rows, list)
        or len(final_rows) != len(bundle["profile"]["final_checks"])
    ):
        raise ReleaseProfileError("run_steps_invalid")
    for row, step in zip(rows, bundle["profile"]["steps"]):
        if not isinstance(row, dict) or set(row) != {
            "id", "kind", "status", "evidence"
        }:
            raise ReleaseProfileError("run_step_shape_invalid")
        if row.get("id") != step["id"] or row.get("kind") != step["kind"]:
            raise ReleaseProfileError("run_steps_invalid")
        evidence = row.get("evidence")
        if step["kind"] == "check":
            if row.get("status") not in ("pending", "failed", "completed"):
                raise ReleaseProfileError("run_step_status_invalid")
            if row["status"] == "pending":
                if evidence is not None:
                    raise ReleaseProfileError("run_step_evidence_invalid")
            elif not _command_receipt_valid(evidence, step):
                raise ReleaseProfileError("run_step_evidence_invalid")
            elif row["status"] == "completed" and evidence["exit_code"] != 0:
                raise ReleaseProfileError("run_step_evidence_invalid")
            elif row["status"] == "failed" and evidence["exit_code"] == 0:
                raise ReleaseProfileError("run_step_evidence_invalid")
            continue
        if row.get("status") not in (
            "pending", "precondition_failed", "started", "effect_failed",
            "completed",
        ):
            raise ReleaseProfileError("run_step_status_invalid")
        if row["status"] == "pending":
            if evidence is not None:
                raise ReleaseProfileError("run_step_evidence_invalid")
            continue
        if not isinstance(evidence, dict) or set(evidence) - {
            "precondition", "effect", "postcondition"
        }:
            raise ReleaseProfileError("run_step_evidence_invalid")
        precondition = evidence.get("precondition")
        effect = evidence.get("effect")
        postcondition = evidence.get("postcondition")
        if not _command_receipt_valid(precondition, step["precondition"]):
            raise ReleaseProfileError("run_step_evidence_invalid")
        if row["status"] == "precondition_failed":
            if precondition["exit_code"] == 0 or effect is not None or postcondition is not None:
                raise ReleaseProfileError("run_step_evidence_invalid")
            continue
        if precondition["exit_code"] != 0:
            raise ReleaseProfileError("run_step_evidence_invalid")
        if row["status"] == "started":
            if effect is not None and not _effect_receipt_valid(effect, step):
                raise ReleaseProfileError("run_step_evidence_invalid")
            if postcondition is not None and not _command_receipt_valid(
                postcondition, step["postcondition"]
            ):
                raise ReleaseProfileError("run_step_evidence_invalid")
            if postcondition is not None and postcondition["exit_code"] == 0:
                raise ReleaseProfileError("run_step_evidence_invalid")
        elif row["status"] == "effect_failed":
            if (
                not _command_receipt_valid(effect, step)
                or effect["exit_code"] == 0
                or not _command_receipt_valid(
                    postcondition, step["postcondition"]
                )
                or postcondition["exit_code"] != 0
            ):
                raise ReleaseProfileError("run_step_evidence_invalid")
        elif (
            not _effect_receipt_valid(effect, step)
            or not _command_receipt_valid(postcondition, step["postcondition"])
            or postcondition["exit_code"] != 0
        ):
            raise ReleaseProfileError("run_step_evidence_invalid")
    for row, check in zip(final_rows, bundle["profile"]["final_checks"]):
        if not isinstance(row, dict) or set(row) != {
            "id", "kind", "status", "evidence"
        }:
            raise ReleaseProfileError("run_final_shape_invalid")
        if row.get("id") != check["id"] or row.get("kind") != "final_check":
            raise ReleaseProfileError("run_final_invalid")
        if row.get("status") not in ("pending", "failed", "completed"):
            raise ReleaseProfileError("run_final_status_invalid")
        evidence = row.get("evidence")
        if row["status"] == "pending":
            if evidence is not None:
                raise ReleaseProfileError("run_final_evidence_invalid")
        elif not _command_receipt_valid(evidence, check):
            raise ReleaseProfileError("run_final_evidence_invalid")
        elif row["status"] == "completed" and evidence["exit_code"] != 0:
            raise ReleaseProfileError("run_final_evidence_invalid")
        elif row["status"] == "failed" and evidence["exit_code"] == 0:
            raise ReleaseProfileError("run_final_evidence_invalid")
    all_complete = all(row["status"] == "completed" for row in rows + final_rows)
    completion = receipt.get("completion_sha256")
    if receipt["status"] == "completed":
        if not all_complete or completion != _value_sha(_completion_basis(receipt)):
            raise ReleaseProfileError("run_completion_invalid")
    elif completion is not None:
        raise ReleaseProfileError("run_completion_invalid")
    return receipt


def _failure(root, bundle, step_id, evidence):
    paths = _state_paths(root)
    value = {
        "schema_version": 1,
        "event_id": secrets.token_hex(16),
        "profile_sha256": bundle["profile_sha256"],
        "step_id": step_id,
        "evidence": evidence,
    }
    _persist_local(
        paths["failure"], value, "failure_persist_failed"
    )
    return _value_sha(value)


def _persist_run(root, receipt):
    path = _state_paths(root)["run"]
    _persist_local(path, receipt, "run_persist_failed")


def _require_controls_current(root, bundle):
    if not _controls_current(root, bundle["profile"]):
        raise ReleaseProfileError("control_drift")


def _require_run_head(root, receipt):
    head = _git(root, "rev-parse", "HEAD")
    if (
        head.returncode
        or head.stdout.decode("ascii", "strict").strip() != receipt["head"]
    ):
        raise ReleaseProfileError("run_head_drift")


def _run_profile_locked(root, authorize, new=False):
    if authorize is not True:
        raise ReleaseProfileError("authorization_required")
    readiness = status(root)
    if readiness.get("status") != "ready":
        raise ReleaseProfileError(readiness.get("reason") or readiness["status"])
    bundle = _load_bundle(root)
    paths = _state_paths(root)
    receipt = _load_optional(paths["run"], "run")
    next_generation = 1
    if receipt is not None and _superseded_run(receipt, bundle):
        next_generation = max(1, receipt.get("generation", 0) + 1)
        receipt = None
    if receipt is None:
        receipt = _empty_run(root, bundle, next_generation)
        _persist_run(root, receipt)
    else:
        if receipt.get("profile_sha256") != bundle["profile_sha256"]:
            raise ReleaseProfileError("run_profile_mismatch")
        _validate_run(receipt, bundle)
        if receipt["status"] == "completed":
            if not new:
                return receipt
            receipt = _empty_run(root, bundle, receipt["generation"] + 1)
            _persist_run(root, receipt)
        elif new:
            raise ReleaseProfileError("run_already_active")

    profile = bundle["profile"]
    for index, step in enumerate(profile["steps"]):
        _require_controls_current(root, bundle)
        row = receipt["steps"][index]
        if row["status"] == "completed":
            continue
        if step["kind"] == "check":
            evidence = _execute(root, step, probe=True)
            row["evidence"] = evidence
            if evidence["exit_code"] != 0:
                row["status"] = "failed"
                receipt["status"] = "audit_required"
                _failure(root, bundle, step["id"], evidence)
                _persist_run(root, receipt)
                raise ReleaseProfileError("step_failed")
            row["status"] = "completed"
            _persist_run(root, receipt)
            continue

        if row["status"] in ("pending", "precondition_failed"):
            precondition = _execute(
                root, step["precondition"], probe=True
            )
            row["evidence"] = {"precondition": precondition}
            if precondition["exit_code"] != 0:
                row["status"] = "precondition_failed"
                receipt["status"] = "audit_required"
                _failure(root, bundle, step["id"], precondition)
                _persist_run(root, receipt)
                raise ReleaseProfileError("precondition_failed")
            _require_controls_current(root, bundle)
            row["status"] = "started"
            _persist_run(root, receipt)
            effect = _execute(root, step)
            row["evidence"]["effect"] = effect
            _persist_run(root, receipt)
        else:
            effect = (row.get("evidence") or {}).get("effect")
            if effect is None:
                effect = _missing_effect_receipt(step)
                row["evidence"]["effect"] = effect
                _persist_run(root, receipt)

        postcondition = _execute(
            root, step["postcondition"], probe=True
        )
        if row.get("evidence") is None:
            row["evidence"] = {}
        row["evidence"]["postcondition"] = postcondition
        if postcondition["exit_code"] == 0:
            if (
                _command_receipt_valid(effect, step)
                and effect["exit_code"] != 0
            ):
                row["status"] = "effect_failed"
                receipt["status"] = "audit_required"
                _failure(root, bundle, step["id"], effect)
                _persist_run(root, receipt)
                raise ReleaseProfileError("effect_reported_failure")
            row["status"] = "completed"
            _persist_run(root, receipt)
            continue
        receipt["status"] = "audit_required"
        _failure(root, bundle, step["id"], postcondition)
        _persist_run(root, receipt)
        raise ReleaseProfileError("postcondition_failed")

    for index, check in enumerate(profile["final_checks"]):
        _require_controls_current(root, bundle)
        row = receipt["final_checks"][index]
        if row["status"] == "completed":
            continue
        evidence = _execute(root, check, probe=True)
        row["evidence"] = evidence
        if evidence["exit_code"] != 0:
            row["status"] = "failed"
            receipt["status"] = "audit_required"
            _failure(root, bundle, check["id"], evidence)
            _persist_run(root, receipt)
            raise ReleaseProfileError("final_check_failed")
        row["status"] = "completed"
        _persist_run(root, receipt)

    _require_controls_current(root, bundle)
    receipt["completion_sha256"] = _value_sha(_completion_basis(receipt))
    receipt["status"] = "completed"
    _persist_run(root, receipt)
    return receipt


def _v2_empty_run(root, bundle, resolved_sha256, input_sha256, generation, bound):
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode:
        raise ReleaseProfileError("head_unavailable")
    profile = bundle["profile"]
    return {
        "schema_version": 2,
        "profile_sha256": bundle["profile_sha256"],
        "resolved_profile_sha256": resolved_sha256,
        "input_sha256": input_sha256,
        "control_set_sha256": bundle["control_set_sha256"],
        "binding": bound,
        "head": head.stdout.decode("ascii", "strict").strip(),
        "generation": generation,
        "status": "active",
        "identity": None,
        "steps": [
            {
                "id": step["id"],
                "kind": step["kind"],
                "status": "pending",
                "failure_class": None,
                "failure_audit_sha256s": [],
                "resume_context_sha256": None,
                "evidence": None,
            }
            for step in profile["steps"]
        ],
        "final_checks": [
            {
                "id": check["id"],
                "kind": "final_check",
                "status": "pending",
                "failure_class": None,
                "failure_audit_sha256s": [],
                "resume_context_sha256": None,
                "evidence": None,
            }
            for check in profile["final_checks"]
        ],
        "completion_sha256": None,
    }


def _validate_v2_evidence(value):
    if not isinstance(value, dict):
        return False
    required = {
        "exit_code", "output_sha256", "output_bytes", "command_sha256",
        "duration_milliseconds", "failure_class", "source",
    }
    return (
        set(value) == required
        and isinstance(value.get("exit_code"), int)
        and not isinstance(value.get("exit_code"), bool)
        and isinstance(value.get("output_bytes"), int)
        and value["output_bytes"] >= 0
        and isinstance(value.get("duration_milliseconds"), int)
        and value["duration_milliseconds"] >= 0
        and DIGEST_RE.fullmatch(value.get("output_sha256", "")) is not None
        and DIGEST_RE.fullmatch(value.get("command_sha256", "")) is not None
        and value.get("failure_class") in (
            None, "auth", "network", "rate_limit", "timeout",
            "unavailable", "semantic",
        )
        and value.get("source") in ("executed", "kimiflow_verification")
    )


def _failure_audit_matches(bundle, marker, step_id, evidence):
    if marker is None:
        return False
    return any(
        _value_sha(item) == marker
        and item["failure"]["step_id"] == step_id
        and item["failure"]["evidence"] == evidence
        for item in bundle.get("failure_audits", [])
    )


def _validate_run_v2(receipt, bundle, resolved_profile=None):
    required = {
        "schema_version", "profile_sha256", "resolved_profile_sha256",
        "input_sha256", "control_set_sha256", "binding", "head",
        "generation", "status", "identity", "steps", "final_checks",
        "completion_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ReleaseProfileError("run_shape_invalid")
    if (
        receipt.get("schema_version") != 2
        or receipt.get("profile_sha256") != bundle["profile_sha256"]
        or receipt.get("control_set_sha256") != bundle["control_set_sha256"]
        or DIGEST_RE.fullmatch(receipt.get("resolved_profile_sha256", "")) is None
        or DIGEST_RE.fullmatch(receipt.get("input_sha256", "")) is None
        or not isinstance(receipt.get("binding"), dict)
        or set(receipt["binding"]) != {
            "repository_id", "worktree_id", "target_sha256",
        }
        or not all(isinstance(receipt["binding"][key], str) for key in receipt["binding"])
        or receipt.get("status") not in (
            "active", "retryable_failure", "audit_required", "completed",
        )
        or isinstance(receipt.get("generation"), bool)
        or not isinstance(receipt.get("generation"), int)
        or receipt["generation"] < 1
        or re.fullmatch(
            r"[0-9a-f]{40}(?:[0-9a-f]{24})?", receipt.get("head", "")
        )
        is None
    ):
        raise ReleaseProfileError("run_contract_invalid")
    identity = receipt.get("identity")
    if identity is not None and (
        not isinstance(identity, dict)
        or set(identity) != {"kind", "account_sha256", "resolver_probes"}
        or identity.get("kind") not in (
            "environment", "github_native", "github_cli",
        )
        or DIGEST_RE.fullmatch(identity.get("account_sha256", "")) is None
        or not isinstance(identity.get("resolver_probes"), int)
        or identity["resolver_probes"] < 0
    ):
        raise ReleaseProfileError("run_identity_invalid")
    rows = receipt.get("steps")
    finals = receipt.get("final_checks")
    if (
        not isinstance(rows, list)
        or len(rows) != len(bundle["profile"]["steps"])
        or not isinstance(finals, list)
        or len(finals) != len(bundle["profile"]["final_checks"])
    ):
        raise ReleaseProfileError("run_steps_invalid")
    allowed_status = {
        "pending", "failed", "retryable", "started", "completed",
    }
    expected_profile = resolved_profile or bundle["profile"]
    accepted_failure_audits = {
        _value_sha(row) for row in bundle.get("failure_audits", [])
    }
    for row, expected in zip(
        rows + finals,
        expected_profile["steps"] + expected_profile["final_checks"],
    ):
        if (
            not isinstance(row, dict)
            or set(row) != {
                "id", "kind", "status", "failure_class",
                "failure_audit_sha256s", "resume_context_sha256",
                "evidence",
            }
            or row.get("id") != expected["id"]
            or row.get("status") not in allowed_status
            or row.get("failure_class") not in (
                None, "auth", "network", "rate_limit", "timeout",
                "unavailable", "semantic",
            )
            or not isinstance(row.get("failure_audit_sha256s"), list)
            or len(row["failure_audit_sha256s"]) > 128
            or len(row["failure_audit_sha256s"])
            != len(set(row["failure_audit_sha256s"]))
            or any(
                not isinstance(marker, str)
                or DIGEST_RE.fullmatch(marker) is None
                or marker not in accepted_failure_audits
                for marker in row["failure_audit_sha256s"]
            )
        ):
            raise ReleaseProfileError("run_step_invalid")
        resume_context_sha256 = row.get("resume_context_sha256")
        if (
            resume_context_sha256 is not None
            and DIGEST_RE.fullmatch(resume_context_sha256) is None
        ):
            raise ReleaseProfileError("run_step_invalid")
        evidence = row.get("evidence")
        if expected.get("kind") == "effect" and evidence is not None:
            if (
                not isinstance(evidence, dict)
                or set(evidence) - {"precondition", "effect", "postcondition"}
                or any(
                    not _validate_v2_evidence(item)
                    for item in evidence.values()
                )
            ):
                raise ReleaseProfileError("run_step_evidence_invalid")
        elif evidence is not None and not _validate_v2_evidence(evidence):
            raise ReleaseProfileError("run_step_evidence_invalid")
        if expected.get("kind") == "effect":
            if resume_context_sha256 is not None:
                raise ReleaseProfileError("run_step_evidence_invalid")
        elif row["status"] == "completed":
            if (
                expected["policy"]["auth"] == "provider"
                and resume_context_sha256 is not None
            ) or (
                expected["policy"]["auth"] == "none"
                and resume_context_sha256 is None
            ):
                raise ReleaseProfileError(
                    "run_completed_evidence_invalid"
                )
        elif resume_context_sha256 is not None:
            raise ReleaseProfileError("run_step_evidence_invalid")
        if expected.get("kind") == "effect":
            precondition = (
                evidence.get("precondition")
                if isinstance(evidence, dict)
                else None
            )
            effect = (
                evidence.get("effect")
                if isinstance(evidence, dict)
                else None
            )
            if (
                effect is not None
                and effect["exit_code"] != 0
                and row["failure_audit_sha256s"]
                and not any(
                    _failure_audit_matches(
                        bundle, marker, row["id"], effect
                    )
                    for marker in row["failure_audit_sha256s"]
                )
            ):
                raise ReleaseProfileError("run_step_evidence_invalid")
            postcondition = (
                evidence.get("postcondition")
                if isinstance(evidence, dict)
                else None
            )
            if (
                postcondition is not None
                and postcondition["exit_code"] != 0
                and not _retryable(postcondition["failure_class"])
                and receipt["status"] != "audit_required"
                and not any(
                    _failure_audit_matches(
                        bundle, marker, row["id"], postcondition
                    )
                    for marker in row["failure_audit_sha256s"]
                )
            ):
                raise ReleaseProfileError("run_step_evidence_invalid")
            if row["status"] == "pending":
                if (
                    evidence is not None
                    or row["failure_class"] is not None
                    or row["failure_audit_sha256s"]
                ):
                    raise ReleaseProfileError("run_step_evidence_invalid")
            elif row["status"] in ("retryable", "failed"):
                if (
                    not isinstance(evidence, dict)
                    or set(evidence) != {"precondition"}
                    or precondition["exit_code"] == 0
                    or row["failure_class"]
                    != precondition["failure_class"]
                    or row["failure_audit_sha256s"]
                    or (
                        row["status"] == "retryable"
                        and not _retryable(precondition["failure_class"])
                    )
                    or (
                        row["status"] == "failed"
                        and _retryable(precondition["failure_class"])
                    )
                ):
                    raise ReleaseProfileError("run_step_evidence_invalid")
            elif row["status"] == "started":
                expected_failure = (
                    evidence["postcondition"]["failure_class"]
                    if isinstance(evidence, dict)
                    and "postcondition" in evidence
                    else (
                        evidence["effect"]["failure_class"]
                        if isinstance(evidence, dict)
                        and "effect" in evidence
                        else None
                    )
                )
                if (
                    not isinstance(evidence, dict)
                    or precondition is None
                    or precondition["exit_code"] != 0
                    or (
                        "postcondition" in evidence
                        and evidence["postcondition"]["exit_code"] == 0
                    )
                    or row["failure_class"] != expected_failure
                ):
                    raise ReleaseProfileError("run_step_evidence_invalid")
        elif row["status"] == "pending":
            if (
                evidence is not None
                or row["failure_class"] is not None
                or row["failure_audit_sha256s"]
            ):
                raise ReleaseProfileError("run_step_evidence_invalid")
        elif row["status"] in ("retryable", "failed"):
            if (
                evidence is None
                or evidence["exit_code"] == 0
                or row["failure_class"] != evidence["failure_class"]
                or row["failure_audit_sha256s"]
                or (
                    row["status"] == "retryable"
                    and not _retryable(evidence["failure_class"])
                )
                or (
                    row["status"] == "failed"
                    and _retryable(evidence["failure_class"])
                )
            ):
                raise ReleaseProfileError("run_step_evidence_invalid")
        elif row["status"] == "started":
            raise ReleaseProfileError("run_step_evidence_invalid")
        if row["status"] == "completed":
            if expected.get("kind") == "effect":
                if (
                    not isinstance(evidence, dict)
                    or not {"precondition", "postcondition"}.issubset(evidence)
                    or evidence["precondition"]["exit_code"] != 0
                    or evidence["postcondition"]["exit_code"] != 0
                    or (
                        "effect" in evidence
                        and evidence["effect"]["exit_code"] != 0
                        and not any(
                            _failure_audit_matches(
                                bundle,
                                marker,
                                row["id"],
                                evidence["effect"],
                            )
                            for marker in row[
                                "failure_audit_sha256s"
                            ]
                        )
                    )
                ):
                    raise ReleaseProfileError(
                        "run_completed_evidence_invalid"
                    )
                expected_commands = {
                    "precondition": expected["precondition"],
                    "effect": expected,
                    "postcondition": expected["postcondition"],
                }
                if resolved_profile is not None and any(
                    evidence[name]["command_sha256"]
                    != _command_digest(command)
                    for name, command in expected_commands.items()
                    if name in evidence
                ):
                    raise ReleaseProfileError(
                        "run_completed_evidence_invalid"
                    )
            elif (
                evidence is None
                or evidence["exit_code"] != 0
                or row["failure_class"] is not None
                or row["failure_audit_sha256s"]
                or (
                    resolved_profile is not None
                    and evidence["command_sha256"]
                    != _command_digest(expected)
                )
            ):
                raise ReleaseProfileError("run_completed_evidence_invalid")
    all_complete = all(
        row["status"] == "completed" for row in rows + finals
    )
    if receipt["status"] == "completed":
        if (
            not all_complete
            or receipt["identity"] is None
            or receipt["completion_sha256"] != _value_sha(
                {
                    key: receipt[key]
                    for key in (
                        "profile_sha256", "resolved_profile_sha256",
                        "input_sha256", "control_set_sha256", "binding",
                        "head", "generation", "identity", "steps",
                        "final_checks",
                    )
                }
            )
        ):
            raise ReleaseProfileError("run_completion_invalid")
    elif receipt["completion_sha256"] is not None:
        raise ReleaseProfileError("run_completion_invalid")
    return receipt


def _classify_v2_failure(exit_code, output_sample, command, timed_out, unavailable):
    if exit_code == 0:
        return None
    if timed_out:
        return "timeout"
    if unavailable:
        return "unavailable"
    if command["policy"]["failure"] == "semantic":
        return "semantic"
    if command["policy"]["auth"] != "provider":
        return "semantic"
    text = output_sample.decode("utf-8", "replace").lower()
    if re.search(r"(?:rate.?limit|http\s*429|too many requests)", text):
        return "rate_limit"
    if re.search(
        r"(?:unauthori[sz]ed|forbidden|bad credentials|not logged in|"
        r"authentication failed|http\s*(?:401|403))",
        text,
    ):
        return "auth"
    if re.search(
        r"(?:network|connection (?:reset|refused|timed out)|"
        r"could not resolve|temporary failure|tls handshake|dns)",
        text,
    ):
        return "network"
    return "semantic"


def _terminate_process(process):
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    elif process.poll() is None:
        process.kill()


def _start_cleanup_watchdog(process, home):
    if os.name != "posix":
        raise ReleaseProfileError("cleanup_watchdog_unavailable")
    program = (
        "import os,signal,shutil,sys,time\n"
        "parent=int(sys.argv[1]); target=int(sys.argv[2]); home=sys.argv[3]\n"
        "orphaned=False\n"
        "while True:\n"
        " if os.getppid()!=parent:\n"
        "  orphaned=True; break\n"
        " try: os.kill(target,0)\n"
        " except OSError: break\n"
        " time.sleep(0.05)\n"
        "try: os.killpg(target,signal.SIGKILL)\n"
        "except OSError: pass\n"
        "for _ in range(100):\n"
        " try: os.kill(target,0)\n"
        " except OSError: break\n"
        " time.sleep(0.02)\n"
        "if orphaned: shutil.rmtree(home,ignore_errors=True)\n"
    )
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                program,
                str(os.getpid()),
                str(process.pid),
                home,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                "PATH": os.defpath,
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise ReleaseProfileError(
            "cleanup_watchdog_unavailable"
        ) from exc


def _bounded_output_reader(stream, process, capture):
    limit = max(0, MAX_OUTPUT_BYTES)
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            room = max(0, limit + 1 - capture["size"])
            accepted = chunk[:room]
            if accepted:
                capture["digest"].update(accepted)
                capture["size"] += len(accepted)
                if len(capture["sample"]) < 65536:
                    capture["sample"].extend(
                        accepted[:65536 - len(capture["sample"])]
                    )
            if len(accepted) != len(chunk) or capture["size"] > limit:
                capture["exceeded"] = True
                _terminate_process(process)
                break
    except (OSError, ValueError):
        capture["read_error"] = True


def _execute_v2(root, command, credentials=None):
    _validate_argv(
        command["argv"],
        "command",
        probe=command.get("kind") != "effect",
        sealed=True,
    )
    cwd = _safe_cwd(root, command["cwd"])
    started = time.monotonic_ns()
    timed_out = False
    unavailable = False
    capture = {
        "digest": hashlib.sha256(),
        "size": 0,
        "sample": bytearray(),
        "exceeded": False,
        "read_error": False,
    }
    try:
        temporary_home = release_memory.temporary_directory(
            root, "kimiflow-release-home-"
        )
    except release_memory.ReleaseMemoryError as exc:
        raise ReleaseProfileError(exc.code) from exc
    with temporary_home as home:
        try:
            environment = release_memory.sealed_environment(
                os.environ,
                declared_public=command["policy"]["declared_env"],
                credentials=(
                    credentials
                    if command["policy"]["auth"] == "provider"
                    else None
                ),
                home=home,
            )
        except release_memory.ReleaseMemoryError as exc:
            raise ReleaseProfileError(exc.code) from exc
        process = None
        watchdog = None
        reader = None
        previous_handlers = {}

        def interrupted(_signum, _frame):
            raise ReleaseProfileError("command_interrupted")

        if threading.current_thread() is threading.main_thread():
            for name in ("SIGTERM", "SIGINT", "SIGHUP"):
                candidate = getattr(signal, name, None)
                if candidate is not None:
                    previous_handlers[candidate] = signal.getsignal(candidate)
                    signal.signal(candidate, interrupted)
        try:
            process = subprocess.Popen(
                command["argv"],
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
            )
            if command["policy"]["auth"] == "provider":
                try:
                    watchdog = _start_cleanup_watchdog(process, home)
                except ReleaseProfileError:
                    _terminate_process(process)
                    process.wait()
                    raise
            reader = threading.Thread(
                target=_bounded_output_reader,
                args=(process.stdout, process, capture),
                daemon=True,
            )
            reader.start()
            try:
                exit_code = process.wait(
                    timeout=command["timeout_seconds"]
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process(process)
                process.wait()
                exit_code = 124
            reader.join(timeout=1)
            if reader.is_alive():
                _terminate_process(process)
                if process.stdout is not None:
                    process.stdout.close()
                reader.join(timeout=1)
                capture["read_error"] = True
        except OSError:
            if process is not None:
                _terminate_process(process)
                process.wait()
            exit_code = 126
            unavailable = True
        finally:
            if process is not None:
                if (
                    command["policy"]["auth"] == "provider"
                    or process.poll() is None
                ):
                    _terminate_process(process)
                if process.poll() is None:
                    process.wait()
                if process.stdout is not None:
                    process.stdout.close()
            if watchdog is not None:
                try:
                    watchdog.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    watchdog.terminate()
                    watchdog.wait()
            for candidate, handler in previous_handlers.items():
                signal.signal(candidate, handler)
    if capture["exceeded"]:
        exit_code = 125
        unavailable = True
    elif capture["read_error"] and not timed_out:
        exit_code = 126
        unavailable = True
    failure_class = _classify_v2_failure(
        exit_code,
        bytes(capture["sample"]),
        command,
        timed_out,
        unavailable,
    )
    return {
        "exit_code": exit_code,
        "output_sha256": (
            "sha256:" + capture["digest"].hexdigest()
        ),
        "output_bytes": capture["size"],
        "command_sha256": _command_digest(command),
        "duration_milliseconds": max(
            0, (time.monotonic_ns() - started) // 1_000_000
        ),
        "failure_class": failure_class,
        "source": "executed",
    }


def _v2_command_by_id(profile, check_id):
    for step in profile["steps"]:
        if step["id"] == check_id and step["kind"] == "check":
            return step
        if step["kind"] == "effect":
            for probe in (step["precondition"], step["postcondition"]):
                if probe["id"] == check_id:
                    return probe
    for check in profile["final_checks"]:
        if check["id"] == check_id:
            return check
    raise ReleaseProfileError("evidence_check_unknown")


def _source_run(root, value):
    run = os.path.realpath(value)
    parent = os.path.realpath(os.path.join(root, ".kimiflow"))
    try:
        if os.path.commonpath((parent, run)) != parent:
            raise ReleaseProfileError("evidence_run_invalid")
    except ValueError as exc:
        raise ReleaseProfileError("evidence_run_invalid") from exc
    relative = os.path.relpath(run, parent)
    if os.sep in relative or relative in (".", ".."):
        raise ReleaseProfileError("evidence_run_invalid")
    return run, relative


def _conformance_open(run_path):
    hooks_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    gate = os.path.join(hooks_directory, "conformance-gate.sh")
    try:
        completed = subprocess.run(
            [gate, run_path, "--finish"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            env=release_memory.scrub_environment(os.environ),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0 or len(completed.stdout) > 4096:
        return False
    try:
        output = completed.stdout.decode("utf-8", "strict").strip()
    except UnicodeError:
        return False
    return re.fullmatch(
        r"CONFORMANCE_GATE\tOPEN\tblockers=0\treason=clean\tdetail=[^\r\n]*",
        output,
    ) is not None


def evidence_execute(root, run, check_id, inputs=None, write=False):
    root = workspace_root(root)
    if not write:
        raise ReleaseProfileError("write_required")
    with _release_lock(root):
        bundle = _load_bundle(root)
        profile = bundle["profile"]
        if profile["schema_version"] != 2:
            raise ReleaseProfileError("evidence_v2_required")
        readiness = status(root)
        if readiness.get("status") != "ready":
            raise ReleaseProfileError(
                readiness.get("reason") or readiness["status"]
            )
        resolved, _input_sha256, _resolved_sha256 = _resolved_v2_profile(
            root, profile, inputs or {}, bundle["discovery"]
        )
        command = _v2_command_by_id(resolved, check_id)
        if command["policy"]["reuse"] != "kimiflow_verification":
            raise ReleaseProfileError("evidence_reuse_not_declared")
        evidence_paths = _command_evidence_paths(
            profile, inputs or {}, command
        )
        run_path, run_name = _source_run(root, run)
        _require_controls_current(root, bundle)
        try:
            environment = release_memory.sealed_environment(
                os.environ,
                declared_public=command["policy"]["declared_env"],
            )
            before = {
                "affected_sha256": release_memory.path_fingerprints(
                    root, evidence_paths
                ),
                "declared_environment_sha256": (
                    release_memory.declared_environment_digest(
                        os.environ, command["policy"]["declared_env"]
                    )
                ),
                "path_sha256": _sha(
                    environment.get("PATH", "").encode("utf-8")
                ),
                "tool_sha256": release_memory.tool_fingerprints(
                    command["argv"],
                    environment,
                    cwd=_safe_cwd(root, command["cwd"]),
                ),
            }
        except release_memory.ReleaseMemoryError as exc:
            raise ReleaseProfileError(exc.code) from exc
        _require_relative_inputs_current(root, profile, inputs or {})
        _require_controls_current(root, bundle)
        _require_profile_tools_current(root, bundle)
        _require_command_tools_current(
            root, command, before["tool_sha256"]
        )
        _require_relative_inputs_current(root, profile, inputs or {})
        evidence = _execute_v2(root, command)
        if evidence["exit_code"] != 0:
            raise ReleaseProfileError("evidence_check_failed")
        try:
            _require_controls_current(root, bundle)
            after_environment = release_memory.sealed_environment(
                os.environ,
                declared_public=command["policy"]["declared_env"],
            )
            after = {
                "affected_sha256": release_memory.path_fingerprints(
                    root, evidence_paths
                ),
                "declared_environment_sha256": (
                    release_memory.declared_environment_digest(
                        os.environ, command["policy"]["declared_env"]
                    )
                ),
                "path_sha256": _sha(
                    after_environment.get("PATH", "").encode("utf-8")
                ),
                "tool_sha256": release_memory.tool_fingerprints(
                    command["argv"],
                    after_environment,
                    cwd=_safe_cwd(root, command["cwd"]),
                ),
            }
            if after != before:
                raise ReleaseProfileError("evidence_inputs_changed")
            receipt = {
                "schema_version": 2,
                "check_id": check_id,
                "profile_sha256": bundle["profile_sha256"],
                "source_run": run_name,
                "source_run_sha256": _value_sha({"run": run_name}),
                "command_sha256": _command_digest(command),
                "affected_sha256": before["affected_sha256"],
                "declared_environment_sha256": before[
                    "declared_environment_sha256"
                ],
                "path_sha256": before["path_sha256"],
                "tool_sha256": before["tool_sha256"],
                "execution": evidence,
            }
            release_memory.write_evidence(root, check_id, receipt)
        except release_memory.ReleaseMemoryError as exc:
            raise ReleaseProfileError(exc.code) from exc
        return receipt


def _current_evidence(root, bundle, command, inputs):
    if command["policy"]["reuse"] != "kimiflow_verification":
        return None
    try:
        receipt = release_memory.read_evidence(root, command["id"])
        if receipt is None:
            return None
        expected_keys = {
            "schema_version", "check_id", "profile_sha256",
            "source_run", "source_run_sha256", "command_sha256",
            "affected_sha256", "declared_environment_sha256",
            "path_sha256", "tool_sha256", "execution",
        }
        if (
            set(receipt) != expected_keys
            or receipt.get("schema_version") != 2
            or receipt.get("check_id") != command["id"]
            or receipt.get("profile_sha256") != bundle["profile_sha256"]
            or receipt.get("command_sha256") != _command_digest(command)
            or not _validate_v2_evidence(receipt.get("execution"))
            or receipt["execution"]["exit_code"] != 0
        ):
            return None
        run_path, run_name = _source_run(
            root, os.path.join(root, ".kimiflow", receipt["source_run"])
        )
        if (
            run_name != receipt["source_run"]
            or receipt["source_run_sha256"] != _value_sha({"run": run_name})
            or not release_memory.kimiflow_run_terminal(run_path)
            or not _conformance_open(run_path)
        ):
            return None
        paths = _command_evidence_paths(
            bundle["profile"], inputs, command
        )
        affected = release_memory.path_fingerprints(root, paths)
        if (
            affected != receipt["affected_sha256"]
            or release_memory.head_path_fingerprints(root, paths) != affected
            or receipt["declared_environment_sha256"]
            != release_memory.declared_environment_digest(
                os.environ, command["policy"]["declared_env"]
            )
        ):
            return None
        environment = release_memory.sealed_environment(
            os.environ, declared_public=command["policy"]["declared_env"]
        )
        if (
            receipt["path_sha256"]
            != _sha(environment.get("PATH", "").encode("utf-8"))
            or receipt["tool_sha256"]
            != release_memory.tool_fingerprints(
                command["argv"],
                environment,
                cwd=_safe_cwd(root, command["cwd"]),
            )
        ):
            return None
        return {
            "exit_code": 0,
            "output_sha256": receipt["execution"]["output_sha256"],
            "output_bytes": receipt["execution"]["output_bytes"],
            "command_sha256": _command_digest(command),
            "duration_milliseconds": 0,
            "failure_class": None,
            "source": "kimiflow_verification",
        }
    except (ReleaseProfileError, release_memory.ReleaseMemoryError):
        return None


def _resume_context_sha256(root, profile, inputs, command):
    """Bind an in-run check result to its current non-secret inputs."""
    if command["policy"]["auth"] == "provider":
        return None
    try:
        environment = release_memory.sealed_environment(
            os.environ,
            declared_public=command["policy"]["declared_env"],
        )
        return _value_sha(
            {
                "affected_sha256": release_memory.path_fingerprints(
                    root,
                    _command_evidence_paths(profile, inputs, command),
                ),
                "declared_environment_sha256": (
                    release_memory.declared_environment_digest(
                        os.environ, command["policy"]["declared_env"]
                    )
                ),
                "path_sha256": _sha(
                    environment.get("PATH", "").encode("utf-8")
                ),
                "tool_sha256": release_memory.tool_fingerprints(
                    command["argv"],
                    environment,
                    cwd=_safe_cwd(root, command["cwd"]),
                ),
            }
        )
    except release_memory.ReleaseMemoryError as exc:
        raise ReleaseProfileError(exc.code) from exc


def _resume_context_matches(root, profile, inputs, command, expected):
    if expected is None or command["policy"]["auth"] == "provider":
        return False
    try:
        return _resume_context_sha256(
            root, profile, inputs, command
        ) == expected
    except ReleaseProfileError as exc:
        if exc.code == "evidence_path_unavailable":
            return False
        raise


def _retryable(failure_class):
    return failure_class in {
        "auth", "network", "rate_limit", "timeout", "unavailable",
    }


def _add_v2_metrics(metrics, evidence, command, reused=False):
    if command.get("kind") != "effect":
        key = "checks_reused" if reused else "checks_executed"
        metrics["counts"][key] += 1
    stage = command["policy"]["stage"]
    metrics["duration_milliseconds"][stage] += evidence[
        "duration_milliseconds"
    ]


def _finalize_v2_control_duration(metrics, timer):
    command_time = sum(
        value
        for stage, value in metrics["duration_milliseconds"].items()
        if stage != "kimiflow_control"
    )
    metrics["duration_milliseconds"]["kimiflow_control"] = max(
        0, timer.milliseconds() - command_time
    )


def _write_v2_memory(
    root,
    bundle,
    resolved_sha256,
    bound,
    receipt,
    prior_memory,
    duration_totals,
):
    failure_classes = dict(
        prior_memory.get("failure_classes", {}) if prior_memory else {}
    )
    for row in receipt["steps"] + receipt["final_checks"]:
        failure_class = row.get("failure_class")
        if failure_class is not None:
            failure_classes[failure_class] = min(
                failure_classes.get(failure_class, 0) + 1,
                2**31 - 1,
            )
    return release_memory.write_verified_memory(
        root,
        bound,
        bundle["profile_sha256"],
        resolved_sha256,
        receipt["identity"],
        [
            row["id"]
            for row in receipt["steps"] + receipt["final_checks"]
            if row["status"] == "completed"
        ],
        generation=receipt["generation"],
        failure_classes=failure_classes,
        duration_totals=duration_totals,
        previous_duration_totals=(
            prior_memory.get("duration_totals", {})
            if prior_memory
            else {}
        ),
    )


def _v2_failure(root, bundle, receipt, row, evidence, code):
    row["failure_class"] = evidence["failure_class"]
    row["failure_audit_sha256s"] = []
    row["resume_context_sha256"] = None
    if _retryable(evidence["failure_class"]):
        row["status"] = "retryable"
        receipt["status"] = "retryable_failure"
        _persist_run(root, receipt)
        raise ReleaseProfileError("retryable_failure")
    row["status"] = "failed"
    receipt["status"] = "audit_required"
    _failure(root, bundle, row["id"], evidence)
    _persist_run(root, receipt)
    raise ReleaseProfileError(code)


def _run_profile_v2_locked(root, bundle, authorize, inputs, new=False):
    if authorize is not True:
        raise ReleaseProfileError("authorization_required")
    resolved, input_sha256, resolved_sha256 = _resolved_v2_profile(
        root, bundle["profile"], inputs, bundle["discovery"]
    )
    _require_controls_current(root, bundle)
    resolved_tool_fingerprints = _profile_tool_fingerprints(root, resolved)
    try:
        bound = release_memory.binding(
            root,
            inputs,
            bundle["profile"]["inputs"],
            provider=bundle["profile"]["identity"]["provider"],
        )
        try:
            memory = release_memory.read_memory(root, bound)
        except release_memory.ReleaseMemoryError as exc:
            if (
                exc.code == "binding_mismatch"
                or exc.code in release_memory.RECOVERABLE_MEMORY_ERRORS
            ):
                memory = None
            else:
                raise
        if (
            memory is not None
            and memory["profile_sha256"] != bundle["profile_sha256"]
        ):
            memory = None
    except release_memory.ReleaseMemoryError as exc:
        raise ReleaseProfileError(exc.code) from exc
    paths = _state_paths(root)
    receipt = _load_optional(paths["run"], "run")
    next_generation = 1
    if receipt is not None and _superseded_run(receipt, bundle):
        next_generation = max(1, receipt.get("generation", 0) + 1)
        receipt = None
    if receipt is None:
        receipt = _v2_empty_run(
            root,
            bundle,
            resolved_sha256,
            input_sha256,
            next_generation,
            bound,
        )
        _persist_run(root, receipt)
    else:
        _validate_run(receipt, bundle)
        if receipt["status"] == "completed":
            if not new:
                if (
                    receipt["input_sha256"] != input_sha256
                    or receipt["resolved_profile_sha256"] != resolved_sha256
                    or receipt["binding"] != bound
                ):
                    raise ReleaseProfileError("run_input_mismatch")
                _validate_run(receipt, bundle, resolved)
                try:
                    _write_v2_memory(
                        root,
                        bundle,
                        resolved_sha256,
                        bound,
                        receipt,
                        memory,
                        {
                            "kimiflow_control": 0,
                            "project_checks": 0,
                            "build": 0,
                            "provider": 0,
                        },
                    )
                except release_memory.ReleaseMemoryError as exc:
                    raise ReleaseProfileError(exc.code) from exc
                return receipt
            receipt = _v2_empty_run(
                root,
                bundle,
                resolved_sha256,
                input_sha256,
                receipt["generation"] + 1,
                bound,
            )
            _persist_run(root, receipt)
        else:
            if (
                receipt["input_sha256"] != input_sha256
                or receipt["resolved_profile_sha256"] != resolved_sha256
                or receipt["binding"] != bound
            ):
                raise ReleaseProfileError("run_input_mismatch")
            _validate_run(receipt, bundle, resolved)
            if new:
                raise ReleaseProfileError("run_already_active")
            if receipt["status"] == "audit_required":
                raise ReleaseProfileError("release_failure")
            receipt["status"] = "active"

    _require_run_head(root, receipt)
    metrics = release_memory.metrics_template()
    control_timer = release_memory.Timer()
    control_finalized = False
    credentials = {}
    try:
        credentials, identity = release_memory.resolve_identity(
            root,
            bundle["profile"]["identity"],
            memory=memory,
            repository=release_memory.publication_repository(
                inputs, bundle["profile"]["inputs"]
            ),
            expected_tool_sha256=resolved_tool_fingerprints.get(
                "identity:github"
            ),
        )
        receipt["identity"] = identity
        metrics["counts"]["resolver_probes"] += identity["resolver_probes"]
        _persist_run(root, receipt)

        def execute(command):
            _require_relative_inputs_current(
                root, bundle["profile"], inputs
            )
            _require_controls_current(root, bundle)
            _require_profile_tools_current(root, bundle)
            _require_command_tools_current(
                root,
                command,
                resolved_tool_fingerprints[command["id"]],
            )
            _require_relative_inputs_current(
                root, bundle["profile"], inputs
            )
            return _execute_v2(root, command, credentials=credentials)

        for index, step in enumerate(resolved["steps"]):
            _require_run_head(root, receipt)
            _require_controls_current(root, bundle)
            row = receipt["steps"][index]
            if row["status"] == "completed":
                if step["kind"] == "effect":
                    continue
                if _resume_context_matches(
                    root,
                    bundle["profile"],
                    inputs,
                    step,
                    row["resume_context_sha256"],
                ):
                    continue
                row.update(
                    {
                        "status": "pending",
                        "evidence": None,
                        "failure_class": None,
                        "failure_audit_sha256s": [],
                        "resume_context_sha256": None,
                    }
                )
                _persist_run(root, receipt)
            if step["kind"] == "check":
                evidence = _current_evidence(
                    root, bundle, step, inputs
                )
                reused = evidence is not None
                if evidence is None:
                    evidence = execute(step)
                _add_v2_metrics(metrics, evidence, step, reused=reused)
                row["evidence"] = evidence
                if evidence["exit_code"] != 0:
                    _v2_failure(
                        root, bundle, receipt, row, evidence, "step_failed"
                    )
                row["failure_class"] = None
                row["failure_audit_sha256s"] = []
                row["status"] = "completed"
                row["resume_context_sha256"] = _resume_context_sha256(
                    root, bundle["profile"], inputs, step
                )
                _persist_run(root, receipt)
                continue

            if row["status"] in ("pending", "retryable", "failed"):
                precondition = execute(step["precondition"])
                _add_v2_metrics(metrics, precondition, step["precondition"])
                row["evidence"] = {"precondition": precondition}
                if precondition["exit_code"] != 0:
                    _v2_failure(
                        root,
                        bundle,
                        receipt,
                        row,
                        precondition,
                        "precondition_failed",
                    )
                artifact_snapshot = _scan_release_artifacts(
                    root,
                    bundle["profile"],
                    inputs,
                    step,
                    credentials,
                )
                _require_controls_current(root, bundle)
                row["status"] = "started"
                _persist_run(root, receipt)
                if not _release_artifact_snapshot_current(
                    root,
                    bundle["profile"],
                    inputs,
                    step,
                    artifact_snapshot,
                ):
                    raise ReleaseProfileError("release_artifact_drift")
                effect = execute(step)
                _add_v2_metrics(metrics, effect, step)
                row["evidence"]["effect"] = effect
                if effect["failure_class"] is not None:
                    row["failure_class"] = effect["failure_class"]
                _persist_run(root, receipt)

            effect_evidence = (row.get("evidence") or {}).get("effect")
            if (
                effect_evidence is not None
                and effect_evidence["exit_code"] != 0
                and not any(
                    _failure_audit_matches(
                        bundle, marker, row["id"], effect_evidence
                    )
                    for marker in row["failure_audit_sha256s"]
                )
            ):
                _failure(root, bundle, step["id"], effect_evidence)
                receipt["status"] = "audit_required"
                _persist_run(root, receipt)
                raise ReleaseProfileError("effect_reported_failure")

            postcondition = execute(step["postcondition"])
            _add_v2_metrics(metrics, postcondition, step["postcondition"])
            row.setdefault("evidence", {})["postcondition"] = postcondition
            if postcondition["exit_code"] != 0:
                # A remote effect may have happened.  Its postcondition is the
                # only safe resume action and uncertainty remains fail closed.
                row["status"] = "started"
                row["failure_class"] = postcondition["failure_class"]
                receipt["status"] = (
                    "retryable_failure"
                    if _retryable(postcondition["failure_class"])
                    else "audit_required"
                )
                if receipt["status"] == "audit_required":
                    _failure(root, bundle, step["id"], postcondition)
                _persist_run(root, receipt)
                raise ReleaseProfileError(
                    "retryable_failure"
                    if receipt["status"] == "retryable_failure"
                    else "postcondition_failed"
                )
            row["status"] = "completed"
            _persist_run(root, receipt)

        for index, check in enumerate(resolved["final_checks"]):
            _require_run_head(root, receipt)
            _require_controls_current(root, bundle)
            row = receipt["final_checks"][index]
            if row["status"] == "completed":
                if _resume_context_matches(
                    root,
                    bundle["profile"],
                    inputs,
                    check,
                    row["resume_context_sha256"],
                ):
                    continue
                row.update(
                    {
                        "status": "pending",
                        "evidence": None,
                        "failure_class": None,
                        "failure_audit_sha256s": [],
                        "resume_context_sha256": None,
                    }
                )
                _persist_run(root, receipt)
            evidence = _current_evidence(
                root, bundle, check, inputs
            )
            reused = evidence is not None
            if evidence is None:
                evidence = execute(check)
            _add_v2_metrics(metrics, evidence, check, reused=reused)
            row["evidence"] = evidence
            if evidence["exit_code"] != 0:
                _v2_failure(
                    root, bundle, receipt, row, evidence, "final_check_failed"
                )
            row["failure_class"] = None
            row["failure_audit_sha256s"] = []
            row["status"] = "completed"
            row["resume_context_sha256"] = _resume_context_sha256(
                root, bundle["profile"], inputs, check
            )
            _persist_run(root, receipt)

        _require_run_head(root, receipt)
        _require_controls_current(root, bundle)
        _finalize_v2_control_duration(metrics, control_timer)
        control_finalized = True
        _write_v2_memory(
            root,
            bundle,
            resolved_sha256,
            bound,
            receipt,
            memory,
            metrics["duration_milliseconds"],
        )
        receipt["completion_sha256"] = _value_sha(
            {
                key: receipt[key]
                for key in (
                    "profile_sha256", "resolved_profile_sha256",
                    "input_sha256", "control_set_sha256", "binding", "head",
                    "generation", "identity", "steps", "final_checks",
                )
            }
        )
        receipt["status"] = "completed"
        _persist_run(root, receipt)
        return receipt
    except release_memory.ReleaseMemoryError as exc:
        metrics["counts"]["resolver_probes"] += exc.probes
        raise ReleaseProfileError(exc.code) from exc
    finally:
        if not control_finalized:
            _finalize_v2_control_duration(metrics, control_timer)
        try:
            release_memory.write_metrics(root, metrics)
        except release_memory.ReleaseMemoryError as exc:
            raise ReleaseProfileError(exc.code) from exc


def run_profile(root, authorize=False, write=False, new=False, inputs=None):
    root = workspace_root(root)
    if not write:
        raise ReleaseProfileError("write_required")
    with _release_lock(root):
        bundle = _load_bundle(root)
        if bundle["profile"]["schema_version"] == 2:
            readiness = status(root)
            if readiness.get("status") != "ready":
                raise ReleaseProfileError(
                    readiness.get("reason") or readiness["status"]
                )
            return _run_profile_v2_locked(
                root,
                bundle,
                authorize=authorize,
                inputs=inputs or {},
                new=new,
            )
        return _run_profile_locked(root, authorize=authorize, new=new)


def _parser():
    parser = argparse.ArgumentParser(
        description="Kimiflow local project release-profile control plane"
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--pretty", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    discovery = subparsers.add_parser("discover")
    discovery.add_argument("--write", action="store_true")
    discovery.add_argument("--include", action="append", default=[])
    validate = subparsers.add_parser("validate")
    validate.add_argument("--candidate", required=True)
    validate.add_argument("--audit", required=True)
    adopt_parser = subparsers.add_parser("adopt")
    adopt_parser.add_argument("--candidate", required=True)
    adopt_parser.add_argument("--audit", required=True)
    adopt_parser.add_argument("--write", action="store_true")
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--prefer-v2", action="store_true")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--authorize", action="store_true")
    run_parser.add_argument("--write", action="store_true")
    run_parser.add_argument("--new", action="store_true")
    run_parser.add_argument("--input", action="append", default=[])
    evidence_parser = subparsers.add_parser("evidence-execute")
    evidence_parser.add_argument("--run", required=True)
    evidence_parser.add_argument("--check", required=True)
    evidence_parser.add_argument("--input", action="append", default=[])
    evidence_parser.add_argument("--write", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "discover":
            result = discover(
                args.root, write=args.write, includes=args.include
            )
        elif args.command == "validate":
            result = adopt(
                args.root, args.candidate, args.audit, write=False
            )
        elif args.command == "adopt":
            result = adopt(
                args.root, args.candidate, args.audit, write=args.write
            )
        elif args.command == "status":
            result = status(args.root, prefer_v2=args.prefer_v2)
        elif args.command == "evidence-execute":
            result = evidence_execute(
                args.root,
                args.run,
                args.check,
                inputs=_parse_inputs(args.input),
                write=args.write,
            )
        else:
            result = run_profile(
                args.root,
                authorize=args.authorize,
                write=args.write,
                new=args.new,
                inputs=_parse_inputs(args.input),
            )
        print(
            json.dumps(
                result,
                ensure_ascii=True,
                sort_keys=True,
                indent=2 if args.pretty else None,
            )
        )
        return 0
    except ReleaseProfileError as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "error",
                    "reason": exc.code,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
