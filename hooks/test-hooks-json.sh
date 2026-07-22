#!/usr/bin/env bash
# test-hooks-json.sh — regression tests for the hook manifests (hooks/hooks.json + root hooks.json).
#
# Guards the fail-open found in audit: an UNQUOTED ${KIMIFLOW_PLUGIN_ROOT:-…} expansion
# word-splits on a plugin root containing a space (e.g. ".../VIBE CODING/kimiflow"), every
# hook exits 126/127 instead of running, and since the exit code is not 2 the fail-closed
# PreToolUse gates (commit-secret-gate, state-gate) silently stop blocking.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fails=0

fail() { printf 'FAIL: %s\n' "$1"; fails=$((fails + 1)); }
ok() { printf 'ok: %s\n' "$1"; }

command -v jq >/dev/null 2>&1 || { echo "test-hooks-json: jq required" >&2; exit 2; }

# --- 1. Both manifests are valid JSON ------------------------------------------------
for f in "$ROOT/hooks/hooks.json" "$ROOT/hooks.json"; do
  if jq empty "$f" >/dev/null 2>&1; then ok "valid JSON: ${f#"$ROOT"/}"; else fail "invalid JSON: $f"; fi
done

# --- 2. Every command quotes the plugin-root expansion --------------------------------
# The expansion must appear as "…"${KIMIFLOW_PLUGIN_ROOT…}"…" (quoted), never bare.
for f in "$ROOT/hooks/hooks.json" "$ROOT/hooks.json"; do
  bad=0
  while IFS= read -r cmd; do
    case "$cmd" in
      *'"${KIMIFLOW_PLUGIN_ROOT'*) : ;;                   # quoted — good
      *'${KIMIFLOW_PLUGIN_ROOT'*) bad=1 ;;                 # bare — word-splits on spaces
    esac
  done < <(jq -r '.. | objects | select(.type? == "command") | .command' "$f")
  if [ "$bad" -eq 0 ]; then ok "plugin-root quoted: ${f#"$ROOT"/}"; else fail "unquoted plugin-root expansion in $f"; fi
done

# --- 3. End-to-end: every command runs from a SPACED plugin root ----------------------
# Symlink a spaced dir at the repo and execute each command exactly as the host would.
tmp="$(mktemp -d "${TMPDIR:-/tmp}/kimiflow-spaced.XXXXXX")" || { echo "mktemp failed" >&2; exit 2; }
trap 'rm -rf "$tmp"' EXIT
spaced="$tmp/spaced root"
mkdir -p "$spaced"
ln -s "$ROOT/hooks" "$spaced/hooks"

for f in "$ROOT/hooks/hooks.json" "$ROOT/hooks.json"; do
  while IFS= read -r cmd; do
    # Run with empty stdin from a neutral cwd; hooks must exit 0 (no gate context) — never 126/127.
    ( cd "$tmp" && KIMIFLOW_PLUGIN_ROOT="$spaced" CLAUDE_PLUGIN_ROOT="$spaced" bash -c "$cmd" </dev/null >/dev/null 2>&1 )
    rc=$?
    case "$rc" in
      126|127) fail "word-split/exec failure (rc=$rc) from spaced root: $cmd" ;;
      0|2) ok "runs from spaced root (rc=$rc): ${cmd%% *}…" ;;
      *) ok "runs from spaced root (rc=$rc, tolerated): ${cmd%% *}…" ;;
    esac
  done < <(jq -r '.. | objects | select(.type? == "command") | .command' "$f")
done

# --- 4. Product Intake guard is wired before writes and explicit response capture -----
if jq -e '.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[0].command | contains("intake-gate.sh")' "$ROOT/hooks/hooks.json" >/dev/null \
  && jq -e '.hooks.PreToolUse[] | select(.matcher == "Bash") | .hooks[0].command | contains("intake-gate.sh")' "$ROOT/hooks.json" >/dev/null; then
  ok "intake gate is first on Bash for both hosts"
else
  fail "intake gate is not first on Bash for both hosts"
fi
if jq -e '.hooks.PostToolUse[] | select(.matcher == "AskUserQuestion") | .hooks[].command | contains("intake-response")' "$ROOT/hooks/hooks.json" >/dev/null \
  && jq -e '.hooks.PostToolUse[] | select(.matcher == "request_user_input") | .hooks[].command | contains("intake-response")' "$ROOT/hooks.json" >/dev/null; then
  ok "explicit native intake response adapters are wired"
else
  fail "native intake response adapter wiring missing"
fi

echo
if [ "$fails" -gt 0 ]; then echo "test-hooks-json: $fails failure(s)"; exit 1; fi
echo "test-hooks-json: all green"
