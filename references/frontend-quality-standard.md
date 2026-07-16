# Frontend Quality — Standard

Read this file only for `Frontend quality: standard|flagship`. The top model owns aesthetic judgment; these are compact constraints, not a style recipe.

## Source truth first

- Inspect existing design tokens, shared components, comparable screens, product copy and interaction conventions before designing.
- Use a supplied mock, screenshot or visual reference as source truth for the named viewport/state. Otherwise use the project system plus the confirmed UI brief.
- Reuse the project vocabulary when it is coherent. Change it only where the feature needs a clear, evidenced improvement.
- For task-oriented product UI, favor earned familiarity: reuse standard affordances and the established component vocabulary; add novelty only when it materially improves the task.
- Compare flow shape, progressive disclosure, hierarchy and terminology with neighboring features so visual polish does not hide product inconsistency.
- Before repairing design drift, classify its root as a missing token, a bypassed shared component or a conceptual flow/hierarchy mismatch; fix that layer instead of patching the symptom.

## Geometric integrity

- Build a legible hierarchy through scale, weight, contrast and grouping.
- Check alignment, spacing rhythm, visual mass and optical balance. Symmetry is useful only when it supports the content; do not force mirror layouts.
- Preserve intentional relationships across responsive sizes instead of merely shrinking the desktop composition.
- Design loading, empty, error, disabled, focus, hover and active states that the feature actually needs.
- Keep controls understandable, keyboard/focus behavior visible and contrast robust.
- Use motion only to explain change, continuity or causality; respect reduced motion and avoid decorative delay.

## Model freedom

No palette, font pair, grid, radius, shadow, animation library or layout family is prescribed. Choose the smallest project-fit implementation that produces a coherent, polished result. Avoid generic dashboard/Bento defaults unless the product and content genuinely call for them.

## Completion

Implement the feature, run deterministic checks, then verify the real rendered state at a declared viewport. Fix concrete P0/P1/P2 findings before passing to Phase 6.
