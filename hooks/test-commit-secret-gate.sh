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

# --- intended boundary: bare `token` is NOT caught (would false-positive on tokenizer etc.) ---
clear_index; stage token.txt; assert_allow "git commit -m x" "bare_token_not_flagged(intended)"

# --- bulk add is blocked; named add is allowed ---
assert_deny  "git add -A"          "bulk_add_-A"
assert_deny  "git add ."           "bulk_add_dot"
assert_deny  "git add --all"       "bulk_add_--all"
assert_allow "git add safe.txt"    "named_add_allowed"

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

# --- scope: a repo WITHOUT .kimiflow/ is never policed (even with a staged secret) ---
NOREPO="$WORK/norepo"; git init -q "$NOREPO"; : > "$NOREPO/.env"; git -C "$NOREPO" add -f .env >/dev/null 2>&1
assert_allow "git commit -m x" "out_of_scope_repo_allowed" "$NOREPO"

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
for t in cat grep sed head git tr; do s="$(command -v "$t")"; [ -n "$s" ] && ln -s "$s" "$NOJQ/$t"; done
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

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
