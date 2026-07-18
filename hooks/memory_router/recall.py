"""`recall` subcommand: assemble the recall-context object (budget-bounded MEMORY/USER
content + LEARNINGS/FACTS substring hits + RECALL.sqlite FTS hits + run-artifact hits)
with reason codes, and on `--write` emit RECALL.md + a sibling .json and update
MEMORY-USAGE.json. Behavioral port of the Bash cmd_recall (1826-2019) + its helpers
(terms_json_from_query / jsonl_hits / write_recall_markdown / recall_json_path_for /
write_recall_json) at kimiflow--v0.1.50. stdout is timestamp-free; only the written
files carry the iso_now nondeterminism."""
import os
import re

from . import clock, contracts, outcomes, recall_index, store, text, usage_metrics
from .cli import die, resolve_root, usage

# terms_json_from_query (Bash 1576-1580): split on runs of chars outside [:alnum:]_-
# (ASCII alnum in the C locale), keep length>=3, drop the stopword set, first-occurrence dedup.
_TERM_SPLIT = re.compile(r"[^a-z0-9_-]+")
_STOPWORDS = frozenset((
    "the", "and", "for", "mit", "und", "der", "die", "das", "ein", "eine", "ist",
    "sind", "was", "wie", "this", "that", "from", "into", "zur", "zum", "auf", "von",
))


def _jq_or(value, default):
    # jq `value // default`: substitute when null (None) or false; "" / 0 pass through.
    return default if value is None or value is False else value


def _int_env(name, default):
    # Bash `${VAR:-default}`: unset OR empty -> default; else the value. Non-integer is
    # unreachable (Bash `-le` also requires an integer).
    raw = os.environ.get(name, "")
    return int(raw) if raw else default


def _sed_read(path, count):
    # Bash `$(sed -n '1,Np' file)`: first `count` lines (sed splits on \n only, keeps \r);
    # command substitution strips trailing newlines. newline="" preserves \r like sed.
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            data = handle.read()
    except (OSError, UnicodeDecodeError):
        return ""
    return "\n".join(data.split("\n")[:count]).rstrip("\n")


def terms_json_from_query(query):
    lowered = text.ascii_lower(query)
    terms = []
    seen = set()
    for tok in _TERM_SPLIT.split(lowered):
        if len(tok) >= 3 and tok not in _STOPWORDS and tok not in seen:
            seen.add(tok)
            terms.append(tok)
            if len(terms) == 30:   # head -30
                break
    if not terms:
        return [text.ascii_lower(query)]
    return terms


def _join_elem(elem):
    # jq join(" ") element coercion: null -> "", string -> itself. A non-string/non-null
    # element makes jq error (unreachable for evidence, always string); str() is safer.
    if elem is None:
        return ""
    if isinstance(elem, str):
        return elem
    return _tostring_scalar(elem)


def _tostring_scalar(value):
    # jq `tostring` for scalars: string verbatim, bool -> "true"/"false". Numbers are
    # unreachable for the recall field sets (all strings); compact dumps is close enough.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return contracts.dumps(value)


def _field_text(row, fields):
    # jq field_text (1603-1612): join the named fields; arrays join(" "), objects/scalars
    # tostring, missing/null/false -> "".
    parts = []
    for field in fields.split(","):
        value = row.get(field)
        if value is None or value is False:
            value = ""
        if isinstance(value, list):
            parts.append(" ".join(_join_elem(e) for e in value))
        elif isinstance(value, dict):
            parts.append(contracts.dumps(value))   # jq tostring = compact JSON
        else:
            parts.append(_tostring_scalar(value))
    return " ".join(parts)


def _hit(blob, terms):
    # jq hit (1613-1615): ascii_downcase, then any non-empty term is a substring.
    lowered = text.ascii_lower(blob)
    return any(term != "" and term in lowered for term in terms)


def jsonl_hits(path, terms, max_hits, fields):
    # jq jsonl_hits (1591-1622): current-status rows whose field_text matches a term,
    # capped at max. Missing file -> []. Non-dict rows are skipped (jq would error on
    # `.status`; unreachable, safer - same class as the summaries non-object rows).
    if not os.path.isfile(path):
        return []
    out = [
        row for row in store.read_jsonl(path)
        if isinstance(row, dict)
        and _jq_or(row.get("status"), "current") == "current"
        and _hit(_field_text(row, fields), terms)
    ]
    return out[:max_hits]


def recall_json(root, query, max_hits, targeted=False, strategies=False, mode=""):
    project = os.path.join(root, ".kimiflow", "project")
    memory = os.path.join(project, "MEMORY.md")
    user_memory = os.path.join(project, "USER.md")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    facts = os.path.join(project, "FACTS.jsonl")
    budget = _int_env("KIMIFLOW_MEMORY_BUDGET", 900)
    user_budget = _int_env("KIMIFLOW_USER_MEMORY_BUDGET", 500)
    memory_tokens = 0 if targeted else text.word_count_file(memory)
    user_tokens = 0 if targeted else text.word_count_file(user_memory)
    terms = terms_json_from_query(query)
    omitted = []

    if targeted:
        memory_status = "omitted_targeted"
        memory_content = ""
        omitted.append("MEMORY.md omitted: targeted recall")
    elif os.path.isfile(memory):
        if memory_tokens <= budget:
            memory_status = "included"
            memory_content = _sed_read(memory, 160)
        else:
            memory_status = "omitted_over_budget"
            memory_content = ""
            omitted.append("MEMORY.md omitted: over budget")
    else:
        memory_status = "missing"
        memory_content = ""
        omitted.append("MEMORY.md missing")

    if targeted:
        user_status = "omitted_targeted"
        user_content = ""
        omitted.append("USER.md omitted: targeted recall")
    elif os.path.isfile(user_memory):
        if user_tokens <= user_budget:
            user_status = "included"
            user_content = _sed_read(user_memory, 120)
        else:
            user_status = "omitted_over_budget"
            user_content = ""
            omitted.append("USER.md omitted: over budget")
    else:
        user_status = "missing"
        user_content = ""
        omitted.append("USER.md missing")

    learning_hits = jsonl_hits(
        learnings, terms, max_hits, "id,kind,scope,topic,summary,status,sensitivity,evidence")
    if targeted:
        fact_hits = []
        index_hits = []
        history_hits = recall_index.run_artifact_hits_json(
            root, terms, max(0, max_hits - len(learning_hits)))
        index_status = "skipped_targeted"
        omitted.extend(("FACTS.jsonl omitted: targeted recall",
                        "RECALL.sqlite omitted: targeted recall"))
    else:
        fact_hits = jsonl_hits(facts, terms, max_hits, "kind,area,path,summary,confidence")
        index_hits = recall_index.fts_hits_json(root, terms, max_hits)
        history_hits = recall_index.run_artifact_hits_json(root, terms, max_hits)

        if len(index_hits) > 0:
            index_status = "used"
        elif os.path.isfile(os.path.join(project, "RECALL.sqlite")):
            index_status = "available_no_hits"
        elif recall_index.fts5_available():
            index_status = "missing"
        else:
            index_status = "unavailable"

    strategy_source = outcomes.strategy_recall_json(root, terms, mode=mode) if strategies else None
    strategy_n = strategy_source["count"] if strategy_source is not None else 0
    learn_n, fact_n, idx_n, hist_n = (
        len(learning_hits), len(fact_hits), len(index_hits), len(history_hits))
    total = learn_n + fact_n + idx_n + hist_n + strategy_n

    reason_codes = []
    if targeted:
        reason_codes.append("targeted_recall")
    if memory_status == "included":
        reason_codes.append("always_on_included")
    if memory_status == "omitted_over_budget":
        reason_codes.append("memory_over_budget")
    if memory_status == "missing":
        reason_codes.append("memory_missing")
    if user_status == "included":
        reason_codes.append("user_profile_included")
    if user_status == "omitted_over_budget":
        reason_codes.append("user_profile_over_budget")
    if learn_n > 0:
        reason_codes.append("local_recall_hits")
    if fact_n > 0:
        reason_codes.append("project_map_fact_hits")
    if idx_n > 0:
        reason_codes.append("fts_index_hits")
    if hist_n > 0:
        reason_codes.append("history_hits")
    if strategy_n > 0:
        reason_codes.append("strategy_outcome_hits")
    if total == 0:
        reason_codes.append("no_recall_hits")

    included_sources = []
    if memory_status == "included":
        included_sources.append("MEMORY.md")
    if user_status == "included":
        included_sources.append("USER.md")
    if learn_n > 0:
        included_sources.append("LEARNINGS.jsonl")
    if fact_n > 0:
        included_sources.append("FACTS.jsonl")
    if idx_n > 0:
        included_sources.append("RECALL.sqlite")
    if hist_n > 0:
        included_sources.append("RUN-HISTORY")
    if strategy_n > 0:
        included_sources.append("STRATEGY-OUTCOMES.jsonl")

    omitted_sources = []
    if memory_status == "omitted_targeted":
        omitted_sources.append({"source": "MEMORY.md", "reason": "targeted_recall"})
    if memory_status == "omitted_over_budget":
        omitted_sources.append({"source": "MEMORY.md", "reason": "over_budget"})
    if memory_status == "missing":
        omitted_sources.append({"source": "MEMORY.md", "reason": "missing"})
    if user_status == "omitted_targeted":
        omitted_sources.append({"source": "USER.md", "reason": "targeted_recall"})
    if user_status == "omitted_over_budget":
        omitted_sources.append({"source": "USER.md", "reason": "over_budget"})
    if user_status == "missing":
        omitted_sources.append({"source": "USER.md", "reason": "missing"})
    if targeted:
        omitted_sources.extend((
            {"source": "FACTS.jsonl", "reason": "targeted_recall"},
            {"source": "RECALL.sqlite", "reason": "targeted_recall"},
        ))

    sources = {
        "memory": {
            "path": ".kimiflow/project/MEMORY.md",
            "status": memory_status,
            "tokens_estimate": memory_tokens,
            "content": memory_content,
        },
        "user_profile": {
            "path": ".kimiflow/project/USER.md",
            "status": user_status,
            "tokens_estimate": user_tokens,
            "budget": user_budget,
            "content": user_content,
        },
        "learnings": {
            "path": ".kimiflow/project/LEARNINGS.jsonl",
            "count": learn_n,
            "hits": learning_hits,
        },
        "facts": {
            "path": ".kimiflow/project/FACTS.jsonl",
            "count": fact_n,
            "hits": fact_hits,
        },
        "index": {
            "path": ".kimiflow/project/RECALL.sqlite",
            "status": index_status,
            "count": idx_n,
            "hits": index_hits,
        },
        "history": {
            "path": ".kimiflow/project/RUN-HISTORY.json",
            "status": "used" if hist_n > 0 else "available_no_hits",
            "count": hist_n,
            "hits": history_hits,
        },
    }
    if strategy_source is not None:
        sources["strategies"] = strategy_source
    hit_counts = {
        "learnings": learn_n,
        "facts": fact_n,
        "index": idx_n,
        "history": hist_n,
    }
    if strategy_source is not None:
        hit_counts["strategies"] = strategy_n
    hit_counts["total"] = total
    return {
        "schema_version": 1,
        "query": query,
        "query_terms": terms,
        "token_budget": budget,
        "sources": sources,
        "explanation": {
            "reason_codes": reason_codes,
            "included_sources": included_sources,
            "omitted_sources": omitted_sources,
            "hit_counts": hit_counts,
        },
        "omitted": omitted,
    }


def write_recall_markdown(path, obj):
    # Bash write_recall_markdown (1772-1794): byte-faithful printf layout.
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    sources = obj["sources"]
    explanation = obj["explanation"]
    total = _jq_or(explanation.get("hit_counts", {}).get("total"), 0)
    parts = [
        "# Recall\n\n",
        "Generated: %s\n\n" % clock.iso_now(),
        "Query: %s\n\n" % obj["query"],
        "Terms: %s\n\n" % ", ".join(obj["query_terms"]),
        "Token budget: %s\n\n" % obj["token_budget"],
        "## Sources\n\n",
        "- MEMORY.md: %s\n" % sources["memory"]["status"],
        "- USER.md: %s\n" % sources["user_profile"]["status"],
        "- LEARNINGS.jsonl hits: %s\n" % sources["learnings"]["count"],
        "- FACTS.jsonl hits: %s\n" % sources["facts"]["count"],
        "- RECALL.sqlite: %s (%s hits)\n" % (sources["index"]["status"], sources["index"]["count"]),
        "- Run history hits: %s\n" % sources["history"]["count"],
        "\n## Explanation\n\n",
        "- Reason codes: %s\n" % ", ".join(_jq_or(explanation.get("reason_codes"), [])),
        "- Total hits: %s\n" % total,
        "\n## Omitted\n\n",
    ]
    for item in _jq_or(obj.get("omitted"), []):
        parts.append("- %s\n" % item)
    strategy_source = sources.get("strategies")
    if isinstance(strategy_source, dict):
        parts.append("\n## Strategy Outcomes\n\n")
        hits = strategy_source.get("hits", [])
        if not hits:
            parts.append("- none\n")
        else:
            for hit in hits:
                parts.append("- %s %s: %s\n" % (
                    hit.get("classification", ""),
                    hit.get("id", ""),
                    hit.get("strategy", ""),
                ))
    store.atomic_write(path, "".join(parts))


def recall_json_path_for(path):
    # Bash recall_json_path_for (1796-1802): strip a trailing .md, then add .json.
    if path.endswith(".md"):
        return path[:-3] + ".json"
    return path + ".json"


def write_recall_json(path, obj):
    # Bash write_recall_json (1804-1808): `jq . > path` (pretty 2-space + trailing newline).
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    store.atomic_write(path, contracts.dumps(obj, pretty=True) + "\n")


def run(argv):
    root = ""
    query = ""
    query_file = ""
    pretty = False
    max_raw = "5"
    write_path = ""
    targeted = False
    strategies = False
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
            i += 1
            write_path = argv[i] if i < len(argv) else ""
        elif arg == "--targeted":
            targeted = True
        elif arg == "--strategies":
            strategies = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("recall: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    if query_file:
        if not os.path.isfile(query_file):
            return die("query file not found: %s" % query_file, 2)
        query = _sed_read(query_file, 120)
    mode = outcomes.mode_for_artifact(os.path.abspath(query_file)) if query_file else ""
    if not query:
        return die("recall requires --query or --query-file", 2)
    # Bash `case "$max" in ''|*[!0-9]*)`: reject the empty string AND any non-ASCII-digit.
    if not (max_raw != "" and all("0" <= c <= "9" for c in max_raw)):
        return die("recall --max must be a number", 2)
    max_hits = int(max_raw)

    obj = recall_json(
        root,
        query,
        max_hits,
        targeted=targeted,
        strategies=strategies,
        mode=mode,
    )

    if write_path:
        if not write_path.startswith("/"):
            write_path = root + "/" + write_path
        write_recall_markdown(write_path, obj)
        write_recall_json(recall_json_path_for(write_path), obj)
        usage_hits = (obj["sources"]["learnings"]["hits"]
                      + obj["sources"]["index"]["hits"]
                      + obj["sources"]["history"]["hits"])
        if strategies:
            usage_hits += obj["sources"]["strategies"]["hits"]
        usage_metrics.update_usage_metrics(root, usage_hits, "recall")

    contracts.json_print(obj, pretty)
    return 0
