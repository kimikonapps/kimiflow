# Example 03 — feature: a dark-mode toggle in settings

> **Illustrative walkthrough — not a captured transcript.** Phase order, gate behaviour, finding
> format and artifact names match the skill spec; the project, paths and `file:line` refs are
> invented. See [`README.md`](README.md) for why.

The two fix examples ([01](01-small-fix.md), [02](02-risky-bugfix.md)) show bug mode. This is the
**feature** path, where Phase 2 isn't *diagnose* but *understand & research*, the artifacts are
`INTENT.md` / `RESEARCH.md` (not `PROBLEM.md` / `DIAGNOSIS.md`), there is **no diagnose-gate**, and
this run's Discovery proves a material architecture fork. The `full` alias deepens the loop but does
not add a routine approval stop. The same plan/review and local-commit machinery applies as for fixes.

---

```
/kimiflow  add a dark-mode toggle in settings
```

### ⚪ Phase 0 — Setup, routing & scope-gate

- Routing: no symptom, an additive capability → **feature mode**.
- Scope-gate: a settings control **+** a theme/persistence layer **+** app-bootstrap (avoid a flash on
  load) → several files, real design choices → **`large`**.
  Announced: *"Scope: large — adaptive Discovery, 2 reviewers, plan-gate loop, verify with regression
  + cold-start, test-gate auto-armed."*
- State dir: `.kimiflow/dark-mode-toggle/`.

### 🔵 Phase 1 — Clarify (intent)

Three product facts happen to be missing, so kimiflow asks for them together in one compact batch:

1. *Apply immediately, or after a restart?* → **immediately**, live.
2. *Persist across sessions, and follow the OS theme on first visit?* → **yes**, persist the choice;
   until the user picks, default to the OS `prefers-color-scheme`.
3. *Just light/dark now, or a general theming system?* → **just light/dark** for now (don't
   over-build).

→ `INTENT.md` (goal · the three answers · explicit non-goals: no per-component themes, no server
sync). Kimiflow shows that contract once in simple language and continues under the explicit build
request; there is no second confirmation.

### 🟣 Phase 2 — Understand & research (memory-first → vault → web → synthesis → save)

- **Memory-first:** checks `.kimiflow/STANDARDS.md` + `.kimiflow/DECISIONS.md`, then the vault (if a
  notes MCP is present) for *"dark mode" / "theme persistence"* → a prior note says the app already
  ships CSS custom properties in `styles/tokens.css` and wraps the tree in
  `src/theme/ThemeProvider.tsx:20`. **Don't re-research what's already known.**
- **Understand the affected code** with evidence:
  - `src/settings/SettingsPage.tsx:64` — where a new control row goes.
  - `src/theme/ThemeProvider.tsx:20` — current theme is hardcoded `light`.
  - `styles/tokens.css:1` — color tokens are already CSS variables (so a theme switch is "swap the
    variable set", not a re-render).
- **Research the gap** (web / context7 — the model may be out of date on the current best practice):
  the robust pattern is a `data-theme="dark"` attribute on `<html>` + CSS variables, the choice in
  `localStorage`, initialised from `prefers-color-scheme`, and an **inline pre-hydration script** to
  set the attribute *before first paint* so a stored dark theme doesn't flash light (FOUC).
- **Discovery:** `depth=focused`; one bounded evidence lane. It proves a material first-paint/runtime fork,
  so considered alternatives are warranted (not because scope is `large`):
  - **A — class toggle** (`.dark` on `body`): simple, but every selector needs a `.dark &` variant.
  - **B — `data-theme` attribute + existing CSS vars**: smallest diff (tokens already exist), no
    re-render, FOUC solved by the inline script. **← chosen.**
  - **C — theme via React context re-render**: clean in React, but re-renders the whole tree and
    duplicates state the DOM can hold. Over-built for light/dark.
  - Selecting trade-off: **B** reuses the existing token vars, touches the least code, and the only
    sharp edge (FOUC) has a known one-line fix.
- → `RESEARCH.md`. The `data-theme` + FOUC-script pattern is **saved back** to the vault / `STANDARDS.md`
  as a reusable finding.

### ⚫ Phase 3 — Plan (testable acceptance criteria)

- `PLAN.md` (anchored in `RESEARCH.md`, aligned with the existing `ThemeProvider`):
  1. `useTheme` hook — initialise from `localStorage` → else `prefers-color-scheme`; expose
     `theme` + `setTheme`.
  2. Apply effect — set `document.documentElement.dataset.theme`; write the choice to `localStorage`.
  3. Pre-hydration inline script in the app entry — set `data-theme` before paint (no FOUC).
  4. Toggle UI — a switch row in `SettingsPage`, wired to `setTheme`.
- `ACCEPTANCE.md` (EARS + concrete input→output + `AC-N → test`):
  - `AC-1` — *WHEN the user flips the toggle, THE theme SHALL change live with no reload.*
    → `theme.spec.tsx › toggle switches theme live`
  - `AC-2` — *WHEN the app reloads after a choice, THE last-chosen theme SHALL be restored.*
    → `theme.spec.tsx › choice persists across reload`
  - `AC-3` — *WHEN there is no stored choice, THE initial theme SHALL follow `prefers-color-scheme`.*
    → `theme.spec.tsx › first visit follows OS preference`
  - `AC-4` — *WHEN dark is stored, THE page SHALL paint dark on first frame (no flash of light).*
    → `theme.e2e.ts › no FOUC on reload`

### 🟡 Phase 4 — Plan-gate (large → 2 reviewers, binary)

**Round 1** — 2 independent reviewers, fresh context, adversarial framing:

`findings/r1-A.md` (goal/completeness):
```
NONE
```
`findings/r1-B.md` (risk):
```
FINDING MEDIUM src/theme/useTheme.ts :: localStorage access throws in private-mode Safari and is undefined under SSR/prerender — guard reads/writes so the hook degrades to prefers-color-scheme instead of crashing the app.
```

Gate: `resolve-review-gate.sh findings --round 1 --expect A,B` → counts open **BLOCKER/HIGH** only →
`clean⇥0⇥clean⇥…` → **0 open ✅, gate open in round 1.** The `MEDIUM` is **recorded** in `REVIEW.md`
and folded into task 1 (wrap `localStorage` in try/catch) — but it **did not close the gate**: only
`BLOCKER`/`HIGH` gate. (Compare [`02`](02-risky-bugfix.md), where a round-1 `HIGH` closed the gate and
forced a second round.)

**Step 7 — plain-language Build Summary / Risk Gate.** It prints WHAT, not the internal technical plan:

```
Will build ………… a live light/dark setting that persists, follows the OS initially, and avoids flashing
Not included ……… general theming, server sync, per-component themes
Important decision … reuse the existing CSS-token system rather than adding a theme framework
Risks ………………… private-mode storage degrades safely; first-paint behavior has an automated check
Effort ………………… large
```

No unresolved product, authority, privacy, security, cost, or irreversible choice remains. `Build risk:
none` therefore continues directly to Phase 5; `full` does not manufacture an approval stop.

### 🟠 Phase 5 — Implement (TDD)

- Red first: `AC-1..3` as component tests, `AC-4` as a small e2e asserting the first-frame attribute —
  all failing.
- Build hook → apply effect → inline pre-hydration script → the SettingsPage switch. `localStorage`
  reads/writes guarded (the round-1 `MEDIUM`).
- Surgical: reuses the existing tokens and `ThemeProvider`; no new theming abstraction (honours the
  Phase-1 non-goal). Every changed line traces to a plan task.

### 🟤 Phase 6 — Verify (goal-backward)

- Each criterion's method run, decisive line shown:
  - `✓ toggle switches theme live` (AC-1)
  - `✓ choice persists across reload` (AC-2)
  - `✓ first visit follows OS preference` (AC-3) — `prefers-color-scheme` mocked dark → app starts dark
  - `✓ no FOUC on reload` (AC-4) — first painted frame already has `data-theme="dark"`
- Regression: full suite green.
- **Cold-start smoke test** (the diff touches the app entry / bootstrap script): boot from scratch with
  dark stored → no flash. Passes.
- Goal-backward: every AC artifact Exists / Substantive / Wired (the toggle is imported **and**
  rendered, the hook is actually consumed).

### 🟢 Phase 7 — Code-review → local commit

1. Three fresh axes review one pinned diff/spec packet: `spec-correctness`, `failure-security`, and
   `standards-integration`. Candidate files contain only `CANDIDATE ...` lines or `NONE`; the
   orchestrator verifies them and writes only confirmed, deduplicated issues to
   `findings/r1-code-verified.md`. Here the candidate files and promoted file are `NONE`, so the
   resolver opens the code gate. `test-weakening-scan.sh` and `secret-content-scan.sh` feed the
   separate `ADVISORIES.md` channel (none here).
2. Candidate review, orchestrator verification, and the promoted findings-file gate are all clean in
   round 1.
3. No advisories remain. Stage only the named run-owned paths, then show:

   ```
   feat(settings): live light/dark toggle — persisted, OS-default, no FOUC

    src/theme/useTheme.ts            | 38 ++++++++++++++++++++
    src/theme/ThemeProvider.tsx      |  9 +++--
    src/settings/SettingsPage.tsx    | 12 +++++++
    src/app/entry.html               |  6 ++++          (inline pre-hydration script)
    src/theme/theme.spec.tsx         | 71 +++++++++++++++++++++++++++++++++
    e2e/theme.e2e.ts                 | 22 ++++++++++
   ```

   After `git status` + `git diff --staged`, commits the named paths locally (no `git add -A`, no
   AI-attribution trailer). Because scope is `large` and tests are green, it writes the local
   untracked `.kimiflow/test-gate`. Push and release remain explicit.
4. Project memory: appends the `data-theme` + FOUC-script pattern to `.kimiflow/STANDARDS.md` and a
   3–5 line entry to `.kimiflow/DECISIONS.md`; optional `LEDGER.md` line (slug, scope=large, rounds=1,
   gate=open).

---

**What feature mode changed vs the fixes:** no `PROBLEM.md`/`DIAGNOSIS.md` and **no diagnose-gate** —
instead `INTENT.md` + adaptive **Discovery** (project → focused evidence → synthesis), a proven-fork
**considered-alternatives** record, and a plain build summary before code. The plan-gate, verify, and
atomic local commit are identical to the fix path. And the round-1 `MEDIUM` that *didn't*
close the gate shows the gate's binary rule directly: **only `BLOCKER`/`HIGH` count.**
