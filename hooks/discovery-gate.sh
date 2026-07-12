#!/usr/bin/env bash
# kimiflow — mechanical Phase-2 discovery completeness gate.
# It validates declared state and evidence shape; it does not judge research quality.
#
# Usage: discovery-gate.sh <run-dir> [--pretty]
# Output: DISCOVERY_GATE<TAB>OPEN|CLOSED<TAB>blockers=<n><TAB>reason=<code><TAB>detail=<codes>
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hooks/kimiflow-lib.sh
. "$SCRIPT_DIR/kimiflow-lib.sh"

emit() {
  printf 'DISCOVERY_GATE\t%s\tblockers=%s\treason=%s\tdetail=%s\n' "$1" "$2" "$3" "${4:-}"
  exit 0
}

run_dir=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --pretty) shift ;;
    -*) shift ;;
    *) [ -z "$run_dir" ] && run_dir="$1"; shift ;;
  esac
done

[ -n "$run_dir" ] || emit CLOSED 1 malformed "missing_run_dir"
[ -d "$run_dir" ] || emit CLOSED 1 malformed "run_dir_missing"

state="$run_dir/STATE.md"
flow_schema="$(kimiflow_state_value "$state" "Flow schema" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
mode="$(kimiflow_state_value "$state" "Mode" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
scope="$(kimiflow_state_value "$state" "Scope" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
required="$(kimiflow_state_value "$state" "Discovery required" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"

case "$flow_schema" in
  "") ;;
  *[!0-9]*) emit CLOSED 1 malformed "flow_schema_invalid" ;;
esac

new_flow=0
if [ -n "$flow_schema" ] && [ "$flow_schema" -ge 2 ]; then
  new_flow=1
fi
if [ "$new_flow" -eq 1 ]; then
  case "$mode" in feature|fix|audit|feature-check|review) ;; *) emit CLOSED 1 malformed "flow_mode_invalid" ;; esac
  case "$scope" in trivial|small|large) ;; *) emit CLOSED 1 malformed "flow_scope_invalid" ;; esac
fi
nontrivial_feature=0
if [ "$mode" = "feature" ] && [ "$scope" != "trivial" ]; then
  nontrivial_feature=1
fi

case "$required" in
  yes|true|1|required)
    if [ "$new_flow" -eq 1 ] && [ "$nontrivial_feature" -eq 0 ]; then
      emit CLOSED 1 malformed "discovery_requirement_mode_mismatch"
    fi
    ;;
  no|false|0|not_required)
    [ "$nontrivial_feature" -eq 0 ] || emit CLOSED 1 malformed "discovery_requirement_mode_mismatch"
    emit OPEN 0 not-required "explicitly_not_required"
    ;;
  "")
    if [ "$new_flow" -eq 1 ]; then
      emit CLOSED 1 malformed "discovery_requirement_missing"
    fi
    emit OPEN 0 legacy "discovery_requirement_absent"
    ;;
  *) emit CLOSED 1 malformed "discovery_requirement_invalid" ;;
esac

research="$run_dir/RESEARCH.md"
[ -s "$research" ] || emit CLOSED 1 discovery-missing "research_artifact_missing"

marker="$(grep -Eio '<!--[[:space:]]*kimiflow:discovery[^>]*-->|kimiflow:discovery[^[:cntrl:]]*' "$research" | head -1 || true)"
marker="$(printf '%s\n' "$marker" | sed 's/<!--[[:space:]]*//; s/[[:space:]]*-->//')"
[ -n "$marker" ] || emit CLOSED 1 discovery-missing "discovery_marker_missing"

value() {
  printf '%s\n' "$marker" | sed -n "s/.*$1=\([A-Za-z0-9_-][A-Za-z0-9_-]*\).*/\1/p" | tr '[:upper:]' '[:lower:]'
}

depth="$(value depth)"
status="$(value status)"
lanes="$(value lanes)"
claims="$(value claims)"
technical_gaps="$(value technical_gaps)"
user_decisions="$(value user_decisions)"
scope_change="$(value scope_change)"

blockers=0
details=""
add_blocker() {
  blockers=$((blockers + 1))
  if [ -z "$details" ]; then details="$1"; else details="$details,$1"; fi
}

case "$depth" in none|pulse|focused) ;; *) add_blocker "discovery_depth_invalid" ;; esac
case "$status" in
  sufficient|not_required) ;;
  incomplete|conflicting|stale|blocked) add_blocker "discovery_status_$status" ;;
  *) add_blocker "discovery_status_invalid" ;;
esac
case "$lanes" in none|complete) ;; *) add_blocker "research_lanes_incomplete" ;; esac
case "$claims" in
  none) ;;
  sourced)
    grep -Eiq 'source_url:[[:space:]]*https?://' "$research" || add_blocker "external_claim_source_missing"
    grep -Eiq 'source_type:[[:space:]]*[A-Za-z0-9_-]+' "$research" || add_blocker "external_claim_source_type_missing"
    ;;
  *) add_blocker "external_claims_invalid" ;;
esac
case "$technical_gaps" in 0) ;; ""|*[!0-9]*) add_blocker "technical_gaps_invalid" ;; *) add_blocker "technical_gaps_open" ;; esac
case "$user_decisions" in 0) ;; ""|*[!0-9]*) add_blocker "user_decisions_invalid" ;; *) add_blocker "user_decisions_open" ;; esac
case "$scope_change" in no|confirmed) ;; pending|yes) add_blocker "scope_change_unconfirmed" ;; *) add_blocker "scope_change_invalid" ;; esac

if [ "$blockers" -eq 0 ]; then
  emit OPEN 0 clean ""
fi
emit CLOSED "$blockers" discovery-blockers "$details"
