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
from . import consolidate as _consolidate
from . import curate as _curate
from . import history as _history
from . import index as _index
from . import metrics as _metrics
from . import outcomes as _outcomes
from . import propose as _propose
from . import provider as _provider
from . import recall as _recall
from . import record as _record
from . import review as _review
from . import runs as _runs
from . import status as _status

COMMANDS = {
    "classify": _classify.run,
    "consolidate": _consolidate.run,
    "curate": _curate.run,
    "history": _history.run,
    "index": _index.run,
    "metrics": _metrics.run,
    "evaluate-run": _outcomes.run,
    "propose": _propose.run,
    "provider": _provider.run,
    "recall": _recall.run,
    "record": _record.run,
    "review-run": _review.run,
    "status": _status.run,
    "verify-run": _runs.run,
}

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
