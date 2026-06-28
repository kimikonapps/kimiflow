# Design: port `memory-router.sh` to a stdlib Python CLI

- **Date:** 2026-06-28
- **Status:** approved design, pre-plan
- **Author:** kimiflow maintainer
- **Baseline:** `hooks/memory-router.sh` @ tag `kimiflow--v0.1.50` (4403 lines, 116 functions)

## 1. Problem & motivation

`hooks/memory-router.sh` is the single largest, most logic-dense unit in kimiflow:
memory recall, learning classification, sqlite-FTS indexing, JSON serialization,
provider/Vault auth, metric hashing, the learning lifecycle, and proposals — ~4400
lines of Bash. Bash is notoriously fragile for string/quoting/edge-case handling at
that size (the SC2318 latent-bug class already shipped here). It is the maintenance and
correctness liability flagged as the highest-leverage item in the project review.

It uses external CLIs `jq` (365×), `sqlite3` (7×), `openssl` (2×), plus `awk`/`sed`.
Every one of these maps to the Python standard library (`json`, `sqlite3`,
`hashlib`/`secrets`, native string ops) — so the port needs **no third-party packages**.

## 2. Goals / non-goals

### Goals
- Replace the Bash implementation with a stdlib-only Python CLI (Python ≥ 3.9).
- Break the monolith into independently understandable, independently testable modules.
- Fix latent bugs surfaced during the port (the chosen fidelity policy, §5).
- Keep the public contract a **drop-in**: no edits required to `SKILL.md`, `reference.md`,
  the manifests, or the existing `hooks/test-memory-router.sh`.

### Non-goals (YAGNI / scope fences)
- **No** new features, subcommands, flags, or output/file-format changes.
- **No** refactor of other hooks. `jq` stays a project dependency (other hooks —
  `commit-secret-gate`, `test-gate` — require it); removing jq project-wide is out of scope.
- **No** change to the `MR = …/memory-router.sh` invocation path.

## 3. Strategy (decided)

- **Big-bang rewrite, single cutover.** Build the full Python CLI, swap the entrypoint
  once, delete the Bash. (Chosen over incremental strangler / subset.)
- De-risked by a **parity harness** (§7) that diffs old-Bash vs new-Python output on a
  fixture battery, plus the existing test suite as an integration oracle. The swap is only
  safe once parity-harness + `test-memory-router.sh` + both smokes are green.

## 4. Public contract (source of truth = the Bash @ v0.1.50)

The Python CLI MUST reproduce, byte-for-byte where consumed as text:

### 4.1 Subcommands (top-level dispatch)
`status`, `recall`, `history`, `metrics`, `classify`, `record`, `review-run`,
`verify-run`, `curate`, `index`, `consolidate`, `propose`, `provider`, and
`help`/`--help`/`-h` (exit 0). Unknown/empty command → `usage` + exit 2.

- `provider` sub-dispatch: `status`, `health`, `setup`, `detect`, `connect`, `configure`,
  `prefetch`, `sync`, `auth` (per the Bash `cmd_provider` case).
- `propose` flags: `--approve <id>`, `--reject <id> --reason`, `--apply`, `--write`.
- Common flags across commands: `--run`, `--query`, `--query-file`, `--write`, `--skip`,
  `--scope`, `--root`, `--pretty`, `--reason`. (Enumerated per-command from the Bash during
  the plan; the Bash is authoritative.)

### 4.2 stdout
- JSON outputs match the Bash `jq` formatting exactly: `jq -c` sites → compact
  (`separators=(",", ":")`); key ordering as the jq programs emit it; `jq -r` raw strings
  unquoted; booleans/null/integers formatted as jq does. Centralized in `contracts.py`.
- TSV/line outputs match column order, separators, and trailing-newline behavior.

### 4.3 Exit codes
Same code per outcome as the Bash (e.g. `verify-run` CLOSED, `review-run` quality-gate
block, malformed-args = 2). Captured per-command in the parity harness.

### 4.4 Files read (inputs, not written)
Run artifacts: `INTENT.md`, `PROBLEM.md`, `AUDIT-INTENT.md`, `PLAN.md`, `ACCEPTANCE.md`,
`STATE.md`, `DIAGNOSIS.md`, `RESEARCH.md`, `REVIEW.md`, `CODE-REVIEW.md`, `ADVISORIES.md`,
`AUDIT.md`, `VERIFICATION.md`, `CURRENT-STATE.md`, plus project `STANDARDS.md`/`DECISIONS.md`.

### 4.5 Files written (outputs — formats preserved exactly)
- Project memory: `LEARNINGS.jsonl`, `FACTS.jsonl`, `MEMORY.md`, `MEMORY-INDEX.json`,
  `MEMORY-USAGE.json`, `MEMORY-ECONOMICS.jsonl`, `RECALL.sqlite`, `PROPOSALS.jsonl`,
  `PENDING-PROPOSALS.md`, `USER.jsonl`, `USER.md`, `VAULT-PROVIDER.json`,
  `VAULT-PREFETCH.md`, `VAULT-SYNC.md`.
- Run-local: `LEARNING-REVIEW.md`, `RUN-LIFECYCLE.{json,md}`, `RUN-HISTORY.{json,md}`,
  `RECALL.{json,md}`.
- Global: `~/.kimiflow/metrics/token-economics.jsonl`, salt file under `~/.kimiflow/metrics/`.

### 4.6 Environment variables (honored identically)
`KIMIFLOW_GLOBAL_METRICS`, `KIMIFLOW_PROVIDER_SYNC_MAX`, `KIMIFLOW_*_MCP_AVAILABLE`,
`KIMIFLOW_HOST`, `KIMIFLOW_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT`/`CLAUDE_SKILL_DIR`.

## 5. Fidelity policy (decided): same contract, fix bugs

Drop-in contract, but latent bugs are corrected during the port. Every intentional
divergence from the Bash output is recorded in three places: a **"Known parity
divergences"** table in this spec (appended as found), a code comment at the site, and a
whitelist entry in the parity harness so the diff stays meaningful. No silent drift.

## 6. Architecture (chosen: subsystem-modular package)

```
hooks/memory-router.sh            # ~8-line shim: exec python3 .../memory_router "$@"
hooks/memory_router/
  __main__.py                     # argparse dispatch, exit codes, top-level error handling
  status.py recall.py history.py  # one module per subsystem
  metrics.py classify.py record.py
  review.py  verify.py            # lifecycle: review-run / verify-run / run-lifecycle
  curate.py  index.py consolidate.py propose.py
  provider.py                     # vault detect/health/setup/connect/configure/prefetch/sync/auth
  store.py                        # ALL file + sqlite IO: atomic write (tmp+rename),
                                  #   symlink guard, chmod bits, JSONL/MD/sqlite readers+writers
  contracts.py                    # jq-faithful JSON/TSV serialization + quality-gate predicates
  __init__.py
```

- **Shim:** resolve own dir, `exec python3 "$dir/memory_router" "$@"`. If `python3` is
  absent → clear stderr install hint + exit 1 (gates/orchestrator already treat non-zero as
  failure; this preserves the Bash's fail-closed posture).
- Each module exposes one `run(args) -> int` (or pure helpers) and depends only on `store.py`
  + `contracts.py`. Target < ~400 lines/module.
- **No** module reads another subsystem's internals; cross-cutting IO and formatting live in
  the two shared modules so a subsystem can be understood and tested in isolation.

## 7. Test strategy (de-risk the big-bang)

1. **Parity harness** (`hooks/test-memory-router-parity.sh` + a Python driver): copy the
   old Bash from `git show kimiflow--v0.1.50:hooks/memory-router.sh` into a temp path; for a
   fixture battery of synthetic `.kimiflow/<slug>` run dirs and project dirs, run **every**
   subcommand/flag combo through both old-Bash and new-Python; diff stdout + exit code + all
   resulting files. Normalize nondeterminism (timestamps, per-machine salt, absolute paths)
   before diffing. Known-bug divergences (§5) are whitelisted with a reason.
2. **`hooks/test-memory-router.sh`** (672 lines) stays green — it now drives Python through
   the shim and is the integration oracle.
3. **Python unit tests** via stdlib `unittest` (zero dependency, matches the doctrine) for
   pure logic: classification, quality gates, contract formatting, sqlite schema/queries,
   hashing.
4. Wire all of the above into `.github/workflows/ci.yml` and the release skill's
   `hooks/test-*.sh` loop (it already discovers new `test-*.sh`).

## 8. Data/IO parity details

- **sqlite (`RECALL.sqlite`):** reproduce the FTS schema + queries 1:1 via the `sqlite3`
  module. The schema string and each query are lifted verbatim from the Bash.
- **Hashing/metrics:** same algorithm + input construction as the Bash
  (`openssl dgst -sha256` → `hashlib.sha256`; salt via `secrets`, stored in the same file with
  the same permissions). The salt value is per-machine random in both; only the algorithm and
  the hashed-input layout must match so anonymized rows stay consistent across the cutover.
- **Atomic writes:** every writer writes a temp sibling then `os.replace` (mirrors the Bash
  `tmp.$$` + `mv`), keeps the symlink guard (`[ ! -L ]` → `os.path.islink` check), and the
  `chmod 700` on the salt dir.

## 9. Cutover & docs

1. Build `hooks/memory_router/` alongside the existing Bash.
2. Parity harness green → replace `hooks/memory-router.sh` body with the shim; delete the
   Bash logic (recoverable from git/tag for the harness).
3. `test-memory-router.sh` + both smokes + full `test-*.sh` loop green.
4. Update `README.md` + `COMPATIBILITY.md` dependency section to state **Python ≥ 3.9
   required**; add a `CHANGELOG.md` `### Changed` entry.
5. Ship as a version bump via the `/release` skill.

## 10. Python floor & doctrine

Target **Python 3.9+** (the macOS system `python3` is 3.9.6; all modern installs satisfy it),
**stdlib-only**. This adds a Python runtime to kimiflow's dependency surface (documented in
COMPATIBILITY); it does **not** add any pip package, and does not remove `jq` (still needed by
other hooks).

## 11. Risks (honest)

- **jq output-format parity** is the chief hazard (365 call sites) — mitigated by centralizing
  formatting in `contracts.py` and the byte-diff parity harness.
- sqlite-FTS behavior, hash-input layout, integer/locale formatting, and `usage`/error text
  must all match. The parity battery must be broad enough to exercise each.
- This is a **large** effort — realistically a multi-task plan across several sessions, not a
  single sitting. The cutover is gated on green parity + suite + smokes.

## 12. Known parity divergences (append as found)

| Site (Bash ref) | Old behavior | New behavior | Reason |
|---|---|---|---|
| `contracts.dumps` numbers | `jq` emits `1` for integer-valued floats (e.g. `1.0 → 1`) and normalizes number precision | Python `json.dumps(1.0)` → `"1.0"` | Must add float/number parity coverage and handling before first numeric-stdout subcommand (metrics/economics) ships; no current foundation path emits non-integer numbers so this is safe to defer |
| `classify` when `jq` absent (`need_jq`) | dies `memory-router: jq is required` exit 2 | classifies normally (no jq needed) | Python uses no jq; jq-requirement was a Bash impl artifact, not a user contract. Harness runs with jq present, so no diff. |
