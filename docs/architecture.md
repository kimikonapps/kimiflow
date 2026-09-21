# Architecture

The skill is the product. It supplies a short working agreement and defers planning, execution,
permissions and workspace management to the host. There is no separate workflow state machine.

For user-selected delegation, `references/planner-worker.md` defines model roles: a capable planner,
a different, cheaper implementation worker, and a reviewer more capable than the worker (normally
the planner). Selection uses current pricing, availability and task suitability, not fixed model IDs.
Review covers the diff, integration and acceptance evidence. This is a skill instruction enforced by
the host agent, not a programmatic model router or cost guarantee. Total cost includes review and rework.

The only optional runtime program is `scripts/check_change.py`: explicit argv checks, bounded returned
failure output, timeout cleanup and comparison of source snapshots. It has no provider, persistent
state, automatic recall or commit logic. Prefer native checks when this adds no useful assurance.

`docs/render/kimiflow/` contains the shared skill and thin Codex/Pi wrappers. `scripts/render_skills.py`
produces their installed copies. `scripts/build_plugin.py` builds/checks the exact allowlisted runtime
under `plugins/kimiflow`; its fingerprint proves package identity, not software correctness.

Development-era runtimes are not bundled. Their source history and local user data remain available;
see `MIGRATION.md` when handling existing managed work.
