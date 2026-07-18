#!/usr/bin/env bash
# kimiflow — named fail-closed integration checks for the optional headless runner.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAKE_BIN="$WORK/bin"
REPO="$WORK/repo"
LOG="$WORK/codex.log"
mkdir -p "$FAKE_BIN" "$REPO"
git -C "$REPO" init -q
git -C "$REPO" config user.name "Kimiflow Test"
git -C "$REPO" config user.email "kimiflow@example.test"
printf 'fixture\n' > "$REPO/README.md"
git -C "$REPO" add README.md
git -C "$REPO" commit -qm fixture

cat > "$FAKE_BIN/codex" <<'EOF'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$FAKE_LOG"
thread="019f5fa0-567a-70e0-9b07-604ffbdafbf4"
mkdir -p "$FAKE_ROOT/.kimiflow/demo" "$FAKE_ROOT/.kimiflow/session"
if [ "${FAKE_MODE:-done}" = "noactivate" ]; then
  printf '{"type":"thread.started","thread_id":"%s"}\n' "$thread"
  printf '{"type":"turn.completed"}\n'
  exit 0
fi
if [ "${2:-}" != "resume" ]; then
  head="$(git -C "$FAKE_ROOT" rev-parse HEAD)"
  awaiting=false
  [ "${FAKE_MODE:-done}" = "wait" ] && awaiting=true
  cat > "$FAKE_ROOT/.kimiflow/demo/STATE.md" <<STATE
Flow schema: 4
Mode: feature
Scope: small
Status: active
Affected files:
- README.md
Phase 0: done
Phase 1: done
STATE
  cat > "$FAKE_ROOT/.kimiflow/session/ACTIVE_RUN.json" <<JSON
{"schema_version":1,"status":"active","run":".kimiflow/demo","mode":"feature","scope":"small","host":"codex","started_head":"$head","last_checked_head":"$head","owner":{"host":"codex","session_id":"$thread"},"awaiting_user":$awaiting}
JSON
  printf '{"type":"thread.started","thread_id":"%s"}\n' "$thread"
  printf '{"type":"turn.completed"}\n'
  [ "${FAKE_MODE:-done}" = "transport" ] && exit 9
  exit 0
fi
if [ "${FAKE_MODE:-done}" = "transport" ]; then
  printf '{"type":"turn.failed"}\n'
  exit 9
fi
rm -f "$FAKE_ROOT/.kimiflow/session/ACTIVE_RUN.json"
printf '{"schema_version":1,"outcome":"done"}\n' > "$FAKE_ROOT/.kimiflow/demo/SESSION-OUTCOME.json"
printf '{"type":"turn.completed"}\n'
EOF
chmod +x "$FAKE_BIN/codex"

export PATH="$FAKE_BIN:$PATH" FAKE_ROOT="$REPO" FAKE_LOG="$LOG"

out="$("$ROOT/hooks/kimiflow-runner.sh" run --root "$REPO" "build fixture")" || {
  printf 'runner_autonomous_same_thread_loop: command failed\n' >&2
  exit 1
}
jq -e '.status == "done" and .turns == 2' >/dev/null <<<"$out"
grep -q 'exec --json' "$LOG"
grep -q 'exec resume.*019f5fa0-567a-70e0-9b07-604ffbdafbf4' "$LOG"
printf 'ok   runner_start_contract\n'
printf 'ok   runner_autonomous_same_thread_loop\n'

rm -rf "$REPO/.kimiflow"
: > "$LOG"
set +e
FAKE_MODE=wait "$ROOT/hooks/kimiflow-runner.sh" run --root "$REPO" "needs choice" > "$WORK/wait.json"
rc=$?
set -e
if [ "$rc" -ne 3 ]; then
  cat "$WORK/wait.json" >&2
  printf 'runner_material_wait_only: expected exit 3, got %s\n' "$rc" >&2
  exit 1
fi
jq -e '.status == "awaiting_user"' "$WORK/wait.json" >/dev/null
FAKE_MODE=done "$ROOT/hooks/kimiflow-runner.sh" resume --root "$REPO" --message approved > "$WORK/resumed.json"
jq -e '.status == "done"' "$WORK/resumed.json" >/dev/null
printf 'ok   runner_material_wait_only\n'

rm -rf "$REPO/.kimiflow"
set +e
FAKE_MODE=noactivate "$ROOT/hooks/kimiflow-runner.sh" run --root "$REPO" "must activate" > "$WORK/noactivate.json"
rc=$?
set -e
[ "$rc" -ne 0 ]
jq -e '.status == "no_kimiflow_run"' "$WORK/noactivate.json" >/dev/null
printf 'ok   runner_fail_closed_contract\n'
