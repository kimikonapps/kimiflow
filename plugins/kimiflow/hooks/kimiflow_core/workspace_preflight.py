"""Workspace/worktree inventory and conservative solo-developer cleanup."""

import argparse
import contextlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys

try:
    import fcntl
except ImportError:  # Non-POSIX hosts keep read-only inventory and fail closed on registry writes.
    fcntl = None


TERMINAL_RUN_STATUS = {"done", "failed", "aborted"}
RUN_RE = re.compile(r"^\.kimiflow/[A-Za-z0-9][A-Za-z0-9._-]*$")
IDENTITY_RE = re.compile(r"^[0-9a-f]{64}$")
IGNORED_PATH_SAMPLE_LIMIT = 20
DIRTY_PATH_SAMPLE_LIMIT = 100
UNTRACKED_PATH_SAMPLE_LIMIT = DIRTY_PATH_SAMPLE_LIMIT
OWNER_RECEIPT_NAME = "kimiflow-owner.json"
DESCRIPTOR_RELATIVE_SUPPORTED = all(function in os.supports_dir_fd for function in (os.open, os.stat, os.mkdir))


class WorkspaceError(Exception):
    pass


class MetadataRollbackError(WorkspaceError):
    pass


class RegistryOperation:
    def __init__(self, primary, descriptor):
        self.primary = primary
        self.descriptor = descriptor
        self.invalid_rollbacks = []

    def add_invalid_rollback(self, callback, cleanup=None):
        self.invalid_rollbacks.append((callback, cleanup))

    def rollback_invalid(self):
        errors = []
        for callback, _ in reversed(self.invalid_rollbacks):
            try:
                callback()
            except (OSError, WorkspaceError) as exc:
                errors.append(str(exc))
        return errors

    def close(self):
        for _, cleanup in reversed(self.invalid_rollbacks):
            if cleanup is not None:
                try:
                    cleanup()
                except OSError:
                    pass
        os.close(self.descriptor)


def registry_descriptor(value):
    return value.descriptor if isinstance(value, RegistryOperation) else value


def run_git(root, args, check=True):
    proc = subprocess.run(
        ["git", "-C", root] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise WorkspaceError(detail or "git command failed")
    return proc


def stream_nul_git_paths(root, args, sample_limit):
    """Count NUL-delimited Git paths exactly while retaining only a bounded sample."""
    proc = subprocess.Popen(
        ["git", "-C", root] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    sample = []
    count = 0
    pending = b""
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            fields = (pending + chunk).split(b"\0")
            pending = fields.pop()
            for raw in fields:
                if not raw:
                    continue
                count += 1
                if len(sample) < sample_limit:
                    sample.append(raw.decode("utf-8", "surrogateescape"))
        if pending:
            raise WorkspaceError("malformed NUL-delimited Git path output")
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    if proc.wait() != 0:
        raise WorkspaceError("cannot stream Git path inventory")
    return sample, count


def repo_root(root=None):
    base = os.path.abspath(root or os.getcwd())
    proc = run_git(base, ["rev-parse", "--show-toplevel"], check=False)
    if proc.returncode != 0:
        raise WorkspaceError("not in a git repository")
    output = proc.stdout.decode("utf-8", "surrogateescape")
    if output.endswith("\n"):
        output = output[:-1]
        if output.endswith("\r"):
            output = output[:-1]
    return os.path.realpath(output)


def git_path(root, args):
    proc = run_git(root, args, check=False)
    if proc.returncode != 0:
        raise WorkspaceError("cannot resolve Git administrative path")
    output = proc.stdout.decode("utf-8", "surrogateescape")
    if output.endswith("\n"):
        output = output[:-1]
        if output.endswith("\r"):
            output = output[:-1]
    if not os.path.isabs(output):
        output = os.path.join(root, output)
    return os.path.realpath(output)


def parse_worktree_list(data):
    records = []
    current = None
    for raw in data.split(b"\0"):
        if not raw:
            if current:
                records.append(current)
                current = None
            continue
        text = raw.decode("utf-8", "surrogateescape")
        key, _, value = text.partition(" ")
        if key == "worktree":
            if current:
                records.append(current)
            current = {"path": os.path.realpath(value)}
        elif current is not None:
            if key in {"detached", "bare"}:
                current[key] = True
            elif key in {"locked", "prunable"}:
                current[key] = value or True
            else:
                current[key.lower()] = value
    if current:
        records.append(current)
    return records


def worktree_records(root):
    proc = run_git(root, ["worktree", "list", "--porcelain", "-z"])
    records = parse_worktree_list(proc.stdout)
    if not records:
        raise WorkspaceError("git returned no worktrees")
    return records


def parse_status_v2(data, path_limit=DIRTY_PATH_SAMPLE_LIMIT):
    paths = []
    path_count = 0
    staged = 0
    unstaged = 0
    untracked = 0
    fields = data.split(b"\0")
    index = 0
    while index < len(fields):
        raw = fields[index]
        index += 1
        if not raw or raw.startswith(b"# "):
            continue
        text = raw.decode("utf-8", "surrogateescape")
        kind = text[:1]
        path = ""
        original_path = ""
        xy = ".."
        if kind == "1":
            parts = text.split(" ", 8)
            if len(parts) == 9:
                xy, path = parts[1], parts[8]
        elif kind == "2":
            parts = text.split(" ", 9)
            if len(parts) == 10:
                xy, path = parts[1], parts[9]
            if index < len(fields):
                original_path = fields[index].decode("utf-8", "surrogateescape")
                index += 1
        elif kind == "u":
            parts = text.split(" ", 10)
            if len(parts) == 11:
                xy, path = parts[1], parts[10]
        elif kind == "?" and text.startswith("? "):
            path = text[2:]
            untracked += 1
        candidate_paths = [item for item in (path, original_path) if item]
        if not path:
            continue
        for candidate in candidate_paths:
            path_count += 1
            if len(paths) < path_limit:
                paths.append(candidate)
        if kind in {"1", "2", "u"}:
            if len(xy) > 0 and xy[0] != ".":
                staged += 1
            if len(xy) > 1 and xy[1] != ".":
                unstaged += 1
    return {
        "dirty": path_count > 0,
        "dirty_paths": paths,
        "tracked_path_count": path_count,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
    }


def worktree_status(path):
    if not os.path.isdir(path):
        return {
            "dirty": False,
            "dirty_paths": [],
            "dirty_path_count": 0,
            "dirty_paths_truncated": False,
            "staged": 0,
            "unstaged": 0,
            "untracked": 0,
            "ignored_paths": [],
            "ignored_count": 0,
            "ignored_paths_truncated": False,
        }
    proc = run_git(path, ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=no"], check=False)
    if proc.returncode != 0:
        raise WorkspaceError("cannot inspect worktree status: %s" % path)
    result = parse_status_v2(proc.stdout)
    remaining = max(0, UNTRACKED_PATH_SAMPLE_LIMIT - len(result["dirty_paths"]))
    untracked_paths, untracked_count = stream_nul_git_paths(
        path, ["ls-files", "--others", "--exclude-standard", "-z"], remaining
    )
    result["dirty_paths"].extend(untracked_paths)
    result["untracked"] = untracked_count
    result["dirty"] = result["dirty"] or untracked_count > 0
    result["dirty_path_count"] = result.pop("tracked_path_count") + untracked_count
    result["dirty_paths_truncated"] = result["dirty_path_count"] > len(result["dirty_paths"])
    ignored_paths, ignored_count = stream_nul_git_paths(
        path, ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"], IGNORED_PATH_SAMPLE_LIMIT
    )
    result["ignored_paths"] = ignored_paths
    result["ignored_count"] = ignored_count
    result["ignored_paths_truncated"] = ignored_count > len(ignored_paths)
    return result


def is_within(path, parent):
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(parent)]) == os.path.realpath(parent)
    except ValueError:
        return False


def codex_managed(path):
    codex_home = os.path.abspath(os.path.expanduser(os.environ.get("CODEX_HOME", "~/.codex")))
    return is_within(path, os.path.join(codex_home, "worktrees"))


@contextlib.contextmanager
def registry_directory(primary, create=False):
    """Pin .kimiflow/session so registry reads and writes never reopen its path."""
    if not DESCRIPTOR_RELATIVE_SUPPORTED:
        if not create:
            yield None
            return
        raise WorkspaceError("registry writes require descriptor-relative filesystem support")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    base_descriptor = None
    session_descriptor = None
    base_path = os.path.join(primary, ".kimiflow")
    try:
        try:
            base_descriptor = os.open(base_path, flags)
        except FileNotFoundError:
            if not create:
                yield None
                return
            os.mkdir(base_path, 0o700)
            base_descriptor = os.open(base_path, flags)
        try:
            session_descriptor = os.open("session", flags, dir_fd=base_descriptor)
        except FileNotFoundError:
            if not create:
                yield None
                return
            os.mkdir("session", 0o700, dir_fd=base_descriptor)
            session_descriptor = os.open("session", flags, dir_fd=base_descriptor)
        for descriptor in (base_descriptor, session_descriptor):
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise WorkspaceError("unsafe registry parent")
        pinned_base = os.fstat(base_descriptor)
        pinned_session = os.fstat(session_descriptor)
        yield session_descriptor
        named_base = os.lstat(base_path)
        named_session = os.stat("session", dir_fd=base_descriptor, follow_symlinks=False)
        if (named_base.st_dev, named_base.st_ino) != (pinned_base.st_dev, pinned_base.st_ino) or (
            named_session.st_dev,
            named_session.st_ino,
        ) != (pinned_session.st_dev, pinned_session.st_ino):
            raise WorkspaceError("registry parent identity changed")
    except OSError as exc:
        raise WorkspaceError("unsafe registry parent") from exc
    finally:
        if session_descriptor is not None:
            os.close(session_descriptor)
        if base_descriptor is not None:
            os.close(base_descriptor)


def atomic_directory_write(directory_descriptor, name, payload):
    temporary = ".kimiflow-%s-%s" % (name, secrets.token_hex(8))
    backup = ".kimiflow-backup-%s-%s" % (name, secrets.token_hex(8))
    quarantine = ".kimiflow-quarantine-%s-%s" % (name, secrets.token_hex(8))
    descriptor = None
    temporary_identity = None
    backup_identity = None
    target_moved = False
    installed_by_us = False
    quarantined_target = None
    namespace_compensated = False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_descriptor)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short descriptor-relative write")
            view = view[written:]
        os.fsync(descriptor)
        temporary_info = os.fstat(descriptor)
        temporary_identity = (temporary_info.st_dev, temporary_info.st_ino)
        try:
            target = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            target = None
        if target is not None:
            backup_identity = (target.st_dev, target.st_ino)
            os.rename(name, backup, src_dir_fd=directory_descriptor, dst_dir_fd=directory_descriptor)
            target_moved = True
            moved = os.stat(backup, dir_fd=directory_descriptor, follow_symlinks=False)
            if (moved.st_dev, moved.st_ino) != backup_identity:
                os.rename(backup, name, src_dir_fd=directory_descriptor, dst_dir_fd=directory_descriptor)
                target_moved = False
                raise OSError("atomic target identity changed")
        os.link(
            temporary,
            name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        installed_by_us = True
        installed = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (installed.st_dev, installed.st_ino) != temporary_identity:
            os.rename(name, quarantine, src_dir_fd=directory_descriptor, dst_dir_fd=directory_descriptor)
            if target_moved:
                os.rename(backup, name, src_dir_fd=directory_descriptor, dst_dir_fd=directory_descriptor)
                target_moved = False
            raise OSError("atomic source identity changed")
        os.unlink(temporary, dir_fd=directory_descriptor)
        os.fsync(directory_descriptor)
        committed_name = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if (committed_name.st_dev, committed_name.st_ino) != temporary_identity:
            raise OSError("atomic committed target identity changed")
        if target_moved:
            current_backup = os.stat(backup, dir_fd=directory_descriptor, follow_symlinks=False)
            if (current_backup.st_dev, current_backup.st_ino) != backup_identity:
                raise OSError("atomic backup identity changed")
            os.unlink(backup, dir_fd=directory_descriptor)
            target_moved = False
        try:
            os.fsync(directory_descriptor)
        except OSError:
            # The new name is already durable and the old backup is gone. A
            # second directory-fsync failure is an uncertain durability signal,
            # not permission to report a rolled-back transaction.
            pass
    except OSError:
        if target_moved:
            try:
                current_backup = os.stat(backup, dir_fd=directory_descriptor, follow_symlinks=False)
                if (current_backup.st_dev, current_backup.st_ino) == backup_identity:
                    try:
                        current_name = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
                        if installed_by_us and (current_name.st_dev, current_name.st_ino) == temporary_identity:
                            os.unlink(name, dir_fd=directory_descriptor)
                            namespace_compensated = True
                        else:
                            foreign_identity = (current_name.st_dev, current_name.st_ino)
                            os.rename(
                                name,
                                quarantine,
                                src_dir_fd=directory_descriptor,
                                dst_dir_fd=directory_descriptor,
                            )
                            quarantined_target = quarantine
                            namespace_compensated = True
                            quarantined = os.stat(
                                quarantine,
                                dir_fd=directory_descriptor,
                                follow_symlinks=False,
                            )
                            if (quarantined.st_dev, quarantined.st_ino) != foreign_identity:
                                raise OSError("atomic concurrent target identity changed")
                    except FileNotFoundError:
                        pass
                    try:
                        os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        os.rename(backup, name, src_dir_fd=directory_descriptor, dst_dir_fd=directory_descriptor)
                        target_moved = False
                        namespace_compensated = True
            except OSError:
                pass
        elif installed_by_us:
            try:
                current_name = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
                if (current_name.st_dev, current_name.st_ino) == temporary_identity:
                    os.unlink(name, dir_fd=directory_descriptor)
                    namespace_compensated = True
                else:
                    foreign_identity = (current_name.st_dev, current_name.st_ino)
                    os.rename(
                        name,
                        quarantine,
                        src_dir_fd=directory_descriptor,
                        dst_dir_fd=directory_descriptor,
                    )
                    quarantined_target = quarantine
                    quarantined = os.stat(
                        quarantine,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                    if (quarantined.st_dev, quarantined.st_ino) != foreign_identity:
                        raise OSError("atomic concurrent target identity changed")
                    namespace_compensated = True
            except OSError:
                pass
        try:
            current_temporary = os.stat(temporary, dir_fd=directory_descriptor, follow_symlinks=False)
            if temporary_identity is None or (current_temporary.st_dev, current_temporary.st_ino) == temporary_identity:
                os.unlink(temporary, dir_fd=directory_descriptor)
                namespace_compensated = True
        except OSError:
            pass
        if namespace_compensated:
            os.fsync(directory_descriptor)
        if quarantined_target is not None:
            raise OSError("concurrent target preserved as %s" % quarantined_target)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def validate_registry(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise WorkspaceError("malformed worktree registry")
    entries = data.get("entries")
    if not isinstance(entries, list) or len(entries) > 1:
        raise WorkspaceError("malformed worktree registry")
    clean = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "run", "identity"}:
            raise WorkspaceError("malformed worktree registry")
        path = entry.get("path")
        run = entry.get("run")
        identity = entry.get("identity")
        if not isinstance(path, str) or not os.path.isabs(path) or os.path.realpath(path) != path:
            raise WorkspaceError("malformed worktree registry")
        if not isinstance(run, str) or not RUN_RE.match(run):
            raise WorkspaceError("malformed worktree registry")
        if not isinstance(identity, str) or not IDENTITY_RE.match(identity):
            raise WorkspaceError("malformed worktree registry")
        clean.append({"path": path, "run": run, "identity": identity})
    return {"schema_version": 1, "entries": clean}


def read_registry_descriptor(directory_descriptor):
    directory_descriptor = registry_descriptor(directory_descriptor)
    if directory_descriptor is None:
        return {"schema_version": 1, "entries": []}
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open("WORKTREE_REGISTRY.json", flags, dir_fd=directory_descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceError("unsafe worktree registry")
        payload = os.read(descriptor, 65537)
        if len(payload) > 65536:
            raise WorkspaceError("malformed worktree registry")
        return validate_registry(json.loads(payload.decode("utf-8")))
    except FileNotFoundError:
        return {"schema_version": 1, "entries": []}
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, WorkspaceError):
            raise
        raise WorkspaceError("malformed worktree registry") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_registry(primary, directory_descriptor=None):
    if directory_descriptor is not None:
        return read_registry_descriptor(directory_descriptor)
    with registry_directory(primary, create=False) as pinned_directory:
        return read_registry_descriptor(pinned_directory)


def write_registry_descriptor(directory_descriptor, registry):
    directory_descriptor = registry_descriptor(directory_descriptor)
    payload = (json.dumps(validate_registry(registry), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        existing = os.stat("WORKTREE_REGISTRY.json", dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise WorkspaceError("cannot inspect worktree registry") from exc
    else:
        if stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode):
            raise WorkspaceError("unsafe worktree registry")
    try:
        atomic_directory_write(directory_descriptor, "WORKTREE_REGISTRY.json", payload)
    except OSError as exc:
        raise WorkspaceError("cannot write worktree registry: %s" % exc) from exc


def write_registry(primary, registry, directory_descriptor=None):
    if directory_descriptor is not None:
        write_registry_descriptor(directory_descriptor, registry)
        return
    with registry_directory(primary, create=True) as pinned_directory:
        write_registry_descriptor(pinned_directory, registry)


def ensure_registry_descriptor_current(primary, directory_descriptor):
    directory_descriptor = registry_descriptor(directory_descriptor)
    session_path = os.path.join(primary, ".kimiflow", "session")
    try:
        named = os.lstat(session_path)
        pinned = os.fstat(directory_descriptor)
    except OSError as exc:
        raise WorkspaceError("registry parent identity changed") from exc
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(named.st_mode) or (
        named.st_dev,
        named.st_ino,
    ) != (pinned.st_dev, pinned.st_ino):
        raise WorkspaceError("registry parent identity changed")


@contextlib.contextmanager
def registry_lock(primary):
    if fcntl is None:
        raise WorkspaceError("worktree registry writes require POSIX file locking")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    operation = None
    common_dir = git_path(primary, ["rev-parse", "--git-common-dir"])
    try:
        named = os.lstat(common_dir)
        descriptor = os.open(common_dir, flags)
        pinned = os.fstat(descriptor)
        if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(pinned.st_mode) or (
            named.st_dev,
            named.st_ino,
        ) != (pinned.st_dev, pinned.st_ino):
            raise WorkspaceError("unsafe worktree registry lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with registry_directory(primary, create=True) as directory_descriptor:
            operation = RegistryOperation(primary, os.dup(directory_descriptor))
            yield operation
    except OSError as exc:
        raise WorkspaceError("cannot lock worktree registry") from exc
    except WorkspaceError as exc:
        rollback_errors = operation.rollback_invalid() if operation is not None else []
        if rollback_errors:
            raise WorkspaceError(
                "%s; invalid registry transaction rollback failed: %s"
                % (exc, "; ".join(rollback_errors))
            ) from exc
        raise
    finally:
        if operation is not None:
            operation.close()
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


@contextlib.contextmanager
def registry_operation(root, write):
    current = repo_root(root)
    primary = worktree_records(current)[0]["path"]
    if write:
        with registry_lock(primary) as directory_descriptor:
            yield directory_descriptor
    else:
        yield None


def owner_receipt_path(path):
    git_dir = git_path(path, ["rev-parse", "--absolute-git-dir"])
    common_dir = git_path(path, ["rev-parse", "--git-common-dir"])
    worktrees_dir = os.path.join(common_dir, "worktrees")
    if git_dir == os.path.realpath(worktrees_dir) or not is_within(git_dir, worktrees_dir):
        raise WorkspaceError("worktree has no linked-worktree administrative directory")
    info = os.lstat(git_dir)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError("unsafe linked-worktree administrative directory")
    return os.path.join(git_dir, OWNER_RECEIPT_NAME)


def receipt_data(entry):
    return {
        "schema_version": 1,
        "path": entry["path"],
        "run": entry["run"],
        "identity": entry["identity"],
    }


def receipt_file_matches(receipt, entry):
    try:
        if not os.path.lexists(receipt):
            return False
        info = os.lstat(receipt)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return False
        with open(receipt, "r", encoding="utf-8") as handle:
            return json.load(handle) == receipt_data(entry)
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def owner_receipt_matches(path, entry):
    try:
        return receipt_file_matches(owner_receipt_path(path), entry)
    except WorkspaceError:
        return False


def read_admin_file(admin_descriptor, name, limit=65536):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(name, flags, dir_fd=admin_descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceError("unsafe administrative file: %s" % name)
        payload = os.read(descriptor, limit + 1)
        if len(payload) > limit:
            raise WorkspaceError("administrative file is too large: %s" % name)
        return payload
    except OSError as exc:
        raise WorkspaceError("cannot read administrative file: %s" % name) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def atomic_admin_write(admin_descriptor, name, payload):
    try:
        atomic_directory_write(admin_descriptor, name, payload)
    except OSError as exc:
        raise WorkspaceError("cannot write administrative file: %s" % name) from exc


def unlink_admin_file(admin_descriptor, name):
    try:
        os.unlink(name, dir_fd=admin_descriptor)
        os.fsync(admin_descriptor)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkspaceError("cannot remove administrative file: %s" % name) from exc


def state_value_from_text(source, wanted):
    for raw in source.splitlines():
        line = raw.strip().lstrip("-").strip().replace("**", "")
        label, sep, value = line.partition(":")
        if sep and label.strip().lower() == wanted.lower():
            return value.strip()
    return ""


def safe_run_state(primary, run):
    if not RUN_RE.match(run or ""):
        return "", ""
    flags = os.O_RDONLY
    directory_flags = flags | (os.O_DIRECTORY if hasattr(os, "O_DIRECTORY") else 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
        directory_flags |= os.O_NOFOLLOW
    base_path = os.path.join(primary, ".kimiflow")
    run_name = run.split("/", 1)[1]
    base_descriptor = None
    run_descriptor = None
    state_descriptor = None
    try:
        base_descriptor = os.open(base_path, directory_flags)
        run_descriptor = os.open(run_name, directory_flags, dir_fd=base_descriptor)
        state_descriptor = os.open("STATE.md", flags, dir_fd=run_descriptor)
        base_info = os.fstat(base_descriptor)
        run_info = os.fstat(run_descriptor)
        state_info = os.fstat(state_descriptor)
        if not stat.S_ISDIR(base_info.st_mode) or not stat.S_ISDIR(run_info.st_mode) or not stat.S_ISREG(state_info.st_mode):
            return "", ""
        payload = os.read(state_descriptor, 1048577)
        if len(payload) > 1048576:
            return "", ""
        named_base = os.lstat(base_path)
        named_run = os.stat(run_name, dir_fd=base_descriptor, follow_symlinks=False)
        named_state = os.stat("STATE.md", dir_fd=run_descriptor, follow_symlinks=False)
        if (named_base.st_dev, named_base.st_ino) != (base_info.st_dev, base_info.st_ino):
            return "", ""
        if (named_run.st_dev, named_run.st_ino) != (run_info.st_dev, run_info.st_ino):
            return "", ""
        if (named_state.st_dev, named_state.st_ino) != (state_info.st_dev, state_info.st_ino):
            return "", ""
        source = payload.decode("utf-8")
        flow_schema = state_value_from_text(source, "Flow schema")
        status_value = state_value_from_text(source, "Status")
        final_state = os.fstat(state_descriptor)
        final_named_state = os.stat("STATE.md", dir_fd=run_descriptor, follow_symlinks=False)
        initial_content_identity = (
            state_info.st_dev,
            state_info.st_ino,
            state_info.st_size,
            state_info.st_mtime_ns,
            state_info.st_ctime_ns,
        )
        final_content_identity = (
            final_state.st_dev,
            final_state.st_ino,
            final_state.st_size,
            final_state.st_mtime_ns,
            final_state.st_ctime_ns,
        )
        if final_content_identity != initial_content_identity or (
            final_named_state.st_dev,
            final_named_state.st_ino,
        ) != (state_info.st_dev, state_info.st_ino):
            return "", ""
        return flow_schema, status_value
    except (OSError, UnicodeError):
        return "", ""
    finally:
        if state_descriptor is not None:
            os.close(state_descriptor)
        if run_descriptor is not None:
            os.close(run_descriptor)
        if base_descriptor is not None:
            os.close(base_descriptor)


def local_active(path):
    session = os.path.join(path, ".kimiflow", "session")
    active = os.path.join(session, "ACTIVE_RUN.json")
    for parent in (os.path.join(path, ".kimiflow"), session):
        if os.path.lexists(parent):
            info = os.lstat(parent)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return True
        else:
            return False
    if not os.path.lexists(active):
        return False
    info = os.lstat(active)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return True
    return True


def run_status(primary, run):
    _, value = safe_run_state(primary, run)
    return value.lower().split(" ", 1)[0]


def build_status(root=None, registry_descriptor=None):
    current = repo_root(root)
    records = worktree_records(current)
    primary = records[0]["path"]
    registry = read_registry(primary, registry_descriptor)
    registrations = {entry["path"]: entry for entry in registry["entries"]}
    trees = []
    prunable_paths = []
    registered_prunable_paths = []
    for record in records:
        path = record["path"]
        path_info_before = os.lstat(path) if os.path.lexists(path) else None
        status = worktree_status(path)
        path_info = os.lstat(path) if os.path.lexists(path) else None
        before_identity = (
            (path_info_before.st_dev, path_info_before.st_ino, path_info_before.st_mode)
            if path_info_before is not None
            else None
        )
        after_identity = (
            (path_info.st_dev, path_info.st_ino, path_info.st_mode)
            if path_info is not None
            else None
        )
        if before_identity != after_identity:
            raise WorkspaceError("worktree identity changed during status inspection: %s" % path)
        registration = registrations.get(path)
        receipt_valid = bool(registration and not record.get("prunable") and owner_receipt_matches(path, registration))
        run = registration["run"] if registration else ""
        status_value = run_status(primary, run) if run else ""
        active = local_active(path) or bool(run and status_value not in TERMINAL_RUN_STATUS)
        locked = bool(record.get("locked"))
        prunable = bool(record.get("prunable"))
        current_tree = path == current
        primary_tree = path == primary
        managed = codex_managed(path)
        blockers = []
        if current_tree:
            blockers.append("current")
        if primary_tree:
            blockers.append("primary")
        if status["dirty"]:
            blockers.append("dirty")
        if status["ignored_count"]:
            blockers.append("ignored-content")
        if locked:
            blockers.append("locked")
        if active:
            blockers.append("active")
        if managed:
            blockers.append("codex-managed")
        if not registration:
            blockers.append("unregistered")
        elif not receipt_valid and not prunable:
            blockers.append("ownership-receipt-invalid")
        if run and status_value not in TERMINAL_RUN_STATUS:
            blockers.append("run-not-done")
        if prunable:
            blockers.append("prunable")
            prunable_paths.append(path)
            if run:
                registered_prunable_paths.append(path)
        trees.append({
            "path": path,
            "head": record.get("head", ""),
            "branch": record.get("branch", ""),
            "detached": bool(record.get("detached")),
            "locked": locked,
            "prunable": prunable,
            "exists": os.path.isdir(path),
            "device": path_info.st_dev if path_info and stat.S_ISDIR(path_info.st_mode) else None,
            "inode": path_info.st_ino if path_info and stat.S_ISDIR(path_info.st_mode) else None,
            "current": current_tree,
            "primary": primary_tree,
            "dirty": status["dirty"],
            "dirty_paths": status["dirty_paths"],
            "dirty_path_count": status["dirty_path_count"],
            "dirty_paths_truncated": status["dirty_paths_truncated"],
            "staged": status["staged"],
            "unstaged": status["unstaged"],
            "untracked": status["untracked"],
            "ignored_paths": status["ignored_paths"],
            "ignored_count": status["ignored_count"],
            "ignored_paths_truncated": status["ignored_paths_truncated"],
            "active": active,
            "codex_managed": managed,
            "kimiflow_registered": bool(registration),
            "kimiflow_owned": receipt_valid,
            "run": run or None,
            "run_status": status_value or None,
            "removable": not blockers,
            "blockers": blockers,
        })
    record_paths = {tree["path"] for tree in trees}
    reconcilable_registry_paths = []
    unresolved = []
    for entry in registry["entries"]:
        if entry["path"] in record_paths:
            continue
        if run_status(primary, entry["run"]) in TERMINAL_RUN_STATUS:
            reconcilable_registry_paths.append(entry["path"])
        else:
            unresolved.append({"path": entry["path"], "reason": "registered-worktree-missing"})
    for tree in trees:
        if tree["current"] and tree["dirty"]:
            unresolved.append({"path": tree["path"], "reason": "current-dirty"})
        elif tree["prunable"] and tree["kimiflow_registered"]:
            unresolved.append({"path": tree["path"], "reason": "registered-worktree-prunable"})
        elif not tree["current"] and not tree["prunable"] and not tree["removable"]:
            unresolved.append({"path": tree["path"], "reason": ",".join(tree["blockers"])})
    current_tree = next(tree for tree in trees if tree["current"])
    safe_prune = bool(prunable_paths) and not registered_prunable_paths
    return {
        "schema_version": 1,
        "repo_root": current,
        "primary_root": primary,
        "branch": current_tree["branch"],
        "head": current_tree["head"],
        "dirty": current_tree["dirty"],
        "dirty_paths": current_tree["dirty_paths"],
        "dirty_path_count": current_tree["dirty_path_count"],
        "dirty_paths_truncated": current_tree["dirty_paths_truncated"],
        "ignored_paths": current_tree["ignored_paths"],
        "ignored_count": current_tree["ignored_count"],
        "ignored_paths_truncated": current_tree["ignored_paths_truncated"],
        "worktree_count": len(trees),
        "temporary_count": len(registry["entries"]),
        "can_register_temporary": len(registry["entries"]) == 0,
        "safe_prune_available": safe_prune,
        "registry_reconcile_available": bool(reconcilable_registry_paths),
        "registry_reconcile_paths": reconcilable_registry_paths,
        "auto_cleanup_paths": [tree["path"] for tree in trees if tree["removable"]],
        "decision_required": bool(unresolved),
        "unresolved": unresolved,
        "policy": {"mode": "solo", "new_worktrees": "explicit-only", "max_temporary": 1},
        "worktrees": trees,
    }


def find_tree(status, path):
    target = os.path.realpath(os.path.abspath(path))
    for tree in status["worktrees"]:
        if tree["path"] == target:
            return tree
    raise WorkspaceError("path is not a linked worktree")


def register(root, path, run, write=False):
    if not RUN_RE.match(run or ""):
        raise WorkspaceError("run must be .kimiflow/<slug>")
    with registry_operation(root, write) as registry_descriptor:
        status = build_status(root, registry_descriptor)
        tree = find_tree(status, path)
        if tree["current"] or tree["primary"] or tree["codex_managed"] or tree["locked"] or tree["prunable"] or tree["dirty"] or tree["ignored_count"] or tree["active"] or not tree["exists"]:
            raise WorkspaceError("worktree is not eligible for registration")
        primary = status["primary_root"]
        schema, status_value = safe_run_state(primary, run)
        if schema.split(" ", 1)[0] != "4" or status_value.lower() != "active":
            raise WorkspaceError("registration requires a primary schema-4 run")
        registry = read_registry(primary, registry_descriptor)
        if registry["entries"]:
            raise WorkspaceError("temporary worktree cap reached")
        entry = {"path": tree["path"], "run": run, "identity": secrets.token_hex(32)}
        if write:
            receipt = owner_receipt_path(tree["path"])
            admin_dir = os.path.dirname(receipt)
            with pinned_worktree(tree["path"], None, admin_dir) as pinned_values:
                pinned, pinned_admin, admin_descriptor = pinned_values
                if (pinned.st_dev, pinned.st_ino) != (tree["device"], tree["inode"]):
                    raise WorkspaceError("worktree identity changed before registration")
                try:
                    os.stat(OWNER_RECEIPT_NAME, dir_fd=admin_descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise WorkspaceError("cannot inspect worktree ownership receipt") from exc
                else:
                    raise WorkspaceError("worktree ownership receipt already exists")
                atomic_admin_write(
                    admin_descriptor,
                    OWNER_RECEIPT_NAME,
                    (json.dumps(receipt_data(entry), sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"),
                )
                current = os.lstat(tree["path"])
                if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                    unlink_admin_file(admin_descriptor, OWNER_RECEIPT_NAME)
                    raise WorkspaceError("worktree identity changed during registration")
                current_admin = os.lstat(admin_dir)
                if (current_admin.st_dev, current_admin.st_ino) != (pinned_admin.st_dev, pinned_admin.st_ino):
                    unlink_admin_file(admin_descriptor, OWNER_RECEIPT_NAME)
                    raise WorkspaceError("worktree administrative identity changed during registration")
                try:
                    final_status = worktree_status(tree["path"])
                    final_tree = os.lstat(tree["path"])
                    final_admin = os.lstat(admin_dir)
                except (OSError, WorkspaceError):
                    unlink_admin_file(admin_descriptor, OWNER_RECEIPT_NAME)
                    raise
                if (final_tree.st_dev, final_tree.st_ino) != (pinned.st_dev, pinned.st_ino) or (
                    final_admin.st_dev,
                    final_admin.st_ino,
                ) != (pinned_admin.st_dev, pinned_admin.st_ino):
                    unlink_admin_file(admin_descriptor, OWNER_RECEIPT_NAME)
                    raise WorkspaceError("worktree identity changed during final registration check")
                if final_status["dirty"] or final_status["ignored_count"]:
                    unlink_admin_file(admin_descriptor, OWNER_RECEIPT_NAME)
                    raise WorkspaceError("worktree became dirty during registration")
                registry_written = False
                try:
                    write_registry(
                        primary,
                        {"schema_version": 1, "entries": [entry]},
                        registry_descriptor,
                    )
                    registry_written = True
                    with pinned_worktree(tree["path"], entry, admin_dir) as final_values:
                        final_tree, final_admin, _ = final_values
                        if (final_tree.st_dev, final_tree.st_ino) != (pinned.st_dev, pinned.st_ino) or (
                            final_admin.st_dev,
                            final_admin.st_ino,
                        ) != (pinned_admin.st_dev, pinned_admin.st_ino):
                            raise WorkspaceError("worktree identity changed after registration")
                        published_status = worktree_status(tree["path"])
                        published_tree = os.lstat(tree["path"])
                        published_admin = os.lstat(admin_dir)
                        if (published_tree.st_dev, published_tree.st_ino) != (pinned.st_dev, pinned.st_ino) or (
                            published_admin.st_dev,
                            published_admin.st_ino,
                        ) != (pinned_admin.st_dev, pinned_admin.st_ino):
                            raise WorkspaceError("worktree identity changed during published status check")
                        if published_status["dirty"] or published_status["ignored_count"]:
                            raise WorkspaceError("worktree became dirty during registry publication")
                    ensure_registry_descriptor_current(primary, registry_descriptor)
                    rollback_admin_descriptor = os.dup(admin_descriptor)

                    def rollback_invalid_registration():
                        write_registry(
                            primary,
                            {"schema_version": 1, "entries": []},
                            registry_descriptor,
                        )
                        rollback_admin = os.fstat(rollback_admin_descriptor)
                        if (rollback_admin.st_dev, rollback_admin.st_ino) != (
                            pinned_admin.st_dev,
                            pinned_admin.st_ino,
                        ):
                            raise WorkspaceError("cannot roll back changed worktree administrative record")
                        unlink_admin_file(rollback_admin_descriptor, OWNER_RECEIPT_NAME)

                    registry_descriptor.add_invalid_rollback(
                        rollback_invalid_registration,
                        lambda: os.close(rollback_admin_descriptor),
                    )
                except WorkspaceError:
                    if registry_written:
                        write_registry(
                            primary,
                            {"schema_version": 1, "entries": []},
                            registry_descriptor,
                        )
                    unlink_admin_file(admin_descriptor, OWNER_RECEIPT_NAME)
                    raise
    return {"status": "registered" if write else "preview", "written": bool(write), "entry": entry}


def retirement_paths(path, identity):
    parent = os.path.dirname(path)
    label = os.path.basename(path) or "worktree"
    archive_root = os.path.join(parent, ".%s.kimiflow-archive-%s" % (label, identity[:12]))
    archive_path = os.path.join(archive_root, "worktree")
    return archive_root, archive_path


def prepare_retirement_paths(paths):
    archive_root, _ = paths
    try:
        os.mkdir(archive_root, 0o700)
    except FileExistsError as exc:
        raise WorkspaceError("worktree archive destination already exists") from exc
    except OSError as exc:
        raise WorkspaceError("cannot create worktree archive") from exc


@contextlib.contextmanager
def safe_directory(path, create=False):
    if create:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise WorkspaceError("cannot create safe directory: %s" % path) from exc
    try:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise WorkspaceError("unsafe directory: %s" % path)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        pinned = os.fstat(descriptor)
        if (pinned.st_dev, pinned.st_ino) != (info.st_dev, info.st_ino):
            os.close(descriptor)
            raise WorkspaceError("directory identity changed: %s" % path)
    except OSError as exc:
        raise WorkspaceError("cannot pin safe directory: %s" % path) from exc
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def metadata_retirement_paths(common_dir, identity):
    root = os.path.join(common_dir, "kimiflow-retired-worktrees")
    return root, os.path.join(root, identity)


def directory_path_matches_descriptor(path, descriptor):
    try:
        named = os.lstat(path)
        pinned = os.fstat(descriptor)
    except OSError:
        return False
    return (
        not stat.S_ISLNK(named.st_mode)
        and stat.S_ISDIR(named.st_mode)
        and (named.st_dev, named.st_ino) == (pinned.st_dev, pinned.st_ino)
    )


def locate_directory_descriptor(parent, descriptor):
    pinned = os.fstat(descriptor)
    try:
        with safe_directory(parent) as parent_descriptor:
            for candidate in os.listdir(parent_descriptor):
                try:
                    info = os.stat(candidate, dir_fd=parent_descriptor, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (pinned.st_dev, pinned.st_ino):
                    return os.path.join(parent, candidate)
    except WorkspaceError:
        pass
    return ""


def admin_record_expected_name(admin_descriptor, admin_parent):
    """Return the administrative name proven by a linked checkout's gitfile."""
    back_reference = os.fsdecode(read_admin_file(admin_descriptor, "gitdir")).rstrip("\r\n")
    if not os.path.isabs(back_reference):
        return ""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    git_descriptor = None
    try:
        git_descriptor = os.open(back_reference, flags)
        info = os.fstat(git_descriptor)
        if not stat.S_ISREG(info.st_mode):
            return ""
        payload = os.read(git_descriptor, 65537)
        if len(payload) > 65536:
            return ""
    except OSError:
        return ""
    finally:
        if git_descriptor is not None:
            os.close(git_descriptor)
    text = payload.decode("utf-8", "surrogateescape").rstrip("\r\n")
    if not text.startswith("gitdir: "):
        return ""
    pointer = text[len("gitdir: "):]
    if not os.path.isabs(pointer):
        pointer = os.path.join(os.path.dirname(back_reference), pointer)
    if os.path.realpath(os.path.dirname(pointer)) != os.path.realpath(admin_parent):
        return ""
    return os.path.basename(pointer)


def rollback_mismatched_admin_move(
    parent_descriptor,
    archive_descriptor,
    admin_parent,
    admin_name,
    identity,
    pinned_admin,
):
    """Undo only the metadata move, restoring a proven two-record exchange when possible."""
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    moved_descriptor = None
    try:
        moved_descriptor = os.open(identity, flags, dir_fd=archive_descriptor)
        expected_name = admin_record_expected_name(moved_descriptor, admin_parent)
        pinned_name = ""
        for candidate in os.listdir(parent_descriptor):
            try:
                info = os.stat(candidate, dir_fd=parent_descriptor, follow_symlinks=False)
            except OSError:
                continue
            if (info.st_dev, info.st_ino) == (pinned_admin.st_dev, pinned_admin.st_ino):
                pinned_name = candidate
                break
        if expected_name and pinned_name == expected_name and expected_name != admin_name:
            try:
                os.stat(admin_name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(expected_name, admin_name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
                try:
                    os.rename(identity, expected_name, src_dir_fd=archive_descriptor, dst_dir_fd=parent_descriptor)
                    return True
                except OSError:
                    # A transient failure after the first half of the exchange
                    # must not strand foreign metadata in our archive.
                    try:
                        os.rename(identity, expected_name, src_dir_fd=archive_descriptor, dst_dir_fd=parent_descriptor)
                        return True
                    except OSError:
                        try:
                            os.rename(admin_name, expected_name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
                        except OSError:
                            return False
            except OSError:
                pass
        # If the exchange cannot be proven, restore the helper's own rename.
        try:
            os.stat(admin_name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            os.rename(identity, admin_name, src_dir_fd=archive_descriptor, dst_dir_fd=parent_descriptor)
            return True
    except OSError:
        return False
    finally:
        if moved_descriptor is not None:
            os.close(moved_descriptor)
    return False


def persist_admin_compensation(parent_descriptor, archive_descriptor, metadata_path):
    try:
        os.fsync(parent_descriptor)
        os.fsync(archive_descriptor)
    except OSError as exc:
        raise MetadataRollbackError(
            "metadata compensation is not durable; inspect %s" % metadata_path
        ) from exc


def detach_admin_record(admin_dir, admin_descriptor, common_dir, identity):
    admin_parent = os.path.dirname(admin_dir)
    admin_name = os.path.basename(admin_dir)
    metadata_root, metadata_path = metadata_retirement_paths(common_dir, identity)
    pinned_admin = os.fstat(admin_descriptor)
    with safe_directory(common_dir) as common_descriptor, safe_directory(admin_parent) as parent_descriptor, safe_directory(metadata_root, create=True) as archive_descriptor:
        try:
            os.fsync(common_descriptor)
        except OSError as exc:
            raise WorkspaceError("cannot persist metadata archive root") from exc
        try:
            os.stat(identity, dir_fd=archive_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise WorkspaceError("cannot inspect metadata archive destination") from exc
        else:
            raise WorkspaceError("metadata archive destination already exists")
        try:
            current = os.stat(admin_name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as exc:
            raise WorkspaceError("cannot revalidate worktree administrative record") from exc
        if (current.st_dev, current.st_ino) != (pinned_admin.st_dev, pinned_admin.st_ino):
            raise WorkspaceError("worktree administrative identity changed before retirement")
        try:
            os.rename(
                admin_name,
                identity,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=archive_descriptor,
            )
        except OSError as exc:
            raise WorkspaceError("cannot archive targeted worktree metadata") from exc
        try:
            moved = os.stat(identity, dir_fd=archive_descriptor, follow_symlinks=False)
        except OSError as exc:
            restored = rollback_mismatched_admin_move(
                parent_descriptor,
                archive_descriptor,
                admin_parent,
                admin_name,
                identity,
                pinned_admin,
            )
            if not restored:
                raise MetadataRollbackError(
                    "cannot verify archived worktree metadata; administrative record remains at %s"
                    % metadata_path
                ) from exc
            persist_admin_compensation(parent_descriptor, archive_descriptor, metadata_path)
            raise WorkspaceError("cannot verify archived worktree metadata; retirement rolled back") from exc
        if (moved.st_dev, moved.st_ino) != (pinned_admin.st_dev, pinned_admin.st_ino):
            restored = rollback_mismatched_admin_move(
                parent_descriptor,
                archive_descriptor,
                admin_parent,
                admin_name,
                identity,
                pinned_admin,
            )
            if not restored:
                raise MetadataRollbackError(
                    "worktree administrative identity changed and compensation failed; metadata remains at %s"
                    % metadata_path
                )
            persist_admin_compensation(parent_descriptor, archive_descriptor, metadata_path)
            raise WorkspaceError(
                "worktree administrative identity changed during retirement; refusing to relocate archive entry"
            )
        try:
            os.fsync(parent_descriptor)
            os.fsync(archive_descriptor)
        except OSError as exc:
            restored = rollback_mismatched_admin_move(
                parent_descriptor,
                archive_descriptor,
                admin_parent,
                admin_name,
                identity,
                pinned_admin,
            )
            if restored:
                persist_admin_compensation(parent_descriptor, archive_descriptor, metadata_path)
                raise WorkspaceError("cannot persist archived worktree metadata; retirement rolled back") from exc
            raise MetadataRollbackError(
                "cannot persist archived worktree metadata; administrative record remains at %s"
                % metadata_path
            ) from exc
    return metadata_path


@contextlib.contextmanager
def pinned_worktree(path, entry, admin_dir):
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    git_descriptor = None
    admin_descriptor = None
    receipt_descriptor = None
    try:
        descriptor = os.open(path, flags)
        pinned = os.fstat(descriptor)
        if not stat.S_ISDIR(pinned.st_mode):
            raise WorkspaceError("worktree target is not a directory")
        git_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            git_flags |= os.O_NOFOLLOW
        git_descriptor = os.open(".git", git_flags, dir_fd=descriptor)
        git_info = os.fstat(git_descriptor)
        if not stat.S_ISREG(git_info.st_mode):
            raise WorkspaceError("unsafe linked-worktree gitfile")
        payload = os.read(git_descriptor, 65537)
        if len(payload) > 65536:
            raise WorkspaceError("linked-worktree gitfile is too large")
        text = payload.decode("utf-8", "surrogateescape")
        if text.endswith("\n"):
            text = text[:-1]
            if text.endswith("\r"):
                text = text[:-1]
        if not text.startswith("gitdir: "):
            raise WorkspaceError("malformed linked-worktree gitfile")
        pointer = text[len("gitdir: "):]
        if not os.path.isabs(pointer):
            pointer = os.path.join(path, pointer)
        if os.path.realpath(pointer) != os.path.realpath(admin_dir):
            raise WorkspaceError("linked-worktree identity changed")
        admin_descriptor = os.open(admin_dir, flags)
        pinned_admin = os.fstat(admin_descriptor)
        if not stat.S_ISDIR(pinned_admin.st_mode):
            raise WorkspaceError("worktree administrative target is not a directory")
        back_reference = os.fsdecode(read_admin_file(admin_descriptor, "gitdir"))
        if back_reference.endswith("\n"):
            back_reference = back_reference[:-1]
            if back_reference.endswith("\r"):
                back_reference = back_reference[:-1]
        if not os.path.isabs(back_reference):
            back_reference = os.path.join(admin_dir, back_reference)
        if os.path.realpath(back_reference) != os.path.realpath(os.path.join(path, ".git")):
            raise WorkspaceError("linked-worktree administrative identity changed")
        receipt_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            receipt_flags |= os.O_NOFOLLOW
        if entry is not None:
            receipt_descriptor = os.open(OWNER_RECEIPT_NAME, receipt_flags, dir_fd=admin_descriptor)
            receipt_info = os.fstat(receipt_descriptor)
            if not stat.S_ISREG(receipt_info.st_mode):
                raise WorkspaceError("unsafe worktree ownership receipt")
            receipt_payload = os.read(receipt_descriptor, 65537)
            if len(receipt_payload) > 65536:
                raise WorkspaceError("worktree ownership receipt is too large")
            try:
                receipt_matches = json.loads(receipt_payload.decode("utf-8")) == receipt_data(entry)
            except (UnicodeDecodeError, json.JSONDecodeError):
                receipt_matches = False
            if not receipt_matches:
                raise WorkspaceError("worktree ownership receipt changed")
        yield pinned, pinned_admin, admin_descriptor
    except OSError as exc:
        raise WorkspaceError("cannot pin linked-worktree identity") from exc
    finally:
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)
        if admin_descriptor is not None:
            os.close(admin_descriptor)
        if git_descriptor is not None:
            os.close(git_descriptor)
        if descriptor is not None:
            os.close(descriptor)


def restore_archived_worktree(path, archive_root, archive_path, expected_identity, archive_descriptor=None):
    try:
        parent = os.path.dirname(path)
        name = os.path.basename(path)
        with safe_directory(parent) as parent_descriptor:
            try:
                os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise OSError("original worktree path is occupied")
            if archive_descriptor is None:
                with safe_directory(archive_root) as pinned_archive:
                    archived = os.stat("worktree", dir_fd=pinned_archive, follow_symlinks=False)
                    if (archived.st_dev, archived.st_ino) != expected_identity:
                        raise OSError("archived worktree identity changed")
                    os.rename(
                        "worktree",
                        name,
                        src_dir_fd=pinned_archive,
                        dst_dir_fd=parent_descriptor,
                    )
                    os.fsync(pinned_archive)
            else:
                archived = os.stat("worktree", dir_fd=archive_descriptor, follow_symlinks=False)
                if (archived.st_dev, archived.st_ino) != expected_identity:
                    raise OSError("archived worktree identity changed")
                os.rename(
                    "worktree",
                    name,
                    src_dir_fd=archive_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                os.fsync(archive_descriptor)
            os.fsync(parent_descriptor)
            if archive_descriptor is None or directory_path_matches_descriptor(archive_root, archive_descriptor):
                os.rmdir(archive_root)
                os.fsync(parent_descriptor)
    except OSError as exc:
        raise WorkspaceError("worktree retirement rollback failed; content remains archived at %s" % archive_path) from exc


def remove(root, path, write=False, before_archive=None):
    with registry_operation(root, write) as registry_descriptor:
        status = build_status(root, registry_descriptor)
        tree = find_tree(status, path)
        if not tree["removable"]:
            raise WorkspaceError("worktree removal refused: %s" % ",".join(tree["blockers"]))
        registry = read_registry(status["primary_root"], registry_descriptor)
        entry = next((item for item in registry["entries"] if item["path"] == tree["path"]), None)
        if entry is None:
            raise WorkspaceError("worktree removal refused: registration missing")
        result = {
            "status": "archived" if write else "preview",
            "written": bool(write),
            "path": tree["path"],
            "archive_path": None,
            "metadata_archive_path": None,
        }
        if write:
            receipt = owner_receipt_path(tree["path"])
            admin_dir = os.path.dirname(receipt)
            common_dir = git_path(tree["path"], ["rev-parse", "--git-common-dir"])
            paths = retirement_paths(tree["path"], entry["identity"])
            archive_root, archive_path = paths
            metadata_archive_path = metadata_retirement_paths(common_dir, entry["identity"])[1]
            result["archive_path"] = archive_path
            result["metadata_archive_path"] = metadata_archive_path
            with pinned_worktree(tree["path"], entry, admin_dir) as pinned_values:
                pinned, _, admin_descriptor = pinned_values
                if (pinned.st_dev, pinned.st_ino) != (tree["device"], tree["inode"]):
                    raise WorkspaceError("worktree identity changed before retirement")
                if before_archive is not None:
                    before_archive({**result, "status": "planned", "written": False})
                prepare_retirement_paths(paths)
                checkout_parent = os.path.dirname(tree["path"])
                checkout_name = os.path.basename(tree["path"])
                with safe_directory(checkout_parent) as checkout_parent_descriptor, safe_directory(archive_root) as checkout_archive_descriptor:
                    try:
                        current_checkout = os.stat(
                            checkout_name,
                            dir_fd=checkout_parent_descriptor,
                            follow_symlinks=False,
                        )
                    except OSError as exc:
                        raise WorkspaceError("cannot revalidate checkout before retirement") from exc
                    if (current_checkout.st_dev, current_checkout.st_ino) != (pinned.st_dev, pinned.st_ino):
                        raise WorkspaceError("worktree identity changed before archive rename")
                    try:
                        os.rename(
                            checkout_name,
                            "worktree",
                            src_dir_fd=checkout_parent_descriptor,
                            dst_dir_fd=checkout_archive_descriptor,
                        )
                    except OSError as exc:
                        if directory_path_matches_descriptor(archive_root, checkout_archive_descriptor):
                            os.rmdir(archive_root)
                        raise WorkspaceError("cannot archive worktree atomically") from exc
                    archived = os.stat("worktree", dir_fd=checkout_archive_descriptor, follow_symlinks=False)
                    if (archived.st_dev, archived.st_ino) != (pinned.st_dev, pinned.st_ino):
                        foreign_identity = (archived.st_dev, archived.st_ino)
                        restore_archived_worktree(
                            tree["path"],
                            archive_root,
                            archive_path,
                            foreign_identity,
                            checkout_archive_descriptor,
                        )
                        raise WorkspaceError(
                            "worktree identity changed during retirement; refusing to relocate archive entry"
                        )
                    if not directory_path_matches_descriptor(archive_root, checkout_archive_descriptor):
                        restore_archived_worktree(
                            tree["path"],
                            archive_root,
                            archive_path,
                            (pinned.st_dev, pinned.st_ino),
                            checkout_archive_descriptor,
                        )
                        raise WorkspaceError("worktree archive path identity changed during retirement")
                    try:
                        os.fsync(checkout_parent_descriptor)
                        os.fsync(checkout_archive_descriptor)
                    except OSError as exc:
                        restore_archived_worktree(
                            tree["path"],
                            archive_root,
                            archive_path,
                            (pinned.st_dev, pinned.st_ino),
                            checkout_archive_descriptor,
                        )
                        raise WorkspaceError("cannot persist worktree archive; retirement rolled back") from exc
                    try:
                        metadata_archive_path = detach_admin_record(
                            admin_dir,
                            admin_descriptor,
                            common_dir,
                            entry["identity"],
                        )
                    except MetadataRollbackError as exc:
                        raise WorkspaceError(
                            "%s; checkout remains archived at %s" % (exc, archive_path)
                        ) from exc
                    except WorkspaceError:
                        restore_archived_worktree(
                            tree["path"],
                            archive_root,
                            archive_path,
                            (pinned.st_dev, pinned.st_ino),
                            checkout_archive_descriptor,
                        )
                        raise
                    if not directory_path_matches_descriptor(archive_root, checkout_archive_descriptor):
                        actual_root = locate_directory_descriptor(os.path.dirname(archive_root), checkout_archive_descriptor)
                        if not actual_root:
                            raise WorkspaceError("worktree archive path changed after metadata retirement")
                        archive_root = actual_root
                        archive_path = os.path.join(actual_root, "worktree")
            result["archive_path"] = archive_path
            write_registry(
                status["primary_root"],
                {"schema_version": 1, "entries": []},
                registry_descriptor,
            )
            ensure_registry_descriptor_current(status["primary_root"], registry_descriptor)
            if isinstance(registry_descriptor, RegistryOperation):
                def report_invalid_retirement():
                    raise WorkspaceError(
                        "retirement already preserved checkout at %s and metadata at %s"
                        % (archive_path, metadata_archive_path)
                    )

                registry_descriptor.add_invalid_rollback(report_invalid_retirement)
    return result


def prune(root, write=False):
    with registry_operation(root, write) as registry_descriptor:
        status = build_status(root, registry_descriptor)
        registered_prunable = [
            tree["path"] for tree in status["worktrees"] if tree["prunable"] and tree["kimiflow_registered"]
        ]
        if write and registered_prunable:
            raise WorkspaceError("worktree prune refused: registered worktree is prunable")
        available = status["safe_prune_available"] or status["registry_reconcile_available"]
        result = {
            "status": "pruned" if write else "preview",
            "written": bool(write),
            "available": available,
            "reconciled_paths": status["registry_reconcile_paths"] if write else [],
        }
        if write and status["safe_prune_available"]:
            run_git(status["primary_root"], ["worktree", "prune", "--expire", "now"])
        if write and status["registry_reconcile_available"]:
            stale = set(status["registry_reconcile_paths"])
            registry = read_registry(status["primary_root"], registry_descriptor)
            registry["entries"] = [entry for entry in registry["entries"] if entry["path"] not in stale]
            write_registry(status["primary_root"], registry, registry_descriptor)
            ensure_registry_descriptor_current(status["primary_root"], registry_descriptor)
    return result


def parser():
    result = argparse.ArgumentParser(prog="workspace-preflight.sh")
    sub = result.add_subparsers(dest="command")
    for name in ("status", "prune"):
        item = sub.add_parser(name)
        item.add_argument("--root")
        item.add_argument("--write", action="store_true")
        item.add_argument("--pretty", action="store_true")
    for name in ("register", "remove"):
        item = sub.add_parser(name)
        item.add_argument("--root")
        item.add_argument("--path", required=True)
        if name == "register":
            item.add_argument("--run", required=True)
        item.add_argument("--write", action="store_true")
        item.add_argument("--pretty", action="store_true")
    return result


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0].startswith("-"):
        raw.insert(0, "status")
    args = parser().parse_args(raw)
    command = args.command
    try:
        if command == "status":
            result = build_status(args.root)
        elif command == "register":
            result = register(args.root, args.path, args.run, args.write)
        elif command == "remove":
            result = remove(args.root, args.path, args.write)
        else:
            result = prune(args.root, args.write)
        print(json.dumps(result, ensure_ascii=True, indent=2 if args.pretty else None, sort_keys=True))
        return 0
    except WorkspaceError as exc:
        print("workspace-preflight: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
