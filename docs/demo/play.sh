#!/usr/bin/env bash
# kimiflow demo — SCRIPTED ILLUSTRATION of the current core workflow, NOT a
# captured model run. It shows the current front door: launcher, project map,
# memory recall, gated build/fix flow, commit stop, and learning loop. Rendered
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
e "${Y}User chooses: build a feature through Kimiflow${Z}" 0.7
e "⚪ setup ······· ${D}scope · state dir · project-map freshness · current-state gate${Z}" 0.6
e "🔵 clarify ····· ${D}plain-language intent → INTENT.md → \"Does this match?\"${Z}" 0.7
e "🟣 understand ·· ${D}memory recall first → FACTS/LEARNINGS → only then code/web gaps${Z}" 0.8
e "               ${D}fast-moving APIs? primary sources required before spec/plan${Z}" 0.6
e "⚫ plan ········ ${D}minimal tasks + EARS acceptance criteria → PLAN.md / ACCEPTANCE.md${Z}" 0.7
e "🟡 plan-gate ··· ${D}reviewer findings → resolve-review-gate.sh →${Z} ${G}0 BLOCKER/HIGH${Z}" 0.8
e "               ${Y}pre-build summary stops for your approval${Z}" 0.6
e "🟠 implement ··· ${D}TDD where useful · surgical diff · no unrelated refactors${Z}" 0.6
e "🟤 verify ······ ${D}each acceptance check + regression evidence${Z}" 0.6
e "🟢 review ······ ${D}code-review gate + test-weakening + secret advisory scan${Z}" 0.7
e "               ${B}${Y}commit-gate shows the diff and STOPS for your OK${Z}" 0.9
e "↺ learn ······· ${D}classify → record LEARNINGS.jsonl → curate MEMORY-INDEX.json${Z}" 0.8
e "" 0.3
e "${B}less re-reading, better context, same hard gates — in Claude Code and Codex.${Z}" 1.2
