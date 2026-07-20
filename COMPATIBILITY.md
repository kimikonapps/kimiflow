# Compatibility — host primitives kimiflow depends on

kimiflow is, at its core, a large prompt-program riding on host plugin / skill / hook /
subagent contracts. If a host moves one of these primitives, parts of kimiflow can break **silently**
(a hook that stops firing looks identical to a hook that passed). This file lists every primitive
kimiflow concretely uses, what breaks if it changes, and a smoke checklist to run at each version bump.

**Last verified against:** Claude Code **2.1.202** · Codex CLI **0.142.5** · kimiflow **0.2.8** · 2026-07-20.

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
| Stable Codex hook directory `${CODEX_HOME:-~/.codex}/hooks` | `hooks/install-codex-hooks.sh` writes Kimiflow wrappers here | **Load-bearing** — if Codex stops scanning this hook directory, secret/state/test gates stop firing |
| Hook wrapper root pin `KIMIFLOW_PLUGIN_ROOT` | installed Codex wrappers delegate back to this plugin checkout | **Load-bearing** — stale/missing root means wrappers cannot find the tested scripts |
| Root hook manifest `hooks.json` | optional Codex plugin lifecycle hook wiring for builds with `plugin_hooks` enabled | Graceful/experimental — safety gates rely on stable hook wrappers, not this path |
| Hook event `PreToolUse` (matcher `Bash`) | stable wrappers and optional root `hooks.json` → `commit-secret-gate.sh` + `state-gate.sh` | **Load-bearing** — secret/state gates silently stop gating |
| Hook event `Stop` | stable wrapper and optional root `hooks.json` → `test-gate.sh` | **Load-bearing** — hard test-gate silently stops gating |
| Hook trust review | Codex may require non-managed command hooks to be trusted before running | **Load-bearing for safety** — untrusted hooks are skipped until reviewed |
| Hook JSON-on-stdin contract (`cwd`, command fields, stop-active fields) | hook scripts parse Codex-shaped payloads plus Claude-shaped payloads | **Load-bearing** — scripts may misparse; gate-critical paths fail safe where possible |
| Hook deny/block output contract | `emit_deny` and `test-gate.sh` block output | **Load-bearing** — blocks stop taking effect |
| `KIMIFLOW_HOST=codex` | Codex skill and stable hook wrappers invoke helpers with Codex-specific global config paths | Graceful-ish — without it global verbosity writes to Claude default; project gates still work |
| Codex plan/status updates | Phase 0 glance task-list equivalent | Graceful — UI progress degrades; `STATE.md` remains the durable source |
| Codex subagents (`explorer`, `worker`, `default`) + per-spawn `model`/`reasoning_effort` | Luna bounded support / Terra implementation / Sol plan-review-verify roles | Graceful quality routing — unavailable overrides inherit the active session tier; a non-Sol main session records the fallback and continues without a model-switch prompt |
| Codex web/search/tool availability | Phase 2 current external research | Graceful — absent → research degrades, codebase/project memory still ground the plan |
| Optional notes MCP / app connectors | Phase 2 recall and vault memory | Graceful — absent → skip + note in STATE.md |
| `codex exec --json` + `codex exec resume <SESSION_ID>` (optional) | `hooks/kimiflow_core/runner.py` starts and autonomously continues the optional terminal runner in one owning Codex thread | **Load-bearing only for the optional terminal runner** — missing JSONL `thread.started`, changed resume syntax, or lost thread persistence fails closed with a resumable/error receipt; embedded Codex and Claude flows remain unaffected |
| `codex exec --sandbox workspace-write` + config `approval_policy="never"` (optional) | bounds terminal-run writes to the project without routine approval prompts | **Load-bearing only for the optional terminal runner** — flag/config drift must fail closed and be updated before advertising autonomous terminal use; Kimiflow never falls back to unrestricted access |

## Version-bump smoke checklist

Run on every Claude Code or Codex upgrade (and at each kimiflow release):

1. **CI hard gates** — `bash -n hooks/*.sh` + all `hooks/test-*.sh` unit-test scripts green; `jq -e .` on all
   Claude JSON manifests, Codex JSON manifests, `bash hooks/smoke-install.sh`, and
   `bash hooks/smoke-install-codex.sh` (structural install checks: manifests, skill frontmatter,
   stable hook wrapper install into a temp `CODEX_HOME`, optional plugin hook wiring, and synthetic
   gate-fires probes). (Enforced by `.github/workflows/ci.yml`.)
2. **Claude resolvers run installed** — `/kimiflow --settings` resolves (exercises `resolve-verbosity.sh` /
   `resolve-build-gate.sh` via `${CLAUDE_PLUGIN_ROOT}`).
3. **Claude hooks fire installed** — in a repo with a `.kimiflow/` dir, confirm `commit-secret-gate.sh` blocks
   a `git add .` and the `Stop` test-gate engages (path resolves through `${CLAUDE_PLUGIN_ROOT}`).
4. **Codex plugin install/invocation** — add the repo marketplace, run
   `bash hooks/install-codex-hooks.sh`, install kimiflow through the Codex plugin browser/app, start
   a new thread, and run `$kimiflow <tiny fix>`.
5. **Codex hooks fire installed** — in a repo with a `.kimiflow/` dir, confirm `commit-secret-gate.sh`
   blocks `git add .` and the `Stop` test-gate engages through the stable Codex hook wrappers.
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
