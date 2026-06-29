# Handoff — memory-router Bash->Python port (session 6, Plans 21-24 + release 0.1.51)

**Date:** 2026-06-29 · **Repo:** kimiflow · **Branch:** `main` (post-merge)

Supersedes session-5. Per-plan detail lives in the gitignored ledger `.superpowers/sdd/progress.md` (read it for exact nuances + commit SHAs). This handoff carries the verified state + the remaining roadmap.

---

## TL;DR

Continuing the memory-router Bash->Python port toward **cutover** (user-authorized: "erst fertig bauen, dann ganz umschalten" - build all 13 subcommands, then switch the runtime). This session:

- **Merged** `feat/memory-router-py-foundation` -> `main` (fast-forward) and **released 0.1.51** (pushed `main` + tag, GitHub release, CI green). The Python port shipped **additive + dormant** - `hooks/memory-router.sh` (Bash) is still the active runtime; **no cutover yet**.
- Ported **4 more subcommands** (Plans 21-24): **metrics**, **verify-run** (+ `resolve_run_dir`), **consolidate**, **propose** - each plan-audited / grounded byte-for-byte / senior-reviewed / committed.

**Wired: 11/13 subcommands** - `classify`, `index`, `status`, `curate`, `record`, `recall`, `history`, `metrics`, `verify-run`, `consolidate`, `propose`. **Remaining: `review-run`, `provider`.** Then **cutover**. **Suite: 415 tests, all green.**

**Two real Bash bugs caught by grounding this session** (both replicated for drop-in fidelity, spec 12):
1. `resolve_run_dir` `die` is swallowed by its `$( )` caller -> empty `run_dir` -> `missing_review` exit 1 (not a clean exit 2).
2. `cmd_propose` unknown-id gate is dead code (jq `.`-rebinding `($known | index(.))` = subarray-self-search, never fires) -> unknown approve/reject ids are silently accepted.

Resume with **"weiter mit review-run"**.

---

## Git state (end of session 6)

| Ref | Meaning |
|---|---|
| `main` | HEAD = Plan-24 feat `2b8bb86`; **8 commits ahead of `origin/main`** (Plans 21-24, unpushed); all additive, **Bash runtime untouched**, dormant |
| `origin/main` | `db25588` (Release 0.1.51) |
| `kimiflow--v0.1.51` (tag, pushed) | last release |
| `kimiflow--v0.1.50` (tag) | the pinned Bash source-of-truth for grounding |

**Note:** post-release porting commits are on **local `main`**, unpushed (additive/dormant, so safe to hold). `git diff kimiflow--v0.1.50 HEAD -- hooks/memory-router.sh` is still empty.

**Verify:**
```bash
cd "<repo>" && export PATH="/opt/homebrew/bin:$PATH"
( cd hooks && python3 -m unittest discover -s memory_router/tests -p 'test_*.py' )   # 415 OK
```

## Package state — `hooks/memory_router/`

Subcommand modules: `classify`, `index`, `status`, `curate`, `record`, `recall`, `history`, `metrics`, `runs` (verify-run), `consolidate`, `propose`. Support: `cli`, `contracts`, `store`, `paths`, `text`, `clock`, `rows`, `writes`, `memory_md`, `recall_index`, `summaries`, `global_metrics`, `provider`, `usage_metrics`.

---

## What's NEXT — review-run, provider, then cutover

### 1. `review-run` (`cmd_review_run` 3281-3415) — THE LARGEST. ~15 new helpers. Deep-read map:
- **DONE deps:** `resolve_run_dir` (runs.py), `append_learning_row` (writes), `write_bounded_memory` (memory_md), `curate.run`, `index.run`, **`propose.run`** (review-run calls it at 3381!), `status_json` (status), `economics_summary_json` (summaries), `classify.classify_text`, `rel_path`, `iso_now`.
- **NEW helpers to port:**
  - `quality_gate_json` (2339-2387): word-count(tr-cs alnum_-) + `rows.memory_security_json` (done) + generic/kind regexes (incl. German `bestätigt`/`muss`/`immer`...). `{ok, words, reasons, security}`.
  - tsv extraction: `first_substantive_tsv` (2656-2673), `structured_learning_tsv` (2675-2717, kind-keyword regexes), `learning_summary_json` (2719-2736: structured-then-fallback, `line/summary[:320]/source`).
  - `review_candidate_json` (2738-2787): per file -> `learning_summary_json` -> `classify_text` -> skip if target==skip, run_only->project_memory -> `quality_gate_json` -> the candidate object; returns FIRST matching file.
  - **economics-record subsystem** (for `record_run_economics_json` 3124-3142): `run_economics_row_json` (NOT yet read), `write_economics_row` (append MEMORY-ECONOMICS.jsonl), `write_global_economics_row` (the global salt/hash/project_id record: `hash_text` 387, `ensure_global_metrics_salt` 406, `project_id`, on top of `global_metrics.py` Plan-12 helpers). **Read 3000-3130 + 387-460 before planning.** (Consider a separate sub-plan for this writer subsystem.)
  - `write_learning_review_markdown` (3144-3168): `# Learning Review` + Run/Status/Generated, then skip-reason OR `## Four Questions` (per-entry block).
  - `run_lifecycle_json` (3171-3242): composes `status_json` + economics + notification into the lifecycle object (incl. `next_actions` unique list). `write_run_lifecycle_json` (3244, `jq . >`) + `write_run_lifecycle_markdown` (3250-3279, the MD layout).
- **`cmd_review_run` flow (3281-3415):** args `--root/--run/--write/--pretty/--skip <reason>`; `--skip` -> write skipped review + economics + lifecycle (on `--write`) + `{status:"skipped",...}`. Else build 4 candidates via `review_candidate_json` over fixed (question,kind,topic,file-list) tuples; 0 -> `die "no reusable learning candidates ... --skip" 1`; quality-gate failures -> `die "review-run quality gate closed: <q:reasons;...>" 1`; on `--write` per entry `append_learning_row` (+`recorded_id`), then `write_bounded_memory` + `curate --write` + `index --write` + **`propose --write`** (capture `.notification`) + `record_run_economics_json` + `write_learning_review_markdown` + lifecycle writers. Output object 3390-3413 (status recorded|preview, entries, recorded_count, memory_updated, proposal_update, notification, economics, lifecycle).
  - **Watch:** `run_dir="$(resolve_run_dir ...)"` inherits the empty-run_dir subshell quirk (spec 12).

### 2. `provider` (`cmd_provider` 4160+) — status/health/setup/detect/connect/configure/prefetch/sync; needs `provider_setup_plan_json` (890-994), `write_provider_prefetch_markdown`/`write_provider_sync_markdown` (4115-4158), base/mcp-url helpers. Most provider_*_json already ported (Plans 14-15).

### 3. Cutover (final, public — present to user for go/no-go): replace `hooks/memory-router.sh` body with the shim `exec env PYTHONPATH="$dir" python3 -m memory_router "$@"`, delete the Bash logic, full suite + smokes green, update README/COMPATIBILITY (Python >=3.9)/CHANGELOG, `/release`.

---

## The proven loop (unchanged; key reminders)
1. **Plan-audit (external, pre-impl)** for complex commands; skip for 1-file clear-scope (mini-fix rule). Grounding is the strongest gate regardless.
2. **Ground byte-for-byte** vs pinned Bash (`env -i PATH=... HOME=/tmp KIMIFLOW_OBSIDIAN_URL='http://127.0.0.1:9/'`, dead detect port, **test token only** - host has a real `OBSIDIAN_API_KEY`). This caught BOTH bash bugs this session - review alone missed the subshell-die quirk.
3. **Independent senior-review** per command (the agent sometimes infra-stalls; just re-dispatch).
4. **Pure ASCII** every changed *source* file (middot/accents as `\uXXXX`). Markdown docs keep literal `§`/`·`.
5. Commit named paths only; **no AI/co-author trailer**; never `git add -A`. `docs: plan N` = plan doc; `feat(memory_router): ...` = code + tests + spec 12. Ledger `.superpowers/sdd/progress.md` gitignored.
6. **Test fixtures:** `append_learning_row` sanitizes evidence to `NOT VERIFIED` in non-git temp dirs - hand-craft `LEARNINGS.jsonl` + `rows.evidence_fingerprints_json`-computed fingerprints when you need evidence-backed rows to survive the filters.

## Open decisions for the user
- **Push local `main`** (8 unpushed additive commits) now, or hold until cutover?
- review-run is large enough to warrant splitting the economics-record writer subsystem into its own sub-plan.
