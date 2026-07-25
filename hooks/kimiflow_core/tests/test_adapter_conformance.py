import json
import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from kimiflow_core import adapter_conformance


class AdapterConformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = os.path.join(self.tmp.name, "user-project")
        os.mkdir(self.project)
        Path(self.project, "owned.txt").write_text("unchanged\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def harness(self, escape=False, claims_only=False, featureful=True):
        path = os.path.join(self.tmp.name, "fixture-adapter")
        profile = {
            "schema_version": 1,
            "name": "fixture-agent",
            "host": "local",
            "capabilities": {key: True for key in ("files", "shell", "tests", "resume", "gates")},
            "features": (
                {"structured_events": True, "root_confinement": True}
                if featureful else {}
            ),
        }
        script = """#!/usr/bin/env python3
import json, os, re, shlex, subprocess, sys
INFO = %r
if sys.argv[1:] == ['capabilities', '--json']:
    print(json.dumps(INFO)); raise SystemExit(0)
payload = json.loads(sys.stdin.readline())
probe = json.loads(re.search(r'Probe: (\\{.*\\})$', payload['prompt']).group(1))
root = payload['root']
name = 'start.txt' if probe['operation'] == 'start' else 'resume.txt'
with open(os.path.join(root, name), 'w', encoding='utf-8') as handle:
    handle.write(probe['marker'] + '\\n')
if probe['operation'] == 'start' and not %r:
    for capability in ('shell', 'tests', 'gates'):
        subprocess.run(
            shlex.split(probe['probes'][capability]), cwd=root,
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
if %r:
    with open(os.path.join(os.path.dirname(root), 'outside-canary'), 'w') as handle:
        handle.write('escaped')
session = payload.get('session_id') or 'session-fixture-1234'
print(json.dumps({'type':'session.started','session_id':session}))
if %r:
    print(json.dumps({'type':'progress','current':1,'total':1}))
    print(json.dumps({'type':'tool.started','tool':'shell'}))
    print(json.dumps({'type':'tool.completed','tool':'shell','status':'passed'}))
    print(json.dumps({'type':'test.completed','name':'fixture','status':'passed'}))
print(json.dumps({'type':'turn.completed','usage':{'model_calls':1,'tool_calls':1,'input_tokens':10,'output_tokens':2}}))
""" % (profile, claims_only, escape, featureful)
        Path(path).write_text(textwrap.dedent(script), encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return path

    def test_adapter_conformance_rejects_false_capability_and_preserves_root(self):
        before = Path(self.project, "owned.txt").read_bytes()
        result = adapter_conformance.run(self.harness(escape=True), self.project)
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual(result["checks"]["root_confinement"], "failed")
        self.assertEqual(result["checks"]["project_preservation"], "passed")
        self.assertEqual(Path(self.project, "owned.txt").read_bytes(), before)
        self.assertNotIn(self.project, json.dumps(result))

    def test_conformant_adapter_gets_privacy_bounded_receipt(self):
        result = adapter_conformance.run(self.harness(), self.project)
        self.assertEqual(result["status"], "compatible")
        self.assertTrue(all(value != "failed" for value in result["checks"].values()))
        self.assertRegex(result["contract_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(
            result["assurance"],
            {
                "mode": "cooperative_black_box",
                "host_trust_required": True,
                "os_process_attestation": False,
            },
        )
        self.assertNotIn("prompt", json.dumps(result))

    def test_valid_baseline_adapter_without_optional_features_is_supported(self):
        executable = self.harness(featureful=False)
        result = adapter_conformance.run(
            executable, self.project, model="model-a"
        )
        self.assertEqual(result["status"], "compatible")
        self.assertRegex(result["contract_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(result["checks"]["structured_events"], "not_claimed")
        self.assertEqual(result["checks"]["root_confinement"], "not_claimed")
        other_model = adapter_conformance.run(
            executable, self.project, model="model-b"
        )
        self.assertNotEqual(
            result["contract_fingerprint"],
            other_model["contract_fingerprint"],
        )

    def test_claimed_tools_without_behavioral_evidence_are_rejected(self):
        result = adapter_conformance.run(self.harness(claims_only=True), self.project)
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual(
            result["failed_checks"],
            ["gates", "shell", "tests"],
        )


if __name__ == "__main__":
    unittest.main()
