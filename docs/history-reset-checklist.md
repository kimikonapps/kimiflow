# Public history reset checklist for 0.2

This runbook is for the planned **0.2 public-history reset**: GitHub should look as if kimiflow starts at
`0.2.0`, while the old `0.1.x` history is preserved privately.

Use this only for an intentional public-history reset. It is not a normal release path and should stay manual:
rewriting `main`, deleting old tags, and deleting old GitHub Releases are destructive public actions.

## Sources checked

- Git `checkout --orphan` creates a first commit with no parents and is intended for publishing a current tree
  without exposing prior history: <https://git-scm.com/docs/git-checkout>
- Git bundles are suitable for full repository backups: <https://git-scm.com/docs/git-bundle>
- GitHub Releases can be created/edited/deleted through `gh release`: <https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository>
- GitHub protected branches block force pushes by default unless explicitly allowed for selected actors:
  <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>
- GitHub's sensitive-data guidance warns that rewritten history can still exist in clones, forks, cached views,
  and pull requests; actual exposed secrets must be rotated and may require GitHub Support:
  <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository>

## Decision gate

- [ ] Confirm this is a **presentation/history reset**, not a secret-remediation incident.
- [ ] If any real secret, API key, token, private key, or credential was ever public: rotate/revoke it first.
- [ ] Confirm the old history should disappear from the normal GitHub repo view.
- [ ] Confirm whether old `0.1.x` GitHub Releases and tags should be deleted from GitHub.
- [ ] Announce a short freeze window: nobody pushes to `main` during the reset.
- [ ] Confirm admin rights for `kimikonapps/kimiflow` and authenticated `gh`.

## Final 0.2 tree preparation

The 0.2 tree must be exactly what should become the new public root commit.

- [ ] `git status --short --branch` is clean before starting metadata edits.
- [ ] Prepare 0.2 release notes in a temp file, for example `/tmp/kimiflow-0.2-notes.md`.
- [ ] Use the normal release helper locally only, so manifests/changelog/compatibility are consistent:

```bash
hooks/release.sh --version 0.2.0 --notes-file /tmp/kimiflow-0.2-notes.md --no-push --no-github-release --yes
```

- [ ] Delete the local tag created on the old history; the public `0.2.0` tag must point to the new root commit:

```bash
git tag -d kimiflow--v0.2.0
```

- [ ] Run final checks from this old-history source tree:

```bash
bash -n hooks/*.sh
bash hooks/smoke-install.sh
bash hooks/smoke-install-codex.sh
git diff --check
```

- [ ] Check that local/private/generated files are not tracked:

```bash
git ls-files | grep -E '(^|/)(\.kimiflow|\.env|\.envrc|.*\.pem|.*\.key|.*\.p12|.*\.pfx|.*\.asc|id_rsa|id_dsa|id_ecdsa|id_ed25519|\.npmrc|\.pypirc)' && echo "STOP: tracked sensitive/local path"
git status --ignored --short | grep -E '(^| )\.kimiflow/' || true
```

- [ ] Run any available content scanners:

```bash
git diff --cached --quiet || bash hooks/secret-content-scan.sh
command -v gitleaks >/dev/null 2>&1 && gitleaks detect --no-banner --redact || true
```

## Private backup

Keep a private backup before rewriting public history.

```bash
git fetch origin --tags
OLD_MAIN="$(git rev-parse origin/main)"
BACKUP_DIR="$HOME/private/kimiflow-history-backups"
mkdir -p "$BACKUP_DIR"
BUNDLE="$BACKUP_DIR/kimiflow-pre-0.2-$(date +%Y%m%d-%H%M%S).bundle"
git bundle create "$BUNDLE" --all
git bundle verify "$BUNDLE"
```

Optional stronger check:

```bash
tmp="$(mktemp -d)"
git clone "$BUNDLE" "$tmp/kimiflow-pre-0.2-restore-check"
git -C "$tmp/kimiflow-pre-0.2-restore-check" log --oneline --max-count=3
rm -rf "$tmp"
```

Also consider pushing the old history to a private archive repository before public reset.

## Build the new root commit in a temporary directory

This avoids mutating the old clone until the final force push.

```bash
SOURCE="$(pwd)"
RESET_DIR="$(mktemp -d)"
git archive --format=tar HEAD | tar -x -C "$RESET_DIR"
cd "$RESET_DIR"
git init -b main
git add -A
git commit -m "Initial release 0.2.0"
git tag -a kimiflow--v0.2.0 -m "Release 0.2.0"
git remote add origin git@github.com:kimikonapps/kimiflow.git
```

Inspect before publishing:

```bash
git log --oneline --decorate --max-count=5
git status --short --branch
git ls-files | grep -E '(^|/)(\.kimiflow|\.env|\.envrc|.*\.pem|.*\.key|.*\.p12|.*\.pfx|.*\.asc|id_rsa|id_dsa|id_ecdsa|id_ed25519|\.npmrc|\.pypirc)' && echo "STOP: tracked sensitive/local path"
bash hooks/smoke-install.sh
bash hooks/smoke-install-codex.sh
```

## GitHub branch protection

If `main` is protected, GitHub blocks force pushes by default. For the reset window:

- [ ] Temporarily allow force pushes only for the maintainer account/team doing the reset, or use the repository
  admin bypass if policy allows it.
- [ ] Keep all other protection rules as strict as possible.
- [ ] Re-enable normal protection immediately after the reset.

## Publish the new public history

Use an explicit lease pinned to the old remote `main` SHA from the source clone.

```bash
git push origin HEAD:main --force-with-lease=refs/heads/main:$OLD_MAIN
git push origin kimiflow--v0.2.0
```

Verify:

```bash
git ls-remote origin refs/heads/main refs/tags/kimiflow--v0.2.0
gh release create kimiflow--v0.2.0 --repo kimikonapps/kimiflow --title "kimiflow 0.2.0" --notes-file /tmp/kimiflow-0.2-notes.md --latest
gh release view kimiflow--v0.2.0 --repo kimikonapps/kimiflow
```

## Delete old public releases and tags

Only do this after the `0.2.0` release is visible and the backup has been verified.

Preview:

```bash
gh release list --repo kimikonapps/kimiflow --limit 200 | awk -F '\t' '$3 ~ /^kimiflow--v0\.1\./ {print $3}'
git ls-remote --tags origin 'kimiflow--v0.1.*'
```

Delete releases first, then tags, so GitHub does not show releases with invalid/missing tags:

```bash
gh release list --repo kimikonapps/kimiflow --limit 200 \
  | awk -F '\t' '$3 ~ /^kimiflow--v0\.1\./ {print $3}' \
  | while IFS= read -r tag; do
      gh release delete "$tag" --repo kimikonapps/kimiflow -y
    done

git ls-remote --tags origin 'kimiflow--v0.1.*' \
  | awk '{print $2}' \
  | sed 's#refs/tags/##; s#\\^{}##' \
  | sort -u \
  | while IFS= read -r tag; do
      git push origin --delete "$tag"
    done
```

## Post-reset verification

- [ ] GitHub repository commit graph shows the new root commit only for `main`.
- [ ] Latest release is `kimiflow 0.2.0`.
- [ ] Old `0.1.x` releases/tags are absent from the public Releases/Tags UI, if deletion was approved.
- [ ] `codex plugin marketplace upgrade kimiflow` and `claude plugin update kimiflow` can see the new release state.
- [ ] Fresh clone install smoke:

```bash
tmp="$(mktemp -d)"
git clone git@github.com:kimikonapps/kimiflow.git "$tmp/kimiflow"
cd "$tmp/kimiflow"
bash hooks/smoke-install.sh
bash hooks/smoke-install-codex.sh
```

- [ ] Re-enable branch protection.
- [ ] Keep the private bundle/archive; do not upload it to the public repository.

## Rollback window

Rollback is simplest before old releases/tags are deleted.

From the old source clone:

```bash
git push origin "$OLD_MAIN":refs/heads/main --force-with-lease
```

If old releases/tags were already deleted, restore them only from the private bundle/archive if truly needed.
Document the reason before doing that; otherwise the public project should continue from `0.2.0`.

## What this does not guarantee

- It does not erase existing third-party clones, forks, caches, screenshots, package mirrors, or local copies.
- It does not make leaked credentials safe. Rotate secrets and follow GitHub's sensitive-data process when needed.
- It does not preserve public PR/issue cross-links to old commits. Some old links may become unreachable or confusing.
