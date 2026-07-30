#!/usr/bin/env bash
# Contract-3/4 Product Intake lifecycle and PreToolUse barrier tests.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
ACTIVE="$DIR/active-run.sh"
GATE="$DIR/intake-gate.sh"
FRONTEND="$DIR/frontend-quality-gate.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
trap 'rm -rf "$WORK"' EXIT
fails=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1" >&2; fails=$((fails + 1)); }
assert() { if eval "$1"; then pass "$2"; else fail "$2"; fi; }

reset_repo() {
  contract="${1:-3}"
  rm -rf "$REPO"
  mkdir -p "$REPO/.kimiflow/demo" "$REPO/src"
  git -C "$REPO" init -q
  git -C "$REPO" config user.email test@example.test
  git -C "$REPO" config user.name test
  printf '.kimiflow/\n' > "$REPO/.gitignore"
  printf 'base\n' > "$REPO/src/app.txt"
  cat > "$REPO/.kimiflow/demo/STATE.md" <<'EOF'
Flow schema: 4
Intent contract: 3
Status: active
Mode: feature
Scope: large
Affected files: src/app.txt
Phase 0: in-progress
EOF
  if [ "$contract" = 4 ]; then
    sed \
      -e 's/Flow schema: 4/Flow schema: 5/' \
      -e 's/Intent contract: 3/Intent contract: 4/' \
      "$REPO/.kimiflow/demo/STATE.md" \
      > "$REPO/.kimiflow/demo/STATE.md.next"
    printf 'Convergence contract: 1\nConformance contract: 1\nExecution contract: 1\n' \
      >> "$REPO/.kimiflow/demo/STATE.md.next"
    mv "$REPO/.kimiflow/demo/STATE.md.next" \
      "$REPO/.kimiflow/demo/STATE.md"
  fi
  git -C "$REPO" add .gitignore src/app.txt
  git -C "$REPO" commit -qm base
  KIMIFLOW_HOST=codex KIMIFLOW_SESSION_ID=owner-session "$ACTIVE" start --root "$REPO" --run .kimiflow/demo --mode feature --scope large --write >/dev/null
}

write_request() {
  round="$1"; name=INTAKE.md; extra=""
  contract="$(jq -r '.intent_contract' "$REPO/.kimiflow/session/ACTIVE_RUN.json")"
  confirmation=""
  [ "$contract" = 4 ] && confirmation=" confirmation=concrete_product_flow"
  [ "$round" = 1 ] || { name=INTAKE-2.md; extra=" cause=first_response_conflict"; }
  printf '<!-- kimiflow:intake contract=%s round=%s questions=2 selection=impact_uncertainty technical_questions=0%s%s -->\n\n' "$contract" "$round" "$confirmation" "$extra" > "$REPO/.kimiflow/demo/$name"
  if [ "$contract" = 4 ]; then
    cat >> "$REPO/.kimiflow/demo/$name" <<'EOF'
Product flow entry: The developer asks the existing assistant to run Kimiflow for a named feature.
User interaction: The assistant confirms this flow once and remains available for questions.
Visible delegation outcome: Isolated workers build accepted slices while the assistant reports attention.
Unchanged path: Existing direct Codex and Claude Kimiflow entrypoints continue unchanged.
Done scenario: Completion is visible only after the terminal Kimiflow gate receipt.
EOF
  else
    printf 'Product questions only.\n' >> "$REPO/.kimiflow/demo/$name"
  fi
}

await_round() {
  round="$1"; name=INTAKE.md; [ "$round" = 1 ] || name=INTAKE-2.md
  KIMIFLOW_HOST=codex KIMIFLOW_SESSION_ID=owner-session "$ACTIVE" await-user --root "$REPO" --run .kimiflow/demo --kind intake --round "$round" --request ".kimiflow/demo/$name" --write >/dev/null
}

hook() { KIMIFLOW_HOST=codex "$GATE"; }

reset_repo
assert "jq -e '.intent_contract == \"3\"' '$REPO/.kimiflow/session/ACTIVE_RUN.json' >/dev/null" "start_pins_contract3"
write_request 1
await_round 1
printf '{"cwd":"%s","session_id":"owner-session","prompt":"PRIVATE ANSWER MUST NOT PERSIST"}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" prompt-context >/dev/null
assert "jq -e '.contract == 3 and .round == 1 and .channel == \"chat\"' '$REPO/.kimiflow/demo/INTAKE-RECEIPT-1.json' >/dev/null" "chat_writes_content_free_receipt"
assert "! grep -R -F 'PRIVATE ANSWER MUST NOT PERSIST' '$REPO/.kimiflow' >/dev/null" "chat_answer_not_persisted"
assert "jq -e 'has(\"awaiting_user\") | not' '$REPO/.kimiflow/session/ACTIVE_RUN.json' >/dev/null" "chat_receipt_clears_wait"

write_request 2
await_round 2
printf '{"cwd":"%s","session_id":"owner-session","tool_name":"request_user_input","tool_input":{"autoResolutionMs":60000},"tool_response":{"answers":{"scope":"default"}}}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" intake-response
assert "[ ! -e '$REPO/.kimiflow/demo/INTAKE-RECEIPT-2.json' ]" "auto_resolved_native_response_rejected"
printf '{"cwd":"%s","session_id":"owner-session","tool_name":"AskUserQuestion","tool_input":{"questions":[{"id":"scope"}]},"tool_response":{"status":"defaulted","value":"recommended"}}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" intake-response
assert "[ ! -e '$REPO/.kimiflow/demo/INTAKE-RECEIPT-2.json' ]" "defaulted_native_response_rejected"
printf '{"cwd":"%s","session_id":"owner-session","tool_name":"request_user_input","tool_input":{"questions":[{"id":"scope"}]},"tool_response":{"answers":{"scope":"explicit"}}}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" intake-response
assert "jq -e '.round == 2 and .channel == \"native_tool\"' '$REPO/.kimiflow/demo/INTAKE-RECEIPT-2.json' >/dev/null" "explicit_native_response_records_receipt"
assert "! grep -R -F 'explicit' '$REPO/.kimiflow/demo/INTAKE-RECEIPT-2.json' >/dev/null" "native_answer_not_persisted"

reset_repo
write_request 1
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"update_plan",tool_input:{plan:[]}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "planning_blocked_before_receipt"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"rg -n TODO src"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "readonly_inspection_allowed_before_receipt"
payload="$(jq -nc --arg d "$REPO" --arg c "\"$FRONTEND\" .kimiflow/demo --record-start --write" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "frontend_start_receipt_allowed_before_intake"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"hooks/active-run.sh status; touch src/bypass.txt"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "setup_command_chaining_blocked"
mkdir -p "$REPO/tools"
printf '#!/usr/bin/env bash\ntouch src/bypass.txt\n' > "$REPO/tools/active-run.sh"
chmod +x "$REPO/tools/active-run.sh"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"tools/active-run.sh status"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "untrusted_setup_script_path_blocked"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"rg TODO $(touch src/bypass.txt)"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "readonly_command_substitution_blocked"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{patch:"*** Begin Patch\n*** Update File: src/app.txt\n@@\n-base\n+changed\n*** End Patch"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "project_write_blocked_before_receipt"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{patch:"*** Begin Patch\n*** Update File: .kimiflow/demo/INTAKE.md\n@@\n-Product\n+Product question\n*** End Patch"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "exact_intake_artifact_allowed"
mv "$REPO/.kimiflow/demo/INTAKE.md" "$REPO/.kimiflow/demo/INTAKE.md.regular"
printf 'outside\n' > "$WORK/outside-intake.md"
ln -s "$WORK/outside-intake.md" "$REPO/.kimiflow/demo/INTAKE.md"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Write",tool_input:{file_path:".kimiflow/demo/INTAKE.md",content:"external overwrite"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "exact_intake_symlink_outside_run_blocked"
rm "$REPO/.kimiflow/demo/INTAKE.md"
mv "$REPO/.kimiflow/demo/INTAKE.md.regular" "$REPO/.kimiflow/demo/INTAKE.md"
mv "$REPO/.kimiflow/demo/INTAKE.md" "$REPO/.kimiflow/demo/INTAKE.md.regular"
printf 'hard-linked product\n' > "$REPO/product-intake.md"
ln "$REPO/product-intake.md" "$REPO/.kimiflow/demo/INTAKE.md"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Write",tool_input:{file_path:".kimiflow/demo/INTAKE.md",content:"product overwrite"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "exact_intake_hardlink_blocked"
rm "$REPO/.kimiflow/demo/INTAKE.md"
mv "$REPO/.kimiflow/demo/INTAKE.md.regular" "$REPO/.kimiflow/demo/INTAKE.md"
mkdir "$REPO/product-run"
printf 'product alias\n' > "$REPO/product-run/INTAKE.md"
mv "$REPO/.kimiflow/demo" "$REPO/.kimiflow/demo.regular"
ln -s "$REPO/product-run" "$REPO/.kimiflow/demo"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Write",tool_input:{file_path:".kimiflow/demo/INTAKE.md",content:"product overwrite"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "intake_run_directory_alias_blocked"
rm "$REPO/.kimiflow/demo"
mv "$REPO/.kimiflow/demo.regular" "$REPO/.kimiflow/demo"
mkdir -p "$REPO/docs"
printf 'unrelated\n' > "$REPO/docs/INTAKE.md"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{patch:"*** Begin Patch\n*** Update File: docs/INTAKE.md\n@@\n-unrelated\n+changed\n*** End Patch"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "intake_basename_outside_run_blocked"
await_round 1
printf '{"cwd":"%s","session_id":"owner-session","prompt":"answer"}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" prompt-context >/dev/null
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{patch:"*** Begin Patch\n*** Update File: src/app.txt\n@@\n-base\n+changed\n*** End Patch"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "project_write_allowed_after_receipt"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Write",tool_input:{file_path:".kimiflow/demo/INTENT-LOCK.json",content:"replacement"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "intent_lock_remains_protected_after_receipt"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"other-session",tool_name:"update_plan",tool_input:{plan:[]}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "nonowner_session_keeps_existing_routing"

reset_repo 4
assert "jq -e '.intent_contract == \"4\"' '$REPO/.kimiflow/session/ACTIVE_RUN.json' >/dev/null" "start_pins_contract4"
printf '<!-- kimiflow:intake contract=4 round=1 questions=2 selection=impact_uncertainty technical_questions=0 -->\n\nMissing confirmation.\n' > "$REPO/.kimiflow/demo/INTAKE.md"
if await_round 1 >/dev/null 2>&1; then
  fail "contract4_rejects_intake_without_concrete_flow_confirmation"
else
  pass "contract4_rejects_intake_without_concrete_flow_confirmation"
fi
printf '<!-- kimiflow:intake contract=4 round=1 questions=2 selection=impact_uncertainty technical_questions=0 confirmation=concrete_product_flow -->\n\nGeneric confirmation without the five proposed values.\n' > "$REPO/.kimiflow/demo/INTAKE.md"
if await_round 1 >/dev/null 2>&1; then
  fail "contract4_rejects_confirmation_without_five_concrete_values"
else
  pass "contract4_rejects_confirmation_without_five_concrete_values"
fi
printf '<!-- kimiflow:intake contract=4 round=1 questions=2 selection=impact_uncertainty technical_questions=0 confirmation=concrete_product_flow -->\nProduct flow entry: TBD\nUser interaction: TBD\nVisible delegation outcome: TBD\nUnchanged path: TBD\nDone scenario: TBD\n' > "$REPO/.kimiflow/demo/INTAKE.md"
if await_round 1 >/dev/null 2>&1; then
  fail "contract4_rejects_tbd_product_flow"
else
  pass "contract4_rejects_tbd_product_flow"
fi
write_request 1
await_round 1
printf '{"cwd":"%s","session_id":"owner-session","prompt":"No, change the unchanged path"}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" prompt-context >/dev/null
assert "jq -e '.contract == 4 and .round == 1' '$REPO/.kimiflow/demo/INTAKE-RECEIPT-1.json' >/dev/null" "contract4_conflict_writes_causal_round1_receipt"
assert "jq -e '.awaiting_user == true and .intake_conflict == true' '$REPO/.kimiflow/session/ACTIVE_RUN.json' >/dev/null" "contract4_conflict_keeps_wait_readonly"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{patch:"*** Begin Patch\n*** Update File: src/app.txt\n@@\n-base\n+changed\n*** End Patch"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "contract4_conflict_does_not_unlock_product_writes"
write_request 2
await_round 2
printf '{"cwd":"%s","session_id":"owner-session","prompt":"confirmed"}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" prompt-context >/dev/null
assert "jq -e '.contract == 4 and .round == 2' '$REPO/.kimiflow/demo/INTAKE-RECEIPT-2.json' >/dev/null" "contract4_conflict_allows_one_causal_second_round"
assert "jq -e 'has(\"awaiting_user\") | not' '$REPO/.kimiflow/session/ACTIVE_RUN.json' >/dev/null" "contract4_second_round_confirmation_releases_wait"

reset_repo 4
write_request 1
await_round 1
printf '{"cwd":"%s","session_id":"owner-session","prompt":"confirmed"}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" prompt-context >/dev/null
assert "jq -e '.contract == 4 and .round == 1 and .channel == \"chat\"' '$REPO/.kimiflow/demo/INTAKE-RECEIPT-1.json' >/dev/null" "contract4_chat_writes_matching_receipt"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{patch:"*** Begin Patch\n*** Update File: src/app.txt\n@@\n-base\n+changed\n*** End Patch"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "contract4_project_write_allowed_after_receipt"

echo "----"
if [ "$fails" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$fails FAILED"; exit 1; fi
