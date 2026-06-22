#!/usr/bin/env bash
# kimiflow — unit tests for resolve-review-gate.sh. Self-contained, no framework.
# Fixtures = temp findings-dir with crafted r<N>-<lens>.md files. Run: bash hooks/test-resolve-review-gate.sh
set -u
SCRIPT="$(cd "$(dirname "$0")" && pwd)/resolve-review-gate.sh"
WORK="$(mktemp -d)"; FD="$WORK/findings"; trap 'rm -rf "$WORK"' EXIT
FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
reset() { rm -rf "$FD"; mkdir -p "$FD"; }
put()  { printf '%s\n' "$2" > "$FD/$1"; }                 # put r1-B.md "FINDING ..."
putraw(){ printf '%b' "$2" > "$FD/$1"; }                  # exact bytes (multi-line/leading space)
run()  { "$SCRIPT" "$FD" "$@"; }
# assert field <output> <fieldnum> <expected> <label>
af() { got="$(printf '%s' "$1" | cut -f"$2")"; if [ "$got" = "$3" ]; then pass "$4"; else fail "$4 (f$2='$got' want '$3')"; fi; }

# clean: all-NONE
reset; put r1-B.md "NONE"
out="$(run --round 1 --expect B)"; af "$out" 1 OPEN "clean_none_verdict"; af "$out" 3 clean "clean_none_reason"
# clean: MEDIUM/LOW only
reset; put r1-B.md "FINDING MEDIUM src/a:1 :: dup helper
FINDING LOW src/b:2 :: nit"
af "$(run --round 1 --expect B)" 1 OPEN "med_low_open"
# open: one BLOCKER / one HIGH → CLOSED open-findings
reset; put r1-B.md "FINDING BLOCKER src/a:1 :: drops data"
out="$(run --round 1 --expect B)"; af "$out" 1 CLOSED "blocker_closed"; af "$out" 2 1 "blocker_count"; af "$out" 3 open-findings "blocker_reason"
reset; put r1-B.md "FINDING HIGH src/a:1 :: missing check"
af "$(run --round 1 --expect B)" 3 open-findings "high_reason"
# incomplete: missing expected file
reset; put r1-B.md "NONE"
af "$(run --round 1 --expect A,B)" 3 incomplete "missing_file_incomplete"
# incomplete: empty file
reset; : > "$FD/r1-B.md"
af "$(run --round 1 --expect B)" 3 incomplete "empty_file_incomplete"
# malformed: bad severity / leading space / missing :: / NONE+FINDING mixed / multi-line reason
reset; put r1-B.md "FINDING CRITICAL src/a:1 :: x";             af "$(run --round 1 --expect B)" 3 malformed "mal_severity"
reset; putraw r1-B.md " FINDING HIGH src/a:1 :: x\n";           af "$(run --round 1 --expect B)" 3 malformed "mal_leadspace"
reset; put r1-B.md "FINDING HIGH src/a:1 no-delimiter";          af "$(run --round 1 --expect B)" 3 malformed "mal_nodelim"
reset; put r1-B.md "NONE
FINDING HIGH src/a:1 :: x";                                       af "$(run --round 1 --expect B)" 3 malformed "mal_none_mixed"
# misuse → fail closed
reset; put r1-B.md "NONE"
af "$(run --round x --expect B)" 1 CLOSED "misuse_round"
# language-agnostic: non-ASCII reason counts; PLAN ref valid
reset; put r1-B.md "FINDING HIGH src/app.ts:42 :: Nullzeiger-Zugriff möglich — 空ポインタ"
af "$(run --round 1 --expect B)" 2 1 "utf8_reason_counts"
reset; put r1-B.md "FINDING MEDIUM PLAN.md §Abschnitt 3 :: criterion AC-2 has no test"
af "$(run --round 1 --expect B)" 1 OPEN "planref_valid"

# oscillation: count not strictly decreasing r1->r2 (2 -> 2) → CLOSED oscillation
reset
put r1-B.md "FINDING HIGH src/a:1 :: x
FINDING HIGH src/b:2 :: y"
put r2-B.md "FINDING HIGH src/a:1 :: x
FINDING HIGH src/c:3 :: z"
af "$(run --round 2 --expect B)" 3 oscillation "osc_not_decreasing"
# progress: 2 -> 1 (strictly decreasing) → still open-findings (not oscillation)
reset
put r1-B.md "FINDING HIGH src/a:1 :: x
FINDING HIGH src/b:2 :: y"
put r2-B.md "FINDING HIGH src/a:1 :: x"
af "$(run --round 2 --expect B)" 3 open-findings "progress_decreasing"
# resolved: 1 -> 0 → OPEN clean
reset
put r1-B.md "FINDING HIGH src/a:1 :: x"
put r2-B.md "NONE"
af "$(run --round 2 --expect B)" 1 OPEN "resolved_clean"
# reappearance: count strictly decreasing (oscillation does NOT fire), but a finding present
# in r1, absent in r2, returns in r3 → CLOSED reappeared (isolates reappearance vs oscillation)
reset
put r1-B.md "FINDING HIGH src/a:1 :: x
FINDING HIGH src/b:2 :: y"
put r2-B.md "FINDING HIGH src/b:2 :: y
FINDING HIGH src/c:3 :: z"
put r3-B.md "FINDING HIGH src/a:1 :: x"
af "$(run --round 3 --expect B)" 3 reappeared "reappeared_isolated"
# cap reached with open findings → CLOSED cap-reached
reset
put r1-B.md "FINDING HIGH src/a:1 :: x"
put r2-B.md "FINDING HIGH src/a:1 :: x"
put r3-B.md "FINDING HIGH src/a:1 :: x"
put r4-B.md "FINDING HIGH src/a:1 :: x"
af "$(run --round 4 --expect B --cap 3)" 3 cap-reached "cap_reached"
# degrade safely: prior-round files absent → no false oscillation, just open-findings
reset
put r2-B.md "FINDING HIGH src/a:1 :: x"
af "$(run --round 2 --expect B)" 3 open-findings "degrade_no_prior"

echo "----"; if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
