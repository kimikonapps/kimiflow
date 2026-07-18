#!/usr/bin/env bash
# kimiflow — stable Codex hook installer feature-detection contract.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAKE_BIN="$WORK/bin"
mkdir -p "$FAKE_BIN"

cat > "$FAKE_BIN/codex" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-} ${2:-}" = "features list" ]; then
  printf '%s\n' "${FAKE_CODEX_FEATURES:-}"
  exit 0
fi
exit 2
EOF
chmod +x "$FAKE_BIN/codex"

run_installer() {
  CODEX_HOME="$WORK/codex-$1" PATH="$FAKE_BIN:$PATH" FAKE_CODEX_FEATURES="$2" \
    "$ROOT/hooks/install-codex-hooks.sh" 2>&1
}

out="$(run_installer canonical 'hooks stable true
plugin_hooks removed false')"
printf '%s\n' "$out" | grep -Fq 'hooks feature is enabled.'
if printf '%s\n' "$out" | grep -Fq 'did not appear enabled'; then
  printf 'canonical hooks feature produced a false disabled warning\n' >&2
  exit 1
fi
printf 'ok   installer_detects_canonical_hooks_feature\n'

out="$(run_installer deprecated 'codex_hooks deprecated true
plugin_hooks removed false')"
printf '%s\n' "$out" | grep -Fq 'hooks feature is enabled.'
printf 'ok   installer_accepts_deprecated_codex_hooks_alias\n'

out="$(run_installer disabled 'hooks stable false
plugin_hooks removed false')"
printf '%s\n' "$out" | grep -Fq 'hooks did not appear enabled'
printf 'ok   installer_warns_when_hooks_are_disabled\n'
