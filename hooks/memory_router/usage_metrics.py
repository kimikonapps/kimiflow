"""MEMORY-USAGE.json writer: update_usage_metrics. Behavioral port of the Bash
update_usage_metrics at kimiflow--v0.1.50 (1705-1769). Shared by the `recall` and
(future) `history` subcommands - records per-hit use counts + a capped event log."""
import os
import re

from . import clock, contracts, store

_USAGE_FILE = "MEMORY-USAGE.json"
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9_]+")


def _jq_or(value, default):
    # jq `value // default`: substitute when null (None) or false; "" / 0 pass through.
    return default if value is None or value is False else value


def _hit_key(hit):
    # Bash hit_key (1719-1723).
    hid = _jq_or(hit.get("id"), "")
    if hid != "":
        return "learning:" + str(hid)
    if _jq_or(hit.get("kind"), "") == "run_artifact":
        return "run:" + str(_jq_or(hit.get("path"), _jq_or(hit.get("ref"), "unknown")))
    kind = str(_jq_or(hit.get("kind"), "memory"))
    ref = _jq_or(hit.get("ref"), _jq_or(hit.get("path"), _jq_or(hit.get("title"), "unknown")))
    return kind + ":" + str(ref)


def _evidence_first(hit):
    # jq `(.evidence // []) | .[0] // ""`: first evidence entry, or "".
    evidence = _jq_or(hit.get("evidence"), [])
    first = evidence[0] if isinstance(evidence, list) and evidence else None
    return _jq_or(first, "")


def _hit_value(hit):
    # Bash value object (1726-1732); field order kind,source,title,ref,summary.
    return {
        "kind": _jq_or(hit.get("kind"), "memory"),
        "source": _jq_or(hit.get("source"), _jq_or(hit.get("path"), "")),
        "title": _jq_or(hit.get("title"), _jq_or(hit.get("summary"), _jq_or(hit.get("id"), ""))),
        "ref": _jq_or(hit.get("ref"), _evidence_first(hit)),
        "summary": _jq_or(hit.get("summary"), ""),
    }


def _token_count(value):
    # Bash 1761: ((.value.title // "") + " " + (.value.summary // "")) | gsub("[^A-Za-z0-9_]+"; " ")
    # | split(" ") | map(select(length > 0)) | length.
    text = str(_jq_or(value.get("title"), "")) + " " + str(_jq_or(value.get("summary"), ""))
    return len([t for t in _WORD_SPLIT.sub(" ", text).split(" ") if t])


def update_usage_metrics(root, hits, event_kind="recall"):
    project = os.path.join(root, ".kimiflow", "project")
    usage_file = os.path.join(project, _USAGE_FILE)
    os.makedirs(project, exist_ok=True)
    now = clock.iso_now()

    current = store.read_json(usage_file)
    if not isinstance(current, dict):
        # missing / invalid / null / false / non-object -> the default shape (Bash uses the
        # `jq -e .` guard; the port treats any non-dict as absent, same class as the
        # summaries non-object rows - unreachable, safer than the Bash `.schema_version=`
        # crash on a non-object file).
        current = {"schema_version": 1, "updated_at": None, "items": {}, "events": []}

    updates = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue  # jq would error on a non-object hit; unreachable, skip (safer).
        updates.append({"key": _hit_key(hit), "value": _hit_value(hit)})

    out = dict(current)
    out["schema_version"] = 1
    out["updated_at"] = now

    items = current.get("items")
    items = dict(items) if isinstance(items, dict) else {}
    events = current.get("events")
    events = list(events) if isinstance(events, list) else []

    # Bash reduce (1745-1754): use_count reads the ACCUMULATING items, so a key repeated
    # within one batch increments cumulatively. jq `+` keeps the left operand's key order.
    for upd in updates:
        existing = items.get(upd["key"])
        existing = existing if isinstance(existing, dict) else {}
        merged = dict(existing)
        merged.update(upd["value"])
        merged["use_count"] = _jq_or(existing.get("use_count"), 0) + 1
        merged["last_used_at"] = now
        items[upd["key"]] = merged
    out["items"] = items

    # estimated_tokens is `[...] | add // 0` over integer word-counts; sum() is byte-identical
    # for the reachable (int-only) domain (empty -> 0). keys: jq `unique` = sorted + dedup.
    events = events + [{
        "kind": event_kind,
        "at": now,
        "hit_count": len(updates),
        "estimated_tokens": sum(_token_count(u["value"]) for u in updates),
        "keys": sorted({u["key"] for u in updates}),
    }]
    out["events"] = events[-100:]

    # Bash mktemp (0600) + mv (replaces a symlinked path, not write-through).
    store.atomic_write(usage_file, contracts.dumps(out, pretty=True) + "\n",
                       mode=0o600, refuse_symlink=False)
