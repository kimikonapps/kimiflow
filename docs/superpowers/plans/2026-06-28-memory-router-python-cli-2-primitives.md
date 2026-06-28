# memory-router Python CLI — Plan 2: shared row/path/text primitives

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the small, deterministic, pure helper functions that the write/index/recall layer all depend on — path resolution, slugify, SQL quoting, word counting, and UTC clock formatting — as tested package modules, so the larger `append_learning_row` / index / sqlite / curate plans compose them.

**Architecture:** These are internal helpers (no subcommand), so this plan is verified by Python unit tests only — there are no new parity-harness cases yet (parity arrives when `record`/`status` wire these in). Each helper is a verbatim behavioral port of its Bash counterpart at `kimiflow--v0.1.50`.

**Tech Stack:** Python 3.9+ stdlib only (`re`, `datetime`); no new third-party deps.

## Global Constraints

- **Python floor:** 3.9+, stdlib-only.
- **Drop-in / scope:** no edits to `hooks/memory-router.sh`, SKILL.md, reference.md, manifests, existing tests, or other existing package modules beyond adding the three new modules + their tests. No subcommand wiring in this plan.
- **Source of truth:** Bash @ `kimiflow--v0.1.50` — `rel_path` (2514-2521), `rows_path_for_scope` (2389-2395), `id_prefix_for_scope` (2397-2403), `slugify` (2221-2227), `sql_quote` (2522-2524), `word_count_file` (52-58), `iso_now`/`date_now` (44-49).
- **ASCII-locale fidelity:** Bash `tr '[:alnum:]'`/`[:upper:]` run in the C-ish locale → ASCII classes. The Python ports use explicit ASCII (`[^a-z0-9]`, `str.lower()` after which only ASCII matters) to match.
- **Commits:** named paths only (no `git add -A`); no co-author / AI-attribution trailer.
- **Branch:** continue on `feat/memory-router-py-foundation`.

## File Structure

| Path | Responsibility |
|---|---|
| `hooks/memory_router/paths.py` | `rel_path`, `rows_path_for_scope`, `id_prefix_for_scope`. |
| `hooks/memory_router/text.py` | `slugify`, `sql_quote`, `word_count_file`. |
| `hooks/memory_router/clock.py` | `iso_now`, `date_now` (UTC, nondeterministic — format-tested). |
| `hooks/memory_router/tests/test_primitives.py` | unit tests for all of the above. |

---

### Task 1: row/path/text/clock primitives

**Files:**
- Create: `hooks/memory_router/paths.py`, `hooks/memory_router/text.py`, `hooks/memory_router/clock.py`
- Test: `hooks/memory_router/tests/test_primitives.py`

**Interfaces (produced; later plans consume):**
- `paths.rel_path(root: str, path: str) -> str`
- `paths.rows_path_for_scope(root: str, scope: str) -> str`
- `paths.id_prefix_for_scope(scope: str) -> str`
- `text.slugify(s: str) -> str`
- `text.sql_quote(s: str) -> str`
- `text.word_count_file(path: str) -> int`
- `clock.iso_now() -> str` (`YYYY-MM-DDTHH:MM:SSZ`), `clock.date_now() -> str` (`YYYY-MM-DD`)

- [ ] **Step 1: Write the failing tests**

```python
# hooks/memory_router/tests/test_primitives.py
import os, re, tempfile, unittest
from memory_router import paths, text, clock

class TestPaths(unittest.TestCase):
    def test_rel_path_strips_root_prefix(self):
        self.assertEqual(paths.rel_path("/a/b", "/a/b/c/d.txt"), "c/d.txt")
    def test_rel_path_equal_root_is_dot(self):
        self.assertEqual(paths.rel_path("/a/b", "/a/b"), ".")
    def test_rel_path_outside_root_unchanged(self):
        self.assertEqual(paths.rel_path("/a/b", "/x/y"), "/x/y")
    def test_rows_path_user_and_default(self):
        self.assertEqual(paths.rows_path_for_scope("/r", "user"), "/r/.kimiflow/project/USER.jsonl")
        self.assertEqual(paths.rows_path_for_scope("/r", "profile"), "/r/.kimiflow/project/USER.jsonl")
        self.assertEqual(paths.rows_path_for_scope("/r", "project"), "/r/.kimiflow/project/LEARNINGS.jsonl")
        self.assertEqual(paths.rows_path_for_scope("/r", "anything"), "/r/.kimiflow/project/LEARNINGS.jsonl")
    def test_id_prefix(self):
        self.assertEqual(paths.id_prefix_for_scope("user"), "user")
        self.assertEqual(paths.id_prefix_for_scope("profile"), "user")
        self.assertEqual(paths.id_prefix_for_scope("project"), "learn")

class TestText(unittest.TestCase):
    def test_slugify_basic(self):
        self.assertEqual(text.slugify("My Topic!"), "my-topic")
    def test_slugify_collapses_and_trims(self):
        self.assertEqual(text.slugify("  --Hello___World--  "), "hello-world")
    def test_slugify_truncates_40(self):
        self.assertEqual(text.slugify("a" * 50), "a" * 40)
    def test_slugify_all_nonalnum_is_empty(self):
        self.assertEqual(text.slugify("!!!"), "")
    def test_sql_quote_doubles_single_quotes(self):
        self.assertEqual(text.sql_quote("it's a 'test'"), "it''s a ''test''")
    def test_word_count_file(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "f.txt")
        with open(p, "w") as f:
            f.write("one two   three\nfour\n")
        self.assertEqual(text.word_count_file(p), 4)
    def test_word_count_missing_is_zero(self):
        self.assertEqual(text.word_count_file("/no/such/file"), 0)

class TestClock(unittest.TestCase):
    def test_iso_now_format(self):
        self.assertRegex(clock.iso_now(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    def test_date_now_format(self):
        self.assertRegex(clock.date_now(), r"^\d{4}-\d{2}-\d{2}$")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_primitives -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_router.paths'`.

- [ ] **Step 3: Write `paths.py`**

```python
# hooks/memory_router/paths.py
"""Path/scope helpers (verbatim ports of the Bash rel_path / *_for_scope)."""


def rel_path(root, path):
    if path == root:
        return "."
    prefix = root + "/"
    if path.startswith(prefix):
        return path[len(prefix):]
    return path


def rows_path_for_scope(root, scope):
    if scope in ("user", "profile"):
        return "%s/.kimiflow/project/USER.jsonl" % root
    return "%s/.kimiflow/project/LEARNINGS.jsonl" % root


def id_prefix_for_scope(scope):
    if scope in ("user", "profile"):
        return "user"
    return "learn"
```

- [ ] **Step 4: Write `text.py`**

```python
# hooks/memory_router/text.py
"""Text helpers (verbatim ports of Bash slugify / sql_quote / word_count_file)."""
import re

# Bash: tr '[:upper:]' '[:lower:]' | tr -cs '[:alnum:]' '-' | sed strip/collapse | cut -c1-40.
# In the C-ish locale [:alnum:] is ASCII, so match with [^a-z0-9] after str.lower().
_NONALNUM = re.compile(r"[^a-z0-9]+")


def slugify(s):
    s = _NONALNUM.sub("-", s.lower())   # runs of non-alnum -> single '-'
    s = s.strip("-")                    # sed 's/^-//; s/-$//'
    return s[:40]                       # cut -c1-40


def sql_quote(s):
    return s.replace("'", "''")


def word_count_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return len(handle.read().split())
    except OSError:
        return 0
```

- [ ] **Step 5: Write `clock.py`**

```python
# hooks/memory_router/clock.py
"""UTC clock helpers (ports of Bash iso_now / date_now). Nondeterministic by nature."""
from datetime import datetime, timezone


def iso_now():
    # Bash: date -u +"%Y-%m-%dT%H:%M:%SZ"
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def date_now():
    # Bash: date -u +"%Y-%m-%d"
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_primitives -v`
Expected: PASS. Then the full package suite to confirm no regression:
`cd hooks && python3 -m unittest discover -s memory_router/tests -p 'test_*.py'`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add hooks/memory_router/paths.py hooks/memory_router/text.py hooks/memory_router/clock.py hooks/memory_router/tests/test_primitives.py
git commit -m "feat(memory_router): shared row/path/text/clock primitives"
```

---

## Self-Review

**1. Spec coverage:** the seven helpers map verbatim to their Bash sources (cited per function). No subcommand is touched (correct — these are internal building blocks; the spec's drop-in contract is preserved since nothing user-visible changes).

**2. Placeholder scan:** complete code in every step; no TBD/vague items.

**3. Type consistency:** module/function names match the Interfaces block and are the names the later `append_learning_row` / index / curate plans will import. `word_count_file` returns `int` (Bash prints a numeric string; callers feed it to JSON as a number — `int` is the faithful in-process representation; `contracts.dumps` will render it as an integer later).

## Notes for later plans (not part of this plan)
- Next shared-layer pieces, smallest-first: (a) `memory_security_json` + `sanitize_evidence_json` + `evidence_fingerprints_json` (row-validation helpers append needs); (b) `append_learning_row` write path (dedup + supersession; nondeterministic `id`/`last_verified`/`source_commit` — parity must normalize these); (c) `MEMORY-INDEX.json` builder + `write_bounded_memory`; (d) `RECALL.sqlite`/FTS5 layer (hardest parity); (e) `curate` composition; then thin `record`/`index`/`curate` subcommand wiring.
- `clock.iso_now`/`date_now` are nondeterministic; any subcommand that emits them needs harness normalization (like the existing path normalization).
