#!/usr/bin/env bash
# kimiflow — contract tests for review-convergence-gate.sh.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/review-convergence-gate.sh"
WORK="$(mktemp -d)"
RUN="$WORK/.kimiflow/demo"
trap 'rm -rf "$WORK"' EXIT

git -C "$WORK" init -q
git -C "$WORK" config user.email kimiflow-test@example.invalid
git -C "$WORK" config user.name "Kimiflow Test"
mkdir -p "$WORK/src"
printf 'baseline\n' > "$WORK/src/reviewed.py"
git -C "$WORK" add src/reviewed.py
git -C "$WORK" commit -qm baseline

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
hash_file() { shasum -a 256 "$1" | awk '{print $1}'; }
hash_text() { printf '%s' "$1" | shasum -a 256 | awk '{print $1}'; }
candidate_id() { printf '%s\0%s' "$1" "$2" | shasum -a 256 | awk '{print "cand_" $1}'; }

reset_run() {
  rm -rf "$RUN"
  printf 'baseline\n' > "$WORK/src/reviewed.py"
  mkdir -p \
    "$RUN/code-review-candidates" \
    "$RUN/findings" \
    "$RUN/review-evidence" \
    "$RUN/review-saturation" \
    "$RUN/review-repairs" \
    "$RUN/review-trajectories"
  printf 'minimum complete plan\n' > "$RUN/PLAN.md"
  printf 'Affected files:\n- src/reviewed.py\n' > "$RUN/STATE.md"
  printf 'NONE\n' > "$RUN/findings/r1-code-verified.md"
}

verdict() { printf '%s\n' "$1" | awk -F '\t' '{print $2}'; }
reason() { printf '%s\n' "$1" | awk -F '\t' '{sub(/^reason=/, "", $4); print $4}'; }
assert_gate() {
  local out="$1" expected="$2" expected_reason="$3" name="$4"
  if [ "$(verdict "$out")" = "$expected" ] && [ "$(reason "$out")" = "$expected_reason" ]; then
    pass "$name"
  else
    fail "$name"
    printf '%s\n' "$out"
  fi
}

write_candidate() {
  local round="$1" axis="$2" content="$3"
  printf '%s\n' "$content" > "$RUN/code-review-candidates/r${round}-${axis}.md"
}

write_evidence() {
  local name="$1" class="$2" verify="$3" outcome="$4" detail="$5" file
  file="$RUN/review-evidence/$name"
  printf 'REVIEW_EVIDENCE class=%s :: verify=%s :: outcome=%s :: %s\n' \
    "$class" "$verify" "$outcome" "$detail" > "$file"
  printf 'review-evidence/%s@%s' "$name" "$(hash_file "$file")"
}

write_saturation_receipt() {
  local round="$1" axes_json="$2" files_json="$3" dispositions_json="$4" carried_json="$5"
  local basis_json
  basis_json="$("$SCRIPT" basis --run "$RUN" --base HEAD)"
  jq -n \
    --argjson round "$round" \
    --arg plan_sha256 "$(hash_file "$RUN/PLAN.md")" \
    --argjson axes "$axes_json" \
    --argjson candidate_files "$files_json" \
    --argjson dispositions "$dispositions_json" \
    --argjson carried_classes "$carried_json" \
    --argjson basis "$basis_json" \
    '{
      schema_version: 1,
      round: $round,
      plan_sha256: $plan_sha256,
      review_base_sha: $basis.review_base_sha,
      review_target_sha: $basis.review_target_sha,
      review_snapshot_sha256: $basis.review_snapshot_sha256,
      axes: $axes,
      candidate_files: $candidate_files,
      dispositions: $dispositions,
      carried_classes: $carried_classes
    }' > "$RUN/review-saturation/r${round}.json"
}

candidate_file_rows() {
  local round="$1"; shift
  local rows='[]' axis file
  for axis in "$@"; do
    file="$RUN/code-review-candidates/r${round}-${axis}.md"
    rows="$(printf '%s\n' "$rows" | jq -c \
      --arg axis "$axis" \
      --arg sha256 "$(hash_file "$file")" \
      '. + [{axis:$axis,sha256:$sha256}]')"
  done
  printf '%s' "$rows"
}

# Saturation: every scheduled axis and material disposition is evidence-bound.
reset_run
write_candidate 1 spec-correctness NONE
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' "$rows" '[]' '[]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "saturation_clean_axis_opens"

reset_run
outside="$WORK/outside-candidates"
mkdir -p "$outside"
printf 'NONE\n' > "$outside/r1-spec-correctness.md"
rm -rf "$RUN/code-review-candidates"
ln -s "$outside" "$RUN/code-review-candidates"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED unsafe-artifact "saturation_symlink_candidate_parent_closes"

reset_run
write_candidate 1 spec-correctness NONE
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' "$rows" '[]' '[]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED missing-axis "saturation_missing_axis_closes"

reset_run
line='CANDIDATE HIGH src/a:1 :: rollback can remain partial :: verify=command:bash hooks/test-review-convergence-gate.sh'
write_candidate 1 spec-correctness "$line"
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' "$rows" '[]' '[]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED undisposed-candidate "saturation_omitted_material_candidate_closes"

reset_run
line='CANDIDATE HIGH src/a:1 :: rollback can remain partial :: verify=command:true'
write_candidate 1 spec-correctness "$line"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED candidate-verify-invalid "saturation_noop_verifier_closes"

for noop in "sh -c true" "bash -c true" "exit 0" "test 1 = 1" "python3 -c pass"; do
  reset_run
  printf -v line 'CANDIDATE HIGH src/a:1 :: rollback can remain partial :: verify=command:%s' "$noop"
  write_candidate 1 spec-correctness "$line"
  name="$(printf '%s' "$noop" | tr ' =-' '___')"
  assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED candidate-verify-invalid "saturation_wrapped_noop_${name}_closes"
done

reset_run
line='CANDIDATE HIGH src/a:1 :: rollback can remain partial :: verify=command:bash hooks/test-review-convergence-gate.sh'
write_candidate 1 spec-correctness "$line"
evidence="$(write_evidence promoted.txt rollback-atomicity 'command:bash hooks/test-review-convergence-gate.sh' reproduced confirmed)"
printf 'FINDING HIGH src/a:1 :: rollback can remain partial :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$evidence" > "$RUN/findings/r1-code-verified.md"
rows="$(candidate_file_rows 1 spec-correctness)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line")" \
  --arg evidence "$evidence" \
  '[{candidate_id:$id,outcome:"promoted",stable_class:"rollback-atomicity",verify:"command:bash hooks/test-review-convergence-gate.sh",evidence:$evidence}]')"
write_saturation_receipt 1 '["spec-correctness"]' "$rows" "$dispositions" '[]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "saturation_promoted_candidate_opens"

reset_run
line='CANDIDATE HIGH src/a:1 :: suspected issue is unreachable :: verify=verifier:inspect call path'
write_candidate 1 spec-correctness "$line"
evidence="$(write_evidence refuted.txt unreachable verifier:'inspect call path' not_reproduced disproved)"
rows="$(candidate_file_rows 1 spec-correctness)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line")" \
  --arg evidence "$evidence" \
  '[{candidate_id:$id,outcome:"refuted",stable_class:"unreachable",verify:"verifier:inspect call path",evidence:$evidence}]')"
write_saturation_receipt 1 '["spec-correctness"]' "$rows" "$dispositions" '[]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "saturation_refuted_candidate_opens"

reset_run
line_a='CANDIDATE HIGH src/a:1 :: first independent issue :: verify=command:bash hooks/test-a.sh'
line_b='CANDIDATE BLOCKER src/b:2 :: second independent issue :: verify=command:bash hooks/test-b.sh'
write_candidate 1 spec-correctness "$line_a
$line_b"
ev_a="$(write_evidence one.txt first-class 'command:bash hooks/test-a.sh' reproduced confirmed)"
printf 'FINDING HIGH src/a:1 :: first independent issue :: class=first-class :: verify=command:bash hooks/test-a.sh :: evidence=%s\n' "$ev_a" > "$RUN/findings/r1-code-verified.md"
rows="$(candidate_file_rows 1 spec-correctness)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line_a")" \
  --arg evidence "$ev_a" \
  '[{candidate_id:$id,outcome:"promoted",stable_class:"first-class",verify:"command:bash hooks/test-a.sh",evidence:$evidence}]')"
write_saturation_receipt 1 '["spec-correctness"]' "$rows" "$dispositions" '[]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED undisposed-candidate "saturation_two_findings_need_two_dispositions"

reset_run
line_a='CANDIDATE HIGH src/a:1 :: atomicity issue :: verify=command:bash hooks/test-review-convergence-gate.sh'
line_b='CANDIDATE HIGH src/a:1 :: same atomicity issue :: verify=command:bash hooks/test-review-convergence-gate.sh'
write_candidate 1 spec-correctness "$line_a"
write_candidate 1 failure-security "$line_b"
evidence="$(write_evidence duplicate.txt rollback-atomicity 'command:bash hooks/test-review-convergence-gate.sh' reproduced confirmed)"
printf 'FINDING HIGH src/a:1 :: atomicity issue :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$evidence" > "$RUN/findings/r1-code-verified.md"
rows="$(candidate_file_rows 1 spec-correctness failure-security)"
dispositions="$(jq -nc \
  --arg one "$(candidate_id spec-correctness "$line_a")" \
  --arg two "$(candidate_id failure-security "$line_b")" \
  --arg evidence "$evidence" \
  '[
    {candidate_id:$one,outcome:"promoted",stable_class:"rollback-atomicity",verify:"command:bash hooks/test-review-convergence-gate.sh",evidence:$evidence},
    {candidate_id:$two,outcome:"promoted",stable_class:"rollback-atomicity",verify:"command:bash hooks/test-review-convergence-gate.sh",evidence:$evidence}
  ]')"
write_saturation_receipt 1 '["spec-correctness","failure-security"]' "$rows" "$dispositions" '[]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" OPEN saturated "saturation_cross_axis_dedupe_opens"

write_saturation_receipt 1 '["spec-correctness","failure-security"]' "$rows" "$dispositions" '["rollback-atomicity"]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED carried-class-unproven "saturation_first_round_cannot_forge_carry"

write_saturation_receipt 1 '["spec-correctness","failure-security"]' "$rows" "$dispositions" '[]'
sed -i.bak 's/minimum complete plan/changed plan/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED stale-plan "saturation_stale_plan_closes"

reset_run
write_candidate 1 spec-correctness NONE
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' "$rows" '[]' '[]'
printf 'changed after review\n' >> "$WORK/src/reviewed.py"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED stale-review-basis "saturation_changed_code_basis_closes"

reset_run
evidence="$(write_evidence carry-r1.txt rollback-atomicity 'command:bash hooks/test-review-convergence-gate.sh' reproduced confirmed)"
printf 'FINDING HIGH src/a:1 :: carried issue :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$evidence" > "$RUN/findings/r1-code-verified.md"
write_candidate 2 spec-correctness NONE
printf 'FINDING HIGH src/a:1 :: carried issue :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$evidence" > "$RUN/findings/r2-code-verified.md"
rows="$(candidate_file_rows 2 spec-correctness)"
write_saturation_receipt 2 '["spec-correctness"]' "$rows" '[]' '["rollback-atomicity"]'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" OPEN saturated "saturation_exact_prior_carry_opens"

drifted="$(write_evidence carry-r2.txt rollback-atomicity 'command:bash hooks/test-review-convergence-gate.sh' reproduced changed)"
printf 'FINDING HIGH src/a:1 :: carried issue :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$drifted" > "$RUN/findings/r2-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED carried-class-drift "saturation_drifted_carry_closes"

# Repair batch: every aggregate material class is covered once in an acyclic group graph.
reset_run
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" OPEN not-required "repair_clean_aggregate_needs_no_batch"

reset_run
ev_a="$(write_evidence class-a.txt class-a 'command:bash hooks/test-a.sh' reproduced confirmed)"
ev_b="$(write_evidence class-b.txt class-b verifier:'inspect trace' reproduced confirmed)"
printf 'FINDING HIGH src/a:1 :: issue a :: class=class-a :: verify=command:bash hooks/test-a.sh :: evidence=%s\nFINDING BLOCKER src/b:2 :: issue b :: class=class-b :: verify=verifier:inspect trace :: evidence=%s\n' \
  "$ev_a" "$ev_b" > "$RUN/findings/r1-code-verified.md"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED missing-repair "repair_missing_receipt_closes"

write_repair() {
  local groups="$1"
  jq -n \
    --arg plan_sha256 "$(hash_file "$RUN/PLAN.md")" \
    --arg findings_sha256 "$(hash_file "$RUN/findings/r1-code-verified.md")" \
    --argjson groups "$groups" \
    '{
      schema_version:1,
      round:1,
      plan_sha256:$plan_sha256,
      findings_sha256:$findings_sha256,
      groups:$groups
    }' > "$RUN/review-repairs/r1.json"
}

groups='[{
  "id":"atomic-write",
  "classes":["class-a","class-b"],
  "root_cause":"Both failures cross the same incomplete transaction boundary.",
  "depends_on":[],
  "repair":"Move both writes behind the existing atomic boundary.",
  "checks":[{"kind":"command","method":"bash hooks/test-a.sh"}]
}]'
write_repair "$groups"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" OPEN repair-ready "repair_complete_group_opens"

write_repair '[{
  "id":"noop",
  "classes":["class-a","class-b"],
  "root_cause":"Both failures cross the same incomplete transaction boundary.",
  "depends_on":[],
  "repair":"Pretend that both failures were repaired.",
  "checks":[{"kind":"command","method":"true"}]
}]'
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED repair-check-invalid "repair_noop_check_closes"

write_repair '[{
  "id":"unbound",
  "classes":["class-a","class-b"],
  "root_cause":"Both failures cross the same incomplete transaction boundary.",
  "depends_on":[],
  "repair":"Repair an unrelated surface.",
  "checks":[{"kind":"command","method":"bash hooks/test-unrelated.sh"}]
}]'
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED repair-check-unbound "repair_unbound_check_closes"

write_repair '[{
  "id":"partial",
  "classes":["class-a"],
  "root_cause":"Only one class is covered.",
  "depends_on":[],
  "repair":"Incomplete repair.",
  "checks":[{"kind":"command","method":"bash hooks/test-a.sh"}]
}]'
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED incomplete-repair "repair_missing_class_closes"

write_repair '[{
  "id":"one",
  "classes":["class-a"],
  "root_cause":"First group.",
  "depends_on":["two"],
  "repair":"First repair.",
  "checks":[{"kind":"command","method":"bash hooks/test-a.sh"}]
},{
  "id":"two",
  "classes":["class-b"],
  "root_cause":"Second group.",
  "depends_on":["one"],
  "repair":"Second repair.",
  "checks":[{"kind":"verifier","method":"inspect trace"}]
}]'
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED dependency-cycle "repair_cycle_closes"

# Trajectory: after two failed code strategy epochs a new plan-bound hypothesis is mandatory.
reset_run
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 1)" OPEN below-threshold "trajectory_no_recovery_is_free"

baseline="$(hash_file "$RUN/PLAN.md")"
printf '<!-- kimiflow:strategy gate=code epoch-start=1 fingerprint=%s -->\n' "$baseline" > "$RUN/RECOVERY.md"
printf 'strategy two\n' > "$RUN/PLAN.md"
after_one="$(hash_file "$RUN/PLAN.md")"
marker_one="<!-- kimiflow:recovery gate=code source-round=1 epoch-start=2 cap=4 before=$baseline after=$after_one -->"
printf '%s\n' "$marker_one" >> "$RUN/RECOVERY.md"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 2)" OPEN below-threshold "trajectory_one_failed_strategy_is_free"

printf '%s\n' \
  'strategy three' \
  'Trajectory action: replan' \
  'Trajectory hypothesis: The repair targeted symptoms while the shared transaction boundary remained invalid.' \
  'Changed assumption: Treat both writes as one atomic operation before reviewing again.' \
  'Trajectory check: command :: bash hooks/test-review-convergence-gate.sh' \
  > "$RUN/PLAN.md"
after_two="$(hash_file "$RUN/PLAN.md")"
marker_two="<!-- kimiflow:recovery gate=code source-round=2 epoch-start=3 cap=5 before=$after_one after=$after_two -->"
printf '%s\n' "$marker_two" >> "$RUN/RECOVERY.md"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 3)" CLOSED trajectory-required "trajectory_two_failures_require_reset"

write_trajectory() {
  local action="$1" plan_sha="$2" hashes="$3"
  jq -n \
    --arg plan_sha256 "$plan_sha" \
    --arg action "$action" \
    --argjson receipt_sha256s "$hashes" \
    '{
      schema_version:1,
      source_round:2,
      plan_sha256:$plan_sha256,
      failed_source_rounds:[1,2],
      recovery_receipt_sha256s:$receipt_sha256s,
      prior_trajectory_sha256s:[],
      hypothesis:"The repair targeted symptoms while the shared transaction boundary remained invalid.",
      action:$action,
      changed_assumption:"Treat both writes as one atomic operation before reviewing again.",
      checks:[{"kind":"command","method":"bash hooks/test-review-convergence-gate.sh"}]
    }' > "$RUN/review-trajectories/source-r2.json"
}

hashes="$(jq -nc --arg one "$(hash_text "$marker_one")" --arg two "$(hash_text "$marker_two")" '[$one,$two]')"
write_trajectory replan "$after_two" "$hashes"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 3)" OPEN trajectory-ready "trajectory_valid_reset_opens"

write_trajectory reword "$after_two" "$hashes"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 3)" CLOSED trajectory-action-invalid "trajectory_rewording_closes"

write_trajectory replan "$baseline" "$hashes"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 3)" CLOSED stale-plan "trajectory_stale_plan_closes"

write_trajectory replan "$after_two" "$hashes"
printf '%s\n' \
  'strategy four' \
  'Trajectory action: replan' \
  'Trajectory hypothesis: The repair targeted symptoms while the shared transaction boundary remained invalid.' \
  'Changed assumption: Treat both writes as one atomic operation before reviewing again.' \
  'Trajectory check: command :: bash hooks/test-review-convergence-gate.sh' \
  > "$RUN/PLAN.md"
after_three="$(hash_file "$RUN/PLAN.md")"
marker_three="<!-- kimiflow:recovery gate=code source-round=3 epoch-start=4 cap=6 before=$after_two after=$after_three -->"
printf '%s\n' "$marker_three" >> "$RUN/RECOVERY.md"
prior_hash="$(hash_file "$RUN/review-trajectories/source-r2.json")"
hashes_three="$(jq -nc --arg two "$(hash_text "$marker_two")" --arg three "$(hash_text "$marker_three")" '[$two,$three]')"
jq -n \
  --arg plan_sha256 "$after_three" \
  --argjson receipt_sha256s "$hashes_three" \
  --arg prior "$prior_hash" \
  '{
    schema_version:1,
    source_round:3,
    plan_sha256:$plan_sha256,
    failed_source_rounds:[2,3],
    recovery_receipt_sha256s:$receipt_sha256s,
    prior_trajectory_sha256s:[$prior],
    hypothesis:"The repair targeted symptoms while the shared transaction boundary remained invalid.",
    action:"replan",
    changed_assumption:"Treat both writes as one atomic operation before reviewing again.",
    checks:[{"kind":"command","method":"bash hooks/test-review-convergence-gate.sh"}]
  }' > "$RUN/review-trajectories/source-r3.json"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 4)" CLOSED trajectory-repeated "trajectory_repeated_strategy_closes"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
