# Independent merge review — branch `rebuild/r2-prose` (72282e6..c68fbdc)

**Date:** 2026-07-02 · **Reviewers:** 3 independent adversarial auditors (lenses: rule-preservation semantics · Python-port correctness · enforcement bypass), findings verified by the orchestrator against code before inclusion. This review is the independent seat the rebuild program requires before merge; the in-repo `2026-07-02-r4-exit-audit.md` came from the build process itself and does not substitute for it.

## Verdict

**NOT mergeable.** The mechanical loop is fully green (32/32 suites, both smokes, release-consistency, needle-check — reproduced independently), but R2 (prose inversion) violated its own audited contract in three ways, and the enforcement that was supposed to justify the moves is inert in every consumer install. R1 (Python port) and R3 (render) are structurally sound: no BLOCKER/HIGH there, the critical §12 claims verified clean.

## BLOCKER

FINDING BLOCKER hooks/kimiflow_core/phase_reads.py:16 :: Phase-read enforcement is silently inert in every consumer install — `manifest_path(root)` resolves `phases/PHASES.json` against the USER project root (`active_run.py:95-98` git toplevel; `:480 manifest_exists(root)`), but `phases/` ships only in the plugin; outside this repo `phase_reads_required` is never set, `gate()` returns `OPEN reason=legacy` (phase_reads.py:160-161), and every rule moved to `phases/*.md` degrades to prose that is never load-enforced. All tests pass only because fixtures run inside the kimiflow repo where `phases/` happens to sit at root. Aggravation: a foreign project shipping its own `phases/PHASES.json` flips enforcement on and an invalid manifest deadlocks clarify/plan-blocker/finish (`phase_reads.py:65-73→168`); a schema-valid foreign manifest lets arbitrary project files count as kimiflow phase detail. Neither smoke covers any of this.

FINDING BLOCKER docs/superpowers/plans/2026-07-02-invariant-targets.tsv :: Protected `prior_class=CORE-ALWAYS` rules for Phases 2/3/5/6/7 (≈20+ rows, e.g. INV-058, INV-062–072, INV-086–088, INV-092/093, INV-097/098, INV-105/106) were moved to phase files although the audited R2 detail plan forbids exactly this ("remain CORE-ALWAYS until a real pre-action gate is implemented and tested", rebuild-r2-prose-detail.md:97/103; "finish-time blocking is only a backstop", :51) and the approval packet promised "no protected Phase 2/3/5/6/7 rule gets moved by this packet" (r2-approval-packet.md:24). Pre-action gates exist only through Phase 1 (clarify-gate.sh:48) and Phase 4 (plan-blocker-gate.sh:79); Phase 5–7 reads are first checked at `finish` (active_run.py:729-731) — AFTER build/verify/commit. Worst instances: "Wait for explicit OK" (INV-105) and the no-co-author/AI-trailer prohibition (INV-106) now live only in phases/phase-7-review-commit.md; "production code never rides along" only in phases/phase-5-build.md.

## HIGH

FINDING HIGH docs/superpowers/plans/2026-07-02-r2-approval-packet.md:74-81 :: Rows the packet explicitly HELD ("stays CORE-ALWAYS unless a later packet proves a safe pre-action target") were moved anyway: INV-080 ("stop + ask, gate CLOSED" → phase-4 file), INV-090 ("--mode fix" → phase-6), INV-102 ("test-weakening-scan.sh" → phase-7), INV-120 ("Only after the commit gate and learning review are open" → phase-7). All four needles grep to 0 in SKILL.md; no later approval packet exists; CHANGELOG's phase-move entry lacks the "explicit approval" wording it uses for Group B. (INV-080/090/120 re-verified by orchestrator.)

FINDING HIGH SKILL.md:52 :: Operative clauses DELETED, not moved — rule loss, violating program goal #1 ("No rule is deleted anywhere in R2", rebuild-program.md): "The budget applies per fan-out decision, not cumulatively per run", "Same-seat substitutions are not new spawns", "Default stays lean (1 implementer, 1–2 reviewers)" grep to 0 across SKILL.md, reference.md, phases/, docs/, docs/render/ (only a CHANGELOG claim survives). Same class: the phase-colors legend (old SKILL.md@72282e6:48) is gone while SKILL.md:41 and phase-0-setup.md:12 still invoke the markers. Root cause: these clauses were never needles — the invariant corpus has deletion blind spots.

## MEDIUM

FINDING MEDIUM hooks/clarify-gate.sh:41 :: Pre-action phase-read trigger is fail-open: keys solely on the agent-writable STATE.md marker (`case "$marker" in yes|true|1|required) ;; *) return 0`, same plan-blocker-gate.sh:72) and returns 0 on unknown root / missing gate binary; ACTIVE_RUN.json's `phase_reads_required` (which phase_reads.py:129-133 would honor) is never consulted by the bash gates.

FINDING MEDIUM hooks/kimiflow_core/phase_reads.py:136 :: `record_read --write` is self-attestation (hashes the file, requires existence only) — an agent can satisfy all gates including finish with 8 back-to-back record commands and zero content in context; the detail plan sells PHASE-READS.json as "proof that the orchestrator read" (rebuild-r2-prose-detail.md:45).

FINDING MEDIUM hooks/smoke-install.sh:102 :: Neither smoke installs/verifies the new enforcement artifacts — zero references to `phases/`, `PHASES.json`, or `hooks/kimiflow_core`; active-run.sh only `bash -n` checked, never executed; a packaging regression dropping phases/ or kimiflow_core passes both smokes.

FINDING MEDIUM hooks/active-run.sh:13 :: python3-missing breaks the documented degrade contract in all 5 shims — only the jq degrade survived (plan r1-core-detail.md:81 "keep degrading to exit 0"); `exec env … python3` exits 127 (verified live on prompt-context; old bash: silent exit 0), so on hosts without python3 every UserPromptSubmit/Stop emits a hook error and the stop gate is silently non-functional.

FINDING MEDIUM hooks/kimiflow_core/launcher_status.py:135 :: Documented state_value unification not implemented — launcher's copy matches case-SENSITIVELY (`re.match` without IGNORECASE, orchestrator-verified) while kimiflow-lib.sh:14 and state.py are case-insensitive; the four former sites still disagree, active_run.py:140 carries a dead second copy, and the spec §12 table has NO state_value row (grep count 0) despite the R1 detail plan requiring one (r1-core-detail.md:40).

FINDING MEDIUM hooks/kimiflow_core/project_map_status.py:651 :: Valid-but-non-dict INDEX.json (e.g. `null`) crashes with AttributeError/exit 1 (only JSONDecodeError/OSError caught; `sections()` calls `data.get` — orchestrator-verified by code read); old bash degraded to `PROJECT_MAP unknown`, exit 0. Same class in launcher_status.py:589 (`false` → whole snapshot lost, exit 1; old: full JSON, exit 0).

FINDING MEDIUM hooks/kimiflow_core/active_run.py:742 :: finish swallows memory-router review-run stderr (run_cmd pipes it, nothing writes it) — a failing finish exits nonzero with zero diagnostics; old bash passed stderr through.

FINDING MEDIUM hooks/test-kimiflow-core-parity.sh:332 :: Parity harness compares only exit code + normalized stdout/stderr, never post-run file state — the mutating cases cannot catch written-artifact divergences that in fact exist (perms, trailing newlines, ITEMS formatting). Coverage gaps (:360 CASES): no case for active-run start/finish/stop-gate/mark-*/refresh-baseline/fail/abort, background start --write/status/collect-OPEN/cancel/mark-stale, improvements reopen/--queue findings, launcher --full; only 1 of 5 spec-§12 divergences whitelisted.

FINDING MEDIUM phases/phase-4-review-approval.md:18 :: Backlog-resume re-approval rule unreachable in its own path — the pre-build-summary re-present on backlog resume survives only via reference.md:457, pointed to from the phase-4 file, which a backlog resume entering at Phase 5 never loads; old SKILL.md:28 carried it inline in the always-loaded resume line.

## LOW (ticket list — fix opportunistically or with the M-fixes)

FINDING LOW hooks/release-consistency-check.sh:46 :: drift/budget gates skip silently when subjects vanish (rendered-source dir, SKILL.md, phases/ absent → "skip", no existence assertion in the real repo).
FINDING LOW SKILL.md:32 :: dangling pointer to a "🧭 section below" that no longer exists in the driver (Explore now in phase-1 file).
FINDING LOW hooks/kimiflow_core/background_run.py:525 :: `cmd_status` prints raw `null` exit 0 where old died exit 1; :528 `validate_files_json` accepts null/false FILES.json old rejected.
FINDING LOW hooks/kimiflow_core/launcher_status.py:251 :: repo_docs depth widened vs `find -maxdepth 2` (live-verified divergence); :313 degraded default_memory_status drops keys the old default emitted.
FINDING LOW hooks/kimiflow_core/active_run.py:807 :: hook_root crashes on non-dict tool_input (old degraded to pwd, exit 0); :471 empty-but-set KIMIFLOW_HOST recorded as "" (old: "unknown"); :488 iso_now/git_head computed twice per pair (old guaranteed identical); :394 undocumented 0600 tightening on STATE.md/ITEMS.jsonl rewrites (§12 covers only INDEX writes).
FINDING LOW hooks/kimiflow_core/improvements_status.py:187 :: trailing blank lines preserved where old collapsed to one (different bytes on mark-done/reopen --write).
FINDING LOW hooks/kimiflow_core/background_run.py:618 :: install/index error wording collapsed to one message where old named the failing target.

## Verified clean (no finding — checked adversarially)

hooks.json/hooks/hooks.json byte-identical to 72282e6 · render drift check fail-closed incl. python3-missing (release-consistency-check.sh:132-134) and covers hand-edits + unrendered source edits (AC-2.2/2.3) · byte budgets fail-closed with oversize fixture tests, phases glob covers all files · atomic.py fixes real (same-dir mkstemp + os.replace + chmod 0600 + symlink refusal); project-map failure paths honest, unit-tested · clarify/plan-blocker both source shared kimiflow-lib.sh, no private copies · resolve_root §12 rows match code · shims pass args/env/exit correctly.

## Recommended fix order (each step test-first, per-commit full loop, no AI attribution)

1. **R2 contract restoration (kills both prose BLOCKERs + both HIGHs):** move every `prior_class=CORE-ALWAYS` row for Phases 2/3/5/6/7 and all four held rows back into the driver; restore the deleted clauses (agent-budget trio, phase-colors legend, backlog-resume re-present inline or in a path a backlog resume actually loads; fix the 🧭 pointer). Explanatory/non-protected moves may stay. The 15,000-byte driver ceiling will likely break — raise it honestly in the same commit (the 13K driver was bought by breaking the contract; report the real floor). Update invariant-targets.tsv accordingly and EXTEND the needle corpus to cover the clauses that proved deletable without failing the check.
2. **Phase-read infrastructure fix (BLOCKER 1), even though restored rules no longer depend on it:** resolve manifest + phase files against the PLUGIN root (KIMIFLOW_PLUGIN_ROOT / script dir), never the user project (also kills the foreign-manifest deadlock/spoof by construction); bash gates additionally consult ACTIVE_RUN.json's `phase_reads_required`; add a consumer-shaped smoke (install into a scratch project, assert enforcement activates and a missing read closes the gate). Only after this exists and is tested may a FUTURE approval packet re-propose protected moves per the detail plan's own rules.
3. **Port M-fixes (M5–M8):** python3 guard in all 5 shims honoring the degrade contract; non-dict JSON guards (project-map, launcher, background status/FILES); finish stderr passthrough; launcher state_value case-insensitivity + spec §12 row + delete the dead copy; parity harness file-state diffing + the missing entrypoint cases.
4. Re-run the full loop + a fresh delta-audit on the fix diff; only then merge to `main` and push (user OK).
