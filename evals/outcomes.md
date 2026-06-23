# Outcomes — does kimiflow produce better results than a plain session?

The scenario suite (`scenarios/`) tests **gate integrity** — do the gates hold under pressure. It does
NOT measure **outcome quality**: whether a kimiflow run actually ships better code than an ordinary
Claude Code session would have. This file is the honest, lightweight log for that question — **field
notes, not a benchmark**. No marketing number is derived from it until the table below has real rows.

## Status

**Comparisons recorded: 0.** Nothing here yet — rows accrue from real runs. Until then kimiflow makes
**no claim** of better outcomes, and the README must not cite this file while it is empty.

## Method (keep it small + honest)

For ~5–10 real bugs/features, capture both paths on the *same* task:

- **kimiflow path** — the actual `/kimiflow` run.
- **plain path** — what a normal Claude Code session delivered (or a fair estimate, marked as such).

Record only what you can back with evidence (commit/PR links, review findings, post-merge bug reports).
A missing cell is `—`, never a guess. Skews and confounds go in **Notes**.

## Metrics

| Field | Meaning |
|-------|---------|
| Task | one line + link (commit/PR/issue) |
| Path | `kimiflow` or `plain` |
| Rework rounds | revise→re-gate / re-review cycles before done |
| Defects caught in review | genuine BLOCKER/HIGH found *before* merge (not style) |
| Post-merge bugs | defects found after merge, within ~7–30 days |
| Approx. tokens | from `LEDGER.md` if available, else `—` |
| Notes | confounds, scope, why the comparison is / isn't fair |

## Comparisons

<!-- TEMPLATE — copy the two-row block per task (same task on both paths so rows compare).
     Delete this comment and the template rows when the first REAL comparison lands.
     Evidence or `—`, never a guess. -->

| Task | Path | Rework rounds | Defects caught in review | Post-merge bugs | Approx. tokens | Notes |
|------|------|---------------|--------------------------|-----------------|----------------|-------|
| _fix NPE in checkout (#123)_ | kimiflow | _2_ | _1 (HIGH: wrong root cause)_ | _0_ | _~48k_ | _TEMPLATE ROW — not real data_ |
| _fix NPE in checkout (#123)_ | plain | _—_ | _—_ | _—_ | _—_ | _TEMPLATE ROW — not real data_ |

## Reading the result (once there is data)

kimiflow "wins" only if its **cost** buys a measurable drop in post-merge bugs / rework — not just more
process. If the columns don't show that, say so here and prefer the cheaper path. Honest null results
stay in this file.
