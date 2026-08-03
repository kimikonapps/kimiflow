import { execFile as execFileCallback, spawn as spawnProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const execFile = promisify(execFileCallback);

export const CREW_ACTIONS = ["activate", "spawn", "status", "send", "drain", "teardown"];
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
];

const PARAMETERS = {
  type: "object",
  properties: {
    action: { type: "string", enum: CREW_ACTIONS },
    task: { type: "string", description: "Stable FirstMate task id." },
    kind: { type: "string", enum: ["ship", "scout"], default: "ship" },
    brief: { type: "string", description: "Confirmed Kimiflow work packet for a new worker." },
    message: { type: "string", description: "Text sent to an existing worker." },
    key: { type: "string", enum: ["Enter", "Escape", "C-c"], description: "Optional control key for send." },
    model: { type: "string", description: "Optional Pi model passed through to FirstMate." },
    effort: { type: "string", enum: ["low", "medium", "high", "xhigh", "max"] },
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

function cleanText(value, limit = 12_000) {
  const text = String(value ?? "").trim();
  return text.length <= limit ? text : `${text.slice(0, limit)}\n[truncated]`;
}

function appendOutput(current, chunk, limit = 24_000) {
  const next = current + String(chunk ?? "");
  return next.length <= limit ? next : next.slice(next.length - limit);
}

function startupWakeSection(output) {
  const match = String(output ?? "").match(/(?:^|\n)WAKE QUEUE\r?\n-+\r?\n([\s\S]*?)\r?\n=+\r?\nCONTEXT\r?\n=+/);
  const wakes = cleanText(match?.[1] ?? "");
  return wakes && wakes !== "(no queued wakes)" ? wakes : "";
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
  add(env.KIMIFLOW_FIRSTMATE_ROOT);
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
    this.projectRoot = null;
    this.watcher = null;
    this.stopping = false;
    this.pendingWakeTasks = new Set();
    this.terminalTasks = new Set();
  }

  async execute(params, signal) {
    switch (params.action) {
      case "activate": return this.activate(signal);
      case "spawn": return this.spawnWorker(params, signal);
      case "status": return this.status(params, signal);
      case "send": return this.send(params, signal);
      case "drain": return this.drain(signal);
      case "teardown": return this.teardown(params, signal);
      default: return fail("invalid_action", "Unknown crew action.");
    }
  }

  async activate(signal) {
    if (this.root && this.projectRoot && this.watcher) {
      return ok("already_active", { projectRoot: this.projectRoot, firstMateRoot: this.root });
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
    const projectRoot = cleanText(git.stdout, 4_096);
    if (git.code !== 0 || !path.isAbsolute(projectRoot)) {
      return fail("project_git_root_unavailable", "Kimiflow crew requires Pi to run inside a Git project.", { detail: cleanText(git.stderr) });
    }
    for (const tool of ["herdr", "jq", "treehouse", "pi"]) {
      const check = await this.run("which", [tool], {
        cwd: projectRoot, env: this.env, signal, timeout: 10_000,
      });
      if (check.code !== 0) return fail("firstmate_capability_missing", `Required FirstMate/Herdr capability is unavailable: ${tool}.`);
    }

    const commandEnv = this.commandEnv(root);
    const session = await this.run(path.join(root, "bin", "fm-session-start.sh"), [], {
      cwd: root, env: commandEnv, signal, timeout: 180_000,
    });
    const sessionOutput = `${session.stdout}\n${session.stderr}`;
    if (session.code !== 0 || !/lock acquired: harness pid \d+/.test(sessionOutput) || /READ-ONLY SESSION/.test(sessionOutput)) {
      return fail("firstmate_lock_unavailable", "FirstMate did not grant verified fleet ownership; no worker operation was attempted.", {
        detail: cleanText(sessionOutput),
      });
    }

    this.root = root;
    this.projectRoot = path.resolve(projectRoot);
    const startupWakes = startupWakeSection(sessionOutput);
    const watched = await this.startWatcher(true);
    if (!watched.ok) {
      this.root = null;
      this.projectRoot = null;
      return startupWakes ? { ...watched, startupWakes } : watched;
    }
    return ok("activated", {
      projectRoot: this.projectRoot,
      firstMateRoot: this.root,
      watcher: watched.watcher,
      ...(startupWakes ? { startupWakes } : {}),
    });
  }

  commandEnv(root = this.root) {
    return {
      ...this.env,
      FM_HOME: root,
      FM_ROOT_OVERRIDE: root,
      FM_BACKEND: "herdr",
    };
  }

  requireActive() {
    if (!this.root || !this.projectRoot || !this.watcher) {
      return fail("crew_not_active", "Call kimiflow_crew with action=activate first.");
    }
    return null;
  }

  validateTask(task) {
    if (!TASK_ID.test(task ?? "")) return fail("invalid_task", "Task ids must be 1-64 lowercase letters, digits, dots, underscores or hyphens.");
    return null;
  }

  async ensureProjectMode(signal) {
    const name = path.basename(this.projectRoot);
    if (!PROJECT_NAME.test(name)) return fail("invalid_project_name", "FirstMate requires a simple project-directory name.");
    const registry = path.join(this.root, "data", "projects.md");
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
    if (mode !== "local-only" && mode !== "direct-PR") {
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
    if (mode !== "local-only" && mode !== "direct-PR") {
      return fail("project_mode_unverified", "FirstMate project delivery mode is neither local-only nor direct-PR.");
    }
    return ok("project_mode_ready", { project: name, mode });
  }

  async spawnWorker(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const kind = params.kind ?? "ship";
    const briefPath = path.join(this.root, "data", params.task, "brief.md");
    const confirmedBrief = cleanText(params.brief, 50_000);
    let existingBrief = false;
    try {
      const info = await fs.lstat(briefPath);
      existingBrief = info.isFile() && !info.isSymbolicLink();
    } catch {
      existingBrief = false;
    }
    if (!existingBrief && !confirmedBrief) {
      return fail("brief_required", "A new visible worker requires the already confirmed Kimiflow work packet.");
    }
    if (existingBrief && confirmedBrief) {
      let currentBrief;
      try {
        currentBrief = await fs.readFile(briefPath, "utf8");
      } catch (error) {
        return fail("brief_unavailable", "The existing FirstMate work packet could not be read.", { detail: error.message });
      }
      const expectedPacket = `Confirmed Kimiflow work packet:\n${confirmedBrief}\n\nKimiflow worker boundary:`;
      if (!currentBrief.includes(expectedPacket)) {
        return fail("brief_conflict", "This FirstMate task already has a different confirmed work packet. Use a new task id.");
      }
    }
    const projectMode = await this.ensureProjectMode(signal);
    if (!projectMode.ok) return projectMode;

    if (!existingBrief) {
      const briefArgs = [params.task, path.basename(this.projectRoot)];
      if (kind === "scout") briefArgs.push("--scout");
      const scaffold = await this.run(path.join(this.root, "bin", "fm-brief.sh"), briefArgs, {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
      });
      if (scaffold.code !== 0) {
        return fail("brief_scaffold_failed", "FirstMate could not create the normal worker brief.", { detail: cleanText(`${scaffold.stdout}\n${scaffold.stderr}`) });
      }
      const template = await fs.readFile(briefPath, "utf8");
      const taskMarker = "# Task\n{TASK}";
      if (template.split(taskMarker).length !== 2) {
        return fail("brief_contract_invalid", "FirstMate brief did not contain exactly one canonical task section.");
      }
      const workerContract = [
        "Confirmed Kimiflow work packet:",
        confirmedBrief,
        "",
        "Kimiflow worker boundary:",
        "- This product contract is already confirmed. Do not repeat product intake or ask the user to confirm it again.",
        "- Work autonomously in this isolated FirstMate worktree. Do not open a second user conversation.",
        "- Do not spawn another crew. Return decisions, blockers and results only through this brief's FirstMate status protocol.",
        "- Use the installed Kimiflow skill for implementation, verification and bounded review.",
      ].join("\n");
      const rendered = template
        .replace(taskMarker, `# Task\n${workerContract}`)
        .replaceAll("{TASK}", "the task description");
      await fs.writeFile(briefPath, rendered, { mode: 0o600 });
    }

    const spawnArgs = [params.task, this.projectRoot, "--harness", "pi", "--backend", "herdr"];
    if (params.model) spawnArgs.push("--model", params.model);
    if (params.effort) spawnArgs.push("--effort", params.effort);
    if (kind === "scout") spawnArgs.push("--scout");
    const spawned = await this.run(path.join(this.root, "bin", "fm-spawn.sh"), spawnArgs, {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 240_000,
    });
    if (spawned.code !== 0) {
      return fail("spawn_failed", "FirstMate did not create the requested worker.", { detail: cleanText(`${spawned.stdout}\n${spawned.stderr}`) });
    }
    let peek = { code: 1, stdout: "", stderr: "endpoint did not settle" };
    let lifecycle = { code: 1, stdout: "", stderr: "Pi lifecycle did not settle" };
    let ready = false;
    let trustAccepted = false;
    for (let attempt = 0; attempt < 60; attempt += 1) {
      if (attempt > 0) await this.sleep(250);
      peek = await this.run(path.join(this.root, "bin", "fm-peek.sh"), [params.task, "40"], {
        cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
      });
      if (peek.code !== 0) continue;
      if (/Trust project folder\?/i.test(peek.stdout)) {
        if (trustAccepted) continue;
        const trust = await this.run(path.join(this.root, "bin", "fm-send.sh"), [params.task, "--key", "Enter"], {
          cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
        });
        if (trust.code !== 0) {
          return fail("worker_trust_failed", "The visible Pi worker is reachable, but FirstMate could not accept its project-worktree trust gate.", {
            detail: cleanText(`${trust.stdout}\n${trust.stderr}`),
          });
        }
        trustAccepted = true;
        continue;
      }
      if (PI_WORKER_READY.test(peek.stdout)) {
        lifecycle = await this.run(path.join(this.root, "bin", "fm-crew-state.sh"), [params.task], {
          cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
        });
        if (lifecycle.code === 0 && PI_LIFECYCLE_READY.test(lifecycle.stdout)) {
          ready = true;
          break;
        }
      }
    }
    if (!ready) {
      return fail("spawn_unverified", "FirstMate returned from spawn, but the exact endpoint and Pi lifecycle were not both verified.", {
        detail: cleanText(`${peek.stdout}\n${peek.stderr}\n${lifecycle.stdout}\n${lifecycle.stderr}`),
      });
    }
    this.pendingWakeTasks.delete(params.task);
    this.terminalTasks.delete(params.task);
    return ok("worker_reachable", {
      task: params.task,
      kind,
      mode: projectMode.mode,
      endpoint: cleanText(peek.stdout),
      spawnReceipt: cleanText(spawned.stdout, 4_000),
    });
  }

  async status(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    const result = await this.run(path.join(this.root, "bin", "fm-crew-state.sh"), [params.task], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 30_000,
    });
    if (result.code !== 0) return fail("status_failed", "FirstMate could not read the worker state.", { detail: cleanText(`${result.stdout}\n${result.stderr}`) });
    const state = cleanText(result.stdout);
    this.pendingWakeTasks.delete(params.task);
    if (/^state: (?:done|failed)\b/m.test(state)) this.terminalTasks.add(params.task);
    return ok("status", { task: params.task, state });
  }

  async send(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
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

  async teardown(params, signal) {
    const inactive = this.requireActive();
    if (inactive) return inactive;
    const invalid = this.validateTask(params.task);
    if (invalid) return invalid;
    if (params.confirmation !== params.task) {
      return fail("teardown_confirmation_required", "Teardown confirmation must exactly equal the task id.");
    }
    const result = await this.run(path.join(this.root, "bin", "fm-teardown.sh"), [params.task], {
      cwd: this.root, env: this.commandEnv(), signal, timeout: 180_000,
    });
    if (result.code !== 0) return fail("teardown_refused", "FirstMate refused safe teardown; no force fallback was used.", { detail: cleanText(`${result.stdout}\n${result.stderr}`) });
    this.pendingWakeTasks.delete(params.task);
    this.terminalTasks.delete(params.task);
    return ok("torn_down", { task: params.task, receipt: cleanText(result.stdout) });
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
      const state = path.join(this.root, "state");
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
    const output = cleanText(`${result.stdout}\n${result.stderr}`);
    const actionable = WAKE_LINE.test(output) && !WATCH_FAILED.test(output);
    if (actionable) {
      const rearmed = await this.restoreWatcher();
      const task = await this.taskFromWake(output);
      if (task && (this.terminalTasks.has(task) || this.pendingWakeTasks.has(task))) return;
      if (task) this.pendingWakeTasks.add(task);
      const suffix = rearmed.ok ? "" : `\nWatcher re-arm failed: ${rearmed.code}.`;
      this.pi?.sendUserMessage?.(
        `KIMIFLOW CREW WAKE\n${output}${suffix}\nUse kimiflow_crew action=drain, then action=status for the named task. Keep all user discussion in this Main session.`,
        { deliverAs: "followUp" },
      );
      return;
    }
    const restored = await this.restoreWatcher();
    if (!restored.ok) {
      this.pi?.sendUserMessage?.(
        `KIMIFLOW CREW FAILURE\nFirstMate watcher could not be restored after three bounded attempts. No recovery is claimed.\n${output}`,
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
    description: "Optionally delegate confirmed, genuinely independent Kimiflow work to visible FirstMate Pi workers in Herdr. The current Pi session remains Main. Activate first; a failed operation stays failed.",
    parameters: PARAMETERS,
    async execute(_toolCallId, params, signal) {
      return toolResult(await adapter.execute(params, signal));
    },
  });
  pi.on?.("session_shutdown", () => adapter.shutdown());
}
