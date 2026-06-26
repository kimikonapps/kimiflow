#!/usr/bin/env bash
# kimiflow — unit tests for background-run.sh.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/background-run.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — background-run uses jq"; exit 0
fi

reset_repo() {
  rm -rf "$REPO"
  mkdir -p "$REPO/hooks"
  git init -q "$REPO"
  git -C "$REPO" config user.email t@example.com
  git -C "$REPO" config user.name tester
  printf '.kimiflow/\n' > "$REPO/.gitignore"
  printf 'base\n' > "$REPO/hooks/a.sh"
  git -C "$REPO" add .gitignore hooks/a.sh
  git -C "$REPO" commit -q -m base
}

run_bg() {
  local command="$1"
  shift
  "$SCRIPT" "$command" --root "$REPO" "$@"
}

start_handle() {
  run_bg start --kind "${1:-deep-codebase}" --title "${2:-Map architecture}" --affected "${3:-hooks}" --write \
    | jq -r '.id'
}

make_ready() {
  local id="$1"
  printf '# Result\nDone.\n' > "$WORK/result.md"
  printf '["hooks/a.sh"]\n' > "$WORK/files.json"
  run_bg update --id "$id" --status ready --result "$WORK/result.md" --files "$WORK/files.json" --write >/dev/null
}

assert_jq() {
  local json="$1" expr="$2" name="$3"
  if printf '%s\n' "$json" | jq -e "$expr" >/dev/null 2>&1; then pass "$name"; else fail "$name"; printf '%s\n' "$json"; fi
}

assert_contains() {
  local text="$1" needle="$2" name="$3"
  if printf '%s\n' "$text" | grep -Fq -- "$needle"; then pass "$name"; else fail "$name (missing $needle in $text)"; fi
}

reset_repo
id="$(start_handle deep-codebase "Map architecture" hooks)"
[ -f "$REPO/.kimiflow/background/HANDLES.jsonl" ] && pass "start_writes_index" || fail "start_writes_index"
[ -s "$REPO/.kimiflow/background/$id/HANDOFF.md" ] && pass "start_writes_handoff" || fail "start_writes_handoff"
[ -s "$REPO/.kimiflow/background/$id/STATUS.json" ] && pass "start_writes_status" || fail "start_writes_status"
out="$(run_bg list --json)"
assert_jq "$out" '.total == 1 and .pending == 1 and .items[0].id == "'"$id"'"' "list_reports_pending_handle"
out="$(run_bg status --id "$id")"
assert_jq "$out" '.id == "'"$id"'" and .affected_paths == ["hooks"]' "status_reports_metadata"

make_ready "$id"
out="$(run_bg collect --id "$id")"
assert_contains "$out" $'BACKGROUND_HANDLE\tOPEN' "collect_opens_ready_current_handle"
assert_contains "$out" "reason=clean" "collect_open_reason_clean"
out="$(run_bg list --json)"
assert_jq "$out" '.ready == 1 and .collectable == 1' "list_reports_collectable_ready"

reset_repo
id="$(start_handle docs "Docs" hooks)"
run_bg update --id "$id" --status ready --write >/dev/null
out="$(run_bg list --json)"
assert_jq "$out" '.ready == 1 and .collectable == 0 and .items[0].collect_verdict == "CLOSED" and .items[0].collect_reason == "result_missing"' "list_excludes_missing_result_from_collectable"

reset_repo
id1="$(start_handle docs "Docs" hooks)"
id2="$(start_handle docs "Docs" hooks)"
printf '"not object"\n' > "$REPO/.kimiflow/background/$id1/STATUS.json"
out="$(run_bg list --json)"
assert_jq "$out" '.total == 1 and .pending == 1 and .items[0].id == "'"$id2"'"' "list_skips_corrupt_scalar_status"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
printf 'unstaged drift\n' > "$REPO/hooks/a.sh"
out="$(run_bg list --json)"
assert_jq "$out" '.ready == 1 and .collectable == 0 and .stale == 1 and .items[0].collect_reason == "stale"' "list_marks_drifted_ready_handle_stale"

reset_repo
id="$(start_handle deep-codebase "Map architecture" hooks)"
make_ready "$id"
printf 'tracked drift\n' > "$REPO/hooks/a.sh"
git -C "$REPO" add hooks/a.sh
git -C "$REPO" commit -q -m drift
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=stale" "collect_closes_after_committed_drift"

reset_repo
id="$(start_handle deep-codebase "Map architecture" hooks)"
make_ready "$id"
printf 'staged drift\n' > "$REPO/hooks/a.sh"
git -C "$REPO" add hooks/a.sh
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=stale" "collect_closes_after_staged_drift"

reset_repo
id="$(start_handle deep-codebase "Map architecture" hooks)"
make_ready "$id"
printf 'unstaged drift\n' > "$REPO/hooks/a.sh"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=stale" "collect_closes_after_unstaged_drift"

reset_repo
id="$(start_handle deep-codebase "Map architecture" hooks)"
make_ready "$id"
printf 'new\n' > "$REPO/hooks/new.sh"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=stale" "collect_closes_after_untracked_drift"

reset_repo
id="$(run_bg start --kind docs --title "Docs" --affected ./hooks/ --write | jq -r '.id')"
make_ready "$id"
printf 'unstaged drift\n' > "$REPO/hooks/a.sh"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=stale" "normalized_dot_slash_directory_matches"

reset_repo
if run_bg start --kind docs --title "Bad affected" --affected ../hooks --write >/dev/null 2>&1; then
  fail "unsafe_affected_rejected"
else
  pass "unsafe_affected_rejected"
fi
if run_bg start --kind docs --title "Bad affected" --affected .kimiflow/project --write >/dev/null 2>&1; then
  fail "kimiflow_affected_rejected"
else
  pass "kimiflow_affected_rejected"
fi
if run_bg start --kind docs --title "Bad affected" --affected hooks/. --write >/dev/null 2>&1; then
  fail "dot_segment_affected_rejected"
else
  pass "dot_segment_affected_rejected"
fi
if run_bg start --kind docs --title "Bad affected" --affected hooks//a.sh --write >/dev/null 2>&1; then
  fail "double_slash_affected_rejected"
else
  pass "double_slash_affected_rejected"
fi

reset_repo
if run_bg status --id '../escape' >/dev/null 2>&1; then
  fail "unsafe_id_rejected"
else
  pass "unsafe_id_rejected"
fi
if run_bg status --id 'bh_bad$id' >/dev/null 2>&1; then
  fail "unsafe_id_special_chars_rejected"
else
  pass "unsafe_id_special_chars_rejected"
fi

reset_repo
id="$(start_handle docs "Docs" hooks)"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=not_ready" "pending_collect_closes"
run_bg update --id "$id" --status ready --write >/dev/null
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=result_missing" "missing_result_closes"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
old_result="$(cat "$REPO/.kimiflow/background/$id/RESULT.md")"
printf '# Result\nNew but invalid.\n' > "$WORK/new-result.md"
printf '{bad json\n' > "$WORK/invalid-files.json"
if run_bg update --id "$id" --status ready --result "$WORK/new-result.md" --files "$WORK/invalid-files.json" --write >/dev/null 2>&1; then
  fail "invalid_files_update_rejected"
else
  pass "invalid_files_update_rejected"
fi
if [ "$(cat "$REPO/.kimiflow/background/$id/RESULT.md")" = "$old_result" ] && jq -e . "$REPO/.kimiflow/background/$id/FILES.json" >/dev/null 2>&1; then
  pass "invalid_files_update_left_existing_files_intact"
else
  fail "invalid_files_update_left_existing_files_intact"
fi

reset_repo
id="$(start_handle docs "Docs" hooks)"
printf 'victim-old\n' > "$WORK/victim.txt"
ln -sf "$WORK/victim.txt" "$REPO/.kimiflow/background/$id/RESULT.md"
printf '# Result\nPayload.\n' > "$WORK/payload.md"
printf '["hooks/a.sh"]\n' > "$WORK/files.json"
if run_bg update --id "$id" --status ready --result "$WORK/payload.md" --files "$WORK/files.json" --write >/dev/null 2>&1; then
  fail "update_rejects_result_symlink"
elif [ "$(cat "$WORK/victim.txt")" = "victim-old" ] && [ -L "$REPO/.kimiflow/background/$id/RESULT.md" ]; then
  pass "update_rejects_result_symlink"
else
  fail "update_rejects_result_symlink"
fi

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
printf '["hooks/a.sh"]\n' > "$WORK/files.json"
mkdir "$REPO/.kimiflow/background/$id/FILES.json.dir"
rm "$REPO/.kimiflow/background/$id/FILES.json"
mv "$REPO/.kimiflow/background/$id/FILES.json.dir" "$REPO/.kimiflow/background/$id/FILES.json"
if run_bg update --id "$id" --status ready --result "$WORK/payload.md" --files "$WORK/files.json" --write >/dev/null 2>&1; then
  fail "update_rejects_directory_file_target"
else
  pass "update_rejects_directory_file_target"
fi

reset_repo
id="$(start_handle docs "Docs" hooks)"
printf '{"status":"pending"}\n' > "$WORK/status-victim.json"
rm "$REPO/.kimiflow/background/$id/STATUS.json"
ln -sf "$WORK/status-victim.json" "$REPO/.kimiflow/background/$id/STATUS.json"
if run_bg update --id "$id" --status ready --write >/dev/null 2>&1; then
  fail "update_rejects_status_symlink"
elif grep -q '"status":"pending"' "$WORK/status-victim.json" && [ -L "$REPO/.kimiflow/background/$id/STATUS.json" ]; then
  pass "update_rejects_status_symlink"
else
  fail "update_rejects_status_symlink"
fi
if run_bg collect --id "$id" >/dev/null 2>&1; then
  fail "collect_rejects_status_symlink"
else
  pass "collect_rejects_status_symlink"
fi

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
printf '# External Result\n' > "$WORK/external-result.md"
rm "$REPO/.kimiflow/background/$id/RESULT.md"
ln -sf "$WORK/external-result.md" "$REPO/.kimiflow/background/$id/RESULT.md"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=result_invalid" "collect_rejects_result_symlink"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
jq '.result_path = "hooks/a.sh"' "$REPO/.kimiflow/background/$id/STATUS.json" > "$WORK/status.json"
mv "$WORK/status.json" "$REPO/.kimiflow/background/$id/STATUS.json"
rm "$REPO/.kimiflow/background/$id/RESULT.md"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=result_missing" "tampered_result_path_ignored"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
jq '.affected_paths = "hooks"' "$REPO/.kimiflow/background/$id/STATUS.json" > "$WORK/status.json"
mv "$WORK/status.json" "$REPO/.kimiflow/background/$id/STATUS.json"
printf 'unstaged drift\n' > "$REPO/hooks/a.sh"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=affected_invalid" "malformed_affected_paths_close"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
jq '.affected_paths = ["hooks/."]' "$REPO/.kimiflow/background/$id/STATUS.json" > "$WORK/status.json"
mv "$WORK/status.json" "$REPO/.kimiflow/background/$id/STATUS.json"
printf 'unstaged drift\n' > "$REPO/hooks/a.sh"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=affected_invalid" "persisted_dot_segment_affected_paths_close"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
jq '.affected_paths = ["hooks//a.sh"]' "$REPO/.kimiflow/background/$id/STATUS.json" > "$WORK/status.json"
mv "$WORK/status.json" "$REPO/.kimiflow/background/$id/STATUS.json"
printf 'unstaged drift\n' > "$REPO/hooks/a.sh"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=affected_invalid" "persisted_double_slash_affected_paths_close"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
jq '.affected_paths = ["../hooks"]' "$REPO/.kimiflow/background/$id/STATUS.json" > "$WORK/status.json"
mv "$WORK/status.json" "$REPO/.kimiflow/background/$id/STATUS.json"
printf 'unstaged drift\n' > "$REPO/hooks/a.sh"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=affected_invalid" "persisted_traversal_affected_paths_close"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
jq '.affected_paths = [".kimiflow/project"]' "$REPO/.kimiflow/background/$id/STATUS.json" > "$WORK/status.json"
mv "$WORK/status.json" "$REPO/.kimiflow/background/$id/STATUS.json"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=affected_invalid" "persisted_kimiflow_affected_paths_close"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
jq '.base_commit = "deadbeef"' "$REPO/.kimiflow/background/$id/STATUS.json" > "$WORK/status.json"
mv "$WORK/status.json" "$REPO/.kimiflow/background/$id/STATUS.json"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=base_invalid" "invalid_base_closes"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
jq '.affected_paths = []' "$REPO/.kimiflow/background/$id/STATUS.json" > "$WORK/status.json"
mv "$WORK/status.json" "$REPO/.kimiflow/background/$id/STATUS.json"
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=affected_missing" "missing_affected_closes"

reset_repo
id="$(start_handle docs "Docs" hooks)"
make_ready "$id"
run_bg cancel --id "$id" --reason "not needed" --write >/dev/null
out="$(run_bg collect --id "$id")"
assert_contains "$out" "reason=status_cancelled" "cancelled_collect_closes"
if run_bg update --id "$id" --status ready --write >/dev/null 2>&1; then
  fail "terminal_reopen_rejected"
else
  pass "terminal_reopen_rejected"
fi

reset_repo
id="$(start_handle security "Security scan" hooks)"
make_ready "$id"
out="$(run_bg status --id "$id")"
assert_jq "$out" '.candidate_only == true and .collect_policy == "foreground_orchestrator_verifies_before_apply"' "security_output_candidate_only"
run_bg mark-stale --id "$id" --reason "manual stale" --write >/dev/null
[ -s "$REPO/.kimiflow/background/$id/RESULT.md" ] && pass "mark_stale_keeps_result" || fail "mark_stale_keeps_result"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
