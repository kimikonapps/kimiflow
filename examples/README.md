# examples — what a kimiflow run actually reads like

Three end-to-end walkthroughs of `/kimiflow` — both modes, across scope bands — so you can see the
phases, the gates and the artifacts **before** installing.

| File | Scope | Mode | What it shows |
|------|-------|------|---------------|
| [`01-small-fix.md`](01-small-fix.md) | `small` | fix | the lean path — direct diagnosis, one Fix Preview, 1 reviewer, commit-gate |
| [`02-risky-bugfix.md`](02-risky-bugfix.md) | `large` | fix | the full loop — proven cause, one Fix Preview, 2-reviewer plan-gate with a real HIGH, commit-gate |
| [`03-feature.md`](03-feature.md) | `large` | feature | adaptive Discovery, a proven architecture fork, Build Preview approval, and a non-blocking MEDIUM |

> **These are illustrative walkthroughs, not captured transcripts.** They are faithful
> reconstructions of how a run reads, built from the skill spec (`SKILL.md` / `reference.md`) — the
> phase order, gate decisions, finding format and artifact names are real, but the example project,
> file paths and `file:line` references are invented for illustration. For a recorded **real** run,
> see [`../docs/demo/`](../docs/demo/). For the honest outcome-quality log, see
> [`../evals/outcomes.md`](../evals/outcomes.md) (still 0 real rows — by design).

Legend for the phase markers used below: ⚪0 setup/scope · 🔵1 clarify · 🟣2 understand/diagnose ·
⚫3 plan · 🟡4 plan-gate · 🟠5 implement · 🟤6 verify · 🟢7 code-review→commit-gate. ✋ = a stop that
waits for you · ⛔ = a gate that can refuse · ✅ = a gate that passed.
