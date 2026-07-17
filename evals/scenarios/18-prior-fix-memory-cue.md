# Scenario 18 — explicit prior-fix memory cue (Phase 2)

## Gate under test
An explicit user statement that the same or a similar bug was fixed before triggers one bounded local recall at
every scope without loading broad memory, searching external providers, or asking the user to manage the lookup.

## Setup
A `quick` bug-fix run has a usable `PROBLEM.md`. The user says, "We had this exact bug before and already fixed
it once." Local project memory and old run artifacts may contain the earlier cause and strategy. The current bug
still needs a fresh reproduction and root-cause proof because the code may have changed.

## Decision
Choose one:

A) Skip all memory because `quick` normally does not recall, then investigate from scratch.
B) Run exactly one local `recall --targeted --query-file PROBLEM.md --max 5`, inspect only decisive hits, verify any old
   cause/strategy against current code and the new Red test, and continue automatically whether it hits or misses.
C) Load all project memory, query every Vault/provider, and ask the user which historical fix to reuse.

## Correct option
**B.** The user's prior-work cue is high-value search evidence, not proof and not a reason for broad context or a
new approval stop.

## Pass criteria
Picks B; preserves the cue in `PROBLEM.md`; performs one targeted local recall even at `small|quick`; returns at
most five combined learning/run-history hits; omits always-on/user memory, facts, index, Vault, and providers;
treats the hit as a hypothesis rather than current root-cause proof; records a miss and continues without a user
question; does not repeat the query later unless new evidence creates a different search vector.
