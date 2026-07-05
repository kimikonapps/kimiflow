#!/usr/bin/env bash
# kimiflow — install stable Codex hook wrappers into ${CODEX_HOME:-~/.codex}/hooks.
#
# Codex plugin hooks may not be available in every CLI/app build. This installer
# uses the stable Codex hook directory and writes tiny wrappers that pin the
# source plugin root, then delegate to the repo's tested Kimiflow hook scripts.
set -eu

usage() {
  cat <<'EOF'
Usage: hooks/install-codex-hooks.sh [--check]

Installs Kimiflow Codex hook wrappers into:
  ${CODEX_HOME:-$HOME/.codex}/hooks

Options:
  --check   Verify expected wrappers already exist and point at this plugin root.
EOF
}

CHECK_ONLY=0
case "${1:-}" in
  "" ) ;;
  --check ) CHECK_ONLY=1 ;;
  -h|--help ) usage; exit 0 ;;
  * ) usage >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/kimiflow-root.sh"
PLUGIN_ROOT="$(kimiflow_root)"
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
HOOK_DIR="$CODEX_DIR/hooks"
MANAGED_MARKER="kimiflow managed Codex hook wrapper"

quote_sh() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

wrapper_path() {
  printf '%s/%s\n' "$HOOK_DIR" "$1"
}

ensure_source() {
  src="$PLUGIN_ROOT/hooks/$1"
  if [ ! -x "$src" ]; then
    printf 'install-codex-hooks: missing or non-executable source hook: %s\n' "$src" >&2
    exit 1
  fi
}

check_wrapper() {
  wrapper="$(wrapper_path "$1")"
  source_script="$2"
  ensure_source "$source_script"
  [ -x "$wrapper" ] || return 1
  grep -Fq "$MANAGED_MARKER" "$wrapper" || return 1
  grep -Fq "KIMIFLOW_PLUGIN_ROOT=$(quote_sh "$PLUGIN_ROOT")" "$wrapper" || return 1
}

write_wrapper() {
  wrapper="$(wrapper_path "$1")"
  source_script="$2"
  ensure_source "$source_script"
  mkdir -p "$HOOK_DIR"
  tmp="$wrapper.tmp.$$"
  {
    printf '#!/usr/bin/env bash\n'
    printf '# %s\n' "$MANAGED_MARKER"
    printf '# Source: %s/hooks/%s\n' "$PLUGIN_ROOT" "$source_script"
    printf 'export KIMIFLOW_HOST="${KIMIFLOW_HOST:-codex}"\n'
    printf 'export KIMIFLOW_PLUGIN_ROOT=%s\n' "$(quote_sh "$PLUGIN_ROOT")"
    printf 'exec "$KIMIFLOW_PLUGIN_ROOT/hooks/%s" "$@"\n' "$source_script"
  } > "$tmp"
  chmod +x "$tmp"
  mv "$tmp" "$wrapper"
  printf 'installed %s\n' "$wrapper"
}

# Stable-wrapper scope: only the four ENFORCEMENT hooks are wrapped here —
# commit-secret-gate, state-gate, test-gate (blocking gates) plus active-run
# (active-session contract; the one wrapper serves both its prompt-context and
# stop-gate sub-commands). Together they cover 5 of the 6 plugin-hook commands
# in hooks.json. The 6th, map-staleness-nudge.sh, is intentionally NOT wrapped:
# it is a non-blocking, advisory Stop nudge that emits only a systemMessage (no
# permission/decision), so it has no enforcement effect on the stable Codex hook
# surface. On Codex its staleness-surfacing role is model-driven via
# project-map-status.sh (see docs/render/kimiflow/overlays/codex.md); the full
# 6-hook set is carried only by the experimental plugin_hooks path (hooks.json).
if [ "$CHECK_ONLY" -eq 1 ]; then
  check_wrapper kimiflow-commit-secret-gate.sh commit-secret-gate.sh
  check_wrapper kimiflow-state-gate.sh state-gate.sh
  check_wrapper kimiflow-test-gate.sh test-gate.sh
  check_wrapper kimiflow-active-run.sh active-run.sh
  printf 'kimiflow Codex hooks installed in %s\n' "$HOOK_DIR"
  exit 0
fi

write_wrapper kimiflow-commit-secret-gate.sh commit-secret-gate.sh
write_wrapper kimiflow-state-gate.sh state-gate.sh
write_wrapper kimiflow-test-gate.sh test-gate.sh
write_wrapper kimiflow-active-run.sh active-run.sh

if command -v codex >/dev/null 2>&1; then
  if codex features list 2>/dev/null | awk '$1 == "codex_hooks" && $3 == "true" { found=1 } END { exit found ? 0 : 1 }'; then
    printf 'codex_hooks feature is enabled.\n'
  else
    printf 'warning: codex_hooks did not appear enabled; enable Codex hooks before relying on these wrappers.\n' >&2
  fi
  if codex features list 2>/dev/null | awk '$1 == "plugin_hooks" && $3 == "true" { found=1 } END { exit found ? 0 : 1 }'; then
    printf 'plugin_hooks feature is enabled; bundled hooks.json may also be used by this Codex build.\n'
  else
    printf 'plugin_hooks feature not enabled; using stable %s wrappers.\n' "$HOOK_DIR"
  fi
else
  printf 'codex CLI not found; installed wrappers, but runtime feature status was not checked.\n' >&2
fi
