#!/usr/bin/env bash
# kimiflow — unit tests for current-state-gate.sh.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/current-state-gate.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

if ! command -v jq >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  echo "SKIP: jq and python3 are required"; exit 0
fi

now="$(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
)"
stale="$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) - timedelta(days=45)).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
)"
future="$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
)"
five_days_ago="$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc) - timedelta(days=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
PY
)"

write_file() {
  local target="$1" text="$2"
  printf '%s\n' "$text" > "$target"
}

assert_assess() {
  local text="$1" risk="$2" name="$3" input out
  input="$WORK/$name.txt"
  write_file "$input" "$text"
  out="$("$SCRIPT" assess --input "$input")"
  if printf '%s\n' "$out" | jq -e \
    --arg risk "$risk" \
    '.schema_version == 2
     and .current_state_risk == $risk
     and (.research_subject_sha256 | test("^sha256:[a-f0-9]{64}$"))
     and (.research_terms | length) >= 1
     and (
       if $risk == "low" then
         .freshness_horizon_days == null and .minimum_source_count == 0
       else
         (.freshness_horizon_days | type) == "number"
         and .minimum_source_count == 1
         and (.acceptable_source_types | length) >= 2
       end
     )' >/dev/null 2>&1; then
    pass "$name"
  else
    fail "$name"
    printf '%s\n' "$out"
  fi
}

assert_legacy_verify() {
  local assessment_text="$1" recall_text="$2" expected="$3" reason="$4" name="$5"
  local assessment recall out verdict got_reason
  assessment="$WORK/$name.assessment.json"
  recall="$WORK/$name.recall.md"
  write_file "$assessment" "$assessment_text"
  write_file "$recall" "$recall_text"
  out="$("$SCRIPT" verify --assessment "$assessment" --recall "$recall")"
  verdict="$(printf '%s\n' "$out" | awk -F '\t' '{print $2}')"
  got_reason="$(printf '%s\n' "$out" | awk -F '\t' '{sub(/^reason=/, "", $4); print $4}')"
  if [ "$verdict" = "$expected" ] && [ "$got_reason" = "$reason" ]; then
    pass "$name"
  else
    fail "$name"
    printf '%s\n' "$out"
  fi
}

assert_v2_verify() {
  local assessment_text="$1" sources_text="$2" expected="$3" reason="$4" name="$5"
  local assessment sources out verdict got_reason risk subject_input
  assessment="$WORK/$name.assessment.json"
  sources="$WORK/$name.sources.json"
  write_file "$assessment" "$assessment_text"
  write_file "$sources" "$sources_text"
  risk="$(printf '%s\n' "$assessment_text" | jq -r '.current_state_risk')"
  case "$risk" in
    high) subject_input="$HIGH_INPUT" ;;
    medium) subject_input="$MEDIUM_INPUT" ;;
    low) subject_input="$LOW_INPUT" ;;
    *) subject_input="$LOW_INPUT" ;;
  esac
  out="$("$SCRIPT" verify --assessment "$assessment" --input "$subject_input" --sources "$sources")"
  verdict="$(printf '%s\n' "$out" | awk -F '\t' '{print $2}')"
  got_reason="$(printf '%s\n' "$out" | awk -F '\t' '{sub(/^reason=/, "", $4); print $4}')"
  if [ "$verdict" = "$expected" ] && [ "$got_reason" = "$reason" ]; then
    pass "$name"
  else
    fail "$name"
    printf '%s\n' "$out"
  fi
}

source_receipt() {
  local retrieved="$1" status="$2" source_type="$3" url="$4" applies="$5" version="$6" basis="$7"
  local subject="${8:-$HIGH_SUBJECT}"
  jq -nc \
    --arg now "$now" \
    --arg retrieved "$retrieved" \
    --arg status "$status" \
    --arg source_type "$source_type" \
    --arg url "$url" \
    --arg applies "$applies" \
    --arg version "$version" \
    --arg basis "$basis" \
    --arg subject "$subject" \
    '{
      schema_version: 2,
      status: "checked",
      checked_at: $now,
      research_subject_sha256: $subject,
      sources: [{
        source_type: $source_type,
        source_url: $url,
        retrieved_at: $retrieved,
        applies_to: $applies,
        version_or_release: $version,
        freshness_basis: $basis,
        status: $status
      }]
    }'
}

assert_assess "Rename helper variable in local shell script" "low" "low_local_change"
assert_assess "Build a Codex and Claude Code plugin hook for MCP marketplace behavior" "high" "high_host_plugin_surface"
assert_assess "Implement Stripe payment auth deployment SDK flow" "high" "high_security_external_surface"
assert_assess "Update React dependency usage for new framework API" "medium" "medium_library_api_surface"
assert_assess "Research current software architecture and coding methods" "medium" "medium_generic_architecture_surface"
assert_assess "Fix capitalization in a local message" "low" "low_api_substring_does_not_route"

LEGACY_HIGH='{"schema_version":1,"current_state_risk":"high"}'
LEGACY_LOW='{"schema_version":1,"current_state_risk":"low"}'
assert_legacy_verify "$LEGACY_LOW" "" "OPEN" "not-required" "legacy_low_opens_without_recall"
assert_legacy_verify "$LEGACY_HIGH" "# Recall" "CLOSED" "not-checked" "legacy_high_closes_without_checked_source"
assert_legacy_verify "$LEGACY_HIGH" "# Recall

Status: checked

- source_type: official_docs
  source_url: https://developers.openai.com/codex/hooks" "OPEN" "checked" "legacy_high_stays_compatible"

HIGH_INPUT="$WORK/high-subject.txt"
MEDIUM_INPUT="$WORK/medium-subject.txt"
LOW_INPUT="$WORK/low-subject.txt"
write_file "$HIGH_INPUT" "Build a Codex plugin hook"
write_file "$MEDIUM_INPUT" "Update React framework API usage"
write_file "$LOW_INPUT" "Rename local helper variable"
HIGH="$("$SCRIPT" assess --input "$HIGH_INPUT")"
MEDIUM="$("$SCRIPT" assess --input "$MEDIUM_INPUT")"
LOW="$("$SCRIPT" assess --input "$LOW_INPUT")"
HIGH_SUBJECT="$(printf '%s\n' "$HIGH" | jq -r '.research_subject_sha256')"
MEDIUM_SUBJECT="$(printf '%s\n' "$MEDIUM" | jq -r '.research_subject_sha256')"

assert_v2_verify "$LOW" '{}' "OPEN" "not-required" "schema2_low_opens_without_sources"
assert_v2_verify "$MEDIUM" "$(source_receipt "$now" current official_docs https://react.dev/reference/react 'React API' current current_official_page "$MEDIUM_SUBJECT")" "OPEN" "checked" "schema2_current_official_source_opens"
assert_v2_verify "$HIGH" "$(source_receipt "$now" current schema_or_manifest https://www.ietf.org/rfc/rfc9110.html 'Codex plugin HTTP semantics' RFC-9110 stable_standard)" "OPEN" "checked" "schema2_old_stable_standard_can_open"
assert_v2_verify "$HIGH" "$(source_receipt "$stale" current official_docs https://example.com/docs 'Codex plugin' v2 current_official_page)" "CLOSED" "stale-source" "schema2_stale_retrieval_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$future" current official_docs https://example.com/docs 'Codex plugin' v2 current_official_page)" "CLOSED" "future-timestamp" "schema2_future_retrieval_closes"
after_check="$(source_receipt "$now" current official_docs https://example.com/docs 'Codex plugin' v2 current_official_page | jq -c --arg checked "$five_days_ago" '.checked_at = $checked')"
assert_v2_verify "$HIGH" "$after_check" "CLOSED" "source-after-check" "schema2_retrieval_after_check_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$now" stale official_docs https://example.com/docs 'Codex plugin' v2 current_official_page)" "CLOSED" "source-not-current" "schema2_stale_status_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$now" current blog https://example.com/post 'Codex plugin' v2 current_official_page)" "CLOSED" "source-type-not-acceptable" "schema2_unaccepted_type_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$now" current official_docs http://example.com/docs 'Codex plugin' v2 current_official_page)" "CLOSED" "source-url-invalid" "schema2_http_source_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$now" current official_docs https://example.com:bad/docs 'Codex plugin' v2 current_official_page)" "CLOSED" "source-url-invalid" "schema2_invalid_port_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$now" current official_docs https://example.com/docs '' v2 current_official_page)" "CLOSED" "source-applicability-missing" "schema2_missing_applicability_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$now" current official_docs https://example.com/docs 'Codex plugin' '' current_official_page)" "CLOSED" "source-version-missing" "schema2_missing_version_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$now" current official_docs https://example.com/docs 'Codex plugin' v2 guess)" "CLOSED" "freshness-basis-invalid" "schema2_bad_basis_closes"
assert_v2_verify "$HIGH" "$(source_receipt "$now" current official_docs https://example.com/docs Database-storage v2 current_official_page)" "CLOSED" "source-applicability-mismatch" "schema2_unrelated_applicability_closes"
wrong_subject="$(source_receipt "$now" current official_docs https://example.com/docs 'Codex plugin' v2 current_official_page "$MEDIUM_SUBJECT")"
assert_v2_verify "$HIGH" "$wrong_subject" "CLOSED" "research-subject-mismatch" "schema2_wrong_subject_closes"

duplicate="$(source_receipt "$now" current official_docs https://example.com/docs 'Codex plugin' v2 current_official_page | jq -c '.sources += [.sources[0]]')"
assert_v2_verify "$HIGH" "$duplicate" "CLOSED" "duplicate-source" "schema2_duplicate_source_closes"

assessment="$WORK/schema2_missing_sources.assessment.json"
write_file "$assessment" "$HIGH"
out="$("$SCRIPT" verify --assessment "$assessment" --input "$HIGH_INPUT")"
if printf '%s\n' "$out" | grep -q $'CURRENT_STATE_GATE\tCLOSED\t.*reason=missing-sources'; then
  pass "schema2_missing_sources_argument_closes"
else
  fail "schema2_missing_sources_argument_closes"
  printf '%s\n' "$out"
fi

write_file "$WORK/real-sources.json" "$(source_receipt "$now" current official_docs https://example.com/docs 'Codex plugin' v2 current_official_page)"
ln -s real-sources.json "$WORK/link-sources.json"
out="$("$SCRIPT" verify --assessment "$assessment" --input "$HIGH_INPUT" --sources "$WORK/link-sources.json")"
if printf '%s\n' "$out" | grep -q 'reason=unsafe-sources'; then
  pass "schema2_symlink_sources_close"
else
  fail "schema2_symlink_sources_close"
  printf '%s\n' "$out"
fi

out="$("$SCRIPT" verify --assessment "$assessment" --sources "$WORK/real-sources.json")"
if printf '%s\n' "$out" | grep -q 'reason=missing-subject-input'; then
  pass "schema2_missing_subject_input_closes"
else
  fail "schema2_missing_subject_input_closes"
  printf '%s\n' "$out"
fi

write_file "$WORK/changed-subject.txt" "Build a database migration"
out="$("$SCRIPT" verify --assessment "$assessment" --input "$WORK/changed-subject.txt" --sources "$WORK/real-sources.json")"
if printf '%s\n' "$out" | grep -q 'reason=research-policy-mismatch'; then
  pass "schema2_changed_subject_closes"
else
  fail "schema2_changed_subject_closes"
  printf '%s\n' "$out"
fi

tampered="$WORK/tampered-risk.assessment.json"
printf '%s\n' "$HIGH" | jq -c \
  '.current_state_risk = "low"
   | .current_state_reasons = []
   | .freshness_horizon = null
   | .freshness_horizon_days = null
   | .minimum_source_count = 0
   | .acceptable_source_types = []
   | .required_source_types = []
   | .status = "not_required"' > "$tampered"
out="$("$SCRIPT" verify --assessment "$tampered" --input "$HIGH_INPUT" --sources "$WORK/real-sources.json")"
if printf '%s\n' "$out" | grep -q 'reason=research-policy-mismatch'; then
  pass "schema2_risk_downgrade_closes"
else
  fail "schema2_risk_downgrade_closes"
  printf '%s\n' "$out"
fi

tampered="$WORK/tampered-horizon.assessment.json"
printf '%s\n' "$HIGH" | jq -c \
  '.freshness_horizon = "3650d" | .freshness_horizon_days = 3650' > "$tampered"
out="$("$SCRIPT" verify --assessment "$tampered" --input "$HIGH_INPUT" --sources "$WORK/real-sources.json")"
if printf '%s\n' "$out" | grep -q 'reason=research-policy-mismatch'; then
  pass "schema2_horizon_expansion_closes"
else
  fail "schema2_horizon_expansion_closes"
  printf '%s\n' "$out"
fi

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
