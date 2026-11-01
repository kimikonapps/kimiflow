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

- Commit your work on the package's own feature or fix branch as soon as it builds: named paths
  only, no AI attribution, `git status` and `git diff --staged` read first. A finished package never
  stays uncommitted, so "is it on the branch?" always has a true answer.
- Never commit to the main or an integration branch; never merge, push, stash or change Git history.
  The planner reviews the commits and merges, so the integration line stays one reviewed history.

## Tests

- A behaviour change starts with a test that fails on the current code with an assertion. A compile
  error or a missing module is not red. Report the failing output; it shows the test covers the change.
- A fix covers the inverse case too: edited and unedited, failure and success. Fixes often break the
  path nobody looked at.
- Golden files and recorded baselines are never re-pinned without the owner's OK; a changed golden
  is a changed behaviour.

## Code

- Use the central part the brief names; generalise it, never copy it. A second version of the same
  thing is a finding in review.
- Remove the orphans of your own change, grep replaced symbols to zero hits and report a Removed list.
  Other dead code: list it, do not remove it.
- Shape: guard returns, at most `<3>` nesting levels, functions up to about `<40>` lines.
- File size: new files at most `<400>` lines; a file already over the cap does not grow (new logic goes
  into a new file). Splitting a big file is its own neutral step, named in the brief. Add only your
  own entries to a size guard's exception list, each with a reason.

## Waits and scratch files

- Every wait loop has a time cap, so a hung build or test cannot block the session for good.
- Wait for a run's log to end with `EXIT <code>`; stop a run by killing its process group, so no
  child keeps running beside the next one.
- Scratch files use the prefix given in the brief, so parallel work does not collide.

## Stopping

- Stop and report after two failed attempts at the same problem, when a stop condition from the brief
  is hit, or when the brief contradicts the real code. A fresh look from the planner is cheaper
  than a third guess.
- Near about 150k tokens of context, stop at a compiling point, note what is left and report. A
  worker past that point loses track of its own changes; a fresh worker continues from the note.

## Report

1. Goal checklist first: every point of the brief's checklist ticked with file:line, or NOT DONE and
   why. Leaving a goal out ("scope cut", "belongs to another package") is never the worker's call;
   report it as NOT DONE and the planner asks the owner.
2. Changed files: one line each; the Removed list.
3. Checks: each command with its real result. A check that was not run is reported as not run.
4. Open points: unresolved issues, assumptions, deviations from the brief.

Reviewers and auditors follow the finding schema of their brief instead of points 1 to 3.

## Project checks and style

- Tests: `<test command>` · Lint/format: `<command>` · Build: `<command>`
- `<file-size cap, forbidden APIs, naming or style rules the project enforces>`
