"""Private, content-free state for release identity, evidence and economics.

This module deliberately does not use Kimiflow recall, Vault or capsules.  Its
files are local control-plane receipts, keyed to the current Git repository,
worktree and publication target.  Credential values are returned to the caller
in memory only and are never part of a serializable result.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time

from .atomic import atomic_write


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CREDENTIAL_NAME_RE = re.compile(
    r"(?:^|_)(?:auth|credential|key|password|secret|token)(?:_|$)",
    re.IGNORECASE,
)
_SECRET_PATH_RE = re.compile(
    r"(?:^|/)(?:\.env(?:\.|$)|.*\.(?:pem|key|p12|pfx)$|"
    r"(?:secrets?|credentials?)(?:[./_-]|$))",
    re.IGNORECASE,
)
_MAX_FAILURE_CLASSES = 16
_MAX_SUCCESSFUL_STEPS = 64
_MAX_GIT_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_EVIDENCE_FILE_BYTES = 64 * 1024 * 1024
_MAX_TOOL_BYTES = 256 * 1024 * 1024
_SYSTEM_EXECUTABLE_PATH = os.defpath
RECOVERABLE_MEMORY_ERRORS = frozenset(
    {
        "memory_malformed",
        "memory_shape_invalid",
        "memory_version_invalid",
        "memory_contract_invalid",
    }
)
_FIXED_ENVIRONMENT = {
    "CI": "1",
    "GIT_ASKPASS": "true",
    "GIT_TERMINAL_PROMPT": "0",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
}
_RESERVED_ENVIRONMENT = {
    "BASH_ENV", "CLASSPATH", "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH", "ENV", "GIT_ALLOW_PROTOCOL",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_ASKPASS",
    "GIT_CEILING_DIRECTORIES", "GIT_COMMON_DIR", "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM", "GIT_DIFF_OPTS", "GIT_DIR", "GIT_EXEC_PATH",
    "GH_CONFIG_DIR", "GH_ENTERPRISE_TOKEN", "GH_HOST",
    "GIT_EXTERNAL_DIFF", "GIT_INDEX_FILE", "GIT_NO_LAZY_FETCH",
    "GIT_OBJECT_DIRECTORY", "GIT_OPTIONAL_LOCKS", "GIT_PAGER",
    "GIT_PROXY_COMMAND", "GIT_SSH", "GIT_SSH_COMMAND",
    "GIT_TERMINAL_PROMPT", "GIT_WORK_TREE", "HOME", "JAVA_TOOL_OPTIONS",
    "JDK_JAVA_OPTIONS", "LD_LIBRARY_PATH", "LD_PRELOAD", "NODE_OPTIONS",
    "NODE_PATH",
    "NPM_CONFIG_GLOBAL", "NPM_CONFIG_GLOBALCONFIG", "NPM_CONFIG_PREFIX",
    "NPM_CONFIG_USERCONFIG", "NPM_CONFIG_WORKSPACE",
    "NPM_CONFIG_WORKSPACES", "PAGER", "PATH", "PERL5LIB", "PERL5OPT",
    "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "RUBYLIB", "RUBYOPT",
    "SSH_ASKPASS", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR", "XDG_STATE_HOME", "YARN_PROJECT_CWD", "YARN_RC_FILENAME",
    "ZDOTDIR", "_JAVA_OPTIONS",
} | set(_FIXED_ENVIRONMENT)
_RESERVED_ENVIRONMENT_PREFIXES = (
    "BASH_FUNC_", "GIT_CONFIG_", "GIT_TRACE",
)


class ReleaseMemoryError(ValueError):
    def __init__(self, code, probes=0):
        super().__init__(code)
        self.code = code
        self.probes = probes


def internal_git_executable():
    """Return Git from the fixed system path, never from ambient PATH."""
    for directory in _SYSTEM_EXECUTABLE_PATH.split(os.pathsep):
        if not os.path.isabs(directory):
            continue
        candidate = os.path.realpath(os.path.join(directory, "git"))
        try:
            info = os.stat(candidate, follow_symlinks=False)
            parent = os.stat(
                os.path.dirname(candidate), follow_symlinks=False
            )
        except OSError:
            continue
        if (
            stat.S_ISREG(info.st_mode)
            and not info.st_mode & 0o022
            and stat.S_ISDIR(parent.st_mode)
            and not parent.st_mode & 0o022
            and os.access(candidate, os.X_OK)
        ):
            return candidate
    raise ReleaseMemoryError("git_identity_unavailable")


def _durable_write(path, data, mode):
    atomic_write(path, data, mode=mode)
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(os.path.dirname(path), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise ReleaseMemoryError("memory_persist_failed") from exc


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value):
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _sha_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha_file(path, maximum):
    try:
        info = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size < 0
            or info.st_size > maximum
        ):
            raise ReleaseMemoryError("evidence_file_oversize")
        digest_value = hashlib.sha256()
        size = 0
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    raise ReleaseMemoryError("evidence_file_oversize")
                digest_value.update(chunk)
        return "sha256:" + digest_value.hexdigest()
    except ReleaseMemoryError:
        raise
    except OSError as exc:
        raise ReleaseMemoryError("evidence_path_unavailable") from exc


def _git_bytes(root, args, maximum=_MAX_GIT_OUTPUT_BYTES):
    try:
        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(
                [internal_git_executable(), "-C", root, *args],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=10,
                env=scrub_environment(os.environ),
                check=False,
            )
            size = output.tell()
            if completed.returncode or size > maximum:
                raise ReleaseMemoryError("evidence_git_unavailable")
            output.seek(0)
            return output.read()
    except ReleaseMemoryError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseMemoryError("evidence_git_unavailable") from exc


def _paths(root):
    directory = os.path.join(root, ".kimiflow", "release")
    return {
        "directory": directory,
        "salt": os.path.join(directory, ".identity-salt"),
        "memory": os.path.join(directory, "MEMORY.json"),
        "metrics": os.path.join(directory, "METRICS.json"),
        "evidence": os.path.join(directory, "evidence"),
    }


def _ensure_directory(path):
    if os.path.lexists(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ReleaseMemoryError("memory_path_unsafe")
    else:
        os.mkdir(path, 0o700)
    os.chmod(path, 0o700)


def ensure_storage(root):
    paths = _paths(root)
    _ensure_directory(os.path.join(root, ".kimiflow"))
    _ensure_directory(paths["directory"])
    _ensure_directory(paths["evidence"])
    return paths


class _LeasedTemporaryDirectory:
    def __init__(self, base, prefix):
        self.name = tempfile.mkdtemp(prefix=prefix, dir=base)
        os.chmod(self.name, 0o700)
        lease = os.path.join(self.name, ".lease")
        self._lease = os.open(
            lease, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600
        )
        fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __enter__(self):
        return self.name

    def __exit__(self, _kind, _value, _traceback):
        self.cleanup()

    def cleanup(self):
        if self._lease is None:
            return
        try:
            shutil.rmtree(self.name)
        finally:
            os.close(self._lease)
            self._lease = None


def _cleanup_stale_temporary_directories(base, prefix):
    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in names:
        if not name.startswith(prefix):
            continue
        path = os.path.join(base, name)
        lease_path = os.path.join(path, ".lease")
        descriptor = None
        try:
            info = os.lstat(path)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
            ):
                continue
            try:
                descriptor = os.open(
                    lease_path,
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                )
            except FileNotFoundError:
                if time.time() - info.st_mtime < 24 * 60 * 60:
                    continue
            if descriptor is not None:
                lease_info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(lease_info.st_mode)
                    or lease_info.st_uid != os.getuid()
                ):
                    continue
                fcntl.flock(
                    descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            shutil.rmtree(path)
        except (BlockingIOError, OSError):
            continue
        finally:
            if descriptor is not None:
                os.close(descriptor)


def temporary_directory(root, prefix):
    """Create a private temporary directory outside the project tree."""
    project = os.path.realpath(root)
    candidates = [
        tempfile.gettempdir(),
        "/tmp",
        "/var/tmp",
    ]
    seen = set()
    for candidate in candidates:
        base = os.path.realpath(candidate)
        if base in seen:
            continue
        seen.add(base)
        try:
            if (
                not os.path.isdir(base)
                or not os.access(base, os.W_OK | os.X_OK)
                or os.path.commonpath((project, base)) == project
            ):
                continue
        except (OSError, ValueError):
            continue
        _cleanup_stale_temporary_directories(base, prefix)
        return _LeasedTemporaryDirectory(base, prefix)
    raise ReleaseMemoryError("identity_temp_unavailable")


def _read_regular(path):
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ReleaseMemoryError("memory_file_unsafe")
        if info.st_mode & 0o077:
            raise ReleaseMemoryError("memory_mode_unsafe")
        if info.st_size > 1024 * 1024:
            raise ReleaseMemoryError("memory_oversize")
        with open(path, "rb") as handle:
            return handle.read()
    except ReleaseMemoryError:
        raise
    except OSError as exc:
        raise ReleaseMemoryError("memory_unreadable") from exc


def _salt(root):
    paths = ensure_storage(root)
    if not os.path.exists(paths["salt"]):
        atomic_write(paths["salt"], secrets.token_hex(32) + "\n", mode=0o600)
    payload = _read_regular(paths["salt"]).strip()
    if not re.fullmatch(rb"[0-9a-f]{64}", payload):
        raise ReleaseMemoryError("memory_salt_invalid")
    return payload


def _git_directories(root):
    environment = scrub_environment(os.environ)
    try:
        result = subprocess.run(
            [
                internal_git_executable(), "-C", root, "rev-parse",
                "--path-format=absolute",
                "--git-common-dir", "--absolute-git-dir",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseMemoryError("git_identity_unavailable") from exc
    if result.returncode or len(result.stdout) > 4096:
        raise ReleaseMemoryError("git_identity_unavailable")
    try:
        values = result.stdout.decode("utf-8", "strict").splitlines()
    except UnicodeError as exc:
        raise ReleaseMemoryError("git_identity_unavailable") from exc
    if len(values) != 2 or any(not os.path.isabs(value) for value in values):
        raise ReleaseMemoryError("git_identity_unavailable")
    return tuple(os.path.realpath(value) for value in values)


def project_identity(root):
    """Return salted path-free repository/worktree pseudonyms."""
    salt = _salt(root)
    common, private = _git_directories(root)

    def pseudonym(prefix, value):
        return prefix + hashlib.sha256(
            salt + b"\0" + os.fsencode(value)
        ).hexdigest()

    return {
        "repository_id": pseudonym("repo_", common),
        "worktree_id": pseudonym("wt_", private),
    }


def target_digest(inputs, declarations):
    names = sorted(
        row["name"] for row in declarations if row.get("publication_target")
    )
    return digest({name: inputs[name] for name in names})


def canonical_github_repository(value):
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    candidate = re.sub(r"^git@github\.com:", "", candidate)
    candidate = re.sub(r"^https?://github\.com/", "", candidate)
    candidate = re.sub(r"^ssh://git@github\.com/", "", candidate)
    candidate = re.sub(r"\.git$", "", candidate).strip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", candidate):
        return candidate.lower()
    return None


def current_github_repository(root):
    environment = scrub_environment(os.environ)
    try:
        result = subprocess.run(
            [
                internal_git_executable(), "-C", root, "remote",
                "get-url", "origin",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode or len(result.stdout) > 4096:
        return None
    try:
        return canonical_github_repository(
            result.stdout.decode("utf-8", "strict").strip()
        )
    except UnicodeError:
        return None


def binding(root, inputs, declarations, provider=None):
    identity = project_identity(root)
    result = {
        "repository_id": identity["repository_id"],
        "worktree_id": identity["worktree_id"],
        "target_sha256": target_digest(inputs, declarations),
    }
    if provider == "github":
        repository_values = [
            inputs[row["name"]]
            for row in declarations
            if row.get("publication_target") and row.get("type") == "repository"
        ]
        if len(repository_values) != 1:
            raise ReleaseMemoryError("binding_mismatch")
        declared = canonical_github_repository(repository_values[0])
        current = current_github_repository(root)
        if declared is None or current is None or declared != current:
            raise ReleaseMemoryError("binding_mismatch")
    return result


def publication_repository(inputs, declarations):
    values = [
        inputs[row["name"]]
        for row in declarations
        if row.get("publication_target") and row.get("type") == "repository"
    ]
    if len(values) != 1:
        return None
    return canonical_github_repository(values[0])


def _validate_duration_totals(value):
    return (
        isinstance(value, dict)
        and set(value).issubset(
            {"kimiflow_control", "project_checks", "build", "provider"}
        )
        and all(
            isinstance(item, dict)
            and set(item) == {"runs", "milliseconds"}
            and not isinstance(item["runs"], bool)
            and isinstance(item["runs"], int)
            and 0 <= item["runs"] <= 2**31 - 1
            and not isinstance(item["milliseconds"], bool)
            and isinstance(item["milliseconds"], int)
            and 0 <= item["milliseconds"] <= 2**63 - 1
            for item in value.values()
        )
    )


def valid_environment_name(name, credential=False):
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name) is None
        or name in _RESERVED_ENVIRONMENT
        or name.startswith(_RESERVED_ENVIRONMENT_PREFIXES)
    ):
        return False
    return credential or _CREDENTIAL_NAME_RE.search(name) is None


def _credential_value_valid(value):
    if not isinstance(value, str) or "\x00" in value or "\n" in value:
        return False
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        return False
    return 8 <= size <= 16384


def read_memory(root, expected_binding):
    path = _paths(root)["memory"]
    if not os.path.exists(path):
        return None
    try:
        value = json.loads(_read_regular(path).decode("utf-8", "strict"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseMemoryError("memory_malformed") from exc
    required_v1 = {
        "schema_version", "binding", "profile_sha256",
        "resolved_profile_sha256", "identity", "successful_steps",
        "failure_classes", "duration_totals",
    }
    required_v2 = required_v1 | {"generation"}
    if (
        not isinstance(value, dict)
        or (
            value.get("schema_version") == 1
            and set(value) != required_v1
        )
        or (
            value.get("schema_version") == 2
            and set(value) != required_v2
        )
        or value.get("schema_version") not in (1, 2)
    ):
        raise ReleaseMemoryError("memory_shape_invalid")
    if value["schema_version"] == 1:
        value = dict(value, generation=0, schema_version=2)
    if value.get("binding") != expected_binding:
        raise ReleaseMemoryError("binding_mismatch")
    if (
        isinstance(value.get("generation"), bool)
        or not isinstance(value.get("generation"), int)
        or value["generation"] < 0
        or not _DIGEST_RE.fullmatch(value.get("profile_sha256", ""))
        or not _DIGEST_RE.fullmatch(value.get("resolved_profile_sha256", ""))
        or not isinstance(value.get("identity"), dict)
        or set(value["identity"]) != {"kind", "account_sha256"}
        or value["identity"]["kind"] not in ("environment", "github_native", "github_cli")
        or not _DIGEST_RE.fullmatch(value["identity"].get("account_sha256", ""))
        or not isinstance(value.get("successful_steps"), list)
        or len(value["successful_steps"]) > _MAX_SUCCESSFUL_STEPS
        or any(not isinstance(item, str) for item in value["successful_steps"])
        or not isinstance(value.get("failure_classes"), dict)
        or len(value["failure_classes"]) > _MAX_FAILURE_CLASSES
        or any(
            not isinstance(key, str)
            or isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 <= count <= 2**31 - 1
            for key, count in value["failure_classes"].items()
        )
        or not _validate_duration_totals(value.get("duration_totals"))
    ):
        raise ReleaseMemoryError("memory_contract_invalid")
    return value


def write_verified_memory(
    root,
    expected_binding,
    profile_sha256,
    resolved_profile_sha256,
    identity,
    successful_steps,
    generation=1,
    failure_classes=None,
    duration_totals=None,
    previous_duration_totals=None,
):
    """Persist only verified, bounded, content-free learning."""
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise ReleaseMemoryError("memory_generation_invalid")
    if identity.get("kind") not in ("environment", "github_native", "github_cli"):
        raise ReleaseMemoryError("identity_invalid")
    account_sha256 = identity.get("account_sha256")
    if not _DIGEST_RE.fullmatch(account_sha256 or ""):
        raise ReleaseMemoryError("identity_invalid")
    successful = sorted(set(successful_steps))[:_MAX_SUCCESSFUL_STEPS]
    try:
        existing = read_memory(root, expected_binding)
    except ReleaseMemoryError as exc:
        if (
            exc.code != "binding_mismatch"
            and exc.code not in RECOVERABLE_MEMORY_ERRORS
        ):
            raise
        existing = None
    if (
        existing is not None
        and existing["generation"] == generation
        and existing["profile_sha256"] == profile_sha256
        and existing["resolved_profile_sha256"]
        == resolved_profile_sha256
    ):
        if (
            existing["identity"]
            != {
                "kind": identity["kind"],
                "account_sha256": account_sha256,
            }
            or existing["successful_steps"] != successful
        ):
            raise ReleaseMemoryError("memory_generation_conflict")
        return existing
    if (
        existing is not None
        and existing["profile_sha256"] == profile_sha256
        and existing["generation"] > generation
    ):
        raise ReleaseMemoryError("memory_generation_conflict")
    counters = {}
    for key, count in sorted((failure_classes or {}).items())[:_MAX_FAILURE_CLASSES]:
        if isinstance(key, str) and isinstance(count, int) and not isinstance(count, bool):
            counters[key] = min(max(0, count), 2**31 - 1)
    totals = {}
    previous_duration_totals = previous_duration_totals or {}
    for key, item in sorted((duration_totals or {}).items()):
        if (
            key not in {
                "kimiflow_control", "project_checks", "build", "provider"
            }
            or isinstance(item, bool)
            or not isinstance(item, (int, float))
        ):
            continue
        previous = previous_duration_totals.get(
            key, {"runs": 0, "milliseconds": 0}
        )
        if (
            not isinstance(previous, dict)
            or set(previous) != {"runs", "milliseconds"}
        ):
            previous = {"runs": 0, "milliseconds": 0}
        totals[key] = {
            "runs": min(max(0, int(previous["runs"])) + 1, 2**31 - 1),
            "milliseconds": min(
                max(0, int(previous["milliseconds"]))
                + max(0, int(item)),
                2**63 - 1,
            ),
        }
    value = {
        "schema_version": 2,
        "generation": generation,
        "binding": expected_binding,
        "profile_sha256": profile_sha256,
        "resolved_profile_sha256": resolved_profile_sha256,
        "identity": {
            "kind": identity["kind"],
            "account_sha256": account_sha256,
        },
        "successful_steps": successful,
        "failure_classes": counters,
        "duration_totals": totals,
    }
    ensure_storage(root)
    _durable_write(
        _paths(root)["memory"],
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        0o600,
    )
    return value


def scrub_environment(environment, preserve_user_config=False):
    """Copy an environment without credentials or loader/config injection."""
    unsafe_exact = _RESERVED_ENVIRONMENT | {
        "BASH_ENV", "ENV", "GH_ENTERPRISE_TOKEN", "GH_TOKEN",
        "GITHUB_TOKEN", "GIT_ASKPASS", "LD_PRELOAD", "NODE_OPTIONS",
        "PYTHONHOME", "PYTHONPATH", "RUBYOPT", "SSH_ASKPASS", "ZDOTDIR",
    }
    result = {}
    for name, value in environment.items():
        upper = name.upper()
        if (
            upper in unsafe_exact
            or _CREDENTIAL_NAME_RE.search(upper)
            or upper.startswith(_RESERVED_ENVIRONMENT_PREFIXES)
        ):
            continue
        result[name] = value
    result["PATH"] = environment.get("PATH", os.defpath)
    if preserve_user_config:
        for name in ("HOME", "XDG_CONFIG_HOME"):
            if name in environment:
                result[name] = environment[name]
    result.update(_FIXED_ENVIRONMENT)
    return result


def sealed_environment(environment, declared_public=None, credentials=None, home=None):
    """Create the deterministic v2 command environment."""
    declared_public = set(declared_public or ())
    result = {
        "PATH": environment.get("PATH", os.defpath),
        **_FIXED_ENVIRONMENT,
    }
    if home is not None:
        result.update(
            {
                "HOME": home,
                "XDG_CACHE_HOME": os.path.join(home, "cache"),
                "XDG_CONFIG_HOME": os.path.join(home, "config"),
                "XDG_DATA_HOME": os.path.join(home, "data"),
                "XDG_RUNTIME_DIR": os.path.join(home, "runtime"),
                "XDG_STATE_HOME": os.path.join(home, "state"),
            }
        )
    for name in sorted(declared_public):
        if not valid_environment_name(name, credential=False):
            raise ReleaseMemoryError("declared_environment_secret_like")
        if name in environment:
            result[name] = environment[name]
    for name, value in (credentials or {}).items():
        if not valid_environment_name(name, credential=True):
            raise ReleaseMemoryError("identity_environment_invalid")
        result[name] = value
    return result


def _require_identity_tool(
    argv, environment, expected_tool_sha256, cwd
):
    if expected_tool_sha256 is None:
        raise ReleaseMemoryError("identity_tool_unbound")
    if (
        tool_fingerprints(argv, environment, cwd=cwd)
        != expected_tool_sha256
    ):
        raise ReleaseMemoryError("identity_tool_drift")


def _run_json(
    argv,
    environment,
    runner,
    expected_tool_sha256,
    cwd,
):
    _require_identity_tool(
        argv, environment, expected_tool_sha256, cwd
    )
    try:
        result = runner(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseMemoryError("identity_unavailable") from exc
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ReleaseMemoryError("identity_unavailable")
    try:
        return json.loads(result.stdout.decode("utf-8", "strict"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseMemoryError("identity_unavailable") from exc


def _github_repository_capable(
    repository,
    token,
    environment,
    runner,
    expected_tool_sha256,
    cwd,
):
    if repository is None:
        return False
    capability_environment = scrub_environment(environment)
    capability_environment["GH_TOKEN"] = token
    with temporary_directory(cwd, "kimiflow-gh-") as sandbox:
        capability_environment.update(
            {
                "HOME": sandbox,
                "XDG_CACHE_HOME": os.path.join(sandbox, "cache"),
                "XDG_CONFIG_HOME": os.path.join(sandbox, "config"),
                "XDG_STATE_HOME": os.path.join(sandbox, "state"),
            }
        )
        _require_identity_tool(
            ["gh"], capability_environment, expected_tool_sha256, cwd
        )
        try:
            capability = runner(
                [
                    "gh", "api",
                    "--hostname", "github.com",
                    "repos/" + repository,
                    "--jq", ".permissions.push // false",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                env=capability_environment,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
    return (
        capability.returncode == 0
        and len(capability.stdout) <= 32
        and capability.stdout.decode("utf-8", "replace").strip() == "true"
    )


def resolve_identity(
    root,
    identity,
    memory=None,
    environment=None,
    runner=subprocess.run,
    repository=None,
    expected_tool_sha256=None,
):
    """Resolve credentials in memory and return separate public metadata."""
    environment = os.environ if environment is None else environment
    provider = identity.get("provider")
    probes = 0
    if provider == "environment":
        names = identity.get("environment", [])
        if (
            not isinstance(names, list)
            or not names
            or any(
                not valid_environment_name(name, credential=True)
                or name in {"GH_TOKEN", "GITHUB_TOKEN"}
                for name in names
            )
        ):
            raise ReleaseMemoryError("identity_environment_invalid")
        missing = [name for name in names if name not in environment]
        if missing:
            raise ReleaseMemoryError("identity_unavailable")
        credentials = {name: environment[name] for name in names}
        if any(
            not _credential_value_valid(value)
            for value in credentials.values()
        ):
            raise ReleaseMemoryError("identity_environment_invalid")
        metadata = {
            "kind": "environment",
            "account_sha256": digest({"names": sorted(names)}),
            "resolver_probes": probes,
        }
        return credentials, metadata
    if provider != "github":
        raise ReleaseMemoryError("identity_provider_invalid")

    native_name = next(
        (name for name in ("GITHUB_TOKEN", "GH_TOKEN") if environment.get(name)),
        None,
    )
    if native_name:
        if not _credential_value_valid(environment[native_name]):
            raise ReleaseMemoryError(
                "identity_unavailable", probes=probes
            )
        probes += 1
        if not _github_repository_capable(
            repository,
            environment[native_name],
            environment,
            runner,
            expected_tool_sha256,
            root,
        ):
            raise ReleaseMemoryError(
                "identity_unavailable", probes=probes
            )
        return (
            {"GH_TOKEN": environment[native_name]},
            {
                "kind": "github_native",
                "account_sha256": digest({"source": native_name}),
                "resolver_probes": probes,
            },
        )

    clean = scrub_environment(environment, preserve_user_config=True)
    probes += 1
    try:
        status = _run_json(
            [
                "gh", "auth", "status", "--hostname", "github.com",
                "--json", "hosts",
            ],
            clean,
            runner,
            expected_tool_sha256,
            root,
        )
    except ReleaseMemoryError as exc:
        raise ReleaseMemoryError(exc.code, probes=probes) from exc
    hosts = status.get("hosts", {}) if isinstance(status, dict) else {}
    accounts = []
    rows = hosts.get("github.com", []) if isinstance(hosts, dict) else []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            account = row.get("login", row.get("account"))
            if row.get("state") == "success" and isinstance(account, str):
                accounts.append(account)
    accounts = sorted(set(accounts))
    learned = None
    if memory and memory.get("identity", {}).get("kind") == "github_cli":
        learned_digest = memory["identity"]["account_sha256"]
        matches = [
            account for account in accounts
            if digest({"account": account}) == learned_digest
        ]
        if len(matches) == 1:
            learned = matches[0]
    if learned is None:
        if repository and len(accounts) > 1:
            probes += 1
            _require_identity_tool(
                ["gh"], clean, expected_tool_sha256, root
            )
            try:
                recent = runner(
                    [
                        "gh", "api",
                        "--hostname", "github.com",
                        "repos/" + repository + "/releases/latest",
                        "--jq", ".author.login",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5,
                    env=clean,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                recent = None
            if (
                recent is not None
                and recent.returncode == 0
                and len(recent.stdout) <= 1024
            ):
                try:
                    candidate = recent.stdout.decode("utf-8", "strict").strip()
                except UnicodeError:
                    candidate = ""
                if candidate in accounts:
                    learned = candidate
        if learned is None:
            if len(accounts) != 1 or repository is None:
                raise ReleaseMemoryError(
                    "identity_ambiguous", probes=probes
                )
            learned = accounts[0]
    if repository is None:
        raise ReleaseMemoryError("identity_ambiguous", probes=probes)
    probes += 1
    _require_identity_tool(
        ["gh"], clean, expected_tool_sha256, root
    )
    try:
        token = runner(
            [
                "gh", "auth", "token", "--hostname", "github.com",
                "--user", learned,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            env=clean,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseMemoryError(
            "identity_unavailable", probes=probes
        ) from exc
    if token.returncode or not token.stdout or len(token.stdout) > 16384:
        raise ReleaseMemoryError("identity_unavailable", probes=probes)
    try:
        token_value = token.stdout.decode("utf-8", "strict").strip()
    except UnicodeError as exc:
        raise ReleaseMemoryError(
            "identity_unavailable", probes=probes
        ) from exc
    if not _credential_value_valid(token_value):
        raise ReleaseMemoryError("identity_unavailable", probes=probes)
    probes += 1
    if not _github_repository_capable(
        repository,
        token_value,
        environment,
        runner,
        expected_tool_sha256,
        root,
    ):
        raise ReleaseMemoryError("identity_unavailable", probes=probes)
    return (
        {"GH_TOKEN": token_value},
        {
            "kind": "github_cli",
            "account_sha256": digest({"account": learned}),
            "resolver_probes": probes,
        },
    )


def _repo_regular_file(root, relative):
    root = os.path.realpath(root)
    current = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise ReleaseMemoryError("evidence_path_unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ReleaseMemoryError("evidence_path_unsafe")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ReleaseMemoryError("evidence_path_unsafe")
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseMemoryError("evidence_path_unsafe")
    try:
        if os.path.commonpath((root, os.path.realpath(current))) != root:
            raise ReleaseMemoryError("evidence_path_unsafe")
    except ValueError as exc:
        raise ReleaseMemoryError("evidence_path_unsafe") from exc
    return current


def _repo_directory(root, relative):
    root = os.path.realpath(root)
    current = root
    for part in relative.split("/"):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise ReleaseMemoryError("evidence_path_unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ReleaseMemoryError("evidence_path_unsafe")
    try:
        if os.path.commonpath((root, os.path.realpath(current))) != root:
            raise ReleaseMemoryError("evidence_path_unsafe")
    except ValueError as exc:
        raise ReleaseMemoryError("evidence_path_unsafe") from exc
    return current


def path_fingerprints(root, paths):
    result = {}
    for relative in sorted(set(paths)):
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
            or ".." in relative.split("/")
        ):
            raise ReleaseMemoryError("evidence_path_invalid")
        full = os.path.join(root, *relative.split("/"))
        if os.path.isdir(full):
            _repo_directory(root, relative)
            tracked_payload = _git_bytes(
                root,
                [
                    "ls-files", "-z", "--cached", "--others",
                    "--exclude-standard", "--", relative,
                ],
            )
            files = [
                item.decode("utf-8", "surrogateescape")
                for item in tracked_payload.split(b"\0") if item
            ]
            ignored_payload = _git_bytes(
                root,
                [
                    "ls-files", "-z", "--others",
                    "--ignored", "--exclude-standard", "--", relative,
                ],
            )
            ignored_files = [
                item.decode("utf-8", "surrogateescape")
                for item in ignored_payload.split(b"\0") if item
            ]
        else:
            files = [relative]
            ignored_files = []
        if any(_SECRET_PATH_RE.search(item) for item in files + ignored_files):
            raise ReleaseMemoryError("evidence_path_unsafe")
        rows = []
        for item in sorted(files):
            path = _repo_regular_file(root, item)
            try:
                rows.append(
                    (item, _sha_file(path, _MAX_EVIDENCE_FILE_BYTES))
                )
            except OSError as exc:
                raise ReleaseMemoryError("evidence_path_unavailable") from exc
        rows.extend(
            ("ignored:" + item, digest({"present": True}))
            for item in sorted(ignored_files)
        )
        result[relative] = digest(rows)
    return result


def head_path_fingerprints(root, paths):
    result = {}
    for relative in sorted(set(paths)):
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or "\\" in relative
            or ".." in relative.split("/")
        ):
            raise ReleaseMemoryError("evidence_path_invalid")
        listed_payload = _git_bytes(
            root,
            [
                "ls-tree", "-r", "-z", "--name-only", "HEAD", "--",
                relative,
            ],
        )
        files = [
            item.decode("utf-8", "surrogateescape")
            for item in listed_payload.split(b"\0") if item
        ]
        if not files:
            files = [relative]
        rows = []
        for item in sorted(files):
            shown = _git_bytes(
                root,
                ["show", "HEAD:" + item],
                maximum=_MAX_EVIDENCE_FILE_BYTES,
            )
            rows.append((item, _sha_bytes(shown)))
        result[relative] = digest(rows)
    return result


def _command_executables(argv):
    current = list(argv)
    result = []
    for _ in range(5):
        if not current:
            raise ReleaseMemoryError("evidence_tool_unavailable")
        result.append(current[0])
        if os.path.basename(current[0]).lower() != "env":
            return result
        index = 1
        while index < len(current):
            item = current[index]
            if item == "--":
                index += 1
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", item):
                index += 1
                continue
            if item in (
                "-i", "--ignore-environment", "-0", "--null",
                "-v", "--debug",
            ):
                index += 1
                continue
            if item in ("-u", "--unset", "-C", "--chdir"):
                index += 2
                continue
            if item.startswith(("--unset=", "--chdir=")):
                index += 1
                continue
            if item.startswith("-"):
                raise ReleaseMemoryError("evidence_tool_unavailable")
            break
        current = current[index:]
    raise ReleaseMemoryError("evidence_tool_unavailable")


def tool_fingerprints(argv, environment, cwd=None):
    result = {}
    for index, executable in enumerate(_command_executables(argv)):
        resolved = (
            executable if os.path.isabs(executable)
            else (
                os.path.realpath(os.path.join(cwd or os.curdir, executable))
                if "/" in executable
                else shutil_which(
                    executable, environment.get("PATH", os.defpath)
                )
            )
        )
        if not resolved:
            raise ReleaseMemoryError("evidence_tool_unavailable")
        try:
            result[
                str(index) + ":" + os.path.basename(executable)
            ] = _sha_file(resolved, _MAX_TOOL_BYTES)
        except OSError as exc:
            raise ReleaseMemoryError("evidence_tool_unavailable") from exc
    return result


def shutil_which(executable, path):
    for directory in path.split(os.pathsep):
        candidate = os.path.join(directory or os.curdir, executable)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return os.path.realpath(candidate)
    return None


def declared_environment_digest(environment, names):
    return digest(
        {
            name: {
                "present": name in environment,
                "value_sha256": (
                    _sha_bytes(environment[name].encode("utf-8"))
                    if name in environment else None
                ),
            }
            for name in sorted(names)
        }
    )


def evidence_path(root, check_id):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", check_id):
        raise ReleaseMemoryError("evidence_id_invalid")
    return os.path.join(ensure_storage(root)["evidence"], check_id + ".json")


def write_evidence(root, check_id, receipt):
    atomic_write(
        evidence_path(root, check_id),
        json.dumps(receipt, sort_keys=True, indent=2) + "\n",
        mode=0o600,
    )


def read_evidence(root, check_id):
    path = evidence_path(root, check_id)
    if not os.path.exists(path):
        return None
    try:
        value = json.loads(_read_regular(path).decode("utf-8", "strict"))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseMemoryError("evidence_malformed") from exc
    return value if isinstance(value, dict) else None


def kimiflow_run_terminal(run_path):
    state = os.path.join(run_path, "STATE.md")
    verification = os.path.join(run_path, "VERIFICATION.md")
    try:
        with open(state, encoding="utf-8") as handle:
            state_text = handle.read(256 * 1024)
        with open(verification, encoding="utf-8") as handle:
            verification_text = handle.read(2 * 1024 * 1024)
    except OSError:
        return False
    return (
        "Phase 6: done" in state_text
        and "kimiflow:verification outcome=passed" in verification_text
        and re.search(
            r"kimiflow:conformance [^>]*status=converged [^>]*diff=passed",
            verification_text,
        )
        is not None
    )


def metrics_template():
    return {
        "schema_version": 1,
        "counts": {
            "checks_executed": 0,
            "checks_reused": 0,
            "resolver_probes": 0,
            "discovery_content_reads": 0,
            "audits_executed": 0,
            "model_calls": 0,
        },
        "duration_milliseconds": {
            "kimiflow_control": 0,
            "project_checks": 0,
            "build": 0,
            "provider": 0,
        },
    }


def write_metrics(root, value):
    required_counts = set(metrics_template()["counts"])
    required_durations = set(metrics_template()["duration_milliseconds"])
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "counts", "duration_milliseconds"}
        or value.get("schema_version") != 1
        or set(value.get("counts", {})) != required_counts
        or set(value.get("duration_milliseconds", {})) != required_durations
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in list(value["counts"].values())
            + list(value["duration_milliseconds"].values())
        )
    ):
        raise ReleaseMemoryError("metrics_invalid")
    ensure_storage(root)
    atomic_write(
        _paths(root)["metrics"],
        json.dumps(value, sort_keys=True, indent=2) + "\n",
        mode=0o600,
    )
    return value


class Timer:
    def __init__(self):
        self.started = time.monotonic_ns()

    def milliseconds(self):
        return max(0, (time.monotonic_ns() - self.started) // 1_000_000)
