---
name: kimiflow
description: "Codex port of the Kimiflow feature/fix/release loop. Use automatically only for actionable implementation requests for substantial feature work that crosses product surfaces/subsystems, adds material integration/data/security/public-API/architecture risk, or needs real discovery. Discussion, ideation, recommendations, explanations, status requests, and wish formulations stay direct/read-only. Explicit Kimiflow always starts it; explicit direct or direkt always bypasses it. Do not auto-trigger for fixes, reviews, refactors, cleanup, docs/config, or small low-risk features."
---

# Kimiflow For Codex

Run the Kimiflow loop for the user's request.

Read installed `SKILL.md` once. Per phase call `active-run.sh phase-read --run .kimiflow/<slug> --phase <N> --file phases/<phase-file> --packet --write`; trust its packet and do not read phase/reference files separately. Never preload all `reference.md`. Then apply this host map.

## Routing

Apply the frontmatter routing boundary: implementation authority and a material trigger are required. Use the current target without a routing question; raw file count is insufficient. If no trigger is clear, work directly. Explicit overrides win.

## Invocation

Treat these as explicit Kimiflow requests:

- `$kimiflow`
- `@kimiflow`
- `$kimiflow --launcher` / `$kimiflow --menu`
- `$kimiflow full|grill|plan|build|quick|review|audit|fix|release`
- `$kimiflow <task>`
- `kimiflow full`, `kimiflow grill`, `kimiflow plan`, `kimiflow build`, `kimiflow quick`, `kimiflow review`, `kimiflow audit`, `kimiflow fix`, `kimiflow release`
- `run kimiflow ...`
- `with kimiflow ...`

## Host Map

Take the normalized absolute plugin-root path two levels above this installed `skills/kimiflow/SKILL.md`.
Copy that literal path into each helper command: `KIMIFLOW_HOST=codex /absolute/plugin/root/hooks/<helper>`.
Do not assign or expand `KIMIFLOW_PLUGIN_ROOT` inside the command; never use project-relative hooks.
Do not probe helpers with `--help` during a run; use the exact documented command shapes.

Apply the canonical Kimiflow workflow from `$KIMIFLOW_PLUGIN_ROOT/SKILL.md` with these Codex substitutions:

- `/kimiflow` in user-facing text means `$kimiflow` or an explicit "run Kimiflow" prompt in Codex.
- `/kimiflow`, `/kimiflow --launcher`, and `/kimiflow --menu` mean `$kimiflow`, `$kimiflow --launcher`, and `$kimiflow --menu` in Codex. Empty or vague explicit Kimiflow invocations open the context-aware launcher and must use `$KIMIFLOW_PLUGIN_ROOT/hooks/launcher-status.sh` for the status snapshot.
- `/kimiflow full|grill|plan|build|quick|review|audit|fix|release` maps directly to Codex. `full` forces the strict loop but no approval by itself; `grill|plan|review|audit` are no-code modes until later build authority/selection; `quick` proves product intent or asks one small batch and normally uses a no-worker Discovery pulse; `fix` diagnoses first and pauses only for a material decision; `review` maps to existing-feature/current-change review; `release` authorizes one project-profile release.
- `/kimiflow --project-map <quick|skip>` means `$kimiflow --project-map <quick|skip>` in Codex. Missing maps, per-section staleness checks, `coverage`-based Phase-2 depth (`compressed|targeted|full`), recommended-but-skippable delta refreshes, focus selection, storage targets, and Improve/Docs publishing use the same canonical Project Map rules and `hooks/project-map-status.sh`. Repo docs are publish-safe derivatives only; raw `.kimiflow/project/` maps and sensitive findings stay local/private unless the user explicitly overrides that policy.
- Kimiflow's Project-Map auto-refresh and lookup use the same `$KIMIFLOW_PLUGIN_ROOT/hooks/project-map-status.sh` in Codex with `KIMIFLOW_HOST=codex`. Phase 7 runs `project-map-status.sh refresh --changed` after verify (re-stamps touched sections to `current`, prunes deleted members, adopts new files by prefix with `NEW-FILE` hints, re-indexes `.sh` `symbols`); Map Bootstrap runs `index-symbols` after writing sections; Phase 2 runs `$KIMIFLOW_PLUGIN_ROOT/hooks/suggest-affected-sections.sh --intent <file>|--text "<terms>"` to rank candidate sections and feed their `paths` to `coverage --affected`. The non-blocking Stop nudge `$KIMIFLOW_PLUGIN_ROOT/hooks/map-staleness-nudge.sh` surfaces residual staleness (rate-limited, USER-visible `systemMessage`, never blocks). All are recommended-but-skippable; raw map facts stay local. → reference.md "Project Map Bootstrap".
- `/kimiflow --verify-feature <feature-or-path>` means `$kimiflow --verify-feature <feature-or-path>` in Codex. Existing feature checks run the canonical Phase-7 code-review ensemble from `reference.md` read-only over the named path or current diff: candidate lens output is verified by the Codex orchestrator before it is promoted to findings, and no code is edited.
- `kimiflow security scan` / `security diff|deep|ci-artifact|eval|promote` stays private/advisory and fail-closed.
- Large `full|plan` after final `confirmed`: one Sol `fork_turns="none"` worker replaces parent planning through clean Phase 4. Pass literal roots/run/Active owner; helper writes set `KIMIFLOW_SESSION_HOST=codex KIMIFLOW_SESSION_ID=<owner>`. Parent never duplicates and waits with `timeout_ms=3600000`, not polling. Return only `READY_PHASE5|NEEDS_DECISION`.
- Phase 4: round 1 uses independent frozen-plan lenses; round 2 uses one fresh whole-contract verifier for repairs, counterexamples, and exact lens partitions; round 3 repeats only if still material. Seal once per batch; global cap=3 never resets.
- Phase-7 Review Ensemble runs every axis once per stable PLAN, then required repair deltas. Mandatory schema-4 saturation authenticates bounded bug-cascade evidence; only source-bound schema-3 repairs for that exact source are accepted. Protected impacts cannot be waived; stable classes and the absolute three-round-plus-zero-carry-closeout cap stop ping-pong.
- Active Session and Adaptive Execution contracts use `active-run.sh`; the originating task owns the run. Stop coalesces an explicit same-turn observation; `status`/`next-action` are read-only and `observe` records only decisive artifacts. For pending context rollover, use one fresh `top` worker with `fork_turns="none"`. Pass the literal workspace root, literal plugin root, exact allowed status/read/handoff commands, run/packet identity, retained artifact digests, phase, and named code; the worker must not rediscover helpers or inspect hook internals. Persist its result, then call `rollover-handoff` with the exact ID/digest. Shared-checkout edits require `conflict-check`=`allow_disjoint`; use canonical item/terminal helpers.
- `$KIMIFLOW_PLUGIN_ROOT/hooks/workspace-preflight.sh` routes: free Primary direct; busy/dirty gets ≤3 locked owned Fleet trees, then FIFO. Work in its returned root. Phase 3 `declare`s exclusive Primary/Fleet path-contract leases + `blocked_by` against PLAN; Phase 5 requires `write-gate` and post-advance `revalidate`. Commit, `integrate` the JSON-argv-checked combined candidate, then `needs-reconcile` or finish/`retire`. Never mutate manual/Codex trees or request routine commit/stash/clean; ask only about ambiguous foreign bytes and require `WORKING_TREE_GATE OPEN`.
- Kimiflow's clarify gate uses `$KIMIFLOW_PLUGIN_ROOT/hooks/clarify-gate.sh`. Fresh Contract-4 features preserve user language and require two explicit actions: discuss/accept scope, then correct/confirm the final flow. Generic assent never confirms; content-free receipts and the intent lock bind both stages. Contract-3/schema-1 resumes.
- Large/critical Codex runs must visibly spawn exactly one independent bounded Intent Critic with `fork_turns="none"` before writing `INTENT.md`; local judgment may never set `critic=passed`. “Do not parallelize implementation” does not disable this verification.
- The host hook records an exact intake action. On resume call `status` once and trust its stage/action; never inspect receipt-hook internals or manufacture a hook response. Continue Phase 1 after `scope_ready`; after `confirmed`, write intent and run the clarify gate.
- Before `confirmed`, do not call `append-item`, create/update the task widget, or patch phase progress; `phase-read` advances phase state mechanically.
- Kimiflow's Current-State Pulse / Gate uses `$KIMIFLOW_PLUGIN_ROOT/hooks/current-state-gate.sh` in Codex. Run it for every non-trivial run; only an explicitly changing/current external dependency, platform, API, or method is medium/high and requires a subject-bound fresh primary source. A local public API is low-risk by itself. Persisted schema-1 runs keep legacy recall.
- Discovery uses `$KIMIFLOW_PLUGIN_ROOT/hooks/discovery-gate.sh`; `$KIMIFLOW_PLUGIN_ROOT/hooks/codebase-basis.sh` first binds HEAD and affected-path bytes/types. Prove `reuse → evolve → new`, compare research to that basis, and reject scope expansion.
- Conformance uses `$KIMIFLOW_PLUGIN_ROOT/hooks/conformance-gate.sh`. Bind up to five material decisions to evidence/paths/ACs/checks and `review_only|spike_required|runtime_required`; required spikes bind executed fixture, command, and output. Trace Contract-4 requirements like Contract 3.
- Decision-bearing artifacts, findings, learnings, and final reports load all of `$KIMIFLOW_PLUGIN_ROOT/references/workflow-prose-quality.md` only at their existing writing/review boundary and apply it in the same model pass; it adds no agent, model call, step, or gate.
- Kimiflow's fix-mode Red-Green Gate uses `$KIMIFLOW_PLUGIN_ROOT/hooks/red-green-gate.sh` in Codex. A fix run records Red/Green/Regression evidence in `BUG-REPRO.md`; `RED_GREEN_GATE OPEN` is required before Phase 7, learning promotion, or `Status: done`.
- Kimiflow's lazy frontend lane uses `$KIMIFLOW_PLUGIN_ROOT/hooks/frontend-quality-gate.sh`. Phase 0 records only the clean Contract-1 start; Phase 2 loads `$KIMIFLOW_PLUGIN_ROOT/references/frontend-quality-standard.md` and the optional Flagship delta only for UI features/polish; Phase 6 loads the plugin-rooted QA file and requires `FRONTEND_QUALITY_GATE OPEN`; Phase 7 rechecks the serialization preflight. Fix/audit/off and legacy/read-only runs never receive design payloads or synthetic screenshots.
- Kimiflow's local diagnostics advisory uses `$KIMIFLOW_PLUGIN_ROOT/hooks/lsp-diagnostics.sh` in Codex. It runs a bounded set of existing local diagnostics tools or one untracked `.kimiflow/lsp-diagnostics` command, never installs anything, rejects free-form CLI commands, classifies `FLAG`s by changed-file relevance, and routes them to `ADVISORIES.md`.
- Kimiflow's Memory Router uses `$KIMIFLOW_PLUGIN_ROOT/hooks/memory-router.sh`. Keep canonical bounded local-first recall/history/outcome/provider/Vault behavior; a prior-work cue replaces broad recall/Vault Pulse with one targeted query. Confirmed root causes may cite up to eight validated `Source evidence:` paths whose byte drift stales the learning.
- `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}` means the installed Kimiflow plugin root. In Codex, use `KIMIFLOW_PLUGIN_ROOT`.
- When invoking Kimiflow helper scripts from Codex, set `KIMIFLOW_HOST=codex`.
- `TaskCreate` / `TaskUpdate` means use Codex's task plan/status updates.
- Claude Code subagent names map to Codex subagents as follows:
  - bounded file/symbol/map/log lookup: use a Codex `explorer` with `model: gpt-5.6-luna` and `reasoning_effort: low|medium`; the `top` model still performs Phase-2 synthesis.
  - normal implementation or fix worker: use a Codex `worker` with `model: gpt-5.6-terra` and `reasoning_effort: medium|high`; promote to Sol for a named high-risk trigger.
  - planning, review, independent semantic verification, risky diagnosis, or general quality work: use a Codex `default` with `model: gpt-5.6-sol` and `reasoning_effort: high|xhigh` unless a `cross_family_top` seat applies.
- Codex maps `top=Sol`, `balanced=Terra`, `cheap=Luna`; prefer Sol, record an exposed lower-tier fallback, and use Sol review seats when available. Never use `ultra`: Kimiflow owns delegation.
- Keep Frontier prompts lean: goal, user boundaries, relevant evidence/tools, output contract, and exit criteria. Avoid provider folklore, forced reasoning, or generic checklists. A model/profile/prompt change gets a new fingerprint and must recalibrate adaptive savings.
- Kimiflow's cross-family seats (→ canonical `reference.md` "Model routing (per-role)") use the `claude` CLI on the Codex host: attempt condition `command -v claude`, invocation `claude -p --model fable --effort high "<prompt>"` (the final message is stdout). This pins the current strongest Claude tier; unavailable/refused/limited calls use the normal sticky same-family fallback. Never inherit an unverified default/cheap tier.
- `WebSearch` / `WebFetch` means Codex web/search or another available current-source tool. For current external technical facts, prefer primary sources.
- `CLAUDE.md` is a Claude project convention file. In Codex, read `AGENTS.md` first, and also read `CLAUDE.md` if it exists because Kimiflow historically treats it as a conventions hint.

## Gate Commands

Use the bundled scripts as the only mechanical source of truth:

- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-review-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/plan-review-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/plan-blocker-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/conformance-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-build-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-verbosity.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/working-tree-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/workspace-preflight.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/clarify-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/codebase-basis.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/current-state-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/review-convergence-gate.sh`
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
