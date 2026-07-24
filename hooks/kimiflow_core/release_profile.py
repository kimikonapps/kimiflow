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
import stat
import subprocess
import sys
import tempfile

from .atomic import atomic_write


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_DISCOVERY_FILES = 128
MAX_EXPLICIT_SOURCES = 32
MAX_SOURCE_BYTES = 1024 * 1024
MAX_DISCOVERY_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SECRET_VALUE_RE = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|"
    r"(?:token|password|passwd|secret|api[_-]?key)=[^$<][^\s]{7,})",
    re.IGNORECASE,
)
SECRET_PATH_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.|$)|.*\.(?:pem|key|p12|pfx)$|"
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
        return subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
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


def _validate_argv(value, label, probe=False):
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
                break
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


def _validate_probe(value, label):
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
    _validate_argv(value.get("argv"), label, probe=True)
    return value


def validate_profile(profile):
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


def _command_input_path(root, cwd, value, required):
    if not isinstance(value, str) or not value:
        raise ReleaseProfileError("command_local_input_unsafe")
    if posixpath.isabs(value):
        if required:
            raise ReleaseProfileError("command_local_input_unsafe")
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


def _direct_local_inputs(root, command):
    normalized = _unwrap_env(command["argv"])
    if not normalized:
        raise ReleaseProfileError("command_wrapper_invalid")
    candidates = []
    executable = normalized[0]
    if "/" in executable:
        path = _command_input_path(
            root, command["cwd"], executable, required=True
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
            root, command["cwd"], script, required=True
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
        for path in _direct_local_inputs(root, command):
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
        None if failure is None else _value_sha(_validate_failure(failure))
    )
    audit = validate_audit(
        _read_json(audit_path, "audit"),
        profile,
        discovery,
        expected_failure_sha256=failure_sha256,
    )
    return profile, audit, discovery


def _bundle(profile, audit, discovery):
    return {
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


def _load_bundle(root):
    value = _read_json(_state_paths(root)["profile"], "adopted_profile")
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "document_type", "profile_sha256", "audit_sha256",
        "discovery_sha256", "control_set_sha256", "profile", "audit",
        "discovery",
    }:
        raise ReleaseProfileError("adopted_profile_shape_invalid")
    if (
        value.get("schema_version") != 1
        or value.get("document_type") != "adopted_release_profile"
    ):
        raise ReleaseProfileError("adopted_profile_version_invalid")
    profile = validate_profile(value.get("profile"))
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
    _validate_command_bindings(root, profile, discovery)
    return value


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


def status(root):
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
        failure = _validate_failure(failure)
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
    }
    receipt = _load_optional(paths["run"], "run")
    if receipt is not None:
        try:
            _validate_run(receipt, bundle)
        except ReleaseProfileError as exc:
            return {
                "schema_version": 1,
                "status": "invalid",
                "reason": exc.code,
            }
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
    return result


def _run_has_started_effect(receipt):
    return any(
        row.get("kind") == "effect"
        and row.get("status") in ("started", "effect_failed", "completed")
        for row in receipt.get("steps", [])
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
        new_bundle = _bundle(profile, audit, discovery)
        existing_run = _load_optional(paths["run"], "run")
        if existing_run is not None:
            try:
                current_bundle = _load_bundle(root)
                _validate_run(existing_run, current_bundle)
            except ReleaseProfileError as exc:
                raise ReleaseProfileError("existing_run_invalid") from exc
            if (
                existing_run["status"] != "completed"
                and existing_run["profile_sha256"] != new_bundle["profile_sha256"]
                and _run_has_started_effect(existing_run)
            ):
                raise ReleaseProfileError("uncertain_run_profile_conflict")
        if (
            existing_run is not None
            and existing_run["profile_sha256"] != new_bundle["profile_sha256"]
        ):
            os.unlink(paths["run"])
            existing_run = None
        _write_local(paths["profile"], new_bundle)
        if existing_run is not None:
            changed = False
            for row in existing_run.get("steps", []) + existing_run.get("final_checks", []):
                if row.get("status") in ("failed", "precondition_failed"):
                    row["status"] = "pending"
                    row["evidence"] = None
                    changed = True
                elif row.get("status") == "effect_failed":
                    row["status"] = "completed"
                    changed = True
            if existing_run.get("status") == "audit_required":
                existing_run["status"] = "active"
                changed = True
            if changed:
                _write_local(paths["run"], existing_run)
        if os.path.exists(paths["failure"]):
            os.unlink(paths["failure"])
        return {
            "schema_version": 1,
            "status": "adopted",
            "profile_sha256": new_bundle["profile_sha256"],
            "audit_sha256": _value_sha(audit),
            "finding_count": len(audit["findings"]),
        }


def _command_digest(command):
    return _value_sha(
        {
            "argv": command["argv"],
            "cwd": command["cwd"],
            "timeout_seconds": command["timeout_seconds"],
        }
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


def _validate_run(receipt, bundle):
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
    _write_local(paths["failure"], value)


def _persist_run(root, receipt):
    _write_local(_state_paths(root)["run"], receipt)


def _require_controls_current(root, bundle):
    if not _controls_current(root, bundle["profile"]):
        raise ReleaseProfileError("control_drift")


def _run_profile_locked(root, authorize, new=False):
    if authorize is not True:
        raise ReleaseProfileError("authorization_required")
    readiness = status(root)
    if readiness.get("status") != "ready":
        raise ReleaseProfileError(readiness.get("reason") or readiness["status"])
    bundle = _load_bundle(root)
    paths = _state_paths(root)
    receipt = _load_optional(paths["run"], "run")
    if receipt is None:
        receipt = _empty_run(root, bundle, 1)
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


def run_profile(root, authorize=False, write=False, new=False):
    root = workspace_root(root)
    if not write:
        raise ReleaseProfileError("write_required")
    with _release_lock(root):
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
    subparsers.add_parser("status")
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--authorize", action="store_true")
    run_parser.add_argument("--write", action="store_true")
    run_parser.add_argument("--new", action="store_true")
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
            result = status(args.root)
        else:
            result = run_profile(
                args.root,
                authorize=args.authorize,
                write=args.write,
                new=args.new,
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
