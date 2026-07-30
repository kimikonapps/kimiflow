import {
  execFileSync,
  spawn as nodeSpawn,
} from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  closeSync,
  constants,
  existsSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  realpathSync,
} from "node:fs";
import {
  dirname,
  isAbsolute,
  resolve,
} from "node:path";
import { TextDecoder } from "node:util";

const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const WORKER = /^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$/;
const RUN = /^\.kimiflow\/(?!session(?:\/|$))(?!.*\/\.\.(?:\/|$))[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$/;
const INTENT_DIGEST = /^sha256:[0-9a-f]{64}$/;
const BRIDGE_ENV = "KIMIFLOW_PI_BRIDGE_BINDING";
const ACTIVE_RUN_ENV = "KIMIFLOW_PI_ACTIVE_RUN";
const TRANSPORT_PROMPT_ENV = "KIMIFLOW_PI_TRANSPORT_PROMPT";
const SUBAGENT_TREE_TOKEN_ENV = "__KIMIFLOW_PI_SUBAGENT_TOKEN";
const TREE_TOKEN = /^[0-9a-f]{64}$/;
const SUBAGENT_RESERVATION_DIRECTORY = "PI-SUBAGENT-SLOTS-v1";
const SUBAGENT_GENERATION_AUTHORITY_DIRECTORY = "PI-SUBAGENT-GENERATIONS-v1";
const MAX_SUBAGENTS = 3;
const MAX_TASK_BYTES = 64 * 1024;
const MAX_SUBAGENT_OUTPUT = 256 * 1024;
const MAX_HOOK_OUTPUT = 64 * 1024;
const BINDING_KEYS = [
  "schema_version",
  "root",
  "captain_session_id",
  "worker_id",
];
const READ_TOOLS = new Set(["read", "grep", "find", "glob", "ls"]);
const WRITE_TOOLS = new Set(["write", "edit", "patch", "apply_patch"]);
const TRUSTED_INTAKE_HOOK = /^(?:(?:bash|sh) )?(?:\.\/)?hooks\/(?:active-run|clarify-gate|intake-gate|state-gate|current-state-gate|workspace-preflight)\.sh(?: |$)/;
const CONTROL_HOOK = /^(?:(?:bash|sh) )?(?:\.\/)?hooks\/([a-z-]+)\.sh(?: +([^ ]+))?/;
const ACTIVE_RUN_INTAKE_COMMANDS = new Set([
  "await-user",
  "conflict-check",
  "next-action",
  "phase-read",
  "phase-read-gate",
  "phase-read-status",
  "pin-intent-lock",
  "rescope",
  "start",
  "status",
]);
const WORKSPACE_PREFLIGHT_INTAKE_COMMANDS = new Set(["status", "write-gate"]);
const SAFE_CONTROL_COMMAND = /^[A-Za-z0-9._/@:=+,'"\- ]+$/;
const ROOT_OPTION = /(?:^| )--root(?:=| +)(?:"([^"]+)"|'([^']+)'|([^ "'=]+))/g;
const ROOT_MENTION = /(?:^| )--root(?:(?:=| +)|$)/g;
const PROTECTED_RUN_ARTIFACT = /^(?:INTENT-LOCK\.json|INTAKE-RECEIPT-[12]\.json)$/;
const BOOTSTRAP_ARTIFACT = /^(?:STATE\.md|INTENT\.md|INTAKE(?:-2)?\.md|WORKSPACE-PREFLIGHT\.json|ADAPTIVE-CLASSIFICATION\.json)$/;

// Pi loads this package without project-local dependencies. This is the
// runtime shape produced by Type.Object({ task: Type.String(...) }).
const SUBAGENT_PARAMETERS = Object.freeze({
  type: "object",
  properties: {
    task: {
      type: "string",
      minLength: 1,
      maxLength: MAX_TASK_BYTES,
      description: "One bounded task for a fresh Kimiflow Pi subagent.",
    },
  },
  required: ["task"],
  additionalProperties: false,
});

function exactKeys(value, keys) {
  return value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

function exactId(value, label) {
  if (typeof value !== "string" || !IDENTITY.test(value)) {
    throw new Error(`${label}_invalid`);
  }
  return value;
}

function validateReservationDirectory(pathname, privateDirectory = false) {
  const info = lstatSync(pathname);
  if (
    !info.isDirectory()
    || info.isSymbolicLink()
    || realpathSync(pathname) !== pathname
    || (privateDirectory && (info.mode & 0o077) !== 0)
  ) {
    throw new Error("subagent_capacity_invalid");
  }
}

function ensureReservationDirectory(pathname, privateDirectory = false) {
  try {
    mkdirSync(pathname, { mode: 0o700 });
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
  }
  validateReservationDirectory(pathname, privateDirectory);
}

function directoryIdentity(pathname) {
  const info = lstatSync(pathname);
  return Object.freeze({ device: info.dev, inode: info.ino });
}

function validateDirectoryIdentity(pathname, identity) {
  validateReservationDirectory(pathname, true);
  const current = lstatSync(pathname);
  if (
    current.dev !== identity.device
    || current.ino !== identity.inode
  ) {
    throw new Error("subagent_capacity_invalid");
  }
}

function reservationDirectory(authority) {
  const kimiflow = resolve(authority.root, ".kimiflow");
  const session = resolve(kimiflow, "session");
  const registry = resolve(session, SUBAGENT_RESERVATION_DIRECTORY);
  const authorityRegistry = resolve(
    session,
    SUBAGENT_GENERATION_AUTHORITY_DIRECTORY,
  );
  const generation = resolve(registry, authority.worker_id);
  const generationMarker = resolve(
    registry,
    `.generation-${authority.worker_id}`,
  );
  const authorityMarker = resolve(authorityRegistry, authority.worker_id);
  if (realpathSync(authority.root) !== authority.root) {
    throw new Error("subagent_capacity_invalid");
  }
  ensureReservationDirectory(kimiflow);
  ensureReservationDirectory(session);
  ensureReservationDirectory(registry, true);
  ensureReservationDirectory(authorityRegistry, true);
  if (existsSync(authorityMarker)) {
    try {
      validateReservationDirectory(authorityMarker, true);
      validateReservationDirectory(generationMarker, true);
      validateReservationDirectory(generation, true);
    } catch {
      throw new Error("subagent_capacity_invalid");
    }
  } else {
    ensureReservationDirectory(generation, true);
    ensureReservationDirectory(generationMarker, true);
    ensureReservationDirectory(authorityMarker, true);
  }
  return Object.freeze({
    path: generation,
    identity: directoryIdentity(generation),
  });
}

function validateSlot(pathname) {
  validateReservationDirectory(pathname, true);
}

function durableReservationCount(directory) {
  const entries = readdirSync(directory);
  const slots = new Set();
  for (const entry of entries) {
    const match = entry.match(/^slot-([1-3])$/);
    if (match === null) throw new Error("subagent_capacity_invalid");
    const slot = Number(match[1]);
    if (slots.has(slot)) throw new Error("subagent_capacity_invalid");
    validateSlot(resolve(directory, entry));
    slots.add(slot);
  }
  return slots.size;
}

export function createFileSubagentReservations(authority) {
  const directory = reservationDirectory(authority);
  const validateDirectory = () => {
    validateDirectoryIdentity(directory.path, directory.identity);
  };
  return Object.freeze({
    count() {
      validateDirectory();
      return durableReservationCount(directory.path);
    },
    reserve() {
      validateDirectory();
      for (let slot = 1; slot <= MAX_SUBAGENTS; slot += 1) {
        const pathname = resolve(directory.path, `slot-${slot}`);
        try {
          mkdirSync(pathname, { mode: 0o700 });
          const directoryDescriptor = openSync(
            directory.path,
            constants.O_RDONLY | (constants.O_DIRECTORY ?? 0),
          );
          try {
            fsyncSync(directoryDescriptor);
          } finally {
            closeSync(directoryDescriptor);
          }
          return slot;
        } catch (error) {
          if (error?.code === "EEXIST") {
            validateSlot(pathname);
            continue;
          }
          throw error;
        }
      }
      throw new Error("subagent_capacity");
    },
  });
}

function createMemorySubagentReservations() {
  let count = 0;
  return Object.freeze({
    count() {
      return count;
    },
    reserve() {
      if (count >= MAX_SUBAGENTS) throw new Error("subagent_capacity");
      count += 1;
      return count;
    },
  });
}

function parseSelection(value) {
  let parsed;
  try {
    parsed = JSON.parse(value ?? "");
  } catch {
    throw new Error("pi_selection_invalid");
  }
  if (
    !exactKeys(parsed, ["provider", "model", "thinking"])
    || !/^[a-z0-9][a-z0-9._-]{0,63}$/.test(parsed.provider)
    || !/^[A-Za-z0-9@][A-Za-z0-9._/@:-]{0,191}$/.test(parsed.model)
    || !/^(off|minimal|low|medium|high|xhigh|max)$/.test(parsed.thinking)
  ) {
    throw new Error("pi_selection_invalid");
  }
  return parsed;
}

function parseTransportPrompt(value) {
  let parsed;
  try {
    parsed = JSON.parse(value ?? "");
  } catch {
    throw new Error("pi_transport_prompt_invalid");
  }
  if (
    typeof parsed !== "string"
    || parsed.length === 0
    || Buffer.byteLength(parsed, "utf8") > MAX_TASK_BYTES
  ) {
    throw new Error("pi_transport_prompt_invalid");
  }
  return parsed;
}

export function loadWorkerAuthority(environment = process.env) {
  const encoded = environment[BRIDGE_ENV];
  if (encoded === undefined) return null;
  let binding;
  try {
    binding = JSON.parse(encoded);
  } catch {
    throw new Error("worker_binding_invalid");
  }
  if (
    !exactKeys(binding, BINDING_KEYS)
    || binding.schema_version !== 1
    || !isAbsolute(binding.root)
    || realpathSync(binding.root) !== binding.root
    || !WORKER.test(binding.worker_id)
    || !IDENTITY.test(binding.captain_session_id)
  ) {
    throw new Error("worker_binding_invalid");
  }
  const executable = environment.KIMIFLOW_PI_EXECUTABLE;
  if (
    typeof executable !== "string"
    || !isAbsolute(executable)
    || realpathSync(executable) !== executable
  ) {
    throw new Error("pi_executable_invalid");
  }
  const activeRun = environment[ACTIVE_RUN_ENV];
  if (
    typeof activeRun !== "string"
    || !isAbsolute(activeRun)
    || realpathSync(activeRun) !== activeRun
    || activeRun.slice(activeRun.lastIndexOf("/") + 1) !== "active-run.sh"
  ) {
    throw new Error("active_run_hook_invalid");
  }
  return Object.freeze({
    ...binding,
    executable,
    activeRun,
    selection: Object.freeze(parseSelection(environment.KIMIFLOW_PI_SELECTION)),
    transportPrompt: parseTransportPrompt(environment[TRANSPORT_PROMPT_ENV]),
  });
}

function intakeState(authority) {
  try {
    const active = JSON.parse(readFileSync(
      resolve(authority.root, ".kimiflow/session/ACTIVE_RUN.json"),
      "utf8",
    ));
    const run = typeof active?.run === "string" && RUN.test(active.run)
      ? active.run
      : null;
    if (run === null || !INTENT_DIGEST.test(active?.intent_lock_digest ?? "")) {
      return { state: "intake", run };
    }
    const lock = readFileSync(resolve(authority.root, run, "INTENT-LOCK.json"));
    const digest = `sha256:${createHash("sha256").update(lock).digest("hex")}`;
    return {
      state: digest === active.intent_lock_digest ? "confirmed" : "intake",
      run,
    };
  } catch {
    return { state: "intake", run: null };
  }
}

function normalizedIntakeState(value) {
  if (value === "confirmed") return { state: "confirmed", run: null };
  if (value === "intake") return { state: "intake", run: null };
  if (
    value !== null
    && typeof value === "object"
    && ["confirmed", "intake"].includes(value.state)
    && (value.run === null || (typeof value.run === "string" && RUN.test(value.run)))
  ) {
    return value;
  }
  return { state: "intake", run: null };
}

function mutationPath(event) {
  const input = event?.input ?? event?.arguments ?? {};
  return input.path ?? input.file_path ?? input.filePath ?? null;
}

function inside(parent, candidate) {
  return candidate === parent || candidate.startsWith(`${parent}/`);
}

function confinedTarget(root, cwd, supplied) {
  const target = resolve(cwd, supplied);
  if (!inside(root, target)) return null;
  let existing = existsSync(target) ? target : dirname(target);
  while (!existsSync(existing) && existing !== dirname(existing)) {
    existing = dirname(existing);
  }
  try {
    if (realpathSync(existing) !== existing) return null;
    if (existsSync(target)) {
      const info = lstatSync(target);
      if (
        info.isSymbolicLink()
        || !info.isFile()
        || info.nlink !== 1
        || realpathSync(target) !== target
      ) return null;
    }
  } catch {
    return null;
  }
  return target;
}

function trustedControlCommand(authority, context, command) {
  if (
    typeof command !== "string"
    || command.length > 4096
    || !SAFE_CONTROL_COMMAND.test(command)
    || !TRUSTED_INTAKE_HOOK.test(command)
  ) {
    return false;
  }
  try {
    const cwd = realpathSync(resolve(context?.cwd ?? authority.root));
    if (cwd !== authority.root) return false;
    const hook = command.match(CONTROL_HOOK);
    if (hook === null) return false;
    if (
      hook[1] === "active-run"
      && !ACTIVE_RUN_INTAKE_COMMANDS.has(hook[2] ?? "")
    ) {
      return false;
    }
    if (
      hook[1] === "workspace-preflight"
      && !WORKSPACE_PREFLIGHT_INTAKE_COMMANDS.has(hook[2] ?? "")
    ) {
      return false;
    }
    const roots = [...command.matchAll(ROOT_OPTION)].map(
      (match) => match[1] ?? match[2] ?? match[3],
    );
    if (roots.length !== [...command.matchAll(ROOT_MENTION)].length) return false;
    for (const supplied of roots) {
      const target = resolve(cwd, supplied);
      if (target !== authority.root || realpathSync(target) !== authority.root) {
        return false;
      }
    }
    return true;
  } catch {
    return false;
  }
}

function writableRunArtifact(authority, state, target) {
  const kimiflowRoot = resolve(authority.root, ".kimiflow");
  if (!inside(kimiflowRoot, target)) return false;
  const basename = target.slice(target.lastIndexOf("/") + 1);
  if (PROTECTED_RUN_ARTIFACT.test(basename)) return false;
  if (state.run !== null) {
    return dirname(target) === resolve(authority.root, state.run)
      && BOOTSTRAP_ARTIFACT.test(basename);
  }
  const relative = target.slice(kimiflowRoot.length + 1);
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\/[^/]+$/.test(relative)
    && BOOTSTRAP_ARTIFACT.test(basename);
}

export function createPreIntakeGuard(authority, {
  getIntakeState = () => intakeState(authority),
  intakeState: legacyIntakeState,
} = {}) {
  const readState = legacyIntakeState ?? getIntakeState;
  return async (event, context) => {
    const state = normalizedIntakeState(readState());
    if (state.state === "confirmed") return undefined;
    const toolName = String(event?.toolName ?? event?.name ?? "").toLowerCase();
    if (READ_TOOLS.has(toolName)) return undefined;
    if (toolName === "bash" || toolName === "shell") {
      const command = event?.input?.command ?? event?.arguments?.command;
      if (trustedControlCommand(authority, context, command)) {
        return undefined;
      }
      return {
        block: true,
        reason: "Kimiflow intake is not confirmed; product commands are blocked.",
      };
    }
    if (WRITE_TOOLS.has(toolName)) {
      const supplied = mutationPath(event);
      if (typeof supplied === "string" && supplied.length > 0) {
        const cwd = resolve(context?.cwd ?? authority.root);
        const target = confinedTarget(authority.root, cwd, supplied);
        if (target !== null && writableRunArtifact(authority, state, target)) {
          return undefined;
        }
      }
    }
    return {
      block: true,
      reason: "Kimiflow intake is not confirmed; only bounded run intake artifacts are writable.",
    };
  };
}

function exited(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve();
  }
  return new Promise((done) => {
    child.once("exit", done);
    child.once("error", done);
  });
}

function signalChild(child, signal) {
  if (child.kimiflowProcessGroup !== true) return child.kill(signal);
  try {
    process.kill(-child.pid, signal);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return child.kill(signal);
    throw error;
  }
}

function processGroupExited(child) {
  if (child.kimiflowProcessGroup !== true) return exited(child);
  return new Promise((done, reject) => {
    const probe = () => {
      try {
        process.kill(-child.pid, 0);
        setTimeout(probe, 10);
      } catch (error) {
        if (error?.code === "ESRCH") done();
        else if (error?.code === "EPERM") setTimeout(probe, 10);
        else reject(error);
      }
    };
    probe();
  });
}

function taggedProcessPids(token) {
  if (typeof token !== "string" || !TREE_TOKEN.test(token)) {
    throw new Error("subagent_process_cleanup_unavailable");
  }
  const marker = `${SUBAGENT_TREE_TOKEN_ENV}=${token}`;
  if (process.platform === "linux" && existsSync("/proc")) {
    const found = [];
    for (const entry of readdirSync("/proc")) {
      if (!/^[0-9]+$/.test(entry)) continue;
      const pid = Number(entry);
      if (pid <= 1 || pid === process.pid) continue;
      try {
        const environment = readFileSync(`/proc/${entry}/environ`);
        if (environment.toString("utf8").split("\0").includes(marker)) {
          found.push(pid);
        }
      } catch {
        // The process may exit or become unreadable between discovery and pinning.
      }
    }
    return found;
  }
  const executable = existsSync("/bin/ps")
    ? "/bin/ps"
    : (existsSync("/usr/bin/ps") ? "/usr/bin/ps" : null);
  if (executable === null) {
    throw new Error("subagent_process_cleanup_unavailable");
  }
  let output;
  try {
    output = execFileSync(
      executable,
      ["eww", "-axo", "pid=,command="],
      {
        encoding: "utf8",
        env: { LC_ALL: "C", PATH: "/usr/bin:/bin" },
        maxBuffer: 64 * 1024 * 1024,
      },
    );
  } catch {
    throw new Error("subagent_process_cleanup_unavailable");
  }
  const needle = ` ${marker}`;
  const found = [];
  for (const line of output.split("\n")) {
    if (!line.includes(needle)) continue;
    const match = line.match(/^\s*([0-9]+)\s+/);
    if (match === null) continue;
    const pid = Number(match[1]);
    if (pid > 1 && pid !== process.pid) found.push(pid);
  }
  return found;
}

function killTaggedProcess(pid) {
  try {
    process.kill(-pid, "SIGKILL");
    return;
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  try {
    process.kill(pid, "SIGKILL");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
}

async function stopTaggedProcesses(token) {
  let stable = 0;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const found = taggedProcessPids(token);
    for (const pid of found) killTaggedProcess(pid);
    if (found.length === 0) {
      stable += 1;
      if (stable >= 2) return;
    } else {
      stable = 0;
    }
    await new Promise((done) => setTimeout(done, 10));
  }
  throw new Error("subagent_process_cleanup_failed");
}

async function stopChild(child) {
  let failure = null;
  try {
    if (
      child.kimiflowProcessGroup === true
      || (child.exitCode === null && child.signalCode === null)
    ) {
      signalChild(child, "SIGTERM");
      let timer;
      const graceful = await Promise.race([
        Promise.all([exited(child), processGroupExited(child)]).then(() => true),
        new Promise((done) => {
          timer = setTimeout(() => done(false), 1000);
        }),
      ]);
      clearTimeout(timer);
      if (!graceful) {
        signalChild(child, "SIGKILL");
        await Promise.all([exited(child), processGroupExited(child)]);
      }
    }
  } catch (error) {
    failure = error;
  }
  try {
    await stopTaggedProcesses(child.kimiflowCleanupToken);
  } catch (error) {
    failure ??= error;
  }
  if (failure !== null) throw failure;
}

function boundedTask(value) {
  if (
    typeof value !== "string"
    || value.trim().length === 0
    || Buffer.byteLength(value, "utf8") > MAX_TASK_BYTES
  ) {
    throw new Error("subagent_task_invalid");
  }
  return value.trim();
}

function assistantMessage(event) {
  const message = event?.message;
  if (message?.role !== "assistant" || !Array.isArray(message.content)) {
    return { text: null, failed: false };
  }
  const text = message.content
    .filter((item) => item?.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("");
  return {
    text: text || null,
    failed: ["error", "aborted", "pending"].includes(message.stopReason),
  };
}

function collectSubagent(child, authority, sessionId) {
  return new Promise((done, reject) => {
    const chunks = [];
    let size = 0;
    let outputFailure = null;
    let processSettled = false;
    child.stdout?.on("data", (chunk) => {
      const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      size += value.length;
      if (size > MAX_SUBAGENT_OUTPUT) {
        outputFailure = new Error("subagent_output_oversized");
        signalChild(child, "SIGKILL");
        return;
      }
      chunks.push(value);
    });
    child.once("error", (error) => {
      if (processSettled) return;
      processSettled = true;
      reject(error);
    });
    child.once("close", (code, signal) => {
      if (processSettled) return;
      processSettled = true;
      if (outputFailure) return reject(outputFailure);
      if (code !== 0 || signal !== null) {
        return reject(new Error("subagent_process_failed"));
      }
      try {
        let decoded;
        try {
          decoded = new TextDecoder("utf-8", { fatal: true })
            .decode(Buffer.concat(chunks));
        } catch {
          throw new Error("subagent_output_invalid");
        }
        const events = decoded
          .split("\n")
          .filter(Boolean)
          .map((line) => JSON.parse(line));
        const header = events.shift();
        if (
          header?.type !== "session"
          || header.version !== 3
          || header.id !== sessionId
          || header.cwd !== authority.root
        ) {
          throw new Error("subagent_session_invalid");
        }
        let active = false;
        let ended = false;
        let settled = false;
        let currentResult = null;
        let currentFailed = false;
        let result = null;
        let failed = false;
        for (const event of events) {
          if (event?.type === "session") {
            throw new Error("subagent_session_invalid");
          }
          if (event?.type === "agent_start") {
            if (active || settled) throw new Error("subagent_lifecycle_invalid");
            active = true;
            currentResult = null;
            currentFailed = false;
          } else if (event?.type === "message_end") {
            if (!active) throw new Error("subagent_lifecycle_invalid");
            const message = assistantMessage(event);
            if (message.text) currentResult = message.text;
            currentFailed ||= message.failed;
          } else if (event?.type === "agent_end") {
            if (!active || settled) throw new Error("subagent_lifecycle_invalid");
            active = false;
            ended = true;
            result = currentResult;
            failed = currentFailed;
          } else if (event?.type === "agent_settled") {
            if (active || !ended || settled) {
              throw new Error("subagent_lifecycle_invalid");
            }
            settled = true;
          }
        }
        if (active || !ended || !settled || failed || !result) {
          throw new Error("subagent_lifecycle_incomplete");
        }
        done(result);
      } catch (error) {
        reject(error);
      }
      return undefined;
    });
  });
}

export class GenerationSupervisor {
  constructor({
    authority,
    spawn = nodeSpawn,
    idFactory = randomUUID,
    reservations = null,
  } = {}) {
    if (!authority) throw new Error("subagent_capacity_invalid");
    if (
      reservations !== null
      && (
        typeof reservations !== "object"
        || typeof reservations.count !== "function"
        || typeof reservations.reserve !== "function"
      )
    ) {
      throw new Error("subagent_capacity_invalid");
    }
    this.authority = authority;
    this.spawn = spawn;
    this.idFactory = idFactory;
    this.reservations = reservations ?? createMemorySubagentReservations();
    this.active = new Set();
    this.launched = 0;
    this.workerSessionId = null;
    this.stopping = false;
  }

  generationId() {
    return `generation-${this.authority.worker_id}`;
  }

  restoreReservations(_context) {
    if (this.active.size !== 0 || this.launched !== 0) {
      throw new Error("subagent_capacity_invalid");
    }
    this.launched = this.reservations.count();
    if (
      !Number.isInteger(this.launched)
      || this.launched < 0
      || this.launched > MAX_SUBAGENTS
    ) {
      throw new Error("subagent_capacity_invalid");
    }
    return this.launched;
  }

  async launchSubagent(task) {
    const input = boundedTask(task);
    if (this.stopping) throw new Error("subagent_generation_stopping");
    if (this.launched >= MAX_SUBAGENTS) throw new Error("subagent_capacity");
    exactId(this.workerSessionId, "worker_session");
    const sessionId = exactId(this.idFactory(), "subagent_identity");
    const slot = this.reservations.reserve();
    if (
      !Number.isInteger(slot)
      || slot < 1
      || slot > MAX_SUBAGENTS
    ) {
      throw new Error("subagent_capacity_invalid");
    }
    const subagentId = `subagent-${slot}-${sessionId.slice(0, 12)}`;
    this.launched = Math.max(this.launched, slot);
    const selection = this.authority.selection;
    const args = [
      "--mode", "json",
      "--no-extensions",
      "--provider", selection.provider,
      "--model", selection.model,
      "--thinking", selection.thinking,
      "--session-id", sessionId,
      `Kimiflow bounded subagent task:\n${input}`,
    ];
    const environment = { ...process.env };
    for (const key of Object.keys(environment)) {
      if (key.startsWith("KIMIFLOW_")) delete environment[key];
    }
    const cleanupToken = createHash("sha256")
      .update(`${randomUUID()}:${sessionId}`)
      .digest("hex");
    environment[SUBAGENT_TREE_TOKEN_ENV] = cleanupToken;
    const child = this.spawn(this.authority.executable, args, {
      cwd: this.authority.root,
      env: environment,
      detached: true,
      stdio: ["ignore", "pipe", "ignore"],
    });
    if (!Number.isInteger(child?.pid) || child.pid < 1) {
      child?.kill?.("SIGKILL");
      throw new Error("subagent_spawn_failed");
    }
    child.kimiflowProcessGroup = true;
    child.kimiflowCleanupToken = cleanupToken;
    this.active.add(child);
    try {
      return {
        generation: this.generationId(),
        workerId: this.authority.worker_id,
        workerSessionId: this.workerSessionId,
        subagentId,
        subagentSessionId: sessionId,
        status: "completed",
        result: await collectSubagent(child, this.authority, sessionId),
        backend: "process",
      };
    } finally {
      await stopChild(child);
      this.active.delete(child);
    }
  }

  async stopGeneration() {
    this.stopping = true;
    const children = [...this.active];
    const stopped = await Promise.allSettled(children.map(stopChild));
    this.active.clear();
    const failure = stopped.find(({ status }) => status === "rejected");
    if (failure) throw failure.reason;
    return {
      generation: this.generationId(),
      stopped: children.length,
      released: true,
    };
  }
}

export function forwardPromptContext(
  authority,
  _event,
  context,
  spawn = nodeSpawn,
) {
  const sessionId = exactId(
    context?.sessionManager?.getSessionId?.()
      ?? context?.sessionId,
    "worker_session",
  );
  const prompt = authority.transportPrompt;
  if (typeof prompt !== "string" || Buffer.byteLength(prompt, "utf8") > MAX_TASK_BYTES) {
    throw new Error("prompt_context_invalid");
  }
  return new Promise((resolvePromise, reject) => {
    const child = spawn(authority.activeRun, ["prompt-context"], {
      cwd: authority.root,
      env: {
        ...process.env,
        KIMIFLOW_HOST: "pi",
        KIMIFLOW_SESSION_HOST: "pi",
        KIMIFLOW_SESSION_ID: sessionId,
      },
      detached: false,
      stdio: ["pipe", "pipe", "ignore"],
    });
    if (!Number.isInteger(child?.pid) || child.pid < 1) {
      child?.kill?.("SIGKILL");
      reject(new Error("prompt_context_spawn_failed"));
      return;
    }
    const chunks = [];
    let size = 0;
    let settled = false;
    child.stdout?.on("data", (chunk) => {
      const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      size += value.length;
      if (size > MAX_HOOK_OUTPUT) {
        child.kill("SIGKILL");
        return;
      }
      chunks.push(value);
    });
    child.once("error", (error) => {
      if (settled) return;
      settled = true;
      reject(error);
    });
    child.once("exit", (code, signal) => {
      if (settled) return;
      settled = true;
      if (size > MAX_HOOK_OUTPUT) {
        reject(new Error("prompt_context_output_oversized"));
        return;
      }
      if (code !== 0 || signal !== null) {
        reject(new Error("prompt_context_failed"));
        return;
      }
      const output = Buffer.concat(chunks).toString("utf8").trim();
      if (!output) {
        resolvePromise(undefined);
        return;
      }
      try {
        const parsed = JSON.parse(output);
        const content = parsed?.hookSpecificOutput?.additionalContext;
        resolvePromise(
          typeof content === "string" && content
            ? {
                message: {
                  customType: "kimiflow-active-run",
                  content,
                  display: false,
                },
              }
            : undefined,
        );
      } catch {
        reject(new Error("prompt_context_output_invalid"));
      }
    });
    child.stdin?.end(JSON.stringify({
      cwd: authority.root,
      session_id: sessionId,
      prompt,
    }));
  });
}

export default function registerWorkerExtension(pi, options = {}) {
  const environment = options.environment ?? process.env;
  const authority = loadWorkerAuthority(environment);
  if (authority === null) return null;
  const supervisor = new GenerationSupervisor({
    authority,
    spawn: options.spawn ?? nodeSpawn,
    idFactory: options.idFactory ?? randomUUID,
    reservations: options.reservations
      ?? createFileSubagentReservations(authority),
  });
  pi.on("tool_call", createPreIntakeGuard(authority, options));
  pi.on("before_agent_start", (event, context) => forwardPromptContext(
    authority,
    event,
    context,
    options.hookSpawn ?? nodeSpawn,
  ));
  pi.on("session_start", (_event, context) => {
    supervisor.workerSessionId = exactId(
      context?.sessionManager?.getSessionId?.()
        ?? context?.sessionId,
      "worker_session",
    );
    supervisor.restoreReservations(context);
    environment.KIMIFLOW_HOST = "pi";
    environment.KIMIFLOW_SESSION_HOST = "pi";
    environment.KIMIFLOW_SESSION_ID = supervisor.workerSessionId;
  });
  pi.registerTool({
    name: "kimiflow_subagent",
    label: "Kimiflow Subagent",
    description: "Run one bounded fresh Pi subagent for this Kimiflow worker generation.",
    parameters: SUBAGENT_PARAMETERS,
    async execute(_toolCallId, params) {
      const value = await supervisor.launchSubagent(params.task);
      return {
        content: [{ type: "text", text: JSON.stringify(value) }],
        details: value,
      };
    },
  });
  pi.on("session_shutdown", () => supervisor.stopGeneration());
  return supervisor;
}
