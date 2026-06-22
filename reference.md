# kimiflow — reference

Detailed conventions for the orchestrator. Read a section only when its phase calls for it.

---

## Display verbosity (all phases)

Tunes **how much the orchestrator prints** — nothing else.

**Engine invariant (the whole point):** gates, on-disk artifacts (INTENT/PLAN/findings/…), evidence gathered, subagents spawned, thresholds and acceptance standards are **identical at every level**. Verbosity changes only the *visible chat output*; quality and rigor are constant. No gate/threshold/cost/scope instruction may ever be made conditional on verbosity.

**Levels (visible output only):**
| level | what the orchestrator prints |
|---|---|
| `quiet` | minimum: terse phase-marker lines; artifacts = **path only** (no 3-line summary); evidence = pass/fail + path; gate verdict still one line. Everything still happens — almost nothing is narrated. |
| `balanced` *(default)* | the Terse-output HARD RULE as written in SKILL.md: one-line phase announcement, ≤3-line artifact summary + path, one-line gate verdict, decisive evidence line(s). |
| `verbose` | fuller narration: multi-clause phase context, richer artifact summaries, more evidence lines, reasoning shown. |

**Bounded at every level:** invariant **(b)** of the HARD RULE — *never paste a full artifact or log dump into chat* — holds at **all** levels, `verbose` included. Verbose only lengthens summaries / adds narration; it never dumps a whole file or full logs. (This keeps the anti-bloat goal intact.)

**Precedence:** `flag > project > global > balanced`.

| source | location | set by |
|---|---|---|
| flag | `--quiet` / `--verbose` (one-off, **never persists**) | the invocation |
| project | `.kimiflow/verbosity` (at the git root) | `--set-verbosity`, `--settings`, onboarding |
| global | `~/.claude/kimiflow/verbosity` | `--settings`, onboarding |
| default | — | `balanced` |

**File format (both scopes):** a single line — the bare level word + newline (e.g. `verbose`). No keys, no other content. This format **structurally enforces the self-contained rule**: only a valid level word is ever read/honored, so a gate/cost/scope line placed in (especially) the global file is not a level and is silently ignored.

**Self-contained rule:** **only verbosity may live globally.** Nothing gate-, threshold-, scope-tier- or cost-related is ever read from `~/.claude` — those stay project-local / embedded in the skill (see "Self-contained — the skill is the authority" in SKILL.md). Verbosity is the single permitted global escape *because* it touches only presentation. (The pre-build summary gate's toggle lives **project-local** — `.kimiflow/build-gate` — for exactly this reason: it is gate-related, so it must never be read from `~/.claude`.)

**Helper — all reads AND writes go through one tested script** (`hooks/resolve-verbosity.sh`, invoked as `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/resolve-verbosity.sh`; unit-tested by `hooks/test-resolve-verbosity.sh`):
- `get [--flag <level>]` → resolves and echoes the level (precedence above).
- `origin [--flag <level>]` → echoes the winning source `flag|project|global|default`.
- `onboard-check [--flag <level>]` → echoes `ASK` iff `origin`==`default` (nothing set anywhere), else `SKIP`. Moves the first-run "is it already set?" decision out of the orchestrator's judgment and into the script, so onboarding can't misfire on an already-configured project.
- `set <project|global> <level>` → validates, `mkdir -p`s the parent, writes, **verifies the write** (stderr + exit 1 on failure — never a false success), echoes the path. A garbage level/scope is rejected without writing.

**Invocations (orchestrator behavior):**
- **`--quiet` / `--verbose`** — resolve this run only via `get --flag <level>`; never call `set`, never persist.
- **`--set-verbosity <level>`** — utility invocation: `set project <level>`, report the path, **exit** (no loop).
- **`--settings`** — utility invocation: ask verbosity level **and** scope (project/global) → `set <scope> <level>`; AND ask the pre-build gate `on`/`off` (project scope only) → `resolve-build-gate.sh set <on|off>`; report the paths, **exit**.
- **First-run onboarding** — at Phase 0 of a normal run, run `onboard-check` and fire **iff it prints `ASK` ∧ the session is interactive**. `ASK` already encodes the whole config precondition (no flag, no project file, no global file) — it is **mechanical**, not the orchestrator's to re-derive. Then ask **once** for level + save-scope → `set <scope> <level>`. An explicit answer makes the next `onboard-check` print `SKIP` ⇒ never asked again. **`SKIP`, headless (no interactive channel), or the user dismisses ⇒ `balanced`, no `set`, no block** (so it stays unset and a later interactive run may ask again — only an explicit answer persists).

---

## Pre-build summary gate (Phase 4 → Phase 5)

A user-approval checkpoint between the (internally vetted) plan and implementation. **Project-local, default on; control-flow only — it never changes the engine.**

- **Toggle:** `.kimiflow/build-gate` at the git root, one line `on` | `off`. Missing/invalid → `on` (fail-safe). Read/written ONLY by the unit-tested `hooks/resolve-build-gate.sh` (`get` / `set <on|off>`). **Project-local only** — the self-contained rule forbids gate-related config in `~/.claude`; there is no global build-gate and no per-run flag.
- **Fires** at the end of Phase 4 (after the plan-gate opens) **iff `get`==`on` ∧ the session is interactive**.
- **Summary content** (condensed from existing artifacts, not re-researched): Problem/Goal · Decisions · Plan/Design · Tests/Acceptance (`AC-N → test_name`) · Risks · + artifact paths. A **bounded terse-output exemption** like the commit-gate: structured summary + paths, never a full-artifact dump (invariant (b) still holds).
- **Outcomes:** approve → Phase 5; "change" → back to Phase 3 (revise → re-gate); `off` → straight to Phase 5; **headless / no answer → treat like `--prepare`** (STOP, update STATE, emit the `--resume` command — never build unapproved).
- **Set via `--settings`** (project scope only).

---

## Phase task list (all phases)

A native task-list widget for glance-level progress. In Phase 0 create one task per phase actually run (`TaskCreate`), scaled to scope; mark `in_progress`/`completed` via `TaskUpdate` as phases open/close. It **complements**, never replaces: `STATE.md` is the durable, resume-able record (survives sessions; the widget is ephemeral per session) and the colored markers remain the per-phase event line. It satisfies the "reads at a glance" goal as structured output, not prose narration (see terse-output (e)). Subagents keep their own internal task-lists — keep those out of the orchestrator's phase list.

---

## Intent clarification (grill, plain language) (Phase 1)

Goal: shared understanding BEFORE research/plan. kimiflow runs the interview **itself** (embedded, no external skill).

**Interview loop:**
- **One question at a time**, wait for the answer.
- **Offer a recommended answer or choices** per question — the user reacts instead of composing from scratch.
- Resolve in **dependency order** (the branch before its leaves).
- **What you can answer from the code/project, do NOT ask — look it up yourself.**

**Questions in plain language (mandatory):**
- Everyday language. No jargon, no code/framework/tool vocabulary. An unavoidable technical term → explain it in half a sentence.
- Short questions, **one thought per question**. No nested multi-questions.
- Concrete and with an example ("More like X or like Y?"), not abstract.
- Ask **WHAT** and **WHY** (goal, value, boundaries) — not **HOW** (implementation).

**Bounded:** cap **~5 questions**. Priority when tight: **scope > security/privacy > UX > technical details**. Stop when no real ambiguity remains OR the user says "ok". Depth by scope: trivial → none; small → 2–3; large → full. **Terminal state:** write INTENT.md → gate → on to research; do NOT implement.

**INTENT.md template** (plain language, NO tech/code):
```
# Intent: <feature in plain words>
## What we're building   (1–3 sentences)
## Why / goal            (which problem, for whom, what value)
## Out of scope          (deliberately left out)
## In scope              (deliberately included)
## What "done" looks like (from the user's view; concrete examples — basis for acceptance criteria)
## Assumptions           (until disproven)
## Open questions        ([NEEDS CLARIFICATION: …] — max 3, only what truly blocks)
```

**Gate:** show a **≤3-line summary of INTENT.md + its path** (do NOT paste the whole file), ask "Does this match?", continue only after explicit OK.

---

## Understand & research (Phase 2)

Goal: kimiflow must **truly understand** the affected code before planning — evidence-based, not guessed. This is what separates kimiflow from "fast but shallow".

**Codebase understanding (`Explore` agent, read-only):**
- **Where & how:** where similar things live, which patterns/conventions to match (naming, architecture, error handling, tests).
- **Integration points & data flow:** what calls what, which modules/interfaces are affected, where data comes from / goes to.
- **Existing tests:** what covers the area (basis for acceptance criteria + regression).
- **Risks/pitfalls/assumptions.**
- **Back every claim with `file:line`.** Unproven → "NOT VERIFIED".
- Read project memory/standards FIRST (see "Project memory & standards") and only fill gaps. Depth by scope.

**External research:** only the gaps that vault + codebase don't close (current standard, API/library behavior) — web/context7/docs, sources with URL. Parallel to codebase understanding when both are needed.

**RESEARCH.md structure:**
```
## Understanding (how the code works in the area)   … with file:line evidence.
## Patterns/conventions to match
## Integration points & data flow
## Existing tests
## External findings (standard/API) — sources with URL
## Risks & assumptions
## Open unknowns   [NEEDS UNDERSTANDING: …] — resolve plan-blocking ones first.
```

**Considered alternatives (`large` scope).** For `large` runs, `RESEARCH.md`/`PLAN.md` records 2–3 candidate approaches and the trade-off that selected the chosen one — guards against tunnel-vision on the first idea. `small`/`trivial` are exempt.

**Mini-gate:** a *plan-blocking* unknown → resolve first, don't plan on assumptions.

---

## Fix mode (diagnosis) (Phase 1–2)

For bug fixes this branch replaces the intent/research logic. **Core rule: prove the problem first, then fix — never on a guessed cause.** From phase 3 on, `PROBLEM.md` ≙ `INTENT.md`, `DIAGNOSIS.md` ≙ `RESEARCH.md`.

**PROBLEM.md (Phase 1, plain language):**
```
# Problem: <bug in plain words>
## Symptom            (error message / crash / wrong behavior)
## Expected vs. actual
## Reproduction       (steps / inputs / environment; since when? always or intermittent?)
## Affected / severity
```

**Diagnosis (Phase 2) — the three mandatory steps:**
- **Reproduce:** ideally a **failing test** (Red). Not reproducible = a finding → clarify with the user.
- **Verify the root cause:** find AND prove the cause (`file:line` + why that spot produces the symptom). Hypothesis → minimal proof. **Not** the first guess.
- **Fix research (proactive, BEFORE the fix):** how is this *currently solved correctly*? Vault → `WebSearch`/context7/`WebFetch` → official docs/issues. The model may be outdated → check the obvious guess against the current state; discard stale/naive approaches. A **fresh** Vault hit that already answers it → skip the web step; if you re-search, change the **search vector** — don't repeat a prior query.

**DIAGNOSIS.md:**
```
## Reproduction              (how triggered — ideally a test name)
## Verified root cause        (file:line + evidence why it produces the symptom)
## Correct fix approach (researched)  (with source; contrasted against the naive guess)
## Discarded approaches
## Risks & regression
```

**Diagnosis gate:** root cause **not** proven → **do NOT fix.** The fix's acceptance criterion = **"the reproduction no longer fails" + no regression.**

---

## Audit mode (ponytail lens) (Phase 1–7)

A third mode (beside feature/fix) to safely shrink over-engineered / dead code in a **bounded target**. **Staged:** find → report → approve → execute. **Engine unchanged**; reuses the deletion gate ("Code mandate"), adversarial reviewers ("Review rubric"), the Phase-4 summary gate, and atomic commits.

**Core rule (ponytail Rung-1):** for each item ask not "can we dedupe" but **"should this exist at all?"** — resolves to *delete* or *earns-its-place → simplify*. Every cut is **caller-verified at execution time**; on any doubt, downgrade or skip — never delete on assumption.

**Tags:** `yagni` (speculative architecture) · `delete` (dead, zero-caller) · `shrink` (dedupe, behavior preserved) · `stdlib` (hand-rolled → standard library, edge-cases preserved).

**Safety (non-negotiable):**
- **Caller-greps run repo-wide** (`src` + tests), never only the target — a symbol in the target can be called from anywhere.
- **Caller-grep is a MINIMUM:** dynamic dispatch / reflection / string-keyed lookup escape it → tests-green + a do-NOT-touch list + the Phase-4 "refute the cut" lens are the backstop.
- **Git-history-freshness:** weigh a zero-caller symbol by `git log` — recently touched = likely WIP (downgrade); import removed long ago = confidently dead.

**`AUDIT-INTENT.md` (Phase 1, plain language):** target paths · aggressiveness · behavior-preserve constraints · do-NOT-touch hints · what stays untouched.

**`AUDIT.md` (Phase 2) — self-contained slices, ranked biggest-cut-first:**
```
## Slice <n>: <scope>  (~−<x> lines)
**Scope:** <paths>
**ponytail lens (why each exists):** per item — delete | earns-its-place→simplify
**Findings (ranked):**
| tag | what to cut | replacement | path:line | repo-wide pre-delete grep (→ 0 / expected) | freshness |
**do-NOT-touch:** <symbol> — <why it stays despite the grep suspicion>
**Verify gate:** grep-sweep clean → typecheck/build → tests green (shrink/stdlib: green before+after)
**Companion edits:** <tests referencing cut code, edited in lockstep>
```

**Execution (Phase 5–7):** one slice at a time — verify grep==0 → apply → run the slice's verify gate → companion edits → **one slice = one commit**. Never batch slices. `--prepare` stops after Phase 4 with the approved `AUDIT.md`.

---

## Project memory & standards (Phase 2 read · Phase 7 append)

Lets kimiflow get smarter about a project over time instead of re-deriving it every run. **Opt-in, append-only, verified content only** — the anti-hallucination rule governs what may be written; a wrong "standard" must never silently poison future runs.

**Read (Phase 2, always — cheap: native `CLAUDE.md` + two small `.kimiflow` files only if present):**
- The project's native **`CLAUDE.md`** (Claude Code loads it anyway) — house rules, stack, conventions.
- If present: **`.kimiflow/STANDARDS.md`** (accumulated conventions) and **`.kimiflow/DECISIONS.md`** (past decisions/lessons).
- Use these as ground truth; the `Explore` agent only fills the gaps they leave.

**Append (Phase 7, after the commit, only if the user enabled project memory):**
- `.kimiflow/STANDARDS.md` — newly **verified** conventions worth keeping (e.g. "errors use `Result<T>`; tests live in `__tests__/`"). One line each, no speculation.
- `.kimiflow/DECISIONS.md` — a 3–5 line entry: what we chose, why, what surprised us (source-attributed).
- Optional `.kimiflow/LEDGER.md` — one line per run: slug · scope · rounds used · gate pass/fail · knobs enabled.

Keep all three **flat markdown and short**. This is the lightweight version of steering/standards files (Kiro/Agent OS/Cursor) and learnings (GSD) — no DB, no schema, no scoring.

---

## Vault conventions (Phase 2)

The vault is an **optional** notes MCP (e.g. Obsidian — `obsidian_simple_search`, `obsidian_get_file_contents`, `obsidian_append_content`). **No vault MCP → skip, note in STATE.md** — the repo-local `.kimiflow/` memory still works. Notes follow the **user's language**, never a fixed one.

- **Discover, don't assume — kimiflow self-optimizes placement but keeps it findable.** Before saving, inspect the vault's existing layout and **reuse** an existing research/notes folder and an existing index/MOC note. Only if none exists, fall back to one predictable folder (`Research/` at the vault root). Never assume hardcoded folder names.
- **Template:** use the vault's own research template if it has one; otherwise the built-in minimal structure below.
- **Filename:** descriptive title + date suffix `YYYY-MM`. No `/` in the filename.
- **Frontmatter required:** `date:` + `source:`. `tags:` with `type/research` + topic tags.
- **Freshness on read:** weigh a hit by its `date:` (+ file mtime via `obsidian_get_recent_changes` for amendments). A fresh hit that answers the question **replaces** web research; re-research only a **stale** hit (fast-moving topic) or one that **doesn't cover the current question** — and then with a **different search vector**, not the same query. Optionally set `updated:` when amending a note (else mtime carries the amendment date).
- **Structure (built-in fallback):** Question/trigger · Core answer (1–3 sentences) · Details · Gotchas · Sources (with "retrieved YYYY-MM-DD") · Related.
- **Anti-hallucination:** mark uncertain points "NOT VERIFIED".
- **Findable index:** maintain one index note so saved research can be found again — reuse the vault's existing MOC if there is one, else append to (or create) a `Research` index note: a date-stamped wikilink + 1-line summary per entry.
- **Don't save** trivial lookups (version, 1-line API check).

---

## Review rubric (Phase 4 plan-gate · Phase 7 code-review)

**Binary gate, NO numeric score.** A 0–10 score is an anti-pattern (LLMs aren't calibrated — same input → 7 then 9). What counts: are there open BLOCKER/HIGH, yes/no.

**Severity:** BLOCKER (breaks goal / data / security) · HIGH (correctness/requirement gap with real impact) · MEDIUM (quality/dup/dead code; doesn't block) · LOW (style; doesn't block).

**Reviewer rules:**
- **Fresh context, independent, adversarial framing.** Tell each reviewer: "you did NOT write this; assume it is flawed; find the strongest objection." (Counters same-family self-preference — kimiflow's Claude writes AND reviews; diversity is the de-biaser.)
- **Reasoning before verdict.** Justify first, then severity.
- **Every finding with a reference** (file:line / plan section). No evidence → no finding.
- **Anti-hallucination:** a false finding is worse than a missed one. Unsure → drop it.
- **Diverse lenses** (Phase 4): A = goal/completeness/measurability (goal-backward); B = security/edge/error/architecture/over-engineering.
- **Reviewers write findings to their own files — the gate counts them mechanically (closes self-report + silent-drop).** Each reviewer writes this round's findings to an append-only, orchestrator-immutable file `.kimiflow/<slug>/findings/r<N>-<lens>.md` — one canonical line per finding, at column 0, **no newline in the reason**:
  - `FINDING <SEVERITY> <ref> :: <one-line reason>` — `<SEVERITY>` is exactly one of `BLOCKER|HIGH|MEDIUM|LOW`; `<ref>` is `file:line` or `PLAN.md §section`. A reviewer that finds nothing writes the single sentinel line `NONE`.
  - Reviewers do NOT self-report a count; the orchestrator **reads** these files and never edits them — so no finding can be silently dropped or self-resolved.
- **Gate count (mechanical, current round only):** `open = grep -hE '^FINDING (BLOCKER|HIGH) ' .kimiflow/<slug>/findings/r<N>-*.md | wc -l`. `0` → gate open. **Fail-closed:** the gate is CLOSED if any expected reviewer file is missing/empty (review incomplete ≠ zero findings) **or** any line fails the canonical grammar (malformed severity / indentation / multi-line → reject). The gate never reads `REVIEW.md`.
- **Resolution = non-recurrence, re-derived by the reviewer (closes self-attestation).** A finding counts as resolved only because the freshly re-spawned reviewer of the next round, re-reviewing the revised `PLAN.md`/diff, **no longer emits it**. The orchestrator never flips a finding's status by its own judgment and never writes a self-supplied "resolved".
- **Code-review scope (Phase 7): correctness/requirements/security only, NOT style.** Also check: were tests weakened/deleted to go green? This is **mechanized** by `hooks/test-weakening-scan.sh` (deleted test files, added `.skip`/`xit`/`it.only`/`@Disabled`/`@pytest.mark.skip`/`t.Skip`/`assumeTrue(false)`, removed assertions) → `FLAG` advisories in `.kimiflow/<slug>/ADVISORIES.md`. **Advisories are non-gating** — a separate channel, never counted by the gate grep — and are **surfaced at the commit-gate**, where the human dismisses (legit refactor) or promotes them (fail-closed: an unresolved FLAG blocks the commit). The scan is a **minimum**: semantic weakening (changed expected values, loosened tolerances) is not detected.

**Anti-oscillation (blocker-aware):** compare the open BLOCKER/HIGH set round r→r+1. **Stop + ask with the gate CLOSED** if the open BLOCKER/HIGH count does not strictly decrease across the round, or a finding that had disappeared reappears. The 3-round cap is a hard backstop: reaching it → **stop + ask, gate CLOSED — never auto-proceed.**

**Knob — multi-run verdict (large/critical only):** run the reviewer's binary verdict 3× and take the majority (single-judge verdicts have real run-to-run variance). Not for default `small`.

---

## Acceptance-criteria template (Phase 3)

Each criterion needs three parts plus a test link:

1. **EARS sentence:** Ubiquitous "The <system> shall <response>." · Event "When <trigger>, the <system> shall <response>." · State "While <precondition>, …" · Unwanted "If <trigger>, then …".
2. **Concrete example:** input → expected output (the oracle — unambiguous pass/fail).
3. **Verification method** (exactly one): automated test · command + expected exit code · file/fixture diff · screenshot compare · verifier agent (last resort).
4. **Test link:** `AC-N → test_name` — the named test that proves it. This makes the test suite the per-feature drift detector (the one spec-sync mechanism with long-term evidence).

Properties: **observable**, **binary** (pass/fail, not "almost"), **bounded**. Reject criteria without a clean method. **Lint** for vague terms ("fast", "robust", "user-friendly" → quantify) and missing **error/edge** criteria. Trace each to `INTENT.md`/`PROBLEM.md`.

**Coverage check (Phase 4, before the gate):** every criterion → a plan task AND a test; no orphan task without a criterion. Gaps are findings — fix the plan first.

**Task interface block (parallel/worktree tasks).** Each PLAN.md task names `Consumes:` (signatures it uses from earlier tasks) and `Produces:` (exact function names + parameter/return types later tasks rely on). A worktree implementer sees only its own task — this block is how it learns neighbor signatures without shared context. Sequential single-implementer runs may omit it.

Example:
```
AC-1 — When an empty search string is sent, the API shall return HTTP 400.
  Example: POST /search {"q":""} → 400 + {"error":"q required"}
  Check: automated test test_search_empty_query (exit 0 = green)   →  AC-1 → test_search_empty_query
```

---

## Verification (goal-backward) (Phase 6)

Run each criterion's method and show the command + the decisive output line(s) (not full logs). Then verify **goal-backward** — "task completion ≠ goal achievement":

- For each criterion's artifact, check three levels: **Exists** (the code is there) · **Substantive** (real logic, not a stub/placeholder) · **Wired** (imported AND actually used on a real path). Mark ✓VERIFIED / ⚠ORPHANED / ✗STUB / ✗MISSING. A criterion is met only at **Wired**.
- **Regression:** existing/affected test suite green.
- **Cold-start smoke test:** if the diff touches `server.*` / `app.*` / `migrations/*` / `seed*` / `docker-compose*`, boot the thing from scratch once — many "green tests, broken app" failures only show on a cold boot.
- Non-automatable criteria → a verifier subagent that derives pass/fail from evidence and **does not trust** the implementer's self-report.
- Any failure → back to phase 5 (escalation rule applies).

---

## Hard test-gate (opt-in, per project) (scaling knob)

kimiflow ships a **Stop hook** (in `hooks/`) that blocks the turn from ending while the project's tests are red — turning "tests green" from self-reported into enforced-by-construction. It is **opt-in and safe by default**: the hook **no-ops unless the project opts in**, so installing kimiflow never imposes a gate on unrelated work.

**To enable in a project:** create a **local (untracked)** `.kimiflow/test-gate` containing the test command, e.g.
```
npm test --silent
```
With that file present, the hook runs the command on stop; on failure it blocks with the failing output so the agent keeps working. No file → the hook exits 0 immediately. Keep it tests-only; do not block `git commit` (kimiflow's human commit-gate already covers that).

**Auto-enabled for `large` scope:** a `large` run writes this marker in Phase 7 from the test command verified green in Phase 6 (idempotent — an existing marker is left untouched) and announces it, so the hardest runs can't silently skip the gate. `small`/`trivial` and unrelated repos stay opt-in (no marker, no gate).

**Security — local/untracked only:** the marker's first line is executed (`eval`) on every stop. So a committed marker from a cloned repo could run as a **drive-by**. To prevent that, **kimiflow refuses to run a git-tracked `.kimiflow/test-gate`** — only a local, untracked marker (created by you or by kimiflow) is honored; a tracked one is a no-op (a note goes to stderr). Keep `.kimiflow/` out of version control (gitignore it); **never commit `.kimiflow/test-gate`**. Even a local marker still runs your own shell command, so only put a test command there.

---

## Code mandate (Phase 3 directive · Phase 5 build · Phase 7 review)

- **Simplicity-first:** minimal code for the problem. No speculative abstractions, no configurability without a request, no error handling for impossible cases. "Would a senior call this overkill?" → yes → simplify.
- **Match the existing architecture** + project standards: adopt the project's patterns, naming, style. State-of-the-art means **fitting**, not **new at any cost**.
- **Scales with the project:** prototype ≠ enterprise layers; a hot path needs performance awareness.
- **Efficient & elegant:** readable, no needless recomputation in hot paths, clear single-purpose units.
- **Surgical:** touch only what the request demands; clean up your own orphans; leave foreign code alone.
- **Deletions are caller-verified (mechanical).** Removing code requires a recorded proof of **zero live callers** — a `grep`/search over `src` (and tests) that returns none, attached to the change. A deletion without that proof is a **code-review BLOCKER**. If something survives the grep but a reviewer judges it load-bearing, record it on a short **do-NOT-touch** list with the reason instead of deleting (anti-hallucination for deletions — a wrong "dead" claim is worse than a missed one).

---

## Commit hygiene (Phase 7 commit-gate)

Before the commit, after explicit user OK:

1. Read `git status` + `git diff --staged` before composing the message.
2. **Stage only explicitly named paths** — no `git add -A` / `git add .`.
3. **Never** stage `.env`, keys, tokens, credentials — on suspicion, stop and ask.
4. If the project has tests and the change touches code: run them. Red → STOP, no commit.
5. **No co-author trailer, no "Generated with" line, no AI attribution.**
6. Commit message: terse, what & why.

**Mechanized (kimiflow repos only):** points 2–3 are also enforced by the `commit-secret-gate` PreToolUse hook — it **blocks** `git add -A`/`.` and any `git commit` whose staged paths match secret patterns (`.env*`, `*.pem/.key/.p12/.pfx`, `id_rsa*`, `.npmrc`/`.pypirc`, `secret(s)`/`credential(s)`/`api_key`/`access_token`/`auth_token` in a path). It is **auto-active only where a `.kimiflow/` directory exists at the git root** (kimiflow creates one in Phase 0), so it never polices unrelated repos — and commits in repos without `.kimiflow/` are knowingly unprotected. The pattern list is a **minimum deny-list**, not exhaustive; false positives on filenames merely containing those words are possible (resolve by committing the safe file by name from outside a kimiflow run).
