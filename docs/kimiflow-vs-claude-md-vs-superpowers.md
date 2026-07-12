# KimiFlow vs CLAUDE.md vs Superpowers: where mechanical gates matter

Three popular ways to make Claude Code more disciplined are a **`CLAUDE.md`**, the **Superpowers**
skills framework, and a gated loop like **KimiFlow**. They get framed as competitors. They aren't —
they sit at different layers, and the only interesting question is narrow:

> **Where does a *mechanism* beat a *prompt*?**

This article answers that honestly, including where mechanical gates are the *wrong* tool.

## The three approaches, in one line each

- **`CLAUDE.md`** — prose conventions the model reads. It *asks*.
- **Superpowers** — a skills framework that enforces *process* (brainstorming-first, TDD,
  systematic-debugging, subagent-driven-development, verification-before-completion) through mandatory
  skill invocation and subagent patterns. It *disciplines how you work*.
- **KimiFlow** — a single user-invoked feature/fix loop whose few critical checkpoints are **tested,
  fail-closed scripts and hooks**, not instructions. It *enforces a handful of invariants*.

| | `CLAUDE.md` | Superpowers | KimiFlow |
|---|---|---|---|
| **What it is** | prose conventions file | skills / process framework | gated 8-phase loop |
| **Enforcement** | advisory — model reads it | instruction salience + subagents — model follows it | **mechanical** — scripts + hooks the model can't talk past |
| **Best for** | conventions, preferences, project facts | how-to-work discipline (TDD, debugging, planning) | invariants that must hold under pressure |
| **Fails when** | the model drifts or ignores it | the model rationalises past the instruction | applied to soft judgement; adds cost |
| **Cost / setup** | trivial, portable | install skills | install plugin + hooks; higher tokens on large runs |

## Why "asks" vs "enforces" is the whole game

A `CLAUDE.md` that says *"always run the tests before saying done"* is advice. Under pressure — a long
session, a plausible-looking diff, a model optimising to close the task — advice is the first thing to
slip. Not maliciously: the model *rationalises*. "Tests are probably fine." "This is clearly correct."
That rationalisation path is exactly what a prompt cannot close, because the same model that should
obey the rule is the one deciding whether the rule applies.

Superpowers raises the floor a lot here. Mandatory skill invocation ("if there's even a 1% chance a
skill applies, you must invoke it"), TDD-first, systematic-debugging, and verification-before-completion
are strong *process* guarantees — and the subagent patterns add independent perspectives that a single
context can't fake. But the guarantee is still **as strong as the model's adherence to an
instruction**. There is no external referee counting open blockers or intercepting a `git commit`.
That's not a flaw; it's a deliberate boundary — even mature skills frameworks keep
*model-behaviour* checks separate from the deterministic, CI-able layer.

KimiFlow's bet is that for a *small* set of checkpoints, you want the referee to be code:

- **Don't commit a secret.** A `PreToolUse` hook (`commit-secret-gate`) blocks staging secret-looking
  paths and bulk `git add -A`/`.` — before the commit runs. A reminder in prose can be forgotten; a
  hook cannot.
- **Don't proceed past an open `BLOCKER`/`HIGH`.** Reviewers write findings to files; a tested,
  fail-closed script (`resolve-review-gate.sh`) counts the open ones and returns a verdict (small
  feature/fix cap 2; large/audit cap 3; blocker-aware anti-oscillation; no clean round beyond cap).
  A verbose model can't argue past a number it didn't compute.
- **Don't finish on red tests.** An opt-in `Stop` hook (`test-gate`) blocks completion while the
  project's tests fail.
- **Don't commit without a human OK.** The commit-gate *stops* and shows the diff; "done" is never
  "committed" until you say so. Before implementation, a plain-language Build Preview stops only for
  material product/irreversibility risk by default; routine technical HOW continues after the preview.

The pattern is consistent: **when a violation is costly and the model is tempted to rationalise past
it, replace the judgement call with a mechanism.** That removes the rationalisation path entirely.

## The honest limits of mechanical gates

Mechanical gates are easy to oversell. They aren't here to.

- **A gate enforces a verdict; it doesn't generate one.** `resolve-review-gate.sh` counts the findings
  the reviewers *wrote*. It cannot prove the reviewers found everything. KimiFlow makes the gate
  *un-foolable*, not the reviewer *omniscient*.
- **They're narrow on purpose.** Four-ish invariants get mechanised. Everything else — scope
  classification, root-cause judgement, whether the design is good — stays model-judged, because
  mechanising soft judgement just produces brittle theatre.
- **They cost.** A `large` KimiFlow run fans out reviewers, an implementer, a verifier; that's real
  tokens. The scope-gate exists precisely so small work doesn't pay for machinery it doesn't need.
- **They're rigid.** One loop shape. If your task doesn't look like "build a feature" or "fix a bug,"
  the structure is overhead.

If you mechanise the wrong thing, you get a slow, rigid process that still can't tell good code from
bad. The skill is choosing *which* checks deserve a mechanism.

## They're layers, not rivals

The cleanest mental model is a stack, not a bracket:

1. **`CLAUDE.md`** carries your conventions and project facts. KimiFlow *reads* it as a hint — it just
   never relies on it for a gate.
2. **Superpowers** (or its patterns) carries process discipline. KimiFlow openly *borrows* from it —
   brainstorming before building, TDD, subagent-driven-development, verification-before-completion.
3. **Mechanical gates** carry the few invariants that must hold even when the model is confident and
   the session is long.

You can run all three. CLAUDE.md for *what we prefer*, Superpowers for *how we work*, mechanical gates
for *the lines we don't cross*.

## A decision rule

Before mechanising anything, ask:

> **Is this an invariant whose violation is costly, and which the model is tempted to rationalise past
> under pressure?**

- **Yes** → make it a mechanism (a hook, a fail-closed script, a hard stop). Secrets, open blockers,
  red tests, commit-without-OK.
- **No** → keep it prose or a skill. Conventions, style, exploration, design taste, anything needing
  flexibility.

Most of your rules are "no." That's why a `CLAUDE.md` and a good skills framework do most of the work,
and why mechanical gates should stay few — and uncompromising — for the rest.

---

*KimiFlow is a public, user-invoked Claude Code skill + plugin: <https://github.com/kimikonapps/kimiflow>.
The gates described here are real and tested — see [`hooks/`](../hooks/), the
[`examples/`](../examples/), and the "What gates are mechanical" section of the
[README](../README.md).*
