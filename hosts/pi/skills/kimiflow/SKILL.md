---
name: kimiflow
description: Run the Kimiflow product workflow in standalone Pi or through an unmodified FirstMate crew.
---

# Kimiflow for Pi

Kimiflow supplies the product workflow. It does not own Pi sessions, Herdr tabs, workers, worktrees, Calm, status delivery, recovery, or cleanup.

## Choose the current context

1. **FirstMate primary:** the current repository is a stock FirstMate home and its instructions identify this session as the first mate that talks to the captain.
2. **FirstMate crewmate:** the normal FirstMate brief identifies this session as a crewmate or scout and provides its status-file contract.
3. **Standalone Pi:** neither FirstMate role is present.

Do not infer FirstMate from Herdr alone. Never create a Kimiflow-owned Captain, worker, transport envelope, endpoint, background runner, or recovery state.

## FirstMate primary

The user speaks only with this primary session. Before spawning a ship:

- Preserve the user's language from the opening request.
- Inspect the project's current codebase and commit state. Check current authoritative primary sources when technology, APIs, models, or external behavior may have changed.
- Discuss the feature even when a numbered plan already exists. In plain language show: the problem, intended user flow, visible success, boundaries, risks, and useful adjacent functions. Challenge weak assumptions and invite corrections.
- Show one compact, non-technical flow sketch. Obtain one explicit final confirmation of the resulting product contract before implementation.
- Resolve the exact FirstMate project name. Through stock FirstMate project management, persist `local-only` by default or `direct-PR` only when the user explicitly wants it. Verify the selection with `bin/fm-project-mode.sh <project>` and refuse to spawn if it is missing, resolves to `no-mistakes`, or cannot be changed with current authority.
- Create an ordinary stock FirstMate Ship brief. Put the confirmed Kimiflow contract, its minimum-complete plan, acceptance criteria, current codebase/research evidence, and the instruction to use this skill in that brief. The brief is authoritative product confirmation for the crewmate; do not make it ask the user to confirm the same contract again.
- After spawning a Pi ship, follow stock FirstMate's harness-adapter startup check. If Pi shows its documented project-trust dialog, resolve it through FirstMate's normal worker-control path and verify that brief processing began before treating the dispatch as active.
- Use a Scout only for genuinely independent read-only investigation. Use visible FirstMate workers for genuine delegation; fold local reasoning into the current role without inventing an agent.

FirstMate alone owns spawn, worktrees, Pi/Herdr lifecycle, Calm, status, steering, resume, recovery, and cleanup. Receive every crewmate question or result through FirstMate's normal status path, discuss it with the user here, and send any answer through FirstMate. A failed spawn stays failed; do not fall back to a hidden process or claim recovery.

`no-mistakes` is not a valid delivery mode for a Kimiflow ship because it would add a second review/fix workflow. Kimiflow performs its own bounded implementation, verification, and review inside the crewmate.

## FirstMate crewmate or scout

Treat the normal FirstMate brief's confirmed product contract and plan as completed Kimiflow intake and planning. Validate that the brief actually contains a concrete problem, user flow, observable success, boundaries, plan, and acceptance criteria. If it leaves a material product decision unresolved, report `needs-decision:` through the exact status-file protocol and stop; never question the user in the worker tab.

Execute the confirmed plan, verification, and bounded review in the isolated FirstMate worktree. Do not create a second Kimiflow Active Run, replay Product Intake, or synthesize intake/worker receipts; FirstMate already owns this task's durable lifecycle and the brief is its authority. Use the package root reported for this loaded skill when reading bundled guidance; never guess an install path. When a real decision is required, write one concise `needs-decision:` status so the primary can steer this same worker. Report progress and completion only through the normal FirstMate brief/status contract. Do not spawn a hidden Pi subagent; any genuinely independent delegation must be requested from the FirstMate primary as a visible Ship or Scout.

## Standalone Pi

Run Kimiflow in the current Pi conversation and current repository. The same user-facing discussion and final confirmation are required before a substantial feature is fixed. The user may answer here because this is the only session.

Use the package root reported for this loaded skill to read the canonical `SKILL.md` and phase guidance. Standalone Pi follows the workflow in the current session. It uses mechanical Active Run/gates only when Pi exposes a reliable host/session identity; otherwise it follows the same semantics and states that mechanical gates are unavailable. Do not invent an owner or pretend a gate ran. Standalone Pi never requires FirstMate and never attempts to spawn or control Herdr.

Direct Codex and Claude Kimiflow flows remain independent of FirstMate.
