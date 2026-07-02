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
      if (plain ~ /^[[:space:]]*(Affected files|Affected paths|Files|Paths|Touches)[[:space:]]*:/) {
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

phase_detail="$(phase_read_blocker)"
[ -z "$phase_detail" ] || add_blocker "$phase_detail"

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
