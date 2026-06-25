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
  local file="$1" heading_re="$2"
  [ -f "$file" ] || { printf '0'; return 0; }
  awk -v heading_re="$heading_re" '
    $0 ~ heading_re { in_section = 1; next }
    in_section && /^## / { in_section = 0 }
    in_section && /^### / { count++ }
    END { print count + 0 }
  ' "$file"
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
      provider: ".kimiflow/project/VAULT-PROVIDER.json"
    },
    memory: {present: false, path: ".kimiflow/project/MEMORY.md", tokens_estimate: 0, budget: 900, over_budget: false},
    learnings: {present: false, path: ".kimiflow/project/LEARNINGS.jsonl", total: 0, current: 0, stale: 0, superseded: 0, archived: 0, private: 0, security: 0, by_topic: {}},
    lifecycle: {stale_after_days: 90, cutoff_date: null, current: 0, stale_candidates: 0, stale_candidate_ids: [], unused_current: 0, used_current: 0},
    usage: {present: false, path: ".kimiflow/project/MEMORY-USAGE.json", tracked_items: 0, total_uses: 0, last_used_at: null, by_kind: {}},
    proposals: {present: false, path: ".kimiflow/project/PROPOSALS.jsonl", total: 0, pending: 0, approved: 0, applied: 0, rejected: 0, needs_revalidation: 0, by_type: {}},
    history: {present: false, path: ".kimiflow/project/RUN-HISTORY.json"},
    provider: {present: false, path: ".kimiflow/project/VAULT-PROVIDER.json", type: "none", available: false, mode: "local-first", vault_path: "", last_prefetch_at: null, last_write_at: null, capabilities: {status: true, prefetch: false, write: false, extract: false}},
    vault: {available: false, last_recall_at: null, last_write_at: null, provider: null},
    curation: {recommended: false, reasons: ["memory_router_unavailable"]}
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
FINDINGS_OPEN="$(count_section_items "$ROOT/$FINDINGS_PATH" '^##[[:space:]]+(Offen|Open)([[:space:]].*)?$')"
IMPROVEMENTS_OPEN="$(count_section_items "$ROOT/$IMPROVEMENTS_PATH" '^##[[:space:]]+(Priorisierte Slices|Prioritized Slices)([[:space:]].*)?$')"

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

MAINTENANCE_REASONS='[]'
if [ "$DIRTY" = true ]; then
  MAINTENANCE_REASONS="$(json_append_string "$MAINTENANCE_REASONS" "working_tree_dirty")"
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
  --arg head "$HEAD" \
  --arg index "$MAP_INDEX" \
  --arg depth "$MAP_DEPTH" \
  --arg baseline "$MAP_BASELINE" \
  --arg map_status "$MAP_STATUS" \
  --arg findings_path "$FINDINGS_PATH" \
  --arg improvements_path "$IMPROVEMENTS_PATH" \
  --argjson repo_present "$REPO_PRESENT" \
  --argjson dirty "$DIRTY" \
  --argjson map_present "$MAP_PRESENT" \
  --argjson map_valid "$MAP_VALID" \
  --argjson findings_open "$FINDINGS_OPEN" \
  --argjson improvements_open "$IMPROVEMENTS_OPEN" \
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
  '{
    schema_version: 1,
    repo: {present: $repo_present, root: $root, head: $head, dirty: $dirty},
    project_map: {present: $map_present, valid: $map_valid, depth: $depth, status: $map_status, index: $index, baseline_commit: $baseline},
    memory: $memory,
    findings: {open: $findings_open, path: $findings_path},
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
  }')"

if [ "$PRETTY" -eq 1 ]; then
  printf '%s\n' "$out" | jq .
else
  printf '%s\n' "$out" | jq -c .
fi
