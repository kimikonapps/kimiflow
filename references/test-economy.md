# Test economy

How an initialized project spends its test time. On a long project the test slot, not the planner or
the workers, decides how fast work lands; these rules keep it busy with runs that can still find
something. The project's numbers (commands, slots, quiet window) live in OPERATIONS.md § 4.

## Who runs what

- **Workers compile, they do not test.** One compile check before the report; the planner runs the
  tests. Exception, a fix round: only the red tests and their neighbours, at most three runs. Reason:
  parallel workers running suites compete for one machine and produce load reds.
- **Review runs parallel to the build.** The reviewer reads the diff while it builds; its fixes land
  before the package's single test run. Never build → test → review → fix → test again.
- **Merge first, test together.** A package whose only pending step is a test run goes into the
  integration branch with a compile check; the next combined run proves it. Own runs only for pieces
  that would block others if they were wrong.
- **Review fixes in batches.** Small review fixes run as parallel compile-only workers with disjoint
  files, then one combined run for all of them.

## Gates

- **Combined run after two or three merged packages** ([operating model](operating-model.md) §
  Integration), never per merge.
- **Gate = changed + affected + guards, never cumulative.** The set is the tests of changed files,
  tests that mention changed types, and the project's guard tests. Never the previous list plus new
  entries; a green test is not run again in the same batch unless its files changed since. Reason:
  cumulative lists grow until every gate is the full suite.
- **From the last fix to the main merge in under an hour, without losing quality** (owner rule
  2026-10-31). The pre-merge gate is the touched classes + guards in ONE run (≤ 40 min wall); the
  full suite and the measurement classes run only in the night before a beta, never per wave or
  per merge. Packages merge continuously as soon as they compile in the combined build — there is
  no end-of-wave fix round. Over an hour from last fix to main is a defect of the pipeline, not of
  the wave: audit the lanes and fix the pipeline first.
- **The test slot is the critical path.** Before a long measurement or perception gate, check what
  waits for the slot; run the gate smaller, or after the suite, rather than in front of it.
- **Small gate by default, full gate before a release.** A measurement or perception gate has a
  standard set that fits the slot (about an hour) and a full set for releases or when the owner asks.
- **Quiet window for numbers.** Performance and budget numbers count only on an otherwise idle
  machine (OPERATIONS.md § 4); structural counters gate, wall times only indicate.

## Reds

- **New red, same day.** A red that is not from the current package gets its own small fix package
  the same day. No list of "known reds": it only grows, and it hides the next real one.
- **Load reds.** A red seen in a busy run is re-run once alone from the same build: green alone is a
  `load-red`, red again a `real-red` (a bug, traced to its package). The runner does this, not the
  planner by hand. A test that is load-red repeatedly is fixed at its cause.
- **Held-back runs are visible.** A run deliberately held back (quiet window, waiting for another
  package) gets one line in HANDOFF.md or the runner's state: what it waits for, since when.

## Make the pipeline itself a product

Source: owner-supplied CI article, 2026-10-31. With agents writing the code, the pipeline is the bottleneck.

- **Audit before tuning.** Split the last runs into waiting, setup and running; the time is usually
  in setup and waiting, not in the tests. Deleting tests rarely buys time.
- **Waiting is part of the job.** A 5-second job that waits 10 minutes is a 10-minute job; no job
  stays on a slow lane or queue.
- **Parallelism = cores.** More cores beat more shards; shards beyond the cores spend their time loading.
- **Load once.** Do not start the app or test world per test file: one host per shard, reset hooks,
  and briefs say what a test must clean up.
- **Run what the change touches**; the full suite only in the night before a beta. Test the skip
  logic too: a change the selection misses must fail the gate. Measurement and timing classes
  never sit in a gate list or a guard set: they go to the quiet pre-beta night.
- **Cheap checks in a hook.** Lint, size and style guards and a typecheck of changed files run in a
  pre-commit or pre-push hook in seconds, not as a line in AGENTS.md; agents skip steps.
- **A rerun is not a fix.** Every rerun of a red carries a one-line cause.
- **Caches are seeded from the integration branch.** A cold cache once after a toolchain update is expected.

## Runner contract

One script starts every build and test run (template: `.kimiflow/ops/tools/run.sh`). Its only
stack-specific lines are the project's build and test commands and how to read red test ids from the
log; everything else is this contract:

- One log per run; the last line is `EXIT <code> after <n>s`. Waits watch for that line, with a time
  cap. Stop a run by killing its process group, not only the runner's pid.
- One test phase per machine: a lock directory (`mkdir`), a stale pid is taken over.
- At most `LANES` builds at once; the runner waits for a free lane.
- Test modes refuse without `TESTS_OK=1`, so a worker's compile check cannot turn into a test run.
- Reds re-run once alone (see Load reds) and are reported as `load-red:` / `real-red:` lines; the run
  exits 0 when every red was a load red.
- A small state file per run and phase, so a queue view or the planner can see what runs and waits.
