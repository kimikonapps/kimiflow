#!/usr/bin/env bash
# kimiflow — unit tests for map-staleness-nudge.sh.
# Isolation: temp git repo under mktemp; the real repo is never touched.
set -u

HOOKS_DIR="$(cd "$(dirname "$0")" && pwd)"
NUDGE="$HOOKS_DIR/map-staleness-nudge.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1' want '$2')"; fi; }
assert_has() { case "$1" in *"$2"*) pass "$3" ;; *) fail "$3 (missing '$2' in: $1)" ;; esac; }
assert_no() { case "$1" in *"$2"*) fail "$3 (unexpected '$2' in: $1)" ;; *) pass "$3" ;; esac; }

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — map-staleness-nudge uses jq"; exit 0
fi

hash_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print "sha256:" $1}'
  else
    sha256sum "$1" | awk '{print "sha256:" $1}'
  fi
}

# Build a repo with a project map. $1 = current|stale.
mk_repo() {
  rm -rf "$REPO"
  mkdir -p "$REPO/hooks" "$REPO/.kimiflow/project"
  ( cd "$REPO" && git init -q && git config user.email "kimiflow@example.test" && git config user.name "kimiflow test" )
  printf 'one\n' > "$REPO/hooks/a.sh"
  ( cd "$REPO" && git add hooks/a.sh && git commit -q -m init )
  local base; base="$(cd "$REPO" && git rev-parse --short HEAD)"
  jq -n --arg base "$base" --arg h "$(hash_file "$REPO/hooks/a.sh")" '{
    schema_version: 1, language: "de", scan_depth: "standard", baseline_commit: $base,
    created_at: "2026-06-25T00:00:00Z",
    sections: {hooks: {files: ["hooks/a.sh"], prefixes: ["hooks/"], file_hashes: {"hooks/a.sh": $h}, last_scanned_commit: $base, status: "current"}},
    artifacts: {}
  }' > "$REPO/.kimiflow/project/INDEX.json"
  if [ "$1" = "stale" ]; then printf 'two\n' > "$REPO/hooks/a.sh"; fi
}

stop_input() { jq -nc --arg d "$REPO" '{cwd: $d, stop_hook_active: false}'; }

# AC-7 — stale map, no rate-limit → systemMessage with N = stale + potentially_stale, exit 0
mk_repo stale
out="$(stop_input | bash "$NUDGE")"; rc=$?
assert_has "$out" '"systemMessage"' "nudge_emits_systemmessage_when_stale"
assert_has "$out" 'Project map: 1 section(s) need refresh.' "nudge_systemmessage_counts_stale"
assert_eq "$rc" "0" "nudge_stale_exit0"
[ -f "$REPO/.kimiflow/.map-nudge-stamp" ] && pass "nudge_writes_stamp_when_stale" || fail "nudge_writes_stamp_when_stale"

# AC-8 — current map → no systemMessage, stamp still written, exit 0
mk_repo current
out="$(stop_input | bash "$NUDGE")"; rc=$?
assert_no "$out" '"systemMessage"' "nudge_silent_when_current"
assert_eq "$rc" "0" "nudge_current_exit0"
[ -f "$REPO/.kimiflow/.map-nudge-stamp" ] && pass "nudge_writes_stamp_when_current" || fail "nudge_writes_stamp_when_current"

# AC-9 — rate-limited: stamp carries today's date → status is NOT called, no systemMessage.
# Use a stub project-map-status.sh so we can prove the status sweep did not run.
PLUG="$WORK/plug"
mkdir -p "$PLUG"
MARKER="$WORK/pms-called"
cat > "$PLUG/project-map-status.sh" <<EOF
#!/usr/bin/env bash
echo called >> "$MARKER"
printf 'PROJECT_MAP\tpartially_stale\tstale=2\tpotentially_stale=1\tunknown=0\taffected_stale=0\tindex=x\n'
EOF
chmod +x "$PLUG/project-map-status.sh"
cp "$NUDGE" "$PLUG/map-staleness-nudge.sh"
chmod +x "$PLUG/map-staleness-nudge.sh"

mk_repo stale
printf '%s\n' "$(date -u '+%Y-%m-%d')" > "$REPO/.kimiflow/.map-nudge-stamp"
rm -f "$MARKER"
out="$(stop_input | bash "$PLUG/map-staleness-nudge.sh")"; rc=$?
assert_eq "$rc" "0" "nudge_rate_limited_exit0"
[ ! -f "$MARKER" ] && pass "nudge_rate_limited" || fail "nudge_rate_limited (status was called despite today's stamp)"
assert_no "$out" '"systemMessage"' "nudge_rate_limited_silent"

# Control: without the stamp the stub IS called (proves the marker wiring is real)
rm -f "$REPO/.kimiflow/.map-nudge-stamp" "$MARKER"
out="$(stop_input | bash "$PLUG/map-staleness-nudge.sh")"
[ -f "$MARKER" ] && pass "nudge_runs_status_when_not_rate_limited" || fail "nudge_runs_status_when_not_rate_limited"
assert_has "$out" '"systemMessage"' "nudge_emits_with_stub_when_not_rate_limited"

# AC-10 — missing map → exit 0 with no output
rm -rf "$REPO"; mkdir -p "$REPO"; ( cd "$REPO" && git init -q )
out="$(stop_input | bash "$NUDGE")"; rc=$?
assert_eq "$out" "" "nudge_silent_when_no_map"
assert_eq "$rc" "0" "nudge_no_map_exit0"

# AC-10 — stop_hook_active:true (loop continuation) → exit 0 with no output even on a stale map
mk_repo stale
out="$(jq -nc --arg d "$REPO" '{cwd: $d, stop_hook_active: true}' | bash "$NUDGE")"; rc=$?
assert_eq "$out" "" "nudge_exit0_when_no_map_or_loop"
assert_eq "$rc" "0" "nudge_loop_break_exit0"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
