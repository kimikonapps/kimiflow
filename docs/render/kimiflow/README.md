# Kimiflow Skill Render Sources

`docs/render/kimiflow/` is the edit source for the committed host skill files:

- `claude/SKILL.md` renders to repository-root `SKILL.md`.
- `codex/SKILL.md` renders to `skills/kimiflow/SKILL.md`.

Render after source edits:

```bash
PYTHONPATH="$PWD/hooks" python3 -m kimiflow_core.render
```

`hooks/release-consistency-check.sh` re-renders these files and fails when the committed outputs drift.
