#!/usr/bin/env bash
# kimiflow — structural contract for lazy same-pass workflow prose quality.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
REF="$ROOT/references/workflow-prose-quality.md"
PHASES="phase-1-clarify.md phase-2-understand.md phase-3-plan.md phase-4-review-approval.md phase-6-verify.md phase-7-review-commit.md"

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
has() { grep -Fq -- "$2" "$1" || fail "$3"; }
has_flat() { tr -s '[:space:]' ' ' < "$1" | grep -Fq -- "$2" || fail "$3"; }

test -f "$REF" || fail "missing workflow prose quality reference"
test "$(wc -c < "$REF" | tr -d ' ')" -le 4000 || fail "workflow prose quality reference exceeds 4000 bytes"

has "$REF" "same model pass" "same-pass rule missing"
has "$REF" "no extra agent, model call, workflow step, or prose gate" "zero-extra-control rule missing"
has "$REF" "Keep the confirmed meaning" "meaning-preservation rule missing"
has "$REF" "Make the minimum effective edit" "minimum-edit rule missing"
has "$REF" "keep the owning existing gate closed" "semantic defects do not retain existing gate ownership"
has "$REF" "Unsupported or invented claims, unverifiable success criteria, terminology drift, and generic review findings" "semantic defect classes missing"
has "$REF" "never block a run by themselves" "style-only defects became blocking"
has "$REF" "Remove only filler, importance rhetoric, meta-commentary, synonym cycling, repetition, and decorative formatting" "style defect classes missing"
has "$REF" "Do not invent claims, numbers, sources, evidence, opinions, or requirements" "invention ban missing"
has "$REF" "Code, JSON, schemas, commands, logs, diffs, machine receipts, evidence quotes, and third-party text" "technical exclusions missing"
has "$REF" "never replaces evidence or a mechanical gate" "evidence precedence missing"

for phase in $PHASES; do
  file="$ROOT/phases/$phase"
  has "$file" '${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/references/workflow-prose-quality.md' "$phase lacks installed Claude/Pi reference"
  has "$file" '$KIMIFLOW_PLUGIN_ROOT/references/workflow-prose-quality.md' "$phase lacks installed Codex reference"
  has "$file" "same model pass" "$phase lacks same-pass boundary"
done

for phase in phase-3-plan.md phase-4-review-approval.md phase-6-verify.md phase-7-review-commit.md; do
  test "$(grep -Fo 'workflow-prose-quality.md' "$ROOT/phases/$phase" | wc -l | tr -d ' ')" -ge 2 \
    || fail "$phase does not pass prose quality to delegated authors/reviewers"
done

has "$ROOT/docs/render/kimiflow/canonical/SKILL.md" "workflow-prose-quality.md" "canonical skill lacks lazy prose-quality route"
has "$ROOT/docs/render/kimiflow/overlays/codex.md" '$KIMIFLOW_PLUGIN_ROOT/references/workflow-prose-quality.md' "Codex overlay lacks plugin-rooted prose-quality route"
PI_SKILL="$ROOT/hosts/pi/skills/kimiflow/SKILL.md"
has "$PI_SKILL" '<loaded-kimiflow-package-root>/references/workflow-prose-quality.md' "Pi skill lacks installed-root prose-quality route"
has "$PI_SKILL" 'Clarify, Understand, Plan, Plan-Review, Verify, or Review/Commit' "Pi skill lacks all six prose boundaries"
has "$PI_SKILL" 'same model pass' "Pi skill lacks same-pass boundary"
has "$PI_SKILL" 'never from the current project' "Pi skill allows cwd-dependent prose routing"
test "$(wc -c < "$ROOT/SKILL.md" | tr -d ' ')" -le 17000 || fail "canonical always-loaded skill exceeds 17000 bytes"
test "$(wc -c < "$ROOT/skills/kimiflow/SKILL.md" | tr -d ' ')" -le 15000 || fail "Codex always-loaded skill exceeds 15000 bytes"

for readme in README.md README.de.md; do
  has "$ROOT/$readme" "https://github.com/petergyang/no-ai-slop" "$readme lacks no-ai-slop credit"
  grep -Fiq 'same-pass' "$ROOT/$readme" || fail "$readme lacks same-pass behavior"
  case "$readme" in
    README.md)
      has_flat "$ROOT/$readme" "never adds another model call" "$readme lacks negative extra-call claim"
      has_flat "$ROOT/$readme" "not an AI-authorship detector" "$readme lacks negative detector boundary"
      ;;
    README.de.md)
      has_flat "$ROOT/$readme" "keinen weiteren Modellaufruf" "$readme lacks negative extra-call claim"
      has_flat "$ROOT/$readme" "kein AI-Urheberschaftsdetektor" "$readme lacks negative detector boundary"
      ;;
  esac
done

if [ -d "$ROOT/plugins/kimiflow" ]; then
  test -f "$ROOT/plugins/kimiflow/references/workflow-prose-quality.md" || fail "runtime candidate lacks prose-quality reference"
  cmp "$REF" "$ROOT/plugins/kimiflow/references/workflow-prose-quality.md" >/dev/null || fail "runtime candidate prose-quality reference drifted"
fi

printf 'PASS: workflow_prose_quality_contract\n'
