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
intent_contract="$(kimiflow_state_value "$state" "Intent contract" | awk '{print $1}')"

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

schema2_intake=0
if [ -f "$run_dir/INTENT-LOCK.json" ] && command -v python3 >/dev/null 2>&1; then
  schema2_intake="$(python3 - "$run_dir/INTENT-LOCK.json" <<'PY' 2>/dev/null || true
import json, os, sys
try:
    path=sys.argv[1]
    if os.path.islink(path): raise OSError
    with open(path,encoding="utf-8") as handle: value=json.load(handle)
    print(1 if isinstance(value,dict) and value.get("schema_version")==2 and value.get("contract")==4 else 0)
except (OSError,ValueError,UnicodeError):
    print(0)
PY
)"
fi
if [ "$schema2_intake" = "1" ]; then
  schema2_details="$(python3 - "$run_dir" <<'PY'
import hashlib, json, os, re, sys
run_dir=os.path.realpath(sys.argv[1])
root=os.path.realpath(os.path.join(run_dir,"..",".."))
errors=[]
research_path=os.path.join(run_dir,"RESEARCH.md")
basis_path=os.path.join(run_dir,"CODEBASE-BASIS.json")
try:
    with open(research_path,encoding="utf-8") as handle: text=handle.read()
    with open(basis_path,"rb") as handle: basis_payload=handle.read()
    basis_digest="sha256:"+hashlib.sha256(basis_payload).hexdigest()
except (OSError,UnicodeError):
    print("codebase_basis_missing"); raise SystemExit

def one(label):
    values=re.findall(r"^%s:\s*(\S.+)$"%re.escape(label),text,re.M)
    if len(values)!=1:
        errors.append(label.lower().replace(" ","_")+"_invalid")
        return ""
    return values[0].strip()

markers=re.findall(r"^<!-- kimiflow:reuse-order ([^\n]+) -->$",text,re.M)
attrs=dict(re.findall(r"([a-z_]+)=([A-Za-z0-9_-]+)",markers[0])) if len(markers)==1 else {}
if attrs.get("contract")!="4" or attrs.get("schema")!="2": errors.append("reuse_order_marker_invalid")
reuse=attrs.get("reuse")
evolve=attrs.get("evolve")
new=attrs.get("new")
selected=attrs.get("selected")
reuse_candidate=one("Reuse candidate")
reuse_evidence=one("Reuse evidence")
one("Own idea"); one("Research finding"); one("Code comparison")
if one("Codebase basis")!=basis_digest: errors.append("research_codebase_basis_stale")
if one("Scope result")!="non_expanded": errors.append("research_scope_expanded")

def current_source(value,label):
    match=re.fullmatch(r"([^\s:]+):(\d+)",value)
    if not match:
        errors.append(label+"_invalid"); return
    path=os.path.realpath(os.path.join(root,match.group(1)))
    try:
        if os.path.commonpath((root,path))!=root or os.path.islink(path) or not os.path.isfile(path): raise OSError
    except (OSError,ValueError): errors.append(label+"_invalid")

if reuse_candidate: current_source(reuse_evidence,"reuse_evidence")
if reuse=="fit":
    if selected!="reuse" or evolve!="not_needed" or new!="not_needed": errors.append("reuse_order_invalid")
elif reuse=="gap":
    evolve_candidate=one("Evolve candidate")
    evolve_evidence=one("Evolve evidence")
    if evolve_candidate: current_source(evolve_evidence,"evolve_evidence")
    if evolve=="fit":
        if selected!="evolve" or new!="not_needed": errors.append("reuse_order_invalid")
    elif evolve=="gap":
        one("New gap")
        falsifier=one("New falsifier")
        if selected!="new" or new!="selected" or not re.match(r"^(command|verifier) :: \S",falsifier): errors.append("new_without_proven_gap")
    else: errors.append("reuse_order_invalid")
else: errors.append("reuse_order_invalid")

scope_markers=re.findall(r"^<!-- kimiflow:scope-research ([^\n]+) -->$",text,re.M)
scope_attrs=dict(re.findall(r"([a-z_]+)=([A-Za-z0-9_:-]+)",scope_markers[0])) if len(scope_markers)==1 else {}
if (
    scope_attrs.get("contract")!="4"
    or scope_attrs.get("schema")!="2"
    or scope_attrs.get("codebase_basis")!=basis_digest
    or not re.fullmatch(r"sha256:[0-9a-f]{64}",scope_attrs.get("scope", ""))
    or scope_attrs.get("selection")!="non_expanded"
): errors.append("scope_research_not_bound")
print(",".join(dict.fromkeys(errors)))
PY
)"
  if [ -n "$schema2_details" ]; then
    old_ifs="$IFS"; IFS=','; set -- $schema2_details; IFS="$old_ifs"
    for detail in "$@"; do add_blocker "$detail"; done
  fi
fi

if [ "$nontrivial_feature" -eq 1 ] && [ "$intent_contract" = "3" ]; then
  feasibility_lines="$(grep -E '^<!--[[:space:]]*kimiflow:feasibility[[:space:]][^>]*-->$' "$research" 2>/dev/null || true)"
  feasibility_count="$(printf '%s\n' "$feasibility_lines" | grep -c . || true)"
  if [ "$feasibility_count" -ne 1 ]; then
    add_blocker "feasibility_marker_invalid"
  else
    feasibility="$(printf '%s\n' "$feasibility_lines" | sed 's/<!--[[:space:]]*//; s/[[:space:]]*-->//')"
    feasibility_value() {
      printf '%s\n' "$feasibility" | sed -n "s/.*$1=\([A-Za-z0-9_-][A-Za-z0-9_-]*\).*/\1/p" | tr '[:upper:]' '[:lower:]'
    }
    feasibility_status="$(feasibility_value status)"
    feasibility_user_gate="$(feasibility_value user_gate)"
    feasibility_decision="$(feasibility_value decision)"
    summary_count="$(grep -Ec '^Feasibility summary:[[:space:]]*\S' "$research" 2>/dev/null || true)"
    [ "$summary_count" -eq 1 ] || add_blocker "feasibility_summary_invalid"
    case "$feasibility_status" in
      fit|evolve)
        [ "$feasibility_user_gate" = "no" ] || add_blocker "feasibility_user_gate_invalid"
        [ "$feasibility_decision" = "not_required" ] || add_blocker "feasibility_decision_invalid"
        ;;
      replace)
        [ "$feasibility_user_gate" = "yes" ] || add_blocker "feasibility_replace_unconfirmed"
        [ "$feasibility_decision" = "confirmed" ] || add_blocker "feasibility_replace_unconfirmed"
        grep -Eiq '^Feasibility decision kind:[[:space:]]*(scope-risk|irreversible)[[:space:]]*$' "$research" || add_blocker "feasibility_decision_kind_invalid"
        ;;
      conflict|unproven) add_blocker "feasibility_status_$feasibility_status" ;;
      *) add_blocker "feasibility_status_invalid" ;;
    esac
  fi
fi

if [ "$blockers" -eq 0 ]; then
  emit OPEN 0 clean ""
fi
emit CLOSED "$blockers" discovery-blockers "$details"
