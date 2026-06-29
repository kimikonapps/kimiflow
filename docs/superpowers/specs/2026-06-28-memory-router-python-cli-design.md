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
hooks/memory-router.sh            # ~8-line shim: exec env PYTHONPATH=... python3 -m memory_router "$@"
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

- **Shim:** resolve own dir, `exec env PYTHONPATH="$dir" python3 -m memory_router "$@"`. If `python3` is
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
| `memory_security_json` hidden_unicode + `file_digest_json` | gated on `command -v perl` / falls back `shasum`→`sha256sum`→`cksum`→`unavailable` | Python stdlib always scans for hidden unicode and always hashes with `hashlib.sha256` | Stdlib is strictly more capable than shelling to perl/shasum; identical on targets that have them (the harness host does), so no diff. The Bash cksum/unavailable/perl-absent branches are unreachable on supported targets. |
| `write_bounded_memory` / `write_bounded_user_memory` body | `jq -Rsc ... | join("\n")` (`-c`) JSON-encodes the joined string, so MEMORY.md/USER.md render the bullet body as a quoted one-liner with a literal `\n` | renders real newline-separated markdown bullets | The `-c`-quoted body is a latent rendering bug (`-c` where `-r` was intended); the port emits correct markdown. **User-blessed fix (2026-06-29).** The MEMORY.md/USER.md file-parity harness (when these writers are wired, Plan 7/8) must whitelist the body-format difference; the word count for the budget-shrink loop differs marginally as a result. |
| RECALL.sqlite engine (`sqlite_available` / `insert_fts_row` / `fts_hits_json`) | gates on `command -v sqlite3` (CLI), shells out per op with `sql_quote` string interpolation | uses the stdlib `sqlite3` module: probes FTS5 availability and binds parameters | The module is always importable, so the port probes FTS5 (catches a missing-FTS5 build) and degrades to `[]` when absent; parameter binding replaces `sql_quote` (equivalent, no quoting bugs). The module's bundled sqlite version may differ from the system CLI, so FTS5 tokenization/ranking could differ at the margin; parity verified on the harness host (default unicode61 tokenizer; simple quoted-OR queries). |
| `build_recall_index` run-artifact order + malformed rows | `find` emits run-artifact files in filesystem (readdir) order; jq parses each JSONL line and an unparseable line yields empty fields (FACTS would still insert an all-empty row) | run-artifact paths are **sorted** before insertion; `store.read_jsonl` **skips** unparseable lines entirely | `find`'s order is undefined across hosts, so a stable sort is chosen for reproducible indexes (observable only via `fts_hits_json` LIMIT, which has no ORDER BY - Bash is non-deterministic there too). Skipping malformed JSONL is the project-wide `read_jsonl` convention (Plans 4/5); it only diverges for FACTS, where Bash has no status filter to drop the garbage row. jq `//` null/false handling, jq-1.7 number-literal preservation (`7.0` → `"7.0"`), and `sed`'s newline handling (CRLF/bare-CR kept verbatim via a `newline=""` read) are **replicated** (`_jq_or` + `str` + `_read_body`), so they are not divergences. |
| `resolve_root` + `need_jq` (cmd_index wiring) | `(cd "$root" && pwd)` (shells out; logical pwd) with git `rev-parse --show-toplevel`/`pwd` fallback; `need_jq` dies if jq absent | `os.path.abspath(root)` when `os.path.isdir(root)` else literal, with a `git rev-parse` subprocess / logical-cwd (`$PWD`-validated) fallback; `need_jq` is a no-op | Both branches keep symlinks unresolved to match bash's *logical* path handling: `os.path.abspath` matches `cd && pwd` for real roots (normalizes `..` lexically, keeps symlinks), and the no-`--root` fallback uses `$PWD` when it still names the cwd (mirroring bare `pwd -L`) instead of the symlink-resolving `os.getcwd()`. The only edge is a `--root` that exists-but-`cd`-fails (e.g. no-exec permission), unobservable for normal roots. The port needs no jq (engine uses the stdlib sqlite3 module), so `need_jq` is dropped for every ported subcommand (generalizes the classify-jq row). |
| `usage_summary_json` non-object input | `jq -e .` passes for a valid non-object top level (e.g. `[1,2]`), then `.items` errors -> empty stdout (breaks the `--argjson` caller) | returns the absent shape | The port treats any non-dict parse as absent (more robust); `MEMORY-USAGE.json` is always an object, so this is unreachable. Missing / invalid / top-level null|false already map to absent in both. |
| `economics_summary_json` `_n` sci-notation strings | jq `tonumber` on a scientific-notation string (e.g. `"1e3"`) yields a number rendered `"1E+3"` | Python parses to `1000.0` -> json `"1000.0"` | Unreachable: `MEMORY-ECONOMICS.jsonl` fields are JSON numbers, not strings. `_n` matches jq for all real inputs (ints, floats, plain int/float strings, whitespace-padded); only exotic sci-notation/underscore strings differ. |
| `global_efficiency_summary_json` totals (`_jq_sum`) | jq `[...] \| add // 0` returns a single-element list verbatim (`[5.0] \| add -> 5.0`, literal preserved) but renders a computed multi-element sum canonically, collapsing an integral float to an int (`[-5.5,2.5] \| add -> -3`) | `_jq_sum` replicates both: empty -> `0`, single element verbatim, 2+ elements summed then integral-float -> int | **Replicated, not a divergence** - sharpens the general `contracts.dumps` numbers row for the aggregator's computed sums (verified vs `jq -cn`). Reachable only with float fields, which real token telemetry never has; the stored fields are summed directly with `// 0` and no `tonumber` (the Bash `def n` at line 533 is dead code). |
| `learning_lifecycle_json` / `learning_usefulness_json` non-object usage (`_usage_items`) | a valid non-object `MEMORY-USAGE.json` top level (Bash passes `jq -e .` then errors on `.items`), or a `false`/number/array usage ENTRY (jq errors on e.g. `false.use_count`), makes the Bash function emit empty output | `_usage_items` falls back to `{}` for any non-object top level / `.items`, and a non-object entry yields `use_count` 0 | Unreachable: `MEMORY-USAGE.json` is always an object whose `.items` is an object of item-objects. The port stays robust (valid result) where Bash crashes; same class as the `usage_summary_json` non-object row. All reachable usage shapes (object / missing / null / false / `{}` / items-null) match byte-for-byte. |
| `provider_detection_json` / `provider_auth_json` curl gate | `command -v curl` absent -> detection returns `status:"unavailable", reason:"curl_unavailable"`; the auth token probe is blocked with `probe_blocked_reason:"curl_unavailable"` | the port always probes via stdlib `urllib`, so it never emits the `curl_unavailable` shape/reason | Generalizes the jq/sqlite stdlib rows: `urllib` is always importable, so the curl-absent branch is unreachable. On any host with curl (the harness host) there is no diff. |
| `provider.py` TLS verification + redirects (`_http_probe` / `_ssl_context` / `_NoRedirect`) | `curl -k` (no `-L`) skips TLS verification for the local self-signed Obsidian REST API and does NOT follow redirects | `urllib` with `ssl.CERT_NONE` (verification disabled) + a `_NoRedirect` opener so 3xx is terminal | **Replicated, not a divergence.** Faithful to `curl -k`; the probe targets ONLY a `_normalize_loopback_origin`-validated loopback host AND never follows a redirect, so the bearer token never leaves the machine (two compensating controls for the disabled verification). Default urllib WOULD follow a 3xx and re-send `Authorization` cross-origin (token leak); `_NoRedirect` prevents that and restores `curl`'s 3xx -> `token_unverified` parity. Flagged by the security hook; intentional for the local self-signed cert. |
| `provider_manifest_json` non-object / `provider_detection_json` timeout | Bash `jq '.'` returns a valid non-object manifest verbatim (callers then error on `.type`); a non-numeric `KIMIFLOW_OBSIDIAN_DETECT_TIMEOUT` is passed to curl, which errors -> probe fails | `manifest_json` returns the default manifest for any non-dict; `_detect_timeout` falls back to `0.35` for a non-numeric value | Unreachable: `VAULT-PROVIDER.json` is always an object and the timeout is a numeric config. The port stays robust; reachable manifest/timeout inputs match byte-for-byte. |
