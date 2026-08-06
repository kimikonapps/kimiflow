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

[![Latest release](https://img.shields.io/github/v/release/kimikonapps/kimiflow?display_name=tag&sort=semver)](https://github.com/kimikonapps/kimiflow/releases/latest)

Kimiflow is a workflow plugin that can be invoked explicitly or auto-routed for substantial feature
work. Its eight phases clarify, understand or diagnose, plan, review, implement, verify, code-review,
and commit. Before a feature is fixed in code, Kimiflow discusses the product flow with the user,
checks the current codebase, and binds important claims to executable evidence.

<p align="center">
  <a href="https://kimikonapps.github.io/kimiflow/">
    <img src="docs/kimiflow-graph.svg" alt="Kimiflow workflow: confirm product intent, inspect current code, execute evidence, expand material findings through five bug-cascade probes, repair one proved root, retain verified learning in human-readable memory, and commit locally.">
  </a>
  <br>
  <sub><a href="https://kimikonapps.github.io/kimiflow/">Explore the interactive graph</a></sub>
</p>

<!-- capabilities:start -->
**What kimiflow does:** disciplined **feature and bug-fix** delivery with mechanical gates; local
**project intelligence** and human-readable cross-project memory; publish-safe **repo docs**; and
reviewable local **findings**, including mechanically bounded bug-cascade analysis.
<!-- capabilities:end -->

## What is new

- **Decision-bearing prose now gets one bounded same-pass quality check.** The existing author or reviewer
  preserves meaning and evidence, corrects generic or inflated wording in place, and never adds another
  model call or gate. The approach adapts the minimum-effective-edit idea from
  [petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop); it is not an AI-authorship detector.
- **Bug cascades are repaired as one cause, not many symptoms.** Every material review class must check
  direct callers, data flow, shared state, assumption users, and error consequences. Related findings share
  one proved root cause, one repair, and retained regression evidence.
- **Cross-project memory no longer needs Obsidian.** Verified portable learnings become ordinary Markdown
  notes under `~/.kimiflow/memory/`, with a human-readable `INDEX.md`, bounded related-note links, and
  revocable project bindings. Ask the model when you want to query that graph; no database UI is required.
- **Nothing is promoted merely because a run produced it.** Project learnings still need current evidence and
  repeated verified use before they can enter global memory. A contradiction or stale source revokes trust.

The execution runtime is model-agnostic. Codex and Claude Code are built-in terminal adapters, while
other coding agents can implement the same versioned JSON-stdio contract. Strict read-only Work-Units
and an opt-in bounded Solution Search keep delegated research, review, and materially open design
decisions inside measured permission, usage, privacy, and retention boundaries.

Kimiflow can auto-route actionable implementation requests for substantial feature work. Discussion,
ideation, recommendations, explanations, status requests, and wish formulations stay direct and
read-only. Fixes and small low-risk changes also stay direct unless you explicitly invoke `/kimiflow`
in Claude Code or `$kimiflow` in Codex. Explicit `direct` or `direkt` always bypasses it.

## Why Kimiflow

Native agents already plan, delegate, and review. Kimiflow adds a durable contract around those
abilities:

- state and evidence live under `.kimiflow/<slug>/`, so a run can resume safely;
- plan and code-review findings use tested fail-closed resolvers; material findings trigger a bounded
  root-cause cascade scan before repair;
- repeated runtime claims require executable evidence; irrelevant findings stop before repair and repeated failed strategies cannot create an endless loop;
- fixes require reproduction, a proven cause, and red/green evidence;
- material product/authority decisions stop for human approval; verified local commits are automatic, while push and release stay explicit;
- successful learnings are curated into project-local state and, when safe and durable, a human-readable
  cross-project graph; failed or parked attempts are not promoted as truth;
- the strongest selected model orchestrates and plans, while typed bounded workers handle cheaper tasks;
- mechanically bounded Solution Search stays off for clear work and explores at most three fixed candidate
  lenses plus one fresh selector only when a material design decision is genuinely open.

The result is not maximum ceremony. The default is the smallest loop that still protects the work.

## First Principles

- **Understand before building.** Kimiflow restates the problem, observable success, boundaries, options, and the complete user flow in the user's language. The user can discuss and correct that contract before implementation starts.
- **Current code beats remembered code.** Planning is bound to the current HEAD and the exact bytes/types of every affected path. Kimiflow searches in the order `reuse → evolve → new` so an existing feature or abstraction is not rebuilt by accident.
- **Research challenges the local idea; it does not expand the product.** Project evidence comes first, current primary sources close named gaps, and the selected approach is compared back against the codebase and confirmed scope.
- **Runtime claims need runtime evidence.** Important decisions declare whether review, an isolated spike, or executable runtime proof is required. A prose-only “passed” claim is never enough.
- **Reviews are proportional, cascade-aware, and finite.** Reproduced findings are classified by contract,
  supported path, impact, and repair cost. Each material class probes callers, data flow, shared state,
  assumption users, and error consequences before related symptoms are repaired at one proved root. Irrelevant
  edge cases do not enter the loop; protected impacts cannot be waived; repeated strategies remain bounded.
- **Prefer the smallest replaceable design.** Features, integrations, models, and review routes must be easy to add, adapt, or remove without rewriting the workflow. No new service, provider, or abstraction is introduced when an existing contract can evolve cleanly.
- **Learning remains evidence-bound.** Durable lessons cite current source paths and become stale automatically when those source bytes change.

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
continue without confirming every turn, install the optional thin controller. Codex and Claude Code are
built-in adapters:

```bash
bash hooks/install-kimiflow-cli.sh
kimiflow run "implement the requested feature"
kimiflow run --adapter claude --model claude-opus-5 "implement the requested feature"
kimiflow status --pretty
```

### Pi with stock FirstMate

Kimiflow's Pi package keeps the Pi session in your current project as the conversational **FirstMate Captain**.
It includes the workflow skill and one dormant `kimiflow_crew` extension. On request, Captain starts one visible
**Kimiflow Main**; Main owns the workflow and its visible crew. Install a trusted Kimiflow checkout with:

```bash
pi install /absolute/path/to/kimiflow
```

Use `pi install -l /absolute/path/to/kimiflow` for project-local scope. Install
[stock FirstMate](https://github.com/kunchenguid/firstmate) separately when you want visible workers. Kimiflow
does not copy or fork FirstMate and does not require a particular commit; it checks the required FirstMate
capabilities and validates each consumed brief/harness boundary before use. If automatic sibling discovery cannot find the checkout, set
`KIMIFLOW_FIRSTMATE_ROOT=/absolute/path/to/firstmate` before starting Herdr.

```bash
cd /absolute/path/to/your-project
herdr
# inside the Herdr terminal:
pi
```

Then say naturally in Pi:

```text
Start Kimiflow Run 7.2.
```

There is no separate FirstMate command and Pi does not run from the FirstMate checkout. Captain passes the user
request plus any existing plan as immutable launch input and reports Main only after its exact Herdr/Pi endpoint
and lifecycle are readable. Captain then stays available for conversation. Main inspects current code, researches
current sources, discusses and confirms the feature through Captain, and owns planning through review. Main may
start read-only research Scouts before confirmation; implementation Ships and review Scouts require confirmation.

All user discussion remains in Captain. Main and workers return decisions and results through FirstMate status;
Captain shows a bounded decision, sends the answer back, and Main resumes. FirstMate alone owns briefs,
worktrees, endpoints, wake, recovery, delivery and safe cleanup. Kimiflow stores no competing worker truth and
does not reimplement Calm. Main may run several Ships only for disjoint, self-contained write packets.
A tightly coupled build intentionally starts one Ship.
Independent research and semantic review use separate visible Scouts; `small` code review uses two review axes
and `large`/release-critical review uses three when the FirstMate crew is available.

The adapter mechanically restricts which crew actions each role can call; it is not an OS sandbox around Pi's
shell. Brief boundaries plus Kimiflow's integration/final Git and evidence gates detect forbidden product writes.

Kimiflow display verbosity is inherited by Main and every newly started worker. In particular, `quiet` supplies
the process-local Kimiflow quiet level and loads FirstMate's stock Calm extension, so their panes stay calm.
This changes presentation only. An already running worker must be restarted because Pi cannot add presentation
extensions retroactively.

FirstMate's Herdr backend is experimental. A failed Herdr spawn must remain an explicit FirstMate failure; there
is no hidden Kimiflow process fallback or false `recovered` state. Old sessions created by Kimiflow's removed
custom Pi/Herdr bridge remain diagnostic history but are not resumable by this architecture.

The adapter uses project/run-scoped FirstMate Homes, so different projects do not share a fleet lock. Kimiflow
runtime state must remain local: tracked `.kimiflow` paths are rejected, while normal untracked state is added to
the repository-local Git exclude. Local non-`main`/`master` default branches are exposed to unchanged FirstMate
through one owned reversible marker only after the branch is locally or remotely proven. Product delivery still
uses stock `fm-merge-local.sh`; Kimiflow has no alternate merge path.

Without FirstMate, Kimiflow still works directly in the current Pi session and project, without pretending the
Captain→Main hierarchy exists. Codex, Claude Code, and the optional terminal runner remain independent.

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

The native adapters launch the already authenticated Codex or Claude Code CLI and preserve its resumable
session identity. Ordinary Codex turns retain the `workspace-write` sandbox. Read-only Research/Review
Work-Units instead require provider-bound read-only policy, empty inherited hooks/settings/MCP capability,
measured usage, deterministic serial completion, and process-group cleanup. Solution candidates and their
fresh selector run in separate empty sealed roots with no filesystem, tools, settings, MCP, hooks, or resume.
Only winner and strongest-alternative digests survive successful selection; raw candidate text is discarded on
every terminal path.

Every adapter uses the same `.kimiflow/` state, gates, and memory; none adds a daemon, second memory store, or
worktree. A persisted turn limit plus one final recovery turn prevents an endless loop; an exhausted run stays
explicitly resumable instead of claiming completion.

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

Clients that speak MCP can use the same bridge through the local stdio facade; no network server or second
workflow engine is introduced:

```bash
hooks/kimiflow-mcp.sh --root /path/to/project
# Mutating clients additionally launch it with the active owner identity:
hooks/kimiflow-mcp.sh --root /path/to/project --host codex --session-id <session-id>
```

The server implements MCP `2025-11-25` lifecycle, ping, and exactly four tools: `kimiflow_status`,
`kimiflow_context`, `kimiflow_scorecard`, and `kimiflow_action`. Read tools remain available without an owner;
actions fail closed unless the launch identity matches the Active Run and still pass through its cursor, CAS,
action-ID, and replay checks. KimiTalk, an IDE, or a local-model host can therefore integrate one immutable
Kimiflow release without forking Kimiflow.

### Optional bounded code intelligence

`hooks/code-intelligence.sh` can query an explicitly configured local Command/SCIP/LSP bridge for typed code
relationships on eligible `large` cross-file work. It is never auto-discovered, never runs for local/small work,
and returns to Project Map/`rg` on timeout, stale snapshots, dirty-workspace mismatch, oversized output, or any
unsafe path. New providers begin with content-free Shadow evidence. A green holdout plus Shadow permits one
replacement Canary path; only five clean verified Canary outcomes permit Active use. A quality, retry, token,
or freshness regression revokes it automatically. The provider returns bounded `path:line` facts, not free-form
prompts or a second memory store, so normal runs pay zero additional context tokens.

Before trusting a new host adapter, `hooks/adapter-conformance.sh --adapter <command> --project-root <root>`
runs start/resume, event, usage and claimed root-confinement probes inside a disposable Kimiflow-owned area and
emits only a digest/count receipt. This is cooperative black-box compatibility evidence, not OS process
attestation: the host must trust or separately sandbox the configured executable, and every receipt exposes
`host_trust_required=true` plus `os_process_attestation=false`. Repository maintainers exercise the model-free
retrieval scorer in CI.

### Optional continuity for architecture changes and multi-run programs

Normal single-run Kimiflow stays unchanged. For larger work, three explicit local commands are available:

- `hooks/build-replan.sh` sends Phase 5 back to planning only with current evidence that a PLAN assumption is false; normal test failures stay in Build.
- `hooks/project-delta.sh` records a verified architecture change after a successful committed run and injects it later only when current affected paths intersect.
- `hooks/program-engine.sh` validates a `.kimiflow/programs/<name>/PROGRAM.json` DAG and selects one deterministic next-ready run.

The Program Engine is deliberately serial and mechanical. It journals and durably acknowledges activation,
claims each run exclusively, binds terminal evidence and final checks, but never starts an agent, run, branch, or worktree. Projects that do
not create a Program or Project Delta pay no model-context cost. See
[`references/program-v1.schema.json`](references/program-v1.schema.json) and
[`reference.md`](reference.md#optional-project-continuity-and-program-scheduling).

### Optional project release profiles

An explicit `kimiflow release` or “Release Flow” can use a local, provider-neutral release profile under
`.kimiflow/release/`. Kimiflow discovers tracked release controls, binds a model audit to their exact digests,
and reuses that audit until a control changes or a real release failure occurs. One request authorizes one
serial release run; mutating steps require mechanical preconditions and postconditions, uncertain effects are
never blindly replayed, real failures require an audit bound to the exact failure receipt, and project final
checks must pass. Audit improvement findings are evidence-bound
advisories and never modify a live publication. Projects that never invoke release mode load no release
profile context.

Schema-v2 profiles add typed public runtime inputs, project-and-target-bound private release memory, ephemeral
provider identity, granular retry classes, and exact Phase-6 evidence reuse. Non-GitHub releases use the generic
`environment` identity with only project-declared ephemeral credentials and must declare at least one public
publication target, so changing a registry, App Store or internal destination cannot reuse another target's
memory. The release effect must consume that target explicitly; GitHub is an optional adapter that
prefers a native token, then its
local fallback reuses the account proven by a successful release (or the latest release author on first use)
and revalidates repository write capability without switching global `gh` state. Credentials, raw inputs,
command output and absolute paths are never
persisted. Credential-bearing temporary homes are forced outside the project, guarded by an independent
controller-death cleanup process and reclaimed through local leases after a host restart. Provider output is
capped while the command runs, internal repository discovery uses a fixed system Git rather than ambient
`PATH`, and `env` wrappers cannot replace sealed HOME/XDG/provider configuration. Runtime artifacts named by
`relative_path`, static local effect arguments or affected paths are streamed through a bounded built-in
credential scan before an effect. Directory membership and bytes are snapshot-bound; ZIP/tar members are
inspected, while unsafe, encrypted, nested or unsupported containers fail closed. Secret-looking descendants,
known token shapes and the active ephemeral credential itself fail closed without persisting content. Unused per-release
inputs such as a new tag do not invalidate an independent check; every relative-path input actually consumed by
a command, its affected bytes, environment and adopted external tool fingerprints must still match. Unchanged
releases therefore skip repeated discovery, audit, model work and already-current checks. Within one interrupted
generation, completed unauthenticated checks are reused only while their path, environment, PATH and tool
context still matches; authenticated checks always rerun. Release memory is file- and directory-fsynced
idempotently before the completed marker; a completed-run retry repairs missing or malformed memory without
repeating project work;
actual project checks, builds and provider operations still run whenever their evidence is absent or stale.
Provider-authenticated checks are never reused; they always run with the current ephemeral release identity.
Local content-free metrics separate control, check, build and provider work—there is deliberately no fixed
release time budget. See [`references/release-profile-v2.schema.json`](references/release-profile-v2.schema.json).
`kimiflow release` reports an existing ready v1 profile as a one-time `upgrade_required` migration and infers
its real provider from tracked controls; an active v1 generation finishes safely first. Direct v1 execution
remains compatible.

## Demo

![Kimiflow feature flow from confirmed intent through executable evidence, bug-cascade repair, human-readable memory, and an atomic local commit](docs/demo/kimiflow.gif)

> Scripted illustration of the current feature conversation, codebase check, evidence classes,
> relevance-aware review, and local commit. The source and real-recording guide
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
| `kimiflow release` | Import/re-audit if needed, then execute one explicit project release profile. |

Useful explicit forms:

```text
/kimiflow <feature-or-bug>
/kimiflow --fix <bug>
/kimiflow --verify-feature <feature-or-path>
/kimiflow <request> --prepare
/kimiflow --resume <slug>
/kimiflow --project-map quick
/kimiflow release
```

Every non-trivial feature—including an already prepared plan—starts with a short product discussion in
the user's language. Kimiflow inspects current code, then shows the understood problem, observable
success, boundary, two to five relevant options, and what is included, later, or excluded. The user can
discuss and correct that draft before choosing `scope_ready`. Focused research then compares the current
code, primary sources, and confirmed scope. A final two-to-seven-step product flow is accepted only through
`confirmed` or replaced through `corrected`; generic chat or a timeout confirms nothing. The user owns
WHAT/WHY, while architecture, libraries, data models, tests, and other technical HOW stay with the agent.
Exact trivial work and fixes keep their direct routes.

## Eight Phases

| Phase | What happens |
|---|---|
| 0 Setup | Inventory every worktree; route clean primary directly or up to three busy-primary runs into the owned Fleet before FIFO queueing. |
| 1 Clarify | Show a code-informed problem/success/options draft, let the user discuss it, then lock the corrected final product flow through explicit structured actions. |
| 2 Understand | Snapshot current affected paths and bytes, check `reuse → evolve → new`, and compare focused research with the confirmed scope. Fixes reproduce and prove the cause. |
| 3 Plan | Write a flat minimum-complete plan, testable acceptance criteria, and up to five decisions classified as `review_only`, `spike_required`, or `runtime_required`. |
| 4 Review | Resolve plan blockers and pause only for a material authority, scope, risk, privacy, cost, or irreversible decision. |
| 5 Implement | Apply the smallest accepted change, normally sequentially; fixes preserve red evidence before production code. |
| 6 Verify | Execute the required acceptance, regression, spike, and runtime evidence and prove that every locked requirement is covered. |
| 7 Review and commit | Classify findings by contract, supported path, impact, and proportionality; scan material findings for a five-surface bug cascade, repair each proved root once, then create and prove the named-path local commit. |

## Mechanical Gates

"Mechanical" means a tested script or hook decides the boundary, not a prose self-report.

| Gate | Enforced boundary |
|---|---|
| Workspace preflight | Every linked tree and dirty path is classified; up to three owned Fleet trees receive exclusive leases, revalidation, serialized candidate-first integration, and ancestry-gated archive. |
| Product Intake, Clarify and Discovery gates | Planning and writes stay blocked until the user explicitly marks scope ready and confirms the final product flow; generic chat, defaults, and timeouts never confirm it. |
| Current-code and plan gates | Every affected path is bound to current HEAD/type/bytes; discovery proves `reuse → evolve → new`, and material decisions declare their required evidence class. |
| Plan-blocker and review gates | Acceptance mappings and evidenced `BLOCKER/HIGH` findings are resolved within a bounded budget; every material review class carries one root-cause cascade with five evidence-bound probes, while immaterial edges stop before repair and protected impacts cannot be waived. |
| Implementation-conformance gate | Researched decisions, invariants, affected paths, exact checks, and every locked product requirement converge in Phase 6; finish additionally proves the committed delivery matches. |
| Adaptive execution controller | Run-wide no-progress and budget pressure select a bounded recovery action; mandatory quality gates remain intact. |
| Evidence evaluation | Four critical workflow behaviors run once in CI against a sealed prior-release baseline; artifacts contain bounded metadata and digests, never prompts, output, code, secrets, or absolute paths. |
| Local run control plane | Hosts receive one readiness/cursor contract; shared locking, owner proof and action receipts make supported item mutations fail closed and replay-safe. |
| Work-Unit and Solution Search gates | Typed read-only units bind provider isolation and measured budgets; bounded search has a zero-call off path, sealed candidates, a fresh selector, and content-poor receipts. |
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
- A deterministic classifier elevates scope from subsystem, data, security, integration, and irreversibility
  evidence, but never invents a missing product decision.
- Large materially changing contexts roll over only under measured pressure; small runs remain unchanged.
- Lower-cost model routes are earned by five comparable clean outcomes, revoked on regression, and never used
  for critical-risk work.
- Self-verifying model profiles earn embedded code review only after five runtime/policy/prompt-bound clean
  samples. Exactly one of ten matching routine runs remains an independent audit; critical work keeps an
  independent ensemble. Mechanical tests, diff, scope, secret and evidence gates never disappear.
- Optional code intelligence is Shadow-first and replaces lexical context only after evidence; it never adds a
  duplicate model path or tokens to small/local work.
- Deterministic behavior evaluation adds no model call. Model-judged calibration is represented by a
  non-executing release plan and stays outside normal runs and CI.

`small` and `quick` skip broad memory recall and the **Vault Pulse** by default. An explicit user cue that a
similar bug or fix existed before instead triggers one targeted local recall with at most five hits at any scope,
without provider searches.
Current-state checks and the final learning review still run at every non-trivial scope.

Domain complexity and operational impact are separate conditional contracts. When active, each requires one
typed Research row, an acceptance-linked Plan check, and matching Verification evidence. When inactive they add
no prompt ceremony.

## Project Intelligence and Memory

Kimiflow can create a local project map under `.kimiflow/project/` with codebase, architecture,
convention, test, and flow evidence. Later runs check affected sections and refresh only stale areas.
The map is optional, local by default, and never blocks normal work when absent.

The local Memory Router stores bounded project facts, decisions, standards, run history, and
evidence-backed learnings. New project learnings start `probationary`: they are available to targeted
recall but stay out of always-on context, proposals, and portable/Vault handoffs. Two later verified
helpful applications of the same fingerprinted learning content with still-current evidence make them
`durable`; rewritten content cannot inherit old success, and a verified contradiction, content drift, or
evidence drift demotes trust again. Missing maturity on older rows preserves their legacy durable behavior.
The fingerprint covers every Recall-visible field except explicit lifecycle metadata, so a future field cannot
silently inherit earlier verification.
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
0–5 utility score and the verified-use state. Terminal runs invoke `lifecycle --write` automatically only after
the terminal state commits and outcome persistence succeeds. A cooperative 20-second deadline rolls back the
bounded learning and text-derivative transaction before the 30-second host timeout and is disarmed at successful derivative commit;
any timeout or other curation error is reported
without creating a user gate. Unchanged writes are byte-idempotent. It promotes/demotes trust metadata
and quarantines only strictly parsed stale rows that are provably unused with a unique ID. Missing sealed Recall
evidence fails curation closed; a legacy missing learning ID is recovered only from that sealed hit. Outcome
persistence and lifecycle share one local ledger lock, reject duplicate-key rows, count each run once, and use
serialized ledger order rather than editable timestamps for trust causality. Persisted Recall selection/usage and
lifecycle also share the usage-ledger lock under one physical identity even through root aliases, so a just-used
row cannot be quarantined by a race. Durable candidates
rank ahead of locality before the bounded candidate window. Failed outcome persistence leaves
the run resumable for an autonomous retry. An
atomic path exchange verifies displaced source identity/mode and the installed candidate; bounded
re-exchanges promote a later writer without ever removing the canonical path, while an unresolved race retains an
explicit local recovery copy. Unsupported native exchange fails closed before mutation. Then
`lifecycle --restore <id> --write` restores one row only while its evidence is still exact. For optional
cross-project handoff, `capsule --write` creates a mode-0600 local privacy capsule containing at most 20
fresh, durable, allowlisted six-field projections. Vault sync uses the same projection and never exports source IDs,
paths, evidence references, credential or JWT shapes, dotted/dotless emails, private/security rows, or unsafe content.
The outcome writer retains the newest complete rows below the stricter lifecycle ceiling, so normal long-running
projects do not eventually require manual ledger cleanup.

Safe durable learnings can also enter a local, Obsidian-independent graph at `~/.kimiflow/memory/`. Its
`INDEX.md` and one Markdown file per learning are deliberately human-readable; each note links to at most five
related notes. Project-specific bindings are stored separately, so one project can revoke its association
without deleting a learning still proven by another project. Files are private by default, bounded, created
atomically, and rejected on malformed content, symlink substitution, stale evidence, or capacity overflow.
Kimiflow reads this graph through the same bounded recall path; it is not a second workflow engine or a source
of truth above current code and tests.

An Obsidian Vault is optional. Without it, project-local memory and every quality gate continue to
work, and the Markdown graph provides cross-project recall. With authenticated Vault MCP tools, Kimiflow can
additionally recall and export curated, non-private learning. API keys are never stored in `.kimiflow/`.

Vault reads are namespace-bound: foreign project paths and unsafe fields are rejected before selected content
enters a run, then locally deduplicated and capped. Old terminal run artifacts can be archived one at a time
after a safe age while preserving verified evidence and a small resumable stub; active runs and learnings are
never retention candidates.

See [`reference.md`](reference.md#memory-router--learning-loop-phase-2-recall--phase-7-learn) for the
artifact and privacy contract, and [`reference.md`](reference.md#vault-conventions-phase-2) for Vault
setup details.

## Workspace Safety and Resume

An active run records its owning Codex or Claude session. Other sessions may read, discuss, and plan.
Before writing, Kimiflow inventories every checkout. A clean/free primary stays direct with no broker
state. If primary is dirty or another run owns it, Kimiflow automatically creates up to three locked,
owned `codex/<slug>*` worktrees; further runs queue FIFO without asking for another confirmation.
Unrelated ignored files do not make an otherwise safe checkout busy. Kimiflow-owned ignored run
artifacts are preserved during retirement without weakening ancestry, ownership, or integration checks.
Phase-3 path/contract declarations and the exact PLAN bytes establish exclusive Primary/Fleet leases;
`blocked_by` names the winning owner and every Primary advance requires revalidation. Delivery uses
merge-tree preflight, no-shell project checks on the combined candidate before mutation, an owned-branch
reconciliation commit when needed, and an ff-only primary update followed only by mechanical Git
integrity receipts. Conflicts remain recoverable as `needs-reconcile`. Retirement requires
terminal state, green receipts and ancestry, then crash-recoverably archives the complete checkout and
matched Git metadata. Manual and Codex-managed trees are never mutated.

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
- High-capability model guidance is treated as threat-model input, not runtime attestation. The
  [GPT-5.6 System Card](https://deploymentsafety.openai.com/gpt-5-6/introduction) and official
  [Claude Opus 5 prompting guidance](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)
  map to provider-neutral scope, permission, delegation, evidence, and `user_required` gates. Unofficial prompt
  captures may motivate an eval but never define Kimiflow's contract.
- This is a pre-1.0 workflow plugin; rerun compatibility checks after host upgrades.

## Documentation

- [`reference.md`](reference.md) - complete workflow and gate contract.
- [`COMPATIBILITY.md`](COMPATIBILITY.md) - supported host primitives and upgrade checks.
- [`docs/architecture.md`](docs/architecture.md) - canonical engine, adapters, hooks, and data flow.
- [`docs/codebase.md`](docs/codebase.md) - repository map and ownership boundaries.
- [`docs/testing.md`](docs/testing.md) - local, smoke, and CI verification.
- [`examples/`](examples/README.md) - small fix, risky fix, and feature walkthroughs.
- [`evals/`](evals/README.md) - deterministic evidence checks and behavioral release calibration.
- [`CHANGELOG.md`](CHANGELOG.md) - release history.

### Public schemas

All machine-readable contracts are versioned JSON Schema files. Internal run artifacts remain internal; a new
public schema is added only when an external producer or consumer needs a stable contract.

| Area | Schemas |
|---|---|
| Agent adapters | [`adapter-protocol-v1`](references/adapter-protocol-v1.schema.json), [`adapter-conformance-v1`](references/adapter-conformance-v1.schema.json) |
| Code intelligence | [`code-intelligence-provider-v1`](references/code-intelligence-provider-v1.schema.json), [`code-retrieval-eval-v1`](references/code-retrieval-eval-v1.schema.json) |
| Programs and releases | [`program-v1`](references/program-v1.schema.json), [`release-profile-v1`](references/release-profile-v1.schema.json), [`release-profile-v2`](references/release-profile-v2.schema.json), [`runtime-release-v1`](references/runtime-release-v1.schema.json) |
| Security | [`coverage-v1`](references/security-coverage-v1.schema.json), [`deep-plan-v1`](references/security-deep-plan-v1.schema.json), [`deep-result-v1`](references/security-deep-result-v1.schema.json), [`eval-v1`](references/security-eval-v1.schema.json), [`findings-v1`](references/security-findings-v1.schema.json), [`promotion-v1`](references/security-promotion-v1.schema.json), [`report-v1`](references/security-report-v1.schema.json), [`scan-manifest-v1`](references/security-scan-manifest-v1.schema.json) |

## License

[MIT](LICENSE)
