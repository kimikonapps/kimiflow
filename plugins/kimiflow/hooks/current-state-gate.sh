#!/usr/bin/env bash
# kimiflow — current-state gate resolver. Orchestrator-invoked, not a hook.
#
# Usage:
#   current-state-gate.sh assess --input <file> [--pretty]
#   current-state-gate.sh verify --assessment <json> --recall <file>
#
# Output:
#   assess -> JSON
#   verify -> CURRENT_STATE_GATE<TAB>OPEN|CLOSED<TAB>risk=<risk><TAB>reason=<code><TAB>detail=<detail>
# R2 invariant target: CSG verify --assessment
set -u

usage() {
  sed -n '1,10p' "$0" >&2
}

die() {
  printf 'current-state-gate: %s\n' "$1" >&2
  exit "${2:-1}"
}

need_jq() {
  command -v jq >/dev/null 2>&1 || die "jq is required" 2
}

json_append_string() {
  local json="$1" value="$2"
  printf '%s\n' "$json" | jq --arg value "$value" '. + [$value]'
}

emit_verdict() {
  printf 'CURRENT_STATE_GATE\t%s\trisk=%s\treason=%s\tdetail=%s\n' "$1" "$2" "$3" "${4:-}"
  exit 0
}

text_has() {
  local file="$1" pattern="$2"
  grep -Eiq "$pattern" "$file" 2>/dev/null
}

primary_source_count() {
  local file="$1"
  grep -Ei 'source_type:[[:space:]]*(official_docs|release_notes|schema_or_manifest|official_github)|"source_type"[[:space:]]*:[[:space:]]*"(official_docs|release_notes|schema_or_manifest|official_github)"' "$file" 2>/dev/null \
    | wc -l \
    | tr -d '[:space:]'
}

has_checked_status() {
  local file="$1"
  grep -Eiq '(^|[[:space:]-])Status:[[:space:]]*checked([[:space:]]|$)|"status"[[:space:]]*:[[:space:]]*"checked"' "$file" 2>/dev/null
}

has_source_url() {
  local file="$1"
  grep -Eiq 'source_url:[[:space:]]*https?://|"source_url"[[:space:]]*:[[:space:]]*"https?://' "$file" 2>/dev/null
}

mode="${1:-assess}"
case "$mode" in
  assess|verify) shift ;;
  --help|-h) usage; exit 0 ;;
  *) die "unknown mode: $mode" 2 ;;
esac

INPUT=""
ASSESSMENT=""
RECALL=""
PRETTY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --input) shift; INPUT="${1:-}" ;;
    --assessment) shift; ASSESSMENT="${1:-}" ;;
    --recall) shift; RECALL="${1:-}" ;;
    --pretty) PRETTY=1 ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" 2 ;;
  esac
  shift
done

need_jq

if [ "$mode" = "assess" ]; then
  [ -n "$INPUT" ] || die "assess requires --input <file>" 2
  [ -f "$INPUT" ] || die "input not found: $INPUT" 2

  risk="low"
  reasons='[]'
  required_sources='[]'
  freshness_horizon='null'
  status="not_required"

  if text_has "$INPUT" 'codex|claude[ -]?code|cursor|windsurf|plugin|marketplace|hook|hooks|skill|mcp|model context protocol'; then
    risk="high"
    reasons="$(json_append_string "$reasons" "host_or_plugin_surface")"
  fi
  if text_has "$INPUT" 'security|auth|oauth|payment|payments|stripe|privacy|deployment|deploy|ci/cd|app store|external service|hosted api|sdk'; then
    risk="high"
    reasons="$(json_append_string "$reasons" "security_or_external_platform")"
  fi

  if [ "$risk" = "low" ] && text_has "$INPUT" 'library|framework|dependency|dependencies|package|version|api|tooling|typescript|react|vite|node|python|swift|xcode|npm|pip'; then
    risk="medium"
    reasons="$(json_append_string "$reasons" "possibly_changing_tooling_or_api")"
  fi

  case "$risk" in
    high)
      required_sources='["official_docs","release_notes","schema_or_manifest"]'
      freshness_horizon='"30d"'
      status="required"
      ;;
    medium)
      required_sources='["official_docs","release_notes"]'
      freshness_horizon='"90d"'
      status="recommended"
      ;;
    *)
      required_sources='[]'
      freshness_horizon='null'
      ;;
  esac

  out="$(jq -n \
    --arg risk "$risk" \
    --arg status "$status" \
    --argjson reasons "$reasons" \
    --argjson required_sources "$required_sources" \
    --argjson freshness_horizon "$freshness_horizon" \
    '{
      schema_version: 1,
      current_state_risk: $risk,
      current_state_reasons: $reasons,
      freshness_horizon: $freshness_horizon,
      required_source_types: $required_sources,
      status: $status
    }')"

  if [ "$PRETTY" -eq 1 ]; then printf '%s\n' "$out" | jq .; else printf '%s\n' "$out" | jq -c .; fi
  exit 0
fi

[ -n "$ASSESSMENT" ] || die "verify requires --assessment <json>" 2
[ -f "$ASSESSMENT" ] || die "assessment not found: $ASSESSMENT" 2
if ! jq -e . "$ASSESSMENT" >/dev/null 2>&1; then
  emit_verdict CLOSED unknown malformed-assessment "$ASSESSMENT"
fi

risk="$(jq -r '.current_state_risk // "unknown"' "$ASSESSMENT" 2>/dev/null)"
case "$risk" in
  low)
    emit_verdict OPEN "$risk" not-required "current-state check not required"
    ;;
  medium|high)
    ;;
  *)
    emit_verdict CLOSED "$risk" malformed-assessment "missing current_state_risk"
    ;;
esac

[ -n "$RECALL" ] || emit_verdict CLOSED "$risk" missing-recall "missing --recall"
[ -f "$RECALL" ] || emit_verdict CLOSED "$risk" missing-recall "$RECALL"

if ! has_checked_status "$RECALL"; then
  emit_verdict CLOSED "$risk" not-checked "recall lacks Status: checked"
fi
count="$(primary_source_count "$RECALL")"
case "$count" in ''|*[!0-9]*) count=0 ;; esac
if [ "$count" -lt 1 ]; then
  emit_verdict CLOSED "$risk" missing-primary-source "recall lacks a primary source_type"
fi
if ! has_source_url "$RECALL"; then
  emit_verdict CLOSED "$risk" missing-source-url "recall lacks source_url"
fi

emit_verdict OPEN "$risk" checked "primary_sources=$count"
