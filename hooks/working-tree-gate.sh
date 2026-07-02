#!/usr/bin/env bash
# kimiflow - working tree start gate. Orchestrator-invoked, not a hook.
#
# Usage:
#   working-tree-gate.sh [--root <path>] [--max-paths <n>]
#
# Output:
#   WORKING_TREE_GATE<TAB>OPEN|CLOSED<TAB>dirty=<n><TAB>staged=<n><TAB>unstaged=<n><TAB>untracked=<n><TAB>reason=<code><TAB>detail=<paths>
# R2 invariant target: hooks/working-tree-gate.sh
set -u

root_arg=""
max_paths=5
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      shift
      [ "$#" -gt 0 ] || { printf 'WORKING_TREE_GATE\tCLOSED\tdirty=0\tstaged=0\tunstaged=0\tuntracked=0\treason=malformed\tdetail=missing_root\n'; exit 0; }
      root_arg="${1:-}"
      shift
      ;;
    --max-paths)
      shift
      max_paths="${1:-5}"
      shift
      ;;
    --pretty)
      shift
      ;;
    --help|-h)
      sed -n '1,10p' "$0"
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

case "$max_paths" in
  [1-9]|[1-9][0-9]) ;;
  *) max_paths=5 ;;
esac

if [ -n "$root_arg" ]; then
  root="$root_arg"
else
  root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi

if [ -z "$root" ] || ! git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf 'WORKING_TREE_GATE\tOPEN\tdirty=0\tstaged=0\tunstaged=0\tuntracked=0\treason=no-git\tdetail=\n'
  exit 0
fi

tmp="$(mktemp "${TMPDIR:-/tmp}/kimiflow-working-tree.XXXXXX")" || exit 0
trap 'rm -f "$tmp"' EXIT

git -C "$root" status --porcelain=v1 --untracked-files=normal 2>/dev/null \
  | awk '
      {
        path = substr($0, 4)
        sub(/^"|"$/, "", path)
        if (path ~ /^\.kimiflow(\/|$)/) next
        print
      }
    ' > "$tmp"

staged=0
unstaged=0
untracked=0
dirty_paths=0
details=""
detail_count=0

while IFS= read -r line; do
  [ -n "$line" ] || continue
  x="${line%"${line#?}"}"
  rest="${line#?}"
  y="${rest%"${rest#?}"}"
  path="${line#???}"

  if [ "$x$y" = "??" ]; then
    untracked=$((untracked + 1))
  else
    [ "$x" != " " ] && staged=$((staged + 1))
    [ "$y" != " " ] && unstaged=$((unstaged + 1))
  fi

  dirty_paths=$((dirty_paths + 1))
  if [ "$detail_count" -lt "$max_paths" ]; then
    [ -n "$details" ] && details="$details,"
    details="$details$path"
    detail_count=$((detail_count + 1))
  fi
done < "$tmp"

dirty="$dirty_paths"
if [ "$dirty" -eq 0 ]; then
  printf 'WORKING_TREE_GATE\tOPEN\tdirty=0\tstaged=0\tunstaged=0\tuntracked=0\treason=clean\tdetail=\n'
else
  printf 'WORKING_TREE_GATE\tCLOSED\tdirty=%s\tstaged=%s\tunstaged=%s\tuntracked=%s\treason=working-tree-dirty\tdetail=%s\n' "$dirty" "$staged" "$unstaged" "$untracked" "$details"
fi
