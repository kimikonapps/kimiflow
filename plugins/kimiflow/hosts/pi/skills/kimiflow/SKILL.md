---
name: kimiflow
description: Lean verified feature/fix delivery in Pi, with optional explicitly requested managed FirstMate crews.
---

# Kimiflow for Pi

Read `SKILL.md` at the loaded Kimiflow package root once. Ordinary Pi and local-model tasks use its
short Understand → Build → Verify loop, the selected model, and the host's own tools. No crew,
external provider, phase state or learning ledger is required.

Use absolute paths rooted at the loaded package, never the current project's hooks or skill files.
`hooks/check-change.sh` executes explicit project checks; a local model uses the same command contract.
Keep context small and useful; do not infer ability or permission from a model name.

Only when the user explicitly requests a managed FirstMate crew, or `KIMIFLOW_CREW_ROLE` identifies
an existing Captain/Main/Worker, read `references/legacy-pi.md` and follow that exact role contract.
An existing run with `Flow schema` uses `references/legacy-workflow.md`; do not initialize a competing
lean task in its worktree or change its stored authority. A missing crew is a reported limitation,
not permission to create another transport.
