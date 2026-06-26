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

echo "== skill frontmatter (SKILL.md) =="
fm="$(awk 'NR==1 && $0=="---"{f=1;next} f && $0=="---"{exit} f' "$ROOT/SKILL.md")"
printf '%s\n' "$fm" | grep -qE '^name:[[:space:]]*kimiflow'                 && ok "name: kimiflow"                       || bad "name missing/wrong"
printf '%s\n' "$fm" | grep -qE '^description:'                              && ok "description present"                  || bad "description missing"
printf '%s\n' "$fm" | grep -qE '^argument-hint:'                            && ok "argument-hint present"                || bad "argument-hint missing"
printf '%s\n' "$fm" | grep -q -- '--launcher|--menu'                         && ok "launcher argument hint present"       || bad "launcher argument hint missing"
printf '%s\n' "$fm" | grep -q -- '--project-map <quick|standard|deep|skip>'   && ok "project-map argument hint present"     || bad "project-map argument hint missing"
# Model-invocation is ENABLED (opt-in, on-request — the "invoke only when asked" policy lives in the
# description, not a hard flag). It must NOT be `true`, or the model can't launch kimiflow on request.
if printf '%s\n' "$fm" | grep -qE '^disable-model-invocation:[[:space:]]*true'; then bad "disable-model-invocation: true → model can't launch kimiflow on request"; else ok "model-invocable (disable-model-invocation not true) — opt-in/on-request per description"; fi
# user-invocable defaults true; it must NOT be false or /kimiflow vanishes from the slash menu.
if printf '%s\n' "$fm" | grep -qE '^user-invocable:[[:space:]]*false'; then bad "user-invocable: false → /kimiflow hidden from the slash menu"; else ok "user-invocable not disabled (slash-invocable)"; fi

echo "== project map bootstrap contract =="
grep -q 'Launcher / menu' "$ROOT/SKILL.md" && ok "canonical skill documents Launcher mode" || bad "missing Launcher mode in SKILL.md"
grep -q 'Launcher mode' "$ROOT/reference.md" && ok "reference documents Launcher mode" || bad "missing Launcher mode in reference.md"
grep -q 'Resume safety check' "$ROOT/reference.md" && ok "reference documents resume safety check" || bad "missing resume safety check in reference.md"
if [ -x "$ROOT/hooks/launcher-status.sh" ] && bash -n "$ROOT/hooks/launcher-status.sh" 2>/dev/null; then ok "launcher status helper ok"; else bad "launcher status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-launcher-status.sh" ] && bash -n "$ROOT/hooks/test-launcher-status.sh" 2>/dev/null; then ok "launcher status test ok"; else bad "launcher status test missing/not-exec/bad"; fi
grep -q 'Project Map Bootstrap' "$ROOT/SKILL.md" && ok "canonical skill documents Project Map Bootstrap" || bad "missing Project Map Bootstrap in SKILL.md"
grep -q -- '--project-map quick|standard|deep' "$ROOT/reference.md" && ok "reference documents project-map depths" || bad "missing project-map depths in reference.md"
for term in INDEX.json FACTS.jsonl CODEBASE.md ARCHITECTURE.md CONVENTIONS.md TESTING.md FLOWS.md OPEN-QUESTIONS.md; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map artifact documented: $term" || bad "project map artifact missing: $term"
done
if [ -x "$ROOT/hooks/project-map-status.sh" ] && bash -n "$ROOT/hooks/project-map-status.sh" 2>/dev/null; then ok "project map status helper ok"; else bad "project map status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-project-map-status.sh" ] && bash -n "$ROOT/hooks/test-project-map-status.sh" 2>/dev/null; then ok "project map status test ok"; else bad "project map status test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/current-state-gate.sh" ] && bash -n "$ROOT/hooks/current-state-gate.sh" 2>/dev/null; then ok "current-state gate helper ok"; else bad "current-state gate helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-current-state-gate.sh" ] && bash -n "$ROOT/hooks/test-current-state-gate.sh" 2>/dev/null; then ok "current-state gate test ok"; else bad "current-state gate test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/memory-router.sh" ] && bash -n "$ROOT/hooks/memory-router.sh" 2>/dev/null; then ok "memory router helper ok"; else bad "memory router helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-memory-router.sh" ] && bash -n "$ROOT/hooks/test-memory-router.sh" 2>/dev/null; then ok "memory router test ok"; else bad "memory router test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/vault-mcp-setup.sh" ] && bash -n "$ROOT/hooks/vault-mcp-setup.sh" 2>/dev/null; then ok "vault MCP setup helper ok"; else bad "vault MCP setup helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-vault-mcp-setup.sh" ] && bash -n "$ROOT/hooks/test-vault-mcp-setup.sh" 2>/dev/null; then ok "vault MCP setup test ok"; else bad "vault MCP setup test missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/vault-mcp-open-terminal.sh" ] && bash -n "$ROOT/hooks/vault-mcp-open-terminal.sh" 2>/dev/null; then ok "vault MCP terminal helper ok"; else bad "vault MCP terminal helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-vault-mcp-open-terminal.sh" ] && bash -n "$ROOT/hooks/test-vault-mcp-open-terminal.sh" 2>/dev/null; then ok "vault MCP terminal test ok"; else bad "vault MCP terminal test missing/not-exec/bad"; fi
grep -q 'project-map-status.sh' "$ROOT/reference.md" && ok "reference documents project-map status helper" || bad "missing project-map status helper in reference.md"
grep -q 'current-state-gate.sh' "$ROOT/reference.md" && ok "reference documents current-state gate helper" || bad "missing current-state gate helper in reference.md"
grep -q 'memory-router.sh' "$ROOT/reference.md" && ok "reference documents memory router helper" || bad "missing memory router helper in reference.md"
grep -q 'Current-State Gate' "$ROOT/SKILL.md" && ok "canonical skill documents Current-State Gate" || bad "missing Current-State Gate in SKILL.md"
grep -q 'Memory Router & Learning Loop' "$ROOT/SKILL.md" && ok "canonical skill documents Memory Router" || bad "missing Memory Router in SKILL.md"
grep -q 'potentially_stale' "$ROOT/reference.md" && ok "reference documents per-section staleness" || bad "missing per-section staleness in reference.md"
grep -q 'phase2_depth' "$ROOT/reference.md" && ok "reference documents adaptive map coverage depth" || bad "missing adaptive map coverage depth in reference.md"
for term in MEMORY.md USER.md LEARNINGS.jsonl USER.jsonl MEMORY-INDEX.json MEMORY-USAGE.json RECALL.sqlite RECALL.md RUN-HISTORY.json VAULT-PROVIDER.json VAULT-PREFETCH.md VAULT-SYNC.md SKILL-DRAFTS PENDING-PROPOSALS.md PROPOSALS.jsonl LEARNING-REVIEW.md review-run verify-run 'history --query' metrics 'provider status' 'provider health' 'provider setup' 'provider detect' 'provider sync' 'vault-mcp-setup.sh' 'vault-mcp-open-terminal.sh' '--interactive' bearer_token_env_var headersHelper 'index --write' 'consolidate --write' 'propose --write' '--approve' '--reject' '--apply' evidence_fingerprints 'Learning quality gate' 'Source freshness gate' provider_sync_pending provider_detected_unconfigured provider_auth_required provider_auth_failed connected_local_only authenticated auth_failed; do
  grep -q -- "$term" "$ROOT/reference.md" && ok "memory artifact documented: $term" || bad "memory artifact missing: $term"
done
for term in 'Storage targets' 'kimiflow+vault' 'repo-docs' 'IMPROVEMENTS.md' 'DOCS-PLAN.md'; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map publishing documented: $term" || bad "project map publishing missing: $term"
done
for term in 'Raw map vs. publishable docs' 'Repo-doc publish safety' 'never auto-commit `.kimiflow/project/`' 'concrete vulnerabilities' 'sanitized version'; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map publish safety documented: $term" || bad "project map publish safety missing: $term"
done

echo "== hooks wiring (referenced scripts exist, executable, valid) =="
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

echo "== MANUAL (needs a live Claude Code session — cannot be automated) =="
cat <<'MANUAL'
  [ ] /plugin marketplace add kimikonapps/kimiflow && /plugin install kimiflow@kimiflow → restart
  [ ] type "/kimiflow" → the command appears and fires (slash invocation works; cf. CC #26251)
  [ ] kimiflow launches when you ASK for it ("with kimiflow" / "run kimiflow") but does NOT fire unprompted on an unrelated request (opt-in policy is description-guided, not a hard flag; cf. CC #22345)
  [ ] in a repo with .kimiflow/, attempting `git add .` is blocked by the commit-secret-gate hook
  [ ] the Stop test-gate engages when .kimiflow/test-gate is present and tests are red
MANUAL

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "SMOKE OK (structural)"; exit 0; else echo "$FAILS SMOKE FAILURE(S)"; exit 1; fi
