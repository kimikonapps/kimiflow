#!/usr/bin/env bash
# kimiflow — clarify gate. Mechanical Phase-1 guard for small/quick runs.
#
# Usage:
#   clarify-gate.sh <run-dir> [--pretty]
#
# Output:
#   CLARIFY_GATE<TAB>OPEN|CLOSED<TAB>blockers=<n><TAB>reason=<code><TAB>detail=<codes>
#
# For small/quick runs, Phase 1 must leave durable evidence that the agent asked
# 2+ targeted questions OR confirmed a compact set of recommended assumptions
# in the current Kimiflow run. Loose prior conversation is context, not consent.
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

blockers=0
details=""
add_blocker() {
  blockers=$((blockers + 1))
  if [ -z "$details" ]; then details="$1"; else details="$details,$1"; fi
}

artifact="$(find_first INTENT.md PROBLEM.md AUDIT-INTENT.md 2>/dev/null || true)"
scope="$(kimiflow_state_value "$state" scope | tr '[:upper:]' '[:lower:]' | awk '{print $1}')"
alias_value="$(kimiflow_state_value "$state" alias | tr '[:upper:]' '[:lower:]')"
mode_value="$(kimiflow_state_value "$state" mode | tr '[:upper:]' '[:lower:]')"

if [ "$scope" = "trivial" ]; then
  emit_open
fi

if [ -z "$artifact" ] || [ ! -s "$artifact" ]; then
  emit CLOSED 1 clarify-missing "clarify_artifact_missing"
fi

needs_micro=0
case "$scope" in
  ""|small) needs_micro=1 ;;
esac
if printf '%s\n%s\n' "$alias_value" "$mode_value" | grep -Eiq '(^|[[:space:][:punct:]])quick($|[[:space:][:punct:]])'; then
  needs_micro=1
fi

if [ "$needs_micro" -eq 0 ]; then
  emit_open
fi

marker="$(grep -Eio '<!--[[:space:]]*kimiflow:clarify-evidence[^>]*-->|kimiflow:clarify-evidence[^[:cntrl:]]*' "$artifact" | head -1 || true)"
marker="$(printf '%s\n' "$marker" | sed 's/<!--[[:space:]]*//; s/[[:space:]]*-->//')"

if [ -z "$marker" ]; then
  add_blocker "micro_grill_evidence_missing"
else
  evidence_mode="$(printf '%s\n' "$marker" | sed -n 's/.*mode=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
  evidence_count="$(printf '%s\n' "$marker" | sed -n 's/.*count=\([0-9][0-9]*\).*/\1/p')"
  evidence_confirmed="$(printf '%s\n' "$marker" | sed -n 's/.*confirmed=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"
  evidence_source="$(printf '%s\n' "$marker" | sed -n 's/.*source=\([A-Za-z_-][A-Za-z0-9_-]*\).*/\1/p' | tr '[:upper:]' '[:lower:]')"

  [ -n "$evidence_mode" ] || evidence_mode="questions"
  [ -n "$evidence_count" ] || evidence_count=0

  case "$evidence_source" in
    current-run|current_run) ;;
    *) add_blocker "micro_grill_not_current_run" ;;
  esac

  case "$evidence_confirmed" in
    yes|y|true|ok|confirmed) ;;
    *) add_blocker "micro_grill_not_confirmed" ;;
  esac

  case "$evidence_mode" in
    questions)
      [ "$evidence_count" -ge 2 ] || add_blocker "micro_grill_too_short"
      ;;
    assumptions)
      [ "$evidence_count" -ge 3 ] || add_blocker "micro_grill_assumptions_incomplete"
      ;;
    *)
      add_blocker "micro_grill_mode_invalid"
      ;;
  esac
fi

if [ "$blockers" -eq 0 ]; then
  emit_open
fi

emit CLOSED "$blockers" clarify-blockers "$details"
