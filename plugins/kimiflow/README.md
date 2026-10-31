# Kimiflow

An operating kit for planner-led work with coding agents, plus a short working agreement for
everything else: define the result, use native tools, verify the work.

[Deutsch](README.de.md) · [Workflow](SKILL.md) · [Release 0.6.2](https://github.com/kimikonapps/kimiflow/releases/tag/kimiflow--v0.6.2)

## What Kimiflow does

- **Operating kit.** `/kimiflow init` puts a set of project documents into `.kimiflow/ops/`: binding
  rules, standing worker rules, one handoff, one list and plan templates. From the next session on,
  the planner session reads them and follows the [operating model](references/operating-model.md).
  The kit is project-agnostic; the project's own rules go into its OPERATIONS.md.
- **Flow by risk.** Data writes, migrations, security, public API and deletes get the full flow
  (feature sheet → plan → audit → worker → review); UI work gets a short plan and one review; a bug
  fix starts with a failing test. The only gates are the ones the operating model names: the owner's
  OK on the feature sheet, a plan audit where the risk calls for it, and the owner-items check before
  a merge to main.
- **Delivery support elsewhere.** Outside an initialized project the short working agreement applies:
  outcome, acceptance, boundaries, relevant project conventions, project-native checks.
- No controller, run state, API key or new service. Kimiflow is documents, conventions, one helper
  script and, in Claude Code, two hooks.

The public release history starts with **0.5.0**.

## Quick start

1. Install the plugin (see [Installation](#installation)).
2. In the project, run `/kimiflow init` in Claude Code (`/kimiflow:kimiflow init` if another
   `/kimiflow` command exists). The agent runs the init script, analyses the project and fills the
   project sections of OPERATIONS.md. Changes to CLAUDE.md, AGENTS.md or settings are proposed and
   applied only with your OK.
3. Start a new session. From then on each session starts with the kit.

## Operating kit

`init` resolves the main checkout (also when called from a worktree) and creates:

```text
.kimiflow/ops/
├── OPERATIONS.md          binding rules, short form; rule register; role and model table
├── OPERATIONS-history.md  reasons, incidents and flakes behind the rules
├── rules-worker.md        standing rules for workers, reviewers and auditors
├── HANDOFF.md             current state and next steps only
├── LIST.md                the one list of open and deferred items
├── LIST-done.md           finished items
├── plans/                 templates: feature sheet, plan, worker brief, review brief, build order
├── archive/               old handoffs and plans
├── research/              findings worth keeping
└── .kit.json              kit version and template hashes, used by update
```

- `init` never overwrites an existing file; a second run changes nothing.
- It adds `.kimiflow/` to Git's local exclude file, so the kit stays local and applies in all
  worktrees. It refuses to run when the repository tracks files under `.kimiflow/`.
- It refuses when `.kimiflow/ops/` already holds an earlier task's `STATE.md` or `NOTES.md`, and it
  lists legacy runs (`.kimiflow/<task>/STATE.md`) without touching them.

## Roles and models

| Role | Responsibility |
| --- | --- |
| Planner (the session) | Shapes features with the owner, plans, writes briefs, accepts results, commits. |
| Worker | Implements one bounded brief and runs the named checks. |
| Reviewer / auditor | Checks a plan or a diff from a fresh context. |
| Architect | Advises on rare, hard cross-cutting questions; never edits. |

The project's OPERATIONS.md holds the role table with model and effort per role. A worker may run on
the same model as the planner: its empty context already saves tokens. A cheaper worker model is an
option, not a requirement. The dated [model guide](references/models.md) covers the current Claude
models, their documented strengths and how to brief them; it excludes no model. Current availability
and pricing decide. Count planning, review and rework when judging cost; cheaper worker calls alone
do not prove a cheaper result. See the [planner-worker agreement](references/planner-worker.md).

In Claude Code the plugin ships the agents `kimiflow:worker`, `kimiflow:reviewer` and
`kimiflow:architect` (no Edit/Write tools; read-only by instruction, Bash only for reading). They have no model of their own; the planner passes the role
table's model when it starts one. A subagent with no model setting inherits the session model.
A user agent with the same name takes precedence, so use the `kimiflow:` prefix.

## Updating the kit

`/kimiflow update` compares the project's kit files with the templates shipped by the installed
plugin. By default it writes nothing and reports each file as new, safe, conflict, unchanged or
unknown_base; conflicts and unknown_base files come with a diff.

- `--apply-safe` copies new files and files you never edited; it never touches conflicts.
- `--resolved <path>` records a file you merged by hand as in sync.
- The kit version in `.kit.json` moves to the plugin version only when every file is in sync.

## Hooks and git guard (Claude Code)

The plugin registers two hooks through `.claude-plugin/plugin.json`. Both run `python3` from PATH.

- **Session start.** In a project initialized locally, each session (startup, resume, clear,
  compact; also in a worktree) gets the absolute paths of the kit files and the reading order:
  OPERATIONS.md, then HANDOFF.md; LIST.md is searched, not read whole. It adds a notice when the kit
  is older than the plugin and when commits landed since HANDOFF.md was last changed. It also warns
  when OPERATIONS.md exceeds 12 KB or HANDOFF.md exceeds 6 KB.
- **Trust gate.** The hook speaks only for a kit set up by `init` here: Git's local exclude lists
  `.kimiflow/` and no kit file is tracked. A cloned repository cannot promote its own files to
  operating rules.
- **Git guard.** Before each Bash command it blocks bulk staging (`git add -A`, `--all`, `.`, `:/`,
  `*`) and a `git commit` whose command text carries AI attribution, recognised by the sender address
  or product link of an AI tool. A human co-author line stays allowed. The guard is active only in
  an initialized project.

## Codex and Pi

Codex and Pi get the skill texts only: no hooks, no agents. The skill's `init` and `update` steps run
the same helper script there. Sessions do not get the kit automatically; `init` proposes a line in
AGENTS.md that points Codex at `.kimiflow/ops/OPERATIONS.md`.

## Use

Invoke `/kimiflow <task>` in Claude Code or `$kimiflow <task>` in Codex. Pi loads the same agreement.
Explicit `direct` / `direkt` bypasses the skill's flow; in an initialized project the session-start
context, the git guard and the operating rules in OPERATIONS.md still apply, to every task.
Elsewhere, automatic selection stays limited to substantial authorized features; ordinary
fixes, reviews, refactors and small changes stay direct unless explicitly requested. An empty
invocation asks for the task, without running a project inventory.

Read-only requests remain read-only. The host and project own commit and publication policies.
Starting from an idea such as “I want X so that Y” is covered by the [feature guide](references/feature-work.md).

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
For existing installations, see [compatibility](COMPATIBILITY.md) and [migration](MIGRATION.md).

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

That is an empirical question, and it is not answered yet. No benchmark of the operating kit exists.
A fair comparison runs the same tasks with and without the kit, using the same model, start state,
tools, permissions and total budget, and judges acceptance, remaining defects, interruptions, elapsed
time and measured usage. [Evaluation guidance](evals/README.md) describes the setup. The available
development pilots do not establish a quality or cost advantage.

## Development

```bash
python3 scripts/render_skills.py
python3 scripts/build_plugin.py
python3 -m unittest discover -s tests -v
python3 scripts/build_plugin.py --check
```

The package has an explicit allowlist; tests, development tools, histories and user data are not shipped.
See [architecture](docs/architecture.md), [codebase](docs/codebase.md), [testing](docs/testing.md)
and [changelog](CHANGELOG.md).

[MIT License](LICENSE)
