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

run_gate() { KIMIFLOW_PLUGIN_ROOT="$WORK" "$SCRIPT" "$RUN"; }
run_post_diagnosis_gate() { KIMIFLOW_PLUGIN_ROOT="$WORK" "$SCRIPT" "$RUN" --post-diagnosis; }

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
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "fix_preview_missing_approval_closes"
assert_contains "$out" "fix_approval_missing" "fix_preview_missing_approval_detail"

cat > "$RUN/DIAGNOSIS.md" <<'EOF'
# Diagnosis
<!-- kimiflow:fix-approval cause=confirmed fix=confirmed scope=confirmed risk=confirmed source=current-run -->
The user approved the verified cause, bounded fix, affected scope, and risk.
EOF
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 OPEN "complete_fix_preview_opens"

sed -i.bak 's/risk=confirmed/risk=pending/' "$RUN/DIAGNOSIS.md" && rm "$RUN/DIAGNOSIS.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "incomplete_fix_preview_closes"
assert_contains "$out" "fix_risk_unconfirmed" "incomplete_fix_preview_detail"

sed -i.bak -e 's/risk=pending/risk=confirmed/' -e 's/source=current-run/source=prior-chat/' "$RUN/DIAGNOSIS.md" && rm "$RUN/DIAGNOSIS.md.bak"
out="$(run_post_diagnosis_gate)"
assert_field "$out" 2 CLOSED "stale_fix_preview_approval_closes"
assert_contains "$out" "fix_approval_not_current_run" "stale_fix_preview_approval_detail"

sed -i.bak 's/Flow schema: 3/Flow schema: 2/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
rm "$RUN/DIAGNOSIS.md"
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
