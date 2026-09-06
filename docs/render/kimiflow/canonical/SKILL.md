---
name: kimiflow
description: "Lean, verified feature/fix delivery. Auto-route only actionable implementation requests for substantial features with material integration, data, security, architecture, or discovery risk. Discussion, ideation stays direct/read-only. Explicit Kimiflow starts; explicit direct or direkt always bypasses. Do not auto-trigger for fixes, reviews, refactors, cleanup, docs/config, or low-risk features."
disable-model-invocation: false
argument-hint: "[full|grill|plan|build|quick|review|audit|fix|release] [target] [--resume <slug>]"
---

# Kimiflow

Work on **$ARGUMENTS**, or the current request when the host passes the task separately.

Deliver the requested result with the chosen model and the host's existing tools. New work uses the short loop below. There is no required phase ledger, model promotion, memory write, fixed reviewer ensemble, or routine confirmation.

## Entry and compatibility

- **Managed host context:** a supplied Kimiflow `workflow_context` with `phase_manifest` and `run_bridge` selects the managed Legacy workflow, including before an active run exists. Load [legacy-workflow.md](references/legacy-workflow.md), and `references/legacy-codex.md` on Codex, instead of the lean loop below. `SKILL.md` remains the public adapter-v1 entry dispatcher.

- A concrete authorized request starts immediately. Use the conversation's target; ask only when it is genuinely missing. Explicit `direct` / `direkt` bypasses Kimiflow.
- Before editing, inspect Git status and existing run ownership. If `.kimiflow/session/ACTIVE_RUN.json` exists, call installed `hooks/active-run.sh status` once. Continue a matching or explicitly resumed unfinished run with [legacy-workflow.md](references/legacy-workflow.md). Preserve unrelated runs and use an isolated host worktree for independent work; do not migrate old state or bypass a pending decision.
- **Launcher / menu:** an empty or vague invocation runs installed `hooks/launcher-status.sh` once and shows the relevant action. No automatic implementation.
- **Natural mode aliases:** `full|grill|plan|build|quick|review|audit|fix|release`. `full` means thorough coverage of the actual risks and does not create an approval stop. `quick` uses the same correctness boundaries with a smaller scope. `fix` reproduces before changing production code. `grill` clarifies only; `plan` / `--prepare` produces a resumable plan only; `review` / `--verify-feature <feature-or-path>` inspects only; `audit` reports removable responsibilities and concrete evidence before editing. `build` implements an existing plan under the user's build authority, revalidating its basis. Do not treat read-only modes as build permission.
- `--resume <slug>` reads the named run. A `STATE.md` containing `Flow schema` uses the legacy instructions. A new `NOTES.md` is a plain handoff: inspect current code and checks, then continue the loop below.
- `release`, managed FirstMate crews, headless `kimiflow run`, and explicitly requested optional tools use [optional-tools.md](references/optional-tools.md). Load only the relevant entry. Ordinary Pi/local sessions use this same loop.

## Understand

Read the applicable project instructions and affected code. State the goal, observable acceptance and material assumptions briefly. Search existing behavior before designing a replacement. Research current external facts only where they affect the decision; do not expand product scope from research.

Reuse the user's existing authorization. Ask when missing product input or a new material cost, privacy, compatibility or irreversible consequence changes the outcome. A complete request needs no final confirmation ritual.

Use a short host-native plan when it helps. For a long task or handoff, keep one local `.kimiflow/<slug>/NOTES.md` with goal, starting revision, affected paths, decisions, open work and check results. It is advisory, contains no secrets and creates no new run schema. Do not create it for work that fits in the current task. Existing memory is queried only for a concrete prior-work cue or known project fact; current code wins.

## Build

Work sequentially by default. Delegate only a concrete, independent subtask when doing so saves time or adds needed expertise. Give it the goal, relevant files, boundaries, expected result and stopping condition. No fixed seat count or provider chain.

Respect the user's selected model, including GPT-6 Astra, Fable 5.1 and local models. Inherit the host's model and effort; never silently switch to a paid service or downgrade implementation. Model names do not determine competence. For limited context or unreliable tool use, use smaller coherent tasks, exact commands and a short handoff. Do not add calibration ledgers or repeat a whole transcript.

Preserve existing edits and staging. Use the host's worktree support if ownership overlaps or is unclear. Do not stash, reset, clean or integrate unrelated work. Keep changes within the acceptance criteria; tests should exercise behavior rather than mirror the patch.

On a reproduced failure, investigate its cause before another patch. After two attempts with the same failing assumption, change diagnosis or approach. Stop and explain a concrete remaining blocker when no justified route remains; never invent a clean result.

## Verify and deliver

Execute the project's relevant acceptance, regression and build/runtime checks. For fixes, retain the failing reproduction and passing regression. For UI work inspect the actual rendered result. A check being unavailable is a stated limitation, not a pass.

For code changes in a Git worktree, run those commands once through installed `hooks/check-change.sh`, for example:

```bash
/absolute/plugin/root/hooks/check-change.sh --root /absolute/project --check '["python3","-m","unittest","discover"]'
```

Replace the example with the project's real argv; repeat `--check` for several checks. Commands execute without an implicit shell. Explicit shell scripts are permitted when they are the project's intended check. The helper binds HEAD, index and source bytes, returns bounded failure output, stops on a failing check, and rejects differences between source snapshots before and after each check. On POSIX, surviving processes in the check’s process group are stopped and reported as an incomplete check. Checks must finish their work synchronously; this is not continuous filesystem monitoring. It writes no receipts and makes no commit. Ignored build output and untracked `.kimiflow/` notes are excluded; tracked files are always included. If a formatter changes source, inspect its changes and rerun the affected checks. A later code change invalidates the result. Non-Git projects run the checks directly.

Review the diff against acceptance. Use one independent review for concrete security/data-loss, migration, concurrency, public-contract risks, or repeated failures when the host supports it. Additional specialist review requires a named uncertainty. A model opinion is not an executed test; absence of a repeated finding is not proof of resolution. Recheck actual defects and their affected callers. If no independent reviewer is available, use targeted counterexamples and disclose that limitation without claiming independence.

Before committing, inspect named paths and the staged diff; use existing secret and test-weakening checks where relevant. Preserve foreign staged entries and commit only intended paths with `git commit --only -m "<message>" -- <named paths>` under the original build authority. Respect an instruction not to commit. Push, publication and irreversible operations require their own authorization. The helper verifies checks; host permissions and Git operations enforce their own boundaries, not a semantic quality guarantee.

Finish with what changed, what was checked and any unresolved limitation. Optional notes or memory maintenance never block a successful delivery. Give useful progress updates in the user's language; no phase announcements, gate narration or separate prose-quality pass.
