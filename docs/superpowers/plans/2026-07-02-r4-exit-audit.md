# R4 Exit Audit

Date: 2026-07-02

Scope: exit audit for `docs/superpowers/plans/2026-07-02-rebuild-program.md` after R2 Group B, R3 render hardening, and R4 budget checks.

## Verdict

Exit gate is not fully open for the original rebuild program.

Mechanical gates are green, and the confirmed R4 implementation bugs found by the exit audit were fixed:

- Render drift check now uses `kimiflow_core.render --check` and does not overwrite manual output drift.
- Launcher default/pretty output budgets are asserted in `hooks/test-launcher-status.sh`.
- `release-consistency-check.sh` enforces byte ceilings for always-loaded skill files, `phases/*.md`, and launcher output.
- Python-ported helpers now carry their R2 invariant targets in the production Python modules instead of Bash shim comments.
- Render sources are named as canonical workflow plus Codex host overlay.

Open program gap:

- `SKILL.md` is still 54,126 bytes, not the intended thin 10-15K always-loaded driver. The R2 approval packet explicitly approved only Group B movement; Groups A/C/D were not safely requested in that packet, and the phase files remain R2.3 skeletons. Completing the original prose inversion still requires a protected-rule movement plan for phase detail / smoke contracts / reference repartitioning and another invariant-preserving move series.

## Evidence

- `bash docs/superpowers/plans/2026-07-02-invariant-check.sh` -> `INVARIANTS OK`
- `bash hooks/release-consistency-check.sh` -> all version/render/budget checks consistent
- `bash hooks/test-release-consistency-check.sh` -> includes unstaged render-drift and phase-budget regressions
- `bash hooks/test-launcher-status.sh` -> includes default/pretty byte budget checks
- Full hook loop excluding production hooks reports `hook test failures: 0`
