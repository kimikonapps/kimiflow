# Outcomes — paired Plain-vs-Kimiflow evidence

This evaluation asks whether Kimiflow produces better software outcomes than a good Plain session under
the same execution contract. It does not treat gate integrity, extra process or lower cost alone as
proof of better quality.

## Current status

`evals/outcome-comparisons.jsonl` contains two recorded pilot comparisons. Both are intentionally
classified as field notes. The first lacks exact call/rework counters and exposed test-harness confounds;
the second used exact counters but the shared sandbox prevented the task-required Git commit, while the
Kimiflow arm crossed the between-sample token cap before it could be stopped. Therefore there are still
zero primary-eligible comparisons and the current claim status remains `insufficient_evidence`: no better
outcome has been proven.

The pilot used a disposable checkpoint-recovery bug repository. Plain and Kimiflow both completed the
task, passed all 12 held-out acceptance groups and tied in the verified blind review. Plain took 196
seconds; Kimiflow took 1,854 seconds. The result is useful for repairing the benchmark protocol, but not
for a general product claim.

The second pilot used a disposable transactional JSONL import feature repository. Both arms passed all
12 held-out acceptance groups. A blinded review preferred Kimiflow on one reproduced MEDIUM robustness
edge: it enforced UTF-8 for stdin even when the process text encoding was deliberately misconfigured.
Plain took 334 seconds and 319,170 total tokens; Kimiflow took 1,954 seconds and 25,933,540 total tokens
before termination. Neither arm could create the requested commit because the shared workspace-write
sandbox made Git metadata read-only. This is useful directional Evidence, but remains a field note rather
than a primary result.

## Safety boundary

The validator and Summary only read locally recorded JSONL and repository-relative Evidence files. They
do not start sessions, call a model, access the network or change Kimiflow's runtime flow.

Any later Plain-vs-Kimiflow runs must use purpose-built disposable benchmark repositories or disposable
copies of public open-source test projects. Private or production user projects must never be used as a
test environment. Each comparison run remains separate work and requires explicit start authority.

## Commands

```bash
bash hooks/outcome-comparisons.sh validate
bash hooks/outcome-comparisons.sh summary
```

`validate` exits nonzero when a recorded Row is malformed, semantically inconsistent, unsafe or bound to
missing or fingerprint-mismatched Evidence. A Row with estimated, missing or null primary measurements is
kept as a field note and excluded from the primary result without replacing null with zero.

`summary` reports the full count partition:

- `recorded_pairs = valid_pairs + field_note_pairs + invalid_pairs`
- `excluded_pairs = field_note_pairs + invalid_pairs`
- only `valid_pairs` contribute to outcome metrics and verdicts

## Fair-pair contract

Each JSONL Row binds exactly one `plain` arm and one `kimiflow` arm to the same task digest, source commit,
byte-bound start snapshot, acceptance oracle and blinded final review. Both arms must use the exact same
model, effort, tool, permission, time-budget, token-budget and execution fingerprints. Session IDs and
session-Evidence fingerprints must be unique so reused runs cannot inflate the sample.

Every Row also carries a stable `task_id`; task kind is exactly `bug` or `feature`. Each arm records
model calls and tool calls separately. `confounds=null` means no known Confound. A nonempty list of
nonempty Confound descriptions keeps the Pair as a field note, while an empty or malformed list is
invalid.

Evidence references are repository-relative regular files with declared SHA-256 fingerprints. The
validator rejects absolute paths, traversal, symlink components, missing or oversized files and byte
mismatches. Correctly bound bytes show what was recorded; the maintainer remains responsible for the
authenticity of the underlying run Evidence.

The start-snapshot Evidence is canonical JSON. It records the source commit, the tracked binary-diff
fingerprint and a path-sorted list of untracked files with POSIX mode bits and content fingerprints. A
path, mode or byte change therefore changes the bound snapshot.

Session, outcome, diff, merge and post-merge Evidence files are canonical compact UTF-8 JSON with sorted
keys. Each has one closed typed payload that must exactly equal its corresponding Row projection,
including nulls and nested execution or defect values. Duplicate or unknown payload keys, noncanonical
bytes and any Row mutation after Evidence capture invalidate the Pair.

## Reported measurements

The Summary keeps these measurements separate:

- completion and acceptance per arm;
- internal BLOCKER/HIGH defects found before completion;
- remaining BLOCKER/HIGH defects after completion;
- observed post-merge BLOCKER/HIGH defects in a fixed 7–30 day window;
- pairwise `kimiflow - plain` differences for tokens, elapsed seconds, user interactions, rework rounds
  and changed lines of code.

It also emits one dataset-ordered `per_task` entry per primary-eligible Pair with
`comparison_id`, `task_id`, `task_kind` and `verdict`, so aggregate counts remain auditable.

Post-merge windows can be `pending`, `observed` or `not_applicable`. Pending future Evidence blocks a
cost-based verdict when quality is otherwise tied. An intentionally unmerged arm can remain eligible for
its recorded pre-merge outcome, but its post-merge criterion is not applicable.

## Win, tie and loss

The verdict compares quality first, in this fixed order:

1. completion;
2. acceptance;
3. remaining BLOCKER defects;
4. remaining HIGH defects;
5. observed post-merge defects;
6. tokens, elapsed time, user interactions, rework and changed LOC.

Lower raw cost matters only when the available quality measurements are tied. Fewer than ten valid Pairs
always yields `claim_status=insufficient_evidence`. Ten or more valid Pairs yields
`claim_status=evidence_available`; it does not by itself establish statistical significance or prove that
Kimiflow is generally better. A later collection target of six bugs and six features is not a second MVP
gate: real comparison collection remains explicitly outside this validator.
