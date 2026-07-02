#!/usr/bin/env bash
# kimiflow — unit tests for project-map-status.sh.
# Isolation: temp git repo under mktemp; the real repo is never touched.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/project-map-status.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
INDEX="$REPO/.kimiflow/project/INDEX.json"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_has() { case "$1" in *"$2"*) pass "$3" ;; *) fail "$3 (missing '$2' in: $1)" ;; esac; }
assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1' want '$2')"; fi; }

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — project-map-status uses jq"; exit 0
fi

hash_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print "sha256:" $1}'
  else
    sha256sum "$1" | awk '{print "sha256:" $1}'
  fi
}

file_mode() {
  case "$(uname -s)" in
    Darwin|FreeBSD) stat -f %Lp "$1" ;;
    *) stat -c %a "$1" ;;
  esac
}

reset_repo() {
  rm -rf "$REPO"
  mkdir -p "$REPO/hooks" "$REPO/docs" "$REPO/.kimiflow/project"
  ( cd "$REPO" && git init -q && git config user.email "kimiflow@example.test" && git config user.name "kimiflow test" )
  printf 'one\n' > "$REPO/hooks/a.sh"
  printf 'guide\n' > "$REPO/docs/guide.md"
  ( cd "$REPO" && git add hooks/a.sh docs/guide.md && git commit -q -m init )
}

write_index() {
  local base="$1"
  local hook_hash="$2"
  local docs_hash="$3"
  jq -n \
    --arg base "$base" \
    --arg hook_hash "$hook_hash" \
    --arg docs_hash "$docs_hash" \
    '{
      schema_version: 1,
      language: "de",
      scan_depth: "standard",
      baseline_commit: $base,
      created_at: "2026-06-25T00:00:00Z",
      sections: {
        hooks: {
          files: ["hooks/a.sh"],
          prefixes: ["hooks/"],
          file_hashes: {"hooks/a.sh": $hook_hash},
          last_scanned_commit: $base,
          status: "current"
        },
        docs: {
          files: ["docs/guide.md"],
          prefixes: ["docs/"],
          file_hashes: {"docs/guide.md": $docs_hash},
          last_scanned_commit: $base,
          status: "stale"
        },
        tech: {
          files: ["package.json"],
          prefixes: ["."],
          file_hashes: {},
          last_scanned_commit: $base,
          status: "current"
        }
      },
      artifacts: {}
    }' > "$INDEX"
}

run_status() {
  ( cd "$REPO" && "$SCRIPT" status "$@" )
}

run_refresh() {
  ( cd "$REPO" && "$SCRIPT" refresh "$@" )
}

run_coverage() {
  ( cd "$REPO" && "$SCRIPT" coverage "$@" )
}

run_index_symbols() {
  ( cd "$REPO" && "$SCRIPT" index-symbols "$@" )
}

# missing index
reset_repo
rm -f "$INDEX"
out="$(run_status)"
assert_has "$out" $'PROJECT_MAP\tmissing' "missing_index_reports_missing"
out="$(run_coverage --affected hooks/a.sh)"
assert_has "$out" $'PROJECT_MAP_COVERAGE\tmissing' "missing_index_coverage_reports_missing"
assert_has "$out" 'phase2_depth=full' "missing_index_coverage_uses_full_depth"

# syntactically valid but structurally invalid index
reset_repo
printf 'null\n' > "$INDEX"
out="$(run_status)"
assert_has "$out" $'PROJECT_MAP\tunknown' "scalar_index_reports_unknown"
out="$(run_coverage --affected hooks/a.sh)"
assert_has "$out" $'PROJECT_MAP_COVERAGE\tunknown' "scalar_index_coverage_reports_unknown"
assert_has "$out" 'reason=invalid-index' "scalar_index_coverage_reports_invalid_reason"

# current section from matching hashes
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
out="$(run_status)"
assert_has "$out" $'SECTION\thooks\tcurrent' "matching_hash_reports_current"
out="$(run_coverage --affected hooks/a.sh)"
assert_has "$out" $'PROJECT_MAP_COVERAGE\tcovered' "coverage_reports_current_affected_path_covered"
assert_has "$out" 'phase2_depth=compressed' "coverage_current_path_uses_compressed_phase2"
tmp_index="$(mktemp)"
jq '.sections.empty = {status: "current"}' "$INDEX" > "$tmp_index" && mv "$tmp_index" "$INDEX"
out="$(run_coverage --affected hooks/a.sh)"
assert_has "$out" $'PROJECT_MAP_COVERAGE\tcovered' "coverage_ignores_unrelated_unknown_section"
assert_has "$out" 'phase2_depth=compressed' "coverage_unrelated_unknown_keeps_compressed_phase2"

# exact hash mismatch marks that section stale
printf 'two\n' > "$REPO/hooks/a.sh"
out="$(run_status --affected hooks/a.sh)"
assert_has "$out" $'PROJECT_MAP\tpartially_stale' "hash_mismatch_makes_map_partially_stale"
assert_has "$out" $'SECTION\thooks\tstale\taffected=yes\treason=hash-mismatch' "hash_mismatch_section_stale"
assert_has "$out" 'affected_stale=1' "affected_stale_counted"
out="$(run_coverage --affected hooks/a.sh)"
assert_has "$out" $'PROJECT_MAP_COVERAGE\tstale' "coverage_marks_stale_affected_path"
assert_has "$out" 'phase2_depth=targeted' "coverage_stale_path_uses_targeted_phase2"

# new file under a known prefix is only potentially stale
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
printf 'new\n' > "$REPO/hooks/new.sh"
out="$(run_status)"
assert_has "$out" $'SECTION\thooks\tpotentially_stale' "new_file_under_prefix_potentially_stale"
out="$(run_coverage --affected outside/new.txt)"
assert_has "$out" $'PROJECT_MAP_COVERAGE\tpartial' "coverage_marks_unmapped_affected_path"
assert_has "$out" 'phase2_depth=full' "coverage_unmapped_path_uses_full_phase2"

# manifest/build config change fans out to stack-ish sections
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
printf '{"scripts":{"test":"true"}}\n' > "$REPO/package.json"
out="$(run_status)"
assert_has "$out" $'SECTION\ttech\tpotentially_stale' "manifest_change_marks_stackish_section_potentially_stale"

# refresh updates only selected sections
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
printf 'two\n' > "$REPO/hooks/a.sh"
out="$(run_refresh --section hooks)"
assert_has "$out" $'REFRESHED\thooks\tfiles=1' "refresh_reports_selected_section"
assert_eq "$(jq -r '.sections.hooks.status' "$INDEX")" "current" "refresh_marks_selected_section_current"
assert_eq "$(jq -r '.sections.docs.status' "$INDEX")" "stale" "refresh_leaves_other_section_status_alone"
assert_eq "$(jq -r '.sections.hooks.file_hashes["hooks/a.sh"]' "$INDEX")" "$(hash_file "$REPO/hooks/a.sh")" "refresh_updates_hash"
assert_eq "$(file_mode "$INDEX")" "600" "refresh_installs_index_mode_600"

# R1 write-safety — no success line if the atomic install cannot create its temp file.
if [ "$(id -u)" = "0" ]; then
  pass "refresh_write_failure_no_success_skipped_as_root"
else
  reset_repo
  BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
  write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
  chmod 500 "$REPO/.kimiflow/project"
  out="$(run_refresh --section hooks 2>&1)"; rc=$?
  chmod 700 "$REPO/.kimiflow/project"
  assert_eq "$rc" "1" "refresh_write_failure_exits_nonzero"
  assert_has "$out" 'cannot install' "refresh_write_failure_reports_install_error"
  case "$out" in *$'REFRESHED\t'*) fail "refresh_write_failure_does_not_print_success" ;; *) pass "refresh_write_failure_does_not_print_success" ;; esac
fi

# no section files/hashes is unknown, not silently current
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
jq -n --arg base "$BASE" '{
  schema_version: 1,
  language: "de",
  scan_depth: "standard",
  baseline_commit: $base,
  created_at: "2026-06-25T00:00:00Z",
  sections: {empty: {status: "current"}},
  artifacts: {}
}' > "$INDEX"
out="$(run_status)"
assert_has "$out" $'SECTION\tempty\tunknown' "empty_section_unknown"

reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
tmp_index="$(mktemp)"
jq '.sections.mystery = {prefixes: ["mystery/"], last_scanned_commit: "NOT VERIFIED", status: "current"}' "$INDEX" > "$tmp_index" && mv "$tmp_index" "$INDEX"
out="$(run_coverage --affected mystery/new.txt)"
assert_has "$out" $'PROJECT_MAP_COVERAGE\tunknown' "coverage_marks_affected_unknown_section"
assert_has "$out" 'affected_unknown=1' "coverage_counts_affected_unknown_section"
assert_has "$out" 'phase2_depth=targeted' "coverage_affected_unknown_uses_targeted_phase2"

# A1 — refresh --changed restamps only the section whose file changed
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
printf 'two\n' > "$REPO/hooks/a.sh"
out="$(run_refresh --changed)"
assert_has "$out" $'REFRESHED\thooks\t' "refresh_changed_reports_affected_section"
assert_eq "$(jq -r '.sections.hooks.status' "$INDEX")" "current" "refresh_changed_restamps_only_affected"
assert_eq "$(jq -r '.sections.hooks.file_hashes["hooks/a.sh"]' "$INDEX")" "$(hash_file "$REPO/hooks/a.sh")" "refresh_changed_updates_affected_hash"
assert_eq "$(jq -r '.sections.docs.status' "$INDEX")" "stale" "refresh_changed_leaves_unaffected_section_alone"

# A1 — section matched purely by .files membership (genuinely prefix-less member)
reset_repo
printf 'root\n' > "$REPO/foo.md"
( cd "$REPO" && git add foo.md && git commit -q -m foo )
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
jq -n --arg base "$BASE" --arg fh "$(hash_file "$REPO/foo.md")" '{
  schema_version: 1, language: "de", scan_depth: "standard", baseline_commit: $base,
  created_at: "2026-06-25T00:00:00Z",
  sections: {
    rootdoc: {files: ["foo.md"], prefixes: ["docs/"], file_hashes: {"foo.md": $fh}, last_scanned_commit: $base, status: "stale"}
  },
  artifacts: {}
}' > "$INDEX"
printf 'changed\n' > "$REPO/foo.md"
out="$(run_refresh --changed)"
assert_eq "$(jq -r '.sections.rootdoc.status' "$INDEX")" "current" "refresh_changed_restamps_section_by_files_membership"
assert_eq "$(jq -r '.sections.rootdoc.file_hashes["foo.md"]' "$INDEX")" "$(hash_file "$REPO/foo.md")" "refresh_changed_membership_updates_hash"

# A1 — new file under a section prefix is adopted; longest prefix wins
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
mkdir -p "$REPO/hooks/deep"
jq -n --arg base "$BASE" --arg ah "$(hash_file "$REPO/hooks/a.sh")" '{
  schema_version: 1, language: "de", scan_depth: "standard", baseline_commit: $base,
  created_at: "2026-06-25T00:00:00Z",
  sections: {
    hooks: {files: ["hooks/a.sh"], prefixes: ["hooks/"], file_hashes: {"hooks/a.sh": $ah}, last_scanned_commit: $base, status: "current"},
    hooksdeep: {files: [], prefixes: ["hooks/deep/"], file_hashes: {}, last_scanned_commit: $base, status: "current"}
  },
  artifacts: {}
}' > "$INDEX"
printf 'x\n' > "$REPO/hooks/deep/x.sh"
out="$(run_refresh --changed)"
assert_has "$out" $'NEW-FILE\thooksdeep\thooks/deep/x.sh' "refresh_changed_adds_new_file_by_prefix"
assert_eq "$(jq -r '.sections.hooksdeep.files | index("hooks/deep/x.sh")' "$INDEX")" "0" "refresh_changed_new_file_added_to_files"
assert_eq "$(jq -r '.sections.hooksdeep.file_hashes["hooks/deep/x.sh"]' "$INDEX")" "$(hash_file "$REPO/hooks/deep/x.sh")" "refresh_changed_new_file_hashed"
assert_eq "$(jq -r '.sections.hooks.files | index("hooks/deep/x.sh")' "$INDEX")" "null" "refresh_changed_new_file_not_in_shorter_prefix"

# A1 — clean tree → no mutation, exit 0
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
before="$(cat "$INDEX")"
out="$(run_refresh --changed)"; rc=$?
after="$(cat "$INDEX")"
assert_eq "$after" "$before" "refresh_changed_noop_when_clean"
assert_eq "$rc" "0" "refresh_changed_noop_exit0"

# A1 — a COMMITTED change is absorbed once; baseline advances so a re-run is a true no-op (idempotent)
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/hooks/a.sh")" "$(hash_file "$REPO/docs/guide.md")"
printf 'changed-and-committed\n' > "$REPO/hooks/a.sh"
( cd "$REPO" && git add hooks/a.sh && git commit -q -m change )
HEAD2="$(cd "$REPO" && git rev-parse --short HEAD)"
out1="$(run_refresh --changed)"
assert_has "$out1" $'REFRESHED\thooks\t' "refresh_changed_absorbs_committed_change"
assert_eq "$(jq -r '.baseline_commit' "$INDEX")" "$HEAD2" "refresh_changed_advances_baseline_to_head"
before2="$(cat "$INDEX")"
out2="$(run_refresh --changed)"; rc2=$?
after2="$(cat "$INDEX")"
assert_eq "$out2" "" "refresh_changed_idempotent_after_commit"
assert_eq "$after2" "$before2" "refresh_changed_idempotent_no_mutation"
assert_eq "$rc2" "0" "refresh_changed_idempotent_exit0"

# A1/AC-16 — deleted member is pruned, remaining member kept, section reports current
reset_repo
printf 'bee\n' > "$REPO/hooks/b.sh"
( cd "$REPO" && git add hooks/b.sh && git commit -q -m addb )
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
jq -n --arg base "$BASE" --arg ah "$(hash_file "$REPO/hooks/a.sh")" --arg bh "$(hash_file "$REPO/hooks/b.sh")" '{
  schema_version: 1, language: "de", scan_depth: "standard", baseline_commit: $base,
  created_at: "2026-06-25T00:00:00Z",
  sections: {
    multi: {files: ["hooks/a.sh", "hooks/b.sh"], prefixes: ["hooks/"], file_hashes: {"hooks/a.sh": $ah, "hooks/b.sh": $bh}, last_scanned_commit: $base, status: "current"}
  },
  artifacts: {}
}' > "$INDEX"
( cd "$REPO" && git rm -q hooks/b.sh && git commit -q -m rmb )
out="$(run_refresh --changed)"
assert_eq "$(jq -r '.sections.multi.files | index("hooks/b.sh")' "$INDEX")" "null" "refresh_changed_prunes_deleted_file"
assert_eq "$(jq -r '.sections.multi.file_hashes["hooks/b.sh"] // "gone"' "$INDEX")" "gone" "refresh_changed_prunes_deleted_hash"
assert_eq "$(jq -r '.sections.multi.files | index("hooks/a.sh")' "$INDEX")" "0" "refresh_changed_keeps_remaining_file"
out="$(run_status)"
assert_has "$out" $'SECTION\tmulti\tcurrent' "refresh_changed_pruned_section_status_current"

# B1 — index-symbols extracts shell functions, skips comment lines
reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
printf '#!/usr/bin/env bash\nfoo() {\n  echo hi\n}\n# bar() {\n#   echo no\n# }\nbaz() {\n  echo b\n}\n' > "$REPO/hooks/sym.sh"
jq -n --arg base "$BASE" --arg sh "$(hash_file "$REPO/hooks/sym.sh")" '{
  schema_version: 1, language: "de", scan_depth: "standard", baseline_commit: $base,
  created_at: "2026-06-25T00:00:00Z",
  sections: {
    syms: {files: ["hooks/sym.sh"], prefixes: ["hooks/"], file_hashes: {"hooks/sym.sh": $sh}, last_scanned_commit: $base, status: "current"}
  },
  artifacts: {}
}' > "$INDEX"
out="$(run_index_symbols --section syms)"
assert_eq "$(jq -r '.sections.syms.symbols.foo' "$INDEX")" "hooks/sym.sh:2" "index_symbols_populates_and_skips_comments"
assert_eq "$(jq -r '.sections.syms.symbols.bar // "none"' "$INDEX")" "none" "index_symbols_skips_comment_function"
assert_eq "$(jq -r '.sections.syms.symbols.baz' "$INDEX")" "hooks/sym.sh:8" "index_symbols_records_second_function"
assert_eq "$(jq -r '.schema_version' "$INDEX")" "1" "index_symbols_keeps_schema_version"
assert_eq "$(file_mode "$INDEX")" "600" "index_symbols_installs_index_mode_600"

if [ "$(id -u)" = "0" ]; then
  pass "index_symbols_write_failure_no_success_skipped_as_root"
else
  chmod 500 "$REPO/.kimiflow/project"
  out="$(run_index_symbols --section syms 2>&1)"; rc=$?
  chmod 700 "$REPO/.kimiflow/project"
  assert_eq "$rc" "1" "index_symbols_write_failure_exits_nonzero"
  assert_has "$out" 'cannot install' "index_symbols_write_failure_reports_install_error"
  case "$out" in *$'SYMBOLS\t'*) fail "index_symbols_write_failure_does_not_print_success" ;; *) pass "index_symbols_write_failure_does_not_print_success" ;; esac
fi

# B1 wiring — refresh --changed re-indexes symbols of a refreshed .sh section
reset_repo
printf '#!/usr/bin/env bash\nfoo() {\n  echo hi\n}\n' > "$REPO/hooks/sym2.sh"
( cd "$REPO" && git add hooks/sym2.sh && git commit -q -m sym2 )
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
jq -n --arg base "$BASE" --arg sh "$(hash_file "$REPO/hooks/sym2.sh")" '{
  schema_version: 1, language: "de", scan_depth: "standard", baseline_commit: $base,
  created_at: "2026-06-25T00:00:00Z",
  sections: {
    syms2: {files: ["hooks/sym2.sh"], prefixes: ["hooks/"], file_hashes: {"hooks/sym2.sh": $sh}, last_scanned_commit: $base, status: "current"}
  },
  artifacts: {}
}' > "$INDEX"
printf '#!/usr/bin/env bash\nfoo() {\n  echo hi\n}\nqux() {\n  echo q\n}\n' > "$REPO/hooks/sym2.sh"
out="$(run_refresh --changed)"
assert_eq "$(jq -r '.sections.syms2.symbols.qux' "$INDEX")" "hooks/sym2.sh:5" "refresh_changed_reindexes_symbols"
assert_eq "$(jq -r '.sections.syms2.symbols.foo' "$INDEX")" "hooks/sym2.sh:2" "refresh_changed_reindex_keeps_existing_symbol"

# Regression — many new files under a prefix must not exhaust subshells (bash 3.2 SIGTRAP).
# do_refresh_changed used to recompute section_prefixes_effective per (changed-path × section),
# spawning O(paths × sections) process substitutions; on large deltas macOS bash 3.2 died with
# SIGTRAP (exit 133) mid-adoption. Many sections × many new files reproduces it.
reset_repo
mkdir -p "$REPO/bulk"
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
jq -n --arg base "$BASE" --arg ah "$(hash_file "$REPO/hooks/a.sh")" '{
  schema_version: 1, language: "de", scan_depth: "standard", baseline_commit: $base,
  created_at: "2026-06-25T00:00:00Z",
  sections: (
    {
      hooks: {files: ["hooks/a.sh"], prefixes: ["hooks/"], file_hashes: {"hooks/a.sh": $ah}, last_scanned_commit: $base, status: "current"},
      bulk:  {files: [], prefixes: ["bulk/"], file_hashes: {}, last_scanned_commit: $base, status: "current"}
    }
    + (reduce range(0;7) as $i ({}; . + {("extra\($i)"): {files: [], prefixes: ["extra\($i)/"], file_hashes: {}, last_scanned_commit: $base, status: "current"}}))
  ),
  artifacts: {}
}' > "$INDEX"
i=0; while [ "$i" -lt 60 ]; do printf 'n\n' > "$REPO/bulk/f_$i.md"; i=$((i + 1)); done
out="$(run_refresh --changed)"; rc=$?
assert_eq "$rc" "0" "refresh_changed_many_new_files_no_subshell_crash"
assert_eq "$(jq -r '.sections.bulk.files | length' "$INDEX")" "60" "refresh_changed_adopts_all_new_files"
assert_eq "$(jq -r '.sections.bulk.files | (index("bulk/f_59.md") != null)' "$INDEX")" "true" "refresh_changed_adopts_last_new_file"
assert_eq "$(jq -r '.sections.bulk.file_hashes["bulk/f_0.md"]' "$INDEX")" "$(hash_file "$REPO/bulk/f_0.md")" "refresh_changed_hashes_new_files"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
