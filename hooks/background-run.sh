#!/usr/bin/env bash
# kimiflow — local Background Handles registry and collect gate.
#
# Orchestrator commands:
#   background-run.sh start --kind <kind> --title <text> --affected <path> [--root <path>] [--write] [--pretty]
#   background-run.sh list [--root <path>] [--json|--pretty]
#   background-run.sh status --id <id> [--root <path>] [--pretty]
#   background-run.sh update --id <id> --status <status> [--result <file>] [--files <file>] [--advisories <file>] [--verify <file>] [--reason <text>] [--root <path>] [--write] [--pretty]
#   background-run.sh collect --id <id> [--root <path>]
#   background-run.sh cancel|mark-stale --id <id> --reason <text> [--root <path>] [--write] [--pretty]
set -u

usage() {
  sed -n '1,12p' "$0" >&2
}

die() {
  printf 'background-run: %s\n' "$1" >&2
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
    (cd "$root" 2>/dev/null && pwd) || printf '%s' "$root"
  else
    git rev-parse --show-toplevel 2>/dev/null || pwd
  fi
}

git_head() {
  local root="$1"
  git -C "$root" rev-parse HEAD 2>/dev/null || printf 'NOT VERIFIED'
}

git_commit_ok() {
  local root="$1" commit="$2"
  [ -n "$commit" ] && [ "$commit" != "NOT VERIFIED" ] || return 1
  git -C "$root" cat-file -e "$commit^{commit}" >/dev/null 2>&1
}

background_dir() {
  printf '%s/.kimiflow/background\n' "$1"
}

handle_dir() {
  printf '%s/%s\n' "$(background_dir "$1")" "$2"
}

index_file() {
  printf '%s/HANDLES.jsonl\n' "$(background_dir "$1")"
}

valid_kind() {
  case "$1" in
    deep-codebase|docs|security|improve|custom) return 0 ;;
    *) return 1 ;;
  esac
}

valid_status() {
  case "$1" in
    pending|running|ready|finished|stale|failed|cancelled) return 0 ;;
    *) return 1 ;;
  esac
}

terminal_status() {
  case "$1" in
    stale|failed|cancelled) return 0 ;;
    *) return 1 ;;
  esac
}

validate_id() {
  [[ "$1" =~ ^bh_[A-Za-z0-9_-]+$ ]]
}

new_id() {
  local stamp rand
  stamp="$(date -u +"%Y%m%d_%H%M%S")"
  rand="$(od -An -N4 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')"
  [ -n "$rand" ] || rand="00000000"
  printf 'bh_%s_%s\n' "$stamp" "$rand"
}

normalize_affected_one() {
  local path="$1"
  path="${path#"${path%%[![:space:]]*}"}"
  path="${path%"${path##*[![:space:]]}"}"
  while :; do
    case "$path" in ./*) path="${path#./}" ;; *) break ;; esac
  done
  while :; do
    case "$path" in */) path="${path%/}" ;; *) break ;; esac
  done
  case "$path" in
    ""|"."|".."|/*|../*|*/../*|*/..|*/./*|*/.|*//*|.kimiflow|.kimiflow/*) return 1 ;;
  esac
  printf '%s\n' "$path"
}

affected_json_from_args() {
  local json='[]' path normalized
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    normalized="$(normalize_affected_one "$path")" || return 1
    json="$(printf '%s\n' "$json" | jq --arg path "$normalized" 'if index($path) then . else . + [$path] end')"
  done
  printf '%s\n' "$json"
}

normalize_affected_json() {
  local raw="$1" json='[]' path normalized
  if ! printf '%s\n' "$raw" | jq -e 'type == "array" and all(.[]; type == "string" and length > 0)' >/dev/null 2>&1; then
    return 1
  fi
  while IFS= read -r path; do
    [ -n "$path" ] || return 1
    normalized="$(normalize_affected_one "$path")" || return 1
    [ "$normalized" = "$path" ] || return 1
    json="$(printf '%s\n' "$json" | jq --arg path "$normalized" 'if index($path) then . else . + [$path] end')"
  done < <(printf '%s\n' "$raw" | jq -r '.[]')
  [ "$(printf '%s\n' "$json" | jq 'length')" -gt 0 ] || return 1
  printf '%s\n' "$json"
}

json_print() {
  local json="$1" pretty="$2"
  if [ "$pretty" -eq 1 ]; then
    printf '%s\n' "$json" | jq .
  else
    printf '%s\n' "$json" | jq -c .
  fi
}

write_index_event() {
  local root="$1" json="$2" file
  file="$(index_file "$root")"
  mkdir -p "$(dirname "$file")" || return 1
  printf '%s\n' "$json" | jq -c . >> "$file"
}

status_file_for_id() {
  local root="$1" id="$2" dir
  validate_id "$id" || die "unsafe handle id" 2
  dir="$(handle_dir "$root" "$id")"
  [ ! -L "$dir" ] || die "unsafe handle dir" 2
  printf '%s/STATUS.json\n' "$dir"
}

load_status() {
  local root="$1" id="$2" file
  file="$(status_file_for_id "$root" "$id")"
  [ ! -L "$file" ] || die "unsafe handle status: $id" 2
  [ -f "$file" ] || die "handle not found: $id" 1
  jq -e . "$file" 2>/dev/null || die "invalid handle status: $id" 1
}

changed_paths_json() {
  local root="$1" base="$2" json='[]' path
  if git_commit_ok "$root" "$base"; then
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      case "$path" in .kimiflow/*) continue ;; esac
      json="$(printf '%s\n' "$json" | jq --arg path "$path" 'if index($path) then . else . + [$path] end')"
    done < <(
      {
        git -C "$root" diff --name-only "$base"..HEAD 2>/dev/null
        git -C "$root" diff --name-only --cached 2>/dev/null
        git -C "$root" diff --name-only 2>/dev/null
        git -C "$root" ls-files --others --exclude-standard 2>/dev/null
      } | sort -u
    )
  fi
  printf '%s\n' "$json"
}

path_matches() {
  local changed="$1" affected="$2"
  case "$affected" in
    *"*"*|*"?"*) [[ "$changed" == $affected ]] ;;
    *)
      [ "$changed" = "$affected" ] && return 0
      case "$changed" in "$affected"/*) return 0 ;; esac
      return 1
      ;;
  esac
}

stale_matches_json() {
  local changed_json="$1" affected_json="$2" matches='[]' changed affected
  while IFS= read -r changed; do
    [ -n "$changed" ] || continue
    while IFS= read -r affected; do
      [ -n "$affected" ] || continue
      if path_matches "$changed" "$affected"; then
        matches="$(printf '%s\n' "$matches" | jq --arg path "$changed" 'if index($path) then . else . + [$path] end')"
      fi
    done < <(printf '%s\n' "$affected_json" | jq -r '.[]?')
  done < <(printf '%s\n' "$changed_json" | jq -r '.[]?')
  printf '%s\n' "$matches"
}

collect_line() {
  printf 'BACKGROUND_HANDLE\t%s\tid=%s\tstatus=%s\treason=%s\tdetail=%s\n' "$1" "$2" "$3" "$4" "${5:-}"
}

list_json() {
  local root="$1" dir items='[]' file item id status collect verdict reason detail
  dir="$(background_dir "$root")"
  if [ -d "$dir" ]; then
    while IFS= read -r file; do
      [ -n "$file" ] || continue
      item="$(jq -c 'select(type == "object")' "$file" 2>/dev/null || true)"
      [ -n "$item" ] || continue
      id="$(printf '%s\n' "$item" | jq -r '.id // ""')"
      status="$(printf '%s\n' "$item" | jq -r '.status // ""')"
      validate_id "$id" || continue
      valid_status "$status" || continue
      case "$status" in
        ready|finished)
          collect="$(collect_status "$root" "$id" 2>/dev/null || true)"
          if printf '%s\n' "$collect" | grep -q $'^BACKGROUND_HANDLE\t'; then
            verdict="$(printf '%s\n' "$collect" | awk -F '\t' '{print $2}')"
            reason="$(printf '%s\n' "$collect" | awk -F '\t' '{for (i=1;i<=NF;i++) if ($i ~ /^reason=/) {sub(/^reason=/, "", $i); print $i; exit}}')"
            detail="$(printf '%s\n' "$collect" | awk -F '\t' '{for (i=1;i<=NF;i++) if ($i ~ /^detail=/) {sub(/^detail=/, "", $i); print $i; exit}}')"
          else
            verdict="CLOSED"
            reason="status_invalid"
            detail=""
          fi
          item="$(printf '%s\n' "$item" | jq --arg verdict "$verdict" --arg reason "$reason" --arg detail "$detail" '. + {collect_verdict: $verdict, collect_reason: $reason, collect_detail: $detail}')"
          ;;
      esac
      items="$(printf '%s\n' "$items" | jq --argjson item "$item" '. + [$item]')"
    done < <(find "$dir" -mindepth 2 -maxdepth 2 -name STATUS.json -type f 2>/dev/null | sort)
  fi
  jq -n --argjson items "$items" '{
    schema_version: 1,
    present: (($items | length) > 0),
    path: ".kimiflow/background",
    total: ($items | length),
    pending: ($items | map(select(.status == "pending")) | length),
    running: ($items | map(select(.status == "running")) | length),
    ready: ($items | map(select(.status == "ready")) | length),
    finished: ($items | map(select(.status == "finished")) | length),
    collectable: ($items | map(select(.collect_verdict == "OPEN")) | length),
    stale: ($items | map(select(.status == "stale" or .collect_reason == "stale")) | length),
    failed: ($items | map(select(.status == "failed")) | length),
    cancelled: ($items | map(select(.status == "cancelled")) | length),
    items: $items
  }'
}

copy_to_temp() {
  local src="$1" tmp="$2"
  [ -n "$src" ] || return 0
  [ -f "$src" ] || { printf 'background-run: source file missing: %s\n' "$src" >&2; return 1; }
  [ -n "$tmp" ] || { printf 'background-run: cannot create temp file\n' >&2; return 1; }
  cp "$src" "$tmp" || { printf 'background-run: cannot copy %s\n' "$src" >&2; return 1; }
}

cleanup_temps() {
  local tmp
  for tmp in "$@"; do
    [ -n "$tmp" ] && rm -f "$tmp"
  done
}

install_temp() {
  local tmp="$1" dest="$2"
  [ -n "$tmp" ] || return 0
  if [ -L "$dest" ] || { [ -e "$dest" ] && [ ! -f "$dest" ]; }; then
    cleanup_temps "$tmp"
    die "unsafe handle file target: $dest" 1
  fi
  mv -f "$tmp" "$dest" || die "cannot install $dest" 1
}

ensure_regular_file_target() {
  local path="$1"
  if [ -L "$path" ] || { [ -e "$path" ] && [ ! -f "$path" ]; }; then
    die "unsafe handle file target: $path" 1
  fi
}

cmd_start() {
  local root="" kind="" title="" write=0 pretty=0 affected_lines="" affected_json id dir now base status handoff
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --kind) shift; kind="${1:-}" ;;
      --title) shift; title="${1:-}" ;;
      --affected) shift; affected_lines="${affected_lines}${1:-}"$'\n' ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "start: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  valid_kind "$kind" || die "invalid kind: $kind" 2
  [ -n "$title" ] || die "start requires --title" 2
  [ -n "$affected_lines" ] || die "start requires --affected" 2
  affected_json="$(printf '%s' "$affected_lines" | affected_json_from_args)" || die "unsafe affected path" 2
  [ "$(printf '%s\n' "$affected_json" | jq 'length')" -gt 0 ] || die "start requires affected paths" 2
  id="$(new_id)"
  dir="$(handle_dir "$root" "$id")"
  now="$(iso_now)"
  base="$(git_head "$root")"
  status="$(jq -n \
    --arg id "$id" --arg kind "$kind" --arg title "$title" --arg status "pending" \
    --arg now "$now" --arg base "$base" --arg handoff ".kimiflow/background/$id/HANDOFF.md" \
    --arg result ".kimiflow/background/$id/RESULT.md" --arg files ".kimiflow/background/$id/FILES.json" \
    --arg advisories ".kimiflow/background/$id/ADVISORIES.md" --arg verify ".kimiflow/background/$id/VERIFY.md" \
    --argjson affected "$affected_json" '{
      schema_version: 1,
      id: $id,
      kind: $kind,
      title: $title,
      status: $status,
      created_at: $now,
      updated_at: $now,
      base_commit: $base,
      affected_paths: $affected,
      handoff_path: $handoff,
      result_path: $result,
      files_path: $files,
      advisories_path: $advisories,
      verify_path: $verify,
      candidate_only: ($kind == "security" or $kind == "improve"),
      collect_policy: "foreground_orchestrator_verifies_before_apply"
    }')"
  if [ "$write" -eq 1 ]; then
    mkdir -p "$dir" || die "cannot create handle dir" 1
    printf '%s\n' "$status" | jq . > "$dir/STATUS.json"
    printf 'Background Handle: %s\nKind: %s\nTitle: %s\nBase commit: %s\nAffected paths:\n' "$id" "$kind" "$title" "$base" > "$dir/HANDOFF.md"
    printf '%s\n' "$affected_json" | jq -r '.[] | "- " + .' >> "$dir/HANDOFF.md"
    printf '[]\n' > "$dir/FILES.json"
    : > "$dir/ADVISORIES.md"
    : > "$dir/VERIFY.md"
    write_index_event "$root" "$status" || die "cannot write background index" 1
  fi
  json_print "$status" "$pretty"
}

cmd_list() {
  local root="" pretty=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --json) ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "list: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  root="$(resolve_root "$root")"
  json_print "$(list_json "$root")" "$pretty"
}

cmd_status() {
  local root="" id="" pretty=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --id) shift; id="${1:-}" ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "status: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  [ -n "$id" ] || die "status requires --id" 2
  root="$(resolve_root "$root")"
  local status_json
  status_json="$(load_status "$root" "$id")" || exit $?
  json_print "$status_json" "$pretty"
}

cmd_update() {
  local root="" id="" new_status="" result="" files="" advisories="" verify="" reason="" write=0 pretty=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --id) shift; id="${1:-}" ;;
      --status) shift; new_status="${1:-}" ;;
      --result) shift; result="${1:-}" ;;
      --files) shift; files="${1:-}" ;;
      --advisories) shift; advisories="${1:-}" ;;
      --verify) shift; verify="${1:-}" ;;
      --reason) shift; reason="${1:-}" ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "update: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  [ -n "$id" ] || die "update requires --id" 2
  valid_status "$new_status" || die "invalid status: $new_status" 2
  root="$(resolve_root "$root")"
  local current current_status dir now updated tmp_result="" tmp_files="" tmp_advisories="" tmp_verify="" tmp_status="" files_check
  current="$(load_status "$root" "$id")" || exit $?
  current_status="$(printf '%s\n' "$current" | jq -r '.status')"
  if terminal_status "$current_status" && [ "$new_status" != "$current_status" ]; then
    die "terminal handle cannot transition from $current_status to $new_status" 1
  fi
  dir="$(handle_dir "$root" "$id")"
  [ ! -L "$dir" ] || die "unsafe handle dir" 2
  now="$(iso_now)"
  if [ "$write" -eq 1 ]; then
    ensure_regular_file_target "$dir/RESULT.md"
    ensure_regular_file_target "$dir/FILES.json"
    ensure_regular_file_target "$dir/ADVISORIES.md"
    ensure_regular_file_target "$dir/VERIFY.md"
    ensure_regular_file_target "$dir/STATUS.json"
    [ -n "$result" ] && tmp_result="$(mktemp "$dir/.RESULT.md.tmp.XXXXXX")"
    [ -n "$files" ] && tmp_files="$(mktemp "$dir/.FILES.json.tmp.XXXXXX")"
    [ -n "$advisories" ] && tmp_advisories="$(mktemp "$dir/.ADVISORIES.md.tmp.XXXXXX")"
    [ -n "$verify" ] && tmp_verify="$(mktemp "$dir/.VERIFY.md.tmp.XXXXXX")"
    copy_to_temp "$result" "$tmp_result" || { cleanup_temps "$tmp_result" "$tmp_files" "$tmp_advisories" "$tmp_verify"; exit 1; }
    copy_to_temp "$files" "$tmp_files" || { cleanup_temps "$tmp_result" "$tmp_files" "$tmp_advisories" "$tmp_verify"; exit 1; }
    copy_to_temp "$advisories" "$tmp_advisories" || { cleanup_temps "$tmp_result" "$tmp_files" "$tmp_advisories" "$tmp_verify"; exit 1; }
    copy_to_temp "$verify" "$tmp_verify" || { cleanup_temps "$tmp_result" "$tmp_files" "$tmp_advisories" "$tmp_verify"; exit 1; }
    files_check="${tmp_files:-$dir/FILES.json}"
    [ -f "$files_check" ] && jq -e . "$files_check" >/dev/null 2>&1 || { cleanup_temps "$tmp_result" "$tmp_files" "$tmp_advisories" "$tmp_verify"; die "FILES.json must be valid JSON" 1; }
  fi
  updated="$(printf '%s\n' "$current" | jq \
    --arg status "$new_status" \
    --arg now "$now" \
    --arg reason "$reason" \
    '. + {status: $status, updated_at: $now}
     + (if $reason == "" then {} else {reason: $reason} end)')"
  if [ "$write" -eq 1 ]; then
    tmp_status="$(mktemp "$dir/.STATUS.json.tmp.XXXXXX")"
    printf '%s\n' "$updated" | jq . > "$tmp_status" || { cleanup_temps "$tmp_result" "$tmp_files" "$tmp_advisories" "$tmp_verify" "$tmp_status"; die "cannot write status temp" 1; }
    install_temp "$tmp_result" "$dir/RESULT.md"
    install_temp "$tmp_files" "$dir/FILES.json"
    install_temp "$tmp_advisories" "$dir/ADVISORIES.md"
    install_temp "$tmp_verify" "$dir/VERIFY.md"
    install_temp "$tmp_status" "$dir/STATUS.json"
    write_index_event "$root" "$updated" || die "cannot write background index" 1
  fi
  json_print "$updated" "$pretty"
}

collect_status() {
  local root="$1" id="$2" status current result_path result_file base raw_affected affected changed matches match_count
  current="$(load_status "$root" "$id")" || exit $?
  status="$(printf '%s\n' "$current" | jq -r '.status')"
  case "$status" in
    ready|finished) ;;
    stale|cancelled|failed) collect_line CLOSED "$id" "$status" "status_$status" ""; return 0 ;;
    *) collect_line CLOSED "$id" "$status" "not_ready" ""; return 0 ;;
  esac
  result_path=".kimiflow/background/$id/RESULT.md"
  result_file="$(handle_dir "$root" "$id")/RESULT.md"
  [ ! -L "$result_file" ] || { collect_line CLOSED "$id" "$status" "result_invalid" "$result_path"; return 0; }
  [ -s "$result_file" ] || { collect_line CLOSED "$id" "$status" "result_missing" "$result_path"; return 0; }
  base="$(printf '%s\n' "$current" | jq -r '.base_commit')"
  git_commit_ok "$root" "$base" || { collect_line CLOSED "$id" "$status" "base_invalid" "$base"; return 0; }
  raw_affected="$(printf '%s\n' "$current" | jq -c '.affected_paths // []')"
  if ! printf '%s\n' "$raw_affected" | jq -e 'type == "array" and all(.[]; type == "string" and length > 0)' >/dev/null 2>&1; then
    collect_line CLOSED "$id" "$status" "affected_invalid" ""
    return 0
  fi
  [ "$(printf '%s\n' "$raw_affected" | jq 'length')" -gt 0 ] || { collect_line CLOSED "$id" "$status" "affected_missing" ""; return 0; }
  if ! affected="$(normalize_affected_json "$raw_affected")"; then
    collect_line CLOSED "$id" "$status" "affected_invalid" ""
    return 0
  fi
  changed="$(changed_paths_json "$root" "$base")"
  matches="$(stale_matches_json "$changed" "$affected")"
  match_count="$(printf '%s\n' "$matches" | jq 'length')"
  if [ "$match_count" -gt 0 ]; then
    collect_line CLOSED "$id" "$status" "stale" "$(printf '%s\n' "$matches" | jq -r 'join(",")')"
    return 0
  fi
  collect_line OPEN "$id" "$status" "clean" "$result_path"
}

cmd_collect() {
  local root="" id=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --id) shift; id="${1:-}" ;;
      --help|-h) usage; exit 0 ;;
      *) die "collect: unknown argument: $1" 2 ;;
    esac
    shift
  done
  need_jq
  [ -n "$id" ] || die "collect requires --id" 2
  root="$(resolve_root "$root")"
  collect_status "$root" "$id"
}

cmd_terminal() {
  local command="$1"; shift
  local root="" id="" reason="" write=0 pretty=0 new_status args=()
  case "$command" in
    cancel) new_status="cancelled" ;;
    mark-stale) new_status="stale" ;;
    *) die "unknown terminal command: $command" 2 ;;
  esac
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root) shift; root="${1:-}" ;;
      --id) shift; id="${1:-}" ;;
      --reason) shift; reason="${1:-}" ;;
      --write) write=1 ;;
      --pretty) pretty=1 ;;
      --help|-h) usage; exit 0 ;;
      *) die "$command: unknown argument: $1" 2 ;;
    esac
    shift
  done
  [ -n "$reason" ] || die "$command requires --reason" 2
  args=(--root "$root" --id "$id" --status "$new_status" --reason "$reason")
  [ "$write" -eq 1 ] && args+=(--write)
  [ "$pretty" -eq 1 ] && args+=(--pretty)
  cmd_update "${args[@]}"
}

cmd="${1:-}"
[ -n "$cmd" ] || { usage; exit 2; }
shift || true

case "$cmd" in
  start) cmd_start "$@" ;;
  list) cmd_list "$@" ;;
  status) cmd_status "$@" ;;
  update) cmd_update "$@" ;;
  collect) cmd_collect "$@" ;;
  cancel|mark-stale) cmd_terminal "$cmd" "$@" ;;
  --help|-h|help) usage; exit 0 ;;
  *) die "unknown command: $cmd" 2 ;;
esac
