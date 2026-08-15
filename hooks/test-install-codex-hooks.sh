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

mkdir -p "$WORK/legacy-home"
cat >"$WORK/legacy-home/hooks.json" <<'EOF'
{
  "hooks": {
    "SessionStart": [
      {"hooks": [
        {"type": "command", "command": "gh-axi"},
        {"name": "Kimiflow custom user hook", "type": "command", "command": "custom-kimiflow-helper"}
      ]}
    ],
    "UserPromptSubmit": [
      {"hooks": [
        {"name": "Kimiflow active session", "type": "command", "command": "KIMIFLOW_HOST=codex /obsolete/kimiflow/hooks/active-run.sh prompt-context"},
        {"name": "Unrelated prompt hook", "type": "command", "command": "other-prompt-hook"}
      ]}
    ],
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [
        {"name": "Kimiflow Product Intake", "type": "command", "command": "KIMIFLOW_HOST=codex /obsolete/kimiflow/hooks/intake-gate.sh"},
        {"name": "Unrelated gate", "type": "command", "command": "other-gate"}
      ]}
    ],
    "Stop": [
      {"hooks": [
        {"name": "Kimiflow test gate", "type": "command", "command": "KIMIFLOW_HOST=codex /obsolete/kimiflow/hooks/test-gate.sh"}
      ]}
    ]
  }
}
EOF

if CODEX_HOME="$WORK/legacy-home" "$ROOT/hooks/install-codex-hooks.sh" --check >"$WORK/legacy-check.out" 2>&1; then
  printf 'obsolete global Kimiflow hooks passed validation\n' >&2
  exit 1
fi
grep -Fq 'found 3 obsolete global Kimiflow hook(s)' "$WORK/legacy-check.out"
migration="$(CODEX_HOME="$WORK/legacy-home" "$ROOT/hooks/install-codex-hooks.sh" --migrate-legacy)"
printf '%s\n' "$migration" | grep -Fq 'removed=3'
backup="$(printf '%s\n' "$migration" | sed -n 's/.* backup=\(.*\)$/\1/p')"
[ -f "$backup" ]
jq -e '
  .hooks.SessionStart[0].hooks[0].command == "gh-axi"
  and .hooks.SessionStart[0].hooks[1].command == "custom-kimiflow-helper"
  and .hooks.UserPromptSubmit[0].hooks[0].command == "other-prompt-hook"
  and .hooks.PreToolUse[0].hooks[0].command == "other-gate"
  and (.hooks | has("Stop") | not)
' "$WORK/legacy-home/hooks.json" >/dev/null
jq -e '.hooks.Stop[0].hooks[0].name == "Kimiflow test gate"' "$backup" >/dev/null
CODEX_HOME="$WORK/legacy-home" "$ROOT/hooks/install-codex-hooks.sh" --check >/dev/null
second="$(CODEX_HOME="$WORK/legacy-home" "$ROOT/hooks/install-codex-hooks.sh" --migrate-legacy)"
printf '%s\n' "$second" | grep -Fq 'already clean'
printf 'ok   legacy_global_hooks_migrate_surgically_and_idempotently\n'

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
