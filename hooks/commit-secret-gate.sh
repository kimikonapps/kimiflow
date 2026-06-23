#!/usr/bin/env bash
# kimiflow — commit-secret-gate (PreToolUse, Bash). Blocks a `git commit` that would
# stage a secret, or a bulk `git add -A` / `git add .`. AUTO-ACTIVE only in kimiflow
# repos — a `.kimiflow/` directory at the git root — so installing kimiflow never polices
# unrelated repos. No-op for every non-git command and every repo without `.kimiflow/`.
#
# Requires `jq` (same dependency as test-gate.sh). Without jq the hook cannot parse
# the payload to verify staged files, so it FAILS CLOSED: it denies a git add/commit
# inside a kimiflow repo with an install hint, rather than silently letting secrets through.
#
# SCOPE: this is FILENAME/PATH hygiene, NOT secret-in-source detection — it matches
# secret-looking staged PATHS, never file CONTENTS (a key pasted into app.js passes).
# Pair it with a content scanner (gitleaks / trufflehog) for in-source secrets. The
# patterns are a MINIMUM deny-list (see reference.md → "Commit hygiene"); false positives
# on filenames that merely contain secret-words are possible.
set -u

input="$(cat 2>/dev/null || true)"

emit_deny() { # $1 = reason; emits a valid PreToolUse deny with or without jq
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -cRs '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:.}}'
  else
    r="$(printf '%s' "$1" | tr '\n' ' ' | sed 's/\\/\\\\/g; s/"/\\"/g')"
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$r"
  fi
  exit 0
}

git_root() { # $1 = candidate cwd; echo the git root (cwd first, hook cwd fallback)
  git -C "${1:-.}" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel 2>/dev/null || true
}

# ---- No jq: cannot parse/verify → FAIL CLOSED on git add/commit in kimiflow repos ----
# This fallback is intentionally BLUNT: with no jq it can't even extract the command from
# the JSON, so it greps the raw payload for a git-add/commit token. It therefore OVER-BLOCKS
# benign commands that merely mention git (e.g. `echo "git commit later"`). That is deliberate
# — over-blocking is the safe failure for a fail-closed gate, and it is rare (jq is required;
# the deny message says to install it). The precise jq path below does NOT over-block. We do
# not sharpen this with more regex: reliably classifying a shell command needs an AST, not a
# regex over a serialized string (see reference.md → "Commit hygiene").
if ! command -v jq >/dev/null 2>&1; then
  if printf '%s' "$input" | grep -qE 'git.{0,200}(add|commit)'; then
    cwd="$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    root="$(git_root "$cwd")"
    [ -n "$root" ] && [ -d "$root/.kimiflow" ] && emit_deny "kimiflow commit-secret-gate: jq is not installed — cannot verify staged files for secrets, so this git command is blocked (fail-closed). Install jq (brew install jq / apt-get install jq); jq is also required by kimiflow's test-gate."
  fi
  exit 0
fi

# ---- jq available: precise path ----
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null || true)"
cwd="$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null || true)"
[ -n "$cmd" ] || exit 0

# True when `git`'s SUBCOMMAND is $1 (anchored past optional `-C path` / `-c cfg` /
# flag globals) — so `git commit -m "...add -A..."` is NOT misread as a bulk add.
git_sub() { printf '%s' "$cmd" | grep -qE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +$1( |\$)"; }

git_sub add || git_sub commit || exit 0

root="$(git_root "$cwd")"
[ -n "$root" ] || exit 0
[ -d "$root/.kimiflow" ] || exit 0   # scope: kimiflow repos only

# Block bulk add — kimiflow stages only explicitly named paths. Scope the bulk-pattern check to the
# `git add` invocation's OWN args (the segment after `add`, bounded by ;&|), so a bare `.` pathspec
# in a DIFFERENT subcommand of the same compound command (e.g. `git add foo && git grep -- .`) is
# not misread as `git add .`.
if git_sub add; then
  add_args="$(printf '%s' "$cmd" \
    | grep -oE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +add( +[^;&|]+)+" \
    | sed -E 's/.*[[:space:]]add[[:space:]]+//' || true)"
  if printf '%s' "$add_args" | grep -qE '(^|[[:space:]])(-A|--all|\.)([[:space:]]|$)'; then
    emit_deny "kimiflow commit-secret-gate: refusing 'git add -A/.' — stage only explicitly named paths (commit hygiene). Add the files you mean by name."
  fi
fi

# On a commit, scan the staged paths for secret-looking files. Also scan any paths
# added by a `git add` in the SAME compound command (e.g. `git add prod.env && git
# commit`): they are not in the index yet when this PreToolUse hook runs, so the
# index scan alone would miss them.
if git_sub commit; then
  staged="$(git -C "$root" diff --cached --name-only 2>/dev/null || true)"
  added_now=""
  if git_sub add; then
    added_now="$(printf '%s' "$cmd" \
      | grep -oE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +add( +[^;&|]+)+" \
      | sed -E 's/.*[[:space:]]add[[:space:]]+//' \
      | tr ' ' '\n' \
      | grep -vE '^(-|[[:space:]]*$)' || true)"
  fi
  scan="$(printf '%s\n%s\n' "$staged" "$added_now" | grep -vE '^[[:space:]]*$' || true)"
  [ -n "$scan" ] || exit 0
  # Keyword boundary: a secret-word is flagged as the LEADING or trailing token, but the
  # trailing side excludes '-' so a compound NAME like commit-secret-gate.sh / secret-manager.ts
  # (keyword mid-name, continues with '-...') is NOT flagged, while a trailing secret token like
  # client-secret.txt / aws-credentials.yml still is (leading '-' kept, trailing '.'/'/'/'_'/$).
  secret_re='(^|/)[^/]*\.env(rc)?(\.|$)|\.(pem|key|p12|pfx|asc)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)$|(^|/)\.(npmrc|pypirc)$|(^|[/._-])(secrets?|credentials?|api[._-]?keys?|access[._-]?tokens?|auth[._-]?tokens?)([/._]|$)'
  hits="$(printf '%s\n' "$scan" | grep -iE "$secret_re" || true)"
  if [ -n "$hits" ]; then
    emit_deny "$(printf 'kimiflow commit-secret-gate: refusing commit — staged paths look like secrets:\n%s\n\nUnstage them (git restore --staged <path>) or add to .gitignore. False positive? Commit the specific safe files by name from outside a kimiflow run.' "$hits")"
  fi
fi

exit 0
