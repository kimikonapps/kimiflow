# Top-Model Orchestrator Routing

**Date:** 2026-07-09
**Status:** Approved for implementation

## Goal

Keep Kimiflow portable between Codex and Claude while reserving orchestration and planning for the strongest available model. Reduce token cost only on bounded leaf work, never on control-flow or final quality decisions.

## Routing Contract

Kimiflow uses semantic tiers in its canonical workflow and maps them to concrete models in each host overlay:

- `top`: strongest available model on the active host.
- `balanced`: capable value-tier model for normal implementation work.
- `cheap`: smallest suitable model for low-risk, mechanically bounded support work.
- `cross_family_top`: strong model from a different family for independent review or risky diagnosis.

The active host's `top` model always owns orchestration and planning. `balanced` and `cheap` models must never orchestrate, plan, perform risky diagnosis, independently verify a critical result, or issue the final semantic review verdict.

## Role Allocation

| Role | Required tier | Notes |
| --- | --- | --- |
| Orchestrator | `top` | Always the strongest available host model. |
| Planner | `top` | Includes architecture and acceptance design. |
| Phase-2 synthesis | `top` | Cheap workers may gather bounded facts, but the top model interprets them. |
| Implementer | `balanced` by default | Promote to `top` for risky, tightly coupled, or repeatedly failing work. |
| Gather/map/log helper | `cheap` | Only deterministic, bounded work with no quality verdict. |
| Reviewer | `top` or `cross_family_top` | Must not be weaker than the implementer for semantic review. |
| Independent verifier | `top` or `cross_family_top` | Mechanical commands and gates remain model-free. |
| Risky diagnosis | `top` or `cross_family_top` | Failed hypotheses remain candidates until verified. |

## Host Mapping

Current Codex mapping:

- `top` -> GPT-5.6 Sol
- `balanced` -> GPT-5.6 Terra
- `cheap` -> GPT-5.6 Luna
- `cross_family_top` -> explicitly pinned strong Claude model

Claude mapping stays capability-based so model generations can change without rewriting the canonical workflow:

- `top` -> strongest selected Claude tier
- `balanced` -> current value-tier Claude model
- `cheap` -> current smallest suitable Claude model
- `cross_family_top` -> GPT-5.6 Sol, with the existing Gemini fallback where applicable

Concrete host mappings are advisory and degrade to the session model when per-seat selection is unavailable. Degradation must never route orchestration or planning downward.

## Token Controls

- Use one sequential `balanced` implementer by default.
- Use `cheap` only when inputs, outputs, and verification are deterministic and narrowly bounded.
- Keep reviewer packets compact and fresh-context; do not pass the full orchestrator conversation.
- Do not use automatic nested-delegation effort modes inside Kimiflow; Kimiflow remains the sole orchestrator.
- Escalate a seat instead of adding duplicate seats unless independence is the reason for the extra call.

## Scope

Implementation updates only the canonical routing contract, Codex host overlay, rendered copies required by release consistency, and focused documentation/tests that assert the role mapping. It does not add a routing service, provider registry, user-facing configuration, or pricing logic.

## Acceptance

1. Canonical rules state that orchestration and planning always use `top`.
2. Codex rules explicitly map Sol/Terra/Luna to `top`/`balanced`/`cheap`.
3. Cheap models are barred from planning, review verdicts, critical verification, and risky diagnosis.
4. Claude/Codex switching preserves semantic roles and changes only host mappings.
5. Existing cross-family fallback and fail-closed gates remain unchanged.
6. Release-consistency and relevant documentation checks pass.

## Considered Alternatives

- Hard-code Sol, Terra, and Luna throughout the canonical workflow: rejected because it would couple Kimiflow to one host and model generation.
- Let the selected session model orchestrate regardless of tier: rejected because a cheap model could control a large or risky run.
- Use the top model for every seat: rejected because bounded implementation and support work can use lower tiers without weakening the control plane.
