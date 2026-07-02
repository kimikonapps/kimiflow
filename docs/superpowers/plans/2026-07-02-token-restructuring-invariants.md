# B4 preservation invariants — always-loaded rules in SKILL.md (pre-restructuring) — rev 3

Extracted 2026-07-02 from SKILL.md @ fb13559 by a fresh-context inventory agent, verified line-by-line; rev 2 added the gaps found by plan-audit round 1 (rows 3, 18, 47, 50, 70, 93, 122, 123, 132, 145–147, extended 190, three added hooks, smoke phrase contracts); **rev 3 applies the round-2 corrections** (exception scope 145–146 only, row 23+181, row-122 gloss, check mechanics, codex smoke refs). Classes: **a** = gate/hook command · **b** = mandatory STOP/approval · **c** = fail-closed · **d** = prohibition.

**Contract:** every row keeps an always-loaded mention in SKILL.md after the B4 rewrite. Rows marked (cmd) additionally require the full hook command string with arguments. Exception (WS3): rows **145–146 only** accept a 1-line always-loaded summary in SKILL.md **plus** the full clauses in reference.md "Review rubric"; row 147 and row 23/181 stay in full in SKILL.md.

**Check mechanics (rev 3):** the row texts below are paraphrased glosses, NOT grep needles. Before Commit C, one verbatim mid-string substring per row is extracted from the CURRENT SKILL.md into the check script; the script must pass against the unmodified files first, and its scope is SKILL.md **and** reference.md (rubric clauses for 145–146, the reference-side smoke lines below).

| Line (old) | Class | Key phrase / rule |
|---|---|---|
| 3 | d | frontmatter: OPT-IN — invoke ONLY when the user explicitly asks; Do NOT auto-trigger on ordinary feature/bug/refactor requests |
| 16 | a,d | `hooks/launcher-status.sh` (cmd); never writes code directly; never auto-picks a risky action |
| 18 | b | alias target omitted → use conversation topic only when unambiguous; otherwise ask one plain-language question |
| 19 | b | full: STOP at the pre-build approval gate; do not implement until the user approves |
| 20 | b | grill: ask "Does this match?", then STOP. No plan and no code |
| 21 | b | plan: STOP with a resumable backlog run. No code |
| 22 | b,d | build: ask whether to run full/plan/quick; do not silently invent a plan |
| 23 | d | quick: never use when the user asked for full/grill/plan |
| 23+181 | c | quick = review light: ONE code-review lens `bug-regression` (cross-family when available) plus the advisory scans; add the third lens when the diff touches hooks, plugins, memory, launcher, APIs/contracts, multiple surfaces, or any high-risk path (stays in full in SKILL — the rubric has no quick profile) |
| 24 | d | review: no code edits |
| 25 | d | audit: no edits until the user chooses a slice |
| 27 | b | `--prepare`: phases 0–4, then STOP |
| 28 | b,c | resume: revalidate before Phase 5; blind implementation forbidden; working-tree `OPEN` required — stop + ask; no slug → list + ask |
| 30 | b | audit staged findings shown for approval (Phase-4 summary gate) |
| 31 | d | `--verify-feature` does not edit code |
| 33 | d | `.kimiflow/project/` never auto-committed; repo docs omit vulnerabilities/exploit paths/secrets/private paths |
| 34 | d | verbosity flags never persisted |
| 35 | b | pre-build gate waits for your OK |
| 40–46 | d | terse-output HARD RULE; NEVER paste a full artifact into chat; gate verdict = ONE line |
| 45 | c,d | narration ≠ persistence — never removes writing STATE.md/phase artifacts to disk |
| 47 | d | density NEVER costs rigor — keep every required field, every file:line, all evidence (named in every artifact-producing delegation's output spec) |
| 50 | d | no speculative abstractions, no features beyond the request |
| 49 | d | never in personal/global CLAUDE.md; never for gate criteria/scores/thresholds; never attribute a kimiflow gate to one |
| 51 | c | "Not verifiable" is valid; severity never higher than provable by a code reference |
| 52 | d | never claim done/green/root-cause without actual command + output |
| 53 | b,d | agent budget: beyond ~10 → stop and ask |
| 55 | c | state-gate hook blocks review-gate call when STATE.md missing; chat-only run state = contract violation |
| 56 | a | `hooks/active-run.sh start --run .kimiflow/<slug> --write` (cmd); finish/park/fail/abort --write only |
| 57 | a,c | `hooks/background-run.sh` (cmd); stale/failed/cancelled background work cannot be applied blindly; collection only through the foreground orchestrator |
| 58 | a | `hooks/agentic-readiness.sh status|gate` before background-trust/fan-out (cmd) |
| 59 | b,c | budget-stop: cap reached → stop + ask; never loop forever |
| 60 | d | delegation: path + section names, not verbatim (verbatim only < ~15 lines) |
| 64 | a,b,c | `launcher-status.sh --pretty` (cmd); headless/no answer → STOP, do not auto-pick |
| 65 | a,b,c | `hooks/working-tree-gate.sh` (cmd); `OPEN` is required; `CLOSED` → STOP + ask |
| 67 | b | routing: in doubt ask one simple question; audit requires target path — ask; feature-check ask if missing |
| 69 | a | `active-run.sh start … --write` after STATE.md; `refresh-baseline --write` on stale_risk; Stop hook may block completion |
| 70 | d | subagents keep their OWN internal task-lists; do not mix them into the phase list |
| 71 | a,b | `git rev-parse --is-inside-work-tree`; no repo → report + ask |
| 73–75 | c | scope-gate: in doubt the smaller tier; large activates hard test-gate |
| 77–79 | a,b | `resolve-verbosity.sh get/onboard-check/set` (cmd); `ASK` ∧ interactive → MUST ask once |
| 80 | a | `project-map-status.sh status` / `coverage --affected` (cmd); `--project-map skip` → record + continue |
| 86 | b | explore: one plain-language question only if goal/constraints missing |
| 89 | b,c | explore pick: headless → never auto-pick; none → ONE re-fan-out, then stop + ask |
| 93 | d | loose prior conversation informs the questions but never counts as confirmation |
| 95 | b | gate "Does this match?" |
| 96 | b | gate "Did I understand the problem correctly?" |
| 97 | b | gate "Is this the right cleanup scope?" |
| 98 | a,c | `hooks/clarify-gate.sh .kimiflow/<slug>` (cmd); `OPEN` is required; clarify-evidence marker verbatim |
| 104 | a | `memory-router.sh status` (cmd); MEMORY.md only under budget |
| 105 | a,c | `MR recall --query-file … --write`; `MR provider health` (cmd); vault pulse mandatory small/quick; no vault → record graceful skip + continue |
| 106 | a,c | `current-state-gate.sh assess/verify` (cmd); medium|high → do not finalize RESEARCH/PLAN/spec until `verify` returns `OPEN` |
| 108 | a | `hooks/suggest-affected-sections.sh --intent …` (cmd; never a gate) |
| 113 | c | plan-blocking unknown → resolve first, don't plan on assumptions |
| 116 | b,c | Red evidence to BUG-REPRO.md BEFORE changing production code; not reproducible → clarify, don't fix blindly |
| 117 | c | root cause: find AND prove, not the first guess |
| 119 | b,c | root cause not proven → do NOT fix (keep investigating or stop + ask) |
| 122 | c | audit branch: ask the existence-first question — not "can we dedupe" but "should this exist at all" |
| 123 | c | audit branch: tag format + repo-wide pre-delete grep → 0 callers + git-history freshness check |
| 124 | c | caller-grep is a MINIMUM; tests + refute-the-cut lens are the backstop |
| 126 | c | vault save only if vault MCP connected; else skip + note in STATE |
| 132 | d | planner merge: never structural merges |
| 135 | d | unresolved markers (TBD/TODO) out of plan/acceptance; do not send to reviewers |
| 143 | a,c | `hooks/plan-blocker-gate.sh .kimiflow/<slug>` (cmd); `OPEN` required before spawning reviewers |
| 145 | c | lens A unique clauses: fixes the verified root cause; criteria measurable, complete, non-contradictory; no invented assumptions (WS3 exception: 1-line summary + full clauses in rubric) |
| 146 | c | lens B unique clause: Fix mode — does it address the cause, not the symptom? (WS3 exception: 1-line summary + full clauses in rubric) |
| 147 | c | audit lens fail-closed: a cut survives only if no reviewer finds one — any live caller → downgrade or move to do-NOT-touch; shrink/stdlib need tests green before+after (**stays in full in SKILL — no exception; the rubric carries no audit lens**) |
| 148 | d | findings files: no self-reported count; orchestrator reads, never edits |
| 150 | a,c | `hooks/resolve-review-gate.sh … --round <N> --expect <lensCSV>` (cmd, fail-closed); resolved only by non-reappearance |
| 151 | b,c | anti-oscillation → stop + ask, gate CLOSED |
| 152 | b,c | cap (3) reached without open gate → stop + ask, CLOSED, never auto-proceed |
| 153 | b | `--prepare`: STOP, update STATE, output `--resume` |
| 154 | a,b,c | `resolve-build-gate.sh get` (cmd); STOP, ask approve/change/defer; headless → do NOT build |
| 160 | a,d | red-test-commit exception: test files only, named paths; production code never rides along |
| 161 | c,d | every deletion carries a caller-grep proving zero callers; no proof → don't delete |
| 162 | b,c | unclear failure → escalate to research; after 2 failed fixes cross-family diagnosis (candidate-only); then stop + ask |
| 170 | a,c | `hooks/red-green-gate.sh .kimiflow/<slug> --mode fix` (cmd); `OPEN` required before Phase 7 / promotion / done |
| 171 | a | `hooks/lsp-diagnostics.sh` → FLAGs to ADVISORIES.md (cmd) |
| 175 | c | verifier discrepancy: re-run decisive command; confirmed → phase 5, else record + proceed |
| 176 | c | any failure → back to phase 5 |
| 180 | a | `agentic-readiness.sh packet --run … --kind review --write` (cmd, large/high-risk) |
| 185 | a | lens output `CANDIDATE <SEVERITY> <ref> :: <claim> :: verify=<smallest check>` or `NONE` to code-review-candidates/ |
| 186 | c | BLOCKER/HIGH need active refutation attempt; refuted → rejected; resolver counts only the promoted file |
| 187 | a,c,d | `git add <named paths>` — never `-A`; `test-weakening-scan.sh`; never silently skip advisory channel; `secret-content-scan.sh`; `lsp-diagnostics.sh` (cmds) |
| 188 | a,c | `resolve-review-gate.sh … --expect code-verified` (cmd); same fail-closed/anti-oscillation/cap rules |
| 189 | a,b,c,d | commit-gate STOP; advisory triage fail-closed (dismiss/promote each); wait for explicit OK; named paths, no `git add -A`, no AI trailer, tests green; test-gate marker never staged; audit: never batch slices |
| 190 | a,c,d | `MR review-run --run … --write` required before `Status: done`; quality gate blocks the write; `--skip "<reason>"`; `MR verify-run` CLOSED blocks completion (cmds); neither `.kimiflow/` nor repo files store the key; never patches skills or writes external notes blindly; review-only drafts under `SKILL-DRAFTS/` |
| 191 | a | `project-map-status.sh refresh --changed` (cmd, non-blocking); `hooks/map-staleness-nudge.sh` named as the Stop-hook safety net |
| 192 | a,d | `improvements-status.sh list` / `mark-done <id> --commit <sha> --write` (cmds); attribution EXPLICIT, never auto-guess; `hooks/improvements-staleness-nudge.sh` named as the non-blocking backstop |
| 193 | c | only after commit gate + learning review open → `Status: done` |
| 197 | d | verbosity never coupled to gates/cost |
| 200 | d | best-of-2: oracle authored + committed BEFORE fan-out; candidates uncommitted |
| 207 | d | evals never wired into CI |

## Smoke phrase contracts (added rev 2 — audit round 1)

These literal strings are grepped by `hooks/smoke-install.sh` / `hooks/smoke-install-codex.sh` and must survive both compaction passes **unreworded**. Authoritative source: the smoke scripts themselves (both run per B4 commit); this list makes the invariant grep-check catch a break before the smoke run.

In **SKILL.md** (smoke-install.sh lines in parens): `Launcher / menu` (77) · `Natural mode aliases` (79) · `pre-build approval stop` (84) · `mandatory micro-grill` (85) · `Vault Pulse` (88) · `Project Map Bootstrap` (108) · `improvements-status.sh` (129) · `refresh --changed` (154) · `suggest-affected-sections.sh` (155) · `Agentic Readiness Layer` (167) · `Active Session Contract` (173) · `Background Handles` (174) · `Current-State Gate` (175) · `working-tree-gate.sh` (176) · `clarify-gate.sh` (177) · `red-green-gate.sh` (178) · `lsp-diagnostics.sh` (179) · `--verify-feature` (181) · `Memory Router & Learning Loop` (182) · `code-review ensemble` (183) · plus the smoke-install-codex.sh greps (`--verify-feature <feature-or-path>` :70, `Memory Router & Learning Loop` :175, `code-review ensemble` :176).

In **reference.md**: the alias-table no-code semantics lines (`kimiflow grill.*no code`, `kimiflow plan.*no code`, `kimiflow review.*no code`, `kimiflow audit.*no code` — smoke-install.sh:91–94, smoke-install-codex.sh:96–99; reference.md:73/74/79/80 — **alias table therefore untouched in B4**) · the CANDIDATE format lines (smoke-install.sh:185, smoke-install-codex.sh:178 — satisfied via reference.md:538 and :1440, both kept).
