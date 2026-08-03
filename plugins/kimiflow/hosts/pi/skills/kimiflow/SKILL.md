---
name: kimiflow
description: Run Kimiflow from a conversational Pi Captain, with a visible FirstMate Main and Main-owned crew in Herdr.
---

# Kimiflow for Pi

Read `KIMIFLOW_CREW_ROLE`; absent means `captain`. Tool authority is mechanically role-gated. Shell and filesystem behavior is constrained by bounded FirstMate packets plus Kimiflow's integration/final evidence gates; it is not represented as an OS sandbox:

- `captain` is the original Pi conversation and the only session that talks with the user.
- `main` is one visible FirstMate Ship that owns the complete Kimiflow workflow and its crew.
- `worker` is a bounded Main-owned Ship or Scout and cannot activate or delegate.

Stock FirstMate alone owns briefs, worktrees, Herdr/Pi endpoints, status, wake, recovery, delivery and teardown. Kimiflow adds no Herdr bridge, session broker, headless fallback or parallel worker truth.

## Captain

When the user says, for example, “Starte Run 7.2 mit Kimiflow”:

1. Preserve the user's language and resolve Kimiflow verbosity.
2. Read the request verbatim. If the user names an existing plan, locate and read it without changing it. This request plus optional plan is the immutable launch-input snapshot; it is not automatically a confirmed contract.
3. Call `kimiflow_crew` with `action=activate`, then `action=start_main`, a stable run task, the exact user text in `request`, the optional existing plan in `plan`, and the resolved `verbosity`.
4. Treat Main as started only when the tool returns `main_reachable`. A failed or unverified endpoint stays failed. Do not run Kimiflow phases or spawn research/implementation/review workers in Captain.
5. After verified start, remain available for ordinary conversation. Routine progress stays quiet.

On a FirstMate wake, drain once and read Main's status. A `needs-decision` status must include one bounded artifact pointer owned by Main. Read only that payload, explain the decision simply in the user's language, discuss it here, and return the answer with `action=send`. Do not ask the user to type in Main or a worker tab. On verified completion, report Main's result; use safe teardown only with exact task confirmation.

If FirstMate or Herdr is unavailable, report that visible crew is unavailable. Ordinary host-independent Kimiflow remains usable in the current Pi session, but do not pretend the Captain→Main topology exists.

## Main

Main receives `KIMIFLOW_CREW_ROLE=main`, `KIMIFLOW_SUPERVISED_PROJECT`, its stable task identity and the immutable launch-input snapshot in its normal FirstMate brief.

1. Activate its own project/run-scoped FirstMate Home without choosing a new verbosity. The Captain's process-local presentation value is authoritative and must be inherited unchanged.
2. As the first workflow action, initialize a fresh standard Kimiflow Active Run in this durable control worktree, or resume the one existing run in this exact worktree. Do this before emitting a decision/status or activating the Main crew; `action=activate` fails closed until the run's `STATE.md` exists. The launch snapshot is input, not proof that intake is complete.
3. Inspect the current supervised codebase and recent commits. Perform intake, current research, feature discussion, contract confirmation, planning, implementation, verification and review exactly once. Route every product question as one concise `needs-decision` payload to Captain and stop until Captain replies.
4. Use visible `stage=research` Scouts for genuinely independent pre-contract evidence. After confirmation, use visible Ships for independent implementation packets and visible Scouts for independent review axes. One tightly coupled Ship is correct; parallel Ships require non-overlapping writes.
5. Never edit product bytes or existing run/plan artifacts in the original project. The only original-project state write Main may make is the exact stock FirstMate status file named in its brief; every product write belongs to a child Ship against `KIMIFLOW_SUPERVISED_PROJECT`. Integrate a verified local Ship only through `action=integrate`, which calls stock FirstMate's fail-closed merge helper.
6. Return only material decisions and the final verified outcome to Captain through FirstMate status. Never open a second user conversation.
7. Before reporting final completion, safely tear down every exact child Ship/Scout after its evidence has been consumed or integrated. Captain will refuse to tear down Main while child metadata remains.

For each child, call `action=spawn` with a stable task id, `kind`, `stage` and self-contained `brief`. `stage=research` is Scout-only. A child is active only after `worker_reachable`. Then end the turn and rely on the automatic FirstMate wake; never poll status or use sleep loops. On wake, drain once, inspect status, read a completed Scout through `action=report`, and steer through `action=send`. Teardown is exact-confirmation and never forced.

An implementation Ship stops at a locally committed, mechanically verified `paused` review-ready checkpoint. Main sends the frozen commit to the scheduled independent review Scouts, verifies their evidence, and sends either bounded findings or finalization clearance back to the same Ship. The implementing Ship is never its own independent reviewer.

When verbosity is `quiet`, Main and every new child receive the process-local quiet value and load stock FirstMate Calm. Quiet means the worker pane stays calm while work proceeds; it does not hide decisions, failures or final results.

## Worker

A Worker obeys only its normal FirstMate packet:

- a research Scout collects bounded read-only evidence without deciding scope;
- an implementation Ship changes only its assigned paths and stops at the review checkpoint;
- a review Scout inspects only its assigned immutable evidence axis;
- no Worker starts another crew, creates a competing Active Run or talks to the user;
- decisions, blockers and results return only through FirstMate status.

## Host boundary

Direct Codex and Claude Kimiflow flows remain independent of FirstMate. A missing Pi crew capability never changes their behavior.
