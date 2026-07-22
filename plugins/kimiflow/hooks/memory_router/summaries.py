"""JSONL summary aggregators (status/type counters). Behavioral ports of the Bash
read_jsonl_summary / proposal_summary_json / usage_summary_json / economics_summary_json
at kimiflow--v0.1.50 (135-171, 79-110, 184-241, 243-364). Each reads a JSONL/JSON file
(malformed lines skipped, matching jq `fromjson? // empty`) and returns a fixed-shape
summary dict; serialization stays at the contracts.dumps boundary in the calling
subcommand."""
import math
import os
from datetime import date

from . import clock, global_metrics, store

_PROPOSALS_PATH = ".kimiflow/project/PROPOSALS.jsonl"
_USAGE_PATH = ".kimiflow/project/MEMORY-USAGE.json"
_ECONOMICS_PATH = ".kimiflow/project/MEMORY-ECONOMICS.jsonl"
_DEFAULT_AVOIDED_PER_HIT = 1200
_MAX_UTILITY_USE_COUNT = 999999999
_GLOBAL_EFFICIENCY_FILE = "token-economics.jsonl"


def _jq_or(value, default):
    # jq `value // default`: substitute when value is null (None) or false; "" / 0
    # are truthy in jq and pass through. (Mirrors recall_index._jq_or.)
    return default if value is None or value is False else value


def _max_present(values):
    # jq `[... // empty] | sort | last // null` (and the `// null` + select(!=null)
    # variant): keep values that are neither null nor false, sort, take the max;
    # null when nothing remains.
    kept = [v for v in values if v is not None and v is not False]
    return sorted(kept)[-1] if kept else None


def _jq_sum(values):
    # jq `[ ... ] | add // 0` number rendering: empty -> 0; a single element is returned
    # verbatim (jq preserves a literal's form, so 5.0 stays 5.0); 2+ elements do real
    # addition rendered canonically -- an integral float result collapses to an int
    # (e.g. -5.5 + 2.5 -> -3, not -3.0). (Reachable only with float fields, which real
    # token-count telemetry never has; this keeps byte-parity with jq regardless.)
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    total = sum(values)
    if isinstance(total, float) and total.is_integer():
        return int(total)
    return total


def read_jsonl_summary(path):
    # Bash read_jsonl_summary (135-171): counts by status/sensitivity plus a
    # topic->count map. Missing file -> the all-zero shape (identical to an empty
    # file through the jq branch). `current` defaults missing status to "current";
    # the other status/sensitivity buckets default to "" so only explicit values count.
    if not os.path.isfile(path):
        rows = []
    else:
        rows = [row for row in store.read_jsonl(path) if isinstance(row, dict)]

    counts = {}
    for row in rows:
        topic = _jq_or(row.get("topic"), "uncategorized")
        if not isinstance(topic, str):
            topic = "uncategorized"
        counts[topic] = counts.get(topic, 0) + 1
    by_topic = {key: counts[key] for key in sorted(counts)}  # jq sort_by + group_by

    def status_is(value):
        return sum(1 for r in rows if _jq_or(r.get("status"), "") == value)

    def sensitivity_is(value):
        return sum(1 for r in rows if _jq_or(r.get("sensitivity"), "") == value)

    return {
        "total": len(rows),
        "current": sum(1 for r in rows if _jq_or(r.get("status"), "current") == "current"),
        "stale": status_is("stale"),
        "superseded": status_is("superseded"),
        "archived": status_is("archived"),
        "private": sensitivity_is("private"),
        "security": sensitivity_is("security"),
        "by_topic": by_topic,
    }


def proposal_summary_json(path):
    # Bash proposal_summary_json (79-110): PROPOSALS.jsonl counts by status, plus a
    # type->count map. by_type uses jq `reduce` -> first-appearance key order (NOT
    # sorted, unlike read_jsonl_summary's by_topic). `pending` defaults missing
    # status to "pending"; the other buckets default to "".
    if not os.path.isfile(path):
        return {
            "present": False,
            "path": _PROPOSALS_PATH,
            "total": 0,
            "pending": 0,
            "approved": 0,
            "applied": 0,
            "rejected": 0,
            "needs_revalidation": 0,
            "by_type": {},
        }

    rows = [row for row in store.read_jsonl(path) if isinstance(row, dict)]
    by_type = {}
    for row in rows:
        kind = _jq_or(row.get("type"), "unknown")
        if not isinstance(kind, str):
            kind = "unknown"
        by_type[kind] = by_type.get(kind, 0) + 1

    def status_is(value, default=""):
        return sum(1 for r in rows if _jq_or(r.get("status"), default) == value)

    return {
        "present": True,
        "path": _PROPOSALS_PATH,
        "total": len(rows),
        "pending": status_is("pending", "pending"),
        "approved": status_is("approved"),
        "applied": status_is("applied"),
        "rejected": status_is("rejected"),
        "needs_revalidation": status_is("needs_revalidation"),
        "by_type": by_type,
    }


def _usage_absent():
    return {
        "present": False,
        "path": _USAGE_PATH,
        "tracked_items": 0,
        "total_uses": 0,
        "last_used_at": None,
        "by_kind": {},
        "events_tracked": 0,
        "by_event": {},
        "economics": {
            "recall_writes": 0,
            "history_writes": 0,
            "total_hit_count": 0,
            "estimated_output_tokens": 0,
            "last_event_at": None,
        },
        "hot_items": 0,
    }


def usage_summary_json(path):
    # Bash usage_summary_json (184-241): reads MEMORY-USAGE.json (a single object with
    # `.items` map + `.events` array). The Bash guard `[ ! -f ] || ! jq -e .` falls to
    # the absent shape when the file is missing, invalid JSON, or top-level null/false;
    # store.read_json returns None for missing/invalid and the literal for null. We also
    # treat a valid-but-non-object top level as absent (Bash jq-errors on `.items` there
    # -- unreachable for real MEMORY-USAGE.json; see plan).
    data = store.read_json(path)
    if not isinstance(data, dict):
        return _usage_absent()

    items = _jq_or(data.get("items"), {})
    events = _jq_or(data.get("events"), [])
    if not isinstance(items, dict):
        items = {}
    if not isinstance(events, list):
        events = []
    item_values = list(items.values())

    by_kind = {}
    for item in item_values:
        kind = _jq_or(item.get("kind"), "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1

    by_event = {}
    for event in events:
        kind = _jq_or(event.get("kind"), "unknown")
        acc = by_event.get(kind)
        if acc is None:
            acc = {"writes": 0, "hits": 0, "estimated_tokens": 0, "last_at": None}
            by_event[kind] = acc
        acc["writes"] += 1
        acc["hits"] += _jq_or(event.get("hit_count"), 0)
        acc["estimated_tokens"] += _jq_or(event.get("estimated_tokens"), 0)
        # jq: .last_at = ([.last_at, (.at // null)] | map(select(. != null)) | sort | last // null)
        at = _jq_or(event.get("at"), None)
        acc["last_at"] = _max_present([acc["last_at"], at])

    def count_event_kind(value):
        return sum(1 for e in events if _jq_or(e.get("kind"), "") == value)

    return {
        "present": True,
        "path": _USAGE_PATH,
        "tracked_items": len(items),
        "total_uses": sum(_jq_or(i.get("use_count"), 0) for i in item_values),
        "last_used_at": _max_present([i.get("last_used_at") for i in item_values]),
        "by_kind": by_kind,
        "events_tracked": len(events),
        "by_event": by_event,
        "economics": {
            "recall_writes": count_event_kind("recall"),
            "history_writes": count_event_kind("history"),
            "total_hit_count": sum(_jq_or(e.get("hit_count"), 0) for e in events),
            "estimated_output_tokens": sum(_jq_or(e.get("estimated_tokens"), 0) for e in events),
            "last_event_at": _max_present([e.get("at") for e in events]),
        },
        "hot_items": sum(1 for i in item_values if _jq_or(i.get("use_count"), 0) > 1),
    }


def _n(value):
    # jq `tonumber? // 0`: numbers pass through (int/float preserved); numeric strings
    # parse (whitespace-tolerant); bool / null / non-numeric string / container -> 0.
    # Scientific-notation strings (e.g. "1e3") render differently than jq (Python json
    # "1000.0" vs jq "1E+3") -- unreachable: economics fields are JSON numbers, not strings.
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return 0
    return 0


def _field_n(row, key):
    # Bash `(.key // 0) | n`: null/false/missing -> 0, then tonumber-normalize.
    return _n(_jq_or(row.get(key), 0))


def _avoided_per_hit():
    # Bash: ${KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT:-1200} then case ''|*[!0-9]* -> 1200.
    # Only a non-empty all-ASCII-digit value is honored (so "0" is valid; "-5"/"1.5" -> 1200).
    raw = os.environ.get("KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT")
    if raw and all(c in "0123456789" for c in raw):
        return int(raw)
    return _DEFAULT_AVOIDED_PER_HIT


def _economics_absent():
    return {
        "present": False,
        "path": _ECONOMICS_PATH,
        "runs_tracked": 0,
        "confidence": "none",
        "verdict": "no_data",
        "action_required": False,
        "normalized_legacy_rows": 0,
        "by_result": {},
        "totals": {
            "always_on_tokens": 0,
            "user_memory_tokens": 0,
            "recall_tokens": 0,
            "recall_hit_count": 0,
            "used_hit_count": 0,
            "estimated_avoided_scan_tokens": 0,
            "net_estimated_tokens_saved": 0,
        },
        "estimated_savings_percent": None,
        "averages": {
            "net_estimated_tokens_saved_per_run": 0,
            "recall_hit_count_per_run": 0,
            "used_hit_count_per_run": 0,
        },
        "last_recorded_at": None,
        "note": "No run-level memory economics recorded yet.",
    }


def economics_summary_json(path):
    # Bash economics_summary_json (243-364): normalizes each MEMORY-ECONOMICS.jsonl row
    # (recompute avoided = used * avoided_per_hit; net = avoided - always - user - recall),
    # classifies a per-row `result`, then aggregates totals/averages/verdict/confidence.
    if not os.path.isfile(path):
        return _economics_absent()

    avoided_per_hit = _avoided_per_hit()
    rows = []
    for raw in store.read_jsonl(path):
        always = _field_n(raw, "always_on_tokens")
        user = _field_n(raw, "user_memory_tokens")
        recall_tokens = _field_n(raw, "recall_tokens")
        hits = _field_n(raw, "recall_hit_count")
        used = _field_n(raw, "used_hit_count")
        avoided = used * avoided_per_hit
        net = avoided - always - user - recall_tokens
        if hits == 0:
            result = "unknown"
        elif used > 0 and net > 0:
            result = "saving"
        elif net < 0:
            result = "waste"
        else:
            result = "neutral"
        rows.append({
            "always": always, "user": user, "recall_tokens": recall_tokens,
            "hits": hits, "used": used, "avoided": avoided, "net": net, "result": result,
            "raw_avoided": _field_n(raw, "estimated_avoided_scan_tokens"),
            "raw_net": _field_n(raw, "net_estimated_tokens_saved"),
            "recorded_at": raw.get("recorded_at"),
        })

    n = len(rows)
    net = sum(r["net"] for r in rows)
    hits = sum(r["hits"] for r in rows)
    used = sum(r["used"] for r in rows)
    avoided = sum(r["avoided"] for r in rows)
    always = sum(r["always"] for r in rows)
    user = sum(r["user"] for r in rows)
    recall_tokens = sum(r["recall_tokens"] for r in rows)
    saving = sum(1 for r in rows if r["result"] == "saving")
    waste = sum(1 for r in rows if r["result"] == "waste")

    if n == 0:
        confidence = "none"
    elif n < 8:
        confidence = "low"
    elif n < 20:
        confidence = "medium"
    else:
        confidence = "high"

    if n == 0:
        verdict = "no_data"
    elif n < 8:
        verdict = "insufficient_data"
    elif net > 0 and saving >= waste:
        verdict = "saving_likely"
    elif waste > saving or net < 0:
        verdict = "waste_risk"
    else:
        verdict = "neutral"

    action_required = n >= 8 and (waste > saving or net < 0)

    normalized_legacy_rows = sum(
        1 for r in rows if r["raw_avoided"] != r["avoided"] or r["raw_net"] != r["net"]
    )

    by_result = {}
    for r in rows:
        by_result[r["result"]] = by_result.get(r["result"], 0) + 1

    if n < 8:
        note = "Too few runs for a reliable savings claim; treat this as directional telemetry."
    elif net > 0 and saving >= waste:
        note = "Run telemetry suggests memory is likely saving tokens."
    elif waste > saving or net < 0:
        note = ("Run telemetry suggests memory may cost more than it saves; "
                "review recall/always-on budget.")
    else:
        note = "Run telemetry is roughly neutral."

    return {
        "present": True,
        "path": _ECONOMICS_PATH,
        "runs_tracked": n,
        "confidence": confidence,
        "verdict": verdict,
        "action_required": action_required,
        "normalized_legacy_rows": normalized_legacy_rows,
        "by_result": by_result,
        "totals": {
            "always_on_tokens": always,
            "user_memory_tokens": user,
            "recall_tokens": recall_tokens,
            "recall_hit_count": hits,
            "used_hit_count": used,
            "estimated_avoided_scan_tokens": avoided,
            "net_estimated_tokens_saved": net,
        },
        "estimated_savings_percent": (
            math.floor(net * 100 / avoided) if avoided > 0 else None
        ),
        "averages": {
            "net_estimated_tokens_saved_per_run": math.floor(net / n) if n > 0 else 0,
            "recall_hit_count_per_run": math.floor(hits / n) if n > 0 else 0,
            "used_hit_count_per_run": math.floor(used / n) if n > 0 else 0,
        },
        "last_recorded_at": _max_present([r["recorded_at"] for r in rows]),
        "note": note,
    }


def _global_efficiency_absent(enabled, display):
    return {
        "enabled": enabled,
        "present": False,
        "path": display,
        "scope": "global_local_anonymous",
        "runs_tracked": 0,
        "projects_tracked": 0,
        "confidence": "none",
        "verdict": "no_data",
        "estimated_savings_percent": None,
        "action_required": False,
        "by_result": {},
        "totals": {
            "always_on_tokens": 0,
            "user_memory_tokens": 0,
            "recall_tokens": 0,
            "recall_hit_count": 0,
            "used_hit_count": 0,
            "estimated_avoided_scan_tokens": 0,
            "net_estimated_tokens_saved": 0,
        },
        "averages": {
            "net_estimated_tokens_saved_per_run": 0,
            "recall_hit_count_per_run": 0,
            "used_hit_count_per_run": 0,
        },
        "last_recorded_day": None,
        "privacy": {
            "local_only": True,
            "stores_content": False,
            "stores_paths": False,
            "stores_repo_name": False,
            "stores_prompts": False,
            "project_id_salted_hash": True,
        },
        "note": (
            "No global local efficiency rows recorded yet." if enabled
            else "Global local efficiency stats are disabled by KIMIFLOW_GLOBAL_METRICS."
        ),
    }


def global_efficiency_summary_json():
    # Bash global_efficiency_summary_json (483-597): aggregates the cross-project,
    # local-anonymous token-economics.jsonl. Unlike economics_summary_json this sums the
    # stored fields directly (NO tonumber / avoided recompute -- the Bash `def n` here is
    # dead code) and adds enabled/scope/projects_tracked/privacy/last_recorded_day. Reads
    # env (KIMIFLOW_GLOBAL_METRICS/KIMIFLOW_HOME/HOME) via global_metrics, like the Bash.
    enabled = global_metrics.enabled()
    display = global_metrics.display_path()
    base = global_metrics.base_dir()
    path = (base + "/" + _GLOBAL_EFFICIENCY_FILE) if base else ""
    if not enabled or not base or not os.path.isfile(path):
        return _global_efficiency_absent(enabled, display)

    rows = store.read_jsonl(path)
    n = len(rows)
    net = _jq_sum([_jq_or(r.get("net_estimated_tokens_saved"), 0) for r in rows])
    hits = _jq_sum([_jq_or(r.get("recall_hit_count"), 0) for r in rows])
    used = _jq_sum([_jq_or(r.get("used_hit_count"), 0) for r in rows])
    avoided = _jq_sum([_jq_or(r.get("estimated_avoided_scan_tokens"), 0) for r in rows])
    always = _jq_sum([_jq_or(r.get("always_on_tokens"), 0) for r in rows])
    user = _jq_sum([_jq_or(r.get("user_memory_tokens"), 0) for r in rows])
    recall_tokens = _jq_sum([_jq_or(r.get("recall_tokens"), 0) for r in rows])
    saving = sum(1 for r in rows if _jq_or(r.get("result"), "") == "saving")
    waste = sum(1 for r in rows if _jq_or(r.get("result"), "") == "waste")

    projects = set()
    for r in rows:
        pid = r.get("project_id")
        if pid is not None and pid is not False:  # jq `.project_id // empty`
            projects.add(pid)

    if n == 0:
        confidence = "none"
    elif n < 8:
        confidence = "low"
    elif n < 20:
        confidence = "medium"
    else:
        confidence = "high"

    if n == 0:
        verdict = "no_data"
    elif n < 8:
        verdict = "insufficient_data"
    elif net > 0 and saving >= waste:
        verdict = "saving_likely"
    elif waste > saving or net < 0:
        verdict = "waste_risk"
    else:
        verdict = "neutral"

    by_result = {}
    for r in rows:
        key = _jq_or(r.get("result"), "unknown")
        by_result[key] = by_result.get(key, 0) + 1

    if n < 8:
        note = "Too few global local runs for a reliable savings claim; show as an estimate only."
    elif net > 0 and saving >= waste:
        note = "Global local telemetry suggests memory is likely saving tokens."
    elif waste > saving or net < 0:
        note = "Global local telemetry suggests memory may cost more than it saves."
    else:
        note = "Global local telemetry is roughly neutral."

    return {
        "enabled": True,
        "present": True,
        "path": display,
        "scope": "global_local_anonymous",
        "runs_tracked": n,
        "projects_tracked": len(projects),
        "confidence": confidence,
        "verdict": verdict,
        "estimated_savings_percent": (
            math.floor(net * 100 / avoided) if avoided > 0 else None
        ),
        "action_required": n >= 8 and (waste > saving or net < 0),
        "by_result": by_result,
        "totals": {
            "always_on_tokens": always,
            "user_memory_tokens": user,
            "recall_tokens": recall_tokens,
            "recall_hit_count": hits,
            "used_hit_count": used,
            "estimated_avoided_scan_tokens": avoided,
            "net_estimated_tokens_saved": net,
        },
        "averages": {
            "net_estimated_tokens_saved_per_run": math.floor(net / n) if n > 0 else 0,
            "recall_hit_count_per_run": math.floor(hits / n) if n > 0 else 0,
            "used_hit_count_per_run": math.floor(used / n) if n > 0 else 0,
        },
        "last_recorded_day": _max_present([r.get("recorded_day") for r in rows]),
        "privacy": {
            "local_only": True,
            "stores_content": False,
            "stores_paths": False,
            "stores_repo_name": False,
            "stores_prompts": False,
            "project_id_salted_hash": True,
        },
        "note": note,
    }


_LEARNINGS_DEFAULT_STALE_AFTER = 90
_UTILITY_SNAPSHOT_UNSET = object()


def _learning_stale_after():
    # Bash: ${KIMIFLOW_LEARNING_STALE_AFTER_DAYS:-90}; then case ''|*[!0-9]* -> 90.
    # Only a non-empty all-ASCII-digit value is honored (so "0" is valid).
    raw = os.environ.get("KIMIFLOW_LEARNING_STALE_AFTER_DAYS")
    if raw and all(c in "0123456789" for c in raw):
        return int(raw)
    return _LEARNINGS_DEFAULT_STALE_AFTER


def _usage_items(usage_file):
    # Bash: usage='{}'; if [ -f file ] && jq -e . file; then usage=$(jq -c '.items // {}').
    # store.read_json -> None for missing/invalid/null; a valid non-dict top level (Bash
    # passes `jq -e .` then errors on `.items`) and a non-dict `.items` both fall back to
    # {} here (more robust; MEMORY-USAGE.json is always an object -> unreachable).
    data = store.read_json(usage_file)
    if not isinstance(data, dict):
        return {}
    items = _jq_or(data.get("items"), {})
    return items if isinstance(items, dict) else {}


def _strict_usage_items(usage_file):
    if not os.path.exists(usage_file):
        return {}, True
    try:
        with open(usage_file, "r", encoding="utf-8") as handle:
            data = store.parse_json_object_strict(handle.read())
    except (OSError, UnicodeDecodeError):
        return {}, False
    if data is None:
        return {}, False
    items = data.get("items", {})
    return (items, True) if isinstance(items, dict) else ({}, False)


def _strict_usage_items_snapshot(snapshot):
    if snapshot is None:
        return {}, True
    try:
        data = store.parse_json_object_strict(snapshot[1].decode("utf-8"))
    except (UnicodeDecodeError, IndexError, TypeError):
        return {}, False
    if data is None:
        return {}, False
    items = data.get("items", {})
    return (items, True) if isinstance(items, dict) else ({}, False)


def _utility_use_count(entry, present):
    if not present:
        return 0, False, True
    if not isinstance(entry, dict) or "use_count" not in entry:
        return 0, False, False
    value = entry.get("use_count")
    if isinstance(value, bool):
        return 0, False, False
    if isinstance(value, int) and value >= 0:
        return min(value, _MAX_UTILITY_USE_COUNT), value > _MAX_UTILITY_USE_COUNT, True
    if isinstance(value, str):
        digits = value.strip()
        if digits and all(char in "0123456789" for char in digits):
            limit = str(_MAX_UTILITY_USE_COUNT)
            if len(digits) > len(limit) or (len(digits) == len(limit) and digits > limit):
                return _MAX_UTILITY_USE_COUNT, True, True
            return int(digits), False, True
    return 0, False, False


def _current_rows(learnings):
    # jq: split lines | fromjson?//empty | select((.status // "current") == "current").
    return [r for r in store.read_jsonl(learnings)
            if isinstance(r, dict) and _jq_or(r.get("status"), "current") == "current"]


def _learning_id(row):
    # jq `.id // ""` (null/false/missing -> "").
    value = _jq_or(row.get("id"), "")
    return value if isinstance(value, str) else ""


def _last_verified_is_stale(row, cutoff):
    # jq `(cutoff != "") and ((.last_verified // "") < cutoff)` with jq's total order:
    # after `// ""`, null/false are "" (string compare); a non-string last_verified is
    # compared cross-type -- bool/number sort BELOW strings (stale), array/object ABOVE
    # (not stale). Guards the malformed non-string case that a raw Python `<` would crash.
    if cutoff == "":
        return False
    value = _jq_or(row.get("last_verified"), "")
    if isinstance(value, str):
        return value < cutoff
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return True
    return False


def _verified_date_valid(row):
    value = row.get("last_verified")
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def learning_is_stale(row):
    """Return the shared lifecycle-staleness decision for one learning row."""
    return _last_verified_is_stale(row, clock.date_days_ago(_learning_stale_after()))


def learning_lifecycle_json(learnings, usage_file):
    # Bash learning_lifecycle_json (599-651): current LEARNINGS rows split into
    # used/unused (by MEMORY-USAGE.json `items["learning:<id>"]` presence) and
    # stale_candidates (last_verified < cutoff). Missing learnings -> a smaller absent
    # shape (omits the *_ids lists).
    stale_after = _learning_stale_after()
    cutoff = clock.date_days_ago(stale_after)
    cutoff_date = cutoff if cutoff != "" else None

    if not os.path.isfile(learnings):
        return {
            "stale_after_days": stale_after,
            "cutoff_date": cutoff_date,
            "current": 0,
            "stale_candidates": 0,
            "stale_candidate_ids": [],
            "unused_current": 0,
            "used_current": 0,
        }

    usage = _usage_items(usage_file)
    current = _current_rows(learnings)
    ids = [i for i in (_learning_id(r) for r in current) if len(i) > 0]

    def tracked(i):
        # jq `($usage["learning:" + .] // null) != null`: present and not null/false.
        value = usage.get("learning:" + i)
        return value is not None and value is not False

    used = [i for i in ids if tracked(i)]
    unused = [i for i in ids if not tracked(i)]
    stale_ids = [_learning_id(r) for r in current if _last_verified_is_stale(r, cutoff)]

    return {
        "stale_after_days": stale_after,
        "cutoff_date": cutoff_date,
        "current": len(current),
        "stale_candidates": len(stale_ids),
        "stale_candidate_ids": stale_ids,
        "unused_current": len(unused),
        "unused_current_ids": unused[:20],
        "cold_candidate_ids": unused[:10],
        "used_current": len(used),
        "used_current_ids": used[:20],
    }


def _bounded_ids(rows):
    # jq `map(.id // "") | map(select(length > 0)) | .[:20]`.
    return [i for i in (_learning_id(r) for r in rows) if len(i) > 0][:20]


def learning_usefulness_json(learnings, usage_file):
    # Bash learning_usefulness_json (653-712): classifies current rows into exclusive
    # hot/warm/cold/stale tiers by use_count + staleness, then promote (hot+warm with
    # safe confidence/sensitivity) and compress (cold+stale) candidates.
    stale_after = _learning_stale_after()
    cutoff = clock.date_days_ago(stale_after)
    cutoff_date = cutoff if cutoff != "" else None
    usage = _usage_items(usage_file)

    if not os.path.isfile(learnings):
        empty = {"count": 0, "ids": []}
        return {
            "schema_version": 1,
            "stale_after_days": stale_after,
            "cutoff_date": cutoff_date,
            "hot": dict(empty),
            "warm": dict(empty),
            "cold": dict(empty),
            "stale": dict(empty),
            "promote_candidates": dict(empty),
            "compress_candidates": dict(empty),
        }

    rows = []
    for r in _current_rows(learnings):
        entry = usage.get("learning:" + _learning_id(r))
        raw_uc = entry.get("use_count") if isinstance(entry, dict) else None
        use_count = _n(_jq_or(raw_uc, 0))
        is_stale = _last_verified_is_stale(r, cutoff)
        rows.append(dict(r, use_count=use_count, is_stale=is_stale))

    stale = [r for r in rows if r["is_stale"]]
    hot = [r for r in rows if not r["is_stale"] and r["use_count"] >= 2]
    warm = [r for r in rows if not r["is_stale"] and r["use_count"] == 1]
    cold = [r for r in rows if not r["is_stale"] and r["use_count"] == 0]
    promote = [
        r for r in (hot + warm)
        if _jq_or(r.get("confidence"), "medium") in ("high", "medium")
        and _jq_or(r.get("sensitivity"), "normal") not in ("private", "security")
    ]
    compress = cold + stale

    def tier(rows_):
        return {"count": len(rows_), "ids": _bounded_ids(rows_)}

    return {
        "schema_version": 1,
        "stale_after_days": stale_after,
        "cutoff_date": cutoff_date,
        "hot": tier(hot),
        "warm": tier(warm),
        "cold": tier(cold),
        "stale": tier(stale),
        "promote_candidates": tier(promote),
        "compress_candidates": tier(compress),
        "basis": "exclusive_tiers_current_rows; stale rows are never promote candidates",
    }


def learning_utility_rows(learnings, usage_file, max_rows=20, learning_rows=None,
                          usage_snapshot=_UTILITY_SNAPSHOT_UNSET):
    """Return a bounded, explainable utility view of current learning rows.

    The five points are intentionally simple: up to two for observed use, and one
    each for freshness, current stored evidence, and medium/high confidence.
    """
    cutoff = clock.date_days_ago(_learning_stale_after())
    if usage_snapshot is _UTILITY_SNAPSHOT_UNSET:
        usage, usage_file_valid = _strict_usage_items(usage_file)
    else:
        usage, usage_file_valid = _strict_usage_items_snapshot(usage_snapshot)
    result = []
    source_rows = (store.read_jsonl_objects_strict(learnings)
                   if learning_rows is None else learning_rows)
    for row in source_rows:
        if _jq_or(row.get("status"), "current") != "current":
            continue
        rid = _learning_id(row)
        if len(rid) > 128:
            rid = ""
        key = "learning:" + rid
        present = key in usage
        use_count, use_count_capped, entry_valid = _utility_use_count(
            usage.get(key), present
        )
        usage_valid = usage_file_valid and entry_valid
        use_points = 2 if use_count >= 2 else (1 if use_count >= 1 else 0)
        verified_valid = _verified_date_valid(row)
        stale = verified_valid and _last_verified_is_stale(row, cutoff)
        fingerprints = _jq_or(row.get("evidence_fingerprints"), [])
        evidence_current = (
            isinstance(fingerprints, list) and bool(fingerprints)
            and all(isinstance(fp, dict) and fp.get("status") == "current"
                    for fp in fingerprints)
        )
        confidence = _jq_or(row.get("confidence"), "medium")
        if not isinstance(confidence, str) or len(confidence) > 32:
            confidence = "invalid"
        confidence_point = confidence in ("medium", "high")
        points = min(5, use_points + (verified_valid and not stale)
                     + evidence_current + confidence_point)
        reasons = ["uses:%s%s" % (use_count, "+" if use_count_capped else "")]
        if not usage_valid:
            reasons.append("usage_invalid")
        reasons.append("stale" if stale else ("fresh" if verified_valid else "last_verified_invalid"))
        reasons.append("evidence_current" if evidence_current else "evidence_not_current")
        reasons.append("confidence:%s" % confidence)
        result.append({
            "id": rid,
            "utility_points": int(points),
            "use_count": use_count,
            "stale": stale,
            "last_verified_valid": verified_valid,
            "evidence_current": evidence_current,
            "confidence": confidence,
            "usage_valid": usage_valid,
            "quarantine_eligible": stale and use_count == 0 and usage_valid and verified_valid,
            "reasons": reasons,
        })
        if max_rows is not None and len(result) >= max_rows:
            break
    return result
