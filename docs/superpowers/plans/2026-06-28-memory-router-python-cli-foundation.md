# memory-router Python CLI — Foundation (Plan 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the stdlib-Python `hooks/memory_router/` package skeleton, the shared IO + jq-faithful serialization modules, and the parity harness — proven end-to-end on the CLI's dispatch/usage layer — so every later per-subsystem port plugs into tested infrastructure.

**Architecture:** A Python package run directly as `python3 hooks/memory_router <args>`. `__main__.py` does argparse-free top-level dispatch (matching the Bash `case`); `contracts.py` centralizes jq-identical JSON output; `store.py` centralizes atomic file IO. The Bash `hooks/memory-router.sh` stays UNCHANGED in this plan (no cutover) — the package is exercised directly and against the pinned old Bash via a parity harness.

**Tech Stack:** Python 3.9+ standard library only (`json`, `os`, `tempfile`, `sys`, `unittest`); Bash + `jq` + `git` for the parity harness; existing kimiflow shell test conventions (`hooks/test-*.sh`).

## Global Constraints

- **Python floor:** 3.9+ (macOS system `python3` is 3.9.6). Stdlib-only — no pip packages, ever.
- **Drop-in contract:** no edits to `SKILL.md`, `reference.md`, manifests, or `hooks/test-memory-router.sh` in this plan. The package is invoked directly; the Bash entrypoint is untouched.
- **Parity source of truth:** the Bash at tag `kimiflow--v0.1.50`, fetched via `git show kimiflow--v0.1.50:hooks/memory-router.sh`.
- **Fidelity:** same contract, fix latent bugs; record every intentional divergence in the spec's §12 table + a code comment + a parity-harness whitelist entry.
- **Commits:** stage only named paths (never `git add -A`/`.`); no co-author / AI-attribution trailer.
- **Tests:** new Python unit tests run under `python3 -m unittest`; new shell tests follow the `hooks/test-*.sh` ok/bad-counter convention so the release loop and CI discover them.

## File Structure

| Path | Responsibility |
|---|---|
| `hooks/memory_router/__init__.py` | Package marker; `__version__`. |
| `hooks/memory_router/__main__.py` | Top-level dispatch, `USAGE` header, exit codes. |
| `hooks/memory_router/contracts.py` | jq-faithful `dumps()` (compact/pretty). |
| `hooks/memory_router/store.py` | Atomic write, text/JSONL readers, symlink guard. |
| `hooks/memory_router/tests/__init__.py` | Test package marker. |
| `hooks/memory_router/tests/test_contracts.py` | `dumps()` vs real `jq` golden parity. |
| `hooks/memory_router/tests/test_store.py` | atomic write, symlink refusal, readers. |
| `hooks/memory_router/tests/test_dispatch.py` | usage/help/unknown exit codes + stderr. |
| `hooks/test-memory-router-parity.sh` | old-Bash vs new-Python differential harness. |

---

### Task 1: Package skeleton + dispatch layer (usage/help/unknown)

**Files:**
- Create: `hooks/memory_router/__init__.py`
- Create: `hooks/memory_router/__main__.py`
- Create: `hooks/memory_router/tests/__init__.py`
- Test: `hooks/memory_router/tests/test_dispatch.py`

**Interfaces:**
- Produces: `memory_router.__main__.main(argv: list[str]) -> int` — argv excludes program name; returns process exit code. `USAGE: str` — the verbatim 17-line Bash header, newline-terminated, written to **stderr**.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# hooks/memory_router/tests/test_dispatch.py
import io, unittest, contextlib
from memory_router.__main__ import main, USAGE

class TestDispatch(unittest.TestCase):
    def _run(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(argv)
        return code, err.getvalue()

    def test_no_args_prints_usage_to_stderr_exit_2(self):
        code, err = self._run([])
        self.assertEqual(code, 2)
        self.assertEqual(err, USAGE)

    def test_help_prints_usage_exit_0(self):
        for flag in ("--help", "-h", "help"):
            code, err = self._run([flag])
            self.assertEqual(code, 0)
            self.assertEqual(err, USAGE)

    def test_unknown_command_prints_usage_exit_2(self):
        code, err = self._run(["bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, USAGE)

    def test_usage_is_17_lines_and_starts_with_shebang(self):
        lines = USAGE.split("\n")
        # 17 content lines + trailing "" from the final newline
        self.assertEqual(len(lines), 18)
        self.assertEqual(lines[0], "#!/usr/bin/env bash")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_dispatch -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_router'` (package not created yet).

- [ ] **Step 3: Create the package marker**

```python
# hooks/memory_router/__init__.py
__version__ = "0.0.0-dev"
```

```python
# hooks/memory_router/tests/__init__.py
```

- [ ] **Step 4: Write the dispatch module**

```python
# hooks/memory_router/__main__.py
"""kimiflow memory-router CLI (Python port). Run: python3 hooks/memory_router <cmd> ..."""
import sys

# Verbatim copy of the Bash header (memory-router.sh lines 1-17). The Bash `usage()`
# does `sed -n '1,17p' "$0" >&2`; after cutover the entrypoint is a shim, so the header
# is embedded here and parity-checked against the pinned old Bash.
USAGE = (
    "#!/usr/bin/env bash\n"
    "# kimiflow — token-cheap local memory router. Orchestrator-invoked, not a hook.\n"
    "#\n"
    "# Usage:\n"
    "#   memory-router.sh status [--root <path>] [--pretty]\n"
    "#   memory-router.sh recall --query <text>|--query-file <path> [--root <path>] [--max <n>] [--write <path>] [--pretty]\n"
    "#   memory-router.sh history [--query <text>|--query-file <path>] [--root <path>] [--max <n>] [--write] [--pretty]\n"
    "#   memory-router.sh metrics [--root <path>] [--global] [--global-purge] [--pretty]\n"
    "#   memory-router.sh classify --input <path>|--text <text> [--pretty]\n"
    "#   memory-router.sh record --summary <text> --topic <topic> --evidence <ref>... [--root <path>] [--kind <kind>] [--scope <scope>] [--confidence <level>] [--sensitivity <level>] [--status <status>]\n"
    "#   memory-router.sh review-run --run <path> [--root <path>] [--write] [--pretty] [--skip <reason>]\n"
    "#   memory-router.sh verify-run --run <path> [--root <path>]\n"
    "#   memory-router.sh curate [--root <path>] [--write] [--pretty]\n"
    "#   memory-router.sh index [--root <path>] [--write] [--pretty]\n"
    "#   memory-router.sh consolidate [--root <path>] [--write] [--pretty]\n"
    "#   memory-router.sh propose [--root <path>] [--write] [--approve <id>] [--reject <id>] [--reason <text>] [--apply] [--pretty]\n"
    "#   memory-router.sh provider <status|health|setup|detect|connect|configure|prefetch|sync> [--root <path>] [--type <obsidian|none>] [--available <true|false>] [--path <path>] [--host <codex|claude|all>] [--pretty]\n"
)

# Subcommand table. Foundation registers none yet; per-subsystem plans add entries
# mapping a command name to a `run(argv: list[str]) -> int` callable.
COMMANDS = {}


def usage(stream=sys.stderr):
    stream.write(USAGE)


def main(argv):
    if not argv:
        usage()
        return 2
    cmd = argv[0]
    if cmd in ("--help", "-h", "help"):
        usage()
        return 0
    handler = COMMANDS.get(cmd)
    if handler is None:
        usage()
        return 2
    return handler(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_dispatch -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Verify it runs as a directory program**

Run: `python3 hooks/memory_router; echo "exit=$?"`
Expected: the 17-line header on stderr, `exit=2`.

- [ ] **Step 7: Commit**

```bash
git add hooks/memory_router/__init__.py hooks/memory_router/__main__.py hooks/memory_router/tests/__init__.py hooks/memory_router/tests/test_dispatch.py
git commit -m "feat(memory_router): package skeleton + dispatch/usage layer"
```

---

### Task 2: `contracts.py` — jq-faithful JSON serialization

**Files:**
- Create: `hooks/memory_router/contracts.py`
- Test: `hooks/memory_router/tests/test_contracts.py`

**Interfaces:**
- Produces: `contracts.dumps(obj, pretty: bool = False) -> str` — compact (no spaces, matching `jq -c .`) or pretty (2-space indent, matching `jq .`); UTF-8 literal (no `\uXXXX`); **no** trailing newline (the caller adds `\n`, mirroring Bash `printf '%s\n'`).
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# hooks/memory_router/tests/test_contracts.py
import json, shutil, subprocess, unittest
from memory_router import contracts

def jq(obj, *args):
    payload = json.dumps(obj)
    out = subprocess.run(["jq", *args, "."], input=payload,
                         capture_output=True, text=True, check=True)
    return out.stdout  # jq appends a trailing newline

SAMPLES = [
    {},
    {"a": 1, "b": True, "c": None, "d": [1, 2, 3]},
    {"nested": {"x": [{"k": "v"}], "ü": "ä/ö"}},
    {"order_b": 2, "order_a": 1},   # insertion order must be preserved verbatim
    [],
    [1, "two", False, None],
]

@unittest.skipUnless(shutil.which("jq"), "jq not installed")
class TestContractsParity(unittest.TestCase):
    def test_compact_matches_jq_c(self):
        for obj in SAMPLES:
            self.assertEqual(contracts.dumps(obj) + "\n", jq(obj, "-c"))

    def test_pretty_matches_jq(self):
        for obj in SAMPLES:
            self.assertEqual(contracts.dumps(obj, pretty=True) + "\n", jq(obj))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_contracts -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_router.contracts'`.

- [ ] **Step 3: Write the implementation**

```python
# hooks/memory_router/contracts.py
"""jq-identical JSON serialization. All stdout JSON in the CLI goes through dumps()."""
import json


def dumps(obj, pretty=False):
    if pretty:
        # jq default pretty: 2-space indent, ", "/": " separators, UTF-8 literals.
        return json.dumps(obj, indent=2, ensure_ascii=False)
    # jq -c compact: no spaces.
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_contracts -v`
Expected: PASS (or SKIP if `jq` absent — install with `brew install jq`).

- [ ] **Step 5: Commit**

```bash
git add hooks/memory_router/contracts.py hooks/memory_router/tests/test_contracts.py
git commit -m "feat(memory_router): jq-faithful JSON serialization (contracts.dumps)"
```

---

### Task 3: `store.py` — atomic write, readers, symlink guard

**Files:**
- Create: `hooks/memory_router/store.py`
- Test: `hooks/memory_router/tests/test_store.py`

**Interfaces:**
- Produces:
  - `store.atomic_write(path: str, data: str, mode: int = 0o644, refuse_symlink: bool = True) -> None` — writes to a `path + ".tmp.XXXXXX"` sibling then `os.replace` (mirrors Bash `mktemp "${file}.tmp.XXXXXX"` + `mv`). Raises `IsADirectoryError`/`OSError` on failure after cleaning the temp; raises `ValueError` if `refuse_symlink` and `path` is an existing symlink.
  - `store.read_text(path: str, default: str = "") -> str`
  - `store.read_jsonl(path: str) -> list` — skips blank/invalid lines (Bash-equivalent leniency).
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# hooks/memory_router/tests/test_store.py
import json, os, tempfile, unittest
from memory_router import store

class TestStore(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_atomic_write_creates_file_with_content(self):
        p = os.path.join(self.d, "out.txt")
        store.atomic_write(p, "hello\n")
        with open(p) as f:
            self.assertEqual(f.read(), "hello\n")

    def test_atomic_write_leaves_no_tmp_siblings(self):
        p = os.path.join(self.d, "out.txt")
        store.atomic_write(p, "x")
        siblings = [n for n in os.listdir(self.d) if n != "out.txt"]
        self.assertEqual(siblings, [])

    def test_atomic_write_refuses_symlink_target(self):
        real = os.path.join(self.d, "real.txt")
        link = os.path.join(self.d, "link.txt")
        store.atomic_write(real, "orig")
        os.symlink(real, link)
        with self.assertRaises(ValueError):
            store.atomic_write(link, "evil")
        with open(real) as f:
            self.assertEqual(f.read(), "orig")  # untouched

    def test_read_text_missing_returns_default(self):
        self.assertEqual(store.read_text(os.path.join(self.d, "nope"), "d"), "d")

    def test_read_jsonl_skips_blank_and_invalid(self):
        p = os.path.join(self.d, "x.jsonl")
        with open(p, "w") as f:
            f.write('{"a":1}\n\n  \nnot json\n{"b":2}\n')
        self.assertEqual(store.read_jsonl(p), [{"a": 1}, {"b": 2}])

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_store -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory_router.store'`.

- [ ] **Step 3: Write the implementation**

```python
# hooks/memory_router/store.py
"""All filesystem IO for the memory-router CLI: atomic writes + lenient readers."""
import json
import os
import tempfile


def atomic_write(path, data, mode=0o644, refuse_symlink=True):
    if refuse_symlink and os.path.islink(path):
        raise ValueError("refusing to write through symlink: %s" % path)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".tmp.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_text(path, default=""):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return default


def read_jsonl(path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hooks && python3 -m unittest memory_router.tests.test_store -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add hooks/memory_router/store.py hooks/memory_router/tests/test_store.py
git commit -m "feat(memory_router): atomic file IO + lenient readers (store)"
```

---

### Task 4: Parity harness — old-Bash vs new-Python differential

**Files:**
- Create: `hooks/test-memory-router-parity.sh`

**Interfaces:**
- Consumes: `python3 hooks/memory_router <args>` (the package) and the pinned old Bash from `git show kimiflow--v0.1.50:hooks/memory-router.sh`.
- Produces: a shell test that prints `ALL GREEN` / exits 0 on full parity, or lists each diverging case and exits 1. Later subsystem plans extend its `CASES` array.

- [ ] **Step 1: Write the failing test (the harness itself, no cases passing yet)**

```bash
#!/usr/bin/env bash
# kimiflow — memory-router parity harness: runs each case through the pinned old Bash
# and the new Python package, normalizes nondeterminism, and diffs stdout+stderr+exit.
# Known-bug divergences are listed in WHITELIST (see spec §12).
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="kimiflow--v0.1.50"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

OLD="$WORK/old-mr.sh"
if ! git -C "$ROOT" show "$TAG:hooks/memory-router.sh" > "$OLD" 2>/dev/null; then
  echo "cannot fetch $TAG:hooks/memory-router.sh — is the tag present?" >&2
  exit 1
fi
chmod +x "$OLD"

FAILS=0
ok()  { printf 'ok   %s\n' "$1"; }
bad() { printf 'BAD  %s\n' "$1"; FAILS=$((FAILS + 1)); }

# Cases the foundation covers: dispatch layer only (no --root state needed).
# Format: "label::arg1|arg2|..."  ('|' separates argv tokens; empty = no args)
CASES=(
  "no_args::"
  "help_long::--help"
  "help_short::-h"
  "help_word::help"
  "unknown_cmd::bogus"
)

normalize() { sed -e "s#$WORK#WORK#g" -e "s#$ROOT#ROOT#g"; }

for entry in "${CASES[@]}"; do
  label="${entry%%::*}"; argstr="${entry#*::}"
  args=(); [ -n "$argstr" ] && IFS='|' read -r -a args <<< "$argstr"

  o_out="$(bash "$OLD" "${args[@]}" 2>"$WORK/o.err")"; o_code=$?
  o_err="$(normalize < "$WORK/o.err")"
  n_out="$(python3 "$ROOT/hooks/memory_router" "${args[@]}" 2>"$WORK/n.err")"; n_code=$?
  n_err="$(normalize < "$WORK/n.err")"

  if [ "$o_code" = "$n_code" ] && [ "$o_out" = "$n_out" ] && [ "$o_err" = "$n_err" ]; then
    ok "$label"
  else
    bad "$label (exit $o_code/$n_code)"
  fi
done

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS DIVERGENCES"; exit 1; fi
```

- [ ] **Step 2: Make executable and run to verify current state**

Run: `chmod +x hooks/test-memory-router-parity.sh && bash hooks/test-memory-router-parity.sh`
Expected: `ALL GREEN`, exit 0. (Tasks 1–3 already make the dispatch layer match the old Bash's usage/help/unknown behavior.)
If any case is `BAD`: the old Bash differs from our Python on the dispatch layer — inspect the diverging stream, and either correct `__main__.py` (if the Python is wrong) or add the case to a `WHITELIST` with a spec §12 entry (if it is a Bash bug we intentionally fixed).

- [ ] **Step 3: Commit**

```bash
git add hooks/test-memory-router-parity.sh
git commit -m "test(memory_router): old-Bash vs Python parity harness (dispatch layer)"
```

---

## Self-Review

**1. Spec coverage (foundation slice of the spec):**
- §6 architecture (package + `__main__`/`contracts`/`store`) → Tasks 1–3. ✓
- §4.2 jq stdout formatting → Task 2 (compact + pretty parity vs real jq). ✓
- §8 atomic writes + symlink guard → Task 3. ✓
- §7.1 parity harness (pinned old Bash, normalized diff, whitelist hook) → Task 4. ✓
- §4.1 dispatch/usage/exit-codes → Task 1. ✓
- Deferred to later plans (correctly out of this plan's scope): per-subcommand ports (`status`, `recall`, `classify`, `review-run`/`verify-run`, `metrics`, `provider`, `propose`/`consolidate`/`curate`/`index`, `record`/`history`), the sqlite/FTS layer (§8), hashing/metrics (§8), the shim cutover + docs + release (§9). Each becomes its own plan.

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Every code/test step contains complete content. The empty `COMMANDS = {}` and the empty `tests/__init__.py` are intentional, complete artifacts (a registry later tasks append to; a package marker), not placeholders. ✓

**3. Type consistency:** `main(argv) -> int`, `USAGE: str`, `contracts.dumps(obj, pretty=False) -> str`, `store.atomic_write/read_text/read_jsonl` — names and signatures used in tests match the implementations exactly across Tasks 1–4. The parity harness `CASES`/`WHITELIST` names match what later plans extend. ✓

## Notes for later plans (not part of this plan)

- **`status` is the natural first subsystem plan** — it exercises `contracts.dumps`, `store` readers, and adds the first `COMMANDS` entry + the first stateful parity fixtures (`--root` with/without `.kimiflow/project`).
- **sqlite/FTS5** belongs to the `recall` plan; verify `python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')"` succeeds on the target interpreters and replicate the Bash graceful-degradation when FTS5 is unavailable.
- The cutover plan replaces `hooks/memory-router.sh` with the shim and updates `README.md`/`COMPATIBILITY.md` (Python ≥3.9) + `CHANGELOG.md`, then runs `/release`.
