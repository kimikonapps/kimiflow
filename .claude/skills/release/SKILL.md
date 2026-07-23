---
name: release
description: Cut a new kimiflow release — promote CHANGELOG, bump manifests, regenerate the canonical runtime candidate, run all gates, commit, tag, push, and delegate immutable GitHub publication to the tracked publisher. Fully automatic but fail-closed. Local maintainer tool for the kimiflow repo.
argument-hint: "[<version>]   (default: next patch bump of .claude-plugin/plugin.json .version)"
---

# /release — kimiflow release

Fully automatic release of the kimiflow plugin, from version bump through the published GitHub release.

## Mode & safety (read first)

- **Fully automatic:** no approval stops in the happy path. This skill **pushes to `origin/main` and publishes a public GitHub release without asking** — that is the configured behaviour.
- **Fail-closed:** every gate below **aborts** (it does *not* prompt) when a precondition fails. A broken/duplicate release is worse than no release. On any abort: stop, show the failing command + the decisive output line(s), change nothing further.
- **Reply in the user's language. Terse output:** one line per step + the decisive evidence. Never paste full logs.
- **Source of truth for "which files must carry the version":** `hooks/release-consistency-check.sh`. If that script and this skill ever disagree, the script wins — re-read it.

## 0. Preconditions — hard gates (abort on any)

1. **Repo root:** `.claude-plugin/plugin.json` exists in the cwd. Else abort ("run from the kimiflow repo root").
2. **Tools:** `jq` and `gh` are on PATH. Else abort.
3. **Branch:** `git rev-parse --abbrev-ref HEAD` is `main`. Else abort.
4. **Clean tree:** `git status --porcelain` is empty. (`.kimiflow/`, `.claude/worktrees/`, and `.claude/skills/` are gitignored and won't appear.) If dirty → abort: "commit or stash first".
5. **In sync with origin:** `git fetch origin`, then verify local `main` is **not behind** `origin/main` (`git rev-list --left-right --count origin/main...HEAD` → left count 0). Else abort: "pull/rebase first".

## 1. Determine the version

- `OLD = jq -r .version .claude-plugin/plugin.json`.
- `NEW =` the `<version>` argument if provided, **else patch-bump** `OLD` (`x.y.z → x.y.(z+1)`). For a minor/major release the maintainer must pass `<version>` explicitly — automatic mode only patch-bumps.
- **Already-released guard (abort):** tag `kimiflow--v<NEW>` must not exist (`git rev-parse -q --verify "refs/tags/kimiflow--v<NEW>"` returns nothing) **and** `CHANGELOG.md` must not already contain a `## <NEW>` heading. If either exists → abort: "<NEW> is already released".

## 2. CHANGELOG — promote Unreleased → `## <NEW>`

The CHANGELOG keeps a `## Unreleased` section at the top (placeholder `_No unreleased changes._` when empty).

- If `## Unreleased` has **real content**: move that content under a new `## <NEW>` heading directly below Unreleased. If it lacks a one-line summary right under the heading, add one distilled from the entries.
- If `## Unreleased` is **the empty placeholder**: synthesise the entry from `git log "kimiflow--v<OLD>..HEAD" --pretty='- %s'`, grouped into `### Added` / `### Changed` / `### Fixed` by commit-subject prefix (`feat`→Added, `fix`→Fixed, `docs`/`refactor`/`chore`→Changed), with a one-line summary above them. Skip the `Release <OLD>` commit itself.
- Reset `## Unreleased` back to `_No unreleased changes._`.
- The new heading MUST be exactly `## <NEW>` (the consistency check anchors on this and rejects substring collisions like `## 0.1.490`).

## 3. Bump the version (mechanical)

Edit exactly these targets. Skip a JSON target **only** if it has no `.version`/value yet, mirroring `release-consistency-check.sh`:

- `.claude-plugin/plugin.json` → `.version`  (source of truth)
- `.claude-plugin/marketplace.json` → `.plugins[0].version`
- `.codex-plugin/plugin.json` → `.version`
- `.agents/plugins/marketplace.json` → `.version` **only if it already has a `version` field** (it currently does **not** → skip; the consistency check skips it too)
- `COMPATIBILITY.md` → replace the token `kimiflow **<OLD>**` with `kimiflow **<NEW>**`, and update the trailing `· <date>.` to today (`date +%F`). Do a **targeted** replace of the kimiflow token only — the line also holds Claude Code / Codex CLI versions in other `**…**` tokens; do not touch those.

For JSON use `jq` with a temp file (`jq '.version=$v' … > tmp && mv tmp file`) to preserve indentation. After editing, re-run `jq -e .` on each touched JSON to prove it still parses.

## 4. Regenerate and stage the canonical runtime candidate

Run `bash hooks/build-plugin-candidate.sh --write`.

Stage the named release-source paths changed in steps 2–3, then stage the complete generated candidate namespace
with `git add -A -- plugins/kimiflow`. The candidate is a committed release input, not disposable build output.
If a newly added release source is ignored locally (currently this tracked skill is excluded by a developer-only
`.git/info/exclude` rule), force-add that exact file once; never broaden the force-add.

Prove that every release input is now represented in the index:

- `git diff --quiet -- <named release-source paths>` must exit 0;
- `git diff --quiet -- plugins/kimiflow` must exit 0;
- inspect `git diff --cached --stat` and `git diff --cached -- <named paths>` before verification.

Foreign staged paths are a hard abort. Do not unstage, overwrite, or include them.

## 5. Verify — hard gates (abort on any failure, do NOT commit)

- **Consistency:** `bash hooks/release-consistency-check.sh` → exit 0 (`all consistent for version <NEW>`).
- **Runtime release:** build twice from `plugins/kimiflow` with the same full `HEAD` source commit, compare both
  ZIP and manifest byte-for-byte, then run offline artifact verification. The consistency helper performs this.
- **JSON valid:** `jq -e . > /dev/null` on `hooks/hooks.json`, `hooks.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`, `.agents/plugins/marketplace.json`.
- **Shell syntax:** `bash -n` on every `hooks/*.sh`.
- **Unit tests:** every discovered `hooks/test-*.sh` exits 0, using the CI discovery loop: skip only `hooks/test-gate.sh` and `hooks/test-weakening-scan.sh` because they are production hooks with dedicated `*-unit.sh` suites.
- **Smoke:** `bash hooks/smoke-install.sh` and `bash hooks/smoke-install-codex.sh` exit 0.

Any non-zero → stop, show the failing command + the decisive line(s), leave the working tree as-is for inspection. Do not proceed to commit.

## 6. Commit — named release inputs only

- The named release sources plus `plugins/kimiflow/**` were staged in step 4. `git add -A` is allowed only with
  the explicit `plugins/kimiflow` pathspec; repository-wide bulk staging remains forbidden.
- Re-check the staged inventory and confirm no foreign path entered it.
- `git commit -m "Release <NEW>"`. No co-author / AI-attribution trailer.
- (The repo's `commit-secret-gate` hook is active because `.kimiflow/` exists — that's expected; it must pass.)

Record `SOURCE_COMMIT="$(GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD^{commit})"` after the commit.

## 7. Tag

- `git tag -a "kimiflow--v<NEW>" -m "kimiflow <NEW>"` (annotated, on the release commit — matches the existing `kimiflow--v*` convention).

## 8. Push

- `git push origin main`
- `git push origin "kimiflow--v<NEW>"`

After both pushes, prove the official remote tag resolves to `SOURCE_COMMIT`. A mismatch is terminal.

## 9. Immutable GitHub release

- Notes = the `## <NEW>` CHANGELOG block body (everything under the heading, up to the next `## `; drop the heading line itself).
- Write those notes to a temporary file.
- Run `hooks/publish-runtime-release.sh --tag "kimiflow--v<NEW>" --source-commit "$SOURCE_COMMIT" --notes-file
  "<notes-file>"`.
- The publisher is the only official publication path. It requires repository immutable releases, exports the
  pinned commit with replacement refs disabled, rebuilds the candidate in isolation, creates/uploads a draft,
  verifies both assets, publishes, and performs fixed-origin post-publication verification.
- Any tag race, asset mismatch, mutable release, or failed final verification is terminal. Do not substitute an
  ad-hoc GitHub CLI publication command.

## 10. Report

One compact block: `OLD → NEW`, commit sha, tag, and the release URL (`gh release view "kimiflow--v<NEW>" --json url -q .url`). Done — no further narration.
