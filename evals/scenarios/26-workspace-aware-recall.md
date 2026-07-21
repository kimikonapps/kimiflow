# Scenario 26 — Workspace-aware Recall without lost global authority

## Setup

A large monorepo contains `packages/api` and `packages/web`, each with its own package manifest. A feature run
affects only API files. Search relevance strongly favors many old Web facts, while one weaker API fact and one
root-level architecture rule are relevant. During selection, a new nearer manifest may also appear. The user wants
useful context without a repository-wide token flood, another worktree, or another approval.

## Decision

A. Take the highest-scoring project-wide hits; package locality would hide useful information and needs a full
dependency graph first.

B. Infer the API package from bounded affected-path ancestor checks, filter exclusively foreign package evidence
before bounded source windows, rank local before textual coverage, keep root/unbound authority global, expose only
bounded path-free scope metadata, and retry exactly once project-wide if resolution is unsafe or changes.

C. Create a sparse checkout or separate worktree for the API package, then ask the user whether Web evidence may
be omitted.

## Correct

B

## Why

Kimiflow narrows only when nested boundaries are mechanically proven. Locality prevents a noisy sibling package
from starving the candidate window, but it cannot override shared project rules. Fail-safe fallback and a final
fresh resolution prevent a guessed or changing boundary from hiding evidence. Existing salted Git metadata may
distinguish linked worktrees without serializing their paths; no checkout, index, network call, or user gate is
introduced.
