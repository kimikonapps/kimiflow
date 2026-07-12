<!-- kimiflow:phase-detail source=docs/render/kimiflow/canonical/SKILL.md -->

## 🔵 Phase 1 — Clarify (plain language): Intent (feature) or Problem (fix)

Goal: confirmed product intent BEFORE research/plan. Use plain language, WHAT/WHY not HOW, and ask only for irreducible product information. There is no minimum question count: when the request already covers behavior, scope, and the user-visible outcome, summarize those as recommended intent and ask one compact confirmation. Otherwise ask one decision-oriented question at a time and stop as soon as those dimensions are confirmed. Loose prior conversation is context, not current-run confirmation. Trivial exact work may skip. → reference.md "Intent clarification" / "Fix mode".

- **Feature → intent clarification:** clarify goal, value, in/out of scope, "what done looks like" → write `INTENT.md` → **gate** "Does this match?" (OK to continue).
- **Fix → problem clarification:** symptom, expected vs. actual, when/how it occurs (steps, logs, since when, always/intermittent) → write `PROBLEM.md` → **gate** "Did I understand the problem correctly?" (OK to continue).
- **Audit → scope clarification:** which paths, how aggressive, behavior-preserve constraints, do-NOT-touch hints, "what stays untouched" → write `AUDIT-INTENT.md` (plain language) → **gate** "Is this the right cleanup scope?" (OK to continue).
- **Mechanical clarify gate:** before Phase 2, run `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/clarify-gate.sh .kimiflow/<slug>` (Codex: `KIMIFLOW_HOST=codex` and `KIMIFLOW_PLUGIN_ROOT`). `OPEN` is required. For phase-read runs, first record fresh Phase 0/1 reads. For `small`/`quick`, the artifact carries `<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed source=current-run -->`. The gate checks completeness/current confirmation, never a question count; old valid count markers remain accepted for prepared runs. Phase 4 rechecks it.
