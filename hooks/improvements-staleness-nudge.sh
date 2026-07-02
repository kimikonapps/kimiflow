#!/usr/bin/env bash
# kimiflow — Stop-hook nudge when a Kimiflow run has just completed while the local workqueue still has open
# slices. Non-blocking, USER-visible via `systemMessage`, and it fires ONLY on a genuine signal: the number of
# `Status: done` runs has increased since the last check AND >=1 open slice exists in IMPROVEMENTS.md/FINDINGS.md.
# A missing stamp SEEDS the baseline without firing (the repo already has many done runs — never fire spuriously
# on first install). Rate-limited to at most once per UTC day. Never blocks; exits 0 on every path.
# R2 invariant target: improvements-staleness-nudge.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
ISH="${KIMIFLOW_PLUGIN_ROOT:+$KIMIFLOW_PLUGIN_ROOT/hooks}"
ISH="${ISH:-$SCRIPT_DIR}/improvements-status.sh"

input="$(cat 2>/dev/null || true)"

# No jq → exit 0 silently (needed for input parse and the helper's JSON).
command -v jq >/dev/null 2>&1 || exit 0

# Loop-break: a Stop that is itself a hook continuation must never re-fire.
active="$(printf '%s' "$input" | jq -r '.stop_hook_active // .hook_input.stop_hook_active // false' 2>/dev/null || true)"
[ "$active" = "true" ] && exit 0

# Project dir: prefer the hook's reported cwd, else the current dir.
proj="$(printf '%s' "$input" | jq -r '.cwd // .hook_input.cwd // .working_directory // empty' 2>/dev/null || true)"
[ -n "$proj" ] && cd "$proj" 2>/dev/null || true

# Need at least one workqueue file, else nothing to nudge about.
[ -f ".kimiflow/project/IMPROVEMENTS.md" ] || [ -f ".kimiflow/project/FINDINGS.md" ] || exit 0

today="$(date -u '+%Y-%m-%d' 2>/dev/null || printf '')"
[ -n "$today" ] || exit 0

# Current count of completed runs (run-level "Status: done", not the per-phase lines).
done_count=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if grep -Eq '^[[:space:]]*[-*]?[[:space:]]*Status:[[:space:]]*done\b' "$f" 2>/dev/null; then
    done_count=$((done_count + 1))
  fi
done < <(find .kimiflow -mindepth 2 -maxdepth 2 -name STATE.md 2>/dev/null)

stamp=".kimiflow/.improvements-nudge-stamp"

write_stamp() { # $1=count  $2=fired_date
  mkdir -p ".kimiflow" 2>/dev/null || return 0
  local old_umask; old_umask="$(umask)"; umask 077
  if printf '%s\n%s\n' "$1" "$2" > "$stamp.tmp.$$" 2>/dev/null; then
    mv -f "$stamp.tmp.$$" "$stamp" 2>/dev/null || rm -f "$stamp.tmp.$$" 2>/dev/null
  fi
  umask "$old_umask"
}

# Missing stamp → seed baseline WITHOUT firing (B#2: do not treat absent stamp as count 0).
if [ ! -f "$stamp" ]; then
  write_stamp "$done_count" ""
  exit 0
fi

prev_count="$(sed -n '1p' "$stamp" 2>/dev/null | tr -dc '0-9')"
fired_date="$(sed -n '2p' "$stamp" 2>/dev/null | tr -d '[:space:]')"
prev_count="${prev_count:-0}"

increased=0
[ "$done_count" -gt "$prev_count" ] 2>/dev/null && increased=1
already_fired_today=0
[ "$fired_date" = "$today" ] && already_fired_today=1

# Default: refresh the seen-count, keep the existing fired_date.
new_fired="$fired_date"

if [ "$increased" -eq 1 ] && [ "$already_fired_today" -eq 0 ]; then
  open_imp="$(bash "$ISH" list --queue improvements --json 2>/dev/null | jq -r '.count // 0' 2>/dev/null || printf '0')"
  open_fnd="$(bash "$ISH" list --queue findings --json 2>/dev/null | jq -r '.count // 0' 2>/dev/null || printf '0')"
  open_imp="${open_imp:-0}"; open_fnd="${open_fnd:-0}"
  open=$((open_imp + open_fnd))
  if [ "$open" -ge 1 ]; then
    new_fired="$today"
    write_stamp "$done_count" "$new_fired"
    msg="Kimiflow: Ein Run wurde fertig und es gibt $open offene Workqueue-Slice(s). Falls dieser Run eine umgesetzt hat: \`improvements-status.sh mark-done <id> --commit <sha> --write\` (sonst ignorieren)."
    ctx="A Kimiflow run completed with $open open workqueue slice(s); mark any closed slice done via improvements-status.sh mark-done."
    jq -nc --arg m "$msg" --arg c "$ctx" \
      '{systemMessage: $m, hookSpecificOutput: {hookEventName: "Stop", additionalContext: $c}}'
    exit 0
  fi
fi

# No fire: just refresh the seen-count (so a later genuine increase is measured from here).
write_stamp "$done_count" "$new_fired"
exit 0
