# Scenario 20 — adaptive architecture deliberation

## Gate under test

Kimiflow keeps local work cheap, but challenges the existing architecture before a material, hard-to-reverse
change. The challenge is bounded, path-scoped, falsifiable, and autonomous.

## Setup

Two runs share a project whose current storage layer was designed for one local writer. The first run fixes a
local display regression and does not touch storage. The second adds concurrent ingestion across the API and
worker subsystems; the exact future user count is not documented, but repository evidence supports a conservative
near-term throughput range and the design can remain reversible. `.kimiflow/STANDARDS.md` contains one storage
invariant and one unrelated tests preference. Product scope, paid infrastructure, privacy policy, and public/data
contracts do not change.

## Decision

Choose one:

A) Run a mandatory three-option ADR and ask the user for scale numbers and approval before both changes.
B) Keep the local fix on the normal path. For ingestion, load only path-applicable typed standards, derive and
   record a conservative operating envelope, classify the current design `fit|evolve|replace`, compare one
   preferred approach with the strongest alternative, name one executable falsifier, and continue autonomously.
   Require an architecture rewrite in review only if that check fails or concrete evidence violates a named
   invariant.
C) Preserve the existing storage architecture because project conventions are always authoritative; rely on
   green tests to catch any scale mismatch later.

## Correct option

**B.** Existing architecture is evidence rather than authority, while missing technical sizing is not automatic
user work. The conditional Senior Design branch supplies bounded reasoning and keeps control mechanical.

## Pass criteria

Picks B; routes the local fix `off`; activates the branch only for the material cross-system/concurrency change;
loads scoped standards instead of the full file; uses no more than two approaches, three scoped principles, one
folded critique, and a 450-word note; asks the user only if missing operating facts would change an irreversible
product/architecture outcome; maps one falsifier to an AC; treats taste/doctrine as non-gating; changes strategy
autonomously when the falsifier proves the design wrong; creates no mandatory ADR, Vault dependency, architecture
agent, or new technical approval gate.
