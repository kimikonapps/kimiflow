"""`recall` subcommand: assemble the recall-context object (budget-bounded MEMORY/USER
content + LEARNINGS/FACTS substring hits + RECALL.sqlite FTS hits + run-artifact hits)
with reason codes, and on `--write` emit RECALL.md + a sibling .json and update
MEMORY-USAGE.json. Behavioral port of the Bash cmd_recall (1826-2019) + its helpers
(terms_json_from_query / jsonl_hits / write_recall_markdown / recall_json_path_for /
write_recall_json) at kimiflow--v0.1.50. stdout is timestamp-free; only the written
files carry the iso_now nondeterminism."""
import hashlib
import heapq
import json
import os
import re
import stat

from . import (attribution, clock, contracts, outcomes, recall_index, store, text,
               usage_metrics, workspace_scope)
from .cli import die, resolve_root, usage

# terms_json_from_query (Bash 1576-1580): split on runs of chars outside [:alnum:]_-
# (ASCII alnum in the C locale), keep length>=3, drop the stopword set, first-occurrence dedup.
_TERM_SPLIT = re.compile(r"[^a-z0-9_-]+")
_STOPWORDS = frozenset((
    "the", "and", "for", "mit", "und", "der", "die", "das", "ein", "eine", "ist",
    "sind", "was", "wie", "this", "that", "from", "into", "zur", "zum", "auf", "von",
))
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|\Z)")
_WORKFLOW_METADATA_RE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?(?:"
    r"flow schema[ \t]*:[ \t]*[0-9].*|"
    r"intent contract[ \t]*:[ \t]*[0-9].*|"
    r"status[ \t]*:[ \t]*(?:active|done|failed|aborted|parked|backlog|open|closed)(?:[ \t].*)?|"
    r"mode[ \t]*:[ \t]*(?:feature|fix|audit|review)(?:[ \t].*)?|"
    r"scope[ \t]*:[ \t]*(?:trivial|quick|small|large|critical)(?:[ \t].*)?|"
    r"phase [0-7][ \t]*:[ \t]*(?:open|in-progress|done|blocked)(?:[ \t].*)?|"
    r"recovery[ \t]*:[ \t]*(?:clean|active|superseded)(?:[ \t].*)?|"
    r"affected (?:files|paths)[ \t]*:.*"
    r")(?:\*\*)?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


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
    cleaned = _HTML_COMMENT_RE.sub(" ", query)
    cleaned = _FRONTMATTER_RE.sub(" ", cleaned)
    cleaned = _WORKFLOW_METADATA_RE.sub(" ", cleaned)
    lowered = text.ascii_lower(cleaned)
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


def _iter_jsonl_matches(path, terms, fields, accept=None):
    for row in recall_index._iter_jsonl_objects(path):
        try:
            matches = (_jq_or(row.get("status"), "current") == "current"
                       and _hit(_field_text(row, fields), terms))
        except (RecursionError, TypeError, ValueError):
            continue
        if matches and (accept is None or accept(row)):
            yield row


def _iter_jsonl_objects_with_receipt(path, completion):
    """Stream one stable regular JSONL object through a no-follow descriptor."""
    completion["complete"] = False
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        return
    try:
        descriptor = os.open(
            path, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0)
        )
    except OSError:
        return
    try:
        handle = os.fdopen(descriptor, "rb")
    except OSError:
        os.close(descriptor)
        return
    try:
        with handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                return
            while True:
                raw = handle.readline(recall_index.JSONL_ROW_BYTE_LIMIT + 1)
                if not raw:
                    break
                oversized = len(raw) > recall_index.JSONL_ROW_BYTE_LIMIT
                while oversized and raw and not raw.endswith(b"\n"):
                    raw = handle.readline(recall_index.JSONL_ROW_BYTE_LIMIT + 1)
                if oversized:
                    continue
                try:
                    row = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, RecursionError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    yield row
            after = os.fstat(handle.fileno())
            completion["complete"] = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) == (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
    except OSError:
        return


def _ranked_jsonl_hits(path, terms, limit, fields, accept=None, priority=None):
    """Scan a JSONL source once while retaining only its best bounded window."""
    if limit <= 0:
        return []
    best = []
    sequence = 0
    for row in _iter_jsonl_matches(path, terms, fields, accept=accept):
        coverage = _query_coverage(row, terms)
        local_priority = priority(row) if priority is not None else 0
        item = (local_priority, coverage, -sequence, sequence, row)
        sequence += 1
        if len(best) < limit:
            heapq.heappush(best, item)
        elif item[:3] > best[0][:3]:
            heapq.heapreplace(best, item)
    best.sort(key=lambda item: (-item[0], -item[1], item[3]))
    return [item[4] for item in best]


def _preferred_duplicates(path, terms, fields, existing, lower_hits, limit,
                          accept=None, seed_match=None, scan_receipt=None,
                          safe_lower_hits=(), accept_factory=None,
                          safe_match_key=None):
    """Recover bounded preferred direct representatives dropped by source windows."""
    seed_refs = {hit_ref(hit) for hit in lower_hits if hit_ref(hit)}
    seed_summaries = {_normalized_summary(hit) for hit in lower_hits
                      if _normalized_summary(hit)}

    def identity_token(domain, value):
        if not value:
            return None
        digest = hashlib.sha256()
        digest.update(domain)
        digest.update(b"\0")
        digest.update(str(value).encode("utf-8", "surrogatepass"))
        return digest.digest()

    def ref_token(hit):
        return identity_token(b"ref", hit_ref(hit))

    def summary_token(hit):
        return identity_token(b"summary", _normalized_summary(hit))

    ref_owners = {}
    summary_owners = {}
    for hit in existing + lower_hits:
        ref = ref_token(hit)
        summary = summary_token(hit)
        if ref:
            ref_owners[ref] = ref_owners.get(ref, 0) + 1
        if summary:
            summary_owners[summary] = summary_owners.get(summary, 0) + 1
    initial_safe_hit_ids = {
        id(hit) for hit in safe_lower_hits
        if (ref_token(hit) is None or ref_owners.get(ref_token(hit)) == 1)
        and (
            summary_token(hit) is None
            or summary_owners.get(summary_token(hit)) == 1
        )
    }
    safe_identities = {
        id(hit): (ref_token(hit), summary_token(hit))
        for hit in safe_lower_hits if id(hit) in initial_safe_hit_ids
    }
    safe_match_keys = {
        id(hit): (
            safe_match_key(hit) if safe_match_key is not None
            else (hit_ref(hit), _normalized_summary(hit))
        )
        for hit in safe_lower_hits if id(hit) in initial_safe_hit_ids
    }
    safe_by_ref = {}
    safe_by_summary = {}
    safe_by_match_key = {}
    for hit_id, (ref, summary) in safe_identities.items():
        if ref:
            safe_by_ref.setdefault(ref, set()).add(hit_id)
        if summary:
            safe_by_summary.setdefault(summary, set()).add(hit_id)
        match_key = safe_match_keys.get(hit_id)
        if match_key is not None:
            safe_by_match_key.setdefault(match_key, set()).add(hit_id)
    reachable_refs = set(seed_refs)
    reachable_summaries = set(seed_summaries)
    selected_pairs = {
        (hit_ref(hit), _normalized_summary(hit)) for hit in existing
    }
    promoted = []
    truncated = False
    final_safe_hit_ids = set(initial_safe_hit_ids)

    def new_safe_state():
        return {
            "hit_ids": set(initial_safe_hit_ids),
            "by_ref": {key: set(value) for key, value in safe_by_ref.items()},
            "by_summary": {
                key: set(value) for key, value in safe_by_summary.items()
            },
            "by_match_key": {
                key: set(value) for key, value in safe_by_match_key.items()
            },
            "full_pairs": {},
        }

    def revoke_bridged_safe_hits(row, state):
        pair = (hit_ref(row), _normalized_summary(row))
        identity_pair = (
            identity_token(b"ref", pair[0]),
            identity_token(b"summary", pair[1]),
        )
        match_key = safe_match_key(row) if safe_match_key is not None else pair
        exact = set(state["by_match_key"].get(match_key, ()))
        touched = set(state["by_ref"].get(identity_pair[0], ()))
        touched.update(state["by_summary"].get(identity_pair[1], ()))
        for hit_id in touched:
            if hit_id not in exact:
                state["hit_ids"].discard(hit_id)
        for hit_id in exact & state["hit_ids"]:
            previous = state["full_pairs"].get(hit_id)
            if previous is not None and previous != identity_pair:
                state["hit_ids"].discard(hit_id)
                continue
            state["full_pairs"][hit_id] = identity_pair
            if identity_pair[0]:
                state["by_ref"].setdefault(identity_pair[0], set()).add(hit_id)
            if identity_pair[1]:
                state["by_summary"].setdefault(identity_pair[1], set()).add(hit_id)

    def rows_for_closure():
        nonlocal final_safe_hit_ids
        if seed_match is None:
            yield from _iter_jsonl_matches(
                path, terms, fields, accept=accept
            )
            return
        completion = {}
        observations = {}
        safe_state = new_safe_state()
        final_safe_hit_ids = set()
        scan_accept = (
            accept_factory(observations)
            if accept_factory is not None else accept
        )
        if scan_receipt is not None:
            scan_receipt.clear()
        for row in _iter_jsonl_objects_with_receipt(path, completion):
            try:
                matches = (
                    _jq_or(row.get("status"), "current") == "current"
                    and (_hit(_field_text(row, fields), terms) or seed_match(row))
                )
                accepted = matches and (
                    scan_accept is None or scan_accept(row)
                )
                if accepted:
                    revoke_bridged_safe_hits(row, safe_state)
                if accepted:
                    yield row
            except (RecursionError, TypeError, ValueError):
                continue
        if scan_receipt is not None and completion.get("complete"):
            scan_receipt["complete"] = True
            scan_receipt["classes"] = observations
        final_safe_hit_ids = set(safe_state["hit_ids"])

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
        for row in rows_for_closure():
            pair = (hit_ref(row), _normalized_summary(row))
            if pair in selected_pairs:
                continue
            if len(promoted) >= limit:
                if ((pair[0] and pair[0] in reachable_refs)
                        or (pair[1] and pair[1] in reachable_summaries)):
                    truncated = True
                continue
            if not connect(row):
                continue
            selected_pairs.add(pair)
            promoted.append(row)
            changed = True
            if len(promoted) >= limit and seed_match is None:
                break
        if not changed:
            break
    if len(promoted) >= limit and not truncated:
        expand_existing()
        for row in rows_for_closure():
            pair = (hit_ref(row), _normalized_summary(row))
            if pair in selected_pairs:
                continue
            if ((pair[0] and pair[0] in reachable_refs)
                    or (pair[1] and pair[1] in reachable_summaries)):
                truncated = True
    return (
        existing + promoted,
        {
            hit_ref(hit) for hit in lower_hits
            if truncated and id(hit) not in final_safe_hit_ids and hit_ref(hit)
        },
        {
            _normalized_summary(hit) for hit in lower_hits
            if (truncated and id(hit) not in final_safe_hit_ids
                and _normalized_summary(hit))
        },
    )


def _content_identities(content):
    if not content:
        return ()
    values = [content, content[:420]]
    values.extend(line for line in content.splitlines() if line.strip())
    return tuple(identity for identity in (_normalized_text(value) for value in values)
                 if identity)


def _pack_hits(source_hits, terms, max_hits, token_limit, initial_refs=(),
               initial_summaries=(), locality=None):
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
        local_rank = locality(source, hit) if locality is not None else 0
        records.append((source, hit, sequence, _query_coverage(hit, terms), ref,
                        summary, local_rank))

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
    for index, (_, _, _, _, ref, summary, _) in enumerate(records):
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
            key=lambda index: (
                -records[index][6] if locality is not None else 0,
                rank[records[index][0]], records[index][2],
            ),
        )
        source, hit, sequence, _, _, _, _ = records[representative]
        coverage = max(records[index][3] for index in members)
        local_rank = max(records[index][6] for index in members)
        unique.append([source, hit, sequence, coverage, local_rank])
        duplicates += len(members) - 1
    if locality is None:
        unique.sort(key=lambda item: (-item[3], rank[item[0]], item[2]))
    else:
        unique.sort(key=lambda item: (-item[4], -item[3], rank[item[0]], item[2]))

    selected = {source: [] for source in preference}
    used = 0
    omitted = 0
    selected_count = 0
    for source, hit, _, _, _ in unique:
        cost = _estimated_tokens(hit)
        if selected_count >= max_hits or used + cost > token_limit:
            omitted += 1
            continue
        selected[source].append(hit)
        selected_count += 1
        used += cost
    return selected, used, duplicates, omitted


def _with_scope_metadata(obj, metadata):
    """Insert additive scope metadata without disturbing the legacy key order."""
    result = {}
    for key, value in obj.items():
        result[key] = value
        if key == "attribution":
            result["workspace_scope"] = metadata
    return result


def recall_json(root, query, max_hits, targeted=False, strategies=False, mode="",
                refresh_index=False, scope_paths=None, scope_source="explicit",
                scope_state_receipt=None, _allow_scope_retry=True):
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
    scope_selection = None
    foreign_identities = set()
    foreign_identities_truncated = False
    index_shadow_keys = set()
    foreign_shadow_keys = set()
    if scope_paths is not None:
        scope_selection = workspace_scope.resolve_scope(
            root, scope_paths, source=scope_source,
            state_receipt=scope_state_receipt)
    scope_active = (
        scope_selection is not None and scope_selection.get("status") == "active"
    )

    def index_shadow_summary(hit):
        value = hit.get("summary")
        if value in (None, False, ""):
            value = hit.get("strategy", "")
        return _normalized_text(str(value)[:420])

    def index_shadow_key(source, hit):
        if source == "learnings":
            values = ("learning", hit_ref(hit), index_shadow_summary(hit))
        elif source == "facts":
            values = ("fact", hit_ref(hit), index_shadow_summary(hit))
        elif source == "index" and hit.get("kind") in ("learning", "fact"):
            values = (
                hit.get("kind"), str(hit.get("ref", "")),
                index_shadow_summary(hit),
            )
        else:
            return None
        serialized = contracts.dumps(list(values)).encode("utf-8", "surrogatepass")
        return hashlib.sha256(serialized).digest()

    def scoped_accept(source, shadow_observations=None):
        if not scope_active:
            return None

        def accept(hit):
            nonlocal foreign_identities_truncated
            if source == "index":
                return index_shadow_key(source, hit) not in foreign_shadow_keys
            else:
                classification = workspace_scope.classify_hit(
                    root, scope_selection, source, hit)
                key = index_shadow_key(source, hit)
                if key in index_shadow_keys and shadow_observations is not None:
                    shadow_observations.setdefault(key, set()).add(
                        classification
                    )
            if classification == "foreign":
                identity = workspace_scope.hit_identity(source, hit)
                if identity in foreign_identities:
                    return False
                if len(foreign_identities) < workspace_scope.MAX_FOREIGN_IDENTITIES:
                    foreign_identities.add(identity)
                else:
                    foreign_identities_truncated = True
                return False
            return True

        return accept

    def locality(source, hit):
        if source == "index":
            classification = "shared"
        else:
            classification = workspace_scope.classify_hit(
                root, scope_selection, source, hit)
        return 1 if classification == "local" else 0

    def scoped_priority(source):
        if not scope_active:
            return None
        return lambda hit: locality(source, hit)

    def scoped_seed_match(source):
        if not scope_active or not index_shadow_keys:
            return None
        return lambda hit: index_shadow_key(source, hit) in index_shadow_keys

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
    index_refresh = "not_requested"
    if targeted:
        index_hits = []
        index_status = "skipped_targeted"
        index_freshness = "not_applicable"
    else:
        index_candidate_limit = (
            min(
                recall_budget,
                workspace_scope.MAX_SCOPED_INDEX_CANDIDATES,
                max(candidate_limit, candidate_limit * 4),
            )
            if scope_active else candidate_limit
        )
        index_hits, state = recall_index.fts_hits_with_state(
            root, terms, index_candidate_limit)
        index_freshness = state["status"]
        if refresh_index and index_freshness in ("stale", "corrupt"):
            for _ in range(2):
                if recall_index.build_recall_index(
                        root, recall_index.recall_db_path(root)) != 0:
                    index_refresh = "failed"
                    break
                index_hits, state = recall_index.fts_hits_with_state(
                    root, terms, index_candidate_limit)
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
        elif scope_active:
            index_shadow_keys.update(
                key for key in (
                    index_shadow_key("index", hit) for hit in index_hits
                ) if key is not None
            )

    index_seed_hits = list(index_hits)
    index_seed_ids = {id(hit) for hit in index_seed_hits}

    def scoped_shadow_match_key(source):
        if not scope_active:
            return None

        def match_key(hit):
            if id(hit) in index_seed_ids:
                return index_shadow_key("index", hit)
            return index_shadow_key(source, hit)

        return match_key

    learning_hits = _ranked_jsonl_hits(
        learnings, terms, candidate_limit,
        "id,kind,scope,topic,summary,status,sensitivity,evidence",
        accept=scoped_accept("learnings"),
        priority=scoped_priority("learnings"))
    if targeted:
        fact_hits = []
        history_hits = recall_index.ranked_run_artifact_hits_json(
            root, terms, candidate_limit)
        omitted.extend(("FACTS.jsonl omitted: targeted recall",
                        "RECALL.sqlite omitted: targeted recall"))
    else:
        fact_hits = _ranked_jsonl_hits(
            facts, terms, candidate_limit,
            "kind,area,path,line,summary,confidence",
            accept=scoped_accept("facts"), priority=scoped_priority("facts"))
        history_hits = recall_index.ranked_run_artifact_hits_json(
            root, terms, candidate_limit)
        if index_freshness == "fresh" and not scope_active:
            index_hits = index_hits[:candidate_limit]
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
        + history_hits + index_seed_hits
    )
    learning_scan_receipt = {}
    learning_hits, unsafe_refs, unsafe_summaries = _preferred_duplicates(
        learnings, terms,
        "id,kind,scope,topic,summary,status,sensitivity,evidence",
        learning_hits, lower_than_learning, candidate_limit,
        accept=scoped_accept("learnings"),
        seed_match=scoped_seed_match("learnings"),
        scan_receipt=learning_scan_receipt,
        safe_lower_hits=index_seed_hits if scope_active else (),
        accept_factory=(
            lambda observations: scoped_accept("learnings", observations)
            if scope_active else None
        ),
        safe_match_key=scoped_shadow_match_key("learnings"))
    if not targeted:
        fact_scan_receipt = {}
        fact_hits, fact_unsafe_refs, fact_unsafe_summaries = _preferred_duplicates(
            facts, terms, "kind,area,path,line,summary,confidence",
            fact_hits, learning_hits + lower_than_learning, candidate_limit,
            accept=scoped_accept("facts"),
            seed_match=scoped_seed_match("facts"),
            scan_receipt=fact_scan_receipt,
            safe_lower_hits=index_seed_hits if scope_active else (),
            accept_factory=(
                lambda observations: scoped_accept("facts", observations)
                if scope_active else None
            ),
            safe_match_key=scoped_shadow_match_key("facts"))
        unsafe_refs.update(fact_unsafe_refs)
        unsafe_summaries.update(fact_unsafe_summaries)
        if scope_active:
            for source, receipt in (
                ("learnings", learning_scan_receipt),
                ("facts", fact_scan_receipt),
            ):
                if receipt.get("complete"):
                    foreign_shadow_keys.update(
                        key for key, classes in receipt.get("classes", {}).items()
                        if classes == {"foreign"}
                    )
            index_accept = scoped_accept("index")
            index_hits = [
                hit for hit in index_seed_hits if index_accept(hit)
            ][:candidate_limit]
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
        initial_summaries=initial_summaries,
        locality=locality if scope_active else None)
    if scope_active and not workspace_scope.revalidate_scope(root, scope_selection):
        if _allow_scope_retry:
            unscoped = recall_json(
                root, query, max_hits, targeted=targeted, strategies=strategies,
                mode=mode, refresh_index=False, scope_paths=None,
                _allow_scope_retry=False)
            fallback = workspace_scope.fallback_scope(
                root, scope_selection.get("_paths", ()),
                scope_selection.get("_overflow_reason") or "scope_changed_during_recall",
                scope_source)
            return _with_scope_metadata(unscoped, workspace_scope.scope_json(fallback))
        scope_selection = workspace_scope.fallback_scope(
            root, scope_selection.get("_paths", ()),
            "scope_changed_during_recall", scope_source)
        scope_active = False
    selected = attribution.attach_ids(selected)
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
    result = {
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
        "attribution": {
            "contract": 1,
            "id_algorithm": "sha256-source-reference-content",
            "hit_count": total,
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
    if scope_selection is not None:
        scope_selection["_foreign_hits_omitted"] = len(foreign_identities)
        scope_selection["_foreign_hits_truncated"] = foreign_identities_truncated
        result = _with_scope_metadata(
            result, workspace_scope.scope_json(scope_selection)
        )
    return result


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
    ]
    if total:
        parts.append("\n## Recall attribution\n\n")
        for source in attribution.SOURCE_ORDER:
            section = sources.get(source)
            hits = section.get("hits", []) if isinstance(section, dict) else []
            for hit in hits:
                parts.append("- %s [%s] %s\n" % (
                    hit["recall_id"], source, attribution.hit_reference(hit),
                ))
    parts.append("\n## Omitted\n\n")
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
    scope = obj.get("workspace_scope")
    if isinstance(scope, dict):
        parts.append("\n## Workspace Scope\n\n")
        parts.append("- Status: %s (%s)\n" % (
            scope.get("status", "fallback"), scope.get("reason", "unknown")))
        parts.append("- Source: %s\n" % scope.get("source", "unknown"))
        units = scope.get("units", [])
        parts.append("- Units: %s\n" % (
            ", ".join(unit.get("path", "") for unit in units) if units else "project-wide"))
        parts.append("- Foreign hits omitted: %s\n" %
                     scope.get("foreign_hits_omitted", 0))
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
    scope_paths = []
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
        elif arg == "--scope-path":
            i += 1
            scope_paths.append(argv[i] if i < len(argv) else "")
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
    scope_source = "explicit"
    scope_state_receipt = None
    requested_scope_paths = scope_paths if scope_paths else None
    if query_file and not scope_paths:
        inferred_paths, inference_reason, scope_state_receipt = (
            workspace_scope.scope_paths_for_query_file(
                query_file, root=root, include_receipt=True)
        )
        if inferred_paths is not None:
            requested_scope_paths = inferred_paths
            scope_source = inference_reason
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
        scope_paths=requested_scope_paths,
        scope_source=scope_source,
        scope_state_receipt=scope_state_receipt,
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
