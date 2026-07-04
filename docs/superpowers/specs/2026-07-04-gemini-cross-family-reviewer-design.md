# Gemini (via `agy`) as a second cross-family reviewer — Design

**Date:** 2026-07-04
**Status:** Design approved, pending spec review → plan
**Scope:** Extend Kimiflow's "Model routing (per-role)" cross-family reviewer seat to add Gemini (through the Antigravity `agy` CLI) as a second cross-family option, ordered after Codex and before same-family fallback.

---

## 1. Motivation

Kimiflow runs each quality gate with **one cross-family reviewer seat** — a CLI from a family *different* from the session model (Claude Opus 4.8), to de-bias review (the session model both writes and reviews). On a Claude Code host today that seat is **Codex (GPT-5.5)** only. On failure it collapses straight to a **same-family Claude** substitute — losing all cross-family diversity.

Two concrete problems this solves:

1. **In this repo, Codex reliably times out** (~360 s, PreToolUse-hook interaction). The cross-family seat therefore *always* falls back to same-family Claude here — no independent-family review actually happens.
2. There is no second independent family to fall to before same-family. Adding Gemini gives a **still-cross-family** middle tier.

## 2. Verified facts (measured 2026-07-04, this machine)

- **Codex CLI** 0.142.5 → model `gpt-5.5`, `reasoning_effort=xhigh`, 800k ctx.
- **Antigravity CLI** `agy` 1.0.16 (`/Users/sr/.local/bin/agy`) — a **multi-model gateway**. `agy models` offers: Gemini 3.5 Flash (Low/Med/High), Gemini 3.1 Pro (Low/High), Claude Sonnet/Opus 4.6 (Thinking), GPT-OSS 120B. (The prior standalone `gemini` CLI 0.38.2 is **dead** on this account — free-tier `oauth-personal` is `IneligibleTier`; Antigravity migration is the working path.)
- **Transport verified working headless:** `agy -p "<prompt>" --model "Gemini 3.5 Flash (High)"` prints **only** the model's final answer to stdout (exit 0, no event-stream noise, no permission-prompt hang). Cleaner than Codex, which needs `--output-last-message` to separate the answer from its activity stream.
- **Model choice — Gemini 3.5 Flash (High)** (released 2026-05-19, Google I/O): wins 11/15 published benchmarks vs 3.1 Pro and is ~4× faster; SWE-bench Pro 55.1 vs 54.2, Terminal-Bench 2.1 76.2 vs 70.3, MCP Atlas 83.6 vs 78.2. 3.1 Pro only leads on hardest abstract reasoning (ARC-AGI-2, GPQA) and 128k+ long-context. For agentic code-review seats, 3.5 Flash High is the better default; the 4× speed also reduces the risk of hitting `agy`'s 5-min `--print-timeout`.
- **Render pipeline:** canonical `docs/render/kimiflow/canonical/SKILL.md` → `render.py` → root `SKILL.md` + (codex overlay) `skills/kimiflow/SKILL.md`. **Phases are not rendered.**
- **No executable code parses `.kimiflow/cross-family`** — cross-family routing is entirely spec-driven (orchestrator instruction). This is a **docs/spec-only change**; no code, no code tests.

## 3. Design

### 3.1 One seat, three-tier chain
Per gate there remains exactly **one** cross-family seat. Attempt order on a Claude Code host:

```
Codex (GPT-5.5)  →  Gemini via agy (Gemini 3.5 Flash High)  →  same-family Claude
```

Each step is a **substitution on failure** (nonzero exit, timeout, auth/interactive prompt, empty output, or refusal-shaped final message) — never an added spawn, never a block. The move is **sticky**: once the seat lands on a tier, it stays there for the rest of the run (limits reviewer-identity flapping across gate rounds). This generalizes the existing 2-tier rule (Codex → same-family) to 3 tiers.

### 3.2 New Gemini transport (Claude Code host)
```
agy -p "<prompt>" --model "Gemini 3.5 Flash (High)"
```
- stdout **is** the reviewer's final message → persisted **byte-for-byte verbatim** as that lens's findings file (same immutability rule as the Codex external-reviewer path).
- **Read-only seats only:** review lenses, plan-review, escalation diagnosis, independent verifier. The model name **must** be pinned explicitly — `agy` is a multi-model gateway and its default could resolve to Flash-Low or even Claude, destroying cross-family diversity.
- The **best-of-2 implementer** seat (workspace-write) stays **Codex-only** — Codex is the agentic-coding platform, and there is no `agy` write path in scope. Its existing rule is unchanged (failure degrades to best-of-1, never substitutes same-family).

### 3.3 Per-repo ordering (`.kimiflow/cross-family`)
The opt-out file gains an **optional second token** specifying preference order over the cross-family CLIs:

| File content | Meaning |
|---|---|
| `auto` (or absent/unreadable) | cross-family on, **default order `codex,gemini`** |
| `auto gemini,codex` | cross-family on, **Gemini first** (this repo — skips Codex's 6-min timeout) |
| `off` | cross-family disabled entirely (unchanged) |

The order is a **preference over the CLIs detected available** — it can never select an un-installed CLI, so the "the file can never contradict availability detection" invariant is preserved. Still read directly (no resolver hook), still project-local.

### 3.4 Verifier seat prefers the Gemini tier (large only, advisory)
The Phase-6 independent verifier (read-only, `large` runs) **prefers the Gemini seat** when available (wide-context goal-backward sweep), else follows the normal chain. Advisory one-liner, consistent with existing advisory-routing framing. Same pinned model (3.5 Flash High) — no per-seat model split.

### 3.5 Security lens & refusal detection — wording only
- The `failure-security` "route off Fable when another family is available" rule generalizes to "**another non-Fable family**" — both Codex and Gemini qualify; the chain serves it automatically. No mechanism change.
- Refusal-shaped-message detection and sticky fallback apply to the Gemini step identically (a Gemini refusal → next tier).

## 4. Files affected (spec/docs only)

1. **`reference.md`** — "Model routing (per-role)": add Gemini to the cross-family seat list; "Cross-family transport (pinned)": add the `agy` invocation + read-only/model-pin rules + 3-tier sticky chain; "Opt-out": document the order token; security-lens wording generalization.
2. **`docs/render/kimiflow/canonical/SKILL.md`** — canonical condensed version of the same, then run `render.py`.
3. **Generated (do not hand-edit):** `SKILL.md`, `skills/kimiflow/SKILL.md` — regenerated by `render.py`; verify no unrelated drift.
4. **`docs/kimiflow-scaling-knobs.md`** — document the Gemini seat + order token.
5. **`evals/scenarios/`** — extend `11-refusal-as-fallback.md` or add a scenario asserting the 3-tier chain / Gemini-first ordering behavior.
6. **`CHANGELOG.md`** — Unreleased entry.

## 5. Out of scope (YAGNI)
- No `agy` write/implementer path (implementer stays Codex-only).
- No reviewer rotation, no round-robin.
- No use of `agy`'s Claude or GPT-OSS models (same-family / weaker).
- No per-seat model split (uniform 3.5 Flash High).
- No new config mechanism beyond the single order token.
- No change to the fail-closed findings resolver, gate math, or agent budget.

## 6. Acceptance criteria
- **AC-1** `reference.md` documents Gemini as the second cross-family tier with the exact `agy -p … --model "Gemini 3.5 Flash (High)"` transport, read-only scope, and mandatory model pin.
- **AC-2** The 3-tier sticky fallback chain (Codex → Gemini → same-family) is specified, including refusal-shaped-message handling for the Gemini step.
- **AC-3** `.kimiflow/cross-family` grammar documents the optional order token with the availability-preserving semantics; `off` and absent-file behavior unchanged.
- **AC-4** Canonical SKILL.md updated and `render.py` reproduces root + codex SKILL.md with no unrelated drift.
- **AC-5** An eval scenario asserts the ordering/fallback behavior.
- **AC-6** Security-lens wording generalizes to "another non-Fable family"; no mechanism/gate-math change.

## 7. Open items for the plan phase
- Confirm exact insertion points / current line ranges in `reference.md` (the "Model routing (per-role)" and "Cross-family transport (pinned)" blocks) and the canonical SKILL.md counterpart.
- Decide eval: extend `11-refusal-as-fallback.md` vs. new `12-cross-family-order.md`.
- Confirm `render.py` drift-check command used elsewhere in the repo (render no-drift gate).
- Whether `COMPATIBILITY.md` / `README.md` need a mention (likely a one-line note each).
