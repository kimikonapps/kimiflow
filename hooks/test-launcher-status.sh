#!/usr/bin/env bash
# kimiflow — unit tests for launcher-status.sh.
# Isolation: temp git repo under mktemp; the real repo is never touched.
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/launcher-status.sh"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
INDEX="$REPO/.kimiflow/project/INDEX.json"
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
assert_jq() {
  local json="$1" expr="$2" name="$3"
  if printf '%s\n' "$json" | jq -e "$expr" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi
}

if ! command -v jq >/dev/null 2>&1; then
  echo "SKIP: jq not installed — launcher-status uses jq"; exit 0
fi

hash_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print "sha256:" $1}'
  else
    sha256sum "$1" | awk '{print "sha256:" $1}'
  fi
}

reset_repo() {
  rm -rf "$REPO"
  mkdir -p "$REPO/src" "$REPO/docs" "$REPO/.kimiflow/project"
  ( cd "$REPO" && git init -q && git config user.email "kimiflow@example.test" && git config user.name "kimiflow test" )
  printf '.kimiflow/\n' > "$REPO/.gitignore"
  printf 'one\n' > "$REPO/src/a.txt"
  printf '# Docs\n' > "$REPO/docs/guide.md"
  ( cd "$REPO" && git add .gitignore src/a.txt docs/guide.md && git commit -q -m init )
}

write_index() {
  local base="$1"
  local src_hash="$2"
  jq -n \
    --arg base "$base" \
    --arg src_hash "$src_hash" \
    '{
      schema_version: 1,
      language: "de",
      scan_depth: "standard",
      baseline_commit: $base,
      created_at: "2026-06-25T00:00:00Z",
      sections: {
        code: {
          files: ["src/a.txt"],
          prefixes: ["src/"],
          file_hashes: {"src/a.txt": $src_hash},
          last_scanned_commit: $base,
          status: "current"
        }
      },
      artifacts: {}
    }' > "$INDEX"
}

run_status() {
  "$SCRIPT" --root "$REPO"
}

reset_repo
rm -f "$INDEX"
out="$(run_status)"
assert_jq "$out" '.repo.present == true' "repo_present"
assert_jq "$out" '.project_map.present == false and .project_map.status == "missing"' "missing_map_reports_missing"

reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/src/a.txt")"
out="$(run_status)"
assert_jq "$out" '.project_map.present == true and .project_map.depth == "standard" and .project_map.status == "current"' "current_map_reports_current"
assert_jq "$out" '.repo.dirty == false' "ignored_kimiflow_does_not_dirty_repo"

printf 'two\n' > "$REPO/src/a.txt"
out="$(run_status)"
assert_jq "$out" '.project_map.status == "stale" and .repo.dirty == true' "stale_map_and_dirty_repo_reported"

reset_repo
BASE="$(cd "$REPO" && git rev-parse --short HEAD)"
write_index "$BASE" "$(hash_file "$REPO/src/a.txt")"
cat > "$REPO/.kimiflow/project/FINDINGS.md" <<'EOF'
# Findings

## Offen

### F-001
### F-002

## Erledigt

### F-000
EOF
cat > "$REPO/.kimiflow/project/IMPROVEMENTS.md" <<'EOF'
# Improvements

## Priorisierte Slices

### 1. First
### 2. Second
EOF
out="$(run_status)"
assert_jq "$out" '.findings.open == 2 and .improvements.open == 2' "findings_and_improvements_counted"

mkdir -p "$REPO/.kimiflow/parked"
cat > "$REPO/.kimiflow/parked/STATE.md" <<EOF
# STATE

- **Status:** backlog
- **Mode:** feature
- **Scope:** small
Plan commit: $BASE
Affected files:
- src/a.txt
Plan status: approved
EOF
out="$(run_status)"
assert_jq "$out" '.runs.backlog == 1 and (.runs.items[] | select(.slug == "parked" and .stale_risk == "low"))' "backlog_run_low_risk_when_clean"

printf 'changed\n' > "$REPO/src/a.txt"
out="$(run_status)"
assert_jq "$out" '.runs.backlog == 1 and (.runs.items[] | select(.slug == "parked" and .stale_risk == "needs-revalidation"))' "backlog_run_needs_revalidation_when_affected_file_changed"

reset_repo
printf '{bad json\n' > "$INDEX"
out="$(run_status)"
assert_jq "$out" '.project_map.present == true and .project_map.valid == false and .project_map.status == "unknown"' "invalid_map_reports_unknown"

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
