#!/usr/bin/env bash
# kimiflow - unit tests for agentic-readiness.sh.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/agentic-readiness.sh"
ACTIVE_RUN="$(cd "$(dirname "$0")" && pwd)/active-run.sh"
BACKGROUND_RUN="$(cd "$(dirname "$0")" && pwd)/background-run.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
FAILS=0
trap 'rm -rf "$WORK"' EXIT

# Determinism: session-level vault signals must not leak in from the runner's env.
unset KIMIFLOW_OBSIDIAN_MCP_AVAILABLE KIMIFLOW_VAULT_MCP_AVAILABLE \
  KIMIFLOW_OBSIDIAN_AUTHENTICATED KIMIFLOW_VAULT_AUTHENTICATED

pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

assert_jq() {
  local json="$1" expr="$2" name="$3"
  if printf '%s\n' "$json" | jq -e "$expr" >/dev/null 2>&1; then pass "$name"; else fail "$name"; printf '%s\n' "$json"; fi
}

assert_contains() {
  local text="$1" needle="$2" name="$3"
  if printf '%s\n' "$text" | grep -Fq "$needle"; then pass "$name"; else fail "$name (missing $needle)"; fi
}

assert_not_contains() {
  local text="$1" needle="$2" name="$3"
  if printf '%s\n' "$text" | grep -Fq "$needle"; then fail "$name (unexpected $needle)"; else pass "$name"; fi
}

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed - agentic-readiness uses jq"
  exit 0
fi

reset_repo() {
  rm -rf "$REPO"
  mkdir -p "$REPO/src" "$REPO/.kimiflow/demo" "$REPO/.kimiflow/project"
  git init -q "$REPO"
  git -C "$REPO" config user.email t@example.com
  git -C "$REPO" config user.name tester
  printf '.kimiflow/\n' > "$REPO/.gitignore"
  printf 'base\n' > "$REPO/src/a.txt"
  git -C "$REPO" add .gitignore src/a.txt
  git -C "$REPO" commit -q -m base
  cat > "$REPO/.kimiflow/demo/STATE.md" <<'EOF'
Status: active
Mode: feature
Scope: large
Affected files:
- src/a.txt
Phase 0: done
Phase 1: done
Phase 2: done
Phase 3: done
Phase 4: done
EOF
  cat > "$REPO/.kimiflow/demo/INTENT.md" <<'EOF'
# Absicht
Build a small readiness fixture.
EOF
  cat > "$REPO/.kimiflow/demo/RESEARCH.md" <<'EOF'
# Recherche
Evidence: src/a.txt:1
EOF
  cat > "$REPO/.kimiflow/demo/PLAN.md" <<'EOF'
# Plan
Affected files:
- src/a.txt
Task maps AC-1.
EOF
  cat > "$REPO/.kimiflow/demo/ACCEPTANCE.md" <<'EOF'
# Acceptance
- AC-1 -> test: verify src/a.txt behavior.
EOF
  cat > "$REPO/.kimiflow/demo/CURRENT-STATE.json" <<'EOF'
{"schema_version":1,"current_state_risk":"high"}
EOF
  cat > "$REPO/.kimiflow/demo/CURRENT-STATE.md" <<'EOF'
Status: checked
- source_type: official_docs
  source_url: https://developers.openai.com/codex/skills
  summary: checked.
EOF
  cat > "$REPO/.kimiflow/project/VAULT-PROVIDER.json" <<'EOF'
{"schema_version":1,"available":true,"auth":{"authenticated":true,"status":"authenticated","source":"mcp"},"capabilities":{"direct_search":true,"mcp_direct_write":false}}
EOF
  "$ACTIVE_RUN" start --root "$REPO" --run .kimiflow/demo --mode feature --scope large --write >/dev/null
}

run_status() {
  KIMIFLOW_HOST=codex "$SCRIPT" status --root "$REPO" --run .kimiflow/demo
}

reset_repo
out="$(run_status)"
assert_jq "$out" '.readiness.level == "autonomous" and (.readiness.blockers | length) == 0 and .host == "codex" and .active_session.present == true and .provider.mcp_ready == true and .privacy.network_calls == false' "status_clean"
out="$(HOME="$WORK" run_status)"
assert_jq "$out" '(.root | startswith("~/"))' "status_output_privacy_root"

printf 'dirty\n' > "$REPO/src/a.txt"
out="$(run_status)"
assert_jq "$out" '.working_tree.dirty == true and (.readiness.blockers | index("working_tree_dirty")) and .readiness.level != "autonomous"' "status_dirty_blocks_autonomy"
gate="$(KIMIFLOW_HOST=codex "$SCRIPT" gate --root "$REPO" --run .kimiflow/demo --min-level governed)"
assert_contains "$gate" $'AGENTIC_READINESS_GATE\tCLOSED' "gate_fail_closed_dirty_tree"

reset_repo
rm "$REPO/.kimiflow/demo/CURRENT-STATE.md"
gate="$(KIMIFLOW_HOST=codex "$SCRIPT" gate --root "$REPO" --run .kimiflow/demo --min-level governed)"
assert_contains "$gate" "reason=current_state_gate_closed" "gate_fail_closed_current_state"

reset_repo
rm -rf "$REPO/.kimiflow/session"
gate="$(KIMIFLOW_HOST=codex "$SCRIPT" gate --root "$REPO" --run .kimiflow/demo --min-level governed)"
assert_contains "$gate" "reason=active_session_missing" "gate_fail_closed_active_session_missing"

reset_repo
cat > "$REPO/.kimiflow/demo/DIAGNOSIS.md" <<'EOF'
# Diagnosis
Root cause: src/a.txt:1
EOF
cat > "$REPO/.kimiflow/demo/BUG-REPRO.md" <<'EOF'
# Bug Reproduction
Red: failing fixture
Green: passing fixture
EOF
printf 'dirty for packet\n' > "$REPO/src/a.txt"
packet="$(KIMIFLOW_HOST=codex "$SCRIPT" packet --root "$REPO" --run .kimiflow/demo --kind review --write)"
packet_path="$(printf '%s\n' "$packet" | jq -r '.path')"
assert_jq "$packet" '.status == "packet_written" and .bytes <= 12000 and (.path | startswith(".kimiflow/demo/context-packets/"))' "packet_write_bounds"
if grep -q "^/Users/" "$REPO/$packet_path"; then fail "packet_uses_repo_relative_paths"; else pass "packet_uses_repo_relative_paths"; fi
packet_body="$(cat "$REPO/$packet_path")"
assert_contains "$packet_body" "## Acceptance" "packet_includes_acceptance_before_trim"
assert_contains "$packet_body" "## Changed Files" "packet_includes_changed_files"
assert_contains "$packet_body" "## Diagnosis" "packet_includes_diagnosis"
assert_contains "$packet_body" "## Bug Reproduction" "packet_includes_bug_repro"

if KIMIFLOW_HOST=codex "$SCRIPT" packet --root "$REPO" --run ../outside --kind review --write >/dev/null 2>&1; then
  fail "packet_rejects_unsafe_paths"
else
  pass "packet_rejects_unsafe_paths"
fi

reset_repo
rm -rf "$REPO/.kimiflow/demo/context-packets"
printf 'victim-old\n' > "$WORK/victim.txt"
ln -s "$WORK" "$REPO/.kimiflow/demo/context-packets"
if KIMIFLOW_HOST=codex "$SCRIPT" packet --root "$REPO" --run .kimiflow/demo --kind review --write >/dev/null 2>&1; then
  fail "packet_rejects_symlink_escape"
elif [ "$(cat "$WORK/victim.txt")" = "victim-old" ]; then
  pass "packet_rejects_symlink_escape"
else
  fail "packet_rejects_symlink_escape"
fi

reset_repo
printf 'outside-secret\n' > "$WORK/outside-secret.txt"
rm "$REPO/.kimiflow/demo/PLAN.md"
ln -s "$WORK/outside-secret.txt" "$REPO/.kimiflow/demo/PLAN.md"
if KIMIFLOW_HOST=codex "$SCRIPT" packet --root "$REPO" --run .kimiflow/demo --kind review --write >/dev/null 2>&1; then
  fail "packet_rejects_symlinked_sources"
else
  pass "packet_rejects_symlinked_sources"
fi

reset_repo
id="$("$BACKGROUND_RUN" start --root "$REPO" --kind deep-codebase --title "Map src" --affected src --write | jq -r '.id')"
printf '# Result\nDone.\n' > "$WORK/result.md"
printf '["src/a.txt"]\n' > "$WORK/files.json"
"$BACKGROUND_RUN" update --root "$REPO" --id "$id" --status ready --result "$WORK/result.md" --files "$WORK/files.json" --write >/dev/null
printf 'drift\n' > "$REPO/src/a.txt"
out="$(run_status)"
assert_jq "$out" '(.readiness.blockers | index("working_tree_dirty")) and (.readiness.blockers | index("background_handles_stale")) and .background.stale == 1' "status_background_stale"

reset_repo
mkdir -p "$WORK/bin"
cat > "$WORK/bin/curl" <<'EOF'
#!/usr/bin/env bash
printf called >> "$KIMIFLOW_FAKE_CURL_MARKER"
exit 9
EOF
chmod +x "$WORK/bin/curl"
export KIMIFLOW_FAKE_CURL_MARKER="$WORK/curl-called"
PATH="$WORK/bin:$PATH" KIMIFLOW_HOST=codex "$SCRIPT" status --root "$REPO" --run .kimiflow/demo >/dev/null
if [ -e "$KIMIFLOW_FAKE_CURL_MARKER" ]; then fail "status_no_network_probe"; else pass "status_no_network_probe"; fi

reset_repo
cat > "$REPO/.kimiflow/project/VAULT-PROVIDER.json" <<'EOF'
{"schema_version":1,"available":true,"vault_path":"https://127.0.0.1:27124","note":"capabilities tools words only"}
EOF
out="$(run_status)"
assert_jq "$out" '.provider.mcp_ready == false and (.readiness.warnings | index("mcp_not_direct_ready"))' "mcp_false_positive_not_ready"
cat > "$REPO/.kimiflow/project/VAULT-PROVIDER.json" <<'EOF'
{"schema_version":1,"available":true,"auth":{"authenticated":false,"status":"connected_local_only"},"capabilities":{"direct_search":true,"mcp_direct_write":true}}
EOF
out="$(run_status)"
assert_jq "$out" '.provider.mcp_ready == false and .provider.direct_search_ready == false and .provider.direct_write_ready == false and (.readiness.warnings | index("mcp_not_direct_ready"))' "mcp_requires_authenticated_tools"
mkdir -p "$WORK/fake-hooks"
cp "$SCRIPT" "$WORK/fake-hooks/agentic-readiness.sh"
out="$(KIMIFLOW_HOST=codex "$WORK/fake-hooks/agentic-readiness.sh" status --root "$REPO" --run .kimiflow/demo)"
assert_jq "$out" '.hooks.ready == false and (.readiness.blockers | index("required_helpers_missing"))' "capability_detection_is_structural"

reset_repo
printf 'OBSIDIAN_API_KEY=super-secret-token\n/Users/example/private/path\nraw prompt: do the hidden thing\n' >> "$REPO/.kimiflow/demo/PLAN.md"
out="$(run_status)"
assert_not_contains "$out" "super-secret-token" "status_output_privacy_token"
assert_not_contains "$out" "/Users/example" "status_output_privacy_home_path"
packet="$(HOME=/Users/example KIMIFLOW_HOST=codex "$SCRIPT" packet --root "$REPO" --run .kimiflow/demo --kind handoff --write)"
packet_path="$(printf '%s\n' "$packet" | jq -r '.path')"
packet_body="$(cat "$REPO/$packet_path")"
assert_not_contains "$packet_body" "super-secret-token" "packet_output_privacy_token"
assert_not_contains "$packet_body" "/Users/example" "packet_output_privacy_home_path"
assert_not_contains "$packet_body" "do the hidden thing" "packet_output_privacy_raw_prompt_text"

gate="$(KIMIFLOW_HOST=codex "$SCRIPT" gate --root "$REPO" --run .kimiflow/demo --min-level governed)"
assert_contains "$gate" $'AGENTIC_READINESS_GATE\tOPEN' "gate_opens_when_ready"
if [ -s "$REPO/.kimiflow/demo/AGENTIC-AUDIT.jsonl" ] \
  && jq -e 'select(.action == "packet" or .action == "gate") | .level and .blockers and .warnings' "$REPO/.kimiflow/demo/AGENTIC-AUDIT.jsonl" >/dev/null 2>&1 \
  && ! grep -q "super-secret-token" "$REPO/.kimiflow/demo/AGENTIC-AUDIT.jsonl"; then
  pass "audit_trail_records_gate_and_packet"
else
  fail "audit_trail_records_gate_and_packet"
fi

reset_repo
ln -s "$WORK/outside-secret.txt" "$REPO/.kimiflow/demo/AGENTIC-AUDIT.jsonl"
if KIMIFLOW_HOST=codex "$SCRIPT" packet --root "$REPO" --run .kimiflow/demo --kind review --write >/dev/null 2>&1; then
  fail "packet_requires_audit_trail"
else
  pass "packet_requires_audit_trail"
fi

# A connected, authenticated Obsidian/Vault MCP in this session (no network) supersedes
# a stale or capability-less per-repo manifest, so the false "not direct ready" warning clears.
reset_repo
cat > "$REPO/.kimiflow/project/VAULT-PROVIDER.json" <<'EOF'
{"schema_version":1,"available":true,"auth":{"authenticated":false,"status":"connected_local_only"},"capabilities":{"direct_search":false,"mcp_direct_write":false}}
EOF
out="$(KIMIFLOW_OBSIDIAN_MCP_AVAILABLE=1 KIMIFLOW_HOST=codex "$SCRIPT" status --root "$REPO" --run .kimiflow/demo)"
assert_jq "$out" '.provider.mcp_ready == true and .provider.direct_search_ready == true and .provider.direct_write_ready == true and (.readiness.warnings | index("mcp_not_direct_ready") | not)' "mcp_session_signal_clears_warning"
out="$(run_status)"
assert_jq "$out" '.provider.mcp_ready == false and (.readiness.warnings | index("mcp_not_direct_ready"))' "mcp_no_signal_still_not_ready"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
