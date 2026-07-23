#!/usr/bin/env bash
# kimiflow — review-gate resolver (read-only). Single tested source of truth for the binary
# Phase-4/Phase-7 review gate. Reads a round's findings files, echoes a machine verdict, FAILS
# CLOSED on any incompleteness/malformation. Orchestrator-invoked (not a Claude Code event hook).
#
# LANGUAGE-AGNOSTIC: operates only on the findings abstraction `FINDING <SEVERITY> <ref> :: <reason>`
# — no source, no file-extension/keyword/per-language logic. The ONLY fixed marker is the keyword
# `FINDING <SEVERITY>` at column 0; <ref> and <reason> may be arbitrary UTF-8. Output is stable
# reason-codes (the orchestrator localizes for display).
#
# Usage: resolve-review-gate.sh <findings-dir> --round <N> --expect <lensA,lensB> [--gate plan|code] [--epoch-start 1] [--cap 3] [--finding-contract 1]
# Output (one TAB line, exit 0): <VERDICT>\t<open_count|->\t<reason_code>\t<detail>
#   VERDICT ∈ {OPEN,CLOSED}; reason_code ∈ {clean,open-findings,incomplete,malformed,unproven-resolution,root-class-repeated,oscillation,reappeared,cap-reached}
# R2 invariant targets: hooks/resolve-review-gate.sh; --round <N> --expect <lensCSV>; --expect code-verified
set -u
emit() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4:-}"; exit 0; }
LEGACY_FINDING_RE='^FINDING (BLOCKER|HIGH|MEDIUM|LOW) .+ :: .+$'
CONTRACT_FINDING_RE='^FINDING (BLOCKER|HIGH|MEDIUM|LOW) .+ :: .+ :: class=[a-z0-9][a-z0-9-]{0,63} :: verify=(command|verifier):[^[:cntrl:]]+ :: evidence=review-evidence/[A-Za-z0-9._/-]+@[a-f0-9]{64}$'
CONTRACT_RESOLVED_RE='^RESOLVED class=[a-z0-9][a-z0-9-]{0,63} :: verify=(command|verifier):[^[:cntrl:]]+ :: evidence=review-evidence/[A-Za-z0-9._/-]+@[a-f0-9]{64}$'

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    return 1
  fi
}

state_value() {
  local file="$1" wanted="$2"
  awk -v wanted="$wanted" '
    {
      line=$0
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      pos=index(line, ":")
      if (!pos) next
      label=substr(line, 1, pos-1)
      value=substr(line, pos+1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", label)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (tolower(label) == tolower(wanted)) { print value; exit }
    }
  ' "$file" 2>/dev/null
}

state_value_count() {
  local file="$1" wanted="$2"
  awk -v wanted="$wanted" '
    {
      line=$0
      gsub(/\r/, "", line)
      gsub(/\*\*/, "", line)
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      pos=index(line, ":")
      if (!pos) next
      label=substr(line, 1, pos-1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", label)
      if (tolower(label) == tolower(wanted)) count++
    }
    END { print count + 0 }
  ' "$file" 2>/dev/null
}

dir=""; round=""; expect=""; gate=""; cap=3; epoch_start=1; epoch_arg=false; finding_contract=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --round)       round="${2:-}";       shift 2 || shift ;;
    --expect)      expect="${2:-}";      shift 2 || shift ;;
    --gate)        gate="${2:-}";        shift 2 || shift ;;
    --cap)         cap="${2:-3}";         shift 2 || shift ;;
    --epoch-start) epoch_start="${2:-}"; epoch_arg=true; shift 2 || shift ;;
    --finding-contract) finding_contract="${2:-}"; shift 2 || shift ;;
    -*)            shift ;;
    *)             [ -z "$dir" ] && dir="$1"; shift ;;
  esac
done
case "$round"       in ''|*[!0-9]*) emit CLOSED - malformed "bad-or-missing --round" ;; esac
case "$cap"         in ''|*[!0-9]*) emit CLOSED - malformed "bad --cap" ;; esac
case "$epoch_start" in ''|*[!0-9]*) emit CLOSED - malformed "bad --epoch-start" ;; esac
[ -n "$dir" ]    || emit CLOSED - malformed "missing findings-dir"
[ -n "$expect" ] || emit CLOSED - malformed "missing --expect"
case "$gate" in ''|plan|code) ;; *) emit CLOSED - malformed "bad --gate" ;; esac
case "$finding_contract" in ''|1) ;; *) emit CLOSED - malformed "bad --finding-contract" ;; esac
# Normalize base-10 so a zero-padded round (e.g. 08) can't trip octal arithmetic later.
round=$((10#$round)); cap=$((10#$cap)); epoch_start=$((10#$epoch_start))
if [ "$epoch_arg" = true ]; then
  [ "$epoch_start" -ge 1 ] && [ "$epoch_start" -le "$round" ] && [ "$epoch_start" -le "$cap" ] \
    || emit CLOSED - malformed "invalid --epoch-start ${epoch_start} for round ${round}, cap ${cap}"
fi

run_dir="$(dirname "${dir%/}")"
state="$run_dir/STATE.md"
state_finding_contract_count="$(state_value_count "$state" "Convergence contract")"
state_finding_contract_count="${state_finding_contract_count:-0}"
state_finding_contract="$(state_value "$state" "Convergence contract" | awk '{print $1}')"
[ "$state_finding_contract_count" -le 1 ] \
  || emit CLOSED - malformed "duplicate STATE convergence contract"
if [ "$state_finding_contract_count" -eq 1 ]; then
  [ "$state_finding_contract" = "1" ] \
    || emit CLOSED - malformed "unsupported STATE convergence contract"
  [ "$finding_contract" = "1" ] \
    || emit CLOSED - malformed "finding contract missing for converged run"
elif [ -n "$finding_contract" ] && [ "$finding_contract" != "1" ]; then
  emit CLOSED - malformed "finding contract mismatch"
fi
contracted=false
[ "$finding_contract" = "1" ] && contracted=true

safe_evidence_path() {
  local rel="$1"
  case "$rel" in
    review-evidence/*) ;;
    *) return 1 ;;
  esac
  case "/$rel/" in
    *"/../"*|*"/./"*|*"//"*) return 1 ;;
  esac
  return 0
}

validate_evidence() {
  local class="$1" verify="$2" outcome="$3" spec="$4"
  local rel="${spec%@*}" digest="${spec##*@}" full size actual content prefix parent cursor part
  [ "$rel" != "$spec" ] || return 1
  safe_evidence_path "$rel" || return 1
  full="$run_dir/$rel"
  parent="${rel%/*}"
  cursor="$run_dir"
  OLDIFS="$IFS"; IFS='/'; set -- $parent; IFS="$OLDIFS"
  for part in "$@"; do
    cursor="$cursor/$part"
    [ -d "$cursor" ] && [ ! -L "$cursor" ] || return 1
  done
  [ -f "$full" ] && [ ! -L "$full" ] || return 1
  size="$(wc -c < "$full" 2>/dev/null | tr -d '[:space:]')"
  case "$size" in ''|*[!0-9]*) return 1 ;; esac
  [ "$size" -gt 0 ] && [ "$size" -le 8192 ] || return 1
  [ "$(awk 'END { print NR + 0 }' "$full" 2>/dev/null)" -eq 1 ] || return 1
  actual="$(sha256_file "$full")" || return 1
  [ "$actual" = "$digest" ] || return 1
  content="$(cat "$full")"
  prefix="REVIEW_EVIDENCE class=${class} :: verify=${verify} :: outcome=${outcome} :: "
  case "$content" in
    "$prefix"?*) ;;
    *) return 1 ;;
  esac
  return 0
}

parse_contract_line() {
  local line="$1" expected_outcome="$2"
  local evidence_spec before_evidence verify before_verify class
  evidence_spec="${line##* :: evidence=}"
  before_evidence="${line% :: evidence=*}"
  verify="${before_evidence##* :: verify=}"
  before_verify="${before_evidence% :: verify=*}"
  case "$line" in
    FINDING\ *) class="${before_verify##* :: class=}" ;;
    RESOLVED\ *) class="${before_verify#RESOLVED class=}" ;;
    *) return 1 ;;
  esac
  validate_evidence "$class" "$verify" "$expected_outcome" "$evidence_spec"
}

if [ "$epoch_arg" = true ] && [ -n "$gate" ]; then
  run_dir="$(dirname "${dir%/}")"
  recovery="$run_dir/RECOVERY.md"
  basis="$run_dir/PLAN.md"
  [ -s "$basis" ] || emit CLOSED - malformed "missing ${gate} strategy basis"
  basis_fingerprint="$(sha256_file "$basis")" \
    || emit CLOSED - malformed "sha256 unavailable"
  baseline_lines="$(grep -E "^<!-- kimiflow:strategy gate=${gate} epoch-start=1 fingerprint=[A-Fa-f0-9]{64} -->$" "$recovery" 2>/dev/null || true)"
  [ "$(printf '%s\n' "$baseline_lines" | grep -c .)" -eq 1 ] \
    || emit CLOSED - malformed "missing or duplicate ${gate} strategy baseline"
  baseline="$baseline_lines"
  baseline_fingerprint="${baseline##* fingerprint=}"
  baseline_fingerprint="${baseline_fingerprint% -->}"
  baseline_fingerprint="$(printf '%s' "$baseline_fingerprint" | tr '[:upper:]' '[:lower:]')"
  if [ "$epoch_start" -eq 1 ]; then
    [ "$baseline_fingerprint" = "$basis_fingerprint" ] \
      || emit CLOSED - malformed "stale ${gate} strategy baseline"
  fi
fi

if [ "$epoch_arg" = true ] && [ "$epoch_start" -gt 1 ]; then
  [ -n "$gate" ] || emit CLOSED - malformed "later epoch requires --gate"
  state="$run_dir/STATE.md"
  [ -s "$state" ] || emit CLOSED - malformed "missing recovery STATE.md"
  [ -s "$recovery" ] || emit CLOSED - malformed "missing RECOVERY.md"
  marker_lines="$(grep -E "^<!-- kimiflow:recovery gate=${gate} source-round=[0-9]+ epoch-start=${epoch_start} cap=${cap} before=[A-Fa-f0-9]{64} after=[A-Fa-f0-9]{64} -->$" "$recovery" 2>/dev/null || true)"
  [ "$(printf '%s\n' "$marker_lines" | grep -c .)" -eq 1 ] \
    || emit CLOSED - malformed "missing or duplicate matching recovery receipt"
  marker="$marker_lines"

  marker_body="${marker#<!-- kimiflow:recovery }"
  marker_body="${marker_body% -->}"
  marker_source=""; marker_start=""; marker_cap=""; marker_before=""; marker_after=""
  OLDIFS="$IFS"; IFS=' '; set -- $marker_body; IFS="$OLDIFS"
  for field in "$@"; do
    case "$field" in
      source-round=*) marker_source="${field#source-round=}" ;;
      epoch-start=*) marker_start="${field#epoch-start=}" ;;
      cap=*) marker_cap="${field#cap=}" ;;
      before=*) marker_before="${field#before=}" ;;
      after=*) marker_after="${field#after=}" ;;
    esac
  done
  marker_source=$((10#$marker_source)); marker_start=$((10#$marker_start)); marker_cap=$((10#$marker_cap))
  [ "$marker_source" -eq $((epoch_start - 1)) ] \
    || emit CLOSED - malformed "non-contiguous recovery source round"
  [ "$marker_start" -eq "$epoch_start" ] && [ "$marker_cap" -eq "$cap" ] \
    || emit CLOSED - malformed "recovery receipt bounds mismatch"
  marker_before="$(printf '%s' "$marker_before" | tr '[:upper:]' '[:lower:]')"
  marker_after="$(printf '%s' "$marker_after" | tr '[:upper:]' '[:lower:]')"
  [ "$marker_before" != "$marker_after" ] || emit CLOSED - malformed "unchanged strategy fingerprint"
  [ "$marker_after" = "$basis_fingerprint" ] \
    || emit CLOSED - malformed "stale ${gate} strategy fingerprint"

  previous_marker="$(grep -E "^<!-- kimiflow:recovery gate=${gate} source-round=[0-9]+ epoch-start=[0-9]+ cap=[0-9]+ before=[A-Fa-f0-9]{64} after=[A-Fa-f0-9]{64} -->$" "$recovery" 2>/dev/null \
    | awk -v current_start="$epoch_start" -v current_cap="$cap" -v baseline="$baseline_fingerprint" '
        BEGIN { last_start=0; current_count=0; bad=0; expected=tolower(baseline) }
        {
          source=$4; sub(/^source-round=/, "", source); source += 0
          start=$5; sub(/^epoch-start=/, "", start); start += 0
          receipt_cap=$6; sub(/^cap=/, "", receipt_cap); receipt_cap += 0
          before=$7; sub(/^before=/, "", before); before=tolower(before)
          after=$8; sub(/^after=/, "", after); after=tolower(after)
          if (start <= 1 || start <= last_start || start > current_start) bad=1
          if (source != start - 1 || receipt_cap < start || before == after || before != expected) bad=1
          if (start < current_start) previous=$0
          if (start == current_start) {
            current_count++
            if (receipt_cap != current_cap) bad=1
          }
          expected=after
          last_start=start
        }
        END {
          if (bad || current_count != 1) print "__MALFORMED__"
          else print previous
        }
      ')"
  [ "$previous_marker" != "__MALFORMED__" ] \
    || emit CLOSED - malformed "non-monotonic or duplicate ${gate} recovery receipts"
  expected_before="$baseline_fingerprint"
  if [ -n "$previous_marker" ]; then
    expected_before="${previous_marker##* after=}"
    expected_before="${expected_before% -->}"
    expected_before="$(printf '%s' "$expected_before" | tr '[:upper:]' '[:lower:]')"
  fi
  [ "$marker_before" = "$expected_before" ] \
    || emit CLOSED - malformed "broken ${gate} strategy fingerprint chain"

  OLDIFS="$IFS"; IFS=','; set -- $expect; IFS="$OLDIFS"
  for lens in "$@"; do
    source_file="$dir/r${marker_source}-${lens}.md"
    [ -f "$source_file" ] \
      || emit CLOSED - malformed "missing recovery source r${marker_source}-${lens}.md"
    [ -s "$source_file" ] \
      || emit CLOSED - malformed "empty recovery source r${marker_source}-${lens}.md"
    if [ "$(grep -c '' "$source_file")" -eq 1 ] && [ "$(head -n1 "$source_file")" = "NONE" ]; then
      continue
    fi
    source_lineno=0
    while IFS= read -r source_line || [ -n "$source_line" ]; do
      source_lineno=$((source_lineno + 1))
      if [ "$contracted" = true ]; then
        if printf '%s\n' "$source_line" | grep -qE "$CONTRACT_FINDING_RE"; then
          parse_contract_line "$source_line" reproduced \
            || emit CLOSED - malformed "recovery evidence r${marker_source}-${lens}.md:${source_lineno}"
        elif printf '%s\n' "$source_line" | grep -qE "$CONTRACT_RESOLVED_RE"; then
          parse_contract_line "$source_line" not_reproduced \
            || emit CLOSED - malformed "recovery evidence r${marker_source}-${lens}.md:${source_lineno}"
        else
          emit CLOSED - malformed "recovery source r${marker_source}-${lens}.md:${source_lineno}"
        fi
      else
        printf '%s\n' "$source_line" | grep -qE "$LEGACY_FINDING_RE" \
          || emit CLOSED - malformed "recovery source r${marker_source}-${lens}.md:${source_lineno}"
      fi
    done < "$source_file"
  done

  state_gate="$(state_value "$state" "Review gate" | tr '[:upper:]' '[:lower:]')"
  state_start="$(state_value "$state" "Review epoch start")"
  state_cap="$(state_value "$state" "Review epoch cap")"
  state_fingerprint="$(state_value "$state" "Strategy fingerprint" | tr '[:upper:]' '[:lower:]')"
  state_recovery="$(state_value "$state" "Recovery" | tr '[:upper:]' '[:lower:]')"
  for state_label in "Review gate" "Review epoch start" "Review epoch cap" "Strategy fingerprint" "Recovery"; do
    [ "$(state_value_count "$state" "$state_label")" -eq 1 ] \
      || emit CLOSED - malformed "missing or duplicate STATE ${state_label}"
  done
  case "$state_start" in ''|*[!0-9]*) emit CLOSED - malformed "bad STATE review epoch start" ;; esac
  case "$state_cap" in ''|*[!0-9]*) emit CLOSED - malformed "bad STATE review epoch cap" ;; esac
  state_start=$((10#$state_start)); state_cap=$((10#$state_cap))
  [ "$state_gate" = "$gate" ] && [ "$state_start" -eq "$epoch_start" ] && [ "$state_cap" -eq "$cap" ] \
    && [ "$state_fingerprint" = "$marker_after" ] \
    && { [ "$state_recovery" = "active" ] || [ "$state_recovery" = "clean" ]; } \
    || emit CLOSED - malformed "recovery STATE mismatch"
fi

# The round ledger is global for the caller's expected lens set. A caller cannot bypass the
# revision budget by submitting a clean file after the cap; stop before reading/counting it.
[ "$round" -gt "$cap" ] && emit CLOSED - cap-reached "round ${round} > cap ${cap}"

# List existing findings files for the --expect lens set at a given round (newline-delimited).
# Phase 4 (lenses A/B) and Phase 7 (code-verified) share the findings dir with overlapping
# round numbers; every cross-round check MUST be scoped to --expect, never a bare r<N>-*.md glob.
expected_round_files() {
  local rnum="$1" lens f OLDIFS
  OLDIFS="$IFS"; IFS=','; set -- $expect; IFS="$OLDIFS"
  for lens in "$@"; do
    f="$dir/r${rnum}-${lens}.md"
    [ -f "$f" ] && printf '%s\n' "$f"
  done
}
# True (return 0) iff <id> appears as an open/any FINDING line in any --expect file of <round>.
id_in_round() {
  local target="$1" rnum="$2" f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    grep -qF "FINDING $target :: " "$f" 2>/dev/null && return 0
  done <<EOF
$(expected_round_files "$rnum")
EOF
  return 1
}

pair_value() {
  local pairs="$1" target="$2"
  printf '%s' "$pairs" | awk -F '\t' -v target="$target" '$1 == target { print substr($0, length($1) + 2); exit }'
}

pairs_without() {
  local pairs="$1" target="$2"
  printf '%s' "$pairs" | awk -F '\t' -v target="$target" '$1 != target && NF >= 2 { print $0 }'
}

open_count=0
cur_ids=""   # newline-list of "<SEV> <ref>" identities for open findings this round
cur_pairs="" # newline-list of "<class>\t<verify>" for contracted material findings
resolved_pairs="" # newline-list of "<class>\t<verify>" for contracted negative receipts
OLDIFS="$IFS"; IFS=','; set -- $expect; IFS="$OLDIFS"
for lens in "$@"; do
  f="$dir/r${round}-${lens}.md"
  [ -f "$f" ] || emit CLOSED - incomplete "missing r${round}-${lens}.md"
  [ -s "$f" ] || emit CLOSED - incomplete "empty r${round}-${lens}.md"
  if [ "$(grep -c '' "$f")" -eq 1 ] && [ "$(head -n1 "$f")" = "NONE" ]; then
    continue
  fi
  lineno=0
  while IFS= read -r ln || [ -n "$ln" ]; do
    lineno=$((lineno + 1))
    if [ "$contracted" = true ]; then
      if printf '%s\n' "$ln" | grep -qE "$CONTRACT_FINDING_RE"; then
        parse_contract_line "$ln" reproduced \
          || emit CLOSED - malformed "evidence r${round}-${lens}.md:${lineno}"
      elif printf '%s\n' "$ln" | grep -qE "$CONTRACT_RESOLVED_RE"; then
        parse_contract_line "$ln" not_reproduced \
          || emit CLOSED - malformed "evidence r${round}-${lens}.md:${lineno}"
        evidence_spec="${ln##* :: evidence=}"
        before_evidence="${ln% :: evidence=*}"
        verify="${before_evidence##* :: verify=}"
        before_verify="${before_evidence% :: verify=*}"
        class="${before_verify#RESOLVED class=}"
        [ -z "$(pair_value "$resolved_pairs" "$class")" ] \
          || emit CLOSED - malformed "duplicate resolution class ${class}"
        resolved_pairs="${resolved_pairs}${class}	${verify}
"
        continue
      else
        emit CLOSED - malformed "r${round}-${lens}.md:${lineno}"
      fi
    else
      printf '%s\n' "$ln" | grep -qE "$LEGACY_FINDING_RE" \
        || emit CLOSED - malformed "r${round}-${lens}.md:${lineno}"
    fi
    case "$ln" in
      'FINDING BLOCKER '*|'FINDING HIGH '*)
        open_count=$((open_count + 1))
        id="${ln#FINDING }"; id="${id%% :: *}"
        cur_ids="${cur_ids}${id}
"
        if [ "$contracted" = true ]; then
          evidence_spec="${ln##* :: evidence=}"
          before_evidence="${ln% :: evidence=*}"
          verify="${before_evidence##* :: verify=}"
          before_verify="${before_evidence% :: verify=*}"
          class="${before_verify##* :: class=}"
          [ -z "$(pair_value "$cur_pairs" "$class")" ] \
            || emit CLOSED - malformed "duplicate material class ${class}"
          cur_pairs="${cur_pairs}${class}	${verify}
"
        fi
        ;;
    esac
  done < "$f"
done

prev=$((round - 1))
prev_files="$(expected_round_files "$prev")"
prev_exists=false
[ -n "$prev_files" ] && prev_exists=true
prev_pairs=""
debt_pairs=""

if [ "$contracted" = true ] && [ "$prev" -ge 1 ]; then
  history_round=1
  while [ "$history_round" -le "$prev" ]; do
    history_round_pairs=""
    history_round_resolved_pairs=""
    OLDIFS="$IFS"; IFS=','; set -- $expect; IFS="$OLDIFS"
    for lens in "$@"; do
      pf="$dir/r${history_round}-${lens}.md"
      [ -f "$pf" ] || emit CLOSED - incomplete "missing r${history_round}-${lens}.md"
      [ -s "$pf" ] || emit CLOSED - incomplete "empty r${history_round}-${lens}.md"
      if [ "$(grep -c '' "$pf")" -eq 1 ] && [ "$(head -n1 "$pf")" = "NONE" ]; then
        continue
      fi
      prior_lineno=0
      while IFS= read -r prior_line || [ -n "$prior_line" ]; do
        prior_lineno=$((prior_lineno + 1))
        if printf '%s\n' "$prior_line" | grep -qE "$CONTRACT_FINDING_RE"; then
          parse_contract_line "$prior_line" reproduced \
            || emit CLOSED - malformed "prior evidence ${pf}:${prior_lineno}"
          case "$prior_line" in
            'FINDING BLOCKER '*|'FINDING HIGH '*)
              prior_before_evidence="${prior_line% :: evidence=*}"
              prior_verify="${prior_before_evidence##* :: verify=}"
              prior_before_verify="${prior_before_evidence% :: verify=*}"
              prior_class="${prior_before_verify##* :: class=}"
              [ -z "$(pair_value "$history_round_pairs" "$prior_class")" ] \
                || emit CLOSED - malformed "duplicate prior material class ${prior_class}"
              [ -z "$(pair_value "$history_round_resolved_pairs" "$prior_class")" ] \
                || emit CLOSED - malformed "prior class both reproduced and resolved ${prior_class}"
              debt_verify="$(pair_value "$debt_pairs" "$prior_class")"
              [ -z "$debt_verify" ] || [ "$debt_verify" = "$prior_verify" ] \
                || emit CLOSED - malformed "prior finding method mismatch ${prior_class}"
              history_round_pairs="${history_round_pairs}${prior_class}	${prior_verify}
"
              if [ -z "$debt_verify" ]; then
                debt_pairs="${debt_pairs}${prior_class}	${prior_verify}
"
              fi
              ;;
          esac
        elif printf '%s\n' "$prior_line" | grep -qE "$CONTRACT_RESOLVED_RE"; then
          parse_contract_line "$prior_line" not_reproduced \
            || emit CLOSED - malformed "prior evidence ${pf}:${prior_lineno}"
          prior_before_evidence="${prior_line% :: evidence=*}"
          prior_verify="${prior_before_evidence##* :: verify=}"
          prior_before_verify="${prior_before_evidence% :: verify=*}"
          prior_class="${prior_before_verify#RESOLVED class=}"
          [ -z "$(pair_value "$history_round_pairs" "$prior_class")" ] \
            || emit CLOSED - malformed "prior class both reproduced and resolved ${prior_class}"
          [ -z "$(pair_value "$history_round_resolved_pairs" "$prior_class")" ] \
            || emit CLOSED - malformed "duplicate prior resolution class ${prior_class}"
          debt_verify="$(pair_value "$debt_pairs" "$prior_class")"
          [ -n "$debt_verify" ] \
            || emit CLOSED - malformed "unexpected prior resolution class ${prior_class}"
          [ "$debt_verify" = "$prior_verify" ] \
            || emit CLOSED - malformed "prior resolution method mismatch ${prior_class}"
          debt_pairs="$(pairs_without "$debt_pairs" "$prior_class")"
          [ -z "$debt_pairs" ] || debt_pairs="${debt_pairs}
"
          history_round_resolved_pairs="${history_round_resolved_pairs}${prior_class}	${prior_verify}
"
        else
          emit CLOSED - malformed "prior findings ${pf}:${prior_lineno}"
        fi
      done < "$pf"
    done
    if [ "$history_round" -eq "$prev" ] && [ "$history_round" -ge "$epoch_start" ]; then
      prev_pairs="$history_round_pairs"
    fi
    history_round=$((history_round + 1))
  done
fi

if [ "$contracted" = true ]; then
  while IFS="$(printf '\t')" read -r resolved_class resolved_verify; do
    [ -n "$resolved_class" ] || continue
    prior_verify="$(pair_value "$debt_pairs" "$resolved_class")"
    [ -n "$prior_verify" ] \
      || emit CLOSED - malformed "unexpected resolution class ${resolved_class}"
    [ "$prior_verify" = "$resolved_verify" ] \
      || emit CLOSED - malformed "resolution method mismatch ${resolved_class}"
    [ -z "$(pair_value "$cur_pairs" "$resolved_class")" ] \
      || emit CLOSED - malformed "class both reproduced and resolved ${resolved_class}"
  done <<EOF
$resolved_pairs
EOF

  while IFS="$(printf '\t')" read -r prior_class prior_verify; do
    [ -n "$prior_class" ] || continue
    current_verify="$(pair_value "$cur_pairs" "$prior_class")"
    if [ -n "$current_verify" ]; then
      [ "$current_verify" = "$prior_verify" ] \
        || emit CLOSED "$open_count" root-class-repeated "${prior_class}:method-changed"
      emit CLOSED "$open_count" root-class-repeated "$prior_class"
    fi
    resolved_verify="$(pair_value "$resolved_pairs" "$prior_class")"
    [ "$resolved_verify" = "$prior_verify" ] \
      || emit CLOSED "$open_count" unproven-resolution "$prior_class"
  done <<EOF
$prev_pairs
EOF

  while IFS="$(printf '\t')" read -r prior_class prior_verify; do
    [ -n "$prior_class" ] || continue
    current_verify="$(pair_value "$cur_pairs" "$prior_class")"
    if [ -n "$current_verify" ]; then
      [ "$current_verify" = "$prior_verify" ] \
        || emit CLOSED - malformed "finding method mismatch ${prior_class}"
      continue
    fi
    resolved_verify="$(pair_value "$resolved_pairs" "$prior_class")"
    [ "$resolved_verify" = "$prior_verify" ] \
      || emit CLOSED "$open_count" unproven-resolution "$prior_class"
  done <<EOF
$debt_pairs
EOF
fi

[ "$open_count" -eq 0 ] && emit OPEN 0 clean

# ---- open_count > 0: anti-oscillation (cap → oscillation → reappeared → open-findings) ----
[ "$round" -ge "$cap" ] && emit CLOSED "$open_count" cap-reached "round ${round} >= cap ${cap}"

if [ "$prev" -ge "$epoch_start" ] && [ "$prev_exists" = true ]; then
  prev_open=0
  while IFS= read -r pf; do
    [ -n "$pf" ] || continue
    n="$(grep -cE '^FINDING (BLOCKER|HIGH) ' "$pf" 2>/dev/null || true)"
    prev_open=$((prev_open + n))
  done <<EOF
$prev_files
EOF
  [ "$open_count" -ge "$prev_open" ] && emit CLOSED "$open_count" oscillation "${prev_open}->${open_count}"
  if [ "$prev" -gt "$epoch_start" ]; then
    while IFS= read -r id; do
      [ -n "$id" ] || continue
      id_in_round "$id" "$prev" && continue
      k="$epoch_start"
      while [ "$k" -le $((prev - 1)) ]; do
        id_in_round "$id" "$k" && emit CLOSED "$open_count" reappeared "$id"
        k=$((k + 1))
      done
    done <<EOF
$cur_ids
EOF
  fi
fi

emit CLOSED "$open_count" open-findings
