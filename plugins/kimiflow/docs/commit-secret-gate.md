# commit-secret-gate — maintainer notes (mechanics, boundaries, residual gaps)

Maintainer/background documentation for `hooks/commit-secret-gate.sh`, extracted from
`reference.md` "Commit hygiene" (audit batch B4 — the runtime instruction path keeps only the
operative rules; this file is the single home for the enforcement mechanics). The operative
contract lives in `reference.md` "Commit hygiene (Phase 7 commit-gate)" and SKILL.md Phase 7.

## Enforcement mechanics

The `commit-secret-gate` PreToolUse hook **blocks** `git add -A`/`.` (incl. the `./`, `:/` and
`:(top)` whole-tree pathspec synonyms and `-A` flag clusters) and any `git commit` whose staged
paths — or, for `git commit -a`/`--all`, the tracked working-tree paths it would auto-stage —
match secret patterns: `.env`/`.envrc` incl. `*.env` suffixes like `prod.env`,
`*.pem/.key/.p12/.pfx/.asc`, private SSH keys `id_rsa`/`id_dsa`/`id_ecdsa`/`id_ed25519` (not
`.pub`), `.npmrc`/`.pypirc`, `secret(s)`/`credential(s)`/`api_key`/`access_token`/`auth_token`
in a path; a combined `git add <secret> && git commit` is also caught.

In Claude Code and Codex the hook ships through the plugin's manifest-declared
`hooks/hooks.json`; `hooks/install-codex-hooks.sh --check` validates that loaded contract and
writes no unregistered user-level wrappers. Commits in repos without
`.kimiflow/` are knowingly unprotected (the hook is auto-active only where a `.kimiflow/`
directory exists at the git root, so it never polices unrelated repos).

The pattern list is a **minimum deny-list**, not exhaustive; false positives on filenames merely
containing those words are possible (resolve by committing the safe file by name from outside a
kimiflow run).

## Parsing boundaries (by design)

The gate matches secret-looking **paths**, never file **contents** — a secret pasted into source
(e.g. `const API_KEY = "sk-…"` in `app.js`) passes through untouched; that is what the
complementary content scanners (`gitleaks`: regex + Shannon entropy; `trufflehog`: per-credential
detectors + live verification) and the advisory wrapper `hooks/secret-content-scan.sh` are for.

Outside the active run owner's session, four compatibility boundaries remain:

1. The precise (jq) path only governs `git` at a **command position** (line start, or after
   `;`/`&`/`|`) — `sudo git …`, `env X=y git …`, a path-prefixed `/usr/bin/git …`, and a
   `command`/`builtin`/`exec git …` wrapper are out of scope by design (a deliberate
   non-standard invocation is not the gate's threat model — it is accident-hygiene). A global
   **`git -C <path>` IS honored**, though: the gate resolves the target repo via git's own
   cumulative `-C` (so `git -C <repo> commit` run from another cwd is scoped to `<repo>`, not
   the tool cwd), for **unquoted, space-free** `-C` paths — a quoted `-C` path containing a
   space (`git -C "my repo"`) stays a residual.
2. The **jq-less fallback is intentionally blunt** — unable to extract the command, it greps the
   raw payload and may **over-block** a benign command that merely mentions git (e.g.
   `echo "git commit later"`). Over-blocking is the safe failure for a fail-closed gate; install
   `jq` for the precise path rather than expecting a regex to parse the shell.
3. An explicit **pathspec commit** (`git commit <path>`, e.g. `git commit .env -m …`) of an
   **already-tracked** secret-looking file is **not** covered — it stages the named path at
   commit time, and reliably parsing a pathspec out of a shell string needs an AST, not a regex.
4. An **escaped quote** inside the `-m` message (`git commit -m "a\"; b" -a`) can desync the
   naive quote-strip and re-hide a `;`/`&`/`|` separator before the `-a` — also out of scope
   (same root: regex ≠ shell parser).

The `git commit -a`/`--all` form — including bundled short flags where `a` is not first
(`-am`/`-vam`/`-qam`), and a metachar **hidden in a quoted message** (`-m "a; b" -a`) or behind a
**backslash-newline continuation** — **is** covered: the command is line-joined and unquoted
*before* the `;`/`&`/`|` split, then the tracked working-tree is scanned. That flag detection is
best-effort over the unparsed string: it matches `a` before any value-taking short option, so
`-ma` (a message) and `--allow-empty` are correctly ignored, while an unquoted `-a` token inside
a commit message would over-block — the safe failure.

**Bottom line:** treat the gate as a hygiene backstop, not complete secret protection — real
coverage is `.gitignore` discipline + a content scanner (gitleaks/trufflehog) + not tracking
secrets in the first place.

During an active Kimiflow run, the owning host/session has a smaller fail-closed surface: only
`git commit --only -m <message> -- <explicit named paths>` is accepted. The gate parses that
shape with `shlex`, rejects wrappers, implicit pathspecs, traversal and bulk/pathless forms, and
adds every explicit path to the filename scan. No-run, terminal-run and foreign-session behavior
remains compatibility-preserving.
