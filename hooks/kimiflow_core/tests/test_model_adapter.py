import json
import os
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

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

    def write_harness(self, capabilities=None, features=None, events=None):
        path = os.path.join(self.tmp.name, "agent-harness")
        caps = capabilities or {key: True for key in model_adapter.CAPABILITY_KEYS}
        source = """#!/usr/bin/env python3
import json, os, subprocess, sys
CAPS = %s
FEATURES = %s
EVENTS = %s
if sys.argv[1] == "capabilities":
    info = {"schema_version":1,"name":"fixture-agent","host":"local","capabilities":CAPS}
    if FEATURES is not None: info["features"] = FEATURES
    print(json.dumps(info))
    raise SystemExit(0)
payload = json.loads(sys.stdin.readline())
root = payload["root"]
session = payload.get("session_id") or "local-session-123"
assert payload["host"] == "local" and payload["adapter"] == "fixture-agent"
assert os.environ["KIMIFLOW_HOST"] == "local"
if payload["action"] == "resume": assert os.environ["KIMIFLOW_SESSION_ID"] == session
if FEATURES is None:
    assert set(payload) == {"schema_version","action","root","session_id","host","adapter","prompt","model","required_capabilities"}
payload_log = os.environ.get("PAYLOAD_LOG")
if payload_log:
    with open(payload_log, "a", encoding="utf-8") as handle: handle.write(json.dumps(payload) + "\\n")
run = os.path.join(root, ".kimiflow", "demo")
state = os.path.join(root, ".kimiflow", "session")
os.makedirs(run, exist_ok=True); os.makedirs(state, exist_ok=True)
if payload["action"] == "start":
    head = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip()
    open(os.path.join(run, "STATE.md"), "w").write("Flow schema: 4\\nMode: feature\\nScope: small\\nStatus: active\\nAffected files:\\n- result.txt\\nPhase 0: done\\n")
    active = {"schema_version":1,"status":"active","run":".kimiflow/demo","mode":"feature","scope":"small","host":"local","started_head":head,"last_checked_head":head,"owner":{"host":"local","session_id":session}}
    if os.environ.get("HARNESS_WAIT") == "1": active.update({"awaiting_user":True,"awaiting_kind":"scope-risk"})
    json.dump(active, open(os.path.join(state, "ACTIVE_RUN.json"), "w"))
    print(json.dumps({"type":"session.started","session_id":session}))
else:
    open(os.path.join(root, "result.txt"), "w").write("implemented\\n")
    subprocess.run(["git", "-C", root, "status", "--short"], check=True, stdout=subprocess.DEVNULL)
    os.unlink(os.path.join(state, "ACTIVE_RUN.json"))
    json.dump({"schema_version":1,"outcome":"done"}, open(os.path.join(run, "SESSION-OUTCOME.json"), "w"))
for event in EVENTS: print(json.dumps(event))
print(json.dumps({"type":"turn.completed","usage":{"model_calls":1,"tool_calls":2,"input_tokens":10,"output_tokens":5}}))
""" % (repr(caps), repr(features), repr(events or []))
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

    def test_legacy_command_adapter_contract_stays_unchanged(self):
        payload_log = os.path.join(self.tmp.name, "legacy-payloads.jsonl")
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(), model="legacy-model", environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )
        result = runner.run_task(self.root, "legacy request", adapter=adapter)
        self.assertEqual(result["status"], "done")
        payloads = [json.loads(line) for line in Path(payload_log).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(payloads), 2)
        expected = {
            "schema_version", "action", "root", "session_id", "host", "adapter",
            "prompt", "model", "required_capabilities",
        }
        self.assertTrue(all(set(payload) == expected for payload in payloads))
        self.assertIn("$kimiflow", payloads[0]["prompt"])
        self.assertNotIn("adapter_contract", json.dumps(result))

    def test_feature_capable_adapter_start_and_resume_preserve_workflow_and_model_roles(self):
        features = {key: True for key in model_adapter.FEATURE_KEYS}
        payload_log = os.path.join(self.tmp.name, "feature-payloads.jsonl")
        public_events = []
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features=features,
                events=[
                    {"type": "progress", "current": 1, "total": 2, "label": "Planning", "private": "drop"},
                    {"type": "tool.completed", "tool": "tests", "status": "passed", "duration_ms": 4, "command": "drop"},
                ],
            ),
            model="fallback-local",
            model_roles={"top": "qwen-local", "balanced": "qwen-coder-local"},
            required_features=model_adapter.FEATURE_KEYS,
            event_sink=public_events.append,
            environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )
        result = runner.run_task(self.root, "feature request", adapter=adapter)
        self.assertEqual(result["status"], "done")
        schema_path = Path(__file__).resolve().parents[3] / "references" / "adapter-protocol-v1.schema.json"
        run_result = json.loads(schema_path.read_text(encoding="utf-8"))["$defs"]["runResult"]
        stream_result = {"schema_version": 1, "type": "run.result", "result": result}
        self.assertTrue(set(run_result["required"]).issubset(stream_result))
        self.assertTrue(set(run_result["properties"]["result"]["required"]).issubset(result))
        payloads = [json.loads(line) for line in Path(payload_log).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([payload["action"] for payload in payloads], ["start", "resume"])
        for payload in payloads:
            self.assertEqual(payload["model_routing"]["roles"], {
                "top": "qwen-local", "balanced": "qwen-coder-local",
            })
            context = payload["workflow_context"]
            self.assertEqual(context["name"], "kimiflow")
            plugin_root = os.path.realpath(context["plugin_root"])
            for key in ("skill", "phase_manifest", "run_bridge"):
                target = os.path.realpath(os.path.join(plugin_root, context[key]))
                self.assertEqual(os.path.commonpath((plugin_root, target)), plugin_root)
                self.assertTrue(os.path.isfile(target))
        self.assertNotIn("$kimiflow", payloads[0]["prompt"])
        progress = next(event for event in public_events if event["type"] == "progress")
        self.assertNotIn("private", progress)
        tool = next(event for event in public_events if event["type"] == "tool.completed")
        self.assertNotIn("command", tool)
        receipt = json.loads(Path(runner.receipt_path(self.root)).read_text(encoding="utf-8"))
        self.assertRegex(receipt["adapter_contract"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("qwen-local", json.dumps(receipt))
        self.assertNotIn("Planning", json.dumps(receipt))

    def test_resume_rejects_adapter_contract_drift(self):
        features = {key: True for key in model_adapter.FEATURE_KEYS}
        payload_log = os.path.join(self.tmp.name, "drift-payloads.jsonl")
        environ = {
            "PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log, "HARNESS_WAIT": "1",
        }
        harness = self.write_harness(features=features)
        first = model_adapter.CommandAgentAdapter(
            harness, model_roles={"top": "qwen-local"},
            required_features=model_adapter.FEATURE_KEYS, environ=environ,
        )
        waiting = runner.run_task(self.root, "wait", adapter=first)
        self.assertEqual(waiting["status"], "awaiting_user")
        drifted = model_adapter.CommandAgentAdapter(
            harness, required_features=model_adapter.FEATURE_KEYS, environ=environ,
        )
        with self.assertRaises(runner.RunnerError) as context:
            runner.resume_task(self.root, message="continue", adapter=drifted)
        self.assertEqual(context.exception.status, "adapter_mismatch")
        self.assertEqual(len(Path(payload_log).read_text(encoding="utf-8").splitlines()), 1)

        second_harness = os.path.join(self.tmp.name, "agent-harness-copy")
        Path(second_harness).write_bytes(Path(harness).read_bytes())
        os.chmod(second_harness, 0o700)
        different_command = model_adapter.CommandAgentAdapter(
            second_harness, model_roles={"top": "qwen-local"},
            required_features=model_adapter.FEATURE_KEYS, environ=environ,
        )
        with self.assertRaises(runner.RunnerError) as command_context:
            runner.resume_task(self.root, message="continue", adapter=different_command)
        self.assertEqual(command_context.exception.status, "adapter_mismatch")
        self.assertEqual(len(Path(payload_log).read_text(encoding="utf-8").splitlines()), 1)

    def test_required_adapter_feature_fails_before_start(self):
        features = {key: True for key in model_adapter.FEATURE_KEYS}
        features["root_confinement"] = False
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(features=features), required_features=("root_confinement",),
        )
        with self.assertRaisesRegex(model_adapter.AdapterError, "root_confinement"):
            adapter.info()
        with self.assertRaisesRegex(model_adapter.AdapterError, "root_confinement"):
            adapter.info()

    def test_command_adapter_rejects_invalid_model_and_capability_encoding(self):
        with self.assertRaisesRegex(model_adapter.AdapterError, "model_invalid"):
            model_adapter.CommandAgentAdapter(self.write_harness(), model="")
        with self.assertRaisesRegex(model_adapter.AdapterError, "model_invalid"):
            model_adapter.CommandAgentAdapter(self.write_harness(), model="x" * 129)

        path = os.path.join(self.tmp.name, "invalid-encoding-harness")
        Path(path).write_bytes(
            b"#!/usr/bin/env python3\nimport sys\nsys.stdout.buffer.write(b'\\xff')\n"
        )
        os.chmod(path, 0o755)
        adapter = model_adapter.CommandAgentAdapter(path)
        with self.assertRaisesRegex(model_adapter.AdapterError, "adapter_info_invalid"):
            adapter.info()

    def test_oversized_or_unrecognized_structured_event_fails_closed(self):
        features = {"structured_events": True}
        unknown = model_adapter.CommandAgentAdapter(
            self.write_harness(features=features, events=[{"type": "private.reasoning", "text": "no"}])
        ).start(self.root, "task", lambda _: None)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertEqual(unknown.error_code, "invalid_event")

        path = self.write_harness(features=features)
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        source = source.replace(
            'for event in EVENTS: print(json.dumps(event))',
            'print(json.dumps({"type":"message","text":"x" * (300 * 1024)}))',
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        oversized = model_adapter.CommandAgentAdapter(path).start(self.root, "task", lambda _: None)
        self.assertNotEqual(oversized.returncode, 0)
        self.assertEqual(oversized.error_code, "event_too_large")

    def test_failed_process_does_not_publish_successful_completion_event(self):
        path = self.write_harness(features={"structured_events": True})
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        source += "\nraise SystemExit(9)\n"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        events = []
        result = model_adapter.CommandAgentAdapter(path, event_sink=events.append).start(
            self.root, "task", lambda _: None,
        )
        self.assertEqual(result.returncode, 9)
        self.assertNotIn("turn.completed", [event["type"] for event in events])

    def test_event_stream_limits_stop_and_reap_the_adapter(self):
        path = self.write_harness(
            features={"structured_events": True},
            events=[{"type": "progress", "current": value} for value in range(3)],
        )
        original = model_adapter.MAX_EVENTS_PER_TURN
        model_adapter.MAX_EVENTS_PER_TURN = 2
        self.addCleanup(setattr, model_adapter, "MAX_EVENTS_PER_TURN", original)
        result = model_adapter.CommandAgentAdapter(path, event_sink=lambda _: None).start(
            self.root, "task", lambda _: None,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.error_code, "event_stream_too_large")

    def test_silent_turn_timeout_is_bounded_and_reaps_the_adapter(self):
        path = self.write_harness(features={"structured_events": True})
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        source = source.replace(
            "import json, os, subprocess, sys",
            "import json, os, subprocess, sys, time",
        ).replace(
            "for event in EVENTS: print(json.dumps(event))",
            'print(json.dumps({"type":"progress","current":1})); sys.stdout.flush(); time.sleep(30)',
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        started = time.monotonic()
        result = model_adapter.CommandAgentAdapter(
            path,
            event_sink=lambda _: None,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS": "1",
            },
        ).start(self.root, "task", lambda _: None)
        self.assertLess(time.monotonic() - started, 5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.error_code, "turn_timeout")

    def test_turn_timeout_covers_blocked_stdin_write(self):
        path = os.path.join(self.tmp.name, "blocked-stdin-harness")
        Path(path).write_text(
            """#!/usr/bin/env python3
import json, sys, time
if sys.argv[1] == "capabilities":
    print(json.dumps({"schema_version":1,"name":"blocked-stdin","host":"local","capabilities":{"files":True,"shell":True,"tests":True,"resume":True,"gates":True}}))
    raise SystemExit(0)
time.sleep(30)
""",
            encoding="utf-8",
        )
        os.chmod(path, 0o700)
        started = time.monotonic()
        result = model_adapter.CommandAgentAdapter(
            path,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS": "1",
            },
        ).start(self.root, "x" * (2 * 1024 * 1024), lambda _: None)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result.error_code, "turn_timeout")

    def test_turn_timeout_kills_descendants_inheriting_stdout(self):
        path = os.path.join(self.tmp.name, "descendant-harness")
        pid_path = os.path.join(self.tmp.name, "descendant.pid")
        Path(path).write_text(
            """#!/usr/bin/env python3
import json, os, subprocess, sys, time
if sys.argv[1] == "capabilities":
    print(json.dumps({"schema_version":1,"name":"descendant","host":"local","capabilities":{"files":True,"shell":True,"tests":True,"resume":True,"gates":True}}))
    raise SystemExit(0)
json.loads(sys.stdin.readline())
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
open(os.environ["DESCENDANT_PID"], "w").write(str(child.pid))
print(json.dumps({"type":"session.started","session_id":"descendant-session"}), flush=True)
time.sleep(30)
""",
            encoding="utf-8",
        )
        os.chmod(path, 0o700)
        started = time.monotonic()
        result = model_adapter.CommandAgentAdapter(
            path,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "DESCENDANT_PID": pid_path,
                "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS": "1",
            },
        ).start(self.root, "task", lambda _: None)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result.error_code, "turn_timeout")
        child_pid = int(Path(pid_path).read_text(encoding="utf-8"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("adapter descendant survived the turn timeout")

    def test_closed_event_sink_records_transport_error_without_retry(self):
        def closed_sink(_event):
            raise BrokenPipeError()

        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(features={"structured_events": True}), event_sink=closed_sink,
        )
        with self.assertRaises(runner.RunnerError) as context:
            runner.run_task(self.root, "task", adapter=adapter)
        self.assertEqual(context.exception.status, "transport_error")
        receipt = json.loads(Path(runner.receipt_path(self.root)).read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "transport_error")
        self.assertEqual(receipt["error_code"], "event_sink_failed")
        self.assertEqual(receipt["turns"], 1)

    def test_adapter_protocol_schema_matches_runtime_contract(self):
        root = Path(__file__).resolve().parents[3]
        schema = json.loads((root / "references" / "adapter-protocol-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        definitions = schema["$defs"]
        self.assertTrue({"adapterInfo", "turnRequest", "event"}.issubset(definitions))
        self.assertEqual(
            set(definitions["capabilities"]["properties"]), set(model_adapter.CAPABILITY_KEYS),
        )
        self.assertEqual(
            set(definitions["features"]["properties"]), set(model_adapter.FEATURE_KEYS),
        )
        self.assertEqual(
            set(definitions["modelRouting"]["properties"]["roles"]["properties"]),
            set(model_adapter.MODEL_ROLE_KEYS),
        )
        run_result = definitions["runResult"]
        self.assertEqual(run_result["properties"]["type"]["const"], "run.result")
        self.assertEqual(set(run_result["required"]), {"schema_version", "type", "result"})
        self.assertEqual(run_result["properties"]["schema_version"]["const"], 1)
        self.assertIn({"$ref": "#/$defs/runResult"}, schema["oneOf"])
        self.assertNotIn({"$ref": "#/$defs/runResult"}, definitions["event"]["oneOf"])
        events = definitions["event"]["oneOf"]
        by_type = {
            event["properties"]["type"].get("const"): event
            for event in events
            if isinstance(event.get("properties", {}).get("type"), dict)
            and "const" in event["properties"]["type"]
        }
        self.assertEqual(
            by_type["progress"]["properties"]["current"]["maximum"],
            model_adapter.MAX_PROGRESS_VALUE,
        )
        self.assertEqual(
            by_type["tool.completed"]["properties"]["duration_ms"]["maximum"],
            model_adapter.MAX_DURATION_MS,
        )
        self.assertEqual(
            by_type["context.compacted"]["properties"]["before_tokens"]["maximum"],
            model_adapter.MAX_TOKEN_COUNT,
        )

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
