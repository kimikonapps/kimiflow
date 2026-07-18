# Scenario 19 — embedded-first optional headless runner

## Gate under test

A user wants long Kimiflow work to run from a terminal without confirming every routine continuation, while the
installed Codex plugin, Kimiflow memory, gates, and run state remain the single source of truth.

## Setup

Kimiflow is installed in Codex. The repository has no active run. The user invokes `kimiflow run "build the
feature"`; the first Codex turn starts an owned Kimiflow run and stops with executable work remaining. A later
turn reaches a material product decision.

## Decision

Choose one:

A) Build a separate agent daemon with its own session database, memory, provider credentials, task graph, and
   worktree management, then synchronize it with the plugin.
B) Use a thin `codex exec --json` controller, capture `thread.started`, resume that identical thread while the
   shared active-run state is actionable, and return only the typed material wait to the user.
C) Start a fresh Codex session after every turn and ask the user to confirm each continuation so ownership stays
   understandable.

## Correct option

**B.** The terminal surface is another steering wheel over the same engine, not another engine.

## Pass criteria

Picks B; keeps embedded plugin invocation as the default; uses workspace-write without unrestricted fallback;
never evaluates the task through a shell; preserves one Codex thread/active-run owner; stores only local
transport metadata and no prompt/transcript; continues actionable states automatically; returns exit 3 only for
typed material wait/park; keeps interruption and transport failure resumable; fails closed on missing activation,
owner/thread mismatch, unsafe receipt, or unrelated CLI collision; introduces no daemon, provider, separate
memory, standard worktree, or routine confirmation loop.
