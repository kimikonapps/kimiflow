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

make_launcher_fixture() {
  local d="$1" v="$2"
  make_fixture "$d" "$v"
  mkdir -p "$d/hooks"
}

run() { OUT="$("$SCRIPT" --root "$1" 2>&1)"; RC=$?; }

make_tagged_fixture() {
  local d="$1" v="$2"
  make_fixture "$d" "$v"
  git -C "$d" init -q
  git -C "$d" config user.email kimiflow@example.test
  git -C "$d" config user.name "kimiflow test"
  git -C "$d" add .
  git -C "$d" commit -q -m release
  git -C "$d" tag "kimiflow--v$v"
}

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
awk 'BEGIN{for(i=0;i<17001;i++) printf "x"}' > "$F/SKILL.md"
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

# AC-4.1 launcher budget fixture must not inherit caller KIMIFLOW_HOME/HOME content
F="$TMP/c10"; make_launcher_fixture "$F" "0.1.0"
cat > "$F/hooks/launcher-status.sh" <<'EOF'
#!/usr/bin/env bash
if [ -n "${KIMIFLOW_HOME:-}" ] && [ -f "$KIMIFLOW_HOME/metrics/token-economics.jsonl" ]; then
  cat "$KIMIFLOW_HOME/metrics/token-economics.jsonl"
fi
printf '{"ok":true}\n'
EOF
chmod +x "$F/hooks/launcher-status.sh"
dirty_home="$TMP/dirty-home"
mkdir -p "$dirty_home/metrics"
awk 'BEGIN{for(i=0;i<9000;i++) printf "x"}' > "$dirty_home/metrics/token-economics.jsonl"
OUT="$(KIMIFLOW_HOME="$dirty_home" HOME="$dirty_home" "$SCRIPT" --root "$F" 2>&1)"; RC=$?
{ [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -qF 'launcher-status default output bytes'; } \
  && pass "launcher_budget_uses_clean_home" || fail "launcher_budget_uses_clean_home: rc=$RC :: $OUT"

# AC-4.2 launcher byte budget counts exact stdout bytes, including trailing newline
F="$TMP/c11"; make_launcher_fixture "$F" "0.1.0"
cat > "$F/hooks/launcher-status.sh" <<'EOF'
#!/usr/bin/env bash
awk 'BEGIN{for(i=0;i<8000;i++) printf "x"; printf "\n"}'
EOF
chmod +x "$F/hooks/launcher-status.sh"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'launcher-status default output bytes: 8001'; } \
  && pass "launcher_budget_counts_trailing_newline" || fail "launcher_budget_counts_trailing_newline: rc=$RC :: $OUT"

# AC-5.1 a repository candidate checker is part of the manual release verdict.
F="$TMP/c12"; make_fixture "$F" "0.1.0"
mkdir -p "$F/hooks"
printf '#!/usr/bin/env bash\nexit 1\n' > "$F/hooks/build-plugin-candidate.sh"
chmod +x "$F/hooks/build-plugin-candidate.sh"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'clean plugin candidate'; } \
  && pass "candidate_drift_blocks_release" || fail "candidate_drift_blocks_release: rc=$RC :: $OUT"

# AC-5.2 source commits after the current tag require real Unreleased notes.
F="$TMP/c13"; make_tagged_fixture "$F" "0.1.0"
printf 'change\n' > "$F/code.txt"
git -C "$F" add code.txt
git -C "$F" commit -q -m change
printf '# Changelog\n\n## Unreleased\n\n_No unreleased changes._\n\n## 0.1.0\n\n- release\n' > "$F/CHANGELOG.md"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'Unreleased is empty'; } \
  && pass "post_tag_change_requires_unreleased_note" || fail "post_tag_change_requires_unreleased_note: rc=$RC :: $OUT"

# AC-5.3 a populated Unreleased section satisfies the post-tag guard.
printf '# Changelog\n\n## Unreleased\n\n### Changed\n\n- runtime repair\n\n## 0.1.0\n\n- release\n' > "$F/CHANGELOG.md"
run "$F"
[ "$RC" -eq 0 ] && pass "post_tag_change_with_note_passes" || fail "post_tag_change_with_note: rc=$RC :: $OUT"

make_runtime_fixture() {
  local d="$1" v="$2" variant="${3:-stable}"
  make_fixture "$d" "$v"
  mkdir -p "$d/hooks" "$d/plugins/kimiflow" "$d/references"
  printf '{"schema_version":1}\n' >"$d/plugins/kimiflow/RUNTIME-FINGERPRINT.json"
  printf '{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n' >"$d/references/runtime-release-v1.schema.json"
  printf '#!/usr/bin/env bash\nexit 0\n' >"$d/hooks/build-plugin-candidate.sh"
  cat >"$d/hooks/build-runtime-release.sh" <<EOF
#!/usr/bin/env bash
set -eu
command_name="\$1"
shift
if [ "\$command_name" = build ]; then
  while [ "\$#" -gt 0 ]; do
    case "\$1" in --output) output="\$2"; shift 2 ;; *) shift ;; esac
  done
  mkdir -p "\$output"
  printf '{"ok":true}\\n' >"\$output/kimiflow-update-v1.json"
  if [ "$variant" = stable ]; then
    printf 'stable\\n' >"\$output/kimiflow-runtime-${v}.zip"
  else
    counter=0
    [ ! -f "$d/runtime-counter" ] || counter="\$(cat "$d/runtime-counter")"
    counter=\$((counter + 1))
    printf '%s\\n' "\$counter" >"$d/runtime-counter"
    printf '%s\\n' "\$counter" >"\$output/kimiflow-runtime-${v}.zip"
  fi
elif [ "\$command_name" = verify ]; then
  exit 0
else
  exit 2
fi
EOF
  chmod +x "$d/hooks/build-plugin-candidate.sh" "$d/hooks/build-runtime-release.sh"
  git -C "$d" init -q
  git -C "$d" config user.email kimiflow@example.test
  git -C "$d" config user.name "kimiflow test"
  git -C "$d" add .
  git -C "$d" commit -q -m runtime
}

# AC-6.1 deterministic runtime packaging contributes to the manual release verdict.
F="$TMP/c14"; make_runtime_fixture "$F" "0.1.0" "stable"
run "$F"
{ [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -qF 'deterministic runtime release'; } \
  && pass "runtime_release_reproducible" || fail "runtime_release_reproducible: rc=$RC :: $OUT"

# AC-6.2 byte drift across identical builds blocks release.
F="$TMP/c15"; make_runtime_fixture "$F" "0.1.0" '${RANDOM}'
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'deterministic runtime release or offline integrity'; } \
  && pass "runtime_release_nondeterminism_detected" || fail "runtime_release_nondeterminism: rc=$RC :: $OUT"

# AC-6.3 every runtime candidate input must already be represented in the Git index.
F="$TMP/c16"; make_runtime_fixture "$F" "0.1.0" "stable"
printf 'untracked release input\n' >"$F/plugins/kimiflow/new-runtime-file.txt"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'untracked release-input drift'; } \
  && pass "runtime_release_untracked_input_detected" || fail "runtime_release_untracked_input: rc=$RC :: $OUT"

# AC-6.4 once the distribution contract exists, its builder is a mandatory release gate.
F="$TMP/c17"; make_runtime_fixture "$F" "0.1.0" "stable"
chmod -x "$F/hooks/build-runtime-release.sh"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'required runtime release builder'; } \
  && pass "runtime_release_builder_required" || fail "runtime_release_builder_required: rc=$RC :: $OUT"

# AC-6.5 removing both runtime inputs cannot hide the still-declared distribution contract.
F="$TMP/c18"; make_runtime_fixture "$F" "0.1.0" "stable"
rm "$F/hooks/build-runtime-release.sh" "$F/plugins/kimiflow/RUNTIME-FINGERPRINT.json"
run "$F"
{ [ "$RC" -ne 0 ] && printf '%s' "$OUT" | grep -qF 'required runtime release builder'; } \
  && pass "runtime_release_contract_cannot_skip_gate" || fail "runtime_release_contract_skip: rc=$RC :: $OUT"

# NOTE: real-repo version consistency is verified MANUALLY before a release
# (`bash hooks/release-consistency-check.sh`), NOT asserted here — this unit test must stay a
# logic test over fixtures so CI never becomes a de-facto release-consistency gate (INTENT: kein CI-Gate).

printf -- '----\n'
if [ "$FAILS" -eq 0 ]; then
  echo "ok   test_release_runtime_asset_gate"
  echo "release-consistency-check tests: PASS"
  exit 0
else
  echo "release-consistency-check tests: $FAILS FAIL"
  exit 1
fi
