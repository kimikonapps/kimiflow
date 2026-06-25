---
name: kimiflow
description: "Codex port of the Kimiflow feature and bug-fix loop. Use ONLY when the user explicitly asks for Kimiflow, says \"with kimiflow\" or \"run kimiflow\", mentions the $kimiflow skill, or asks to build/fix through Kimiflow gates. Do NOT auto-trigger on ordinary feature, bug, refactor, review, or cleanup requests."
---

# Kimiflow For Codex

Run the Kimiflow loop for the user's request.

This Codex skill is the host-native entrypoint for the same Kimiflow engine used by the Claude Code plugin. The canonical workflow lives in the installed plugin root (`SKILL.md` and `reference.md`); read those files before running any phase, then apply the Codex host map below.

## Invocation

Treat these as explicit Kimiflow requests:

- `$kimiflow <feature-or-bug>`
- `@kimiflow <feature-or-bug>`
- `run kimiflow ...`
- `with kimiflow ...`
- `build/fix this through the Kimiflow gates`

Do not invoke Kimiflow merely because the user asks for a normal feature, bug fix, cleanup, audit, or review.

## Host Map

Before invoking any Kimiflow helper script, establish the plugin root from this installed skill file:

1. Treat `KIMIFLOW_SKILL_DIR` as the absolute directory that contains this `skills/kimiflow/SKILL.md` file.
2. Export `KIMIFLOW_PLUGIN_ROOT="$(cd "$KIMIFLOW_SKILL_DIR/../.." && pwd)"`.
3. Export `KIMIFLOW_HOST=codex`.

Never invoke helper scripts through a two-parent relative `hooks` path from the user's project cwd; Codex shell commands run in the workspace, not in the installed skill directory.

Apply the canonical Kimiflow workflow from `$KIMIFLOW_PLUGIN_ROOT/SKILL.md` with these Codex substitutions:

- `/kimiflow` in user-facing text means `$kimiflow` or an explicit "run Kimiflow" prompt in Codex.
- `/kimiflow --project-map <quick|standard|deep|skip>` means `$kimiflow --project-map <quick|standard|deep|skip>` in Codex. Missing maps, per-section staleness checks, recommended-but-skippable delta refreshes, focus selection, storage targets, and Improve/Docs publishing use the same canonical Project Map rules and `hooks/project-map-status.sh`.
- `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}` means the installed Kimiflow plugin root. In Codex, use `KIMIFLOW_PLUGIN_ROOT`.
- When invoking Kimiflow helper scripts from Codex, set `KIMIFLOW_HOST=codex`.
- `TaskCreate` / `TaskUpdate` means use Codex's task plan/status updates.
- Claude Code subagent names map to Codex subagents as follows:
  - `Explore` or read-only codebase exploration: use a Codex `explorer` subagent when subagents are available.
  - implementation or fix worker: use a Codex `worker` subagent when useful.
  - planning, review, verification, or general work: use a Codex `default` subagent unless a more specific configured agent exists.
- `WebSearch` / `WebFetch` means Codex web/search or another available current-source tool. For current external technical facts, prefer primary sources.
- `CLAUDE.md` is a Claude project convention file. In Codex, read `AGENTS.md` first, and also read `CLAUDE.md` if it exists because Kimiflow historically treats it as a conventions hint.

## Gate Commands

Use the bundled scripts as the only mechanical source of truth:

- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-review-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-build-gate.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-verbosity.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/test-weakening-scan.sh`
- `$KIMIFLOW_PLUGIN_ROOT/hooks/secret-content-scan.sh`

For Codex invocations, call them with `KIMIFLOW_HOST=codex`, for example:

```bash
KIMIFLOW_HOST=codex "$KIMIFLOW_PLUGIN_ROOT/hooks/resolve-review-gate.sh" .kimiflow/<slug>/findings --round 1 --expect A,B
```

Do not replace these scripts with model judgment. If a resolver says the gate is closed, the gate is closed.

## Output

Reply in the user's language. Keep Kimiflow's terse output rule from the canonical workflow: visible chat is control-plane only; artifacts and evidence go to files.
