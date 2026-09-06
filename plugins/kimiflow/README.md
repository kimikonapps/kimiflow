# Kimiflow

**A small delivery loop for capable coding agents, including local models.**

[Deutsch](README.de.md) · [Workflow](SKILL.md) · [Optional tools](references/optional-tools.md) · [Compatibility](COMPATIBILITY.md)

<!-- capabilities:start -->
Kimiflow supports **feature and bug-fix delivery** with current-source verification and focused
review findings. Project intelligence, repo docs, memory and managed workflows are optional.
<!-- capabilities:end -->

## Release 0.4.3

[Release notes and verified runtime download](https://github.com/kimikonapps/kimiflow/releases/tag/kimiflow--v0.4.3)

This release makes the lean loop the default, removes six duplicate CI invocations without deleting
coverage, and keeps existing managed runs compatible. Review fixes reject checks that leave background
processes running and keep every headless recovery path on its managed Legacy contract.

After updating the plugin, start a new host session. Existing managed runs continue their pinned
contracts; new ordinary tasks use the lean loop. Merely pulling this repository does not replace an
installed plugin cache.

## The default loop

1. **Understand:** inspect current code, establish observable acceptance and resolve only material unknowns.
2. **Build:** use the selected agent and the host's plan, tools and worktree support.
3. **Verify and deliver:** execute relevant checks, review the diff, fix concrete failures and deliver a scoped change.

An authorized request needs no second routine confirmation. New tasks create no eight-phase state,
phase-read receipts, reviewer matrix, model-promotion ledger or required learning artifact. A long task
may keep one local `NOTES.md` for resumption. That note is advisory; current source and executed checks win.

Keep the model and effort selected by the user. GPT-6 Astra and Fable 5.1 are the current frontier
baseline, not required providers. The same loop works with a local model: smaller coherent tasks and
compact context help when tools or context are limited. Kimiflow never infers permission to use a paid
provider from an installed CLI and adds no API-key dependency.

Independent review is driven by concrete risk, not fixed seats or a different provider by default.
Existing tests, workspace ownership, named-path commits and publication authority remain important.
A review opinion is not an executed test, and a passing test suite is not proof of complete acceptance.

## Install

Requirements: Git, `python3 >= 3.9` and `jq` for the bundled legacy hooks/tools.

Claude Code:

```text
/plugin marketplace add kimikonapps/kimiflow
/plugin install kimiflow@kimiflow
```

Codex:

```bash
codex plugin marketplace add kimikonapps/kimiflow
codex plugin add kimiflow@kimiflow
```

Restart the host after updating the plugin. A source checkout does not update an installed cache.
Both hosts load the same canonical workflow; their wrappers provide only host-specific paths and tools.

## Use

Invoke `/kimiflow` in Claude Code or `$kimiflow` in Codex. A bare invocation shows project status;
a concrete task starts directly. `direct` / `direkt` bypasses the skill. Automatic routing is limited
to authorized substantial features; normal fixes, reviews, refactors and small changes remain direct.

| Mode | Result |
|---|---|
| `kimiflow full` | Thorough coverage of the requested work, without a forced approval or reviewer topology. |
| `kimiflow quick` | Small scope with the same correctness boundaries. |
| `kimiflow fix` | Reproduce, diagnose, fix and verify regression. |
| `kimiflow grill` | Clarify the idea; no implementation. |
| `kimiflow plan` | Prepare a plan; no implementation. |
| `kimiflow build` | Build the existing plan after checking its current basis. |
| `kimiflow review` | Read-only review of the requested change or feature. |
| `kimiflow audit` | Read-only subtraction analysis; edits require a selected scope. |
| `kimiflow release` | Explicitly run the optional audited project-release workflow. |

## Current-source verification

Run the actual project checks once through the installed helper:

```bash
/absolute/plugin/root/hooks/check-change.sh --root /absolute/project \
  --check '["python3","-m","unittest","discover"]' \
  --check '["git","diff","--check"]'
```

Each `--check` is an argv array, executed without an implicit shell. The helper stops on failure,
limits returned diagnostics, enforces a timeout, and rejects differences in HEAD, index or source between the snapshots taken before and after each check. Tracked bytes, nonignored untracked files and initialized submodules are included;
ignored build output and untracked `.kimiflow/` notes are excluded. It does not stage, commit, run a
model or write persistent state. On POSIX, a check that leaves members of its process group alive is
stopped and fails even when its launcher exited successfully. Checks must complete their work
synchronously; detached processes outside that group and transient changes restored between snapshots
are outside this helper's observation. A later code change requires fresh relevant checks.

The result proves only that these commands passed against the captured source. Select meaningful
acceptance/regression checks and inspect their coverage. Host permissions remain the security boundary.

## Existing runs and optional tools

Existing `Flow schema` runs retain their ownership, intake, evidence and completion contracts. Resume
with `--resume <slug>`; the skill loads the [legacy workflow](references/legacy-workflow.md) only then.
The optional headless controller and managed FirstMate crews still consume that state machine.
No migration rewrites active state or deletes stored memory.

[Optional tools](references/optional-tools.md) routes project maps, local memory/Vault, security,
Fleet integration, Solution Search and project release. They are not automatically loaded or executed
for a new task. The [legacy reference](reference.md) describes their existing contracts.

## Evidence and development

The [paired evaluator](evals/outcomes.md) currently has two confounded historical pilot pairs and zero
primary-eligible comparisons. There is no demonstrated general quality or token-cost advantage over
native agent work. Prompt-size reductions can be measured directly; actual task savings require fair
runs with the same model, permissions, tools and total budget.

The legacy same-pass prose guidance credits [no-ai-slop](https://github.com/petergyang/no-ai-slop);
it never adds another model call and is not an AI-authorship detector. Fresh work follows the host's
writing guidance and does not load a separate prose contract.

See [architecture](docs/architecture.md), [testing](docs/testing.md) and [changelog](CHANGELOG.md).

[MIT License](LICENSE)
