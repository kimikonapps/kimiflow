#!/usr/bin/env bash
# kimiflow — unit tests for resolve-build-gate.sh (the pre-build summary-gate toggle).
# Self-contained, no framework. Isolation: a NON-git temp project dir, so the real
# repo's .kimiflow/build-gate is never touched. Run: bash hooks/test-resolve-build-gate.sh
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/resolve-build-gate.sh"
WORK="$(mktemp -d)"
PROJ="$WORK/proj"          # non-git → gitroot falls back to pwd
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
reset() { rm -rf "$PROJ"; mkdir -p "$PROJ"; }
set_project() { mkdir -p "$PROJ/.kimiflow"; printf '%s\n' "$1" > "$PROJ/.kimiflow/build-gate"; }
run() { ( cd "$PROJ" && "$SCRIPT" "$@" ); }
assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1' want '$2')"; fi; }

# default: nothing set → on
reset
assert_eq "$(run get)" "on" "test_default_on"
assert_eq "$(run)" "on" "test_default_on_bareword"

# project on/off honored
reset; set_project off
assert_eq "$(run get)" "off" "test_project_off"
reset; set_project on
assert_eq "$(run get)" "on" "test_project_on"

# garbage value → default on
reset; set_project "maybe"
assert_eq "$(run get)" "on" "test_garbage_defaults_on"

# set roundtrip
reset
out="$(run set off)"
if [ -f "$PROJ/.kimiflow/build-gate" ]; then pass "test_set_creates_file"; else fail "test_set_creates_file"; fi
assert_eq "$(run get)" "off" "test_set_off_roundtrip"
run set on >/dev/null
assert_eq "$(run get)" "on" "test_set_on_roundtrip"

# invalid set → exit 1, no file
reset
if run set nonsense >/dev/null 2>&1; then fail "test_set_invalid_rejected"; else pass "test_set_invalid_rejected"; fi
if [ -f "$PROJ/.kimiflow/build-gate" ]; then fail "test_set_invalid_nofile"; else pass "test_set_invalid_nofile"; fi

# get never persists
reset
run get >/dev/null
if [ -f "$PROJ/.kimiflow/build-gate" ]; then fail "test_get_no_persist"; else pass "test_get_no_persist"; fi

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
