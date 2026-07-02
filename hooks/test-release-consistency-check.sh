#!/usr/bin/env bash
# Tests for release-consistency-check.sh (fixture-based, mirrors test-working-tree-gate.sh style).
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/release-consistency-check.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAILS=0
pass() { printf '  ok   %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }

make_fixture() {
  local d="$1" v="$2"
  mkdir -p "$d/.claude-plugin" "$d/.codex-plugin" "$d/.agents/plugins"
  printf '{"name":"kimiflow","version":"%s"}\n' "$v" > "$d/.claude-plugin/plugin.json"
  printf '{"name":"kimiflow","version":"%s"}\n' "$v" > "$d/.codex-plugin/plugin.json"
  printf '{"name":"kimiflow","plugins":[{"name":"kimiflow","version":"%s"}]}\n' "$v" > "$d/.claude-plugin/marketplace.json"
  printf '{"name":"kimiflow","plugins":[{"name":"kimiflow"}]}\n' > "$d/.agents/plugins/marketplace.json"
  printf 'Last verified against kimiflow **%s** today.\n' "$v" > "$d/COMPATIBILITY.md"
  printf '# Changelog\n\n## %s\n\n- stuff\n' "$v" > "$d/CHANGELOG.md"
}

make_render_fixture() {
  local d="$1" v="$2"
  make_fixture "$d" "$v"
  mkdir -p "$d/docs/render/kimiflow/canonical" "$d/docs/render/kimiflow/overlays" "$d/skills/kimiflow"
  printf 'canonical skill source\n' > "$d/docs/render/kimiflow/canonical/SKILL.md"
  printf 'codex overlay source\n' > "$d/docs/render/kimiflow/overlays/codex.md"
  cp "$d/docs/render/kimiflow/canonical/SKILL.md" "$d/SKILL.md"
  cp "$d/docs/render/kimiflow/overlays/codex.md" "$d/skills/kimiflow/SKILL.md"
  git -C "$d" init -q
  git -C "$d" add .
}

run() { OUT="$("$SCRIPT" --root "$1" 2>&1)"; RC=$?; }

# AC-1.1 consistent fixture passes
F="$TMP/c1"; make_fixture "$F" "0.1.0"
run "$F"
[ "$RC" -eq 0 ] && pass "consistent_passes (exit 0)" || fail "consistent_passes: rc=$RC :: $OUT"

# AC-1.4 .agents (no version field) reported skip, not drift
printf '%s\n' "$OUT" | grep -qiE 'skip .*\.agents/plugins/marketplace\.json' \
  && pass "no_version_field_skipped" || fail "no_version_field_skipped: $OUT"

# AC-1.2 one manifest version drifted -> fail naming the file AND its value
F="$TMP/c2"; make_fixture "$F" "0.1.0"
printf '{"name":"kimiflow","version":"9.9.9"}\n' > "$F/.codex-plugin/plugin.json"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF '.codex-plugin/plugin.json' && printf '%s' "$OUT" | grep -qF '9.9.9'; } \
  && pass "drift_detected (file + value)" || fail "drift_detected: rc=$RC :: $OUT"

# AC-1.3a missing CHANGELOG heading -> fail naming CHANGELOG.md
F="$TMP/c3"; make_fixture "$F" "0.1.0"
printf '# Changelog\n\n## 0.0.9\n\n- old\n' > "$F/CHANGELOG.md"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'CHANGELOG.md'; } \
  && pass "missing_changelog_entry" || fail "missing_changelog_entry: rc=$RC :: $OUT"

# AC-1.3a (anchor) semver substring must NOT satisfy: ## 0.1.470 != ## 0.1.47
F="$TMP/c3b"; make_fixture "$F" "0.1.47"
printf '# Changelog\n\n## 0.1.470\n\n- not a real match\n' > "$F/CHANGELOG.md"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'CHANGELOG.md'; } \
  && pass "changelog_anchored_no_substring_collision" || fail "changelog_substring_collision: rc=$RC :: $OUT"

# AC-1.3a (anchor) a suffixed heading "## <ver> - <date>" MUST still satisfy
F="$TMP/c3c"; make_fixture "$F" "0.1.0"
printf '# Changelog\n\n## 0.1.0 - 2026-06-28\n\n- release\n' > "$F/CHANGELOG.md"
run "$F"
[ "$RC" -eq 0 ] && pass "changelog_suffixed_heading_ok" || fail "changelog_suffixed_heading: rc=$RC :: $OUT"

# AC-1.3b missing COMPATIBILITY version -> fail naming COMPATIBILITY.md
F="$TMP/c4"; make_fixture "$F" "0.1.0"
printf 'Compatibility notes without the version token.\n' > "$F/COMPATIBILITY.md"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'COMPATIBILITY.md'; } \
  && pass "missing_compat_version" || fail "missing_compat_version: rc=$RC :: $OUT"

# AC-2.1 render sources present and host outputs current -> pass
F="$TMP/c5"; make_render_fixture "$F" "0.1.0"
run "$F"
{ [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -qF 'rendered skill outputs'; } \
  && pass "rendered_outputs_current" || fail "rendered_outputs_current: rc=$RC :: $OUT"

# AC-2.2 committed host output drift -> fail naming the rendered outputs
F="$TMP/c6"; make_render_fixture "$F" "0.1.0"
printf 'manual drift\n' > "$F/SKILL.md"
git -C "$F" add SKILL.md
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'rendered skill outputs drift'; } \
  && pass "rendered_output_drift_detected" || fail "rendered_output_drift_detected: rc=$RC :: $OUT"

# AC-2.3 unstaged host output drift must fail and must not be overwritten
F="$TMP/c6b"; make_render_fixture "$F" "0.1.0"
printf 'unstaged drift\n' > "$F/SKILL.md"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'rendered skill outputs drift' && grep -qF 'unstaged drift' "$F/SKILL.md"; } \
  && pass "rendered_unstaged_drift_not_overwritten" || fail "rendered_unstaged_drift_not_overwritten: rc=$RC :: $OUT"

# AC-3.1 present always-loaded prose under budget -> pass
F="$TMP/c7"; make_fixture "$F" "0.1.0"
mkdir -p "$F/skills/kimiflow"
printf 'root skill\n' > "$F/SKILL.md"
printf 'codex skill\n' > "$F/skills/kimiflow/SKILL.md"
run "$F"
{ [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -qF 'SKILL.md always-loaded prose bytes'; } \
  && pass "prose_budget_current" || fail "prose_budget_current: rc=$RC :: $OUT"

# AC-3.2 oversized always-loaded prose -> fail naming the file
F="$TMP/c8"; make_fixture "$F" "0.1.0"
mkdir -p "$F/skills/kimiflow"
awk 'BEGIN{for(i=0;i<56001;i++) printf "x"}' > "$F/SKILL.md"
printf 'codex skill\n' > "$F/skills/kimiflow/SKILL.md"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'SKILL.md always-loaded prose bytes'; } \
  && pass "prose_budget_oversize_detected" || fail "prose_budget_oversize_detected: rc=$RC :: $OUT"

# AC-3.3 oversized phase detail prose -> fail naming the phase file
F="$TMP/c9"; make_fixture "$F" "0.1.0"
mkdir -p "$F/phases"
awk 'BEGIN{for(i=0;i<20001;i++) printf "x"}' > "$F/phases/phase-0-setup.md"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'phases/phase-0-setup.md phase prose bytes'; } \
  && pass "phase_budget_oversize_detected" || fail "phase_budget_oversize_detected: rc=$RC :: $OUT"

# NOTE: real-repo version consistency is verified MANUALLY before a release
# (`bash hooks/release-consistency-check.sh`), NOT asserted here — this unit test must stay a
# logic test over fixtures so CI never becomes a de-facto release-consistency gate (INTENT: kein CI-Gate).

printf -- '----\n'
if [ "$FAILS" -eq 0 ]; then echo "release-consistency-check tests: PASS"; exit 0; else echo "release-consistency-check tests: $FAILS FAIL"; exit 1; fi
