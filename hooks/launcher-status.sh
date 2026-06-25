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
  local file="$1" heading="$2"
  [ -f "$file" ] || { printf '0'; return 0; }
  awk -v heading="$heading" '
    $0 == heading { in_section = 1; next }
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
if [ -f "$INDEX" ]; then
  MAP_PRESENT=true
  if jq -e . "$INDEX" >/dev/null 2>&1; then
    MAP_VALID=true
    MAP_DEPTH="$(jq -r '.scan_depth // "unknown"' "$INDEX" 2>/dev/null)"
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
FINDINGS_OPEN="$(count_section_items "$ROOT/$FINDINGS_PATH" "## Offen")"
IMPROVEMENTS_OPEN="$(count_section_items "$ROOT/$IMPROVEMENTS_PATH" "## Priorisierte Slices")"

REPO_DOCS_PRESENT=false
if [ -d "$ROOT/docs" ] && find "$ROOT/docs" -maxdepth 2 -type f -name '*.md' -print -quit 2>/dev/null | grep -q .; then
  REPO_DOCS_PRESENT=true
fi

RUNS_JSON='[]'
ACTIVE=0
BACKLOG=0
DONE=0
OTHER=0
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
    mode="$(state_value "$state" "Mode")"
    scope="$(state_value "$state" "Scope")"
    plan_commit="$(state_value "$state" "Plan commit")"
    [ -n "$plan_commit" ] || plan_commit="NOT VERIFIED"
    plan_status="$(state_value "$state" "Plan status")"
    [ -n "$plan_status" ] || plan_status="unknown"
    affected_json="$(json_path_array_from_state "$state")"
    affected_count="$(printf '%s\n' "$affected_json" | jq 'length')"
    stale_risk="n/a"

    case "$status" in
      backlog) BACKLOG=$((BACKLOG + 1)) ;;
      done) DONE=$((DONE + 1)) ;;
      active) ACTIVE=$((ACTIVE + 1)) ;;
      *) OTHER=$((OTHER + 1)) ;;
    esac

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
      --arg status_detail "$raw_status" \
      --arg mode "$mode" \
      --arg scope "$scope" \
      --arg plan_commit "$plan_commit" \
      --arg plan_status "$plan_status" \
      --arg stale_risk "$stale_risk" \
      --argjson affected_files "$affected_json" \
      '. + [{
        slug: $slug,
        status: $status,
        status_detail: $status_detail,
        mode: $mode,
        scope: $scope,
        plan_commit: $plan_commit,
        plan_status: $plan_status,
        affected_files: $affected_files,
        stale_risk: $stale_risk
      }]')"
  done < <(find "$ROOT/.kimiflow" -mindepth 2 -maxdepth 2 -name STATE.md -type f 2>/dev/null | sort)
fi

out="$(jq -n \
  --arg root "$ROOT" \
  --arg head "$HEAD" \
  --arg index "$MAP_INDEX" \
  --arg depth "$MAP_DEPTH" \
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
  --argjson items "$RUNS_JSON" \
  '{
    schema_version: 1,
    repo: {present: $repo_present, root: $root, head: $head, dirty: $dirty},
    project_map: {present: $map_present, valid: $map_valid, depth: $depth, status: $map_status, index: $index},
    findings: {open: $findings_open, path: $findings_path},
    improvements: {open: $improvements_open, path: $improvements_path},
    runs: {active: $active, backlog: $backlog, done: $done, other: $other, items: $items},
    repo_docs: {present: $docs_present}
  }')"

if [ "$PRETTY" -eq 1 ]; then
  printf '%s\n' "$out" | jq .
else
  printf '%s\n' "$out" | jq -c .
fi
