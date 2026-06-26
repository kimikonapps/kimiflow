#!/usr/bin/env bash
# kimiflow — unit tests for launcher-status.sh.
# Isolation: temp git repo under mktemp; the real repo is never touched.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/launcher-status.sh"
MEMORY_ROUTER="$(cd "$(dirname "$0")" && pwd)/memory-router.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
INDEX="$REPO/.kimiflow/project/INDEX.json"
export KIMIFLOW_HOME="$WORK/home"
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
  rm -rf "$KIMIFLOW_HOME"
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
cat > "$REPO/.kimiflow/project/RUN-HISTORY.json" <<'EOF'
{"schema_version":1,"status":"written","hits":[]}
EOF
cat > "$REPO/.kimiflow/project/MEMORY-USAGE.json" <<'EOF'
{"schema_version":1,"updated_at":"2026-06-25T00:00:00Z","items":{"learning:learn_memory":{"kind":"learning","use_count":2,"last_used_at":"2026-06-25T00:00:00Z"}}}
EOF
cat > "$REPO/.kimiflow/project/VAULT-PROVIDER.json" <<'EOF'
{"schema_version":1,"type":"obsidian","available":true,"mode":"local-first","vault_path":"","last_prefetch_at":"2026-06-25T00:00:00Z","last_write_at":null,"updated_at":"2026-06-25T00:00:00Z"}
EOF
mkdir -p "$KIMIFLOW_HOME/metrics"
cat > "$KIMIFLOW_HOME/metrics/token-economics.jsonl" <<'EOF'
{"schema_version":1,"recorded_day":"2026-06-25","host":"codex","run_type":"feature","project_size_bucket":"small","project_id":"anon_project","run_id":"anon_run","always_on_tokens":100,"user_memory_tokens":0,"recall_tokens":100,"recall_hit_count":3,"used_hit_count":1,"estimated_avoided_scan_tokens":1200,"net_estimated_tokens_saved":1000,"estimated_savings_percent":83,"result":"saving","confidence":"medium","basis":{"heuristic":"directional_estimate_only","stores_content":false,"stores_paths":false,"local_only":true}}
EOF
out="$(run_status)"
assert_jq "$out" '.memory.present == true and .memory.learnings.current == 1 and .memory.curation.recommended == true and (.maintenance.reasons | index("memory_curation_recommended"))' "memory_status_reports_index_missing_curation"
assert_jq "$out" '.memory.history.present == true and .memory.usage.total_uses == 2 and .memory.provider.available == true and .memory.vault.available == true' "launcher_surfaces_history_usage_provider"
assert_jq "$out" '.efficiency.present == true and .efficiency.runs_tracked == 1 and .efficiency.estimated_savings_percent == 83 and .efficiency.confidence == "low" and .efficiency.privacy.stores_paths == false and .memory.global_efficiency.runs_tracked == 1' "launcher_surfaces_global_efficiency"

"$MEMORY_ROUTER" curate --root "$REPO" --write >/dev/null
out="$(run_status)"
assert_jq "$out" '.memory.curation.recommended == false and (.maintenance.reasons | index("memory_curation_recommended") | not)' "memory_index_clears_curation_recommendation"
cat >> "$REPO/.kimiflow/project/LEARNINGS.jsonl" <<'EOF'
{"id":"learn_many_one","kind":"process","scope":"project","topic":"memory","summary":"Additional healthy learning should not surface as user maintenance.","evidence":["hooks/launcher-status.sh:1"],"confidence":"high","sensitivity":"normal","last_verified":"2026-06-25","source_commit":"abc1234","status":"current"}
{"id":"learn_many_two","kind":"process","scope":"project","topic":"memory","summary":"Healthy many-learnings threshold remains an internal signal only.","evidence":["hooks/launcher-status.sh:1"],"confidence":"high","sensitivity":"normal","last_verified":"2026-06-25","source_commit":"abc1234","status":"current"}
EOF
"$MEMORY_ROUTER" curate --root "$REPO" --write >/dev/null
out="$(KIMIFLOW_OBSIDIAN_URL=http://127.0.0.1:1 KIMIFLOW_MEMORY_CURATE_AFTER_LEARNINGS=3 run_status)"
assert_jq "$out" '.memory.curation.recommended == false and .memory.curation.internal_recommended == true and (.memory.curation.silent_reasons | index("many_learnings")) and .maintenance.bring_current_recommended == false and (.maintenance.reasons | index("memory_curation_recommended") | not)' "launcher_hides_benign_many_learnings_signal"

cat > "$REPO/.kimiflow/project/PROPOSALS.jsonl" <<'EOF'
{"id":"learn_memory","learning_id":"learn_memory","type":"standard","kind":"project_rule_confirmed","target_path":".kimiflow/STANDARDS.md","summary":"Project rule confirmed: launcher status exposes pending learning proposals.","evidence":["hooks/launcher-status.sh:1"],"status":"pending","created_at":"2026-06-25T00:00:00Z","updated_at":"2026-06-25T00:00:00Z"}
EOF
out="$(run_status)"
assert_jq "$out" '.memory.proposals.pending == 1 and (.maintenance.reasons | index("learning_proposals_pending"))' "pending_learning_proposals_surface_in_launcher"
perl -0pi -e 's/"status":"pending"/"status":"approved"/' "$REPO/.kimiflow/project/PROPOSALS.jsonl"
out="$(run_status)"
assert_jq "$out" '.memory.proposals.approved == 1 and (.maintenance.reasons | index("learning_proposals_approved"))' "approved_learning_proposals_surface_in_launcher"
rm "$REPO/.kimiflow/project/PROPOSALS.jsonl"

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
mkdir -p "$REPO/.kimiflow/legacy-active-done" "$REPO/.kimiflow/legacy-missing-done" "$REPO/.kimiflow/stale-review-done" "$REPO/.kimiflow/still-active" "$REPO/.kimiflow/not-done"
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
cat > "$REPO/.kimiflow/stale-review-done/STATE.md" <<'EOF'
# STATE

- **Status:** done
EOF
cat > "$REPO/.kimiflow/stale-review-done/LEARNING-REVIEW.md" <<'EOF'
# Learning Review

Run: .kimiflow/stale-review-done
Status: recorded
Generated: 2026-06-25T00:00:00Z

Recorded: learn_missing
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
assert_jq "$out" '.runs.done == 3 and .runs.active == 2 and (.runs.items[] | select(.slug == "legacy-active-done" and .status == "done")) and (.runs.items[] | select(.slug == "legacy-missing-done" and .status == "done")) and (.runs.items[] | select(.slug == "not-done" and .status == "active"))' "legacy_phase7_done_runs_inferred_done"
assert_jq "$out" '.runs.learning_reviews.missing_done == 2 and (.runs.items[] | select(.slug == "legacy-active-done" and .learning_review.verdict == "CLOSED" and .learning_review.reason == "missing_review"))' "done_runs_without_learning_review_surface_without_legacy_noise"
assert_jq "$out" '.runs.learning_reviews.needs_attention == 1 and (.maintenance.reasons | index("learning_reviews_need_attention")) and (.runs.items[] | select(.slug == "stale-review-done" and .learning_review.verdict == "CLOSED" and .learning_review.reason == "missing_learnings"))' "stale_existing_learning_review_recommends_attention"
assert_jq "$out" '.maintenance.bring_current_recommended == true and (.maintenance.reasons | index("active_runs"))' "active_runs_recommend_maintenance"

reset_repo
printf '{bad json\n' > "$INDEX"
out="$(run_status)"
assert_jq "$out" '.project_map.present == true and .project_map.valid == false and .project_map.status == "unknown"' "invalid_map_reports_unknown"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
