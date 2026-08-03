#!/usr/bin/env bash
# Verify that normal Pi in a project can use stock FirstMate for visible Herdr workers.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
MODE=${1:---static}

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
  require_command node
  jq -e '
    .name == "@kimiflow/pi"
    and .pi.skills == ["./hosts/pi/skills/kimiflow"]
    and .pi.extensions == ["./hosts/pi/extensions/kimiflow-crew.js"]
  ' "$ROOT/package.json" >/dev/null || fail "package.json does not expose exactly one crew adapter"

  extension="$ROOT/hosts/pi/extensions/kimiflow-crew.js"
  skill="$ROOT/hosts/pi/skills/kimiflow/SKILL.md"
  [ -f "$extension" ] || fail "kimiflow crew adapter is missing"
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

  assert_contains "$skill" "always **Kimiflow Main**"
  assert_contains "$skill" "only session that talks with the user"
  assert_contains "$skill" "kimiflow_crew"
  assert_contains "$skill" "worker_reachable"
  assert_contains "$skill" "confirmed workers treat product intake as complete"
  assert_contains "$skill" "FirstMate alone owns"
  assert_contains "$skill" "stage=research"
  assert_contains "$skill" "visible Scouts"
  assert_contains "$skill" "stock Calm extension"
  assert_contains "$skill" "Standalone behavior"
  for action in activate spawn status report send drain teardown; do
    assert_contains "$extension" "\"$action\""
  done
  for command in fm-session-start.sh fm-brief.sh fm-spawn.sh fm-peek.sh fm-crew-state.sh fm-send.sh fm-wake-drain.sh fm-watch-arm.sh fm-teardown.sh; do
    assert_contains "$extension" "$command"
  done
  assert_contains "$extension" "spawn_unverified"
  assert_contains "$extension" "HERDR_ENV"
  assert_contains "$extension" "KIMIFLOW_WORKER_VERBOSITY=quiet"
  assert_contains "$extension" "fm-calm.ts"
  assert_contains "$extension" "research_ship_forbidden"
  node --check "$extension" >/dev/null || fail "kimiflow crew adapter does not parse"
  node --test "$ROOT/hosts/pi/tests/kimiflow-crew.test.mjs" >/dev/null \
    || fail "kimiflow crew adapter unit tests failed"

  assert_contains "$ROOT/phases/phase-0-setup.md" "<loaded-kimiflow-package-root>/hooks/resolve-verbosity.sh"
  assert_contains "$ROOT/phases/phase-0-setup.md" "KIMIFLOW_HOST=pi"
  assert_contains "$ROOT/phases/phase-7-review-commit.md" "Pi visible-review override"
  if rg -n 'kimiflow_activate|kimiflow_attention|KIMIFLOW_PI_HERDR|PI-HERDR-ENDPOINTS|kimiflow_core\.(pi_host|pi_herdr|pi_project)' \
    "$ROOT" --glob '!CHANGELOG.md' --glob '!plugins/kimiflow/**' --glob '!.kimiflow/**' \
    --glob '!hooks/test-firstmate-integration.sh' >/dev/null; then
    fail "a productive reference to the removed Kimiflow bridge remains"
  fi
  for doc in README.md README.de.md COMPATIBILITY.md docs/architecture.md docs/testing.md; do
    assert_contains "$ROOT/$doc" "FirstMate"
  done
  assert_contains "$ROOT/COMPATIBILITY.md" "capability-based"
  assert_contains "$ROOT/COMPATIBILITY.md" "not resumable"
  printf 'firstmate integration: static project-session contract passed\n'
}

verify_firstmate_capabilities() {
  for script in fm-session-start.sh fm-brief.sh fm-spawn.sh fm-peek.sh fm-crew-state.sh fm-send.sh fm-wake-drain.sh fm-watch-arm.sh fm-teardown.sh fm-project-mode.sh; do
    [ -x "$firstmate/bin/$script" ] || fail "FirstMate capability is missing: bin/$script"
  done
}

verify_main_transcript() {
  session_file="$(find "$session_dir" -type f -name '*.jsonl' -print 2>/dev/null | sort | tail -1)"
  [ -n "$session_file" ] || fail "Pi Main session was not persisted"
  python3 - "$session_file" <<'PY'
import json
import re
import sys

calls = []
markers = []
for line_no, line in enumerate(open(sys.argv[1], encoding="utf-8"), 1):
    entry = json.loads(line)
    message = entry.get("message")
    if not isinstance(message, dict):
        continue
    for item in message.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "toolCall":
            calls.append((line_no, item.get("name"), item.get("arguments") or {}))
        if message.get("role") == "assistant" and item.get("type") == "text":
            markers.extend([line_no] * item.get("text", "").count("KIMIFLOW_PROJECT_MAIN_HANDLED"))

crew = [(line, args) for line, name, args in calls if name == "kimiflow_crew"]
actions = [args.get("action") for _, args in crew]
for required in ("activate", "spawn", "drain", "status", "report", "send"):
    if required not in actions:
        raise SystemExit(f"Pi Main did not call kimiflow_crew action={required}")
if actions.index("activate") > actions.index("spawn"):
    raise SystemExit("Pi Main spawned before crew activation")
if actions.count("spawn") < 2:
    raise SystemExit("Pi Main did not dispatch both the implementation Ship and independent review Scout")
if len(markers) != 1:
    raise SystemExit("Pi Main completion marker is missing or duplicated")
for _, name, args in calls:
    if name != "bash":
        continue
    command = args.get("command", "")
    if re.search(r"(?:^|[;&|]\s*)(?:herdr|.*bin/fm-(?:spawn|send|peek|watch|teardown))\b", command):
        raise SystemExit("Pi Main bypassed the kimiflow_crew boundary")
PY
}

run_live() {
  run_static
  for command in git herdr jq pi python3 rg tmux treehouse; do require_command "$command"; done

  firstmate=${KIMIFLOW_FIRSTMATE_ROOT:-"$ROOT/../firstmate"}
  firstmate="$(CDPATH= cd -- "$firstmate" 2>/dev/null && pwd -P)" \
    || fail "set KIMIFLOW_FIRSTMATE_ROOT to a stock FirstMate checkout"
  verify_firstmate_capabilities
  [ -z "$(git -C "$firstmate" status --porcelain --untracked-files=no)" ] \
    || fail "FirstMate checkout has tracked changes"
  user_pi_agent=${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}
  [ -f "$user_pi_agent/auth.json" ] || fail "an authenticated Pi credential store is required"

  stock_log="$(mktemp "${TMPDIR:-/tmp}/kimiflow-firstmate-stock.XXXXXX")"
  bash "$firstmate/tests/fm-pi-primary-types.test.sh" >"$stock_log" 2>&1 \
    || { cat "$stock_log" >&2; fail "stock FirstMate Pi checks failed"; }
  bash "$firstmate/tests/fm-backend-herdr.test.sh" >>"$stock_log" 2>&1 \
    || { cat "$stock_log" >&2; fail "stock FirstMate Herdr checks failed"; }
  grep -Fq 'skip:' "$stock_log" && { cat "$stock_log" >&2; fail "a required stock FirstMate check skipped"; }

  work="$(mktemp -d "${TMPDIR:-/tmp}/kimiflow-firstmate-live.XXXXXX")"
  fm_home="$work/firstmate"
  project="$work/fixture"
  pi_agent="$work/pi-agent"
  session_dir="$work/main-sessions"
  task=kimiflow-project-live
  review_task=kimiflow-project-live-review
  socket="kimiflow-project-main-$$"
  lab="$("$firstmate/bin/fm-herdr-lab.sh" name kimiflow-project-main)"
  cleanup() {
    if [ "${KIMIFLOW_KEEP_LIVE:-0}" = 1 ]; then
      printf 'firstmate integration: preserved live fixture at %s (tmux socket %s, Herdr lab %s)\n' "$work" "$socket" "$lab" >&2
      return
    fi
    tmux -L "$socket" kill-server >/dev/null 2>&1 || true
    FM_HOME="$fm_home" FM_ROOT_OVERRIDE="$fm_home" "$fm_home/bin/fm-teardown.sh" "$review_task" --force >/dev/null 2>&1 || true
    FM_HOME="$fm_home" FM_ROOT_OVERRIDE="$fm_home" "$fm_home/bin/fm-teardown.sh" "$task" --force >/dev/null 2>&1 || true
    "$fm_home/bin/fm-herdr-lab.sh" teardown "$lab" >/dev/null 2>&1 || true
    rm -rf -- "$work"
    rm -f -- "$stock_log"
  }
  trap cleanup EXIT INT TERM

  git clone -q --no-hardlinks "$firstmate" "$fm_home"
  mkdir -p "$project" "$pi_agent" "$session_dir"
  cp "$user_pi_agent/auth.json" "$pi_agent/auth.json"
  chmod 600 "$pi_agent/auth.json"

  git init -q "$project"
  git -C "$project" config user.name 'Kimiflow Project Main E2E'
  git -C "$project" config user.email 'kimiflow-project-main@invalid.example'
  printf 'fixture\n' > "$project/README.md"
  mkdir -p "$project/.kimiflow"
  printf 'quiet\n' > "$project/.kimiflow/verbosity"
  (cd "$project" && PI_CODING_AGENT_DIR="$pi_agent" pi install "$ROOT" -l --approve >/dev/null)
  grep -Fq "$ROOT" "$project/.pi/settings.json" || fail "fixture does not pin the current Kimiflow checkout"
  git -C "$project" add README.md .pi/settings.json
  git -C "$project" commit -qm 'fixture: initialize'
  base_head="$(git -C "$project" rev-parse HEAD)"

  "$fm_home/bin/fm-herdr-lab.sh" provision "$lab"
  model=${KIMIFLOW_LIVE_MODEL:-openai-codex/gpt-5.6-sol}
  tmux -L "$socket" new-session -d -s main -x 180 -y 42 -c "$project" \
    "env HERDR_ENV=1 HERDR_SESSION='$lab' KIMIFLOW_FIRSTMATE_ROOT='$fm_home' PI_CODING_AGENT_DIR='$pi_agent' FM_POLL=1 FM_SIGNAL_GRACE=0 FM_HEARTBEAT=600 pi --approve --session-dir '$session_dir' --name kimiflow-project-main --model '$model' --thinking low; sleep 30"

  attempts=0
  while [ "$attempts" -lt 120 ]; do
    pane="$(tmux -L "$socket" capture-pane -p -t main -S -160 2>/dev/null || true)"
    printf '%s\n' "$pane" | grep -Fq 'kimiflow' && break
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 120 ] || fail "project Pi Main did not become ready"

  prompt="Beginne diesen bestaetigten Kimiflow-Live-Run in dieser Main-Sitzung. Verwende ausschliesslich das Tool kimiflow_crew. Aktiviere die Crew mit verbosity=quiet und starte den sichtbaren stage=confirmed Ship $task. Brief: Erstelle nur kimiflow-live-proof.txt mit exakt stock-firstmate-kimiflow-ok, verifiziere Inhalt und git diff --check, committe lokal und melde danach paused review-ready mit dem exakten Commit. Wiederhole kein Produkt-Intake. Nach worker_reachable: nicht pollen oder Fortschritt melden, sondern ruhig auf den automatischen FirstMate-Wake warten. Nach dem review-ready Wake: einmal drain und status, dann starte den sichtbaren stage=confirmed Scout $review_task. Sein Brief: Pruefe den exakten Branch fm/$task ab Basis $base_head read-only gegen die Anforderung und git diff --check; schreibe einen eigenstaendigen FirstMate-Report mit erster Zeile REVIEW_RESULT: NONE und danach Evidenz, wenn alles stimmt, sonst REVIEW_RESULT: CANDIDATE plus Evidenz. Warte wieder ohne Polling auf den Wake, nutze drain, status und report. Bei REVIEW_RESULT: NONE sende dem Ship $task die Nachricht, dass der unabhaengige Review sauber ist und er jetzt done ready in branch melden soll. Warte auf dessen Wake, nutze drain und status und antworte erst bei done exakt KIMIFLOW_PROJECT_MAIN_HANDLED. Verwende keine direkten FirstMate- oder Herdr-Shellbefehle."
  tmux -L "$socket" send-keys -t main -l "$prompt"
  tmux -L "$socket" send-keys -t main Enter

  attempts=0
  while [ "$attempts" -lt 900 ]; do
    pane="$(tmux -L "$socket" capture-pane -p -t main -S -320 2>/dev/null || true)"
    if [ -f "$fm_home/state/$task.status" ] \
      && grep -Eq '^done:' "$fm_home/state/$task.status" \
      && [ "$(printf '%s\n' "$pane" | grep -Fc 'KIMIFLOW_PROJECT_MAIN_HANDLED' || true)" -ge 2 ]; then
      break
    fi
    if [ -f "$fm_home/state/$task.status" ] && grep -Eq '^(failed|blocked|needs-decision):' "$fm_home/state/$task.status"; then
      cat "$fm_home/state/$task.status" >&2
      fail "visible FirstMate worker did not complete"
    fi
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 900 ] || fail "worker result did not return to project Pi Main"

  meta="$fm_home/state/$task.meta"
  [ -f "$meta" ] || fail "FirstMate task metadata is missing"
  grep -Fxq 'backend=herdr' "$meta" || fail "worker is not a Herdr endpoint"
  [ "$(cat "$fm_home/config/calm")" = on ] || fail "FirstMate Calm preference was not enabled"
  review_meta="$fm_home/state/$review_task.meta"
  [ -f "$review_meta" ] || fail "independent review Scout metadata is missing"
  grep -Fxq 'kind=scout' "$review_meta" || fail "independent reviewer is not a FirstMate Scout"
  grep -Fxq 'REVIEW_RESULT: NONE' "$fm_home/data/$review_task/report.md" || fail "independent review Scout did not return the expected report"
  worktree="$(sed -n 's/^worktree=//p' "$meta" | tail -1)"
  [ -n "$worktree" ] && [ -d "$worktree" ] || fail "worker worktree is missing"
  [ "$(cat "$worktree/kimiflow-live-proof.txt")" = 'stock-firstmate-kimiflow-ok' ] \
    || fail "worker proof is incorrect"
  git -C "$worktree" diff --check
  [ -z "$(git -C "$worktree" status --porcelain)" ] || fail "worker worktree is dirty"
  [ "$(git -C "$worktree" rev-parse HEAD)" != "$base_head" ] || fail "worker did not commit"
  verify_main_transcript
  printf 'firstmate integration: project Pi Main and visible FirstMate Herdr worker passed\n'
}

case "$MODE" in
  --static) run_static ;;
  --live) run_live ;;
  *) fail "usage: hooks/test-firstmate-integration.sh --static|--live" ;;
esac
