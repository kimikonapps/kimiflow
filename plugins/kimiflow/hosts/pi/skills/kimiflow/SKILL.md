---
name: kimiflow
description: Activate the optional Kimiflow Captain when the user asks this Pi session to build with Kimiflow.
---

# Kimiflow for Pi

When the user naturally asks to build a feature with Kimiflow, call `kimiflow_activate` with the complete request.
`/kimiflow <feature request>` is an equivalent explicit convenience command.

- This Pi session becomes the conversational Captain and stays available.
- The extension starts the existing Kimiflow runner in the background; runner and Active Run remain authoritative.
- Kimiflow owns intake, planning, its normal subagents/worktrees, gates, review, recovery, and completion.
- When the Captain runs inside Herdr, Kimiflow opens one unfocused visible Pi tab for its main worker and visible temporary Pi tabs for bounded read-only subagents in the same workspace. The Captain tab keeps focus and remains conversational.
- Kimiflow owns only the exact Herdr tabs it creates and closes them by verified IDs. Herdr remains UI transport, while runner and Active Run remain workflow authority. Pi lifecycle events do not prove completion.
- Outside Herdr, the existing process transport remains available. Kimiflow never installs Pi, Herdr, or a provider.
- A request that names an existing numbered project plan such as `Run 7` uses that exact plan and must not be replaced by generic intake or confused with `Run 7.2`.
- Use `/kimiflow-status` for status. Route replies and steering only with the exact run, worker, and provider-session identities supplied by Captain attention.
- Direct Codex and Claude Kimiflow flows are unchanged.
