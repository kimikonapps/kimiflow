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
jq -e '.name == "kimiflow" and (.plugins[] | select(.name == "kimiflow" and .source.path == "./"))' "$ROOT/.agents/plugins/marketplace.json" >/dev/null 2>&1 \
  && ok "codex marketplace entry" || bad "codex marketplace entry"
jq -e '[.hooks[]?[]?.hooks[]? | select(.type == "command")] | length == 3 and all(.[]; (.name // "" | length > 0) and (.description // "" | length > 0) and (.statusMessage // "" | length > 0))' "$ROOT/hooks.json" >/dev/null 2>&1 \
  && ok "codex plugin hooks are labelled" || bad "codex plugin hook labels missing"

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
grep -q -- '--project-map <quick|standard|deep|skip>' "$SKILL" && ok "Codex wrapper maps project-map invocation" || bad "Codex wrapper missing project-map invocation mapping"
grep -q 'Project Map Bootstrap' "$ROOT/SKILL.md" && ok "canonical Project Map Bootstrap present" || bad "canonical Project Map Bootstrap missing"
grep -q 'FACTS.jsonl' "$ROOT/reference.md" && ok "project map evidence artifact documented" || bad "project map evidence artifact missing"
if [ -x "$ROOT/hooks/project-map-status.sh" ] && bash -n "$ROOT/hooks/project-map-status.sh" 2>/dev/null; then ok "project map status helper ok"; else bad "project map status helper missing/not-exec/bad"; fi
if [ -x "$ROOT/hooks/test-project-map-status.sh" ] && bash -n "$ROOT/hooks/test-project-map-status.sh" 2>/dev/null; then ok "project map status test ok"; else bad "project map status test missing/not-exec/bad"; fi
grep -q 'project-map-status.sh' "$ROOT/reference.md" && ok "canonical project-map status helper documented" || bad "canonical project-map status helper missing"
grep -q 'potentially_stale' "$ROOT/reference.md" && ok "per-section staleness documented" || bad "per-section staleness missing"
for term in 'Storage targets' 'kimiflow+vault' 'repo-docs' 'IMPROVEMENTS.md' 'DOCS-PLAN.md'; do
  grep -q "$term" "$ROOT/reference.md" && ok "project map publishing documented: $term" || bad "project map publishing missing: $term"
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
for f in kimiflow-commit-secret-gate.sh kimiflow-state-gate.sh kimiflow-test-gate.sh; do
  wp="$tmp_home/codex/hooks/$f"
  if [ -x "$wp" ] && bash -n "$wp" 2>/dev/null && grep -q "KIMIFLOW_PLUGIN_ROOT=" "$wp"; then ok "wrapper ok: $f"; else bad "wrapper missing/bad: $f"; fi
done

echo "== codex gate fires (synthetic payloads) =="
COMMIT_HOOK="$tmp_home/codex/hooks/kimiflow-commit-secret-gate.sh"
STATE_HOOK="$tmp_home/codex/hooks/kimiflow-state-gate.sh"
TEST_HOOK="$tmp_home/codex/hooks/kimiflow-test-gate.sh"

deny_commit() { jq -nc --arg c "$1" --arg d "$2" '{tool_input:{args:{command:$c}}, cwd:$d, hook_event_name:"PreToolUse"}' | bash "$COMMIT_HOOK" 2>/dev/null | grep -q '"permissionDecision":"deny"'; }
deny_state()  { jq -nc --arg c "$1" --arg d "$2" '{tool_input:{args:{command:$c}}, cwd:$d, hook_event_name:"PreToolUse"}' | bash "$STATE_HOOK" 2>/dev/null | grep -q '"permissionDecision":"deny"'; }
block_stop()  { jq -nc --arg d "$1" '{cwd:$d, hook_input:{stop_hook_active:false}, hook_event_name:"Stop"}' | bash "$TEST_HOOK" 2>/dev/null | grep -qE '"decision"[[:space:]]*:[[:space:]]*"block"'; }
allow_stop_active() { out="$(jq -nc --arg d "$1" '{cwd:$d, hook_input:{stop_hook_active:true}, hook_event_name:"Stop"}' | bash "$TEST_HOOK" 2>/dev/null)"; [ -z "$out" ]; }

tmp1="$(mktemp -d)"; ( cd "$tmp1" && git init -q && mkdir .kimiflow )
tmp2="$(mktemp -d)"; ( cd "$tmp2" && git init -q )
if deny_commit 'git add .' "$tmp1"; then ok "commit-secret-gate blocks git add . in Codex payload"; else bad "commit-secret-gate did not block git add . in Codex payload"; fi
if deny_commit 'git add .' "$tmp2"; then bad "commit-secret-gate wrongly blocked outside Kimiflow repo"; else ok "commit-secret-gate allows outside Kimiflow repo"; fi
mkdir -p "$tmp1/.kimiflow/nostate/findings"
if deny_state './hooks/resolve-review-gate.sh .kimiflow/nostate/findings --round 1 --expect A,B' "$tmp1"; then ok "state-gate blocks missing STATE in Codex payload"; else bad "state-gate did not block missing STATE in Codex payload"; fi
printf 'false\n' > "$tmp1/.kimiflow/test-gate"
if block_stop "$tmp1"; then ok "test-gate blocks red tests in Codex payload"; else bad "test-gate did not block red tests in Codex payload"; fi
if allow_stop_active "$tmp1"; then ok "test-gate allows active stop continuation"; else bad "test-gate did not allow active stop continuation"; fi
rm -rf "$tmp1" "$tmp2" "$tmp_home"

echo "== MANUAL (needs Codex app/CLI plugin browser) =="
cat <<'MANUAL'
  [ ] Add the Git marketplace (`codex plugin marketplace add swinxx/kimiflow`), then install kimiflow.
  [ ] Run the stable hook installer from that marketplace checkout once.
  [ ] Start a new Codex thread and invoke "$kimiflow <tiny change>".
  [ ] Confirm Kimiflow launches only when explicitly requested.
  [ ] In a repo with .kimiflow/, attempting `git add .` is blocked by the installed stable Codex hook.
  [ ] With .kimiflow/test-gate containing a failing command, Codex Stop is blocked.
MANUAL

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "CODEX SMOKE OK (structural)"; exit 0; else echo "$FAILS CODEX SMOKE FAILURE(S)"; exit 1; fi
