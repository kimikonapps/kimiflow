#!/usr/bin/env bash
# kimiflow — contract tests for review-convergence-gate.sh.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/review-convergence-gate.sh"
ADAPTIVE="$(cd "$(dirname "$0")" && pwd)/adaptive-control.sh"
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
    "$RUN/code-review-cascades" \
    "$RUN/findings" \
    "$RUN/review-evidence" \
    "$RUN/review-saturation" \
    "$RUN/review-repairs" \
    "$RUN/review-deltas" \
    "$RUN/review-trajectories"
  printf 'minimum complete plan\n' > "$RUN/PLAN.md"
  printf 'Architecture deliberation: off\nBuild risk: none\nAffected files:\n- src/reviewed.py\n' > "$RUN/STATE.md"
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

write_cascade_candidate() {
  local round="$1" content="$2"
  printf '%s\n' "$content" > "$RUN/code-review-cascades/r${round}.md"
}

write_evidence() {
  local name="$1" class="$2" verify="$3" outcome="$4" detail="$5" file
  file="$RUN/review-evidence/$name"
  printf 'REVIEW_EVIDENCE class=%s :: verify=%s :: outcome=%s :: %s\n' \
    "$class" "$verify" "$outcome" "$detail" > "$file"
  printf 'review-evidence/%s@%s' "$name" "$(hash_file "$file")"
}

upgrade_dispositions_v4() {
  local input="$1" rows='[]' count index row outcome stable_class verify cascade
  count="$(printf '%s' "$input" | jq 'length')"
  index=0
  while [ "$index" -lt "$count" ]; do
    row="$(printf '%s' "$input" | jq -c ".[${index}]")"
    outcome="$(printf '%s' "$row" | jq -r '.outcome // empty')"
    if [ "$outcome" = "promoted" ]; then
      stable_class="$(printf '%s' "$row" | jq -r '.stable_class')"
      verify="$(printf '%s' "$row" | jq -r '.verify')"
      cascade="$(cascade_json \
        "$stable_class" "$stable_class" root "$verify" \
        'The reproduced class owns one authenticated review root cause.' \
        'The supported path violates the confirmed review contract.')"
      row="$(printf '%s' "$row" | jq -c \
        --argjson cascade "$cascade" \
        '.contract_status //= "violated"
         | .support_status //= "supported"
         | .impact_class //= "correctness"
         | .proportionality //= "The reproduced supported behavior violates the confirmed contract."
         | .cascade //= $cascade')"
    else
      row="$(printf '%s' "$row" | jq -c \
        '.contract_status //= "not_violated"
         | .support_status //= "unsupported"
         | .impact_class //= "none"
         | .proportionality //= "No supported user flow or confirmed contract is affected."
         | .cascade=null')"
    fi
    rows="$(printf '%s' "$rows" | jq -c --argjson row "$row" '. + [$row]')"
    index=$((index + 1))
  done
  printf '%s' "$rows"
}

write_saturation_receipt() {
  local round="$1" scheduled_json="$2" axes_json="$3" files_json="$4"
  local dispositions_json="$5" carried_json="$6" delta_receipt="$7"
  local basis_json cascade_path
  if [ ! -e "$RUN/code-review-cascades/r${round}.md" ]; then
    write_cascade_candidate "$round" NONE
  fi
  dispositions_json="$(upgrade_dispositions_v4 "$dispositions_json")"
  basis_json="$("$SCRIPT" basis --run "$RUN" --base HEAD --details)"
  cascade_path="code-review-cascades/r${round}.md"
  jq -n \
    --argjson round "$round" \
    --arg plan_sha256 "$(hash_file "$RUN/PLAN.md")" \
    --argjson scheduled_axes "$scheduled_json" \
    --argjson axes "$axes_json" \
    --argjson candidate_files "$files_json" \
    --argjson dispositions "$dispositions_json" \
    --argjson carried_classes "$carried_json" \
    --argjson basis "$basis_json" \
    --argjson delta_receipt "$delta_receipt" \
    --arg cascade_path "$cascade_path" \
    --arg cascade_sha256 "$(hash_file "$RUN/$cascade_path")" \
    '{
      schema_version: 4,
      round: $round,
      plan_sha256: $plan_sha256,
      review_base_sha: $basis.review_base_sha,
      review_target_sha: $basis.review_target_sha,
      review_snapshot_sha256: $basis.review_snapshot_sha256,
      scheduled_axes: $scheduled_axes,
      axes: $axes,
      review_files: $basis.review_files,
      candidate_files: $candidate_files,
      cascade_candidate_file: {path:$cascade_path,sha256:$cascade_sha256},
      dispositions: $dispositions,
      carried_classes: $carried_classes,
      delta_receipt: $delta_receipt
    }' > "$RUN/review-saturation/r${round}.json"
}

cascade_json() {
  local stable_class="$1" root_id="$2" role="$3" verify="$4"
  local root_cause="$5" assumption="$6" surface evidence probes='[]'
  for surface in direct-callers data-flow shared-state assumption-users error-consequences; do
    evidence="$(write_evidence "cascade-${stable_class}-${surface}.txt" "$stable_class" "$verify" reproduced "checked ${surface} for ${stable_class}")"
    probes="$(printf '%s' "$probes" | jq -c \
      --arg surface "$surface" \
      --arg verify "$verify" \
      --arg evidence "$evidence" \
      '. + [{surface:$surface,status:"checked",verify:$verify,evidence:$evidence}]')"
  done
  jq -nc \
    --arg root_cause_id "$root_id" \
    --arg root_cause "$root_cause" \
    --arg assumption "$assumption" \
    --arg role "$role" \
    --argjson probes "$probes" \
    '{root_cause_id:$root_cause_id,root_cause:$root_cause,assumption:$assumption,role:$role,probes:$probes}'
}

seed_v4_cascade_failure() {
  local root_cause assumption root_cascade member_cascade rows dispositions
  V4_ROOT_VERIFY='command:bash hooks/test-a.sh'
  V4_MEMBER_VERIFY='verifier:inspect retry handoff'
  V4_ROOT_LINE="CANDIDATE HIGH src/reviewed.py:1 :: main can remain stale :: verify=${V4_ROOT_VERIFY}"
  V4_MEMBER_LINE="CANDIDATE HIGH src/reviewed.py:1 :: retry can break the handoff :: verify=${V4_MEMBER_VERIFY}"
  V4_ROOT_EVIDENCE="$(write_evidence cascade-root.txt main-stale "$V4_ROOT_VERIFY" reproduced 'main remains stale after the failed handoff')"
  V4_MEMBER_EVIDENCE="$(write_evidence cascade-member.txt retry-broken-pipe "$V4_MEMBER_VERIFY" reproduced 'retry breaks the same handoff')"
  root_cause='The handoff publishes state before ownership is durably transferred.'
  assumption='A successful send was assumed to prove that the receiver accepted ownership.'
  write_candidate 1 spec-correctness "$V4_ROOT_LINE"
  write_candidate 1 failure-security NONE
  write_cascade_candidate 1 "$V4_MEMBER_LINE"
  printf 'FINDING HIGH src/reviewed.py:1 :: main can remain stale :: class=main-stale :: verify=%s :: evidence=%s\nFINDING HIGH src/reviewed.py:1 :: retry can break the handoff :: class=retry-broken-pipe :: verify=%s :: evidence=%s\n' \
    "$V4_ROOT_VERIFY" "$V4_ROOT_EVIDENCE" "$V4_MEMBER_VERIFY" "$V4_MEMBER_EVIDENCE" \
    > "$RUN/findings/r1-code-verified.md"
  root_cascade="$(cascade_json main-stale handoff-ownership root "$V4_ROOT_VERIFY" "$root_cause" "$assumption")"
  member_cascade="$(cascade_json retry-broken-pipe handoff-ownership downstream "$V4_MEMBER_VERIFY" "$root_cause" "$assumption")"
  rows="$(candidate_file_rows 1 spec-correctness failure-security)"
  dispositions="$(jq -nc \
    --arg root_id "$(candidate_id spec-correctness "$V4_ROOT_LINE")" \
    --arg member_id "$(candidate_id cascade-scan "$V4_MEMBER_LINE")" \
    --arg root_verify "$V4_ROOT_VERIFY" \
    --arg member_verify "$V4_MEMBER_VERIFY" \
    --arg root_evidence "$V4_ROOT_EVIDENCE" \
    --arg member_evidence "$V4_MEMBER_EVIDENCE" \
    --argjson root_cascade "$root_cascade" \
    --argjson member_cascade "$member_cascade" \
    '[
      {candidate_id:$root_id,outcome:"promoted",stable_class:"main-stale",verify:$root_verify,evidence:$root_evidence,contract_status:"violated",support_status:"supported",impact_class:"runtime",proportionality:"The supported handoff can leave Main parked indefinitely.",cascade:$root_cascade},
      {candidate_id:$member_id,outcome:"promoted",stable_class:"retry-broken-pipe",verify:$member_verify,evidence:$member_evidence,contract_status:"violated",support_status:"supported",impact_class:"runtime",proportionality:"The retry path breaks the same supported ownership handoff.",cascade:$member_cascade}
    ]')"
  write_saturation_receipt 1 \
    '["spec-correctness","failure-security"]' \
    '["spec-correctness","failure-security"]' \
    "$rows" "$dispositions" '[]' 'null'
}

write_repair_v3() {
  local round="$1" groups="$2"
  jq -n \
    --arg plan_sha256 "$(hash_file "$RUN/PLAN.md")" \
    --arg findings_sha256 "$(hash_file "$RUN/findings/r${round}-code-verified.md")" \
    --arg source_saturation_sha256 "$(hash_file "$RUN/review-saturation/r${round}.json")" \
    --argjson round "$round" \
    --argjson groups "$groups" \
    '{schema_version:3,round:$round,plan_sha256:$plan_sha256,findings_sha256:$findings_sha256,source_saturation_sha256:$source_saturation_sha256,groups:$groups}' \
    > "$RUN/review-repairs/r${round}.json"
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

seed_v3_failure() {
  local round="$1" stable_class="$2" line evidence rows dispositions verify
  verify="verifier:inspect ${stable_class} runtime path"
  line="CANDIDATE HIGH src/reviewed.py:1 :: ${stable_class} remains reproducible :: verify=${verify}"
  write_candidate "$round" spec-correctness "$line"
  evidence="$(write_evidence "trajectory-r${round}-${stable_class}.txt" "$stable_class" "$verify" reproduced "confirmed ${stable_class} failure")"
  printf 'FINDING HIGH src/reviewed.py:1 :: %s remains reproducible :: class=%s :: verify=%s :: evidence=%s\n' \
    "$stable_class" "$stable_class" "$verify" "$evidence" > "$RUN/findings/r${round}-code-verified.md"
  rows="$(candidate_file_rows "$round" spec-correctness)"
  dispositions="$(jq -nc \
    --arg id "$(candidate_id spec-correctness "$line")" \
    --arg class "$stable_class" \
    --arg verify "$verify" \
    --arg evidence "$evidence" \
    '[{candidate_id:$id,outcome:"promoted",stable_class:$class,verify:$verify,evidence:$evidence,contract_status:"violated",support_status:"supported",impact_class:"correctness",proportionality:"The reproduced supported behavior violates the confirmed contract."}]')"
  write_saturation_receipt "$round" '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
}

seed_selective_review_route() {
  local model runtime policy prompt route index stage
  model="sha256:$(hash_text model)"
  runtime="sha256:$(hash_text runtime)"
  policy="sha256:$(hash_text policy)"
  prompt="sha256:$(hash_text prompt)"
  index=0
  for stage in holdout shadow canary canary canary canary canary; do
    "$ADAPTIVE" review-cascade-record \
      --root "$WORK" \
      --sample-id "$(printf 'sample_%024x' "$((8000 + index))")" \
      --model-fingerprint "$model" \
      --execution-variant native \
      --role top \
      --task-class routine-repair \
      --runtime-fingerprint "$runtime" \
      --policy-fingerprint "$policy" \
      --prompt-gate-fingerprint "$prompt" \
      --stage "$stage" \
      --quality passed \
      --verification passed \
      --full-input-tokens 3000 \
      --full-output-tokens 300 \
      --cascade-input-tokens 1200 \
      --cascade-output-tokens 180 \
      --full-rounds 2 \
      --cascade-rounds 2 >/dev/null
    index=$((index + 1))
  done
  while :; do
    route="$("$ADAPTIVE" review-cascade-resolve \
      --root "$WORK" \
      --model-fingerprint "$model" \
      --execution-variant native \
      --role top \
      --task-class routine-repair \
      --runtime-fingerprint "$runtime" \
      --policy-fingerprint "$policy" \
      --prompt-gate-fingerprint "$prompt")"
    [ "$(printf '%s' "$route" | jq -r .route)" = "selective" ] && break
    [ "$index" -ge 17 ] && return 1
    "$ADAPTIVE" review-cascade-record \
      --root "$WORK" \
      --sample-id "$(printf 'sample_%024x' "$((8000 + index))")" \
      --model-fingerprint "$model" \
      --execution-variant native \
      --role top \
      --task-class routine-repair \
      --runtime-fingerprint "$runtime" \
      --policy-fingerprint "$policy" \
      --prompt-gate-fingerprint "$prompt" \
      --stage canary \
      --quality passed \
      --verification passed \
      --full-input-tokens 3000 \
      --full-output-tokens 300 \
      --cascade-input-tokens 1200 \
      --cascade-output-tokens 180 \
      --full-rounds 2 \
      --cascade-rounds 2 >/dev/null
    index=$((index + 1))
  done
  "$ADAPTIVE" review-cascade-resolve \
    --root "$WORK" \
    --run "$RUN" \
    --write \
    --model-fingerprint "$model" \
    --execution-variant native \
    --role top \
    --task-class routine-repair \
    --runtime-fingerprint "$runtime" \
    --policy-fingerprint "$policy" \
    --prompt-gate-fingerprint "$prompt" >/dev/null
}

if PYTHONPATH="$(dirname "$SCRIPT")" python3 -c \
  'from kimiflow_core.review_convergence import _required_delta_axes as required; axes=["spec-correctness","failure-security","standards-integration"]; assert required(["src/auth/session.py"], axes) == {"spec-correctness","failure-security"}; assert required(["src/payments/tokens.py"], axes) == {"spec-correctness","failure-security"}; assert required(["Migrations/001.sql"], axes) == {"spec-correctness","failure-security"}; assert required(["SKILL.md"], axes) == {"spec-correctness","standards-integration"}; assert required(["Docs/render/widget.md"], axes) == {"spec-correctness","standards-integration"}; assert required(["docs/authors.md"], axes) == {"spec-correctness"}'; then
  pass "review_lease_path_axes_are_precise"
else
  fail "review_lease_path_axes_are_precise"
fi

reset_run
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 5)" CLOSED review-limit-reached "preflight_fifth_round_closes_before_review"
assert_gate "$("$SCRIPT" delta --run "$RUN" --round 5 --scheduled-axes spec-correctness --rerun-axes spec-correctness)" CLOSED review-limit-reached "delta_fifth_round_closes_before_artifacts"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 5 --axes spec-correctness)" CLOSED review-limit-reached "saturation_fifth_round_closes_before_artifacts"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 4)" CLOSED review-limit-reached "closeout_cannot_start_another_repair"

# Saturation: every scheduled axis and material disposition is evidence-bound.
reset_run
write_candidate 1 spec-correctness NONE
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
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
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED missing-axis "saturation_missing_axis_closes"

reset_run
line='CANDIDATE HIGH src/a:1 :: rollback can remain partial :: verify=command:bash hooks/test-review-convergence-gate.sh'
write_candidate 1 spec-correctness "$line"
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
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
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
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
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "saturation_refuted_candidate_opens"

reset_run
line='CANDIDATE HIGH src/a:1 :: reproduced edge case has no supported-path impact :: verify=verifier:inspect supported call path'
write_candidate 1 spec-correctness "$line"
evidence="$(write_evidence non-blocking.txt unsupported-edge verifier:'inspect supported call path' reproduced 'reproduced only outside the supported contract')"
rows="$(candidate_file_rows 1 spec-correctness)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line")" \
  --arg evidence "$evidence" \
  '[{candidate_id:$id,outcome:"non_blocking",stable_class:"unsupported-edge",verify:"verifier:inspect supported call path",evidence:$evidence}]')"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "saturation_reproduced_non_blocking_candidate_opens"

# Schema 3 makes relevance explicit: unsupported edge cases stay out of repair,
# protected impacts cannot be waived, and user-boundary repairs stop at the
# existing material-decision gate.
reset_run
line='CANDIDATE HIGH src/a:1 :: reproduced edge case is outside the supported contract :: verify=verifier:inspect supported call path'
write_candidate 1 spec-correctness "$line"
evidence="$(write_evidence v3-non-blocking.txt unsupported-edge verifier:'inspect supported call path' reproduced 'outside the supported contract and no product impact')"
rows="$(candidate_file_rows 1 spec-correctness)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line")" \
  --arg evidence "$evidence" \
  '[{candidate_id:$id,outcome:"non_blocking",stable_class:"unsupported-edge",verify:"verifier:inspect supported call path",evidence:$evidence,contract_status:"not_violated",support_status:"unsupported",impact_class:"none",proportionality:"No supported user flow or confirmed contract is affected."}]')"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "saturation_v3_irrelevant_edge_stays_non_blocking"

reset_run
line='CANDIDATE HIGH src/a:1 :: reproduced supported edge is immaterial :: verify=verifier:inspect supported call path'
write_candidate 1 spec-correctness "$line"
evidence="$(write_evidence v3-supported-immaterial.txt immaterial-edge verifier:'inspect supported call path' reproduced 'supported edge does not violate the contract or affect product behavior')"
rows="$(candidate_file_rows 1 spec-correctness)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line")" \
  --arg evidence "$evidence" \
  '[{candidate_id:$id,outcome:"non_blocking",stable_class:"immaterial-edge",verify:"verifier:inspect supported call path",evidence:$evidence,contract_status:"not_violated",support_status:"supported",impact_class:"none",proportionality:"The supported edge is reproduced but does not violate the contract or affect product behavior."}]')"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "saturation_v3_supported_immaterial_edge_stays_non_blocking"

dispositions="$(printf '%s' "$dispositions" | jq -c '.[0].impact_class="privacy"')"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED protected-impact-required "saturation_v3_protected_impact_cannot_be_waived"

reset_run
line='CANDIDATE BLOCKER src/a:1 :: fixing the defect requires a new privacy boundary :: verify=verifier:inspect data flow'
write_candidate 1 spec-correctness "$line"
evidence="$(write_evidence material-decision.txt privacy-boundary verifier:'inspect data flow' reproduced 'the defect is real and repair changes collection of personal data')"
rows="$(candidate_file_rows 1 spec-correctness)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line")" \
  --arg evidence "$evidence" \
  '[{candidate_id:$id,outcome:"material_decision",stable_class:"privacy-boundary",verify:"verifier:inspect data flow",evidence:$evidence,contract_status:"violated",support_status:"supported",impact_class:"privacy",proportionality:"Repair changes the confirmed privacy boundary and needs user authority."}]')"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED material-decision-required "saturation_v3_material_boundary_uses_typed_user_gate"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED material-decision-required "repair_unavailable_while_material_decision_is_pending"

dispositions="$(printf '%s' "$dispositions" | jq -c '.[0].impact_class="correctness"')"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED material-decision-boundary-invalid "saturation_v3_routine_defect_cannot_force_user_decision"

reset_run
runtime_line='CANDIDATE HIGH src/a:1 :: runtime branch remains suspect :: verify=verifier:inspect runtime trace'
runtime_evidence="$(write_evidence runtime-text.txt runtime-branch verifier:'inspect runtime trace' reproduced 'text-only runtime inspection')"
runtime_disposition="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$runtime_line")" \
  --arg evidence "$runtime_evidence" \
  '[{candidate_id:$id,outcome:"promoted",stable_class:"runtime-branch",verify:"verifier:inspect runtime trace",evidence:$evidence,contract_status:"violated",support_status:"supported",impact_class:"runtime",proportionality:"The supported runtime path violates the confirmed behavior."}]')"
for round in 1 2; do
  printf 'runtime strategy %s\n' "$round" > "$RUN/PLAN.md"
  write_candidate "$round" spec-correctness "$runtime_line"
  printf 'FINDING HIGH src/a:1 :: runtime branch remains suspect :: class=runtime-branch :: verify=verifier:inspect runtime trace :: evidence=%s\n' "$runtime_evidence" > "$RUN/findings/r${round}-code-verified.md"
  rows="$(candidate_file_rows "$round" spec-correctness)"
  write_saturation_receipt "$round" '["spec-correctness"]' '["spec-correctness"]' "$rows" "$runtime_disposition" '[]' 'null'
done
printf 'runtime strategy 3\n' > "$RUN/PLAN.md"
write_candidate 3 spec-correctness "$runtime_line"
printf 'FINDING HIGH src/a:1 :: runtime branch remains suspect :: class=runtime-branch :: verify=verifier:inspect runtime trace :: evidence=%s\n' "$runtime_evidence" > "$RUN/findings/r3-code-verified.md"
rows="$(candidate_file_rows 3 spec-correctness)"
write_saturation_receipt 3 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$runtime_disposition" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 3 --axes spec-correctness)" CLOSED runtime-evidence-required "saturation_v3_third_runtime_round_requires_executable_evidence"

runtime_line='CANDIDATE HIGH src/a:1 :: runtime branch remains suspect :: verify=command:bash hooks/test-review-convergence-gate.sh'
runtime_evidence="$(write_evidence runtime-command.txt runtime-branch 'command:bash hooks/test-review-convergence-gate.sh' reproduced 'executable runtime falsifier passed')"
runtime_disposition="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$runtime_line")" \
  --arg evidence "$runtime_evidence" \
  '[{candidate_id:$id,outcome:"promoted",stable_class:"runtime-branch",verify:"command:bash hooks/test-review-convergence-gate.sh",evidence:$evidence,contract_status:"violated",support_status:"supported",impact_class:"runtime",proportionality:"The executable check reproduces the supported runtime defect."}]')"
write_candidate 3 spec-correctness "$runtime_line"
printf 'FINDING HIGH src/a:1 :: runtime branch remains suspect :: class=runtime-branch :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$runtime_evidence" > "$RUN/findings/r3-code-verified.md"
rows="$(candidate_file_rows 3 spec-correctness)"
write_saturation_receipt 3 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$runtime_disposition" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 3 --axes spec-correctness)" OPEN saturated "saturation_v3_executable_runtime_evidence_opens"

reset_run
seed_v3_failure 1 legacy-bypass
jq '.schema_version=3
    | del(.cascade_candidate_file)
    | .dispositions |= map(del(.cascade))' \
  "$RUN/review-saturation/r1.json" > "$RUN/review-saturation/r1.legacy"
mv "$RUN/review-saturation/r1.legacy" "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED saturation-schema-required "current_run_rejects_legacy_saturation_bypass"

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
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
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
write_saturation_receipt 1 '["spec-correctness","failure-security"]' '["spec-correctness","failure-security"]' "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" OPEN saturated "saturation_cross_axis_dedupe_opens"

write_saturation_receipt 1 '["spec-correctness","failure-security"]' '["spec-correctness","failure-security"]' "$rows" "$dispositions" '["rollback-atomicity"]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED carried-class-unproven "saturation_first_round_cannot_forge_carry"

write_saturation_receipt 1 '["spec-correctness","failure-security"]' '["spec-correctness","failure-security"]' "$rows" "$dispositions" '[]' 'null'
sed -i.bak 's/minimum complete plan/changed plan/' "$RUN/PLAN.md" && rm "$RUN/PLAN.md.bak"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED stale-plan "saturation_stale_plan_closes"

reset_run
write_candidate 1 spec-correctness NONE
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
printf 'changed after review\n' >> "$WORK/src/reviewed.py"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED stale-review-basis "saturation_changed_code_basis_closes"

reset_run
evidence="$(write_evidence carry-r1.txt rollback-atomicity 'command:bash hooks/test-review-convergence-gate.sh' reproduced confirmed)"
line='CANDIDATE HIGH src/a:1 :: carried issue :: verify=command:bash hooks/test-review-convergence-gate.sh'
write_candidate 1 spec-correctness "$line"
printf 'FINDING HIGH src/a:1 :: carried issue :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$evidence" > "$RUN/findings/r1-code-verified.md"
rows="$(candidate_file_rows 1 spec-correctness)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line")" \
  --arg evidence "$evidence" \
  '[{candidate_id:$id,outcome:"promoted",stable_class:"rollback-atomicity",verify:"command:bash hooks/test-review-convergence-gate.sh",evidence:$evidence}]')"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$dispositions" '[]' 'null'
printf 'changed carry plan\n' > "$RUN/PLAN.md"
write_candidate 2 spec-correctness NONE
printf 'FINDING HIGH src/a:1 :: carried issue :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$evidence" > "$RUN/findings/r2-code-verified.md"
rows="$(candidate_file_rows 2 spec-correctness)"
write_saturation_receipt 2 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '["rollback-atomicity"]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" OPEN saturated "saturation_exact_prior_carry_opens"

printf 'FINDING HIGH src/renamed.py:9 :: rewritten carried issue :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$evidence" > "$RUN/findings/r2-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED carried-class-drift "saturation_reworded_carry_closes"

drifted="$(write_evidence carry-r2.txt rollback-atomicity 'command:bash hooks/test-review-convergence-gate.sh' reproduced changed)"
printf 'FINDING HIGH src/a:1 :: carried issue :: class=rollback-atomicity :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' "$drifted" > "$RUN/findings/r2-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED carried-class-drift "saturation_drifted_carry_closes"

# Schema 4 authenticates cascade-discovered candidates, requires one complete
# five-surface scan per promoted class, and rejects incoherent or scan-only
# root-cause groups before repair.
reset_run
write_candidate 1 spec-correctness NONE
write_cascade_candidate 1 NONE
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "saturation_v4_clean_review_opens"

reset_run
seed_v4_cascade_failure
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" OPEN saturated "saturation_v4_multi_member_cascade_opens"
cp "$RUN/review-saturation/r1.json" "$RUN/review-saturation/r1.valid"

printf 'NONE\n' >> "$RUN/code-review-cascades/r1.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED cascade-candidate-digest-mismatch "saturation_v4_scan_candidate_digest_drift_closes"
write_cascade_candidate 1 "$V4_MEMBER_LINE"

jq '(.dispositions[0].cascade.probes) |= .[0:4]' \
  "$RUN/review-saturation/r1.valid" > "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED cascade-probes-invalid "saturation_v4_missing_probe_closes"

cp "$RUN/review-evidence/cascade-main-stale-direct-callers.txt" \
  "$RUN/review-evidence/cascade-main-stale-duplicate.txt"
duplicate_probe="review-evidence/cascade-main-stale-duplicate.txt@$(hash_file "$RUN/review-evidence/cascade-main-stale-duplicate.txt")"
jq --arg evidence "$duplicate_probe" '.dispositions[0].cascade.probes[1].evidence = $evidence' \
  "$RUN/review-saturation/r1.valid" > "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED cascade-probe-evidence-duplicate "saturation_v4_reused_probe_evidence_closes"

jq '.dispositions[1].cascade.assumption="A contradictory ownership assumption is used for this member."' \
  "$RUN/review-saturation/r1.valid" > "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED cascade-group-conflict "saturation_v4_conflicting_assumption_closes"

jq '.dispositions[1].cascade.role="root"' \
  "$RUN/review-saturation/r1.valid" > "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED cascade-root-count-invalid "saturation_v4_two_roots_close"

jq '.dispositions[1].cascade.root_cause_id="scan-only" | .dispositions[1].cascade.role="root"' \
  "$RUN/review-saturation/r1.valid" > "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED cascade-origin-missing "saturation_v4_scan_only_group_closes"

jq '.dispositions[1].outcome="non_blocking" | .dispositions[1].contract_status="not_violated" | .dispositions[1].support_status="unsupported" | .dispositions[1].impact_class="none"' \
  "$RUN/review-saturation/r1.valid" > "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED cascade-not-allowed "saturation_v4_nonpromoted_cascade_closes"

jq '.dispositions[0].cascade.probes[0].status="not_applicable"' \
  "$RUN/review-saturation/r1.valid" > "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED evidence-mismatch "saturation_v4_probe_outcome_drift_closes"

not_applicable="$(write_evidence cascade-root-not-applicable.txt main-stale "$V4_ROOT_VERIFY" not_reproduced 'no direct caller exists outside the checked handoff entry point')"
jq --arg evidence "$not_applicable" \
  '.dispositions[0].cascade.probes[0].status="not_applicable" | .dispositions[0].cascade.probes[0].evidence=$evidence' \
  "$RUN/review-saturation/r1.valid" > "$RUN/review-saturation/r1.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" OPEN saturated "saturation_v4_evidenced_not_applicable_opens"
mv "$RUN/review-saturation/r1.valid" "$RUN/review-saturation/r1.json"

write_cascade_candidate 1 NONE
rows="$(candidate_file_rows 1 spec-correctness failure-security)"
dispositions="$(jq -c .dispositions "$RUN/review-saturation/r1.json")"
write_saturation_receipt 1 \
  '["spec-correctness","failure-security"]' \
  '["spec-correctness","failure-security"]' \
  "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" CLOSED disposition-candidate-invalid "saturation_v4_unbound_scan_member_closes"

reset_run
: > "$RUN/code-review-candidates/r1-spec-correctness.md"
: > "$RUN/findings/r1-code-verified.md"
write_cascade_candidate 1 NONE
limit_dispositions='[]'
for index in $(seq 1 33); do
  printf -v stable_class 'cascade-%02d' "$index"
  verify="verifier:inspect cascade path ${index}"
  line="CANDIDATE HIGH src/reviewed.py:1 :: cascade member ${index} remains reproducible :: verify=${verify}"
  evidence="$(write_evidence "limit-${stable_class}.txt" "$stable_class" "$verify" reproduced "confirmed cascade member ${index}")"
  printf '%s\n' "$line" >> "$RUN/code-review-candidates/r1-spec-correctness.md"
  printf 'FINDING HIGH src/reviewed.py:1 :: cascade member %s remains reproducible :: class=%s :: verify=%s :: evidence=%s\n' \
    "$index" "$stable_class" "$verify" "$evidence" >> "$RUN/findings/r1-code-verified.md"
  role=downstream
  [ "$index" -eq 1 ] && role=root
  cascade="$(cascade_json "$stable_class" oversized-cascade "$role" "$verify" 'All members share one oversized review root cause.' 'The oversized fixture deliberately exceeds the bounded class budget.')"
  limit_dispositions="$(printf '%s' "$limit_dispositions" | jq -c \
    --arg id "$(candidate_id spec-correctness "$line")" \
    --arg stable_class "$stable_class" \
    --arg verify "$verify" \
    --arg evidence "$evidence" \
    --argjson cascade "$cascade" \
    '. + [{candidate_id:$id,outcome:"promoted",stable_class:$stable_class,verify:$verify,evidence:$evidence,contract_status:"violated",support_status:"supported",impact_class:"runtime",proportionality:"The reproduced member violates the bounded supported cascade contract.",cascade:$cascade}]')"
done
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" "$limit_dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" CLOSED cascade-limit-reached "saturation_v4_over_32_classes_closes"

# Schema-3 repairs are bound to the exact schema-4 source and must reproduce
# its groups while retaining every member's authoritative verifier.
reset_run
seed_v4_cascade_failure
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security)" OPEN saturated "cascade_repair_source_saturates"
cascade_groups='[{
  "id":"handoff-ownership",
  "classes":["main-stale","retry-broken-pipe"],
  "root_cause":"The handoff publishes state before ownership is durably transferred.",
  "depends_on":[],
  "repair":"Publish the handoff only after the receiver durably accepts ownership.",
  "checks":[
    {"kind":"command","method":"bash hooks/test-a.sh"},
    {"kind":"verifier","method":"inspect retry handoff"}
  ]
}]'
write_repair_v3 1 "$cascade_groups"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" OPEN repair-ready "cascade_repair_requires_matching_group_and_individual_checks"

cp "$RUN/review-repairs/r1.json" "$RUN/review-repairs/r1.current"
jq '.schema_version=1 | del(.source_saturation_sha256)' \
  "$RUN/review-repairs/r1.current" > "$RUN/review-repairs/r1.json"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED cascade-repair-required "cascade_repair_rejects_schema1_receipt"

jq '.schema_version=2' "$RUN/review-repairs/r1.current" > "$RUN/review-repairs/r1.legacy"
mv "$RUN/review-repairs/r1.legacy" "$RUN/review-repairs/r1.json"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED cascade-repair-required "cascade_repair_rejects_legacy_receipt"
mv "$RUN/review-repairs/r1.current" "$RUN/review-repairs/r1.json"

write_repair_v3 1 "$(printf '%s' "$cascade_groups" | jq -c '.[0].checks |= .[0:1]')"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED repair-check-incomplete "cascade_repair_missing_member_check_closes"

write_repair_v3 1 "$(printf '%s' "$cascade_groups" | jq -c '.[0].root_cause="A different root cause was supplied by repair."')"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" CLOSED repair-cascade-group-mismatch "cascade_repair_root_cause_drift_closes"

# The next delta partitions every source class into exact carry or one fresh,
# verifier-stable negative resolution; a shared repair check cannot hide a
# missing class outcome.
write_repair_v3 1 "$cascade_groups"
printf 'repaired handoff\n' > "$WORK/src/reviewed.py"
basis_json="$("$SCRIPT" basis --run "$RUN" --base HEAD --details)"
scheduled='["spec-correctness","failure-security"]'
jq -n \
  --arg plan_sha256 "$(hash_file "$RUN/PLAN.md")" \
  --arg source_saturation_sha256 "$(hash_file "$RUN/review-saturation/r1.json")" \
  --arg repair_sha256 "$(hash_file "$RUN/review-repairs/r1.json")" \
  --argjson review_files "$(printf '%s' "$basis_json" | jq -c .review_files)" \
  --argjson scheduled_axes "$scheduled" \
  '{schema_version:1,source_round:1,round:2,plan_sha256:$plan_sha256,source_saturation_sha256:$source_saturation_sha256,repair_sha256:$repair_sha256,scheduled_axes:$scheduled_axes,rerun_axes:["spec-correctness"],carried_axes:["failure-security"],review_files:$review_files,changed_paths:["src/reviewed.py"],route_receipt_sha256:null}' \
  > "$RUN/review-deltas/r2.json"
assert_gate "$("$SCRIPT" delta --run "$RUN" --round 2 --scheduled-axes spec-correctness,failure-security --rerun-axes spec-correctness)" OPEN selective-review-ready "cascade_delta_source_opens"

write_candidate 2 spec-correctness NONE
write_cascade_candidate 2 NONE
rows="$(candidate_file_rows 2 spec-correctness)"
delta_sha="$(hash_file "$RUN/review-deltas/r2.json")"
delta_spec="$(jq -Rn --arg value "review-deltas/r2.json@${delta_sha}" '$value')"
unbound_root="$(write_evidence cascade-root-unbound-r2.txt main-stale "$V4_ROOT_VERIFY" not_reproduced 'precomputed root resolution')"
unbound_member="$(write_evidence cascade-member-unbound-r2.txt retry-broken-pipe "$V4_MEMBER_VERIFY" not_reproduced 'precomputed member resolution')"
printf 'RESOLVED class=main-stale :: verify=%s :: evidence=%s\nRESOLVED class=retry-broken-pipe :: verify=%s :: evidence=%s\n' \
  "$V4_ROOT_VERIFY" "$unbound_root" "$V4_MEMBER_VERIFY" "$unbound_member" \
  > "$RUN/findings/r2-code-verified.md"
write_saturation_receipt 2 "$scheduled" '["spec-correctness"]' "$rows" '[]' '[]' "$delta_spec"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED delta-resolution-context-mismatch "cascade_delta_precomputed_resolution_closes"

root_resolved="$(write_evidence cascade-root-r2.txt main-stale "$V4_ROOT_VERIFY" not_reproduced "main no longer remains stale :: delta_sha256=${delta_sha}")"
printf 'RESOLVED class=main-stale :: verify=%s :: evidence=%s\n' \
  "$V4_ROOT_VERIFY" "$root_resolved" > "$RUN/findings/r2-code-verified.md"
write_saturation_receipt 2 "$scheduled" '["spec-correctness"]' "$rows" '[]' '[]' "$delta_spec"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED delta-resolution-incomplete "cascade_delta_missing_member_resolution_closes"

wrong_resolved="$(write_evidence cascade-member-wrong-r2.txt retry-broken-pipe 'verifier:inspect unrelated handoff' not_reproduced "an unrelated path no longer reproduces :: delta_sha256=${delta_sha}")"
printf 'RESOLVED class=retry-broken-pipe :: verify=verifier:inspect unrelated handoff :: evidence=%s\n' \
  "$wrong_resolved" >> "$RUN/findings/r2-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED delta-resolution-verifier-mismatch "cascade_delta_verifier_drift_closes"

member_resolved="$(write_evidence cascade-member-r2.txt retry-broken-pipe "$V4_MEMBER_VERIFY" not_reproduced "retry no longer breaks the handoff :: delta_sha256=${delta_sha}")"
printf 'RESOLVED class=main-stale :: verify=%s :: evidence=%s\nRESOLVED class=retry-broken-pipe :: verify=%s :: evidence=%s\n' \
  "$V4_ROOT_VERIFY" "$root_resolved" "$V4_MEMBER_VERIFY" "$member_resolved" \
  > "$RUN/findings/r2-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" OPEN saturated "cascade_delta_individual_resolutions_open"

printf 'RESOLVED class=main-stale :: verify=%s :: evidence=%s\nFINDING HIGH src/reviewed.py:1 :: retry can break the handoff :: class=retry-broken-pipe :: verify=%s :: evidence=%s\n' \
  "$V4_ROOT_VERIFY" "$root_resolved" "$V4_MEMBER_VERIFY" "$V4_MEMBER_EVIDENCE" \
  > "$RUN/findings/r2-code-verified.md"
write_saturation_receipt 2 "$scheduled" '["spec-correctness"]' "$rows" '[]' '["retry-broken-pipe"]' "$delta_spec"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" OPEN saturated "cascade_delta_exact_partial_carry_opens"

printf 'RESOLVED class=retry-broken-pipe :: verify=%s :: evidence=%s\n' \
  "$V4_MEMBER_VERIFY" "$member_resolved" >> "$RUN/findings/r2-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED delta-resolution-overlap "cascade_delta_carry_resolution_overlap_closes"

parking_verify='verifier:inspect parking handoff'
parking_line="CANDIDATE HIGH src/reviewed.py:1 :: parking can stall the handoff :: verify=${parking_verify}"
parking_evidence="$(write_evidence cascade-parking-r2.txt parking-stall "$parking_verify" reproduced 'parking stalls the same supported ownership handoff')"
write_candidate 2 spec-correctness "$parking_line"
parking_cascade="$(cascade_json parking-stall handoff-ownership downstream "$parking_verify" 'The handoff publishes state before ownership is durably transferred.' 'A successful send was assumed to prove that the receiver accepted ownership.')"
parking_disposition="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$parking_line")" \
  --arg verify "$parking_verify" \
  --arg evidence "$parking_evidence" \
  --argjson cascade "$parking_cascade" \
  '[{candidate_id:$id,outcome:"promoted",stable_class:"parking-stall",verify:$verify,evidence:$evidence,contract_status:"violated",support_status:"supported",impact_class:"runtime",proportionality:"The parking path extends the reproduced supported ownership cascade.",cascade:$cascade}]')"
printf 'FINDING HIGH src/reviewed.py:1 :: main can remain stale :: class=main-stale :: verify=%s :: evidence=%s\nRESOLVED class=retry-broken-pipe :: verify=%s :: evidence=%s\nFINDING HIGH src/reviewed.py:1 :: parking can stall the handoff :: class=parking-stall :: verify=%s :: evidence=%s\n' \
  "$V4_ROOT_VERIFY" "$V4_ROOT_EVIDENCE" "$V4_MEMBER_VERIFY" "$member_resolved" "$parking_verify" "$parking_evidence" \
  > "$RUN/findings/r2-code-verified.md"
rows="$(candidate_file_rows 2 spec-correctness)"
write_saturation_receipt 2 "$scheduled" '["spec-correctness"]' "$rows" "$parking_disposition" '["main-stale"]' "$delta_spec"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" OPEN saturated "cascade_delta_new_member_uses_carried_root"

r2_groups='[{
  "id":"handoff-ownership",
  "classes":["main-stale","parking-stall"],
  "root_cause":"The handoff publishes state before ownership is durably transferred.",
  "depends_on":[],
  "repair":"Repair the parking path while preserving the accepted ownership handoff.",
  "checks":[
    {"kind":"command","method":"bash hooks/test-a.sh"},
    {"kind":"verifier","method":"inspect parking handoff"}
  ]
}]'
write_repair_v3 2 "$r2_groups"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 2)" OPEN repair-ready "cascade_second_repair_source_opens"

printf 'repaired parking handoff\n' > "$WORK/src/reviewed.py"
basis_json="$("$SCRIPT" basis --run "$RUN" --base HEAD --details)"
jq -n \
  --arg plan_sha256 "$(hash_file "$RUN/PLAN.md")" \
  --arg source_saturation_sha256 "$(hash_file "$RUN/review-saturation/r2.json")" \
  --arg repair_sha256 "$(hash_file "$RUN/review-repairs/r2.json")" \
  --argjson review_files "$(printf '%s' "$basis_json" | jq -c .review_files)" \
  --argjson scheduled_axes "$scheduled" \
  '{schema_version:1,source_round:2,round:3,plan_sha256:$plan_sha256,source_saturation_sha256:$source_saturation_sha256,repair_sha256:$repair_sha256,scheduled_axes:$scheduled_axes,rerun_axes:["spec-correctness"],carried_axes:["failure-security"],review_files:$review_files,changed_paths:["src/reviewed.py"],route_receipt_sha256:null}' \
  > "$RUN/review-deltas/r3.json"
assert_gate "$("$SCRIPT" delta --run "$RUN" --round 3 --scheduled-axes spec-correctness,failure-security --rerun-axes spec-correctness)" OPEN selective-review-ready "cascade_third_round_replays_frozen_delta_source"
printf 'repaired handoff\n' > "$WORK/src/reviewed.py"

new_root_verify='verifier:inspect replacement root'
new_root_line="CANDIDATE HIGH src/reviewed.py:1 :: replacement root remains reproducible :: verify=${new_root_verify}"
new_root_evidence="$(write_evidence cascade-new-root-r2.txt replacement-root "$new_root_verify" reproduced 'a second root was reproduced')"
write_candidate 2 spec-correctness "$new_root_line"
new_root_cascade="$(cascade_json replacement-root handoff-ownership root "$new_root_verify" 'The handoff publishes state before ownership is durably transferred.' 'A successful send was assumed to prove that the receiver accepted ownership.')"
new_root_disposition="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$new_root_line")" \
  --arg verify "$new_root_verify" \
  --arg evidence "$new_root_evidence" \
  --argjson cascade "$new_root_cascade" \
  '[{candidate_id:$id,outcome:"promoted",stable_class:"replacement-root",verify:$verify,evidence:$evidence,contract_status:"violated",support_status:"supported",impact_class:"runtime",proportionality:"The replacement root duplicates the carried root for the same cascade.",cascade:$cascade}]')"
printf 'FINDING HIGH src/reviewed.py:1 :: main can remain stale :: class=main-stale :: verify=%s :: evidence=%s\nRESOLVED class=retry-broken-pipe :: verify=%s :: evidence=%s\nFINDING HIGH src/reviewed.py:1 :: replacement root remains reproducible :: class=replacement-root :: verify=%s :: evidence=%s\n' \
  "$V4_ROOT_VERIFY" "$V4_ROOT_EVIDENCE" "$V4_MEMBER_VERIFY" "$member_resolved" "$new_root_verify" "$new_root_evidence" \
  > "$RUN/findings/r2-code-verified.md"
rows="$(candidate_file_rows 2 spec-correctness)"
write_saturation_receipt 2 "$scheduled" '["spec-correctness"]' "$rows" "$new_root_disposition" '["main-stale"]' "$delta_spec"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED cascade-root-count-invalid "cascade_delta_new_root_cannot_duplicate_carried_root"

# Review delta: one full review per stable plan, then exact repair deltas.
reset_run
line='CANDIDATE HIGH src/reviewed.py:1 :: stale behavior survives the repair :: verify=command:bash hooks/test-review-convergence-gate.sh'
write_candidate 1 spec-correctness "$line"
write_candidate 1 failure-security NONE
write_candidate 1 standards-integration NONE
evidence="$(write_evidence lease-r1.txt stale-behavior 'command:bash hooks/test-review-convergence-gate.sh' reproduced confirmed)"
printf 'FINDING HIGH src/reviewed.py:1 :: stale behavior survives the repair :: class=stale-behavior :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' \
  "$evidence" > "$RUN/findings/r1-code-verified.md"
rows="$(candidate_file_rows 1 spec-correctness failure-security standards-integration)"
dispositions="$(jq -nc \
  --arg id "$(candidate_id spec-correctness "$line")" \
  --arg evidence "$evidence" \
  '[{candidate_id:$id,outcome:"promoted",stable_class:"stale-behavior",verify:"command:bash hooks/test-review-convergence-gate.sh",evidence:$evidence}]')"
scheduled='["spec-correctness","failure-security","standards-integration"]'
write_saturation_receipt 1 "$scheduled" "$scheduled" "$rows" "$dispositions" '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness,failure-security,standards-integration)" OPEN saturated "saturation_v2_full_round_opens"

jq -n \
  --arg plan_sha256 "$(hash_file "$RUN/PLAN.md")" \
  --arg findings_sha256 "$(hash_file "$RUN/findings/r1-code-verified.md")" \
  --arg source_saturation_sha256 "$(hash_file "$RUN/review-saturation/r1.json")" \
  '{
    schema_version:3,
    round:1,
    plan_sha256:$plan_sha256,
    findings_sha256:$findings_sha256,
    source_saturation_sha256:$source_saturation_sha256,
    groups:[{
      id:"stale-behavior",
      classes:["stale-behavior"],
      root_cause:"The reproduced class owns one authenticated review root cause.",
      depends_on:[],
      repair:"Replace the stale behavior and rerun its exact falsifier.",
      checks:[{kind:"command",method:"bash hooks/test-review-convergence-gate.sh"}]
    }]
  }' > "$RUN/review-repairs/r1.json"
assert_gate "$("$SCRIPT" repair --run "$RUN" --round 1)" OPEN repair-ready "review_lease_source_repair_opens"

printf 'repaired\n' > "$WORK/src/reviewed.py"
basis_json="$("$SCRIPT" basis --run "$RUN" --base HEAD --details)"
jq -n \
  --arg plan_sha256 "$(hash_file "$RUN/PLAN.md")" \
  --arg source_saturation_sha256 "$(hash_file "$RUN/review-saturation/r1.json")" \
  --arg repair_sha256 "$(hash_file "$RUN/review-repairs/r1.json")" \
  --argjson review_files "$(printf '%s' "$basis_json" | jq -c .review_files)" \
  --argjson scheduled_axes "$scheduled" \
  '{
    schema_version:1,
    source_round:1,
    round:2,
    plan_sha256:$plan_sha256,
    source_saturation_sha256:$source_saturation_sha256,
    repair_sha256:$repair_sha256,
    scheduled_axes:$scheduled_axes,
    rerun_axes:["spec-correctness"],
    carried_axes:["failure-security","standards-integration"],
    review_files:$review_files,
    changed_paths:["src/reviewed.py"],
    route_receipt_sha256:null
  }' > "$RUN/review-deltas/r2.json"
assert_gate "$("$SCRIPT" delta --run "$RUN" --round 2 --scheduled-axes spec-correctness,failure-security,standards-integration --rerun-axes spec-correctness)" OPEN selective-review-ready "review_delta_opens_without_calibration"

if seed_selective_review_route; then
  route_sha="$(hash_file "$RUN/REVIEW-CASCADE-ROUTE.json")"
  jq --arg route_receipt_sha256 "$route_sha" \
    '.route_receipt_sha256=$route_receipt_sha256' \
    "$RUN/review-deltas/r2.json" > "$RUN/review-deltas/r2.next"
  mv "$RUN/review-deltas/r2.next" "$RUN/review-deltas/r2.json"
  assert_gate "$("$SCRIPT" delta --run "$RUN" --round 2 --scheduled-axes spec-correctness,failure-security,standards-integration --rerun-axes spec-correctness)" OPEN selective-review-ready "review_delta_legacy_calibrated_receipt_opens"

  resolved="$(write_evidence lease-r2.txt stale-behavior 'command:bash hooks/test-review-convergence-gate.sh' not_reproduced "repaired :: delta_sha256=$(hash_file "$RUN/review-deltas/r2.json")")"
  printf 'RESOLVED class=stale-behavior :: verify=command:bash hooks/test-review-convergence-gate.sh :: evidence=%s\n' \
    "$resolved" > "$RUN/findings/r2-code-verified.md"
  write_candidate 2 spec-correctness NONE
  rows="$(candidate_file_rows 2 spec-correctness)"
  delta_spec="$(jq -Rn --arg value "review-deltas/r2.json@$(hash_file "$RUN/review-deltas/r2.json")" '$value')"
  write_saturation_receipt 2 "$scheduled" '["spec-correctness"]' "$rows" '[]' '[]' "$delta_spec"
  assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" OPEN saturated "review_lease_selective_saturation_opens"

  printf '\n' >> "$RUN/REVIEW-CASCADE-ROUTE.json"
  assert_gate "$("$SCRIPT" delta --run "$RUN" --round 2 --scheduled-axes spec-correctness,failure-security,standards-integration --rerun-axes spec-correctness)" CLOSED review-cascade-route-inactive "review_lease_route_digest_drift_closes"

  jq --arg route_receipt_sha256 "$(hash_file "$RUN/REVIEW-CASCADE-ROUTE.json")" \
    '.route_receipt_sha256=$route_receipt_sha256' \
    "$RUN/review-deltas/r2.json" > "$RUN/review-deltas/r2.next"
  mv "$RUN/review-deltas/r2.next" "$RUN/review-deltas/r2.json"
  sed -i.bak 's/Architecture deliberation: off/Architecture deliberation: active/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
  assert_gate "$("$SCRIPT" delta --run "$RUN" --round 2 --scheduled-axes spec-correctness,failure-security,standards-integration --rerun-axes spec-correctness)" OPEN selective-review-ready "review_delta_critical_run_stays_incremental"
else
  fail "review_lease_calibration_setup"
fi

reset_run
write_candidate 1 spec-correctness NONE
rows="$(candidate_file_rows 1 spec-correctness)"
write_saturation_receipt 1 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 1 --axes spec-correctness)" OPEN saturated "full_once_source_round_opens"
printf 'NONE\n' > "$RUN/findings/r2-code-verified.md"
write_candidate 2 spec-correctness NONE
rows="$(candidate_file_rows 2 spec-correctness)"
write_saturation_receipt 2 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" CLOSED incremental-review-required "full_once_same_plan_second_full_closes"

printf 'materially changed architecture plan\n' > "$RUN/PLAN.md"
write_saturation_receipt 2 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 2 --axes spec-correctness)" OPEN saturated "full_once_changed_plan_allows_new_full_basis"

reset_run
printf 'NONE\n' > "$RUN/findings/r4-code-verified.md"
write_candidate 4 spec-correctness NONE
rows="$(candidate_file_rows 4 spec-correctness)"
write_saturation_receipt 4 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness)" CLOSED closeout-delta-required "closeout_round_rejects_full_review"

write_cascade_candidate 4 NONE
write_saturation_receipt 4 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '["still-open"]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness)" CLOSED closeout-carry-forbidden "closeout_round_rejects_open_cascade_carry"

line='CANDIDATE HIGH src/reviewed.py:1 :: late semantic discovery :: verify=verifier:inspect supported call path'
write_candidate 4 spec-correctness "$line"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness)" CLOSED closeout-candidates-forbidden "closeout_round_rejects_new_candidates"

reset_run
printf 'prior closeout plan\n' > "$RUN/PLAN.md"
printf 'NONE\n' > "$RUN/findings/r2-code-verified.md"
write_candidate 2 spec-correctness NONE
write_candidate 2 failure-security NONE
write_candidate 2 standards-integration NONE
rows="$(candidate_file_rows 2 spec-correctness failure-security standards-integration)"
write_saturation_receipt 2 \
  '["spec-correctness","failure-security","standards-integration"]' \
  '["spec-correctness","failure-security","standards-integration"]' \
  "$rows" '[]' '[]' 'null'
printf 'minimum complete plan\n' > "$RUN/PLAN.md"
before="$(hash_file "$RUN/PLAN.md")"
seed_v3_failure 3 closeout-recovery
write_candidate 3 failure-security NONE
write_candidate 3 standards-integration NONE
rows="$(candidate_file_rows 3 spec-correctness failure-security standards-integration)"
dispositions="$(jq -c .dispositions "$RUN/review-saturation/r3.json")"
write_saturation_receipt 3 \
  '["spec-correctness","failure-security","standards-integration"]' \
  '["spec-correctness","failure-security","standards-integration"]' \
  "$rows" "$dispositions" '[]' 'null'
printf '<!-- kimiflow:strategy gate=code epoch-start=1 fingerprint=%s -->\n' "$before" > "$RUN/RECOVERY.md"
printf 'plan-changing closeout repair\n' > "$RUN/PLAN.md"
after="$(hash_file "$RUN/PLAN.md")"
printf '<!-- kimiflow:recovery gate=code source-round=3 epoch-start=4 cap=4 before=%s after=%s -->\n' \
  "$before" "$after" >> "$RUN/RECOVERY.md"
resolved="$(write_evidence closeout-recovery.txt closeout-recovery 'verifier:inspect closeout-recovery runtime path' not_reproduced repaired)"
printf 'RESOLVED class=closeout-recovery :: verify=verifier:inspect closeout-recovery runtime path :: evidence=%s\n' \
  "$resolved" > "$RUN/findings/r4-code-verified.md"
write_candidate 4 spec-correctness NONE
rows="$(candidate_file_rows 4 spec-correctness)"
write_saturation_receipt 4 '["spec-correctness"]' '["spec-correctness"]' "$rows" '[]' '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness)" CLOSED closeout-delta-required "closeout_plan_recovery_cannot_omit_prior_axis"

write_candidate 4 failure-security NONE
write_candidate 4 standards-integration NONE
rows="$(candidate_file_rows 4 spec-correctness failure-security standards-integration)"
write_saturation_receipt 4 \
  '["spec-correctness","failure-security","standards-integration"]' \
  '["spec-correctness","failure-security","standards-integration"]' \
  "$rows" '[]' '[]' 'null'
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" CLOSED missing-repair "closeout_plan_recovery_requires_current_repair"

jq -n \
  --arg plan_sha256 "$after" \
  --arg findings_sha256 "$(hash_file "$RUN/findings/r3-code-verified.md")" \
  --arg source_saturation_sha256 "$(hash_file "$RUN/review-saturation/r3.json")" \
  '{
    schema_version:3,
    round:3,
    plan_sha256:$plan_sha256,
    findings_sha256:$findings_sha256,
    source_saturation_sha256:$source_saturation_sha256,
    groups:[{
      id:"closeout-recovery",
      classes:["closeout-recovery"],
      root_cause:"The reproduced class owns one authenticated review root cause.",
      depends_on:[],
      repair:"Apply the recovered plan and rerun its exact falsifier.",
      checks:[{kind:"verifier",method:"inspect closeout-recovery runtime path"}]
    }]
  }' > "$RUN/review-repairs/r3.json"
cp "$RUN/review-saturation/r3.json" "$RUN/review-saturation/r3.valid"
cp "$RUN/review-repairs/r3.json" "$RUN/review-repairs/r3.valid"
jq '.candidate_files=[]' "$RUN/review-saturation/r3.valid" > "$RUN/review-saturation/r3.json"
jq --arg source_saturation_sha256 "$(hash_file "$RUN/review-saturation/r3.json")" \
  '.source_saturation_sha256=$source_saturation_sha256' \
  "$RUN/review-repairs/r3.valid" > "$RUN/review-repairs/r3.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" CLOSED candidate-digest-mismatch "closeout_plan_recovery_rejects_prebound_candidate_forgery"

jq '.review_snapshot_sha256="0000000000000000000000000000000000000000000000000000000000000000"' \
  "$RUN/review-saturation/r3.valid" > "$RUN/review-saturation/r3.json"
jq --arg source_saturation_sha256 "$(hash_file "$RUN/review-saturation/r3.json")" \
  '.source_saturation_sha256=$source_saturation_sha256' \
  "$RUN/review-repairs/r3.valid" > "$RUN/review-repairs/r3.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" CLOSED stale-source-review-basis "closeout_plan_recovery_rejects_prebound_basis_forgery"
mv "$RUN/review-saturation/r3.valid" "$RUN/review-saturation/r3.json"
mv "$RUN/review-repairs/r3.valid" "$RUN/review-repairs/r3.json"

printf 'NONE\n' > "$RUN/findings/r4-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" CLOSED closeout-resolution-incomplete "closeout_plan_recovery_requires_negative_resolution_evidence"

different="$(write_evidence closeout-different-verifier.txt closeout-recovery 'verifier:inspect unrelated runtime path' not_reproduced unrelated)"
printf 'RESOLVED class=closeout-recovery :: verify=verifier:inspect unrelated runtime path :: evidence=%s\n' \
  "$different" > "$RUN/findings/r4-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" CLOSED closeout-resolution-verifier-mismatch "closeout_plan_recovery_requires_authoritative_verifier"

printf 'RESOLVED class=closeout-recovery :: verify=verifier:inspect closeout-recovery runtime path :: evidence=%s\n' \
  "$resolved" > "$RUN/findings/r4-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" CLOSED recovery-resolution-context-mismatch "closeout_plan_recovery_rejects_precomputed_resolution"

recovery_binding="recovery_repair_sha256=$(hash_file "$RUN/review-repairs/r3.json") :: closeout_saturation_sha256=$(hash_file "$RUN/review-saturation/r4.json")"
resolved="$(write_evidence closeout-recovery-current.txt closeout-recovery 'verifier:inspect closeout-recovery runtime path' not_reproduced "repaired :: ${recovery_binding}")"
printf 'RESOLVED class=closeout-recovery :: verify=verifier:inspect closeout-recovery runtime path :: evidence=%s\n' \
  "$resolved" > "$RUN/findings/r4-code-verified.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" OPEN saturated "closeout_plan_recovery_allows_context_bound_resolution_review"

cp "$RUN/review-saturation/r3.json" "$RUN/review-saturation/r3.saved"
jq '.candidate_files=[]' "$RUN/review-saturation/r3.saved" > "$RUN/review-saturation/r3.json"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" CLOSED stale-source-saturation "closeout_plan_recovery_rejects_source_receipt_drift"
mv "$RUN/review-saturation/r3.saved" "$RUN/review-saturation/r3.json"

printf '<!-- kimiflow:recovery gate=code malformed -->\n' >> "$RUN/RECOVERY.md"
assert_gate "$("$SCRIPT" saturation --run "$RUN" --round 4 --axes spec-correctness,failure-security,standards-integration)" CLOSED recovery-malformed "closeout_plan_recovery_malformed_marker_closes"

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

printf 'strategy three\n' > "$RUN/PLAN.md"
after_two="$(hash_file "$RUN/PLAN.md")"
marker_two="<!-- kimiflow:recovery gate=code source-round=2 epoch-start=3 cap=5 before=$after_one after=$after_two -->"
printf '%s\n' "$marker_two" >> "$RUN/RECOVERY.md"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 3)" CLOSED trajectory-source-schema-required "trajectory_legacy_source_receipts_close"

# Schema-4 saturation makes recovery class-scoped. A failure in class A and a
# later failure in class B do not manufacture a repeated-strategy loop.
reset_run
baseline="$(hash_file "$RUN/PLAN.md")"
printf '<!-- kimiflow:strategy gate=code epoch-start=1 fingerprint=%s -->\n' "$baseline" > "$RUN/RECOVERY.md"
printf 'class a strategy\n' > "$RUN/PLAN.md"
seed_v3_failure 1 class-a
after_one="$(hash_file "$RUN/PLAN.md")"
marker_one="<!-- kimiflow:recovery gate=code source-round=1 epoch-start=2 cap=4 before=$baseline after=$after_one -->"
printf '%s\n' "$marker_one" >> "$RUN/RECOVERY.md"
printf 'class b strategy\n' > "$RUN/PLAN.md"
seed_v3_failure 2 class-b
after_two="$(hash_file "$RUN/PLAN.md")"
marker_two="<!-- kimiflow:recovery gate=code source-round=2 epoch-start=3 cap=5 before=$after_one after=$after_two -->"
printf '%s\n' "$marker_two" >> "$RUN/RECOVERY.md"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 3)" OPEN below-threshold "trajectory_v2_different_classes_do_not_cross_count"

reset_run
baseline="$(hash_file "$RUN/PLAN.md")"
printf '<!-- kimiflow:strategy gate=code epoch-start=1 fingerprint=%s -->\n' "$baseline" > "$RUN/RECOVERY.md"
printf 'class a strategy one\n' > "$RUN/PLAN.md"
seed_v3_failure 1 class-a
after_one="$(hash_file "$RUN/PLAN.md")"
marker_one="<!-- kimiflow:recovery gate=code source-round=1 epoch-start=2 cap=4 before=$baseline after=$after_one -->"
printf '%s\n' "$marker_one" >> "$RUN/RECOVERY.md"
printf '%s\n' \
  'class a strategy two' \
  'Trajectory class: class-a' \
  'Trajectory action: replan' \
  'Trajectory hypothesis: The class-a repair targeted a symptom outside its shared boundary.' \
  'Changed assumption: Treat class-a as one boundary and falsify that boundary directly.' \
  'Trajectory check: command :: bash hooks/test-review-convergence-gate.sh' \
  > "$RUN/PLAN.md"
seed_v3_failure 2 class-a
after_two="$(hash_file "$RUN/PLAN.md")"
marker_two="<!-- kimiflow:recovery gate=code source-round=2 epoch-start=3 cap=5 before=$after_one after=$after_two -->"
printf '%s\n' "$marker_two" >> "$RUN/RECOVERY.md"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 3)" CLOSED trajectory-required "trajectory_v2_same_class_requires_changed_strategy"
hashes="$(jq -nc --arg one "$(hash_text "$marker_one")" --arg two "$(hash_text "$marker_two")" '[$one,$two]')"
jq -n \
  --arg plan_sha256 "$after_two" \
  --argjson receipt_sha256s "$hashes" \
  '{
    schema_version:2,
    source_round:2,
    plan_sha256:$plan_sha256,
    failed_source_rounds:[1,2],
    recovery_receipt_sha256s:$receipt_sha256s,
    prior_trajectory_sha256s:[],
    stable_class:"class-a",
    hypothesis:"The class-a repair targeted a symptom outside its shared boundary.",
    action:"replan",
    changed_assumption:"Treat class-a as one boundary and falsify that boundary directly.",
    checks:[{kind:"command",method:"bash hooks/test-review-convergence-gate.sh"}]
  }' > "$RUN/review-trajectories/source-r2.json"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 3)" OPEN trajectory-ready "trajectory_v2_same_class_changed_strategy_opens"

printf '%s\n' \
  'class a strategy three with reworded explanation' \
  'Trajectory class: class-a' \
  'Trajectory action: replan' \
  'Trajectory hypothesis: A newly worded explanation still points at the same class-a boundary.' \
  'Changed assumption: Rephrase the class-a assumption without changing the falsifier.' \
  'Trajectory check: command :: bash hooks/test-review-convergence-gate.sh' \
  > "$RUN/PLAN.md"
seed_v3_failure 3 class-a
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
    schema_version:2,
    source_round:3,
    plan_sha256:$plan_sha256,
    failed_source_rounds:[2,3],
    recovery_receipt_sha256s:$receipt_sha256s,
    prior_trajectory_sha256s:[$prior],
    stable_class:"class-a",
    hypothesis:"A newly worded explanation still points at the same class-a boundary.",
    action:"replan",
    changed_assumption:"Rephrase the class-a assumption without changing the falsifier.",
    checks:[{kind:"command",method:"bash hooks/test-review-convergence-gate.sh"}]
  }' > "$RUN/review-trajectories/source-r3.json"
assert_gate "$("$SCRIPT" preflight --run "$RUN" --round 4)" CLOSED trajectory-strategy-unchanged "trajectory_v2_reworded_same_strategy_closes"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
