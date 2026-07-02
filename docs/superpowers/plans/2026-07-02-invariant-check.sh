#!/usr/bin/env bash
# B4 preservation-invariant check (plan tooling, not a runtime hook).
# One verbatim needle per invariants-artifact row, fixed-string-grepped against
# SKILL.md / reference.md. Fails on ANY miss. Must pass against the pre-compaction
# files (sanity) and gates every B4 commit that touches SKILL.md/reference.md.
# Run: bash docs/superpowers/plans/2026-07-02-invariant-check.sh
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FAILS=0
need() { # $1=file $2=needle
  if grep -qF -- "$2" "$ROOT/$1"; then :; else
    printf 'MISS %s :: %s\n' "$1" "$2"; FAILS=$((FAILS + 1))
  fi
}

# --- SKILL.md: always-loaded rules (artifact rows) ---
while IFS= read -r n; do [ -n "$n" ] && need SKILL.md "$n"; done <<'NEEDLES'
Do NOT auto-trigger on ordinary feature/bug/refactor requests
never writes code directly and never auto-picks a risky action
otherwise ask one plain-language question
STOP at the pre-build approval gate
then STOP. No plan and no code
STOP with a resumable backlog run
do not silently invent a plan
Never use when the user asked for
ONE code-review lens
No code edits
no edits until the user chooses a slice
phases 0–4, then STOP
blind implementation is forbidden
shows them for approval
It does not edit code
never auto-committed
exploit paths
never persisted
HARD RULE
NEVER paste a full artifact into chat
Gate verdict = ONE line
Narration ≠ persistence
Density NEVER costs rigor
never for gate criteria, scores, or thresholds
No speculative abstractions, no features beyond the request
Severity never higher than provable
done/green/root cause found
Beyond ~10 → stop and ask the user first
blocks the review-gate call
hooks/active-run.sh start --run .kimiflow/<slug>
hooks/background-run.sh
cannot be applied blindly
hooks/agentic-readiness.sh status|gate
Never loop forever
verbatim only for a snippet under ~15 lines
hooks/launcher-status.sh --pretty
do not auto-pick
hooks/working-tree-gate.sh
STOP before changing files
ask one simple question
requires a target path
refresh-baseline --write
git rev-parse --is-inside-work-tree
In doubt, the smaller tier
resolve-verbosity.sh
you MUST ask once
project-map-status.sh
PMS coverage --affected
one plain-language question only if the request lacks
never auto-pick
Does this match?
Did I understand the problem correctly?
Is this the right cleanup scope?
hooks/clarify-gate.sh
<!-- kimiflow:clarify-evidence mode=questions count=2 confirmed=yes source=current-run -->
hooks/memory-router.sh status
MR recall --query-file
record the graceful skip and continue
current-state-gate.sh
CSG verify --assessment
suggest-affected-sections.sh --intent
a plan-blocking unknown → resolve first
before changing production code
find AND prove the cause
root cause not proven → do NOT fix
should this exist at all
repo-wide pre-delete grep
git-history-freshness
Caller-grep is a MINIMUM
only if a vault MCP is connected
never structural merges
do not send it to reviewers
hooks/plan-blocker-gate.sh
required before spawning reviewers
A cut survives only if no reviewer finds one
tests green before+after
No self-reported count
hooks/resolve-review-gate.sh
--round <N> --expect <lensCSV>
stop + ask, gate CLOSED
never auto-proceed
Status: backlog
resolve-build-gate.sh
Approve to build, change something, or defer to backlog?
do NOT build
production code never rides along
no proof → don't delete
don't burn a blind second attempt
hooks/red-green-gate.sh
--mode fix
hooks/lsp-diagnostics.sh
re-run the decisive command
back to phase 5
--kind review --write
review light
bug-regression
add the third when the diff touches hooks
CANDIDATE <SEVERITY> <ref> :: <claim> :: verify=<smallest check>
active refutation attempt
never raw candidates
never silently skip the advisory channel
test-weakening-scan.sh
secret-content-scan.sh
--expect code-verified
Wait for explicit OK
no co-author/AI trailer
never staged or committed
Never batch slices
MR review-run --run
--skip "<reason>"
blocks completion
store the key
never patches skills or writes external notes blindly
SKILL-DRAFTS
PMS refresh --changed
map-staleness-nudge.sh
mark-done <id> --commit <sha> --write
never auto-guess
improvements-staleness-nudge.sh
Only after the commit gate and learning review are open
never gates, cost, quality, or behavior
BEFORE fan-out
uncommitted
never wired into CI
NEEDLES

# --- SKILL.md: smoke phrase contracts (smoke-install*.sh greps) ---
while IFS= read -r n; do [ -n "$n" ] && need SKILL.md "$n"; done <<'NEEDLES'
Launcher / menu
Natural mode aliases
pre-build approval stop
mandatory micro-grill
Vault Pulse
Project Map Bootstrap
improvements-status.sh
refresh --changed
Agentic Readiness Layer
Active Session Contract
Background Handles
Current-State Pulse / Gate
--verify-feature <feature-or-path>
Memory Router & Learning Loop
code-review ensemble
NEEDLES

# --- reference.md: WS3-exception clauses (rows 145-146 canonical in rubric) + smoke lines ---
while IFS= read -r n; do [ -n "$n" ] && need reference.md "$n"; done <<'NEEDLES'
fixes the verified root cause
non-contradictory
no invented assumptions
does it address the cause, not the symptom
CANDIDATE <SEVERITY> <ref> :: <claim> :: verify=<smallest check>
NEEDLES

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "INVARIANTS OK"; exit 0; else echo "$FAILS MISSING"; exit 1; fi
