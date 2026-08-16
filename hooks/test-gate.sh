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

test_root="$(pwd -P)"
marker_root="$test_root"

if ! command -v jq >/dev/null 2>&1; then
  # Without jq the hook cannot prove that this Stop belongs to the active run owner.
  exit 0
fi

owner_status="$(printf '%s' "$input" | "$ACTIVE_RUN" owner-check 2>/dev/null || true)"
relation="$(printf '%s' "$owner_status" | jq -r '.relation // "unknown"' 2>/dev/null || true)"
[ "$relation" = "owner" ] || exit 0

resolved_root="$(printf '%s' "$owner_status" | jq -r '.root // empty' 2>/dev/null || true)"
[ -n "$resolved_root" ] && [ -d "$resolved_root" ] || exit 0
test_root="$(cd "$resolved_root" && pwd -P)"

# A Fleet run keeps the local opt-in marker in the primary checkout but executes
# the command in the resolved active worktree.
primary_root="$(git -C "$test_root" worktree list --porcelain 2>/dev/null | sed -n 's/^worktree //p' | head -n 1)"
if [ -n "$primary_root" ] && [ -d "$primary_root" ] && [ ! -f "$test_root/.kimiflow/test-gate" ]; then
  marker_root="$(cd "$primary_root" && pwd -P)"
fi
marker="$marker_root/.kimiflow/test-gate"
# No opt-in marker → do nothing (allow stop).
[ -f "$marker" ] || exit 0

cmd="$(head -n 1 "$marker" 2>/dev/null || true)"
[ -n "$cmd" ] || exit 0

# Security: only run a LOCAL, untracked marker. A git-TRACKED (committed) `.kimiflow/test-gate`
# could be a drive-by from a cloned repo — its first line is eval'd. An untracked marker
# can only have been created locally (by you or by kimiflow); refuse to run a tracked one.
if git -C "$marker_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
   && git -C "$marker_root" ls-files --error-unmatch .kimiflow/test-gate >/dev/null 2>&1; then
  printf 'kimiflow test-gate: refusing to run a git-tracked .kimiflow/test-gate (drive-by risk) — keep it local/untracked to enable.\n' >&2
   exit 0
fi

# Serialize expensive project checks. An interrupted Stop hook may leave its
# child build alive; the directory lock prevents a later Stop from starting a
# second build against the same DerivedData/output paths. A stale lock is
# intentionally explicit and recoverable instead of guessed away.
lock_dir="$marker_root/.kimiflow/test-gate.running"
if ! mkdir "$lock_dir" 2>/dev/null; then
  reason="kimiflow test-gate: another test-gate command is already running; wait for it to finish or remove $lock_dir after verifying that no test/build process remains."
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$reason" | jq -Rs '{decision:"block", reason:.}'
  else
    printf '{"decision":"block","reason":"kimiflow test-gate: another test-gate command is already running."}'
  fi
  exit 0
fi
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT HUP INT TERM

# Run the project's test command.
if out="$(cd "$test_root" && eval "$cmd" 2>&1)"; then
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
