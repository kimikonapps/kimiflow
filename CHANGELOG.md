# Changelog

Notable changes to **kimiflow**. Versions track `.claude-plugin/plugin.json`.

## 0.1.12

A self-applied claim/evidence remediation (kimiflow's own `evidence-before-assertion` standard turned
on its own docs) + eval-suite hardening + secret-gate scoping. **Docs / evals / tests only — engine
behavior unchanged; `secret_re` and all gate logic untouched.**

### Added
- **`COMPATIBILITY.md`** — every Claude Code primitive kimiflow depends on (PreToolUse/Stop hooks,
  `${CLAUDE_PLUGIN_ROOT}`/`${CLAUDE_SKILL_DIR}`, `TaskCreate`/`TaskUpdate`, subagent types,
  `disable-model-invocation`, the manifests), classed load-bearing vs graceful, with a version-bump
  smoke checklist. Last verified: Claude Code 2.1.186.
- **Eval suite expanded to 10 scenarios** — `07-scope-gate` (both directions),
  `08-advisory-triage-failclosed`, `09-headless-build-gate`, `10-terse-output`; an open-ended tier
  beside the MCQ tier; and a run procedure requiring n≥3 per pass + a CLAUDE.md-free / attribution-
  forcing setup (addresses the ambient-CLAUDE.md confound in the method, not just the prose).
- **`evals/outcomes.md`** — an honest, currently-empty log for outcome quality (kimiflow vs a plain
  session); field notes, not a benchmark. Nothing is cited from it while empty.

### Changed
- **Outward claims aligned with evidence.** The 0.1.11 "6/6 held" line now carries the
  ambient-CLAUDE.md confound caveat (only 3/6 cleanly attributable). README weakened "enforced, not
  self-reported" → "a `done` self-report can't inflate past open blockers"; `reference.md` "Review
  rubric" now states what the gate does **not** guarantee (sound over its inputs, not a completeness
  proof). Added a "Why kimiflow over plan-mode + a `CLAUDE.md`" section.
- **Scenario pass-criteria tightened** — every scenario now requires the citation to name its
  `SKILL.md`/`reference.md` location; the cartoonish distractors in 01/02 became tempting near-misses.
- **`commit-secret-gate` claim scoped to path-hygiene** — README / `reference.md` now state it is
  filename/path hygiene, **not** secret-in-source detection, and point to gitleaks/trufflehog for
  in-source secrets. Doc pattern list synced to the regex (`.asc`, the four concrete SSH keytypes,
  `.p12`/`.pfx`). The blunt no-jq fallback's intentional over-block is documented and locked by tests.
- **`LEDGER.md` schema** gains approx-token-cost + post-commit-outcome columns (a cheap ROI
  instrument) and an honest "when is `large` worth it?" note.

## 0.1.11

### Added
- **Behavioral-eval tier (`evals/`, out-of-CI, on-demand).** A subagent pressure-test suite for the
  six highest-stakes gates (commit-gate, diagnosis-before-fix, plan-gate cap/anti-oscillation,
  deletion caller-verification, evidence-before-assertion, anti-hallucination). Each scenario loads a
  fresh subagent with the real deployed skill and a multi-pressure situation, then checks whether the
  gate holds and is cited — the `testing-skills-with-subagents` (TDD-for-process-docs) tier. LLM-judged
  and variant by design, so never wired into CI; a one-line Scaling-knobs pointer makes it
  discoverable. First run (2026-06-23): 6/6 held, but only 3/6 (03/05/06) are cleanly attributable
  to kimiflow's own text — 01/02/04 were confounded by the ambient global `CLAUDE.md` (see
  `evals/README.md` → "Known limitation"). Treat as a smoke pass, not a robustness proof.

## 0.1.10

### Added
- **Opt-in `🧭 Explore` phase (`--explore`).** A divergent front-end that runs *before* the
  convergent Phase 1 Clarify: 2–3 codebase-grounded explorer subagents each propose a **distinct
  direction** (minimal / robust / sideways), the orchestrator synthesizes a terse menu, and the user
  picks one — which then seeds Clarify. Forced with `/kimiflow --explore <idea>`; otherwise kimiflow
  offers once on an open-ended request (decline / headless → normal routing, never blocks). Pick →
  continue into the loop, or stop with an `EXPLORE.md` option memo and `--resume` later. Feature-mode
  only (a fix/cleanup that surfaces → suggests `--fix`/`--audit`). Purely additive — non-explore runs
  are behaviorally unchanged; no new hook/script (the pick is a human gate).

## 0.1.9

### Changed
- **Slimmed `SKILL.md` to a thinner state-machine spine.** The always-loaded orchestrator spec was
  compressed in 12 spots where the detail already lives in `reference.md` — in-line explanation
  replaced with terse imperatives + section pointers (Modes, Core principles, Phase 0/2/4/7).
  **Behavior-preserving:** every gate, threshold, transition and mechanical contract is kept inline
  or reachable via a working `reference.md` pointer (verified clause-by-clause by an independent
  adversarial audit; all pointers resolve; `reference.md` unchanged; hook tests green). ≈−8% bytes /
  −9% words off the per-run orchestrator context.

## 0.1.8

### Fixed
- **commit-secret-gate — bulk-add false positive across subcommands.** The bulk-add guard scanned
  the whole compound command for a bare `.` / `-A` / `--all`, so a named `git add foo` combined with
  a `.` pathspec in a DIFFERENT subcommand (e.g. `git add foo && git grep -- .`) was wrongly refused
  as `git add .`. The check is now scoped to the `git add` invocation's own args (segment after `add`,
  bounded by `;&|`). Genuine bulk adds (`git add .`, `-A`, `--all`, `git add foo .`) stay blocked;
  tests added both ways.

## 0.1.7

### Added
- **Mechanized review gate.** The binary Phase-4 / Phase-7 review decision is now a single tested,
  deterministic resolver `hooks/resolve-review-gate.sh` instead of a prose-instructed `grep | wc -l`.
  It validates findings completeness + canonical grammar, counts open BLOCKER/HIGH, and applies
  anti-oscillation (`cap → oscillation → reappearance`), echoing one stable machine line
  `VERDICT⇥count⇥reason_code⇥detail`. **Fail-closed** on any incompleteness / malformation / misuse
  (never a false `OPEN`); **language-agnostic** — operates only on the `FINDING <SEVERITY> <ref> :: <reason>`
  abstraction (arbitrary UTF-8 refs/reasons, no source or per-language logic). Unit-tested (22 cases),
  wired into CI as a hard gate.

### Changed
- `reference.md` "Review rubric" and `SKILL.md` (Phase 4 / Phase 7) now delegate the gate count to
  `resolve-review-gate.sh` (the single source of truth); gate semantics unchanged (mechanized 1:1).

## 0.1.6

### Fixed
- **commit-secret-gate — compound code filenames:** the keyword deny-list flagged a secret-word
  wherever it was bounded by `[/._-]`, so the gate's own files (`commit-secret-gate.sh`,
  `test-commit-secret-gate.sh`) and source files like `secret-manager.ts` were refused — a false
  positive the "commit from outside a run" hint couldn't resolve. The **trailing** word-boundary now
  excludes `-`: a secret-word is still caught as a path's trailing token (`client-secret.txt`,
  `aws-credentials.yml`, `prod-secret.json`) but no longer mid-name. Leading `-` kept; tests added
  for both directions.

### Changed
- **resolve-verbosity:** dropped the unused standalone `origin` mode — it was documented and
  unit-tested but never invoked by the orchestrator (`onboard-check` already encapsulates the sole
  origin-based decision). `get`/`onboard-check`/`set` unchanged; an `origin` arg now degrades to `get`.
- Renamed leftover internal `flow_root()` → `git_root()` in commit-secret-gate (flow→kimiflow rename).

### Removed
- `design/` plans/specs for already-shipped features — trims the published repo (git history retains them).

## 0.1.5

### Fixed
- **commit-secret-gate — suffix-style `.env`:** the secret pattern matched only dotfile `.env`;
  `prod.env`/`dev.env`/`.envrc`-style names now match too.
- **commit-secret-gate — combined add+commit:** a `git add <secret> && git commit` in one command
  now has its add-targets scanned, not just the index.
- **commit-secret-gate — no-jq fail-closed:** the jq-less detection was quote-fragile, so
  `git -C "…" commit` / `git -c k="v" commit` slipped through; now denied (quote-robust).
- **test-gate — no-jq loop-break:** the `stop_hook_active` break now works without jq (grep
  fallback), so a red marker can no longer re-block forever; a stderr hint recommends jq.
- SKILL.md YAML frontmatter (`description` quoted) — fixes the GitHub render error.

### Added
- Unit tests for `commit-secret-gate`, `test-gate`, and the test-weakening scanner, all wired into
  CI as hard gates. CI now also validates `marketplace.json`.

### Changed
- Hooks documented as **plugin-mode only**; secret-pattern wording corrected (incl. `.env`/`.envrc`,
  `access_token`/`auth_token`).
- Removed build-time external-toolchain references from the published repo; design artifacts moved
  to `design/`. The audit-mode lens is described in kimiflow's own terms.

## 0.1.4

### Added
- **Audit / cleanup mode** — a third mode (`/kimiflow --audit <path>` or auto-detected) that runs an
  existence-first cleanup lens over a bounded target: finds tagged slices (`yagni`/`delete`/`shrink`/`stdlib`) with
  repo-wide caller-greps and git-history-freshness, presents them for approval (Phase-4 summary gate),
  then executes one slice = one commit with a per-slice verify gate. Caller-grep is a documented
  MINIMUM; tests + do-NOT-touch + adversarial "refute the cut" verification are the backstop. Engine unchanged.

## 0.1.3

### Added
- **Pre-build summary gate** — at the end of Phase 4 (after the plan-gate opens), kimiflow
  prints a structured summary (problem/goal · decisions · plan · tests/acceptance · risks +
  artifact paths) and **waits for your OK** before implementing. Project-local toggle
  `.kimiflow/build-gate` (`on`/`off`, default `on`), set via `--settings`; never global
  (self-contained rule). Control-flow only — the engine is unchanged. Toggle resolved by the
  unit-tested `hooks/resolve-build-gate.sh`.
- **Native phase task-list** — Phase 0 creates a glance widget (`TaskCreate`/`TaskUpdate`) of
  the phases being run; complements `STATE.md` and the colored markers, replaces narrated status.

### Changed
- **Deletions are now caller-verified** — removing code requires a recorded zero-caller proof
  (`grep`); an unproven deletion is a code-review BLOCKER. Load-bearing-but-removable-looking code
  goes on a do-NOT-touch list instead.
- **Plan tasks carry a `Consumes:`/`Produces:` interface block** for parallel/worktree implementers.
- **`large`-scope plans record 2–3 considered alternatives** + the selecting trade-off.

## 0.1.2

### Added
- **MIT license** — a `LICENSE` file + `license` field in the manifest, so the README's
  "anyone can install/fork" is actually covered (previously de-facto all-rights-reserved).
- **CI runs the unit tests** — `hooks/test-resolve-verbosity.sh` is now a hard gate in CI
  (was `bash -n` + JSON validation + advisory shellcheck only; the green tests were never run).
- **Artifact-economy rule** — on-disk artifacts (re-read by every subagent each round, the
  dominant token cost) are written information-dense; density never trades away rigor.

### Changed
- **First-run onboarding is now mechanical** — `resolve-verbosity.sh onboard-check` decides
  `ASK`/`SKIP` in the unit-tested script (`ASK` iff no project/global config and no flag), so it
  fires reliably on a fresh project and never nags a configured one. 0.1.1's prompt was
  orchestrator-judged and could be silently skipped.
- **Stale `flow` → `kimiflow`** in the hooks' headers and operator-visible deny/block messages.
- **SKILL.md / reference.md prose compacted** — decoration removed, telegraphic phrasing;
  every rule, threshold, path, and acceptance-criteria precision unchanged.

## 0.1.1

### Added
- **Display verbosity** — `quiet` / `balanced` / `verbose` levels that change **only** visible
  output; the engine (gates, artifacts, evidence, subagents, thresholds) is identical at every
  level. One-off `--quiet` / `--verbose`, setter `--set-verbosity`, a `--settings` dialog
  (level + scope), and a one-time first-run prompt (headless/skip → `balanced`, no block).
  Precedence `flag > project > global > balanced`, resolved by a unit-tested helper
  (`hooks/resolve-verbosity.sh`). Only verbosity may live globally (`~/.claude/kimiflow/verbosity`).

### Changed
- **State dir renamed `.flow/` → `.kimiflow/`** (self-documenting).
- **Fix-mode research** now names `WebSearch` / context7 / `WebFetch` explicitly (parity with the
  feature path).
- **Vault research is freshness-aware** — a hit is weighed by its `date:`; a fresh hit that
  answers the question replaces web research, and re-search uses a **different search vector**
  rather than repeating a prior query.

## 0.1.0 — Initial release

> Pre-1.0: early and evolving — interfaces and gate details may change between 0.x versions.

A user-invoked Claude Code skill: a disciplined feature & bug-fix loop with real,
mechanical quality gates.

### Added
- **8-phase loop** — scope-gate → clarify → understand/diagnose → plan → plan-gate →
  implement → verify → code-review/commit — with **colored phase markers**
  (⚪🔵🟣⚫🟡🟠🟤🟢) so a run reads at a glance in Claude Code.
- **Binary gates, no numeric score.** Reviewers write structured findings to per-round,
  orchestrator-immutable files; the gate counts open BLOCKER/HIGH **mechanically** and
  **fails closed** on missing/empty/malformed input — no self-reported counts, no re-count.
- **Fix mode** — reproduce, prove the root cause (`file:line`), and research the correct
  fix *before* fixing.
- **Self-contained** — every gate/threshold lives in the skill + `reference.md`, never in
  a personal/global `CLAUDE.md`.
- **Bundled hooks** (active only in kimiflow repos — a `.kimiflow/` dir at the git root):
  - `commit-secret-gate` (PreToolUse) — blocks staged secrets and bulk `git add -A`/`.`;
    **fails closed without `jq`**.
  - `test-gate` (opt-in Stop hook) — blocks finishing on red tests; runs **only a local,
    untracked marker** (a committed `.kimiflow/test-gate` is refused — no drive-by `eval`).
  - `test-weakening-scan` (advisory) — flags deleted tests / added skips / removed
    assertions to a non-gating channel, surfaced at the commit-gate.

### Requirements
- `jq` on `PATH` (used by the hooks).

### Notes
- Renamed from `claude-flow` to **kimiflow** to de-collide from `ruvnet/claude-flow`.
