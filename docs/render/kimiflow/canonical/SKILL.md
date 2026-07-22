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

- **Automatic feature routing:** apply the frontmatter boundary; implementation authority and a material trigger are required. Use the current target without a launcher or routing question. Raw file count is insufficient. Explicit overrides win.
- **Launcher / menu:** **`/kimiflow`**, **`/kimiflow --launcher`**, **`/kimiflow --menu`**, or a vague explicit Kimiflow request ("run Kimiflow") opens a context-aware launcher. It first runs `hooks/launcher-status.sh`, uses `.launcher.primary_action` for one recommendation, and shows the compact `.launcher.status` groups; internal hygiene stays in drilldowns. It never writes code directly and never auto-picks a risky action. → reference.md "Launcher mode".
- **`/kimiflow <feature-or-bug>`** — full run (phases 0–7).
- **Natural mode aliases:** **`/kimiflow full|grill|plan|build|quick|review|audit|fix [target]`** and plain text such as **`kimiflow full`** are first-class shortcuts. If the target is omitted, use the current conversation topic only when it is unambiguous; otherwise ask one plain-language question. Alias meanings:
  - **`full`** — scope=`large` strict loop; it does not create an approval stop by itself. Only material risk/authority decisions pause.
  - **`grill`** — Phase 1 only: clarify/spec, write the plain contract, then STOP. No plan or code.
  - **`plan`** — prepare only: clarify + understand/diagnose + `PLAN.md`/`ACCEPTANCE.md` + plan-gate, then STOP with a resumable backlog run. No code.
  - **`build`** — implement an approved/prepared Kimiflow plan. If no current approved plan/backlog run is available, ask whether to run `full`, `plan`, or `quick`; do not silently invent a plan.
  - **`quick`** — lean small/low-risk: feature proves intent or asks one small product batch; fix diagnoses and continues unless a material decision is missing; no-worker Discovery default; **skips Phase-2 recall/Vault Pulse**; one review lens. Never for `full|grill|plan`.
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
- **Terse output (HARD RULE).** At the `balanced` baseline, visible output is control-plane only; verbosity changes volume, never the engine (→ reference.md "Display verbosity"):
  - **(a) One-line phase announcements** — marker + name + ≤1 clause. Never a paragraph.
  - **(b) NEVER paste full artifacts into chat.** Write them; show a ≤3-line summary + path.
  - **(c) Gate verdict = ONE line** — e.g. `gate open · open BLOCKER/HIGH: 0`. No narrative; reasoning lives in `REVIEW.md`.
  - **(d) Evidence = command + decisive output lines**, never a full log.
  - **(e) No STATE narration, recap tables, or subagent play-by-play.** Use the task widget. Terse chat never removes required disk artifacts.
  - **Budget: ≤~6 lines of your own prose per phase**, outside required summaries/evidence.
  - **At `quiet`: no progress narration, same engine.** Show only required questions/stops, launcher, one-line phases/gates, decisive evidence, and final status. Host-required pings are one factual line. Never reduce reading, research, tests, reviewers, gates, or artifacts.
- **Artifact economy (terse output, for files).** On-disk artifacts (INTENT/PROBLEM/RESEARCH/DIAGNOSIS/PLAN/ACCEPTANCE/findings) are re-read by every fresh subagent every round — write them dense: structured fields + evidence only, no narration or padding. Density NEVER costs rigor — keep every required field, every `file:line`, all evidence, full acceptance precision (EARS + example + method + `AC-N → test_name`). State this density requirement in every artifact-producing delegation's output spec.
- **Phase colors:** announce ⚪0 Setup · 🔵1 Clarify · 🟣2 Understand · ⚫3 Plan · 🟡4 Plan-gate · 🟠5 Implement · 🟤6 Verify · 🟢7 Review/Commit; keep the marker on STATE/status lines.
- **Self-contained.** Gates/thresholds live here + reference.md, never personal/global `CLAUDE.md`; project `CLAUDE.md` is only an optional Phase-2 conventions hint.
- **Minimum-complete.** User owns product WHAT/WHY; the agent owns technical HOW. Every task/file/test maps to approved behavior or verified `required` constraints. Research corrects HOW, never expands WHAT; keep `optional` out and defaults reversible.
- **Anti-hallucination.** Only claims you can back. "Not verifiable" is valid. Severity never higher than provable by a code reference.
- **Evidence-before-assertion.** Never claim "done/green/root cause found" without showing the actual command + output / the `file:line`.
- **Agent budget.** Default 1 implementer + 1–2 reviewers; fan out ~5–10 only when useful, >10 asks first. Record fan-out; substitutions reuse a seat and external CLI counts as one. Fold work unless independence matters.
- **Model routing.** Prefer `top` for orchestration/planning/verdicts/risky diagnosis; `balanced` may implement bounded work; `cheap` only deterministic collection/support. A lower-tier active session is a recorded quality fallback, not a user-interaction gate. Fallback → reference.md "Model routing (per-role)".
- **Persist phase progress (NOT optional, NOT terse-trimmable).** Phase 0 creates `.kimiflow/<slug>/STATE.md`; after every phase set `Phase N: open|in-progress|done`. Chat state is not enough: `state-gate` blocks the review-gate call when `STATE.md` is missing.
- **Workspace + Active Session Contract.** Before writes, `workspace-preflight.sh` inventories all worktrees; default current, batch ambiguity once, never force cleanup, and treat Codex-managed trees as app-owned. Start `active-run.sh` before that decision so its receipt survives resume; `working-tree-gate.sh` is the final current-tree assertion. Other sessions need `conflict-check`; separate trees need authority and trusted registration. At most one exceptional tree may exist; retire it only when noncurrent, owned, terminal, clean, and unlocked, with identity pinned and all bytes archived.
- **Adaptive Execution Contract.** New non-trivial schema-4 `feature|fix` write runs declare `Execution contract: 1`. Each owner Stop boundary counts one run-wide work unit unless an explicit observation already covered that boundary. Two semantically unchanged units select the current phase's explicit recovery edge automatically; source churn, repeated reads, replayed evidence, and formatting-only evidence rewrites do not count as progress. `compact|standard|critical` are bounded quality profiles with an explicit selection reason, and `normal|recovery` is an orthogonal strategy mode. Hard budget pressure prunes optional breadth only—never required product, evidence, verification, review, learning, or finish gates. Explicit graph events outrank the generic no-progress route. `status`/`next-action` are read-only; record only new decisive run-artifact evidence with `active-run.sh observe`.
- **Stop criteria:** success ends; technical failures/findings/caps change strategy and continue. Schema 4 awaits only missing input/authority, workspace ambiguity, external access, paid/privacy, material scope/risk, or irreversibility; preview/commit waits are invalid. Never bypass a gate or repeat a failed strategy.
- **Subagents lack your context.** Pass objective, output format, boundaries, and relevant state paths. For reference content, pass `${CLAUDE_SKILL_DIR}/reference.md` plus exact section names, not its text (except snippets under ~15 lines). Results go to named paths.

## Phase Files (on-demand)

On phase entry, post-R2 runs (`phase_reads_required`) read `phases/PHASES.json`, its phase file, and only that row's `reference_sections` via `hooks/reference-section.sh "<section>"`. Record with `active-run.sh phase-read --run .kimiflow/<slug> --phase <N> --file phases/<file>.md --write`; the receipt binds both hash sets. Never preload all `reference.md`. Clarify checks through Phase 1, plan-blocker through Phase 4, and finish through Phase 7. Resume via `active-run.sh next-action`.

| Phase | File | Always-loaded boundary cues |
|---|---|---|
| 0 Setup, Routing & Scope-Gate | `phases/phase-0-setup.md` | model/session; `workspace-preflight.sh` then clean gate; frontend Contract-1 start receipt; scope/verbosity. |
| 1 Clarify | `phases/phase-1-clarify.md` | Contract-3 mandatory Product Intake via `clarify-gate.sh`; ≤1 causal second batch; no HOW; intent lock. |
| 2 Understand / diagnose | `phases/phase-2-understand.md` | Current-State Pulse / Gate; `discovery-gate.sh`; `suggest-affected-sections.sh`; Contract-3 feasibility; scoped standards; conditional Senior Design trigger; lazy frontend classification. |
| 3 Plan | `phases/phase-3-plan.md` | acceptance criteria, conditional Architecture Fit mapping, Red evidence for fix mode, cause proof. |
| 4 Plan-gate / approval | `phases/phase-4-review-approval.md` | plan/review resolvers; plain-language build summary; material-risk CONTINUE/STOP/PARK. |
| 5 Implement / fix | `phases/phase-5-build.md` | TDD; Red/clean-tree verification checkpoints; caller-grep; failure escalation. |
| 6 Verify | `phases/phase-6-verify.md` | goal-backward; red/green; adaptive implementation conformance; frontend evidence; `lsp-diagnostics.sh`; regression. |
| 7 Review / commit | `phases/phase-7-review-commit.md` | frontend/conformance preflight; review; Memory Router & Learning Loop; `refresh --changed`; commit. |

## Always-Loaded Protected Phase Rules

These operative rules stay in the driver until a later approved packet proves an earlier mechanical gate for the target phase. Phase files may elaborate, but this section is always loaded.

- **Phase 1 protected rules:** a new non-trivial Contract-3 feature requires one explicit 1–5-question Product Intake before plan/write; complete intent gets a contract confirmation. HOW stays agent-owned; round 2 is only for a product conflict created by round 1. Receipts omit answers and reject auto/cancel/error; Clarify pins a one-shot INTENT lock, then continues.
- **Phase 2 protected rules:** top owns Discovery/synthesis/triage and Reference Strategy Fit. Load standards by Scope/Type. Architecture is `off` for local/reversible work; `active` for material integration/data/security/API/concurrency/scale/irreversibility or poor fit. Active records envelope, `fit|evolve|replace`, 2 approaches, ≤3 principles, 1 falsifier, ≤450 words, `user_gate=no`. Technical gaps change strategy; only irreducible product/policy choices ask.
- **Phase 3 protected rules:** write one flat minimum-complete, subtracted, AC-mapped plan. Active architecture maps decision/evidence/falsifier to an existing AC or at most one new AC. Dual-plan adoption takes isolated elements only; unresolved blockers never reach review.
- **Phase 4 held rule:** only evidenced BLOCKER/HIGH revises; architecture change needs an executable failure or concrete named-invariant violation, never taste. Valid findings/rounds never reset. Budgets: small 2, large/audit 3. Oscillation/cap recovers autonomously with matching receipts. Schema 4 pauses only for durable material risk; risk `none` continues. Schema 3 keeps legacy approval.
- **Phase 5 protected rules:** Red commits tests only. Red/clean-tree checkpoints inspect named staging, isolate foreign staging with a path-limited commit, and run weakening plus secret/path scans. A named local production checkpoint needs a decisive clean-tree-only verifier; later review stays based on ACTIVE_RUN `started_head`. Deletions need proof; technical failure changes approach. Execution Contract 1 follows its profile/strategy/directive; churn never resets recovery.
- **Phase 6 protected rules:** fixes require `red-green-gate.sh`. Contract-1 feature/fix binds 1–5 evidence→invariant→path→AC decisions to exact checks via `conformance-gate.sh`; Contract-3 features additionally prove every immutable `Requirement Rn` with one passed evidence row. `small` folds, `large` reuses its independent verifier. Architecture/frontend stay conditional. Code/scope gaps → Phase 5; strategy/architecture/research drift → Phase 2 without confirmation. Observe the final named verification artifact once so new evidence—not activity—resets no-progress.
- **Phase 7 protected rules:** frontend/conformance preflights must be OPEN. Review the named delta from immutable start; pin targets, add R3 for contracts/multiple surfaces, verify candidates, refute HIGH. Persist only verified scoped principles. Schema 4 commits named paths; foreign staging stays untouched and push/release explicit. Learning CLOSED blocks; finish proves delivery transactionally.

## Scaling Knobs

Detailed knobs live in `docs/kimiflow-scaling-knobs.md`. Display verbosity is always-on visible-output volume only, never gates, cost, quality, or behavior. Solo-dev implementation stays sequential in the current worktree; behavioral evals are never wired into CI.
