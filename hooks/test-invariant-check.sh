#!/usr/bin/env bash
# kimiflow — fixture tests for the R2 invariant target-map checker.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/docs/superpowers/plans/2026-07-02-invariant-check.sh"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kimiflow-invariant-test.XXXXXX")" || exit 2
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

sha256_text() {
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
  else
    printf '%s' "$1" | sha256sum | awk '{print $1}'
  fi
}

plans_dir() {
  printf '%s/docs/superpowers/plans\n' "$1"
}

write_corpus_header() {
  mkdir -p "$(plans_dir "$1")"
  {
    printf '# fixture corpus\n'
    printf '# id\tsource\tstrong_needle\ttarget_constraint\tnotes\n'
  } > "$(plans_dir "$1")/2026-07-02-invariant-corpus.tsv"
}

write_targets_header() {
  mkdir -p "$(plans_dir "$1")"
  {
    printf '# fixture targets\n'
    printf '# id\tauthoritative_target\tverification_path\tnotes\n'
  } > "$(plans_dir "$1")/2026-07-02-invariant-targets.tsv"
}

add_corpus() {
  local root="$1" id="$2" needle="$3" constraint="$4" notes="$5"
  printf '%s\tfixture\t%s\t%s\t%s\n' "$id" "$needle" "$constraint" "$notes" \
    >> "$(plans_dir "$root")/2026-07-02-invariant-corpus.tsv"
}

add_target() {
  local root="$1" id="$2" target="$3" verify="$4" notes="$5"
  printf '%s\t%s\t%s\t%s\n' "$id" "$target" "$verify" "$notes" \
    >> "$(plans_dir "$root")/2026-07-02-invariant-targets.tsv"
}

make_fixture() {
  local root="$1" needle="${2:-critical irreversible action}"
  mkdir -p "$root" "$root/hooks"
  printf 'Driver text: %s\n' "$needle" > "$root/SKILL.md"
  printf 'Reference text: %s\n' "$needle" > "$root/reference.md"
  write_corpus_header "$root"
  write_targets_header "$root"
  add_corpus "$root" "INV-001" "$needle" "core-always-or-approved-target" "class=CORE-ALWAYS; current_target=SKILL.md"
  add_target "$root" "INV-001" "SKILL.md" "-" "needle_sha256=$(sha256_text "$needle"); note with spaces"
}

run_check() {
  OUT="$(bash "$SCRIPT" --root "$1" 2>&1)"
  RC=$?
}

assert_ok() {
  local root="$1" name="$2"
  run_check "$root"
  if [ "$RC" -eq 0 ]; then
    pass "$name"
  else
    fail "$name rc=$RC :: $OUT"
  fi
}

assert_fails_with() {
  local root="$1" needle="$2" name="$3"
  run_check "$root"
  if [ "$RC" -ne 0 ] && printf '%s\n' "$OUT" | grep -qF "$needle"; then
    pass "$name"
  else
    fail "$name rc=$RC expected '$needle' :: $OUT"
  fi
}

# exact-path target success + repository root override/temp fixture execution
F="$WORK/exact-success"
make_fixture "$F"
assert_ok "$F" "exact_path_target_success"

# target map row with notes containing spaces
assert_ok "$F" "target_notes_with_spaces"

# missing file failure
F="$WORK/missing-file"
make_fixture "$F"
write_targets_header "$F"
add_target "$F" "INV-001" "MISSING.md" "-" "needle_sha256=$(sha256_text "critical irreversible action"); note with spaces"
assert_fails_with "$F" "missing target file" "missing_file_failure"

# missing needle failure
F="$WORK/missing-needle"
make_fixture "$F"
printf 'Driver text without it\n' > "$F/SKILL.md"
assert_fails_with "$F" "missing needle in SKILL.md" "missing_needle_failure"

# missing corpus ID in target map failure
F="$WORK/missing-target-id"
make_fixture "$F"
add_corpus "$F" "INV-002" "second protected action" "core-always-or-approved-target" "class=CORE-ALWAYS; current_target=SKILL.md"
printf 'Second text: second protected action\n' >> "$F/SKILL.md"
assert_fails_with "$F" "target-map id missing: INV-002" "missing_corpus_id_in_target_map"

# extra target-map ID failure
F="$WORK/extra-target-id"
make_fixture "$F"
add_target "$F" "INV-999" "SKILL.md" "-" "needle_sha256=$(sha256_text "critical irreversible action"); extra row"
assert_fails_with "$F" "target-map id has no corpus row: INV-999" "extra_target_map_id"

# duplicate and empty ID failures
F="$WORK/duplicate-id"
make_fixture "$F"
add_corpus "$F" "INV-001" "critical irreversible action" "core-always-or-approved-target" "class=CORE-ALWAYS; duplicate"
assert_fails_with "$F" "duplicate id INV-001" "duplicate_id_failure"

F="$WORK/empty-id"
make_fixture "$F"
printf '\tfixture\tcritical irreversible action\tcore-always-or-approved-target\tclass=CORE-ALWAYS\n' \
  >> "$(plans_dir "$F")/2026-07-02-invariant-corpus.tsv"
assert_fails_with "$F" "empty id" "empty_id_failure"

# empty target and empty needle failures
F="$WORK/empty-target"
make_fixture "$F"
write_targets_header "$F"
printf 'INV-001\t\t-\tneedle_sha256=%s; empty target\n' "$(sha256_text "critical irreversible action")" \
  >> "$(plans_dir "$F")/2026-07-02-invariant-targets.tsv"
assert_fails_with "$F" "empty authoritative_target" "empty_target_failure"

F="$WORK/empty-needle"
make_fixture "$F"
write_corpus_header "$F"
printf 'INV-001\tfixture\t\tcore-always-or-approved-target\tclass=CORE-ALWAYS; current_target=SKILL.md\n' \
  >> "$(plans_dir "$F")/2026-07-02-invariant-corpus.tsv"
assert_fails_with "$F" "empty strong_needle" "empty_needle_failure"

# test-only target rejected for runtime/prohibition rule
F="$WORK/test-only-target"
make_fixture "$F"
printf 'Driver text: critical irreversible action\n' > "$F/hooks/test-fixture.sh"
write_targets_header "$F"
add_target "$F" "INV-001" "hooks/test-fixture.sh" "-" "needle_sha256=$(sha256_text "critical irreversible action"); test target"
assert_fails_with "$F" "test-only authoritative target rejected" "test_only_target_rejected"

# attempted target-map needle override rejected
F="$WORK/needle-override"
make_fixture "$F"
printf 'INV-001\tSKILL.md\t-\tnotes\tcritical\n' \
  >> "$(plans_dir "$F")/2026-07-02-invariant-targets.tsv"
assert_fails_with "$F" "target map must not define or override needles" "target_map_needle_override_rejected"

# corpus strong-needle weakening caught even when target ID remains
F="$WORK/corpus-weakening"
make_fixture "$F"
write_corpus_header "$F"
add_corpus "$F" "INV-001" "critical" "core-always-or-approved-target" "class=CORE-ALWAYS; current_target=SKILL.md"
assert_fails_with "$F" "corpus strong_needle hash mismatch" "corpus_weakening_caught"

printf -- '----\n'
if [ "$FAILS" -eq 0 ]; then
  echo "invariant-check tests: PASS"
  exit 0
else
  echo "invariant-check tests: $FAILS FAIL"
  exit 1
fi
