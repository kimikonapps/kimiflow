# kimiflow Pre-Build-Gate + Phase-Tasklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a project-local, default-on pre-build summary gate (stop for user approval at the end of Phase 4) and a native phase task-list widget to the kimiflow skill — without changing the engine.

**Architecture:** One new tested shell helper `hooks/resolve-build-gate.sh` (project-only `on`/`off`, default `on`, mirrors `resolve-verbosity.sh`) provides the mechanical toggle. The rest is prompt edits in `SKILL.md` / `reference.md`: a new Phase-4 step that prints a structured summary and stops when the gate is `on`, and Phase-0 `TaskCreate`/`TaskUpdate` instructions for the phase task-list. A release task bumps to 0.1.2→0.1.3 and updates the CHANGELOG.

**Tech Stack:** POSIX-ish bash (the hooks), markdown (skill + reference), `jq` (manifests), GitHub Actions (CI). No new dependencies.

## Global Constraints

Copied verbatim from the spec + repo conventions. Every task implicitly includes these.

- **Engine unchanged:** gates, on-disk artifacts, evidence, subagents, thresholds, acceptance standards are identical. These two features touch only control-flow (a stop) and presentation (a widget).
- **build-gate is project-local ONLY** — `.kimiflow/build-gate`, never `~/.claude`. The self-contained rule (reference.md) forbids gate-related config living globally.
- **Default is `on`** — a missing or invalid `.kimiflow/build-gate` resolves to `on` (fail-safe toward more control).
- **No per-run flag, no global switch, no configurable summary sections** (explicit out-of-scope).
- **Commits:** stage explicit named paths only — never `git add -A`/`.`; no co-author/AI-attribution trailer.
- **Never mix a bare `git add` with a standalone `.` token in one shell line** (e.g. `jq -e .`) — the active `commit-secret-gate` greps the whole command and false-positives on ` . `. Keep git commands git-only.
- **Tests stay green:** `bash hooks/test-resolve-verbosity.sh` AND the new `bash hooks/test-resolve-build-gate.sh` must print `ALL GREEN`; `bash -n` clean on every `hooks/*.sh`; both JSON manifests valid.
- **Style:** match `hooks/resolve-verbosity.sh` exactly (header comment block, `set -u`, helper-function shape, stderr+exit-1 on write failure).

---

### Task 1: build-gate resolver + unit tests + CI gate

**Files:**
- Create: `hooks/resolve-build-gate.sh`
- Create: `hooks/test-resolve-build-gate.sh`
- Modify: `.github/workflows/ci.yml` (add a hard-gate step after the resolve-verbosity test step)

**Interfaces:**
- Produces: `resolve-build-gate.sh get` → prints `on` | `off` (project `.kimiflow/build-gate` at git root, else `on`). `resolve-build-gate.sh set <on|off>` → validates, `mkdir -p`s `.kimiflow/`, writes, verifies the write (stderr + `exit 1` on failure), prints the path. Invalid value → `exit 1`, no write.
- Consumes: nothing (Task 1 is the foundation).

- [ ] **Step 1: Write the failing test file**

Create `hooks/test-resolve-build-gate.sh`:

```bash
#!/usr/bin/env bash
# kimiflow — unit tests for resolve-build-gate.sh (the pre-build summary-gate toggle).
# Self-contained, no framework. Isolation: a NON-git temp project dir, so the real
# repo's .kimiflow/build-gate is never touched. Run: bash hooks/test-resolve-build-gate.sh
set -u

SCRIPT="$(cd "$(dirname "$0")" && pwd)/resolve-build-gate.sh"
WORK="$(mktemp -d)"
PROJ="$WORK/proj"          # non-git → gitroot falls back to pwd
trap 'rm -rf "$WORK"' EXIT

FAILS=0
pass() { printf 'PASS: %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }
reset() { rm -rf "$PROJ"; mkdir -p "$PROJ"; }
set_project() { mkdir -p "$PROJ/.kimiflow"; printf '%s\n' "$1" > "$PROJ/.kimiflow/build-gate"; }
run() { ( cd "$PROJ" && "$SCRIPT" "$@" ); }
assert_eq() { if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1' want '$2')"; fi; }

# default: nothing set → on
reset
assert_eq "$(run get)" "on" "test_default_on"
assert_eq "$(run)" "on" "test_default_on_bareword"

# project on/off honored
reset; set_project off
assert_eq "$(run get)" "off" "test_project_off"
reset; set_project on
assert_eq "$(run get)" "on" "test_project_on"

# garbage value → default on
reset; set_project "maybe"
assert_eq "$(run get)" "on" "test_garbage_defaults_on"

# set roundtrip
reset
out="$(run set off)"
if [ -f "$PROJ/.kimiflow/build-gate" ]; then pass "test_set_creates_file"; else fail "test_set_creates_file"; fi
assert_eq "$(run get)" "off" "test_set_off_roundtrip"
run set on >/dev/null
assert_eq "$(run get)" "on" "test_set_on_roundtrip"

# invalid set → exit 1, no file
reset
if run set nonsense >/dev/null 2>&1; then fail "test_set_invalid_rejected"; else pass "test_set_invalid_rejected"; fi
if [ -f "$PROJ/.kimiflow/build-gate" ]; then fail "test_set_invalid_nofile"; else pass "test_set_invalid_nofile"; fi

# get never persists
reset
run get >/dev/null
if [ -f "$PROJ/.kimiflow/build-gate" ]; then fail "test_get_no_persist"; else pass "test_get_no_persist"; fi

echo "----"
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILED"; exit 1; fi
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash hooks/test-resolve-build-gate.sh`
Expected: FAIL — every `run` errors because `hooks/resolve-build-gate.sh` does not exist yet (e.g. `FAIL: test_default_on (got '' want 'on')`), ends `N FAILED`, exit 1.

- [ ] **Step 3: Write the resolver**

Create `hooks/resolve-build-gate.sh`:

```bash
#!/usr/bin/env bash
# kimiflow — build-gate resolver (read + write). The single tested place for the
# pre-build summary-gate toggle. PROJECT-LOCAL ONLY (.kimiflow/build-gate at the git
# root) — the self-contained rule forbids gate-related config in ~/.claude. Default ON
# (fail-safe toward more control). This is CONTROL-FLOW only: it never affects gates,
# artifacts, evidence, subagents or thresholds — only whether the orchestrator stops
# for approval before Phase 5. Orchestrator-invoked (not a Claude Code event hook).
#
# Usage:
#   resolve-build-gate.sh [get]        -> echo on|off  (project file, else on)
#   resolve-build-gate.sh set <on|off> -> validate, mkdir -p, write, verify, echo path
set -u

VALID="on off"
is_valid() { case " $VALID " in *" ${1:-} "*) return 0 ;; *) return 1 ;; esac; }

project_file() {
  local root
  root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  printf '%s/.kimiflow/build-gate' "$root"
}

# Echo the first line of $1 (trimmed) iff it is a valid value; else return 1.
read_value() {
  local f="$1" line
  [ -f "$f" ] || return 1
  IFS= read -r line < "$f" 2>/dev/null || return 1
  line="${line#"${line%%[![:space:]]*}"}"   # ltrim
  line="${line%"${line##*[![:space:]]}"}"   # rtrim
  is_valid "$line" || return 1
  printf '%s' "$line"
}

mode="get"
case "${1:-}" in
  get|set) mode="$1"; shift ;;
esac

if [ "$mode" = "set" ]; then
  val="${1:-}"
  if ! is_valid "$val"; then
    printf 'resolve-build-gate: set: value must be on|off (got "%s")\n' "$val" >&2; exit 1
  fi
  target="$(project_file)"
  git rev-parse --show-toplevel >/dev/null 2>&1 \
    || printf 'resolve-build-gate: not in a git repo; writing to %s\n' "$target" >&2
  dir="${target%/*}"
  if ! mkdir -p "$dir" 2>/dev/null; then
    printf 'resolve-build-gate: set: cannot create %s\n' "$dir" >&2; exit 1
  fi
  if ! printf '%s\n' "$val" > "$target" 2>/dev/null; then
    printf 'resolve-build-gate: set: cannot write %s\n' "$target" >&2; exit 1
  fi
  if [ "$(read_value "$target" || true)" != "$val" ]; then
    printf 'resolve-build-gate: set: write verification failed for %s\n' "$target" >&2; exit 1
  fi
  printf '%s\n' "$target"
  exit 0
fi

# get: project value or default on
if value="$(read_value "$(project_file)")"; then
  printf '%s\n' "$value"
else
  printf 'on\n'
fi
exit 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash hooks/test-resolve-build-gate.sh`
Expected: every line `PASS:`, then `ALL GREEN`, exit 0. Also run `bash -n hooks/resolve-build-gate.sh hooks/test-resolve-build-gate.sh` → no output (clean).

- [ ] **Step 5: Add the CI hard-gate step**

In `.github/workflows/ci.yml`, find:

```yaml
      - name: Unit tests (resolve-verbosity) — hard gate
        run: bash hooks/test-resolve-verbosity.sh
```

Replace it with (adds the second test as a hard gate too):

```yaml
      - name: Unit tests (resolve-verbosity) — hard gate
        run: bash hooks/test-resolve-verbosity.sh

      - name: Unit tests (resolve-build-gate) — hard gate
        run: bash hooks/test-resolve-build-gate.sh
```

- [ ] **Step 6: Commit**

```bash
git add hooks/resolve-build-gate.sh hooks/test-resolve-build-gate.sh .github/workflows/ci.yml
git commit -m "feat: project-local build-gate resolver (on/off, default on) + tests + CI"
```

---

### Task 2: Pre-build summary gate (Feature A) — SKILL.md + reference.md

**Files:**
- Modify: `SKILL.md` (Phase 4 step 6 + new step 7; Modes bullet; `argument-hint`; terse-output budget exemption)
- Modify: `reference.md` (new "Pre-build summary gate" section; `--settings` bullet; self-contained-rule note)

**Interfaces:**
- Consumes: `resolve-build-gate.sh get` / `set on|off` from Task 1.
- Produces: nothing code-level (prompt behavior).

- [ ] **Step 1: SKILL.md — re-route Phase 4 step 6 into a new pre-build gate step**

In `SKILL.md` Phase 4, find:

```
6. **Gate open →** `--prepare`: STOP, update STATE (0–4 done), output `/kimiflow --resume <slug>`. Else → phase 5.
```

Replace with:

```
6. **Gate open →** `--prepare`: STOP, update STATE (0–4 done), output `/kimiflow --resume <slug>`. Else → step 7.
7. **Pre-build summary gate (project-local toggle, default on).** Let `BG` = `${CLAUDE_PLUGIN_ROOT:-$CLAUDE_SKILL_DIR}/hooks/resolve-build-gate.sh`. Run `BG get`. If it prints `off` → straight to phase 5. If `on` **and the session is interactive** → print the **pre-build summary** (a bounded terse-output exemption, like the commit-gate — structured, NOT a full-artifact dump): **Problem/Goal** (from `INTENT.md`/`PROBLEM.md`) · **Decisions** (from `RESEARCH.md`/the plan) · **Plan/Design** (from `PLAN.md`) · **Tests/Acceptance** (from `ACCEPTANCE.md`, incl. the `AC-N → test_name` links) · **Risks** (from `RESEARCH.md`/`DIAGNOSIS.md`) · **+ the artifact paths**. Then **STOP** and ask "Approve to build, or tell me what to change?". **Approve → phase 5.** **Change → back to phase 3** (revise plan → re-gate). **If `on` but headless / no interactive answer → do NOT build:** behave like `--prepare` (STOP, update STATE, output the `--resume` command). The toggle is set via `--settings`; it is control-flow only and never changes the engine.
```

- [ ] **Step 2: SKILL.md — note the summary as a terse-output exemption**

In the `Terse output (HARD RULE …)` core principle, find the Budget sub-bullet:

```
  - **Budget: ≤~6 lines of your own prose per phase**, outside the required artifact-summary / decisive-evidence. Exempt: the Phase-7 commit-gate `git diff --staged` and direct answers the user asked for. Gates, findings and evidence stay — the volume around them goes.
```

Replace `Exempt: the Phase-7 commit-gate \`git diff --staged\` and direct answers the user asked for.` with:

```
Exempt: the Phase-7 commit-gate `git diff --staged`, the Phase-4 pre-build summary, and direct answers the user asked for.
```

- [ ] **Step 3: SKILL.md — Modes bullet + argument-hint**

In the Modes section, after the `Display verbosity …` bullet, add a new bullet:

```
- **Pre-build summary gate (project-local, default on):** before building (end of Phase 4) kimiflow prints a structured summary (problem · decisions · plan · tests · risks + paths) and **waits for your OK**. Toggle per project via `--settings` (writes `.kimiflow/build-gate` `on`/`off`); never global (self-contained rule). → reference.md "Pre-build summary gate".
```

In the frontmatter `argument-hint`, change:

```
argument-hint: <feature-or-bug> [--fix] [--prepare] [--quiet|--verbose] [--set-verbosity <level>] [--settings]  ·  --resume <slug>
```

to (no new flag — `--settings` already covers it; only a doc note, so leave argument-hint UNCHANGED). **No edit in this step beyond the Modes bullet above** — confirm `argument-hint` stays identical (the toggle is settings-only, not a flag).

- [ ] **Step 4: reference.md — add the "Pre-build summary gate" section**

In `reference.md`, after the `## Display verbosity (all phases)` section (immediately before the next `---` separator that precedes `## Intent clarification`), insert:

```
---

## Pre-build summary gate (Phase 4 → Phase 5)

A user-approval checkpoint between the (internally vetted) plan and implementation. **Project-local, default on; control-flow only — it never changes the engine.**

- **Toggle:** `.kimiflow/build-gate` at the git root, one line `on` | `off`. Missing/invalid → `on` (fail-safe). Read/written ONLY by the unit-tested `hooks/resolve-build-gate.sh` (`get` / `set <on|off>`). **Project-local only** — the self-contained rule forbids gate-related config in `~/.claude`; there is no global build-gate and no per-run flag.
- **Fires** at the end of Phase 4 (after the plan-gate opens) **iff `get`==`on` ∧ the session is interactive**.
- **Summary content** (condensed from existing artifacts, not re-researched): Problem/Goal · Decisions · Plan/Design · Tests/Acceptance (`AC-N → test_name`) · Risks · + artifact paths. A **bounded terse-output exemption** like the commit-gate: structured summary + paths, never a full-artifact dump (invariant (b) still holds).
- **Outcomes:** approve → Phase 5; "change" → back to Phase 3 (revise → re-gate); `off` → straight to Phase 5; **headless / no answer → treat like `--prepare`** (STOP, update STATE, emit the `--resume` command — never build unapproved).
- **Set via `--settings`** (project scope only).
```

- [ ] **Step 5: reference.md — extend the `--settings` bullet + self-contained note**

In `reference.md`, find the `--settings` bullet under "Invocations":

```
- **`--settings`** — utility invocation: ask level **and** scope (project/global) → `set <scope> <level>`, report, **exit**.
```

Replace with:

```
- **`--settings`** — utility invocation: ask verbosity level **and** scope (project/global) → `set <scope> <level>`; AND ask the pre-build gate `on`/`off` (project scope only) → `resolve-build-gate.sh set <on|off>`; report the paths, **exit**.
```

In the `**Self-contained rule:**` paragraph, append this sentence at the end:

```
 (The pre-build summary gate's toggle lives **project-local** — `.kimiflow/build-gate` — for exactly this reason: it is gate-related, so it must never be read from `~/.claude`.)
```

- [ ] **Step 6: Verify the edits are present and consistent**

Run: `grep -c "resolve-build-gate" SKILL.md reference.md`
Expected: `SKILL.md` ≥ 1, `reference.md` ≥ 2.
Run: `grep -n "Pre-build summary gate" reference.md`
Expected: one match (the new section heading).
Run: `grep -n "Else → step 7" SKILL.md && grep -n "pre-build summary" SKILL.md`
Expected: both match (re-routing + budget exemption present).
Confirm `SKILL.md` frontmatter (lines 1–6) is unchanged: `git diff SKILL.md | grep -E '^\+.*argument-hint' || echo "argument-hint unchanged ✓"` → prints the "unchanged" line.

- [ ] **Step 7: Commit**

```bash
git add SKILL.md reference.md
git commit -m "feat: pre-build summary gate (Phase 4, project-local toggle, default on)"
```

---

### Task 3: Native phase task-list (Feature B) — SKILL.md + reference.md

**Files:**
- Modify: `SKILL.md` (Phase 0 — create the task-list; terse-output (e) precision)
- Modify: `reference.md` (short "Phase task list" note)

**Interfaces:**
- Consumes: nothing from Tasks 1–2.
- Produces: nothing code-level (prompt behavior).

- [ ] **Step 1: SKILL.md — Phase 0 creates the phase task-list**

In `SKILL.md` Phase 0, find step 3 (`**Resume check.**`). Immediately AFTER step 3, insert a new step (renumber the following steps 4→5, 5→6, 6→7 accordingly — Git check becomes 5, Scope-gate 6, Display verbosity 7):

```
4. **Phase task-list (glance widget).** Create one task per phase you will actually run (`TaskCreate`), scaled to scope (trivial → the few steps it runs; small/large → the phases of its loop). As you enter a phase set it `in_progress` (`TaskUpdate`) and `completed` when it closes. This is the at-a-glance progress view; it **complements** STATE.md (durable/resume) and the colored markers (per-phase event line) and replaces narrated status — it does not change the engine. Subagents keep their OWN internal task-lists; do not mix them into the phase list.
```

- [ ] **Step 2: SKILL.md — precise (e) so the widget is allowed**

In the `Terse output` principle, find sub-bullet (e):

```
  - **(e) No STATE narration in chat, no recap tables, no restating what a subagent will do or just did.**
```

Replace with:

```
  - **(e) No STATE *narration* in chat, no recap tables, no restating what a subagent will do or just did.** (The native phase task-list widget — Phase 0 — is structured, not narration, and IS the sanctioned glance view; use it instead of prose status.)
```

- [ ] **Step 3: reference.md — add a short "Phase task list" note**

In `reference.md`, immediately after the new `## Pre-build summary gate …` section (before its trailing `---`/next section), add:

```
---

## Phase task list (all phases)

A native task-list widget for glance-level progress. In Phase 0 create one task per phase actually run (`TaskCreate`), scaled to scope; mark `in_progress`/`completed` via `TaskUpdate` as phases open/close. It **complements**, never replaces: `STATE.md` is the durable, resume-able record (survives sessions; the widget is ephemeral per session) and the colored markers remain the per-phase event line. It satisfies the "reads at a glance" goal as structured output, not prose narration (see terse-output (e)). Subagents keep their own internal task-lists — keep those out of the orchestrator's phase list.
```

- [ ] **Step 4: Verify**

Run: `grep -n "Phase task-list (glance widget)" SKILL.md` → one match.
Run: `grep -n "Phase task list" reference.md` → one match (the heading).
Run: `grep -nE "^[0-9]+\. " SKILL.md | sed -n '1,8p'` → confirm Phase 0 now numbers 1..7 with no duplicate/gap.

- [ ] **Step 5: Commit**

```bash
git add SKILL.md reference.md
git commit -m "feat: native phase task-list widget in Phase 0 (complements STATE.md)"
```

---

### Task 4: Release 0.1.3 (CHANGELOG + version bump)

**Files:**
- Modify: `.claude-plugin/plugin.json` (`version`)
- Modify: `.claude-plugin/marketplace.json` (`plugins[0].version`)
- Modify: `CHANGELOG.md` (new `## 0.1.3` section)

**Interfaces:** none.

- [ ] **Step 1: Bump both version fields**

In `.claude-plugin/plugin.json` change `"version": "0.1.2",` → `"version": "0.1.3",`.
In `.claude-plugin/marketplace.json` change `      "version": "0.1.2",` → `      "version": "0.1.3",`.

- [ ] **Step 2: Add the CHANGELOG entry**

In `CHANGELOG.md`, after the line `Notable changes to **kimiflow**. Versions track \`.claude-plugin/plugin.json\`.` and before `## 0.1.2`, insert:

```
## 0.1.3

### Added
- **Pre-build summary gate** — at the end of Phase 4 (after the plan-gate opens), kimiflow
  prints a structured summary (problem/goal · decisions · plan · tests/acceptance · risks +
  artifact paths) and **waits for your OK** before implementing. Project-local toggle
  `.kimiflow/build-gate` (`on`/`off`, default `on`), set via `--settings`; never global
  (self-contained rule). Control-flow only — the engine is unchanged. Toggle resolved by the
  unit-tested `hooks/resolve-build-gate.sh`.
- **Native phase task-list** — Phase 0 creates a glance widget (`TaskCreate`/`TaskUpdate`) of
  the phases being run; complements `STATE.md` and the colored markers, replaces narrated status.

```

- [ ] **Step 3: Validate + commit**

```bash
jq empty .claude-plugin/plugin.json
jq empty .claude-plugin/marketplace.json
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json CHANGELOG.md
git commit -m "release: 0.1.3 (pre-build summary gate, native phase task-list)"
```

(Note: keep this `git add` line git-only — do not append a `jq -e .` check on the same line; the `commit-secret-gate` false-positives on a standalone ` . `.)

- [ ] **Step 4: Final green check**

```bash
bash hooks/test-resolve-verbosity.sh
bash hooks/test-resolve-build-gate.sh
```
Expected: both end `ALL GREEN`.

---

## Self-Review

**1. Spec coverage** — every spec section maps to a task:
- Feature A placement (end Phase 4, unify with `--prepare`) → Task 2 Step 1. ✓
- A toggle project-local default-on, no global, no flag → Task 1 (resolver) + Task 2 Steps 3–5 (settings/notes). ✓
- A summary content (Problem/Decisions/Plan/Tests/Risks + paths) → Task 2 Steps 1 & 4. ✓
- A terse-output exemption → Task 2 Step 2. ✓
- A headless → no build → Task 2 Step 1 + reference section Step 4. ✓
- B task-list, scope-scaled, complements STATE/markers, (e) precision → Task 3. ✓
- Self-contained-rule note → Task 2 Step 5. ✓
- Tests for resolver (default/project/garbage/roundtrip/invalid/no-persist) → Task 1 Step 1. ✓
- CHANGELOG + version → Task 4. ✓
- Out-of-scope items are simply absent (no flag, no global, no section config). ✓

**2. Placeholder scan** — no "TBD/TODO/handle edge cases"; all code shown in full; all insert blocks are literal. ✓

**3. Type/name consistency** — `resolve-build-gate.sh` verbs `get`/`set <on|off>`, file `.kimiflow/build-gate`, values `on`/`off`, var `BG`, default `on` — identical across Task 1 (code+tests), Task 2 (SKILL/reference), Task 4 (CHANGELOG). Phase-0 step renumber (1..7) called out in Task 3 Step 1 so Phase-4 references ("step 7") stay valid. ✓

**4. Known non-mechanical parts** — the gate STOP and the task-list are orchestrator-behavioral (no script enforces them, like the onboarding prompt before `onboard-check`); covered by imperative phrasing + a fail-safe `on` default. Verification of those is by reading + UAT, not a unit test. This is acknowledged, not a gap.
