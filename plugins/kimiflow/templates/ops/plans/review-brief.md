# Review brief — <scope>

Read `<absolute path>/.kimiflow/ops/rules-worker.md` first. This brief adds only what is specific to
this review.

## Scope

<exact files and commits>

## Decided, not to re-negotiate

- <decision> — <reason>

## Dimensions

- Correctness against the brief and the feature sheet
- Data loss or corruption
- One source of truth; duplicates of an existing control, model, state or service
- Nesting and complexity
- Tests, including the inverse case (edited and unedited, failure and success)

## Finding schema

ID · location · evidence (≤ 10 lines) · failure scenario · smallest fix · VERIFIED / PLAUSIBLE

Severity BLOCKER / HIGH / MEDIUM / LOW, no score. A wrong finding costs more than a missing one.

## Report sections

1. Findings
2. NOT VERIFIABLE (what you could not check, and why)
3. Conform (what you checked and found in order)
