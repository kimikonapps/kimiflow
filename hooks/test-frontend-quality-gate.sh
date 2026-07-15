#!/usr/bin/env bash
# Regression tests for the token-efficient frontend quality gate.
set -u

ROOT="$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)"
GATE="$ROOT/hooks/frontend-quality-gate.sh"
PASS=0
FAIL=0
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kimiflow-frontend-quality.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT HUP INT TERM

pass() { PASS=$((PASS + 1)); printf 'ok %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf 'not ok %s\n' "$1" >&2; }

assert_has() {
  case "$1" in
    *"$2"*) pass "$3" ;;
    *) fail "$3 (wanted $2, got $1)" ;;
  esac
}

replace_line() {
  python3 - "$1" "$2" "$3" <<'PY'
import os, sys, tempfile
path, key, value = sys.argv[1:]
text = open(path, encoding="utf-8").read()
if text.endswith("\n"): text = text[:-1]
lines = text.split("\n")
matches = [i for i, line in enumerate(lines) if line.startswith(key + ":")]
if len(matches) != 1:
    raise SystemExit("expected one %s, got %d" % (key, len(matches)))
lines[matches[0]] = "%s: %s" % (key, value)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
os.replace(tmp, path)
PY
}

set_affected() {
  python3 - "$1" "${@:2}" <<'PY'
import os, sys, tempfile
path, paths = sys.argv[1], sys.argv[2:]
text = open(path, encoding="utf-8").read()
if text.endswith("\n"): text = text[:-1]
lines = text.split("\n")
start = [i for i, line in enumerate(lines) if line == "Affected files:"]
if len(start) != 1:
    raise SystemExit("affected header")
i = start[0] + 1
while i < len(lines) and lines[i].startswith("- "):
    del lines[i]
for item in reversed(paths):
    lines.insert(i, "- " + item)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state.")
with os.fdopen(fd, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
os.replace(tmp, path)
PY
}

write_png() {
  python3 - "$1" "$2" "$3" "${4:-18}" <<'PY'
import binascii, os, struct, sys, time, zlib
path, width, height, red = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)
row = bytes([0]) + bytes([red, 52, 86, 255]) * width
raw = row * height
png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(raw))
png += chunk(b"IEND", b"")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "wb") as handle:
    handle.write(png)
future = time.time_ns() + 2_000_000_000
os.utime(path, ns=(future, future))
PY
}

write_qa() {
  run="$1" lane="$2" result="$3" history="$4" strategy="$5" image="$6" p1="${7:-0}"
  {
    printf 'Lane: %s\n' "$lane"
    printf 'Source truth: project-system:src/ui/App.tsx\n'
    printf 'Implementation evidence: screenshot:evidence/%s\n' "$image"
    printf 'Viewport: 2x2\n'
    printf 'State: primary feature state\n'
    printf 'Strategy: %s\n' "$strategy"
    printf 'Deterministic checks: passed\n'
    printf 'Comparison history: %s\n' "$history"
    printf 'Open P0: 0\n'
    printf 'Open P1: %s\n' "$p1"
    printf 'Open P2: 0\n'
    printf 'Open P3: 0\n'
    printf 'Final result: %s\n' "$result"
  } > "$run/DESIGN-QA.md"
}

new_repo() {
  name="$1"
  repo="$WORK/$name"
  mkdir -p "$repo/.kimiflow/session" "$repo/.kimiflow/run"
  git -C "$repo" init -q
  git -C "$repo" config user.email test@example.com
  git -C "$repo" config user.name Test
  printf 'base\n' > "$repo/base.txt"
  git -C "$repo" add base.txt
  git -C "$repo" commit -qm base
  printf '%s\n' "$repo"
}

write_active() {
  repo="$1" run_rel="${2:-.kimiflow/run}" marker="${3:-absent}"
  python3 - "$repo/.kimiflow/session/ACTIVE_RUN.json" "$run_rel" "$(git -C "$repo" rev-parse HEAD)" "$marker" <<'PY'
import json, sys
path, run, head, marker = sys.argv[1:]
obj = {"schema_version": 1, "status": "active", "run": run, "started_head": head, "host": "codex", "mode": "feature"}
if marker != "absent": obj["frontend_quality_contract"] = int(marker)
open(path, "w", encoding="utf-8").write(json.dumps(obj) + "\n")
PY
}

write_state() {
  repo="$1" mode="${2:-feature}"
  {
    printf 'Feature: frontend gate fixture\n'
    printf 'Slug: run\n'
    printf 'Mode: %s\n' "$mode"
    printf 'Flow schema: 3\n'
    printf 'Frontend quality contract: 1\n'
    printf 'Frontend quality: off\n'
    printf 'Frontend quality routing: provisional\n'
    printf 'Frontend quality evidence: pending\n'
    printf 'Frontend quality basis: pending\n'
    printf 'Frontend quality start: pending\n'
    printf 'Frontend quality recovery: clean\n'
    printf 'Frontend quality recovery owns global: no\n'
    printf 'Recovery: clean\n'
    printf 'Affected files:\n'
  } > "$repo/.kimiflow/run/STATE.md"
  case "$mode" in
    feature) printf 'Add a backend capability.\n' > "$repo/.kimiflow/run/INTENT.md" ;;
    fix) printf 'Fix a displaced button.\n' > "$repo/.kimiflow/run/PROBLEM.md" ;;
    audit) printf 'Audit the target.\n' > "$repo/.kimiflow/run/AUDIT-INTENT.md" ;;
  esac
}

record_start() {
  "$GATE" "$1/.kimiflow/run" --record-start --write
}

record_routing() {
  "$GATE" "$1/.kimiflow/run" --record-routing --write
}

test_frontend_quality_contract_routing() {
  repo="$(new_repo routing)"
  write_active "$repo"
  write_state "$repo"
  out="$(record_start "$repo")"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tOPEN' "record_start_opens"
  assert_has "$(cat "$repo/.kimiflow/session/ACTIVE_RUN.json")" '"frontend_quality_contract":1' "record_start_marks_active_run"
  mkdir -p "$repo/src"
  printf 'value = 1\n' > "$repo/src/server.py"
  set_affected "$repo/.kimiflow/run/STATE.md" src/server.py
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality evidence" "ui-surface=no; ref=request:INTENT.md"
  out="$(record_routing "$repo")"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tOPEN' "backend_route_records"
  assert_has "$("$GATE" "$repo/.kimiflow/run")" $'FRONTEND_QUALITY_GATE\tOPEN' "backend_off_opens"

  mkdir -p "$repo/src/ui"
  printf 'export default 1\n' > "$repo/src/ui/App.tsx"
  set_affected "$repo/.kimiflow/run/STATE.md" src/server.py src/ui/App.tsx
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality routing" "provisional"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality basis" "pending"
  out="$(record_routing "$repo")"
  assert_has "$out" 'lane_route_mismatch' "ui_feature_cannot_record_off"

  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality" "standard"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality evidence" "ui-surface=yes; ref=path:src/ui/App.tsx"
  out="$(record_routing "$repo")"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tOPEN' "ui_standard_records"
}

prepare_off_delta() {
  repo="$1"
  write_active "$repo"
  write_state "$repo"
  record_start "$repo" >/dev/null
}

finish_off_delta() {
  repo="$1"
  shift
  set_affected "$repo/.kimiflow/run/STATE.md" "$@"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality evidence" "ui-surface=no; ref=request:INTENT.md"
  record_routing "$repo"
}

test_canonical_git_delta_sources() {
  repo="$(new_repo delta-committed)"; prepare_off_delta "$repo"
  printf 'committed\n' >> "$repo/base.txt"; git -C "$repo" add base.txt; git -C "$repo" commit -qm committed
  assert_has "$(finish_off_delta "$repo" base.txt)" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_committed"

  repo="$(new_repo delta-staged)"; prepare_off_delta "$repo"
  printf 'staged\n' >> "$repo/base.txt"; git -C "$repo" add base.txt
  assert_has "$(finish_off_delta "$repo" base.txt)" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_staged"

  repo="$(new_repo delta-unstaged)"; prepare_off_delta "$repo"
  printf 'unstaged\n' >> "$repo/base.txt"
  assert_has "$(finish_off_delta "$repo" base.txt)" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_unstaged"

  repo="$(new_repo delta-untracked)"; prepare_off_delta "$repo"
  printf 'untracked\n' > "$repo/new.txt"
  assert_has "$(finish_off_delta "$repo" new.txt)" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_untracked"

  repo="$(new_repo delta-delete)"; prepare_off_delta "$repo"
  rm "$repo/base.txt"
  assert_has "$(finish_off_delta "$repo" base.txt)" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_delete"

  repo="$(new_repo delta-rename)"; prepare_off_delta "$repo"
  git -C "$repo" mv base.txt renamed.txt
  assert_has "$(finish_off_delta "$repo" base.txt renamed.txt)" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_rename_both_sides"

  repo="$(new_repo delta-colon-name)"; prepare_off_delta "$repo"
  printf 'colon\n' > "$repo/Mode: feature"
  assert_has "$(finish_off_delta "$repo" 'Mode: feature')" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_colon_name_is_not_state"

  repo="$(new_repo delta-routing-colon)"; prepare_off_delta "$repo"
  printf 'colon\n' > "$repo/Frontend quality routing: final"
  assert_has "$(finish_off_delta "$repo" 'Frontend quality routing: final')" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_colon_name_does_not_block_state_write"

  repo="$(new_repo delta-header-name)"; prepare_off_delta "$repo"
  printf 'header\n' > "$repo/Affected files:"
  assert_has "$(finish_off_delta "$repo" 'Affected files:')" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_header_name_stays_opaque"

  repo="$(new_repo delta-whitespace-name)"; prepare_off_delta "$repo"
  printf 'leading\n' > "$repo/ leading.txt"
  assert_has "$(finish_off_delta "$repo" ' leading.txt')" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_leading_space_is_preserved"

  repo="$(new_repo delta-trailing-name)"; prepare_off_delta "$repo"
  printf 'trailing\n' > "$repo/Frontend quality routing: final "
  assert_has "$(finish_off_delta "$repo" 'Frontend quality routing: final ')" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_trailing_space_is_preserved"

  repo="$(new_repo delta-unicode-separator)"; prepare_off_delta "$repo"
  separator_name="$(python3 -c 'print("line\u2028separator.txt", end="")')"
  printf 'separator\n' > "$repo/$separator_name"
  assert_has "$(finish_off_delta "$repo" "$separator_name")" $'FRONTEND_QUALITY_GATE\tOPEN' "delta_unicode_separator_is_preserved"
}

test_bugfix_and_flagship_routing() {
  repo="$(new_repo fix-ui)"
  write_active "$repo"
  write_state "$repo" fix
  record_start "$repo" >/dev/null
  mkdir -p "$repo/src/ui"; printf 'button\n' > "$repo/src/ui/Button.tsx"
  set_affected "$repo/.kimiflow/run/STATE.md" src/ui/Button.tsx
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality evidence" "ui-surface=excluded-by-mode; ref=request:PROBLEM.md"
  assert_has "$(record_routing "$repo")" $'FRONTEND_QUALITY_GATE\tOPEN' "visual_bugfix_stays_off"

  repo="$(new_repo flagship-route)"
  write_active "$repo"
  write_state "$repo"
  printf 'Polish the UI into a release-quality visual refresh.\n' > "$repo/.kimiflow/run/INTENT.md"
  record_start "$repo" >/dev/null
  mkdir -p "$repo/src/ui"; printf 'screen\n' > "$repo/src/ui/App.tsx"
  set_affected "$repo/.kimiflow/run/STATE.md" src/ui/App.tsx
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality" "standard"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality evidence" "ui-surface=yes; ref=path:src/ui/App.tsx"
  assert_has "$(record_routing "$repo")" 'flagship_route_mismatch' "polish_requires_flagship"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality" "flagship"
  assert_has "$(record_routing "$repo")" $'FRONTEND_QUALITY_GATE\tOPEN' "flagship_route_records"
}

test_off_and_legacy_open_without_artifact() {
  repo="$(new_repo legacy)"
  write_active "$repo"
  printf 'Mode: feature\nAffected files:\n' > "$repo/.kimiflow/run/STATE.md"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" $'OPEN\tblockers=0\treason=not-required' "legacy_opens"
  write_active "$repo" .kimiflow/run 1
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'contract_missing' "marker_prevents_legacy_downgrade"
}

test_declared_lane_missing_or_invalid_closes() {
  repo="$(new_repo invalid)"
  write_active "$repo"
  write_state "$repo"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'active_contract_marker_missing' "partial_contract_closes"
  write_active "$repo" .kimiflow/run 1
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'start_not_recorded' "pending_start_closes"
  printf 'Frontend quality: flagship\n' >> "$repo/.kimiflow/run/STATE.md"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'duplicate_lane' "duplicate_lane_closes"

  repo="$(new_repo dirty-start)"
  write_active "$repo"
  write_state "$repo"
  printf 'dirty\n' >> "$repo/base.txt"
  out="$(record_start "$repo")"
  assert_has "$out" 'dirty_start' "dirty_start_closes"

  repo="$(new_repo noncanonical-start)"
  write_active "$repo"
  write_state "$repo"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality routing" "final"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality evidence" "ui-surface=no; ref=request:INTENT.md"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality basis" "not-a-basis"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery" "active"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery owns global" "yes"
  replace_line "$repo/.kimiflow/run/STATE.md" "Recovery" "active"
  out="$(record_start "$repo")"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "noncanonical_start_contract_closes"
}

test_partial_active_marker_closes() {
  repo="$(new_repo null-marker)"
  write_active "$repo"
  printf 'Mode: feature\nAffected files:\n' > "$repo/.kimiflow/run/STATE.md"
  python3 - "$repo/.kimiflow/session/ACTIVE_RUN.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
data["frontend_quality_contract"] = None
open(path, "w", encoding="utf-8").write(json.dumps(data) + "\n")
PY
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "null_marker_cannot_open_legacy"

  printf '{"frontend_quality_contract":' > "$repo/.kimiflow/session/ACTIVE_RUN.json"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "invalid_active_run_cannot_open_legacy"

  rm "$repo/.kimiflow/session/ACTIVE_RUN.json"
  mkdir "$repo/.kimiflow/session/ACTIVE_RUN.json"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "nonregular_active_run_cannot_open_legacy"
}

prepare_standard() {
  repo="$1"
  write_active "$repo"
  write_state "$repo"
  record_start "$repo" >/dev/null
  mkdir -p "$repo/src/ui"
  printf 'export default 1\n' > "$repo/src/ui/App.tsx"
  set_affected "$repo/.kimiflow/run/STATE.md" src/ui/App.tsx
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality" "standard"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality evidence" "ui-surface=yes; ref=path:src/ui/App.tsx"
  record_routing "$repo" >/dev/null
  write_png "$repo/.kimiflow/run/evidence/final.png" 2 2 18
  write_qa "$repo/.kimiflow/run" standard passed initial-capture hierarchy-reset final.png
}

test_standard_and_flagship_pass_contract() {
  repo="$(new_repo standard)"
  prepare_standard "$repo"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tOPEN' "standard_valid_evidence_opens"
  write_png "$repo/.kimiflow/run/evidence/wrong.png" 1 1 18
  replace_line "$repo/.kimiflow/run/DESIGN-QA.md" "Implementation evidence" "screenshot:evidence/wrong.png"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'png_viewport_mismatch' "viewport_mismatch_closes"
  replace_line "$repo/.kimiflow/run/DESIGN-QA.md" "Implementation evidence" "screenshot:evidence/final.png"
  printf 'Basis: deadbeef\n' >> "$repo/.kimiflow/run/FRONTEND-ROUTING-RECEIPT"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'routing_receipt_invalid' "duplicate_receipt_field_closes"

  repo="$(new_repo png-profile)"
  prepare_standard "$repo"
  python3 - "$repo/.kimiflow/run/evidence/final.png" <<'PY'
import sys
path = sys.argv[1]
data = bytearray(open(path, "rb").read())
data[-5] ^= 1
open(path, "wb").write(data)
PY
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'png_crc_invalid' "bad_png_crc_closes"

  rm "$repo/.kimiflow/run/evidence/final.png"
  ln -s "$repo/base.txt" "$repo/.kimiflow/run/evidence/final.png"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'evidence_symlink' "evidence_symlink_closes"

  repo="$(new_repo missing-route-receipt)"
  prepare_standard "$repo"
  rm "$repo/.kimiflow/run/FRONTEND-ROUTING-RECEIPT"
  out="$("$GATE" "$repo/.kimiflow/run" --write 2>&1)"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "missing_route_receipt_stays_structured"
  assert_has "$(grep '^Frontend quality recovery:' "$repo/.kimiflow/run/STATE.md")" 'active' "missing_route_receipt_starts_recovery"

  repo="$(new_repo affected-symlink)"
  prepare_standard "$repo"
  rm "$repo/src/ui/App.tsx"
  ln -s ../../base.txt "$repo/src/ui/App.tsx"
  python3 - "$repo/.kimiflow/run/evidence/final.png" "$repo/src/ui/App.tsx" <<'PY'
import os, sys
image, link = sys.argv[1:]
newer = os.stat(image).st_mtime_ns + 1_000_000_000
os.utime(link, ns=(newer, newer), follow_symlinks=False)
PY
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'screenshot_stale' "affected_symlink_retarget_stales_capture"

  repo="$(new_repo oversized-viewport)"
  prepare_standard "$repo"
  huge_viewport="$(python3 -c 'print("9" * 5000 + "x2")')"
  replace_line "$repo/.kimiflow/run/DESIGN-QA.md" "Viewport" "$huge_viewport"
  out="$("$GATE" "$repo/.kimiflow/run" 2>&1)"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "oversized_viewport_stays_structured"

  repo="$(new_repo recovery-symlink)"
  prepare_standard "$repo"
  ln -s ../../base.txt "$repo/.kimiflow/run/FRONTEND-QUALITY-RECOVERY"
  out="$("$GATE" "$repo/.kimiflow/run")"
  assert_has "$out" 'recovery_receipt_invalid' "recovery_symlink_cannot_look_absent"
}

test_frontend_quality_autonomous_recovery_contract() {
  repo="$(new_repo recovery)"
  prepare_standard "$repo"
  write_qa "$repo/.kimiflow/run" standard blocked initial-capture hierarchy-reset final.png 1
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "visual_failure_closes"
  assert_has "$(cat "$repo/.kimiflow/run/FRONTEND-QUALITY-RECOVERY")" 'Kind: visual' "visual_receipt_written"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery" "clean"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery owns global" "no"
  replace_line "$repo/.kimiflow/run/STATE.md" "Recovery" "clean"
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" 'recovery_transition_incomplete' "closed_receipt_crash_is_detected"
  assert_has "$(grep '^Frontend quality recovery:' "$repo/.kimiflow/run/STATE.md")" 'active' "closed_receipt_crash_resumes_state"
  write_qa "$repo/.kimiflow/run" standard passed fix-capture-compare spacing-reframe final.png
  write_png "$repo/.kimiflow/run/evidence/final.png" 2 2 18
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" 'pixel_not_changed' "renamed_strategy_same_pixels_stays_closed"
  replace_line "$repo/.kimiflow/run/DESIGN-QA.md" "Strategy" "composition-reframe"
  write_png "$repo/.kimiflow/run/evidence/final.png" 2 2 99
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tOPEN' "changed_strategy_and_pixels_open"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery" "active"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery owns global" "yes"
  replace_line "$repo/.kimiflow/run/STATE.md" "Recovery" "active"
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tOPEN' "resolved_receipt_crash_resumes_open"
  assert_has "$(grep '^Frontend quality recovery:' "$repo/.kimiflow/run/STATE.md")" 'clean' "resolved_receipt_crash_cleans_state"
  assert_has "$("$GATE" "$repo/.kimiflow/run" --write)" $'FRONTEND_QUALITY_GATE\tOPEN' "resolved_open_is_idempotent"

  repo="$(new_repo foreign-recovery)"
  prepare_standard "$repo"
  replace_line "$repo/.kimiflow/run/STATE.md" "Recovery" "active"
  write_qa "$repo/.kimiflow/run" standard blocked initial-capture hierarchy-reset final.png 1
  "$GATE" "$repo/.kimiflow/run" --write >/dev/null
  assert_has "$(grep '^Frontend quality recovery owns global:' "$repo/.kimiflow/run/STATE.md")" 'no' "foreign_recovery_not_owned"
  write_qa "$repo/.kimiflow/run" standard passed fix-capture-compare spacing-reframe final.png
  write_png "$repo/.kimiflow/run/evidence/final.png" 2 2 77
  "$GATE" "$repo/.kimiflow/run" --write >/dev/null
  assert_has "$(grep '^Recovery:' "$repo/.kimiflow/run/STATE.md")" 'active' "frontend_open_preserves_foreign_recovery"

  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery" "clean"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery owns global" "yes"
  before="$(shasum -a 256 "$repo/.kimiflow/run/STATE.md")"
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  after="$(shasum -a 256 "$repo/.kimiflow/run/STATE.md")"
  assert_has "$out" 'recovery_state_invalid' "clean_yes_is_invalid"
  if [ "$before" = "$after" ]; then pass clean_yes_does_not_mutate; else fail clean_yes_does_not_mutate; fi
}

test_visual_recovery_identity_is_immutable() {
  repo="$(new_repo recovery-identity)"
  prepare_standard "$repo"
  write_qa "$repo/.kimiflow/run" standard blocked initial-capture hierarchy-reset final.png 1
  "$GATE" "$repo/.kimiflow/run" --write >/dev/null
  write_qa "$repo/.kimiflow/run" standard passed fix-capture-compare identity-rebase-one final.png
  replace_line "$repo/.kimiflow/run/DESIGN-QA.md" "Source truth" "visual-reference:replacement.png"
  write_png "$repo/.kimiflow/run/evidence/final.png" 2 2 55
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" 'source_truth_changed' "visual_recovery_rejects_identity_change"
  replace_line "$repo/.kimiflow/run/DESIGN-QA.md" "Strategy" "identity-rebase-two"
  write_png "$repo/.kimiflow/run/evidence/final.png" 2 2 99
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" 'source_truth_changed' "visual_recovery_does_not_rebase_identity"

  repo="$(new_repo recovery-missing-evidence)"
  prepare_standard "$repo"
  write_qa "$repo/.kimiflow/run" standard blocked initial-capture hierarchy-reset final.png 1
  "$GATE" "$repo/.kimiflow/run" --write >/dev/null
  rm "$repo/.kimiflow/run/evidence/final.png"
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" 'evidence_missing' "visual_recovery_rejects_missing_evidence"
  assert_has "$(cat "$repo/.kimiflow/run/FRONTEND-QUALITY-RECOVERY")" 'Kind: visual' "visual_recovery_kind_is_preserved"
  write_png "$repo/.kimiflow/run/evidence/final.png" 2 2 18
  write_qa "$repo/.kimiflow/run" standard passed fix-capture-compare hierarchy-reset final.png
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "visual_recovery_cannot_open_without_changed_output"

  repo="$(new_repo malformed-visual-recovery)"
  prepare_standard "$repo"
  write_qa "$repo/.kimiflow/run" standard blocked initial-capture hierarchy-reset final.png 1
  "$GATE" "$repo/.kimiflow/run" --write >/dev/null
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery" "clean"
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality recovery owns global" "no"
  replace_line "$repo/.kimiflow/run/STATE.md" "Recovery" "clean"
  replace_line "$repo/.kimiflow/run/FRONTEND-QUALITY-RECOVERY" "Lane" "broken"
  write_qa "$repo/.kimiflow/run" standard passed fix-capture-compare hierarchy-reset final.png
  write_png "$repo/.kimiflow/run/evidence/final.png" 2 2 18
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" 'recovery_receipt_invalid' "malformed_visual_recovery_closes"
  assert_has "$(cat "$repo/.kimiflow/run/FRONTEND-QUALITY-RECOVERY")" 'Lane: broken' "malformed_visual_recovery_is_not_overwritten"
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tCLOSED' "malformed_visual_recovery_cannot_downgrade_open"
}

test_off_contract_recovery_without_capture() {
  repo="$(new_repo off-recovery)"
  prepare_off_delta "$repo"
  printf 'backend\n' > "$repo/server.py"
  set_affected "$repo/.kimiflow/run/STATE.md" server.py
  replace_line "$repo/.kimiflow/run/STATE.md" "Frontend quality evidence" "ui-surface=no; ref=request:INTENT.md"
  record_routing "$repo" >/dev/null
  printf 'Add a revised backend capability.\n' > "$repo/.kimiflow/run/INTENT.md"
  out="$("$GATE" "$repo/.kimiflow/run" --write)"
  assert_has "$out" 'routing_basis_stale' "off_stale_contract_closes"
  assert_has "$(cat "$repo/.kimiflow/run/FRONTEND-QUALITY-RECOVERY")" 'Kind: contract' "off_uses_contract_recovery"
  huge_attempt="$(python3 -c 'print("9" * 5000)')"
  replace_line "$repo/.kimiflow/run/FRONTEND-QUALITY-RECOVERY" "Attempt" "$huge_attempt"
  record_routing "$repo" >/dev/null
  out="$("$GATE" "$repo/.kimiflow/run" --write 2>&1)"
  assert_has "$out" $'FRONTEND_QUALITY_GATE\tOPEN' "off_contract_repair_opens_without_capture"
  assert_has "$(grep '^Attempt:' "$repo/.kimiflow/run/FRONTEND-QUALITY-RECOVERY")" "$huge_attempt" "large_attempt_stays_textual"
  if [ ! -e "$repo/.kimiflow/run/DESIGN-QA.md" ]; then pass off_recovery_creates_no_qa; else fail off_recovery_creates_no_qa; fi
}

test_frontend_quality_lazy_loading() {
  for file in frontend-quality-standard.md frontend-quality-flagship.md frontend-quality-qa.md; do
    [ -s "$ROOT/references/$file" ] || { fail "lazy_reference_$file"; continue; }
    pass "lazy_reference_$file"
  done
  if ! grep -q 'frontend-quality-standard.md' "$ROOT/phases/phase-2-understand.md"; then fail phase2_lazy_standard; else pass phase2_lazy_standard; fi
  if ! grep -q 'frontend-quality-qa.md' "$ROOT/phases/phase-6-verify.md"; then fail phase6_lazy_qa; else pass phase6_lazy_qa; fi
  dollar='$'
  rooted_references="${dollar}{CLAUDE_PLUGIN_ROOT:-${dollar}CLAUDE_SKILL_DIR}/references/frontend-quality"
  if grep -Fq "$rooted_references-standard.md" "$ROOT/phases/phase-2-understand.md"; then pass phase2_rooted_standard; else fail phase2_rooted_standard; fi
  if grep -Fq "$rooted_references-flagship.md" "$ROOT/phases/phase-2-understand.md"; then pass phase2_rooted_flagship; else fail phase2_rooted_flagship; fi
  if grep -Fq "$rooted_references-qa.md" "$ROOT/phases/phase-6-verify.md"; then pass phase6_rooted_qa; else fail phase6_rooted_qa; fi
  if grep -q 'Geometric integrity' "$ROOT/reference.md" 2>/dev/null; then fail no_global_design_payload; else pass no_global_design_payload; fi
}

test_frontend_quality_model_first_contract() {
  file="$ROOT/references/frontend-quality-standard.md"
  for term in 'existing' 'hierarchy' 'alignment' 'rhythm' 'responsive' 'motion'; do
    if grep -Eiq "$term" "$file"; then pass "model_first_$term"; else fail "model_first_$term"; fi
  done
}

test_frontend_quality_release_regression() {
  dollar='$'
  rooted_helper="${dollar}{CLAUDE_PLUGIN_ROOT:-${dollar}CLAUDE_SKILL_DIR}/hooks/frontend-quality-gate.sh"
  if [ "$(wc -c < "$ROOT/SKILL.md" | tr -d ' ')" -le 17000 ]; then pass root_skill_budget; else fail root_skill_budget; fi
  if [ "$(wc -c < "$ROOT/skills/kimiflow/SKILL.md" | tr -d ' ')" -le 15000 ]; then pass codex_skill_budget; else fail codex_skill_budget; fi
  if [ "$(wc -c < "$ROOT/references/frontend-quality-standard.md" | tr -d ' ')" -le 5000 ]; then pass standard_budget; else fail standard_budget; fi
  if [ "$(wc -c < "$ROOT/references/frontend-quality-flagship.md" | tr -d ' ')" -le 2500 ]; then pass flagship_budget; else fail flagship_budget; fi
  if [ "$(wc -c < "$ROOT/references/frontend-quality-qa.md" | tr -d ' ')" -le 5000 ]; then pass qa_budget; else fail qa_budget; fi
  if grep -q 'Frontend quality recovery: clean' "$ROOT/phases/phase-7-review-commit.md"; then pass phase7_barrier; else fail phase7_barrier; fi
  for phase in phase-0-setup.md phase-6-verify.md phase-7-review-commit.md; do
    if grep -Fq "$rooted_helper" "$ROOT/phases/$phase"; then
      pass "rooted_helper_$phase"
    else
      fail "rooted_helper_$phase"
    fi
  done
  if bash -n "$GATE"; then pass gate_syntax; else fail gate_syntax; fi
}

if [ ! -x "$GATE" ]; then
  printf 'not ok frontend gate helper missing or not executable: %s\n' "$GATE" >&2
  exit 1
fi

test_frontend_quality_contract_routing
test_canonical_git_delta_sources
test_bugfix_and_flagship_routing
test_off_and_legacy_open_without_artifact
test_declared_lane_missing_or_invalid_closes
test_partial_active_marker_closes
test_standard_and_flagship_pass_contract
test_frontend_quality_autonomous_recovery_contract
test_visual_recovery_identity_is_immutable
test_off_contract_recovery_without_capture
test_frontend_quality_lazy_loading
test_frontend_quality_model_first_contract
test_frontend_quality_release_regression

printf '%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
