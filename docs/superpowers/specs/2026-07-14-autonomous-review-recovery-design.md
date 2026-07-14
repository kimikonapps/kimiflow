# Autonomous Review Recovery

**Date:** 2026-07-14

**Status:** Draft for review

## Goal

Close Kimiflow's plan- and code-review loops without repeated "continue?" prompts. A review cap ends
the current strategy, not the run. Technical `BLOCKER/HIGH` findings must trigger an autonomous,
evidence-guided strategy change until the review gate is clean. The gate remains strict: Kimiflow
never builds or finishes with an open `BLOCKER/HIGH` and never downgrades one to escape the loop.

User interaction remains valid only when new authority or product input is required: missing
credentials/access, contradictory requirements, paid/privacy-sensitive infrastructure, scope change,
or an irreversible public/data/migration decision.

## Research Input

- Anthropic's [Ralph Wiggum plugin](https://github.com/anthropics/claude-code/blob/main/plugins/ralph-wiggum/README.md)
  proves that a Stop hook can keep one session working without a human repeatedly saying "continue".
  Its hook persists an iteration counter, blocks Stop, and feeds the same prompt back until a verified
  completion promise or iteration limit. Kimiflow should reuse its existing active-run Stop gate, but
  not Ralph's same-prompt retry because that can repeat a failed strategy.
- Superpowers' [Subagent-Driven Development](https://github.com/obra/superpowers/blob/main/skills/subagent-driven-development/SKILL.md)
  executes continuously and says a blocked worker must receive more context, a stronger model, a
  smaller task, or a corrected plan; retrying the same model unchanged is forbidden. Its durable
  progress ledger also prevents lost context from repeating completed work.
- Superpowers' [Systematic Debugging](https://github.com/obra/superpowers/blob/main/skills/systematic-debugging/SKILL.md)
  returns to evidence and a new hypothesis after a failed fix, then questions the architecture after
  repeated failures. This is the right recovery ladder, except Kimiflow should make the architecture
  pivot autonomous when it stays inside confirmed product scope.
- GSD's [autonomous workflow](https://github.com/gsd-build/get-shit-done/blob/main/get-shit-done/workflows/autonomous.md)
  already chains review to auto-fix, but after one unsuccessful gap-closure attempt it asks the user
  whether to retry, skip, or stop. That is the babysitting boundary Kimiflow should deliberately avoid.
- Superpowers later replaced repeated plan/spec subagent reviews with a small inline self-review after
  measured overhead without quality gain; see its [release notes](https://github.com/obra/superpowers/blob/main/RELEASE-NOTES.md#v506-2026-03-24).
  Kimiflow should spend fresh reviewers on changed strategies, not add reviewer calls merely because a
  recovery epoch started.

## Decision

Keep the existing binary review gate and existing active-session Stop hook. Introduce **strategy
epochs** inside one run:

- `small`/`quick`: two review rounds per strategy epoch;
- `large`/`audit`/release-critical: three review rounds per strategy epoch;
- global round numbers and findings remain append-only and never reset;
- a new epoch begins only after a recovery packet records a materially different hypothesis or
  implementation strategy and the relevant plan/code basis fingerprint changes;
- there is no run-global technical retry cap that routes to the user.

"Materially different" means changing at least one evidenced root-cause hypothesis, algorithm/control
flow, integration pattern, architecture boundary, or task decomposition that can resolve the blocker.
Rewording the same plan, changing only the reviewer/model, or editing whitespace is not a strategy change.

The resolver remains the source of truth for whether review is clean. `OPEN/clean` alone permits the
next phase. `CLOSED` carries a machine action:

| Reason | Automatic action |
|---|---|
| `incomplete` / `malformed` | Repair reviewer transport/artifact and rerun the same round. |
| `open-findings` with progress inside the epoch | Apply the smallest evidenced repair and run the next round. |
| `oscillation` / `reappeared` / `cap-reached` | Enter autonomous recovery; never call `await-user`. |

`resolve-review-gate.sh` gains an optional epoch boundary while preserving legacy behavior. Without
the new argument, current global-cap semantics remain unchanged. With `--epoch-start <N>`, the
absolute `--cap <M>` applies to that epoch and anti-oscillation/reappearance comparisons do not cross
the epoch boundary. This permits global round numbers such as epochs `1–2`, `3–4`, `5–6` without
silently resetting or overwriting the findings ledger.

## Recovery Controller

On `oscillation`, `reappeared`, or `cap-reached`, the owner orchestrator persists a compact recovery
packet in `RECOVERY.md` and continues through the existing Stop gate:

1. Group the current blocker identities and state why the previous strategy failed.
2. Re-check the cited code, intent/acceptance boundary, and reviewer evidence.
3. Search bounded local run history and project memory using the blocker packet. Recovery enables
   targeted recall even for `small/quick`, because a failed strategy now justifies the lookup cost.
4. For uncovered or stale technical gaps, research current primary sources. Repeat neither an old
   query nor a source already shown insufficient.
5. Form one new falsifiable hypothesis and run the smallest useful spike/test when uncertainty is
   executable.
6. Revise the plan/architecture or implementation. The new strategy basis fingerprint must differ
   from the previous epoch; renaming the epoch or rewording the same approach is invalid.
7. Run the normal pre-review checks, then start the next global review round with the new epoch bound.

Plan recovery re-enters Phase 2/3 and reruns Current State, Discovery, plan-blocker, AC mapping, and
subtraction as applicable. Code recovery re-enters diagnosis/implementation/verification, preserves
Red/Green evidence for fixes, and reruns affected tests before code review.

The escalation ladder is progressive and token-aware:

1. top orchestrator re-analysis with existing evidence;
2. bounded memory/history recall;
3. focused current-source research;
4. minimal spike or reproduction refinement;
5. alternative architecture/task decomposition;
6. one independent `top`/`cross_family_top` recovery solver when the prior strategy or architecture
   has failed twice.

Each step consumes the prior packet and produces only a compact delta. A fresh review worker receives
the confirmed intent, current strategy, decisive evidence, and named files—not the full run transcript.

## Durable State and Learning

`RECOVERY.md` is append-only and stores one compact section per epoch:

- phase and trigger verdict;
- source round, next epoch start, and absolute epoch cap;
- open blocker identities;
- failed hypothesis/strategy;
- new hypothesis/strategy and decisive evidence references;
- before/after basis fingerprints;
- outcome: `active|clean|superseded`.

The plan fingerprint covers the intent/problem, research/diagnosis, plan, and acceptance artifacts. The
code fingerprint covers the pinned review basis, declared affected paths and their content/status, acceptance,
and fix reproduction evidence when present. A changed content fingerprint is necessary but not sufficient:
the top orchestrator must also verify the material-strategy definition above before opening an epoch.

`STATE.md` holds only the current epoch number, start round, cap, strategy fingerprint, and
`Recovery: active|clean`. This lets compaction or Stop-hook continuation resume without replaying
chat history.

Failed strategies remain run-local evidence, not promoted as truth. After a clean verified run, the
existing Memory Router learning review may promote both the successful solution and the evidenced
trap/pitfall that made the earlier strategy fail. Existing confidence, freshness, sensitivity, and
evidence gates continue to apply.

## User Interaction Contract

Kimiflow must not ask the user to:

- authorize another review/recovery epoch;
- choose between reversible technical implementations;
- confirm a model/context/architecture escalation inside approved scope;
- acknowledge a review cap or oscillation.

Kimiflow may ask once when progress requires product authority or inaccessible external state. Such a
question uses the existing `await-user` mechanism and names the exact missing decision/access. A
technical blocker never becomes `await-user` merely because recovery is expensive or has taken many
epochs. The user can still explicitly park, abort, or change scope.

## Token Efficiency

- No new Stop hook, daemon, heartbeat, or repeated static prompt: reuse `active-run.sh stop-gate`.
- Normal clean runs pay zero recovery cost.
- First-epoch progress uses the existing targeted repair path without extra research or solver calls.
- Recall and web research are query-bounded by the current blocker packet.
- Memory precedes web; fresh relevant memory replaces research.
- Only one recovery solver runs at a time; cross-family escalation is conditional.
- Existing reviewer counts remain unchanged per epoch; inline subtraction/self-review happens before a
  fresh external review.
- Chat remains control-plane only; `RECOVERY.md` and `STATE.md` carry durable detail.

## Compatibility and Safety

- Legacy calls without `--epoch-start` retain the current resolver behavior and test expectations.
- Review remains fail-closed: epoch recovery cannot produce `OPEN`; only a clean findings round can.
- Round files remain immutable and globally numbered.
- MEDIUM/LOW stay advisory and never trigger recovery.
- Scope cannot expand through recovery. A required scope or irreversible product change routes to the
  user instead.
- Claude Code and Codex use the same canonical artifacts and resolver semantics. Host wrappers only
  map tools/models.

## Verification

1. A `small` run with an open HIGH at round 2 emits `cap-reached`, records recovery, and continues
   without `await-user`.
2. A `large` run with non-decreasing blocker count enters recovery before its cap.
3. A reappeared finding enters recovery rather than being downgraded or shown to the user as a
   continue prompt.
4. A clean round at an epoch cap opens; any round beyond that epoch cap remains closed.
5. Anti-oscillation is enforced within an epoch but does not compare the first round of a materially
   new epoch against the failed strategy.
6. Global round numbering and all prior finding files remain intact across epochs.
7. An unchanged strategy fingerprint cannot justify a new epoch.
8. Recovery recall is bounded, works for `small/quick`, and falls back to current primary-source
   research only for uncovered/stale gaps.
9. A purely technical blocker never sets `awaiting_user`; a synthetic missing-credential or
   irreversible-scope decision still may.
10. Phase 4 and Phase 7 behavioral evals, resolver tests, active-run tests, host smokes, install parity,
    and the full local test suite pass.

## Non-goals

- Automatically waiving or lowering valid findings.
- Guaranteeing progress when credentials, external systems, or product authority are unavailable.
- Running broad memory or web searches on every normal review.
- Adding a second orchestration framework beside the existing active-run contract.
