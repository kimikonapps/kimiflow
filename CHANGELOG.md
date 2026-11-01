# Changelog

The public release history starts at **0.5.0**. Earlier development artifacts are no longer listed as releases.

## Unreleased

_No unreleased changes._

## 0.8.0

Process lessons from long-running app work, written project-agnostically into the kit.

- Workers commit on their package's own branch (named paths, no AI attribution) and never merge,
  push or touch the main or an integration branch; the planner reviews the commits and merges.
- Worker brief: a goal checklist, ticked in the report with file:line or NOT DONE; a missing goal
  blocks the merge.
- Runner `tools/run.sh`: a test run releases its build lane before the test phase; one run per build
  output directory (new project line `BUILD_DIR`); optional fast lane (`SMALL_MAX`) for small test
  runs beside the big one; the state file names the run's directory.
- Test economy: gates and test runs on a snapshot worktree, no merge into a worktree a run uses;
  guards once per integration tip; a gate estimate before building (40-minute budget, split or
  nightly for a class over 10 minutes, measured time from last fix to main).
- Operating model: reuse search includes installed SDKs, the platform and open source (licence
  noted); estimates only from measured numbers; stall rule for silent run logs; long non-build jobs
  at low priority; the full suite only before a beta, also at block end.


Lessons from long-running app work, written project-agnostically into the kit.

- Add `references/test-economy.md`: workers compile only, review parallel to the build, merge first and
  test together, non-cumulative gates, one bundled full suite per main merge, new red same day, load
  reds, the pipeline as a product (audit waiting/setup/running, cores over shards, tested skip logic,
  cheap checks in a hook, a cause line per rerun) and the runner contract.
- Add the kit template `tools/run.sh`: a POSIX runner skeleton with one log per run ending in
  `EXIT <code> after <n>s`, build lanes, one test lock per machine, `TESTS_OK=1` for test modes and a
  load-red rerun. The project sets its build and test commands.
- Add the kit template `plans/build-order.md` and the rule to consolidate a batch before building.
- Operating model: big or risky packages get a worker-written design, one audit and a fresh build
  worker; acceptance greps for a second implementation and removed symbols; one merger per
  integration branch; relay only changes; ask at once for a missing report; learnings check at block
  end; a day-one checklist for a new project.
- Kit templates: OPERATIONS § 4 gains runner, slot, build-output, quiet-window, quick-path and
  owner-view fields; rules-worker gains § Code (central part, Removed list, shape and file-size caps),
  a context stop, golden files and waiting on the `EXIT` line; the worker brief names the central part
  and the Removed list.
- Git guard: also blocks `git add -u`/`--update`, AI co-author trailers (Claude, Codex) and a plain
  "Generated with Claude Code" footer.

## 0.6.2

Documentation consistency: the texts now match the 0.6.1 behaviour.

- Old plan versions go to `archive/`, as in the kit templates.
- The skill and host overlays follow `OPERATIONS.md` only for a kit set up locally by `kimiflow init`.
- Planning and acceptance stay in the session model; in an initialized project reviews follow the role
  table from a fresh context.
- `direct`/`direkt` bypasses the skill's flow; session-start context, git guard and `OPERATIONS.md`
  still apply in an initialized project.
- The architect has no Edit/Write tools and is read-only by instruction.
- Architecture and testing docs list the session-start size notices.

## 0.6.1

A size check keeps the always-read kit files short.

- Session start notes when `OPERATIONS.md` exceeds 12 KB (move reasons and history to
  `OPERATIONS-history.md`) or `HANDOFF.md` exceeds 6 KB (rewrite it to the current state, older parts
  to `archive/`). One line each, only for a locally set-up kit; nothing blocks.

## 0.6.0

Kimiflow becomes a project-agnostic operating kit plus delivery support.

- Add `/kimiflow init` (or `/kimiflow:kimiflow init`): `scripts/kimiflow_ops.py init` creates
  `.kimiflow/ops/` in the main checkout (OPERATIONS, OPERATIONS-history, rules-worker, HANDOFF, LIST,
  LIST-done, plan and brief templates), never overwrites and adds `.kimiflow/` to Git's local exclude.
  It refuses tracked kit files, symlinks leading outside the checkout and an earlier task named `ops`,
  and only reports legacy Flow-schema runs.
- Add `/kimiflow update`: reports template changes without writing; `--apply-safe` copies new and
  unedited files, `--resolved` records hand-merged ones. `.kit.json` records kit version and hashes.
- Add `references/operating-model.md`: roles, flow scaled by risk and the only gates (feature sheet
  OK, plan audit by risk, owner-items check before main).
- Claude Code only: a SessionStart hook that points each session, also in a worktree, at the kit,
  with notices for an older kit and for commits since HANDOFF.md changed; it speaks only for a kit
  set up locally by `init`.
- Claude Code only: a PreToolUse git guard that blocks bulk `git add` and AI attribution in commits,
  recognised by AI sender signatures; active only in initialized projects.
- Claude Code only: agents `kimiflow:worker`, `kimiflow:reviewer` and `kimiflow:architect`
  (read-only), without a fixed model.
- Add the dated `references/models.md` for the Claude 5 models. A worker may now use the same model
  as the planner; a cheaper worker model is an option, not a requirement.
- Rewrite the skill as a router for init, update, initialized projects and other projects; move
  verification and continuation details to `references/`.
- Hooks need `python3` on PATH and are tested on POSIX. Codex and Pi get the skill texts only.
- Update README, compatibility, migration and maintainer documentation.

## 0.5.3

Claude Code becomes a first-class host of the shared wrapper, without new moving parts.

- Make the shared `skills/kimiflow` wrapper host-neutral for Codex and Claude Code.
- State how Claude Code runs a cheaper worker: a subagent with an explicitly chosen `model`, while
  planning and review stay in the session model; a subagent without an explicit model is not cheaper.
- Add no new host, hooks, agents or renderer complexity.

## 0.5.2

Model-independent planner-worker collaboration focused on total delivery cost.

- Require a different, cheaper worker model for explicitly selected delegation.
- Use more capable planner/reviewer models; the planner normally performs the review.
- Review actual changes, integration, requirements and check evidence, including corrections.
- Select models using current availability, pricing and task suitability, without fixed model versions.
- Include planning, review and rework in cost assessment; no measured savings are claimed.
- Package the collaboration guide and update English/German documentation.

## 0.5.1

Small feature briefs, early usable results and lightweight feedback for better feature delivery.

- Add a short feature brief, early usable slices and explicit user-scenario acceptance.
- Provide an optional local five-feature results table without new workflow gates.

## 0.5.0

Initial release of the minimal Kimiflow working agreement for coding agents.

- A short shared skill for goals, acceptance criteria, project boundaries and verification.
- Codex, Claude Code and Pi entry points using the selected model and native host tools.
- Optional plain continuation notes for long or interrupted tasks.
- An optional stateless helper for explicit project checks, source snapshots and POSIX process cleanup.
- A 14-file plugin package with reproducible generation and a content fingerprint.
- 31 automated tests and CI coverage on Linux/Python 3.9 and macOS/Python 3.14.

The skill's additional benefit over native agent work has not yet been established by model benchmarks.
