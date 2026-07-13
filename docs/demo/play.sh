#!/usr/bin/env bash
# kimiflow demo — SCRIPTED ILLUSTRATION of the current core workflow, NOT a
# captured model run. It shows the current front door: launcher, project map,
# adaptive discovery, gated build/fix flow, commit stop, and learning loop. Rendered
# to a GIF by kimiflow-demo.tape. For a REAL run, see docs/demo/README.md.
set -euo pipefail

D=$'\033[2m'      # dim — detail under a phase
B=$'\033[1m'      # bold — command + climax
G=$'\033[1;32m'   # green — a gate that passed
C=$'\033[1;36m'   # cyan — section
Y=$'\033[1;33m'   # yellow — human choice/stop
Z=$'\033[0m'

e(){ printf '%b\n' "$1"; sleep "${2:-0.5}"; }

e "${B}\$ /kimiflow${Z}   ${D}or \$kimiflow in Codex${Z}" 0.9
e "" 0.2
e "${C}Launcher reads the project before asking you to choose${Z}" 0.6
e "  Project Map ······· ${G}current${Z}" 0.4
e "  Memory Router ····· ${G}under budget · relevant learnings ready${Z}" 0.4
e "  Runs / Findings ··· ${G}open work surfaced · curation clean${Z}" 0.4
e "  Menu ·············  map codebase · fix bug · build feature · docs · improve" 0.8
e "" 0.2
e "${Y}User chooses: build a feature or fix a bug${Z}" 0.7
e "⚪ setup ······· ${D}top orchestrator · smallest valid scope · durable state${Z}" 0.6
e "🔵 clarify ····· ${D}feature confirms intent · clear fix records problem, no early stop${Z}" 0.7
e "🟣 understand ·· ${D}project first · Discovery none|pulse|focused · broad recall only at large${Z}" 0.8
e "               ${D}fix → reproduce + prove cause · changing APIs → current primary source${Z}" 0.6
e "⚫ plan ········ ${D}minimal tasks + EARS acceptance criteria → PLAN.md / ACCEPTANCE.md${Z}" 0.7
e "🟡 plan-gate ··· ${D}reviewer findings → resolve-review-gate.sh →${Z} ${G}0 BLOCKER/HIGH${Z}" 0.8
e "               ${Y}feature: risk/full Build Preview · fix: one post-diagnosis Fix Preview${Z}" 0.6
e "🟠 implement ··· ${D}TDD where useful · surgical diff · no unrelated refactors${Z}" 0.6
e "🟤 verify ······ ${D}each acceptance check + regression evidence${Z}" 0.6
e "🟢 review ······ ${D}code-review gate + test-weakening + secret advisory scan${Z}" 0.7
e "               ${B}${Y}commit-gate shows the diff and STOPS for your OK${Z}" 0.9
e "↺ learn ······· ${D}successful evidence only → curate bounded project memory${Z}" 0.8
e "" 0.3
e "${B}less re-reading, better context, same hard gates — in Claude Code and Codex.${Z}" 1.2
