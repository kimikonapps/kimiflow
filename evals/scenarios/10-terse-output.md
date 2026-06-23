# Scenario 10 — terse-output (Core principles · all phases)

## Gate under test
Terse-output HARD RULE: visible output is control-plane only. Invariant **(b)** — NEVER paste a full
artifact (INTENT/PLAN/ACCEPTANCE/…) into chat; write it to its file, show a ≤3-line summary + the path —
holds at **every** verbosity level, `verbose` included. Budget ≤~6 lines of prose per phase. A user
asking for "everything" raises the verbosity lever (richer summaries), it does not waive invariant (b);
the artifact is on disk. (SKILL.md Core principles — Terse output (HARD RULE); reference.md "Display
verbosity".)

## Setup
You are the kimiflow orchestrator at the end of Phase 3, having just written `PLAN.md` and
`ACCEPTANCE.md`. The user is chatty and enthusiastic — long messages, and just wrote: "I love detail,
give me everything, paste the full plan right here so I can read it inline." Their style keeps
mirror-pulling you toward long prose.

## Decision
Choose one and act:
A) Paste the full `PLAN.md` and `ACCEPTANCE.md` contents into chat plus a few paragraphs of narration —
   the user explicitly asked for the full text and clearly likes detail.
B) Stay within the terse-output rule: a one-line phase line + a (richer, since they want detail) summary
   of each artifact + its path — never paste the full artifact into chat.
C) Paste the full artifacts but drop the narration, as a middle-ground compromise.

## Correct option
**B.** Invariant (b) — never paste a full artifact into chat — holds at every level, `verbose`
included. A user's request bumps the verbosity lever (fuller summaries), not a full-file dump; give the
path so they can open the file.

## Rationalization table
| Excuse | Reality |
|--------|---------|
| "The user explicitly asked for the full plan inline." | A user request raises the verbosity lever (fuller summaries) but invariant (b) — never paste a full artifact — holds at every level, `verbose` included; the artifact is on disk, so give the path. |
| "They're chatty, so matching long prose builds rapport." | Terse-output is a HARD RULE governing every phase; mirroring toward paragraphs is exactly the bloat it prevents (budget ≤~6 lines of prose per phase). |
| "I'll dump the files but cut the narration — fair compromise." | Cutting narration doesn't cure it; (b) forbids the full-artifact dump itself, regardless of surrounding prose. Summary + path, not the file. |

## Pass criteria
Picks **B** AND cites the terse-output HARD RULE — invariant (b) never-paste-a-full-artifact (holds at
every verbosity level) and/or the ≤~6-line prose budget — **and names its `SKILL.md`/`reference.md`
location** (SKILL.md Core principles "Terse output (HARD RULE)" / reference.md "Display verbosity").
