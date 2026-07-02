# kimiflow_core rebuild design

**Date:** 2026-07-02 · **Scope:** R1 Python core rebuild for large state/status helpers.

This spec is the divergence ledger for the R1 port. The implementation plan is `docs/superpowers/plans/2026-07-02-rebuild-r1-core-detail.md`.

## 12. Known parity divergences

Every deliberate old-vs-new behavior change must be added here in the same commit as the code change, with a matching code comment and parity harness whitelist/expectation.

| Area | Bash behavior | Python behavior | Rationale |
|---|---|---|---|
| `improvements-status` mutating explicit invalid `--root` | `resolve_root` printed the invalid explicit root when `cd "$root"` failed, so `mark-done`/`reopen` proceeded and usually failed later as "queue file not found" under that synthetic path. | Mutating commands fail closed during root resolution (`improvements-status: cannot resolve root: <path>`, exit 2). `list` keeps observational root behavior. | R1 root canonicalization: mutating state writes must not proceed from a known-invalid explicit root. Hook-safe/read-only behavior is preserved separately. |
| `project-map-status` failed mutating INDEX writes | `refresh`, `refresh --changed`, and `index-symbols` used bare `mktemp`/`mv` write paths; a temp/install failure could still be followed by `REFRESHED` or `SYMBOLS` because callers did not consistently check the failed write helper. | Mutating commands install through same-directory atomic writes with mode `0600`; if install fails, they emit `project-map-status: cannot install <index>`, exit non-zero, and do not print the success line for the failed operation. | R1 write-safety rule: project state mutation must be private-mode, same-filesystem atomic, and honest about failed persistence. |
