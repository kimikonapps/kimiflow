#!/usr/bin/env bash
# kimiflow — unit tests for clarify-gate.sh.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/clarify-gate.sh"
ACTIVE="$(cd "$(dirname "$0")" && pwd)/active-run.sh"
WORK="$(mktemp -d)"
RUN="$WORK/.kimiflow/demo"
FAILS=0
trap 'rm -rf "$WORK"' EXIT

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
field() { printf '%s' "$1" | cut -f"$2"; }
assert_field() {
  local out="$1" n="$2" want="$3" label="$4" got
  got="$(field "$out" "$n")"
  if [ "$got" = "$want" ]; then pass "$label"; else fail "$label (field $n='$got' want '$want')"; fi
}
assert_contains() {
  local out="$1" want="$2" label="$3"
  if printf '%s\n' "$out" | grep -qF "$want"; then pass "$label"; else fail "$label (missing '$want')"; fi
}

reset_run() {
  rm -rf "$WORK"
  mkdir -p "$RUN"
  cat > "$RUN/STATE.md" <<'EOF'
Status: active
Mode: feature
Alias: quick
Scope: small
Phase 0: done
Phase 1: done
EOF
  cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed source=current-run -->
Build a small feature after confirming behavior, scope, and outcome.
EOF
}

reset_contract2_feature() {
  rm -rf "$WORK"
  mkdir -p "$RUN"
  cat > "$RUN/STATE.md" <<'EOF'
Flow schema: 4
Intent contract: 2
Status: active
Mode: feature
Alias: quick
Scope: small
Phase 0: done
Phase 1: done
EOF
  cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:intent-coverage contract=2 goal=user_explicit actor=project_evidence behavior=user_explicit boundaries=reversible_default success=user_explicit constraints=not_applicable unknown_material=0 question_rounds=0 technical_questions=0 critic=folded authority=explicit summary=present source=current-run -->
The request and project evidence cover every material product dimension.
Intent evidence: actor :: docs/product.md:12
EOF
}

run_gate() { KIMIFLOW_PLUGIN_ROOT="$WORK" "$SCRIPT" "$RUN"; }
run_post_diagnosis_gate() { KIMIFLOW_PLUGIN_ROOT="$WORK" "$SCRIPT" "$RUN" --post-diagnosis; }
record_fix_approval() { KIMIFLOW_PLUGIN_ROOT="$WORK" "$SCRIPT" "$RUN" --record-fix-approval; }
record_fix_approval_with_gnu_stat() { PATH="$WORK/bin:$PATH" KIMIFLOW_PLUGIN_ROOT="$WORK" "$SCRIPT" "$RUN" --record-fix-approval; }

write_phase_fixture() {
  mkdir -p "$WORK/phases"
  cat > "$WORK/phases/PHASES.json" <<'EOF'
{"schema_version":1,"phases":[
{"id":0,"name":"p0","file":"phases/phase-0.md"},
{"id":1,"name":"p1","file":"phases/phase-1.md"}
]}
EOF
  printf 'phase 0\n' > "$WORK/phases/phase-0.md"
  printf 'phase 1\n' > "$WORK/phases/phase-1.md"
}

record_phase() {
  KIMIFLOW_PLUGIN_ROOT="$WORK" "$ACTIVE" phase-read --root "$WORK" --run .kimiflow/demo --phase "$1" --file "phases/phase-$1.md" --write >/dev/null
}

reset_run
out="$(run_gate)"
assert_field "$out" 2 OPEN "complete_intent_marker_opens"
assert_contains "$out" "reason=clean" "complete_intent_marker_reason"
out="$(record_fix_approval)"
assert_field "$out" 2 CLOSED "feature_cannot_record_fix_approval"
assert_contains "$out" "fix_approval_mode_invalid" "feature_record_fix_approval_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
Build a small feature without documented clarification.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "small_missing_marker_closes"
assert_contains "$out" "intent_evidence_missing" "small_missing_marker_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed source=current-run -->
One compact confirmation covered every required intent dimension.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "no_minimum_question_count"

reset_contract2_feature
out="$(run_gate)"
assert_field "$out" 2 OPEN "contract2_complete_zero_round_opens"

reset_contract2_feature
sed -i.bak 's/ authority=explicit//' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_feature_requires_build_authority"
assert_contains "$out" "implementation_authority_missing" "contract2_feature_authority_detail"

reset_contract2_feature
sed -i.bak 's/summary=present/summary=missing/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_requires_plain_summary_receipt"
assert_contains "$out" "plain_summary_missing" "contract2_plain_summary_detail"

reset_contract2_feature
sed -i.bak 's/Alias: quick/Alias: plan/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
sed -i.bak 's/ authority=explicit//' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 OPEN "contract2_plan_does_not_claim_future_build_authority"

reset_contract2_feature
sed -i.bak '/^Intent evidence: actor/d' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_project_evidence_needs_reference"
assert_contains "$out" "intent_actor_evidence_missing" "contract2_project_evidence_detail"

reset_contract2_feature
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed authority=explicit summary=present source=current-run -->
Generic confirmation must not satisfy the provenance-aware contract.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_generic_confirmation_closes"
assert_contains "$out" "intent_coverage_missing" "contract2_generic_confirmation_detail"

reset_contract2_feature
sed -i.bak 's/goal=user_explicit/goal=reversible_default/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_goal_cannot_be_invented"
assert_contains "$out" "intent_goal_provenance_invalid" "contract2_goal_provenance_detail"

reset_contract2_feature
sed -i.bak 's/unknown_material=0/unknown_material=1/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_material_unknown_closes"
assert_contains "$out" "intent_material_unknowns_open" "contract2_material_unknown_detail"

reset_contract2_feature
sed -i.bak 's/technical_questions=0/technical_questions=1/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_technical_question_closes"
assert_contains "$out" "intent_technical_questions_forbidden" "contract2_technical_question_detail"

reset_contract2_feature
sed -i.bak 's/question_rounds=0/question_rounds=2/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_multiple_rounds_close"
assert_contains "$out" "intent_question_rounds_exceeded" "contract2_multiple_rounds_detail"

reset_contract2_feature
sed -i.bak 's/goal=user_explicit/goal=user_confirmed/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
sed -i.bak 's/question_rounds=0/question_rounds=1/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 OPEN "contract2_one_product_batch_opens"

reset_contract2_feature
sed -i.bak 's/question_rounds=0/question_rounds=1/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_round_needs_user_answer_provenance"
assert_contains "$out" "intent_question_round_unbound" "contract2_round_provenance_detail"

reset_contract2_feature
sed -i.bak 's/Scope: small/Scope: large/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_large_requires_fresh_critic"
assert_contains "$out" "intent_critic_required" "contract2_large_critic_detail"
sed -i.bak 's/critic=folded/critic=passed/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 OPEN "contract2_large_passed_critic_opens"

reset_contract2_feature
sed -i.bak 's/ source=current-run//' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "contract2_missing_source_closes"
assert_contains "$out" "intent_coverage_not_current_run" "contract2_missing_source_detail"

reset_contract2_feature
sed -i.bak 's/Intent contract: 2/Intent contract: invalid/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "invalid_intent_contract_closes"
assert_contains "$out" "intent_contract_invalid" "invalid_intent_contract_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=pending source=current-run -->
The user-visible outcome was not confirmed.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "incomplete_intent_closes"
assert_contains "$out" "intent_outcome_unconfirmed" "incomplete_intent_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed source=prior-chat -->
Loose prior discussion was mistaken for current-run clarification.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "prior_chat_source_closes"
assert_contains "$out" "micro_grill_not_current_run" "prior_chat_source_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed source=current-run -->
The prompt already covered behavior, scope, and acceptance signal; user confirmed.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "confirmed_assumptions_open"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=pending outcome=confirmed source=current-run -->
The scope boundary was not confirmed.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "incomplete_assumptions_close"
assert_contains "$out" "intent_scope_unconfirmed" "incomplete_assumptions_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=yes source=current-run -->
Legacy evidence remains valid for an existing prepared run.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "legacy_marker_stays_compatible"

reset_run
cat > "$RUN/STATE.md" <<'EOF'
Status: active
Mode: feature
Scope: trivial
Phase 0: done
EOF
rm "$RUN/INTENT.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "trivial_without_artifact_opens"

reset_run
cat > "$RUN/STATE.md" <<'EOF'
Status: active
Mode: feature
Scope: large
Phase 0: done
Phase 1: done
EOF
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
Large run has a full clarification artifact.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "large_artifact_without_micro_marker_opens"

reset_run
cat > "$RUN/STATE.md" <<'EOF'
Flow schema: 4
Status: active
Mode: feature
Scope: large
Phase 0: done
Phase 1: done
EOF
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
Large schema-4 feature without a front-loaded contract.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "schema4_large_requires_complete_frontloaded_contract"
assert_contains "$out" "intent_evidence_missing" "schema4_large_missing_contract_detail"
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed authority=explicit summary=present source=current-run -->
The user asked to build this bounded feature and received a simple summary.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "schema4_large_complete_frontloaded_contract_opens"
sed -i.bak 's/ authority=explicit//' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "schema4_feature_still_requires_implementation_authority"
assert_contains "$out" "implementation_authority_missing" "schema4_feature_authority_detail"
sed -i.bak 's/outcome=confirmed/outcome=confirmed authority=explicit/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
sed -i.bak 's/summary=present/summary=missing/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "schema4_requires_plain_summary_receipt"
assert_contains "$out" "plain_summary_missing" "schema4_plain_summary_detail"

reset_run
sed -i.bak 's/Alias: quick/Alias: plan/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
sed -i.bak '1i\
Flow schema: 4' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
sed -i.bak 's/Scope: small/Scope: large/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed summary=present source=current-run -->
Prepare a plan only; no implementation has been authorized.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "schema4_plan_does_not_claim_future_build_authority"

reset_run
sed -i.bak 's/Mode: feature/Mode: audit/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
sed -i.bak 's/Alias: quick/Alias: audit/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
sed -i.bak '1i\
Flow schema: 4' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
cat > "$RUN/AUDIT-INTENT.md" <<'EOF'
# Audit intent
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed summary=present source=current-run -->
Inspect this target read-only; no cleanup slice has been authorized.
EOF
rm "$RUN/INTENT.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "schema4_audit_does_not_claim_future_build_authority"

reset_run
cat > "$RUN/STATE.md" <<'EOF'
Flow schema: 4
Status: active
Mode: fix
Scope: small
Build risk: none
Phase 0: done
Phase 1: done
EOF
cat > "$RUN/PROBLEM.md" <<'EOF'
# Problem
<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed authority=explicit summary=present source=current-run -->
Fix the confirmed behavior inside the stated boundary.
EOF
rm "$RUN/INTENT.md"
printf '# Diagnosis\nThe technical cause is proven.\n' > "$RUN/DIAGNOSIS.md"
printf '# Plan\nApply the bounded correction.\n' > "$RUN/PLAN.md"
printf '# Acceptance\nAC-1: The confirmed behavior is restored.\n' > "$RUN/ACCEPTANCE.md"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "schema4_fix_needs_no_post_diagnosis_approval"
out="$(record_fix_approval)"
assert_field "$out" 2 CLOSED "schema4_rejects_legacy_fix_approval_write"
assert_contains "$out" "fix_approval_schema_unsupported" "schema4_legacy_fix_approval_detail"

reset_run
cat > "$RUN/STATE.md" <<'EOF'
Status: active
Mode: feature
Scope: small
Phase 0: done
Phase 1: done
EOF
rm "$RUN/INTENT.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "nontrivial_missing_artifact_closes"
assert_contains "$out" "clarify_artifact_missing" "nontrivial_missing_artifact_detail"

reset_run
cat > "$RUN/STATE.md" <<'EOF'
Flow schema: 3
Status: active
Mode: fix
Scope: small
Build risk: none
Phase 0: done
Phase 1: done
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "fix_rejects_wrong_mode_intent_artifact"
assert_contains "$out" "clarify_artifact_missing" "fix_wrong_mode_artifact_detail"

cat > "$RUN/PROBLEM.md" <<'EOF'
# Problem
The report already identifies the symptom and expected behavior.
EOF
rm "$RUN/INTENT.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "fix_phase1_needs_no_confirmation_stop"

out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "fix_preview_requires_diagnosis"
assert_contains "$out" "fix_diagnosis_missing" "fix_preview_missing_diagnosis_detail"

cat > "$RUN/DIAGNOSIS.md" <<'EOF'
# Diagnosis
The root cause is proven, but the fix preview has not been approved.
EOF
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
Apply the bounded fix and run the focused regression test.
EOF
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
AC-1: The reported behavior is corrected without scope expansion.
EOF
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "fix_preview_missing_approval_closes"
assert_contains "$out" "fix_approval_missing" "fix_preview_missing_approval_detail"

mkdir -p "$WORK/bin"
cat > "$WORK/bin/stat" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  -c) printf '644\n' ;;
  -f) printf 'GNU stat accepted -f as filesystem output, not a mode\n' ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$WORK/bin/stat"
out="$(record_fix_approval_with_gnu_stat)"
assert_field "$out" 2 OPEN "record_fix_approval_opens"
assert_contains "$(cat "$RUN/DIAGNOSIS.md")" "basis=" "record_fix_approval_persists_basis"
assert_contains "$(cat "$RUN/STATE.md")" "Fix approval: confirmed" "record_fix_approval_persists_state_marker"
assert_contains "$(cat "$RUN/STATE.md")" "Fix approval basis:" "record_fix_approval_persists_authority_basis"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "complete_fix_preview_opens"

printf '%s\n' 'The cause changed after approval.' >> "$RUN/DIAGNOSIS.md"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "changed_diagnosis_keeps_authority_approval"

printf '%s\n' 'Use a different bounded implementation strategy.' >> "$RUN/PLAN.md"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "changed_plan_keeps_authority_approval"

printf '%s\n' 'Affected files: src/recovered.ts' >> "$RUN/STATE.md"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "changed_affected_files_keep_authority_approval"

printf '%s\n' 'AC-2: Preserve the approved boundary.' >> "$RUN/ACCEPTANCE.md"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "changed_acceptance_invalidates_fix_approval"
assert_contains "$out" "fix_approval_basis_stale" "changed_acceptance_stale_detail"

out="$(record_fix_approval)"
assert_field "$out" 2 OPEN "rerecord_changed_acceptance_opens"
printf '%s\n' 'The requested outcome changed.' >> "$RUN/PROBLEM.md"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "changed_problem_invalidates_fix_approval"
assert_contains "$out" "fix_approval_basis_stale" "changed_problem_stale_detail"

out="$(record_fix_approval)"
assert_field "$out" 2 OPEN "rerecord_changed_problem_opens"
sed -i.bak 's/Scope: small/Scope: large/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "changed_scope_invalidates_fix_approval"
assert_contains "$out" "fix_approval_basis_stale" "changed_scope_stale_detail"

sed -i.bak 's/Scope: large/Scope: small/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(record_fix_approval)"
assert_field "$out" 2 OPEN "rerecord_current_basis_opens"
sed -i.bak 's/Build risk: none/Build risk: required/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "changed_risk_invalidates_fix_approval"
assert_contains "$out" "fix_approval_basis_stale" "changed_risk_stale_detail"

out="$(record_fix_approval)"
assert_field "$out" 2 OPEN "rerecord_changed_risk_opens"
sed -i.bak 's/Scope: small/Scope: trivial/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "changed_scope_to_trivial_invalidates_fix_approval"
assert_contains "$out" "fix_approval_basis_stale" "changed_trivial_scope_stale_detail"

sed -i.bak 's/Scope: trivial/Scope: small/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "restored_scope_reuses_current_approval"
sed -i.bak 's/Mode: fix/Mode: feature/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "changed_mode_cannot_bypass_fix_approval"
assert_contains "$out" "fix_approval_mode_changed" "changed_mode_closes_fix_approval_detail"

sed -i.bak 's/Mode: feature/Mode: fix/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "restored_mode_reuses_current_approval"
grep -v '^Fix approval' "$RUN/STATE.md" > "$RUN/STATE.tmp" && mv "$RUN/STATE.tmp" "$RUN/STATE.md"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "legacy_diagnosis_approval_fallback_opens"
sed -i.bak 's/Mode: fix/Mode: feature/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "legacy_approval_mode_change_stays_closed"
assert_contains "$out" "fix_approval_mode_changed" "legacy_mode_change_closes_detail"
sed -i.bak 's/Mode: feature/Mode: fix/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
sed -i.bak 's/source=current-run/source=prior-chat/' "$RUN/DIAGNOSIS.md" && rm "$RUN/DIAGNOSIS.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "prior_chat_fix_approval_closes"
assert_contains "$out" "fix_approval_not_current_run" "prior_chat_fix_approval_detail"

sed -i.bak 's/Flow schema: 3/Flow schema: 2/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
rm "$RUN/DIAGNOSIS.md"
out="$(record_fix_approval)"
assert_field "$out" 2 CLOSED "schema2_cannot_record_new_fix_approval"
assert_contains "$out" "fix_approval_schema_unsupported" "schema2_record_fix_approval_detail"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "schema2_fix_keeps_legacy_phase1_contract"
assert_contains "$out" "intent_evidence_missing" "schema2_fix_legacy_contract_detail"
printf '%s\n' '<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=yes source=current-run -->' >> "$RUN/PROBLEM.md"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "schema2_fix_resume_needs_no_new_approval_marker"

if command -v jq >/dev/null 2>&1; then
  reset_run
  write_phase_fixture
  printf 'Phase reads required: yes\n' >> "$RUN/STATE.md"
  out="$(run_gate)"
  assert_field "$out" 2 CLOSED "phase_reads_missing_closes_clarify"
  assert_contains "$out" "phase_0_read_missing" "phase_reads_missing_clarify_detail"

  record_phase 0
  record_phase 1
  out="$(run_gate)"
  assert_field "$out" 2 OPEN "phase_reads_fresh_open_clarify"

  reset_run
  write_phase_fixture
  mkdir -p "$WORK/.kimiflow/session"
  printf '{"run":".kimiflow/demo","phase_reads_required":true}\n' > "$WORK/.kimiflow/session/ACTIVE_RUN.json"
  out="$(run_gate)"
  assert_field "$out" 2 CLOSED "active_json_phase_reads_missing_closes_clarify"
  assert_contains "$out" "phase_0_read_missing" "active_json_phase_reads_missing_clarify_detail"
else
  pass "phase_reads_clarify_skipped_without_jq"
fi

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
