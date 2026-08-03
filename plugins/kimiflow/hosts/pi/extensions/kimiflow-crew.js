import { execFile as execFileCallback, spawn as spawnProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);
const KIMIFLOW_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");

export const REQUIRED_FIRSTMATE_CALM_FILES = [
  ".pi/extensions/fm-calm.ts",
  ".pi/extensions/lib/fm-calm-assistant-layout.ts",
  ".pi/extensions/lib/fm-calm-operational-user-layout.ts",
  ".pi/extensions/lib/fm-calm-working-ship.ts",
  ".pi/extensions/lib/fm-calm-visibility.ts",
  ".pi/extensions/lib/fm-operational-input.ts",
];

export const CREW_ACTIONS = ["role", "activate", "start_main", "spawn", "status", "report", "send", "drain", "teardown", "integrate"];
export const REQUIRED_FIRSTMATE_SCRIPTS = [
  "fm-session-start.sh",
  "fm-brief.sh",
  "fm-spawn.sh",
  "fm-peek.sh",
  "fm-crew-state.sh",
  "fm-send.sh",
  "fm-wake-drain.sh",
  "fm-watch-arm.sh",
  "fm-teardown.sh",
  "fm-project-mode.sh",
  "fm-merge-local.sh",
];

const CREW_ROLES = new Set(["captain", "main", "worker"]);
const ROLE_ACTIONS = {
  captain: new Set(["role", "activate", "start_main", "status", "send", "drain", "teardown"]),
  main: new Set(["role", "activate", "spawn", "status", "report", "send", "drain", "teardown", "integrate"]),
  worker: new Set(["role"]),
};

const PARAMETERS = {
  type: "object",
  properties: {
    action: { type: "string", enum: CREW_ACTIONS },
    task: { type: "string", description: "Stable FirstMate task id." },
    kind: { type: "string", enum: ["ship", "scout"], default: "ship" },
    brief: { type: "string", description: "Self-contained Kimiflow work packet for a new worker." },
    request: { type: "string", description: "Exact user request frozen into a new Kimiflow Main launch." },
    plan: { type: "string", description: "Optional already-agreed plan frozen into a new Kimiflow Main launch." },
    stage: { type: "string", enum: ["research", "confirmed"], default: "confirmed", description: "Research is read-only and Scout-only; confirmed packets may implement or review." },
    message: { type: "string", description: "Text sent to an existing worker." },
    key: { type: "string", enum: ["Enter", "Escape", "C-c"], description: "Optional control key for send." },
    model: { type: "string", description: "Optional Pi model passed through to FirstMate." },
    effort: { type: "string", enum: ["low", "medium", "high", "xhigh", "max"] },
    verbosity: { type: "string", enum: ["quiet", "balanced", "verbose"], description: "Main's already-resolved Kimiflow presentation level, passed on activate." },
    confirmation: { type: "string", description: "For teardown, must exactly equal task." },
  },
  required: ["action"],
  additionalProperties: false,
};

const TASK_ID = /^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$/;
const PROJECT_NAME = /^[A-Za-z0-9._-]+$/;
const WAKE_LINE = /^(?:signal:|stale:|check:|heartbeat(?:$|:))/m;
const WATCH_READY = /^watcher: (?:started|attached)\b/m;
const WATCH_FAILED = /^watcher: FAILED\b/m;
const PI_WORKER_READY = /(?:\bpi v\d|\[Skills\]|\[skill\]\s+kimiflow|Working\.\.\.)/i;
const PI_LIFECYCLE_READY = /\bpi-ext\b|^state:\s+(?:working|done|failed|blocked|needs-decision|paused)\b.*\bsource:\s+status-log\b/m;
const FIRSTMATE_HERDR_GATE = "# Herdr lifecycle declaration - NOT ENABLED";

function cleanText(value, limit = 12_000) {
  const text = String(value ?? "").trim();
  return text.length <= limit ? text : `${text.slice(0, limit)}\n[truncated]`;
}

function stripFirstMateHerdrGate(template) {
  const occurrences = template.split(FIRSTMATE_HERDR_GATE).length - 1;
  if (occurrences === 0) return ok("brief_transport_ready", { template, changed: false });
  if (occurrences !== 1) {
    return fail("brief_contract_invalid", "FirstMate brief contained an ambiguous Herdr lifecycle declaration.");
  }
  const start = template.indexOf(FIRSTMATE_HERDR_GATE);
  const setup = template.indexOf("# Setup\n", start);
  if (setup < 0) {
    return fail("brief_contract_invalid", "FirstMate brief's Herdr lifecycle declaration was not followed by the canonical setup section.");
  }
  const prefix = template.slice(0, start).trimEnd();
  const suffix = template.slice(setup);
  return ok("brief_transport_ready", { template: `${prefix}\n\n${suffix}`, changed: true });
}

function appendOutput(current, chunk, limit = 24_000) {
  const next = current + String(chunk ?? "");
  return next.length <= limit ? next : next.slice(next.length - limit);
}

function startupWakeSection(output) {
  const match = String(output ?? "").match(/(?:^|\n)WAKE QUEUE\r?\n-+\r?\n([\s\S]*?)\r?\n=+\r?\nCONTEXT\r?\n=+/);
  return String(match?.[1] ?? "")
    .split(/\r?\n/)
    .filter((line) => /^\d+\t(?:signal|stale|check|heartbeat)\t/.test(line))
    .join("\n");
}

function watcherPresentation(output, quiet) {
  if (!quiet) return output;
  return output
    .split(/\r?\n/)
    .filter((line) => !WATCH_READY.test(line) && !/herdr\.sh: line \d+: printf: write error: Broken pipe$/.test(line))
    .join("\n")
    .trim();
}

function ok(code, values = {}) {
  return { ok: true, code, ...values };
}

function fail(code, message, values = {}) {
  return { ok: false, code, message, ...values };
}

function toolResult(result) {
  return {
    content: [{ type: "text", text: JSON.stringify(result) }],
    details: result,
  };
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'\\''`)}'`;
}

async function defaultRun(file, args, options = {}) {
  try {
    const result = await execFile(file, args, {
      cwd: options.cwd,
      env: options.env,
      encoding: "utf8",
      maxBuffer: 4 * 1024 * 1024,
      timeout: options.timeout ?? 120_000,
      signal: options.signal,
    });
    return { code: 0, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
  } catch (error) {
    return {
      code: Number.isInteger(error?.code) ? error.code : 1,
      stdout: error?.stdout ?? "",
      stderr: error?.stderr ?? error?.message ?? "command failed",
    };
  }
}

async function isExecutableFile(file) {
  try {
    const [info] = await Promise.all([fs.lstat(file), fs.access(file, 1)]);
    return info.isFile() && !info.isSymbolicLink();
  } catch {
    return false;
  }
}

async function isFirstMateRoot(candidate) {
  if (!candidate) return false;
  const root = path.resolve(candidate);
  for (const script of REQUIRED_FIRSTMATE_SCRIPTS) {
    if (!(await isExecutableFile(path.join(root, "bin", script)))) return false;
  }
  return true;
}

export async function locateFirstMateRoot({ cwd = process.cwd(), env = process.env } = {}) {
  const candidates = [];
  const add = (candidate) => {
    if (candidate && !candidates.includes(path.resolve(candidate))) candidates.push(path.resolve(candidate));
  };
  add(env.FM_ROOT_OVERRIDE);
  add(env.KIMIFLOW_FIRSTMATE_ROOT);
  // Backward compatibility only: an old installation may still point FM_HOME
  // at the checkout. A normal runtime home will fail isFirstMateRoot().
  add(env.FM_HOME);

  const config = path.join(env.HOME || os.homedir(), ".config", "kimiflow", "firstmate-root");
  try {
    add((await fs.readFile(config, "utf8")).split(/\r?\n/, 1)[0].trim());
  } catch {
    // An explicit config is optional.
  }

  let current = path.resolve(cwd);
  for (let depth = 0; depth < 5; depth += 1) {
    add(path.join(current, "firstmate"));
    add(path.join(path.dirname(current), "firstmate"));
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  const home = env.HOME || os.homedir();
  add(path.join(home, "firstmate"));
  add(path.join(home, ".firstmate"));
  add(path.join(home, "Documents", "VIBE CODING", "firstmate"));

  for (const candidate of candidates) {
    if (await isFirstMateRoot(candidate)) return candidate;
  }
  return null;
}

export class FirstMateAdapter {
  constructor({ pi, cwd = process.cwd(), env = process.env, run = defaultRun, spawn = spawnProcess, sleep = delay } = {}) {
    this.pi = pi;
    this.cwd = path.resolve(cwd);
    this.env = { ...env };
    this.run = run;
    this.spawn = spawn;
    this.sleep = sleep;
    this.root = null;
    this.controlRoot = null;
    this.projectRoot = null;
    this.runtimeHome = null;
    this.role = null;
    this.mainTask = null;
    this.modeCapability = null;
    this.watcher = null;
    this.stopping = false;
    this.pendingWakeTasks = new Set();
    this.terminalTasks = new Set();
    this.workerVerbosity = null;
  }

  async execute(params, signal, context) {
    const role = this.resolveRole();
    if (!role.ok) return role;
    if (!ROLE_ACTIONS[role.role].has(params.action)) {
      return fail("role_action_forbidden", `Kimiflow crew role ${role.role} cannot perform action=${params.action}.`, {
        role: role.role,
        action: params.action,
      });
    }
    switch (params.action) {
      case "role": return ok("crew_role", { role: role.role });
      case "activate": return this.activate(params, signal);
      case "start_main": return this.startMain(params, signal, context);
      case "spawn": return this.spawnWorker(params, signal, context);
      case "status": return this.status(params, signal);
      case "report": return this.report(params, signal);
      case "send": return this.send(params, signal);
      case "drain": return this.drain(signal);
      case "teardown": return this.teardown(params, signal);
      case "integrate": return this.integrate(params, signal);
      default: return fail("invalid_action", "Unknown crew action.");
    }
  }

  resolveRole() {
    const role = this.role ?? cleanText(this.env.KIMIFLOW_CREW_ROLE || "captain", 32).toLowerCase();
    if (!CREW_ROLES.has(role)) {
      return fail("crew_role_invalid", "KIMIFLOW_CREW_ROLE must be captain, main, or worker.");
    }
    this.role = role;
    return ok("crew_role", { role });
  }

  async activate(params = {}, signal) {
    const role = this.resolveRole();
    if (!role.ok) return role;
    if (role.role === "worker") return fail("role_action_forbidden", "A Kimiflow Worker cannot activate or own a FirstMate crew.");
    if (this.root && this.projectRoot && this.runtimeHome && this.watcher) {
      return ok("already_active", {
        role: role.role,
        projectRoot: this.projectRoot,
        firstMateRoot: this.root,
        firstMateHome: this.runtimeHome,
      });
    }
    const root = await locateFirstMateRoot({ cwd: this.cwd, env: this.env });
    if (!root) {
      return fail("firstmate_unavailable", "No capability-compatible FirstMate checkout was found. Ordinary Pi remains available.");
    }
    if (this.env.HERDR_ENV !== "1") {
      return fail("not_in_herdr", "This Pi session is not running inside Herdr (HERDR_ENV=1 is absent). Ordinary Pi remains available.");
    }

    const git = await this.run("git", ["-C", this.cwd, "rev-parse", "--show-toplevel"], {
      cwd: this.cwd, env: this.env, signal, timeout: 15_000,
    });
    const controlRoot = cleanText(git.stdout, 4_096);
    if (git.code !== 0 || !path.isAbsolute(controlRoot)) {
      return fail("project_git_root_unavailable", "Kimiflow crew requires Pi to run inside a Git project.", { detail: cleanText(git.stderr) });
    }
    let projectRoot = path.resolve(controlRoot);
    if (role.role === "main") {
      const supervised = cleanText(this.env.KIMIFLOW_SUPERVISED_PROJECT, 4_096);
      if (!path.isAbsolute(supervised)) {
        return fail("supervised_project_unavailable", "Kimiflow Main requires an absolute KIMIFLOW_SUPERVISED_PROJECT.");
      }
      const verified = await this.run("git", ["-C", supervised, "rev-parse", "--show-toplevel"], {
        cwd: controlRoot, env: this.env, signal, timeout: 15_000,
      });
      const verifiedRoot = cleanText(verified.stdout, 4_096);
      if (verified.code !== 0 || path.resolve(verifiedRoot) !== path.resolve(supervised)) {
        return fail("supervised_project_unavailable", "KIMIFLOW_SUPERVISED_PROJECT is not an exact Git root.", {
          detail: cleanText(`${verified.stdout}\n${verified.stderr}`),
        });
      }
      projectRoot = path.resolve(supervised);
      const mainTask = cleanText(this.env.KIMIFLOW_MAIN_TASK, 128);
      const invalidMainTask = this.validateTask(mainTask);
      if (invalidMainTask) return fail("main_task_unavailable", "Kimiflow Main requires a stable KIMIFLOW_MAIN_TASK.");
      this.mainTask = mainTask;
      const activeRun = await this.verifyMainActiveRun(controlRoot);
      if (!activeRun.ok) return activeRun;
    }
    for (const tool of ["herdr", "jq", "treehouse", "pi"]) {
      const check = await this.run("which", [tool], {
        cwd: projectRoot, env: this.env, signal, timeout: 10_000,
      });
      if (check.code !== 0) return fail("firstmate_capability_missing", `Required FirstMate/Herdr capability is unavailable: ${tool}.`);
    }

    const capability = await this.probeFirstMateCapabilities(root, signal);
    if (!capability.ok) return capability;

    if (role.role === "captain") {
      const prepared = await this.prepareCaptainProject(projectRoot, signal);
      if (!prepared.ok) return prepared;
    }

    const runtimeHome = this.resolveRuntimeHome(role.role, projectRoot, controlRoot);
    const homeReady = await this.initializeRuntimeHome(runtimeHome, role.role === "main" ? controlRoot : projectRoot);
    if (!homeReady.ok) return homeReady;

    this.root = root;
    this.controlRoot = path.resolve(controlRoot);
    this.projectRoot = path.resolve(projectRoot);
    this.runtimeHome = runtimeHome;
    this.modeCapability = capability.capability;

    const recovered = await this.recoverCaptainMainTask();
    if (!recovered.ok) {
      this.resetActivation();
      return recovered;
    }

    const presentation = await this.resolveWorkerPresentation(root, this.projectRoot, signal, params.verbosity);
    if (!presentation.ok) {
      this.resetActivation();
      return presentation;
    }

    if (presentation.calm) {
      const preference = await this.enableFirstMateCalm(runtimeHome);
      if (!preference.ok) {
        this.resetActivation();
        return preference;
      }
    }

    const commandEnv = this.commandEnv();
    const session = await this.run(path.join(root, "bin", "fm-session-start.sh"), [], {
      cwd: root, env: commandEnv, signal, timeout: 180_000,
    });
    const sessionOutput = `${session.stdout}\n${session.stderr}`;
    if (session.code !== 0 || !/lock acquired: harness pid \d+/.test(sessionOutput) || /READ-ONLY SESSION/.test(sessionOutput)) {
      this.resetActivation();
      return fail("firstmate_lock_unavailable", "FirstMate did not grant verified fleet ownership; no worker operation was attempted.", {
        detail: cleanText(sessionOutput),
      });
    }

    this.workerVerbosity = presentation.verbosity;
    const startupWakes = startupWakeSection(sessionOutput);
    const watched = await this.startWatcher(true);
    if (!watched.ok) {
      this.resetActivation();
      return startupWakes ? { ...watched, startupWakes } : watched;
    }
    return ok("activated", {
      projectRoot: this.projectRoot,
      firstMateRoot: this.root,
      firstMateHome: this.runtimeHome,
      role: role.role,
      cli: this.modeCapability,
      watcher: watched.watcher,
      presentation: this.workerVerbosity === "quiet" ? "quiet+firstmate-calm" : this.workerVerbosity,
      ...(startupWakes ? { startupWakes } : {}),
    });
  }

  resetActivation() {
    this.root = null;
    this.controlRoot = null;
    this.projectRoot = null;
    this.runtimeHome = null;
    this.modeCapability = null;
    this.workerVerbosity = null;
  }

  resolveRuntimeHome(role, projectRoot, controlRoot) {
    if (role === "main") {
      return path.join(controlRoot, ".kimiflow", "session", "FIRSTMATE-MAIN-v1", this.mainTask);
    }
    return path.join(projectRoot, ".kimiflow", "session", "FIRSTMATE-CAPTAIN-v1");
  }

  async recoverCaptainMainTask() {
    if (this.role !== "captain") return ok("captain_main_not_applicable");
    const stateDirectory = path.join(this.runtimeHome, "state");
    let entries;
    try {
      entries = await fs.readdir(stateDirectory, { withFileTypes: true });
    } catch (error) {
      return fail("captain_main_state_unavailable", "Captain could not inspect FirstMate's own task metadata.", { detail: error.message });
    }
    const tasks = [];
    for (const entry of entries) {
      if (!entry.name.endsWith(".meta")) continue;
      if (!entry.isFile() || entry.isSymbolicLink()) {
        return fail("captain_main_state_invalid", "Captain found non-regular FirstMate task metadata and refused to infer ownership.", { entry: entry.name });
      }
      const task = entry.name.slice(0, -5);
      if (this.validateTask(task)) {
        return fail("captain_main_state_invalid", "Captain found invalid FirstMate task metadata and refused to infer ownership.", { entry: entry.name });
      }
      const launchPath = path.join(this.runtimeHome, "data", task, "launch-input.json");
      let launch;
      try {
        const info = await fs.lstat(launchPath);
        if (!info.isFile() || info.isSymbolicLink() || info.size > 200_000) throw new Error("launch input is not a bounded regular file");
        launch = JSON.parse(await fs.readFile(launchPath, "utf8"));
      } catch (error) {
        return fail("captain_main_state_invalid", "FirstMate task metadata lacks a valid immutable Kimiflow launch input.", {
          task,
          detail: error.message,
        });
      }
      if (launch?.schemaVersion !== 1 || launch?.task !== task || typeof launch?.supervisedProject !== "string"
        || !path.isAbsolute(launch.supervisedProject) || path.resolve(launch.supervisedProject) !== this.projectRoot) {
        return fail("captain_main_state_invalid", "FirstMate task metadata and the immutable Kimiflow launch input disagree.", { task });
      }
      tasks.push(task);
    }
    if (tasks.length > 1) {
      return fail("captain_main_state_ambiguous", "Captain found more than one FirstMate Main in its isolated project home and refused to choose.", {
        tasks: tasks.sort(),
      });
    }
    this.mainTask = tasks[0] ?? null;
    return ok(this.mainTask ? "captain_main_recovered" : "captain_main_absent", this.mainTask ? { task: this.mainTask } : {});
  }

  async initializeRuntimeHome(runtimeHome, boundaryRoot) {
    try {
      const boundary = path.resolve(boundaryRoot);
      const target = path.resolve(runtimeHome);
      const relative = path.relative(boundary, target);
      if (relative.startsWith("..") || path.isAbsolute(relative)) throw new Error("runtime home escapes its project/control boundary");
      let cursor = boundary;
      for (const segment of relative.split(path.sep).filter(Boolean)) {
        cursor = path.join(cursor, segment);
        try {
          const info = await fs.lstat(cursor);
          if (info.isSymbolicLink() || !info.isDirectory()) throw new Error(`${cursor} is not a real directory`);
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
      }
      await fs.mkdir(runtimeHome, { recursive: true, mode: 0o700 });
      const homeInfo = await fs.lstat(runtimeHome);
      if (!homeInfo.isDirectory() || homeInfo.isSymbolicLink()) throw new Error("runtime home is not a real directory");
      const [realBoundary, realTarget] = await Promise.all([fs.realpath(boundary), fs.realpath(runtimeHome)]);
      const realRelative = path.relative(realBoundary, realTarget);
      if (realRelative.startsWith("..") || path.isAbsolute(realRelative)) throw new Error("runtime home resolves outside its project/control boundary");
      for (const name of ["config", "data", "state"]) {
        const target = path.join(runtimeHome, name);
        await fs.mkdir(target, { recursive: true, mode: 0o700 });
        const info = await fs.lstat(target);
        if (!info.isDirectory() || info.isSymbolicLink()) throw new Error(`${name} is not a real directory`);
      }
      return ok("firstmate_home_ready", { firstMateHome: runtimeHome });
    } catch (error) {
      return fail("firstmate_home_unavailable", "The isolated FirstMate runtime home could not be initialized inside its exact project/control boundary.", { detail: error.message });
    }
  }

  async verifyMainActiveRun(controlRoot) {
    const kimiflowDirectory = path.join(controlRoot, ".kimiflow");
    const runs = [];
    try {
      const entries = await fs.readdir(kimiflowDirectory, { withFileTypes: true });
      for (const entry of entries) {
        if (["project", "session"].includes(entry.name)) continue;
        if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
        const statePath = path.join(kimiflowDirectory, entry.name, "STATE.md");
        try {
          const info = await fs.lstat(statePath);
          if (info.isFile() && !info.isSymbolicLink()) runs.push(statePath);
        } catch (error) {
          if (error.code !== "ENOENT") throw error;
        }
      }
    } catch (error) {
      if (error.code !== "ENOENT") {
        return fail("main_active_run_unavailable", "Kimiflow Main could not inspect its control-worktree run state.", { detail: error.message });
      }
    }
    if (runs.length === 0) {
      return fail("main_active_run_required", "Initialize one standard Kimiflow Active Run in this control worktree before activating Main's FirstMate crew, then retry action=activate.");
    }
    if (runs.length > 1) {
      return fail("main_active_run_ambiguous", "Kimiflow Main found more than one Active Run in its control worktree and refused to choose.", { runs });
    }
    return ok("main_active_run_ready", { state: runs[0] });
  }

  async probeFirstMateCapabilities(root, signal) {
    let spawnSource;
    try {
      spawnSource = await fs.readFile(path.join(root, "bin", "fm-spawn.sh"), "utf8");
    } catch (error) {
      return fail("firstmate_harness_capability_mismatch", "FirstMate's raw harness contract could not be inspected.", { detail: error.message });
    }
    const harnessTokens = ["__MODELFLAG__", "__EFFORTFLAG__", "__PIEXT__", "__OPINPUT__", "__BRIEF__"];
    const missingHarnessTokens = harnessTokens.filter((token) => !spawnSource.includes(token));
    if (missingHarnessTokens.length > 0) {
      return fail("firstmate_harness_capability_mismatch", "FirstMate does not expose the complete raw Pi harness contract Kimiflow consumes.", {
        missing: missingHarnessTokens,
      });
    }
    const brief = await this.run(path.join(root, "bin", "fm-brief.sh"), ["--help"], {
      cwd: root, env: { ...this.env, FM_ROOT_OVERRIDE: root }, signal, timeout: 15_000,
    });
    const spawn = await this.run(path.join(root, "bin", "fm-spawn.sh"), ["--help"], {
      cwd: root, env: { ...this.env, FM_ROOT_OVERRIDE: root }, signal, timeout: 15_000,
    });
    if (brief.code !== 0 || spawn.code !== 0) {
      return fail("firstmate_capability_probe_failed", "FirstMate CLI help could not be read without side effects.", {
        detail: cleanText(`${brief.stdout}\n${brief.stderr}\n${spawn.stdout}\n${spawn.stderr}`),
      });
    }
    const briefMode = /(?:^|\s)--mode(?:\s|[=<]|$)/m.test(`${brief.stdout}\n${brief.stderr}`);
    const spawnText = `${spawn.stdout}\n${spawn.stderr}`;
    const spawnMode = /(?:^|\s)--mode(?:\s|[=<]|$)/m.test(spawnText);
    const spawnYolo = /(?:^|\s)--yolo(?:\s|[=<]|$)/m.test(spawnText);
    if (!briefMode && !spawnMode && !spawnYolo) return ok("firstmate_capability_ready", { capability: "legacy" });
    if (briefMode && spawnMode && spawnYolo) return ok("firstmate_capability_ready", { capability: "current" });
    return fail("firstmate_mode_capability_mismatch", "FirstMate exposes a partial delivery-mode CLI; Kimiflow refuses to guess argument semantics.", {
      briefMode,
      spawnMode,
      spawnYolo,
    });
  }

  async prepareCaptainProject(projectRoot, signal) {
    const tracked = await this.run("git", ["-C", projectRoot, "ls-files", "--", ".kimiflow"], {
      cwd: projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (tracked.code !== 0) {
      return fail("tracked_kimiflow_check_failed", "Kimiflow could not verify whether its runtime store is tracked.", { detail: cleanText(tracked.stderr) });
    }
    if (cleanText(tracked.stdout)) {
      return fail("tracked_kimiflow_forbidden", "The project tracks .kimiflow; Captain runtime state must never enter product history.", {
        detail: cleanText(tracked.stdout),
      });
    }
    const common = await this.run("git", ["-C", projectRoot, "rev-parse", "--git-common-dir"], {
      cwd: projectRoot, env: this.env, signal, timeout: 15_000,
    });
    const commonValue = cleanText(common.stdout, 4_096);
    if (common.code !== 0 || !commonValue) {
      return fail("git_exclude_unavailable", "Kimiflow could not locate the repository-local Git exclude file.", { detail: cleanText(common.stderr) });
    }
    const commonDir = path.resolve(projectRoot, commonValue);
    const infoDir = path.join(commonDir, "info");
    const exclude = path.join(infoDir, "exclude");
    try {
      await fs.mkdir(infoDir, { recursive: true, mode: 0o700 });
      const info = await fs.lstat(infoDir);
      if (!info.isDirectory() || info.isSymbolicLink()) throw new Error("Git info directory is not a real directory");
      let text = "";
      try {
        const excludeInfo = await fs.lstat(exclude);
        if (!excludeInfo.isFile() || excludeInfo.isSymbolicLink()) throw new Error("Git exclude is not a regular file");
        text = await fs.readFile(exclude, "utf8");
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
      if (!text.split(/\r?\n/).includes("/.kimiflow/")) {
        const rendered = `${text}${text && !text.endsWith("\n") ? "\n" : ""}/.kimiflow/\n`;
        const temporary = `${exclude}.kimiflow-${process.pid}-${randomBytes(4).toString("hex")}`;
        try {
          await fs.writeFile(temporary, rendered, { flag: "wx", mode: 0o600 });
          await fs.rename(temporary, exclude);
        } finally {
          await fs.rm(temporary, { force: true });
        }
      }
    } catch (error) {
      return fail("git_exclude_unavailable", "Kimiflow could not establish the repository-local .kimiflow exclusion.", { detail: error.message });
    }
    const ignored = await this.run("git", ["-C", projectRoot, "check-ignore", "--no-index", "-q", ".kimiflow/session/FIRSTMATE-CAPTAIN-v1"], {
      cwd: projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (ignored.code !== 0) return fail("git_exclude_unverified", "Git did not verify the repository-local .kimiflow exclusion.");
    return ok("captain_project_ready");
  }

  async resolveWorkerPresentation(root, projectRoot, signal, explicitVerbosity) {
    const helper = path.join(KIMIFLOW_ROOT, "hooks", "resolve-verbosity.sh");
    if (!(await isExecutableFile(helper))) {
      return fail("kimiflow_verbosity_unavailable", "Kimiflow could not resolve Main verbosity; refusing to start a noisy worker.");
    }
    const inheritedVerbosity = cleanText(this.env.KIMIFLOW_WORKER_VERBOSITY, 64);
    const resolveArgs = ["get"];
    // Captain resolves presentation once. A Main must inherit that process-local
    // value instead of allowing a model-selected activate argument to make its
    // children noisier than the Captain requested.
    if (explicitVerbosity && !(this.role === "main" && inheritedVerbosity)) {
      resolveArgs.push("--flag", explicitVerbosity);
    }
    const resolved = await this.run(helper, resolveArgs, {
      cwd: projectRoot,
      env: { ...this.env, KIMIFLOW_HOST: "pi", KIMIFLOW_PLUGIN_ROOT: KIMIFLOW_ROOT },
      signal,
      timeout: 15_000,
    });
    const verbosity = cleanText(resolved.stdout, 64).split(/\s+/, 1)[0];
    if (resolved.code !== 0 || !["quiet", "balanced", "verbose"].includes(verbosity)) {
      return fail("kimiflow_verbosity_unavailable", "Kimiflow could not resolve Main verbosity; refusing to start a worker with an unverified presentation level.", {
        detail: cleanText(`${resolved.stdout}\n${resolved.stderr}`),
      });
    }
    if (verbosity !== "quiet") return ok("worker_presentation_ready", { verbosity, calm: false });

    for (const relative of REQUIRED_FIRSTMATE_CALM_FILES) {
      const candidate = path.join(root, relative);
      try {
        const info = await fs.lstat(candidate);
        if (!info.isFile() || info.isSymbolicLink()) throw new Error("not a regular file");
      } catch (error) {
        return fail("firstmate_calm_unavailable", "Kimiflow Main is quiet, but this FirstMate checkout cannot provide its Calm worker presentation.", {
          detail: `${relative}: ${error.message}`,
        });
      }
    }
    return ok("worker_presentation_ready", {
      verbosity,
      calm: true,
      extension: path.join(root, REQUIRED_FIRSTMATE_CALM_FILES[0]),
    });
  }

  async enableFirstMateCalm(runtimeHome) {
    const directory = path.join(runtimeHome, "config");
    const preference = path.join(directory, "calm");
    try {
      await fs.mkdir(directory, { recursive: true });
      try {
        const info = await fs.lstat(preference);
        if (!info.isFile() || info.isSymbolicLink()) {
          return fail("firstmate_calm_preference_invalid", "FirstMate Calm preference is not a regular file.");
        }
        if ((await fs.readFile(preference, "utf8")).trim() === "on") {
          return ok("firstmate_calm_ready");
        }
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
      const temporary = `${preference}.kimiflow-${process.pid}-${randomBytes(4).toString("hex")}`;
      try {
        await fs.writeFile(temporary, "on\n", { flag: "wx", mode: 0o600 });
        await fs.rename(temporary, preference);
      } finally {
        await fs.rm(temporary, { force: true });
      }
      if ((await fs.readFile(preference, "utf8")).trim() !== "on") throw new Error("write verification failed");
      return ok("firstmate_calm_ready");
    } catch (error) {
      return fail("firstmate_calm_preference_unavailable", "Kimiflow Main is quiet, but FirstMate Calm could not be enabled before worker start.", {
        detail: error.message,
      });
    }
  }

  async createPiLauncher({ role, task = null, mainHome = null }) {
    let directory;
    try {
      const temporaryRoot = await fs.realpath("/tmp");
      if (!/^[A-Za-z0-9_./-]+$/.test(temporaryRoot)) {
        throw new Error("the system temporary path is not safe for FirstMate harness detection");
      }
      directory = await fs.mkdtemp(path.join(temporaryRoot, "kimiflow-pi-launch-"));
      await fs.chmod(directory, 0o700);
      const launcher = path.join(directory, "pi");
      const lines = [
        "#!/bin/sh",
        "set -eu",
        "umask 077",
        `export KIMIFLOW_CREW_ROLE=${shellQuote(role)}`,
        `export KIMIFLOW_SUPERVISED_PROJECT=${shellQuote(this.projectRoot)}`,
        `export KIMIFLOW_FIRSTMATE_ROOT=${shellQuote(this.root)}`,
        `export FM_ROOT_OVERRIDE=${shellQuote(this.root)}`,
        `export KIMIFLOW_WORKER_VERBOSITY=${shellQuote(this.workerVerbosity)}`,
      ];
      const piAgentDirectory = cleanText(this.env.PI_CODING_AGENT_DIR, 4_096);
      if (piAgentDirectory) lines.push(`export PI_CODING_AGENT_DIR=${shellQuote(piAgentDirectory)}`);
      if (role === "main") {
        lines.push(`export KIMIFLOW_MAIN_TASK=${shellQuote(task)}`);
        lines.push(`export FM_HOME="$PWD/${mainHome}"`);
      } else {
        lines.push(`export FM_HOME=${shellQuote(this.runtimeHome)}`);
      }
      if (this.workerVerbosity === "quiet") {
        lines.push('mkdir -p "$FM_HOME/config"');
        lines.push("printf 'on\\n' > \"$FM_HOME/config/calm\"");
      }
      lines.push('exec pi "$@"', "");
      await fs.writeFile(launcher, lines.join("\n"), { flag: "wx", mode: 0o700 });
      const info = await fs.lstat(launcher);
      if (!info.isFile() || info.isSymbolicLink()) throw new Error("launcher is not a regular file");
      return ok("pi_launcher_ready", { launcher, directory });
    } catch (error) {
      if (directory) {
        await fs.rm(path.join(directory, "pi"), { force: true }).catch(() => {});
        await fs.rmdir(directory).catch(() => {});
      }
      return fail("pi_launcher_unavailable", "Kimiflow could not create a bounded Pi launcher for FirstMate.", { detail: error.message });
    }
  }

  async removePiLauncher(launcher) {
    if (!launcher?.directory || !launcher?.launcher) return;
    await fs.rm(launcher.launcher, { force: true }).catch(() => {});
    await fs.rmdir(launcher.directory).catch(() => {});
  }

  commandEnv() {
    return {
      ...this.env,
      FM_HOME: this.runtimeHome,
      FM_ROOT_OVERRIDE: this.root,
      FM_BACKEND: "herdr",
    };
  }

  inheritedPiModel(context) {
    const contextModel = cleanText(context?.model?.id, 512);
    if (contextModel) {
      const contextProvider = cleanText(context?.model?.provider, 256);
      return contextProvider && !contextModel.includes("/") ? `${contextProvider}/${contextModel}` : contextModel;
    }
    const model = cleanText(this.env.PI_MODEL, 512);
    if (!model) return "";
    const provider = cleanText(this.env.PI_PROVIDER, 256);
    return provider && !model.includes("/") ? `${provider}/${model}` : model;
  }

  inheritedPiEffort(context) {
    const contextEffort = cleanText(context?.thinkingLevel, 32);
    if (["low", "medium", "high", "xhigh", "max"].includes(contextEffort)) return contextEffort;
    const effort = cleanText(this.env.PI_REASONING_LEVEL, 32);
    return ["low", "medium", "high", "xhigh", "max"].includes(effort) ? effort : "";
  }

  requireActive() {
    if (!this.root || !this.projectRoot || !this.runtimeHome || !this.watcher) {
      return fail("crew_not_active", "Call kimiflow_crew with action=activate first.");
    }
    return null;
  }

  validateTask(task) {
    if (!TASK_ID.test(task ?? "")) return fail("invalid_task", "Task ids must be 1-64 lowercase letters, digits, dots, underscores or hyphens.");
    return null;
  }

  validateTaskAuthority(task) {
    if (this.role !== "captain") return null;
    if (!this.mainTask || task !== this.mainTask) {
      return fail("captain_task_forbidden", "Captain may address only its exact Kimiflow Main task.", {
        mainTask: this.mainTask,
        task,
      });
    }
    return null;
  }

  async ensureProjectMode(signal) {
    if (this.modeCapability === "current") {
      return ok("project_mode_ready", { project: path.basename(this.projectRoot), mode: "local-only" });
    }
    const name = path.basename(this.projectRoot);
    if (!PROJECT_NAME.test(name)) return fail("invalid_project_name", "FirstMate requires a simple project-directory name.");
    const registry = path.join(this.runtimeHome, "data", "projects.md");
    let text;
    try {
      text = await fs.readFile(registry, "utf8");
    } catch (error) {
      if (error.code !== "ENOENT") {
        return fail("project_registry_unavailable", "FirstMate project registry is unavailable.", { detail: error.message });
      }
      try {
        await fs.mkdir(path.dirname(registry), { recursive: true });
        await fs.writeFile(registry, "", { flag: "wx", mode: 0o600 });
        text = "";
      } catch (createError) {
        if (createError.code === "EEXIST") text = await fs.readFile(registry, "utf8");
        else return fail("project_registry_unavailable", "FirstMate project registry could not be initialized.", { detail: createError.message });
      }
    }
    const lines = text.split(/\r?\n/);
    const index = lines.findIndex((line) => line.startsWith(`- ${name} `));
    const current = await this.run(path.join(this.root, "bin", "fm-project-mode.sh"), [name], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 15_000,
    });
    let mode = cleanText(current.stdout, 256).split(/\s+/, 1)[0];
    if (mode !== "local-only") {
      const date = new Date().toISOString().slice(0, 10);
      if (index >= 0) {
        const suffix = lines[index].slice(`- ${name}`.length).replace(/^ \[[^\]]+\]/, "");
        lines[index] = `- ${name} [local-only]${suffix || ` - ${name} (added ${date})`}`;
      } else {
        if (lines.at(-1) !== "") lines.push("");
        lines.push(`- ${name} [local-only] - ${name} (added ${date})`);
      }
      const temporary = `${registry}.kimiflow-${process.pid}-${randomBytes(4).toString("hex")}`;
      await fs.writeFile(temporary, `${lines.join("\n").replace(/\n+$/, "")}\n`, { mode: 0o600 });
      await fs.rename(temporary, registry);
      const verified = await this.run(path.join(this.root, "bin", "fm-project-mode.sh"), [name], {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 15_000,
      });
      mode = cleanText(verified.stdout, 256).split(/\s+/, 1)[0];
    }
    if (mode !== "local-only") {
      return fail("project_mode_unverified", "The isolated Kimiflow FirstMate home did not verify local-only delivery.");
    }
    return ok("project_mode_ready", { project: name, mode });
  }

  async ensureDefaultBranchMarker(signal) {
    const symbolic = await this.run("git", ["-C", this.projectRoot, "symbolic-ref", "refs/remotes/origin/HEAD"], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (symbolic.code === 0) return ok("default_branch_ready", { marker: "existing", target: cleanText(symbolic.stdout, 512) });

    const occupied = await this.run("git", ["-C", this.projectRoot, "show-ref", "--verify", "--quiet", "refs/remotes/origin/HEAD"], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (occupied.code === 0) {
      return fail("default_branch_marker_occupied", "refs/remotes/origin/HEAD exists but is not symbolic; Kimiflow will not overwrite it.");
    }
    for (const branch of ["main", "master"]) {
      const local = await this.run("git", ["-C", this.projectRoot, "show-ref", "--verify", "--quiet", `refs/heads/${branch}`], {
        cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
      });
      if (local.code === 0) return ok("default_branch_ready", { marker: "unneeded", branch });
    }

    const currentResult = await this.run("git", ["-C", this.projectRoot, "symbolic-ref", "--short", "HEAD"], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    const branch = cleanText(currentResult.stdout, 512);
    if (currentResult.code !== 0 || !branch || branch.startsWith("-") || branch.includes("..")) {
      return fail("default_branch_unverified", "The project has no standard default branch and its current local branch is not provable.");
    }
    const currentExists = await this.run("git", ["-C", this.projectRoot, "show-ref", "--verify", "--quiet", `refs/heads/${branch}`], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (currentExists.code !== 0) return fail("default_branch_unverified", "The current branch ref could not be verified.");

    const remotesResult = await this.run("git", ["-C", this.projectRoot, "remote"], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (remotesResult.code !== 0) return fail("default_branch_unverified", "Git remotes could not be inspected.");
    const remotes = remotesResult.stdout.split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    let authority = "local";
    if (remotes.length > 0) {
      const remote = remotes.includes("origin") ? "origin" : remotes.length === 1 ? remotes[0] : null;
      if (!remote) return fail("default_branch_remote_ambiguous", "Multiple remotes exist without origin; no authoritative default can be inferred.");
      const remoteHead = await this.run("git", ["-C", this.projectRoot, "ls-remote", "--symref", remote, "HEAD"], {
        cwd: this.projectRoot, env: this.env, signal, timeout: 30_000,
      });
      const remoteBranch = remoteHead.stdout.match(/^ref:\s+refs\/heads\/([^\s]+)\s+HEAD$/m)?.[1];
      if (remoteHead.code !== 0 || !remoteBranch) {
        return fail("default_branch_remote_unverified", "The authoritative remote did not expose a symbolic HEAD.", { detail: cleanText(`${remoteHead.stdout}\n${remoteHead.stderr}`) });
      }
      if (remoteBranch !== branch) {
        return fail("default_branch_remote_mismatch", "The authoritative remote default differs from the current local branch.", {
          local: branch,
          remote: remoteBranch,
        });
      }
      authority = remote;
    }

    // A symbolic remote-HEAD may legally point at the verified local branch.
    // This keeps no-remote repositories non-dangling while `symbolic-ref
    // --short refs/remotes/origin/HEAD` still yields the branch name expected
    // by stock fm-merge-local.sh.
    const target = `refs/heads/${branch}`;
    const create = await this.run("git", ["-C", this.projectRoot, "symbolic-ref", "refs/remotes/origin/HEAD", target], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (create.code !== 0) return fail("default_branch_marker_failed", "The verified reversible origin/HEAD marker could not be created.", { detail: cleanText(create.stderr) });
    const verify = await this.run("git", ["-C", this.projectRoot, "symbolic-ref", "refs/remotes/origin/HEAD"], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (verify.code !== 0 || cleanText(verify.stdout, 512) !== target) {
      return fail("default_branch_marker_failed", "The reversible origin/HEAD marker could not be verified.");
    }
    const owner = path.join(this.runtimeHome, "state", "kimiflow-origin-head-owner.json");
    const record = `${JSON.stringify({ schemaVersion: 1, projectRoot: this.projectRoot, ref: "refs/remotes/origin/HEAD", target, authority })}\n`;
    try {
      await fs.writeFile(owner, record, { flag: "wx", mode: 0o600 });
    } catch (error) {
      await this.run("git", ["-C", this.projectRoot, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], {
        cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
      });
      return fail("default_branch_marker_owner_failed", "Kimiflow could not record ownership of its reversible origin/HEAD marker.", { detail: error.message });
    }
    return ok("default_branch_ready", { marker: "owned", branch, target, authority });
  }

  async cleanupOwnedDefaultBranchMarker(signal) {
    const owner = path.join(this.runtimeHome, "state", "kimiflow-origin-head-owner.json");
    let record;
    try {
      const info = await fs.lstat(owner);
      if (!info.isFile() || info.isSymbolicLink()) throw new Error("owner record is not a regular file");
      record = JSON.parse(await fs.readFile(owner, "utf8"));
    } catch (error) {
      if (error.code === "ENOENT") return ok("default_branch_marker_absent");
      return fail("default_branch_marker_cleanup_refused", "The origin/HEAD ownership record is invalid; no ref was changed.", { detail: error.message });
    }
    if (record.projectRoot !== this.projectRoot || record.ref !== "refs/remotes/origin/HEAD" || !String(record.target).startsWith("refs/heads/")) {
      return fail("default_branch_marker_cleanup_refused", "The origin/HEAD ownership record does not match this project; no ref was changed.");
    }
    const current = await this.run("git", ["-C", this.projectRoot, "symbolic-ref", record.ref], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (current.code !== 0 || cleanText(current.stdout, 512) !== record.target) {
      return fail("default_branch_marker_cleanup_refused", "The owned origin/HEAD marker changed after creation; no ref was changed.");
    }
    const removed = await this.run("git", ["-C", this.projectRoot, "symbolic-ref", "--delete", record.ref], {
      cwd: this.projectRoot, env: this.env, signal, timeout: 15_000,
    });
    if (removed.code !== 0) return fail("default_branch_marker_cleanup_refused", "Git refused removal of the exact owned origin/HEAD marker.", { detail: cleanText(removed.stderr) });
    await fs.rm(owner, { force: true });
    return ok("default_branch_marker_removed");
  }

  async startMain(params, signal, context) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const request = cleanText(params.request, 50_000);
    const plan = cleanText(params.plan, 100_000);
    if (!request) return fail("main_request_required", "A new Kimiflow Main requires the exact user request.");
    if (this.mainTask && this.mainTask !== params.task) {
      return fail("captain_main_conflict", "This Captain already owns a different Kimiflow Main task.", { task: this.mainTask });
    }
    const snapshot = `${JSON.stringify({
      schemaVersion: 1,
      task: params.task,
      supervisedProject: this.projectRoot,
      request,
      plan,
    }, null, 2)}\n`;
    const snapshotPath = path.join(this.runtimeHome, "data", params.task, "launch-input.json");
    try {
      await fs.mkdir(path.dirname(snapshotPath), { recursive: true, mode: 0o700 });
      try {
        await fs.writeFile(snapshotPath, snapshot, { flag: "wx", mode: 0o600 });
      } catch (error) {
        if (error.code !== "EEXIST") throw error;
        const current = await fs.readFile(snapshotPath, "utf8");
        if (current !== snapshot) return fail("main_launch_input_conflict", "This Main task already owns a different immutable launch input. Use a new task id.");
      }
      const info = await fs.lstat(snapshotPath);
      if (!info.isFile() || info.isSymbolicLink()) throw new Error("launch snapshot is not a regular file");
    } catch (error) {
      if (error?.ok === false) return error;
      return fail("main_launch_input_unavailable", "Kimiflow could not establish the immutable Main launch input.", { detail: error.message });
    }

    const projectMode = await this.ensureProjectMode(signal);
    if (!projectMode.ok) return projectMode;
    const briefPath = path.join(this.runtimeHome, "data", params.task, "brief.md");
    let existingBrief = false;
    try {
      const info = await fs.lstat(briefPath);
      existingBrief = info.isFile() && !info.isSymbolicLink();
    } catch {
      existingBrief = false;
    }
    if (!existingBrief) {
      const briefArgs = [params.task, this.modeCapability === "current" ? this.projectRoot : path.basename(this.projectRoot)];
      if (this.modeCapability === "current") briefArgs.push("--mode", "local-only");
      const scaffold = await this.run(path.join(this.root, "bin", "fm-brief.sh"), briefArgs, {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
      });
      if (scaffold.code !== 0) return fail("main_brief_scaffold_failed", "FirstMate could not create the normal Main brief.", { detail: cleanText(`${scaffold.stdout}\n${scaffold.stderr}`) });
      const scaffoldTemplate = await fs.readFile(briefPath, "utf8");
      const transport = stripFirstMateHerdrGate(scaffoldTemplate);
      if (!transport.ok) return transport;
      const template = transport.template;
      const taskMarker = "# Task\n{TASK}";
      if (template.split(taskMarker).length !== 2) return fail("brief_contract_invalid", "FirstMate Main brief did not contain exactly one canonical task section.");
      const mainContract = [
        "Kimiflow control Main:",
        "- This outer FirstMate worktree is a durable control container only. Do not write product implementation bytes here and do not deliver or merge its branch as product code.",
        `- Supervise the read-only product checkout at ${this.projectRoot}. Never write product bytes or existing run/plan artifacts there; the exact stock FirstMate status file named below is the sole control-state exception.`,
        "- Your first workflow action is to initialize or resume exactly one standard Kimiflow Active Run inside this current control worktree from the immutable launch input below. Do this before emitting a decision/status or activating your FirstMate crew.",
        "- All product changes must be delegated to visible child FirstMate Ships through kimiflow_crew. Research and review use visible Scouts.",
        "- Return decisions, blockers and bounded artifact pointers through the stock FirstMate status protocol. Do not open a separate user conversation.",
        "- The Captain owns only this Main; this Main owns the child crew.",
        "- `kimiflow_crew` is the only crew-control interface. It is not direct Herdr lifecycle work: never invoke `herdr` or `fm-herdr-lab.sh`, and never request or regenerate this brief with `--herdr-lab`. The Kimiflow adapter and stock FirstMate own production endpoint lifecycle.",
        "",
        "Immutable launch input:",
        "```json",
        snapshot.trimEnd(),
        "```",
      ].join("\n");
      const withTask = template.replace(taskMarker, `# Task\n${mainContract}`).replaceAll("{TASK}", "the control task description");
      const setupMarker = "\n# Setup\n";
      if (withTask.split(setupMarker).length !== 2) {
        return fail("brief_contract_invalid", "FirstMate Main brief did not contain exactly one canonical setup section.");
      }
      const statusPath = path.join(this.runtimeHome, "state", `${params.task}.status`);
      const controlTail = [
        "",
        "# Setup",
        "You are in an isolated FirstMate worktree used only as the durable Kimiflow control store.",
        "Verify `pwd -P` and `git rev-parse --show-toplevel` resolve to this worktree and never to the supervised product checkout named above. Do not create, switch, merge, or deliver an outer product branch.",
        "",
        "# Rules",
        "1. Stay inside this control worktree. The supervised product checkout and its existing `.kimiflow` run/plan artifacts are read-only; only the exact stock FirstMate status file named below may receive control-state lines.",
        "2. Before any decision/status or crew activation, initialize or resume exactly one standard Kimiflow Active Run here from the immutable launch input. Main crew activation fails closed until its STATE.md exists. Do not invent a second orchestration state machine.",
        "3. Delegate every product write to a visible FirstMate Ship and every independent research/review axis to a visible Scout via `kimiflow_crew`.",
        "4. Use stock FirstMate lifecycle actions for status, steering, local integration and safe teardown. No custom merge or delivery fallback is allowed.",
        `5. Report only bounded supervisor-relevant changes by appending one line to ${shellQuote(statusPath)}: \`working: ...\`, \`needs-decision: ...\`, \`blocked: ...\`, \`done: ...\`, or \`failed: ...\`.`,
        "6. Do not speak to the user from this Main. Captain owns the user conversation and receives decisions through FirstMate status.",
        "7. After appending `needs-decision:` or `blocked:`, end the turn immediately. Do not activate a crew, spawn a child, inspect further, or continue until Captain's exact reply arrives through FirstMate.",
        "8. After a child returns `worker_reachable`, end the turn and wait for the automatic FirstMate wake. Never poll status and never use sleep loops.",
        "9. Before final completion, safely tear down every exact child after its report or integrated result has been consumed. Never force teardown.",
        "",
        "# Definition of done",
        "Delivery contract: mode=local-only",
        "This records the stock FirstMate contract for child Ships; it does not authorize delivery of this outer control branch.",
        "The confirmed Kimiflow run is complete only when its required child work is mechanically verified, independently reviewed, integrated through stock `fm-merge-local.sh`, and the durable run evidence is complete.",
        `Append \`done: Kimiflow run complete\` to ${shellQuote(statusPath)} and stop.`,
        "",
      ].join("\n");
      const rendered = `${withTask.slice(0, withTask.indexOf(setupMarker))}${controlTail}`;
      await fs.writeFile(briefPath, rendered, { mode: 0o600 });
    } else {
      const current = await fs.readFile(briefPath, "utf8");
      if (!current.includes(snapshot.trimEnd()) || !current.includes("Kimiflow control Main")) {
        return fail("main_brief_conflict", "This Main task already has a different or non-control FirstMate brief. Use a new task id.");
      }
      const transport = stripFirstMateHerdrGate(current);
      if (!transport.ok) return transport;
      if (transport.changed) await fs.writeFile(briefPath, transport.template, { mode: 0o600 });
    }

    const marker = await this.ensureDefaultBranchMarker(signal);
    if (!marker.ok) return marker;
    const calmExtension = path.join(this.root, REQUIRED_FIRSTMATE_CALM_FILES[0]);
    const mainHome = `.kimiflow/session/FIRSTMATE-MAIN-v1/${params.task}`;
    const launcher = await this.createPiLauncher({ role: "main", task: params.task, mainHome });
    if (!launcher.ok) {
      const markerRollback = marker.marker === "owned" ? await this.cleanupOwnedDefaultBranchMarker(signal) : null;
      return markerRollback ? { ...launcher, markerRollback } : launcher;
    }
    const harness = this.workerVerbosity === "quiet"
      ? `${launcher.launcher} __MODELFLAG____EFFORTFLAG__-e ${shellQuote(calmExtension)} -e __PIEXT__ "\$(__OPINPUT__ encode launch-brief < __BRIEF__)"`
      : `${launcher.launcher} __MODELFLAG____EFFORTFLAG__-e __PIEXT__ "\$(__OPINPUT__ encode launch-brief < __BRIEF__)"`;
    const spawnArgs = [params.task, this.projectRoot, "--harness", harness, "--backend", "herdr"];
    if (this.modeCapability === "current") spawnArgs.push("--mode", "local-only", "--yolo", "off");
    const model = cleanText(params.model, 512) || this.inheritedPiModel(context);
    const effort = params.effort || this.inheritedPiEffort(context);
    if (model) spawnArgs.push("--model", model);
    if (effort) spawnArgs.push("--effort", effort);
    try {
      const spawned = await this.run(path.join(this.root, "bin", "fm-spawn.sh"), spawnArgs, {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 240_000,
      });
      const ownership = await this.recoverCaptainMainTask();
      if (!ownership.ok) return ownership;
      if (spawned.code !== 0) {
        let markerRollback = null;
        if (marker.marker === "owned" && this.mainTask !== params.task) markerRollback = await this.cleanupOwnedDefaultBranchMarker(signal);
        return fail("main_spawn_failed", "FirstMate did not create the Kimiflow Main.", {
          task: params.task,
          owned: this.mainTask === params.task,
          ...(markerRollback ? { markerRollback } : {}),
          detail: cleanText(`${spawned.stdout}\n${spawned.stderr}`),
        });
      }
      if (this.mainTask !== params.task) {
        const markerRollback = marker.marker === "owned" ? await this.cleanupOwnedDefaultBranchMarker(signal) : null;
        return fail("main_spawn_untracked", "FirstMate returned from Main spawn without exact task metadata; Captain refuses to claim or control it.", {
          task: params.task,
          ...(markerRollback ? { markerRollback } : {}),
        });
      }
      const endpoint = await this.verifyEndpoint(params.task, signal);
      if (!endpoint.ok) return fail("main_spawn_unverified", "FirstMate returned from Main spawn, but the exact endpoint and Pi lifecycle were not both verified.", { detail: endpoint.detail });
      const identifiers = await this.readTaskIdentifiers(params.task);
      this.pendingWakeTasks.delete(params.task);
      this.terminalTasks.delete(params.task);
      return ok("main_reachable", {
        task: params.task,
        endpoint: "verified",
        launchInput: "immutable",
        controlOnly: true,
        ...identifiers,
        mode: projectMode.mode,
        presentation: this.workerVerbosity === "quiet" ? "quiet+firstmate-calm" : this.workerVerbosity,
        spawnReceipt: cleanText(spawned.stdout, 4_000),
      });
    } finally {
      await this.removePiLauncher(launcher);
    }
  }

  async spawnWorker(params, signal, context) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const kind = params.kind ?? "ship";
    const stage = params.stage ?? "confirmed";
    if (stage === "research" && kind !== "scout") {
      return fail("research_ship_forbidden", "A pre-contract research packet must use a read-only FirstMate Scout.");
    }
    const briefPath = path.join(this.runtimeHome, "data", params.task, "brief.md");
    const confirmedBrief = cleanText(params.brief, 50_000);
    let existingBrief = false;
    try {
      const info = await fs.lstat(briefPath);
      existingBrief = info.isFile() && !info.isSymbolicLink();
    } catch {
      existingBrief = false;
    }
    if (!existingBrief && !confirmedBrief) {
      return fail("brief_required", "A new visible worker requires a self-contained Kimiflow work packet.");
    }
    if (existingBrief && confirmedBrief) {
      let currentBrief;
      try {
        currentBrief = await fs.readFile(briefPath, "utf8");
      } catch (error) {
        return fail("brief_unavailable", "The existing FirstMate work packet could not be read.", { detail: error.message });
      }
      const packetLabel = stage === "research" ? "Bounded Kimiflow research packet (product contract not final):" : "Confirmed Kimiflow work packet:";
      const expectedPacket = `${packetLabel}\n${confirmedBrief}\n\nKimiflow worker boundary:`;
      if (!currentBrief.includes(expectedPacket)) {
        return fail("brief_conflict", "This FirstMate task already has a different Kimiflow work packet or stage. Use a new task id.");
      }
    }
    const projectMode = await this.ensureProjectMode(signal);
    if (!projectMode.ok) return projectMode;

    if (existingBrief) {
      const current = await fs.readFile(briefPath, "utf8");
      const transport = stripFirstMateHerdrGate(current);
      if (!transport.ok) return transport;
      if (transport.changed) await fs.writeFile(briefPath, transport.template, { mode: 0o600 });
    }

    if (!existingBrief) {
      const briefArgs = [params.task, this.modeCapability === "current" ? this.projectRoot : path.basename(this.projectRoot)];
      if (kind === "scout") briefArgs.push("--scout");
      else if (this.modeCapability === "current") briefArgs.push("--mode", "local-only");
      const scaffold = await this.run(path.join(this.root, "bin", "fm-brief.sh"), briefArgs, {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
      });
      if (scaffold.code !== 0) {
        return fail("brief_scaffold_failed", "FirstMate could not create the normal worker brief.", { detail: cleanText(`${scaffold.stdout}\n${scaffold.stderr}`) });
      }
      const scaffoldTemplate = await fs.readFile(briefPath, "utf8");
      const transport = stripFirstMateHerdrGate(scaffoldTemplate);
      if (!transport.ok) return transport;
      const template = transport.template;
      const taskMarker = "# Task\n{TASK}";
      if (template.split(taskMarker).length !== 2) {
        return fail("brief_contract_invalid", "FirstMate brief did not contain exactly one canonical task section.");
      }
      const packetLabel = stage === "research" ? "Bounded Kimiflow research packet (product contract not final):" : "Confirmed Kimiflow work packet:";
      const stageBoundary = stage === "research"
        ? [
            "- This is bounded read-only research before the final product contract. Collect independent evidence only; do not implement, choose product scope or ask the user.",
            "- Write the required FirstMate Scout report and return it through the status protocol for synthesis by Kimiflow Main.",
          ]
        : kind === "ship"
          ? [
              "- This product contract is already confirmed. Do not repeat product intake or ask the user to confirm it again.",
              "- Implement and run mechanical verification, but do not replace independent semantic review with self-review.",
              "- When the implementation checkpoint is ready, report a paused review-ready status with its exact commit and stop. Kimiflow Main will dispatch visible review Scouts and then send either verified findings or finalization clearance.",
            ]
          : [
              "- This product contract is already confirmed. Review only the assigned evidence axis; do not implement or ask the user.",
              "- Write the required FirstMate Scout report and return it through the status protocol for verification by Kimiflow Main.",
            ];
      const workerContract = [
        packetLabel,
        confirmedBrief,
        "",
        "Kimiflow worker boundary:",
        ...stageBoundary,
        "- Work autonomously in this isolated FirstMate worktree. Do not open a second user conversation.",
        "- Do not spawn another crew. Return decisions, blockers and results only through this brief's FirstMate status protocol.",
        "- This task uses Kimiflow-managed stock FirstMate transport. Never invoke `herdr` or `fm-herdr-lab.sh`, and never request or regenerate this brief with `--herdr-lab`.",
        "- Use the installed Kimiflow skill only for the phase and evidence boundary assigned above; do not create a second Active Run for this task.",
      ].join("\n");
      const rendered = template
        .replace(taskMarker, `# Task\n${workerContract}`)
        .replaceAll("{TASK}", "the task description");
      await fs.writeFile(briefPath, rendered, { mode: 0o600 });
    }

    const calmExtension = path.join(this.root, REQUIRED_FIRSTMATE_CALM_FILES[0]);
    const launcher = await this.createPiLauncher({ role: "worker" });
    if (!launcher.ok) return launcher;
    const harness = this.workerVerbosity === "quiet"
      ? `${launcher.launcher} __MODELFLAG____EFFORTFLAG__-e ${shellQuote(calmExtension)} -e __PIEXT__ "\$(__OPINPUT__ encode launch-brief < __BRIEF__)"`
      : `${launcher.launcher} __MODELFLAG____EFFORTFLAG__-e __PIEXT__ "\$(__OPINPUT__ encode launch-brief < __BRIEF__)"`;
    const spawnArgs = [params.task, this.projectRoot, "--harness", harness, "--backend", "herdr"];
    if (this.modeCapability === "current" && kind === "ship") spawnArgs.push("--mode", "local-only", "--yolo", "off");
    const model = cleanText(params.model, 512) || this.inheritedPiModel(context);
    const effort = params.effort || this.inheritedPiEffort(context);
    if (model) spawnArgs.push("--model", model);
    if (effort) spawnArgs.push("--effort", effort);
    if (kind === "scout") spawnArgs.push("--scout");
    try {
      const spawned = await this.run(path.join(this.root, "bin", "fm-spawn.sh"), spawnArgs, {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 240_000,
      });
      if (spawned.code !== 0) {
        return fail("spawn_failed", "FirstMate did not create the requested worker.", { detail: cleanText(`${spawned.stdout}\n${spawned.stderr}`) });
      }
      const endpoint = await this.verifyEndpoint(params.task, signal);
      if (!endpoint.ok) {
        return fail("spawn_unverified", "FirstMate returned from spawn, but the exact endpoint and Pi lifecycle were not both verified.", {
          detail: endpoint.detail,
        });
      }
      this.pendingWakeTasks.delete(params.task);
      this.terminalTasks.delete(params.task);
      return ok("worker_reachable", {
        task: params.task,
        kind,
        stage,
        mode: projectMode.mode,
        endpoint: "verified",
        presentation: this.workerVerbosity === "quiet" ? "quiet+firstmate-calm" : this.workerVerbosity,
        spawnReceipt: cleanText(spawned.stdout, 4_000),
      });
    } finally {
      await this.removePiLauncher(launcher);
    }
  }

  async verifyEndpoint(task, signal) {
    let peek = { code: 1, stdout: "", stderr: "endpoint did not settle" };
    let lifecycle = { code: 1, stdout: "", stderr: "Pi lifecycle did not settle" };
    let trustAccepted = false;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (attempt > 0) await this.sleep(250);
      peek = await this.run(path.join(this.root, "bin", "fm-peek.sh"), [task, "40"], {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
      });
      if (peek.code !== 0) continue;
      if (/Trust project folder\?/i.test(peek.stdout)) {
        if (trustAccepted) continue;
        const trust = await this.run(path.join(this.root, "bin", "fm-send.sh"), [task, "--key", "Enter"], {
          cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
        });
        if (trust.code !== 0) {
          return fail("worker_trust_failed", "The visible Pi endpoint is reachable, but FirstMate could not accept its project-worktree trust gate.", {
            detail: cleanText(`${trust.stdout}\n${trust.stderr}`),
          });
        }
        trustAccepted = true;
        continue;
      }
      if (!PI_WORKER_READY.test(peek.stdout)) continue;
      lifecycle = await this.run(path.join(this.root, "bin", "fm-crew-state.sh"), [task], {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
      });
      if (lifecycle.code === 0 && PI_LIFECYCLE_READY.test(lifecycle.stdout)) {
        return ok("endpoint_verified", { peek: cleanText(peek.stdout), lifecycle: cleanText(lifecycle.stdout) });
      }
    }
    return fail("endpoint_unverified", "The exact endpoint and Pi lifecycle were not both verified.", {
      detail: cleanText(`${peek.stdout}\n${peek.stderr}\n${lifecycle.stdout}\n${lifecycle.stderr}`),
    });
  }

  async readTaskIdentifiers(task) {
    const metaPath = path.join(this.runtimeHome, "state", `${task}.meta`);
    try {
      const info = await fs.lstat(metaPath);
      if (!info.isFile() || info.isSymbolicLink() || info.size > 100_000) return {};
      const lines = (await fs.readFile(metaPath, "utf8")).split(/\r?\n/);
      const value = (key) => cleanText(lines.find((line) => line.startsWith(`${key}=`))?.slice(key.length + 1), 4_096);
      const mainWorktree = value("worktree");
      const mainWindow = value("window");
      return {
        ...(mainWorktree ? { mainWorktree } : {}),
        ...(mainWindow ? { mainWindow } : {}),
      };
    } catch {
      return {};
    }
  }

  async status(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const forbidden = this.validateTaskAuthority(params.task);
    if (forbidden) return forbidden;
    const result = await this.run(path.join(this.root, "bin", "fm-crew-state.sh"), [params.task], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
    });
    if (result.code !== 0) return fail("status_failed", "FirstMate could not read the worker state.", { detail: cleanText(`${result.stdout}\n${result.stderr}`) });
    const state = cleanText(result.stdout);
    this.pendingWakeTasks.delete(params.task);
    if (/^state: (?:done|failed)\b/m.test(state)) this.terminalTasks.add(params.task);
    return ok("status", { task: params.task, state });
  }

  async report(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const stateResult = await this.run(path.join(this.root, "bin", "fm-crew-state.sh"), [params.task], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
    });
    if (stateResult.code !== 0 || !/^state: done\b/m.test(cleanText(stateResult.stdout))) {
      return fail("scout_report_not_ready", "The FirstMate Scout report is readable only after the task reaches done.", {
        detail: cleanText(`${stateResult.stdout}\n${stateResult.stderr}`),
      });
    }
    const metaPath = path.join(this.runtimeHome, "state", `${params.task}.meta`);
    const reportPath = path.join(this.runtimeHome, "data", params.task, "report.md");
    try {
      for (const candidate of [metaPath, reportPath]) {
        const info = await fs.lstat(candidate);
        if (!info.isFile() || info.isSymbolicLink()) throw new Error(`${path.basename(candidate)} is not a regular file`);
        if (info.size > 100_000) throw new Error(`${path.basename(candidate)} exceeds 100000 bytes`);
      }
      const meta = await fs.readFile(metaPath, "utf8");
      if (!meta.split(/\r?\n/).includes("kind=scout")) {
        return fail("scout_report_forbidden", "Only a FirstMate Scout owns a read-only report.");
      }
      const report = cleanText(await fs.readFile(reportPath, "utf8"), 100_000);
      if (!report) return fail("scout_report_empty", "The completed FirstMate Scout report is empty.");
      this.pendingWakeTasks.delete(params.task);
      this.terminalTasks.add(params.task);
      return ok("scout_report", { task: params.task, report });
    } catch (error) {
      return fail("scout_report_unavailable", "FirstMate did not expose a valid completed Scout report.", { detail: error.message });
    }
  }

  async send(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const forbidden = this.validateTaskAuthority(params.task);
    if (forbidden) return forbidden;
    if (Boolean(params.key) === Boolean(cleanText(params.message))) {
      return fail("send_payload_invalid", "Provide exactly one of message or key.");
    }
    const args = params.key ? [params.task, "--key", params.key] : [params.task, params.message];
    const result = await this.run(path.join(this.root, "bin", "fm-send.sh"), args, {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 45_000,
    });
    if (result.code !== 0) return fail("send_failed", "FirstMate did not verify delivery to the worker.", { detail: cleanText(`${result.stdout}\n${result.stderr}`) });
    this.pendingWakeTasks.delete(params.task);
    this.terminalTasks.delete(params.task);
    return ok("sent", { task: params.task, receipt: cleanText(result.stdout) });
  }

  async drain(signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const result = await this.run(path.join(this.root, "bin", "fm-wake-drain.sh"), [], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
    });
    if (result.code !== 0) return fail("drain_failed", "FirstMate wake records could not be drained.", { detail: cleanText(`${result.stdout}\n${result.stderr}`) });
    return ok("drained", { wakes: cleanText(result.stdout) });
  }

  async verifyMainChildrenClosed(task, signal) {
    const mainState = await this.run(path.join(this.root, "bin", "fm-crew-state.sh"), [task], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
    });
    if (mainState.code !== 0 || !/^state: (?:done|failed|blocked|parked)\b/m.test(cleanText(mainState.stdout))) {
      return fail("main_not_quiescent", "Captain refuses to tear down Main until FirstMate reports a non-working terminal or parked state.", {
        detail: cleanText(`${mainState.stdout}\n${mainState.stderr}`),
      });
    }
    const identifiers = await this.readTaskIdentifiers(task);
    const worktree = identifiers.mainWorktree;
    if (!path.isAbsolute(worktree ?? "")) {
      return fail("main_worktree_unavailable", "Captain cannot verify nested child cleanup because FirstMate did not expose the Main worktree.");
    }
    try {
      const worktreeInfo = await fs.lstat(worktree);
      if (!worktreeInfo.isDirectory() || worktreeInfo.isSymbolicLink()) throw new Error("Main worktree is not a real directory");
      const stateDirectory = path.join(worktree, ".kimiflow", "session", "FIRSTMATE-MAIN-v1", task, "state");
      let entries;
      try {
        const stateInfo = await fs.lstat(stateDirectory);
        if (!stateInfo.isDirectory() || stateInfo.isSymbolicLink()) throw new Error("nested FirstMate state is not a real directory");
        entries = await fs.readdir(stateDirectory, { withFileTypes: true });
      } catch (error) {
        if (error.code === "ENOENT") return ok("main_children_absent");
        throw error;
      }
      const children = entries.filter((entry) => entry.name.endsWith(".meta")).map((entry) => entry.name.slice(0, -5)).sort();
      if (children.length > 0) {
        return fail("main_children_not_torn_down", "Captain refuses to tear down Main while Main-owned FirstMate children still have lifecycle metadata.", { children });
      }
      return ok("main_children_closed");
    } catch (error) {
      return fail("main_children_unverified", "Captain could not verify that Main-owned FirstMate children are closed.", { detail: error.message });
    }
  }

  async teardown(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const forbidden = this.validateTaskAuthority(params.task);
    if (forbidden) return forbidden;
    if (params.confirmation !== params.task) {
      return fail("teardown_confirmation_required", "Teardown confirmation must exactly equal the task id.");
    }
    if (this.role === "captain") {
      const children = await this.verifyMainChildrenClosed(params.task, signal);
      if (!children.ok) return children;
    }
    const result = await this.run(path.join(this.root, "bin", "fm-teardown.sh"), [params.task], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 180_000,
    });
    if (result.code !== 0) return fail("teardown_refused", "FirstMate refused safe teardown; no force fallback was used.", { detail: cleanText(`${result.stdout}\n${result.stderr}`) });
    let marker = null;
    if (this.role === "captain") {
      this.mainTask = null;
      this.pendingWakeTasks.delete(params.task);
      this.terminalTasks.delete(params.task);
      marker = await this.cleanupOwnedDefaultBranchMarker(signal);
      if (!marker.ok) {
        return fail("main_torn_down_marker_cleanup_failed", "FirstMate tore down Main, but cleanup of Kimiflow's reversible Git marker failed. Main is no longer active; marker cleanup remains retryable on the next exact lifecycle operation.", {
          task: params.task,
          mainTornDown: true,
          markerFailure: marker,
        });
      }
    }
    this.pendingWakeTasks.delete(params.task);
    this.terminalTasks.delete(params.task);
    return ok("torn_down", { task: params.task, receipt: cleanText(result.stdout), ...(marker ? { defaultBranchMarker: marker.code } : {}) });
  }

  async integrate(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const result = await this.run(path.join(this.root, "bin", "fm-merge-local.sh"), [params.task], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 180_000,
    });
    if (result.code !== 0) {
      return fail("local_integration_refused", "Stock FirstMate refused local integration; Kimiflow used no custom merge fallback.", {
        detail: cleanText(`${result.stdout}\n${result.stderr}`),
      });
    }
    return ok("local_integrated", { task: params.task, receipt: cleanText(result.stdout) });
  }

  async startWatcher(restart) {
    if (!this.root) return fail("crew_not_active", "FirstMate is not activated.");
    if (this.watcher) {
      this.watcher.removeAllListeners();
      this.watcher.kill("SIGTERM");
      this.watcher = null;
    }
    const args = restart ? ["--restart"] : [];
    const child = this.spawn(path.join(this.root, "bin", "fm-watch-arm.sh"), args, {
      cwd: this.root,
      env: this.commandEnv(),
      stdio: ["ignore", "pipe", "pipe"],
    });
    this.watcher = child;
    let stdout = "";
    let stderr = "";
    let ready = false;
    const readiness = new Promise((resolve) => {
      const timer = setTimeout(() => {
        if (this.watcher === child) this.watcher = null;
        child.kill("SIGTERM");
        resolve(fail("watcher_unverified", "FirstMate watcher did not become ready in time."));
      }, 20_000);
      const inspect = () => {
        if (ready || !WATCH_READY.test(stdout)) return;
        ready = true;
        clearTimeout(timer);
        resolve(ok("watcher_ready", { watcher: stdout.match(/^watcher: (?:started|attached)[^\n]*/m)?.[0] ?? "ready" }));
      };
      child.stdout?.on("data", (chunk) => { stdout = appendOutput(stdout, chunk); inspect(); });
      child.stderr?.on("data", (chunk) => { stderr = appendOutput(stderr, chunk); });
      child.once("error", (error) => {
        clearTimeout(timer);
        if (!ready) {
          if (this.watcher === child) this.watcher = null;
          resolve(fail("watcher_failed", "FirstMate watcher process failed to start.", { detail: error.message }));
        }
      });
      child.once("close", (code, signal) => {
        clearTimeout(timer);
        if (!ready) {
          if (this.watcher === child) this.watcher = null;
          resolve(fail("watcher_failed", "FirstMate watcher exited before readiness.", { detail: cleanText(`${stdout}\n${stderr}`), exit: code, signal }));
          return;
        }
        this.handleWatcherClose(child, { stdout, stderr, code, signal });
      });
    });
    return readiness;
  }

  async restoreWatcher() {
    let last = fail("watcher_failed", "FirstMate watcher was not restored.");
    for (let attempt = 0; attempt < 3 && !this.stopping; attempt += 1) {
      if (attempt > 0) await this.sleep(250 * (2 ** (attempt - 1)));
      last = await this.startWatcher(false);
      if (last.ok) return last;
    }
    return last;
  }

  async taskFromWake(output) {
    const direct = output.match(/[/\\]state[/\\]([a-z0-9._-]+)\.(?:status|turn-ended)\b/);
    if (direct) return direct[1];
    const target = output.match(/^stale:\s+(\S+)\s*$/m)?.[1];
    if (!target || !this.root) return null;
    try {
      const state = path.join(this.runtimeHome, "state");
      const entries = await fs.readdir(state, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isFile() || !entry.name.endsWith(".meta")) continue;
        const meta = await fs.readFile(path.join(state, entry.name), "utf8");
        if (meta.split(/\r?\n/).includes(`window=${target}`)) return entry.name.slice(0, -5);
      }
    } catch {
      // Unknown wake identity stays visible instead of being guessed away.
    }
    return null;
  }

  async handleWatcherClose(child, result) {
    if (this.stopping || this.watcher !== child) return;
    this.watcher = null;
    const rawOutput = cleanText(`${result.stdout}\n${result.stderr}`);
    const actionable = WAKE_LINE.test(rawOutput) && !WATCH_FAILED.test(rawOutput);
    const output = watcherPresentation(rawOutput, this.workerVerbosity === "quiet");
    if (actionable) {
      const rearmed = await this.restoreWatcher();
      const task = await this.taskFromWake(output);
      if (task && (this.terminalTasks.has(task) || this.pendingWakeTasks.has(task))) return;
      if (task) this.pendingWakeTasks.add(task);
      const suffix = rearmed.ok ? "" : `\nWatcher re-arm failed: ${rearmed.code}.`;
      const instruction = this.role === "captain"
        ? "Use kimiflow_crew action=drain, then action=status for the Kimiflow Main. Keep all user discussion in this Captain session."
        : "Use kimiflow_crew action=drain, then action=status for the named child task. Return only bounded decisions or artifact pointers to Captain through FirstMate status.";
      this.pi?.sendUserMessage?.(
        `KIMIFLOW CREW WAKE\n${output}${suffix}\n${instruction}`,
        { deliverAs: "followUp" },
      );
      return;
    }
    const restored = await this.restoreWatcher();
    if (!restored.ok) {
      const owner = this.role === "captain" ? "CAPTAIN" : "MAIN";
      this.pi?.sendUserMessage?.(
        `KIMIFLOW ${owner} CREW FAILURE\nFirstMate watcher could not be restored after three bounded attempts. No recovery is claimed.\n${output}`,
        { deliverAs: "followUp" },
      );
    }
  }

  shutdown() {
    this.stopping = true;
    if (this.watcher) {
      this.watcher.removeAllListeners();
      this.watcher.kill("SIGTERM");
      this.watcher = null;
    }
  }
}

export default function kimiflowCrewExtension(pi) {
  const adapter = new FirstMateAdapter({ pi });
  pi.registerTool({
    name: "kimiflow_crew",
    label: "Kimiflow crew",
    description: "Role-gated Kimiflow crew control for visible FirstMate Pi sessions in Herdr: Captain owns one control-only Main; Main owns child Ships and Scouts; Workers cannot delegate. Activate first; a failed operation stays failed.",
    parameters: PARAMETERS,
    async execute(_toolCallId, params, signal, _onUpdate, context) {
      return toolResult(await adapter.execute(params, signal, context));
    },
  });
  pi.on?.("session_shutdown", () => adapter.shutdown());
}
