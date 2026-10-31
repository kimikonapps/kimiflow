# Kimiflow operating model

How a project initialized with `kimiflow init` works. These rules come from long-running app work with
a planner session and subagents. Each rule carries its reason, so it can be applied with judgement.
The project's `.kimiflow/ops/` holds the documents named here.

## Roles

- **Planner** (the session): shapes features with the owner, plans, writes briefs, accepts results
  and commits. It keeps the long context, so it should spend it on decisions, not on file dumps.
- **Worker** (subagent): implements one bounded brief and runs the checks. Start it with an explicit
  `model` from the project's role table; without one it may inherit the session model.
- **Reviewer / auditor** (subagent): checks a plan or a diff from a fresh context, which catches
  what the author no longer sees.
- **Architect**: only for hard cross-cutting or hard-to-reverse questions. It advises; the planner
  decides, the owner decides product questions.
- In Claude Code the plugin ships these as the agents `kimiflow:worker`, `kimiflow:reviewer` and
  `kimiflow:architect`, without a model of their own; the planner passes the role table's model.
- A Claude Code hook (git guard) blocks `git add -A`/`--all`/`.` and AI attribution in commit messages
  in an initialized project, so these two rules hold without anyone remembering them.

## Process scales with risk

| Task | Flow |
| --- | --- |
| Data or disk writes, migrations, security, public API, deletes | Full flow: feature sheet → plan (design by a worker) → one audit → fresh worker → review |
| UI, display or performance without such writes | Short plan → worker → one review |
| Bug fix | Reproduce (RED) → fix → review |
| Unsure | Full flow |

**Big or risky packages.** A worker writes the design and grep-verifies every fact of the brief
first; one audit checks it, and its corrections are appended to the design as binding. Only open
BLOCKER/HIGH findings get a re-check; still open after that → architect or owner. A fresh worker builds
from the design file, and the review runs on the diff while it builds ([test economy](test-economy.md)).
Reason: the design worker's context is full of exploration, a fresh one builds from the agreed text;
repeated audit rounds cost more than they find.

The gates are exactly these: the owner's OK on the feature sheet, a plan audit where the risk calls
for it, and the owner-items check (the LIST grep) before a merge to main. Small corrections need none of them.

## Shaping and planning

- **Feature shaping first.** A wish is not a plan start. Lead a short conversation with
  recommendations and their costs; the result is a feature sheet (`plans/feature-sheet.md`). The plan
  starts after the owner's OK. Exceptions: bug fixes, small corrections of decided things, questions.
  Reason: most rework comes from building the wrong thing well.
- **One source.** Before any new control, model, state or service, search for an existing one. Each
  state has one source of truth. Plans name what was searched, what was found, and whether it is
  reused or why something new is needed. In review, a duplicate is at least MEDIUM. Reason: duplicates
  drift apart and produce bugs that look unrelated.
- **Consolidate before build.** After a batch of items, group them, put shared parts first, cut
  parallel lanes by file ownership and fix the order in `plans/build-order.md`; then act. The planner
  decides this and does not ask. Reason: items built one by one touch the same files twice and
  collide in parallel.
- **Plans are rewritten, not stacked.** Old versions go to `archive/`. Reason: a reader should
  see one current plan, not reconstruct it from revisions.

## Briefs and workers

- **Brief check.** Every "the code does X today" claim in a brief is grep-verified by the planner
  before the worker starts. Reason: a wrong premise costs a full worker round.
- **RED is an assertion.** A test for new behaviour must fail on the old code with an assertion; a
  compile error is not red. Acceptance checks the evidence. Reason: a test that never failed may not
  test the change.
- **Acceptance greps.** A brief names the central part to use (file:line). Before merge, grep for a
  second implementation of it and for zero references to removed symbols. Reason: copies and
  leftovers pass every test and drift apart later.
- **Fixes go back to the same worker**, which keeps its context. After two rounds, re-cut the brief or
  take the work over.
- **Fix briefs demand the inverse case too:** edited and unedited, failure and success. Reason: fixes
  often break the path nobody looked at.
- **Rules as guard tests where possible:** file-size caps, forbidden APIs, single-source checks. A test
  keeps a rule alive after everybody has forgotten it.

## Reviews and findings

- **Central fix after a repeat.** When a review finds the same pattern at a new call site, move the
  rule to the one choke point and propose a guard test. Reason: fixing call sites one by one never ends.
- **Collect while the owner tests.** Findings from a test build are collected, then bundled into one
  block, instead of interrupting work item by item.
- **Merge on intake.** Every new finding is checked against open LIST items and bundled with a match
  (`→ package <ID>`). Reason: one package per area means one review and one test run.
- **Deferred is never forgotten.** Every deferral, known limit or accepted LOW goes into LIST.md the
  moment it is decided: ID, what, why, trigger.

## Integration

- Branches: `feat/<slug>` → integration branch `int/<batch>` → main.
- Each package has its own RED, its own tests and its own review.
- After two or three merged packages: a broad test run and a release build. At block end: the full
  suite and a test build. A red test is traced to its package before anything else moves.
- **Test economy.** Who runs which tests, what a gate contains, how reds are handled and what the
  project's runner guarantees: [test economy](test-economy.md).
- **Owner-items check** before every test build, main merge and handoff:
  `grep -nE '\[(NEXT|OPEN|IN PROGRESS)\].*[Oo]wner' LIST.md`. Reason: owner decisions get lost in a
  long list unless something asks for them.

## Block end and continuity

- Rewrite HANDOFF.md as one current block; the old version goes to `archive/`.
- Tell the owner it is a good moment for a fresh session, with a one-line start prompt. A fresh session
  reads OPERATIONS.md and HANDOFF.md and continues without the old context.
- Session start names how many commits landed since HANDOFF.md was last updated: a stale handoff is
  worse than none, because the next session trusts it.
- `kimiflow update` compares the project's kit files with newer shipped templates; it replaces only
  files the owner never edited, and only with the owner's OK.
- Session start speaks only for a kit set up locally by init, so a cloned repository cannot promote
  its own files to operating rules.

## Autonomous and night runs

- Take your own recommendation, record it as "default, owner can change", and keep going.
- Questions go into the morning summary instead of stopping the run.
- Keep a wake source while agents run, so the machine does not sleep mid-run.

## Load and waits

- Limit parallel builds and test runs; they compete for the same machine.
- Every wait loop has a time cap.
- Workers use unique scratch file names, so parallel work does not collide.
- Search only inside the project, its worktrees and the session scratch space; never read the
  owner's other data (other drives, network shares, home folders) unless the owner names it.

## Rules and documents

- Owner decisions and rules carry a date and the reason, so they can be revisited when the reason
  no longer holds.
- CLAUDE.md stays short and true; edit it only with the owner's OK. Detailed inventories load per
  path instead of at every session start.
- Keep OPERATIONS short: rules there, reasons and incidents in OPERATIONS-history.md, looked up by
  rule name. Every session reads OPERATIONS.md, so each extra line costs every session.
- `rules-worker.md` is the single source for the standing worker, reviewer and auditor rules. Briefs
  link it instead of copying it, because copies drift and cost time to rewrite. The rule register at
  the top of OPERATIONS.md lists every rule and where it lives, so a new rule is checked against
  existing ones first. Split a topic into its own `rules-<topic>.md` only when it separates cleanly.
- HANDOFF.md: current state only. LIST.md: the one list. `plans/`: feature sheets, plans, briefs, build order.
  `research/`: findings worth keeping.
- Session start warns when OPERATIONS.md exceeds 12 KB or HANDOFF.md exceeds 6 KB.

## Communication

Answer the owner in the owner's language. Short and structured: the result, what the owner must do,
what is open.

Project-specific rules live in the project's OPERATIONS.md.
