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

before_manifest="$(shasum -a 256 "$ROOT/plugins/kimiflow/.codex-plugin/plugin.json" | awk '{print $1}')"
if CODEX_THREAD_ID="live-codex-task" "$SCRIPT" --codex-cli "$explicit_cli" >"$WORK/live-install.out" 2>&1; then
  echo "development installer allowed an unacknowledged live-task reinstall" >&2
  exit 1
fi
grep -Fq 'refusing a silent reinstall inside a live Codex task' "$WORK/live-install.out"
after_manifest="$(shasum -a 256 "$ROOT/plugins/kimiflow/.codex-plugin/plugin.json" | awk '{print $1}')"
[ "$before_manifest" = "$after_manifest" ]
if "$SCRIPT" --prepare-only --output "$WORK/invalid-ack" --acknowledge-new-thread-required >"$WORK/invalid-ack.out" 2>&1; then
  echo "development installer accepted live-task acknowledgement outside installation" >&2
  exit 1
fi

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
printf 'ok   live_task_reinstall_requires_explicit_final_action\n'
