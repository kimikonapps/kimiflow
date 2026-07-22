#!/usr/bin/env bash
# kimiflow — hard test-gate (opt-in, safe). Blocks finishing while the project's
# tests are red. NO-OP unless the project opts in via a LOCAL, untracked
# `.kimiflow/test-gate` (a file whose first line is the test command). A git-TRACKED
# (committed) marker is REFUSED — its first line is eval'd, so a committed marker
# from a cloned repo would be a drive-by. Installing kimiflow never gates unrelated work.
set -u

# The red-test Stop gate belongs to the same session as an active Kimiflow run.
# Other or owner-unknown sessions must always be able to finish a read-only turn.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
ACTIVE_RUN="${KIMIFLOW_PLUGIN_ROOT:+$KIMIFLOW_PLUGIN_ROOT/hooks}"
ACTIVE_RUN="${ACTIVE_RUN:-$SCRIPT_DIR}/active-run.sh"

input="$(cat 2>/dev/null || true)"

# Break the loop if this stop is itself a hook continuation (never re-block forever).
if command -v jq >/dev/null 2>&1; then
  active="$(printf '%s' "$input" | jq -r '.stop_hook_active // .hook_input.stop_hook_active // false' 2>/dev/null || true)"
  [ "$active" = "true" ] && exit 0
else
  # No jq: detect the continuation flag with a tolerant grep, so the loop-break still
  # works and we never re-block forever (jq is recommended — see the block branch below).
  printf '%s' "$input" | grep -qE '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && exit 0
fi

# Project dir: prefer the hook's reported cwd, else the current dir.
proj=""
if command -v jq >/dev/null 2>&1; then
  proj="$(printf '%s' "$input" | jq -r '.cwd // .tool_input.cwd // .working_directory // empty' 2>/dev/null || true)"
fi
[ -n "$proj" ] && cd "$proj" 2>/dev/null || true

marker=".kimiflow/test-gate"
# No opt-in marker → do nothing (allow stop).
[ -f "$marker" ] || exit 0

cmd="$(head -n 1 "$marker" 2>/dev/null || true)"
[ -n "$cmd" ] || exit 0

# Security: only run a LOCAL, untracked marker. A git-TRACKED (committed) `.kimiflow/test-gate`
# could be a drive-by from a cloned repo — its first line is eval'd. An untracked marker
# can only have been created locally (by you or by kimiflow); refuse to run a tracked one.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
   && git ls-files --error-unmatch "$marker" >/dev/null 2>&1; then
  printf 'kimiflow test-gate: refusing to run a git-tracked .kimiflow/test-gate (drive-by risk) — keep it local/untracked to enable.\n' >&2
   exit 0
fi

if command -v jq >/dev/null 2>&1; then
  relation="$(printf '%s' "$input" | "$ACTIVE_RUN" owner-check 2>/dev/null | jq -r '.relation // "unknown"' 2>/dev/null || true)"
  case "$relation" in
    other|unknown) exit 0 ;;
    owner|none) ;;
    *) exit 0 ;;
  esac
elif [ -f ".kimiflow/session/ACTIVE_RUN.json" ]; then
  # Without jq the hook cannot compare session identities safely. Preserve
  # no-jq gating only when no active run exists; active runs fail open for Stop.
  exit 0
fi

# Run the project's test command.
if out="$(eval "$cmd" 2>&1)"; then
  exit 0
fi

# Tests failed → block the stop and feed the tail of the output back.
tail_out="$(printf '%s' "$out" | tail -n 30)"
if command -v jq >/dev/null 2>&1; then
  printf '%s' "$tail_out" | jq -Rs '{decision:"block", reason:("kimiflow test-gate: tests are red — fix before finishing.\n\n" + .)}'
else
  printf 'kimiflow test-gate: jq not installed — blocking on red tests without the output tail; install jq for detail.\n' >&2
  printf '{"decision":"block","reason":"kimiflow test-gate: tests are red — fix before finishing (install jq for the failing output)."}'
fi
exit 0
