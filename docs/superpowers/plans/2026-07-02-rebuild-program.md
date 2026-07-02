# Plan: Rebuild program (R0–R4) — code-over-prose restructuring — rev 2 (AUDIT ROUND 1 FIXED)

**Date:** 2026-07-02 · **Status:** Program plan audit round 1 complete (2 adversarial auditors; BLOCKER/HIGH findings fixed in rev 2). R1+ still require their own detail plan + external plan-audit before implementation. · **Executor:** Codex (external agent), workpackage by workpackage, each with its own detail plan + audit.

**Basis:** B1–B4 audit-hardening complete and green (handoffs `docs/superpowers/handoffs/2026-07-02-audit-hardening-session*.md`); architectural assessment 2026-07-02. This program **supersedes** the open B5 items — see "Handoff disposition" below.

## Goal (priority order)

1. **Zero rule loss** (dominates everything; same contract as B4 — the needle-check `docs/superpowers/plans/2026-07-02-invariant-check.sh` is never weakened, only relocated with its targets).
2. **Invert the prose/code ratio:** every rule that is machine-checkable becomes (or maps to) a gate; prose keeps only what code cannot check. SKILL.md becomes a thin always-loaded flow driver; phase detail loads on phase entry.
3. **One implementation language for complex logic:** the five large state/status bash scripts move to a tested Python core (`kimiflow_core`), thin bash shims keep every hook entrypoint and CLI contract identical. Remaining small bash gates share one sourced helper lib (kills the `resolve_root`/`state_value` drift class).
4. **One canonical source for both hosts:** Claude `SKILL.md` and Codex `skills/kimiflow/SKILL.md` are rendered from a single source; drift becomes a release-consistency failure.
5. **Token budgets as enforced constraints:** byte ceilings for always-loaded prose and default hook output, checked by `release-consistency-check.sh`.

## Non-goals

- No behavior change to any gate semantics (fail-closed stays fail-closed; deny/allow decisions byte-compatible unless a §12 row documents a deliberate divergence, as in the memory_router port).
- No redesign of the phase model (Phases 0–7, modes, scaling knobs stay as specified).
- No port of `memory_router` (already Python, already tested) and no port of the small gates to Python (they stay bash + shared lib; `vault-mcp-setup.sh` stays bash — setup utility, not hot path).
- No change to `evals/`, examples, demo.
- No new features. This is a restructuring program.

## Preservation contract (hard, inherited from B4)

- The invariants artifact (`2026-07-02-token-restructuring-invariants.md`) and needle-check gate every commit that touches SKILL.md / reference.md / rendered ports before R2's target map exists; after the target map exists, they gate every commit that touches any invariant target or verification path. When a rule moves (SKILL → production gate/code target, SKILL → phase file, SKILL → rendered source), the needle moves **in the same commit** and the check must pass before and after.
- Existing test suites are necessary but not sufficient for parity: for every ported script, the **unmodified** `hooks/test-<name>.sh` suite must pass against the new shim, and a new old-vs-new differential harness must compare stdout/stderr/exit for representative CLI paths against the pre-port Bash implementation. Suite edits during a port are forbidden except where the detail plan documents a deliberate divergence as a spec §12 row (precedent: memory_router port, `docs/superpowers/specs/2026-06-28-memory-router-python-cli-design.md` §12).
- Smoke contracts: `smoke-install.sh` + `smoke-install-codex.sh` run per commit; greped phrases are never reworded without updating the smoke in the same commit (lesson from `84672dd`).

## Workpackage R0 — Preconditions & ride-alongs (small, before Codex starts)

0. **Commit/park this plan artifact first.** The worktree must not carry an untracked `docs/superpowers/plans/2026-07-02-rebuild-program.md` when R1 starts; either commit the audited plan on `main` (preferred, named path only) or move it outside the repo and record the decision. A dirty/untracked plan would close the working-tree gate before implementation work.
1. **Push local `main` through the audited plan commit** (currently the B1-B4 commits `2b5c096`…`54e5cbf`, plus the plan artifact if committed) — user OK required. The rebuild must not sit on top of unpushed-only history.
2. Verify/fix the local release skill (`.claude/skills/release/SKILL.md`, ignored by git): its test loop must match the CI discovery loop that excludes the 2 production hooks (`test-gate.sh`, `test-weakening-scan.sh`). If already aligned, record as no-op; do not pretend this is a repo commit.
3. Freeze the Codex port `skills/kimiflow/SKILL.md` (no manual edits from here on; R3 regenerates it).
4. Branching: each workpackage runs on its own branch (`rebuild/r1-core`, `rebuild/r2-prose`, …), merged to `main` only after the full verification loop is green and the diff is reviewed.

## Workpackage R1 — `kimiflow_core` Python package + bash helper consolidation

The complexity center. Sized like the memory_router port (that was ~4,400 bash lines over 7 sessions; this is ~3,200–3,700).

**Port scope (bash → Python, thin shims stay):**

| Script | Lines | Known defects fixed by the port |
|---|---|---|
| `hooks/active-run.sh` | 905 | `state_value` semantics unified (see below) |
| `hooks/launcher-status.sh` | 861 | `state_value` semantics unified |
| `hooks/project-map-status.sh` | 627 | bare `mktemp` in `$TMPDIR` + cross-device `mv` (not atomic), 0600 mode, ENOSPC still prints REFRESHED (~lines 314/425/441/537) → Python `tempfile`/`mkstemp` in the destination dir + explicit `0600` mode + same-dir `os.replace`, honest failure paths |
| `hooks/background-run.sh` | 555 | shares `resolve_root` drift |
| `hooks/improvements-status.sh` | 252 | shares `resolve_root` drift |

Optional (decided in the R1 detail plan after a call-graph inventory): `hooks/agentic-readiness.sh` (482). If it stays bash it uses the shared lib below.

**Shim pattern** (proven by `hooks/memory-router.sh`):

```bash
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m kimiflow_core.<entry> "$@"
```

Python floor: stdlib ≥ 3.9, same as memory_router. Package at `hooks/kimiflow_core/` with `tests/` run via `python3 -m unittest discover` (same pattern as `hooks/test-memory-router-unit.sh:16`), wrapped in a `hooks/test-kimiflow-core-unit.sh` so the existing `test-*.sh` discovery loop picks it up.

**Differential parity harness (new, before the first cutover):** add `hooks/test-kimiflow-core-parity.sh` (or per-script parity wrappers if the detail plan chooses smaller files). It records a `BASE_SHA` from the pre-port commit and runs curated cases against `git show "$BASE_SHA:hooks/<script>.sh"` materialized in a temp dir vs. the working-tree shim, diffing stdout, stderr, and exit code. The case list starts from the existing `hooks/test-<name>.sh` fixtures and adds CLI/status/help/error paths they do not assert. Deliberate divergences require a spec §12 row and an explicit parity expectation update in the same commit.

**Helper unification (the drift killers):**

- `resolve_root()` is currently defined 4× (`active-run.sh`, `background-run.sh`, `agentic-readiness.sh`, `improvements-status.sh`) with divergent semantics (`pwd -P` + hard-die vs logical + fallback — baseline-audit finding). One canonical implementation in `kimiflow_core.paths`; remaining bash hooks get it from a shared sourced lib (extend `hooks/kimiflow-root.sh` or a new `hooks/lib.sh`).
- `state_value()` is currently defined 4× (`active-run.sh`, `clarify-gate.sh`, `launcher-status.sh`, `plan-blocker-gate.sh`) with case-(in)sensitivity divergence. The R1 detail plan FIRST documents current behavior per site (with repro), THEN picks one canonical semantic, records it as a spec §12 row, and unifies all four. No silent behavior pick.
- `state-gate.sh:61` deny message is currently aligned with the SKILL contract; keep it as a verified no-op unless a fresh repro proves drift.

**Verification per ported script:** old-vs-new parity green for that script → existing `hooks/test-<name>.sh` green unchanged → both smokes → full loop. Port order: smallest first (`improvements-status` → `project-map-status` → `background-run` → `launcher-status` → `active-run`) so the shim/parity pattern is proven cheap before the two giants.

## Workpackage R2 — Rule inventory + prose inversion

Depends on R1 (new gates are written in `kimiflow_core`; phase-read enforcement hooks into the existing state machine).

1. **Inventory (mechanical, no judgment):** every rule in SKILL.md (53,277 bytes) gets a row: ID · line · verbatim needle · classification `CORE-ALWAYS` / `GATE-CHECKABLE` / `PHASE-ONDEMAND` · target home · one-line checkability rationale. Starting corpus: the 130+ needles in the invariants artifact. Output: `2026-07-02-rule-inventory.md`.
2. **User sign-off per group** (binding constraint from session 2: relocating protected rules weakens the B4 preservation contract, so each group move needs explicit user approval — BLOCKER-level stop, not advisory).
3. **Extend the invariant gate before moving prose:** convert `docs/superpowers/plans/2026-07-02-invariant-check.sh` from SKILL/reference-only greps into a corpus-backed target-map check whose rows can point at SKILL.md, reference.md, `phases/*.md`, production hook/code targets, and rendered host files. Hook tests are verification paths, never the sole authoritative target for runtime/prohibition rules. The extended check must pass against the pre-move state before any rule relocation commit.
4. **Implement per approved group:**
   - `GATE-CHECKABLE` → production gate/check in `kimiflow_core` or Bash hook plus a behavior test, THEN the prose copy shrinks to a one-line pointer ("enforced by <hook>"). The invariant needle relocates to the authoritative production target; the test proves behavior but cannot be the only target.
   - `PHASE-ONDEMAND` → `phases/phase-<N>.md`; loaded at phase entry; the read is **enforced** via the existing phase state the hooks already track (exact mechanism — marker/ack checked by `state-gate.sh` or `active-run.sh` — fixed in the R2 detail plan against real code, not invented here).
   - `CORE-ALWAYS` → stays in the thin SKILL.md driver (target ~10–15K bytes: frontmatter, mode table, phase transitions + gate commands, pointers).
5. reference.md (125,879 bytes) is re-partitioned the same way: per-phase drilldowns move next to their phase file; maintainer prose continues moving to `docs/` (precedent: `docs/commit-secret-gate.md`).

**This supersedes the open floor decision (OFFEN 1):** the ≤30K question dies — the driver lands well under it, and rules live where they're either enforced or phase-loaded. No rule is deleted anywhere in R2.

## Workpackage R3 — Single-source host rendering

Depends on R2 (render from the NEW thin structure, not the old fat file).

- Canonical source: the thin driver + phase files + a small host-overlay layer (Claude vs Codex differences: hook wiring strings, `KIMIFLOW_HOST`, path roots — inventory in the detail plan from a diff of today's two SKILL.md files).
- Renderer: `PYTHONPATH="$ROOT/hooks" python3 -m kimiflow_core.render` writes both `SKILL.md` and `skills/kimiflow/SKILL.md` from the repo root (or an equivalent wrapper in `hooks/` that sets `PYTHONPATH`). **Rendered files stay committed** (keeps the plain-files-on-disk property; no install-time build step).
- `release-consistency-check.sh` gains: re-render with that portable invocation → `git diff --exit-code` on the two outputs (fail-closed drift check).
- Kills the baseline-audit leftover "Codex port spelling of Current-State Pulse/Gate" by construction.

## Workpackage R4 — Token budgets + program exit re-audit

1. Byte ceilings enforced in `release-consistency-check.sh` (numbers fixed in the detail plan after R2 lands, measured not guessed): thin SKILL.md ≤ ceiling; each `phases/*.md` ≤ ceiling; launcher default output ≤ ceiling (new assertion in `test-launcher-status.sh`).
2. **Program exit gate = the old B5 re-audit, relocated:** fresh independent adversarial auditors (consistency + token lenses, anti-hallucination rules, findings self-verified against code) over the rebuilt system; then the full loop: `bash -n hooks/*.sh`, JSON validation with `jq -e` for the CI manifest set, all `hooks/test-*.sh` except `test-gate.sh`/`test-weakening-scan.sh`, Python unit wrappers (`test-memory-router-unit.sh`, `test-kimiflow-core-unit.sh`) and parity wrappers, both smokes, `release-consistency-check.sh`, needle-check, and `shellcheck --severity=error hooks/*.sh`. No `pytest` dependency unless a later audited plan explicitly introduces one.
3. CHANGELOG consolidation; release only on user's call.

## Handoff disposition (what happens to the open Session-2 items)

| Open item (handoff 54e5cbf) | Disposition |
|---|---|
| OFFEN 1 — floor decision ≤30K | **Superseded by R2** (thin driver + relocation with per-group user sign-off) |
| OFFEN 2.1 — B5 re-audit | **Moved to R4 exit gate** (auditing prose that R2 restructures would be wasted; needle-check already validates today's state as the porting source) |
| `state-gate.sh:61` wording | Verified already aligned; no-op unless fresh repro proves drift |
| `resolve_root` drift (4 sites) | → R1 (shared helper) |
| `state_value` case drift (4 sites) | → R1 (documented unification, §12 row) |
| `project-map-status.sh` mktemp/atomicity | → R1 (Python port) |
| release skill test loop | → **R0.2** (local ignored maintainer skill: verify/fix, or record no-op if already aligned) |
| Codex port spelling | → R3 (by construction) |
| `test-gate.sh` untracked-marker residual | Stays as decided (documented residual) |
| OFFEN 3 — 12 unpushed commits | → **R0.1** (push before Codex starts; user OK) |

## Global constraints (bind Codex on every commit)

- **No AI attribution anywhere** — no Co-Authored-By trailers, no "Generated with" lines, in commits, code, or PRs.
- Stage named paths only; never `git add -A` / `git add .`. Never stage `.env`/keys/tokens.
- TDD: failing test first for every behavior change; ports use existing suites plus the new old-vs-new differential harness as the parity gate (RED = suite/parity against an empty shim is acceptable).
- Per commit: full suite loop (all `hooks/test-*.sh` except the 2 production hooks — this includes the Python unit wrappers) + both smokes + `release-consistency-check.sh` + CHANGELOG Unreleased entry. Before the R2 target map exists, run the needle-check on any SKILL/reference/phase-file edit. After it exists, run it on any commit touching SKILL.md, reference.md, phases, rendered host files, the invariant corpus/map/checker, or any path listed as an invariant target or verification path.
- Bash 3.2 (macOS) for all remaining bash: `${arr[@]+…}` idiom, no `timeout(1)`, no associative arrays, no `mapfile`.
- Anti-hallucination: auditor/agent findings are verified against code (repro/grep) before entering any plan or fix. A wrong finding is worse than a missed one.
- Each workpackage: detail plan (bite-sized, code-complete tasks per `superpowers:writing-plans`) → external plan-audit (≥2 disjoint-lens adversarial auditors, `FINDING <SEV> <ref> :: <reason>`, cap 3 rounds, zero open BLOCKER/HIGH) → only then implementation. Codex cross-family seat optional; on hang → same-family fallback + note (precedent: B4).

## Execution order & sizing honesty

R0 (hours) → R1 (largest; memory_router-port scale — plan for multiple sessions, one port sub-plan per script) → R2 (B4-scale plus gate work; blocked on per-group user sign-offs) → R3 (small once R2 defines the structure) → R4 (audit + budgets). R1 and the R2 **inventory step** can run in parallel; R2 implementation waits for R1.

## Risks

- **Parity gaps in the ports** — mitigated by old-vs-new differential parity + unmodified-suite coverage + smallest-first order + §12 rows for deliberate divergence (proven pattern).
- **Rule loss in R2** — needle-check same-commit relocation rule + per-group user sign-off + R4 adversarial re-audit.
- **Phase-read enforcement too weak** (model skips on-demand files) — R2 detail plan must specify a hook-enforced mechanism against real state-machine code; if none is verifiable, the affected group stays `CORE-ALWAYS` (fail-closed default).
- **Renderer indirection confuses contributors** — rendered files stay committed + consistency check tells you exactly what to run; documented in `docs/architecture.md`.
- **Codex drift from repo doctrine** — global constraints above are copied verbatim into every workpackage detail plan; per-commit loop is the enforcement.
- **Python availability** — same bet memory_router already made (stdlib ≥ 3.9); no new dependency class.
