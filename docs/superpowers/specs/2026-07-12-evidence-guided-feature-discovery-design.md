# Evidence-Guided Feature Discovery

## Goal

Improve feature decisions with current, project-specific evidence without turning every run into broad research, forcing technical questions onto the user, or expanding requested product scope.

## Architecture

Keep the eight-phase state machine. Phase 1 records complete product intent without requiring a fixed question count. Phase 2 performs an adaptive discovery assessment inside the existing understanding and research phase. Current State continues to answer whether technical information is fresh; Discovery separately answers whether the solution space is understood well enough to plan.

The top model owns the assessment, research brief, source evaluation, synthesis, decision triage, specification, plan, and Build Preview. Cheap or balanced workers may collect bounded evidence from a brief, but do not choose architecture or product scope. No worker is spawned for stable project-local work. Focused research defaults to one worker and expands to at most two independent lanes when the expected decision value justifies the coordination cost.

## Artifacts

Reuse `INTENT.md`, `RESEARCH.md`, `ACCEPTANCE.md`, and `PLAN.md`.

`INTENT.md` carries machine-readable evidence that behavior, scope, and the user-visible outcome were confirmed. It no longer records a mandatory number of questions.

`RESEARCH.md` remains the single discovery artifact and adds a compact machine-readable marker plus sections for assessment, research brief, evidence, recommendation, decision triage, and stop status. Do not add `DISCOVERY.json`, `EVIDENCE.jsonl`, or `SPEC.md`.

## Gates

Add a small `discovery-gate.sh` resolver. It validates only observable structure: a valid discovery depth/status, completed required lanes, source references for plan-changing external claims, no unresolved technical research gap, no unconfirmed product decision, and confirmed scope changes. It does not claim that research was exhaustive or that the chosen architecture is objectively best.

`plan-blocker-gate.sh` calls Clarify and Discovery before checking plan executability. Current State remains an independent freshness gate.

## User Interaction

The user confirms product intent, not technical implementation details. When the initial request already covers behavior, scope, and success, Phase 1 presents those inferred facts for one compact confirmation instead of inventing questions.

After the internal plan gate, Kimiflow shows a plain-language Build Preview derived from intent, discovery decisions, and acceptance criteria. Normal low-risk runs continue automatically. Explicit confirmation is required for scope expansion, unresolved product choices, breaking changes, risky migrations, public API or durable data contracts, new paid/privacy-sensitive services, hard-to-reverse architecture, or material drift from confirmed intent.

The build policy becomes `risk|always|off`: absent configuration defaults to `risk`; legacy `on` maps to `always`; `off` remains informational auto-continue. `full` keeps an explicit Build Preview confirmation, while `plan`/`--prepare` always parks for resume.

## Compatibility

Legacy runs without discovery evidence remain resumable. Discovery is required only when a new or revalidated feature run passes Phase 2. Fix mode keeps diagnosis and current-fix research rather than product discovery. Quick uses at most a pulse unless a volatile or risky boundary requires focused research. Review and audit may inspect current standards but never propose unrequested product features.

Claude Code and Codex use the same canonical phase files, resolvers, artifacts, and thresholds. Host wrappers only map tool and model names.

## Verification

Cover the Clarify marker, Discovery resolver, Plan Blocker integration, build-policy migration, resume behavior, host smokes, and behavioral scenarios for local UI work, volatile APIs, AI/search, auth, privacy, migration, SaaS, established conventions, conflicting sources, and research with no material result.

Success means no forced technical questions, no default research worker for stable local work, one focused worker by default, source-backed plan-changing claims, no optional findings entering the plan, risk decisions surfaced, and a one-screen Build Preview.
