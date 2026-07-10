#!/usr/bin/env bash
# kimiflow — install smoke-test. Verifies the plugin is structurally installable and its gates
# are wired + actually FIRE, WITHOUT a live Claude Code session. Run before a release and on a
# Claude Code upgrade. Exits non-zero on any automatable failure.
#
# WHY this exists: Claude Code's plugin/skill invocation contract has had real regressions —
#   https://github.com/anthropics/claude-code/issues/26251  (slash invocation vs disable-model-invocation)
#   https://github.com/anthropics/claude-code/issues/22345  (plugin skills honoring disable-model-invocation)
# The structural half is automated below; the parts that need a real CC session (actual
# /plugin install, /kimiflow slash invocation, no-auto-trigger) are printed as a MANUAL checklist.
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
# Model-invocation is ENABLED (opt-in, on-request — the "invoke only when asked" policy lives in the
# description, not a hard flag). It must NOT be `true`, or the model can't launch kimiflow on request.
if printf '%s\n' "$fm" | grep -qE '^disable-model-invocation:[[:space:]]*true'; then bad "disable-model-invocation: true → model can't launch kimiflow on request"; else ok "model-invocable (disable-model-invocation not true) — opt-in/on-request per description"; fi
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
grep -q 'pre-build approval stop' "$ROOT/SKILL.md" && ok "full mode includes pre-build approval stop" || bad "full mode missing pre-build approval stop"
grep -q 'mandatory micro-grill' "$ROOT/SKILL.md" && ok "canonical skill requires micro-grill for small/quick" || bad "canonical skill missing small/quick micro-grill"
grep -q 'Mandatory micro-grill for small/quick' "$ROOT/reference.md" && ok "reference documents small/quick micro-grill" || bad "reference missing small/quick micro-grill"
grep -q 'Micro-Grill' "$ROOT/README.md" && ok "README documents small/quick micro-grill" || bad "README missing small/quick micro-grill"
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
grep -q 'project-map-status.sh' "$ROOT/reference.md" && ok "reference documents project-map status helper" || bad "missing project-map status helper in reference.md"
grep -q 'suggest-affected-sections.sh' "$ROOT/reference.md" && ok "reference documents suggest-affected helper" || bad "missing suggest-affected helper in reference.md"
grep -q 'map-staleness-nudge.sh' "$ROOT/reference.md" && ok "reference documents map staleness nudge helper" || bad "missing map staleness nudge helper in reference.md"
grep -q -- 'refresh --changed' "$ROOT/reference.md" && ok "reference documents auto delta refresh" || bad "missing refresh --changed in reference.md"
grep -q 'index-symbols' "$ROOT/reference.md" && ok "reference documents symbol index" || bad "missing index-symbols in reference.md"
grep -q -- 'refresh --changed' "$ROOT/SKILL.md" && ok "canonical skill documents Phase-7 auto-refresh" || bad "missing Phase-7 auto-refresh in SKILL.md"
grep -q 'suggest-affected-sections.sh' "$ROOT/SKILL.md" && ok "canonical skill documents Phase-2 section lookup" || bad "missing Phase-2 section lookup in SKILL.md"
grep -q 'current-state-gate.sh' "$ROOT/reference.md" && ok "reference documents current-state gate helper" || bad "missing current-state gate helper in reference.md"
grep -q 'working-tree-gate.sh' "$ROOT/reference.md" && ok "reference documents working-tree gate helper" || bad "missing working-tree gate helper in reference.md"
grep -q 'clarify-gate.sh' "$ROOT/reference.md" && ok "reference documents clarify gate helper" || bad "missing clarify gate helper in reference.md"
grep -q 'plan-blocker-gate.sh' "$ROOT/reference.md" && ok "reference documents plan-blocker gate helper" || bad "missing plan-blocker gate helper in reference.md"
grep -q 'red-green-gate.sh' "$ROOT/reference.md" && ok "reference documents red-green gate helper" || bad "missing red-green gate helper in reference.md"
grep -q 'BUG-REPRO.md' "$ROOT/reference.md" && ok "reference documents BUG-REPRO evidence" || bad "missing BUG-REPRO evidence in reference.md"
grep -q 'lsp-diagnostics.sh' "$ROOT/reference.md" && ok "reference documents local diagnostics helper" || bad "missing local diagnostics helper in reference.md"
grep -q 'memory-router.sh' "$ROOT/reference.md" && ok "reference documents memory router helper" || bad "missing memory router helper in reference.md"
grep -q 'active-run.sh' "$ROOT/reference.md" && ok "reference documents active session helper" || bad "missing active session helper in reference.md"
grep -q 'Active Session Contract' "$ROOT/SKILL.md" && ok "canonical skill documents Active Session Contract" || bad "missing Active Session Contract in SKILL.md"
grep -q 'Current-State Pulse / Gate' "$ROOT/SKILL.md" && ok "canonical skill documents Current-State Pulse / Gate" || bad "missing Current-State Pulse / Gate in SKILL.md"
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
rm -rf "$consumer"

echo "== MANUAL (needs a live Claude Code session — cannot be automated) =="
cat <<'MANUAL'
  [ ] /plugin marketplace add kimikonapps/kimiflow && /plugin install kimiflow@kimiflow → restart
  [ ] type "/kimiflow" → the command appears and fires (slash invocation works; cf. CC #26251)
  [ ] kimiflow launches when you ASK for it ("with kimiflow" / "run kimiflow") but does NOT fire unprompted on an unrelated request (opt-in policy is description-guided, not a hard flag; cf. CC #22345)
  [ ] in a repo with .kimiflow/, attempting `git add .` is blocked by the commit-secret-gate hook
  [ ] the Stop test-gate engages when .kimiflow/test-gate is present and tests are red
  [ ] while an active Kimiflow session exists, its owner stays gated while a second project session can read, answer, and plan without any Stop continuation
MANUAL

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "SMOKE OK (structural)"; exit 0; else echo "$FAILS SMOKE FAILURE(S)"; exit 1; fi
