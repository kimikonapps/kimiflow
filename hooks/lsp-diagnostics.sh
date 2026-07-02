#!/usr/bin/env bash
# kimiflow - local diagnostics scan (ADVISORY, never blocks). Invoked by kimiflow after
# code changes and before final review/commit gates. Uses existing local tooling only:
# project scripts first, then common language diagnostics on PATH. It never installs.
#
# stdout: FLAG advisory lines only.
# stderr: SKIPPED/ignored notes.
# R2 invariant target: hooks/lsp-diagnostics.sh
set -u

root_arg=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      shift
      [ "$#" -gt 0 ] || { printf 'kimiflow lsp-diagnostics: missing --root value - SKIPPED.\n' >&2; exit 0; }
      case "${1:-}" in --*) printf 'kimiflow lsp-diagnostics: missing --root value - SKIPPED.\n' >&2; exit 0 ;; esac
      root_arg="${1:-}"
      shift
      ;;
    --command)
      printf 'kimiflow lsp-diagnostics: --command is not accepted; use an untracked .kimiflow/lsp-diagnostics file for local custom diagnostics - SKIPPED.\n' >&2
      exit 0
      ;;
    --label)
      printf 'kimiflow lsp-diagnostics: --label is not accepted without a configured local diagnostics file - SKIPPED.\n' >&2
      exit 0
      ;;
    --help|-h)
      sed -n '1,12p' "$0"
      exit 0
      ;;
    *)
      shift
      ;;
  esac
done

if [ -n "$root_arg" ]; then
  root="$root_arg"
else
  root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
[ -n "$root" ] || exit 0
[ -d "$root" ] || exit 0
cd "$root" 2>/dev/null || exit 0

first_non_comment_line() {
  local file="$1"
  awk '
    /^[[:space:]]*($|#)/ { next }
    { print; exit }
  ' "$file" 2>/dev/null
}

tracked_file() {
  git ls-files --error-unmatch "$1" >/dev/null 2>&1
}

has_pkg_script() {
  local name="$1"
  [ -f package.json ] || return 1
  if command -v jq >/dev/null 2>&1; then
    jq -e --arg name "$name" '.scripts[$name]?' package.json >/dev/null 2>&1
    return $?
  fi
  grep -Eq "\"$name\"[[:space:]]*:" package.json 2>/dev/null
}

pkg_runner() {
  if [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null 2>&1; then
    printf 'pnpm -s run'
  elif [ -f yarn.lock ] && command -v yarn >/dev/null 2>&1; then
    printf 'yarn -s'
  elif command -v npm >/dev/null 2>&1; then
    printf 'npm run -s'
  else
    return 1
  fi
}

has_python_files() {
  find . -path './.git' -prune -o -path './.kimiflow' -prune -o -name '*.py' -print -quit 2>/dev/null | grep -q .
}

list_changed_files() {
  {
    git diff --name-only --diff-filter=ACMRTUXB HEAD -- 2>/dev/null
    git ls-files --others --exclude-standard 2>/dev/null
  } | sed '/^[[:space:]]*$/d;/^\.kimiflow\//d' | sort -u
}

add_command() {
  [ "$command_count" -lt "$max_commands" ] || return 0
  labels[$command_count]="$1"
  commands[$command_count]="$2"
  command_count=$((command_count + 1))
}

choose_commands() {
  local config runner has_package_typecheck
  has_package_typecheck=0

  config=".kimiflow/lsp-diagnostics"
  if [ -f "$config" ]; then
    if tracked_file "$config"; then
      printf 'kimiflow lsp-diagnostics: tracked .kimiflow/lsp-diagnostics ignored for safety; use an untracked local file.\n' >&2
    else
      command_line="$(first_non_comment_line "$config")"
      if [ -n "$command_line" ]; then
        add_command "configured diagnostics" "$command_line"
        return 0
      fi
    fi
  fi

  if [ -f package.json ]; then
    runner="$(pkg_runner || true)"
    if [ -n "$runner" ] && has_pkg_script typecheck; then
      add_command "package typecheck" "$runner typecheck"
      has_package_typecheck=1
    fi
    if [ -n "$runner" ] && has_pkg_script lint; then
      add_command "package lint" "$runner lint"
    fi
  fi

  if [ "$has_package_typecheck" -eq 0 ] && [ -f tsconfig.json ] && command -v tsc >/dev/null 2>&1; then
    add_command "typescript diagnostics" "tsc --noEmit --pretty false"
  fi

  if { [ -f pyrightconfig.json ] || [ -f pyproject.toml ] || has_python_files; } && command -v pyright >/dev/null 2>&1; then
    add_command "pyright diagnostics" "pyright"
  fi

  if has_python_files && command -v ruff >/dev/null 2>&1; then
    add_command "ruff diagnostics" "ruff check ."
  fi

  if { [ -f mypy.ini ] || [ -f setup.cfg ] || [ -f pyproject.toml ]; } && command -v mypy >/dev/null 2>&1; then
    add_command "mypy diagnostics" "mypy ."
  fi

  [ "$command_count" -gt 0 ]
}

summarize_output() {
  tr '\r' '\n' | sed '/^[[:space:]]*$/d' | head -1 | cut -c1-220
}

changed_matches() {
  local output_file="$1"
  local path count
  count=0
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    if grep -Fq -- "$path" "$output_file"; then
      [ "$count" -gt 0 ] && printf ', '
      printf '%s' "$path"
      count=$((count + 1))
      [ "$count" -ge 3 ] && { printf ', ...'; break; }
    fi
  done <<EOF
$changed_paths
EOF
}

flag() {
  local scope="$1" matches="$2" summary="$3" detail
  detail=""
  case "$scope" in
    changed-files) detail="; touched: $matches" ;;
    project-wide) detail="; no touched file referenced" ;;
    unknown-scope) detail="; changed files unknown" ;;
  esac
  if [ -n "$summary" ]; then
    printf -- '- [FLAG] local diagnostics (%s) - %s reported issues via `%s`%s; review before commit. First line: %s\n' "$scope" "$label" "$command_line" "$detail" "$summary"
  else
    printf -- '- [FLAG] local diagnostics (%s) - %s reported issues via `%s`%s; review before commit.\n' "$scope" "$label" "$command_line" "$detail"
  fi
}

max_commands="${KIMIFLOW_LSP_MAX_COMMANDS:-3}"
case "$max_commands" in
  [1-9]|[1-9][0-9]) ;;
  *) max_commands=3 ;;
esac
[ "$max_commands" -le 5 ] || max_commands=5

command_count=0
labels=()
commands=()
command_line=""
changed_paths="$(list_changed_files || true)"

if ! choose_commands; then
  printf 'kimiflow lsp-diagnostics: no local diagnostics command found - SKIPPED (no install attempted). Configure an untracked .kimiflow/lsp-diagnostics or add a local typecheck/lint tool.\n' >&2
  exit 0
fi

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/kimiflow-lsp-diagnostics.XXXXXX")" || exit 0
trap 'rm -rf "$tmpdir"' EXIT

i=0
while [ "$i" -lt "$command_count" ]; do
  label="${labels[$i]}"
  command_line="${commands[$i]}"
  tmp="$tmpdir/out-$i"

  if ! sh -c "$command_line" >"$tmp" 2>&1; then
    summary="$(summarize_output < "$tmp")"
    matches="$(changed_matches "$tmp")"
    if [ -n "$matches" ]; then
      scope="changed-files"
    elif [ -n "$changed_paths" ]; then
      scope="project-wide"
    else
      scope="unknown-scope"
    fi
    flag "$scope" "$matches" "$summary"
  fi

  i=$((i + 1))
done

exit 0
