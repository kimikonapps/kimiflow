"""`recall` subcommand: assemble the recall-context object (budget-bounded MEMORY/USER
content + LEARNINGS/FACTS substring hits + RECALL.sqlite FTS hits + run-artifact hits)
with reason codes, and on `--write` emit RECALL.md + a sibling .json and update
MEMORY-USAGE.json. Behavioral port of the Bash cmd_recall (1826-2019) + its helpers
(terms_json_from_query / jsonl_hits / write_recall_markdown / recall_json_path_for /
write_recall_json) at kimiflow--v0.1.50. stdout is timestamp-free; only the written
files carry the iso_now nondeterminism."""
import heapq
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


def _bounded_read_status(path, count, max_chars):
    """Read a bounded prefix and report overflow separately from unreadable input."""
    if count <= 0 or max_chars < 0:
        return "", False, True
    data = ""
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            while True:
                chunk = handle.read(min(65536, max(1, max_chars - len(data) + 1)))
                if not chunk:
                    break
                data += chunk
                parts = data.split("\n")
                if len(parts) > count:
                    return "\n".join(parts[:count]).rstrip("\n"), False, True
                if len(data) > max_chars:
                    return data[:max_chars], True, True
    except (OSError, UnicodeDecodeError):
        return "", False, False
    return data.rstrip("\n"), False, True


def _bounded_read(path, count, max_chars):
    """Compatibility wrapper returning the bounded content and overflow flag."""
    content, overflow, _ = _bounded_read_status(path, count, max_chars)
    return content, overflow


def _sed_read(path, count):
    # Bash `$(sed -n '1,Np' file)`: first `count` lines (sed splits on \n only, keeps \r);
    # command substitution strips trailing newlines. newline="" preserves \r like sed.
    return _bounded_read(path, count, 1024 * 1024)[0]


def _word_count_file_streamed(path):
    """Return the exact legacy count with constant memory instead of one full-file read."""
    count = 0
    in_word = False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            while True:
                chunk = handle.read(4096)
                if not chunk:
                    return count
                for char in chunk:
                    if char.isspace():
                        in_word = False
                    elif not in_word:
                        count += 1
                        in_word = True
    except (OSError, UnicodeDecodeError):
        return 0


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
    return out if max_hits is None else out[:max_hits]


def _scalar_texts(value):
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _scalar_texts(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _scalar_texts(item)
    elif value is not None and value is not False:
        yield _tostring_scalar(value)


def _structural_tokens(value):
    if isinstance(value, dict):
        return max(1, sum(
            _structural_tokens(str(key)) + _structural_tokens(item)
            for key, item in value.items()
        ))
    if isinstance(value, list):
        return max(1, sum(_structural_tokens(item) for item in value))
    if value is None or value is False:
        return 1
    rendered = _tostring_scalar(value)
    return max(1, len(rendered.split()), (len(rendered) + 3) // 4)


def _estimated_tokens(value):
    try:
        structural = _structural_tokens(value)
        if isinstance(value, (dict, list)):
            serialized = contracts.dumps(value)
            return max(structural, (len(serialized) + 3) // 4)
        return structural
    except (RecursionError, TypeError, ValueError):
        # An unrepresentable/deep value must fail closed against every practical budget.
        return 10 ** 12


def hit_ref(hit):
    """Return the stable evidence identity shared by all recall sources."""
    if not isinstance(hit, dict):
        return ""
    if hit.get("ref") not in (None, False, ""):
        return str(hit["ref"])
    evidence = hit.get("evidence")
    if isinstance(evidence, list) and evidence and evidence[0] not in (None, False):
        return str(evidence[0])
    if hit.get("path") not in (None, False, ""):
        line = _jq_or(hit.get("line"), 1)
        return "%s:%s" % (hit["path"], line)
    if hit.get("id") not in (None, False, ""):
        return str(hit["id"])
    return ""


def _normalized_text(value):
    return " ".join(text.ascii_lower(str(value)).split())


def _normalized_summary(hit):
    value = hit.get("summary")
    if value in (None, False, ""):
        value = hit.get("strategy", "")
    return _normalized_text(value)


def _query_coverage(hit, terms):
    try:
        blob = text.ascii_lower(" ".join(_scalar_texts(hit)))
        return len({term for term in terms if term and term in blob})
    except (RecursionError, TypeError, ValueError):
        return 0


def _iter_jsonl_matches(path, terms, fields):
    for row in recall_index._iter_jsonl_objects(path):
        try:
            matches = (_jq_or(row.get("status"), "current") == "current"
                       and _hit(_field_text(row, fields), terms))
        except (RecursionError, TypeError, ValueError):
            continue
        if matches:
            yield row


def _ranked_jsonl_hits(path, terms, limit, fields):
    """Scan a JSONL source once while retaining only its best bounded window."""
    if limit <= 0:
        return []
    best = []
    sequence = 0
    for row in _iter_jsonl_matches(path, terms, fields):
        coverage = _query_coverage(row, terms)
        item = (coverage, -sequence, sequence, row)
        sequence += 1
        if len(best) < limit:
            heapq.heappush(best, item)
        elif item[:2] > best[0][:2]:
            heapq.heapreplace(best, item)
    best.sort(key=lambda item: (-item[0], item[2]))
    return [item[3] for item in best]


def _preferred_duplicates(path, terms, fields, existing, lower_hits, limit):
    """Recover bounded preferred direct representatives dropped by source windows."""
    seed_refs = {hit_ref(hit) for hit in lower_hits if hit_ref(hit)}
    seed_summaries = {_normalized_summary(hit) for hit in lower_hits
                      if _normalized_summary(hit)}
    reachable_refs = set(seed_refs)
    reachable_summaries = set(seed_summaries)
    selected_pairs = {
        (hit_ref(hit), _normalized_summary(hit)) for hit in existing
    }
    promoted = []

    def connect(row):
        ref = hit_ref(row)
        summary = _normalized_summary(row)
        if not ((ref and ref in reachable_refs)
                or (summary and summary in reachable_summaries)):
            return False
        if ref:
            reachable_refs.add(ref)
        if summary:
            reachable_summaries.add(summary)
        return True

    def expand_existing():
        while True:
            expanded = False
            for row in existing:
                before = (len(reachable_refs), len(reachable_summaries))
                if connect(row) and before != (len(reachable_refs), len(reachable_summaries)):
                    expanded = True
            if not expanded:
                return

    while len(promoted) < limit:
        changed = False
        expand_existing()
        for row in _iter_jsonl_matches(path, terms, fields):
            pair = (hit_ref(row), _normalized_summary(row))
            if pair in selected_pairs or not connect(row):
                continue
            selected_pairs.add(pair)
            promoted.append(row)
            changed = True
            if len(promoted) >= limit:
                break
        if not changed:
            break
    truncated = False
    if len(promoted) >= limit:
        expand_existing()
        for row in _iter_jsonl_matches(path, terms, fields):
            pair = (hit_ref(row), _normalized_summary(row))
            if pair in selected_pairs:
                continue
            if ((pair[0] and pair[0] in reachable_refs)
                    or (pair[1] and pair[1] in reachable_summaries)):
                truncated = True
                break
    return (
        existing + promoted,
        seed_refs if truncated else set(),
        seed_summaries if truncated else set(),
    )


def _content_identities(content):
    if not content:
        return ()
    values = [content, content[:420]]
    values.extend(line for line in content.splitlines() if line.strip())
    return tuple(identity for identity in (_normalized_text(value) for value in values)
                 if identity)


def _pack_hits(source_hits, terms, max_hits, token_limit, initial_refs=(),
               initial_summaries=()):
    """Rank, deduplicate and pack all source candidates under one hard envelope."""
    preference = ("facts", "learnings", "strategies", "history", "index")
    rank = {source: pos for pos, source in enumerate(preference)}
    candidates = []
    sequence = 0
    for source in preference:
        for hit in source_hits.get(source, []):
            candidates.append((source, hit, sequence))
            sequence += 1
    records = []
    for source, hit, sequence in candidates:
        ref = hit_ref(hit)
        summary = _normalized_summary(hit)
        records.append((source, hit, sequence, _query_coverage(hit, terms), ref, summary))

    parent = list(range(len(records)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    ref_owner = {}
    summary_owner = {}
    suppressed = set()
    initial_ref_set = set(initial_refs)
    initial_summary_set = set(initial_summaries)
    for index, (_, _, _, _, ref, summary) in enumerate(records):
        if (ref and ref in initial_ref_set) or (summary and summary in initial_summary_set):
            suppressed.add(index)
        if ref:
            if ref in ref_owner:
                union(index, ref_owner[ref])
            else:
                ref_owner[ref] = index
        if summary:
            if summary in summary_owner:
                union(index, summary_owner[summary])
            else:
                summary_owner[summary] = index

    groups = {}
    for index in range(len(records)):
        groups.setdefault(find(index), []).append(index)
    unique = []
    duplicates = 0
    for members in groups.values():
        if any(index in suppressed for index in members):
            duplicates += len(members)
            continue
        representative = min(
            members,
            key=lambda index: (rank[records[index][0]], records[index][2]),
        )
        source, hit, sequence, _, _, _ = records[representative]
        coverage = max(records[index][3] for index in members)
        unique.append([source, hit, sequence, coverage])
        duplicates += len(members) - 1
    unique.sort(key=lambda item: (
        -item[3], rank[item[0]], item[2]
    ))

    selected = {source: [] for source in preference}
    used = 0
    omitted = 0
    selected_count = 0
    for source, hit, _, _ in unique:
        cost = _estimated_tokens(hit)
        if selected_count >= max_hits or used + cost > token_limit:
            omitted += 1
            continue
        selected[source].append(hit)
        selected_count += 1
        used += cost
    return selected, used, duplicates, omitted


def recall_json(root, query, max_hits, targeted=False, strategies=False, mode="",
                refresh_index=False):
    project = os.path.join(root, ".kimiflow", "project")
    memory = os.path.join(project, "MEMORY.md")
    user_memory = os.path.join(project, "USER.md")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    facts = os.path.join(project, "FACTS.jsonl")
    recall_budget = max(0, _int_env("KIMIFLOW_RECALL_BUDGET", 1800))
    memory_budget = max(0, _int_env("KIMIFLOW_MEMORY_BUDGET", 900))
    user_budget = max(0, _int_env("KIMIFLOW_USER_MEMORY_BUDGET", 500))
    memory_tokens = 0 if targeted else _word_count_file_streamed(memory)
    user_tokens = 0 if targeted else _word_count_file_streamed(user_memory)
    terms = terms_json_from_query(query)
    omitted = []
    used_context = 0

    if targeted:
        memory_status = "omitted_targeted"
        memory_content = ""
        omitted.append("MEMORY.md omitted: targeted recall")
    elif os.path.isfile(memory):
        memory_cap = min(memory_budget, recall_budget)
        memory_content, memory_overflow, memory_readable = _bounded_read_status(
            memory, 160, memory_cap * 4)
        memory_cost = _estimated_tokens(memory_content)
        if not memory_readable:
            memory_status = "unreadable"
            memory_content = ""
            omitted.append("MEMORY.md omitted: unreadable")
        elif (not memory_overflow and memory_tokens <= memory_budget
                and memory_cost <= recall_budget):
            memory_status = "included"
            used_context += memory_cost
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
        user_cap = min(user_budget, max(0, recall_budget - used_context))
        user_content, user_overflow, user_readable = _bounded_read_status(
            user_memory, 120, user_cap * 4)
        user_cost = _estimated_tokens(user_content)
        if not user_readable:
            user_status = "unreadable"
            user_content = ""
            omitted.append("USER.md omitted: unreadable")
        elif (not user_overflow and user_tokens <= user_budget
                and used_context + user_cost <= recall_budget):
            user_status = "included"
            used_context += user_cost
        else:
            user_status = "omitted_over_budget"
            user_content = ""
            omitted.append("USER.md omitted: over budget")
    else:
        user_status = "missing"
        user_content = ""
        omitted.append("USER.md missing")

    candidate_limit = max(20, min(max_hits * 4, recall_budget))
    learning_hits = _ranked_jsonl_hits(
        learnings, terms, candidate_limit,
        "id,kind,scope,topic,summary,status,sensitivity,evidence")
    index_refresh = "not_requested"
    if targeted:
        fact_hits = []
        index_hits = []
        history_hits = recall_index.ranked_run_artifact_hits_json(
            root, terms, candidate_limit)
        index_status = "skipped_targeted"
        index_freshness = "not_applicable"
        omitted.extend(("FACTS.jsonl omitted: targeted recall",
                        "RECALL.sqlite omitted: targeted recall"))
    else:
        fact_hits = _ranked_jsonl_hits(
            facts, terms, candidate_limit,
            "kind,area,path,line,summary,confidence")
        history_hits = recall_index.ranked_run_artifact_hits_json(
            root, terms, candidate_limit)
        index_hits, state = recall_index.fts_hits_with_state(
            root, terms, candidate_limit)
        index_freshness = state["status"]
        if refresh_index and index_freshness in ("stale", "corrupt"):
            for _ in range(2):
                if recall_index.build_recall_index(
                        root, recall_index.recall_db_path(root)) != 0:
                    index_refresh = "failed"
                    break
                index_hits, state = recall_index.fts_hits_with_state(
                    root, terms, candidate_limit)
                index_freshness = state["status"]
                if index_freshness == "fresh":
                    index_refresh = "rebuilt"
                    break
            else:
                index_refresh = "failed"
        elif refresh_index and index_freshness == "fresh":
            index_refresh = "not_needed"
        if index_freshness != "fresh":
            index_hits = []
        if index_freshness == "fresh" and index_hits:
            index_status = "used"
        elif index_freshness == "fresh":
            index_status = "available_no_hits"
        elif index_freshness in ("stale", "corrupt"):
            index_status = index_freshness + "_bypassed"
        else:
            index_status = index_freshness

    strategy_source = outcomes.strategy_recall_json(root, terms, mode=mode) if strategies else None
    lower_than_learning = (
        (strategy_source["hits"] if strategy_source is not None else [])
        + history_hits + index_hits
    )
    learning_hits, unsafe_refs, unsafe_summaries = _preferred_duplicates(
        learnings, terms,
        "id,kind,scope,topic,summary,status,sensitivity,evidence",
        learning_hits, lower_than_learning, candidate_limit)
    if not targeted:
        fact_hits, fact_unsafe_refs, fact_unsafe_summaries = _preferred_duplicates(
            facts, terms, "kind,area,path,line,summary,confidence",
            fact_hits, learning_hits + lower_than_learning, candidate_limit)
        unsafe_refs.update(fact_unsafe_refs)
        unsafe_summaries.update(fact_unsafe_summaries)
    raw_hits = {
        "facts": fact_hits,
        "learnings": learning_hits,
        "strategies": strategy_source["hits"] if strategy_source is not None else [],
        "history": history_hits,
        "index": index_hits,
    }
    available = max(0, recall_budget - used_context)
    initial_refs = []
    if memory_status == "included":
        initial_refs.append(".kimiflow/project/MEMORY.md")
    if user_status == "included":
        initial_refs.append(".kimiflow/project/USER.md")
    initial_refs.extend(unsafe_refs)
    initial_summaries = []
    if memory_status == "included":
        initial_summaries.extend(_content_identities(memory_content))
    if user_status == "included":
        initial_summaries.extend(_content_identities(user_content))
    initial_summaries.extend(unsafe_summaries)
    if unsafe_refs or unsafe_summaries:
        omitted.append("Recall hits omitted: duplicate identity closure reached its candidate limit")
    selected, hit_tokens, duplicates, hits_omitted = _pack_hits(
        raw_hits, terms, max(0, max_hits), available, initial_refs=initial_refs,
        initial_summaries=initial_summaries)
    fact_hits = selected["facts"]
    learning_hits = selected["learnings"]
    history_hits = selected["history"]
    index_hits = selected["index"]
    if index_status == "used" and not index_hits:
        index_status = "available_no_hits"
    if strategy_source is not None:
        strategy_source = dict(strategy_source)
        strategy_source["hits"] = selected["strategies"]
        strategy_source["count"] = len(selected["strategies"])
        if strategy_source["count"] == 0 and strategy_source.get("status") == "used":
            strategy_source["status"] = "available_no_hits"

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
    if memory_status == "unreadable":
        reason_codes.append("memory_unreadable")
    if user_status == "included":
        reason_codes.append("user_profile_included")
    if user_status == "omitted_over_budget":
        reason_codes.append("user_profile_over_budget")
    if user_status == "unreadable":
        reason_codes.append("user_profile_unreadable")
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
    if unsafe_refs or unsafe_summaries:
        reason_codes.append("duplicate_closure_truncated")
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
    if memory_status == "unreadable":
        omitted_sources.append({"source": "MEMORY.md", "reason": "unreadable"})
    if user_status == "omitted_targeted":
        omitted_sources.append({"source": "USER.md", "reason": "targeted_recall"})
    if user_status == "omitted_over_budget":
        omitted_sources.append({"source": "USER.md", "reason": "over_budget"})
    if user_status == "missing":
        omitted_sources.append({"source": "USER.md", "reason": "missing"})
    if user_status == "unreadable":
        omitted_sources.append({"source": "USER.md", "reason": "unreadable"})
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
            "budget": memory_budget,
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
            "freshness": index_freshness,
            "refresh": index_refresh,
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
        "schema_version": 2,
        "query": query,
        "query_terms": terms,
        "token_budget": recall_budget,
        "budget": {
            "limit": recall_budget,
            "used": used_context + hit_tokens,
            "remaining": recall_budget - used_context - hit_tokens,
            "unit": "estimated_tokens",
            "global_hit_limit": max(0, max_hits),
            "duplicates_removed": duplicates,
            "hits_omitted": hits_omitted,
        },
        "authority": {
            "recall_status": "advisory",
            "rule": "current_project_sources_override_recall",
            "selection_order": ["facts", "learnings", "strategies", "history", "index"],
        },
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
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    sources = obj["sources"]
    explanation = obj["explanation"]
    budget = obj["budget"]
    total = _jq_or(explanation.get("hit_counts", {}).get("total"), 0)
    parts = [
        "# Recall\n\n",
        "Generated: %s\n\n" % clock.iso_now(),
        "Query: %s\n\n" % obj["query"],
        "Terms: %s\n\n" % ", ".join(obj["query_terms"]),
        "Context budget: %s/%s estimated tokens\n\n" % (budget["used"], budget["limit"]),
        "Recall authority: advisory; current code, tests, and specifications override it.\n\n",
        "## Sources\n\n",
        "- MEMORY.md: %s\n" % sources["memory"]["status"],
        "- USER.md: %s\n" % sources["user_profile"]["status"],
        "- LEARNINGS.jsonl hits: %s\n" % sources["learnings"]["count"],
        "- FACTS.jsonl hits: %s\n" % sources["facts"]["count"],
        "- RECALL.sqlite: %s, freshness=%s, refresh=%s (%s hits)\n" % (
            sources["index"]["status"], sources["index"]["freshness"],
            sources["index"]["refresh"], sources["index"]["count"]),
        "- Run history hits: %s\n" % sources["history"]["count"],
        "\n## Explanation\n\n",
        "- Reason codes: %s\n" % ", ".join(_jq_or(explanation.get("reason_codes"), [])),
        "- Total hits: %s\n" % total,
        "- Global hit limit: %s\n" % budget["global_hit_limit"],
        "- Duplicates removed: %s\n" % budget["duplicates_removed"],
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
        refresh_index=bool(write_path),
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
