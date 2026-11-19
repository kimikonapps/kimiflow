#!/bin/sh
# Wait for one run.sh run to end: one capped, blocking wait on its log. Rule and reason: rules-worker.md
# § Waits and scratch files. Start it right after run.sh; run.sh's first log line is
# "START <epoch-seconds> <pid>", and only a log whose START is at or after --after counts, so a
# reused log name never yields the previous run's EXIT line.
#
# Usage:  sh <main checkout>/.kimiflow/ops/tools/wait.sh [--after <epoch>] <logname> [max-seconds]
#         --after defaults to the time wait.sh started minus 2 s; pass the epoch taken before run.sh
#         started (date +%s) when wait.sh starts later than that.
# Prints: the run's real-red: / load-red: lines, then its last line "EXIT <code> after <n>s";
#         exits with the run's code. On the cap: "run live, log <path>, no EXIT yet", exit 124.

T0=$(date +%s); AFTER=$((T0 - 2))
if [ "${1:-}" = --after ] && [ $# -ge 2 ]; then AFTER=$2; shift 2; elif [ "${1:-}" = --after ]; then AFTER=; fi
case $AFTER in ''|*[!0-9]*) echo "wait.sh: --after needs epoch seconds" >&2; exit 64 ;; esac
[ $# -ge 1 ] || { echo "usage: wait.sh [--after <epoch>] <logname> [max-seconds]" >&2; exit 64; }
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
