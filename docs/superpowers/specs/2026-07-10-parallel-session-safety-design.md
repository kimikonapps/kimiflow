# Parallel Session Safety

**Date:** 2026-07-10
**Status:** Approved for implementation

## Goal

Allow multiple Codex and Claude sessions to use the same project without letting a project-wide Kimiflow `Stop` hook erase or replace answers from unrelated sessions. Reading, questions, analysis, and planning must always remain available. Parallel writes are allowed only when the intended paths are known and do not overlap another active run.

## Session Ownership

Each new active Kimiflow run records a namespaced owner identity consisting of the host and session id. Codex obtains the identity from `CODEX_THREAD_ID`. Claude receives its hook `session_id` through a `SessionStart` hook that persists a Kimiflow session variable for subsequent shell commands.

Hook payload identity is authoritative when deciding whether `UserPromptSubmit` or `Stop` belongs to the active run. Host namespacing prevents an accidental id collision between Codex and Claude.

Existing active runs without owner metadata remain readable and non-blocking. The next mutating Kimiflow command executed by their owner may backfill the owner identity. Until then, parallel write conflicts are treated as unknown.

## Hook Behavior

### Owner Session

- `UserPromptSubmit` keeps follow-up requests inside the active Kimiflow run.
- `Stop` retains the existing completion gate and may continue the owner session until the run is finished, parked, failed, or aborted.
- Existing `awaiting_user` and stop-loop protections remain unchanged.

### Other Sessions

- `UserPromptSubmit` never adopts the prompt into the active Kimiflow run.
- It may inject compact advisory context identifying the other active run and its declared affected paths.
- Reading, questions, analysis, and plan creation are always allowed.
- `Stop` exits without output and never returns `decision: block`.
- The generated assistant response therefore remains visible and cannot be replaced by an active-session continuation prompt.

### Unknown Ownership

An active run with no reliable owner must never block `Stop`. This is deliberately fail-open for conversation continuity. It remains fail-closed for parallel writes until ownership and affected paths are known.

## Write Conflict Check

A mechanical conflict-check command accepts the paths a second session intends to modify and compares them with the active run's declared affected paths.

It returns one of three stable decisions:

- `allow_disjoint`: ownership and both path sets are known, with no path overlap.
- `block_overlap`: an exact path or ancestor/descendant path overlaps the active run.
- `block_unknown`: ownership, intended paths, or active affected paths are unavailable.

The advisory context instructs a non-owner agent to run this check once its intended files are known and before editing. `allow_disjoint` permits parallel work. `block_overlap` and `block_unknown` prohibit edits in the shared checkout; the agent must wait, narrow the scope, or use an isolated Git worktree.

Git status remains part of the agent's assessment. A disjoint session must not stage, commit, revert, or clean files owned by another session. For substantial parallel changes, a separate worktree is preferred even when paths are disjoint because test output, generated files, and broad Git commands can still interact in one checkout.

## Cross-Host Contract

The project-local active-run record is shared by Codex and Claude. Both hosts use the same owner comparison and path-conflict semantics:

- Codex hook events use their documented `session_id`, while shell commands inherit `CODEX_THREAD_ID`.
- Claude hook events use their documented `session_id`; `SessionStart` persists it through `CLAUDE_ENV_FILE` for later Bash commands.
- Host-specific setup changes only identity transport, not conflict policy.

## Token Efficiency

- Identity and path comparison are deterministic local operations and use no model calls.
- Owner sessions retain the existing compact reminder.
- Non-owner advisory context appears only while another active run exists.
- Conflict checking runs once per intended path set instead of on every read-only prompt.
- No general session heartbeat, lease daemon, or background model is introduced.

## Testing

Focused tests must verify:

1. The owner session still receives Kimiflow prompt context and Stop enforcement.
2. A non-owner Codex or Claude session receives no Stop block.
3. Non-owner prompt context is advisory and does not adopt the prompt into the run.
4. Disjoint paths return `allow_disjoint`.
5. Exact and ancestor/descendant overlaps return `block_overlap`.
6. Missing owner or affected paths return `block_unknown` without blocking conversation output.
7. Legacy active-run files remain compatible and can acquire owner metadata through a later mutating owner command.
8. Install and structural smoke tests cover the Claude session bootstrap and existing Codex wrappers.

## Scope

Implementation is limited to active-run identity, hook routing, deterministic path conflict checking, the Claude session bootstrap, canonical/host documentation, and focused regression tests. It does not attempt to infer arbitrary user intent from prompt text, monitor every process in the repository, or introduce a persistent lock service.

## Considered Alternatives

- Scope only the `Stop` hook to its owner: rejected as incomplete because it would preserve answers but give parallel writers no conflict guidance.
- Maintain heartbeats and expiring path leases for every Codex and Claude session: rejected for now because stale-session cleanup and always-on hook writes add complexity beyond the reported active-Kimiflow conflict.
- Require a separate worktree for every second session: rejected as unnecessarily restrictive for read-only work and small, clearly disjoint edits.
