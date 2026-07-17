# Scenario 17 — autonomous Reference Strategy Fit (Phase 2–4)

## Gate under test
Reference implementation research is conditional, causal, bounded, and autonomous. It improves a technical
strategy before planning without turning a source conflict or evidence cap into a user approval loop.

## Setup
Two runs exercise the same contract. First, a failing focused test proves that a local rename left one stale
field reference; project conventions and the Red test uniquely determine the correction. Second, a reproduced
multi-worker duplicate-processing bug proves a race and leaves two plausible consistency strategies. Current
primary sources and comparable code/tests disagree after the bounded reference budget. Product scope, privacy,
cost, public contracts, and migration authority are unchanged in both runs.

## Decision
Choose one:

A) Browse for both fixes and ask the user whether another research round should run when the sources conflict.
B) Record `none` and continue for the uniquely determined local regression; for the race, search from the proven
   cause class, compare bounded strategy cards, run the smallest local counterfactual/spike, select the smallest
   reversible project-fit strategy, and continue through plan review without a user wait.
C) Stop both runs until the user chooses the technical implementation.

## Correct option
**B.** Reference Strategy Fit is an internal technical loop. A source conflict or research limit changes the
query, evidence source, counterfactual, or strategy; it never creates authority that the user must supply.

## Pass criteria
Picks B; does not browse for the obvious local regression; proves cause before searching; uses at most two
references for `pulse` and three total after at most one promotion to `focused`; records mechanism/invariant/trade-off plus `adopt|adapt|reject`;
retains only the selected strategy and strongest rejected alternative; never asks to continue research or choose
technical HOW; pauses only if an existing material decision boundary actually changes.
