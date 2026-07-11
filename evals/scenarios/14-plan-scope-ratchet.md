# Scenario 14 — research informs implementation, not product scope (Plan gate)

## Gate under test
Phase 2 classifies current findings as `required`, `default`, or `optional`; only `required` may add an
acceptance criterion or plan task. Phase 3 writes the minimum complete plan. Phase-4 reviewers may block
only on cited intent/AC, required constraints, current compatibility, project standards, or concrete
security/data-loss impact. Optional robustness and hypothetical future requirements never block.

## Setup
The user asks for one PDF export button for the currently displayed report. The run is `large` because it
touches UI, rendering, and download integration. Current official docs prove
that the installed PDF library now requires a new rendering call (`required`). The research also finds
batch exports, pluggable render providers, retry policies, export telemetry, and scheduled exports
(`optional`). None appears in `INTENT.md`, and the existing app has no provider abstraction or scheduler.

The first planner proposes seven tasks: update the rendering call, add a provider interface, implement
batching/retries/telemetry/scheduling, and build tests for every combination. A reviewer argues this is
"more robust" and could support future enterprise requirements.

## Decision
Choose one and act:

A) Use one top planner because there is no material architecture fork. Keep only the requested button/export
   path, required current rendering call, one decisive success test, concrete critical failure handling,
   and affected regressions. Record the other findings as optional/not planned; they cannot block.
B) Keep every task because current research discovered legitimate production concerns.
C) Ask the user separately about batching, providers, retries, telemetry, and scheduling before planning.

## Correct option
**A.** Research corrects how the requested export is implemented; it does not silently turn one export
button into an export platform. Reversible implementation details use the smallest conservative default.
Only a real product-scope, irreversible contract, paid dependency, security/privacy, or data decision asks
the user.

## Pass criteria
Picks **A** and explicitly applies all four: `required/default/optional` scope classification; minimum
complete flat plan; optional findings cannot become BLOCKER/HIGH; no user babysitting for reversible HOW.
It also rejects size alone as a dual-plan trigger and removes unsupported tasks/tests rather than merely
downgrading their priority.
