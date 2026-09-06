#!/usr/bin/env bash
# kimiflow — Codex install smoke-test. Verifies the Codex plugin layer, skill
# entrypoint, bundled plugin hook wiring, and synthetic
# Codex-shaped hook payloads.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILS=0
cv="$(jq -r '.version' "$ROOT/.codex-plugin/plugin.json")"
ok()  { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }

command -v jq  >/dev/null 2>&1 || { echo "smoke-install-codex: jq required"; exit 2; }
command -v git >/dev/null 2>&1 || { echo "smoke-install-codex: git required"; exit 2; }

# Fresh default and legacy invariants are verified separately.
if bash "$ROOT/hooks/smoke-default.sh" --host codex; then
  ok "lean default installation and executable checks"
else
  bad "lean default installation failed"
fi

echo "== retained managed-run compatibility checks =="
if [ -x "$ROOT/hooks/launcher-status.sh" ] && bash -n "$ROOT/hooks/launcher-status.sh" 2>/dev/null; then ok "launcher status helper ok"; else bad "launcher status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-launcher-status.sh" ] && bash -n "$ROOT/hooks/test-launcher-status.sh" 2>/dev/null; then ok "launcher status test ok"; else bad "launcher status test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/active-run.sh" ] && bash -n "$ROOT/hooks/active-run.sh" 2>/dev/null; then ok "active session helper ok"; else bad "active session helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-active-run.sh" ] && bash -n "$ROOT/hooks/test-active-run.sh" 2>/dev/null; then ok "active session test ok"; else bad "active session test missing/not-exec/bad"; fi
if [ -f "$ROOT/hooks/kimiflow_core/execution_control.py" ] \
  && [ -x "$ROOT/hooks/test-execution-control.sh" ] \
  && grep -q '"execution_control"' "$ROOT/phases/PHASES.json" \
  && grep -q 'Execution contract: 1' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Adaptive Execution contracts' "$ROOT/references/legacy-codex.md"; then
  ok "Codex adaptive execution controller wiring"
else
  bad "Codex adaptive execution controller wiring incomplete"
fi
if [ -x "$ROOT/hooks/adapter-conformance.sh" ] \
  && [ -x "$ROOT/hooks/kimiflow-mcp.sh" ] \
  && [ -x "$ROOT/hooks/code-intelligence.sh" ] \
  && [ -s "$ROOT/references/adapter-conformance-v1.schema.json" ] \
  && [ -s "$ROOT/references/code-intelligence-provider-v1.schema.json" ] \
  && grep -q 'kimiflow_status' "$ROOT/hooks/kimiflow_core/mcp_server.py" \
  ; then
  ok "Codex provider-neutral MCP, conformance and code-intelligence wiring"
else
  bad "Codex provider-neutral MCP, conformance or code-intelligence wiring incomplete"
fi
grep -q 'Project Map Bootstrap' "$ROOT/references/legacy-workflow.md" && ok "canonical Project Map Bootstrap present" || bad "canonical Project Map Bootstrap missing"
grep -q 'FACTS.jsonl' "$ROOT/reference.md" && ok "project map evidence artifact documented" || bad "project map evidence artifact missing"
if [ -x "$ROOT/hooks/project-map-status.sh" ] && bash -n "$ROOT/hooks/project-map-status.sh" 2>/dev/null; then ok "project map status helper ok"; else bad "project map status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-project-map-status.sh" ] && bash -n "$ROOT/hooks/test-project-map-status.sh" 2>/dev/null; then ok "project map status test ok"; else bad "project map status test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/suggest-affected-sections.sh" ] && bash -n "$ROOT/hooks/suggest-affected-sections.sh" 2>/dev/null; then ok "suggest-affected helper ok"; else bad "suggest-affected helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-suggest-affected-sections.sh" ] && bash -n "$ROOT/hooks/test-suggest-affected-sections.sh" 2>/dev/null; then ok "suggest-affected test ok"; else bad "suggest-affected test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/map-staleness-nudge.sh" ] && bash -n "$ROOT/hooks/map-staleness-nudge.sh" 2>/dev/null; then ok "map staleness nudge helper ok"; else bad "map staleness nudge helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-map-staleness-nudge.sh" ] && bash -n "$ROOT/hooks/test-map-staleness-nudge.sh" 2>/dev/null; then ok "map staleness nudge test ok"; else bad "map staleness nudge test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/current-state-gate.sh" ] \
  && bash -n "$ROOT/hooks/current-state-gate.sh" 2>/dev/null \
  && PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import current_state' 2>/dev/null; then
  ok "current-state gate helper ok"
else
  bad "current-state gate helper missing/not-exec/unloadable"
fi
if [ -x "$ROOT/hooks/test-current-state-gate.sh" ] && bash -n "$ROOT/hooks/test-current-state-gate.sh" 2>/dev/null; then ok "current-state gate test ok"; else bad "current-state gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/review-convergence-gate.sh" ] \
  && bash -n "$ROOT/hooks/review-convergence-gate.sh" 2>/dev/null \
  && PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import review_convergence; assert callable(review_convergence.delta); assert review_convergence.REVIEW_CLOSEOUT_ROUND == 4; assert "cascade_candidate_file" in review_convergence.SATURATION_V4_KEYS; assert not hasattr(review_convergence, "SATURATION_V3_KEYS")' 2>/dev/null \
  && grep -q 'schema-4 saturation' "$ROOT/phases/phase-7-review-commit.md" \
  && grep -q 'material-decision-required' "$ROOT/phases/phase-7-review-commit.md"; then
  ok "review convergence gate helper ok"
else
  bad "review convergence gate helper missing/not-exec/unloadable"
fi
if [ -x "$ROOT/hooks/test-review-convergence-gate.sh" ] && bash -n "$ROOT/hooks/test-review-convergence-gate.sh" 2>/dev/null; then ok "review convergence gate test ok"; else bad "review convergence gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/discovery-gate.sh" ] && bash -n "$ROOT/hooks/discovery-gate.sh" 2>/dev/null; then ok "discovery gate helper ok"; else bad "discovery gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-discovery-gate.sh" ] && bash -n "$ROOT/hooks/test-discovery-gate.sh" 2>/dev/null; then ok "discovery gate test ok"; else bad "discovery gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/working-tree-gate.sh" ] && bash -n "$ROOT/hooks/working-tree-gate.sh" 2>/dev/null; then ok "working-tree gate helper ok"; else bad "working-tree gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-working-tree-gate.sh" ] && bash -n "$ROOT/hooks/test-working-tree-gate.sh" 2>/dev/null; then ok "working-tree gate test ok"; else bad "working-tree gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/workspace-preflight.sh" ] && bash -n "$ROOT/hooks/workspace-preflight.sh" 2>/dev/null; then ok "workspace preflight helper ok"; else bad "workspace preflight helper missing/not-exec/bad"; fi
if [ -f "$ROOT/hooks/kimiflow_core/worktree_broker.py" ] \
  && PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import worktree_broker; assert worktree_broker.BROKER_SCHEMA == 2; assert worktree_broker.MAX_WORKTREES == 3; assert worktree_broker.BROKER_NAME == "FLEET.json"; assert "needs-reconcile" in worktree_broker.TASK_STATES' 2>/dev/null \
  && grep -q 'route --run .kimiflow/<slug>' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'at most three identity-locked' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'blocked_by.*revalidate.*needs-reconcile' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'write-gate --run .kimiflow/<slug>' "$ROOT/phases/phase-5-build.md"; then
  ok "Codex autonomous worktree broker wiring"
else
  bad "Codex autonomous worktree broker wiring incomplete"
fi
if [ -x "$ROOT/hooks/ci-test-plan.sh" ] && bash -n "$ROOT/hooks/ci-test-plan.sh" 2>/dev/null && PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import ci_test_plan' 2>/dev/null; then ok "CI test plan helper ok"; else bad "CI test plan helper missing/not-exec/unloadable"; fi
if [ -x "$ROOT/hooks/behavior-eval-receipt.sh" ] && bash -n "$ROOT/hooks/behavior-eval-receipt.sh" 2>/dev/null && PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import eval_receipt' 2>/dev/null; then ok "behavior eval receipt helper ok"; else bad "behavior eval receipt helper missing/not-exec/unloadable"; fi
if grep -q 'show one plain summary' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'route --run .kimiflow/<slug> --write' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'never ask merely to create, queue, retry, integrate, or retire' "$ROOT/phases/phase-0-setup.md"; then
  ok "Codex autonomous workspace route and bounded foreign decision"
else
  bad "Codex autonomous workspace route or bounded foreign decision is incomplete"
fi
grep -q 'Only after step 3.55' "$ROOT/phases/phase-0-setup.md" && ok "frontend baseline follows workspace closure" || bad "frontend baseline timing bypasses workspace closure"
if grep -Fqi 'use a separate git worktree' "$ROOT/hooks/kimiflow_core/active_run.py"; then bad "non-owner hook suggests an unguarded worktree"; else ok "non-owner hook preserves exceptional-worktree authority"; fi
if grep -Ei 'code-review-audit|full.*one mode-specific Preview approval|explicit Build Preview approval|one post-diagnosis Fix Preview|ask the user to commit/stash/clean first|fix defers approval|Do not ask for approval yet|Freigabe vor Build|riskante Entscheidungen und Commits|One question at a time|STOP and ask the user to switch|STOP before Phase 0|asks for a Sol session|until a \*\*human\*\*|one defined exception.*commit-gate|Build/Fix Preview control|full waits once|clean-worktree|human commit-gate|STOPS for your OK|waits for your explicit OK|Never auto-commits|best-of-2 implementer|second best-of-2 candidate|own worktree|commit-without-OK' "$ROOT/README.md" "$ROOT/README.de.md" "$ROOT/.claude-plugin/plugin.json" "$ROOT/.claude-plugin/marketplace.json" "$ROOT/.codex-plugin/plugin.json" "$ROOT/docs/render/kimiflow/canonical/SKILL.md" "$ROOT/phases/phase-1-clarify.md" "$ROOT/phases/phase-2-understand.md" "$ROOT/phases/phase-5-build.md" "$ROOT/evals/scenarios/08-advisory-triage-failclosed.md" "$ROOT/evals/scenarios/13-top-model-orchestrator.md" "$ROOT/evals/scenarios/15-evidence-guided-discovery.md" "$ROOT/docs/render/kimiflow/overlays/codex.md" "$ROOT/reference.md" "$ROOT/docs/architecture.md" "$ROOT/docs/demo/play.sh" "$ROOT/docs/demo/README.md" "$ROOT/examples/README.md" "$ROOT/examples/01-small-fix.md" "$ROOT/examples/02-risky-bugfix.md" "$ROOT/examples/03-feature.md" "$ROOT/docs/kimiflow-vs-claude-md-vs-superpowers.md" >/dev/null; then bad "stale schema4 babysitting guidance remains"; else ok "schema4 babysitting guidance removed"; fi
grep -q 'automatisch geroutete' "$ROOT/docs/architecture.md" && grep -q 'automatically routed' "$ROOT/docs/kimiflow-vs-claude-md-vs-superpowers.md" && ok "maintainer docs preserve automatic routing" || bad "maintainer docs lost automatic routing"
if [ -x "$ROOT/hooks/clarify-gate.sh" ] && bash -n "$ROOT/hooks/clarify-gate.sh" 2>/dev/null; then ok "clarify gate helper ok"; else bad "clarify gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-clarify-gate.sh" ] && bash -n "$ROOT/hooks/test-clarify-gate.sh" 2>/dev/null; then ok "clarify gate test ok"; else bad "clarify gate test missing/not-exec/bad"; fi
if grep -q 'Intent contract: 4' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'One final product-contract confirmation' "$ROOT/phases/phase-4-review-approval.md" \
  && grep -q 'Product flow entry' "$ROOT/reference.md" \
  && grep -q 'Interaction language:' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'question_rounds=1' "$ROOT/reference.md" \
  && grep -q 'technical_questions=0' "$ROOT/reference.md" \
  && grep -q 'intent_coverage_missing' "$ROOT/hooks/clarify-gate.sh" \
  && grep -q 'contract4_schema2_lock_binds_scope_final_and_language' "$ROOT/hooks/test-clarify-gate.sh" \
  && grep -q 'product-intent ownership' "$ROOT/evals/README.md"; then
  ok "product intent ownership and mandatory-intake autonomy"
else
  bad "product intent ownership contract incomplete"
fi
if [ -x "$ROOT/hooks/plan-blocker-gate.sh" ] && bash -n "$ROOT/hooks/plan-blocker-gate.sh" 2>/dev/null; then ok "plan-blocker gate helper ok"; else bad "plan-blocker gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/plan-review-gate.sh" ] && bash -n "$ROOT/hooks/plan-review-gate.sh" 2>/dev/null; then ok "plan-review gate helper ok"; else bad "plan-review gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-plan-blocker-gate.sh" ] && bash -n "$ROOT/hooks/test-plan-blocker-gate.sh" 2>/dev/null; then ok "plan-blocker gate test ok"; else bad "plan-blocker gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/codebase-basis.sh" ] \
  && bash -n "$ROOT/hooks/codebase-basis.sh" 2>/dev/null \
  && PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import codebase_basis; assert callable(codebase_basis.capture); assert callable(codebase_basis.verify)' 2>/dev/null \
  && grep -q 'reuse → evolve → new' "$ROOT/phases/phase-2-understand.md"; then
  ok "Codex current codebase basis and reuse-order wiring"
else
  bad "Codex current codebase basis or reuse-order wiring incomplete"
fi
if [ -x "$ROOT/hooks/conformance-gate.sh" ] && bash -n "$ROOT/hooks/conformance-gate.sh" 2>/dev/null; then ok "implementation conformance gate helper ok"; else bad "implementation conformance gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-conformance-gate.sh" ] && bash -n "$ROOT/hooks/test-conformance-gate.sh" 2>/dev/null; then ok "implementation conformance gate test ok"; else bad "implementation conformance gate test missing/not-exec/bad"; fi
if grep -q 'Conformance contract: 1' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Implementation decision evidence' "$ROOT/phases/phase-2-understand.md" \
  && grep -q 'kimiflow:decision-contract contract=1 decisions=<1..5>' "$ROOT/phases/phase-3-plan.md" \
  && grep -q 'Evidence result Dn:' "$ROOT/phases/phase-6-verify.md" \
  && grep -q 'Conformance serialization preflight' "$ROOT/phases/phase-7-review-commit.md" \
  && grep -q 'code-changing audit slice' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'release-discovered product-code/config repair' "$ROOT/reference.md" \
  && grep -q 'conformance gate closed' "$ROOT/hooks/kimiflow_core/active_run.py" \
  && grep -q 'KIMIFLOW_PLUGIN_ROOT/hooks/conformance-gate.sh' "$ROOT/references/legacy-codex.md" \
  && grep -q 'Implementation conformance (adaptive Phase 6)' "$ROOT/reference.md"; then
  ok "Codex adaptive implementation conformance wiring"
else
  bad "Codex adaptive implementation conformance wiring incomplete"
fi
if grep -q 'Flow schema: 5' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Convergence contract: 1' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'kimiflow:convergence contract=1 risk=' "$ROOT/phases/phase-3-plan.md" \
  && grep -q 'plan-review-gate.sh seal.*--write' "$ROOT/phases/phase-4-review-approval.md" \
  && grep -q 'Codex planning context boundary' "$ROOT/phases/phase-1-clarify.md" \
  && grep -q 'Codex intake command economy' "$ROOT/phases/phase-1-clarify.md" \
  && grep -q 'worker replaces parent planning' "$ROOT/references/legacy-codex.md" \
  && grep -q 'one fresh independent closeout verifier' "$ROOT/phases/phase-4-review-approval.md" \
  && grep -q 'dispatch A+B together' "$ROOT/phases/phase-4-review-approval.md" \
  && grep -q 'root-class-repeated' "$ROOT/hooks/resolve-review-gate.sh" \
  && grep -q 'review_snapshot_sha256' "$ROOT/hooks/kimiflow_core/review_convergence.py" \
  && grep -q 'requires explicit --epoch-start' "$ROOT/hooks/resolve-review-gate.sh" \
  && grep -q 'research_subject_sha256' "$ROOT/hooks/kimiflow_core/current_state.py" \
  && grep -q 'kimiflow:convergence-verification contract=1 risk=' "$ROOT/phases/phase-6-verify.md" \
  && grep -q 'convergence_contract' "$ROOT/hooks/kimiflow_core/active_run.py" \
  && grep -q 'schema-4+ run' "$ROOT/hooks/kimiflow_core/workspace_preflight.py"; then
  ok "Codex risk-shaped convergence wiring"
else
  bad "Codex risk-shaped convergence wiring incomplete"
fi
if [ -x "$ROOT/hooks/red-green-gate.sh" ] && bash -n "$ROOT/hooks/red-green-gate.sh" 2>/dev/null; then ok "red-green gate helper ok"; else bad "red-green gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-red-green-gate.sh" ] && bash -n "$ROOT/hooks/test-red-green-gate.sh" 2>/dev/null; then ok "red-green gate test ok"; else bad "red-green gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/frontend-quality-gate.sh" ] && bash -n "$ROOT/hooks/frontend-quality-gate.sh" 2>/dev/null; then ok "frontend quality gate helper ok"; else bad "frontend quality gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-frontend-quality-gate.sh" ] && bash -n "$ROOT/hooks/test-frontend-quality-gate.sh" 2>/dev/null; then ok "frontend quality gate test ok"; else bad "frontend quality gate test missing/not-exec/bad"; fi
for spec in frontend-quality-standard.md frontend-quality-flagship.md frontend-quality-qa.md; do
  [ -s "$ROOT/references/$spec" ] && ok "frontend quality reference present: $spec" || bad "frontend quality reference missing: $spec"
done
grep -q 'frontend-quality-standard.md' "$ROOT/phases/phase-2-understand.md" && ok "Phase 2 lazy frontend routing present" || bad "Phase 2 lazy frontend routing missing"
grep -q 'frontend-quality-qa.md' "$ROOT/phases/phase-6-verify.md" && ok "Phase 6 frontend QA gate present" || bad "Phase 6 frontend QA gate missing"
grep -Fq "\$KIMIFLOW_PLUGIN_ROOT/references/frontend-quality-standard.md" "$ROOT/phases/phase-2-understand.md" && ok "Phase 2 Codex frontend reference is plugin-rooted" || bad "Phase 2 Codex frontend reference is not plugin-rooted"
grep -Fq "\$KIMIFLOW_PLUGIN_ROOT/references/frontend-quality-qa.md" "$ROOT/phases/phase-6-verify.md" && ok "Phase 6 Codex frontend QA is plugin-rooted" || bad "Phase 6 Codex frontend QA is not plugin-rooted"
grep -q 'Frontend quality recovery: clean' "$ROOT/phases/phase-7-review-commit.md" && ok "Phase 7 frontend serialization present" || bad "Phase 7 frontend serialization missing"
grep -q 'frontend-quality-gate.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps frontend quality gate" || bad "Codex wrapper missing frontend quality gate"
[ "$(wc -c < "$ROOT/references/frontend-quality-standard.md")" -le 5000 ] && ok "standard frontend reference budget" || bad "standard frontend reference over budget"
[ "$(wc -c < "$ROOT/references/frontend-quality-flagship.md")" -le 2500 ] && ok "flagship frontend reference budget" || bad "flagship frontend reference over budget"
[ "$(wc -c < "$ROOT/references/frontend-quality-qa.md")" -le 5000 ] && ok "QA frontend reference budget" || bad "QA frontend reference over budget"
if [ -x "$ROOT/hooks/lsp-diagnostics.sh" ] && bash -n "$ROOT/hooks/lsp-diagnostics.sh" 2>/dev/null; then ok "local diagnostics helper ok"; else bad "local diagnostics helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-lsp-diagnostics.sh" ] && bash -n "$ROOT/hooks/test-lsp-diagnostics.sh" 2>/dev/null; then ok "local diagnostics test ok"; else bad "local diagnostics test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/memory-router.sh" ] && bash -n "$ROOT/hooks/memory-router.sh" 2>/dev/null; then ok "memory router helper ok"; else bad "memory router helper missing/not-exec/bad"; fi
if grep -q 'renamex_np(RENAME_SWAP)' "$ROOT/COMPATIBILITY.md" \
  && grep -q 'renameat2(RENAME_EXCHANGE)' "$ROOT/COMPATIBILITY.md" \
  && grep -q 'bounded exchanges' "$ROOT/COMPATIBILITY.md"; then
  ok "memory lifecycle native compatibility declared"
else
  bad "memory lifecycle native compatibility missing"
fi
if [ -f "$ROOT/hooks/memory_router/outcomes.py" ] \
  && [ -f "$ROOT/hooks/memory_router/attribution.py" ] \
  && [ -f "$ROOT/hooks/memory_router/lifecycle.py" ] \
  && [ -f "$ROOT/hooks/memory_router/capsule.py" ] \
  && [ -f "$ROOT/hooks/memory_router/workspace_scope.py" ] \
  && "$ROOT/hooks/memory-router.sh" evaluate-run --help >/dev/null 2>&1 \
  && grep -q -- '--strategies' "$ROOT/phases/phase-2-understand.md" \
  && grep -q 'Strategy evidence:' "$ROOT/phases/phase-3-plan.md" \
  && grep -q 'kimiflow:recall-attribution contract=1' "$ROOT/phases/phase-3-plan.md" \
  && grep -q 'kimiflow:verification outcome=' "$ROOT/phases/phase-6-verify.md" \
  && grep -q 'Recall contradiction <rec_id>' "$ROOT/phases/phase-6-verify.md" \
  && grep -q 'recall_attribution' "$ROOT/hooks/memory_router/outcomes.py" \
  && grep -q 'test_end_to_end_recall_plan_verification_outcome_contract' "$ROOT/hooks/memory_router/tests/test_attribution.py" \
  && grep -q '"lifecycle": _lifecycle.run' "$ROOT/hooks/memory_router/__main__.py" \
  && grep -q '"capsule": _capsule.run' "$ROOT/hooks/memory_router/__main__.py" \
  && grep -q 'portable_entry' "$ROOT/hooks/memory_router/provider.py" \
  && grep -q -- '--scope-path <path>' "$ROOT/reference.md" \
  && grep -q 'test_workspace_scope_keeps_local_and_global_and_omits_foreign_unit' "$ROOT/hooks/memory_router/tests/test_recall.py" \
  && grep -q 'OUTCOME-EVALUATION.json' "$ROOT/phases/phase-7-review-commit.md"; then
  ok "automatic strategy, verified recall, memory lifecycle, and workspace recall contract"
else
  bad "automatic strategy, verified recall, memory lifecycle, or workspace recall contract incomplete"
fi
if [ -x "$ROOT/hooks/test-memory-router-parity.sh" ] && bash -n "$ROOT/hooks/test-memory-router-parity.sh" 2>/dev/null; then ok "memory router test ok"; else bad "memory router test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/vault-mcp-setup.sh" ] && bash -n "$ROOT/hooks/vault-mcp-setup.sh" 2>/dev/null; then ok "vault MCP setup helper ok"; else bad "vault MCP setup helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-vault-mcp-setup.sh" ] && bash -n "$ROOT/hooks/test-vault-mcp-setup.sh" 2>/dev/null; then ok "vault MCP setup test ok"; else bad "vault MCP setup test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/vault-mcp-open-terminal.sh" ] && bash -n "$ROOT/hooks/vault-mcp-open-terminal.sh" 2>/dev/null; then ok "vault MCP terminal helper ok"; else bad "vault MCP terminal helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-vault-mcp-open-terminal.sh" ] && bash -n "$ROOT/hooks/test-vault-mcp-open-terminal.sh" 2>/dev/null; then ok "vault MCP terminal test ok"; else bad "vault MCP terminal test missing/not-exec/bad"; fi
grep -q 'project-map-status.sh' "$ROOT/reference.md" && ok "canonical project-map status helper documented" || bad "canonical project-map status helper missing"
grep -q 'suggest-affected-sections.sh' "$ROOT/reference.md" && ok "canonical suggest-affected helper documented" || bad "canonical suggest-affected helper missing"
grep -q 'map-staleness-nudge.sh' "$ROOT/reference.md" && ok "canonical map staleness nudge helper documented" || bad "canonical map staleness nudge helper missing"
grep -q -- 'refresh --changed' "$ROOT/reference.md" && ok "canonical auto delta refresh documented" || bad "canonical refresh --changed missing"
grep -q 'index-symbols' "$ROOT/reference.md" && ok "canonical symbol index documented" || bad "canonical index-symbols missing"
grep -q -- 'refresh --changed' "$ROOT/references/legacy-workflow.md" && ok "canonical skill documents Phase-7 auto-refresh" || bad "canonical Phase-7 auto-refresh missing"
grep -q 'suggest-affected-sections.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps suggest-affected helper" || bad "Codex wrapper missing suggest-affected helper"
grep -q 'map-staleness-nudge.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps map staleness nudge helper" || bad "Codex wrapper missing map staleness nudge helper"
grep -q 'current-state-gate.sh' "$ROOT/reference.md" && ok "canonical current-state gate helper documented" || bad "canonical current-state gate helper missing"
grep -q 'discovery-gate.sh' "$ROOT/reference.md" && ok "canonical discovery gate helper documented" || bad "canonical discovery gate helper missing"
grep -q 'Architecture contract: 1' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Senior Design trigger' "$ROOT/phases/phase-2-understand.md" \
  && grep -q 'Architecture deliberation: off|active' "$ROOT/phases/phase-2-understand.md" \
  && ok "canonical adaptive architecture routing documented" || bad "canonical adaptive architecture routing missing"
grep -q 'approaches=2 principles=<0..3> critique=1 user_gate=no' "$ROOT/reference.md" \
  && grep -q 'at most 450 words' "$ROOT/reference.md" \
  && grep -q 'architecture_note_over_budget' "$ROOT/hooks/plan-blocker-gate.sh" \
  && ok "canonical adaptive architecture contract bounded" || bad "canonical adaptive architecture contract bounds missing"
grep -q 'exact failing scenario/executable check' "$ROOT/phases/phase-4-review-approval.md" \
  && grep -q 'Architecture falsification' "$ROOT/phases/phase-6-verify.md" \
  && grep -q 'Demand architecture change only' "$ROOT/phases/phase-7-review-commit.md" \
  && ok "canonical architecture falsifier reaches review and verify" || bad "canonical architecture falsifier wiring missing"
if [ -f "$ROOT/hooks/memory_router/standards.py" ] \
  && [ -f "$ROOT/hooks/memory_router/tests/test_standards.py" ] \
  && grep -q '"standards": _standards.run' "$ROOT/hooks/memory_router/__main__.py" \
  && grep -q 'standards select --affected' "$ROOT/reference.md"; then
  ok "canonical scoped standards selector installed"
else
  bad "canonical scoped standards selector missing"
fi
grep -q 'working-tree-gate.sh' "$ROOT/reference.md" && ok "canonical working-tree gate helper documented" || bad "canonical working-tree gate helper missing"
grep -q 'clarify-gate.sh' "$ROOT/reference.md" && ok "canonical clarify gate helper documented" || bad "canonical clarify gate helper missing"
grep -q 'plan-blocker-gate.sh' "$ROOT/reference.md" && ok "canonical plan-blocker gate helper documented" || bad "canonical plan-blocker gate helper missing"
grep -q 'plan-review-gate.sh' "$ROOT/reference.md" && ok "canonical plan-review gate helper documented" || bad "canonical plan-review gate helper missing"
grep -q 'red-green-gate.sh' "$ROOT/reference.md" && ok "canonical red-green gate helper documented" || bad "canonical red-green gate helper missing"
grep -q 'BUG-REPRO.md' "$ROOT/reference.md" && ok "canonical BUG-REPRO evidence documented" || bad "canonical BUG-REPRO evidence missing"
grep -q 'lsp-diagnostics.sh' "$ROOT/reference.md" && ok "canonical local diagnostics helper documented" || bad "canonical local diagnostics helper missing"
grep -q 'memory-router.sh' "$ROOT/reference.md" && ok "canonical memory router helper documented" || bad "canonical memory router helper missing"
for term in PRIVACY-CAPSULE.json 'lifecycle --write' 'lifecycle --restore' 'capsule --write' 'six-field Privacy Capsule'; do
  grep -q -- "$term" "$ROOT/reference.md" && ok "canonical memory lifecycle documented: $term" || bad "canonical memory lifecycle missing: $term"
done
grep -q 'active-run.sh' "$ROOT/reference.md" && ok "canonical active session helper documented" || bad "canonical active session helper missing"
grep -q 'current-state-gate.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps current-state gate helper" || bad "Codex wrapper missing current-state gate helper"
grep -q 'discovery-gate.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps discovery gate helper" || bad "Codex wrapper missing discovery gate helper"
grep -q 'working-tree-gate.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps working-tree gate helper" || bad "Codex wrapper missing working-tree gate helper"
grep -q 'clarify-gate.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps clarify gate helper" || bad "Codex wrapper missing clarify gate helper"
grep -q 'plan-blocker-gate.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps plan-blocker gate helper" || bad "Codex wrapper missing plan-blocker gate helper"
grep -q 'plan-review-gate.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps plan-review gate helper" || bad "Codex wrapper missing plan-review gate helper"
grep -q 'red-green-gate.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps red-green gate helper" || bad "Codex wrapper missing red-green gate helper"
grep -q 'lsp-diagnostics.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps local diagnostics helper" || bad "Codex wrapper missing local diagnostics helper"
grep -q 'memory-router.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps memory router helper" || bad "Codex wrapper missing memory router helper"
grep -q 'active-run.sh' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps active session helper" || bad "Codex wrapper missing active session helper"
grep -q 'Existing feature check' "$ROOT/reference.md" && ok "canonical existing feature check documented" || bad "canonical existing feature check missing"
grep -q 'Memory Router & Learning Loop' "$ROOT/references/legacy-workflow.md" && ok "canonical Memory Router present" || bad "canonical Memory Router missing"
grep -q 'code-review ensemble' "$ROOT/references/legacy-workflow.md" && ok "canonical code-review ensemble present" || bad "canonical code-review ensemble missing"
grep -q 'Code-review ensemble' "$ROOT/reference.md" && ok "canonical code-review ensemble documented" || bad "canonical code-review ensemble docs missing"
grep -q 'CANDIDATE <SEVERITY>' "$ROOT/reference.md" && ok "canonical review candidate format documented" || bad "canonical review candidate format missing"
grep -q 'code-verified' "$ROOT/reference.md" && ok "canonical promoted code-review findings documented" || bad "canonical promoted code-review findings missing"
grep -q 'review_base_sha' "$ROOT/reference.md" && ok "canonical review basis pinned" || bad "canonical fixed review basis missing"
grep -q 'Spec / Correctness' "$ROOT/reference.md" && ok "canonical review axes preserved" || bad "canonical axis-preserving report missing"
grep -q 'Standards smell baseline' "$ROOT/reference.md" && ok "canonical advisory smell baseline documented" || bad "canonical standards smell baseline missing"
grep -q 'Scope classification' "$ROOT/reference.md" && ok "canonical research scope classified" || bad "canonical research scope classification missing"
grep -q 'depth=none|pulse|focused' "$ROOT/reference.md" && ok "canonical adaptive Discovery documented" || bad "canonical adaptive Discovery missing"
grep -q 'Reference Strategy Fit' "$ROOT/phases/phase-2-understand.md" && ok "canonical Reference Strategy Fit documented" || bad "canonical Reference Strategy Fit missing"
grep -Eiq 'pulse.{0,160}(at most|max(imum)?)[^0-9]{0,20}2|pulse.{0,160}two.{0,160}references' "$ROOT/reference.md" \
  && grep -Eiq 'focused.{0,160}(at most|max(imum)?)[^0-9]{0,20}3|focused.{0,160}three.{0,160}references' "$ROOT/reference.md" \
  && ok "canonical Reference Strategy Fit evidence bounded" || bad "canonical Reference Strategy Fit evidence unbounded"
if grep -Fq 'small`/`quick` go straight to the web' "$ROOT/phases/phase-2-understand.md" "$ROOT/reference.md"; then
  bad "canonical small/quick fixes still force web research"
else
  ok "canonical small/quick fix research is adaptive"
fi
grep -Eiq 'research limit.{0,120}(never|not).{0,80}user wait' "$ROOT/reference.md" \
  && ok "canonical research limits stay autonomous" || bad "canonical autonomous research-limit routing missing"
grep -Fq "Caps are total for the run's fit assessment" "$ROOT/phases/phase-2-understand.md" \
  && grep -Fq 'three total references for the fit assessment' "$ROOT/reference.md" \
  && ok "canonical Reference Strategy Fit cap is run-total" || bad "canonical Reference Strategy Fit cap can multiply"
grep -Fq 'does not suppress a later named Discovery/Reference Strategy Fit gap' "$ROOT/phases/phase-2-understand.md" \
  && grep -Fq 'does not suppress a later named Discovery/Reference Strategy Fit gap' "$ROOT/reference.md" \
  && ok "canonical freshness and Reference Strategy Fit compose" || bad "canonical freshness suppresses Reference Strategy Fit"
grep -Fq 'Explicit prior-work cue override' "$ROOT/phases/phase-2-understand.md" \
  && grep -Fq 'Explicit prior-work cue override' "$ROOT/reference.md" \
  && grep -Fq 'prior-work cue' "$ROOT/references/legacy-codex.md" \
  && ok "canonical prior-fix cue override reaches Codex" || bad "canonical prior-fix cue override missing in Codex"
grep -Fq 'MR recall --targeted --strategies --query-file <PROBLEM.md> --max 5 --write .kimiflow/<slug>/RECALL.md' "$ROOT/phases/phase-2-understand.md" \
  && grep -Fq 'replaces the default broad recall' "$ROOT/reference.md" \
  && grep -Fq 'continues without a user question' "$ROOT/reference.md" \
  && ok "canonical prior-fix recall is bounded and non-interactive" || bad "canonical prior-fix recall is broad or interactive"
grep -q 'Build Preview / Risk Gate' "$ROOT/reference.md" && ok "canonical conditional Build Preview documented" || bad "canonical Build Preview risk policy missing"
grep -q 'Flow schema: 5' "$ROOT/phases/phase-0-setup.md" && ok "new runs declare flow schema 5" || bad "phase 0 missing flow schema 5"
grep -q -- '--state .kimiflow/<slug>/STATE.md' "$ROOT/phases/phase-4-review-approval.md" && ok "Build risk reads durable STATE" || bad "Phase 4 does not bind Build risk to STATE"
grep -q 'No routine Human Gate here' "$ROOT/phases/phase-1-clarify.md" && ok "fixes skip early confirmation stop" || bad "Phase 1 still requires an early fix confirmation"
grep -q 'Schema-3 runs retain their legacy' "$ROOT/phases/phase-4-review-approval.md" && ok "schema3 Preview stays resumable" || bad "Phase 4 missing schema3 compatibility"
grep -q 'Schema 4+ does this automatically under the original build authority' "$ROOT/phases/phase-7-review-commit.md" && ok "schema4_atomic_commit_contract" || bad "Phase 7 missing schema4+ atomic commit"
grep -q 'Clean-tree verification checkpoint' "$ROOT/phases/phase-5-build.md" \
  && grep -q 'STATE-backed `started_head`' "$ROOT/phases/phase-7-review-commit.md" \
  && ok "schema4_clean_tree_verification_checkpoint" || bad "clean-tree verification checkpoint/review basis missing"
grep -Fq '${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/test-weakening-scan.sh' "$ROOT/phases/phase-5-build.md" \
  && grep -Fq '$KIMIFLOW_PLUGIN_ROOT/hooks/test-weakening-scan.sh' "$ROOT/references/legacy-codex.md" \
  && ok "Phase 5 test-weakening scan is plugin-rooted for Codex" || bad "Phase 5 test-weakening scan is not plugin-rooted for Codex"
grep -q 'git ls-files --others --exclude-standard' "$ROOT/phases/phase-7-review-commit.md" \
  && ok "phase7_named_untracked_review_basis" || bad "Phase 7 review basis omits named untracked files"
grep -q -- '--record-fix-approval' "$ROOT/reference.md" && ok "reference documents schema3 Fix Preview compatibility" || bad "reference missing schema3 Fix Preview compatibility"
grep -q 'research-driven product expansion is forbidden' "$ROOT/reference.md" && ok "canonical research scope creep blocked" || bad "canonical research scope-creep guard missing"
grep -q -- '--epoch-start <S>' "$ROOT/reference.md" && ok "canonical strategy epoch bounds documented" || bad "canonical strategy epoch bounds missing"
grep -q -- '--gate <plan|code>' "$ROOT/reference.md" && ok "canonical strategy epoch gate documented" || bad "canonical strategy epoch gate missing"
grep -q 'plan seal records and binds that transition mechanically' "$ROOT/reference.md" && ok "canonical recovery receipt documented" || bad "canonical recovery receipt missing"
grep -q 'kimiflow:strategy gate=<plan|code>' "$ROOT/reference.md" && ok "canonical strategy baseline documented" || bad "canonical strategy baseline missing"
grep -Eq -- 'await-user .*--kind <kind>' "$ROOT/reference.md" && ok "canonical typed user pauses documented" || bad "canonical typed user pauses missing"
grep -q 'Autonomous recovery contract' "$ROOT/reference.md" && ok "canonical autonomous review recovery documented" || bad "canonical autonomous review recovery missing"
grep -q 'Minimum-complete' "$ROOT/references/legacy-workflow.md" && ok "canonical minimum-complete rule loaded" || bad "canonical minimum-complete rule missing"
grep -q 'Scope size alone never adds a second planner' "$ROOT/reference.md" && ok "canonical dual-plan is conditional" || bad "canonical conditional dual-plan guard missing"
grep -q 'Review Ensemble' "$ROOT/references/legacy-codex.md" && ok "Codex wrapper maps code-review ensemble" || bad "Codex wrapper missing code-review ensemble mapping"
grep -q 'potentially_stale' "$ROOT/reference.md" && ok "per-section staleness documented" || bad "per-section staleness missing"
grep -q 'phase2_depth' "$ROOT/reference.md" && ok "adaptive map coverage depth documented" || bad "adaptive map coverage depth missing"
for term in MEMORY.md USER.md LEARNINGS.jsonl USER.jsonl MEMORY-INDEX.json MEMORY-USAGE.json RECALL.sqlite RECALL.md RUN-HISTORY.json VAULT-PROVIDER.json VAULT-PREFETCH.md VAULT-SYNC.md SKILL-DRAFTS PENDING-PROPOSALS.md PROPOSALS.jsonl LEARNING-REVIEW.md review-run verify-run 'history --query' metrics 'provider status' 'provider health' 'provider setup' 'provider detect' 'provider sync' 'Vault Pulse' 'vault-mcp-setup.sh' 'vault-mcp-open-terminal.sh' '--interactive' bearer_token_env_var headersHelper 'index --write' 'consolidate --write' 'propose --write' '--approve' '--reject' '--apply' evidence_fingerprints 'Learning quality gate' 'Source freshness gate' provider_sync_pending provider_detected_unconfigured provider_auth_required provider_auth_failed connected_local_only authenticated auth_failed; do
  grep -q -- "$term" "$ROOT/reference.md" && ok "memory artifact documented: $term" || bad "memory artifact missing: $term"
done
for term in 'Storage targets' 'kimiflow+vault' 'repo-docs' 'IMPROVEMENTS.md' 'DOCS-PLAN.md'; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map publishing documented: $term" || bad "project map publishing missing: $term"
done
for term in 'Raw map vs. publishable docs' 'Repo-doc publish safety' 'never auto-commit `.kimiflow/project/`' 'concrete vulnerabilities' 'sanitized version'; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map publish safety documented: $term" || bad "project map publish safety missing: $term"
done

echo "== bundled codex plugin hook wiring =="
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  rel="$(printf '%s\n' "$cmd" | grep -oE 'hooks/[^ "]*\.sh' | head -1)"
  p="$ROOT/$rel"
  if [ -x "$p" ] && bash -n "$p" 2>/dev/null; then ok "hook script ok: $rel"; else bad "hook script missing/not-exec/bad: $rel"; fi
done < <(jq -r '.hooks[]?[]?.hooks[]?.command' "$ROOT/hooks/hooks.json" 2>/dev/null)

echo "== bundled codex hook contract validator =="
echo "== optional embedded-first terminal runner =="
for rel in hooks/kimiflow-runner.sh hooks/install-kimiflow-cli.sh hooks/test-kimiflow-runner.sh hooks/test-install-kimiflow-cli.sh; do
  if [ -x "$ROOT/$rel" ] && bash -n "$ROOT/$rel" 2>/dev/null; then ok "runner surface ok: $rel"; else bad "runner surface missing/not-exec/bad: $rel"; fi
done
PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import runner; assert runner.RECEIPT_RELATIVE == ".kimiflow/session/HEADLESS_RUN.json"' 2>/dev/null \
  && ok "shared-core runner module imports" || bad "shared-core runner module unavailable"
if jq -e --arg version "$cv" '
    .name == "@kimiflow/pi" and .version == $version and .type == "module"
    and .pi.skills == ["./hosts/pi/skills/kimiflow"]
    and .pi.extensions == ["./hosts/pi/extensions/kimiflow-crew.js"]
  ' "$ROOT/package.json" >/dev/null 2>&1 \
  && [ -f "$ROOT/hosts/pi/skills/kimiflow/SKILL.md" ] \
  && [ -f "$ROOT/hosts/pi/extensions/kimiflow-crew.js" ] \
  && [ ! -e "$ROOT/hosts/pi/extensions/captain.js" ] \
  && [ ! -e "$ROOT/hosts/pi/extensions/worker.js" ] \
  && [ ! -e "$ROOT/hosts/pi/extensions/calm.js" ] \
  && [ ! -e "$ROOT/hooks/pi-host.sh" ]; then
  ok "Codex remains standalone with one optional Pi crew adapter"
else
  bad "optional Pi package inventory or version is incomplete"
fi
if [ -x "$ROOT/hooks/secret-content-scan.sh" ] \
  && bash -n "$ROOT/hooks/secret-content-scan.sh" \
  && PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import security, security_deep, runner; assert callable(security.run_scan) and callable(security_deep.run_deep) and callable(security_deep.advisory_diff_artifact); p=runner._parser()._subparsers._group_actions[0].choices["security"]._subparsers._group_actions[0].choices; assert {"deep","ci-artifact","eval","promote"}.issubset(p)' 2>/dev/null; then
  ok "Codex local actionable security runtime and CLI wiring"
else
  bad "Codex local actionable security runtime or CLI wiring incomplete"
fi
for schema in security-scan-manifest-v1.schema.json security-coverage-v1.schema.json security-findings-v1.schema.json security-report-v1.schema.json security-deep-plan-v1.schema.json security-deep-result-v1.schema.json security-eval-v1.schema.json security-promotion-v1.schema.json; do
  if jq -e '.["$schema"] == "https://json-schema.org/draft/2020-12/schema"' "$ROOT/references/$schema" >/dev/null 2>&1; then
    ok "Codex security schema ships: $schema"
  else
    bad "Codex security schema missing/invalid: $schema"
  fi
done
grep -q 'kimiflow security scan' "$ROOT/references/legacy-codex.md" \
  && grep -q 'kimiflow security diff' "$ROOT/reference.md" \
  && grep -q 'kimiflow security deep' "$ROOT/reference.md" \
  && grep -q 'kimiflow security ci-artifact' "$ROOT/reference.md" \
  && ok "Codex actionable and deep security contracts documented" || bad "Codex security docs missing"
grep -q 'legacy-workflow.md' "$ROOT/references/optional-tools.md" \
  && ok "smoke_runner_surface_visible" || bad "embedded-first runner docs missing"
echo "== unified local run control plane =="
for rel in hooks/run-bridge.sh hooks/test-run-bridge.sh; do
  if [ -x "$ROOT/$rel" ] && bash -n "$ROOT/$rel" 2>/dev/null; then ok "control-plane surface ok: $rel"; else bad "control-plane surface missing/not-exec/bad: $rel"; fi
done
PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import phase_context, readiness, run_bridge, scorecard; assert run_bridge.RECEIPT_NAME == "RUN-BRIDGE.json"; assert scorecard.SCORECARD_NAME == "RUN-SCORECARD.json"; assert phase_context.SHADOW_NAME == "PHASE-CONTEXT-SHADOW.json"' 2>/dev/null \
  && ok "Codex unified run control-plane modules import" || bad "Codex unified run control-plane modules unavailable"
grep -q 'Unified local run control plane' "$ROOT/reference.md" \
  && ok "Codex unified run control-plane contract documented" || bad "Codex unified run control-plane docs missing"
INSTALLER="$ROOT/hooks/install-codex-hooks.sh"
if [ -x "$INSTALLER" ] && bash -n "$INSTALLER" 2>/dev/null; then ok "installer script ok: hooks/install-codex-hooks.sh"; else bad "installer script missing/not-exec/bad"; fi
tmp_home="$(mktemp -d)"
if CODEX_HOME="$tmp_home/codex" "$INSTALLER" --check >/dev/null 2>&1; then ok "bundled hook contract validates"; else bad "bundled hook contract validation failed"; fi
[ ! -e "$tmp_home/codex/hooks" ] && ok "validator creates no unregistered wrappers" || bad "validator created unregistered wrappers"

echo "== codex gate fires (synthetic payloads) =="
COMMIT_HOOK="$ROOT/hooks/commit-secret-gate.sh"
STATE_HOOK="$ROOT/hooks/state-gate.sh"
TEST_HOOK="$ROOT/hooks/test-gate.sh"
ACTIVE_HOOK="$ROOT/hooks/active-run.sh"

deny_commit() { jq -nc --arg c "$1" --arg d "$2" '{tool_input:{args:{command:$c}}, cwd:$d, hook_event_name:"PreToolUse"}' | bash "$COMMIT_HOOK" 2>/dev/null | grep -q '"permissionDecision":"deny"'; }
deny_state()  { jq -nc --arg c "$1" --arg d "$2" '{tool_input:{args:{command:$c}}, cwd:$d, hook_event_name:"PreToolUse"}' | bash "$STATE_HOOK" 2>/dev/null | grep -q '"permissionDecision":"deny"'; }
test_gate_no_active_passes() { out="$(jq -nc --arg d "$1" '{cwd:$d, hook_input:{stop_hook_active:false}, hook_event_name:"Stop"}' | bash "$TEST_HOOK" 2>/dev/null)"; [ -z "$out" ]; }
allow_stop_active() { out="$(jq -nc --arg d "$1" '{cwd:$d, hook_input:{stop_hook_active:true}, hook_event_name:"Stop"}' | bash "$TEST_HOOK" 2>/dev/null)"; [ -z "$out" ]; }
test_gate_owner_blocks() { jq -nc --arg d "$1" '{cwd:$d, session_id:"owner-session", hook_input:{stop_hook_active:false}, hook_event_name:"Stop"}' | KIMIFLOW_HOST=codex bash "$TEST_HOOK" 2>/dev/null | grep -qE '"decision"[[:space:]]*:[[:space:]]*"block"'; }
test_gate_other_passes() { out="$(jq -nc --arg d "$1" '{cwd:$d, session_id:"other-session", hook_input:{stop_hook_active:false}, hook_event_name:"Stop"}' | KIMIFLOW_HOST=codex bash "$TEST_HOOK" 2>/dev/null)"; [ -z "$out" ]; }
active_prompt_context() { jq -nc --arg d "$1" '{cwd:$d, session_id:"owner-session", prompt:"follow-up text", hook_event_name:"UserPromptSubmit"}' | KIMIFLOW_HOST=codex bash "$ACTIVE_HOOK" prompt-context 2>/dev/null | grep -q 'additionalContext'; }
active_stop_blocks() { jq -nc --arg d "$1" '{cwd:$d, session_id:"owner-session", hook_input:{stop_hook_active:false}, hook_event_name:"Stop"}' | KIMIFLOW_HOST=codex bash "$ACTIVE_HOOK" stop-gate 2>/dev/null | grep -qE '"decision"[[:space:]]*:[[:space:]]*"block"'; }
active_other_stop_passes() { out="$(jq -nc --arg d "$1" '{cwd:$d, session_id:"other-session", hook_input:{stop_hook_active:false}, hook_event_name:"Stop"}' | KIMIFLOW_HOST=codex bash "$ACTIVE_HOOK" stop-gate 2>/dev/null)"; [ -z "$out" ]; }

tmp1="$(mktemp -d)"; ( cd "$tmp1" && git init -q && mkdir .kimiflow )
tmp2="$(mktemp -d)"; ( cd "$tmp2" && git init -q )
if deny_commit 'git add .' "$tmp1"; then ok "commit-secret-gate blocks git add . in Codex payload"; else bad "commit-secret-gate did not block git add . in Codex payload"; fi
if deny_commit 'git add .' "$tmp2"; then bad "commit-secret-gate wrongly blocked outside Kimiflow repo"; else ok "commit-secret-gate allows outside Kimiflow repo"; fi
mkdir -p "$tmp1/.kimiflow/nostate/findings"
if deny_state './hooks/resolve-review-gate.sh .kimiflow/nostate/findings --round 1 --expect A,B' "$tmp1"; then ok "state-gate blocks missing STATE in Codex payload"; else bad "state-gate did not block missing STATE in Codex payload"; fi
printf 'touch .kimiflow/no-active-eval; false\n' > "$tmp1/.kimiflow/test-gate"
if test_gate_no_active_passes "$tmp1" && [ ! -e "$tmp1/.kimiflow/no-active-eval" ]; then ok "test-gate ignores Codex task without active owner"; else bad "test-gate evaluated marker without active owner"; fi
if allow_stop_active "$tmp1"; then ok "test-gate allows active stop continuation"; else bad "test-gate did not allow active stop continuation"; fi
mkdir -p "$tmp1/.kimiflow/demo"
cat > "$tmp1/.kimiflow/demo/STATE.md" <<'EOF'
Status: active
Affected files: README.md
Phase 0: done
Phase 5: in-progress
EOF
CODEX_THREAD_ID=owner-session bash "$ACTIVE_HOOK" start --root "$tmp1" --run .kimiflow/demo --write >/dev/null
CODEX_THREAD_ID=owner-session bash "$ACTIVE_HOOK" append-item --root "$tmp1" --title "synthetic active-session item" --write >/dev/null
if active_prompt_context "$tmp1"; then ok "active session hook injects Codex prompt context"; else bad "active session hook did not inject Codex prompt context"; fi
if active_stop_blocks "$tmp1"; then ok "active session Stop hook blocks unfinished session"; else bad "active session Stop hook did not block unfinished session"; fi
if active_other_stop_passes "$tmp1"; then ok "active session Stop hook ignores unrelated Codex session"; else bad "active session Stop hook blocked unrelated Codex session"; fi
if test_gate_owner_blocks "$tmp1"; then ok "active test gate still blocks owner Codex session"; else bad "active test gate did not block owner Codex session"; fi
if test_gate_other_passes "$tmp1"; then ok "active test gate ignores unrelated Codex session"; else bad "active test gate blocked unrelated Codex session"; fi

tmp3="$(mktemp -d)"
( cd "$tmp3" && git init -q )
mkdir -p "$tmp3/.kimiflow/demo"
cat > "$tmp3/.kimiflow/demo/STATE.md" <<'EOF'
Flow schema: 4
Intent contract: 3
Status: active
Mode: feature
Scope: small
Recovery: clean
Affected files: README.md
Phase 0: done
Phase 1: done
Phase 2: done
Phase 3: done
Phase 4: done
Phase 5: in-progress
Phase 6: open
Phase 7: open
EOF
active_phase_out="$(KIMIFLOW_SESSION_HOST=codex KIMIFLOW_SESSION_ID=owner-session bash "$ACTIVE_HOOK" start --root "$tmp3" --run .kimiflow/demo --write 2>/dev/null || true)"
if printf '%s\n' "$active_phase_out" | jq -e '.phase_reads_required == true' >/dev/null 2>&1; then
  ok "active session wrapper enables phase reads from plugin root"
else
  bad "active session wrapper did not enable phase reads in scratch consumer"
fi
if [ ! -e "$tmp3/phases" ]; then
  ok "Codex scratch consumer has no local phases directory"
else
  bad "Codex scratch consumer unexpectedly has local phases directory"
fi
phase_gate="$(bash "$ACTIVE_HOOK" phase-read-gate --root "$tmp3" --run .kimiflow/demo --through-phase 1 2>/dev/null || true)"
if printf '%s\n' "$phase_gate" | grep -q $'PHASE_READ_GATE\tCLOSED' \
  && printf '%s\n' "$phase_gate" | grep -q 'phase_0_read_missing'; then
  ok "active session wrapper phase-read gate closes on missing read"
else
  bad "active session wrapper phase-read gate did not close on missing read"
fi
next_action="$(bash "$ACTIVE_HOOK" next-action --root "$tmp3" 2>/dev/null || true)"
if printf '%s\n' "$next_action" | jq -e '.graph_status == "ready" and .current_node == "phase_5" and (.action | length > 0)' >/dev/null 2>&1; then
  ok "active session wrapper resolves installed transition graph"
else
  bad "active session wrapper did not resolve installed transition graph"
fi
rm -rf "$tmp1" "$tmp2" "$tmp3" "$tmp_home"

if grep -Eiq 'open [`]?/hooks|under [`]?/hooks|unter [`]?/hooks|review/trust.*?/hooks|start_new_codex_task_after_hook_review' \
  "$ROOT/README.md" "$ROOT/README.de.md" "$ROOT/COMPATIBILITY.md" "$ROOT/reference.md" \
  "$ROOT/hooks/kimiflow_core/active_run.py"; then
  bad "Codex docs still instruct a nonexistent /hooks workflow"
else
  ok "Codex docs use restart plus new-task recovery"
fi

echo "== MANUAL (needs Codex app/CLI plugin browser) =="
cat <<'MANUAL'
  [ ] Add the Git marketplace (`codex plugin marketplace add kimikonapps/kimiflow`), then install kimiflow.
  [ ] Restart Codex after install/update, start a new thread, and invoke "$kimiflow <tiny change>".
  [ ] Confirm an actionable implementation request for a substantial cross-surface/integration/data/security/API/architecture/discovery feature auto-routes into Kimiflow.
  [ ] Confirm a discussion, idea, recommendation, explanation/status request, or wish formulation stays direct and read-only.
  [ ] Confirm a normal fix, review, refactor, cleanup, docs/config task, or small low-risk feature stays direct unless Kimiflow is explicit.
  [ ] Confirm explicit "direct" or "direkt" bypasses Kimiflow and explicit "with kimiflow" launches it.
  [ ] In a repo with .kimiflow/, attempting `git add .` is blocked by the installed stable Codex hook.
  [ ] With an owned active Kimiflow session and .kimiflow/test-gate containing a failing command, that owner's Codex Stop is blocked.
  [ ] With an active Kimiflow session, its owner stays gated while a second project task can read, answer, and plan without any Stop continuation.
MANUAL

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "CODEX SMOKE OK (structural)"; exit 0; else echo "$FAILS CODEX SMOKE FAILURE(S)"; exit 1; fi
