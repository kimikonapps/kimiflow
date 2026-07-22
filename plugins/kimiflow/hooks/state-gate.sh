#!/usr/bin/env bash
# kimiflow — state-gate (PreToolUse, Bash). Makes run-state persistence ENFORCED, not a prose
# ask the orchestrator can rationalize past. When the orchestrator invokes the review-gate
# resolver
#     resolve-review-gate.sh <.kimiflow/<slug>/findings> ...
# this hook DENIES the call (fail-closed) unless that run's STATE.md
# (.kimiflow/<slug>/STATE.md) exists and is non-empty — so no gate verdict (→ no commit)
# can be produced without persisted run state. `resolve-review-gate.sh` itself is UNTOUCHED.
#
# AUTO-ACTIVE only in kimiflow repos (.kimiflow/ at the git root). A no-op for every command
# that is not a review-gate resolver call, and for resolver calls whose findings dir is not
# under .kimiflow/ (e.g. a test's temp dir). Needs no jq — it only needs the findings-path
# token + a file check; jq is used when present for a precise command extraction.
#
# Coverage note: this catches every run that REACHES the gate (i.e. everything that commits).
# A --prepare/trivial run that stops before any gate is covered by the prose contract
# (SKILL.md "Persist phase progress") + the behavioral eval (evals/scenarios/11), not here.
set -u

input="$(cat 2>/dev/null || true)"

emit_deny() { # $1 = reason; valid PreToolUse deny with or without jq
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$1" | jq -cRs '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:.}}'
  else
    r="$(printf '%s' "$1" | tr '\n' ' ' | sed 's/\\/\\\\/g; s/"/\\"/g')"
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$r"
  fi
  exit 0
}

git_root() { git -C "${1:-.}" rev-parse --show-toplevel 2>/dev/null || git rev-parse --show-toplevel 2>/dev/null || true; }

# Command (jq precise; raw fallback) + cwd.
if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // .tool_input.args.command // .command // .shell_command // .args.command // empty' 2>/dev/null || true)"
  cwd="$(printf '%s' "$input" | jq -r '.cwd // .tool_input.cwd // .working_directory // empty' 2>/dev/null || true)"
else
  cmd="$input"
  cwd="$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
fi

# Only act on a review-gate resolver invocation.
printf '%s' "$cmd" | grep -q 'resolve-review-gate\.sh' || exit 0

# The run's findings dir as the resolver is called: .kimiflow/<slug>/findings. None → not a
# standard run-scoped call (e.g. a temp/test dir) → allow.
# Scope (by design): the PRESCRIBED repo-root-relative, kebab-slug form. Off-spec invocations —
# an absolute `.kimiflow` path not under the git root, or a whitespace-containing slug — are out
# of contract and not policed (Phase 0 guarantees a kebab-case slug AND the relative resolver call).
findings="$(printf '%s' "$cmd" | grep -oE '\.kimiflow/[^[:space:]]+/findings' | head -1)"
[ -n "$findings" ] || exit 0

root="$(git_root "$cwd")"
[ -n "$root" ] || exit 0
[ -d "$root/.kimiflow" ] || exit 0   # scope: kimiflow repos only

state="$root/${findings%/findings}/STATE.md"
[ -s "$state" ] && exit 0            # STATE.md present + non-empty → allow

emit_deny "kimiflow state-gate: refusing the review-gate call — no STATE.md at ${findings%/findings}/STATE.md. Persist the run state before the gate: Phase 0 creates .kimiflow/<slug>/STATE.md and every phase updates it. terse-output reduces visible output only — it never removes STATE.md. ('small/lean/doc-only run' is not an exemption; only the 'trivial' scope tier runs without STATE, and it runs no gate.)"
