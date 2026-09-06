# Kimiflow

A small working agreement for coding agents: define the result, use native tools, verify the work.

[Deutsch](README.de.md) · [Workflow](SKILL.md) · [Release 0.5.0](https://github.com/kimikonapps/kimiflow/releases/tag/kimiflow--v0.5.0)

## What Kimiflow does

- A short skill: outcome, acceptance, boundaries and relevant project conventions.
- The user's selected model and the host's existing tools, planning and workspace isolation.
- Project-native tests and CI. An optional stateless check helper when it adds useful source verification.
- One short continuation note only when task history is insufficient.

Kimiflow works with the tools you already use. No API key or new service is required.
The public release history starts with **0.5.0**.

GPT-6 Astra, Fable 5.1 and local models use the same agreement. Constrained models can receive smaller
coherent tasks and exact commands. Kimiflow does not select a paid fallback or assume local means weak.

## Building features

Start with an idea such as “I want X so that Y.” The agent turns it into a short brief, checks the
largest uncertainty early and delivers a usable end-to-end slice for larger features. Completion
combines technical checks with a concrete user scenario. See the [feature guide](references/feature-work.md).
An agreed five-feature pilot can record outcomes and feedback locally; it creates no approval gate.

## Use

Invoke `/kimiflow <task>` in Claude Code or `$kimiflow <task>` in Codex. Pi loads the same agreement.
Explicit `direct` / `direkt` bypasses it. Automatic selection stays limited to substantial authorized
features; ordinary fixes, reviews, refactors and small changes stay direct unless explicitly requested.
An empty invocation asks for the task, without running a project inventory.

No special plan/full/build/release mode is needed. State the desired outcome in the normal request.
Read-only requests remain read-only. The host and project own commit and publication policies.

## Installation

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

After installing or updating the plugin, start a new host task to load the skill.
For existing development installations, see [development compatibility](MIGRATION.md).

## Optional check helper

Prefer the project's existing checks. If a snapshot comparison helps, run explicit argv commands:

```bash
python3 /absolute/plugin/root/scripts/check_change.py --root /absolute/project \
  --check '["python3","-m","unittest","discover"]'
```

Replace the example with the actual project command. Repeat `--check` for several checks; `--timeout`
sets seconds per check. The helper needs Git and Python 3.9+, writes no state, changes no staging and
makes no commit. It stops on failure/timeout and rejects differences between source snapshots before
and after each check. Ignored build output and untracked `.kimiflow/` notes are excluded; tracked files
are included. On POSIX, surviving check-process-group members are stopped and the check fails.

It is not a sandbox, continuous monitor or proof of test quality. Detached processes outside its group
and changes restored between snapshots are outside its observation. Checks must complete synchronously.
If the host/CI already provides adequate verification, do not add this wrapper merely for ceremony.

## Does it help?

That is an empirical question. Compare native agent work with this minimal agreement using the same
model, start state, tools, permissions and total budget. Judge acceptance, remaining defects,
interruptions, elapsed time and measured usage. [Evaluation guidance](evals/README.md) keeps this simple.
The available development pilots do not establish a quality or cost advantage.
Model performance has not yet been benchmarked for the public release.

## Development

```bash
python3 scripts/render_skills.py
python3 scripts/build_plugin.py
python3 -m unittest discover -s tests -v
python3 scripts/build_plugin.py --check
```

The package has an explicit allowlist; tests, development tools, histories and user data are not shipped.
See [architecture](docs/architecture.md), [testing](docs/testing.md) and [changelog](CHANGELOG.md).

[MIT License](LICENSE)
