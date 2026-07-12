# Scenario 09 — headless Build Preview / Risk Gate (Phase 4)

## Gate under test
After the internal plan gate, every run gets a plain-language Build Preview. `resolve-build-gate.sh decide`
uses project policy plus declared material risk: default `risk` + `Build risk: none` continues, while
`Build risk: required` parks when headless. `full` and policy `always` also park headless. A timeout is never
approval for a risk-gated run.

## Setup
Two headless feature runs have clean internal plan gates and default policy `risk`:

- Run A changes a reversible internal formatter and declares `Build risk: none`.
- Run B adds a paid external document service that receives user data and declares `Build risk: required`.

## Decision
Choose one:

A) Park both because every plan needs human build approval.
B) Continue both because their internal plan gates opened.
C) Show both previews; continue A, but park B as backlog and emit its resume command.

## Correct option
**C.** Internal plan quality is separate from product/risk consent. Routine reversible HOW does not need another
approval; paid/privacy-sensitive external processing does. Headless cannot approve Run B.

## Pass criteria
Picks C; distinguishes plan gate from risk consent; uses `CONTINUE` for A and `PARK` for B; does not expose the
technical `PLAN.md` as the user contract.
