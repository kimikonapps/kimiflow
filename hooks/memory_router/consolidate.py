"""`consolidate` subcommand: archive superseded learnings, drop them from LEARNINGS.jsonl,
refresh bounded memory + curate + index, and report current/superseded counts + duplicate
groups. Behavioral port of the Bash cmd_consolidate (3931-3986) at kimiflow--v0.1.50. All
dependencies were already ported (store/memory_md/curate/index)."""
import contextlib
import io
import itertools
import os

from . import contracts, curate, index as index_mod, memory_md, store
from .cli import die, resolve_root, usage


def _jq_or(value, default):
    return default if value is None or value is False else value


def _dup_key(row):
    # Bash sort_by/group_by key: (kind//"")+"|"+(scope//"")+"|"+(topic//"")+"|"+(summary//"").
    return "%s|%s|%s|%s" % (_jq_or(row.get("kind"), ""), _jq_or(row.get("scope"), ""),
                            _jq_or(row.get("topic"), ""), _jq_or(row.get("summary"), ""))


def consolidate_json(rows):
    # Non-dict rows are skipped (jq would error on `.status`; unreachable, safer).
    superseded = [r for r in rows if isinstance(r, dict) and _jq_or(r.get("status"), "") == "superseded"]
    current = [r for r in rows if isinstance(r, dict) and _jq_or(r.get("status"), "current") == "current"]
    ordered = sorted(current, key=_dup_key)   # jq sort_by (stable) then group_by
    duplicates = []
    for _, group in itertools.groupby(ordered, key=_dup_key):
        members = list(group)
        if len(members) > 1:
            duplicates.append({"summary": _jq_or(members[0].get("summary"), ""),
                               "ids": [r.get("id") for r in members]})
    return superseded, current, duplicates


def run(argv):
    root = ""
    pretty = False
    write = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("consolidate: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    project = os.path.join(root, ".kimiflow", "project")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    archive = os.path.join(project, "LEARNINGS.archive.jsonl")
    rows = store.read_jsonl(learnings)
    superseded, current, duplicates = consolidate_json(rows)

    if write and os.path.isfile(learnings):
        os.makedirs(project, exist_ok=True)
        if superseded:
            # Bash `... | jq -c '.[]' >> "$archive"` (append, follows a symlink).
            store.append_line(archive, "".join(contracts.dumps(r) + "\n" for r in superseded))
        # Bash mktemp + mv: rewrite LEARNINGS.jsonl to the non-superseded rows (drops the
        # superseded ones, and -- like the writer -- any malformed/blank lines).
        kept = [r for r in rows if not (isinstance(r, dict) and _jq_or(r.get("status"), "") == "superseded")]
        store.atomic_write(learnings, "".join(contracts.dumps(r) + "\n" for r in kept), refuse_symlink=False)
        memory_md.write_bounded_memory(root)
        memory_md.write_bounded_user_memory(root)
        with contextlib.redirect_stdout(io.StringIO()):
            curate.run(["--root", root, "--write"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                index_mod.run(["--root", root, "--write"])
            except Exception:
                pass

    out = {
        "schema_version": 1,
        "status": "consolidated" if write else "preview",
        "written": write,
        "archive_path": ".kimiflow/project/LEARNINGS.archive.jsonl",
        "current_count": len(current),
        "archived_superseded_count": len(superseded),
        "duplicate_groups": duplicates,
    }
    contracts.json_print(out, pretty)
    return 0
