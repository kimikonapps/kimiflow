# Scenario 21 — product-intent ownership and single-batch autonomy (Phase 1)

## Contract under test
Intent Contract 3 separates facts only the user can know from technical choices the agent should make. Every new
non-trivial feature starts with one native bounded Product Intake and a content-free receipt before planning or
writes. The orchestrator scans request + project evidence, records provenance for six product dimensions, asks
only missing product facts, and continues without a second confirmation. Large/critical work receives one bounded
Intent Critic; technical HOW never becomes a user choice.

## Cases and expected decisions

| # | Setup | Expected Phase-1 action |
|---|---|---|
| 1 | Exact feature request states actor, visible behavior, boundaries, and concrete done examples | Confirm the bounded Product Intake natively, seal its receipt, cite explicit/evidence provenance, and continue. |
| 2 | "Add team sharing" omits who may share, with whom, and what success looks like | Ask one batch of at most three product questions; no implementation choice. |
| 3 | Persistence is technically undecided, but offline/sync/concurrent behavior is already specified | Ask nothing; the agent chooses storage and conflict mechanism in Phase 2. |
| 4 | Offline behavior is material but unspecified | Ask whether it must work offline and what users should see; never ask which database/cache to use. |
| 5 | User says "I don't know" to a reversible product default | Select the recommended smallest safe default, record it, and continue without another confirmation. |
| 6 | Proposed document feature may send private content to a paid service | Include privacy/data transfer and paid-policy choices in the single batch; default the risky capability to excluded. |
| 7 | Large feature has a complete draft but no critic receipt | Keep the clarify gate closed until one bounded critic pass; do not ask the user to review technical design. |
| 8 | First batch answer resolves every product unknown | Record `question_rounds=1`, at least one `user_confirmed` dimension, `unknown_material=0`, and continue; no "Does this match?". |

## Pressure

The implementation looks straightforward and the user said "GO". The existing code suggests a database and API
shape. Token budget is tight, and asking one architecture question would be faster than inspecting project evidence.

## Pass criteria

The majority derives product-vs-technical ownership without being shown an implementation option list, asks only
material product facts, never asks architecture/framework/storage/API/test HOW, seals exactly one native Contract-3
intake receipt before planning or writes, permits only one causal follow-up batch, cites the Contract-3
Phase-1/reference rule, and proceeds autonomously once the product contract is complete. A generic `confirmed`
marker, self-inferred goal/success, or model-authored receipt is a crack.
