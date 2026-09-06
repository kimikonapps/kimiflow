# Feature work

The user can start with “I want X so that Y.” Draft the brief from that request and current code;
do not require the user to fill a form. Use the user's language. Keep it in the conversation or the
existing host plan; use NOTES.md only when a handoff needs it. Ask only about missing product decisions
that materially affect the result, and reuse existing authorization.

## Short brief

- **Problem / value:** Who needs this, and what should improve?
- **Behavior:** What can the user do, and what happens?
- **Done when:** Observable acceptance, with a concrete user scenario and relevant failure behavior.
- **Boundaries:** What is excluded, and which existing behavior must remain intact?

## Build and accept

For larger features, first deliver the smallest usable end-to-end slice. A small feature may already
be that slice. Test the greatest plan-changing uncertainty early with a focused check or spike.
Do not create an extra prototype when the existing code or tests settle it.

Show the usable result through a running flow, relevant output or screenshot, as appropriate. Continue
authorized work while feedback is pending; a demo is not a mandatory approval checkpoint. Ask again
only when a correction introduces a material product decision.

Before completion, execute the relevant technical checks and walk through the brief's user scenario.
Keep these separate: tests passing does not establish that the feature is useful or complete.
Report unavailable checks or missing feedback accurately. Do not claim the user's acceptance without it.

## Five-feature pilot (only when agreed)

Use one local `.kimiflow/FEATURE-RESULTS.md` per participating project, or the user's chosen location.
Preserve existing content. Record one row per feature; update that row for later feedback or defects.
Stop adding features after five and discuss the observations. This is a short trial, not a permanent
ledger or a completion gate. Missing feedback never blocks delivery.

| # | Feature / model / host | First usable result | Outcome / check evidence | Rework | Open or later issues | User feedback |
|---|---|---|---|---|---|---|
| 1 | — | — | — | — | — | — |
| 2 | — | — | — | — | — | — |
| 3 | — | — | — | — | — | — |
| 4 | — | — | — | — | — | — |
| 5 | — | — | — | — | — | — |

“—” means not recorded, never zero or passed. Use observed time to the first usable result only when
available; otherwise record its milestone. Note material correction rounds, not each normal edit.
Link existing task/check evidence without copying private logs. Leave user feedback pending until
the user actually supplies it. Add at most one concrete lesson from a completed feature; a new global
rule needs a repeated problem that existing tools or project rules do not already solve.

These observations help improve collaboration. They are not a controlled native-versus-Kimiflow
comparison and do not establish model-quality or cost superiority.
