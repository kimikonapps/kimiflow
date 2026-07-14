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
af "$(run --round 3 --expect B --cap 5)" 3 reappeared "reappeared_isolated"
# cap reached with open findings → CLOSED cap-reached
reset
put r1-B.md "FINDING HIGH src/a:1 :: x"
put r2-B.md "FINDING HIGH src/a:1 :: x"
put r3-B.md "FINDING HIGH src/a:1 :: x"
put r4-B.md "FINDING HIGH src/a:1 :: x"
af "$(run --round 4 --expect B --cap 3)" 3 cap-reached "cap_reached"
# a clean file beyond the cap must not reopen/reset the revision ledger
reset
put r3-B.md "NONE"
af "$(run --round 3 --expect B --cap 2)" 3 cap-reached "clean_round_beyond_cap_stays_closed"
# clean at the cap is allowed: the final permitted repair resolved the blockers
reset
put r2-B.md "NONE"
af "$(run --round 2 --expect B --cap 2)" 1 OPEN "clean_at_cap_opens"
# cap reached AT the cap round (round == cap), strictly decreasing so neither oscillation
# nor reappearance fires → CLOSED cap-reached. The cap is the round LIMIT, not limit+1.
reset
put r1-B.md "FINDING HIGH src/a:1 :: x
FINDING HIGH src/b:2 :: y
FINDING HIGH src/c:3 :: z"
put r2-B.md "FINDING HIGH src/a:1 :: x
FINDING HIGH src/b:2 :: y"
put r3-B.md "FINDING HIGH src/a:1 :: x"
af "$(run --round 3 --expect B --cap 3)" 3 cap-reached "cap_reached_at_cap_round"
# degrade safely: prior-round files absent → no false oscillation, just open-findings
reset
put r2-B.md "FINDING HIGH src/a:1 :: x"
af "$(run --round 2 --expect B)" 3 open-findings "degrade_no_prior"

# cross-phase isolation (audit finding C8): Phase 4 (lenses A/B) and Phase 7 (code-verified)
# share the findings dir with overlapping round numbers. The anti-oscillation prev-round
# check MUST be scoped to the --expect lens set, else stale Phase-4 findings inflate
# prev_open and a genuine Phase-7 1->1 stagnation is mis-emitted as open-findings.
reset
put r1-A.md "FINDING HIGH plan:3 :: p"
put r1-B.md "FINDING HIGH plan:5 :: q"
put r1-code-verified.md "FINDING HIGH src/a:9 :: z"
put r2-code-verified.md "FINDING HIGH src/a:9 :: z"
af "$(run --round 2 --expect code-verified)" 3 oscillation "cross_phase_isolation_oscillation"

# zero-padded round must still emit a verdict line (fail-closed), never crash unbound
reset
put r1-B.md "FINDING HIGH src/a:1 :: x"
out="$(run --round 08 --expect B --cap 10)"; af "$out" 1 CLOSED "zeropad_round_has_verdict"

# strategy epochs preserve the global ledger but do not compare the first epoch round with the
# failed strategy immediately before it.
reset
put r2-B.md "FINDING HIGH src/old:1 :: old strategy"
put r3-B.md "FINDING HIGH src/new:1 :: new strategy"
af "$(run --round 3 --expect B --epoch-start 3 --cap 4)" 3 open-findings "epoch_first_round_skips_previous_strategy"

# anti-oscillation still applies after the first round inside the new epoch.
reset
put r3-B.md "FINDING HIGH src/a:1 :: x"
put r4-B.md "FINDING HIGH src/b:2 :: y"
af "$(run --round 4 --expect B --epoch-start 3 --cap 5)" 3 oscillation "epoch_internal_oscillation"

# a finding from an older failed epoch is not a reappearance in the current strategy epoch.
reset
put r1-B.md "FINDING HIGH src/a:1 :: old"
put r3-B.md "FINDING HIGH src/c:3 :: c
FINDING HIGH src/d:4 :: d"
put r4-B.md "FINDING HIGH src/a:1 :: new epoch"
af "$(run --round 4 --expect B --epoch-start 3 --cap 5)" 3 open-findings "epoch_reappearance_ignores_older_epochs"

# a disappeared finding that returns inside the current epoch is still rejected.
reset
put r3-B.md "FINDING HIGH src/a:1 :: a
FINDING HIGH src/b:2 :: b
FINDING HIGH src/c:3 :: c"
put r4-B.md "FINDING HIGH src/b:2 :: b
FINDING HIGH src/c:3 :: c"
put r5-B.md "FINDING HIGH src/a:1 :: a"
af "$(run --round 5 --expect B --epoch-start 3 --cap 6)" 3 reappeared "epoch_internal_reappearance"

# clean at an epoch cap opens, while a later round stays closed.
reset
put r4-B.md "NONE"
af "$(run --round 4 --expect B --epoch-start 3 --cap 4)" 1 OPEN "epoch_clean_at_cap_opens"
put r5-B.md "NONE"
af "$(run --round 5 --expect B --epoch-start 3 --cap 4)" 3 cap-reached "epoch_clean_beyond_cap_stays_closed"

# explicit epoch bounds are fail-closed; legacy calls without the option retain their old range.
reset
put r1-B.md "NONE"
af "$(run --round 1 --expect B --epoch-start x --cap 2)" 3 malformed "epoch_nonnumeric_malformed"
af "$(run --round 1 --expect B --epoch-start 0 --cap 2)" 3 malformed "epoch_zero_malformed"
af "$(run --round 1 --expect B --epoch-start 2 --cap 3)" 3 malformed "epoch_after_round_malformed"
af "$(run --round 2 --expect B --epoch-start 2 --cap 1)" 3 malformed "epoch_after_cap_malformed"

echo "----"; if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
