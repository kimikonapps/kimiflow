#!/usr/bin/env bash
# kimiflow — focused project release-profile tests.
set -u
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$DIR"
python3 -m unittest \
  kimiflow_core.tests.test_release_profile \
  kimiflow_core.tests.test_release_memory || exit 1
ROOT="$(CDPATH= cd -- "$DIR/.." && pwd -P)"
cmp "$ROOT/hooks/release-profile.sh" "$ROOT/plugins/kimiflow/hooks/release-profile.sh" \
  && cmp "$ROOT/hooks/kimiflow_core/release_profile.py" "$ROOT/plugins/kimiflow/hooks/kimiflow_core/release_profile.py" \
  && cmp "$ROOT/hooks/kimiflow_core/release_memory.py" "$ROOT/plugins/kimiflow/hooks/kimiflow_core/release_memory.py" \
  && cmp "$ROOT/references/release-profile-v1.schema.json" "$ROOT/plugins/kimiflow/references/release-profile-v1.schema.json" \
  && cmp "$ROOT/references/release-profile-v2.schema.json" "$ROOT/plugins/kimiflow/references/release-profile-v2.schema.json" \
  && test -x "$ROOT/plugins/kimiflow/hooks/release-profile.sh" \
  && echo "release profile runtime parity: PASS"
