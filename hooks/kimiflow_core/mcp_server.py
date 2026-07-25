"""Thin MCP 2025-11-25 stdio facade over the existing Kimiflow run bridge."""

import argparse
import contextlib
import json
import os
import re
import sys

from . import active_run, run_bridge


PROTOCOL_VERSION = "2025-11-25"
MAX_MESSAGE_BYTES = 256 * 1024
IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")


class McpError(ValueError):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _reject_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _tools():
    empty = {"type": "object", "additionalProperties": False}
    return [
        {
            "name": "kimiflow_status",
            "title": "Kimiflow status",
            "description": "Read current Kimiflow readiness and mutation cursor.",
            "inputSchema": empty,
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
        {
            "name": "kimiflow_context",
            "title": "Kimiflow phase context",
            "description": "Read the bounded context packet for a run phase.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run": {"type": "string"},
                    "phase": {"type": "integer", "minimum": 0, "maximum": 7},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
        {
            "name": "kimiflow_scorecard",
            "title": "Kimiflow scorecard",
            "description": "Read the evidence-based run scorecard.",
            "inputSchema": {
                "type": "object",
                "properties": {"run": {"type": "string"}},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
        },
        {
            "name": "kimiflow_action",
            "title": "Kimiflow action",
            "description": "Apply one owner-bound, replay-safe run item mutation.",
            "inputSchema": {
                "type": "object",
                "required": ["action_id", "cursor", "operation", "arguments"],
                "properties": {
                    "action_id": {"type": "string"},
                    "cursor": {
                        "type": "object",
                        "required": ["sequence", "readiness_fingerprint"],
                        "properties": {
                            "sequence": {"type": "integer", "minimum": 0},
                            "readiness_fingerprint": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "operation": {"enum": sorted(run_bridge.OPERATIONS)},
                    "arguments": {"type": "object"},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        },
    ]


TOOL_MAP = {
    "kimiflow_status": "run/readiness",
    "kimiflow_context": "run/context",
    "kimiflow_scorecard": "run/scorecard",
    "kimiflow_action": "run/mutate",
}


@contextlib.contextmanager
def _bound_identity(host, session_id):
    keys = ("KIMIFLOW_SESSION_HOST", "KIMIFLOW_SESSION_ID")
    prior = {key: os.environ.get(key) for key in keys}
    try:
        if host is None or session_id is None:
            for key in keys:
                os.environ.pop(key, None)
        else:
            os.environ[keys[0]] = host
            os.environ[keys[1]] = session_id
        yield
    finally:
        for key in keys:
            if prior[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior[key]


def _tool_result(value, is_error=False):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": value,
        "isError": bool(is_error),
    }


def _runtime_version(root):
    for candidate in (
        os.path.join(root, ".codex-plugin", "plugin.json"),
        os.path.join(os.path.dirname(__file__), "..", "..", ".codex-plugin", "plugin.json"),
    ):
        try:
            with open(candidate, "rb") as handle:
                payload = handle.read(16 * 1024 + 1)
            if len(payload) > 16 * 1024:
                continue
            version = json.loads(payload.decode("utf-8")).get("version")
            if isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                return version
        except (OSError, UnicodeError, ValueError, AttributeError):
            continue
    return "0.0.0"


class Server:
    def __init__(self, root, host=None, session_id=None):
        self.root = active_run.resolve_root(root, strict=False)
        if (host is None) != (session_id is None):
            raise McpError(-32602, "host and session identity must be supplied together")
        if host is not None and (
            not isinstance(host, str) or IDENTITY_RE.fullmatch(host) is None
            or not isinstance(session_id, str) or IDENTITY_RE.fullmatch(session_id) is None
        ):
            raise McpError(-32602, "invalid owner identity")
        self.host = host
        self.session_id = session_id
        self.initialized = False
        self.ready = False
        self.seen_ids = set()

    def _request_id(self, message):
        request_id = message.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)) or request_id in self.seen_ids:
            raise McpError(-32600, "invalid or reused request id")
        self.seen_ids.add(request_id)
        return request_id

    def _call_tool(self, params):
        if not isinstance(params, dict) or set(params) != {"name", "arguments"}:
            raise McpError(-32602, "invalid tool call")
        name = params.get("name")
        arguments = params.get("arguments")
        if name not in TOOL_MAP or not isinstance(arguments, dict):
            raise McpError(-32602, "unknown tool or invalid arguments")
        if name == "kimiflow_status" and arguments:
            raise McpError(-32602, "status accepts no arguments")
        if name == "kimiflow_context" and set(arguments) - {"run", "phase"}:
            raise McpError(-32602, "context arguments invalid")
        if name == "kimiflow_scorecard" and set(arguments) - {"run"}:
            raise McpError(-32602, "scorecard arguments invalid")
        if name == "kimiflow_action":
            if self.host is None or self.session_id is None:
                return _tool_result({
                    "schema_version": 1,
                    "status": "error",
                    "error": {"code": "owner_identity_missing", "message": "owner identity is required for mutations"},
                }, True)
            bridge_params = dict(arguments)
            bridge_params["write"] = True
        else:
            bridge_params = arguments
        try:
            with _bound_identity(self.host, self.session_id):
                result = run_bridge.handle(self.root, {
                    "schema_version": 1,
                    "method": TOOL_MAP[name],
                    "params": bridge_params,
                })
            return _tool_result(result)
        except (run_bridge.BridgeError, active_run.ActiveError, OSError, ValueError) as exc:
            code = exc.code if isinstance(exc, run_bridge.BridgeError) else "bridge_error"
            message = exc.message if isinstance(exc, run_bridge.BridgeError) else str(exc)
            return _tool_result({
                "schema_version": 1,
                "status": "error",
                "error": {"code": code, "message": message},
            }, True)

    def handle(self, message):
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            raise McpError(-32600, "invalid JSON-RPC request")
        method = message["method"]
        is_notification = "id" not in message
        if is_notification:
            if method == "notifications/initialized" and self.initialized:
                self.ready = True
                return None
            if method == "notifications/cancelled":
                return None
            raise McpError(-32600, "unexpected notification")
        request_id = self._request_id(message)
        params = message.get("params", {})
        if method == "initialize":
            if self.initialized or not isinstance(params, dict) or set(params) - {"protocolVersion", "capabilities", "clientInfo"}:
                raise McpError(-32602, "invalid initialize request")
            if params.get("protocolVersion") != PROTOCOL_VERSION or not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                raise McpError(-32602, "unsupported protocol version", {"supported": [PROTOCOL_VERSION]})
            self.initialized = True
            return request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "kimiflow", "title": "Kimiflow", "version": _runtime_version(self.root)},
                "instructions": "Use read tools freely. Mutations preserve Kimiflow owner, cursor, CAS and replay gates.",
            }
        if method == "ping":
            return request_id, {}
        if not self.ready:
            raise McpError(-32002, "server is not initialized")
        if method == "tools/list":
            if not isinstance(params, dict) or set(params) - {"cursor"} or params.get("cursor") not in (None, ""):
                raise McpError(-32602, "pagination cursor unsupported")
            return request_id, {"tools": _tools()}
        if method == "tools/call":
            return request_id, self._call_tool(params)
        raise McpError(-32601, "method not found")


def _response(request_id, result=None, error=None):
    value = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        value["error"] = {"code": error.code, "message": error.message}
        if error.data is not None:
            value["error"]["data"] = error.data
    else:
        value["result"] = result
    return value


def serve(server, stdin=None, stdout=None):
    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout
    for raw in source:
        request_id = None
        try:
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            if len(raw) > MAX_MESSAGE_BYTES or b"\n" in raw.rstrip(b"\r\n"):
                raise McpError(-32700, "message too large or contains an embedded newline")
            message = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
            if isinstance(message, dict):
                request_id = message.get("id")
            handled = server.handle(message)
            if handled is None:
                continue
            request_id, result = handled
            response = _response(request_id, result=result)
        except McpError as exc:
            response = _response(request_id, error=exc)
        except (UnicodeError, ValueError, json.JSONDecodeError):
            response = _response(request_id, error=McpError(-32700, "parse error"))
        sink.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sink.flush()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kimiflow-mcp")
    parser.add_argument("--root", required=True)
    parser.add_argument("--host")
    parser.add_argument("--session-id")
    args = parser.parse_args(argv)
    try:
        return serve(Server(args.root, args.host, args.session_id))
    except (McpError, active_run.ActiveError, OSError, ValueError) as exc:
        print("kimiflow-mcp: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
