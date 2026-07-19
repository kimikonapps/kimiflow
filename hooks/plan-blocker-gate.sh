#!/usr/bin/env bash
# kimiflow — plan-blocker gate. Mechanical, plan-agnostic pre-review guard.
#
# Usage:
#   plan-blocker-gate.sh <run-dir> [--pretty]
#
# Output:
#   PLAN_BLOCKER_GATE<TAB>OPEN|CLOSED<TAB>blockers=<n><TAB>reason=<code><TAB>detail=<codes>
#
# This is intentionally conservative and language-agnostic. It does not judge whether
# a plan is good; it blocks plans that are not implementable/verifiable enough to
# deserve an expensive reviewer round.
# R2 invariant target: hooks/plan-blocker-gate.sh
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hooks/kimiflow-lib.sh
. "$SCRIPT_DIR/kimiflow-lib.sh"

emit() {
  printf 'PLAN_BLOCKER_GATE\t%s\tblockers=%s\treason=%s\tdetail=%s\n' "$1" "$2" "$3" "${4:-}"
  exit 0
}

run_dir=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --pretty) shift ;;   # accepted, reserved no-op (no pretty-print path implemented)
    -*) shift ;;
    *) [ -z "$run_dir" ] && run_dir="$1"; shift ;;
  esac
done

[ -n "$run_dir" ] || emit CLOSED 1 malformed "missing_run_dir"
[ -d "$run_dir" ] || emit CLOSED 1 malformed "run_dir_missing"

state="$run_dir/STATE.md"
plan="$run_dir/PLAN.md"
acceptance="$run_dir/ACCEPTANCE.md"

find_first() {
  local p
  for p in "$@"; do
    [ -f "$run_dir/$p" ] && { printf '%s\n' "$run_dir/$p"; return 0; }
  done
  return 1
}

intent="$(find_first INTENT.md PROBLEM.md AUDIT-INTENT.md 2>/dev/null || true)"
understanding="$(find_first RESEARCH.md DIAGNOSIS.md AUDIT.md 2>/dev/null || true)"

# Audit runs carry AUDIT-INTENT.md + AUDIT.md (slices), not PLAN.md/ACCEPTANCE.md. Detect the
# audit profile so the executable-plan checks below don't hard-require plan artifacts (deadlock).
mode_value="$(kimiflow_state_value "$state" mode | tr '[:upper:]' '[:lower:]')"
alias_value="$(kimiflow_state_value "$state" alias | tr '[:upper:]' '[:lower:]')"
audit_mode=0
if printf '%s\n%s\n' "$mode_value" "$alias_value" | grep -Eiq '(^|[^a-z])audit([^a-z]|$)'; then
  audit_mode=1
elif [ -f "$run_dir/AUDIT-INTENT.md" ] && [ ! -f "$plan" ]; then
  audit_mode=1
fi

blockers=0
details=""
add_blocker() {
  blockers=$((blockers + 1))
  if [ -z "$details" ]; then details="$1"; else details="$details,$1"; fi
}

phase_read_blocker() {
  local root run_rel gate out status detail
  kimiflow_phase_reads_required "$run_dir" "$state" || return 0
  root="$(kimiflow_run_root "$run_dir" 2>/dev/null || true)"
  [ -n "$root" ] || { printf 'phase_read_root_unknown\n'; return 0; }
  run_rel="$(kimiflow_run_rel "$root" "$run_dir" 2>/dev/null || true)"
  [ -n "$run_rel" ] || { printf 'phase_read_run_unknown\n'; return 0; }
  gate="$SCRIPT_DIR/active-run.sh"
  [ -x "$gate" ] || { printf 'phase_read_gate_missing\n'; return 0; }
  out="$("$gate" phase-read-gate --root "$root" --run "$run_rel" --through-phase 4 2>/dev/null)"
  status="$(printf '%s\n' "$out" | cut -f2)"
  detail="$(printf '%s\n' "$out" | cut -f5 | sed 's/^detail=//')"
  case "$status" in
    OPEN) return 0 ;;
    CLOSED) printf 'phase_read_gate_closed:%s\n' "${detail:-unknown}"; return 0 ;;
    *) printf 'phase_read_gate_error\n'; return 0 ;;
  esac
}

ac_token_pattern() {
  printf '(^|[^[:alnum:]_-])%s([^[:alnum:]_-]|$)' "$1"
}

file_has_ac_token() {
  local file="$1" ac="$2"
  [ -f "$file" ] && grep -Eq "$(ac_token_pattern "$ac")" "$file"
}

PATH_RE='(^|[[:space:][:punct:]])([A-Za-z0-9._/-]+\.[A-Za-z0-9]{1,8}|[A-Za-z0-9._/-]*(Dockerfile|Containerfile|Makefile|Procfile|Justfile|Rakefile|Gemfile|Vagrantfile))(:[0-9]+)?([[:space:][:punct:]]|$)'
file_has_path_evidence() {
  local file="$1"
  [ -f "$file" ] && grep -Eq "$PATH_RE" "$file"
}

# Accepted "Affected" header set (case-insensitive via tolower, POSIX awk) — keep in sync
# with AFFECTED_HEADER_RE / run_affected_paths in hooks/kimiflow_core/active_run.py: every
# header/source this gate accepts must also be visible to the staleness parser, or a plan
# passes the gate but staleness stays unknown and finish wedges.
file_declares_affected_paths() {
  local file="$1"
  [ -f "$file" ] || return 1
  awk '
    {
      line = $0
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      plain = line
      sub(/^[[:space:]]*-[[:space:]]*/, "", plain)
      if (tolower(plain) ~ /^[[:space:]]*(affected files|affected paths|files|paths|touches)[[:space:]]*:/) {
        sub(/^[^:]*:[[:space:]]*/, "", plain)
        if (length(plain) > 0) print plain
        in_list = 1
        next
      }
      if (in_list && line ~ /^[[:space:]]*-[[:space:]]+/) {
        sub(/^[[:space:]]*-[[:space:]]+/, "", line)
        print line
        next
      }
      if (in_list && line !~ /^[[:space:]]*$/) in_list = 0
    }
  ' "$file" | grep -Eq "$PATH_RE"
}

require_file() {
  local path="$1" code="$2"
  if [ -z "$path" ] || [ ! -f "$path" ]; then
    add_blocker "$code"
    return 1
  fi
  if [ ! -s "$path" ]; then
    add_blocker "${code}_empty"
    return 1
  fi
  return 0
}

require_file "$state" state_missing >/dev/null || true
require_file "$intent" intent_missing >/dev/null || true
require_file "$understanding" understanding_missing >/dev/null || true
if [ "$audit_mode" -eq 0 ]; then
  require_file "$plan" plan_missing >/dev/null || true
  require_file "$acceptance" acceptance_missing >/dev/null || true
fi

clarify_gate="$SCRIPT_DIR/clarify-gate.sh"
if [ -x "$clarify_gate" ]; then
  clarify_out="$("$clarify_gate" "$run_dir" 2>/dev/null)"
  clarify_rc=$?
  clarify_status="$(printf '%s\n' "$clarify_out" | cut -f2)"
  clarify_detail="$(printf '%s\n' "$clarify_out" | cut -f5 | sed 's/^detail=//')"
  case "$clarify_status" in
    OPEN) ;;
    CLOSED) add_blocker "clarify_gate_closed:${clarify_detail:-unknown}" ;;
    *)
      if [ "$clarify_rc" -ne 0 ]; then
        add_blocker "clarify_gate_error"
      else
        add_blocker "clarify_gate_malformed"
      fi
      ;;
  esac
else
  add_blocker "clarify_gate_missing"
fi

# Discovery is a separate semantic boundary from Current State: Current State proves
# freshness, while this gate proves that Phase 2 declared enough evidence to plan.
# Legacy/pre-discovery runs have no requirement marker and remain resumable.
discovery_gate="$SCRIPT_DIR/discovery-gate.sh"
discovery_required="$(kimiflow_state_value "$state" "Discovery required" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
if [ -x "$discovery_gate" ]; then
  discovery_out="$("$discovery_gate" "$run_dir" 2>/dev/null)"
  discovery_rc=$?
  discovery_status="$(printf '%s\n' "$discovery_out" | cut -f2)"
  discovery_detail="$(printf '%s\n' "$discovery_out" | cut -f5 | sed 's/^detail=//')"
  case "$discovery_status" in
    OPEN) ;;
    CLOSED) add_blocker "discovery_gate_closed:${discovery_detail:-unknown}" ;;
    *)
      if [ "$discovery_rc" -ne 0 ]; then
        add_blocker "discovery_gate_error"
      else
        add_blocker "discovery_gate_malformed"
      fi
      ;;
  esac
elif printf '%s\n' "$discovery_required" | grep -Eq '^(yes|true|1|required)$'; then
  add_blocker "discovery_gate_missing"
fi

# Contracted feature/fix runs must bind every plan-shaping decision to evidence,
# affected paths, acceptance, and one later falsifier. Legacy runs remain resumable.
conformance_contract="$(kimiflow_state_value "$state" "Conformance contract" | awk '{print $1}')"
if [ -n "$conformance_contract" ]; then
  conformance_gate="$SCRIPT_DIR/conformance-gate.sh"
  if [ ! -x "$conformance_gate" ]; then
    add_blocker "conformance_plan_gate_missing"
  else
    conformance_out="$("$conformance_gate" "$run_dir" --plan 2>/dev/null)"
    conformance_rc=$?
    conformance_lines="$(printf '%s\n' "$conformance_out" | awk 'END { print NR }')"
    conformance_fields="$(printf '%s\n' "$conformance_out" | awk -F '\t' 'NR == 1 { print NF }')"
    conformance_tag="$(printf '%s\n' "$conformance_out" | cut -f1)"
    conformance_status="$(printf '%s\n' "$conformance_out" | cut -f2)"
    conformance_blockers="$(printf '%s\n' "$conformance_out" | cut -f3)"
    conformance_reason="$(printf '%s\n' "$conformance_out" | cut -f4)"
    conformance_detail="$(printf '%s\n' "$conformance_out" | cut -f5 | sed 's/^detail=//')"
    if [ "$conformance_rc" -ne 0 ]; then
      add_blocker "conformance_plan_gate_error"
    elif [ "$conformance_lines" -ne 1 ] || [ "$conformance_fields" -ne 5 ] || [ "$conformance_tag" != "CONFORMANCE_GATE" ]; then
      add_blocker "conformance_plan_gate_malformed"
    elif [ "$conformance_status" = "OPEN" ] && [ "$conformance_blockers" = "blockers=0" ] && [ "$conformance_reason" = "reason=plan-clean" ]; then
      :
    elif [ "$conformance_status" = "CLOSED" ] \
      && printf '%s\n' "$conformance_blockers" | grep -Eq '^blockers=[1-9][0-9]*$' \
      && printf '%s\n' "$conformance_reason" | grep -Eq '^reason=[a-z0-9-]+$' \
      && printf '%s\n' "$conformance_out" | cut -f5 | grep -Eq '^detail='; then
      add_blocker "conformance_plan_gate_closed:${conformance_detail:-unknown}"
    else
      add_blocker "conformance_plan_gate_malformed"
    fi
  fi
fi

phase_detail="$(phase_read_blocker)"
[ -z "$phase_detail" ] || add_blocker "$phase_detail"

# New runs may opt into the bounded Architecture Deliberation contract. A missing
# contract is legacy-compatible; once the key exists, malformed or incomplete state
# fails closed before reviewers. This validates observable shape and budgets, not the
# semantic quality of the architecture decision.
architecture_contract="$(kimiflow_state_value "$state" "Architecture contract" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
if [ -n "$architecture_contract" ]; then
  if ! printf '%s\n' "$architecture_contract" | grep -Eq '^(1|yes|true)$'; then
    add_blocker "architecture_contract_version_invalid"
  else
    architecture_state="$(kimiflow_state_value "$state" "Architecture deliberation" | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
    case "$architecture_state" in
      active|off) ;;
      pending|'') add_blocker "architecture_deliberation_pending" ;;
      *) add_blocker "architecture_deliberation_state_invalid" ;;
    esac

    architecture_marker_count=0
    architecture_marker=""
    if [ -n "$understanding" ] && [ -f "$understanding" ]; then
      architecture_marker_count="$(grep -c '^<!-- kimiflow:architecture-deliberation ' "$understanding" 2>/dev/null || true)"
      architecture_marker="$(grep -E '^<!-- kimiflow:architecture-deliberation status=(active|off) approaches=[0-9]+ principles=[0-9]+ critique=[0-9]+ user_gate=(yes|no) -->$' "$understanding" 2>/dev/null || true)"
    fi
    if [ "$architecture_marker_count" -ne 1 ]; then
      add_blocker "architecture_marker_count_invalid"
    elif [ -z "$architecture_marker" ] || [ "$(printf '%s\n' "$architecture_marker" | grep -c .)" -ne 1 ]; then
      add_blocker "architecture_marker_malformed"
    else
      marker_status=""; marker_approaches=""; marker_principles=""; marker_critique=""; marker_user_gate=""
      marker_body="${architecture_marker#<!-- kimiflow:architecture-deliberation }"
      marker_body="${marker_body% -->}"
      OLDIFS="$IFS"; IFS=' '; set -- $marker_body; IFS="$OLDIFS"
      for field in "$@"; do
        case "$field" in
          status=*) marker_status="${field#status=}" ;;
          approaches=*) marker_approaches="${field#approaches=}" ;;
          principles=*) marker_principles="${field#principles=}" ;;
          critique=*) marker_critique="${field#critique=}" ;;
          user_gate=*) marker_user_gate="${field#user_gate=}" ;;
        esac
      done
      [ "$marker_status" = "$architecture_state" ] || add_blocker "architecture_marker_state_mismatch"
      [ "$marker_user_gate" = "no" ] || add_blocker "architecture_user_gate_forbidden"

      if [ "$marker_status" = "active" ]; then
        [ "$marker_approaches" = "2" ] || add_blocker "architecture_approach_count_invalid"
        [ "$marker_critique" = "1" ] || add_blocker "architecture_critique_count_invalid"
        case "$marker_principles" in
          0|1|2|3) ;;
          *) add_blocker "architecture_principle_count_invalid" ;;
        esac

        [ "$(grep -c '^## Adaptive Architecture Deliberation[[:space:]]*$' "$understanding" 2>/dev/null || true)" -eq 1 ] \
          || add_blocker "architecture_section_missing"

        for label in \
          'Problem behind request:' 'Operating envelope:' 'Architecture status:' \
          'Quality drivers:' 'Project principles:' 'Preferred approach:' \
          'Strongest alternative:' 'Trade-off / debt:' \
          'Reversibility / evolution trigger:' 'Falsification check:'; do
          [ "$(grep -cF "$label" "$understanding" 2>/dev/null || true)" -eq 1 ] \
            || add_blocker "architecture_field_missing:$(printf '%s' "$label" | tr '[:upper:] /' '[:lower:]__' | tr -cd '[:alnum:]_:_-')"
        done
        grep -Eq '^Architecture status: (fit|evolve|replace)$' "$understanding" 2>/dev/null \
          || add_blocker "architecture_status_invalid"

        note_words="$(awk '
          /^## Adaptive Architecture Deliberation[[:space:]]*$/ { in_note=1; next }
          in_note && /^## / { in_note=0 }
          in_note { count += NF }
          END { print count + 0 }
        ' "$understanding" 2>/dev/null)"
        [ "$note_words" -le 450 ] || add_blocker "architecture_note_over_budget:${note_words}"

        principle_lines="$(awk '
          /^Project principles:[[:space:]]*$/ { in_principles=1; next }
          in_principles && /^Preferred approach:/ { in_principles=0 }
          in_principles && /^- Type:/ { print }
        ' "$understanding" 2>/dev/null)"
        principle_count="$(printf '%s\n' "$principle_lines" | grep -c .)"
        [ "$principle_count" -eq "$marker_principles" ] \
          || add_blocker "architecture_principle_marker_mismatch"
        invalid_principles="$(printf '%s\n' "$principle_lines" \
          | grep -Ev '^- Type: (invariant|constraint|preference|heuristic|legacy); Scope: [^;]+; Rule: [^;]+; Evidence: .+$' \
          | grep -c . || true)"
        [ "$invalid_principles" -eq 0 ] || add_blocker "architecture_principle_shape_invalid"

        [ -f "$plan" ] && [ "$(grep -c '^Architecture fit: active$' "$plan" 2>/dev/null || true)" -eq 1 ] \
          || add_blocker "architecture_plan_fit_missing"
        [ -f "$plan" ] && [ "$(grep -c '^Architecture decision: .' "$plan" 2>/dev/null || true)" -eq 1 ] \
          || add_blocker "architecture_plan_decision_missing"
        [ -f "$plan" ] && [ "$(grep -cF 'Architecture evidence: RESEARCH.md §Adaptive Architecture Deliberation' "$plan" 2>/dev/null || true)" -eq 1 ] \
          || add_blocker "architecture_plan_evidence_missing"
        architecture_check="$(grep -E '^Architecture check: AC-[0-9]+ -> .+' "$plan" 2>/dev/null || true)"
        if [ "$(printf '%s\n' "$architecture_check" | grep -c .)" -ne 1 ]; then
          add_blocker "architecture_plan_check_missing"
        else
          architecture_ac="$(printf '%s\n' "$architecture_check" | grep -Eo 'AC-[0-9]+' | head -1)"
          file_has_ac_token "$acceptance" "$architecture_ac" \
            || add_blocker "architecture_check_ac_missing:${architecture_ac}"
        fi
      elif [ "$marker_status" = "off" ]; then
        [ "$marker_approaches" = "0" ] && [ "$marker_principles" = "0" ] && [ "$marker_critique" = "0" ] \
          || add_blocker "architecture_off_counts_invalid"
        [ "$(grep -c '^Architecture off reason: .' "$understanding" 2>/dev/null || true)" -eq 1 ] \
          || add_blocker "architecture_off_reason_missing"
        [ "$(grep -c '^## Adaptive Architecture Deliberation[[:space:]]*$' "$understanding" 2>/dev/null || true)" -eq 0 ] \
          || add_blocker "architecture_off_note_forbidden"
        [ -f "$plan" ] && grep -Eq '^Architecture fit: off( —|-|:) .+' "$plan" 2>/dev/null \
          || add_blocker "architecture_plan_off_missing"
      fi
    fi
  fi
fi

if [ -f "$plan" ]; then
  if grep -Eiq '\b(TBD|TODO|FIXME|NEEDS CLARIFICATION|OPEN QUESTION|NOT VERIFIED|UNKNOWN)\b' "$plan"; then
    add_blocker "plan_contains_unresolved_marker"
  fi
fi

if [ -f "$acceptance" ]; then
  if grep -Eiq '\b(TBD|TODO|FIXME|NEEDS CLARIFICATION|OPEN QUESTION|NOT VERIFIED|UNKNOWN)\b' "$acceptance"; then
    add_blocker "acceptance_contains_unresolved_marker"
  fi
  ac_ids="$(grep -Eo 'AC-[0-9]+' "$acceptance" | sort -u || true)"
  if [ -z "$ac_ids" ]; then
    add_blocker "acceptance_has_no_ac_ids"
  else
    missing_plan_ids=""
    missing_verify_ids=""
    while IFS= read -r ac; do
      [ -n "$ac" ] || continue
      if [ -f "$plan" ] && ! file_has_ac_token "$plan" "$ac"; then
        missing_plan_ids="${missing_plan_ids}${ac} "
      fi
      ac_lines="$(grep -En "$(ac_token_pattern "$ac")" "$acceptance" || true)"
      if ! printf '%s\n' "$ac_lines" | grep -Eiq '(→|->|test|verify|verification|command|manual|smoke|assert|check)'; then
        missing_verify_ids="${missing_verify_ids}${ac} "
      fi
    done <<EOF
$ac_ids
EOF
    [ -z "$missing_plan_ids" ] || add_blocker "acceptance_not_mapped_to_plan:${missing_plan_ids% }"
    [ -z "$missing_verify_ids" ] || add_blocker "acceptance_missing_verification:${missing_verify_ids% }"
  fi

  if grep -Eiq '\b(fast|robust|proper|nice|easy|seamless|user-friendly|performant)\b' "$acceptance" \
    && ! grep -Eiq '(ms|seconds?|tokens?|count|limit|threshold|exit code|assert|snapshot|golden|expected|actual|command|test|verify|manual|smoke)' "$acceptance"; then
    add_blocker "acceptance_uses_vague_quality_terms"
  fi
fi

if [ "$audit_mode" -eq 1 ]; then
  # Audit profile: AUDIT.md (understanding) must have no unresolved markers, carry slice
  # path:line evidence, and either it or STATE.md must declare the affected paths.
  if [ -f "$understanding" ]; then
    if grep -Eiq '\b(TBD|TODO|FIXME|NEEDS CLARIFICATION|OPEN QUESTION|NOT VERIFIED|UNKNOWN)\b' "$understanding"; then
      add_blocker "audit_contains_unresolved_marker"
    fi
    file_has_path_evidence "$understanding" || add_blocker "audit_slices_no_path_evidence"
  fi
  if ! file_declares_affected_paths "$state" && ! file_declares_affected_paths "$understanding"; then
    add_blocker "affected_files_not_declared"
  fi
else
  if [ -f "$plan" ] || [ -f "$acceptance" ]; then
    if ! file_has_path_evidence "$plan" && ! file_has_path_evidence "$acceptance"; then
      add_blocker "no_code_or_artifact_path_evidence"
    fi
  fi

  if [ -f "$state" ]; then
    if ! file_declares_affected_paths "$state" && ! file_declares_affected_paths "$plan"; then
      add_blocker "affected_files_not_declared"
    fi
  fi
fi

if [ "$blockers" -eq 0 ]; then
  emit OPEN 0 clean ""
fi

emit CLOSED "$blockers" plan-blockers "$details"
