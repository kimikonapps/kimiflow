#!/bin/sh
# Wait for one run.sh run to end: one capped, blocking wait on its log. Rule and reason: rules-worker.md
# § Waits and scratch files. Start it after run.sh has started (run.sh empties the log first).
#
# Usage:  sh <main checkout>/.kimiflow/ops/tools/wait.sh <logname> [max-seconds]
# Prints: the run's real-red: / load-red: lines, then its last line "EXIT <code> after <n>s";
#         exits with the run's code. On the cap: "run live, log <path>, no EXIT yet", exit 124.

[ $# -ge 1 ] || { echo "usage: wait.sh <logname> [max-seconds]" >&2; exit 64; }
RUN_DIR=${RUN_DIR:-$(dirname "$0")/../../runs}   # the same default as run.sh
LOG=$RUN_DIR/$1.log MAX=${2:-3600} POLL=${WAIT_POLL:-5} T0=$(date +%s)

while :; do
    last=$(tail -n 1 "$LOG" 2>/dev/null)
    case $last in "EXIT "*) break ;; esac
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
