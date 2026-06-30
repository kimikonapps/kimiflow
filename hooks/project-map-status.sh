#!/usr/bin/env bash
# kimiflow — project-map staleness resolver. Orchestrator-invoked, not a hook.
#
# Usage:
#   project-map-status.sh [status] [--index <path>] [--affected <path>]...
#   project-map-status.sh coverage [--index <path>] [--affected <path>]...
#   project-map-status.sh refresh [--index <path>] --section <name>...
#   project-map-status.sh refresh [--index <path>] --changed
#   project-map-status.sh index-symbols [--index <path>] [--section <name>...]   (default: all sections)
#
# Output is TSV-ish and stable:
#   PROJECT_MAP <status> stale=<n> potentially_stale=<n> unknown=<n> affected_stale=<n> index=<path>
#   PROJECT_MAP_COVERAGE <status> affected=<n> mapped=<n> unmapped=<n> affected_stale=<n> affected_unknown=<n> phase2_depth=<compressed|targeted|full> reason=<reason> index=<path>
#   SECTION     <name>   <status> affected=<yes|no|all> reason=<reason> paths=<csv|->
#   REFRESHED   <name>   files=<n> commit=<sha|NOT VERIFIED>
#   NEW-FILE    <section> <path>
#   SYMBOLS     <section>
set -u

usage() {
  sed -n '1,17p' "$0" >&2
}

die() {
  printf 'project-map-status: %s\n' "$1" >&2
  exit "${2:-1}"
}

need_jq() {
  command -v jq >/dev/null 2>&1 || die "jq is required" 2
}

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print "sha256:" $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print "sha256:" $1}'
  else
    die "shasum or sha256sum is required" 2
  fi
}

contains() {
  local needle="$1"; shift
  local item
  for item in "$@"; do
    [ "$item" = "$needle" ] && return 0
  done
  return 1
}

join_csv() {
  local out="" item
  for item in "$@"; do
    [ -n "$item" ] || continue
    if [ -z "$out" ]; then out="$item"; else out="$out,$item"; fi
  done
  [ -n "$out" ] && printf '%s' "$out" || printf '-'
}

path_matches_prefix() {
  local path="$1" prefix
  shift
  for prefix in "$@"; do
    [ -n "$prefix" ] || continue
    case "$path" in
      "$prefix"|"$prefix"/*) return 0 ;;
      *) case "$prefix" in */) case "$path" in "$prefix"*) return 0 ;; esac ;; esac ;;
    esac
  done
  return 1
}

manifest_path() {
  case "$1" in
    package.json|package-lock.json|pnpm-lock.yaml|yarn.lock|bun.lockb|tsconfig*.json|Cargo.toml|Cargo.lock|pyproject.toml|requirements*.txt|uv.lock|go.mod|go.sum|Gemfile|Gemfile.lock|composer.json|composer.lock|pom.xml|build.gradle|settings.gradle|Makefile|Dockerfile|docker-compose*.yml|docker-compose*.yaml)
      return 0 ;;
    *) return 1 ;;
  esac
}

flow_path() {
  case "$1" in
    *route*|*api*|*schema*|*migration*|db/migrate/*)
      return 0 ;;
    *) return 1 ;;
  esac
}

section_is_stackish() {
  case "$1" in
    tech|stack|dependencies|architecture|testing|quality|conventions)
      return 0 ;;
    *) return 1 ;;
  esac
}

section_is_flowish() {
  case "$1" in
    flow|flows|routing|api|schema|migrations)
      return 0 ;;
    *) return 1 ;;
  esac
}

git_commit_ok() {
  local root="$1" commit="$2"
  [ -n "$commit" ] && [ "$commit" != "NOT VERIFIED" ] || return 1
  git -C "$root" cat-file -e "$commit^{commit}" >/dev/null 2>&1
}

collect_changed_paths() {
  local root="$1" base="$2" status path old new
  CHANGED_PATHS=()

  if git_commit_ok "$root" "$base"; then
    while IFS=$'\t' read -r status old new; do
      [ -n "${status:-}" ] || continue
      case "$status" in
        R*|C*)
          [ -n "${old:-}" ] && CHANGED_PATHS+=("$old")
          [ -n "${new:-}" ] && CHANGED_PATHS+=("$new")
          ;;
        *)
          [ -n "${old:-}" ] && CHANGED_PATHS+=("$old")
          ;;
      esac
    done < <(git -C "$root" diff --name-status "$base" HEAD 2>/dev/null)
  fi

  while IFS= read -r path; do [ -n "$path" ] && CHANGED_PATHS+=("$path"); done < <(git -C "$root" diff --name-only --cached 2>/dev/null)
  while IFS= read -r path; do [ -n "$path" ] && CHANGED_PATHS+=("$path"); done < <(git -C "$root" diff --name-only 2>/dev/null)
  while IFS= read -r path; do [ -n "$path" ] && CHANGED_PATHS+=("$path"); done < <(git -C "$root" ls-files --others --exclude-standard 2>/dev/null)
}

read_section_list() {
  local index="$1" section="$2" query="$3"
  jq -r --arg s "$section" "$query" "$index" 2>/dev/null
}

section_status() {
  local root="$1" index="$2" section="$3"
  local base status reason affected path expected actual
  local files hash_paths prefixes hit_paths paths_out
  files=(); hash_paths=(); prefixes=(); hit_paths=()

  while IFS= read -r path; do [ -n "$path" ] && files+=("$path"); done < <(
    read_section_list "$index" "$section" '((.sections[$s].files // []) + ((.sections[$s].file_hashes // {}) | keys)) | unique[]'
  )
  while IFS= read -r path; do [ -n "$path" ] && hash_paths+=("$path"); done < <(
    read_section_list "$index" "$section" '((.sections[$s].file_hashes // {}) | keys[])'
  )
  while IFS= read -r path; do [ -n "$path" ] && prefixes+=("$path"); done < <(
    read_section_list "$index" "$section" '(.sections[$s].prefixes // [])[]'
  )
  if [ "${#prefixes[@]}" -eq 0 ]; then
    for path in ${files[@]+"${files[@]}"}; do
      case "$path" in
        */*) prefixes+=("${path%/*}/") ;;
        *) prefixes+=("$path") ;;
      esac
    done
  fi

  base="$(jq -r --arg s "$section" '.sections[$s].last_scanned_commit // .baseline_commit // "NOT VERIFIED"' "$index" 2>/dev/null)"
  collect_changed_paths "$root" "$base"

  status="current"; reason="clean"
  if [ "${#files[@]}" -eq 0 ] && [ "${#prefixes[@]}" -eq 0 ]; then
    status="unknown"; reason="no-section-files"
  fi

  for path in ${files[@]+"${files[@]}"}; do
    if [ ! -e "$root/$path" ]; then
      status="stale"; reason="deleted-section-file"; hit_paths+=("$path"); break
    fi
    expected="$(jq -r --arg s "$section" --arg p "$path" '.sections[$s].file_hashes[$p] // empty' "$index" 2>/dev/null)"
    if [ -n "$expected" ]; then
      actual="$(sha256_file "$root/$path")"
      if [ "$actual" != "$expected" ]; then
        status="stale"; reason="hash-mismatch"; hit_paths+=("$path"); break
      fi
    fi
  done

  if [ "$status" = "current" ]; then
    for path in ${CHANGED_PATHS[@]+"${CHANGED_PATHS[@]}"}; do
      if [ "${#files[@]}" -gt 0 ] && contains "$path" ${files[@]+"${files[@]}"}; then
        if [ "${#hash_paths[@]}" -eq 0 ] || ! contains "$path" ${hash_paths[@]+"${hash_paths[@]}"}; then
          if manifest_path "$path" && section_is_stackish "$section"; then
            status="potentially_stale"; reason="manifest-or-build-config-changed"; hit_paths+=("$path"); break
          else
            status="stale"; reason="changed-section-file"; hit_paths+=("$path"); break
          fi
        fi
      fi
    done
  fi

  if [ "$status" = "current" ]; then
    for path in ${CHANGED_PATHS[@]+"${CHANGED_PATHS[@]}"}; do
      if { [ "${#files[@]}" -eq 0 ] || ! contains "$path" ${files[@]+"${files[@]}"}; } && \
        [ "${#prefixes[@]}" -gt 0 ] && path_matches_prefix "$path" ${prefixes[@]+"${prefixes[@]}"}; then
        status="potentially_stale"; reason="new-or-unmapped-file-under-prefix"; hit_paths+=("$path"); break
      fi
    done
  fi

  if [ "$status" = "current" ]; then
    for path in ${CHANGED_PATHS[@]+"${CHANGED_PATHS[@]}"}; do
      if manifest_path "$path" && section_is_stackish "$section"; then
        status="potentially_stale"; reason="manifest-or-build-config-changed"; hit_paths+=("$path"); break
      fi
      if flow_path "$path" && section_is_flowish "$section"; then
        status="stale"; reason="route-api-schema-or-migration-changed"; hit_paths+=("$path"); break
      fi
    done
  fi

  if [ "$status" = "current" ] && ! git_commit_ok "$root" "$base" && [ "${#hash_paths[@]}" -eq 0 ]; then
    status="unknown"; reason="baseline-commit-not-verifiable"
  fi

  affected="all"
  if [ "${#AFFECTED_PATHS[@]}" -gt 0 ]; then
    affected="no"
    for path in ${AFFECTED_PATHS[@]+"${AFFECTED_PATHS[@]}"}; do
      if { [ "${#files[@]}" -gt 0 ] && contains "$path" ${files[@]+"${files[@]}"}; } || \
        { [ "${#prefixes[@]}" -gt 0 ] && path_matches_prefix "$path" ${prefixes[@]+"${prefixes[@]}"}; }; then
        affected="yes"; break
      fi
    done
  fi

  paths_out="$(join_csv ${hit_paths[@]+"${hit_paths[@]}"})"
	  printf 'SECTION\t%s\t%s\taffected=%s\treason=%s\tpaths=%s\n' \
	    "$section" "$status" "$affected" "$reason" "$paths_out"
	}

build_map_scope() {
  local path
  MAP_FILES=()
  MAP_PREFIXES=()
  while IFS= read -r path; do [ -n "$path" ] && MAP_FILES+=("$path"); done < <(
    jq -r '[.sections[]? | (.files // [])[]?, ((.file_hashes // {}) | keys[]?)] | unique[]' "$INDEX" 2>/dev/null
  )
  while IFS= read -r path; do [ -n "$path" ] && MAP_PREFIXES+=("$path"); done < <(
    jq -r '[.sections[]? | (.prefixes // [])[]?] | unique[]' "$INDEX" 2>/dev/null
  )
  for path in ${MAP_FILES[@]+"${MAP_FILES[@]}"}; do
    case "$path" in
      */*) MAP_PREFIXES+=("${path%/*}/") ;;
      *) MAP_PREFIXES+=("$path") ;;
    esac
  done
}

path_is_mapped() {
  local path="$1"
  if [ "${#MAP_FILES[@]}" -gt 0 ] && contains "$path" ${MAP_FILES[@]+"${MAP_FILES[@]}"}; then
    return 0
  fi
  if [ "${#MAP_PREFIXES[@]}" -gt 0 ] && path_matches_prefix "$path" ${MAP_PREFIXES[@]+"${MAP_PREFIXES[@]}"}; then
    return 0
  fi
  return 1
}

section_members() {
  read_section_list "$INDEX" "$1" '((.sections[$s].files // []) + ((.sections[$s].file_hashes // {}) | keys)) | unique[]'
}

section_prefixes_effective() {
  local s="$1" path
  local pf
  pf=()
  while IFS= read -r path; do [ -n "$path" ] && pf+=("$path"); done < <(
    read_section_list "$INDEX" "$s" '(.sections[$s].prefixes // [])[]'
  )
  if [ "${#pf[@]}" -eq 0 ]; then
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      case "$path" in
        */*) pf+=("${path%/*}/") ;;
        *) pf+=("$path") ;;
      esac
    done < <(section_members "$s")
  fi
  printf '%s\n' ${pf[@]+"${pf[@]}"}
}

# B1 — extract shell function definitions (`name()` at line start, comment lines skipped)
# into sections[$s].symbols = {"NAME":"path:line"}. .sh files only; schema stays additive.
index_symbols_section() {
  local s="$1" path name line rest grepline symbols tmp
  symbols='{}'
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in *.sh) ;; *) continue ;; esac
    [ -f "$ROOT/$path" ] || continue
    while IFS= read -r grepline; do
      [ -n "$grepline" ] || continue
      line="${grepline%%:*}"
      rest="${grepline#*:}"
      name="${rest%%(*}"
      [ -n "$name" ] || continue
      symbols="$(printf '%s\n' "$symbols" | jq --arg n "$name" --arg loc "$path:$line" '. + {($n): $loc}')"
    done < <(grep -nE '^[A-Za-z_][A-Za-z0-9_]*\(\)' "$ROOT/$path" 2>/dev/null)
  done < <(section_members "$s")
  tmp="$(mktemp)"
  jq --arg s "$s" --argjson symbols "$symbols" '.sections[$s].symbols = $symbols' "$INDEX" > "$tmp" && mv "$tmp" "$INDEX"
}

# A1 — restamp only the sections whose files changed vs the baseline commit (with a graceful
# working-tree-only fallback when the baseline is unreachable). Prunes deleted members, adopts new
# files under a section prefix (longest prefix wins, ties resolve to the first INDEX section), and
# re-indexes symbols of each refreshed section. No change → no mutation.
do_refresh_changed() {
  local base commit now path s m i h count tmp files_json k prefix plen best_s best_len
  local ALL_SECTIONS ALL_MEMBERS UNIQUE_CHANGED AFFECTED_SECTIONS NEWFILE_SECTION NEWFILE_PATH final_files hashes
  local MEMBER_SECTION MEMBER_PATH PFX_SECTION PFX_VALUE
  ALL_SECTIONS=(); ALL_MEMBERS=(); UNIQUE_CHANGED=(); AFFECTED_SECTIONS=(); NEWFILE_SECTION=(); NEWFILE_PATH=()
  MEMBER_SECTION=(); MEMBER_PATH=(); PFX_SECTION=(); PFX_VALUE=()

  base="$(jq -r '.baseline_commit // "NOT VERIFIED"' "$INDEX" 2>/dev/null)"
  collect_changed_paths "$ROOT" "$base"
  commit="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf 'NOT VERIFIED')"
  now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

  while IFS= read -r s; do [ -n "$s" ] && ALL_SECTIONS+=("$s"); done < <(jq -r '.sections // {} | keys_unsorted[]' "$INDEX")
  while IFS= read -r path; do [ -n "$path" ] && ALL_MEMBERS+=("$path"); done < <(
    jq -r '[.sections[]? | (.files // [])[]?, ((.file_hashes // {}) | keys[]?)] | unique[]' "$INDEX"
  )

  # Precompute section attribution ONCE so the per-changed-path loop below stays pure-shell.
  # Recomputing section_members / section_prefixes_effective *inside* that loop spawned
  # O(changed_paths × sections) process substitutions, which crashes bash 3.2 (SIGTRAP, exit 133)
  # on large deltas. member→section pairs in one jq pass (section order preserved):
  while IFS="$(printf '\t')" read -r s path; do
    [ -n "$s" ] && [ -n "$path" ] || continue
    MEMBER_SECTION+=("$s"); MEMBER_PATH+=("$path")
  done < <(jq -r '.sections | to_entries[] | .key as $s | ((.value.files // []) + ((.value.file_hashes // {}) | keys)) | unique[] | [$s, .] | @tsv' "$INDEX")
  # effective prefixes, flattened to (section, prefix) pairs in section order (one subshell each):
  for s in ${ALL_SECTIONS[@]+"${ALL_SECTIONS[@]}"}; do
    while IFS= read -r prefix; do
      [ -n "$prefix" ] || continue
      PFX_SECTION+=("$s"); PFX_VALUE+=("$prefix")
    done < <(section_prefixes_effective "$s")
  done

  for path in ${CHANGED_PATHS[@]+"${CHANGED_PATHS[@]}"}; do
    contains "$path" ${UNIQUE_CHANGED[@]+"${UNIQUE_CHANGED[@]}"} || UNIQUE_CHANGED+=("$path")
  done

  for path in ${UNIQUE_CHANGED[@]+"${UNIQUE_CHANGED[@]}"}; do
    if [ "${#ALL_MEMBERS[@]}" -gt 0 ] && contains "$path" ${ALL_MEMBERS[@]+"${ALL_MEMBERS[@]}"}; then
      k=0
      while [ "$k" -lt "${#MEMBER_PATH[@]}" ]; do
        if [ "${MEMBER_PATH[$k]}" = "$path" ]; then
          s="${MEMBER_SECTION[$k]}"
          contains "$s" ${AFFECTED_SECTIONS[@]+"${AFFECTED_SECTIONS[@]}"} || AFFECTED_SECTIONS+=("$s")
        fi
        k=$((k + 1))
      done
    else
      [ -e "$ROOT/$path" ] || continue
      # longest matching prefix across all sections wins; ties resolve to the first section
      # (PFX_* arrays are built in section order, and we only update on a strictly longer match).
      best_s=""; best_len=-1
      k=0
      while [ "$k" -lt "${#PFX_VALUE[@]}" ]; do
        prefix="${PFX_VALUE[$k]}"
        plen=-1
        if [ -n "$prefix" ]; then
          case "$path" in
            "$prefix"|"$prefix"/*) plen="${#prefix}" ;;
            *) case "$prefix" in */) case "$path" in "$prefix"*) plen="${#prefix}" ;; esac ;; esac ;;
          esac
        fi
        if [ "$plen" -gt "$best_len" ]; then best_len="$plen"; best_s="${PFX_SECTION[$k]}"; fi
        k=$((k + 1))
      done
      if [ -n "$best_s" ] && [ "$best_len" -ge 0 ]; then
        NEWFILE_SECTION+=("$best_s"); NEWFILE_PATH+=("$path")
        contains "$best_s" ${AFFECTED_SECTIONS[@]+"${AFFECTED_SECTIONS[@]}"} || AFFECTED_SECTIONS+=("$best_s")
      fi
    fi
  done

  [ "${#AFFECTED_SECTIONS[@]}" -gt 0 ] || return 0

  i=0
  while [ "$i" -lt "${#NEWFILE_PATH[@]}" ]; do
    printf 'NEW-FILE\t%s\t%s\n' "${NEWFILE_SECTION[$i]}" "${NEWFILE_PATH[$i]}"
    i=$((i + 1))
  done

  for s in ${AFFECTED_SECTIONS[@]+"${AFFECTED_SECTIONS[@]}"}; do
    final_files=()
    while IFS= read -r m; do
      [ -n "$m" ] || continue
      [ -e "$ROOT/$m" ] || continue
      contains "$m" ${final_files[@]+"${final_files[@]}"} || final_files+=("$m")
    done < <(section_members "$s")
    i=0
    while [ "$i" -lt "${#NEWFILE_PATH[@]}" ]; do
      if [ "${NEWFILE_SECTION[$i]}" = "$s" ]; then
        contains "${NEWFILE_PATH[$i]}" ${final_files[@]+"${final_files[@]}"} || final_files+=("${NEWFILE_PATH[$i]}")
      fi
      i=$((i + 1))
    done
    hashes='{}'; count=0
    for path in ${final_files[@]+"${final_files[@]}"}; do
      if [ -f "$ROOT/$path" ]; then
        h="$(sha256_file "$ROOT/$path")"
        hashes="$(printf '%s\n' "$hashes" | jq --arg p "$path" --arg h "$h" '. + {($p): $h}')"
        count=$((count + 1))
      fi
    done
    files_json="$(printf '%s\n' ${final_files[@]+"${final_files[@]}"} | jq -R . | jq -s 'map(select(length > 0))')"
    tmp="$(mktemp)"
    jq --arg s "$s" --arg commit "$commit" --arg now "$now" --argjson hashes "$hashes" --argjson files "$files_json" '
      .sections[$s].files = $files |
      .sections[$s].file_hashes = $hashes |
      .sections[$s].last_scanned_commit = $commit |
      .sections[$s].status = "current" |
      .sections[$s].updated_at = $now
    ' "$INDEX" > "$tmp" && mv "$tmp" "$INDEX"
    index_symbols_section "$s"
    printf 'REFRESHED\t%s\tfiles=%s\tcommit=%s\n' "$s" "$count" "$commit"
  done

  # Advance the baseline so an already-absorbed COMMITTED delta is not re-detected next run
  # (idempotent for committed changes; working-tree staged/unstaged/untracked changes are still
  # re-detected each run regardless of baseline). Only when HEAD is resolvable.
  if [ "$commit" != "NOT VERIFIED" ]; then
    tmp="$(mktemp)"
    jq --arg commit "$commit" '.baseline_commit = $commit' "$INDEX" > "$tmp" && mv "$tmp" "$INDEX"
  fi
  return 0
}

mode="status"
INDEX=""
AFFECTED_PATHS=()
REFRESH_SECTIONS=()
REFRESH_CHANGED=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    status|coverage|refresh|index-symbols) mode="$1" ;;
    --index) shift; INDEX="${1:-}" ;;
    --affected) shift; AFFECTED_PATHS+=("${1:-}") ;;
    --section) shift; REFRESH_SECTIONS+=("${1:-}") ;;
    --changed) REFRESH_CHANGED=1 ;;
    --help|-h) usage; exit 0 ;;
    *)
      if [ "$mode" = "refresh" ] || [ "$mode" = "index-symbols" ]; then REFRESH_SECTIONS+=("$1"); else AFFECTED_PATHS+=("$1"); fi
      ;;
  esac
  shift
done

need_jq
ROOT="$(repo_root)"
[ -n "$INDEX" ] || INDEX="$ROOT/.kimiflow/project/INDEX.json"

if [ "${#AFFECTED_PATHS[@]}" -gt 0 ]; then
  i=0
  while [ "$i" -lt "${#AFFECTED_PATHS[@]}" ]; do
    path="${AFFECTED_PATHS[$i]}"
    case "$path" in
      "$ROOT"/*) path="${path#$ROOT/}" ;;
      ./*) path="${path#./}" ;;
    esac
    AFFECTED_PATHS[$i]="$path"
    i=$((i + 1))
  done
fi

if [ ! -f "$INDEX" ]; then
  if [ "$mode" = "coverage" ]; then
    printf 'PROJECT_MAP_COVERAGE\tmissing\taffected=%s\tmapped=0\tunmapped=%s\taffected_stale=0\taffected_unknown=0\tphase2_depth=full\treason=missing-index\tindex=%s\n' "${#AFFECTED_PATHS[@]}" "${#AFFECTED_PATHS[@]}" "$INDEX"
  else
    printf 'PROJECT_MAP\tmissing\tstale=0\tpotentially_stale=0\tunknown=0\taffected_stale=0\tindex=%s\n' "$INDEX"
  fi
  exit 0
fi

if ! jq -e . "$INDEX" >/dev/null 2>&1; then
  if [ "$mode" = "coverage" ]; then
    printf 'PROJECT_MAP_COVERAGE\tunknown\taffected=%s\tmapped=0\tunmapped=%s\taffected_stale=0\taffected_unknown=0\tphase2_depth=full\treason=invalid-index\tindex=%s\n' "${#AFFECTED_PATHS[@]}" "${#AFFECTED_PATHS[@]}" "$INDEX"
  else
    printf 'PROJECT_MAP\tunknown\tstale=0\tpotentially_stale=0\tunknown=1\taffected_stale=0\tindex=%s\n' "$INDEX"
  fi
  exit 0
fi

if [ "$mode" = "index-symbols" ]; then
  [ "${#REFRESH_SECTIONS[@]}" -gt 0 ] || while IFS= read -r s; do REFRESH_SECTIONS+=("$s"); done < <(jq -r '.sections // {} | keys[]' "$INDEX")
  for section in ${REFRESH_SECTIONS[@]+"${REFRESH_SECTIONS[@]}"}; do
    jq -e --arg s "$section" '.sections[$s]' "$INDEX" >/dev/null 2>&1 || die "unknown section: $section"
    index_symbols_section "$section"
    printf 'SYMBOLS\t%s\n' "$section"
  done
  exit 0
fi

if [ "$mode" = "refresh" ]; then
  if [ "$REFRESH_CHANGED" = "1" ]; then
    do_refresh_changed
    exit 0
  fi
  [ "${#REFRESH_SECTIONS[@]}" -gt 0 ] || while IFS= read -r s; do REFRESH_SECTIONS+=("$s"); done < <(jq -r '.sections // {} | keys[]' "$INDEX")
  commit="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf 'NOT VERIFIED')"
  now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

  for section in ${REFRESH_SECTIONS[@]+"${REFRESH_SECTIONS[@]}"}; do
    jq -e --arg s "$section" '.sections[$s]' "$INDEX" >/dev/null 2>&1 || die "unknown section: $section"
    files=()
    while IFS= read -r path; do [ -n "$path" ] && files+=("$path"); done < <(
      jq -r --arg s "$section" '((.sections[$s].files // []) + ((.sections[$s].file_hashes // {}) | keys)) | unique[]' "$INDEX"
    )
    hashes='{}'
    count=0
    for path in ${files[@]+"${files[@]}"}; do
      if [ -f "$ROOT/$path" ]; then
        h="$(sha256_file "$ROOT/$path")"
        hashes="$(printf '%s\n' "$hashes" | jq --arg p "$path" --arg h "$h" '. + {($p): $h}')"
        count=$((count + 1))
      fi
    done
    tmp="$(mktemp)"
    jq --arg s "$section" --arg commit "$commit" --arg now "$now" --argjson hashes "$hashes" '
      .sections[$s].file_hashes = $hashes |
      .sections[$s].last_scanned_commit = $commit |
      .sections[$s].status = "current" |
      .sections[$s].updated_at = $now
    ' "$INDEX" > "$tmp" && mv "$tmp" "$INDEX"
    printf 'REFRESHED\t%s\tfiles=%s\tcommit=%s\n' "$section" "$count" "$commit"
  done
  exit 0
fi

SECTIONS=()
while IFS= read -r section; do [ -n "$section" ] && SECTIONS+=("$section"); done < <(jq -r '.sections // {} | keys[]' "$INDEX")

if [ "${#SECTIONS[@]}" -eq 0 ]; then
  printf 'PROJECT_MAP\tunknown\tstale=0\tpotentially_stale=0\tunknown=1\taffected_stale=0\tindex=%s\n' "$INDEX"
  exit 0
fi

LINES=()
stale=0; potential=0; unknown=0; affected_stale=0; affected_unknown=0
for section in ${SECTIONS[@]+"${SECTIONS[@]}"}; do
  line="$(section_status "$ROOT" "$INDEX" "$section")"
  LINES+=("$line")
  case "$line" in
    *$'\tstale\t'*) stale=$((stale + 1)) ;;
    *$'\tpotentially_stale\t'*) potential=$((potential + 1)) ;;
    *$'\tunknown\t'*) unknown=$((unknown + 1)) ;;
  esac
  case "$line" in
    *$'\tstale\t'*"affected=yes"*|*$'\tpotentially_stale\t'*"affected=yes"*) affected_stale=$((affected_stale + 1)) ;;
  esac
  case "$line" in
    *$'\tunknown\t'*"affected=yes"*) affected_unknown=$((affected_unknown + 1)) ;;
  esac
done

overall="current"
if [ "$stale" -gt 0 ]; then
  if [ "$stale" -eq "${#SECTIONS[@]}" ]; then overall="stale"; else overall="partially_stale"; fi
elif [ "$potential" -gt 0 ]; then
  overall="partially_stale"
elif [ "$unknown" -gt 0 ]; then
  overall="unknown"
fi

if [ "$mode" = "coverage" ]; then
  build_map_scope
  affected="${#AFFECTED_PATHS[@]}"
  mapped=0
  unmapped=0
  for path in ${AFFECTED_PATHS[@]+"${AFFECTED_PATHS[@]}"}; do
    if path_is_mapped "$path"; then
      mapped=$((mapped + 1))
    else
      unmapped=$((unmapped + 1))
    fi
  done
  coverage_status="covered"
  phase2_depth="compressed"
  reason="affected-paths-covered-current"
  if [ "$affected" -eq 0 ]; then
    coverage_status="unscoped"
    phase2_depth="targeted"
    reason="no-affected-paths"
  elif [ "$unmapped" -gt 0 ]; then
    coverage_status="partial"
    phase2_depth="full"
    reason="unmapped-affected-paths"
  elif [ "$affected_stale" -gt 0 ]; then
    coverage_status="stale"
    phase2_depth="targeted"
    reason="mapped-but-stale"
  elif [ "$affected_unknown" -gt 0 ]; then
    coverage_status="unknown"
    phase2_depth="targeted"
    reason="mapped-but-unknown"
  elif [ "$overall" != "current" ]; then
    coverage_status="covered"
    phase2_depth="compressed"
    reason="affected-paths-covered-unrelated-map-staleness"
  fi
  printf 'PROJECT_MAP_COVERAGE\t%s\taffected=%s\tmapped=%s\tunmapped=%s\taffected_stale=%s\taffected_unknown=%s\tphase2_depth=%s\treason=%s\tindex=%s\n' \
    "$coverage_status" "$affected" "$mapped" "$unmapped" "$affected_stale" "$affected_unknown" "$phase2_depth" "$reason" "$INDEX"
  exit 0
fi

printf 'PROJECT_MAP\t%s\tstale=%s\tpotentially_stale=%s\tunknown=%s\taffected_stale=%s\tindex=%s\n' \
  "$overall" "$stale" "$potential" "$unknown" "$affected_stale" "$INDEX"
for line in ${LINES[@]+"${LINES[@]}"}; do printf '%s\n' "$line"; done
