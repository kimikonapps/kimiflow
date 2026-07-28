#!/usr/bin/env bash
# kimiflow — black-box tests for the advisory wrapper and shared provider facade.
# No framework. Run: bash hooks/test-secret-content-scan.sh
set -u
SCANNER="$(cd "$(dirname "$0")" && pwd)/secret-content-scan.sh"
REALBASH="$(command -v bash)"
REALPYTHON="$(command -v python3)"
WORK="$(mktemp -d)"; REPO="$WORK/repo"; BIN="$WORK/bin"; LOG="$WORK/scanner.log"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$BIN"
GIT_BIN="/usr/bin/git"
[ -x "$GIT_BIN" ] || GIT_BIN="$(command -v git)"
printf '#!/bin/sh\nexec "%s" "$@"\n' "$GIT_BIN" > "$BIN/git"; chmod +x "$BIN/git" # keep scanners off PATH
ln -s "$REALBASH" "$BIN/bash"
ln -s "$REALPYTHON" "$BIN/python3"
command -v dirname >/dev/null 2>&1 && ln -s "$(command -v dirname)" "$BIN/dirname"

FAILS=0
pass(){ printf 'PASS: %s\n' "$1"; }
fail(){ printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
reset_repo(){ rm -rf "$REPO"; git init -q "$REPO"; git -C "$REPO" config user.email t@e.com; git -C "$REPO" config user.name t; }
stage(){ mkdir -p "$REPO/$(dirname "$1")"; printf '%s' "$2" > "$REPO/$1"; git -C "$REPO" add -A >/dev/null 2>&1; }
unmock(){ rm -f "$BIN/gitleaks" "$BIN/trufflehog"; }
mock_gitleaks(){
  scan_code="$1"
  scan_payload="$2"
  {
    printf '#!/bin/sh\n'
    printf 'printf "%%s\\n" "$*" >> "%s"\n' "$LOG"
    printf 'if [ "${1:-}" = "--version" ]; then printf "gitleaks version 8.30.1\\n"; exit 0; fi\n'
    printf "printf '%%s' '%s'\n" "$scan_payload"
    printf 'exit %s\n' "$scan_code"
  } > "$BIN/gitleaks"
  chmod +x "$BIN/gitleaks"
}
mock_trufflehog(){
  {
    printf '#!/bin/sh\n'
    printf 'printf "%%s\\n" "$*" >> "%s"\n' "$LOG"
    printf 'if [ "${1:-}" = "--version" ]; then printf "trufflehog 3.95.5\\n"; exit 0; fi\n'
    printf 'exit 183\n'
  } > "$BIN/trufflehog"
  chmod +x "$BIN/trufflehog"
}
run(){ ( cd "$REPO" && PATH="$BIN" "$REALBASH" "$SCANNER" 2>/dev/null ); }
runerr(){ ( cd "$REPO" && PATH="$BIN" "$REALBASH" "$SCANNER" 2>&1 1>/dev/null ); }
has(){   printf '%s' "$1" | grep -qF "$2" && pass "$3" || fail "$3 (want '$2' in: ${1:-<empty>})"; }
hasnt(){ printf '%s' "$1" | grep -qF "$2" && fail "$3 (did NOT want '$2')" || pass "$3"; }

# 1) current gitleaks finding → one redacted facade call and FLAG
reset_repo; stage "app.js" 'const k="CANARY-RAW-123";'; unmock; : > "$LOG"
mock_gitleaks 1 '[{"RuleID":"generic-api-key","Description":"Generic API Key","StartLine":2,"Fingerprint":"app.js:generic-api-key:1","Secret":"CANARY-RAW-123"}]'
out="$(run)"
has "$out" "[FLAG]" "gitleaks_finding_flagged"
has "$out" "gitleaks" "gitleaks_named"
hasnt "$out" "CANARY-RAW-123" "gitleaks_raw_secret_redacted"
[ "$(grep -vc -- '--version' "$LOG")" -eq 1 ] && pass "gitleaks_single_scan_invocation" || fail "gitleaks_single_scan_invocation"
has "$(tail -n 1 "$LOG")" "stdin" "gitleaks_current_stdin_argv"
hasnt "$(tail -n 1 "$LOG")" "protect" "gitleaks_legacy_protect_absent"

# 2) gitleaks clean → no FLAG
reset_repo; stage "app.js" 'const k = 1;'; unmock; mock_gitleaks 0 '[]'
hasnt "$(run)" "[FLAG]" "gitleaks_clean_no_flag"

# 3) TruffleHog is deliberately not a staged/diff fallback
reset_repo; stage "app.js" 'token=abc'; unmock; : > "$LOG"; mock_trufflehog
hasnt "$(run)" "[FLAG]" "trufflehog_not_diff_fallback"
[ ! -s "$LOG" ] && pass "trufflehog_not_invoked_for_staged" || fail "trufflehog_not_invoked_for_staged"

# 4) no scanner on PATH → SKIPPED note on STDERR, no FLAG on stdout
reset_repo; stage "app.js" 'x = 1'; unmock
hasnt "$(run)" "[FLAG]" "noscanner_no_flag_stdout"; has "$(runerr)" "SKIPPED" "noscanner_stderr_note"

# 5) nothing staged → no provider invocation
reset_repo; stage "a.js" "x"; git -C "$REPO" commit -q -m seed; unmock; : > "$LOG"; mock_gitleaks 1 '[]'
hasnt "$(run)" "[FLAG]" "nothing_staged_no_flag"
[ ! -s "$LOG" ] && pass "nothing_staged_no_provider" || fail "nothing_staged_no_provider"

echo "----"; if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
