---
name: kimiflow
description: "Feature/fix/release loop. Auto-route only actionable implementation requests for substantial features with material cross-surface, integration, data, security, public-API, architecture, or discovery risk. Discussion, ideation stays direct/read-only. Explicit Kimiflow starts; explicit direct or direkt always bypasses. Do not auto-trigger for fixes, reviews, refactors, cleanup, docs/config, or low-risk features. Modes: full|grill|plan|build|quick|review|audit|fix|release."
disable-model-invocation: false
argument-hint: [full|grill|plan|build|quick|review|audit|fix|release] [<feature-or-bug>] [--launcher|--menu] [--fix] [--audit <path>] [--verify-feature <feature-or-path>] [--prepare] [--project-map <quick|skip>] [--quiet|--verbose] [--set-verbosity <level>] [--settings]  ·  --resume <slug>
---

# kimiflow — Feature & Fix Loop

Orchestrates the full loop for: **$ARGUMENTS**

You are the **orchestrator**. Run phases as a state machine; load each phase from `phases/` on entry.

## Modes (invocation)

- **Automatic feature routing:** apply the frontmatter boundary; implementation authority and a material trigger are required. Use the current target without a launcher or routing question. Raw file count is insufficient. Explicit overrides win.
- **Launcher / menu:** **`/kimiflow`**, **`--launcher`**, **`--menu`**, or vague explicit Kimiflow opens the launcher. Run `hooks/launcher-status.sh`, show its primary action and compact status; never write code or auto-pick risk. → reference.md "Launcher mode".
- **`/kimiflow <feature-or-bug>`** — full run (phases 0–7).
- **Natural mode aliases:** **`/kimiflow full|grill|plan|build|quick|review|audit|fix|release [target]`** and plain text such as **`kimiflow full`** are first-class shortcuts. If the target is omitted, use the current conversation topic only when it is unambiguous; otherwise ask one plain-language question. Alias meanings:
  - **`full`** — scope=`large` strict loop; it does not create an approval stop by itself. Only material risk/authority decisions pause.
  - **`grill`** — Phase 1 only: clarify/spec, write the plain contract, then STOP. No plan or code.
  - **`plan`** — phases 0–4 only; writes an approved, resumable backlog plan. No code.
  - **`build`** — implement an approved/prepared Kimiflow plan. If no current approved plan/backlog run is available, ask whether to run `full`, `plan`, or `quick`; do not silently invent a plan.
  - **`quick`** — lean small/low-risk: feature proves intent or asks one small product batch; fix diagnoses and continues unless a material decision is missing; no-worker Discovery default; **skips Phase-2 recall/Vault Pulse**; one review lens. Never for `full|grill|plan`.
  - **`review`** — alias for `--verify-feature` / current-change review: read-only Phase-7 code-review ensemble over the named path or current diff. No code edits.
  - **`audit`** — alias for `--audit <path>`: read-only cleanup/refactoring scan first; no edits until the user chooses a slice.
  - **`fix`** — alias for `--fix`: problem brief → Red/cause/research → bounded fix → Green/regression; pause only for a material decision.
  - **`release` / “Release Flow”** — explicitly authorizes one project-profile release. Load release context only now; import or re-audit on first use, control drift, or a real failure, then run the bound profile without routine prompts. Audit improvements stay separate from publication. → reference.md "Project Release Profile".
- **`/kimiflow … --prepare`** — phases 0–4 only; package a resumable backlog run.
- **`/kimiflow --resume <slug>`** — read STATE, run resume safety, revalidate changed plans, and regenerate the plain-language build summary before Phase 5. Pause only for a material decision. Unknown basis/paths forbid blind build. No slug → list runs and ask.
- **Feature or fix:** kimiflow detects whether you are building or fixing a bug, and routes accordingly. Force with **`/kimiflow --fix <bug>`**.
- **Audit / cleanup mode:** kimiflow detects cleanup intent ("remove dead code", "over-engineering audit", "entschlacken", "clean up") and runs an **existence-first cleanup lens** over a **required target path**. Force with **`/kimiflow --audit <path>`**. Staged: it finds tagged slices, shows them for approval (the Phase-4 summary gate), then executes them one slice = one commit with a per-slice verify gate. → reference.md "Audit mode".
- **Existing feature check:** **`/kimiflow --verify-feature <target>`** runs the Phase-7 review ensemble read-only; confirmed findings can seed a fix run. → reference.md "Existing feature check".
- **Local security:** **`kimiflow security scan` / `security diff|deep|ci-artifact|eval|promote`** stays private/advisory, bounded, provider-neutral, and fail-closed; `accept|close` uses a fix child. → reference.md "Local actionable security".
- **Project Map Bootstrap (recommended, skippable):** **`/kimiflow --project-map <quick|skip>`** controls the local `.kimiflow/project/` map. `.kimiflow/project/` is never auto-committed; publish-safe repo docs omit concrete vulnerabilities, exploit paths, secrets, and private/local paths. Declining/`skip` never blocks.
- **Display verbosity:** `--quiet`/`--verbose` affect visible output for one run; `--set-verbosity`/`--settings` persist and exit. Resolve before launcher prose. Every level/host keeps identical gates, artifacts, agents, evidence, tests, and acceptance. → Phase 0 + reference.md "Display verbosity".
- **Build/Fix summary:** show the bounded outcome in plain language; schema 4+ continues without approval when risk is `none`. `full` does not force a wait.

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
- **Artifact economy.** Write required fields + evidence without padding; keep locators and oracles. Load `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/references/workflow-prose-quality.md` (Codex: `$KIMIFLOW_PLUGIN_ROOT/references/workflow-prose-quality.md`) once before the first decision artifact and apply it to all writing/review. Reload only after fresh-context rollover or file-digest change, never on phase change. Semantic defects stay with existing gates; style alone never blocks.
- **Phase colors:** announce ⚪0 Setup · 🔵1 Clarify · 🟣2 Understand · ⚫3 Plan · 🟡4 Plan-gate · 🟠5 Implement · 🟤6 Verify · 🟢7 Review/Commit; keep the marker on STATE/status lines.
- **Self-contained.** Gates/thresholds live here + reference.md, never global `CLAUDE.md`; project `CLAUDE.md` is only a Phase-2 hint.
- **Minimum-complete.** User owns product WHAT/WHY; the agent owns technical HOW. Every task/file/test maps to approved behavior or verified `required` constraints. Research corrects HOW, never expands WHAT; keep `optional` out and defaults reversible.
- **Clarify, inspect, plan, confirm once, then build.** Ask only for material product facts that are actually missing. Bind research to current HEAD/path bytes and inspect `reuse → evolve → new`; once the reviewed plan exists, obtain one final product-contract confirmation in the user's language. Research may correct HOW, never expand WHAT.
- **Anti-hallucination.** Only claims you can back. "Not verifiable" is valid. Severity never higher than provable by a code reference.
- **Evidence-before-assertion.** Never claim "done/green/root cause found" without showing the actual command + output / the `file:line`.
- **Agent budget.** Default 1 implementer + 1–2 reviewers; use ~5–10 only when useful, >10 asks first. Substitutions reuse a seat; external CLI counts as one. Fold work unless an independent counter-perspective or semantic review matters.
- **Adaptive control.** Classify scope/intent/domain/operations locally; never infer missing product choices. Rollover only under measured pressure, lower model/variant and embedded-review routes only after runtime-bound clean evals, and keep one deterministic independent audit in ten. Optional code intelligence is explicit, `large`+signal only, Shadow-first, bounded to current root/snapshot, and revoked on quality/token regression. Vault context uses its namespace; old runs use verified retention. Missing support falls back to the existing flow without a user gate. → reference.md "Adaptive control plane".
- **Persist phase progress.** Phase 0 creates `.kimiflow/<slug>/STATE.md`; `phase-read` closes the prior phase and opens the current one, so never patch those transitions around it. STATE, not chat, is authoritative; `state-gate` blocks review without it.
- **Workspace + Active Session Contract.** `workspace-preflight.sh`: free Primary direct; busy/dirty: ≤3 locked Fleet trees, then FIFO; foreign/Codex untouched. Phase 3: exclusive Primary/Fleet path-contract leases to PLAN + `blocked_by`; Phase 5 gates writes; revalidates after Primary advances. Commit → combined merge-tree/argv integration → `needs-reconcile` or finish → ancestry retire. If an operator already fast-forwarded the exact Fleet head, only Primary-only `recover-integrated --expected-head <full-sha>` with fresh no-shell checks may adopt it; each delivered path outside the declaration needs an exact `--approved-path`, and that set must equal the undeclared delta. Never hand-edit a receipt. `working-tree-gate.sh` uses its root.
- **Adaptive Execution Contract.** New non-trivial schema-5 `feature|fix` writes declare `Execution contract: 1`; schema 4 remains compatible. A Stop boundary counts one work unit unless already observed. Two unchanged units select phase-local recovery; churn/replays are not progress. Profile and strategy stay independent. Budget pressure prunes only optional breadth. Explicit graph events win; `status`/`next-action` read only, and `observe` records only new decisive artifacts.
- **Risk-shaped Convergence.** Schema-5 writes pin Contract 1: 1–8 AC-mapped slices; only risky work adds 1–5 failure classes. Audit/release parents stay lean; each code-changing slice/repair uses one contracted child. Findings need meaningful typed digest-pinned reproduction and matching negative evidence. Reviews bind affected-path bytes; targeted checks precede whole-intent conformance; no extra phase, agent, or user gate.
- **Stop criteria:** success ends; technical failures/findings/caps/repeated root classes change strategy and continue. Schema 4+ awaits only missing input/authority, workspace ambiguity, external access, paid/privacy, material scope/risk, or irreversibility; preview/commit waits are invalid. Never bypass a gate or repeat a failed strategy.
- **Pi/FirstMate:** Pi remains the free conversational Captain/user contact. It starts one visible Main from immutable request/plan. Main owns intake-to-review and visible FirstMate crew: pre-confirmation research Scouts, then implementation Ships/review Scouts. Main/workers use brief/status.
- **Subagents lack context.** Pass objective, format, boundaries, state paths, and reference file plus exact sections. Results go to named paths.

## Phase Files (on-demand)

Bootstrap: run `workspace-preflight.sh route --run .kimiflow/<slug> --write`, then `active-run.sh init-state --run .kimiflow/<slug> --mode <mode> --scope <scope> --language <BCP-47> --title "<title>" --write` alone. Fresh non-trivial Contract-4 features retain its `next_command` until the Phase-4 confirmation; other modes execute it now. Never chain or guess selectors. Enter phases with `active-run.sh phase-read --run .kimiflow/<slug> --phase <N> --file phases/<phase-file> --packet --write`.

Contracts/helpers: Contract-4 single final Product Intake after planning; schema-2 runs remain resumable. Current Codebase Basis; Current-State Pulse / Gate; Memory Router & Learning Loop; `clarify-gate.sh`, `codebase-basis.sh`, `discovery-gate.sh`, `lsp-diagnostics.sh`, `suggest-affected-sections.sh`, P7 `refresh --changed`.

| Phase | File | Always-loaded boundary cues |
|---|---|---|
| 0 Setup, Routing & Scope-Gate | `phases/phase-0-setup.md` | model/session; `workspace-preflight.sh` then clean gate; frontend Contract-1 start receipt; scope/verbosity. |
| 1 Clarify | `phases/phase-1-clarify.md` | Resolve only material unknowns; no routine confirmation and no HOW questions. |
| 2 Understand / diagnose | `phases/phase-2-understand.md` | Current-State/Discovery gates; selective Vault context; scoped standards; conditional architecture/domain/operations. |
| 3 Plan | `phases/phase-3-plan.md` | acceptance criteria; conditional architecture/domain/operations checks; Red/cause proof for fixes. |
| 4 Plan-gate / approval | `phases/phase-4-review-approval.md` | one final product-contract confirmation on the plan; review resolvers; material-risk CONTINUE/STOP/PARK. |
| 5 Implement / fix | `phases/phase-5-build.md` | TDD; Red/clean-tree verification checkpoints; caller-grep; failure escalation. |
| 6 Verify | `phases/phase-6-verify.md` | goal-backward; red/green; conditional contract checks; conformance; frontend/regression evidence. |
| 7 Review / commit | `phases/phase-7-review-commit.md` | preflights; review; Memory/Learning; model outcome; bounded retention; commit. |

## Always-Loaded Protected Phase Rules

These operative rules stay in the driver until a later approved packet proves an earlier mechanical gate for the target phase. Phase files may elaborate, but this section is always loaded.

- **Phase 1 protected rules:** Ask one compact batch only for a missing material WHAT/WHY fact; complete requests continue. Write provisional `INTENT.md`, run the bounded critic, and keep HOW agent-owned. No Phase-1 confirmation. Active schema-2 runs resume their pinned contract.
- **Phase 2 protected rules:** top owns Discovery/synthesis/triage/fit; memory is bounded. Before external research, capture the current affected-path basis and inspect `reuse → evolve → new`; a new file does not select `new` when existing behavior evolves. RESEARCH must carry the exact discovery, scope, coherent reuse, and architecture markers/fields, then pass `discovery-gate.sh` before Phase 3. Technical gaps recover; only product/policy choices ask.
- **Phase 3 protected rules:** one flat minimum-complete, subtracted, AC-mapped plan. Material decisions declare `review_only|spike_required|runtime_required`; required spikes are digest-bound. Contract 1 adds checkable slices/failure classes. New reviews mark `standard|contract`; contract adds the independent state/aggregation lens, not a matrix. Managed trees bind paths/contracts to its digest.
- **Phase 4 held rule:** With PLAN complete and no missing user fact, record exactly one final product-contract confirmation. Return only for a new material product decision. Then only evidenced BLOCKER/HIGH revises; lenses share one frozen PLAN and one seal per round. Risk `none` never asks again.
- **Phase 5 protected rules:** require the PLAN-digest write gate; execute required spike/runtime evidence and slices. Stage named paths only, preserve foreign staging, and scan weakening/secrets/paths. Red commits tests only; deletion needs proof; failures change approach.
- **Phase 6 protected rules:** fixes require `red-green-gate.sh`. Execute declared checks; record each decision's evidence result plus the whole-intent sweep, and trace every Contract-3/4 Requirement. Code gaps → Phase 5; strategy/research drift → Phase 2.
- **Phase 7 protected rules:** review the stable PLAN once, then exact deltas. Schema-4 saturation binds cascades; only source-bound schema-3 repairs pass. Protected impacts stay gated. Recovery is class-scoped; three rounds plus zero-carry closeout are absolute.

## Scaling Knobs

Details: `docs/kimiflow-scaling-knobs.md`. Verbosity changes output only—not gates/cost/quality. Solo-dev: one path/run, ≤3 Fleet trees; behavioral evals stay out of CI.
