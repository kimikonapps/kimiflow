"""kimiflow memory-router CLI (Python port). Run: python3 hooks/memory_router <cmd> ..."""
import sys

# Verbatim copy of the Bash header (memory-router.sh lines 1-17). The Bash `usage()`
# does `sed -n '1,17p' "$0" >&2`; after cutover the entrypoint is a shim, so the header
# is embedded here and parity-checked against the pinned old Bash.
USAGE = (
    "#!/usr/bin/env bash\n"
    "# kimiflow — token-cheap local memory router. Orchestrator-invoked, not a hook.\n"
    "#\n"
    "# Usage:\n"
    "#   memory-router.sh status [--root <path>] [--pretty]\n"
    "#   memory-router.sh recall --query <text>|--query-file <path> [--root <path>] [--max <n>] [--write <path>] [--pretty]\n"
    "#   memory-router.sh history [--query <text>|--query-file <path>] [--root <path>] [--max <n>] [--write] [--pretty]\n"
    "#   memory-router.sh metrics [--root <path>] [--global] [--global-purge] [--pretty]\n"
    "#   memory-router.sh classify --input <path>|--text <text> [--pretty]\n"
    "#   memory-router.sh record --summary <text> --topic <topic> --evidence <ref>... [--root <path>] [--kind <kind>] [--scope <scope>] [--confidence <level>] [--sensitivity <level>] [--status <status>]\n"
    "#   memory-router.sh review-run --run <path> [--root <path>] [--write] [--pretty] [--skip <reason>]\n"
    "#   memory-router.sh verify-run --run <path> [--root <path>]\n"
    "#   memory-router.sh curate [--root <path>] [--write] [--pretty]\n"
    "#   memory-router.sh index [--root <path>] [--write] [--pretty]\n"
    "#   memory-router.sh consolidate [--root <path>] [--write] [--pretty]\n"
    "#   memory-router.sh propose [--root <path>] [--write] [--approve <id>] [--reject <id>] [--reason <text>] [--apply] [--pretty]\n"
    "#   memory-router.sh provider <status|health|setup|detect|connect|configure|prefetch|sync> [--root <path>] [--type <obsidian|none>] [--available <true|false>] [--path <path>] [--host <codex|claude|all>] [--pretty]\n"
)

# Subcommand table. Foundation registers none yet; per-subsystem plans add entries
# mapping a command name to a `run(argv: list[str]) -> int` callable.
COMMANDS = {}


def usage(stream=None):
    if stream is None:
        stream = sys.stderr
    stream.write(USAGE)


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
        usage()
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
