# memory-router Python CLI - Plan 22: `verify-run` subcommand (+ `resolve_run_dir`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Port `cmd_verify_run` (Bash 3417-3505) - the learning-review verification gate. It reads a run's `LEARNING-REVIEW.md`, and depending on `Status:` emits a single tab-separated `LEARNING_REVIEW\t<OPEN|CLOSED>\t...` line with exit **0** (OPEN) or **1** (CLOSED). Also ports the shared `resolve_run_dir` (2646-2654), reused by `review-run` later. Self-contained: the only other dependency, `evidence_fingerprints_json`, is already ported (rows.py).

**Architecture:** New module `hooks/memory_router/runs.py` with `run(argv)` + `resolve_run_dir(root, run)`. Registered in `__main__.COMMANDS`. Reuses `rows.evidence_fingerprints_json`, `store.read_jsonl`, `paths.rel_path`, `contracts.dumps`.

**Grounding finding (resolve_run_dir subshell-die quirk):** Bash always calls `resolve_run_dir` inside `$( )` (3298/3431), so its `die ... 2` kills only the **subshell** — the message reaches stderr, but the exit code is **discarded** and the caller gets an **empty** `run_dir`. verify-run then builds `review="$run_dir/LEARNING-REVIEW.md"` = `/LEARNING-REVIEW.md` and emits `missing_review` + exit **1** (NOT a clean exit 2). The port **replicates** this: `resolve_run_dir` writes the die line to stderr and returns `""`; the review path uses string concat (so empty -> `/LEARNING-REVIEW.md`). spec 12 row added. (An earlier `DieError`/exit-2 design was rejected after grounding showed the divergence.)

**Tech Stack:** Python 3.9+ stdlib only (`os`, `re`, `sys`). No new deps.

## Global Constraints

- **Drop-in / scope:** new `runs.py`, `tests/test_runs.py`; `cli.py` += `DieError`; `__main__.py` += `"verify-run": runs.run`. No edits to `hooks/memory-router.sh`, manifests. **No new §12 row.**
- **Source of truth:** Bash `cmd_verify_run` (3417-3505) + `resolve_run_dir` (2646-2654) @ `kimiflow--v0.1.50`. Ground byte-for-byte (whole real Bash vs Python CLI, isolated `env -i`).
- **stdout is TEXT, not JSON** (tab-separated `LEARNING_REVIEW` lines); use `sys.stdout.write`. Exit codes: 0 (OPEN), 1 (CLOSED), 2 (arg/run errors).

### `resolve_run_dir(root, run)` (Bash 2646-2654)
Empty `run` -> `raise DieError("run path required", 2)`. Prefix `root + "/" + run` when not absolute. Must be an existing dir (Bash `(cd && pwd)`) else `raise DieError("run directory not found: <run>", 2)`; return `os.path.abspath(run)` (logical abs, symlinks kept - matches the `resolve_root` convention).

### `run(argv)` (Bash 3417-3505)
- Args `--root`/`--run`/`--help`/`-h`/unknown->`die("verify-run: unknown argument: <a>", 2)`. `need_jq` no-op. `root = resolve_root(root)`. `run_dir = resolve_run_dir(root, run)` (catch `DieError` -> `die`). `review = run_dir/LEARNING-REVIEW.md`.
- Missing `review` file -> write `LEARNING_REVIEW\tCLOSED\treason=missing_review\tpath=<rel review>\n`; return 1.
- `status` = value after `: ` on the **first** line starting `Status:` (awk `-F': '` `$2`; missing -> `""`).
- **`recorded`:** `ids` = `$2` (whitespace field 2) of each line matching `^Recorded:\s+learn_` (in file order). Empty -> `...reason=missing_recorded_ids...` ret 1. `LEARNINGS.jsonl` absent -> `...reason=missing_learnings...` ret 1. `missing_ids` = ids not present among current-status `.id`s. If any missing -> `...reason=recorded_ids_missing_or_not_current\tids=<csv>...` ret 1. Else freshness: per id, the first current row with that id; `stored = .evidence_fingerprints // []`; empty -> failure `{id, "missing_evidence_fingerprints"}`; else compare (compact-JSON string) to `evidence_fingerprints_json(root, .evidence // [])`; differ -> `{id, "evidence_changed_or_missing"}`. No failures -> `LEARNING_REVIEW\tOPEN\tstatus=recorded\tfreshness=current\tpath=<rel>\n` ret 0; else `...reason=evidence_stale\tids=<id:reason,...>...` ret 1.
- **`skipped`:** `reason` = first `Skip reason:` value (`-F': '` `$2`). Non-empty -> `LEARNING_REVIEW\tOPEN\tstatus=skipped\treason=<reason>\tpath=<rel>\n` ret 0; else `...reason=missing_skip_reason...` ret 1.
- **else:** `LEARNING_REVIEW\tCLOSED\treason=invalid_status\tstatus=<status or "missing">\tpath=<rel>\n` ret 1.

**Parsing fidelity:** `Status:`/`Skip reason:` use awk `-F': '` -> the substring after the FIRST `: ` (split on `": "`, field index 2 = the second piece; a value containing `": "` keeps only up to the next `": "`). Recorded ids use default-FS field 2 (so one id per `Recorded:` line, the first `learn_*` token). Replicate exactly.

- **Commits:** named paths only; no AI-attribution trailer. **Branch:** `feat/memory-router-py-foundation`.

## File Structure

| Path | Responsibility |
|---|---|
| `hooks/memory_router/runs.py` | NEW: `resolve_run_dir`, `run` (verify-run). |
| `hooks/memory_router/cli.py` | EDIT: add `DieError`. |
| `hooks/memory_router/__main__.py` | register `"verify-run": runs.run`. |
| `hooks/memory_router/tests/test_runs.py` | NEW: `ResolveRunDirCase` + `VerifyRunCase` + `VerifyRunParityCase` (every status/branch + exit code, vs pinned bash). |

---

### Task 1: verify-run

**Step 1 (Red -> Green):** Implement `runs.py` + `DieError` + tests + dispatch.

**Step 2 (verify):**
- `( cd hooks && python3 -m unittest discover -s memory_router/tests -p 'test_*.py' )` -> all green.
- Grounding (isolated `env -i`): `bash <pinned> verify-run ...` vs `python3 -m memory_router verify-run ...` on run dirs with: missing review; Status recorded (ids present+fresh -> OPEN; ids missing -> CLOSED; stale fingerprints -> CLOSED; missing fingerprints -> CLOSED); Status skipped (with/without reason); invalid/missing Status. Verify the **stdout line AND the exit code** match each case; verify `run path required` / `run directory not found` (exit 2) and unknown-arg.
- ASCII check on `runs.py` -> clean.

## Self-Review (grounding evidence)

**Grounded byte-for-byte vs the real Bash** (isolated `env -i`):
- In-repo `VerifyRunParityCase`: stdout+exit identical for 7 simple branches (missing_review / skipped+reason / missing_skip_reason / invalid_status / status=missing / missing_recorded_ids / missing_learnings); fingerprint cases (fresh -> OPEN, stale -> evidence_stale) grounded with byte-identical state mirrored to both roots; **and the `resolve_run_dir` subshell-die quirk** (no `--run` and a missing `--run`) grounded for `(exit, stdout)`.
- Manual: the quirk cases match on **stdout + stderr + exit** simultaneously.

**Grounding finding (load-bearing):** `resolve_run_dir`'s `die` is swallowed by the `$( )` caller -> empty `run_dir` -> `/LEARNING-REVIEW.md` -> `missing_review` exit 1. The port replicates this (stderr line + return `""` + string-concat review path); a clean `DieError`/exit-2 design was built first and **rejected after grounding showed the divergence**. spec 12 row added.

**Independent senior-review:** the FIRST review passed the `DieError` version but **missed the quirk** (it judged `resolve_run_dir` in isolation as "exit 2 matches Bash") -- grounding caught it, not review. A SECOND review of the corrected code: 0 BLOCKER/HIGH, no issues; confirmed the quirk replication, all 9 branches, the awk-faithful parsing, the freshness compact-JSON compare, and the unreachable non-list `evidence_fingerprints` guard. (1 P3 from the first review -> the `isinstance(stored, list)` guard.)

**Suite:** 368 -> 390 tests, all green. ASCII-clean on `runs.py` + tests. `cli.py` ends unchanged (the `DieError` addition was reverted).
