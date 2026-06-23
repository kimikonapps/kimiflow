# Compatibility — Claude Code primitives kimiflow depends on

kimiflow is, at its core, a large prompt-program riding on Claude Code's plugin / skill / hook /
subagent contract. If Anthropic moves one of these primitives, parts of kimiflow can break **silently**
(a hook that stops firing looks identical to a hook that passed). This file lists every primitive
kimiflow concretely uses, what breaks if it changes, and a smoke checklist to run at each version bump.

**Last verified against:** Claude Code **2.1.186** · kimiflow **0.1.11** · 2026-06-23.

> **0.x expectation.** These primitives are NOT a stable public contract. Treat breakage as *expected*
> across Claude Code minor versions until a version is explicitly pinned — keep the README's pre-1.0
> warning and re-run the smoke checklist below on every Claude Code upgrade.

## Primitives used

Load-bearing = a change breaks core behavior. Graceful = absence degrades a feature but the default
loop still runs.

| Primitive | Where kimiflow uses it | If it changes |
|-----------|------------------------|---------------|
| Plugin manifest `.claude-plugin/plugin.json` (`name`/`description`/`version`/`license`/`author`) | plugin packaging + version source of truth | **Load-bearing** — schema/field rename → plugin won't load |
| Marketplace manifest `.claude-plugin/marketplace.json` | install / listing | **Load-bearing** — schema change → install fails |
| Skill frontmatter `name` / `description` / `argument-hint` + `$ARGUMENTS` substitution | `SKILL.md` header + `## Modes` | **Load-bearing** — substitution/field change → args & routing break |
| `disable-model-invocation: true` | `SKILL.md` frontmatter (manual-only `/kimiflow`) | **Load-bearing** — semantics change → kimiflow could auto-trigger unprompted |
| Slash invocation `/kimiflow` | user entry point | **Load-bearing** — command-routing change |
| Hook event `PreToolUse` (matcher `Bash`) | `hooks/hooks.json` → `commit-secret-gate.sh` | **Load-bearing** — event/matcher rename → secret gate silently stops gating |
| Hook event `Stop` | `hooks/hooks.json` → `test-gate.sh` | **Load-bearing** — event rename → test-gate silently stops gating |
| Hook `type: command` + JSON-on-stdin contract (`cwd`, tool input, `stop_hook_active`) | both hook scripts (jq-parsed) | **Load-bearing** — stdin-schema change → hooks misparse (they fail-closed, but may over-block) |
| Hook deny/decision output contract | `commit-secret-gate.sh` `emit_deny` | **Load-bearing** — output-contract change → blocks stop taking effect |
| Env `${CLAUDE_PLUGIN_ROOT}` | `hooks.json` command paths + `SKILL.md` resolver calls | **Load-bearing** — unset/rename → resolver scripts unfound |
| Env `${CLAUDE_SKILL_DIR}` | `SKILL.md` resolver fallback + `reference.md` path passing to subagents | **Load-bearing** — unset/rename → fallback path breaks |
| `TaskCreate` / `TaskUpdate` | Phase 0 glance task-list widget | Graceful — API change breaks the widget only; engine + STATE.md unaffected |
| Subagent spawning (fresh, isolated context) | every delegated phase (understand / plan / review / verify) | **Load-bearing** — spawn-model change → the whole delegation loop breaks |
| Named agent types `general-purpose` · `Explore` · `Plan` · `code-review-audit` · `senior-reviewer` | research / plan / review / explore delegations | Graceful-ish — rename/removal needs a fallback type, but is recoverable |
| Subagent `isolation: worktree` | parallel-implementation knob (opt-in, OFF by default) | Graceful — breaks the parallel knob only; default sequential path unaffected |
| External `codex` CLI (optional) | cross-family reviewer knob | Graceful — absent → knob simply unavailable |
| `WebSearch` / context7 / `WebFetch` (via subagents) | Phase 2 external research | Graceful — absent → research degrades, vault/codebase still ground the plan |
| Optional notes MCP (e.g. Obsidian) | Phase 2 vault memory | Graceful — absent → skip + note in STATE.md |

## Version-bump smoke checklist

Run on every Claude Code upgrade (and at each kimiflow release):

1. **CI hard gates** — `bash -n hooks/*.sh` + the seven unit-test scripts green; `jq -e .` on all three
   JSON manifests; and `bash hooks/smoke-install.sh` (structural install check: manifests, skill
   frontmatter, hook wiring, and a synthetic gate-fires probe). (Enforced by `.github/workflows/ci.yml`.)
2. **Resolvers run installed** — `/kimiflow --settings` resolves (exercises `resolve-verbosity.sh` /
   `resolve-build-gate.sh` via `${CLAUDE_PLUGIN_ROOT}`).
3. **Hooks fire installed** — in a repo with a `.kimiflow/` dir, confirm `commit-secret-gate.sh` blocks
   a `git add .` and the `Stop` test-gate engages (path resolves through `${CLAUDE_PLUGIN_ROOT}`).
4. **One trivial end-to-end** — `/kimiflow <tiny fix>`: the Phase-0 task widget appears, the commit-gate
   STOPs for explicit OK, and `disable-model-invocation` still holds (no auto-trigger).
5. **Re-stamp** — update the "Last verified against" line above with the new `claude --version`.

Anything that fails here is an upstream-compatibility break — record it in the CHANGELOG and pin or
work around before release.
