#!/usr/bin/env bash
# kimiflow — unit tests for commit-secret-gate.sh (the PreToolUse secret/bulk-add gate).
# Black-box: drives the REAL hook with crafted JSON payloads against a throwaway git
# repo that has a .kimiflow/ dir (so the gate is in scope). No framework.
# Isolation: a temp repo under mktemp — the real repo is never touched.
# Run: bash hooks/test-commit-secret-gate.sh   (requires jq, same as the hook)
set -u

HOOK="$(cd "$(dirname "$0")" && pwd)/commit-secret-gate.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — the hook's precise path (and this test) needs jq"; exit 0
fi

git init -q "$REPO"
mkdir -p "$REPO/.kimiflow"

# Build a PreToolUse payload for a command running in $1 (repo dir defaults to $REPO).
payload() { jq -nc --arg c "$1" --arg d "${2:-$REPO}" '{tool_input:{command:$c}, cwd:$d}'; }
run()     { payload "$1" "${2:-$REPO}" | "$HOOK"; }
payload_owner() { jq -nc --arg c "$1" --arg d "${2:-$REPO}" '{tool_input:{command:$c}, cwd:$d, session_id:"owner-session"}'; }
run_owner() { payload_owner "$1" "${2:-$REPO}" | KIMIFLOW_HOST=codex "$HOOK"; }

assert_deny()  { # $1=cmd $2=label [$3=repo]
  out="$(run "$1" "${3:-$REPO}")"
  if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then pass "$2"
  else fail "$2 (expected DENY, got: ${out:-<empty/allow>})"; fi
}
assert_allow() { # $1=cmd $2=label [$3=repo]
  out="$(run "$1" "${3:-$REPO}")"
  if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "$2 (expected ALLOW, got DENY: $out)"
  else pass "$2"; fi
}

clear_index() { git -C "$REPO" rm -r --cached --quiet . >/dev/null 2>&1 || true; }
stage()       { for p in "$@"; do mkdir -p "$REPO/$(dirname "$p")"; : > "$REPO/$p"; git -C "$REPO" add -f "$p" >/dev/null 2>&1; done; }

# --- HIGH: .env conventions (dotfile AND suffix style) must all be caught on commit ---
for f in .env .env.local .env.production foo/.env prod.env dev.env staging.env database.env local.env app.env .envrc; do
  clear_index; stage "$f"; assert_deny "git commit -m x" "env_caught:$f"
done

# --- env false positives: must NOT be flagged ---
for f in environment.js venv/activate src/env/index.js README.md; do
  clear_index; stage "$f"; assert_allow "git commit -m x" "env_safe:$f"
done

# --- other secret patterns ---
for f in server.pem private.key cert.p12 store.pfx backup.asc id_rsa .npmrc .pypirc my-secrets.txt config/credentials.yml api_key.json access_token.json; do
  clear_index; stage "$f"; assert_deny "git commit -m x" "secret_caught:$f"
done

# --- discriminator: a secret word as the TRAILING token is still caught, even hyphen-prefixed ---
for f in client-secret.txt prod-secret.json oauth-credentials.yml; do
  clear_index; stage "$f"; assert_deny "git commit -m x" "secret_trailing_caught:$f"
done

# --- false positive fix: a secret word MID-name (compound code filename, keyword then `-token`)
# must NOT be flagged. kimiflow's own hook files contain "secret"; so do many source files. ---
for f in hooks/commit-secret-gate.sh hooks/test-commit-secret-gate.sh lib/secret-manager.ts; do
  clear_index; stage "$f"; assert_allow "git commit -m x" "compound_name_safe:$f"
done

# --- intended boundary: bare `token` is NOT caught (would false-positive on tokenizer etc.) ---
clear_index; stage token.txt; assert_allow "git commit -m x" "bare_token_not_flagged(intended)"

# --- bulk add is blocked; named add is allowed ---
assert_deny  "git add -A"          "bulk_add_-A"
assert_deny  "git add ."           "bulk_add_dot"
assert_deny  "git add --all"       "bulk_add_--all"
assert_allow "git add safe.txt"    "named_add_allowed"
# whole-tree pathspecs that the standalone-token check missed (finding C2):
assert_deny  "git add ./"          "bulk_add_dotslash"
assert_deny  "git add :/"          "bulk_add_colonslash_top"
assert_deny  "git add ':(top)'"    "bulk_add_top_magic"
assert_deny  "git add -Av"         "bulk_add_-A_cluster"
assert_deny  "git add ./ && git commit -m wip" "bypass_add_dotslash_then_commit"
assert_allow "git add ./src/file.ts" "named_add_dotslash_path_allowed"

# --- malformed JSON: empty input is a no-op, but malformed git-like payloads fail closed ---
out="$(printf '' | "$HOOK")"
if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "empty_input_allowed"; else pass "empty_input_allowed"; fi
out="$(printf '{bad json git commit \"cwd\":\"%s\"' "$REPO" | "$HOOK")"
if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then pass "malformed_git_payload_denied"; else fail "malformed_git_payload_denied (expected DENY, got: ${out:-<empty/allow>})"; fi
out="$(printf '{bad json ls' | "$HOOK")"
if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "malformed_nongit_payload_allowed"; else pass "malformed_nongit_payload_allowed"; fi
MALFORMED_NOREPO="$WORK/malformed-norepo"; git init -q "$MALFORMED_NOREPO"
out="$(printf '{bad json git commit \"cwd\":\"%s\"' "$MALFORMED_NOREPO" | "$HOOK")"
if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "malformed_git_payload_out_of_scope_allowed"; else pass "malformed_git_payload_out_of_scope_allowed"; fi

# --- bulk-pattern scoping: a bare `.` pathspec in a DIFFERENT subcommand of the same compound
# command must NOT be misread as `git add .` (the bulk check is scoped to the add invocation's args) ---
assert_allow "git add safe.txt && git grep -n foo -- ."       "named_add_then_grep_dot_pathspec"
assert_allow "git add a.md b.json && git log --oneline -- ."   "named_add_then_log_dot_pathspec"
assert_deny  "git add foo ."                                   "named_then_dot_is_bulk"

# --- git_sub anchoring: a commit MESSAGE containing "add -A" is not misread as a bulk add ---
clear_index; stage README.md
assert_allow 'git commit -m "add -A to parser"' "anchor_commit_msg_not_bulkadd"

# --- combined `git add <secret> && git commit`: file not yet in index, still caught ---
clear_index
assert_deny  "git add prod.env && git commit -m wip"   "bypass_add_commit_suffix_env"
assert_deny  "git add -f .env && git commit -m wip"     "bypass_add_commit_dotenv_flag"
assert_deny  "git add a.txt server.pem && git commit -m wip" "bypass_add_commit_multi_target"
assert_deny  "git add prod.env; git commit -m wip"     "bypass_add_commit_semicolon"
assert_allow "git add safe.txt && git commit -m wip"    "bypass_add_commit_safe_allowed"

# --- HARDENING: `git commit -a/--all/-am` stages tracked working-tree mods AT COMMIT TIME
# (after this hook runs), so `diff --cached` alone misses them. With -a present the hook also
# scans tracked unstaged mods (`git diff --name-only`). Setup: a fresh kimiflow repo with a
# TRACKED file that is then MODIFIED but left unstaged. ---
AREPO="$WORK/arepo"; git init -q "$AREPO"; mkdir -p "$AREPO/.kimiflow"
git -C "$AREPO" config user.email t@t >/dev/null 2>&1; git -C "$AREPO" config user.name t >/dev/null 2>&1
seed_arepo() { # reset to base (.env + safe.txt committed clean), then modify the named files unstaged
  git -C "$AREPO" reset -q --hard >/dev/null 2>&1 || true
  printf 'A=1\n' > "$AREPO/.env"; printf 'ok\n' > "$AREPO/safe.txt"
  git -C "$AREPO" add -f .env safe.txt >/dev/null 2>&1
  git -C "$AREPO" commit -qm base >/dev/null 2>&1
  for p in "$@"; do printf 'changed\n' >> "$AREPO/$p"; done
}
seed_arepo .env;     assert_deny  "git commit -am wip"          "commit_a_modified_env_caught"          "$AREPO"
seed_arepo .env;     assert_deny  "git commit -a -m wip"        "commit_a_split_modified_env_caught"    "$AREPO"
seed_arepo .env;     assert_deny  "git commit --all -m wip"     "commit_all_modified_env_caught"        "$AREPO"
# bundled short flags where `a` is NOT first (verbose/quiet/etc. before -a) must STILL be caught —
# they all auto-stage tracked mods (regression guard for the a-not-first bypass) ---
seed_arepo .env;     assert_deny  "git commit -vam wip"         "commit_vam_modified_env_caught"        "$AREPO"
seed_arepo .env;     assert_deny  "git commit -qam wip"         "commit_qam_modified_env_caught"        "$AREPO"
seed_arepo .env;     assert_deny  "git commit -va -m wip"       "commit_va_modified_env_caught"         "$AREPO"
seed_arepo safe.txt; assert_allow "git commit -am wip"          "commit_a_only_safe_dirty_allowed"      "$AREPO"
# a quoted shell metachar in the -m message must NOT truncate the segment before -a (the command is
# unquoted BEFORE the ;&| split); same for a backslash-newline continuation (lines are joined first).
seed_arepo .env;     assert_deny  'git commit -m "hello; world" -a' "commit_quoted_semicolon_a_caught"     "$AREPO"
seed_arepo .env;     assert_deny  'git commit -m "a && b" -a'       "commit_quoted_amp_a_caught"            "$AREPO"
seed_arepo .env;     assert_deny  'git commit -m "a | b" -a'        "commit_quoted_pipe_a_caught"           "$AREPO"
seed_arepo .env;     assert_deny  'git commit -m "a; b" --all'      "commit_quoted_semicolon_all_caught"    "$AREPO"
seed_arepo .env;     assert_deny  $'git commit -m "x" \\\n -a'      "commit_newline_continuation_a_caught"  "$AREPO"
# false-positive guard: a plain commit must NOT scan unstaged mods (.env dirty+tracked, not staged)
seed_arepo .env;     assert_allow "git commit -m wip"           "plain_commit_ignores_unstaged_env"     "$AREPO"
# false-positive guard: a `-a` token INSIDE a quoted -m message must NOT trigger the unstaged scan
seed_arepo .env;     assert_allow 'git commit -m "drop -a flag"' "commit_msg_dash_a_not_triggered"      "$AREPO"
# `-ma` is `-m` with value "a" (a message), NOT `-a` — must not trigger the unstaged scan
seed_arepo .env;     assert_allow "git commit -ma"              "commit_dash_ma_is_message_not_all"     "$AREPO"
# `--allow-empty` must NOT be misread as `--all` (whole-word match) — plain commit, env not staged
seed_arepo .env;     assert_allow "git commit --allow-empty -m wip" "commit_allow_empty_not_all"        "$AREPO"
# value-taking shorts -u (untracked-files) / -S (gpg-sign) whose VALUE contains `a` must NOT be
# read as `-a` (git does not auto-stage for these) — over-block guard
seed_arepo .env;     assert_allow "git commit -uall -m wip"        "commit_u_value_all_not_dash_a"      "$AREPO"
seed_arepo .env;     assert_allow "git commit -Sabc123 -m wip"     "commit_S_keyid_a_not_dash_a"        "$AREPO"
# documented residuals (regex ≠ shell parser) — locked as KNOWN ALLOW so each gap is honest, not silent:
# (a) env/sudo prefix defeats the command-position anchor (gate-wide; see docs/commit-secret-gate.md)
seed_arepo .env;     assert_allow "env X=1 git commit -am wip"   "commit_env_prefix_known_gap"           "$AREPO"
seed_arepo .env;     assert_allow "sudo git commit -am wip"      "commit_sudo_prefix_known_gap"          "$AREPO"
# (b) an escaped quote inside the message desyncs the quote-strip
seed_arepo .env;     assert_allow 'git commit -m "a\"; x" -a'    "commit_escaped_quote_known_gap"        "$AREPO"
# (c) an explicit pathspec commit of a tracked secret is NOT covered (no shell-AST pathspec parsing)
seed_arepo .env;     assert_allow "git commit .env -m wip"      "pathspec_commit_known_gap(documented)" "$AREPO"

# The legacy parser boundaries stay unchanged outside an owner run. The active owner gets a
# deliberately narrower, mechanically parseable grammar instead of relying on those regex gaps.
mkdir -p "$AREPO/.kimiflow/session"
jq -n '{schema_version:1,status:"active",host:"codex",owner:{host:"codex",session_id:"owner-session"}}' > "$AREPO/.kimiflow/session/ACTIVE_RUN.json"
assert_owner_deny() {
  out="$(run_owner "$1" "$AREPO")"
  if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then pass "$2"; else fail "$2 (expected DENY, got: ${out:-<empty/allow>})"; fi
}
assert_owner_allow() {
  out="$(run_owner "$1" "$AREPO")"
  if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "$2 (expected ALLOW, got DENY: $out)"; else pass "$2"; fi
}
clear_index
stage safe.txt
assert_owner_allow "git commit --only -m wip -- safe.txt" "owner_canonical_named_commit_allowed"
assert_owner_deny "env X=1 git commit -am wip" "owner_env_prefix_denied"
assert_owner_deny "sudo git commit -am wip" "owner_sudo_prefix_denied"
assert_owner_deny 'git commit -m "a\"; x" -a' "owner_escaped_quote_denied"
assert_owner_deny "git commit .env -m wip" "owner_implicit_pathspec_denied"
assert_owner_deny "git commit -m wip" "owner_pathless_commit_denied"
assert_owner_deny "git commit --only -m wip -- ../safe.txt" "owner_traversal_path_denied"
assert_owner_deny 'git commit --only -m wip -- "$(printf .env)"' "owner_command_substitution_path_denied"
assert_owner_deny 'git commit --only -m wip -- *.env' "owner_unquoted_glob_path_denied"
assert_owner_deny 'git commit --only -m wip -- ~/.env' "owner_tilde_path_denied"
assert_owner_deny '/usr/bin/git commit --only -m wip -- .env' "owner_absolute_git_binary_denied"
assert_owner_deny "'/usr/bin/git' commit --only -m wip -- .env" "owner_single_quoted_git_binary_denied"
assert_owner_deny '"./bin/git" commit --only -m wip -- .env' "owner_double_quoted_git_binary_denied"
assert_owner_deny '\git commit --only -m wip -- .env' "owner_escaped_git_binary_denied"
assert_owner_deny "gi''t commit --only -m wip -- .env" "owner_concatenated_git_binary_denied"
assert_owner_deny $'git \\\n commit --only -m wip -- .env' "owner_line_continuation_git_denied"
assert_owner_deny $'g\\\nit commit --only -m wip -- .e\\\nnv' "owner_continuation_inside_tokens_denied"
mkdir -p "$AREPO/app/(auth)/[slug]"
: > "$AREPO/app/(auth)/[slug]/page.tsx"
git -C "$AREPO" add -f 'app/(auth)/[slug]/page.tsx' >/dev/null 2>&1
assert_owner_allow "git commit --only -m wip -- 'app/(auth)/[slug]/page.tsx'" "owner_quoted_framework_path_allowed"
payload_owner "git commit -m wip" "$AREPO" | jq '.session_id="foreign-session"' | KIMIFLOW_HOST=codex "$HOOK" > "$WORK/foreign-owner.out"
if grep -q 'active run owners must commit' "$WORK/foreign-owner.out"; then fail "foreign_session_not_newly_policed"; else pass "foreign_session_not_newly_policed"; fi
payload_owner "/usr/bin/git commit -m wip" "$AREPO" | jq '.session_id="foreign-session"' | KIMIFLOW_HOST=codex "$HOOK" > "$WORK/foreign-absolute-git.out"
if grep -q 'permissionDecision.*deny' "$WORK/foreign-absolute-git.out"; then fail "foreign_absolute_git_keeps_legacy_allow"; else pass "foreign_absolute_git_keeps_legacy_allow"; fi
payload_owner "git commit -m wip" "$AREPO" | KIMIFLOW_HOST=claude "$HOOK" > "$WORK/foreign-host.out"
if grep -q 'active run owners must commit' "$WORK/foreign-host.out"; then fail "foreign_host_not_owner"; else pass "foreign_host_not_owner"; fi
payload_owner "git commit -m wip" "$AREPO" | PLUGIN_ROOT=/installed/kimiflow "$HOOK" > "$WORK/inferred-codex-host.out"
if grep -q 'active run owners must commit' "$WORK/inferred-codex-host.out"; then pass "codex_host_inferred_from_plugin_root"; else fail "codex_host_inferred_from_plugin_root"; fi
git -C "$AREPO" add -f .env >/dev/null 2>&1
assert_owner_deny "git commit --only -m wip -- .env" "owner_named_secret_path_denied"
out="$(run_owner "\\git -C $AREPO commit --only -m wip -- .env" "$WORK")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny'; then pass "owner_escaped_git_dashc_outside_denied"; else fail "owner_escaped_git_dashc_outside_denied (expected DENY, got: ${out:-<empty/allow>})"; fi
out="$(run_owner "gi''t -C $AREPO commit --only -m wip -- .env" "$WORK")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny'; then pass "owner_concatenated_git_dashc_outside_denied"; else fail "owner_concatenated_git_dashc_outside_denied (expected DENY, got: ${out:-<empty/allow>})"; fi
out="$(run_owner $'git -C \\\n '$AREPO' commit --only -m wip -- .env' "$WORK")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny'; then pass "owner_line_continuation_dashc_outside_denied"; else fail "owner_line_continuation_dashc_outside_denied (expected DENY, got: ${out:-<empty/allow>})"; fi
assert_owner_allow "echo git commit later" "owner_non_git_echo_allowed"
assert_owner_allow "printf git commit" "owner_non_git_printf_allowed"
assert_owner_allow "command -v git commit" "owner_command_lookup_allowed"
assert_owner_deny "sh -c 'git commit --only -m wip -- .env'" "owner_shell_wrapper_denied"
assert_owner_deny "eval 'git commit --only -m wip -- .env'" "owner_eval_wrapper_denied"
assert_owner_deny "sh -c 'sh -c \"git commit --only -m wip -- .env\"'" "owner_nested_shell_wrapper_denied"
assert_owner_deny "sh -lc 'git commit --only -m wip -- .env'" "owner_shell_lc_wrapper_denied"
assert_owner_deny "env -S 'git commit --only -m wip -- .env'" "owner_env_split_wrapper_denied"
assert_owner_deny "exec -a fake-git git commit --only -m wip -- .env" "owner_exec_argv0_wrapper_denied"
out="$(run_owner "sh -c 'git -C $AREPO commit --only -m wip -- .env'" "$WORK")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny'; then pass "owner_shell_wrapper_dashc_outside_denied"; else fail "owner_shell_wrapper_dashc_outside_denied (expected DENY, got: ${out:-<empty/allow>})"; fi
out="$(run_owner $'true\ngit -C '$AREPO' commit --only -m wip -- .env' "$WORK")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny'; then pass "owner_newline_segment_outside_denied"; else fail "owner_newline_segment_outside_denied (expected DENY, got: ${out:-<empty/allow>})"; fi
two_backslash_command=$'true\\\\\n'"git -C $AREPO commit --only -m wip -- .env"
out="$(run_owner "$two_backslash_command" "$WORK")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny'; then pass "owner_even_backslashes_keep_newline_denied"; else fail "owner_even_backslashes_keep_newline_denied (expected DENY, got: ${out:-<empty/allow>})"; fi
deep_shell_command="$(python3 - "$AREPO" <<'PY'
import shlex
import sys

command = f"git -C {shlex.quote(sys.argv[1])} commit --only -m wip -- .env"
for _ in range(5):
    command = f"sh -c {shlex.quote(command)}"
print(command)
PY
)"
out="$(run_owner "$deep_shell_command" "$WORK")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny'; then pass "owner_deep_shell_wrapper_denied"; else fail "owner_deep_shell_wrapper_denied (expected DENY, got: ${out:-<empty/allow>})"; fi
deep_eval_command="$(python3 - "$AREPO" <<'PY'
import shlex
import sys

commit = f"git -C {shlex.quote(sys.argv[1])} commit --only -m wip -- .env"
print(("eval " * 1100) + commit)
PY
)"
out="$(run_owner "$deep_eval_command" "$WORK" 2>"$WORK/deep-eval.err")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny' && [ ! -s "$WORK/deep-eval.err" ]; then
  pass "owner_deep_eval_wrapper_denied_without_traceback"
else
  fail "owner_deep_eval_wrapper_denied_without_traceback (expected quiet DENY, got: ${out:-<empty/allow>})"
fi
out="$(run_owner "git commit -m noop || git -C $AREPO commit --only -m wip -- .env" "$WORK")"
if printf '%s' "$out" | grep -q 'permissionDecision.*deny'; then pass "owner_later_compound_root_denied"; else fail "owner_later_compound_root_denied (expected DENY, got: ${out:-<empty/allow>})"; fi

# whitespace normalization: a literal TAB between argv tokens must NOT defeat detection (tabs are
# normalized to spaces before parsing) — covers both the -a working-tree scan and the git_sub-guarded
# staged scan. (Without normalization, a tab after `commit` makes git_sub NO-MATCH → branch skipped.)
seed_arepo .env;         assert_deny  $'git commit\t-am wip'      "tab_after_commit_a_caught"             "$AREPO"
seed_arepo .env;         assert_deny  $'git commit\t--all -m wip' "tab_before_all_caught"                 "$AREPO"
seed_arepo .env;         assert_deny  $'git\tcommit -am wip'      "tab_after_git_a_caught"                "$AREPO"
clear_index; stage .env; assert_deny  $'git commit\t-m wip'      "tab_after_commit_staged_env_caught"

# --- scope: a repo WITHOUT .kimiflow/ is never policed (even with a staged secret) ---
NOREPO="$WORK/norepo"; git init -q "$NOREPO"; : > "$NOREPO/.env"; git -C "$NOREPO" add -f .env >/dev/null 2>&1
assert_allow "git commit -m x" "out_of_scope_repo_allowed" "$NOREPO"

# --- BYPASS FIX: `git -C <target>` must scope the gate to <target>, not the tool cwd. These run from
# an OUTSIDE non-kimiflow dir with the PROCESS cwd = the JSON cwd (run_at), so the git_root no-`-C`
# fallback (commit-secret-gate.sh:35) can't mask a broken fix. ---
OUTSIDE="$WORK/outside"; mkdir -p "$OUTSIDE"                       # plain dir, NOT a git/kimiflow repo
TREPO="$WORK/trepo";   git init -q "$TREPO";   mkdir -p "$TREPO/.kimiflow";   git -C "$TREPO" config user.email t@t; git -C "$TREPO" config user.name t
TREPO2="$WORK/trepo2"; git init -q "$TREPO2";  mkdir -p "$TREPO2/.kimiflow";  git -C "$TREPO2" config user.email t@t; git -C "$TREPO2" config user.name t
SAFE="$WORK/safe";     git init -q "$SAFE";    mkdir -p "$SAFE/.kimiflow"

run_at()          { ( cd "$2" 2>/dev/null && payload "$1" "$2" | "$HOOK" ); }   # PROCESS cwd = json cwd
assert_deny_at()  { out="$(run_at "$1" "$3")"; if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then pass "$2"; else fail "$2 (expected DENY, got: ${out:-<empty/allow>})"; fi; }
assert_allow_at() { out="$(run_at "$1" "$3")"; if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "$2 (expected ALLOW, got DENY: $out)"; else pass "$2"; fi; }

# guard: OUTSIDE must NOT sit under a kimiflow repo, else the fallback could mask a broken fix (unsound test)
g="$OUTSIDE"; gk=""; while [ -n "$g" ] && [ "$g" != "/" ]; do [ -d "$g/.kimiflow" ] && gk="$g"; g="$(dirname "$g")"; done
if [ -z "$gk" ]; then pass "gitc_outside_guard_no_kimiflow"; else fail "gitc_outside_guard ($gk has .kimiflow — test unsound)"; fi

: > "$TREPO/prod.env"; git -C "$TREPO" add -f prod.env >/dev/null 2>&1                    # secret staged in target
assert_deny_at  "git -C $TREPO commit -m x"          "gitc_commit_outside"                "$OUTSIDE"   # AC-1
assert_deny_at  "git -C $TREPO commit -C HEAD -m x"  "gitc_reuse_message_discriminator"   "$OUTSIDE"   # AC-3 (reuse -C ≠ chdir)
assert_deny_at  "git -C ../$(basename "$TREPO") commit -m x" "gitc_relative_C"            "$OUTSIDE"   # AC-8 (relative/cumulative -C)
: > "$SAFE/README.md"; git -C "$SAFE" add -f README.md >/dev/null 2>&1                    # only a safe file staged
assert_allow_at "git -C $SAFE commit -m x"           "gitc_safe_outside_allows"           "$OUTSIDE"   # AC-4 (no false positive)
printf v1 > "$TREPO2/tracked.key"; git -C "$TREPO2" add -f tracked.key >/dev/null 2>&1; git -C "$TREPO2" commit -q -m base
printf v2 > "$TREPO2/tracked.key"                                                         # tracked secret now modified (unstaged)
assert_deny_at  "git -C $TREPO2 commit -am x"        "gitc_commit_am_outside"             "$OUTSIDE"   # AC-2 (-a scans TARGET work tree)

# ============================================================================
# No-jq path: the hook FAILS CLOSED without jq, and the git add/commit detection
# must be robust against quotes between `git` and the subcommand. Drive the REAL
# hook under a PATH that OMITS jq (symlink only the tools its no-jq branch needs).
# The test itself keeps jq (to build payloads) — only the HOOK sees no jq.
# Tool paths are resolved with `command -v` INSIDE this script (alias-free in
# non-interactive bash), so a user's interactive `grep` alias can't poison them.
# ============================================================================
REALBASH="$(command -v bash)"
NOJQ="$WORK/nojq-bin"; mkdir -p "$NOJQ"
for t in bash cat grep sed head git tr; do
  if [ "$t" = "git" ] && [ -x /usr/bin/git ]; then
    s="/usr/bin/git"
  else
    s="$(command -v "$t")"
  fi
  [ -n "$s" ] && ln -s "$s" "$NOJQ/$t"
done
PLAIN="$WORK/plain"; git init -q "$PLAIN"   # git repo WITHOUT .kimiflow/

deny_nojq()  { # $1=cmd $2=label [$3=repo]
  out="$(payload "$1" "${3:-$REPO}" | PATH="$NOJQ" "$REALBASH" "$HOOK" 2>/dev/null)"
  if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then pass "$2"
  else fail "$2 (expected DENY, got: ${out:-<empty/allow>})"; fi
}
allow_nojq() { # $1=cmd $2=label [$3=repo]
  out="$(payload "$1" "${3:-$REPO}" | PATH="$NOJQ" "$REALBASH" "$HOOK" 2>/dev/null)"
  if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "$2 (expected ALLOW, got DENY: $out)"
  else pass "$2"; fi
}

deny_nojq  "git commit -m x"                     "nojq_commit_denied"                 # AC-A1
deny_nojq  'git -C "/x" commit -m x'             "nojq_dashC_commit_denied"           # AC-A2
deny_nojq  'git -c user.name="a b" commit -m x'  "nojq_dashc_commit_denied"           # AC-A3
deny_nojq  "git add prod.env && git commit -m x" "nojq_add_commit_denied"             # AC-A4
allow_nojq "ls -la"                              "nojq_nongit_allowed"                # AC-A5
allow_nojq "git commit -m x"                     "nojq_no_kimiflow_allowed" "$PLAIN"  # AC-A6

# --- documented intentional over-block: the blunt no-jq fallback greps the raw payload, so a
# benign command that merely MENTIONS git add/commit is over-blocked. This is deliberate (safe
# failure for a fail-closed gate; install jq for the precise path). These cases LOCK that
# contract — see commit-secret-gate.sh no-jq comment + docs/commit-secret-gate.md. ---
deny_nojq  'echo "git commit later"'             "nojq_benign_git_mention_overblocked(intended)"  # AC-1
allow_nojq 'echo "deploy later"'                 "nojq_nongit_phrase_allowed"                      # AC-1

# no-jq path must ALSO honor `git -C <target>` from outside (fail-closed): process cwd = OUTSIDE
deny_nojq_at() { out="$( ( cd "$3" 2>/dev/null && payload "$1" "$3" | PATH="$NOJQ" "$REALBASH" "$HOOK" ) 2>/dev/null)"; if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then pass "$2"; else fail "$2 (expected DENY, got: ${out:-<empty/allow>})"; fi; }
deny_nojq_at "git -C $TREPO commit -m x"         "nojq_gitc_outside_denied"           "$OUTSIDE"   # AC-6
deny_nojq_at "git -C $TREPO commit -C HEAD -m x" "nojq_gitc_reuse_outside_denied"     "$OUTSIDE"   # AC-6 (reuse -C must not poison the real -C)

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
