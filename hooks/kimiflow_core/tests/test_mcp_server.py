import io
import json
import os
import tempfile
import unittest
from unittest import mock

from kimiflow_core import mcp_server, run_bridge


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self, server):
        response = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        })
        self.assertEqual(response[1]["protocolVersion"], "2025-11-25")
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_lifecycle_and_four_coarse_tools(self):
        server = mcp_server.Server(self.root)
        self.initialize(server)
        _, result = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual([tool["name"] for tool in result["tools"]], [
            "kimiflow_status", "kimiflow_context", "kimiflow_scorecard", "kimiflow_action",
        ])
        self.assertFalse(result["tools"][0]["annotations"]["destructiveHint"])
        self.assertTrue(result["tools"][3]["annotations"]["destructiveHint"])

    def test_missing_identity_keeps_reads_and_fails_mutation_closed(self):
        server = mcp_server.Server(self.root)
        self.initialize(server)
        expected = {"schema_version": 1, "status": "ok"}
        with mock.patch.object(run_bridge, "handle", return_value=expected) as handle:
            _, read = server.handle({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "kimiflow_status", "arguments": {}},
            })
        self.assertFalse(read["isError"])
        self.assertEqual(read["structuredContent"], expected)
        handle.assert_called_once()
        _, denied = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "kimiflow_action",
                "arguments": {
                    "action_id": "action_1234567890abcdef12345678",
                    "cursor": {"sequence": 0, "readiness_fingerprint": "sha256:" + "a" * 64},
                    "operation": "append-item",
                    "arguments": {"title": "demo"},
                },
            },
        })
        self.assertTrue(denied["isError"])
        self.assertEqual(denied["structuredContent"]["error"]["code"], "owner_identity_missing")

    def test_mcp_action_preserves_run_bridge_authority_and_replay_guards(self):
        server = mcp_server.Server(self.root, "codex", "session-12345678")
        self.initialize(server)
        with mock.patch.object(run_bridge, "handle", side_effect=run_bridge.BridgeError("stale_cursor")) as handle:
            _, result = server.handle({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {
                    "name": "kimiflow_action",
                    "arguments": {
                        "action_id": "action_1234567890abcdef12345678",
                        "cursor": {"sequence": 0, "readiness_fingerprint": "sha256:" + "a" * 64},
                        "operation": "append-item",
                        "arguments": {"title": "demo"},
                    },
                },
            })
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["code"], "stale_cursor")
        request = handle.call_args.args[1]
        self.assertEqual(request["method"], "run/mutate")
        self.assertTrue(request["params"]["write"])
        self.assertNotIn("root", request["params"])

    def test_stdio_uses_newline_delimited_json_rpc_only(self):
        server = mcp_server.Server(self.root)
        payload = "\n".join(json.dumps(value) for value in [
            {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "x", "version": "1"}},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]) + "\n"
        output = io.StringIO()
        self.assertEqual(mcp_server.serve(server, io.BytesIO(payload.encode()), output), 0)
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([row["id"] for row in rows], [1, 2])
        self.assertEqual(rows[1]["result"], {})


if __name__ == "__main__":
    unittest.main()
