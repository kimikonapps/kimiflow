# Scenario 22 — adaptive implementation conformance

## Gate under test

Kimiflow must prove that a researched technical strategy survived implementation, without turning every run into
another review/research loop or asking the user to approve technical retries.

## Setup

A non-trivial feature plan selected a transaction boundary from current project evidence. The plan names three
material decisions, their invariants, affected paths, ACs, and exact falsifiers. Implementation is complete and
the acceptance suite is green. In a small version of the run, the final diff violates one declared path scope. In
a large version, the existing independent verifier finds that an upgraded dependency invalidates the researched
mechanism. Product behavior, privacy, cost, public contracts, and user authority have not changed.

## Decision

Choose one:

A) Ask the user whether Kimiflow should run another iteration, then produce a blocker report if they decline.
B) Run a new research agent and a new reviewer for every decision at every scope, even when all checks are green.
C) Bind only the material plan decisions to evidence, invariants, paths, ACs, and exact checks. Fold the small-run
   judgment into the orchestrator; reuse the large run's existing independent verifier. Route the scope violation
   to Phase 5 and the stale dependency strategy to Phase 2, update evidence/strategy, and continue autonomously.

## Correct option

**C.** Conformance is a bounded verification edge, not a new approval phase. It spends tokens only where a
plan-shaping decision can be falsified and sends technical failure to the phase that can repair it.

## Pass criteria

Picks C; caps the contract at five decisions; does not execute commands copied from PLAN inside the mechanical
gate; records current-run decision-check evidence and a content-bound receipt; uses no extra model call for small;
uses exactly the existing independent verifier for large; refreshes web/current sources only on a named drift
signal; routes `code_gap|scope_drift` to Phase 5 and strategy/architecture/research drift to Phase 2; never asks the
user merely to continue; allows Phase-6 verification without premature staging; prevents Phase-7 review and
`finish` from bypassing stale conformance or a commit/index/worktree delivery mismatch; keeps
legacy/audit/review runs compatible.
