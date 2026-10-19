# Architecture

Kimiflow is documents and conventions plus one stdlib helper script. There is no workflow state
machine, controller or service. Planning, execution, permissions and workspace management stay with
the host.

## Two modes

- **Initialized project.** `.kimiflow/ops/` in the main checkout holds the project's operating kit,
  copied from `templates/ops/`. `references/operating-model.md` defines roles, the risk-scaled flow and
  the only gates: the owner's OK on the feature sheet, a plan audit where the risk calls for it, and
  the owner-items check before a merge to main.
- **Other projects.** The skill's short working agreement: outcome, acceptance, boundaries, project
  checks. Explicit `direct`/`direkt` bypasses Kimiflow in both modes.

## Helper script

`scripts/kimiflow_ops.py` has four subcommands:

- `init --root`: resolves the main checkout with `git rev-parse --git-common-dir` (also from a linked
  worktree; submodules, separate git dirs, bare repositories and non-Git paths are refused), copies
  the templates without overwriting, writes `.kit.json` (kit version and template hashes) and adds
  `.kimiflow/` to the local exclude file found with `git rev-parse --git-path info/exclude`. It
  refuses tracked `.kimiflow/` files, symlinks leading outside the checkout and an earlier task named
  `ops`; legacy `STATE.md` runs are only reported.
- `update --root [--apply-safe] [--resolved PATH]`: classifies each template against `.kit.json` as
  new, safe, conflict, unchanged or unknown_base. It writes only with `--apply-safe` or `--resolved`,
  never replaces an owner edit, and moves the kit version only when every file is in sync.
- `session-start` (SessionStart hook): prints the absolute kit paths, reading order and notices for
  an older kit and for commits since HANDOFF.md changed. It is silent unless the kit was set up
  locally: `.kimiflow/` in the main checkout's `.git/info/exclude` and no tracked kit file. Errors
  never block a session.
- `git-guard` (PreToolUse hook, matcher `Bash`): exits early unless the command text matches bulk
  staging or a `git commit` together with an AI sender signature; then it blocks (exit code 2) only in
  a locally initialized project.

## Claude Code integration

`claude/hooks.json` and `claude/agents/{worker,reviewer,architect}.md` are registered only through
`.claude-plugin/plugin.json`. The hook file is deliberately not at the default `hooks/hooks.json`
path, which other hosts may also load. Hooks start `python3` with the script path, so packaged files
need no exec bit. The agents carry no `model`; the planner passes one from the project's role table.
The architect has read-only tools. Codex and Pi get the skill texts only.

## Delegation and models

`references/planner-worker.md` defines planner, worker and reviewer roles. A worker may use the same
model as the planner; a cheaper one is an option. `references/models.md` is the dated guide to the
current Claude models and to briefing them; it excludes no model. These are skill instructions
followed by the host agent, not a programmatic model router or cost guarantee.

## Other runtime files

`scripts/check_change.py` is an optional verifier: explicit argv checks, bounded failure output,
timeout cleanup and comparison of source snapshots. It has no provider, persistent state or commit
logic.

`docs/render/kimiflow/` contains the canonical skill plus a shared Codex/Claude Code wrapper and a Pi
wrapper. `scripts/render_skills.py` produces their installed copies. `scripts/build_plugin.py`
builds/checks the exact allowlisted runtime under `plugins/kimiflow` and accepts hooks and agents only
in the Claude manifest, with exactly these paths. Its fingerprint proves package identity, not
software correctness.

Development-era runtimes are not bundled. Their source history and local user data remain available;
see `MIGRATION.md` when handling existing managed work.
