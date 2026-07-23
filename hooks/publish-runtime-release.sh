#!/usr/bin/env bash
# Publish the pinned runtime candidate as an immutable, independently verifiable release.
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
REPOSITORY="kimikonapps/kimiflow"
TAG=""
SOURCE_COMMIT=""
NOTES_FILE=""
OUTPUT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag) [ "$#" -ge 2 ] || { echo "publish-runtime-release: --tag requires a value" >&2; exit 2; }; TAG="$2"; shift 2 ;;
    --source-commit) [ "$#" -ge 2 ] || { echo "publish-runtime-release: --source-commit requires a value" >&2; exit 2; }; SOURCE_COMMIT="$2"; shift 2 ;;
    --notes-file) [ "$#" -ge 2 ] || { echo "publish-runtime-release: --notes-file requires a value" >&2; exit 2; }; NOTES_FILE="$2"; shift 2 ;;
    --output) [ "$#" -ge 2 ] || { echo "publish-runtime-release: --output requires a value" >&2; exit 2; }; OUTPUT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: hooks/publish-runtime-release.sh --tag TAG --source-commit SHA --notes-file FILE [--output DIR]"
      exit 0
      ;;
    *) echo "publish-runtime-release: unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$TAG" ] && [ -n "$SOURCE_COMMIT" ] && [ -n "$NOTES_FILE" ] || {
  echo "publish-runtime-release: --tag, --source-commit and --notes-file are required" >&2
  exit 2
}
[[ "$TAG" =~ ^kimiflow--v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$ ]] || {
  echo "publish-runtime-release: invalid Kimiflow tag" >&2
  exit 2
}
case "$SOURCE_COMMIT" in
  *[!0-9a-f]*|"") echo "publish-runtime-release: source commit must be lowercase hexadecimal" >&2; exit 2 ;;
esac
case "${#SOURCE_COMMIT}" in 40|64) ;; *) echo "publish-runtime-release: source commit must be a full object id" >&2; exit 2 ;; esac
[ -f "$NOTES_FILE" ] && [ ! -L "$NOTES_FILE" ] || {
  echo "publish-runtime-release: notes file is missing or unsafe" >&2
  exit 2
}

for command_name in git gh jq python3 tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "publish-runtime-release: $command_name is required" >&2
    exit 2
  }
done

[ "$(git -C "$ROOT" branch --show-current)" = "main" ] || {
  echo "publish-runtime-release: main branch required" >&2
  exit 1
}
[ -z "$(GIT_NO_REPLACE_OBJECTS=1 git -C "$ROOT" status --porcelain)" ] || {
  echo "publish-runtime-release: clean worktree and index required" >&2
  exit 1
}
HEAD_COMMIT="$(GIT_NO_REPLACE_OBJECTS=1 git -C "$ROOT" rev-parse HEAD^{commit})"
[ "$HEAD_COMMIT" = "$SOURCE_COMMIT" ] || {
  echo "publish-runtime-release: source commit is not current HEAD" >&2
  exit 1
}
LOCAL_TAG_COMMIT="$(GIT_NO_REPLACE_OBJECTS=1 git -C "$ROOT" rev-parse "refs/tags/$TAG^{commit}" 2>/dev/null || true)"
[ "$LOCAL_TAG_COMMIT" = "$SOURCE_COMMIT" ] || {
  echo "publish-runtime-release: local annotated tag does not resolve to source commit" >&2
  exit 1
}
LOCAL_TAG_TYPE="$(GIT_NO_REPLACE_OBJECTS=1 git -C "$ROOT" cat-file -t "refs/tags/$TAG" 2>/dev/null || true)"
[ "$LOCAL_TAG_TYPE" = "tag" ] || {
  echo "publish-runtime-release: release tag must be annotated" >&2
  exit 1
}

gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  "repos/$REPOSITORY/immutable-releases" --jq '.enabled == true' |
  grep -qx true || {
    echo "publish-runtime-release: repository immutable releases are not enabled" >&2
    exit 1
  }

remote_tag_commit() {
  gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    "repos/$REPOSITORY/commits/$TAG" --jq .sha
}

[ "$(remote_tag_commit)" = "$SOURCE_COMMIT" ] || {
  echo "publish-runtime-release: official remote tag does not resolve to source commit" >&2
  exit 1
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/export"
GIT_NO_REPLACE_OBJECTS=1 git -C "$ROOT" archive "$SOURCE_COMMIT" -- plugins/kimiflow |
  tar -x -C "$WORK/export"
CANDIDATE="$WORK/export/plugins/kimiflow"
[ -f "$CANDIDATE/RUNTIME-FINGERPRINT.json" ] || {
  echo "publish-runtime-release: pinned commit has no runtime candidate" >&2
  exit 1
}

if [ -z "$OUTPUT" ]; then
  OUTPUT="$ROOT/dist/runtime-release/$TAG"
fi
mkdir -p "$OUTPUT"
"$SCRIPT_DIR/build-runtime-release.sh" build \
  --candidate "$CANDIDATE" \
  --output "$OUTPUT" \
  --source-commit "$SOURCE_COMMIT" >/dev/null
MANIFEST="$OUTPUT/kimiflow-update-v1.json"
ARCHIVE="$OUTPUT/kimiflow-runtime-${TAG#kimiflow--v}.zip"
[ -f "$ARCHIVE" ] && [ ! -L "$ARCHIVE" ] || {
  echo "publish-runtime-release: expected runtime archive is missing or unsafe" >&2
  exit 1
}

RELEASE_LIST="$WORK/releases.json"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  --paginate "repos/$REPOSITORY/releases?per_page=100" >"$RELEASE_LIST"
MATCH_COUNT="$(jq -s --arg tag "$TAG" '[.[] | if type == "array" then .[] else . end | select(.tag_name == $tag)] | length' "$RELEASE_LIST")"
[ "$MATCH_COUNT" -le 1 ] || {
  echo "publish-runtime-release: duplicate release metadata for tag" >&2
  exit 1
}
if [ "$MATCH_COUNT" -eq 1 ]; then
  jq -se --arg tag "$TAG" \
    '[.[] | if type == "array" then .[] else . end | select(.tag_name == $tag)][0] | .draft == true' \
    "$RELEASE_LIST" >/dev/null || {
      echo "publish-runtime-release: tag already has a published release" >&2
      exit 1
    }
else
  gh release create "$TAG" \
    --repo "$REPOSITORY" \
    --draft \
    --verify-tag \
    --title "kimiflow ${TAG#kimiflow--v}" \
    --notes-file "$NOTES_FILE" >/dev/null
fi

gh release upload "$TAG" "$MANIFEST" "$ARCHIVE" \
  --repo "$REPOSITORY" \
  --clobber

DRAFT_JSON="$WORK/draft-release.json"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  --paginate "repos/$REPOSITORY/releases?per_page=100" >"$RELEASE_LIST"
MATCH_COUNT="$(jq -s --arg tag "$TAG" '[.[] | if type == "array" then .[] else . end | select(.tag_name == $tag)] | length' "$RELEASE_LIST")"
[ "$MATCH_COUNT" -eq 1 ] || {
  echo "publish-runtime-release: draft release metadata is missing or duplicated" >&2
  exit 1
}
jq -se --arg tag "$TAG" \
  '[.[] | if type == "array" then .[] else . end | select(.tag_name == $tag)][0]' \
  "$RELEASE_LIST" >"$DRAFT_JSON"
"$SCRIPT_DIR/build-runtime-release.sh" verify \
  --manifest "$MANIFEST" \
  --archive "$ARCHIVE" \
  --release-json "$DRAFT_JSON" \
  --stage draft >/dev/null

[ "$(remote_tag_commit)" = "$SOURCE_COMMIT" ] || {
  echo "publish-runtime-release: official remote tag changed before publication" >&2
  exit 1
}

gh release edit "$TAG" \
  --repo "$REPOSITORY" \
  --draft=false \
  --latest >/dev/null

"$SCRIPT_DIR/build-runtime-release.sh" verify-published \
  --manifest "$MANIFEST" \
  --archive "$ARCHIVE" \
  --tag "$TAG" >/dev/null

jq -n \
  --arg status published_verified \
  --arg tag "$TAG" \
  --arg source_commit "$SOURCE_COMMIT" \
  --arg manifest "$MANIFEST" \
  --arg archive "$ARCHIVE" \
  '{status:$status,tag:$tag,source_commit:$source_commit,manifest:$manifest,archive:$archive}'
