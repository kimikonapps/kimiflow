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
- Worker and subagent process topology is an implementation detail; Pi lifecycle events do not prove completion.
- Herdr may host Pi but is not controlled by Kimiflow. Never install or start Pi, Herdr, or a provider.
- Use `/kimiflow-status` for status. Route replies and steering only with the exact run, worker, and provider-session identities supplied by Captain attention.
- Direct Codex and Claude Kimiflow flows are unchanged.
