#!/usr/bin/env bash
# kimiflow — secret CONTENT scan (ADVISORY, never blocks). Invoked by kimiflow in Phase 7
# (NOT auto-registered as a hook). Complements commit-secret-gate, which is filename/path
# hygiene ONLY: this scans the STAGED CONTENT for in-source secrets via an optional
# open-source scanner (gitleaks, else trufflehog). Findings print as FLAG advisory lines to
# stdout; kimiflow routes them to .kimiflow/<slug>/ADVISORIES.md and forces human triage at
# the commit-gate (dismiss = false positive / allowlisted, or promote = a real finding).
#
# NON-GATING + graceful: no scanner on PATH → a SKIPPED note to STDERR, exit 0. It never
# blocks and never grants a false sense of coverage. The blocking path-hygiene gate
# (commit-secret-gate) and its fail-closed behavior are entirely untouched by this script.
#
# R2 invariant target: secret-content-scan.sh
# Exit-code contract (advisory): the chosen scanner returns non-zero when it finds something
# (gitleaks: leaks found; trufflehog --fail: results found). A scanner *error* is also non-zero,
# so a rare FLAG may be a tool error rather than a real secret — acceptable for a non-gating
# advisory (the human triages it; the scanner's own stderr shows the cause).
set -u

root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$root" ] || exit 0
cd "$root" 2>/dev/null || exit 0
git diff --cached --quiet 2>/dev/null && exit 0   # nothing staged → nothing to scan

flag() {  # $1 = tool name
  printf -- '- [FLAG] staged content — %s reported a potential in-source secret (or a scanner error); commit-secret-gate checks PATHS only — review the staged diff before commit. Details: re-run %s verbosely.\n' "$1" "$1"
}

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks protect --staged --no-banner --redact >/dev/null 2>&1 || flag gitleaks
  exit 0
fi
if command -v trufflehog >/dev/null 2>&1; then
  # Best-effort: trufflehog has no first-class "staged" mode; scan commits since HEAD
  # (broader than the staged diff — may also surface already-committed secrets). Advisory.
  trufflehog --no-update git "file://$root" --since-commit HEAD --fail >/dev/null 2>&1 || flag trufflehog
  exit 0
fi

printf 'kimiflow secret-content-scan: no gitleaks/trufflehog on PATH — in-source secret scan SKIPPED (advisory only; commit-secret-gate still enforces path hygiene). Install gitleaks for content scanning.\n' >&2
exit 0
