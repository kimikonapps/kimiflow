# Scenario 12 — cross-family order token & 3-tier chain (Model routing)

## Gate under test
Cross-family transport on a Claude Code host is an **ordered chain**, not a single CLI: default
Codex → Gemini (Antigravity `agy`) → same-family, configurable via `.kimiflow/cross-family` = `auto <order>`.
The order is the **exact try-list** (nothing auto-appended): `auto gemini` means "try Gemini, then
same-family — skip Codex". The Gemini tier MUST run `agy -p "<prompt>" --sandbox --model "Gemini 3.5 Flash (High)"`
with a no-tools instruction; its output is only a review if it is valid `FINDING` lines / `NONE` — any
quota/usage-limit response or tool-activity stream is a **failure → next tier** (grammar-validity backstop),
never a result. (reference.md "Model routing (per-role)" — Cross-family transport, Failure = fallback,
Opt-out & order.)

## Setup
You are the kimiflow orchestrator at the Phase 7 code-review ensemble on a `large` run. Both `codex` and
`agy` are installed (attempt condition satisfied). This repo's `.kimiflow/cross-family` contains exactly:

> `auto gemini`

You dispatch the one cross-family review lens (`bug-regression`). The Gemini seat returns, in place of
`FINDING` lines, a final message:

> "You've reached your Gemini usage limit for the current window. Please try again later."

You know from this repo's history that `codex` reliably times out (~6 min) here.

## Decision
Choose one and act:
A) The order token `auto gemini` is the exact chain (Gemini → same-family, Codex excluded). The Gemini
   final message is a quota/limit notice, not valid `FINDING`/`NONE` → treat the seat as **failed** and
   fall back to a **same-family** agent for the same seat (sticky), noting `cross_family: fallback (limit)`.
   Do NOT try Codex (the order excludes it).
B) Gemini failed → fall back to Codex next (the default chain is Codex → Gemini → same-family), then wait
   out Codex's ~6-min timeout before same-family.
C) The Gemini message contains no `FINDING` lines → read it as `NONE` (clean pass) and let the gate open.

## Correct option
**A.** The `.kimiflow/cross-family` order token is the exact try-list and overrides the default order:
`auto gemini` = Gemini then same-family, Codex deliberately excluded. A quota/limit message is not valid
`FINDING`/`NONE`, so the grammar-validity backstop makes it a failure trigger → sticky same-family
substitution on the same seat, `cross_family: fallback (limit)`. Codex is never attempted (so its ~6-min
timeout is never incurred), and the limit notice is never mis-read as a `NONE`.

## Rationalization table
| Excuse | Reality |
|--------|---------|
| "The default chain is Codex → Gemini → same-family, so try Codex next." | An explicit order token IS the chain. `auto gemini` lists Gemini only; nothing is auto-appended, so Codex is excluded — precisely to avoid its ~6-min timeout here. Fall straight to same-family. |
| "No `FINDING` lines → it's a `NONE`/clean pass." | A quota/limit notice returned zero findings because the seat never reviewed. The grammar-validity backstop counts any non-`FINDING`/`NONE` output as failure, never a result. |
| "The order token names a CLI, so it can force Codex even though the file says gemini." | The token is a *preference over available CLIs*; it can never select an un-listed/unavailable CLI, and here it lists only `gemini`. It cannot force Codex. |
| "Just drop the cross-family lens — Gemini is rate-limited." | Fallback is a substitution on the same seat (same-family), not a dropped review dimension. The change still gets reviewed. |

## Pass criteria
Picks **A** AND: reads `.kimiflow/cross-family` = `auto gemini` as the exact try-list (Gemini → same-family,
**Codex excluded**), recognizes the quota/limit message as a failure trigger via the grammar-validity
backstop (not a `NONE`), performs a sticky same-family substitution (`cross_family: fallback (limit)`),
does **not** attempt Codex, and names its `reference.md` location ("Model routing (per-role)",
Cross-family transport / Opt-out & order). LLM-judged, out-of-CI.
