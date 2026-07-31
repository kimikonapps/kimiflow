#!/usr/bin/env bash
# kimiflow — deterministic existing-Pi -> thin runner bridge -> Kimiflow E2E.
set -eu

# This fixture proves the process fallback. Do not inherit a maintainer shell's
# Herdr transport identity and accidentally route the fake Pi command through Herdr.
unset HERDR_ENV HERDR_PANE_ID HERDR_SOCKET_PATH HERDR_TAB_ID HERDR_WORKSPACE_ID

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
WORK="$(mktemp -d)"
REPO="$WORK/repo"
FAKE_PI="$WORK/pi"
PI_LOG="$WORK/pi.log"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$REPO"
git -C "$REPO" init -q
git -C "$REPO" config user.name "Kimiflow Pi E2E"
git -C "$REPO" config user.email "pi-e2e@example.test"
printf '.kimiflow/\n' > "$REPO/.gitignore"
printf 'fixture\n' > "$REPO/README.md"
git -C "$REPO" add .gitignore README.md
git -C "$REPO" commit -qm fixture

cat > "$FAKE_PI" <<'PY'
#!/usr/bin/env python3
import json
import hashlib
import os
import subprocess
import sys
import traceback


def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def run_checked(argv, env, stdin=None):
    result = subprocess.run(
        argv,
        env=env,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(argv), result.stdout))
    return result.stdout.strip()


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def write_implementation_receipt(run_dir, root, binding, worker_session):
    role = "implementation"
    phase = 5
    round_number = 1
    seat = "implementation-1"
    subagent_session = "pi-subagent-e2e-0001"
    receipt_id = "sha256:" + hashlib.sha256("\0".join((
        binding["worker_id"], worker_session, subagent_session,
        str(phase), role, str(round_number), seat,
    )).encode("utf-8")).hexdigest()
    directory = os.path.join(run_dir, "PI-SUBAGENTS")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    name = "%s-%s-%s-%s-%s.json" % (
        phase, round_number, role, seat, receipt_id[7:31],
    )
    value = {
        "schema_version": 1,
        "receipt_id": receipt_id,
        "root": root,
        "run": ".kimiflow/pi-bridge-e2e",
        "worker_id": binding["worker_id"],
        "worker_session_id": worker_session,
        "subagent_session_id": subagent_session,
        "phase": phase,
        "role": role,
        "round": round_number,
        "seat": seat,
        "slot": 1,
        "backend": "process",
        "status": "completed",
        "task_digest": "sha256:" + hashlib.sha256(
            b"build fixture-output.txt",
        ).hexdigest(),
        "result_digest": "sha256:" + hashlib.sha256(
            b"fixture-output.txt verified",
        ).hexdigest(),
    }
    target = os.path.join(directory, name)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, separators=(",", ":"))
        handle.write("\n")


if sys.argv[1:] == ["--version"]:
    print("0.82.1")
    raise SystemExit(0)

source = os.environ["PI_E2E_SOURCE"]
root = os.path.realpath(os.getcwd())
args = sys.argv[1:]
session = (
    args[args.index("--session") + 1]
    if "--session" in args
    else "pi-worker-e2e-0001"
)
resume = "--session" in args
run_rel = ".kimiflow/pi-bridge-e2e"
run_dir = os.path.join(root, *run_rel.split("/"))
active = os.path.join(source, "hooks", "active-run.sh")
env = dict(os.environ)
env.update({
    "KIMIFLOW_PLUGIN_ROOT": source,
    "KIMIFLOW_HOST": "pi",
    "KIMIFLOW_SESSION_HOST": "pi",
    "KIMIFLOW_SESSION_ID": session,
})
delimiter = "\n\nTransport request:\n"
if delimiter not in args[-1]:
    raise RuntimeError("Kimiflow transport wrapper is missing")
transport_prompt = args[-1].split(delimiter, 1)[1]

try:
    if "--prompt" in args or "--extension" not in args or "--no-extensions" not in args:
        raise RuntimeError("Pi prompt must be positional and the worker extension explicit")
    binding = json.loads(os.environ["KIMIFLOW_PI_BRIDGE_BINDING"])
    if (
        sorted(binding) != [
            "captain_session_id",
            "root",
            "schema_version",
            "worker_id",
        ]
        or binding["schema_version"] != 1
        or binding["root"] != root
        or binding["captain_session_id"] != "pi-primary-e2e"
        or not binding["worker_id"].startswith("worker-")
    ):
        raise RuntimeError("thin Pi bridge binding mismatch")

    emit({
        "type": "session",
        "version": 3,
        "id": session,
        "timestamp": "2026-07-29T10:00:00Z",
        "cwd": root,
    })
    emit({"type": "agent_start"})

    if not resume:
        state = """Flow schema: 5
Intent contract: 4
Conformance contract: 1
Conformance basis: pending
Convergence contract: 1
Architecture deliberation: off
Status: active
Mode: feature
Alias: build
Scope: small
Discovery required: yes
Build risk: none
Phase reads required: yes
Affected files:
- fixture-output.txt
Phase 0: done
Phase 1: in-progress
Phase 2: open
Phase 3: open
Phase 4: open
Phase 5: open
Phase 6: open
Phase 7: open
"""
        intent = """# Intent
<!-- kimiflow:intent-coverage contract=4 goal=user_explicit actor=user_confirmed behavior=user_explicit boundaries=user_confirmed success=user_explicit constraints=not_applicable unknown_material=0 question_rounds=1 technical_questions=0 critic=folded authority=explicit summary=present source=current-run entry=user_confirmed interaction=user_confirmed delegation=user_confirmed unchanged=user_confirmed done=user_confirmed -->
Product flow entry: The developer asks an already-running Pi session to use Kimiflow.
User interaction: The same Pi conversation remains available and relays the intake question.
Visible delegation outcome: The Kimiflow runner creates fixture-output.txt and reports completion.
Unchanged path: Direct Codex and Claude entrypoints remain unchanged.
Done scenario: Pi announces completion only after the terminal runner receipt.
Requirement R1: Build the confirmed fixture output through the thin Pi bridge.
"""
        intake = """<!-- kimiflow:intake contract=4 round=1 questions=1 selection=impact_uncertainty technical_questions=0 confirmation=concrete_product_flow -->
Confirm the concrete Pi-to-Kimiflow product flow.
Product flow entry: The developer asks an already-running Pi session to use Kimiflow.
User interaction: The same Pi conversation remains available and relays the intake question.
Visible delegation outcome: The Kimiflow runner creates fixture-output.txt and reports completion.
Unchanged path: Direct Codex and Claude entrypoints remain unchanged.
Done scenario: Pi announces completion only after the terminal runner receipt.
"""
        research = """# Research
<!-- kimiflow:discovery depth=pulse status=sufficient lanes=complete claims=none technical_gaps=0 user_decisions=0 scope_change=no -->
The deterministic fixture output is local to the existing repository.
"""
        plan = """# Plan
Affected files:
- fixture-output.txt

- Create and verify fixture-output.txt.

<!-- kimiflow:decision-contract contract=1 decisions=1 -->
Decision D1: Keep the Pi integration as a thin bridge over the existing runner.
Evidence D1: RESEARCH.md §Research
Invariant D1: The existing Pi session stays the conversation surface.
Paths D1: fixture-output.txt
AC D1: AC-1
Check D1: command :: test -f fixture-output.txt && grep -q built-by-thin-pi-bridge fixture-output.txt
Recheck D1: Re-run after Pi bridge or runner changes.

<!-- kimiflow:convergence contract=1 risk=routine slices=1 failures=0 -->
Slice S1: Build and verify the fixture output.
AC S1: AC-1
Paths S1: fixture-output.txt
Check S1: command :: test -f fixture-output.txt && grep -q built-by-thin-pi-bridge fixture-output.txt
Depends S1: none
"""
        acceptance = """# Acceptance
AC-1 -- When the Pi-backed Kimiflow run completes, fixture-output.txt shall contain `built-by-thin-pi-bridge`.
Example: existing repository -> fixture-output.txt.
Check: `test -f fixture-output.txt && grep -q built-by-thin-pi-bridge fixture-output.txt` (exit 0) -> AC-1
Requirement trace R1: AC-1
"""
        write(os.path.join(run_dir, "STATE.md"), state)
        write(os.path.join(run_dir, "INTENT.md"), intent)
        write(os.path.join(run_dir, "INTAKE.md"), intake)
        write(os.path.join(run_dir, "RESEARCH.md"), research)
        write(os.path.join(run_dir, "PLAN.md"), plan)
        write(os.path.join(run_dir, "ACCEPTANCE.md"), acceptance)
        run_checked([
            active, "start", "--root", root, "--run", run_rel,
            "--mode", "feature", "--scope", "small", "--host", "pi", "--write",
        ], env)
        for phase, phase_file in (
            (0, "phases/phase-0-setup.md"),
            (1, "phases/phase-1-clarify.md"),
        ):
            run_checked([
                active, "phase-read", "--root", root, "--run", run_rel,
                "--phase", str(phase), "--file", phase_file, "--write",
            ], env)
        run_checked([
            active, "await-user", "--root", root, "--run", run_rel,
            "--kind", "intake", "--round", "1",
            "--request", run_rel + "/INTAKE.md",
            "--reason", "Confirm the concrete Pi-to-Kimiflow product flow.",
            "--write",
        ], env)
    else:
        run_checked(
            [active, "prompt-context"],
            env,
            json.dumps({
                "cwd": root,
                "session_id": session,
                "prompt": transport_prompt,
            }),
        )
        clarify_record = run_checked([
            os.path.join(source, "hooks", "clarify-gate.sh"),
            run_dir, "--record-intent-lock",
        ], env)
        clarify = run_checked([
            os.path.join(source, "hooks", "clarify-gate.sh"), run_dir,
        ], env)
        for phase, phase_file in (
            (2, "phases/phase-2-understand.md"),
            (3, "phases/phase-3-plan.md"),
            (4, "phases/phase-4-review-approval.md"),
        ):
            run_checked([
                active, "phase-read", "--root", root, "--run", run_rel,
                "--phase", str(phase), "--file", phase_file, "--write",
            ], env)
        plan_check = run_checked([
            os.path.join(source, "hooks", "plan-blocker-gate.sh"), run_dir,
        ], env)
        build_check = run_checked([
            os.path.join(source, "hooks", "resolve-build-gate.sh"),
            "decide", "--state", os.path.join(run_dir, "STATE.md"),
            "--interactive", "no", "--risk", "none", "--alias", "build",
        ], env)
        write(
            os.path.join(run_dir, "PI-E2E-GATES.json"),
            json.dumps({
                "clarify_record": clarify_record,
                "clarify": clarify,
                "plan": plan_check,
                "build": build_check,
            }, sort_keys=True) + "\n",
        )
        write(
            os.path.join(root, "fixture-output.txt"),
            "built-by-thin-pi-bridge\n",
        )
        write_implementation_receipt(run_dir, root, binding, session)
        run_checked([
            "git", "-C", root, "add", "--", "fixture-output.txt",
        ], env)
        staged = subprocess.run(
            ["git", "-C", root, "diff", "--cached", "--quiet", "--"],
            env=env,
            check=False,
        )
        if staged.returncode != 0:
            run_checked([
                "git", "-C", root, "commit", "-m", "build thin Pi bridge fixture",
            ], env)
        write(
            os.path.join(run_dir, "BUILD.md"),
            "# Build\n\nCreated fixture-output.txt through the thin Pi bridge.\n",
        )
        write(
            os.path.join(run_dir, "VERIFICATION.md"),
            """# Verification
<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->
<!-- kimiflow:conformance contract=1 status=converged diff=passed strategy=passed architecture=not_applicable research=stable scope=passed decisions=1 checks=1 verifier=folded source=current-run -->
<!-- kimiflow:convergence-verification contract=1 risk=routine slices=1 failures=0 -->
Slice check S1: passed :: command :: test -f fixture-output.txt && grep -q built-by-thin-pi-bridge fixture-output.txt
Decision check D1: passed :: test -f fixture-output.txt && grep -q built-by-thin-pi-bridge fixture-output.txt
Requirement R1: passed :: test -f fixture-output.txt && grep -q built-by-thin-pi-bridge fixture-output.txt
""",
        )
        write(os.path.join(run_dir, "RECOVERY.md"), "# Recovery\n\nNo recovery was required.\n")
        write(
            os.path.join(run_dir, "CODE-REVIEW.md"),
            "# Code Review\n\nNo blocking findings in the deterministic fixture delta.\n",
        )
        with open(os.path.join(run_dir, "STATE.md"), encoding="utf-8") as handle:
            final_state = handle.read()
        for phase in range(6):
            final_state = final_state.replace(
                "Phase %d: open" % phase,
                "Phase %d: done" % phase,
            ).replace(
                "Phase %d: in-progress" % phase,
                "Phase %d: done" % phase,
            )
        final_state = final_state.replace("Phase 6: open", "Phase 6: in-progress")
        write(os.path.join(run_dir, "STATE.md"), final_state)
        for phase, phase_file in (
            (5, "phases/phase-5-build.md"),
            (6, "phases/phase-6-verify.md"),
        ):
            run_checked([
                active, "phase-read", "--root", root, "--run", run_rel,
                "--phase", str(phase), "--file", phase_file, "--write",
            ], env)
        conformance_record = run_checked([
            os.path.join(source, "hooks", "conformance-gate.sh"),
            run_dir, "--record", "--write",
        ], env)
        if not conformance_record.startswith("CONFORMANCE_GATE\tOPEN\t"):
            raise RuntimeError("conformance record did not open: " + conformance_record)
        with open(os.path.join(run_dir, "STATE.md"), encoding="utf-8") as handle:
            final_state = handle.read()
        final_state = final_state.replace("Phase 6: in-progress", "Phase 6: done")
        final_state = final_state.replace("Phase 7: open", "Phase 7: done")
        write(os.path.join(run_dir, "STATE.md"), final_state)
        run_checked([
            active, "phase-read", "--root", root, "--run", run_rel,
            "--phase", "7", "--file", "phases/phase-7-review-commit.md",
            "--write",
        ], env)
        run_checked([
            active, "refresh-baseline", "--root", root, "--write",
        ], env)
        conformance_finish = run_checked([
            os.path.join(source, "hooks", "conformance-gate.sh"),
            run_dir, "--finish",
        ], env)
        if not conformance_finish.startswith("CONFORMANCE_GATE\tOPEN\t"):
            raise RuntimeError("conformance finish did not open: " + conformance_finish)
        run_checked([
            active, "finish", "--root", root,
            "--skip-learning", "pi_e2e_fixture", "--write",
        ], env)

    emit({"type": "agent_end", "messages": []})
    emit({"type": "agent_settled"})
except Exception:
    with open(os.environ["PI_E2E_LOG"], "a", encoding="utf-8") as handle:
        traceback.print_exc(file=handle)
    raise
PY
chmod +x "$FAKE_PI"

cat > "$WORK/e2e.mjs" <<'JS'
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const { createCaptainExtension } = await import(process.env.PI_E2E_EXTENSION);
const context = {
  cwd: process.env.PI_E2E_REPO,
  sessionId: "pi-primary-e2e",
  model: { provider: "openai", id: "gpt-5.6" },
  thinkingLevel: "high",
};
const messages = [];
const pi = { sendMessage(value) { messages.push(value); } };
const extension = createCaptainExtension({ root: process.env.PI_E2E_SOURCE });

const activated = await extension.activate(
  "build fixture feature with Kimiflow",
  context,
);
assert.equal(activated.status, "activated");
assert.equal(activated.captainSessionId, "pi-primary-e2e");

let question;
let deadline = Date.now() + 30000;
while (Date.now() < deadline) {
  const attention = await extension.pollAttention(pi);
  if (attention.announced === 1) {
    question = messages.at(-1).details;
    if (question.kind === "question") break;
  }
  if (["failed", "aborted", "transport_error"].includes(attention.snapshot?.status)) {
    throw new Error(`runner failed before intake: ${JSON.stringify(attention.snapshot)}`);
  }
  await delay(100);
}
assert.equal(question?.kind, "question");
assert.equal(question.captain_session_id, "pi-primary-e2e");
assert.equal(question.worker_id, activated.workerId);
assert.equal(question.run, ".kimiflow/pi-bridge-e2e");
assert.match(question.question, /concrete Pi-to-Kimiflow product flow/i);
assert.equal(messages.at(-1).content, question.question);
const intakeText = fs.readFileSync(
  path.join(context.cwd, ".kimiflow/pi-bridge-e2e/INTAKE.md"),
  "utf8",
);
for (const label of [
  "Product flow entry",
  "User interaction",
  "Visible delegation outcome",
  "Unchanged path",
  "Done scenario",
]) {
  const row = intakeText.split("\n").find((line) => line.startsWith(`${label}: `));
  assert.ok(row, `missing receipt-bound ${label} row`);
  assert.ok(
    question.question.includes(row),
    `Captain question omitted receipt-bound ${label} row`,
  );
}

const waiting = await extension.status();
assert.equal(waiting.status, "awaiting_user");
assert.equal(waiting.active_run.awaiting_kind, "intake");
let receiptBeforeReply;
deadline = Date.now() + 5000;
while (Date.now() < deadline) {
  receiptBeforeReply = JSON.parse(
    fs.readFileSync(
      path.join(context.cwd, ".kimiflow/session/HEADLESS_RUN.json"),
      "utf8",
    ),
  );
  if (receiptBeforeReply.status === "awaiting_user") break;
  await delay(25);
}
assert.equal(receiptBeforeReply.status, "awaiting_user");
assert.equal(receiptBeforeReply.session_id, question.provider_session_id);

const queued = await extension.deliver("reply", {
  workerId: question.worker_id,
  providerSessionId: question.provider_session_id,
  run: question.run,
  message: "confirmed",
}, context);
assert.equal(queued.status, "queued");

let terminal;
deadline = Date.now() + 40000;
while (Date.now() < deadline) {
  terminal = await extension.status();
  if (terminal.status === "done") break;
  if (["failed", "aborted", "transport_error"].includes(terminal.status)) {
    throw new Error(`runner terminal failure: ${JSON.stringify(terminal)}`);
  }
  await delay(100);
}
assert.equal(terminal?.status, "done");
assert.equal(
  fs.readFileSync(path.join(context.cwd, "fixture-output.txt"), "utf8"),
  "built-by-thin-pi-bridge\n",
);
const gates = JSON.parse(
  fs.readFileSync(
    path.join(context.cwd, ".kimiflow/pi-bridge-e2e/PI-E2E-GATES.json"),
    "utf8",
  ),
);
assert.match(gates.clarify_record, /^CLARIFY_GATE\tOPEN\t/);
assert.match(gates.clarify, /^CLARIFY_GATE\tOPEN\t/);
assert.match(gates.plan, /^PLAN_BLOCKER_GATE\tOPEN\t/);
assert.match(gates.build, /^BUILD_GATE\tCONTINUE\t/);

const completionPoll = await extension.pollAttention(pi);
assert.equal(completionPoll.announced, 1);
const completion = messages.at(-1).details;
assert.equal(completion.kind, "completion");
assert.equal(completion.run, question.run);
assert.equal(completion.provider_session_id, question.provider_session_id);
assert.match(messages.at(-1).content, /^✓ Kimiflow · /);
JS

export KIMIFLOW_PI_COMMAND="$FAKE_PI"
export PI_E2E_SOURCE="$ROOT"
export PI_E2E_REPO="$REPO"
export PI_E2E_LOG="$PI_LOG"
export PI_E2E_EXTENSION="file://$ROOT/hosts/pi/extensions/captain.js"

if ! node "$WORK/e2e.mjs"; then
  [ ! -s "$PI_LOG" ] || cat "$PI_LOG" >&2
  exit 1
fi

printf 'ok   test_existing_pi_remains_captain_for_thin_kimiflow_run\n'
