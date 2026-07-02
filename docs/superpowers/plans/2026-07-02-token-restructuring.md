# Plan: Token restructuring (audit batch B4) — rev 3

**Date:** 2026-07-02 · **Basis:** baseline token audit (handoff T1–T13) + two fresh code inventories + **plan-audit rounds 1–2** (round 1: 2 auditors, 1 BLOCKER + 4 HIGH fixed in rev 2, all fixes delta-verified in round 2; round 2: 2 auditors, 2 HIGH + 5 MEDIUM fixed in this rev 3; cross-family codex seat unavailable — hung twice with zero output — same-family substitutes, per the flow's sticky-fallback doctrine). User approved the structure 2026-07-02.

## Goal (priority order)

1. **Zero rule loss** (dominates everything below).
2. `reference.md` becomes single-copy authority for detail prose; SKILL.md stays the compact always-loaded flow spec.
3. `SKILL.md` from **59,685 chars / 60,463 bytes** toward **≤ ~30K chars** — honest expectation: itemized moves land at ~45K; the sentence-level second pass targets ~33–37K; ≤30K only if reachable without touching any preserved rule. The real floor is reported to the user, never bought by cutting rules.
4. Launcher first screen compact (`--full` for the heavy arrays); reviewer spawn contract deterministic and self-contained.

## Non-goals

- No behavior change to any gate/hook except the launcher **output shape** (`--full`); `.launcher`/maintenance computation stays byte-identical (they read `$snapshot.memory` internally — see WS4).
- No change to the reference.md alias table (smoke-install.sh:91–94 / smoke-install-codex.sh:96–99 grep the no-code semantics on exactly those lines).
- No rewrite of the Codex port `skills/kimiflow/SKILL.md` (verified: references only paths + section names B4 keeps).
- Moved text moves without semantic change; compressed text keeps every clause listed in the invariants artifact.

## Preservation invariant (hard)

Artifact: `docs/superpowers/plans/2026-07-02-token-restructuring-invariants.md` (rev 3 — extended per audit rounds 1–2 with: frontmatter opt-in rule (line 3), lines 18/47/50/70/93/122/123/132, lens A/B unique clauses (145–146), audit-lens fail-closed rules (147, full clauses stay in SKILL — no compression exception), **quick review-light ensemble (23+181)**, the three line-190 prohibitions, the staleness-nudge/background hooks, and a **smoke phrase contract** section). **Every row keeps an always-loaded mention in SKILL.md after the rewrite.**

Mechanical check (round-2 fix — the artifact rows are paraphrases, a literal grep of them would fail even against today's file): **before Commit C**, a check script `hooks/… (local, uncommitted or docs/)` is built by extracting one verbatim mid-string substring per row from the CURRENT SKILL.md (e.g. `NEVER costs rigor — keep every required field`), plus the reference.md-side needles (rubric clauses for rows 145–146, smoke alias/CANDIDATE lines). The script must pass against the **unmodified** files first (sanity), then gates every B4 commit that touches SKILL.md/reference.md, in addition to both smoke installs. Scope: SKILL.md **and** reference.md, per row.

Hard constraints (verified against code in round 1):

- Every hook command string in SKILL.md stays with its arguments — full list in the artifact, now including `background-run.sh` (line 57), `map-staleness-nudge.sh` (191), `improvements-staleness-nudge.sh` (192).
- `smoke-install.sh` / `smoke-install-codex.sh` grep contracts (SKILL.md phrases *and* reference.md alias/CANDIDATE lines) are enumerated in the artifact and re-verified by running both smokes per commit.
- The clarify-evidence marker string stays verbatim in SKILL.md (writers need it without loading reference.md).

## Workstream 1 — SKILL.md compaction

Line refs = current file. Char counts corrected per audit (1.2 includes line 16; 1.7/1.8 merged — they shared a range).

| # | Section / lines | Chars | Action | Est. saved |
|---|---|---|---|---|
| 1.1 | Frontmatter description (line 3) | 1,180 | Rewrite to ≤ ~450 chars. **Keeps verbatim-equivalent: the OPT-IN sentence ("invoke ONLY when the user explicitly asks … Do NOT auto-trigger on ordinary feature/bug/refactor requests")** + trigger examples + mode names. | ~700 |
| 1.2 | Launcher line + Modes (16–35) | 7,196 | Keep the alias table and every per-alias STOP/verbot line (artifact rows 16–35), **incl. the full quick review-light definition on line 23 ("ONE code-review lens, `bug-regression`, … plus the advisory scans" — artifact row 23/181)**. Line 16: keep `launcher-status.sh` call + `primary_action` + never-auto-pick; drop the status-group enumeration (pointer to reference "Launcher mode"). Move rare-path descriptive prose (audit/explore/verify-feature/resume mechanics beyond gate rules) to their existing reference sections; each alias keeps ≤2 lines (row 28's resume clauses may take a third line — rules beat the line budget). | ~2,700 |
| 1.3 | Core principles (38–60) | 7,753 | Keep all artifact rows **incl. 47 (density never costs rigor), 50 (no speculative abstractions), 70 (subagent task-lists separate — sits in Phase 0 but same class)**. Verbosity invariant: canonical one-liner stays at 40; lines 34/76/197 shrink to short clause + pointer. Trim only explanatory clauses. | ~700 |
| 1.4 | Phase 0 (62–80) | ~8,900 (excl. line 16) | Keep every gate command (64, 65, 69, 71, 77–80) + STOP rules. Line 64 keeps command + headless-STOP, drops the second status-group enumeration; line 67 drops the alias-semantics restatement (points at Modes table); line 70 keeps the subagent-task-list rule. | ~2,400 |
| 1.5 | Explore (84–89) | 1,086 | Keep rows 86/89; trim only if free. | ~100 |
| 1.6 | Phase 1 (93–98) | 1,877 | Keep gates 95–98 + marker verbatim + **line 93 "loose prior conversation … never counts as confirmation"**. Trim remaining intro prose. | ~150 |
| 1.7 | Phase 2 + fix/audit branch (102–126) | 5,962 | Keep command lines 104–108, mini-gate 113, fix-mode rows 116/117/119, **audit-branch rows 122 (existence-first) / 123 (tag format + pre-delete grep → 0 + git-history freshness)**, 124, 126. Compress narrative to pointers (recall/vault/current-state/audit sections exist in reference). | ~1,700 |
| 1.8 | Phase 3 (130–137) | 2,172 | Keep delegation spec + **line 132 "never structural merges"** + 135. Minor trims. | ~200 |
| 1.9 | Phase 4 (141–154) | 4,952 | Keep loop mechanics, gate commands (143/150), stop rules (151–153), pre-build gate (154), **audit-lens 147 in full — its fail-closed clauses ("a cut survives only if no reviewer finds one; any live caller → downgrade/move to do-NOT-touch"; "tests green before+after") stay always-loaded, no compression exception (round-2 fix: the rubric has no audit lens and WS2.3 does not add one)**. Lens A/B compression only AFTER WS3 canonicalizes their full clauses into the rubric (see WS3, sequencing inside commit C). | ~700 |
| 1.10 | Phase 5 (158–162) | 1,613 | Keep 160–162; trim. | ~150 |
| 1.11 | Phase 6 (164–176) | 1,896 | Keep: gate command 170 + lsp 171 + "Regression … green" + "Any failure → phase 5" + verifier-discrepancy 175. Compress Exists/Substantive/Wired + smoke prose to one line + pointer to reference "Verification". | ~800 |
| 1.12 | Phase 7 (180–193) | 10,384 | Keep every command/stop (180, 185–193). Line 190 (4,021): keep the operative core **plus its three prohibitions ("neither `.kimiflow/` nor repo files store the key", "never patches skills or writes external notes blindly", "review-only drafts under `SKILL-DRAFTS/`") — kept core ~1.4K** + pointer; drop only prose verifiably restated in reference "Memory Router & Learning Loop" (1:1 check against reference 1057–1292 during implementation; anything not restated moves there first). Lens R1–R3 compression only after WS3 canonicalization (already near-canonical at reference 1436–1440); **the quick/small/large ensemble-sizing clause on line 181 (quick = review light, ONE `bug-regression` lens; third-lens trigger list) stays always-loaded (artifact row 23/181 — the rubric does not carry the quick profile)**. CANDIDATE format line (160 chars, grammar core ~60) stays verbatim. | ~3,000 |
| 1.13 | Scaling knobs (197–207) | 3,143 | Keep defaults, budget-stop, best-of-2 committed-oracle rule (200), evals-not-CI (207). Move large-knob mechanics detail to reference "Model routing"/"Hard test-gate". | ~1,100 |

Itemized: ~14.4K → pass-1 landing ≈ 45K. **Second pass:** sentence-level compression (shorter phrasing only, no clause deletion; every artifact key phrase re-checked after) → realistic 33–37K; then report the floor vs. the ≤30K goal to the user. Smoke-phrase contract strings are never reworded.

## Workstream 2 — reference.md single-copy authority

| # | Action |
|---|---|
| 2.1 | "Commit hygiene": keep operative 1521–1530 (red-test exception + 6 numbered rules) + the secret-content-scan Phase-7 sentence + the two operative clauses of 1532 ("skill-only use loads no hook"; auto-activation via `.kimiflow/`). Move the rest of 1532 and the maintainer part of 1534 to new `docs/commit-secret-gate.md`. 1536 LSP third copy → 1-line pointer to "Verification". Verified in round 1: no hook/test/smoke greps the moved content; `lsp-diagnostics.sh` grep (smoke:162) stays satisfied via reference:1483; "Commit hygiene" heading stays. **Round-2 addendum: the six code comments pointing at reference "Commit hygiene" for the moved maintainer prose (commit-secret-gate.sh:8/18/57/173, test-commit-secret-gate.sh:156/243) are updated to point at `docs/commit-secret-gate.md` (comment-only edits, same commit).** |
| 2.2 | Alias table 67–82: **unchanged** (round-1 finding: smoke greps match the semantics lines). SKILL.md remains canonical for mode STOP rules; a sync note is added as an HTML comment above the reference table. |
| 2.3 | Rubric canonicalization (from WS3): reference:1428 lens A/B enriched with the clauses currently only in SKILL 145–146 ("fixes the verified root cause", "non-contradictory", "no invented assumptions", "Fix mode: cause, not symptom"). Additive — no existing rubric text removed. |
| 2.4 | Canonical strings stay duplicated where templates need them (marker :599/:651; CANDIDATE :537–539/:1440 — also keeps smoke CANDIDATE greps green). |

## Workstream 3 — reviewer spawn contract (revised per audit)

Round-1 correction: nothing inlines the rubric today, and SKILL:148 already inlines the FINDING one-liner; SKILL:144 already limits reviewer inputs. The remaining real gaps: the spawn contract omits the **file-form constraints** the fail-closed resolver enforces, lens definitions live only in SKILL (blocking their compression), and nothing tells reviewers *not* to read reference.md.

- Canonicalize lens definitions in the rubric (WS 2.3); SKILL lens lines A/B then compress to 1-line summaries (artifact clauses preserved via the rubric text — the artifact check for **145–146 only** accepts the always-loaded 1-line summary + requires the full clauses in the rubric; **147 and the quick-ensemble line 181 stay in full in SKILL**, see 1.9/1.12).
- SKILL Phase-4 and Phase-7 spawn specs: each spawn prompt inlines (verbatim, <15 lines — allowed by SKILL:60): the reviewer's lens definition + the FINDING/CANDIDATE grammar core + **the file-form constraints from reference:1429 ("one canonical line per finding, at column 0, no newline in the reason; nothing but FINDING lines, or the single sentinel line `NONE`")** + the findings-file path. Add: "reviewers do not read reference.md."
- Inline source (round-2 fix): Phase 4 has the orchestrator rubric-read at SKILL:141; **Phase 7 today has none (lens definitions are inline at 181–184) — the compressed Phase 7 gets the same one-line orchestrator step ("Read → reference.md 'Review rubric'") before spawning lenses**, so the inlined lens text has a canonical source after compression.
- Parser compatibility: grammar text matches `resolve-review-gate.sh:57` (`^FINDING (BLOCKER|HIGH|MEDIUM|LOW) .+ :: .+$`), the 1-line `NONE` check (:66–68), and the `r<N>-<lens>.md` filename contract — byte-checked during implementation.
- Savings honesty: per-spawn token savings depend on whether reviewers currently follow the SKILL:148 rubric pointer (plausible, not proven — NOT VERIFIABLE per both auditors). The verified benefits: deterministic spawn contract (fewer malformed retries), lens compression in SKILL, no reviewer reads of an 11.4K section *by instruction*.

## Workstream 4 — launcher first screen (revised per audit)

Behavior:

- Full pipeline computes today's complete snapshot **unchanged** (the `.launcher` block, visible/hidden maintenance reasons, vault status and the "memory" drilldown all read `$snapshot.memory` after the snapshot closes at line 774 — launcher-status.sh:694/775–776/818–823). The trim happens **only at serialization**: default output applies `del(.runs.items, .background.items, .memory)`; `--full` outputs everything (today's shape). `memory_summary`, all counts, `runs.learning_reviews`, `maintenance`, `findings`, `improvements`, `feature_checks`, `active_session`, `agentic_readiness`, `.launcher`: unchanged in default. `--pretty` composable. Runtime cost unchanged.

Steps (test-first):

1. RED — extend `hooks/test-launcher-status.sh`: (a) default output: `.runs.items`, `.background.items`, `.memory` absent; `memory_summary` + counts + `.launcher` unchanged; (b) `--full`: today's assertions hold. Rewire to `--full`: the 6 item tests (310/314/359–361/397) **and the 9 `.memory.*` default assertions found in round 1 (≈ lines 258–294: memory_status_reports…, launcher_surfaces_history…, launcher_surfaces_memory_usefulness… [memory half], launcher_surfaces_global_efficiency [memory half], memory_index_clears…, launcher_hides_benign…, pending/approved_learning_proposals…, memory_over_budget…)**. Assertions in those tests that read `maintenance`/`memory_summary` keep passing under `--full` (superset). (c) `--pretty` test unchanged.
2. GREEN — implement: arg parse (312–322) + serialization-stage `del(...)` after the `.launcher` merge; nothing before line 774 changes.
3. Prose: SKILL.md:16/64 unchanged call (default IS the first screen). reference.md drilldowns that consume item/memory fields (118–119 backlog runs, 124–127 background, 146–149 provider/memory detail, 302–306 background items) get "re-run `launcher-status.sh --full`"; **the "Mechanical snapshot" contract paragraph (reference.md:17–19) is updated to describe the compact default + `--full`** (round-2 fix — it currently lists the item arrays as part of the launcher JSON). `skills/kimiflow/SKILL.md`: no change.
4. Guards: `hooks/test-improvements-status.sh` (`.improvements.open`, top-level, stays in default) runs in the batch loop.

## Execution order & commits

1. Commit A — launcher `--full` (WS4, TDD; launcher + improvements suites green; each commit individually green).
2. Commit B — `docs/commit-secret-gate.md` extraction + commit-hygiene slim (WS2.1); both smokes green.
3. Commit C — rubric canonicalization (WS2.3) **first**, then SKILL.md compaction pass 1 + spawn-contract change (WS1 + WS3) — single commit so lens definitions are never without a canonical home; invariant grep-check + both smokes green.
4. Commit D — second pass (sentence-level) + floor report; invariant grep-check + smokes green.
5. Each commit: named paths only, full local suite loop (all `hooks/test-*.sh` except the two production hooks) + `release-consistency-check.sh` + both smoke installs; CHANGELOG Unreleased entry per commit.

## Verification (whole batch)

- Invariant grep-check: 100% of artifact key phrases present in new SKILL.md (incl. smoke-phrase section).
- Line-190 drop-check: every dropped clause verified restated in reference "Memory Router & Learning Loop" (or moved there) before deletion.
- `wc -m SKILL.md` measured and reported; suites + smokes + release-consistency green.
- Fresh consistency re-audit (B5) runs after B4 lands.

## Risks

- **Rule loss during compression** — invariant grep-check (now covering the round-1 gaps) + plan audit + B5 re-audit.
- **Smoke-grep breakage** — phrase contracts enumerated in the artifact; smokes run per commit (was a round-1 BLOCKER-class gap for the alias table; alias table now untouched).
- **Launcher consumers** — round-1 BLOCKER fixed: all 15 structure-dependent tests rewired; serialization-stage `del()` keeps `.launcher`/maintenance byte-identical.
- **Reviewer output drift** — grammar + file-form constraints inlined byte-exact against the resolver regex.
- **Codex-port drift** — no B4-moved targets referenced (verified); codex smoke runs per commit.
