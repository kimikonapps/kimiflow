#!/usr/bin/env bash
# kimiflow — unit tests for suggest-affected-sections.sh.
# Isolation: temp dir under mktemp; the real repo is never touched.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/suggest-affected-sections.sh"
WORK="$(mktemp -d)"
IDX="$WORK/INDEX.json"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1' want '$2')"; fi; }
assert_gt() { if [ "$1" -gt "$2" ] 2>/dev/null; then pass "$3"; else fail "$3 (got '$1' not > '$2')"; fi; }

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — suggest-affected-sections uses jq"; exit 0
fi

# AC-5 — relevant section ranks first and carries a non-empty paths array
jq -n '{
  schema_version: 1, language: "de", scan_depth: "standard",
  sections: {
    alpha: {
      files: ["src/token.sh"],
      prefixes: ["src/"],
      symbols: {"parse_token": "src/token.sh:5"},
      file_hashes: {"src/token.sh": "sha256:x"},
      status: "current"
    },
    beta: {
      files: ["src/render.sh"],
      prefixes: ["lib/"],
      symbols: {"render": "src/render.sh:3"},
      file_hashes: {"src/render.sh": "sha256:y"},
      status: "current"
    }
  },
  artifacts: {}
}' > "$IDX"

out="$("$SCRIPT" --text "token kaputt" --index "$IDX")"
first="$(printf '%s' "$out" | jq -r '.sections[0].name')"
assert_eq "$first" "alpha" "suggest_ranks_relevant_section_first_with_paths"
plen="$(printf '%s' "$out" | jq -r '.sections[0].paths | length')"
assert_gt "$plen" "0" "suggest_top_section_has_nonempty_paths"
betascore="$(printf '%s' "$out" | jq -r '[.sections[] | select(.name == "beta")] | length')"
assert_eq "$betascore" "0" "suggest_drops_zero_score_section"

# AC-6 — missing index → empty
out="$("$SCRIPT" --text "token" --index "$WORK/nope.json")"
assert_eq "$out" '{"sections":[]}' "suggest_empty_when_no_index"

# AC-6 — present index but no keyword match → empty
out="$("$SCRIPT" --text "zzz unrelated nomatchterm" --index "$IDX")"
assert_eq "$out" '{"sections":[]}' "suggest_empty_when_no_match"

# --intent <file> path is read as the term source
printf 'fix the token parser\n' > "$WORK/INTENT.md"
out="$("$SCRIPT" --intent "$WORK/INTENT.md" --index "$IDX")"
assert_eq "$(printf '%s' "$out" | jq -r '.sections[0].name')" "alpha" "suggest_reads_intent_file"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
