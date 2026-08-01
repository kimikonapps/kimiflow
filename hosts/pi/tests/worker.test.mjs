import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn as spawnProcess, spawnSync } from "node:child_process";
import { EventEmitter } from "node:events";
import {
  existsSync,
  linkSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import registerWorkerExtension, {
  createFileSubagentReservations,
  createPreIntakeGuard,
  createSubagentReceiptWriter,
  forwardPromptContext,
  GenerationSupervisor,
  loadWorkerAuthority,
} from "../extensions/worker.js";

function authority(root = process.cwd()) {
  const calmExtension = realpathSync(path.join(
    process.cwd(),
    "hosts/pi/extensions/calm.js",
  ));
  return {
    schema_version: 1,
    root,
    captain_session_id: "captain-00000001",
    worker_id: "worker-00000001",
    executable: process.execPath,
    activeRun: `${process.cwd()}/hooks/active-run.sh`,
    calmExtension,
    calmDigest: `sha256:${createHash("sha256")
      .update(readFileSync(calmExtension))
      .digest("hex")}`,
    verbosity: "quiet",
    selection: {
      provider: "openai",
      model: "gpt-5.6",
      thinking: "high",
    },
  };
}

function environment(overrides = {}, root = process.cwd()) {
  const value = authority(root);
  delete value.executable;
  delete value.activeRun;
  delete value.calmExtension;
  delete value.calmDigest;
  delete value.verbosity;
  delete value.selection;
  return {
    KIMIFLOW_PI_BRIDGE_BINDING: JSON.stringify(value),
    KIMIFLOW_PI_EXECUTABLE: process.execPath,
    KIMIFLOW_PI_ACTIVE_RUN: `${process.cwd()}/hooks/active-run.sh`,
    KIMIFLOW_PI_SELECTION: JSON.stringify({
      provider: "openai",
      model: "gpt-5.6",
      thinking: "high",
    }),
    KIMIFLOW_PI_CALM_EXTENSION: value.calmExtension,
    KIMIFLOW_PI_CALM_EXTENSION_DIGEST: value.calmDigest,
    ...overrides,
  };
}

function lifecycle(sessionId, cwd, result = "bounded result") {
  return [
    { type: "session", version: 3, id: sessionId, cwd },
    { type: "agent_start" },
    {
      type: "message_end",
      message: {
        role: "assistant",
        content: [{ type: "text", text: result }],
        stopReason: "stop",
      },
    },
    { type: "agent_end" },
    { type: "agent_settled" },
  ];
}

function subagentRequest(task, overrides = {}) {
  const stem = task.toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 42) || "worker";
  return {
    task,
    role: "code_review",
    round: 1,
    seat: `test-${stem}`,
    ...overrides,
  };
}

function completingSpawn(log, eventsFactory = lifecycle) {
  let pid = 4000;
  return (command, args, options) => {
    const child = new EventEmitter();
    child.pid = ++pid;
    child.exitCode = null;
    child.signalCode = null;
    child.stdout = new EventEmitter();
    child.kill = (signal) => {
      child.signalCode = signal;
      queueMicrotask(() => {
        child.emit("exit", null, signal);
        child.emit("close", null, signal);
      });
      return true;
    };
    log.push({ command, args, options, child });
    const sessionId = args[args.indexOf("--session-id") + 1];
    queueMicrotask(() => {
      const events = eventsFactory(sessionId, options.cwd);
      child.stdout.emit(
        "data",
        Buffer.from(`${events.map(JSON.stringify).join("\n")}\n`),
      );
      child.exitCode = 0;
      child.emit("exit", 0, null);
      child.emit("close", 0, null);
    });
    return child;
  };
}

test("worker authority is optional, exact, and preserves colon model IDs", () => {
  assert.equal(loadWorkerAuthority({}), null);
  assert.throws(
    () => loadWorkerAuthority(environment({
      KIMIFLOW_PI_BRIDGE_BINDING: JSON.stringify({ root: process.cwd() }),
    })),
    /worker_binding_invalid/,
  );
  const loaded = loadWorkerAuthority(environment({
    KIMIFLOW_PI_CALM_EXTENSION: "/missing/calm.js",
    KIMIFLOW_PI_CALM_EXTENSION_DIGEST: "invalid",
    KIMIFLOW_PI_SELECTION: JSON.stringify({
      provider: "cloudflare",
      model: "@cf/meta/llama:free",
      thinking: "max",
    }),
  }));
  assert.deepEqual(loaded.selection, {
    provider: "cloudflare",
    model: "@cf/meta/llama:free",
    thinking: "max",
  });
  assert.equal(loaded.worker_id, "worker-00000001");
  assert.equal(loaded.calmExtension, null);
  assert.equal(Object.isFrozen(loaded), true);
  assert.throws(
    () => loadWorkerAuthority(environment({
      KIMIFLOW_PI_HERDR: "1",
      KIMIFLOW_PI_CALM_EXTENSION: "/missing/calm.js",
    })),
    /calm_extension_invalid/,
  );
});

test("pre-intake guard permits only reads, trusted control, and current run artifacts", async () => {
  const value = authority();
  const guard = createPreIntakeGuard(value, {
    getIntakeState: () => ({
      state: "intake",
      run: ".kimiflow/run-6-fixture",
    }),
  });
  const context = { cwd: value.root };
  assert.equal(await guard({ toolName: "read", input: { path: "src/app.js" } }, context), undefined);
  assert.equal(await guard({
    toolName: "bash",
    input: { command: `hooks/active-run.sh status --root '${value.root}'` },
  }, context), undefined);
  for (const command of [
    "git rev-parse --is-inside-work-tree",
    "git status --short --branch",
    "git worktree list --porcelain",
  ]) {
    assert.equal(await guard({
      toolName: "bash",
      input: { command },
    }, context), undefined);
  }
  assert.equal(await guard({
    toolName: "write",
    input: { path: ".kimiflow/run-6-fixture/INTAKE.md" },
  }, context), undefined);
  assert.equal(await guard({
    toolName: "write",
    input: { path: ".kimiflow/run-6-fixture/INTAKE-2.md" },
  }, context), undefined);

  for (const event of [
    { toolName: "bash", input: { command: "npm test" } },
    { toolName: "bash", input: { command: "git status --short --branch; env" } },
    { toolName: "bash", input: { command: "git diff" } },
    { toolName: "bash", input: { command: `hooks/active-run.sh abort --root '${value.root}'` } },
    { toolName: "bash", input: { command: "hooks/active-run.sh abort --root /tmp/project" } },
    { toolName: "bash", input: { command: "bash hooks/workspace-preflight.sh integrate --root=/tmp/project" } },
    { toolName: "write", input: { path: "src/product.js" } },
    { toolName: "write", input: { path: ".kimiflow/run-6-fixture/PLAN.md" } },
    { toolName: "write", input: { path: ".kimiflow/other-run/INTAKE.md" } },
    { toolName: "write", input: { path: ".kimiflow/run-6-fixture/INTENT-LOCK.json" } },
    { toolName: "kimiflow_subagent", input: { task: "change the product" } },
  ]) {
    assert.equal((await guard(event, context)).block, true);
  }
});

test("pre-intake bootstrap is bounded and confirmed intake releases normal tools", async () => {
  const value = authority();
  const bootstrap = createPreIntakeGuard(value, {
    getIntakeState: () => ({ state: "intake", run: null }),
  });
  assert.equal(await bootstrap({
    toolName: "write",
    input: { path: ".kimiflow/new-run/STATE.md" },
  }, { cwd: value.root }), undefined);
  assert.equal((await bootstrap({
    toolName: "write",
    input: { path: ".kimiflow/new-run/PLAN.md" },
  }, { cwd: value.root })).block, true);

  const confirmed = createPreIntakeGuard(value, {
    getIntakeState: () => ({ state: "confirmed", run: ".kimiflow/new-run" }),
  });
  assert.equal(await confirmed({
    toolName: "bash",
    input: { command: "npm test && npm run build" },
  }, { cwd: value.root }), undefined);
  assert.equal(await confirmed({
    toolName: "kimiflow_subagent",
    input: { task: "review the implementation" },
  }, { cwd: value.root }), undefined);
});

test("fix runs do not require the feature intent lock", async (t) => {
  const root = path.resolve(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-fix-intake-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(path.join(root, ".kimiflow/session"), { recursive: true });
  writeFileSync(
    path.join(root, ".kimiflow/session/ACTIVE_RUN.json"),
    JSON.stringify({
      schema_version: 1,
      status: "active",
      run: ".kimiflow/fix-herdr-resume",
      mode: "fix",
    }),
  );
  const guard = createPreIntakeGuard(authority(root));
  assert.equal(await guard({
    toolName: "bash",
    input: { command: "npm test" },
  }, { cwd: root }), undefined);
  assert.equal(await guard({
    toolName: "write",
    input: { path: "src/fix.js" },
  }, { cwd: root }), undefined);

  writeFileSync(
    path.join(root, ".kimiflow/session/ACTIVE_RUN.json"),
    JSON.stringify({
      schema_version: 1,
      status: "active",
      run: ".kimiflow/feature-herdr-resume",
      mode: "feature",
    }),
  );
  assert.equal((await guard({
    toolName: "bash",
    input: { command: "npm test" },
  }, { cwd: root })).block, true);
});

test("pre-intake guard rejects run-directory aliases and exact hard links", async (t) => {
  const root = path.resolve(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-intake-object-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  mkdirSync(path.join(root, ".kimiflow"));
  const product = path.join(root, "product");
  mkdirSync(product);
  writeFileSync(path.join(product, "INTAKE.md"), "product\n");
  symlinkSync(product, path.join(root, ".kimiflow", "run"));
  const value = { ...authority(), root };
  const guard = createPreIntakeGuard(value, {
    getIntakeState: () => ({ state: "intake", run: ".kimiflow/run" }),
  });
  const event = {
    toolName: "write",
    input: { path: ".kimiflow/run/INTAKE.md" },
  };
  assert.equal((await guard(event, { cwd: root })).block, true);

  rmSync(path.join(root, ".kimiflow", "run"));
  mkdirSync(path.join(root, ".kimiflow", "run"));
  linkSync(
    path.join(product, "INTAKE.md"),
    path.join(root, ".kimiflow", "run", "INTAKE.md"),
  );
  assert.equal((await guard(event, { cwd: root })).block, true);
});

test("subagent command, identity, model, root, and environment are derived", async () => {
  const calls = [];
  const inheritedAuthority = {
    KIMIFLOW_HOST: "pi",
    KIMIFLOW_SESSION_HOST: "pi",
    KIMIFLOW_SESSION_ID: "pi-worker-00000001",
    KIMIFLOW_RUNNER_CONTROLLER: "pi",
    KIMIFLOW_PI_START_CLAIM: `claim-${"a".repeat(32)}`,
  };
  const previous = new Map(
    Object.keys(inheritedAuthority).map((key) => [key, process.env[key]]),
  );
  Object.assign(process.env, inheritedAuthority);
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn: completingSpawn(calls),
    idFactory: () => "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  });
  supervisor.workerSessionId = "pi-worker-00000001";

  const result = await supervisor.launchSubagent(
    subagentRequest("--model attacker-controlled"),
  );
  for (const [key, value] of previous) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  const call = calls[0];
  assert.equal(result.status, "completed");
  assert.equal(result.result, "bounded result");
  assert.equal(result.backend, "process");
  assert.equal(result.generation, "generation-worker-00000001");
  assert.deepEqual(call.args, [
    "--mode", "json",
    "--no-extensions",
    "--tools", "read,grep,find,ls",
    "--provider", "openai",
    "--model", "gpt-5.6",
    "--thinking", "high",
    "--session-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "Kimiflow bounded code_review subagent task:\n--model attacker-controlled",
  ]);
  assert.equal(call.options.cwd, process.cwd());
  assert.equal(call.options.detached, true);
  assert.deepEqual(
    Object.keys(call.options.env).filter((key) => key.startsWith("KIMIFLOW_")),
    [],
  );
});

test("Herdr workers launch visible read-only Pi subagents through pi-host", async () => {
  const calls = [];
  const spawn = (command, args, options) => {
    const child = new EventEmitter();
    child.pid = 4201;
    child.exitCode = null;
    child.signalCode = null;
    child.stdout = new EventEmitter();
    child.kill = () => true;
    child.stdin = {
      end(raw) {
        const payload = JSON.parse(raw);
        calls.push({ command, args, options, payload });
        queueMicrotask(() => {
          child.stdout.emit("data", Buffer.from(
            `${lifecycle(
              payload.session_id,
              payload.root,
              "visible review",
            ).map(JSON.stringify).join("\n")}\n`,
          ));
          child.exitCode = 0;
          child.emit("close", 0, null);
        });
      },
    };
    return child;
  };
  const supervisor = new GenerationSupervisor({
    authority: {
      ...authority(),
      herdr: true,
      piHost: `${process.cwd()}/hooks/pi-host.sh`,
    },
    spawn,
    idFactory: () => "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  });
  supervisor.workerSessionId = "pi-worker-00000001";
  const result = await supervisor.launchSubagent(
    subagentRequest("review the current code"),
  );
  assert.equal(result.backend, "herdr");
  assert.equal(result.result, "visible review");
  assert.equal(calls[0].command, `${process.cwd()}/hooks/pi-host.sh`);
  assert.deepEqual(calls[0].args, ["subagent", "--json"]);
  assert.equal(calls[0].payload.task, "review the current code");
  assert.equal(calls[0].payload.slot, 1);
  assert.equal(calls[0].payload.role, "code_review");
  assert.equal(calls[0].payload.round, 1);
  assert.equal(calls[0].payload.seat, "test-review-the-current-code");
  assert.equal(calls[0].payload.calm_extension, authority().calmExtension);
  assert.equal(calls[0].payload.calm_extension_digest, authority().calmDigest);
  assert.equal(calls[0].payload.verbosity, "quiet");
  assert.deepEqual(calls[0].payload.selection, authority().selection);
});

test("implementation workers receive the bounded write-capable tool set", async () => {
  const calls = [];
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn: completingSpawn(calls),
    idFactory: () => "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
  });
  supervisor.workerSessionId = "pi-worker-00000001";
  const result = await supervisor.launchSubagent(subagentRequest(
    "implement the accepted slice",
    { role: "implementation", seat: "implementation-1" },
  ));
  assert.equal(result.role, "implementation");
  assert.equal(result.phase, 5);
  const args = calls[0].args;
  assert.equal(args[args.indexOf("--tools") + 1], "read,bash,edit,write,grep,find,ls");
});

test("completed role-bound subagents write one private mechanical receipt", (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-receipt-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const run = path.join(root, ".kimiflow", "run-7");
  mkdirSync(run, { recursive: true });
  const value = authority(root);
  const writeReceipt = createSubagentReceiptWriter(value, () => ({
    state: "confirmed",
    run: ".kimiflow/run-7",
  }));
  const relative = writeReceipt({
    workerId: value.worker_id,
    workerSessionId: "pi-worker-00000001",
    subagentSessionId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    phase: 5,
    role: "implementation",
    round: 1,
    seat: "implementation-1",
    slot: 1,
    backend: "herdr",
    status: "completed",
    task: "implement the accepted slice",
    result: "implemented and verified",
  });
  const receiptPath = path.join(root, relative);
  const receipt = JSON.parse(readFileSync(receiptPath, "utf8"));
  assert.equal(lstatSync(path.dirname(receiptPath)).mode & 0o077, 0);
  assert.equal(lstatSync(receiptPath).mode & 0o077, 0);
  assert.equal(receipt.role, "implementation");
  assert.equal(receipt.phase, 5);
  assert.equal(receipt.seat, "implementation-1");
  assert.match(receipt.receipt_id, /^sha256:[0-9a-f]{64}$/);
  assert.match(receipt.task_digest, /^sha256:[0-9a-f]{64}$/);
  assert.match(receipt.result_digest, /^sha256:[0-9a-f]{64}$/);
});

test("subagent completion uses the last successful Pi retry lifecycle", async () => {
  const calls = [];
  const spawn = completingSpawn(calls, (sessionId, cwd) => [
    { type: "session", version: 3, id: sessionId, cwd },
    { type: "agent_start" },
    {
      type: "message_end",
      message: {
        role: "assistant",
        content: [{ type: "text", text: "retry me" }],
        stopReason: "error",
      },
    },
    { type: "agent_end" },
    { type: "agent_start" },
    {
      type: "message_end",
      message: {
        role: "assistant",
        content: [{ type: "text", text: "recovered result" }],
        stopReason: "stop",
      },
    },
    { type: "agent_end" },
    { type: "agent_settled" },
  ]);
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn,
    idFactory: () => "11111111-2222-4333-8444-555555555555",
  });
  supervisor.workerSessionId = "pi-worker-00000001";
  const result = await supervisor.launchSubagent(subagentRequest("recover once"));
  assert.equal(result.result, "recovered result");
});

test("subagent completion rejects Pi 0.83 pending output", async () => {
  const calls = [];
  const spawn = completingSpawn(calls, (sessionId, cwd) => [
    { type: "session", version: 3, id: sessionId, cwd },
    { type: "agent_start" },
    {
      type: "message_end",
      message: {
        role: "assistant",
        content: [{ type: "text", text: "partial result" }],
        stopReason: "pending",
      },
    },
    { type: "agent_end" },
    { type: "agent_settled" },
  ]);
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn,
    idFactory: () => "11111111-2222-4333-8444-555555555555",
  });
  supervisor.workerSessionId = "pi-worker-00000001";
  await assert.rejects(
    supervisor.launchSubagent(subagentRequest("reject partial output")),
    /subagent_lifecycle_incomplete/,
  );
});

test("subagent output rejects invalid UTF-8", async () => {
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn(_command, args, options) {
      const child = new EventEmitter();
      child.pid = 4801;
      child.exitCode = null;
      child.signalCode = null;
      child.stdout = new EventEmitter();
      child.kill = () => true;
      const sessionId = args[args.indexOf("--session-id") + 1];
      queueMicrotask(() => {
        const events = lifecycle(sessionId, options.cwd, "PLACEHOLDER");
        const encoded = Buffer.from(`${events.map(JSON.stringify).join("\n")}\n`);
        const needle = Buffer.from("PLACEHOLDER");
        const offset = encoded.indexOf(needle);
        encoded[offset] = 0xff;
        child.stdout.emit("data", encoded);
        child.exitCode = 0;
        child.emit("exit", 0, null);
        child.emit("close", 0, null);
      });
      return child;
    },
    idFactory: () => "11111111-2222-4333-8444-555555555555",
  });
  supervisor.workerSessionId = "pi-worker-00000001";
  await assert.rejects(
    supervisor.launchSubagent(subagentRequest("reject invalid UTF-8")),
    /subagent_output_invalid/,
  );
});

test("subagent output waits for close after exit", async () => {
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn(_command, args, options) {
      const child = new EventEmitter();
      child.pid = 4802;
      child.exitCode = null;
      child.signalCode = null;
      child.stdout = new EventEmitter();
      child.kill = () => true;
      const sessionId = args[args.indexOf("--session-id") + 1];
      queueMicrotask(() => {
        const encoded = Buffer.from(
          `${lifecycle(sessionId, options.cwd).map(JSON.stringify).join("\n")}\n`,
        );
        const split = encoded.length - 12;
        child.stdout.emit("data", encoded.subarray(0, split));
        child.exitCode = 0;
        child.emit("exit", 0, null);
        child.stdout.emit("data", encoded.subarray(split));
        child.emit("close", 0, null);
      });
      return child;
    },
    idFactory: () => "11111111-2222-4333-8444-555555555555",
  });
  supervisor.workerSessionId = "pi-worker-00000001";
  const result = await supervisor.launchSubagent(
    subagentRequest("wait for stdout close"),
  );
  assert.equal(result.result, "bounded result");
});

test("generation enforces three fresh children and shutdown stops all of them", async () => {
  const children = [];
  const spawn = (_command, _args, _options) => {
    const child = new EventEmitter();
    child.pid = 5000 + children.length;
    child.exitCode = null;
    child.signalCode = null;
    child.stdout = new EventEmitter();
    child.kill = (signal) => {
      child.signalCode = signal;
      queueMicrotask(() => {
        child.emit("exit", null, signal);
        child.emit("close", null, signal);
      });
      return true;
    };
    children.push(child);
    return child;
  };
  const identities = [
    "11111111-2222-4333-8444-555555555555",
    "22222222-2222-4333-8444-555555555555",
    "33333333-2222-4333-8444-555555555555",
    "44444444-2222-4333-8444-555555555555",
  ];
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn,
    idFactory: () => identities.shift(),
  });
  supervisor.workerSessionId = "pi-worker-00000001";
  const pending = [
    supervisor.launchSubagent(subagentRequest("one")),
    supervisor.launchSubagent(subagentRequest("two")),
    supervisor.launchSubagent(subagentRequest("three")),
  ];
  await new Promise((done) => setImmediate(done));
  await assert.rejects(
    supervisor.launchSubagent(subagentRequest("four")),
    /subagent_capacity/,
  );
  assert.equal(supervisor.active.size, 3);
  assert.deepEqual(await supervisor.stopGeneration(), {
    generation: "generation-worker-00000001",
    stopped: 3,
    released: true,
  });
  assert.equal(supervisor.active.size, 0);
  assert.ok((await Promise.allSettled(pending)).every(({ status }) => status === "rejected"));
  await assert.rejects(
    supervisor.launchSubagent(subagentRequest("later")),
    /subagent_generation_stopping/,
  );
});

test("generation reuses a released slot after sequential subagents", async () => {
  const calls = [];
  const identities = [
    "11111111-2222-4333-8444-555555555555",
    "22222222-2222-4333-8444-555555555555",
    "33333333-2222-4333-8444-555555555555",
    "44444444-2222-4333-8444-555555555555",
  ];
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn: completingSpawn(calls),
    idFactory: () => identities.shift(),
  });
  supervisor.workerSessionId = "pi-worker-00000001";
  await supervisor.launchSubagent(subagentRequest("one"));
  await supervisor.launchSubagent(subagentRequest("two"));
  await supervisor.launchSubagent(subagentRequest("three"));
  const fourth = await supervisor.launchSubagent(subagentRequest("four"));
  assert.equal(fourth.slot, 1);
  assert.equal(calls.length, 4);
});

test("a fresh Pi transport sees released completed slots", async (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-worker-slots-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const calls = [];
  const identities = [
    "11111111-2222-4333-8444-555555555555",
    "22222222-2222-4333-8444-555555555555",
    "33333333-2222-4333-8444-555555555555",
    "44444444-2222-4333-8444-555555555555",
  ];
  const current = {
    sessionManager: {
      getSessionId() { return "pi-worker-00000001"; },
      getBranch() { return []; },
    },
  };
  const createTransport = () => {
    const handlers = new Map();
    const tools = new Map();
    const pi = {
      on(name, handler) { handlers.set(name, handler); },
      registerTool(value) { tools.set(value.name, value); },
    };
    registerWorkerExtension(pi, {
      environment: environment({}, root),
      spawn: completingSpawn(calls),
      idFactory: () => identities.shift(),
      receiptWriter: () => ".kimiflow/run/PI-SUBAGENTS/test.json",
    });
    handlers.get("session_start")({}, current);
    return tools.get("kimiflow_subagent");
  };

  const first = createTransport();
  for (const task of ["one", "two", "three"]) {
    await first.execute("tool-call", subagentRequest(task));
  }
  const second = createTransport();
  await second.execute("tool-call", subagentRequest("four"));
  assert.equal(calls.length, 4);
  assert.deepEqual(
    readdirSync(path.join(
      root,
      ".kimiflow",
      "session",
      "PI-SUBAGENT-SLOTS-v2",
      "worker-00000001",
    )).sort(),
    [],
  );
});

test("generation cap survives Pi branch rewind", async (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-worker-rewind-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const value = authority(root);
  const calls = [];
  const first = new GenerationSupervisor({
    authority: value,
    reservations: createFileSubagentReservations(value),
    spawn: completingSpawn(calls),
    idFactory: (() => {
      const ids = [
        "11111111-2222-4333-8444-555555555555",
        "22222222-2222-4333-8444-555555555555",
        "33333333-2222-4333-8444-555555555555",
      ];
      return () => ids.shift();
    })(),
  });
  first.workerSessionId = "pi-worker-00000001";
  for (const task of ["one", "two", "three"]) {
    await first.launchSubagent(subagentRequest(task));
  }
  const restored = new GenerationSupervisor({
    authority: value,
    reservations: createFileSubagentReservations(value),
    spawn: completingSpawn(calls),
    idFactory: () => "44444444-2222-4333-8444-555555555555",
  });
  restored.workerSessionId = "pi-worker-00000001";
  assert.equal(restored.restoreReservations({
    sessionManager: { getBranch() { return []; } },
  }), 0);
  await restored.launchSubagent(subagentRequest("four"));
  assert.equal(calls.length, 4);
});

test("file generation slots serialize fresh transport processes", async (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-worker-race-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const ready = path.join(root, "ready");
  const barrier = path.join(root, "go");
  const moduleUrl = new URL("../extensions/worker.js", import.meta.url).href;
  const script = `
    import { appendFileSync, existsSync } from "node:fs";
    const { createFileSubagentReservations } = await import(process.argv[1]);
    appendFileSync(process.argv[3], String(process.pid) + "\\n");
    while (!existsSync(process.argv[4])) {
      await new Promise((resolve) => setTimeout(resolve, 2));
    }
    try {
      const slot = createFileSubagentReservations({
        root: process.argv[2],
        worker_id: "worker-00000001",
      }).reserve();
      process.stdout.write(JSON.stringify({ slot }));
    } catch (error) {
      process.stdout.write(JSON.stringify({ error: error.message }));
    }
  `;
  const run = () => new Promise((resolvePromise, reject) => {
    const child = spawnProcess(process.execPath, [
      "--input-type=module",
      "-e",
      script,
      moduleUrl,
      root,
      ready,
      barrier,
    ]);
    const output = [];
    const errors = [];
    child.stdout.on("data", (chunk) => output.push(chunk));
    child.stderr.on("data", (chunk) => errors.push(chunk));
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code !== 0) {
        reject(new Error(Buffer.concat(errors).toString("utf8")));
        return;
      }
      resolvePromise(JSON.parse(Buffer.concat(output).toString("utf8")));
    });
  });
  const children = Array.from({ length: 4 }, () => run());
  while (
    !existsSync(ready)
    || readFileSync(ready, "utf8").trim().split("\n").length < 4
  ) {
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
  }
  writeFileSync(barrier, "go\n");
  const results = await Promise.all(children);
  assert.deepEqual(
    results.filter(({ slot }) => slot).map(({ slot }) => slot).sort(),
    [1, 2, 3],
  );
  assert.deepEqual(
    results.filter(({ error }) => error).map(({ error }) => error),
    ["subagent_capacity"],
  );
});

test("removing a known generation ledger fails closed instead of resetting capacity", () => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-worker-delete-")));
  try {
    const value = authority(root);
    const reservations = createFileSubagentReservations(value);
    assert.deepEqual(
      [reservations.reserve(), reservations.reserve(), reservations.reserve()],
      [1, 2, 3],
    );
    const generation = path.join(
      root,
      ".kimiflow",
      "session",
      "PI-SUBAGENT-SLOTS-v2",
      value.worker_id,
    );
    renameSync(generation, `${generation}-removed`);
    assert.throws(
      () => createFileSubagentReservations(value),
      /subagent_capacity_invalid/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("replacing a pinned generation directory cannot reset live reservations", () => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-worker-replace-")));
  try {
    const value = authority(root);
    const reservations = createFileSubagentReservations(value);
    assert.deepEqual(
      [reservations.reserve(), reservations.reserve(), reservations.reserve()],
      [1, 2, 3],
    );
    const generation = path.join(
      root,
      ".kimiflow",
      "session",
      "PI-SUBAGENT-SLOTS-v2",
      value.worker_id,
    );
    renameSync(generation, `${generation}-removed`);
    mkdirSync(generation, { mode: 0o700 });
    assert.throws(
      () => reservations.reserve(),
      /subagent_capacity_invalid/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("moving the ledger and colocated marker preserves external generation authority", () => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-worker-dual-")));
  try {
    const value = authority(root);
    const reservations = createFileSubagentReservations(value);
    assert.deepEqual(
      [reservations.reserve(), reservations.reserve(), reservations.reserve()],
      [1, 2, 3],
    );
    const registry = path.join(
      root,
      ".kimiflow",
      "session",
      "PI-SUBAGENT-SLOTS-v2",
    );
    renameSync(
      path.join(registry, value.worker_id),
      path.join(registry, `${value.worker_id}-removed`),
    );
    renameSync(
      path.join(registry, `.generation-${value.worker_id}`),
      path.join(registry, `.generation-${value.worker_id}-removed`),
    );
    assert.throws(
      () => createFileSubagentReservations(value),
      /subagent_capacity_invalid/,
    );
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("generation shutdown settles a real descendant process group", async (t) => {
  const temporary = mkdtempSync(path.join(tmpdir(), "kimiflow-worker-group-"));
  const pidfile = path.join(temporary, "descendant.pid");
  const marker = path.join(temporary, "escaped.marker");
  let descendantPid = null;
  t.after(() => {
    if (Number.isInteger(descendantPid)) {
      try {
        process.kill(descendantPid, "SIGKILL");
      } catch {
        // The expected path already settled the descendant.
      }
    }
    rmSync(temporary, { recursive: true, force: true });
  });
  const program = `
    const { spawn } = require("node:child_process");
    const { writeFileSync } = require("node:fs");
    const descendant = spawn(
      process.execPath,
      [
        "-e",
        ${JSON.stringify(`
          const { writeFileSync } = require("node:fs");
          setTimeout(() => writeFileSync(${JSON.stringify(marker)}, "escaped"), 600);
          setInterval(() => {}, 1000);
        `)},
      ],
      { detached: true, stdio: "ignore", env: process.env },
    );
    descendant.unref();
    writeFileSync(${JSON.stringify(pidfile)}, String(descendant.pid));
    setInterval(() => {}, 1000);
  `;
  const supervisor = new GenerationSupervisor({
    authority: authority(),
    spawn: (_command, _args, options) => spawnProcess(
      process.execPath,
      ["-e", program],
      options,
    ),
    idFactory: () => "subagent-00000001",
  });
  supervisor.workerSessionId = "worker-session-0001";
  const launch = supervisor.launchSubagent(
    subagentRequest("hold one descendant"),
  ).then(
    () => null,
    (error) => error,
  );
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      descendantPid = Number(readFileSync(pidfile, "utf8"));
      break;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
  }
  assert.ok(Number.isInteger(descendantPid) && descendantPid > 1);

  const result = await supervisor.stopGeneration();
  assert.equal(result.released, true);
  assert.match(String(await launch), /subagent_process_failed/);
  let alive = true;
  for (let attempt = 0; attempt < 100 && alive; attempt += 1) {
    try {
      process.kill(descendantPid, 0);
      await new Promise((resolve) => setTimeout(resolve, 10));
    } catch (error) {
      if (error?.code === "ESRCH") alive = false;
      else throw error;
    }
  }
  assert.equal(alive, false);
  await new Promise((resolve) => setTimeout(resolve, 700));
  assert.equal(existsSync(marker), false);
});

test("before_agent_start forwards the exact Pi prompt to Active Run", async () => {
  const calls = [];
  const hookSpawn = (command, args, options) => {
    const child = new EventEmitter();
    child.pid = 7100;
    child.stdout = new EventEmitter();
    child.stdin = {
      end(payload) {
        calls.push({ command, args, options, payload: JSON.parse(payload) });
        queueMicrotask(() => {
          child.stdout.emit("data", Buffer.from(JSON.stringify({
            hookSpecificOutput: {
              additionalContext: "Kimiflow intake resumed.",
            },
          })));
          child.emit("exit", 0, null);
        });
      },
    };
    child.kill = () => true;
    return child;
  };
  const userPrompt = [
    "Use the current answer.",
    "",
    "Transport request:",
    "Keep this embedded marker too.",
  ].join("\n");
  const generated = spawnSync("python3", [
    "-c",
    [
      "import os, sys",
      "from kimiflow_core import model_adapter, pi_host",
      "payload = {'root': os.getcwd(), 'prompt': sys.argv[1], 'workflow_context': model_adapter.workflow_context()}",
      "sys.stdout.write(pi_host._workflow_prompt(payload, verbosity='quiet'))",
    ].join("; "),
    userPrompt,
  ], {
    cwd: process.cwd(),
    env: {
      ...process.env,
      PYTHONPATH: path.join(process.cwd(), "hooks"),
    },
    encoding: "utf8",
  });
  assert.equal(generated.status, 0, generated.stderr);
  const result = await forwardPromptContext(
    authority(),
    { prompt: generated.stdout },
    { sessionId: "pi-worker-00000001" },
    hookSpawn,
  );
  assert.deepEqual(calls[0].args, ["prompt-context"]);
  assert.equal(calls[0].options.cwd, process.cwd());
  assert.equal(calls[0].options.env.KIMIFLOW_HOST, "pi");
  assert.deepEqual(calls[0].payload, {
    cwd: process.cwd(),
    session_id: "pi-worker-00000001",
    prompt: userPrompt,
  });
  assert.equal(result.message.content, "Kimiflow intake resumed.");

  await forwardPromptContext(
    authority(),
    { prompt: "Vertrag bestätigen" },
    { sessionId: "pi-worker-00000001" },
    hookSpawn,
  );
  assert.equal(calls[1].payload.prompt, "Vertrag bestätigen");
});

test("before_agent_start rejects malformed or unsupported Kimiflow transport envelopes", async () => {
  const prompts = [
    "\u2063kimiflow:transport-v2\nAuthoritative Kimiflow workflow_context:\n\nTransport request:\nconfirmed",
    "\u2063kimiflow:transport-v1\nTransport request:\nconfirmed",
    "\u2063kimiflow:transport-v1\nAuthoritative Kimiflow workflow_context:\nconfirmed",
  ];
  for (const prompt of prompts) {
    await assert.rejects(
      async () => forwardPromptContext(
        authority(),
        { prompt },
        { sessionId: "pi-worker-00000001" },
        () => { throw new Error("must not spawn"); },
      ),
      /prompt_context_transport_(?:invalid|unsupported)/,
    );
  }
});

test("production registration is inert without bridge authority and exposes one bounded tool with it", (t) => {
  const inertPi = {
    on() { throw new Error("must not register"); },
    registerTool() { throw new Error("must not register"); },
  };
  assert.equal(registerWorkerExtension(inertPi, { environment: {} }), null);

  const handlers = new Map();
  const tools = [];
  const pi = {
    on(name, handler) { handlers.set(name, handler); },
    registerTool(tool) { tools.push(tool); },
  };
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-worker-register-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const workerEnvironment = environment({}, root);
  const supervisor = registerWorkerExtension(pi, {
    environment: workerEnvironment,
  });
  assert.ok(supervisor);
  assert.deepEqual(tools.map(({ name }) => name), ["kimiflow_subagent"]);
  assert.equal(tools[0].parameters.type, "object");
  handlers.get("session_start")({}, { sessionId: "pi-worker-00000001" });
  assert.equal(supervisor.workerSessionId, "pi-worker-00000001");
  assert.equal(workerEnvironment.KIMIFLOW_HOST, "pi");
  assert.equal(workerEnvironment.KIMIFLOW_SESSION_ID, "pi-worker-00000001");
  assert.ok(handlers.has("before_agent_start"));
});
