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
<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=yes source=current-run -->
Build a small feature after two user answers.
EOF
}

run_gate() { KIMIFLOW_PLUGIN_ROOT="$WORK" "$SCRIPT" "$RUN"; }

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
assert_field "$out" 2 OPEN "small_questions_marker_opens"
assert_contains "$out" "reason=clean" "small_questions_marker_reason"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
Build a small feature without documented clarification.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "small_missing_marker_closes"
assert_contains "$out" "micro_grill_evidence_missing" "small_missing_marker_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence mode=questions count=1 confirmed=yes source=current-run -->
Only one question was asked.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "one_question_closes"
assert_contains "$out" "micro_grill_too_short" "one_question_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=no source=current-run -->
Questions were asked but not confirmed.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "unconfirmed_questions_closes"
assert_contains "$out" "micro_grill_not_confirmed" "unconfirmed_questions_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=yes source=prior-chat -->
Loose prior discussion was mistaken for current-run clarification.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "prior_chat_source_closes"
assert_contains "$out" "micro_grill_not_current_run" "prior_chat_source_detail"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence mode=assumptions count=3 confirmed=yes source=current-run -->
The prompt already covered behavior, scope, and acceptance signal; user confirmed.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "confirmed_assumptions_open"

reset_run
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence mode=assumptions count=2 confirmed=yes source=current-run -->
Not enough assumptions were confirmed.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "incomplete_assumptions_close"
assert_contains "$out" "micro_grill_assumptions_incomplete" "incomplete_assumptions_detail"

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
