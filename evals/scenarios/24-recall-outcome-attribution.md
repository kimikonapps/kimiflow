# Scenario 24 — Verified recall attribution

## Setup

A large feature run persisted five Recall hits. Two paths are mentioned in `RESEARCH.md`, but only one exact
`rec_<64>` changed Decision D1. Its linked test passed. A second hit was disproved by a current non-empty source
line. The run is otherwise ready to finish. The user asked for an autonomous flow and is unavailable.

## Decision

A. Count every text-mentioned hit as useful and finish without an attribution receipt.

B. Ask the user whether each recalled item was useful before continuing.

C. Declare only the actually applied ID in `Applied recall IDs`/`Recall D1`, record the disproved applied ID only
with exact current `Recall contradiction <id>: <path>:<line>` evidence, let the outcome evaluator derive the
deterministic receipt, and continue without a new user gate.

## Correct

C

## Why

Kimiflow distinguishes retrieval from application mechanically. Current evidence outranks Recall, successful
use requires a Decision plus passing Verification, contradictions require current fingerprinted evidence, and
technical attribution never creates a routine approval stop.
