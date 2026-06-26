#!/usr/bin/env bash
# kimiflow release helper. Prepares a version bump, runs release checks, commits,
# tags, pushes, and creates/updates the matching GitHub Release.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REMOTE="origin"
BRANCH="main"
NEXT="patch"
TARGET_VERSION=""
SUMMARY=""
NOTES_FILE=""
DRY_RUN=0
YES=0
PUSH=1
GITHUB_RELEASE=1
SKIP_CHECKS=0
QUICK_CHECKS=0

RELEASE_FILES="
.claude-plugin/plugin.json
.claude-plugin/marketplace.json
.codex-plugin/plugin.json
CHANGELOG.md
COMPATIBILITY.md
"

usage() {
  cat <<'USAGE'
Usage:
  hooks/release.sh [--next patch|minor|major] [--summary "short release theme"] --yes
  hooks/release.sh --version 0.2.0 --notes-file /tmp/release-notes.md --yes

Options:
  --version X.Y.Z          Release exact version.
  --next patch|minor|major
                           Bump from the current manifest version. Default: patch.
  --summary TEXT          Used to create a changelog section when Unreleased is empty.
  --notes-file PATH       Changelog body to insert when the target section is missing.
  --dry-run               Print the plan and run safety prechecks without editing files.
  --yes                   Do not prompt before mutating, pushing, and publishing.
  --no-push               Commit and tag locally only.
  --no-github-release     Do not create/update the GitHub Release.
  --skip-checks           Skip release checks. Use only for emergency metadata repairs.
  --quick-checks          Run syntax + smoke checks, not every hooks/test-*.sh file.
  --branch NAME           Expected release branch. Default: main.
  --remote NAME           Git remote. Default: origin.
  -h, --help              Show this help.

Notes:
  The helper requires a clean working tree before it edits release metadata.
  It stages only the known release metadata files and never runs git add -A.
USAGE
}

info() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'release.sh: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is required"
}

run() {
  info "+ $*"
  "$@"
}

run_shell() {
  info "+ $*"
  sh -c "$*"
}

validate_version() {
  printf '%s' "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9._-]+)?$' \
    || die "invalid version: $1"
}

current_version() {
  jq -r '.version' .claude-plugin/plugin.json
}

next_version() {
  version="$1"
  part="$2"
  case "$version" in
    *[!-0-9.]*|"") die "cannot auto-bump non-plain semver: $version; use --version" ;;
  esac
  IFS=. read -r major minor patch <<EOF
$version
EOF
  case "$part" in
    patch) patch=$((patch + 1)) ;;
    minor) minor=$((minor + 1)); patch=0 ;;
    major) major=$((major + 1)); minor=0; patch=0 ;;
    *) die "unknown --next value: $part" ;;
  esac
  printf '%s.%s.%s\n' "$major" "$minor" "$patch"
}

check_manifest_versions() {
  claude="$(jq -r '.version' .claude-plugin/plugin.json)"
  marketplace="$(jq -r '.plugins[0].version' .claude-plugin/marketplace.json)"
  codex="$(jq -r '.version' .codex-plugin/plugin.json)"
  [ "$claude" = "$marketplace" ] || die "version mismatch: claude=$claude marketplace=$marketplace"
  [ "$claude" = "$codex" ] || die "version mismatch: claude=$claude codex=$codex"
}

check_clean_worktree() {
  status="$(git status --porcelain)"
  [ -z "$status" ] || {
    printf '%s\n' "$status" >&2
    die "working tree is not clean; commit or stash current work before releasing"
  }
}

check_branch_and_remote() {
  branch="$(git branch --show-current)"
  [ "$branch" = "$BRANCH" ] || die "expected branch $BRANCH, got ${branch:-detached}"
  run git fetch "$REMOTE" --tags
  upstream="$REMOTE/$BRANCH"
  git rev-parse --verify "$upstream" >/dev/null 2>&1 || die "missing upstream $upstream"
  counts="$(git rev-list --left-right --count "$upstream"...HEAD)"
  behind="$(printf '%s' "$counts" | awk '{print $1}')"
  ahead="$(printf '%s' "$counts" | awk '{print $2}')"
  [ "$behind" = "0" ] || die "$BRANCH is behind $upstream by $behind commit(s); pull/rebase first"
  if [ "$ahead" != "0" ]; then
    warn "$BRANCH is ahead of $upstream by $ahead commit(s); release push will publish them too"
  fi
}

check_tag_available() {
  tag="$1"
  if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    die "local tag already exists: $tag"
  fi
  if git ls-remote --exit-code --tags "$REMOTE" "$tag" >/dev/null 2>&1; then
    die "remote tag already exists: $tag"
  fi
}

unreleased_body() {
  awk '
    $0 == "## Unreleased" {flag=1; next}
    flag && /^## / {exit}
    flag {print}
  ' CHANGELOG.md
}

body_is_empty_or_placeholder() {
  body="$(printf '%s\n' "$1" | sed '/^[[:space:]]*$/d')"
  [ -z "$body" ] || [ "$body" = "_No unreleased changes._" ]
}

changelog_has_section() {
  grep -qx "## $1" CHANGELOG.md
}

write_changelog_section() {
  version="$1"
  notes_tmp="$2"
  out="$(mktemp)"
  awk -v version="$version" -v notes_file="$notes_tmp" '
    BEGIN {
      while ((getline line < notes_file) > 0) {
        notes = notes line ORS
      }
      in_unreleased = 0
      inserted = 0
    }
    $0 == "## Unreleased" {
      print
      print ""
      print "_No unreleased changes._"
      in_unreleased = 1
      next
    }
    in_unreleased && /^## / {
      print ""
      print "## " version
      print ""
      printf "%s", notes
      if (notes !~ /\n$/) print ""
      print ""
      in_unreleased = 0
      inserted = 1
      print
      next
    }
    in_unreleased { next }
    { print }
    END {
      if (in_unreleased && !inserted) {
        print ""
        print "## " version
        print ""
        printf "%s", notes
      }
    }
  ' CHANGELOG.md > "$out"
  mv "$out" CHANGELOG.md
}

prepare_changelog() {
  version="$1"
  if changelog_has_section "$version"; then
    return 0
  fi

  notes_tmp="$(mktemp)"
  if [ -n "$NOTES_FILE" ]; then
    [ -f "$NOTES_FILE" ] || die "notes file not found: $NOTES_FILE"
    cp "$NOTES_FILE" "$notes_tmp"
  else
    unreleased="$(unreleased_body)"
    if ! body_is_empty_or_placeholder "$unreleased"; then
      printf '%s\n' "$unreleased" > "$notes_tmp"
    elif [ -n "$SUMMARY" ]; then
      printf 'Ship **%s**.\n' "$SUMMARY" > "$notes_tmp"
    else
      die "CHANGELOG.md has no ## $version section and Unreleased is empty; pass --summary or --notes-file"
    fi
  fi

  [ -s "$notes_tmp" ] || die "release notes are empty"
  write_changelog_section "$version" "$notes_tmp"
  rm -f "$notes_tmp"
}

extract_release_notes() {
  version="$1"
  out="$2"
  awk -v version="$version" '
    $0 == "## " version {flag=1}
    flag && /^## / && $0 != "## " version {exit}
    flag {print}
  ' CHANGELOG.md > "$out"
  [ -s "$out" ] || die "could not extract CHANGELOG.md section for $version"
}

update_json_file() {
  file="$1"
  filter="$2"
  tmp="$(mktemp)"
  jq --arg version "$TARGET_VERSION" "$filter" "$file" > "$tmp"
  mv "$tmp" "$file"
}

update_versions() {
  update_json_file .claude-plugin/plugin.json '.version = $version'
  update_json_file .claude-plugin/marketplace.json '.plugins[0].version = $version'
  update_json_file .codex-plugin/plugin.json '.version = $version'
}

host_version() {
  cmd="$1"
  fallback="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    "$cmd" --version 2>/dev/null | head -1 | sed -E 's/ \(Claude Code\)//; s/^codex-cli //'
  else
    printf '%s\n' "$fallback"
  fi
}

current_compat_value() {
  label="$1"
  sed -n "s/.*$label \\*\\*\\([^*]*\\)\\*\\*.*/\\1/p" COMPATIBILITY.md | head -1
}

update_compatibility_stamp() {
  claude_version="$(host_version claude "$(current_compat_value 'Claude Code')")"
  codex_version="$(host_version codex "$(current_compat_value 'Codex CLI')")"
  today="$(date +%F)"
  out="$(mktemp)"
  awk -v claude="$claude_version" -v codex="$codex_version" -v version="$TARGET_VERSION" -v today="$today" '
    /^\*\*Last verified against:/ {
      print "**Last verified against:** Claude Code **" claude "** · Codex CLI **" codex "** · kimiflow **" version "** · " today "."
      next
    }
    { print }
  ' COMPATIBILITY.md > "$out"
  mv "$out" COMPATIBILITY.md
}

run_release_checks() {
  if [ "$SKIP_CHECKS" -eq 1 ]; then
    warn "release checks skipped"
    return 0
  fi

  run_shell 'for f in hooks/*.sh; do bash -n "$f"; done'

  if [ "$QUICK_CHECKS" -eq 1 ]; then
    run bash hooks/smoke-install.sh
    run bash hooks/smoke-install-codex.sh
  else
    run_shell 'for f in hooks/test-*.sh; do bash "$f"; done'
    run bash hooks/smoke-install.sh
    run bash hooks/smoke-install-codex.sh
  fi

  run git diff --check
}

stage_release_files() {
  # shellcheck disable=SC2086
  run git add -- $RELEASE_FILES
}

run_staged_advisory_scans() {
  if [ "$SKIP_CHECKS" -eq 1 ]; then
    return 0
  fi
  run bash hooks/secret-content-scan.sh
  run bash hooks/test-weakening-scan.sh
}

confirm_release() {
  if [ "$YES" -eq 1 ]; then
    return 0
  fi
  if [ ! -t 0 ]; then
    die "refusing to release non-interactively without --yes"
  fi
  printf 'Release kimiflow %s on %s and publish to GitHub? [y/N] ' "$TARGET_VERSION" "$BRANCH"
  read -r answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "release cancelled" ;;
  esac
}

publish_github_release() {
  tag="$1"
  notes_file="$2"
  if gh release view "$tag" --repo kimikonapps/kimiflow >/dev/null 2>&1; then
    run gh release edit "$tag" --repo kimikonapps/kimiflow --title "kimiflow $TARGET_VERSION" --notes-file "$notes_file" --latest
  else
    run gh release create "$tag" --repo kimikonapps/kimiflow --title "kimiflow $TARGET_VERSION" --notes-file "$notes_file" --latest
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) TARGET_VERSION="${2:-}"; shift 2 ;;
    --next) NEXT="${2:-}"; shift 2 ;;
    --summary) SUMMARY="${2:-}"; shift 2 ;;
    --notes-file) NOTES_FILE="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) YES=1; shift ;;
    --no-push) PUSH=0; shift ;;
    --no-github-release) GITHUB_RELEASE=0; shift ;;
    --skip-checks) SKIP_CHECKS=1; shift ;;
    --quick-checks) QUICK_CHECKS=1; shift ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --remote) REMOTE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

need_cmd git
need_cmd jq
[ "$PUSH" -eq 1 ] || [ "$GITHUB_RELEASE" -eq 0 ] || die "--no-push requires --no-github-release"
if [ "$GITHUB_RELEASE" -eq 1 ]; then
  need_cmd gh
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated"
fi

check_manifest_versions
CURRENT_VERSION="$(current_version)"
if [ -z "$TARGET_VERSION" ]; then
  TARGET_VERSION="$(next_version "$CURRENT_VERSION" "$NEXT")"
fi
validate_version "$TARGET_VERSION"
TAG="kimiflow--v$TARGET_VERSION"

check_clean_worktree
check_branch_and_remote
check_tag_available "$TAG"

if [ "$DRY_RUN" -eq 1 ]; then
  info "dry run: current version $CURRENT_VERSION"
  info "dry run: target version  $TARGET_VERSION"
  info "dry run: tag             $TAG"
  info "dry run: branch/remote   $BRANCH / $REMOTE"
  if changelog_has_section "$TARGET_VERSION"; then
    info "dry run: CHANGELOG.md already has ## $TARGET_VERSION"
  elif [ -n "$NOTES_FILE" ]; then
    [ -s "$NOTES_FILE" ] || die "notes file is missing or empty: $NOTES_FILE"
    info "dry run: CHANGELOG.md section will be created from --notes-file"
  elif ! body_is_empty_or_placeholder "$(unreleased_body)"; then
    info "dry run: CHANGELOG.md section will be created from Unreleased"
  elif [ -n "$SUMMARY" ]; then
    info "dry run: CHANGELOG.md section will be created from --summary"
  else
    die "CHANGELOG.md has no ## $TARGET_VERSION section and Unreleased is empty; pass --summary or --notes-file"
  fi
  exit 0
fi

confirm_release

prepare_changelog "$TARGET_VERSION"
update_versions
update_compatibility_stamp
run_release_checks
stage_release_files
run_staged_advisory_scans

run git commit -m "Release $TARGET_VERSION"
run git tag -a "$TAG" -m "Release $TARGET_VERSION"

if [ "$PUSH" -eq 1 ]; then
  run git push "$REMOTE" "$BRANCH"
  run git push "$REMOTE" "$TAG"
fi

if [ "$GITHUB_RELEASE" -eq 1 ]; then
  notes_tmp="$(mktemp)"
  extract_release_notes "$TARGET_VERSION" "$notes_tmp"
  publish_github_release "$TAG" "$notes_tmp"
  rm -f "$notes_tmp"
fi

info "release complete: kimiflow $TARGET_VERSION ($TAG)"
