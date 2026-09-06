# Compatibility with development installations

The public release series starts at 0.5.0. Earlier development releases and version tags have been
removed from the public release list. Existing Git history and local user data are retained.

## Existing managed work

Some development installations used Flow-schema runs, managed adapters, hooks, memory or worktree
controllers. The public core does not implement those interfaces. Do not reinterpret their state as
new continuation notes, mark them complete without evidence, or discard their data.

Finish such work with its original installation. If that runtime is missing, its last development
source is preserved at commit `c0849d69985074e9a8a7d5e5d5fac2cdd2f824b2` in this repository's history.
Use a separate checkout of that exact commit for recovery; do not overwrite the current project or
assume a deleted release/tag is still downloadable. Applications requiring the old adapter-v1 or
managed update-v1 contracts must not automatically switch to the public core.

Keep project checkouts, `.kimiflow/` state, `~/.kimiflow/` memory, external Vault data and worktrees intact.
After old work is finished, remove only its obsolete hook/CLI registrations through that installation's
documented procedure. Never clear unrelated host hooks. The public plugin registers no hooks or services.

The public check helper is `python3 <installed-root>/scripts/check_change.py`. It is optional and does
not migrate state, install old runtimes or provide a fallback controller.
