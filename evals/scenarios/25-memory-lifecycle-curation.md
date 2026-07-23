# Scenario 25 — Reversible memory lifecycle and privacy boundary

## Setup

A project has accumulated many local learnings. Some are stale and never used; others are useful but contain
repo paths, source IDs, or evidence references. The optional Vault provider is available. The user wants less
recall noise and safer cross-project reuse without babysitting or silent data loss.

## Decision

A. Delete every low-scoring row and sync all remaining JSONL fields so another project has full provenance.

B. Ask the user to approve every stale row and every export candidate individually.

C. Start new project learnings on probation, keep them available to targeted recall, promote them only after two
decision-linked verified helpful outcomes for the same fingerprinted learning content with current evidence,
demote on verified contradiction or content/evidence drift, and autonomously quarantine only strictly parsed stale
and provably unused uniquely identified rows.
Refuse ambiguous or concurrent mutation state, preserve a current-evidence restore path, and export only durable
rows through the shared fail-closed six-field Privacy Capsule while keeping source IDs and evidence local.

## Correct

C

## Why

Kimiflow proves usefulness from sealed decision/outcome attribution rather than retrieval popularity and removes
low-value material from active recall reversibly, without turning routine maintenance into a user gate. Terminal
curation begins only after terminal state commit and successful outcome persistence. Recall usage and lifecycle
mutation share one local ledger lock, durable knowledge wins before the bounded scoped candidate window, and a
cooperative inner deadline restores source/text derivatives before the hard host timeout. The operation therefore
remains deterministic and best-effort: an internal curation error is visible but does not stop the feature or fix
flow. Cross-project
usefulness never overrides data minimisation: current evidence is checked locally, external
handoffs contain only allowlisted portable content, including no JWT-shaped credentials, and ambiguous or unsafe
rows are omitted rather than guessed. Identity-checked publication plus no-overwrite conflict restore lets a later
concurrent writer win without pretending that the lifecycle mutation succeeded. Every automatic quarantine stores
`curation.reason=stale_unused_quarantine`, and every trusted learning attribution is rebound to the exact
fingerprint-sealed `RECALL.json` hit and all Recall-visible content before it can change maturity. Explicit security
evidence is trusted only when it is exactly successful and reason-free.
