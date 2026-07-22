#!/usr/bin/env bash
# kimiflow — validate the bundled Codex hook contract.
#
# Current Codex loads plugin hooks from the path declared in
# .codex-plugin/plugin.json. Older Kimiflow releases wrote unregistered wrapper
# files into ~/.codex/hooks; that directory alone is not a hook configuration
# and therefore must not be presented as enforcement.
set -eu

usage() {
  cat <<'EOF'
Usage: hooks/install-codex-hooks.sh [--check]

Validates the bundled Codex plugin hook contract. No user-level wrapper files
are installed; Codex loads ./hooks/hooks.json through the plugin manifest.
EOF
}

case "${1:-}" in
  ""|--check) ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

command -v jq >/dev/null 2>&1 || { printf 'install-codex-hooks: jq is required\n' >&2; exit 2; }

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)"
MANIFEST="$ROOT/.codex-plugin/plugin.json"

[ -f "$MANIFEST" ] || { printf 'install-codex-hooks: missing Codex plugin manifest\n' >&2; exit 1; }
hook_rel="$(jq -er '.hooks | select(type == "string")' "$MANIFEST")" \
  || { printf 'install-codex-hooks: manifest hook path is missing or invalid\n' >&2; exit 1; }
[ "$hook_rel" = "./hooks/hooks.json" ] \
  || { printf 'install-codex-hooks: unexpected manifest hook path: %s\n' "$hook_rel" >&2; exit 1; }
HOOKS="$ROOT/${hook_rel#./}"
[ -f "$HOOKS" ] && jq -e . "$HOOKS" >/dev/null 2>&1 \
  || { printf 'install-codex-hooks: loaded hook contract is missing or invalid\n' >&2; exit 1; }

require_matcher() {
  event="$1"; shift
  expression='.hooks[$event][]?.matcher // ""'
  values="$(jq -r --arg event "$event" "$expression" "$HOOKS")"
  for token in "$@"; do
    printf '%s\n' "$values" | grep -Eq "(^|[|])${token}([|]|$)" \
      || { printf 'install-codex-hooks: %s does not cover %s\n' "$event" "$token" >&2; exit 1; }
  done
}

require_command() {
  script="$1"
  count="$(jq -r '.. | objects | select(.type? == "command") | .command' "$HOOKS" \
    | grep -Ec "/hooks/${script}([ \"']|$)" || true)"
  [ "$count" -ge 1 ] \
    || { printf 'install-codex-hooks: loaded contract does not delegate to %s\n' "$script" >&2; exit 1; }
  [ -x "$ROOT/hooks/$script" ] \
    || { printf 'install-codex-hooks: delegated hook is missing or non-executable: %s\n' "$script" >&2; exit 1; }
}

require_matcher PreToolUse Bash apply_patch Edit Write update_plan Agent TaskCreate TaskUpdate request_user_input AskUserQuestion
require_matcher PostToolUse request_user_input AskUserQuestion
for script in active-run.sh intake-gate.sh commit-secret-gate.sh state-gate.sh test-gate.sh map-staleness-nudge.sh; do
  require_command "$script"
done

while IFS= read -r command; do
  case "$command" in
    *'"${KIMIFLOW_PLUGIN_ROOT'*) ;;
    *) printf 'install-codex-hooks: unquoted or unpinned plugin root in command: %s\n' "$command" >&2; exit 1 ;;
  esac
  targets="$(printf '%s\n' "$command" | grep -oE '/hooks/[A-Za-z0-9-]+\.sh' | sed 's#.*/##' || true)"
  [ "$(printf '%s\n' "$targets" | grep -c . || true)" -eq 1 ] \
    || { printf 'install-codex-hooks: command must delegate to exactly one hook: %s\n' "$command" >&2; exit 1; }
  case "$targets" in
    active-run.sh|intake-gate.sh|commit-secret-gate.sh|state-gate.sh|test-gate.sh|map-staleness-nudge.sh) ;;
    *) printf 'install-codex-hooks: unexpected delegated hook target: %s\n' "$targets" >&2; exit 1 ;;
  esac
done < <(jq -r '.. | objects | select(.type? == "command") | .command' "$HOOKS")

printf 'kimiflow Codex plugin hook contract verified: %s\n' "$hook_rel"
