#!/usr/bin/env bash
# kimiflow — install smoke-test. Verifies the plugin is structurally installable and its gates
# are wired + actually FIRE, WITHOUT a live Claude Code session. Run before a release and on a
# Claude Code upgrade. Exits non-zero on any automatable failure.
#
# WHY this exists: Claude Code's plugin/skill invocation contract has had real regressions —
#   https://github.com/anthropics/claude-code/issues/26251  (slash invocation vs disable-model-invocation)
#   https://github.com/anthropics/claude-code/issues/22345  (plugin skills honoring disable-model-invocation)
# The structural half is automated below; the parts that need a real CC session (actual
# /plugin install, slash invocation, and model routing) are printed as a MANUAL checklist.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILS=0
ok()  { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }

command -v jq  >/dev/null 2>&1 || { echo "smoke-install: jq required"; exit 2; }
command -v git >/dev/null 2>&1 || { echo "smoke-install: git required"; exit 2; }

echo "== manifests =="
for j in .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json; do
  if jq -e . "$ROOT/$j" >/dev/null 2>&1; then ok "valid JSON: $j"; else bad "invalid JSON: $j"; fi
done
pv="$(jq -r '.version' "$ROOT/.claude-plugin/plugin.json" 2>/dev/null)"
mv="$(jq -r '.plugins[0].version' "$ROOT/.claude-plugin/marketplace.json" 2>/dev/null)"
if [ -n "$pv" ] && [ "$pv" = "$mv" ]; then ok "version consistent ($pv)"; else bad "version mismatch: plugin=$pv marketplace=$mv"; fi
jq -e '((.description // "") | test("code-review ensembles"))' "$ROOT/.claude-plugin/plugin.json" >/dev/null 2>&1 \
  && ok "Claude plugin describes code-review ensembles" || bad "Claude plugin description missing code-review ensembles"
jq -e '((.description // "") | test("full/grill/plan/build/quick/review/audit/fix"))' "$ROOT/.claude-plugin/plugin.json" >/dev/null 2>&1 \
  && ok "Claude plugin describes natural mode aliases" || bad "Claude plugin description missing natural mode aliases"
jq -e '((.metadata.description // "") + " " + (.plugins[0].description // "") | test("full/grill/plan/build/quick/review/audit/fix"))' "$ROOT/.claude-plugin/marketplace.json" >/dev/null 2>&1 \
  && ok "Claude marketplace describes natural mode aliases" || bad "Claude marketplace missing natural mode aliases"

echo "== capability display sync (Claude) =="
# Four canonical capabilities must each appear in every prominent Claude surface (drift guard).
# README is checked ONLY inside the delimited capabilities block so markers elsewhere can't satisfy it (non-vacuous).
# Guard: both delimiters must exist, else an unclosed block would capture to EOF and the marker checks turn vacuous.
{ grep -q '<!-- capabilities:start -->' "$ROOT/README.md" && grep -q '<!-- capabilities:end -->' "$ROOT/README.md"; } \
  && ok "README capabilities block is delimited" || bad "README capabilities block delimiters missing/unbalanced"
readme_caps="$(awk '/<!-- capabilities:start -->/{f=1;next} /<!-- capabilities:end -->/{f=0} f' "$ROOT/README.md")"
for m in 'feature[^.]*fix' 'project intelligence' 'repo docs' 'findings'; do
  printf '%s' "$readme_caps" | grep -qiE "$m" \
    && ok "README capabilities block names: $m" || bad "README capabilities block missing: $m"
done
for m in 'feature[^.]*fix' 'project intelligence' 'repo docs' 'findings'; do
  jq -e --arg m "$m" '((.description // "") | test($m; "i"))' "$ROOT/.claude-plugin/plugin.json" >/dev/null 2>&1 \
    && ok "Claude plugin describes capability: $m" || bad "Claude plugin description missing capability: $m"
done
for m in 'feature[^.]*fix' 'project intelligence' 'repo docs' 'findings'; do
  jq -e --arg m "$m" '(((.metadata.description // "") + " " + (.plugins[0].description // "")) | test($m; "i"))' "$ROOT/.claude-plugin/marketplace.json" >/dev/null 2>&1 \
    && ok "Claude marketplace describes capability: $m" || bad "Claude marketplace missing capability: $m"
done

echo "== skill frontmatter (SKILL.md) =="
fm="$(awk 'NR==1 && $0=="---"{f=1;next} f && $0=="---"{exit} f' "$ROOT/SKILL.md")"
printf '%s\n' "$fm" | grep -qE '^name:[[:space:]]*kimiflow'                 && ok "name: kimiflow"                       || bad "name missing/wrong"
printf '%s\n' "$fm" | grep -qE '^description:'                              && ok "description present"                  || bad "description missing"
printf '%s\n' "$fm" | grep -qE '^argument-hint:'                            && ok "argument-hint present"                || bad "argument-hint missing"
printf '%s\n' "$fm" | grep -q -- '--launcher|--menu'                         && ok "launcher argument hint present"       || bad "launcher argument hint missing"
printf '%s\n' "$fm" | grep -q -- '--project-map <quick|skip>'   && ok "project-map argument hint present"     || bad "project-map argument hint missing"
printf '%s\n' "$fm" | grep -q -- '--verify-feature <feature-or-path>'          && ok "verify-feature argument hint present"  || bad "verify-feature argument hint missing"
# Model-invocation is enabled; routing boundaries live in the description rather than a hard flag.
if printf '%s\n' "$fm" | grep -qE '^disable-model-invocation:[[:space:]]*true'; then bad "disable-model-invocation: true → model can't route or launch kimiflow"; else ok "model-invocable (disable-model-invocation not true)"; fi
printf '%s\n' "$fm" | grep -q 'actionable implementation requests' && ok "description limits auto-routing to implementation requests" || bad "description missing implementation-authorization boundary"
printf '%s\n' "$fm" | grep -q 'Discussion, ideation' && ok "description keeps non-build discussion direct" || bad "description missing discussion-only boundary"
printf '%s\n' "$fm" | grep -q 'explicit direct or direkt always bypasses' && ok "description preserves direct/direkt overrides" || bad "description missing direct/direkt overrides"
printf '%s\n' "$fm" | grep -q 'Do not auto-trigger for fixes' && ok "description keeps fixes direct by default" || bad "description missing direct-by-default fix boundary"
# user-invocable defaults true; it must NOT be false or /kimiflow vanishes from the slash menu.
if printf '%s\n' "$fm" | grep -qE '^user-invocable:[[:space:]]*false'; then bad "user-invocable: false → /kimiflow hidden from the slash menu"; else ok "user-invocable not disabled (slash-invocable)"; fi

echo "== project map bootstrap contract =="
grep -q 'Launcher / menu' "$ROOT/SKILL.md" && ok "canonical skill documents Launcher mode" || bad "missing Launcher mode in SKILL.md"
grep -q 'Launcher mode' "$ROOT/reference.md" && ok "reference documents Launcher mode" || bad "missing Launcher mode in reference.md"
grep -q 'Natural mode aliases' "$ROOT/SKILL.md" && ok "canonical skill documents natural mode aliases" || bad "missing natural mode aliases in SKILL.md"
grep -q 'Natural mode aliases' "$ROOT/reference.md" && ok "reference documents natural mode aliases" || bad "missing natural mode aliases in reference.md"
for term in 'kimiflow full' 'kimiflow grill' 'kimiflow plan' 'kimiflow build' 'kimiflow review' 'kimiflow audit' 'kimiflow fix' 'kimiflow quick'; do
  grep -q "$term" "$ROOT/README.md" && ok "README documents mode alias: $term" || bad "README missing mode alias: $term"
done
grep -q 'full.*does not create an approval stop' "$ROOT/SKILL.md" && ok "full mode follows material-risk decisions" || bad "full mode still forces approval"
grep -q 'provenance scan; ≤1 product batch' "$ROOT/SKILL.md" && ok "canonical skill bounds intent interaction" || bad "canonical skill missing bounded intent interaction"
grep -q 'Intent Coverage Scan (Contract 2)' "$ROOT/reference.md" && ok "reference documents provenance-aware intent coverage" || bad "reference missing provenance-aware intent coverage"
grep -q 'one compact batch' "$ROOT/README.md" && ok "README documents batched clarification" || bad "README missing batched clarification"
grep -q 'git commit --only' "$ROOT/phases/phase-7-review-commit.md" && grep -q 'foreign staged' "$ROOT/phases/phase-7-review-commit.md" \
  && ok "atomic commit isolates foreign staged paths" || bad "atomic commit foreign-staging isolation missing"
grep -q 'Vault Pulse' "$ROOT/SKILL.md" && ok "canonical skill requires scope=large Vault Pulse semantics" || bad "canonical skill missing Vault Pulse"
grep -Eq 'Vault Pulse.*scope=large|scope=large.*Vault Pulse' "$ROOT/reference.md" && ok "reference documents scope=large Vault Pulse semantics" || bad "reference missing scope=large Vault Pulse semantics"
grep -q 'Vault Pulse' "$ROOT/README.md" && ok "README documents scope=large Vault Pulse semantics" || bad "README missing Vault Pulse"
if grep -q 'kimiflow grill.*no code' "$ROOT/reference.md" \
  && grep -q 'kimiflow plan.*no code' "$ROOT/reference.md" \
  && grep -q 'kimiflow review.*no code' "$ROOT/reference.md" \
  && grep -q 'kimiflow audit.*no code' "$ROOT/reference.md"; then
  ok "launcher documents no-code aliases"
else
  bad "launcher docs missing no-code alias rule"
fi
grep -q 'Resume safety check' "$ROOT/reference.md" && ok "reference documents resume safety check" || bad "missing resume safety check in reference.md"
if [ -x "$ROOT/hooks/launcher-status.sh" ] && bash -n "$ROOT/hooks/launcher-status.sh" 2>/dev/null; then ok "launcher status helper ok"; else bad "launcher status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-launcher-status.sh" ] && bash -n "$ROOT/hooks/test-launcher-status.sh" 2>/dev/null; then ok "launcher status test ok"; else bad "launcher status test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/active-run.sh" ] && bash -n "$ROOT/hooks/active-run.sh" 2>/dev/null; then ok "active session helper ok"; else bad "active session helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-active-run.sh" ] && bash -n "$ROOT/hooks/test-active-run.sh" 2>/dev/null; then ok "active session test ok"; else bad "active session test missing/not-exec/bad"; fi
if [ -f "$ROOT/hooks/kimiflow_core/execution_control.py" ] \
  && [ -x "$ROOT/hooks/test-execution-control.sh" ] \
  && grep -q '"execution_control"' "$ROOT/phases/PHASES.json" \
  && grep -q 'Execution contract: 1' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Adaptive Execution Contract' "$ROOT/SKILL.md"; then
  ok "adaptive execution controller wiring"
else
  bad "adaptive execution controller wiring incomplete"
fi
grep -q 'Project Map Bootstrap' "$ROOT/SKILL.md" && ok "canonical skill documents Project Map Bootstrap" || bad "missing Project Map Bootstrap in SKILL.md"
grep -q -- '--project-map quick' "$ROOT/reference.md" && ok "reference documents project-map quick tier" || bad "missing project-map quick tier in reference.md"
if grep -Eq -- '--project-map[^)]*(standard|deep)' "$ROOT/reference.md" "$ROOT/SKILL.md"; then bad "retired project-map tier (standard/deep) resurfaced in live docs"; else ok "no retired project-map tiers in live docs"; fi
for term in INDEX.json FACTS.jsonl CODEBASE.md ARCHITECTURE.md CONVENTIONS.md TESTING.md FLOWS.md OPEN-QUESTIONS.md; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map artifact documented: $term" || bad "project map artifact missing: $term"
done
if [ -x "$ROOT/hooks/project-map-status.sh" ] && bash -n "$ROOT/hooks/project-map-status.sh" 2>/dev/null; then ok "project map status helper ok"; else bad "project map status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-project-map-status.sh" ] && bash -n "$ROOT/hooks/test-project-map-status.sh" 2>/dev/null; then ok "project map status test ok"; else bad "project map status test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/suggest-affected-sections.sh" ] && bash -n "$ROOT/hooks/suggest-affected-sections.sh" 2>/dev/null; then ok "suggest-affected helper ok"; else bad "suggest-affected helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-suggest-affected-sections.sh" ] && bash -n "$ROOT/hooks/test-suggest-affected-sections.sh" 2>/dev/null; then ok "suggest-affected test ok"; else bad "suggest-affected test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/map-staleness-nudge.sh" ] && bash -n "$ROOT/hooks/map-staleness-nudge.sh" 2>/dev/null; then ok "map staleness nudge helper ok"; else bad "map staleness nudge helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-map-staleness-nudge.sh" ] && bash -n "$ROOT/hooks/test-map-staleness-nudge.sh" 2>/dev/null; then ok "map staleness nudge test ok"; else bad "map staleness nudge test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/current-state-gate.sh" ] && bash -n "$ROOT/hooks/current-state-gate.sh" 2>/dev/null; then ok "current-state gate helper ok"; else bad "current-state gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-current-state-gate.sh" ] && bash -n "$ROOT/hooks/test-current-state-gate.sh" 2>/dev/null; then ok "current-state gate test ok"; else bad "current-state gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/discovery-gate.sh" ] && bash -n "$ROOT/hooks/discovery-gate.sh" 2>/dev/null; then ok "discovery gate helper ok"; else bad "discovery gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-discovery-gate.sh" ] && bash -n "$ROOT/hooks/test-discovery-gate.sh" 2>/dev/null; then ok "discovery gate test ok"; else bad "discovery gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/working-tree-gate.sh" ] && bash -n "$ROOT/hooks/working-tree-gate.sh" 2>/dev/null; then ok "working-tree gate helper ok"; else bad "working-tree gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-working-tree-gate.sh" ] && bash -n "$ROOT/hooks/test-working-tree-gate.sh" 2>/dev/null; then ok "working-tree gate test ok"; else bad "working-tree gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/workspace-preflight.sh" ] && bash -n "$ROOT/hooks/workspace-preflight.sh" 2>/dev/null; then ok "workspace preflight helper ok"; else bad "workspace preflight helper missing/not-exec/bad"; fi
if grep -q 'show one plain summary' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'await-user --run .kimiflow/<slug> --kind workspace' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Apply only the selected safe actions, rerun status' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'test_schema4_workspace_wait_is_one_shot' "$ROOT/hooks/kimiflow_core/tests/test_active_run.py"; then
  ok "schema4_workspace_summary_and_single_decision"
else
  bad "workspace summary/one-shot disposition procedure is incomplete"
fi
grep -q 'test_schema4_workspace_wait_is_one_shot' "$ROOT/hooks/kimiflow_core/tests/test_active_run.py" && ok "schema4_workspace_wait_is_mechanically_one_shot" || bad "workspace wait lacks one-shot regression coverage"
grep -q 'test_schema4_workspace_wait_receipt_survives_park_and_resume' "$ROOT/hooks/kimiflow_core/tests/test_active_run.py" && ok "workspace receipt survives park/resume" || bad "workspace receipt lacks park/resume coverage"
grep -q 'test_targeted_cleanup_preserves_unrelated_prunable_metadata' "$ROOT/hooks/kimiflow_core/tests/test_workspace_preflight.py" && ok "targeted retirement preserves unrelated metadata" || bad "targeted retirement lacks unrelated-metadata coverage"
grep -q 'Only after step 3.55' "$ROOT/phases/phase-0-setup.md" && ok "frontend baseline follows workspace closure" || bad "frontend baseline timing bypasses workspace closure"
if grep -Fqi 'use a separate git worktree' "$ROOT/hooks/kimiflow_core/active_run.py"; then bad "non-owner hook suggests an unguarded worktree"; else ok "non-owner hook preserves exceptional-worktree authority"; fi
if grep -Ei 'code-review-audit|full.*one mode-specific Preview approval|explicit Build Preview approval|one post-diagnosis Fix Preview|ask the user to commit/stash/clean first|fix defers approval|Do not ask for approval yet|Freigabe vor Build|riskante Entscheidungen und Commits|One question at a time|STOP and ask the user to switch|STOP before Phase 0|asks for a Sol session|until a \*\*human\*\*|one defined exception.*commit-gate|Build/Fix Preview control|full waits once|clean-worktree|human commit-gate|STOPS for your OK|waits for your explicit OK|Never auto-commits|best-of-2 implementer|second best-of-2 candidate|own worktree|commit-without-OK' "$ROOT/README.md" "$ROOT/README.de.md" "$ROOT/.claude-plugin/plugin.json" "$ROOT/.claude-plugin/marketplace.json" "$ROOT/.codex-plugin/plugin.json" "$ROOT/docs/render/kimiflow/canonical/SKILL.md" "$ROOT/phases/phase-1-clarify.md" "$ROOT/phases/phase-2-understand.md" "$ROOT/phases/phase-5-build.md" "$ROOT/evals/scenarios/08-advisory-triage-failclosed.md" "$ROOT/evals/scenarios/13-top-model-orchestrator.md" "$ROOT/evals/scenarios/15-evidence-guided-discovery.md" "$ROOT/docs/render/kimiflow/overlays/codex.md" "$ROOT/reference.md" "$ROOT/docs/architecture.md" "$ROOT/docs/demo/play.sh" "$ROOT/docs/demo/README.md" "$ROOT/examples/README.md" "$ROOT/examples/01-small-fix.md" "$ROOT/examples/02-risky-bugfix.md" "$ROOT/examples/03-feature.md" "$ROOT/docs/kimiflow-vs-claude-md-vs-superpowers.md" >/dev/null; then bad "stale schema4 babysitting guidance remains"; else ok "schema4 babysitting guidance removed"; fi
grep -q 'automatisch geroutete' "$ROOT/docs/architecture.md" && grep -q 'automatically routed' "$ROOT/docs/kimiflow-vs-claude-md-vs-superpowers.md" && ok "maintainer docs preserve automatic routing" || bad "maintainer docs lost automatic routing"
if [ -x "$ROOT/hooks/clarify-gate.sh" ] && bash -n "$ROOT/hooks/clarify-gate.sh" 2>/dev/null; then ok "clarify gate helper ok"; else bad "clarify gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-clarify-gate.sh" ] && bash -n "$ROOT/hooks/test-clarify-gate.sh" 2>/dev/null; then ok "clarify gate test ok"; else bad "clarify gate test missing/not-exec/bad"; fi
if grep -q 'Intent contract: 2' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Impact x Uncertainty' "$ROOT/phases/phase-1-clarify.md" \
  && grep -q 'technical_questions=0' "$ROOT/reference.md" \
  && grep -q 'intent_coverage_missing' "$ROOT/hooks/clarify-gate.sh" \
  && grep -q 'contract2_complete_zero_round_opens' "$ROOT/hooks/test-clarify-gate.sh" \
  && grep -q 'product-intent ownership' "$ROOT/evals/README.md"; then
  ok "product intent ownership and single-batch autonomy"
else
  bad "product intent ownership contract incomplete"
fi
if [ -x "$ROOT/hooks/plan-blocker-gate.sh" ] && bash -n "$ROOT/hooks/plan-blocker-gate.sh" 2>/dev/null; then ok "plan-blocker gate helper ok"; else bad "plan-blocker gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-plan-blocker-gate.sh" ] && bash -n "$ROOT/hooks/test-plan-blocker-gate.sh" 2>/dev/null; then ok "plan-blocker gate test ok"; else bad "plan-blocker gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/conformance-gate.sh" ] && bash -n "$ROOT/hooks/conformance-gate.sh" 2>/dev/null; then ok "implementation conformance gate helper ok"; else bad "implementation conformance gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-conformance-gate.sh" ] && bash -n "$ROOT/hooks/test-conformance-gate.sh" 2>/dev/null; then ok "implementation conformance gate test ok"; else bad "implementation conformance gate test missing/not-exec/bad"; fi
if grep -q 'Conformance contract: 1' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Implementation decision evidence' "$ROOT/phases/phase-2-understand.md" \
  && grep -q 'kimiflow:decision-contract contract=1 decisions=<1..5>' "$ROOT/phases/phase-3-plan.md" \
  && grep -q 'kimiflow:conformance contract=1 status=' "$ROOT/phases/phase-6-verify.md" \
  && grep -q 'Conformance serialization preflight' "$ROOT/phases/phase-7-review-commit.md" \
  && grep -q 'conformance gate closed' "$ROOT/hooks/kimiflow_core/active_run.py" \
  && grep -q 'Implementation conformance (adaptive Phase 6)' "$ROOT/reference.md"; then
  ok "adaptive implementation conformance wiring"
else
  bad "adaptive implementation conformance wiring incomplete"
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
grep -Fq "\${CLAUDE_PLUGIN_ROOT:-\$CLAUDE_SKILL_DIR}/references/frontend-quality-standard.md" "$ROOT/phases/phase-2-understand.md" && ok "Phase 2 frontend reference is plugin-rooted" || bad "Phase 2 frontend reference is not plugin-rooted"
grep -Fq "\${CLAUDE_PLUGIN_ROOT:-\$CLAUDE_SKILL_DIR}/references/frontend-quality-qa.md" "$ROOT/phases/phase-6-verify.md" && ok "Phase 6 frontend QA is plugin-rooted" || bad "Phase 6 frontend QA is not plugin-rooted"
grep -q 'Frontend quality recovery: clean' "$ROOT/phases/phase-7-review-commit.md" && ok "Phase 7 frontend serialization present" || bad "Phase 7 frontend serialization missing"
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
grep -q 'project-map-status.sh' "$ROOT/reference.md" && ok "reference documents project-map status helper" || bad "missing project-map status helper in reference.md"
grep -q 'suggest-affected-sections.sh' "$ROOT/reference.md" && ok "reference documents suggest-affected helper" || bad "missing suggest-affected helper in reference.md"
grep -q 'map-staleness-nudge.sh' "$ROOT/reference.md" && ok "reference documents map staleness nudge helper" || bad "missing map staleness nudge helper in reference.md"
grep -q -- 'refresh --changed' "$ROOT/reference.md" && ok "reference documents auto delta refresh" || bad "missing refresh --changed in reference.md"
grep -q 'index-symbols' "$ROOT/reference.md" && ok "reference documents symbol index" || bad "missing index-symbols in reference.md"
grep -q -- 'refresh --changed' "$ROOT/SKILL.md" && ok "canonical skill documents Phase-7 auto-refresh" || bad "missing Phase-7 auto-refresh in SKILL.md"
grep -q 'suggest-affected-sections.sh' "$ROOT/SKILL.md" && ok "canonical skill documents Phase-2 section lookup" || bad "missing Phase-2 section lookup in SKILL.md"
grep -q 'current-state-gate.sh' "$ROOT/reference.md" && ok "reference documents current-state gate helper" || bad "missing current-state gate helper in reference.md"
grep -q 'discovery-gate.sh' "$ROOT/reference.md" && ok "reference documents discovery gate helper" || bad "missing discovery gate helper in reference.md"
grep -q 'Architecture contract: 1' "$ROOT/phases/phase-0-setup.md" \
  && grep -q 'Senior Design trigger' "$ROOT/phases/phase-2-understand.md" \
  && grep -q 'Architecture deliberation: off|active' "$ROOT/phases/phase-2-understand.md" \
  && ok "adaptive architecture routing documented" || bad "adaptive architecture routing missing"
grep -q 'approaches=2 principles=<0..3> critique=1 user_gate=no' "$ROOT/reference.md" \
  && grep -q 'at most 450 words' "$ROOT/reference.md" \
  && grep -q 'architecture_note_over_budget' "$ROOT/hooks/plan-blocker-gate.sh" \
  && ok "adaptive architecture contract bounded" || bad "adaptive architecture contract bounds missing"
grep -q 'exact failing scenario/executable check' "$ROOT/phases/phase-4-review-approval.md" \
  && grep -q 'Architecture falsification' "$ROOT/phases/phase-6-verify.md" \
  && grep -q 'Demand architecture change only' "$ROOT/phases/phase-7-review-commit.md" \
  && ok "architecture falsifier reaches review and verify" || bad "architecture falsifier wiring missing"
if [ -f "$ROOT/hooks/memory_router/standards.py" ] \
  && [ -f "$ROOT/hooks/memory_router/tests/test_standards.py" ] \
  && grep -q '"standards": _standards.run' "$ROOT/hooks/memory_router/__main__.py" \
  && grep -q 'standards select --affected' "$ROOT/reference.md"; then
  ok "scoped standards selector installed"
else
  bad "scoped standards selector missing"
fi
grep -q 'working-tree-gate.sh' "$ROOT/reference.md" && ok "reference documents working-tree gate helper" || bad "missing working-tree gate helper in reference.md"
grep -q 'clarify-gate.sh' "$ROOT/reference.md" && ok "reference documents clarify gate helper" || bad "missing clarify gate helper in reference.md"
grep -q 'plan-blocker-gate.sh' "$ROOT/reference.md" && ok "reference documents plan-blocker gate helper" || bad "missing plan-blocker gate helper in reference.md"
grep -q 'red-green-gate.sh' "$ROOT/reference.md" && ok "reference documents red-green gate helper" || bad "missing red-green gate helper in reference.md"
grep -q 'BUG-REPRO.md' "$ROOT/reference.md" && ok "reference documents BUG-REPRO evidence" || bad "missing BUG-REPRO evidence in reference.md"
grep -q 'lsp-diagnostics.sh' "$ROOT/reference.md" && ok "reference documents local diagnostics helper" || bad "missing local diagnostics helper in reference.md"
grep -q 'memory-router.sh' "$ROOT/reference.md" && ok "reference documents memory router helper" || bad "missing memory router helper in reference.md"
for term in PRIVACY-CAPSULE.json 'lifecycle --write' 'lifecycle --restore' 'capsule --write' 'six-field Privacy Capsule'; do
  grep -q -- "$term" "$ROOT/reference.md" && ok "memory lifecycle documented: $term" || bad "memory lifecycle missing: $term"
done
grep -q 'active-run.sh' "$ROOT/reference.md" && ok "reference documents active session helper" || bad "missing active session helper in reference.md"
grep -q 'Active Session Contract' "$ROOT/SKILL.md" && ok "canonical skill documents Active Session Contract" || bad "missing Active Session Contract in SKILL.md"
grep -q 'Current-State Pulse / Gate' "$ROOT/SKILL.md" && ok "canonical skill documents Current-State Pulse / Gate" || bad "missing Current-State Pulse / Gate in SKILL.md"
grep -q 'discovery-gate.sh' "$ROOT/SKILL.md" && ok "canonical skill documents Discovery Gate" || bad "missing Discovery Gate in SKILL.md"
grep -q 'Flow schema: 4' "$ROOT/phases/phase-0-setup.md" && ok "new runs declare flow schema 4" || bad "phase 0 missing flow schema 4"
grep -q -- '--state .kimiflow/<slug>/STATE.md' "$ROOT/phases/phase-4-review-approval.md" && ok "Build risk reads durable STATE" || bad "Phase 4 does not bind Build risk to STATE"
grep -q 'No routine Human Gate here' "$ROOT/phases/phase-1-clarify.md" && ok "fixes skip early confirmation stop" || bad "Phase 1 still requires an early fix confirmation"
grep -q 'Schema-3 runs retain their legacy' "$ROOT/phases/phase-4-review-approval.md" && ok "schema3 Preview stays resumable" || bad "Phase 4 missing schema3 compatibility"
grep -q 'Schema 4 does this automatically under the original build authority' "$ROOT/phases/phase-7-review-commit.md" && ok "schema4_atomic_commit_contract" || bad "Phase 7 missing schema4 atomic commit"
grep -q 'Clean-tree verification checkpoint' "$ROOT/phases/phase-5-build.md" \
  && grep -q 'STATE-backed `started_head`' "$ROOT/phases/phase-7-review-commit.md" \
  && ok "schema4_clean_tree_verification_checkpoint" || bad "clean-tree verification checkpoint/review basis missing"
grep -Fq '${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/test-weakening-scan.sh' "$ROOT/phases/phase-5-build.md" \
  && ok "Phase 5 test-weakening scan is plugin-rooted" || bad "Phase 5 test-weakening scan is not plugin-rooted"
grep -q 'git ls-files --others --exclude-standard' "$ROOT/phases/phase-7-review-commit.md" \
  && ok "phase7_named_untracked_review_basis" || bad "Phase 7 review basis omits named untracked files"
grep -q -- '--record-fix-approval' "$ROOT/reference.md" && ok "reference documents schema3 Fix Preview compatibility" || bad "reference missing schema3 Fix Preview compatibility"
grep -q 'working-tree-gate.sh' "$ROOT/SKILL.md" && ok "canonical skill documents working-tree gate" || bad "missing working-tree gate in SKILL.md"
grep -q 'clarify-gate.sh' "$ROOT/SKILL.md" && ok "canonical skill documents clarify gate" || bad "missing clarify gate in SKILL.md"
grep -q 'red-green-gate.sh' "$ROOT/SKILL.md" && ok "canonical skill documents red-green gate" || bad "missing red-green gate in SKILL.md"
grep -q 'lsp-diagnostics.sh' "$ROOT/SKILL.md" && ok "canonical skill documents local diagnostics" || bad "missing local diagnostics in SKILL.md"
grep -q 'Existing feature check' "$ROOT/reference.md" && ok "reference documents existing feature check" || bad "missing existing feature check in reference.md"
grep -q -- '--verify-feature' "$ROOT/SKILL.md" && ok "canonical skill documents verify-feature mode" || bad "missing verify-feature mode in SKILL.md"
grep -q 'Memory Router & Learning Loop' "$ROOT/SKILL.md" && ok "canonical skill documents Memory Router" || bad "missing Memory Router in SKILL.md"
grep -q 'code-review ensemble' "$ROOT/SKILL.md" && ok "canonical skill documents code-review ensemble" || bad "missing code-review ensemble in SKILL.md"
grep -q 'Code-review ensemble' "$ROOT/reference.md" && ok "reference documents code-review ensemble" || bad "missing code-review ensemble in reference.md"
grep -q 'CANDIDATE <SEVERITY>' "$ROOT/reference.md" && ok "reference documents review candidates" || bad "missing review candidate format in reference.md"
grep -q 'code-verified' "$ROOT/reference.md" && ok "reference documents promoted code-review findings" || bad "missing code-review promoted findings in reference.md"
grep -q 'review_base_sha' "$ROOT/reference.md" && ok "reference pins one review basis" || bad "missing fixed review basis in reference.md"
grep -q 'Spec / Correctness' "$ROOT/reference.md" && ok "reference preserves review axes" || bad "missing axis-preserving review report"
grep -q 'Standards smell baseline' "$ROOT/reference.md" && ok "reference documents advisory smell baseline" || bad "missing standards smell baseline"
grep -q 'Scope classification' "$ROOT/reference.md" && ok "reference classifies research scope" || bad "missing research scope classification"
grep -q 'depth=none|pulse|focused' "$ROOT/reference.md" && ok "reference documents adaptive Discovery" || bad "missing adaptive Discovery contract"
grep -q 'Reference Strategy Fit' "$ROOT/phases/phase-2-understand.md" && ok "Phase 2 documents conditional Reference Strategy Fit" || bad "Phase 2 missing Reference Strategy Fit"
grep -Eiq 'pulse.{0,160}(at most|max(imum)?)[^0-9]{0,20}2|pulse.{0,160}two.{0,160}references' "$ROOT/reference.md" \
  && grep -Eiq 'focused.{0,160}(at most|max(imum)?)[^0-9]{0,20}3|focused.{0,160}three.{0,160}references' "$ROOT/reference.md" \
  && ok "reference bounds Reference Strategy Fit evidence" || bad "reference missing bounded Reference Strategy Fit evidence"
if grep -Fq 'small`/`quick` go straight to the web' "$ROOT/phases/phase-2-understand.md" "$ROOT/reference.md"; then
  bad "small/quick fixes still force web research"
else
  ok "small/quick fix research is adaptive"
fi
grep -Eiq 'research limit.{0,120}(never|not).{0,80}user wait' "$ROOT/reference.md" \
  && ok "reference keeps research limits autonomous" || bad "reference missing autonomous research-limit routing"
grep -Fq "Caps are total for the run's fit assessment" "$ROOT/phases/phase-2-understand.md" \
  && grep -Fq 'three total references for the fit assessment' "$ROOT/reference.md" \
  && ok "Reference Strategy Fit cap cannot multiply across questions or lanes" || bad "Reference Strategy Fit cap is not run-total"
grep -Fq 'does not suppress a later named Discovery/Reference Strategy Fit gap' "$ROOT/phases/phase-2-understand.md" \
  && grep -Fq 'does not suppress a later named Discovery/Reference Strategy Fit gap' "$ROOT/reference.md" \
  && ok "Current State freshness does not suppress Reference Strategy Fit" || bad "Current State conflicts with Reference Strategy Fit"
grep -Fq 'Explicit prior-work cue override' "$ROOT/phases/phase-2-understand.md" \
  && grep -Fq 'Explicit prior-work cue override' "$ROOT/reference.md" \
  && ok "explicit prior-fix cues override small/quick recall skip" || bad "explicit prior-fix recall override missing"
grep -Fq 'MR recall --targeted --strategies --query-file <PROBLEM.md> --max 5 --write .kimiflow/<slug>/RECALL.md' "$ROOT/phases/phase-2-understand.md" \
  && grep -Fq 'replaces the default broad recall' "$ROOT/reference.md" \
  && grep -Fq 'continues without a user question' "$ROOT/reference.md" \
  && ok "prior-fix recall is bounded and non-interactive" || bad "prior-fix recall is broad or interactive"
grep -q 'Build Preview / Risk Gate' "$ROOT/reference.md" && ok "reference documents conditional Build Preview" || bad "missing Build Preview risk policy"
grep -q 'research-driven product expansion is forbidden' "$ROOT/reference.md" && ok "reference blocks research scope creep" || bad "missing research scope-creep guard"
grep -q -- '--epoch-start <S>' "$ROOT/reference.md" && ok "reference documents strategy epoch bounds" || bad "missing strategy epoch bounds"
grep -q -- '--gate <plan|code>' "$ROOT/reference.md" && ok "reference binds strategy epochs to review gate" || bad "missing strategy epoch gate binding"
grep -q 'kimiflow:recovery gate=<plan|code>' "$ROOT/reference.md" && ok "reference documents recovery receipt" || bad "missing recovery receipt contract"
grep -q 'kimiflow:strategy gate=<plan|code>' "$ROOT/reference.md" && ok "reference documents verified strategy baseline" || bad "missing strategy baseline contract"
grep -Eq -- 'await-user .*--kind <kind>' "$ROOT/reference.md" && ok "reference documents typed user pauses" || bad "missing typed user pause contract"
grep -q 'Autonomous recovery contract' "$ROOT/reference.md" && ok "reference documents autonomous review recovery" || bad "missing autonomous review recovery"
grep -q 'Minimum-complete' "$ROOT/SKILL.md" && ok "canonical skill keeps minimum-complete planning loaded" || bad "missing minimum-complete core rule"
grep -q 'Scope size alone never adds a second planner' "$ROOT/reference.md" && ok "reference keeps dual-plan conditional" || bad "missing conditional dual-plan guard"
grep -q 'potentially_stale' "$ROOT/reference.md" && ok "reference documents per-section staleness" || bad "missing per-section staleness in reference.md"
grep -q 'phase2_depth' "$ROOT/reference.md" && ok "reference documents adaptive map coverage depth" || bad "missing adaptive map coverage depth in reference.md"
for term in MEMORY.md USER.md LEARNINGS.jsonl USER.jsonl MEMORY-INDEX.json MEMORY-USAGE.json RECALL.sqlite RECALL.md RUN-HISTORY.json VAULT-PROVIDER.json VAULT-PREFETCH.md VAULT-SYNC.md SKILL-DRAFTS PENDING-PROPOSALS.md PROPOSALS.jsonl LEARNING-REVIEW.md review-run verify-run 'history --query' metrics 'provider status' 'provider health' 'provider setup' 'provider detect' 'provider sync' 'Vault Pulse' 'vault-mcp-setup.sh' 'vault-mcp-open-terminal.sh' '--interactive' bearer_token_env_var headersHelper 'index --write' 'consolidate --write' 'propose --write' '--approve' '--reject' '--apply' evidence_fingerprints 'Learning quality gate' 'Source freshness gate' provider_sync_pending provider_detected_unconfigured provider_auth_required provider_auth_failed connected_local_only authenticated auth_failed; do
  grep -q -- "$term" "$ROOT/reference.md" && ok "memory artifact documented: $term" || bad "memory artifact missing: $term"
done
for term in 'Storage targets' 'kimiflow+vault' 'repo-docs' 'IMPROVEMENTS.md' 'DOCS-PLAN.md'; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map publishing documented: $term" || bad "project map publishing missing: $term"
done
for term in 'Raw map vs. publishable docs' 'Repo-doc publish safety' 'never auto-commit `.kimiflow/project/`' 'concrete vulnerabilities' 'sanitized version'; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map publish safety documented: $term" || bad "project map publish safety missing: $term"
done

echo "== hooks wiring (referenced scripts exist, executable, valid) =="
echo "== optional embedded-first terminal runner =="
for rel in hooks/kimiflow-runner.sh hooks/install-kimiflow-cli.sh hooks/test-kimiflow-runner.sh hooks/test-install-kimiflow-cli.sh; do
  if [ -x "$ROOT/$rel" ] && bash -n "$ROOT/$rel" 2>/dev/null; then ok "runner surface ok: $rel"; else bad "runner surface missing/not-exec/bad: $rel"; fi
done
PYTHONPATH="$ROOT/hooks" python3 -c 'from kimiflow_core import runner; assert runner.RECEIPT_RELATIVE == ".kimiflow/session/HEADLESS_RUN.json"' 2>/dev/null \
  && ok "shared-core runner module imports" || bad "shared-core runner module unavailable"
grep -q 'embedded plugin remains the default' "$ROOT/README.md" \
  && grep -q 'same `.kimiflow/` state' "$ROOT/README.md" \
  && ok "smoke_embedded_first_runner_docs" || bad "embedded-first runner docs missing"
jq -e '.hooks.SessionStart[0].hooks | any(.command | contains("active-run.sh session-bootstrap"))' "$ROOT/hooks/hooks.json" >/dev/null 2>&1 \
  && ok "Claude SessionStart persists Kimiflow session identity" || bad "Claude SessionStart identity bootstrap missing"
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  rel="$(printf '%s\n' "$cmd" | grep -oE 'hooks/[^ "]*\.sh' | head -1)"
  p="$ROOT/$rel"
  if [ -x "$p" ] && bash -n "$p" 2>/dev/null; then ok "hook script ok: $rel"; else bad "hook script missing/not-exec/bad: $rel"; fi
done < <(jq -r '.hooks[]?[]?.hooks[]?.command' "$ROOT/hooks/hooks.json" 2>/dev/null)

echo "== gate fires (commit-secret-gate, synthetic PreToolUse stdin) =="
HOOK="$ROOT/hooks/commit-secret-gate.sh"
deny() { jq -nc --arg c "$1" --arg d "$2" '{tool_input:{command:$c}, cwd:$d}' | bash "$HOOK" 2>/dev/null | grep -q '"permissionDecision":"deny"'; }
tmp1="$(mktemp -d)"; ( cd "$tmp1" && git init -q && mkdir .kimiflow )
tmp2="$(mktemp -d)"; ( cd "$tmp2" && git init -q )
if deny 'git add .' "$tmp1"; then ok "blocks 'git add .' in a .kimiflow repo"; else bad "did NOT block 'git add .' in a .kimiflow repo"; fi
if deny 'git add .' "$tmp2"; then bad "wrongly blocked 'git add .' OUTSIDE a kimiflow repo"; else ok "allows 'git add .' outside a kimiflow repo"; fi
rm -rf "$tmp1" "$tmp2"

echo "== phase-read enforcement (consumer-shaped scratch project) =="
consumer="$(mktemp -d)"
( cd "$consumer" && git init -q )
mkdir -p "$consumer/.kimiflow/demo"
cat > "$consumer/.kimiflow/demo/STATE.md" <<'EOF'
Flow schema: 4
Status: active
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
active_out="$(KIMIFLOW_PLUGIN_ROOT="$ROOT" "$ROOT/hooks/active-run.sh" start --root "$consumer" --run .kimiflow/demo --write 2>/dev/null || true)"
if printf '%s\n' "$active_out" | jq -e '.phase_reads_required == true' >/dev/null 2>&1; then
  ok "active-run start enables phase reads from plugin root"
else
  bad "active-run start did not enable phase reads in scratch consumer"
fi
if [ ! -e "$consumer/phases" ]; then
  ok "scratch consumer has no local phases directory"
else
  bad "scratch consumer unexpectedly has local phases directory"
fi
phase_gate="$(KIMIFLOW_PLUGIN_ROOT="$ROOT" "$ROOT/hooks/active-run.sh" phase-read-gate --root "$consumer" --run .kimiflow/demo --through-phase 1 2>/dev/null || true)"
if printf '%s\n' "$phase_gate" | grep -q $'PHASE_READ_GATE\tCLOSED' \
  && printf '%s\n' "$phase_gate" | grep -q 'phase_0_read_missing'; then
  ok "phase-read gate closes on missing consumer read"
else
  bad "phase-read gate did not close on missing consumer read"
fi
next_action="$(KIMIFLOW_PLUGIN_ROOT="$ROOT" "$ROOT/hooks/active-run.sh" next-action --root "$consumer" 2>/dev/null || true)"
if printf '%s\n' "$next_action" | jq -e '.graph_status == "ready" and .current_node == "phase_5" and (.action | length > 0)' >/dev/null 2>&1; then
  ok "active-run resolves installed transition graph"
else
  bad "active-run did not resolve installed transition graph"
fi
rm -rf "$consumer"

echo "== MANUAL (needs a live Claude Code session — cannot be automated) =="
cat <<'MANUAL'
  [ ] /plugin marketplace add kimikonapps/kimiflow && /plugin install kimiflow@kimiflow → restart
  [ ] type "/kimiflow" → the command appears and fires (slash invocation works; cf. CC #26251)
  [ ] an actionable implementation request for a substantial cross-surface/integration/data/security/API/architecture/discovery feature auto-routes into Kimiflow
  [ ] a discussion, idea, recommendation, explanation/status request, or wish formulation stays direct and read-only
  [ ] a normal fix, review, refactor, cleanup, docs/config task, or small low-risk feature stays direct unless Kimiflow is explicit
  [ ] explicit "direct" or "direkt" bypasses Kimiflow and explicit "with kimiflow" launches it
  [ ] in a repo with .kimiflow/, attempting `git add .` is blocked by the commit-secret-gate hook
  [ ] the Stop test-gate engages when .kimiflow/test-gate is present and tests are red
  [ ] while an active Kimiflow session exists, its owner stays gated while a second project session can read, answer, and plan without any Stop continuation
MANUAL

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "SMOKE OK (structural)"; exit 0; else echo "$FAILS SMOKE FAILURE(S)"; exit 1; fi
