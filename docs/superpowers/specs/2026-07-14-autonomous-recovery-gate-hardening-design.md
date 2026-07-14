# Autonomous Recovery Gate Hardening

**Date:** 2026-07-14

**Status:** Implemented

## Goal

Close the three integration gaps found after autonomous review recovery shipped:

1. technical recovery must not invalidate an existing human approval while product intent, accepted
   behavior, scope, and risk remain unchanged;
2. a technical review failure must not escape the active loop through an untyped `await-user` call;
3. a caller must not reset anti-oscillation by declaring a new epoch without durable recovery state.

The published risky-bugfix example must describe the same behavior. The change stays inside the
existing resolver, approval gate, and active-session driver; it adds no controller, daemon, provider,
or background process.

## Approval Boundary

For schema-3 fix runs, the human approves the product-authority boundary, not one technical attempt.
The approval basis contains:

- `PROBLEM.md`;
- `ACCEPTANCE.md`;
- flow schema and mode;
- declared scope;
- build-risk classification.

It deliberately excludes `DIAGNOSIS.md`, `PLAN.md`, affected-file lists, implementation details, and
strategy fingerprints. Those may change autonomously during recovery while the approved behavior,
scope, and risk stay fixed.

New approvals are stored durably in `STATE.md` as a confirmed marker plus a SHA-256 authority basis.
Existing approval comments in `DIAGNOSIS.md` remain readable for legacy runs. Recording a new approval
writes both records: STATE receives the new authority hash, while the compatibility comment receives
the unchanged legacy technical-basis hash. The gate reads STATE first and falls back to the comment
only when no STATE approval record exists. Changing problem, acceptance, scope, mode, or build risk
makes the new approval stale; changing diagnosis, plan, affected files, or recovery strategy does not.

## Typed User Pauses

`active-run.sh await-user` gains `--kind <kind>`. Schema-3 runs require a non-empty recognized kind;
legacy runs without a flow schema retain the old untyped behavior. Recognized deliberate workflow
gates are `preview` and `commit`. Recognized missing-authority categories are:

- `missing-input`;
- `authority`;
- `external-access`;
- `paid-privacy`;
- `scope-risk`;
- `irreversible`.

When `STATE.md` says `Recovery: active`, `preview` and `commit` are rejected and only the six
missing-authority categories may set `awaiting_user`. Missing or unknown kinds fail closed. A rejected
call leaves the active session running, so the existing Stop gate continues the loop. Once recovery is
clean, the existing one-time Preview or Commit gate can use its deliberate kind normally.

The kind is a mechanical guardrail, not proof that the stated external condition is true. Kimiflow's
behavioral contract still requires evidence that safe local/configured alternatives were exhausted.

## Recovery Receipt

The review resolver remains read-only. Calls that omit `--epoch-start` stay compatible. Explicit
gate-aware epochs use `PLAN.md` as the canonical strategy basis for both plan and code recovery and
derive `STATE.md` plus `RECOVERY.md` from the findings directory's parent run directory. Before each
gate's first round, RECOVERY contains exactly one verified baseline:

```text
<!-- kimiflow:strategy gate=plan epoch-start=1 fingerprint=<sha256(PLAN.md)> -->
```

`RECOVERY.md` contains one machine-readable marker inside the compact human-readable epoch entry:

```text
<!-- kimiflow:recovery gate=plan source-round=2 epoch-start=3 cap=4 before=<sha256> after=<sha256> -->
```

The resolver verifies:

- `source-round = epoch-start - 1`, preserving the global immutable ledger;
- marker gate, epoch start, and cap match the call;
- every expected source-round findings file exists, is nonempty, and parses canonically;
- `before` matches the verified baseline or previous same-gate receipt, while `after` is a different
  value recomputed directly from the current `PLAN.md` bytes;
- STATE says `Review gate`, `Review epoch start`, `Review epoch cap`, `Strategy fingerprint`, and
  `Recovery: active` with values matching the marker and call.

Any missing or inconsistent receipt emits `CLOSED / malformed`. A valid receipt only permits the new
epoch's normal closed/progress verdict; it never opens the review gate. Only a clean findings round can
emit `OPEN`.

Mechanical checks can prove continuity and changed bytes, not that the strategy changed meaningfully.
The epoch entry must still name the falsifiable hypothesis and semantic delta, and the behavioral eval
must reject rewording, model-only changes, or file churn as a new strategy.

## Documentation and Compatibility

- Phase 4 and Phase 7 document the receipt marker, typed pause categories, and approval boundary.
- The risky-bugfix example changes `stop and ask` to closed-gate autonomous recovery.
- Legacy resolver calls without `--epoch-start` retain their fixtures.
- Legacy fix approvals stored only in `DIAGNOSIS.md` remain valid under their original basis rules.
- Legacy active runs without flow schema may still use untyped `await-user`.

## Verification

1. Diagnosis, plan, affected-file, and strategy changes do not stale a new schema-3 approval.
2. Problem, acceptance, mode, scope, or build-risk changes do stale it.
3. Legacy diagnosis-comment approvals remain readable and retain their original protection.
4. A schema-3 `await-user` call with no/unknown kind is rejected.
5. During active recovery, `preview`/`commit` are rejected and each allowed missing-authority kind can
   pause the run.
6. A rejected pause leaves the Stop gate blocking; a permitted pause lets the turn end.
7. A later epoch without a receipt, verified baseline, complete source ledger, changed `PLAN.md`, or
   matching fingerprint chain is `CLOSED / malformed`.
8. A matching receipt permits normal intra-epoch resolver behavior but never emits `OPEN` by itself.
9. Existing resolver, approval, active-run, host-smoke, render, release-consistency, and full hook tests
   remain green.

## Non-goals

- Automatically judging the semantic quality of a recovery hypothesis.
- Removing the deliberate one-time Preview or final Commit gate.
- Allowing recovery to expand product scope, increase approved risk, or make irreversible decisions.
- Adding a generic workflow engine or another autonomous loop driver.
