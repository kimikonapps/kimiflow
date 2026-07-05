#!/usr/bin/env bash
# kimiflow — golden-snapshot harness for the kimiflow_core ports.
#
# WHAT THIS LOCKS
#   For each curated case it runs one kimiflow_core CLI (active-run,
#   project-map-status, launcher-status, clarify-gate, plan-blocker-gate)
#   against a fixture and freezes the result — exit code, normalized stdout,
#   normalized stderr, and post-run file-state (mode + normalized content hash
#   of every file) — into a checked-in golden under hooks/golden/<case>.snap.
#   A run recomputes each snapshot and fails on any drift from its golden.
#   The goldens ARE the expected current behavior; this is a behavior lock,
#   not a port-fidelity check.
#
#   History: this harness used to materialize the frozen pre-R1 Bash baseline
#   (git archive 72282e6) and diff working-tree-vs-baseline, stripping an
#   ever-growing list of fields removed from the ports (background,
#   agentic_readiness, feature_checks, improvements, awaiting_user, ...). The
#   port-fidelity guarantee retired once the Python ports went live; the
#   growing strip-list was the signal to switch to golden snapshots. No git
#   archive, network, or old-side execution is needed anymore.
#
# REGENERATING GOLDENS (only after an INTENTIONAL behavior change)
#   UPDATE_GOLDEN=1 bash hooks/test-kimiflow-core-parity.sh
#   then REVIEW `git diff hooks/golden/` before committing. A golden change you
#   did not intend is a regression you are about to bless — never regenerate
#   just to "make it green" without reading exactly which fields moved and why.
#
# DETERMINISM
#   Only known nondeterminism is normalized (see normalize()): tmp paths, ISO
#   timestamps, 40-hex commit SHAs, and the plugin version. The fixture repo
#   commit is pinned (fixed identity + author/committer date) so its short SHA
#   is byte-reproducible across machines. Content hashes are taken over the
#   normalized bytes, so goldens are portable (macOS dev ↔ Linux CI).
set -u
# Pin collation/byte semantics: the @@files@@ block is `find | sort`-ordered and
# goldens are byte-compared, so an unpinned locale (macOS de_AT.UTF-8 vs. CI's
# LC_ALL=C) would flip the sort order and fail every case.
export LC_ALL=C

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GOLDEN_DIR="$ROOT/hooks/golden"
UPDATE="${UPDATE_GOLDEN:-0}"
WORK="$(mktemp -d)"
WORK_REAL="$(cd "$WORK" 2>/dev/null && pwd -P || printf '%s' "$WORK")"
trap 'rm -rf "$WORK"' EXIT

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
# Pin identity + dates so the commit SHA (and its short form, which surfaces in
# INDEX.json / project-map output) is byte-reproducible across runs and machines.
GIT_AUTHOR_DATE='2026-07-02 00:00:00 +0000' \
GIT_COMMITTER_DATE='2026-07-02 00:00:00 +0000' \
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

# Normalizes the only known nondeterminism so goldens are byte-stable and
# portable. Keep this list tight: every entry is a documented volatile field,
# NOT a place to hide removed-field drift (that is exactly the trap the old
# baseline strip-list fell into).
normalize() {
  sed -E \
    -e "s#$ROOT#ROOT#g" \
    -e "s#$REPO#REPO#g" \
    -e "s#$WORK_REAL#WORK#g" \
    -e "s#$WORK#WORK#g" \
    -e 's#WORK/cases/[A-Za-z0-9_.:-]+#REPO#g' \
    -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z/TIMESTAMP/g' \
    -e 's/[0-9a-f]{40}/COMMIT/g' \
    -e 's/"version": ?"[0-9]+\.[0-9]+\.[0-9]+"/"version": "VERSION"/g'
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
  local label="$1" script="$2" argstr="$3" new_script case_dir arg args new_args new_stdin golden
  new_script="$ROOT/hooks/$script"
  args=()
  [ -n "$argstr" ] && IFS='|' read -r -a args <<< "$argstr"

  mkdir -p "$WORK/cases"
  case_dir="$WORK/cases/${label}"
  rm -rf "$case_dir"
  cp -R "$REPO" "$case_dir"

  case "$label" in
    active_start_write)
      rm -rf "$case_dir/.kimiflow/session"
      ;;
    project_map_status_current|project_map_coverage_current|project_map_index_symbols|project_map_refresh_section|project_map_refresh_changed_new)
      write_project_map_index "$case_dir"
      ;;
  esac
  case "$label" in
    project_map_refresh_changed_new)
      printf 'new\n' > "$case_dir/hooks/new.sh"
      ;;
  esac
  case "$label" in
    launcher_no_kimiflow)
      rm -rf "$case_dir/.kimiflow"
      ;;
    launcher_invalid_map_json)
      mkdir -p "$case_dir/.kimiflow/project"
      printf '{bad json\n' > "$case_dir/.kimiflow/project/INDEX.json"
      ;;
    launcher_scalar_map_json)
      mkdir -p "$case_dir/.kimiflow/project"
      printf 'null\n' > "$case_dir/.kimiflow/project/INDEX.json"
      ;;
    launcher_stale_plugin_cache)
      mkdir -p "$case_dir/.codex-plugin" "$case_dir/fake-cache/.codex-plugin"
      printf '{"version":"9.9.9"}\n' > "$case_dir/.codex-plugin/plugin.json"
      printf '{"version":"0.0.1"}\n' > "$case_dir/fake-cache/.codex-plugin/plugin.json"
      ;;
    active_append_preview|active_finish_preview|active_park_write|active_fail_write|active_abort_write|active_prompt_payload|active_stop_gate|active_refresh_baseline_write)
      write_active_fixture "$case_dir"
      ;;
    active_mark_built_write|active_mark_accepted_write|active_mark_rejected_write|active_drop_item_write)
      write_active_fixture "$case_dir"
      write_active_item "$case_dir" "pending"
      ;;
    clarify_markdown_state|plan_blocker_markdown_state)
      write_gate_fixture "$case_dir"
      ;;
  esac

  new_args=()
  for arg in ${args[@]+"${args[@]}"}; do
    if [ "$arg" = "__REPO__" ]; then
      new_args+=("$case_dir")
    elif [ "$arg" = "__RUN__" ]; then
      new_args+=("$case_dir/.kimiflow/demo")
    else
      new_args+=("$arg")
    fi
  done

  new_env=(HOME="$HOME_DIR" KIMIFLOW_OBSIDIAN_URL= KIMIFLOW_OBSIDIAN_API_KEY=)
  case "$label" in
    active_*)
      mkdir -p "$case_dir/fake-plugin"
      new_env+=(KIMIFLOW_PLUGIN_ROOT="$case_dir/fake-plugin")
      ;;
    launcher_stale_plugin_cache)
      new_env+=(KIMIFLOW_PLUGIN_ROOT="$case_dir/fake-cache")
      ;;
  esac

  new_stdin="/dev/null"
  case "$label" in
    active_prompt_payload|active_stop_gate)
      new_stdin="$WORK/new.stdin"
      if [ "$label" = "active_stop_gate" ]; then
        printf '{"cwd":"%s"}' "$case_dir" > "$new_stdin"
      else
        printf '{"cwd":"%s","prompt":"must not persist"}' "$case_dir" > "$new_stdin"
      fi
      ;;
  esac

  (cd "$case_dir" && env "${new_env[@]}" bash "$new_script" ${new_args[@]+"${new_args[@]}"} < "$new_stdin") > "$WORK/n.out" 2> "$WORK/n.err"; n_code=$?

  # Freeze/compare the full observable result of this case.
  {
    printf '@@exit@@\n%s\n' "$n_code"
    printf '@@stdout@@\n'; normalize < "$WORK/n.out"
    printf '@@stderr@@\n'; normalize < "$WORK/n.err"
    printf '@@files@@\n'; file_state "$case_dir"
  } > "$WORK/snapshot"

  golden="$GOLDEN_DIR/${label}.snap"
  if [ "$UPDATE" = "1" ]; then
    mkdir -p "$GOLDEN_DIR"
    cp "$WORK/snapshot" "$golden"
    ok "$label (golden written)"
    return 0
  fi

  if [ ! -f "$golden" ]; then
    bad "$label — missing golden: hooks/golden/${label}.snap (run UPDATE_GOLDEN=1 to create)"
    return 1
  fi

  if cmp -s "$golden" "$WORK/snapshot"; then
    ok "$label"
    return 0
  fi

  bad "$label — snapshot diverged from golden"
  diff -u "$golden" "$WORK/snapshot" | sed 's/^/  /' || true
  return 1
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

# Golden-dir <-> CASES coupling: a removed case must not leave its .snap behind
# (silent coverage shrink), and the .snap count must match the case count exactly.
# UPDATE mode prunes orphans; verify mode fails on any mismatch.
golden_guard() {
  local expected="$WORK/expected.snaps" actual="$WORK/actual.snaps" orphans missing entry f
  for entry in "${CASES[@]}"; do printf '%s.snap\n' "${entry%%::*}"; done | sort > "$expected"
  (cd "$GOLDEN_DIR" 2>/dev/null && find . -maxdepth 1 -name '*.snap' -exec basename {} \; || true) | sort > "$actual"
  orphans="$(comm -13 "$expected" "$actual")"
  missing="$(comm -23 "$expected" "$actual")"
  if [ "$UPDATE" = "1" ]; then
    if [ -n "$orphans" ]; then
      while IFS= read -r f; do
        rm -f "$GOLDEN_DIR/$f"
        printf 'pruned orphan golden: hooks/golden/%s\n' "$f"
      done <<< "$orphans"
    fi
    return 0
  fi
  if [ -n "$orphans" ] || [ -n "$missing" ]; then
    bad "golden_guard — snap files do not match CASES (expected ${#CASES[@]})"
    [ -z "$orphans" ] || printf '  orphan (no case): %s\n' $orphans
    [ -z "$missing" ] || printf '  missing (case has no golden): %s\n' $missing
    return 1
  fi
  ok "golden_guard (${#CASES[@]} cases == $(wc -l < "$actual" | tr -d ' ') goldens)"
}
golden_guard

echo "----"
if [ "$UPDATE" = "1" ]; then
  echo "GOLDENS WRITTEN (${#CASES[@]} cases) — review \`git diff hooks/golden/\` before committing"
  exit 0
fi
if [ "$FAILS" -eq 0 ]; then
  echo "ALL GREEN"
  exit 0
fi
echo "$FAILS DIVERGENCES"
exit 1
