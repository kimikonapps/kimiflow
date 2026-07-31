#!/usr/bin/env bash
# kimiflow demo — SCRIPTED ILLUSTRATION of the current feature workflow, NOT a
# captured model run. It shows feature dialogue, current-code inspection,
# evidence-first planning, relevance-aware review, and a local commit. Rendered to
# a GIF by kimiflow-demo.tape. For a REAL run, see docs/demo/README.md.
set -euo pipefail

D=$'\033[2m'      # dim — detail under a phase
B=$'\033[1m'      # bold — command + climax
G=$'\033[1;32m'   # green — a gate that passed
C=$'\033[1;36m'   # cyan — section
Y=$'\033[1;33m'   # yellow — human choice/stop
Z=$'\033[0m'

e(){ printf '%b\n' "$1"; sleep "${2:-0.5}"; }

e "${B}\$ kimiflow build a feature${Z}" 0.9
e "" 0.2
e "⚪ setup ··········· ${D}safe workspace · durable state · current HEAD${Z}" 0.6
e "" 0.2
e "${C}1 · Feature dialogue — before the implementation is fixed${Z}" 0.7
e "  Problem ··········· what should this solve for the user?" 0.5
e "  Visible success ···· what will the user observe when it works?" 0.5
e "  Options ··········· A recommended · B simpler · C broader" 0.5
e "  Scope ············· included · later · excluded" 0.5
e "${Y}  User: discuss — change option B and keep C for later${Z}" 0.7
e "  Kimiflow updates the plain-language flow" 0.5
e "${Y}  User: scope_ready${Z}" 0.7
e "" 0.2
e "${C}2 · Current code + focused research${Z}" 0.7
e "  Code basis ········· ${G}HEAD + affected paths + current bytes${Z}" 0.5
e "  Existing behavior ·· ${G}reuse → evolve → new${Z}" 0.5
e "  Compare ············ code × primary sources × confirmed scope" 0.6
e "  Final product flow ·· 4 visible steps" 0.5
e "${Y}  User: confirmed${Z}" 0.7
e "" 0.2
e "⚫ 3 · plan ········· ${D}ACs + review_only | spike_required | runtime_required${Z}" 0.7
e "🟡 4 · plan review ·· ${D}falsify material assumptions →${Z} ${G}ready${Z}" 0.7
e "🟠 5 · implement ···· ${D}smallest accepted change · no parallel overlap${Z}" 0.7
e "🟤 6 · verify ······· ${D}execute acceptance, regression, spike, runtime evidence${Z}" 0.8
e "🟢 7 · review ······· ${D}contract · supported path · impact · proportionality${Z}" 0.8
e "  Edge finding ······· ${G}non-blocking: immaterial, no repair loop${Z}" 0.6
e "  Protected impact ··· ${G}still blocks: security · privacy · data loss${Z}" 0.6
e "  ${B}${G}named run-owned paths committed locally · push stays explicit${Z}" 0.9
e "↺ learn ············ ${D}only verified, path-bound evidence becomes memory${Z}" 0.7
e "" 0.3
e "${B}Discuss first. Inspect current code. Prove what matters. Stop when done.${Z}" 1.2
