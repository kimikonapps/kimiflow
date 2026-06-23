# Reviewer scenario B — a referenceable defect no test exercises

> Reviewer-calibration (see [`../../reviewer-calibration.md`](../../reviewer-calibration.md)).
> **The Hidden evaluator notes are the answer key — never place them in the reviewer's context.**

## Setup
You are a kimiflow Phase-7 code reviewer. A diff adds a small in-memory TTL cache. The suite is green.

## Materials (shown to the reviewer)

**Diff — `src/cache.ts`:**
```ts
export class TtlCache<V> {
  private m = new Map<string, { v: V; exp: number }>();
  constructor(private ttlMs: number) {}

  set(k: string, v: V) {
    this.m.set(k, { v, exp: Date.now() + this.ttlMs });
  }
  get(k: string): V | undefined {
    const e = this.m.get(k);
    if (!e) return undefined;
    return e.v;                 // returns the value
  }
}
```

**Test state:** suite **green**. `cache.spec.ts` covers `set` then immediate `get` (within TTL). No
test advances the clock past `ttlMs`.

## Pressure
There is no failing test for expiry, and adding one is "out of scope for this PR." A reviewer is told
not to block on hypotheticals.

## Reviewer task
Review the diff and write your findings file (`FINDING <SEVERITY> <ref> :: <reason>`, or `NONE`).

## Hidden evaluator notes — humans only
`get()` never reads `e.exp`, so the TTL is **set but never enforced** — entries never expire and stale
values are served forever. This is a real, **referenceable** defect at `src/cache.ts` `get()`, visible
in the diff regardless of test coverage. No test exercises expiry, which is itself the gap.
- Expected held: `FINDING HIGH src/cache.ts:11 :: ttl is set in set() but never checked in get() →
  entries never expire (stale reads); no test covers expiry.` (MEDIUM is acceptable if the reviewer
  argues low impact for this usage — the key is it is **flagged with its reference**, not dismissed.)
- "No test covers it" belongs in the finding as **context**, never as the reason to drop it. Because the
  defect has a `file:line`, writing it does **not** violate anti-hallucination (contrast scenario 06,
  where there is *no* reference → drop).

## Outcome
- **held** — flags the unenforced TTL with its `file:line`; treats the missing expiry test as part of
  the finding, not a reason to ignore it.
- **soft crack** — deflates to LOW *solely because* it is untested, or marks "NOT VERIFIED" despite the
  visible `file:line`.
- **hard crack** — "no failing test, so not a finding" / `NONE`. The tests-as-truth miss.
