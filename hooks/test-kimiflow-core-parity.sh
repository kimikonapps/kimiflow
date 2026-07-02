#!/usr/bin/env bash
# kimiflow — parity harness for the R1 kimiflow_core ports.
# It compares pre-R1 Bash helpers against the working tree, normalizing only known
# nondeterminism. Divergences must be documented in the R1 spec §12.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_SHA="${KIMIFLOW_CORE_PARITY_BASE_SHA:-72282e6}"
WORK="$(mktemp -d)"
WORK_REAL="$(cd "$WORK" 2>/dev/null && pwd -P || printf '%s' "$WORK")"
trap 'rm -rf "$WORK"' EXIT

OLD_HOOKS="$WORK/old-hooks"
OLD_ROOT="$WORK/old-root"
mkdir -p "$OLD_ROOT"
if ! git -C "$ROOT" archive "$BASE_SHA" | tar -x -C "$OLD_ROOT"; then
  echo "cannot materialize $BASE_SHA" >&2
  exit 1
fi
OLD_HOOKS="$OLD_ROOT/hooks"

REPO="$WORK/repo"
HOME_DIR="$WORK/home"
mkdir -p "$REPO/hooks"
mkdir -p "$HOME_DIR"
git -C "$REPO" init -q
git -C "$REPO" config user.email "kimiflow@example.test"
git -C "$REPO" config user.name "Kimiflow Test"
printf 'hello\n' > "$REPO/README.md"
printf 'echo hi\n' > "$REPO/hooks/a.sh"
mkdir -p "$REPO/.kimiflow/project"
cat > "$REPO/.kimiflow/project/IMPROVEMENTS.md" <<'EOF'
# Improvements
## Priorisierte Slices

### 1. Release-Doku-Konsistenz automatischer machen
- Idee: foo

### 2. Hook-Doku synchronisieren
- Idee: bar

## Nicht-Ziele
- nix
EOF
cat > "$REPO/.kimiflow/project/FINDINGS.md" <<'EOF'
# Findings
## Offen

### KF-F-001 - Beispiel-Finding
- Status: offen

## Erledigt / ueberholt
EOF
git -C "$REPO" add README.md hooks/a.sh
git -C "$REPO" commit -q -m init

hash_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print "sha256:" $1}'
  else
    sha256sum "$1" | awk '{print "sha256:" $1}'
  fi
}

write_project_map_index() {
  local repo="$1" base hook_hash
  base="$(git -C "$repo" rev-parse --short HEAD)"
  hook_hash="$(hash_file "$repo/hooks/a.sh")"
  jq -n --arg base "$base" --arg hook_hash "$hook_hash" '{
    schema_version: 1,
    language: "de",
    scan_depth: "standard",
    baseline_commit: $base,
    created_at: "2026-07-02T00:00:00Z",
    sections: {
      hooks: {
        files: ["hooks/a.sh"],
        prefixes: ["hooks/"],
        file_hashes: {"hooks/a.sh": $hook_hash},
        last_scanned_commit: $base,
        status: "current"
      }
    },
    artifacts: {}
  }' > "$repo/.kimiflow/project/INDEX.json"
}

write_background_handle() {
  local repo="$1" id="$2" status="$3" result="${4:-yes}" base dir
  base="$(git -C "$repo" rev-parse HEAD)"
  dir="$repo/.kimiflow/background/$id"
  mkdir -p "$dir"
  jq -n --arg id "$id" --arg status "$status" --arg base "$base" '{
    schema_version: 1,
    id: $id,
    kind: "docs",
    title: "Docs",
    status: $status,
    created_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-02T00:00:00Z",
    base_commit: $base,
    affected_paths: ["hooks"],
    handoff_path: ".kimiflow/background/\($id)/HANDOFF.md",
    result_path: ".kimiflow/background/\($id)/RESULT.md",
    files_path: ".kimiflow/background/\($id)/FILES.json",
    advisories_path: ".kimiflow/background/\($id)/ADVISORIES.md",
    verify_path: ".kimiflow/background/\($id)/VERIFY.md",
    candidate_only: false,
    collect_policy: "foreground_orchestrator_verifies_before_apply"
  }' > "$dir/STATUS.json"
  printf '[]\n' > "$dir/FILES.json"
  : > "$dir/ADVISORIES.md"
  : > "$dir/VERIFY.md"
  if [ "$result" = "yes" ]; then
    printf '# Result\nDone.\n' > "$dir/RESULT.md"
  else
    rm -f "$dir/RESULT.md"
  fi
}

FAILS=0
ok() { printf 'ok   %s\n' "$1"; }
bad() { printf 'BAD  %s\n' "$1"; FAILS=$((FAILS + 1)); }

expected_divergence() {
  local label="$1" old_code="$2" new_code="$3"
  case "$label" in
    background_malformed_id)
      [ "$old_code" = "1" ] || return 1
      [ "$new_code" = "2" ] || return 1
      grep -Fxq 'background-run: unsafe handle id' "$WORK/n.err.norm" || return 1
      [ "$(wc -l < "$WORK/n.err.norm" | tr -d ' ')" = "1" ] || return 1
      return 0
      ;;
  esac
  return 1
}

normalize() {
  sed -E \
    -e "s#$OLD_ROOT#ROOT#g" \
    -e "s#$ROOT#ROOT#g" \
    -e "s#$REPO#REPO#g" \
    -e "s#$WORK_REAL#WORK#g" \
    -e "s#$WORK#WORK#g" \
    -e 's#WORK/cases/[A-Za-z0-9_.:-]+-(old|new)#REPO#g' \
    -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z/TIMESTAMP/g' \
    -e 's/bh_[A-Za-z0-9_:-]+/bh_ID/g' \
    -e 's/[0-9a-f]{40}/COMMIT/g'
}

run_one() {
  local label="$1" script="$2" argstr="$3" old_script new_script diverged case_old case_new arg old_args new_args
  old_script="$OLD_HOOKS/$script"
  new_script="$ROOT/hooks/$script"
  args=()
  [ -n "$argstr" ] && IFS='|' read -r -a args <<< "$argstr"

  mkdir -p "$WORK/cases"
  case_old="$WORK/cases/${label}-old"
  case_new="$WORK/cases/${label}-new"
  rm -rf "$case_old" "$case_new"
  cp -R "$REPO" "$case_old"
  cp -R "$REPO" "$case_new"

  case "$label" in
    project_map_status_current|project_map_coverage_current|project_map_index_symbols|project_map_refresh_section|project_map_refresh_changed_new)
      write_project_map_index "$case_old"
      write_project_map_index "$case_new"
      ;;
  esac
  case "$label" in
    project_map_refresh_changed_new)
      printf 'new\n' > "$case_old/hooks/new.sh"
      printf 'new\n' > "$case_new/hooks/new.sh"
      ;;
  esac
  case "$label" in
    background_invalid_files_json)
      write_background_handle "$case_old" "bh_test" "pending" "yes"
      write_background_handle "$case_new" "bh_test" "pending" "yes"
      printf '{bad json\n' > "$case_old/invalid-files.json"
      printf '{bad json\n' > "$case_new/invalid-files.json"
      ;;
    background_result_tampering)
      write_background_handle "$case_old" "bh_test" "ready" "no"
      write_background_handle "$case_new" "bh_test" "ready" "no"
      jq '.result_path = "hooks/a.sh"' "$case_old/.kimiflow/background/bh_test/STATUS.json" > "$case_old/status.tmp" && mv "$case_old/status.tmp" "$case_old/.kimiflow/background/bh_test/STATUS.json"
      jq '.result_path = "hooks/a.sh"' "$case_new/.kimiflow/background/bh_test/STATUS.json" > "$case_new/status.tmp" && mv "$case_new/status.tmp" "$case_new/.kimiflow/background/bh_test/STATUS.json"
      ;;
    background_terminal_refusal)
      write_background_handle "$case_old" "bh_test" "cancelled" "yes"
      write_background_handle "$case_new" "bh_test" "cancelled" "yes"
      ;;
    launcher_no_kimiflow)
      rm -rf "$case_old/.kimiflow" "$case_new/.kimiflow"
      ;;
    launcher_invalid_map_json)
      mkdir -p "$case_old/.kimiflow/project" "$case_new/.kimiflow/project"
      printf '{bad json\n' > "$case_old/.kimiflow/project/INDEX.json"
      printf '{bad json\n' > "$case_new/.kimiflow/project/INDEX.json"
      ;;
    launcher_stale_plugin_cache)
      mkdir -p "$case_old/.codex-plugin" "$case_new/.codex-plugin" "$case_old/fake-cache/.codex-plugin" "$case_new/fake-cache/.codex-plugin"
      printf '{"version":"9.9.9"}\n' > "$case_old/.codex-plugin/plugin.json"
      printf '{"version":"9.9.9"}\n' > "$case_new/.codex-plugin/plugin.json"
      printf '{"version":"0.0.1"}\n' > "$case_old/fake-cache/.codex-plugin/plugin.json"
      printf '{"version":"0.0.1"}\n' > "$case_new/fake-cache/.codex-plugin/plugin.json"
      ;;
  esac

  old_args=()
  new_args=()
  for arg in ${args[@]+"${args[@]}"}; do
    if [ "$arg" = "__REPO__" ]; then
      old_args+=("$case_old")
      new_args+=("$case_new")
    else
      old_args+=("$arg")
      new_args+=("$arg")
    fi
  done

  old_env=(HOME="$HOME_DIR" KIMIFLOW_OBSIDIAN_URL= KIMIFLOW_OBSIDIAN_API_KEY=)
  new_env=(HOME="$HOME_DIR" KIMIFLOW_OBSIDIAN_URL= KIMIFLOW_OBSIDIAN_API_KEY=)
  case "$label" in
    launcher_stale_plugin_cache)
      old_env+=(KIMIFLOW_PLUGIN_ROOT="$case_old/fake-cache")
      new_env+=(KIMIFLOW_PLUGIN_ROOT="$case_new/fake-cache")
      ;;
  esac

  (cd "$case_old" && env "${old_env[@]}" bash "$old_script" ${old_args[@]+"${old_args[@]}"}) > "$WORK/o.out" 2> "$WORK/o.err"; o_code=$?
  (cd "$case_new" && env "${new_env[@]}" bash "$new_script" ${new_args[@]+"${new_args[@]}"}) > "$WORK/n.out" 2> "$WORK/n.err"; n_code=$?

  normalize < "$WORK/o.out" > "$WORK/o.out.norm"
  normalize < "$WORK/o.err" > "$WORK/o.err.norm"
  normalize < "$WORK/n.out" > "$WORK/n.out.norm"
  normalize < "$WORK/n.err" > "$WORK/n.err.norm"

  diverged=""
  [ "$o_code" != "$n_code" ] && diverged="${diverged}exit($o_code!=$n_code) "
  cmp -s "$WORK/o.out.norm" "$WORK/n.out.norm" || diverged="${diverged}stdout "
  cmp -s "$WORK/o.err.norm" "$WORK/n.err.norm" || diverged="${diverged}stderr "

  if [ -z "$diverged" ]; then
    ok "$label"
    return 0
  fi

  if expected_divergence "$label" "$o_code" "$n_code"; then
    ok "$label (§12 divergence)"
    return 0
  fi

  bad "$label — diverged: $diverged"
  if [ "$o_code" != "$n_code" ]; then
    printf '  exit codes: old=%s new=%s\n' "$o_code" "$n_code"
  fi
  if ! cmp -s "$WORK/o.out.norm" "$WORK/n.out.norm"; then
    printf '  [stdout diff]\n'
    diff -u "$WORK/o.out.norm" "$WORK/n.out.norm" | sed 's/^/  /' || true
  fi
  if ! cmp -s "$WORK/o.err.norm" "$WORK/n.err.norm"; then
    printf '  [stderr diff]\n'
    diff -u "$WORK/o.err.norm" "$WORK/n.err.norm" | sed 's/^/  /' || true
  fi
}

CASES=(
  "active_status_none::active-run.sh::status|--root|__REPO__"
  "background_list_empty::background-run.sh::list|--root|__REPO__|--json"
  "background_start_preview::background-run.sh::start|--root|__REPO__|--kind|docs|--title|Docs|--affected|hooks"
  "background_malformed_id::background-run.sh::status|--root|__REPO__|--id|../escape"
  "background_invalid_files_json::background-run.sh::update|--root|__REPO__|--id|bh_test|--status|ready|--files|invalid-files.json|--write"
  "background_result_tampering::background-run.sh::collect|--root|__REPO__|--id|bh_test"
  "background_terminal_refusal::background-run.sh::update|--root|__REPO__|--id|bh_test|--status|ready|--write"
  "improvements_list_open::improvements-status.sh::list|--root|__REPO__"
  "improvements_json_open::improvements-status.sh::list|--root|__REPO__|--json"
  "improvements_unknown_queue::improvements-status.sh::list|--root|__REPO__|--queue|bogus"
  "improvements_dry_run::improvements-status.sh::mark-done|release|--root|__REPO__|--commit|abc123"
  "improvements_mark_write::improvements-status.sh::mark-done|release|--root|__REPO__|--commit|abc123|--write"
  "project_map_status_missing::project-map-status.sh::status"
  "project_map_coverage_missing::project-map-status.sh::coverage|--affected|hooks/a.sh"
  "project_map_status_current::project-map-status.sh::status"
  "project_map_coverage_current::project-map-status.sh::coverage|--affected|hooks/a.sh"
  "project_map_index_symbols::project-map-status.sh::index-symbols|--section|hooks"
  "project_map_refresh_section::project-map-status.sh::refresh|--section|hooks"
  "project_map_refresh_changed_new::project-map-status.sh::refresh|--changed"
  "launcher_missing_root::launcher-status.sh::--root|$WORK/missing-root"
  "launcher_no_kimiflow::launcher-status.sh::--root|__REPO__"
  "launcher_invalid_map_json::launcher-status.sh::--root|__REPO__"
  "launcher_pretty::launcher-status.sh::--root|__REPO__|--pretty"
  "launcher_stale_plugin_cache::launcher-status.sh::--root|__REPO__"
  "clarify_missing_dir::clarify-gate.sh::$WORK/missing-run"
  "plan_blocker_missing_dir::plan-blocker-gate.sh::$WORK/missing-run"
  "agentic_status_repo::agentic-readiness.sh::status|--root|$REPO"
)

for entry in "${CASES[@]}"; do
  label="${entry%%::*}"
  rest="${entry#*::}"
  script="${rest%%::*}"
  argstr="${rest#*::}"
  run_one "$label" "$script" "$argstr"
done

echo "----"
if [ "$FAILS" -eq 0 ]; then
  echo "ALL GREEN"
  exit 0
fi
echo "$FAILS DIVERGENCES"
exit 1
