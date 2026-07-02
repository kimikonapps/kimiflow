#!/usr/bin/env bash
# R2 preservation-invariant check (plan tooling, not a runtime hook).
# Joins the immutable invariant corpus to the target map by id and greps the
# corpus strong_needle against the exact authoritative target path.
# Run: bash docs/superpowers/plans/2026-07-02-invariant-check.sh
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
CORPUS_REL="docs/superpowers/plans/2026-07-02-invariant-corpus.tsv"
TARGETS_REL="docs/superpowers/plans/2026-07-02-invariant-targets.tsv"
CORPUS_ARG=""
TARGETS_ARG=""

usage() {
  cat >&2 <<'EOF'
usage: invariant-check.sh [--root DIR] [--corpus PATH] [--targets PATH]

PATH values are resolved relative to --root unless absolute.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      ROOT="$(cd "$2" 2>/dev/null && pwd -P)" || { printf 'invalid --root: %s\n' "$2" >&2; exit 2; }
      shift 2
      ;;
    --corpus)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      CORPUS_ARG="$2"
      shift 2
      ;;
    --targets)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      TARGETS_ARG="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      printf 'unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

abs_path() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$ROOT" "$1" ;;
  esac
}

CORPUS="$(abs_path "${CORPUS_ARG:-$CORPUS_REL}")"
TARGETS="$(abs_path "${TARGETS_ARG:-$TARGETS_REL}")"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kimiflow-invariants.XXXXXX")" || exit 2
trap 'rm -rf "$WORK"' EXIT

FAILS=0
fail() {
  printf 'MISS %s\n' "$1"
  FAILS=$((FAILS + 1))
}

sha256_text() {
  if command -v shasum >/dev/null 2>&1; then
    printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
  else
    printf '%s' "$1" | sha256sum | awk '{print $1}'
  fi
}

is_test_only_target() {
  case "$1" in
    hooks/test-*|*/test-*|tests/*|*/tests/*) return 0 ;;
    *) return 1 ;;
  esac
}

validate_rel_path() {
  case "$1" in
    ""|/*|../*|*/../*|*/..) return 1 ;;
    *) return 0 ;;
  esac
}

parse_corpus() {
  awk -F '\t' '
    function err(msg) { printf "INVALID corpus:%s: %s\n", FNR, msg > "/dev/stderr"; bad = 1 }
    /^#/ || NF == 0 { next }
    NF != 5 { err("expected 5 tab-separated columns, got " NF); next }
    $1 == "" { err("empty id") }
    $2 == "" { err("empty source") }
    $3 == "" { err("empty strong_needle") }
    $4 == "" { err("empty target_constraint") }
    seen[$1]++ { err("duplicate id " $1) }
    $1 != "" && $3 != "" { print $1 "\t" $3 "\t" $4 "\t" $5 }
    END { exit bad ? 1 : 0 }
  ' "$CORPUS" > "$WORK/corpus.parsed"
}

parse_targets() {
  awk -F '\t' '
    function err(msg) { printf "INVALID targets:%s: %s\n", FNR, msg > "/dev/stderr"; bad = 1 }
    /^#/ || NF == 0 { next }
    NF != 4 { err("expected 4 tab-separated columns; target map must not define or override needles"); next }
    $1 == "" { err("empty id") }
    $2 == "" { err("empty authoritative_target") }
    $4 ~ /(^|[;[:space:]])(strong_needle|needle)=/ { err("target map notes must not define needles") }
    seen[$1]++ { err("duplicate id " $1) }
    {
      verify = $3
      if (verify == "") verify = "-"
      if ($1 != "" && $2 != "") print $1 "\t" $2 "\t" verify "\t" $4
    }
    END { exit bad ? 1 : 0 }
  ' "$TARGETS" > "$WORK/targets.parsed"
}

if [ ! -f "$CORPUS" ]; then
  fail "corpus file missing: $CORPUS"
fi
if [ ! -f "$TARGETS" ]; then
  fail "target map missing: $TARGETS"
fi

if [ "$FAILS" -eq 0 ]; then
  if ! parse_corpus; then
    FAILS=$((FAILS + 1))
  fi
  if ! parse_targets; then
    FAILS=$((FAILS + 1))
  fi
fi

if [ "$FAILS" -eq 0 ]; then
  cut -f1 "$WORK/corpus.parsed" | sort > "$WORK/corpus.ids"
  cut -f1 "$WORK/targets.parsed" | sort > "$WORK/target.ids"

  comm -23 "$WORK/corpus.ids" "$WORK/target.ids" > "$WORK/missing.ids"
  while IFS= read -r id; do
    [ -n "$id" ] && fail "target-map id missing: $id"
  done < "$WORK/missing.ids"

  comm -13 "$WORK/corpus.ids" "$WORK/target.ids" > "$WORK/extra.ids"
  while IFS= read -r id; do
    [ -n "$id" ] && fail "target-map id has no corpus row: $id"
  done < "$WORK/extra.ids"
fi

if [ "$FAILS" -eq 0 ]; then
  awk -F '\t' '
    NR == FNR { needle[$1] = $2; constraint[$1] = $3; corpus_notes[$1] = $4; next }
    { print $1 "\t" $2 "\t" $3 "\t" $4 "\t" needle[$1] "\t" constraint[$1] "\t" corpus_notes[$1] }
  ' "$WORK/corpus.parsed" "$WORK/targets.parsed" > "$WORK/joined.tsv"

  TAB="$(printf '\t')"
  while IFS="$TAB" read -r id target verify target_notes needle constraint corpus_notes; do
    expected_hash="$(printf '%s\n' "$target_notes" | sed -n 's/.*needle_sha256=\([0-9a-f][0-9a-f]*\).*/\1/p')"
    if [ -z "$expected_hash" ]; then
      fail "$id target map missing needle_sha256 lock"
    else
      actual_hash="$(sha256_text "$needle")"
      if [ "$actual_hash" != "$expected_hash" ]; then
        fail "$id corpus strong_needle hash mismatch"
      fi
    fi

    if ! validate_rel_path "$target"; then
      fail "$id unsafe authoritative target: $target"
      continue
    fi

    if is_test_only_target "$target" && [ "$constraint" != "literal-smoke-target" ]; then
      fail "$id test-only authoritative target rejected: $target"
      continue
    fi

    target_file="$(abs_path "$target")"
    if [ ! -f "$target_file" ]; then
      fail "$id missing target file: $target"
      continue
    fi

    if ! grep -qF -- "$needle" "$target_file"; then
      fail "$id missing needle in $target :: $needle"
    fi

    if [ "$verify" != "-" ]; then
      OLD_IFS="$IFS"
      IFS=","
      for verification_path in $verify; do
        IFS="$OLD_IFS"
        if ! validate_rel_path "$verification_path"; then
          fail "$id unsafe verification path: $verification_path"
        elif [ ! -e "$(abs_path "$verification_path")" ]; then
          fail "$id missing verification path: $verification_path"
        fi
        IFS=","
      done
      IFS="$OLD_IFS"
    fi
  done < "$WORK/joined.tsv"
fi

echo "----"
if [ "$FAILS" -eq 0 ]; then
  echo "INVARIANTS OK"
  exit 0
else
  echo "$FAILS FAILURES"
  exit 1
fi
