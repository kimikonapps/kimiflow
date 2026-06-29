# memory-router Python CLI - Plan 26: `review-run` subcommand

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Port `cmd_review_run` (Bash 3281-3415) - the run-completion learning gate. It scans a finished run's artifacts for four "reusable learning" candidates (one per question/kind), runs each through a quality gate, and on `--write` records them as learnings, refreshes bounded memory + curate + index + propose, records run economics, and writes `LEARNING-REVIEW.md` + `RUN-LIFECYCLE.json/.md`. `--skip <reason>` short-circuits to a skipped review (+ economics + lifecycle on `--write`). This is the LAST and LARGEST subcommand; it wires **13/13**. All heavy deps are already ported (Plan 25 shipped `economics.record_run_economics_json`; propose/curate/index/append_learning_row/write_bounded_memory/status_json/classify_text/resolve_run_dir from earlier plans).

**Architecture:** New module `hooks/memory_router/review.py` with the 9 helpers + `run(argv)`; registered as `"review-run"` in `__main__.COMMANDS`. Reuses: `runs.resolve_run_dir`, `economics.record_run_economics_json`, `propose.run`/`curate.run`/`index.run` (stdout captured/suppressed like Bash), `writes.append_learning_row` (+`SecurityGateError`), `memory_md.write_bounded_memory`, `status.status_json`, `classify.classify_text`, `rows.memory_security_json`, `text.ascii_lower`/`word_count_file`, `paths.rel_path`, `store.atomic_write`, `contracts`.

**Tech Stack:** Python 3.9+ stdlib only (`os`, `re`, `io`, `contextlib`, `string`, `sys`). No new deps.

## Global Constraints

- **Drop-in / scope:** new `review.py`, `tests/test_review.py`; `__main__.py` += `"review-run": review.run`. No edits to `hooks/memory-router.sh`, manifests. **No new ASCII §12 row expected** (reuses cut-c codepoint slice, atomic-write-vs-redirect, resolve_run_dir die-in-$() quirk - all already in §12; add one only if grounding finds a genuinely new divergence).
- **Source of truth (kimiflow--v0.1.50):** `cmd_review_run` (3281-3415), `quality_gate_json` (2339-2387), `first_substantive_tsv` (2656-2673), `structured_learning_tsv` (2675-2717), `learning_summary_json` (2719-2736), `review_candidate_json` (2738-2787), `write_learning_review_markdown` (3144-3168), `run_lifecycle_json` (3171-3242), `write_run_lifecycle_json` (3244-3248), `write_run_lifecycle_markdown` (3250-3279). Ground byte-for-byte (whole real Bash vs `python3 -m memory_router review-run`, isolated `env -i`, dead detect port, `KIMIFLOW_HOME` sandboxed; test token only).
- **stdout is JSON** (`json_print`); `record_run_economics_json` / `run_lifecycle_json` are captured internally (not printed). Exit codes: 0 (recorded/preview/skipped), 1 (no candidates / quality gate closed / security gate), 2 (unknown arg). The `resolve_run_dir` die-in-`$()` quirk (§12 row 197) applies: `run_dir` may be `""` -> `review="/LEARNING-REVIEW.md"`.
- **Lowercasing:** every `tr '[:upper:]' '[:lower:]'` / awk `tolower` uses `text.ascii_lower` (C-locale ASCII A-Z only), NEVER `str.lower()` - the German keyword regexes (`bestätigt`/`muss`/...) carry both umlaut + ASCII spellings and must match the ascii-lowered text exactly as Bash does.
- **Commits:** named paths only; no AI-attribution trailer. **Branch:** `main` (continues the additive/dormant port).

## Function-by-function contract

### `quality_gate_json(kind, summary, evidence)` (Bash 2339-2387)
`lower = ascii_lower(summary)`. `words` = count of `[A-Za-z0-9_-]+` tokens in `summary` (Bash `tr -cs '[:alnum:]_-' '\n'` -> `re.split(r"[^A-Za-z0-9_-]+", summary)`, drop empties). `security = rows.memory_security_json(summary)`. Append reasons IN ORDER:
1. `too_short` if `words < 7`.
2. `security_scan_failed` if `security["ok"] is not True`.
3. `too_generic` if the generic regex matches `lower` (search): `^(done|fixed|updated|changed|implemented|cleanup|misc|note|todo)[<punct><space>]*$` OR `(^|<space>)(various|several|stuff|things|something|some files)(<space>|$)` where `<punct>` = `string.punctuation`, `<space>` = `[ \t\r\f\v]`.
4. `missing_verified_evidence` if `len(evidence) == 0` OR any element `== "NOT VERIFIED"`.
5. kind-specific (search `lower`, reason if NO match):
   - `project_rule_confirmed` -> needs `(rule|confirmed|every|must|always|convention|standard|should|regel|bestätigt|bestaetigt|muss|immer|jede|jedes|konvention)` else `project_rule_without_rule`.
   - `trap_or_pitfall` -> needs `(pitfall|trap|avoid|risk|do not|don't|never|falle|risiko|vermeiden|nicht|niemals|achtung|surprise)` else `pitfall_without_avoidance`.
   - `important_decision` -> needs `(decision|decided|choose|chosen|keep|use|because|trade-off|instead|entscheidung|entschieden|bleibt|nutzen|beibehalten)` else `decision_without_decision`.
Return `{ok: len(reasons)==0, words, reasons, security}` (key order exact).

### `first_substantive_tsv(file)` (Bash 2656-2673)
Missing file -> `None`. Per `\n`-line (newline=""-faithful read), `NR` 1-based over ALL lines: strip `\r`, strip lead+trail `[ \t\r\f\v]`; skip empty; skip `^#{1,6}[ \t\r\f\v]` (headings); skip `^\`\`\`` (fences); collapse `[ \t\r\f\v]+`->" "; return `"%d\t%s" % (NR, line)` and stop. No match -> `None`.

### `structured_learning_tsv(file, kind)` (Bash 2675-2717)
Unknown kind or missing file -> `None`. Per line, `NR` 1-based: strip `\r`, remove `**`, trim, strip leading `[-*][ \t\r\f\v]+` bullet, strip leading `>[ \t\r\f\v]+`; `lower = ascii_lower(line)`; if `lower` matches the kind anchor (`^(...)[ \t\r\f\v]*:`) -> `summary=line`, if non-empty collapse whitespace, return `"%d\t%s"`, stop. Kind anchors:
- `learned`: `^(what was learned|learned|learning|lesson learned|gelernt|was gelernt wurde|erkenntnis)\s*:`
- `project_rule_confirmed`: `^(which project rule was confirmed|project rule confirmed|rule confirmed|confirmed rule|project rule|projektregel|bestaetigte regel)\s*:`
- `trap_or_pitfall`: `^(which trap or pitfall appeared|pitfall|trap|risk|avoid|falle|risiko|achtung)\s*:`
- `important_decision`: `^(which decision remains important|important decision|decision|decided|entscheidung|wichtige entscheidung)\s*:`
(`\s*` = `[ \t\r\f\v]*`.) The returned summary KEEPS the `What was learned: ...` prefix.

### `learning_summary_json(file, kind)` (Bash 2719-2736)
`row = structured_learning_tsv(file, kind)` (source `"structured"`); if falsy -> `row = first_substantive_tsv(file)` (source `"fallback"`). No row -> `None`. `line` = field before first `\t` (int). `summary` = everything after first `\t`, then codepoint `[:320]` (Bash `cut -f2- | cut -c1-320`). Empty summary -> `None`. Return `{"line": int, "summary": str, "source": str}`.

### `review_candidate_json(root, run_dir, question, kind, topic, files)` (Bash 2738-2787)
For each file in `files`: `path = run_dir/file`; skip if not a file; `info = learning_summary_json(path, kind)`, skip if None; `summary/summary_line/summary_source` from info; skip if empty summary; `rel = rel_path(root, path)`; `evidence = [rel + ":" + str(line)]`; `cls = classify.classify_text(summary)["classification"]`; `target/sensitivity/confidence` from cls; if `target=="skip"` skip; if `target=="run_only"` -> `"project_memory"`; `quality = quality_gate_json(kind, summary, evidence)`; RETURN (first match) the candidate dict (key order: question, kind, scope:"project", topic, summary, evidence, extraction_source, target, sensitivity, confidence, quality). No file matched -> `None`.

### `write_learning_review_markdown(path, run_rel, status, entries, skip_reason)` (Bash 3144-3168)
`makedirs(dirname)`; build: `# Learning Review\n\n`, `Run: <run_rel>\n`, `Status: <status>\n`, `Generated: <iso_now()>\n\n`. If `status=="skipped"`: `Skip reason: <skip_reason>\n`. Else `## Four Questions\n\n` then per entry the jq -r block (each entry's block + a trailing `\n`): `### <question>\nSummary: <summary//"">\nKind: <kind//"">\nTarget: <target//"">\nSensitivity: <sensitivity//"">\nQuality: <"passed" if quality.ok else "failed:"+reasons.join(",")>\nEvidence:\n<each "- "+ev join "\n">\nRecorded: <recorded_id//"pending">\n`. Write via `store.atomic_write` (Bash `> path`; regular file, refuse-symlink safe).

### `run_lifecycle_json(root, run_dir, learning_status, review_path, recorded_count, memory_updated, economics_update, notification)` (Bash 3171-3242)
`status_snapshot = status.status_json(root)`. Emit the object EXACTLY (keys 3190-3241): schema_version, run, generated_at(iso_now), written:true, status:learning_status, paths{learning_review(rel review_path), lifecycle_json(rel run_dir/RUN-LIFECYCLE.json), lifecycle_markdown(rel run_dir/RUN-LIFECYCLE.md), provider_sync:".kimiflow/project/VAULT-SYNC.md"}, learning{status, recorded_count, memory_updated, review_path(rel)}, usefulness:(status.usefulness//{}), economics{recorded:(eco.recorded is True), result:(eco.row.result//"unknown"), confidence:(eco.row.confidence//"none"), net_estimated_tokens_saved:(eco.row.net...//0), estimated_avoided_scan_tokens:(eco.row.est...//0), basis:"directional_estimate_only"}, curation{recommended:(status.curation.recommended is True), reasons:(status.curation.reasons//[])}, provider_sync{status:(status.provider.sync.status//"unknown"), pending_count:(...//0), direct_write_ready:(... is True), path:".kimiflow/project/VAULT-SYNC.md"}, proposals{notification}, external_writes{performed:false, reason:"review-run records local lifecycle state only; provider sync/write stays explicit"}, next_actions: `unique` of (curation.reasons//[]) + (["provider_sync_pending"] if sync.pending_count//0 > 0) + (["review_learning_proposals"] if notification.pending//0 > 0). `unique` = sorted+deduped (use `sorted(set(...))`).

### `write_run_lifecycle_json(path, obj)` (Bash 3244-3248) / `write_run_lifecycle_markdown(path, obj)` (Bash 3250-3279)
JSON: `atomic_write(path, dumps(obj, pretty=True) + "\n")` (Bash `jq . > path`). MD: the fixed template reading `.run/.status/.generated_at`, `## Learning` (recorded_count, memory_updated), `## Usefulness` (hot/warm/cold/stale `.count//0`), `## Economics` (result/confidence/net), `## Curation` (reasons join ", "), `## Provider Sync` (status/pending_count/direct_write_ready), `## Next Actions` (`- none` if empty else `- `+join "\n"). Match the exact `printf` layout (incl. blank lines).

### `run(argv)` (Bash 3281-3415)
Args `--root`/`--run`/`--write`/`--pretty`/`--skip <reason>`/`--help|-h`/unknown->`die("review-run: unknown argument: <a>", 2)`. `root = resolve_root(root)`. `run_dir = resolve_run_dir(root, run)` (die-in-$() quirk: may be `""`). `run_rel = rel_path(root, run_dir)`; `review = run_dir + "/LEARNING-REVIEW.md"` (string concat -> `/LEARNING-REVIEW.md` if run_dir==""). Defaults: `memory_updated=False`, `proposal_update={}`, `notification={}`, `economics_update={"recorded": False}`, `lifecycle_update={"written": False}`.
- **`--skip`:** if `write`: `write_learning_review_markdown(review, run_rel, "skipped", [], skip_reason)`; `economics_update = record_run_economics_json(root, run_dir)`; `lifecycle_update = run_lifecycle_json(root, run_dir, "skipped", review, 0, False, economics_update, {})`; write the two lifecycle files. Emit skip object (keys 3322-3333: schema_version, status:"skipped", run, review_path(rel review), skip_reason, written:(write==1), entries:[], recorded_count:0, memory_updated:false, economics, lifecycle); `json_print`; return 0.
- **else:** build `entries` = the non-None results of `review_candidate_json` over the 4 fixed tuples (RESEARCH/DIAGNOSIS/VERIFICATION; ACCEPTANCE/STANDARDS/PLAN; CODE-REVIEW/ADVISORIES/CURRENT-STATE; PLAN/RESEARCH/DIAGNOSIS - questions/kinds/topics per Bash 3340-3347). `count=len(entries)`; `count==0` -> `die("review-run found no reusable learning candidates; pass --skip <reason> if this run is intentionally trivial", 1)`. `quality_failures = [e for e if e.quality.ok != True]`; if any -> `die("review-run quality gate closed: " + ";".join(q.question + ":" + ",".join(q.quality.reasons//[])), 1)`.
- if `write`: per entry `id = append_learning_row(root, kind, scope, topic, summary, evidence, confidence, sensitivity, "current")` (catch `SecurityGateError` -> stderr `memory-router: memory security gate closed: <reasons>` + return 1, matching record.py); set `recorded_id`; replace entries with the recorded list. Then `write_bounded_memory(root)`; `memory_updated=True`; `curate.run(["--root",root,"--write"])` (suppress stdout); `index.run([...,"--write"])` (suppress stdout+stderr, ignore errors); `proposal_update = json.loads(capture(propose.run(["--root",root,"--write"])))` (compact stdout); `notification = proposal_update.get("notification") // {}`; `economics_update = record_run_economics_json(root, run_dir)`; `write_learning_review_markdown(review, run_rel, "recorded", entries, "")`; `lifecycle_update = run_lifecycle_json(root, run_dir, "recorded", review, count, True, economics_update, notification)`; write the two lifecycle files.
- Emit the main object (keys 3400-3413): schema_version, status:("recorded" if write else "preview"), run, review_path(rel review), written:(write==1), entries, recorded_count:(count of entries with recorded_id != None), memory_updated, proposal_update, notification, economics, lifecycle. `json_print`; return 0.

**propose capture nuance:** Bash `proposal_update="$(cmd_propose --root ... --write)"` (no `set -e`) captures propose's compact stdout regardless of exit; the reachable path (freshly-recorded learnings, no prior proposal state) returns valid JSON with a `.notification`. Capture stdout via `contextlib.redirect_stdout` (let stderr flow, like Bash), `json.loads` it; on the unreachable empty/invalid case default `proposal_update={}`/`notification={}` (Bash would cascade to a broken object there - never happens). `notification = _jq_or(proposal_update.get("notification"), {})`.

## File Structure

| Path | Responsibility |
|---|---|
| `hooks/memory_router/review.py` | NEW: the 9 helpers + `run` (review-run). |
| `hooks/memory_router/__main__.py` | register `"review-run": review.run`. |
| `hooks/memory_router/tests/test_review.py` | NEW: helper unit cases + `ReviewRunCase` + `ReviewParityCase` (whole-bash vs python: preview, quality-gate-closed, no-candidates, --skip [+write], --write full record incl. LEARNING-REVIEW.md / RUN-LIFECYCLE.json/.md / economics / proposal, --pretty, errors). |

---

### Task 1: review-run

**Step 1 (Red -> Green):** Implement `review.py` + dispatch + tests.

**Step 2 (verify):**
- `( cd hooks && python3 -m unittest discover -s memory_router/tests -p 'test_*.py' )` -> all green.
- **Grounding (isolated `env -i`, dead detect port, `KIMIFLOW_HOME` sandboxed):** `bash <pinned> review-run ...` vs `python3 -m memory_router review-run ...` over run dirs covering: a clean preview (candidates found, quality passes); quality-gate-closed (`die` exit 1 + exact summary); no-candidates (`die` exit 1); `--skip reason` (preview) and `--skip reason --write` (LEARNING-REVIEW.md skipped + economics + RUN-LIFECYCLE.*); full `--write` record (entries recorded, MEMORY/INDEX/RECALL refreshed, propose run, economics, all artifacts written); `--pretty`; unknown arg / resolve_run_dir quirk (no `--run`). Compare stdout (ts/date/random-id normalized) AND every written artifact byte-for-byte.
- ASCII check on `review.py` + tests -> clean.

## Self-Review (grounding evidence)

**Plan-audit (external, pre-impl):** 0 BLOCKER / 0 HIGH. 2 LOW, both informational (no corrections): (1) `die()` returns the code -> use `return die(...)` per the established sibling-module pattern (followed); (2) `cut -c1-320` codepoint slice is the already-blessed §12 row-196 convention - grounding should include a >320-byte umlaut summary (added a unit test confirming 320 codepoints / 622 bytes).

**Grounded byte-for-byte vs the pinned Bash subcommand** (`ReviewParityCase`, isolated env, dead detect port, `KIMIFLOW_HOME` sandboxed, salt pre-seeded). Stdio-only cases (separate roots, deterministic) compare **stdout + stderr + exit**: preview (candidates found, quality passes), `--pretty` preview, quality-gate-closed (`die` exit 1 + exact `;`-join summary), no-candidates (`die` exit 1), unknown-arg (exit 2), `--skip` preview. Write cases (SAME root, reset to the canonical pre-run state between bash and python so anonymized hashes + evidence fingerprints reproduce; only timestamps + the pid-suffixed learning ids normalized) compare stdout + stderr + every written artifact: `--skip --write` and full `--write` (record 2 learnings -> bounded memory -> curate -> index -> propose -> economics) verify `LEARNING-REVIEW.md`, `RUN-LIFECYCLE.json`, `RUN-LIFECYCLE.md`, `LEARNINGS.jsonl`, `MEMORY-ECONOMICS.jsonl`, and the global `token-economics.jsonl` byte-for-byte. `RECALL.sqlite` (engine differs, §12) and `MEMORY.md` (blessed `-c`-body divergence, §12 row 181) are excluded by design. Helper unit tests cover quality_gate (reason order, kind-specific, not-verified, ascii umlaut), first/structured tsv (fence-pair skip, anchor keeps prefix), learning_summary (structured/fallback, cut-320 ASCII + multibyte), review_candidate (first-file, key order), lifecycle next_actions unique-sort.

**Key fidelity points:** `text.ascii_lower` (NOT `str.lower`) for every tr/tolower so German umlaut keywords match Bash; the propose stdout is captured via `redirect_stdout` (stderr flows, no `set -e`) and `.notification // {}` extracted; the `resolve_run_dir` die-in-`$()` quirk yields `run_dir==""` -> `review="/LEARNING-REVIEW.md"` (§12 row 197); `SecurityGateError` is unreachable (the quality gate's `security_scan_failed` closes first) but handled for parity; `bestätigt` is written as a `ä` escape (ASCII source).

**Independent senior-review:** 0 BLOCKER / 0 HIGH. 1 P3: `_compare_write` did not assert stderr -> added `assertEqual(_norm(be), _norm(pe))` (the write path's curate/propose stderr flows through on both sides; empty on success, now grounded).

**Suite:** 441 -> 464 tests, all green. ASCII-clean on `review.py` + `tests/test_review.py`. `review-run` registered -> **13/13 subcommands wired**. No `memory-router.sh`/manifest change (additive + dormant). No new §12 row (reuses cut-c codepoint, atomic-write-vs-redirect, die-in-$() - all already registered).
