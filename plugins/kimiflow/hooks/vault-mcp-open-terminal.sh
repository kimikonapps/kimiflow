#!/usr/bin/env bash
# kimiflow - open a user-owned Terminal wizard for Obsidian MCP setup.
#
# Usage:
#   vault-mcp-open-terminal.sh [--host codex|claude|all] [--url <loopback-url>] [--dry-run]
set -u

usage() {
  sed -n '1,6p' "$0" >&2
}

die() {
  printf 'vault-mcp-open-terminal: %s\n' "$1" >&2
  exit "${2:-1}"
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

shell_quote() {
  printf "'"
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
  printf "'"
}

applescript_string() {
  printf '"'
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
  printf '"'
}

case "${KIMIFLOW_HOST:-}" in
  codex|claude) host="$KIMIFLOW_HOST" ;;
  *) host="codex" ;;
esac
url="${KIMIFLOW_OBSIDIAN_URL:-https://127.0.0.1:27124}"
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host) shift; host="${1:-}" ;;
    --url) shift; url="${1:-}" ;;
    --dry-run) dry_run=1 ;;
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

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
setup_script="$script_dir/vault-mcp-setup.sh"
[ -x "$setup_script" ] || die "setup helper is missing or not executable: $setup_script" 1

cmd="cd $(shell_quote "$repo_root") && $(shell_quote "$setup_script") --host $(shell_quote "$host") --url $(shell_quote "$url") --interactive; status=\$?; printf '\\nKimiflow Vault setup finished with exit code %s.\\n' \"\$status\"; printf 'Press return to close this window... '; IFS= read -r _; exit \"\$status\""

if [ "$dry_run" -eq 1 ]; then
  printf '%s\n' "$cmd"
  exit 0
fi

command -v osascript >/dev/null 2>&1 || die "osascript is required to open Terminal.app automatically" 2
osascript \
  -e 'tell application "Terminal"' \
  -e "do script $(applescript_string "$cmd")" \
  -e 'activate' \
  -e 'end tell' >/dev/null || die "failed to open Terminal.app" 1

printf 'Opened Terminal.app for Kimiflow Vault setup.\n'
