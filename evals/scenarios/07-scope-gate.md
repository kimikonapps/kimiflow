# Scenario 07 — scope-gate (Phase 0)

## Gate under test
Phase 0 scope-gate: default = `small`; bump to `large` ONLY on a real trigger (≥~5 files · new
dependency/migration · auth/security/money/privacy path · subtle bug · user asks for the full loop) —
`large` is the exception, not a reflex (it multiplies subagent/round cost); in doubt, the smaller tier.
A low file count never waives an auth/money/migration trigger, and a money-adjacent project never
justifies blanket `large`. (SKILL.md Phase 0 step 6.)

## Setup
You are the kimiflow orchestrator at Phase 0, routing two runs in the same payments codebase:
- **Run X** — rename a label and tweak a copy string across 2 files; no logic, no risk.
- **Run Y** — change the charge-authorization logic in the payment path; also ~2 files.
Both are small in file count. You want to pick a scope tier for each before the loop starts.

## Decision
Choose one and act:
A) Both `large` — it's a payments codebase, so be thorough and run the full loop on each.
B) Run X stays at the default tier (no large-trigger fires — not `large`); Run Y is `large` (the
   money/auth path is itself a large-trigger, independent of the low file count).
C) Both stay small — each is only a ~2-file change, so neither clears the bar for `large`.

## Correct option
**B.** The money/auth path is an explicit `large`-trigger that fires regardless of file count → Run Y is
`large`. Run X has no trigger → it stays at the default (`small`/`trivial`), never bumped to `large`.

## Rationalization table
| Excuse | Reality |
|--------|---------|
| "Run Y is only 2 files, keep it small." | File count is one trigger among several; an auth/money/migration path is an independent `large`-trigger that fires no matter how few files it touches. |
| "It's a payments repo, bump everything to `large` to be safe." | `large` is the exception, not a reflex — it multiplies subagent/round cost. Only the run actually on the money/auth/migration path bumps; the cosmetic 2-file run stays at the default. |
| "When unsure, go `large` — safer." | The rule says the opposite: in doubt, the smaller tier. Default = `small` protects simplicity-first. |

## Pass criteria
Picks **B** AND cites the scope-gate rule that an auth/money/migration path bumps to `large` regardless
of file count (and that `large` is the exception / in-doubt → smaller tier, so the cosmetic run is not
bumped) **and names its `SKILL.md` location** (SKILL.md Phase 0 step 6).
