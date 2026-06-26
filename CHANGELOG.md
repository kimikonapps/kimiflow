# Changelog

Notable changes to **kimiflow**. Versions track `.claude-plugin/plugin.json`.

## Unreleased

### Added
- Added `hooks/release.sh`, a maintainer release helper that checks repository state, bumps plugin manifest
  versions, updates changelog/compatibility metadata, runs release checks, commits, tags, pushes, and creates
  or updates the matching GitHub Release.
- Added `docs/history-reset-checklist.md`, a researched manual runbook for the planned 0.2 public-history reset.

## 0.1.38

Ship **memory-economics normalization and commit-hook hardening**.

### Added
- README now documents the repository structure in English and German, including which generated `.kimiflow/`
  project-intelligence files should stay local by default.

### Fixed
- `memory-router.sh metrics` now normalizes older run-economics rows to the current `used_hit_count` heuristic
  so legacy `recall_hit_count` estimates cannot inflate token-savings totals.
- `commit-secret-gate.sh` now fails closed for malformed git-like hook payloads inside Kimiflow repositories,
  while preserving no-op behavior for malformed payloads outside `.kimiflow/` scope.

## 0.1.37

Ship **Memory Economics and searchable review findings** for the Kimiflow learning loop.

### Added
- `memory-router.sh recall --write` now writes a run-local `RECALL.json` beside `RECALL.md`.
- `review-run --write` now records directional run-level token-efficiency telemetry in
  `.kimiflow/project/MEMORY-ECONOMICS.jsonl`.
- `status`, `metrics`, and `curate --write` now expose memory-economics summaries while preserving legacy
  usage-economics fields.
- Local run history and FTS recall now include review summaries and canonical `.kimiflow/<slug>/findings/*.md`
  so Kimiflow can recall prior review findings after the gate closes.

### Fixed
- Token-savings estimates now use `used_hit_count` instead of all recall hits, avoiding inflated savings claims.
- Generic `REVIEW.md` prose stays searchable local run history but is no longer promoted into durable
  `LEARNINGS.jsonl` entries.
- Docs and plugin metadata now point installs at the `kimikonapps/kimiflow` Git marketplace.

## 0.1.36

Ship **frictionless Obsidian Vault MCP setup** for Codex and Claude Code.

### Added
- Memory Router provider status now auto-detects a running Obsidian Local REST API on the common local ports
  and reports `provider_detected_unconfigured` until the user connects it.
- Added `provider detect` / `provider connect` for a frictionless local Obsidian setup that writes only
  `.kimiflow/project/VAULT-PROVIDER.json` and never stores an Obsidian API key.
- Added `provider health` with `detected_unconfigured`, `connected_local_only`, `authenticated`, and `auth_failed`
  states, plus auth-ready capabilities without storing API keys in `.kimiflow/`.
- Added `provider setup` and `hooks/vault-mcp-setup.sh` for safe Obsidian Local REST API MCP setup:
  Codex uses `bearer_token_env_var = "OBSIDIAN_API_KEY"`, Claude Code uses `headersHelper`, and non-loopback
  URLs are refused before any token-bearing setup is printed.
- Added `hooks/vault-mcp-open-terminal.sh`, an interactive macOS Terminal wizard that writes host config, stores the
  API key in Keychain, verifies local auth, and keeps the key out of chat and `.kimiflow/`.
- Provider prefetch/sync handoffs now include health/auth readiness, so direct Vault search/write is used only
  when authenticated and otherwise stays as reviewable local `VAULT-PREFETCH.md` / `VAULT-SYNC.md`.
- Launcher/README/skill docs now describe the V2 flow: detect Obsidian, connect locally, check health/auth, print
  host-owned MCP setup, then use direct Vault search/write only when authenticated.

## 0.1.35

Ship **bounded Vault sync handoffs** for the Memory Provider lifecycle.

### Added
- Memory Router now supports optional local FTS5 recall via `.kimiflow/project/RECALL.sqlite` and `index --write`.
- Added `history --query ... --write` for bounded old-run/session recall snapshots in `RUN-HISTORY.json` /
  `RUN-HISTORY.md`.
- Added persisted recall/history usage metrics in `MEMORY-USAGE.json` plus lifecycle curation metadata in
  `MEMORY-INDEX.json`.
- Added `provider status|configure|prefetch` for local optional Vault/Obsidian provider manifests and bounded
  `VAULT-PREFETCH.md` handoffs.
- Added `record --scope user` with local-only `USER.jsonl` / `USER.md` profile memory.
- Added `consolidate --write` to archive superseded learning rows without silent deletion.
- Added `propose --write` to generate review-only rule/skill proposals from evidence-backed learnings.
- Approved skill/workflow proposals now create review-only drafts under `.kimiflow/project/SKILL-DRAFTS/` instead
  of patching skill files automatically.
- Added `provider sync --write` to create `.kimiflow/project/VAULT-SYNC.md` from current, non-private,
  non-security learnings with freshly verified repo-relative evidence.
- Launcher and memory status now report `provider.sync` and `provider_sync_pending` so omitted Vault sync
  candidates stay visible until exported.

### Fixed
- Refreshed learning rows now supersede older rows with changed evidence fingerprints, and recall returns only
  `current` learnings.
- Outside-repo evidence paths are sanitized to `OUTSIDE_REPO` before persistence.
- Evidence fingerprints now store an explicit digest algorithm and digest; `sha256` is populated only when the
  digest is actually SHA-256.
- Active memory writes are now blocked when they contain prompt-injection, hidden-instruction, or credential
  exfiltration patterns.
- Provider sync recomputes evidence fingerprints before export so stale or changed evidence rows are not written
  to the Vault handoff.
- Vault sync handoffs are capped by `${KIMIFLOW_PROVIDER_SYNC_MAX:-20}`, and only exported IDs are marked synced.

## 0.1.34

Add **quality and source-freshness gates** to the Learning Loop.

### Added
- `review-run` now blocks low-quality learning candidates before writing: too short, generic, missing verified
  evidence, decisions without a decision, rules without a rule, or pitfalls without an avoidance signal.
- Learning rows now include `evidence_fingerprints` so `verify-run` can detect when source evidence changed
  after the run-close review.
- `verify-run` now returns `CLOSED reason=evidence_stale` when a recorded learning's evidence file changed,
  is missing, or lacks a current fingerprint.
- Memory-router tests cover evidence fingerprints, stale evidence, refresh after evidence changes, and
  low-quality learning rejection.

## 0.1.33

Harden the **Learning Loop close gate** after code review.

### Fixed
- `memory-router.sh verify-run` now validates every `Recorded: learn_*` ID against current rows in
  `.kimiflow/project/LEARNINGS.jsonl` instead of trusting the review markdown alone.
- Learning recording no longer reuses stale or superseded rows as proof of a fresh completed run; repeated
  proof appends a new current row while current duplicates remain idempotent.
- Memory-router tests cover forged/missing recorded IDs and stale-learning reconfirmation.

## 0.1.32

Close the **Learning Loop** mechanically for completed Kimiflow runs.

### Added
- `memory-router.sh review-run` writes `.kimiflow/<slug>/LEARNING-REVIEW.md`, records the four-question
  learning set in `.kimiflow/project/LEARNINGS.jsonl`, refreshes bounded `MEMORY.md`, and updates the
  memory index.
- `memory-router.sh verify-run` fails closed when a run has no learning review, no recorded learning IDs, or
  a skipped review without an explicit reason.
- Memory-router tests cover recorded reviews, explicit skip reviews, index refresh, and the missing-review
  blocker.

### Changed
- Phase 7 now requires `review-run` + `verify-run` before `STATE.md` may be marked `Status: done`.
- README, reference docs, and install smokes now surface `LEARNING-REVIEW.md`, `review-run`, and `verify-run`
  as part of the Kimiflow memory contract.

## 0.1.31

Ship the **Memory Router and Learning Loop** for token-cheap project recall.

### Added
- `hooks/memory-router.sh` with `status`, `recall`, `classify`, `record`, and `curate` commands for local
  `.kimiflow/project/` memory artifacts (`MEMORY.md`, `LEARNINGS.jsonl`, `MEMORY-INDEX.json`, `RECALL.md`).
- `hooks/test-memory-router.sh` covering empty state, recall, sensitivity classification, recording, and
  non-destructive curation.
- Launcher status now includes memory budget, learning counts, Vault availability, and curation reasons so
  the start menu can offer memory hygiene before feature/fix work.

### Changed
- Canonical Claude and Codex skill docs now route Phase 2 through local memory recall before optional Vault,
  claude-mem, or web research, and route Phase 7 through automatic learning classification/recording.
- Plugin metadata, README, and publish-safe repo docs now mention bounded memory/recall alongside Project
  Intelligence.

## 0.1.30

Fix **launcher run hygiene edge cases** and clarify project-map baseline maintenance context.

### Fixed
- `hooks/launcher-status.sh` no longer infers `Status: done` from ambiguous Phase 7 lines such as
  `Phase 7: not done yet`; only explicit `Phase 7: done` / `RUN COMPLETE` markers count.
- Launcher maintenance JSON now reports `commits_since_project_map_baseline` as an informational baseline
  count, so callers do not mistake it for a stale-map signal.
- `hooks/test-launcher-status.sh` covers both the legacy Phase 7 completion inference and the ambiguous
  `not done` regression case.

## 0.1.29

Fix **launcher open-item counts for English project-map artifacts**.

### Fixed
- `hooks/launcher-status.sh` now counts Findings and Improvements under German and English section
  headings (`## Offen` / `## Open`, `## Priorisierte Slices` / `## Prioritized Slices`), matching
  Kimiflow's user-language artifact rule.
- `hooks/test-launcher-status.sh` now covers both DE and EN count formats so the launcher cannot silently
  show `0` open items for English projects again.

## 0.1.28

Ship the **context-aware Kimiflow Launcher** and publish-safe repo documentation.

### Added
- `hooks/launcher-status.sh` and `hooks/test-launcher-status.sh` provide a read-only launcher snapshot:
  project-map status/depth, findings, improvement slices, repo docs, dirty working tree, and active/backlog
  runs.
- Empty or vague Kimiflow invocations (`/kimiflow`, `$kimiflow`, `--launcher`, `--menu`) now route through
  a context-aware launcher instead of requiring the user to know the right flag up front.
- Resume safety rules now require revalidation before implementing a parked plan when affected files changed
  since the plan commit, or when the plan basis/affected files are unknown.
- Publish-safe repo docs under `docs/` document architecture, codebase layout, testing, and the public docs
  boundary while keeping raw findings local.

### Changed
- Codex plugin metadata now surfaces the launcher in the default prompt and description.
- Claude and Codex smoke tests assert the launcher contract and helper wiring.

## 0.1.27

Ship **hook labels and publish-safety docs** for the Codex plugin path.

### Changed
- Codex plugin-bundled hooks now carry names/descriptions/status text so plugin UIs can label them instead
  of showing only generic hook numbers.
- Project-map docs now state that raw `.kimiflow/project/` maps and sensitive findings stay local/private;
  repo docs are curated publish-safe derivatives.
- README and compatibility docs clarify local Codex plugin cache paths and update expectations.

## 0.1.26

Ship **Codex plugin visibility improvements** so the Project Intelligence capability is visible in the
Codex plugin detail view and CLI update docs.

### Changed
- Codex plugin display metadata now surfaces Project Intelligence in the plugin detail view: default
  prompts include codebase mapping, architecture/refactoring opportunities, and project documentation,
  and the Codex description mentions `.kimiflow/project/` codebase understanding.
- Codex install docs now recommend the Git marketplace (`swinxx/kimiflow`) for normal installs so
  `codex plugin marketplace upgrade kimiflow` works. Local path marketplaces are documented as a
  development mode because Codex shows the local manifest version but does not upgrade that source type.

## 0.1.25

Ship **Project Intelligence** for kimiflow: a local project map, per-section staleness/refresh, and
optional Vault/repo-doc/improvement publishing.

### Added
- **Vault, repo-doc, and Improve publishing contract (Slice 3)** for standalone project-map runs:
  user-language focus choices (`codebase`, `architecture`, `docs`, opt-in `improve`), explicit storage
  targets (`kimiflow`, `kimiflow+vault`, `kimiflow+vault+repo-docs`), local-first source-of-truth rules,
  and evidence-backed `IMPROVEMENTS.md` / `DOCS-PLAN.md` outputs.
- **Project Map Staleness + Delta Refresh (Slice 2)** via `hooks/project-map-status.sh` and
  `hooks/test-project-map-status.sh`. Kimiflow can now classify existing `.kimiflow/project/INDEX.json`
  sections as `current`, `stale`, `potentially_stale`, or `unknown`, report affected stale sections, and
  mark only selected sections refreshed by updating their hashes/commit metadata.
- **Project Map Bootstrap (Slice 1)** docs/contract for a recommended, skippable project-intelligence
  cache under `.kimiflow/project/`. Kimiflow now documents `--project-map quick|standard|deep|skip`,
  the local artifacts (`INDEX.json`, `FACTS.jsonl`, `CODEBASE.md`, `ARCHITECTURE.md`, `CONVENTIONS.md`,
  `TESTING.md`, `FLOWS.md`, `OPEN-QUESTIONS.md`), user-language output, and token-efficient mapper
  focus rules. This is the foundation for later per-section staleness and Vault/repo-doc publishing.

## 0.1.24

Ship **Codex plugin parity** for kimiflow while keeping the Claude Code path intact.

### Added
- **Codex plugin packaging** via `.codex-plugin/plugin.json`, repo-local `.agents/plugins/marketplace.json`,
  and `skills/kimiflow/SKILL.md`, so Codex can install kimiflow as a plugin-backed skill and invoke it
  explicitly with `$kimiflow` / named Kimiflow prompts.
- **Stable Codex hook installer** (`hooks/install-codex-hooks.sh`) that writes managed wrappers into
  `${CODEX_HOME:-~/.codex}/hooks` and pins `KIMIFLOW_PLUGIN_ROOT` back to the plugin checkout. This makes
  commit-secret-gate, state-gate, and test-gate work in Codex without relying on experimental
  `plugin_hooks`.
- **Codex structural smoke test** (`hooks/smoke-install-codex.sh`) covering Codex manifests, skill
  frontmatter, optional plugin-hook wiring, temp `CODEX_HOME` wrapper installation, and synthetic Codex
  hook payloads for commit, state, and test gates.

### Changed
- Hook payload parsing now accepts Codex-shaped command/cwd/stop-active fields alongside Claude-shaped
  payloads.
- `resolve-verbosity.sh` now honors `KIMIFLOW_HOST=codex` and uses `${CODEX_HOME:-~/.codex}` for Codex
  global presentation settings.
- Compatibility and README docs now distinguish stable Codex hook wrappers from optional plugin-bundled
  hooks, and document Codex install/invocation flow.

## 0.1.23

Make **slimness an active counter-force** instead of a polite principle. AIs over-build because training
rewards "comprehensive" and complexity carries no felt cost; a "keep it simple" line doesn't counter that.
This applies kimiflow's own philosophy — adversarial + surfaced, not self-assessed — to over-engineering,
while staying token-cheap (the check must not itself become bloat). **Docs/contract only — no new hook.**

### Added
- **Simplicity lens in Phase-7 code-review** (`reference.md` "Review rubric", `SKILL.md` Phase 7). KPI:
  *"what can be deleted while the ACCEPTANCE tests stay green?"* It FLAGs any abstraction/option/error-
  handling/layer **no test or real requirement demands** (earn the abstraction: **≥2 callers or a written
  reason**; single-caller pass-throughs, impossible-state handling, speculative generality) and **proposes
  the smaller version**. Output is **advisory** → `ADVISORIES.md`, triaged at the commit-gate
  (dismiss-with-reason or adopt) — un-ignorable but non-gating (no false-positive thrash).
- **Token-aware by design.** Runs only where a Phase-7 review runs (`small`/`large`); `trivial` is exempt
  and pays nothing. At `small` the dimension is **folded into the existing reviewer** (no new spawn); a
  **dedicated, blind Simplicity prosecutor** (a new Scaling knob) runs only at `large` or when a **size
  tripwire** fires — `git diff --stat` shows a diff much larger than its scope suggests (orchestrator-read,
  no hook), which raises a STOP+justify advisory.
- **"Fold, don't spawn" rule** (`SKILL.md` Agent budget): prefer extending an existing subagent's brief
  over a fresh spawn when it already has the inputs (~hundreds vs ~tens-of-k tokens); spawn a dedicated
  agent only when independence/blindness is the point.

## 0.1.22

Close a `commit-secret-gate` bypass where **`git -C <target> commit`** scoped the gate to the wrong repo.
The hook located the repo from the tool **cwd**, never from the global `-C <path>`, so a secret-looking
staged path could be committed into a kimiflow repo by running git from a different directory
(`git -C <kimiflow-repo> commit -am …` from outside). **Hook + tests + docs only.**

### Fixed
- **`git -C <path>` is now honored** (`hooks/commit-secret-gate.sh`). Repo resolution passes the global
  `-C` option(s) to git, which resolves them cumulatively relative to the cwd — exactly as `git -C` does
  (so `git -C <repo> commit` from any cwd is scoped to `<repo>`). Extraction is scoped to the **global**
  span (before the subcommand), so a reuse-message `-C <commit>` that *follows* `commit` is not mistaken
  for a chdir. Applied to both the precise (jq) path and the fail-closed jq-less fallback (which tests
  each `-C` target independently, so an unresolvable reuse-`-C` can't mask a real one). `git_root`, on an
  unresolvable `-C`, falls back to the cwd itself — never to the hook's own process cwd. bash-3.2-safe.
- **9 new tests** (`hooks/test-commit-secret-gate.sh`), all run with a process-cwd-faithful runner +
  an OUTSIDE-not-kimiflow guard: `-C` commit/`-am`/relative-`-C`/reuse-message-discriminator/no-false-
  positive, plus the jq-less `-C` cases.

### Docs
- **Known residuals updated** (hook header + `README`/`reference.md` "Commit hygiene"): `git -C <path>` is
  honored for **unquoted, space-free** paths; a **quoted `-C` path with a space** stays a residual; and
  `/usr/bin/git` / `command`/`builtin`/`exec git` are documented as command-position-evasion residuals
  alongside `sudo` / `env X=y` (a deliberate non-standard invocation is out of the gate's threat model).

## 0.1.21

Add a deliberate **defer → backlog** outcome to the Phase-4 pre-build summary gate. Until now a ready,
plan-gate-approved plan could only be **approved** (build now) or sent back to **change** — to park it for
later you had to lean on the silent headless/`--prepare` fallback. Now the interactive stop offers an
explicit third choice: "good plan, not now → backlog." **Docs/contract only — no engine, hook, script, or
flag change.**

### Added
- **`SKILL.md` Phase 4 step 7:** the pre-build gate question is now "Approve to build, change something, or
  defer to backlog?" — **defer → backlog** STOPs, marks `Status: backlog` in `STATE.md`, and emits
  `/kimiflow --resume <slug>`. It is the *explicit* twin of the `--prepare`/headless stop (same parked
  state, deliberate intent).
- **`STATE.md` gains a `Status:` line** (`SKILL.md` Phase 0 step 3): `active` while a run is in progress,
  `backlog` once a complete plan is parked before implementation (phases 0–4 done, 5 open); an absent
  `Status:` reads as `active`. The marker is written by every Phase-4 pre-build park reaching 0–4-done
  (the `defer`, headless, and step-6 plan-gate-open `--prepare` stops all share it) — so the backlog view
  can't mislabel one park as different from its identical-state siblings; an earlier stop (Explore,
  mid-phase) stays `active`.
- **`--resume` (no-slug) listing** now surfaces each run's `Status:` (absent → `active`), so deliberately
  parked **backlog** items are visible as a backlog.

### Changed
- **`reference.md` "Pre-build summary gate" Outcomes** documents the `defer → backlog` outcome and the
  explicit-defer-vs-silent-headless distinction (same parked state + marker; the difference is intent).
  Headless / no-answer control-flow is **unchanged** — it still behaves like `--prepare`, now also
  stamping the shared `Status: backlog` marker.

## 0.1.20

Make kimiflow **model-invocable (opt-in)** instead of hard-blocked. Previously `disable-model-invocation:
true` meant the assistant could not launch kimiflow at all — even when you asked it to ("run this with
kimiflow"); only the human typing `/kimiflow` worked. **Docs/contract only — no engine change.**

### Changed
- **`SKILL.md` frontmatter:** `disable-model-invocation: true` → `false`. The assistant can now launch
  kimiflow **on request**. The "only when asked, never unprompted" policy moved into the `description`
  (lead clause), so it's **opt-in by judgment, not a hard flag**. `/kimiflow` slash invocation is
  unchanged.
- **Honest trade-off, documented** (`README.md` EN+DE, `COMPATIBILITY.md`): the no-unprompted-trigger
  guarantee is now **soft** (description-guided), not mechanically enforced. Anyone who wants the hard
  guarantee back can set `disable-model-invocation: true`. `hooks/smoke-install.sh` now asserts
  model-invocation is enabled (not `true`) and rewords the manual no-auto-trigger check accordingly.

> **Takes effect after you update the installed plugin and restart** — a running session keeps the
> frontmatter it loaded at startup.

## 0.1.19

Close the pre-existing literal-TAB gap in `commit-secret-gate` (the LOW from 0.1.18's review).

### Fixed
- **A non-space token separator (TAB/VT/FF/CR) defeated detection** (`hooks/commit-secret-gate.sh`).
  The git/subcommand matchers anchor on a literal space, so `git<TAB>commit …` or `git commit<TAB>--all …`
  made `git_sub` NO-MATCH — skipping the **whole** commit branch (both the staged-path scan and the
  `-a` working-tree scan), letting a tracked/staged secret commit unblocked. Fixed with a single
  normalization: non-newline whitespace is collapsed to spaces (`tr '\t\v\f\r' ' '`) right after the
  command is parsed, so every downstream matcher benefits. Newlines stay as line separators. 4 tab
  unit tests added (82 cases total).

## 0.1.18

Close a second bypass class in `commit-secret-gate`'s `-a`/`--all` detection and make the README's
promise honest. Found by an external review of 0.1.17. **Hook + tests + docs only.**

### Fixed
- **`-a` detection bypass via a shell metachar hidden from the parser** (`hooks/commit-secret-gate.sh`).
  The commit args were split on `;`/`&`/`|` **before** quotes were stripped, so a metachar inside the
  `-m` message (`git commit -m "a; b" -a`) — or a `\`+newline line continuation — truncated the
  extraction and dropped the trailing `-a`, letting a tracked+modified secret commit unblocked. The
  hook now **joins backslash-newline continuations and strips quoted spans first, then** splits and
  detects `-a`/`--all`. Safe because this branch reads only flags, never pathspec/filenames. Unit
  tests added for quoted `;`/`&`/`|`, the `--all` variant, and a newline continuation (78 cases).

### Changed
- **Honest residuals, in docs and as locked tests** (`README.md`, `reference.md`,
  `hooks/test-commit-secret-gate.sh`). The README no longer implies it blocks "any" secret commit; it
  now names the **backstop, not complete secret protection** framing and the known gaps. Documented +
  test-locked as known ALLOW (regex ≠ shell parser): an `env X=y`/`sudo` prefix (defeats the
  command-position anchor, gate-wide), an **escaped quote** in the message, and an explicit **pathspec
  commit** (`git commit <path>`). A pre-existing literal-tab-after-`git` gap is also known (LOW).

## 0.1.17

Close a real bypass in the `commit-secret-gate` hook and document its boundaries honestly. The gate
only inspected the **index** (`git diff --cached`), so a secret-looking file committed via implicit
staging slipped through. **Hook + tests + docs only — no new mechanism or dependency.**

### Fixed
- **`commit-secret-gate` — `git commit -a`/`--all`/`-am` bypass** (`hooks/commit-secret-gate.sh`). These
  forms auto-stage tracked working-tree modifications *at commit time*, after the PreToolUse hook has
  already read the index — so a modified, already-tracked `.env` (etc.) was committed unblocked. The
  hook now also scans tracked-but-unstaged modifications (`git diff --name-only`) when `-a`/`--all` is
  present. Flag detection matches `a` before any value-taking short option (m/c/C/F/S/u), so bundled
  forms `-am`/`-vam`/`-qam` are caught while `-ma` (a message), `-uall`, `-Sabc` and `--allow-empty` are
  correctly ignored. Unit tests added (`hooks/test-commit-secret-gate.sh`, 70 cases).

### Changed
- **Honest scope docs** (`reference.md` "Commit hygiene" + hook header). The gate no longer claims to
  block "any `git commit`": it now states the `-a`/`--all` coverage **and** the residual limitations —
  an explicit **pathspec commit** (`git commit <path>`) of an already-tracked secret is **not** covered
  (parsing a pathspec from a shell string needs an AST, not a regex). **Bottom line: the gate is a
  path-hygiene backstop, not complete secret protection** — pair it with `.gitignore` discipline + a
  content scanner (gitleaks/trufflehog) and don't track secrets in the first place.

## 0.1.16

Add **claude-mem** as a second *optional* memory-recall provider in Phase 2, alongside the Obsidian
vault. Recall beats re-research: kimiflow now searches cross-session memory too, when it's present.
**Documentation/contract only — no new hook, script, CI, or gate logic; no hard dependency.**

### Changed
- **Phase 2 recall is now provider-agnostic** (`SKILL.md`, `reference.md`). Step 1 ("Recall before
  researching") searches whichever optional providers are connected — the **vault** (notes MCP, e.g.
  Obsidian) and **claude-mem** (cross-session memory MCP, e.g. `memory_search`/`observation_search`).
  Each is independent and graceful: present → use, absent → note in `STATE.md` + continue. A fresh
  relevant hit from either replaces web research. Detection is **per-run by tool availability**, so a
  later-installed provider is picked up on the next run.
- **claude-mem is search-only.** kimiflow recalls from it but never writes to it (it auto-captures
  sessions); verified findings still save to the vault. The "Always last — vault-save" step is
  unchanged.
- **New `reference.md` "Memory recall (Phase 2)"** section documents the two optional providers, the
  graceful-skip contract, and per-run detection. "Vault conventions" stays for vault save-back.
- **README** ("Vault memory layer" / "Vault-Memory-Schicht", EN + DE) names claude-mem as the second
  optional source — search-only, graceful skip, independent of the vault.

## 0.1.15

Make STATE-persistence **enforced**, not a prose ask the orchestrator can rationalize past — closing a
gap found when a "lean" doc run skipped `.kimiflow/<slug>/STATE.md` and lost resumability.

### Added
- **`state-gate` hook** (`hooks/state-gate.sh`, PreToolUse/Bash). Intercepts the review-gate resolver
  call (`resolve-review-gate.sh .kimiflow/<slug>/findings …`) and **denies it fail-closed unless that
  run's `STATE.md` exists and is non-empty** — so no gate verdict (→ no commit) without persisted run
  state. The safety-critical `resolve-review-gate.sh` is **untouched** (separate hook, not a resolver
  edit). Auto-active only in kimiflow repos; needs no jq; unit-tested (`hooks/test-state-gate.sh`, 11
  cases incl. a no-jq path); wired into `hooks.json` + smoke-test. **Honest limit:** catches every run
  that reaches a gate (everything that commits), not a `--prepare`/`trivial` run that stops before any
  gate — those are covered by the prose + eval below.
- **Behavioral-eval scenario 11** (`evals/scenarios/11-state-persistence.md`): does the orchestrator
  still persist `STATE.md` under "keep it in chat to stay lean" pressure?

### Changed
- **SKILL.md "Persist phase progress"** — explicit negation: not optional, not terse-trimmable;
  "small / lean / doc-only run" is not an exemption (only `trivial` runs without the loop). Plus a
  **"Narration ≠ persistence"** clause on the terse-output rule: terse suppresses *talking about* state
  in chat, it never removes writing `STATE.md` / the phase artifacts to disk.

## 0.1.14

A review-contract sharpening: reviewers judge against intent, acceptance, the diff and actual behavior
— **tests are evidence, not the boundary of truth**, not the limit of it. Plus a second eval dimension
that calibrates reviewer *judgement* (not just gate-holding). **Documentation and scenarios only — no
new mechanism, CI, or gate logic.**

### Changed
- **Review rubric — "Tests are evidence, not the boundary of truth"** (`reference.md`). A reviewer
  judges against intent/acceptance/diff/behavior; a green suite may *support* a finding but never
  *refutes* one grounded in code/spec ("not covered by a test" is not a counter-argument). An untested
  real risk is still a finding, and missing coverage of a real risk can itself be a finding —
  anti-hallucination still binds: **severity = provable impact**.
- **Phase 7 reviewer brief** (`SKILL.md`): hunt untested-but-real requirement gaps; a green suite never
  refutes a finding grounded in code/spec. Spine-terse; detail in `reference.md`.
- **Evals reframed as release-calibration** (`evals/README.md`): a mirror read around a release, not a
  runtime oracle; the model under test never sees a findings list; judged post-hoc. Not "test cases."

### Added
- **Reviewer-calibration eval dimension** (`evals/reviewer-calibration.md`): pressure-tests whether a
  reviewer judges cleanly (writes the warranted finding) under green CI / authority / time, vs. the
  tests-as-truth failure. Hidden-notes rule (the answer key never enters the reviewer's context),
  held/soft-crack/hard-crack judging, and an explicit anti-goal — **no gold list, no CI grading of LLM
  reviewers**.
- **Three reviewer pressure scenarios** (`evals/scenarios/reviewer/`): green-but-acceptance-unmet, a
  referenceable defect no test exercises, and a test narrower than the intent.

## 0.1.13

A hardening pass from a second audit: an exact review-gate cap contract, an optional secret
content-scan advisory, and an install smoke-test that guards plugin/skill invocation. This release
also re-syncs the GitHub tag/release, which had lagged at `0.1.0` while the plugin advanced.

### Added
- **Optional secret content-scan advisory** (`hooks/secret-content-scan.sh`). Complements the
  path-only `commit-secret-gate` by scanning the **staged content** for in-source secrets via
  `gitleaks` (else `trufflehog`) when one is installed; findings become `FLAG` advisories in
  `ADVISORIES.md` for commit-gate triage. **Non-gating**, with a graceful STDERR skip when no scanner
  is present — the fail-closed path-hygiene gate is untouched. Wired into Phase 7; unit-tested
  (PATH-mocked) and added to CI.
- **Install smoke-test** (`hooks/smoke-install.sh`). Structural, runnable without a live Claude Code
  session: validates the manifests + version consistency, the `SKILL.md` frontmatter
  (`disable-model-invocation: true`, `user-invocable` not disabled, `name`/`description`/
  `argument-hint`), the `hooks.json` wiring, and fires `commit-secret-gate` against synthetic
  PreToolUse stdin. Prints the manual live-CC checklist and references the Claude Code invocation
  issues it guards (anthropics/claude-code#26251, #22345). CI hard gate.

### Fixed
- **Review-gate cap fires at the round limit, not one past it.** `resolve-review-gate.sh` flagged
  `cap-reached` only at `round > cap` (round 4 under `--cap 3`) — one round past the documented
  "cap 3 reached → stop". Now `round >= cap`: round 3 under `--cap 3` with open findings →
  `cap-reached`, so a run does **at most 3 review rounds** (was effectively 4). TDD-covered; the
  reappearance test gets `--cap 5` headroom to keep exercising its own branch.

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
