"""`record` subcommand: append a learning row, refresh bounded MEMORY.md/USER.md,
re-curate, and print the RECORDED line. Behavioral port of the Bash cmd_record
(3508-3543) @ kimiflow--v0.1.50."""
import contextlib
import io
import sys

from . import curate, memory_md, paths, writes
from .cli import die, resolve_root, usage


def run(argv):
    root = ""
    summary = ""
    topic = ""
    kind = "learning"
    scope = "project"
    confidence = "medium"
    sensitivity = "normal"
    status = "current"
    evidence = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--summary":
            i += 1
            summary = argv[i] if i < len(argv) else ""
        elif arg == "--topic":
            i += 1
            topic = argv[i] if i < len(argv) else ""
        elif arg == "--kind":
            i += 1
            kind = argv[i] if i < len(argv) else ""
        elif arg == "--scope":
            i += 1
            scope = argv[i] if i < len(argv) else ""
        elif arg == "--confidence":
            i += 1
            confidence = argv[i] if i < len(argv) else ""
        elif arg == "--sensitivity":
            i += 1
            sensitivity = argv[i] if i < len(argv) else ""
        elif arg == "--status":
            i += 1
            status = argv[i] if i < len(argv) else ""
        elif arg == "--evidence":
            i += 1
            evidence.append(argv[i] if i < len(argv) else "")
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("record: unknown argument: %s" % arg, 2)
        i += 1

    if not summary:
        return die("record requires --summary", 2)
    if not topic:
        return die("record requires --topic", 2)
    if len(evidence) == 0:
        return die("record requires at least one --evidence", 2)

    root = resolve_root(root)

    try:
        rid = writes.append_learning_row(root, kind, scope, topic, summary, evidence,
                                         confidence, sensitivity, status)
    except writes.SecurityGateError as exc:
        # Bash append_learning_row prints this line itself; the port raises, so the
        # subcommand formats it (matching `|| return 1`).
        sys.stderr.write("memory-router: memory security gate closed: %s\n" % ",".join(exc.reasons))
        return 1

    if scope in ("user", "profile"):
        memory_md.write_bounded_user_memory(root)
    else:
        memory_md.write_bounded_memory(root)

    # Bash `cmd_curate --root "$root" --write >/dev/null`: suppress only stdout.
    with contextlib.redirect_stdout(io.StringIO()):
        curate.run(["--root", root, "--write"])

    path = paths.rel_path(root, paths.rows_path_for_scope(root, scope))
    sys.stdout.write("RECORDED\t%s\t%s\n" % (path, rid))
    return 0
