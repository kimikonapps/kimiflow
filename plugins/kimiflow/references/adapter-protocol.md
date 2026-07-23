# Kimiflow command-adapter protocol v1

This is the optional local boundary for an existing tool-capable coding-agent harness. Embedded Kimiflow in
Codex or Claude Code and the built-in `kimiflow run` Codex adapter do not use or require this contract. An app
such as KimiTalk can implement it without becoming a Kimiflow dependency.

Machine-readable schema: [`adapter-protocol-v1.schema.json`](adapter-protocol-v1.schema.json).
Release discovery and artifact trust are deliberately separate from this execution protocol. App hosts consume
the canonical manifest described in [`runtime-distribution.md`](runtime-distribution.md); they do not vendor or
fork Kimiflow to implement this adapter.

## Handshake

Kimiflow executes:

```text
<adapter> capabilities --json
```

The command prints one JSON object with `schema_version: 1`, safe `name` and `host` identities, and all five
mandatory capabilities set to `true`: `files`, `shell`, `tests`, `resume`, and `gates`.

Optional v1 features are advertised under `features` and are off when absent:

- `workflow_context`: Kimiflow sends transient canonical skill, phase-manifest, and bridge locations. The host
  loads the workflow; the model does not need a native `$kimiflow` command.
- `model_roles`: the host accepts abstract `top`, `balanced`, `cheap`, and `cross_family_top` mappings. Model and
  provider IDs remain host configuration, not Kimiflow policy.
- `structured_events`: the host may emit the bounded public event types in the schema.
- `root_confinement`: the host declares that its file/shell tools are confined to the selected project policy.
  This is a trust boundary owned and enforced by the host; Kimiflow can require the declaration but cannot
  sandbox an external process from inside JSON-stdio.

Use the mechanical preflight before a real model turn:

```bash
kimiflow adapter-check \
  --adapter-command my-agent-harness \
  --require-feature workflow_context \
  --require-feature model_roles \
  --require-feature structured_events \
  --require-feature root_confinement
```

## Start and resume

Kimiflow launches `<adapter> start --json` or `<adapter> resume --json` in the selected Git root and writes one
request object to stdin. The harness returns JSONL on stdout. `session.started` establishes ownership; exactly
one `turn.completed` ends a successful turn. `turn.failed`, `error`, malformed, oversized, or undeclared events
fail the transport closed.

Example app-host invocation:

```bash
kimiflow run --adapter command \
  --adapter-command my-agent-harness \
  --require-feature workflow_context \
  --require-feature model_roles \
  --require-feature structured_events \
  --require-feature root_confinement \
  --model-role top=qwen-local \
  --model-role balanced=qwen-coder-local \
  --events-jsonl \
  --root /path/to/project \
  "Implement the approved feature"
```

Repeat the same adapter command, required features, model, and role mapping on `resume`. Kimiflow stores only a
SHA-256 fingerprint of that optional negotiated contract and rejects drift before invoking another coding turn.
Legacy adapters without optional features keep their original payload and resume behavior.

## Event and privacy rules

`--events-jsonl` is opt-in and command-adapter-only. Validated public events are emitted as JSONL, followed by
one `run.result` event. Kimiflow strips undeclared fields before forwarding. Tool arguments, commands, file
contents, hidden reasoning, prompts, user answers, and workflow/event paths are never added to the runner receipt.
The pre-existing receipt still stores the canonical project root and Active Run path because safe same-project
resume depends on them. Event lines are size-bounded before JSON parsing; each turn is also limited to 10,000
events and 16 MiB of event input. The runtime enforces the semantic relations `total >= current` and
`after_tokens <= before_tokens` in addition to the schema's numeric bounds. A command-adapter turn is capped at
two hours by default so a silent harness cannot stall the controller forever; set the bounded local environment
value `KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS` (1–86400) when a host needs a different ceiling.

The harness owns provider authentication, model loading/routing, sandbox enforcement, tool execution, and UI.
Kimiflow continues to own the workflow, Active Run, mechanical gates, bounded recovery, and project-local memory.
