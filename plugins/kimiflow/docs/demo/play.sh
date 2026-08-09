#!/usr/bin/env bash
# kimiflow demo — SCRIPTED ILLUSTRATION of the current feature workflow, NOT a
# captured model run. It shows feature dialogue, current-code inspection, fresh large-run planning,
# sealed three-round review, executable evidence, bug-cascade review, and a local commit. Rendered to
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
e "⚫ 3 · plan ········· ${D}compact packet → fresh worker on large Codex runs${Z}" 0.7
e "  Evidence classes ·· ${D}review_only | spike_required | runtime_required${Z}" 0.6
e "🟡 4 · plan review ·· ${D}R1 independent A/B/C discovery${Z}" 0.6
e "  R2 / R3 ··········· ${D}whole-contract repair check → resolution only${Z}" 0.6
e "  Deterministic seal · ${G}≤3 global rounds · direct state-space checks${Z}" 0.7
e "🟠 5 · implement ···· ${D}smallest accepted change · no parallel overlap${Z}" 0.7
e "🟤 6 · verify ······· ${D}execute acceptance, regression, spike, runtime evidence${Z}" 0.8
e "🟢 7 · review ······· ${D}contract · supported path · impact · proportionality${Z}" 0.8
e "  Cascade scan ······· callers · data · state · assumptions · consequences" 0.7
e "  Root group ········· ${G}3 symptoms → 1 proved cause → 1 repair${Z}" 0.7
e "  Edge finding ······· ${G}non-blocking: immaterial, no repair loop${Z}" 0.6
e "  Protected impact ··· ${G}still blocks: security · privacy · data loss${Z}" 0.6
e "  ${B}${G}named run-owned paths committed locally · push stays explicit${Z}" 0.9
e "↺ learn ············ ${D}only verified, path-bound evidence becomes memory${Z}" 0.7
e "  Compare outcomes ··· ${G}recorded Plain ↔ Kimiflow evidence · disposable repos only${Z}" 0.7
e "" 0.3
e "${B}One fresh plan. Three review roles. Executable evidence. A finite loop.${Z}" 1.2
