#!/usr/bin/env bash
# kimiflow — unit tests for resolve-build-gate.sh (Build Preview risk policy).
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
set_state() { mkdir -p "$PROJ/.kimiflow/run"; printf 'Build risk: %s\n' "$1" > "$PROJ/.kimiflow/run/STATE.md"; }
run() { ( cd "$PROJ" && "$SCRIPT" "$@" ); }
assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1' want '$2')"; fi; }
field() { printf '%s' "$1" | cut -f"$2"; }

# default: nothing set → risk
reset
assert_eq "$(run get)" "risk" "test_default_risk"
assert_eq "$(run)" "risk" "test_default_risk_bareword"

# project policy and legacy on/off honored
reset; set_project off
assert_eq "$(run get)" "off" "test_project_off"
reset; set_project risk
assert_eq "$(run get)" "risk" "test_project_risk"
reset; set_project always
assert_eq "$(run get)" "always" "test_project_always"
reset; set_project on
assert_eq "$(run get)" "always" "test_legacy_on_maps_always"

# garbage value → default risk
reset; set_project "maybe"
assert_eq "$(run get)" "risk" "test_garbage_defaults_risk"

# set roundtrip
reset
run set off >/dev/null
if [ -f "$PROJ/.kimiflow/build-gate" ]; then pass "test_set_creates_file"; else fail "test_set_creates_file"; fi
assert_eq "$(run get)" "off" "test_set_off_roundtrip"
run set risk >/dev/null
assert_eq "$(run get)" "risk" "test_set_risk_roundtrip"
run set always >/dev/null
assert_eq "$(run get)" "always" "test_set_always_roundtrip"

# invalid set → exit 1, no file
reset
if run set nonsense >/dev/null 2>&1; then fail "test_set_invalid_rejected"; else pass "test_set_invalid_rejected"; fi
if [ -f "$PROJ/.kimiflow/build-gate" ]; then fail "test_set_invalid_nofile"; else pass "test_set_invalid_nofile"; fi

# get never persists
reset
run get >/dev/null
if [ -f "$PROJ/.kimiflow/build-gate" ]; then fail "test_get_no_persist"; else pass "test_get_no_persist"; fi

# Decision output is mechanical: low-risk default continues; risk/full/always stop.
reset
assert_eq "$(field "$(run decide --risk none --interactive yes)" 2)" "CONTINUE" "test_risk_none_continues"
assert_eq "$(field "$(run decide --risk required --interactive yes)" 2)" "STOP" "test_risk_required_stops"
assert_eq "$(field "$(run decide --risk required --interactive no)" 2)" "PARK" "test_risk_required_headless_parks"
assert_eq "$(field "$(run decide --risk none --interactive yes --alias full)" 2)" "STOP" "test_full_always_stops"
assert_eq "$(field "$(run decide --risk none --interactive no --alias full)" 2)" "PARK" "test_full_headless_parks"
reset; set_project always
assert_eq "$(field "$(run decide --risk none --interactive yes)" 2)" "STOP" "test_always_stops"
reset; set_project off
assert_eq "$(field "$(run decide --risk required --interactive yes)" 2)" "CONTINUE" "test_off_continues"
reset
assert_eq "$(field "$(run decide --risk unknown --interactive yes)" 2)" "PARK" "test_malformed_risk_parks"

# New runs bind the decision to durable STATE; mismatches and missing state fail closed.
reset; set_state none
assert_eq "$(field "$(run decide --state .kimiflow/run/STATE.md --interactive yes)" 2)" "CONTINUE" "test_state_none_continues"
reset; set_state required
assert_eq "$(field "$(run decide --state .kimiflow/run/STATE.md --interactive yes)" 2)" "STOP" "test_state_required_stops"
set_state "required paid-service"
assert_eq "$(field "$(run decide --state .kimiflow/run/STATE.md --interactive yes)" 2)" "STOP" "test_state_risk_allows_inline_reason"
assert_eq "$(field "$(run decide --state .kimiflow/run/STATE.md --risk none --interactive yes)" 2)" "PARK" "test_state_argument_mismatch_parks"
reset
assert_eq "$(field "$(run decide --state .kimiflow/run/STATE.md --interactive yes)" 2)" "PARK" "test_missing_state_parks"
mkdir -p "$PROJ/.kimiflow/run"; printf 'Status: active\n' > "$PROJ/.kimiflow/run/STATE.md"
assert_eq "$(field "$(run decide --state .kimiflow/run/STATE.md --interactive yes)" 2)" "PARK" "test_missing_state_risk_parks"
set_state invalid
assert_eq "$(field "$(run decide --state .kimiflow/run/STATE.md --interactive yes)" 2)" "PARK" "test_invalid_state_risk_parks"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
