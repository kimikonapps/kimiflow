# Changelog

The public release history starts at **0.5.0**. Earlier development artifacts are no longer listed as releases.

## Unreleased

_No unreleased changes._

## 0.5.3

Claude Code becomes a first-class host of the shared wrapper, without new moving parts.

- Make the shared `skills/kimiflow` wrapper host-neutral for Codex and Claude Code.
- State how Claude Code runs a cheaper worker: a subagent with an explicitly chosen `model`, while
  planning and review stay in the session model; a subagent without an explicit model is not cheaper.
- Add no new host, hooks, agents or renderer complexity.

## 0.5.2

Model-independent planner-worker collaboration focused on total delivery cost.

- Require a different, cheaper worker model for explicitly selected delegation.
- Use more capable planner/reviewer models; the planner normally performs the review.
- Review actual changes, integration, requirements and check evidence, including corrections.
- Select models using current availability, pricing and task suitability, without fixed model versions.
- Include planning, review and rework in cost assessment; no measured savings are claimed.
- Package the collaboration guide and update English/German documentation.

## 0.5.1

Small feature briefs, early usable results and lightweight feedback for better feature delivery.

- Add a short feature brief, early usable slices and explicit user-scenario acceptance.
- Provide an optional local five-feature results table without new workflow gates.

## 0.5.0

Initial release of the minimal Kimiflow working agreement for coding agents.

- A short shared skill for goals, acceptance criteria, project boundaries and verification.
- Codex, Claude Code and Pi entry points using the selected model and native host tools.
- Optional plain continuation notes for long or interrupted tasks.
- An optional stateless helper for explicit project checks, source snapshots and POSIX process cleanup.
- A 14-file plugin package with reproducible generation and a content fingerprint.
- 31 automated tests and CI coverage on Linux/Python 3.9 and macOS/Python 3.14.

The skill's additional benefit over native agent work has not yet been established by model benchmarks.
