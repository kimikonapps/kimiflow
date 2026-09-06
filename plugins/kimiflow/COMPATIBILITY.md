# Compatibility

The 0.5 core uses skills in Codex, Claude Code and Pi. It registers no hooks, extensions, MCP servers
or agent adapters. The selected host owns tool availability, permissions, context continuation and
workspace isolation. A local model needs a tool-capable host, not a Kimiflow-specific adapter.

The optional verification helper and maintainer tools require Python 3.9+ and Git. CI covers Linux
and macOS. Process-group cleanup is POSIX-specific; it is not a sandbox for detached processes.

Earlier development interfaces are not supported by the public core. Preserve existing managed
work and use its original runtime; see [development compatibility](MIGRATION.md).
No current model-performance or live-host interoperability benchmark is claimed by structural tests.
