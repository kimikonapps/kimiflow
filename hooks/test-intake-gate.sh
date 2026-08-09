#!/usr/bin/env bash
# Contract-3/4 Product Intake lifecycle and PreToolUse barrier tests.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(dirname "$DIR")"
ACTIVE="$DIR/active-run.sh"
GATE="$DIR/intake-gate.sh"
FRONTEND="$DIR/frontend-quality-gate.sh"
VERBOSITY="$DIR/resolve-verbosity.sh"
PREFLIGHT="$DIR/workspace-preflight.sh"
WORKING_TREE="$DIR/working-tree-gate.sh"
ADAPTIVE="$DIR/adaptive-control.sh"
CODEBASE_BASIS="$DIR/codebase-basis.sh"
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
    # This suite exercises the resumable Contract-4 schema-1 lifecycle. A
    # pre-existing schema-1 request distinguishes it from fresh schema-2 runs.
    cat > "$REPO/.kimiflow/demo/INTAKE.md" <<'EOF'
<!-- kimiflow:intake contract=4 round=1 questions=1 selection=impact_uncertainty technical_questions=0 confirmation=concrete_product_flow -->
Product flow entry: The developer starts the existing Kimiflow flow.
User interaction: The assistant confirms the concrete flow once.
Visible delegation outcome: The assistant reports the completed result.
Unchanged path: Existing entrypoints remain unchanged.
Done scenario: Completion follows the terminal gate receipt.
EOF
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
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"rg -n \"Intake|intake|awaiting|Phase\" .kimiflow/demo"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "quoted_search_alternation_is_not_a_pipeline"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"rg --files .kimiflow | sort"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "readonly_pipeline_allowed_before_receipt"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"find . -maxdepth 2 -type f -print | LC_ALL=C sort"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "readonly_pipeline_with_bounded_environment_allowed"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"sed -n '\''1,80p'\'' .kimiflow/demo/STATE.md && test -e .kimiflow/project/INDEX.json; printf '\''index_exit=%s\\n'\'' \"$?\""}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "readonly_test_and_printf_chain_allowed_before_receipt"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:"rg --files .kimiflow | touch src/bypass.txt"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "readonly_pipeline_rejects_mutating_stage"
payload="$(jq -nc --arg d "$REPO" --arg c "\"$FRONTEND\" .kimiflow/demo --record-start --write" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "frontend_start_receipt_allowed_before_intake"
payload="$(jq -nc --arg d "$REPO" --arg c "env KIMIFLOW_PLUGIN_ROOT=\"$PLUGIN_ROOT\" KIMIFLOW_HOST=codex \"$ACTIVE\" phase-read-status --run .kimiflow/demo" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "trusted_plugin_root_assignment_allows_phase_setup"
preamble_command="export KIMIFLOW_PLUGIN_ROOT=\"$PLUGIN_ROOT\"
KIMIFLOW_HOST=codex \"\$KIMIFLOW_PLUGIN_ROOT/hooks/active-run.sh\" phase-read-status --run .kimiflow/demo"
payload="$(jq -nc --arg d "$REPO" --arg c "$preamble_command" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "trusted_two_line_plugin_root_preamble_allowed"
preamble_command="export KIMIFLOW_PLUGIN_ROOT=\"$WORK/fake\"
KIMIFLOW_HOST=codex \"\$KIMIFLOW_PLUGIN_ROOT/hooks/active-run.sh\" phase-read-status --run .kimiflow/demo"
payload="$(jq -nc --arg d "$REPO" --arg c "$preamble_command" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "forged_two_line_plugin_root_preamble_blocked"
payload="$(jq -nc --arg d "$REPO" --arg c "env KIMIFLOW_PLUGIN_ROOT=\"$PLUGIN_ROOT\" KIMIFLOW_HOST=codex \"$ACTIVE\" next-action --root \"$REPO\" --pretty" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "trusted_next_action_allowed_before_intake"
payload="$(jq -nc --arg d "$REPO" --arg c "env KIMIFLOW_PLUGIN_ROOT=\"$PLUGIN_ROOT\" KIMIFLOW_HOST=codex \"$ADAPTIVE\" classify --run .kimiflow/demo --write" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "trusted_adaptive_classification_allowed_before_intake"
payload="$(jq -nc --arg d "$REPO" --arg c "KIMIFLOW_HOST=codex \"$ADAPTIVE\" classify --run .kimiflow/demo --root \"$REPO\" --write --pretty" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "trusted_adaptive_classification_with_root_allowed_before_intake"
payload="$(jq -nc --arg d "$REPO" --arg c "KIMIFLOW_HOST=codex \"$PREFLIGHT\" route --run .kimiflow/demo --root \"$REPO\" --write --pretty" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "trusted_workspace_route_with_root_allowed_before_intake"
payload="$(jq -nc --arg d "$REPO" --arg c "KIMIFLOW_HOST=codex \"$PREFLIGHT\" route --run .kimiflow/demo --root \"$WORK\" --write --pretty" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "forged_workspace_route_root_blocked"
payload="$(jq -nc --arg d "$REPO" --arg c "env KIMIFLOW_PLUGIN_ROOT=\"$PLUGIN_ROOT\" KIMIFLOW_HOST=codex \"$WORKING_TREE\" --root \"$REPO\" --pretty" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "trusted_working_tree_gate_allowed_before_intake"
payload="$(jq -nc --arg d "$REPO" --arg c "env KIMIFLOW_PLUGIN_ROOT=$WORK/fake KIMIFLOW_HOST=codex $ACTIVE phase-read-status --run .kimiflow/demo" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "forged_plugin_root_assignment_blocked"
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
patch='*** Begin Patch
*** Update File: .kimiflow/demo/INTAKE.md
@@
-Product
+Product question
*** End Patch'
payload="$(jq -nc --arg d "$REPO" --arg p "$patch" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",command:$p,tool_input:{patch:$p}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "codex_apply_patch_top_level_command_allows_exact_intake"
payload="$(jq -nc --arg d "$REPO" --arg p "$patch" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{command:$p}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "codex_apply_patch_nested_command_allows_exact_intake"
product_patch='*** Begin Patch
*** Update File: src/app.txt
@@
-base
+changed
*** End Patch'
payload="$(jq -nc --arg d "$REPO" --arg p "$product_patch" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",command:$p,tool_input:{patch:$p}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "codex_apply_patch_top_level_command_blocks_product_write"
payload="$(jq -nc --arg d "$REPO" --arg p "$product_patch" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{command:$p}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "codex_apply_patch_nested_command_blocks_product_write"
setup_command="git rev-parse --is-inside-work-tree; git status --short --branch; KIMIFLOW_HOST=codex \"$VERBOSITY\" get; KIMIFLOW_HOST=codex \"$PREFLIGHT\" status --pretty; KIMIFLOW_HOST=codex \"$PREFLIGHT\" route --run .kimiflow/demo --write --pretty"
payload="$(jq -nc --arg d "$REPO" --arg c "$setup_command" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "bounded_phase0_setup_chain_allowed_before_intake"
await_command="env KIMIFLOW_PLUGIN_ROOT=\"$PLUGIN_ROOT\" KIMIFLOW_HOST=codex \"$ACTIVE\" await-user --root \"$REPO\" --run .kimiflow/demo --kind intake --round 1 --request .kimiflow/demo/INTAKE.md --reason scope_deliberation --write"
payload="$(jq -nc --arg d "$REPO" --arg c "$await_command" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "exact_intake_wait_registration_allowed"
payload="$(jq -nc --arg d "$REPO" --arg c "env KIMIFLOW_PLUGIN_ROOT=\"$PLUGIN_ROOT\" KIMIFLOW_HOST=codex \"$ACTIVE\" await-user --root \"$REPO\" --run .kimiflow/demo --kind intake --reason scope_deliberation --write" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "incomplete_intake_wait_registration_blocked"
payload="$(jq -nc --arg d "$REPO" --arg c "$setup_command; touch src/bypass.txt" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "phase0_setup_chain_rejects_extra_mutation"
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

# Fresh Contract-4 schema 2 keeps product bytes read-only after scope readiness,
# while permitting bounded run-local research until final confirmation.
rm -rf "$REPO/.kimiflow/session" "$REPO/.kimiflow/demo"
mkdir -p "$REPO/.kimiflow/demo"
KIMIFLOW_HOST=codex KIMIFLOW_SESSION_ID=owner-session "$ACTIVE" init-state --root "$REPO" --run .kimiflow/demo --mode feature --scope large --language en --title "Schema 2 intake" --write >/dev/null
KIMIFLOW_HOST=codex KIMIFLOW_SESSION_ID=owner-session "$ACTIVE" start --root "$REPO" --run .kimiflow/demo --mode feature --scope large --write >/dev/null
cat > "$REPO/.kimiflow/demo/INTAKE.md" <<'EOF'
# Product Intake
<!-- kimiflow:intake contract=4 schema=2 stage=scope round=1 confirmation=scope_deliberation user_language=en -->

Problem: Add one bounded feature without changing unrelated behavior.

Observable success: The named feature works and existing tests remain green.

Boundary: Only the named local files and standard library are in scope.

Option 1: Implement the complete named behavior.

Option 2: Add focused regression coverage for its failure boundary.

Included: The named behavior and focused tests.

Later: Unrelated enhancements.

Excluded: Network services and unrelated refactors.

Counter perspective: A smaller partial implementation would be cheaper but incomplete.

Completeness check: Goal, boundary, outcome, and exclusions are explicit.

Action scope_ready: Continue to bounded research and the final product contract.

Action discuss: Discuss or revise this scope draft.
EOF
KIMIFLOW_HOST=codex KIMIFLOW_SESSION_ID=owner-session "$ACTIVE" await-user --root "$REPO" --run .kimiflow/demo --kind intake --round 1 --request .kimiflow/demo/INTAKE.md --reason scope_deliberation --write >/dev/null
printf '{"cwd":"%s","session_id":"owner-session","prompt":"Continue to bounded research and the final product contract."}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" prompt-context >/dev/null
status_out="$(KIMIFLOW_HOST=codex KIMIFLOW_SESSION_ID=owner-session "$ACTIVE" status --root "$REPO")"
assert "printf '%s' '$status_out' | jq -e '.intake_stage == \"scope\" and .intake_action == \"scope_ready\" and (.intake_response_at | type == \"string\")' >/dev/null" "schema2_status_exposes_recorded_scope_action"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{patch:"*** Begin Patch\n*** Update File: src/app.txt\n@@\n-base\n+changed\n*** End Patch"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "schema2_scope_receipt_keeps_product_readonly"
payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"apply_patch",tool_input:{patch:"*** Begin Patch\n*** Add File: .kimiflow/demo/RESEARCH.md\n+bounded research\n*** End Patch"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "schema2_scope_receipt_allows_run_local_research"
payload="$(jq -nc --arg d "$REPO" --arg c "env KIMIFLOW_PLUGIN_ROOT=\"$PLUGIN_ROOT\" KIMIFLOW_HOST=codex \"$CODEBASE_BASIS\" create --root \"$REPO\" --run .kimiflow/demo --write" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "schema2_scope_receipt_allows_bounded_codebase_basis"
payload="$(jq -nc --arg d "$REPO" --arg c "KIMIFLOW_HOST=codex \"$CODEBASE_BASIS\" create --run .kimiflow/demo --write --pretty" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "schema2_scope_receipt_allows_pretty_codebase_basis"
payload="$(jq -nc --arg d "$REPO" --arg c "python3 -m unittest discover -s tests -v" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "schema2_scope_receipt_allows_exact_baseline_suite"
payload="$(jq -nc --arg d "$REPO" --arg c "python3 -m unittest discover -s tests" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "schema2_scope_receipt_rejects_other_python_commands"

cat > "$REPO/.kimiflow/demo/INTAKE-2.md" <<'EOF'
<!-- kimiflow:intake contract=4 schema=2 stage=final round=2 confirmation=final_contract cause=scope_ready user_language=en -->
Problem: Add one bounded feature without changing unrelated behavior.
Step 1: Validate the confirmed input.
Step 2: Implement and verify the bounded behavior.
Roles and boundaries: The user owns product behavior and the agent owns implementation details.
Included: The named behavior and focused tests.
Excluded: Network services and unrelated refactors.
Observable success: The named feature works and existing tests remain green.
End-to-end example: A valid request completes with the named result and no unrelated behavior changes.
Requirement R1: Preserve existing behavior outside the named feature.
Action confirmed: Confirm this final product contract.
Action corrected: Correct this final product contract.
EOF
KIMIFLOW_HOST=codex KIMIFLOW_SESSION_ID=owner-session "$ACTIVE" await-user --root "$REPO" --run .kimiflow/demo --kind intake --round 2 --request .kimiflow/demo/INTAKE-2.md --reason final_contract --write >/dev/null
printf '{"cwd":"%s","session_id":"owner-session","prompt":"Confirm this final product contract."}' "$REPO" | KIMIFLOW_HOST=codex "$ACTIVE" prompt-context >/dev/null
multiline_read="sed -n '1,80p' .kimiflow/demo/STATE.md
sed -n '1,80p' .kimiflow/demo/CODEBASE-BASIS.json
command -v python3 || true"
payload="$(jq -nc --arg d "$REPO" --arg c "$multiline_read" '{cwd:$d,session_id:"owner-session",tool_name:"Bash",tool_input:{command:$c}}')"
out="$(printf '%s' "$payload" | hook)"
assert "[ -z '$out' ]" "schema2_final_receipt_allows_fresh_worker_multiline_reads"

payload="$(jq -nc --arg d "$REPO" '{cwd:$d,session_id:"owner-session",tool_name:"Write",tool_input:{file_path:".kimiflow/demo/INTENT-LOCK.json",content:"replacement"}}')"
out="$(printf '%s' "$payload" | hook)"
assert "printf '%s' '$out' | jq -e '.hookSpecificOutput.permissionDecision == \"deny\"' >/dev/null" "schema2_final_receipt_keeps_authority_files_protected"

echo "----"
if [ "$fails" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$fails FAILED"; exit 1; fi
