#!/usr/bin/env bash
# kimiflow — unit tests for plan-blocker-gate.sh.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/plan-blocker-gate.sh"
ACTIVE="$(cd "$(dirname "$0")" && pwd)/active-run.sh"
LIB="$(cd "$(dirname "$0")" && pwd)/kimiflow-lib.sh"
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
Scope: small
Discovery required: yes
Affected files: src/feature.ts, tests/feature.test.ts
Phase 0: done
Phase 1: done
Phase 2: done
Phase 3: done
Phase 4: open
EOF
  cat > "$RUN/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=yes source=current-run -->
Build a small feature with observable output.
EOF
  cat > "$RUN/RESEARCH.md" <<'EOF'
# Research
<!-- kimiflow:discovery depth=pulse status=sufficient lanes=complete claims=none technical_gaps=0 user_decisions=0 scope_change=no -->
## Existing implementation
Existing implementation lives in src/feature.ts:12 and tests in tests/feature.test.ts:4.
EOF
  cat > "$RUN/PLAN.md" <<'EOF'
# Plan
- Update src/feature.ts for AC-1.
- Add tests/feature.test.ts for AC-1.
EOF
  cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
- AC-1 -> feature_acceptance_test: Given input "x", the output is "done:x".
EOF
}

run_gate() { KIMIFLOW_PLUGIN_ROOT="$WORK" "$SCRIPT" "$RUN"; }

write_phase_fixture() {
  mkdir -p "$WORK/phases"
  cat > "$WORK/phases/PHASES.json" <<'EOF'
{"schema_version":1,"phases":[
{"id":0,"name":"p0","file":"phases/phase-0.md"},
{"id":1,"name":"p1","file":"phases/phase-1.md"},
{"id":2,"name":"p2","file":"phases/phase-2.md"},
{"id":3,"name":"p3","file":"phases/phase-3.md"},
{"id":4,"name":"p4","file":"phases/phase-4.md"}
]}
EOF
  for i in 0 1 2 3 4; do
    printf 'phase %s\n' "$i" > "$WORK/phases/phase-$i.md"
  done
}

record_phase() {
  KIMIFLOW_PLUGIN_ROOT="$WORK" "$ACTIVE" phase-read --root "$WORK" --run .kimiflow/demo --phase "$1" --file "phases/phase-$1.md" --write >/dev/null
}

reset_run
out="$(run_gate)"
assert_field "$out" 2 OPEN "clean_plan_opens"
assert_contains "$out" "reason=clean" "clean_reason"

# Runs created before Architecture Contract remain resumable.
assert_field "$out" 2 OPEN "legacy_run_without_architecture_contract_opens"

enable_active_architecture() {
  cat >> "$RUN/STATE.md" <<'EOF'
Architecture contract: 1
Architecture deliberation: active
EOF
  cat >> "$RUN/RESEARCH.md" <<'EOF'
<!-- kimiflow:architecture-deliberation status=active approaches=2 principles=2 critique=1 user_gate=no -->
## Adaptive Architecture Deliberation
Problem behind request: The current split may not fit the required data flow.
Operating envelope: One local process today, bounded growth, reversible storage boundary.
Architecture status: evolve
Quality drivers: correctness, reversibility, and local operation.
Project principles:
- Type: invariant; Scope: src/**; Rule: Writes use one transaction boundary; Evidence: tests/transaction.test.ts.
- Type: preference; Scope: src/**; Rule: Prefer the existing module boundary; Evidence: src/feature.ts:12.
Preferred approach: Evolve the current module boundary.
Strongest alternative: Replace the module with a service.
Trade-off / debt: Keep one adapter until the growth trigger is observed.
Reversibility / evolution trigger: Replace only after measured contention.
Falsification check: Run architecture_contract_test.
EOF
  cat >> "$RUN/PLAN.md" <<'EOF'
Architecture fit: active
Architecture decision: Evolve the existing boundary.
Architecture evidence: RESEARCH.md §Adaptive Architecture Deliberation
Architecture check: AC-1 -> architecture_contract_test
EOF
}

reset_run
cat >> "$RUN/STATE.md" <<'EOF'
Architecture contract: 1
Architecture deliberation: pending
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "architecture_pending_closes"
assert_contains "$out" "architecture_deliberation_pending" "architecture_pending_detail"

reset_run
enable_active_architecture
out="$(run_gate)"
assert_field "$out" 2 OPEN "valid_active_architecture_contract_opens"

for mutation in 'approaches=2 approaches=3 architecture_approach_count_invalid' \
                'principles=2 principles=4 architecture_principle_count_invalid' \
                'critique=1 critique=2 architecture_critique_count_invalid'; do
  set -- $mutation
  reset_run
  enable_active_architecture
  sed -i.bak "s/$1/$2/" "$RUN/RESEARCH.md" && rm "$RUN/RESEARCH.md.bak"
  out="$(run_gate)"
  assert_field "$out" 2 CLOSED "architecture_${3}_closes"
  assert_contains "$out" "$3" "architecture_${3}_detail"
done

reset_run
enable_active_architecture
printf '%s\n' '<!-- kimiflow:architecture-deliberation status=off approaches=0 principles=0 critique=0 user_gate=no -->' >> "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "architecture_duplicate_marker_closes"
assert_contains "$out" "architecture_marker_count_invalid" "architecture_duplicate_marker_detail"

reset_run
enable_active_architecture
sed -i.bak 's/principles=2/principles=3/' "$RUN/RESEARCH.md" && rm "$RUN/RESEARCH.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "architecture_principle_marker_mismatch_closes"
assert_contains "$out" "architecture_principle_marker_mismatch" "architecture_principle_mismatch_detail"

reset_run
enable_active_architecture
sed -i.bak 's/user_gate=no/user_gate=yes/' "$RUN/RESEARCH.md" && rm "$RUN/RESEARCH.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "architecture_user_gate_closes"
assert_contains "$out" "architecture_user_gate_forbidden" "architecture_user_gate_detail"

reset_run
enable_active_architecture
sed -i.bak '/^Architecture check:/d' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "architecture_missing_plan_check_closes"
assert_contains "$out" "architecture_plan_check_missing" "architecture_missing_plan_check_detail"

reset_run
enable_active_architecture
sed -i.bak 's/Architecture check: AC-1/Architecture check: AC-9/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "architecture_check_requires_acceptance_ac"
assert_contains "$out" "architecture_check_ac_missing:AC-9" "architecture_check_ac_detail"

reset_run
enable_active_architecture
for _ in $(seq 1 460); do printf 'budgetword ' >> "$RUN/RESEARCH.md"; done
printf '\n' >> "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "architecture_note_budget_closes"
assert_contains "$out" "architecture_note_over_budget" "architecture_note_budget_detail"

reset_run
cat >> "$RUN/STATE.md" <<'EOF'
Architecture contract: 1
Architecture deliberation: off
EOF
cat >> "$RUN/RESEARCH.md" <<'EOF'
<!-- kimiflow:architecture-deliberation status=off approaches=0 principles=0 critique=0 user_gate=no -->
Architecture off reason: local reversible change.
EOF
printf 'Architecture fit: off — local reversible change\n' >> "$RUN/PLAN.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "valid_off_architecture_contract_opens"

printf '%s\n' '## Adaptive Architecture Deliberation' >> "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "architecture_off_note_closes"
assert_contains "$out" "architecture_off_note_forbidden" "architecture_off_note_detail"

reset_run
sed -i.bak -e 's/Mode: feature/Mode: fix/' -e 's/Discovery required: yes/Discovery required: no/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
printf 'Flow schema: 3\n' >> "$RUN/STATE.md"
mv "$RUN/INTENT.md" "$RUN/PROBLEM.md"
sed -i.bak '/kimiflow:clarify-evidence/d' "$RUN/PROBLEM.md" && rm "$RUN/PROBLEM.md.bak"
mv "$RUN/RESEARCH.md" "$RUN/DIAGNOSIS.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "fix_plan_reaches_internal_review_without_early_approval"

reset_run
sed '/kimiflow:discovery/d' "$RUN/RESEARCH.md" > "$RUN/RESEARCH.tmp" && mv "$RUN/RESEARCH.tmp" "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "plan_gate_requires_discovery_evidence"
assert_contains "$out" "discovery_gate_closed:discovery_marker_missing" "plan_gate_requires_discovery_detail"

reset_run
cat > "$RUN/STATE.md" <<'EOF'
- **Status:** active
- **Mode:** feature
- **Scope:** small
- **Affected files:**
  - src/feature.ts
  - tests/feature.test.ts
- **Phase 0:** done
- **Phase 1:** done
- **Phase 2:** done
- **Phase 3:** done
- **Phase 4:** open
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "markdown_state_affected_files_opens"

reset_run
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
AC-1 -- When input "x" is processed, the system shall return "done:x".
Example: "x" -> "done:x".
Check: automated test feature_acceptance_test (exit 0) -> AC-1
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "multiline_acceptance_with_check_opens"

reset_run
sed '/kimiflow:clarify-evidence/d' "$RUN/INTENT.md" > "$RUN/INTENT.tmp" && mv "$RUN/INTENT.tmp" "$RUN/INTENT.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "plan_gate_requires_small_micro_grill"
assert_contains "$out" "clarify_gate_closed:intent_evidence_missing" "plan_gate_requires_small_micro_grill_detail"

reset_run
FAKE_HOOKS="$WORK/fake-hooks-discovery-missing"
mkdir -p "$FAKE_HOOKS"
cp "$SCRIPT" "$FAKE_HOOKS/plan-blocker-gate.sh"
cp "$LIB" "$FAKE_HOOKS/kimiflow-lib.sh"
cp "$(dirname "$SCRIPT")/clarify-gate.sh" "$FAKE_HOOKS/clarify-gate.sh"
chmod +x "$FAKE_HOOKS/plan-blocker-gate.sh" "$FAKE_HOOKS/clarify-gate.sh"
out="$("$FAKE_HOOKS/plan-blocker-gate.sh" "$RUN")"
assert_field "$out" 2 CLOSED "plan_gate_blocks_missing_discovery_gate"
assert_contains "$out" "discovery_gate_missing" "plan_gate_blocks_missing_discovery_detail"

reset_run
FAKE_HOOKS="$WORK/fake-hooks-discovery-malformed"
mkdir -p "$FAKE_HOOKS"
cp "$SCRIPT" "$FAKE_HOOKS/plan-blocker-gate.sh"
cp "$LIB" "$FAKE_HOOKS/kimiflow-lib.sh"
cp "$(dirname "$SCRIPT")/clarify-gate.sh" "$FAKE_HOOKS/clarify-gate.sh"
cat > "$FAKE_HOOKS/discovery-gate.sh" <<'EOF'
#!/usr/bin/env bash
printf 'not a discovery verdict\n'
EOF
chmod +x "$FAKE_HOOKS/plan-blocker-gate.sh" "$FAKE_HOOKS/clarify-gate.sh" "$FAKE_HOOKS/discovery-gate.sh"
out="$("$FAKE_HOOKS/plan-blocker-gate.sh" "$RUN")"
assert_field "$out" 2 CLOSED "plan_gate_blocks_malformed_discovery"
assert_contains "$out" "discovery_gate_malformed" "plan_gate_blocks_malformed_discovery_detail"

reset_run
FAKE_HOOKS="$WORK/fake-hooks-discovery-error"
mkdir -p "$FAKE_HOOKS"
cp "$SCRIPT" "$FAKE_HOOKS/plan-blocker-gate.sh"
cp "$LIB" "$FAKE_HOOKS/kimiflow-lib.sh"
cp "$(dirname "$SCRIPT")/clarify-gate.sh" "$FAKE_HOOKS/clarify-gate.sh"
cat > "$FAKE_HOOKS/discovery-gate.sh" <<'EOF'
#!/usr/bin/env bash
exit 2
EOF
chmod +x "$FAKE_HOOKS/plan-blocker-gate.sh" "$FAKE_HOOKS/clarify-gate.sh" "$FAKE_HOOKS/discovery-gate.sh"
out="$("$FAKE_HOOKS/plan-blocker-gate.sh" "$RUN")"
assert_field "$out" 2 CLOSED "plan_gate_blocks_discovery_crash"
assert_contains "$out" "discovery_gate_error" "plan_gate_blocks_discovery_crash_detail"

reset_run
FAKE_HOOKS="$WORK/fake-hooks"
mkdir -p "$FAKE_HOOKS"
cp "$SCRIPT" "$FAKE_HOOKS/plan-blocker-gate.sh"
cp "$LIB" "$FAKE_HOOKS/kimiflow-lib.sh"
cat > "$FAKE_HOOKS/clarify-gate.sh" <<'EOF'
#!/usr/bin/env bash
printf 'not a gate verdict\n'
EOF
chmod +x "$FAKE_HOOKS/plan-blocker-gate.sh" "$FAKE_HOOKS/clarify-gate.sh"
out="$("$FAKE_HOOKS/plan-blocker-gate.sh" "$RUN")"
assert_field "$out" 2 CLOSED "plan_gate_blocks_malformed_clarify_output"
assert_contains "$out" "clarify_gate_malformed" "plan_gate_blocks_malformed_clarify_detail"

reset_run
FAKE_HOOKS="$WORK/fake-hooks-error"
mkdir -p "$FAKE_HOOKS"
cp "$SCRIPT" "$FAKE_HOOKS/plan-blocker-gate.sh"
cp "$LIB" "$FAKE_HOOKS/kimiflow-lib.sh"
cat > "$FAKE_HOOKS/clarify-gate.sh" <<'EOF'
#!/usr/bin/env bash
exit 2
EOF
chmod +x "$FAKE_HOOKS/plan-blocker-gate.sh" "$FAKE_HOOKS/clarify-gate.sh"
out="$("$FAKE_HOOKS/plan-blocker-gate.sh" "$RUN")"
assert_field "$out" 2 CLOSED "plan_gate_blocks_clarify_crash"
assert_contains "$out" "clarify_gate_error" "plan_gate_blocks_clarify_crash_detail"

reset_run
printf '\n- TODO: choose the real implementation later.\n' >> "$RUN/PLAN.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "todo_plan_closes"
assert_contains "$out" "plan_contains_unresolved_marker" "todo_detail"

reset_run
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
- AC-1: The feature works.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "missing_acceptance_verification_closes"
assert_contains "$out" "acceptance_missing_verification:AC-1" "missing_acceptance_verification_detail"

reset_run
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
- Implement the feature.
- Update src/feature.ts for AC-10.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "missing_ac_plan_mapping_closes"
assert_contains "$out" "acceptance_not_mapped_to_plan:AC-1" "missing_ac_plan_mapping_detail"

reset_run
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
- AC-1: Given input "x", the output is "done:x".
- AC-10 -> other_feature_test: Given input "y", the output is "done:y".
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "ac_token_match_does_not_confuse_ac1_with_ac10"
assert_contains "$out" "acceptance_missing_verification:AC-1" "ac_token_missing_verification_detail"

reset_run
cat > "$RUN/RESEARCH.md" <<'EOF'
# Research
The codebase supports this feature.
EOF
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
- Implement AC-1.
EOF
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
- AC-1 -> feature_acceptance_test: Given input "x", the output is "done:x".
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "missing_path_evidence_closes"
assert_contains "$out" "no_code_or_artifact_path_evidence" "missing_path_evidence_detail"

reset_run
cat > "$RUN/RESEARCH.md" <<'EOF'
# Research
Stale implementation reference: src/stale.ts:1.
EOF
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
- Implement AC-1.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "research_only_path_evidence_does_not_open"
assert_contains "$out" "no_code_or_artifact_path_evidence" "research_only_path_evidence_detail"

reset_run
grep -v '^Affected files:' "$RUN/STATE.md" > "$RUN/STATE.tmp" && mv "$RUN/STATE.tmp" "$RUN/STATE.md"
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
- Update src/feature.ts for AC-1.
- Files are affected by this plan.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "missing_affected_files_closes"
assert_contains "$out" "affected_files_not_declared" "missing_affected_files_detail"

reset_run
grep -v '^Affected files:' "$RUN/STATE.md" > "$RUN/STATE.tmp" && mv "$RUN/STATE.tmp" "$RUN/STATE.md"
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
Affected files: src/feature.ts, tests/feature.test.ts
- Update src/feature.ts for AC-1.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "plan_affected_files_with_paths_opens"

reset_run
grep -v '^Affected files:' "$RUN/STATE.md" > "$RUN/STATE.tmp" && mv "$RUN/STATE.tmp" "$RUN/STATE.md"
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
- **Affected files:**
  - src/feature.ts
  - tests/feature.test.ts
- Update src/feature.ts for AC-1.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "markdown_plan_affected_files_with_paths_opens"

reset_run
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
- Update Dockerfile for AC-1.
EOF
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
- AC-1 -> dockerfile_smoke: Given the image build runs, Dockerfile builds successfully.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "extensionless_project_file_path_opens"

reset_run
cat >> "$RUN/STATE.md" <<'EOF'
Flow schema: 4
Conformance contract: 1
Conformance basis: pending
EOF
sed -i.bak 's/<!-- kimiflow:clarify-evidence .* -->/<!-- kimiflow:clarify-evidence behavior=confirmed scope=confirmed outcome=confirmed authority=explicit summary=present source=current-run -->/' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "conformance_plan_contract_missing_closes"
assert_contains "$out" "conformance_plan_gate_closed" "conformance_plan_contract_missing_detail"
cat >> "$RUN/PLAN.md" <<'EOF'
<!-- kimiflow:decision-contract contract=1 decisions=1 -->
Decision D1: Keep the feature behavior complete.
Evidence D1: RESEARCH.md §Existing implementation
Invariant D1: The implementation and tests change together.
Paths D1: src/feature.ts, tests/feature.test.ts
AC D1: AC-1
Check D1: command :: test -s src/feature.ts
Recheck D1: Re-run after feature paths or behavior change.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "conformance_plan_contract_valid_opens"

FAKE_HOOKS="$WORK/fake-hooks-conformance"
mkdir -p "$FAKE_HOOKS"
cp "$SCRIPT" "$FAKE_HOOKS/plan-blocker-gate.sh"
cp "$LIB" "$FAKE_HOOKS/kimiflow-lib.sh"
cp "$(dirname "$SCRIPT")/clarify-gate.sh" "$FAKE_HOOKS/clarify-gate.sh"
cp "$(dirname "$SCRIPT")/discovery-gate.sh" "$FAKE_HOOKS/discovery-gate.sh"
cat > "$FAKE_HOOKS/conformance-gate.sh" <<'EOF'
#!/usr/bin/env bash
printf 'NOT_CONFORMANCE\tOPEN\tblockers=0\treason=plan-clean\tdetail=\n'
EOF
chmod +x "$FAKE_HOOKS"/*.sh
out="$(KIMIFLOW_PLUGIN_ROOT="$WORK" "$FAKE_HOOKS/plan-blocker-gate.sh" "$RUN")"
assert_field "$out" 2 CLOSED "malformed_conformance_open_closes"
assert_contains "$out" "conformance_plan_gate_malformed" "malformed_conformance_open_detail"

cat > "$FAKE_HOOKS/conformance-gate.sh" <<'EOF'
#!/usr/bin/env bash
printf 'CONFORMANCE_GATE\tOPEN\tblockers=0\treason=plan-clean\tdetail=\n'
exit 23
EOF
chmod +x "$FAKE_HOOKS/conformance-gate.sh"
out="$(KIMIFLOW_PLUGIN_ROOT="$WORK" "$FAKE_HOOKS/plan-blocker-gate.sh" "$RUN")"
assert_field "$out" 2 CLOSED "crashed_conformance_open_closes"
assert_contains "$out" "conformance_plan_gate_error" "crashed_conformance_open_detail"

# --- Header-set consistency: every Affected header this gate accepts must also be
# visible to the staleness parser in hooks/kimiflow_core/active_run.py (keep in sync),
# or a plan passes the gate but finish wedges on stale_risk=unknown -----------------
for header in "Affected files" "Affected paths" "Files" "Paths" "Touches" "files"; do
  reset_run
  grep -v '^Affected files:' "$RUN/STATE.md" > "$RUN/STATE.tmp" && mv "$RUN/STATE.tmp" "$RUN/STATE.md"
  printf '%s: src/feature.ts, tests/feature.test.ts\n' "$header" >> "$RUN/STATE.md"
  out="$(run_gate)"
  assert_field "$out" 2 OPEN "header_${header// /_}_opens_gate"
  if command -v python3 >/dev/null 2>&1; then
    got="$(PYTHONPATH="$(dirname "$SCRIPT")" python3 -c 'import sys; from kimiflow_core.active_run import affected_paths; print(",".join(affected_paths(sys.argv[1])))' "$RUN/STATE.md")"
    if [ "$got" = "src/feature.ts,tests/feature.test.ts" ]; then
      pass "header_${header// /_}_visible_to_staleness"
    else
      fail "header_${header// /_}_visible_to_staleness (got '$got')"
    fi
  else
    pass "header_${header// /_}_staleness_check_skipped_without_python3"
  fi
done

# --- Audit-mode profile (finding C1: audit runs carry AUDIT-INTENT.md + AUDIT.md, not
# PLAN.md/ACCEPTANCE.md; the gate must not hard-require plan artifacts or it deadlocks) ---
reset_audit() {
  rm -rf "$WORK"; mkdir -p "$RUN"
  cat > "$RUN/STATE.md" <<'EOF'
Status: active
Mode: audit
Scope: small
Affected files: src/legacy.ts
Phase 4: open
EOF
  cat > "$RUN/AUDIT-INTENT.md" <<'EOF'
# Audit intent
<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=yes source=current-run -->
Remove dead code under src/legacy.ts; preserve behavior.
EOF
  cat > "$RUN/AUDIT.md" <<'EOF'
# Audit
## Slice 1: delete unused helper (~-40 lines)
- delete src/legacy.ts:88 oldHelper() — grep `oldHelper` repo-wide returns 0 callers.
## Do NOT touch
- src/legacy.ts:12 publicApi() — exported.
EOF
}

reset_audit
out="$(run_gate)"
assert_field "$out" 2 OPEN "audit_mode_opens_without_plan_acceptance"

reset_audit
cat >> "$RUN/STATE.md" <<'EOF'
Architecture contract: 1
Architecture deliberation: off
EOF
cat >> "$RUN/AUDIT.md" <<'EOF'
<!-- kimiflow:architecture-deliberation status=off approaches=0 principles=0 critique=0 user_gate=no -->
Architecture off reason: This is a read-only audit of the existing implementation.
EOF
out="$(run_gate)"
assert_field "$out" 2 OPEN "audit_architecture_off_opens_without_compatibility_plan"

# Audit without AUDIT.md → still blocked (understanding missing)
reset_audit; rm -f "$RUN/AUDIT.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "audit_mode_without_audit_md_blocks"

# Audit AUDIT.md without any path evidence → blocked
reset_audit
cat > "$RUN/AUDIT.md" <<'EOF'
# Audit
## Slice 1
- remove some old stuff that nobody uses anymore.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "audit_mode_without_path_evidence_blocks"

# Audit with a skipped micro-grill (clarify marker absent) → blocked by clarify recheck
reset_audit
cat > "$RUN/AUDIT-INTENT.md" <<'EOF'
# Audit intent
Remove dead code under src/legacy.ts; preserve behavior.
EOF
out="$(run_gate)"
assert_field "$out" 2 CLOSED "audit_mode_skipped_grill_blocks"

if command -v jq >/dev/null 2>&1; then
  reset_run
  write_phase_fixture
  printf 'Phase reads required: yes\n' >> "$RUN/STATE.md"
  out="$(run_gate)"
  assert_field "$out" 2 CLOSED "phase_reads_missing_closes_plan_gate"
  assert_contains "$out" "phase_0_read_missing" "phase_reads_missing_plan_detail"

  for i in 0 1 2 3 4; do
    record_phase "$i"
  done
  out="$(run_gate)"
  assert_field "$out" 2 OPEN "phase_reads_fresh_open_plan_gate"

  reset_run
  write_phase_fixture
  mkdir -p "$WORK/.kimiflow/session"
  printf '{"run":".kimiflow/demo","phase_reads_required":true}\n' > "$WORK/.kimiflow/session/ACTIVE_RUN.json"
  out="$(run_gate)"
  assert_field "$out" 2 CLOSED "active_json_phase_reads_missing_closes_plan_gate"
  assert_contains "$out" "phase_0_read_missing" "active_json_phase_reads_missing_plan_detail"
else
  pass "phase_reads_plan_gate_skipped_without_jq"
fi

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
