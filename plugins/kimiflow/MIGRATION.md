# Compatibility with development installations

The public release series starts at 0.5.0. Earlier development releases and version tags have been
removed from the public release list. Existing Git history and local user data are retained.

## 0.5 to 0.6

- Continuation notes in `.kimiflow/<task>/NOTES.md` stay valid; `init` does not read, move or change
  them. In an initialized project, HANDOFF.md carries the state between sessions.
- `init` uses `.kimiflow/ops/`. If an earlier task named `ops` left `STATE.md` or `NOTES.md` there,
  `init` stops without writing; rename or move that task directory, then run it again.
- Legacy Flow-schema runs (`.kimiflow/<task>/STATE.md`) are listed in the `init` report and left
  untouched. Handle them as described below.
- The operating kit is optional. Without `init`, the short working agreement applies as before.

## 0.6 to 0.7

- `kimiflow update` reports `plans/build-order.md` and `tools/run.sh` as new, and `OPERATIONS.md`,
  `rules-worker.md` and `plans/worker-brief.md` as changed: safe where the project never edited them,
  otherwise conflict with a diff. Merge the new sections into the project's filled-in files by hand,
  then record each with `--resolved <path>`.
- The git guard now also blocks `git add -u`/`--update`; stage named paths instead.

## 0.7 to 0.8

- Workers now commit on their package's own branch; the planner reviews and merges instead of
  committing worker diffs. Briefs that told workers not to commit can drop that line.
- `kimiflow update` reports `tools/run.sh`, `OPERATIONS.md`, `rules-worker.md`, `plans/worker-brief.md`
  and `plans/plan.md` as changed. A filled-in `run.sh` needs the new project line `BUILD_DIR`; test
  mode now runs `BUILD_CMD` in a build lane first and `TEST_CMD` after releasing it, so `TEST_CMD`
  should run on that build without rebuilding where the stack allows. Merge by hand where the project
  edited the file, then record it with `--resolved <path>`.

## Existing managed work

Some development installations used Flow-schema runs, managed adapters, hooks, memory or worktree
controllers. The public core does not implement those interfaces. Do not reinterpret their state as
new continuation notes, mark them complete without evidence, or discard their data.

Finish such work with its original installation. If that runtime is missing, its last development
source is preserved at commit `c0849d69985074e9a8a7d5e5d5fac2cdd2f824b2` in this repository's history.
Use a separate checkout of that exact commit for recovery; do not overwrite the current project or
assume a deleted release/tag is still downloadable. Applications requiring the old adapter-v1 or
managed update-v1 contracts must not automatically switch to the public core.

Keep project checkouts, `.kimiflow/` state, `~/.kimiflow/` memory, external Vault data and worktrees intact.
After old work is finished, remove only its obsolete hook/CLI registrations through that installation's
documented procedure. Never clear unrelated host hooks. The public plugin registers no services; in
Claude Code it registers two hooks (session start and git guard), see [compatibility](COMPATIBILITY.md).

The public check helper is `python3 <installed-root>/scripts/check_change.py`. It is optional and does
not migrate state, install old runtimes or provide a fallback controller.
