# Autonomous Review Recovery

**Date:** 2026-07-14

**Status:** Approved and implemented

## Goal and Scope

Close Kimiflow's plan- and code-review repair loops without repeated "continue?" prompts. A review
cap ends the current strategy, not the run. Technical `BLOCKER/HIGH` findings trigger an autonomous,
evidence-guided strategy change until the review gate is clean. Kimiflow never builds or finishes
with an open `BLOCKER/HIGH` and never downgrades one to escape the loop.

This change removes only recovery babysitting. Existing deliberate workflow gates—such as the
one-time pre-build approval selected by policy and the final commit gate—remain unchanged. Recovery
does not repeat those gates while work stays inside the already confirmed intent, scope, and risk.

## Research Review

- Anthropic's [Ralph Wiggum plugin](https://github.com/anthropics/claude-code/blob/main/plugins/ralph-wiggum/README.md)
  demonstrates the smallest useful loop driver: a Stop hook persists state and blocks session exit
  until completion or a configured boundary. Kimiflow already has this mechanism in
  `active-run.sh stop-gate`, so it needs no second hook, daemon, or heartbeat.
- Anthropic's [evaluator-optimizer guidance](https://www.anthropic.com/engineering/building-effective-agents#workflow-evaluator-optimizer)
  recommends iterative generation and critique when evaluation criteria are clear and improvement is
  measurable. It also requires agents to use environment results as ground truth. Kimiflow already has
  the right evaluator contract: acceptance criteria, mechanical gates, findings grammar, tests, and a
  clean-only completion condition.
- Anthropic's [agent-evaluation guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
  recommends turning observed failures into regression cases and using unambiguous success criteria
  instead of subjective progress. Recovery therefore adds or reruns the smallest decisive check before
  spending another reviewer round.
- [snarktank/ralph](https://github.com/snarktank/ralph/blob/main/prompt.md) keeps autonomous iterations
  bounded to one task, runs quality checks every iteration, and persists a compact progress/learning
  log. Kimiflow should likewise keep one coherent recovery hypothesis per epoch and durable evidence,
  without copying its separate outer process loop.
- [ralph-claude-code](https://github.com/frankbria/ralph-claude-code/blob/main/CLAUDE.md#circuit-breaker)
  detects no-progress and repeated-error loops, then supports automatic recovery. Kimiflow should
  borrow the stagnation signals, not its cooldown timer or terminal circuit state: the existing review
  reason codes can directly trigger a new strategy.
- Superpowers' [Subagent-Driven Development](https://github.com/obra/superpowers/blob/main/skills/subagent-driven-development/SKILL.md)
  forbids retrying the same worker unchanged and routes blockers through more context, a stronger
  model, a smaller task, or a corrected plan. Its
  [Systematic Debugging](https://github.com/obra/superpowers/blob/main/skills/systematic-debugging/SKILL.md)
  similarly requires a new hypothesis after a failed fix and an architecture check after repeated
  failures.
- GSD's [autonomous workflow](https://github.com/gsd-build/get-shit-done/blob/main/get-shit-done/workflows/autonomous.md)
  chains review to auto-fix, but asks the user after one unsuccessful gap-closure attempt. Kimiflow
  deliberately avoids that boundary for reversible technical work.
- Superpowers later removed repeated plan/spec subagent reviews after measuring time cost without
  quality gain; see its [release notes](https://github.com/obra/superpowers/blob/main/RELEASE-NOTES.md#v506-2026-03-24).
  Recovery should improve the strategy and evidence, not multiply reviewers.

## Minimal Design

Use the existing active-session Stop gate as the loop driver and the existing review resolver as the
evaluator. Add only:

1. an optional epoch boundary to `resolve-review-gate.sh`;
2. recovery instructions in the canonical Phase 4/7 and review-rubric documentation;
3. a compact run-local `RECOVERY.md` written only after actual stagnation;
4. resolver tests and behavioral evals for autonomous continuation.

There is no new controller executable, orchestration framework, background process, provider, or
general state machine. Normal clean runs create no recovery artifact and pay no recovery cost.

## Failure Routing

The resolver remains binary: only `OPEN / clean` advances. A `CLOSED` result selects an automatic
action:

| Failure class | Signal | Automatic action |
|---|---|---|
| Reviewer transport/artifact | `incomplete` or `malformed` | Repair or substitute the reviewer seat and complete the same round; do not start recovery. A file is immutable only after it is grammar-valid. |
| Local repair is progressing | `open-findings` | Apply the smallest evidenced repair, rerun affected checks, and use the next round in the same epoch. |
| Strategy is stagnant | `oscillation`, `reappeared`, or `cap-reached` | Record recovery, change strategy, and start a new epoch automatically. Never call `await-user`. |
| Execution evidence failed | failing test/build/reproduction before review | Return to diagnosis or implementation; do not spend a reviewer round yet. |
| External authority is missing | credentials/access, contradictory requirements, approved-scope change, paid/privacy-sensitive dependency, or irreversible public/data/migration choice | Exhaust safe configured/local fallbacks, then use the existing `await-user` gate with one precise question. |

A reviewer/model switch alone is transport recovery, not a technical strategy change. Likewise, more
tokens, another identical prompt, file churn, or rewritten prose are not progress.

## Strategy Epochs

Each plan or code gate has its own monotonically numbered, immutable findings ledger. Within that gate:

- `small`/`quick` gets two review rounds per strategy epoch;
- `large`/`audit`/release-critical gets three;
- the first epoch starts at round 1;
- later epochs continue global round numbering and never overwrite earlier findings;
- there is no run-global technical retry cap that routes to the user.

For an epoch starting at round `S` with budget `B`, the absolute cap is `C = S + B - 1`. Calls use:

```text
resolve-review-gate.sh <findings-dir> --round <N> --expect <lenses> --epoch-start <S> --cap <C>
```

The optional `--epoch-start` defaults to `1`. Without it, resolver behavior stays backward-compatible.
With it, oscillation and reappearance checks inspect only rounds `S..N`; a clean result at `C` may
open, while an open result at `C` or any attempt beyond `C` is `cap-reached`.

The existing cheap liveness rule remains intentionally simple: inside an epoch, open
`BLOCKER/HIGH` count must strictly decrease and a disappeared finding must not reappear. This does
not prove correctness and never opens the gate; it only permits one more same-strategy repair. Actual
completion still requires both fresh environmental verification and a clean review round.

A new epoch requires one coherent, falsifiable recovery hypothesis and a materially different
strategy. "Materially different" changes at least one evidenced root-cause hypothesis,
algorithm/control flow, integration or architecture boundary, dependency choice, or task
decomposition. Decomposition must preserve every acceptance criterion; it may not skip, defer, or
downgrade required work. The relevant plan/code basis fingerprint must also change. A changed hash is
necessary but not sufficient—the recovery entry must name the semantic delta.

## Recovery Procedure

On `oscillation`, `reappeared`, or `cap-reached`, the owner orchestrator continues through the
existing Stop gate:

1. Capture only the open blocker identities, failed strategy, decisive evidence, and trigger.
2. Re-read the cited code and confirmed acceptance/intent boundary. Classify whether the failure is
   wrong hypothesis, insufficient evidence, wrong technical approach, integration mismatch, or task
   size/decomposition.
3. Search the current run's prior recovery entries and bounded project memory/history for the blocker
   and failed approach (`--max 5`). Do not repeat a recorded failed strategy.
4. If the answer is absent or potentially stale, research focused current primary sources. Do not
   repeat an already answered query or reuse a source previously shown insufficient.
5. Form one new falsifiable hypothesis. When uncertainty is executable, run the smallest reproduction,
   spike, test, or static check that can refute it before changing the full solution.
6. Change the plan/implementation strategy. If the task is too coupled, split it into smaller,
   sequential AC-preserving steps, but review the completed approved scope as a whole.
7. Rerun normal pre-review checks, record the changed basis fingerprint, and start the next review
   round with the new epoch boundary.

Plan recovery re-enters Current State/Discovery/Plan only as far as the blocker requires, then reruns
the plan-blocker gate, AC mapping, and subtraction pass. Code recovery returns to
diagnosis/implementation/verification, preserves fix-mode Red/Green evidence, and reruns affected
tests before review.

The escalation ladder is progressive rather than cumulative:

1. top-orchestrator re-analysis using current evidence;
2. bounded run history and project-memory recall;
3. focused current-source research for uncovered/stale facts;
4. smallest useful spike or refined reproduction;
5. alternative architecture or AC-preserving task decomposition;
6. after two failed recovery epochs, one independent `top`/`cross_family_top` recovery solver.

The independent solver replaces more same-strategy retries; it does not add another standing review
seat. Every later epoch still selects one coherent hypothesis, consumes prior evidence, and produces
only a delta. Reviewers receive the confirmed intent, current strategy, decisive evidence, and named
files—not the full chat transcript.

## Durable State and Learning

`RECOVERY.md` is a chronological, compact run-local ledger. Each epoch records:

- gate (`plan|code`), trigger, source round, next start, and absolute cap;
- current blocker identities and failure classification;
- failed strategy and evidence that refuted it;
- new hypothesis, semantic strategy delta, and decisive evidence references;
- previous and current plan/code basis fingerprints;
- outcome (`active|clean|superseded`).

`STATE.md` holds only the current gate's epoch number, start, cap, strategy fingerprint, and
`Recovery: active|clean`. No transcript, research dump, or full findings copy belongs there. This is
enough for compaction or Stop-hook continuation to resume from disk.

Failed strategies remain run-local evidence, not project truth. After a clean verified run, the
existing Memory Router review may promote the successful solution and an evidenced reusable
trap/replacement pair. Existing confidence, freshness, sensitivity, evidence, and user-memory rules
remain unchanged.

## User Interaction Contract

During recovery Kimiflow must not ask the user to:

- authorize another review/recovery epoch;
- choose between reversible technical implementations inside confirmed scope;
- approve a model, context, research, decomposition, or architecture escalation inside confirmed
  intent and risk;
- acknowledge a cap, oscillation, reappeared finding, or expensive recovery.

Kimiflow may pause only for genuinely missing authority or inaccessible external state as classified
above. The question names exactly what is unavailable and why safe local/configured alternatives are
insufficient. A technical blocker never becomes `await-user` merely because it is difficult or has
consumed several epochs. The user may always explicitly park, abort, or change scope.

Existing one-time pre-build and commit approvals remain governed by their current policies. A
recovery epoch does not create another approval checkpoint by itself.

## Token Efficiency

- Reuse `active-run.sh stop-gate`; no new Stop hook or repeated static prompt.
- Do not invoke memory, web research, a solver, or `RECOVERY.md` on normal clean/progressing runs.
- Query memory once per distinct blocker/failed-strategy pair, capped at five hits; fresh relevant
  memory replaces web research.
- Use one coherent recovery hypothesis and the smallest refutation check per epoch.
- Run no extra reviewer merely because recovery began; retain the existing per-round reviewer axes.
- Add one independent recovery solver only after two failed recovery epochs.
- Send reviewers a compact delta packet, while files carry durable detail.

## Compatibility and Safety

- Legacy resolver calls without `--epoch-start` retain current semantics and tests.
- Review stays fail-closed: recovery cannot emit `OPEN`; only a grammar-valid clean findings round can.
- Valid findings files remain immutable and gate-local round numbers never reset.
- `MEDIUM/LOW` remain advisory and never trigger recovery.
- Recovery cannot expand approved product scope, add paid infrastructure by default, or make an
  irreversible decision. Those require existing user authority gates.
- Claude Code and Codex use the same canonical artifacts and resolver semantics. Host wrappers only
  map tools and models.
- Existing active-run code does not change; its Stop gate already continues an owned active run and
  permits exit only for terminal or `awaiting_user` state.

## Verification

1. A `small` plan/code gate with an open HIGH at its second epoch round emits `cap-reached`, writes a
   recovery entry, and continues without `await-user`.
2. A non-decreasing blocker count and a reappeared finding each trigger recovery before another
   same-strategy review.
3. `incomplete`/`malformed` reviewer output retries or substitutes the same seat and same round; it
   does not consume a strategy epoch.
4. A clean round at an epoch cap opens; any round beyond that absolute cap remains closed.
5. Anti-oscillation considers only rounds at or after `--epoch-start`; global round files remain
   intact and monotonically numbered.
6. Invalid epoch bounds fail closed. Calls without `--epoch-start` preserve every legacy fixture.
7. An unchanged fingerprint or prose-only strategy delta cannot justify a new epoch in behavioral
   evals.
8. Recovery recall is bounded and available to `small/quick`; web research occurs only for
   uncovered/stale facts and uses current primary sources.
9. Two failed recovery epochs allow exactly one independent recovery solver, not extra review seats.
10. A purely technical blocker never sets `awaiting_user`; synthetic missing credentials or an
    irreversible scope/risk decision still may.
11. Existing pre-build and commit gates behave unchanged and are not repeated per recovery epoch.
12. Phase 4/7 behavioral evals, resolver tests, active-run tests, host smokes, install parity, and the
    full local suite pass.

## Non-goals

- Automatically waiving, lowering, or deferring valid findings.
- Guaranteeing progress without credentials, external systems, or product authority that the user
  has not supplied.
- Running broad memory/web searches on every review.
- Adding a generic autonomous-agent framework, timer-based circuit breaker, or second orchestrator.
- Changing existing one-time build/commit approval policy.
