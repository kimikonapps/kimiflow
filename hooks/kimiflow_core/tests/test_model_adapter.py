import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from kimiflow_core import adaptive_control, model_adapter, runner


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

    def execution_profile(self):
        return {
            "schema_version": 1,
            "model_fingerprint": "sha256:" + "a" * 64,
            "max_input_tokens": 1_000_000,
            "max_output_tokens": 128_000,
            "execution_variants": [
                {"id": "budget8192", "default": True, "cost_rank": 20, "depth_rank": 40},
                {"id": "budget32768", "default": False, "cost_rank": 60, "depth_rank": 80},
            ],
            "controls": {
                "thinking": "selectable",
                "task_budget": True,
                "prompt_cache": True,
                "compaction": True,
                "structured_failures": True,
            },
        }

    def write_harness(
        self, capabilities=None, features=None, events=None, completion=None,
        execution_profile=None,
    ):
        path = os.path.join(self.tmp.name, "agent-harness")
        caps = capabilities or {key: True for key in model_adapter.CAPABILITY_KEYS}
        if (
            execution_profile is None
            and isinstance(features, dict)
            and features.get("adaptive_execution_profiles") is True
        ):
            execution_profile = self.execution_profile()
        completion_event = completion if completion is not None else {
            "type": "turn.completed",
            "usage": {
                "model_calls": 1,
                "tool_calls": 2,
                "input_tokens": 10,
                "output_tokens": 5,
            },
        }
        source = """#!/usr/bin/env python3
import json, os, subprocess, sys
CAPS = %s
FEATURES = %s
EXECUTION_PROFILE = %s
EVENTS = %s
COMPLETION = %s
if sys.argv[1] == "capabilities":
    info = {"schema_version":1,"name":"fixture-agent","host":"local","capabilities":CAPS}
    if FEATURES is not None: info["features"] = FEATURES
    if EXECUTION_PROFILE is not None: info["execution_profile"] = EXECUTION_PROFILE
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
completion = json.loads(json.dumps(COMPLETION))
if isinstance(completion, dict) and isinstance(completion.get("usage_v2"), dict):
    completion["usage_v2"]["turn_id"] = "turn_" + payload["action"]
    completion["usage_v2"]["session_id"] = session
print(json.dumps(completion))
""" % (
            repr(caps),
            repr(features),
            repr(execution_profile),
            repr(events or []),
            repr(completion_event),
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(source)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return path

    def usage_v2(self, turn_id="turn_one", **updates):
        value = {
            "schema_version": 2,
            "turn_id": turn_id,
            "session_id": "local-session-123",
            "model_fingerprint": "sha256:" + "a" * 64,
            "execution_variant": "budget8192",
            "model_calls": 1,
            "tool_calls": 2,
            "uncached_input_tokens": 60,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 10,
            "logical_input_tokens": 100,
            "output_tokens": 20,
            "active_context_tokens": 400,
            "peak_context_tokens": 500,
            "max_input_tokens": 1_000_000,
        }
        value.update(updates)
        return value

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

    def test_command_adapter_process_isolation_is_host_neutral(self):
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(),
            environ={
                "PATH": os.environ.get("PATH", ""),
                "KIMIFLOW_PI_BRIDGE_BINDING": "legacy-unused-value",
            },
        )
        original_popen = subprocess.Popen
        with mock.patch.object(
            model_adapter.subprocess,
            "Popen",
            wraps=original_popen,
        ) as popen:
            result = adapter.start(self.root, "isolated", lambda _session: None)

        self.assertEqual(result.returncode, 0)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

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

    def test_execution_profile_is_provider_neutral_and_pins_host_default(self):
        payload_log = os.path.join(self.tmp.name, "profile-payload.jsonl")
        profile = self.execution_profile()
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features={"adaptive_execution_profiles": True},
                execution_profile=profile,
            ),
            required_features=("adaptive_execution_profiles",),
            environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )

        result = adapter.start(self.root, "profile", lambda _session: None)

        self.assertEqual(result.returncode, 0)
        payload = json.loads(Path(payload_log).read_text(encoding="utf-8"))
        selection = payload["execution_profile"]
        self.assertEqual(selection["execution_variant"], "budget8192")
        self.assertEqual(selection["max_input_tokens"], 1_000_000)
        self.assertRegex(selection["profile_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("opus", json.dumps(payload).lower())
        self.assertNotIn("xhigh", json.dumps(payload).lower())

    def test_usage_v2_enforces_exact_accounting_and_aggregates_turn_deltas(self):
        first = model_adapter.normalize_usage_v2(self.usage_v2("turn_one"))
        second = model_adapter.normalize_usage_v2(self.usage_v2(
            "turn_two",
            uncached_input_tokens=20,
            cache_read_input_tokens=5,
            cache_creation_input_tokens=0,
            logical_input_tokens=25,
            active_context_tokens=450,
            peak_context_tokens=600,
        ))

        aggregate = runner._merge_usage_v2(None, first, initialize=True)
        aggregate = runner._merge_usage_v2(aggregate, second)

        self.assertEqual(aggregate["turns"], 2)
        self.assertEqual(aggregate["logical_input_tokens"], 125)
        self.assertEqual(aggregate["cache_read_input_tokens"], 35)
        self.assertEqual(aggregate["active_context_tokens"], 450)
        self.assertEqual(aggregate["peak_context_tokens"], 600)
        with self.assertRaisesRegex(model_adapter.AdapterError, "logical_input_mismatch"):
            model_adapter.normalize_usage_v2(self.usage_v2(logical_input_tokens=99))
        with self.assertRaisesRegex(model_adapter.AdapterError, "context_bounds"):
            model_adapter.normalize_usage_v2(self.usage_v2(
                active_context_tokens=600, peak_context_tokens=500,
            ))

    def test_usage_v2_is_bound_to_negotiated_profile_and_session(self):
        completion = {"type": "turn.completed", "usage_v2": self.usage_v2()}
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features={"adaptive_execution_profiles": True},
                completion=completion,
            ),
        )
        self.assertEqual(adapter.start(self.root, "usage", lambda _session: None).returncode, 0)

        mismatch = self.usage_v2(model_fingerprint="sha256:" + "b" * 64)
        rejected = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features={"adaptive_execution_profiles": True},
                completion={"type": "turn.completed", "usage_v2": mismatch},
            ),
        ).start(self.root, "usage", lambda _session: None)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(rejected.error_code, "usage_v2_profile_mismatch")
        self.assertIsNone(rejected.usage)
        self.assertIsNone(rejected.usage_v2)

    def test_runner_persists_exact_usage_v2_session_totals(self):
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features={"adaptive_execution_profiles": True},
                completion={"type": "turn.completed", "usage_v2": self.usage_v2()},
            ),
        )

        result = runner.run_task(self.root, "usage-v2 run", adapter=adapter)

        self.assertEqual(result["status"], "done")
        self.assertEqual(result["usage_v2"]["status"], "available")
        self.assertEqual(result["usage_v2"]["turns"], 2)
        self.assertEqual(result["usage_v2"]["turn_ids"], ["turn_start", "turn_resume"])
        self.assertEqual(result["usage_v2"]["logical_input_tokens"], 200)
        self.assertEqual(result["usage_v2"]["cache_read_input_tokens"], 60)
        self.assertEqual(result["usage_v2"]["peak_context_tokens"], 500)

    def test_execution_profile_requires_one_unique_default(self):
        duplicate_default = self.execution_profile()
        duplicate_default["execution_variants"][1]["default"] = True
        with self.assertRaisesRegex(model_adapter.AdapterError, "execution_profile_invalid"):
            model_adapter.validate_info({
                "schema_version": 1,
                "name": "profiled",
                "host": "local",
                "capabilities": {key: True for key in model_adapter.CAPABILITY_KEYS},
                "features": {"adaptive_execution_profiles": True},
                "execution_profile": duplicate_default,
            })

    def test_execution_profile_is_negotiated_cache_stable_and_evidence_routed(self):
        payload_log = os.path.join(self.tmp.name, "stable-profile-payloads.jsonl")
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features={"adaptive_execution_profiles": True},
                execution_profile=self.execution_profile(),
            ),
            environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )
        started = adapter.start(self.root, "start", lambda _session: None)
        resumed = adapter.resume(self.root, started.session_id, "resume", lambda _session: None)
        self.assertEqual((started.returncode, resumed.returncode), (0, 0))
        payloads = [json.loads(line) for line in Path(payload_log).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(payloads[0]["execution_profile"], payloads[1]["execution_profile"])

        selection = payloads[0]["execution_profile"]
        key = {
            "model_fingerprint": selection["model_fingerprint"],
            "execution_variant": selection["execution_variant"],
            "role": "balanced",
            "task_class": "routine-code",
            "runtime_fingerprint": "sha256:" + "b" * 64,
            "policy_fingerprint": "sha256:" + "c" * 64,
            "prompt_gate_fingerprint": "sha256:" + "d" * 64,
        }
        for index in range(5):
            adaptive_control.record_review_outcome(
                self.root, "sample_%024x" % (700 + index), **key,
                quality_passed=True,
            )
        modes = []
        for index in range(10):
            route = adaptive_control.resolve_review_mode(self.root, **key)
            modes.append(route["review_mode"])
            adaptive_control.record_review_outcome(
                self.root, "sample_%024x" % (800 + index), **key,
                quality_passed=True,
            )
        self.assertIn("embedded", modes)
        self.assertEqual(modes.count("single-independent"), 1)
        drifted = dict(key, prompt_gate_fingerprint="sha256:" + "e" * 64)
        self.assertEqual(
            adaptive_control.resolve_review_mode(self.root, **drifted)["review_mode"],
            "single-independent",
        )

    def test_verified_lower_cost_execution_variant_is_selected_and_session_stable(self):
        profile = self.execution_profile()
        profile["execution_variants"][1]["cost_rank"] = 10
        profile["execution_variants"][1]["depth_rank"] = 30
        payload_log = os.path.join(self.tmp.name, "variant-payloads.jsonl")
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features={"adaptive_execution_profiles": True},
                execution_profile=profile,
            ),
            environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )
        binding = {
            "role": "implementation",
            "task_class": "routine-code",
            "runtime_fingerprint": model_adapter.runtime_fingerprint(),
            "policy_fingerprint": model_adapter.execution_profile_fingerprint(profile),
            "prompt_gate_fingerprint": adapter.contract_fingerprint(),
        }
        for index in range(5):
            for variant, tokens in (("budget8192", 1000), ("budget32768", 500)):
                adaptive_control.record_execution_variant_outcome(
                    self.root,
                    "sample_%024x" % (1200 + index * 2 + (variant == "budget32768")),
                    profile["model_fingerprint"],
                    variant,
                    **binding,
                    quality_passed=True,
                    verification_passed=True,
                    logical_input_tokens=tokens,
                    output_tokens=100,
                )
        started = adapter.start(self.root, "start", lambda _session: None)
        self.assertEqual(started.returncode, 0)
        adaptive_control.record_execution_variant_outcome(
            self.root,
            "sample_%024x" % 1300,
            profile["model_fingerprint"],
            "budget32768",
            **binding,
            quality_passed=False,
            verification_passed=False,
            high_findings=1,
            logical_input_tokens=500,
            output_tokens=100,
        )
        resumed = adapter.resume(
            self.root, started.session_id, "resume", lambda _session: None
        )
        self.assertEqual(resumed.returncode, 0)
        payloads = [
            json.loads(line)
            for line in Path(payload_log).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [row["execution_profile"]["execution_variant"] for row in payloads],
            ["budget32768", "budget32768"],
        )

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
                "top": "qwen-local", "balanced": "qwen-local",
            })
            self.assertEqual(payload["model_routing"]["candidates"], {
                "top": "qwen-local", "balanced": "qwen-coder-local",
            })
            self.assertEqual(payload["model_routing"]["policy"]["status"], "resolved")
            self.assertEqual(payload["model_routing"]["policy"]["decisions"]["balanced"]["reason"], "top_default")
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

    def test_runtime_model_policy_selects_and_revokes_the_emitted_candidate(self):
        features = {"model_roles": True, "adaptive_model_routes": True}
        payload_log = os.path.join(self.tmp.name, "routing-payloads.jsonl")
        roles = {"top": "sol", "balanced": "qwen"}
        for index in range(5):
            adaptive_control.record_model_outcome(
                self.root, "sample_%024x" % index,
                "balanced", "qwen", "sol", "routine", "passed",
                input_tokens=100 + index, output_tokens=20,
                evidence_digest="sha256:" + ("%064x" % (index + 1)),
            )
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(features=features),
            model_roles=roles,
            environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )
        self.assertEqual(adapter.start(self.root, "eligible", lambda _: None).returncode, 0)
        adaptive_control.record_model_outcome(
            self.root, "sample_%024x" % 99,
            "balanced", "qwen", "sol", "routine", "failed", high_findings=1,
            evidence_digest="sha256:" + ("%064x" % 100),
        )
        self.assertEqual(adapter.start(self.root, "revoked", lambda _: None).returncode, 0)
        payloads = [
            json.loads(line)
            for line in Path(payload_log).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(payloads[0]["model_routing"]["roles"]["balanced"], "qwen")
        self.assertEqual(payloads[1]["model_routing"]["roles"]["balanced"], "sol")
        for payload in payloads:
            self.assertEqual(payload["model_routing"]["candidates"]["balanced"], "qwen")
            self.assertIn("policy", payload["model_routing"])

    def test_legacy_model_roles_never_apply_adaptive_policy(self):
        features = {"model_roles": True}
        payload_log = os.path.join(self.tmp.name, "legacy-routing-payloads.jsonl")
        roles = {"top": "sol", "balanced": "qwen"}
        adaptive_control.record_model_outcome(
            self.root, "sample_%024x" % 99,
            "balanced", "qwen", "sol", "routine", "failed", high_findings=1,
            evidence_digest="sha256:" + ("%064x" % 100),
        )
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(features=features),
            model_roles=roles,
            environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )

        self.assertEqual(adapter.start(self.root, "legacy", lambda _: None).returncode, 0)

        payload = json.loads(Path(payload_log).read_text(encoding="utf-8"))
        self.assertEqual(payload["model_routing"]["roles"], roles)
        self.assertNotIn("candidates", payload["model_routing"])
        self.assertNotIn("policy", payload["model_routing"])

    def test_adapter_attests_candidate_route_and_runner_binds_its_usage(self):
        features = {"model_roles": True, "adaptive_model_routes": True}
        route = {"role": "balanced", "model": "qwen", "baseline": "sol"}
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features=features,
                completion={
                    "type": "turn.completed",
                    "usage": {
                        "model_calls": 1,
                        "tool_calls": 2,
                        "input_tokens": 10,
                        "output_tokens": 5,
                    },
                    "model_route": route,
                },
            ),
            model_roles={"top": "sol", "balanced": "qwen"},
        )

        result = runner.run_task(self.root, "candidate evidence", adapter=adapter)

        self.assertEqual(result["status"], "done")
        evidence = json.loads(Path(
            self.root, ".kimiflow", "demo",
            adaptive_control.MODEL_ROUTE_EVIDENCE_NAME,
        ).read_text(encoding="utf-8"))
        self.assertEqual(evidence["routes"][0]["role"], "balanced")
        self.assertEqual(evidence["routes"][0]["model"], "qwen")
        self.assertEqual(evidence["routes"][0]["baseline"], "sol")
        self.assertEqual(evidence["routes"][0]["turns"], 2)
        self.assertEqual(evidence["routes"][0]["input_tokens"], 20)
        self.assertEqual(evidence["routes"][0]["output_tokens"], 10)

    def test_adapter_rejects_unconfigured_model_route_attestation(self):
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features={"model_roles": True, "adaptive_model_routes": True},
                completion={
                    "type": "turn.completed",
                    "usage": {
                        "model_calls": 1,
                        "tool_calls": 1,
                        "input_tokens": 5,
                        "output_tokens": 2,
                    },
                    "model_route": {
                        "role": "balanced",
                        "model": "arbitrary",
                        "baseline": "sol",
                    },
                },
            ),
            model_roles={"top": "sol", "balanced": "qwen"},
        )

        result = adapter.start(self.root, "test", lambda _session: None)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.error_code, "model_route_mismatch")

    def test_context_rollover_payload_and_acknowledgement_share_exact_identity(self):
        rollover_id = "roll_" + "a" * 32
        digest = "sha256:" + "b" * 64
        features = {"structured_events": True, "context_rollover": True}
        payload_log = os.path.join(self.tmp.name, "rollover-payload.jsonl")
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(
                features=features,
                events=[{
                    "type": "context.compacted",
                    "rollover_id": rollover_id,
                    "current_digest": digest,
                    "before_tokens": 100,
                    "after_tokens": 40,
                }],
            ),
            environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )
        adapter.set_context_rollover({
            "schema_version": 1,
            "status": "pending",
            "rollover_id": rollover_id,
            "current_digest": digest,
            "phase": 3,
            "reason": "material_phase_context_change",
            "retained": [],
            "user_gate": False,
        })
        active_dir = os.path.join(self.root, ".kimiflow", "session")
        os.makedirs(active_dir, exist_ok=True)
        with open(os.path.join(active_dir, "ACTIVE_RUN.json"), "w", encoding="utf-8") as handle:
            json.dump({"status": "active"}, handle)
        result = adapter.resume(self.root, "local-session-123", "continue", lambda _: None)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.context_compaction["rollover_id"], rollover_id)
        self.assertEqual(result.context_compaction["current_digest"], digest)
        payload = json.loads(Path(payload_log).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(payload["context_rollover"]["rollover_id"], rollover_id)
        self.assertEqual(payload["context_rollover"]["current_digest"], digest)

    def test_legacy_token_only_context_compaction_remains_valid_telemetry(self):
        event = model_adapter.normalize_event({
            "type": "context.compacted",
            "before_tokens": 10,
            "after_tokens": 5,
        }, structured=True)

        self.assertEqual(event, {
            "type": "context.compacted",
            "before_tokens": 10,
            "after_tokens": 5,
        })

    def test_turn_failure_preserves_bounded_diagnostic_code(self):
        event = model_adapter.normalize_event({
            "type": "turn.failed",
            "error_code": "provider_crash",
            "diagnostic_code": "herdr_turn_invalid",
        }, structured=True)

        self.assertEqual(event, {
            "type": "turn.failed",
            "error_code": "provider_crash",
            "diagnostic_code": "herdr_turn_invalid",
        })
        with self.assertRaises(model_adapter.AdapterError):
            model_adapter.normalize_event({
                "type": "turn.failed",
                "error_code": "provider_crash",
                "diagnostic_code": "contains spaces",
            }, structured=True)

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
            set(definitions["workUnitPolicy"]["properties"]),
            set(model_adapter.WORK_UNIT_POLICY_KEYS),
        )
        self.assertFalse(definitions["workUnitPolicy"]["additionalProperties"])
        self.assertEqual(
            definitions["turnRequest"]["properties"]["work_unit_policy"]["$ref"],
            "#/$defs/workUnitPolicy",
        )
        self.assertIn(
            {
                "if": {
                    "properties": {
                        "work_unit_policy": {
                            "properties": {
                                "context_scope": {"const": "sealed_input"},
                            },
                            "required": ["context_scope"],
                        },
                    },
                    "required": ["work_unit_policy"],
                },
                "then": {
                    "properties": {
                        "action": {"const": "start"},
                        "session_id": {"type": "null"},
                    },
                    "not": {
                        "anyOf": [
                            {"required": ["workflow_context"]},
                            {"required": ["model_routing"]},
                            {"required": ["context_rollover"]},
                            {"required": ["execution_profile"]},
                        ],
                    },
                },
            },
            definitions["turnRequest"]["allOf"],
        )
        self.assertEqual(
            definitions["features"]["properties"]["work_unit_policy"],
            {"type": "boolean"},
        )
        self.assertEqual(
            set(definitions["modelRouting"]["properties"]["roles"]["properties"]),
            set(model_adapter.MODEL_ROLE_KEYS),
        )
        self.assertEqual(
            set(definitions["modelRouting"]["properties"]["candidates"]["properties"]),
            set(model_adapter.MODEL_ROLE_KEYS),
        )
        self.assertEqual(
            set(definitions["modelRoute"]["properties"]),
            {"role", "model", "baseline"},
        )
        self.assertEqual(
            set(definitions["executionProfile"]["properties"]),
            {
                "schema_version", "model_fingerprint", "max_input_tokens",
                "max_output_tokens", "execution_variants", "controls",
            },
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
        self.assertEqual(
            by_type["turn.completed"]["properties"]["model_route"]["$ref"],
            "#/$defs/modelRoute",
        )
        self.assertEqual(
            by_type["turn.completed"]["properties"]["usage_v2"]["$ref"],
            "#/$defs/usageV2",
        )

    def test_command_adapter_rejects_missing_tool_capability(self):
        caps = {key: True for key in model_adapter.CAPABILITY_KEYS}
        caps["tests"] = False
        adapter = model_adapter.CommandAgentAdapter(self.write_harness(caps))
        with self.assertRaisesRegex(model_adapter.AdapterError, "tests"):
            adapter.info()

    def test_context_rollover_requires_structured_events(self):
        with self.assertRaisesRegex(
            model_adapter.AdapterError,
            "context_rollover_requires_structured_events",
        ):
            model_adapter.validate_info({
                "schema_version": 1,
                "name": "fixture-agent",
                "host": "local",
                "capabilities": {
                    key: True for key in model_adapter.CAPABILITY_KEYS
                },
                "features": {"context_rollover": True},
            })

    def test_adaptive_model_routes_require_model_roles(self):
        with self.assertRaisesRegex(
            model_adapter.AdapterError,
            "adaptive_model_routes_requires_model_roles",
        ):
            model_adapter.validate_info({
                "schema_version": 1,
                "name": "fixture-agent",
                "host": "local",
                "capabilities": {
                    key: True for key in model_adapter.CAPABILITY_KEYS
                },
                "features": {"adaptive_model_routes": True},
            })

    def test_command_adapter_requires_one_terminal_completion_event(self):
        path = self.write_harness(
            completion={"type": "message", "text": "no terminal event"},
        )
        adapter = model_adapter.CommandAgentAdapter(path)
        result = adapter.start(self.root, "task", lambda _: None)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.error_code, "missing_turn_completed")

    def test_command_adapter_accepts_absent_usage_as_unavailable(self):
        path = self.write_harness(completion={"type": "turn.completed"})
        result = model_adapter.CommandAgentAdapter(path).start(self.root, "task", lambda _: None)
        self.assertEqual(result.returncode, 0)
        self.assertIsNone(result.usage)

    def test_command_adapter_rejects_malformed_usage(self):
        path = self.write_harness(completion={
            "type": "turn.completed",
            "usage": {
                "model_calls": "invalid",
                "tool_calls": 2,
                "input_tokens": 10,
                "output_tokens": 5,
            },
        })
        result = model_adapter.CommandAgentAdapter(path).start(self.root, "task", lambda _: None)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.error_code, "invalid_usage")

    def test_claude_start_resume_and_usage(self):
        path = os.path.join(self.tmp.name, "claude-fixture")
        log = os.path.join(self.tmp.name, "claude-argv.jsonl")
        Path(path).write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os, sys
            with open(os.environ["ARGV_LOG"], "a", encoding="utf-8") as handle:
                handle.write(json.dumps(sys.argv[1:]) + "\\n")
            session = "abc12345"
            print(json.dumps({"type":"system","subtype":"init","session_id":session}))
            print(json.dumps({
                "type":"assistant",
                "message":{
                    "content":[{"type":"text","text":"fixture answer"}],
                    "usage":{"input_tokens":12,"output_tokens":4}
                }
            }))
            print(json.dumps({
                "type":"result","subtype":"success","is_error":False,
                "session_id":session
            }))
        """), encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        adapter = model_adapter.ClaudeCodeAdapter(
            claude=path, environ={"PATH": os.environ.get("PATH", ""), "ARGV_LOG": log},
        )
        sessions = []
        started = adapter.start(self.root, "start", sessions.append)
        resumed = adapter.resume(self.root, started.session_id, "resume", sessions.append)
        self.assertEqual((started.returncode, resumed.returncode), (0, 0))
        self.assertEqual((started.session_id, resumed.session_id), ("abc12345", "abc12345"))
        self.assertEqual(started.usage, {
            "model_calls": 1, "tool_calls": 0, "input_tokens": 12, "output_tokens": 4,
        })
        self.assertEqual(started.output, {"messages": ["fixture answer"]})
        argv = [json.loads(line) for line in Path(log).read_text(encoding="utf-8").splitlines()]
        self.assertNotIn("--resume", argv[0])
        self.assertIn("--permission-mode", argv[0])
        self.assertEqual(
            argv[0][argv[0].index("--permission-mode") + 1],
            "bypassPermissions",
        )
        self.assertEqual(argv[1][argv[1].index("--resume") + 1], "abc12345")
        self.assertEqual(sessions, ["abc12345", "abc12345"])

    def test_claude_event_sink_failures_are_normalized(self):
        path = os.path.join(self.tmp.name, "claude-sink-fixture")
        Path(path).write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json
            print(json.dumps({
                "type":"system","subtype":"init","session_id":"abc12345"
            }), flush=True)
            print(json.dumps({
                "type":"assistant",
                "message":{
                    "content":[{"type":"text","text":"fixture answer"}],
                    "usage":{"input_tokens":12,"output_tokens":4}
                }
            }), flush=True)
            print(json.dumps({
                "type":"result","subtype":"success","is_error":False,
                "session_id":"abc12345"
            }), flush=True)
        """), encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        for failed_type in ("session.started", "message", "turn.completed"):
            with self.subTest(failed_type=failed_type):
                def sink(event):
                    if event["type"] == failed_type:
                        raise BrokenPipeError()

                result = model_adapter.ClaudeCodeAdapter(
                    claude=path,
                    event_sink=sink,
                    environ={"PATH": os.environ.get("PATH", "")},
                ).start(self.root, "task", lambda _session: None)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.error_code, "event_sink_failed")

    def test_provider_errors_and_cancel_are_normalized(self):
        path = os.path.join(self.tmp.name, "claude-error-fixture")
        Path(path).write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os, subprocess, sys, time
            mode = os.environ.get("FIXTURE_MODE", "")
            if mode == "cancel":
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
                with open(os.environ["CHILD_PID"], "w", encoding="utf-8") as handle:
                    handle.write(str(child.pid))
                print(json.dumps({"type":"system","subtype":"init","session_id":"abc12345"}), flush=True)
                time.sleep(30)
            elif mode == "timeout":
                time.sleep(30)
            elif mode == "crash":
                raise SystemExit(9)
            else:
                print(json.dumps({"type":"system","subtype":"init","session_id":"abc12345"}))
                print(json.dumps({
                    "type":"result","subtype":mode,"is_error":True,
                    "result":(
                        "quota exceeded" if mode == "quota"
                        else "request refused" if mode == "refusal"
                        else "unknown provider failure"
                    ),
                    "session_id":"abc12345"
                }))
        """), encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        for mode, expected in (
            ("refusal", "refusal"),
            ("quota", "quota_exceeded"),
            ("error_max_budget_usd", "quota_exceeded"),
            ("unexpected_error", "provider_crash"),
            ("crash", "provider_crash"),
        ):
            adapter = model_adapter.ClaudeCodeAdapter(
                claude=path,
                environ={"PATH": os.environ.get("PATH", ""), "FIXTURE_MODE": mode},
            )
            result = adapter.start(self.root, mode, lambda _: None)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.error_code, expected)

        timeout = model_adapter.ClaudeCodeAdapter(
            claude=path,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "FIXTURE_MODE": "timeout",
                "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS": "1",
            },
        ).start(self.root, "timeout", lambda _: None)
        self.assertEqual(timeout.error_code, "turn_timeout")

        child_pid_path = os.path.join(self.tmp.name, "child.pid")
        adapter = model_adapter.ClaudeCodeAdapter(
            claude=path,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "FIXTURE_MODE": "cancel",
                "CHILD_PID": child_pid_path,
            },
        )
        turns = []
        thread = threading.Thread(
            target=lambda: turns.append(adapter.start(self.root, "cancel", lambda _: None)),
        )
        thread.start()
        deadline = time.time() + 3
        while not os.path.exists(child_pid_path) and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(os.path.exists(child_pid_path))
        self.assertTrue(adapter.cancel())
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(turns[0].error_code, "turn_cancelled")
        child_pid = int(Path(child_pid_path).read_text(encoding="utf-8"))
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            self.fail("Claude fixture descendant survived cancellation")

    def test_work_unit_policy_is_bound_on_start_and_resume(self):
        payload_log = os.path.join(self.tmp.name, "policy.jsonl")
        features = {"work_unit_policy": True}
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(features=features),
            environ={"PATH": os.environ.get("PATH", ""), "PAYLOAD_LOG": payload_log},
        )
        policy = {
            "schema_version": 1,
            "unit_kind": "research",
            "context_scope": "project_root",
            "filesystem_access": "read_only",
            "allowed_tools": ["Read", "Glob", "Grep"],
            "settings_sources": [],
            "mcp_servers": [],
            "hooks": False,
            "input_digest": "sha256:" + "d" * 64,
        }
        started = adapter.start(
            self.root, "start", lambda _: None, work_unit_policy=policy,
        )
        resumed = adapter.resume(
            self.root, started.session_id, "resume", lambda _: None,
            work_unit_policy=policy,
        )
        self.assertEqual((started.returncode, resumed.returncode), (0, 0))
        payloads = [
            json.loads(line)
            for line in Path(payload_log).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(payloads[0]["work_unit_policy"], policy)
        self.assertEqual(payloads[1]["work_unit_policy"], policy)
        codex = model_adapter.CodexExecAdapter()
        codex_argv = codex.start_argv(self.root, "task", policy)
        self.assertIn("read-only", codex_argv)
        self.assertNotIn("--ephemeral", codex_argv)
        for flag in ("--ignore-user-config", "--ignore-rules"):
            self.assertIn(flag, codex_argv)
        resumed_argv = codex.resume_argv("abc12345", "task", policy)
        for setting in ("mcp_servers={}", "hooks={}"):
            self.assertIn(setting, codex_argv)
            self.assertIn(setting, resumed_argv)
        codex_cli = shutil.which("codex")
        if codex_cli is not None:
            parsed = subprocess.run(
                [
                    codex_cli, "debug", "prompt-input",
                    "-c", "hooks={}", "test",
                ],
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
            )
            self.assertEqual(parsed.returncode, 0, parsed.stderr)
        claude = model_adapter.ClaudeCodeAdapter()
        argv = claude.start_argv(self.root, "task", policy)
        for flag in (
            "--safe-mode", "--allowedTools", "--setting-sources",
            "--strict-mcp-config", "--mcp-config",
        ):
            self.assertIn(flag, argv)
        self.assertIn('{"mcpServers":{}}', argv)
        sealed = {
            **policy,
            "unit_kind": "solution_candidate",
            "context_scope": "sealed_input",
            "filesystem_access": "none",
            "allowed_tools": [],
        }
        sealed_codex_argv = codex.start_argv(
            self.root, "task", sealed,
        )
        for flag in (
            "--ephemeral", "--skip-git-repo-check",
            "--ignore-user-config", "--ignore-rules",
        ):
            self.assertIn(flag, sealed_codex_argv)
        for setting in (
            "mcp_servers={}", "hooks={}",
            'web_search="disabled"',
        ):
            self.assertIn(setting, sealed_codex_argv)
        for feature in (
            "shell_tool", "unified_exec", "apps", "plugins",
            "browser_use", "computer_use", "image_generation",
            "multi_agent", "tool_suggest", "workspace_dependencies",
        ):
            self.assertIn(feature, sealed_codex_argv)
        with self.assertRaisesRegex(
            model_adapter.AdapterError, "work_unit_resume_forbidden",
        ):
            codex.resume_argv("abc12345", "task", sealed)
        sealed_argv = claude.start_argv(self.root, "task", sealed)
        self.assertIn("--no-session-persistence", sealed_argv)
        self.assertNotIn("--resume", sealed_argv)
        opus_argv = model_adapter.ClaudeCodeAdapter(
            model="claude-opus-5",
        ).start_argv(self.root, "task", sealed)
        self.assertEqual(
            opus_argv[opus_argv.index("--model") + 1],
            "claude-opus-5",
        )
        with self.assertRaisesRegex(
            model_adapter.AdapterError, "work_unit_resume_forbidden",
        ):
            claude.resume_argv("abc12345", "task", sealed)
        with self.assertRaisesRegex(
            model_adapter.AdapterError, "work_unit_resume_forbidden",
        ):
            adapter.resume(
                self.root, "abc12345", "task", lambda _: None,
                work_unit_policy=sealed,
            )
        self.assertEqual(
            len(
                Path(payload_log)
                .read_text(encoding="utf-8")
                .splitlines()
            ),
            2,
        )
        invalid = {
            **sealed,
            "context_scope": "project_root",
            "filesystem_access": "read_only",
            "allowed_tools": ["Write"],
        }
        with self.assertRaisesRegex(
            model_adapter.AdapterError, "work_unit_policy_invalid",
        ):
            model_adapter.validate_work_unit_policy(invalid)

    def test_codex_sealed_runtime_is_content_empty_auth_only_and_ephemeral(self):
        source_home = os.path.join(self.tmp.name, "source-codex-home")
        os.mkdir(source_home, 0o700)
        auth_path = os.path.join(source_home, "auth.json")
        Path(auth_path).write_text(
            '{"token":"CANARY-AUTH"}', encoding="utf-8",
        )
        os.chmod(auth_path, 0o600)
        Path(os.path.join(source_home, "config.toml")).write_text(
            'model = "ambient-model"\n', encoding="utf-8",
        )
        Path(os.path.join(source_home, "AGENTS.md")).write_text(
            "ambient instructions\n", encoding="utf-8",
        )
        os.mkdir(os.path.join(source_home, "skills"))
        adapter = model_adapter.CodexExecAdapter(
            environ={
                "PATH": os.environ.get("PATH", ""),
                "CODEX_HOME": source_home,
                "PROJECT_SECRET_CANARY": "must-not-cross",
            },
        )
        policy = {
            "schema_version": 1,
            "unit_kind": "solution_selector",
            "context_scope": "sealed_input",
            "filesystem_access": "none",
            "allowed_tools": [],
            "settings_sources": [],
            "mcp_servers": [],
            "hooks": False,
            "input_digest": "sha256:" + "f" * 64,
        }
        captured = {}

        def invoke(argv, root, _on_session, sealed=False, child_environment=None):
            captured["root"] = root
            captured["home"] = child_environment["CODEX_HOME"]
            self.assertTrue(sealed)
            self.assertNotEqual(root, self.root)
            self.assertEqual(os.listdir(root), [])
            self.assertEqual(
                os.listdir(child_environment["CODEX_HOME"]),
                ["auth.json"],
            )
            linked_auth = os.path.join(
                child_environment["CODEX_HOME"], "auth.json",
            )
            self.assertTrue(os.path.islink(linked_auth))
            self.assertEqual(
                os.path.realpath(linked_auth),
                os.path.realpath(auth_path),
            )
            self.assertNotIn("PROJECT_SECRET_CANARY", child_environment)
            self.assertEqual(
                child_environment["CODEX_SQLITE_HOME"],
                child_environment["CODEX_HOME"],
            )
            self.assertEqual(argv[argv.index("-C") + 1], root)
            self.assertNotIn(self.root, argv)
            self.assertIn("--ephemeral", argv)
            return model_adapter.TurnResult(0, output={"messages": []})

        with mock.patch.object(adapter, "_invoke", side_effect=invoke):
            result = adapter.start(
                self.root, "sealed prompt", lambda _: None,
                work_unit_policy=policy,
            )
        self.assertEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(captured["root"]))
        self.assertFalse(os.path.exists(captured["home"]))

    def test_sealed_command_policy_omits_optional_context_and_customizations(self):
        payload_log = os.path.join(self.tmp.name, "sealed-policy.jsonl")
        features = {
            "work_unit_policy": True,
            "structured_events": True,
            "workflow_context": True,
            "model_roles": True,
            "adaptive_execution_profiles": True,
            "adaptive_model_routes": True,
            "context_rollover": True,
        }
        adapter = model_adapter.CommandAgentAdapter(
            self.write_harness(features=features),
            model="local-default",
            model_roles={"top": "local-top", "balanced": "local-balanced"},
            environ={
                "PATH": os.environ.get("PATH", ""),
                "PAYLOAD_LOG": payload_log,
            },
        )
        adapter._context_rollover = {"private": "must not cross boundary"}
        policy = {
            "schema_version": 1,
            "unit_kind": "solution_selector",
            "context_scope": "sealed_input",
            "filesystem_access": "none",
            "allowed_tools": [],
            "settings_sources": [],
            "mcp_servers": [],
            "hooks": False,
            "input_digest": "sha256:" + "f" * 64,
        }
        state_dir = os.path.join(self.root, ".kimiflow", "session")
        os.makedirs(state_dir, exist_ok=True)
        Path(os.path.join(state_dir, "ACTIVE_RUN.json")).write_text(
            "{}", encoding="utf-8",
        )
        result = adapter.start(
            self.root, "sealed", lambda _: None,
            work_unit_policy=policy,
        )
        self.assertEqual(result.returncode, 0)
        payload = json.loads(
            Path(payload_log).read_text(encoding="utf-8").splitlines()[0],
        )
        self.assertEqual(payload["work_unit_policy"], policy)
        for key in (
            "execution_profile", "workflow_context", "model_routing",
            "context_rollover",
        ):
            self.assertNotIn(key, payload)

    def test_eof_does_not_disable_deadline_or_public_cancel(self):
        claude_path = os.path.join(self.tmp.name, "claude-eof-fixture")
        marker = os.path.join(self.tmp.name, "claude-eof-ready")
        Path(claude_path).write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import os, time
            with open(os.environ["EOF_READY"], "w", encoding="utf-8") as handle:
                handle.write("ready")
            os.close(1)
            time.sleep(30)
        """), encoding="utf-8")
        os.chmod(claude_path, 0o700)
        started = time.monotonic()
        timeout = model_adapter.ClaudeCodeAdapter(
            claude=claude_path,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "EOF_READY": marker,
                "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS": "1",
            },
        ).start(self.root, "timeout", lambda _: None)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(timeout.error_code, "turn_timeout")

        os.unlink(marker)
        adapter = model_adapter.ClaudeCodeAdapter(
            claude=claude_path,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "EOF_READY": marker,
            },
        )
        turns = []
        thread = threading.Thread(
            target=lambda: turns.append(
                adapter.start(self.root, "cancel", lambda _: None),
            ),
        )
        thread.start()
        deadline = time.monotonic() + 3
        while not os.path.exists(marker) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(os.path.exists(marker))
        self.assertTrue(adapter.cancel())
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(turns[0].error_code, "turn_cancelled")

        command_path = os.path.join(self.tmp.name, "command-eof-fixture")
        Path(command_path).write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os, sys, time
            if sys.argv[1] == "capabilities":
                print(json.dumps({
                    "schema_version":1,
                    "name":"eof-fixture",
                    "host":"local",
                    "capabilities":{
                        "files":True,"shell":True,"tests":True,
                        "resume":True,"gates":True
                    }
                }))
                raise SystemExit(0)
            json.loads(sys.stdin.readline())
            os.close(1)
            time.sleep(30)
        """), encoding="utf-8")
        os.chmod(command_path, 0o700)
        started = time.monotonic()
        command_timeout = model_adapter.CommandAgentAdapter(
            command_path,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS": "1",
            },
        ).start(self.root, "timeout", lambda _: None)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(command_timeout.error_code, "turn_timeout")

    def test_claude_parent_exit_reaps_same_group_descendants(self):
        path = os.path.join(self.tmp.name, "claude-orphan-fixture")
        marker = os.path.join(self.tmp.name, "orphan-effect")
        Path(path).write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import os, subprocess, sys
            code = (
                "import os,time; mode=os.environ['ORPHAN_MODE']; "
                "marker=os.environ['ORPHAN_MARKER']; "
                "os.close(1) if mode == 'closed' else None; "
                "time.sleep(float(os.environ['ORPHAN_DELAY'])); "
                "open(marker,'w').write('late')"
            )
            subprocess.Popen([sys.executable, "-c", code])
            os._exit(9)
        """), encoding="utf-8")
        os.chmod(path, 0o700)
        for mode, delay in (("inherit", "1.5"), ("closed", "0.4")):
            with self.subTest(mode=mode):
                if os.path.exists(marker):
                    os.unlink(marker)
                adapter = model_adapter.ClaudeCodeAdapter(
                    claude=path,
                    environ={
                        "PATH": os.environ.get("PATH", ""),
                        "ORPHAN_MODE": mode,
                        "ORPHAN_MARKER": marker,
                        "ORPHAN_DELAY": delay,
                        "KIMIFLOW_ADAPTER_TURN_TIMEOUT_SECONDS": "1",
                    },
                )
                started = time.monotonic()
                result = adapter.start(self.root, "orphan", lambda _: None)
                self.assertLess(time.monotonic() - started, 3)
                self.assertEqual(
                    result.error_code,
                    "turn_timeout" if mode == "inherit" else "provider_crash",
                )
                time.sleep(0.7)
                self.assertFalse(os.path.exists(marker))

    def test_claude_sealed_output_is_private_and_streams_are_bounded(self):
        path = os.path.join(self.tmp.name, "claude-bounded-fixture")
        Path(path).write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os
            mode = os.environ.get("FIXTURE_MODE", "success")
            print(json.dumps({"type":"system","subtype":"init","session_id":"abc12345"}))
            if mode == "oversized":
                print("x" * 300000)
            else:
                for index in range(4):
                    print(json.dumps({
                        "type":"assistant",
                        "message":{
                            "content":[{"type":"text","text":"sealed-secret-%d" % index}],
                            "usage":{"input_tokens":1,"output_tokens":1}
                        }
                    }))
                print(json.dumps({
                    "type":"result","subtype":"success","is_error":False,
                    "session_id":"abc12345"
                }))
        """), encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        policy = {
            "schema_version": 1,
            "unit_kind": "solution_candidate",
            "context_scope": "sealed_input",
            "filesystem_access": "none",
            "allowed_tools": [],
            "settings_sources": [],
            "mcp_servers": [],
            "hooks": False,
            "input_digest": "sha256:" + "e" * 64,
        }
        diagnostics = io.StringIO()
        adapter = model_adapter.ClaudeCodeAdapter(
            claude=path,
            environ={"PATH": os.environ.get("PATH", "")},
            stderr=diagnostics,
        )
        result = adapter.start(
            self.root, "sealed", lambda _: None, work_unit_policy=policy,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("sealed-secret-0", json.dumps(result.output))
        self.assertNotIn("sealed-secret", diagnostics.getvalue())

        oversized = model_adapter.ClaudeCodeAdapter(
            claude=path,
            environ={
                "PATH": os.environ.get("PATH", ""),
                "FIXTURE_MODE": "oversized",
            },
        ).start(self.root, "bounded", lambda _: None)
        self.assertNotEqual(oversized.returncode, 0)
        self.assertEqual(oversized.error_code, "event_too_large")

        with mock.patch.object(model_adapter, "MAX_EVENT_STREAM_BYTES", 100):
            aggregate = model_adapter.ClaudeCodeAdapter(
                claude=path,
                environ={"PATH": os.environ.get("PATH", "")},
            ).start(self.root, "bounded", lambda _: None)
        self.assertNotEqual(aggregate.returncode, 0)
        self.assertEqual(aggregate.error_code, "event_stream_too_large")


if __name__ == "__main__":
    unittest.main()
