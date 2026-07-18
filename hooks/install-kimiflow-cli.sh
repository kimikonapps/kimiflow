#!/usr/bin/env bash
# kimiflow — install the optional managed terminal runner wrapper.
set -eu

usage() {
  cat <<'EOF'
Usage: hooks/install-kimiflow-cli.sh [--prefix <path>] [--check]

Installs the optional `kimiflow` command into <prefix>/bin (default: ~/.local).
The embedded Codex/Claude plugin remains the standard entry point.
EOF
}

PREFIX="${KIMIFLOW_PREFIX:-$HOME/.local}"
CHECK_ONLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix) shift; [ -n "${1:-}" ] || { usage >&2; exit 2; }; PREFIX="$1" ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/kimiflow-root.sh"
PLUGIN_ROOT="$(kimiflow_root)"
SOURCE="$PLUGIN_ROOT/hooks/kimiflow-runner.sh"
BIN_DIR="$PREFIX/bin"
TARGET="$BIN_DIR/kimiflow"
MANAGED_MARKER="kimiflow managed CLI wrapper"

quote_sh() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\''/g")"
}

check_target() {
  [ -x "$SOURCE" ] || return 1
  [ -x "$TARGET" ] && [ ! -L "$TARGET" ] || return 1
  grep -Fq "$MANAGED_MARKER" "$TARGET" || return 1
  grep -Fq "KIMIFLOW_PLUGIN_ROOT=$(quote_sh "$PLUGIN_ROOT")" "$TARGET" || return 1
}

if [ "$CHECK_ONLY" -eq 1 ]; then
  check_target || {
    printf 'install-kimiflow-cli: managed wrapper missing or stale: %s\n' "$TARGET" >&2
    exit 1
  }
  printf 'kimiflow CLI installed at %s\n' "$TARGET"
  exit 0
fi

[ -x "$SOURCE" ] || { printf 'install-kimiflow-cli: source runner missing: %s\n' "$SOURCE" >&2; exit 1; }
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  if ! check_target; then
    printf 'install-kimiflow-cli: refusing to overwrite unrelated executable: %s\n' "$TARGET" >&2
    exit 1
  fi
fi
mkdir -p "$BIN_DIR"
[ -d "$BIN_DIR" ] && [ ! -L "$BIN_DIR" ] || { printf 'install-kimiflow-cli: unsafe bin directory: %s\n' "$BIN_DIR" >&2; exit 1; }
tmp="$(mktemp "$BIN_DIR/.kimiflow.XXXXXX")"
trap 'rm -f "$tmp"' EXIT HUP INT TERM
{
  printf '#!/usr/bin/env bash\n'
  printf '# %s\n' "$MANAGED_MARKER"
  printf 'export KIMIFLOW_HOST=codex\n'
  printf 'export KIMIFLOW_PLUGIN_ROOT=%s\n' "$(quote_sh "$PLUGIN_ROOT")"
  printf 'exec "$KIMIFLOW_PLUGIN_ROOT/hooks/kimiflow-runner.sh" "$@"\n'
} > "$tmp"
chmod 755 "$tmp"
mv "$tmp" "$TARGET"
trap - EXIT HUP INT TERM
printf 'installed %s\n' "$TARGET"
