import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";
import * as PiCodingAgent from "@earendil-works/pi-coding-agent";
import {
  Box,
  Container,
  getKeybindings,
} from "@earendil-works/pi-tui";

const TRANSPORT_PREFIX_FAMILY = "\u2063kimiflow:transport-";
const ASSISTANT_PATCH = Symbol.for("kimiflow:calm-assistant-layout:pi-0.83");
const USER_PATCH = Symbol.for("kimiflow:calm-user-layout:pi-0.83");
const TOOL_PATCH = Symbol.for("kimiflow:calm-operational-tools:pi-0.83");
const KIMIFLOW_OPERATIONAL_TOOLS = new Set([
  "kimiflow_activate",
  "kimiflow_reply",
  "kimiflow_steer",
  "kimiflow_subagent",
]);

let exportRendering = false;

function configuredLevel(pathname) {
  try {
    const value = readFileSync(pathname, "utf8")
      .split(/\r?\n/, 1)[0]
      .trim();
    return ["quiet", "balanced", "verbose"].includes(value) ? value : null;
  } catch {
    return null;
  }
}

export function calmEnabled(root, environment = process.env) {
  if (["quiet", "balanced", "verbose"].includes(environment.KIMIFLOW_PI_VERBOSITY)) {
    return environment.KIMIFLOW_PI_VERBOSITY === "quiet";
  }
  const project = configuredLevel(resolve(root, ".kimiflow", "verbosity"));
  if (project !== null) return project === "quiet";
  const codexHome = environment.CODEX_HOME ?? resolve(homedir(), ".codex");
  return configuredLevel(resolve(codexHome, "kimiflow", "verbosity")) === "quiet";
}

function installOperationalToolLayout() {
  const registry = globalThis;
  if (registry[TOOL_PATCH]) return;
  const Component = PiCodingAgent.ToolExecutionComponent;
  const original = Component?.prototype?.render;
  if (typeof Component !== "function" || typeof original !== "function") {
    throw new Error("Pi ToolExecutionComponent.render is unavailable");
  }
  Component.prototype.render = function render(width) {
    if (!exportRendering && KIMIFLOW_OPERATIONAL_TOOLS.has(this.toolName)) {
      return [];
    }
    return original.call(this, width);
  };
  registry[TOOL_PATCH] = true;
}

export function operationalInput(text) {
  return typeof text === "string" && text.startsWith(TRANSPORT_PREFIX_FAMILY);
}

function textOnly(content) {
  if (typeof content === "string") return true;
  return Array.isArray(content)
    && content.length > 0
    && content.every((block) => (
      block !== null
      && typeof block === "object"
      && block.type === "text"
      && typeof block.text === "string"
    ));
}

function installAssistantLayout() {
  const registry = globalThis;
  if (registry[ASSISTANT_PATCH]) return;
  const Component = PiCodingAgent.AssistantMessageComponent;
  const original = Component?.prototype?.updateContent;
  if (typeof Component !== "function" || typeof original !== "function") {
    throw new Error("Pi AssistantMessageComponent.updateContent is unavailable");
  }
  Component.prototype.updateContent = function updateContent(message) {
    const hideThinking = this.hiddenThinkingLabel === "" && this.hideThinkingBlock;
    const presented = hideThinking
      ? {
          ...message,
          content: message.content.filter((block) => block.type !== "thinking"),
        }
      : message;
    original.call(this, presented);
    if (presented !== message) this.lastMessage = message;
  };
  registry[ASSISTANT_PATCH] = true;
}

function installOperationalUserLayout() {
  const registry = globalThis;
  if (registry[USER_PATCH]) return;
  const InteractiveMode = PiCodingAgent.InteractiveMode;
  const UserMessage = PiCodingAgent.UserMessageComponent;
  const original = InteractiveMode?.prototype?.addMessageToChat;
  if (
    typeof InteractiveMode !== "function"
    || typeof UserMessage !== "function"
    || typeof original !== "function"
  ) {
    throw new Error("Pi operational user-row layout is unavailable");
  }
  class CalmOperationalUserMessage extends UserMessage {
    render(width) {
      if (!exportRendering) return [];
      return super.render(width);
    }
  }
  InteractiveMode.prototype.addMessageToChat = function addMessageToChat(
    message,
    options,
  ) {
    if (message?.role !== "user" || !textOnly(message.content)) {
      original.call(this, message, options);
      return;
    }
    const text = this.getUserMessageText(message);
    if (!operationalInput(text)) {
      original.call(this, message, options);
      return;
    }
    this.chatContainer.addChild(new CalmOperationalUserMessage(
      text,
      this.getMarkdownThemeWithSettings(),
      this.outputPad,
    ));
    if (options?.populateHistory) this.editor.addToHistory?.(text);
  };
  registry[USER_PATCH] = true;
}

function registerBuiltIn(pi, factory) {
  const definitions = new Map();
  const definitionFor = (cwd) => {
    if (!definitions.has(cwd)) definitions.set(cwd, factory(cwd));
    return definitions.get(cwd);
  };
  const original = definitionFor(process.cwd());
  const renderCall = original.renderCall;
  const renderResult = original.renderResult;
  if (typeof renderCall !== "function" || typeof renderResult !== "function") {
    throw new Error(`Pi tool renderer is unavailable: ${original.name}`);
  }
  const selfShell = original.renderShell === "self";
  const shells = new WeakMap();
  const shellFor = (context) => {
    const row = context.state;
    if (!shells.has(row)) shells.set(row, {});
    return shells.get(row);
  };
  const refreshShell = (state, theme, context) => {
    const background = context.isPartial
      ? (text) => theme.bg("toolPendingBg", text)
      : context.isError
        ? (text) => theme.bg("toolErrorBg", text)
        : (text) => theme.bg("toolSuccessBg", text);
    const shell = state.shell ?? new Box(1, 1, background);
    state.shell = shell;
    shell.setBgFn(background);
    shell.clear();
    if (state.call) shell.addChild(state.call);
    if (state.result) shell.addChild(state.result);
    return shell;
  };
  pi.registerTool({
    ...original,
    renderShell: "self",
    execute(toolCallId, params, signal, onUpdate, context) {
      return definitionFor(context.cwd).execute(
        toolCallId,
        params,
        signal,
        onUpdate,
        context,
      );
    },
    renderCall(args, theme, context) {
      if (!exportRendering) return new Container();
      if (selfShell) return renderCall(args, theme, context);
      const state = shellFor(context);
      state.call = renderCall(args, theme, {
        ...context,
        lastComponent: state.call,
      });
      return refreshShell(state, theme, context);
    },
    renderResult(result, options, theme, context) {
      if (!exportRendering) return new Container();
      if (selfShell) return renderResult(result, options, theme, context);
      const state = shellFor(context);
      state.result = renderResult(result, options, theme, {
        ...context,
        lastComponent: state.result,
      });
      refreshShell(state, theme, context);
      return new Container();
    },
  });
}

export default function registerCalmExtension(pi) {
  if (!calmEnabled(process.cwd())) return;
  for (const install of [
    installAssistantLayout,
    installOperationalUserLayout,
    installOperationalToolLayout,
  ]) {
    try {
      install();
    } catch (error) {
      console.error(
        `Kimiflow Calm presentation adapter unavailable: ${error?.message ?? error}`,
      );
    }
  }
  for (const factory of [
    PiCodingAgent.createReadToolDefinition,
    PiCodingAgent.createBashToolDefinition,
    PiCodingAgent.createEditToolDefinition,
    PiCodingAgent.createWriteToolDefinition,
    PiCodingAgent.createGrepToolDefinition,
    PiCodingAgent.createFindToolDefinition,
    PiCodingAgent.createLsToolDefinition,
  ]) {
    try {
      if (typeof factory !== "function") {
        throw new Error("a required Pi built-in tool factory is unavailable");
      }
      registerBuiltIn(pi, factory);
    } catch (error) {
      console.error(
        `Kimiflow Calm tool adapter unavailable: ${error?.message ?? error}`,
      );
    }
  }
  pi.on("session_start", (_event, context) => {
    exportRendering = false;
    context.ui.setHiddenThinkingLabel?.("");
    const removeInputHandler = context.ui.onTerminalInput?.((data) => {
      if (!getKeybindings().matches(data, "tui.input.submit")) return;
      const input = context.ui.getEditorText?.().trim() ?? "";
      if (
        input !== "/share"
        && input !== "/export"
        && !input.startsWith("/export ")
      ) return;
      exportRendering = true;
      setTimeout(() => {
        exportRendering = false;
        const expanded = context.ui.getToolsExpanded?.();
        if (typeof expanded === "boolean") {
          context.ui.setToolsExpanded(!expanded);
          context.ui.setToolsExpanded(expanded);
        }
      }, 0);
    });
    if (typeof removeInputHandler === "function") {
      pi.on("session_shutdown", () => removeInputHandler());
    }
  });
}
