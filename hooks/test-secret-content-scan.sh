#!/usr/bin/env bash
# kimiflow — unit tests for secret-content-scan.sh (the advisory content scanner). Black-box:
# stages real content in a throwaway repo with a MOCK scanner on a controlled PATH whose exit
# code we control (the real gitleaks/trufflehog are NOT needed, and a real one installed on the
# dev box must not leak into the "no scanner" case — hence a hermetic PATH with only git).
# No framework. Run: bash hooks/test-secret-content-scan.sh
set -u
SCANNER="$(cd "$(dirname "$0")" && pwd)/secret-content-scan.sh"
REALBASH="$(command -v bash)"
WORK="$(mktemp -d)"; REPO="$WORK/repo"; BIN="$WORK/bin"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$BIN"
GIT_BIN="/usr/bin/git"
[ -x "$GIT_BIN" ] || GIT_BIN="$(command -v git)"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$GIT_BIN" > "$BIN/git"; chmod +x "$BIN/git" # keep scanners off PATH
ln -s "$REALBASH" "$BIN/bash"              # fallback for env-bash git wrappers
command -v dirname >/dev/null 2>&1 && ln -s "$(command -v dirname)" "$BIN/dirname"

FAILS=0
pass(){ printf 'PASS: %s\n' "$1"; }
fail(){ printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
reset_repo(){ rm -rf "$REPO"; git init -q "$REPO"; git -C "$REPO" config user.email t@e.com; git -C "$REPO" config user.name t; }
stage(){ mkdir -p "$REPO/$(dirname "$1")"; printf '%s' "$2" > "$REPO/$1"; git -C "$REPO" add -A >/dev/null 2>&1; }
mock(){ printf '#!/bin/sh\nexit %s\n' "$2" > "$BIN/$1"; chmod +x "$BIN/$1"; }
unmock(){ rm -f "$BIN/gitleaks" "$BIN/trufflehog"; }
run(){ ( cd "$REPO" && PATH="$BIN" "$REALBASH" "$SCANNER" 2>/dev/null ); }       # stdout only
runerr(){ ( cd "$REPO" && PATH="$BIN" "$REALBASH" "$SCANNER" 2>&1 1>/dev/null ); } # stderr only
has(){   printf '%s' "$1" | grep -qF "$2" && pass "$3" || fail "$3 (want '$2' in: ${1:-<empty>})"; }
hasnt(){ printf '%s' "$1" | grep -qF "$2" && fail "$3 (did NOT want '$2')" || pass "$3"; }

# 1) gitleaks finds something (exit 1) → FLAG, named gitleaks
reset_repo; stage "app.js" 'const k="sk-live-xxx";'; unmock; mock gitleaks 1
out="$(run)"; has "$out" "[FLAG]" "gitleaks_finding_flagged"; has "$out" "gitleaks" "gitleaks_named"
# 2) gitleaks clean (exit 0) → no FLAG
reset_repo; stage "app.js" 'const k = 1;'; unmock; mock gitleaks 0
hasnt "$(run)" "[FLAG]" "gitleaks_clean_no_flag"
# 3) trufflehog branch (gitleaks absent), finding (exit 183) → FLAG, named trufflehog
reset_repo; stage "app.js" 'token=abc'; unmock; mock trufflehog 183
has "$(run)" "trufflehog" "trufflehog_finding_flagged"
# 4) no scanner on PATH → SKIPPED note on STDERR, no FLAG on stdout
reset_repo; stage "app.js" 'x = 1'; unmock
hasnt "$(run)" "[FLAG]" "noscanner_no_flag_stdout"; has "$(runerr)" "SKIPPED" "noscanner_stderr_note"
# 5) nothing staged → early exit, no FLAG even with a "finding" scanner present
reset_repo; stage "a.js" "x"; git -C "$REPO" commit -q -m seed; unmock; mock gitleaks 1
hasnt "$(run)" "[FLAG]" "nothing_staged_no_flag"

echo "----"; if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
