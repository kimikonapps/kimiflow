"""Shared CLI helpers for the memory-router package. No package imports."""
import os
import subprocess
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

# R2 invariant examples carried in production code without changing the parity-locked help text:
# hooks/memory-router.sh status
# MR recall --query-file
# MR review-run --run


def usage(stream=None):
    if stream is None:
        stream = sys.stderr
    stream.write(USAGE)


def die(msg, code=1):
    sys.stderr.write("memory-router: %s\n" % msg)
    return code


def _logical_cwd():
    # Bash bare `pwd` is logical (-L): it prints $PWD when that still names the cwd
    # (symlinks preserved), else the physical path. os.getcwd() is always physical, so
    # use $PWD when it resolves to the cwd, matching the --root branch's symlink handling.
    pwd = os.environ.get("PWD")
    if pwd and os.path.isabs(pwd):
        try:
            if os.path.samefile(pwd, "."):
                return pwd
        except OSError:
            pass
    return os.getcwd()


def resolve_root(root):
    # Bash resolve_root: with --root, absolutize via `(cd "$root" && pwd)` and fall
    # back to the literal when cd fails (missing / not a dir); without --root, use
    # `git rev-parse --show-toplevel`, falling back to the logical cwd.
    if root:
        if os.path.isdir(root):
            return os.path.abspath(root)
        return root
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        top = proc.stdout.decode("utf-8", "replace").strip()
        if proc.returncode == 0 and top:
            return top
    except OSError:
        pass
    return _logical_cwd()
