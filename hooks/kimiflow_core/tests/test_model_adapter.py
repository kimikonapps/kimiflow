import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest

from kimiflow_core import model_adapter, runner


class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "repo")
        os.mkdir(self.root)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "test@example.test"], check=True)
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("fixture\n")
        subprocess.run(["git", "-C", self.root, "add", "README.md"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "fixture"], check=True)

    def write_harness(self, capabilities=None):
        path = os.path.join(self.tmp.name, "agent-harness")
        caps = capabilities or {key: True for key in model_adapter.CAPABILITY_KEYS}
        source = """#!/usr/bin/env python3
import json, os, subprocess, sys
CAPS = %s
if sys.argv[1] == "capabilities":
    print(json.dumps({"schema_version":1,"name":"fixture-agent","host":"local","capabilities":CAPS}))
    raise SystemExit(0)
payload = json.loads(sys.stdin.readline())
root = payload["root"]
session = payload.get("session_id") or "local-session-123"
assert payload["host"] == "local" and payload["adapter"] == "fixture-agent"
assert os.environ["KIMIFLOW_HOST"] == "local"
if payload["action"] == "resume": assert os.environ["KIMIFLOW_SESSION_ID"] == session
run = os.path.join(root, ".kimiflow", "demo")
state = os.path.join(root, ".kimiflow", "session")
os.makedirs(run, exist_ok=True); os.makedirs(state, exist_ok=True)
if payload["action"] == "start":
    head = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
    open(os.path.join(run, "STATE.md"), "w").write("Flow schema: 4\\nMode: feature\\nScope: small\\nStatus: active\\nAffected files:\\n- result.txt\\nPhase 0: done\\n")
    json.dump({"schema_version":1,"status":"active","run":".kimiflow/demo","mode":"feature","scope":"small","host":"local","started_head":head,"last_checked_head":head,"owner":{"host":"local","session_id":session}}, open(os.path.join(state, "ACTIVE_RUN.json"), "w"))
    print(json.dumps({"type":"session.started","session_id":session}))
else:
    open(os.path.join(root, "result.txt"), "w").write("implemented\\n")
    subprocess.run(["git", "-C", root, "status", "--short"], check=True, stdout=subprocess.DEVNULL)
    os.unlink(os.path.join(state, "ACTIVE_RUN.json"))
    json.dump({"schema_version":1,"outcome":"done"}, open(os.path.join(run, "SESSION-OUTCOME.json"), "w"))
print(json.dumps({"type":"turn.completed","usage":{"model_calls":1,"tool_calls":2,"input_tokens":10,"output_tokens":5}}))
""" % repr(caps)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return path

    def test_command_adapter_runs_same_lifecycle_and_normalizes_usage(self):
        adapter = model_adapter.CommandAgentAdapter(self.write_harness(), model="qwen-local")
        result = runner.run_task(self.root, "build the fixture", adapter=adapter)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["host"], "local")
        self.assertEqual(result["adapter"], "fixture-agent")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "result.txt")))
        self.assertEqual(result["usage"], {
            "status": "available", "model_calls": 2, "tool_calls": 4,
            "input_tokens": 20, "output_tokens": 10,
        })

    def test_command_adapter_rejects_missing_tool_capability(self):
        caps = {key: True for key in model_adapter.CAPABILITY_KEYS}
        caps["tests"] = False
        adapter = model_adapter.CommandAgentAdapter(self.write_harness(caps))
        with self.assertRaisesRegex(model_adapter.AdapterError, "tests"):
            adapter.info()

    def test_command_adapter_requires_one_terminal_completion_event(self):
        path = self.write_harness()
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        source = source.replace(
            'print(json.dumps({"type":"turn.completed","usage":{"model_calls":1,"tool_calls":2,"input_tokens":10,"output_tokens":5}}))',
            'print(json.dumps({"type":"message","text":"no terminal event"}))',
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        adapter = model_adapter.CommandAgentAdapter(path)
        result = adapter.start(self.root, "task", lambda _: None)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.error_code, "missing_turn_completed")

    def test_command_adapter_accepts_absent_usage_as_unavailable(self):
        path = self.write_harness()
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        source = source.replace(
            'print(json.dumps({"type":"turn.completed","usage":{"model_calls":1,"tool_calls":2,"input_tokens":10,"output_tokens":5}}))',
            'print(json.dumps({"type":"turn.completed"}))',
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        result = model_adapter.CommandAgentAdapter(path).start(self.root, "task", lambda _: None)
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(result.usage)

    def test_command_adapter_rejects_malformed_usage(self):
        path = self.write_harness()
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        source = source.replace(
            '"model_calls":1,"tool_calls":2,"input_tokens":10,"output_tokens":5',
            '"model_calls":"invalid","tool_calls":2,"input_tokens":10,"output_tokens":5',
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        result = model_adapter.CommandAgentAdapter(path).start(self.root, "task", lambda _: None)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.error_code, "invalid_usage")


if __name__ == "__main__":
    unittest.main()
