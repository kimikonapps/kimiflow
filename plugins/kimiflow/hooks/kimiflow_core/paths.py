"""Path and root-resolution helpers shared by the Python ports."""

import os
import subprocess


class RootResolutionError(ValueError):
    pass


def _logical_abs(path, cwd=None):
    base = cwd or os.getcwd()
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path))


def git_root(cwd=None):
    base = cwd or os.getcwd()
    try:
        proc = subprocess.run(
            ["git", "-C", base, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    root = proc.stdout.strip()
    return root or None


def resolve_root(root=None, cwd=None, mode="strict"):
    """Resolve a project root.

    mode:
      strict: explicit roots must exist, else raise.
      observational: explicit missing roots are returned as logical paths.
      hook_safe: invalid cwd/root returns None so hooks can no-op.
    """
    base = cwd or os.getcwd()
    if root:
        resolved = _logical_abs(root, base)
        if os.path.isdir(resolved):
            return resolved
        if mode == "strict":
            raise RootResolutionError("cannot resolve root: %s" % root)
        if mode == "hook_safe":
            return None
        return resolved

    if mode == "hook_safe" and not os.path.isdir(base):
        return None
    git = git_root(base)
    if git:
        return git
    if os.path.isdir(base):
        return _logical_abs(base)
    if mode == "strict":
        raise RootResolutionError("cannot resolve cwd: %s" % base)
    return None


def rel_path(root, path):
    root = os.path.normpath(root)
    path = os.path.normpath(path)
    try:
        rel = os.path.relpath(path, root)
    except ValueError:
        return path
    if rel == ".":
        return "."
    if rel.startswith("..%s" % os.sep) or rel == "..":
        return path
    return rel


def reject_relative_traversal(path):
    norm = os.path.normpath(path)
    if norm == ".." or norm.startswith("..%s" % os.sep) or os.path.isabs(path):
        raise ValueError("path must stay inside the project")
    return norm
