```
██╗  ██╗██╗███╗   ███╗██╗███████╗██╗      ██████╗ ██╗    ██╗
██║ ██╔╝██║████╗ ████║██║██╔════╝██║     ██╔═══██╗██║    ██║
█████╔╝ ██║██╔████╔██║██║█████╗  ██║     ██║   ██║██║ █╗ ██║
██╔═██╗ ██║██║╚██╔╝██║██║██╔══╝  ██║     ██║   ██║██║███╗██║
██║  ██╗██║██║ ╚═╝ ██║██║██║     ███████╗╚██████╔╝╚███╔███╔╝
╚═╝  ╚═╝╚═╝╚═╝     ╚═╝╚═╝╚═╝     ╚══════╝ ╚═════╝  ╚══╝╚══╝ 
```

# kimiflow — Feature & Fix Loop (Claude Code + Codex skill/plugin)

A **user-invoked** `/kimiflow` (Claude Code) / `$kimiflow` (Codex) skill+plugin that runs a disciplined **8-phase loop** for building features and fixing bugs — clarify → understand/diagnose → plan → plan-gate → implement → verify → code-review ensemble → commit. Its gates are **mechanical, not advisory**: reviewers write structured findings to files, tested **fail-closed** scripts count the open blockers, fix runs keep red/green evidence, and a "done" self-report can't talk its way past them. Not every change needs the full loop: **`kimiflow quick`** is a lean pass for small, low-risk work — and kimiflow never auto-triggers, it runs only when you invoke it.

<!-- capabilities:start -->
**What kimiflow does:** a disciplined **feature & bug-fix loop** with mechanical gates · **project intelligence** (codebase/architecture map + memory) · **repo docs** generation · local **findings** you can act on.
<!-- capabilities:end -->

> `SKILL.md` / `reference.md` are written in English. **kimiflow replies in the language you write in** — write in German and it grills/answers in German.

## Why this exists

Claude Code and Codex both cover a lot with native planning, subagents and hooks — so why a skill? Because a prose instruction file *asks*; kimiflow *enforces*. The plan-gate and code-review gates are **tested, fail-closed resolver scripts** (`hooks/resolve-review-gate.sh`) that count open blockers mechanically — a verbose model can't argue past them. Phase 7 also uses a **review ensemble**: focused bug/regression, failure/security, and integration/contract lenses produce candidate findings, then the orchestrator verifies them before anything counts as a blocker. The secret-commit and test gates are real **PreToolUse/Stop hooks**, not reminders (the secret gate matches secret-looking **paths**, not file contents — a hygiene backstop; pair it with a content scanner like gitleaks for in-source secrets, see [Hooks](#hooks-bundled)). And it travels: install once, identical gates in every repo, no per-project prompt drift. (kimiflow still reads project convention files such as `AGENTS.md` / `CLAUDE.md` as hints — it just never relies on them for a gate.)

## Install

**Prerequisite:** [`jq`](https://jqlang.github.io/jq/) on your `PATH` — the hooks need it. `brew install jq` (macOS) · `sudo apt-get install jq` (Debian/Ubuntu).

**Optional (recommended):** Obsidian for the **vault memory layer** — kimiflow auto-detects a running Obsidian Local REST API on the common local ports and can connect it with a local provider manifest, then writes a reviewable sync handoff for reusable findings. An authenticated Vault MCP is needed for direct Vault reads/writes; an API key can validate the local REST API but is never stored and does not by itself add direct tools. No vault provider → kimiflow skips it and uses the repo-local `.kimiflow/` memory. → full setup + why it's worth it under **[Vault memory layer](#vault-memory-layer-optional-but-recommended)** below.

### Claude Code — plugin (skill **+** hooks)

Inside Claude Code:
```
/plugin marketplace add kimikonapps/kimiflow
/plugin install kimiflow@kimiflow
```
…or from a terminal:
```bash
claude plugin marketplace add kimikonapps/kimiflow
claude plugin install kimiflow@kimiflow
```
Then **restart Claude Code** (or open a new session) and run `/kimiflow`. This installs the skill **and** the safety hooks (`commit-secret-gate`, `state-gate`, `test-gate`). Update later with `claude plugin update kimiflow`.

### Codex — plugin skill **+** stable hooks

Recommended public install:

```bash
codex plugin marketplace add kimikonapps/kimiflow
bash "${CODEX_HOME:-$HOME/.codex}/.tmp/marketplaces/kimiflow/hooks/install-codex-hooks.sh"
```

Then open the Codex plugin browser (`/plugins` in the CLI, or **Plugins** in the Codex app), install **kimiflow** from the **kimiflow** marketplace, start a new thread, and invoke it explicitly:

```text
$kimiflow Add a dark-mode toggle in settings
$kimiflow --fix App crashes when opening an empty project
```

Update the marketplace later with:

```bash
codex plugin marketplace upgrade kimiflow
```

`hooks/install-codex-hooks.sh` writes Kimiflow wrappers into `${CODEX_HOME:-~/.codex}/hooks`, the stable Codex hook surface, and pins them back to the checkout it is run from with `KIMIFLOW_PLUGIN_ROOT`. Some Codex CLI versions expose marketplace management but not a non-interactive plugin install/update command; in that case the plugin browser/app install step is expected after the marketplace upgrade. Codex plugin-bundled hooks are also described in `hooks.json` for builds that enable `plugin_hooks`, but Kimiflow's safety gates do not rely on that experimental path.

The Codex plugin UI may show hook commands with an expanded local cache path such as `~/.codex/plugins/cache/...` or `~/.codex/.tmp/marketplaces/...`. That path is resolved on each user's machine; it is not a published path from this repository, so other users see their own local Codex directory, not the maintainer's. If the UI still shows an older version in that path after `codex plugin marketplace upgrade kimiflow`, the Git marketplace checkout may already be current while the app's installed plugin cache is still stale; restart Codex and reinstall/update the plugin from the plugin browser if needed.

For local plugin development, register the checkout instead:

```bash
codex plugin marketplace add .
bash hooks/install-codex-hooks.sh
```

Local path marketplaces show the newest local manifest in the plugin browser, but `codex plugin marketplace upgrade` only works for Git marketplaces. Use the Git marketplace (`kimikonapps/kimiflow`) for normal installs and repeatable CLI updates.

The Codex port uses the same `.kimiflow/<slug>/` state, resolver scripts, commit-secret-gate, state-gate, and test-gate as the Claude Code plugin once the hook installer has run.

### Claude Code alternative — skill only (no hooks)

```bash
git clone https://github.com/kimikonapps/kimiflow ~/.claude/skills/kimiflow
```
Gives you `/kimiflow` (auto-discovered, no restart needed) — but **not** the hooks (`hooks.json` loads only via the plugin).

> **Public repo** — anyone can install; no access request needed. The skill is **opt-in**: it launches when you ask for it (say "kimiflow" / "with kimiflow" / "run kimiflow", type `/kimiflow` in Claude Code, or invoke `$kimiflow` in Codex) and **won't fire unprompted** on unrelated requests. This is description-guided judgment, not a hard block.

## 30-second demo

![kimiflow demo — launcher, project map, memory recall, gates, commit stop, and learning loop](docs/demo/kimiflow.gif)

> _Illustrative reconstruction_ — the current Kimiflow front door and core loop: launcher status →
> project map + memory recall → mode choice → clarify/understand/plan → mechanical gates → **commit-gate**
> (stops for your OK) → learning loop. Rendered via [`docs/demo/`](docs/demo/); a real capture replaces it later.

The same gates on a **bug fix** — the other mode (full walkthrough: [`examples/02-risky-bugfix.md`](examples/02-risky-bugfix.md)):

```text
/kimiflow --fix  token refresh throws after the access token expires

⚪ Phase 0  scope-gate ····· large (touches auth; reproducible symptom)
🔵 Phase 1  clarify ········ symptom? repro? expected? → PROBLEM.md  ✋ "Does this match?"
🟣 Phase 2  diagnose ······· reproduces the throw, proves the cause at auth/refresh.ts:88
            └─ no proven root cause ⇒ NO fix. (proven → continue)
⚫ Phase 3  plan ··········· fix + EARS acceptance criteria → PLAN.md
🟡 Phase 4  PLAN-GATE ······ plan-blocker-gate.sh → independent reviewers → resolve-review-gate.sh
            └─ counts evidenced BLOCKER/HIGH, fail-closed, large cap 3 → 0 open ✅
🟠 Phase 5  implement ······ failing test first (red) → fix → green
🟤 Phase 6  verify ········· throw gone, suite green, checked against the criteria
🟢 Phase 7  code-review ···· ensemble candidates → orchestrator verifies → gate counts confirmed findings
            └─ COMMIT-GATE: shows the diff, ✋ STOPS for your OK — never auto-commits
```

Each ✋/✅ and the diagnose/commit stop is a real gate, not a prompt suggestion. The clip above is a scripted illustration — record your own from a **real** run with the steps under [`docs/demo/`](docs/demo/).

## What gates are mechanical

"Mechanical" = a tested script or a hook makes the call, not the model's self-report. The honest split:

| Gate | Phase | Mechanism | Fail-closed? |
|------|-------|-----------|--------------|
| **Working-tree start gate** | 0 | `hooks/working-tree-gate.sh` requires a clean repo before new write-mode Kimiflow runs; `.kimiflow/` local state is ignored | ✅ yes |
| **Clarify gate** | 1/4 | `hooks/clarify-gate.sh` requires confirmed behavior, scope, and outcome for small/quick; no question quota | ✅ yes |
| **Discovery gate** | 2/4 | `hooks/discovery-gate.sh` binds new schema-2 feature runs to Discovery evidence while preserving pre-schema resumes | ✅ yes |
| **Plan-blocker gate** | 4 | composes Clarify + Discovery, then checks AC mapping, verification, path evidence, and affected files before reviewers | ✅ yes |
| **Plan-gate** | 4 | `hooks/resolve-review-gate.sh` counts evidenced `BLOCKER/HIGH`; small feature/fix cap 2, large/audit cap 3; rounds never reset | ✅ yes |
| **Red/green fix gate** | 6 | `hooks/red-green-gate.sh` requires `BUG-REPRO.md` with red command/status/output, green command/status/output, and regression evidence before fix-mode review/learning can finish | ✅ yes |
| **Local diagnostics advisory** | 6/7 | `hooks/lsp-diagnostics.sh` runs bounded existing local typecheck/lint/LSP-adjacent tools or one untracked local `.kimiflow/lsp-diagnostics` command; flags are triaged before commit | advisory |
| **Code-review gate** | 7 | focused review lenses produce candidates; the orchestrator verifies and promotes confirmed findings; the same resolver counts open `BLOCKER/HIGH` | ✅ yes |
| **Commit-gate** | 7 | STOP + advisory triage; waits for your explicit OK before any commit | ✅ yes |
| **Secret-commit hook** | any commit | `PreToolUse` hook — blocks staging secret-looking **paths** + bulk `git add -A`/`.` | ✅ yes |
| **State-gate hook** | review gates | `PreToolUse` hook — blocks resolver calls without durable `.kimiflow/<slug>/STATE.md` | ✅ yes |
| **Test-gate hook** (opt-in) | finish | `Stop` hook — blocks finishing while the project's tests are red | ✅ yes |

What is **not** mechanical (model-judged, by design): the scope classification, the root-cause proof, the verification call, and — the honest limit — **whether the candidate lenses are complete**. The gate is mechanical *over confirmed findings*; it can't prove reviewers found everything. kimiflow makes the gate un-foolable, and the ensemble reduces blind spots without pretending to be omniscient.

## Usage

```
/kimiflow                    # open the context-aware launcher/menu
/kimiflow <feature>          # build a feature
/kimiflow <bug>              # fix a bug (auto-detected)
/kimiflow --fix <bug>        # force fix mode
/kimiflow --verify-feature <feature-or-path>  # review an already-built feature; no code edits
/kimiflow <…> --prepare      # prepare only (through plan-gate), implement later
/kimiflow --resume <slug>    # continue a prepared/interrupted run in a fresh session
/kimiflow --project-map quick  # bootstrap the local project map; launcher shows status only
```

Natural shortcuts:

```text
kimiflow full    # strict: confirmed intent + adaptive discovery + plan-gate + Build Preview approval
kimiflow grill   # clarify/spec only; no code
kimiflow plan    # prepare plan + acceptance criteria; no code
kimiflow build   # build an approved/prepared plan
kimiflow quick   # lean small change; confirms complete intent, normally no research worker
kimiflow review  # read-only feature/current-change review
kimiflow audit   # read-only cleanup/refactoring scan first
kimiflow fix     # bug flow with Red/Green evidence
```

For `small` and `quick`, kimiflow confirms behavior, scope, and the user-visible outcome. There is no minimum question count: a complete request needs only one compact intent confirmation; technical gaps go to Discovery, not back to the user. Only exact `trivial` work may skip this.
`small`/`quick` skip memory recall and the **Vault Pulse** (both run at `scope=large` only); the Current-State Pulse and the Phase-7 learning loop still run at every scope.

In Codex, use the same arguments with `$kimiflow`:

```text
$kimiflow
$kimiflow <feature>
$kimiflow full
$kimiflow grill
$kimiflow --fix <bug>
$kimiflow --verify-feature <feature-or-path>
$kimiflow --resume <slug>
$kimiflow --project-map quick
```

## Existing feature check

Use `/kimiflow --verify-feature <feature-or-path>` (or the `review` alias) when a feature already exists and you
want to know whether it is really wired correctly. It runs the normal Phase-7 code-review ensemble read-only over
the named path or the current diff — the same lenses and verify-before-promote mechanic as a build run's review.
Findings land in the run's `findings/`/`CODE-REVIEW.md`; the mode does not edit code, and confirmed findings can
seed a normal fix/improve run.

## Project structure

The repository is intentionally small and script-first:

- `SKILL.md` and `reference.md` — canonical Kimiflow workflow contract and detailed rules.
- `skills/kimiflow/SKILL.md` — Codex-facing wrapper that maps the canonical workflow to Codex.
- `.claude-plugin/`, `.codex-plugin/`, `.agents/plugins/`, `hooks.json` — host/plugin manifests.
- `hooks/` — mechanical gates, launcher/status helpers, memory router, Obsidian setup helpers, smoke tests,
  and focused shell tests.
- `docs/` and `examples/` — demos, design notes, and walkthrough examples.
- `.kimiflow/` — local project intelligence, run state, memory, findings, and economics data. This directory is
  generated during local runs and is not meant to be committed by default.

## Active session loop

When you start Kimiflow for a real feature or fix, it creates a local active-session contract under
`.kimiflow/session/ACTIVE_RUN.json`. Follow-up requests like "also add this button" or "that still does not work"
stay inside the same Kimiflow run until the run is finished, parked, failed, or aborted.

The run keeps a small item backlog in `.kimiflow/<slug>/ITEMS.jsonl`. Kimiflow will not finish while items are
pending, only built, rejected, or stale after relevant files changed. Positive learnings are written only on a
successful `finish`; parked, failed, or aborted runs do not promote unverified memory.

## Launcher

If you invoke kimiflow without a concrete task (`/kimiflow` or `$kimiflow`), it opens a context-aware
launcher. The launcher first runs `hooks/launcher-status.sh` and summarizes the current project state:
one recommended next action, installed/cache version status, active session status,
project-map status, memory/recall status, open findings, repo docs,
dirty working tree, and active or backlog runs. Technical hygiene that does not need your decision stays in
drilldowns instead of becoming front-page work. It then routes your choice into the normal Kimiflow modes.
It also surfaces the short mode aliases (`full`, `grill`, `plan`, `build`, `quick`, `review`, `audit`, `fix`) so
users can choose the desired workflow mode without memorizing flags.

Backlog/resume is guarded: a parked plan is not implemented blindly if affected files changed since its
plan commit, or if the plan basis is unknown. In that case kimiflow offers plan revalidation before Phase 5.

## Project map bootstrap

On non-trivial runs, if `.kimiflow/project/INDEX.json` is missing, kimiflow can offer a recommended but skippable **Project Map Bootstrap**. It creates a local project-intelligence cache under `.kimiflow/project/`: `INDEX.json`, `FACTS.jsonl`, and compact markdown notes for codebase, architecture, conventions, tests, flows, and open questions. Future runs read this first so they can understand what already exists before planning a bug fix or feature.

Bootstrap choices:

- `quick` — the CLI bootstrap option: stack, structure, entry points, central modules, flows,
  conventions, and tests, written into the local map.
- `skip` — continue without creating the map.

The launcher reports the resulting map by status, for example `Project map: current`. `quick` is command vocabulary for the bootstrap step, not a user-facing map level. The map is local and optional. Missing, skipped, or incomplete maps never block the normal kimiflow loop.

If a map already exists, kimiflow checks it per section with `hooks/project-map-status.sh`. Sections can
be `current`, `stale`, `potentially_stale`, or `unknown`; stale affected sections trigger a recommended
but skippable delta refresh. Refresh updates only the selected section hashes/commit metadata, so future
runs reuse the map without paying for a full rescan. Once likely affected paths are known,
`project-map-status.sh coverage --affected <path>...` recommends the Phase-2 detail level: `compressed` for mapped/current
code, `targeted` for mapped but stale/unknown sections, and `full` for unmapped or missing/invalid maps.

Standalone map runs can also choose a focus: codebase, architecture, docs, or opt-in improvement ideas.
Storage is explicit: `kimiflow` only, `kimiflow + Vault`, or `kimiflow + Vault + repo docs`. The local
`.kimiflow/project/` map is always written first; Vault and repo docs are publishing layers, never
requirements. Improvement slices are written as proposals with evidence, value, risk, effort, acceptance
criteria, and "do not touch" notes.

`.kimiflow/project/` is a local agent cache and is not meant to be committed by default. When repo docs are
requested, kimiflow writes a curated publish-safe derivative instead: architecture, codebase, flow and
testing docs may go under the repo's docs structure, while concrete vulnerabilities, exploit paths,
secrets, private/local paths, vault references, and raw improvement findings stay local or private unless
you explicitly ask for a sanitized public note.

## Memory Router

Kimiflow also keeps a bounded local memory under `.kimiflow/project/`: `MEMORY.md`, `USER.md`,
`LEARNINGS.jsonl`, `USER.jsonl`, `MEMORY-INDEX.json`, optional `RECALL.sqlite`, `RECALL.md`,
`RUN-HISTORY.json`, `MEMORY-USAGE.json`, `MEMORY-ECONOMICS.jsonl`, `VAULT-PROVIDER.json`, `VAULT-PREFETCH.md`, `VAULT-SYNC.md`,
`PENDING-PROPOSALS.md`, `PROPOSALS.jsonl`, and review-only `SKILL-DRAFTS/`; each completed run also gets a
run-local `LEARNING-REVIEW.md`, `RUN-LIFECYCLE.json`, `RUN-LIFECYCLE.md`, plus a machine-readable `RECALL.json`
when recall is written. Local review
summaries and canonical `findings/*.md` are searchable through history/recall but stay local-only by default.
`hooks/memory-router.sh` gives the launcher and Phase 2 a cheap way to check memory freshness, recall relevant
project facts, classify new learnings, write the required run-close learning review, and curate the index
without rereading the whole repo or Vault every time. (As of the current release the router is a stdlib-only
Python implementation — `hooks/memory_router/` — behind the unchanged `memory-router.sh <cmd> …` entrypoint;
it requires `python3` >= 3.9. See `COMPATIBILITY.md`.) Persisted recall/history writes are measured in
`MEMORY-USAGE.json`; `status` exposes compact hot/warm/cold/stale usefulness tiers, `recall --write` explains
which sources were included or omitted, and completed runs append cautious, directional token-efficiency estimates to
`MEMORY-ECONOMICS.jsonl`; `memory-router.sh metrics` reports legacy usage economics at `.economics`,
run-economics at `.run_economics`, and a global local anonymous aggregate at `.global_efficiency`, normalizing
older rows to the current `used_hit_count` heuristic. Fewer than 8 recorded runs are reported as insufficient
data, so Kimiflow does not pretend savings are proven too early. The global aggregate lives under
`~/.kimiflow/metrics/token-economics.jsonl`, can be disabled with `KIMIFLOW_GLOBAL_METRICS=off`, and stores only
numbers/enums plus salted hash IDs: no code, prompts, repo names, file paths, Vault contents, or learnings text.
The launcher can show a compact estimate such as "estimated token savings", but it is always labelled as an
estimate, not billing truth.
`MEMORY.md` prioritizes frequently used, high-confidence, recent publish-safe learnings instead of forcing every
row into the prompt.

This layer is local-first and optional-provider-aware. It works without a Vault MCP; `provider status`
auto-detects a running Obsidian Local REST API on `https://127.0.0.1:27124` / `http://127.0.0.1:27123`, and
`provider connect` writes only `.kimiflow/project/VAULT-PROVIDER.json`. It never stores an Obsidian API key.
`provider health` distinguishes `detected_unconfigured`, `connected_local_only`, `authenticated`, and
`auth_failed`, so the launcher can explain exactly whether Obsidian is merely detected, locally connected,
locally API-validated, or backed by direct MCP search/write tools. If direct Vault MCP access is available,
kimiflow can promote curated long-term learnings there while keeping private/security details local or sanitized. Run-close learnings are
quality-gated and source-freshness checked, so vague notes and stale
evidence do not become active project memory. Evidence references are stored repo-relative; outside-repo paths
are collapsed to `OUTSIDE_REPO`. When evidence changes, refreshed rows supersede older rows and recall returns
only current learnings. Recall can also search bounded old run artifacts and records use-count/last-used metrics
only when a recall/history snapshot is written. Memory writes are scanned for prompt-injection/exfiltration
patterns, user preferences are split into local-only profile files, and `propose`/`consolidate` turn accumulated
learning into reviewable rule/skill proposals and compacted history. Proposal state supports `--approve`,
`--reject`, and `--apply`; approved standards/decisions can be appended to local `.kimiflow/` docs, while skill
candidates create review-only draft notes instead of patching skills automatically. Provider sync writes a
bounded `VAULT-SYNC.md` handoff with only current, non-private, non-security learnings with freshly verified
repo-relative evidence; it exports at most 20 candidates by default, records only exported IDs locally, and never
writes external Vault notes blindly.
Approve/apply revalidates evidence first, so stale proposals stay local until refreshed.
The launcher surfaces memory budget, learning counts, usefulness counts, run-history/usage/provider health, pending
provider sync handoffs, pending proposal notifications, Vault availability, and only user-actionable curation reasons.
Internal threshold hints such as `many_learnings` stay silent when memory is fresh and under budget.

## Example

**Feature:**
```
/kimiflow Add a dark-mode toggle in settings
```
1. kimiflow confirms behavior, scope, and outcome → `INTENT.md`
2. understands the code, assesses Discovery depth, and researches only plan-changing gaps → `RESEARCH.md`
3. internal plan + gate → plain-language Build Preview → build (or risk approval) → verify → review
4. shows the diff and **waits for your OK before committing**

**Bug fix:**
```
/kimiflow --fix App crashes when opening an empty project
```
1. clarifies the problem (symptom, reproduction) → `PROBLEM.md`
2. **reproduces the crash**, **proves the cause** (`file:line`), **researches the correct fix** → `DIAGNOSIS.md`. Without a proven cause it does **not** fix.
3. fixes → verifies the crash is gone + no regression → code-review ensemble → **stops before committing**

## Flow (8 phases)

Scope-gate → **confirm intent** → **project-first adaptive Discovery** (`none|pulse|focused`, no worker by default; research informs HOW, never expands WHAT) resp. **diagnose** → **minimum-complete internal plan** → **plan-gate** → **Build Preview / conditional Risk Gate** → **implement** → **verify** → **code review** → **commit** (stops for your OK).

State is persisted to `.kimiflow/<slug>/` in the target project (resumable). `small`/`quick` runs stay lean, but they do not skip Phase 1: kimiflow asks or confirms enough to avoid building the wrong thing.
`small`/`quick` also run a tiny Current-State Pulse: local-only work records "no external freshness check needed"; changing APIs/tooling/hosts gets one current primary-source check before planning.
`small`/`quick` skip memory recall and the Vault Pulse — both run at `scope=large` only (recall payoff is thin on small tasks); the Phase-7 learning loop still runs at every scope.

> **Cost:** Discovery `none|pulse` uses no research worker by default; `focused` normally uses one bounded evidence worker and at most two independent lanes. A top model owns assessment, synthesis, decisions, planning, and review. Large still defaults to one planner; a second appears only for a material architecture/contract fork. Current Codex mapping is Sol/Terra/Luna.

## Principles

- **Simplicity-first** — complexity scales with the work (scope-gate).
- **Binary no-blocker gates**, never a numeric score.
- **Evidence-before-assertion** — verify against specs, not vibes.
- **Fix mode:** prove the root cause and research the correct fix *before* fixing (the model may not be up to date).
- **Colored phase markers** — each of the 8 phases announces with its own color (⚪🔵🟣⚫🟡🟠🟤🟢) so a run reads at a glance in Claude Code.

Details in [`reference.md`](reference.md).

## Hooks (bundled)

kimiflow ships safety hooks under `hooks/`, **active only in kimiflow repos** (a `.kimiflow/` dir at the git root) so they never touch unrelated projects:

- **`commit-secret-gate`** — **filename/path hygiene, not secret-in-source detection**: blocks a `git commit` that would stage a secret-looking **path** (`.env`/`.envrc` incl. `prod.env`-style suffixes, `*.pem/.key/.p12/.pfx/.asc`, private SSH keys `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519` (not `.pub`), `.npmrc`, `secret`/`credential`/`access_token`/`auth_token` paths) and any bulk `git add -A`/`.`. It matches **paths, never file contents** — a key pasted into source passes — so pair it with a content scanner for in-source secrets. kimiflow's advisory `secret-content-scan.sh` does this: **`gitleaks protect --staged`** is the clean staged-content path; **trufflehog** is a best-effort fallback (no native staged mode — it scans commits since `HEAD`). It also covers the working-tree paths a `git commit -a`/`--all` would auto-stage, but it is **a backstop, not complete secret protection**: an explicit pathspec commit (`git commit <path>`), a command-position-evasion prefix (`env X=y`/`sudo`/`/usr/bin/git`/`command git`), a quoted `-C` path with a space, and an escaped quote in the message are **known, documented gaps** (regex isn't a shell parser — see [reference.md](reference.md) "Commit hygiene"). A global **`git -C <path>`** to another repo **is** honored (the gate scopes to the target, not the cwd). Real coverage = `.gitignore` discipline + a content scanner + not tracking secrets.
- **`state-gate`** — blocks review-gate resolver calls when a non-trivial kimiflow run has no durable `STATE.md`; this protects resume and gate state from living only in chat.
- **`clarify-gate`** — requires current-run confirmation of behavior, scope, and outcome for `small`/`quick`, never a minimum question count.
- **`discovery-gate`** — checks Phase-2 completeness and source shape before planning; it does not pretend to prove that research was exhaustive or objectively optimal.
- **`test-gate`** (opt-in) — blocks finishing while the project's tests are red; enable per project via a **local, untracked** `.kimiflow/test-gate` file (auto-enabled for `large`-scope runs). A git-tracked (committed) marker is refused — its first line is `eval`'d, so committed markers can't run as a drive-by.

## Vault memory layer (optional, but recommended)

kimiflow can use an **Obsidian vault as a cross-project knowledge base**. It can auto-detect Obsidian's Local REST API when the app is open, connect it locally, and write reviewable prefetch/sync handoffs for reusable findings. With authenticated MCP tool access, Phase 2 can also **search your vault before researching** (so it never re-researches what you already learned). Across many projects this compounds into a personal, searchable memory that makes every run faster and better-grounded. **It's genuinely worth setting up.**

**Without a vault MCP — nothing breaks.** kimiflow can still detect a running Obsidian app and create local `VAULT-PROVIDER.json` / `VAULT-SYNC.md` handoffs, but skips direct vault search + save and continues. Research falls back to the codebase + web, and the **repo-local `.kimiflow/` memory** (`STANDARDS.md` / `DECISIONS.md`) still persists project-level learning. No errors, no blocked phases — identical gates, hooks and outcome; you only lose the direct cross-project shortcut until an authenticated MCP is configured.

The newer local memory router (`.kimiflow/project/MEMORY.md`, `LEARNINGS.jsonl`, `MEMORY-INDEX.json`) still
works without a vault and is the default project-level learning layer.

**Second optional source — claude-mem.** If the **claude-mem** plugin (cross-session memory) is installed, kimiflow *also* searches it during Phase 2 recall ("did we already deal with this?") — **search-only**; saving still goes to the vault / repo-local `.kimiflow/` memory. Not installed → skipped, exactly like the vault. **Detection is per-run**, so adding it later is picked up on the next run (after a `/reload-plugins` or restart). The two are independent — either, both, or neither.

### Setup — so the vault layer actually works

1. **Install Obsidian:** <https://obsidian.md> — open or create a vault.
2. **Enable the *Local REST API* plugin** ([coddingtonbear/obsidian-local-rest-api](https://github.com/coddingtonbear/obsidian-local-rest-api)): Obsidian → Settings → Community plugins → install & enable. Keep Obsidian running; kimiflow auto-detects the default HTTPS endpoint at `https://127.0.0.1:27124` and offers to connect it.
3. **Optional, for direct Vault reads/writes:** use the built-in MCP endpoint from Local REST API. The easiest path opens a Terminal wizard so the API key stays out of chat:
   ```bash
   hooks/vault-mcp-open-terminal.sh --host codex
   ```
   On macOS, the wizard writes the user-level Codex MCP config, stores the key in Keychain, sets the launch environment for newly opened Codex, verifies the loopback Local REST API, and checks that the MCP endpoint initializes. For Claude Code use `--host claude`; for both hosts use `--host all`.
4. **Manual/CLI fallback:** run `hooks/vault-mcp-setup.sh --host all --interactive` in your own terminal, or `hooks/vault-mcp-setup.sh --host all` to print Codex and Claude Code snippets for `https://127.0.0.1:27124/mcp/`. It never prints, commits, or stores the API key in `.kimiflow/`.
5. **Restart/reload your MCP client** and keep **Obsidian running** during a kimiflow run.

**If HTTPS certificate trust fails:** the Local REST API plugin uses a local certificate for
`https://127.0.0.1:27124`. The wizard detects this and prints the fix. Open/download
`https://127.0.0.1:27124/obsidian-local-rest-api.crt`, add it to the macOS **System** keychain, open the
certificate, set SSL trust to **Always Trust**, confirm, then restart Obsidian and Codex/Claude Code. Re-run the
wizard; a healthy check verifies `/mcp/` without an insecure TLS bypass. If you deliberately do not want to trust
the certificate, use the local-only fallback `--url http://127.0.0.1:27123`.

The frictionless path is: detect Obsidian → `provider connect` → `provider health` → Terminal setup wizard → local
`VAULT-PREFETCH.md` / `VAULT-SYNC.md` handoffs. Direct note search/write uses authenticated
Vault MCP tools (for example Local REST API's built-in `search_simple`, `vault_read`, `vault_append`/`vault_write`,
or compatible legacy `obsidian_*` tools) only once the host exposes them.
An `OBSIDIAN_API_KEY` environment variable can validate the local REST API for health checks, but direct
search/write stays disabled until a tool provider is actually present.

---

# kimiflow — Feature- & Fix-Loop (Deutsch)

Ein **user-invoked** `/kimiflow`- (Claude Code) / `$kimiflow`-Skill+Plugin (Codex), das einen disziplinierten **8-Phasen-Loop** fürs Bauen von Features und Fixen von Bugs fährt — Klärung → Verstehen/Diagnose → Plan → Plan-Gate → Umsetzung → Verifikation → Code-Review → Commit. Seine Gates sind **mechanisch, nicht beratend**: Reviewer schreiben strukturierte Findings in Dateien, getestete **fail-closed** Scripts zählen die offenen Blocker, und ein „fertig" lässt sich nicht daran vorbeireden. Nicht jede Änderung braucht den vollen Loop: **`kimiflow quick`** ist ein schlanker Durchlauf für kleine, risikoarme Arbeit — und kimiflow triggert nie automatisch, es läuft nur, wenn du es aufrufst.

> `SKILL.md` / `reference.md` sind auf Englisch geschrieben. **kimiflow antwortet in deiner Sprache** — schreibst du Deutsch, grillt/antwortet es auf Deutsch.

## Warum es das gibt

Claude Code und Codex decken mit nativer Planung, Subagents und Hooks schon viel ab — warum also ein Skill? Weil eine prosaische Instruktionsdatei *bittet*; kimiflow *erzwingt*. Plan-Gate und Code-Review-Gate sind **getestete, fail-closed Resolver-Scripts** (`hooks/resolve-review-gate.sh`), die offene Blocker mechanisch zählen — ein geschwätziges Modell argumentiert sich da nicht vorbei. Secret-Commit- und Test-Gate sind echte **PreToolUse/Stop-Hooks**, keine Erinnerungen (der Secret-Gate matcht secret-verdächtige **Pfade**, keine Datei-Inhalte — ein Hygiene-Backstop; für Secrets im Quelltext einen Content-Scanner wie gitleaks dazunehmen, siehe [Hooks](#hooks-mitgeliefert)). Und es reist mit: einmal installiert, identische Gates in jedem Repo, kein Per-Projekt-Prompt-Drift. (kimiflow liest Projektkonventionen wie `AGENTS.md` / `CLAUDE.md` als Hinweise — verlässt sich für ein Gate nur nie darauf.)

## Installation

**Voraussetzung:** [`jq`](https://jqlang.github.io/jq/) im `PATH` — die Hooks brauchen es. `brew install jq` (macOS) · `sudo apt-get install jq` (Debian/Ubuntu).

**Optional (empfohlen):** Obsidian für die **Vault-Memory-Schicht** — kimiflow erkennt eine laufende Obsidian Local REST API automatisch auf den üblichen lokalen Ports, kann sie lokal verbinden und schreibt dann ein reviewbares Sync-Handoff für wiederverwendbare Erkenntnisse. Ein authentifizierter Vault-MCP ist für direkte Vault-Reads/Writes nötig; ein API-Key kann die lokale REST-API validieren, wird nie gespeichert und liefert allein noch keine Direct-Tools. Kein Vault-Provider → kimiflow nutzt die repo-lokale `.kimiflow/`-Memory. → vollständiges Setup + warum es sich lohnt unter **Vault-Memory-Schicht** unten.

### Claude Code — Plugin (Skill **+** Hooks)

In Claude Code:
```
/plugin marketplace add kimikonapps/kimiflow
/plugin install kimiflow@kimiflow
```
…oder im Terminal:
```bash
claude plugin marketplace add kimikonapps/kimiflow
claude plugin install kimiflow@kimiflow
```
Dann **Claude Code neu starten** (oder neue Session) und `/kimiflow` aufrufen. Das installiert den Skill **und** die Sicherheits-Hooks (`commit-secret-gate`, `state-gate`, `test-gate`). Später aktualisieren mit `claude plugin update kimiflow`.

### Codex — Plugin-Skill **+** stabile Hooks

Empfohlene öffentliche Installation:

```bash
codex plugin marketplace add kimikonapps/kimiflow
bash "${CODEX_HOME:-$HOME/.codex}/.tmp/marketplaces/kimiflow/hooks/install-codex-hooks.sh"
```

Dann im Codex-Plugin-Browser (`/plugins` in der CLI oder **Plugins** in der Codex-App) **kimiflow** aus dem **kimiflow**-Marketplace installieren, einen neuen Thread starten und explizit aufrufen:

```text
$kimiflow Dunkelmodus-Schalter in den Einstellungen
$kimiflow --fix App stürzt ab beim Öffnen eines leeren Projekts
```

Später den Marketplace aktualisieren mit:

```bash
codex plugin marketplace upgrade kimiflow
```

`hooks/install-codex-hooks.sh` schreibt Kimiflow-Wrapper nach `${CODEX_HOME:-~/.codex}/hooks`, also in die stabile Codex-Hook-Oberfläche, und pinnt sie über `KIMIFLOW_PLUGIN_ROOT` zurück auf den Checkout, aus dem der Installer läuft. Einige Codex-CLI-Versionen haben Marketplace-Verwaltung, aber keinen nicht-interaktiven Plugin-Install-/Update-Befehl; dann ist der Installationsschritt über Plugin-Browser/App nach dem Marketplace-Upgrade normal. Plugin-gebündelte Codex-Hooks sind zusätzlich in `hooks.json` beschrieben, falls ein Build `plugin_hooks` aktiviert, aber Kimiflows Sicherheitsgates hängen nicht von diesem experimentellen Pfad ab.

Die Codex-Plugin-UI kann Hook-Befehle mit einem expandierten lokalen Cache-Pfad wie `~/.codex/plugins/cache/...` oder `~/.codex/.tmp/marketplaces/...` anzeigen. Dieser Pfad wird auf dem Rechner jedes Users aufgelöst; er ist kein veröffentlichter Pfad aus diesem Repository, andere User sehen also ihr eigenes lokales Codex-Verzeichnis, nicht das des Maintainers. Wenn die UI nach `codex plugin marketplace upgrade kimiflow` noch eine ältere Version in diesem Pfad zeigt, kann der Git-Marketplace-Checkout bereits aktuell sein, während der installierte App-Plugin-Cache noch stale ist; dann Codex neu starten und das Plugin bei Bedarf im Plugin-Browser neu installieren/aktualisieren.

Für lokale Plugin-Entwicklung registrierst du stattdessen den Checkout:

```bash
codex plugin marketplace add .
bash hooks/install-codex-hooks.sh
```

Lokale Pfad-Marketplaces zeigen im Plugin-Browser das neueste lokale Manifest, aber `codex plugin marketplace upgrade` funktioniert nur für Git-Marketplaces. Für normale Installationen und wiederholbare CLI-Updates ist der Git-Marketplace (`kimikonapps/kimiflow`) der richtige Weg.

Der Codex-Port nutzt dieselbe `.kimiflow/<slug>/`-State-Struktur, dieselben Resolver-Scripts, denselben commit-secret-gate, state-gate und test-gate wie das Claude-Code-Plugin, sobald der Hook-Installer gelaufen ist.

### Claude-Code-Alternative — nur Skill (ohne Hooks)

```bash
git clone https://github.com/kimikonapps/kimiflow ~/.claude/skills/kimiflow
```
Gibt dir `/kimiflow` (automatisch erkannt, kein Neustart nötig) — aber **nicht** die Hooks (`hooks.json` lädt nur über das Plugin).

> **Öffentliches Repo** — jeder kann installieren; kein Zugriffsantrag nötig. Der Skill ist **opt-in**: er startet, wenn du ihn verlangst (sag „kimiflow" / „mit kimiflow" / „lauf kimiflow", tippe `/kimiflow` in Claude Code oder nutze `$kimiflow` in Codex) und springt **nicht ungefragt** bei unverwandten Anfragen an. Das steuert die Beschreibung + Urteilsvermögen, keine harte Sperre.

## 30-Sekunden-Demo

![kimiflow-Demo — ein Dark-Mode-Toggle, gebaut durch alle 8 Phasen bis zum Commit-Gate](docs/demo/kimiflow.gif)

> _Illustrative Reko_ — ein Feature (Dark-Mode-Toggle), Gate für Gate gebaut: Klärung → Recherche → Plan → **Plan-Gate** → Umsetzung → Verifikation → Review → **Commit-Gate** (stoppt für dein OK). Gerendert via [`docs/demo/`](docs/demo/); ein echter Mitschnitt ersetzt sie später.

Dieselben Gates an einem **Bug-Fix** — der andere Modus (vollständiger Walkthrough: [`examples/02-risky-bugfix.md`](examples/02-risky-bugfix.md)):

```text
/kimiflow --fix  Token-Refresh wirft, nachdem das Access-Token abgelaufen ist

⚪ Phase 0  Scope-Gate ····· large (betrifft Auth; reproduzierbares Symptom)
🔵 Phase 1  Klärung ········ Symptom? Repro? Erwartet? → PROBLEM.md  ✋ „Passt das so?"
🟣 Phase 2  Diagnose ······· reproduziert den Throw, belegt die Ursache bei auth/refresh.ts:88
            └─ keine belegte Root-Cause ⇒ KEIN Fix. (belegt → weiter)
⚫ Phase 3  Plan ··········· Fix + EARS-Akzeptanzkriterien → PLAN.md
🟡 Phase 4  PLAN-GATE ······ plan-blocker-gate.sh → unabhängige Reviewer → resolve-review-gate.sh
            └─ zählt offene BLOCKER/HIGH, fail-closed, Cap 3 → 0 offen ✅
🟠 Phase 5  Umsetzung ······ erst der fehlschlagende Test (rot) → Fix → grün
🟤 Phase 6  Verifikation ··· Throw weg, Suite grün, gegen die Kriterien geprüft
🟢 Phase 7  Code-Review ···· Ensemble-Kandidaten → Orchestrator verifiziert → Gate zählt bestätigte Findings
            └─ COMMIT-GATE: zeigt den Diff, ✋ STOPPT für dein OK — committet nie selbst
```

Jedes ✋/✅ sowie der Diagnose- und Commit-Stopp ist ein echtes Gate, kein Prompt-Vorschlag. Der Clip oben ist eine gescriptete Illustration — deine eigene aus einem **echten** Lauf nimmst du mit den Schritten unter [`docs/demo/`](docs/demo/) auf.

## Welche Gates mechanisch sind

„Mechanisch" = ein getestetes Script oder ein Hook entscheidet, nicht der Selbstreport des Modells. Die ehrliche Aufteilung:

| Gate | Phase | Mechanismus | Fail-closed? |
|------|-------|-------------|--------------|
| **Clarify-Gate** | 1/4 | verlangt bestätigtes Verhalten, Scope und Ergebnis für small/quick; keine Fragenquote | ✅ ja |
| **Discovery-Gate** | 2/4 | bindet neue Schema-2-Feature-Runs an Discovery-Evidence und erhält Pre-Schema-Resumes | ✅ ja |
| **Planblocker-Gate** | 4 | kombiniert Clarify + Discovery und prüft danach AC-Mapping, Verifikation, Pfade und betroffene Dateien | ✅ ja |
| **Plan-Gate** | 4 | zählt belegte `BLOCKER/HIGH`; small Feature/Fix Cap 2, large/audit Cap 3; Runden werden nie zurückgesetzt | ✅ ja |
| **Code-Review-Gate** | 7 | fokussierte Review-Linsen liefern Kandidaten; der Orchestrator verifiziert und promotet bestätigte Findings; derselbe Resolver zählt offene `BLOCKER/HIGH` | ✅ ja |
| **Commit-Gate** | 7 | STOP + Advisory-Triage; wartet auf dein explizites OK vor jedem Commit | ✅ ja |
| **Secret-Commit-Hook** | jeder Commit | `PreToolUse`-Hook — blockt secret-verdächtige **Pfade** + Bulk-`git add -A`/`.` | ✅ ja |
| **State-Gate-Hook** | Review-Gates | `PreToolUse`-Hook — blockt Resolver-Aufrufe ohne dauerhafte `.kimiflow/<slug>/STATE.md` | ✅ ja |
| **Test-Gate-Hook** (opt-in) | Abschluss | `Stop`-Hook — blockt das Beenden, solange die Projekt-Tests rot sind | ✅ ja |

**Nicht** mechanisch (modell-beurteilt, by design): die Scope-Einstufung, der Root-Cause-Beleg, die Verifikations-Entscheidung und — die ehrliche Grenze — **ob die Kandidaten-Linsen vollständig sind**. Das Gate ist mechanisch *über bestätigte Findings*; es kann nicht beweisen, dass Reviewer alles gefunden haben. kimiflow macht das Gate un-überredbar, und das Ensemble reduziert Blind Spots, ohne Allwissenheit zu behaupten.

## Nutzung

```
/kimiflow                    # kontextbewussten Launcher / Menü öffnen
/kimiflow <feature>          # Feature bauen
/kimiflow <bug>              # Bug fixen (wird automatisch erkannt)
/kimiflow --fix <bug>        # Fix-Modus erzwingen
/kimiflow --verify-feature <feature-or-path>  # eingebautes Feature prüfen; keine Code-Edits
/kimiflow <…> --prepare      # nur vorbereiten (bis Plan-Gate), später umsetzen
/kimiflow --resume <slug>    # vorbereiteten/abgebrochenen Lauf in neuer Session fortsetzen
/kimiflow --project-map quick  # lokale Projektkarte anlegen; Launcher zeigt nur Status
```

Natürliche Kurzmodi:

```text
kimiflow full    # streng: bestätigte Absicht + adaptive Discovery + Plan-Gate + Build-Preview-Freigabe
kimiflow grill   # nur klären/specen; kein Code
kimiflow plan    # Plan + Akzeptanzkriterien vorbereiten; kein Code
kimiflow build   # freigegebenen/vorbereiteten Plan bauen
kimiflow quick   # schlanke kleine Änderung, fragt/bestätigt trotzdem kurz die Absicht
kimiflow review  # read-only Feature-/Diff-Prüfung
kimiflow audit   # read-only Cleanup-/Refactoring-Scan zuerst
kimiflow fix     # Bugflow mit Red/Green-Evidenz
```

Bei `small` und `quick` bestätigt kimiflow Verhalten, Scope und sichtbares Ergebnis. Es gibt keine Mindestzahl an Fragen: Ein vollständiger Auftrag braucht nur eine kompakte Intent-Bestätigung; technische Lücken gehen in die Discovery, nicht zurück an dich.
`small`/`quick` überspringt Memory-Recall und den **Vault Pulse** (beides läuft nur bei `scope=large`); Current-State-Pulse und der Phase-7-Learning-Loop laufen weiterhin bei jedem Scope.

In Codex nutzt du dieselben Argumente mit `$kimiflow`:

```text
$kimiflow
$kimiflow <feature>
$kimiflow full
$kimiflow grill
$kimiflow --fix <bug>
$kimiflow --verify-feature <feature-or-path>
$kimiflow --resume <slug>
$kimiflow --project-map quick
```

## Eingebaute Features prüfen

Nutze `/kimiflow --verify-feature <feature-or-path>` (oder den `review`-Alias), wenn ein Feature schon gebaut ist und
du wissen willst, ob es wirklich richtig verdrahtet ist. Der Modus fährt read-only das normale
Phase-7-Code-Review-Ensemble über den benannten Pfad bzw. den aktuellen Diff — gleiche Lenses und
Verify-vor-Promote-Mechanik wie beim Review eines Build-Runs. Findings landen in `findings/`/`CODE-REVIEW.md`; der
Modus editiert keinen Code, und bestätigte Findings können danach in einen normalen Fix-/Improve-Run gehen.

## Projektstruktur

Das Repository ist bewusst klein und script-first aufgebaut:

- `SKILL.md` und `reference.md` — kanonischer Kimiflow-Workflow und Detailregeln.
- `skills/kimiflow/SKILL.md` — Codex-Wrapper fuer denselben Workflow.
- `.claude-plugin/`, `.codex-plugin/`, `.agents/plugins/`, `hooks.json` — Host-/Plugin-Manifeste.
- `hooks/` — mechanische Gates, Launcher-/Status-Helfer, Memory Router, Obsidian-Setup, Smoke-Checks und
  fokussierte Shell-Tests.
- `docs/` und `examples/` — Demos, Design-Notizen und Beispielablaeufe.
- `.kimiflow/` — lokale Projektintelligenz, Run-State, Memory, Findings und Economics-Daten. Dieser Ordner wird
  bei lokalen Runs erzeugt und standardmaessig nicht committed.

## Aktive Session

Wenn du Kimiflow fuer ein echtes Feature oder einen Fix startest, legt es lokal
`.kimiflow/session/ACTIVE_RUN.json` an. Folgeauftraege wie "bau noch den Button ein" oder "das funktioniert noch
nicht" bleiben im selben Kimiflow-Run, bis der Run abgeschlossen, geparkt, fehlgeschlagen oder abgebrochen ist.

Der Run sammelt kleine Aenderungen in `.kimiflow/<slug>/ITEMS.jsonl`. Kimiflow beendet den Run nicht, solange
Items pending, nur gebaut, rejected oder nach relevanten Datei-Aenderungen stale sind. Positive Learnings landen
nur bei erfolgreichem `finish` im Memory; geparkte, fehlgeschlagene oder abgebrochene Runs schreiben keine
ungeprueften positiven Learnings.

## Launcher

Wenn du kimiflow ohne konkreten Auftrag startest (`/kimiflow` oder `$kimiflow`), öffnet es einen
kontextbewussten Launcher. Der Launcher ruft zuerst `hooks/launcher-status.sh` auf und fasst den
Projektzustand zusammen: eine empfohlene nächste Aktion, installierte/cache Version, aktive Session,
Projektkartenstatus, Memory-/Recall-Status, offene Findings,
Verbesserungs-Slices, Repo-Doku, dirty Working Tree und aktive oder geparkte Runs. Technische Hygiene, die keine
Entscheidung braucht, bleibt im Drilldown statt als Aufgabe vorne aufzutauchen. Deine Auswahl wird danach in den
normalen Kimiflow-Modus geroutet. Er zeigt auch die Kurzmodi (`full`, `grill`, `plan`, `build`, `quick`, `review`,
`audit`, `fix`), damit du den gewünschten Workflow-Modus ohne Flag-Wissen auswählen kannst.

Resume ist abgesichert: Ein geparkter Plan wird nicht blind umgesetzt, wenn betroffene Dateien seit dem
Plan-Commit geändert wurden oder die Plan-Basis unbekannt ist. Dann bietet kimiflow vor Phase 5 eine
Plan-Revalidierung an.

## Project-Map-Bootstrap

Bei nicht-trivialen Läufen kann kimiflow eine empfohlene, aber überspringbare **Projektkarte** anbieten, wenn `.kimiflow/project/INDEX.json` fehlt. Sie legt lokale Projektintelligenz unter `.kimiflow/project/` an: `INDEX.json`, `FACTS.jsonl` und kompakte Markdown-Notizen zu Codebase, Architektur, Konventionen, Tests, Flows und offenen Fragen. Spätere Läufe lesen das zuerst, damit Bugfixes und Features nicht jedes Mal blind starten.

Bootstrap-Auswahl:

- `quick` — die CLI-Bootstrap-Option: Stack, Struktur, Entry Points, zentrale Module, Flows,
  Konventionen und Tests werden in die lokale Map geschrieben.
- `skip` — ohne Projektkarte weiterlaufen.

Der Launcher meldet die fertige Projektkarte per Status, zum Beispiel `Projektkarte: aktuell`. `quick` ist nur Befehlsvokabular fuer den Bootstrap-Schritt, keine sichtbare Map-Stufe. Die Projektkarte ist lokal und optional. Fehlende, übersprungene oder unvollständige Maps blockieren den normalen kimiflow-Loop nie.

Wenn eine Projektkarte existiert, prüft kimiflow sie pro Bereich mit `hooks/project-map-status.sh`.
Bereiche können `current`, `stale`, `potentially_stale` oder `unknown` sein; stale betroffene Bereiche
lösen einen empfohlenen, aber überspringbaren Delta-Refresh aus. Der Refresh aktualisiert nur Hashes und
Commit-Metadaten der ausgewählten Bereiche, damit spätere Läufe die Map ohne Vollscan wiederverwenden.
Sobald wahrscheinlich betroffene Pfade bekannt sind, empfiehlt `project-map-status.sh coverage --affected <pfad>...`
den Phase-2-Detailgrad: `compressed` für gemappte/aktuelle Bereiche, `targeted` für gemappte aber stale/unklare
Bereiche und `full` für unmapped oder fehlende/ungültige Maps.

Standalone-Map-Läufe können außerdem einen Fokus wählen: Codebase, Architektur, Doku oder opt-in
Verbesserungsideen. Das Speicherziel ist explizit: nur `kimiflow`, `kimiflow + Vault` oder
`kimiflow + Vault + Repo-Doku`. Die lokale `.kimiflow/project/`-Map wird immer zuerst geschrieben; Vault
und Repo-Doku sind Publishing-Ebenen, keine Voraussetzung. Verbesserungs-Slices werden als Vorschläge
mit Evidence, Nutzen, Risiko, Aufwand, Akzeptanzkriterien und „Nicht anfassen" geschrieben.

`.kimiflow/project/` ist ein lokaler Agent-Cache und wird standardmäßig nicht committed. Wenn Repo-Doku
angefordert wird, schreibt kimiflow stattdessen eine kuratierte publish-safe Ableitung: Architektur-,
Codebase-, Flow- und Testing-Doku können in die Repo-Doku, konkrete Schwachstellen, Exploit-Pfade,
Secrets, private/lokale Pfade, Vault-Referenzen und rohe Verbesserungs-Findings bleiben lokal oder privat,
außer du verlangst explizit eine sanitisierte öffentliche Notiz.

## Memory Router

Kimiflow hält zusätzlich ein bounded lokales Gedächtnis unter `.kimiflow/project/`: `MEMORY.md`, `USER.md`,
`LEARNINGS.jsonl`, `USER.jsonl`, `MEMORY-INDEX.json`, optional `RECALL.sqlite`, `RECALL.md`,
`RUN-HISTORY.json`, `MEMORY-USAGE.json`, `MEMORY-ECONOMICS.jsonl`, `VAULT-PROVIDER.json`, `VAULT-PREFETCH.md`, `VAULT-SYNC.md`,
`PENDING-PROPOSALS.md`, `PROPOSALS.jsonl` und reviewbare `SKILL-DRAFTS/`; jeder abgeschlossene Run bekommt zusätzlich run-lokale
`LEARNING-REVIEW.md`, `RUN-LIFECYCLE.json`, `RUN-LIFECYCLE.md` sowie ein maschinenlesbares `RECALL.json`, wenn Recall geschrieben wird. Lokale Review-Zusammenfassungen
und kanonische `findings/*.md` sind ueber History/Recall suchbar, bleiben aber standardmaessig lokal. `hooks/memory-router.sh` gibt Launcher und Phase 2 einen günstigen Weg,
Memory-Freshness zu prüfen, relevante Projektfakten abzurufen, neue Learnings zu klassifizieren, die
verpflichtende Run-Abschluss-Review zu schreiben und den Index zu kuratieren, ohne jedes Mal das ganze Repo
oder den ganzen Vault zu lesen. Persistierte Recall-/History-Snapshots werden in `MEMORY-USAGE.json`
gemessen; `status` zeigt kompakte hot/warm/cold/stale Usefulness-Tiers, `recall --write` erklaert,
welche Quellen geladen oder ausgelassen wurden, und abgeschlossene Runs schreiben vorsichtige Token-Effizienz-Schaetzungen in `MEMORY-ECONOMICS.jsonl`;
`memory-router.sh metrics` zeigt die bisherige Usage-Economics unter `.economics`, Run-Economics unter `.run_economics`
und ein globales lokales anonymes Aggregat unter `.global_efficiency`; aeltere Zeilen werden auf die aktuelle
`used_hit_count`-Heuristik normalisiert. Unter 8 aufgezeichneten Runs meldet Kimiflow `insufficient_data`, damit
keine falsche Sparbehauptung entsteht. Das globale Aggregat liegt unter `~/.kimiflow/metrics/token-economics.jsonl`,
kann mit `KIMIFLOW_GLOBAL_METRICS=off` deaktiviert werden und speichert nur Zahlen/Enums plus gesalzene Hash-IDs:
keinen Code, keine Prompts, keine Repo-Namen, keine Dateipfade, keine Vault-Inhalte und keine Learnings im Klartext.
Der Launcher darf daraus eine kompakte Schaetzung wie "geschaetzte Token Savings" anzeigen, aber immer als
Schaetzung, nie als Abrechnungswahrheit. `MEMORY.md` priorisiert häufig genutzte,
vertrauenswürdige, aktuelle publish-safe Learnings statt jede Zeile in den Prompt zu laden.

Diese Schicht ist local-first und funktioniert ohne Vault-MCP. `provider status` erkennt eine laufende
Obsidian Local REST API auf `https://127.0.0.1:27124` / `http://127.0.0.1:27123`, und `provider connect`
schreibt nur `.kimiflow/project/VAULT-PROVIDER.json`. Ein Obsidian API-Key wird nie dort gespeichert.
`provider health` unterscheidet `detected_unconfigured`, `connected_local_only`, `authenticated` und
`auth_failed`, damit der Launcher genau erklären kann, ob Obsidian nur erkannt, lokal verbunden,
lokal API-validiert oder durch direkte MCP-Such-/Write-Tools nutzbar ist. Wenn direkter Vault-MCP-Zugriff
verfügbar ist, kann kimiflow kuratierte Langzeit-Learnings dorthin schreiben; private oder sicherheitsrelevante Details bleiben lokal oder
werden sanitisiert. Run-Abschluss-Learnings sind qualitätsgeprüft und source-freshness-geprüft, damit
vage Notizen und stale Evidence nicht als aktives Projektwissen landen. Evidence-Referenzen werden repo-relativ
gespeichert; Pfade außerhalb des Repos werden zu `OUTSIDE_REPO` zusammengefasst. Wenn sich Evidence ändert,
superseded der Refresh ältere Zeilen und Recall liefert nur aktuelle Learnings. Recall kann zusätzlich bounded
alte Run-Artefakte durchsuchen und schreibt Use-Count/Last-Used-Metriken plus bounded Cost-Events nur dann, wenn ein Recall-/History-
Snapshot gespeichert wird. Memory-Writes werden auf Prompt-Injection/Exfiltration gescannt, User-Präferenzen
liegen in lokalen Profil-Dateien, und `propose`/`consolidate` machen aus Learnings reviewbare Regel-/Skill-
Vorschläge und kompakte Historie. Proposal-State unterstützt `--approve`, `--reject` und `--apply`;
freigegebene Standards/Entscheidungen können lokal in `.kimiflow/` landen, Skill-Kandidaten erzeugen
reviewbare Draft-Notizen statt automatische Skill-Patches. Provider-Sync schreibt ein bounded `VAULT-SYNC.md`
mit nur aktuellen, nicht-privaten, nicht-security Learnings mit frisch verifizierter repo-relativer Evidence,
exportiert standardmäßig maximal 20 Kandidaten, merkt sich nur exportierte IDs lokal und schreibt niemals blind externe Vault-Notizen. Approve/apply prüft Evidence
vorher erneut, stale Vorschläge bleiben lokal bis zum Refresh. Der Launcher zeigt Memory-Budget,
Learning-Zählungen, Usefulness-Zählungen, Run-History-/Usage-/Provider-Health, pending Provider-Sync-Handoffs, pending Proposal
Notifications, Vault-Verfügbarkeit und nur Kuratierungsgründe, bei denen der User wirklich handeln muss. Interne
Schwellen wie `many_learnings` bleiben still, wenn Memory frisch und unter Budget ist.

## Beispiel

**Feature:**
```
/kimiflow Dunkelmodus-Schalter in den Einstellungen
```
1. kimiflow bestätigt Verhalten, Scope und Ergebnis → `INTENT.md`
2. versteht den Code, bestimmt die Discovery-Tiefe und recherchiert nur planverändernde Lücken → `RESEARCH.md`
3. interner Plan + Gate → verständliche Build Preview → Build beziehungsweise Risiko-Freigabe → Verifikation → Review
4. zeigt den Diff und **wartet auf dein OK vor dem Commit**

**Bug-Fix:**
```
/kimiflow --fix App stürzt ab beim Öffnen eines leeren Projekts
```
1. klärt das Problem (Symptom, Reproduktion) → `PROBLEM.md`
2. **reproduziert den Crash**, **belegt die Ursache** (`file:line`), **recherchiert den korrekten Fix** → `DIAGNOSIS.md`. Ohne belegte Ursache wird **nicht** gefixt.
3. fixt → verifiziert, dass der Crash weg ist + keine Regression → Code-Review → **Stopp vor dem Commit**

## Ablauf (8 Phasen)

Scope-Gate → **Intent bestätigen** → **projektbasierte adaptive Discovery** (`none|pulse|focused`, standardmäßig kein Worker) beziehungsweise **Diagnose** → **minimum-complete interner Plan** → **Plan-Gate** (small Cap 2, large/audit Cap 3) → **Build Preview / bedingtes Risk Gate** → **Umsetzung** → **Verifikation** → **Code-Review** → **Commit**.

State wird nach `.kimiflow/<slug>/` im Zielprojekt persistiert (resume-fähig). `small`/`quick` bleibt schlank, überspringt aber Phase 1 nicht: kimiflow fragt oder bestätigt genug, damit es nicht am eigentlichen Wunsch vorbeibaut.
`small`/`quick` macht außerdem einen winzigen Current-State-Pulse: lokale Arbeit dokumentiert "keine externe Aktualitätsprüfung nötig"; geänderte APIs/Tooling/Hosts bekommen vor dem Plan eine aktuelle Primärquelle.
`small`/`quick` überspringt Memory-Recall und den Vault Pulse — beides läuft nur bei `scope=large` (der Recall-Payoff ist bei kleinen Tasks dünn); der Phase-7-Learning-Loop läuft weiterhin bei jedem Scope.

> **Kosten:** Discovery `none|pulse` startet standardmäßig keinen Research-Worker; `focused` nutzt normalerweise einen begrenzten Evidence-Worker und höchstens zwei unabhängige Lanes. Das Top-Modell behält Assessment, Synthese, Entscheidungen, Planung und Review. Auch `large` nutzt standardmäßig nur einen Planner.

## Prinzipien

- **Simplicity-first** — Komplexität skaliert mit der Arbeit (Scope-Gate).
- **Binäre Kein-Blocker-Gates**, nie ein numerischer Score.
- **Evidence-before-assertion** — gegen Specs verifizieren, nicht gegen Bauchgefühl.
- **Fix-Modus:** Root-Cause belegen und den korrekten Fix recherchieren *bevor* gefixt wird (das Modell ist evtl. nicht am aktuellen Stand).
- **Farbige Phasen-Marker** — jede der 8 Phasen meldet sich mit eigener Farbe (⚪🔵🟣⚫🟡🟠🟤🟢), damit ein Lauf in Claude Code auf einen Blick lesbar ist.

Details in [`reference.md`](reference.md).

## Hooks (mitgeliefert)

kimiflow bringt Sicherheits-Hooks unter `hooks/` mit, **nur in kimiflow-Repos aktiv** (ein `.kimiflow/`-Verzeichnis am Git-Root) — also nie in fremden Projekten:

- **`commit-secret-gate`** — **Dateiname/Pfad-Hygiene, keine Secret-im-Quelltext-Erkennung**: blockt einen `git commit`, der einen secret-verdächtigen **Pfad** stagen würde (`.env`/`.envrc` inkl. `prod.env`-artiger Suffixe, `*.pem/.key/.p12/.pfx/.asc`, private SSH-Keys `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519` (nicht `.pub`), `.npmrc`, `secret`/`credential`/`access_token`/`auth_token`-Pfade), sowie jedes Bulk-`git add -A`/`.`. Er matcht **Pfade, nie Datei-Inhalte** — ein in den Quelltext gepasteter Key passiert — also ergänze ihn mit einem Content-Scanner für Secrets im Code. kimiflows Advisory `secret-content-scan.sh` macht genau das: **`gitleaks protect --staged`** ist der saubere Staged-Content-Pfad; **trufflehog** ist ein Best-effort-Fallback (kein nativer Staged-Mode — scannt Commits seit `HEAD`).
- **`state-gate`** — blockt Review-Gate-Resolver-Aufrufe, wenn einem nicht-trivialen kimiflow-Lauf die dauerhafte `STATE.md` fehlt; dadurch lebt Resume-/Gate-State nicht nur im Chat.
- **`clarify-gate`** — verlangt bestätigtes Verhalten, Scope und Ergebnis im aktuellen Run, aber keine Mindestzahl an Fragen.
- **`discovery-gate`** — prüft Phase-2-Vollständigkeit und Quellenform vor der Planung, ohne semantische Allwissenheit vorzutäuschen.
- **`test-gate`** (opt-in) — blockt das Beenden, solange die Projekt-Tests rot sind; pro Projekt via **lokaler, untracked** `.kimiflow/test-gate`-Datei aktivieren (für `large`-Läufe automatisch). Ein git-getrackter (committeter) Marker wird abgelehnt — seine erste Zeile wird `eval`'t, committete Marker können so nicht als Drive-by laufen.

## Vault-Memory-Schicht (optional, aber empfohlen)

kimiflow kann einen **Obsidian-Vault als projektübergreifende Wissensbasis** nutzen. Es erkennt Obsidian automatisch, wenn die Local REST API läuft, verbindet sie lokal und schreibt reviewbare Prefetch-/Sync-Handoffs für wiederverwendbare Erkenntnisse. Mit authentifiziertem MCP-Tool-Zugriff kann Phase 2 zusätzlich **deinen Vault vor dem Recherchieren durchsuchen** (damit es nie neu recherchiert, was du schon gelernt hast). Über viele Projekte hinweg wächst das zu einem persönlichen, durchsuchbaren Gedächtnis, das jeden Lauf schneller und fundierter macht. **Das Einrichten lohnt sich wirklich.**

**Ohne Vault-MCP — nichts bricht.** kimiflow kann eine laufende Obsidian-App trotzdem erkennen und lokale `VAULT-PROVIDER.json` / `VAULT-SYNC.md`-Handoffs erstellen, überspringt aber direkte Vault-Suche + -Save und läuft weiter. Recherche fällt auf Codebase + Web zurück, und die **repo-lokale `.kimiflow/`-Memory** (`STANDARDS.md` / `DECISIONS.md`) persistiert weiterhin projektbezogenes Lernen. Keine Fehler, keine blockierten Phasen — identische Gates, Hooks und Ergebnisqualität; nur die direkte projektübergreifende Abkürzung fehlt bis ein authentifizierter MCP konfiguriert ist.

**Zweite optionale Quelle — claude-mem.** Ist das **claude-mem**-Plugin (cross-session Memory) installiert, durchsucht kimiflow es in Phase 2 **zusätzlich** beim Recall ("hatten wir das schon mal?") — **nur lesend**; gespeichert wird weiterhin in den Vault / die repo-lokale `.kimiflow/`-Memory. Nicht installiert → übersprungen, exakt wie der Vault. **Erkennung pro Run**, ein späteres Nachrüsten wird also beim nächsten Lauf erkannt (nach `/reload-plugins` oder Neustart). Beide sind unabhängig — eines, beides oder keines.

### Setup — damit die Vault-Schicht wirklich funktioniert

1. **Obsidian installieren:** <https://obsidian.md> — Vault öffnen oder anlegen.
2. **Das *Local REST API*-Plugin aktivieren** ([coddingtonbear/obsidian-local-rest-api](https://github.com/coddingtonbear/obsidian-local-rest-api)): Obsidian → Einstellungen → Community-Plugins → installieren & aktivieren. Obsidian laufen lassen; kimiflow erkennt den Standard-HTTPS-Endpunkt `https://127.0.0.1:27124` automatisch und bietet die Verbindung an.
3. **Optional, für direkte Vault-Reads/Writes:** den eingebauten MCP-Endpunkt der Local REST API verwenden. Der einfachste Weg öffnet einen Terminal-Wizard, damit der API-Key nicht im Chat landet:
   ```bash
   hooks/vault-mcp-open-terminal.sh --host codex
   ```
   Auf macOS schreibt der Wizard die user-level Codex-MCP-Konfig, speichert den Key im Keychain, setzt die Launch-Umgebung für neu geöffnete Codex-Fenster, prüft die lokale REST API und testet, ob der MCP-Endpunkt initialisiert. Für Claude Code nutze `--host claude`; für beide Hosts `--host all`.
4. **Manueller/CLI-Fallback:** `hooks/vault-mcp-setup.sh --host all --interactive` im eigenen Terminal starten, oder `hooks/vault-mcp-setup.sh --host all` nutzen, um Codex- und Claude-Code-Snippets für `https://127.0.0.1:27124/mcp/` zu drucken. Er druckt, committet und speichert den API-Key nie in `.kimiflow/`.
5. **MCP-Client neu starten/neu laden** und **Obsidian während eines kimiflow-Laufs laufen lassen**.

**Wenn HTTPS-Zertifikatsvertrauen fehlschlägt:** Das Local-REST-API-Plugin nutzt ein lokales Zertifikat für
`https://127.0.0.1:27124`. Der Wizard erkennt das und zeigt die Lösung. Öffne/lade
`https://127.0.0.1:27124/obsidian-local-rest-api.crt`, füge es zum macOS-**System**-Schlüsselbund hinzu, öffne
das Zertifikat, setze SSL-Vertrauen auf **Immer vertrauen**, bestätige, starte Obsidian und Codex/Claude Code neu
und führe den Wizard erneut aus. Ein gesunder Check verifiziert `/mcp/` ohne unsicheren TLS-Bypass. Wenn du das
Zertifikat bewusst nicht vertrauen möchtest, nutze den lokalen Fallback `--url http://127.0.0.1:27123`.

Der frictionless Pfad ist: Obsidian erkennen → `provider connect` → `provider health` → Terminal-Setup-Wizard → lokale
`VAULT-PREFETCH.md` / `VAULT-SYNC.md`-Handoffs schreiben. Direkte Notizsuche/-writes nutzen
authentifizierte Vault-MCP-Tools, zum Beispiel die eingebauten Local-REST-API-Tools `search_simple`,
`vault_read`, `vault_append`/`vault_write` oder kompatible Legacy-`obsidian_*`-Tools, erst sobald der Host sie
bereitstellt. Eine `OBSIDIAN_API_KEY`-Umgebungsvariable kann die lokale
REST-API für Health-Checks validieren, direkte Search/Writes bleiben aber deaktiviert, bis wirklich ein
Tool-Provider vorhanden ist.
