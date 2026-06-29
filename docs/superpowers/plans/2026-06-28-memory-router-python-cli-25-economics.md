# memory-router Python CLI - Plan 25: economics-record writer subsystem

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Port the economics-record **writer** subsystem - the code path that, given a run dir, computes a per-run token-economics row, appends it to the project ledger `.kimiflow/project/MEMORY-ECONOMICS.jsonl`, and (when global metrics are enabled) records an anonymized row to `~/.kimiflow/metrics/token-economics.jsonl`. The single public entry point is `record_run_economics_json(root, run_dir)` (Bash 3124-3142), which `review-run` (Plan 26) calls on both the `--skip --write` and the record-`--write` paths. Split out of review-run per the session-6 handoff because it is a self-contained writer cluster with its own global-metrics + hashing dependencies.

**Architecture:** New module `hooks/memory_router/economics.py` holding the eight economics functions. The shared global-metrics infra (`hash_text`, `ensure_global_metrics_salt`, `anonymous_hash_id`) is added to the existing `hooks/memory_router/global_metrics.py` (which already owns `enabled`/`base_dir`/`display_path` from Plan 12). The two row-enrichment helpers used ONLY by the global writer (`project_size_bucket`, `run_type_from_state`) live in `economics.py`. **No subcommand is wired in this plan** (no `__main__` change) - it is pure library code consumed by Plan 26. It is therefore additive + dormant exactly like the rest of the port.

**Tech Stack:** Python 3.9+ stdlib only (`os`, `re`, `math`, `hashlib`, `subprocess` for `git ls-files`). No new deps.

## Global Constraints

- **Drop-in / scope:** new `economics.py`, `tests/test_economics.py`; `global_metrics.py` += `hash_text`/`ensure_global_metrics_salt`/`anonymous_hash_id`. No edits to `hooks/memory-router.sh`, `__main__.py`, manifests. **No new §12 row expected** (the writers reuse already-blessed atomic-write + hashlib-vs-shasum rationale; add one only if grounding surfaces a genuine new divergence).
- **Source of truth:** Bash @ `kimiflow--v0.1.50`: `run_artifact_corpus` (2886), `recall_hits_for_economics_json` (2903), `economics_hits_tokens` (2919), `economics_used_hits_count` (2933), `run_economics_row_json` (2949-3022), `write_economics_row` (3024-3043), `write_global_economics_row` (3045-3122), `record_run_economics_json` (3124-3142); `hash_text` (387), `ensure_global_metrics_salt` (406), `anonymous_hash_id` (429), `project_size_bucket` (434), `run_type_from_state` (447). Ground byte-for-byte (helper-level harness, isolated `env -i`, `KIMIFLOW_HOME` sandboxed, salt file pre-seeded so hashes are deterministic on both sides).
- **Numbers:** every economics field is integer-valued (word counts, `used_hits * avoided_per_hit`, integer subtraction, `| floor` of an int-or-float division). `contracts.dumps` already renders ints faithfully; no float path is reachable here. The savings-percent `floor` must match jq's `floor` (round toward -inf) - use `math.floor` over true division (`net * 100 / avoided`), guarded by `avoided > 0` (else `null`).
- **Hashing (§12 row 180 generalization):** `hash_text` uses `hashlib.sha256(data.encode()).hexdigest()` instead of shelling to `shasum`/`sha256sum`; identical on targets that have them. `anonymous_hash_id(salt, value)` hashes exactly the bytes `salt:value` (no trailing newline, matching `printf '%s:%s'`).
- **Commits:** named paths only; no AI-attribution trailer. **Branch:** `feat/memory-router-py-foundation`.

## Function-by-function contract

### `global_metrics.hash_text(data)` (Bash 387-404)
Return `hashlib.sha256(data.encode("utf-8")).hexdigest()` (lowercase hex). Bash falls back `shasum -a 256` -> `sha256sum` -> fail; the stdlib is always present, so the port never fails. `data` is the exact string to hash (no added newline).

### `global_metrics.ensure_global_metrics_salt(dir)` (Bash 406-427)
`os.makedirs(dir, exist_ok=True)` (return `""` on failure -> Bash `return 1`); best-effort `chmod 0o700`. If `dir/salt` missing: generate a salt (prefer `secrets.token_hex(32)` to mirror `openssl rand -hex 32`; the Bash iso/pid/RANDOM fallback is unreachable when stdlib is present), write it with `0600` perms (umask-077 equivalent), best-effort `chmod 0o600`. Return the **first line** of `dir/salt` (matches `sed -n '1p'`), or `""` if unreadable. **Tests pre-seed the salt file** so this never generates and both runtimes read the identical salt.

### `global_metrics.anonymous_hash_id(salt, value)` (Bash 429-432)
`return hash_text("%s:%s" % (salt, value))`.

### `economics.project_size_bucket(root)` (Bash 434-445)
`count` = number of lines from `git -C <root> ls-files` (subprocess; on any failure/non-numeric -> 0). `< 200 -> "small"`, `< 1000 -> "medium"`, else `"large"`.

### `economics.run_type_from_state(run_dir)` (Bash 447-481)
Parse `run_dir/STATE.md` for the first `mode:` line: per line strip `\r`, strip `**`, strip a leading `- ` bullet, lowercase; if it matches `^mode:\s*` take the remainder lowercased. Then map: contains `fix`|`bug` -> `bugfix`; `audit` -> `audit`; `doc` -> `docs`; `refactor` -> `refactor`; `feature` -> `feature`. If no STATE.md / no mode match: `PROBLEM.md`|`DIAGNOSIS.md` present -> `bugfix`; `AUDIT.md`|`AUDIT-INTENT.md` present -> `audit`; else `unknown`. (Replicate the awk: `-F': *'` is irrelevant to the body since it rebuilds `line=$0`; the gsub/sub sequence is what matters.)

### `economics.run_artifact_corpus(run_dir)` (Bash 2886-2901)
Concatenate, for each of `RESEARCH.md DIAGNOSIS.md PLAN.md ACCEPTANCE.md REVIEW.md CODE-REVIEW.md VERIFICATION.md ADVISORIES.md` that exists: its first 220 lines (`sed -n '1,220p'`) then a `\n`. Then, if `run_dir/findings` is a dir: for each `*.md` file found (recursively) **sorted by path**, its first 120 lines then a `\n`. Return the joined string. **Findings recursion + sort** mirrors `find ... -name '*.md' | sort` (lexicographic full-path sort).
- **sed line reader (audit MEDIUM fix):** `sed -n '1,Np'` splits on `\n` ONLY and does **NOT** add a trailing newline - it preserves the input's final-line terminator (verified: `printf 'a\nb\nc' | sed -n '1,220p'` -> `a\nb\nc`, no trailing `\n`; `printf 'a\nb\nc\n'` -> `a\nb\nc\n`). Use a faithful local `_sed_head(content, count)`: split on `\n`; if `len(lines) <= count` return `content` unchanged; else `"\n".join(lines[:count]) + "\n"`. Read with `newline=""` to keep `\r` like sed. The per-file `_sed_head(...) + "\n"` then concatenates. (The wrong "append `\n` if non-empty" recipe double-newlines trailing-newline files and is dropped.)
- **Internal-only:** corpus feeds ONLY `economics_used_hits_count`'s substring `contains` and is never emitted; the whole function output is captured by Bash `corpus="$(...)"`, which strips trailing newlines anyway. The newline exactness therefore cannot affect any byte-compared output (needles are single-line), but `_sed_head` is implemented faithfully regardless.

### `economics.recall_hits_for_economics_json(recall_json)` (Bash 2903-2917)
If `recall_json` exists and its content is a **dict** (audit LOW: Bash gates on `jq -e .`, which fails for top-level `null` OR `false` -> both route to `[]`; `store.read_json` returns `None`/`False` for those, so guard with `isinstance(data, dict)`, NOT `data is not None`): return the concatenation, in this order, of `sources.learnings.hits`, `sources.facts.hits`, `sources.index.hits`, `sources.history.hits` (each `[]?` -> empty if missing/not a list), with each hit dict shallow-merged with `{"_economics_source": <"learning"|"fact"|"index"|"history">}`. Else `[]`. (Non-dict hit -> jq `+` would error; schema-valid hits are dicts. Guard with `isinstance(hit, dict)` and skip otherwise = same robustness class as the recall §12 rows; note if used.)

### `economics.economics_hits_tokens(hits)` (Bash 2919-2931)
Sum over hits of: `((title//"") + " " + (summary//"") + " " + (body//"") + " " + (text//""))`, `gsub("[^A-Za-z0-9_]+"; " ")`, split on `" "`, drop empties, count. Return total (`add // 0` -> 0 for empty). Use `_jq_or(field, "")` then `str()` per field. (Audit LOW: jq's `+` does NOT stringify a non-string - `number + " "` would type-error and abort; but schema hit fields are always strings, so the non-string path is unreachable and the `str()` is merely defensive.) Regex: `re.sub(r"[^A-Za-z0-9_]+", " ", joined)` then `.split(" ")` dropping empties.

### `economics.economics_used_hits_count(hits, corpus)` (Bash 2933-2947)
Count hits where ANY needle is a substring of `corpus`. needles = `[id, ref, path, title, (evidence[0] // "")]` filtered to `length > 0` (after `// ""`). `corpus | contains($needle)` = plain substring test. A hit with zero non-empty needles -> `any(empty)` is false -> not counted.

### `economics.run_economics_row_json(root, run_dir)` (Bash 2949-3022)
- `project = root/.kimiflow/project`; `run_rel = rel_path(root, run_dir)`; `memory = project/MEMORY.md`; `user_memory = project/USER.md`; `recall_json = run_dir/RECALL.json`.
- If `recall_json` content is a **dict** (same `isinstance` guard as above, matching `jq -e .`): `always_tokens = sources.memory.tokens_estimate // 0`, `user_tokens = sources.user_profile.tokens_estimate // 0` (read as ints; jq `-r ... // 0` then `--argjson`). Else `always_tokens = word_count_file(memory)`, `user_tokens = word_count_file(user_memory)`.
- `hits = recall_hits_for_economics_json(recall_json)`; `corpus = run_artifact_corpus(run_dir)`; `recall_tokens = economics_hits_tokens(hits)`; `hit_count = len(hits)`; `used_hits = economics_used_hits_count(hits, corpus)`.
- `avoided_per_hit = KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT` (non-numeric/empty -> 1200); `avoided = used_hits * avoided_per_hit`; `net = avoided - always_tokens - user_tokens - recall_tokens`.
- `result`: `hit_count==0 -> "unknown"`; elif `used_hits>0 and net>0 -> "saving"`; elif `net<0 -> "waste"`; else `"neutral"`.
- `confidence`: `hit_count==0 -> "none"`; elif `used_hits>0 -> "medium"`; else `"low"`.
- Emit the object (key order EXACT, Bash 3004-3021): `schema_version:1, run, recorded_at:iso_now(), always_on_tokens, user_memory_tokens, recall_tokens, recall_hit_count, used_hit_count, estimated_avoided_scan_tokens, net_estimated_tokens_saved, result, confidence, basis:{recall_json: rel_path(root, recall_json), heuristic: "avoided_scan_tokens = used_hit_count * KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT (default 1200); directional only"}`.

### `economics.write_economics_row(root, row)` (Bash 3024-3043)
`project = root/.kimiflow/project`; `file = project/MEMORY-ECONOMICS.jsonl`; `os.makedirs(project)`. If `file` exists: read it, parse each non-empty line (skip unparseable -> the `fromjson? // empty` convention = `store.read_jsonl`), DROP rows whose `.run // "" == row.run // ""`, append `row`, rewrite (one compact JSON per line + trailing `\n`). Else write just `row` (compact + `\n`). Use `store.atomic_write` (temp+rename; the Bash `mktemp`+`mv` is atomic and replaces a symlink target -> `refuse_symlink=False` to match `mv`). **Verify the no-trailing/with-trailing newline byte layout vs Bash during grounding** (jq stream output ends each record with `\n`).

### `economics.write_global_economics_row(root, run_dir, local_row)` (Bash 3045-3122)
Early-return dicts (each is the FULL return value, key order `{recorded, reason}` or `{recorded, path, summary}`):
- `not global_metrics.enabled()` -> `{"recorded": False, "reason": "disabled"}`.
- `dir = global_metrics.base_dir()`; falsy -> `{"recorded": False, "reason": "home_unavailable"}`.
- `salt = ensure_global_metrics_salt(dir)`; falsy -> `{"recorded": False, "reason": "salt_unavailable"}`.
- `project_hash = anonymous_hash_id(salt, root)`; `run_hash = anonymous_hash_id(salt, root + ":" + rel_path(root, run_dir))`; either falsy -> `{"recorded": False, "reason": "hash_unavailable"}` (unreachable - hashlib never fails).
- `run_type = run_type_from_state(run_dir)`; `size_bucket = project_size_bucket(root)`; `host = KIMIFLOW_HOST` if in `{codex, claude}` else `"unknown"`.
- Build `row` from `local_row` (key order EXACT, Bash 3074-3098): `schema_version:1, recorded_day: date_now(), host, run_type, project_size_bucket, project_id, run_id`, then the 7 token fields each `(local_row.<f> // 0) | tonumber? // 0` (int coercion; non-numeric -> 0), `estimated_savings_percent`: if `estimated_avoided_scan_tokens > 0` then `floor(net_estimated_tokens_saved * 100 / estimated_avoided_scan_tokens)` else `null`, `result`: passthrough if in `{unknown,saving,neutral,waste}` else `"unknown"`, `confidence`: passthrough if in `{none,low,medium,high}` else `"low"`, `basis:{heuristic:"directional_estimate_only", stores_content:False, stores_paths:False, local_only:True}`.
- `os.makedirs(dir)` (fail -> `{"recorded": False, "reason": "mkdir_failed"}`); best-effort chmod 0o700; `file = dir/token-economics.jsonl`. Same dedupe-by-`run_id` + append + atomic write as the project ledger (errors at each step -> the matching `{recorded:False, reason:...}`: `mktemp_failed`/`write_failed`/`move_failed`). chmod 0o600 on tmp + final.
- Success -> `{"recorded": True, "path": global_metrics.display_path(), "summary": summaries.global_efficiency_summary_json()}`.
- **Pragmatic port:** the granular `mktemp_failed`/`write_failed`/`move_failed` reasons come from `mktemp`/`jq`/`mv` failures that the stdlib path collapses; replicate the reachable ones (`disabled`/`home_unavailable`/`salt_unavailable`/success) faithfully and keep a single best-effort try/except mapping write failures to `write_failed` (document any reason-string narrowing in the self-review; these branches are unreachable on the harness host).

### `economics.record_run_economics_json(root, run_dir)` (Bash 3124-3142)
`row = run_economics_row_json(root, run_dir)`; `write_economics_row(root, row)`; `summary = summaries.economics_summary_json(root/.kimiflow/project/MEMORY-ECONOMICS.jsonl)`; `global_update = write_global_economics_row(root, run_dir, row)`. Return (key order EXACT): `{recorded:True, path:".kimiflow/project/MEMORY-ECONOMICS.jsonl", row, summary, global: global_update}`.

## File Structure

| Path | Responsibility |
|---|---|
| `hooks/memory_router/economics.py` | NEW: the 8 economics functions + `project_size_bucket` + `run_type_from_state`. |
| `hooks/memory_router/global_metrics.py` | EDIT: add `hash_text`, `ensure_global_metrics_salt`, `anonymous_hash_id`. |
| `hooks/memory_router/tests/test_economics.py` | NEW: helper unit cases + an `EconomicsParityCase` that shells to the pinned Bash for `record_run_economics_json` (project + global ledgers, ts/recorded_at/recorded_day normalized, salt pre-seeded). |

---

### Task 1: economics writer subsystem

**Step 1 (Red -> Green):** Implement `economics.py` + the 3 `global_metrics.py` helpers + tests. No `__main__` change.

**Step 2 (verify):**
- `( cd hooks && python3 -m unittest discover -s memory_router/tests -p 'test_*.py' )` -> all green.
- **Grounding (isolated `env -i`, `KIMIFLOW_HOME` sandboxed, salt pre-seeded):** drive `record_run_economics_json` through a tiny bash shim that sources the pinned script and calls the function, vs a Python shim that imports `economics`, over run dirs covering: RECALL.json present (tokens from sources) vs absent (word_count fallback); hits used (`saving`) vs unused (`neutral`/`waste`/`unknown`); findings/ recursion in the corpus; `run_type_from_state` each branch (STATE.md mode variants + PROBLEM/AUDIT fallbacks); `project_size_bucket` (git vs non-git -> `small`); global enabled vs `KIMIFLOW_GLOBAL_METRICS=off` (`disabled`) vs no HOME (`home_unavailable`). Compare the returned JSON (normalize `recorded_at`/`recorded_day`) AND the two written `.jsonl` files (dedupe-by-run / run_id, byte layout).
- ASCII check on `economics.py` + `global_metrics.py` -> clean (middot/accents as `\uXXXX`).

## Self-Review (grounding evidence)

**Plan-audit (external, pre-impl):** 0 BLOCKER / 0 HIGH. 1 MEDIUM (the `sed -n '1,Np'` reader recipe was concretely wrong - sed does NOT add a trailing newline; fixed to the faithful `_sed_head` and verified empirically) + 2 LOW (jq `+` does not stringify -> reworded; `jq -e .` null/false gate -> `isinstance(dict)`). All folded into the plan before implementation.

**Grounded byte-for-byte vs the pinned Bash** (`EconomicsParityCase`, isolated env, `KIMIFLOW_HOME` sandboxed, salt pre-seeded, SAME root both sides so the `salt:value`/`salt:root:rel` hashes are genuinely comparable): the bash side sources a dispatch-free copy of the pinned script (everything before `cmd="${1:-}"`) and calls `record_run_economics_json` directly. Compared the pretty stdout (`jq -n` is pretty), the project ledger, AND the global ledger (timestamps/dates normalized) for: RECALL.json-present `saving` (+ project-ledger `mode=0o600` asserted on both sides); word-count `fallback` (no RECALL.json, `unknown`/`none`); `KIMIFLOW_GLOBAL_METRICS=off` (`global: {recorded:false, reason:"disabled"}`); `waste` with a small `KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT` (net<0, `confidence:medium`, **negative `estimated_savings_percent` floor** = -160); `neutral` (net==0, used==0); and the **dedupe/existing-file branch** (pre-seeded both ledgers with a colliding + a non-colliding row -> colliding replaced, non-colliding survives, byte-identical on both runtimes). Helper-level unit tests cover `_sed_head`, corpus order/cap/findings-sort, recall-hit order+tag, hits-tokens, used-hits (incl. evidence[0] + empty needles), `run_type_from_state` (mode variants + file fallbacks), `project_size_bucket` boundaries (mocked git), `_gnum`, `_dedupe_append`.

**Independent senior-review:** 0 BLOCKER / 0 HIGH. 3 LOW, all addressed: (1) project ledger was `0o644` vs Bash `0o600` (mktemp+mv) -> fixed `write_economics_row(mode=0o600)` + asserted in parity; (2) dedupe branch was only unit-tested -> added the `test_parity_dedupe_existing_ledgers` parity case (+ `waste`/`neutral`); (3) findings glob included symlinked `.md` vs Bash `find -type f` -> added `os.path.isfile and not os.path.islink` guard.

**Reason-string narrowing (§12 row added):** `write_global_economics_row` collapses the unreachable stepwise `mktemp_failed`/`move_failed` into `write_failed` (stdlib atomic writer); the reachable reasons + both ledgers are byte-identical.

**Suite:** 415 -> 441 tests, all green. ASCII-clean on `economics.py`, `global_metrics.py`, `tests/test_economics.py`. No `__main__`/manifest/`memory-router.sh` change (additive + dormant). One §12 row added.
