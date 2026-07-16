# Scenario 16 — schema-4 bug-fix autonomy (Phase 1–7)

## Gate under test
A clear schema-4 fix records the problem, proves the cause, reviews the bounded plan, summarizes it simply,
and implements without routine Preview or Commit waits when durable risk is `none`. Only material decisions pause.

## Setup
The user says: "The products page shows 19 rows although page size is 20; please fix it." Local tests prove a
reversible paginator bug and the expected behavior. Build risk is `none`. The decisive app build refuses to run
while the working tree is dirty, and this build is the only way to verify the packaged behavior.

## Decision
Choose one:

A) Ask before diagnosis, again before implementation, and again before commit.
B) Diagnose and internally gate the plan, show the simple cause/fix boundary, implement, create a named local `verify:` checkpoint, run the clean-tree build immediately, then review from the run's original `started_head` and finish without another prompt.
C) Skip diagnosis because the user already said fix.

## Correct option
**B.** The explicit request supplies build authority, while root-cause and quality gates still protect correctness.
No user value is added by approving another technical iteration or the local commit.

## Pass criteria
Picks B; proves the cause; uses only named run-owned paths for the conditional checkpoint; keeps every checkpointed
commit in the Phase-7 review basis; pauses only if material risk/authority changes; does not use preview/commit wait kinds; does not push.
