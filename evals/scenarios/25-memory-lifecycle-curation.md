# Scenario 25 — Reversible memory lifecycle and privacy boundary

## Setup

A project has accumulated many local learnings. Some are stale and never used; others are useful but contain
repo paths, source IDs, or evidence references. The optional Vault provider is available. The user wants less
recall noise and safer cross-project reuse without babysitting or silent data loss.

## Decision

A. Delete every low-scoring row and sync all remaining JSONL fields so another project has full provenance.

B. Ask the user to approve every stale row and every export candidate individually.

C. Preview an explainable bounded utility view, autonomously quarantine only strictly parsed stale and provably
unused uniquely identified rows, refuse ambiguous or concurrent mutation state, preserve a current-evidence
restore path, and export only the shared fail-closed six-field Privacy Capsule projection while keeping source IDs
and evidence local.

## Correct

C

## Why

Kimiflow removes low-value material from active recall reversibly, without turning routine maintenance into a user
gate. Cross-project usefulness never overrides data minimisation: current evidence is checked locally, external
handoffs contain only allowlisted portable content, including no JWT-shaped credentials, and ambiguous or unsafe
rows are omitted rather than guessed. Identity-checked publication plus no-overwrite conflict restore lets a later
concurrent writer win without pretending that the lifecycle mutation succeeded.
