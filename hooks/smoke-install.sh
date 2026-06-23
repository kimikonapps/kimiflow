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
printf '%s\n' "$fm" | grep -qE '^disable-model-invocation:[[:space:]]*true' && ok "disable-model-invocation: true (no auto-trigger)" || bad "disable-model-invocation not true"
# user-invocable defaults true; it must NOT be false or /kimiflow vanishes from the slash menu.
if printf '%s\n' "$fm" | grep -qE '^user-invocable:[[:space:]]*false'; then bad "user-invocable: false → /kimiflow hidden from the slash menu"; else ok "user-invocable not disabled (slash-invocable)"; fi

echo "== hooks wiring (referenced scripts exist, executable, valid) =="
while IFS= read -r cmd; do
  [ -n "$cmd" ] || continue
  rel="${cmd#\$\{CLAUDE_PLUGIN_ROOT\}/}"; p="$ROOT/$rel"
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
  [ ] /plugin marketplace add swinxx/kimiflow && /plugin install kimiflow@kimiflow → restart
  [ ] type "/kimiflow" → the command appears and fires (slash invocation works; cf. CC #26251)
  [ ] kimiflow does NOT auto-trigger on an unrelated request (disable-model-invocation honored; cf. CC #22345)
  [ ] in a repo with .kimiflow/, attempting `git add .` is blocked by the commit-secret-gate hook
  [ ] the Stop test-gate engages when .kimiflow/test-gate is present and tests are red
MANUAL

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "SMOKE OK (structural)"; exit 0; else echo "$FAILS SMOKE FAILURE(S)"; exit 1; fi
