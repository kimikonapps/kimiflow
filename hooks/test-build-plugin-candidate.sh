#!/usr/bin/env bash
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
WORK="$(mktemp -d)"
tracked_source="$ROOT/hooks/.candidate-untracked-fixture"
trap 'rm -rf "$WORK"; rm -f "$tracked_source"' EXIT
CANDIDATE="$WORK/kimiflow"

"$ROOT/hooks/build-plugin-candidate.sh" --write --output "$CANDIDATE" >/dev/null
[ -f "$CANDIDATE/RUNTIME-FINGERPRINT.json" ]
jq -e '.schema_version == 1 and (.runtime_fingerprint | test("^sha256:[0-9a-f]{64}$")) and .file_count == (.files | length)' "$CANDIDATE/RUNTIME-FINGERPRINT.json" >/dev/null

test_optional_pi_package_release_and_embedded_host_parity() {
  version="$(jq -r '.version' "$ROOT/.claude-plugin/plugin.json")"
  jq -e --arg version "$version" '
    .name == "@kimiflow/pi" and .version == $version and .type == "module"
    and .pi.extensions == ["./hosts/pi/extensions/calm.js","./hosts/pi/extensions/captain.js","./hosts/pi/extensions/worker.js"]
    and .pi.skills == ["./hosts/pi/skills/kimiflow"]
  ' "$CANDIDATE/package.json" >/dev/null
  for rel in \
    hosts/pi/extensions/calm.js \
    hosts/pi/extensions/captain.js \
    hosts/pi/extensions/worker.js \
    hosts/pi/skills/kimiflow/SKILL.md \
    hooks/pi-host.sh \
    hooks/kimiflow_core/pi_herdr.py \
    hooks/kimiflow_core/pi_host.py; do
    [ -f "$CANDIDATE/$rel" ]
    jq -e --arg rel "$rel" '.files[] | select(.path == $rel)' \
      "$CANDIDATE/RUNTIME-FINGERPRINT.json" >/dev/null
  done
}
test_optional_pi_package_release_and_embedded_host_parity

test_required_pi_runtime_paths_must_be_git_indexed() {
  fixture="$WORK/index-fixture"
  mkdir -p "$fixture/hooks/kimiflow_core" \
    "$fixture/hosts/pi/extensions" \
    "$fixture/hosts/pi/skills/kimiflow"
  cp "$ROOT/hooks/build-plugin-candidate.sh" "$fixture/hooks/"
  cp "$ROOT/package.json" "$fixture/"
  cp "$ROOT/hooks/pi-host.sh" "$fixture/hooks/"
  cp "$ROOT/hooks/kimiflow_core/pi_herdr.py" \
    "$fixture/hooks/kimiflow_core/"
  cp "$ROOT/hooks/kimiflow_core/pi_host.py" \
    "$fixture/hooks/kimiflow_core/"
  cp "$ROOT/hosts/pi/extensions/calm.js" \
    "$ROOT/hosts/pi/extensions/captain.js" \
    "$ROOT/hosts/pi/extensions/worker.js" \
    "$fixture/hosts/pi/extensions/"
  cp "$ROOT/hosts/pi/skills/kimiflow/SKILL.md" \
    "$fixture/hosts/pi/skills/kimiflow/"
  git -C "$fixture" init -q
  git -C "$fixture" add \
    hooks/build-plugin-candidate.sh package.json hooks/pi-host.sh \
    hooks/kimiflow_core/pi_herdr.py \
    hooks/kimiflow_core/pi_host.py \
    hosts/pi/extensions/calm.js \
    hosts/pi/extensions/captain.js \
    hosts/pi/skills/kimiflow/SKILL.md
  if "$fixture/hooks/build-plugin-candidate.sh" \
    --write --output "$WORK/untracked-required/kimiflow" \
    >"$WORK/untracked-required.log" 2>&1; then
    echo "candidate builder accepted an untracked required Pi runtime" >&2
    exit 1
  fi
  grep -q 'required Pi runtime file is not tracked: hosts/pi/extensions/worker.js' \
    "$WORK/untracked-required.log"
}
test_required_pi_runtime_paths_must_be_git_indexed

[ ! -e "$CANDIDATE/hooks/build-runtime-release.sh" ]
[ ! -e "$CANDIDATE/hooks/publish-runtime-release.sh" ]
[ ! -e "$CANDIDATE/hooks/kimiflow_core/runtime_release.py" ]
printf 'must not ship\n' > "$tracked_source"
"$ROOT/hooks/build-plugin-candidate.sh" --write --output "$CANDIDATE" >/dev/null
[ ! -e "$CANDIDATE/hooks/.candidate-untracked-fixture" ]
rm "$tracked_source"
for forbidden in .git .kimiflow .superpowers docs/superpowers; do
  [ ! -e "$CANDIDATE/$forbidden" ] || { echo "candidate contains forbidden path: $forbidden" >&2; exit 1; }
done
PYTHONDONTWRITEBYTECODE=1 KIMIFLOW_PLUGIN_ROOT="$CANDIDATE" "$CANDIDATE/hooks/install-codex-hooks.sh" --check >/dev/null
PYTHONDONTWRITEBYTECODE=1 KIMIFLOW_PLUGIN_ROOT="$CANDIDATE" "$CANDIDATE/hooks/reference-section.sh" 'Intent clarification (grill, plain language) (Phase 1)' | grep -q '^## Intent clarification'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$CANDIDATE/hooks" python3 -c 'from kimiflow_core import active_run, model_adapter; assert active_run.normalized_host("local-qwen") == "local-qwen"; assert model_adapter.PROTOCOL_VERSION == 1'

# Build the smoke root from candidate bytes first. Add only an explicit set of
# maintainer-only fixtures that the structural smoke harnesses inspect. Starting
# from the checkout would let a missing runtime file leak into the smoke root and
# hide an allowlist regression.
SMOKE_ROOT="$WORK/smoke-root"
mkdir -p "$SMOKE_ROOT"
cp -R "$CANDIDATE"/. "$SMOKE_ROOT"/
copy_fixture() {
  rel="$1"
  if jq -e --arg rel "$rel" '.files[] | select(.path == $rel)' "$CANDIDATE/RUNTIME-FINGERPRINT.json" >/dev/null; then
    echo "candidate smoke fixture overlaps runtime inventory: $rel" >&2
    exit 1
  fi
  [ -f "$ROOT/$rel" ] || { echo "candidate smoke fixture missing: $rel" >&2; exit 1; }
  mkdir -p "$SMOKE_ROOT/$(dirname "$rel")"
  cp "$ROOT/$rel" "$SMOKE_ROOT/$rel"
}
while IFS= read -r rel; do
  [ -n "$rel" ] && copy_fixture "$rel"
done <<'EOF'
.agents/plugins/marketplace.json
.claude-plugin/marketplace.json
docs/architecture.md
docs/demo/README.md
docs/demo/play.sh
docs/kimiflow-vs-claude-md-vs-superpowers.md
docs/render/kimiflow/canonical/SKILL.md
docs/render/kimiflow/overlays/codex.md
evals/README.md
evals/scenarios/08-advisory-triage-failclosed.md
evals/scenarios/13-top-model-orchestrator.md
evals/scenarios/15-evidence-guided-discovery.md
examples/01-small-fix.md
examples/02-risky-bugfix.md
examples/03-feature.md
examples/README.md
hooks/ci-test-plan.sh
hooks/kimiflow_core/ci_test_plan.py
hooks/memory_router/tests/test_attribution.py
hooks/memory_router/tests/test_recall.py
hooks/memory_router/tests/test_standards.py
hooks/smoke-install.sh
hooks/smoke-install-codex.sh
hooks/test-active-run.sh
hooks/test-clarify-gate.sh
hooks/test-conformance-gate.sh
hooks/test-current-state-gate.sh
hooks/test-review-convergence-gate.sh
hooks/test-discovery-gate.sh
hooks/test-execution-control.sh
hooks/test-frontend-quality-gate.sh
hooks/test-launcher-status.sh
hooks/test-lsp-diagnostics.sh
hooks/test-map-staleness-nudge.sh
hooks/test-memory-router-parity.sh
hooks/test-install-kimiflow-cli.sh
hooks/test-kimiflow-runner.sh
hooks/test-plan-blocker-gate.sh
hooks/test-project-map-status.sh
hooks/test-red-green-gate.sh
hooks/test-run-bridge.sh
hooks/test-suggest-affected-sections.sh
hooks/test-vault-mcp-open-terminal.sh
hooks/test-vault-mcp-setup.sh
hooks/test-working-tree-gate.sh
EOF
if ! "$SMOKE_ROOT/hooks/smoke-install.sh" >"$WORK/claude-smoke.log" 2>&1; then
  tail -300 "$WORK/claude-smoke.log" >&2
  exit 1
fi
if ! "$SMOKE_ROOT/hooks/smoke-install-codex.sh" >"$WORK/codex-smoke.log" 2>&1; then
  tail -300 "$WORK/codex-smoke.log" >&2
  exit 1
fi
rm "$SMOKE_ROOT/hooks/intake-gate.sh"
if "$SMOKE_ROOT/hooks/smoke-install.sh" >"$WORK/missing-runtime-smoke.log" 2>&1; then
  echo "candidate smoke accepted a missing runtime gate" >&2
  exit 1
fi
"$ROOT/hooks/build-plugin-candidate.sh" --check --output "$CANDIDATE" >/dev/null

# Candidate validity follows executable-bit classes, not checkout umask noise.
find "$CANDIDATE" -type d -exec chmod 0700 {} +
while IFS= read -r rel; do
  [ -n "$rel" ] || continue
  case "$(jq -r --arg rel "$rel" '.files[] | select(.path == $rel) | .mode' "$CANDIDATE/RUNTIME-FINGERPRINT.json")" in
    0755) chmod 0700 "$CANDIDATE/$rel" ;;
    0644) chmod 0600 "$CANDIDATE/$rel" ;;
  esac
done < <(jq -r '.files[].path' "$CANDIDATE/RUNTIME-FINGERPRINT.json")
chmod 0600 "$CANDIDATE/RUNTIME-FINGERPRINT.json"
"$ROOT/hooks/build-plugin-candidate.sh" --check --output "$CANDIDATE" >/dev/null

chmod 0700 "$CANDIDATE/RUNTIME-FINGERPRINT.json"
if "$ROOT/hooks/build-plugin-candidate.sh" --check --output "$CANDIDATE" >/dev/null 2>&1; then
  echo "candidate checker accepted executable runtime fingerprint" >&2; exit 1
fi
"$ROOT/hooks/build-plugin-candidate.sh" --write --output "$CANDIDATE" >/dev/null

unsafe="$WORK/unmanaged/kimiflow"
mkdir -p "$unsafe"
printf 'keep\n' > "$unsafe/user-data"
if "$ROOT/hooks/build-plugin-candidate.sh" --write --output "$unsafe" >/dev/null 2>&1; then
  echo "candidate builder replaced unmanaged output" >&2; exit 1
fi
[ "$(cat "$unsafe/user-data")" = "keep" ]
if "$ROOT/hooks/build-plugin-candidate.sh" --write --output "$ROOT" >/dev/null 2>&1; then
  echo "candidate builder accepted repository root" >&2; exit 1
fi

mkdir "$CANDIDATE/extra-empty"
if "$ROOT/hooks/build-plugin-candidate.sh" --check --output "$CANDIDATE" >/dev/null 2>&1; then
  echo "candidate checker ignored extra directory" >&2; exit 1
fi
rmdir "$CANDIDATE/extra-empty"

printf 'ok   clean_candidate_inventory\n'
printf 'ok   candidate_host_contracts_load\n'
printf 'ok   candidate_install_smokes_fire\n'
printf 'ok   candidate_missing_runtime_is_detected\n'
printf 'ok   candidate_fingerprint_is_reproducible\n'
printf 'ok   candidate_output_is_non_destructive\n'
printf 'ok   test_optional_pi_package_release_and_embedded_host_parity\n'
printf 'ok   test_required_pi_runtime_paths_must_be_git_indexed\n'
