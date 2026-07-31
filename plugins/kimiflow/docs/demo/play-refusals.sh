#!/usr/bin/env bash
# kimiflow demo — SCRIPTED ILLUSTRATION of the gate-refusal moments, NOT a captured
# model run. Dramatises four times kimiflow says "no", anchored in the real mechanisms
# (secret-path block · resolve-review-gate open-findings · diagnose rule · test-weakening
# scan FLAG). Rendered to a GIF by kimiflow-demo.tape. For a REAL run, see README.md.
set -euo pipefail

D=$'\033[2m'      # dim — the model's intent
B=$'\033[1m'      # bold
C=$'\033[1;36m'   # cyan — beat title
R=$'\033[1;31m'   # red  — the refusal
Z=$'\033[0m'

e(){ printf '%b\n' "$1"; sleep "${2:-0.5}"; }

e "${B}kimiflow — four moments it says no${Z}   ${D}(scripted illustration)${Z}" 0.9
e "" 0.2

e "${C}1 · bulk staging or a secret-looking path${Z}" 0.4
e "  ${D}model: tests green — git add -A…${Z}" 0.6
e "  🟢 COMMIT-SECRET-GATE  checks the staging command" 0.4
e "  ${R}⛔ blocked — stage verified run-owned paths by name${Z}" 0.7
e "     ${D}local commits are automatic; unsafe staging is not.${Z}" 0.7
e "" 0.2

e "${C}2 · a HIGH the model can't talk past${Z}" 0.4
e "  ${D}model: that edge case is unlikely — let's just proceed${Z}" 0.6
e "  🟡 PLAN-GATE  reviewer wrote:  FINDING HIGH auth/refresh.ts" 0.4
e "  ${R}⛔ resolve-review-gate.sh → open-findings 1 → gate CLOSED${Z}" 0.7
e "     ${D}counts the finding file, not the model's confidence${Z}" 0.7
e "" 0.2

e "${C}3 · a fix with no proven cause${Z}" 0.4
e "  ${D}model: probably a null check — I'll just add one${Z}" 0.6
e "  🟣 DIAGNOSE-GATE  no reproduced failure + proven cause on record" 0.4
e "  ${R}⛔ no fix. reproduce → prove the root cause → then fix.${Z}" 0.7
e "" 0.2

e "${C}4 · a quietly skipped test${Z}" 0.4
e "  ${D}model: staged the fix — ready to commit${Z}" 0.6
e "  🟢 test-weakening-scan  FLAG  it.skip(\"refresh rotates\")" 0.4
e "  ${R}⛔ autonomous triage restores the test or proves the flag irrelevant${Z}" 0.8
e "" 0.3

e "${B}every ⛔ above is a script or a hook — a verbose model can't argue past it.${Z}" 1.2
