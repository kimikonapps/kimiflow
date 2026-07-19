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

reset_repo
cat > "$RUN/STATE.md" <<'EOF'
Flow schema: 4
Mode: feature
Scope: small
EOF
out="$(run_gate)"
assert_status "$out" OPEN "absent_contract_is_legacy_open"
assert_contains "$out" "not-required" "legacy_open_reason"

reset_repo
write_contract small
out="$(run_gate --plan)"
assert_status "$out" OPEN "valid_plan_contract_opens"

sed -i.bak '/^Recheck D1:/d' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
out="$(run_gate --plan)"
assert_status "$out" CLOSED "missing_plan_field_closes"
assert_contains "$out" "recheck_D1_missing" "missing_plan_field_detail"

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
out="$(run_gate --plan)"
assert_status "$out" CLOSED "missing_decision_ac_closes"
assert_contains "$out" "ac_D1_missing" "missing_decision_ac_detail"

reset_repo
write_contract small
sed -i.bak 's/verifier=folded/verifier=independent/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "small_independent_receipt_closes"
assert_contains "$out" "verifier_scope_mismatch" "small_independent_detail"

reset_repo
write_contract large
sed -i.bak 's/verifier=independent/verifier=folded/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "large_folded_receipt_closes"
assert_contains "$out" "verifier_scope_mismatch" "large_folded_detail"

reset_repo
write_contract small
sed -i.bak 's/status=converged/status=research_stale/; s/research=stable/research=stale/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "research_stale_receipt_closes"
assert_contains "$out" "route_phase_2" "research_stale_routes_phase2"

reset_repo
write_contract small
sed -i.bak 's/status=converged/status=code_gap/; s/diff=passed/diff=failed/' "$RUN/VERIFICATION.md" && rm "$RUN/VERIFICATION.md.bak"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "code_gap_receipt_closes"
assert_contains "$out" "route_phase_5" "code_gap_routes_phase5"

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

printf 'changed again\n' > "$REPO/src/a.txt"
out="$(run_gate)"
assert_status "$out" CLOSED "changed_content_stales_basis"
assert_contains "$out" "conformance_basis_stale" "changed_content_stale_detail"

reset_repo
write_contract small
printf 'extra\n' > "$REPO/src/extra.txt"
out="$(run_gate --record --write)"
assert_status "$out" CLOSED "extra_untracked_path_closes"
assert_contains "$out" "affected_files_mismatch" "extra_untracked_detail"

reset_repo
write_contract small
out="$(run_gate --record --write)"
assert_status "$out" OPEN "precommit_basis_records"
sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
before="$(sed -n 's/^Conformance basis: //p' "$RUN/STATE.md")"
git_repo add src/a.txt
git_repo commit -q -m implementation
after_out="$(run_gate)"
after="$(sed -n 's/^Conformance basis: //p' "$RUN/STATE.md")"
assert_status "$after_out" OPEN "postcommit_identical_content_opens"
if [ "$before" = "$after" ]; then pass "basis_is_commit_location_invariant"; else fail "basis_is_commit_location_invariant"; fi

reset_repo
write_contract large
out="$(run_gate --record --write)"
assert_status "$out" OPEN "large_independent_basis_records"
sed -i.bak 's/Phase 6: in-progress/Phase 6: done/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_status "$out" OPEN "large_independent_receipt_opens"

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
git_repo add src/a.txt src/b.txt
git_repo commit -q -m rename
out="$(run_gate)"
assert_status "$out" OPEN "rename_basis_stable_after_commit"

printf '%s\n' '----'
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
