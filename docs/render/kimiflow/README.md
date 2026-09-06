# Kimiflow Skill Render Sources

`docs/render/kimiflow/` is the edit source for the committed host skill files:

- `canonical/SKILL.md` renders to repository-root `SKILL.md`.
- `overlays/codex.md` renders to `skills/kimiflow/SKILL.md`.

The canonical workflow is the lean default for fresh work. Host overlays supply only paths and tool
substitutions. `references/legacy-workflow.md`, `legacy-codex.md` and `legacy-pi.md` preserve the managed
workflow for existing Flow-schema runs and explicitly requested controllers. The old phase files and
scaling rules belong to that compatibility surface; do not preload them for the lean default.

Render after source edits:

```bash
PYTHONPATH="$PWD/hooks" python3 -m kimiflow_core.render
```

`hooks/release-consistency-check.sh` checks these rendered files and fails when the committed outputs drift.
