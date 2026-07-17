# Changelog

Notable changes to **kimiflow**. Versions track `.claude-plugin/plugin.json`.

## Unreleased

### Changed

- **Explicit prior-fix cues now trigger one targeted local recall at every scope**: when the user says a bug or
  fix existed before, Kimiflow searches at most five current learning/run-history hits, verifies any old strategy
  against the fresh reproduction and current code, and continues on a miss without broad memory/provider loading
  or another user question.

## 0.2.4

The autonomous strategy-fit release: Kimiflow learns from comparable implementations without forcing
unnecessary research or another user-confirmed technical loop.

### Changed

- **Reference Strategy Fit stays bounded and autonomous**: Phase 2 now skips external research when project
  evidence or a proven local regression uniquely determines the correction, compares at most two `pulse` or
  three `focused` references for real plan-changing uncertainty, records compact `adopt|adapt|reject` fit evidence,
  and turns source conflict or research exhaustion into a changed query, local counterfactual, or recovery strategy
  instead of another user-confirmed run.

## 0.2.3

The autonomous solo-developer release: Kimiflow front-loads material decisions, adapts strategy without
babysitting, and keeps a single safe workspace with atomic local checkpoints.

### Changed

- **Schema-4 runs no longer need routine babysitting**: Kimiflow batches real ambiguity up front, explains the
  agreed target in plain language, changes technical strategy autonomously when a review stays blocked, and
  pauses later only for material product, authority, security/privacy, cost, external-access, or irreversible
  decisions.
- **Solo-dev workspace hygiene and atomic local commits are now mechanical**: every run inventories the current
  branch, dirty paths, and all linked worktrees; defaults to the current tree; caps exceptional registered trees
  at one; preserves foreign work; and creates named-path local checkpoints/commits when required for clean-tree
  verification. Push and release remain separately authorized.

## 0.2.2

### Changed
- **Smart feature routing replaces the universal preflight prompt** (`AGENTS.md`, `CLAUDE.md`,
  Claude/Codex skill metadata, README, compatibility and smoke contracts): explicit `Kimiflow` and
  `direct` remain hard overrides; fixes, reviews, refactors, cleanup, docs/config, and small low-risk
  features stay direct, while the agent may automatically enter Kimiflow for substantial feature work
  with material cross-surface, integration, data, security, public-API, architecture, or discovery
  needs. Raw file count alone never triggers the loop.
- **Frontend guidance gains compact product-design safeguards** (`frontend-quality-standard.md`,
  `frontend-quality-qa.md`): task-oriented UI now favors earned familiarity, checks flow and language
  against neighboring features, and repairs design drift at its token, shared-component, or conceptual
  root without adding another gate, dependency, or design payload.

## 0.2.1

The frontend-quality release: feature work can opt into a token-efficient visual quality lane, while the gate now remains reliable across Git layers, nested repositories, recovery retries, and nuanced design intent.

### Added
- **Token-efficient frontend quality lanes** (`frontend-quality-gate.sh`, Phase 0/2/6/7, Claude/Codex skills and focused references): UI features route to compact standard or flagship design guidance only when relevant, then require deterministic visual QA evidence without loading frontend design payload into bug-fix or backend-only runs.

### Fixed
- **Frontend evidence cannot silently go stale** (`frontend-quality-gate.sh`): routing snapshots bind raw worktree bytes plus Git-layer identities, deletions, symlinks, clean-filter changes, and recursively unsuppressed submodule deltas while preserving normal staging of unchanged rendered content.
- **Start, routing, and recovery edge cases fail closed** (`frontend-quality-gate.sh`): prepared-run resume is crash-safe, Git-native commit IDs support SHA-1/SHA-256 and reject malformed values structurally, generic backend paths no longer look like UI, negated polish intent remains standard, and legacy Source-Truth receipts migrate to exact Version-2 identity.
- **Focused regression coverage** (`test-frontend-quality-gate.sh`): 135 assertions cover the new lane and its reviewed lifecycle, path, recovery, OID, submodule, clean-filter, and intent-classification boundaries.

## 0.2.0

The autonomous recovery release: persistent review blockers now make Kimiflow change strategy and continue autonomously instead of asking the user to approve another equivalent run.

### Changed
- **Autonomous recovery is mechanically bound to authority and strategy state** (`clarify-gate.sh`, `active-run.sh`, `resolve-review-gate.sh`): schema-3 Fix Preview approval protects problem/acceptance/mode/scope/risk across technical recovery (including trivial/mode bypasses); typed `await-user --kind` rejects Preview/Commit pauses until recovery is clean; and gate-aware epochs require verified `PLAN.md` baselines, chained recomputed hashes, and a complete source-round ledger.
- **Review caps now change strategy instead of asking for another run** (`resolve-review-gate.sh`, Phase 2/4/5/7, review rubric, behavioral scenario 03): plan/code findings keep immutable global round ledgers while `--epoch-start` scopes anti-oscillation to one strategy epoch; cap/oscillation/reappearance records compact recovery, uses bounded memory/current primary sources and a falsifiable strategy delta, then continues through the existing Stop gate. Only missing authority/external state may await the user; existing one-time Preview and Commit Gates are unchanged.
- **README and demo are current and substantially leaner** (`README.md`, `README.de.md`, `docs/demo/`): the default GitHub README is now a concise English entry point with a separate German version, deep operational detail links to the canonical docs, current Codex install/update commands, and the re-rendered demo reflects adaptive Discovery, scope-aware recall, mode-specific Previews, and evidence-only learning promotion.

## 0.1.67

Linux portability patch for the basis-bound Fix Preview recorder introduced in 0.1.66.

### Fixed
- **Fix approval recording works on GNU/Linux** (`hooks/clarify-gate.sh`): file-mode preservation now queries GNU `stat -c` before BSD/macOS `stat -f`; a fake-GNU regression test guards the option collision that failed the 0.1.66 GitHub Actions runs.

## 0.1.66

The streamlined bug-fix gate release: clear reports proceed directly to diagnosis, one basis-bound Fix Preview replaces two earlier confirmation stops, and the final Commit Gate remains unchanged.

### Changed
- **Bug fixes use one post-diagnosis Fix Preview** (`hooks/clarify-gate.sh`, Phase 1/4): a clear bug report proceeds directly to reproduction and root-cause proof; after internal plan review, one durable cause/fix/scope/risk approval replaces both the old pre-diagnosis confirmation and the generic Build Preview. The final Commit Gate remains unchanged.

### Fixed
- **Fix Preview approval cannot outlive its reviewed basis** (`hooks/clarify-gate.sh`): `--record-fix-approval` fingerprints the problem, diagnosis, plan, acceptance criteria, and relevant state; `--post-diagnosis` now closes automatically after any material basis change instead of trusting manual marker removal.

## 0.1.65

The evidence-guided discovery release: complete intent replaces question quotas, project-first research scales only with real decision gaps, and durable state now fails closed across Discovery and risk-based Build Preview decisions.

### Added
- **Evidence-guided feature Discovery** (`phases/phase-2-understand.md`, `hooks/discovery-gate.sh`): feature runs now choose `none`, `pulse`, or `focused` research depth from concrete decision gaps, default to no research worker, keep external content read-only/untrusted, and mechanically block planning while research, source, scope, or user-decision state remains open.
- **Plain-language Build Preview with conditional Risk Gate** (`hooks/resolve-build-gate.sh`, Phase 4): the user sees what will and will not be built instead of reviewing `PLAN.md`. The project policy is now `risk|always|off` with `risk` as the default; legacy `on` maps to `always`, headless risky work parks, and normal reversible work continues after its preview.

### Changed
- **Intent completeness replaces question quotas** (`hooks/clarify-gate.sh`): small/quick runs confirm behavior, scope, and the user-visible outcome without requiring two or three questions. Existing valid count-based markers remain compatible for prepared runs.
- **Top-owned Decision Triage and bounded research routing** (`reference.md`, Claude/Codex skills): technical gaps return to research, safe reversible HOW uses project-fit defaults, only irreducible product/policy decisions reach the user, and focused research normally uses one evidence worker with at most two independent lanes.

### Fixed
- **Discovery cannot be disabled by malformed new-run state** (`hooks/discovery-gate.sh`): schema-2 feature runs fail closed when the requirement is missing or contradicts mode/scope, while pre-schema runs remain resumable.
- **Build risk is bound to durable state** (`hooks/resolve-build-gate.sh`): Phase 4 reads `Build risk` from `STATE.md`; missing, invalid, or contradictory risk input parks instead of silently continuing.

## 0.1.64

The minimum-complete planning release: research can correct implementation choices without silently growing product scope, dual planning requires a real architectural fork, and review loops stop after a small scope-based repair budget.

### Changed
- **Minimum-complete planning and research scope classification** (`SKILL.md`, Phase 2–4, `reference.md`): current research is classified as `required`, `default`, or `optional`; only verified required constraints may add scope. Plans stay flat, every task/file/abstraction/test maps to an acceptance criterion, reversible HOW decisions use conservative defaults, and reviewers must subtract unsupported complexity before proposing additions.
- **Dual planning now requires a proven material fork** (`phases/phase-3-plan.md`, routing docs): `large` no longer spends two full planners merely because it is large. One top planner is the default at every scope; a second appears only for genuinely different viable architectures or an irreversible public API/data/migration contract, and both remain bound to the same minimum-complete scope.
- **Proportional review loops** (Phase 4/7, behavioral eval scenario 14): normal features and quick/small code review get one repair round; large/audit/release-critical work gets at most two. MEDIUM/LOW never forces plan revision, and plan BLOCKER/HIGH requires evidence tied to intent, current compatibility, project standards, or concrete security/data impact.

### Fixed
- **Revision cap cannot be bypassed by a later clean round** (`hooks/resolve-review-gate.sh`, tests): `round > cap` now closes mechanically before reading findings, while a clean result at the cap still opens. Round numbering remains global across Phase-3 re-entry instead of resetting and recreating the babysitting loop.

## 0.1.63

The review-clarity release: Phase 7 now pins one Git comparison basis for every reviewer, discovers cited specification and project-standard sources explicitly, and keeps Spec/Correctness, Failure/Security, and Standards/Integration reasoning separate without adding default reviewer calls.

### Added
- **Fixed review basis and source discovery** (`phases/phase-7-review-commit.md`, `reference.md`): every review records one validated base/target SHA, exact committed and working-tree diff commands, reviewed commits, and ordered spec/standards sources before spawning. Empty diffs spend no reviewer tokens; missing specs are reported instead of inferred from the implementation.
- **Standards review with an advisory smell baseline** (`reference.md`): documented repository standards now participate in review, while classic code smells remain heuristic and non-gating unless tied to a documented rule or demonstrable correctness/integration impact.

### Changed
- **Axis-preserving, token-neutral review routing** (`SKILL.md`, canonical/Codex render sources, Phase 7, behavioral evals): `spec-correctness` replaces the broad bug-regression lens, `standards-integration` combines project standards with contract/wiring review, and exact cross-axis duplicates are linked in `CODE-REVIEW.md` but promoted only once. Quick still uses one reviewer, small uses two by default, and large/high-risk uses three.
- **Review contract smoke coverage** (`hooks/smoke-install.sh`, `hooks/smoke-install-codex.sh`): both host smokes now assert the fixed basis, separated report axes, and advisory smell contract.

## 0.1.62

The parallel-session safety release: active Kimiflow and red-test Stop gates now apply only to the Codex or Claude session that owns the run. Other sessions in the same project can keep answers visible, inspect the codebase, and create plans; disjoint writes are coordinated through a deterministic path-conflict check, while overlapping or unknown paths stay blocked.

### Added
- **Cross-host parallel-session conflict check** (`hooks/active-run.sh conflict-check`): Codex and Claude sessions can compare intended write paths with another active Kimiflow run and receive `allow_disjoint`, `block_overlap`, or `block_unknown` without using a model call.

### Fixed
- **Active-session hooks are scoped to their owner session** (`hooks/kimiflow_core/active_run.py`, hook manifests, canonical/host docs): an active Kimiflow run now records its Codex/Claude session identity. Unrelated sessions may always read, ask questions, analyze, and create plans; their `Stop` hooks never continue another session's run or replace a completed assistant response. Unknown legacy ownership fails open for conversation output and closed for parallel writes.

## 0.1.61

The portable top-model routing release: the strongest available model now always orchestrates and plans, Codex maps Sol/Terra/Luna to top/balanced/cheap roles, normal implementation moves to the value tier, and review, semantic verification, and risky diagnosis stay top-tier or strong cross-family. Launcher wording and quiet output are also clearer without changing the engine.

### Added
- **Top-model Phase-0 preflight and behavioral eval** (`phases/phase-0-setup.md`, `evals/scenarios/13-top-model-orchestrator.md`): a Codex run exposed as Terra/Luna stops before Phase 0 and asks for Sol; a stronger subagent cannot act as a surrogate orchestrator for a cheaper main session.

### Changed
- **Portable per-role routing contract** (`reference.md`, phase files, canonical/Codex skills): semantic tiers are now `top`, `balanced`, `cheap`, and `cross_family_top`. Current Codex mapping is Sol/Terra/Luna; Terra is the normal bounded implementer, Luna is deterministic support only, and Sol owns orchestration, planning, Phase-2 synthesis, review verdicts, independent semantic verification, and risky diagnosis. Claude keeps its strongest session tier for control and quality while Opus remains a value-tier implementation leaf under Fable sessions.
- **Cross-family quality seats pinned explicitly** (`reference.md`, Codex overlay): Claude-host calls pin `gpt-5.6-sol` with high reasoning and read-only sandboxing; Codex-host calls pin Fable with high effort. Codex `ultra` is excluded inside Kimiflow to avoid nested automatic delegation.
- **Launcher and quiet-output language clarified** (`launcher_status.py`, launcher goldens/demo, README, canonical phase docs): the launcher reports project-map status instead of exposing the internal `quick` bootstrap tier, and `quiet` is explicitly a low-narration control surface with the same gates, artifacts, tests, reviewers, and acceptance standards on Claude Code and Codex.

## 0.1.60

The post-Fable release: under a strongest-tier non-Fable Anthropic session (e.g. Opus 4.8) the four quality seats take `effort: xhigh` and a `small` run carrying a genuine design decision runs the two-planner dual-plan — breadth-plus-effort recovering the depth a single Fable pass gave, at half the token price; the Fable-family conditionals are labelled inert off Fable. Plus the pre-code simplicity ladder now ships in the always-loaded spec, and the global economics telemetry records each run's scope tier.

### Added
- **`scope` in the global economics telemetry** (`hooks/memory_router/economics.py`, spec §12): the anonymous row in `~/.kimiflow/metrics/token-economics.jsonl` now records the run's scope tier (`trivial|small|large|unknown`, parsed from STATE.md like `run_type`) — the existing 40 rows were scope-blind, which made the open "does recall pay off at `large`?" question unanswerable from the ledger. Additive enum inside the existing privacy allowlist; the memory-router parity harnesses strip the documented divergence before their byte comparisons.

### Changed
- **Pre-code simplicity ladder shipped in the spec** (`docs/render/kimiflow/canonical/SKILL.md` → `SKILL.md`, `reference.md`): the always-loaded Simplicity-first rule now names the decision ladder for new code — does this need to exist at all? → stdlib before hand-rolled → native platform features before a new dependency → one line before fifty. Ships with every install on both hosts (the Codex skill reads the canonical `SKILL.md`/`reference.md`), so the anti-over-engineering reflex no longer depends on a user's personal `CLAUDE.md`.
- **Post-Fable strongest-tier session: xhigh quality seats + conditional small dual-plan** (`reference.md` "Model routing (per-role)", `docs/kimiflow-scaling-knobs.md`, `phases/phase-3-plan.md`): under a session whose model is the strongest available Anthropic tier and **not** the Fable family (e.g. Opus 4.8), the four quality seats (planner, implementer, verifier incl. the additive Phase-6 verifier, Phase-2 understanding) now take **`effort: xhigh`** explicitly (was discretionary `high`/`xhigh`), and a `small` run that carries a genuine design/architecture decision (≥2 sensible solution paths OR a new interface/contract) also runs the two-planner dual-plan selection — recovering the breadth a single Fable pass gave, at half the token price. **Advisory only, never a gate**: the second planner adds no plan-reviewer (still 1) and no review-ensemble spawn; a `small` run with no real design decision stays single-planner. The Fable-family conditionals (leaf routing, the failure-security priority swap, the Phase-6 same-family opus fallback) are now labelled **inert off the Fable family** — a documentation clarification, no behavior change. No new gate/hook.

## 0.1.59

The slim-down release: Opus-pinned leaf seats under Fable sessions, four zero-usage subsystems removed (Explore mode, Background Handles, Agentic Readiness, workqueue close-back), memory recall and the Vault Pulse skipped on small runs, the project map reduced to the quick tier, and the core test harness converted to golden snapshots — plus hardening from real-world incidents: the Codex cross-family transport is pinned instead of trusting host defaults, mechanical gate markers are spelled out verbatim for subagents, and the active-session Stop gate learns to yield while the orchestrator is legitimately waiting for a user answer.

### Fixed
- **Golden-snapshot harness made hermetic against host state** (`hooks/test-kimiflow-core-parity.sh`, `hooks/golden/launcher_*.snap`): the seven launcher goldens had frozen developer-host provider state and all failed on CI — an empty `KIMIFLOW_OBSIDIAN_URL` falls back to the default loopback probe URLs (a live local Obsidian REST API answered), and the plain `env` overlay let host-set provider knobs (`KIMIFLOW_OBSIDIAN_MCP_AVAILABLE`) leak `authenticated:true` into the snapshots. Cases now run under `env -i` (only `PATH` and the `LC_ALL=C` pin pass through) with the probe URL pinned to a dead loopback address, and `normalize()` masks the now-derived `cutoff_date` (today minus the stale-after window — it would have rotted the `launcher_full` golden daily). Regenerated goldens changed only in the provider/vault/auth fields and the date mask.

### Added
- **`awaiting_user` state in the Active Session Contract** (`hooks/kimiflow_core/active_run.py`, `hooks/test-active-run.sh`, `hooks/kimiflow_core/tests/test_active_run.py`): new `await-user --run <dir> [--reason <text>] --write` subcommand marks the run as waiting for a user answer at an engine gate (micro-grill, pre-build approval); the Stop gate passes while the flag is set, and the next `prompt-context` call clears it automatically — no more park/restart cycles just to ask the user a question. `status` reports the flag; absent flag = `false` (backward compatible).

### Changed
- **Per-seat model routing → Opus under a Fable session** (`reference.md` "Model routing (per-role)", `docs/kimiflow-scaling-knobs.md`, `SKILL.md`): on a Claude Code host under a **Fable-family session** (Fable 5 + Mythos 5) with per-subagent model selection, the session-model **leaf** seats — implementer, synthesizer, Phase-6 verification, and every same-family review lens — now spawn at per-spawn **`model: opus`** (the next non-Fable Anthropic tier, **half the price**: $5/$25 vs $10/$50 per MTok), reserving the pricier Fable tokens for the orchestrator. **Orchestrator, planner(s), and Phase-2 understanding keep the session model;** the cross-family seats, the `failure-security` lens (already non-Fable), and the smallest-tier lenses are untouched. Advisory routing only — **never a gate**, a **No-Op** off the Fable family (an Opus/weaker session; never escalates a non-Fable session up to Fable), and the seats inherit the session model on the Codex host (no Opus tier) or any host without per-subagent selection. No new gate/hook.
- **Cross-family transport hardened** (`reference.md` "Cross-family transport"): review/diagnosis/verify seats now pin `codex exec -s read-only … </dev/null` explicitly — the host sandbox default is never assumed (a local `~/.codex/config.toml` can override it; a reviewer call executing write commands was a real incident), stdin must be closed (unredirected `codex exec` blocks on stdin), and every seat call or malformed-retry is a fresh `codex exec` session (never resume — cross-call context bleed is an observed failure mode).
- **Gate markers spelled out for subagents** (`phases/phase-0-setup.md`, `phases/phase-2-understand.md`, `phases/phase-3-plan.md`): the literal `Affected files:` line (plan-blocker gate + staleness tracking) and the current-state evidence markers (`source_type:` enum, `source_url: https://…`, `Status: checked`) are now named verbatim where plans, STATE.md, and evidence files are authored — planner subagents must not have to guess mechanical marker syntax.
- **Affected-header parsing unified** (`hooks/kimiflow_core/active_run.py`, `hooks/plan-blocker-gate.sh`, `hooks/test-plan-blocker-gate.sh`): `affected_paths` now accepts the same five headers as the plan-blocker gate (`Affected files|Affected paths|Files|Paths|Touches`), both parsers are case-insensitive, and staleness tracking falls back to `PLAN.md` when `STATE.md` carries no affected line (matching the gate's STATE-or-PLAN acceptance) — previously a plan using `Files:`/`Paths:`/`Touches:` or declaring paths only in `PLAN.md` passed the gate but was invisible to staleness tracking, wedging `finish` with `risk=unknown`. Both parsers carry keep-in-sync cross-references.
- **Existing feature check simplified to a read-only Phase-7 ensemble run over the named path/current diff; standalone FEATURE-CHECK machinery removed (zero usage)** (`reference.md` "Existing feature check", `docs/render/kimiflow/canonical/SKILL.md`, `docs/render/kimiflow/overlays/codex.md`, `README.md`, `docs/kimiflow-scaling-knobs.md`, `hooks/kimiflow_core/launcher_status.py`, `hooks/test-launcher-status.sh`): `--verify-feature <feature-or-path>` / the `review` alias now runs the normal Phase-7 code-review ensemble read-only over the named path or current diff — same lenses, same `CANDIDATE`→verify→promote mechanic, cross-family as usual — with findings in `findings/`/`CODE-REVIEW.md` under a run slug. The dedicated `behavior`/`wiring`/`contract`/`state-data`/`tests`/`docs-security` lens set, the `FEATURE-CHECK.md` artifact, the `r*-feature-check.md` finding routing, the smallest-tier lens seat, and the launcher `.feature_checks` count + `review_feature_findings` action are gone. No code edits; confirmed findings can seed a fix/improve run.
- **Quick-path radicalized — `small`/`quick` skip Phase-2 recall and the Vault Pulse** (`phases/phase-2-understand.md`, `reference.md` "Memory recall", `docs/render/kimiflow/canonical/SKILL.md`, `docs/render/kimiflow/overlays/codex.md`, `README.md`, `hooks/smoke-install.sh`, `hooks/smoke-install-codex.sh`): recall (`memory-router.sh status` + `MR recall`) and the Vault Pulse now run on **`scope=large` only**; small/quick runs go straight to codebase + web. Telemetry showed a thin payoff on small tasks — **24/40 runs recalled 0 hits and only 29/251 recalled hits were ever used**, so on small work the recall cost more than it saved. **Unchanged:** the **Current-State Pulse** still runs at every scope (cheap, documented freshness protection against stale model knowledge); the memory **engine** — the Learning Loop and the Phase-7 `review-run` — still runs at every scope; `large` runs are untouched; and vault-save is unchanged (still gated only on a connected vault MCP). The orchestrator honors the scope condition per the docs — no new gate, hook, or code change.
- **Core test harness converted from baseline-parity to golden snapshots** (`hooks/test-kimiflow-core-parity.sh`, `hooks/golden/` — 33 checked-in snapshots): the harness no longer materializes the frozen pre-R1 Bash baseline (`git archive 72282e6`) and no longer strips the ever-growing list of removed launcher fields; each case now locks exit code, normalized stdout/stderr, and post-run file state (mode + normalized content hash) against a golden under `hooks/golden/<case>.snap`. Same 33 cases, no network/archive needed, `LC_ALL=C` pinned for portable file ordering, and a golden guard fails the suite on orphaned or missing snapshots. Regenerate after an intentional behavior change via `UPDATE_GOLDEN=1` and review `git diff hooks/golden/` before committing.
- **Stable Codex-wrapper coverage made explicit** (`hooks/install-codex-hooks.sh`, `hooks/smoke-install-codex.sh`, `reference.md`): the stable installer deliberately wraps only the four enforcement gates (covering 5 of the 6 plugin-hook commands); `map-staleness-nudge.sh` is plugin_hooks-only — a systemMessage-only advisory has no enforcement effect on the stable Codex hook surface, where staleness surfacing is model-driven via `project-map-status.sh`. The codex smoke now asserts exactly this coverage and fails on drift (a new unwrapped enforcement hook).
- **Project Map reduced to the `quick` tier — `standard`/`deep` retired** (`reference.md` "Project Map Bootstrap", `docs/render/kimiflow/canonical/SKILL.md`, `docs/render/kimiflow/overlays/codex.md`, `phases/phase-0-setup.md`, `README.md`, `hooks/smoke-install.sh`, `hooks/smoke-install-codex.sh`, `hooks/test-launcher-status.sh`): `--project-map` now takes `<quick|skip>`. `quick` is the single bootstrap tier — it writes the full local `.kimiflow/project/` artifact set (`INDEX.json`, `FACTS.jsonl`, `CODEBASE.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`, `TESTING.md`, `FLOWS.md`, `OPEN-QUESTIONS.md`) and is kept current by `project-map-status.sh refresh --changed` after commits. The `standard`/`deep` depth tiers and the depth-comparison table are gone: map **content** went stale (frozen since 2026-06-28) while the index kept updating, so the deep-tier maintenance loop never worked as designed; `quick` + `refresh --changed` is the maintained path. **Unchanged:** the staleness helper, `suggest-affected-sections.sh`, `map-staleness-nudge.sh`, the INDEX.json/section mechanics, `coverage`-based Phase-2 depth (`compressed|targeted|full`), the `improve` lens, `refresh --changed`, and the launcher `project_map` snapshot. Migration: existing `standard`/`deep` maps stay readable and valid — the launcher still reports their stored `scan_depth` — they are just no longer regenerated per tier. No code/gate/hook change: `project-map-status.sh` never branched on tier, and `launcher_status.py` passes `scan_depth` through unchanged.

### Removed
- **Explore mode (`--explore`, the 🧭 divergence phase, and the open-ended "explore a few directions?" offer)** (`reference.md`, `phases/phase-1-clarify.md`, `phases/phase-0-setup.md`, `SKILL.md`, `docs/kimiflow-scaling-knobs.md`): the opt-in pre-Clarify divergence front-end is gone — zero usage across all runs since introduction; divergence lives in normal clarify questioning. The `--explore` flag, the offer-on-detect, the 🧭 phase marker, `EXPLORE.md`, and the `Phase E (explore)` STATE line no longer exist; open-ended feature requests route straight into Phase 1 Clarify.
- **Background Handles (the `.kimiflow/background/` handle registry)** (`hooks/background-run.sh`, `hooks/kimiflow_core/background_run.py`, `hooks/test-background-run.sh`, `reference.md`, `SKILL.md`, `README.md`, `hooks/kimiflow_core/launcher_status.py`, `.claude-plugin/*.json`, `.codex-plugin/plugin.json`): the local registry for long read-only/draft work is gone — **no runtime store was ever created across any run** (`.kimiflow/background/` never existed). The `start/list/status/update/collect/cancel/mark-stale` helper and its Python port, the launcher `.background` snapshot field and `collect_background_handles` action, and all handle documentation no longer exist; untracked-diff drift is already covered by the working-tree gate.
- **Agentic Readiness Layer (`status`/`gate`/`packet`, `AGENTIC-AUDIT.jsonl`, context packets)** (`hooks/agentic-readiness.sh`, `hooks/test-agentic-readiness.sh`, `reference.md`, `SKILL.md`, `README.md`, `phases/phase-7-review-commit.md`, `hooks/kimiflow_core/launcher_status.py`, `.claude-plugin/*.json`, `.codex-plugin/plugin.json`): the local preflight readiness signal (`guided`/`governed`/`autonomous`, blockers/warnings, bounded sanitized context packets, launcher `.agentic_readiness`) is gone — **packets were produced only in its own build run, never in production use**, and it primarily gated Background/fan-out trust, which is removed too. The dirty-working-tree check it duplicated stays in the working-tree gate.
- **Workqueue close-back (the `improvements-status.sh` helper, its Stop-hook staleness nudge, and the launcher `.improvements` count)** (`hooks/improvements-status.sh`, `hooks/kimiflow_core/improvements_status.py`, `hooks/improvements-staleness-nudge.sh`, their tests, `hooks.json`, `hooks/hooks.json`, `reference.md`, `docs/render/kimiflow/canonical/SKILL.md`, `docs/render/kimiflow/overlays/codex.md`, `phases/phase-7-review-commit.md`, `hooks/kimiflow_core/launcher_status.py`, `README.md`, `docs/codebase.md`): the Phase-7 `mark-done`/`reopen` close-back that stamped built `IMPROVEMENTS.md`/`FINDINGS.md` slices with an in-place `kimiflow:queue-done` marker is gone — **zero usage across all runs**. The `list`/`mark-done`/`reopen` helper and its Python port, the once-per-day staleness nudge (removed from both Stop manifests), the launcher `.improvements` snapshot field + `improvements_open` count + `review_improvements` action, and the Phase-7 step-8a close-back are removed. **The local project map is untouched:** the `improve` lens still writes `IMPROVEMENTS.md` proposals and the launcher still surfaces open **findings** — only the close-back reader is gone. Migration: any existing `<!-- kimiflow:queue-done … -->` marker in a local `.kimiflow/project/IMPROVEMENTS.md`/`FINDINGS.md` stays in place harmlessly — the launcher findings counter still honors it (a marked finding stays out of the open count); only the writer helper and the improvements count are gone. No migration script.

## 0.1.58

Gemini joins the cross-family reviewer chain: on a Claude Code host one review lens can now route to Gemini (via the Antigravity `agy` CLI) as a second independent family, ordered after Codex and before same-family — with a per-repo order token and a hardened, sandboxed transport.

### Added
- **Gemini as a second cross-family reviewer** (`reference.md` "Model routing (per-role)", `docs/kimiflow-scaling-knobs.md`, new `evals/scenarios/12-cross-family-order.md`): on a Claude Code host the cross-family review seat is now an **ordered chain** — default Codex → Gemini (Antigravity `agy`, pinned `Gemini 3.5 Flash (High)`) → same-family. `.kimiflow/cross-family` gains an optional **order token** (`auto <order>`, an exact try-list, e.g. `auto gemini,codex` or `auto gemini` to skip Codex where it times out), also settable via `--settings`. The Gemini transport is **`agy -p … --sandbox`** with a mandatory no-tools instruction — unconstrained, `agy` is an agentic CLI that scans the filesystem and pollutes stdout; sandboxed it returns a clean final message. A **quota/usage-limit/rate-limit** response is now an explicit fallback trigger, and a **grammar-validity backstop** (any non-`FINDING`/`NONE` output = failure, never a result) guarantees a limit notice or tool-activity stream is never mis-persisted as a review. The `large` Phase-6 verifier prefers the Gemini tier (wide context); the `failure-security` off-Fable routing now names either non-Fable family. Advisory routing only — no new gate/hook, graceful degradation when a CLI is absent.

## 0.1.57

Model-aware routing: kimiflow's per-role routing now accounts for model-family behaviour differences, not just tier + family.

### Added
- **Model-aware routing** (`reference.md` "Model routing (per-role)", `docs/kimiflow-scaling-knobs.md`, new `evals/scenarios/11-refusal-as-fallback.md`): a **refusal-shaped final message** from any seat is now a cross-family fallback trigger — recognized by the *shape* of the message, since the transport exposes no API status field; the **`failure-security` lens routes off the Fable family** (Fable 5 + Mythos 5) when another family is available and takes priority for the single cross-family review lens under a Fable session (no second seat, no budget change); and per-seat **`effort`** is documented as an advisory allocation dimension (`high`/`xhigh` for the quality-deciding roles, `low` for narrow read-only lenses). Advisory-only and degrades gracefully; no new gate or hook. A new out-of-CI eval scenario pins the refusal→fallback behaviour.

## 0.1.56

Audit-hardening plus the R0-R4 rebuild: a 7-lens adversarial baseline audit with every confirmed finding fixed test-first; the five large bash hooks ported to a tested Python core (`kimiflow_core`); `SKILL.md` inverted to a thin always-loaded driver with on-demand phase detail; both host skills rendered from one source; release byte budgets; and an independent merge review of the rebuild branch with all BLOCKER/HIGH findings fixed.

### Fixed
- **Release launcher-budget check is hermetic:** `release-consistency-check.sh` now runs launcher output budgets with an isolated temp repo/home and counts stdout from a file, so user-local `KIMIFLOW_HOME` metrics cannot make releases fail spuriously and trailing newlines are counted.
- **Phase-read driver deadlock:** post-R2 runs auto-enable `phase_reads_required`, but the always-loaded workflow did not tell the orchestrator to write `PHASE-READS.json` before `clarify-gate`, `plan-blocker-gate`, or `finish`. `SKILL.md` and `reference.md` now name the `active-run.sh phase-read ... --write` path and the phase boundaries it must satisfy.
- **Hook manifests quote `${KIMIFLOW_PLUGIN_ROOT}` expansions** (`hooks/hooks.json`, Codex `hooks.json`): an install path containing spaces word-split the hook commands (exit 126/127), silently **failing open** every PreToolUse gate. New `hooks/test-hooks-json.sh` runs each manifest command from a spaced plugin root.
- **`active-run.sh` no longer blocks every prompt when `jq` is missing:** the `prompt-context`/`stop-gate` entry points degraded from exit 2 (which froze all sessions in all repos) to exit 0 (context/nudge skipped); CLI subcommands still require jq.
- **`plan-blocker-gate.sh` audit-mode deadlock:** audit runs never produce PLAN/ACCEPTANCE, but the gate demanded them. An audit profile (mode from STATE.md, fallback AUDIT-INTENT ∧ ¬PLAN) now requires AUDIT.md path evidence + affected paths + clarify recheck instead.
- **`resolve-review-gate.sh` cross-phase isolation:** anti-oscillation/reappeared checks globbed `r<N>-*.md` across ALL lenses, so Phase-4 leftovers masked genuine Phase-7 oscillation (a mandatory stop was suppressed). Previous-round checks are now `--expect`-scoped; `round`/`cap` are base-10-normalized (no octal crash on `08`).
- **`commit-secret-gate.sh` bulk-add bypasses closed:** `git add ./`, `git add :/`, and `:(top)` pathspecs (whole-tree synonyms) now hit the bulk-stage block, including `-A` flag clusters and quoted forms.
- **memory-router path traversal (`rows.py`):** `../`-evidence refs escaped the repo-root check, so out-of-repo files (e.g. `/etc/hosts`) were hashed into `evidence_fingerprints`. Paths are now lexically normalized before the root check; escaping refs map to `OUTSIDE_REPO` (spec §12).
- **memory-router rewrite data loss (`writes.py`/`store.py`):** the status=current full-rewrite dropped blank/malformed lines from `LEARNINGS.jsonl` that the append path preserved. The rewrite now keeps unparseable lines verbatim, in place (spec §12).
- **Smoke expectations aligned with the hardened manifests and B1 wording** (`hooks/smoke-install.sh`): the closeback-nudge manifest greps now expect the quoted `${KIMIFLOW_PLUGIN_ROOT…}` forms introduced by the fail-open fix, and the SKILL.md check greps `Current-State Pulse / Gate`. The smoke had silently reported 3 failures since those fixes landed; B4's per-commit smoke loop caught it.
- **memory-router security-gate scope (`writes.py`/`rows.py`):** the gate scanned only `summary` — injection phrases or hidden unicode in `topic`/`evidence` passed through, and bare secret values kept `sensitivity=normal`, making such rows vault-sync candidates. The gate now scans summary+topic+evidence (newline-joined, no cross-field matches), and a minimal secret-value pattern class (AWS key ids, PEM headers, GitHub/Slack tokens, long `key=value` literals) forces `sensitivity=security` — recorded locally, quarantined from sync (spec §12).

- **Independent merge review of the rebuild branch — all BLOCKER/HIGH findings fixed:** protected always-loaded rules that the R2 movement had relocated or deleted (incl. the pre-commit "Wait for explicit OK", the no-AI-trailer prohibition, the agent-budget clauses, and the phase-colors legend) are restored verbatim in `SKILL.md`; phase manifests resolve from the plugin root (`KIMIFLOW_PLUGIN_ROOT`), so phase-read enforcement works in consumer installs and foreign `phases/PHASES.json` files can no longer activate or deadlock the gates; `clarify-gate`/`plan-blocker-gate` honor `ACTIVE_RUN.json` in addition to the STATE.md marker; both smokes gained a consumer-shaped phase-read scenario; all five `kimiflow_core` shims guard missing `python3` per the hook degrade contract; launcher STATE parsing is case-insensitive (spec §12); valid-but-non-dict JSON no longer crashes project-map/launcher/background paths; `finish` passes memory-router stderr through; the parity harness now diffs post-run file state and covers the previously missing entrypoints.

### Added
- **`kimiflow_core` R1 foundation:** added the stdlib Python package skeleton, shared contracts/path/state/atomic helpers, unit wrapper, old-vs-new parity harness, and divergence-ledger spec for the large-script rebuild before any production shim cutover.
- **`launcher-status.sh --full`; the default output is now the compact first screen.** Without the flag, the heavy arrays (`runs.items` — 13K+ chars on run-heavy repos — and `background.items`) and the full `memory` object are omitted; all counts, `memory_summary`, `maintenance`, and the `.launcher` block are unchanged. The snapshot is still computed in full — the trim is serialization-only, so maintenance reasons and the `.launcher` status stay byte-identical. Drilldown prose in `reference.md` points at `--full`.

### Changed
- **R2 prose inversion complete (post-review state):** the root `SKILL.md` is a ~16K always-loaded driver; non-protected phase detail loads from `phases/*.md` with phase-read verification, expanded scaling rules live in `docs/kimiflow-scaling-knobs.md`, and protected CORE-ALWAYS rules stay in the driver (restored after the independent merge review). Release budgets enforce a 17K root skill ceiling.
- **Rebuild program planning:** added the audited R0-R4 rebuild program and R1 detail plan for the `kimiflow_core` port, including mandatory old-vs-new parity, multi-target invariant preservation, and no-open-BLOCKER/HIGH plan-audit gates before implementation.
- **R2 prose-inversion planning:** added the audit-clean R2 detail plan for rule inventory, corpus-backed invariant targets, phase-read enforcement, and explicit user approval before any protected-rule movement; strengthened the current invariant check with the alias no-code smoke lines and stronger negative needles.
- **R2.1 rule inventory:** added the immutable protected-rule corpus and full `SKILL.md` line-coverage inventory, with no prose movement and no approval-consuming rule relocation.
- **R2.2 invariant target map:** replaced the fixed-target invariant grep with a corpus-joined target-map checker plus fixture coverage for missing IDs, unsafe test-only targets, target-map needle overrides, and corpus strong-needle weakening.
- **R2.3 phase-read enforcement:** added the on-demand phase manifest skeleton, run-local `PHASE-READS.json` hash records, Active Run phase-read commands, opt-in start markers, clarify/plan-blocker phase-read gates, and finish-time blocking for post-R2 runs.
- **R2.4 approval packet:** generated the explicit pre-movement approval packet for gate-backed protected-rule relocation, with phase/smoke/reference movement deferred and no `SKILL.md` / `reference.md` prose moved.
- **R2 Group B target movement:** after explicit approval, moved the gate-backed invariant targets from `SKILL.md` to their production hook/code files, retaining concise driver pointers and target-map verification paths.
- **R3 single-source skill rendering:** added `kimiflow_core.render` and render sources under `docs/render/kimiflow/`; `release-consistency-check.sh` now re-renders the committed Claude/Codex skill files and fails on drift.
- **R4 byte budgets:** `release-consistency-check.sh` now enforces ceilings for always-loaded skill prose, per-phase detail files, and launcher default/pretty output, with fixture tests for oversized prose and output-budget assertions in the launcher test.
- **R4 exit-audit tightening:** moved R2 invariant targets for Python-ported helpers from Bash shim comments into their production Python modules, switched skill rendering to canonical source + host overlay naming, fixed render drift checks so they never overwrite uncommitted drift, and aligned the Codex Current-State spelling with the canonical workflow.
- **`active-run.sh` now runs through `kimiflow_core`:** active-session status/start/items/revalidation/finish/park/fail/abort plus prompt-context and stop-gate hook paths keep their public contract through the Active suite and expanded parity cases; hook entrypoints still degrade to exit 0 without `jq`, while CLI subcommands still hard-fail without it, and mutating `--write` calls now fail closed on explicit invalid roots (spec §12).
- **`background-run.sh` now runs through `kimiflow_core`:** start/list/status/update/collect/cancel/mark-stale keep their public contract through the Background suite and expanded parity cases; mutating `--write` calls now fail closed on explicit invalid roots, and malformed handle ids stop at the unsafe-id error instead of falling through to a misleading secondary lookup (spec §12).
- **Remaining small Bash gates now source `kimiflow-lib.sh`:** `clarify-gate.sh`, `plan-blocker-gate.sh`, and `agentic-readiness.sh` share the canonical Bash `STATE.md` value parser / root resolver where applicable, with expanded old-vs-new parity over markdown-formatted state fixtures.
- **`improvements-status.sh` now runs through `kimiflow_core`:** the workqueue close-back helper keeps its CLI contract through the old-vs-new parity harness, with mutating commands now failing closed on an explicit invalid `--root`.
- **`launcher-status.sh` now runs through `kimiflow_core`:** the read-only launcher snapshot, compact default serialization, `--full`, `--pretty`, plugin-cache detection, Project Map, Memory, Active Session, Background Handles, and Agentic Readiness surfaces keep their public contract through the Launcher suite and expanded parity cases.
- **`project-map-status.sh` now runs through `kimiflow_core`:** status, coverage, refresh, `refresh --changed`, and `index-symbols` keep their public CLI contract through the parity harness; mutating INDEX writes are now same-directory atomic installs with mode `0600` and no false `REFRESHED`/`SYMBOLS` success line after failed persistence (spec §12).
- **SKILL.md compacted (two passes, 60,463 → 53,277 bytes) with a mechanical preservation contract:** pass 2 (sentence-level, deletions only) confirmed the honest floor — the remaining text is ~85% protected rules (gate commands, STOPs, fail-closed rules, prohibitions per the invariants artifact), so the original ≤30K goal is not reachable without rule loss; the real per-run token cuts come from the launcher/reviewer/commit-hygiene levers above. Duplicate enumerations, restated reference prose and the 4K learning-loop line were reduced to their operative cores + pointers; every gate command, STOP, fail-closed rule and prohibition is enumerated in `docs/superpowers/plans/2026-07-02-token-restructuring-invariants.md` and verified by a needle grep-check (`…/2026-07-02-invariant-check.sh`, validated against the pre-compaction file first). Reviewer spawn contract tightened: reviewers no longer read `reference.md` — each spawn prompt inlines its lens definition + the FINDING/CANDIDATE grammar incl. the file-form constraints the fail-closed resolver enforces; lens A/B definitions are now canonical in the "Review rubric" (additive), Phase 7 gained the orchestrator rubric-read step.
- **Commit-secret-gate maintainer prose moved out of the instruction path** (`reference.md` "Commit hygiene" → new `docs/commit-secret-gate.md`): enforcement mechanics, the full pattern deny-list, parsing boundaries and residual gaps live in the doc; the reference section keeps the operative rules (red-test exception, 6 commit rules, hook activation scope, secret-content-scan advisory, hygiene-backstop bottom line) plus pointers. The LSP-advisory paragraph became a pointer to "Verification" (single copy, `KIMIFLOW_LSP_MAX_COMMANDS` detail folded there).
- **Flow-prose coherence fixes** (SKILL.md, reference.md): the `full` alias forces the pre-build approval stop even with `build-gate off`; Phase 7 stages named paths *before* the advisory scans (they previously read an empty staged diff); the Phase-5 resume path runs through the working-tree gate (own reference section); the Phase-5 red-test commit is the single defined exception to the commit-hygiene rule; best-of-2 candidate failure degrades to best-of-1 (the implementer seat never substitutes same-family); audit-mode reviewers receive `AUDIT-INTENT.md` + `AUDIT.md`; `quick` is defined as one `bug-regression` lens + advisory scans; phantom "split promoted files" wording removed and Current-State Pulse/Gate pointers aligned.
- **CI test discovery** (`.github/workflows/ci.yml`): the 19 hard-coded test steps are replaced by a discovery loop over all `hooks/test-*.sh` (production hooks excluded), so new suites gate CI automatically; `shellcheck --severity=error` is a hard gate.

## 0.1.55

Ship **calm launcher status UX**.

## 0.1.54


Agentic quality upgrade: kimiflow now uses model diversity, test-oracle selection, and independent verification by default where cheap — closing review blind spots when the session model reviews its own family's output.

### Added
- **Per-role model routing** (`reference.md` "Model routing (per-role)"): session model takes planner/implementer/verification seats, a cross-family CLI takes one review lens per gate, the smallest tier takes narrow read-only lenses; pinned transport (`codex exec --output-last-message` on Claude Code, `claude -p` on Codex), explicit timeouts, sticky same-family fallback, and a project-local `.kimiflow/cross-family` `auto|off` opt-out (also settable via `--settings`).
- **Dual-plan selection at `large`** (Phase 3): two independent planners with distinct framings (minimal-first vs risk-first, one cross-family when available); selection-first synthesis — the losing approach becomes the recorded "Considered alternatives" entry.
- **Best-of-2 auto-offer** (pre-build gate): at `large` with a fully test-encoded acceptance set and a cross-family CLI available, the shown pre-build summary offers two candidate implementations in parallel worktrees judged by the test oracle; candidates never commit — the oracle is committed in the main worktree before fan-out and the winning diff goes through the normal commit-gated path.
- **Additive independent verifier at `large`** (Phase 6): an implementer-blind verifier re-derives the goal-backward sweep and tries to falsify "done" claims; discrepancies are adjudicated by the orchestrator re-running the decisive command — an unverified claim never steers control flow.
- **Cross-family escalation step** (Phase 5): after two failed fix attempts, the failure evidence goes to a bounded cross-family diagnosis call; its hypothesis is candidate-only.
- **Refutation requirement** (Phase 7): BLOCKER/HIGH candidates must survive an active refutation attempt before promotion — false blockers no longer burn fix rounds.

### Changed
- **Cross-family review is now the default, not a knob:** one plan-gate lens and one code-review lens route to a different model family whenever a cross-family CLI is available (scope ≥ `small`); external reviewer output is persisted verbatim as the lens's findings file with an exhaustively defined malformed-retry/fallback path — the fail-closed resolver stays the only grammar authority.
- **Agent budget disambiguated:** the ~5–10 automatic budget applies per fan-out decision (not cumulatively per run); `large` runs disclose their expected ensemble at the scope announcement and in the pre-build summary's new "Knobs" line.
- **Resume re-approval:** resuming a `backlog` run into Phase 5 re-presents the pre-build summary when the build-gate is on, so deferred plans get the same approval as direct runs.
- Scaling-knobs heading reworded (defaults scale with scope); README cost notes updated (EN/DE).

## 0.1.53

A correctness fix for the project-map hook: `refresh --changed` no longer crashes on large deltas under macOS Bash 3.2, and `test-project-map-status.sh` is now a CI hard gate.

### Fixed
- **`project-map-status.sh refresh --changed` no longer crashes on large deltas.** `do_refresh_changed` recomputed the per-section prefix/member attribution *inside* the per-changed-path loop, spawning `O(changed-paths × sections)` process substitutions. On macOS Bash 3.2 a delta with many new unmapped files (e.g. several sessions' worth of new docs) exhausted file descriptors and died with SIGTRAP (exit 133) — so the Phase-7 auto-refresh and the Stop-hook map-staleness nudge's recommended `bring-current` path silently failed. The attribution is now precomputed once and matched in pure shell (zero subshells in the hot loop); behaviour is unchanged (longest-prefix-wins, ties resolve to the first section). The now-orphaned `section_owns`/`longest_prefix_len` helpers were removed.

### Changed
- **`hooks/test-project-map-status.sh` is now a CI hard gate**, with a new regression test for the crash above. It previously ran only locally and as a `bash -n` smoke check, so the `refresh --changed` path was never exercised in CI.

## 0.1.52

The `memory-router` hook is now powered by the Python (stdlib) port. `hooks/memory-router.sh` is a thin shim that execs `python3 -m memory_router`, and the ~4400-line Bash implementation has been removed. The CLI contract is unchanged — every subcommand was ported byte-for-byte and grounded against the pinned `kimiflow--v0.1.50` Bash.

### Changed
- **memory-router runtime cut over to Python.** `hooks/memory-router.sh` now execs the stdlib `hooks/memory_router/` package across all 13 subcommands (`classify`, `index`, `status`, `curate`, `record`, `recall`, `history`, `metrics`, `verify-run`, `consolidate`, `propose`, `review-run`, `provider`). The Bash logic is deleted; behaviour is byte-for-byte identical — verified by the parity harness, the full Python test suite (run under system `python3` 3.9.6), and a direct shim spot-check (`status`/`verify-run`/`metrics`/`provider`/`classify`/`--help`/unknown-command all identical to the pinned Bash).
- `hooks/test-memory-router-unit.sh` now runs the **full** `memory_router` test suite via discovery (was: three foundation modules), so all of it gates CI and releases.

### Added
- **Runtime requirement: `python3` >= 3.9** for the memory-router hook (documented in `COMPATIBILITY.md`). The previous Bash runtime already required `jq`.

### Removed
- `hooks/test-memory-router.sh` — the legacy Bash-implementation unit test, superseded by the Python suite + the parity harness. Its three Bash-only assertions (a `curl` stub and `openssl`/`shasum`-absent stubs) tested stdlib divergences (`urllib`/`hashlib`) that no longer apply.

## 0.1.51

Additive Python (stdlib) port of the `memory-router` hook, built and verified behind the scenes. The Bash `hooks/memory-router.sh` stays the active runtime — this release ships the new `hooks/memory_router/` package and its test suite alongside it, with no cutover and no behaviour change yet.

### Added
- **`memory_router` Python package** — a stdlib-only port of `memory-router.sh`, grounded byte-for-byte against the pinned `kimiflow--v0.1.50` Bash. Wired subcommands: `classify`, `index`, `status`, `curate`, `record`, `recall`, `history`, over the full read/write/recall stack — bounded `MEMORY.md`/`USER.md` writers, the learning-row write path + security gate, the `RECALL.sqlite` FTS5 engine + index builder, the summary aggregators (usage/economics/lifecycle/global-efficiency), the provider/vault status chain, and the `MEMORY-USAGE.json` metrics writer. Shared layers: jq-faithful JSON serialization, atomic IO + lenient readers, and row/path/text/clock primitives.
- **Parity + unit test suite** (`hooks/memory_router/tests/`) including harnesses that shell to the pinned Bash for byte-for-byte verification, gated into CI and the release loop.

### Fixed
- **memory_router parity hardening**: UTF-8-tolerant `word_count_file`; newline-faithful `--input`/parity reads; location-independent parity launch (`PYTHONPATH` + `-m`); a bash-3.2 empty-array false-green under `set -u`; and a Bash-style unknown-command error (stderr + exit 2).

### Changed
- Planning + handoff docs for the port (`docs/superpowers/`), the CLI design spec, and `.gitignore` for Python bytecode + SDD scratch.

## 0.1.50

ShellCheck cleanup across the hooks: a real `local` path-derivation bug plus dead `case` patterns, unused variables, and two error-level parsing ambiguities.

### Fixed
- **ShellCheck hygiene across `hooks/`**: split compound `local` declarations so derived path variables
  (`state`/`file`/`project`/`salt_file`) read the just-bound `$1`/`$2` instead of a masked outer value
  (9× SC2318 latent bug in `active-run.sh`, `agentic-readiness.sh`, `memory-router.sh`); dropped dead
  `case` alternatives `*routes*` / `migrations/*` in `project-map-status.sh` (strict subsets of `*route*`
  / `*migration*`, behaviour identical — SC2221/SC2222); removed the unused `pretty` variable in
  `clarify-gate.sh` / `plan-blocker-gate.sh` (the `--pretty` flag stays an accepted no-op) and the unused
  `handoff` in `background-run.sh` (SC2034); and disambiguated `$((` → `$( (` in
  `test-commit-secret-gate.sh` / `test-lsp-diagnostics.sh` (SC1102, error-level). Repo-wide error-level
  ShellCheck items: 2 → 0; all hook test suites stay green.

## 0.1.49

Close the local workqueue loop: built `IMPROVEMENTS.md`/`FINDINGS.md` slices get marked done so the launcher stops counting them as open.

### Added
- **Workqueue close-back** (`hooks/improvements-status.sh` + tests): a `list` / `mark-done <id>` / `reopen <id>` helper
  that marks a built slice from `.kimiflow/project/IMPROVEMENTS.md` (`## Priorisierte Slices`) or `FINDINGS.md`
  (`## Offen`) done via an idempotent in-place `<!-- kimiflow:queue-done -->` marker (stable slug/token ids, atomic
  write). The `hooks/launcher-status.sh` counter gains an optional done-marker argument — with a `length>0`
  backward-compat guard — and no longer counts marked slices as open. A non-blocking Stop-hook nudge
  (`hooks/improvements-staleness-nudge.sh`, registered in both `hooks.json` and `hooks/hooks.json`) reminds at most once
  per day, and only when a run just completed while open slices remain. Documented as Phase-7 step 8a
  "Workqueue close-back" in `SKILL.md`, `skills/kimiflow/SKILL.md`, and `reference.md`, with Claude + Codex smoke
  assertions and CI unit tests.

## 0.1.48

Release-hygiene check, consistent capability display, and project-map outputs framed as a local workqueue.

### Added
- **Release version-consistency check** (`hooks/release-consistency-check.sh` + test): a manual pre-release helper
  that verifies one version across `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`,
  `.claude-plugin/marketplace.json`, `COMPATIBILITY.md`, and a `## <ver>` `CHANGELOG.md` heading (manifest version
  fields without a value are skipped). Wired as a CI unit test — not a release gate.
- **Capability-display sync + drift guard**: the four core capabilities (feature/fix loop, project intelligence,
  repo docs, local findings) are now named consistently across `README`, the Claude plugin/marketplace
  descriptions, and the Codex `shortDescription` surfaces, with per-field smoke assertions that fail if a
  capability is dropped (README via a delimited capabilities block; Codex per `shortDescription`).

### Changed
- **Project-map outputs documented as a local workqueue**: `reference.md` and `SKILL.md` now describe
  `FINDINGS.md`/`IMPROVEMENTS.md`/`DOCS-PLAN.md` as an actionable local workqueue — findings/improvement slices are
  launcher-surfaced and picked up by later fix/build runs; `DOCS-PLAN.md` is the docs-run output — not a static report.

## 0.1.47

Keep the **project map fresh automatically** after Kimiflow runs, surface staleness, and make section lookup token-cheap.

### Added
- **A1 — `project-map-status.sh refresh --changed`**: after a run, auto-restamps the map sections whose files changed
  (matched by `.files` membership or longest prefix), prunes deleted files, adopts new files under a section prefix,
  re-indexes their `.sh` symbols, and advances the baseline (idempotent for committed deltas). Wired into the Phase-7
  step in `SKILL.md`, so the project map no longer goes stale after building with Kimiflow.
- **A2 — `map-staleness-nudge.sh`**: a non-blocking `Stop` hook that surfaces a `systemMessage` when the local
  project map is stale (rate-limited; resolves its helper by absolute path before `cd`). Registered in both
  `hooks.json` and `hooks/hooks.json`, so map drift is visible even after non-Kimiflow edits.
- **B1 — `project-map-status.sh index-symbols`**: a dependency-free `.sh` symbol→section index stored under
  `sections.<name>.symbols` in `INDEX.json` (additive; `schema_version` unchanged).
- **B4 — `suggest-affected-sections.sh`**: ranks the likely-affected map sections (with representative paths for
  `coverage --affected`) from intent/problem terms, so Phase 2 stops guessing affected paths blind.

## 0.1.46

Fix the **false agentic-readiness MCP warning**; ship Claude Obsidian MCP auto-setup.

### Fixed
- `agentic-readiness.sh` surfaced `mcp_not_direct_ready` even when an authenticated Obsidian/Vault MCP was
  connected, because it read only the static `.kimiflow/project/VAULT-PROVIDER.json` — which `provider connect`
  writes without live capabilities. It now honors the local `KIMIFLOW_VAULT_MCP_AVAILABLE` /
  `KIMIFLOW_OBSIDIAN_MCP_AVAILABLE` session signal (env only, no network), matching the precedence already used in
  `memory-router.sh` provider auth, so a connected host MCP clears the false warning.

### Changed
- The Obsidian Vault MCP wizard (`vault-mcp-setup.sh --host claude --write-config`, used by the interactive
  `vault-mcp-open-terminal.sh` flow) now applies the Claude Code MCP server automatically via `claude mcp add-json`
  instead of only printing the JSON snippet. It replaces any existing user-scope `obsidian` server — including an
  older stdio `mcp-obsidian` entry with an inline key — so no stale plaintext token lingers in `~/.claude.json`;
  the key stays in macOS Keychain and is read at connection time by the headers helper. This brings Claude setup to
  parity with the existing Codex `--write-config` automation.

## 0.1.45

Ship **Agentic Readiness Layer**.

## 0.1.44

Ship **natural Kimiflow mode shortcuts** for Claude Code and Codex.

### Added
- Added first-class `full`, `grill`, `plan`, `build`, `quick`, `review`, `audit`, and `fix` aliases to the
  canonical Kimiflow contract, Codex wrapper, launcher docs, README, and plugin metadata.
- `full` now explicitly forces the strict full loop with grill/spec, research, plan-gate, and a pre-build approval
  stop before implementation.

### Changed
- Install smokes now verify the alias contract across Claude Code, Codex, README, launcher docs, and plugin metadata
  with stricter checks for the no-code aliases.

## 0.1.43


### Added
- Added local Background Handles for long-running read-only or draft-producing Kimiflow work such as deep codebase
  analysis, docs drafts, security/advisory review, and improvement scans.
- Added `hooks/background-run.sh` and tests for handle start/list/status/update/collect/cancel/stale flows,
  stale affected-path detection, unsafe path rejection, and fail-closed corrupted status handling.
- Launcher status now surfaces collectable and stale background handles as maintenance reasons.

### Changed
- README, reference docs, plugin metadata, install smokes, and CI now document and verify the Background Handles
  workflow for Claude Code and Codex.

## 0.1.42

Ship **working-tree, red/green, and local diagnostics gates**.

### Added
- Normal write-mode Kimiflow runs now require a clean working tree before starting, while ignoring local `.kimiflow/`
  state.
- Fix runs now use a Red/Green evidence gate around `BUG-REPRO.md` before review, learning promotion, or completion.
- Local diagnostics now run as a bounded advisory using existing typecheck/lint/LSP-adjacent tools or an untracked
  local `.kimiflow/lsp-diagnostics` command.
- CI now runs the new working-tree, red/green, and local diagnostics unit tests explicitly.

## 0.1.41

Ship **Harden review lifecycle and active-session safety gates**.

## 0.1.40

Ship **global local memory efficiency metrics**.

## 0.1.39

Ship **Stabilize CI tests and keep maintainer notes local**.

## 0.1.38

Ship **memory-economics normalization and commit-hook hardening**.

### Added
- README now documents the repository structure in English and German, including which generated `.kimiflow/`
  project-intelligence files should stay local by default.

### Fixed
- `memory-router.sh metrics` now normalizes older run-economics rows to the current `used_hit_count` heuristic
  so legacy `recall_hit_count` estimates cannot inflate token-savings totals.
- `commit-secret-gate.sh` now fails closed for malformed git-like hook payloads inside Kimiflow repositories,
  while preserving no-op behavior for malformed payloads outside `.kimiflow/` scope.

## 0.1.37

Ship **Memory Economics and searchable review findings** for the Kimiflow learning loop.

### Added
- `memory-router.sh recall --write` now writes a run-local `RECALL.json` beside `RECALL.md`.
- `review-run --write` now records directional run-level token-efficiency telemetry in
  `.kimiflow/project/MEMORY-ECONOMICS.jsonl`.
- `status`, `metrics`, and `curate --write` now expose memory-economics summaries while preserving legacy
  usage-economics fields.
- Local run history and FTS recall now include review summaries and canonical `.kimiflow/<slug>/findings/*.md`
  so Kimiflow can recall prior review findings after the gate closes.

### Fixed
- Token-savings estimates now use `used_hit_count` instead of all recall hits, avoiding inflated savings claims.
- Generic `REVIEW.md` prose stays searchable local run history but is no longer promoted into durable
  `LEARNINGS.jsonl` entries.
- Docs and plugin metadata now point installs at the `kimikonapps/kimiflow` Git marketplace.

## 0.1.36

Ship **frictionless Obsidian Vault MCP setup** for Codex and Claude Code.

### Added
- Memory Router provider status now auto-detects a running Obsidian Local REST API on the common local ports
  and reports `provider_detected_unconfigured` until the user connects it.
- Added `provider detect` / `provider connect` for a frictionless local Obsidian setup that writes only
  `.kimiflow/project/VAULT-PROVIDER.json` and never stores an Obsidian API key.
- Added `provider health` with `detected_unconfigured`, `connected_local_only`, `authenticated`, and `auth_failed`
  states, plus auth-ready capabilities without storing API keys in `.kimiflow/`.
- Added `provider setup` and `hooks/vault-mcp-setup.sh` for safe Obsidian Local REST API MCP setup:
  Codex uses `bearer_token_env_var = "OBSIDIAN_API_KEY"`, Claude Code uses `headersHelper`, and non-loopback
  URLs are refused before any token-bearing setup is printed.
- Added `hooks/vault-mcp-open-terminal.sh`, an interactive macOS Terminal wizard that writes host config, stores the
  API key in Keychain, verifies local auth, and keeps the key out of chat and `.kimiflow/`.
- Provider prefetch/sync handoffs now include health/auth readiness, so direct Vault search/write is used only
  when authenticated and otherwise stays as reviewable local `VAULT-PREFETCH.md` / `VAULT-SYNC.md`.
- Launcher/README/skill docs now describe the V2 flow: detect Obsidian, connect locally, check health/auth, print
  host-owned MCP setup, then use direct Vault search/write only when authenticated.

## 0.1.35

Ship **bounded Vault sync handoffs** for the Memory Provider lifecycle.

### Added
- Memory Router now supports optional local FTS5 recall via `.kimiflow/project/RECALL.sqlite` and `index --write`.
- Added `history --query ... --write` for bounded old-run/session recall snapshots in `RUN-HISTORY.json` /
  `RUN-HISTORY.md`.
- Added persisted recall/history usage metrics in `MEMORY-USAGE.json` plus lifecycle curation metadata in
  `MEMORY-INDEX.json`.
- Added `provider status|configure|prefetch` for local optional Vault/Obsidian provider manifests and bounded
  `VAULT-PREFETCH.md` handoffs.
- Added `record --scope user` with local-only `USER.jsonl` / `USER.md` profile memory.
- Added `consolidate --write` to archive superseded learning rows without silent deletion.
- Added `propose --write` to generate review-only rule/skill proposals from evidence-backed learnings.
- Approved skill/workflow proposals now create review-only drafts under `.kimiflow/project/SKILL-DRAFTS/` instead
  of patching skill files automatically.
- Added `provider sync --write` to create `.kimiflow/project/VAULT-SYNC.md` from current, non-private,
  non-security learnings with freshly verified repo-relative evidence.
- Launcher and memory status now report `provider.sync` and `provider_sync_pending` so omitted Vault sync
  candidates stay visible until exported.

### Fixed
- Refreshed learning rows now supersede older rows with changed evidence fingerprints, and recall returns only
  `current` learnings.
- Outside-repo evidence paths are sanitized to `OUTSIDE_REPO` before persistence.
- Evidence fingerprints now store an explicit digest algorithm and digest; `sha256` is populated only when the
  digest is actually SHA-256.
- Active memory writes are now blocked when they contain prompt-injection, hidden-instruction, or credential
  exfiltration patterns.
- Provider sync recomputes evidence fingerprints before export so stale or changed evidence rows are not written
  to the Vault handoff.
- Vault sync handoffs are capped by `${KIMIFLOW_PROVIDER_SYNC_MAX:-20}`, and only exported IDs are marked synced.

## 0.1.34

Add **quality and source-freshness gates** to the Learning Loop.

### Added
- `review-run` now blocks low-quality learning candidates before writing: too short, generic, missing verified
  evidence, decisions without a decision, rules without a rule, or pitfalls without an avoidance signal.
- Learning rows now include `evidence_fingerprints` so `verify-run` can detect when source evidence changed
  after the run-close review.
- `verify-run` now returns `CLOSED reason=evidence_stale` when a recorded learning's evidence file changed,
  is missing, or lacks a current fingerprint.
- Memory-router tests cover evidence fingerprints, stale evidence, refresh after evidence changes, and
  low-quality learning rejection.

## 0.1.33

Harden the **Learning Loop close gate** after code review.

### Fixed
- `memory-router.sh verify-run` now validates every `Recorded: learn_*` ID against current rows in
  `.kimiflow/project/LEARNINGS.jsonl` instead of trusting the review markdown alone.
- Learning recording no longer reuses stale or superseded rows as proof of a fresh completed run; repeated
  proof appends a new current row while current duplicates remain idempotent.
- Memory-router tests cover forged/missing recorded IDs and stale-learning reconfirmation.

## 0.1.32

Close the **Learning Loop** mechanically for completed Kimiflow runs.

### Added
- `memory-router.sh review-run` writes `.kimiflow/<slug>/LEARNING-REVIEW.md`, records the four-question
  learning set in `.kimiflow/project/LEARNINGS.jsonl`, refreshes bounded `MEMORY.md`, and updates the
  memory index.
- `memory-router.sh verify-run` fails closed when a run has no learning review, no recorded learning IDs, or
  a skipped review without an explicit reason.
- Memory-router tests cover recorded reviews, explicit skip reviews, index refresh, and the missing-review
  blocker.

### Changed
- Phase 7 now requires `review-run` + `verify-run` before `STATE.md` may be marked `Status: done`.
- README, reference docs, and install smokes now surface `LEARNING-REVIEW.md`, `review-run`, and `verify-run`
  as part of the Kimiflow memory contract.

## 0.1.31

Ship the **Memory Router and Learning Loop** for token-cheap project recall.

### Added
- `hooks/memory-router.sh` with `status`, `recall`, `classify`, `record`, and `curate` commands for local
  `.kimiflow/project/` memory artifacts (`MEMORY.md`, `LEARNINGS.jsonl`, `MEMORY-INDEX.json`, `RECALL.md`).
- `hooks/test-memory-router.sh` covering empty state, recall, sensitivity classification, recording, and
  non-destructive curation.
- Launcher status now includes memory budget, learning counts, Vault availability, and curation reasons so
  the start menu can offer memory hygiene before feature/fix work.

### Changed
- Canonical Claude and Codex skill docs now route Phase 2 through local memory recall before optional Vault,
  claude-mem, or web research, and route Phase 7 through automatic learning classification/recording.
- Plugin metadata, README, and publish-safe repo docs now mention bounded memory/recall alongside Project
  Intelligence.

## 0.1.30

Fix **launcher run hygiene edge cases** and clarify project-map baseline maintenance context.

### Fixed
- `hooks/launcher-status.sh` no longer infers `Status: done` from ambiguous Phase 7 lines such as
  `Phase 7: not done yet`; only explicit `Phase 7: done` / `RUN COMPLETE` markers count.
- Launcher maintenance JSON now reports `commits_since_project_map_baseline` as an informational baseline
  count, so callers do not mistake it for a stale-map signal.
- `hooks/test-launcher-status.sh` covers both the legacy Phase 7 completion inference and the ambiguous
  `not done` regression case.

## 0.1.29

Fix **launcher open-item counts for English project-map artifacts**.

### Fixed
- `hooks/launcher-status.sh` now counts Findings and Improvements under German and English section
  headings (`## Offen` / `## Open`, `## Priorisierte Slices` / `## Prioritized Slices`), matching
  Kimiflow's user-language artifact rule.
- `hooks/test-launcher-status.sh` now covers both DE and EN count formats so the launcher cannot silently
  show `0` open items for English projects again.

## 0.1.28

Ship the **context-aware Kimiflow Launcher** and publish-safe repo documentation.

### Added
- `hooks/launcher-status.sh` and `hooks/test-launcher-status.sh` provide a read-only launcher snapshot:
  project-map status/depth, findings, improvement slices, repo docs, dirty working tree, and active/backlog
  runs.
- Empty or vague Kimiflow invocations (`/kimiflow`, `$kimiflow`, `--launcher`, `--menu`) now route through
  a context-aware launcher instead of requiring the user to know the right flag up front.
- Resume safety rules now require revalidation before implementing a parked plan when affected files changed
  since the plan commit, or when the plan basis/affected files are unknown.
- Publish-safe repo docs under `docs/` document architecture, codebase layout, testing, and the public docs
  boundary while keeping raw findings local.

### Changed
- Codex plugin metadata now surfaces the launcher in the default prompt and description.
- Claude and Codex smoke tests assert the launcher contract and helper wiring.

## 0.1.27

Ship **hook labels and publish-safety docs** for the Codex plugin path.

### Changed
- Codex plugin-bundled hooks now carry names/descriptions/status text so plugin UIs can label them instead
  of showing only generic hook numbers.
- Project-map docs now state that raw `.kimiflow/project/` maps and sensitive findings stay local/private;
  repo docs are curated publish-safe derivatives.
- README and compatibility docs clarify local Codex plugin cache paths and update expectations.

## 0.1.26

Ship **Codex plugin visibility improvements** so the Project Intelligence capability is visible in the
Codex plugin detail view and CLI update docs.

### Changed
- Codex plugin display metadata now surfaces Project Intelligence in the plugin detail view: default
  prompts include codebase mapping, architecture/refactoring opportunities, and project documentation,
  and the Codex description mentions `.kimiflow/project/` codebase understanding.
- Codex install docs now recommend the Git marketplace (`swinxx/kimiflow`) for normal installs so
  `codex plugin marketplace upgrade kimiflow` works. Local path marketplaces are documented as a
  development mode because Codex shows the local manifest version but does not upgrade that source type.

## 0.1.25

Ship **Project Intelligence** for kimiflow: a local project map, per-section staleness/refresh, and
optional Vault/repo-doc/improvement publishing.

### Added
- **Vault, repo-doc, and Improve publishing contract (Slice 3)** for standalone project-map runs:
  user-language focus choices (`codebase`, `architecture`, `docs`, opt-in `improve`), explicit storage
  targets (`kimiflow`, `kimiflow+vault`, `kimiflow+vault+repo-docs`), local-first source-of-truth rules,
  and evidence-backed `IMPROVEMENTS.md` / `DOCS-PLAN.md` outputs.
- **Project Map Staleness + Delta Refresh (Slice 2)** via `hooks/project-map-status.sh` and
  `hooks/test-project-map-status.sh`. Kimiflow can now classify existing `.kimiflow/project/INDEX.json`
  sections as `current`, `stale`, `potentially_stale`, or `unknown`, report affected stale sections, and
  mark only selected sections refreshed by updating their hashes/commit metadata.
- **Project Map Bootstrap (Slice 1)** docs/contract for a recommended, skippable project-intelligence
  cache under `.kimiflow/project/`. Kimiflow now documents `--project-map quick|standard|deep|skip`,
  the local artifacts (`INDEX.json`, `FACTS.jsonl`, `CODEBASE.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`,
  `TESTING.md`, `FLOWS.md`, `OPEN-QUESTIONS.md`), user-language output, and token-efficient mapper
  focus rules. This is the foundation for later per-section staleness and Vault/repo-doc publishing.

## 0.1.24

Ship **Codex plugin parity** for kimiflow while keeping the Claude Code path intact.

### Added
- **Codex plugin packaging** via `.codex-plugin/plugin.json`, repo-local `.agents/plugins/marketplace.json`,
  and `skills/kimiflow/SKILL.md`, so Codex can install kimiflow as a plugin-backed skill and invoke it
  explicitly with `$kimiflow` / named Kimiflow prompts.
- **Stable Codex hook installer** (`hooks/install-codex-hooks.sh`) that writes managed wrappers into
  `${CODEX_HOME:-~/.codex}/hooks` and pins `KIMIFLOW_PLUGIN_ROOT` back to the plugin checkout. This makes
  commit-secret-gate, state-gate, and test-gate work in Codex without relying on experimental
  `plugin_hooks`.
- **Codex structural smoke test** (`hooks/smoke-install-codex.sh`) covering Codex manifests, skill
  frontmatter, optional plugin-hook wiring, temp `CODEX_HOME` wrapper installation, and synthetic Codex
  hook payloads for commit, state, and test gates.

### Changed
- Hook payload parsing now accepts Codex-shaped command/cwd/stop-active fields alongside Claude-shaped
  payloads.
- `resolve-verbosity.sh` now honors `KIMIFLOW_HOST=codex` and uses `${CODEX_HOME:-~/.codex}` for Codex
  global presentation settings.
- Compatibility and README docs now distinguish stable Codex hook wrappers from optional plugin-bundled
  hooks, and document Codex install/invocation flow.

## 0.1.23

Make **slimness an active counter-force** instead of a polite principle. AIs over-build because training
rewards "comprehensive" and complexity carries no felt cost; a "keep it simple" line doesn't counter that.
This applies kimiflow's own philosophy — adversarial + surfaced, not self-assessed — to over-engineering,
while staying token-cheap (the check must not itself become bloat). **Docs/contract only — no new hook.**

### Added
- **Simplicity lens in Phase-7 code-review** (`reference.md` "Review rubric", `SKILL.md` Phase 7). KPI:
  *"what can be deleted while the ACCEPTANCE tests stay green?"* It FLAGs any abstraction/option/error-
  handling/layer **no test or real requirement demands** (earn the abstraction: **≥2 callers or a written
  reason**; single-caller pass-throughs, impossible-state handling, speculative generality) and **proposes
  the smaller version**. Output is **advisory** → `ADVISORIES.md`, triaged at the commit-gate
  (dismiss-with-reason or adopt) — un-ignorable but non-gating (no false-positive thrash).
- **Token-aware by design.** Runs only where a Phase-7 review runs (`small`/`large`); `trivial` is exempt
  and pays nothing. At `small` the dimension is **folded into the existing reviewer** (no new spawn); a
  **dedicated, blind Simplicity prosecutor** (a new Scaling knob) runs only at `large` or when a **size
  tripwire** fires — `git diff --stat` shows a diff much larger than its scope suggests (orchestrator-read,
  no hook), which raises a STOP+justify advisory.
- **"Fold, don't spawn" rule** (`SKILL.md` Agent budget): prefer extending an existing subagent's brief
  over a fresh spawn when it already has the inputs (~hundreds vs ~tens-of-k tokens); spawn a dedicated
  agent only when independence/blindness is the point.

## 0.1.22

Close a `commit-secret-gate` bypass where **`git -C <target> commit`** scoped the gate to the wrong repo.
The hook located the repo from the tool **cwd**, never from the global `-C <path>`, so a secret-looking
staged path could be committed into a kimiflow repo by running git from a different directory
(`git -C <kimiflow-repo> commit -am …` from outside). **Hook + tests + docs only.**

### Fixed
- **`git -C <path>` is now honored** (`hooks/commit-secret-gate.sh`). Repo resolution passes the global
  `-C` option(s) to git, which resolves them cumulatively relative to the cwd — exactly as `git -C` does
  (so `git -C <repo> commit` from any cwd is scoped to `<repo>`). Extraction is scoped to the **global**
  span (before the subcommand), so a reuse-message `-C <commit>` that *follows* `commit` is not mistaken
  for a chdir. Applied to both the precise (jq) path and the fail-closed jq-less fallback (which tests
  each `-C` target independently, so an unresolvable reuse-`-C` can't mask a real one). `git_root`, on an
  unresolvable `-C`, falls back to the cwd itself — never to the hook's own process cwd. bash-3.2-safe.
- **9 new tests** (`hooks/test-commit-secret-gate.sh`), all run with a process-cwd-faithful runner +
  an OUTSIDE-not-kimiflow guard: `-C` commit/`-am`/relative-`-C`/reuse-message-discriminator/no-false-
  positive, plus the jq-less `-C` cases.

### Docs
- **Known residuals updated** (hook header + `README`/`reference.md` "Commit hygiene"): `git -C <path>` is
  honored for **unquoted, space-free** paths; a **quoted `-C` path with a space** stays a residual; and
  `/usr/bin/git` / `command`/`builtin`/`exec git` are documented as command-position-evasion residuals
  alongside `sudo` / `env X=y` (a deliberate non-standard invocation is out of the gate's threat model).

## 0.1.21

Add a deliberate **defer → backlog** outcome to the Phase-4 pre-build summary gate. Until now a ready,
plan-gate-approved plan could only be **approved** (build now) or sent back to **change** — to park it for
later you had to lean on the silent headless/`--prepare` fallback. Now the interactive stop offers an
explicit third choice: "good plan, not now → backlog." **Docs/contract only — no engine, hook, script, or
flag change.**

### Added
- **`SKILL.md` Phase 4 step 7:** the pre-build gate question is now "Approve to build, change something, or
  defer to backlog?" — **defer → backlog** STOPs, marks `Status: backlog` in `STATE.md`, and emits
  `/kimiflow --resume <slug>`. It is the *explicit* twin of the `--prepare`/headless stop (same parked
  state, deliberate intent).
- **`STATE.md` gains a `Status:` line** (`SKILL.md` Phase 0 step 3): `active` while a run is in progress,
  `backlog` once a complete plan is parked before implementation (phases 0–4 done, 5 open); an absent
  `Status:` reads as `active`. The marker is written by every Phase-4 pre-build park reaching 0–4-done
  (the `defer`, headless, and step-6 plan-gate-open `--prepare` stops all share it) — so the backlog view
  can't mislabel one park as different from its identical-state siblings; an earlier stop (Explore,
  mid-phase) stays `active`.
- **`--resume` (no-slug) listing** now surfaces each run's `Status:` (absent → `active`), so deliberately
  parked **backlog** items are visible as a backlog.

### Changed
- **`reference.md` "Pre-build summary gate" Outcomes** documents the `defer → backlog` outcome and the
  explicit-defer-vs-silent-headless distinction (same parked state + marker; the difference is intent).
  Headless / no-answer control-flow is **unchanged** — it still behaves like `--prepare`, now also
  stamping the shared `Status: backlog` marker.

## 0.1.20

Make kimiflow **model-invocable (opt-in)** instead of hard-blocked. Previously `disable-model-invocation:
true` meant the assistant could not launch kimiflow at all — even when you asked it to ("run this with
kimiflow"); only the human typing `/kimiflow` worked. **Docs/contract only — no engine change.**

### Changed
- **`SKILL.md` frontmatter:** `disable-model-invocation: true` → `false`. The assistant can now launch
  kimiflow **on request**. The "only when asked, never unprompted" policy moved into the `description`
  (lead clause), so it's **opt-in by judgment, not a hard flag**. `/kimiflow` slash invocation is
  unchanged.
- **Honest trade-off, documented** (`README.md` EN+DE, `COMPATIBILITY.md`): the no-unprompted-trigger
  guarantee is now **soft** (description-guided), not mechanically enforced. Anyone who wants the hard
  guarantee back can set `disable-model-invocation: true`. `hooks/smoke-install.sh` now asserts
  model-invocation is enabled (not `true`) and rewords the manual no-auto-trigger check accordingly.

> **Takes effect after you update the installed plugin and restart** — a running session keeps the
> frontmatter it loaded at startup.

## 0.1.19

Close the pre-existing literal-TAB gap in `commit-secret-gate` (the LOW from 0.1.18's review).

### Fixed
- **A non-space token separator (TAB/VT/FF/CR) defeated detection** (`hooks/commit-secret-gate.sh`).
  The git/subcommand matchers anchor on a literal space, so `git<TAB>commit …` or `git commit<TAB>--all …`
  made `git_sub` NO-MATCH — skipping the **whole** commit branch (both the staged-path scan and the
  `-a` working-tree scan), letting a tracked/staged secret commit unblocked. Fixed with a single
  normalization: non-newline whitespace is collapsed to spaces (`tr '\t\v\f\r' ' '`) right after the
  command is parsed, so every downstream matcher benefits. Newlines stay as line separators. 4 tab
  unit tests added (82 cases total).

## 0.1.18

Close a second bypass class in `commit-secret-gate`'s `-a`/`--all` detection and make the README's
promise honest. Found by an external review of 0.1.17. **Hook + tests + docs only.**

### Fixed
- **`-a` detection bypass via a shell metachar hidden from the parser** (`hooks/commit-secret-gate.sh`).
  The commit args were split on `;`/`&`/`|` **before** quotes were stripped, so a metachar inside the
  `-m` message (`git commit -m "a; b" -a`) — or a `\`+newline line continuation — truncated the
  extraction and dropped the trailing `-a`, letting a tracked+modified secret commit unblocked. The
  hook now **joins backslash-newline continuations and strips quoted spans first, then** splits and
  detects `-a`/`--all`. Safe because this branch reads only flags, never pathspec/filenames. Unit
  tests added for quoted `;`/`&`/`|`, the `--all` variant, and a newline continuation (78 cases).

### Changed
- **Honest residuals, in docs and as locked tests** (`README.md`, `reference.md`,
  `hooks/test-commit-secret-gate.sh`). The README no longer implies it blocks "any" secret commit; it
  now names the **backstop, not complete secret protection** framing and the known gaps. Documented +
  test-locked as known ALLOW (regex ≠ shell parser): an `env X=y`/`sudo` prefix (defeats the
  command-position anchor, gate-wide), an **escaped quote** in the message, and an explicit **pathspec
  commit** (`git commit <path>`). A pre-existing literal-tab-after-`git` gap is also known (LOW).

## 0.1.17

Close a real bypass in the `commit-secret-gate` hook and document its boundaries honestly. The gate
only inspected the **index** (`git diff --cached`), so a secret-looking file committed via implicit
staging slipped through. **Hook + tests + docs only — no new mechanism or dependency.**

### Fixed
- **`commit-secret-gate` — `git commit -a`/`--all`/`-am` bypass** (`hooks/commit-secret-gate.sh`). These
  forms auto-stage tracked working-tree modifications *at commit time*, after the PreToolUse hook has
  already read the index — so a modified, already-tracked `.env` (etc.) was committed unblocked. The
  hook now also scans tracked-but-unstaged modifications (`git diff --name-only`) when `-a`/`--all` is
  present. Flag detection matches `a` before any value-taking short option (m/c/C/F/S/u), so bundled
  forms `-am`/`-vam`/`-qam` are caught while `-ma` (a message), `-uall`, `-Sabc` and `--allow-empty` are
  correctly ignored. Unit tests added (`hooks/test-commit-secret-gate.sh`, 70 cases).

### Changed
- **Honest scope docs** (`reference.md` "Commit hygiene" + hook header). The gate no longer claims to
  block "any `git commit`": it now states the `-a`/`--all` coverage **and** the residual limitations —
  an explicit **pathspec commit** (`git commit <path>`) of an already-tracked secret is **not** covered
  (parsing a pathspec from a shell string needs an AST, not a regex). **Bottom line: the gate is a
  path-hygiene backstop, not complete secret protection** — pair it with `.gitignore` discipline + a
  content scanner (gitleaks/trufflehog) and don't track secrets in the first place.

## 0.1.16

Add **claude-mem** as a second *optional* memory-recall provider in Phase 2, alongside the Obsidian
vault. Recall beats re-research: kimiflow now searches cross-session memory too, when it's present.
**Documentation/contract only — no new hook, script, CI, or gate logic; no hard dependency.**

### Changed
- **Phase 2 recall is now provider-agnostic** (`SKILL.md`, `reference.md`). Step 1 ("Recall before
  researching") searches whichever optional providers are connected — the **vault** (notes MCP, e.g.
  Obsidian) and **claude-mem** (cross-session memory MCP, e.g. `memory_search`/`observation_search`).
  Each is independent and graceful: present → use, absent → note in `STATE.md` + continue. A fresh
  relevant hit from either replaces web research. Detection is **per-run by tool availability**, so a
  later-installed provider is picked up on the next run.
- **claude-mem is search-only.** kimiflow recalls from it but never writes to it (it auto-captures
  sessions); verified findings still save to the vault. The "Always last — vault-save" step is
  unchanged.
- **New `reference.md` "Memory recall (Phase 2)"** section documents the two optional providers, the
  graceful-skip contract, and per-run detection. "Vault conventions" stays for vault save-back.
- **README** ("Vault memory layer" / "Vault-Memory-Schicht", EN + DE) names claude-mem as the second
  optional source — search-only, graceful skip, independent of the vault.

## 0.1.15

Make STATE-persistence **enforced**, not a prose ask the orchestrator can rationalize past — closing a
gap found when a "lean" doc run skipped `.kimiflow/<slug>/STATE.md` and lost resumability.

### Added
- **`state-gate` hook** (`hooks/state-gate.sh`, PreToolUse/Bash). Intercepts the review-gate resolver
  call (`resolve-review-gate.sh .kimiflow/<slug>/findings …`) and **denies it fail-closed unless that
  run's `STATE.md` exists and is non-empty** — so no gate verdict (→ no commit) without persisted run
  state. The safety-critical `resolve-review-gate.sh` is **untouched** (separate hook, not a resolver
  edit). Auto-active only in kimiflow repos; needs no jq; unit-tested (`hooks/test-state-gate.sh`, 11
  cases incl. a no-jq path); wired into `hooks.json` + smoke-test. **Honest limit:** catches every run
  that reaches a gate (everything that commits), not a `--prepare`/`trivial` run that stops before any
  gate — those are covered by the prose + eval below.
- **Behavioral-eval scenario 11** (`evals/scenarios/11-state-persistence.md`): does the orchestrator
  still persist `STATE.md` under "keep it in chat to stay lean" pressure?

### Changed
- **SKILL.md "Persist phase progress"** — explicit negation: not optional, not terse-trimmable;
  "small / lean / doc-only run" is not an exemption (only `trivial` runs without the loop). Plus a
  **"Narration ≠ persistence"** clause on the terse-output rule: terse suppresses *talking about* state
  in chat, it never removes writing `STATE.md` / the phase artifacts to disk.

## 0.1.14

A review-contract sharpening: reviewers judge against intent, acceptance, the diff and actual behavior
— **tests are evidence, not the boundary of truth**, not the limit of it. Plus a second eval dimension
that calibrates reviewer *judgement* (not just gate-holding). **Documentation and scenarios only — no
new mechanism, CI, or gate logic.**

### Changed
- **Review rubric — "Tests are evidence, not the boundary of truth"** (`reference.md`). A reviewer
  judges against intent/acceptance/diff/behavior; a green suite may *support* a finding but never
  *refutes* one grounded in code/spec ("not covered by a test" is not a counter-argument). An untested
  real risk is still a finding, and missing coverage of a real risk can itself be a finding —
  anti-hallucination still binds: **severity = provable impact**.
- **Phase 7 reviewer brief** (`SKILL.md`): hunt untested-but-real requirement gaps; a green suite never
  refutes a finding grounded in code/spec. Spine-terse; detail in `reference.md`.
- **Evals reframed as release-calibration** (`evals/README.md`): a mirror read around a release, not a
  runtime oracle; the model under test never sees a findings list; judged post-hoc. Not "test cases."

### Added
- **Reviewer-calibration eval dimension** (`evals/reviewer-calibration.md`): pressure-tests whether a
  reviewer judges cleanly (writes the warranted finding) under green CI / authority / time, vs. the
  tests-as-truth failure. Hidden-notes rule (the answer key never enters the reviewer's context),
  held/soft-crack/hard-crack judging, and an explicit anti-goal — **no gold list, no CI grading of LLM
  reviewers**.
- **Three reviewer pressure scenarios** (`evals/scenarios/reviewer/`): green-but-acceptance-unmet, a
  referenceable defect no test exercises, and a test narrower than the intent.

## 0.1.13

A hardening pass from a second audit: an exact review-gate cap contract, an optional secret
content-scan advisory, and an install smoke-test that guards plugin/skill invocation. This release
also re-syncs the GitHub tag/release, which had lagged at `0.1.0` while the plugin advanced.

### Added
- **Optional secret content-scan advisory** (`hooks/secret-content-scan.sh`). Complements the
  path-only `commit-secret-gate` by scanning the **staged content** for in-source secrets via
  `gitleaks` (else `trufflehog`) when one is installed; findings become `FLAG` advisories in
  `ADVISORIES.md` for commit-gate triage. **Non-gating**, with a graceful STDERR skip when no scanner
  is present — the fail-closed path-hygiene gate is untouched. Wired into Phase 7; unit-tested
  (PATH-mocked) and added to CI.
- **Install smoke-test** (`hooks/smoke-install.sh`). Structural, runnable without a live Claude Code
  session: validates the manifests + version consistency, the `SKILL.md` frontmatter
  (`disable-model-invocation: true`, `user-invocable` not disabled, `name`/`description`/
  `argument-hint`), the `hooks.json` wiring, and fires `commit-secret-gate` against synthetic
  PreToolUse stdin. Prints the manual live-CC checklist and references the Claude Code invocation
  issues it guards (anthropics/claude-code#26251, #22345). CI hard gate.

### Fixed
- **Review-gate cap fires at the round limit, not one past it.** `resolve-review-gate.sh` flagged
  `cap-reached` only at `round > cap` (round 4 under `--cap 3`) — one round past the documented
  "cap 3 reached → stop". Now `round >= cap`: round 3 under `--cap 3` with open findings →
  `cap-reached`, so a run does **at most 3 review rounds** (was effectively 4). TDD-covered; the
  reappearance test gets `--cap 5` headroom to keep exercising its own branch.

## 0.1.12

A self-applied claim/evidence remediation (kimiflow's own `evidence-before-assertion` standard turned
on its own docs) + eval-suite hardening + secret-gate scoping. **Docs / evals / tests only — engine
behavior unchanged; `secret_re` and all gate logic untouched.**

### Added
- **`COMPATIBILITY.md`** — every Claude Code primitive kimiflow depends on (PreToolUse/Stop hooks,
  `${CLAUDE_PLUGIN_ROOT}`/`${CLAUDE_SKILL_DIR}`, `TaskCreate`/`TaskUpdate`, subagent types,
  `disable-model-invocation`, the manifests), classed load-bearing vs graceful, with a version-bump
  smoke checklist. Last verified: Claude Code 2.1.186.
- **Eval suite expanded to 10 scenarios** — `07-scope-gate` (both directions),
  `08-advisory-triage-failclosed`, `09-headless-build-gate`, `10-terse-output`; an open-ended tier
  beside the MCQ tier; and a run procedure requiring n≥3 per pass + a CLAUDE.md-free / attribution-
  forcing setup (addresses the ambient-CLAUDE.md confound in the method, not just the prose).
- **`evals/outcomes.md`** — an honest, currently-empty log for outcome quality (kimiflow vs a plain
  session); field notes, not a benchmark. Nothing is cited from it while empty.

### Changed
- **Outward claims aligned with evidence.** The 0.1.11 "6/6 held" line now carries the
  ambient-CLAUDE.md confound caveat (only 3/6 cleanly attributable). README weakened "enforced, not
  self-reported" → "a `done` self-report can't inflate past open blockers"; `reference.md` "Review
  rubric" now states what the gate does **not** guarantee (sound over its inputs, not a completeness
  proof). Added a "Why kimiflow over plan-mode + a `CLAUDE.md`" section.
- **Scenario pass-criteria tightened** — every scenario now requires the citation to name its
  `SKILL.md`/`reference.md` location; the cartoonish distractors in 01/02 became tempting near-misses.
- **`commit-secret-gate` claim scoped to path-hygiene** — README / `reference.md` now state it is
  filename/path hygiene, **not** secret-in-source detection, and point to gitleaks/trufflehog for
  in-source secrets. Doc pattern list synced to the regex (`.asc`, the four concrete SSH keytypes,
  `.p12`/`.pfx`). The blunt no-jq fallback's intentional over-block is documented and locked by tests.
- **`LEDGER.md` schema** gains approx-token-cost + post-commit-outcome columns (a cheap ROI
  instrument) and an honest "when is `large` worth it?" note.

## 0.1.11

### Added
- **Behavioral-eval tier (`evals/`, out-of-CI, on-demand).** A subagent pressure-test suite for the
  six highest-stakes gates (commit-gate, diagnosis-before-fix, plan-gate cap/anti-oscillation,
  deletion caller-verification, evidence-before-assertion, anti-hallucination). Each scenario loads a
  fresh subagent with the real deployed skill and a multi-pressure situation, then checks whether the
  gate holds and is cited — the `testing-skills-with-subagents` (TDD-for-process-docs) tier. LLM-judged
  and variant by design, so never wired into CI; a one-line Scaling-knobs pointer makes it
  discoverable. First run (2026-06-23): 6/6 held, but only 3/6 (03/05/06) are cleanly attributable
  to kimiflow's own text — 01/02/04 were confounded by the ambient global `CLAUDE.md` (see
  `evals/README.md` → "Known limitation"). Treat as a smoke pass, not a robustness proof.

## 0.1.10

### Added
- **Opt-in `🧭 Explore` phase (`--explore`).** A divergent front-end that runs *before* the
  convergent Phase 1 Clarify: 2–3 codebase-grounded explorer subagents each propose a **distinct
  direction** (minimal / robust / sideways), the orchestrator synthesizes a terse menu, and the user
  picks one — which then seeds Clarify. Forced with `/kimiflow --explore <idea>`; otherwise kimiflow
  offers once on an open-ended request (decline / headless → normal routing, never blocks). Pick →
  continue into the loop, or stop with an `EXPLORE.md` option memo and `--resume` later. Feature-mode
  only (a fix/cleanup that surfaces → suggests `--fix`/`--audit`). Purely additive — non-explore runs
  are behaviorally unchanged; no new hook/script (the pick is a human gate).

## 0.1.9

### Changed
- **Slimmed `SKILL.md` to a thinner state-machine spine.** The always-loaded orchestrator spec was
  compressed in 12 spots where the detail already lives in `reference.md` — in-line explanation
  replaced with terse imperatives + section pointers (Modes, Core principles, Phase 0/2/4/7).
  **Behavior-preserving:** every gate, threshold, transition and mechanical contract is kept inline
  or reachable via a working `reference.md` pointer (verified clause-by-clause by an independent
  adversarial audit; all pointers resolve; `reference.md` unchanged; hook tests green). ≈−8% bytes /
  −9% words off the per-run orchestrator context.

## 0.1.8

### Fixed
- **commit-secret-gate — bulk-add false positive across subcommands.** The bulk-add guard scanned
  the whole compound command for a bare `.` / `-A` / `--all`, so a named `git add foo` combined with
  a `.` pathspec in a DIFFERENT subcommand (e.g. `git add foo && git grep -- .`) was wrongly refused
  as `git add .`. The check is now scoped to the `git add` invocation's own args (segment after `add`,
  bounded by `;&|`). Genuine bulk adds (`git add .`, `-A`, `--all`, `git add foo .`) stay blocked;
  tests added both ways.

## 0.1.7

### Added
- **Mechanized review gate.** The binary Phase-4 / Phase-7 review decision is now a single tested,
  deterministic resolver `hooks/resolve-review-gate.sh` instead of a prose-instructed `grep | wc -l`.
  It validates findings completeness + canonical grammar, counts open BLOCKER/HIGH, and applies
  anti-oscillation (`cap → oscillation → reappearance`), echoing one stable machine line
  `VERDICT⇥count⇥reason_code⇥detail`. **Fail-closed** on any incompleteness / malformation / misuse
  (never a false `OPEN`); **language-agnostic** — operates only on the `FINDING <SEVERITY> <ref> :: <reason>`
  abstraction (arbitrary UTF-8 refs/reasons, no source or per-language logic). Unit-tested (22 cases),
  wired into CI as a hard gate.

### Changed
- `reference.md` "Review rubric" and `SKILL.md` (Phase 4 / Phase 7) now delegate the gate count to
  `resolve-review-gate.sh` (the single source of truth); gate semantics unchanged (mechanized 1:1).

## 0.1.6

### Fixed
- **commit-secret-gate — compound code filenames:** the keyword deny-list flagged a secret-word
  wherever it was bounded by `[/._-]`, so the gate's own files (`commit-secret-gate.sh`,
  `test-commit-secret-gate.sh`) and source files like `secret-manager.ts` were refused — a false
  positive the "commit from outside a run" hint couldn't resolve. The **trailing** word-boundary now
  excludes `-`: a secret-word is still caught as a path's trailing token (`client-secret.txt`,
  `aws-credentials.yml`, `prod-secret.json`) but no longer mid-name. Leading `-` kept; tests added
  for both directions.

### Changed
- **resolve-verbosity:** dropped the unused standalone `origin` mode — it was documented and
  unit-tested but never invoked by the orchestrator (`onboard-check` already encapsulates the sole
  origin-based decision). `get`/`onboard-check`/`set` unchanged; an `origin` arg now degrades to `get`.
- Renamed leftover internal `flow_root()` → `git_root()` in commit-secret-gate (flow→kimiflow rename).

### Removed
- `design/` plans/specs for already-shipped features — trims the published repo (git history retains them).

## 0.1.5

### Fixed
- **commit-secret-gate — suffix-style `.env`:** the secret pattern matched only dotfile `.env`;
  `prod.env`/`dev.env`/`.envrc`-style names now match too.
- **commit-secret-gate — combined add+commit:** a `git add <secret> && git commit` in one command
  now has its add-targets scanned, not just the index.
- **commit-secret-gate — no-jq fail-closed:** the jq-less detection was quote-fragile, so
  `git -C "…" commit` / `git -c k="v" commit` slipped through; now denied (quote-robust).
- **test-gate — no-jq loop-break:** the `stop_hook_active` break now works without jq (grep
  fallback), so a red marker can no longer re-block forever; a stderr hint recommends jq.
- SKILL.md YAML frontmatter (`description` quoted) — fixes the GitHub render error.

### Added
- Unit tests for `commit-secret-gate`, `test-gate`, and the test-weakening scanner, all wired into
  CI as hard gates. CI now also validates `marketplace.json`.

### Changed
- Hooks documented as **plugin-mode only**; secret-pattern wording corrected (incl. `.env`/`.envrc`,
  `access_token`/`auth_token`).
- Removed build-time external-toolchain references from the published repo; design artifacts moved
  to `design/`. The audit-mode lens is described in kimiflow's own terms.

## 0.1.4

### Added
- **Audit / cleanup mode** — a third mode (`/kimiflow --audit <path>` or auto-detected) that runs an
  existence-first cleanup lens over a bounded target: finds tagged slices (`yagni`/`delete`/`shrink`/`stdlib`) with
  repo-wide caller-greps and git-history-freshness, presents them for approval (Phase-4 summary gate),
  then executes one slice = one commit with a per-slice verify gate. Caller-grep is a documented
  MINIMUM; tests + do-NOT-touch + adversarial "refute the cut" verification are the backstop. Engine unchanged.

## 0.1.3

### Added
- **Pre-build summary gate** — at the end of Phase 4 (after the plan-gate opens), kimiflow
  prints a structured summary (problem/goal · decisions · plan · tests/acceptance · risks +
  artifact paths) and **waits for your OK** before implementing. Project-local toggle
  `.kimiflow/build-gate` (`on`/`off`, default `on`), set via `--settings`; never global
  (self-contained rule). Control-flow only — the engine is unchanged. Toggle resolved by the
  unit-tested `hooks/resolve-build-gate.sh`.
- **Native phase task-list** — Phase 0 creates a glance widget (`TaskCreate`/`TaskUpdate`) of
  the phases being run; complements `STATE.md` and the colored markers, replaces narrated status.

### Changed
- **Deletions are now caller-verified** — removing code requires a recorded zero-caller proof
  (`grep`); an unproven deletion is a code-review BLOCKER. Load-bearing-but-removable-looking code
  goes on a do-NOT-touch list instead.
- **Plan tasks carry a `Consumes:`/`Produces:` interface block** for parallel/worktree implementers.
- **`large`-scope plans record 2–3 considered alternatives** + the selecting trade-off.

## 0.1.2

### Added
- **MIT license** — a `LICENSE` file + `license` field in the manifest, so the README's
  "anyone can install/fork" is actually covered (previously de-facto all-rights-reserved).
- **CI runs the unit tests** — `hooks/test-resolve-verbosity.sh` is now a hard gate in CI
  (was `bash -n` + JSON validation + advisory shellcheck only; the green tests were never run).
- **Artifact-economy rule** — on-disk artifacts (re-read by every subagent each round, the
  dominant token cost) are written information-dense; density never trades away rigor.

### Changed
- **First-run onboarding is now mechanical** — `resolve-verbosity.sh onboard-check` decides
  `ASK`/`SKIP` in the unit-tested script (`ASK` iff no project/global config and no flag), so it
  fires reliably on a fresh project and never nags a configured one. 0.1.1's prompt was
  orchestrator-judged and could be silently skipped.
- **Stale `flow` → `kimiflow`** in the hooks' headers and operator-visible deny/block messages.
- **SKILL.md / reference.md prose compacted** — decoration removed, telegraphic phrasing;
  every rule, threshold, path, and acceptance-criteria precision unchanged.

## 0.1.1

### Added
- **Display verbosity** — `quiet` / `balanced` / `verbose` levels that change **only** visible
  output; the engine (gates, artifacts, evidence, subagents, thresholds) is identical at every
  level. One-off `--quiet` / `--verbose`, setter `--set-verbosity`, a `--settings` dialog
  (level + scope), and a one-time first-run prompt (headless/skip → `balanced`, no block).
  Precedence `flag > project > global > balanced`, resolved by a unit-tested helper
  (`hooks/resolve-verbosity.sh`). Only verbosity may live globally (`~/.claude/kimiflow/verbosity`).

### Changed
- **State dir renamed `.flow/` → `.kimiflow/`** (self-documenting).
- **Fix-mode research** now names `WebSearch` / context7 / `WebFetch` explicitly (parity with the
  feature path).
- **Vault research is freshness-aware** — a hit is weighed by its `date:`; a fresh hit that
  answers the question replaces web research, and re-search uses a **different search vector**
  rather than repeating a prior query.

## 0.1.0 — Initial release

> Pre-1.0: early and evolving — interfaces and gate details may change between 0.x versions.

A user-invoked Claude Code skill: a disciplined feature & bug-fix loop with real,
mechanical quality gates.

### Added
- **8-phase loop** — scope-gate → clarify → understand/diagnose → plan → plan-gate →
  implement → verify → code-review/commit — with **colored phase markers**
  (⚪🔵🟣⚫🟡🟠🟤🟢) so a run reads at a glance in Claude Code.
- **Binary gates, no numeric score.** Reviewers write structured findings to per-round,
  orchestrator-immutable files; the gate counts open BLOCKER/HIGH **mechanically** and
  **fails closed** on missing/empty/malformed input — no self-reported counts, no re-count.
- **Fix mode** — reproduce, prove the root cause (`file:line`), and research the correct
  fix *before* fixing.
- **Self-contained** — every gate/threshold lives in the skill + `reference.md`, never in
  a personal/global `CLAUDE.md`.
- **Bundled hooks** (active only in kimiflow repos — a `.kimiflow/` dir at the git root):
  - `commit-secret-gate` (PreToolUse) — blocks staged secrets and bulk `git add -A`/`.`;
    **fails closed without `jq`**.
  - `test-gate` (opt-in Stop hook) — blocks finishing on red tests; runs **only a local,
    untracked marker** (a committed `.kimiflow/test-gate` is refused — no drive-by `eval`).
  - `test-weakening-scan` (advisory) — flags deleted tests / added skips / removed
    assertions to a non-gating channel, surfaced at the commit-gate.

### Requirements
- `jq` on `PATH` (used by the hooks).

### Notes
- Renamed from `claude-flow` to **kimiflow** to de-collide from `ruvnet/claude-flow`.
