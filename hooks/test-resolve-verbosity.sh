#!/usr/bin/env bash
# kimiflow — unit tests for resolve-verbosity.sh (the display-verbosity helper).
# Self-contained, no framework. Isolation: a fake $HOME + a NON-git temp project
# dir, so the real ~/.claude / ~/.codex verbosity files and this repo are never touched.
# Run: bash hooks/test-resolve-verbosity.sh   (exit 0 = all green)
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/resolve-verbosity.sh"

WORK="$(mktemp -d)"
PROJ="$WORK/proj"          # cwd for project-file resolution (non-git → gitroot falls back to pwd)
FAKE_HOME="$WORK/home"
FAKE_CODEX_HOME="$WORK/codex-home"
FAKE_PI_HOME="$WORK/pi-agent"
PROJ_FILE_REL=".kimiflow/verbosity"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

reset() { rm -rf "$PROJ" "$FAKE_HOME" "$FAKE_CODEX_HOME" "$FAKE_PI_HOME"; mkdir -p "$PROJ" "$FAKE_HOME" "$FAKE_CODEX_HOME" "$FAKE_PI_HOME"; }
set_project() { mkdir -p "$PROJ/.kimiflow"; printf '%s\n' "$1" > "$PROJ/.kimiflow/verbosity"; }
set_global()  { mkdir -p "$FAKE_HOME/.claude/kimiflow"; printf '%s\n' "$1" > "$FAKE_HOME/.claude/kimiflow/verbosity"; }
set_codex_global() { mkdir -p "$FAKE_CODEX_HOME/kimiflow"; printf '%s\n' "$1" > "$FAKE_CODEX_HOME/kimiflow/verbosity"; }
set_pi_global() { mkdir -p "$FAKE_PI_HOME/kimiflow"; printf '%s\n' "$1" > "$FAKE_PI_HOME/kimiflow/verbosity"; }
run() {
  (
    cd "$PROJ" \
      && HOME="$FAKE_HOME" \
        CLAUDE_HOME="$FAKE_HOME/.claude" \
        KIMIFLOW_HOST=claude \
        "$SCRIPT" "$@"
  )
}
run_codex() { ( cd "$PROJ" && HOME="$FAKE_HOME" CODEX_HOME="$FAKE_CODEX_HOME" KIMIFLOW_HOST=codex "$SCRIPT" "$@" ); }
run_pi() { ( cd "$PROJ" && HOME="$FAKE_HOME" PI_CODING_AGENT_DIR="$FAKE_PI_HOME" KIMIFLOW_HOST=pi "$SCRIPT" "$@" ); }
run_worker() { ( cd "$PROJ" && HOME="$FAKE_HOME" CLAUDE_HOME="$FAKE_HOME/.claude" KIMIFLOW_HOST=claude KIMIFLOW_WORKER_VERBOSITY="$1" "$SCRIPT" "${@:2}" ); }
assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1' want '$2')"; fi; }

# --- AC-1: flag overrides project/global ---
reset; set_project quiet
assert_eq "$(run --flag verbose)" "verbose" "test_flag_wins"

# --- AC-1b: internal worker handoff is below an explicit flag, above files, and read-only ---
reset; set_project verbose; set_global verbose
assert_eq "$(run_worker quiet get)" "quiet" "test_worker_handoff_over_files"
assert_eq "$(run_worker quiet get --flag verbose)" "verbose" "test_flag_over_worker_handoff"
assert_eq "$(run_worker nonsense get)" "verbose" "test_invalid_worker_handoff_skipped"
if [ "$(cat "$PROJ/.kimiflow/verbosity")" = "verbose" ] && [ "$(cat "$FAKE_HOME/.claude/kimiflow/verbosity")" = "verbose" ]; then
  pass "test_worker_handoff_no_persist"
else
  fail "test_worker_handoff_no_persist"
fi

# --- AC-2: project over global ---
reset; set_project quiet; set_global verbose
assert_eq "$(run)" "quiet" "test_project_over_global"

# --- AC-3: global fallback (no flag, no project) ---
reset; set_global verbose
assert_eq "$(run)" "verbose" "test_global_fallback"
reset; set_codex_global quiet
assert_eq "$(run_codex)" "quiet" "test_codex_global_fallback"
reset; set_pi_global verbose
assert_eq "$(run_pi)" "verbose" "test_pi_global_fallback"

# --- AC-3b: linked Fleet worktree inherits the primary project setting ---
reset
GIT_MAIN="$WORK/git-main"
GIT_LINKED="$WORK/git-linked"
mkdir -p "$GIT_MAIN/.kimiflow"
( cd "$GIT_MAIN" && git init -q && git config user.email test@example.test && git config user.name Test \
  && printf 'tracked\n' > tracked.txt && git add tracked.txt && git commit -qm init )
printf 'quiet\n' > "$GIT_MAIN/.kimiflow/verbosity"
git -C "$GIT_MAIN" worktree add -q -b fleet-test "$GIT_LINKED"
linked_level="$(cd "$GIT_LINKED" && HOME="$FAKE_HOME" CODEX_HOME="$FAKE_CODEX_HOME" KIMIFLOW_HOST=codex "$SCRIPT" get)"
assert_eq "$linked_level" "quiet" "test_linked_worktree_inherits_primary_project"
mkdir -p "$GIT_LINKED/.kimiflow"
printf 'verbose\n' > "$GIT_LINKED/.kimiflow/verbosity"
linked_override="$(cd "$GIT_LINKED" && HOME="$FAKE_HOME" CODEX_HOME="$FAKE_CODEX_HOME" KIMIFLOW_HOST=codex "$SCRIPT" get)"
assert_eq "$linked_override" "verbose" "test_linked_worktree_override_wins"

# --- AC-4: default balanced (nothing) ---
reset
assert_eq "$(run)" "balanced" "test_default_balanced"

# --- AC-5: invalid source skipped ---
reset; set_project "scope=large"; set_global verbose
assert_eq "$(run)" "verbose" "test_invalid_source_skipped"

# --- AC-11: set roundtrip (mkdir -p + write==read) ---
reset
out="$(run set global verbose)"
if [ -f "$FAKE_HOME/.claude/kimiflow/verbosity" ]; then pass "test_set_global_creates_file"; else fail "test_set_global_creates_file"; fi
assert_eq "$(run)" "verbose" "test_set_global_roundtrip"
reset
out="$(run_codex set global verbose)"
if [ -f "$FAKE_CODEX_HOME/kimiflow/verbosity" ]; then pass "test_set_codex_global_creates_file"; else fail "test_set_codex_global_creates_file"; fi
assert_eq "$(run_codex)" "verbose" "test_set_codex_global_roundtrip"
reset
out="$(run_pi set global quiet)"
if [ -f "$FAKE_PI_HOME/kimiflow/verbosity" ]; then pass "test_set_pi_global_creates_file"; else fail "test_set_pi_global_creates_file"; fi
assert_eq "$(run_pi)" "quiet" "test_set_pi_global_roundtrip"
if [ -e "$FAKE_HOME/.claude/kimiflow/verbosity" ]; then fail "test_pi_global_does_not_touch_claude"; else pass "test_pi_global_does_not_touch_claude"; fi
reset
run set project quiet >/dev/null
if [ -f "$PROJ/.kimiflow/verbosity" ]; then pass "test_set_project_creates_file"; else fail "test_set_project_creates_file"; fi
assert_eq "$(run)" "quiet" "test_set_project_roundtrip"
# invalid level/scope → exit 1, no file
reset
if run set global nonsense >/dev/null 2>&1; then fail "test_set_invalid_level_rejected"; else pass "test_set_invalid_level_rejected"; fi
if [ -f "$FAKE_HOME/.claude/kimiflow/verbosity" ]; then fail "test_set_invalid_level_nofile"; else pass "test_set_invalid_level_nofile"; fi
reset
if run set bogus verbose >/dev/null 2>&1; then fail "test_set_invalid_scope_rejected"; else pass "test_set_invalid_scope_rejected"; fi

# --- AC-11b: set failure branch (mkdir fails → exit 1, no false success) ---
reset; rm -rf "$FAKE_HOME"; mkdir -p "$FAKE_HOME"; : > "$FAKE_HOME/.claude"   # .claude is a FILE
if run set global verbose >/dev/null 2>&1; then fail "test_set_failure_exit1"; else pass "test_set_failure_exit1"; fi

# --- AC-13: flag/get never persists ---
reset
run --flag verbose >/dev/null
if [ -f "$PROJ/.kimiflow/verbosity" ] || [ -f "$FAKE_HOME/.claude/kimiflow/verbosity" ]; then
  fail "test_flag_no_persist"
else
  pass "test_flag_no_persist"
fi

# --- robustness: --flag with no value degrades to default, no crash ---
reset
out="$(run --flag)"; rc=$?
if [ "$rc" -eq 0 ] && [ "$out" = "balanced" ]; then pass "test_flag_missing_value_degrades"; else fail "test_flag_missing_value_degrades (rc=$rc out='$out')"; fi

# --- AC-14: onboard-check → ASK only when nothing is set anywhere, else SKIP ---
reset
assert_eq "$(run onboard-check)" "ASK" "test_onboard_ask_when_unset"
reset; set_project quiet
assert_eq "$(run onboard-check)" "SKIP" "test_onboard_skip_project_set"
reset; set_global verbose
assert_eq "$(run onboard-check)" "SKIP" "test_onboard_skip_global_set"
reset
assert_eq "$(run onboard-check --flag verbose)" "SKIP" "test_onboard_skip_with_flag"
# onboard-check is read-only — like get it must never persist anything
reset
run onboard-check >/dev/null
if [ -f "$PROJ/.kimiflow/verbosity" ] || [ -f "$FAKE_HOME/.claude/kimiflow/verbosity" ]; then
  fail "test_onboard_no_persist"
else
  pass "test_onboard_no_persist"
fi

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
