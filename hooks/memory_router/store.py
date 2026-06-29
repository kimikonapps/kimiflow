"""All filesystem IO for the memory-router CLI: atomic writes + lenient readers."""
import json
import os
import tempfile


def atomic_write(path, data, mode=0o644, refuse_symlink=True):
    if refuse_symlink and os.path.islink(path):
        raise ValueError("refusing to write through symlink: %s" % path)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_line(path, text):
    # Faithful to Bash `printf '%s\n' "$row" >> "$file"`: append-mode write that
    # follows an existing symlink (no guard) and creates the file if absent. The
    # caller is responsible for ensuring the parent directory exists.
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text)


def read_text(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as handle:
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
