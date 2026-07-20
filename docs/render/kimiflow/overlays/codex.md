---
name: kimiflow
description: "Codex port of the Kimiflow feature and bug-fix loop. Use automatically only for actionable implementation requests for substantial feature work that crosses product surfaces/subsystems, adds material integration/data/security/public-API/architecture risk, or needs real discovery. Discussion, ideation, recommendations, explanations, status requests, and wish formulations stay direct/read-only. Explicit Kimiflow always starts it; explicit direct or direkt always bypasses it. Do not auto-trigger for fixes, reviews, refactors, cleanup, docs/config, or small low-risk features."
---

# Kimiflow For Codex

Run the Kimiflow loop for the user's request.

This Codex skill is the host-native entrypoint for the same Kimiflow engine used by the Claude Code plugin. The canonical workflow lives in the installed plugin root (`SKILL.md` and `reference.md`); read those files before running any phase, then apply the Codex host map below.

## Routing

Apply the frontmatter routing boundary: implementation authority and a material trigger are required. Use the current target without a routing question; raw file count is insufficient. If no trigger is clear, work directly. Explicit overrides win.

## Invocation

Treat these as explicit Kimiflow requests:

- `$kimiflow`
- `@kimiflow`
- `$kimiflow --launcher` / `$kimiflow --menu`
- `$kimiflow full|grill|plan|build|quick|review|audit|fix`
- `$kimiflow <feature-or-bug>`
- `@kimiflow <feature-or-bug>`
- `kimiflow full`, `kimiflow grill`, `kimiflow plan`, `kimiflow build`, `kimiflow quick`, `kimiflow review`, `kimiflow audit`, `kimiflow fix`
- `run kimiflow ...`
- `with kimiflow ...`
- `build/fix this through the Kimiflow gates`

## Host Map

Before invoking any Kimiflow helper script, establish the plugin root from this installed skill file:

1. Treat `KIMIFLOW_SKILL_DIR` as the absolute directory that contains this `skills/kimiflow/SKILL.md` file.
2. Export `KIMIFLOW_PLUGIN_ROOT="$(cd "$KIMIFLOW_SKILL_DIR/../.." && pwd)"`.
3. Export `KIMIFLOW_HOST=codex`.

Never invoke helper scripts through a two-parent relative `hooks` path from the user's project cwd; Codex shell commands run in the workspace, not in the installed skill directory.

Apply the canonical Kimiflow workflow from `$KIMIFLOW_PLUGIN_ROOT/SKILL.md` with these Codex substitutions:

- `/kimiflow` in user-facing text means `$kimiflow` or an explicit "run Kimiflow" prompt in Codex.
- `/kimiflow`, `/kimiflow --launcher`, and `/kimiflow --menu` mean `$kimiflow`, `$kimiflow --launcher`, and `$kimiflow --menu` in Codex. Empty or vague explicit Kimiflow invocations open the context-aware launcher and must use `$KIMIFLOW_PLUGIN_ROOT/hooks/launcher-status.sh` for the status snapshot.
- `/kimiflow full|grill|plan|build|quick|review|audit|fix` maps directly to Codex. `full` forces the strict loop but no approval by itself; `grill|plan|review|audit` are no-code modes until later build authority/selection; `quick` proves product intent or asks one small batch and normally uses a no-worker Discovery pulse; `fix` diagnoses first and pauses only for a material decision; `review` maps to existing-feature/current-change review.
- `/kimiflow --project-map <quick|skip>` means `$kimiflow --project-map <quick|skip>` in Codex. Missing maps, per-section staleness checks, `coverage`-based Phase-2 depth (`compressed|targeted|full`), recommended-but-skippable delta refreshes, focus selection, storage targets, and Improve/Docs publishing use the same canonical Project Map rules and `hooks/project-map-status.sh`. Repo docs are publish-safe derivatives only; raw `.kimiflow/project/` maps and sensitive findings stay local/private unless the user explicitly overrides that policy.
- Kimiflow's Project-Map auto-refresh and lookup use the same `$KIMIFLOW_PLUGIN_ROOT/hooks/project-map-status.sh` in Codex with `KIMIFLOW_HOST=codex`. Phase 7 runs `project-map-status.sh refresh --changed` after verify (re-stamps touched sections to `current`, prunes deleted members, adopts new files by prefix with `NEW-FILE` hints, re-indexes `.sh` `symbols`); Map Bootstrap runs `index-symbols` after writing sections; Phase 2 runs `$KIMIFLOW_PLUGIN_ROOT/hooks/suggest-affected-sections.sh --intent <file>|--text "<terms>"` to rank candidate sections and feed their `paths` to `coverage --affected`. The non-blocking Stop nudge `$KIMIFLOW_PLUGIN_ROOT/hooks/map-staleness-nudge.sh` surfaces residual staleness (rate-limited, USER-visible `systemMessage`, never blocks). All are recommended-but-skippable; raw map facts stay local. → reference.md "Project Map Bootstrap".
- `/kimiflow --verify-feature <feature-or-path>` means `$kimiflow --verify-feature <feature-or-path>` in Codex. Existing feature checks run the canonical Phase-7 code-review ensemble from `reference.md` read-only over the named path or current diff: candidate lens output is verified by the Codex orchestrator before it is promoted to findings, and no code is edited.
- Phase-7 code review uses the canonical Review Ensemble from `reference.md`: pin one review basis, discover cited spec/standards sources, run the `spec-correctness`, `failure-security`, and when relevant `standards-integration` axes, then let the Codex orchestrator verify candidates while preserving axis labels before writing canonical `FINDING` lines to the gate. Raw `CANDIDATE` files never count as blockers until promoted.
- Kimiflow's Active Session and Adaptive Execution contracts use `$KIMIFLOW_PLUGIN_ROOT/hooks/active-run.sh` in Codex. The originating Codex thread owns the run, so only its follow-up prompts remain inside Kimiflow and only its Stop hook may continue the loop. Contract-1 Stop records one bounded turn observation automatically, but coalesces an explicit same-turn observation instead of charging it twice; `status` and `next-action` stay read-only. Use `observe --event <event> --outcome <progress|passed|failed> --evidence .kimiflow/<slug>/<artifact> --write` only for new decisive run artifacts, never prompts or source churn. Other Codex or Claude sessions may read and plan normally; before shared-checkout edits they run `conflict-check --path <path>` for every intended path and proceed only on `allow_disjoint`. Use `append-item`, `mark-built`, `mark-accepted`, `mark-rejected`, `drop-item`, `refresh-baseline`, and `finish|park|fail|abort --write` exactly as the canonical workflow describes.
- Workspace inventory and guarded cleanup use `$KIMIFLOW_PLUGIN_ROOT/hooks/workspace-preflight.sh`. Worktrees below `${CODEX_HOME:-~/.codex}/worktrees` are Codex-managed: report them and use task archive/retention controls, never remove them directly from Kimiflow.
- Kimiflow's workspace start uses `$KIMIFLOW_PLUGIN_ROOT/hooks/workspace-preflight.sh` before `$KIMIFLOW_PLUGIN_ROOT/hooks/working-tree-gate.sh`. Inventory every worktree and the current branch/changes first; establish the schema-4 active session before any ambiguity prompt, default to the current worktree, preserve every file, and batch genuine cleanup ambiguity into at most one STATE-backed `workspace` decision across park/resume. Do not repeatedly ask the user to commit/stash/clean. Guarded exceptional-tree retirement pins identity, archives the complete checkout, and detaches only the matching Git metadata. After the safe disposition, normal write runs require `WORKING_TREE_GATE OPEN` before editing.
- Kimiflow's clarify gate uses `$KIMIFLOW_PLUGIN_ROOT/hooks/clarify-gate.sh`. New Contract-2 features prove product-intent provenance, allow zero or one product-question batch, forbid technical HOW questions, and use a bounded critic for large/critical work; only an actual build needs current authority. The answer becomes the contract with no second confirmation. Older runs, audits, fixes, and schema 3 retain their compatible contracts.
- Kimiflow's Current-State Pulse / Gate uses `$KIMIFLOW_PLUGIN_ROOT/hooks/current-state-gate.sh` in Codex. Run it for every non-trivial run; for small/quick, low risk records no external freshness research, while medium/high requires a bounded current primary-source check before planning.
- Kimiflow's feature Discovery Gate uses `$KIMIFLOW_PLUGIN_ROOT/hooks/discovery-gate.sh`. Sol owns assessment, brief, source evaluation, synthesis, Decision Triage, and the plain-language build summary/material-risk decision. Luna/Terra may collect bounded evidence only; no worker is default, focused uses one normally and at most two independent lanes.
- Kimiflow's adaptive implementation-conformance gate uses `$KIMIFLOW_PLUGIN_ROOT/hooks/conformance-gate.sh`. Contract-1 feature/fix plans bind at most five material decisions to current evidence, affected paths, ACs, invariants, and exact checks. Small runs fold the judgment into Sol; large runs reuse their one existing independent verifier. Drift routes autonomously to Phase 5 or Phase 2, and `finish` proves HEAD/index/worktree delivery before and after transactional learning.
- Kimiflow's fix-mode Red-Green Gate uses `$KIMIFLOW_PLUGIN_ROOT/hooks/red-green-gate.sh` in Codex. A fix run records Red/Green/Regression evidence in `BUG-REPRO.md`; `RED_GREEN_GATE OPEN` is required before Phase 7, learning promotion, or `Status: done`.
- Kimiflow's lazy frontend lane uses `$KIMIFLOW_PLUGIN_ROOT/hooks/frontend-quality-gate.sh`. Phase 0 records only the clean Contract-1 start; Phase 2 loads `$KIMIFLOW_PLUGIN_ROOT/references/frontend-quality-standard.md` and the optional Flagship delta only for UI features/polish; Phase 6 loads the plugin-rooted QA file and requires `FRONTEND_QUALITY_GATE OPEN`; Phase 7 rechecks the serialization preflight. Fix/audit/off and legacy/read-only runs never receive design payloads or synthetic screenshots.
- Kimiflow's local diagnostics advisory uses `$KIMIFLOW_PLUGIN_ROOT/hooks/lsp-diagnostics.sh` in Codex. It runs a bounded set of existing local diagnostics tools or one untracked `.kimiflow/lsp-diagnostics` command, never installs anything, rejects free-form CLI commands, classifies `FLAG`s by changed-file relevance, and routes them to `ADVISORIES.md`.
- Kimiflow's Memory Router uses `$KIMIFLOW_PLUGIN_ROOT/hooks/memory-router.sh` in Codex. Launcher status exposes one `primary_action` plus bounded install, maintenance, memory, history, economics, provider, Vault and proposal state. Phase 2/7 follow the same canonical rules as Claude Code: schema-v2 recall with one global context budget/hit cap, cross-source deduplication, advisory authority with current project sources winning, fingerprint-checked atomic FTS refresh, bounded old-run history, run-local `RECALL.json`, at most one verified successful and failed outcome strategy, automatic terminal outcome evaluation, usage/economics/lifecycle curation, optional authenticated Vault Pulse/prefetch/sync, stale-evidence supersession, path sanitization, user-profile memory, consolidation, security scanning, and review-only rule/skill proposals. A prior-work cue still replaces broad recall/Vault with one targeted local query; learning/evaluation runs at every scope.
- `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}` means the installed Kimiflow plugin root. In Codex, use `KIMIFLOW_PLUGIN_ROOT`.
- When invoking Kimiflow helper scripts from Codex, set `KIMIFLOW_HOST=codex`.
- `TaskCreate` / `TaskUpdate` means use Codex's task plan/status updates.
- Claude Code subagent names map to Codex subagents as follows:
  - bounded file/symbol/map/log lookup: use a Codex `explorer` with `model: gpt-5.6-luna` and `reasoning_effort: low|medium`; the `top` model still performs Phase-2 synthesis.
  - normal implementation or fix worker: use a Codex `worker` with `model: gpt-5.6-terra` and `reasoning_effort: medium|high`; promote to Sol for a named high-risk trigger.
  - planning, review, independent semantic verification, risky diagnosis, or general quality work: use a Codex `default` with `model: gpt-5.6-sol` and `reasoning_effort: high|xhigh` unless a `cross_family_top` seat applies.
- Codex capability mapping is `top=gpt-5.6-sol`, `balanced=gpt-5.6-terra`, `cheap=gpt-5.6-luna`. Prefer Sol for the active Kimiflow session. If Codex exposes a Terra/Luna session, record the quality fallback and continue without a model-switch prompt; use deliberate Sol review/verification seats when available. Never use `ultra` inside Kimiflow because it adds nested automatic delegation under Kimiflow's own orchestrator.
- Kimiflow's cross-family seats (→ canonical `reference.md` "Model routing (per-role)") use the `claude` CLI on the Codex host: attempt condition `command -v claude`, invocation `claude -p --model fable --effort high "<prompt>"` (the final message is stdout). This pins the current strongest Claude tier; unavailable/refused/limited calls use the normal sticky same-family fallback. Never inherit an unverified default/cheap tier.
- `WebSearch` / `WebFetch` means Codex web/search or another available current-source tool. For current external technical facts, prefer primary sources.
- `CLAUDE.md` is a Claude project convention file. In Codex, read `AGENTS.md` first, and also read `CLAUDE.md` if it exists because Kimiflow historically treats it as a conventions hint.

## Gate Commands

Use the bundled scripts as the only mechanical source of truth:

- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-review-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/plan-blocker-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/conformance-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-build-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-verbosity.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/working-tree-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/workspace-preflight.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/clarify-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/current-state-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/discovery-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/red-green-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/frontend-quality-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/lsp-diagnostics.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/launcher-status.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/active-run.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/memory-router.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/test-weakening-scan.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/secret-content-scan.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/project-map-status.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/suggest-affected-sections.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/map-staleness-nudge.sh`

For Codex invocations, call them with `KIMIFLOW_HOST=codex`, for example:

```bash
KIMIFLOW_HOST=codex "$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-review-gate.sh" .kimiflow/<slug>/findings --round 1 --expect code-verified
```

Do not replace these scripts with model judgment. If a resolver says the gate is closed, the gate is closed.

## Output

Reply in the user's language. Keep Kimiflow's terse output rule from the canonical workflow: visible chat is control-plane only; artifacts and evidence go to files.
