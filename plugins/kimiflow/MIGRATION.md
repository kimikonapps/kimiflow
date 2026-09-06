# Moving from 0.4.3 to the minimal core

0.5 removes the managed workflow rather than maintaining two engines. It is a breaking change for
headless/stdio/MCP clients, FirstMate crews and users of Kimiflow's Fleet, memory and release commands.
New work uses native agent tools, project tests and optional plain continuation notes.

## Existing work comes first

- Finish an active Flow-schema run using its existing **0.4.3** installation before upgrading that host.
  Keep its project checkout, `.kimiflow/` data and worktrees intact. Do not convert its state into a
  successful run or copy it into the new notes format as an implicit migration.
- The immutable [0.4.3 release](https://github.com/kimikonapps/kimiflow/releases/tag/kimiflow--v0.4.3)
  retains the old runtime ZIP and its verified update manifest. The Git tag `kimiflow--v0.4.3` also
  preserves the old source, commands and documentation. Recover a missing old runtime from that version;
  do not point an active run at the new source checkout.
- This change does not modify installed caches, global hook registrations, `.kimiflow/` data,
  `~/.kimiflow/` memory, external Vault data or existing worktrees. Their lifecycle remains with the old
  runtime and the user. The repository's historical plans are available at the old Git tag.

## Removed interfaces

There is no `kimiflow run`, stdio adapter/MCP server, Fleet controller, memory router, project-map engine,
security engine, project-release engine, phase gate or FirstMate extension in 0.5. The adapter-v1 and
managed update-v1 contracts belong to 0.4.3; applications requiring them must stay pinned there.
Do not auto-upgrade a managed host to 0.5 merely because its version number is higher.

The new plugin registers no hooks. If old manually installed Kimiflow hooks or CLI wrappers remain,
finish their runs first, then remove only those entries through the old version's documented uninstall
or migration procedure. Never clear unrelated host hooks or use a missing command as evidence of success.

The optional check helper moved from `hooks/check-change.sh` to
`python3 <installed-root>/scripts/check_change.py`. It is no longer a compulsory wrapper around project
checks. No automatic state migration, remote fallback or legacy runtime is bundled.
