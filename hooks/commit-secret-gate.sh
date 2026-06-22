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
# The secret patterns are a MINIMUM deny-list (see reference.md → "Commit hygiene");
# false positives on filenames that merely contain secret-words are possible.
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

flow_root() { # $1 = candidate cwd; echo the git root (cwd first, hook cwd fallback)
  git -C "${1:-.}" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel 2>/dev/null || true
}

# ---- No jq: cannot parse/verify → FAIL CLOSED on git add/commit in kimiflow repos ----
if ! command -v jq >/dev/null 2>&1; then
  if printf '%s' "$input" | grep -qE 'git[^"]{0,80}(add|commit)'; then
    cwd="$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    root="$(flow_root "$cwd")"
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

root="$(flow_root "$cwd")"
[ -n "$root" ] || exit 0
[ -d "$root/.kimiflow" ] || exit 0   # scope: kimiflow repos only

# Block bulk add — kimiflow stages only explicitly named paths.
if git_sub add && printf '%s' "$cmd" | grep -qE '(\s-A\b|\s--all\b|\s\.(\s|$))'; then
  emit_deny "kimiflow commit-secret-gate: refusing 'git add -A/.' — stage only explicitly named paths (commit hygiene). Add the files you mean by name."
fi

# On a commit, scan the staged paths for secret-looking files.
if git_sub commit; then
  staged="$(git -C "$root" diff --cached --name-only 2>/dev/null || true)"
  [ -n "$staged" ] || exit 0
  secret_re='(^|/)[^/]*\.env(rc)?(\.|$)|\.(pem|key|p12|pfx|asc)$|(^|/)id_(rsa|dsa|ecdsa|ed25519)$|(^|/)\.(npmrc|pypirc)$|(^|[/._-])(secrets?|credentials?|api[._-]?keys?|access[._-]?tokens?|auth[._-]?tokens?)([/._-]|$)'
  hits="$(printf '%s\n' "$staged" | grep -iE "$secret_re" || true)"
  if [ -n "$hits" ]; then
    emit_deny "$(printf 'kimiflow commit-secret-gate: refusing commit — staged paths look like secrets:\n%s\n\nUnstage them (git restore --staged <path>) or add to .gitignore. False positive? Commit the specific safe files by name from outside a kimiflow run.' "$hits")"
  fi
fi

exit 0
