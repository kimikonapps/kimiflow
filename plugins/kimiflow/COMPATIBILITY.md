# Compatibility — host primitives kimiflow depends on

kimiflow is, at its core, a large prompt-program riding on host plugin / skill / hook /
subagent contracts. If a host moves one of these primitives, parts of kimiflow can break **silently**
(a hook that stops firing looks identical to a hook that passed). This file lists every primitive
kimiflow concretely uses, what breaks if it changes, and a smoke checklist to run at each version bump.

**Last verified against:** Claude Code **2.1.202** · Codex CLI **0.142.5** · kimiflow **0.2.13** · 2026-07-22.

> **0.x expectation.** These primitives are NOT a stable public contract. Treat breakage as *expected*
> across Claude Code or Codex minor versions until a version is explicitly pinned — keep the README's
> pre-1.0 warning and re-run the smoke checklist below on every host upgrade.

## Claude Code primitives used

Load-bearing = a change breaks core behavior. Graceful = absence degrades a feature but the default
loop still runs.

| Primitive | Where kimiflow uses it | If it changes |
|-----------|------------------------|---------------|
| Plugin manifest `.claude-plugin/plugin.json` (`name`/`description`/`version`/`license`/`author`) | plugin packaging + version source of truth | **Load-bearing** — schema/field rename → plugin won't load |
| Marketplace manifest `.claude-plugin/marketplace.json` | install / listing | **Load-bearing** — schema change → install fails |
| Skill frontmatter `name` / `description` / `argument-hint` + `$ARGUMENTS` substitution | `SKILL.md` header + `## Modes` | **Load-bearing** — substitution/field change → args & routing break |
| `disable-model-invocation: false` | `SKILL.md` frontmatter (model-invocable; smart feature routing and explicit direct/Kimiflow overrides live in the `description`) | **Load-bearing** — if forced back to `true`, the model cannot auto-route substantial features or launch Kimiflow on request; routing remains description-guided judgment rather than a hard host rule |
| Slash invocation `/kimiflow` | user entry point | **Load-bearing** — command-routing change |
| Hook event `PreToolUse` (matcher `Bash`) | `hooks/hooks.json` → `commit-secret-gate.sh` | **Load-bearing** — event/matcher rename → secret gate silently stops gating |
| Hook event `Stop` | `hooks/hooks.json` → `test-gate.sh` | **Load-bearing** — event rename → test-gate silently stops gating |
| Hook `type: command` + JSON-on-stdin contract (`cwd`, tool input, `stop_hook_active`) | both hook scripts (jq-parsed) | **Load-bearing** — stdin-schema change → hooks misparse (they fail-closed, but may over-block) |
| Hook deny/decision output contract | `commit-secret-gate.sh` `emit_deny` | **Load-bearing** — output-contract change → blocks stop taking effect |
| Env `${CLAUDE_PLUGIN_ROOT}` | `hooks.json` command paths + `SKILL.md` resolver calls | **Load-bearing** — unset/rename → resolver scripts unfound |
| Env `${CLAUDE_SKILL_DIR}` | `SKILL.md` resolver fallback + `reference.md` path passing to subagents | **Load-bearing** — unset/rename → fallback path breaks |
| `TaskCreate` / `TaskUpdate` | Phase 0 glance task-list widget | Graceful — API change breaks the widget only; engine + STATE.md unaffected |
| Subagent spawning (fresh, isolated context) | optional fresh understand / plan / review / verify seats | Graceful — unavailable routing falls back to separate passes by the strongest current orchestrator; candidate grammar and mechanical gates remain load-bearing |
| Named agent types `general-purpose` · `Explore` · `Plan` plus fresh axis-specific review prompts | research / plan / review / explore delegations | Graceful-ish — rename/removal needs a fallback type, but is recoverable; review correctness depends on the candidate grammar and axis prompt, not retired custom reviewer names |
| Git worktree/status porcelain + POSIX file locking for registry mutations | `workspace-preflight.sh` complete inventory, identity receipt, one-tree cap, and atomic archive-before-detach retirement | **Load-bearing for exceptional-worktree writes** — format/command/lock/atomic-rename loss fails closed; normal read-only inventory remains available and there is no destructive fallback |
| External `codex` CLI (optional) | pinned `gpt-5.6-sol` cross-family quality seat (Claude-host tier 1) | Graceful — absent → tier skipped; model/effort flags changing requires a transport update |
| External `agy` (Antigravity) CLI (optional) | cross-family reviewer knob (Claude-host Gemini tier); invoked `agy -p … --sandbox --model "Gemini 3.5 Flash (High)"` | Graceful — absent → tier skipped, chain falls to same-family. `--sandbox`+no-tools required (unconstrained `agy` is agentic); output validated by the FINDING-grammar backstop |
| External `python3` >= 3.9 (stdlib only) | `hooks/memory-router.sh` is a shim that execs `python3 -m memory_router` (the memory-router runtime: status / recall / record / curate / review-run / provider / …) | **Load-bearing** — absent or < 3.9 → the memory-router hook fails; its callers (`launcher-status.sh`, `active-run.sh`) fail closed and degrade gracefully. Replaces the former in-process Bash runtime (which required `jq`). |
| Python `dir_fd` + `O_NOFOLLOW` and read-only Git `rev-parse --path-format=absolute` / `--absolute-git-dir` | Workspace-aware Recall package-boundary receipts and optional salted repository/worktree pseudonyms | **Graceful** — unsupported or unstable path/Git identity disables narrowing or identity output and falls back project-wide; it never creates or mutates a worktree. |
| `WebSearch` / context7 / `WebFetch` (via subagents) | Phase 2 external research | Graceful — absent → research degrades, vault/codebase still ground the plan |
| Optional notes MCP (e.g. Obsidian) | Phase 2 vault memory | Graceful — absent → skip + note in STATE.md |

## Codex primitives used

Load-bearing = a change breaks core behavior. Graceful = absence degrades a feature but the default
loop still runs.

| Primitive | Where kimiflow uses it | If it changes |
|-----------|------------------------|---------------|
| Plugin manifest `.codex-plugin/plugin.json` | Codex plugin packaging + version source of truth | **Load-bearing** — schema/field rename → plugin won't load |
| Repo marketplace `.agents/plugins/marketplace.json` | Codex repo-local install/listing | **Load-bearing** — marketplace schema/path resolution change → install fails |
| Skill frontmatter `name` / `description` | `skills/kimiflow/SKILL.md` | **Load-bearing** — skill not discoverable or smart-routing boundary not visible |
| Skill metadata `skills/kimiflow/agents/openai.yaml` | Codex app/plugin presentation metadata | Graceful — display metadata degrades, engine unaffected |
| Explicit skill invocation `$kimiflow` / `@kimiflow` / named request | user entry point | **Load-bearing** — if explicit skill invocation changes, users need new invocation docs |
| Manifest hook path `.codex-plugin/plugin.json` → `./hooks/hooks.json` | Codex loads the bundled lifecycle contract directly | **Load-bearing** — missing/path/schema drift means the safety hooks are not registered; `install-codex-hooks.sh --check` validates without writing user files |
| Hook command root pin `KIMIFLOW_PLUGIN_ROOT` | bundled commands delegate to scripts inside the installed plugin | **Load-bearing** — stale/missing root means the declared commands cannot find the tested scripts |
| Root compatibility mirror `hooks.json` | legacy/experimental host wiring outside the manifest-declared Codex path | Graceful — the load-bearing Codex path is `hooks/hooks.json`; tests keep the shared command contract aligned |
| Hook event `PreToolUse` | bundled contract → intake, active-run, commit-secret, state and test gates across Bash/edit/plan/intake tools | **Load-bearing** — matcher or command drift silently removes enforcement |
| Hook event `PostToolUse` / `Stop` | bundled contract → native intake receipt plus test and map-staleness gates | **Load-bearing** — response capture or terminal gates stop firing |
| Hook trust review (`/hooks`) | Codex requires non-managed plugin command hooks to be trusted and re-reviews changed definitions | **Load-bearing for safety** — untrusted hooks are skipped until reviewed; this is a one-time install/update security action, never a per-run continuation gate |
| Hook JSON-on-stdin contract (`cwd`, command fields, stop-active fields) | hook scripts parse Codex-shaped payloads plus Claude-shaped payloads | **Load-bearing** — scripts may misparse; gate-critical paths fail safe where possible |
| Hook deny/block output contract | `emit_deny` and `test-gate.sh` block output | **Load-bearing** — blocks stop taking effect |
| `KIMIFLOW_HOST=codex` | Codex skill and bundled hook commands invoke helpers with Codex-specific global config paths | Graceful-ish — without it global verbosity writes to Claude default; project gates still work |
| Codex plan/status updates | Phase 0 glance task-list equivalent | Graceful — UI progress degrades; `STATE.md` remains the durable source |
| Codex subagents (`explorer`, `worker`, `default`) + per-spawn `model`/`reasoning_effort` | Luna bounded support / Terra implementation / Sol plan-review-verify roles | Graceful quality routing — unavailable overrides inherit the active session tier; a non-Sol main session records the fallback and continues without a model-switch prompt |
| Codex web/search/tool availability | Phase 2 current external research | Graceful — absent → research degrades, codebase/project memory still ground the plan |
| Optional notes MCP / app connectors | Phase 2 recall and vault memory | Graceful — absent → skip + note in STATE.md |
| `codex exec --json` + `codex exec resume <SESSION_ID>` (optional) | `hooks/kimiflow_core/runner.py` starts and autonomously continues the optional terminal runner in one owning Codex thread | **Load-bearing only for the optional terminal runner** — missing JSONL `thread.started`, changed resume syntax, or lost thread persistence fails closed with a resumable/error receipt; embedded Codex and Claude flows remain unaffected |
| `codex exec --sandbox workspace-write` + config `approval_policy="never"` (optional) | bounds terminal-run writes to the project without routine approval prompts | **Load-bearing only for the optional terminal runner** — flag/config drift must fail closed and be updated before advertising autonomous terminal use; Kimiflow never falls back to unrestricted access |
| Command-adapter protocol v1 (`capabilities --json`, JSON-stdio `start|resume`) | tool-capable local/remote coding-agent harnesses, including local models | **Load-bearing only for a non-Codex terminal adapter** — all five capabilities (`files`, `shell`, `tests`, `resume`, `gates`) are mandatory; invalid identity/events/usage fail closed, and resume requires the identical adapter + host |

## Shared local runtime primitives

| Primitive | Where kimiflow uses it | If it changes |
|-----------|------------------------|---------------|
| Same-filesystem atomic path exchange (`renamex_np(RENAME_SWAP)` on macOS or `renameat2(RENAME_EXCHANGE)` on Linux) | Reversible Memory Lifecycle compare/publish/restore for `LEARNINGS.jsonl` | **Load-bearing only for `lifecycle --write` / `--restore --write`** — an unavailable primitive returns a bounded nonzero refusal before mutation; preview, recall, record, Capsule export, and the rest of Kimiflow remain available. Conflict recovery uses only bounded exchanges, so the canonical path is never removed; a persistently racing or failing filesystem keeps the extra version as an explicit local recovery copy. The release smokes assert that this compatibility boundary stays declared. |
| POSIX `flock`, `dir_fd`, `O_NOFOLLOW`, atomic rename and JSON stdio | Unified local run control plane (`run-bridge.sh`), phase-context shadow and scorecard | **Load-bearing only for local bridge writes** — missing owner identity, lock support, pinned file safety, valid cursor or exact action receipt fails closed. Read-only embedded Kimiflow remains available; there is no unlocked or network fallback. |
| Read-only Git object commands (`cat-file`, `ls-tree`) honoring `GIT_NO_REPLACE_OBJECTS`, `GIT_NO_LAZY_FETCH` and `GIT_ALLOW_PROTOCOL` | Offline behavioral-eval receipts resolve scenarios and rule citations from their declared source commit | **Load-bearing only for receipt validation** — replacement indirection is disabled and network protocols are denied; a missing local commit/tree/blob fails closed. Git versions that ignore lazy-fetch suppression remain network-blocked by the protocol allowlist, but should be upgraded before treating partial-clone receipt validation as supported. |
| Allowlisted marketplace candidate + `RUNTIME-FINGERPRINT.json` | publish-safe plugin contents and same-version cache freshness | **Load-bearing for releases** — inventory/content/mode drift blocks the manual release check; private maintainer/run data is never copied into the candidate |

## Version-bump smoke checklist

Run on every Claude Code or Codex upgrade (and at each kimiflow release):

1. **CI hard gates** — `bash hooks/ci-test-plan.sh run full` on Ubuntu plus
   `bash hooks/ci-test-plan.sh run portability` on macOS; `bash -n hooks/*.sh`; `jq -e .` on all Claude and
   Codex JSON manifests; `bash hooks/smoke-install.sh`; and `bash hooks/smoke-install-codex.sh`. The test
   planner classifies every `hooks/test-*.sh` surface, rejects missing parity dependencies and real skips,
   and keeps focused/retired migration helpers out of the full lane only when their full-suite replacement
   exists. (Enforced by `.github/workflows/ci.yml`.)
2. **Claude resolvers run installed** — `/kimiflow --settings` resolves (exercises `resolve-verbosity.sh` /
   `resolve-build-gate.sh` via `${CLAUDE_PLUGIN_ROOT}`).
3. **Claude hooks fire installed** — in a repo with a `.kimiflow/` dir, confirm `commit-secret-gate.sh` blocks
   a `git add .` and the `Stop` test-gate engages (path resolves through `${CLAUDE_PLUGIN_ROOT}`).
4. **Codex plugin install/invocation** — add the repo marketplace, run
   `bash hooks/install-codex-hooks.sh --check`, install kimiflow through the Codex plugin browser/app, start
   a new thread, and run `$kimiflow <tiny fix>`.
5. **Codex hooks fire installed** — in a repo with a `.kimiflow/` dir, confirm `commit-secret-gate.sh`
   blocks `git add .` and the `Stop` test-gate engages through the manifest-declared bundled hooks.
6. **One trivial Claude end-to-end** — `/kimiflow <tiny fix>`: the Phase-0 task widget appears, workspace preflight is compact, and schema 4 commits named paths locally without a routine second OK; the opt-in policy holds — kimiflow launches when asked ("with kimiflow")
   but does not fire unprompted on an unrelated request (soft, description-guided — not a hard flag).
7. **One trivial Codex end-to-end** — `$kimiflow <tiny fix>`: the Codex status/plan view and workspace summary appear, the local commit needs no routine second OK, and the opt-in policy holds.
8. **Re-stamp** — update the "Last verified against" line above with the new `claude --version` and
   `codex --version`.
9. **Optional terminal controller** — install it into a temporary prefix, run `--check`, then execute
   `bash hooks/test-kimiflow-runner.sh`; confirm one fake two-turn run resumes the identical thread, material
   waits return 3, and missing activation fails closed.

Anything that fails here is an upstream-compatibility break — record it in the CHANGELOG and pin or
work around before release.
