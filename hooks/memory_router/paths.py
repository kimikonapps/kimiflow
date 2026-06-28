"""Path/scope helpers (verbatim ports of the Bash rel_path / *_for_scope)."""


def rel_path(root, path):
    if path == root:
        return "."
    prefix = root + "/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return path


def rows_path_for_scope(root, scope):
    if scope in ("user", "profile"):
        return "%s/.kimiflow/project/USER.jsonl" % root
    return "%s/.kimiflow/project/LEARNINGS.jsonl" % root


def id_prefix_for_scope(scope):
    if scope in ("user", "profile"):
        return "user"
    return "learn"
