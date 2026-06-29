# memory-router Python CLI — Plan 5: bounded always-on memory writers (MEMORY.md / USER.md)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `write_bounded_memory` + `write_bounded_user_memory` — the renderers that regenerate the always-on `MEMORY.md` (project) and `USER.md` (user profile) markdown from the JSONL rows: read rows, keep only current + publish-safe ones, prioritize, truncate to a word budget by shrinking the item count, and write the markdown. These are called by `record`/`curate`/`review-run` after a write.

**Architecture:** New cohesive module `hooks/memory_router/memory_md.py` (the "bounded memory markdown" layer), a behavioral port of the Bash at `kimiflow--v0.1.50` (2789-2887). It composes already-ported helpers: `clock.iso_now`, `store.read_jsonl`, plus a tiny new `store.read_json`. **One user-blessed divergence** is included: the body is rendered as real markdown bullets (the Bash emits a `jq -c` quoted one-liner — a latent rendering bug); recorded in spec §12.

**Tech Stack:** Python 3.9+ stdlib only (`os`, `json` via `store`); no new third-party deps.

## Global Constraints

- **Python floor:** 3.9+, stdlib-only.
- **Drop-in / scope:** no edits to `hooks/memory-router.sh`, SKILL.md, reference.md, manifests, existing tests, or unrelated modules. This plan adds exactly: `memory_md.py`, `tests/test_memory_md.py`, one function appended to `store.py` (`read_json`), and one row appended to spec §12. **No subcommand wiring** (that is Plan 8).
- **Source of truth:** Bash @ `kimiflow--v0.1.50`:
  - `write_bounded_memory` (2789-2845), `write_bounded_user_memory` (2846-2887).
  - `iso_now` (44-46), `word_count_file` (52-59, `wc -w` == Python `len(text.split())`).
  - depends on already-ported `store.read_jsonl` (jsonl_rows lenient read, 70-78) and `clock.iso_now`.
- **MEMORY.md exact layout** (Bash 2828-2839): `# Project Memory\n\n` + `Generated: <iso>\n` + `Policy: bounded always-on summary prioritized by use, confidence, and recency; raw/private/security learnings stay in LEARNINGS.jsonl and are recalled on demand.\n\n` + `## Always-On Learnings\n\n` + (body + `\n`, or `No publish-safe always-on learnings yet. Use LEARNINGS.jsonl recall on demand.\n`).
- **USER.md exact layout** (Bash 2867-2878): `# User Profile\n\n` + `Generated: <iso>\n` + `Policy: local-only user/workflow preferences; never publish to repo docs.\n\n` + `## Always-On User Notes\n\n` + (body + `\n`, or `No user-profile notes yet.\n`).
- **Bullet formats:** project `- [<topic> \u00b7 <kind>] <summary[:220]> (evidence: <evidence[0] or NOT VERIFIED>)`; user `- [<topic>] <summary[:220]> (evidence: <...>)` (no kind). The `\u00b7` (U+00B7 middle dot) MUST be written as a byte-stable `\u00b7` escape, never a literal char.
- **Project selection** (`write_bounded_memory`): filter `status == "current"` (default current) and `sensitivity` not in (`security`, `private`); sort by `(-usage_count, confidence_rank, -row_index)` ascending where confidence_rank is high→0/medium→1/else→2 and usage_count comes from `MEMORY-USAGE.json` `.items["learning:<id>"].use_count`; then take the first `max_items`.
- **User selection** (`write_bounded_user_memory`): filter `status == "current"` and `sensitivity != "security"` ONLY (private is **kept**, unlike project); take the **last** `max_items` in original order (`reverse | .[:max] | reverse`); no usage weighting, no kind.
- **Budget shrink loop:** start `max_items`, render, count words of the whole file (`len(content.split())`); if `words <= budget` or `max_items <= 2` → stop; else `max_items -= 2` and re-render. Project budget `KIMIFLOW_MEMORY_BUDGET` (900); user budget `KIMIFLOW_USER_MEMORY_BUDGET` (500).
- **Project `max_items` env:** `KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS` (8); non-numeric → 8; `<= 0` → 8 (Bash `case ''|*[!0-9]*` then `-gt 0`). User `max_items` is a fixed 8 (no env).
- **Early return:** `write_bounded_memory` returns without writing if `LEARNINGS.jsonl` is absent; `write_bounded_user_memory` if `USER.jsonl` is absent.
- **Body-format divergence (user-blessed, spec §12):** the Bash renders the bullet body via `jq -Rsc … | join("\n")`; `-c` JSON-encodes the joined string, so MEMORY.md/USER.md get a quoted one-liner with literal `\n`. This port renders **real markdown bullets** (the obvious intent). A code comment marks it; the file-parity harness (Plan 7/8, when these writers are wired) must whitelist the body-format difference.
- **Nondeterminism:** `Generated: <iso_now>` — unit tests monkeypatch `clock.iso_now`; file parity normalizes it in the harness later.
- **Commits:** named paths only; no co-author / AI-attribution trailer.
- **Branch:** continue on `feat/memory-router-py-foundation`.

## Module placement rationale

These two renderers share filtering/render/shrink structure and are one responsibility (render bounded always-on memory markdown from rows) — distinct from `writes.py` (row persistence with dedup). A dedicated `memory_md.py` keeps each module single-purpose and matches the Plan 2/3/4 precedent of cohesive helper modules. `store.read_json` is a genuinely reusable IO primitive (status/metrics will reuse it), so it joins `store.py` per spec §6 ("ALL file IO in store.py").

## File Structure

| Path | Responsibility |
|---|---|
| `hooks/memory_router/memory_md.py` | `write_bounded_memory`, `write_bounded_user_memory`, `_int_env`, `_confidence_rank`, `_bullet_evidence`. |
| `hooks/memory_router/tests/test_memory_md.py` | unit tests (render, filters, sort, usage weighting, budget shrink, env validation, user reverse/keep-private, `store.read_json`). |
| `hooks/memory_router/store.py` | append `read_json()` (lenient single-object JSON read). |
| `docs/superpowers/specs/2026-06-28-memory-router-python-cli-design.md` | append one §12 row (body-format divergence). |

---

### Task 1: bounded memory writers (`memory_md.py`)

**Files:**
- Create: `hooks/memory_router/memory_md.py`
- Test: `hooks/memory_router/tests/test_memory_md.py`
- Modify: `hooks/memory_router/store.py` (append `read_json`)
- Edit: spec §12 (append one divergence row)

**Interfaces:**
- Consumes: `clock.iso_now`, `store.read_jsonl`, `store.read_json`, `store.atomic_write`.
- Produces (Plan 7/8 consume): `memory_md.write_bounded_memory(root) -> None` (writes `MEMORY.md`), `memory_md.write_bounded_user_memory(root) -> None` (writes `USER.md`), `store.read_json(path, default=None)`.

- [ ] **Step 1: Write the failing tests**

```python
# hooks/memory_router/tests/test_memory_md.py
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from memory_router import memory_md, store

ISO = "2026-06-29T00:00:00Z"


class MemoryMdBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project, exist_ok=True)
        p = mock.patch("memory_router.clock.iso_now", return_value=ISO)
        p.start()
        self.addCleanup(p.stop)
        # Guarantee defaults regardless of ambient environment.
        env = mock.patch.dict(os.environ, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for var in ("KIMIFLOW_MEMORY_BUDGET", "KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS",
                    "KIMIFLOW_USER_MEMORY_BUDGET"):
            os.environ.pop(var, None)

    def write_rows(self, name, rows):
        path = os.path.join(self.project, name)
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return path

    def read_md(self, name):
        with open(os.path.join(self.project, name), encoding="utf-8") as fh:
            return fh.read()

    def bullets(self, name):
        return [ln for ln in self.read_md(name).splitlines() if ln.startswith("- ")]


class WriteBoundedMemoryCase(MemoryMdBase):
    def test_no_learnings_file_writes_nothing(self):
        memory_md.write_bounded_memory(self.root)
        self.assertFalse(os.path.isfile(os.path.join(self.project, "MEMORY.md")))

    def test_basic_render_header_and_bullet(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "a", "topic": "build flow", "kind": "pattern",
             "summary": "we fixed the build", "evidence": ["src/foo.py:5"],
             "confidence": "high", "status": "current"},
        ])
        memory_md.write_bounded_memory(self.root)
        md = self.read_md("MEMORY.md")
        self.assertTrue(md.startswith("# Project Memory\n\nGenerated: " + ISO + "\n"))
        self.assertIn("Policy: bounded always-on summary prioritized by use", md)
        self.assertIn("## Always-On Learnings\n\n", md)
        self.assertIn("- [build flow \u00b7 pattern] we fixed the build (evidence: src/foo.py:5)", md)
        self.assertTrue(md.endswith("\n"))

    def test_empty_body_fallback(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "a", "topic": "t", "summary": "s", "status": "superseded"},
        ])
        memory_md.write_bounded_memory(self.root)
        md = self.read_md("MEMORY.md")
        self.assertIn("No publish-safe always-on learnings yet. Use LEARNINGS.jsonl recall on demand.", md)
        self.assertEqual(self.bullets("MEMORY.md"), [])

    def test_filters_status_and_sensitivity(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "ok", "topic": "keep", "summary": "s", "status": "current"},
            {"id": "old", "topic": "drop1", "summary": "s", "status": "superseded"},
            {"id": "sec", "topic": "drop2", "summary": "s", "status": "current", "sensitivity": "security"},
            {"id": "prv", "topic": "drop3", "summary": "s", "status": "current", "sensitivity": "private"},
        ])
        memory_md.write_bounded_memory(self.root)
        bullets = self.bullets("MEMORY.md")
        self.assertEqual(len(bullets), 1)
        self.assertIn("keep", bullets[0])

    def test_sort_confidence_then_recency(self):
        # No usage weighting: high before medium before low; ties by recency (later wins).
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "1", "topic": "low-old", "summary": "s", "confidence": "low", "status": "current"},
            {"id": "2", "topic": "high-mid", "summary": "s", "confidence": "high", "status": "current"},
            {"id": "3", "topic": "med", "summary": "s", "confidence": "medium", "status": "current"},
            {"id": "4", "topic": "high-new", "summary": "s", "confidence": "high", "status": "current"},
        ])
        memory_md.write_bounded_memory(self.root)
        topics = [ln.split("[")[1].split(" \u00b7")[0] for ln in self.bullets("MEMORY.md")]
        self.assertEqual(topics, ["high-new", "high-mid", "med", "low-old"])

    def test_usage_weighting_wins(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "x", "topic": "high-conf", "summary": "s", "confidence": "high", "status": "current"},
            {"id": "y", "topic": "used", "summary": "s", "confidence": "low", "status": "current"},
        ])
        with open(os.path.join(self.project, "MEMORY-USAGE.json"), "w", encoding="utf-8") as fh:
            json.dump({"items": {"learning:y": {"use_count": 9}}}, fh)
        memory_md.write_bounded_memory(self.root)
        first = self.bullets("MEMORY.md")[0]
        self.assertIn("used", first)  # higher use_count outranks higher confidence

    def test_summary_truncated_to_220_chars(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "a", "topic": "t", "summary": "x" * 400, "status": "current"},
        ])
        memory_md.write_bounded_memory(self.root)
        bullet = self.bullets("MEMORY.md")[0]
        self.assertIn("x" * 220 + " (evidence:", bullet)
        self.assertNotIn("x" * 221, bullet)

    def test_evidence_not_verified_when_absent(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "a", "topic": "t", "summary": "s", "status": "current"},
        ])
        memory_md.write_bounded_memory(self.root)
        self.assertIn("(evidence: NOT VERIFIED)", self.bullets("MEMORY.md")[0])

    def test_budget_shrinks_item_count(self):
        rows = [{"id": str(i), "topic": "t%d" % i, "summary": "alpha beta gamma delta",
                 "status": "current"} for i in range(6)]
        self.write_rows("LEARNINGS.jsonl", rows)
        os.environ["KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS"] = "6"
        memory_md.write_bounded_memory(self.root)
        high = len(self.bullets("MEMORY.md"))
        os.environ["KIMIFLOW_MEMORY_BUDGET"] = "60"
        memory_md.write_bounded_memory(self.root)
        low = len(self.bullets("MEMORY.md"))
        self.assertEqual(high, 6)
        self.assertLess(low, 6)
        self.assertGreaterEqual(low, 2)

    def test_max_items_env_validation(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": str(i), "topic": "t%d" % i, "summary": "s", "status": "current"}
            for i in range(12)
        ])
        os.environ["KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS"] = "0"  # -> 8
        memory_md.write_bounded_memory(self.root)
        self.assertEqual(len(self.bullets("MEMORY.md")), 8)
        os.environ["KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS"] = "notnum"  # -> 8
        memory_md.write_bounded_memory(self.root)
        self.assertEqual(len(self.bullets("MEMORY.md")), 8)


class WriteBoundedUserMemoryCase(MemoryMdBase):
    def test_no_user_rows_writes_nothing(self):
        memory_md.write_bounded_user_memory(self.root)
        self.assertFalse(os.path.isfile(os.path.join(self.project, "USER.md")))

    def test_basic_render_and_fallback(self):
        self.write_rows("USER.jsonl", [
            {"id": "a", "topic": "tabs", "summary": "prefers spaces", "status": "current"},
        ])
        memory_md.write_bounded_user_memory(self.root)
        md = self.read_md("USER.md")
        self.assertTrue(md.startswith("# User Profile\n\nGenerated: " + ISO + "\n"))
        self.assertIn("Policy: local-only user/workflow preferences; never publish to repo docs.", md)
        self.assertIn("## Always-On User Notes\n\n", md)
        self.assertIn("- [tabs] prefers spaces (evidence: NOT VERIFIED)", md)

    def test_user_keeps_private_drops_security(self):
        self.write_rows("USER.jsonl", [
            {"id": "p", "topic": "priv", "summary": "s", "status": "current", "sensitivity": "private"},
            {"id": "s", "topic": "sec", "summary": "s", "status": "current", "sensitivity": "security"},
        ])
        memory_md.write_bounded_user_memory(self.root)
        bullets = self.bullets("USER.md")
        self.assertEqual(len(bullets), 1)
        self.assertIn("priv", bullets[0])  # private kept (unlike project memory)

    def test_user_takes_last_n_in_order(self):
        self.write_rows("USER.jsonl", [
            {"id": str(i), "topic": "t%d" % i, "summary": "s", "status": "current"}
            for i in range(10)
        ])
        memory_md.write_bounded_user_memory(self.root)
        topics = [ln.split("[")[1].split("]")[0] for ln in self.bullets("USER.md")]
        self.assertEqual(topics, ["t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9"])

    def test_user_empty_body_fallback(self):
        self.write_rows("USER.jsonl", [
            {"id": "a", "topic": "t", "summary": "s", "status": "superseded"},
        ])
        memory_md.write_bounded_user_memory(self.root)
        self.assertIn("No user-profile notes yet.", self.read_md("USER.md"))


class StoreReadJsonCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_reads_valid_json(self):
        path = os.path.join(self.dir, "a.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"k": 1}')
        self.assertEqual(store.read_json(path), {"k": 1})

    def test_missing_file_returns_default(self):
        self.assertEqual(store.read_json("/no/such.json", default={}), {})

    def test_invalid_json_returns_default(self):
        path = os.path.join(self.dir, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not json")
        self.assertIsNone(store.read_json(path))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_memory_md -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_router.memory_md'` (import error before tests run).

- [ ] **Step 3: Add `store.read_json`**

Append to `hooks/memory_router/store.py` (place it after `read_text`):

```python
def read_json(path, default=None):
    # Lenient single-object JSON read (Bash guards with `jq -e . file`): returns
    # `default` when the file is missing, unreadable, or not valid JSON.
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default
```

- [ ] **Step 4: Write `memory_md.py`**

```python
# hooks/memory_router/memory_md.py
"""Bounded always-on memory markdown renderers: MEMORY.md (project) and USER.md
(user profile). Verbatim behavioral ports of the Bash write_bounded_memory /
write_bounded_user_memory at kimiflow--v0.1.50 (2789-2887): read the JSONL rows,
filter to current + publish-safe, prioritize, truncate to a word budget by
shrinking the item count, and render markdown."""
import os

from . import clock, store


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
    return str(evidence[0]) if evidence else "NOT VERIFIED"


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
    for index, row in enumerate(rows):
        usage_entry = usage.get("learning:" + str(row.get("id", "")))
        use_count = usage_entry.get("use_count", 0) if isinstance(usage_entry, dict) else 0
        entries.append((index, use_count, row))

    iso = clock.iso_now()
    while True:
        selected = [
            entry for entry in entries
            if entry[2].get("status", "current") == "current"
            and entry[2].get("sensitivity", "normal") not in ("security", "private")
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
            row for row in rows
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
```

- [ ] **Step 5: Run the focused tests to verify they pass**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_memory_md -v`
Expected: PASS — 18 tests OK.

- [ ] **Step 6: Run the full package suite (no regression)**

Run: `export PATH="/opt/homebrew/bin:$PATH" && cd hooks && python3 -m unittest discover -s memory_router/tests -p 'test_*.py'`
Expected: all green (103 tests: 85 prior + 18 new). `PATH` exports homebrew so the `contracts` test finds `jq`.

- [ ] **Step 7: Append spec §12 divergence row**

Append to the table in `docs/superpowers/specs/2026-06-28-memory-router-python-cli-design.md` §12:

```
| `write_bounded_memory` / `write_bounded_user_memory` body | `jq -Rsc ... | join("\n")` (`-c`) JSON-encodes the joined string, so MEMORY.md/USER.md render the bullet body as a quoted one-liner with a literal `\n` | renders real newline-separated markdown bullets | The `-c`-quoted body is a latent rendering bug (`-c` where `-r` was intended); the port emits correct markdown. **User-blessed fix (2026-06-29).** The MEMORY.md/USER.md file-parity harness (when these writers are wired, Plan 7/8) must whitelist the body-format difference; the word count for the budget-shrink loop differs marginally as a result. |
```

- [ ] **Step 8: Commit**

```bash
git add hooks/memory_router/memory_md.py hooks/memory_router/tests/test_memory_md.py hooks/memory_router/store.py docs/superpowers/specs/2026-06-28-memory-router-python-cli-design.md
git commit -m "feat(memory_router): bounded always-on memory writers (MEMORY.md / USER.md)"
```

---

## Self-Review

**1. Spec coverage:** both renderers map to Bash 2789-2887 — early-return-on-missing-rows, project usage-weighted sort + security/private filter, user reverse-take + security-only filter, the exact MEMORY.md/USER.md headers and fallbacks, summary[:220] truncation, evidence[0]/NOT VERIFIED, and the budget shrink loop. The one intentional difference (real bullets vs the `jq -c` quoted body) is user-blessed and recorded in §12.

**2. Placeholder scan:** complete code in every step; no TBD/vague items. The `\u00b7` is a byte-stable `\u00b7` escape (no literal non-ASCII in the source).

**3. Parity nuances captured & tested:** (a) project excludes security+private, user excludes only security (private kept) — both tested; (b) usage weighting outranks confidence — tested; (c) confidence→recency tiebreak ordering — tested; (d) user takes the LAST max_items in order — tested; (e) budget shrink reduces item count — tested; (f) max_items env validation (`0`/non-numeric → 8) — tested; (g) summary 220-char truncation, NOT VERIFIED evidence, empty-body fallbacks — tested; (h) `store.read_json` valid/missing/invalid — tested.

**4. Type consistency:** both writers return `None` (side-effecting file writes); `store.read_json(path, default=None)` returns the parsed object or `default`. Rendering uses `clock.iso_now` + `store` helpers only.

## Notes for later plans (not part of this plan)
- **Plan 7/8 wiring:** `record`/`curate`/`review-run` call these after a row write. When the file-parity harness compares `MEMORY.md`/`USER.md`, it must (a) normalize the `Generated:` timestamp and (b) whitelist the body-format divergence (real bullets vs the Bash `jq -c` quoted one-liner).
- The body-format fix could later be back-ported to the Bash (change `-c` to `-r`) to converge, but that is a separate change to the running impl, not part of this port.
