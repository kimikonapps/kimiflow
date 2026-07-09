<!-- kimiflow:phase-detail source=docs/render/kimiflow/canonical/SKILL.md -->

## 🟤 Phase 6 — Verify against acceptance criteria (goal-backward)

Run each check, prove the goal, and persist full evidence in the run artifacts — full checklist (incl. the ✓/⚠/✗ marking scheme, cold-start trigger list, LSP tool selection): → reference.md "Verification". If resolved verbosity is `quiet`, chat shows only pass/fail + command/path for decisive checks; full output stays in files.

- **Run each criterion's method** and show the command + the decisive result line(s) — not full logs. At `quiet`, show only the command label/status and artifact path unless a failure needs the decisive error line.
- **Goal-backward:** for each criterion's artifact check Exists / Substantive / Wired — "task done ≠ goal achieved".
- **Fix mode (mandatory):** the reproduction no longer fails. Then run `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/red-green-gate.sh .kimiflow/<slug> --mode fix` (Codex: `KIMIFLOW_HOST=codex` and `KIMIFLOW_PLUGIN_ROOT`). `OPEN` is required before Phase 7, memory promotion, or `Status: done`.
- **Local diagnostics advisory:** when code changed, run `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/lsp-diagnostics.sh` and append any `FLAG` lines to `.kimiflow/<slug>/ADVISORIES.md` (bounded existing local tools only, never installs).
- **Regression:** existing/affected test suite green.
- Cold-start smoke test when the diff touches boot-critical files; non-automatable criteria → a `top`/`cross_family_top` verifier subagent (derives pass/fail from evidence; does not trust the implementer's self-report). Mechanical commands and gates remain model-free.
- **(large) Independent verifier — additive:** one implementer-blind `cross_family_top` verifier when available, else a fresh same-family `top` verifier, re-derives the sweep and tries to falsify "done" claims. A discrepancy never bounces the run by itself — re-run the decisive command: confirmed → phase 5, else record the rejected claim and proceed.
- Any failure → back to phase 5 (escalation rule applies).
