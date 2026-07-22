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

rm -rf "$REPO/.kimiflow"
APP_HARNESS="$WORK/app-harness"
APP_PAYLOADS="$WORK/app-payloads.jsonl"
cat > "$APP_HARNESS" <<'EOF'
#!/usr/bin/env bash
set -eu
if [ "${1:-}" = "capabilities" ]; then
  if [ "${APP_INVALID_ENCODING:-0}" = "1" ]; then
    printf '\377'
    exit 0
  fi
  printf '%s\n' "{\"schema_version\":1,\"name\":\"app-fixture\",\"host\":\"kimitalk\",\"capabilities\":{\"files\":true,\"shell\":true,\"tests\":true,\"resume\":true,\"gates\":true},\"features\":{\"workflow_context\":true,\"model_roles\":true,\"structured_events\":true,\"root_confinement\":${APP_ROOT_CONFINEMENT:-true}}}"
  exit 0
fi
IFS= read -r payload
printf '%s\n' "$payload" >> "$APP_PAYLOADS"
root="$(printf '%s\n' "$payload" | jq -r '.root')"
action="$(printf '%s\n' "$payload" | jq -r '.action')"
session="$(printf '%s\n' "$payload" | jq -r '.session_id // "kimitalk-session-123"')"
printf '%s\n' "$payload" | jq -e '
  .host == "kimitalk" and .adapter == "app-fixture"
  and .model_routing.roles.top == "qwen-local"
  and .model_routing.roles.balanced == "qwen-coder-local"
  and .workflow_context.name == "kimiflow"
  and .workflow_context.skill == "SKILL.md"
  and .workflow_context.phase_manifest == "phases/PHASES.json"
  and .workflow_context.run_bridge == "hooks/run-bridge.sh"
' >/dev/null
[ -f "$(printf '%s\n' "$payload" | jq -r '.workflow_context.plugin_root')/SKILL.md" ]
mkdir -p "$root/.kimiflow/demo" "$root/.kimiflow/session"
if [ "$action" = "start" ]; then
  head="$(git -C "$root" rev-parse HEAD)"
  cat > "$root/.kimiflow/demo/STATE.md" <<STATE
Flow schema: 4
Mode: feature
Scope: small
Status: active
Affected files:
- README.md
Phase 0: done
Phase 1: done
STATE
  printf '%s\n' "{\"schema_version\":1,\"status\":\"active\",\"run\":\".kimiflow/demo\",\"mode\":\"feature\",\"scope\":\"small\",\"host\":\"kimitalk\",\"started_head\":\"$head\",\"last_checked_head\":\"$head\",\"owner\":{\"host\":\"kimitalk\",\"session_id\":\"$session\"}}" > "$root/.kimiflow/session/ACTIVE_RUN.json"
  printf '%s\n' "{\"type\":\"session.started\",\"session_id\":\"$session\"}"
  printf '%s\n' '{"type":"progress","current":1,"total":2,"label":"Planning","private":"drop"}'
else
  rm -f "$root/.kimiflow/session/ACTIVE_RUN.json"
  printf '%s\n' '{"schema_version":1,"outcome":"done"}' > "$root/.kimiflow/demo/SESSION-OUTCOME.json"
  printf '%s\n' '{"type":"tool.completed","tool":"tests","status":"passed","duration_ms":3,"command":"drop"}'
fi
printf '%s\n' '{"type":"turn.completed","usage":{"model_calls":1,"tool_calls":1,"input_tokens":8,"output_tokens":3}}'
EOF
chmod +x "$APP_HARNESS"
export APP_PAYLOADS

check="$("$ROOT/hooks/kimiflow-runner.sh" adapter-check \
  --adapter-command "$APP_HARNESS" \
  --require-feature workflow_context \
  --require-feature model_roles \
  --require-feature structured_events \
  --require-feature root_confinement)"
jq -e '.status == "compatible" and .adapter.host == "kimitalk" and (.adapter_contract | test("^sha256:[0-9a-f]{64}$"))' >/dev/null <<<"$check"
[ ! -e "$APP_PAYLOADS" ]
set +e
APP_ROOT_CONFINEMENT=false "$ROOT/hooks/kimiflow-runner.sh" adapter-check \
  --adapter-command "$APP_HARNESS" --require-feature root_confinement > "$WORK/incompatible.json"
rc=$?
set -e
[ "$rc" -eq 2 ]
jq -e '.status == "adapter_incompatible" and (.error | contains("root_confinement"))' "$WORK/incompatible.json" >/dev/null
set +e
APP_INVALID_ENCODING=1 "$ROOT/hooks/kimiflow-runner.sh" adapter-check \
  --adapter-command "$APP_HARNESS" > "$WORK/invalid-encoding.json"
rc=$?
set -e
[ "$rc" -eq 2 ]
jq -e '.status == "adapter_incompatible"' "$WORK/invalid-encoding.json" >/dev/null
printf 'ok   adapter_preflight_contract\n'

"$ROOT/hooks/kimiflow-runner.sh" run --adapter command \
  --adapter-command "$APP_HARNESS" \
  --require-feature workflow_context \
  --require-feature model_roles \
  --require-feature structured_events \
  --require-feature root_confinement \
  --model-role top=qwen-local \
  --model-role balanced=qwen-coder-local \
  --events-jsonl --root "$REPO" "build through app host" > "$WORK/app-events.jsonl"
jq -s -e '
  any(.[]; .type == "progress" and .label == "Planning" and (has("private") | not))
  and any(.[]; .type == "tool.completed" and .tool == "tests" and (has("command") | not))
  and (last | .type == "run.result" and .result.status == "done" and .result.host == "kimitalk")
' "$WORK/app-events.jsonl" >/dev/null
[ "$(wc -l < "$APP_PAYLOADS" | tr -d ' ')" -eq 2 ]
if grep -Eq 'qwen-local|Planning|workflow_context' "$REPO/.kimiflow/session/HEADLESS_RUN.json"; then
  printf 'runner_structured_event_stream: receipt leaked optional payload data\n' >&2
  exit 1
fi
printf 'ok   runner_structured_event_stream\n'
printf 'ok   adapter_compatibility_matrix\n'
