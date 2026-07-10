#!/usr/bin/env bash
# kimiflow — unit tests for test-gate.sh (the opt-in Stop hook that blocks finishing
# on red tests and refuses git-tracked markers). Named *-unit.sh because the script
# under test is itself test-gate.sh — that name is taken (same convention as
# test-weakening-scan-unit.sh). Black-box: drives the REAL hook with crafted Stop
# payloads against a throwaway git repo. No framework.
# No-jq cases run the hook under a PATH that omits jq (symlink the tools it needs);
# the test itself keeps jq to build payloads. Run: bash hooks/test-gate-unit.sh
set -u

HOOK="$(cd "$(dirname "$0")" && pwd)/test-gate.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
ERR="$WORK/err"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — this test builds payloads with jq"; exit 0
fi

# No-jq PATH: symlink the tools the hook's no-jq path + the eval'd markers need.
# Resolve with `command -v` inside the script (alias-free in non-interactive bash).
REALBASH="$(command -v bash)"
NOJQ="$WORK/nojq-bin"; mkdir -p "$NOJQ"
for t in cat head git tail grep touch; do s="$(command -v "$t")"; [ -n "$s" ] && ln -s "$s" "$NOJQ/$t"; done

reset_repo() {
  rm -rf "$REPO"; git init -q "$REPO"
  git -C "$REPO" config user.email t@example.com; git -C "$REPO" config user.name tester
  mkdir -p "$REPO/.kimiflow"; rm -f "$REPO/SENTINEL.flag"
}
set_marker() { printf '%s\n' "$1" > "$REPO/.kimiflow/test-gate"; }            # untracked by default
track_marker() { git -C "$REPO" add .kimiflow/test-gate >/dev/null 2>&1; git -C "$REPO" commit -q -m marker; }
payload() { jq -nc --argjson s "${1:-false}" --arg d "$REPO" '{stop_hook_active:$s, cwd:$d}'; }
run_jq()   { payload "$1" | "$HOOK" 2>"$ERR"; }                                # jq present → hook cds via cwd
run_nojq() { payload "$1" | ( cd "$REPO" && PATH="$NOJQ" "$REALBASH" "$HOOK" ) 2>"$ERR"; }
run_jq_session() {
  jq -nc --arg d "$REPO" --arg sid "$1" '{stop_hook_active:false, cwd:$d, session_id:$sid}' \
    | KIMIFLOW_HOST=codex "$HOOK" 2>"$ERR"
}
set_active_owner() {
  mkdir -p "$REPO/.kimiflow/demo" "$REPO/.kimiflow/session"
  printf 'Status: active\nAffected files: src/a.txt\n' > "$REPO/.kimiflow/demo/STATE.md"
  printf '{"schema_version":1,"status":"active","run":".kimiflow/demo","host":"codex","owner":{"host":"codex","session_id":"owner-session"},"started_head":"NOT VERIFIED","last_checked_head":"NOT VERIFIED"}\n' \
    > "$REPO/.kimiflow/session/ACTIVE_RUN.json"
}

# Block JSON is pretty-printed by the jq path ("decision": "block") and compact by
# the no-jq path ("decision":"block") — match both, whitespace-tolerant.
BLOCK_RE='"decision"[[:space:]]*:[[:space:]]*"block"'
assert_block()   { if printf '%s' "$1" | grep -qE "$BLOCK_RE"; then pass "$2"; else fail "$2 (expected BLOCK, got: ${1:-<none>})"; fi; }
assert_noblock() { if printf '%s' "$1" | grep -qE "$BLOCK_RE"; then fail "$2 (expected no block, got BLOCK)"; else pass "$2"; fi; }
assert_has()     { if printf '%s' "$1" | grep -qF "$2"; then pass "$3"; else fail "$3 (missing '$2' in: ${1:-<empty>})"; fi; }
assert_nofile()  { if [ -e "$1" ]; then fail "$2 (eval ran — file exists)"; else pass "$2"; fi; }

# B1 — no marker → no-op.
reset_repo
assert_noblock "$(run_jq false)" "no_marker_noop"

# B2 — untracked green marker → no block.
reset_repo; set_marker "true"
assert_noblock "$(run_jq false)" "green_marker_allows"

# B3 — untracked red marker → block, reason carries the output tail.
reset_repo; set_marker 'echo fail-tail-marker; exit 1'
out="$(run_jq false)"
assert_block "$out" "red_marker_blocks"
assert_has   "$out" "fail-tail-marker" "red_marker_block_reason_has_tail"

# B4 — git-TRACKED marker → refuse, no eval, no block.
reset_repo; set_marker "touch \"$REPO/SENTINEL.flag\"; exit 1"; track_marker
out="$(run_jq false)"
assert_noblock "$out" "tracked_marker_refused_noblock"
assert_nofile  "$REPO/SENTINEL.flag" "tracked_marker_no_eval"
assert_has     "$(cat "$ERR")" "refusing" "tracked_marker_stderr_note"

# B5 — stop_hook_active:true → immediate exit, no eval, no block (loop-break, jq path).
reset_repo; set_marker "touch \"$REPO/SENTINEL.flag\"; exit 1"
out="$(run_jq true)"
assert_noblock "$out" "stop_hook_active_breaks"
assert_nofile  "$REPO/SENTINEL.flag" "stop_hook_active_no_eval"

# B6a — no jq + red marker (not a continuation) → still blocks; stderr hints about jq.
reset_repo; set_marker 'echo rednojq; exit 1'
out="$(run_nojq false)"
assert_block "$out" "nojq_red_blocks"
assert_has   "$(cat "$ERR")" "jq" "nojq_block_stderr_hint"

# B6b — no jq + stop_hook_active:true → loop-break (no eval, no block) → no infinite re-block.
reset_repo; set_marker "touch \"$REPO/SENTINEL.flag\"; exit 1"
out="$(run_nojq true)"
assert_noblock "$out" "nojq_continuation_breaks"
assert_nofile  "$REPO/SENTINEL.flag" "nojq_continuation_no_eval"

# B7 — an active run's red-test gate applies only to its owner session.
reset_repo; set_active_owner; set_marker "touch \"$REPO/SENTINEL.flag\"; exit 1"
out="$(run_jq_session other-session)"
assert_noblock "$out" "other_session_ignores_active_test_gate"
assert_nofile  "$REPO/SENTINEL.flag" "other_session_test_gate_does_not_eval"
out="$(run_jq_session owner-session)"
assert_block "$out" "owner_session_keeps_active_test_gate"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
