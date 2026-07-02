# R2 Approval Packet — Explicit Stop Before Rule Movement

**Date:** 2026-07-02 · **Branch:** `rebuild/r2-prose` · **Status:** approval required before any protected-rule movement.

## Scope

- R2.1 inventoried the protected corpus; R2.2 made invariants target-mapped; R2.3 added phase-read enforcement.
- No `SKILL.md` or `reference.md` protected prose has been moved in R2 so far.
- This packet asks for explicit approval only. Broad autonomous execution is not treated as approval for movement.
- If a group is approved, the next work must move that group in its own commit series and update `2026-07-02-invariant-targets.tsv` in the same commit as the prose move.

## Approval Choices

- **Approve Group B**: move only the gate-backed rows listed in the Group B table to their production hook/code target, retaining concise `SKILL.md` pointers.
- **Defer Group B**: keep all rows in their current targets and stop R2 movement.
- **Partial Group B approval**: approve only named IDs from the Group B table.

Groups A, C, and D have no safe movement request in this packet.

## Group A — Phase-Detail Movement

**Approval requested:** none.

R2.3 created `phases/PHASES.json` and skeletal phase files, but R2.1 classified no protected corpus row as `PHASE-ONDEMAND`. Finish-time blocking is only a backstop; no protected Phase 2/3/5/6/7 rule gets moved by this packet. Any future Phase A packet must name a pre-action `enforcement_boundary` per row.

## Group B — Gate-Backed Movement

**Approval requested:** yes, for the rows below only.

Movement contract for each approved row:

- Authoritative target becomes the listed production hook/code file, never a test file alone.
- `SKILL.md` keeps a short operational pointer naming the hook/command.
- The invariant target row moves in the same commit as the prose movement.
- The listed focused verification plus the full R2 verification loop must pass.

| ID | Current target | Proposed authoritative target | Retained always-loaded pointer | Verification command | Protected needle |
|---|---|---|---|---|---|
| `INV-030` | `SKILL.md` | `hooks/active-run.sh` | Keep a one-line `SKILL.md` pointer to `hooks/active-run.sh` / command. | `bash hooks/test-active-run.sh; bash hooks/test-kimiflow-core-parity.sh` | `hooks/active-run.sh start --run .kimiflow/<slug>` |
| `INV-031` | `SKILL.md` | `hooks/background-run.sh` | Keep a one-line `SKILL.md` pointer to `hooks/background-run.sh` / command. | `bash hooks/test-background-run.sh; bash hooks/test-kimiflow-core-parity.sh` | `hooks/background-run.sh` |
| `INV-033` | `SKILL.md` | `hooks/agentic-readiness.sh` | Keep a one-line `SKILL.md` pointer to `hooks/agentic-readiness.sh` / command. | `bash hooks/test-agentic-readiness.sh` | `hooks/agentic-readiness.sh status\|gate` |
| `INV-036` | `SKILL.md` | `hooks/launcher-status.sh` | Keep a one-line `SKILL.md` pointer to `hooks/launcher-status.sh` / command. | `bash hooks/test-launcher-status.sh; bash hooks/test-kimiflow-core-parity.sh` | `hooks/launcher-status.sh --pretty` |
| `INV-038` | `SKILL.md` | `hooks/working-tree-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/working-tree-gate.sh` / command. | `bash hooks/test-working-tree-gate.sh` | `hooks/working-tree-gate.sh` |
| `INV-042` | `SKILL.md` | `hooks/active-run.sh` | Keep a one-line `SKILL.md` pointer to `hooks/active-run.sh` / command. | `bash hooks/test-active-run.sh` | `refresh-baseline --write` |
| `INV-045` | `SKILL.md` | `hooks/resolve-verbosity.sh` | Keep a one-line `SKILL.md` pointer to `hooks/resolve-verbosity.sh` / command. | `bash hooks/test-resolve-verbosity.sh` | `resolve-verbosity.sh` |
| `INV-047` | `SKILL.md` | `hooks/project-map-status.sh` | Keep a one-line `SKILL.md` pointer to `hooks/project-map-status.sh` / command. | `bash hooks/test-project-map-status.sh; bash hooks/test-kimiflow-core-parity.sh` | `project-map-status.sh` |
| `INV-048` | `SKILL.md` | `hooks/project-map-status.sh` | Keep a one-line `SKILL.md` pointer to `hooks/project-map-status.sh` / command. | `bash hooks/test-project-map-status.sh` | `PMS coverage --affected` |
| `INV-054` | `SKILL.md` | `hooks/clarify-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/clarify-gate.sh` / command. | `bash hooks/test-clarify-gate.sh` | `hooks/clarify-gate.sh` |
| `INV-056` | `SKILL.md` | `hooks/memory-router.sh` | Keep a one-line `SKILL.md` pointer to `hooks/memory-router.sh` / command. | `bash hooks/test-memory-router-unit.sh; bash hooks/test-memory-router-parity.sh` | `hooks/memory-router.sh status` |
| `INV-057` | `SKILL.md` | `hooks/memory-router.sh` | Keep a one-line `SKILL.md` pointer to `hooks/memory-router.sh` / command. | `bash hooks/test-memory-router-unit.sh; bash hooks/test-memory-router-parity.sh` | `MR recall --query-file` |
| `INV-059` | `SKILL.md` | `hooks/current-state-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/current-state-gate.sh` / command. | `bash hooks/test-current-state-gate.sh` | `current-state-gate.sh` |
| `INV-060` | `SKILL.md` | `hooks/current-state-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/current-state-gate.sh` / command. | `bash hooks/test-current-state-gate.sh` | `CSG verify --assessment` |
| `INV-061` | `SKILL.md` | `hooks/suggest-affected-sections.sh` | Keep a one-line `SKILL.md` pointer to `hooks/suggest-affected-sections.sh` / command. | `bash hooks/test-suggest-affected-sections.sh` | `suggest-affected-sections.sh --intent` |
| `INV-073` | `SKILL.md` | `hooks/plan-blocker-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/plan-blocker-gate.sh` / command. | `bash hooks/test-plan-blocker-gate.sh` | `hooks/plan-blocker-gate.sh` |
| `INV-078` | `SKILL.md` | `hooks/resolve-review-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/resolve-review-gate.sh` / command. | `bash hooks/test-resolve-review-gate.sh` | `hooks/resolve-review-gate.sh` |
| `INV-079` | `SKILL.md` | `hooks/resolve-review-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/resolve-review-gate.sh` / command. | `bash hooks/test-resolve-review-gate.sh` | `--round <N> --expect <lensCSV>` |
| `INV-083` | `SKILL.md` | `hooks/resolve-build-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/resolve-build-gate.sh` / command. | `bash hooks/test-resolve-build-gate.sh` | `resolve-build-gate.sh` |
| `INV-089` | `SKILL.md` | `hooks/red-green-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/red-green-gate.sh` / command. | `bash hooks/test-red-green-gate.sh` | `hooks/red-green-gate.sh` |
| `INV-091` | `SKILL.md` | `hooks/lsp-diagnostics.sh` | Keep a one-line `SKILL.md` pointer to `hooks/lsp-diagnostics.sh` / command. | `bash hooks/test-lsp-diagnostics.sh` | `hooks/lsp-diagnostics.sh` |
| `INV-094` | `SKILL.md` | `hooks/agentic-readiness.sh` | Keep a one-line `SKILL.md` pointer to `hooks/agentic-readiness.sh` / command. | `bash hooks/test-agentic-readiness.sh` | `--kind review --write` |
| `INV-103` | `SKILL.md` | `hooks/secret-content-scan.sh` | Keep a one-line `SKILL.md` pointer to `hooks/secret-content-scan.sh` / command. | `bash hooks/test-secret-content-scan.sh` | `secret-content-scan.sh` |
| `INV-104` | `SKILL.md` | `hooks/resolve-review-gate.sh` | Keep a one-line `SKILL.md` pointer to `hooks/resolve-review-gate.sh` / command. | `bash hooks/test-resolve-review-gate.sh` | `--expect code-verified` |
| `INV-109` | `SKILL.md` | `hooks/memory-router.sh` | Keep a one-line `SKILL.md` pointer to `hooks/memory-router.sh` / command. | `bash hooks/test-memory-router-unit.sh; bash hooks/test-memory-router-parity.sh` | `MR review-run --run` |
| `INV-115` | `SKILL.md` | `hooks/project-map-status.sh` | Keep a one-line `SKILL.md` pointer to `hooks/project-map-status.sh` / command. | `bash hooks/test-project-map-status.sh` | `PMS refresh --changed` |
| `INV-116` | `SKILL.md` | `hooks/map-staleness-nudge.sh` | Keep a one-line `SKILL.md` pointer to `hooks/map-staleness-nudge.sh` / command. | `bash hooks/test-map-staleness-nudge.sh` | `map-staleness-nudge.sh` |
| `INV-117` | `SKILL.md` | `hooks/improvements-status.sh` | Keep a one-line `SKILL.md` pointer to `hooks/improvements-status.sh` / command. | `bash hooks/test-improvements-status.sh` | `mark-done <id> --commit <sha> --write` |
| `INV-119` | `SKILL.md` | `hooks/improvements-staleness-nudge.sh` | Keep a one-line `SKILL.md` pointer to `hooks/improvements-staleness-nudge.sh` / command. | `bash hooks/test-improvements-staleness-nudge.sh` | `improvements-staleness-nudge.sh` |
| `SMOKE-SKILL-007` | `SKILL.md` | `hooks/improvements-status.sh` | Keep a one-line `SKILL.md` pointer to `hooks/improvements-status.sh` / command. | `bash hooks/test-improvements-status.sh; bash hooks/smoke-install.sh; bash hooks/smoke-install-codex.sh` | `improvements-status.sh` |

### Group B Holds

| ID | Current target | Hold reason | Protected needle |
|---|---|---|---|
| `INV-102` | `SKILL.md` | held: current proposed target would be hooks/test-weakening-scan.sh, which the R2.2 checker correctly rejects as a test-only authoritative target for a production/prohibition rule | `test-weakening-scan.sh` |
| `INV-004` | `SKILL.md` | held: `core-always-or-approved-target` / `CORE-ALWAYS+GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `STOP at the pre-build approval gate` |
| `INV-021` | `SKILL.md` | held: `core-always-or-approved-target` / `GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `Gate verdict = ONE line` |
| `INV-024` | `SKILL.md` | held: `core-always-or-approved-target` / `CORE-ALWAYS+GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `never for gate criteria, scores, or thresholds` |
| `INV-029` | `SKILL.md` | held: `core-always-or-approved-target` / `GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `blocks the review-gate call` |
| `INV-080` | `SKILL.md` | held: `core-always-or-approved-target` / `CORE-ALWAYS+GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `stop + ask, gate CLOSED` |
| `INV-090` | `SKILL.md` | held: `core-always-or-approved-target` / `GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `--mode fix` |
| `INV-120` | `SKILL.md` | held: `core-always-or-approved-target` / `CORE-ALWAYS+GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `Only after the commit gate and learning review are open` |
| `INV-121` | `SKILL.md` | held: `core-always-or-approved-target` / `CORE-ALWAYS+GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `never gates, cost, quality, or behavior` |
| `SMOKE-SKILL-012` | `SKILL.md` | held: `core-always-or-approved-target` / `GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | `Current-State Pulse / Gate` |
| `SMOKE-REF-006` | `reference.md` | held: `literal-smoke-target` / `SMOKE-CONTRACT+CORE-ALWAYS+GATE-CHECKABLE` stays CORE-ALWAYS unless a later packet proves a safe pre-action target | ``kimiflow plan` — clarify + understand + plan + plan-gate, then park/resume, no code.` |

## Group C — Smoke-Contract Rewrites

**Approval requested:** none.

Current smoke-contract rows remain in their current targets. No smoke phrase rewrite is needed for R2 movement in this packet.

| ID | Current target | Protected needle |
|---|---|---|
| `SMOKE-SKILL-001` | `SKILL.md` | `Launcher / menu` |
| `SMOKE-SKILL-014` | `SKILL.md` | `Memory Router & Learning Loop` |
| `SMOKE-SKILL-015` | `SKILL.md` | `code-review ensemble` |
| `SMOKE-REF-005` | `reference.md` | ``kimiflow grill` — clarify/spec only, no code.` |
| `SMOKE-REF-006` | `reference.md` | ``kimiflow plan` — clarify + understand + plan + plan-gate, then park/resume, no code.` |
| `SMOKE-REF-007` | `reference.md` | ``kimiflow review` — read-only existing-feature/current-change review, no code.` |
| `SMOKE-REF-008` | `reference.md` | ``kimiflow audit` — read-only cleanup/refactoring scan first, no code until a slice is approved.` |
| `SMOKE-REF-009` | `reference.md` | `CANDIDATE <SEVERITY> <ref> :: <claim> :: verify=<smallest check>` |

## Group D — Reference/Doc Repartitioning

**Approval requested:** none.

R2.1 produced no protected `REFERENCE-DETAIL` corpus rows. Existing `reference.md` smoke/reference rows stay in place unless a later approval packet creates a concrete, invariant-targeted movement plan.

## Required Post-Approval Verification

Every approved movement commit must run:

- `bash docs/superpowers/plans/2026-07-02-invariant-check.sh`
- `bash hooks/test-invariant-check.sh`
- affected hook tests listed above
- full discovered hook loop excluding `test-gate.sh` / `test-weakening-scan.sh`
- `bash hooks/smoke-install.sh`
- `bash hooks/smoke-install-codex.sh`
- `bash hooks/release-consistency-check.sh`
- `bash -n hooks/*.sh docs/superpowers/plans/2026-07-02-invariant-check.sh`
- `shellcheck --severity=error hooks/*.sh docs/superpowers/plans/2026-07-02-invariant-check.sh`
- `python3 -m py_compile hooks/kimiflow_core/*.py hooks/kimiflow_core/tests/*.py`
- `git diff --check`

## Stop

STOP: do not move protected rule prose until the user explicitly approves Group B, rejects it, or names a partial list of approved IDs.
