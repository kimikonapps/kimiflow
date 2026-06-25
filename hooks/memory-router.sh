#!/usr/bin/env bash
# kimiflow — token-cheap local memory router. Orchestrator-invoked, not a hook.
#
# Usage:
#   memory-router.sh status [--root <path>] [--pretty]
#   memory-router.sh recall --query <text>|--query-file <path> [--root <path>] [--max <n>] [--write <path>] [--pretty]
#   memory-router.sh history [--query <text>|--query-file <path>] [--root <path>] [--max <n>] [--write] [--pretty]
#   memory-router.sh classify --input <path>|--text <text> [--pretty]
#   memory-router.sh record --summary <text> --topic <topic> --evidence <ref>... [--root <path>] [--kind <kind>] [--scope <scope>] [--confidence <level>] [--sensitivity <level>] [--status <status>]
#   memory-router.sh review-run --run <path> [--root <path>] [--write] [--pretty] [--skip <reason>]
#   memory-router.sh verify-run --run <path> [--root <path>]
#   memory-router.sh curate [--root <path>] [--write] [--pretty]
#   memory-router.sh index [--root <path>] [--write] [--pretty]
#   memory-router.sh consolidate [--root <path>] [--write] [--pretty]
#   memory-router.sh propose [--root <path>] [--write] [--approve <id>] [--reject <id>] [--reason <text>] [--apply] [--pretty]
#   memory-router.sh provider <status|configure|prefetch|sync> [--root <path>] [--type <obsidian|none>] [--available <true|false>] [--path <path>] [--pretty]
#
# Output: JSON except record/verify-run, which emit stable tab-separated lines.
set -u

usage() {
  sed -n '1,16p' "$0" >&2
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

jsonl_rows() {
  local file="$1"
  if [ -f "$file" ]; then
    jq -Rsc 'split("\n") | map(select(length > 0) | (fromjson? // empty))' "$file"
  else
    jq -n '[]'
  fi
}

proposal_summary_json() {
  local file="$1"
  if [ -f "$file" ]; then
    jq -Rsc '
      split("\n")
      | map(select(length > 0) | (fromjson? // empty)) as $rows
      | {
          present: true,
          path: ".kimiflow/project/PROPOSALS.jsonl",
          total: ($rows | length),
          pending: ($rows | map(select((.status // "pending") == "pending")) | length),
          approved: ($rows | map(select((.status // "") == "approved")) | length),
          applied: ($rows | map(select((.status // "") == "applied")) | length),
          rejected: ($rows | map(select((.status // "") == "rejected")) | length),
          needs_revalidation: ($rows | map(select((.status // "") == "needs_revalidation")) | length),
          by_type: (reduce $rows[] as $row ({}; ($row.type // "unknown") as $type | .[$type] = ((.[$type] // 0) + 1)))
        }
    ' "$file"
  else
    jq -n '{
      present: false,
      path: ".kimiflow/project/PROPOSALS.jsonl",
      total: 0,
      pending: 0,
      approved: 0,
      applied: 0,
      rejected: 0,
      needs_revalidation: 0,
      by_type: {}
    }'
  fi
}

memory_security_json() {
  local text="$1"
  local lower reasons='[]'
  lower="$(printf '%s\n' "$text" | tr '[:upper:]' '[:lower:]')"

  if printf '%s\n' "$lower" | grep -Eq '(ignore|disregard|override).{0,40}(previous|prior|above|system|developer|instructions)|system prompt|developer message|hidden instruction|prompt injection|jailbreak'; then
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["instruction_override"]')"
  fi
  if printf '%s\n' "$lower" | grep -Eq '(exfiltrat|send|post|upload|leak|reveal).{0,80}(secret|token|credential|password|private key|api key|\\.env)|credential harvesting|ssh backdoor'; then
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["exfiltration_or_credential_request"]')"
  fi
  if command -v perl >/dev/null 2>&1; then
    if printf '%s' "$text" | perl -CS -ne '$found = 1 if /[\x{200B}-\x{200F}\x{202A}-\x{202E}\x{2060}-\x{206F}]/; END { exit($found ? 0 : 1) }' >/dev/null 2>&1; then
      reasons="$(printf '%s\n' "$reasons" | jq '. + ["hidden_unicode"]')"
    fi
  fi

  jq -n --argjson reasons "$reasons" '{
    ok: (($reasons | length) == 0),
    reasons: $reasons
  }'
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

date_days_ago() {
  local days="$1"
  if date -u -v-"$days"d +"%Y-%m-%d" >/dev/null 2>&1; then
    date -u -v-"$days"d +"%Y-%m-%d"
  elif date -u -d "$days days ago" +"%Y-%m-%d" >/dev/null 2>&1; then
    date -u -d "$days days ago" +"%Y-%m-%d"
  else
    printf ''
  fi
}

usage_summary_json() {
  local usage_file="$1"
  if [ ! -f "$usage_file" ] || ! jq -e . "$usage_file" >/dev/null 2>&1; then
    jq -n --arg path ".kimiflow/project/MEMORY-USAGE.json" '{
      present: false,
      path: $path,
      tracked_items: 0,
      total_uses: 0,
      last_used_at: null,
      by_kind: {}
    }'
    return 0
  fi

  jq '
    (.items // {}) as $items
    | {
        present: true,
        path: ".kimiflow/project/MEMORY-USAGE.json",
        tracked_items: ($items | length),
        total_uses: ([$items[]?.use_count // 0] | add // 0),
        last_used_at: ([$items[]?.last_used_at // empty] | sort | last // null),
        by_kind: (
          reduce ($items[]?) as $item ({}; ($item.kind // "unknown") as $kind | .[$kind] = ((.[$kind] // 0) + 1))
        )
      }
  ' "$usage_file"
}

learning_lifecycle_json() {
  local learnings="$1" usage_file="$2"
  local stale_after="${KIMIFLOW_LEARNING_STALE_AFTER_DAYS:-90}"
  local cutoff
  case "$stale_after" in ''|*[!0-9]*) stale_after=90 ;; esac
  cutoff="$(date_days_ago "$stale_after")"
  if [ ! -f "$learnings" ]; then
    jq -n \
      --argjson stale_after "$stale_after" \
      --arg cutoff "$cutoff" \
      '{
        stale_after_days: $stale_after,
        cutoff_date: (if $cutoff == "" then null else $cutoff end),
        current: 0,
        stale_candidates: 0,
        stale_candidate_ids: [],
        unused_current: 0,
        used_current: 0
      }'
    return 0
  fi

  local usage='{}'
  if [ -f "$usage_file" ] && jq -e . "$usage_file" >/dev/null 2>&1; then
    usage="$(jq -c '.items // {}' "$usage_file")"
  fi

  jq -Rsc \
    --argjson usage "$usage" \
    --arg cutoff "$cutoff" \
    --argjson stale_after "$stale_after" \
    '
      split("\n")
      | map(select(length > 0) | (fromjson? // empty))
      | map(select((.status // "current") == "current")) as $current
      | ($current | map(.id // "") | map(select(length > 0))) as $ids
      | ($ids | map(select(($usage["learning:" + .] // null) != null))) as $used
      | ($current | map(select(($cutoff != "") and ((.last_verified // "") < $cutoff))) | map(.id // "")) as $stale_ids
      | {
          stale_after_days: $stale_after,
          cutoff_date: (if $cutoff == "" then null else $cutoff end),
          current: ($current | length),
          stale_candidates: ($stale_ids | length),
          stale_candidate_ids: $stale_ids,
          unused_current: (($ids | length) - ($used | length)),
          used_current: ($used | length)
        }
    ' "$learnings"
}

provider_manifest_json() {
  local file="$1"
  if [ -f "$file" ] && jq -e . "$file" >/dev/null 2>&1; then
    jq '.' "$file"
  else
    jq -n '{
      schema_version: 1,
      type: "none",
      available: false,
      mode: "local-first",
      vault_path: "",
      last_prefetch_at: null,
      last_write_at: null,
      synced_learning_ids: [],
      updated_at: null
    }'
  fi
}

provider_status_json() {
  local manifest_file="$1"
  local manifest env_available available
  manifest="$(provider_manifest_json "$manifest_file")"
  env_available="${KIMIFLOW_VAULT_AVAILABLE:-}"
  available="$(printf '%s\n' "$manifest" | jq -r '.available == true')"
  case "$env_available" in
    1|true|TRUE|yes|YES) available=true ;;
  esac
  printf '%s\n' "$manifest" | jq \
    --arg path ".kimiflow/project/VAULT-PROVIDER.json" \
    --argjson available "$available" \
    '{
      present: (.updated_at != null or .type != "none"),
      path: $path,
      type: (.type // "none"),
      available: $available,
      mode: (.mode // "local-first"),
      vault_path: (.vault_path // ""),
      last_prefetch_at: (.last_prefetch_at // null),
      last_write_at: (.last_write_at // null),
      capabilities: {
        status: true,
        prefetch: $available,
        sync: $available,
        write: $available,
        extract: $available
      }
    }'
}

provider_sync_base_candidates_json() {
  local learnings="$1" manifest_file="$2"
  local synced
  synced="$(provider_manifest_json "$manifest_file" | jq -c '.synced_learning_ids // []')"
  jsonl_rows "$learnings" | jq --argjson synced "$synced" '
    map(select((.status // "current") == "current"))
    | map(select((.sensitivity // "normal") != "security" and (.sensitivity // "normal") != "private"))
    | map(select((.evidence // []) | length > 0))
    | map(select(((.evidence // []) | any(. == "NOT VERIFIED" or . == "OUTSIDE_REPO")) | not))
    | map(select((.evidence_fingerprints // []) | length > 0))
    | map(select(((.evidence_fingerprints // []) | all(.status == "current"))))
    | map(select((.id // "") as $id | ($id != "" and (($synced | index($id)) == null))))
  '
}

provider_sync_candidates_json() {
  local root="$1" learnings="$2" manifest_file="$3"
  local candidates fresh='[]' row evidence_json stored_fingerprints current_fingerprints
  candidates="$(provider_sync_base_candidates_json "$learnings" "$manifest_file")"
  while IFS= read -r row; do
    [ -n "$row" ] || continue
    evidence_json="$(printf '%s\n' "$row" | jq -c '.evidence // []')"
    stored_fingerprints="$(printf '%s\n' "$row" | jq -c '.evidence_fingerprints // []')"
    current_fingerprints="$(evidence_fingerprints_json "$root" "$evidence_json")"
    if [ "$stored_fingerprints" = "$current_fingerprints" ]; then
      fresh="$(printf '%s\n' "$fresh" | jq --argjson row "$row" '. + [$row]')"
    fi
  done < <(printf '%s\n' "$candidates" | jq -c '.[]')
  printf '%s\n' "$fresh"
}

provider_sync_status_json() {
  local root="$1" learnings="$2" manifest_file="$3"
  local provider candidates
  provider="$(provider_status_json "$manifest_file")"
  candidates="$(provider_sync_candidates_json "$root" "$learnings" "$manifest_file")"
  jq -n \
    --arg path ".kimiflow/project/VAULT-SYNC.md" \
    --argjson provider "$provider" \
    --argjson candidates "$candidates" \
    '{
      path: $path,
      available: ($provider.available == true),
      pending_count: (if $provider.available == true then ($candidates | length) else 0 end),
      pending_ids: (if $provider.available == true then ($candidates | map(.id)) else [] end),
      exportable_count: ($candidates | length),
      status: (
        if $provider.available != true then "provider_unavailable"
        elif ($candidates | length) > 0 then "pending"
        else "current"
        end
      )
    }'
}

vault_status_json() {
  local index="$1" provider_manifest="${2:-}"
  local env_available="${KIMIFLOW_VAULT_AVAILABLE:-}"
  local available=false
  local last_recall='null'
  local last_write='null'
  local provider='null'
  local index_recall='null'
  local index_write='null'

  case "$env_available" in
    1|true|TRUE|yes|YES) available=true ;;
  esac

  if [ -n "$provider_manifest" ]; then
    provider="$(provider_status_json "$provider_manifest")"
    if printf '%s\n' "$provider" | jq -e '.available == true' >/dev/null 2>&1; then
      available=true
    fi
    last_recall="$(printf '%s\n' "$provider" | jq -c '.last_prefetch_at // null')"
    last_write="$(printf '%s\n' "$provider" | jq -c '.last_write_at // null')"
  fi

  if [ -f "$index" ] && jq -e . "$index" >/dev/null 2>&1; then
    if jq -e '.vault.available == true' "$index" >/dev/null 2>&1; then
      available=true
    fi
    index_recall="$(jq -c '.vault.last_recall_at // null' "$index" 2>/dev/null || printf 'null')"
    index_write="$(jq -c '.vault.last_write_at // null' "$index" 2>/dev/null || printf 'null')"
    [ "$last_recall" != "null" ] || last_recall="$index_recall"
    [ "$last_write" != "null" ] || last_write="$index_write"
  fi

  jq -n \
    --argjson available "$available" \
    --argjson last_recall "$last_recall" \
    --argjson last_write "$last_write" \
    --argjson provider "$provider" \
    '{
      available: $available,
      last_recall_at: $last_recall,
      last_write_at: $last_write,
      provider: $provider
    }'
}

status_json() {
  local root="$1"
  local budget="${KIMIFLOW_MEMORY_BUDGET:-900}"
  local learning_threshold="${KIMIFLOW_MEMORY_CURATE_AFTER_LEARNINGS:-10}"
  local project="$root/.kimiflow/project"
  local memory="$project/MEMORY.md"
  local learnings="$project/LEARNINGS.jsonl"
  local user_memory="$project/USER.md"
  local user_rows="$project/USER.jsonl"
  local index="$project/MEMORY-INDEX.json"
  local recall="$project/RECALL.md"
  local recall_db="$project/RECALL.sqlite"
  local run_history="$project/RUN-HISTORY.json"
  local usage_file="$project/MEMORY-USAGE.json"
  local provider_manifest="$project/VAULT-PROVIDER.json"
  local proposal_rows="$project/PROPOSALS.jsonl"

  local memory_tokens user_tokens memory_present learnings_present user_memory_present user_rows_present index_present recall_present recall_db_present run_history_present usage_present provider_present proposal_rows_present learning_json user_json proposals_json usage_json lifecycle_json provider_json provider_sync_json vault_json sqlite_available
  memory_tokens="$(word_count_file "$memory")"
  user_tokens="$(word_count_file "$user_memory")"
  memory_present=false; [ -f "$memory" ] && memory_present=true
  learnings_present=false; [ -f "$learnings" ] && learnings_present=true
  user_memory_present=false; [ -f "$user_memory" ] && user_memory_present=true
  user_rows_present=false; [ -f "$user_rows" ] && user_rows_present=true
  index_present=false; [ -f "$index" ] && index_present=true
  recall_present=false; [ -f "$recall" ] && recall_present=true
  recall_db_present=false; [ -f "$recall_db" ] && recall_db_present=true
  run_history_present=false; [ -f "$run_history" ] && run_history_present=true
  usage_present=false; [ -f "$usage_file" ] && usage_present=true
  provider_present=false; [ -f "$provider_manifest" ] && provider_present=true
  proposal_rows_present=false; [ -f "$proposal_rows" ] && proposal_rows_present=true
  sqlite_available=false; command -v sqlite3 >/dev/null 2>&1 && sqlite_available=true
  learning_json="$(read_jsonl_summary "$learnings")"
  user_json="$(read_jsonl_summary "$user_rows")"
  proposals_json="$(proposal_summary_json "$proposal_rows")"
  usage_json="$(usage_summary_json "$usage_file")"
  lifecycle_json="$(learning_lifecycle_json "$learnings" "$usage_file")"
  provider_json="$(provider_status_json "$provider_manifest")"
  provider_sync_json="$(provider_sync_status_json "$root" "$learnings" "$provider_manifest")"
  vault_json="$(vault_status_json "$index" "$provider_manifest")"

  jq -n \
    --arg root "$root" \
    --arg memory_path ".kimiflow/project/MEMORY.md" \
    --arg learnings_path ".kimiflow/project/LEARNINGS.jsonl" \
    --arg user_memory_path ".kimiflow/project/USER.md" \
    --arg user_rows_path ".kimiflow/project/USER.jsonl" \
    --arg proposals_path ".kimiflow/project/PROPOSALS.jsonl" \
    --arg index_path ".kimiflow/project/MEMORY-INDEX.json" \
    --arg recall_path ".kimiflow/project/RECALL.md" \
    --arg recall_db_path ".kimiflow/project/RECALL.sqlite" \
    --arg run_history_path ".kimiflow/project/RUN-HISTORY.json" \
    --arg usage_path ".kimiflow/project/MEMORY-USAGE.json" \
    --arg provider_path ".kimiflow/project/VAULT-PROVIDER.json" \
    --arg provider_sync_path ".kimiflow/project/VAULT-SYNC.md" \
    --argjson memory_present "$memory_present" \
    --argjson learnings_present "$learnings_present" \
    --argjson user_memory_present "$user_memory_present" \
    --argjson user_rows_present "$user_rows_present" \
    --argjson index_present "$index_present" \
    --argjson recall_present "$recall_present" \
    --argjson recall_db_present "$recall_db_present" \
    --argjson run_history_present "$run_history_present" \
    --argjson usage_present "$usage_present" \
    --argjson provider_present "$provider_present" \
    --argjson proposal_rows_present "$proposal_rows_present" \
    --argjson sqlite_available "$sqlite_available" \
    --argjson memory_tokens "$memory_tokens" \
    --argjson user_tokens "$user_tokens" \
    --argjson budget "$budget" \
    --argjson learning_threshold "$learning_threshold" \
    --argjson learnings "$learning_json" \
    --argjson user_profile "$user_json" \
    --argjson proposals "$proposals_json" \
    --argjson usage "$usage_json" \
    --argjson lifecycle "$lifecycle_json" \
    --argjson provider "$provider_json" \
    --argjson provider_sync "$provider_sync_json" \
    --argjson vault "$vault_json" \
    '{
      schema_version: 1,
      present: ($memory_present or $learnings_present or $user_memory_present or $user_rows_present or $index_present or $recall_present or $recall_db_present or $run_history_present or $usage_present or $provider_present or $proposal_rows_present),
      root: $root,
      paths: {
        memory: $memory_path,
        learnings: $learnings_path,
        user_memory: $user_memory_path,
        user_profile: $user_rows_path,
        proposals: $proposals_path,
        index: $index_path,
        recall: $recall_path,
        recall_index: $recall_db_path,
        run_history: $run_history_path,
        usage: $usage_path,
        provider: $provider_path,
        provider_sync: $provider_sync_path
      },
      memory: {
        present: $memory_present,
        path: $memory_path,
        tokens_estimate: $memory_tokens,
        budget: $budget,
        over_budget: ($memory_tokens > $budget)
      },
      user_profile: {
        present: ($user_memory_present or $user_rows_present),
        memory_present: $user_memory_present,
        rows_present: $user_rows_present,
        path: $user_memory_path,
        rows_path: $user_rows_path,
        tokens_estimate: $user_tokens,
        rows: $user_profile
      },
      learnings: ($learnings + {present: $learnings_present, path: $learnings_path}),
      lifecycle: $lifecycle,
      usage: ($usage + {present: $usage_present, path: $usage_path}),
      proposals: $proposals,
      history: {
        present: $run_history_present,
        path: $run_history_path
      },
      recall_index: {
        present: $recall_db_present,
        path: $recall_db_path,
        sqlite_available: $sqlite_available
      },
      provider: ($provider + {present: ($provider.present or $provider_present), path: $provider_path, sync: $provider_sync}),
      vault: $vault,
      curation: {
        recommended: (
          ($memory_tokens > $budget)
          or ($learnings.stale > 0)
          or ($learnings.superseded > 0)
          or ($lifecycle.stale_candidates > 0)
          or (($learnings.total > 0) and ($index_present | not))
          or ($learnings.total >= $learning_threshold)
          or (($learnings.total > 0) and ($sqlite_available == true) and ($recall_db_present | not))
          or ($proposals.pending > 0)
          or ($proposals.approved > 0)
          or ($proposals.needs_revalidation > 0)
          or ($provider_sync.pending_count > 0)
        ),
        reasons: ([
          if $memory_tokens > $budget then "memory_over_budget" else empty end,
          if $learnings.stale > 0 then "stale_learnings" else empty end,
          if $learnings.superseded > 0 then "superseded_learnings" else empty end,
          if $lifecycle.stale_candidates > 0 then "learning_lifecycle_review_due" else empty end,
          if (($learnings.total > 0) and ($index_present | not)) then "memory_index_missing" else empty end,
          if $learnings.total >= $learning_threshold then "many_learnings" else empty end,
          if (($learnings.total > 0) and ($sqlite_available == true) and ($recall_db_present | not)) then "recall_index_missing" else empty end,
          if $proposals.pending > 0 then "learning_proposals_pending" else empty end,
          if $proposals.approved > 0 then "learning_proposals_approved" else empty end,
          if $proposals.needs_revalidation > 0 then "learning_proposals_need_revalidation" else empty end,
          if $provider_sync.pending_count > 0 then "provider_sync_pending" else empty end
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
      | map(select((.status // "current") == "current"))
      | map(select(hit(field_text(.; $fields))))
      | .[:$max]
    ' "$file"
}

run_artifact_rows_json() {
  local root="$1"
  local project="$root/.kimiflow/project"
  local out='[]' file rel slug artifact body summary
  if [ ! -d "$root/.kimiflow" ]; then
    jq -n '[]'
    return 0
  fi

  while IFS= read -r file; do
    [ -n "$file" ] || continue
    rel="$(rel_path "$root" "$file")"
    slug="$(basename "$(dirname "$file")")"
    artifact="$(basename "$file")"
    body="$(sed -n '1,180p' "$file")"
    summary="$(printf '%s\n' "$body" | awk '
      {
        line = $0
        sub(/^[[:space:]]+/, "", line)
        sub(/[[:space:]]+$/, "", line)
        if (line == "") next
        if (line ~ /^#{1,6}[[:space:]]/) next
        if (line ~ /^```/) next
        gsub(/[[:space:]]+/, " ", line)
        print line
        exit
      }
    ' | cut -c1-420)"
    out="$(printf '%s\n' "$out" | jq \
      --arg slug "$slug" \
      --arg artifact "$artifact" \
      --arg path "$rel" \
      --arg summary "$summary" \
      --arg text "$body" \
      '. + [{
        kind: "run_artifact",
        slug: $slug,
        artifact: $artifact,
        path: $path,
        ref: $path,
        title: ($slug + " · " + $artifact),
        summary: $summary,
        text: $text
      }]')"
  done < <(find "$root/.kimiflow" -path "$project" -prune -o -type f \( -name 'INTENT.md' -o -name 'PROBLEM.md' -o -name 'RESEARCH.md' -o -name 'DIAGNOSIS.md' -o -name 'PLAN.md' -o -name 'ACCEPTANCE.md' -o -name 'CODE-REVIEW.md' -o -name 'LEARNING-REVIEW.md' -o -name 'ADVISORIES.md' -o -name 'STATE.md' \) -print 2>/dev/null | sort)
  printf '%s\n' "$out"
}

run_artifact_hits_json() {
  local root="$1" terms="$2" max="$3"
  run_artifact_rows_json "$root" | jq \
    --argjson terms "$terms" \
    --argjson max "$max" \
    '
      def hit($text):
        ($text | ascii_downcase) as $t
        | any($terms[]?; . as $term | ($term != "" and ($t | contains($term))));
      map(select(hit((.slug + " " + .artifact + " " + .summary + " " + .text))))
      | .[:$max]
      | map(del(.text))
    '
}

write_history_markdown() {
  local path="$1" json="$2"
  mkdir -p "$(dirname "$path")"
  {
    printf '# Run History Recall\n\n'
    printf 'Generated: %s\n\n' "$(iso_now)"
    printf 'Query: %s\n\n' "$(printf '%s\n' "$json" | jq -r '.query')"
    printf 'Hits: %s\n\n' "$(printf '%s\n' "$json" | jq -r '.hits | length')"
    printf '## Hits\n\n'
    printf '%s\n' "$json" | jq -r '
      .hits[]
      | "- [" + (.slug // "run") + " · " + (.artifact // "artifact") + "] "
        + (.summary // "")
        + " (" + (.path // "") + ")"
    '
  } > "$path"
}

update_usage_metrics() {
  local root="$1" hits="$2"
  local project="$root/.kimiflow/project"
  local usage_file="$project/MEMORY-USAGE.json"
  local now current updates tmp
  mkdir -p "$project"
  now="$(iso_now)"
  if [ -f "$usage_file" ] && jq -e . "$usage_file" >/dev/null 2>&1; then
    current="$(cat "$usage_file")"
  else
    current="$(jq -n '{schema_version: 1, updated_at: null, items: {}}')"
  fi
  updates="$(printf '%s\n' "$hits" | jq '
    def hit_key:
      if (.id // "") != "" then "learning:" + .id
      elif (.kind // "") == "run_artifact" then "run:" + (.path // (.ref // "unknown"))
      else (.kind // "memory") + ":" + (.ref // (.path // (.title // "unknown")))
      end;
    map({
      key: hit_key,
      value: {
        kind: (.kind // "memory"),
        source: (.source // .path // ""),
        title: (.title // .summary // .id // ""),
        ref: (.ref // ((.evidence // []) | .[0] // "")),
        summary: (.summary // "")
      }
    })
  ')"
  tmp="$(mktemp "${usage_file}.tmp.XXXXXX")"
  jq \
    --arg now "$now" \
    --argjson updates "$updates" \
    '
      .schema_version = 1
      | .updated_at = $now
      | .items = (.items // {})
      | reduce $updates[] as $update (.;
          .items[$update.key] = (
            (.items[$update.key] // {})
            + $update.value
            + {
                use_count: (((.items[$update.key].use_count // 0) + 1)),
                last_used_at: $now
              }
          )
        )
    ' <<EOF > "$tmp"
$current
EOF
  mv "$tmp" "$usage_file"
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
    printf -- '- USER.md: %s\n' "$(printf '%s\n' "$json" | jq -r '.sources.user_profile.status')"
    printf -- '- LEARNINGS.jsonl hits: %s\n' "$(printf '%s\n' "$json" | jq -r '.sources.learnings.count')"
    printf -- '- FACTS.jsonl hits: %s\n' "$(printf '%s\n' "$json" | jq -r '.sources.facts.count')"
    printf -- '- RECALL.sqlite: %s (%s hits)\n' "$(printf '%s\n' "$json" | jq -r '.sources.index.status')" "$(printf '%s\n' "$json" | jq -r '.sources.index.count')"
    printf -- '- Run history hits: %s\n' "$(printf '%s\n' "$json" | jq -r '.sources.history.count')"
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

  local project memory user_memory learnings facts budget user_budget memory_tokens user_tokens terms memory_status memory_content user_status user_content learning_hits fact_hits index_hits index_status history_hits omitted json usage_hits
  project="$root/.kimiflow/project"
  memory="$project/MEMORY.md"
  user_memory="$project/USER.md"
  learnings="$project/LEARNINGS.jsonl"
  facts="$project/FACTS.jsonl"
  budget="${KIMIFLOW_MEMORY_BUDGET:-900}"
  user_budget="${KIMIFLOW_USER_MEMORY_BUDGET:-500}"
  memory_tokens="$(word_count_file "$memory")"
  user_tokens="$(word_count_file "$user_memory")"
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
  if [ -f "$user_memory" ]; then
    if [ "$user_tokens" -le "$user_budget" ]; then
      user_status="included"
      user_content="$(sed -n '1,120p' "$user_memory")"
    else
      user_status="omitted_over_budget"
      user_content=""
      omitted="$(printf '%s\n' "$omitted" | jq '. + ["USER.md omitted: over budget"]')"
    fi
  else
    user_status="missing"
    user_content=""
    omitted="$(printf '%s\n' "$omitted" | jq '. + ["USER.md missing"]')"
  fi

  learning_hits="$(jsonl_hits "$learnings" "$terms" "$max" "id,kind,scope,topic,summary,status,sensitivity,evidence")"
  fact_hits="$(jsonl_hits "$facts" "$terms" "$max" "kind,area,path,summary,confidence")"
  index_hits="$(fts_hits_json "$root" "$terms" "$max")"
  history_hits="$(run_artifact_hits_json "$root" "$terms" "$max")"
  if [ "$(printf '%s\n' "$index_hits" | jq 'length')" -gt 0 ]; then
    index_status="used"
  elif [ -f "$project/RECALL.sqlite" ]; then
    index_status="available_no_hits"
  elif sqlite_available; then
    index_status="missing"
  else
    index_status="unavailable"
  fi

  json="$(jq -n \
    --arg query "$query" \
    --argjson terms "$terms" \
    --arg memory_status "$memory_status" \
    --arg memory_path ".kimiflow/project/MEMORY.md" \
    --arg memory_content "$memory_content" \
    --arg user_status "$user_status" \
    --arg user_path ".kimiflow/project/USER.md" \
    --arg user_content "$user_content" \
    --argjson memory_tokens "$memory_tokens" \
    --argjson user_tokens "$user_tokens" \
    --argjson budget "$budget" \
    --argjson user_budget "$user_budget" \
    --argjson learnings "$learning_hits" \
    --argjson facts "$fact_hits" \
    --argjson index_hits "$index_hits" \
    --argjson history_hits "$history_hits" \
    --arg index_status "$index_status" \
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
        user_profile: {
          path: $user_path,
          status: $user_status,
          tokens_estimate: $user_tokens,
          budget: $user_budget,
          content: $user_content
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
        },
        index: {
          path: ".kimiflow/project/RECALL.sqlite",
          status: $index_status,
          count: ($index_hits | length),
          hits: $index_hits
        },
        history: {
          path: ".kimiflow/project/RUN-HISTORY.json",
          status: (if ($history_hits | length) > 0 then "used" else "available_no_hits" end),
          count: ($history_hits | length),
          hits: $history_hits
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
    usage_hits="$(printf '%s\n' "$json" | jq '[.sources.learnings.hits[], .sources.index.hits[], .sources.history.hits[]]')"
    update_usage_metrics "$root" "$usage_hits"
  fi
  json_print "$json" "$pretty"
}

cmd_history() {
  local root="" query="" query_file="" pretty=0 max=10 write=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --query) shift; query="${1:-}" ;;
      --query-file) shift; query_file="${1:-}" ;;
      --max) shift; max="${1:-}" ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "history: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  if [ -n "$query_file" ]; then
    [ -f "$query_file" ] || die "query file not found: $query_file" 2
    query="$(sed -n '1,120p' "$query_file")"
  fi
  case "$max" in ''|*[!0-9]*) die "history --max must be a number" 2 ;; esac

  local project terms hits status out json_path md_path
  project="$root/.kimiflow/project"
  if [ -n "$query" ]; then
    terms="$(terms_json_from_query "$query")"
    hits="$(run_artifact_hits_json "$root" "$terms" "$max")"
  else
    query="recent"
    terms='[]'
    hits="$(run_artifact_rows_json "$root" | jq --argjson max "$max" '.[:$max] | map(del(.text))')"
  fi
  status="preview"
  if [ "$write" -eq 1 ]; then
    mkdir -p "$project"
    json_path="$project/RUN-HISTORY.json"
    md_path="$project/RUN-HISTORY.md"
    status="written"
  fi
  out="$(jq -n \
    --arg query "$query" \
    --argjson terms "$terms" \
    --arg status "$status" \
    --arg path ".kimiflow/project/RUN-HISTORY.json" \
    --arg markdown_path ".kimiflow/project/RUN-HISTORY.md" \
    --argjson hits "$hits" \
    --argjson written "$write" \
    '{
      schema_version: 1,
      status: $status,
      query: $query,
      query_terms: $terms,
      path: $path,
      markdown_path: $markdown_path,
      written: ($written == 1),
      hits: $hits
    }')"
  if [ "$write" -eq 1 ]; then
    printf '%s\n' "$out" | jq . > "$json_path"
    write_history_markdown "$md_path" "$out"
    update_usage_metrics "$root" "$hits"
  fi
  json_print "$out" "$pretty"
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

file_digest_json() {
  local file="$1"
  local algorithm digest sha256
  if command -v shasum >/dev/null 2>&1; then
    algorithm="sha256"
    digest="$(shasum -a 256 "$file" | awk '{print $1}')"
    sha256="$digest"
  elif command -v sha256sum >/dev/null 2>&1; then
    algorithm="sha256"
    digest="$(sha256sum "$file" | awk '{print $1}')"
    sha256="$digest"
  elif command -v cksum >/dev/null 2>&1; then
    algorithm="cksum"
    digest="$(cksum "$file" | awk '{print $1 ":" $2}')"
    sha256=""
  else
    algorithm="unavailable"
    digest=""
    sha256=""
  fi
  jq -nc --arg algorithm "$algorithm" --arg digest "$digest" --arg sha256 "$sha256" \
    '{algorithm: $algorithm, digest: $digest, sha256: $sha256}'
}

evidence_file_path() {
  local root="$1" ref="$2" ref_path
  ref_path="$(printf '%s' "$ref" | sed -E 's/:[0-9]+$//')"
  case "$ref_path" in
    /*) printf '%s' "$ref_path" ;;
    *) printf '%s/%s' "$root" "$ref_path" ;;
  esac
}

evidence_line_suffix() {
  printf '%s' "$1" | sed -nE 's/^.*(:[0-9]+)$/\1/p'
}

sanitize_evidence_ref() {
  local root="$1" ref="$2" path suffix
  case "$ref" in
    "NOT VERIFIED"|"OUTSIDE_REPO") printf '%s' "$ref"; return 0 ;;
  esac

  path="$(evidence_file_path "$root" "$ref")"
  suffix="$(evidence_line_suffix "$ref")"
  case "$path" in
    "$root"/*|"$root") printf '%s%s' "$(rel_path "$root" "$path")" "$suffix" ;;
    *) printf 'OUTSIDE_REPO' ;;
  esac
}

sanitize_evidence_json() {
  local root="$1" evidence_json="$2"
  local out='[]' ref safe
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    safe="$(sanitize_evidence_ref "$root" "$ref")"
    out="$(printf '%s\n' "$out" | jq --arg ref "$safe" '. + [$ref]')"
  done < <(printf '%s\n' "$evidence_json" | jq -r '.[]?')
  printf '%s\n' "$out" | jq -c .
}

evidence_fingerprints_json() {
  local root="$1" evidence_json="$2"
  local out='[]' ref path rel status digest_info sha digest algorithm
  while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    ref="$(sanitize_evidence_ref "$root" "$ref")"
    case "$ref" in
      "NOT VERIFIED")
        out="$(printf '%s\n' "$out" | jq \
          --arg ref "$ref" \
          '. + [{ref: $ref, path: $ref, sha256: "", digest: "", digest_algorithm: "none", status: "unverified"}]')"
        continue
        ;;
      "OUTSIDE_REPO")
        out="$(printf '%s\n' "$out" | jq \
          --arg ref "$ref" \
          '. + [{ref: $ref, path: $ref, sha256: "", digest: "", digest_algorithm: "none", status: "outside_root"}]')"
        continue
        ;;
    esac
    path="$(evidence_file_path "$root" "$ref")"
    rel="$(rel_path "$root" "$path")"
    status="missing"
    sha=""
    digest=""
    algorithm="none"
    if [ -f "$path" ]; then
      status="current"
      digest_info="$(file_digest_json "$path")"
      sha="$(printf '%s\n' "$digest_info" | jq -r '.sha256')"
      digest="$(printf '%s\n' "$digest_info" | jq -r '.digest')"
      algorithm="$(printf '%s\n' "$digest_info" | jq -r '.algorithm')"
      if [ -z "$digest" ]; then
        status="unverified"
      fi
    fi
    out="$(printf '%s\n' "$out" | jq \
      --arg ref "$ref" \
      --arg path "$rel" \
      --arg sha "$sha" \
      --arg digest "$digest" \
      --arg algorithm "$algorithm" \
      --arg status "$status" \
      '. + [{ref: $ref, path: $path, sha256: $sha, digest: $digest, digest_algorithm: $algorithm, status: $status}]')"
  done < <(printf '%s\n' "$evidence_json" | jq -r '.[]?')
  printf '%s\n' "$out" | jq -c .
}

quality_gate_json() {
  local kind="$1" summary="$2" evidence_json="$3"
  local lower words reasons='[]' security
  lower="$(printf '%s\n' "$summary" | tr '[:upper:]' '[:lower:]')"
  words="$(printf '%s\n' "$summary" | tr -cs '[:alnum:]_-' '\n' | awk 'length($0) > 0 {n++} END{print n+0}')"
  security="$(memory_security_json "$summary")"

  if [ "$words" -lt 7 ]; then
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["too_short"]')"
  fi
  if ! printf '%s\n' "$security" | jq -e '.ok == true' >/dev/null; then
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["security_scan_failed"]')"
  fi
  if printf '%s\n' "$lower" | grep -Eq '^(done|fixed|updated|changed|implemented|cleanup|misc|note|todo)[[:punct:][:space:]]*$|(^|[[:space:]])(various|several|stuff|things|something|some files)([[:space:]]|$)'; then
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["too_generic"]')"
  fi
  if [ "$(printf '%s\n' "$evidence_json" | jq 'length')" -eq 0 ] || printf '%s\n' "$evidence_json" | jq -e 'any(.[]; . == "NOT VERIFIED")' >/dev/null; then
    reasons="$(printf '%s\n' "$reasons" | jq '. + ["missing_verified_evidence"]')"
  fi

  case "$kind" in
    project_rule_confirmed)
      if ! printf '%s\n' "$lower" | grep -Eq '(rule|confirmed|every|must|always|convention|standard|should|regel|bestätigt|bestaetigt|muss|immer|jede|jedes|konvention)'; then
        reasons="$(printf '%s\n' "$reasons" | jq '. + ["project_rule_without_rule"]')"
      fi
      ;;
    trap_or_pitfall)
      if ! printf '%s\n' "$lower" | grep -Eq '(pitfall|trap|avoid|risk|do not|don'\''t|never|falle|risiko|vermeiden|nicht|niemals|achtung|surprise)'; then
        reasons="$(printf '%s\n' "$reasons" | jq '. + ["pitfall_without_avoidance"]')"
      fi
      ;;
    important_decision)
      if ! printf '%s\n' "$lower" | grep -Eq '(decision|decided|choose|chosen|keep|use|because|trade-off|instead|entscheidung|entschieden|bleibt|nutzen|beibehalten)'; then
        reasons="$(printf '%s\n' "$reasons" | jq '. + ["decision_without_decision"]')"
      fi
      ;;
  esac

  jq -n \
    --argjson reasons "$reasons" \
    --argjson words "$words" \
    --argjson security "$security" \
    '{
      ok: (($reasons | length) == 0),
      words: $words,
      reasons: $reasons,
      security: $security
    }'
}

rows_path_for_scope() {
  local root="$1" scope="$2"
  case "$scope" in
    user|profile) printf '%s/.kimiflow/project/USER.jsonl' "$root" ;;
    *) printf '%s/.kimiflow/project/LEARNINGS.jsonl' "$root" ;;
  esac
}

id_prefix_for_scope() {
  local scope="$1"
  case "$scope" in
    user|profile) printf 'user' ;;
    *) printf 'learn' ;;
  esac
}

append_learning_row() {
  local root="$1" kind="$2" scope="$3" topic="$4" summary="$5" evidence_json="$6" confidence="$7" sensitivity="$8" status="$9"
  local project learnings stored_evidence_json fingerprints_json source_commit id row security_scan id_prefix
  project="$root/.kimiflow/project"
  learnings="$(rows_path_for_scope "$root" "$scope")"
  mkdir -p "$project"
  security_scan="$(memory_security_json "$summary")"
  if [ "$status" = "current" ] && ! printf '%s\n' "$security_scan" | jq -e '.ok == true' >/dev/null; then
    printf 'memory-router: memory security gate closed: %s\n' "$(printf '%s\n' "$security_scan" | jq -r '.reasons | join(",")')" >&2
    return 1
  fi
  stored_evidence_json="$(sanitize_evidence_json "$root" "$evidence_json")"
  fingerprints_json="$(evidence_fingerprints_json "$root" "$stored_evidence_json")"
  if [ -f "$learnings" ]; then
    local existing_id
    existing_id="$(jq -Rsc -r \
      --arg kind "$kind" \
      --arg scope "$scope" \
      --arg topic "$topic" \
      --arg summary "$summary" \
      --argjson evidence "$stored_evidence_json" \
      --argjson fingerprints "$fingerprints_json" \
      '
        split("\n")
        | map(select(length > 0) | (fromjson? // empty))
        | map(select(
            (.kind // "") == $kind
            and (.scope // "") == $scope
            and (.topic // "") == $topic
            and (.summary // "") == $summary
            and ((.evidence // []) == $evidence)
            and ((.evidence_fingerprints // []) == $fingerprints)
            and ((.status // "current") == "current")
          ))
        | .[0].id // ""
      ' "$learnings")"
    if [ -n "$existing_id" ]; then
      printf '%s' "$existing_id"
      return 0
    fi
  fi
  source_commit="$(git -C "$root" rev-parse --short HEAD 2>/dev/null || printf 'NOT VERIFIED')"
  id_prefix="$(id_prefix_for_scope "$scope")"
  id="${id_prefix}_$(date -u +%Y%m%d)_$(slugify "$topic")_$$"
  if [ "$status" = "current" ] && [ -f "$learnings" ]; then
    local tmp superseded_at
    tmp="$(mktemp "${learnings}.tmp.XXXXXX")"
    superseded_at="$(date_now)"
    jq -Rsc -c \
      --arg kind "$kind" \
      --arg scope "$scope" \
      --arg topic "$topic" \
      --arg summary "$summary" \
      --argjson evidence "$stored_evidence_json" \
      --argjson fingerprints "$fingerprints_json" \
      --arg superseded_by "$id" \
      --arg superseded_at "$superseded_at" \
      '
        split("\n")
        | map(select(length > 0) | (fromjson? // empty))
        | map(
            if ((.status // "current") == "current"
              and (.kind // "") == $kind
              and (.scope // "") == $scope
              and (.topic // "") == $topic
              and (.summary // "") == $summary
              and ((.evidence // []) == $evidence)
              and ((.evidence_fingerprints // []) != $fingerprints))
            then . + {status: "superseded", superseded_by: $superseded_by, superseded_at: $superseded_at}
            else .
            end
          )
        | .[]
      ' "$learnings" > "$tmp"
    mv "$tmp" "$learnings"
  fi
  row="$(jq -nc \
    --arg id "$id" \
    --arg kind "$kind" \
    --arg scope "$scope" \
    --arg topic "$topic" \
    --arg summary "$summary" \
    --argjson evidence "$stored_evidence_json" \
    --argjson evidence_fingerprints "$fingerprints_json" \
    --argjson security_scan "$security_scan" \
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
      evidence_fingerprints: $evidence_fingerprints,
      security_scan: $security_scan,
      confidence: $confidence,
      sensitivity: $sensitivity,
      last_verified: $last_verified,
      source_commit: $source_commit,
      status: $status
    }')"
  printf '%s\n' "$row" >> "$learnings"
  printf '%s' "$id"
}

rel_path() {
  local root="$1" path="$2"
  case "$path" in
    "$root"/*) printf '%s' "${path#"$root"/}" ;;
    "$root") printf '.' ;;
    *) printf '%s' "$path" ;;
  esac
}

sql_quote() {
  printf "%s" "$1" | sed "s/'/''/g"
}

sqlite_available() {
  command -v sqlite3 >/dev/null 2>&1
}

fts_query_from_terms() {
  local terms="$1"
  printf '%s\n' "$terms" | jq -r '
    map(gsub("[^A-Za-z0-9_]"; ""))
    | map(select(length >= 3))
    | unique
    | map("\"" + . + "\"")
    | join(" OR ")
  '
}

insert_fts_row() {
  local db="$1" kind="$2" source="$3" title="$4" body="$5" ref="$6"
  sqlite3 "$db" "INSERT INTO recall_fts(kind, source, title, body, ref) VALUES('$(sql_quote "$kind")','$(sql_quote "$source")','$(sql_quote "$title")','$(sql_quote "$body")','$(sql_quote "$ref")');"
}

build_recall_index() {
  local root="$1" db="$2"
  sqlite_available || return 2
  local project memory user_memory learnings user_rows facts file rel body row id kind topic summary source title ref
  project="$root/.kimiflow/project"
  memory="$project/MEMORY.md"
  user_memory="$project/USER.md"
  learnings="$project/LEARNINGS.jsonl"
  user_rows="$project/USER.jsonl"
  facts="$project/FACTS.jsonl"
  mkdir -p "$project"

  sqlite3 "$db" <<'SQL'
DROP TABLE IF EXISTS recall_meta;
DROP TABLE IF EXISTS recall_fts;
CREATE TABLE recall_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIRTUAL TABLE recall_fts USING fts5(kind, source, title, body, ref);
SQL
  sqlite3 "$db" "INSERT INTO recall_meta(key, value) VALUES('updated_at','$(sql_quote "$(iso_now)")');"

  if [ -f "$memory" ]; then
    body="$(sed -n '1,180p' "$memory")"
    insert_fts_row "$db" "memory" ".kimiflow/project/MEMORY.md" "Project Memory" "$body" ".kimiflow/project/MEMORY.md"
  fi
  if [ -f "$user_memory" ]; then
    body="$(sed -n '1,180p' "$user_memory")"
    insert_fts_row "$db" "user_profile" ".kimiflow/project/USER.md" "User Profile" "$body" ".kimiflow/project/USER.md"
  fi

  if [ -f "$learnings" ]; then
    while IFS= read -r row; do
      [ -n "$row" ] || continue
      [ "$(printf '%s\n' "$row" | jq -r '.status // "current"')" = "current" ] || continue
      id="$(printf '%s\n' "$row" | jq -r '.id // ""')"
      kind="$(printf '%s\n' "$row" | jq -r '.kind // "learning"')"
      topic="$(printf '%s\n' "$row" | jq -r '.topic // "uncategorized"')"
      summary="$(printf '%s\n' "$row" | jq -r '.summary // ""')"
      ref="$(printf '%s\n' "$row" | jq -r '(.evidence // []) | .[0] // ""')"
      insert_fts_row "$db" "learning" ".kimiflow/project/LEARNINGS.jsonl" "$topic · $kind · $id" "$summary" "$ref"
    done < "$learnings"
  fi

  if [ -f "$user_rows" ]; then
    while IFS= read -r row; do
      [ -n "$row" ] || continue
      [ "$(printf '%s\n' "$row" | jq -r '.status // "current"')" = "current" ] || continue
      id="$(printf '%s\n' "$row" | jq -r '.id // ""')"
      topic="$(printf '%s\n' "$row" | jq -r '.topic // "profile"')"
      summary="$(printf '%s\n' "$row" | jq -r '.summary // ""')"
      ref="$(printf '%s\n' "$row" | jq -r '(.evidence // []) | .[0] // ""')"
      insert_fts_row "$db" "user_profile" ".kimiflow/project/USER.jsonl" "$topic · $id" "$summary" "$ref"
    done < "$user_rows"
  fi

  if [ -f "$facts" ]; then
    while IFS= read -r row; do
      [ -n "$row" ] || continue
      kind="$(printf '%s\n' "$row" | jq -r '.kind // "fact"')"
      title="$(printf '%s\n' "$row" | jq -r '(.area // "codebase") + " · " + (.path // "")')"
      summary="$(printf '%s\n' "$row" | jq -r '.summary // ""')"
      ref="$(printf '%s\n' "$row" | jq -r '(.path // "") + ":" + ((.line // 1) | tostring)')"
      insert_fts_row "$db" "fact" ".kimiflow/project/FACTS.jsonl" "$kind · $title" "$summary" "$ref"
    done < "$facts"
  fi

  if [ -d "$root/.kimiflow" ]; then
    while IFS= read -r file; do
      rel="$(rel_path "$root" "$file")"
      body="$(sed -n '1,180p' "$file")"
      title="$(basename "$(dirname "$file")") · $(basename "$file")"
      insert_fts_row "$db" "run_artifact" "$rel" "$title" "$body" "$rel"
    done < <(find "$root/.kimiflow" -path "$project" -prune -o -type f \( -name 'INTENT.md' -o -name 'PROBLEM.md' -o -name 'RESEARCH.md' -o -name 'DIAGNOSIS.md' -o -name 'PLAN.md' -o -name 'ACCEPTANCE.md' -o -name 'CODE-REVIEW.md' -o -name 'LEARNING-REVIEW.md' \) -print 2>/dev/null)
  fi
}

fts_hits_json() {
  local root="$1" terms="$2" max="$3"
  local db="$root/.kimiflow/project/RECALL.sqlite" query out
  if ! sqlite_available || [ ! -f "$db" ]; then
    jq -n '[]'
    return 0
  fi
  query="$(fts_query_from_terms "$terms")"
  if [ -z "$query" ]; then
    jq -n '[]'
    return 0
  fi
  out="$(sqlite3 -json "$db" "SELECT kind, source, title, ref, substr(body, 1, 420) AS summary FROM recall_fts WHERE recall_fts MATCH '$(sql_quote "$query")' LIMIT $max;" 2>/dev/null)" || {
    jq -n '[]'
    return 0
  }
  if [ -n "$out" ]; then
    printf '%s\n' "$out"
  else
    jq -n '[]'
  fi
}

resolve_run_dir() {
  local root="$1" run="$2"
  [ -n "$run" ] || die "run path required" 2
  case "$run" in
    /*) ;;
    *) run="$root/$run" ;;
  esac
  (cd "$run" 2>/dev/null && pwd) || die "run directory not found: $run" 2
}

first_substantive_tsv() {
  local file="$1"
  [ -f "$file" ] || return 1
  awk '
    {
      line = $0
      gsub(/\r/, "", line)
      sub(/^[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      if (line == "") next
      if (line ~ /^#{1,6}[[:space:]]/) next
      if (line ~ /^```/) next
      gsub(/[[:space:]]+/, " ", line)
      print NR "\t" line
      exit
    }
  ' "$file"
}

structured_learning_tsv() {
  local file="$1" kind="$2"
  [ -f "$file" ] || return 1
  awk -v kind="$kind" '
    function trim(s) {
      sub(/^[[:space:]]+/, "", s)
      sub(/[[:space:]]+$/, "", s)
      return s
    }
    function matches_kind(lower) {
      if (kind == "learned") {
        return lower ~ /^(what was learned|learned|learning|lesson learned|gelernt|was gelernt wurde|erkenntnis)[[:space:]]*:/
      }
      if (kind == "project_rule_confirmed") {
        return lower ~ /^(which project rule was confirmed|project rule confirmed|rule confirmed|confirmed rule|project rule|projektregel|bestaetigte regel)[[:space:]]*:/
      }
      if (kind == "trap_or_pitfall") {
        return lower ~ /^(which trap or pitfall appeared|pitfall|trap|risk|avoid|falle|risiko|achtung)[[:space:]]*:/
      }
      if (kind == "important_decision") {
        return lower ~ /^(which decision remains important|important decision|decision|decided|entscheidung|wichtige entscheidung)[[:space:]]*:/
      }
      return 0
    }
    {
      line = $0
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      line = trim(line)
      sub(/^[-*][[:space:]]+/, "", line)
      sub(/^>[[:space:]]+/, "", line)
      lower = tolower(line)
      if (matches_kind(lower)) {
        summary = line
        if (summary != "") {
          gsub(/[[:space:]]+/, " ", summary)
          print NR "\t" summary
          exit
        }
      }
    }
  ' "$file"
}

learning_summary_json() {
  local file="$1" kind="$2" row line summary source
  row="$(structured_learning_tsv "$file" "$kind" | head -n 1)"
  source="structured"
  if [ -z "$row" ]; then
    row="$(first_substantive_tsv "$file" | head -n 1)"
    source="fallback"
  fi
  [ -n "$row" ] || return 1
  line="$(printf '%s\n' "$row" | awk -F '\t' '{print $1}')"
  summary="$(printf '%s\n' "$row" | cut -f2- | cut -c1-320)"
  [ -n "$summary" ] || return 1
  jq -nc \
    --argjson line "$line" \
    --arg summary "$summary" \
    --arg source "$source" \
    '{line: $line, summary: $summary, source: $source}'
}

review_candidate_json() {
  local root="$1" run_dir="$2" question="$3" kind="$4" topic="$5"
  shift 5
  local file path summary_info summary summary_line summary_source rel evidence_json classification target sensitivity confidence quality
  for file in "$@"; do
    path="$run_dir/$file"
    [ -f "$path" ] || continue
    summary_info="$(learning_summary_json "$path" "$kind")" || continue
    summary="$(printf '%s\n' "$summary_info" | jq -r '.summary')"
    summary_line="$(printf '%s\n' "$summary_info" | jq -r '.line')"
    summary_source="$(printf '%s\n' "$summary_info" | jq -r '.source')"
    [ -n "$summary" ] || continue
    rel="$(rel_path "$root" "$path")"
    evidence_json="$(jq -nc --arg evidence "$rel:$summary_line" '[$evidence]')"
    classification="$(classify_text "$summary")"
    target="$(printf '%s\n' "$classification" | jq -r '.classification.target')"
    sensitivity="$(printf '%s\n' "$classification" | jq -r '.classification.sensitivity')"
    confidence="$(printf '%s\n' "$classification" | jq -r '.classification.confidence')"
    [ "$target" = "skip" ] && continue
    [ "$target" = "run_only" ] && target="project_memory"
    quality="$(quality_gate_json "$kind" "$summary" "$evidence_json")"
    jq -nc \
      --arg question "$question" \
      --arg kind "$kind" \
      --arg scope "project" \
      --arg topic "$topic" \
      --arg summary "$summary" \
      --argjson evidence "$evidence_json" \
      --arg source "$summary_source" \
      --arg target "$target" \
      --arg sensitivity "$sensitivity" \
      --arg confidence "$confidence" \
      --argjson quality "$quality" \
      '{
        question: $question,
        kind: $kind,
        scope: $scope,
        topic: $topic,
        summary: $summary,
        evidence: $evidence,
        extraction_source: $source,
        target: $target,
        sensitivity: $sensitivity,
        confidence: $confidence,
        quality: $quality
      }'
    return 0
  done
  return 1
}

write_bounded_memory() {
  local root="$1" budget="${KIMIFLOW_MEMORY_BUDGET:-900}"
  local project memory learnings body max_items words
  project="$root/.kimiflow/project"
  memory="$project/MEMORY.md"
  learnings="$project/LEARNINGS.jsonl"
  [ -f "$learnings" ] || return 0
  mkdir -p "$project"

  max_items=8
  while :; do
    body="$(jq -Rsc --argjson max "$max_items" '
      split("\n")
      | map(select(length > 0) | (fromjson? // empty))
      | map(select((.status // "current") == "current"))
      | map(select((.sensitivity // "normal") != "security" and (.sensitivity // "normal") != "private"))
      | reverse
      | .[:$max]
      | reverse
      | map("- [" + (.topic // "uncategorized") + " · " + (.kind // "learning") + "] " + ((.summary // "") | tostring | .[0:220]) + " (evidence: " + (((.evidence // []) | .[0] // "NOT VERIFIED") | tostring) + ")")
      | join("\n")
    ' "$learnings")"
    {
      printf '# Project Memory\n\n'
      printf 'Generated: %s\n' "$(iso_now)"
      printf 'Policy: bounded always-on summary; raw/private/security learnings stay in LEARNINGS.jsonl and are recalled on demand.\n\n'
      printf '## Always-On Learnings\n\n'
      if [ -n "$body" ]; then
        printf '%s\n' "$body"
      else
        printf 'No publish-safe always-on learnings yet. Use LEARNINGS.jsonl recall on demand.\n'
      fi
    } > "$memory"
    words="$(word_count_file "$memory")"
    [ "$words" -le "$budget" ] && break
    [ "$max_items" -le 2 ] && break
    max_items=$((max_items - 2))
  done
}

write_bounded_user_memory() {
  local root="$1" budget="${KIMIFLOW_USER_MEMORY_BUDGET:-500}"
  local project memory rows body max_items words
  project="$root/.kimiflow/project"
  memory="$project/USER.md"
  rows="$project/USER.jsonl"
  [ -f "$rows" ] || return 0
  mkdir -p "$project"

  max_items=8
  while :; do
    body="$(jq -Rsc --argjson max "$max_items" '
      split("\n")
      | map(select(length > 0) | (fromjson? // empty))
      | map(select((.status // "current") == "current"))
      | map(select((.sensitivity // "normal") != "security"))
      | reverse
      | .[:$max]
      | reverse
      | map("- [" + (.topic // "profile") + "] " + ((.summary // "") | tostring | .[0:220]) + " (evidence: " + (((.evidence // []) | .[0] // "NOT VERIFIED") | tostring) + ")")
      | join("\n")
    ' "$rows")"
    {
      printf '# User Profile\n\n'
      printf 'Generated: %s\n' "$(iso_now)"
      printf 'Policy: local-only user/workflow preferences; never publish to repo docs.\n\n'
      printf '## Always-On User Notes\n\n'
      if [ -n "$body" ]; then
        printf '%s\n' "$body"
      else
        printf 'No user-profile notes yet.\n'
      fi
    } > "$memory"
    words="$(word_count_file "$memory")"
    [ "$words" -le "$budget" ] && break
    [ "$max_items" -le 2 ] && break
    max_items=$((max_items - 2))
  done
}

write_learning_review_markdown() {
  local path="$1" run_rel="$2" status="$3" entries="$4" skip_reason="$5"
  mkdir -p "$(dirname "$path")"
  {
    printf '# Learning Review\n\n'
    printf 'Run: %s\n' "$run_rel"
    printf 'Status: %s\n' "$status"
    printf 'Generated: %s\n\n' "$(iso_now)"
    if [ "$status" = "skipped" ]; then
      printf 'Skip reason: %s\n' "$skip_reason"
    else
      printf '## Four Questions\n\n'
      printf '%s\n' "$entries" | jq -r '
        .[] |
        "### " + .question + "\n" +
        "Summary: " + (.summary // "") + "\n" +
        "Kind: " + (.kind // "") + "\n" +
        "Target: " + (.target // "") + "\n" +
        "Sensitivity: " + (.sensitivity // "") + "\n" +
        "Quality: " + (if (.quality.ok // false) then "passed" else "failed:" + (((.quality.reasons // []) | join(","))) end) + "\n" +
        "Evidence:\n" + (((.evidence // []) | map("- " + .) | join("\n"))) + "\n" +
        "Recorded: " + (.recorded_id // "pending") + "\n"
      '
    fi
  } > "$path"
}

cmd_review_run() {
  local root="" run="" pretty=0 write=0 skip_reason=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --run) shift; run="${1:-}" ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --skip) shift; skip_reason="${1:-}" ;;
      --help|-h) usage; exit 0 ;;
      *) die "review-run: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  local run_dir run_rel review candidate entries recorded count i entry kind scope topic summary evidence_json confidence sensitivity id out memory_updated proposal_update notification
  run_dir="$(resolve_run_dir "$root" "$run")"
  run_rel="$(rel_path "$root" "$run_dir")"
  review="$run_dir/LEARNING-REVIEW.md"
  memory_updated=false
  proposal_update='{}'
  notification='{}'

  if [ -n "$skip_reason" ]; then
    if [ "$write" -eq 1 ]; then
      write_learning_review_markdown "$review" "$run_rel" "skipped" "[]" "$skip_reason"
    fi
    out="$(jq -n \
      --arg run "$run_rel" \
      --arg review_path "$(rel_path "$root" "$review")" \
      --arg reason "$skip_reason" \
      --argjson written "$write" \
      '{
        schema_version: 1,
        status: "skipped",
        run: $run,
        review_path: $review_path,
        skip_reason: $reason,
        written: ($written == 1),
        entries: [],
        recorded_count: 0,
        memory_updated: false
      }')"
    json_print "$out" "$pretty"
    return 0
  fi

  entries='[]'
  candidate="$(review_candidate_json "$root" "$run_dir" "what_was_learned" "learned" "run-learning" RESEARCH.md DIAGNOSIS.md VERIFICATION.md)" \
    && entries="$(printf '%s\n' "$entries" | jq --argjson item "$candidate" '. + [$item]')"
  candidate="$(review_candidate_json "$root" "$run_dir" "which_project_rule_was_confirmed" "project_rule_confirmed" "project-rules" ACCEPTANCE.md STANDARDS.md PLAN.md)" \
    && entries="$(printf '%s\n' "$entries" | jq --argjson item "$candidate" '. + [$item]')"
  candidate="$(review_candidate_json "$root" "$run_dir" "which_trap_or_pitfall_appeared" "trap_or_pitfall" "pitfalls" CODE-REVIEW.md ADVISORIES.md CURRENT-STATE.md)" \
    && entries="$(printf '%s\n' "$entries" | jq --argjson item "$candidate" '. + [$item]')"
  candidate="$(review_candidate_json "$root" "$run_dir" "which_decision_remains_important" "important_decision" "decisions" PLAN.md RESEARCH.md DIAGNOSIS.md)" \
    && entries="$(printf '%s\n' "$entries" | jq --argjson item "$candidate" '. + [$item]')"

  count="$(printf '%s\n' "$entries" | jq 'length')"
  [ "$count" -gt 0 ] || die "review-run found no reusable learning candidates; pass --skip <reason> if this run is intentionally trivial" 1

  local quality_failures quality_summary
  quality_failures="$(printf '%s\n' "$entries" | jq '[.[] | select((.quality.ok // false) != true)]')"
  if [ "$(printf '%s\n' "$quality_failures" | jq 'length')" -gt 0 ]; then
    quality_summary="$(printf '%s\n' "$quality_failures" | jq -r 'map(.question + ":" + ((.quality.reasons // []) | join(","))) | join(";")')"
    die "review-run quality gate closed: $quality_summary" 1
  fi

  if [ "$write" -eq 1 ]; then
    recorded='[]'
    i=0
    while [ "$i" -lt "$count" ]; do
      entry="$(printf '%s\n' "$entries" | jq -c ".[$i]")"
      kind="$(printf '%s\n' "$entry" | jq -r '.kind')"
      scope="$(printf '%s\n' "$entry" | jq -r '.scope')"
      topic="$(printf '%s\n' "$entry" | jq -r '.topic')"
      summary="$(printf '%s\n' "$entry" | jq -r '.summary')"
      evidence_json="$(printf '%s\n' "$entry" | jq -c '.evidence')"
      confidence="$(printf '%s\n' "$entry" | jq -r '.confidence')"
      sensitivity="$(printf '%s\n' "$entry" | jq -r '.sensitivity')"
      id="$(append_learning_row "$root" "$kind" "$scope" "$topic" "$summary" "$evidence_json" "$confidence" "$sensitivity" "current")" || return 1
      entry="$(printf '%s\n' "$entry" | jq --arg id "$id" '. + {recorded_id: $id}')"
      recorded="$(printf '%s\n' "$recorded" | jq --argjson item "$entry" '. + [$item]')"
      i=$((i + 1))
    done
    entries="$recorded"
    write_bounded_memory "$root"
    memory_updated=true
    cmd_curate --root "$root" --write >/dev/null
    cmd_index --root "$root" --write >/dev/null 2>&1 || true
    proposal_update="$(cmd_propose --root "$root" --write)"
    notification="$(printf '%s\n' "$proposal_update" | jq -c '.notification // {}')"
    write_learning_review_markdown "$review" "$run_rel" "recorded" "$entries" ""
  fi

  out="$(jq -n \
    --arg run "$run_rel" \
    --arg review_path "$(rel_path "$root" "$review")" \
    --argjson entries "$entries" \
    --argjson written "$write" \
    --argjson memory_updated "$memory_updated" \
    --argjson proposal_update "$proposal_update" \
    --argjson notification "$notification" \
    '{
      schema_version: 1,
      status: (if $written == 1 then "recorded" else "preview" end),
      run: $run,
      review_path: $review_path,
      written: ($written == 1),
      entries: $entries,
      recorded_count: ($entries | map(select(.recorded_id != null)) | length),
      memory_updated: $memory_updated,
      proposal_update: $proposal_update,
      notification: $notification
    }')"
  json_print "$out" "$pretty"
}

cmd_verify_run() {
  local root="" run=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --run) shift; run="${1:-}" ;;
      --help|-h) usage; exit 0 ;;
      *) die "verify-run: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  local run_dir review status reason learnings ids_json ids_count missing_ids missing_csv freshness_failures freshness_csv id row stored_fingerprints current_fingerprints evidence_json
  run_dir="$(resolve_run_dir "$root" "$run")"
  review="$run_dir/LEARNING-REVIEW.md"
  if [ ! -f "$review" ]; then
    printf 'LEARNING_REVIEW\tCLOSED\treason=missing_review\tpath=%s\n' "$(rel_path "$root" "$review")"
    return 1
  fi
  status="$(awk -F': ' '/^Status:/ {print $2; exit}' "$review")"
  case "$status" in
    recorded)
      ids_json="$(awk '/^Recorded:[[:space:]]+learn_/ {print $2}' "$review" | jq -R . | jq -s .)"
      ids_count="$(printf '%s\n' "$ids_json" | jq 'length')"
      if [ "$ids_count" -eq 0 ]; then
        printf 'LEARNING_REVIEW\tCLOSED\treason=missing_recorded_ids\tpath=%s\n' "$(rel_path "$root" "$review")"
        return 1
      fi
      learnings="$root/.kimiflow/project/LEARNINGS.jsonl"
      if [ ! -f "$learnings" ]; then
        printf 'LEARNING_REVIEW\tCLOSED\treason=missing_learnings\tpath=%s\n' "$(rel_path "$root" "$review")"
        return 1
      fi
      missing_ids="$(jq -Rsc --argjson ids "$ids_json" '
        (
          split("\n")
          | map(select(length > 0) | (fromjson? // empty))
          | map(select((.status // "current") == "current") | .id)
        ) as $current
        | [$ids[] | . as $id | select(($current | index($id)) == null)]
      ' "$learnings")"
      if [ "$(printf '%s\n' "$missing_ids" | jq 'length')" -eq 0 ]; then
        freshness_failures='[]'
        while IFS= read -r id; do
          [ -n "$id" ] || continue
          row="$(jq -Rsc -c --arg id "$id" '
            split("\n")
            | map(select(length > 0) | (fromjson? // empty))
            | map(select((.status // "current") == "current" and (.id // "") == $id))
            | .[0] // {}
          ' "$learnings")"
          evidence_json="$(printf '%s\n' "$row" | jq -c '.evidence // []')"
          stored_fingerprints="$(printf '%s\n' "$row" | jq -c '.evidence_fingerprints // []')"
          if [ "$(printf '%s\n' "$stored_fingerprints" | jq 'length')" -eq 0 ]; then
            freshness_failures="$(printf '%s\n' "$freshness_failures" | jq --arg id "$id" '. + [{id: $id, reason: "missing_evidence_fingerprints"}]')"
            continue
          fi
          current_fingerprints="$(evidence_fingerprints_json "$root" "$evidence_json")"
          if [ "$stored_fingerprints" != "$current_fingerprints" ]; then
            freshness_failures="$(printf '%s\n' "$freshness_failures" | jq --arg id "$id" '. + [{id: $id, reason: "evidence_changed_or_missing"}]')"
          fi
        done < <(printf '%s\n' "$ids_json" | jq -r '.[]')
        if [ "$(printf '%s\n' "$freshness_failures" | jq 'length')" -eq 0 ]; then
          printf 'LEARNING_REVIEW\tOPEN\tstatus=recorded\tfreshness=current\tpath=%s\n' "$(rel_path "$root" "$review")"
          return 0
        fi
        freshness_csv="$(printf '%s\n' "$freshness_failures" | jq -r 'map(.id + ":" + .reason) | join(",")')"
        printf 'LEARNING_REVIEW\tCLOSED\treason=evidence_stale\tids=%s\tpath=%s\n' "$freshness_csv" "$(rel_path "$root" "$review")"
        return 1
      fi
      missing_csv="$(printf '%s\n' "$missing_ids" | jq -r 'join(",")')"
      printf 'LEARNING_REVIEW\tCLOSED\treason=recorded_ids_missing_or_not_current\tids=%s\tpath=%s\n' "$missing_csv" "$(rel_path "$root" "$review")"
      return 1
      ;;
    skipped)
      reason="$(awk -F': ' '/^Skip reason:/ {print $2; exit}' "$review")"
      if [ -n "$reason" ]; then
        printf 'LEARNING_REVIEW\tOPEN\tstatus=skipped\treason=%s\tpath=%s\n' "$reason" "$(rel_path "$root" "$review")"
        return 0
      fi
      printf 'LEARNING_REVIEW\tCLOSED\treason=missing_skip_reason\tpath=%s\n' "$(rel_path "$root" "$review")"
      return 1
      ;;
    *)
      printf 'LEARNING_REVIEW\tCLOSED\treason=invalid_status\tstatus=%s\tpath=%s\n' "${status:-missing}" "$(rel_path "$root" "$review")"
      return 1
      ;;
  esac
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

  local id path
  id="$(append_learning_row "$root" "$kind" "$scope" "$topic" "$summary" "$evidence_json" "$confidence" "$sensitivity" "$status")" || return 1
  if [ "$scope" = "user" ] || [ "$scope" = "profile" ]; then
    write_bounded_user_memory "$root"
  else
    write_bounded_memory "$root"
  fi
  cmd_curate --root "$root" --write >/dev/null
  path="$(rel_path "$root" "$(rows_path_for_scope "$root" "$scope")")"
  printf 'RECORDED\t%s\t%s\n' "$path" "$id"
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

current_evidence_backed_rows() {
  local file="$1"
  jsonl_rows "$file" | jq '
    map(select((.status // "current") == "current"))
    | map(select((.sensitivity // "normal") != "security"))
    | map(select((.evidence // []) | length > 0))
    | map(select(((.evidence // []) | any(. == "NOT VERIFIED" or . == "OUTSIDE_REPO")) | not))
  '
}

proposal_candidates_json() {
  local rows="$1" state="$2" now="$3"
  jq -n \
    --argjson rows "$rows" \
    --argjson state "$state" \
    --arg now "$now" \
    '
      def previous($id):
        ($state | map(select((.id // "") == $id)) | .[-1] // {});
      def proposal_type($kind):
        if $kind == "project_rule_confirmed" then "standard"
        elif $kind == "important_decision" then "decision"
        else "skill"
        end;
      def target_path($type):
        if $type == "standard" then ".kimiflow/STANDARDS.md"
        elif $type == "decision" then ".kimiflow/DECISIONS.md"
        else ".kimiflow/project/PENDING-PROPOSALS.md"
        end;
      $rows
      | map(select((.kind // "") == "project_rule_confirmed" or (.kind // "") == "important_decision" or (.kind // "") == "learned" or (.kind // "") == "trap_or_pitfall"))
      | map(
          . as $row
          | ($row.id // "") as $id
          | previous($id) as $prev
          | (proposal_type($row.kind // "")) as $type
          | {
              id: $id,
              learning_id: $id,
              type: $type,
              kind: ($row.kind // "learning"),
              target_path: target_path($type),
              summary: ($row.summary // ""),
              evidence: ($row.evidence // []),
              evidence_fingerprints: ($row.evidence_fingerprints // []),
              status: ($prev.status // "pending"),
              reason: ($prev.reason // ""),
              created_at: ($prev.created_at // $now),
              updated_at: ($prev.updated_at // $now)
            }
            + (if (($prev.applied_at // "") | length) > 0 then {applied_at: $prev.applied_at} else {} end)
            + (if (($prev.apply_note // "") | length) > 0 then {apply_note: $prev.apply_note} else {} end)
            + (if (($prev.skill_draft_path // "") | length) > 0 then {skill_draft_path: $prev.skill_draft_path} else {} end)
        )
    '
}

proposal_freshness_failures_json() {
  local root="$1" proposals="$2"
  local failures='[]' prop id evidence_json stored_fingerprints current_fingerprints reason
  while IFS= read -r prop; do
    [ -n "$prop" ] || continue
    id="$(printf '%s\n' "$prop" | jq -r '.id // ""')"
    evidence_json="$(printf '%s\n' "$prop" | jq -c '.evidence // []')"
    stored_fingerprints="$(printf '%s\n' "$prop" | jq -c '.evidence_fingerprints // []')"
    reason=""
    if [ "$(printf '%s\n' "$stored_fingerprints" | jq 'length')" -eq 0 ]; then
      reason="missing_evidence_fingerprints"
    else
      current_fingerprints="$(evidence_fingerprints_json "$root" "$evidence_json")"
      if [ "$stored_fingerprints" != "$current_fingerprints" ]; then
        reason="evidence_changed_or_missing"
      fi
    fi
    if [ -n "$reason" ]; then
      failures="$(printf '%s\n' "$failures" | jq \
        --arg id "$id" \
        --arg reason "$reason" \
        '. + [{id: $id, reason: $reason}]')"
    fi
  done < <(printf '%s\n' "$proposals" | jq -c '.[]')
  printf '%s\n' "$failures"
}

mark_proposals_need_revalidation() {
  local proposals="$1" failures="$2" now="$3"
  printf '%s\n' "$proposals" | jq \
    --argjson failures "$failures" \
    --arg now "$now" \
    '($failures | map(.id)) as $ids
    | ($failures | map({key: .id, value: .reason}) | from_entries) as $reasons
    | map(
        if (.id as $id | $ids | index($id)) then
          . + {
            status: "needs_revalidation",
            reason: ($reasons[.id] // "evidence_changed_or_missing"),
            updated_at: $now
          }
        else .
        end
      )'
}

proposal_counts_json() {
  local proposals="$1"
  printf '%s\n' "$proposals" | jq '{
    total: length,
    pending: (map(select((.status // "pending") == "pending")) | length),
    approved: (map(select((.status // "") == "approved")) | length),
    applied: (map(select((.status // "") == "applied")) | length),
    rejected: (map(select((.status // "") == "rejected")) | length),
    needs_revalidation: (map(select((.status // "") == "needs_revalidation")) | length),
    by_type: (reduce .[] as $row ({}; ($row.type // "unknown") as $type | .[$type] = ((.[$type] // 0) + 1)))
  }'
}

proposal_notification_json() {
  local proposals="$1"
  local counts
  counts="$(proposal_counts_json "$proposals")"
  jq -n \
    --arg path ".kimiflow/project/PENDING-PROPOSALS.md" \
    --arg state_path ".kimiflow/project/PROPOSALS.jsonl" \
    --argjson counts "$counts" \
    '{
      kind: "learning_proposals",
      path: $path,
      state_path: $state_path,
      pending: $counts.pending,
      approved: $counts.approved,
      applied: $counts.applied,
      rejected: $counts.rejected,
      needs_revalidation: $counts.needs_revalidation,
      message: (
        "Learning proposals: "
        + ($counts.pending | tostring) + " pending, "
        + ($counts.approved | tostring) + " approved, "
        + ($counts.applied | tostring) + " applied, "
        + ($counts.rejected | tostring) + " rejected, "
        + ($counts.needs_revalidation | tostring) + " need revalidation."
      )
    }'
}

write_proposals_state() {
  local path="$1" proposals="$2"
  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$proposals" | jq -c '.[]' > "$path"
}

write_proposals_markdown() {
  local path="$1" proposals="$2"
  mkdir -p "$(dirname "$path")"
  {
    printf '# Pending Kimiflow Proposals\n\n'
    printf 'Generated: %s\n' "$(iso_now)"
    printf 'Policy: review-only proposals derived from current, evidence-backed local learnings. Standards and decisions may be applied to local `.kimiflow/` files after approval. Skill/workflow candidates remain manual-review only.\n\n'
    printf 'Commands:\n\n'
    printf -- '- Approve: `memory-router.sh propose --approve <id>`\n'
    printf -- '- Reject: `memory-router.sh propose --reject <id> --reason "<why>"`\n'
    printf -- '- Apply approved standards/decisions: `memory-router.sh propose --apply`\n\n'
    printf '## Standards Candidates\n\n'
    printf '%s\n' "$proposals" | jq -r '
      map(select((.type // "") == "standard"))
      | if length == 0 then "No candidates." else map("- [" + (.status // "pending") + "] " + (.summary // "") + " (id: " + (.id // "") + "; evidence: " + (((.evidence // []) | join(", "))) + ")") | join("\n") end
    '
    printf '\n## Decision Candidates\n\n'
    printf '%s\n' "$proposals" | jq -r '
      map(select((.type // "") == "decision"))
      | if length == 0 then "No candidates." else map("- [" + (.status // "pending") + "] " + (.summary // "") + " (id: " + (.id // "") + "; evidence: " + (((.evidence // []) | join(", "))) + ")") | join("\n") end
    '
    printf '\n## Skill/Workflow Candidates\n\n'
    printf '%s\n' "$proposals" | jq -r '
      map(select((.type // "") == "skill"))
      | if length == 0 then "No candidates." else map("- [" + (.status // "pending") + "] " + (.summary // "") + " (id: " + (.id // "") + "; evidence: " + (((.evidence // []) | join(", "))) + (if ((.skill_draft_path // "") | length) > 0 then "; draft: " + .skill_draft_path else "" end) + ")") | join("\n") end
    '
  } > "$path"
}

append_project_line() {
  local file="$1" title="$2" summary="$3" line="$4"
  mkdir -p "$(dirname "$file")"
  if [ ! -f "$file" ]; then
    printf '# %s\n\n' "$title" > "$file"
  fi
  if grep -Fq -- "$summary" "$file" 2>/dev/null; then
    return 1
  fi
  printf '%s\n' "$line" >> "$file"
}

write_skill_draft() {
  local root="$1" prop="$2"
  local id summary evidence draft_dir draft_file rel_file
  id="$(printf '%s\n' "$prop" | jq -r '.id')"
  summary="$(printf '%s\n' "$prop" | jq -r '.summary')"
  evidence="$(printf '%s\n' "$prop" | jq -r '(.evidence // []) | join(", ")')"
  draft_dir="$root/.kimiflow/project/SKILL-DRAFTS"
  draft_file="$draft_dir/$id.md"
  rel_file="$(rel_path "$root" "$draft_file")"
  mkdir -p "$draft_dir"
  {
    printf '# Skill Draft: %s\n\n' "$id"
    printf 'Generated: %s\n' "$(iso_now)"
    printf 'Status: review-only\n'
    printf 'Source learning: %s\n' "$id"
    printf 'Evidence: %s\n\n' "$evidence"
    printf '## Candidate Behavior\n\n'
    printf '%s\n\n' "$summary"
    printf '## Review Instructions\n\n'
    printf -- '- Verify the evidence is still current before editing any skill file.\n'
    printf -- '- Keep the skill change small and specific to the repeated workflow lesson.\n'
    printf -- '- Do not publish private, security, or local-path details.\n'
  } > "$draft_file"
  printf '%s' "$rel_file"
}

apply_approved_proposals() {
  local root="$1" proposals="$2"
  local standards="$root/.kimiflow/STANDARDS.md" decisions="$root/.kimiflow/DECISIONS.md"
  local applied='[]' manual='[]' skill_drafts='[]' appended_standards=0 appended_decisions=0 prop id type summary evidence line draft_path
  while IFS= read -r prop; do
    [ -n "$prop" ] || continue
    id="$(printf '%s\n' "$prop" | jq -r '.id')"
    type="$(printf '%s\n' "$prop" | jq -r '.type')"
    summary="$(printf '%s\n' "$prop" | jq -r '.summary')"
    evidence="$(printf '%s\n' "$prop" | jq -r '(.evidence // []) | join(", ")')"
    case "$type" in
      standard)
        line="- $summary (evidence: $evidence; learning: $id)"
        if append_project_line "$standards" "Kimiflow Standards" "$summary" "$line"; then
          appended_standards=$((appended_standards + 1))
        fi
        applied="$(printf '%s\n' "$applied" | jq --arg id "$id" '. + [$id]')"
        ;;
      decision)
        line="- $(date_now): $summary (evidence: $evidence; learning: $id)"
        if append_project_line "$decisions" "Kimiflow Decisions" "$summary" "$line"; then
          appended_decisions=$((appended_decisions + 1))
        fi
        applied="$(printf '%s\n' "$applied" | jq --arg id "$id" '. + [$id]')"
        ;;
      *)
        draft_path="$(write_skill_draft "$root" "$prop")"
        manual="$(printf '%s\n' "$manual" | jq --arg id "$id" '. + [$id]')"
        skill_drafts="$(printf '%s\n' "$skill_drafts" | jq --arg id "$id" --arg path "$draft_path" '. + [{id: $id, path: $path}]')"
        ;;
    esac
  done < <(printf '%s\n' "$proposals" | jq -c '.[] | select((.status // "") == "approved")')

  jq -n \
    --argjson applied_ids "$applied" \
    --argjson manual_ids "$manual" \
    --argjson skill_drafts "$skill_drafts" \
    --argjson appended_standards "$appended_standards" \
    --argjson appended_decisions "$appended_decisions" \
    '{
      applied_ids: $applied_ids,
      manual_ids: $manual_ids,
      skill_drafts: $skill_drafts,
      appended: {
        standards: $appended_standards,
        decisions: $appended_decisions
      }
    }'
}

cmd_propose() {
  local root="" pretty=0 write=0 apply=0 reason=""
  local approve_ids='[]' reject_ids='[]'
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --write) write=1 ;;
      --approve) shift; approve_ids="$(printf '%s\n' "$approve_ids" | jq --arg id "${1:-}" '. + [$id]')"; write=1 ;;
      --reject) shift; reject_ids="$(printf '%s\n' "$reject_ids" | jq --arg id "${1:-}" '. + [$id]')"; write=1 ;;
      --reason) shift; reason="${1:-}" ;;
      --apply) apply=1; write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "propose: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  local project learnings proposal_md proposal_state rows state proposals now counts out missing approve_count reject_count apply_result notification freshness_targets freshness_failures freshness_csv
  project="$root/.kimiflow/project"
  learnings="$project/LEARNINGS.jsonl"
  proposal_md="$project/PENDING-PROPOSALS.md"
  proposal_state="$project/PROPOSALS.jsonl"
  rows="$(current_evidence_backed_rows "$learnings")"
  state="$(jsonl_rows "$proposal_state")"
  now="$(iso_now)"
  proposals="$(proposal_candidates_json "$rows" "$state" "$now")"

  missing="$(jq -n --argjson ids "$approve_ids" --argjson proposals "$proposals" '($proposals | map(.id)) as $known | [$ids[] | select(($known | index(.)) == null)]')"
  [ "$(printf '%s\n' "$missing" | jq 'length')" -eq 0 ] || die "propose: unknown proposal id(s): $(printf '%s\n' "$missing" | jq -r 'join(",")')" 2
  missing="$(jq -n --argjson ids "$reject_ids" --argjson proposals "$proposals" '($proposals | map(.id)) as $known | [$ids[] | select(($known | index(.)) == null)]')"
  [ "$(printf '%s\n' "$missing" | jq 'length')" -eq 0 ] || die "propose: unknown proposal id(s): $(printf '%s\n' "$missing" | jq -r 'join(",")')" 2

  approve_count="$(printf '%s\n' "$approve_ids" | jq 'length')"
  reject_count="$(printf '%s\n' "$reject_ids" | jq 'length')"
  if [ "$approve_count" -gt 0 ]; then
    freshness_targets="$(printf '%s\n' "$proposals" | jq --argjson ids "$approve_ids" '[.[] | select(.id as $id | $ids | index($id))]')"
    freshness_failures="$(proposal_freshness_failures_json "$root" "$freshness_targets")"
    if [ "$(printf '%s\n' "$freshness_failures" | jq 'length')" -gt 0 ]; then
      proposals="$(mark_proposals_need_revalidation "$proposals" "$freshness_failures" "$now")"
      write_proposals_state "$proposal_state" "$proposals"
      write_proposals_markdown "$proposal_md" "$proposals"
      freshness_csv="$(printf '%s\n' "$freshness_failures" | jq -r 'map(.id + ":" + .reason) | join(",")')"
      die "propose: evidence stale; refresh learning review before approval: $freshness_csv" 1
    fi
    proposals="$(printf '%s\n' "$proposals" | jq --argjson ids "$approve_ids" --arg now "$now" '
      map(if (.id as $id | $ids | index($id)) then . + {status: "approved", reason: "", updated_at: $now} else . end)
    ')"
  fi
  if [ "$reject_count" -gt 0 ]; then
    proposals="$(printf '%s\n' "$proposals" | jq --argjson ids "$reject_ids" --arg reason "$reason" --arg now "$now" '
      map(if (.id as $id | $ids | index($id)) then . + {status: "rejected", reason: $reason, updated_at: $now} else . end)
    ')"
  fi

  apply_result='{"applied_ids":[],"manual_ids":[],"appended":{"standards":0,"decisions":0}}'
  if [ "$apply" -eq 1 ]; then
    freshness_targets="$(printf '%s\n' "$proposals" | jq '[.[] | select((.status // "") == "approved")]')"
    freshness_failures="$(proposal_freshness_failures_json "$root" "$freshness_targets")"
    if [ "$(printf '%s\n' "$freshness_failures" | jq 'length')" -gt 0 ]; then
      proposals="$(mark_proposals_need_revalidation "$proposals" "$freshness_failures" "$now")"
      write_proposals_state "$proposal_state" "$proposals"
      write_proposals_markdown "$proposal_md" "$proposals"
      freshness_csv="$(printf '%s\n' "$freshness_failures" | jq -r 'map(.id + ":" + .reason) | join(",")')"
      die "propose: evidence stale; refresh learning review before apply: $freshness_csv" 1
    fi
    apply_result="$(apply_approved_proposals "$root" "$proposals")"
    proposals="$(printf '%s\n' "$proposals" | jq \
      --argjson applied_ids "$(printf '%s\n' "$apply_result" | jq '.applied_ids')" \
      --argjson manual_ids "$(printf '%s\n' "$apply_result" | jq '.manual_ids')" \
      --argjson skill_drafts "$(printf '%s\n' "$apply_result" | jq '.skill_drafts')" \
      --arg now "$now" \
      '($skill_drafts | map({key: .id, value: .path}) | from_entries) as $draft_paths
      | map(
        if (.id as $id | $applied_ids | index($id)) then . + {status: "applied", applied_at: $now, updated_at: $now}
        elif (.id as $id | $manual_ids | index($id)) then . + {status: "approved", apply_note: "skill_draft_review", skill_draft_path: ($draft_paths[.id] // ""), updated_at: $now}
        else .
        end
      )')"
  fi

  if [ "$write" -eq 1 ]; then
    write_proposals_state "$proposal_state" "$proposals"
    write_proposals_markdown "$proposal_md" "$proposals"
  fi
  counts="$(proposal_counts_json "$proposals")"
  notification="$(proposal_notification_json "$proposals")"
  out="$(jq -n \
    --arg path ".kimiflow/project/PENDING-PROPOSALS.md" \
    --arg state_path ".kimiflow/project/PROPOSALS.jsonl" \
    --argjson written "$write" \
    --argjson apply "$apply" \
    --argjson counts "$counts" \
    --argjson apply_result "$apply_result" \
    --argjson notification "$notification" \
    '{
      schema_version: 1,
      status: (if $apply == 1 then "applied" elif $written == 1 then "written" else "preview" end),
      path: $path,
      state_path: $state_path,
      written: ($written == 1),
      proposals: $counts,
      apply_result: $apply_result,
      notification: $notification
    }')"
  json_print "$out" "$pretty"
}

cmd_consolidate() {
  local root="" pretty=0 write=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "consolidate: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  local project learnings archive rows superseded current duplicates out tmp
  project="$root/.kimiflow/project"
  learnings="$project/LEARNINGS.jsonl"
  archive="$project/LEARNINGS.archive.jsonl"
  rows="$(jsonl_rows "$learnings")"
  superseded="$(printf '%s\n' "$rows" | jq '[.[] | select((.status // "") == "superseded")]')"
  current="$(printf '%s\n' "$rows" | jq '[.[] | select((.status // "current") == "current")]')"
  duplicates="$(printf '%s\n' "$current" | jq '
    sort_by((.kind // "") + "|" + (.scope // "") + "|" + (.topic // "") + "|" + (.summary // ""))
    | group_by((.kind // "") + "|" + (.scope // "") + "|" + (.topic // "") + "|" + (.summary // ""))
    | map(select(length > 1) | {summary: (.[0].summary // ""), ids: map(.id)})
  ')"
  if [ "$write" -eq 1 ] && [ -f "$learnings" ]; then
    mkdir -p "$project"
    if [ "$(printf '%s\n' "$superseded" | jq 'length')" -gt 0 ]; then
      printf '%s\n' "$superseded" | jq -c '.[]' >> "$archive"
    fi
    tmp="$(mktemp "${learnings}.tmp.XXXXXX")"
    printf '%s\n' "$rows" | jq -c '.[] | select((.status // "") != "superseded")' > "$tmp"
    mv "$tmp" "$learnings"
    write_bounded_memory "$root"
    write_bounded_user_memory "$root"
    cmd_curate --root "$root" --write >/dev/null
    cmd_index --root "$root" --write >/dev/null 2>&1 || true
  fi
  out="$(jq -n \
    --arg archive ".kimiflow/project/LEARNINGS.archive.jsonl" \
    --argjson written "$write" \
    --argjson superseded_count "$(printf '%s\n' "$superseded" | jq 'length')" \
    --argjson current_count "$(printf '%s\n' "$current" | jq 'length')" \
    --argjson duplicates "$duplicates" \
    '{
      schema_version: 1,
      status: (if $written == 1 then "consolidated" else "preview" end),
      written: ($written == 1),
      archive_path: $archive,
      current_count: $current_count,
      archived_superseded_count: $superseded_count,
      duplicate_groups: $duplicates
    }')"
  json_print "$out" "$pretty"
}

cmd_index() {
  local root="" pretty=0 write=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "index: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  local project db status count out
  project="$root/.kimiflow/project"
  db="$project/RECALL.sqlite"
  status="preview"
  count=0
  if ! sqlite_available; then
    out="$(jq -n --arg path ".kimiflow/project/RECALL.sqlite" '{schema_version: 1, status: "unavailable", path: $path, sqlite_available: false, documents: 0}')"
    json_print "$out" "$pretty"
    return 0
  fi
  if [ "$write" -eq 1 ]; then
    build_recall_index "$root" "$db"
    status="indexed"
  elif [ -f "$db" ]; then
    status="available"
  fi
  if [ -f "$db" ]; then
    count="$(sqlite3 "$db" 'SELECT count(*) FROM recall_fts;' 2>/dev/null || printf '0')"
  fi
  out="$(jq -n \
    --arg path ".kimiflow/project/RECALL.sqlite" \
    --arg status "$status" \
    --argjson write "$write" \
    --argjson count "$count" \
    '{schema_version: 1, status: $status, path: $path, written: ($write == 1), sqlite_available: true, documents: $count}')"
  json_print "$out" "$pretty"
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

  local project memory learnings user_rows index usage_file provider_manifest status learning_summary user_summary usage_summary lifecycle provider vault existing_vault topics out
  project="$root/.kimiflow/project"
  memory="$project/MEMORY.md"
  learnings="$project/LEARNINGS.jsonl"
  user_rows="$project/USER.jsonl"
  index="$project/MEMORY-INDEX.json"
  usage_file="$project/MEMORY-USAGE.json"
  provider_manifest="$project/VAULT-PROVIDER.json"
  status="$(status_json "$root")"
  learning_summary="$(read_jsonl_summary "$learnings")"
  user_summary="$(read_jsonl_summary "$user_rows")"
  usage_summary="$(usage_summary_json "$usage_file")"
  lifecycle="$(learning_lifecycle_json "$learnings" "$usage_file")"
  provider="$(printf '%s\n' "$status" | jq -c '.provider')"
  vault="$(printf '%s\n' "$status" | jq -c '.vault')"
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
    --argjson user_profile "$user_summary" \
    --argjson usage "$usage_summary" \
    --argjson lifecycle "$lifecycle" \
    --argjson provider "$provider" \
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
      provider: $provider,
      learnings: $learnings,
      user_profile: $user_profile,
      usage: $usage,
      lifecycle: $lifecycle,
      topics: $topics,
      curation: $status.curation
    }')"

  if [ "$write" -eq 1 ]; then
    mkdir -p "$project"
    printf '%s\n' "$out" | jq . > "$index"
    cmd_index --root "$root" --write >/dev/null 2>&1 || true
  fi
  json_print "$out" "$pretty"
}

write_provider_prefetch_markdown() {
  local path="$1" json="$2"
  mkdir -p "$(dirname "$path")"
  {
    printf '# Vault Provider Prefetch\n\n'
    printf 'Generated: %s\n\n' "$(iso_now)"
    printf 'Provider: %s\n' "$(printf '%s\n' "$json" | jq -r '.provider.type')"
    printf 'Available: %s\n' "$(printf '%s\n' "$json" | jq -r '.provider.available')"
    printf 'Query: %s\n\n' "$(printf '%s\n' "$json" | jq -r '.query')"
    printf 'Use this as a bounded handoff for an Obsidian/Vault MCP search. Save only curated, publish-safe notes back through the provider.\n'
  } > "$path"
}

write_provider_sync_markdown() {
  local path="$1" json="$2"
  mkdir -p "$(dirname "$path")"
  {
    printf '# Vault Provider Sync\n\n'
    printf 'Generated: %s\n\n' "$(iso_now)"
    printf 'Provider: %s\n' "$(printf '%s\n' "$json" | jq -r '.provider.type')"
    printf 'Available: %s\n' "$(printf '%s\n' "$json" | jq -r '.provider.available')"
    printf 'Candidates exported: %s\n' "$(printf '%s\n' "$json" | jq -r '.candidates.exported_count // .candidates.count')"
    printf 'Total pending: %s\n' "$(printf '%s\n' "$json" | jq -r '.candidates.count')"
    printf 'Omitted: %s\n\n' "$(printf '%s\n' "$json" | jq -r '.candidates.omitted_count // 0')"
    printf 'Policy: review this bounded handoff before writing to the Vault. It includes only current, non-private, non-security learnings with verified repo-relative evidence. Remaining candidates stay pending for a later sync.\n\n'
    if printf '%s\n' "$json" | jq -e '(.candidates.exported_count // .candidates.count) == 0' >/dev/null 2>&1; then
      printf 'No new publish-safe learning candidates are pending for Vault sync.\n'
    else
      printf '## Candidates\n\n'
      printf '%s\n' "$json" | jq -r '
        .candidates.rows[]
        | "- [" + (.topic // "uncategorized") + " · " + (.kind // "learning") + " · " + (.id // "") + "] "
          + ((.summary // "") | tostring | gsub("\n"; " ") | .[0:260])
          + " (evidence: " + (((.evidence // []) | .[0] // "NOT VERIFIED") | tostring) + ")"
      '
    fi
  } > "$path"
}

cmd_provider() {
  local action="${1:-status}"
  [ "$#" -gt 0 ] && shift
  local root="" pretty=0 type="obsidian" available="" vault_path="" query="" write=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --type) shift; type="${1:-}" ;;
      --available) shift; available="${1:-}" ;;
      --path) shift; vault_path="${1:-}" ;;
      --query) shift; query="${1:-}" ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "provider: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  local project manifest provider out available_json now prefetch_path sync_path candidates export_candidates ids handoff sync_max total_count export_count omitted_count
  project="$root/.kimiflow/project"
  manifest="$project/VAULT-PROVIDER.json"
  now="$(iso_now)"

  case "$action" in
    status)
      out="$(provider_status_json "$manifest")"
      ;;
    configure)
      case "$available" in
        1|true|TRUE|yes|YES) available_json=true ;;
        0|false|FALSE|no|NO|"") available_json=false ;;
        *) die "provider configure --available must be true or false" 2 ;;
      esac
      out="$(jq -n \
        --arg type "$type" \
        --arg path "$vault_path" \
        --arg now "$now" \
        --argjson available "$available_json" \
        '{
          schema_version: 1,
          type: $type,
          available: $available,
          mode: "local-first",
          vault_path: $path,
          last_prefetch_at: null,
          last_write_at: null,
          synced_learning_ids: [],
          updated_at: $now
        }')"
      mkdir -p "$project"
      printf '%s\n' "$out" | jq . > "$manifest"
      out="$(provider_status_json "$manifest")"
      ;;
    prefetch)
      provider="$(provider_status_json "$manifest")"
      prefetch_path="$project/VAULT-PREFETCH.md"
      if ! printf '%s\n' "$provider" | jq -e '.available == true' >/dev/null 2>&1; then
        out="$(jq -n \
          --arg path ".kimiflow/project/VAULT-PREFETCH.md" \
          --argjson provider "$provider" \
          '{schema_version: 1, status: "skipped", reason: "provider_unavailable", path: $path, provider: $provider}')"
      else
        [ -n "$query" ] || query="project memory recall"
        out="$(jq -n \
          --arg query "$query" \
          --arg path ".kimiflow/project/VAULT-PREFETCH.md" \
          --argjson provider "$provider" \
          '{schema_version: 1, status: "prefetch_handoff", query: $query, path: $path, provider: $provider}')"
        if [ "$write" -eq 1 ]; then
          mkdir -p "$project"
          write_provider_prefetch_markdown "$prefetch_path" "$out"
          provider_manifest_json "$manifest" \
            | jq --arg now "$now" '.last_prefetch_at = $now | .updated_at = $now | .available = true' \
            > "$manifest.tmp" && mv "$manifest.tmp" "$manifest"
          out="$(printf '%s\n' "$out" | jq '.written = true')"
        fi
      fi
      ;;
    sync)
      provider="$(provider_status_json "$manifest")"
      sync_path="$project/VAULT-SYNC.md"
      if ! printf '%s\n' "$provider" | jq -e '.available == true' >/dev/null 2>&1; then
        out="$(jq -n \
          --arg path ".kimiflow/project/VAULT-SYNC.md" \
          --argjson provider "$provider" \
          '{schema_version: 1, status: "skipped", reason: "provider_unavailable", path: $path, provider: $provider, candidates: {count: 0, exported_count: 0, omitted_count: 0, ids: []}}')"
      else
        sync_max="${KIMIFLOW_PROVIDER_SYNC_MAX:-20}"
        case "$sync_max" in
          ''|*[!0-9]*) sync_max=20 ;;
        esac
        [ "$sync_max" -gt 0 ] || sync_max=20
        candidates="$(provider_sync_candidates_json "$root" "$project/LEARNINGS.jsonl" "$manifest")"
        total_count="$(printf '%s\n' "$candidates" | jq 'length')"
        export_candidates="$(printf '%s\n' "$candidates" | jq --argjson max "$sync_max" '.[0:$max]')"
        export_count="$(printf '%s\n' "$export_candidates" | jq 'length')"
        omitted_count=$((total_count - export_count))
        out="$(jq -n \
          --arg path ".kimiflow/project/VAULT-SYNC.md" \
          --argjson provider "$provider" \
          --argjson candidates "$candidates" \
          --argjson export_candidates "$export_candidates" \
          --argjson exported "$export_count" \
          --argjson omitted "$omitted_count" \
          '{
            schema_version: 1,
            status: "sync_handoff",
            path: $path,
            provider: $provider,
            candidates: {
              count: ($candidates | length),
              exported_count: $exported,
              omitted_count: $omitted,
              ids: ($export_candidates | map(.id))
            },
            written: false
          }')"
        if [ "$write" -eq 1 ]; then
          mkdir -p "$project"
          handoff="$(printf '%s\n' "$out" | jq --argjson candidates "$export_candidates" '.candidates.rows = $candidates')"
          write_provider_sync_markdown "$sync_path" "$handoff"
          ids="$(printf '%s\n' "$export_candidates" | jq -c 'map(.id)')"
          provider_manifest_json "$manifest" \
            | jq --arg now "$now" --argjson ids "$ids" \
                '.last_write_at = $now | .updated_at = $now | .available = true | .synced_learning_ids = (((.synced_learning_ids // []) + $ids) | unique)' \
            > "$manifest.tmp" && mv "$manifest.tmp" "$manifest"
          provider="$(provider_status_json "$manifest")"
          out="$(printf '%s\n' "$out" | jq --argjson provider "$provider" '.written = true | .provider = $provider')"
        fi
      fi
      ;;
    *)
      die "provider action must be status, configure, prefetch, or sync" 2
      ;;
  esac
  json_print "$out" "$pretty"
}

cmd="${1:-}"
[ -n "$cmd" ] || { usage; exit 2; }
shift

case "$cmd" in
  status) cmd_status "$@" ;;
  recall) cmd_recall "$@" ;;
  history) cmd_history "$@" ;;
  classify) cmd_classify "$@" ;;
  record) cmd_record "$@" ;;
  review-run) cmd_review_run "$@" ;;
  verify-run) cmd_verify_run "$@" ;;
  curate) cmd_curate "$@" ;;
  index) cmd_index "$@" ;;
  consolidate) cmd_consolidate "$@" ;;
  propose) cmd_propose "$@" ;;
  provider) cmd_provider "$@" ;;
  --help|-h|help) usage; exit 0 ;;
  *) die "unknown command: $cmd" 2 ;;
esac
