"""All filesystem IO for the memory-router CLI: atomic writes + lenient readers."""
import contextlib
import ctypes
import errno
import hashlib
import json
import os
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


def _exchange_paths(source, target):
    """Atomically swap two existing paths or fail closed when unsupported."""
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
    return (identity, b"".join(chunks), mode) if stable else None


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


def _retain_conflict_copy(path):
    recovery = path + ".recovery"
    try:
        os.rename(path, recovery)
    except OSError:
        return path
    return recovery


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
            os.unlink(tmp)
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
                os.unlink(tmp)
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
                os.unlink(tmp)
            except OSError:
                pass
            return
        if not _same_snapshot(installed, desired):
            # A writer after the exchange owns the canonical path and wins.
            try:
                os.unlink(tmp)
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


def atomic_write(path, data, mode=0o644, refuse_symlink=True, expected=None):
    with path_lock(path):
        if refuse_symlink and os.path.islink(path):
            raise ValueError("refusing to write through symlink: %s" % path)
        directory = os.path.dirname(path) or "."
        expected_snapshot = None
        if expected is not None:
            expected_snapshot = _file_snapshot(path)
            if expected_snapshot is None or expected_snapshot[1] != expected.encode("utf-8"):
                raise ConcurrentWriteError("source changed during rewrite")
            mode = expected_snapshot[2]
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
            os.chmod(tmp, mode)
            if expected is None:
                os.replace(tmp, path)
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
                    or not _same_snapshot(displaced_snapshot, expected_snapshot)
                    or not _same_snapshot(installed_snapshot, candidate_snapshot)
                ):
                    conflict_tmp = tmp
                    tmp = ""
                    _restore_exchange_conflict(
                        conflict_tmp, path, candidate_snapshot
                    )
                    raise ConcurrentWriteError("source changed during rewrite")
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                tmp = ""
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
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
    rows = []
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            raw_lines = handle.read().split("\n")
    except (OSError, UnicodeDecodeError):
        return rows
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        row = parse_json_object_strict(stripped)
        if row is not None:
            rows.append(row)
    return rows


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
