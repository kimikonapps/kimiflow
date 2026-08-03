import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import extension, { FirstMateAdapter, REQUIRED_FIRSTMATE_SCRIPTS, locateFirstMateRoot } from "../extensions/kimiflow-crew.js";

async function fixture() {
  const base = await mkdtemp(path.join(os.tmpdir(), "kimiflow-crew-"));
  const root = path.join(base, "firstmate");
  const project = path.join(base, "project");
  await Promise.all([mkdir(path.join(root, "bin"), { recursive: true }), mkdir(path.join(root, "data"), { recursive: true }), mkdir(project)]);
  for (const script of REQUIRED_FIRSTMATE_SCRIPTS) {
    await writeFile(path.join(root, "bin", script), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  }
  await writeFile(path.join(root, "data", "projects.md"), "", "utf8");
  return { base, root, project };
}

function fakeWatcher(lines = "watcher: started pid=42 (beacon fresh)\n") {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {};
  queueMicrotask(() => child.stdout.emit("data", Buffer.from(lines)));
  return child;
}

function failedWatcher() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {};
  queueMicrotask(() => child.emit("close", 1, null));
  return child;
}

test("extension registration is dormant", () => {
  const registered = [];
  extension({ registerTool: (tool) => registered.push(tool), on: () => {} });
  assert.equal(registered.length, 1);
  assert.equal(registered[0].name, "kimiflow_crew");
});

test("bounded discovery finds a capability-compatible sibling", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  assert.equal(await locateFirstMateRoot({ cwd: f.project, env: { HOME: f.base, KIMIFLOW_FIRSTMATE_ROOT: f.root } }), f.root);
});

test("activation stays explicit and requires Herdr", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const adapter = new FirstMateAdapter({ cwd: f.project, env: { HOME: f.base, KIMIFLOW_FIRSTMATE_ROOT: f.root } });
  assert.equal((await adapter.activate()).code, "not_in_herdr");
  assert.equal(adapter.root, null);
});

test("activation refuses an unverified FirstMate lock", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const run = async (file) => {
    if (file === "git") return { code: 0, stdout: `${f.project}\n`, stderr: "" };
    if (file === "sh") return { code: 0, stdout: "", stderr: "" };
    return { code: 0, stdout: "READ-ONLY SESSION\n", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, env: { HOME: f.base, HERDR_ENV: "1", KIMIFLOW_FIRSTMATE_ROOT: f.root }, run });
  assert.equal((await adapter.activate()).code, "firstmate_lock_unavailable");
});

test("activation preserves wake records drained by FirstMate session start", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const run = async (file) => {
    if (file === "git") return { code: 0, stdout: `${f.project}\n`, stderr: "" };
    if (file.endsWith("fm-session-start.sh")) {
      return {
        code: 0,
        stdout: `lock acquired: harness pid 7\n\nWAKE QUEUE\n--------------------------------\n1\tsignal\t${f.root}/state/worker-one.status\tsignal: ${f.root}/state/worker-one.status\n\n================================\nCONTEXT\n================================\n`,
        stderr: "",
      };
    }
    return { code: 0, stdout: "/usr/bin/tool\n", stderr: "" };
  };
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: { HOME: f.base, HERDR_ENV: "1", KIMIFLOW_FIRSTMATE_ROOT: f.root },
    run,
    spawn: () => fakeWatcher(),
  });
  const result = await adapter.activate();
  assert.equal(result.code, "activated");
  assert.match(result.startupWakes, /worker-one\.status/);
  adapter.shutdown();
});

test("activation failure still returns wake records already drained by FirstMate", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const run = async (file) => {
    if (file === "git") return { code: 0, stdout: `${f.project}\n`, stderr: "" };
    if (file.endsWith("fm-session-start.sh")) {
      return {
        code: 0,
        stdout: `lock acquired: harness pid 7\n\nWAKE QUEUE\n--------------------------------\n1\tsignal\t${f.root}/state/worker-two.status\tsignal: ${f.root}/state/worker-two.status\n\n================================\nCONTEXT\n================================\n`,
        stderr: "",
      };
    }
    return { code: 0, stdout: "/usr/bin/tool\n", stderr: "" };
  };
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: { HOME: f.base, HERDR_ENV: "1", KIMIFLOW_FIRSTMATE_ROOT: f.root },
    run,
    spawn: () => failedWatcher(),
  });
  const result = await adapter.activate();
  assert.equal(result.code, "watcher_failed");
  assert.match(result.startupWakes, /worker-two\.status/);
});

test("spawn is green only after the exact endpoint is readable", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const calls = [];
  const run = async (file, args) => {
    calls.push([path.basename(file), args]);
    if (file === "git") return { code: 0, stdout: `${f.project}\n`, stderr: "" };
    if (file === "sh") return { code: 0, stdout: "", stderr: "" };
    if (file.endsWith("fm-session-start.sh")) return { code: 0, stdout: "lock acquired: harness pid 7\n", stderr: "" };
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-brief.sh")) {
      const brief = path.join(f.root, "data", args[0], "brief.md");
      await mkdir(path.dirname(brief), { recursive: true });
      await writeFile(brief, "# Task\n{TASK}\n\nReplace `{TASK}` after scaffolding.\n", "utf8");
      return { code: 0, stdout: "scaffolded\n", stderr: "" };
    }
    if (file.endsWith("fm-spawn.sh")) return { code: 0, stdout: "spawned\n", stderr: "" };
    if (file.endsWith("fm-peek.sh")) return { code: 0, stdout: "pi v0.83.0\n", stderr: "" };
    if (file.endsWith("fm-crew-state.sh")) return { code: 0, stdout: "state: working · source: pane · harness busy (busy pi-ext)\n", stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: { HOME: f.base, HERDR_ENV: "1", KIMIFLOW_FIRSTMATE_ROOT: f.root },
    run,
    spawn: () => fakeWatcher(),
    sleep: async () => {},
  });
  assert.equal((await adapter.activate()).code, "activated");
  const result = await adapter.spawnWorker({ task: "run-7-2-build", brief: "Build the confirmed Run 7.2.", kind: "ship" });
  assert.equal(result.code, "worker_reachable");
  const renderedBrief = await readFile(path.join(f.root, "data", "run-7-2-build", "brief.md"), "utf8");
  assert.match(renderedBrief, /already confirmed/);
  assert.doesNotMatch(renderedBrief, /\{TASK\}/);
  assert(calls.some(([name, args]) => name === "fm-spawn.sh" && args.includes("--backend") && args.includes("herdr")));
  adapter.shutdown();
});

test("a fresh FirstMate home gets its first local-only project entry", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  await rm(path.join(f.root, "data", "projects.md"));
  const run = async (file) => {
    if (file.endsWith("fm-project-mode.sh")) {
      const registry = await readFile(path.join(f.root, "data", "projects.md"), "utf8");
      return { code: 0, stdout: registry.includes("[local-only]") ? "local-only off\n" : "no-mistakes off\n", stderr: "" };
    }
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, env: {}, run, sleep: async () => {} });
  adapter.root = f.root;
  adapter.projectRoot = f.project;
  const result = await adapter.ensureProjectMode();
  assert.equal(result.code, "project_mode_ready");
  assert.match(await readFile(path.join(f.root, "data", "projects.md"), "utf8"), /- project \[local-only\]/);
});

test("spawn never claims recovery without an endpoint", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const brief = path.join(f.root, "data", "existing", "brief.md");
  await mkdir(path.dirname(brief), { recursive: true });
  await writeFile(brief, "confirmed", "utf8");
  const run = async (file) => {
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-spawn.sh")) return { code: 0, stdout: "recovered\n", stderr: "" };
    if (file.endsWith("fm-peek.sh")) return { code: 1, stdout: "", stderr: "missing" };
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, env: {}, run, sleep: async () => {} });
  adapter.root = f.root;
  adapter.projectRoot = f.project;
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.spawnWorker({ task: "existing" })).code, "spawn_unverified");
});

test("spawn clears Pi project trust before returning reachable", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const brief = path.join(f.root, "data", "trusted-worker", "brief.md");
  await mkdir(path.dirname(brief), { recursive: true });
  await writeFile(brief, "confirmed", "utf8");
  let peeks = 0;
  let trustAccepted = false;
  const run = async (file, args) => {
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-spawn.sh")) return { code: 0, stdout: "spawned\n", stderr: "" };
    if (file.endsWith("fm-peek.sh")) {
      peeks += 1;
      if (peeks <= 4) return { code: 0, stdout: "pi v0.83.0\n", stderr: "" };
      return { code: 0, stdout: trustAccepted ? "Working...\n" : "Trust project folder?\n", stderr: "" };
    }
    if (file.endsWith("fm-crew-state.sh")) {
      const source = trustAccepted ? "pi-ext" : "fm-spawn";
      return { code: 0, stdout: `state: working · source: pane · harness busy (busy ${source})\n`, stderr: "" };
    }
    if (file.endsWith("fm-send.sh") && args.join(" ") === "trusted-worker --key Enter") {
      trustAccepted = true;
      return { code: 0, stdout: "sent\n", stderr: "" };
    }
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, env: {}, run, sleep: async () => {} });
  adapter.root = f.root;
  adapter.projectRoot = f.project;
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.spawnWorker({ task: "trusted-worker" })).code, "worker_reachable");
  assert.equal(trustAccepted, true);
  assert(peeks >= 6);
});

test("an existing task cannot silently accept a different confirmed packet", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const brief = path.join(f.root, "data", "existing-task", "brief.md");
  await mkdir(path.dirname(brief), { recursive: true });
  await writeFile(brief, "Confirmed Kimiflow work packet:\nold contract\n\nKimiflow worker boundary:\n", "utf8");
  let spawned = false;
  const run = async (file) => {
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-spawn.sh")) spawned = true;
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, env: {}, run, sleep: async () => {} });
  adapter.root = f.root;
  adapter.projectRoot = f.project;
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.spawnWorker({ task: "existing-task", brief: "new contract" })).code, "brief_conflict");
  assert.equal(spawned, false);
});

test("a verified resume clears stale terminal wake suppression", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const brief = path.join(f.root, "data", "resumed-task", "brief.md");
  await mkdir(path.dirname(brief), { recursive: true });
  await writeFile(brief, "Confirmed Kimiflow work packet:\nsame contract\n\nKimiflow worker boundary:\n", "utf8");
  const run = async (file) => {
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-spawn.sh")) return { code: 0, stdout: "resumed\n", stderr: "" };
    if (file.endsWith("fm-peek.sh")) return { code: 0, stdout: "pi v0.83.0\n", stderr: "" };
    if (file.endsWith("fm-crew-state.sh")) return { code: 0, stdout: "state: working · source: pane · harness busy (busy pi-ext)\n", stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, env: {}, run, sleep: async () => {} });
  adapter.root = f.root;
  adapter.projectRoot = f.project;
  adapter.watcher = fakeWatcher();
  adapter.terminalTasks.add("resumed-task");
  adapter.pendingWakeTasks.add("resumed-task");
  assert.equal((await adapter.spawnWorker({ task: "resumed-task", brief: "same contract" })).code, "worker_reachable");
  assert.equal(adapter.terminalTasks.has("resumed-task"), false);
  assert.equal(adapter.pendingWakeTasks.has("resumed-task"), false);
});

test("watcher re-arms before returning a crew wake to Main", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const children = [];
  const messages = [];
  const adapter = new FirstMateAdapter({
    pi: { sendUserMessage: (message, options) => messages.push([message, options]) },
    spawn: () => {
      const child = fakeWatcher();
      children.push(child);
      return child;
    },
  });
  adapter.root = f.root;
  adapter.projectRoot = f.project;
  assert.equal((await adapter.startWatcher(true)).code, "watcher_ready");
  children[0].stdout.emit("data", Buffer.from("signal: task=worker-one\n"));
  children[0].emit("close", 0, null);
  for (let attempt = 0; attempt < 20 && messages.length === 0; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.equal(children.length, 2);
  assert.equal(messages.length, 1);
  assert.match(messages[0][0], /action=drain/);
  assert.deepEqual(messages[0][1], { deliverAs: "followUp" });
  adapter.shutdown();
});

test("terminal worker wakes are suppressed after Main reads final state", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const messages = [];
  const adapter = new FirstMateAdapter({
    pi: { sendUserMessage: (message) => messages.push(message) },
    run: async (file) => file.endsWith("fm-crew-state.sh")
      ? { code: 0, stdout: "state: done · source: status-log\n", stderr: "" }
      : { code: 0, stdout: "", stderr: "" },
    spawn: () => fakeWatcher(),
  });
  adapter.root = f.root;
  adapter.projectRoot = f.project;
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.status({ task: "worker-one" })).code, "status");
  const closed = adapter.watcher;
  await adapter.handleWatcherClose(closed, {
    stdout: `signal: ${f.root}/state/worker-one.status\n`, stderr: "", code: 0, signal: null,
  });
  assert.equal(messages.length, 0);
  adapter.shutdown();
});

test("verified steering releases terminal wake suppression", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const messages = [];
  const adapter = new FirstMateAdapter({
    pi: { sendUserMessage: (message) => messages.push(message) },
    run: async (file) => file.endsWith("fm-crew-state.sh")
      ? { code: 0, stdout: "state: failed · source: status-log\n", stderr: "" }
      : { code: 0, stdout: "sent\n", stderr: "" },
    spawn: () => fakeWatcher(),
  });
  adapter.root = f.root;
  adapter.projectRoot = f.project;
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.status({ task: "worker-one" })).code, "status");
  assert.equal((await adapter.send({ task: "worker-one", message: "Retry with the confirmed correction." })).code, "sent");
  const closed = adapter.watcher;
  await adapter.handleWatcherClose(closed, {
    stdout: `signal: ${f.root}/state/worker-one.status\n`, stderr: "", code: 0, signal: null,
  });
  assert.equal(messages.length, 1);
  adapter.shutdown();
});

test("teardown requires the exact task confirmation", async () => {
  const adapter = new FirstMateAdapter({ run: async () => ({ code: 0, stdout: "done", stderr: "" }) });
  adapter.root = "/firstmate";
  adapter.projectRoot = "/project";
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.teardown({ task: "worker-one", confirmation: "yes" })).code, "teardown_confirmation_required");
});
