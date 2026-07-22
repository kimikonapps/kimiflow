#!/usr/bin/env bash
# kimiflow — unit tests for active-run.sh.
# Isolation: temp git repo under mktemp; the real repo is never touched.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/active-run.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
PLUGIN="$WORK/plugin"
FAKE_ROUTER="$WORK/fake-memory-router.sh"
ROUTER_LOG="$WORK/router.log"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_jq() {
  local json="$1" expr="$2" name="$3"
  if printf '%s\n' "$json" | jq -e "$expr" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi
}
assert_empty() {
  local value="$1" name="$2"
  if [ -z "$value" ]; then pass "$name"; else fail "$name (got '$value')"; fi
}
assert_contains() {
  local value="$1" needle="$2" name="$3"
  if printf '%s\n' "$value" | grep -qF "$needle"; then pass "$name"; else fail "$name (missing '$needle')"; fi
}

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — active-run uses jq"; exit 0
fi

cat > "$FAKE_ROUTER" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${KIMIFLOW_FAKE_ROUTER_LOG:?}"
case "${1:-}" in
  review-run)
    if [ "${KIMIFLOW_FAKE_REVIEW_FAIL:-0}" = "1" ]; then
      printf 'synthetic review failure\n' >&2
      exit 17
    fi
    root=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --root) shift; root="${1:-}" ;;
      esac
      shift || true
    done
    if [ "${KIMIFLOW_FAKE_REVIEW_WRITES:-0}" = "1" ] && [ -n "$root" ]; then
      mkdir -p "$root/.kimiflow/project"
      printf '{"written":true}\n' > "$root/.kimiflow/project/SENTINEL.json"
      printf '{"written":true}\n' > "$root/.kimiflow/demo/LEARNING-REVIEW.md"
    fi
    printf '{"schema_version":1,"status":"recorded","written":true,"recorded_count":1}\n'
    ;;
  verify-run)
    if [ "${KIMIFLOW_FAKE_VERIFY_FAIL:-0}" = "1" ]; then
      printf 'LEARNING_REVIEW\tCLOSED\treason=synthetic_failure\tpath=.kimiflow/demo/LEARNING-REVIEW.md\n'
      exit 1
    fi
    printf 'LEARNING_REVIEW\tOPEN\tstatus=recorded\tfreshness=current\tpath=.kimiflow/demo/LEARNING-REVIEW.md\n'
    ;;
  evaluate-run)
    root=""; run=""; terminal=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --root) shift; root="${1:-}" ;;
        --run) shift; run="${1:-}" ;;
        --terminal) shift; terminal="${1:-}" ;;
      esac
      shift || true
    done
    if [ "${KIMIFLOW_FAKE_EVALUATE_WRITES:-0}" = "1" ] && [ -n "$root" ] && [ -n "$run" ]; then
      mkdir -p "$root/.kimiflow/project"
      printf '{"partial":true}\n' > "$root/.kimiflow/project/STRATEGY-OUTCOMES.jsonl"
      printf '{"partial":true}\n' > "$root/$run/OUTCOME-EVALUATION.json"
    fi
    if [ "${KIMIFLOW_FAKE_EVALUATE_FAIL:-0}" = "1" ]; then
      printf 'synthetic outcome evaluation failure\n' >&2
      exit 23
    fi
    classification="inconclusive"; promotable="false"
    if [ "$terminal" = "done" ]; then classification="verified_success"; promotable="true"; fi
    if [ "$terminal" = "failed" ]; then classification="verified_failure"; promotable="true"; fi
    printf '{"schema_version":1,"status":"evaluated","written":true,"evaluation":{"id":"out_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","terminal":"%s","classification":"%s","promotable":%s}}\n' "$terminal" "$classification" "$promotable"
    ;;
  *)
    printf 'fake-memory-router: unsupported command %s\n' "${1:-}" >&2
    exit 2
    ;;
esac
EOF
chmod +x "$FAKE_ROUTER"

reset_repo() {
  rm -rf "$REPO" "$PLUGIN"
  : > "$ROUTER_LOG"
  mkdir -p "$REPO/src" "$REPO/.kimiflow/demo" "$PLUGIN"
  ( cd "$REPO" && git init -q && git config user.email "kimiflow@example.test" && git config user.name "kimiflow test" )
  printf '.kimiflow/\n' > "$REPO/.gitignore"
  printf 'one\n' > "$REPO/src/a.txt"
  cat > "$REPO/.kimiflow/demo/STATE.md" <<'EOF'
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
Phase 6: open
Phase 7: open
EOF
  cat > "$REPO/.kimiflow/demo/RESEARCH.md" <<'EOF'
Learning: active sessions should keep follow-up work inside Kimiflow.
EOF
  cat > "$REPO/.kimiflow/demo/ACCEPTANCE.md" <<'EOF'
Project rule confirmed: every active session has explicit item status.
EOF
  cat > "$REPO/.kimiflow/demo/CODE-REVIEW.md" <<'EOF'
Pitfall: stale plans must be revalidated before finish.
EOF
  cat > "$REPO/.kimiflow/demo/PLAN.md" <<'EOF'
Decision: active session state stays in .kimiflow/session/ACTIVE_RUN.json.
EOF
  ( cd "$REPO" && git add .gitignore src/a.txt && git commit -q -m init )
}

write_phase_manifest() {
  mkdir -p "$PLUGIN/phases"
  cat > "$PLUGIN/phases/PHASES.json" <<'EOF'
{
  "schema_version": 1,
  "phases": [
    {"id": 0, "name": "p0", "file": "phases/phase-0.md"},
    {"id": 1, "name": "p1", "file": "phases/phase-1.md"},
    {"id": 2, "name": "p2", "file": "phases/phase-2.md"},
    {"id": 3, "name": "p3", "file": "phases/phase-3.md"},
    {"id": 4, "name": "p4", "file": "phases/phase-4.md"},
    {"id": 5, "name": "p5", "file": "phases/phase-5.md"},
    {"id": 6, "name": "p6", "file": "phases/phase-6.md"},
    {"id": 7, "name": "p7", "file": "phases/phase-7.md"}
  ]
}
EOF
  for i in 0 1 2 3 4 5 6 7; do
    printf 'phase %s\n' "$i" > "$PLUGIN/phases/phase-$i.md"
  done
}

write_flow_manifest() {
  mkdir -p "$PLUGIN/phases"
  cat > "$PLUGIN/phases/PHASES.json" <<'EOF'
{
  "schema_version": 2,
  "phases": [
    {"id": 0, "name": "p0", "file": "phases/phase-0.md"},
    {"id": 1, "name": "p1", "file": "phases/phase-1.md"},
    {"id": 2, "name": "p2", "file": "phases/phase-2.md"},
    {"id": 3, "name": "p3", "file": "phases/phase-3.md"},
    {"id": 4, "name": "p4", "file": "phases/phase-4.md"},
    {"id": 5, "name": "p5", "file": "phases/phase-5.md"},
    {"id": 6, "name": "p6", "file": "phases/phase-6.md"},
    {"id": 7, "name": "p7", "file": "phases/phase-7.md"}
  ],
  "flow": {
    "schema_version": 1,
    "terminal_node": "done",
    "guards": [
      {"condition": "awaiting_user", "action": "wait_for_material_decision", "target": "current", "blocks_events": true},
      {"condition": "stale", "action": "revalidate_then_refresh_baseline", "target": "current", "blocks_events": true},
      {"condition": "recovery_plan", "action": "recover_plan_strategy", "target": "phase_2", "blocks_events": false},
      {"condition": "recovery_code", "action": "recover_build", "target": "phase_5", "blocks_events": false},
      {"condition": "items_rejected", "action": "rework_rejected_items", "target": "phase_5", "blocks_events": false},
      {"condition": "items_pending", "action": "build_pending_items", "target": "phase_5", "blocks_events": false},
      {"condition": "items_built", "action": "verify_built_items", "target": "phase_6", "blocks_events": false}
    ],
    "transitions": [
      {"from": "phase_0", "event": "phase_done", "to": "phase_1", "action": "run_phase"},
      {"from": "phase_1", "event": "phase_done", "to": "phase_2", "action": "run_phase"},
      {"from": "phase_2", "event": "phase_done", "to": "phase_3", "action": "run_phase"},
      {"from": "phase_3", "event": "phase_done", "to": "phase_4", "action": "run_phase"},
      {"from": "phase_4", "event": "phase_done", "to": "phase_5", "action": "run_phase"},
      {"from": "phase_5", "event": "phase_done", "to": "phase_6", "action": "run_phase"},
      {"from": "phase_6", "event": "phase_done", "to": "phase_7", "action": "run_phase"},
      {"from": "phase_7", "event": "phase_done", "to": "done", "action": "finish_run"},
      {"from": "phase_4", "event": "plan_recovery", "to": "phase_2", "action": "recover_plan_strategy"},
      {"from": "phase_6", "event": "verification_failed", "to": "phase_5", "action": "recover_build"},
      {"from": "phase_6", "event": "code_gap", "to": "phase_5", "action": "recover_build"},
      {"from": "phase_6", "event": "scope_drift", "to": "phase_5", "action": "recover_build"},
      {"from": "phase_6", "event": "strategy_drift", "to": "phase_2", "action": "recover_plan_strategy"},
      {"from": "phase_6", "event": "architecture_falsified", "to": "phase_2", "action": "recover_plan_strategy"},
      {"from": "phase_6", "event": "research_stale", "to": "phase_2", "action": "recover_plan_strategy"},
      {"from": "phase_7", "event": "review_failed", "to": "phase_5", "action": "recover_build"}
    ]
  }
}
EOF
  for i in 0 1 2 3 4 5 6 7; do
    printf 'phase %s\n' "$i" > "$PLUGIN/phases/phase-$i.md"
  done
}

run_active() {
  KIMIFLOW_HOST=codex CODEX_THREAD_ID=owner-session KIMIFLOW_PLUGIN_ROOT="$PLUGIN" KIMIFLOW_MEMORY_ROUTER="$FAKE_ROUTER" KIMIFLOW_FAKE_ROUTER_LOG="$ROUTER_LOG" "$SCRIPT" "$@" --root "$REPO"
}

reset_repo
out="$(run_active status)"
assert_jq "$out" '.present == false and .status == "none"' "status_reports_no_active_session"
out="$(run_active start --run .kimiflow/preview)"
assert_jq "$out" '.present == false' "start_preview_does_not_activate_session"
[ ! -d "$REPO/.kimiflow/preview" ] && pass "start_preview_does_not_create_run_dir" || fail "start_preview_does_not_create_run_dir"
if run_active start --run "$WORK/outside" --write >/dev/null 2>&1; then
  fail "start_rejects_outside_run_path"
else
  pass "start_rejects_outside_run_path"
fi
missing_root="$WORK/missing-root"
out="$("$SCRIPT" status --root "$missing_root")"
assert_jq "$out" '.present == false and .status == "none"' "invalid_root_status_observational"
err="$("$SCRIPT" start --root "$missing_root" --run .kimiflow/demo --write 2>&1 >/dev/null)"; rc=$?
if [ "$rc" = "2" ]; then pass "invalid_root_write_fails_closed"; else fail "invalid_root_write_fails_closed"; fi
assert_contains "$err" "cannot resolve root" "invalid_root_write_reports_resolution_error"

out="$(run_active start --run .kimiflow/demo --write)"
assert_jq "$out" '.present == true and .run == ".kimiflow/demo" and .stale_risk == "current" and .item_counts.open == 0 and .owner.host == "codex" and .owner.session_id == "owner-session"' "start_creates_owned_active_session"
[ -f "$REPO/.kimiflow/session/ACTIVE_RUN.json" ] && pass "start_writes_active_file" || fail "start_writes_active_file"
assert_jq "$out" 'has("phase_reads_required") | not' "start_without_manifest_no_phase_reads"
if grep -q '^Phase reads required:' "$REPO/.kimiflow/demo/STATE.md"; then
  fail "start_without_manifest_no_state_marker"
else
  pass "start_without_manifest_no_state_marker"
fi

out="$(run_active append-item --title "Add first button" --kind feature --write)"
assert_jq "$out" '.item.id == "item_001" and .item_counts.open == 1' "append_item_creates_stable_id"
out="$(run_active mark-built --id item_001 --write)"
assert_jq "$out" '.item_status == "built" and .item_counts.built == 1' "mark_built_updates_item"
out="$(run_active mark-accepted --id item_001 --write)"
assert_jq "$out" '.item_status == "accepted" and .item_counts.accepted == 1 and .item_counts.open == 0' "mark_accepted_closes_item"

out="$(run_active append-item --title "Add second button" --kind feature --write)"
assert_jq "$out" '.item.id == "item_002"' "append_item_increments_id"
out="$(run_active mark-rejected --id item_002 --reason "visual check failed" --write)"
assert_jq "$out" '.item_status == "rejected" and .item_counts.rejected == 1 and .item_counts.open == 1' "mark_rejected_keeps_item_open"
if run_active finish --write >/dev/null 2>&1; then
  fail "finish_refuses_rejected_item"
else
  pass "finish_refuses_rejected_item"
fi
out="$(run_active drop-item --id item_002 --reason "out of scope for this run" --write)"
assert_jq "$out" '.item_status == "dropped" and .item_counts.open == 0' "drop_item_clears_rejected_item"

input='{"cwd":"'"$REPO"'","session_id":"owner-session","prompt":"secret prompt text should not be stored"}'
out="$(printf '%s' "$input" | KIMIFLOW_HOST=codex "$SCRIPT" prompt-context)"
assert_jq "$out" '.hookSpecificOutput.hookEventName == "UserPromptSubmit" and (.hookSpecificOutput.additionalContext | contains("Kimiflow active session is open"))' "prompt_context_injects_active_session"
if grep -R "secret prompt text should not be stored" "$REPO/.kimiflow" >/dev/null 2>&1; then
  fail "prompt_context_does_not_store_prompt_text"
else
  pass "prompt_context_does_not_store_prompt_text"
fi

out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" stop-gate)"
assert_jq "$out" '.decision == "block" and (.reason | contains("active-session gate"))' "stop_gate_blocks_open_active_session"
out="$(printf '{"cwd":"%s","session_id":"owner-session","stop_hook_active":true}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" stop-gate)"
assert_empty "$out" "stop_gate_loop_break_allows_continuation"

out="$(printf '{"cwd":"%s","session_id":"other-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" prompt-context)"
assert_jq "$out" '(.hookSpecificOutput.additionalContext | contains("This prompt is not part of that run")) and (.hookSpecificOutput.additionalContext | contains("conflict-check"))' "other_session_gets_nonblocking_conflict_context"
out="$(printf '{"cwd":"%s","session_id":"other-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" stop-gate)"
assert_empty "$out" "other_session_stop_never_blocks"
out="$(KIMIFLOW_HOST=codex CODEX_THREAD_ID=other-session "$SCRIPT" conflict-check --root "$REPO" --path src/b.txt)"
assert_jq "$out" '.decision == "allow_disjoint" and .reason == "no_overlap"' "conflict_check_allows_disjoint_path"
out="$(KIMIFLOW_HOST=codex CODEX_THREAD_ID=other-session "$SCRIPT" conflict-check --root "$REPO" --path src/a.txt)"
assert_jq "$out" '.decision == "block_overlap" and .overlaps[0].active == "src/a.txt"' "conflict_check_blocks_exact_overlap"
out="$(KIMIFLOW_HOST=codex CODEX_THREAD_ID=other-session "$SCRIPT" conflict-check --root "$REPO" --path src)"
assert_jq "$out" '.decision == "block_overlap"' "conflict_check_blocks_parent_overlap"

tmp_active="$REPO/.kimiflow/session/ACTIVE_RUN.tmp"
jq 'del(.owner)' "$REPO/.kimiflow/session/ACTIVE_RUN.json" > "$tmp_active" && mv "$tmp_active" "$REPO/.kimiflow/session/ACTIVE_RUN.json"
out="$(printf '{"cwd":"%s","session_id":"other-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" stop-gate)"
assert_empty "$out" "legacy_ownerless_session_stop_fails_open"
out="$(KIMIFLOW_HOST=codex CODEX_THREAD_ID=other-session "$SCRIPT" conflict-check --root "$REPO" --path src/b.txt)"
assert_jq "$out" '.decision == "block_unknown" and .reason == "active_owner_unknown"' "legacy_ownerless_session_write_fails_closed"
run_active refresh-baseline --write >/dev/null
out="$(run_active status)"
assert_jq "$out" '.owner.host == "codex" and .owner.session_id == "owner-session"' "owner_mutation_backfills_legacy_session"

out="$(run_active await-user --run .kimiflow/demo --reason "engine gate: waiting for user answer" --write)"
assert_jq "$out" '.status == "awaiting_user" and .written == true and .run == ".kimiflow/demo"' "await_user_sets_flag"
out="$(run_active status)"
assert_jq "$out" '.awaiting_user == true' "status_reports_awaiting_user"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" stop-gate)"
assert_empty "$out" "stop_gate_passes_while_awaiting_user"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" prompt-context)"
assert_jq "$out" '.hookSpecificOutput.hookEventName == "UserPromptSubmit"' "prompt_context_injects_while_awaiting_user"
out="$(run_active status)"
assert_jq "$out" '.awaiting_user == false' "prompt_context_clears_awaiting_user"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" stop-gate)"
assert_jq "$out" '.decision == "block"' "stop_gate_blocks_after_awaiting_user_cleared"

printf 'Flow schema: 3\nRecovery: active\n' >> "$REPO/.kimiflow/demo/STATE.md"
if run_active await-user --run .kimiflow/demo --reason "another technical round" --write >/dev/null 2>&1; then
  fail "schema3_await_user_requires_kind"
else
  pass "schema3_await_user_requires_kind"
fi
if run_active await-user --run .kimiflow/demo --kind preview --reason "approve another technical round" --write >/dev/null 2>&1; then
  fail "recovery_rejects_preview_pause"
else
  pass "recovery_rejects_preview_pause"
fi
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" stop-gate)"
assert_jq "$out" '.decision == "block"' "rejected_recovery_pause_keeps_stop_gate_closed"
out="$(run_active await-user --run .kimiflow/demo --kind external-access --reason "credential unavailable" --write)"
assert_jq "$out" '.status == "awaiting_user" and .awaiting_kind == "external-access"' "recovery_allows_external_access_pause"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" prompt-context)"
assert_jq "$out" '.hookSpecificOutput.hookEventName == "UserPromptSubmit"' "typed_recovery_pause_resumes_on_user_prompt"
sed -i.bak 's/Recovery: active/Recovery: clean/' "$REPO/.kimiflow/demo/STATE.md" && rm "$REPO/.kimiflow/demo/STATE.md.bak"
out="$(run_active await-user --run .kimiflow/demo --kind commit --reason "clean recovery commit gate" --write)"
assert_jq "$out" '.status == "awaiting_user" and .awaiting_kind == "commit"' "clean_recovery_reenables_commit_pause"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" prompt-context)"
assert_jq "$out" '.hookSpecificOutput.hookEventName == "UserPromptSubmit"' "clean_recovery_commit_pause_resumes"

printf 'two\n' > "$REPO/src/a.txt"
( cd "$REPO" && git add src/a.txt && git commit -q -m change-a )
out="$(run_active status)"
assert_jq "$out" '.stale_risk == "needs_revalidation" and (.stale.relevant_changed_paths | index("src/a.txt"))' "status_reports_stale_relevant_change"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" prompt-context)"
assert_jq "$out" '(.hookSpecificOutput.additionalContext | contains("revalidate"))' "prompt_context_mentions_revalidation"
if run_active finish --write >/dev/null 2>&1; then
  fail "finish_refuses_stale_session"
else
  pass "finish_refuses_stale_session"
fi
out="$(run_active refresh-baseline --write)"
assert_jq "$out" '.stale_risk == "current"' "refresh_baseline_clears_stale_risk"

out="$(run_active finish --write)"
assert_jq "$out" '.status == "finished" and .outcome.outcome == "done" and .outcome.outcome_evaluation.classification == "verified_success"' "finish_succeeds_after_acceptance_and_revalidation"
[ ! -f "$REPO/.kimiflow/session/ACTIVE_RUN.json" ] && pass "finish_clears_active_session" || fail "finish_clears_active_session"
grep -q '^review-run ' "$ROUTER_LOG" && grep -q '^verify-run ' "$ROUTER_LOG" && grep -q '^evaluate-run .*--terminal done' "$ROUTER_LOG" && pass "finish_calls_learning_review_verify_and_outcome_evaluation" || fail "finish_calls_learning_review_verify_and_outcome_evaluation"
grep -q '^Status: done' "$REPO/.kimiflow/demo/STATE.md" && pass "finish_marks_state_done" || fail "finish_marks_state_done"

reset_repo
cat >> "$REPO/.kimiflow/demo/STATE.md" <<'EOF'
Flow schema: 4
Conformance contract: 1
Conformance basis: pending
Run started head: NOT VERIFIED
EOF
run_active start --run .kimiflow/demo --write >/dev/null
err="$(run_active finish --write 2>&1 >/dev/null)"; rc=$?
if [ "$rc" -ne 0 ]; then pass "finish_refuses_closed_conformance_gate"; else fail "finish_refuses_closed_conformance_gate"; fi
assert_contains "$err" "conformance gate closed" "finish_reports_conformance_gate"
[ -f "$REPO/.kimiflow/session/ACTIVE_RUN.json" ] && pass "conformance_failure_keeps_active_session" || fail "conformance_failure_keeps_active_session"
if [ ! -s "$ROUTER_LOG" ]; then pass "conformance_failure_precedes_learning_writes"; else fail "conformance_failure_precedes_learning_writes"; fi

reset_repo
cat > "$REPO/.kimiflow/demo/STATE.md" <<'EOF'
- **Status:** active
- **Mode:** feature
- **Scope:** small
- **Affected files:**
  - src/a.txt
- **Phase 0:** done
- **Phase 1:** done
- **Phase 2:** done
- **Phase 3:** done
- **Phase 4:** done
- **Phase 5:** in-progress
- **Phase 6:** open
- **Phase 7:** open
EOF
run_active start --run .kimiflow/demo --write >/dev/null
printf 'two\n' > "$REPO/src/a.txt"
( cd "$REPO" && git add src/a.txt && git commit -q -m change-a )
out="$(run_active status)"
assert_jq "$out" '.affected_files == ["src/a.txt"] and .stale_risk == "needs_revalidation" and (.stale.relevant_changed_paths | index("src/a.txt"))' "markdown_affected_files_are_parsed"

reset_repo
sed 's/^Affected files:/Touches:/' "$REPO/.kimiflow/demo/STATE.md" > "$REPO/.kimiflow/demo/STATE.tmp" && mv "$REPO/.kimiflow/demo/STATE.tmp" "$REPO/.kimiflow/demo/STATE.md"
run_active start --run .kimiflow/demo --write >/dev/null
printf 'two\n' > "$REPO/src/a.txt"
( cd "$REPO" && git add src/a.txt && git commit -q -m change-a )
out="$(run_active status)"
assert_jq "$out" '.affected_files == ["src/a.txt"] and .stale_risk == "needs_revalidation"' "touches_header_affected_files_are_parsed"

reset_repo
grep -v '^Affected files:' "$REPO/.kimiflow/demo/STATE.md" > "$REPO/.kimiflow/demo/STATE.tmp" && mv "$REPO/.kimiflow/demo/STATE.tmp" "$REPO/.kimiflow/demo/STATE.md"
printf 'Affected files: src/a.txt\n' >> "$REPO/.kimiflow/demo/PLAN.md"
run_active start --run .kimiflow/demo --write >/dev/null
printf 'two\n' > "$REPO/src/a.txt"
( cd "$REPO" && git add src/a.txt && git commit -q -m change-a )
out="$(run_active status)"
assert_jq "$out" '.affected_files == ["src/a.txt"] and .stale_risk == "needs_revalidation"' "plan_md_only_affected_files_fall_back"

reset_repo
grep -v '^Affected files:' "$REPO/.kimiflow/demo/STATE.md" > "$REPO/.kimiflow/demo/STATE.tmp" && mv "$REPO/.kimiflow/demo/STATE.tmp" "$REPO/.kimiflow/demo/STATE.md"
run_active start --run .kimiflow/demo --write >/dev/null
printf 'two\n' > "$REPO/src/a.txt"
( cd "$REPO" && git add src/a.txt && git commit -q -m change-a )
out="$(run_active status)"
assert_jq "$out" '.affected_files == [] and .stale_risk == "unknown" and .stale.reason == "affected_paths_unknown" and (.stale.changed_paths | index("src/a.txt"))' "missing_affected_files_is_unknown_after_changes"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" prompt-context)"
assert_jq "$out" '(.hookSpecificOutput.additionalContext | contains("revalidate"))' "prompt_context_mentions_revalidation_when_unknown"
if run_active finish --write >/dev/null 2>&1; then
  fail "finish_refuses_unknown_staleness"
else
  pass "finish_refuses_unknown_staleness"
fi

reset_repo
mkdir -p "$REPO/.kimiflow/project"
printf '{"existing":true}\n' > "$REPO/.kimiflow/project/EXISTING.json"
run_active start --run .kimiflow/demo --write >/dev/null
if KIMIFLOW_HOST=codex CODEX_THREAD_ID=owner-session KIMIFLOW_MEMORY_ROUTER="$FAKE_ROUTER" KIMIFLOW_FAKE_ROUTER_LOG="$ROUTER_LOG" KIMIFLOW_FAKE_REVIEW_WRITES=1 KIMIFLOW_FAKE_VERIFY_FAIL=1 "$SCRIPT" finish --root "$REPO" --write >/dev/null 2>&1; then
  fail "finish_fails_when_learning_verify_fails"
else
  pass "finish_fails_when_learning_verify_fails"
fi
[ -f "$REPO/.kimiflow/session/ACTIVE_RUN.json" ] && pass "failed_finish_keeps_active_session" || fail "failed_finish_keeps_active_session"
[ -f "$REPO/.kimiflow/project/EXISTING.json" ] && pass "failed_finish_restores_existing_project_memory" || fail "failed_finish_restores_existing_project_memory"
[ ! -f "$REPO/.kimiflow/project/SENTINEL.json" ] && pass "failed_finish_rolls_back_review_memory_write" || fail "failed_finish_rolls_back_review_memory_write"
[ ! -f "$REPO/.kimiflow/demo/LEARNING-REVIEW.md" ] && pass "failed_finish_rolls_back_run_learning_review" || fail "failed_finish_rolls_back_run_learning_review"
if grep -q '^Status: done' "$REPO/.kimiflow/demo/STATE.md"; then
  fail "failed_finish_does_not_mark_state_done"
else
  pass "failed_finish_does_not_mark_state_done"
fi

reset_repo
mkdir -p "$REPO/.kimiflow/project"
printf '{"existing":true}\n' > "$REPO/.kimiflow/project/EXISTING.json"
run_active start --run .kimiflow/demo --write >/dev/null
if KIMIFLOW_HOST=codex CODEX_THREAD_ID=owner-session KIMIFLOW_MEMORY_ROUTER="$FAKE_ROUTER" KIMIFLOW_FAKE_ROUTER_LOG="$ROUTER_LOG" KIMIFLOW_FAKE_EVALUATE_WRITES=1 KIMIFLOW_FAKE_EVALUATE_FAIL=1 "$SCRIPT" finish --root "$REPO" --write >/dev/null 2>&1; then
  fail "finish_fails_when_outcome_evaluation_fails"
else
  pass "finish_fails_when_outcome_evaluation_fails"
fi
[ -f "$REPO/.kimiflow/session/ACTIVE_RUN.json" ] && pass "outcome_failure_keeps_active_session" || fail "outcome_failure_keeps_active_session"
[ -f "$REPO/.kimiflow/project/EXISTING.json" ] && pass "outcome_failure_restores_existing_project" || fail "outcome_failure_restores_existing_project"
[ ! -f "$REPO/.kimiflow/project/STRATEGY-OUTCOMES.jsonl" ] && pass "outcome_failure_rolls_back_strategy_ledger" || fail "outcome_failure_rolls_back_strategy_ledger"
[ ! -f "$REPO/.kimiflow/demo/OUTCOME-EVALUATION.json" ] && pass "outcome_failure_rolls_back_run_artifact" || fail "outcome_failure_rolls_back_run_artifact"

reset_repo
run_active start --run .kimiflow/demo --write >/dev/null
err="$(KIMIFLOW_HOST=codex CODEX_THREAD_ID=owner-session KIMIFLOW_PLUGIN_ROOT="$PLUGIN" KIMIFLOW_MEMORY_ROUTER="$FAKE_ROUTER" KIMIFLOW_FAKE_ROUTER_LOG="$ROUTER_LOG" KIMIFLOW_FAKE_REVIEW_FAIL=1 "$SCRIPT" finish --root "$REPO" --write 2>&1 >/dev/null)"; rc=$?
if [ "$rc" = "17" ]; then
  pass "finish_returns_review_failure_code"
else
  fail "finish_returns_review_failure_code (rc=$rc)"
fi
assert_contains "$err" "synthetic review failure" "finish_passes_through_review_stderr"

reset_repo
write_phase_manifest
out="$(run_active start --run .kimiflow/demo --write)"
assert_jq "$out" '.phase_reads_required == true' "start_with_manifest_sets_phase_reads"
grep -q '^Phase reads required: yes' "$REPO/.kimiflow/demo/STATE.md" && pass "start_with_manifest_marks_state" || fail "start_with_manifest_marks_state"
out="$(run_active phase-read --run .kimiflow/demo --phase 0 --file phases/phase-0.md --write)"
assert_jq "$out" '.status == "phase_read_recorded" and .record.phase == 0 and .record.file == "phases/phase-0.md"' "phase_read_records_phase"
out="$(run_active phase-read-status --run .kimiflow/demo --json)"
assert_jq "$out" '.phase_reads_required == true and .records.reads["0"].file == "phases/phase-0.md"' "phase_read_status_reports_record"
out="$(run_active phase-read-gate --run .kimiflow/demo --through-phase 1)"
assert_contains "$out" "PHASE_READ_GATE"$'\t'"CLOSED" "phase_read_gate_closes_missing"
assert_contains "$out" "phase_1_read_missing" "phase_read_gate_missing_detail"
run_active phase-read --run .kimiflow/demo --phase 1 --file phases/phase-1.md --write >/dev/null
out="$(run_active phase-read-gate --run .kimiflow/demo --through-phase 1)"
assert_contains "$out" "PHASE_READ_GATE"$'\t'"OPEN" "phase_read_gate_opens_fresh"
printf 'changed\n' >> "$PLUGIN/phases/phase-1.md"
out="$(run_active phase-read-gate --run .kimiflow/demo --through-phase 1)"
assert_contains "$out" "phase_1_read_stale" "phase_read_gate_stale_detail"
if run_active phase-read --run .kimiflow/demo --phase 1 --file ../phase-1.md --write >/dev/null 2>&1; then
  fail "phase_read_rejects_traversal"
else
  pass "phase_read_rejects_traversal"
fi
ln -sf "$WORK/outside-phase.md" "$PLUGIN/phases/phase-2.md"
printf 'outside\n' > "$WORK/outside-phase.md"
if run_active phase-read --run .kimiflow/demo --phase 2 --file phases/phase-2.md --write >/dev/null 2>&1; then
  fail "phase_read_rejects_symlink_escape"
else
  pass "phase_read_rejects_symlink_escape"
fi
rm "$PLUGIN/phases/phase-2.md"
printf 'phase 2\n' > "$PLUGIN/phases/phase-2.md"
if run_active finish --write >/dev/null 2>&1; then
  fail "finish_blocks_missing_phase_reads"
else
  pass "finish_blocks_missing_phase_reads"
fi
for i in 0 1 2 3 4 5 6 7; do
  run_active phase-read --run .kimiflow/demo --phase "$i" --file "phases/phase-$i.md" --write >/dev/null
done
out="$(run_active finish --write)"
assert_jq "$out" '.status == "finished" and .outcome.outcome == "done"' "finish_allows_fresh_phase_reads"

reset_repo
write_flow_manifest
sed -i.bak '1i\
Flow schema: 4\
Recovery: clean\
Review gate: code
' "$REPO/.kimiflow/demo/STATE.md" && rm "$REPO/.kimiflow/demo/STATE.md.bak"
out="$(run_active start --run .kimiflow/demo --write)"
assert_jq "$out" '.transition.graph_status == "ready" and .transition.current_node == "phase_5" and .transition.action == "run_phase" and .next_action == "finish_or_continue"' "flow_status_adds_transition_without_breaking_legacy_scalar"
out="$(run_active next-action)"
assert_jq "$out" '.current_node == "phase_5" and .action == "run_phase" and .target_file == "phases/phase-5.md"' "next_action_cli_resolves_current_phase"
run_active append-item --title "Graph item" --write >/dev/null
out="$(run_active next-action)"
assert_jq "$out" '.action == "build_pending_items" and .target_node == "phase_5"' "next_action_routes_pending_item"
run_active drop-item --id item_001 --reason "integration test complete" --write >/dev/null
sed -i.bak 's/Phase 5: in-progress/Phase 5: done/; s/Phase 6: open/Phase 6: in-progress/' "$REPO/.kimiflow/demo/STATE.md" && rm "$REPO/.kimiflow/demo/STATE.md.bak"
out="$(run_active next-action --event verification_failed)"
assert_jq "$out" '.current_node == "phase_6" and .action == "recover_build" and .target_node == "phase_5"' "next_action_routes_verification_failure"
out="$(run_active next-action)"
assert_jq "$out" '.current_node == "phase_6" and .action == "run_phase" and .target_node == "phase_6"' "next_action_cli_resolves_same_transition_as_hooks"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex KIMIFLOW_PLUGIN_ROOT="$PLUGIN" "$SCRIPT" prompt-context)"
assert_jq "$out" '(.hookSpecificOutput.additionalContext | contains("run_phase")) and (.hookSpecificOutput.additionalContext | contains("phase_6"))' "prompt_context_uses_exact_transition"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex KIMIFLOW_PLUGIN_ROOT="$PLUGIN" "$SCRIPT" stop-gate)"
assert_jq "$out" '(.reason | contains("run_phase")) and (.reason | contains("phase_6"))' "next_action_cli_and_hooks_share_transition"
printf 'two\n' > "$REPO/src/a.txt"
( cd "$REPO" && git add src/a.txt && git commit -q -m stale-flow-fixture )
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex KIMIFLOW_PLUGIN_ROOT="$PLUGIN" "$SCRIPT" prompt-context)"
prompt_revalidation_count="$(printf '%s\n' "$out" | jq -r '.hookSpecificOutput.additionalContext' | grep -o 'revalidate' | wc -l | tr -d ' ')"
[ "$prompt_revalidation_count" -eq 1 ] && pass "schema4_stale_prompt_has_one_revalidation_instruction" || fail "schema4_stale_prompt_has_one_revalidation_instruction"
out="$(printf '{"cwd":"%s","session_id":"owner-session"}' "$REPO" | KIMIFLOW_HOST=codex KIMIFLOW_PLUGIN_ROOT="$PLUGIN" "$SCRIPT" stop-gate)"
stop_revalidation_count="$(printf '%s\n' "$out" | jq -r '.reason' | grep -o 'revalidate' | wc -l | tr -d ' ')"
[ "$stop_revalidation_count" -eq 1 ] && pass "schema4_stale_stop_has_one_revalidation_instruction" || fail "schema4_stale_stop_has_one_revalidation_instruction"

reset_repo
run_active start --run .kimiflow/demo --write >/dev/null
write_phase_manifest
out="$(run_active status)"
assert_jq "$out" 'has("phase_reads_required") | not' "legacy_run_stays_unmarked_after_manifest_added"
out="$(run_active phase-read-gate --run .kimiflow/demo --through-phase 7)"
assert_contains "$out" "reason=legacy" "legacy_phase_read_gate_opens"
out="$(run_active finish --write)"
assert_jq "$out" '.status == "finished" and .outcome.outcome == "done"' "legacy_finish_ignores_late_manifest"

reset_repo
run_active start --run .kimiflow/demo --write >/dev/null
out="$(run_active park --reason "waiting for user validation" --write)"
assert_jq "$out" '.status == "parked" and .outcome.learning_review.status == "not_promoted" and .outcome.outcome_evaluation.status == "evaluated"' "park_clears_with_best_effort_evaluation"
grep -q '^evaluate-run .*--terminal parked' "$ROUTER_LOG" && pass "park_calls_outcome_evaluation" || fail "park_calls_outcome_evaluation"

reset_repo
run_active start --run .kimiflow/demo --write >/dev/null
out="$(run_active fail --reason "verification failed" --write)"
assert_jq "$out" '.status == "failed" and .outcome.learning_review.status == "not_promoted" and .outcome.outcome_evaluation.status == "evaluated"' "fail_clears_with_best_effort_evaluation"
grep -q '^evaluate-run .*--terminal failed' "$ROUTER_LOG" && pass "fail_calls_outcome_evaluation" || fail "fail_calls_outcome_evaluation"

reset_repo
run_active start --run .kimiflow/demo --write >/dev/null
out="$(run_active abort --reason "user switched workflow" --write)"
assert_jq "$out" '.status == "aborted" and .outcome.learning_review.status == "not_promoted" and .outcome.outcome_evaluation.status == "evaluated"' "abort_clears_with_best_effort_evaluation"
grep -q '^evaluate-run .*--terminal aborted' "$ROUTER_LOG" && pass "abort_calls_outcome_evaluation" || fail "abort_calls_outcome_evaluation"

out="$(printf '{"cwd":"%s"}' "$REPO" | "$SCRIPT" prompt-context)"
assert_empty "$out" "prompt_context_noops_without_active_session"
out="$(printf '{"cwd":"%s"}' "$REPO" | "$SCRIPT" stop-gate)"
assert_empty "$out" "stop_gate_noops_without_active_session"

reset_repo
printf 'Flow schema: 4\nIntent contract: 3\n' >> "$REPO/.kimiflow/demo/STATE.md"
run_active start --run .kimiflow/demo --scope small --write >/dev/null
out="$(run_active status)"
assert_jq "$out" '.mode == "feature" and .scope == "small"' "contract3_start_keeps_feature_selector"
assert_jq "$(cat "$REPO/.kimiflow/session/ACTIVE_RUN.json")" '.intent_contract == "3"' "contract3_start_pins_intent_selector"
cat > "$REPO/.kimiflow/demo/INTAKE.md" <<'EOF'
<!-- kimiflow:intake contract=3 round=1 questions=1 selection=impact_uncertainty technical_questions=0 -->
Confirm the compact product contract.
EOF
run_active await-user --run .kimiflow/demo --kind intake --round 1 --request .kimiflow/demo/INTAKE.md --write >/dev/null
out="$(printf '{"cwd":"%s","session_id":"owner-session","prompt":"private contract answer"}' "$REPO" | KIMIFLOW_HOST=codex "$SCRIPT" prompt-context)"
assert_jq "$out" '.hookSpecificOutput.hookEventName == "UserPromptSubmit"' "contract3_chat_response_resumes_run"
assert_jq "$(cat "$REPO/.kimiflow/demo/INTAKE-RECEIPT-1.json")" '.contract == 3 and .round == 1 and .channel == "chat"' "contract3_chat_response_writes_receipt"
if grep -R -Fq 'private contract answer' "$REPO/.kimiflow"; then fail "contract3_receipt_does_not_store_answer"; else pass "contract3_receipt_does_not_store_answer"; fi
run_active abort --reason "contract3 fixture complete" --write >/dev/null

CLAUDE_ENV_FILE_TEST="$WORK/claude-env"
printf '{"session_id":"claude-session","hook_event_name":"SessionStart"}' \
  | CLAUDE_ENV_FILE="$CLAUDE_ENV_FILE_TEST" "$SCRIPT" session-bootstrap
if grep -q '^export KIMIFLOW_SESSION_ID=claude-session$' "$CLAUDE_ENV_FILE_TEST" \
  && grep -q '^export KIMIFLOW_SESSION_HOST=claude$' "$CLAUDE_ENV_FILE_TEST"; then
  pass "session_bootstrap_persists_claude_identity"
else
  fail "session_bootstrap_persists_claude_identity"
fi

# --- No-jq degradation: the HOOK entrypoints must never block prompts/stops ---------
# prompt-context (UserPromptSubmit) and stop-gate (Stop) run in EVERY repo once the
# plugin is installed; exit 2 without jq would block+erase every user prompt. They
# must degrade to exit 0 (like test-gate.sh and the nudges); CLI subcommands keep
# their hard jq requirement.
REALBASH="$(command -v bash)"
NOJQ="$WORK/nojq-bin"; mkdir -p "$NOJQ"
for t in bash cat grep sed head git tr dirname pwd; do
  s="$(command -v "$t")" && [ -n "$s" ] && ln -s "$s" "$NOJQ/$t" 2>/dev/null
done
printf '{"cwd":"%s"}' "$REPO" | PATH="$NOJQ" "$REALBASH" "$SCRIPT" prompt-context >/dev/null 2>&1
rc=$?
[ "$rc" -eq 0 ] && pass "prompt_context_degrades_to_exit0_without_jq" || fail "prompt_context_degrades_to_exit0_without_jq (rc=$rc)"
printf '{"cwd":"%s","stop_hook_active":false}' "$REPO" | PATH="$NOJQ" "$REALBASH" "$SCRIPT" stop-gate >/dev/null 2>&1
rc=$?
[ "$rc" -eq 0 ] && pass "stop_gate_degrades_to_exit0_without_jq" || fail "stop_gate_degrades_to_exit0_without_jq (rc=$rc)"
PATH="$NOJQ" "$REALBASH" "$SCRIPT" status --root "$REPO" >/dev/null 2>&1
rc=$?
[ "$rc" -eq 2 ] && pass "cli_subcommands_still_require_jq" || fail "cli_subcommands_still_require_jq (rc=$rc)"

NOPY="$WORK/nopy-bin"; mkdir -p "$NOPY"
for t in bash cat grep sed head git tr dirname pwd jq; do
  s="$(command -v "$t")" && [ -n "$s" ] && ln -s "$s" "$NOPY/$t" 2>/dev/null
done
printf '{"cwd":"%s"}' "$REPO" | PATH="$NOPY" "$REALBASH" "$SCRIPT" prompt-context >/dev/null 2>&1
rc=$?
[ "$rc" -eq 0 ] && pass "prompt_context_degrades_to_exit0_without_python3" || fail "prompt_context_degrades_to_exit0_without_python3 (rc=$rc)"
printf '{"cwd":"%s","stop_hook_active":false}' "$REPO" | PATH="$NOPY" "$REALBASH" "$SCRIPT" stop-gate >/dev/null 2>&1
rc=$?
[ "$rc" -eq 0 ] && pass "stop_gate_degrades_to_exit0_without_python3" || fail "stop_gate_degrades_to_exit0_without_python3 (rc=$rc)"
PATH="$NOPY" "$REALBASH" "$SCRIPT" status --root "$REPO" >/dev/null 2>&1
rc=$?
[ "$rc" -eq 2 ] && pass "cli_subcommands_report_missing_python3" || fail "cli_subcommands_report_missing_python3 (rc=$rc)"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
