#!/usr/bin/env bash
# kimiflow — tests for the adaptive implementation-conformance gate.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/conformance-gate.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
RUN="$REPO/.kimiflow/demo"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
field() { printf '%s\n' "$1" | cut -f"$2"; }
assert_status() {
  local out="$1" want="$2" label="$3" got
  got="$(field "$out" 2)"
  if [ "$got" = "$want" ]; then pass "$label"; else fail "$label (got '$got': $out)"; fi
}
assert_contains() {
  local out="$1" want="$2" label="$3"
  if printf '%s\n' "$out" | grep -qF "$want"; then pass "$label"; else fail "$label (missing '$want': $out)"; fi
}

git_repo() {
  git -C "$REPO" "$@"
}

reset_repo() {
  rm -rf "$REPO"
  mkdir -p "$RUN" "$REPO/src"
  git_repo init -q
  git_repo config user.email kimiflow@example.test
  git_repo config user.name "Kimiflow Test"
  printf '.kimiflow/\n' > "$REPO/.gitignore"
  printf 'base\n' > "$REPO/src/a.txt"
  git_repo add .gitignore src/a.txt
  git_repo commit -q -m base
  START="$(git_repo rev-parse HEAD)"
}

write_contract() {
  local scope="${1:-small}" verifier="folded"
  [ "$scope" = "large" ] && verifier="independent"
  cat > "$RUN/STATE.md" <<EOF
Flow schema: 4
Status: active
Mode: feature
Scope: $scope
Conformance contract: 1
Conformance basis: pending
Affected files:
- src/a.txt
Run started head: $START
Phase 0: done
Phase 1: done
Phase 2: done
Phase 3: done
Phase 4: done
Phase 5: done
Phase 6: in-progress
Phase 7: open
EOF
  cat > "$RUN/INTENT.md" <<'EOF'
# Intent
Keep the implementation aligned with the selected decision.
EOF
  cat > "$RUN/RESEARCH.md" <<'EOF'
# Research
## Decision evidence
The local strategy is current and testable.
EOF
  cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
## AC-1
When the implementation changes, the system shall preserve the decision invariant.
Example: changed file -> passing local check.
Verification: automated command test -s src/a.txt.
AC-1 -> decision_one_test
EOF
  cat > "$RUN/PLAN.md" <<'EOF'
# Plan
Affected files: src/a.txt
<!-- kimiflow:decision-contract contract=1 decisions=1 -->
## Implementation Decision Contract
Decision D1: Keep the final file nonempty.
Evidence D1: RESEARCH.md §Decision evidence
Invariant D1: The affected file contains substantive content.
Paths D1: src/a.txt
AC D1: AC-1
Check D1: command :: test -s src/a.txt
Recheck D1: Re-run after the file or plan changes.
- Update src/a.txt for AC-1.
EOF
  cat > "$RUN/VERIFICATION.md" <<EOF
# Verification
<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->
<!-- kimiflow:conformance contract=1 status=converged diff=passed strategy=passed architecture=not_applicable research=stable scope=passed decisions=1 checks=1 verifier=$verifier source=current-run -->
Decision check D1: passed :: test -s src/a.txt
EOF
  printf 'changed\n' > "$REPO/src/a.txt"
}

run_gate() { "$SCRIPT" "$RUN" "$@"; }

out="$("$SCRIPT" "$WORK/missing")"
assert_status "$out" CLOSED "missing_run_closes"
assert_contains "$out" "run_dir_missing" "missing_run_detail"

LEGACY="$WORK/nonrepo/.kimiflow/demo"
mkdir -p "$LEGACY"
printf 'Flow schema: 3\nMode: feature\nScope: small\n' > "$LEGACY/STATE.md"
out="$("$SCRIPT" "$LEGACY")"
assert_status "$out" OPEN "legacy_non_git_run_opens"
assert_contains "$out" "not-required" "legacy_non_git_reason"

reset_repo
cat > "$RUN/STATE.md" <<'EOF'
Flow schema: 4
Mode: feature
Scope: small
EOF
out="$(run_gate)"
assert_status "$out" OPEN "absent_contract_is_legacy_open"
assert_contains "$out" "not-required" "legacy_open_reason"

mkdir -p "$REPO/.kimiflow/session"
cat > "$REPO/.kimiflow/session/ACTIVE_RUN.json" <<EOF
{"run":".kimiflow/demo","mode":"feature","scope":"small","started_head":"$START","conformance_contract":"1"}
EOF
out="$(run_gate)"
assert_status "$out" CLOSED "removed_pinned_contract_closes"
assert_contains "$out" "active_conformance_contract_mismatch" "removed_pinned_contract_detail"

reset_repo
write_contract small
out="$(run_gate --plan)"
assert_status "$out" OPEN "valid_plan_contract_opens"

reset_repo
write_contract small
printf 'Intent contract: 3\n' >> "$RUN/STATE.md"
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
Requirement R1: Preserve the selected decision invariant.
Requirement R2: Produce current verification evidence.
EOF
mkdir -p "$REPO/.kimiflow/session"
python3 - "$RUN" "$REPO/.kimiflow/session/ACTIVE_RUN.json" "$START" <<'PY'
import datetime
import hashlib
import json
import pathlib
import sys

run = pathlib.Path(sys.argv[1])
active_path = pathlib.Path(sys.argv[2])
started_head = sys.argv[3]
intent = run / "INTENT.md"
intent_digest = "sha256:" + hashlib.sha256(intent.read_bytes()).hexdigest()
lock = {
    "schema_version": 1,
    "contract": 3,
    "intent_digest": intent_digest,
    "requirements": ["R1", "R2"],
    "locked_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
lock_path = run / "INTENT-LOCK.json"
lock_path.write_text(json.dumps(lock, sort_keys=True) + "\n", encoding="utf-8")
lock_digest = "sha256:" + hashlib.sha256(lock_path.read_bytes()).hexdigest()
active_path.write_text(json.dumps({
    "status": "active",
    "run": ".kimiflow/demo",
    "mode": "feature",
    "scope": "small",
    "started_head": started_head,
    "intent_contract": "3",
    "intent_lock_digest": lock_digest,
    "conformance_contract": "1",
}) + "\n", encoding="utf-8")
PY
cat >> "$RUN/ACCEPTANCE.md" <<'EOF'
Requirement trace R1: AC-1
Requirement trace R2: AC-1
EOF
out="$(run_gate --plan)"
assert_status "$out" OPEN "contract3_requirement_traces_open_plan"
sed -i.bak '/Requirement trace R2:/d' "$RUN/ACCEPTANCE.md" && rm "$RUN/ACCEPTANCE.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "contract3_missing_requirement_trace_closes"
assert_contains "$out" "requirement_trace_R2_missing" "contract3_missing_requirement_trace_detail"
printf 'Requirement trace R2: AC-1\n' >> "$RUN/ACCEPTANCE.md"
printf 'Requirement R1: passed :: test -s src/a.txt\n' >> "$RUN/VERIFICATION.md"
out="$(run_gate)"
assert_status "$out" CLOSED "contract3_missing_final_requirement_check_closes"
assert_contains "$out" "requirement_check_R2_missing" "contract3_missing_final_requirement_check_detail"
printf 'Requirement R2: passed :: conformance receipt\n' >> "$RUN/VERIFICATION.md"
out="$(run_gate --record)"
assert_status "$out" OPEN "contract3_all_requirement_checks_record_basis"
sed -i.bak '/Requirement R2:/d' "$RUN/INTENT.md" && rm "$RUN/INTENT.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "contract3_intent_lock_drift_closes"
assert_contains "$out" "intent_lock_stale" "contract3_intent_lock_drift_detail"
printf 'Requirement R2: Produce current verification evidence.\n' >> "$RUN/INTENT.md"
sed -i.bak 's/Intent contract: 3/Intent contract: 2/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "contract3_state_downgrade_closes"
assert_contains "$out" "active_intent_contract_mismatch" "contract3_state_downgrade_detail"

cat > "$RUN/PLAN.md" <<'EOF'
# Plan
```markdown
<!-- kimiflow:decision-contract contract=1 decisions=1 -->
Decision D1: Example only.
Evidence D1: RESEARCH.md §Decision evidence
Invariant D1: Example invariant.
Paths D1: src/a.txt
AC D1: AC-1
Check D1: command :: test -s src/a.txt
Recheck D1: Example recheck.
```
EOF
out="$(run_gate --plan)"
assert_status "$out" CLOSED "fenced_decision_contract_is_not_operative"
assert_contains "$out" "decision_marker_missing" "fenced_decision_contract_detail"

reset_repo
write_contract small
python3 - "$RUN/PLAN.md" <<'PY'
import pathlib
import sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
start = text.index("Decision D1:")
end = text.index("\n- Update", start)
path.write_text(text[:start] + "<!--\n" + text[start:end] + "\n-->" + text[end:])
PY
out="$(run_gate --plan)"
assert_status "$out" CLOSED "commented_decision_rows_are_not_operative"
assert_contains "$out" "decision_D1_missing" "commented_decision_rows_detail"

reset_repo
write_contract small
sed -i.bak 's/Evidence D1: RESEARCH.md/Evidence D1: NOTRESEARCH.md/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "lookalike_evidence_source_closes"
assert_contains "$out" "evidence_D1_source_mismatch" "lookalike_evidence_source_detail"

reset_repo
write_contract small
sed -i.bak 's/Evidence D1: RESEARCH.md §Decision evidence/Evidence D1: RESEARCH.md/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "evidence_without_locator_closes"
assert_contains "$out" "evidence_D1_source_mismatch" "evidence_without_locator_detail"

reset_repo
write_contract small
sed -i.bak '/^Recheck D1:/d' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "missing_plan_field_closes"
assert_contains "$out" "recheck_D1_missing" "missing_plan_field_detail"

reset_repo
write_contract small
sed -i.bak 's/Decision D1: Keep the final file nonempty./Decision D1:   /' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "empty_semantic_decision_closes"
assert_contains "$out" "decision_D1_empty" "empty_semantic_decision_detail"

reset_repo
write_contract small
sed -i.bak 's/§Decision evidence/§Missing evidence/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "missing_evidence_section_closes"
assert_contains "$out" "evidence_D1_section_missing" "missing_evidence_section_detail"

reset_repo
write_contract small
sed -i.bak 's/decisions=1/decisions=6/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "decision_cap_closes"
assert_contains "$out" "decision_count_invalid" "decision_cap_detail"

reset_repo
write_contract small
sed -i.bak 's/Paths D1: src\/a.txt/Paths D1: src\/missing.txt/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "undeclared_decision_path_closes"
assert_contains "$out" "path_D1_not_affected" "undeclared_decision_path_detail"

reset_repo
write_contract small
sed -i.bak 's/AC D1: AC-1/AC D1: AC-9/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
printf 'Trace note mentions AC-9 but does not declare it.\n' >> "$RUN/ACCEPTANCE.md"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "missing_decision_ac_closes"
assert_contains "$out" "ac_D1_missing" "missing_decision_ac_detail"

reset_repo
write_contract small
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance

    AC-1: example only
EOF
out="$(run_gate --plan)"
assert_status "$out" CLOSED "indented_code_ac_is_not_operative"
assert_contains "$out" "ac_D1_missing" "indented_code_ac_detail"

reset_repo
write_contract small
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
## AC-1
Verification: automated command exits zero.
AC-1 -> decision_one_test
EOF
out="$(run_gate --plan)"
assert_status "$out" CLOSED "heading_plus_trace_is_not_substantive_ac"
assert_contains "$out" "ac_D1_missing" "heading_plus_trace_detail"

reset_repo
write_contract small
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
AC-1 -> decision_one_test
EOF
out="$(run_gate --plan)"
assert_status "$out" CLOSED "trace_only_ac_does_not_declare_criterion"
assert_contains "$out" "ac_D1_missing" "trace_only_ac_detail"

reset_repo
write_contract small
printf 'Decision D1:\n' >> "$RUN/PLAN.md"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "empty_duplicate_decision_field_closes"
assert_contains "$out" "decision_D1_duplicate" "empty_duplicate_decision_detail"

reset_repo
write_contract small
sed -i.bak 's/verifier=folded/verifier=independent/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "small_independent_receipt_closes"
assert_contains "$out" "verifier_scope_mismatch" "small_independent_detail"

reset_repo
write_contract small
sed -i.bak 's/source=current-run/source=current_run/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "noncanonical_receipt_source_closes"
assert_contains "$out" "conformance_receipt_malformed" "noncanonical_receipt_source_detail"

reset_repo
write_contract large
sed -i.bak 's/verifier=independent/verifier=folded/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "large_folded_receipt_closes"
assert_contains "$out" "verifier_scope_mismatch" "large_folded_detail"

reset_repo
write_contract small
sed -i.bak 's/outcome=passed criteria=passed regression=passed/outcome=failed criteria=passed regression=passed/; s/status=converged/status=research_stale/; s/research=stable/research=stale/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "research_stale_receipt_closes"
assert_contains "$out" "route_phase_2" "research_stale_routes_phase2"

reset_repo
write_contract small
sed -i.bak 's/outcome=passed criteria=passed regression=passed/outcome=failed criteria=failed regression=passed/; s/status=converged/status=code_gap/; s/diff=passed/diff=failed/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "code_gap_receipt_closes"
assert_contains "$out" "route_phase_5" "code_gap_routes_phase5"

reset_repo
write_contract small
printf 'Architecture deliberation: active\n' >> "$RUN/STATE.md"
sed -i.bak \
  -e 's/outcome=passed criteria=passed regression=passed/outcome=failed criteria=failed regression=passed/' \
  -e 's/status=converged/status=code_gap/' \
  -e 's/diff=passed/diff=failed/' \
  -e 's/strategy=passed/strategy=failed/' \
  -e 's/architecture=not_applicable/architecture=failed/' \
  "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "phase2_failure_overrides_code_gap"
assert_contains "$out" "conformance_phase_2_precedence" "phase2_failure_precedence_detail"
if printf '%s\n' "$out" | grep -qF 'route_phase_5'; then fail "phase2_failure_has_no_phase5_route"; else pass "phase2_failure_has_no_phase5_route"; fi

reset_repo
write_contract small
printf 'Architecture deliberation: active\n' >> "$RUN/STATE.md"
sed -i.bak \
  -e 's/outcome=passed criteria=passed regression=passed/outcome=failed criteria=failed regression=passed/' \
  -e 's/status=converged/status=code_gap/' \
  -e 's/diff=passed/diff=failed/' \
  "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "active_architecture_cannot_be_not_applicable_during_recovery"
assert_contains "$out" "architecture_status_mismatch" "active_architecture_recovery_dimension_detail"
if printf '%s\n' "$out" | grep -qF 'route_phase_5'; then fail "invalid_architecture_dimension_has_no_route"; else pass "invalid_architecture_dimension_has_no_route"; fi

reset_repo
write_contract small
sed -i.bak \
  -e 's/outcome=passed criteria=passed regression=passed/outcome=failed criteria=failed regression=passed/' \
  -e 's/status=converged/status=code_gap/' \
  -e 's/diff=passed/diff=failed/' \
  -e 's/Decision check D1: passed/Decision check D1: failed/' \
  "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "failed_generic_receipt_reaches_recovery"
assert_contains "$out" "route_phase_5" "failed_generic_receipt_routes_phase5"

reset_repo
write_contract large
sed -i.bak \
  -e 's/status=converged/status=code_gap/' \
  -e 's/diff=passed/diff=failed/' \
  -e 's/decisions=1 checks=1 verifier=independent/decisions=999 checks=999 verifier=folded/' \
  -e '/^Decision check D1:/d' \
  "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "tampered_recovery_receipt_does_not_route"
assert_contains "$out" "decision_check_count_mismatch" "tampered_recovery_count_detail"
if printf '%s\n' "$out" | grep -qF 'route_phase_5'; then fail "tampered_recovery_has_no_route"; else pass "tampered_recovery_has_no_route"; fi

reset_repo
write_contract small
sed -i.bak 's/status=converged/status=code_gap/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "inconsistent_drift_receipt_closes"
assert_contains "$out" "conformance_status_inconsistent" "inconsistent_drift_receipt_detail"

reset_repo
write_contract small
sed -i.bak 's/Decision check D1: passed :: test -s src\/a.txt/Decision check D1: passed :: test -f src\/a.txt/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "check_command_mismatch_closes"
assert_contains "$out" "check_D1_mismatch" "check_command_mismatch_detail"

reset_repo
write_contract small
out="$(run_gate --record --write)"
assert_status "$out" OPEN "small_folded_basis_records"
assert_contains "$out" "basis-recorded" "basis_record_reason"
if grep -Eq '^Conformance basis: [0-9a-f]{64}$' "$RUN/STATE.md"; then pass "basis_written_to_state"; else fail "basis_written_to_state"; fi

out="$(run_gate)"
assert_status "$out" CLOSED "normal_gate_requires_phase6_done"
assert_contains "$out" "phase_6_not_done" "phase6_done_detail"

sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_status "$out" OPEN "current_small_receipt_opens"
out="$(run_gate --finish)"
assert_status "$out" CLOSED "finish_requires_delivered_tree"
assert_contains "$out" "affected_index_worktree_mismatch" "finish_unstaged_detail"

printf 'changed again\n' > "$REPO/src/a.txt"
out="$(run_gate)"
assert_status "$out" CLOSED "changed_content_stales_basis"
assert_contains "$out" "conformance_basis_stale" "changed_content_stale_detail"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "completed_phase_cannot_rerecord_changed_basis"
assert_contains "$out" "phase_6_status_invalid" "completed_phase_rerecord_detail"

reset_repo
write_contract small
printf 'extra\n' > "$REPO/src/extra.txt"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "extra_untracked_path_closes"
assert_contains "$out" "affected_files_mismatch" "extra_untracked_detail"

reset_repo
write_contract small
printf 'hidden commit\n' > "$REPO/src/hidden.txt"
git_repo add src/hidden.txt
git_repo commit -q -m hidden-layer
rm "$REPO/src/hidden.txt"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "hidden_committed_delta_closes"
assert_contains "$out" "affected_files_mismatch" "hidden_committed_delta_detail"

reset_repo
write_contract small
out="$(run_gate --record --write)"
assert_status "$out" OPEN "precommit_basis_records"
sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
before="$(sed -n 's/^Conformance basis: //p' "$RUN/STATE.md")"
out="$(run_gate --finish)"
assert_status "$out" CLOSED "precommit_finish_closes"
git_repo add src/a.txt
out="$(run_gate --finish)"
assert_status "$out" CLOSED "staged_precommit_finish_closes"
assert_contains "$out" "delivery_head_index_mismatch" "staged_precommit_detail"
git_repo commit -q -m implementation
after_out="$(run_gate)"
after="$(sed -n 's/^Conformance basis: //p' "$RUN/STATE.md")"
assert_status "$after_out" OPEN "postcommit_identical_content_opens"
out="$(run_gate --finish)"
assert_status "$out" OPEN "postcommit_delivered_tree_opens"
if [ "$before" = "$after" ]; then pass "basis_is_commit_location_invariant"; else fail "basis_is_commit_location_invariant"; fi

reset_repo
write_contract small
git_repo add src/a.txt
printf 'different worktree bytes\n' > "$REPO/src/a.txt"
out="$(run_gate --record --write)"
assert_status "$out" OPEN "record_allows_unstaged_phase6_bytes"
sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
git_repo commit -q -m staged-version
out="$(run_gate)"
assert_status "$out" OPEN "basis_remains_content_bound_after_other_index_commit"
out="$(run_gate --finish)"
assert_status "$out" CLOSED "finish_rejects_committed_worktree_split"
assert_contains "$out" "affected_index_worktree_mismatch" "committed_worktree_split_detail"

reset_repo
write_contract large
out="$(run_gate --record --write)"
assert_status "$out" OPEN "large_independent_basis_records"
sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_status "$out" OPEN "large_independent_receipt_opens"

reset_repo
write_contract small
printf 'Architecture deliberation: active\n' >> "$RUN/STATE.md"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "active_architecture_requires_passed_receipt"
assert_contains "$out" "architecture_status_mismatch" "active_architecture_receipt_detail"
sed -i.bak 's/architecture=not_applicable/architecture=passed/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" OPEN "active_architecture_passed_receipt_opens"

sed -i.bak 's/architecture=passed/architecture=failed/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "converged_active_architecture_failure_closes"
assert_contains "$out" "architecture_status_mismatch" "converged_active_architecture_failure_detail"

reset_repo
write_contract small
printf 'Architecture deliberation: off\n' >> "$RUN/STATE.md"
sed -i.bak \
  -e 's/outcome=passed criteria=passed regression=passed/outcome=failed criteria=failed regression=passed/' \
  -e 's/status=converged/status=architecture_falsified/' \
  -e 's/architecture=not_applicable/architecture=failed/' \
  "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "architecture_off_cannot_falsify_architecture"
assert_contains "$out" "architecture_status_inconsistent" "architecture_off_falsifier_detail"
if printf '%s\n' "$out" | grep -qF 'route_phase_2'; then fail "architecture_off_has_no_route"; else pass "architecture_off_has_no_route"; fi

mkdir -p "$REPO/.kimiflow/session"
cat > "$REPO/.kimiflow/session/ACTIVE_RUN.json" <<EOF
{"run":".kimiflow/demo","mode":"feature","scope":"large","started_head":"$START"}
EOF
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "active_run_selector_mismatch_closes"
assert_contains "$out" "active_scope_mismatch" "active_run_selector_mismatch_detail"

reset_repo
write_contract small
mkdir -p "$REPO/.kimiflow/session"
cat > "$REPO/.kimiflow/session/ACTIVE_RUN.json" <<EOF
{"run":".kimiflow/demo","mode":"feature","scope":"small","started_head":"$START","run_device":0,"run_inode":0}
EOF
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "active_run_inode_mismatch_closes"
assert_contains "$out" "active_run_identity_mismatch" "active_run_inode_mismatch_detail"

reset_repo
git_repo update-index --add --cacheinfo "160000,$START,vendor/raw"
git_repo commit -q -m raw-gitlink
START="$(git_repo rev-parse HEAD)"
write_contract small
cat > "$REPO/.gitmodules" <<'EOF'
[submodule "other"]
	path = vendor/other
	url = ../other
EOF
sed -i.bak '/^- src\/a.txt/a\
- .gitmodules' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" OPEN "clean_raw_uninitialized_gitlink_is_not_phantom_delta"

reset_repo
mv "$REPO/src/a.txt" "$REPO/src/b.txt"
cat > "$RUN/STATE.md" <<EOF
Flow schema: 4
Status: active
Mode: feature
Scope: small
Conformance contract: 1
Conformance basis: pending
Affected files:
- src/a.txt
- src/b.txt
Run started head: $START
Phase 0: done
Phase 1: done
Phase 2: done
Phase 3: done
Phase 4: done
Phase 5: done
Phase 6: in-progress
Phase 7: open
EOF
cat > "$RUN/INTENT.md" <<'EOF'
# Intent
Rename the implementation file without losing behavior.
EOF
cat > "$RUN/RESEARCH.md" <<'EOF'
# Research
## Decision evidence
Both rename sides belong to the final delta.
EOF
cat > "$RUN/ACCEPTANCE.md" <<'EOF'
# Acceptance
AC-1 -> rename_test: When the rename runs, src/b.txt shall exist.
EOF
cat > "$RUN/PLAN.md" <<'EOF'
# Plan
Affected files: src/a.txt, src/b.txt
<!-- kimiflow:decision-contract contract=1 decisions=1 -->
Decision D1: Preserve both rename sides in the run delta.
Evidence D1: RESEARCH.md §Decision evidence
Invariant D1: The old path is absent and the new path exists.
Paths D1: src/a.txt, src/b.txt
AC D1: AC-1
Check D1: command :: test ! -e src/a.txt && test -e src/b.txt
Recheck D1: Re-run after rename handling changes.
- Rename src/a.txt to src/b.txt for AC-1.
EOF
cat > "$RUN/VERIFICATION.md" <<'EOF'
# Verification
<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->
<!-- kimiflow:conformance contract=1 status=converged diff=passed strategy=passed architecture=not_applicable research=stable scope=passed decisions=1 checks=1 verifier=folded source=current-run -->
Decision check D1: passed :: test ! -e src/a.txt && test -e src/b.txt
EOF
out="$(run_gate --record --write)"
assert_status "$out" OPEN "rename_both_sides_record"
sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
git_repo add -A -- src
git_repo commit -q -m rename
out="$(run_gate)"
assert_status "$out" OPEN "rename_basis_stable_after_commit"

reset_repo
SUBREPO="$WORK/subrepo"
mkdir -p "$SUBREPO"
git -C "$SUBREPO" init -q
git -C "$SUBREPO" config user.email kimiflow@example.test
git -C "$SUBREPO" config user.name "Kimiflow Test"
printf 'one\n' > "$SUBREPO/value.txt"
git -C "$SUBREPO" add value.txt
git -C "$SUBREPO" commit -q -m one
git_repo -c protocol.file.allow=always submodule add -q "$SUBREPO" vendor/lib
git_repo add .gitmodules vendor/lib
git_repo commit -q -m submodule-base
START="$(git_repo rev-parse HEAD)"
write_contract small
printf 'deletion run\n' > "$REPO/src/a.txt"
sed -i.bak '/^- src\/a.txt/a\
- vendor/lib' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
printf 'two\n' > "$SUBREPO/value.txt"
git -C "$SUBREPO" add value.txt
git -C "$SUBREPO" commit -q -m two
git -C "$REPO/vendor/lib" -c protocol.file.allow=always fetch -q origin
git -C "$REPO/vendor/lib" checkout -q "$(git -C "$SUBREPO" rev-parse HEAD)"
out="$(run_gate --record --write)"
assert_status "$out" OPEN "changed_clean_gitlink_basis_records"
sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
git_repo add src/a.txt vendor/lib
git_repo commit -q -m gitlink-update
out="$(run_gate)"
assert_status "$out" OPEN "gitlink_basis_stable_after_commit"

START="$(git_repo rev-parse HEAD)"
write_contract small
sed -i.bak '/^- src\/a.txt/a\
- .gitmodules\
- vendor/lib' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
git -C "$REPO" config -f .gitmodules --remove-section submodule.vendor/lib
rm -rf "$REPO/vendor/lib"
out="$(run_gate --record --write)"
assert_status "$out" OPEN "unstaged_gitlink_deletion_records"
sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
git_repo add -A -- .gitmodules src vendor/lib
git_repo commit -q -m gitlink-delete
out="$(run_gate)"
assert_status "$out" OPEN "gitlink_deletion_basis_stable_after_commit"
out="$(run_gate --finish)"
assert_status "$out" OPEN "gitlink_deletion_delivery_opens"

printf '%s\n' '----'
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
