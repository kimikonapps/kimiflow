# Architecture

The skill is the product. It supplies a short working agreement and defers planning, execution,
permissions and workspace management to the host. There is no separate workflow state machine.

The only optional runtime program is `scripts/check_change.py`: explicit argv checks, bounded returned
failure output, timeout cleanup and comparison of source snapshots. It has no provider, persistent
state, automatic recall or commit logic. Prefer native checks when this adds no useful assurance.

`docs/render/kimiflow/` contains the shared skill and thin Codex/Pi wrappers. `scripts/render_skills.py`
produces their installed copies. `scripts/build_plugin.py` builds/checks the exact allowlisted runtime
under `plugins/kimiflow`; its fingerprint proves package identity, not software correctness.

Development-era runtimes are not bundled. Their source history and local user data remain available;
see `MIGRATION.md` when handling existing managed work.
