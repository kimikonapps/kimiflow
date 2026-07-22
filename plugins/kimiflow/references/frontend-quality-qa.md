# Frontend Quality — Visual QA

Read only in Phase 6 for `Frontend quality: standard|flagship`.

## Verify

1. Render the real implementation at one meaningful viewport and named state. Use the same viewport/state as the visual reference when one exists.
2. Compare hierarchy, alignment, rhythm, optical balance, responsive relationships, required states, accessibility cues, purposeful motion, flow shape and terminology against the Source truth. When drift exists, verify the repair addresses its token, shared-component or conceptual cause rather than only the visible symptom.
3. Record concrete findings as P0 (unusable/data-loss), P1 (major goal failure), P2 (clear visible/interaction defect), or P3 (reversible polish). Standard must close P0–P2; Flagship must also close concrete P3.
4. Run deterministic checks. Capture a current, run-local canonical RGBA PNG only after code and routing receipt are current.

## Exact DESIGN-QA.md contract

Write exactly these 13 non-empty lines, once each and with no headings or extra fields:

```text
Lane: standard|flagship
Source truth: project-system:<locator>|visual-reference:<locator>
Implementation evidence: screenshot:evidence/<safe-name>.png
Viewport: <width>x<height>
State: <meaningful rendered state>
Strategy: <meaningful implementation/repair strategy>
Deterministic checks: passed
Comparison history: initial-capture|fix-capture-compare
Open P0: 0
Open P1: 0
Open P2: 0
Open P3: <nonnegative integer; 0 for flagship>
Final result: passed
```

The gate validates artifact structure, freshness and closure; the top model judges aesthetics.

## Autonomous recovery

Run `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/frontend-quality-gate.sh <run> --write` (Codex: `$KIMIFLOW_PLUGIN_ROOT/hooks/frontend-quality-gate.sh`). For `Kind: contract`, repair the reported routing/evidence contract and rerun without inventing screenshots for `off`. For `Kind: visual`, keep Lane, Source truth, Viewport and State fixed, choose a substantively different strategy, implement it, capture changed pixels, compare again and rerun. Never stop only to ask whether another technical iteration should happen.
