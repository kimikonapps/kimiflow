#!/usr/bin/env bash
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
REPO="$WORK/repo"
REMOTE="$WORK/remote.git"
BIN="$WORK/bin"
STATE="$WORK/state"
mkdir -p "$REPO/hooks" "$REPO/plugins/kimiflow" "$BIN" "$STATE"
cp "$ROOT/hooks/publish-runtime-release.sh" "$REPO/hooks/"
printf '{"schema_version":1}\n' >"$REPO/plugins/kimiflow/RUNTIME-FINGERPRINT.json"
printf 'runtime\n' >"$REPO/plugins/kimiflow/file.txt"
printf 'notes\n' >"$REPO/notes.md"
printf 'dist/\n' >"$REPO/.gitignore"

cat >"$REPO/hooks/build-runtime-release.sh" <<'EOF'
#!/usr/bin/env bash
set -eu
command_name="$1"
shift
case "$command_name" in
  build)
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --candidate) candidate="$2"; shift 2 ;;
        --output) output="$2"; shift 2 ;;
        --source-commit) source="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    grep -qx 'runtime' "$candidate/file.txt"
    mkdir -p "$output"
    printf 'archive\n' >"$output/kimiflow-runtime-1.2.3.zip"
    printf '{"source":{"commit":"%s"},"release":{"tag":"kimiflow--v1.2.3"}}\n' "$source" >"$output/kimiflow-update-v1.json"
    ;;
  verify) printf 'draft_verified\n' >>"$PUBLISH_TEST_LOG" ;;
  verify-published) printf 'published_verified\n' >>"$PUBLISH_TEST_LOG" ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$REPO/hooks/build-runtime-release.sh" "$REPO/hooks/publish-runtime-release.sh"

git init -q --bare "$REMOTE"
git -C "$REPO" init -q -b main
git -C "$REPO" config user.email kimiflow@example.test
git -C "$REPO" config user.name "Kimiflow Test"
git -C "$REPO" add .gitignore hooks plugins notes.md
git -C "$REPO" commit -q -m release
git -C "$REPO" tag -a kimiflow--v1.2.3 -m "kimiflow 1.2.3"
git -C "$REPO" remote add origin "$REMOTE"
git -C "$REPO" push -q origin main kimiflow--v1.2.3
SOURCE="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" switch -q -c replacement
printf 'replacement\n' >"$REPO/plugins/kimiflow/file.txt"
git -C "$REPO" add plugins/kimiflow/file.txt
git -C "$REPO" commit -q -m replacement
REPLACEMENT="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" switch -q main
git -C "$REPO" replace "$SOURCE" "$REPLACEMENT"

cat >"$BIN/gh" <<'EOF'
#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >>"$PUBLISH_TEST_LOG"
if [ "$1" = api ]; then
  case "$*" in
    *immutable-releases*) printf '%s\n' "${PUBLISH_TEST_IMMUTABLE:-true}" ;;
    *"/commits/kimiflow--v1.2.3"*) printf '%s\n' "${PUBLISH_TEST_REMOTE_SOURCE:-$PUBLISH_TEST_SOURCE}" ;;
    *"releases?per_page=100"*)
      count_file="$PUBLISH_TEST_GH_STATE/release-list-count"
      count=0
      [ ! -f "$count_file" ] || count="$(cat "$count_file")"
      count=$((count + 1))
      printf '%s\n' "$count" >"$count_file"
      if [ "$count" -le 2 ]; then
        printf '[]\n'
      else
        printf '[{"id":123,"tag_name":"kimiflow--v1.2.3","draft":true}]\n'
      fi
      ;;
    *) echo "unexpected gh api: $*" >&2; exit 1 ;;
  esac
elif [ "$1" = release ]; then
  exit 0
else
  echo "unexpected gh command: $*" >&2
  exit 1
fi
EOF
chmod +x "$BIN/gh"

LOG="$STATE/log"
PATH="$BIN:$PATH" PUBLISH_TEST_LOG="$LOG" PUBLISH_TEST_SOURCE="$SOURCE" PUBLISH_TEST_GH_STATE="$STATE" \
  "$REPO/hooks/publish-runtime-release.sh" \
  --tag kimiflow--v1.2.3 \
  --source-commit "$SOURCE" \
  --notes-file "$REPO/notes.md" \
  --output "$REPO/dist" >/dev/null

grep -q '^draft_verified$' "$LOG"
grep -q '^published_verified$' "$LOG"
[ "$(cat "$STATE/release-list-count")" -eq 3 ]
draft_line="$(grep -n 'release create' "$LOG" | cut -d: -f1)"
upload_line="$(grep -n 'release upload' "$LOG" | cut -d: -f1)"
verify_line="$(grep -n '^draft_verified$' "$LOG" | cut -d: -f1)"
publish_line="$(grep -n 'release edit' "$LOG" | cut -d: -f1)"
final_line="$(grep -n '^published_verified$' "$LOG" | cut -d: -f1)"
[ "$draft_line" -lt "$upload_line" ]
[ "$upload_line" -lt "$verify_line" ]
[ "$verify_line" -lt "$publish_line" ]
[ "$publish_line" -lt "$final_line" ]

: >"$LOG"
if PATH="$BIN:$PATH" PUBLISH_TEST_LOG="$LOG" PUBLISH_TEST_SOURCE="$SOURCE" PUBLISH_TEST_GH_STATE="$STATE" \
  PUBLISH_TEST_IMMUTABLE=false \
  "$REPO/hooks/publish-runtime-release.sh" \
    --tag kimiflow--v1.2.3 \
    --source-commit "$SOURCE" \
    --notes-file "$REPO/notes.md" \
    --output "$WORK/immutable-reject" >/dev/null 2>&1; then
  echo "publisher accepted a mutable repository" >&2
  exit 1
fi
! grep -q 'release create' "$LOG"

: >"$LOG"
if PATH="$BIN:$PATH" PUBLISH_TEST_LOG="$LOG" PUBLISH_TEST_SOURCE="$SOURCE" PUBLISH_TEST_GH_STATE="$STATE" \
  PUBLISH_TEST_REMOTE_SOURCE="$(printf '%040d' 0 | tr 0 b)" \
  "$REPO/hooks/publish-runtime-release.sh" \
    --tag kimiflow--v1.2.3 \
    --source-commit "$SOURCE" \
    --notes-file "$REPO/notes.md" \
    --output "$WORK/tag-reject" >/dev/null 2>&1; then
  echo "publisher accepted a mismatched official tag" >&2
  exit 1
fi
! grep -q 'release create' "$LOG"

if grep -Eq 'gh release (create|upload|edit)' "$ROOT/.claude/skills/release/SKILL.md"; then
  echo "release skill bypasses the tracked publisher" >&2
  exit 1
fi
git -C "$ROOT" ls-files --error-unmatch .claude/skills/release/SKILL.md >/dev/null
build_line="$(grep -nF 'Run `bash hooks/build-plugin-candidate.sh --write`.' "$ROOT/.claude/skills/release/SKILL.md" | cut -d: -f1)"
stage_line="$(grep -nF 'git add -A -- plugins/kimiflow' "$ROOT/.claude/skills/release/SKILL.md" | head -1 | cut -d: -f1)"
gate_line="$(grep -nF '**Consistency:** `bash hooks/release-consistency-check.sh`' "$ROOT/.claude/skills/release/SKILL.md" | cut -d: -f1)"
commit_line="$(grep -nF '`git commit -m "Release <NEW>"`' "$ROOT/.claude/skills/release/SKILL.md" | cut -d: -f1)"
tag_line="$(grep -nF '`git tag -a "kimiflow--v<NEW>"' "$ROOT/.claude/skills/release/SKILL.md" | cut -d: -f1)"
push_line="$(grep -nF '`git push origin main`' "$ROOT/.claude/skills/release/SKILL.md" | cut -d: -f1)"
publisher_line="$(grep -nF 'Run `hooks/publish-runtime-release.sh --tag' "$ROOT/.claude/skills/release/SKILL.md" | cut -d: -f1)"
[ "$build_line" -lt "$stage_line" ]
[ "$stage_line" -lt "$gate_line" ]
[ "$gate_line" -lt "$commit_line" ]
[ "$commit_line" -lt "$tag_line" ]
[ "$tag_line" -lt "$push_line" ]
[ "$push_line" -lt "$publisher_line" ]

printf 'ok   immutable_draft_publish_order\n'
printf 'ok   transient_release_metadata_visibility_retried\n'
printf 'ok   mutable_repository_rejected_prepublication\n'
printf 'ok   remote_tag_mismatch_rejected_prepublication\n'
printf 'ok   replacement_refs_cannot_change_exported_candidate\n'
printf 'ok   release_skill_delegates_publication\n'
printf 'ok   test_publish_runtime_release_is_draft_first_and_fail_closed\n'
