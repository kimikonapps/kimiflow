---
name: kimiflow
description: Run Kimiflow in the current Pi project session, with optional visible FirstMate workers in Herdr.
---

# Kimiflow for Pi

The Pi conversation in which the user invokes Kimiflow is always **Kimiflow Main**. It owns the product discussion and is the only session that talks with the user. Kimiflow may use stock FirstMate internally for visible workers, but it never creates a second captain, a Kimiflow worker registry, a Herdr bridge, or its own recovery truth.

## Start in the current project

When the user says, for example, “Beginne mit Kimiflow Run 7.2”:

1. Preserve the user's language from the first request.
2. Inspect the current Git project, current codebase and relevant recent commits. Research current authoritative sources when technology, APIs, models or external behavior may have changed.
3. Discuss the feature here even when a numbered plan already exists. In simple language show the problem, intended user flow, visible success, boundaries, risks and useful adjacent functions. Challenge weak assumptions and invite corrections.
4. Show one compact non-technical flow sketch and obtain one explicit final confirmation of the complete product contract.
5. Decide whether genuinely independent work exists. If not, continue in Main. If yes and this Pi session runs inside Herdr, call `kimiflow_crew` with `action=activate` and delegate only after the final contract is confirmed.

The extension is dormant until `activate`. If FirstMate is absent or incompatible, say so plainly and continue ordinary Kimiflow in this Pi session. Never imply that a worker exists when activation or endpoint verification failed.

## Main and visible crew

Main owns:

- all user questions and confirmations;
- current-code inspection, research, contract, plan and delegation decisions;
- worker status, steering, review decisions, integration and final verification.

Use a visible FirstMate **Ship** only for an independent implementation packet and a visible **Scout** only for independent read-only investigation or verification. Do not create workers for internal reasoning, sequential steps or overlapping writes. Fold those into Main.

For every new worker:

1. Create a stable lowercase task id.
2. Pass the already confirmed, self-contained work packet to `kimiflow_crew` with `action=spawn`, `task`, `kind` and `brief`.
3. Treat the worker as active only when the tool returns `worker_reachable`. `spawn_failed` and `spawn_unverified` are failures, never recovery.
4. After `worker_reachable`, do not poll or narrate progress. End the turn quietly and let FirstMate's watcher return the next actionable wake to this same Main session.
5. On a wake, use `action=drain` once, then `action=status` for current truth. Use `action=send` only for a decision or steering message.
6. Use `action=teardown` only after safe completion, with `confirmation` exactly equal to the task id. The adapter never forces cleanup.

FirstMate alone owns each delegated worker's brief, isolated worktree, Herdr/Pi endpoint, status, wake, recovery and cleanup. Kimiflow stores no parallel worker truth.

If `action=activate` returns `startupWakes`, FirstMate already drained those durable records while acquiring authority. Handle their named tasks with `action=status` before dispatching new work; do not discard them or call `drain` for the already consumed rows.

## Worker boundary

A Pi worker receives a normal FirstMate brief whose task section already contains the confirmed Kimiflow contract. It must:

- treat product intake and confirmation as complete;
- work autonomously in its isolated FirstMate worktree;
- never open a second user conversation or ask the user in the worker tab;
- never spawn another crew;
- return `needs-decision`, blockers and results only through the brief's FirstMate status protocol;
- use the installed Kimiflow skill for implementation, verification and bounded review, without creating a second Active Run for the same task.

When a crew wake arrives in Main, call `kimiflow_crew` with `action=drain`, inspect the named worker with `action=status`, discuss any real decision with the user here, then reply to that worker with `action=send`.

## Standalone behavior

Kimiflow remains fully usable without FirstMate or Herdr. Run the same product workflow in the current Pi conversation and repository. Do not promise visible workers, invent an owner identity, or pretend mechanical gates ran when the host does not expose them.

Direct Codex and Claude Kimiflow flows remain independent of FirstMate.
