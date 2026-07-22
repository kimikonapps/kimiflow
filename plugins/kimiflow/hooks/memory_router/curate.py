"""`curate` subcommand: assemble + (optionally) write MEMORY-INDEX.json and rebuild the
recall index. Behavioral port of the Bash cmd_curate (4030-4113) @ kimiflow--v0.1.50,
including its inline MEMORY-INDEX.json writer and the `cmd_index --write` call. The
index object is composed from status_json + the raw summary aggregators + a topics map."""
import contextlib
import io
import os
import re
import subprocess

from . import clock, contracts, index as index_mod, status as status_mod, store, summaries, text
from .cli import die, resolve_root, usage

_INDEX_PATH = ".kimiflow/project/MEMORY-INDEX.json"


def _jq_or(value, default):
    # jq `value // default`. Local copy (consolidation into a shared jq helper is overdue;
    # carry-forward).
    return default if value is None or value is False else value


def repo_id(root):
    # Bash repo_id (3545-3553): the origin remote, normalized to host/path (no scheme,
    # no .git), else "unknown".
    try:
        proc = subprocess.run(
            ["git", "-C", root, "config", "--get", "remote.origin.url"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        remote = proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else ""
    except OSError:
        remote = ""
    if not remote:
        return "unknown"
    remote = re.sub(r"^git@github\.com:", "github.com/", remote)
    remote = re.sub(r"^https://", "", remote)
    remote = re.sub(r"\.git$", "", remote)
    return remote


def _topic_key(row):
    topic = _jq_or(row.get("topic"), "uncategorized")
    return topic if isinstance(topic, str) else "uncategorized"


def _topics(learnings):
    # Bash topics jq (4064-4072): current rows grouped by `.topic // "uncategorized"`
    # (sorted), value = the list of `.id` (null when missing). Missing file -> {}.
    if not os.path.isfile(learnings):
        return {}
    current = [r for r in store.read_jsonl(learnings)
               if isinstance(r, dict) and _jq_or(r.get("status"), "current") == "current"]
    topics = {}
    for row in sorted(current, key=_topic_key):   # sort_by + group_by (stable, codepoint)
        topics.setdefault(_topic_key(row), []).append(row.get("id"))
    return topics


def curate_json(root, allow_network=True):
    project = root + "/.kimiflow/project"
    memory = project + "/MEMORY.md"
    learnings = project + "/LEARNINGS.jsonl"
    user_rows = project + "/USER.jsonl"
    usage_file = project + "/MEMORY-USAGE.json"
    economics_file = project + "/MEMORY-ECONOMICS.jsonl"

    status = status_mod.status_json(root, allow_network=allow_network)
    return {
        "schema_version": 1,
        "updated_at": clock.iso_now(),
        "repo_id": repo_id(root),
        "language": "de",
        "always_on_memory_tokens_estimate": text.word_count_file(memory),
        "vault": status["vault"],
        "provider": status["provider"],
        "learnings": summaries.read_jsonl_summary(learnings),
        "user_profile": summaries.read_jsonl_summary(user_rows),
        "usage": summaries.usage_summary_json(usage_file),
        "economics": summaries.economics_summary_json(economics_file),
        "lifecycle": summaries.learning_lifecycle_json(learnings, usage_file),
        "topics": _topics(learnings),
        "curation": status["curation"],
    }


def _write_derivatives(root, out):
    project = root + "/.kimiflow/project"
    os.makedirs(project, exist_ok=True)
    store.atomic_write(project + "/MEMORY-INDEX.json", contracts.dumps(out, pretty=True) + "\n")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            index_mod.run(["--root", root, "--write"])
        except Exception:
            pass


def refresh(root, allow_network=True):
    """Write local curated derivatives; callers may explicitly prohibit provider probes."""
    out = curate_json(root, allow_network=allow_network)
    _write_derivatives(root, out)
    return out


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
            return die("curate: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    out = curate_json(root)

    if write:
        # Keep the public command byte-compatible; the internal lifecycle caller uses
        # refresh(..., allow_network=False) to preserve its strict offline boundary.
        _write_derivatives(root, out)

    contracts.json_print(out, pretty)
    return 0
