"""Bounded always-on memory markdown renderers: MEMORY.md (project) and USER.md
(user profile). Verbatim behavioral ports of the Bash write_bounded_memory /
write_bounded_user_memory at kimiflow--v0.1.50 (2789-2887): read the JSONL rows,
filter to current + publish-safe, prioritize, truncate to a word budget by
shrinking the item count, and render markdown."""
import os

from . import clock, rows as row_policy, store


def _int_env(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _confidence_rank(row):
    # jq: high -> 0, medium -> 1, else -> 2.
    confidence = row.get("confidence", "")
    if confidence == "high":
        return 0
    if confidence == "medium":
        return 1
    return 2


def _bullet_evidence(row):
    # jq: (.evidence // []) | .[0] // "NOT VERIFIED". Evidence is the sanitized
    # list[str] from rows.sanitize_evidence_json; empty -> NOT VERIFIED.
    evidence = row.get("evidence", [])
    return str(evidence[0]) if isinstance(evidence, list) and evidence else "NOT VERIFIED"


def write_bounded_memory(root):
    project = os.path.join(root, ".kimiflow", "project")
    memory = os.path.join(project, "MEMORY.md")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    usage_file = os.path.join(project, "MEMORY-USAGE.json")
    if not os.path.isfile(learnings):
        return
    os.makedirs(project, exist_ok=True)

    budget = _int_env("KIMIFLOW_MEMORY_BUDGET", 900)

    raw_max = os.environ.get("KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS", "8")
    max_items = int(raw_max) if raw_max.isdigit() else 8  # case ''|*[!0-9]* -> 8
    if max_items <= 0:                                    # [ "$max_items" -gt 0 ] || 8
        max_items = 8

    usage = {}
    data = store.read_json(usage_file)
    if isinstance(data, dict):
        items = data.get("items")  # jq: .items // {}
        if isinstance(items, dict):
            usage = items

    rows = store.read_jsonl(learnings)
    # jq to_entries + {_row_index, _usage_count}: index each row, look up its usage.
    entries = []
    for index, row in enumerate(row for row in rows if isinstance(row, dict)):
        usage_entry = usage.get("learning:" + str(row.get("id", "")))
        use_count = usage_entry.get("use_count", 0) if isinstance(usage_entry, dict) else 0
        if isinstance(use_count, bool) or not isinstance(use_count, (int, float)):
            use_count = 0
        entries.append((index, use_count, row))

    iso = clock.iso_now()
    while True:
        selected = [
            entry for entry in entries
            if entry[2].get("status", "current") == "current"
            and entry[2].get("sensitivity", "normal") not in ("security", "private")
            and row_policy.learning_is_durable(entry[2])
        ]
        # sort_by([-_usage_count, confidence_rank, -_row_index]) ascending.
        selected.sort(key=lambda e: (-e[1], _confidence_rank(e[2]), -e[0]))
        bullets = [
            "- [%s \u00b7 %s] %s (evidence: %s)" % (
                row.get("topic", "uncategorized"),
                row.get("kind", "learning"),
                str(row.get("summary", ""))[:220],
                _bullet_evidence(row),
            )
            for _index, _use_count, row in selected[:max_items]
        ]
        # DIVERGENCE (spec section 12, user-blessed): the Bash builds the body via
        # `jq -Rsc ... | join("\n")`, whose -c output JSON-encodes the joined string,
        # so MEMORY.md/USER.md get a quoted one-liner with literal "\n". The port
        # renders real newline-separated markdown bullets. The file-parity harness
        # (when these are wired in a later plan) whitelists the body-format difference.
        body = "\n".join(bullets)
        content = (
            "# Project Memory\n\n"
            + "Generated: " + iso + "\n"
            + "Policy: bounded always-on summary prioritized by use, confidence, and recency; "
            + "raw/private/security learnings stay in LEARNINGS.jsonl and are recalled on demand.\n\n"
            + "## Always-On Learnings\n\n"
            + (body + "\n" if body
               else "No publish-safe always-on learnings yet. Use LEARNINGS.jsonl recall on demand.\n")
        )
        # word_count_file equivalent: whitespace token count of the rendered file.
        words = len(content.split())
        if words <= budget or max_items <= 2:
            break
        max_items -= 2

    store.atomic_write(memory, content, refuse_symlink=False)


def write_bounded_user_memory(root):
    project = os.path.join(root, ".kimiflow", "project")
    memory = os.path.join(project, "USER.md")
    rows_path = os.path.join(project, "USER.jsonl")
    if not os.path.isfile(rows_path):
        return
    os.makedirs(project, exist_ok=True)

    budget = _int_env("KIMIFLOW_USER_MEMORY_BUDGET", 500)
    rows = store.read_jsonl(rows_path)

    max_items = 8
    iso = clock.iso_now()
    while True:
        selected = [
            row for row in rows if isinstance(row, dict)
            if row.get("status", "current") == "current"
            and row.get("sensitivity", "normal") != "security"
        ]
        # jq reverse | .[:max] | reverse == the last `max_items` in original order.
        selected = selected[-max_items:]
        bullets = [
            "- [%s] %s (evidence: %s)" % (
                row.get("topic", "profile"),
                str(row.get("summary", ""))[:220],
                _bullet_evidence(row),
            )
            for row in selected
        ]
        # Real markdown bullets, not the Bash `jq -c` quoted one-liner (see
        # write_bounded_memory + spec section 12 for the user-blessed body-format divergence).
        body = "\n".join(bullets)
        content = (
            "# User Profile\n\n"
            + "Generated: " + iso + "\n"
            + "Policy: local-only user/workflow preferences; never publish to repo docs.\n\n"
            + "## Always-On User Notes\n\n"
            + (body + "\n" if body else "No user-profile notes yet.\n")
        )
        words = len(content.split())
        if words <= budget or max_items <= 2:
            break
        max_items -= 2

    store.atomic_write(memory, content, refuse_symlink=False)
