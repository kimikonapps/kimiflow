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

Kimiflow can auto-route substantial feature work; fixes and small low-risk changes stay direct unless
you explicitly invoke `/kimiflow` in Claude Code or `$kimiflow` in Codex. Explicit `direct` always
bypasses it. It answers in the language you use.

## Why Kimiflow

Native agents already plan, delegate, and review. Kimiflow adds a durable contract around those
abilities:

- state and evidence live under `.kimiflow/<slug>/`, so a run can resume safely;
- plan and code-review findings use tested fail-closed resolvers;
- fixes require reproduction, a proven cause, and red/green evidence;
- risky decisions and commits stop for human approval;
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
bash "${CODEX_HOME:-$HOME/.codex}/.tmp/marketplaces/kimiflow/hooks/install-codex-hooks.sh"
```

Restart Codex and open a new task, then invoke `$kimiflow`. To update:

```bash
codex plugin marketplace upgrade kimiflow
bash "${CODEX_HOME:-$HOME/.codex}/.tmp/marketplaces/kimiflow/hooks/install-codex-hooks.sh"
```

The stable hook installer writes managed wrappers to `${CODEX_HOME:-~/.codex}/hooks`. Running it
from the marketplace checkout keeps those wrappers on a stable path across versioned cache updates.

For local development, register this checkout instead:

```bash
codex plugin marketplace add .
bash hooks/install-codex-hooks.sh
```

## Demo

![Kimiflow launcher and gated feature/fix flow](docs/demo/kimiflow.gif)

> Scripted illustration of the current launcher and core loop. The source and real-recording guide
> live in [`docs/demo/`](docs/demo/).

## Modes

The same modes work with `/kimiflow` in Claude Code and `$kimiflow` in Codex.

| Mode | Purpose |
|---|---|
| `kimiflow full` | Strict large-scope loop; one mode-specific Preview approval before implementation. |
| `kimiflow quick` | Lean path for a small, low-risk change. |
| `kimiflow fix` | Diagnose first, show one post-diagnosis Fix Preview, then red/green verification. |
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

For `small` and `quick`, there is **no minimum question count**. A complete request needs only a
compact confirmation; technical gaps go to bounded Discovery instead of becoming repeated user
interviews. Exact trivial work may skip the loop.

## Eight Phases

| Phase | What happens |
|---|---|
| 0 Setup | Route mode, choose the smallest valid scope, check the worktree, and create durable state. |
| 1 Clarify | Feature/audit confirms behavior, scope, and outcome. A clear fix records the problem brief without an early stop. |
| 2 Understand | Inspect project knowledge and code; choose Discovery `none`, `pulse`, or `focused`. Fixes reproduce and prove the cause. |
| 3 Plan | Write a flat minimum-complete plan and testable acceptance criteria. |
| 4 Review | Resolve plan blockers. Features use a risk-based Build Preview; fixes always ask once after diagnosis with a basis-bound Fix Preview. |
| 5 Implement | Apply the smallest accepted change, normally sequentially; fixes preserve red evidence before production code. |
| 6 Verify | Check acceptance criteria, regression behavior, red/green evidence, and bounded local diagnostics. |
| 7 Review and commit | Verify review candidates, resolve blockers, curate learnings, show the diff, and stop for commit approval. |

## Mechanical Gates

"Mechanical" means a tested script or hook decides the boundary, not a prose self-report.

| Gate | Enforced boundary |
|---|---|
| Working-tree gate | New write runs start from a clean tracked worktree. |
| Clarify and Discovery gates | Required intent, source, scope, and decision evidence exists before planning. |
| Plan-blocker and review gates | Acceptance mappings and evidenced `BLOCKER/HIGH` findings are resolved within a bounded repair budget. |
| Fix Preview gate | Approval is fingerprinted against problem, diagnosis, plan, acceptance criteria, and relevant state; later changes invalidate it. |
| Red/green gate | Fixes cannot finish without recorded failing and passing evidence plus regression coverage. |
| Commit gate | Kimiflow shows the final diff and waits for explicit approval before committing. |
| Secret/state hooks | Secret-looking paths, bulk staging, and resolver calls without durable state are blocked. |
| Test gate | Large runs can block completion while the configured project test command is red. |

Scope choice, root-cause quality, and reviewer completeness still require model judgment. Kimiflow
mechanizes the evidence boundaries without pretending a tool can prove that no bug was missed.

## Token-Aware Scaling

- `trivial`: exact, low-risk work; implement, verify briefly, then commit gate.
- `small`: default; compact clarification, adaptive Discovery, one planner, bounded review.
- `large`: reserved for broad changes, new dependencies, migrations, security/privacy/money paths,
  subtle bugs, or explicit `full` runs.
- Discovery starts no worker for `none|pulse`, normally one bounded evidence worker for `focused`,
  and at most two independent lanes.
- Research may correct implementation choices, but only `required` constraints may expand scope.
- A second planner appears only for a real architecture or irreversible contract fork, not because a
  task merely looks large.
- The top model owns orchestration, synthesis, planning, review verdicts, and risky diagnosis.

`small` and `quick` skip broad memory recall and the **Vault Pulse**; both run at `scope=large` only.
Current-state checks and the final learning review still run at every non-trivial scope.

## Project Intelligence and Memory

Kimiflow can create a local project map under `.kimiflow/project/` with codebase, architecture,
convention, test, and flow evidence. Later runs check affected sections and refresh only stale areas.
The map is optional, local by default, and never blocks normal work when absent.

The local Memory Router stores bounded project facts, decisions, standards, run history, and
evidence-backed learnings. Promotion happens only after successful verification and source-freshness
checks. Changed evidence supersedes stale learning instead of silently keeping it active.

An Obsidian Vault is optional. Without it, project-local memory and every quality gate continue to
work. With authenticated Vault MCP tools, Kimiflow can recall and export curated, non-private
cross-project learning. API keys are never stored in `.kimiflow/`.

See [`reference.md`](reference.md#memory-router--learning-loop-phase-2-recall--phase-7-learn) for the
artifact and privacy contract, and [`reference.md`](reference.md#vault-conventions-phase-2) for Vault
setup details.

## Parallel Tasks and Resume

An active run records its owning Codex or Claude session. Other sessions may read, discuss, and plan.
Before writing in the same checkout they run deterministic path-conflict checks: disjoint files may
proceed; overlapping or unknown paths wait, narrow scope, or use a worktree.

Prepared and parked runs can resume from `.kimiflow/<slug>/`. If affected files changed or the plan
basis is unknown, Kimiflow revalidates before implementation instead of building a stale plan.

## Safety Boundaries

- Kimiflow auto-routes only substantial feature work with material cross-surface, integration, data,
  security, public-API, architecture, or discovery needs. Fixes, reviews, refactors, cleanup,
  docs/config, and small low-risk features stay direct unless explicitly routed through Kimiflow.
- Explicit `direct` always bypasses Kimiflow; an explicit Kimiflow request always starts it.
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
