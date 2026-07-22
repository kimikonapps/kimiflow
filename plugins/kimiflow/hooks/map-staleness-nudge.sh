#!/usr/bin/env bash
# kimiflow — Stop-hook nudge when the local project map is stale. Non-blocking, USER-visible via
# `systemMessage`, rate-limited to once per UTC day so a clean map pays the sha256 sweep at most
# once per window. Fires on ANY Stop in a repo that has `.kimiflow/project/INDEX.json` (so it also
# catches non-kimiflow edits). Never blocks; exits 0 on every path.
# R2 invariant target: map-staleness-nudge.sh
set -u

# Resolve the helper path absolutely BEFORE any cd, so the later status call is cwd-independent.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
PMS="${KIMIFLOW_PLUGIN_ROOT:+$KIMIFLOW_PLUGIN_ROOT/hooks}"
PMS="${PMS:-$SCRIPT_DIR}/project-map-status.sh"

input="$(cat 2>/dev/null || true)"

# No jq → exit 0 silently (jq is required for both the input parse and the status helper).
command -v jq >/dev/null 2>&1 || exit 0

# Loop-break: a Stop that is itself a hook continuation must never re-fire.
active="$(printf '%s' "$input" | jq -r '.stop_hook_active // .hook_input.stop_hook_active // false' 2>/dev/null || true)"
[ "$active" = "true" ] && exit 0

# Project dir: prefer the hook's reported cwd, else the current dir.
proj="$(printf '%s' "$input" | jq -r '.cwd // .hook_input.cwd // .working_directory // empty' 2>/dev/null || true)"
[ -n "$proj" ] && cd "$proj" 2>/dev/null || true

# No project map → nothing to nudge about.
[ -f ".kimiflow/project/INDEX.json" ] || exit 0

stamp=".kimiflow/.map-nudge-stamp"
today="$(date -u '+%Y-%m-%d')"

# Rate-limit FIRST (before the expensive status sweep): stamp already carries today's date → done.
if [ -f "$stamp" ] && [ "$(head -n 1 "$stamp" 2>/dev/null)" = "$today" ]; then
  exit 0
fi

out="$(bash "$PMS" status 2>/dev/null || true)"

# Stamp whenever status ran (even on a current map), so the sweep runs at most once per window.
mkdir -p ".kimiflow" 2>/dev/null || true
old_umask="$(umask)"
umask 077
if printf '%s\n' "$today" > "$stamp.tmp.$$" 2>/dev/null; then
  mv "$stamp.tmp.$$" "$stamp" 2>/dev/null || rm -f "$stamp.tmp.$$" 2>/dev/null
fi
umask "$old_umask"

pmline="$(printf '%s\n' "$out" | grep '^PROJECT_MAP' | head -n 1)"
stale="$(printf '%s' "$pmline" | awk -F'\t' '{for (i = 1; i <= NF; i++) if ($i ~ /^stale=/) {split($i, a, "="); print a[2]}}')"
pot="$(printf '%s' "$pmline" | awk -F'\t' '{for (i = 1; i <= NF; i++) if ($i ~ /^potentially_stale=/) {split($i, a, "="); print a[2]}}')"
stale="${stale:-0}"
pot="${pot:-0}"
n=$((stale + pot))

if [ "$n" -ge 1 ]; then
  msg="Kimiflow: Projekt-Map $n Sektion(en) veraltet — \`/kimiflow --project-map quick\` oder bring-current."
  ctx="Project map: $n section(s) need refresh."
  jq -nc --arg m "$msg" --arg c "$ctx" \
    '{systemMessage: $m, hookSpecificOutput: {hookEventName: "Stop", additionalContext: $c}}'
fi

exit 0
