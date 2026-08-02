import {
  execFile as nodeExecFile,
  execFileSync,
  spawn as nodeSpawn,
} from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import {
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(nodeExecFile);
const IDENTITY = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const RUN_IDENTITY = /^\.kimiflow\/(?!.*\/\.\.(?:\/|$))[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$/;
const MODEL_SELECTION = /^[a-z0-9][a-z0-9._-]{0,63}\/[A-Za-z0-9@][A-Za-z0-9._/@:-]{0,191}:(off|minimal|low|medium|high|xhigh|max)$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const BINDING_ENTRY = "kimiflow_pi_bridge_binding_v1";
const CLAIM_ENTRY = "kimiflow_pi_bridge_claim_v1";
const ATTENTION_MESSAGE = "kimiflow";
const LEGACY_ATTENTION_MESSAGE = "kimiflow_attention";
const BRIDGE_ENV = "KIMIFLOW_PI_BRIDGE_BINDING";
const START_CLAIM_ENV = "KIMIFLOW_PI_START_CLAIM";
const START_CLAIM_NAME = "PI-BRIDGE-START-CLAIM";
const CAPTAIN_DIALOG_PROMPT = [
  "This Pi session is the responsive Kimiflow Captain; implementation work stays in visible Worker and subagent sessions.",
  "Continue ordinary conversation normally, even while Kimiflow is waiting.",
  "Kimiflow questions appear here as Kimiflow messages. Never treat unrelated user input as an answer.",
  "When the user clearly answers an open Kimiflow question, call kimiflow_status and then kimiflow_reply with the exact run, worker, and provider-session identity returned by the Runner.",
  "Never ask the user to open or answer inside a Worker tab.",
].join(" ");
const CLEANUP_LEASES_NAME = "PI-CLEANUP-LEASES-v1";
const STATUS_LIMIT = 320;
const DEFAULT_ATTENTION_POLL_MS = 1000;
const MIN_ATTENTION_POLL_MS = 250;
const MAX_ATTENTION_POLL_MS = 30000;
const SPAWN_ACCEPTANCE_MS = 250;
const SPAWN_HANDOFF_TIMEOUT_MS = 5000;
const RUNNER_RECEIPT_TIMEOUT_MS = 45000;
const STALE_REAP_TIMEOUT_MS = 5000;
const ROOT_COORDINATORS = new Map();
const NOOP_START_CLAIMS = Object.freeze({
  acquire() { return null; },
  accepted() { return true; },
  release() { return false; },
});

function processAlive(pid) {
  if (!Number.isInteger(pid) || pid < 1) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

function processGroupAlive(pid) {
  if (!Number.isInteger(pid) || pid < 1) return false;
  try {
    process.kill(-pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

function waitForProcessGroupExit(pid) {
  const deadline = Date.now() + STALE_REAP_TIMEOUT_MS;
  const sleeper = new Int32Array(new SharedArrayBuffer(4));
  while (processGroupAlive(pid) && Date.now() < deadline) {
    Atomics.wait(sleeper, 0, 0, 10);
  }
  return !processGroupAlive(pid);
}

function startClaimOwner(pathname) {
  try {
    const directory = lstatSync(pathname);
    const generation = `${directory.dev.toString(16)}-${directory.ino.toString(16)}`;
    const ownerPath = path.join(pathname, "owner.json");
    const ownerInfo = lstatSync(ownerPath);
    if (
      !directory.isDirectory()
      || directory.isSymbolicLink()
      || !ownerInfo.isFile()
      || ownerInfo.isSymbolicLink()
      || ownerInfo.nlink !== 1
      || ownerInfo.size > 4096
    ) {
      return null;
    }
    const entries = readdirSync(pathname);
    const handoffs = entries.filter((entry) => entry !== "owner.json");
    if (handoffs.some(
      (entry) => !/^\.owner-handoff-[1-9][0-9]*-claim-[0-9a-f]{32}\.json$/.test(entry),
    )) {
      return null;
    }
    for (const handoff of handoffs) {
      const pid = Number(
        handoff.match(/^\.owner-handoff-([1-9][0-9]*)-/)?.[1],
      );
      if (processAlive(pid) || processGroupAlive(pid)) {
        return { pid, generation };
      }
    }
    let value;
    try {
      value = JSON.parse(readFileSync(ownerPath, "utf8"));
    } catch {
      return { recoverable: true, generation };
    }
    if (
      value?.schemaVersion !== 1
      || Object.keys(value).sort().join(",")
        !== "captainSessionId,pid,root,schemaVersion,token,workerId"
      || typeof value.token !== "string"
      || !/^claim-[0-9a-f]{32}$/.test(value.token)
      || !Number.isInteger(value.pid)
      || value.pid < 1
    ) {
      return { recoverable: true, generation };
    }
    if (handoffs.length > 0) return { recoverable: true, generation };
    return { ...value, generation };
  } catch {
    return null;
  }
}

function claimSessionDirectory(root) {
  const kimiflow = path.join(root, ".kimiflow");
  const session = path.join(kimiflow, "session");
  for (const candidate of [kimiflow, session]) {
    try {
      mkdirSync(candidate, { mode: 0o700 });
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
    }
    const info = lstatSync(candidate);
    if (!info.isDirectory() || info.isSymbolicLink()) {
      throw new Error("kimiflow_start_claim_unsafe");
    }
  }
  if (
    realpathSync(root) !== root
    || realpathSync(kimiflow) !== kimiflow
    || realpathSync(session) !== session
  ) {
    throw new Error("kimiflow_start_claim_unsafe");
  }
  return session;
}

function cleanupLeaseActive(root, session, claimPath) {
  const registry = path.join(session, CLEANUP_LEASES_NAME);
  let registryInfo;
  try {
    registryInfo = lstatSync(registry);
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw new Error("kimiflow_start_claim_unsafe");
  }
  if (
    !registryInfo.isDirectory()
    || registryInfo.isSymbolicLink()
    || realpathSync(registry) !== registry
  ) {
    throw new Error("kimiflow_start_claim_unsafe");
  }
  const entries = readdirSync(registry);
  const claim = startClaimOwner(claimPath);
  const runnerOwnsCleanup = claim !== null
    && claim.recoverable !== true
    && (
      processAlive(claim.pid)
      || processGroupAlive(claim.pid)
    );
  for (const entry of entries) {
    const match = entry.match(/^lease-([1-9][0-9]*)-[0-9a-f]{64}$/);
    if (match === null) {
      throw new Error("kimiflow_start_claim_unsafe");
    }
    const pathname = path.join(registry, entry);
    const info = lstatSync(pathname);
    if (
      !info.isDirectory()
      || info.isSymbolicLink()
      || realpathSync(pathname) !== pathname
    ) {
      throw new Error("kimiflow_start_claim_unsafe");
    }
    const sentinelPid = Number(match[1]);
    if (runnerOwnsCleanup || processAlive(sentinelPid)) {
      return true;
    }
    try {
      execFileSync(
        path.join(packageRoot(), "hooks", "pi-host.sh"),
        ["cleanup-lease", "--root", root, "--lease", entry],
        {
          cwd: root,
          stdio: "ignore",
          timeout: STALE_REAP_TIMEOUT_MS,
        },
      );
    } catch {
      return true;
    }
  }
  return readdirSync(registry).length > 0;
}

export function createFileStartClaims() {
  function acquire(binding) {
    const root = exactRoot(binding.root);
    const session = claimSessionDirectory(root);
    const claimPath = path.join(session, START_CLAIM_NAME);
    if (cleanupLeaseActive(root, session, claimPath)) {
      throw new Error("kimiflow_activation_in_progress");
    }
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const token = `claim-${randomUUID().replaceAll("-", "")}`;
      const temporary = path.join(session, `.${START_CLAIM_NAME}-${token}`);
      mkdirSync(temporary, { mode: 0o700 });
      const owner = {
        schemaVersion: 1,
        token,
        pid: process.pid,
        root,
        captainSessionId: binding.captainSessionId,
        workerId: binding.workerId,
      };
      writeFileSync(
        path.join(temporary, "owner.json"),
        `${JSON.stringify(owner)}\n`,
        { encoding: "utf8", mode: 0o600, flag: "wx" },
      );
      try {
        renameSync(temporary, claimPath);
        return Object.freeze({ token, root });
      } catch (error) {
        rmSync(temporary, { recursive: true, force: true });
        if (!["EEXIST", "ENOTEMPTY"].includes(error?.code)) throw error;
      }
      const current = startClaimOwner(claimPath);
      if (current === null || (
        current.recoverable !== true
        && (
          processAlive(current.pid)
          || !waitForProcessGroupExit(current.pid)
        )
      )) {
        throw new Error("kimiflow_activation_in_progress");
      }
      const stale = path.join(
        session,
        `.${START_CLAIM_NAME}-stale-${current.generation}`,
      );
      try {
        renameSync(claimPath, stale);
      } catch (error) {
        if (!["ENOENT", "EEXIST", "ENOTEMPTY"].includes(error?.code)) throw error;
      }
    }
    throw new Error("kimiflow_activation_in_progress");
  }

  function accepted(claim, runnerPid) {
    if (
      claim === null
      || typeof claim !== "object"
      || !/^claim-[0-9a-f]{32}$/.test(claim.token)
      || !Number.isInteger(runnerPid)
      || runnerPid < 1
    ) return false;
    const root = exactRoot(claim.root);
    const current = startClaimOwner(path.join(
      claimSessionDirectory(root),
      START_CLAIM_NAME,
    ));
    return (
      current?.token === claim.token
      && current.root === root
      && current.pid === runnerPid
      && processAlive(runnerPid)
    );
  }

  function release(claim) {
    if (
      claim === null
      || typeof claim !== "object"
      || !/^claim-[0-9a-f]{32}$/.test(claim.token)
    ) return false;
    const root = exactRoot(claim.root);
    const session = claimSessionDirectory(root);
    const claimPath = path.join(session, START_CLAIM_NAME);
    const current = startClaimOwner(claimPath);
    if (
      current?.token !== claim.token
      || current.root !== root
      || current.pid !== process.pid
    ) return false;
    const retired = path.join(
      session,
      `.${START_CLAIM_NAME}-released-${randomUUID().replaceAll("-", "")}`,
    );
    renameSync(claimPath, retired);
    rmSync(retired, { recursive: true, force: true });
    return true;
  }

  return Object.freeze({ acquire, accepted, release });
}

function rootCoordinator(root) {
  let coordinator = ROOT_COORDINATORS.get(root);
  if (coordinator === undefined) {
    coordinator = { activation: false, deliveries: new Set() };
    ROOT_COORDINATORS.set(root, coordinator);
  }
  return coordinator;
}

function releaseCoordinator(root, coordinator) {
  if (!coordinator.activation && coordinator.deliveries.size === 0) {
    ROOT_COORDINATORS.delete(root);
  }
}

function typeBoxSchema(kind, schema) {
  Object.defineProperty(schema, "~kind", {
    value: kind,
    writable: true,
    configurable: true,
  });
  return schema;
}

const activationParameters = typeBoxSchema("Object", {
  type: "object",
  required: ["request"],
  additionalProperties: false,
  properties: {
    request: typeBoxSchema("String", {
      type: "string",
      minLength: 1,
      maxLength: 65536,
      description: "The user's complete feature request to run with Kimiflow.",
    }),
    project: typeBoxSchema("String", {
      type: "string",
      minLength: 1,
      maxLength: 4096,
      description: "Optional registered project id, name, or absolute Git root.",
    }),
  },
});

const projectParameters = typeBoxSchema("Object", {
  type: "object",
  required: ["action"],
  additionalProperties: false,
  properties: {
    action: typeBoxSchema("String", {
      type: "string",
      enum: ["list", "register", "clone", "resolve", "remove"],
    }),
    selector: typeBoxSchema("String", { type: "string", minLength: 1, maxLength: 4096 }),
    name: typeBoxSchema("String", { type: "string", minLength: 1, maxLength: 64 }),
  },
});

const deliveryParameters = typeBoxSchema("Object", {
  type: "object",
  required: ["workerId", "providerSessionId", "run", "message"],
  additionalProperties: false,
  properties: {
    workerId: typeBoxSchema("String", {
      type: "string",
      minLength: 8,
      maxLength: 128,
      pattern: IDENTITY.source,
    }),
    providerSessionId: typeBoxSchema("String", {
      type: "string",
      minLength: 8,
      maxLength: 128,
      pattern: IDENTITY.source,
    }),
    run: typeBoxSchema("String", {
      type: "string",
      minLength: 11,
      maxLength: 265,
      pattern: RUN_IDENTITY.source,
    }),
    message: typeBoxSchema("String", {
      type: "string",
      minLength: 1,
      maxLength: 65536,
    }),
  },
});

const statusParameters = typeBoxSchema("Object", {
  type: "object",
  required: [],
  additionalProperties: false,
  properties: {},
});

function packageRoot() {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
}

function exactId(value, label) {
  if (typeof value !== "string" || !IDENTITY.test(value)) {
    throw new Error(`${label}_invalid`);
  }
  return value;
}

function exactRoot(value) {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > 4096
    || value.includes("\0")
    || !path.isAbsolute(value)
    || path.resolve(value) !== value
  ) {
    throw new Error("root_invalid");
  }
  return value;
}

function contextRoot(context) {
  return exactRoot(realpathSync(path.resolve(context?.cwd ?? process.cwd())));
}

function exactRun(value) {
  if (typeof value !== "string" || !RUN_IDENTITY.test(value)) {
    throw new Error("run_invalid");
  }
  return value;
}

function exactSelection(value) {
  if (typeof value !== "string" || !MODEL_SELECTION.test(value)) {
    throw new Error("pi_model_selection_invalid");
  }
  return value;
}

function selection(context) {
  const provider = context?.model?.provider ?? context?.provider;
  const model = context?.model?.id ?? context?.model?.name;
  const thinking = context?.thinkingLevel ?? context?.thinking;
  if (
    typeof provider !== "string"
    || typeof model !== "string"
    || typeof thinking !== "string"
  ) {
    throw new Error("pi_model_selection_unavailable");
  }
  return exactSelection(`${provider}/${model}:${thinking}`);
}

function sessionId(context) {
  return exactId(
    context?.sessionManager?.getSessionId?.()
      ?? context?.sessionId
      ?? context?.session?.id,
    "captain_session",
  );
}

function digest(value) {
  return `sha256:${createHash("sha256").update(value, "utf8").digest("hex")}`;
}

function durableBinding(value) {
  if (
    value === null
    || typeof value !== "object"
    || value.schemaVersion !== 1
    || Object.keys(value).sort().join(",")
      !== "captainSessionId,deliveryBoundary,deliveryPending,modelSelection,providerSessionId,root,run,schemaVersion,terminal,workerId"
  ) {
    return null;
  }
  try {
    if (value.deliveryBoundary !== null && !DIGEST.test(value.deliveryBoundary)) {
      return null;
    }
    if (
      typeof value.deliveryPending !== "boolean"
      || typeof value.terminal !== "boolean"
    ) {
      return null;
    }
    const terminalWithoutRun = value.terminal === true
      && value.run === null
      && value.providerSessionId === null
      && value.deliveryBoundary === null
      && value.deliveryPending === false;
    if (
      !terminalWithoutRun
      && (
        typeof value.run !== "string"
        || typeof value.providerSessionId !== "string"
      )
    ) {
      return null;
    }
    return {
      schemaVersion: 1,
      root: exactRoot(value.root),
      captainSessionId: exactId(value.captainSessionId, "captain_session"),
      workerId: exactId(value.workerId, "worker"),
      modelSelection: exactSelection(value.modelSelection),
      run: terminalWithoutRun ? null : exactRun(value.run),
      providerSessionId: terminalWithoutRun
        ? null
        : exactId(value.providerSessionId, "provider_session"),
      deliveryBoundary: value.deliveryBoundary,
      deliveryPending: value.deliveryPending,
      terminal: value.terminal,
    };
  } catch {
    return null;
  }
}

function durableClaim(value) {
  if (
    value === null
    || typeof value !== "object"
    || value.schemaVersion !== 1
    || Object.keys(value).sort().join(",")
      !== "captainSessionId,modelSelection,requestDigest,root,schemaVersion,workerId"
  ) {
    return null;
  }
  if (!DIGEST.test(value.requestDigest)) {
    return null;
  }
  try {
    return {
      schemaVersion: 1,
      root: exactRoot(value.root),
      captainSessionId: exactId(value.captainSessionId, "captain_session"),
      workerId: exactId(value.workerId, "worker"),
      modelSelection: exactSelection(value.modelSelection),
      requestDigest: value.requestDigest,
    };
  } catch {
    return null;
  }
}

function currentBridgeEntries(context) {
  const entries = context?.sessionManager?.getBranch?.();
  if (!Array.isArray(entries)) return [];
  const primarySession = sessionId(context);
  const workers = new Set();
  const result = [];
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index];
    if (entry?.type !== "custom") continue;
    const validator = entry.customType === BINDING_ENTRY
      ? durableBinding
      : entry.customType === CLAIM_ENTRY
        ? durableClaim
        : null;
    if (validator === null) continue;
    const value = validator(entry.data);
    if (
      value?.captainSessionId === primarySession
      && !workers.has(value.workerId)
    ) {
      workers.add(value.workerId);
      result.push({ kind: entry.customType, value });
    }
  }
  return result;
}

function currentBridgeEntry(context) {
  return currentBridgeEntries(context)[0] ?? null;
}

function currentClaim(context) {
  const current = currentBridgeEntry(context);
  return current?.kind === CLAIM_ENTRY ? current.value : null;
}

function sameClaim(left, right) {
  return left !== null
    && right !== null
    && left.root === right.root
    && left.captainSessionId === right.captainSessionId
    && left.workerId === right.workerId
    && left.requestDigest === right.requestDigest
    && left.modelSelection === right.modelSelection;
}

function textResult(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value) }],
    details: value,
  };
}

function runnerIsActive(snapshot) {
  return snapshot?.lifecycle?.schema_version === 1
    && snapshot.lifecycle.active === true;
}

function runnerIsReachable(snapshot) {
  return snapshot?.lifecycle?.schema_version === 1
    && snapshot.lifecycle.reachable === true;
}

function runnerIsTerminal(snapshot) {
  return snapshot?.lifecycle?.schema_version === 1
    && snapshot.lifecycle.terminal === true;
}

function snapshotIdentity(snapshot, binding = null) {
  const identity = snapshot?.lifecycle?.identity;
  const run = identity?.run;
  const providerSessionId = identity?.provider_session_id;
  if (
    typeof run !== "string"
    || !RUN_IDENTITY.test(run)
    || typeof providerSessionId !== "string"
    || !IDENTITY.test(providerSessionId)
  ) {
    return null;
  }
  if (
    binding !== null
    && (
      identity?.captain_session_id !== binding.captainSessionId
      || identity?.worker_id !== binding.workerId
    )
  ) {
    return null;
  }
  return { run, providerSessionId };
}

function provisionalTransportFailure(snapshot, binding) {
  const identity = snapshot?.lifecycle?.provisional_identity;
  const providerSessionId = identity?.provider_session_id;
  if (
    binding?.run !== null
    || binding?.providerSessionId !== null
    || typeof providerSessionId !== "string"
    || !IDENTITY.test(providerSessionId)
    || identity?.captain_session_id !== binding.captainSessionId
    || identity?.worker_id !== binding.workerId
  ) {
    return null;
  }
  return { run: null, providerSessionId };
}

function deliveryBoundary(snapshot) {
  const boundary = snapshot?.lifecycle?.delivery_boundary;
  return typeof boundary === "string" && DIGEST.test(boundary)
    ? boundary
    : null;
}

function transition(snapshot, binding) {
  const value = snapshot?.lifecycle?.attention;
  if (
    binding === null
    || value === null
    || typeof value !== "object"
    || value.root !== binding.root
    || value.captain_session_id !== binding.captainSessionId
    || value.worker_id !== binding.workerId
    || (binding.run !== null && value.run !== binding.run)
    || (
      binding.providerSessionId !== null
      && value.provider_session_id !== binding.providerSessionId
    )
  ) {
    return null;
  }
  return value;
}

function attentionContent(value) {
  if (value.kind === "question") {
    return value.question
      .replace(/^<!-- kimiflow:intake [^\n]+ -->\r?\n?/, "")
      .trim();
  }
  const run = typeof value.run === "string" ? value.run : "Kimiflow";
  if (value.kind === "completion") return `✓ Kimiflow · ${run}`;
  const diagnostic = typeof value.diagnostic_code === "string"
    ? ` · ${value.diagnostic_code}`
    : "";
  return `⚠ Kimiflow failed · ${run}${diagnostic}`;
}

function restoredAttentionIds(context) {
  const result = new Set();
  const branch = context?.sessionManager?.getBranch?.();
  if (!Array.isArray(branch)) return result;
  for (const entry of branch) {
    if (
      entry?.type !== "custom_message"
      || ![ATTENTION_MESSAGE, LEGACY_ATTENTION_MESSAGE].includes(entry?.customType)
      || entry?.details?.kind === "reply"
    ) {
      continue;
    }
    let attentionId = entry?.details?.attention_id;
    if (typeof attentionId !== "string" && typeof entry?.content === "string") {
      try {
        attentionId = JSON.parse(entry.content)?.attention_id;
      } catch {
        attentionId = null;
      }
    }
    if (typeof attentionId === "string" && IDENTITY.test(attentionId)) {
      result.add(attentionId);
    }
  }
  return result;
}

function boundedStatus(value) {
  const state = typeof value?.status === "string" ? value.status : "unknown";
  const run = value?.lifecycle?.run;
  const result = `Kimiflow: ${state}${typeof run === "string" ? `; run=${run}` : ""}.`;
  return result.length <= STATUS_LIMIT
    ? result
    : `${result.slice(0, STATUS_LIMIT - 1)}…`;
}

export function createCaptainExtension({
  exec = async (file, args, options) => {
    try {
      const result = await execFileAsync(file, args, {
        ...options,
        encoding: "utf8",
        maxBuffer: 256 * 1024,
      });
      return JSON.parse(result.stdout);
    } catch (error) {
      if (typeof error?.stdout === "string" && error.stdout.trim()) {
        return JSON.parse(error.stdout);
      }
      throw error;
    }
  },
  spawn = nodeSpawn,
  root = packageRoot(),
  startClaims = NOOP_START_CLAIMS,
  prepareWorker = async ({ root: projectRoot, request }) => ({
    root: projectRoot,
    request,
    run: null,
  }),
  adoptWorker = null,
  runnerReceiptTimeoutMs = RUNNER_RECEIPT_TIMEOUT_MS,
  runnerReceiptPollMs = 10,
} = {}) {
  const runner = path.join(root, "hooks", "kimiflow-runner.sh");
  const piHost = path.join(root, "hooks", "pi-host.sh");
  const seenAttention = new Set();
  const fleet = new Map();
  let active = null;

  async function runnerStatus(cwd) {
    return exec(runner, ["status", "--root", cwd], { cwd });
  }

  async function waitForRunnerReceipt(cwd, binding, controllerPid, requireIdentity) {
    const deadline = Date.now() + runnerReceiptTimeoutMs;
    while (true) {
      const snapshot = await runnerStatus(cwd);
      const lifecycle = snapshot?.lifecycle;
      const bridge = lifecycle?.bridge;
      const exactController = lifecycle?.controller_pid === controllerPid;
      const exactBridge = bridge?.schema_version === 1
        && bridge.captain_session_id === binding.captainSessionId
        && bridge.worker_id === binding.workerId;
      if (exactController && exactBridge) {
        if (lifecycle.state === "starting") {
          // The runner owns the start now, but the provider worker has not
          // acknowledged a reachable session yet.
        } else if (
          lifecycle.reachable === true
          || lifecycle.state === "waiting"
        ) {
          if (!requireIdentity || snapshotIdentity(snapshot, binding) !== null) {
            return snapshot;
          }
        } else {
          throw new Error("kimiflow_runner_start_failed");
        }
      }
      if (Date.now() >= deadline) {
        throw new Error("kimiflow_runner_receipt_timeout");
      }
      await new Promise((resolve) => setTimeout(resolve, runnerReceiptPollMs));
    }
  }

  async function preflightPi(cwd) {
    let info;
    try {
      info = await exec(piHost, ["capabilities", "--json"], { cwd });
    } catch {
      throw new Error("kimiflow_pi_unavailable_or_incompatible");
    }
    if (
      info?.schema_version !== 1
      || info?.host !== "pi"
      || info?.features?.workflow_context !== true
      || info?.features?.structured_events !== true
    ) {
      throw new Error("kimiflow_pi_unavailable_or_incompatible");
    }
  }

  function bridgeEnvironment(binding, startClaim = null) {
    const environment = {
      [BRIDGE_ENV]: JSON.stringify({
        schema_version: 1,
        root: binding.root,
        captain_session_id: binding.captainSessionId,
        worker_id: binding.workerId,
      }),
    };
    if (startClaim !== null) environment[START_CLAIM_ENV] = startClaim.token;
    return environment;
  }

  function spawnRunner(args, cwd, binding, startClaim = null) {
    return new Promise((resolve, reject) => {
      const child = spawn(runner, args, {
        cwd,
        env: { ...process.env, ...bridgeEnvironment(binding, startClaim) },
        detached: true,
        stdio: "ignore",
      });
      let settled = false;
      let spawned = false;
      let handoffTimer = null;
      const cleanup = () => {
        child.off?.("error", onError);
        child.off?.("exit", onExit);
        if (handoffTimer !== null) clearTimeout(handoffTimer);
        handoffTimer = null;
      };
      const onError = (error) => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(error);
      };
      const onExit = () => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(new Error(
          spawned ? "kimiflow_spawn_exited_immediately" : "kimiflow_spawn_failed",
        ));
      };
      child.once("error", onError);
      child.once("exit", onExit);
      child.once("spawn", () => {
        if (settled) return;
        spawned = true;
        if (!Number.isInteger(child.pid) || child.pid < 1) {
          settled = true;
          cleanup();
          reject(new Error("kimiflow_spawn_failed"));
          return;
        }
        const deadline = Date.now() + (
          startClaim === null ? SPAWN_ACCEPTANCE_MS : SPAWN_HANDOFF_TIMEOUT_MS
        );
        const accept = () => {
          if (settled) return;
          let handedOff = startClaim === null;
          try {
            handedOff = handedOff || startClaims.accepted(startClaim, child.pid);
          } catch (error) {
            settled = true;
            cleanup();
            child.kill?.("SIGKILL");
            reject(error);
            return;
          }
          if (!handedOff && Date.now() < deadline) {
            handoffTimer = setTimeout(accept, 10);
            return;
          }
          if (!handedOff) {
            settled = true;
            cleanup();
            child.kill?.("SIGKILL");
            reject(new Error("kimiflow_spawn_handoff_timeout"));
            return;
          }
          settled = true;
          cleanup();
          child.unref?.();
          resolve(child.pid);
        };
        handoffTimer = setTimeout(
          accept,
          startClaim === null ? SPAWN_ACCEPTANCE_MS : 0,
        );
      });
    });
  }

  function activationClaim(request, context, projectRoot = null) {
    if (typeof request !== "string" || request.trim().length === 0) {
      throw new Error("kimiflow_request_required");
    }
    const pending = currentClaim(context);
    const cwd = projectRoot === null ? contextRoot(context) : exactRoot(projectRoot);
    const requestDigest = digest(request.trim());
    const modelSelection = selection(context);
    if (
      pending?.root === cwd
      && pending.requestDigest === requestDigest
    ) {
      return pending;
    }
    return {
      schemaVersion: 1,
      root: cwd,
      captainSessionId: sessionId(context),
      workerId: `worker-${randomUUID().replaceAll("-", "").slice(0, 16)}`,
      modelSelection,
      requestDigest,
    };
  }

  function provisionalBinding(claim) {
    return {
      schemaVersion: 1,
      root: claim.root,
      captainSessionId: claim.captainSessionId,
      workerId: claim.workerId,
      modelSelection: claim.modelSelection,
      run: null,
      providerSessionId: null,
      deliveryBoundary: null,
      deliveryPending: false,
      terminal: false,
    };
  }

  function bindSnapshot(binding, snapshot) {
    const identity = snapshotIdentity(snapshot, binding);
    if (identity === null) return null;
    if (
      binding.run !== null
      && (
        binding.run !== identity.run
        || binding.providerSessionId !== identity.providerSessionId
      )
    ) {
      return null;
    }
    return { ...binding, ...identity };
  }

  async function activateOnce(request, context, claim, beforeSpawn) {
    const pending = currentClaim(context);
    const selectedClaim = claim ?? activationClaim(request, context);
    const validated = durableClaim(selectedClaim);
    if (
      validated === null
      || validated.captainSessionId !== sessionId(context)
      || validated.requestDigest !== digest(request.trim())
    ) {
      throw new Error("kimiflow_activation_claim_invalid");
    }
    let recovering = sameClaim(pending, validated);
    if (pending !== null && !recovering) {
      throw new Error("kimiflow_pending_activation_mismatch");
    }
    let snapshot = await runnerStatus(validated.root);
    if (
      active !== null
      && active.terminal !== true
      && active.root === validated.root
      && !runnerIsTerminal(snapshot)
      && snapshot?.lifecycle?.can_resume !== true
      && !(recovering && active.run === null && !runnerIsActive(snapshot))
    ) {
      throw new Error("kimiflow_run_active");
    }

    let preparedRequest = request.trim();
    let resumeAfterAdoption = false;
    let verifiedSpawn = false;
    async function adoptSnapshot() {
      if (typeof adoptWorker !== "function") {
        throw new Error("kimiflow_run_active");
      }
      const adopted = await adoptWorker({
        root: validated.root,
        captainSessionId: validated.captainSessionId,
        request: request.trim(),
        snapshot,
      });
      validated.workerId = exactId(adopted?.workerId, "worker");
      snapshot = await runnerStatus(validated.root);
      recovering = true;
      resumeAfterAdoption = true;
    }

    if (
      recovering
      && snapshot?.lifecycle?.state === "starting"
      && Number.isInteger(snapshot?.lifecycle?.controller_pid)
    ) {
      snapshot = await waitForRunnerReceipt(
        validated.root,
        provisionalBinding(validated),
        snapshot.lifecycle.controller_pid,
        false,
      );
    }

    if (runnerIsActive(snapshot)) {
      if (!recovering) await adoptSnapshot();
      else if (!runnerIsReachable(snapshot)) resumeAfterAdoption = true;
    } else {
      await preflightPi(validated.root);
      const prepared = await prepareWorker({
        root: validated.root,
        request: request.trim(),
        workerId: validated.workerId,
      });
      if (
        prepared === null
        || typeof prepared !== "object"
        || typeof prepared.request !== "string"
        || prepared.request.trim().length === 0
      ) {
        throw new Error("kimiflow_fleet_allocation_invalid");
      }
      if (prepared.status === "queued") {
        throw new Error("kimiflow_fleet_queued");
      }
      validated.root = exactRoot(prepared.root);
      preparedRequest = prepared.request.trim();
      snapshot = await runnerStatus(validated.root);
      if (runnerIsActive(snapshot)) await adoptSnapshot();
    }

    if (!runnerIsActive(snapshot) || resumeAfterAdoption) {
      await preflightPi(validated.root);
      const startClaim = startClaims.acquire(validated);
      try {
        await beforeSpawn?.(validated);
        const command = resumeAfterAdoption ? "resume" : "run";
        const runnerArgs = [command];
        if (!resumeAfterAdoption) runnerArgs.push(preparedRequest);
        if (
          resumeAfterAdoption
          && snapshot?.lifecycle?.awaiting_user === true
        ) {
          runnerArgs.push("--message", request.trim());
        }
        runnerArgs.push(
          "--root", validated.root,
          "--adapter", "command",
          "--adapter-command", piHost,
          "--model", validated.modelSelection,
          "--require-feature", "structured_events",
        );
        const controllerPid = await spawnRunner(
          runnerArgs, validated.root, validated, startClaim,
        );
        if (startClaim !== null || resumeAfterAdoption) {
          snapshot = await waitForRunnerReceipt(
            validated.root,
            provisionalBinding(validated),
            controllerPid,
            resumeAfterAdoption,
          );
          verifiedSpawn = true;
        }
      } catch (error) {
        startClaims.release(startClaim);
        throw error;
      }
    }
    const provisional = provisionalBinding(validated);
    const bound = runnerIsActive(snapshot)
      ? bindSnapshot(provisional, snapshot)
      : null;
    active = bound ?? (
      !runnerIsActive(snapshot) || (verifiedSpawn && !resumeAfterAdoption)
        ? provisional
        : null
    );
    if (active === null) throw new Error("kimiflow_runner_identity_invalid");
    fleet.set(active.workerId, { ...active });
    return {
      status: recovering && (verifiedSpawn || runnerIsReachable(snapshot))
        ? "recovered"
        : "activated",
      ...active,
    };
  }

  async function activate(request, context, claim, beforeSpawn) {
    const coordinatorRoot = claim?.root ?? contextRoot(context);
    const coordinator = rootCoordinator(coordinatorRoot);
    if (coordinator.activation) {
      throw new Error("kimiflow_activation_in_progress");
    }
    coordinator.activation = true;
    try {
      return await activateOnce(request, context, claim, beforeSpawn);
    } finally {
      coordinator.activation = false;
      releaseCoordinator(coordinatorRoot, coordinator);
    }
  }

  async function statusActive() {
    if (active === null) return { status: "inactive" };
    const snapshot = await runnerStatus(active.root);
    if (active.run === null) {
      const bound = bindSnapshot(active, snapshot);
      if (bound !== null) {
        active = bound;
        return snapshot;
      }
      if (provisionalTransportFailure(snapshot, active) !== null) {
        return snapshot;
      }
      if (runnerIsActive(snapshot)) {
        throw new Error("kimiflow_runner_identity_mismatch");
      }
      return {
        schema_version: 1,
        status: "starting",
        runner: null,
        active_run: { present: false },
      };
    }
    const identity = snapshotIdentity(snapshot, active);
    if (
      identity?.run !== active.run
      || identity?.providerSessionId !== active.providerSessionId
    ) {
      throw new Error("kimiflow_runner_identity_mismatch");
    }
    return snapshot;
  }

  async function deliverActive(kind, params, context, persist) {
    if (active === null) throw new Error("kimiflow_bridge_inactive");
    if (active.terminal === true) throw new Error("kimiflow_bridge_terminal");
    if (sessionId(context) !== active.captainSessionId) {
      throw new Error("captain_session_mismatch");
    }
    if (exactId(params?.workerId, "worker") !== active.workerId) {
      throw new Error("worker_mismatch");
    }
    const providerSessionId = exactId(params?.providerSessionId, "provider_session");
    const run = exactRun(params?.run);
    if (typeof params?.message !== "string" || params.message.trim().length === 0) {
      throw new Error("message_invalid");
    }
    const snapshot = await runnerStatus(active.root);
    if (active.run === null) {
      const bound = bindSnapshot(active, snapshot);
      if (bound === null) throw new Error("kimiflow_runner_identity_unavailable");
      await persist?.({ ...bound });
      active = bound;
    }
    if (active.run !== run) throw new Error("run_mismatch");
    if (active.providerSessionId !== providerSessionId) {
      throw new Error("provider_session_mismatch");
    }
    const identity = snapshotIdentity(snapshot, active);
    if (
      identity?.run !== active.run
      || identity?.providerSessionId !== active.providerSessionId
    ) {
      throw new Error("kimiflow_runner_identity_mismatch");
    }
    if (snapshot?.lifecycle?.can_resume !== true) {
      throw new Error(`${kind}_requires_resumable_boundary`);
    }
    const boundary = deliveryBoundary(snapshot);
    if (boundary === null) {
      throw new Error("kimiflow_delivery_boundary_unavailable");
    }
    let startClaim = null;
    if (active.deliveryBoundary === boundary && !active.deliveryPending) {
      throw new Error("kimiflow_delivery_in_progress");
    }
    if (active.deliveryBoundary === boundary) {
      try {
        startClaim = startClaims.acquire(active);
      } catch (error) {
        if (error?.message === "kimiflow_activation_in_progress") {
          throw new Error("kimiflow_delivery_in_progress");
        }
        throw error;
      }
    }
    const coordinator = rootCoordinator(active.root);
    if (coordinator.deliveries.has(boundary)) {
      if (startClaim !== null) startClaims.release(startClaim);
      throw new Error("kimiflow_delivery_in_progress");
    }
    coordinator.deliveries.add(boundary);
    const previous = { ...active };
    const pending = {
      ...active,
      deliveryBoundary: boundary,
      deliveryPending: true,
    };
    try {
      if (startClaim === null) {
        try {
          startClaim = startClaims.acquire(active);
        } catch (error) {
          if (error?.message === "kimiflow_activation_in_progress") {
            throw new Error("kimiflow_delivery_in_progress");
          }
          throw error;
        }
      }
      await persist?.({ ...pending });
      active = pending;
      try {
        await spawnRunner([
          "resume",
          "--message", params.message.trim(),
          "--root", active.root,
          "--adapter", "command",
          "--adapter-command", piHost,
          "--model", active.modelSelection,
          "--require-feature", "structured_events",
        ], active.root, active, startClaim);
        startClaim = null;
      } catch (error) {
        active = previous;
        await persist?.({ ...previous });
        throw error;
      }
      const completed = { ...active, deliveryPending: false };
      await persist?.({ ...completed });
      active = completed;
      return {
        schema_version: 1,
        status: "queued",
        kind,
        run,
        worker_id: active.workerId,
        provider_session_id: providerSessionId,
      };
    } finally {
      if (startClaim !== null) startClaims.release(startClaim);
      coordinator.deliveries.delete(boundary);
      releaseCoordinator(active.root, coordinator);
    }
  }

  async function pollActiveAttention(pi, canAnnounce = true) {
    if (active === null) {
      return { status: "inactive", announced: 0, announcements: [] };
    }
    if (active.terminal === true) {
      return { status: "terminal", announced: 0, announcements: [] };
    }
    const snapshot = await runnerStatus(active.root);
    if (active.run === null) {
      const bound = bindSnapshot(active, snapshot);
      if (bound === null) {
        if (provisionalTransportFailure(snapshot, active) === null) {
          return {
            status: "attention", announced: 0, announcements: [], snapshot,
          };
        }
      } else {
        pi.appendEntry?.(BINDING_ENTRY, bound);
        active = bound;
      }
    } else {
      const identity = snapshotIdentity(snapshot, active);
      if (
        identity?.run !== active.run
        || identity?.providerSessionId !== active.providerSessionId
      ) {
        return {
          status: "attention", announced: 0, announcements: [], snapshot,
        };
      }
    }
    if (active.deliveryPending) {
      const boundary = deliveryBoundary(snapshot);
      if (boundary !== active.deliveryBoundary) {
        const reconciled = { ...active, deliveryPending: false };
        pi.appendEntry?.(BINDING_ENTRY, reconciled);
        active = reconciled;
      }
    }
    const observed = snapshot;
    const value = transition(snapshot, active);
    let announced = 0;
    const announcements = [];
    if (
      canAnnounce
      && value !== null
      && !seenAttention.has(value.attention_id)
    ) {
      if (typeof pi.sendMessage === "function") {
        pi.sendMessage({
          customType: ATTENTION_MESSAGE,
          content: attentionContent(value),
          details: value,
          display: true,
        });
        seenAttention.add(value.attention_id);
        announced = 1;
        announcements.push(value);
      }
    }
    if (
      (
        snapshot?.lifecycle?.cleanup_endpoint === true
      )
      && (value === null || seenAttention.has(value.attention_id))
    ) {
      if (active.providerSessionId !== null) {
        await exec(
          piHost,
          [
            "terminate",
            "--root", active.root,
            "--session-id", active.providerSessionId,
            "--json",
          ],
          {
            cwd: active.root,
            env: {
              ...process.env,
              ...bridgeEnvironment(active),
            },
          },
        );
      }
      const terminal = { ...active, deliveryPending: false, terminal: true };
      pi.appendEntry?.(BINDING_ENTRY, terminal);
      active = terminal;
    }
    return {
      status: "attention",
      announced,
      announcements,
      snapshot: observed,
    };
  }

  function selectedFleetBinding(preferred = null) {
    const preferredBinding = preferred ? fleet.get(preferred) : null;
    if (preferredBinding?.terminal !== true) return preferredBinding;
    return [...fleet.values()].find((binding) => binding.terminal !== true)
      ?? preferredBinding
      ?? fleet.values().next().value
      ?? null;
  }

  async function status() {
    const bindings = [...fleet.values()];
    if (bindings.length <= 1) return statusActive();
    const selected = active?.workerId;
    const workers = [];
    for (const binding of bindings) {
      active = { ...binding };
      try {
        workers.push({ worker_id: binding.workerId, snapshot: await statusActive() });
      } catch (error) {
        workers.push({ worker_id: binding.workerId, status: "status_error", error: error?.message });
      }
      fleet.set(binding.workerId, { ...active });
    }
    active = selectedFleetBinding(selected);
    return { schema_version: 1, status: "fleet", workers };
  }

  async function deliver(kind, params, context, persist) {
    const workerId = exactId(params?.workerId, "worker");
    const target = fleet.get(workerId);
    if (target === undefined) throw new Error("worker_mismatch");
    const selected = active?.workerId;
    active = { ...target };
    try {
      return await deliverActive(kind, params, context, persist);
    } finally {
      fleet.set(workerId, { ...active });
      active = selectedFleetBinding(selected) ?? fleet.get(workerId);
    }
  }

  async function pollAttention(pi, canAnnounce = true) {
    const bindings = [...fleet.values()];
    if (bindings.length <= 1) {
      const result = await pollActiveAttention(pi, canAnnounce);
      if (active !== null) fleet.set(active.workerId, { ...active });
      return result;
    }
    const selected = active?.workerId;
    const workers = [];
    let announced = 0;
    const announcements = [];
    for (const binding of bindings) {
      active = { ...binding };
      try {
        const result = await pollActiveAttention(pi, canAnnounce);
        announced += result?.announced ?? 0;
        announcements.push(...(result?.announcements ?? []));
        workers.push({ worker_id: binding.workerId, status: result?.status ?? "unknown" });
      } catch (error) {
        workers.push({ worker_id: binding.workerId, status: "attention_error", error: error?.message });
      }
      fleet.set(binding.workerId, { ...active });
    }
    active = selectedFleetBinding(selected);
    return { status: "fleet", announced, announcements, workers };
  }

  return {
    activationClaim,
    pendingClaim: currentClaim,
    activate,
    status,
    deliver,
    pollAttention,
    restoreForSession(context) {
      for (const attentionId of restoredAttentionIds(context)) {
        seenAttention.add(attentionId);
      }
      fleet.clear();
      for (const current of currentBridgeEntries(context).reverse()) {
        const binding = current.kind === BINDING_ENTRY
          ? current.value
          : current.kind === CLAIM_ENTRY
            ? provisionalBinding(current.value)
            : null;
        if (binding !== null) {
          fleet.set(binding.workerId, { ...binding });
          active = { ...binding };
        }
      }
      if (fleet.size === 0) active = null;
      return active ? { ...active } : null;
    },
    clearMemory() {
      active = null;
      fleet.clear();
      seenAttention.clear();
    },
    binding() {
      return active ? { ...active } : null;
    },
    bindings() {
      return [...fleet.values()].map((binding) => ({ ...binding }));
    },
  };
}

export default function registerCaptainExtension(pi, options) {
  const extensionRoot = options?.root ?? packageRoot();
  const piHost = path.join(extensionRoot, "hooks", "pi-host.sh");
  const projectExec = options?.exec ?? (async (file, args, execOptions) => {
    try {
      const result = await execFileAsync(file, args, {
        ...execOptions,
        encoding: "utf8",
        maxBuffer: 256 * 1024,
      });
      return JSON.parse(result.stdout);
    } catch (error) {
      if (typeof error?.stdout === "string" && error.stdout.trim()) {
        return JSON.parse(error.stdout);
      }
      throw error;
    }
  });

  async function projectOperation(action, params, context) {
    const cwd = contextRoot(context);
    let args;
    if (action === "list") {
      args = ["project", "list", "--json"];
    } else if (action === "register") {
      const selectedRoot = exactRoot(realpathSync(path.resolve(params?.selector ?? cwd)));
      args = ["project", "register", "--root", selectedRoot];
      if (typeof params?.name === "string" && params.name.trim()) {
        args.push("--name", params.name.trim());
      }
      args.push("--json");
    } else if (action === "clone") {
      if (
        typeof params?.selector !== "string"
        || !params.selector.trim()
        || typeof params?.name !== "string"
        || !params.name.trim()
      ) {
        throw new Error("project_clone_arguments_invalid");
      }
      args = [
        "project", "clone",
        "--source", params.selector.trim(),
        "--name", params.name.trim(),
        "--json",
      ];
    } else if (action === "remove") {
      if (typeof params?.selector !== "string" || !params.selector.trim()) {
        throw new Error("project_selector_invalid");
      }
      args = ["project", "remove", "--selector", params.selector.trim(), "--json"];
    } else if (action === "resolve") {
      args = ["project", "resolve", "--cwd", cwd];
      if (typeof params?.selector === "string" && params.selector.trim()) {
        args.push("--selector", params.selector.trim());
      }
      args.push("--json");
    }
    if (!["list", "register", "clone", "resolve", "remove"].includes(action)) {
      throw new Error("project_action_invalid");
    }
    return projectExec(piHost, args, { cwd });
  }

  const prepareWorker = options?.prepareWorker ?? (async ({ root, request, workerId }) => {
    const allocated = await projectExec(piHost, [
      "fleet", "allocate",
      "--root", root,
      "--worker-id", workerId,
      "--request-base64", Buffer.from(request, "utf8").toString("base64"),
      "--json",
    ], { cwd: root });
    if (allocated?.status === "queued") {
      return { root, request, run: allocated.run, status: "queued" };
    }
    if (
      allocated?.schema_version !== 1
      || typeof allocated?.root !== "string"
      || typeof allocated?.run !== "string"
      || !RUN_IDENTITY.test(allocated.run)
    ) {
      throw new Error("kimiflow_fleet_allocation_invalid");
    }
    return {
      root: exactRoot(allocated.root),
      run: allocated.run,
      request: [
        `Kimiflow Captain allocation: use the exact run ${allocated.run}.`,
        "The current process already runs in its isolated Fleet worktree; do not route to another root.",
        "",
        request,
      ].join("\n"),
    };
  });
  const adoptWorker = options?.adoptWorker ?? (async ({ root, captainSessionId, request, snapshot }) => {
    const run = snapshot?.lifecycle?.run;
    const numbered = typeof request === "string"
      ? request.match(/\b(?:run|lauf)\s+([0-9]+(?:\.[0-9]+)?)\b/i)?.[1]
      : null;
    const numberedPrefix = numbered ? `.kimiflow/run-${numbered.replaceAll(".", "-")}` : null;
    if (
      typeof run !== "string"
      || typeof request !== "string"
      || !(
        request.includes(run)
        || (
          numberedPrefix !== null
          && (run === numberedPrefix || run.startsWith(`${numberedPrefix}-`))
        )
      )
    ) {
      throw new Error("kimiflow_existing_run_requires_exact_resume_request");
    }
    const expectedCaptain = snapshot?.lifecycle?.bridge?.captain_session_id;
    const expectedWorker = snapshot?.lifecycle?.bridge?.worker_id;
    if (typeof expectedCaptain !== "string" || typeof expectedWorker !== "string") {
      throw new Error("kimiflow_fleet_adoption_identity_missing");
    }
    const adopted = await projectExec(piHost, [
      "fleet", "adopt",
      "--root", root,
      "--captain-session", captainSessionId,
      "--expected-captain", expectedCaptain,
      "--expected-worker", expectedWorker,
      "--json",
    ], { cwd: root });
    if (adopted?.status !== "adopted") {
      throw new Error("kimiflow_fleet_adoption_failed");
    }
    return {
      workerId: adopted.worker_id,
      providerSessionId: adopted.provider_session_id,
      run: adopted.run,
    };
  });
  const extension = createCaptainExtension({
    ...options,
    adoptWorker,
    prepareWorker,
    startClaims: options?.startClaims ?? createFileStartClaims(),
  });
  const attentionPollMs = options?.attentionPollMs ?? DEFAULT_ATTENTION_POLL_MS;
  if (
    !Number.isInteger(attentionPollMs)
    || attentionPollMs < MIN_ATTENTION_POLL_MS
    || attentionPollMs > MAX_ATTENTION_POLL_MS
  ) {
    throw new Error("kimiflow_attention_interval_invalid");
  }
  const scheduleTimeout = options?.setTimeout ?? globalThis.setTimeout;
  const cancelTimeout = options?.clearTimeout ?? globalThis.clearTimeout;
  let watcherTimer = null;
  let watcherEpoch = 0;
  let attentionPoll = null;
  let captainTools = null;
  let captainContext = null;
  let presentationActive = false;
  let presentationEpoch = 0;
  const pendingPresentations = new Map();

  function liveBindings() {
    return extension.bindings().filter((binding) => binding.terminal !== true);
  }

  function lockCaptainTools() {
    if (liveBindings().length === 0) return;
    if (captainTools === null && typeof pi.getActiveTools === "function") {
      captainTools = pi.getActiveTools();
    }
    if (typeof pi.setActiveTools === "function" && Array.isArray(captainTools)) {
      const allowed = new Set([
        "read", "grep", "find", "ls",
        "kimiflow_activate", "kimiflow_project", "kimiflow_status",
        "kimiflow_reply", "kimiflow_steer",
      ]);
      pi.setActiveTools(captainTools.filter((name) => allowed.has(name)));
    }
  }

  function unlockCaptainTools() {
    if (captainTools !== null && typeof pi.setActiveTools === "function") {
      pi.setActiveTools(captainTools);
    }
    captainTools = null;
  }

  function persistBinding(binding) {
    pi.appendEntry?.(BINDING_ENTRY, binding);
  }

  function showCaptainReply(message, attentionId) {
    pi.sendMessage?.({
      customType: ATTENTION_MESSAGE,
      content: `→ ${message}`,
      details: { kind: "reply", attention_id: attentionId },
      display: true,
    });
  }

  async function drainPresentations() {
    if (presentationActive) return;
    const context = captainContext;
    const epoch = presentationEpoch;
    if (
      context === null
      || typeof context.ui?.select !== "function"
      || context.isIdle?.() === false
    ) {
      return;
    }
    presentationActive = true;
    try {
      while (
        context === captainContext
        && epoch === presentationEpoch
        && context.isIdle?.() !== false
        && pendingPresentations.size > 0
      ) {
        const [attentionId, attention] = pendingPresentations.entries().next().value;
        pendingPresentations.delete(attentionId);
        const labels = attention.actions.map((item) => item.label);
        const selected = await context.ui.select(
          "Kimiflow – Entscheidung",
          labels,
        );
        if (context !== captainContext || epoch !== presentationEpoch) return;
        if (typeof selected !== "string" || !labels.includes(selected)) continue;
        try {
          await extension.deliver("reply", {
            workerId: attention.worker_id,
            providerSessionId: attention.provider_session_id,
            run: attention.run,
            message: selected,
          }, context, persistBinding);
          showCaptainReply(selected, attention.attention_id);
        } catch (error) {
          context.ui.notify?.(
            `Kimiflow-Antwort konnte nicht übergeben werden: ${error?.message ?? error}`,
            "warning",
          );
        }
      }
    } catch (error) {
      if (context === captainContext && epoch === presentationEpoch) {
        context.ui.notify?.(
          `Kimiflow-Entscheidung konnte nicht angezeigt werden: ${error?.message ?? error}`,
          "warning",
        );
      }
    } finally {
      presentationActive = false;
    }
  }

  function queuePresentations(result) {
    for (const attention of result?.announcements ?? []) {
      if (
        attention?.kind !== "question"
        || attention.actionable !== true
        || !Array.isArray(attention.actions)
        || attention.actions.length === 0
      ) {
        continue;
      }
      pendingPresentations.set(attention.attention_id, attention);
    }
    void drainPresentations();
  }

  function pollAttention() {
    if (attentionPoll !== null) return attentionPoll;
    const canAnnounce = captainContext?.isIdle?.() !== false;
    attentionPoll = Promise.resolve(
      extension.pollAttention(pi, canAnnounce),
    ).then((result) => {
      if (liveBindings().length === 0) unlockCaptainTools();
      else lockCaptainTools();
      queuePresentations(result);
      return result;
    }).finally(() => {
      attentionPoll = null;
    });
    return attentionPoll;
  }

  function stopWatcher() {
    watcherEpoch += 1;
    if (watcherTimer !== null) {
      cancelTimeout(watcherTimer);
      watcherTimer = null;
    }
  }

  function scheduleWatcher(epoch) {
    const binding = extension.binding();
    if (binding === null || liveBindings().length === 0 || epoch !== watcherEpoch) {
      return;
    }
    watcherTimer = scheduleTimeout(async () => {
      watcherTimer = null;
      try {
        await pollAttention();
      } catch {
        // Retry the read-only status poll on the next bounded tick.
      } finally {
        if (
          epoch === watcherEpoch
          && extension.binding() !== null
          && liveBindings().length > 0
        ) {
          scheduleWatcher(epoch);
        }
      }
    }, attentionPollMs);
    watcherTimer?.unref?.();
  }

  function restartWatcher() {
    stopWatcher();
    if (
      extension.binding() !== null
      && liveBindings().length > 0
    ) {
      scheduleWatcher(watcherEpoch);
    }
  }

  async function activateAndPersist(request, context, projectSelector = null) {
    if (typeof pi.appendEntry !== "function") {
      throw new Error("pi_session_persistence_unavailable");
    }
    captainContext = context;
    const resolved = await (options?.resolveProject
      ? options.resolveProject(projectSelector, context)
      : projectOperation("resolve", { selector: projectSelector }, context));
    const project = resolved?.project ?? resolved;
    if (typeof project?.root !== "string") {
      throw new Error("kimiflow_project_resolution_invalid");
    }
    const pending = extension.pendingClaim(context);
    const claim = extension.activationClaim(request, context, project.root);
    const result = await extension.activate(
      request,
      context,
      claim,
      (preparedClaim) => {
        if (pending === null) pi.appendEntry(CLAIM_ENTRY, preparedClaim);
      },
    );
    const binding = extension.binding();
    if (binding.run !== null && binding.providerSessionId !== null) {
      pi.appendEntry(BINDING_ENTRY, binding);
    }
    restartWatcher();
    lockCaptainTools();
    context?.ui?.notify?.(
      "Kimiflow is running in the background; this Pi session remains the Captain.",
      "info",
    );
    return result;
  }

  pi.registerCommand?.("kimiflow", {
    description: "Activate Kimiflow from this already-running Pi session.",
    handler: async (args, context) => {
      const match = typeof args === "string"
        ? args.match(/^--project(?:=|\s+)([^\s]+)\s+([\s\S]+)$/)
        : null;
      await activateAndPersist(match ? match[2] : args, context, match ? match[1] : null);
    },
  });
  pi.registerTool?.({
    name: "kimiflow_activate",
    label: "Activate Kimiflow",
    description: "Run the user's feature with Kimiflow while this Pi conversation remains available.",
    parameters: activationParameters,
    async execute(_toolCallId, params, _signal, _onUpdate, context) {
      return textResult(await activateAndPersist(
        params.request, context, params.project ?? null,
      ));
    },
  });
  pi.registerCommand?.("kimiflow-project", {
    description: "List, register, clone, resolve, or remove private Kimiflow projects.",
    handler: async (args, context) => {
      const parts = typeof args === "string" ? args.trim().split(/\s+/).filter(Boolean) : [];
      const action = parts[0] || "list";
      const result = await projectOperation(action, { selector: parts[1], name: parts[2] }, context);
      context?.ui?.notify?.(JSON.stringify(result), "info");
    },
  });
  pi.registerTool?.({
    name: "kimiflow_project",
    label: "Kimiflow Project",
    description: "Manage or resolve Kimiflow's private project registry.",
    parameters: projectParameters,
    async execute(_toolCallId, params, _signal, _onUpdate, context) {
      return textResult(await projectOperation(params.action, params, context));
    },
  });
  pi.registerCommand?.("kimiflow-status", {
    description: "Read the current Kimiflow runner status.",
    handler: async (_args, context) => {
      context?.ui?.notify?.(boundedStatus(await extension.status()), "info");
    },
  });
  pi.registerTool?.({
    name: "kimiflow_status",
    label: "Kimiflow Status",
    description: "Read the authoritative Runner status for the active Kimiflow worker.",
    parameters: statusParameters,
    async execute() {
      return textResult(await extension.status());
    },
  });
  for (const kind of ["reply", "steer"]) {
    pi.registerTool?.({
      name: `kimiflow_${kind}`,
      label: `Kimiflow ${kind === "reply" ? "Reply" : "Steer"}`,
      description: `${kind} at the exact safe Kimiflow runner boundary.`,
      parameters: deliveryParameters,
      async execute(_toolCallId, params, _signal, _onUpdate, context) {
        if (typeof pi.appendEntry !== "function") {
          throw new Error("pi_session_persistence_unavailable");
        }
        return textResult(await extension.deliver(
          kind,
          params,
          context,
          (binding) => pi.appendEntry(BINDING_ENTRY, binding),
        ));
      },
    });
  }
  pi.on?.("tool_call", (event) => {
    if (
      liveBindings().length > 0
      && ["bash", "edit", "write"].includes(event?.toolName)
    ) {
      return {
        block: true,
        reason: "This Pi session is the read-only Kimiflow Captain; write work belongs to its isolated Fleet worker.",
      };
    }
    return undefined;
  });
  pi.on?.("before_agent_start", (event) => {
    if (liveBindings().length === 0) return undefined;
    return {
      systemPrompt: `${event.systemPrompt}\n\n${CAPTAIN_DIALOG_PROMPT}`,
    };
  });
  pi.on?.("agent_settled", () => pollAttention());
  pi.on?.("session_start", async (_event, context) => {
    captainContext = context;
    stopWatcher();
    extension.restoreForSession(context);
    await pollAttention();
    lockCaptainTools();
    restartWatcher();
  });
  pi.on?.("session_shutdown", () => {
    presentationEpoch += 1;
    pendingPresentations.clear();
    captainContext = null;
    stopWatcher();
    unlockCaptainTools();
    extension.clearMemory();
  });
  return extension;
}
