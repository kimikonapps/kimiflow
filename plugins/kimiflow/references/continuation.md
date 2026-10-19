# Continuation only when needed

In a project initialized with `kimiflow init`, HANDOFF.md in `.kimiflow/ops/` carries the state between
sessions; see [the operating model](operating-model.md). Elsewhere, prefer the host's task history.

For a long or interrupted task outside an initialized project, optionally keep one local
`.kimiflow/<task>/NOTES.md`: goal and boundaries, current revision/paths, decisions, open work, last
checks. Keep it short and advisory; recheck current source before resuming. Before writing or resuming
notes, check for an existing `STATE.md` in that task directory. Do not create or convert a run schema.

A legacy `Flow schema` run or managed adapter request is **not** a notes-based task. Do not reinterpret,
advance or discard its state. Read [MIGRATION.md](../MIGRATION.md) and use its matching development
runtime. No legacy engine, automatic updater or fallback controller is included here.
