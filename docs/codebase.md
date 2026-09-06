# Codebase

- `docs/render/kimiflow/`: editable shared skill and host wrapper sources.
- `SKILL.md`, `skills/kimiflow/`, `hosts/pi/skills/kimiflow/`: generated installed skills.
- `scripts/check_change.py`: optional runtime verifier.
- `scripts/render_skills.py`, `scripts/build_plugin.py`: maintainer-only generation and package checks.
- `tests/`: behavior tests for the surviving verifier and packaging boundaries.
- `plugins/kimiflow/`: generated runtime, never edited separately.
- `evals/`: simple comparative-evaluation guidance and preserved historical pilot evidence.
- `MIGRATION.md`: handling of existing development installations.

No code reads or writes `.kimiflow/` as a managed control plane. Existing user data stays local.
