# Skill sources

Edit `canonical/SKILL.md` and the host-specific `overlays/codex.md` / `overlays/pi.md`.
Run `python3 scripts/render_skills.py` from the repository root, then
`python3 scripts/build_plugin.py`. CI checks source parity and the exact runtime allowlist.
Do not edit the generated copies independently.
