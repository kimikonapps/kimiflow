# Kimiflow Skill Render Sources

`docs/render/kimiflow/` is the edit source for the committed host skill files:

- `canonical/SKILL.md` renders to repository-root `SKILL.md`.
- `overlays/codex.md` renders to `skills/kimiflow/SKILL.md`.

The canonical workflow lives in `canonical/SKILL.md`. Host overlays contain host-specific invocation,
path, and tool substitutions; they must point back to the canonical workflow instead of forking it.

Render after source edits:

```bash
PYTHONPATH="$PWD/hooks" python3 -m kimiflow_core.render
```

`hooks/release-consistency-check.sh` checks these rendered files and fails when the committed outputs drift.
