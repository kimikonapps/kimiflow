#!/bin/sh
# Wait for one run.sh run to end: one capped, blocking wait on its log. Rule and reason: rules-worker.md
# § Waits and scratch files. run.sh's first log line is "START <epoch-seconds> <pid>"; only a log
# whose START is at or after --after counts, so a reused log name never yields the previous run's
# EXIT line, and a run that ended before the wait began is still read.
#
# Usage:  T=$(date +%s); sh run.sh <logname> test … &   (then, any time later)
#         sh <main checkout>/.kimiflow/ops/tools/wait.sh --after "$T" <logname> [max-seconds]
#         --after is required: the epoch taken BEFORE run.sh was started.
#         Limit: START has whole seconds, so never reuse a log name within the second you take --after
#         (a new name per run, or the previous log removed before the launch, avoids it).
# Prints: the run's real-red: / load-red: lines, then its last line "EXIT <code> after <n>s";
#         exits with the run's code. On the cap: "run live, log <path>, no EXIT yet", exit 124.

T0=$(date +%s)
if [ "${1:-}" = --after ] && [ $# -ge 2 ]; then AFTER=$2; shift 2; else AFTER=; fi
case $AFTER in ''|*[!0-9]*) echo "usage: wait.sh --after <epoch before run.sh started> <logname> [max-seconds]" >&2; exit 64 ;; esac
[ $# -ge 1 ] || { echo "usage: wait.sh --after <epoch before run.sh started> <logname> [max-seconds]" >&2; exit 64; }
RUN_DIR=${RUN_DIR:-$(dirname "$0")/../../runs}   # the same default as run.sh
LOG=$RUN_DIR/$1.log MAX=${2:-3600} POLL=${WAIT_POLL:-5}

while :; do
    start=$(head -n 1 "$LOG" 2>/dev/null | sed -n 's/^START \([0-9][0-9]*\) .*/\1/p')
    last=$(tail -n 1 "$LOG" 2>/dev/null)
    if [ -n "$start" ] && [ "$start" -ge "$AFTER" ]; then
        case $last in "EXIT "*) break ;; esac
    fi
    if [ $(( $(date +%s) - T0 )) -ge "$MAX" ]; then
        echo "run live, log $LOG, no EXIT yet after ${MAX}s"
        exit 124
    fi
    sleep "$POLL"
done
grep -E '^(real|load)-red: ' "$LOG"
echo "$last"
code=${last#EXIT }
exit "${code%% *}"
