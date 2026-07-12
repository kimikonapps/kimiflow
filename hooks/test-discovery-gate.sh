#!/usr/bin/env bash
# kimiflow — unit tests for discovery-gate.sh.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/discovery-gate.sh"
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
Flow schema: 3
Discovery required: yes
EOF
  cat > "$RUN/RESEARCH.md" <<'EOF'
# Research
<!-- kimiflow:discovery depth=pulse status=sufficient lanes=complete claims=none technical_gaps=0 user_decisions=0 scope_change=no -->
## Assessment
Project evidence is sufficient; no external plan-changing claim is needed.
EOF
}

run_gate() { "$SCRIPT" "$RUN"; }

reset_run
out="$(run_gate)"
assert_field "$out" 2 OPEN "complete_discovery_opens"
assert_contains "$out" "reason=clean" "complete_discovery_reason"

reset_run
sed -i.bak 's/claims=none/claims=sourced/' "$RUN/RESEARCH.md" && rm "$RUN/RESEARCH.md.bak"
printf '%s\n' '- source_url: https://example.com/official' >> "$RUN/RESEARCH.md"
printf '%s\n' '  source_type: official_docs' >> "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "sourced_claim_opens"

reset_run
sed -i.bak 's/claims=none/claims=sourced/' "$RUN/RESEARCH.md" && rm "$RUN/RESEARCH.md.bak"
printf '%s\n' '- source_type: official_docs' >> "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "sourced_claim_without_url_closes"
assert_contains "$out" "external_claim_source_missing" "sourced_claim_without_url_detail"

reset_run
sed -i.bak 's/claims=none/claims=sourced/' "$RUN/RESEARCH.md" && rm "$RUN/RESEARCH.md.bak"
printf '%s\n' '- source_url: https://example.com/official' >> "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "sourced_claim_without_type_closes"
assert_contains "$out" "external_claim_source_type_missing" "sourced_claim_without_type_detail"

reset_run
rm "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "missing_research_closes"

reset_run
sed -i.bak '/kimiflow:discovery/d' "$RUN/RESEARCH.md" && rm "$RUN/RESEARCH.md.bak"
out="$(run_gate)"
assert_contains "$out" "discovery_marker_missing" "missing_marker_detail"

for pair in \
  'depth=pulse depth=deep discovery_depth_invalid' \
  'status=sufficient status=conflicting discovery_status_conflicting' \
  'lanes=complete lanes=pending research_lanes_incomplete' \
  'technical_gaps=0 technical_gaps=1 technical_gaps_open' \
  'user_decisions=0 user_decisions=1 user_decisions_open' \
  'scope_change=no scope_change=pending scope_change_unconfirmed'
do
  reset_run
  set -- $pair
  sed -i.bak "s/$1/$2/" "$RUN/RESEARCH.md" && rm "$RUN/RESEARCH.md.bak"
  out="$(run_gate)"
  assert_field "$out" 2 CLOSED "$3"
  assert_contains "$out" "$3" "${3}_detail"
done

reset_run
sed -i.bak -e '/Flow schema:/d' -e '/Discovery required:/d' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
rm "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "legacy_run_without_marker_opens"
assert_contains "$out" "reason=legacy" "legacy_run_reason"

reset_run
sed -i.bak 's/Discovery required: yes/Discovery required: no/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
rm "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "feature_cannot_disable_discovery"
assert_contains "$out" "discovery_requirement_mode_mismatch" "feature_disable_detail"

reset_run
sed -i.bak '/Discovery required:/d' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
rm "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "new_feature_missing_requirement_closes"
assert_contains "$out" "discovery_requirement_missing" "new_feature_missing_requirement_detail"

reset_run
sed -i.bak -e 's/Mode: feature/Mode: fix/' -e 's/Discovery required: yes/Discovery required: no/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
rm "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "fix_explicit_not_required_opens"

reset_run
sed -i.bak -e 's/Scope: small/Scope: trivial/' -e 's/Discovery required: yes/Discovery required: no/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
rm "$RUN/RESEARCH.md"
out="$(run_gate)"
assert_field "$out" 2 OPEN "trivial_feature_not_required_opens"

reset_run
sed -i.bak -e 's/Mode: feature/Mode: fix/' -e '/Discovery required:/d' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "new_fix_missing_requirement_closes"
assert_contains "$out" "discovery_requirement_missing" "new_fix_missing_requirement_detail"

reset_run
sed -i.bak 's/Mode: feature/Mode: fix/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "new_fix_cannot_require_feature_discovery"
assert_contains "$out" "discovery_requirement_mode_mismatch" "new_fix_requirement_detail"

reset_run
sed -i.bak 's/Flow schema: 3/Flow schema: invalid/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "invalid_flow_schema_closes"
assert_contains "$out" "flow_schema_invalid" "invalid_flow_schema_detail"

reset_run
sed -i.bak -e 's/Mode: feature/Mode: featre/' -e 's/Discovery required: yes/Discovery required: no/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "new_flow_invalid_mode_closes"
assert_contains "$out" "flow_mode_invalid" "new_flow_invalid_mode_detail"

reset_run
sed -i.bak 's/Scope: small/Scope: unknown/' "$RUN/STATE.md" && rm "$RUN/STATE.md.bak"
out="$(run_gate)"
assert_field "$out" 2 CLOSED "new_flow_invalid_scope_closes"
assert_contains "$out" "flow_scope_invalid" "new_flow_invalid_scope_detail"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
