---
name: kimiflow
description: "Minimal delivery support for substantial, authorized feature work with material integration, data, security, architecture or discovery risk. Explicit Kimiflow selects it; direct/direkt bypasses it. Discussion and ordinary fixes, reviews, refactors, docs and small changes stay direct."
disable-model-invocation: false
argument-hint: "[task]"
---

# Kimiflow

Work on **$ARGUMENTS**, or the task supplied by the host. Use the selected model, its tools and the
project's existing checks. Kimiflow adds a working agreement, not another controller.

## Working agreement

Briefly establish the requested outcome, observable acceptance, boundaries and relevant project rules.
Inspect current code before proposing a replacement. Reuse existing authorization; ask only about a
missing fact or consequence that materially changes the result. Planning and implementation depth are
up to the agent. An empty invocation asks for the task; it does not start a project scan.

For feature work, use [the short feature guide](references/feature-work.md): turn the user's idea
into a brief, test the largest uncertainty early, and deliver a usable slice before expanding it.
Use the existing conversation or host plan; this adds no approval gate or required artifact.

Preserve unrelated work and staging. Use host worktrees or normal Git isolation when needed; Kimiflow
owns no worktree fleet. Keep the user's model and effort. Local models may need smaller coherent tasks,
precise commands and compact context, not a paid fallback or an additional workflow.

## Verification

Use existing project tests and CI. Reproduce bugs, verify the changed behavior and relevant regressions,
and inspect the diff against acceptance. Use independent review when a concrete risk warrants it and
the host supports it; there is no fixed reviewer count. After repeated failure, change diagnosis or
approach instead of repeating the same assumption. Report an unresolved blocker honestly.

If existing tools already provide adequate verification, use them directly. Only when useful, the
installed `scripts/check_change.py` can run explicit argv checks and detect source differences between
snapshots before and after each check:

```bash
python3 /absolute/plugin/root/scripts/check_change.py --root /absolute/project --check '["npm","test"]'
```

Repeat `--check` for multiple checks. It writes no state and makes no commit. This is not continuous
monitoring or a security sandbox. Ignored build output and untracked `.kimiflow/` notes are excluded.
On POSIX it rejects and stops surviving members of the check's process group; detached processes
outside that group and changes restored between snapshots are outside its observation.

Deliver the result with actual checks and remaining limitations. Respect the project's commit policy;
when committing, include only intended paths and preserve foreign staging. Publication and irreversible
actions require their own authority. No memory maintenance or administrative artifact blocks completion.

## Continuation only when needed

Prefer the host's task history. For a long or interrupted task, optionally keep one local
`.kimiflow/<task>/NOTES.md`: goal and boundaries, current revision/paths, decisions, open work, last
checks. Keep it short and advisory; recheck current source before resuming. Before writing or resuming
notes, check for an existing `STATE.md` in that task directory. Do not create or convert a run schema.

A legacy `Flow schema` run or managed adapter request is **not** a notes-based task. Do not reinterpret,
advance or discard its state. Read [MIGRATION.md](MIGRATION.md) and use its matching development runtime.
No legacy engine, automatic updater or fallback controller is included here.
