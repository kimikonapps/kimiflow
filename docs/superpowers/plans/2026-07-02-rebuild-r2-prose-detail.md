# R2 Detail Plan: rule inventory + prose inversion

**Date:** 2026-07-02 · **Branch:** `rebuild/r2-prose` · **Status:** R2 plan audit clean (round 4: 2 auditors, `NONE`; no open BLOCKER/HIGH). Implementation may start with Commit R2.1.

**Goal:** Convert the current fat always-loaded prose into a mechanically preserved, target-mapped rule system: rules are inventoried first, invariant checks learn where each needle lives, phase detail becomes on-demand and hook-enforced, and only then approved rule groups move out of the always-loaded driver.

## Hard Constraints

- Zero rule loss dominates byte reduction. `docs/superpowers/plans/2026-07-02-invariant-check.sh` may be relocated/extended, never weakened.
- No manual edits to `skills/kimiflow/SKILL.md` in R2. Codex rendering is R3.
- No rule relocation before an explicit user approval packet for the affected group. The user's broad "work autonomously" is not treated as approval for protected-rule movement.
- Every move commit changes the rule text target and the invariant target map in the same commit, and the invariant check must pass before and after.
- `SKILL.md` remains a valid, thin always-loaded driver: opt-in, mode routing, phase transitions, gate commands, STOPs, and pointers stay visible.
- Phase-loaded prose must be enforced by a local state artifact, not just by "please read this file" prose.
- Bash remains Bash 3.2-compatible. Python additions stay stdlib-only and live under `hooks/kimiflow_core/`.
- Stage named paths only; no `git add -A` / `git add .`; no AI attribution.
- Per commit: relevant focused tests, full discovered hook loop excluding `test-gate.sh` / `test-weakening-scan.sh`, both smokes, release consistency, `bash -n hooks/*.sh`, `shellcheck --severity=error hooks/*.sh`, `git diff --check`, and a `CHANGELOG.md` Unreleased entry. After the target map exists, the invariant check runs on any commit that touches `SKILL.md`, `reference.md`, `phases/`, rendered host files, the invariant corpus/map/checker, or any path listed as an invariant target or verification path.
- Anti-hallucination for the inventory: no rule may be classified from memory. Every row needs a source line/needle and a current target path.

## Current Inputs

| Artifact | Current role |
|---|---|
| `SKILL.md` | Claude always-loaded authority, 53,277 bytes |
| `reference.md` | detailed reference authority, 125,879 bytes |
| `skills/kimiflow/SKILL.md` | frozen Codex port, regenerated in R3 |
| `docs/superpowers/plans/2026-07-02-token-restructuring-invariants.md` | protected-rule corpus and smoke phrase contract |
| `docs/superpowers/plans/2026-07-02-invariant-check.sh` | current fixed-target grep gate over `SKILL.md` and `reference.md` |
| `hooks/smoke-install.sh`, `hooks/smoke-install-codex.sh` | structural install contracts, including literal phrase greps |
| `hooks/kimiflow_core/active_run.py` | active run state machine available for phase-read enforcement after R1 |

## Target Architecture

| Path | Responsibility |
|---|---|
| `docs/superpowers/plans/2026-07-02-rule-inventory.md` | audited inventory: every protected rule and every operative SKILL.md line mapped to a classification and target home |
| `docs/superpowers/plans/2026-07-02-invariant-corpus.tsv` | immutable protected ID corpus: `id<TAB>source<TAB>strong_needle<TAB>target_constraint<TAB>notes`; the target map must not define the corpus |
| `docs/superpowers/plans/2026-07-02-invariant-targets.tsv` | machine-readable target map: `id<TAB>authoritative_target<TAB>verification_path<TAB>notes`; it never carries an override needle |
| `docs/superpowers/plans/2026-07-02-invariant-check.sh` | generic target-map checker; joins target rows to `invariant-corpus.tsv` and greps the corpus `strong_needle` only; exact-path grep covers SKILL, reference, phases, production hook/code targets, and rendered host files; hook tests are verification paths, never the sole authoritative target for runtime/prohibition rules |
| `phases/phase-0-setup.md` … `phases/phase-7-review-commit.md` | on-demand phase detail, created only after target-map support exists |
| `phases/PHASES.json` | phase manifest with phase id, file path, and required read marker metadata |
| `hooks/kimiflow_core/phase_reads.py` | stdlib helper for phase-read records and stale-file detection |
| `hooks/active-run.sh` / `kimiflow_core.active_run` | records `phase-read --phase N --file phases/... --write`, exposes `phase-read-gate --through-phase N`, and blocks `finish --write` when required phase files for completed phases were not read or changed after reading |
| `hooks/plan-blocker-gate.sh` | after phase files exist, calls the phase-read gate for phases 1-3 before reviewers are spawned |
| `.kimiflow/<slug>/PHASE-READS.json` | run-local proof that the orchestrator read the on-demand phase files it relied on |

## Rule Classifications

- `CORE-ALWAYS`: must stay in the thin `SKILL.md` driver because the model needs it before any phase file can be loaded or because it is a global prohibition/STOP.
- `GATE-CHECKABLE`: should move into an existing or new local gate first. `SKILL.md` keeps a one-line pointer to the enforcing hook. A production hook/module must enforce the rule and a test must verify it; the invariant target may not be test-only for runtime, STOP, fail-closed, or prohibition rules.
- `PHASE-ONDEMAND`: may move to exactly one `phases/phase-*.md` file once phase-read enforcement exists **and** a mechanical gate runs before the first protected action that depends on that phase file. If no pre-action gate exists, the protected rule stays `CORE-ALWAYS`; final `finish --write` blocking is only a backstop, not permission to move commit/build/approval/STOP rules out of the driver.
- `REFERENCE-DETAIL`: maintainer/background explanation that can live in `reference.md` or `docs/` while its operative rule stays covered elsewhere.
- `SMOKE-CONTRACT`: literal phrase currently grepped by a smoke script; preserve the phrase or update the smoke in the same commit with an equivalent target-map-backed assertion.

## Implementation Sequence

### Commit R2.1 — Rule inventory, no prose movement

- Create `docs/superpowers/plans/2026-07-02-rule-inventory.md`.
- Create `docs/superpowers/plans/2026-07-02-invariant-corpus.tsv`; this is the immutable completeness source for protected IDs. Seed it from the invariant artifact rows and the actual smoke scripts, not from the current invariant-check script alone.
- Include the reference alias-table smoke contracts (`kimiflow grill/plan/review/audit ... no code`) and replace weak current needles with strong contextual needles, e.g. `MR verify-run ... CLOSED result blocks completion` and `neither .kimiflow/ nor repo files store the key`.
- Add a line-coverage section for all non-empty `SKILL.md` lines, marking each line as covered by one or more rule IDs, `heading/context`, `example/prose-only`, or `needs-review`; `heading/context` and `example/prose-only` require a short justification, not a blanket escape hatch.
- Classify each protected rule into `CORE-ALWAYS`, `GATE-CHECKABLE`, `PHASE-ONDEMAND`, `REFERENCE-DETAIL`, and/or `SMOKE-CONTRACT`.
- Record target-home proposals, but do not move text or change `SKILL.md` / `reference.md`.
- Verification: inventory has no `needs-review` rows; every current invariant-check needle and every smoke-script phrase appears in the inventory/corpus or is explicitly replaced by a stronger contextual needle; every non-empty `SKILL.md` line appears in the line-coverage section; `bash docs/superpowers/plans/2026-07-02-invariant-check.sh` remains green.

### Commit R2.2 — Target-map invariant checker, no target changes

- Add `docs/superpowers/plans/2026-07-02-invariant-targets.tsv` containing every ID from `2026-07-02-invariant-corpus.tsv` with its current authoritative target (`SKILL.md` or `reference.md`) plus optional verification path. It must not duplicate or override the corpus needle.
- Rewrite `docs/superpowers/plans/2026-07-02-invariant-check.sh` to read the TSV target map.
- Add `hooks/test-invariant-check.sh` with fixture coverage for:
  - exact-path target success,
  - missing file failure,
  - missing needle failure,
  - missing corpus ID in target map failure,
  - extra target-map ID failure,
  - duplicate/empty ID failure,
  - empty target/needle failure,
  - test-only target rejected for runtime/prohibition rule,
  - attempted target-map needle override rejected,
  - corpus strong-needle weakening caught even when the target ID remains,
  - target map row with notes containing spaces,
  - repository root override or temp-fixture execution.
- The rewritten check must pass against the unmodified repo before any move commit.
- Verification: fixture test, real invariant check, full hook loop, both smokes, release consistency.

### Commit R2.3 — Phase-read enforcement, no prose movement

- Add `hooks/kimiflow_core/phase_reads.py` plus unit tests.
- Extend `kimiflow_core.active_run` with:
  - `phase-read --run .kimiflow/<slug> --phase <0-7> --file phases/<file>.md --write`;
  - `phase-read-status --run .kimiflow/<slug> --json`;
  - `phase-read-gate --run .kimiflow/<slug> --through-phase <0-7>`;
  - finish-time validation that required phase files for phases 0-7 have a fresh read record before `finish --write` marks Phase 7 / `Status: done`.
- Add `phases/PHASES.json` with the planned phase ids and file paths, but keep phase files skeletal until approved movement starts.
- Extend `active-run start` for new post-R2 runs: when `phases/PHASES.json` exists at start time, write `phase_reads_required: true` in `ACTIVE_RUN.json` and `Phase reads required: yes` in `STATE.md` (idempotently, preserving existing fields). This is the only automatic opt-in path; existing runs without the marker remain legacy.
- Extend `plan-blocker-gate.sh` so that when phase-read enforcement is active for the run, the gate closes before reviewer spend unless fresh phase-read records exist through Phase 4. This is a pre-review/approval boundary for Phase 4 rules only and a late backstop for earlier phases; it is not allowed to justify moving protected Phase 2/3 rules.
- Phase 1 read is checked by `clarify-gate.sh` before Phase 2 when phase-read enforcement is active.
- Add an `enforcement_boundary` column to the inventory for every `PHASE-ONDEMAND` proposal:
  - `clarify-gate` for Phase 1 rules;
  - `plan-blocker-gate` for Phase 4 review/approval rules;
  - a named existing/new pre-action gate for Phase 5-7 rules.
  Protected Phase 2/3 research/diagnosis/planning rules do not have a pre-action enforcement boundary in the current system; they remain `CORE-ALWAYS` unless R2 first implements and tests a real pre-phase-entry gate. If the boundary is `plan-blocker-late` or `finish-only`, the row may move only if it is explanatory/non-protected. Protected Phase 5-7 build/verify/commit/advisory/STOP/prohibition rules also remain `CORE-ALWAYS` until a real pre-action gate is implemented and tested.
- Preserve current behavior for pre-R2 active runs by requiring an explicit run-local opt-in marker (`phase_reads_required: true` in `ACTIVE_RUN.json` and/or `Phase reads required: yes` in `STATE.md`). A newly added repo-global `phases/PHASES.json` must not retroactively close `finish`, `status`, or `plan-blocker` for already-active runs without that marker.
- Verification: active-run unit tests for recording, symlink/traversal refusal, stale phase-file hashes, missing read records, `phase-read-gate` verdicts, `active-run start` setting the opt-in marker for new runs when a manifest exists, no marker when no manifest exists, clarify/plan-blocker closure on missing phase reads, Phase-7 finish blocking, and legacy/pre-R2 run compatibility even when `phases/PHASES.json` now exists; full `hooks/test-active-run.sh` + `hooks/test-clarify-gate.sh` + `hooks/test-plan-blocker-gate.sh`; parity remains green for unchanged public paths except explicitly new subcommands / marker-gated behavior.

### Commit R2.4 — Approval packet, hard stop before moving rules

- Generate `docs/superpowers/plans/2026-07-02-r2-approval-packet.md`.
- Group proposed movements by blast radius:
  - Group A: phase-detail movement (`PHASE-ONDEMAND`) with no new gate semantics.
  - Group B: gate-backed movement (`GATE-CHECKABLE`) where tests already enforce the behavior.
  - Group C: smoke-contract rewrites, if any are still needed.
  - Group D: reference/doc repartitioning (`REFERENCE-DETAIL`).
- For each group list rule IDs, current target, proposed target, retained always-loaded pointer, verification command, and for every `PHASE-ONDEMAND` row the pre-action `enforcement_boundary`. Rows whose boundary is missing or `finish-only` for a protected rule must stay `CORE-ALWAYS` in the approval packet.
- **STOP and ask the user for explicit approval per group.** No `SKILL.md` / `reference.md` prose move is allowed before that approval.

### Post-Approval Loop — one approved group per commit series

For each approved group:

1. Create or update only the target phase/reference/hook/test files for that group.
2. Move the invariant target rows in `2026-07-02-invariant-targets.tsv` in the same commit.
3. Shrink the original prose only after the authoritative target contains the protected needle and the verification path proves the behavior. For runtime/prohibition/STOP/fail-closed rules, tests alone never satisfy the invariant.
4. Keep `SKILL.md` pointers concise but operational: phase entry must name the phase file and the `active-run.sh phase-read ... --write` command where applicable.
5. Run:
   - `bash docs/superpowers/plans/2026-07-02-invariant-check.sh`
   - `bash hooks/test-invariant-check.sh`
   - affected hook tests
   - full discovered hook loop excluding `test-gate.sh` / `test-weakening-scan.sh`
   - `bash hooks/smoke-install.sh`
   - `bash hooks/smoke-install-codex.sh`
   - `bash hooks/release-consistency-check.sh`
   - `bash -n hooks/*.sh`
   - `shellcheck --severity=error hooks/*.sh`
   - `git diff --check`

## Verification Gate For R2 Completion

R2 is complete only when:

- `SKILL.md` is a thin driver with measured byte count reported in the final R2 commit.
- Every protected rule ID is present in the target map and passes the invariant check.
- Phase-read enforcement blocks before the protected action for every moved protected rule; finish-time blocking covers all phases only as a final backstop, including Phase 7 before `Status: done`.
- `skills/kimiflow/SKILL.md` remains untouched manually; any drift is deferred to R3 rendering.
- Both smokes and the full hook loop are green.
- `CHANGELOG.md` Unreleased explains the inventory, target-map invariant checker, phase-read enforcement, and any approved prose movements.

## Audit Questions For R2 Plan Reviewers

1. Does the target-map invariant checker actually preserve the B4 contract, or can a rule disappear while the check still passes?
2. Is the phase-read enforcement strong enough to make on-demand prose real, given what `active-run.sh` can observe?
3. Are the user-approval stops placed before any protected-rule weakening could occur?
4. Does the plan avoid accidentally editing the frozen Codex port before R3?
5. Are the verification loops sufficient for commits that touch only docs, only hooks, and mixed docs+hooks?
