#!/usr/bin/env bash
# kimiflow — unit tests for launcher-status.sh.
# Isolation: temp git repo under mktemp; the real repo is never touched.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/launcher-status.sh"
MEMORY_ROUTER="$(cd "$(dirname "$0")" && pwd)/memory-router.sh"
ACTIVE_RUN="$(cd "$(dirname "$0")" && pwd)/active-run.sh"
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
assert_max_bytes() {
  local data="$1" max="$2" name="$3" bytes
  bytes="$(printf '%s' "$data" | wc -c | tr -d '[:space:]')"
  if [ "$bytes" -le "$max" ]; then pass "$name"; else fail "$name ($bytes > $max bytes)"; fi
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
      scan_depth: "quick",
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

run_full() {
  "$SCRIPT" --root "$REPO" --full
}

reset_repo
rm -f "$INDEX"
out="$(run_status)"
assert_jq "$out" '.repo.present == true' "repo_present"
assert_max_bytes "$out" 8000 "default_output_byte_budget"
assert_jq "$out" '.project_map.present == false and .project_map.status == "missing"' "missing_map_reports_missing"
assert_jq "$out" '.installation.mode == "source_checkout" and .installation.version != "" and .launcher.presentation == "calm"' "launcher_exposes_installation_and_calm_presentation"
assert_jq "$out" '.launcher.primary_action.id == "project_map_bootstrap" and .launcher.status.project_map.attention == true' "launcher_recommends_project_map_bootstrap"

mkdir -p "$REPO/.codex-plugin"
printf '{"version":"9.9.9"}\n' > "$REPO/.codex-plugin/plugin.json"
( cd "$REPO" && git add .codex-plugin/plugin.json && git commit -q -m plugin-version )
fake_cache="$WORK/home/.codex/plugins/cache/kimiflow/kimiflow/0.0.1"
mkdir -p "$fake_cache/.codex-plugin"
printf '{"version":"0.0.1"}\n' > "$fake_cache/.codex-plugin/plugin.json"
out="$(KIMIFLOW_PLUGIN_ROOT="$fake_cache" run_status)"
assert_jq "$out" '.installation.mode == "plugin_cache" and .installation.cache_status == "stale_cache" and .installation.action_required == true and .launcher.primary_action.id == "update_installed_plugin"' "launcher_detects_stale_installed_cache"

reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/src/a.txt")"
out="$(run_status)"
assert_jq "$out" '.project_map.present == true and .project_map.depth == "quick" and .project_map.status == "current"' "current_map_reports_current"
assert_jq "$out" '(.launcher.status.project_map | has("depth") | not) and .launcher.status.project_map.status == "current"' "launcher_hides_project_map_depth"
assert_jq "$out" '.repo.dirty == false' "ignored_kimiflow_does_not_dirty_repo"
assert_jq "$out" '.maintenance.bring_current_recommended == false and .maintenance.commits_since_project_map_baseline == 0' "clean_current_repo_no_maintenance_recommended"
assert_jq "$out" '.launcher.primary_action.id == "start_kimiflow" and .launcher.maintenance.visible_count == 0 and .launcher.status.installation.cache_status == "source_checkout"' "launcher_ready_state_is_quiet"
pretty_out="$("$SCRIPT" --root "$REPO" --pretty)"
assert_max_bytes "$pretty_out" 12000 "pretty_output_byte_budget"

printf '# Docs\nmore\n' > "$REPO/docs/guide.md"
( cd "$REPO" && git add docs/guide.md && git commit -q -m docs )
out="$(run_status)"
assert_jq "$out" '.project_map.status == "current" and .maintenance.bring_current_recommended == false and .maintenance.commits_since_project_map_baseline == 1' "baseline_commit_count_is_context_not_maintenance"

printf 'two\n' > "$REPO/src/a.txt"
out="$(run_status)"
assert_jq "$out" '.project_map.status == "stale" and .repo.dirty == true' "stale_map_and_dirty_repo_reported"
assert_jq "$out" '.maintenance.bring_current_recommended == true and (.maintenance.reasons | index("working_tree_dirty")) and (.maintenance.reasons | index("project_map_stale"))' "stale_dirty_repo_recommends_maintenance"
assert_jq "$out" '.launcher.primary_action.id == "clean_worktree" and .launcher.primary_action.blocking == true and (.launcher.maintenance.visible_reasons | index("working_tree_dirty")) and (.launcher.maintenance.visible_reasons | index("project_map_stale"))' "launcher_prioritizes_clean_worktree"

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
out="$(run_status)"
assert_jq "$out" '.findings.open == 2' "findings_counted_de"

cat > "$REPO/.kimiflow/project/FINDINGS.md" <<'EOF'
# Findings

## Open

### F-001
### F-002

## Done

### F-000
EOF
out="$(run_status)"
assert_jq "$out" '.findings.open == 2' "findings_counted_en"

# AC-2: a "### " block carrying the queue-done marker is NOT counted as open.
# AC-3: the unmarked siblings stay counted (the count change is solely the marker, not section logic).
cat > "$REPO/.kimiflow/project/FINDINGS.md" <<'EOF'
# Findings

## Offen

### KF-F-001 - done one
<!-- kimiflow:queue-done id=kf-f-001 commit=abc date=2026-06-28 -->
- x
### KF-F-002 - still open
- y

## Erledigt
EOF
out="$(run_status)"
assert_jq "$out" '.findings.open == 1' "marked_not_counted"

cat > "$REPO/.kimiflow/project/MEMORY.md" <<'EOF'
# Memory

Kimiflow loads small project memory before fresh code exploration.
EOF
cat > "$REPO/.kimiflow/project/LEARNINGS.jsonl" <<'EOF'
{"id":"learn_memory","kind":"process","scope":"project","topic":"memory","summary":"Launcher status exposes memory curation needs.","evidence":["hooks/launcher-status.sh:1"],"confidence":"high","sensitivity":"normal","last_verified":"2026-06-25","source_commit":"abc1234","status":"current"}
{"id":"learn_hidden_summary","kind":"process","scope":"project","topic":"memory","summary":"Sensitive internal wording should stay hidden from launcher summary.","evidence":["hooks/launcher-status.sh:1"],"confidence":"high","sensitivity":"normal","last_verified":"2026-06-25","source_commit":"abc1234","status":"current"}
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
# Default output = first screen: full .memory object and item arrays are omitted;
# memory_summary, counts, maintenance and the .launcher block stay.
out="$(run_status)"
assert_jq "$out" '(has("memory") | not) and .memory_summary.usefulness.hot == 1 and (.maintenance.reasons | index("memory_curation_recommended")) and .launcher.presentation == "calm"' "default_omits_full_memory_keeps_summary_and_launcher"
out="$(run_full)"
assert_jq "$out" '.memory.present == true and .memory.learnings.current == 2 and .memory.curation.recommended == true and (.maintenance.reasons | index("memory_curation_recommended"))' "memory_status_reports_index_missing_curation"
assert_jq "$out" '.memory.history.present == true and .memory.usage.total_uses == 2 and .memory.provider.available == true and .memory.vault.available == true' "launcher_surfaces_history_usage_provider"
assert_jq "$out" '.memory.usefulness.hot.count == 1 and .memory_summary.usefulness.hot == 1 and .memory_summary.usefulness.cold >= 1 and (.memory_summary.next_actions | index("memory_index_missing"))' "launcher_surfaces_memory_usefulness_summary"
if printf '%s\n' "$out" | jq -c '.memory_summary' | grep -q "Sensitive internal wording"; then
  fail "launcher_memory_summary_hides_raw_learning_text"
else
  pass "launcher_memory_summary_hides_raw_learning_text"
fi
assert_jq "$out" '.efficiency.present == true and .efficiency.runs_tracked == 1 and .efficiency.estimated_savings_percent == 83 and .efficiency.confidence == "low" and .efficiency.privacy.stores_paths == false and .memory.global_efficiency.runs_tracked == 1' "launcher_surfaces_global_efficiency"

"$MEMORY_ROUTER" curate --root "$REPO" --write >/dev/null
out="$(run_full)"
assert_jq "$out" '.memory.curation.recommended == false and (.maintenance.reasons | index("memory_curation_recommended") | not)' "memory_index_clears_curation_recommendation"
cat >> "$REPO/.kimiflow/project/LEARNINGS.jsonl" <<'EOF'
{"id":"learn_many_one","kind":"process","scope":"project","topic":"memory","summary":"Additional healthy learning should not surface as user maintenance.","evidence":["hooks/launcher-status.sh:1"],"confidence":"high","sensitivity":"normal","last_verified":"2026-06-25","source_commit":"abc1234","status":"current"}
{"id":"learn_many_two","kind":"process","scope":"project","topic":"memory","summary":"Healthy many-learnings threshold remains an internal signal only.","evidence":["hooks/launcher-status.sh:1"],"confidence":"high","sensitivity":"normal","last_verified":"2026-06-25","source_commit":"abc1234","status":"current"}
EOF
"$MEMORY_ROUTER" curate --root "$REPO" --write >/dev/null
out="$(KIMIFLOW_OBSIDIAN_URL=http://127.0.0.1:1 KIMIFLOW_MEMORY_CURATE_AFTER_LEARNINGS=3 run_full)"
assert_jq "$out" '.memory.curation.recommended == false and .memory.curation.internal_recommended == true and (.memory.curation.silent_reasons | index("many_learnings")) and .maintenance.bring_current_recommended == false and (.maintenance.reasons | index("memory_curation_recommended") | not)' "launcher_hides_benign_many_learnings_signal"
assert_jq "$out" '(.launcher.maintenance.hidden_internal_reasons | index("many_learnings")) and (.launcher.maintenance.visible_reasons | index("many_learnings") | not)' "launcher_keeps_benign_memory_hygiene_internal"

cat > "$REPO/.kimiflow/project/PROPOSALS.jsonl" <<'EOF'
{"id":"learn_memory","learning_id":"learn_memory","type":"standard","kind":"project_rule_confirmed","target_path":".kimiflow/STANDARDS.md","summary":"Project rule confirmed: launcher status exposes pending learning proposals.","evidence":["hooks/launcher-status.sh:1"],"status":"pending","created_at":"2026-06-25T00:00:00Z","updated_at":"2026-06-25T00:00:00Z"}
EOF
out="$(run_full)"
assert_jq "$out" '.memory.proposals.pending == 1 and (.maintenance.reasons | index("learning_proposals_pending"))' "pending_learning_proposals_surface_in_launcher"
assert_jq "$out" '(.launcher.maintenance.hidden_internal_reasons | index("learning_proposals_pending")) and (.launcher.maintenance.visible_reasons | index("learning_proposals_pending") | not)' "launcher_hides_pending_learning_proposals_from_primary_menu"
perl -0pi -e 's/"status":"pending"/"status":"approved"/' "$REPO/.kimiflow/project/PROPOSALS.jsonl"
out="$(run_full)"
assert_jq "$out" '.memory.proposals.approved == 1 and (.maintenance.reasons | index("learning_proposals_approved"))' "approved_learning_proposals_surface_in_launcher"
assert_jq "$out" '(.launcher.maintenance.visible_reasons | index("learning_proposals_approved"))' "launcher_surfaces_approved_learning_proposals"
rm "$REPO/.kimiflow/project/PROPOSALS.jsonl"

awk 'BEGIN{for(i=0;i<950;i++) printf "word "}' > "$REPO/.kimiflow/project/MEMORY.md"
out="$(run_full)"
assert_jq "$out" '.memory.memory.over_budget == true and .memory.curation.recommended == true and (.maintenance.reasons | index("memory_curation_recommended"))' "memory_over_budget_surfaces_in_launcher"
assert_jq "$out" '.launcher.primary_action.id == "curate_memory" and (.launcher.maintenance.visible_reasons | index("memory_curation_recommended"))' "launcher_surfaces_memory_over_budget_as_action"

mkdir -p "$REPO/.kimiflow/parked"
cat > "$REPO/.kimiflow/parked/STATE.md" <<EOF
# STATE

- **status:** backlog
- **Mode:** feature
- **Scope:** small
Plan commit: $BASE
Affected files:
- src/a.txt
plan status: approved
EOF
out="$(run_status)"
assert_jq "$out" '.runs.backlog == 1 and (.runs | has("items") | not)' "default_omits_run_items_keeps_counts"
out="$(run_full)"
assert_jq "$out" '.runs.backlog == 1 and (.runs.items[] | select(.slug == "parked" and .stale_risk == "low"))' "backlog_run_low_risk_when_clean"

printf 'changed\n' > "$REPO/src/a.txt"
out="$(run_full)"
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
out="$(run_full)"
assert_jq "$out" '.runs.done == 3 and .runs.active == 2 and (.runs.items[] | select(.slug == "legacy-active-done" and .status == "done")) and (.runs.items[] | select(.slug == "legacy-missing-done" and .status == "done")) and (.runs.items[] | select(.slug == "not-done" and .status == "active"))' "legacy_phase7_done_runs_inferred_done"
assert_jq "$out" '.runs.learning_reviews.missing_done == 2 and (.runs.items[] | select(.slug == "legacy-active-done" and .learning_review.verdict == "CLOSED" and .learning_review.reason == "missing_review"))' "done_runs_without_learning_review_surface_without_legacy_noise"
assert_jq "$out" '.runs.learning_reviews.needs_attention == 1 and (.maintenance.reasons | index("learning_reviews_need_attention")) and (.runs.items[] | select(.slug == "stale-review-done" and .learning_review.verdict == "CLOSED" and .learning_review.reason == "missing_learnings"))' "stale_existing_learning_review_recommends_attention"
assert_jq "$out" '.maintenance.bring_current_recommended == true and (.maintenance.reasons | index("active_runs"))' "active_runs_recommend_maintenance"

reset_repo
mkdir -p "$REPO/.kimiflow/current-session"
cat > "$REPO/.kimiflow/current-session/STATE.md" <<'EOF'
Status: active
Mode: feature
Scope: small
Affected files: src/a.txt
Phase 0: done
Phase 1: done
Phase 2: done
Phase 3: done
Phase 4: done
Phase 5: in-progress
EOF
"$ACTIVE_RUN" start --root "$REPO" --run .kimiflow/current-session --write >/dev/null
"$ACTIVE_RUN" append-item --root "$REPO" --title "Wire active session into launcher" --write >/dev/null
out="$(run_status)"
assert_jq "$out" '.active_session.present == true and .active_session.run == ".kimiflow/current-session" and .active_session.item_counts.open == 1 and .active_session.stale_risk == "current" and (.maintenance.reasons | index("active_session_open"))' "launcher_surfaces_active_session"
printf 'two\n' > "$REPO/src/a.txt"
( cd "$REPO" && git add src/a.txt && git commit -q -m change-a )
out="$(run_status)"
assert_jq "$out" '.active_session.stale_risk == "needs_revalidation" and (.maintenance.reasons | index("active_session_needs_revalidation"))' "launcher_surfaces_active_session_stale_risk"

reset_repo
printf '{bad json\n' > "$INDEX"
out="$(run_status)"
assert_jq "$out" '.project_map.present == true and .project_map.valid == false and .project_map.status == "unknown"' "invalid_map_reports_unknown"
printf 'null\n' > "$INDEX"
out="$(run_status)"
assert_jq "$out" '.project_map.present == true and .project_map.valid == false and .project_map.status == "unknown" and .project_map.depth == "unknown" and (.launcher.status.project_map | has("depth") | not)' "scalar_map_reports_unknown"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
