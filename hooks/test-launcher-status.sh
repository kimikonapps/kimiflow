#!/usr/bin/env bash
# kimiflow — unit tests for launcher-status.sh.
# Isolation: temp git repo under mktemp; the real repo is never touched.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/launcher-status.sh"
MEMORY_ROUTER="$(cd "$(dirname "$0")" && pwd)/memory-router.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
INDEX="$REPO/.kimiflow/project/INDEX.json"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_jq() {
  local json="$1" expr="$2" name="$3"
  if printf '%s\n' "$json" | jq -e "$expr" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi
}

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — launcher-status uses jq"; exit 0
fi

hash_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print "sha256:" $1}'
  else
    sha256sum "$1" | awk '{print "sha256:" $1}'
  fi
}

reset_repo() {
  rm -rf "$REPO"
  mkdir -p "$REPO/src" "$REPO/docs" "$REPO/.kimiflow/project"
  ( cd "$REPO" && git init -q && git config user.email "kimiflow@example.test" && git config user.name "kimiflow test" )
  printf '.kimiflow/\n' > "$REPO/.gitignore"
  printf 'one\n' > "$REPO/src/a.txt"
  printf '# Docs\n' > "$REPO/docs/guide.md"
  ( cd "$REPO" && git add .gitignore src/a.txt docs/guide.md && git commit -q -m init )
}

write_index() {
  local base="$1"
  local src_hash="$2"
  jq -n \
    --arg base "$base" \
    --arg src_hash "$src_hash" \
    '{
      schema_version: 1,
      language: "de",
      scan_depth: "standard",
      baseline_commit: $base,
      created_at: "2026-06-25T00:00:00Z",
      sections: {
        code: {
          files: ["src/a.txt"],
          prefixes: ["src/"],
          file_hashes: {"src/a.txt": $src_hash},
          last_scanned_commit: $base,
          status: "current"
        }
      },
      artifacts: {}
    }' > "$INDEX"
}

run_status() {
  "$SCRIPT" --root "$REPO"
}

reset_repo
rm -f "$INDEX"
out="$(run_status)"
assert_jq "$out" '.repo.present == true' "repo_present"
assert_jq "$out" '.project_map.present == false and .project_map.status == "missing"' "missing_map_reports_missing"

reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/src/a.txt")"
out="$(run_status)"
assert_jq "$out" '.project_map.present == true and .project_map.depth == "standard" and .project_map.status == "current"' "current_map_reports_current"
assert_jq "$out" '.repo.dirty == false' "ignored_kimiflow_does_not_dirty_repo"
assert_jq "$out" '.maintenance.bring_current_recommended == false and .maintenance.commits_since_project_map_baseline == 0' "clean_current_repo_no_maintenance_recommended"

printf '# Docs\nmore\n' > "$REPO/docs/guide.md"
( cd "$REPO" && git add docs/guide.md && git commit -q -m docs )
out="$(run_status)"
assert_jq "$out" '.project_map.status == "current" and .maintenance.bring_current_recommended == false and .maintenance.commits_since_project_map_baseline == 1' "baseline_commit_count_is_context_not_maintenance"

printf 'two\n' > "$REPO/src/a.txt"
out="$(run_status)"
assert_jq "$out" '.project_map.status == "stale" and .repo.dirty == true' "stale_map_and_dirty_repo_reported"
assert_jq "$out" '.maintenance.bring_current_recommended == true and (.maintenance.reasons | index("working_tree_dirty")) and (.maintenance.reasons | index("project_map_stale"))' "stale_dirty_repo_recommends_maintenance"

reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/src/a.txt")"
cat > "$REPO/.kimiflow/project/FINDINGS.md" <<'EOF'
# Findings

## Offen

### F-001
### F-002

## Erledigt

### F-000
EOF
cat > "$REPO/.kimiflow/project/IMPROVEMENTS.md" <<'EOF'
# Improvements

## Priorisierte Slices

### 1. First
### 2. Second
EOF
out="$(run_status)"
assert_jq "$out" '.findings.open == 2 and .improvements.open == 2' "findings_and_improvements_counted_de"

cat > "$REPO/.kimiflow/project/FINDINGS.md" <<'EOF'
# Findings

## Open

### F-001
### F-002

## Done

### F-000
EOF
cat > "$REPO/.kimiflow/project/IMPROVEMENTS.md" <<'EOF'
# Improvements

## Prioritized Slices

### 1. First
EOF
out="$(run_status)"
assert_jq "$out" '.findings.open == 2 and .improvements.open == 1' "findings_and_improvements_counted_en"

cat > "$REPO/.kimiflow/project/MEMORY.md" <<'EOF'
# Memory

Kimiflow loads small project memory before fresh code exploration.
EOF
cat > "$REPO/.kimiflow/project/LEARNINGS.jsonl" <<'EOF'
{"id":"learn_memory","kind":"process","scope":"project","topic":"memory","summary":"Launcher status exposes memory curation needs.","evidence":["hooks/launcher-status.sh:1"],"confidence":"high","sensitivity":"normal","last_verified":"2026-06-25","source_commit":"abc1234","status":"current"}
EOF
out="$(run_status)"
assert_jq "$out" '.memory.present == true and .memory.learnings.current == 1 and .memory.curation.recommended == true and (.maintenance.reasons | index("memory_curation_recommended"))' "memory_status_reports_index_missing_curation"

"$MEMORY_ROUTER" curate --root "$REPO" --write >/dev/null
out="$(run_status)"
assert_jq "$out" '.memory.curation.recommended == false and (.maintenance.reasons | index("memory_curation_recommended") | not)' "memory_index_clears_curation_recommendation"

awk 'BEGIN{for(i=0;i<950;i++) printf "word "}' > "$REPO/.kimiflow/project/MEMORY.md"
out="$(run_status)"
assert_jq "$out" '.memory.memory.over_budget == true and .memory.curation.recommended == true and (.maintenance.reasons | index("memory_curation_recommended"))' "memory_over_budget_surfaces_in_launcher"

mkdir -p "$REPO/.kimiflow/parked"
cat > "$REPO/.kimiflow/parked/STATE.md" <<EOF
# STATE

- **Status:** backlog
- **Mode:** feature
- **Scope:** small
Plan commit: $BASE
Affected files:
- src/a.txt
Plan status: approved
EOF
out="$(run_status)"
assert_jq "$out" '.runs.backlog == 1 and (.runs.items[] | select(.slug == "parked" and .stale_risk == "low"))' "backlog_run_low_risk_when_clean"

printf 'changed\n' > "$REPO/src/a.txt"
out="$(run_status)"
assert_jq "$out" '.runs.backlog == 1 and (.runs.items[] | select(.slug == "parked" and .stale_risk == "needs-revalidation"))' "backlog_run_needs_revalidation_when_affected_file_changed"

reset_repo
mkdir -p "$REPO/.kimiflow/legacy-active-done" "$REPO/.kimiflow/legacy-missing-done" "$REPO/.kimiflow/still-active" "$REPO/.kimiflow/not-done"
cat > "$REPO/.kimiflow/legacy-active-done/STATE.md" <<'EOF'
# STATE

- **Status:** active
- Phase 0: done
- Phase 7: done
EOF
cat > "$REPO/.kimiflow/legacy-missing-done/STATE.md" <<'EOF'
# STATE

Phase 0: done
Phase 7: **done**
EOF
cat > "$REPO/.kimiflow/still-active/STATE.md" <<'EOF'
# STATE

Phase 0: done
Phase 4: done
Phase 5: open
EOF
cat > "$REPO/.kimiflow/not-done/STATE.md" <<'EOF'
# STATE

- **Status:** active
- Phase 7: not done yet
EOF
out="$(run_status)"
assert_jq "$out" '.runs.done == 2 and .runs.active == 2 and (.runs.items[] | select(.slug == "legacy-active-done" and .status == "done")) and (.runs.items[] | select(.slug == "legacy-missing-done" and .status == "done")) and (.runs.items[] | select(.slug == "not-done" and .status == "active"))' "legacy_phase7_done_runs_inferred_done"
assert_jq "$out" '.maintenance.bring_current_recommended == true and (.maintenance.reasons | index("active_runs"))' "active_runs_recommend_maintenance"

reset_repo
printf '{bad json\n' > "$INDEX"
out="$(run_status)"
assert_jq "$out" '.project_map.present == true and .project_map.valid == false and .project_map.status == "unknown"' "invalid_map_reports_unknown"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
