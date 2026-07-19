#!/usr/bin/env bash
# kimiflow — Phase-1 intent guard plus post-diagnosis fix approval guard.
#
# Usage:
#   clarify-gate.sh <run-dir> [--post-diagnosis|--record-fix-approval] [--pretty]
#
# Output:
#   CLARIFY_GATE<TAB>OPEN|CLOSED<TAB>blockers=<n><TAB>reason=<code><TAB>detail=<codes>
#
# Contract-2 feature runs prove product-intent provenance, a single interaction
# round at most, zero technical questions, and a bounded intent-critic result.
# Earlier feature/audit runs keep the behavior/scope/outcome contract.
# Fixes proceed from a problem brief; legacy schema-3 runs keep their Preview.
# R2 invariant target: hooks/clarify-gate.sh
set -u
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=hooks/kimiflow-lib.sh
. "$SCRIPT_DIR/kimiflow-lib.sh"

emit() {
  printf 'CLARIFY_GATE\t%s\tblockers=%s\treason=%s\tdetail=%s\n' "$1" "$2" "$3" "${4:-}"
  exit 0
}

run_dir=""
post_diagnosis=0
record_fix_approval=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --post-diagnosis) post_diagnosis=1; shift ;;
    --record-fix-approval) post_diagnosis=1; record_fix_approval=1; shift ;;
    --pretty) shift ;;   # accepted, reserved no-op (no pretty-print path implemented)
    -*) shift ;;
    *) [ -z "$run_dir" ] && run_dir="$1"; shift ;;
  esac
done

[ -n "$run_dir" ] || emit CLOSED 1 malformed "missing_run_dir"
[ -d "$run_dir" ] || emit CLOSED 1 malformed "run_dir_missing"

state="$run_dir/STATE.md"

phase_read_blocker() {
  local root run_rel gate out status detail
  kimiflow_phase_reads_required "$run_dir" "$state" || return 0
  root="$(kimiflow_run_root "$run_dir" 2>/dev/null || true)"
  [ -n "$root" ] || { printf 'phase_read_root_unknown\n'; return 0; }
  run_rel="$(kimiflow_run_rel "$root" "$run_dir" 2>/dev/null || true)"
  [ -n "$run_rel" ] || { printf 'phase_read_run_unknown\n'; return 0; }
  gate="$SCRIPT_DIR/active-run.sh"
  [ -x "$gate" ] || { printf 'phase_read_gate_missing\n'; return 0; }
  out="$("$gate" phase-read-gate --root "$root" --run "$run_rel" --through-phase 1 2>/dev/null)"
  status="$(printf '%s\n' "$out" | cut -f2)"
  detail="$(printf '%s\n' "$out" | cut -f5 | sed 's/^detail=//')"
  case "$status" in
    OPEN) return 0 ;;
    CLOSED) printf 'phase_read_gate_closed:%s\n' "${detail:-unknown}"; return 0 ;;
    *) printf 'phase_read_gate_error\n'; return 0 ;;
  esac
}

emit_open() {
  local detail
  detail="$(phase_read_blocker)"
  if [ -n "$detail" ]; then
    emit CLOSED 1 phase-read-blockers "$detail"
  fi
  emit OPEN 0 clean ""
}

find_first() {
  local p
  for p in "$@"; do
    [ -f "$run_dir/$p" ] && { printf '%s\n' "$run_dir/$p"; return 0; }
  done
  return 1
}

marker_attr() {
  local marker_line="$1" key="$2"
  printf '%s\n' "$marker_line" | tr ' ' '\n' | awk -F= -v wanted="$key" '
    $1 == wanted {
      value=tolower($2)
      sub(/[^a-z0-9_-].*$/, "", value)
      print value
      exit
    }
  '
}

strong_product_source() {
  case "$1" in
    user_explicit|user_confirmed|project_evidence) return 0 ;;
    *) return 1 ;;
  esac
}

bounded_product_source() {
  case "$1" in
    user_explicit|user_confirmed|project_evidence|reversible_default|not_applicable) return 0 ;;
    *) return 1 ;;
  esac
}

require_project_evidence() {
  local dimension="$1" provenance="$2" evidence_pattern
  [ "$provenance" = "project_evidence" ] || return 0
  evidence_pattern="^Intent evidence:[[:space:]]*${dimension}[[:space:]]*::[[:space:]]*(https://[^[:space:]]+|[^[:space:]]+:[0-9]+)([[:space:]].*)?$"
  grep -Eq "$evidence_pattern" "$artifact" || add_blocker "intent_${dimension}_evidence_missing"
}

blockers=0
details=""
add_blocker() {
  blockers=$((blockers + 1))
  if [ -z "$details" ]; then details="$1"; else details="$details,$1"; fi
}

sha256_stream() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 | awk '{print $NF}'
  else
    return 1
  fi
}

legacy_fix_approval_basis() {
  {
    printf '%s\n' 'artifact=PROBLEM.md'
    cat "$run_dir/PROBLEM.md"
    printf '%s\n' 'artifact=DIAGNOSIS.md'
    awk '!/<!--[[:space:]]*kimiflow:fix-approval[^>]*-->/ { print }' "$run_dir/DIAGNOSIS.md"
    printf '%s\n' 'artifact=PLAN.md'
    cat "$run_dir/PLAN.md"
    printf '%s\n' 'artifact=ACCEPTANCE.md'
    cat "$run_dir/ACCEPTANCE.md"
    printf 'flow_schema=%s\nmode=%s\nscope=%s\naffected_files=%s\nbuild_risk=%s\n' \
      "$flow_schema" "$mode_value" "$scope" "$affected_files" "$build_risk"
  } | sha256_stream
}

fix_authority_basis() {
  {
    printf '%s\n' 'artifact=PROBLEM.md'
    cat "$run_dir/PROBLEM.md"
    printf '%s\n' 'artifact=ACCEPTANCE.md'
    cat "$run_dir/ACCEPTANCE.md"
    printf 'flow_schema=%s\nmode=%s\nscope=%s\nbuild_risk=%s\n' \
      "$flow_schema" "$mode_value" "$scope" "$build_risk"
  } | sha256_stream
}

write_fix_approval() {
  local authority_basis="$1" legacy_basis="$2" diagnosis="$run_dir/DIAGNOSIS.md" tmp state_tmp mode state_mode
  tmp="$(mktemp "$run_dir/.DIAGNOSIS.md.XXXXXX")" || return 1
  state_tmp="$(mktemp "$run_dir/.STATE.md.XXXXXX")" || { rm -f "$tmp"; return 1; }
  awk '!/<!--[[:space:]]*kimiflow:fix-approval[^>]*-->/ { print }' "$diagnosis" > "$tmp" \
    || { rm -f "$tmp" "$state_tmp"; return 1; }
  printf '<!-- kimiflow:fix-approval cause=confirmed fix=confirmed scope=confirmed risk=confirmed source=current-run basis=%s -->\n' "$legacy_basis" >> "$tmp"
  awk '{ line=tolower($0); gsub(/\*\*/, "", line); sub(/^[[:space:]]*-[[:space:]]*/, "", line); if (line !~ /^[[:space:]]*fix approval([[:space:]]+basis)?:/) print }' "$state" > "$state_tmp" \
    || { rm -f "$tmp" "$state_tmp"; return 1; }
  printf 'Fix approval: confirmed\nFix approval basis: %s\n' "$authority_basis" >> "$state_tmp"
  mode="$(stat -c '%a' "$diagnosis" 2>/dev/null || stat -f '%Lp' "$diagnosis" 2>/dev/null || true)"
  state_mode="$(stat -c '%a' "$state" 2>/dev/null || stat -f '%Lp' "$state" 2>/dev/null || true)"
  [ -z "$mode" ] || chmod "$mode" "$tmp" || { rm -f "$tmp" "$state_tmp"; return 1; }
  [ -z "$state_mode" ] || chmod "$state_mode" "$state_tmp" || { rm -f "$tmp" "$state_tmp"; return 1; }
  mv "$tmp" "$diagnosis" || { rm -f "$tmp" "$state_tmp"; return 1; }
  mv "$state_tmp" "$state" || { rm -f "$state_tmp"; return 1; }
}

scope="$(kimiflow_state_value "$state" scope | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
alias_value="$(kimiflow_state_value "$state" alias | tr '[:upper:]' '[:lower:]')"
mode_value="$(kimiflow_state_value "$state" mode | tr '[:upper:]' '[:lower:]')"
flow_schema="$(kimiflow_state_value "$state" "Flow schema" | awk '{print $1}')"
intent_contract="$(kimiflow_state_value "$state" "Intent contract" | awk '{print $1}')"
affected_files="$(kimiflow_state_value "$state" "Affected files")"
build_risk="$(kimiflow_state_value "$state" "Build risk" | tr '[:upper:]' '[:lower:]')"
state_approval="$(kimiflow_state_value "$state" "Fix approval" | tr '[:upper:]' '[:lower:]')"
state_approval_basis="$(kimiflow_state_value "$state" "Fix approval basis" | tr '[:upper:]' '[:lower:]')"
legacy_approval_present="$(grep -Eio '<!--[[:space:]]*kimiflow:fix-approval[^>]*-->' "$run_dir/DIAGNOSIS.md" 2>/dev/null | head -1 || true)"
if [ "$record_fix_approval" -eq 1 ] && [ "$mode_value" != "fix" ]; then
  emit CLOSED 1 malformed "fix_approval_mode_invalid"
fi
if [ "$post_diagnosis" -eq 1 ] && [ -n "$state_approval$state_approval_basis$legacy_approval_present" ] && [ "$mode_value" != "fix" ]; then
  emit CLOSED 1 fix-approval-blockers "fix_approval_mode_changed"
fi
case "$mode_value" in
  feature) artifact="$(find_first INTENT.md 2>/dev/null || true)" ;;
  fix) artifact="$(find_first PROBLEM.md 2>/dev/null || true)" ;;
  audit) artifact="$(find_first AUDIT-INTENT.md 2>/dev/null || true)" ;;
  *) artifact="$(find_first INTENT.md PROBLEM.md AUDIT-INTENT.md 2>/dev/null || true)" ;;
esac

if [ "$scope" = "trivial" ] && { [ "$post_diagnosis" -eq 0 ] || [ "$mode_value" != "fix" ]; }; then
  emit_open
fi

if [ -z "$artifact" ] || [ ! -s "$artifact" ]; then
  emit CLOSED 1 clarify-missing "clarify_artifact_missing"
fi

# A clear bug report can proceed directly to diagnosis. Schema-3 fix runs move
# the one pre-build confirmation to a durable Fix Preview after root-cause proof.
if [ "$mode_value" = "fix" ]; then
  case "$flow_schema" in *[!0-9]*) emit CLOSED 1 malformed "flow_schema_invalid" ;; esac
  if [ "$record_fix_approval" -eq 1 ] && [ "$flow_schema" != "3" ]; then
    emit CLOSED 1 malformed "fix_approval_schema_unsupported"
  fi
  if [ -n "$flow_schema" ] && [ "$flow_schema" -ge 4 ]; then
    emit_open
  fi
  if [ "$flow_schema" = "3" ]; then
    if [ "$post_diagnosis" -eq 0 ]; then
      emit_open
    fi
    diagnosis="$run_dir/DIAGNOSIS.md"
    [ -s "$diagnosis" ] || emit CLOSED 1 fix-approval-missing "fix_diagnosis_missing"
    [ -s "$run_dir/PLAN.md" ] || emit CLOSED 1 fix-approval-missing "fix_plan_missing"
    [ -s "$run_dir/ACCEPTANCE.md" ] || emit CLOSED 1 fix-approval-missing "fix_acceptance_missing"
    authority_basis="$(fix_authority_basis)" || emit CLOSED 1 malformed "fix_approval_hash_unavailable"
    legacy_basis="$(legacy_fix_approval_basis)" || emit CLOSED 1 malformed "fix_approval_hash_unavailable"

    if [ "$record_fix_approval" -eq 1 ]; then
      write_fix_approval "$authority_basis" "$legacy_basis" || emit CLOSED 1 malformed "fix_approval_write_failed"
      state_approval="$(kimiflow_state_value "$state" "Fix approval" | tr '[:upper:]' '[:lower:]')"
      state_approval_basis="$(kimiflow_state_value "$state" "Fix approval basis" | tr '[:upper:]' '[:lower:]')"
    fi

    if [ -n "$state_approval$state_approval_basis" ]; then
      [ "$state_approval" = "confirmed" ] || add_blocker "fix_approval_unconfirmed"
      [ -n "$state_approval_basis" ] || add_blocker "fix_approval_basis_missing"
      [ -z "$state_approval_basis" ] || [ "$state_approval_basis" = "$authority_basis" ] || add_blocker "fix_approval_basis_stale"
    else
      marker="$(grep -Eio '<!--[[:space:]]*kimiflow:fix-approval[^>]*-->' "$diagnosis" | head -1 || true)"
      marker="$(printf '%s\n' "$marker" | sed 's/<!--[[:space:]]*//; s/[[:space:]]*-->//')"
      [ -n "$marker" ] || emit CLOSED 1 fix-approval-missing "fix_approval_missing"

      approval_source="$(printf '%s\n' "$marker" | sed -n 's/.*source=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
      approval_cause="$(printf '%s\n' "$marker" | sed -n 's/.*cause=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
      approval_fix="$(printf '%s\n' "$marker" | sed -n 's/.*fix=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
      approval_scope="$(printf '%s\n' "$marker" | sed -n 's/.*scope=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
      approval_risk="$(printf '%s\n' "$marker" | sed -n 's/.*risk=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
      approval_basis="$(printf '%s\n' "$marker" | sed -n 's/.*basis=\([A-Fa-f0-9][A-Fa-f0-9]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"

      case "$approval_source" in current-run|current_run) ;; *) add_blocker "fix_approval_not_current_run" ;; esac
      [ "$approval_cause" = "confirmed" ] || add_blocker "fix_cause_unconfirmed"
      [ "$approval_fix" = "confirmed" ] || add_blocker "fix_approach_unconfirmed"
      [ "$approval_scope" = "confirmed" ] || add_blocker "fix_scope_unconfirmed"
      [ "$approval_risk" = "confirmed" ] || add_blocker "fix_risk_unconfirmed"
      [ -n "$approval_basis" ] || add_blocker "fix_approval_basis_missing"
      [ -z "$approval_basis" ] || [ "$approval_basis" = "$legacy_basis" ] || add_blocker "fix_approval_basis_stale"
    fi

    if [ "$blockers" -eq 0 ]; then
      emit_open
    fi
    emit CLOSED "$blockers" fix-approval-blockers "$details"
  fi
fi

needs_micro=0
if [ "$mode_value" = "feature" ] || [ "$mode_value" = "audit" ]; then
  if [ "$scope" != "trivial" ] && [ -n "$flow_schema" ] && [ "$flow_schema" -ge 4 ] 2>/dev/null; then
    needs_micro=1
  fi
fi
if [ "$needs_micro" -eq 0 ]; then
  case "$scope" in
    ""|small) needs_micro=1 ;;
  esac
fi
if printf '%s\n%s\n' "$alias_value" "$mode_value" | grep -Eiq '(^|[[:space:][:punct:]])quick($|[[:space:][:punct:]])'; then
  needs_micro=1
fi

requires_implementation_authority=0
if [ "$mode_value" = "feature" ]; then
  case "$alias_value" in
    plan|grill|review|audit) ;;
    *) requires_implementation_authority=1 ;;
  esac
fi

# Intent Contract 2 separates product facts from technical implementation.
# It is additive: absent/Contract-1 runs retain the established marker path.
if [ "$mode_value" = "feature" ] && [ "$scope" != "trivial" ]; then
  case "$intent_contract" in
    ""|1) ;;
    2)
      coverage_marker="$(grep -Eio '<!--[[:space:]]*kimiflow:intent-coverage[^>]*-->|kimiflow:intent-coverage[^[:cntrl:]]*' "$artifact" | head -1 || true)"
      coverage_marker="$(printf '%s\n' "$coverage_marker" | sed 's/<!--[[:space:]]*//; s/[[:space:]]*-->//')"
      if [ -z "$coverage_marker" ]; then
        add_blocker "intent_coverage_missing"
        emit CLOSED "$blockers" clarify-blockers "$details"
      fi

      coverage_contract="$(marker_attr "$coverage_marker" contract)"
      coverage_goal="$(marker_attr "$coverage_marker" goal)"
      coverage_actor="$(marker_attr "$coverage_marker" actor)"
      coverage_behavior="$(marker_attr "$coverage_marker" behavior)"
      coverage_boundaries="$(marker_attr "$coverage_marker" boundaries)"
      coverage_success="$(marker_attr "$coverage_marker" success)"
      coverage_constraints="$(marker_attr "$coverage_marker" constraints)"
      coverage_unknowns="$(marker_attr "$coverage_marker" unknown_material)"
      coverage_rounds="$(marker_attr "$coverage_marker" question_rounds)"
      coverage_technical="$(marker_attr "$coverage_marker" technical_questions)"
      coverage_critic="$(marker_attr "$coverage_marker" critic)"
      coverage_authority="$(marker_attr "$coverage_marker" authority)"
      coverage_summary="$(marker_attr "$coverage_marker" summary)"
      coverage_source="$(marker_attr "$coverage_marker" source)"

      [ "$coverage_contract" = "2" ] || add_blocker "intent_coverage_contract_invalid"
      case "$flow_schema" in
        ''|*[!0-9]*) add_blocker "intent_contract_schema_invalid" ;;
        *) [ "$flow_schema" -ge 4 ] || add_blocker "intent_contract_schema_invalid" ;;
      esac
      case "$scope" in small|large) ;; *) add_blocker "intent_scope_tier_invalid" ;; esac
      strong_product_source "$coverage_goal" || add_blocker "intent_goal_provenance_invalid"
      bounded_product_source "$coverage_actor" || add_blocker "intent_actor_provenance_invalid"
      strong_product_source "$coverage_behavior" || add_blocker "intent_behavior_provenance_invalid"
      bounded_product_source "$coverage_boundaries" || add_blocker "intent_boundaries_provenance_invalid"
      strong_product_source "$coverage_success" || add_blocker "intent_success_provenance_invalid"
      bounded_product_source "$coverage_constraints" || add_blocker "intent_constraints_provenance_invalid"
      require_project_evidence goal "$coverage_goal"
      require_project_evidence actor "$coverage_actor"
      require_project_evidence behavior "$coverage_behavior"
      require_project_evidence boundaries "$coverage_boundaries"
      require_project_evidence success "$coverage_success"
      require_project_evidence constraints "$coverage_constraints"

      case "$coverage_unknowns" in
        0) ;;
        "") add_blocker "intent_unknown_material_missing" ;;
        *) add_blocker "intent_material_unknowns_open" ;;
      esac
      case "$coverage_rounds" in
        0|1) ;;
        "") add_blocker "intent_question_rounds_missing" ;;
        *) add_blocker "intent_question_rounds_exceeded" ;;
      esac
      case "$coverage_technical" in
        0) ;;
        "") add_blocker "intent_technical_questions_missing" ;;
        *) add_blocker "intent_technical_questions_forbidden" ;;
      esac
      if [ "$coverage_rounds" = "1" ]; then
        if ! printf '%s\n' "$coverage_goal" "$coverage_actor" "$coverage_behavior" "$coverage_boundaries" "$coverage_success" "$coverage_constraints" | grep -qx 'user_confirmed'; then
          add_blocker "intent_question_round_unbound"
        fi
      fi

      if [ "$scope" = "large" ]; then
        [ "$coverage_critic" = "passed" ] || add_blocker "intent_critic_required"
      else
        case "$coverage_critic" in passed|folded) ;; *) add_blocker "intent_critic_invalid" ;; esac
      fi
      case "$coverage_source" in current-run|current_run) ;; *) add_blocker "intent_coverage_not_current_run" ;; esac
      if [ "$requires_implementation_authority" -eq 1 ]; then
        case "$coverage_authority" in explicit|confirmed) ;; *) add_blocker "implementation_authority_missing" ;; esac
      fi
      [ "$coverage_summary" = "present" ] || add_blocker "plain_summary_missing"

      if [ "$blockers" -eq 0 ]; then
        emit_open
      fi
      emit CLOSED "$blockers" clarify-blockers "$details"
      ;;
    *)
      add_blocker "intent_contract_invalid"
      emit CLOSED "$blockers" clarify-blockers "$details"
      ;;
  esac
fi

if [ "$needs_micro" -eq 0 ]; then
  emit_open
fi

marker="$(grep -Eio '<!--[[:space:]]*kimiflow:clarify-evidence[^>]*-->|kimiflow:clarify-evidence[^[:cntrl:]]*' "$artifact" | head -1 || true)"
marker="$(printf '%s\n' "$marker" | sed 's/<!--[[:space:]]*//; s/[[:space:]]*-->//')"

if [ -z "$marker" ]; then
  add_blocker "intent_evidence_missing"
else
  evidence_confirmed="$(printf '%s\n' "$marker" | sed -n 's/.*confirmed=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
  evidence_source="$(printf '%s\n' "$marker" | sed -n 's/.*source=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
  evidence_behavior="$(printf '%s\n' "$marker" | sed -n 's/.*behavior=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
  evidence_scope="$(printf '%s\n' "$marker" | sed -n 's/.*scope=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
  evidence_outcome="$(printf '%s\n' "$marker" | sed -n 's/.*outcome=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
  evidence_authority="$(printf '%s\n' "$marker" | sed -n 's/.*authority=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
  evidence_summary="$(printf '%s\n' "$marker" | sed -n 's/.*summary=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"

  case "$evidence_source" in
    current-run|current_run) ;;
    *) add_blocker "micro_grill_not_current_run" ;;
  esac

  if [ -n "$flow_schema" ] && [ "$flow_schema" -ge 4 ] 2>/dev/null && { [ "$mode_value" = "feature" ] || [ "$mode_value" = "audit" ]; }; then
    [ "$evidence_behavior" = "confirmed" ] || add_blocker "intent_behavior_unconfirmed"
    [ "$evidence_scope" = "confirmed" ] || add_blocker "intent_scope_unconfirmed"
    [ "$evidence_outcome" = "confirmed" ] || add_blocker "intent_outcome_unconfirmed"
    if [ "$requires_implementation_authority" -eq 1 ]; then
      case "$evidence_authority" in explicit|confirmed) ;; *) add_blocker "implementation_authority_missing" ;; esac
    fi
    [ "$evidence_summary" = "present" ] || add_blocker "plain_summary_missing"
  elif [ -n "$evidence_behavior$evidence_scope$evidence_outcome" ]; then
    [ "$evidence_behavior" = "confirmed" ] || add_blocker "intent_behavior_unconfirmed"
    [ "$evidence_scope" = "confirmed" ] || add_blocker "intent_scope_unconfirmed"
    [ "$evidence_outcome" = "confirmed" ] || add_blocker "intent_outcome_unconfirmed"
  else
    case "$evidence_confirmed" in
      yes|y|true|ok|confirmed) ;;
      *) add_blocker "intent_not_confirmed" ;;
    esac
  fi
fi

if [ "$blockers" -eq 0 ]; then
  emit_open
fi

emit CLOSED "$blockers" clarify-blockers "$details"
