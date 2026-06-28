"""kimiflow memory-router CLI (Python port). Run: python3 hooks/memory_router <cmd> ..."""
import sys

from .cli import USAGE, usage, die  # re-exported so `from memory_router.__main__ import USAGE` works

# Subcommand table. Foundation registers none yet; per-subsystem plans add entries
# mapping a command name to a `run(argv: list[str]) -> int` callable.
# (Registered at the bottom of the file after imports and function definitions.)


def main(argv):
    if not argv:
        usage()
        return 2
    cmd = argv[0]
    if cmd in ("--help", "-h", "help"):
        usage()
        return 0
    handler = COMMANDS.get(cmd)
    if handler is None:
        return die("unknown command: %s" % cmd, 2)
    return handler(argv[1:])


from . import classify as _classify

COMMANDS = {
    "classify": _classify.run,
}

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
