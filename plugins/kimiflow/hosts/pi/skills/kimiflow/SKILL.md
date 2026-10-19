---
name: kimiflow
description: "Operating kit and delivery support in Pi with the selected model and native tools. Explicit Kimiflow or a project initialized with kimiflow init selects it; no crew, provider routing or workflow controller."
---

# Kimiflow for Pi

Read `SKILL.md` at the installed package root once and follow it; a project whose kit was set up locally by
`kimiflow init` (`.kimiflow/` in Git's local exclude, no tracked kit files) follows its
`.kimiflow/ops/OPERATIONS.md`. Keep the selected
model and native task/tools; no FirstMate crew is activated. Helper paths belong to the installed
package, not the current project.

A `KIMIFLOW_CREW_ROLE` context, Kimiflow adapter-v1 context or Flow-schema run belongs to an earlier development runtime. Read
`MIGRATION.md` instead of trying to continue it with this package. Never create competing run state.
