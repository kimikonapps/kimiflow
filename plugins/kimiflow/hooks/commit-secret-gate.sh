#!/usr/bin/env bash
# kimiflow — commit-secret-gate (PreToolUse, Bash). Blocks a `git commit` whose staged paths
# (plus, for `-a`/`--all`, the tracked working-tree paths it would auto-stage) look like secrets,
# and a bulk `git add -A` / `git add .`. AUTO-ACTIVE only in kimiflow repos — a `.kimiflow/`
# directory at the git root — so installing kimiflow never polices unrelated repos. No-op for
# every non-git command and every repo without `.kimiflow/`. LIMITATION: an explicit pathspec
# commit (`git commit <path>`) is NOT covered — parsing a pathspec from a shell string needs an
# AST, not a regex (see docs/commit-secret-gate.md). This is path hygiene, not a secret
# scanner: pair it with a content scanner (gitleaks/trufflehog) for in-source secrets.
# During the active run owner's session, commits additionally fail closed unless they use
# Kimiflow's canonical `git commit --only -m <message> -- <named paths>` form.
#
# Requires `jq` (same dependency as test-gate.sh). Without jq the hook cannot parse
# the payload to verify staged files, so it FAILS CLOSED: it denies a git add/commit
# inside a kimiflow repo with an install hint, rather than silently letting secrets through.
#
# SCOPE: this is FILENAME/PATH hygiene, NOT secret-in-source detection — it matches
# secret-looking staged PATHS, never file CONTENTS (a key pasted into app.js passes).
# Pair it with a content scanner (gitleaks / trufflehog) for in-source secrets. The
# patterns are a MINIMUM deny-list (see docs/commit-secret-gate.md); false positives
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
# regex over a serialized string (see docs/commit-secret-gate.md).
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
# would otherwise skip git_sub → the whole branch). Join shell backslash-newline continuations before
# every detector; doing this only in the later `-a` branch would leave the owner grammar bypassable.
cmd="$(printf '%s' "$cmd" | tr '\t\v\f\r' ' ')"
cmd_raw="$cmd"
cmd="$(python3 - "$cmd" <<'PY'
import sys

command = sys.argv[1]
result = []
quote = None
index = 0
while index < len(command):
    char = command[index]
    if char == "\\" and quote != "'" and index + 1 < len(command):
        following = command[index + 1]
        if following == "\n":
            index += 2
            continue
        result.extend((char, following))
        index += 2
        continue
    if char in ("'", '"'):
        if quote is None:
            quote = char
        elif quote == char:
            quote = None
    result.append(char)
    index += 1
print("".join(result), end="")
PY
)"

# True when `git`'s SUBCOMMAND is $1 (anchored past optional `-C path` / `-c cfg` /
# flag globals) — so `git commit -m "...add -A..."` is NOT misread as a bulk add. The legacy path
# intentionally remains limited to bare `git`. Owner detection is broader and also recognizes quoted
# or unquoted binary paths, which the canonical owner grammar then refuses.
git_sub() { printf '%s' "$cmd" | grep -qE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +$1( |\$)"; }
git_commit_query() {
  python3 - "$cmd" "$1" <<'PY'
import os
import json
import re
import shlex
import sys

def tokenize(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    return list(lexer)

try:
    tokens = tokenize(sys.argv[1])
except ValueError:
    raise SystemExit(1)
mode = sys.argv[2]
if mode not in ("detect", "roots"):
    raise SystemExit(1)

def split_segments(value):
    segments = []
    current = []
    for token in value:
        if token in (";", "&", "&&", "|", "||", "\n"):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments

assignment = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
wrappers = {"env", "sudo", "command", "builtin", "exec"}

def unwrap_command(segment):
    index = 0
    while index < len(segment) and assignment.match(segment[index]):
        index += 1
    while index < len(segment) and os.path.basename(segment[index]) in wrappers:
        wrapper = os.path.basename(segment[index])
        index += 1
        if wrapper == "env":
            while index < len(segment):
                token = segment[index]
                if token in ("-S", "--split-string"):
                    return None, segment[index + 1] if index + 1 < len(segment) else ""
                if token.startswith("--split-string="):
                    return None, token.split("=", 1)[1]
                if assignment.match(token):
                    index += 1
                    continue
                if token.startswith("-"):
                    takes_value = token in ("-u", "--unset", "-C", "--chdir")
                    index += 2 if takes_value else 1
                    continue
                break
        elif wrapper == "sudo":
            while index < len(segment) and segment[index].startswith("-"):
                takes_value = segment[index] in ("-u", "-g", "-h", "-p", "-C", "-T", "-R", "-D")
                index += 2 if takes_value else 1
        elif wrapper == "command":
            if index < len(segment) and segment[index] in ("-v", "-V"):
                return None, None
            while index < len(segment) and segment[index].startswith("-"):
                index += 1
        elif wrapper == "exec":
            while index < len(segment) and segment[index].startswith("-"):
                takes_value = segment[index] in ("-a", "--argv0")
                index += 2 if takes_value else 1
        else:
            while index < len(segment) and segment[index].startswith("-"):
                index += 1
        while index < len(segment) and assignment.match(segment[index]):
            index += 1
    return index, None

def inspect_segment(segment):
    start, nested_wrapper = unwrap_command(segment)
    if nested_wrapper is not None:
        try:
            return [], [tokenize(nested_wrapper)]
        except ValueError:
            return [[]], []
    if start is None:
        return [], []
    if start >= len(segment):
        return [], []
    command = os.path.basename(segment[start])
    if command in ("sh", "bash", "zsh", "dash", "ksh"):
        command_flag = next((i for i in range(start + 1, len(segment))
                             if segment[i].startswith("-") and "c" in segment[i][1:]), None)
        if command_flag is None or command_flag + 1 >= len(segment):
            return [], []
        try:
            return [], [tokenize(segment[command_flag + 1])]
        except ValueError:
            return [[]], []
    if command == "eval":
        try:
            return [], [tokenize(" ".join(segment[start + 1:]))]
        except ValueError:
            return [[]], []
    if command != "git":
        return [], []
    index = start + 1
    chdirs = []
    while index < len(segment):
        token = segment[index]
        if token == "commit":
            return [chdirs], []
        if token in ("-C", "-c"):
            if index + 1 >= len(segment):
                raise SystemExit(1)
            if token == "-C":
                chdirs.append(segment[index + 1])
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    return [], []

def find_commits(value):
    results = []
    pending = [value]
    while pending:
        current = pending.pop()
        for segment in split_segments(current):
            commits, nested = inspect_segment(segment)
            results.extend(commits)
            pending.extend(nested)
    return results

results = find_commits(tokens)
if not results:
    raise SystemExit(1)
if mode == "roots":
    print(json.dumps(results, separators=(",", ":")))
raise SystemExit(0)
PY
}
git_commit_token() { git_commit_query detect >/dev/null; }
git_commit_roots() { git_commit_query roots; }

git_sub add || git_sub commit || git_commit_token || exit 0

session_id="$(printf '%s' "$input" | jq -r '.session_id // .tool_input.session_id // empty' 2>/dev/null || true)"
request_host="${KIMIFLOW_HOST:-}"
if [ -z "$request_host" ]; then
  if [ -n "${CODEX_THREAD_ID:-}" ] || [ -n "${PLUGIN_ROOT:-}" ]; then
    request_host="codex"
  else
    request_host="claude"
  fi
fi

# Resolve every possible commit independently. Multiple shell segments may target different repos;
# selecting only the first one lets a later `|| git -C <owner> commit` escape owner enforcement.
root=""
owner_run=0
commit_roots_json="$(git_commit_roots 2>/dev/null || true)"
if printf '%s' "$commit_roots_json" | jq -e 'type == "array" and length > 0 and all(.[]; type == "array")' >/dev/null 2>&1; then
  while IFS= read -r root_row; do
    [ -n "$root_row" ] || continue
    set --
    while IFS= read -r p; do
      [ -n "$p" ] && set -- "$@" -C "$p"
    done <<EOF
$(printf '%s' "$root_row" | jq -r '.[]')
EOF
    candidate_root="$(git_root "$cwd" "$@")"
    [ -n "$candidate_root" ] || continue
    [ -n "$root" ] || root="$candidate_root"
    active_file="$candidate_root/.kimiflow/session/ACTIVE_RUN.json"
    if [ "$owner_run" -eq 0 ] && [ -n "$session_id" ] && [ -f "$active_file" ] && [ ! -L "$active_file" ]; then
      active_status="$(jq -r '.status // empty' "$active_file" 2>/dev/null || true)"
      active_session="$(jq -r '.owner.session_id // empty' "$active_file" 2>/dev/null || true)"
      active_host="$(jq -r '.owner.host // .host // empty' "$active_file" 2>/dev/null || true)"
      if [ "$active_status" = "active" ] && [ "$active_session" = "$session_id" ] \
          && [ -n "$active_host" ] && [ "$active_host" = "$request_host" ]; then
        owner_run=1
        root="$candidate_root"
      fi
    fi
  done <<EOF
$(printf '%s' "$commit_roots_json" | jq -c '.[]')
EOF
else
  # Legacy add/commit scoping remains best-effort and unchanged outside the owner parser.
  set --
  while IFS= read -r p; do
    [ -n "$p" ] && set -- "$@" -C "$p"
  done <<EOF
$(printf '%s' "$cmd" \
  | grep -oE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +(add|commit)" \
  | sed -E 's/[[:space:]]+(add|commit)[[:space:]]*$//' \
  | grep -oE -- '-C +[^ ]+' \
  | sed -E 's/^-C +//' || true)
EOF
  root="$(git_root "$cwd" "$@")"
fi
[ -n "$root" ] || exit 0
[ -d "$root/.kimiflow" ] || exit 0   # scope: kimiflow repos only

owner_named_paths=""
if [ "$owner_run" -eq 1 ] && git_commit_token; then
  if [[ "$cmd_raw" == *$'\\\n'* ]]; then
    emit_deny "kimiflow commit-secret-gate: active run owner commits may not use shell line continuations; use one canonical git commit command with explicit named paths."
  fi
  owner_named_paths="$(python3 - "$cmd" <<'PY'
import shlex
import sys

try:
    tokens = shlex.split(sys.argv[1], posix=True)
    raw_tokens = shlex.split(sys.argv[1], posix=False)
except ValueError:
    raise SystemExit(1)
if not tokens or tokens[0] != "git" or raw_tokens[0] != "git" or len(raw_tokens) != len(tokens):
    raise SystemExit(1)
index = 1
while index < len(tokens) and tokens[index] == "-C":
    if index + 1 >= len(tokens):
        raise SystemExit(1)
    index += 2
if index >= len(tokens) or tokens[index] != "commit":
    raise SystemExit(1)
args = tokens[index + 1:]
try:
    delimiter = args.index("--")
except ValueError:
    raise SystemExit(1)
options, paths = args[:delimiter], args[delimiter + 1:]
raw_args = raw_tokens[index + 1:]
if delimiter >= len(raw_args) or raw_args[delimiter] != "--":
    raise SystemExit(1)
raw_paths = raw_args[delimiter + 1:]
if len(raw_paths) != len(paths):
    raise SystemExit(1)
if not paths or options.count("--only") != 1:
    raise SystemExit(1)
message_count = 0
i = 0
while i < len(options):
    token = options[i]
    if token == "--only":
        i += 1
    elif token in ("-m", "--message") and i + 1 < len(options):
        message_count += 1
        i += 2
    else:
        raise SystemExit(1)
if message_count != 1:
    raise SystemExit(1)
for path, raw_path in zip(paths, raw_paths):
    normalized = path[2:] if path.startswith("./") else path
    parts = normalized.split("/")
    backtick = chr(96)
    if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] == "'":
        shell_literal = True
    elif len(raw_path) >= 2 and raw_path[0] == raw_path[-1] == '"':
        shell_literal = not any(char in raw_path[1:-1] for char in ("$", backtick, "\\", "\r", "\n", "\0"))
    else:
        unsafe_unquoted = "\r\n\0$*?[]{};&|<>()\\'\"" + backtick
        shell_literal = not raw_path.startswith("~") and not any(char in raw_path for char in unsafe_unquoted)
    if (not normalized or normalized in (".", "..") or normalized.startswith(("/", ":"))
            or any(part in ("", ".", "..") for part in parts)
            or not shell_literal):
        raise SystemExit(1)
print("\n".join(paths))
PY
)" || emit_deny "kimiflow commit-secret-gate: active run owners must commit atomically with 'git commit --only -m <message> -- <explicit named paths>'. Wrappers, bulk flags, pathless commits, and implicit pathspec commits are refused."
fi

# Block bulk add — kimiflow stages only explicitly named paths. Scope the bulk-pattern check to the
# `git add` invocation's OWN args (the segment after `add`, bounded by ;&|), so a bare `.` pathspec
# in a DIFFERENT subcommand of the same compound command (e.g. `git add foo && git grep -- .`) is
# not misread as `git add .`.
if git_sub add; then
  add_args="$(printf '%s' "$cmd" \
    | grep -oE "(^|[;&|][[:space:]]*)git( +-[Cc] +[^ ]+| +-[^ ]+)* +add( +[^;&|]+)+" \
    | sed -E 's/.*[[:space:]]add[[:space:]]+//' || true)"
  # Strip surrounding quotes so a quoted whole-tree magic pathspec (e.g. `git add ':(top)'`) is
  # still seen. Safe: this branch reads only bulk flags/whole-tree pathspecs, never a real filename,
  # so removing quotes can't drop a path we needed. Deny bulk flags (-A/-Av/--all) AND whole-tree
  # pathspecs the old standalone-`.` check missed: `.` `./` `.\` `:/` `:(top…` (all stage the tree).
  add_args_clean="$(printf '%s' "$add_args" | tr -d "\"'")"
  if printf '%s' "$add_args_clean" | grep -qE '(^|[[:space:]])(-A[A-Za-z]*|--all|\.|\./|\.\\|:/|:\(top[,)])([[:space:]]|$)'; then
    emit_deny "kimiflow commit-secret-gate: refusing bulk 'git add' (-A/./:/ whole-tree) — stage only explicitly named paths (commit hygiene). Add the files you mean by name."
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
  # KNOWN RESIDUALS (regex ≠ shell parser — documented, see docs/commit-secret-gate.md):
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
  scan="$(printf '%s\n%s\n%s\n%s\n' "$staged" "$added_now" "$unstaged" "$owner_named_paths" | grep -vE '^[[:space:]]*$' || true)"
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
