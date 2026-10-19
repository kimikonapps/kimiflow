---
name: kimiflow
description: "Operating kit and delivery support. Explicit Kimiflow or a project initialized with kimiflow init selects it; direct/direkt bypasses it. Elsewhere, discussion and ordinary fixes, reviews, refactors, docs and small changes stay direct."
disable-model-invocation: false
argument-hint: "[init | task]"
---

# Kimiflow

Work on **$ARGUMENTS**, or the task supplied by the host. Kimiflow is a set of documents and
conventions for planner-led project work; it adds no controller or run state.

## init

`/kimiflow init` or `/kimiflow:kimiflow init` sets the operating kit up in the current project:

1. Run `python3 <plugin-root>/scripts/kimiflow_ops.py init --root <project>`. `<plugin-root>` is the
   directory holding this SKILL.md. It resolves the main checkout (also from a worktree), copies the
   templates to `.kimiflow/ops/` without overwriting, adds `.kimiflow/` to Git's local exclude and
   prints a JSON report. Show the owner what was created, kept and listed as legacy.
2. Analyse the project: checks and CI, branch and commit convention, CLAUDE.md/AGENTS.md, existing
   backlogs. Fill the project sections of `.kimiflow/ops/OPERATIONS.md` from what you found; reasons
   and incidents go to `OPERATIONS-history.md`.
3. Propose, and apply only with the owner's OK: trimming CLAUDE.md to short, true rules;
   `additionalDirectories` for the main checkout when sessions run in worktrees; one AGENTS.md line
   pointing Codex to `.kimiflow/ops/OPERATIONS.md`.

## update

`/kimiflow update` runs `python3 <plugin-root>/scripts/kimiflow_ops.py update --root <project>`, which
writes nothing and reports each template as new, safe, conflict, unchanged or unknown_base. Show the
owner the report. Run it again with `--apply-safe` only with the owner's OK; merge conflicts and
unknown_base files by hand together with the owner, then record each with `--resolved <path>`.

## Initialized project

A project with `.kimiflow/ops/OPERATIONS.md` follows that file and
[the operating model](references/operating-model.md). Read OPERATIONS.md, then HANDOFF.md; grep LIST.md by ID.
The flow scales with risk; the gates are the ones the operating model names.

## Other projects

Briefly establish the requested outcome, observable acceptance, boundaries and relevant project rules.
Inspect current code before proposing a replacement. Reuse existing authorization; ask only about a
missing fact or consequence that materially changes the result. An empty invocation asks for the
task; it does not start a project scan.

Preserve unrelated work and staging. Use host worktrees or normal Git isolation when needed. Keep the
user's model and effort. Local models may need smaller coherent tasks, precise commands and compact
context, not a paid fallback or an additional workflow.

## References

- [Operating model](references/operating-model.md): roles, risk-scaled flow, briefs, reviews, handoff.
- [Feature work](references/feature-work.md): from an idea to a brief and a usable slice.
- [Planner-worker](references/planner-worker.md): delegation with explicit model choices.
- [Models](references/models.md): current Claude models per role and how to brief them.
- [Verification](references/verification.md): checks, `check_change.py`, delivery and commits.
- [Continuation](references/continuation.md): notes for long tasks; legacy Flow-schema runs.
