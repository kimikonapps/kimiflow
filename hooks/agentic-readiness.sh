#!/usr/bin/env bash
# kimiflow - local agentic readiness helper. Orchestrator-invoked, not a hook.
#
# Usage:
#   agentic-readiness.sh status [--root <path>] [--run .kimiflow/<slug>] [--pretty]
#   agentic-readiness.sh gate --run .kimiflow/<slug> [--root <path>] [--min-level guided|agentic|governed|autonomous]
#   agentic-readiness.sh packet --run .kimiflow/<slug> --kind plan|review|background|handoff [--root <path>] [--write]
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKET_MAX_BYTES="${KIMIFLOW_AGENTIC_PACKET_MAX_BYTES:-12000}"

usage() {
  sed -n '1,8p' "$0" >&2
}

die() {
  printf 'agentic-readiness: %s\n' "$1" >&2
  exit "${2:-1}"
}

need_jq() {
  command -v jq >/dev/null 2>&1 || die "jq is required" 2
}

iso_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

resolve_root() {
  local root="$1"
  if [ -n "$root" ]; then
    (cd "$root" 2>/dev/null && pwd -P) || die "cannot resolve root: $root" 2
  else
    git rev-parse --show-toplevel 2>/dev/null || pwd -P
  fi
}

level_rank() {
  case "$1" in
    guided) printf '1' ;;
    agentic) printf '2' ;;
    governed) printf '3' ;;
    autonomous) printf '4' ;;
    *) printf '0' ;;
  esac
}

json_array_append() {
  local json="$1" value="$2"
  printf '%s\n' "$json" | jq --arg value "$value" '. + [$value]'
}

sanitize_stream() {
  local home="${HOME:-}" home_real=""
  if [ -n "$home" ]; then
    home_real="$(cd "$home" 2>/dev/null && pwd -P || printf '%s' "$home")"
    sed \
      -e "s#${home_real}#~#g" \
      -e "s#${home}#~#g" \
      -e 's/OBSIDIAN_API_KEY[=:][^[:space:]]*/OBSIDIAN_API_KEY=REDACTED/g' \
      -e 's/KIMIFLOW_OBSIDIAN_API_KEY[=:][^[:space:]]*/KIMIFLOW_OBSIDIAN_API_KEY=REDACTED/g' \
      -e 's/\([A-Za-z_][A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*\)[=:][^[:space:]]*/\1=REDACTED/g' \
      -e 's/Bearer[[:space:]][A-Za-z0-9._~+\/=-]\{8,\}/Bearer REDACTED/g' \
      -e 's/api[_-]\{0,1\}key[[:space:]]*[:=][[:space:]]*[^[:space:]]\+/api_key=REDACTED/Ig' \
      -e 's/^\([[:space:]]*[Rr][Aa][Ww][[:space:]][Pp][Rr][Oo][Mm][Pp][Tt]\)[[:space:]]*:.*/\1: REDACTED/g' \
      -e 's/^\([[:space:]]*[Pp][Rr][Oo][Mm][Pp][Tt]\)[[:space:]]*:.*/\1: REDACTED/g'
  else
    sed \
      -e 's/OBSIDIAN_API_KEY[=:][^[:space:]]*/OBSIDIAN_API_KEY=REDACTED/g' \
      -e 's/KIMIFLOW_OBSIDIAN_API_KEY[=:][^[:space:]]*/KIMIFLOW_OBSIDIAN_API_KEY=REDACTED/g' \
      -e 's/\([A-Za-z_][A-Za-z0-9_]*TOKEN[A-Za-z0-9_]*\)[=:][^[:space:]]*/\1=REDACTED/g' \
      -e 's/Bearer[[:space:]][A-Za-z0-9._~+\/=-]\{8,\}/Bearer REDACTED/g' \
      -e 's/api[_-]\{0,1\}key[[:space:]]*[:=][[:space:]]*[^[:space:]]\+/api_key=REDACTED/Ig' \
      -e 's/^\([[:space:]]*[Rr][Aa][Ww][[:space:]][Pp][Rr][Oo][Mm][Pp][Tt]\)[[:space:]]*:.*/\1: REDACTED/g' \
      -e 's/^\([[:space:]]*[Pp][Rr][Oo][Mm][Pp][Tt]\)[[:space:]]*:.*/\1: REDACTED/g'
  fi
}

sanitize_value() {
  printf '%s\n' "$1" | sanitize_stream | tr -d '\n'
}

repo_dirty_json() {
  local root="$1" json='[]' path dirty=false
  if git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      case "$path" in .kimiflow|.kimiflow/*) continue ;; esac
      dirty=true
      json="$(printf '%s\n' "$json" | jq --arg path "$path" '. + [$path]')"
    done < <(
      {
        git -C "$root" diff --name-only --cached 2>/dev/null
        git -C "$root" diff --name-only 2>/dev/null
        git -C "$root" ls-files --others --exclude-standard 2>/dev/null
      } | sort -u
    )
  fi
  jq -n --argjson dirty "$dirty" --argjson paths "$json" '{dirty: $dirty, changed_paths: $paths}'
}

safe_run_dir() {
  local root="$1" run="$2" path real_root real_run
  [ -n "$run" ] || die "run path is required" 2
  case "$run" in
    .kimiflow/*) path="$root/$run" ;;
    "$root"/.kimiflow/*) path="$run" ;;
    *) die "run path must be under .kimiflow/<slug>" 2 ;;
  esac
  case "$path" in *"/../"*|*"/.."|*"/./"*) die "run path must not contain relative traversal" 2 ;; esac
  [ -d "$path" ] || die "run directory missing: $run" 1
  [ ! -L "$path" ] || die "run directory must not be a symlink" 2
  real_root="$(cd "$root" && pwd -P)"
  real_run="$(cd "$path" && pwd -P)"
  case "$real_run" in "$real_root"/.kimiflow/*) printf '%s\n' "$real_run" ;; *) die "run escaped repository root" 2 ;; esac
}

rel_run_path() {
  local root="$1" run_dir="$2"
  printf '.kimiflow/%s\n' "${run_dir##*/}"
}

active_session_json() {
  local root="$1" json
  if [ -x "$SCRIPT_DIR/active-run.sh" ]; then
    json="$(KIMIFLOW_HOST="${KIMIFLOW_HOST:-}" "$SCRIPT_DIR/active-run.sh" status --root "$root" 2>/dev/null || true)"
    if printf '%s\n' "$json" | jq -e . >/dev/null 2>&1; then
      printf '%s\n' "$json"
      return 0
    fi
  fi
  jq -n '{present: false, status: "none", stale_risk: "none", item_counts: {open: 0}}'
}

background_json() {
  local root="$1" json
  if [ -x "$SCRIPT_DIR/background-run.sh" ]; then
    json="$(KIMIFLOW_HOST="${KIMIFLOW_HOST:-}" "$SCRIPT_DIR/background-run.sh" list --root "$root" --json 2>/dev/null || true)"
    if printf '%s\n' "$json" | jq -e . >/dev/null 2>&1; then
      printf '%s\n' "$json"
      return 0
    fi
  fi
  jq -n '{present: false, total: 0, collectable: 0, stale: 0, items: []}'
}

provider_json() {
  local root="$1" file="$root/.kimiflow/project/VAULT-PROVIDER.json"
  # A connected, authenticated Obsidian/Vault MCP in this session is local truth (env only,
  # no network) and supersedes the static per-repo manifest, which provider connect writes
  # without live capabilities. Mirrors the KIMIFLOW_*_MCP_AVAILABLE precedence in
  # memory-router.sh provider auth, so one session signal clears the false "not direct ready".
  case "${KIMIFLOW_VAULT_MCP_AVAILABLE:-${KIMIFLOW_OBSIDIAN_MCP_AVAILABLE:-}}" in
    1|true|TRUE|yes|YES)
      jq -n '{present: true, source: "session_signal", status: "authenticated", configured: true, vault_ready: true, mcp_ready: true, direct_search_ready: true, direct_write_ready: true}'
      return 0
      ;;
  esac
  if [ ! -f "$file" ]; then
    jq -n '{present: false, source: "local_artifact", status: "missing", vault_ready: false, mcp_ready: false, direct_search_ready: false, direct_write_ready: false}'
    return 0
  fi
  jq -c 'def mcp_auth: (((.health.mcp_tools_authenticated // false) == true) or ((.auth.source // "") == "mcp" and (.auth.authenticated // false) == true));
  {
    present: true,
    source: "local_artifact",
    status: (.auth.status // .health.status // (if .available == true then "configured" else "missing" end)),
    configured: (.available == true or .configured == true),
    vault_ready: (.available == true and ((.auth.authenticated // false) or (.capabilities.authenticated // false) or (.capabilities.rest_api_authenticated // false))),
    mcp_ready: (mcp_auth and (((.capabilities.mcp_direct_write // false) == true) or ((.capabilities.direct_search // false) == true) or ((.capabilities.direct_write // false) == true))),
    direct_search_ready: (mcp_auth and ((.capabilities.direct_search // false) == true)),
    direct_write_ready: (mcp_auth and (((.capabilities.direct_write // false) == true) or ((.capabilities.mcp_direct_write // false) == true)))
  }' "$file" 2>/dev/null || jq -n '{present: true, source: "local_artifact", status: "invalid", vault_ready: false, mcp_ready: false, direct_search_ready: false, direct_write_ready: false}'
}

hooks_json() {
  local root="$1" ready=true hooks='[]' h codex_present=false claude_present=false
  for h in active-run.sh background-run.sh working-tree-gate.sh current-state-gate.sh plan-blocker-gate.sh resolve-review-gate.sh vault-mcp-setup.sh; do
    if [ -x "$SCRIPT_DIR/$h" ]; then
      hooks="$(printf '%s\n' "$hooks" | jq --arg name "$h" '. + [{name: $name, executable: true}]')"
    else
      ready=false
      hooks="$(printf '%s\n' "$hooks" | jq --arg name "$h" '. + [{name: $name, executable: false}]')"
    fi
  done
  [ -f "$root/.codex-plugin/plugin.json" ] && jq -e . "$root/.codex-plugin/plugin.json" >/dev/null 2>&1 && codex_present=true
  [ -f "$root/.claude-plugin/plugin.json" ] && jq -e . "$root/.claude-plugin/plugin.json" >/dev/null 2>&1 && claude_present=true
  jq -n --argjson ready "$ready" --argjson hooks "$hooks" --argjson codex_present "$codex_present" --argjson claude_present "$claude_present" '{
    ready: $ready,
    helpers: $hooks,
    plugin_manifests: {
      codex: {path: ".codex-plugin/plugin.json", present: $codex_present},
      claude: {path: ".claude-plugin/plugin.json", present: $claude_present}
    }
  }'
}

current_state_json() {
  local run_dir="$1" assessment="$run_dir/CURRENT-STATE.json" recall="$run_dir/CURRENT-STATE.md" out verdict reason
  if [ ! -f "$assessment" ]; then
    jq -n '{present: false, verdict: "CLOSED", reason: "assessment_missing"}'
    return 0
  fi
  if [ -x "$SCRIPT_DIR/current-state-gate.sh" ]; then
    out="$("$SCRIPT_DIR/current-state-gate.sh" verify --assessment "$assessment" --recall "$recall" 2>/dev/null || true)"
    verdict="$(printf '%s\n' "$out" | awk -F '\t' '$1 == "CURRENT_STATE_GATE" {print $2; exit}')"
    reason="$(printf '%s\n' "$out" | awk -F '\t' '{for (i=1;i<=NF;i++) if ($i ~ /^reason=/) {sub(/^reason=/, "", $i); print $i; exit}}')"
    [ -n "$verdict" ] || verdict="CLOSED"
    [ -n "$reason" ] || reason="malformed"
    jq -n --arg verdict "$verdict" --arg reason "$reason" '{present: true, verdict: $verdict, reason: $reason}'
  else
    jq -n '{present: true, verdict: "CLOSED", reason: "helper_missing"}'
  fi
}

status_json() {
  local root="$1" run="${2:-}" run_dir="" rel_run="" work active bg provider hooks current='{}'
  local blockers='[]' warnings='[]' level confidence summary
  work="$(repo_dirty_json "$root")"
  active="$(active_session_json "$root")"
  bg="$(background_json "$root")"
  provider="$(provider_json "$root")"
  hooks="$(hooks_json "$root")"

  if [ -n "$run" ]; then
    run_dir="$(safe_run_dir "$root" "$run")"
    rel_run="$(rel_run_path "$root" "$run_dir")"
    current="$(current_state_json "$run_dir")"
  else
    current="$(jq -n '{present: false, verdict: "not_checked", reason: "run_not_provided"}')"
  fi

  if printf '%s\n' "$work" | jq -e '.dirty == true' >/dev/null 2>&1; then
    blockers="$(json_array_append "$blockers" "working_tree_dirty")"
  fi
  if ! printf '%s\n' "$active" | jq -e '.present == true' >/dev/null 2>&1; then
    warnings="$(json_array_append "$warnings" "active_session_missing")"
  fi
  if printf '%s\n' "$active" | jq -e '.stale_risk == "needs_revalidation"' >/dev/null 2>&1; then
    blockers="$(json_array_append "$blockers" "active_session_needs_revalidation")"
  fi
  if printf '%s\n' "$bg" | jq -e '(.stale // 0) > 0' >/dev/null 2>&1; then
    blockers="$(json_array_append "$blockers" "background_handles_stale")"
  fi
  if printf '%s\n' "$current" | jq -e '.present == true and .verdict != "OPEN"' >/dev/null 2>&1; then
    blockers="$(json_array_append "$blockers" "current_state_gate_closed")"
  fi
  if ! printf '%s\n' "$hooks" | jq -e '.ready == true' >/dev/null 2>&1; then
    blockers="$(json_array_append "$blockers" "required_helpers_missing")"
  fi
  if ! printf '%s\n' "$provider" | jq -e '.mcp_ready == true' >/dev/null 2>&1; then
    warnings="$(json_array_append "$warnings" "mcp_not_direct_ready")"
  fi

  if [ "$(printf '%s\n' "$blockers" | jq 'length')" -gt 0 ]; then
    level="guided"
  elif [ "$(printf '%s\n' "$warnings" | jq 'length')" -gt 0 ]; then
    level="governed"
  else
    level="autonomous"
  fi
  confidence="medium"
  if [ -z "$run" ]; then confidence="low"; fi
  summary="Agentic readiness: $level · blockers $(printf '%s\n' "$blockers" | jq 'length') · warnings $(printf '%s\n' "$warnings" | jq 'length')"

  jq -n \
    --arg root "$(sanitize_value "$root")" \
    --arg host "${KIMIFLOW_HOST:-unknown}" \
    --arg now "$(iso_now)" \
    --arg run "$rel_run" \
    --arg level "$level" \
    --arg confidence "$confidence" \
    --arg summary "$summary" \
    --argjson blockers "$blockers" \
    --argjson warnings "$warnings" \
    --argjson work "$work" \
    --argjson active "$active" \
    --argjson bg "$bg" \
    --argjson provider "$provider" \
    --argjson hooks "$hooks" \
    --argjson current "$current" \
    '{
      schema_version: 1,
      status: "readiness_status",
      generated_at: $now,
      root: $root,
      host: $host,
      run: (if $run == "" then null else $run end),
      summary: $summary,
      readiness: {level: $level, confidence: $confidence, blockers: $blockers, warnings: $warnings},
      working_tree: $work,
      active_session: $active,
      background: $bg,
      provider: $provider,
      hooks: $hooks,
      current_state: $current,
      privacy: {stores_secrets: false, stores_prompts: false, local_only: true, network_calls: false}
    }'
}

append_audit() {
  local run_dir="$1" action="$2" status_json="$3" extra="$4" file="$run_dir/AGENTIC-AUDIT.jsonl" tmp
  [ -d "$run_dir" ] || return 1
  [ ! -L "$file" ] || return 1
  tmp="$(mktemp "${file}.tmp.XXXXXX")" || return 1
  jq -nc \
    --arg at "$(iso_now)" \
    --arg action "$action" \
    --argjson status "$status_json" \
    --argjson extra "$extra" \
    '{at: $at, action: $action, level: $status.readiness.level, blockers: $status.readiness.blockers, warnings: $status.readiness.warnings, run: $status.run, extra: $extra}' > "$tmp" || {
      rm -f "$tmp"
      return 1
    }
  cat "$tmp" >> "$file" || {
    rm -f "$tmp"
    return 1
  }
  rm -f "$tmp"
}

gate_line() {
  local root="$1" run="$2" min_level="$3" status rank min_rank verdict reason detail run_dir
  run_dir="$(safe_run_dir "$root" "$run")"
  status="$(status_json "$root" "$run")"
  rank="$(level_rank "$(printf '%s\n' "$status" | jq -r '.readiness.level')")"
  min_rank="$(level_rank "$min_level")"
  verdict="OPEN"
  reason="clean"
  detail="$(printf '%s\n' "$status" | jq -r '.summary')"
  if [ "$(printf '%s\n' "$status" | jq '.readiness.blockers | length')" -gt 0 ]; then
    verdict="CLOSED"
    reason="$(printf '%s\n' "$status" | jq -r '.readiness.blockers[0]')"
  elif ! printf '%s\n' "$status" | jq -e '.active_session.present == true' >/dev/null 2>&1; then
    verdict="CLOSED"
    reason="active_session_missing"
  elif [ "$rank" -lt "$min_rank" ]; then
    verdict="CLOSED"
    reason="level_below_minimum"
  fi
  if ! append_audit "$run_dir" "gate" "$status" "$(jq -n --arg verdict "$verdict" --arg reason "$reason" --arg min "$min_level" '{verdict: $verdict, reason: $reason, min_level: $min}')"; then
    verdict="CLOSED"
    reason="audit_trail_unwritable"
  fi
  printf 'AGENTIC_READINESS_GATE\t%s\tlevel=%s\tmin=%s\treason=%s\tdetail=%s\n' "$verdict" "$(printf '%s\n' "$status" | jq -r '.readiness.level')" "$min_level" "$reason" "$detail"
}

append_file_excerpt() {
  local out="$1" title="$2" file="$3" max_lines="${4:-80}"
  [ -f "$file" ] || return 0
  [ ! -L "$file" ] || die "packet source must not be a symlink: ${file##*/}" 2
  {
    printf '\n## %s\n\n' "$title"
    sed -n "1,${max_lines}p" "$file" | sanitize_stream
  } >> "$out"
}

validate_packet_sources() {
  local run_dir="$1" file
  for file in STATE.md INTENT.md PROBLEM.md RESEARCH.md DIAGNOSIS.md BUG-REPRO.md PLAN.md ACCEPTANCE.md; do
    [ ! -L "$run_dir/$file" ] || die "packet source must not be a symlink: $file" 2
  done
}

append_changed_files_excerpt() {
  local out="$1" root="$2"
  {
    printf '\n## Changed Files\n\n'
    if git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      {
        git -C "$root" diff --name-status --cached 2>/dev/null
        git -C "$root" diff --name-status 2>/dev/null
        git -C "$root" ls-files --others --exclude-standard 2>/dev/null | sed 's/^/?\t/'
      } | awk '$2 !~ /^\.kimiflow(\/|$)/ && $0 != ""' | sort -u | sed -n '1,80p' | sanitize_stream
    else
      printf 'No git repository detected.\n'
    fi
  } >> "$out"
}

write_packet() {
  local root="$1" run="$2" kind="$3" run_dir packet_dir packet_dir_real run_real tmp target bytes rel_target status extra
  case "$kind" in plan|review|background|handoff) ;; *) die "--kind must be plan, review, background, or handoff" 2 ;; esac
  run_dir="$(safe_run_dir "$root" "$run")"
  validate_packet_sources "$run_dir"
  packet_dir="$run_dir/context-packets"
  [ ! -L "$packet_dir" ] || die "context-packets must not be a symlink" 2
  mkdir -p "$packet_dir" || die "cannot create context-packets directory" 1
  [ ! -L "$packet_dir" ] || die "context-packets must not be a symlink" 2
  packet_dir_real="$(cd "$packet_dir" && pwd -P)" || die "cannot resolve packet directory" 1
  run_real="$(cd "$run_dir" && pwd -P)" || die "cannot resolve run directory" 1
  [ "$packet_dir_real" = "$run_real/context-packets" ] || die "packet directory escaped run directory" 2
  tmp="$(mktemp "$packet_dir/.packet.tmp.XXXXXX")" || die "cannot create packet temp file" 1
  target="$packet_dir/${kind}-$(date -u +"%Y%m%dT%H%M%SZ").md"
  [ ! -e "$target" ] && [ ! -L "$target" ] || die "packet target already exists" 1
  status="$(status_json "$root" "$run")"
  {
    printf '# Agentic Context Packet\n\n'
    printf 'Kind: %s\n' "$kind"
    printf 'Generated: %s\n' "$(iso_now)"
    printf 'Run: %s\n' "$(rel_run_path "$root" "$run_dir")"
    printf 'Readiness: %s\n' "$(printf '%s\n' "$status" | jq -r '.summary')"
  } > "$tmp"
  append_file_excerpt "$tmp" "Acceptance" "$run_dir/ACCEPTANCE.md" 80
  append_changed_files_excerpt "$tmp" "$root"
  append_file_excerpt "$tmp" "State" "$run_dir/STATE.md" 80
  append_file_excerpt "$tmp" "Intent" "$run_dir/INTENT.md" 80
  append_file_excerpt "$tmp" "Problem" "$run_dir/PROBLEM.md" 80
  append_file_excerpt "$tmp" "Diagnosis" "$run_dir/DIAGNOSIS.md" 80
  append_file_excerpt "$tmp" "Bug Reproduction" "$run_dir/BUG-REPRO.md" 80
  append_file_excerpt "$tmp" "Plan" "$run_dir/PLAN.md" 100
  append_file_excerpt "$tmp" "Research" "$run_dir/RESEARCH.md" 80
  bytes="$(wc -c < "$tmp" | tr -d '[:space:]')"
  if [ "$bytes" -gt "$PACKET_MAX_BYTES" ]; then
    head -c "$PACKET_MAX_BYTES" "$tmp" > "$tmp.trim" && mv "$tmp.trim" "$tmp"
  fi
  mv "$tmp" "$target" || {
    rm -f "$tmp"
    die "cannot write packet" 1
  }
  rel_target=".kimiflow/${run_dir##*/}/context-packets/${target##*/}"
  extra="$(jq -n --arg path "$rel_target" --arg kind "$kind" '{packet_path: $path, kind: $kind}')"
  if ! append_audit "$run_dir" "packet" "$status" "$extra"; then
    rm -f "$target"
    die "cannot append agentic audit trail" 1
  fi
  jq -n --arg status "packet_written" --arg path "$rel_target" --arg kind "$kind" --argjson bytes "$(wc -c < "$target" | tr -d '[:space:]')" '{status: $status, kind: $kind, path: $path, bytes: $bytes}'
}

cmd="${1:-}"
[ -n "$cmd" ] || { usage; exit 2; }
shift

root_arg=""
run=""
kind="review"
pretty=0
write=0
min_level="governed"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) shift; root_arg="${1:-}" ;;
    --run) shift; run="${1:-}" ;;
    --kind) shift; kind="${1:-review}" ;;
    --write) write=1 ;;
    --pretty) pretty=1 ;;
    --min-level) shift; min_level="${1:-governed}" ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" 2 ;;
  esac
  shift
done

need_jq
root="$(resolve_root "$root_arg")"
case "$PACKET_MAX_BYTES" in
  [1-9][0-9]*) ;;
  *) die "KIMIFLOW_AGENTIC_PACKET_MAX_BYTES must be numeric" 2 ;;
esac

case "$cmd" in
  status)
    out="$(status_json "$root" "$run")"
    if [ "$pretty" -eq 1 ]; then printf '%s\n' "$out" | jq .; else printf '%s\n' "$out" | jq -c .; fi
    ;;
  gate)
    case "$min_level" in guided|agentic|governed|autonomous) ;; *) die "--min-level must be guided, agentic, governed, or autonomous" 2 ;; esac
    gate_line "$root" "$run" "$min_level"
    ;;
  packet)
    [ "$write" -eq 1 ] || die "packet requires --write" 2
    write_packet "$root" "$run" "$kind"
    ;;
  *)
    usage
    exit 2
    ;;
esac
