# Operations — <project>

Binding rules, short form. Reasons, incidents and flakes: `OPERATIONS-history.md` (grep the rule name, never read it whole).
Topic files (`rules-<topic>.md`) only where a section separates cleanly; each one is listed in the register.

## Rule register

One line per rule: `NAME — rule — where it lives`. Check here whether a rule on a topic already exists
before adding one; extend or centralize it instead of writing a second.

- ONE SOURCE — search before adding a control, model, state or service — OPERATIONS § 5
- DEFERRED IN LIST — every deferral goes into LIST.md when decided — OPERATIONS § 5
- FEATURE SHEET FIRST — a wish starts shaping; the plan follows the owner's OK — OPERATIONS § 1
- BRIEF CHECK — code claims in briefs are grep-verified — OPERATIONS § 3
- WORKER STANDING RULES — scope, Git, RED, waits, stopping, report — rules-worker.md
- FLAKES — re-run alone before calling a red test a defect — OPERATIONS § 6, OPERATIONS-history.md
- TEST ECONOMY — workers compile only; gates = changed + affected + guards; new red same day — installed `references/test-economy.md`, numbers in OPERATIONS § 4
- GIT GUARD — stage named paths only; no AI attribution in commits — enforced by the kimiflow hook
- `<NAME>` — `<rule>` — `<OPERATIONS § n / rules-worker.md / rules-<topic>.md>`

## 0 · Quick start

- State and next steps: `HANDOFF.md` (read after this file). The one list: `LIST.md`. Feature
  sheets, plans, briefs: `plans/`. Old versions: `archive/`.
- LIST: search by ID or status, never read it whole. Owner items before every test build, main merge
  and handoff: `grep -nE '\[(NEXT|OPEN|IN PROGRESS)\].*[Oo]wner' LIST.md`
- COMMUNICATION: answer the owner in the owner's language; short and structured — result, what the
  owner must do, what is open.

## 1 · Roles and flow

The planner session plans, briefs, accepts and merges; workers implement and commit on their package
branch; reviewers and auditors check; an architect only for hard cross-cutting questions. The flow
scales with risk; details in the installed `references/operating-model.md`. Model notes: the installed `references/models.md`.

| Role | Model | Effort | Why |
| --- | --- | --- | --- |
| Planner (session) | `<model>` | `<effort>` | `<reason>` |
| Worker | `<model>` | `<effort>` | `<reason>` |
| Reviewer / auditor | `<model>` | `<effort>` | `<reason>` |
| Architect (rare) | `<model>` | `<effort>` | `<reason>` |

Claude Code ships the agents `kimiflow:worker`, `kimiflow:reviewer` and `kimiflow:architect`; pass the
table's model as the Agent `model` parameter when starting one.

- FULL FLOW for: `<data/disk writes, migrations, security, public API, deletes, ...>`. Unsure → full flow.
- FEATURE SHEET FIRST: a wish starts a short shaping conversation; the plan follows the owner's OK.

## 2 · Hard safety rules

- DATA: `<what must never be lost or overwritten, and how it is protected>`
- GIT: never stage `.env`, keys or credentials; stage named paths only.
- `<rule>`

## 3 · Briefs

- Standing worker, reviewer and auditor rules: `rules-worker.md`. Every brief links it; none copies it.
- BRIEF CHECK: every "the code does X today" claim is grep-verified before the brief goes out.
- MODEL: start each subagent with the explicit `model` from the table in § 1.

## 4 · Paths, tools, checks

- Tests: `<test command>` · Lint/format: `<command>` · Build: `<command>` · CI: `<where>`
- Branches: `<feat/<slug> → int/<batch> → main>` · Commits: `<convention>`
- Runner: `<sh .kimiflow/ops/tools/run.sh <log> build|test>` (log ends `EXIT <code> after <n>s`) · Slots:
  tests `<1>`, builds `<LANES>` · Build output: `<dir>` · Guard tests: `<list or file>`
- Quiet window (performance numbers only then): `<when>` · Quick path for an owner bug: `<slot / command>`
  · Owner view: `<local URL or none>`

## 5 · Project rules

- ONE SOURCE: search before adding a control, model, state or service; one source of truth per state.
- DEFERRED IN LIST: every deferral goes into LIST.md the moment it is decided.
- `<RULE NAME>: <rule>`

## 6 · Flakes

A red test is no defect until re-run alone; red twice alone → own bug. Known flakes: grep the test name in OPERATIONS-history.md § Flakes.
