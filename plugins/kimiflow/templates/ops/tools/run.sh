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
BUILD_CMD='<build command>'          # compile only
TEST_CMD='<test command> "$@"'       # "$@" = the test ids given to run.sh; none = the whole suite
reds() { :; }                        # print the red test ids found in log "$1", one per line
# -------------------------------------------------------------------------------------------------

RUN_DIR=${RUN_DIR:-$(dirname "$0")/../../runs}   # .kimiflow/runs: local and untracked like the kit
LANES=${LANES:-2}                    # builds at once on this machine
WAIT_MAX=${WAIT_MAX:-21600}          # seconds to wait for a lane or the test lock

[ $# -ge 2 ] || { echo "usage: run.sh <logname> build|test [test ids...]" >&2; exit 64; }
NAME=$1 MODE=$2; shift 2
mkdir -p "$RUN_DIR/state"
LOG=$RUN_DIR/$NAME.log STATE=$RUN_DIR/state/$NAME.json T0=$(date +%s) HELD=
: > "$LOG"; rm -f "$LOG.rerun"

finish() { echo "EXIT $1 after $(( $(date +%s) - T0 ))s" >> "$LOG"; exit "$1"; }
phase() { printf '{"name":"%s","mode":"%s","phase":"%s","since":%s,"pid":%s}\n' \
    "$NAME" "$MODE" "$1" "$(date +%s)" $$ > "$STATE"; }
cleanup() { for dir in $HELD; do rm -rf "$dir"; done; rm -f "$STATE"; }
trap cleanup EXIT
trap 'finish 143' INT TERM

case $MODE in
    build) CMD=$BUILD_CMD ;;
    test) CMD=$TEST_CMD
        [ -n "${TESTS_OK:-}" ] || { echo "REFUSED: test mode needs TESTS_OK=1 (planner or fix round)." \
            "Workers run: run.sh <name> build" >> "$LOG"; finish 64; } ;;
    *) echo "unknown mode: $MODE" >> "$LOG"; finish 64 ;;
esac
case $CMD in '<'*) echo "Set BUILD_CMD/TEST_CMD in run.sh first." >> "$LOG"; finish 78 ;; esac

# Take a lock directory; a lock whose pid no longer runs is stale and taken over.
take() {
    if mkdir "$1" 2>/dev/null; then echo $$ > "$1/pid"; HELD="$HELD $1"; return 0; fi
    pid=$(cat "$1/pid" 2>/dev/null)
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then rm -rf "$1"; fi
    return 1
}
wait_for() {  # $1 = phase name, rest = lock directories, any one will do
    what=$1; shift; phase "$what"; start=$(date +%s)
    while :; do
        for dir in "$@"; do take "$dir" && return 0; done
        [ $(( $(date +%s) - start )) -lt "$WAIT_MAX" ] || { echo "timeout waiting for $what" >> "$LOG"; finish 75; }
        sleep 10
    done
}

lanes=; i=1
while [ "$i" -le "$LANES" ]; do lanes="$lanes $RUN_DIR/.lane-$i"; i=$((i + 1)); done
wait_for lane $lanes
[ "$MODE" = test ] && wait_for testlock "$RUN_DIR/.testlock"

phase "$MODE"
sh -c "$CMD" run "$@" >> "$LOG" 2>&1
code=$?
[ "$MODE" = test ] && [ "$code" -ne 0 ] || finish "$code"

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
