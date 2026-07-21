"""All filesystem IO for the memory-router CLI: atomic writes + lenient readers."""
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import secrets
import stat
import tempfile
import threading

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback retains compare checks.
    fcntl = None


class _ObjectPairs(list):
    pass


class ConcurrentWriteError(RuntimeError):
    pass


_LOCK_STATE = threading.local()


def _active_anchor(path):
    directory = os.path.abspath(os.path.dirname(path) or ".")
    for anchor in reversed(getattr(_LOCK_STATE, "local_anchors", ())):
        if anchor["path"] == directory:
            return anchor
    return None


def _anchor_current(anchor):
    try:
        pinned = os.fstat(anchor["descriptor"])
        current = os.stat(anchor["path"], follow_symlinks=False)
    except OSError:
        return False
    return (stat.S_ISDIR(current.st_mode)
            and (pinned.st_dev, pinned.st_ino) == (current.st_dev, current.st_ino))


def _file_snapshot_at(directory_descriptor, name):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError:
        return None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            return None
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        anchored = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    identity = (before.st_dev, before.st_ino)
    stable = (
        identity == (after.st_dev, after.st_ino)
        and identity == (anchored.st_dev, anchored.st_ino)
        and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        == (anchored.st_size, anchored.st_mtime_ns, anchored.st_ctime_ns)
    )
    receipt = (anchored.st_size, anchored.st_mtime_ns)
    return (
        identity, b"".join(chunks), stat.S_IMODE(anchored.st_mode), receipt
    ) if stable else None


def _native_exchange_at(directory_descriptor, source, target):
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renameatx_np"):
        exchange = libc.renameatx_np
        exchange.argtypes = [ctypes.c_int, ctypes.c_char_p,
                             ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        exchange.restype = ctypes.c_int
        result = exchange(directory_descriptor, os.fsencode(source),
                          directory_descriptor, os.fsencode(target), 0x00000002)
    elif hasattr(libc, "renameat2"):
        exchange = libc.renameat2
        exchange.argtypes = [ctypes.c_int, ctypes.c_char_p,
                             ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        exchange.restype = ctypes.c_int
        result = exchange(directory_descriptor, os.fsencode(source),
                          directory_descriptor, os.fsencode(target), 0x00000002)
    else:
        raise OSError(errno.ENOTSUP, "atomic path exchange unavailable")
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), target)


def _exchange_paths(source, target):
    """Atomically swap two existing paths or fail closed when unsupported."""
    source_anchor = _active_anchor(source)
    target_anchor = _active_anchor(target)
    if source_anchor is not None and source_anchor is target_anchor:
        return _native_exchange_at(
            source_anchor["descriptor"], os.path.basename(source), os.path.basename(target)
        )
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renamex_np"):
        exchange = libc.renamex_np
        exchange.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        exchange.restype = ctypes.c_int
        result = exchange(os.fsencode(source), os.fsencode(target), 0x00000002)
    elif hasattr(libc, "renameat2"):
        exchange = libc.renameat2
        exchange.argtypes = [ctypes.c_int, ctypes.c_char_p,
                             ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        exchange.restype = ctypes.c_int
        result = exchange(-100, os.fsencode(source), -100, os.fsencode(target), 0x00000002)
    else:
        raise OSError(errno.ENOTSUP, "atomic path exchange unavailable")
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), target)


def _file_snapshot(path):
    """Read one stable path identity and payload without following a symlink."""
    anchor = _active_anchor(path)
    if anchor is not None:
        return _file_snapshot_at(anchor["descriptor"], os.path.basename(path))
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        before = os.fstat(descriptor)
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    identity = (before.st_dev, before.st_ino)
    stable = (
        identity == (after.st_dev, after.st_ino)
        and identity == (current.st_dev, current.st_ino)
        and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        == (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
    )
    mode = stat.S_IMODE(current.st_mode)
    receipt = (current.st_size, current.st_mtime_ns)
    return (identity, b"".join(chunks), mode, receipt) if stable else None


def local_file_snapshot(root, path):
    """Read a regular local file through a no-follow directory-fd chain.

    The returned directory identities bind the payload to the workspace path,
    so replacing any parent directory during a privacy check is observable.
    """
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        return None
    root_path = os.path.realpath(os.path.abspath(root))
    path = os.path.abspath(path)
    lexical_root = os.path.abspath(root)
    try:
        if os.path.commonpath((lexical_root, path)) != lexical_root:
            return None
    except ValueError:
        return None
    relative = os.path.relpath(path, lexical_root)
    parts = relative.split(os.sep)
    if relative == os.curdir or any(part in ("", os.curdir, os.pardir) for part in parts):
        return None

    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    nofollow = os.O_NOFOLLOW
    descriptors = []
    try:
        current = os.open(root_path, directory_flags | nofollow)
        descriptors.append(current)
        directory_identities = []
        root_stat = os.fstat(current)
        directory_identities.append((root_stat.st_dev, root_stat.st_ino))
        for part in parts[:-1]:
            current = os.open(
                part, directory_flags | nofollow, dir_fd=current
            )
            descriptors.append(current)
            current_stat = os.fstat(current)
            directory_identities.append((current_stat.st_dev, current_stat.st_ino))

        descriptor = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            return None
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        anchored = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
    except OSError:
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)

    identity = (before.st_dev, before.st_ino)
    stable = (
        identity == (after.st_dev, after.st_ino)
        and identity == (anchored.st_dev, anchored.st_ino)
        and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        == (anchored.st_size, anchored.st_mtime_ns, anchored.st_ctime_ns)
    )
    if not stable:
        return None
    return (identity, b"".join(chunks), stat.S_IMODE(anchored.st_mode),
            tuple(directory_identities))


def _same_snapshot(left, right):
    return left is not None and right is not None and left == right


def _same_source_generation(current, expected):
    """Match a prior source while permitting an in-place permission update.

    The stat receipt detects an unlink/recreate ABA even when Linux immediately
    reuses the old inode and the replacement contains identical bytes. A chmod
    on the still-pinned file is intentionally accepted so the writer preserves
    the newer permissions instead of treating them as a content conflict.
    """
    if current is None or expected is None or current[:2] != expected[:2]:
        return False
    return current[3] == expected[3]


def stable_file_snapshot(path, missing_ok=False, allow_detached=False):
    """Return a stable regular-file snapshot, distinguishing missing from unsafe."""
    anchor = _active_anchor(path)
    if anchor is not None:
        if not allow_detached and not _anchor_current(anchor):
            raise ConcurrentWriteError("local path parent changed")
        try:
            info = os.stat(os.path.basename(path), dir_fd=anchor["descriptor"],
                           follow_symlinks=False)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise ValueError("required local file is missing: %s" % path)
        except OSError as exc:
            raise ValueError("unsafe local file: %s" % path) from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("unsafe local file: %s" % path)
    else:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise ValueError("required local file is missing: %s" % path)
        except OSError as exc:
            raise ValueError("unsafe local file: %s" % path) from exc
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("unsafe local file: %s" % path)
    snapshot = _file_snapshot(path)
    if snapshot is None:
        raise ConcurrentWriteError("local file changed while reading: %s" % path)
    return snapshot


def stable_local_file_bytes(root, path, missing_ok=False):
    """Read one workspace-local regular file without following any path symlink."""
    snapshot = local_file_snapshot(root, path)
    if snapshot is not None:
        return snapshot[1]
    if missing_ok and not os.path.lexists(path):
        return None
    raise ValueError("unsafe local file: %s" % path)


def _retain_conflict_copy(path):
    recovery = path + ".recovery"
    try:
        anchor = _active_anchor(path)
        if anchor is not None:
            os.rename(os.path.basename(path), os.path.basename(recovery),
                      src_dir_fd=anchor["descriptor"], dst_dir_fd=anchor["descriptor"])
        else:
            os.rename(path, recovery)
    except OSError:
        return path
    return recovery


def _unlink_path(path):
    anchor = _active_anchor(path)
    if anchor is not None:
        os.unlink(os.path.basename(path), dir_fd=anchor["descriptor"])
    else:
        os.unlink(path)


def _replace_path(source, target):
    source_anchor = _active_anchor(source)
    target_anchor = _active_anchor(target)
    if source_anchor is not None and source_anchor is target_anchor:
        os.replace(os.path.basename(source), os.path.basename(target),
                   src_dir_fd=source_anchor["descriptor"],
                   dst_dir_fd=source_anchor["descriptor"])
    else:
        os.replace(source, target)


def _temporary_file(path):
    anchor = _active_anchor(path)
    if anchor is None:
        return tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.",
                                dir=os.path.dirname(path) or ".")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(32):
        name = os.path.basename(path) + ".tmp." + secrets.token_hex(8)
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=anchor["descriptor"])
            return descriptor, os.path.join(anchor["path"], name)
        except FileExistsError:
            continue
    raise OSError(errno.EEXIST, "cannot allocate atomic temporary file", path)


def _restore_exchange_conflict(tmp, path, candidate_snapshot, max_rounds=4):
    """Restore through bounded exchanges while the canonical path always exists."""
    current = _file_snapshot(path)
    if current is None:
        recovery = _retain_conflict_copy(tmp)
        raise ConcurrentWriteError(
            "source changed during rewrite; recovery copy retained at %s"
            % os.path.basename(recovery)
        )
    if not _same_snapshot(current, candidate_snapshot):
        # A post-publication writer already owns the canonical path and wins.
        try:
            _unlink_path(tmp)
        except OSError:
            pass
        return

    expected_target = candidate_snapshot
    for _round in range(max_rounds):
        desired = _file_snapshot(tmp)
        target_before = _file_snapshot(path)
        if desired is None or target_before is None:
            break
        if not _same_snapshot(target_before, expected_target):
            # A writer between recovery rounds already owns the canonical path and
            # is newer than the version waiting at `tmp`.
            try:
                _unlink_path(tmp)
            except OSError:
                pass
            return
        try:
            _exchange_paths(tmp, path)
        except OSError:
            break
        installed = _file_snapshot(path)
        displaced = _file_snapshot(tmp)
        if installed is None or displaced is None:
            break
        if _same_snapshot(installed, desired) and _same_snapshot(displaced, target_before):
            try:
                _unlink_path(tmp)
            except OSError:
                pass
            return
        if not _same_snapshot(installed, desired):
            # A writer after the exchange owns the canonical path and wins.
            try:
                _unlink_path(tmp)
            except OSError:
                pass
            return
        # A writer immediately before the exchange is now preserved at `tmp` and is
        # newer than the installed target. The next bounded exchange promotes it.
        expected_target = installed

    recovery = _retain_conflict_copy(tmp)
    raise ConcurrentWriteError(
        "source changed repeatedly; recovery copy retained at %s"
        % os.path.basename(recovery)
    )


@contextlib.contextmanager
def path_lock(path):
    """Serialize cooperating read/modify/write operations for one absolute path."""
    key = os.path.abspath(path)
    held = getattr(_LOCK_STATE, "held", set())
    if key in held or fcntl is None:
        yield
        return
    lock_dir = os.path.join(
        tempfile.gettempdir(), "kimiflow-memory-router-locks-%s" % getattr(os, "getuid", lambda: 0)()
    )
    os.makedirs(lock_dir, mode=0o700, exist_ok=True)
    lock_path = os.path.join(lock_dir, hashlib.sha256(os.fsencode(key)).hexdigest() + ".lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _LOCK_STATE.held = held | {key}
        yield
    finally:
        _LOCK_STATE.held = held
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextlib.contextmanager
def local_path_guard(root, directory):
    """Pin one workspace directory and route writes through its descriptor."""
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise ValueError("descriptor-anchored local paths are unavailable")
    lexical_root = os.path.abspath(root)
    directory = os.path.abspath(directory)
    try:
        if os.path.commonpath((lexical_root, directory)) != lexical_root:
            raise ValueError("path escapes root")
    except ValueError:
        raise ValueError("path escapes root")
    relative = os.path.relpath(directory, lexical_root)
    parts = [] if relative == os.curdir else relative.split(os.sep)
    if any(part in ("", os.curdir, os.pardir) for part in parts):
        raise ValueError("unsafe local directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        current = os.open(os.path.realpath(lexical_root), flags)
        descriptors.append(current)
        for part in parts:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
        info = os.fstat(current)
        anchor = {
            "path": directory,
            "descriptor": current,
            "identity": (info.st_dev, info.st_ino),
        }
        if not _anchor_current(anchor):
            raise ConcurrentWriteError("local path parent changed")
    except ConcurrentWriteError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise
    except OSError as exc:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise ValueError("unsafe local directory: %s" % directory) from exc
    previous = getattr(_LOCK_STATE, "local_anchors", ())
    _LOCK_STATE.local_anchors = previous + (anchor,)
    try:
        yield anchor
    finally:
        _LOCK_STATE.local_anchors = previous
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def parse_json_object_strict(text):
    """Parse one JSON object while rejecting duplicate keys at every nesting level."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def top_level_string_values(text, key):
    """Return every string value for a top-level key, retaining duplicate keys."""
    try:
        value = json.loads(text, object_pairs_hook=_ObjectPairs)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, _ObjectPairs):
        return []
    return [item for name, item in value if name == key and isinstance(item, str) and item]


def require_local_path(root, path):
    """Reject paths outside root or reached through a symlink below that root."""
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    try:
        if os.path.commonpath((root, path)) != root:
            raise ValueError("path escapes root")
    except ValueError:
        raise ValueError("path escapes root")
    current = root
    relative = os.path.relpath(path, root)
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise ValueError("refusing symlinked local path: %s" % current)
    return path


def ensure_local_directory(root, directory, mode=0o700):
    """Create a workspace-local directory chain without following symlinks."""
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise ValueError("descriptor-anchored local paths are unavailable")
    lexical_root = os.path.abspath(root)
    directory = os.path.abspath(directory)
    try:
        if os.path.commonpath((lexical_root, directory)) != lexical_root:
            raise ValueError("path escapes root")
    except ValueError:
        raise ValueError("path escapes root")
    relative = os.path.relpath(directory, lexical_root)
    parts = [] if relative == os.curdir else relative.split(os.sep)
    if any(part in ("", os.curdir, os.pardir) for part in parts):
        raise ValueError("unsafe local directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        current = os.open(os.path.realpath(lexical_root), flags)
        descriptors.append(current)
        for part in parts:
            try:
                child = os.open(part, flags, dir_fd=current)
            except FileNotFoundError:
                os.mkdir(part, mode, dir_fd=current)
                child = os.open(part, flags, dir_fd=current)
            descriptors.append(child)
            current = child
    except OSError as exc:
        raise ValueError("unsafe local directory: %s" % directory) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return directory


def atomic_write(path, data, mode=0o644, refuse_symlink=True, expected=None,
                 expected_snapshot=None, allow_detached=False):
    with path_lock(path):
        anchor = _active_anchor(path)
        if anchor is not None and not allow_detached and not _anchor_current(anchor):
            raise ConcurrentWriteError("local path parent changed")
        if refuse_symlink:
            if anchor is not None:
                try:
                    target_info = os.stat(os.path.basename(path), dir_fd=anchor["descriptor"],
                                          follow_symlinks=False)
                except FileNotFoundError:
                    target_info = None
                if target_info is not None and stat.S_ISLNK(target_info.st_mode):
                    raise ValueError("refusing to write through symlink: %s" % path)
            elif os.path.islink(path):
                raise ValueError("refusing to write through symlink: %s" % path)
        initial_snapshot = None
        if expected is not None:
            initial_snapshot = _file_snapshot(path)
            if (initial_snapshot is None
                    or initial_snapshot[1] != expected.encode("utf-8")
                    or (expected_snapshot is not None
                        and not _same_source_generation(
                            initial_snapshot, expected_snapshot
                        ))):
                raise ConcurrentWriteError("source changed during rewrite")
            mode = initial_snapshot[2]
        fd, tmp = _temporary_file(path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
            tmp_anchor = _active_anchor(tmp)
            if tmp_anchor is not None:
                os.chmod(os.path.basename(tmp), mode, dir_fd=tmp_anchor["descriptor"])
            else:
                os.chmod(tmp, mode)
            if expected is None:
                _replace_path(tmp, path)
                tmp = ""
            else:
                candidate_snapshot = _file_snapshot(tmp)
                if candidate_snapshot is None:
                    raise OSError(errno.EIO, "cannot snapshot prepared atomic write", tmp)
                _exchange_paths(tmp, path)
                displaced_snapshot = _file_snapshot(tmp)
                installed_snapshot = _file_snapshot(path)
                if (
                    displaced_snapshot is None
                    or not _same_snapshot(displaced_snapshot, initial_snapshot)
                    or not _same_snapshot(installed_snapshot, candidate_snapshot)
                ):
                    conflict_tmp = tmp
                    tmp = ""
                    _restore_exchange_conflict(
                        conflict_tmp, path, candidate_snapshot
                    )
                    raise ConcurrentWriteError("source changed during rewrite")
                if (anchor is not None and not allow_detached
                        and not _anchor_current(anchor)):
                    _exchange_paths(tmp, path)
                    restored = _file_snapshot(path)
                    try:
                        _unlink_path(tmp)
                    except OSError:
                        pass
                    tmp = ""
                    if not _same_snapshot(restored, initial_snapshot):
                        raise ConcurrentWriteError(
                            "local path parent changed; recovery could not be verified"
                        )
                    raise ConcurrentWriteError("local path parent changed")
                try:
                    _unlink_path(tmp)
                except OSError:
                    pass
                tmp = ""
            if (anchor is not None and not allow_detached
                    and not _anchor_current(anchor)):
                raise ConcurrentWriteError("local path parent changed")
        finally:
            if tmp:
                try:
                    _unlink_path(tmp)
                except OSError:
                    pass


def append_line(path, text):
    # Faithful to Bash `printf '%s\n' "$row" >> "$file"`: append-mode write that
    # follows an existing symlink (no guard) and creates the file if absent. The
    # caller is responsible for ensuring the parent directory exists.
    with path_lock(path):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def read_text(path, default="", newline=None):
    try:
        with open(path, "r", encoding="utf-8", newline=newline) as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return default


def read_json(path, default=None):
    # Lenient single-object JSON read (Bash guards with `jq -e . file`): returns
    # `default` when the file is missing, unreadable, or not valid JSON.
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def read_jsonl(path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def read_jsonl_objects_strict(path):
    """Read only duplicate-free JSON objects from an LF-delimited JSONL file."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError):
        return []
    return jsonl_objects_strict_text(content)


def jsonl_objects_strict_text(content):
    """Parse duplicate-free JSON objects from already trusted LF-delimited text."""
    rows = []
    for raw in content.split("\n"):
        stripped = raw.strip()
        if not stripped:
            continue
        row = parse_json_object_strict(stripped)
        if row is not None:
            rows.append(row)
    return rows


def read_local_jsonl_objects_strict(root, path, missing_ok=False):
    """Read strict JSONL from one descriptor-anchored workspace-local source."""
    data = stable_local_file_bytes(root, path, missing_ok=missing_ok)
    if data is None:
        return []
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("unsafe local JSONL encoding: %s" % path) from exc
    return jsonl_objects_strict_text(content)


def read_jsonl_with_lines(path):
    # Lenient JSONL read that keeps the raw lines: [(raw_line, row_or_None)]. row is
    # None for blank/malformed/non-dict lines, so rewrite callers can re-serialize the
    # parsed rows while preserving everything else verbatim, in place (audit fix B3-P2).
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.rstrip("\n")
                row = None
                stripped = raw.strip()
                if stripped:
                    try:
                        parsed = json.loads(stripped)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict):
                        row = parsed
                entries.append((raw, row))
    except OSError:
        return []
    return entries
