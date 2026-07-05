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

file_mode() {
  case "$(uname -s)" in
    Darwin|FreeBSD) stat -f %Lp "$1" ;;
    *) stat -c %a "$1" ;;
  esac
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

write_active_fixture() {
  local repo="$1" base
  base="$(git -C "$repo" rev-parse HEAD)"
  mkdir -p "$repo/.kimiflow/demo" "$repo/.kimiflow/session"
  cat > "$repo/.kimiflow/demo/STATE.md" <<'EOF'
Status: active
Mode: feature
Scope: small
Affected files: hooks/a.sh
Phase 0: done
Phase 1: done
Phase 2: done
Phase 3: done
Phase 4: done
Phase 5: in-progress
Phase 6: open
Phase 7: open
EOF
  jq -n --arg base "$base" '{
    schema_version: 1,
    status: "active",
    run: ".kimiflow/demo",
    mode: "feature",
    scope: "small",
    host: "codex",
    started_at: "2026-07-02T00:00:00Z",
    updated_at: "2026-07-02T00:00:00Z",
    started_head: $base,
    last_checked_head: $base,
    affected_files_at_start: ["hooks/a.sh"]
  }' > "$repo/.kimiflow/session/ACTIVE_RUN.json"
}

write_active_item() {
  local repo="$1" status="${2:-pending}"
  cat > "$repo/.kimiflow/demo/ITEMS.jsonl" <<EOF
{"schema_version":1,"id":"item_001","title":"Do thing","kind":"change","status":"$status","created_at":"2026-07-02T00:00:00Z","updated_at":"2026-07-02T00:00:00Z","reason":""}
EOF
}

write_gate_fixture() {
  local repo="$1" run
  run="$repo/.kimiflow/demo"
  mkdir -p "$run"
  cat > "$run/STATE.md" <<'EOF'
- **Status:** active
- **Mode:** feature
- **Alias:** quick
- **Scope:** small
- **Affected files:**
  - hooks/a.sh
- **Phase 0:** done
- **Phase 1:** done
EOF
  cat > "$run/INTENT.md" <<'EOF'
# Intent
<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=yes source=current-run -->
Build a small fixture against hooks/a.sh.
EOF
  cat > "$run/RESEARCH.md" <<'EOF'
# Research
The fixture touches hooks/a.sh:1.
EOF
  cat > "$run/PLAN.md" <<'EOF'
# Plan
Affected files:
- hooks/a.sh
- Update hooks/a.sh for AC-1.
EOF
  cat > "$run/ACCEPTANCE.md" <<'EOF'
# Acceptance
- AC-1 -> shell_smoke: verify hooks/a.sh behavior.
EOF
}

FAILS=0
ok() { printf 'ok   %s\n' "$1"; }
bad() { printf 'BAD  %s\n' "$1"; FAILS=$((FAILS + 1)); }

expected_divergence() {
  local label="$1" old_code="$2" new_code="$3"
  case "$label" in
    active_mark_built_write|active_mark_accepted_write|active_mark_rejected_write|active_drop_item_write|active_park_write|active_fail_write|active_abort_write)
      [ "$old_code" = "$new_code" ] || return 1
      cmp -s "$WORK/o.out.norm" "$WORK/n.out.norm" || return 1
      cmp -s "$WORK/o.err.norm" "$WORK/n.err.norm" || return 1
      sed -E 's/\tmode=[0-9]+//' "$WORK/o.files.norm" > "$WORK/o.files.nomode"
      sed -E 's/\tmode=[0-9]+//' "$WORK/n.files.norm" > "$WORK/n.files.nomode"
      cmp -s "$WORK/o.files.nomode" "$WORK/n.files.nomode" || return 1
      return 0
      ;;
  esac
  return 1
}

# Post-R1 schema addition: active-session status output gained "awaiting_user"
# (await-user engine gate, hooks/kimiflow_core/active_run.py). Pre-R1 bash never
# emitted the field, so parity strips it (compact and pretty JSON) and keeps
# comparing the shared fields; its semantics are covered by test-active-run.sh
# and the kimiflow_core unit tests.
normalize() {
  sed -E \
    -e "s#$OLD_ROOT#ROOT#g" \
    -e "s#$ROOT#ROOT#g" \
    -e "s#$REPO#REPO#g" \
    -e "s#$WORK_REAL#WORK#g" \
    -e "s#$WORK#WORK#g" \
    -e 's#WORK/cases/[A-Za-z0-9_.:-]+-(old|new)#REPO#g' \
    -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z/TIMESTAMP/g' \
    -e 's/[0-9a-f]{40}/COMMIT/g' \
    -e 's/"version": ?"[0-9]+\.[0-9]+\.[0-9]+"/"version": "VERSION"/g' \
    -e 's/"awaiting_user":(true|false),//g' \
    -e '/^[[:space:]]*"awaiting_user": (true|false),?$/d'
}

normalized_file_hash() {
  if command -v shasum >/dev/null 2>&1; then
    normalize < "$1" | shasum -a 256 | awk '{print "sha256:" $1}'
  else
    normalize < "$1" | sha256sum | awk '{print "sha256:" $1}'
  fi
}

file_state() {
  local dir="$1" rel norm_rel path
  (
    cd "$dir" || exit 1
    find . -path './.git' -prune -o -type f -print | sort | while IFS= read -r rel; do
      path="$dir/${rel#./}"
      norm_rel="$(printf '%s\n' "${rel#./}" | normalize)"
      printf '%s\tmode=%s\thash=%s\n' "$norm_rel" "$(file_mode "$path")" "$(normalized_file_hash "$path")"
    done
  )
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
    active_start_write)
      rm -rf "$case_old/.kimiflow/session" "$case_new/.kimiflow/session"
      ;;
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
    launcher_no_kimiflow)
      rm -rf "$case_old/.kimiflow" "$case_new/.kimiflow"
      ;;
    launcher_invalid_map_json)
      mkdir -p "$case_old/.kimiflow/project" "$case_new/.kimiflow/project"
      printf '{bad json\n' > "$case_old/.kimiflow/project/INDEX.json"
      printf '{bad json\n' > "$case_new/.kimiflow/project/INDEX.json"
      ;;
    launcher_scalar_map_json)
      mkdir -p "$case_old/.kimiflow/project" "$case_new/.kimiflow/project"
      printf 'null\n' > "$case_old/.kimiflow/project/INDEX.json"
      printf 'null\n' > "$case_new/.kimiflow/project/INDEX.json"
      ;;
    launcher_stale_plugin_cache)
      mkdir -p "$case_old/.codex-plugin" "$case_new/.codex-plugin" "$case_old/fake-cache/.codex-plugin" "$case_new/fake-cache/.codex-plugin"
      printf '{"version":"9.9.9"}\n' > "$case_old/.codex-plugin/plugin.json"
      printf '{"version":"9.9.9"}\n' > "$case_new/.codex-plugin/plugin.json"
      printf '{"version":"0.0.1"}\n' > "$case_old/fake-cache/.codex-plugin/plugin.json"
      printf '{"version":"0.0.1"}\n' > "$case_new/fake-cache/.codex-plugin/plugin.json"
      ;;
    active_append_preview|active_finish_preview|active_park_write|active_fail_write|active_abort_write|active_prompt_payload|active_stop_gate|active_refresh_baseline_write)
      write_active_fixture "$case_old"
      write_active_fixture "$case_new"
      ;;
    active_mark_built_write|active_mark_accepted_write|active_mark_rejected_write|active_drop_item_write)
      write_active_fixture "$case_old"
      write_active_fixture "$case_new"
      write_active_item "$case_old" "pending"
      write_active_item "$case_new" "pending"
      ;;
    clarify_markdown_state|plan_blocker_markdown_state)
      write_gate_fixture "$case_old"
      write_gate_fixture "$case_new"
      ;;
  esac

  old_args=()
  new_args=()
  for arg in ${args[@]+"${args[@]}"}; do
    if [ "$arg" = "__REPO__" ]; then
      old_args+=("$case_old")
      new_args+=("$case_new")
    elif [ "$arg" = "__RUN__" ]; then
      old_args+=("$case_old/.kimiflow/demo")
      new_args+=("$case_new/.kimiflow/demo")
    else
      old_args+=("$arg")
      new_args+=("$arg")
    fi
  done

  old_env=(HOME="$HOME_DIR" KIMIFLOW_OBSIDIAN_URL= KIMIFLOW_OBSIDIAN_API_KEY=)
  new_env=(HOME="$HOME_DIR" KIMIFLOW_OBSIDIAN_URL= KIMIFLOW_OBSIDIAN_API_KEY=)
  case "$label" in
    active_*)
      mkdir -p "$case_old/fake-plugin" "$case_new/fake-plugin"
      old_env+=(KIMIFLOW_PLUGIN_ROOT="$case_old/fake-plugin")
      new_env+=(KIMIFLOW_PLUGIN_ROOT="$case_new/fake-plugin")
      ;;
    launcher_stale_plugin_cache)
      old_env+=(KIMIFLOW_PLUGIN_ROOT="$case_old/fake-cache")
      new_env+=(KIMIFLOW_PLUGIN_ROOT="$case_new/fake-cache")
      ;;
  esac

  old_stdin="/dev/null"
  new_stdin="/dev/null"
  case "$label" in
    active_prompt_payload|active_stop_gate)
      old_stdin="$WORK/old.stdin"
      new_stdin="$WORK/new.stdin"
      if [ "$label" = "active_stop_gate" ]; then
        printf '{"cwd":"%s"}' "$case_old" > "$old_stdin"
        printf '{"cwd":"%s"}' "$case_new" > "$new_stdin"
      else
        printf '{"cwd":"%s","prompt":"must not persist"}' "$case_old" > "$old_stdin"
        printf '{"cwd":"%s","prompt":"must not persist"}' "$case_new" > "$new_stdin"
      fi
      ;;
  esac

  (cd "$case_old" && env "${old_env[@]}" bash "$old_script" ${old_args[@]+"${old_args[@]}"} < "$old_stdin") > "$WORK/o.out" 2> "$WORK/o.err"; o_code=$?
  (cd "$case_new" && env "${new_env[@]}" bash "$new_script" ${new_args[@]+"${new_args[@]}"} < "$new_stdin") > "$WORK/n.out" 2> "$WORK/n.err"; n_code=$?

  # Removed subsystems: pre-R1 launcher-status emitted `.background`,
  # `.agentic_readiness` (plus their `launcher.status.counts.background_*` mirror),
  # `.feature_checks` (plus its `launcher.status.counts.feature_check_findings_open`
  # mirror), and `.improvements` (plus its `launcher.status.counts.improvements_open`
  # mirror); all were deleted with the Background Handles / Agentic Readiness layers,
  # the existing-feature-check simplification, and the Workqueue close-back removal.
  # Strip them from the launcher snapshot on both sides so parity keeps comparing the
  # shared fields against the frozen baseline.
  case "$label" in
    launcher_*)
      # Guard: a reintroduced removed field in the NEW launcher must surface as a
      # real divergence — strip the new side only when it is clean; the frozen
      # baseline side is always stripped.
      strip_targets="$WORK/o.out"
      if jq -e 'has("background") or has("agentic_readiness") or has("feature_checks") or has("improvements")
                or ((.launcher.status.counts // {}) | has("background_collectable") or has("background_stale") or has("feature_check_findings_open") or has("improvements_open"))' \
                "$WORK/n.out" >/dev/null 2>&1; then
        : # leave n.out unstripped -> diff against the stripped baseline flags the reintroduction
      else
        strip_targets="$strip_targets $WORK/n.out"
      fi
      for f in $strip_targets; do
        if jq -e . "$f" >/dev/null 2>&1; then
          jq 'del(.background, .agentic_readiness, .feature_checks, .improvements)
              | if (.launcher.status.counts | type) == "object"
                then .launcher.status.counts |= del(.background_collectable, .background_stale, .feature_check_findings_open, .improvements_open)
                else . end' "$f" > "$f.stripped" && mv "$f.stripped" "$f"
        fi
      done
      ;;
  esac

  normalize < "$WORK/o.out" > "$WORK/o.out.norm"
  normalize < "$WORK/o.err" > "$WORK/o.err.norm"
  normalize < "$WORK/n.out" > "$WORK/n.out.norm"
  normalize < "$WORK/n.err" > "$WORK/n.err.norm"
  file_state "$case_old" > "$WORK/o.files.norm"
  file_state "$case_new" > "$WORK/n.files.norm"

  diverged=""
  [ "$o_code" != "$n_code" ] && diverged="${diverged}exit($o_code!=$n_code) "
  cmp -s "$WORK/o.out.norm" "$WORK/n.out.norm" || diverged="${diverged}stdout "
  cmp -s "$WORK/o.err.norm" "$WORK/n.err.norm" || diverged="${diverged}stderr "
  cmp -s "$WORK/o.files.norm" "$WORK/n.files.norm" || diverged="${diverged}file-state "

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
  if ! cmp -s "$WORK/o.files.norm" "$WORK/n.files.norm"; then
    printf '  [file-state diff]\n'
    diff -u "$WORK/o.files.norm" "$WORK/n.files.norm" | sed 's/^/  /' || true
  fi
}

CASES=(
  "active_status_none::active-run.sh::status|--root|__REPO__"
  "active_malformed_arg::active-run.sh::status|--root|__REPO__|--bogus"
  "active_start_write::active-run.sh::start|--root|__REPO__|--run|.kimiflow/demo|--write"
  "active_append_preview::active-run.sh::append-item|--root|__REPO__|--title|Do thing"
  "active_mark_built_write::active-run.sh::mark-built|--root|__REPO__|--id|item_001|--write"
  "active_mark_accepted_write::active-run.sh::mark-accepted|--root|__REPO__|--id|item_001|--write"
  "active_mark_rejected_write::active-run.sh::mark-rejected|--root|__REPO__|--id|item_001|--reason|needs work|--write"
  "active_drop_item_write::active-run.sh::drop-item|--root|__REPO__|--id|item_001|--reason|out of scope|--write"
  "active_refresh_baseline_write::active-run.sh::refresh-baseline|--root|__REPO__|--write"
  "active_finish_preview::active-run.sh::finish|--root|__REPO__"
  "active_park_write::active-run.sh::park|--root|__REPO__|--reason|waiting|--write"
  "active_fail_write::active-run.sh::fail|--root|__REPO__|--reason|failed|--write"
  "active_abort_write::active-run.sh::abort|--root|__REPO__|--reason|aborted|--write"
  "active_prompt_payload::active-run.sh::prompt-context"
  "active_stop_gate::active-run.sh::stop-gate"
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
  "launcher_scalar_map_json::launcher-status.sh::--root|__REPO__"
  "launcher_pretty::launcher-status.sh::--root|__REPO__|--pretty"
  "launcher_full::launcher-status.sh::--root|__REPO__|--full"
  "launcher_stale_plugin_cache::launcher-status.sh::--root|__REPO__"
  "clarify_missing_dir::clarify-gate.sh::$WORK/missing-run"
  "clarify_markdown_state::clarify-gate.sh::__RUN__"
  "plan_blocker_missing_dir::plan-blocker-gate.sh::$WORK/missing-run"
  "plan_blocker_markdown_state::plan-blocker-gate.sh::__RUN__"
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
