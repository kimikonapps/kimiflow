#!/usr/bin/env bash
# release-consistency-check.sh — verify the same version is declared across release manifests + docs.
#
# Usage: release-consistency-check.sh [--root <dir>] [--quiet]
#
# A manual PRE-RELEASE helper (not a CI gate). It enforces the PROJECT's release-hygiene convention
# that one version string is consistent everywhere; per the Claude/Codex plugin schemas a marketplace
# version is not *required* to match, so manifest version fields are checked only when present.
#
# Source of truth: .claude-plugin/plugin.json .version (override is not needed — keep it minimal).
# Manifest targets (version field OPTIONAL -> skip when absent/null/empty, else must equal SoT):
#   .codex-plugin/plugin.json .version
#   .claude-plugin/marketplace.json .plugins[0].version
#   .agents/plugins/marketplace.json .version
# Required text targets (version MUST be present, else FAIL — never skip):
#   COMPATIBILITY.md must contain "**<ver>**"
#   CHANGELOG.md must contain a line equal to "## <ver>" (anchored — no semver substring collision)
#
# Exit 0 = consistent; non-zero = drift / required version missing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
QUIET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) [ $# -ge 2 ] || { echo "release-consistency-check: --root needs a value" >&2; exit 2; }; ROOT="$2"; shift 2 ;;
    --quiet) QUIET=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    *) echo "release-consistency-check: unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v jq >/dev/null 2>&1 || { echo "release-consistency-check: jq required" >&2; exit 2; }

say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$1"; }

sot_file="$ROOT/.claude-plugin/plugin.json"
[ -f "$sot_file" ] || { echo "release-consistency-check: missing $sot_file" >&2; exit 2; }
ver="$(jq -r '.version // empty' "$sot_file" 2>/dev/null || true)"
[ -n "$ver" ] || { echo "release-consistency-check: no .version in .claude-plugin/plugin.json" >&2; exit 2; }
say "source-of-truth version: $ver  (.claude-plugin/plugin.json)"

fails=0

# JSON manifest target: label, file, jq filter. Skip when the version field is absent/null/empty.
check_json() {
  local label="$1" file="$2" filter="$3" val
  if [ ! -f "$file" ]; then
    say "  skip  $label (file absent)"; return 0
  fi
  val="$(jq -r "${filter} // empty" "$file" 2>/dev/null || true)"
  if [ -z "$val" ] || [ "$val" = "null" ]; then
    say "  skip  $label (no version field)"; return 0
  fi
  if [ "$val" = "$ver" ]; then
    say "  ok    $label ($val)"
  else
    say "  DRIFT $label: $val (expected $ver)  [$file]"; fails=$((fails+1))
  fi
}

check_json ".codex-plugin/plugin.json"        "$ROOT/.codex-plugin/plugin.json"        '.version'
check_json ".claude-plugin/marketplace.json"  "$ROOT/.claude-plugin/marketplace.json"  '.plugins[0].version'
check_json ".agents/plugins/marketplace.json" "$ROOT/.agents/plugins/marketplace.json" '.version'

# Required text target: COMPATIBILITY.md
compat="$ROOT/COMPATIBILITY.md"
if [ ! -f "$compat" ]; then
  say "  FAIL  COMPATIBILITY.md missing  [$compat]"; fails=$((fails+1))
elif grep -qF -- "**${ver}**" "$compat"; then
  say "  ok    COMPATIBILITY.md (**${ver}**)"
else
  say "  FAIL  COMPATIBILITY.md does not state **${ver}**  [$compat]"; fails=$((fails+1))
fi

# Required text target: CHANGELOG.md (anchored exact heading line, avoids e.g. ## 0.1.470 matching ## 0.1.47)
changelog="$ROOT/CHANGELOG.md"
if [ ! -f "$changelog" ]; then
  say "  FAIL  CHANGELOG.md missing  [$changelog]"; fails=$((fails+1))
elif awk -v v="## ${ver}" 'index($0,v)==1 { r=substr($0,length(v)+1); if (r=="" || r ~ /^[^0-9.]/) f=1 } END{exit !f}' "$changelog"; then
  # Accept "## <ver>" and "## <ver> - <suffix>"; reject substring collisions like "## 0.1.470" / "## 0.1.47.1".
  say "  ok    CHANGELOG.md (## ${ver})"
else
  say "  FAIL  CHANGELOG.md has no '## ${ver}' heading  [$changelog]"; fails=$((fails+1))
fi

# Rendered skill outputs: the source files live under docs/render/kimiflow, while the host-facing
# SKILL.md files stay committed. Re-render and let git catch any drift in those generated outputs.
render_source="$ROOT/docs/render/kimiflow"
if [ -d "$render_source" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    say "  FAIL  rendered skill outputs: python3 required"
    fails=$((fails+1))
  elif ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    say "  FAIL  rendered skill outputs: git worktree required for drift check"
    fails=$((fails+1))
  elif (cd "$ROOT" && PYTHONPATH="$SCRIPT_DIR" python3 -m kimiflow_core.render --root "$ROOT" --quiet); then
    if git -C "$ROOT" diff --exit-code --quiet -- SKILL.md skills/kimiflow/SKILL.md; then
      say "  ok    rendered skill outputs"
    else
      say "  FAIL  rendered skill outputs drift from docs/render/kimiflow"
      git -C "$ROOT" diff --name-only -- SKILL.md skills/kimiflow/SKILL.md | sed 's/^/        /'
      fails=$((fails+1))
    fi
  else
    say "  FAIL  rendered skill outputs could not be rendered"
    fails=$((fails+1))
  fi
else
  say "  skip  rendered skill outputs (no docs/render/kimiflow source)"
fi

if [ "$fails" -ne 0 ]; then
  echo "release-consistency-check: $fails inconsistency(ies) for version $ver" >&2
  exit 1
fi
say "release-consistency-check: all consistent for version $ver"
exit 0
