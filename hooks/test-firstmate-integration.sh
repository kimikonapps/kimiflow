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

  assert_contains "$skill" "conversational Pi Captain"
  assert_contains "$skill" "only session that talks with the user"
  assert_contains "$skill" 'KIMIFLOW_CREW_ROLE=main'
  assert_contains "$skill" "Do not run Kimiflow phases or spawn research/implementation/review workers in Captain"
  assert_contains "$skill" "owns the complete Kimiflow workflow"
  assert_contains "$skill" "kimiflow_crew"
  assert_contains "$skill" "main_reachable"
  assert_contains "$skill" "worker_reachable"
  assert_contains "$skill" "Stock FirstMate alone owns"
  assert_contains "$skill" "stage=research"
  assert_contains "$skill" "visible FirstMate"
  assert_contains "$skill" "stock FirstMate Calm"
  assert_contains "$skill" "without choosing a new verbosity"
  assert_contains "$skill" "never poll status or use sleep loops"
  assert_contains "$skill" "Host boundary"
  for action in activate start_main spawn status report send drain integrate teardown; do
    assert_contains "$extension" "\"$action\""
  done
  for command in fm-session-start.sh fm-brief.sh fm-spawn.sh fm-peek.sh fm-crew-state.sh fm-send.sh fm-wake-drain.sh fm-watch-arm.sh fm-merge-local.sh fm-teardown.sh; do
    assert_contains "$extension" "$command"
  done
  assert_contains "$extension" "spawn_unverified"
  assert_contains "$extension" "HERDR_ENV"
  assert_contains "$extension" "KIMIFLOW_WORKER_VERBOSITY="
  assert_contains "$extension" "KIMIFLOW_CREW_ROLE"
  assert_contains "$extension" "FM_ROOT_OVERRIDE"
  assert_contains "$extension" "FM_HOME"
  assert_contains "$extension" "fm-calm.ts"
  assert_contains "$extension" "research_ship_forbidden"
  node --check "$extension" >/dev/null || fail "kimiflow crew adapter does not parse"
  unit_output="$(node --test "$ROOT/hosts/pi/tests/kimiflow-crew.test.mjs")" \
    || { printf '%s\n' "$unit_output" >&2; fail "kimiflow crew adapter unit tests failed"; }
  if [ "${KIMIFLOW_REQUIRE_NO_SKIPS:-0}" = 1 ] && printf '%s\n' "$unit_output" | grep -Fq '# SKIP'; then
    printf '%s\n' "$unit_output" >&2
    fail "kimiflow crew adapter unit tests skipped required stock evidence"
  fi

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
  for script in fm-session-start.sh fm-brief.sh fm-spawn.sh fm-peek.sh fm-crew-state.sh fm-send.sh fm-wake-drain.sh fm-watch-arm.sh fm-teardown.sh fm-project-mode.sh fm-merge-local.sh; do
    [ -x "$firstmate/bin/$script" ] || fail "FirstMate capability is missing: bin/$script"
  done
}

run_live() {
  for command in git herdr jq pi python3 rg treehouse; do require_command "$command"; done

  firstmate=${KIMIFLOW_FIRSTMATE_ROOT:-"$ROOT/../firstmate"}
  firstmate="$(CDPATH= cd -- "$firstmate" 2>/dev/null && pwd -P)" \
    || fail "set KIMIFLOW_FIRSTMATE_ROOT to a stock FirstMate checkout"
  verify_firstmate_capabilities
  [ -z "$(git -C "$firstmate" status --porcelain --untracked-files=no)" ] \
    || fail "FirstMate checkout has tracked changes"
  export KIMIFLOW_TEST_FIRSTMATE_ROOT="$firstmate"
  export KIMIFLOW_REQUIRE_NO_SKIPS=1
  run_static
  user_pi_agent=${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}
  [ -f "$user_pi_agent/auth.json" ] || fail "an authenticated Pi credential store is required"

  stock_log="$(mktemp "${TMPDIR:-/tmp}/kimiflow-firstmate-stock.XXXXXX")"
  bash "$firstmate/tests/fm-pi-primary-types.test.sh" >"$stock_log" 2>&1 \
    || { cat "$stock_log" >&2; fail "stock FirstMate Pi checks failed"; }
  bash "$firstmate/tests/fm-backend-herdr.test.sh" >>"$stock_log" 2>&1 \
    || { cat "$stock_log" >&2; fail "stock FirstMate Herdr checks failed"; }
  grep -Fq 'skip:' "$stock_log" && { cat "$stock_log" >&2; fail "a required stock FirstMate check skipped"; }

  work="$(mktemp -d "${TMPDIR:-/tmp}/kimiflow-firstmate-live.XXXXXX")"
  fm_root="$work/firstmate-root"
  project="$work/fixture"
  pi_agent="$work/pi-agent"
  main_task=kimiflow-live-main
  scout_task=kimiflow-live-research
  lab="$("$firstmate/bin/fm-herdr-lab.sh" name kimiflow-nested-main)"
  main_worktree=
  main_home=
  cleanup() {
    if [ "${KIMIFLOW_KEEP_LIVE:-0}" = 1 ]; then
      printf 'firstmate integration: preserved live fixture at %s (Herdr lab %s)\n' "$work" "$lab" >&2
      return
    fi
    if [ -n "$main_home" ]; then
      FM_HOME="$main_home" FM_ROOT_OVERRIDE="$fm_root" "$fm_root/bin/fm-teardown.sh" "$scout_task" --force >/dev/null 2>&1 || true
    fi
    captain_home="$project/.kimiflow/session/FIRSTMATE-CAPTAIN-v1"
    if [ -d "$captain_home" ]; then
      FM_HOME="$captain_home" FM_ROOT_OVERRIDE="$fm_root" "$fm_root/bin/fm-teardown.sh" "$main_task" --force >/dev/null 2>&1 || true
    fi
    "$fm_root/bin/fm-herdr-lab.sh" teardown "$lab" >/dev/null 2>&1 || true
    rm -rf -- "$work"
    rm -f -- "$stock_log"
  }
  trap cleanup EXIT INT TERM

  git clone -q --no-hardlinks "$firstmate" "$fm_root"
  mkdir -p "$project" "$pi_agent"
  cp "$user_pi_agent/auth.json" "$pi_agent/auth.json"
  chmod 600 "$pi_agent/auth.json"

  git init -q -b trunk "$project"
  git -C "$project" config user.name 'Kimiflow Nested Main E2E'
  git -C "$project" config user.email 'kimiflow-nested-main@invalid.example'
  printf 'NESTED_SCOUT_PASS\n' > "$project/README.md"
  mkdir -p "$project/.kimiflow/project"
  printf 'quiet\n' > "$project/.kimiflow/verbosity"
  printf 'immutable run plan sentinel\n' > "$project/.kimiflow/project/RUN-LIVE.md"
  plan_before="$(shasum -a 256 "$project/.kimiflow/project/RUN-LIVE.md" | awk '{print $1}')"
  kimiflow_state_manifest() {
    python3 - "$project" <<'PY'
import hashlib
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1]) / ".kimiflow"
excluded = "session/FIRSTMATE-CAPTAIN-v1"
for candidate in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
    relative = candidate.relative_to(root).as_posix()
    if relative == "session":
        continue
    if relative == excluded or relative.startswith(excluded + "/"):
        continue
    stat = candidate.lstat()
    if candidate.is_symlink():
        payload = "link:" + os.readlink(candidate)
    elif candidate.is_file():
        payload = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    else:
        payload = "directory"
    print(f"{relative}\t{stat.st_mode}\t{payload}")
PY
  }
  state_before="$(kimiflow_state_manifest)"
  (cd "$project" && PI_CODING_AGENT_DIR="$pi_agent" pi install "$ROOT" -l --approve >/dev/null)
  grep -Fq "$ROOT" "$project/.pi/settings.json" || fail "fixture does not pin the current Kimiflow checkout"
  git -C "$project" add README.md .pi/settings.json
  git -C "$project" commit -qm 'fixture: initialize'
  base_head="$(git -C "$project" rev-parse HEAD)"

  "$fm_root/bin/fm-herdr-lab.sh" provision "$lab"
  lab_run() { "$fm_root/bin/fm-herdr-lab.sh" run "$lab" "$@"; }
  submit_prompt() {
    lab_run pane send-text "$1" "$2" >/dev/null || return 1
    sleep 0.5
    lab_run pane send-keys "$1" Enter >/dev/null
  }
  wait_for_shell() {
    wait_attempts=0
    while [ "$wait_attempts" -lt 80 ]; do
      process_json="$(lab_run pane process-info --pane "$captain_pane" 2>/dev/null || true)"
      if printf '%s' "$process_json" | jq -e '
        (.result.process_info.foreground_processes | length) == 1
        and (.result.process_info.foreground_processes[0].name | test("^(?:ba|z|fi)?sh$"))
      ' >/dev/null 2>&1; then
        return 0
      fi
      sleep 0.25
      wait_attempts=$((wait_attempts + 1))
    done
    return 1
  }
  workspace_json="$(lab_run workspace create --cwd "$project" --label kimiflow-live --no-focus)" \
    || fail "isolated Herdr workspace could not be created"
  captain_pane="$(printf '%s' "$workspace_json" | jq -r '.result.root_pane.pane_id // empty')"
  [ -n "$captain_pane" ] || fail "isolated Herdr workspace did not return a Captain pane"
  wait_for_shell || fail "isolated Herdr Captain pane did not settle at its shell"

  root_q="$(printf '%q' "$fm_root")"
  agent_q="$(printf '%q' "$pi_agent")"
  lab_run pane run "$captain_pane" "export KIMIFLOW_FIRSTMATE_ROOT=$root_q PI_CODING_AGENT_DIR=$agent_q" >/dev/null \
    || fail "Captain environment could not be prepared"
  wait_for_shell || fail "Captain shell did not settle after environment setup"
  model=${KIMIFLOW_LIVE_MODEL:-openai-codex/gpt-5.6-sol}
  model_q="$(printf '%q' "$model")"
  lab_run pane run "$captain_pane" "pi --approve --name kimiflow-live-captain --model $model_q --thinking low" >/dev/null \
    || fail "Pi Captain command could not be launched in the isolated Herdr pane"
  attempts=0
  while [ "$attempts" -lt 240 ]; do
    captain_agent="$(lab_run agent get "$captain_pane" 2>/dev/null || true)"
    captain_startup="$(lab_run pane read "$captain_pane" --source recent-unwrapped --lines 160 2>/dev/null || true)"
    if printf '%s' "$captain_agent" | jq -e '
      .result.agent.agent == "pi" and .result.agent.agent_status == "idle"
    ' >/dev/null 2>&1 \
      && printf '%s\n' "$captain_startup" | grep -Fq '[Skills]' \
      && printf '%s\n' "$captain_startup" | grep -Fq '[Extensions]'; then
      break
    fi
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 240 ] || fail "Pi Captain did not become ready in the isolated Herdr pane"

  captain_prompt="Use the installed Kimiflow skill as the conversational Captain. Activate kimiflow_crew with verbosity=quiet. Start exactly one Main named $main_task. Freeze this exact user request: Perform a read-only nested-orchestration proof. The Main must first report needs-decision with the exact question PROCEED_WITH_SCOUT?. Only after the Captain forwards the answer PROCEED_WITH_SCOUT may Main activate its own crew, spawn the visible research Scout $scout_task with stage=research, wait for its completed report, and finish. The Scout must inspect README.md read-only and report first line NESTED_SCOUT_PASS. No project product bytes may change. Freeze this plan too: Captain owns only Main; Main owns the Scout; all user dialogue stays here. After main_reachable print exactly KIMIFLOW_CAPTAIN_FREE and stay available. Do not answer the Main's question yourself. When it arrives, show it here and print exactly KIMIFLOW_DECISION_NEEDED."
  submit_prompt "$captain_pane" "$captain_prompt" \
    || fail "Captain prompt could not be submitted"

  captain_home="$project/.kimiflow/session/FIRSTMATE-CAPTAIN-v1"
  main_meta="$captain_home/state/$main_task.meta"
  attempts=0
  while [ "$attempts" -lt 240 ]; do
    captain_view="$(lab_run pane read "$captain_pane" --source recent-unwrapped --lines 240 2>/dev/null || true)"
    if [ -f "$main_meta" ] \
      && [ "$(printf '%s\n' "$captain_view" | grep -Fc KIMIFLOW_CAPTAIN_FREE || true)" -ge 2 ]; then
      break
    fi
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 240 ] || fail "Captain did not return after creating Main"
  grep -Fxq 'backend=herdr' "$main_meta" || fail "Main is not a visible Herdr endpoint"
  grep -Fxq "model=$model" "$main_meta" || fail "Main did not inherit the Captain's active Pi model"
  grep -Fxq 'effort=low' "$main_meta" || fail "Main did not inherit the Captain's active Pi thinking level"
  main_worktree="$(sed -n 's/^worktree=//p' "$main_meta" | tail -1)"
  [ -n "$main_worktree" ] && [ -d "$main_worktree" ] || fail "Main control worktree is missing"
  main_home="$main_worktree/.kimiflow/session/FIRSTMATE-MAIN-v1/$main_task"

  submit_prompt "$captain_pane" "Antworte exakt KIMIFLOW_CAPTAIN_RESPONSIVE und fuehre keine Crew-Aktion aus." \
    || fail "Captain responsiveness prompt could not be submitted"
  attempts=0
  while [ "$attempts" -lt 240 ]; do
    captain_view="$(lab_run pane read "$captain_pane" --source recent-unwrapped --lines 260 2>/dev/null || true)"
    [ "$(printf '%s\n' "$captain_view" | grep -Fc KIMIFLOW_CAPTAIN_RESPONSIVE || true)" -ge 2 ] && break
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 240 ] || fail "Captain was not conversationally available while Main was running"

  attempts=0
  while [ "$attempts" -lt 600 ]; do
    captain_view="$(lab_run pane read "$captain_pane" --source recent-unwrapped --lines 360 2>/dev/null || true)"
    if [ "$(printf '%s\n' "$captain_view" | grep -Fc KIMIFLOW_DECISION_NEEDED || true)" -ge 2 ] \
      && [ "$(printf '%s\n' "$captain_view" | grep -Fc 'PROCEED_WITH_SCOUT?' || true)" -ge 2 ]; then
      break
    fi
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 600 ] || fail "Main decision did not surface in the Captain"

  reply="PROCEED_WITH_SCOUT. Forward this exact answer to $main_task through kimiflow_crew, then stay available until Main finishes."
  submit_prompt "$captain_pane" "$reply" \
    || fail "Captain decision could not be submitted"

  scout_meta="$main_home/state/$scout_task.meta"
  scout_report="$main_home/data/$scout_task/report.md"
  main_status="$captain_home/state/$main_task.status"
  scout_endpoint_seen=0
  attempts=0
  while [ "$attempts" -lt 900 ]; do
    if [ -f "$scout_meta" ] \
      && grep -Fxq 'kind=scout' "$scout_meta" \
      && grep -Fxq 'backend=herdr' "$scout_meta" \
      && grep -Fxq "model=$model" "$scout_meta" \
      && grep -Fxq 'effort=low' "$scout_meta"; then
      scout_endpoint_seen=1
    fi
    if [ -f "$main_status" ] && grep -Eq '^done:' "$main_status" \
      && [ -f "$scout_report" ] && grep -Fq 'NESTED_SCOUT_PASS' "$scout_report"; then
      break
    fi
    if [ -f "$main_status" ] && grep -Eq '^(failed|blocked):' "$main_status"; then
      cat "$main_status" >&2
      fail "Kimiflow Main terminated before its nested Scout proof completed"
    fi
    sleep 0.5
    attempts=$((attempts + 1))
  done
  [ "$attempts" -lt 900 ] || fail "nested Main/Scout result did not return to Captain"

  [ "$scout_endpoint_seen" = 1 ] || fail "Main-owned visible FirstMate Scout with inherited Pi model was never observed"
  grep -Fq NESTED_SCOUT_PASS "$scout_report" || fail "Main-owned Scout report omitted the evidence marker"
  [ -z "$(find "$main_home/state" -maxdepth 1 -type f -name '*.meta' -print 2>/dev/null)" ] \
    || fail "Main finished while Main-owned FirstMate child metadata was still present"
  run_state="$(find "$main_worktree/.kimiflow" -mindepth 2 -maxdepth 2 -type f -name STATE.md -print 2>/dev/null | head -1)"
  [ -n "$run_state" ] || fail "Main did not initialize a standard Kimiflow Active Run in its control worktree"
  [ "$(cat "$captain_home/config/calm")" = on ] || fail "Captain FirstMate home did not enable Calm"
  [ "$(cat "$main_home/config/calm")" = on ] || fail "Main FirstMate home did not enable Calm for children"
  [ "$(git -C "$project" rev-parse HEAD)" = "$base_head" ] || fail "control orchestration changed the project commit"
  [ "$(shasum -a 256 "$project/.kimiflow/project/RUN-LIVE.md" | awk '{print $1}')" = "$plan_before" ] \
    || fail "control orchestration changed the existing Kimiflow plan"
  state_after="$(kimiflow_state_manifest)"
  if [ "$state_after" != "$state_before" ]; then
    printf 'firstmate integration: original Kimiflow state before:\n%s\n' "$state_before" >&2
    printf 'firstmate integration: original Kimiflow state after:\n%s\n' "$state_after" >&2
    fail "control orchestration changed original Kimiflow state outside the exact Captain runtime home"
  fi
  [ -z "$(git -C "$project" status --porcelain --untracked-files=all)" ] \
    || fail "control orchestration changed tracked project bytes"
  printf 'firstmate integration: Captain -> separate Main -> visible Main-owned Scout passed\n'
}

case "$MODE" in
  --static) run_static ;;
  --live) run_live ;;
  *) fail "usage: hooks/test-firstmate-integration.sh --static|--live" ;;
esac
