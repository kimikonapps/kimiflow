# memory-router Python CLI - Plan 28: cutover (Bash runtime -> Python shim)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. This is the final, public, hard-to-reverse step. **User-authorized (2026-06-29): full cutover + release.**

**Goal:** Make the Python `memory_router` package the ACTIVE runtime. Replace the 4405-line `hooks/memory-router.sh` body with a thin shim that execs `python3 -m memory_router`, delete the Bash logic, update docs (README / COMPATIBILITY / CHANGELOG), prove the whole local test + smoke suite stays green, then `/release`.

**Why it's safe now:** all 13/13 subcommands are wired and grounded byte-for-byte vs the pinned `kimiflow--v0.1.50` Bash (491 tests, every command externally plan-audited + senior-reviewed). The grounding harnesses (`test-memory-router-parity.sh`, the in-package `*ParityCase`s) compare against the **pinned tag** via `git show`, NOT the working tree, so they keep working after the swap.

## The shim (replaces all of `hooks/memory-router.sh`)

```bash
#!/usr/bin/env bash
# kimiflow - token-cheap local memory router. Orchestrator-invoked, not a hook.
# Python (stdlib >= 3.9) port shim: the implementation lives in hooks/memory_router/.
# This thin wrapper preserves the historical `memory-router.sh <cmd> ...` entrypoint by
# pointing PYTHONPATH at this directory and exec'ing the package. See docs/superpowers/.
dir="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m memory_router "$@"
```

- `exec` -> the Python process replaces the shell, so the exit code propagates unchanged.
- `dir` = the script's own directory (= `<plugin>/hooks/`), which contains the `memory_router/` package; PREPEND it to any existing `PYTHONPATH`.
- **Preserve the executable bit** (`chmod 0755`): `launcher-status.sh` guards on `[ -x "$SCRIPT_DIR/memory-router.sh" ]` (170/484) and `active-run.sh` execs `$SCRIPT_DIR/memory-router.sh` (743). git currently tracks mode `100755`; after `Write` re-`chmod +x` and confirm `git ls-files -s` still shows `100755`.
- **Package ships:** all 56 `hooks/memory_router/*.py` are git-tracked; `.gitignore` only excludes `__pycache__/`+`*.pyc`. Claude install copies the plugin tree; codex `install-codex-hooks.sh` wrappers `exec "$KIMIFLOW_PLUGIN_ROOT/hooks/<script>"`, so the shim always runs from the plugin `hooks/` where the package lives.
- **New host requirement:** `python3 >= 3.9` (the harness host has 3.9.6). Document it (COMPATIBILITY). The old runtime already required `jq`, so an external interpreter dependency is not new in kind.

## Callers that now go through the shim (must stay byte-identical)
- `hooks/launcher-status.sh` -> `memory-router.sh verify-run` (171) + `status` (485) (verified by `test-launcher-status.sh`).
- `hooks/active-run.sh` -> `memory-router.sh` via `$KIMIFLOW_MEMORY_ROUTER` default (743) (verified by `test-active-run.sh`).
- `hooks/agentic-readiness.sh` (provider auth references).

## Docs to update
- **CHANGELOG.md:** replace the `## Unreleased` placeholder with the cutover entry (Changed: memory-router runtime is now the Python package via a shim; Bash logic removed. Added: python3 >= 3.9 requirement). `/release` promotes `## Unreleased` -> `## <ver>`.
- **COMPATIBILITY.md:** add a host-primitive row for `python3 >= 3.9` (load-bearing: the memory-router shim execs it); bump the "Last verified against" line (the `/release` skill / consistency check also bumps the version token).
- **README.md:** the descriptive mentions (`hooks/memory-router.sh`, l.197/294/299) remain accurate (same CLI); add one line noting the implementation is now a stdlib-Python package behind the unchanged entrypoint.

## Legacy test disposition (audit BLOCKER)

`hooks/test-memory-router.sh` (672 lines, 107 assertions, header "unit tests for memory-router.sh") invokes the **working-tree** router (`SCRIPT=.../memory-router.sh`), so post-cutover it runs against the shim. 104/107 pass under Python; **3 fail** because they assert Bash-impl artifacts that are the documented §12 stdlib divergences and cannot translate:
- `provider_health_validates_env_token_without_storing_it` (l.255) asserts `.auth.probe_http_status == "200"` from a **fake `curl` stub** - Python uses `urllib`, ignores the stub, and there is no real loopback server -> not 200.
- `global_efficiency_requires_strong_hash` / `..._no_weak_hash_row` (l.412/415) assert `recorded==false, reason=="hash_unavailable"` when `openssl`/`shasum`/`sha256sum` are **stubbed absent** - Python uses stdlib `hashlib`, so it still records.

**Disposition: RETIRE `test-memory-router.sh` + raise `test-memory-router-unit.sh` to the FULL suite.** It is the unit test FOR the Bash implementation being deleted; its 104 translating assertions are redundant with the 491-test Python suite (which grounds every subcommand byte-for-byte vs the pinned Bash, and covers auth-probe via a REAL server in `HttpProbeRedirectCase` + hashlib in the economics tests), and the 3 non-translating ones test obsolete Bash artifacts. `test-memory-router-unit.sh` currently runs only 3 foundation modules - update it to `python3 -m unittest discover` so all 491 gate releases (closing a pre-existing gap). End-to-end shim coverage stays via `test-launcher-status.sh` (invokes the shim's `verify-run` + `status`). (Audit LOWs: `test-active-run.sh` stubs the router so it does NOT exercise the shim - corrected; CI/smoke only `bash -n` the router - acceptable, package is git-tracked + import-tested by the unit gate.)

## Verification (test-driven cutover - this IS the proof)
After the swap + `chmod +x`, ALL must pass (this is exactly what `/release` re-runs):
1. `( cd hooks && python3 -m unittest discover -s memory_router/tests -p 'test_*.py' )` -> 491 OK.
2. **Every `hooks/test-*.sh` exits 0** (loop) - critically `test-memory-router-parity.sh` (python vs pinned bash), `test-memory-router-unit.sh`, `test-launcher-status.sh` + `test-active-run.sh` (the shim end-to-end via the callers).
3. `bash hooks/release-consistency-check.sh` -> exit 0.
4. `bash hooks/smoke-install.sh` and `bash hooks/smoke-install-codex.sh` -> exit 0.
5. **Direct spot-check:** `hooks/memory-router.sh status --root <tmp>` and `... --help` produce byte-identical stdout/stderr/exit vs `bash <pinned-tag-copy> ...` under the dead-detect env.
6. The shim file is valid (`bash -n hooks/memory-router.sh`) and executable.

## Rollback
The swap is one commit. `git revert <shim-commit>` restores the full Bash runtime instantly (the Python package stays, harmlessly dormant again). No data migration, no manifest coupling.

## Commit + release
- Commit the shim + doc edits (named paths; no AI trailer): `feat(memory_router): cut over memory-router.sh to the Python runtime (shim)`.
- Push local `main` (the ~15 additive/dormant commits + this) - user-authorized.
- `/release` (version bump across manifests + COMPATIBILITY, CHANGELOG promote, consistency check, full test/smoke suite, tag `kimiflow--v<ver>`, push main + tag, GitHub release). Next version: **0.1.52** (patch - CLI contract unchanged, implementation swapped + python3 documented).

## Self-Review (evidence)

**External plan-audit:** 1 BLOCKER (the legacy `test-memory-router.sh` would fail the release gate against the shim) + 2 LOW. Resolved: retired `test-memory-router.sh`, raised `test-memory-router-unit.sh` to the full discover, corrected the LOW about `test-active-run.sh` (it stubs the router; the real shim end-to-end gate is `test-launcher-status.sh`). Everything else (consumers, shim, packaging, python floor, CI, reversibility) the audit verified safe.

**python3 floor (independent check):** `python3` on PATH resolves to system `/usr/bin/python3` = **3.9.6**; the FULL 491-test suite passes under it (no 3.10+ syntax). The shim therefore runs green on the production interpreter.

**Shim:** 8-line wrapper, `bash -n` clean, git mode `100755` (executable bit preserved). `dir="$(cd "$(dirname "$0")" && pwd)"` + `exec env PYTHONPATH="$dir${PYTHONPATH:+:$PYTHONPATH}" python3 -m memory_router "$@"`.

**Byte-for-byte spot-check (shim vs pinned `kimiflow--v0.1.50` Bash, dead-detect env):** `--help`, `status`, `status --pretty`, `verify-run` (exit 1), `metrics`, `provider status`, `provider setup --host codex`, `classify`, unknown-command (exit 2) — **all 9 identical** on stdout + stderr + exit.

**Full local suite (= what `/release` re-runs):** all **30 `hooks/test-*.sh` green** (incl. `test-memory-router-parity.sh` python-vs-pinned, `test-memory-router-unit.sh` now the full 491, `test-launcher-status.sh` exercising the shim end-to-end); `release-consistency-check.sh` exit 0; `smoke-install.sh` + `smoke-install-codex.sh` exit 0 (after repointing their `test-memory-router.sh` structural check to `test-memory-router-parity.sh`). CI hard gates (`ci.yml`) call `test-memory-router-unit.sh` + `-parity.sh` (not the retired file) — unaffected + strengthened.

**Rollback:** single `git revert` of the shim commit restores the Bash runtime; the Python package stays dormant.

**Next:** commit (docs plan + feat shim), push `main` (user-authorized), `/release` -> 0.1.52.
