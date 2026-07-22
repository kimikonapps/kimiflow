#!/usr/bin/env bash
# kimiflow - safe Obsidian MCP setup helper. User-run, not a hook.
#
# Usage:
#   vault-mcp-setup.sh [--host codex|claude|all] [--url <loopback-url>] [--data-dir <dir>] [--write-helper] [--write-config] [--store-keychain] [--set-launch-env] [--verify] [--interactive]
set -u

usage() {
  sed -n '1,7p' "$0" >&2
}

die() {
  printf 'vault-mcp-setup: %s\n' "$1" >&2
  exit "${2:-1}"
}

need_jq() {
  command -v jq >/dev/null 2>&1 || die "jq is required to render safe JSON snippets" 2
}

normalize_loopback_origin() {
  local url="$1" scheme rest host_port path host port suffix host_lc
  url="${url%/}"
  case "$url" in
    *[[:space:]]*|*\"*|*\'*|*\\*|*\`*) return 1 ;;
  esac
  case "$url" in
    http://*) scheme="http"; rest="${url#http://}" ;;
    https://*) scheme="https"; rest="${url#https://}" ;;
    *) return 1 ;;
  esac
  host_port="${rest%%/*}"
  path=""
  if [ "$rest" != "$host_port" ]; then
    path="/${rest#*/}"
  fi
  case "$path" in
    ""|"/"|"/mcp"|"/mcp/") ;;
    *) return 1 ;;
  esac

  [ -n "$host_port" ] || return 1
  case "$host_port" in
    *@*) return 1 ;;
  esac
  case "$host_port" in
    \[*\]*)
      host="${host_port#\[}"
      host="${host%%\]*}"
      suffix="${host_port#*\]}"
      case "$suffix" in
        "") port="" ;;
        :*) port="${suffix#:}" ;;
        *) return 1 ;;
      esac
      ;;
    *)
      host="${host_port%%:*}"
      port=""
      if [ "$host_port" != "$host" ]; then
        port="${host_port#*:}"
        case "$port" in
          *:*) return 1 ;;
        esac
      fi
      ;;
  esac
  host_lc="$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"
  case "$port" in
    ""|*[!0-9]*) [ -z "$port" ] || return 1 ;;
  esac
  case "$host_lc" in
    localhost|127.0.0.1)
      if [ -n "$port" ]; then printf '%s://%s:%s\n' "$scheme" "$host_lc" "$port"; else printf '%s://%s\n' "$scheme" "$host_lc"; fi
      ;;
    ::1)
      if [ -n "$port" ]; then printf '%s://[::1]:%s\n' "$scheme" "$port"; else printf '%s://[::1]\n' "$scheme"; fi
      ;;
    *) return 1 ;;
  esac
}

json_string() {
  need_jq
  jq -Rn --arg value "$1" '$value'
}

host_includes() {
  local wanted="$1"
  [ "$host" = "all" ] || [ "$host" = "$wanted" ]
}

write_headers_helper() {
  local helper="$1" tmp
  mkdir -p "$(dirname "$helper")" || die "cannot create helper directory: $(dirname "$helper")"
  umask 077
  tmp="$(mktemp "${helper}.tmp.XXXXXX")" || die "cannot create temporary helper: $helper"
  if ! cat > "$tmp" <<'HELPER'
#!/usr/bin/env bash
set -u

service="${KIMIFLOW_OBSIDIAN_KEYCHAIN_SERVICE:-kimiflow.obsidian.api-key}"
token="${OBSIDIAN_API_KEY:-${KIMIFLOW_OBSIDIAN_API_KEY:-}}"

if [ -z "$token" ] && command -v security >/dev/null 2>&1; then
  token="$(security find-generic-password -a "${USER:-${LOGNAME:-unknown}}" -s "$service" -w 2>/dev/null || true)"
fi

case "$token" in
  *[[:cntrl:]]*)
    printf 'obsidian-mcp-headers: refusing token with control characters\n' >&2
    exit 2
    ;;
esac

[ -n "$token" ] || {
  printf 'obsidian-mcp-headers: missing OBSIDIAN_API_KEY or Keychain item kimiflow.obsidian.api-key\n' >&2
  exit 1
}

if command -v jq >/dev/null 2>&1; then
  jq -n --arg token "$token" '{"Authorization": ("Bearer " + $token)}'
else
  escaped="$(printf '%s' "$token" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  printf '{"Authorization":"Bearer %s"}\n' "$escaped"
fi
HELPER
  then
    rm -f "$tmp"
    die "cannot write temporary helper: $helper"
  fi
  mv "$tmp" "$helper" || {
    rm -f "$tmp"
    die "cannot write helper: $helper"
  }
  chmod 700 "$helper" || die "cannot chmod helper: $helper"
}

read_token() {
  local token=""
  token="${OBSIDIAN_API_KEY:-${KIMIFLOW_OBSIDIAN_API_KEY:-}}"
  if [ -z "$token" ] && command -v security >/dev/null 2>&1; then
    token="$(security find-generic-password -a "${USER:-${LOGNAME:-unknown}}" -s "$service" -w 2>/dev/null || true)"
  fi
  case "$token" in
    *[[:cntrl:]]*) return 2 ;;
  esac
  [ -n "$token" ] || return 1
  printf '%s' "$token"
}

store_keychain_token() {
  local service="$1" token
  command -v security >/dev/null 2>&1 || die "macOS security command is required for --store-keychain" 2
  [ -t 0 ] || die "--store-keychain must be run in your terminal, not through chat/agent automation" 2
  printf 'Paste Obsidian Local REST API key (input hidden, not echoed): ' > /dev/tty
  IFS= read -r -s token < /dev/tty
  printf '\n' > /dev/tty
  [ -n "$token" ] || die "empty API key refused" 2
  case "$token" in
    *[[:cntrl:]]*) die "API key contains control characters" 2 ;;
  esac
  security add-generic-password -U -a "${USER:-${LOGNAME:-unknown}}" -s "$service" -w "$token" >/dev/null
  token=""
  printf 'Stored API key in macOS Keychain service: %s\n' "$service"
}

codex_config_path() {
  if [ -n "${CODEX_HOME:-}" ]; then
    printf '%s/config.toml\n' "${CODEX_HOME%/}"
  else
    [ -n "${HOME:-}" ] || die "HOME is required when CODEX_HOME is not set" 2
    printf '%s/.codex/config.toml\n' "$HOME"
  fi
}

write_codex_config() {
  local mcp_url="$1" config dir tmp had_content=0
  config="$(codex_config_path)"
  dir="$(dirname "$config")"
  mkdir -p "$dir" || die "cannot create Codex config directory: $dir"
  [ -f "$config" ] && had_content=1
  tmp="$(mktemp "${config}.tmp.XXXXXX")" || die "cannot create temporary Codex config: $config"
  if [ -f "$config" ]; then
    awk '
      /^\[\[?mcp_servers\.obsidian(\]|\.)/ { skip = 1; next }
      /^\[\[?[^]]+\]\]?[[:space:]]*$/ { skip = 0 }
      !skip { print }
    ' "$config" > "$tmp" || {
      rm -f "$tmp"
      die "cannot rewrite Codex config: $config"
    }
  else
    : > "$tmp" || {
      rm -f "$tmp"
      die "cannot create Codex config: $config"
    }
  fi
  if [ "$had_content" -eq 1 ] && [ -s "$tmp" ]; then
    printf '\n' >> "$tmp" || {
      rm -f "$tmp"
      die "cannot append Codex config spacing: $config"
    }
  fi
  {
    printf '[mcp_servers.obsidian]\n'
    printf 'url = "%s"\n' "$mcp_url"
    printf 'bearer_token_env_var = "OBSIDIAN_API_KEY"\n'
    printf 'default_tools_approval_mode = "prompt"\n'
  } >> "$tmp" || {
    rm -f "$tmp"
    die "cannot append Codex config: $config"
  }
  mv "$tmp" "$config" || {
    rm -f "$tmp"
    die "cannot update Codex config: $config"
  }
  printf 'Updated Codex config: %s\n' "$config"
}

write_claude_config() {
  local mcp_url="$1" helper="$2" json
  if ! command -v claude >/dev/null 2>&1; then
    printf 'Claude Code CLI (claude) not found on PATH; add the MCP server JSON shown below manually.\n' >&2
    return 1
  fi
  # headersHelper points Claude at the dynamic header script; make sure it exists.
  [ -x "$helper" ] || write_headers_helper "$helper"
  json="$(printf '{"type":"http","url":%s,"headersHelper":%s}' "$(json_string "$mcp_url")" "$(json_string "$helper")")"
  # Replace any existing obsidian server (e.g. an older stdio mcp-obsidian with an inline key)
  # so a stale plaintext key cannot linger in ~/.claude.json.
  claude mcp remove obsidian -s user >/dev/null 2>&1 || true
  if claude mcp add-json obsidian "$json" -s user >/dev/null 2>&1; then
    printf 'Updated Claude Code MCP server: obsidian (user scope, no secret in config)\n'
  else
    printf 'claude mcp add-json failed; add the MCP server JSON shown below manually.\n' >&2
    return 1
  fi
}

set_launch_env() {
  local token
  command -v launchctl >/dev/null 2>&1 || die "launchctl is required for --set-launch-env" 2
  token="$(read_token)" || die "missing valid token for --set-launch-env; run --store-keychain first or set OBSIDIAN_API_KEY" 2
  launchctl setenv OBSIDIAN_API_KEY "$token" >/dev/null || die "launchctl setenv failed" 1
  token=""
  printf 'Set OBSIDIAN_API_KEY for newly launched macOS GUI apps in this login session.\n'
}

verify_local_rest_api() {
  local token code escaped_token
  command -v curl >/dev/null 2>&1 || die "curl is required for --verify" 2
  token="$(read_token)" || die "missing valid token for --verify; run --store-keychain first or set OBSIDIAN_API_KEY" 2
  escaped_token="$(printf '%s' "$token" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  token=""
  code="$(printf 'header = "Authorization: Bearer %s"\n' "$escaped_token" \
    | curl -k -sS -o /dev/null -w '%{http_code}' --connect-timeout 2 -m 5 --config - "$url/vault/" 2>/dev/null || printf '000')"
  escaped_token=""
  case "$code" in
    2*) printf 'Verified Obsidian Local REST API auth: HTTP %s\n' "$code" ;;
    401|403) die "Obsidian Local REST API rejected the API key: HTTP $code" 1 ;;
    *) die "Obsidian Local REST API verification failed: HTTP $code" 1 ;;
  esac
}

mcp_initialize_advertises_tools() {
  need_jq
  awk '
    {
      line = $0
      sub(/\r$/, "", line)
      if (line ~ /^data:[[:space:]]*/) {
        sub(/^data:[[:space:]]*/, "", line)
        if (line != "" && line != "[DONE]") print line
        next
      }
      if (line ~ /^[[:space:]]*[\{\[]/) print line
    }
  ' | jq -e -s --arg protocol "$mcp_protocol_version" 'any(.[]; (.error? == null) and (.jsonrpc == "2.0") and (.id == 1) and (.result.protocolVersion == $protocol) and (.result.capabilities.tools? != null))' >/dev/null 2>&1
}

print_certificate_trust_guide() {
  local cert_url="$url/obsidian-local-rest-api.crt"
  cat <<EOF

Obsidian HTTPS certificate trust is required for MCP clients.

Recommended macOS fix:
1. Keep Obsidian running with the Local REST API plugin enabled.
2. Open/download this local certificate:
   $cert_url
3. Double-click the .crt file and add it to the System keychain.
4. Open the certificate in Keychain Access, expand Trust, set SSL to Always Trust, then close and confirm.
5. Restart Obsidian and restart/reload Codex or Claude Code.
6. Re-run this setup wizard.

Local-only fallback if you deliberately do not want to trust the certificate:
   hooks/vault-mcp-open-terminal.sh --host $host --url http://127.0.0.1:27123
   hooks/vault-mcp-setup.sh --host $host --url http://127.0.0.1:27123 --interactive

EOF
}

verify_mcp_endpoint() {
  local token escaped_token body err_file status payload mcp_url
  command -v curl >/dev/null 2>&1 || die "curl is required for --verify" 2
  need_jq
  token="$(read_token)" || die "missing valid token for --verify; run --store-keychain first or set OBSIDIAN_API_KEY" 2
  escaped_token="$(printf '%s' "$token" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  token=""
  err_file="$(mktemp "${TMPDIR:-/tmp}/kimiflow-obsidian-mcp-err.XXXXXX")" || die "cannot create temporary MCP verification file" 1
  payload="$(printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"%s","capabilities":{},"clientInfo":{"name":"kimiflow-vault-setup","version":"1"}}}' "$mcp_protocol_version")"
  mcp_url="$url/mcp/"
  body="$(printf 'header = "Authorization: Bearer %s"\n' "$escaped_token" \
    | curl -sS --connect-timeout 2 -m 8 --config - \
        -H "Accept: application/json, text/event-stream" \
        -H "Content-Type: application/json" \
        -H "MCP-Protocol-Version: $mcp_protocol_version" \
        --data "$payload" "$mcp_url" 2>"$err_file")"
  status=$?
  escaped_token=""
  if [ "$status" -ne 0 ]; then
    case "$url" in
      https://*)
        if grep -Eiq 'self signed|SSL certificate|certificate.*(verify|problem|invalid)|x509' "$err_file"; then
          print_certificate_trust_guide >&2
          rm -f "$err_file"
          die "Obsidian MCP TLS verification failed; trust the local certificate or use the local HTTP fallback." 1
        fi
        ;;
    esac
    printf 'Obsidian MCP verification failed:\n' >&2
    sed -n '1,6p' "$err_file" >&2
    rm -f "$err_file"
    die "Obsidian MCP endpoint did not respond at $mcp_url" 1
  fi
  rm -f "$err_file"
  if printf '%s\n' "$body" | mcp_initialize_advertises_tools; then
    printf 'Verified Obsidian MCP endpoint: %s\n' "$mcp_url"
  else
    die "Obsidian MCP endpoint responded, but did not advertise MCP tools; keep Obsidian running and verify the Local REST API plugin." 1
  fi
}

print_codex() {
  local mcp_url="$1"
  cat <<EOF
Codex setup (user-level ~/.codex/config.toml):

[mcp_servers.obsidian]
url = "$mcp_url"
bearer_token_env_var = "OBSIDIAN_API_KEY"
default_tools_approval_mode = "prompt"

Set OBSIDIAN_API_KEY outside the repo before starting Codex. Do not paste the key into chat or commit it.
If Codex cannot connect to the HTTPS endpoint, trust the Obsidian Local REST API certificate in macOS Keychain
or rerun the wizard with --url http://127.0.0.1:27123 as a local-only fallback.
EOF
}

print_claude() {
  local mcp_url="$1" helper="$2" mcp_url_json helper_json
  mcp_url_json="$(json_string "$mcp_url")"
  helper_json="$(json_string "$helper")"
  cat <<EOF
Claude Code setup (user scope recommended):

1. Create/update the dynamic header helper:
   hooks/vault-mcp-setup.sh --host claude --write-helper

2. Add this MCP server JSON through Claude Code:

{
  "mcpServers": {
    "obsidian": {
      "type": "http",
      "url": $mcp_url_json,
      "headersHelper": $helper_json
    }
  }
}

The helper reads OBSIDIAN_API_KEY or macOS Keychain service kimiflow.obsidian.api-key at connection time.
EOF
}

host="all"
url="${KIMIFLOW_OBSIDIAN_URL:-https://127.0.0.1:27124}"
if [ -n "${KIMIFLOW_DATA_DIR:-}" ]; then
  data_dir="$KIMIFLOW_DATA_DIR"
elif [ -n "${HOME:-}" ]; then
  data_dir="$HOME/.kimiflow"
else
  data_dir=""
fi
write_helper=0
write_config=0
store_keychain=0
set_launch_env=0
verify=0
interactive=0
service="${KIMIFLOW_OBSIDIAN_KEYCHAIN_SERVICE:-kimiflow.obsidian.api-key}"
mcp_protocol_version="${KIMIFLOW_MCP_PROTOCOL_VERSION:-2025-11-25}"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host) shift; host="${1:-all}" ;;
    --url) shift; url="${1:-}" ;;
    --data-dir) shift; data_dir="${1:-}" ;;
    --write-helper) write_helper=1 ;;
    --write-config) write_config=1 ;;
    --store-keychain) store_keychain=1 ;;
    --set-launch-env) set_launch_env=1 ;;
    --verify) verify=1 ;;
    --interactive)
      interactive=1
      write_helper=1
      write_config=1
      store_keychain=1
      set_launch_env=1
      verify=1
      ;;
    --service) shift; service="${1:-}" ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" 2 ;;
  esac
  shift
done

case "$host" in
  codex|claude|all) ;;
  *) die "--host must be codex, claude, or all" 2 ;;
esac

[ -n "$url" ] || die "--url cannot be empty" 2
raw_url="$url"
url="$(normalize_loopback_origin "$raw_url")" || die "refusing non-loopback or non-origin Obsidian URL: $raw_url" 2
case "$mcp_protocol_version" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) die "KIMIFLOW_MCP_PROTOCOL_VERSION must use YYYY-MM-DD" 2 ;;
esac

[ -n "$data_dir" ] || die "--data-dir cannot be empty" 2
case "$data_dir" in
  *[[:cntrl:]]*) die "--data-dir contains control characters" 2 ;;
esac
helper="$data_dir/obsidian-mcp-headers.sh"
mcp_url="$url/mcp/"

if [ "$interactive" -eq 1 ]; then
  cat <<EOF
Kimiflow Obsidian Vault MCP setup

This terminal wizard can store the Obsidian API key in macOS Keychain, write host config, and verify local auth.
The key is read in this terminal only. It is not printed, committed, or written to .kimiflow/.

EOF
fi

if [ "$store_keychain" -eq 1 ]; then
  store_keychain_token "$service"
fi

if [ "$interactive" -eq 1 ] && [ "$verify" -eq 1 ]; then
  verify_local_rest_api
  verify_mcp_endpoint
  printf '\n'
  verify=0
fi

if [ "$write_helper" -eq 1 ] && host_includes claude; then
  write_headers_helper "$helper"
  printf 'Wrote Claude headers helper: %s\n\n' "$helper"
fi

if [ "$write_config" -eq 1 ] && host_includes codex; then
  write_codex_config "$mcp_url"
  printf '\n'
fi

if [ "$write_config" -eq 1 ] && host_includes claude; then
  write_claude_config "$mcp_url" "$helper"
  printf '\n'
fi

if [ "$set_launch_env" -eq 1 ] && host_includes codex; then
  set_launch_env
  printf '\n'
fi

if [ "$verify" -eq 1 ]; then
  verify_local_rest_api
  verify_mcp_endpoint
  printf '\n'
fi

printf 'Obsidian MCP endpoint: %s\n\n' "$mcp_url"
case "$host" in
  codex)
    print_codex "$mcp_url"
    ;;
  claude)
    print_claude "$mcp_url" "$helper"
    ;;
  all)
    print_codex "$mcp_url"
    printf '\n'
    print_claude "$mcp_url" "$helper"
    ;;
esac

if [ "$interactive" -eq 1 ]; then
  cat <<'EOF'

Next step: restart/reload Codex or Claude Code so the MCP client opens a fresh connection.
After restart, run Kimiflow's provider health check again.
EOF
fi
