# kimiflow — reference

Detailed conventions for the orchestrator. Read a section only when its phase calls for it.

---

## Launcher mode (empty/vague invocation)

The launcher is a context-aware front door for users who explicitly ask for Kimiflow but do not provide an
actionable target yet. It starts on `/kimiflow`, `$kimiflow`, `@kimiflow`, `--launcher`, `--menu`, or vague
requests such as "run Kimiflow" / "lass Kimiflow drüberlaufen". It does not start on clear feature/fix/audit
requests.

**Mechanical snapshot:** before showing options, run `hooks/launcher-status.sh --pretty` from the installed
Kimiflow root (Codex: with `KIMIFLOW_HOST=codex`). The script is read-only and returns JSON for:
repo status, dirty working tree, project-map depth/status, memory/recall status, curation needs, background handles (`total`, status counts, collectable/stale counts, items), open findings,
open feature-check findings, open improvement slices, repo-doc presence, active-session status, and active/backlog/done runs. The orchestrator may summarize this
JSON, but must not invent counts.

**Start menu (user language):** show a compact numbered menu, tuned to the snapshot. Typical full menu:

```text
Kimiflow Start

Projektkarte: standard · aktuell
Memory: 820/900 Tokens · aktuell
Effizienz: geschätzt 18% Token Savings · 12 Runs · Sicherheit niedrig
Offene Findings: 4
Feature-Check Findings: 1
Geparkte Runs: 2
Repo-Doku: vorhanden
Working Tree: geändert
Aktive Session: offen · Items 2 · aktuell
Background: 2 einsammelbar · 1 stale

Was willst du tun?

1. Status ansehen
2. Projektkarte prüfen/aktualisieren
3. Offene Findings ansehen/abarbeiten
4. Geparkten Run fortsetzen
5. Bug fixen
6. Feature bauen
7. Verbesserungen priorisieren
8. Doku schreiben/aktualisieren
9. Idee/unklaren Auftrag ausarbeiten
10. Eingebautes Feature prüfen
11. Memory/Recall prüfen oder kuratieren
12. Background Handles ansehen/einsammeln
```

If `.kimiflow/project/INDEX.json` is missing, bias the first menu toward Project Map Bootstrap:
`standard (recommended)` / `quick` / `deep` / `skip`. If a map exists, use it first: read `INDEX.json`,
then only relevant `FACTS.jsonl` lines and markdown sections. New code exploration is for stale/unknown/gap
areas only.

**"Bring Kimiflow current" offer:** `launcher-status.sh` reports
`maintenance.bring_current_recommended` plus terse `maintenance.reasons`. If it is true, the menu should offer
a first-class "Kimiflow auf aktuellen Stand bringen" action before feature/fix work. It is an interactive
hygiene pass, not an implementation mode:
- **Run-state hygiene first:** normalize completed runs to `Status: done` when `STATE.md` explicitly says
  Phase 7 is done / `RUN COMPLETE`; ask before changing ambiguous runs. `Status: backlog` remains a deliberate
  parked-plan marker.
- **Delta over full scan:** use `project-map-status.sh`, `INDEX.json` section hashes, and `git log --name-status`
  / `git diff --name-status` from the map baseline to HEAD to find changed areas. Read only affected sections,
  recent relevant commits, and changed files; do not re-map the whole codebase unless the index is missing or
  invalid.
- **Baseline count is context:** `maintenance.commits_since_project_map_baseline` is informational only. Use
  `maintenance.reasons` and `project_map.status` to decide whether a refresh is recommended.
- **Cross-tool history as hints:** if project-local workflow artifacts such as `.planning/`, `.gsd/`, roadmap
  logs, or similar tool ledgers exist, read their indexes/recent summaries first and treat them as hints to
  reconcile with the current code. Do not bulk-ingest another tool's full archive.
- **Then refresh:** update only stale `.kimiflow/project/` sections and run-state metadata. Raw maps remain
  local/private; repo docs are updated only when the user chooses a docs/storage action.
- **Memory hygiene:** if `memory.curation.recommended` is true, offer a token-cheap memory curation action.
  This runs `memory-router.sh curate --write`, updates `MEMORY-INDEX.json`, and never rewrites/delete-learns
  destructively without a later explicit action. Ignore `memory.curation.silent_reasons` in normal UI; for example
  `many_learnings` is an internal health/threshold hint, not user-facing work when memory is under budget and fresh.

**Working-tree start gate:** before any normal write-mode Kimiflow run starts (feature, fix, audit, project-map/update, docs write), run `hooks/working-tree-gate.sh`. The stable output is `WORKING_TREE_GATE<TAB>OPEN|CLOSED<TAB>dirty=<n><TAB>staged=<n><TAB>unstaged=<n><TAB>untracked=<n><TAB>reason=<code><TAB>detail=<paths>`. `OPEN` is required before slugging, active-session start, or file edits. `CLOSED` means: stop, show the dirty-path summary, and ask the user to commit, stash, or otherwise clean the worktree first. Do not continue by accepting the risk; the point is to avoid mixing an old diff with a new Kimiflow run. Read-only launcher/status/inspection may still run and report the dirty state. `.kimiflow/` local state is ignored by the gate.

**Drilldowns, not dumps:**
- Findings: if `findings.open > 0`, offer `summarize`, `fix highest priority`, `group by area`, `show details`,
  `back`. Read `.kimiflow/project/FINDINGS.md`; show a compact list only. A selected fix routes into a normal
  `--fix`, docs, or improve run with its own state dir.
- Backlog runs: list slug, status, mode, scope, plan commit, affected-file count, and stale risk from the
  snapshot. Selecting a run starts the resume safety check; it never jumps directly to implementation.
- Active session: if `active_session.present` and not terminal, show it before the normal menu. Offer
  `continue`, `show items`, `finish after verification`, `park`, `fail`, or `abort`. If
  `active_session.stale_risk == "needs_revalidation"`, the first action is revalidation; blind finish is not
  allowed.
- Background handles: if `background.collectable > 0`, offer `collect results`, `show handles`, `mark stale`,
  `cancel`, or `back`. Use `hooks/background-run.sh collect --id <id>` before trusting any result. A `CLOSED`
  collect verdict means the foreground orchestrator must re-run/revalidate the work instead of applying it. If
  `background.stale > 0`, surface it as maintenance but do not delete anything automatically.
- Done runs: count `Status: done`; for legacy states, a Phase-7-done / `RUN COMPLETE` signal may be inferred as
  done so old completed runs do not remain noisy active work. Surface missing `LEARNING-REVIEW.md` in
  `runs.learning_reviews.missing_done` and stale/invalid existing reviews as `learning_reviews_need_attention`;
  completed current runs are clean only when the recorded or skipped learning review verifies `OPEN`.
- Improve: translate "improve" into handles: `top 3 levers`, `architecture simplification`,
  `code quality/refactoring`, `scalability/performance`, `tests/robustness`, `docs/onboarding`,
  `security/privacy`. "Top 3 levers" produces a prioritized improve analysis before any build plan.
- Existing feature check: route to `/kimiflow --verify-feature <feature-or-path>`. Use it when the user wants to
  check whether an already-built feature really works, whether frontend/backend/API pieces are wired together, or
  whether tests/docs cover the delivered behavior. It is review-only; confirmed findings become fix/improve choices,
  not automatic edits.
- Memory: list `MEMORY.md` budget, learning counts by status, vault availability, and curation reasons. Offer
  `recall for current task`, `curate index`, `show current learnings`, `back`; do not dump full Vault notes or
  full `LEARNINGS.jsonl`.
- Vault/Obsidian: if `provider.available` is false but `provider.detection.available` is true, offer
  `Obsidian verbinden`. This runs `memory-router.sh provider connect`, writes only
  `.kimiflow/project/VAULT-PROVIDER.json`, then offers `provider sync --write` to create the local
  `VAULT-SYNC.md` handoff. If `provider.health.status` is `connected_local_only`, offer `Obsidian MCP einrichten`
  and prefer `hooks/vault-mcp-open-terminal.sh --host <current-host>` on macOS, or
  `hooks/vault-mcp-setup.sh --host <current-host> --interactive` as the plain-terminal fallback, so the API key is
  entered only in the user's Terminal, not chat. If it is `authenticated`, distinguish local REST API validation
  from actual direct MCP tools before offering targeted Vault prefetch/sync. It does not store an API key in
  `.kimiflow/` and does not write external Vault notes blindly.
- Vague idea/spec: route to existing Explore/Prepare in V1. Native `--spec` is a follow-up slice, not part of
  launcher V1.

**Resume safety check:** before any backlog/prepared run can enter Phase 5, validate the plan against current
code:

1. Read `.kimiflow/<slug>/STATE.md`, plus `PLAN.md`, `ACCEPTANCE.md`, `RESEARCH.md` or `DIAGNOSIS.md` when present.
2. Determine `Plan commit:` from STATE; if absent or unverifiable, mark `unknown`.
3. Determine affected files from `Affected files:` in STATE; fallback to path references in plan/research/diagnosis.
4. Compare `git diff --name-status <plan_commit> HEAD`, staged changes, unstaged changes, and untracked non-ignored files.
5. If any affected file changed, or the plan basis/affected files are unknown, show `Plan revalidieren
   (empfohlen)` and do not offer blind implementation.
6. Only when affected files are known and unchanged may the menu offer `Fortsetzen`.

**Revalidation:** a stale/unknown prepared plan goes back to Phase 2/3 narrowly: use the current project map
first, refresh stale affected sections if accepted, compare plan assumptions against current code, then update
`PLAN.md` / `ACCEPTANCE.md` and re-open the plan gate when drift exists. No drift → Phase 5 may continue.

Headless/no-answer behavior is always safe: print the snapshot summary, do not select a mode, do not resume
implementation, and STOP.

---

## Active Session Contract

The Active Session Contract makes an explicitly started Kimiflow run sticky across follow-up prompts. It is
plan-agnostic: it does not know what feature is being built; it only knows whether the current Kimiflow run has
open items, stale state, or a terminal outcome.

**Helper:** `hooks/active-run.sh`

Core files:

- `.kimiflow/session/ACTIVE_RUN.json` — project-local pointer to the current active run.
- `.kimiflow/<slug>/ITEMS.jsonl` — run-local list of sequential changes/items.
- `.kimiflow/<slug>/SESSION-OUTCOME.json` — terminal outcome written by finish/park/fail/abort.

Commands:

```bash
hooks/active-run.sh status --pretty
hooks/active-run.sh start --run .kimiflow/<slug> --mode feature --scope small --write
hooks/active-run.sh append-item --title "..." --kind feature --write
hooks/active-run.sh mark-built --id item_001 --write
hooks/active-run.sh mark-accepted --id item_001 --write
hooks/active-run.sh mark-rejected --id item_001 --reason "..." --write
hooks/active-run.sh drop-item --id item_001 --reason "out of scope" --write
hooks/active-run.sh refresh-baseline --write
hooks/active-run.sh finish --write
hooks/active-run.sh park --reason "waiting for user validation" --write
hooks/active-run.sh fail --reason "verification failed" --write
hooks/active-run.sh abort --reason "user switched workflow" --write
```

**Prompt behavior:** the `UserPromptSubmit` hook calls `active-run.sh prompt-context`. When an active session
exists, it injects a small reminder into the next model turn: keep the follow-up request inside Kimiflow unless
the user explicitly exits/parks/fails/aborts/switches. It does not store the raw prompt text.

**Stop behavior:** the `Stop` hook calls `active-run.sh stop-gate`. It blocks completion while an active
session is non-terminal, unless the stop is already a hook continuation. The model must continue the Kimiflow
loop or close it mechanically with `finish`, `park`, `fail`, or `abort`.

**Item lifecycle:** sequential changes accumulate as items:

- `pending` — requested but not built.
- `built` — implemented but not accepted.
- `accepted` — user or verification accepted it.
- `rejected` — user/verification says it still fails; finish is blocked.
- `dropped` — deliberately removed from scope with a reason.

`finish --write` refuses `pending`, `built`, and `rejected` items. It also refuses stale sessions. After the run
is revalidated, `refresh-baseline --write` records the current commit and lets finish proceed.

**Learning boundary:** `finish --write` is the only active-session terminal path that promotes positive
learnings. It runs `memory-router.sh review-run --write` and then `verify-run`. `park`, `fail`, and `abort`
clear the active session with `learning_review.status = not_promoted`, so failed or unverified work does not
become project memory.

**Staleness:** `status` compares the active session baseline to current Git changes and affected files from
`STATE.md`. If a relevant file changed, status reports `stale_risk: needs_revalidation`, the launcher surfaces
that state, prompt-context mentions revalidation, and finish is blocked until revalidated.

---

## Background Handles

Background Handles make long-running or later-collected Kimiflow work visible without letting draft results apply
themselves. They are a local registry, not a host-native agent spawner. Codex/Claude subagents, background threads,
or manual follow-up work may write results into the registry; the foreground Kimiflow orchestrator must still collect
and verify them.

**Helper:** `hooks/background-run.sh`

Core files:

- `.kimiflow/background/HANDLES.jsonl` — append-only handle event/index log.
- `.kimiflow/background/<id>/STATUS.json` — current handle metadata.
- `.kimiflow/background/<id>/HANDOFF.md` — compact task handoff for the worker/background agent.
- `.kimiflow/background/<id>/RESULT.md` — worker result summary.
- `.kimiflow/background/<id>/FILES.json` — files the worker inspected or drafted.
- `.kimiflow/background/<id>/ADVISORIES.md` and `VERIFY.md` — candidate advisories and verification notes.

Commands:

```bash
hooks/background-run.sh start --kind deep-codebase --title "Map architecture" --affected hooks --write
hooks/background-run.sh list --json
hooks/background-run.sh status --id <handle-id>
hooks/background-run.sh update --id <handle-id> --status ready --result RESULT.md --files FILES.json --advisories ADVISORIES.md --verify VERIFY.md --write
hooks/background-run.sh collect --id <handle-id>
hooks/background-run.sh cancel --id <handle-id> --reason "not needed" --write
hooks/background-run.sh mark-stale --id <handle-id> --reason "base changed" --write
```

Valid kinds are `deep-codebase`, `docs`, `security`, `improve`, and `custom`. Statuses are `pending`, `running`,
`ready`, `finished`, `stale`, `failed`, and `cancelled`. `ready` and `finished` are collectable; `stale`, `failed`,
and `cancelled` are terminal.

**Stable collect verdict:**

```text
BACKGROUND_HANDLE<TAB>OPEN|CLOSED<TAB>id=<id><TAB>status=<status><TAB>reason=<code><TAB>detail=<detail>
```

`OPEN` means the result is current enough for foreground review. It still does not apply anything. `CLOSED` blocks
use of the result. Common reasons: `not_ready`, `result_missing`, `base_invalid`, `affected_missing`, `stale`,
`status_cancelled`, `status_failed`, and `status_stale`.

**Staleness:** `start` records `base_commit` and normalized repo-relative `affected_paths`. `collect` checks
committed drift since `base_commit`, staged changes, unstaged changes, and untracked paths. Directory affected paths
match descendants, so `hooks` matches `hooks/launcher-status.sh`. Unsafe affected paths (`/abs`, `..`, empty paths,
internal `.kimiflow` paths) and unsafe ids are rejected. Malformed persisted status data fails closed.

**Candidate-only boundary:** security/advisory and improvement outputs are candidates. `ADVISORIES.md`, `VERIFY.md`,
and `RESULT.md` may inform `FEATURE-CHECK.md`, `FINDINGS.md`, project maps, repo docs, or memory only after the
foreground orchestrator verifies them with targeted reads/commands. A background handle never writes repo docs,
memory rows, project-map facts, or canonical findings by itself.

**Launcher:** `launcher-status.sh` includes `.background` with counts for `total`, `pending`, `running`, `ready`,
`finished`, `collectable`, `stale`, `failed`, `cancelled`, and `items`. `collectable` counts handles whose current
`collect` verdict is `OPEN`, not merely handles with `ready`/`finished` status; `stale` also includes drift detected
during list-time collection checks. `background_handles_collectable` and `background_handles_stale` appear as
maintenance reasons.

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
| global (Claude Code) | `~/.claude/kimiflow/verbosity` | `--settings`, onboarding |
| global (Codex) | `${CODEX_HOME:-~/.codex}/kimiflow/verbosity` when invoked with `KIMIFLOW_HOST=codex` | `--settings`, onboarding |
| default | — | `balanced` |

**File format (both scopes):** a single line — the bare level word + newline (e.g. `verbose`). No keys, no other content. This format **structurally enforces the self-contained rule**: only a valid level word is ever read/honored, so a gate/cost/scope line placed in (especially) the global file is not a level and is silently ignored.

**Self-contained rule:** **only verbosity may live globally.** Nothing gate-, threshold-, scope-tier- or cost-related is ever read from host-global Kimiflow config (`~/.claude` for Claude Code, `${CODEX_HOME:-~/.codex}` for Codex) — those stay project-local / embedded in the skill (see "Self-contained — the skill is the authority" in SKILL.md). Verbosity is the single permitted global escape *because* it touches only presentation. (The pre-build summary gate's toggle lives **project-local** — `.kimiflow/build-gate` — for exactly this reason: it is gate-related, so it must never be read from host-global config.)

**Helper — all reads AND writes go through one tested script** (`hooks/resolve-verbosity.sh`, invoked from the installed Kimiflow plugin root; Claude Code uses `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/resolve-verbosity.sh`, Codex uses `KIMIFLOW_HOST=codex <plugin-root>/hooks/resolve-verbosity.sh`; unit-tested by `hooks/test-resolve-verbosity.sh`):
- `get [--flag <level>]` → resolves and echoes the level (precedence above).
- `onboard-check [--flag <level>]` → echoes `ASK` iff nothing is set anywhere (the winning source is the built-in default), else `SKIP`. Moves the first-run "is it already set?" decision out of the orchestrator's judgment and into the script, so onboarding can't misfire on an already-configured project.
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
- **Outcomes:** approve → Phase 5; "change" → back to Phase 3 (revise → re-gate); **defer → backlog** → STOP, mark `Status: backlog` in STATE (a finished, plan-gate-approved plan parked before implementation: phases 0–4 done, 5 open), emit the `--resume` command — a *deliberate, offered* park; `off` → straight to Phase 5; **headless / no answer → treat like `--prepare`** (STOP, mark `Status: backlog`, emit the `--resume` command — never build unapproved).
  - **Explicit defer vs. silent fallback:** the **defer** outcome and the **headless / no-answer** stop reach the *same* parked state and the *same* `Status: backlog` marker; the only difference is **intent** — defer is the explicit human choice ("good plan, not now"), headless is the silent fallback (no interactive approver). Both are mechanically the `--prepare` stop. The `backlog` marker is written only by these Phase-4 parks (and the step-6 plan-gate-open `--prepare` branch) reaching 0–4-done; an earlier stop (Explore, mid-phase) stays `active`. The `--resume` no-slug listing surfaces the marker (absent → `active`) so backlog items are visible as a backlog.
- **Set via `--settings`** (project scope only).

---

## Phase task list (all phases)

A native task-list widget for glance-level progress. In Phase 0 create one task per phase actually run (`TaskCreate`/`TaskUpdate` in Claude Code; Codex plan/status updates in Codex), scaled to scope; mark `in_progress`/`completed` as phases open/close. It **complements**, never replaces: `STATE.md` is the durable, resume-able record (survives sessions; the widget is ephemeral per session) and the colored markers remain the per-phase event line. It satisfies the "reads at a glance" goal as structured output, not prose narration (see terse-output (e)). Subagents keep their own internal task-lists — keep those out of the orchestrator's phase list.

---

## Explore phase (before Phase 1) — opt-in

A divergent front-end that widens the **direction** space before the convergent Clarify locks the
WHAT. Runs only on `--explore` or an accepted offer. **Feature mode only** — fix is
root-cause-convergent, audit is itself a survey. If exploration reveals the real work is a fix or a
cleanup, that's a **finding** → suggest `--fix`/`--audit`; don't force it through Explore.

**Trigger & offer-on-detect.** The explicit `--explore` flag always runs the phase. Otherwise, when a
request reads as open-ended **and** is not already a concrete spec (a clear WHAT with
acceptance-shaped detail), kimiflow **offers once**: "This looks open-ended — explore a few
directions first?". **Decline → normal routing. Headless / no interactive answer → skip the offer,
proceed normally (never block).** Only `--explore` forces the phase. Detection markers (illustrative,
in the user's language): EN "not sure how", "what are my options", "ideas for", "brainstorm",
"explore", "which way", "how should I"; DE "Ideen für/zu", "weiß nicht wie", "welche Optionen", "wie
am besten".

**Flow — bound → fan-out → menu → pick:**
1. **Bound (≤1 question).** If the request lacks what's needed to explore *relevantly* (goal, hard
   constraints, what "better" means), ask ONE plain-language bounding question. Already bounded →
   skip. A full interview is Clarify's job, later — not here.
2. **Fan-out (2–3 explorers, parallel, read-only `Explore` agents).** Each is forced to a **distinct**
   direction via a diverse lens (e.g. minimal/MVP · robust/long-horizon · sideways/reframe) and is
   **codebase-grounded**: it reads what exists and cites `file:line` where a direction leans on
   current code, marking speculation "NOT VERIFIED". Adversarial-diversity framing so they don't
   converge. Each returns: framing · sketch · rough effort/risk · key trade-off · what it rules out.
   Counts against the agent budget (2–3, within the lean default).
3. **Synthesize → menu.** Dedup/merge into a terse menu of 2–3 directions (each ≤3 lines: name ·
   essence · the deciding trade-off). Write `EXPLORE.md`; show the menu + path — a **bounded
   terse-output exemption** (structured, like the pre-build summary; never a full-artifact dump,
   invariant (b) still holds).
4. **Pick — the Explore gate (human; no numeric score):**
   - **continue** → the chosen direction **seeds Phase 1 Clarify** (`INTENT.md` anchored to it;
     Clarify converges the details and does NOT re-ask the WHAT from scratch) → normal loop.
   - **stop** → behave like `--prepare`: STOP, update STATE (Explore done + chosen direction), emit
     `/kimiflow --resume <slug>` (resume re-enters at Clarify with the chosen direction).
   - **none of these** → ONE re-fan-out using the user's steer (why none fit), bounded to a single
     retry (anti-spin), then stop + ask.
   - **headless / no answer** → never auto-pick a direction; behave like `--prepare`.

**`EXPLORE.md`** (at `.kimiflow/<slug>/EXPLORE.md`, dense — artifact-economy):
```
# Explore: <fuzzy idea in plain words>
## Bounding            (the constraints/goal that scoped the search)
## Directions (2–3)
   ### <Direction name>
   - Essence        (1–2 sentences)
   - Grounding      (file:line it leans on; "NOT VERIFIED" if speculative)
   - Effort / risk  (rough, relative)
   - Trade-off      (what you gain / give up)
   - Rules out      (what choosing it forecloses)
## Chosen             (the picked direction + why; or "none → re-explored / stopped")
```

**Handoff to Clarify:** Phase 1 reads `## Chosen` and seeds `INTENT.md` from it — the WHAT is now the
chosen direction, which Clarify then narrows. **Resume:** Explore done with a chosen direction ⇒
`--resume` starts at Phase 1 Clarify, seeded by it.

**STATE:** the `Phase E (explore): open|in-progress|done` line is written **only when Explore runs** —
it is **absent on non-explore runs** (the phase is purely additive; a run without `--explore`/an
accepted offer is behaviorally unchanged).

---

## Existing feature check (`--verify-feature`)

A review-only mode for features that are already implemented. It answers: does the feature exist, is it wired
end-to-end, and what should be fixed next? It does not edit code or commit. If the user chooses a confirmed issue,
start a new normal `--fix` or improve run with that finding as input.

**Flow:**
1. **Target.** Require a feature name, route, component, command, API path, or file path. If vague, ask one short
   question for the target and expected user-visible behavior.
2. **Recall first.** Use project map and Memory Router recall for the target. Prefer existing current maps/history
   over fresh broad scans.
3. **Cheap lens fan-out when available.** Send narrow read-only lens tasks to small/fast subagents when the host
   supports model choice (Codex may use small model overrides for these lenses; Claude Code should use its cheapest
   suitable subagent/model setting if available). Fallback is one sequential reviewer. Do not spawn duplicate lenses.
4. **Lens set (choose only relevant lenses):**
   - `behavior`: can a user actually trigger the feature and see the expected result?
   - `wiring`: are frontend, backend, routes, commands, hooks, events, and exports connected?
   - `contract`: do API/schema/types/config/env contracts match on both sides?
   - `state-data`: is state, persistence, migration, cache, and error handling coherent?
   - `tests`: do tests cover the real behavior rather than stubs or isolated helpers?
   - `docs-security`: are docs accurate and are security/privacy implications handled?
5. **Candidate format from each lens:** one line per issue:
   `CANDIDATE <SEVERITY> <file:line|artifact> :: <claim> :: verify=<smallest check>`.
   `NONE` if clean. No long prose, no full logs.
6. **Orchestrator verification.** A candidate is not a Kimiflow finding until the orchestrator verifies it with
   targeted code reads, commands, or reproduction. Unverified candidates are recorded as `UNVERIFIED` in
   `FEATURE-CHECK.md`, not promoted to blockers.
7. **Output.** Write `.kimiflow/<slug>/FEATURE-CHECK.md` with: target, evidence read, lens results, verified
   findings, unverified candidates, recommended next actions. Confirmed HIGH/BLOCKER findings may also be written
   to `findings/r1-feature-check.md` in the normal `FINDING <SEVERITY> <ref> :: <reason>` format so the launcher can
   surface them.

**Token rule:** the value comes from not loading everything into the orchestrator. Lenses get only target/context
paths and return candidate lines. The orchestrator verifies only candidates, so total context stays bounded. If a
lens would need a broad scan, prefer Project Map refresh first.

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

**BUG-REPRO.md (Phase 2 + Phase 6 evidence):**
```
## Red
Red command: <smallest command/manual step that reproduces the bug>
Red status: failed
Red output: <decisive line only>

## Green
Green command: <same focused command after the fix>
Green status: passed
Green output: <decisive line only>

## Regression
Regression command: <affected suite>
Regression status: passed
```

`BUG-REPRO.md` is the durable handoff that prevents a fix run from teaching Kimiflow an unproven success. Write the Red block before changing production code; complete the Green and Regression blocks only after the fix. If no regression command is applicable, write `Regression status: not applicable` with a short reason.

**Red-Green Gate:** after Phase 6 in fix mode, run:

```bash
hooks/red-green-gate.sh .kimiflow/<slug> --mode fix
```

The stable output is `RED_GREEN_GATE<TAB>OPEN|CLOSED<TAB>blockers=<n><TAB>reason=<code><TAB>detail=<codes>`. `CLOSED` blocks Phase 7, memory promotion, and `Status: done`. This gate verifies the evidence contract; it does not execute the commands.

---

## Audit mode (Phase 1–7)

A third mode (beside feature/fix) to safely shrink over-engineered / dead code in a **bounded target**. **Staged:** find → report → approve → execute. **Engine unchanged**; reuses the deletion gate ("Code mandate"), adversarial reviewers ("Review rubric"), the Phase-4 summary gate, and atomic commits.

**Core rule (existence-first):** for each item ask not "can we dedupe" but **"should this exist at all?"** — resolves to *delete* or *earns-its-place → simplify*. Every cut is **caller-verified at execution time**; on any doubt, downgrade or skip — never delete on assumption.

**Tags:** `yagni` (speculative architecture) · `delete` (dead, zero-caller) · `shrink` (dedupe, behavior preserved) · `stdlib` (hand-rolled → standard library, edge-cases preserved).

**Safety (non-negotiable):**
- **Caller-greps run repo-wide** (the repo's source + tests), never only the target — a symbol in the target can be called from anywhere.
- **Caller-grep is a MINIMUM:** dynamic dispatch / reflection / string-keyed lookup escape it → tests-green + a do-NOT-touch list + the Phase-4 "refute the cut" lens are the backstop.
- **Git-history-freshness:** weigh a zero-caller symbol by `git log` — recently touched = likely WIP (downgrade); import removed long ago = confidently dead.

**`AUDIT-INTENT.md` (Phase 1, plain language):** target paths · aggressiveness · behavior-preserve constraints · do-NOT-touch hints · what stays untouched.

**`AUDIT.md` (Phase 2) — self-contained slices, ranked biggest-cut-first:**
```
## Slice <n>: <scope>  (~−<x> lines)
**Scope:** <paths>
**Existence lens (why each exists):** per item — delete | earns-its-place→simplify
**Findings (ranked):**
| tag | what to cut | replacement | path:line | repo-wide pre-delete grep (→ 0 / expected) | freshness |
**do-NOT-touch:** <symbol> — <why it stays despite the grep suspicion>
**Verify gate:** grep-sweep clean → typecheck/build → tests green (shrink/stdlib: green before+after)
**Companion edits:** <tests referencing cut code, edited in lockstep>
```

**Execution (Phase 5–7):** one slice at a time — verify grep==0 → apply → run the slice's verify gate → companion edits → **one slice = one commit**. Never batch slices. `--prepare` stops after Phase 4 with the approved `AUDIT.md`.

---

## Project memory & standards (Phase 2 read · Phase 7 append)

Lets kimiflow get smarter about a project over time instead of re-deriving it every run. The old
`.kimiflow/STANDARDS.md` and `.kimiflow/DECISIONS.md` files remain short human-readable steering files. The
new durable project-intelligence memory lives in `.kimiflow/project/` and is routed by
`hooks/memory-router.sh`. **Verified content only** — the anti-hallucination rule governs what may be written;
a wrong "standard" must never silently poison future runs.

**Read (Phase 2, always — cheap: native `CLAUDE.md` + two small `.kimiflow` files only if present):**
- The project's native **`CLAUDE.md`** (Claude Code loads it anyway) — house rules, stack, conventions.
- If present: **`.kimiflow/STANDARDS.md`** (accumulated conventions) and **`.kimiflow/DECISIONS.md`** (past decisions/lessons).
- `memory-router.sh status`, then `.kimiflow/project/MEMORY.md` only if present and under budget.
- Use these as ground truth; the `Explore` agent only fills the gaps they leave.

**Append/record (Phase 7, after verification):**
- `.kimiflow/project/LEARNINGS.jsonl` — durable, machine-readable learnings written through
  `memory-router.sh record`, each with evidence, confidence, sensitivity, freshness, source commit, and status.
- `.kimiflow/project/MEMORY-INDEX.json` — cheap lookup/curation index written by
  `memory-router.sh curate --write`.
- `.kimiflow/project/MEMORY.md` — bounded always-on summary; keep it around 500-900 tokens and curate when
  over budget. Do not make it a second README.
- `.kimiflow/STANDARDS.md` — newly **verified** conventions worth keeping (e.g. "errors use `Result<T>`; tests live in `__tests__/`"). One line each, no speculation.
- `.kimiflow/DECISIONS.md` — a 3–5 line entry: what we chose, why, what surprised us (source-attributed).
- Optional `.kimiflow/LEDGER.md` — one line per run: slug · scope · rounds used · gate pass/fail · knobs enabled · **approx. token cost** · **post-commit outcome** (e.g. `regression-in-7d: y/n`). The last two turn the ledger into a cheap **ROI instrument**: over ~10–20 runs the cost/outcome columns show whether a tier earns its spend.

**When is `large` worth it?** (Honest, pending ledger evidence.) `large` multiplies reviewer × round × knob cost; the current expectation is that it rarely beats default **`small` + one cross-family review** — reserve it for the scope-gate's real triggers (auth/money/privacy, migrations, subtle hard-to-reproduce bugs, ≥~5 files). Let the LEDGER's cost/outcome columns confirm or refute this per project instead of bumping to `large` on reflex.

Keep all three **flat markdown and short**. This is the lightweight version of steering/standards files (Kiro/Agent OS/Cursor) and learnings (GSD) — no DB, no schema, no scoring.

---

## Project Map Bootstrap (Phase 0 offer · Phase 2 read)

Creates a local, evidence-backed project map so future feature/fix/audit runs start with a compact
understanding of what already exists. It is **recommended, skippable, and never a prerequisite**:
missing or stale project maps may reduce speed/context quality, but they do not block kimiflow.

**Source of truth:** `.kimiflow/project/` at the git root. This local folder is the durable machine
and human project-intelligence cache. Vault notes and repo docs are later publishing layers, not the
authoritative cache for Slice 1.

**Trigger:**
- `--project-map quick|standard|deep` → run the bootstrap/update at that depth and STOP after reporting paths.
- `--project-map skip` → record `project_map: skipped` in the active `STATE.md` and continue.
- Normal non-trivial run + missing `.kimiflow/project/INDEX.json` → offer once, with `standard`
  recommended. Decline/headless/no answer/skip → continue normally.
- `trivial` runs do not offer the bootstrap unless the user explicitly passes `--project-map`.

**Depths (token budget by design):**
| depth | purpose | reads |
|---|---|---|
| `quick` | fast orientation before immediate coding | manifests, top-level structure, entry points, tests, critical deps |
| `standard` | default/recommended project understanding | quick + central modules, architecture model, core flows, conventions, test strategy, open questions |
| `deep` | onboarding, major feature, audit/refactor prep | standard + module notes, critical flows, scalability/maintainability/security concerns |
| `skip` | no map this run | no project-map files written |

**Artifacts (Slice 1):**
```
.kimiflow/project/
  INDEX.json
  FACTS.jsonl
  CODEBASE.md
  ARCHITECTURE.md
  CONVENTIONS.md
  TESTING.md
  FLOWS.md
  OPEN-QUESTIONS.md
```

`INDEX.json` is the cheap first read for future runs. Minimum keys:
```json
{
  "schema_version": 1,
  "language": "de",
  "scan_depth": "standard",
  "baseline_commit": "cba4942",
  "created_at": "2026-06-25T00:00:00Z",
  "sections": {},
  "artifacts": {}
}
```
Use `NOT VERIFIED` for `baseline_commit` if there is no git repository. `sections` may be shallow in
Slice 1; Slice 2 adds per-section staleness and hashes.

**Section staleness (Slice 2):** each `sections.<name>` entry may carry the data that lets kimiflow
refresh only the changed areas:
```json
{
  "files": ["hooks/commit-secret-gate.sh"],
  "prefixes": ["hooks/"],
  "file_hashes": {
    "hooks/commit-secret-gate.sh": "sha256:<content-hash>"
  },
  "last_scanned_commit": "cba4942",
  "depends_on": ["git", "jq"],
  "status": "current"
}
```

Use stable section names that match how future work is scoped (`hooks`, `api`, `ui`, `testing`,
`architecture`, `flows`, etc.). `files` are exact load-bearing paths. `prefixes` let the status
resolver notice new files under known areas without reading the whole repo. `file_hashes` are content
hashes for exact files; a matching hash can make an uncommitted but already-refreshed working-tree file
current. `status` is one of `current|stale|potentially_stale|unknown`.

`FACTS.jsonl` is the compact evidence layer. One JSON object per line, stable English keys, concise
human text in the user's language:
```json
{"kind":"entrypoint","area":"hooks","path":"hooks/commit-secret-gate.sh","line":1,"summary":"Commit-Hygiene-Hook fuer git add/commit","confidence":"high","commit":"cba4942"}
```

**Human-readable language rule:** `CODEBASE.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`, `TESTING.md`,
`FLOWS.md`, `OPEN-QUESTIONS.md`, chat prompts, and summaries use the user's language. Preserve code
identifiers, paths, command names, schema keys, required tokens, and package names as-is.

**Mapper focuses (folded or delegated):**
- Tech: stack, package managers, dependencies, external integrations.
- Structure: directory layout, entry points, where to add common kinds of code.
- Architecture: components, responsibilities, data/control flow, invariants.
- Quality: conventions, test strategy, verification commands.
- Synthesis: writes/updates `INDEX.json`, compacts `FACTS.jsonl`, lists `OPEN-QUESTIONS.md`.

Each mapper writes directly to `.kimiflow/project/`; the orchestrator reports paths and does **not**
paste full artifacts back into chat. If subagents are unavailable, perform the same passes sequentially
using filesystem tools (`rg`, `find`, `git`, manifest reads). Do not read `.env` contents.

**Evidence rules:**
- Every architectural claim needs `file:line`, commit SHA, hash, or `NOT VERIFIED`.
- Prefer facts that future plans can reuse: where code lives, how to test, which pattern to match,
  what not to touch, and which unknowns remain.
- Do not store speculative improvements in Slice 1. Improve/refactoring lenses are Slice 3 and opt-in.

**Staleness helper (Slice 2):** `hooks/project-map-status.sh` is the mechanical source for map status.
Invoke it from the installed plugin root (Codex: set `KIMIFLOW_HOST=codex`, same root rule as other
helpers):

- `project-map-status.sh status` → emits `PROJECT_MAP<TAB>current|partially_stale|stale|unknown|missing`
  plus one `SECTION` line per section with `current|stale|potentially_stale|unknown`.
- `project-map-status.sh status --affected <path>` → same output, with `affected=yes/no` so Phase 2 can
  ask only about stale sections that matter to the current feature/fix.
- `project-map-status.sh coverage --affected <path>` → emits `PROJECT_MAP_COVERAGE` with mapped/unmapped
  affected paths and `phase2_depth=compressed|targeted|full`.
- `project-map-status.sh refresh --section <name>...` → after the mapper has refreshed the selected
  section artifacts, updates only those sections' `file_hashes`, `last_scanned_commit`, `status`, and
  `updated_at`.

Impact rules:
- Exact section file deleted or hash-mismatched → `stale`.
- Exact section file changed without a stored hash → `stale`.
- New or unmapped file under a section prefix → `potentially_stale`.
- Manifest/build config changed → `tech`/`stack`/`architecture`/`testing`/`quality`/`conventions`
  `potentially_stale`.
- Route/API/schema/migration path changed → `flows`/related flow section `stale`.
- Invalid/missing commit data with no usable hashes → `unknown`.

**Delta refresh (recommended, non-blocking):** If a normal feature/fix/audit touches a `stale` or
`potentially_stale` affected section, offer a targeted refresh before Phase 2. On accept, read only the
section's `files`/`prefixes`, update the relevant markdown/`FACTS.jsonl` entries, then run
`project-map-status.sh refresh --section <name>...`. On decline/headless/no answer/`unknown`, continue
with normal Phase-2 code exploration and note the status in `STATE.md`.

**Adaptive Phase-2 depth:** After likely affected paths are known, run
`project-map-status.sh coverage --affected <path>...`. Use `compressed` when affected paths are mapped and
current, `targeted` when the map covers them but the touched section is stale/unknown, and `full` when
affected paths are unmapped or the map is missing/invalid. This keeps map-backed runs cheap without trusting
outdated plans blindly.

**Focus menu (Slice 3):** accepted standalone map runs may ask what lens the user wants. Use the user's
language in the prompt and artifacts. Default/headless is `codebase+architecture`.

| focus | writes | notes |
|---|---|---|
| `codebase` | `CODEBASE.md`, `CONVENTIONS.md`, relevant `FACTS.jsonl` | where code lives, entry points, patterns |
| `architecture` | `ARCHITECTURE.md`, `FLOWS.md`, relevant `FACTS.jsonl` | components, responsibilities, flows, invariants |
| `improve` | `IMPROVEMENTS.md` | opt-in only; requires `codebase` + `architecture` evidence first |
| `docs` | `DOCS-PLAN.md` and optional repo docs | documentation plan/output from verified map facts |

Combined focuses are allowed (`codebase+architecture+docs`). Do not generate improvement ideas from a
cold start; first refresh the map sections needed to support them.

**Storage targets (Slice 3):** `.kimiflow/project/` is always written first and remains the source of
truth. Additional targets are publishing layers and require an explicit user choice:

1. `kimiflow` — write only `.kimiflow/project/` (default and headless fallback).
2. `kimiflow+vault` — also save curated notes to the optional Vault MCP using "Vault conventions".
3. `kimiflow+vault+repo-docs` — also write/update repo documentation after discovering existing docs.

No Vault MCP → skip Vault publishing, note it in `STATE.md`, keep local files. Repo docs are never
written by default and never written merely because `docs` focus was selected; the storage target must
include `repo-docs`. Preserve the user's language for human docs; keep schema keys, paths, commands and
identifiers as-is.

**Raw map vs. publishable docs:** never auto-commit `.kimiflow/project/`. Treat it as the local agent
cache and source of truth, not as repo documentation. Commit-capable output must be a curated derivative
under the repo's documentation structure (for example `docs/architecture.md`, `docs/codebase.md`,
`docs/testing.md`, or an ADR) and only after the user explicitly chooses a repo-doc storage target.

**Vault publishing:** save compact, curated project-intelligence notes, not raw dumps of every map file.
Prefer one index/MOC update plus notes such as "Project architecture", "Codebase map", and selected
improvement slices. Include links/references back to `.kimiflow/project/` artifacts and source evidence.
If the Vault already has project folders/templates, reuse them; otherwise follow "Vault conventions".

**Repo-doc publishing:** discover existing documentation first (`README`, `docs/`, ADRs, architecture
notes). Reuse/update the existing structure when clear; if no obvious place exists, propose paths before
writing. Good default targets are `docs/architecture.md`, `docs/codebase.md`, `docs/testing.md`, and a
small docs index, but only when they fit the repo. Repo docs must be verified against current map facts
and cite source paths/sections; no stale or `NOT VERIFIED` claim should be presented as fact.

**Repo-doc publish safety:** repo docs must be publish-safe by default, especially for public repos. They
may include architecture, module responsibilities, major flows, testing strategy, neutral constraints,
and decisions. They must NOT include concrete vulnerabilities, exploit paths, secret names/values,
credentials, private/local filesystem paths, vault references, raw improvement findings, or "this is
untested/easy to break here" detail. Keep those in `.kimiflow/project/OPEN-QUESTIONS.md`, optional local
`RISKS.md`/`SECURITY-NOTES.md`, or a private vault note. If the user explicitly asks to publish risk
context, write a sanitized version: high-level constraint, impact category, owner/next step if known, no
exploit recipe and no sensitive path/value.

Before any repo-doc commit, show the target paths and a bounded summary of what was included and what was
withheld as local/private. This is separate from the raw map report; do not stage `.kimiflow/project/`
unless the user explicitly overrides the local-cache policy after seeing the risk.

**Improve lens (opt-in):** write `.kimiflow/project/IMPROVEMENTS.md` only when the user selects or asks
for improvements/refactoring/scalability/maintainability/security ideas. Each item is a reviewable slice:
```
## Slice <n>: <short title>
Problem
Evidence
Value
Risk
Effort
Acceptance criteria
Do not touch
```
Translate those labels into the user's language in the actual artifact. Every slice needs evidence from `CODEBASE.md`,
`ARCHITECTURE.md`, `FLOWS.md`, `FACTS.jsonl`, or fresh `file:line` reads. Mark speculative items
`NOT VERIFIED` or omit them. Improvement slices are proposals only; they do not authorize code changes
without a later kimiflow feature/fix/audit run.

**Phase 2 consumption:** before fresh code exploration, read `INDEX.json`, the status line from
`project-map-status.sh`, and, once likely affected paths are known, the `PROJECT_MAP_COVERAGE` line. Then read
only the relevant `FACTS.jsonl` lines and markdown sections. If coverage says `compressed`, lean on the map and
verify only the touched code. If it says `targeted`, refresh/read only stale affected sections plus touched code.
If it says `full`, or the map is absent/skipped/invalid/stale-but-declined/unknown, continue with the existing
Phase 2 memory/codebase research path unchanged.

---

## Memory Router & Learning Loop (Phase 2 recall · Phase 7 learn)

The memory router is Kimiflow's bounded project brain. It makes memory useful without paying to reread the
whole codebase, old runs, or Vault on every request. It is local-first and optional-provider-aware: the repo-local
files work without any API key, subscription, or MCP server.

**Source of truth:** `.kimiflow/project/`.

```text
.kimiflow/project/
  MEMORY.md          small always-on summary, target 500-900 tokens
  USER.md            small local-only user/workflow profile
  LEARNINGS.jsonl    evidence-backed durable learnings
  USER.jsonl         evidence-backed user/workflow preferences
  MEMORY-INDEX.json  cheap lookup/curation index
  MEMORY-USAGE.json  local use_count/last_used plus bounded recall/history cost events
  MEMORY-ECONOMICS.jsonl project-local run-level directional token-efficiency estimates
  RECALL.sqlite      optional local FTS5 recall index
  RECALL.md          last/project recall log, or run-local recall when written there
  RUN-HISTORY.json   last on-demand run/session history snapshot
  RUN-HISTORY.md     readable run/session history snapshot
  VAULT-PROVIDER.json local optional Vault/Obsidian provider manifest
  VAULT-PREFETCH.md  bounded handoff for a connected Vault MCP
  VAULT-SYNC.md      bounded handoff for publish-safe learning sync to a Vault MCP
  PENDING-PROPOSALS.md review-only rule/skill proposal candidates
  PROPOSALS.jsonl    local proposal approval state
  SKILL-DRAFTS/      review-only skill/workflow draft notes
  LEARNINGS.archive.jsonl non-active archived learning rows after consolidation

.kimiflow/<slug>/
  RECALL.json        machine-readable recall snapshot when RECALL.md is written
  REVIEW.md          readable plan/code review summary, searched as local run history
  CODE-REVIEW.md     Phase-7 code-review summary, searched and eligible for compact pitfall extraction
  findings/*.md      canonical review-gate findings, searched locally but not promoted directly
  LEARNING-REVIEW.md required run-close artifact; recorded or explicitly skipped
  RUN-LIFECYCLE.json compact machine-readable run-close lifecycle summary
  RUN-LIFECYCLE.md   short human-readable run-close lifecycle summary
```

**Helper:** `hooks/memory-router.sh` is the mechanical source for local memory state, recall, classification,
recording, and curation. Invoke it from the installed plugin root (Codex: set `KIMIFLOW_HOST=codex`, same
plugin-root rule as other helpers):

```text
memory-router.sh status [--root <path>] [--pretty]
memory-router.sh recall --query <text>|--query-file <path> [--max <n>] [--write <path>]
memory-router.sh history [--query <text>|--query-file <path>] [--max <n>] [--write]
memory-router.sh metrics [--global] [--global-purge]
memory-router.sh classify --input <path>|--text <text>
memory-router.sh record --summary <text> --topic <topic> --evidence <ref>...
memory-router.sh review-run --run <path> [--write] [--skip <reason>]
memory-router.sh verify-run --run <path>
memory-router.sh curate [--write]
memory-router.sh index [--write]
memory-router.sh consolidate [--write]
memory-router.sh propose [--write] [--approve <id>] [--reject <id>] [--reason <why>] [--apply]
memory-router.sh provider <status|health|setup|detect|connect|configure|prefetch|sync> [--type <obsidian|none>] [--available <true|false>] [--path <path>] [--host <codex|claude|all>]
```

**Pre-run hydration:**
1. Run `memory-router.sh status`.
2. Read `MEMORY.md` only if present and under budget. If it is over budget, do not load it wholesale; offer or
   run curation when appropriate.
3. Run `memory-router.sh recall --query-file <INTENT|PROBLEM|AUDIT-INTENT> --write .kimiflow/<slug>/RECALL.md`
   before fresh code exploration.
4. Use recall hits to decide which `FACTS.jsonl` lines, map sections, old runs, Vault notes, or web sources are
   still needed. Missing memory never blocks the run.

**Retrieval order (token budget):**
1. Always-on project/user memory (`MEMORY.md` + optional `USER.md`, bounded).
2. Project map index and relevant facts/sections (`INDEX.json`, `FACTS.jsonl`, selected markdown).
3. Local FTS5 recall (`RECALL.sqlite`) when available, plus `LEARNINGS.jsonl`/`USER.jsonl` and old-run fallback hits, including local review summaries and canonical `findings/*.md`.
4. On-demand run/session history via `history` or `recall`'s `sources.history` hits.
5. Vault/claude-mem recall when connected and, for direct Vault access, direct MCP tools are ready.
6. Current-state primary-source check when the Current-State Gate requires it.
7. Web research only for uncovered, stale, or fast-moving external facts.

**Post-run learning loop (required before `Status: done`):** after verification/review and before closing
`STATE.md`, run `memory-router.sh review-run --run .kimiflow/<slug> --write`. It creates the run-local
`LEARNING-REVIEW.md`, appends durable rows to `LEARNINGS.jsonl`, refreshes bounded `MEMORY.md`, and refreshes
`MEMORY-INDEX.json`, optional `RECALL.sqlite`, lifecycle/usage metadata, and a compact run-local
`RUN-LIFECYCLE.json` / `RUN-LIFECYCLE.md` summary. It also refreshes local proposal state and returns a compact notification with pending,
approved, applied, and rejected proposal counts. Then run `memory-router.sh verify-run --run .kimiflow/<slug>`;
`CLOSED` blocks the run
from being marked done. Trivial runs may use `review-run --write --skip "<reason>"`, but the reason must be
written to `LEARNING-REVIEW.md` and verified. Summaries should be in the user's language.

**Learning quality gate:** `review-run --write` must fail closed before writing if a candidate is too short,
generic, missing verified evidence, a project-rule answer without a rule/convention signal, a pitfall without
an avoidance/risk signal, or an important decision without a concrete decision signal. The generated
`LEARNING-REVIEW.md` prints `Quality: passed` for accepted rows. Quality failures stay in the run and should
be fixed in the source artifact rather than promoted to memory.

**Structured learning extraction:** `review-run` prefers explicit labeled lines in run artifacts before falling
back to the first substantive line. Use labels such as `Learning:`, `Project rule confirmed:`, `Pitfall:`, and
`Decision:` under a short learning-summary section when a run artifact has intro/context text. Evidence points
to the actual label line, so later freshness checks validate the precise source that produced the learning.

**Source freshness gate:** every learning row written by `review-run` stores `evidence_fingerprints`
(repo-relative path + digest algorithm + digest + optional sha256 + status). Outside-repo evidence paths are
persisted only as `OUTSIDE_REPO`. `verify-run` recomputes fingerprints from the referenced evidence files. If
any recorded row points to missing/changed evidence, lacks fingerprints, or is no longer `current`,
`verify-run` returns `CLOSED` (for example `reason=evidence_stale`) and the run cannot be marked done until
the review is refreshed or explicitly skipped with a reason. When a refreshed learning replaces an older
fingerprint for the same evidence, the older row becomes `superseded`; recall returns only `current` rows.

**Memory write security gate:** every active row written through `record`/`review-run` is scanned for prompt
injection, instruction override, credential exfiltration, and hidden Unicode markers. Unsafe current rows fail
closed before they can enter always-on memory. Security-sensitive content may still be kept only as explicit,
non-current/local review material when an operator deliberately records it that way.

**User profile split:** `record --scope user` writes to `USER.jsonl` and refreshes bounded `USER.md`. User/workflow
preferences stay local-only and are never repo-doc candidates. Project facts stay in `LEARNINGS.jsonl`.

**Local run/session history:** `memory-router.sh history --query "<task>" --write` searches bounded old Kimiflow
run artifacts, including `REVIEW.md`, `CODE-REVIEW.md`, `ADVISORIES.md`, and canonical `findings/*.md`, then writes
`RUN-HISTORY.json` plus `RUN-HISTORY.md`. `recall` also reports `sources.history` hits, so Phase 2 can reuse old
plans/reviews without loading whole run folders. Raw findings stay local search material; they are not promoted
directly to repo docs or Vault.

**Usefulness, recall explanation, economics, and lifecycle metrics:** `memory-router.sh status` reports a compact
`.usefulness` section with exclusive hot/warm/cold/stale learning tiers plus promote/compress candidate counts;
stale rows are never promotion candidates. `recall --write` stores a small `explanation` object in the run-local
`RECALL.json` with included/omitted source states, hit counts, and reason codes so the agent can say why memory was
loaded without dumping memory contents. Persisted recall/history writes update `MEMORY-USAGE.json` with
`use_count`, `last_used_at`, and a bounded event log for recall/history writes: hit count, approximate output-token
cost, and the recalled keys. A run-local `RECALL.json` is written beside `RECALL.md` so `review-run --write` can
append one idempotent row to `MEMORY-ECONOMICS.jsonl`: always-on/user memory tokens, recall tokens, recall hits,
used hits, estimated avoided scan tokens from used hits, net estimate, estimated savings percent, result (`unknown|saving|neutral|waste`), and confidence.
This is directional telemetry, not billing truth; fewer than 8 runs report `insufficient_data`. `review-run --write`
also appends a **global local anonymous** row to `~/.kimiflow/metrics/token-economics.jsonl` unless
`KIMIFLOW_GLOBAL_METRICS=off`. That global row is a strict allowlist of numbers/enums plus salted hash IDs:
host, run type, project-size bucket, always-on tokens, recall tokens, hit counts, estimated avoided scan tokens,
net estimate, estimated savings percent, result, and confidence. It never stores code, prompts, Learnings text,
repo names, branch names, commit messages, file paths, Vault contents, or raw project identifiers; the salt stays
local on the user's machine. `memory-router.sh metrics` keeps legacy usage economics at top-level `.economics`,
returns run-economics at `.run_economics`, and returns the anonymous aggregate at `.global_efficiency`;
`metrics --global` prints only that aggregate, and `metrics --global-purge` deletes the local global JSONL file.
The launcher may show a single compact line such as `Effizienz: geschätzt 18% Token Savings · 12 Runs · Sicherheit niedrig`;
it must label the value as estimated and must not show it as proven truth.
older rows are normalized to the current `used_hit_count` heuristic when summaries are calculated so legacy
`recall_hit_count` estimates cannot inflate savings.
`curate --write` folds those metrics into
`MEMORY-INDEX.json` and reports lifecycle data such as stale learning candidates, cold/unused current rows, and the
configured `KIMIFLOW_LEARNING_STALE_AFTER_DAYS` window. `review-run --write` writes the run-local lifecycle summary
with learning status, memory update status, usefulness counts, directional economics, visible curation reasons,
provider sync readiness, proposal notification, and bounded next actions. It never writes external Vault notes
directly; provider sync/write remains an explicit handoff/direct-tool action. `MEMORY.md` stays always-on but use-aware:
it prefers frequently recalled, high-confidence, recent publish-safe learnings; cold rows stay searchable in
`LEARNINGS.jsonl`/`RECALL.sqlite` instead of being forced into every prompt.

**Local FTS5 recall:** `memory-router.sh index --write` builds `.kimiflow/project/RECALL.sqlite` when `sqlite3`
is available. It indexes bounded memory, user profile, current learnings, facts, and old run artifacts.
`curate --write` and `review-run --write` refresh it opportunistically. `recall` reports index hits without
requiring the index; missing SQLite falls back to JSONL and run-history matching.

**Optional Vault provider:** `memory-router.sh provider status` exposes the local provider manifest and
auto-detects a running Obsidian Local REST API on `https://127.0.0.1:27124` or `http://127.0.0.1:27123` when no
provider is configured. `provider health` returns the compact state machine: `not_detected`,
`detected_unconfigured`, `connected_local_only`, `authenticated`, or `auth_failed`, plus the recommended next
action. `provider setup --host <codex|claude|all>` returns a safe setup plan for the built-in Obsidian Local
REST API MCP endpoint (`/mcp/`) and recommends `hooks/vault-mcp-open-terminal.sh --host <host>` for interactive
macOS setup, with `hooks/vault-mcp-setup.sh --host <host> --interactive` as the plain-terminal fallback. That
launcher opens Terminal.app and runs `hooks/vault-mcp-setup.sh --interactive`, where the user pastes the key into
a hidden terminal prompt; Codex config can be written to user-level `~/.codex/config.toml`, Claude Code can use a
`headersHelper` script, and the key can live in macOS Keychain or the host environment instead of `.kimiflow/`.
Codex uses `bearer_token_env_var = "OBSIDIAN_API_KEY"`, while Claude Code uses a `headersHelper`
script created by `hooks/vault-mcp-setup.sh` outside the repo. The helper can read `OBSIDIAN_API_KEY` or macOS
Keychain service `kimiflow.obsidian.api-key` at connection time; it stores no token and refuses non-loopback URLs. `provider
detect` previews detection; `provider connect` (or `provider detect --write`) writes only
`.kimiflow/project/VAULT-PROVIDER.json`. It stores the local URL and detection metadata, never an Obsidian API
key or auth material. A local API key environment variable such as `OBSIDIAN_API_KEY`/
`KIMIFLOW_OBSIDIAN_API_KEY` can validate the loopback Local REST API, but direct Vault search/write is ready
only when `provider.health.direct_search_ready` / `provider.health.direct_write_ready` are true from an
authenticated MCP tool provider; token values are never written to `.kimiflow/` and are never probed against
non-loopback URLs. `provider configure --type obsidian --available true --path <vault>` remains the manual
fallback. `provider prefetch --query "<task>" --write` writes a bounded `VAULT-PREFETCH.md` handoff before
research and marks whether direct search is ready. `provider sync --write` writes
`.kimiflow/project/VAULT-SYNC.md`, a bounded review handoff of only current, non-private, non-security learnings
with freshly verified repo-relative evidence, and marks whether direct write is ready.
It exports at most `${KIMIFLOW_PROVIDER_SYNC_MAX:-20}` candidates per run, records only those exported IDs in the
manifest, and leaves omitted candidates pending so later `status` can report whether another Vault sync is
needed. The router never requires a paid provider or API key, never blocks when the provider is absent, and does
not patch skills or write external Vault notes blindly.

**Consolidation:** `memory-router.sh consolidate --write` archives superseded learning rows to
`LEARNINGS.archive.jsonl`, refreshes bounded memory/profile/index files, and never silently deletes data. It is
safe to preview without `--write`.

**Rule/skill proposal approval:** `memory-router.sh propose --write` derives review-only candidates from current,
evidence-backed learnings and writes `.kimiflow/project/PENDING-PROPOSALS.md` plus local state in
`.kimiflow/project/PROPOSALS.jsonl`. Approve or reject by learning/proposal id:
`propose --approve <id>`, `propose --reject <id> --reason "<why>"`. `propose --apply` appends approved
standard and decision candidates to local `.kimiflow/STANDARDS.md` and `.kimiflow/DECISIONS.md`. Approved
skill/workflow candidates create review-only drafts under `.kimiflow/project/SKILL-DRAFTS/`; Kimiflow does not
patch `SKILL.md`, `reference.md`, or repo docs automatically. Approve/apply revalidates evidence fingerprints fail-closed; stale candidates move to
`needs_revalidation` and must be refreshed through the learning review before they can be applied.

**Four-question schema:** every non-skipped review records only compact, verified answers to:

- `what_was_learned` — what reusable fact/pattern did this run prove?
- `which_project_rule_was_confirmed` — which project convention or workflow rule was confirmed?
- `which_trap_or_pitfall_appeared` — what mistake, risk, or surprise should future runs avoid?
- `which_decision_remains_important` — which decision still matters for future changes?

**Storage classification:** `review-run` uses the same classifier as `classify`/`record`:

- `run_only`: keep in the run folder; do not promote.
- `project_memory`: record locally with evidence and source commit.
- `vault`: save a curated note only if a Vault MCP is connected and the sensitivity allows it.
- `repo_doc_candidate`: do not write raw; include only through an explicit repo-doc action and publish-safe rules.
- `skip`: trivial, duplicate, speculative, or not evidence-backed.

**Sensitivity rules:**
- `public`: safe for repo docs if useful and verified.
- `normal`: OK for local memory and usually OK for Vault; repo docs require a publish-safe docs action.
- `private`: local or Vault only; sanitize local paths/user/customer details before broader reuse.
- `security`: local/sanitized only by default; never put concrete vulnerability details, exploit paths, secret names,
  token values, private paths, or raw risk findings into repo docs.

**Curator:** `memory-router.sh status` reports user-visible `curation.recommended` and `curation.reasons` such as `memory_over_budget`,
`stale_learnings`, `superseded_learnings`, `learning_lifecycle_review_due`, `memory_index_missing`, `recall_index_missing`,
`provider_sync_pending`, `provider_detected_unconfigured`, `provider_auth_required`, `provider_auth_failed`,
`learning_proposals_pending`, `learning_proposals_approved`,
or `learning_proposals_need_revalidation`. It also reports `curation.internal_recommended`, `silent_reasons`, and
`all_reasons`; `many_learnings` belongs there so agents can know the threshold fired without asking the user to act.
`review-run --write` refreshes the small always-on `MEMORY.md`; `curate --write` writes/refreshes
`MEMORY-INDEX.json`, lifecycle metrics, provider status, and the optional recall index. Row archival is explicit
through `consolidate --write`.

---

## Memory recall (Phase 2)

Before researching, recall locally first via `memory-router.sh recall`, then search whatever **optional memory
providers** are connected — recall beats re-research. Each provider is independent and **graceful**: present →
use, absent → note in STATE.md + continue (the skill runs identically either way; no provider is ever required).

- **Vault** (notes MCP, e.g. Obsidian) — curated research notes. Searched here; **also saved back** at
  Phase 2's end (see "Vault conventions" below).
- **claude-mem** (cross-session memory plugin, if its search MCP is present — e.g. `memory_search` /
  `observation_search` / `smart_search`) — past observations/decisions across sessions. **Search-only:**
  kimiflow recalls from it but does not write to it (claude-mem auto-captures sessions; verified findings
  are saved to the vault, not duplicated here).

Query the key terms from `INTENT.md`/`PROBLEM.md`/`AUDIT-INTENT.md` against each present provider; a
fresh, relevant hit from any **replaces** web research; re-research only a stale/uncovered hit, with a
different vector. **Detection is per-run, by tool availability** (don't hard-pin a brittle MCP name): a
later-added provider is used automatically on the **next run**, once its MCP is loaded in the session
(restart / `/reload-plugins` after install). None present → codebase + web, unchanged.

---

## Current-State Gate (Phase 2)

The current-state gate protects specs and plans from stale model knowledge when the work touches fast-moving
technology. It is **not** a web crawler and not a blanket research requirement. It is a small mechanical
resolver that tells the orchestrator when current primary-source evidence is required before finalizing a spec
or plan.

Helper:

```text
hooks/current-state-gate.sh assess --input <INTENT.md|PROBLEM.md|AUDIT-INTENT.md> [--pretty]
hooks/current-state-gate.sh verify --assessment .kimiflow/<slug>/CURRENT-STATE.json --recall <CURRENT-STATE.md|RECALL.md>
```

`assess` writes JSON with:

```json
{
  "schema_version": 1,
  "current_state_risk": "high",
  "current_state_reasons": ["host_or_plugin_surface"],
  "freshness_horizon": "30d",
  "required_source_types": ["official_docs", "release_notes", "schema_or_manifest"],
  "status": "required"
}
```

Risk behavior:

| risk | meaning | behavior |
|---|---|---|
| `low` | local code/docs work or stable project convention | no current-source check required |
| `medium` | library/API/tooling may have changed | use fresh memory/vault hit or a short primary-source check |
| `high` | host/plugin/hook/MCP/marketplace, security/auth/payments/privacy/deployment, external services | primary-source evidence required before spec/plan finalization |

High-risk examples: Codex or Claude Code plugin behavior, hooks, skills, MCP, marketplaces, new/changed SDKs,
auth/security/payment/privacy/deployment flows, App Store/marketplace/release mechanics, hosted APIs.

`verify` emits one stable line:

```text
CURRENT_STATE_GATE	OPEN|CLOSED	risk=<risk>	reason=<code>	detail=<detail>
```

For `high`, `OPEN` requires a recall artifact with:

```text
Status: checked

- source_type: official_docs
  source_url: https://example.com/current-doc
  summary: ...
```

Accepted primary `source_type` values are `official_docs`, `release_notes`, `schema_or_manifest`, and
`official_github`. If current sources contradict a stored learning, mark the stored learning `stale` or
`superseded` and do not use it as truth.

Gate rule: `CURRENT_STATE_GATE CLOSED` means do not finalize `RESEARCH.md`/`DIAGNOSIS.md`, `PLAN.md`, or a
spec. Research the current primary source, record the evidence in `CURRENT-STATE.md` or `RECALL.md`, then
run `verify` again.

---

## Vault conventions (Phase 2)

The vault is an **optional** notes MCP (e.g. Obsidian Local REST API's built-in `search_simple`, `vault_read`, `vault_append`/`vault_write`, or compatible legacy `obsidian_*` tools). **No vault MCP/auth → skip direct reads/writes, note the provider health in STATE.md, and continue with local handoffs** — the repo-local `.kimiflow/` memory still works. Notes follow the **user's language**, never a fixed one.

- **Health first.** Before direct Vault search/write, run `memory-router.sh provider health`. Use direct Vault
  search/write only when `provider.health.direct_search_ready` / `provider.health.direct_write_ready` are true.
  `authenticated` may mean the local REST API key validated successfully, not that a direct MCP tool is present.
  If it is `detected_unconfigured`, connect locally first; if `connected_local_only`, create
  `VAULT-PREFETCH.md`/`VAULT-SYNC.md` and offer the Terminal setup wizard from `provider setup`; if
  `auth_failed`, do not retry blindly.
- **Router decides what is vault-worthy.** Do not ask the user to babysit every write. Classify candidate
  learnings through "Memory Router & Learning Loop"; write to Vault automatically only when the classification
  is `vault`, the evidence is strong enough, and sensitivity is not `security`. Security-sensitive concrete
  detail stays local/sanitized unless the user explicitly asks for a sanitized private note.
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
- **Diverse lenses** (Phase 4): A = goal/completeness/measurability (goal-backward); B = security/edge/error/architecture/over-engineering. (Phase 7 has its own code-review ensemble below.)
- **Reviewers write findings to their own files — the gate counts them mechanically (closes self-report + silent-drop).** In Phase 4, each reviewer writes this round's findings to an append-only, orchestrator-immutable file `.kimiflow/<slug>/findings/r<N>-<lens>.md` — one canonical line per finding, at column 0, **no newline in the reason**:
  - `FINDING <SEVERITY> <ref> :: <one-line reason>` — `<SEVERITY>` is exactly one of `BLOCKER|HIGH|MEDIUM|LOW`; `<ref>` is `file:line` or `PLAN.md §section`. A reviewer that finds nothing writes the single sentinel line `NONE`.
  - Reviewers do NOT self-report a count; the orchestrator **reads** these files and never edits them — so no finding can be silently dropped or self-resolved.
- **Mechanical plan-blocker gate (Phase 4, before reviewers).** The orchestrator runs `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/plan-blocker-gate.sh .kimiflow/<slug>` before spawning plan reviewers. The script blocks generic executable-plan failures that reviewers should not have to rediscover: unresolved markers in `PLAN.md`/`ACCEPTANCE.md`, acceptance criteria without `AC-N`, criteria not referenced by `PLAN.md`, missing verification method, missing code/artifact path evidence, and missing affected-file/path declaration. `PLAN_BLOCKER_GATE	OPEN	blockers=0	reason=clean` is required before reviewer round 1. A CLOSED verdict returns to Phase 3; do not spend subagent budget on a plan that is not yet executable.
- **Gate count (mechanical, current round only) — delegated to the tested resolver.** The orchestrator runs `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/resolve-review-gate.sh .kimiflow/<slug>/findings --round <N> --expect <lensCSV>` (lens set from scope). The script is the **single source of truth**: it validates completeness + canonical grammar, counts open BLOCKER/HIGH, applies anti-oscillation, and echoes one TAB line `VERDICT⇥count⇥reason_code⇥detail`. **Fail-closed:** field 1 `OPEN` opens the gate only on `reason_code=clean`; any `CLOSED` keeps it closed. `reason_code` ∈ {clean,open-findings,incomplete,malformed,oscillation,reappeared,cap-reached} — `oscillation`/`reappeared`/`cap-reached` mean **stop + ask** (not "revise & continue"). It is language-agnostic (reads only `FINDING <SEVERITY> …`); unit-tested by `hooks/test-resolve-review-gate.sh`. The gate never reads `REVIEW.md`.
- **Resolution = non-recurrence, re-derived by the reviewer (closes self-attestation).** A finding counts as resolved only because the freshly re-spawned reviewer of the next round, re-reviewing the revised `PLAN.md`/diff, **no longer emits it**. The orchestrator never flips a finding's status by its own judgment and never writes a self-supplied "resolved".
- **Code-review ensemble (Phase 7): candidate-first, orchestrator-verified.** Phase 7 does not rely on one general reviewer. It builds one compact review packet, then sends focused candidates to multiple fresh-context lenses. `small` runs use at least two lenses; add the third for hooks/plugins/memory/launcher/API/contracts/multi-surface/high-risk changes. `large`/release-critical uses all three plus any enabled cross-family knob. Standard lenses:
  - `bug-regression`: logic, edge cases, acceptance drift, missing or weakened tests.
  - `failure-security`: input validation, secrets/privacy, paths, rollback/failure atomicity, partial writes.
  - `integration-contract`: host parity, plugin metadata, installed hooks, launcher/docs wiring, command/API/schema contracts.
  Each lens writes `.kimiflow/<slug>/code-review-candidates/r<N>-<lens>.md` with one line per issue: `CANDIDATE <SEVERITY> <ref> :: <claim> :: verify=<smallest check>`, or `NONE`. The orchestrator then verifies candidates through targeted reads/commands/reproduction, deduplicates them, records accepted/rejected/unverified candidates in `CODE-REVIEW.md`, and promotes only confirmed findings into `.kimiflow/<slug>/findings/r<N>-code-verified.md` using the canonical `FINDING <SEVERITY> <ref> :: <reason>` format. The resolver gate counts the promoted file, never raw candidates. This keeps the benefit of independent lenses without letting unverified false positives become blockers.
- **Code-review scope (Phase 7): correctness/requirements/security only, NOT style.** Also check: were tests weakened/deleted to go green? This is **mechanized** by `hooks/test-weakening-scan.sh` (deleted test files, added `.skip`/`xit`/`it.only`/`@Disabled`/`@pytest.mark.skip`/`t.Skip`/`assumeTrue(false)`, removed assertions) → `FLAG` advisories in `.kimiflow/<slug>/ADVISORIES.md`. **Advisories are non-gating** — a separate channel, never counted by the gate grep — and are **surfaced at the commit-gate**, where the human dismisses (legit refactor) or promotes them (fail-closed: an unresolved FLAG blocks the commit). The scan is a **minimum**: semantic weakening (changed expected values, loosened tolerances) is not detected.
- **Simplicity lens (Phase 7 — slimness as a counter-force, defined once; used folded or dedicated).** A reviewer dimension whose KPI is **"what can be deleted while the `ACCEPTANCE` tests stay green?"** — it makes slimness an active force, not a polite principle. It **FLAGs** (never a gate finding): a new abstraction/layer/option with **<2 real call sites and no written reason** (earn the abstraction: ≥2 callers OR a stated reason); a single-caller pass-through; error-handling for **impossible** states; speculative generality / config nobody asked for. For each, it **proposes the smaller version** (not just "this is complex"). Output rides the **advisory** channel → `.kimiflow/<slug>/ADVISORIES.md`, triaged at the commit-gate (dismiss-with-reason or adopt) — non-gating, so no false-positive thrash, but un-ignorable. Runs **only where a Phase-7 review runs (`small`/`large`)**; `trivial` (no loop, 1–2 files) is exempt. **Token-cheap by default:** at `small` it is **folded into the existing code-reviewer** (no new spawn); a **dedicated, blind prosecutor** runs at `large` (or via the tripwire below). **Size tripwire** — a *changed-line* heuristic that **complements** (does not redefine) the file-count/risk scope tiers: when `git diff --stat` shows a diff **much larger than its scope suggests** (rough guide: a `small` change >~150 changed lines), escalate to the dedicated prosecutor and raise a **STOP+justify** advisory. Orchestrator-read (`git diff --stat`) — no new hook.
- **Tests are evidence, not the boundary of truth.** Judge against **intent, acceptance, the diff, and actual behavior** — not the test suite alone. Green tests certify only what they assert, not correctness; a green suite may *support* a finding but never *refutes* one grounded in code/spec — "not covered by a test" / "no test fails" is **not** a counter-argument. An **untested real risk is still a finding**, and **missing coverage of a real risk can itself be a finding** — but anti-hallucination still binds: severity = provable impact (HIGH only with a reference + demonstrable impact; a coverage gap with no demonstrable risk → MEDIUM/LOW, or dropped). A finding of this kind names: **reference · violated expectation · impact · why tests miss it** (or why tests are irrelevant here).

**What the gate does and does NOT guarantee.** The gate is *sound over its inputs*: given the findings files, the verdict is mechanical and fail-closed — a `gate open` can't be self-reported past an open BLOCKER/HIGH. It does **not** certify the findings are *complete*: a too-lenient reviewer that misses a real blocker, or wrongly writes `NONE`, is not caught by the resolver. The de-biasers against *that* failure are reviewer independence, adversarial framing, and (large/critical) cross-family + multi-run review — not the resolver. The resolver hardens against self-report **inflation**; reviewer quality is what guards **completeness**.

**Anti-oscillation (blocker-aware):** compare the open BLOCKER/HIGH set round r→r+1. **Stop + ask with the gate CLOSED** if the open BLOCKER/HIGH count does not strictly decrease across the round, or a finding that had disappeared reappears. The 3-round cap is a hard backstop: the resolver emits `cap-reached` at **`round == cap`** (the cap is the round *limit* — round 3 under `--cap 3`, not round 4) when open findings remain → **stop + ask, gate CLOSED — never auto-proceed.**

**Knob — multi-run verdict (large/critical only):** run the promoted code-review verdict 3× and take the majority (single-judge verdicts have real run-to-run variance). Not for default `small`.

---

## Acceptance-criteria template (Phase 3)

Each criterion needs three parts plus a test link:

1. **EARS sentence:** Ubiquitous "The <system> shall <response>." · Event "When <trigger>, the <system> shall <response>." · State "While <precondition>, …" · Unwanted "If <trigger>, then …".
2. **Concrete example:** input → expected output (the oracle — unambiguous pass/fail).
3. **Verification method** (exactly one): automated test · command + expected exit code · file/fixture diff · screenshot compare · verifier agent (last resort).
4. **Test link:** `AC-N → test_name` — the named test that proves it. This makes the test suite the per-feature drift detector (the one spec-sync mechanism with long-term evidence).

Properties: **observable**, **binary** (pass/fail, not "almost"), **bounded**. Reject criteria without a clean method. **Lint** for vague terms ("fast", "robust", "user-friendly" → quantify) and missing **error/edge** criteria. Trace each to `INTENT.md`/`PROBLEM.md`.

**Coverage check (Phase 4, before the gate):** every criterion → a plan task AND a test; no orphan task without a criterion. `plan-blocker-gate.sh` catches common unmapped/missing-verification cases before reviewers; remaining gaps are findings — fix the plan first.

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
- **Fix mode:** update `BUG-REPRO.md`, then run `red-green-gate.sh`; a `CLOSED` verdict means the fix is not verified enough to review, finish, or learn from.
- **Local LSP diagnostics advisory:** run `hooks/lsp-diagnostics.sh` after code changes when available. It chooses one untracked local `.kimiflow/lsp-diagnostics` command first; otherwise it runs a bounded set of existing project scripts (`typecheck`, `lint`) and common local diagnostics (`tsc`, `pyright`, `ruff`, `mypy`). Each failed command emits a compact `FLAG` classified as `changed-files`, `project-wide`, or `unknown-scope`. It never installs tools, rejects free-form CLI commands, ignores tracked config for safety, and skips cleanly when nothing suitable is on PATH.
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
- **Deletions are caller-verified (mechanical).** Removing code requires a recorded proof of **zero live callers** — a `grep`/search over the repo's source (and tests) that returns none, attached to the change. A deletion without that proof is a **code-review BLOCKER**. If something survives the grep but a reviewer judges it load-bearing, record it on a short **do-NOT-touch** list with the reason instead of deleting (anti-hallucination for deletions — a wrong "dead" claim is worse than a missed one).

---

## Commit hygiene (Phase 7 commit-gate)

Before the commit, after explicit user OK:

1. Read `git status` + `git diff --staged` before composing the message.
2. **Stage only explicitly named paths** — no `git add -A` / `git add .`.
3. **Never** stage `.env`, keys, tokens, credentials — on suspicion, stop and ask.
4. If the project has tests and the change touches code: run them. Red → STOP, no commit.
5. **No co-author trailer, no "Generated with" line, no AI attribution.**
6. Commit message: terse, what & why.

**Mechanized (kimiflow repos only):** points 2–3 are also enforced by the `commit-secret-gate` PreToolUse hook — it **blocks** `git add -A`/`.` and any `git commit` whose staged paths — or, for `git commit -a`/`--all`, the tracked working-tree paths it would auto-stage — match secret patterns (`.env`/`.envrc` incl. `*.env` suffixes like `prod.env`, `*.pem/.key/.p12/.pfx/.asc`, private SSH keys `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519` (not `.pub`), `.npmrc`/`.pypirc`, `secret(s)`/`credential(s)`/`api_key`/`access_token`/`auth_token` in a path; a combined `git add <secret> && git commit` is also caught). In Claude Code it ships through the plugin hooks; in Codex it is installed through `hooks/install-codex-hooks.sh`, which writes stable wrappers into `${CODEX_HOME:-~/.codex}/hooks` and pins `KIMIFLOW_PLUGIN_ROOT` back to the plugin checkout. **Skill-only use loads no hook.** The hook is **auto-active only where a `.kimiflow/` directory exists at the git root** (kimiflow creates one in Phase 0), so it never polices unrelated repos — and commits in repos without `.kimiflow/` are knowingly unprotected. The pattern list is a **minimum deny-list**, not exhaustive; false positives on filenames merely containing those words are possible (resolve by committing the safe file by name from outside a kimiflow run).

**Scope — filename/path hygiene, NOT secret-in-source detection.** The gate matches secret-looking **paths**, never file **contents**: a secret pasted into source (e.g. `const API_KEY = "sk-…"` in `app.js`) passes through untouched. For in-source secrets, pair it with a dedicated **content scanner** — `gitleaks` (regex + Shannon entropy) or `trufflehog` (per-credential detectors + live verification); the two are complementary, not a substitute for this path-level gate. kimiflow ships an **optional advisory wrapper** `hooks/secret-content-scan.sh` (run in Phase 7) that invokes `gitleaks` — else `trufflehog` — over the **staged content** when one is installed and routes any finding to `ADVISORIES.md` for commit-gate triage; it is **non-gating** and skips gracefully (a STDERR note) when no scanner is present, so it never grants a false sense of coverage. Four further boundaries: (1) the precise (jq) path only governs `git` at a **command position** (line start, or after `;`/`&`/`|`) — `sudo git …`, `env X=y git …`, a path-prefixed `/usr/bin/git …`, and a `command`/`builtin`/`exec git …` wrapper are out of scope by design (a deliberate non-standard invocation is not the gate's threat model — it is accident-hygiene). A global **`git -C <path>` IS honored**, though: the gate resolves the target repo via git's own cumulative `-C` (so `git -C <repo> commit` run from another cwd is scoped to `<repo>`, not the tool cwd), for **unquoted, space-free** `-C` paths — a quoted `-C` path containing a space (`git -C "my repo"`) stays a residual; (2) the **jq-less fallback is intentionally blunt** — unable to extract the command, it greps the raw payload and may **over-block** a benign command that merely mentions git (e.g. `echo "git commit later"`). Over-blocking is the safe failure for a fail-closed gate; install `jq` for the precise path rather than expecting a regex to parse the shell; (3) an explicit **pathspec commit** (`git commit <path>`, e.g. `git commit .env -m …`) of an **already-tracked** secret-looking file is **not** covered — it stages the named path at commit time, and reliably parsing a pathspec out of a shell string needs an AST, not a regex; (4) an **escaped quote** inside the `-m` message (`git commit -m "a\"; b" -a`) can desync the naive quote-strip and re-hide a `;`/`&`/`|` separator before the `-a` — also out of scope (same root: regex ≠ shell parser). (The `git commit -a`/`--all` form — including bundled short flags where `a` is not first (`-am`/`-vam`/`-qam`), and a metachar **hidden in a quoted message** (`-m "a; b" -a`) or behind a **backslash-newline continuation** — **is** covered: the command is line-joined and unquoted *before* the `;`/`&`/`|` split, then the tracked working-tree is scanned. That flag detection is best-effort over the unparsed string: it matches `a` before any value-taking short option, so `-ma` (a message) and `--allow-empty` are correctly ignored, while an unquoted `-a` token inside a commit message would over-block — the safe failure.) **Bottom line: treat the gate as a hygiene backstop, not complete secret protection** — real coverage is `.gitignore` discipline + a content scanner (gitleaks/trufflehog) + not tracking secrets in the first place.

**Local diagnostics / LSP advisory.** `hooks/lsp-diagnostics.sh` is the local-first code diagnostics companion. It is advisory-only like the secret content scanner: existing local tools can produce `FLAG`s, no tool means `SKIPPED`, and nothing is installed. Selection order: one untracked `.kimiflow/lsp-diagnostics` command, else a bounded set of package `typecheck`, package `lint`, `tsc --noEmit`, `pyright`, `ruff check .`, `mypy .` (default max 3 commands via `KIMIFLOW_LSP_MAX_COMMANDS`). Free-form CLI commands are not accepted; custom diagnostics must live in the untracked local config file. A tracked `.kimiflow/lsp-diagnostics` is ignored for safety because it would otherwise execute a command from a cloned repo. Failed diagnostics are classified as `changed-files` when output references touched paths, `project-wide` when touched files exist but are not referenced, and `unknown-scope` when no changed-file basis exists. Treat its output as a cheap extra signal before review/commit, not a substitute for acceptance tests or manual app verification.
