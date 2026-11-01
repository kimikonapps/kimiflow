#!/bin/sh
# Runner skeleton: one script for every build and test run. Contract and reasons: the installed
# references/test-economy.md § Runner contract. Set the project lines below; keep the rest.
#
# Usage:  sh <main checkout>/.kimiflow/ops/tools/run.sh <logname> build
#         TESTS_OK=1 sh <main checkout>/.kimiflow/ops/tools/run.sh <logname> test [test ids...]
#         Commands run in the current directory (checkout or worktree); logs and locks stay next to
#         the kit in the main checkout, so every worktree shares the same lanes and test lock.
# Log:    $RUN_DIR/<logname>.log, last line "EXIT <code> after <n>s". Wait for that line (capped).
# Stop:   kill the process group: kill -TERM -<pgid>  (pgid from: ps -axo pid,pgid,command)

# --- project lines -------------------------------------------------------------------------------
BUILD_CMD='<build command>'          # compile only, the tests included
TEST_CMD='<test command> "$@"'       # runs on BUILD_CMD's output without rebuilding where the stack
                                     # allows; "$@" = the test ids given to run.sh; none = the whole suite
BUILD_DIR='<build output directory>' # relative paths resolve against the current checkout or worktree
reds() { :; }                        # print the red test ids found in log "$1", one per line
# -------------------------------------------------------------------------------------------------

RUN_DIR=${RUN_DIR:-$(dirname "$0")/../../runs}   # .kimiflow/runs: local and untracked like the kit
LANES=${LANES:-2}                    # builds at once on this machine
WAIT_MAX=${WAIT_MAX:-21600}          # seconds to wait for a lane or the test lock
SMALL_MAX=${SMALL_MAX:-0}            # test runs of 1..SMALL_MAX ids may take the small test lock beside
                                     # the big run (then RUN_SMALL=1: run them on one host); 0 = off

[ $# -ge 2 ] || { echo "usage: run.sh <logname> build|test [test ids...]" >&2; exit 64; }
NAME=$1 MODE=$2; shift 2
mkdir -p "$RUN_DIR/state"
LOG=$RUN_DIR/$NAME.log STATE=$RUN_DIR/state/$NAME.json T0=$(date +%s) HELD=
: > "$LOG"; rm -f "$LOG.rerun"

finish() { echo "EXIT $1 after $(( $(date +%s) - T0 ))s" >> "$LOG"; exit "$1"; }
json() { printf %s "$1" | sed 's/[\\"]/\\&/g'; }  # escape \ and " for a JSON string
phase() { printf '{"name":"%s","mode":"%s","phase":"%s","since":%s,"pid":%s,"dir":"%s"}\n' \
    "$(json "$NAME")" "$MODE" "$1" "$(date +%s)" $$ "$(json "$PWD")" > "$STATE"; }
cleanup() { for lock in $HELD; do rm -rf "$RUN_DIR/$lock"; done; rm -f "$STATE"; }
trap cleanup EXIT
trap 'finish 143' INT TERM

case $MODE in
    build) ;;
    test) [ -n "${TESTS_OK:-}" ] || { echo "REFUSED: test mode needs TESTS_OK=1 (planner or fix round)." \
            "Workers run: run.sh <name> build" >> "$LOG"; finish 64; } ;;
    *) echo "unknown mode: $MODE" >> "$LOG"; finish 64 ;;
esac
for line in "$BUILD_CMD" "$TEST_CMD" "$BUILD_DIR"; do
    case $line in '<'*) echo "Set BUILD_CMD, TEST_CMD and BUILD_DIR in run.sh first." >> "$LOG"; finish 78 ;; esac
done
case $BUILD_DIR in /*) ;; *) BUILD_DIR=$PWD/$BUILD_DIR ;; esac

# Take a lock directory in RUN_DIR, by name: names hold no spaces, RUN_DIR may. A lock whose pid no
# longer runs is stale and taken over.
take() {
    if mkdir "$RUN_DIR/$1" 2>/dev/null; then echo $$ > "$RUN_DIR/$1/pid"; HELD="$HELD $1" TAKEN=$1; return 0; fi
    pid=$(cat "$RUN_DIR/$1/pid" 2>/dev/null)
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then rm -rf "$RUN_DIR/$1"; fi
    return 1
}
wait_for() {  # $1 = phase name, rest = lock names, any one will do
    what=$1; shift; phase "$what"; start=$(date +%s)
    while :; do
        for lock in "$@"; do take "$lock" && return 0; done
        [ $(( $(date +%s) - start )) -lt "$WAIT_MAX" ] || { echo "timeout waiting for $what" >> "$LOG"; finish 75; }
        sleep 10
    done
}

# One run per build output directory, until the run ends: two runs in one build directory break
# on the build tool's own locks.
wait_for builddir ".build-$(printf %s "$BUILD_DIR" | cksum | cut -d' ' -f1)"
lanes=; i=1
while [ "$i" -le "$LANES" ]; do lanes="$lanes .lane-$i"; i=$((i + 1)); done
wait_for lane $lanes
LANE=$TAKEN

phase build
sh -c "$BUILD_CMD" run "$@" >> "$LOG" 2>&1
code=$?
[ "$MODE" = test ] && [ "$code" -eq 0 ] || finish "$code"

# The test phase holds no build lane: the next build starts while this run tests.
rm -rf "$RUN_DIR/$LANE"; HELD=${HELD%" $LANE"}
locks=.testlock
[ "$#" -ge 1 ] && [ "$#" -le "$SMALL_MAX" ] && locks="$locks .testlock-small"
wait_for testlock $locks
case $TAKEN in *-small) export RUN_SMALL=1 ;; esac

phase test
sh -c "$TEST_CMD" run "$@" >> "$LOG" 2>&1
code=$?
[ "$code" -ne 0 ] || finish 0

# Load reds: re-run each red once alone from the same build.
phase rerun
real=0
for id in $(reds "$LOG"); do
    if sh -c "$TEST_CMD" run "$id" >> "$LOG.rerun" 2>&1; then
        echo "load-red: $id" >> "$LOG"
    else
        echo "real-red: $id" >> "$LOG"; real=1
    fi
done
grep -q '^load-red: ' "$LOG" && [ "$real" -eq 0 ] && finish 0
finish "$code"
