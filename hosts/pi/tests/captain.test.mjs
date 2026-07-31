import assert from "node:assert/strict";
import { spawn as spawnProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import registerCaptainExtension, {
  createCaptainExtension,
  createFileStartClaims,
} from "../extensions/captain.js";

const idle = {
  schema_version: 1,
  status: "idle",
  runner: null,
  active_run: { present: false },
};
const piCapabilities = {
  schema_version: 1,
  name: "pi-fixture00000001",
  host: "pi",
  capabilities: {
    files: true,
    shell: true,
    tests: true,
    resume: true,
    gates: true,
  },
  features: {
    workflow_context: true,
    structured_events: true,
    root_confinement: false,
  },
};

function context(entries = [], overrides = {}) {
  return {
    cwd: process.cwd(),
    sessionManager: {
      getSessionId() { return "pi-primary-0001"; },
      getBranch() { return [...entries]; },
    },
    model: { provider: "openai", id: "gpt-5.6" },
    thinkingLevel: "high",
    ...overrides,
  };
}

function spawnFixture() {
  const calls = [];
  let pid = 3000;
  const spawn = (file, args, options) => {
    const child = new EventEmitter();
    child.pid = ++pid;
    child.unrefCount = 0;
    child.unref = () => { child.unrefCount += 1; };
    calls.push({ file, args, options, child });
    queueMicrotask(() => child.emit("spawn"));
    return child;
  };
  return { calls, spawn };
}

function statusFixture(initial = idle) {
  const calls = [];
  let snapshot = initial;
  return {
    calls,
    exec: async (file, args, options) => {
      calls.push({ file, args, options });
      if (file.endsWith("/pi-host.sh")) return piCapabilities;
      return snapshot;
    },
    set(value) { snapshot = value; },
  };
}

function production({
  entries = [],
  status = statusFixture(),
  spawned = spawnFixture(),
  timers,
} = {}) {
  const appended = [];
  const commands = new Map();
  const handlers = new Map();
  const messages = [];
  const notifications = [];
  const tools = new Map();
  const pi = {
    appendEntry(customType, data) {
      const entry = { type: "custom", customType, data };
      appended.push(entry);
      entries.push(entry);
    },
    on(name, handler) { handlers.set(name, handler); },
    registerCommand(name, value) { commands.set(name, value); },
    registerTool(value) { tools.set(value.name, value); },
    sendMessage(value) { messages.push(value); },
  };
  const extension = registerCaptainExtension(pi, {
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
    startClaims: {
      acquire() { return null; },
      release() { return false; },
    },
    resolveProject: async (_selector, current) => ({ root: current.cwd }),
    prepareWorker: async ({ root, request }) => ({ root, request, run: null }),
    attentionPollMs: 250,
    ...(timers ? {
      setTimeout: timers.setTimeout,
      clearTimeout: timers.clearTimeout,
    } : {}),
  });
  const current = context(entries);
  current.ui = {
    notify(message, level) { notifications.push({ message, level }); },
  };
  return {
    appended,
    commands,
    context: current,
    entries,
    extension,
    handlers,
    messages,
    notifications,
    pi,
    spawned,
    status,
    tools,
  };
}

function activeSnapshot({
  status = "running",
  awaiting = false,
  awaitingRequest = null,
  turns = 1,
  run = ".kimiflow/feature-x",
  providerSessionId = "pi-worker-00000001",
  workerId = null,
  controllerPid = process.pid,
} = {}) {
  return {
    schema_version: 1,
    status,
    runner: {
      schema_version: 1,
      status,
      host: "pi",
      session_id: providerSessionId,
      active_run: run,
      controller_pid: controllerPid,
      turns,
      ...(workerId ? {
        bridge: {
          schema_version: 1,
          captain_session_id: "pi-primary-0001",
          worker_id: workerId,
        },
      } : {}),
    },
    active_run: {
      present: !["done", "failed", "aborted"].includes(status),
      run,
      awaiting_user: awaiting,
      awaiting_reason: awaiting ? "Which visible behavior should win?" : null,
      awaiting_kind: awaiting ? "intake" : null,
      ...(awaitingRequest === null ? {} : {
        awaiting_request: awaitingRequest,
      }),
    },
  };
}

function fakeTimers() {
  const pending = new Map();
  const delays = [];
  let sequence = 0;
  return {
    clearTimeout(token) { pending.delete(token); },
    count() { return pending.size; },
    delays,
    async runNext() {
      const entry = pending.entries().next().value;
      assert.ok(entry);
      pending.delete(entry[0]);
      await entry[1]();
    },
    setTimeout(callback, delay) {
      const token = { id: ++sequence, unref() {} };
      pending.set(token, callback);
      delays.push(delay);
      return token;
    },
  };
}

test("primary Pi activation starts only the existing runner and returns after spawn", async () => {
  const status = statusFixture();
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
  });
  const result = await extension.activate("build feature-x", context());

  assert.equal(result.status, "activated");
  assert.equal(result.captainSessionId, "pi-primary-0001");
  assert.equal(spawned.calls.length, 1);
  const call = spawned.calls[0];
  assert.equal(call.file, "/pkg/hooks/kimiflow-runner.sh");
  assert.deepEqual(call.args.slice(0, 2), ["run", "build feature-x"]);
  assert.equal(call.args[call.args.indexOf("--adapter-command") + 1], "/pkg/hooks/pi-host.sh");
  assert.equal(call.args[call.args.indexOf("--model") + 1], "openai/gpt-5.6:high");
  assert.equal(call.options.detached, true);
  assert.equal(call.options.stdio, "ignore");
  assert.equal(call.child.unrefCount, 1);
  assert.doesNotMatch(
    JSON.stringify({ file: call.file, args: call.args, cwd: call.options.cwd }),
    /captain start|herdr|workspace|pane/,
  );
  const binding = JSON.parse(call.options.env.KIMIFLOW_PI_BRIDGE_BINDING);
  assert.equal(binding.captain_session_id, "pi-primary-0001");
  assert.equal(binding.worker_id, result.workerId);
  assert.equal(Object.hasOwn(binding, "run"), false);
});

test("natural activation tool and slash command use the same durable transaction", async () => {
  const natural = production();
  const naturalResult = await natural.tools.get("kimiflow_activate").execute(
    "tool-call-0001",
    { request: "build feature-x" },
    undefined,
    undefined,
    natural.context,
  );
  const slash = production();
  await slash.commands.get("kimiflow").handler("build feature-x", slash.context);

  assert.equal(naturalResult.details.status, "activated");
  for (const wiring of [natural, slash]) {
    assert.deepEqual(wiring.appended.map(({ customType }) => customType), [
      "kimiflow_pi_bridge_claim_v1",
    ]);
    assert.equal(wiring.spawned.calls.length, 1);
    assert.deepEqual(wiring.spawned.calls[0].args.slice(0, 2), [
      "run",
      "build feature-x",
    ]);
    assert.equal(wiring.notifications.length, 1);
    assert.match(wiring.notifications[0].message, /Pi session remains the Captain/);
  }
});

test("production Captain resolves a project and allocates its worktree before runner spawn", async () => {
  const entries = [];
  const calls = [];
  const spawned = spawnFixture();
  const handlers = new Map();
  const activeSets = [];
  const pi = {
    appendEntry(customType, data) { entries.push({ type: "custom", customType, data }); },
    getActiveTools() {
      return ["read", "grep", "find", "ls", "bash", "edit", "write", "kimiflow_activate"];
    },
    on(name, handler) { handlers.set(name, handler); },
    registerCommand() {},
    registerTool(value) { if (value.name === "kimiflow_activate") this.activate = value; },
    setActiveTools(value) { activeSets.push(value); },
  };
  const projectRoot = process.cwd();
  const workerRoot = "/tmp/kimiflow-production-worker";
  const exec = async (file, args) => {
    calls.push({ file, args });
    if (args.slice(0, 2).join(" ") === "project resolve") {
      return { schema_version: 1, status: "resolved", project: { root: projectRoot } };
    }
    if (args.slice(0, 2).join(" ") === "fleet allocate") {
      return {
        schema_version: 1,
        status: "allocated",
        root: workerRoot,
        run: ".kimiflow/isolated-feature",
      };
    }
    if (file.endsWith("/pi-host.sh")) return piCapabilities;
    return { schema_version: 1, status: "idle", runner: null, active_run: { present: false } };
  };
  registerCaptainExtension(pi, {
    root: "/pkg",
    exec,
    spawn: spawned.spawn,
    startClaims: { acquire() { return null; }, release() { return false; } },
    attentionPollMs: 250,
  });
  const current = context(entries);

  await pi.activate.execute(
    "tool-call-project",
    { request: "build isolated feature" },
    undefined,
    undefined,
    current,
  );

  assert.ok(calls.some((call) => call.args.slice(0, 2).join(" ") === "project resolve"));
  const allocation = calls.find((call) => call.args.slice(0, 2).join(" ") === "fleet allocate");
  assert.ok(allocation);
  assert.equal(
    Buffer.from(allocation.args[allocation.args.indexOf("--request-base64") + 1], "base64").toString("utf8"),
    "build isolated feature",
  );
  const runner = spawned.calls[0];
  assert.equal(runner.options.cwd, workerRoot);
  assert.equal(runner.args[runner.args.indexOf("--root") + 1], workerRoot);
  assert.match(runner.args[1], /use the exact run \.kimiflow\/isolated-feature/);
  assert.equal(activeSets.at(-1).includes("bash"), false);
  const blocked = await handlers.get("tool_call")({ toolName: "write" });
  assert.equal(blocked.block, true);
});

test("activation preserves slash, at-sign, colon, and max model selection", async () => {
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: statusFixture().exec,
    spawn: spawned.spawn,
  });
  await extension.activate("build feature-x", context([], {
    model: { provider: "cloudflare", id: "@cf/meta/llama:free" },
    thinkingLevel: "max",
  }));
  const args = spawned.calls[0].args;
  assert.equal(
    args[args.indexOf("--model") + 1],
    "cloudflare/@cf/meta/llama:free:max",
  );
});

test("one Captain can launch multiple isolated Fleet workers without blocking", async () => {
  const spawned = spawnFixture();
  const status = statusFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
    prepareWorker: async ({ request, workerId }) => ({
      root: `/tmp/kimiflow-fleet/${workerId}`,
      request,
      run: `.kimiflow/${workerId}`,
    }),
  });
  const current = context();

  const first = await extension.activate("build task a", current);
  const second = await extension.activate("build task b", current);

  assert.notEqual(first.workerId, second.workerId);
  assert.equal(extension.bindings().length, 2);
  assert.equal(spawned.calls.length, 2);
  const roots = spawned.calls.map((call) => call.args[call.args.indexOf("--root") + 1]);
  assert.equal(new Set(roots).size, 2);
  assert.ok(roots.every((root) => root.startsWith("/tmp/kimiflow-fleet/worker-")));
});

test("existing active run blocks a fresh activation before another spawn", async () => {
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: statusFixture(activeSnapshot()).exec,
    spawn: spawned.spawn,
  });
  await assert.rejects(
    extension.activate("build another feature", context()),
    /kimiflow_run_active/,
  );
  assert.equal(spawned.calls.length, 0);
});

test("missing or incompatible Pi fails before claim persistence and runner spawn", async () => {
  const entries = [];
  const spawned = spawnFixture();
  const wiring = production({
    entries,
    spawned,
    status: {
      exec: async (file) => (
        file.endsWith("/pi-host.sh")
          ? { schema_version: 1, type: "turn.failed", error_code: "provider_crash" }
          : idle
      ),
    },
  });
  await assert.rejects(
    wiring.tools.get("kimiflow_activate").execute(
      "tool-call-0001",
      { request: "build feature-x" },
      undefined,
      undefined,
      wiring.context,
    ),
    /kimiflow_pi_unavailable_or_incompatible/,
  );
  assert.equal(wiring.appended.length, 0);
  assert.equal(spawned.calls.length, 0);

  const missingWorkflowContext = production({
    entries: [],
    spawned: spawnFixture(),
    status: {
      exec: async (file) => (
        file.endsWith("/pi-host.sh")
          ? {
            ...piCapabilities,
            features: {
              ...piCapabilities.features,
              workflow_context: false,
            },
          }
          : idle
      ),
    },
  });
  await assert.rejects(
    missingWorkflowContext.tools.get("kimiflow_activate").execute(
      "tool-call-0002",
      { request: "build feature-x" },
      undefined,
      undefined,
      missingWorkflowContext.context,
    ),
    /kimiflow_pi_unavailable_or_incompatible/,
  );
  assert.equal(missingWorkflowContext.appended.length, 0);
  assert.equal(missingWorkflowContext.spawned.calls.length, 0);
});

test("a mismatched pending activation cannot adopt another active run", async () => {
  const seed = createCaptainExtension({
    root: "/pkg",
    exec: statusFixture().exec,
    spawn: spawnFixture().spawn,
  });
  const entries = [{
    type: "custom",
    customType: "kimiflow_pi_bridge_claim_v1",
    data: seed.activationClaim("build feature-x", context()),
  }];
  const wiring = production({
    entries,
    status: statusFixture(activeSnapshot({
      workerId: entries[0].data.workerId,
    })),
  });

  await assert.rejects(
    wiring.tools.get("kimiflow_activate").execute(
      "tool-call-0002",
      { request: "build feature-y" },
      undefined,
      undefined,
      wiring.context,
    ),
    /kimiflow_pending_activation_mismatch/,
  );
  assert.equal(wiring.spawned.calls.length, 0);
  assert.equal(wiring.appended.length, 0);
});

test("a new Captain safely adopts and resumes a dead Fleet runner", async () => {
  const snapshot = activeSnapshot({
    status: "transport_error",
    controllerPid: 99999999,
    workerId: "worker-existing01",
  });
  const status = statusFixture(snapshot);
  const spawned = spawnFixture();
  const adopted = [];
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
    adoptWorker: async (value) => {
      adopted.push(value);
      return { workerId: "worker-existing01" };
    },
  });

  const result = await extension.activate("continue exact run", context());

  assert.equal(result.status, "recovered");
  assert.equal(result.workerId, "worker-existing01");
  assert.equal(adopted.length, 1);
  assert.equal(spawned.calls.length, 1);
  assert.equal(spawned.calls[0].args[0], "resume");
  assert.equal(spawned.calls[0].args.includes("continue exact run"), false);
});

test("matching durable pending claim recovers the running Pi bridge without another spawn", async () => {
  const seed = createCaptainExtension({
    root: "/pkg",
    exec: statusFixture().exec,
    spawn: spawnFixture().spawn,
  });
  const entries = [{
    type: "custom",
    customType: "kimiflow_pi_bridge_claim_v1",
    data: seed.activationClaim("build feature-x", context()),
  }];
  const wiring = production({
    entries,
    status: statusFixture(activeSnapshot({
      workerId: entries[0].data.workerId,
    })),
  });
  const result = await wiring.tools.get("kimiflow_activate").execute(
    "tool-call-0001",
    { request: "build feature-x" },
    undefined,
    undefined,
    wiring.context,
  );
  assert.equal(result.details.status, "recovered");
  assert.equal(wiring.spawned.calls.length, 0);
  assert.deepEqual(wiring.appended.map(({ customType }) => customType), [
    "kimiflow_pi_bridge_binding_v1",
  ]);
});

test("a pending claim cannot recover an unrelated active runner", async () => {
  const seed = createCaptainExtension({
    root: "/pkg",
    exec: statusFixture().exec,
    spawn: spawnFixture().spawn,
  });
  const claim = seed.activationClaim("build feature-x", context());
  const entries = [{
    type: "custom",
    customType: "kimiflow_pi_bridge_claim_v1",
    data: claim,
  }];
  const wiring = production({
    entries,
    status: statusFixture(activeSnapshot({
      run: ".kimiflow/feature-y",
      providerSessionId: "pi-worker-unrelated",
      workerId: "worker-unrelated01",
    })),
  });
  await assert.rejects(
    wiring.tools.get("kimiflow_activate").execute(
      "tool-call-unrelated",
      { request: "build feature-x" },
      undefined,
      undefined,
      wiring.context,
    ),
    /kimiflow_runner_identity_invalid/,
  );
  assert.equal(wiring.appended.length, 0);
  assert.equal(wiring.spawned.calls.length, 0);
});

test("a newer durable claim supersedes an older binding for crash recovery", async () => {
  const entries = [];
  const wiring = production({ entries });
  await wiring.tools.get("kimiflow_activate").execute(
    "tool-call-0001",
    { request: "build feature-x" },
    undefined,
    undefined,
    wiring.context,
  );
  const newer = wiring.extension.activationClaim(
    "build feature-y",
    wiring.context,
  );
  entries.push({
    type: "custom",
    customType: "kimiflow_pi_bridge_claim_v1",
    data: newer,
  });

  assert.deepEqual(wiring.extension.pendingClaim(wiring.context), newer);
  const restarted = createCaptainExtension({
    root: "/pkg",
    exec: statusFixture().exec,
    spawn: spawnFixture().spawn,
  });
  assert.equal(
    restarted.restoreForSession(context(entries)).workerId,
    newer.workerId,
  );
});

test("activation and delivery are single-flight at runner boundaries", async () => {
  const status = statusFixture();
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
  });
  const activations = await Promise.allSettled([
    extension.activate("build feature-x", context()),
    extension.activate("build feature-x", context()),
  ]);
  assert.deepEqual(
    activations.map(({ status: value }) => value).sort(),
    ["fulfilled", "rejected"],
  );
  assert.match(
    String(activations.find(({ status: value }) => value === "rejected").reason),
    /kimiflow_activation_in_progress/,
  );
  assert.equal(spawned.calls.length, 1);
  await assert.rejects(
    extension.activate("build feature-x", context()),
    /kimiflow_run_active/,
  );
  assert.equal(spawned.calls.length, 1);

  const binding = extension.binding();
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    workerId: binding.workerId,
  }));
  const params = {
    workerId: binding.workerId,
    providerSessionId: "pi-worker-00000001",
    run: ".kimiflow/feature-x",
    message: "Use the simpler visible behavior.",
  };
  const deliveries = await Promise.allSettled([
    extension.deliver("reply", params, context()),
    extension.deliver("reply", params, context()),
  ]);
  assert.deepEqual(
    deliveries.map(({ status: value }) => value).sort(),
    ["fulfilled", "rejected"],
  );
  assert.match(
    String(deliveries.find(({ status: value }) => value === "rejected").reason),
    /kimiflow_delivery_in_progress/,
  );
  assert.equal(
    spawned.calls.filter(({ args }) => args[0] === "resume").length,
    1,
  );
  await assert.rejects(
    extension.deliver("reply", params, context()),
    /kimiflow_delivery_in_progress/,
  );
  assert.equal(
    spawned.calls.filter(({ args }) => args[0] === "resume").length,
    1,
  );
});

test("separate Captain extension instances share activation and delivery boundaries", async () => {
  const activationStatus = statusFixture();
  const spawned = spawnFixture();
  const first = createCaptainExtension({
    root: "/pkg",
    exec: activationStatus.exec,
    spawn: spawned.spawn,
  });
  const second = createCaptainExtension({
    root: "/pkg",
    exec: activationStatus.exec,
    spawn: spawned.spawn,
  });
  const activations = await Promise.allSettled([
    first.activate("build feature-x", context()),
    second.activate("build feature-x", context()),
  ]);
  assert.deepEqual(
    activations.map(({ status: value }) => value).sort(),
    ["fulfilled", "rejected"],
  );
  assert.equal(spawned.calls.length, 1);

  activationStatus.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    workerId: first.binding().workerId,
  }));
  await first.pollAttention({ appendEntry() {}, sendMessage() {} });
  const binding = first.binding();
  const entry = {
    type: "custom",
    customType: "kimiflow_pi_bridge_binding_v1",
    data: binding,
  };
  const deliverySpawn = spawnFixture();
  const deliveryOne = createCaptainExtension({
    root: "/pkg",
    exec: activationStatus.exec,
    spawn: deliverySpawn.spawn,
  });
  const deliveryTwo = createCaptainExtension({
    root: "/pkg",
    exec: activationStatus.exec,
    spawn: deliverySpawn.spawn,
  });
  deliveryOne.restoreForSession(context([entry]));
  deliveryTwo.restoreForSession(context([entry]));
  const params = {
    workerId: binding.workerId,
    providerSessionId: binding.providerSessionId,
    run: binding.run,
    message: "Use the accepted behavior.",
  };
  const deliveries = await Promise.allSettled([
    deliveryOne.deliver("reply", params, context()),
    deliveryTwo.deliver("reply", params, context()),
  ]);
  assert.deepEqual(
    deliveries.map(({ status: value }) => value).sort(),
    ["fulfilled", "rejected"],
  );
  assert.equal(
    deliverySpawn.calls.filter(({ args }) => args[0] === "resume").length,
    1,
  );
});

test("file start claims serialize Captain activation across Pi processes", (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-claim-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const claimsOne = createFileStartClaims();
  const claimsTwo = createFileStartClaims();
  const binding = {
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
  };

  const first = claimsOne.acquire(binding);
  assert.match(first.token, /^claim-[0-9a-f]{32}$/);
  assert.throws(
    () => claimsTwo.acquire(binding),
    /kimiflow_activation_in_progress/,
  );
  assert.equal(claimsTwo.release({ ...first, token: `claim-${"0".repeat(32)}` }), false);
  assert.equal(claimsOne.release(first), true);
  const second = claimsTwo.acquire(binding);
  assert.equal(claimsTwo.release(second), true);

  const claimPath = path.join(
    root,
    ".kimiflow",
    "session",
    "PI-BRIDGE-START-CLAIM",
  );
  mkdirSync(claimPath, { mode: 0o700 });
  writeFileSync(path.join(claimPath, "owner.json"), "", { mode: 0o600 });
  const recovered = claimsOne.acquire(binding);
  assert.match(recovered.token, /^claim-[0-9a-f]{32}$/);
  assert.equal(claimsOne.release(recovered), true);
});

test("an active Pi cleanup lease blocks successor activation", (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-cleanup-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const binding = {
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
  };
  const claims = createFileStartClaims();
  const first = claims.acquire(binding);
  assert.equal(claims.release(first), true);
  const leases = path.join(
    root,
    ".kimiflow",
    "session",
    "PI-CLEANUP-LEASES-v1",
  );
  mkdirSync(
    path.join(leases, `lease-${process.pid}-${"a".repeat(64)}`),
    { recursive: true, mode: 0o700 },
  );
  assert.throws(
    () => claims.acquire(binding),
    /kimiflow_activation_in_progress/,
  );
});

test("a dead Pi cleanup lease is recovered before successor activation", (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-stale-cleanup-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const binding = {
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
  };
  const leases = path.join(
    root,
    ".kimiflow",
    "session",
    "PI-CLEANUP-LEASES-v1",
  );
  const lease = path.join(leases, `lease-2147483647-${"b".repeat(64)}`);
  mkdirSync(lease, { recursive: true, mode: 0o700 });

  const claims = createFileStartClaims();
  const claim = claims.acquire(binding);
  assert.equal(existsSync(lease), false);
  assert.equal(claims.release(claim), true);
});

test("a Captain cannot release a claim after runner PID handoff", (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-release-")));
  const runner = spawnProcess(
    process.execPath,
    ["-e", "setInterval(() => {}, 1000)"],
    { detached: true, stdio: "ignore" },
  );
  runner.unref();
  t.after(() => {
    try {
      process.kill(-runner.pid, "SIGKILL");
    } catch {
      // Cleanup is already complete.
    }
    rmSync(root, { recursive: true, force: true });
  });
  const binding = {
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
  };
  const claims = createFileStartClaims();
  const claim = claims.acquire(binding);
  const ownerPath = path.join(
    root,
    ".kimiflow",
    "session",
    "PI-BRIDGE-START-CLAIM",
    "owner.json",
  );
  writeFileSync(ownerPath, `${JSON.stringify({
    schemaVersion: 1,
    token: claim.token,
    pid: runner.pid,
    root,
    captainSessionId: binding.captainSessionId,
    workerId: binding.workerId,
  })}\n`);

  assert.equal(claims.release(claim), false);
  assert.doesNotThrow(() => process.kill(-runner.pid, 0));
  assert.throws(
    () => createFileStartClaims().acquire(binding),
    /kimiflow_activation_in_progress/,
  );
});

test("stale runner claim waits for its cleanup process group to disappear", async (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-reap-")));
  const ready = path.join(root, "ready");
  let groupPid = null;
  t.after(() => {
    if (Number.isInteger(groupPid)) {
      try {
        process.kill(-groupPid, "SIGKILL");
      } catch {
        // The expected path already observed the complete group exit.
      }
    }
    rmSync(root, { recursive: true, force: true });
  });
  const leader = spawnProcess(
    process.execPath,
    [
      "-e",
      `
        const { spawn } = require("node:child_process");
        const { writeFileSync } = require("node:fs");
        const child = spawn(
          process.execPath,
          ["-e", "setTimeout(() => process.exit(0), 500)"],
          { stdio: "ignore" },
        );
        child.unref();
        writeFileSync(process.argv[1], String(child.pid));
      `,
      ready,
    ],
    { detached: true, stdio: "ignore" },
  );
  groupPid = leader.pid;
  await new Promise((resolvePromise, reject) => {
    leader.once("error", reject);
    leader.once("exit", resolvePromise);
  });
  assert.equal(existsSync(ready), true);
  assert.doesNotThrow(() => process.kill(-groupPid, 0));

  const claimPath = path.join(
    root,
    ".kimiflow",
    "session",
    "PI-BRIDGE-START-CLAIM",
  );
  mkdirSync(claimPath, { recursive: true, mode: 0o700 });
  writeFileSync(path.join(claimPath, "owner.json"), `${JSON.stringify({
    schemaVersion: 1,
    token: `claim-${"a".repeat(32)}`,
    pid: groupPid,
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
  })}\n`, { mode: 0o600 });
  const started = Date.now();
  const claims = createFileStartClaims();
  const acquired = claims.acquire({
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
  });
  assert.ok(Date.now() - started >= 100);
  assert.throws(
    () => process.kill(-groupPid, 0),
    (error) => error?.code === "ESRCH",
  );
  assert.equal(claims.release(acquired), true);
});

test("stale claim recovery is single-flight under a cross-process ABA race", async (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-claim-aba-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const session = path.join(root, ".kimiflow", "session");
  const claimPath = path.join(session, "PI-BRIDGE-START-CLAIM");
  mkdirSync(claimPath, { recursive: true, mode: 0o700 });
  writeFileSync(path.join(claimPath, "owner.json"), "", { mode: 0o600 });
  const readyPath = path.join(root, "ready.log");
  const barrierPath = path.join(root, "go");
  const binding = {
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
  };
  const moduleUrl = new URL("../extensions/captain.js", import.meta.url).href;
  const script = `
    import { appendFileSync, existsSync } from "node:fs";
    const { createFileStartClaims } = await import(process.argv[1]);
    appendFileSync(process.argv[3], String(process.pid) + "\\n");
    while (!existsSync(process.argv[4])) {
      await new Promise((resolve) => setTimeout(resolve, 2));
    }
    try {
      const claims = createFileStartClaims();
      const claim = claims.acquire(JSON.parse(process.argv[2]));
      await new Promise((resolve) => setTimeout(resolve, 300));
      process.stdout.write(JSON.stringify({
        acquired: true,
        released: claims.release(claim),
      }));
    } catch (error) {
      process.stdout.write(JSON.stringify({
        acquired: false,
        error: error.message,
      }));
    }
  `;
  const runChild = () => new Promise((resolvePromise, reject) => {
    const child = spawnProcess(process.execPath, [
      "--input-type=module",
      "-e",
      script,
      moduleUrl,
      JSON.stringify(binding),
      readyPath,
      barrierPath,
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
  const children = Array.from({ length: 16 }, () => runChild());
  while (
    !existsSync(readyPath)
    || readFileSync(readyPath, "utf8").trim().split("\n").length < 16
  ) {
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
  }
  writeFileSync(barrierPath, "go\n");
  const results = await Promise.all(children);
  const acquired = results.filter((result) => result.acquired);
  assert.equal(acquired.length, 1);
  assert.equal(acquired[0].released, true);
  assert.ok(results.filter((result) => !result.acquired).every(
    (result) => result.error === "kimiflow_activation_in_progress",
  ));
});

test("file claims serialize one delivery across separate Pi processes", async (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-delivery-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const readyPath = path.join(root, "ready.log");
  const spawnPath = path.join(root, "spawn.log");
  const binding = {
    schemaVersion: 1,
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
    modelSelection: "openai/gpt-5.6:high",
    run: ".kimiflow/feature-x",
    providerSessionId: "pi-worker-00000001",
    deliveryBoundary: null,
    deliveryPending: false,
    terminal: false,
  };
  const snapshot = activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
  });
  const moduleUrl = new URL("../extensions/captain.js", import.meta.url).href;
  const script = `
    import { EventEmitter } from "node:events";
    import { appendFileSync, readFileSync } from "node:fs";
    const { createCaptainExtension, createFileStartClaims } = await import(process.argv[1]);
    const root = process.argv[2];
    const readyPath = process.argv[3];
    const spawnPath = process.argv[4];
    const binding = JSON.parse(process.argv[5]);
    const snapshot = JSON.parse(process.argv[6]);
    const current = {
      cwd: root,
      sessionManager: {
        getSessionId() { return binding.captainSessionId; },
        getBranch() {
          return [{
            type: "custom",
            customType: "kimiflow_pi_bridge_binding_v1",
            data: binding,
          }];
        },
      },
    };
    const extension = createCaptainExtension({
      root: "/pkg",
      exec: async () => snapshot,
      startClaims: createFileStartClaims(),
      spawn() {
        const child = new EventEmitter();
        child.pid = process.pid;
        child.unref = () => {};
        appendFileSync(spawnPath, String(process.pid) + "\\n");
        queueMicrotask(() => child.emit("spawn"));
        return child;
      },
    });
    extension.restoreForSession(current);
    appendFileSync(readyPath, String(process.pid) + "\\n");
    while (readFileSync(readyPath, "utf8").trim().split("\\n").length < 2) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    try {
      await extension.deliver("reply", {
        workerId: binding.workerId,
        providerSessionId: binding.providerSessionId,
        run: binding.run,
        message: "Use the accepted behavior.",
      }, current, async () => {
        await new Promise((resolve) => setTimeout(resolve, 200));
      });
      process.stdout.write("queued");
    } catch (error) {
      process.stdout.write(error.message);
    }
  `;
  const runChild = () => new Promise((resolve, reject) => {
    const child = spawnProcess(process.execPath, [
      "--input-type=module",
      "-e",
      script,
      moduleUrl,
      root,
      readyPath,
      spawnPath,
      JSON.stringify(binding),
      JSON.stringify(snapshot),
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
      resolve(Buffer.concat(output).toString("utf8"));
    });
  });

  const outcomes = await Promise.all([runChild(), runChild()]);
  assert.deepEqual(outcomes.sort(), ["kimiflow_delivery_in_progress", "queued"]);
  assert.equal(readFileSync(spawnPath, "utf8").trim().split("\n").length, 1);
});

test("a dead pre-spawn delivery claim is recovered exactly once", async (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-stale-delivery-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const snapshot = activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    turns: 6,
  });
  const boundary = `sha256:${createHash("sha256").update(JSON.stringify({
    run: ".kimiflow/feature-x",
    providerSessionId: "pi-worker-00000001",
    status: "awaiting_user",
    transitionVersion: 6,
    awaitingUser: true,
  })).digest("hex")}`;
  const binding = {
    schemaVersion: 1,
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
    modelSelection: "openai/gpt-5.6:high",
    run: ".kimiflow/feature-x",
    providerSessionId: "pi-worker-00000001",
    deliveryBoundary: boundary,
    deliveryPending: true,
    terminal: false,
  };
  const moduleUrl = new URL("../extensions/captain.js", import.meta.url).href;
  const claimScript = `
    const { createFileStartClaims } = await import(process.argv[1]);
    createFileStartClaims().acquire(JSON.parse(process.argv[2]));
  `;
  await new Promise((resolvePromise, reject) => {
    const child = spawnProcess(process.execPath, [
      "--input-type=module",
      "-e",
      claimScript,
      moduleUrl,
      JSON.stringify(binding),
    ]);
    const errors = [];
    child.stderr.on("data", (chunk) => errors.push(chunk));
    child.once("error", reject);
    child.once("exit", (code) => {
      if (code === 0) resolvePromise();
      else reject(new Error(Buffer.concat(errors).toString("utf8")));
    });
  });

  const spawned = spawnFixture();
  const fileClaims = createFileStartClaims();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: async () => snapshot,
    spawn: spawned.spawn,
    startClaims: {
      ...fileClaims,
      accepted() { return true; },
    },
  });
  const current = context([{
    type: "custom",
    customType: "kimiflow_pi_bridge_binding_v1",
    data: binding,
  }], { cwd: root });
  extension.restoreForSession(current);
  const result = await extension.deliver("reply", {
    workerId: binding.workerId,
    providerSessionId: binding.providerSessionId,
    run: binding.run,
    message: "confirmed",
  }, current);
  assert.equal(result.status, "queued");
  assert.equal(spawned.calls.length, 1);
});

test("activation waits for runner claim handoff instead of a survival timer", async (t) => {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), "kimiflow-pi-handoff-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const status = statusFixture();
  const exitBeforeHandoff = (_file, _args, options) => spawnProcess(
    process.execPath,
    ["-e", "setTimeout(() => process.exit(17), 300)"],
    options,
  );
  const claims = createFileStartClaims();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: exitBeforeHandoff,
    startClaims: claims,
  });
  const current = context([], { cwd: root });
  await assert.rejects(
    extension.activate("build feature-x", current),
    /kimiflow_spawn_exited_immediately/,
  );

  const binding = {
    root,
    captainSessionId: "pi-primary-0001",
    workerId: "worker-00000001",
  };
  const recovered = claims.acquire(binding);
  assert.equal(claims.release(recovered), true);
});

test("immediate runner exits reject activation and roll back delivery", async () => {
  let calls = 0;
  const spawned = [];
  const spawn = (_file, args) => {
    const child = new EventEmitter();
    child.pid = 6000 + ++calls;
    child.unref = () => {};
    spawned.push({ args, child });
    queueMicrotask(() => {
      child.emit("spawn");
      if (args[0] === "resume" || calls === 1) {
        queueMicrotask(() => child.emit("exit", 1, null));
      }
    });
    return child;
  };
  const status = statusFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn,
  });
  await assert.rejects(
    extension.activate("build feature-x", context()),
    /kimiflow_spawn_exited_immediately/,
  );
  await extension.activate("build feature-x", context());
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    workerId: extension.binding().workerId,
  }));
  await extension.pollAttention({ appendEntry() {}, sendMessage() {} });
  const before = extension.binding();
  const persisted = [];
  await assert.rejects(
    extension.deliver("reply", {
      workerId: before.workerId,
      providerSessionId: before.providerSessionId,
      run: before.run,
      message: "Resume safely.",
    }, context(), (binding) => persisted.push(binding)),
    /kimiflow_spawn_exited_immediately/,
  );
  assert.equal(extension.binding().deliveryBoundary, before.deliveryBoundary);
  assert.equal(extension.binding().deliveryPending, false);
  assert.deepEqual(
    persisted.map(({ deliveryPending }) => deliveryPending),
    [true, false],
  );
});

test("a real immediately exiting child is never accepted as activation or delivery", async () => {
  const immediateExit = (_file, _args, options) => spawnProcess(
    process.execPath,
    ["-e", "process.exit(1)"],
    options,
  );
  const activation = createCaptainExtension({
    root: "/pkg",
    exec: statusFixture().exec,
    spawn: immediateExit,
  });
  await assert.rejects(
    activation.activate("build feature-x", context()),
    /kimiflow_spawn_exited_immediately/,
  );

  const status = statusFixture();
  const seed = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawnFixture().spawn,
  });
  await seed.activate("build feature-x", context());
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    workerId: seed.binding().workerId,
  }));
  await seed.pollAttention({ appendEntry() {}, sendMessage() {} });
  const binding = seed.binding();
  const delivery = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: immediateExit,
  });
  delivery.restoreForSession(context([{
    type: "custom",
    customType: "kimiflow_pi_bridge_binding_v1",
    data: binding,
  }]));
  const persisted = [];
  await assert.rejects(
    delivery.deliver("reply", {
      workerId: binding.workerId,
      providerSessionId: binding.providerSessionId,
      run: binding.run,
      message: "Must reach the runner.",
    }, context(), (value) => persisted.push(value)),
    /kimiflow_spawn_exited_immediately/,
  );
  assert.deepEqual(
    persisted.map(({ deliveryPending }) => deliveryPending),
    [true, false],
  );
  assert.equal(delivery.binding().deliveryBoundary, null);
});

test("a queued delivery is durable across Pi restore and keeps the activation model", async () => {
  const entries = [];
  const status = statusFixture();
  const wiring = production({ entries, status });
  await wiring.tools.get("kimiflow_activate").execute(
    "tool-call-0001",
    { request: "build feature-x" },
    undefined,
    undefined,
    wiring.context,
  );
  const binding = wiring.extension.binding();
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    turns: 7,
    workerId: binding.workerId,
  }));
  await wiring.tools.get("kimiflow_reply").execute(
    "tool-call-0002",
    {
      workerId: binding.workerId,
      providerSessionId: "pi-worker-00000001",
      run: ".kimiflow/feature-x",
      message: "Use the simpler visible behavior.",
    },
    undefined,
    undefined,
    context(entries, {
      model: { provider: "openai", id: "gpt-5.7" },
      thinkingLevel: "max",
    }),
  );
  const resume = wiring.spawned.calls.at(-1).args;
  assert.equal(resume[resume.indexOf("--model") + 1], "openai/gpt-5.6:high");
  assert.deepEqual(wiring.appended.map(({ customType }) => customType), [
    "kimiflow_pi_bridge_claim_v1",
    "kimiflow_pi_bridge_binding_v1",
    "kimiflow_pi_bridge_binding_v1",
    "kimiflow_pi_bridge_binding_v1",
  ]);

  const restartedSpawn = spawnFixture();
  const restarted = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: restartedSpawn.spawn,
  });
  const restoredContext = context(entries);
  const restored = restarted.restoreForSession(restoredContext);
  await assert.rejects(
    restarted.deliver("reply", {
      workerId: restored.workerId,
      providerSessionId: "pi-worker-00000001",
      run: ".kimiflow/feature-x",
      message: "Duplicate reply.",
    }, restoredContext),
    /kimiflow_delivery_in_progress/,
  );
  assert.equal(restartedSpawn.calls.length, 0);
});

test("a persistence crash after spawn keeps delivery pending and blocks replay", async () => {
  const status = statusFixture();
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
  });
  await extension.activate("build feature-x", context());
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    turns: 8,
    workerId: extension.binding().workerId,
  }));
  const entries = [];
  await extension.pollAttention({
    appendEntry(customType, data) {
      entries.push({ type: "custom", customType, data });
    },
    sendMessage() {},
  });
  const binding = extension.binding();
  await assert.rejects(
    extension.deliver("reply", {
      workerId: binding.workerId,
      providerSessionId: binding.providerSessionId,
      run: binding.run,
      message: "Use the durable path.",
    }, context(), (data) => {
      if (data.deliveryBoundary !== null && data.deliveryPending === false) {
        throw new Error("injected_persistence_failure");
      }
      entries.push({
        type: "custom",
        customType: "kimiflow_pi_bridge_binding_v1",
        data,
      });
    }),
    /injected_persistence_failure/,
  );
  assert.equal(extension.binding().deliveryPending, true);

  const restartedSpawn = spawnFixture();
  const restarted = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: restartedSpawn.spawn,
    startClaims: {
      acquire() { throw new Error("kimiflow_activation_in_progress"); },
      release() { return false; },
    },
  });
  const restored = restarted.restoreForSession(context(entries));
  assert.equal(restored.deliveryPending, true);
  await assert.rejects(
    restarted.deliver("reply", {
      workerId: restored.workerId,
      providerSessionId: restored.providerSessionId,
      run: restored.run,
      message: "Do not replay.",
    }, context()),
    /kimiflow_delivery_in_progress/,
  );
  assert.equal(restartedSpawn.calls.length, 0);
});

test("attention is derived from runner state, stable per turn, and restored per Pi session", async () => {
  const status = statusFixture();
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
  });
  await extension.activate("build feature-x", context());
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    awaitingRequest: [
      "Product flow entry: Open Pi.",
      "User interaction: Ask Pi to use Kimiflow.",
      "Visible delegation outcome: Show the run.",
      "Unchanged path: Keep direct Codex unchanged.",
      "Done scenario: Report verified completion.",
    ].join("\n"),
    turns: 4,
    workerId: extension.binding().workerId,
  }));
  const messages = [];
  const pi = { sendMessage(value) { messages.push(value); } };
  const first = await extension.pollAttention(pi);
  const duplicate = await extension.pollAttention(pi);
  assert.equal(first.announced, 1);
  assert.equal(duplicate.announced, 0);
  assert.equal(messages.length, 1);
  const attention = messages[0].details;
  assert.equal(attention.kind, "question");
  assert.equal(attention.transition_version, 4);
  assert.equal(messages[0].content, attention.question);
  assert.match(attention.question, /Product flow entry: Open Pi\./);
  assert.match(attention.question, /Done scenario: Report verified completion\./);

  const binding = extension.binding();
  const restored = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawnFixture().spawn,
  });
  restored.restoreForSession(context([
    {
      type: "custom",
      customType: "kimiflow_pi_bridge_binding_v1",
      data: binding,
    },
    {
      type: "custom_message",
      customType: "kimiflow_attention",
      content: messages[0].content,
      details: attention,
    },
  ]));
  const restoredMessages = [];
  assert.equal((await restored.pollAttention({
    sendMessage(value) { restoredMessages.push(value); },
  })).announced, 0);
  assert.equal(restoredMessages.length, 0);
});

test("a failed attention send is retried on the unchanged transition", async () => {
  const status = statusFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawnFixture().spawn,
  });
  await extension.activate("build feature-x", context());
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    turns: 5,
    workerId: extension.binding().workerId,
  }));
  await assert.rejects(
    extension.pollAttention({
      appendEntry() {},
      sendMessage() { throw new Error("pi_send_failed"); },
    }),
    /pi_send_failed/,
  );
  const messages = [];
  const retried = await extension.pollAttention({
    appendEntry() {},
    sendMessage(value) { messages.push(value); },
  });
  assert.equal(retried.announced, 1);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].details.kind, "question");
});

test("a Pi-0.82.1 void attention send is still announced exactly once", async () => {
  const status = statusFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawnFixture().spawn,
  });
  await extension.activate("build feature-x", context());
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    turns: 6,
    workerId: extension.binding().workerId,
  }));
  const messages = [];
  const pi0821 = {
    sendMessage(value) {
      messages.push(value);
      return undefined;
    },
  };
  const first = await extension.pollAttention(pi0821);
  const deduplicated = await extension.pollAttention(pi0821);
  assert.equal(first.announced, 1);
  assert.equal(deduplicated.announced, 0);
  assert.equal(messages.length, 1);
});

test("a provisional Captain announces an exact fast terminal receipt", async () => {
  const status = statusFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawnFixture().spawn,
  });
  await extension.activate("build feature-x", context());
  status.set(activeSnapshot({
    status: "done",
    workerId: extension.binding().workerId,
  }));
  const entries = [];
  const messages = [];
  const pi = {
    appendEntry(customType, data) { entries.push({ customType, data }); },
    sendMessage(value) { messages.push(value); },
  };
  const result = await extension.pollAttention(pi);
  const retry = await extension.pollAttention(pi);
  assert.equal(result.announced, 1);
  assert.equal(retry.announced, 0);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].details.kind, "completion");
  assert.match(messages[0].content, /^✓ Kimiflow · /);
  assert.equal(extension.binding().run, ".kimiflow/feature-x");
  assert.equal(extension.binding().terminal, true);
  assert.equal(entries.at(-1).data.terminal, true);
});

test("a provisional Captain surfaces transport failure before Run creation", async () => {
  const status = statusFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawnFixture().spawn,
  });
  await extension.activate("build feature-x", context());
  const binding = extension.binding();
  const failed = {
    schema_version: 1,
    status: "transport_error",
    runner: {
      session_id: "pi-worker-00000001",
      active_run: null,
      turns: 0,
      diagnostic_code: "herdr_turn_invalid",
      bridge: {
        schema_version: 1,
        captain_session_id: binding.captainSessionId,
        worker_id: binding.workerId,
      },
    },
    active_run: { present: false },
  };
  status.set(failed);
  assert.deepEqual(await extension.status(), failed);
  const messages = [];
  const first = await extension.pollAttention({
    appendEntry() {},
    sendMessage(value) { messages.push(value); },
  });
  assert.equal(first.announced, 1);
  assert.equal(messages.length, 1);
  const attention = messages[0].details;
  assert.equal(attention.kind, "failure");
  assert.equal(attention.run, null);
  assert.equal(attention.provider_session_id, "pi-worker-00000001");
  assert.equal(attention.diagnostic_code, "herdr_turn_invalid");
});

test("a live intake wait outranks a stale transport error and keeps its worker resumable", async () => {
  const status = statusFixture();
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
  });
  await extension.activate("build feature-x", context());
  const provisional = extension.binding();
  status.set(activeSnapshot({
    status: "transport_error",
    awaiting: true,
    awaitingRequest: "Confirm the simple product flow.",
    workerId: provisional.workerId,
  }));
  assert.equal((await extension.status()).status, "awaiting_user");
  const messages = [];
  const observed = await extension.pollAttention({
    appendEntry() {},
    sendMessage(value) { messages.push(value); },
  });
  assert.equal(observed.snapshot.status, "awaiting_user");
  assert.equal(messages.length, 1);
  assert.equal(messages[0].details.kind, "question");
  assert.equal(messages[0].content, "Confirm the simple product flow.");
  assert.equal(extension.binding().terminal, false);
  assert.equal(
    status.calls.filter(({ args }) => args?.[0] === "terminate").length,
    0,
  );

  const binding = extension.binding();
  const result = await extension.deliver("reply", {
    workerId: binding.workerId,
    providerSessionId: binding.providerSessionId,
    run: binding.run,
    message: "Confirmed.",
  }, context());
  assert.equal(result.status, "queued");
  assert.equal(
    spawned.calls.filter(({ args }) => args[0] === "resume").length,
    1,
  );
});

test("a provisional failure tombstone allows a later unrelated activation", async () => {
  const entries = [];
  const status = statusFixture();
  const first = production({ entries, status });
  await first.tools.get("kimiflow_activate").execute(
    "tool-call-feature-x",
    { request: "build feature-x" },
    undefined,
    undefined,
    first.context,
  );
  const binding = first.extension.binding();
  status.set({
    schema_version: 1,
    status: "transport_error",
    runner: {
      session_id: "pi-worker-00000001",
      active_run: null,
      turns: 0,
      bridge: {
        schema_version: 1,
        captain_session_id: binding.captainSessionId,
        worker_id: binding.workerId,
      },
    },
    active_run: { present: false },
  });
  await first.extension.pollAttention(first.pi);
  await first.extension.pollAttention(first.pi);
  const terminal = entries.at(-1);
  assert.equal(terminal.customType, "kimiflow_pi_bridge_binding_v1");
  assert.equal(terminal.data.run, null);
  assert.equal(terminal.data.providerSessionId, null);
  assert.equal(terminal.data.terminal, true);

  status.set(idle);
  const second = production({ entries, status });
  const result = await second.tools.get("kimiflow_activate").execute(
    "tool-call-feature-y",
    { request: "build feature-y" },
    undefined,
    undefined,
    second.context,
  );
  assert.equal(result.details.status, "activated");
  assert.deepEqual(second.spawned.calls[0].args.slice(0, 2), [
    "run",
    "build feature-y",
  ]);
});

test("a restored binding never adopts a later unrelated runner identity", async () => {
  const status = statusFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawnFixture().spawn,
  });
  await extension.activate("build feature-x", context());
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    workerId: extension.binding().workerId,
  }));
  await extension.pollAttention({ appendEntry() {}, sendMessage() {} });
  const binding = extension.binding();

  const laterStatus = statusFixture(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    run: ".kimiflow/feature-y",
    providerSessionId: "pi-worker-00000002",
  }));
  const restored = createCaptainExtension({
    root: "/pkg",
    exec: laterStatus.exec,
    spawn: spawnFixture().spawn,
  });
  restored.restoreForSession(context([{
    type: "custom",
    customType: "kimiflow_pi_bridge_binding_v1",
    data: binding,
  }]));
  const messages = [];
  assert.equal((await restored.pollAttention({
    appendEntry() {},
    sendMessage(value) { messages.push(value); },
  })).announced, 0);
  assert.equal(messages.length, 0);
  await assert.rejects(
    restored.status(),
    /kimiflow_runner_identity_mismatch/,
  );
  await assert.rejects(
    restored.deliver("reply", {
      workerId: binding.workerId,
      providerSessionId: binding.providerSessionId,
      run: binding.run,
      message: "Must not reach feature-y.",
    }, context()),
    /kimiflow_runner_identity_mismatch/,
  );
});

test("reply resumes the exact runner boundary; mismatched or live steering is rejected", async () => {
  const status = statusFixture();
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
  });
  await extension.activate("build feature-x", context());
  const binding = extension.binding();
  status.set(activeSnapshot({
    status: "awaiting_user",
    awaiting: true,
    workerId: extension.binding().workerId,
  }));

  const result = await extension.deliver("reply", {
    workerId: binding.workerId,
    providerSessionId: "pi-worker-00000001",
    run: ".kimiflow/feature-x",
    message: "Use the simpler visible behavior.",
  }, context());
  assert.equal(result.status, "queued");
  const resume = spawned.calls.at(-1).args;
  assert.equal(resume[0], "resume");
  assert.equal(resume[resume.indexOf("--message") + 1], "Use the simpler visible behavior.");

  await assert.rejects(extension.deliver("reply", {
    workerId: "worker-wrong0001",
    providerSessionId: "pi-worker-00000001",
    run: ".kimiflow/feature-x",
    message: "wrong",
  }, context()), /worker_mismatch/);

  status.set(activeSnapshot({ status: "running", awaiting: false }));
  await assert.rejects(extension.deliver("steer", {
    workerId: binding.workerId,
    providerSessionId: "pi-worker-00000001",
    run: ".kimiflow/feature-x",
    message: "change direction now",
  }, context()), /steer_requires_resumable_boundary/);
});

test("a dead runner controller exposes interruption and permits exact continuation", async () => {
  const status = statusFixture();
  const spawned = spawnFixture();
  const extension = createCaptainExtension({
    root: "/pkg",
    exec: status.exec,
    spawn: spawned.spawn,
  });
  await extension.activate("build feature-x", context());
  const provisional = extension.binding();
  status.set(activeSnapshot({
    workerId: provisional.workerId,
  }));
  await extension.pollAttention({ appendEntry() {}, sendMessage() {} });
  const binding = extension.binding();
  status.set(activeSnapshot({
    workerId: binding.workerId,
    controllerPid: 2147483647,
  }));

  assert.equal((await extension.status()).status, "interrupted");
  const messages = [];
  const attention = await extension.pollAttention({
    appendEntry() {},
    sendMessage(value) { messages.push(value); },
  });
  assert.equal(attention.snapshot.status, "interrupted");
  assert.equal(messages[0].details.kind, "failure");
  const result = await extension.deliver("reply", {
    workerId: binding.workerId,
    providerSessionId: binding.providerSessionId,
    run: binding.run,
    message: "Continue the exact interrupted run.",
  }, context());
  assert.equal(result.status, "queued");
  assert.equal(spawned.calls.at(-1).args[0], "resume");
});

test("status, watcher, shutdown, and session restore stay bounded", async () => {
  const timers = fakeTimers();
  const wiring = production({ timers });
  await wiring.commands.get("kimiflow").handler("build feature-x", wiring.context);
  assert.equal(timers.count(), 1);
  assert.deepEqual(timers.delays, [250]);
  await wiring.commands.get("kimiflow-status").handler("", wiring.context);
  assert.match(wiring.notifications.at(-1).message, /^Kimiflow: starting/);

  wiring.handlers.get("session_shutdown")();
  assert.equal(timers.count(), 0);
  assert.equal(wiring.extension.binding(), null);
  await wiring.handlers.get("session_start")({}, wiring.context);
  assert.equal(wiring.extension.binding()?.captainSessionId, "pi-primary-0001");
  assert.equal(timers.count(), 1);
});

test("Pi tools expose bounded dependency-free schemas", () => {
  const wiring = production();
  assert.deepEqual([...wiring.tools.keys()], [
    "kimiflow_activate",
    "kimiflow_project",
    "kimiflow_reply",
    "kimiflow_steer",
  ]);
  assert.equal(wiring.tools.get("kimiflow_activate").parameters["~kind"], "Object");
  for (const name of ["kimiflow_reply", "kimiflow_steer"]) {
    const schema = wiring.tools.get(name).parameters;
    assert.equal(schema.additionalProperties, false);
    assert.deepEqual([...schema.required].sort(), [
      "message",
      "providerSessionId",
      "run",
      "workerId",
    ]);
  }
});
