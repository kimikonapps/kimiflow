#!/usr/bin/env bash
# kimiflow — managed optional CLI installer contract.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PREFIX="$WORK/prefix"
TARGET="$PREFIX/bin/kimiflow"

"$ROOT/hooks/install-kimiflow-cli.sh" --prefix "$PREFIX" >/dev/null
[ -x "$TARGET" ]
grep -q 'kimiflow managed CLI wrapper' "$TARGET"
grep -Fq "$ROOT" "$TARGET"
"$ROOT/hooks/install-kimiflow-cli.sh" --prefix "$PREFIX" --check >/dev/null
printf 'ok   installer_managed_wrapper_contract\n'

rm -f "$TARGET"
printf 'unrelated executable\n' > "$TARGET"
chmod +x "$TARGET"
before="$(shasum -a 256 "$TARGET" | awk '{print $1}')"
if "$ROOT/hooks/install-kimiflow-cli.sh" --prefix "$PREFIX" >/dev/null 2>&1; then
  printf 'installer unexpectedly overwrote unrelated executable\n' >&2
  exit 1
fi
after="$(shasum -a 256 "$TARGET" | awk '{print $1}')"
[ "$before" = "$after" ]
printf 'ok   installer_refuses_unmanaged_collision\n'
