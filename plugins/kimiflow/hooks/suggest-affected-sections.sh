#!/usr/bin/env bash
# kimiflow — suggest affected project-map sections from intent/problem terms (read-only).
# Orchestrator-invoked, not a hook. Outputs candidate sections + their paths so Phase 2 can
# feed the top sections' paths to `project-map-status.sh coverage --affected`.
#
# Usage:
#   suggest-affected-sections.sh --intent <file> [--index <path>] [--top <n>]
#   suggest-affected-sections.sh --text "<terms>" [--index <path>] [--top <n>]
#
# Output (stdout JSON, stable):
#   {"sections":[{"name":"<s>","score":<n>,"paths":["<prefix-or-file>",...]},...]}
# Scoring per keyword: hit in symbols keys ×2, in files/prefixes ×1, in section name ×3.
# Missing/empty/invalid index or no match → {"sections":[]} exit 0.
set -u

die() { printf 'suggest-affected-sections: %s\n' "$1" >&2; exit "${2:-1}"; }

command -v jq >/dev/null 2>&1 || die "jq is required" 2

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

contains() {
  local needle="$1"; shift
  local item
  for item in "$@"; do
    [ "$item" = "$needle" ] && return 0
  done
  return 1
}

emit_empty() { printf '{"sections":[]}\n'; exit 0; }

INDEX=""
INTENT_FILE=""
TEXT=""
TOP=5

while [ "$#" -gt 0 ]; do
  case "$1" in
    --intent) shift; INTENT_FILE="${1:-}" ;;
    --text) shift; TEXT="${1:-}" ;;
    --index) shift; INDEX="${1:-}" ;;
    --top) shift; TOP="${1:-5}" ;;
    --help|-h) sed -n '1,14p' "$0" >&2; exit 0 ;;
    *) ;;
  esac
  shift
done

ROOT="$(repo_root)"
[ -n "$INDEX" ] || INDEX="$ROOT/.kimiflow/project/INDEX.json"

if [ -n "$INTENT_FILE" ] && [ -f "$INTENT_FILE" ]; then
  TEXT="$TEXT $(cat "$INTENT_FILE" 2>/dev/null)"
fi

[ -n "$TEXT" ] || emit_empty
[ -f "$INDEX" ] || emit_empty
jq -e . "$INDEX" >/dev/null 2>&1 || emit_empty

SECTIONS=()
while IFS= read -r s; do [ -n "$s" ] && SECTIONS+=("$s"); done < <(jq -r '.sections // {} | keys_unsorted[]' "$INDEX" 2>/dev/null)
[ "${#SECTIONS[@]}" -gt 0 ] || emit_empty

# Stopwords (English + German) for short, low-signal terms.
STOPWORDS="the and for with that this from have has are was were will not but you your our the und der die das ein eine fuer mit von den dem auch noch nur wie aber oder wenn dann sein soll wird kann"

KEYWORDS=()
while IFS= read -r kw; do
  [ -n "$kw" ] || continue
  case " $STOPWORDS " in *" $kw "*) continue ;; esac
  contains "$kw" ${KEYWORDS[@]+"${KEYWORDS[@]}"} || KEYWORDS+=("$kw")
done < <(printf '%s\n' "$TEXT" | grep -oE '[A-Za-z_][A-Za-z0-9_]{2,}' | tr '[:upper:]' '[:lower:]')
[ "${#KEYWORDS[@]}" -gt 0 ] || emit_empty

ROWS='[]'
for s in "${SECTIONS[@]}"; do
  sym="$(jq -r --arg s "$s" '((.sections[$s].symbols // {}) | keys[]?)' "$INDEX" 2>/dev/null | tr '\n' ' ' | tr '[:upper:]' '[:lower:]')"
  filespre="$(jq -r --arg s "$s" '((.sections[$s].files // [])[]?, (.sections[$s].prefixes // [])[]?)' "$INDEX" 2>/dev/null | tr '\n' ' ' | tr '[:upper:]' '[:lower:]')"
  name_l="$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]')"
  score=0
  for kw in "${KEYWORDS[@]}"; do
    case "$sym" in *"$kw"*) score=$((score + 2)) ;; esac
    case "$filespre" in *"$kw"*) score=$((score + 1)) ;; esac
    case "$name_l" in *"$kw"*) score=$((score + 3)) ;; esac
  done
  [ "$score" -gt 0 ] || continue
  paths_json="$(jq -c --arg s "$s" '((.sections[$s].prefixes // []) + (.sections[$s].files // [])) | unique' "$INDEX" 2>/dev/null)"
  [ -n "$paths_json" ] || paths_json='[]'
  ROWS="$(printf '%s\n' "$ROWS" | jq -c --arg n "$s" --argjson score "$score" --argjson paths "$paths_json" '. + [{name: $n, score: $score, paths: $paths}]')"
done

printf '%s\n' "$ROWS" | jq -c --argjson top "$TOP" '{sections: (sort_by([-.score, .name]) | .[0:$top])}'
