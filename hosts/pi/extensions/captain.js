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
const BRIDGE_ENV = "KIMIFLOW_PI_BRIDGE_BINDING";
const START_CLAIM_ENV = "KIMIFLOW_PI_START_CLAIM";
const START_CLAIM_NAME = "PI-BRIDGE-START-CLAIM";
const CLEANUP_LEASES_NAME = "PI-CLEANUP-LEASES-v1";
const STATUS_LIMIT = 320;
const DEFAULT_ATTENTION_POLL_MS = 1000;
const MIN_ATTENTION_POLL_MS = 250;
const MAX_ATTENTION_POLL_MS = 30000;
const SPAWN_ACCEPTANCE_MS = 250;
const SPAWN_HANDOFF_TIMEOUT_MS = 5000;
const STALE_REAP_TIMEOUT_MS = 5000;
const ACTIVE_RUNNER_STATES = new Set([
  "running",
  "parked",
  "interrupted",
  "transport_error",
  "exhausted",
]);
const RESUMABLE_STATES = new Set([
  "parked",
  "interrupted",
  "transport_error",
  "exhausted",
]);
const TERMINAL_RUNNER_STATES = new Set(["done", "failed", "aborted"]);
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

function currentBridgeEntry(context) {
  const entries = context?.sessionManager?.getBranch?.();
  if (!Array.isArray(entries)) return null;
  const primarySession = sessionId(context);
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
    if (value?.captainSessionId === primarySession) {
      return { kind: entry.customType, value };
    }
  }
  return null;
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
  if (
    snapshot?.status === "transport_error"
    && snapshot?.active_run?.present !== true
    && typeof snapshot?.runner?.active_run !== "string"
  ) {
    return false;
  }
  return snapshot?.active_run?.present === true
    || ACTIVE_RUNNER_STATES.has(snapshot?.status);
}

function runnerIsTerminal(snapshot) {
  return snapshot?.active_run?.present !== true
    && TERMINAL_RUNNER_STATES.has(snapshot?.status);
}

function snapshotIdentity(snapshot, binding = null) {
  const run = snapshot?.active_run?.run ?? snapshot?.runner?.active_run;
  const providerSessionId = snapshot?.runner?.session_id;
  if (
    typeof run !== "string"
    || !RUN_IDENTITY.test(run)
    || typeof providerSessionId !== "string"
    || !IDENTITY.test(providerSessionId)
  ) {
    return null;
  }
  if (binding?.run === null) {
    const bridge = snapshot?.runner?.bridge;
    if (
      bridge?.schema_version !== 1
      || bridge.captain_session_id !== binding.captainSessionId
      || bridge.worker_id !== binding.workerId
    ) {
      return null;
    }
  }
  return { run, providerSessionId };
}

function provisionalTransportFailure(snapshot, binding) {
  const receipt = snapshot?.runner;
  const bridge = receipt?.bridge;
  const providerSessionId = receipt?.session_id;
  if (
    binding?.run !== null
    || binding?.providerSessionId !== null
    || snapshot?.status !== "transport_error"
    || snapshot?.active_run?.present !== false
    || receipt?.active_run !== null
    || typeof providerSessionId !== "string"
    || !IDENTITY.test(providerSessionId)
    || bridge?.schema_version !== 1
    || bridge.captain_session_id !== binding.captainSessionId
    || bridge.worker_id !== binding.workerId
  ) {
    return null;
  }
  return { run: null, providerSessionId };
}

function runnerControllerLost(snapshot, binding) {
  const controllerPid = snapshot?.runner?.controller_pid;
  const identity = snapshotIdentity(snapshot, binding);
  return snapshot?.status === "running"
    && Number.isInteger(controllerPid)
    && controllerPid > 1
    && !processAlive(controllerPid)
    && !processGroupAlive(controllerPid)
    && identity?.run === binding?.run
    && identity?.providerSessionId === binding?.providerSessionId;
}

function visibleSnapshot(snapshot, binding) {
  if (
    snapshot?.active_run?.present === true
    && snapshot.active_run.awaiting_user === true
  ) {
    return {
      ...snapshot,
      status: "awaiting_user",
    };
  }
  if (!runnerControllerLost(snapshot, binding)) return snapshot;
  return {
    ...snapshot,
    status: "interrupted",
    runner: {
      ...snapshot.runner,
      status: "interrupted",
    },
  };
}

function deliveryBoundary(snapshot, run, providerSessionId) {
  return digest(JSON.stringify({
    run,
    providerSessionId,
    status: typeof snapshot?.status === "string" ? snapshot.status : "unknown",
    transitionVersion: Number.isInteger(snapshot?.runner?.turns)
      ? snapshot.runner.turns
      : 0,
    awaitingUser: snapshot?.active_run?.awaiting_user === true,
  }));
}

function transition(snapshot, binding) {
  if (binding === null || snapshot === null || typeof snapshot !== "object") {
    return null;
  }
  const activeRun = snapshot.active_run;
  const receipt = snapshot.runner;
  let kind = null;
  if (
    activeRun?.present === true
    && activeRun.awaiting_user === true
  ) {
    kind = "question";
  } else if (snapshot.status === "done") {
    kind = "completion";
  } else if (["failed", "aborted", "transport_error"].includes(snapshot.status)) {
    kind = "failure";
  } else if (runnerControllerLost(snapshot, binding)) {
    kind = "failure";
  }
  const run = activeRun?.run ?? receipt?.active_run;
  let providerSessionId = receipt?.session_id;
  const provisionalFailure = kind === "failure"
    ? provisionalTransportFailure(snapshot, binding)
    : null;
  if (
    kind === null
    || (
      provisionalFailure === null
      && (
        run !== binding.run
        || providerSessionId !== binding.providerSessionId
      )
    )
  ) {
    return null;
  }
  const boundRun = provisionalFailure === null ? run : null;
  if (provisionalFailure !== null) {
    providerSessionId = provisionalFailure.providerSessionId;
  }
  const identity = {
    root: binding.root,
    run: boundRun,
    captain_session_id: binding.captainSessionId,
    worker_id: binding.workerId,
    provider_session_id: providerSessionId,
    kind,
    transition_version: Number.isInteger(receipt?.turns) ? receipt.turns : 0,
  };
  if (
    kind === "failure"
    && typeof receipt?.diagnostic_code === "string"
    && IDENTITY.test(receipt.diagnostic_code)
  ) {
    identity.diagnostic_code = receipt.diagnostic_code;
  }
  if (kind === "question") {
    const request = typeof activeRun.awaiting_request === "string"
      && Buffer.byteLength(activeRun.awaiting_request, "utf8") <= 64 * 1024
      ? activeRun.awaiting_request.trim()
      : "";
    identity.question = request || (
      typeof activeRun.awaiting_reason === "string"
      && activeRun.awaiting_reason.trim()
      ? activeRun.awaiting_reason.trim()
      : "Kimiflow is waiting for a material user decision."
    );
  }
  const hash = digest(JSON.stringify(identity)).slice("sha256:".length, 24 + "sha256:".length);
  return { attention_id: `attention-${hash}`, ...identity, actionable: true };
}

function attentionContent(value) {
  if (value.kind === "question") return value.question;
  const run = typeof value.run === "string" ? value.run : "Kimiflow";
  return value.kind === "completion"
    ? `✓ Kimiflow · ${run}`
    : `⚠ Kimiflow · ${run}`;
}

function restoredAttentionIds(context) {
  const result = new Set();
  const branch = context?.sessionManager?.getBranch?.();
  if (!Array.isArray(branch)) return result;
  for (const entry of branch) {
    if (
      entry?.type !== "custom_message"
      || entry?.customType !== "kimiflow_attention"
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
  const run = value?.active_run?.run ?? value?.runner?.active_run;
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
} = {}) {
  const runner = path.join(root, "hooks", "kimiflow-runner.sh");
  const piHost = path.join(root, "hooks", "pi-host.sh");
  const seenAttention = new Set();
  let active = null;

  async function runnerStatus(cwd) {
    return exec(runner, ["status", "--root", cwd], { cwd });
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

  function activationClaim(request, context) {
    if (typeof request !== "string" || request.trim().length === 0) {
      throw new Error("kimiflow_request_required");
    }
    const pending = currentClaim(context);
    const cwd = contextRoot(context);
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
      || validated.root !== contextRoot(context)
      || validated.captainSessionId !== sessionId(context)
      || validated.requestDigest !== digest(request.trim())
    ) {
      throw new Error("kimiflow_activation_claim_invalid");
    }
    const recovering = sameClaim(pending, validated);
    if (pending !== null && !recovering) {
      throw new Error("kimiflow_pending_activation_mismatch");
    }
    const snapshot = await runnerStatus(validated.root);
    if (
      active !== null
      && active.terminal !== true
      && !runnerIsTerminal(snapshot)
      && !(recovering && active.run === null && !runnerIsActive(snapshot))
    ) {
      throw new Error("kimiflow_run_active");
    }
    if (runnerIsActive(snapshot)) {
      if (!recovering) throw new Error("kimiflow_run_active");
    } else {
      await preflightPi(validated.root);
      const startClaim = startClaims.acquire(validated);
      try {
        await beforeSpawn?.(validated);
        await spawnRunner([
          "run",
          request.trim(),
          "--root", validated.root,
          "--adapter", "command",
          "--adapter-command", piHost,
          "--model", validated.modelSelection,
          "--require-feature", "structured_events",
        ], validated.root, validated, startClaim);
      } catch (error) {
        startClaims.release(startClaim);
        throw error;
      }
    }
    const provisional = provisionalBinding(validated);
    active = runnerIsActive(snapshot)
      ? bindSnapshot(provisional, snapshot)
      : provisional;
    if (active === null) throw new Error("kimiflow_runner_identity_invalid");
    seenAttention.clear();
    return {
      status: recovering && runnerIsActive(snapshot) ? "recovered" : "activated",
      ...active,
    };
  }

  async function activate(request, context, claim, beforeSpawn) {
    const cwd = contextRoot(context);
    const coordinator = rootCoordinator(cwd);
    if (coordinator.activation) {
      throw new Error("kimiflow_activation_in_progress");
    }
    coordinator.activation = true;
    try {
      return await activateOnce(request, context, claim, beforeSpawn);
    } finally {
      coordinator.activation = false;
      releaseCoordinator(cwd, coordinator);
    }
  }

  async function status() {
    if (active === null) return { status: "inactive" };
    const snapshot = await runnerStatus(active.root);
    if (active.run === null) {
      const bound = bindSnapshot(active, snapshot);
      if (bound !== null) {
        active = bound;
        return visibleSnapshot(snapshot, active);
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
    return visibleSnapshot(snapshot, active);
  }

  async function deliver(kind, params, context, persist) {
    if (active === null) throw new Error("kimiflow_bridge_inactive");
    if (active.terminal === true) throw new Error("kimiflow_bridge_terminal");
    if (
      contextRoot(context) !== active.root
      || sessionId(context) !== active.captainSessionId
    ) {
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
    const waiting = snapshot?.active_run?.present === true
      && snapshot.active_run.awaiting_user === true;
    if (
      !waiting
      && !RESUMABLE_STATES.has(snapshot?.status)
      && !runnerControllerLost(snapshot, active)
    ) {
      throw new Error(`${kind}_requires_resumable_boundary`);
    }
    const boundary = deliveryBoundary(snapshot, run, providerSessionId);
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

  async function pollAttention(pi) {
    if (active === null) return { status: "inactive", announced: 0 };
    if (active.terminal === true) {
      return { status: "terminal", announced: 0 };
    }
    const snapshot = await runnerStatus(active.root);
    if (active.run === null) {
      const bound = bindSnapshot(active, snapshot);
      if (bound === null) {
        if (provisionalTransportFailure(snapshot, active) === null) {
          return { status: "attention", announced: 0, snapshot };
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
        return { status: "attention", announced: 0, snapshot };
      }
    }
    if (active.deliveryPending) {
      const boundary = deliveryBoundary(
        snapshot,
        active.run,
        active.providerSessionId,
      );
      if (boundary !== active.deliveryBoundary) {
        const reconciled = { ...active, deliveryPending: false };
        pi.appendEntry?.(BINDING_ENTRY, reconciled);
        active = reconciled;
      }
    }
    const observed = visibleSnapshot(snapshot, active);
    const value = transition(snapshot, active);
    let announced = 0;
    if (value !== null && !seenAttention.has(value.attention_id)) {
      if (typeof pi.sendMessage === "function") {
        pi.sendMessage({
          customType: "kimiflow_attention",
          content: attentionContent(value),
          details: value,
          display: true,
        });
        seenAttention.add(value.attention_id);
        announced = 1;
      }
    }
    if (
      (
        runnerIsTerminal(snapshot)
        || provisionalTransportFailure(snapshot, active) !== null
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
    return { status: "attention", announced, snapshot: observed };
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
      const current = currentBridgeEntry(context);
      const binding = current?.kind === BINDING_ENTRY
        ? current.value
        : current?.kind === CLAIM_ENTRY
          ? provisionalBinding(current.value)
          : null;
      active = binding?.root === contextRoot(context) ? { ...binding } : null;
      return active ? { ...active } : null;
    },
    clearMemory() {
      active = null;
      seenAttention.clear();
    },
    binding() {
      return active ? { ...active } : null;
    },
  };
}

export default function registerCaptainExtension(pi, options) {
  const extension = createCaptainExtension({
    ...options,
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

  function pollAttention() {
    if (attentionPoll !== null) return attentionPoll;
    attentionPoll = Promise.resolve(extension.pollAttention(pi)).finally(() => {
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
    if (binding === null || binding.terminal === true || epoch !== watcherEpoch) {
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
          && extension.binding().terminal !== true
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
      && extension.binding().terminal !== true
    ) {
      scheduleWatcher(watcherEpoch);
    }
  }

  async function activateAndPersist(request, context) {
    if (typeof pi.appendEntry !== "function") {
      throw new Error("pi_session_persistence_unavailable");
    }
    const pending = extension.pendingClaim(context);
    const claim = extension.activationClaim(request, context);
    const result = await extension.activate(
      request,
      context,
      claim,
      () => {
        if (pending === null) pi.appendEntry(CLAIM_ENTRY, claim);
      },
    );
    const binding = extension.binding();
    if (binding.run !== null && binding.providerSessionId !== null) {
      pi.appendEntry(BINDING_ENTRY, binding);
    }
    restartWatcher();
    context?.ui?.notify?.(
      "Kimiflow is running in the background; this Pi session remains the Captain.",
      "info",
    );
    return result;
  }

  pi.registerCommand?.("kimiflow", {
    description: "Activate Kimiflow from this already-running Pi session.",
    handler: async (args, context) => {
      await activateAndPersist(args, context);
    },
  });
  pi.registerTool?.({
    name: "kimiflow_activate",
    label: "Activate Kimiflow",
    description: "Run the user's feature with Kimiflow while this Pi conversation remains available.",
    parameters: activationParameters,
    async execute(_toolCallId, params, _signal, _onUpdate, context) {
      return textResult(await activateAndPersist(params.request, context));
    },
  });
  pi.registerCommand?.("kimiflow-status", {
    description: "Read the current Kimiflow runner status.",
    handler: async (_args, context) => {
      context?.ui?.notify?.(boundedStatus(await extension.status()), "info");
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
  pi.on?.("agent_settled", () => pollAttention());
  pi.on?.("session_start", async (_event, context) => {
    stopWatcher();
    extension.restoreForSession(context);
    await pollAttention();
    restartWatcher();
  });
  pi.on?.("session_shutdown", () => {
    stopWatcher();
    extension.clearMemory();
  });
  return extension;
}
