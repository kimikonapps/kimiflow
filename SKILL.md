---
name: kimiflow
description: "Gated feature and bug-fix loop: clarify, discover/diagnose, plan, implement, verify, review, and commit. AUTO-ROUTE only actionable implementation requests for substantial feature work that crosses product surfaces/subsystems, adds material integration/data/security/public-API/architecture risk, or needs real discovery. Discussion, ideation, recommendations, explanations, status requests, and wish formulations stay direct/read-only. Explicit Kimiflow always starts it; explicit direct or direkt always bypasses it. Do not auto-trigger for fixes, reviews, refactors, cleanup, docs/config, or small low-risk features. Modes: full|grill|plan|build|quick|review|audit|fix."
disable-model-invocation: false
argument-hint: [full|grill|plan|build|quick|review|audit|fix] [<feature-or-bug>] [--launcher|--menu] [--fix] [--audit <path>] [--verify-feature <feature-or-path>] [--prepare] [--project-map <quick|skip>] [--quiet|--verbose] [--set-verbosity <level>] [--settings]  ·  --resume <slug>
---

# kimiflow — Feature & Fix Loop

Orchestrates the full loop for: **$ARGUMENTS**

You are the **orchestrator**. Run the phases as a state machine, keep only essentials in context, and load phase details from `phases/` on entry.

## Modes (invocation)

- **Automatic feature routing:** enter the normal feature run automatically only when the current request authorizes implementation and the work is substantial under the frontmatter criteria. Discussion, ideation, recommendations, explanations, status requests, and wish formulations do not authorize implementation; keep them direct and read-only. Use the current request as the target; do not open the launcher or ask a routing question. A raw file count is not sufficient. If no material trigger is clear, work directly. Explicit `direct` or `direkt` bypasses Kimiflow; explicit Kimiflow invocation always wins.
- **Launcher / menu:** **`/kimiflow`**, **`/kimiflow --launcher`**, **`/kimiflow --menu`**, or a vague explicit Kimiflow request ("run Kimiflow") opens a context-aware launcher. It first runs `hooks/launcher-status.sh`, uses `.launcher.primary_action` for one recommendation, and shows the compact `.launcher.status` groups; internal hygiene stays in drilldowns. It never writes code directly and never auto-picks a risky action. → reference.md "Launcher mode".
- **`/kimiflow <feature-or-bug>`** — full run (phases 0–7).
- **Natural mode aliases:** **`/kimiflow full|grill|plan|build|quick|review|audit|fix [target]`** and plain text such as **`kimiflow full`** are first-class shortcuts. If the target is omitted, use the current conversation topic only when it is unambiguous; otherwise ask one plain-language question. Alias meanings:
  - **`full`** — scope=`large` strict loop; it does not create an approval stop by itself. Only material risk/authority decisions pause.
  - **`grill`** — Phase 1 only: clarify/spec in plain language, write `INTENT.md`/`PROBLEM.md`, ask "Does this match?", then STOP. No plan and no code.
  - **`plan`** — prepare only: clarify + understand/diagnose + `PLAN.md`/`ACCEPTANCE.md` + plan-gate, then STOP with a resumable backlog run. No code.
  - **`build`** — implement an approved/prepared Kimiflow plan. If no current approved plan/backlog run is available, ask whether to run `full`, `plan`, or `quick`; do not silently invent a plan.
  - **`quick`** — lean small/low-risk: feature confirms intent with no question minimum; fix diagnoses then continues unless a material decision is missing; no-worker Discovery default; **skips Phase-2 recall/Vault Pulse**; one review lens. Never for `full|grill|plan`.
  - **`review`** — alias for `--verify-feature` / current-change review: read-only Phase-7 code-review ensemble over the named path or current diff. No code edits.
  - **`audit`** — alias for `--audit <path>`: read-only cleanup/refactoring scan first; no edits until the user chooses a slice.
  - **`fix`** — alias for `--fix`: problem brief → Red/cause/research → bounded fix → Green/regression; pause only for a material decision.
- **`/kimiflow … --prepare`** — prepare only: phases 0–4, then STOP. Package in `.kimiflow/<slug>/`; implement later, even in a new session.
- **`/kimiflow --resume <slug>`** — read STATE, run resume safety, revalidate changed plans, and regenerate the plain-language build summary before Phase 5. Pause only for a material decision. Unknown basis/paths forbid blind build. No slug → list runs and ask.
- **Feature or fix:** kimiflow detects whether you are building or fixing a bug, and routes accordingly. Force with **`/kimiflow --fix <bug>`**.
- **Audit / cleanup mode:** kimiflow detects cleanup intent ("remove dead code", "over-engineering audit", "entschlacken", "clean up") and runs an **existence-first cleanup lens** over a **required target path**. Force with **`/kimiflow --audit <path>`**. Staged: it finds tagged slices, shows them for approval (the Phase-4 summary gate), then executes them one slice = one commit with a per-slice verify gate. → reference.md "Audit mode".
- **Existing feature check:** **`/kimiflow --verify-feature <feature-or-path>`** runs the normal Phase-7 code-review ensemble read-only over the named path or current diff — same lenses and CANDIDATE→verify→promote mechanic — with findings in `findings/`/`CODE-REVIEW.md`. It does not edit code; confirmed findings can seed a fix/improve run. → reference.md "Existing feature check".
- **Project Map Bootstrap (recommended, skippable):** **`/kimiflow --project-map <quick|skip>`** controls the local `.kimiflow/project/` map. `.kimiflow/project/` is never auto-committed; publish-safe repo docs omit concrete vulnerabilities, exploit paths, secrets, and private/local paths. Declining/`skip` never blocks.
- **Display verbosity (visible output only — engine identical at every level and on every host):** `--quiet`/`--verbose` set the level for one run (never persisted); `--set-verbosity <level>` and `--settings` write config and exit. Resolve it before any launcher/menu prose. Claude Code and Codex must keep the same gates, artifacts, subagents, evidence, thresholds, tests, and acceptance standards at every verbosity level. → Phase 0 + reference.md "Display verbosity".
- **Build/Fix summary:** show the bounded outcome in plain language; schema 4 continues without approval when risk is `none`. `full` does not force a wait.

## Core principles (apply in ALL phases)

- **Language:** reply in the user's language for chat and artifacts.
- **Terse output (HARD RULE — governs every phase; this is where runs bloat).** The `balanced` baseline (display-verbosity scales only the volume, never the engine → reference.md "Display verbosity"). Visible output is control-plane only: a phase line, the gate verdict, the decisive evidence, a question when you need one. Concretely:
  - **(a) One-line phase announcements** — marker + name + ≤1 clause. Never a paragraph.
  - **(b) NEVER paste a full artifact into chat** (INTENT/PROBLEM/RESEARCH/DIAGNOSIS/PLAN/ACCEPTANCE). Write it to its file; show a ≤3-line summary + the path.
  - **(c) Gate verdict = ONE line** — e.g. `gate open · open BLOCKER/HIGH: 0`. No narrative; reasoning lives in `REVIEW.md`.
  - **(d) Evidence = the command + only the decisive output line(s)**, never a full log dump.
  - **(e) No STATE *narration* in chat, no recap tables, no restating what a subagent will do or just did.** Use the Phase-0 task-list widget for glance status, not prose. **Narration ≠ persistence:** terse-output suppresses *talking about* state in chat — it **never** removes writing `STATE.md` / the phase artifacts to disk.
  - **Budget: ≤~6 lines of your own prose per phase**, outside required summaries/evidence.
  - **If resolved level = `quiet`: no progress narration, same engine.** Show only blocking questions/approval stops, the compact launcher/menu, phase/gate one-liners, decisive test evidence, and final paths/status. If the host requires a progress ping, make it a single factual line. No "I will", "I found", "next I", recap bullets, or explanatory paragraphs. Never make quiet reduce code reading, research, tests, reviewers, subagents, gates, or artifact detail.
- **Artifact economy (terse output, for files).** On-disk artifacts (INTENT/PROBLEM/RESEARCH/DIAGNOSIS/PLAN/ACCEPTANCE/findings) are re-read by every fresh subagent every round — write them dense: structured fields + evidence only, no narration or padding. Density NEVER costs rigor — keep every required field, every `file:line`, all evidence, full acceptance precision (EARS + example + method + `AC-N → test_name`). State this density requirement in every artifact-producing delegation's output spec.
- **Phase colors:** announce ⚪0 Setup · 🔵1 Clarify · 🟣2 Understand · ⚫3 Plan · 🟡4 Plan-gate · 🟠5 Implement · 🟤6 Verify · 🟢7 Review/Commit; keep the marker on STATE/status lines.
- **Self-contained.** Gates/thresholds live here + reference.md, never personal/global `CLAUDE.md`; project `CLAUDE.md` is only an optional Phase-2 conventions hint.
- **Minimum-complete.** Every task/file/test maps to approved behavior or a verified `required` constraint. Research corrects HOW, never expands WHAT; `optional` findings stay out. Choose reversible defaults; no speculative abstractions.
- **Anti-hallucination.** Only claims you can back. "Not verifiable" is valid. Severity never higher than provable by a code reference.
- **Evidence-before-assertion.** Never claim "done/green/root cause found" without showing the actual command + output / the `file:line`.
- **Agent budget.** Default 1 implementer + 1–2 reviewers; fan out ~5–10 only when useful, >10 asks first. Record fan-out; substitutions reuse a seat and external CLI counts as one. Fold work unless independence matters.
- **Model routing.** Prefer `top` for orchestration/planning/verdicts/risky diagnosis; `balanced` may implement bounded work; `cheap` only deterministic collection/support. A lower-tier active session is a recorded quality fallback, not a user-interaction gate. Fallback → reference.md "Model routing (per-role)".
- **Persist phase progress (NOT optional, NOT terse-trimmable).** Phase 0 creates `.kimiflow/<slug>/STATE.md`; after every phase set `Phase N: open|in-progress|done`. Chat state is not enough: `state-gate` blocks the review-gate call when `STATE.md` is missing.
- **Workspace + Active Session Contract.** Before a write run, `workspace-preflight.sh` inventories every worktree; default to the current tree, batch ambiguity into one upfront decision, never force cleanup, and treat Codex-managed trees as app-owned. Every write run starts `active-run.sh` before that decision so its STATE-backed one-shot receipt survives park/resume; `working-tree-gate.sh` remains the final current-tree assertion. Other sessions must pass `conflict-check`; a separate tree still needs explicit authority and trusted registration. An exceptional Git worktree is capped at one and retired only when noncurrent, owned, terminal (`done|failed|aborted`), clean, and unlocked; `parked` stays resumable. Retirement pins identity and archives every checkout/admin byte without a destructive Git remove.
- **Stop criteria:** success ends; technical failures/findings/caps change strategy and continue. Schema 4 awaits only missing input/authority, workspace ambiguity, external access, paid/privacy, material scope/risk, or irreversibility; preview/commit waits are invalid. Never bypass a gate or repeat a failed strategy.
- **Subagents do NOT see your context.** Every delegation carries: objective, output format, allowed files/boundaries, the paths of the relevant state files. For reference.md content, pass the path `${CLAUDE_SKILL_DIR}/reference.md` + the exact section names to read — not the text verbatim (verbatim only for a snippet under ~15 lines). Subagents write results to the named paths.

## Phase Files (on-demand)

Phase detail is loaded only when entering that phase. For post-R2 runs, `hooks/active-run.sh start --run .kimiflow/<slug> --write` marks `phase_reads_required: true`; read `phases/PHASES.json`, read the phase file, then record it with `hooks/active-run.sh phase-read --run .kimiflow/<slug> --phase <N> --file phases/<file>.md --write` before crossing the next gate boundary. `clarify-gate.sh` checks through Phase 1, `plan-blocker-gate.sh` through Phase 4, and `finish --write` through Phase 7.

| Phase | File | Always-loaded boundary cues |
|---|---|---|
| 0 Setup, Routing & Scope-Gate | `phases/phase-0-setup.md` | model/session; `workspace-preflight.sh` then clean gate; frontend Contract-1 start receipt; scope/verbosity. |
| 1 Clarify | `phases/phase-1-clarify.md` | one compact unknowns batch; simple contract + authority receipt; fix problem brief; no question minimum. |
| 2 Understand / diagnose | `phases/phase-2-understand.md` | Current-State Pulse / Gate; `discovery-gate.sh`; `suggest-affected-sections.sh`; lazy frontend classification. |
| 3 Plan | `phases/phase-3-plan.md` | acceptance criteria, Red evidence for fix mode, cause proof, audit existence-first rules. |
| 4 Plan-gate / approval | `phases/phase-4-review-approval.md` | plan/review resolvers; plain-language build summary; material-risk CONTINUE/STOP/PARK. |
| 5 Implement / fix | `phases/phase-5-build.md` | TDD; Red/clean-tree verification checkpoints; caller-grep; failure escalation. |
| 6 Verify | `phases/phase-6-verify.md` | goal-backward; red/green; frontend evidence; `lsp-diagnostics.sh`; regression/cold-start. |
| 7 Review / commit | `phases/phase-7-review-commit.md` | frontend preflight; review; Memory Router & Learning Loop; `refresh --changed`; commit. |

## Always-Loaded Protected Phase Rules

These operative rules stay in the driver until a later approved packet proves an earlier mechanical gate for the target phase. Phase files may elaborate, but this section is always loaded.

- **Phase 2 protected rules:** top owns Discovery/synthesis/triage. `none|pulse|focused` follows decision need, not size; no worker by default, one focused worker normally, at most two independent lanes. `required|default|optional` protects scope; only `required` may add it. Open technical gaps return to research; only irreducible product/policy choices ask the user. Fix still needs Red + proven cause.
- **Phase 3 protected rules:** write one flat minimum-complete plan; every task/file/abstraction/test maps to `AC-N`; run subtraction before review. Dual-plan adoption takes isolated elements only, never structural merges. Unresolved blockers do not reach reviewers.
- **Phase 4 held rule:** only evidenced BLOCKER/HIGH revises; valid findings and round numbers never reset. Budgets: small 2, large/audit 3. Oscillation/cap recovers autonomously; later epochs require matching STATE/RECOVERY receipts. Schema 4 pauses only for durable material risk; risk `none` continues for every alias. Schema 3 keeps its legacy Preview/fix-approval behavior.
- **Phase 5 protected rules:** the Red commit is tests-only. Red and clean-tree checkpoints both inspect named staged paths, isolate foreign staged paths with a path-limited commit, and run weakening plus secret/path scans before committing. Only a demonstrably clean-tree-only decisive verifier permits a named local production checkpoint; it runs immediately and later review stays based on ACTIVE_RUN `started_head`. Deletions require proof. Technical failure changes approach, never asks merely to iterate.
- **Phase 6 protected rules:** fix runs `red-green-gate.sh`; declared frontend runs re-attest routing with `frontend-quality-gate.sh`, load design/QA files only for active lanes, and loop CLOSED contract/visual failures through a changed strategy without another user confirmation. Any verification failure returns to Phase 5.
- **Phase 7 protected rules:** preflight requires the frontend gate OPEN and Contract-1 `clean/no`. Review from the immutable run start across committed, staged, unstaged, and named untracked files; recompute the target per round and pin it across that round's axes. Add R3 for contracts/multiple surfaces; count only verified promoted candidates and refute HIGH. Triage advisories. Schema 4 commits only named paths locally without another wait, using `git commit --only` so foreign staged work remains untouched; push/release stay explicit. Learning CLOSED blocks completion. Only then guarded terminal retirement.

## Scaling Knobs

Detailed knobs live in `docs/kimiflow-scaling-knobs.md`. Display verbosity is always-on visible-output volume only, never gates, cost, quality, or behavior. Solo-dev implementation stays sequential in the current worktree; behavioral evals are never wired into CI.
