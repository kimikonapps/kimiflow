#!/usr/bin/env bash
# kimiflow — test-weakening scan (ADVISORY, never blocks). Invoked by Kimiflow at
# Phase-5 checkpoint and Phase-7 commit boundaries (NOT auto-registered as a hook).
# Scans the staged diff for signs that tests were weakened to go green and prints FLAG
# advisory lines to stdout. Kimiflow routes these to .kimiflow/<slug>/ADVISORIES.md
# for evidence-based triage (dismiss = legit refactor, or promote = a real finding).
#
# The pattern set is a MINIMUM (see reference.md → "Review rubric"): SEMANTIC
# weakening — changed expected values, loosened tolerances, a test rewritten to a
# no-op without a marker — is NOT detected. Surface, do not trust as complete.
set -u

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$root" ] || exit 0
cd "$root" 2>/dev/null || exit 0

testish='(test|spec|__tests__|_test\.|\.test\.|\.spec\.)'

# 1) Deleted test files.
git diff --cached --name-status --diff-filter=D 2>/dev/null | while IFS=$'\t' read -r _ path; do
  printf '%s' "$path" | grep -qiE "$testish" && printf -- '- [FLAG] %s — deleted test file\n' "$path"
done

# 2) Added skip/disable markers (any staged file) and 3) removed assertions (test files).
# NOTE: POSIX/BSD awk has no \b word boundary — markers are matched without it.
# Removed-assertion flags are buffered per hunk and suppressed if the same hunk also
# ADDS a skip/only marker (a skip-rewrite is one change, not "removed assert + added skip").
git diff --cached --diff-filter=ACMR -U0 2>/dev/null | awk '
  function flush() { if (!skip_added) { for (i = 1; i <= nb; i++) print buf[i] }; nb = 0; skip_added = 0 }
  /^\+\+\+ b\// { file = substr($0, 7); next }
  /^--- a\//   { next }
  /^@@/        { flush(); next }
  # Added skip / only / disable markers.
  /^\+/ && /(\.skip|\.only|[^A-Za-z](xit|xdescribe)([^A-Za-z]|$)|@Disabled|@Ignore|@unittest\.skip|@pytest\.mark\.skip|[^A-Za-z]t\.Skip\(|assumeTrue\(false\))/ {
    skip_added = 1; s = $0; sub(/^\+/, "", s); printf "- [FLAG] %s — added skip/disable marker: %s\n", file, substr(s, 1, 90); next
  }
  # Removed assertion inside a test file (buffered; flushed unless this hunk added a skip).
  /^-/ && /(assert|expect|assertEquals|assertTrue|assertThat|EXPECT_|ASSERT_)/ {
    if (file ~ /(test|spec|__tests__|_test\.|\.test\.|\.spec\.)/) {
      s = $0; sub(/^-/, "", s); buf[++nb] = sprintf("- [FLAG] %s — removed assertion: %s", file, substr(s, 1, 90))
    }
  }
  END { flush() }
'
exit 0
