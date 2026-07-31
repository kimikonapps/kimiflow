---
name: kimiflow
description: Activate the optional Kimiflow Captain when the user asks this Pi session to build with Kimiflow.
---

# Kimiflow for Pi

When the user naturally asks to build a feature with Kimiflow, call `kimiflow_activate` with the complete request.
`/kimiflow <feature request>` is an equivalent explicit convenience command.

- This Pi session becomes the conversational, read-only Captain and stays available.
- Resolve the project through `kimiflow_project`; `kimiflow_activate.project` may name a registered project or exact Git root. `/kimiflow --project <name> <request>` is equivalent.
- Before Pi starts, the existing Fleet broker assigns every writing top-level task an isolated owned Git worktree. Up to three disjoint workers may run concurrently; later work queues.
- Runner, Active Run, Fleet leases, and gates remain authoritative. The private project registry is navigation metadata only and contains no task, prompt, code, answer, or transcript.
- Kimiflow owns intake, planning, its normal phase agents, gates, review, recovery, integration, and completion.
- When the Captain runs inside Herdr, Kimiflow opens one unfocused visible Pi tab per Fleet worker and visible temporary Pi tabs for bounded phase agents in the same workspace. The Captain tab keeps focus and remains conversational.
- Kimiflow owns only the exact Herdr tabs it creates and closes them by verified IDs. Herdr remains UI transport, while runner and Active Run remain workflow authority. Pi lifecycle events do not prove completion.
- Herdr's user-managed Pi agent-state integration is loaded explicitly under Kimiflow's `--no-extensions` allowlist so exact native session identity remains available on resume.
- Outside Herdr, the existing process transport remains available. Kimiflow never installs Pi, Herdr, or a provider.
- A request that names an existing numbered project plan such as `Run 7` uses that exact plan and must not be replaced by generic intake or confused with `Run 7.2`.
- Use `/kimiflow-status` for status. Route replies and steering only with the exact run, worker, and provider-session identities supplied by Captain attention.
- Direct Codex and Claude Kimiflow flows are unchanged.
