#!/usr/bin/env bash
# Hermetic bounded Deep-Security contract coverage.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHONPATH="$ROOT/hooks" python3 -m unittest kimiflow_core.tests.test_security_deep
for schema in security-deep-plan-v1.schema.json security-deep-result-v1.schema.json security-eval-v1.schema.json security-promotion-v1.schema.json; do
  python3 -m json.tool "$ROOT/references/$schema" >/dev/null
done
printf 'ok   portable_artifact_is_allowlist_only\n'
