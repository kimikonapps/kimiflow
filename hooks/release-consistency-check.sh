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
# Size budgets (fail when present and exceeded):
#   SKILL.md <= 17000 bytes
#   skills/kimiflow/SKILL.md <= 15000 bytes
#   phases/*.md <= 20000 bytes each
#   hooks/launcher-status.sh default JSON <= 8000 bytes on a clean fixture repo
#   hooks/launcher-status.sh --pretty <= 12000 bytes on a clean fixture repo
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

check_max_bytes() {
  local label="$1" file="$2" max="$3" bytes
  if [ ! -f "$file" ]; then
    say "  skip  $label byte budget (file absent)"
    return 0
  fi
  bytes="$(wc -c < "$file" | tr -d '[:space:]')"
  if [ "$bytes" -le "$max" ]; then
    say "  ok    $label bytes ($bytes <= $max)"
  else
    say "  FAIL  $label bytes: $bytes (max $max)  [$file]"
    fails=$((fails+1))
  fi
}

check_output_bytes() {
  local label="$1" max="$2"; shift 2
  local out_file bytes
  out_file="$(mktemp)"
  if "$@" >"$out_file" 2>/dev/null; then
    bytes="$(wc -c < "$out_file" | tr -d '[:space:]')"
    if [ "$bytes" -le "$max" ]; then
      say "  ok    $label bytes ($bytes <= $max)"
    else
      say "  FAIL  $label bytes: $bytes (max $max)"
      fails=$((fails+1))
    fi
  else
    say "  FAIL  $label byte budget command failed"
    fails=$((fails+1))
  fi
  rm -f "$out_file"
}

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
# SKILL.md files stay committed. Check without writing so manual output drift cannot be overwritten.
render_source="$ROOT/docs/render/kimiflow"
if [ -d "$render_source" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    say "  FAIL  rendered skill outputs: python3 required"
    fails=$((fails+1))
  elif (cd "$ROOT" && PYTHONPATH="$SCRIPT_DIR" python3 -m kimiflow_core.render --root "$ROOT" --check --quiet); then
    say "  ok    rendered skill outputs"
  else
    say "  FAIL  rendered skill outputs drift from docs/render/kimiflow"
    fails=$((fails+1))
  fi
else
  say "  skip  rendered skill outputs (no docs/render/kimiflow source)"
fi

# The published marketplace source must be a clean, reproducible runtime candidate rather than
# the maintainer repository. Keep this manual-release-only like the rest of this helper.
candidate_builder="$ROOT/hooks/build-plugin-candidate.sh"
if [ -x "$candidate_builder" ]; then
  if "$candidate_builder" --check >/dev/null 2>&1; then
    say "  ok    clean plugin candidate + runtime fingerprint"
  else
    say "  FAIL  clean plugin candidate or runtime fingerprint drift"
    fails=$((fails+1))
  fi
else
  say "  skip  clean plugin candidate (builder absent)"
fi

# A tagged version may legitimately have no pending notes. Once source commits exist after that
# tag, however, an empty Unreleased section would make a later release silently omit its changes.
if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  release_ref=""
  for candidate_ref in "kimiflow--v$ver" "v$ver" "$ver"; do
    if git -C "$ROOT" rev-parse --verify --quiet "refs/tags/$candidate_ref" >/dev/null; then
      release_ref="$candidate_ref"
      break
    fi
  done
  if [ -n "$release_ref" ]; then
    commits_after_release="$(git -C "$ROOT" rev-list --count "$release_ref..HEAD" 2>/dev/null || printf '0')"
    if [ "$commits_after_release" -gt 0 ]; then
      if awk '
        /^## Unreleased[[:space:]]*$/ { inside=1; next }
        inside && /^##[[:space:]]/ { exit }
        inside {
          line=$0
          gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
          if (line != "" && line != "_No unreleased changes._" && line !~ /^<!--.*-->$/) found=1
        }
        END { exit !found }
      ' "$changelog"; then
        say "  ok    Unreleased documents $commits_after_release commit(s) after $release_ref"
      else
        say "  FAIL  Unreleased is empty with $commits_after_release commit(s) after $release_ref"
        fails=$((fails+1))
      fi
    fi
  fi
fi

check_max_bytes "SKILL.md always-loaded prose" "$ROOT/SKILL.md" 17000
check_max_bytes "Codex SKILL.md always-loaded prose" "$ROOT/skills/kimiflow/SKILL.md" 15000
if [ -d "$ROOT/phases" ]; then
  phase_found=0
  for phase_file in "$ROOT"/phases/*.md; do
    [ -e "$phase_file" ] || continue
    phase_found=1
    check_max_bytes "${phase_file#$ROOT/} phase prose" "$phase_file" 20000
  done
  if [ "$phase_found" -eq 0 ]; then
    say "  skip  phase prose byte budgets (no phases/*.md files)"
  fi
else
  say "  skip  phase prose byte budgets (no phases directory)"
fi

launcher="$ROOT/hooks/launcher-status.sh"
if [ -x "$launcher" ]; then
  budget_tmp="$(mktemp -d)"
  trap 'rm -rf "$budget_tmp"' EXIT
  budget_repo="$budget_tmp/repo"
  budget_home="$budget_tmp/home"
  mkdir -p "$budget_repo" "$budget_home"
  git -C "$budget_repo" init -q
  check_output_bytes "launcher-status default output" 8000 \
    env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=$budget_home" "KIMIFLOW_HOME=$budget_home" \
      "KIMIFLOW_GLOBAL_METRICS=on" "KIMIFLOW_PLUGIN_ROOT=$ROOT" \
      "$launcher" --root "$budget_repo"
  check_output_bytes "launcher-status --pretty output" 12000 \
    env -i "PATH=${PATH:-/usr/bin:/bin}" "HOME=$budget_home" "KIMIFLOW_HOME=$budget_home" \
      "KIMIFLOW_GLOBAL_METRICS=on" "KIMIFLOW_PLUGIN_ROOT=$ROOT" \
      "$launcher" --root "$budget_repo" --pretty
else
  say "  skip  launcher-status output byte budgets (script absent)"
fi

if [ "$fails" -ne 0 ]; then
  echo "release-consistency-check: $fails inconsistency(ies) for version $ver" >&2
  exit 1
fi
say "release-consistency-check: all consistent for version $ver"
exit 0
