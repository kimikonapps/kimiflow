# Example 01 — small fix: pagination shows one item too few

> **Illustrative walkthrough — not a captured transcript.** Phase order, gate behaviour, finding
> format and artifact names match the skill spec; the project, paths and `file:line` refs are
> invented. See [`README.md`](README.md) for why.

A small, well-understood bug. The point of this example is the **scope-gate keeping it lean**:
`small` skips the plan-gate *loop*, keeps plan review to one lens, and goes straight through verification to
an atomic local commit — the same correctness gates as a `large` run, none of the fan-out cost.

---

```
/kimiflow  the products list shows 19 items per page but the page size is set to 20
```

### ⚪ Phase 0 — Setup, routing & scope-gate

- Routing: a reproducible "shows the wrong number" symptom → **fix mode**.
- Scope-gate: one file, one obvious off-by-one, no architectural risk → **`small`**.
  Announced: *"Scope: small — lean review, no plan-gate loop, no worktrees."*
- State dir: `.kimiflow/pagination-off-by-one/`.

### 🔵 Phase 1 — Problem brief

The report already supplies the symptom and expected result: page size is 20, but a full page shows 19.
Kimiflow writes `PROBLEM.md` and inspects the pagination path immediately. No routine confirmation stop.

### 🟣 Phase 2 — Diagnose

- Reproduces it: a unit test asserting `page(1).length === 20` over 40 rows → **fails, gets 19**.
- Proves the cause, doesn't guess:

  ```
  src/products/paginate.ts:42
    const start = (page - 1) * pageSize;
    const end   = start + pageSize - 1;   // ← off-by-one: slice end is exclusive
    return rows.slice(start, end);
  ```

  `Array.prototype.slice(start, end)` is end-**exclusive**, so `+ pageSize - 1` drops the last row.
- ⛔ **Diagnose-gate:** root cause is proven at `paginate.ts:42`, with the language semantics named →
  cleared. (No proof here would have meant **no fix**.)

→ `DIAGNOSIS.md`. No vault/web research needed — the cause is local and certain.

### ⚫ Phase 3 — Plan

Minimal. One task, one acceptance criterion (fix mode → "the reproduction no longer fails"):

- `PLAN.md`: change the slice bound in `paginate.ts`; no API change, no new file.
- `ACCEPTANCE.md`:
  - `AC-1` — *WHEN a full page is requested over N ≥ pageSize rows, THE list SHALL return exactly
    `pageSize` items.* → `paginate.spec.ts › returns a full page of pageSize`.

### 🟡 Phase 4 — Plan-gate (small → 1 reviewer, no loop)

One adversarial reviewer (lens B, risk), fresh context, sees `PLAN.md` + `ACCEPTANCE.md` +
`PROBLEM.md` + the named code. Writes findings to `findings/r1-B.md`:

```
NONE
```

Gate: `resolve-review-gate.sh findings --round 1 --expect B` → `clean⇥0⇥clean⇥…` → **0 open ✅**.
Small scope → no second round, no loop.

**Plain-language build summary:** verified cause = exclusive slice bound; fix = remove the `- 1`;
scope = paginator + its test; not included = API or pagination redesign; risk = none (small and
reversible). The original fix request already authorizes the work, so schema 4 continues directly.

### 🟠 Phase 5 — Implement (TDD)

- The Phase-2 reproduction is already the red test → keep it.
- Fix:

  ```diff
  - const end = start + pageSize - 1;
  + const end = start + pageSize;
  ```
- Surgical: one line, traces directly to the diagnosis. Nothing else touched.

### 🟤 Phase 6 — Verify

- `npm test -- paginate.spec.ts` → `✓ returns a full page of pageSize (20)` — the reproduction now
  passes.
- Regression: full suite green.
- Goal-backward: AC-1's test exists, is substantive, and is wired (imported + run). Done ≠ done-only.

### 🟢 Phase 7 — Code-review → local commit

- Two focused axes inspect the same pinned diff packet: `spec-correctness` traces AC-1 and regression
  behavior; `failure-security` checks edge/failure behavior and folds in documented standards. Both
  write `NONE` candidate files. The orchestrator verifies that result, writes
  `findings/r1-code-verified.md: NONE`, and only that promoted file is counted by the gate.
- After clean review, stages the two named paths and shows the summary, `git status`, and
  `git diff --staged`:

  ```
  fix(products): paginate slice end is exclusive — return full pageSize per page

   src/products/paginate.ts        | 2 +-
   src/products/paginate.spec.ts   | 14 ++++++++++
  ```

  Commits those named run-owned paths locally (no `git add -A`, no AI-attribution trailer). Push and
  release remain separate explicit actions.

---

**Why this is the cheap path:** same diagnosis, review, verification, and atomic local commit as a
large run, but the scope-gate kept Phase-4 plan review to one lens, skipped its loop, and avoided any
worktree. Phase 7 still uses the minimum two independent code-review axes. Lean work stays lean.
