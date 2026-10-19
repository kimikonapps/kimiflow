---
name: kimiflow
description: "Operating kit and delivery support. Explicit Kimiflow or a project initialized with kimiflow init selects it; direct/direkt bypasses it. Elsewhere, ordinary fixes, reviews, refactors, docs, small changes and discussion stay direct."
---

# Kimiflow for Codex and Claude Code

Read `SKILL.md` at the installed plugin root once. The root is two levels above this file.
Use the user's chosen model and the host's own task history, plan mode, tools and workspace isolation.
Do not create phase state, a reviewer ensemble or memory ledgers. In a project whose kit was set up
locally by `kimiflow init` (`.kimiflow/` in Git's local exclude, no tracked kit files), follow
`.kimiflow/ops/OPERATIONS.md` and the installed `references/operating-model.md`;
its gates are the only ones. Resolve helper paths against the installed plugin, never a project's
similarly named scripts.

For user-selected delegation, follow the installed `references/planner-worker.md`. In Claude Code, run
the worker as a subagent with an explicitly chosen `model` (the same or a cheaper one), and keep planning
and acceptance in the session model; reviewers follow the project's role table, or the session model
outside an initialized project; a subagent with no model setting inherits the session model.
Model names and briefing tips are in the installed `references/models.md`. In Codex, use the host's
native delegation the same way. Use the host's worktree isolation only when a task needs it.

For legacy Flow-schema state or managed `workflow_context`, read installed `MIGRATION.md`; the old
controller is not part of this version. Other new tasks follow the short shared working agreement.
