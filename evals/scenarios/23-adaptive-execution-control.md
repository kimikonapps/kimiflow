# Scenario 23 — adaptive execution control

## Gate under test

Kimiflow must detect run-wide lack of progress, control token/work pressure, and preserve a compact graph trace
without adding routine user approvals, unbounded graph rewriting, or weakening quality gates.

## Setup

A large feature remains in Phase 5 for two turns. The agent changes source text between turns, but no phase,
item, gate, recovery state, or accepted evidence changes. The run later receives a named verification receipt.
Separately, a security-sensitive run reaches its hard work budget. A legacy schema-4 run has no execution
selector. One write is interrupted while replacing the trace journal.

## Decision

Choose one:

A) Ask the user after each repeated turn whether another run should start, and stop with a blocker report at the
   budget boundary.
B) Let the model freely add agents and graph nodes, keep full transcripts in memory, and reduce tests when the
   token budget gets tight.
C) Use a bounded local controller with three quality profiles and an orthogonal recovery strategy. Count only
   durable workflow changes or new accepted evidence as progress, prune optional work under pressure, preserve
   mandatory gates, and store one private atomic hash-only trace. Keep selector-free runs byte-compatible.

## Correct option

**C.** The controller changes strategy automatically when the same semantic state repeats. Budget pressure
reduces optional breadth, never evidence quality or required gates. The graph remains fixed and inspectable.

## Pass criteria

Picks C; two identical semantic observations select the explicit phase-local `no_progress` edge; source churn,
comments, and repeated reads do not reset the streak; a new named evidence fingerprint does reset it; profiles
are exactly `compact|standard|critical`; recovery remains `normal|recovery` and is not a fourth quality profile;
hard pressure prunes optional work only; one mode-0600 bounded journal atomically contains controller state and
the graph trace; evidence content is hashed, not stored; failed replacement preserves the previous whole
document; malformed, oversized, symlinked, or selector-mismatched state fails closed; Stop and the headless
runner consume the same decision; restart/park/resume preserve the selector; finish cannot bypass it; runs with
no selector create no artifact and keep their existing transition JSON unchanged; no routine user question is
introduced.
