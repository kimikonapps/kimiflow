#!/usr/bin/env bash
# Verify the skill-only boundary with stock FirstMate primary-continuity and worker-path evidence.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
MODE=${1:---static}
PINNED_FIRSTMATE_COMMIT=cd73e75e02a1c1e74811b00c5ee08ffae8a59e1e
VERIFIED_PI_VERSION=0.82.0

fail() {
  printf 'firstmate integration: FAIL: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

assert_contains() {
  grep -Fq -- "$2" "$1" || fail "$1 does not contain: $2"
}

assert_absent() {
  [ ! -e "$ROOT/$1" ] || fail "removed Kimiflow runtime path still exists: $1"
}

run_static() {
  require_command jq
  jq -e '
    .name == "@kimiflow/pi"
    and .pi.skills == ["./hosts/pi/skills/kimiflow"]
    and (.pi | has("extensions") | not)
  ' "$ROOT/package.json" >/dev/null || fail "package.json is not skill-only"

  for rel in \
    hosts/pi/extensions/calm.js \
    hosts/pi/extensions/captain.js \
    hosts/pi/extensions/worker.js \
    hooks/pi-host.sh \
    hooks/pi-subagent-gate.sh \
    hooks/kimiflow_core/pi_herdr.py \
    hooks/kimiflow_core/pi_host.py \
    hooks/kimiflow_core/pi_project.py; do
    assert_absent "$rel"
  done

  skill="$ROOT/hosts/pi/skills/kimiflow/SKILL.md"
  assert_contains "$skill" "FirstMate primary"
  assert_contains "$skill" "FirstMate crewmate"
  assert_contains "$skill" "Standalone Pi"
  assert_contains "$skill" "current codebase"
  assert_contains "$skill" "current authoritative primary sources"
  assert_contains "$skill" "bin/fm-project-mode.sh"
  assert_contains "$skill" 'refuse to spawn'
  assert_contains "$skill" '`no-mistakes` is not a valid delivery mode'
  assert_contains "$skill" "visible FirstMate workers"
  assert_contains "$skill" "FirstMate alone owns spawn"
  assert_contains "$skill" "project-trust dialog"
  assert_contains "$skill" "Do not create a second Kimiflow Active Run"
  assert_contains "$skill" "Standalone Pi never requires FirstMate"
  assert_contains "$ROOT/phases/phase-0-setup.md" "<loaded-kimiflow-package-root>/hooks/resolve-verbosity.sh"
  assert_contains "$ROOT/phases/phase-0-setup.md" "KIMIFLOW_HOST=pi"
  assert_contains "$ROOT/phases/phase-0-setup.md" 'use `balanced` for the current session'
  if grep -Fq 'KIMIFLOW_PI_VERBOSITY' "$ROOT/phases/phase-0-setup.md"; then
    fail "standalone Pi verbosity still depends on the removed runtime producer"
  fi

  if rg -n 'kimiflow_activate|kimiflow_attention|KIMIFLOW_PI_HERDR|PI-HERDR-ENDPOINTS|kimiflow_core\.(pi_host|pi_herdr|pi_project)' \
    "$ROOT" \
    --glob '!CHANGELOG.md' \
    --glob '!plugins/kimiflow/**' \
    --glob '!.kimiflow/**' \
    --glob '!hooks/test-firstmate-integration.sh' >/dev/null; then
    fail "a productive or current-documentation reference to the removed Kimiflow bridge remains"
  fi
  if rg -n 'kimiflow_subagent|PI-SUBAGENTS?' \
    "$ROOT" \
    --glob '!CHANGELOG.md' \
    --glob '!plugins/kimiflow/**' \
    --glob '!.kimiflow/**' \
    --glob '!hooks/test-firstmate-integration.sh' >/dev/null; then
    fail "a productive reference to the removed Kimiflow Pi subagent runtime remains"
  fi

  for doc in README.md README.de.md COMPATIBILITY.md docs/architecture.md docs/testing.md; do
    assert_contains "$ROOT/$doc" "FirstMate"
  done
  assert_contains "$ROOT/COMPATIBILITY.md" "experimental"
  assert_contains "$ROOT/COMPATIBILITY.md" "not resumable"

  printf 'firstmate integration: static contract passed\n'
}

replace_brief_task() {
  python3 - "$1" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
task = """Use the installed Kimiflow skill for this already confirmed product contract. Do not ask for another product confirmation.

Problem: prove that a normal stock FirstMate Pi crewmate can execute the skill-only Kimiflow workflow without a Kimiflow runtime bridge.
User flow: read this brief, inspect the current fixture repository, implement the one requested file, verify it, and report through the normal FirstMate status file.
Observable success: create kimiflow-live-proof.txt containing exactly `stock-firstmate-kimiflow-ok`, commit it, and report done with the words `kimiflow-live-proof`.
Boundary: change no other tracked project file; do not open a PR, do not use no-mistakes, and do not start or control Herdr yourself.
Plan: inspect the fixture and loaded Kimiflow skill, create only the proof file, verify exact content and Git state, commit locally, then report through FirstMate status.
Acceptance: the file content is exact, `git diff --check` succeeds, the commit is local, and the normal status line returns to FirstMate.
The codebase and current integration sources were inspected before this contract was confirmed. This brief is the final confirmation."""
if "{TASK}" not in text:
    raise SystemExit("brief placeholder is missing")
path.write_text(text.replace("{TASK}", task, 1), encoding="utf-8")
PY
}

run_primary_continuity() {
  primary_socket="kimiflow-firstmate-primary-$$"
  primary_session=kimiflow-primary
  primary_session_dir="$work/primary-sessions"
  mkdir -p "$primary_session_dir"
  tmux -L "$primary_socket" new-session -d -s "$primary_session" -x 160 -y 36 -c "$firstmate" \
    "env HERDR_SESSION='$lab' FM_HOME='$fm_home' FM_ROOT_OVERRIDE='$firstmate' PI_CODING_AGENT_DIR='$pi_agent' FM_POLL=1 FM_SIGNAL_GRACE=0 FM_HEARTBEAT=600 bash --noprofile --norc -c 'pi --approve --session-dir \"$primary_session_dir\" --name kimiflow-primary-e2e --no-extensions -e .pi/extensions/fm-calm.ts -e .pi/extensions/fm-primary-turnend-guard.ts -e .pi/extensions/fm-primary-pi-watch.ts --model openai-codex/gpt-5.6-sol --thinking low; rc=\$?; printf \"PI_EXIT=%s\\n\" \"\$rc\"; sleep 60'"

  attempts=0
  while [ "$attempts" -lt 120 ]; do
    pane="$(tmux -L "$primary_socket" capture-pane -p -t "$primary_session" -S -120 2>/dev/null || true)"
    if [ -f "$fm_home/state/.pi-turnend-extension-loaded" ] \
      && [ -f "$fm_home/state/.pi-watch-extension-loaded" ] \
      && printf '%s\n' "$pane" | grep -Fq 'gpt-5.6-sol' \
      && printf '%s\n' "$pane" | grep -Fq 'kimiflow'; then
      break
    fi
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 120 ] || fail "stock FirstMate Pi primary did not become ready"

  tmux -L "$primary_socket" send-keys -t "$primary_session" -l '/calm'
  tmux -L "$primary_socket" send-keys -t "$primary_session" Enter
  sleep 0.2
  tmux -L "$primary_socket" send-keys -t "$primary_session" -l 'Reply exactly KIMIFLOW_PRIMARY_READY.'
  tmux -L "$primary_socket" send-keys -t "$primary_session" Enter
  attempts=0
  while [ "$attempts" -lt 120 ]; do
    pane="$(tmux -L "$primary_socket" capture-pane -p -t "$primary_session" -S -200 2>/dev/null || true)"
    [ "$(printf '%s\n' "$pane" | grep -Fc 'KIMIFLOW_PRIMARY_READY' || true)" -ge 2 ] && break
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 120 ] || fail "stock FirstMate Pi primary did not answer in its session"

  primary_prompt="Use the installed Kimiflow skill for this isolated live check and first read its exact source at $ROOT/hosts/pi/skills/kimiflow/SKILL.md with Pi's native read tool. The final product contract and minimum-complete plan in $fm_home/data/$task/brief.md are already confirmed. As the stock FirstMate primary, dispatch the existing task $task for project fixture through the normal lifecycle with bin/fm-spawn.sh $task $project --harness pi --model openai-codex/gpt-5.6-sol --effort low --backend herdr. Follow the stock harness-adapters startup check: inspect the spawned Pi worker with bin/fm-peek.sh, accept its documented project-trust dialog only through bin/fm-send.sh with --key Enter if it appears, and inspect it again to verify that the trust dialog is gone and the worker project is visible. Keep the existing FirstMate Pi supervision cycle; call fm_watch_arm_pi only if FirstMate reports that the cycle is missing, failed, or unhealthy, and never use bash to arm supervision. Only after the watcher returns the terminal worker status, run bin/fm-wake-drain.sh and reply exactly KIMIFLOW_PRIMARY_HANDLED. Never call herdr directly."
  tmux -L "$primary_socket" send-keys -t "$primary_session" -l "$primary_prompt"
  tmux -L "$primary_socket" send-keys -t "$primary_session" Enter
  attempts=0
  while [ "$attempts" -lt 120 ]; do
    pane="$(tmux -L "$primary_socket" capture-pane -p -t "$primary_session" -S -240 2>/dev/null || true)"
    printf '%s\n' "$pane" | grep -Fq 'watcher: started Pi extension arm child' && break
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 120 ] || fail "stock FirstMate Pi primary did not establish its watcher cycle"

  attempts=0
  while [ "$attempts" -lt 720 ]; do
    pane="$(tmux -L "$primary_socket" capture-pane -p -t "$primary_session" -S -300 2>/dev/null || true)"
    if [ -f "$fm_home/state/$task.status" ] \
      && grep -Eq '^done: .*kimiflow-live-proof' "$fm_home/state/$task.status" \
      && [ "$(printf '%s\n' "$pane" | grep -Fc 'KIMIFLOW_PRIMARY_HANDLED' || true)" -ge 2 ]; then
      break
    fi
    if [ -f "$fm_home/state/$task.status" ] \
      && grep -Eq '^(failed|blocked|needs-decision):' "$fm_home/state/$task.status"; then
      cat "$fm_home/state/$task.status" >&2
      fail "real FirstMate crewmate did not complete the confirmed brief"
    fi
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 720 ] || fail "worker result did not return to the same FirstMate Pi primary session"
  tmux -L "$primary_socket" send-keys -t "$primary_session" -l '/quit'
  tmux -L "$primary_socket" send-keys -t "$primary_session" Enter
  sleep 1
}

verify_primary_transcript() {
  primary_session_file="$(find "$primary_session_dir" -type f -name '*.jsonl' -print 2>/dev/null | sort | tail -1)"
  [ -n "$primary_session_file" ] || fail "stock FirstMate primary session was not persisted"
  python3 - "$primary_session_file" "$ROOT/hosts/pi/skills/kimiflow/SKILL.md" "$task" <<'PY'
import json
import re
import sys

path, skill_path, task = sys.argv[1:]
entries = []
with open(path, encoding="utf-8") as handle:
    for line_no, line in enumerate(handle, 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid primary JSONL at line {line_no}: {exc}")
        entries.append((line_no, entry))

calls = []
results = []
assistant_markers = []
for line_no, entry in entries:
    message = entry.get("message")
    if not isinstance(message, dict):
        continue
    role = message.get("role")
    content = message.get("content")
    if role == "toolResult":
        text = "\n".join(
            item.get("text", "") for item in content or []
            if isinstance(item, dict) and item.get("type") == "text"
        )
        results.append((line_no, message.get("toolCallId"), message.get("toolName"), text))
        continue
    if not isinstance(content, list):
        continue
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "toolCall":
            calls.append((line_no, item.get("id"), item.get("name"), item.get("arguments") or {}))
        elif role == "assistant" and item.get("type") == "text":
            marker = item.get("text", "").strip()
            if marker.rstrip(".") == "KIMIFLOW_PRIMARY_HANDLED":
                assistant_markers.append(line_no)

def first_call(predicate, label):
    for call in calls:
        if predicate(call):
            return call
    raise SystemExit(f"missing primary event: {label}")

def command(call):
    args = call[3]
    return args.get("command", "") if isinstance(args, dict) else ""

skill = first_call(lambda c: c[2] == "read" and c[3].get("path") == skill_path, "exact Kimiflow skill read")
spawn = first_call(lambda c: c[2] == "bash" and f"bin/fm-spawn.sh {task}" in command(c), "FirstMate spawn")
peek_before = first_call(lambda c: c[0] > spawn[0] and c[2] == "bash" and f"bin/fm-peek.sh {task}" in command(c), "trust inspection")
send = first_call(lambda c: c[0] > peek_before[0] and c[2] == "bash" and "bin/fm-send.sh" in command(c) and "--key Enter" in command(c), "FirstMate trust acceptance")
peek_after = first_call(lambda c: c[0] > send[0] and c[2] == "bash" and f"bin/fm-peek.sh {task}" in command(c), "post-trust inspection")
drain = first_call(lambda c: c[0] > peek_after[0] and c[2] == "bash" and "bin/fm-wake-drain.sh" in command(c), "wake drain")
watch = first_call(lambda c: c[0] < drain[0] and c[2] == "fm_watch_arm_pi", "Pi watcher arm")

result_by_id = {call_id: (line_no, text) for line_no, call_id, _name, text in results}
trust_result = result_by_id.get(peek_before[1])
if trust_result is None or "Trust project folder?" not in trust_result[1]:
    raise SystemExit("primary did not observe the real Pi project-trust dialog")
started_result = result_by_id.get(peek_after[1])
if (
    started_result is None
    or "Trust project folder?" in started_result[1]
    or not any(marker in started_result[1] for marker in ("fixture", "Working...", "kimiflow-live-proof"))
):
    observed = "<missing result>" if started_result is None else repr(started_result[1][-1200:])
    raise SystemExit(
        "primary did not verify that the confirmed brief began after trust; "
        f"post-trust peek ended with: {observed}"
    )
drain_result = result_by_id.get(drain[1])
if drain_result is None or "done:" not in drain_result[1] or "kimiflow-live-proof" not in drain_result[1]:
    raise SystemExit("primary wake drain did not carry the terminal worker result")
if not (
    watch[0] < drain[0]
    and skill[0] < spawn[0] < peek_before[0] < send[0] < peek_after[0] < drain[0] < drain_result[0]
):
    raise SystemExit("primary lifecycle events are out of order")
if len(assistant_markers) != 1 or assistant_markers[0] <= drain_result[0]:
    raise SystemExit("primary completion marker was missing, duplicated, or premature")
for call in calls:
    if call[2] == "bash" and re.search(r"(?:^|[;&|]\s*)herdr\s+(?:agent|pane)\b", command(call)):
        raise SystemExit("primary bypassed FirstMate with a raw Herdr control command")
PY
}

run_live() {
  run_static
  require_command git
  require_command herdr
  require_command jq
  require_command pi
  require_command python3
  require_command rg
  require_command tmux
  require_command treehouse

  firstmate=${KIMIFLOW_FIRSTMATE_ROOT:-}
  [ -n "$firstmate" ] || fail "KIMIFLOW_FIRSTMATE_ROOT is required for --live"
  firstmate="$(CDPATH= cd -- "$firstmate" && pwd -P)"
  [ "$(git -C "$firstmate" rev-parse HEAD)" = "$PINNED_FIRSTMATE_COMMIT" ] \
    || fail "FirstMate checkout is not at the pinned commit"
  [ -z "$(git -C "$firstmate" status --porcelain --untracked-files=no)" ] \
    || fail "FirstMate checkout has tracked changes"
  [ "$(pi --version)" = "$VERIFIED_PI_VERSION" ] \
    || fail "live check requires verified Pi $VERIFIED_PI_VERSION"
  stock_firstmate=$firstmate
  user_pi_agent=${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}
  [ -f "$user_pi_agent/auth.json" ] \
    || fail "live check requires an existing authenticated Pi credential store"

  stock_log="$(mktemp "${TMPDIR:-/tmp}/kimiflow-firstmate-stock.XXXXXX")"
  if ! bash "$stock_firstmate/tests/fm-pi-primary-types.test.sh" >"$stock_log" 2>&1; then
    cat "$stock_log" >&2
    fail "stock FirstMate Pi primary checks failed"
  fi
  if ! bash "$stock_firstmate/tests/fm-backend-herdr.test.sh" >>"$stock_log" 2>&1; then
    cat "$stock_log" >&2
    fail "stock FirstMate Herdr checks failed"
  fi
  if grep -Fq 'skip:' "$stock_log"; then
    cat "$stock_log" >&2
    fail "a required stock FirstMate check skipped"
  fi
  work="$(mktemp -d "${TMPDIR:-/tmp}/kimiflow-firstmate-live.XXXXXX")"
  firstmate="$work/firstmate"
  git clone -q --no-hardlinks "$stock_firstmate" "$firstmate"
  git -C "$firstmate" checkout -q --detach "$PINNED_FIRSTMATE_COMMIT"
  pi_agent="$work/pi-agent"
  mkdir -p "$pi_agent"
  cp "$user_pi_agent/auth.json" "$pi_agent/auth.json"
  chmod 600 "$pi_agent/auth.json"
  (cd "$work" && PI_CODING_AGENT_DIR="$pi_agent" pi install "$ROOT" >/dev/null)
  (cd "$firstmate" && PI_CODING_AGENT_DIR="$pi_agent" pi install "$ROOT" -l --approve >/dev/null)
  grep -Fq "$ROOT" "$pi_agent/settings.json" \
    || fail "isolated Pi store does not pin the current Kimiflow checkout"
  grep -Fq "$ROOT" "$firstmate/.pi/settings.json" \
    || fail "disposable FirstMate primary does not pin the current Kimiflow checkout"
  export PI_CODING_AGENT_DIR="$pi_agent"
  fm_home="$work/home"
  project="$fm_home/projects/fixture"
  task=kimiflow-live
  lab="$("$firstmate/bin/fm-herdr-lab.sh" name kimiflow-live)"
  primary_socket=
  cleanup() {
    [ -z "$primary_socket" ] || tmux -L "$primary_socket" kill-server >/dev/null 2>&1 || true
    FM_HOME="$fm_home" FM_ROOT_OVERRIDE="$firstmate" \
      "$firstmate/bin/fm-teardown.sh" "$task" --force >/dev/null 2>&1 || true
    "$firstmate/bin/fm-herdr-lab.sh" teardown "$lab" >/dev/null 2>&1 || true
    if [ "${KIMIFLOW_KEEP_LIVE_ARTIFACTS:-0}" = 1 ]; then
      printf 'firstmate integration: kept live artifacts at %s\n' "$work" >&2
    else
      rm -rf -- "$work"
    fi
    rm -f -- "$stock_log"
  }
  trap cleanup EXIT INT TERM

  mkdir -p "$fm_home/config" "$fm_home/data" "$fm_home/projects" "$fm_home/state"
  printf 'herdr\n' > "$fm_home/config/backend"
  printf 'on\n' > "$fm_home/config/calm"
  printf '%s\n' '- fixture [local-only] - disposable Kimiflow live fixture' > "$fm_home/data/projects.md"
  git init -q "$project"
  git -C "$project" config user.name 'Kimiflow FirstMate E2E'
  git -C "$project" config user.email 'kimiflow-firstmate-e2e@invalid.example'
  printf 'fixture\n' > "$project/README.md"
  (cd "$project" && pi install "$ROOT" -l >/dev/null)
  grep -Fq "$ROOT" "$project/.pi/settings.json" \
    || fail "fixture does not pin the current Kimiflow checkout"
  git -C "$project" add README.md .pi/settings.json
  git -C "$project" commit -qm 'fixture: initialize'
  base_head="$(git -C "$project" rev-parse HEAD)"

  [ "$(FM_HOME="$fm_home" "$firstmate/bin/fm-project-mode.sh" fixture)" = 'local-only off' ] \
    || fail "fixture did not resolve to local-only"
  FM_HOME="$fm_home" FM_ROOT_OVERRIDE="$firstmate" \
    "$firstmate/bin/fm-brief.sh" "$task" fixture >/dev/null
  replace_brief_task "$fm_home/data/$task/brief.md"
  session_marker="$work/pi-session-start.marker"
  : > "$session_marker"

  "$firstmate/bin/fm-herdr-lab.sh" provision "$lab"
  run_primary_continuity
  verify_primary_transcript

  status_file="$fm_home/state/$task.status"
  meta_file="$fm_home/state/$task.meta"
  attempts=0
  while [ "$attempts" -lt 360 ]; do
    if [ -f "$status_file" ] && grep -Eq '^done: .*kimiflow-live-proof' "$status_file"; then
      break
    fi
    if [ -f "$status_file" ] && grep -Eq '^(failed|blocked|needs-decision):' "$status_file"; then
      cat "$status_file" >&2
      fail "real FirstMate crewmate did not complete the confirmed brief"
    fi
    sleep 1
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 360 ] || {
    [ ! -f "$status_file" ] || cat "$status_file" >&2
    fail "real FirstMate crewmate did not return status before timeout"
  }

  [ -f "$meta_file" ] || fail "FirstMate did not publish task metadata"
  grep -Fxq 'backend=herdr' "$meta_file" || fail "worker is not a Herdr endpoint"
  worktree="$(sed -n 's/^worktree=//p' "$meta_file" | tail -1)"
  [ -n "$worktree" ] && [ -d "$worktree" ] || fail "visible worker worktree is missing"
  [ "$(cat "$worktree/kimiflow-live-proof.txt")" = 'stock-firstmate-kimiflow-ok' ] \
    || fail "real worker did not produce the Kimiflow proof"
  git -C "$worktree" diff --check
  [ -z "$(git -C "$worktree" status --porcelain)" ] \
    || fail "real worker left its worktree dirty"
  [ "$(git -C "$worktree" rev-parse HEAD)" != "$base_head" ] \
    || fail "real worker did not create the required local commit"
  [ "$(git -C "$worktree" show HEAD:kimiflow-live-proof.txt)" = 'stock-firstmate-kimiflow-ok' ] \
    || fail "the required proof is not contained in the worker commit"
  pi_state_root=$pi_agent
  session_file=
  while IFS= read -r candidate; do
    if grep -Fq 'kimiflow-live-proof' "$candidate" \
      && grep -Fq "$ROOT/hosts/pi/skills/kimiflow/SKILL.md" "$candidate" \
      && grep -Fq "$worktree" "$candidate"; then
      session_file=$candidate
      break
    fi
  done < <(find "$pi_state_root/sessions" -type f -name '*.jsonl' -newer "$session_marker" 2>/dev/null | sort)
  [ -n "$session_file" ] \
    || fail "worker session does not prove that the current Kimiflow skill was loaded"
  for forbidden in \
    .kimiflow/session/ACTIVE_RUN.json \
    .kimiflow/session/INTAKE-RECEIPT-2.json \
    .kimiflow/session/INTENT-LOCK.json; do
    [ ! -e "$worktree/$forbidden" ] \
      || fail "FirstMate crewmate created forbidden duplicate Kimiflow state: $forbidden"
  done
  if find "$worktree/.kimiflow" -type f \( -name 'INTAKE.md' -o -name 'STATE.md' -o -name 'ACTIVE_RUN.json' \) -print -quit 2>/dev/null | grep -q .; then
    fail "FirstMate crewmate replayed Kimiflow intake or Active Run state"
  fi
  python3 - "$session_file" "$ROOT/hosts/pi/skills/kimiflow/SKILL.md" <<'PY'
import json
import re
import sys

path, skill_path = sys.argv[1:]
user_messages = []
assistant_text = []
skill_reads = 0
with open(path, encoding="utf-8") as handle:
    for line_no, line in enumerate(handle, 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid worker JSONL at line {line_no}: {exc}")
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if not isinstance(content, list):
            continue
        texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        if role == "user":
            user_messages.extend(texts)
        elif role == "assistant":
            assistant_text.extend(texts)
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "toolCall" or item.get("name") != "read":
                continue
            args = item.get("arguments") or {}
            if args.get("path") == skill_path:
                skill_reads += 1
if len(user_messages) != 1:
    raise SystemExit(f"worker received {len(user_messages)} user turns instead of one authoritative brief")
if skill_reads < 1:
    raise SystemExit("worker did not read the exact current Kimiflow skill")
for text in assistant_text:
    if re.search(r"(?is)\b(?:please|reply)\b.{0,100}\b(?:confirm|contract|intake)\b", text):
        raise SystemExit("worker asked for a second intake or contract confirmation")
PY
  [ -z "$(git -C "$firstmate" status --porcelain --untracked-files=no)" ] \
    || fail "live check modified the stock FirstMate checkout"

  printf 'firstmate integration: stock primary dispatched and received the visible Pi/Herdr worker\n'
  if ! bash "$stock_firstmate/tests/fm-calm-pi-extension.test.sh" >>"$stock_log" 2>&1; then
    cat "$stock_log" >&2
    fail "stock FirstMate Calm checks failed after the integrated path passed"
  fi
  if grep -Fq 'skip:' "$stock_log"; then
    cat "$stock_log" >&2
    fail "a required stock FirstMate check skipped"
  fi

  printf 'firstmate integration: stock primary continuity and visible Pi/Herdr worker path passed\n'
}

case "$MODE" in
  --static) run_static ;;
  --live) run_live ;;
  *) fail "usage: hooks/test-firstmate-integration.sh --static|--live" ;;
esac
