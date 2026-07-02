"""Python port of hooks/improvements-status.sh."""

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import json

from . import paths


MARKER_SUBSTR = "kimiflow:queue-done"
USAGE = """#!/usr/bin/env bash
# kimiflow — local workqueue close-back helper.
#
# Marks slices in the local workqueues as done so the launcher stops counting them open.
# Canonical done-state = an in-place marker line directly under the slice heading:
#   <!-- kimiflow:queue-done id=<id> commit=<sha> date=<YYYY-MM-DD> -->
# The launcher's count_section_items skips any open-section block carrying this marker.
#
# Commands:
#   improvements-status.sh list      [--queue improvements|findings] [--root <path>] [--json|--pretty]
#   improvements-status.sh mark-done <id> [--queue ...] [--commit <sha>] [--root <path>] [--write]
#   improvements-status.sh reopen    <id> [--queue ...] [--root <path>] [--write]
#
# Queues: improvements -> .kimiflow/project/IMPROVEMENTS.md (open section "## Priorisierte Slices"/"## Prioritized Slices")
#         findings     -> .kimiflow/project/FINDINGS.md      (open section "## Offen"/"## Open")
# Slice id: explicit token (e.g. KF-F-001 -> kf-f-001) if the heading starts with one, else a title slug.
# list is read-only; mark-done/reopen need --write to persist (else dry-run). Atomic write (mktemp + mv -f).
#
# R2 invariant examples:
#   improvements-status.sh
#   mark-done <id> --commit <sha> --write
set -u
"""


def usage():
    sys.stderr.write(USAGE)


def die(message, code=1):
    sys.stderr.write("improvements-status: %s\n" % message)
    return code


def iso_date():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def queue_file(queue):
    if queue == "improvements":
        return ".kimiflow/project/IMPROVEMENTS.md"
    if queue == "findings":
        return ".kimiflow/project/FINDINGS.md"
    raise ValueError("unknown queue: %s (use improvements|findings)" % queue)


def queue_section_re(queue):
    if queue == "improvements":
        return re.compile(r"^##\s+(Priorisierte Slices|Prioritized Slices)(\s.*)?$")
    if queue == "findings":
        return re.compile(r"^##\s+(Offen|Open)(\s.*)?$")
    raise ValueError("unknown queue: %s (use improvements|findings)" % queue)


def derive_id(heading):
    match = re.match(r"^([A-Za-z]+-[A-Za-z]+-[0-9]+|[A-Za-z]+-[0-9]+)", heading)
    if match:
        return match.group(1).lower()
    title = re.sub(r"^[0-9]+\.\s*", "", heading)
    title = re.sub(r"^[-*]\s*", "", title)
    ident = re.sub(r"[^a-z0-9]+", "-", title.lower())
    return ident.strip("-")


def list_slices(path, section_re):
    if not os.path.isfile(path):
        return []
    slices = []
    in_section = False
    have = False
    current_id = ""
    title = ""
    marked = False

    def emit():
        if have:
            slices.append((current_id, marked, title))

    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if section_re.match(line):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                emit()
                have = False
                in_section = False
                continue
            if in_section and line.startswith("### "):
                emit()
                title = re.sub(r"^###\s+", "", line)
                current_id = derive_id(title)
                marked = False
                have = True
                continue
            if in_section and have and MARKER_SUBSTR in line:
                marked = True
    if in_section:
        emit()
    return slices


def cmd_list(queue, root, fmt):
    rel = queue_file(queue)
    section_re = queue_section_re(queue)
    open_slices = [(ident, title) for ident, marked, title in list_slices(os.path.join(root, rel), section_re) if not marked]
    count = len(open_slices)
    if fmt == "json":
        if shutil.which("jq"):
            payload = {"queue": queue, "count": count, "open": [{"id": ident, "title": title} for ident, title in open_slices]}
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write('{"queue":"%s","count":%d,"open":[]}\n' % (queue, count))
        return 0
    if fmt == "pretty":
        if count == 0:
            sys.stdout.write("queue %s: keine offenen Slices.\n" % queue)
        else:
            sys.stdout.write("queue %s: %s offen\n" % (queue, count))
            for ident, title in open_slices:
                sys.stdout.write("  - %-40s %s\n" % (ident, title))
        return 0
    for ident, title in open_slices:
        sys.stdout.write("%s\t%s\n" % (ident, title))
    return 0


def resolve_id(want, candidates):
    if want in candidates:
        return want
    matches = [candidate for candidate in candidates if candidate.startswith(want)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.stderr.write("id not found: %s\n" % want)
        return None
    sys.stderr.write('ambiguous id prefix "%s" matches:\n' % want)
    for match in matches:
        sys.stderr.write("  - %s\n" % match)
    return None


def rewrite_block(path, section_re, target, action, new_marker):
    with open(path, "r", encoding="utf-8") as handle:
        lines = [line.rstrip("\n") for line in handle]

    out = []
    in_section = False
    in_target = False
    for line in lines:
        if section_re.match(line):
            in_section = True
            in_target = False
            out.append(line)
            continue
        if in_section and line.startswith("## "):
            in_section = False
            in_target = False
            out.append(line)
            continue
        if in_section and line.startswith("### "):
            title = re.sub(r"^###\s+", "", line)
            current = derive_id(title)
            out.append(line)
            in_target = current == target
            if in_target and action == "mark":
                out.append(new_marker)
            continue
        if in_section and in_target and MARKER_SUBSTR in line:
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def atomic_write(path, content):
    directory = os.path.dirname(path) or "."
    tmp = None
    try:
        import tempfile

        fd, tmp = tempfile.mkstemp(prefix=".iqs.tmp.", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except OSError:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def git_short_head(root):
    proc = subprocess.run(
        ["git", "-C", root, "rev-parse", "--short", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "NONE"


def sanitize_commit(value):
    cleaned = re.sub(r"[^0-9A-Za-z._-]", "", value)
    return cleaned or "NONE"


def cmd_change(action, id_arg, queue, root, commit, write):
    if not id_arg:
        usage()
        return die("%s needs an <id>" % action, 2)
    rel = queue_file(queue)
    section_re = queue_section_re(queue)
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return die("queue file not found: %s" % rel, 1)

    slices = list_slices(path, section_re)
    if action == "reopen":
        candidates = [ident for ident, marked, _title in slices if marked]
    else:
        candidates = [ident for ident, _marked, _title in slices]
    target = resolve_id(id_arg, candidates)
    if target is None:
        return 1

    new_marker = ""
    if action == "mark":
        if not commit:
            commit = git_short_head(root)
        commit = sanitize_commit(commit)
        new_marker = "<!-- %s id=%s commit=%s date=%s -->" % (MARKER_SUBSTR, target, commit, iso_date())

    new_content = rewrite_block(path, section_re, target, action, new_marker)
    if write:
        try:
            atomic_write(path, new_content)
        except OSError:
            return die("cannot install %s" % path, 1)
        if action == "mark":
            sys.stdout.write("marked done: %s (%s) in %s\n" % (target, queue, rel))
        else:
            sys.stdout.write("reopened: %s (%s) in %s\n" % (target, queue, rel))
    else:
        sys.stdout.write("DRY-RUN (%s %s in %s) — re-run with --write to persist.\n" % (action, target, queue))
    return 0


def parse_args(argv):
    cmd = argv[0] if argv else ""
    args = argv[1:] if argv else []
    queue = "improvements"
    root = ""
    commit = ""
    write = False
    fmt = "text"
    id_arg = ""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--queue":
            queue = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        elif arg == "--root":
            root = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        elif arg == "--commit":
            commit = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        elif arg == "--write":
            write = True
            i += 1
        elif arg == "--json":
            fmt = "json"
            i += 1
        elif arg == "--pretty":
            fmt = "pretty"
            i += 1
        elif arg in ("-h", "--help"):
            usage()
            raise SystemExit(0)
        elif arg.startswith("--"):
            raise ValueError("unknown flag: %s" % arg)
        else:
            if not id_arg:
                id_arg = arg
                i += 1
            else:
                raise ValueError("unexpected argument: %s" % arg)
    return cmd, queue, root, commit, write, fmt, id_arg


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        cmd, queue, root_arg, commit, write, fmt, id_arg = parse_args(argv)
        if queue not in ("improvements", "findings"):
            return die("unknown queue: %s (use improvements|findings)" % queue, 2)

        if cmd in ("", "-h", "--help"):
            usage()
            return 0
        if cmd not in ("list", "mark-done", "reopen"):
            usage()
            return die("unknown command: %s" % cmd, 2)

        mode = "observational" if cmd == "list" else "strict"
        try:
            root = paths.resolve_root(root_arg, mode=mode)
        except paths.RootResolutionError as exc:
            # R1 deliberate hardening: mutating commands no longer proceed from an
            # explicit invalid --root fallback path. See kimiflow-core spec §12.
            return die(str(exc), 2)

        if cmd == "list":
            return cmd_list(queue, root, fmt)
        action = "mark" if cmd == "mark-done" else "reopen"
        return cmd_change(action, id_arg, queue, root, commit, 1 if write else 0)
    except ValueError as exc:
        return die(str(exc), 2)


if __name__ == "__main__":
    raise SystemExit(main())
