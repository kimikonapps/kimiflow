#!/usr/bin/env bash
# kimiflow — token-cheap local memory router. Orchestrator-invoked, not a hook.
#
# Usage:
#   memory-router.sh status [--root <path>] [--pretty]
#   memory-router.sh recall --query <text>|--query-file <path> [--root <path>] [--max <n>] [--write <path>] [--pretty]
#   memory-router.sh classify --input <path>|--text <text> [--pretty]
#   memory-router.sh record --summary <text> --topic <topic> --evidence <ref>... [--root <path>] [--kind <kind>] [--scope <scope>] [--confidence <level>] [--sensitivity <level>] [--status <status>]
#   memory-router.sh curate [--root <path>] [--write] [--pretty]
#
# Output: JSON except record, which emits one stable RECORDED line.
set -u

usage() {
  sed -n '1,12p' "$0" >&2
}

die() {
  printf 'memory-router: %s\n' "$1" >&2
  exit "${2:-1}"
}

need_jq() {
  command -v jq >/dev/null 2>&1 || die "jq is required" 2
}

resolve_root() {
  local root="$1"
  if [ -n "$root" ]; then
    (cd "$root" 2>/dev/null && pwd) || printf '%s' "$root"
  else
    git rev-parse --show-toplevel 2>/dev/null || pwd
  fi
}

iso_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

date_now() {
  date -u +"%Y-%m-%d"
}

word_count_file() {
  local file="$1"
  if [ -f "$file" ]; then
    wc -w < "$file" | tr -d '[:space:]'
  else
    printf '0'
  fi
}

json_print() {
  local json="$1" pretty="$2"
  if [ "$pretty" -eq 1 ]; then
    printf '%s\n' "$json" | jq .
  else
    printf '%s\n' "$json" | jq -c .
  fi
}

read_jsonl_summary() {
  local file="$1"
  if [ ! -f "$file" ]; then
    jq -n '{
      total: 0,
      current: 0,
      stale: 0,
      superseded: 0,
      archived: 0,
      private: 0,
      security: 0,
      by_topic: {}
    }'
    return 0
  fi

  jq -Rsc '
    def rows: split("\n") | map(select(length > 0) | (fromjson? // empty));
    rows as $rows
    | {
        total: ($rows | length),
        current: ($rows | map(select((.status // "current") == "current")) | length),
        stale: ($rows | map(select((.status // "") == "stale")) | length),
        superseded: ($rows | map(select((.status // "") == "superseded")) | length),
        archived: ($rows | map(select((.status // "") == "archived")) | length),
        private: ($rows | map(select((.sensitivity // "") == "private")) | length),
        security: ($rows | map(select((.sensitivity // "") == "security")) | length),
        by_topic: (
          $rows
          | sort_by(.topic // "uncategorized")
          | group_by(.topic // "uncategorized")
          | map({key: (.[0].topic // "uncategorized"), value: length})
          | from_entries
        )
      }
  ' "$file"
}

vault_status_json() {
  local index="$1"
  local env_available="${KIMIFLOW_VAULT_AVAILABLE:-}"
  local available=false
  local last_recall='null'
  local last_write='null'

  case "$env_available" in
    1|true|TRUE|yes|YES) available=true ;;
  esac

  if [ -f "$index" ] && jq -e . "$index" >/dev/null 2>&1; then
    if jq -e '.vault.available == true' "$index" >/dev/null 2>&1; then
      available=true
    fi
    last_recall="$(jq -c '.vault.last_recall_at // null' "$index" 2>/dev/null || printf 'null')"
    last_write="$(jq -c '.vault.last_write_at // null' "$index" 2>/dev/null || printf 'null')"
  fi

  jq -n \
    --argjson available "$available" \
    --argjson last_recall "$last_recall" \
    --argjson last_write "$last_write" \
    '{available: $available, last_recall_at: $last_recall, last_write_at: $last_write}'
}

status_json() {
  local root="$1"
  local budget="${KIMIFLOW_MEMORY_BUDGET:-900}"
  local learning_threshold="${KIMIFLOW_MEMORY_CURATE_AFTER_LEARNINGS:-10}"
  local project="$root/.kimiflow/project"
  local memory="$project/MEMORY.md"
  local learnings="$project/LEARNINGS.jsonl"
  local index="$project/MEMORY-INDEX.json"
  local recall="$project/RECALL.md"

  local memory_tokens memory_present learnings_present index_present recall_present learning_json vault_json
  memory_tokens="$(word_count_file "$memory")"
  memory_present=false; [ -f "$memory" ] && memory_present=true
  learnings_present=false; [ -f "$learnings" ] && learnings_present=true
  index_present=false; [ -f "$index" ] && index_present=true
  recall_present=false; [ -f "$recall" ] && recall_present=true
  learning_json="$(read_jsonl_summary "$learnings")"
  vault_json="$(vault_status_json "$index")"

  jq -n \
    --arg root "$root" \
    --arg memory_path ".kimiflow/project/MEMORY.md" \
    --arg learnings_path ".kimiflow/project/LEARNINGS.jsonl" \
    --arg index_path ".kimiflow/project/MEMORY-INDEX.json" \
    --arg recall_path ".kimiflow/project/RECALL.md" \
    --argjson memory_present "$memory_present" \
    --argjson learnings_present "$learnings_present" \
    --argjson index_present "$index_present" \
    --argjson recall_present "$recall_present" \
    --argjson memory_tokens "$memory_tokens" \
    --argjson budget "$budget" \
    --argjson learning_threshold "$learning_threshold" \
    --argjson learnings "$learning_json" \
    --argjson vault "$vault_json" \
    '{
      schema_version: 1,
      present: ($memory_present or $learnings_present or $index_present or $recall_present),
      root: $root,
      paths: {
        memory: $memory_path,
        learnings: $learnings_path,
        index: $index_path,
        recall: $recall_path
      },
      memory: {
        present: $memory_present,
        path: $memory_path,
        tokens_estimate: $memory_tokens,
        budget: $budget,
        over_budget: ($memory_tokens > $budget)
      },
      learnings: ($learnings + {present: $learnings_present, path: $learnings_path}),
      vault: $vault,
      curation: {
        recommended: (
          ($memory_tokens > $budget)
          or ($learnings.stale > 0)
          or ($learnings.superseded > 0)
          or (($learnings.total > 0) and ($index_present | not))
          or ($learnings.total >= $learning_threshold)
        ),
        reasons: ([
          if $memory_tokens > $budget then "memory_over_budget" else empty end,
          if $learnings.stale > 0 then "stale_learnings" else empty end,
          if $learnings.superseded > 0 then "superseded_learnings" else empty end,
          if (($learnings.total > 0) and ($index_present | not)) then "memory_index_missing" else empty end,
          if $learnings.total >= $learning_threshold then "many_learnings" else empty end
        ])
      }
    }'
}

terms_json_from_query() {
  local query="$1"
  local terms
  terms="$(printf '%s\n' "$query" \
    | tr '[:upper:]' '[:lower:]' \
    | tr -cs '[:alnum:]_-' '\n' \
    | awk '
      length($0) >= 3 &&
      $0 !~ /^(the|and|for|mit|und|der|die|das|ein|eine|ist|sind|was|wie|this|that|from|into|zur|zum|auf|von)$/ &&
      !seen[$0]++ { print }
    ' \
    | head -30 \
    | jq -R . \
    | jq -s .)"
  if [ "$(printf '%s\n' "$terms" | jq 'length')" -eq 0 ]; then
    jq -n --arg q "$(printf '%s' "$query" | tr '[:upper:]' '[:lower:]')" '[$q]'
  else
    printf '%s\n' "$terms"
  fi
}

jsonl_hits() {
  local file="$1" terms="$2" max="$3" fields="$4"
  if [ ! -f "$file" ]; then
    jq -n '[]'
    return 0
  fi

  jq -Rsc \
    --argjson terms "$terms" \
    --argjson max "$max" \
    --arg fields "$fields" \
    '
      def field_text($row; $fields):
        ($fields | split(","))
        | map(
            ($row[.] // "")
            | if type == "array" then join(" ")
              elif type == "object" then tostring
              else tostring
              end
          )
        | join(" ");
      def hit($text):
        ($text | ascii_downcase) as $t
        | any($terms[]; . as $term | ($term != "" and ($t | contains($term))));
      split("\n")
      | map(select(length > 0) | (fromjson? // empty))
      | map(select(hit(field_text(.; $fields))))
      | .[:$max]
    ' "$file"
}

write_recall_markdown() {
  local path="$1" json="$2"
  mkdir -p "$(dirname "$path")"
  {
    printf '# Recall\n\n'
    printf 'Generated: %s\n\n' "$(iso_now)"
    printf 'Query: %s\n\n' "$(printf '%s\n' "$json" | jq -r '.query')"
    printf 'Terms: %s\n\n' "$(printf '%s\n' "$json" | jq -r '.query_terms | join(", ")')"
    printf 'Token budget: %s\n\n' "$(printf '%s\n' "$json" | jq -r '.token_budget')"
    printf '## Sources\n\n'
    printf -- '- MEMORY.md: %s\n' "$(printf '%s\n' "$json" | jq -r '.sources.memory.status')"
    printf -- '- LEARNINGS.jsonl hits: %s\n' "$(printf '%s\n' "$json" | jq -r '.sources.learnings.count')"
    printf -- '- FACTS.jsonl hits: %s\n' "$(printf '%s\n' "$json" | jq -r '.sources.facts.count')"
    printf '\n## Omitted\n\n'
    printf '%s\n' "$json" | jq -r '.omitted[]? | "- " + .'
  } > "$path"
}

cmd_status() {
  local root="" pretty=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "status: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  json_print "$(status_json "$root")" "$pretty"
}

cmd_recall() {
  local root="" query="" query_file="" pretty=0 max=5 write_path=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --query) shift; query="${1:-}" ;;
      --query-file) shift; query_file="${1:-}" ;;
      --max) shift; max="${1:-}" ;;
      --write) shift; write_path="${1:-}" ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "recall: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  if [ -n "$query_file" ]; then
    [ -f "$query_file" ] || die "query file not found: $query_file" 2
    query="$(sed -n '1,120p' "$query_file")"
  fi
  [ -n "$query" ] || die "recall requires --query or --query-file" 2
  case "$max" in ''|*[!0-9]*) die "recall --max must be a number" 2 ;; esac

  local project memory learnings facts budget memory_tokens terms memory_status memory_content learning_hits fact_hits omitted json
  project="$root/.kimiflow/project"
  memory="$project/MEMORY.md"
  learnings="$project/LEARNINGS.jsonl"
  facts="$project/FACTS.jsonl"
  budget="${KIMIFLOW_MEMORY_BUDGET:-900}"
  memory_tokens="$(word_count_file "$memory")"
  terms="$(terms_json_from_query "$query")"
  omitted='[]'

  if [ -f "$memory" ]; then
    if [ "$memory_tokens" -le "$budget" ]; then
      memory_status="included"
      memory_content="$(sed -n '1,160p' "$memory")"
    else
      memory_status="omitted_over_budget"
      memory_content=""
      omitted="$(printf '%s\n' "$omitted" | jq '. + ["MEMORY.md omitted: over budget"]')"
    fi
  else
    memory_status="missing"
    memory_content=""
    omitted="$(printf '%s\n' "$omitted" | jq '. + ["MEMORY.md missing"]')"
  fi

  learning_hits="$(jsonl_hits "$learnings" "$terms" "$max" "id,kind,scope,topic,summary,status,sensitivity,evidence")"
  fact_hits="$(jsonl_hits "$facts" "$terms" "$max" "kind,area,path,summary,confidence")"

  json="$(jq -n \
    --arg query "$query" \
    --argjson terms "$terms" \
    --arg memory_status "$memory_status" \
    --arg memory_path ".kimiflow/project/MEMORY.md" \
    --arg memory_content "$memory_content" \
    --argjson memory_tokens "$memory_tokens" \
    --argjson budget "$budget" \
    --argjson learnings "$learning_hits" \
    --argjson facts "$fact_hits" \
    --argjson omitted "$omitted" \
    '{
      schema_version: 1,
      query: $query,
      query_terms: $terms,
      token_budget: $budget,
      sources: {
        memory: {
          path: $memory_path,
          status: $memory_status,
          tokens_estimate: $memory_tokens,
          content: $memory_content
        },
        learnings: {
          path: ".kimiflow/project/LEARNINGS.jsonl",
          count: ($learnings | length),
          hits: $learnings
        },
        facts: {
          path: ".kimiflow/project/FACTS.jsonl",
          count: ($facts | length),
          hits: $facts
        }
      },
      omitted: $omitted
    }')"

  if [ -n "$write_path" ]; then
    case "$write_path" in
      /*) ;;
      *) write_path="$root/$write_path" ;;
    esac
    write_recall_markdown "$write_path" "$json"
  fi
  json_print "$json" "$pretty"
}

classify_text() {
  local text="$1"
  local lower words sensitivity target confidence reasons vault_allowed repo_doc_allowed sanitized_required
  lower="$(printf '%s\n' "$text" | tr '[:upper:]' '[:lower:]')"
  words="$(printf '%s\n' "$text" | wc -w | tr -d '[:space:]')"
  sensitivity="normal"
  target="run_only"
  confidence="medium"
  reasons='[]'
  vault_allowed=true
  repo_doc_allowed=false
  sanitized_required=false

  if printf '%s\n' "$lower" | grep -Eq '(secret|token|credential|password|private key|\.env|vulnerab|exploit|auth bypass|cve-|xss|csrf|sql injection)'; then
    sensitivity="security"
    vault_allowed=false
    repo_doc_allowed=false
    sanitized_required=true
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["security_sensitive"]')"
  elif printf '%s\n' "$lower" | grep -Eq '(/users/|/home/|customer|client|kunde|kundendaten|private|vault|obsidian)'; then
    sensitivity="private"
    vault_allowed=true
    repo_doc_allowed=false
    sanitized_required=true
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["private_or_local_detail"]')"
  fi

  if [ "$words" -lt 4 ] || printf '%s\n' "$lower" | grep -Eq '^(ok|done|fixed|typo|scratch|temporary)$'; then
    target="skip"
    confidence="high"
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["too_small_or_trivial"]')"
  elif printf '%s\n' "$lower" | grep -Eq '(readme|repo doc|documentation|docs/|architecture doc|onboarding|public docs|publish-safe)'; then
    target="repo_doc_candidate"
    if [ "$sensitivity" = "normal" ] || [ "$sensitivity" = "public" ]; then
      repo_doc_allowed=true
    fi
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["documentation_candidate"]')"
  elif printf '%s\n' "$lower" | grep -Eq '(cross-project|preference|always|remember|pattern|lesson|decision|learned|wiederkehrend|arbeitsstil|vault)'; then
    target="vault"
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["long_term_or_cross_project"]')"
  elif printf '%s\n' "$lower" | grep -Eq '(test|build|release|convention|standard|decision|architecture|flow|hook|launcher|codex|claude|project map|memory|vault|kimiflow)'; then
    target="project_memory"
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["project_reusable"]')"
  fi

  if [ "$sensitivity" = "security" ]; then
    target="project_memory"
    confidence="high"
  fi

  jq -n \
    --arg target "$target" \
    --arg sensitivity "$sensitivity" \
    --arg confidence "$confidence" \
    --argjson reasons "$reasons" \
    --argjson vault_allowed "$vault_allowed" \
    --argjson repo_doc_allowed "$repo_doc_allowed" \
    --argjson sanitized_required "$sanitized_required" \
    '{
      schema_version: 1,
      classification: {
        target: $target,
        sensitivity: $sensitivity,
        confidence: $confidence,
        reasons: $reasons,
        vault_allowed: $vault_allowed,
        repo_doc_allowed: $repo_doc_allowed,
        sanitized_required: $sanitized_required
      }
    }'
}

cmd_classify() {
  local input="" text="" pretty=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --input) shift; input="${1:-}" ;;
      --text) shift; text="${1:-}" ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "classify: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  if [ -n "$input" ]; then
    [ -f "$input" ] || die "input not found: $input" 2
    text="$(sed -n '1,160p' "$input")"
  fi
  [ -n "$text" ] || die "classify requires --input or --text" 2
  json_print "$(classify_text "$text")" "$pretty"
}

slugify() {
  printf '%s\n' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | tr -cs '[:alnum:]' '-' \
    | sed 's/^-//; s/-$//; s/--*/-/g' \
    | cut -c1-40
}

cmd_record() {
  local root="" summary="" topic="" kind="learning" scope="project" confidence="medium" sensitivity="normal" status="current"
  local evidence_json='[]'
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --summary) shift; summary="${1:-}" ;;
      --topic) shift; topic="${1:-}" ;;
      --kind) shift; kind="${1:-}" ;;
      --scope) shift; scope="${1:-}" ;;
      --confidence) shift; confidence="${1:-}" ;;
      --sensitivity) shift; sensitivity="${1:-}" ;;
      --status) shift; status="${1:-}" ;;
      --evidence) shift; evidence_json="$(printf '%s\n' "$evidence_json" | jq --arg value "${1:-}" '. + [$value]')" ;;
      --help|-h) usage; exit 0 ;;
      *) die "record: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  [ -n "$summary" ] || die "record requires --summary" 2
  [ -n "$topic" ] || die "record requires --topic" 2
  [ "$(printf '%s\n' "$evidence_json" | jq 'length')" -gt 0 ] || die "record requires at least one --evidence" 2
  root="$(resolve_root "$root")"

  local project learnings source_commit id row
  project="$root/.kimiflow/project"
  learnings="$project/LEARNINGS.jsonl"
  mkdir -p "$project"
  source_commit="$(git -C "$root" rev-parse --short HEAD 2>/dev/null || printf 'NOT VERIFIED')"
  id="learn_$(date -u +%Y%m%d)_$(slugify "$topic")_$$"
  row="$(jq -nc \
    --arg id "$id" \
    --arg kind "$kind" \
    --arg scope "$scope" \
    --arg topic "$topic" \
    --arg summary "$summary" \
    --argjson evidence "$evidence_json" \
    --arg confidence "$confidence" \
    --arg sensitivity "$sensitivity" \
    --arg last_verified "$(date_now)" \
    --arg source_commit "$source_commit" \
    --arg status "$status" \
    '{
      id: $id,
      kind: $kind,
      scope: $scope,
      topic: $topic,
      summary: $summary,
      evidence: $evidence,
      confidence: $confidence,
      sensitivity: $sensitivity,
      last_verified: $last_verified,
      source_commit: $source_commit,
      status: $status
    }')"
  printf '%s\n' "$row" >> "$learnings"
  printf 'RECORDED\t%s\t%s\n' ".kimiflow/project/LEARNINGS.jsonl" "$id"
}

repo_id() {
  local root="$1" remote
  remote="$(git -C "$root" config --get remote.origin.url 2>/dev/null || true)"
  if [ -n "$remote" ]; then
    printf '%s\n' "$remote" | sed -E 's#^git@github.com:#github.com/#; s#^https://##; s#\.git$##'
  else
    printf 'unknown'
  fi
}

cmd_curate() {
  local root="" pretty=0 write=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "curate: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"

  local project memory learnings index status learning_summary vault existing_vault topics out
  project="$root/.kimiflow/project"
  memory="$project/MEMORY.md"
  learnings="$project/LEARNINGS.jsonl"
  index="$project/MEMORY-INDEX.json"
  status="$(status_json "$root")"
  learning_summary="$(read_jsonl_summary "$learnings")"
  vault="$(vault_status_json "$index")"
  topics='{}'
  if [ -f "$learnings" ]; then
    topics="$(jq -Rsc '
      split("\n")
      | map(select(length > 0) | (fromjson? // empty))
      | map(select((.status // "current") == "current"))
      | sort_by(.topic // "uncategorized")
      | group_by(.topic // "uncategorized")
      | map({key: (.[0].topic // "uncategorized"), value: map(.id)})
      | from_entries
    ' "$learnings")"
  fi

  existing_vault="$vault"
  out="$(jq -n \
    --arg updated_at "$(iso_now)" \
    --arg repo_id "$(repo_id "$root")" \
    --arg language "de" \
    --argjson tokens "$(word_count_file "$memory")" \
    --argjson learnings "$learning_summary" \
    --argjson vault "$existing_vault" \
    --argjson topics "$topics" \
    --argjson status "$status" \
    '{
      schema_version: 1,
      updated_at: $updated_at,
      repo_id: $repo_id,
      language: $language,
      always_on_memory_tokens_estimate: $tokens,
      vault: $vault,
      learnings: $learnings,
      topics: $topics,
      curation: $status.curation
    }')"

  if [ "$write" -eq 1 ]; then
    mkdir -p "$project"
    printf '%s\n' "$out" | jq . > "$index"
  fi
  json_print "$out" "$pretty"
}

cmd="${1:-}"
[ -n "$cmd" ] || { usage; exit 2; }
shift

case "$cmd" in
  status) cmd_status "$@" ;;
  recall) cmd_recall "$@" ;;
  classify) cmd_classify "$@" ;;
  record) cmd_record "$@" ;;
  curate) cmd_curate "$@" ;;
  --help|-h|help) usage; exit 0 ;;
  *) die "unknown command: $cmd" 2 ;;
esac
