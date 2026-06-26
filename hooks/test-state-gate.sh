#!/usr/bin/env bash
# kimiflow — unit tests for state-gate.sh (the PreToolUse STATE-persistence gate).
# Black-box: drives the REAL hook with crafted PreToolUse JSON against a throwaway repo
# with a .kimiflow/ dir. The review-gate resolver call is DENIED unless that run's
# .kimiflow/<slug>/STATE.md exists and is non-empty. No framework.
# Isolation: a temp repo under mktemp — the real repo is never touched.
# Run: bash hooks/test-state-gate.sh
set -u

HOOK="$(cd "$(dirname "$0")" && pwd)/state-gate.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — this test builds payloads with jq"; exit 0
fi

git init -q "$REPO"
mkdir -p "$REPO/.kimiflow"

payload() { jq -nc --arg c "$1" --arg d "${2:-$REPO}" '{tool_input:{command:$c}, cwd:$d}'; }
run()     { payload "$1" "${2:-$REPO}" | "$HOOK"; }

assert_deny()  { out="$(run "$1" "${3:-$REPO}")"; if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then pass "$2"; else fail "$2 (expected DENY, got: ${out:-<allow>})"; fi; }
assert_allow() { out="$(run "$1" "${3:-$REPO}")"; if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "$2 (expected ALLOW, got DENY: $out)"; else pass "$2"; fi; }

G='hooks/resolve-review-gate.sh'   # the resolver path as it appears in the gate command

# --- the core contract: gate call needs the run's STATE.md ---
mkdir -p "$REPO/.kimiflow/withstate/findings"; printf 'run state\n' > "$REPO/.kimiflow/withstate/STATE.md"
assert_allow "$G .kimiflow/withstate/findings --round 1 --expect A,B"  "with_state_allowed"

mkdir -p "$REPO/.kimiflow/nostate/findings"
assert_deny  "$G .kimiflow/nostate/findings --round 1 --expect A,B"    "no_state_denied"

mkdir -p "$REPO/.kimiflow/emptystate/findings"; : > "$REPO/.kimiflow/emptystate/STATE.md"
assert_deny  "$G .kimiflow/emptystate/findings --round 1 --expect A,B" "empty_state_denied"

# realistic ${CLAUDE_PLUGIN_ROOT}-prefixed invocation, no STATE → deny
assert_deny  '"${CLAUDE_PLUGIN_ROOT:-x}/hooks/resolve-review-gate.sh" .kimiflow/nostate/findings --round 2 --expect A,B' "plugin_root_prefixed_no_state_denied"

# --- no-op for anything that is not a review-gate resolver call ---
assert_allow "ls -la .kimiflow/nostate/findings"                       "non_resolver_command_allowed"
assert_allow "git commit -m x"                                         "plain_commit_allowed"

# --- resolver call on a non-.kimiflow findings path → allow (tests/other uses not policed) ---
mkdir -p "$WORK/tmp/findings"
assert_allow "$G $WORK/tmp/findings --round 1 --expect A,B"            "non_kimiflow_findings_allowed"

# --- scope: a repo WITHOUT .kimiflow/ is never policed ---
PLAIN="$WORK/plain"; git init -q "$PLAIN"
assert_allow "$G .kimiflow/nostate/findings --round 1 --expect A,B"    "out_of_scope_repo_allowed" "$PLAIN"

# ============================================================================
# No-jq HOOK path: the hook needs no jq (path token + file check). Drive the REAL
# hook under a PATH that OMITS jq; deny/allow must still hold. The test keeps jq
# (to build payloads) — only the HOOK sees no jq.
# ============================================================================
REALBASH="$(command -v bash)"
NOJQ="$WORK/nojq-bin"; mkdir -p "$NOJQ"
GIT_BIN="/usr/bin/git"
[ -x "$GIT_BIN" ] || GIT_BIN="$(command -v git)"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$GIT_BIN" > "$NOJQ/git"; chmod +x "$NOJQ/git"
ln -s "$REALBASH" "$NOJQ/bash"
for t in cat grep sed head dirname; do s="$(command -v "$t")"; [ -n "$s" ] && ln -s "$s" "$NOJQ/$t"; done
deny_nojq()  { out="$(payload "$1" "${3:-$REPO}" | PATH="$NOJQ" "$REALBASH" "$HOOK" 2>/dev/null)"; if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then pass "$2"; else fail "$2 (expected DENY, got: ${out:-<allow>})"; fi; }
allow_nojq() { out="$(payload "$1" "${3:-$REPO}" | PATH="$NOJQ" "$REALBASH" "$HOOK" 2>/dev/null)"; if printf '%s' "$out" | grep -q '"permissionDecision":"deny"'; then fail "$2 (expected ALLOW, got DENY: $out)"; else pass "$2"; fi; }

deny_nojq  "$G .kimiflow/nostate/findings --round 1 --expect A,B"      "nojq_no_state_denied"
allow_nojq "$G .kimiflow/withstate/findings --round 1 --expect A,B"    "nojq_with_state_allowed"
allow_nojq "ls -la"                                                    "nojq_non_resolver_allowed"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
