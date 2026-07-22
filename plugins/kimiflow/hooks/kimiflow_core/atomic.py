"""Atomic file writes for kimiflow project state."""

import os
import tempfile


def atomic_write(path, data, mode=0o600, refuse_symlink=True):
    if refuse_symlink and os.path.islink(path):
        raise ValueError("refusing to write through symlink: %s" % path)
    directory = os.path.dirname(path) or "."
    basename = os.path.basename(path) or "tmp"
    fd, tmp = tempfile.mkstemp(prefix=".%s.tmp." % basename, dir=directory)
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
