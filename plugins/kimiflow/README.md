```
██╗  ██╗██╗███╗   ███╗██╗███████╗██╗      ██████╗ ██╗    ██╗
██║ ██╔╝██║████╗ ████║██║██╔════╝██║     ██╔═══██╗██║    ██║
█████╔╝ ██║██╔████╔██║██║█████╗  ██║     ██║   ██║██║ █╗ ██║
██╔═██╗ ██║██║╚██╔╝██║██║██╔══╝  ██║     ██║   ██║██║███╗██║
██║  ██╗██║██║ ╚═╝ ██║██║██║     ███████╗╚██████╔╝╚███╔███╔╝
╚═╝  ╚═╝╚═╝╚═╝     ╚═╝╚═╝╚═╝     ╚══════╝ ╚═════╝  ╚══╝╚══╝
```

# kimiflow

**A token-aware feature and bug-fix loop with mechanical gates for Claude Code and Codex.**

[Deutsch](README.de.md) | [Workflow reference](reference.md) | [Examples](examples/README.md) | [Compatibility](COMPATIBILITY.md)

Kimiflow is an explicitly invoked skill/plugin that coordinates an eight-phase engineering loop:
clarify, understand or diagnose, plan, review, implement, verify, code-review, and commit. It keeps
simple work small, but makes important gates enforceable by scripts and hooks instead of relying on
the model to declare itself done.

<!-- capabilities:start -->
**What kimiflow does:** disciplined **feature and bug-fix** delivery with mechanical gates; local
**project intelligence** and curated memory; publish-safe **repo docs**; and reviewable local
**findings**.
<!-- capabilities:end -->

Kimiflow can auto-route actionable implementation requests for substantial feature work. Discussion,
ideation, recommendations, explanations, status requests, and wish formulations stay direct and
read-only. Fixes and small low-risk changes also stay direct unless you explicitly invoke `/kimiflow`
in Claude Code or `$kimiflow` in Codex. Explicit `direct` or `direkt` always bypasses it.

## Why Kimiflow

Native agents already plan, delegate, and review. Kimiflow adds a durable contract around those
abilities:

- state and evidence live under `.kimiflow/<slug>/`, so a run can resume safely;
- plan and code-review findings use tested fail-closed resolvers;
- repeated work without new durable evidence changes strategy automatically instead of asking for another run;
- fixes require reproduction, a proven cause, and red/green evidence;
- material product/authority decisions stop for human approval; verified local commits are automatic, while push and release stay explicit;
- successful learnings are curated, while failed or parked attempts are not promoted as truth;
- the strongest selected model orchestrates and plans, while bounded workers handle cheaper tasks.

The result is not maximum ceremony. The default is the smallest loop that still protects the work.

## Install

Requirements: `jq`, Git, and `python3 >= 3.9` on `PATH`.

### Claude Code

Inside Claude Code:

```text
/plugin marketplace add kimikonapps/kimiflow
/plugin install kimiflow@kimiflow
```

Or from a terminal:

```bash
claude plugin marketplace add kimikonapps/kimiflow
claude plugin install kimiflow@kimiflow
```

Restart Claude Code or open a new session, then run `/kimiflow`. Update later with:

```bash
claude plugin update kimiflow
```

### Codex

```bash
codex plugin marketplace add kimikonapps/kimiflow
codex plugin add kimiflow@kimiflow
```

Restart Codex, open `/hooks`, review and trust the bundled Kimiflow hooks once, then open a new task and invoke
`$kimiflow`. Codex intentionally re-requests this trust review when a plugin update changes a hook definition. To
update:

```bash
codex plugin marketplace upgrade kimiflow
```

Codex loads Kimiflow's bundled hook contract from the `hooks` path declared in the plugin manifest. No
user-level wrapper installation is required. The marketplace publishes only the clean runtime candidate;
maintainer state, eval inputs, and private workflow artifacts are excluded and the candidate carries a
reproducible content fingerprint.

That candidate is also published as a deterministic `kimiflow-runtime-<version>.zip` with
`kimiflow-update-v1.json`. Independent hosts read one stable manifest, verify the official immutable GitHub
release, check the pinned source/artifact digests and their named host profile, then install the same Kimiflow
bytes. Offline or caller-supplied metadata proves artifact integrity only; it can never impersonate an official
compatible update. See [`references/runtime-distribution.md`](references/runtime-distribution.md).

For local development, register this checkout instead:

```bash
codex plugin marketplace add .
bash hooks/install-codex-hooks.sh --check
```

### Optional provider-neutral terminal runner

The embedded plugin remains the default. If you want to start a long Kimiflow task from a terminal and let it
continue without confirming every turn, install the optional thin controller. Codex is the built-in adapter:

```bash
bash hooks/install-kimiflow-cli.sh
kimiflow run "implement the requested feature"
kimiflow status --pretty
```

An existing local or remote coding-agent harness can use the same lifecycle through the versioned JSON-stdio
adapter contract. This path is additive: normal Kimiflow users do not need KimiTalk or another app host.

```bash
kimiflow run --adapter command --adapter-command my-agent-harness --model qwen-local \
  "implement the requested feature"
```

The harness must advertise file, shell, test, resume, and gate capabilities. Kimiflow keeps the workflow,
mechanical gates, active-run ownership, bounded turn limit, and usage receipt provider-neutral; the adapter owns
only model transport and tool execution. App hosts can additionally negotiate canonical workflow context,
abstract `top`/`balanced`/`cheap`/`cross_family_top` model roles, bounded structured events, and root confinement.
Kimiflow never hard-codes Qwen, Ollama, vLLM, or another provider.

Validate an app harness without starting a model turn, then opt into its features explicitly:

```bash
kimiflow adapter-check --adapter-command my-agent-harness \
  --require-feature workflow_context --require-feature model_roles \
  --require-feature structured_events --require-feature root_confinement

kimiflow run --adapter command --adapter-command my-agent-harness \
  --require-feature workflow_context --require-feature model_roles \
  --require-feature structured_events --require-feature root_confinement \
  --model-role top=qwen-local --model-role balanced=qwen-coder-local \
  --events-jsonl --root /path/to/project "implement the requested feature"
```

Repeat the same feature/model arguments when manually resuming. A SHA-256 contract fingerprint prevents silent
role or capability drift before the next coding turn. The complete v1 contract and schema live in
[`references/adapter-protocol.md`](references/adapter-protocol.md) and
[`references/adapter-protocol-v1.schema.json`](references/adapter-protocol-v1.schema.json).
Distribution and adapter execution are separate: a host first verifies the canonical runtime release, then
negotiates the adapter contract. Kimiflow remains independently installable and neither side imports KimiTalk.

With the built-in adapter it launches the already authenticated Codex CLI in a `workspace-write` sandbox and
resumes the same thread. Every adapter uses the same `.kimiflow/` state, gates, and memory; none adds a daemon,
second memory store, or worktree. A persisted turn limit plus one final recovery turn prevents an endless loop;
an exhausted run stays explicitly resumable instead of claiming completion.

Only a material Kimiflow wait or park exits with status 3. Answer it with
`kimiflow resume --message "<decision>"`; interrupted or transport-failed runs can use `kimiflow resume` without
a message while their active run remains open. The local receipt contains bounded transport metadata (including
the existing canonical root/run identity), never the task, transcript, event payloads, workflow paths, model IDs,
or user answers. `bash hooks/install-kimiflow-cli.sh --check` verifies the managed wrapper, and the installer
refuses to overwrite an unrelated `kimiflow` executable.

### Unified local run control plane

Rich clients and model adapters can use `hooks/run-bridge.sh` as a single-request JSON-stdio boundary. It
returns one deterministic readiness view, accepts only owner-bound replay-safe item mutations, and exposes
content-free phase-context metadata plus a multidimensional terminal scorecard. Existing Active Run, graph,
phase, review and finish gates remain authoritative; the bridge adds no daemon, network service or provider.
Phase context stays in shadow mode and never replaces the current phase file plus its exact assigned reference
sections. The complete `reference.md` is not preloaded. Terminal scorecards remain
readable through an explicit safe run path after the Active Run has retired.

## Demo

![Kimiflow launcher and gated feature/fix flow](docs/demo/kimiflow.gif)

> Scripted illustration of the current launcher and core loop. The source and real-recording guide
> live in [`docs/demo/`](docs/demo/).

## Modes

The same modes work with `/kimiflow` in Claude Code and `$kimiflow` in Codex.

| Mode | Purpose |
|---|---|
| `kimiflow full` | Strict large-scope loop; pauses only for a material decision. |
| `kimiflow quick` | Lean path for a small, low-risk change. |
| `kimiflow fix` | Diagnose first, apply a bounded fix, then require red/green verification. |
| `kimiflow grill` | Clarify a request only; no plan or code. |
| `kimiflow plan` | Prepare intent, research, plan, and acceptance criteria; no code. |
| `kimiflow build` | Implement an approved prepared plan. |
| `kimiflow review` | Read-only review of an existing feature or current diff. |
| `kimiflow audit` | Read-only cleanup/refactoring audit before selecting a slice. |

Useful explicit forms:

```text
/kimiflow <feature-or-bug>
/kimiflow --fix <bug>
/kimiflow --verify-feature <feature-or-path>
/kimiflow <request> --prepare
/kimiflow --resume <slug>
/kimiflow --project-map quick
```

Kimiflow first proves where the product goal, actor, visible behavior, boundaries, and success came
from. Every new non-trivial feature gets one compact Product Intake before planning or project writes;
an already-complete request gets a short contract confirmation instead of filler questions. The user
decides WHAT/WHY, while architecture, libraries, data models, tests, and other technical HOW stay with
the agent. After the answer, the product contract is locked and the run continues autonomously. A
second batch is allowed only when the first answer creates a new material product conflict. Exact
trivial work and fixes keep their direct routes.

## Eight Phases

| Phase | What happens |
|---|---|
| 0 Setup | Inventory every worktree, create durable run state, then batch any safe-disposition decision once. |
| 1 Clarify | Run the mandatory Product Intake for non-trivial features, forbid HOW questions, lock the confirmed contract, then continue. |
| 2 Understand | Inspect project knowledge and code; choose Discovery `none`, `pulse`, or `focused`, and prove architecture feasibility before planning. Fixes reproduce and prove the cause. |
| 3 Plan | Write a flat minimum-complete plan, testable acceptance criteria, and up to five evidence-bound implementation decisions. |
| 4 Review | Resolve plan blockers and pause only for a material authority, scope, risk, privacy, cost, or irreversible decision. |
| 5 Implement | Apply the smallest accepted change, normally sequentially; fixes preserve red evidence before production code. |
| 6 Verify | Check acceptance, regression, and whether the delivered diff still matches the researched strategy and invariants. |
| 7 Review and commit | Revalidate conformance, verify findings, create the named-path atomic commit, then prove commit/index/worktree delivery; push/release stay explicit. |

## Mechanical Gates

"Mechanical" means a tested script or hook decides the boundary, not a prose self-report.

| Gate | Enforced boundary |
|---|---|
| Workspace preflight | Every linked tree and dirty path is classified; cleanup is no-force, ownership-bound, and solo-dev by default. |
| Product Intake, Clarify and Discovery gates | Supported planning/writes stay blocked until an explicit product response; the locked intent, zero technical questions, feasibility, and source/scope/decision evidence must hold before planning. |
| Plan-blocker and review gates | Acceptance mappings and evidenced `BLOCKER/HIGH` findings are resolved within a bounded repair budget. |
| Implementation-conformance gate | Researched decisions, invariants, affected paths, exact checks, and every locked product requirement converge in Phase 6; finish additionally proves the committed delivery matches. |
| Adaptive execution controller | Run-wide no-progress and budget pressure select a bounded recovery action; mandatory quality gates remain intact. |
| Local run control plane | Hosts receive one readiness/cursor contract; shared locking, owner proof and action receipts make supported item mutations fail closed and replay-safe. |
| Material-decision gate | Reversible technical work continues; only missing authority, material risk, external access, privacy/cost, or irreversibility pauses. |
| Red/green gate | Fixes cannot finish without recorded failing and passing evidence plus regression coverage. |
| Atomic commit gate | Schema-4 runs stage named run-owned paths and commit locally under the original build authority. |
| Secret/state hooks | Secret-looking paths, bulk staging, and resolver calls without durable state are blocked. |
| Test gate | Large runs can block completion while the configured project test command is red. |

Scope choice, root-cause quality, and reviewer completeness still require model judgment. Kimiflow
mechanizes the evidence boundaries without pretending a tool can prove that no bug was missed.

## Token-Aware Scaling

- `trivial`: exact, low-risk work; implement, verify briefly, then commit locally.
- `small`: default; compact clarification, adaptive Discovery, one planner, bounded review.
- `large`: reserved for broad changes, new dependencies, migrations, security/privacy/money paths,
  subtle bugs, or explicit `full` runs.
- Discovery starts no worker for `none|pulse`, normally one bounded evidence worker for `focused`,
  and at most two independent lanes.
- Research may correct implementation choices, but only `required` constraints may expand scope.
- Conformance records at most five material decisions; `small` adds no model call and `large` reuses its existing independent verifier.
- Execution uses three fixed quality profiles with an explicit selection reason and one compact local trace; hard pressure removes optional breadth, not verification quality.
- A second planner appears only for a real architecture or irreversible contract fork, not because a
  task merely looks large.
- The top model owns orchestration, synthesis, planning, review verdicts, and risky diagnosis.

`small` and `quick` skip broad memory recall and the **Vault Pulse** by default. An explicit user cue that a
similar bug or fix existed before instead triggers one targeted local recall with at most five hits at any scope,
without provider searches.
Current-state checks and the final learning review still run at every non-trivial scope.

## Project Intelligence and Memory

Kimiflow can create a local project map under `.kimiflow/project/` with codebase, architecture,
convention, test, and flow evidence. Later runs check affected sections and refresh only stale areas.
The map is optional, local by default, and never blocks normal work when absent.

The local Memory Router stores bounded project facts, decisions, standards, run history, and
evidence-backed learnings. Promotion happens only after successful verification and source-freshness
checks. Changed evidence supersedes stale learning instead of silently keeping it active.
Completed runs also receive a local automatic outcome evaluation. Future matching runs see at most
one verified success strategy and one evidenced failure strategy, both rechecked against current code.
Recall now packs memory, facts, learnings, strategies, and history into one global context budget and
one global hit limit, removing cross-source duplicates. Every recalled item remains advisory: current
code, tests, specifications, and primary evidence win. The optional SQLite index is used only while its
source fingerprint is current; stale indexes are bypassed and atomically rebuilt on a persisted recall.
For large monorepos, run-artifact Recall infers up to eight nested package units from affected files and
ranks their evidence first. Root-level rules and evidence without a proven package boundary stay global;
invalid, mixed, overflowing, or concurrently changed boundaries fall back to project-wide Recall. The
resolver uses bounded ancestor checks only—no repository scan, dependency graph, worktree change, network
request, or user approval.
Final hits also receive stable local IDs. Kimiflow records an ID as used only when it actually shapes a
plan decision, then links it to verification and classifies it `helpful`, `neutral`, or `contradicted` in
the existing outcome artifact. This adds no external telemetry, copied recall text, or user confirmation.

Memory maintenance is preview-first and reversible. `memory-router.sh lifecycle` explains a bounded
0–5 utility score; `lifecycle --write` quarantines only strictly parsed stale rows that are provably unused with
a unique ID. An atomic path exchange verifies displaced source identity/mode and the installed candidate; bounded
re-exchanges promote a later writer without ever removing the canonical path, while an unresolved race retains an
explicit local recovery copy. Unsupported native exchange fails closed before mutation. Then
`lifecycle --restore <id> --write` restores one row only while its evidence is still exact. For optional
cross-project handoff, `capsule --write` creates a mode-0600 local privacy capsule containing at most 20
fresh, allowlisted six-field projections. Vault sync uses the same projection and never exports source IDs,
paths, evidence references, credential or JWT shapes, dotted/dotless emails, private/security rows, or unsafe content.

An Obsidian Vault is optional. Without it, project-local memory and every quality gate continue to
work. With authenticated Vault MCP tools, Kimiflow can recall and export curated, non-private
cross-project learning. API keys are never stored in `.kimiflow/`.

See [`reference.md`](reference.md#memory-router--learning-loop-phase-2-recall--phase-7-learn) for the
artifact and privacy contract, and [`reference.md`](reference.md#vault-conventions-phase-2) for Vault
setup details.

## Workspace Safety and Resume

An active run records its owning Codex or Claude session. Other sessions may read, discuss, and plan.
Before writing in the same checkout they run deterministic path-conflict checks. Implementation stays
sequential in the current worktree by default. One exceptional temporary tree needs explicit authority,
trusted registration, and is retired only when owned, terminal (`done`, `failed`, or `aborted`), clean,
and unlocked; `parked` stays resumable. Retirement archives the complete checkout and its matched Git
metadata without a destructive Git remove; Codex-managed trees remain controlled by the app.

Prepared and parked runs can resume from `.kimiflow/<slug>/`. If affected files changed or the plan
basis is unknown, Kimiflow revalidates before implementation instead of building a stale plan.

## Safety Boundaries

- Kimiflow auto-routes only actionable implementation requests for substantial feature work with
  material cross-surface, integration, data, security, public-API, architecture, or discovery needs.
  Discussion, ideation, recommendations, explanations, status requests, and wish formulations do not
  authorize implementation. Fixes, reviews, refactors, cleanup, docs/config, and small low-risk
  features stay direct unless explicitly routed through Kimiflow.
- Explicit `direct` or `direkt` always bypasses Kimiflow; an explicit Kimiflow request always starts it.
- `.kimiflow/` is local run state and should not be committed by default.
- The secret hook checks suspicious paths, not secret content; use the bundled advisory scanner or
  a tool such as gitleaks for content scanning.
- Project maps and repo docs exclude raw vulnerabilities, secrets, private paths, and Vault
  references unless an explicitly sanitized public note is requested.
- This is a pre-1.0 workflow plugin; rerun compatibility checks after host upgrades.

## Documentation

- [`reference.md`](reference.md) - complete workflow and gate contract.
- [`COMPATIBILITY.md`](COMPATIBILITY.md) - supported host primitives and upgrade checks.
- [`docs/architecture.md`](docs/architecture.md) - canonical engine, adapters, hooks, and data flow.
- [`docs/codebase.md`](docs/codebase.md) - repository map and ownership boundaries.
- [`docs/testing.md`](docs/testing.md) - local, smoke, and CI verification.
- [`examples/`](examples/README.md) - small fix, risky fix, and feature walkthroughs.
- [`evals/`](evals/README.md) - behavioral release calibration scenarios.
- [`CHANGELOG.md`](CHANGELOG.md) - release history.

## License

[MIT](LICENSE)
