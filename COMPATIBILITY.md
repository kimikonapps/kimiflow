# Compatibility

## Hosts

- **Claude Code:** skill, two hooks (SessionStart and a PreToolUse git guard) and three agents
  (`kimiflow:worker`, `kimiflow:reviewer`, `kimiflow:architect`). Hooks and agents are registered only
  through `.claude-plugin/plugin.json`; the hook file lives at `claude/hooks.json`, not at the default
  `hooks/hooks.json` path.
- **Codex and Pi:** skill texts only. They register no hooks, agents, extensions, MCP servers or
  adapters. `init` and `update` run there as the same helper script; sessions do not get the kit
  automatically.

The selected host owns tool availability, permissions, context continuation and workspace isolation.
A local model needs a tool-capable host, not a Kimiflow-specific adapter.

## Requirements

- The hooks start `python3` from PATH; without it they cannot run.
- The helper scripts and maintainer tools require Python 3.9+ and Git. CI covers Linux/Python 3.9
  and macOS/Python 3.14; hooks and process-group cleanup are tested on POSIX only. Process-group
  cleanup is not a sandbox for detached processes.

## Git guard scope

- The guard reads the whole Bash command text. A compound command that mentions an AI sender address,
  product link, AI co-author trailer or "Generated with Claude Code" anywhere next to `git commit` is
  blocked too, even when that text is not part of
  the commit message.
- It sees only commands run through the Bash tool. Commits from other tools, editors or a terminal
  outside the session are not checked.
- It is active only in a project initialized locally with `kimiflow init`.

## Earlier versions

Earlier development interfaces are not supported by the public core. Preserve existing managed
work and use its original runtime; see [development compatibility](MIGRATION.md).
No current model-performance or live-host interoperability benchmark is claimed by structural tests.
