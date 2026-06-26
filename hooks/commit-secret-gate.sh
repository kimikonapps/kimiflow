#!/usr/bin/env bash
# kimiflow — commit-secret-gate (PreToolUse, Bash). Blocks a `git commit` whose staged paths
# (plus, for `-a`/`--all`, the tracked working-tree paths it would auto-stage) look like secrets,
# and a bulk `git add -A` / `git add .`. AUTO-ACTIVE only in kimiflow repos — a `.kimiflow/`
# directory at the git root — so installing kimiflow never polices unrelated repos. No-op for
# every non-git command and every repo without `.kimiflow/`. LIMITATION: an explicit pathspec
# commit (`git commit <path>`) is NOT covered — parsing a pathspec from a shell string needs an
# AST, not a regex (see reference.md → "Commit hygiene"). This is path hygiene, not a secret
# scanner: pair it with a content scanner (gitleaks/trufflehog) for in-source secrets.
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

git_root() { # $1 = candidate cwd; $2.. = extra git global opts in command order (e.g. `-C <path>`).
  # Echo the git root, HONORING any `git -C <path>` from the command (git applies multiple -C
  # cumulatively, relative to $cwd). The bare process-cwd fallback fires ONLY when NO extra opts were
  # passed — a `-C` that was specified but is unresolvable must NOT silently mis-scope to the hook's cwd.
  c="${1:-.}"; shift || true
  if [ "$#" -gt 0 ]; then
    # honor the -C target; if it's unresolvable (e.g. a quoted/space path we mis-extracted), fall back
    # to the cwd ITSELF — never to the hook's own process cwd — preserving cwd-based detection without
    # mis-scoping elsewhere.
    git -C "$c" "$@" rev-parse --show-toplevel 2>/dev/null \
      || git -C "$c" rev-parse --show-toplevel 2>/dev/null || true
  else
    git -C "$c" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel 2>/dev/null || true
  fi
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
    nojq_deny() { emit_deny "kimiflow commit-secret-gate: jq is not installed — cannot verify staged files for secrets, so this git command is blocked (fail-closed). Install jq (brew install jq / apt-get install jq); jq is also required by kimiflow's test-gate."; }
    nojq_check() { r="$(git_root "$cwd" "$@")"; [ -n "$r" ] && [ -d "$r/.kimiflow" ] && nojq_deny; }
    # Block if the cwd OR any `git -C <path>` target is a kimiflow repo. Without jq we can't tell a global
    # `-C <path>` from a reuse-message `-C <commit>`, so we test each candidate INDEPENDENTLY (not git's
    # cumulative chain) — an unresolvable `-C HEAD` then can't poison a real `-C <kimiflow-repo>`. Raw,
    # best-effort; over-blocking is the safe failure. Heredoc-fed `while` (current shell), NOT a pipe.
    nojq_check                                  # cwd
    while IFS= read -r p; do
      [ -n "$p" ] && nojq_check -C "$p"         # each -C target on its own
    done <<EOF
$(printf '%s' "$input" | grep -oE -- '-C +[^ "]+' | sed -E 's/^-C +//')
EOF
  fi
  exit 0
fi

# ---- jq available: precise path ----
if [ -n "$input" ] && ! printf '%s' "$input" | jq -e . >/dev/null 2>&1; then
  if printf '%s' "$input" | grep -qE 'git.{0,200}(add|commit)'; then
    cwd="$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    malformed_deny() { emit_deny "kimiflow commit-secret-gate: malformed hook payload for a git add/commit command — refusing to proceed fail-closed."; }
    malformed_check() { r="$(git_root "$cwd" "$@")"; [ -n "$r" ] && [ -d "$r/.kimiflow" ] && malformed_deny; }
    malformed_check
    while IFS= read -r p; do
      [ -n "$p" ] && malformed_check -C "$p"
    done <<EOF
$(printf '%s' "$input" | grep -oE -- '-C +[^ "]+' | sed -E 's/^-C +//')
EOF
  fi
  exit 0
fi

cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // .tool_input.args.command // .command // .shell_command // .args.command // empty' 2>/dev/null || true)"
cwd="$(printf '%s' "$input" | jq -r '.cwd // .tool_input.cwd // .working_directory // empty' 2>/dev/null || true)"
[ -n "$cmd" ] || exit 0
# Normalize non-newline whitespace (TAB/VT/FF/CR) to spaces so a token separator that isn't a literal
# space can't defeat the space-anchored matchers below (e.g. `git<TAB>commit` / `git commit<TAB>--all`
# would otherwise skip git_sub → the whole branch). Newlines are left as line separators (grep is
# line-oriented; backslash-newline continuations are joined later, in the commit branch).
cmd="$(printf '%s' "$cmd" | tr '\t\v\f\r' ' ')"

# True when `git`'s SUBCOMMAND is $1 (anchored past optional `-C path` / `-c cfg` /
# flag globals) — so `git commit -m "...add -A..."` is NOT misread as a bulk add.
git_sub() { printf '%s' "$cmd" | grep -qE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +$1( |\$)"; }

git_sub add || git_sub commit || exit 0

# Honor `git -C <path>` GLOBAL options (those between `git` and its add/commit subcommand — NOT a
# `-C <commit>` that FOLLOWS `commit`, which is git's reuse-message) so `git -C <target> commit` scopes
# the gate to <target>, not the tool cwd. We collect them and let git resolve them cumulatively relative
# to $cwd, exactly as `git -C` does — no bespoke path math. The `while` is fed by a HEREDOC in the
# CURRENT shell; a `grep | while` pipe would run in a subshell, leaving $@ empty → silent no-op.
# Best-effort/unquoted like the rest of this gate (a space in a quoted -C path is a documented residual).
set --
while IFS= read -r p; do
  [ -n "$p" ] && set -- "$@" -C "$p"
done <<EOF
$(printf '%s' "$cmd" \
  | grep -oE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +(add|commit)" \
  | sed -E 's/[[:space:]]+(add|commit)[[:space:]]*$//' \
  | grep -oE -- '-C +[^ ]+' \
  | sed -E 's/^-C +//')
EOF
root="$(git_root "$cwd" "$@")"
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
  # `git commit -a/--all/-am…` stages tracked working-tree modifications AT COMMIT TIME — after
  # this PreToolUse hook runs — so the index scan alone misses them. When -a/--all is present,
  # also scan tracked-but-unstaged modifications (`git diff --name-only`). Detection is best-effort
  # over the unparsed command. CRITICAL ORDER: backslash-newline continuations are joined and
  # quoted spans removed FIRST, THEN the commit segment is isolated by the ;&| split — otherwise a
  # shell metachar HIDDEN in a quoted message (`-m "a; b" -a`) or behind a line continuation
  # (`-m "x" \⏎ -a`) would truncate the segment and drop the trailing -a. Safe to strip quotes here:
  # this branch reads only -a/--all FLAGS, never pathspec/filenames (pathspec is out of scope), so
  # removing quoted text can never drop a path we needed. The subcommand prefix is then stripped by
  # an anchored match (so the word "commit" inside a -m message is not taken for the subcommand).
  # The `-a` matcher fires when `a` appears in a SHORT-option cluster BEFORE a value-taking option
  # (m/c/C/F/S/u — incl. optional-arg `-S`gpg / `-u`untracked) — so `-am`/`-vam`/`-qam` are caught,
  # while `-ma` (a message), `-uall`, `-Sabc` are NOT; `--all` is a whole word (not `--allow-empty`).
  # KNOWN RESIDUALS (regex ≠ shell parser — documented, see reference.md → "Commit hygiene"):
  # a command-position-anchor evasion — `env X=y`/`sudo`, a path-prefixed `/usr/bin/git`, or a
  # `command`/`builtin`/`exec git` wrapper (all defeat the `git`-at-command-position anchor, gate-wide);
  # an escaped quote inside the message; a QUOTED `-C` path containing a space (`git -C "my repo"` —
  # `git -C <path>` IS honored, but only for unquoted/space-free paths); and an explicit pathspec
  # commit (`git commit <path>`) are NOT covered. (A global `git -C <path>` to another repo IS
  # honored — the gate resolves the target via git's own cumulative `-C`, not the tool cwd.)
  unstaged=""
  cmd_unq="$(printf '%s' "$cmd" \
    | awk '{ if (sub(/\\$/,"")) printf "%s ", $0; else print }' \
    | sed -E "s/\"[^\"]*\"//g; s/'[^']*'//g")"
  commit_args="$(printf '%s' "$cmd_unq" \
    | grep -oE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +commit( +[^;&|]+)*" \
    | sed -E 's/^[^a-zA-Z]*git( +-[Cc] +[^ ]+| +-[^ ]+)* +commit[[:space:]]*//' || true)"
  if printf '%s' "$commit_args" | grep -qE '(^|[[:space:]])(--all([[:space:]]|$)|-[^-mcCFSu[:space:]]*a)'; then
    unstaged="$(git -C "$root" diff --name-only 2>/dev/null || true)"
  fi
  scan="$(printf '%s\n%s\n%s\n' "$staged" "$added_now" "$unstaged" | grep -vE '^[[:space:]]*$' || true)"
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
