import assert from "node:assert/strict";
import { execFile as execFileCallback } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";

import extension, {
  FirstMateAdapter,
  REQUIRED_FIRSTMATE_CALM_FILES,
  REQUIRED_FIRSTMATE_SCRIPTS,
  locateFirstMateRoot,
} from "../extensions/kimiflow-crew.js";

const execFile = promisify(execFileCallback);

async function realRun(file, args, options = {}) {
  try {
    const result = await execFile(file, args, { cwd: options.cwd, env: options.env, encoding: "utf8" });
    return { code: 0, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
  } catch (error) {
    return { code: Number.isInteger(error.code) ? error.code : 1, stdout: error.stdout ?? "", stderr: error.stderr ?? error.message };
  }
}

async function fixture() {
  const base = await mkdtemp(path.join(os.tmpdir(), "kimiflow-crew-"));
  const root = path.join(base, "firstmate");
  const project = path.join(base, "project");
  await Promise.all([mkdir(path.join(root, "bin"), { recursive: true }), mkdir(path.join(root, "data"), { recursive: true }), mkdir(project)]);
  for (const script of REQUIRED_FIRSTMATE_SCRIPTS) {
    const harnessContract = script === "fm-spawn.sh" ? "# __MODELFLAG__ __EFFORTFLAG__ __PIEXT__ __OPINPUT__ __BRIEF__\n" : "";
    await writeFile(path.join(root, "bin", script), `#!/bin/sh\n${harnessContract}exit 0\n`, { mode: 0o755 });
  }
  for (const relative of REQUIRED_FIRSTMATE_CALM_FILES) {
    const target = path.join(root, relative);
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, "// stock FirstMate Calm fixture\n", "utf8");
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

function activationGit(args, project) {
  if (args.includes("ls-files")) return { code: 0, stdout: "", stderr: "" };
  if (args.includes("--git-common-dir")) return { code: 0, stdout: `${path.join(project, ".git")}\n`, stderr: "" };
  if (args.includes("check-ignore")) return { code: 0, stdout: "", stderr: "" };
  return { code: 0, stdout: `${project}\n`, stderr: "" };
}

function mainEnv(f, task = "kimiflow-main") {
  return {
    HOME: f.base,
    HERDR_ENV: "1",
    KIMIFLOW_FIRSTMATE_ROOT: f.root,
    KIMIFLOW_CREW_ROLE: "main",
    KIMIFLOW_MAIN_TASK: task,
    KIMIFLOW_SUPERVISED_PROJECT: f.project,
  };
}

function mainHome(f, task = "kimiflow-main") {
  return path.join(f.project, ".kimiflow", "session", "FIRSTMATE-MAIN-v1", task);
}

async function createMainActiveRun(f, slug = "test-main-run") {
  const directory = path.join(f.project, ".kimiflow", slug);
  await mkdir(directory, { recursive: true });
  await writeFile(path.join(directory, "STATE.md"), "Status: active\n", "utf8");
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

test("Main crew activation requires exactly one standard Active Run", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const adapter = new FirstMateAdapter({ cwd: f.project });
  assert.equal((await adapter.verifyMainActiveRun(f.project)).code, "main_active_run_required");
  await createMainActiveRun(f);
  assert.equal((await adapter.verifyMainActiveRun(f.project)).code, "main_active_run_ready");
  await createMainActiveRun(f, "second-main-run");
  assert.equal((await adapter.verifyMainActiveRun(f.project)).code, "main_active_run_ambiguous");
});

test("activation refuses an unverified FirstMate lock", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const run = async (file, args = []) => {
    if (file === "git") return activationGit(args, f.project);
    if (file === "sh") return { code: 0, stdout: "", stderr: "" };
    if (file.endsWith("resolve-verbosity.sh")) return { code: 0, stdout: "balanced\n", stderr: "" };
    return { code: 0, stdout: "READ-ONLY SESSION\n", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, env: { HOME: f.base, HERDR_ENV: "1", KIMIFLOW_FIRSTMATE_ROOT: f.root }, run });
  assert.equal((await adapter.activate()).code, "firstmate_lock_unavailable");
  assert.equal(adapter.root, null);
  assert.equal(adapter.runtimeHome, null);
});

test("activation preserves wake records drained by FirstMate session start", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const run = async (file, args = []) => {
    if (file === "git") return activationGit(args, f.project);
    if (file.endsWith("resolve-verbosity.sh")) return { code: 0, stdout: "balanced\n", stderr: "" };
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
  const run = async (file, args = []) => {
    if (file === "git") return activationGit(args, f.project);
    if (file.endsWith("resolve-verbosity.sh")) return { code: 0, stdout: "balanced\n", stderr: "" };
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
  let launcherSource = "";
  const run = async (file, args) => {
    calls.push([path.basename(file), args]);
    if (file === "git") return activationGit(args, f.project);
    if (file === "sh") return { code: 0, stdout: "", stderr: "" };
    if (file.endsWith("resolve-verbosity.sh")) return { code: 0, stdout: "balanced\n", stderr: "" };
    if (file.endsWith("fm-session-start.sh")) return {
      code: 0,
      stdout: "lock acquired: harness pid 7\nWAKE QUEUE\n--------------------------------\n(no queued wakes)\nPI_WATCH_EXTENSION: setup detail\n================================\nCONTEXT\n================================\n",
      stderr: "",
    };
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-brief.sh")) {
      const brief = path.join(mainHome(f), "data", args[0], "brief.md");
      await mkdir(path.dirname(brief), { recursive: true });
      await writeFile(brief, "# Task\n{TASK}\n\nReplace `{TASK}` after scaffolding.\n", "utf8");
      return { code: 0, stdout: "scaffolded\n", stderr: "" };
    }
    if (file.endsWith("fm-spawn.sh") && args.includes("--harness")) {
      const harness = args[args.indexOf("--harness") + 1];
      launcherSource = await readFile(harness.split(" ", 1)[0], "utf8");
      return { code: 0, stdout: "spawned\n", stderr: "" };
    }
    if (file.endsWith("fm-peek.sh")) return { code: 0, stdout: "pi v0.83.0\n", stderr: "" };
    if (file.endsWith("fm-crew-state.sh")) return { code: 0, stdout: "state: working · source: pane · harness busy (busy pi-ext)\n", stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: mainEnv(f),
    run,
    spawn: () => fakeWatcher(),
    sleep: async () => {},
  });
  await createMainActiveRun(f);
  assert.equal((await adapter.activate()).code, "activated");
  const result = await adapter.spawnWorker({ task: "run-7-2-build", brief: "Build the confirmed Run 7.2.", kind: "ship" });
  assert.equal(result.code, "worker_reachable");
  const renderedBrief = await readFile(path.join(mainHome(f), "data", "run-7-2-build", "brief.md"), "utf8");
  assert.match(renderedBrief, /already confirmed/);
  assert.doesNotMatch(renderedBrief, /\{TASK\}/);
  assert(calls.some(([name, args]) => name === "fm-spawn.sh" && args.includes("--backend") && args.includes("herdr")));
  const spawnCall = calls.find(([name, args]) => name === "fm-spawn.sh" && args.includes("--harness"));
  const harness = spawnCall[1][spawnCall[1].indexOf("--harness") + 1];
  assert.match(harness, /^\/[^ ]+\/pi /);
  assert.match(launcherSource, /export KIMIFLOW_CREW_ROLE='worker'/);
  assert.match(launcherSource, /export KIMIFLOW_WORKER_VERBOSITY='balanced'/);
  assert.match(harness, /-e __PIEXT__/);
  assert.match(harness, /launch-brief < __BRIEF__/);
  adapter.shutdown();
});

test("quiet Main launches a quiet worker with stock FirstMate Calm", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const calls = [];
  let launcherSource = "";
  const run = async (file, args = []) => {
    calls.push([file, args]);
    if (file === "git") return activationGit(args, f.project);
    if (file.endsWith("resolve-verbosity.sh")) return { code: 0, stdout: "quiet\n", stderr: "" };
    if (file.endsWith("fm-session-start.sh")) return { code: 0, stdout: "lock acquired: harness pid 7\n", stderr: "" };
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-brief.sh")) {
      const brief = path.join(mainHome(f), "data", args[0], "brief.md");
      await mkdir(path.dirname(brief), { recursive: true });
      await writeFile(brief, "# Task\n{TASK}\n\nReplace `{TASK}` after scaffolding.\n", "utf8");
      return { code: 0, stdout: "scaffolded\n", stderr: "" };
    }
    if (file.endsWith("fm-spawn.sh") && args.includes("--harness")) {
      const harness = args[args.indexOf("--harness") + 1];
      launcherSource = await readFile(harness.split(" ", 1)[0], "utf8");
      return { code: 0, stdout: "spawned\n", stderr: "" };
    }
    if (file.endsWith("fm-peek.sh")) return { code: 0, stdout: "pi v0.83.0\n", stderr: "" };
    if (file.endsWith("fm-crew-state.sh")) return { code: 0, stdout: "state: working · source: pane · harness busy (busy pi-ext)\n", stderr: "" };
    return { code: 0, stdout: "/usr/bin/tool\n", stderr: "" };
  };
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: {
      ...mainEnv(f),
      KIMIFLOW_WORKER_VERBOSITY: "quiet",
      PI_CODING_AGENT_DIR: path.join(f.base, "pi-agent"),
      PI_PROVIDER: "openai-codex",
      PI_MODEL: "gpt-5.6-sol",
      PI_REASONING_LEVEL: "high",
    },
    run,
    spawn: () => fakeWatcher(),
    sleep: async () => {},
  });
  await createMainActiveRun(f);
  const activated = await adapter.activate({ verbosity: "balanced" });
  assert.equal(activated.code, "activated");
  assert.equal(activated.presentation, "quiet+firstmate-calm");
  assert.equal("startupWakes" in activated, false);
  assert.equal(await readFile(path.join(mainHome(f), "config", "calm"), "utf8"), "on\n");
  assert.equal((await adapter.spawnWorker({ task: "quiet-worker", brief: "Build quietly." })).code, "worker_reachable");
  const spawnCall = calls.find(([file, args]) => file.endsWith("fm-spawn.sh") && args.includes("--harness"));
  const resolveCall = calls.find(([file]) => file.endsWith("resolve-verbosity.sh"));
  assert.deepEqual(resolveCall[1], ["get"]);
  const harness = spawnCall[1][spawnCall[1].indexOf("--harness") + 1];
  assert.match(harness, /^\/[^ ]+\/pi /);
  assert.match(launcherSource, /export KIMIFLOW_CREW_ROLE='worker'/);
  assert.match(launcherSource, /export KIMIFLOW_WORKER_VERBOSITY='quiet'/);
  assert.match(launcherSource, /export PI_CODING_AGENT_DIR='/);
  assert.match(launcherSource, /printf 'on\\n' > "\$FM_HOME\/config\/calm"/);
  assert.match(harness, new RegExp(path.join(f.root, REQUIRED_FIRSTMATE_CALM_FILES[0]).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(harness, /-e __PIEXT__/);
  assert.match(harness, /"\$\(__OPINPUT__ encode launch-brief < __BRIEF__\)"$/);
  assert.deepEqual(spawnCall[1].slice(spawnCall[1].indexOf("--model"), spawnCall[1].indexOf("--model") + 4), ["--model", "openai-codex/gpt-5.6-sol", "--effort", "high"]);
  adapter.shutdown();
});

test("quiet activation fails before spawning when stock FirstMate Calm is incomplete", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  await rm(path.join(f.root, REQUIRED_FIRSTMATE_CALM_FILES.at(-1)));
  let sessionStarted = false;
  const run = async (file, args = []) => {
    if (file === "git") return activationGit(args, f.project);
    if (file.endsWith("resolve-verbosity.sh")) return { code: 0, stdout: "quiet\n", stderr: "" };
    if (file.endsWith("fm-session-start.sh")) sessionStarted = true;
    return { code: 0, stdout: "/usr/bin/tool\n", stderr: "" };
  };
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: { HOME: f.base, HERDR_ENV: "1", KIMIFLOW_FIRSTMATE_ROOT: f.root },
    run,
  });
  assert.equal((await adapter.activate()).code, "firstmate_calm_unavailable");
  assert.equal(sessionStarted, false);
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
  adapter.runtimeHome = f.root;
  adapter.projectRoot = f.project;
  const result = await adapter.ensureProjectMode();
  assert.equal(result.code, "project_mode_ready");
  assert.match(await readFile(path.join(f.root, "data", "projects.md"), "utf8"), /- project \[local-only\]/);
});

test("a recovered legacy home is normalized from direct-PR to local-only", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  await writeFile(path.join(f.root, "data", "projects.md"), "- project [direct-PR] - legacy\n", "utf8");
  const run = async (file) => {
    if (!file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "", stderr: "" };
    const registry = await readFile(path.join(f.root, "data", "projects.md"), "utf8");
    return { code: 0, stdout: registry.includes("[local-only]") ? "local-only off\n" : "direct-PR off\n", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, run });
  adapter.root = f.root;
  adapter.runtimeHome = f.root;
  adapter.projectRoot = f.project;
  assert.equal((await adapter.ensureProjectMode()).mode, "local-only");
  assert.match(await readFile(path.join(f.root, "data", "projects.md"), "utf8"), /- project \[local-only\]/);
});

test("pre-contract research is accepted only as a visible read-only Scout", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const calls = [];
  let launcherSource = "";
  const run = async (file, args = []) => {
    calls.push([file, args]);
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-brief.sh")) {
      const brief = path.join(f.root, "data", args[0], "brief.md");
      await mkdir(path.dirname(brief), { recursive: true });
      await writeFile(brief, "# Task\n{TASK}\n\nReplace `{TASK}` after scaffolding.\n", "utf8");
      return { code: 0, stdout: "scaffolded\n", stderr: "" };
    }
    if (file.endsWith("fm-spawn.sh")) {
      const harness = args[args.indexOf("--harness") + 1];
      launcherSource = await readFile(harness.split(" ", 1)[0], "utf8");
      return { code: 0, stdout: "spawned\n", stderr: "" };
    }
    if (file.endsWith("fm-peek.sh")) return { code: 0, stdout: "pi v0.83.0\n", stderr: "" };
    if (file.endsWith("fm-crew-state.sh")) return { code: 0, stdout: "state: working · source: pane · harness busy (busy pi-ext)\n", stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: {},
    run,
    sleep: async () => {},
  });
  adapter.root = f.root;
  adapter.runtimeHome = f.root;
  adapter.role = "main";
  adapter.modeCapability = "current";
  adapter.projectRoot = f.project;
  adapter.workerVerbosity = "balanced";
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.spawnWorker({ task: "invalid-research-ship", stage: "research", kind: "ship", brief: "Research." })).code, "research_ship_forbidden");
  const result = await adapter.spawnWorker({ task: "research-scout", stage: "research", kind: "scout", brief: "Compare two current primary sources." });
  assert.equal(result.code, "worker_reachable");
  assert.equal(result.stage, "research");
  const rendered = await readFile(path.join(f.root, "data", "research-scout", "brief.md"), "utf8");
  assert.match(rendered, /product contract not final/);
  assert.match(rendered, /do not implement, choose product scope or ask the user/);
  const spawnCall = calls.find(([file]) => file.endsWith("fm-spawn.sh"));
  assert(spawnCall[1].includes("--scout"));
  const scoutHarness = spawnCall[1][spawnCall[1].indexOf("--harness") + 1];
  assert.match(scoutHarness, /^\/[^ ]+\/pi /);
  assert.match(launcherSource, /export KIMIFLOW_WORKER_VERBOSITY='balanced'/);
  assert.equal(spawnCall[1].includes("--mode"), false);
  assert.equal(spawnCall[1].includes("--yolo"), false);
  assert.equal((await adapter.spawnWorker({ task: "confirmed-ship", stage: "confirmed", kind: "ship", brief: "Implement the confirmed packet." })).code, "worker_reachable");
  const shipCall = calls.filter(([file]) => file.endsWith("fm-spawn.sh")).at(-1);
  assert.deepEqual(shipCall[1].slice(shipCall[1].indexOf("--mode"), shipCall[1].indexOf("--mode") + 4), ["--mode", "local-only", "--yolo", "off"]);
});

test("Main reads a completed Scout report through the read-only adapter boundary", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  await mkdir(path.join(f.root, "state"), { recursive: true });
  await mkdir(path.join(f.root, "data", "review-scout"), { recursive: true });
  await writeFile(path.join(f.root, "state", "review-scout.meta"), "kind=scout\n", "utf8");
  await writeFile(path.join(f.root, "data", "review-scout", "report.md"), "NONE\n", "utf8");
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: {},
    run: async (file) => file.endsWith("fm-crew-state.sh")
      ? { code: 0, stdout: "state: done · source: status-log\n", stderr: "" }
      : { code: 0, stdout: "", stderr: "" },
  });
  adapter.root = f.root;
  adapter.runtimeHome = f.root;
  adapter.role = "main";
  adapter.projectRoot = f.project;
  adapter.workerVerbosity = "quiet";
  adapter.watcher = fakeWatcher();
  const result = await adapter.report({ task: "review-scout" });
  assert.equal(result.code, "scout_report");
  assert.equal(result.report, "NONE");
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
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: {},
    run,
    sleep: async () => {},
  });
  adapter.root = f.root;
  adapter.runtimeHome = f.root;
  adapter.role = "main";
  adapter.projectRoot = f.project;
  adapter.workerVerbosity = "balanced";
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
  adapter.runtimeHome = f.root;
  adapter.role = "main";
  adapter.projectRoot = f.project;
  adapter.workerVerbosity = "balanced";
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
  adapter.runtimeHome = f.root;
  adapter.role = "main";
  adapter.projectRoot = f.project;
  adapter.workerVerbosity = "balanced";
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
  adapter.runtimeHome = f.root;
  adapter.role = "main";
  adapter.projectRoot = f.project;
  adapter.workerVerbosity = "balanced";
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
  adapter.runtimeHome = f.root;
  adapter.role = "main";
  adapter.projectRoot = f.project;
  adapter.workerVerbosity = "quiet";
  assert.equal((await adapter.startWatcher(true)).code, "watcher_ready");
  children[0].stdout.emit("data", Buffer.from("signal: task=worker-one\n"));
  children[0].stderr.emit("data", Buffer.from("/firstmate/bin/backends/herdr.sh: line 3052: printf: write error: Broken pipe\n"));
  children[0].emit("close", 0, null);
  for (let attempt = 0; attempt < 20 && messages.length === 0; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.equal(children.length, 2);
  assert.equal(messages.length, 1);
  assert.match(messages[0][0], /action=drain/);
  assert.match(messages[0][0], /named child task/);
  assert.doesNotMatch(messages[0][0], /Keep all user discussion/);
  assert.doesNotMatch(messages[0][0], /Broken pipe|watcher: started/);
  assert.deepEqual(messages[0][1], { deliverAs: "followUp" });
  adapter.shutdown();
});

test("Captain watcher routes Main attention back to the Captain conversation", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const children = [];
  const messages = [];
  const adapter = new FirstMateAdapter({
    pi: { sendUserMessage: (message) => messages.push(message) },
    env: { KIMIFLOW_CREW_ROLE: "captain" },
    spawn: () => {
      const child = fakeWatcher();
      children.push(child);
      return child;
    },
  });
  adapter.root = f.root;
  adapter.runtimeHome = f.root;
  adapter.role = "captain";
  adapter.mainTask = "kimiflow-main";
  adapter.projectRoot = f.project;
  assert.equal((await adapter.startWatcher(true)).code, "watcher_ready");
  children[0].stdout.emit("data", Buffer.from(`signal: ${f.root}/state/kimiflow-main.status\n`));
  children[0].emit("close", 0, null);
  for (let attempt = 0; attempt < 20 && messages.length === 0; attempt += 1) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(messages.length, 1);
  assert.match(messages[0], /status for the Kimiflow Main/);
  assert.match(messages[0], /Keep all user discussion in this Captain session/);
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
  adapter.runtimeHome = f.root;
  adapter.role = "main";
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
  adapter.runtimeHome = f.root;
  adapter.role = "main";
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
  adapter.runtimeHome = "/firstmate";
  adapter.role = "main";
  adapter.projectRoot = "/project";
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.teardown({ task: "worker-one", confirmation: "yes" })).code, "teardown_confirmation_required");
});

test("Captain refuses Main teardown until Main-owned FirstMate children are gone", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const task = "run-main";
  const worktree = path.join(f.base, "main-worktree");
  const home = path.join(f.project, ".kimiflow", "session", "FIRSTMATE-CAPTAIN-v1");
  const nestedState = path.join(worktree, ".kimiflow", "session", "FIRSTMATE-MAIN-v1", task, "state");
  await mkdir(path.join(home, "state"), { recursive: true });
  await mkdir(nestedState, { recursive: true });
  await writeFile(path.join(home, "state", `${task}.meta`), `worktree=${worktree}\n`, "utf8");
  await writeFile(path.join(nestedState, "review.meta"), "kind=scout\n", "utf8");
  let tornDown = false;
  let mainState = "working";
  const adapter = new FirstMateAdapter({
    run: async (file) => {
      if (file.endsWith("fm-crew-state.sh")) return { code: 0, stdout: `state: ${mainState} · source: status-log\n`, stderr: "" };
      if (file.endsWith("fm-teardown.sh")) tornDown = true;
      return { code: 0, stdout: "done\n", stderr: "" };
    },
  });
  adapter.root = f.root;
  adapter.runtimeHome = home;
  adapter.role = "captain";
  adapter.mainTask = task;
  adapter.projectRoot = f.project;
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.teardown({ task, confirmation: task })).code, "main_not_quiescent");
  mainState = "done";
  assert.equal((await adapter.teardown({ task, confirmation: task })).code, "main_children_not_torn_down");
  assert.equal(tornDown, false);
  await rm(path.join(nestedState, "review.meta"));
  assert.equal((await adapter.teardown({ task, confirmation: task })).code, "torn_down");
  assert.equal(tornDown, true);
  assert.equal(adapter.mainTask, null);
});

test("role matrix keeps Captain, Main and Worker ownership disjoint", async () => {
  const captain = new FirstMateAdapter({ env: { KIMIFLOW_CREW_ROLE: "captain" } });
  const main = new FirstMateAdapter({ env: { KIMIFLOW_CREW_ROLE: "main" } });
  const worker = new FirstMateAdapter({ env: { KIMIFLOW_CREW_ROLE: "worker" } });
  assert.deepEqual((await captain.execute({ action: "role" })).role, "captain");
  assert.equal((await captain.execute({ action: "spawn", task: "child" })).code, "role_action_forbidden");
  assert.equal((await captain.execute({ action: "integrate", task: "child" })).code, "role_action_forbidden");
  assert.equal((await main.execute({ action: "start_main", task: "nested" })).code, "role_action_forbidden");
  assert.equal((await worker.execute({ action: "activate" })).code, "role_action_forbidden");
  assert.equal((await worker.execute({ action: "spawn", task: "nested" })).code, "role_action_forbidden");
});

test("FirstMate Root and per-project/per-task Homes remain separate and stable", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const otherProject = path.join(f.base, "other-project");
  await mkdir(otherProject);
  const captain = new FirstMateAdapter({ cwd: f.project, env: { KIMIFLOW_FIRSTMATE_HOME: path.join(f.base, "shared") } });
  assert.equal(captain.resolveRuntimeHome("captain", f.project, f.project), path.join(f.project, ".kimiflow", "session", "FIRSTMATE-CAPTAIN-v1"));
  assert.notEqual(
    captain.resolveRuntimeHome("captain", f.project, f.project),
    captain.resolveRuntimeHome("captain", otherProject, otherProject),
  );
  captain.mainTask = "main-a";
  const mainA = captain.resolveRuntimeHome("main", f.project, f.project);
  assert.equal(mainA, captain.resolveRuntimeHome("main", f.project, f.project));
  captain.mainTask = "main-b";
  assert.notEqual(mainA, captain.resolveRuntimeHome("main", f.project, f.project));
  assert.notEqual(mainA, f.root);
  assert.notEqual(mainA, path.join(f.base, "shared"));
});

test("runtime homes reject symlinked project-state ancestors", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const outside = path.join(f.base, "outside");
  await mkdir(outside);
  await symlink(outside, path.join(f.project, ".kimiflow"));
  const adapter = new FirstMateAdapter();
  const result = await adapter.initializeRuntimeHome(
    path.join(f.project, ".kimiflow", "session", "FIRSTMATE-CAPTAIN-v1"),
    f.project,
  );
  assert.equal(result.code, "firstmate_home_unavailable");
});

test("Captain recovers exactly one Main from FirstMate metadata and immutable launch input", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const home = path.join(f.project, ".kimiflow", "session", "FIRSTMATE-CAPTAIN-v1");
  const task = "run-7-2-main";
  await mkdir(path.join(home, "state"), { recursive: true });
  await mkdir(path.join(home, "data", task), { recursive: true });
  await writeFile(path.join(home, "state", `${task}.meta`), "worktree=/tmp/main\n", "utf8");
  await writeFile(path.join(home, "data", task, "launch-input.json"), `${JSON.stringify({
    schemaVersion: 1,
    task,
    supervisedProject: f.project,
    request: "Starte Run 7.2.",
    plan: "",
  })}\n`, "utf8");
  const adapter = new FirstMateAdapter();
  adapter.role = "captain";
  adapter.projectRoot = f.project;
  adapter.runtimeHome = home;
  assert.equal((await adapter.recoverCaptainMainTask()).code, "captain_main_recovered");
  assert.equal(adapter.mainTask, task);
  assert.equal(adapter.validateTaskAuthority(task), null);
  assert.equal(adapter.validateTaskAuthority("other-main").code, "captain_task_forbidden");
});

test("CLI capability probe accepts only coherent legacy or current shapes", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const legacy = new FirstMateAdapter({ run: async () => ({ code: 0, stdout: "usage: command <task>\n", stderr: "" }) });
  assert.equal((await legacy.probeFirstMateCapabilities(f.root)).capability, "legacy");
  const current = new FirstMateAdapter({ run: async (file) => ({
    code: 0,
    stdout: file.endsWith("fm-brief.sh") ? "usage: brief --mode <mode>\n" : "usage: spawn --mode <mode> --yolo <on|off>\n",
    stderr: "",
  }) });
  assert.equal((await current.probeFirstMateCapabilities(f.root)).capability, "current");
  const partial = new FirstMateAdapter({ run: async (file) => ({
    code: 0,
    stdout: file.endsWith("fm-brief.sh") ? "usage: brief --mode <mode>\n" : "usage: spawn --mode <mode>\n",
    stderr: "",
  }) });
  assert.equal((await partial.probeFirstMateCapabilities(f.root)).code, "firstmate_mode_capability_mismatch");
  await writeFile(path.join(f.root, "bin", "fm-spawn.sh"), "#!/bin/sh\n# changed raw harness\n", { mode: 0o755 });
  assert.equal((await legacy.probeFirstMateCapabilities(f.root)).code, "firstmate_harness_capability_mismatch");
});

test("Captain rejects tracked .kimiflow and establishes a repo-local exclude otherwise", async (t) => {
  const base = await mkdtemp(path.join(os.tmpdir(), "kimiflow-exclude-"));
  t.after(() => rm(base, { recursive: true, force: true }));
  const project = path.join(base, "project");
  await mkdir(project);
  await execFile("git", ["init", "-b", "trunk", project]);
  await execFile("git", ["-C", project, "config", "user.email", "test@example.com"]);
  await execFile("git", ["-C", project, "config", "user.name", "Kimiflow Test"]);
  await writeFile(path.join(project, "README.md"), "fixture\n");
  await execFile("git", ["-C", project, "add", "README.md"]);
  await execFile("git", ["-C", project, "commit", "-m", "base"]);
  const adapter = new FirstMateAdapter({ cwd: project, env: process.env, run: realRun });
  assert.equal((await adapter.prepareCaptainProject(project)).code, "captain_project_ready");
  assert.match(await readFile(path.join(project, ".git", "info", "exclude"), "utf8"), /^\/\.kimiflow\/$/m);
  await mkdir(path.join(project, ".kimiflow"), { recursive: true });
  await writeFile(path.join(project, ".kimiflow", "tracked.txt"), "bad\n");
  await execFile("git", ["-C", project, "add", "-f", ".kimiflow/tracked.txt"]);
  assert.equal((await adapter.prepareCaptainProject(project)).code, "tracked_kimiflow_forbidden");
});

test("Captain starts one control-only Main from an immutable launch snapshot", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  const home = path.join(f.base, "captain-home");
  await mkdir(path.join(home, "data"), { recursive: true });
  await mkdir(path.join(home, "state"), { recursive: true });
  const calls = [];
  let launcherSource = "";
  const run = async (file, args = [], options = {}) => {
    calls.push([file, args, options]);
    if (file === "git") {
      if (args.includes("symbolic-ref") && args.includes("refs/remotes/origin/HEAD")) return { code: 1, stdout: "", stderr: "" };
      if (args.includes("show-ref") && args.includes("refs/heads/main")) return { code: 0, stdout: "", stderr: "" };
      if (args.includes("show-ref")) return { code: 1, stdout: "", stderr: "" };
    }
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-brief.sh")) {
      const brief = path.join(home, "data", args[0], "brief.md");
      await mkdir(path.dirname(brief), { recursive: true });
      await writeFile(brief, "# Task\n{TASK}\n\n# Setup\nOld ship setup.\n\n# Rules\nOld ship rules.\n\n# Definition of done\nShip {TASK}.\n", "utf8");
      return { code: 0, stdout: "scaffolded\n", stderr: "" };
    }
    if (file.endsWith("fm-spawn.sh")) {
      const harness = args[args.indexOf("--harness") + 1];
      launcherSource = await readFile(harness.split(" ", 1)[0], "utf8");
      await writeFile(path.join(home, "state", "run-7-2-main.meta"), "worktree=/tmp/control-main\nwindow=w1:t2\n", "utf8");
      return { code: 0, stdout: "spawned\n", stderr: "" };
    }
    if (file.endsWith("fm-peek.sh")) return { code: 0, stdout: "pi v0.83.0\n", stderr: "" };
    if (file.endsWith("fm-crew-state.sh")) return { code: 0, stdout: "state: working · source: pane · harness busy (busy pi-ext)\n", stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({
    cwd: f.project,
    env: {},
    run,
    sleep: async () => {},
  });
  adapter.root = f.root;
  adapter.runtimeHome = home;
  adapter.role = "captain";
  adapter.projectRoot = f.project;
  adapter.controlRoot = f.project;
  adapter.modeCapability = "legacy";
  adapter.workerVerbosity = "quiet";
  adapter.watcher = fakeWatcher();
  const started = await adapter.execute(
    { action: "start_main", task: "run-7-2-main", request: "Starte Run 7.2.", plan: "Use the agreed numbered plan." },
    undefined,
    { model: { provider: "openai-codex", id: "gpt-5.6-sol" }, thinkingLevel: "low" },
  );
  assert.equal(started.code, "main_reachable");
  assert.equal(started.mainWorktree, "/tmp/control-main");
  assert.equal(started.mainWindow, "w1:t2");
  const snapshot = await readFile(path.join(home, "data", "run-7-2-main", "launch-input.json"), "utf8");
  assert.match(snapshot, /Starte Run 7\.2/);
  const brief = await readFile(path.join(home, "data", "run-7-2-main", "brief.md"), "utf8");
  assert.match(brief, /durable control container only/);
  assert.match(brief, /Never write product bytes or existing run\/plan artifacts there/);
  assert.match(brief, /sole control-state exception/);
  assert.match(brief, /All product changes must be delegated/);
  assert.match(brief, /After appending `needs-decision:` or `blocked:`, end the turn immediately/);
  assert.match(brief, /After a child returns `worker_reachable`, end the turn/);
  assert.match(brief, /safely tear down every exact child/);
  assert.match(brief, /^Delivery contract: mode=local-only$/m);
  assert.doesNotMatch(brief, /^Delivery contract: mode=local-only\./m);
  assert.doesNotMatch(brief, /Ship the control task description/);
  const spawnCall = calls.find(([file, args]) => file.endsWith("fm-spawn.sh") && args.includes("--harness"));
  const harness = spawnCall[1][spawnCall[1].indexOf("--harness") + 1];
  assert.match(harness, /^\/[^ ]+\/pi /);
  assert.match(launcherSource, /export KIMIFLOW_CREW_ROLE='main'/);
  assert.match(launcherSource, /export KIMIFLOW_MAIN_TASK='run-7-2-main'/);
  assert.match(launcherSource, /export KIMIFLOW_WORKER_VERBOSITY='quiet'/);
  assert.match(launcherSource, /export FM_HOME="\$PWD\/\.kimiflow\/session\/FIRSTMATE-MAIN-v1\/run-7-2-main"/);
  assert.match(launcherSource, /printf 'on\\n' > "\$FM_HOME\/config\/calm"/);
  assert.doesNotMatch(harness, /\$\(pwd/);
  assert.match(harness, /-e __PIEXT__/);
  assert.match(harness, /launch-brief < __BRIEF__/);
  assert.deepEqual(spawnCall[1].slice(spawnCall[1].indexOf("--model"), spawnCall[1].indexOf("--model") + 4), ["--model", "openai-codex/gpt-5.6-sol", "--effort", "low"]);
  const briefCallIndex = calls.findIndex(([file]) => file.endsWith("fm-brief.sh"));
  const markerCallIndex = calls.findIndex(([file, args]) => file === "git" && args.includes("refs/remotes/origin/HEAD"));
  assert(briefCallIndex >= 0 && markerCallIndex > briefCallIndex);
  assert.equal((await adapter.startMain({ task: "run-7-2-main", request: "Different request." })).code, "main_launch_input_conflict");
  assert.equal((await adapter.startMain({ task: "other-main", brief: "Alias is not canonical." })).code, "main_request_required");
});

test("a pre-metadata Main spawn failure rolls back its newly owned default marker", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.base, { recursive: true, force: true }));
  await execFile("git", ["init", "-b", "trunk", f.project]);
  await execFile("git", ["-C", f.project, "config", "user.email", "test@example.com"]);
  await execFile("git", ["-C", f.project, "config", "user.name", "Kimiflow Test"]);
  await writeFile(path.join(f.project, "README.md"), "base\n");
  await execFile("git", ["-C", f.project, "add", "README.md"]);
  await execFile("git", ["-C", f.project, "commit", "-m", "base"]);
  const home = path.join(f.project, ".kimiflow", "session", "FIRSTMATE-CAPTAIN-v1");
  await mkdir(path.join(home, "data"), { recursive: true });
  await mkdir(path.join(home, "state"), { recursive: true });
  const run = async (file, args = [], options = {}) => {
    if (file === "git") return realRun(file, args, options);
    if (file.endsWith("fm-project-mode.sh")) return { code: 0, stdout: "local-only off\n", stderr: "" };
    if (file.endsWith("fm-brief.sh")) {
      const brief = path.join(home, "data", args[0], "brief.md");
      await mkdir(path.dirname(brief), { recursive: true });
      await writeFile(brief, "# Task\n{TASK}\n\n# Setup\nSetup.\n", "utf8");
      return { code: 0, stdout: "scaffolded\n", stderr: "" };
    }
    if (file.endsWith("fm-spawn.sh")) return { code: 1, stdout: "", stderr: "spawn refused" };
    return { code: 0, stdout: "", stderr: "" };
  };
  const adapter = new FirstMateAdapter({ cwd: f.project, run });
  adapter.root = f.root;
  adapter.runtimeHome = home;
  adapter.role = "captain";
  adapter.projectRoot = f.project;
  adapter.controlRoot = f.project;
  adapter.modeCapability = "legacy";
  adapter.workerVerbosity = "balanced";
  adapter.watcher = fakeWatcher();
  const result = await adapter.startMain({ task: "failed-main", request: "Start the bounded run." });
  assert.equal(result.code, "main_spawn_failed");
  assert.equal(result.markerRollback.code, "default_branch_marker_removed");
  assert.equal(adapter.mainTask, null);
  assert.notEqual((await realRun("git", ["-C", f.project, "symbolic-ref", "refs/remotes/origin/HEAD"])).code, 0);
});

test("owned no-remote origin/HEAD marker is non-dangling, stock-compatible and reversible", async (t) => {
  const base = await mkdtemp(path.join(os.tmpdir(), "kimiflow-default-marker-"));
  t.after(() => rm(base, { recursive: true, force: true }));
  const project = path.join(base, "project");
  const home = path.join(base, "home");
  await mkdir(project);
  await mkdir(path.join(home, "state"), { recursive: true });
  await execFile("git", ["init", "-b", "trunk", project]);
  await execFile("git", ["-C", project, "config", "user.email", "test@example.com"]);
  await execFile("git", ["-C", project, "config", "user.name", "Kimiflow Test"]);
  await writeFile(path.join(project, "README.md"), "base\n");
  await execFile("git", ["-C", project, "add", "README.md"]);
  await execFile("git", ["-C", project, "commit", "-m", "base"]);
  const adapter = new FirstMateAdapter({ cwd: project, env: process.env, run: realRun });
  adapter.projectRoot = project;
  adapter.runtimeHome = home;
  const ready = await adapter.ensureDefaultBranchMarker();
  assert.equal(ready.marker, "owned");
  assert.equal(ready.target, "refs/heads/trunk");
  const short = await execFile("git", ["-C", project, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]);
  assert.equal(short.stdout.trim(), "trunk");
  const commit = await execFile("git", ["-C", project, "rev-parse", "refs/remotes/origin/HEAD"]);
  assert.match(commit.stdout.trim(), /^[0-9a-f]{40}$/);
  assert.equal((await adapter.cleanupOwnedDefaultBranchMarker()).code, "default_branch_marker_removed");
  const gone = await realRun("git", ["-C", project, "symbolic-ref", "refs/remotes/origin/HEAD"]);
  assert.notEqual(gone.code, 0);
});

test("remote default is authoritative for a nonstandard branch and mismatch fails closed", async (t) => {
  const base = await mkdtemp(path.join(os.tmpdir(), "kimiflow-remote-default-"));
  t.after(() => rm(base, { recursive: true, force: true }));
  const project = path.join(base, "project");
  const remote = path.join(base, "remote.git");
  const home = path.join(base, "home");
  await mkdir(project);
  await mkdir(path.join(home, "state"), { recursive: true });
  await execFile("git", ["init", "--bare", remote]);
  await execFile("git", ["init", "-b", "trunk", project]);
  await execFile("git", ["-C", project, "config", "user.email", "test@example.com"]);
  await execFile("git", ["-C", project, "config", "user.name", "Kimiflow Test"]);
  await writeFile(path.join(project, "README.md"), "base\n");
  await execFile("git", ["-C", project, "add", "README.md"]);
  await execFile("git", ["-C", project, "commit", "-m", "base"]);
  await execFile("git", ["-C", project, "remote", "add", "origin", remote]);
  await execFile("git", ["-C", project, "push", "origin", "trunk:trunk", "trunk:other"]);
  await execFile("git", ["-C", remote, "symbolic-ref", "HEAD", "refs/heads/trunk"]);
  const adapter = new FirstMateAdapter({ cwd: project, env: process.env, run: realRun });
  adapter.projectRoot = project;
  adapter.runtimeHome = home;
  const ready = await adapter.ensureDefaultBranchMarker();
  assert.equal(ready.marker, "owned");
  assert.equal(ready.authority, "origin");
  assert.equal((await adapter.cleanupOwnedDefaultBranchMarker()).code, "default_branch_marker_removed");
  await execFile("git", ["-C", remote, "symbolic-ref", "HEAD", "refs/heads/other"]);
  assert.equal((await adapter.ensureDefaultBranchMarker()).code, "default_branch_remote_mismatch");
});

test("owned trunk marker works with the installed stock fm-merge-local helper", async (t) => {
  const stockRoot = process.env.KIMIFLOW_TEST_FIRSTMATE_ROOT || "/Users/sr/Documents/VIBE CODING/firstmate";
  const helper = path.join(stockRoot, "bin", "fm-merge-local.sh");
  try {
    await execFile("test", ["-x", helper]);
  } catch {
    t.skip("a stock FirstMate checkout is not available in this environment");
    return;
  }
  const base = await mkdtemp(path.join(os.tmpdir(), "kimiflow-stock-merge-"));
  t.after(() => rm(base, { recursive: true, force: true }));
  const project = path.join(base, "project");
  const home = path.join(base, "home");
  await mkdir(project);
  await mkdir(path.join(home, "state"), { recursive: true });
  await mkdir(path.join(home, "config"), { recursive: true });
  await execFile("git", ["init", "-b", "trunk", project]);
  await execFile("git", ["-C", project, "config", "user.email", "test@example.com"]);
  await execFile("git", ["-C", project, "config", "user.name", "Kimiflow Test"]);
  await writeFile(path.join(project, "README.md"), "base\n");
  await execFile("git", ["-C", project, "add", "README.md"]);
  await execFile("git", ["-C", project, "commit", "-m", "base"]);
  const before = (await execFile("git", ["-C", project, "rev-parse", "HEAD"])).stdout.trim();
  await execFile("git", ["-C", project, "checkout", "-b", "fm/ship-one"]);
  await writeFile(path.join(project, "result.txt"), "verified\n");
  await execFile("git", ["-C", project, "add", "result.txt"]);
  await execFile("git", ["-C", project, "commit", "-m", "ship result"]);
  const expected = (await execFile("git", ["-C", project, "rev-parse", "HEAD"])).stdout.trim();
  await execFile("git", ["-C", project, "checkout", "trunk"]);
  const adapter = new FirstMateAdapter({ cwd: project, env: process.env, run: realRun });
  adapter.projectRoot = project;
  adapter.runtimeHome = home;
  assert.equal((await adapter.ensureDefaultBranchMarker()).marker, "owned");
  await writeFile(path.join(home, "state", "ship-one.meta"), `project=${project}\nmode=local-only\n`, "utf8");
  const merged = await execFile(helper, ["ship-one"], {
    env: { ...process.env, FM_ROOT_OVERRIDE: stockRoot, FM_HOME: home, FM_GUARD_GRACE: "999999" },
  });
  assert.match(merged.stdout, /merged fm\/ship-one into local trunk/);
  const after = (await execFile("git", ["-C", project, "rev-parse", "HEAD"])).stdout.trim();
  assert.notEqual(after, before);
  assert.equal(after, expected);
  assert.equal((await adapter.cleanupOwnedDefaultBranchMarker()).code, "default_branch_marker_removed");
});

test("Main integrates only through stock fm-merge-local.sh and surfaces refusal", async () => {
  const calls = [];
  const adapter = new FirstMateAdapter({
    env: { KIMIFLOW_CREW_ROLE: "main" },
    run: async (file, args) => {
      calls.push([file, args]);
      return { code: 1, stdout: "", stderr: "dirty checkout" };
    },
  });
  adapter.root = "/firstmate";
  adapter.runtimeHome = "/control/.kimiflow/session/FIRSTMATE-MAIN-v1/run";
  adapter.role = "main";
  adapter.projectRoot = "/project";
  adapter.watcher = fakeWatcher();
  assert.equal((await adapter.integrate({ task: "child-ship" })).code, "local_integration_refused");
  assert.equal(path.basename(calls[0][0]), "fm-merge-local.sh");
  assert.deepEqual(calls[0][1], ["child-ship"]);
});
