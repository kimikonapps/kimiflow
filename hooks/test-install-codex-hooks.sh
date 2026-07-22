#!/usr/bin/env bash
# kimiflow — bundled Codex hook contract validation tests.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

out="$(CODEX_HOME="$WORK/codex-home" "$ROOT/hooks/install-codex-hooks.sh" --check)"
printf '%s\n' "$out" | grep -Fq 'Codex plugin hook contract verified'
[ ! -e "$WORK/codex-home/hooks" ]
printf 'ok   bundled_contract_needs_no_unregistered_wrappers\n'

mkdir -p "$WORK/plugin/.codex-plugin" "$WORK/plugin/hooks"
cp "$ROOT/.codex-plugin/plugin.json" "$WORK/plugin/.codex-plugin/plugin.json"
cp "$ROOT/hooks/hooks.json" "$WORK/plugin/hooks/hooks.json"
for file in install-codex-hooks.sh active-run.sh intake-gate.sh commit-secret-gate.sh state-gate.sh test-gate.sh map-staleness-nudge.sh; do
  cp "$ROOT/hooks/$file" "$WORK/plugin/hooks/$file"
  chmod +x "$WORK/plugin/hooks/$file"
done

jq '(.hooks.PreToolUse[] | select(.hooks[]?.command? | contains("/hooks/intake-gate.sh")) | .hooks[] | select(.command? | contains("/hooks/intake-gate.sh")) | .command) |= sub("/hooks/intake-gate\\.sh"; "/hooks/missing-gate.sh")' \
  "$WORK/plugin/hooks/hooks.json" > "$WORK/plugin/hooks/hooks.json.tmp"
mv "$WORK/plugin/hooks/hooks.json.tmp" "$WORK/plugin/hooks/hooks.json"
if "$WORK/plugin/hooks/install-codex-hooks.sh" --check >/dev/null 2>&1; then
  printf 'corrupt delegated target passed validation\n' >&2
  exit 1
fi
printf 'ok   exact_delegated_target_is_validated\n'

jq '.hooks = "./hooks/other.json"' "$ROOT/.codex-plugin/plugin.json" > "$WORK/plugin/.codex-plugin/plugin.json"
if "$WORK/plugin/hooks/install-codex-hooks.sh" --check >/dev/null 2>&1; then
  printf 'unexpected manifest path passed validation\n' >&2
  exit 1
fi
printf 'ok   manifest_path_is_exact\n'
