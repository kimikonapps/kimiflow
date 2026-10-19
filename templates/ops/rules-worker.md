# Standing rules for workers, reviewers and auditors — <project>

Every worker, reviewer and auditor brief starts with "Read `<absolute path>/rules-worker.md` first".
This file is the one source for these rules, so briefs only carry what is specific to their task.
Change a rule here, never by copying it into a brief.

## Scope

- Search only inside the project, its worktrees and the session scratch space. Never read the
  owner's other data (other drives, network shares, home folders) unless the owner names it. The
  owner's other data is private, and wide searches are slow.
- Touch only the files the brief allows. If the task cannot be done within them, stop and report;
  the planner decides about a wider scope.

## Git

- Never commit, stage or push, and never change Git history. The planner reviews the diff and
  commits, so the history stays one reviewed line.

## Tests

- A behaviour change starts with a test that fails on the current code with an assertion. A compile
  error or a missing module is not red. Report the failing output; it shows the test covers the change.
- A fix covers the inverse case too: edited and unedited, failure and success. Fixes often break the
  path nobody looked at.

## Waits and scratch files

- Every wait loop has a time cap, so a hung build or test cannot block the session for good.
- Scratch files use the prefix given in the brief, so parallel work does not collide.

## Stopping

- Stop and report after two failed attempts at the same problem, when a stop condition from the brief
  is hit, or when the brief contradicts the real code. A fresh look from the planner is cheaper
  than a third guess.

## Report

1. Changed files: one line each.
2. Checks: each command with its real result. A check that was not run is reported as not run.
3. Open points: unresolved issues, assumptions, deviations from the brief.

Reviewers and auditors follow the finding schema of their brief instead of points 1 and 2.

## Project checks and style

- Tests: `<test command>` · Lint/format: `<command>` · Build: `<command>`
- `<file-size cap, forbidden APIs, naming or style rules the project enforces>`
