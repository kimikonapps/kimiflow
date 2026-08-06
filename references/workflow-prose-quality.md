# Workflow prose quality

Use this contract only for new decision-bearing Kimiflow artifacts, findings, learnings, and visible reports. Apply it in the same model pass that writes or reviews the text: no extra agent, model call, workflow step, or prose gate.

## Preserve before editing

- Keep the confirmed meaning, the user's language and voice, stated uncertainty, concrete facts, and stable technical terms.
- Do not invent claims, numbers, sources, evidence, opinions, or requirements. Leave a strong concrete sentence unchanged.
- Make the minimum effective edit. Prefer one precise mechanism, locator, or check over generic confidence.

## Semantic defects

Unsupported or invented claims, unverifiable success criteria, terminology drift, and generic review findings are correctness defects. Correct them from current evidence; if that is impossible, keep the owning existing gate closed. Prose quality never replaces evidence or a mechanical gate.

## Style defects

Remove only filler, importance rhetoric, meta-commentary, synonym cycling, repetition, and decorative formatting that add no meaning. Correct them in place; they never block a run by themselves.

## Boundaries

Code, JSON, schemas, commands, logs, diffs, machine receipts, evidence quotes, and third-party text are not automatic rewrite targets. Do not infer or claim whether a human or AI authored text. Do not silently rewrite user input or quotations.

This bounded workflow adaptation is inspired by [petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop); Kimiflow retains only the meaning-preserving, minimum-edit idea and its own evidence/gate ownership.
