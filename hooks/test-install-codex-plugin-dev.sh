#!/usr/bin/env bash
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SCRIPT="$ROOT/hooks/install-codex-plugin-dev.sh"

make_cli() {
  path="$1"
  mkdir -p "$(dirname "$path")"
  printf '#!/usr/bin/env bash\nexit 0\n' >"$path"
  chmod +x "$path"
}

bundled="$WORK/Codex.app/Contents/Resources/codex"
path_cli="$WORK/bin/codex"
env_cli="$WORK/env/codex"
explicit_cli="$WORK/explicit/codex"
make_cli "$bundled"
make_cli "$path_cli"
make_cli "$env_cli"
make_cli "$explicit_cli"

resolved="$(KIMIFLOW_CODEX_APP_ROOT="$WORK/Codex.app" PATH="$WORK/bin:$PATH" "$SCRIPT" --resolve-cli)"
[ "$resolved" = "$bundled" ]
resolved="$(KIMIFLOW_CODEX_APP_ROOT="$WORK/missing.app" KIMIFLOW_CODEX_CLI="$env_cli" PATH="$WORK/bin:$PATH" "$SCRIPT" --resolve-cli)"
[ "$resolved" = "$env_cli" ]
resolved="$(KIMIFLOW_CODEX_APP_ROOT="$WORK/Codex.app" KIMIFLOW_CODEX_CLI="$env_cli" PATH="$WORK/bin:$PATH" "$SCRIPT" --resolve-cli --codex-cli "$explicit_cli")"
[ "$resolved" = "$explicit_cli" ]

candidate="$WORK/candidate/kimiflow"
result="$("$SCRIPT" --prepare-only --output "$candidate" --cachebuster test-install-1)"
[ "$(printf '%s' "$result" | jq -r '.status')" = "prepared" ]
[ "$(printf '%s' "$result" | jq -r '.version')" = "$(jq -r '.version' "$ROOT/.codex-plugin/plugin.json" | cut -d+ -f1)+codex.test-install-1" ]
"$ROOT/hooks/build-plugin-candidate.sh" \
  --check --output "$candidate" --codex-cachebuster test-install-1 >/dev/null

if grep -Eq '/opt/homebrew/bin/codex|launchctl|osascript|killall' "$SCRIPT"; then
  echo "development installer contains a pinned CLI or restart side effect" >&2
  exit 1
fi

printf 'ok   bundled_codex_cli_precedes_path_cli\n'
printf 'ok   explicit_codex_cli_overrides_defaults\n'
printf 'ok   dev_candidate_keeps_cachebuster_identity\n'
printf 'ok   installer_has_no_restart_side_effect\n'
