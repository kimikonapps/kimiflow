---
name: kimiflow
description: "Gated feature & bug-fix loop: clarify → research/diagnose (memory-first) → plan → plan-gate → implement → verify → red/green fix gate → code-review ensemble → commit (stops first). Modes: launcher/menu · full|grill|plan|build|quick|review|audit|fix · --prepare · --resume · --fix · --audit · --verify-feature · --project-map. OPT-IN: invoke ONLY when the user explicitly asks for kimiflow or its loop (e.g. \"with kimiflow\", \"run kimiflow\", \"kimiflow full\", \"build this through the gates\", or the /kimiflow command). Do NOT auto-trigger on ordinary feature/bug/refactor requests — the user opts in. Also slash-invocable via /kimiflow."
disable-model-invocation: false
argument-hint: [full|grill|plan|build|quick|review|audit|fix] [<feature-or-bug>] [--launcher|--menu] [--fix] [--audit <path>] [--verify-feature <feature-or-path>] [--prepare] [--project-map <quick|skip>] [--quiet|--verbose] [--set-verbosity <level>] [--settings]  ·  --resume <slug>
---

# kimiflow — Feature & Fix Loop

Orchestrates the full loop for: **$ARGUMENTS**

You are the **orchestrator**. Run the phases as a state machine, keep only essentials in context, and load phase details from `phases/` on entry.

## Modes (invocation)

- **Launcher / menu:** **`/kimiflow`**, **`/kimiflow --launcher`**, **`/kimiflow --menu`**, or a vague explicit Kimiflow request ("run Kimiflow") opens a context-aware launcher. It first runs `hooks/launcher-status.sh`, uses `.launcher.primary_action` for one recommendation, and shows the compact `.launcher.status` groups; internal hygiene stays in drilldowns. It never writes code directly and never auto-picks a risky action. → reference.md "Launcher mode".
- **`/kimiflow <feature-or-bug>`** — full run (phases 0–7).
- **Natural mode aliases:** **`/kimiflow full|grill|plan|build|quick|review|audit|fix [target]`** and plain text such as **`kimiflow full`** are first-class shortcuts. If the target is omitted, use the current conversation topic only when it is unambiguous; otherwise ask one plain-language question. Alias meanings:
  - **`full`** — strict full loop, scope=`large`: full grill/spec, understanding/research, plan + acceptance criteria, plan-gate, then **STOP at the pre-build approval gate**. Do not implement until the user approves the plan.
  - **`grill`** — Phase 1 only: clarify/spec in plain language, write `INTENT.md`/`PROBLEM.md`, ask "Does this match?", then STOP. No plan and no code.
  - **`plan`** — prepare only: clarify + understand/diagnose + `PLAN.md`/`ACCEPTANCE.md` + plan-gate, then STOP with a resumable backlog run. No code.
  - **`build`** — implement an approved/prepared Kimiflow plan. If no current approved plan/backlog run is available, ask whether to run `full`, `plan`, or `quick`; do not silently invent a plan.
  - **`quick`** — lean run for small, low-risk work: mandatory micro-grill, **skips Phase-2 recall and the Vault Pulse** (`scope=large` only; the Current-State Pulse still runs), normal verification, review light (= ONE code-review lens, `bug-regression`, cross-family when available, plus the advisory scans). Never use when the user asked for `full`, `grill`, or `plan`.
  - **`review`** — alias for `--verify-feature` / current-change review: read-only Phase-7 code-review ensemble over the named path or current diff. No code edits.
  - **`audit`** — alias for `--audit <path>`: read-only cleanup/refactoring scan first; no edits until the user chooses a slice.
  - **`fix`** — alias for `--fix`: bug flow with problem clarification, reproduction/Red evidence, root-cause proof, current fix research, Green evidence, and regression.
- **`/kimiflow … --prepare`** — prepare only: phases 0–4, then STOP. Package in `.kimiflow/<slug>/`; implement later, even in a new session.
- **`/kimiflow --resume <slug>`** — read `.kimiflow/<slug>/STATE.md`, run resume safety, revalidate changed plans before Phase 5; unknown plan basis/affected files → blind implementation is forbidden. Backlog resumes first run the working-tree gate (`OPEN` required — unrelated dirty changes → stop + ask), then re-presents the pre-build summary when the build-gate is `on` ∧ interactive. Without `<slug>` → list runs and ask.
- **Feature or fix:** kimiflow detects whether you are building or fixing a bug, and routes accordingly. Force with **`/kimiflow --fix <bug>`**.
- **Audit / cleanup mode:** kimiflow detects cleanup intent ("remove dead code", "over-engineering audit", "entschlacken", "clean up") and runs an **existence-first cleanup lens** over a **required target path**. Force with **`/kimiflow --audit <path>`**. Staged: it finds tagged slices, shows them for approval (the Phase-4 summary gate), then executes them one slice = one commit with a per-slice verify gate. → reference.md "Audit mode".
- **Existing feature check:** **`/kimiflow --verify-feature <feature-or-path>`** runs the normal Phase-7 code-review ensemble read-only over the named path or current diff — same lenses and CANDIDATE→verify→promote mechanic — with findings in `findings/`/`CODE-REVIEW.md`. It does not edit code; confirmed findings can seed a fix/improve run. → reference.md "Existing feature check".
- **Project Map Bootstrap (recommended, skippable):** **`/kimiflow --project-map <quick|skip>`** controls the local `.kimiflow/project/` map. `.kimiflow/project/` is never auto-committed; publish-safe repo docs omit concrete vulnerabilities, exploit paths, secrets, and private/local paths. Declining/`skip` never blocks.
- **Display verbosity (visible output only — engine identical at every level):** `--quiet`/`--verbose` set the level for one run (never persisted); `--set-verbosity <level>` and `--settings` write config and exit. → Phase 0 step 7 + reference.md "Display verbosity".
- **Pre-build summary gate:** end of Phase 4, before building: structured summary waits for your OK — *approve* → build · *change* → revise · *defer → backlog*.

## Core principles (apply in ALL phases)

- **Language:** reply in the user's language for chat and artifacts.
- **Terse output (HARD RULE — governs every phase; this is where runs bloat).** The `balanced` baseline (display-verbosity scales only the volume, never the engine → reference.md "Display verbosity"). Visible output is control-plane only: a phase line, the gate verdict, the decisive evidence, a question when you need one. Concretely:
  - **(a) One-line phase announcements** — marker + name + ≤1 clause. Never a paragraph.
  - **(b) NEVER paste a full artifact into chat** (INTENT/PROBLEM/RESEARCH/DIAGNOSIS/PLAN/ACCEPTANCE). Write it to its file; show a ≤3-line summary + the path.
  - **(c) Gate verdict = ONE line** — e.g. `gate open · open BLOCKER/HIGH: 0`. No narrative; reasoning lives in `REVIEW.md`.
  - **(d) Evidence = the command + only the decisive output line(s)**, never a full log dump.
  - **(e) No STATE *narration* in chat, no recap tables, no restating what a subagent will do or just did.** Use the Phase-0 task-list widget for glance status, not prose. **Narration ≠ persistence:** terse-output suppresses *talking about* state in chat — it **never** removes writing `STATE.md` / the phase artifacts to disk.
  - **Budget: ≤~6 lines of your own prose per phase**, outside required summaries/evidence.
- **Artifact economy (terse output, for files).** On-disk artifacts (INTENT/PROBLEM/RESEARCH/DIAGNOSIS/PLAN/ACCEPTANCE/findings) are re-read by every fresh subagent every round — write them dense: structured fields + evidence only, no narration or padding. Density NEVER costs rigor — keep every required field, every `file:line`, all evidence, full acceptance precision (EARS + example + method + `AC-N → test_name`). State this density requirement in every artifact-producing delegation's output spec.
- **Phase colors — announce each phase with its marker:** ⚪ 0 Setup · 🔵 1 Clarify · 🟣 2 Understand · ⚫ 3 Plan · 🟡 4 Plan-gate · 🟠 5 Implement · 🟤 6 Verify · 🟢 7 Review/Commit. Keep that phase's marker on its STATE updates and status lines. (Output is markdown — the emoji IS the color channel.)
- **Self-contained — the skill is the authority.** Every gate, threshold and standard lives here (+ reference.md), never in a personal/global `CLAUDE.md`; kimiflow runs identically with or without one. It consults the project's `CLAUDE.md` only as an optional Phase-2 conventions hint — never for gate criteria, scores, or thresholds, and never attribute a kimiflow gate to one.
- **Simplicity-first.** Minimal code/plan for the problem. No speculative abstractions, no features beyond the request.
- **Anti-hallucination.** Only claims you can back. "Not verifiable" is valid. Severity never higher than provable by a code reference.
- **Evidence-before-assertion.** Never claim "done/green/root cause found" without showing the actual command + output / the `file:line`.
- **Agent budget.** Fan out to ~5–10 subagents when useful. Beyond ~10 → stop and ask the user first. Default stays lean (1 implementer, 1–2 reviewers); knobs spend within this budget; record any fan-out in STATE.md. Fold into an existing brief unless independence/blindness matters. The budget applies per fan-out decision, not cumulatively per run. Same-seat substitutions are not new spawns; an external CLI exec call counts as one subagent-equivalent.
- **Model routing.** Session model orchestrates and plans; on a Claude Code host under a Fable session the leaf seats (implement/verify/synthesize + same-family review) run on Opus; a different model family takes one review lens when available. Details/fallback → reference.md "Model routing (per-role)".
- **Persist phase progress (NOT optional, NOT terse-trimmable).** Phase 0 creates `.kimiflow/<slug>/STATE.md`; after every phase set `Phase N: open|in-progress|done`. Chat state is not enough: `state-gate` blocks the review-gate call when `STATE.md` is missing.
- **Active Session Contract (not optional once Kimiflow starts).** Non-trivial runs start `hooks/active-run.sh start --run .kimiflow/<slug> --write`; follow-ups stay in that run until explicit exit/abort/park/fail/switch. Close mechanically with `finish|park|fail|abort --write`.
- **Stop criteria always active:** success-stop (gate/verification met), failure-stop (escalate — see phase 5), budget-stop (cap reached → stop + ask). Never loop forever.
- **Subagents do NOT see your context.** Every delegation carries: objective, output format, allowed files/boundaries, the paths of the relevant state files. For reference.md content, pass the path `${CLAUDE_SKILL_DIR}/reference.md` + the exact section names to read — not the text verbatim (verbatim only for a snippet under ~15 lines). Subagents write results to the named paths.

## Phase Files (on-demand)

Phase detail is loaded only when entering that phase. For post-R2 runs, `hooks/active-run.sh start --run .kimiflow/<slug> --write` marks `phase_reads_required: true`; read `phases/PHASES.json`, read the phase file, then record it with `hooks/active-run.sh phase-read --run .kimiflow/<slug> --phase <N> --file phases/<file>.md --write` before crossing the next gate boundary. `clarify-gate.sh` checks through Phase 1, `plan-blocker-gate.sh` through Phase 4, and `finish --write` through Phase 7.

| Phase | File | Always-loaded boundary cues |
|---|---|---|
| 0 Setup, Routing & Scope-Gate | `phases/phase-0-setup.md` | `launcher-status.sh --pretty`; `working-tree-gate.sh`; `active-run.sh`; phase state; scope and verbosity gates. |
| 1 Clarify | `phases/phase-1-clarify.md` | `clarify-gate.sh`; mandatory micro-grill evidence; `Does this match?` / problem/scope gates. |
| 2 Understand / diagnose | `phases/phase-2-understand.md` | `memory-router.sh status`, `MR recall --query-file`, Vault Pulse (all `scope=large` only — small/quick skip recall); Current-State Pulse / Gate; `current-state-gate.sh`; `suggest-affected-sections.sh`. |
| 3 Plan | `phases/phase-3-plan.md` | acceptance criteria, Red evidence for fix mode, cause proof, audit existence-first rules. |
| 4 Plan-gate / approval | `phases/phase-4-review-approval.md` | `plan-blocker-gate.sh`; reviewer lenses; `resolve-review-gate.sh`; pre-build approval stop; build-gate STOP/backlog rules. |
| 5 Implement / fix | `phases/phase-5-build.md` | TDD, named Red-test commit exception, caller-grep before deletion, failure escalation. |
| 6 Verify | `phases/phase-6-verify.md` | goal-backward verification; `red-green-gate.sh`; `lsp-diagnostics.sh`; regression and cold-start checks. |
| 7 Review / commit | `phases/phase-7-review-commit.md` | code-review ensemble; Memory Router & Learning Loop; `CANDIDATE` verification; named-path staging; advisory scans; `MR review-run`; `refresh --changed`; `Status: done`. |

## Always-Loaded Protected Phase Rules

These operative rules stay in the driver until a later approved packet proves an earlier mechanical gate for the target phase. Phase files may elaborate, but this section is always loaded.

- **Phase 2 protected rules:** memory/Vault gaps may record the graceful skip and continue; a plan-blocking unknown → resolve first; in fix mode write Red evidence before changing production code, find AND prove the cause, and if root cause not proven → do NOT fix. Audit mode asks should this exist at all; every deletion needs a repo-wide pre-delete grep plus git-history-freshness, and Caller-grep is a MINIMUM. Vault-save runs only if a vault MCP is connected.
- **Phase 3 protected rules:** dual-plan adoption may take isolated elements only, never structural merges. Plans with unresolved blockers do not send it to reviewers.
- **Phase 4 held rule:** anti-oscillation or cap exhaustion means stop + ask, gate CLOSED.
- **Phase 5 protected rules:** the Red test commit is tests-only and production code never rides along. Deletions require proof; no proof → don't delete. On unclear or likely-guess failures, don't burn a blind second attempt.
- **Phase 6 protected rules:** fix mode runs `hooks/red-green-gate.sh .kimiflow/<slug> --mode fix` before Phase 7, memory promotion, or `Status: done`. Verification discrepancies require re-run the decisive command; any failure routes back to phase 5.
- **Phase 7 protected rules:** add the third when the diff touches hooks, plugins, memory, launcher, APIs/contracts, multiple surfaces, or any high-risk path; reviewers emit `CANDIDATE <SEVERITY> <ref> :: <claim> :: verify=<smallest check>`; BLOCKER/HIGH verification includes an active refutation attempt; gates count only promoted findings, never raw candidates. Advisory scans include `test-weakening-scan.sh` and never silently skip the advisory channel. Commit-gate shows status/diff, then Wait for explicit OK; commits use named paths, no co-author/AI trailer, and `.kimiflow/test-gate` is never staged or committed. Audit commits are one slice at a time; Never batch slices. Learning review uses `--skip "<reason>"` when there is no durable learning; a CLOSED result blocks completion; neither `.kimiflow/` nor repo files store the key; memory proposals never patches skills or writes external notes blindly, approved drafts stay under `SKILL-DRAFTS`. Only after the commit gate and learning review are open may the run set all phases done and `Status: done`.

## Scaling Knobs

Detailed knobs live in `docs/kimiflow-scaling-knobs.md`. Display verbosity is NOT a knob: it is always-on visible-output volume only, never gates, cost, quality, or behavior. Best-of-2 keeps the test oracle authored and committed BEFORE fan-out; candidates stay uncommitted; behavioral evals are never wired into CI.
