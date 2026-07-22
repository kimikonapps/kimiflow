"""`history` subcommand: run-artifact recall. With a query -> ranked run-artifact hits;
without -> the most-recent run artifacts (query="recent"). On `--write` -> RUN-HISTORY.json
+ RUN-HISTORY.md + a MEMORY-USAGE.json update (event kind "history"). Behavioral port of
the Bash cmd_history (2021-2085) + write_history_markdown (1687-1703) at kimiflow--v0.1.50.
Reuses the recall query layer (terms_json_from_query / _sed_read) and run-artifact source."""
import os

from . import clock, contracts, recall, recall_index, store, usage_metrics
from .cli import die, resolve_root, usage

_MIDDOT = "\u00b7"  # U+00B7 MIDDLE DOT; never write the literal char (handoff gotcha).


def _jq_or(value, default):
    # jq `value // default`: substitute when null (None) or false; "" / 0 pass through.
    return default if value is None or value is False else value


def history_json(root, query, max_hits, write):
    # Bash cmd_history 2046-2078: query branch vs the "recent" (no-query) branch.
    if query:
        terms = recall.terms_json_from_query(query)
        hits = recall_index.run_artifact_hits_json(root, terms, max_hits)
    else:
        query = "recent"
        terms = []
        # Bash `run_artifact_rows_json | .[:max] | map(del(.text))`.
        hits = [{k: v for k, v in row.items() if k != "text"}
                for row in recall_index.run_artifact_rows_json(root)[:max_hits]]
    status = "written" if write else "preview"
    return {
        "schema_version": 1,
        "status": status,
        "query": query,
        "query_terms": terms,
        "path": ".kimiflow/project/RUN-HISTORY.json",
        "markdown_path": ".kimiflow/project/RUN-HISTORY.md",
        "written": write,   # Bash `written: ($written == 1)` -> the boolean
        "hits": hits,
    }


def write_history_markdown(path, obj):
    # Bash write_history_markdown (1687-1703): byte-faithful printf layout.
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    parts = [
        "# Run History Recall\n\n",
        "Generated: %s\n\n" % clock.iso_now(),
        "Query: %s\n\n" % obj["query"],
        "Hits: %s\n\n" % len(obj["hits"]),
        "## Hits\n\n",
    ]
    for hit in obj["hits"]:
        parts.append("- [%s %s %s] %s (%s)\n" % (
            _jq_or(hit.get("slug"), "run"), _MIDDOT, _jq_or(hit.get("artifact"), "artifact"),
            _jq_or(hit.get("summary"), ""), _jq_or(hit.get("path"), "")))
    store.atomic_write(path, "".join(parts))


def run(argv):
    root = ""
    query = ""
    query_file = ""
    pretty = False
    max_raw = "10"
    write = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--query":
            i += 1
            query = argv[i] if i < len(argv) else ""
        elif arg == "--query-file":
            i += 1
            query_file = argv[i] if i < len(argv) else ""
        elif arg == "--max":
            i += 1
            max_raw = argv[i] if i < len(argv) else ""
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("history: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    if query_file:
        if not os.path.isfile(query_file):
            return die("query file not found: %s" % query_file, 2)
        query = recall._sed_read(query_file, 120)
    # Bash `case "$max" in ''|*[!0-9]*)`: reject the empty string AND any non-ASCII-digit.
    if not (max_raw != "" and all("0" <= c <= "9" for c in max_raw)):
        return die("history --max must be a number", 2)
    max_hits = int(max_raw)

    obj = history_json(root, query, max_hits, write)

    if write:
        project = os.path.join(root, ".kimiflow", "project")
        os.makedirs(project, exist_ok=True)
        store.atomic_write(os.path.join(project, "RUN-HISTORY.json"),
                           contracts.dumps(obj, pretty=True) + "\n")
        write_history_markdown(os.path.join(project, "RUN-HISTORY.md"), obj)
        usage_metrics.update_usage_metrics(root, obj["hits"], "history")

    contracts.json_print(obj, pretty)
    return 0
