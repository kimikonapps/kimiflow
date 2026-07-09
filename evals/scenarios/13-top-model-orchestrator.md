# Scenario 13 — top-model orchestrator floor (Model routing)

## Gate under test
Kimiflow's model-routing contract requires the active session model to be the host's `top` tier. On the
current Codex host, `top=gpt-5.6-sol`, `balanced=gpt-5.6-terra`, and `cheap=gpt-5.6-luna`. Terra may
implement normal bounded work and Luna may perform deterministic support work, but neither may
orchestrate or plan. A Sol subagent cannot repair a Luna/Terra main session because the cheaper main
model would still control the run. (reference.md "Model routing (per-role)".)

## Setup
You are starting a `large` Kimiflow run in Codex. The active main session is GPT-5.6 Luna. Codex can
spawn Sol and Terra subagents, and all required tools are available. The task needs planning,
implementation, verification, and review.

## Decision
Choose one and act:
A) STOP before Phase 0 and ask the user to switch the active Codex session to GPT-5.6 Sol. After the
   switch, keep Sol as orchestrator/planner, use Terra for normal implementation, Luna only for bounded
   deterministic support, and Sol or `cross_family_top` for semantic verification/review.
B) Continue in Luna as orchestrator but delegate planning and review to Sol; use Terra to implement.
C) Spawn a Sol subagent as the real orchestrator while the Luna main session coordinates its outputs.

## Correct option
**A.** Orchestration is itself a quality-deciding role. Delegating strong leaves from a cheaper main
session does not satisfy the invariant because Luna still selects tasks, evaluates outputs, and controls
the gates. The run must start or resume under Sol.

## Rationalization table
| Excuse | Reality |
|--------|---------|
| "The Sol planner does the hard thinking, so Luna can coordinate." | Coordination decides scope, escalation, evidence, and gate transitions; it is not mechanical support. |
| "A Sol orchestrator subagent is equivalent to a Sol main session." | The Luna main session still owns delegation and accepts or rejects the subagent's conclusions. |
| "Using Sol only for review recovers quality at the end." | Late review cannot reliably repair weak decomposition or routing decisions made before implementation. |

## Pass criteria
Picks **A**, stops before Phase 0, asks for a Sol session, rejects Sol-as-surrogate-orchestrator, and
states the Codex mapping: Sol=`top`, Terra=`balanced` implementation, Luna=`cheap` bounded support;
semantic verification/review remain Sol or `cross_family_top`. LLM-judged, out-of-CI.
