#!/usr/bin/env bash
# kimiflow - vault MCP setup helper tests.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
FAILS=0

pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }

contains() {
  local text="$1" needle="$2" name="$3"
  if printf '%s\n' "$text" | grep -Fq "$needle"; then pass "$name"; else fail "$name"; fi
}

not_contains() {
  local text="$1" needle="$2" name="$3"
  if printf '%s\n' "$text" | grep -Fq "$needle"; then fail "$name"; else pass "$name"; fi
}

if grep -Fq '.tmp.$$' "$ROOT/hooks/vault-mcp-setup.sh"; then
  fail "setup_uses_unpredictable_temp_files"
else
  pass "setup_uses_unpredictable_temp_files"
fi

out="$(OBSIDIAN_API_KEY=fixture-token "$ROOT/hooks/vault-mcp-setup.sh" --host all --url https://127.0.0.1:27124 --data-dir "$WORK" 2>&1)"
contains "$out" 'bearer_token_env_var = "OBSIDIAN_API_KEY"' "codex_snippet_uses_env_token_reference"
contains "$out" '"headersHelper": "' "claude_snippet_uses_headers_helper"
contains "$out" 'https://127.0.0.1:27124/mcp/' "setup_prints_mcp_endpoint"
not_contains "$out" "fixture-token" "setup_does_not_echo_env_token"
if [ ! -e "$WORK/obsidian-mcp-headers.sh" ]; then pass "setup_does_not_write_helper_by_default"; else fail "setup_does_not_write_helper_by_default"; fi

out="$("$ROOT/hooks/vault-mcp-setup.sh" --host codex --url https://LOCALHOST:27124/mcp/ --data-dir "$WORK" 2>&1)"
contains "$out" 'url = "https://localhost:27124/mcp/"' "setup_normalizes_loopback_mcp_url"

mkdir -p "$WORK/codex"
config="$WORK/codex/config.toml"
cat > "$config" <<'EOF'
model = "gpt-5"

[mcp_servers.obsidian]
url = "http://old.example/mcp/"
bearer_token = "old-secret"

[mcp_servers.obsidian.headers]
Authorization = "Bearer nested-old-secret"

[profiles.default]
model = "gpt-5"
EOF
out="$(CODEX_HOME="$WORK/codex" "$ROOT/hooks/vault-mcp-setup.sh" --host codex --url https://127.0.0.1:27124 --data-dir "$WORK" --write-config 2>&1)"
contains "$out" "Updated Codex config: $config" "setup_writes_codex_config"
if [ "$(grep -c '^\[mcp_servers\.obsidian\]$' "$config" | tr -d '[:space:]')" = "1" ]; then pass "setup_keeps_one_obsidian_config_block"; else fail "setup_keeps_one_obsidian_config_block"; fi
contains "$(cat "$config")" 'url = "https://127.0.0.1:27124/mcp/"' "setup_codex_config_uses_loopback_mcp_url"
contains "$(cat "$config")" 'bearer_token_env_var = "OBSIDIAN_API_KEY"' "setup_codex_config_uses_env_reference"
contains "$(cat "$config")" '[profiles.default]' "setup_codex_config_preserves_other_sections"
if grep -Fq "old-secret" "$config"; then fail "setup_codex_config_removes_old_inline_secret"; else pass "setup_codex_config_removes_old_inline_secret"; fi
if grep -Fq "nested-old-secret" "$config"; then fail "setup_codex_config_removes_old_nested_secret"; else pass "setup_codex_config_removes_old_nested_secret"; fi

out="$(OBSIDIAN_API_KEY=fixture-token "$ROOT/hooks/vault-mcp-setup.sh" --host claude --url https://127.0.0.1:27124 --data-dir "$WORK" --write-helper 2>&1)"
helper="$WORK/obsidian-mcp-headers.sh"
[ -x "$helper" ] && pass "setup_writes_executable_helper" || fail "setup_writes_executable_helper"
bash -n "$helper" >/dev/null 2>&1 && pass "headers_helper_syntax_ok" || fail "headers_helper_syntax_ok"
if grep -Fq "fixture-token" "$helper"; then fail "headers_helper_does_not_store_token"; else pass "headers_helper_does_not_store_token"; fi
not_contains "$out" "fixture-token" "write_helper_does_not_echo_env_token"

helper_out="$(OBSIDIAN_API_KEY=header-fixture-token "$helper" 2>/dev/null || true)"
if printf '%s\n' "$helper_out" | jq -e '.Authorization == "Bearer header-fixture-token"' >/dev/null 2>&1; then
  pass "headers_helper_emits_authorization_json"
else
  fail "headers_helper_emits_authorization_json"
fi

bad_token="$(printf 'bad\nthing')"
if OBSIDIAN_API_KEY="$bad_token" "$helper" >/dev/null 2>&1; then
  fail "headers_helper_rejects_multiline_token"
else
  pass "headers_helper_rejects_multiline_token"
fi

mkdir -p "$WORK/bin"
cat > "$WORK/bin/curl" <<'EOF'
#!/usr/bin/env bash
url=""
config_stdin=0
prev=""
for arg in "$@"; do
  case "$arg" in
    http://*|https://*) url="${arg%/}" ;;
  esac
  if [ "$prev" = "--config" ] && [ "$arg" = "-" ]; then
    config_stdin=1
  fi
  prev="$arg"
done
if [ "$config_stdin" -eq 1 ]; then
  config="$(cat)"
  case "$url" in
    https://127.0.0.1:27124/vault)
      case "$config" in
        *"Authorization: Bearer fixture-token"*) printf '204'; exit 0 ;;
      esac
      printf '401'
      exit 0
      ;;
  esac
fi
exit 7
EOF
chmod +x "$WORK/bin/curl"
out="$(OBSIDIAN_API_KEY=fixture-token PATH="$WORK/bin:$PATH" "$ROOT/hooks/vault-mcp-setup.sh" --host codex --url https://127.0.0.1:27124 --data-dir "$WORK" --verify 2>&1)"
contains "$out" "Verified Obsidian Local REST API auth: HTTP 204" "setup_verify_checks_local_rest_api"
not_contains "$out" "fixture-token" "setup_verify_does_not_echo_token"

if "$ROOT/hooks/vault-mcp-setup.sh" --host codex --url https://evil.example --data-dir "$WORK" >/dev/null 2>&1; then
  fail "setup_rejects_non_loopback_url"
else
  pass "setup_rejects_non_loopback_url"
fi

bad_url="$(printf 'https://127.0.0.1:27124/evil"\n[mcp_servers.injected]\nurl = "http://example.invalid"')"
if "$ROOT/hooks/vault-mcp-setup.sh" --host codex --url "$bad_url" --data-dir "$WORK" >/dev/null 2>&1; then
  fail "setup_rejects_snippet_injection_url"
else
  pass "setup_rejects_snippet_injection_url"
fi

rm -rf "$WORK"

if [ "$FAILS" -eq 0 ]; then
  echo "ALL GREEN"
  exit 0
fi
echo "$FAILS failure(s)"
exit 1
