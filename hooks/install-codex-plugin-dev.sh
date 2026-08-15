#!/usr/bin/env bash
# Build, cache-bust, install, and verify this checkout's Codex plugin candidate.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
CANDIDATE="$ROOT/plugins/kimiflow"
MARKETPLACE_FILE="$ROOT/.agents/plugins/marketplace.json"
CACHEBUSTER="$(date -u +%Y%m%d%H%M%S)"
CODEX_CLI=""
MODE="install"
ACKNOWLEDGE_NEW_THREAD_REQUIRED="no"

usage() {
  cat <<'EOF'
Usage: hooks/install-codex-plugin-dev.sh [options]

Options:
  --cachebuster TOKEN  Override the generated UTC cachebuster.
  --codex-cli PATH     Use this Codex CLI explicitly.
  --output PATH        Build a candidate at PATH (requires --prepare-only).
  --prepare-only       Build and verify the candidate without installing it.
  --resolve-cli        Print the selected Codex CLI and exit.
  --acknowledge-new-thread-required
                       Allow installation from a live Codex task only as its
                       final action; the updated plugin requires a new task.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cachebuster)
      [ "$#" -ge 2 ] || { echo "install-codex-plugin-dev: --cachebuster requires a token" >&2; exit 2; }
      CACHEBUSTER="$2"; shift 2 ;;
    --codex-cli)
      [ "$#" -ge 2 ] || { echo "install-codex-plugin-dev: --codex-cli requires a path" >&2; exit 2; }
      CODEX_CLI="$2"; shift 2 ;;
    --output)
      [ "$#" -ge 2 ] || { echo "install-codex-plugin-dev: --output requires a path" >&2; exit 2; }
      CANDIDATE="$2"; shift 2 ;;
    --prepare-only) MODE="prepare"; shift ;;
    --resolve-cli) MODE="resolve"; shift ;;
    --acknowledge-new-thread-required) ACKNOWLEDGE_NEW_THREAD_REQUIRED="yes"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install-codex-plugin-dev: unknown argument: $1" >&2; exit 2 ;;
  esac
done

select_codex_cli() {
  if [ -n "$CODEX_CLI" ]; then
    printf '%s\n' "$CODEX_CLI"
    return
  fi
  if [ -n "${KIMIFLOW_CODEX_CLI:-}" ]; then
    printf '%s\n' "$KIMIFLOW_CODEX_CLI"
    return
  fi
  app_root="${KIMIFLOW_CODEX_APP_ROOT:-/Applications/Codex.app}"
  bundled_cli="$app_root/Contents/Resources/codex"
  if [ -x "$bundled_cli" ]; then
    printf '%s\n' "$bundled_cli"
    return
  fi
  command -v codex 2>/dev/null || {
    echo "install-codex-plugin-dev: no Codex CLI found" >&2
    return 1
  }
}

if [ "$MODE" = "resolve" ]; then
  selected_cli="$(select_codex_cli)"
  [ -x "$selected_cli" ] || { echo "install-codex-plugin-dev: Codex CLI is not executable: $selected_cli" >&2; exit 1; }
  printf '%s\n' "$selected_cli"
  exit 0
fi

if [ "$CANDIDATE" != "$ROOT/plugins/kimiflow" ] && [ "$MODE" != "prepare" ]; then
  echo "install-codex-plugin-dev: --output is allowed only with --prepare-only" >&2
  exit 2
fi

if [ "$MODE" != "install" ] && [ "$ACKNOWLEDGE_NEW_THREAD_REQUIRED" = "yes" ]; then
  echo "install-codex-plugin-dev: --acknowledge-new-thread-required is valid only for installation" >&2
  exit 2
fi

if [ "$MODE" = "install" ] && [ -n "${CODEX_THREAD_ID:-}" ] \
  && [ "$ACKNOWLEDGE_NEW_THREAD_REQUIRED" != "yes" ]; then
  echo "install-codex-plugin-dev: refusing a silent reinstall inside a live Codex task" >&2
  echo "install-codex-plugin-dev: prepare and test first, then use --acknowledge-new-thread-required only as the task's final action and continue in a new task" >&2
  exit 1
fi

builder_args=(--write --output "$CANDIDATE" --codex-cachebuster "$CACHEBUSTER")
"$ROOT/hooks/build-plugin-candidate.sh" "${builder_args[@]}" >/dev/null
"$ROOT/hooks/build-plugin-candidate.sh" --check --output "$CANDIDATE" --codex-cachebuster "$CACHEBUSTER" >/dev/null
candidate_version="$(jq -er '.version' "$CANDIDATE/.codex-plugin/plugin.json")"

if [ "$MODE" = "prepare" ]; then
  jq -n --arg status prepared --arg version "$candidate_version" --arg candidate "$CANDIDATE" \
    '{status: $status, version: $version, candidate: $candidate}'
  exit 0
fi

selected_cli="$(select_codex_cli)"
[ -x "$selected_cli" ] || { echo "install-codex-plugin-dev: Codex CLI is not executable: $selected_cli" >&2; exit 1; }
[ -f "$MARKETPLACE_FILE" ] || { echo "install-codex-plugin-dev: marketplace file missing: $MARKETPLACE_FILE" >&2; exit 1; }
marketplace_name="$(jq -er '.name' "$MARKETPLACE_FILE")"

marketplaces_json="$(mktemp)"
install_json="$(mktemp)"
trap 'rm -f "$marketplaces_json" "$install_json"' EXIT
"$selected_cli" plugin marketplace list --json >"$marketplaces_json"
configured_root="$(jq -er --arg name "$marketplace_name" '.marketplaces[] | select(.name == $name) | .root' "$marketplaces_json")"
if [ "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$configured_root")" != "$ROOT" ]; then
  echo "install-codex-plugin-dev: marketplace '$marketplace_name' does not point at this checkout" >&2
  exit 1
fi

"$selected_cli" plugin add "kimiflow@$marketplace_name" --json >"$install_json"
installed_version="$(jq -er '.version' "$install_json")"
installed_path="$(jq -er '.installedPath' "$install_json")"
[ "$installed_version" = "$candidate_version" ] || {
  echo "install-codex-plugin-dev: installed version mismatch ($installed_version != $candidate_version)" >&2
  exit 1
}
[ -d "$installed_path" ] && [ ! -L "$installed_path" ] || {
  echo "install-codex-plugin-dev: installed path missing or unsafe: $installed_path" >&2
  exit 1
}
cmp "$CANDIDATE/RUNTIME-FINGERPRINT.json" "$installed_path/RUNTIME-FINGERPRINT.json" >/dev/null
cmp "$CANDIDATE/hooks/kimiflow_core/active_run.py" "$installed_path/hooks/kimiflow_core/active_run.py" >/dev/null

jq -n \
  --arg status installed \
  --arg version "$installed_version" \
  --arg installedPath "$installed_path" \
  --arg codexCli "$selected_cli" \
  '{
    status: $status,
    version: $version,
    installedPath: $installedPath,
    codexCli: $codexCli,
    restartRequired: true,
    nextAction: "start_new_codex_task"
  }'
