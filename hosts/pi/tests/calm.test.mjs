import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const source = fileURLToPath(new URL("../extensions/calm.js", import.meta.url));

function piModules() {
  try {
    const globalRoot = execFileSync("npm", ["root", "-g"], {
      encoding: "utf8",
    }).trim();
    const codingAgent = path.join(
      globalRoot,
      "@earendil-works",
      "pi-coding-agent",
    );
    const tui = path.join(
      codingAgent,
      "node_modules",
      "@earendil-works",
      "pi-tui",
    );
    return existsSync(codingAgent) && existsSync(tui)
      ? { codingAgent, tui }
      : null;
  } catch {
    return null;
  }
}

function fixture(modules, verbosity) {
  const root = mkdtempSync(path.join(tmpdir(), "kimiflow-calm-"));
  const scope = path.join(root, "node_modules", "@earendil-works");
  mkdirSync(scope, { recursive: true });
  symlinkSync(
    modules.codingAgent,
    path.join(scope, "pi-coding-agent"),
    "dir",
  );
  symlinkSync(modules.tui, path.join(scope, "pi-tui"), "dir");
  copyFileSync(source, path.join(root, "calm.js"));
  mkdirSync(path.join(root, ".kimiflow"), { recursive: true });
  writeFileSync(
    path.join(root, ".kimiflow", "verbosity"),
    `${verbosity}\n`,
  );
  writeFileSync(path.join(root, "package.json"), '{"type":"module"}\n');
  writeFileSync(
    path.join(root, "exercise.mjs"),
    `
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import * as PiCodingAgent from "@earendil-works/pi-coding-agent";
import registerCalm, {
  calmEnabled,
  operationalInput,
} from "./calm.js";

const expected = process.argv[2];
assert.equal(calmEnabled(process.cwd()), expected === "quiet");
assert.equal(
  calmEnabled(process.cwd(), { KIMIFLOW_PI_VERBOSITY: "quiet" }),
  true,
);
assert.equal(
  calmEnabled(process.cwd(), { KIMIFLOW_PI_VERBOSITY: "balanced" }),
  false,
);
const globalRoot = path.join(process.cwd(), "codex-home");
mkdirSync(path.join(globalRoot, "kimiflow"), { recursive: true });
writeFileSync(path.join(globalRoot, "kimiflow", "verbosity"), "quiet\\n");
assert.equal(
  calmEnabled(path.join(process.cwd(), "project-without-setting"), {
    CODEX_HOME: globalRoot,
  }),
  true,
);
assert.equal(
  operationalInput("\\u2063kimiflow:transport-v1\\nrequest"),
  true,
);
assert.equal(
  operationalInput("\\u2063kimiflow:transport-v2\\nrequest"),
  true,
);
assert.equal(operationalInput("kimiflow:transport-v1\\nrequest"), false);

const tools = [];
const handlers = new Map();
const pi = {
  registerTool(definition) {
    tools.push(definition);
  },
  on(name, handler) {
    const values = handlers.get(name) ?? [];
    values.push(handler);
    handlers.set(name, values);
  },
};
registerCalm(pi);

if (expected !== "quiet") {
  assert.equal(tools.length, 0);
  assert.equal(handlers.size, 0);
  process.exit(0);
}

assert.equal(tools.length, 7);
assert.deepEqual(
  tools.map(({ name }) => name).sort(),
  ["bash", "edit", "find", "grep", "ls", "read", "write"],
);
for (const tool of tools) {
  assert.deepEqual(
    tool.renderCall({}, undefined, { state: {} }).render(80),
    [],
  );
  assert.deepEqual(
    tool.renderResult({}, {}, undefined, { state: {} }).render(80),
    [],
  );
}

const original = {
  role: "assistant",
  content: [
    { type: "thinking", thinking: "private reasoning" },
    { type: "text", text: "visible answer" },
  ],
  stopReason: "stop",
};
const component = new PiCodingAgent.AssistantMessageComponent(
  original,
  true,
  undefined,
  "",
);
assert.equal(component.render(100).join("\\n").includes("private reasoning"), false);
assert.equal(component.render(100).join("\\n").includes("visible answer"), true);
assert.equal(original.content[0].thinking, "private reasoning");

const children = [];
PiCodingAgent.InteractiveMode.prototype.addMessageToChat.call(
  {
    chatContainer: {
      children,
      addChild(component) {
        children.push(component);
      },
    },
    editor: { addToHistory() {} },
    getMarkdownThemeWithSettings() {
      return undefined;
    },
    getUserMessageText(message) {
      return message.content[0].text;
    },
    outputPad: 1,
  },
  {
    role: "user",
    content: [{
      type: "text",
      text: "\\u2063kimiflow:transport-v1\\ninternal request",
    }],
  },
);
assert.equal(children.length, 1);
assert.deepEqual(children[0].render(100), []);

let hiddenThinkingLabel;
const start = handlers.get("session_start")[0];
start({}, {
  ui: {
    setHiddenThinkingLabel(value) {
      hiddenThinkingLabel = value;
    },
    onTerminalInput() {
      return () => {};
    },
  },
});
assert.equal(hiddenThinkingLabel, "");

for (const name of [
  "kimiflow_activate",
  "kimiflow_reply",
  "kimiflow_steer",
  "kimiflow_subagent",
]) {
  const component = Object.create(
    PiCodingAgent.ToolExecutionComponent.prototype,
  );
  component.toolName = name;
  assert.deepEqual(component.render(100), []);
}
`,
  );
  return root;
}

test("quiet is a renderer-only Calm projection; balanced changes nothing", (t) => {
  const modules = piModules();
  if (modules === null) {
    t.skip("the tested Pi runtime is not installed");
    return;
  }
  for (const verbosity of ["quiet", "balanced"]) {
    const root = fixture(modules, verbosity);
    const result = spawnSync(
      process.execPath,
      [path.join(root, "exercise.mjs"), verbosity],
      {
        cwd: root,
        encoding: "utf8",
        timeout: 10000,
      },
    );
    assert.equal(
      result.status,
      0,
      `${verbosity} Calm contract failed:\n${result.stderr}\n${result.stdout}`,
    );
  }
});
