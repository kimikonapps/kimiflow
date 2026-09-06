# Architecture

The skill is the product. It supplies a short working agreement and defers planning, execution,
permissions and workspace management to the host. No new workflow state machine replaces the removed one.

The only optional runtime program is `scripts/check_change.py`: explicit argv checks, bounded returned
failure output, timeout cleanup and comparison of source snapshots. It has no provider, persistent
state, automatic recall or commit logic. Prefer native checks when this adds no useful assurance.

`docs/render/kimiflow/` contains the shared skill and thin Codex/Pi wrappers. `scripts/render_skills.py`
produces their installed copies. `scripts/build_plugin.py` builds/checks the exact allowlisted runtime
under `plugins/kimiflow`; its fingerprint proves package identity, not software correctness.

The old engine is available at the 0.4.3 tag/release, not bundled in this tree. User run and memory data
are untouched. See `MIGRATION.md` before changing a host with active managed work.
