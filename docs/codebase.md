# Codebase

- `docs/render/kimiflow/`: editable shared skill and host wrapper sources.
- `SKILL.md`, `skills/kimiflow/`, `hosts/pi/skills/kimiflow/`: generated installed skills.
- `references/`: operating model, model guide, planner-worker, feature work, verification and
  continuation texts the skill links to.
- `templates/ops/`: the operating kit that `init` copies to a project's `.kimiflow/ops/`.
- `scripts/kimiflow_ops.py`: `init`, `update` and the two Claude Code hook entry points
  (`session-start`, `git-guard`).
- `claude/hooks.json`, `claude/agents/`: Claude Code hooks and agents, registered only by
  `.claude-plugin/plugin.json`.
- `scripts/check_change.py`: optional runtime verifier.
- `scripts/render_skills.py`, `scripts/build_plugin.py`: maintainer-only generation and package checks.
- `tests/`: behavior tests for the ops script, the verifier and packaging boundaries.
- `plugins/kimiflow/`: generated runtime, never edited separately.
- `evals/`: simple comparative-evaluation guidance and preserved historical pilot evidence.
- `MIGRATION.md`: handling of existing development installations and of 0.5 notes.

`kimiflow_ops.py` reads and writes only `.kimiflow/ops/` and Git's local exclude file. No code reads
or writes other `.kimiflow/` task directories; it only lists legacy `STATE.md` runs. Project data
stays local.
