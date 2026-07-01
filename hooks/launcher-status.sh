#!/usr/bin/env bash
# kimiflow — read-only launcher status snapshot. Orchestrator-invoked, not a hook.
#
# Usage:
#   launcher-status.sh [--root <path>] [--pretty]
#
# Output: JSON. This script never writes project files.
set -u

usage() {
  sed -n '1,8p' "$0" >&2
}

die() {
  printf 'launcher-status: %s\n' "$1" >&2
  exit "${2:-1}"
}

need_jq() {
  command -v jq >/dev/null 2>&1 || die "jq is required" 2
}

state_value() {
  local file="$1" label="$2"
  awk -v label="$label" '
    {
      line = $0
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      if (line ~ "^" label ":[[:space:]]*") {
        sub("^" label ":[[:space:]]*", "", line)
        print line
        exit
      }
    }
  ' "$file" 2>/dev/null
}

count_section_items() {
  local file="$1" heading_re="$2" done_marker="${3:-}"
  [ -f "$file" ] || { printf '0'; return 0; }
  # When done_marker is a non-empty substring, a "### " block carrying that marker (the queue-done marker
  # written by improvements-status.sh) is NOT counted. Without a 3rd arg the count is unchanged
  # (length(done_marker) > 0 guard — an empty marker must never match every line and zero the count).
  awk -v heading_re="$heading_re" -v done_marker="$done_marker" '
    function flush() { if (have && !blockdone) count++; have = 0; blockdone = 0 }
    $0 ~ heading_re { in_section = 1; next }
    in_section && /^## / { flush(); in_section = 0; next }
    in_section && /^### / { flush(); have = 1; blockdone = 0; next }
    in_section && have && length(done_marker) > 0 && index($0, done_marker) > 0 { blockdone = 1 }
    END { if (in_section) flush(); print count + 0 }
  ' "$file"
}

count_feature_check_findings() {
  local root="$1"
  local count=0 n file
  [ -d "$root/.kimiflow" ] || { printf '0'; return 0; }
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    n="$(grep -E '^FINDING (BLOCKER|HIGH) ' "$file" 2>/dev/null | grep -c '' || true)"
    count=$((count + n))
  done < <(find "$root/.kimiflow" -mindepth 3 -maxdepth 3 -type f -path '*/findings/r*-feature-check.md' 2>/dev/null | sort)
  printf '%s' "$count"
}

count_feature_check_runs() {
  local root="$1"
  [ -d "$root/.kimiflow" ] || { printf '0'; return 0; }
  find "$root/.kimiflow" -mindepth 2 -maxdepth 2 -type f -name FEATURE-CHECK.md 2>/dev/null | wc -l | awk '{print $1 + 0}'
}

json_path_array_from_state() {
  local file="$1"
  local json='[]'
  local path
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    json="$(printf '%s\n' "$json" | jq --arg path "$path" '. + [$path]')"
  done < <(
    awk '
      {
        line = $0
        gsub(/\r/, "", line)
        gsub(/\*\*/, "", line)
        if (line ~ /^Affected files:[[:space:]]*$/) { in_list = 1; next }
        if (in_list && line ~ /^[[:space:]]*-[[:space:]]+/) {
          sub(/^[[:space:]]*-[[:space:]]+/, "", line)
          print line
          next
        }
        if (in_list && line !~ /^[[:space:]]*$/) { in_list = 0 }
      }
    ' "$file" 2>/dev/null
  )
  printf '%s' "$json"
}

state_phase7_done() {
  local file="$1"
  awk '
    {
      line = tolower($0)
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      if (line ~ /^[[:space:]-]*phase[[:space:]]+7([[:space:]]*\([^)]*\))?[[:space:]]*:[[:space:]]*done([[:space:]]|$|[-.,;:])/) found = 1
      if (line ~ /^[[:space:]]*(##[[:space:]]+)?run complete([[:space:]]|$|[-.,;:()])/) found = 1
    }
    END { exit(found ? 0 : 1) }
  ' "$file" 2>/dev/null
}

git_commit_ok() {
  local root="$1" commit="$2"
  [ -n "$commit" ] && [ "$commit" != "NOT VERIFIED" ] || return 1
  git -C "$root" cat-file -e "$commit^{commit}" >/dev/null 2>&1
}

changed_paths() {
  local root="$1" base="${2:-}"
  if [ -n "$base" ] && git_commit_ok "$root" "$base"; then
    git -C "$root" diff --name-only "$base" HEAD 2>/dev/null
  fi
  git -C "$root" diff --name-only --cached 2>/dev/null
  git -C "$root" diff --name-only 2>/dev/null
  git -C "$root" ls-files --others --exclude-standard 2>/dev/null
}

repo_dirty() {
  local root="$1"
  changed_paths "$root" | grep -vE '^\.kimiflow(/|$)' | grep -q .
}

path_in_changed_set() {
  local needle="$1" root="$2" base="$3"
  changed_paths "$root" "$base" | grep -vE '^\.kimiflow(/|$)' | grep -Fxq "$needle"
}

json_append_string() {
  local json="$1" value="$2"
  printf '%s\n' "$json" | jq --arg value "$value" '. + [$value]'
}

manifest_value() {
  local file="$1" expr="$2"
  [ -f "$file" ] || return 0
  jq -r "$expr // empty" "$file" 2>/dev/null | head -n 1
}

learning_review_status_json() {
  local root="$1" slug="$2"
  local run_rel=".kimiflow/$slug"
  local review_path="$run_rel/LEARNING-REVIEW.md"
  local review="$root/$review_path"
  local status verdict reason freshness line

  if [ ! -f "$review" ]; then
    jq -nc --arg path "$review_path" '{
      present: false,
      path: $path,
      status: "missing",
      verdict: "CLOSED",
      reason: "missing_review",
      freshness: null
    }'
    return 0
  fi

  status="$(awk -F': ' '/^Status:/ {print $2; exit}' "$review" 2>/dev/null)"
  [ -n "$status" ] || status="unknown"
  verdict="unknown"
  reason=""
  freshness=""

  if [ -x "$SCRIPT_DIR/memory-router.sh" ]; then
    line="$(KIMIFLOW_HOST="${KIMIFLOW_HOST:-}" "$SCRIPT_DIR/memory-router.sh" verify-run --root "$root" --run "$run_rel" 2>/dev/null || true)"
    verdict="$(printf '%s\n' "$line" | awk -F '\t' '$1 == "LEARNING_REVIEW" {print $2; exit}')"
    [ -n "$verdict" ] || verdict="unknown"
    reason="$(printf '%s\n' "$line" | sed -nE 's/.*reason=([^	]+).*/\1/p' | head -n 1)"
    freshness="$(printf '%s\n' "$line" | sed -nE 's/.*freshness=([^	]+).*/\1/p' | head -n 1)"
  fi

  jq -nc \
    --arg path "$review_path" \
    --arg status "$status" \
    --arg verdict "$verdict" \
    --arg reason "$reason" \
    --arg freshness "$freshness" \
    '{
      present: true,
      path: $path,
      status: $status,
      verdict: $verdict,
      reason: (if $reason == "" then null else $reason end),
      freshness: (if $freshness == "" then null else $freshness end)
    }'
}

default_memory_status() {
  jq -nc '{
    schema_version: 1,
    present: false,
    paths: {
      memory: ".kimiflow/project/MEMORY.md",
      learnings: ".kimiflow/project/LEARNINGS.jsonl",
      proposals: ".kimiflow/project/PROPOSALS.jsonl",
      index: ".kimiflow/project/MEMORY-INDEX.json",
      recall: ".kimiflow/project/RECALL.md",
      recall_index: ".kimiflow/project/RECALL.sqlite",
      run_history: ".kimiflow/project/RUN-HISTORY.json",
      usage: ".kimiflow/project/MEMORY-USAGE.json",
      economics: ".kimiflow/project/MEMORY-ECONOMICS.jsonl",
      provider: ".kimiflow/project/VAULT-PROVIDER.json",
      provider_sync: ".kimiflow/project/VAULT-SYNC.md"
    },
    memory: {present: false, path: ".kimiflow/project/MEMORY.md", tokens_estimate: 0, budget: 900, over_budget: false},
    learnings: {present: false, path: ".kimiflow/project/LEARNINGS.jsonl", total: 0, current: 0, stale: 0, superseded: 0, archived: 0, private: 0, security: 0, by_topic: {}},
    lifecycle: {stale_after_days: 90, cutoff_date: null, current: 0, stale_candidates: 0, stale_candidate_ids: [], unused_current: 0, used_current: 0},
    usefulness: {schema_version: 1, stale_after_days: 90, cutoff_date: null, hot: {count: 0, ids: []}, warm: {count: 0, ids: []}, cold: {count: 0, ids: []}, stale: {count: 0, ids: []}, promote_candidates: {count: 0, ids: []}, compress_candidates: {count: 0, ids: []}},
    usage: {present: false, path: ".kimiflow/project/MEMORY-USAGE.json", tracked_items: 0, total_uses: 0, last_used_at: null, by_kind: {}},
    economics: {present: false, path: ".kimiflow/project/MEMORY-ECONOMICS.jsonl", runs_tracked: 0, confidence: "none", verdict: "no_data", estimated_savings_percent: null, action_required: false},
    global_efficiency: {enabled: true, present: false, path: "~/.kimiflow/metrics/token-economics.jsonl", scope: "global_local_anonymous", runs_tracked: 0, projects_tracked: 0, confidence: "none", verdict: "no_data", estimated_savings_percent: null, action_required: false, privacy: {local_only: true, stores_content: false, stores_paths: false, stores_repo_name: false, stores_prompts: false, project_id_salted_hash: true}},
    proposals: {present: false, path: ".kimiflow/project/PROPOSALS.jsonl", total: 0, pending: 0, approved: 0, applied: 0, rejected: 0, needs_revalidation: 0, by_type: {}},
    history: {present: false, path: ".kimiflow/project/RUN-HISTORY.json"},
    provider: {present: false, configured: false, path: ".kimiflow/project/VAULT-PROVIDER.json", type: "none", available: false, mode: "local-first", vault_path: "", last_prefetch_at: null, last_write_at: null, capabilities: {status: true, prefetch: false, sync: false, write: false, extract: false, search: false, write_review: false, direct_search: false, direct_write: false, mcp_direct_write: false, rest_api_authenticated: false, authenticated: false}, detection: {status: "unavailable", available: false, type: "obsidian", url: "", checked_urls: [], reason: "memory_router_unavailable", direct_write_requires_token: true, manifest: null}, auth: {required: true, status: "not_configured", authenticated: false, source: "none", token_env_present: false, token_source: null, token_stored: false, validated: false, probe_http_status: null, probe_allowed: false, probe_blocked_reason: null, url: "", setup_hint: "Memory router unavailable."}, health: {status: "not_detected", local_handoff_ready: false, direct_search_ready: false, direct_write_ready: false, rest_api_authenticated: false, mcp_tools_authenticated: false, review_required: true, recommended_action: "open_obsidian"}, sync: {path: ".kimiflow/project/VAULT-SYNC.md", available: false, pending_count: 0, pending_ids: [], exportable_count: 0, health_status: "not_detected", auth_status: "not_configured", direct_write_ready: false, status: "provider_unavailable"}},
    vault: {available: false, last_recall_at: null, last_write_at: null, provider: null},
    curation: {recommended: false, internal_recommended: true, reasons: [], silent_reasons: [], all_reasons: ["memory_router_unavailable"]}
  }'
}

memory_summary_json() {
  local memory="$1"
  jq -nc --argjson memory "$memory" '{
    present: ($memory.present == true),
    tokens_estimate: ($memory.memory.tokens_estimate // 0),
    budget: ($memory.memory.budget // 900),
    over_budget: ($memory.memory.over_budget == true),
    learnings: {
      current: ($memory.learnings.current // 0),
      stale: ($memory.learnings.stale // 0),
      superseded: ($memory.learnings.superseded // 0)
    },
    usefulness: {
      hot: ($memory.usefulness.hot.count // 0),
      warm: ($memory.usefulness.warm.count // 0),
      cold: ($memory.usefulness.cold.count // 0),
      stale: ($memory.usefulness.stale.count // 0),
      promote_candidates: ($memory.usefulness.promote_candidates.count // 0),
      compress_candidates: ($memory.usefulness.compress_candidates.count // 0)
    },
    curation: {
      recommended: ($memory.curation.recommended == true),
      reasons: ($memory.curation.reasons // [])
    },
    provider_sync: {
      status: ($memory.provider.sync.status // "unknown"),
      pending_count: ($memory.provider.sync.pending_count // 0),
      direct_write_ready: ($memory.provider.sync.direct_write_ready == true)
    },
    next_actions: (
      (
        ($memory.curation.reasons // [])
        + [if (($memory.provider.sync.pending_count // 0) > 0) then "provider_sync_pending" else empty end]
      ) | unique
    )
  }'
}

default_active_session_json() {
  jq -nc '{
    schema_version: 1,
    present: false,
    status: "none",
    active_file: ".kimiflow/session/ACTIVE_RUN.json",
    run: null,
    item_counts: {total: 0, pending: 0, built: 0, accepted: 0, rejected: 0, dropped: 0, open: 0},
    stale_risk: "none",
    stale: {risk: "none", changed_paths: [], relevant_changed_paths: [], reason: "active_run_unavailable"},
    terminal: true
  }'
}

default_background_json() {
  jq -nc '{
    schema_version: 1,
    present: false,
    path: ".kimiflow/background",
    total: 0,
    pending: 0,
    running: 0,
    ready: 0,
    finished: 0,
    collectable: 0,
    stale: 0,
    failed: 0,
    cancelled: 0,
    items: []
  }'
}

default_agentic_readiness_json() {
  jq -nc '{
    schema_version: 1,
    status: "unavailable",
    summary: "Agentic readiness: unavailable",
    readiness: {level: "guided", confidence: "none", blockers: [], warnings: ["helper_missing"]},
    privacy: {stores_secrets: false, stores_prompts: false, local_only: true, network_calls: false}
  }'
}

ROOT=""
PRETTY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) shift; ROOT="${1:-}" ;;
    --pretty) PRETTY=1 ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" 2 ;;
  esac
  shift
done

need_jq
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -n "$ROOT" ] || ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ROOT="$(cd "$ROOT" 2>/dev/null && pwd || printf '%s' "$ROOT")"

SCRIPT_ROOT="${KIMIFLOW_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}"
if [ -n "$SCRIPT_ROOT" ] && [ -d "$SCRIPT_ROOT" ]; then
  SCRIPT_ROOT="$(cd "$SCRIPT_ROOT" && pwd)"
else
  SCRIPT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

PLUGIN_VERSION="$(manifest_value "$SCRIPT_ROOT/.codex-plugin/plugin.json" '.version')"
[ -n "$PLUGIN_VERSION" ] || PLUGIN_VERSION="$(manifest_value "$SCRIPT_ROOT/.claude-plugin/plugin.json" '.version')"
[ -n "$PLUGIN_VERSION" ] || PLUGIN_VERSION="unknown"
REPO_PLUGIN_VERSION="$(manifest_value "$ROOT/.codex-plugin/plugin.json" '.version')"
[ -n "$REPO_PLUGIN_VERSION" ] || REPO_PLUGIN_VERSION="$(manifest_value "$ROOT/.claude-plugin/plugin.json" '.version')"
[ -n "$REPO_PLUGIN_VERSION" ] || REPO_PLUGIN_VERSION="unknown"

INSTALLATION_MODE="source_checkout"
case "$SCRIPT_ROOT" in
  */.codex/plugins/cache/*|*/.claude/plugins/cache/*|*/.codex/.tmp/marketplaces/*|*/.claude/.tmp/marketplaces/*)
    INSTALLATION_MODE="plugin_cache"
    ;;
esac

CACHE_STATUS="$INSTALLATION_MODE"
INSTALLATION_ACTION_REQUIRED=false
if [ "$INSTALLATION_MODE" = "plugin_cache" ]; then
  CACHE_STATUS="current"
  if [ "$REPO_PLUGIN_VERSION" != "unknown" ] && [ "$PLUGIN_VERSION" != "unknown" ] && [ "$PLUGIN_VERSION" != "$REPO_PLUGIN_VERSION" ]; then
    CACHE_STATUS="stale_cache"
    INSTALLATION_ACTION_REQUIRED=true
  fi
fi

REPO_PRESENT=false
HEAD="NOT VERIFIED"
DIRTY=false
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  REPO_PRESENT=true
  HEAD="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf 'NOT VERIFIED')"
  if repo_dirty "$ROOT"; then DIRTY=true; fi
fi

INDEX="$ROOT/.kimiflow/project/INDEX.json"
MAP_PRESENT=false
MAP_VALID=false
MAP_DEPTH="missing"
MAP_STATUS="missing"
MAP_INDEX=".kimiflow/project/INDEX.json"
MAP_BASELINE="NOT VERIFIED"
COMMITS_SINCE_MAP='null'
if [ -f "$INDEX" ]; then
  MAP_PRESENT=true
  if jq -e . "$INDEX" >/dev/null 2>&1; then
    MAP_VALID=true
    MAP_DEPTH="$(jq -r '.scan_depth // "unknown"' "$INDEX" 2>/dev/null)"
    MAP_BASELINE="$(jq -r '.baseline_commit // "NOT VERIFIED"' "$INDEX" 2>/dev/null)"
    if git_commit_ok "$ROOT" "$MAP_BASELINE"; then
      COMMITS_SINCE_MAP="$(git -C "$ROOT" rev-list --count "$MAP_BASELINE"..HEAD 2>/dev/null || printf '0')"
    fi
    if [ -x "$SCRIPT_DIR/project-map-status.sh" ]; then
      map_line="$(cd "$ROOT" && "$SCRIPT_DIR/project-map-status.sh" status --index "$INDEX" 2>/dev/null | awk -F '\t' '$1 == "PROJECT_MAP" { print $2; exit }')"
      MAP_STATUS="${map_line:-unknown}"
    else
      MAP_STATUS="unknown"
    fi
  else
    MAP_DEPTH="unknown"
    MAP_STATUS="unknown"
  fi
fi

FINDINGS_PATH=".kimiflow/project/FINDINGS.md"
IMPROVEMENTS_PATH=".kimiflow/project/IMPROVEMENTS.md"
QUEUE_DONE_MARKER='kimiflow:queue-done'
FINDINGS_OPEN="$(count_section_items "$ROOT/$FINDINGS_PATH" '^##[[:space:]]+(Offen|Open)([[:space:]].*)?$' "$QUEUE_DONE_MARKER")"
IMPROVEMENTS_OPEN="$(count_section_items "$ROOT/$IMPROVEMENTS_PATH" '^##[[:space:]]+(Priorisierte Slices|Prioritized Slices)([[:space:]].*)?$' "$QUEUE_DONE_MARKER")"
FEATURE_CHECK_FINDINGS_OPEN="$(count_feature_check_findings "$ROOT")"
FEATURE_CHECK_RUNS="$(count_feature_check_runs "$ROOT")"

REPO_DOCS_PRESENT=false
if [ -d "$ROOT/docs" ] && find "$ROOT/docs" -maxdepth 2 -type f -name '*.md' -print -quit 2>/dev/null | grep -q .; then
  REPO_DOCS_PRESENT=true
fi

RUNS_JSON='[]'
ACTIVE=0
BACKLOG=0
DONE=0
OTHER=0
LEARNING_REVIEW_RECORDED=0
LEARNING_REVIEW_SKIPPED=0
LEARNING_REVIEW_MISSING=0
LEARNING_REVIEW_MISSING_DONE=0
LEARNING_REVIEW_CLOSED=0
LEARNING_REVIEW_NEEDS_ATTENTION=0
if [ -d "$ROOT/.kimiflow" ]; then
  while IFS= read -r state; do
    slug="$(basename "$(dirname "$state")")"
    case "$slug" in project|plans|specs) continue ;; esac
    raw_status="$(state_value "$state" "Status")"
    [ -n "$raw_status" ] || raw_status="active"
    case "$raw_status" in
      backlog*) status="backlog" ;;
      done*) status="done" ;;
      active*) status="active" ;;
      *) status="other" ;;
    esac
    status_detail="$raw_status"
    if [ "$status" = "active" ] && state_phase7_done "$state"; then
      status="done"
      status_detail="$raw_status (inferred: phase 7 done)"
    fi
    mode="$(state_value "$state" "Mode")"
    scope="$(state_value "$state" "Scope")"
    plan_commit="$(state_value "$state" "Plan commit")"
    [ -n "$plan_commit" ] || plan_commit="NOT VERIFIED"
    plan_status="$(state_value "$state" "Plan status")"
    [ -n "$plan_status" ] || plan_status="unknown"
    affected_json="$(json_path_array_from_state "$state")"
    affected_count="$(printf '%s\n' "$affected_json" | jq 'length')"
    stale_risk="n/a"
    learning_review_json="$(learning_review_status_json "$ROOT" "$slug")"
    learning_review_status="$(printf '%s\n' "$learning_review_json" | jq -r '.status // "missing"')"
    learning_review_verdict="$(printf '%s\n' "$learning_review_json" | jq -r '.verdict // "unknown"')"

    case "$status" in
      backlog) BACKLOG=$((BACKLOG + 1)) ;;
      done) DONE=$((DONE + 1)) ;;
      active) ACTIVE=$((ACTIVE + 1)) ;;
      *) OTHER=$((OTHER + 1)) ;;
    esac
    case "$learning_review_status" in
      recorded) LEARNING_REVIEW_RECORDED=$((LEARNING_REVIEW_RECORDED + 1)) ;;
      skipped) LEARNING_REVIEW_SKIPPED=$((LEARNING_REVIEW_SKIPPED + 1)) ;;
      missing) LEARNING_REVIEW_MISSING=$((LEARNING_REVIEW_MISSING + 1)) ;;
    esac
    if [ "$learning_review_verdict" = "CLOSED" ]; then
      LEARNING_REVIEW_CLOSED=$((LEARNING_REVIEW_CLOSED + 1))
    fi
    if [ "$status" = "done" ] && [ "$learning_review_status" = "missing" ]; then
      LEARNING_REVIEW_MISSING_DONE=$((LEARNING_REVIEW_MISSING_DONE + 1))
    fi
    if [ "$status" = "done" ] && [ "$learning_review_status" != "missing" ] && [ "$learning_review_verdict" != "OPEN" ]; then
      LEARNING_REVIEW_NEEDS_ATTENTION=$((LEARNING_REVIEW_NEEDS_ATTENTION + 1))
    fi

    if [ "$status" = "backlog" ]; then
      if ! git_commit_ok "$ROOT" "$plan_commit" || [ "$affected_count" -eq 0 ]; then
        stale_risk="unknown"
      else
        stale_risk="low"
        while IFS= read -r affected; do
          if path_in_changed_set "$affected" "$ROOT" "$plan_commit"; then
            stale_risk="needs-revalidation"
            break
          fi
        done < <(printf '%s\n' "$affected_json" | jq -r '.[]')
      fi
    fi

    RUNS_JSON="$(printf '%s\n' "$RUNS_JSON" | jq \
      --arg slug "$slug" \
      --arg status "$status" \
      --arg status_detail "$status_detail" \
      --arg mode "$mode" \
      --arg scope "$scope" \
      --arg plan_commit "$plan_commit" \
      --arg plan_status "$plan_status" \
      --arg stale_risk "$stale_risk" \
      --argjson affected_files "$affected_json" \
      --argjson learning_review "$learning_review_json" \
      '. + [{
        slug: $slug,
        status: $status,
        status_detail: $status_detail,
        mode: $mode,
        scope: $scope,
        plan_commit: $plan_commit,
        plan_status: $plan_status,
        affected_files: $affected_files,
        learning_review: $learning_review,
        stale_risk: $stale_risk
      }]')"
  done < <(find "$ROOT/.kimiflow" -mindepth 2 -maxdepth 2 -name STATE.md -type f 2>/dev/null | sort)
fi

WORKFLOW_ARTIFACTS='[]'
for artifact in .planning .gsd; do
  if [ -e "$ROOT/$artifact" ]; then
    WORKFLOW_ARTIFACTS="$(json_append_string "$WORKFLOW_ARTIFACTS" "$artifact")"
  fi
done

MEMORY_JSON="$(default_memory_status)"
if [ -x "$SCRIPT_DIR/memory-router.sh" ]; then
  maybe_memory_json="$(KIMIFLOW_HOST="${KIMIFLOW_HOST:-}" "$SCRIPT_DIR/memory-router.sh" status --root "$ROOT" 2>/dev/null || true)"
  if printf '%s\n' "$maybe_memory_json" | jq -e . >/dev/null 2>&1; then
    MEMORY_JSON="$maybe_memory_json"
  fi
fi
MEMORY_SUMMARY_JSON="$(memory_summary_json "$MEMORY_JSON")"

ACTIVE_SESSION_JSON="$(default_active_session_json)"
if [ -x "$SCRIPT_DIR/active-run.sh" ]; then
  maybe_active_session_json="$(KIMIFLOW_HOST="${KIMIFLOW_HOST:-}" "$SCRIPT_DIR/active-run.sh" status --root "$ROOT" 2>/dev/null || true)"
  if printf '%s\n' "$maybe_active_session_json" | jq -e . >/dev/null 2>&1; then
    ACTIVE_SESSION_JSON="$maybe_active_session_json"
  fi
fi

BACKGROUND_JSON="$(default_background_json)"
if [ -x "$SCRIPT_DIR/background-run.sh" ]; then
  maybe_background_json="$(KIMIFLOW_HOST="${KIMIFLOW_HOST:-}" "$SCRIPT_DIR/background-run.sh" list --root "$ROOT" --json 2>/dev/null || true)"
  if printf '%s\n' "$maybe_background_json" | jq -e . >/dev/null 2>&1; then
    BACKGROUND_JSON="$maybe_background_json"
  fi
fi

AGENTIC_READINESS_JSON="$(default_agentic_readiness_json)"
if [ -x "$SCRIPT_DIR/agentic-readiness.sh" ]; then
  active_run_for_readiness="$(printf '%s\n' "$ACTIVE_SESSION_JSON" | jq -r '.run // ""' 2>/dev/null || true)"
  if [ -n "$active_run_for_readiness" ] && [ "$active_run_for_readiness" != "null" ]; then
    maybe_agentic_json="$(KIMIFLOW_HOST="${KIMIFLOW_HOST:-}" "$SCRIPT_DIR/agentic-readiness.sh" status --root "$ROOT" --run "$active_run_for_readiness" 2>/dev/null || true)"
  else
    maybe_agentic_json="$(KIMIFLOW_HOST="${KIMIFLOW_HOST:-}" "$SCRIPT_DIR/agentic-readiness.sh" status --root "$ROOT" 2>/dev/null || true)"
  fi
  if printf '%s\n' "$maybe_agentic_json" | jq -e . >/dev/null 2>&1; then
    AGENTIC_READINESS_JSON="$maybe_agentic_json"
  fi
fi

MAINTENANCE_REASONS='[]'
if [ "$DIRTY" = true ]; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "working_tree_dirty")"
fi
if printf '%s\n' "$ACTIVE_SESSION_JSON" | jq -e '.present == true and .terminal == false' >/dev/null 2>&1; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "active_session_open")"
fi
if printf '%s\n' "$ACTIVE_SESSION_JSON" | jq -e '.stale_risk == "needs_revalidation"' >/dev/null 2>&1; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "active_session_needs_revalidation")"
fi
if printf '%s\n' "$BACKGROUND_JSON" | jq -e '(.collectable // 0) > 0' >/dev/null 2>&1; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "background_handles_collectable")"
fi
if printf '%s\n' "$BACKGROUND_JSON" | jq -e '(.stale // 0) > 0' >/dev/null 2>&1; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "background_handles_stale")"
fi
if [ "$MAP_PRESENT" != true ]; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "project_map_missing")"
elif [ "$MAP_VALID" != true ]; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "project_map_invalid")"
elif [ "$MAP_STATUS" != "current" ]; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "project_map_${MAP_STATUS}")"
fi
if [ "$ACTIVE" -gt 0 ]; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "active_runs")"
fi
if [ "$BACKLOG" -gt 0 ]; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "backlog_runs")"
fi
if [ "$LEARNING_REVIEW_NEEDS_ATTENTION" -gt 0 ]; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "learning_reviews_need_attention")"
fi
if printf '%s\n' "$MEMORY_JSON" | jq -e '.curation.recommended == true' >/dev/null 2>&1; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "memory_curation_recommended")"
fi
if printf '%s\n' "$MEMORY_JSON" | jq -e '(.proposals.pending // 0) > 0' >/dev/null 2>&1; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "learning_proposals_pending")"
fi
if printf '%s\n' "$MEMORY_JSON" | jq -e '(.proposals.approved // 0) > 0' >/dev/null 2>&1; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "learning_proposals_approved")"
fi
if printf '%s\n' "$MEMORY_JSON" | jq -e '(.proposals.needs_revalidation // 0) > 0' >/dev/null 2>&1; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "learning_proposals_need_revalidation")"
fi
out="$(jq -n \
  --arg root "$ROOT" \
  --arg plugin_root "$SCRIPT_ROOT" \
  --arg plugin_version "$PLUGIN_VERSION" \
  --arg repo_plugin_version "$REPO_PLUGIN_VERSION" \
  --arg installation_mode "$INSTALLATION_MODE" \
  --arg cache_status "$CACHE_STATUS" \
  --arg head "$HEAD" \
  --arg index "$MAP_INDEX" \
  --arg depth "$MAP_DEPTH" \
  --arg baseline "$MAP_BASELINE" \
  --arg map_status "$MAP_STATUS" \
  --arg findings_path "$FINDINGS_PATH" \
  --arg improvements_path "$IMPROVEMENTS_PATH" \
  --arg feature_check_path_pattern ".kimiflow/*/FEATURE-CHECK.md" \
  --argjson repo_present "$REPO_PRESENT" \
  --argjson installation_action_required "$INSTALLATION_ACTION_REQUIRED" \
  --argjson dirty "$DIRTY" \
  --argjson map_present "$MAP_PRESENT" \
  --argjson map_valid "$MAP_VALID" \
  --argjson findings_open "$FINDINGS_OPEN" \
  --argjson improvements_open "$IMPROVEMENTS_OPEN" \
  --argjson feature_check_findings_open "$FEATURE_CHECK_FINDINGS_OPEN" \
  --argjson feature_check_runs "$FEATURE_CHECK_RUNS" \
  --argjson docs_present "$REPO_DOCS_PRESENT" \
  --argjson active "$ACTIVE" \
  --argjson backlog "$BACKLOG" \
  --argjson done "$DONE" \
  --argjson other "$OTHER" \
  --argjson learning_review_recorded "$LEARNING_REVIEW_RECORDED" \
  --argjson learning_review_skipped "$LEARNING_REVIEW_SKIPPED" \
  --argjson learning_review_missing "$LEARNING_REVIEW_MISSING" \
  --argjson learning_review_missing_done "$LEARNING_REVIEW_MISSING_DONE" \
  --argjson learning_review_closed "$LEARNING_REVIEW_CLOSED" \
  --argjson learning_review_needs_attention "$LEARNING_REVIEW_NEEDS_ATTENTION" \
  --argjson items "$RUNS_JSON" \
  --argjson commits_since_map "$COMMITS_SINCE_MAP" \
  --argjson maintenance_reasons "$MAINTENANCE_REASONS" \
  --argjson workflow_artifacts "$WORKFLOW_ARTIFACTS" \
  --argjson memory "$MEMORY_JSON" \
  --argjson memory_summary "$MEMORY_SUMMARY_JSON" \
  --argjson active_session "$ACTIVE_SESSION_JSON" \
  --argjson background "$BACKGROUND_JSON" \
  --argjson agentic_readiness "$AGENTIC_READINESS_JSON" \
  '
  def memory_curation_visible(memory):
    (((memory | .memory.over_budget) // false) == true)
    or (((memory | .learnings.stale) // 0) > 0)
    or (((memory | .proposals.needs_revalidation) // 0) > 0);

  def memory_curation_hidden(memory):
    (memory_curation_visible(memory) | not);

  def visible_maintenance_reasons(memory):
    [
      .[] | select(
        . == "working_tree_dirty" or
        . == "active_session_open" or
        . == "active_session_needs_revalidation" or
        . == "background_handles_collectable" or
        . == "background_handles_stale" or
        . == "active_runs" or
        . == "backlog_runs" or
        . == "learning_reviews_need_attention" or
        . == "learning_proposals_approved" or
        . == "learning_proposals_need_revalidation" or
        ((. == "memory_curation_recommended") and memory_curation_visible(memory)) or
        startswith("project_map_")
      )
    ] | unique;

  def hidden_internal_reasons(memory):
    (
      [
        .[] | select(
          ((. == "memory_curation_recommended") and memory_curation_hidden(memory)) or
          . == "learning_proposals_pending"
        )
      ]
      + ((memory | .curation.silent_reasons) // [])
      + (if (((memory | .provider.sync.pending_count) // 0) > 0 and ((memory | .provider.sync.direct_write_ready) != true)) then ["provider_sync_waiting_for_user_setup"] else [] end)
    ) | unique;

  def drilldowns(s):
    [
      if (((s | .findings.open) // 0) > 0) then "findings" else empty end,
      if (((s | .feature_checks.verified_findings_open) // 0) > 0) then "feature_checks" else empty end,
      if (((s | .improvements.open) // 0) > 0) then "improvements" else empty end,
      if (((s | .runs.backlog) // 0) > 0) then "backlog_runs" else empty end,
      if (((s | .runs.active) // 0) > 0) then "active_runs" else empty end,
      if (((s | .background.collectable) // 0) > 0 or (((s | .background.stale) // 0) > 0)) then "background" else empty end,
      if (((s | .memory_summary.provider_sync.pending_count) // 0) > 0) then "vault_sync" else empty end,
      if (((s | .memory_summary.curation.recommended) // false) == true or (((s | .memory.proposals.pending) // 0) > 0)) then "memory" else empty end,
      if (((s | .installation.cache_status) // "") == "stale_cache") then "installation" else empty end
    ] | unique;

  def primary_action(s):
    if ((s | .repo.dirty) == true) then
      {id: "clean_worktree", label_key: "clean_worktree", priority: "blocker", blocking: true, reason_key: "working_tree_dirty"}
    elif ((s | .active_session.present) == true and (s | .active_session.terminal) == false and (s | .active_session.stale_risk) == "needs_revalidation") then
      {id: "revalidate_active_session", label_key: "revalidate_active_session", priority: "high", blocking: true, reason_key: "active_session_stale"}
    elif ((s | .active_session.present) == true and (s | .active_session.terminal) == false) then
      {id: "continue_active_session", label_key: "continue_active_session", priority: "high", blocking: false, reason_key: "active_session_open"}
    elif (((s | .installation.cache_status) // "") == "stale_cache") then
      {id: "update_installed_plugin", label_key: "update_installed_plugin", priority: "high", blocking: false, reason_key: "installed_cache_stale"}
    elif (((s | .background.collectable) // 0) > 0) then
      {id: "collect_background_handles", label_key: "collect_background_handles", priority: "medium", blocking: false, reason_key: "background_results_waiting"}
    elif (((s | .project_map.present) // false) != true) then
      {id: "project_map_bootstrap", label_key: "project_map_bootstrap", priority: "recommended", blocking: false, reason_key: "project_map_missing"}
    elif (((s | .project_map.valid) // false) != true) then
      {id: "project_map_rebuild", label_key: "project_map_rebuild", priority: "recommended", blocking: false, reason_key: "project_map_invalid"}
    elif (((s | .project_map.status) // "unknown") != "current") then
      {id: "bring_current", label_key: "bring_current", priority: "recommended", blocking: false, reason_key: ("project_map_" + ((s | .project_map.status) // "unknown"))}
    elif (((s | .memory_summary.over_budget) // false) == true) then
      {id: "curate_memory", label_key: "curate_memory", priority: "recommended", blocking: false, reason_key: "memory_over_budget"}
    elif (((s | .feature_checks.verified_findings_open) // 0) > 0) then
      {id: "review_feature_findings", label_key: "review_feature_findings", priority: "medium", blocking: false, reason_key: "feature_check_findings_open"}
    elif (((s | .findings.open) // 0) > 0) then
      {id: "review_findings", label_key: "review_findings", priority: "medium", blocking: false, reason_key: "findings_open"}
    elif (((s | .improvements.open) // 0) > 0) then
      {id: "review_improvements", label_key: "review_improvements", priority: "medium", blocking: false, reason_key: "improvements_open"}
    elif (((s | .runs.backlog) // 0) > 0) then
      {id: "resume_backlog", label_key: "resume_backlog", priority: "medium", blocking: false, reason_key: "backlog_runs_open"}
    elif (((s | .memory_summary.provider_sync.pending_count) // 0) > 0 and ((s | .memory_summary.provider_sync.direct_write_ready) == true)) then
      {id: "sync_vault", label_key: "sync_vault", priority: "optional", blocking: false, reason_key: "vault_sync_pending"}
    else
      {id: "start_kimiflow", label_key: "start_kimiflow", priority: "normal", blocking: false, reason_key: "ready"}
    end;

  ({
    schema_version: 1,
    repo: {present: $repo_present, root: $root, head: $head, dirty: $dirty},
    installation: {
      mode: $installation_mode,
      plugin_root: $plugin_root,
      version: $plugin_version,
      repo_version: $repo_plugin_version,
      cache_status: $cache_status,
      action_required: $installation_action_required
    },
    project_map: {present: $map_present, valid: $map_valid, depth: $depth, status: $map_status, index: $index, baseline_commit: $baseline},
    memory: $memory,
    memory_summary: $memory_summary,
    efficiency: ($memory.global_efficiency // {enabled: true, present: false, path: "~/.kimiflow/metrics/token-economics.jsonl", scope: "global_local_anonymous", runs_tracked: 0, projects_tracked: 0, confidence: "none", verdict: "no_data", estimated_savings_percent: null, action_required: false, privacy: {local_only: true, stores_content: false, stores_paths: false, stores_repo_name: false, stores_prompts: false, project_id_salted_hash: true}}),
    active_session: $active_session,
    background: $background,
    agentic_readiness: $agentic_readiness,
    findings: {open: $findings_open, path: $findings_path},
    feature_checks: {runs: $feature_check_runs, verified_findings_open: $feature_check_findings_open, path_pattern: $feature_check_path_pattern},
    improvements: {open: $improvements_open, path: $improvements_path},
    runs: {
      active: $active,
      backlog: $backlog,
      done: $done,
      other: $other,
      learning_reviews: {
        recorded: $learning_review_recorded,
        skipped: $learning_review_skipped,
        missing: $learning_review_missing,
        missing_done: $learning_review_missing_done,
        closed: $learning_review_closed,
        needs_attention: $learning_review_needs_attention
      },
      items: $items
    },
    maintenance: {
      bring_current_recommended: (($maintenance_reasons | length) > 0),
      reasons: $maintenance_reasons,
      commits_since_project_map_baseline: $commits_since_map,
      workflow_artifacts: $workflow_artifacts
    },
    repo_docs: {present: $docs_present}
  } as $snapshot
  | ($snapshot.maintenance.reasons | visible_maintenance_reasons($snapshot.memory)) as $visible_reasons
  | ($snapshot.maintenance.reasons | hidden_internal_reasons($snapshot.memory)) as $hidden_reasons
  | $snapshot
    + {
      maintenance: (
        $snapshot.maintenance
        + {
          visible_reasons: $visible_reasons,
          hidden_internal_reasons: $hidden_reasons
        }
      ),
      launcher: {
        schema_version: 1,
        presentation: "calm",
        primary_action: primary_action($snapshot),
        status: {
          installation: {
            version: $snapshot.installation.version,
            repo_version: $snapshot.installation.repo_version,
            mode: $snapshot.installation.mode,
            cache_status: $snapshot.installation.cache_status,
            action_required: $snapshot.installation.action_required
          },
          project_map: {
            present: $snapshot.project_map.present,
            depth: $snapshot.project_map.depth,
            status: $snapshot.project_map.status,
            attention: (($snapshot.project_map.present != true) or ($snapshot.project_map.valid != true) or ($snapshot.project_map.status != "current"))
          },
          memory: {
            present: $snapshot.memory_summary.present,
            tokens_estimate: $snapshot.memory_summary.tokens_estimate,
            budget: $snapshot.memory_summary.budget,
            over_budget: $snapshot.memory_summary.over_budget,
            current_learnings: $snapshot.memory_summary.learnings.current,
            curation_recommended: $snapshot.memory_summary.curation.recommended
          },
          efficiency: {
            present: ($snapshot.efficiency.present == true),
            estimated_savings_percent: $snapshot.efficiency.estimated_savings_percent,
            confidence: $snapshot.efficiency.confidence,
            runs_tracked: ($snapshot.efficiency.runs_tracked // 0),
            projects_tracked: ($snapshot.efficiency.projects_tracked // 0)
          },
          vault: {
            health: ($snapshot.memory.provider.health.status // "not_detected"),
            auth_status: ($snapshot.memory.provider.auth.status // "not_configured"),
            authenticated: ($snapshot.memory.provider.auth.authenticated == true),
            sync_pending_count: ($snapshot.memory_summary.provider_sync.pending_count // 0),
            direct_write_ready: ($snapshot.memory_summary.provider_sync.direct_write_ready == true)
          },
          counts: {
            findings_open: $snapshot.findings.open,
            feature_check_findings_open: $snapshot.feature_checks.verified_findings_open,
            improvements_open: $snapshot.improvements.open,
            active_runs: $snapshot.runs.active,
            backlog_runs: $snapshot.runs.backlog,
            background_collectable: ($snapshot.background.collectable // 0),
            background_stale: ($snapshot.background.stale // 0)
          }
        },
        maintenance: {
          visible_count: ($visible_reasons | length),
          visible_reasons: $visible_reasons,
          hidden_internal_count: ($hidden_reasons | length),
          hidden_internal_reasons: $hidden_reasons
        },
        drilldowns: drilldowns($snapshot)
      }
    })')"

if [ "$PRETTY" -eq 1 ]; then
  printf '%s\n' "$out" | jq .
else
  printf '%s\n' "$out" | jq -c .
fi
