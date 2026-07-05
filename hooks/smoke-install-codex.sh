#!/usr/bin/env bash
# kimiflow — Codex install smoke-test. Verifies the Codex plugin layer, skill
# entrypoint, stable hook installer, optional plugin hook wiring, and synthetic
# Codex-shaped hook payloads.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILS=0
ok()  { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }

command -v jq  >/dev/null 2>&1 || { echo "smoke-install-codex: jq required"; exit 2; }
command -v git >/dev/null 2>&1 || { echo "smoke-install-codex: git required"; exit 2; }

echo "== codex manifests =="
for j in .codex-plugin/plugin.json .agents/plugins/marketplace.json hooks.json; do
  if jq -e . "$ROOT/$j" >/dev/null 2>&1; then ok "valid JSON: $j"; else bad "invalid JSON: $j"; fi
done
cv="$(jq -r '.version' "$ROOT/.codex-plugin/plugin.json" 2>/dev/null)"
pv="$(jq -r '.version' "$ROOT/.claude-plugin/plugin.json" 2>/dev/null)"
if [ -n "$cv" ] && [ "$cv" = "$pv" ]; then ok "version consistent with Claude manifest ($cv)"; else bad "version mismatch: codex=$cv claude=$pv"; fi
jq -e '.name == "kimiflow" and .skills == "./skills/" and (.interface.displayName | length > 0)' "$ROOT/.codex-plugin/plugin.json" >/dev/null 2>&1 \
  && ok "codex plugin shape" || bad "codex plugin shape"
jq -e '(.interface.defaultPrompt // []) | any(test("codebase|architecture|refactoring|Document"))' "$ROOT/.codex-plugin/plugin.json" >/dev/null 2>&1 \
  && ok "codex plugin Project Intelligence prompts visible" || bad "codex plugin Project Intelligence prompts missing"
jq -e '((.interface.longDescription // "") + " " + (.interface.shortDescription // "") + " " + (.description // "")) | test("codebase|architecture|project intelligence")' "$ROOT/.codex-plugin/plugin.json" >/dev/null 2>&1 \
  && ok "codex plugin describes project intelligence" || bad "codex plugin description does not mention project intelligence"
jq -e '((.interface.longDescription // "") + " " + (.description // "")) | test("code-review ensemble")' "$ROOT/.codex-plugin/plugin.json" >/dev/null 2>&1 \
  && ok "codex plugin describes code-review ensemble" || bad "codex plugin description does not mention code-review ensemble"
jq -e '((.interface.longDescription // "") + " " + (.interface.shortDescription // "") + " " + (.description // "") + " " + ((.interface.defaultPrompt // []) | join(" "))) | test("full/grill/plan/build/quick/review/audit/fix"; "i") and test("kimiflow full"; "i") and test("grill"; "i") and test("plan"; "i")' "$ROOT/.codex-plugin/plugin.json" >/dev/null 2>&1 \
  && ok "codex plugin exposes natural mode aliases" || bad "codex plugin natural mode aliases missing"
jq -e '.name == "kimiflow" and (.plugins[] | select(.name == "kimiflow" and .source.path == "./"))' "$ROOT/.agents/plugins/marketplace.json" >/dev/null 2>&1 \
  && ok "codex marketplace entry" || bad "codex marketplace entry"
jq -e '[.. | strings] | join(" ") | test("full/grill/plan/build/quick/review/audit/fix"; "i")' "$ROOT/.agents/plugins/marketplace.json" >/dev/null 2>&1 \
  && ok "codex marketplace describes natural mode aliases" || bad "codex marketplace missing natural mode aliases"
jq -e '[.hooks[]?[]?.hooks[]? | select(.type == "command")] | length == 6 and all(.[]; (.name // "" | length > 0) and (.description // "" | length > 0) and (.statusMessage // "" | length > 0))' "$ROOT/hooks.json" >/dev/null 2>&1 \
  && ok "codex plugin hooks are labelled" || bad "codex plugin hook labels missing"

echo "== capability display sync (Codex) =="
# Four canonical capabilities must each appear PER-FIELD in the prominent shortDescription surfaces (non-vacuous drift guard;
# checking the concatenated long+short surface would let longDescription mask a drop in shortDescription).
for m in 'feature[^.]*fix' 'project intelligence' 'repo docs' 'findings'; do
  jq -e --arg m "$m" '((.interface.shortDescription // "") | test($m; "i"))' "$ROOT/.codex-plugin/plugin.json" >/dev/null 2>&1 \
    && ok "codex shortDescription names capability: $m" || bad "codex shortDescription missing capability: $m"
done
for m in 'feature[^.]*fix' 'project intelligence' 'repo docs' 'findings'; do
  jq -e --arg m "$m" '((.interface.shortDescription // "") | test($m; "i"))' "$ROOT/.agents/plugins/marketplace.json" >/dev/null 2>&1 \
    && ok "codex marketplace shortDescription names capability: $m" || bad "codex marketplace shortDescription missing capability: $m"
done

echo "== codex skill =="
SKILL="$ROOT/skills/kimiflow/SKILL.md"
if [ -f "$SKILL" ]; then ok "skill exists: skills/kimiflow/SKILL.md"; else bad "missing skills/kimiflow/SKILL.md"; fi
fm="$(awk 'NR==1 && $0=="---"{f=1;next} f && $0=="---"{exit} f' "$SKILL" 2>/dev/null || true)"
printf '%s\n' "$fm" | grep -qE '^name:[[:space:]]*kimiflow' && ok "skill name: kimiflow" || bad "skill name missing/wrong"
printf '%s\n' "$fm" | grep -qE '^description:' && ok "skill description present" || bad "skill description missing"
printf '%s\n' "$fm" | grep -qi 'explicitly asks' && ok "opt-in guard present" || bad "opt-in guard missing"
[ -f "$ROOT/skills/kimiflow/agents/openai.yaml" ] && ok "Codex skill metadata exists" || bad "missing agents/openai.yaml"
grep -q 'KIMIFLOW_PLUGIN_ROOT/hooks/resolve-review-gate.sh' "$SKILL" && ok "skill uses absolute plugin-root helper paths" || bad "skill does not use plugin-root helper paths"
if grep -q '\.\./\.\./hooks/' "$SKILL"; then bad "skill still documents cwd-sensitive ../../hooks paths"; else ok "skill avoids cwd-sensitive ../../hooks paths"; fi
grep -q -- '--project-map <quick|skip>' "$SKILL" && ok "Codex wrapper maps project-map invocation" || bad "Codex wrapper missing project-map invocation mapping"
grep -q -- '--verify-feature <feature-or-path>' "$ROOT/SKILL.md" && ok "canonical verify-feature argument present" || bad "canonical verify-feature argument missing"
grep -q -- '--verify-feature <feature-or-path>' "$SKILL" && ok "Codex wrapper maps verify-feature invocation" || bad "Codex wrapper missing verify-feature invocation mapping"
grep -q 'launcher-status.sh' "$SKILL" && ok "Codex wrapper maps launcher status helper" || bad "Codex wrapper missing launcher status helper"
grep -q 'Launcher / menu' "$ROOT/SKILL.md" && ok "canonical Launcher mode present" || bad "canonical Launcher mode missing"
grep -q 'Launcher mode' "$ROOT/reference.md" && ok "canonical Launcher mode documented" || bad "canonical Launcher mode docs missing"
grep -q 'Natural mode aliases' "$ROOT/SKILL.md" && ok "canonical natural mode aliases present" || bad "canonical natural mode aliases missing"
grep -q 'Natural mode aliases' "$ROOT/reference.md" && ok "canonical natural mode aliases documented" || bad "canonical natural mode aliases docs missing"
grep -q 'full|grill|plan|build|quick|review|audit|fix' "$SKILL" && ok "Codex wrapper maps natural mode aliases" || bad "Codex wrapper missing natural mode aliases"
for term in 'kimiflow full' 'kimiflow grill' 'kimiflow plan' 'kimiflow build' 'kimiflow quick' 'kimiflow review' 'kimiflow audit' 'kimiflow fix'; do
  grep -q "$term" "$SKILL" && ok "Codex wrapper documents plain alias: $term" || bad "Codex wrapper missing plain alias: $term"
done
for term in 'kimiflow full' 'kimiflow grill' 'kimiflow plan' 'kimiflow build' 'kimiflow review' 'kimiflow audit' 'kimiflow fix' 'kimiflow quick'; do
  grep -q "$term" "$ROOT/README.md" && ok "README documents mode alias: $term" || bad "README missing mode alias: $term"
done
grep -q 'pre-build approval stop' "$ROOT/SKILL.md" && ok "full mode includes pre-build approval stop" || bad "full mode missing pre-build approval stop"
grep -q 'mandatory micro-grill' "$ROOT/SKILL.md" && ok "canonical skill requires micro-grill for small/quick" || bad "canonical skill missing small/quick micro-grill"
grep -q 'mandatory micro-grill' "$SKILL" && ok "Codex wrapper preserves small/quick micro-grill" || bad "Codex wrapper missing small/quick micro-grill"
grep -q 'Mandatory micro-grill for small/quick' "$ROOT/reference.md" && ok "reference documents small/quick micro-grill" || bad "reference missing small/quick micro-grill"
grep -q 'Micro-Grill' "$ROOT/README.md" && ok "README documents small/quick micro-grill" || bad "README missing small/quick micro-grill"
grep -q 'Vault Pulse' "$ROOT/SKILL.md" && ok "canonical skill requires scope=large Vault Pulse semantics" || bad "canonical skill missing Vault Pulse"
grep -q 'Vault Pulse' "$SKILL" && ok "Codex wrapper preserves scope=large Vault Pulse semantics" || bad "Codex wrapper missing Vault Pulse"
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
grep -q 'Resume safety check' "$ROOT/reference.md" && ok "resume safety check documented" || bad "resume safety check missing"
if [ -x "$ROOT/hooks/launcher-status.sh" ] && bash -n "$ROOT/hooks/launcher-status.sh" 2>/dev/null; then ok "launcher status helper ok"; else bad "launcher status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-launcher-status.sh" ] && bash -n "$ROOT/hooks/test-launcher-status.sh" 2>/dev/null; then ok "launcher status test ok"; else bad "launcher status test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/active-run.sh" ] && bash -n "$ROOT/hooks/active-run.sh" 2>/dev/null; then ok "active session helper ok"; else bad "active session helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-active-run.sh" ] && bash -n "$ROOT/hooks/test-active-run.sh" 2>/dev/null; then ok "active session test ok"; else bad "active session test missing/not-exec/bad"; fi
grep -q 'Project Map Bootstrap' "$ROOT/SKILL.md" && ok "canonical Project Map Bootstrap present" || bad "canonical Project Map Bootstrap missing"
grep -q 'FACTS.jsonl' "$ROOT/reference.md" && ok "project map evidence artifact documented" || bad "project map evidence artifact missing"
if [ -x "$ROOT/hooks/project-map-status.sh" ] && bash -n "$ROOT/hooks/project-map-status.sh" 2>/dev/null; then ok "project map status helper ok"; else bad "project map status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-project-map-status.sh" ] && bash -n "$ROOT/hooks/test-project-map-status.sh" 2>/dev/null; then ok "project map status test ok"; else bad "project map status test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/suggest-affected-sections.sh" ] && bash -n "$ROOT/hooks/suggest-affected-sections.sh" 2>/dev/null; then ok "suggest-affected helper ok"; else bad "suggest-affected helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-suggest-affected-sections.sh" ] && bash -n "$ROOT/hooks/test-suggest-affected-sections.sh" 2>/dev/null; then ok "suggest-affected test ok"; else bad "suggest-affected test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/map-staleness-nudge.sh" ] && bash -n "$ROOT/hooks/map-staleness-nudge.sh" 2>/dev/null; then ok "map staleness nudge helper ok"; else bad "map staleness nudge helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-map-staleness-nudge.sh" ] && bash -n "$ROOT/hooks/test-map-staleness-nudge.sh" 2>/dev/null; then ok "map staleness nudge test ok"; else bad "map staleness nudge test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/current-state-gate.sh" ] && bash -n "$ROOT/hooks/current-state-gate.sh" 2>/dev/null; then ok "current-state gate helper ok"; else bad "current-state gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-current-state-gate.sh" ] && bash -n "$ROOT/hooks/test-current-state-gate.sh" 2>/dev/null; then ok "current-state gate test ok"; else bad "current-state gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/working-tree-gate.sh" ] && bash -n "$ROOT/hooks/working-tree-gate.sh" 2>/dev/null; then ok "working-tree gate helper ok"; else bad "working-tree gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-working-tree-gate.sh" ] && bash -n "$ROOT/hooks/test-working-tree-gate.sh" 2>/dev/null; then ok "working-tree gate test ok"; else bad "working-tree gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/clarify-gate.sh" ] && bash -n "$ROOT/hooks/clarify-gate.sh" 2>/dev/null; then ok "clarify gate helper ok"; else bad "clarify gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-clarify-gate.sh" ] && bash -n "$ROOT/hooks/test-clarify-gate.sh" 2>/dev/null; then ok "clarify gate test ok"; else bad "clarify gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/plan-blocker-gate.sh" ] && bash -n "$ROOT/hooks/plan-blocker-gate.sh" 2>/dev/null; then ok "plan-blocker gate helper ok"; else bad "plan-blocker gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-plan-blocker-gate.sh" ] && bash -n "$ROOT/hooks/test-plan-blocker-gate.sh" 2>/dev/null; then ok "plan-blocker gate test ok"; else bad "plan-blocker gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/red-green-gate.sh" ] && bash -n "$ROOT/hooks/red-green-gate.sh" 2>/dev/null; then ok "red-green gate helper ok"; else bad "red-green gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-red-green-gate.sh" ] && bash -n "$ROOT/hooks/test-red-green-gate.sh" 2>/dev/null; then ok "red-green gate test ok"; else bad "red-green gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/lsp-diagnostics.sh" ] && bash -n "$ROOT/hooks/lsp-diagnostics.sh" 2>/dev/null; then ok "local diagnostics helper ok"; else bad "local diagnostics helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-lsp-diagnostics.sh" ] && bash -n "$ROOT/hooks/test-lsp-diagnostics.sh" 2>/dev/null; then ok "local diagnostics test ok"; else bad "local diagnostics test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/memory-router.sh" ] && bash -n "$ROOT/hooks/memory-router.sh" 2>/dev/null; then ok "memory router helper ok"; else bad "memory router helper missing/not-exec/bad"; fi
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
grep -q -- 'refresh --changed' "$ROOT/SKILL.md" && ok "canonical skill documents Phase-7 auto-refresh" || bad "canonical Phase-7 auto-refresh missing"
grep -q 'suggest-affected-sections.sh' "$SKILL" && ok "Codex wrapper maps suggest-affected helper" || bad "Codex wrapper missing suggest-affected helper"
grep -q 'map-staleness-nudge.sh' "$SKILL" && ok "Codex wrapper maps map staleness nudge helper" || bad "Codex wrapper missing map staleness nudge helper"
grep -q 'current-state-gate.sh' "$ROOT/reference.md" && ok "canonical current-state gate helper documented" || bad "canonical current-state gate helper missing"
grep -q 'working-tree-gate.sh' "$ROOT/reference.md" && ok "canonical working-tree gate helper documented" || bad "canonical working-tree gate helper missing"
grep -q 'clarify-gate.sh' "$ROOT/reference.md" && ok "canonical clarify gate helper documented" || bad "canonical clarify gate helper missing"
grep -q 'plan-blocker-gate.sh' "$ROOT/reference.md" && ok "canonical plan-blocker gate helper documented" || bad "canonical plan-blocker gate helper missing"
grep -q 'red-green-gate.sh' "$ROOT/reference.md" && ok "canonical red-green gate helper documented" || bad "canonical red-green gate helper missing"
grep -q 'BUG-REPRO.md' "$ROOT/reference.md" && ok "canonical BUG-REPRO evidence documented" || bad "canonical BUG-REPRO evidence missing"
grep -q 'lsp-diagnostics.sh' "$ROOT/reference.md" && ok "canonical local diagnostics helper documented" || bad "canonical local diagnostics helper missing"
grep -q 'memory-router.sh' "$ROOT/reference.md" && ok "canonical memory router helper documented" || bad "canonical memory router helper missing"
grep -q 'active-run.sh' "$ROOT/reference.md" && ok "canonical active session helper documented" || bad "canonical active session helper missing"
grep -q 'current-state-gate.sh' "$SKILL" && ok "Codex wrapper maps current-state gate helper" || bad "Codex wrapper missing current-state gate helper"
grep -q 'working-tree-gate.sh' "$SKILL" && ok "Codex wrapper maps working-tree gate helper" || bad "Codex wrapper missing working-tree gate helper"
grep -q 'clarify-gate.sh' "$SKILL" && ok "Codex wrapper maps clarify gate helper" || bad "Codex wrapper missing clarify gate helper"
grep -q 'plan-blocker-gate.sh' "$SKILL" && ok "Codex wrapper maps plan-blocker gate helper" || bad "Codex wrapper missing plan-blocker gate helper"
grep -q 'red-green-gate.sh' "$SKILL" && ok "Codex wrapper maps red-green gate helper" || bad "Codex wrapper missing red-green gate helper"
grep -q 'lsp-diagnostics.sh' "$SKILL" && ok "Codex wrapper maps local diagnostics helper" || bad "Codex wrapper missing local diagnostics helper"
grep -q 'memory-router.sh' "$SKILL" && ok "Codex wrapper maps memory router helper" || bad "Codex wrapper missing memory router helper"
grep -q 'active-run.sh' "$SKILL" && ok "Codex wrapper maps active session helper" || bad "Codex wrapper missing active session helper"
grep -q 'Existing feature check' "$ROOT/reference.md" && ok "canonical existing feature check documented" || bad "canonical existing feature check missing"
grep -q 'Memory Router & Learning Loop' "$ROOT/SKILL.md" && ok "canonical Memory Router present" || bad "canonical Memory Router missing"
grep -q 'code-review ensemble' "$ROOT/SKILL.md" && ok "canonical code-review ensemble present" || bad "canonical code-review ensemble missing"
grep -q 'Code-review ensemble' "$ROOT/reference.md" && ok "canonical code-review ensemble documented" || bad "canonical code-review ensemble docs missing"
grep -q 'CANDIDATE <SEVERITY>' "$ROOT/reference.md" && ok "canonical review candidate format documented" || bad "canonical review candidate format missing"
grep -q 'code-verified' "$ROOT/reference.md" && ok "canonical promoted code-review findings documented" || bad "canonical promoted code-review findings missing"
grep -q 'Review Ensemble' "$SKILL" && ok "Codex wrapper maps code-review ensemble" || bad "Codex wrapper missing code-review ensemble mapping"
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

echo "== codex plugin hook wiring (optional while plugin_hooks is unavailable) =="
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  rel="$(printf '%s\n' "$cmd" | grep -oE 'hooks/[^ "]*\.sh' | head -1)"
  p="$ROOT/$rel"
  if [ -x "$p" ] && bash -n "$p" 2>/dev/null; then ok "hook script ok: $rel"; else bad "hook script missing/not-exec/bad: $rel"; fi
done < <(jq -r '.hooks[]?[]?.hooks[]?.command' "$ROOT/hooks.json" 2>/dev/null)

echo "== stable codex hook installer =="
INSTALLER="$ROOT/hooks/install-codex-hooks.sh"
if [ -x "$INSTALLER" ] && bash -n "$INSTALLER" 2>/dev/null; then ok "installer script ok: hooks/install-codex-hooks.sh"; else bad "installer script missing/not-exec/bad"; fi
tmp_home="$(mktemp -d)"
if CODEX_HOME="$tmp_home/codex" "$INSTALLER" >/dev/null 2>&1; then ok "installer writes wrappers into temp CODEX_HOME"; else bad "installer failed in temp CODEX_HOME"; fi
for f in kimiflow-commit-secret-gate.sh kimiflow-state-gate.sh kimiflow-test-gate.sh kimiflow-active-run.sh; do
  wp="$tmp_home/codex/hooks/$f"
  if [ -x "$wp" ] && bash -n "$wp" 2>/dev/null && grep -q "KIMIFLOW_PLUGIN_ROOT=" "$wp"; then ok "wrapper ok: $f"; else bad "wrapper missing/bad: $f"; fi
done

# Stable-wrapper coverage is EXPLICIT, not incidental: the 6 hooks.json plugin-hook commands reduce to 5
# distinct scripts; the 4 stable wrappers cover the enforcement gates. The one script left unwrapped MUST be
# exactly map-staleness-nudge.sh (a non-blocking advisory nudge, deliberately plugin_hooks-only). Any other
# gap = drift (e.g. a new enforcement hook added to hooks.json but never wrapped).
wrapped_scripts="commit-secret-gate.sh state-gate.sh test-gate.sh active-run.sh"
uncovered=""
while IFS= read -r s; do
  [ -n "$s" ] || continue
  case " $wrapped_scripts " in
    *" $s "*) ;;
    *) uncovered="${uncovered:+$uncovered }$s" ;;
  esac
done < <(jq -r '.hooks[]?[]?.hooks[]?.command' "$ROOT/hooks.json" 2>/dev/null | grep -oE 'hooks/[^ "]*\.sh' | sed 's#hooks/##' | sort -u)
if [ "$uncovered" = "map-staleness-nudge.sh" ]; then
  ok "stable installer covers all enforcement gates; only plugin_hooks-only map-staleness-nudge is unwrapped"
else
  bad "stable-wrapper coverage drift: unwrapped plugin hooks = '$uncovered' (expected exactly 'map-staleness-nudge.sh')"
fi

echo "== codex gate fires (synthetic payloads) =="
COMMIT_HOOK="$tmp_home/codex/hooks/kimiflow-commit-secret-gate.sh"
STATE_HOOK="$tmp_home/codex/hooks/kimiflow-state-gate.sh"
TEST_HOOK="$tmp_home/codex/hooks/kimiflow-test-gate.sh"
ACTIVE_HOOK="$tmp_home/codex/hooks/kimiflow-active-run.sh"

deny_commit() { jq -nc --arg c "$1" --arg d "$2" '{tool_input:{args:{command:$c}}, cwd:$d, hook_event_name:"PreToolUse"}' | bash "$COMMIT_HOOK" 2>/dev/null | grep -q '"permissionDecision":"deny"'; }
deny_state()  { jq -nc --arg c "$1" --arg d "$2" '{tool_input:{args:{command:$c}}, cwd:$d, hook_event_name:"PreToolUse"}' | bash "$STATE_HOOK" 2>/dev/null | grep -q '"permissionDecision":"deny"'; }
block_stop()  { jq -nc --arg d "$1" '{cwd:$d, hook_input:{stop_hook_active:false}, hook_event_name:"Stop"}' | bash "$TEST_HOOK" 2>/dev/null | grep -qE '"decision"[[:space:]]*:[[:space:]]*"block"'; }
allow_stop_active() { out="$(jq -nc --arg d "$1" '{cwd:$d, hook_input:{stop_hook_active:true}, hook_event_name:"Stop"}' | bash "$TEST_HOOK" 2>/dev/null)"; [ -z "$out" ]; }
active_prompt_context() { jq -nc --arg d "$1" '{cwd:$d, prompt:"follow-up text", hook_event_name:"UserPromptSubmit"}' | bash "$ACTIVE_HOOK" prompt-context 2>/dev/null | grep -q 'additionalContext'; }
active_stop_blocks() { jq -nc --arg d "$1" '{cwd:$d, hook_input:{stop_hook_active:false}, hook_event_name:"Stop"}' | bash "$ACTIVE_HOOK" stop-gate 2>/dev/null | grep -qE '"decision"[[:space:]]*:[[:space:]]*"block"'; }

tmp1="$(mktemp -d)"; ( cd "$tmp1" && git init -q && mkdir .kimiflow )
tmp2="$(mktemp -d)"; ( cd "$tmp2" && git init -q )
if deny_commit 'git add .' "$tmp1"; then ok "commit-secret-gate blocks git add . in Codex payload"; else bad "commit-secret-gate did not block git add . in Codex payload"; fi
if deny_commit 'git add .' "$tmp2"; then bad "commit-secret-gate wrongly blocked outside Kimiflow repo"; else ok "commit-secret-gate allows outside Kimiflow repo"; fi
mkdir -p "$tmp1/.kimiflow/nostate/findings"
if deny_state './hooks/resolve-review-gate.sh .kimiflow/nostate/findings --round 1 --expect A,B' "$tmp1"; then ok "state-gate blocks missing STATE in Codex payload"; else bad "state-gate did not block missing STATE in Codex payload"; fi
printf 'false\n' > "$tmp1/.kimiflow/test-gate"
if block_stop "$tmp1"; then ok "test-gate blocks red tests in Codex payload"; else bad "test-gate did not block red tests in Codex payload"; fi
if allow_stop_active "$tmp1"; then ok "test-gate allows active stop continuation"; else bad "test-gate did not allow active stop continuation"; fi
mkdir -p "$tmp1/.kimiflow/demo"
cat > "$tmp1/.kimiflow/demo/STATE.md" <<'EOF'
Status: active
Affected files: README.md
Phase 0: done
Phase 5: in-progress
EOF
bash "$ACTIVE_HOOK" start --root "$tmp1" --run .kimiflow/demo --write >/dev/null
bash "$ACTIVE_HOOK" append-item --root "$tmp1" --title "synthetic active-session item" --write >/dev/null
if active_prompt_context "$tmp1"; then ok "active session hook injects Codex prompt context"; else bad "active session hook did not inject Codex prompt context"; fi
if active_stop_blocks "$tmp1"; then ok "active session Stop hook blocks unfinished session"; else bad "active session Stop hook did not block unfinished session"; fi

tmp3="$(mktemp -d)"
( cd "$tmp3" && git init -q )
active_phase_out="$(bash "$ACTIVE_HOOK" start --root "$tmp3" --run .kimiflow/demo --write 2>/dev/null || true)"
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
rm -rf "$tmp1" "$tmp2" "$tmp3" "$tmp_home"

echo "== MANUAL (needs Codex app/CLI plugin browser) =="
cat <<'MANUAL'
  [ ] Add the Git marketplace (`codex plugin marketplace add kimikonapps/kimiflow`), then install kimiflow.
  [ ] Run the stable hook installer from that marketplace checkout once.
  [ ] Start a new Codex thread and invoke "$kimiflow <tiny change>".
  [ ] Confirm Kimiflow launches only when explicitly requested.
  [ ] In a repo with .kimiflow/, attempting `git add .` is blocked by the installed stable Codex hook.
  [ ] With .kimiflow/test-gate containing a failing command, Codex Stop is blocked.
  [ ] With an active Kimiflow session, a follow-up prompt keeps Kimiflow active and Stop asks for finish/park/fail/abort.
MANUAL

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "CODEX SMOKE OK (structural)"; exit 0; else echo "$FAILS CODEX SMOKE FAILURE(S)"; exit 1; fi
