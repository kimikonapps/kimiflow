import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import active_run, adaptive_control, phase_context, runner


THREAD = "019f5fa0-567a-70e0-9b07-604ffbdafbf4"


class FakeAdapter:
    def __init__(
        self, start_action=None, resume_actions=None, returncode=0, usage=None,
        error_code="", pre_thread_action=None,
    ):
        self.start_action = start_action
        self.pre_thread_action = pre_thread_action
        self.resume_actions = list(resume_actions or [])
        self.returncode = returncode
        self.usage = usage
        self.error_code = error_code
        self.starts = []
        self.resumes = []

    def start(self, root, prompt, on_thread):
        self.starts.append((root, prompt))
        if self.pre_thread_action:
            self.pre_thread_action()
        on_thread(THREAD)
        if self.start_action:
            self.start_action()
        return runner.TurnResult(
            returncode=self.returncode, thread_id=THREAD, usage=self.usage,
            error_code=self.error_code,
        )

    def resume(self, root, thread_id, prompt, on_thread):
        self.resumes.append((root, thread_id, prompt))
        if self.resume_actions:
            action = self.resume_actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            action()
        return runner.TurnResult(
            returncode=self.returncode, thread_id=thread_id, usage=self.usage,
            error_code=self.error_code,
        )


class InterruptingAdapter(FakeAdapter):
    def start(self, root, prompt, on_thread):
        self.starts.append((root, prompt))
        on_thread(THREAD)
        raise KeyboardInterrupt()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Kimiflow Test"], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "kimiflow@example.test"], check=True)
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("fixture\n")
        subprocess.run(["git", "-C", self.root, "add", "README.md"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "fixture"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    @property
    def run_dir(self):
        return os.path.join(self.root, ".kimiflow", "demo")

    @property
    def active_path(self):
        return os.path.join(self.root, ".kimiflow", "session", "ACTIVE_RUN.json")

    def write_active(self, awaiting=False, owner=THREAD):
        os.makedirs(self.run_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.active_path), exist_ok=True)
        head = subprocess.check_output(["git", "-C", self.root, "rev-parse", "HEAD"], text=True).strip()
        with open(os.path.join(self.run_dir, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "Flow schema: 4\nMode: feature\nScope: small\nStatus: active\n"
                "Affected files:\n- README.md\nPhase 0: done\nPhase 1: done\n"
            )
        active = {
            "schema_version": 1,
            "status": "active",
            "run": ".kimiflow/demo",
            "mode": "feature",
            "scope": "small",
            "host": "codex",
            "started_head": head,
            "last_checked_head": head,
            "owner": {"host": "codex", "session_id": owner},
        }
        if awaiting:
            active.update({"awaiting_user": True, "awaiting_kind": "scope-risk", "awaiting_reason": "choose"})
        with open(self.active_path, "w", encoding="utf-8") as handle:
            json.dump(active, handle)

    def write_outcome(self, outcome="done"):
        os.makedirs(self.run_dir, exist_ok=True)
        if os.path.exists(self.active_path):
            os.unlink(self.active_path)
        with open(os.path.join(self.run_dir, "SESSION-OUTCOME.json"), "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "outcome": outcome, "reason": "material choice"}, handle)

    def read_receipt(self):
        with open(runner.receipt_path(self.root), encoding="utf-8") as handle:
            return json.load(handle)

    def write_pi_start_claim(self, binding, token):
        claim_path = os.path.join(
            self.root,
            ".kimiflow",
            "session",
            runner.PI_START_CLAIM_NAME,
        )
        os.makedirs(claim_path, mode=0o700, exist_ok=False)
        with open(os.path.join(claim_path, "owner.json"), "w", encoding="utf-8") as handle:
            json.dump({
                "schemaVersion": 1,
                "token": token,
                "pid": os.getpid(),
                "root": binding["root"],
                "captainSessionId": binding["captain_session_id"],
                "workerId": binding["worker_id"],
            }, handle)
        return claim_path

    def test_run_starts_codex_safely_and_writes_minimal_receipt(self):
        nested = os.path.join(self.root, "nested")
        os.mkdir(nested)
        self.assertEqual(runner._resolve_project_root(nested), os.path.realpath(self.root))
        adapter_contract = runner.CodexExecAdapter(codex="/usr/local/bin/codex")
        argv = adapter_contract.start_argv(self.root, "prompt")
        self.assertEqual(argv[:3], ["/usr/local/bin/codex", "exec", "--json"])
        self.assertIn("workspace-write", argv)
        self.assertIn(self.root, argv)
        self.assertIn('approval_policy="never"', argv)
        env = adapter_contract.child_environment(
            {
                "PATH": "/bin",
                "CODEX_THREAD_ID": "parent",
                "KIMIFLOW_SESSION_ID": "parent",
                "KIMIFLOW_SESSION_HOST": "codex",
            }
        )
        self.assertEqual(env["PATH"], "/bin")
        self.assertNotIn("CODEX_THREAD_ID", env)
        self.assertNotIn("KIMIFLOW_SESSION_ID", env)
        self.assertNotIn("KIMIFLOW_SESSION_HOST", env)

        adapter = FakeAdapter(start_action=lambda: self.write_active(awaiting=True))
        result = runner.run_task(self.root, "secret task text", adapter=adapter)
        receipt = self.read_receipt()
        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(receipt["thread_id"], THREAD)
        self.assertEqual(receipt["status"], "awaiting_user")
        self.assertEqual(receipt["controller_pid"], os.getpid())
        self.assertNotIn("secret task text", json.dumps(receipt))
        self.assertEqual(stat.S_IMODE(os.stat(runner.receipt_path(self.root)).st_mode), 0o600)

    def test_default_codex_adapter_stays_default(self):
        args = runner._parser().parse_args(["run", "build it"])
        self.assertEqual(args.adapter, "codex")
        self.assertFalse(args.events_jsonl)
        self.assertEqual(args.model_role, [])
        self.assertEqual(args.require_feature, [])
        self.assertIsInstance(runner._adapter_from_args(args), runner.CodexExecAdapter)
        self.assertIn("$kimiflow", runner._initial_prompt("build it"))
        self.assertNotIn("$kimiflow", runner._initial_prompt("build it", workflow_aware=True))

    def test_pi_bridge_identity_is_persisted_and_required_for_resume(self):
        binding = {
            "schema_version": 1,
            "root": os.path.realpath(self.root),
            "captain_session_id": "pi-primary-0001",
            "worker_id": "worker-00000001",
        }
        adapter = FakeAdapter(start_action=lambda: self.write_active(awaiting=True))
        with mock.patch.dict(
            os.environ,
            {runner.PI_BRIDGE_ENV: json.dumps(binding)},
        ):
            result = runner.run_task(self.root, "bridge fixture", adapter=adapter)
        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(self.read_receipt()["bridge"], {
            "schema_version": 1,
            "captain_session_id": "pi-primary-0001",
            "worker_id": "worker-00000001",
        })

        unrelated = {
            **binding,
            "worker_id": "worker-unrelated01",
        }
        with mock.patch.dict(
            os.environ,
            {runner.PI_BRIDGE_ENV: json.dumps(unrelated)},
        ), self.assertRaisesRegex(
            runner.RunnerError,
            "same Pi bridge identity",
        ):
            runner.resume_task(self.root, "do not deliver", adapter=adapter)

    def test_pi_start_claim_is_released_after_exact_initial_receipt(self):
        binding = {
            "schema_version": 1,
            "root": os.path.realpath(self.root),
            "captain_session_id": "pi-primary-0001",
            "worker_id": "worker-00000001",
        }
        token = "claim-" + "a" * 32
        claim_path = self.write_pi_start_claim(binding, token)
        handed_off = []

        def inspect_handoff():
            with open(os.path.join(claim_path, "owner.json"), encoding="utf-8") as handle:
                handed_off.append(json.load(handle)["pid"])

        def start_action():
            self.write_active(awaiting=True)

        adapter = FakeAdapter(
            start_action=start_action,
            pre_thread_action=inspect_handoff,
        )
        with mock.patch.dict(os.environ, {
            runner.PI_BRIDGE_ENV: json.dumps(binding),
            runner.PI_START_CLAIM_ENV: token,
        }):
            result = runner.run_task(self.root, "bridge fixture", adapter=adapter)

        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(handed_off, [os.getpid()])
        self.assertFalse(os.path.exists(claim_path))
        self.assertEqual(self.read_receipt()["bridge"]["worker_id"], binding["worker_id"])

    def test_pi_start_claim_is_released_when_existing_run_blocks_start(self):
        binding = {
            "schema_version": 1,
            "root": os.path.realpath(self.root),
            "captain_session_id": "pi-primary-0001",
            "worker_id": "worker-00000001",
        }
        token = "claim-" + "b" * 32
        claim_path = self.write_pi_start_claim(binding, token)
        self.write_active()
        with mock.patch.dict(os.environ, {
            runner.PI_BRIDGE_ENV: json.dumps(binding),
            runner.PI_START_CLAIM_ENV: token,
        }), self.assertRaisesRegex(runner.RunnerError, "active Kimiflow run"):
            runner.run_task(self.root, "bridge fixture", adapter=FakeAdapter())

        self.assertFalse(os.path.exists(claim_path))

    def test_pi_start_claim_serializes_resume_until_runner_settles(self):
        binding = {
            "schema_version": 1,
            "root": os.path.realpath(self.root),
            "captain_session_id": "pi-primary-0001",
            "worker_id": "worker-00000001",
        }
        adapter = FakeAdapter(
            start_action=lambda: self.write_active(awaiting=True),
            resume_actions=[lambda: self.write_active(awaiting=True)],
        )
        with mock.patch.dict(
            os.environ,
            {runner.PI_BRIDGE_ENV: json.dumps(binding)},
        ):
            runner.run_task(self.root, "bridge fixture", adapter=adapter)

        token = "claim-" + "e" * 32
        claim_path = self.write_pi_start_claim(binding, token)
        handed_off = []

        def inspect_resume_handoff():
            with open(os.path.join(claim_path, "owner.json"), encoding="utf-8") as handle:
                handed_off.append(json.load(handle)["pid"])
            self.write_active(awaiting=True)

        adapter.resume_actions = [inspect_resume_handoff]
        with mock.patch.dict(os.environ, {
            runner.PI_BRIDGE_ENV: json.dumps(binding),
            runner.PI_START_CLAIM_ENV: token,
        }):
            result = runner.resume_task(self.root, "accepted", adapter=adapter)

        self.assertEqual(result["status"], "awaiting_user")
        self.assertEqual(handed_off, [os.getpid()])
        self.assertFalse(os.path.exists(claim_path))

    def test_pi_start_claim_rejects_mismatched_owner_without_deleting_it(self):
        binding = {
            "schema_version": 1,
            "root": os.path.realpath(self.root),
            "captain_session_id": "pi-primary-0001",
            "worker_id": "worker-00000001",
        }
        claim_path = self.write_pi_start_claim(binding, "claim-" + "c" * 32)
        with mock.patch.dict(os.environ, {
            runner.PI_BRIDGE_ENV: json.dumps(binding),
            runner.PI_START_CLAIM_ENV: "claim-" + "d" * 32,
        }), self.assertRaisesRegex(runner.RunnerError, "start claim owner is invalid"):
            runner.run_task(self.root, "bridge fixture", adapter=FakeAdapter())

        self.assertTrue(os.path.isdir(claim_path))

    def test_pi_start_claim_handoff_never_truncates_the_published_owner(self):
        binding = {
            "schema_version": 1,
            "root": os.path.realpath(self.root),
            "captain_session_id": "pi-primary-0001",
            "worker_id": "worker-00000001",
        }
        token = "claim-" + "b" * 32
        claim_path = self.write_pi_start_claim(binding, token)
        with mock.patch.dict(os.environ, {
            runner.PI_BRIDGE_ENV: json.dumps(binding),
            runner.PI_START_CLAIM_ENV: token,
        }), mock.patch.object(
            runner.os,
            "rename",
            side_effect=OSError("injected handoff interruption"),
        ), self.assertRaises(runner.RunnerError):
            runner._read_pi_start_claim(self.root)

        with open(os.path.join(claim_path, "owner.json"), encoding="utf-8") as handle:
            owner = json.load(handle)
        self.assertEqual(owner["token"], token)
        self.assertEqual(owner["pid"], os.getpid())
        self.assertEqual(os.listdir(claim_path), ["owner.json"])

    def test_runner_selects_claude_and_pins_resume_identity(self):
        args = runner._parser().parse_args([
            "run", "build it", "--adapter", "claude", "--model", "fable",
        ])
        adapter = runner._adapter_from_args(args)
        self.assertIsInstance(adapter, runner.ClaudeCodeAdapter)
        self.assertEqual(adapter.model, "fable")
        runner.write_receipt(self.root, {
            "schema_version": 1,
            "host": "claude",
            "adapter": "claude-code",
            "root": self.root,
            "session_id": THREAD,
            "thread_id": THREAD,
            "status": "interrupted",
            "turns": 1,
            "adapter_contract": runner._adapter_contract(adapter),
            "started_at": "2026-07-27T00:00:00Z",
            "updated_at": "2026-07-27T00:00:00Z",
        })
        with self.assertRaises(runner.RunnerError) as context:
            runner.resume_task(self.root, adapter=runner.CodexExecAdapter())
        self.assertEqual(context.exception.status, "adapter_mismatch")
        self.assertEqual(context.exception.code, 2)
        with self.assertRaises(runner.RunnerError) as drift:
            runner.resume_task(
                self.root,
                adapter=runner.ClaudeCodeAdapter(model="different-model"),
            )
        self.assertEqual(drift.exception.status, "adapter_mismatch")
        self.assertEqual(drift.exception.code, 2)

    def test_run_continues_same_thread_until_terminal_outcome(self):
        adapter = FakeAdapter(
            start_action=self.write_active,
            resume_actions=[lambda: self.write_outcome("done")],
        )
        result = runner.run_task(self.root, "build it", adapter=adapter)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["turns"], 2)
        self.assertEqual(len(adapter.resumes), 1)
        self.assertEqual(adapter.resumes[0][1], THREAD)
        self.assertIn("next action", adapter.resumes[0][2].lower())
        self.assertEqual(self.read_receipt()["status"], "done")

    def test_missing_rollover_capability_uses_bounded_fallback_without_wait(self):
        self.write_active()
        pending = {
            "schema_version": 1,
            "status": "pending",
            "rollover_id": "roll_" + "a" * 32,
            "reason": "measured_context_pressure",
            "scope": "small",
            "pressure": "hard",
            "phase": 3,
            "previous_digest": "sha256:" + "b" * 64,
            "current_digest": "sha256:" + "c" * 64,
            "changed_ratio": 0,
            "estimated_tokens": 100,
            "cumulative_input_tokens": 120000,
            "retained": [],
            "user_gate": False,
        }
        adaptive_control.write_rollover(self.root, self.run_dir, pending)
        status = active_run.status_json(self.root)
        runner._prepare_context_rollover(self.root, status, FakeAdapter())
        with open(
            os.path.join(self.run_dir, adaptive_control.ROLLOVER_NAME),
            encoding="utf-8",
        ) as handle:
            result = json.load(handle)
        self.assertEqual(result["status"], "bounded_fallback")
        self.assertFalse(result["user_gate"])
        self.assertFalse(status.get("awaiting_user", False))

    def test_invalid_rollover_feature_dependency_uses_bounded_fallback(self):
        test_case = self

        class InvalidRolloverAdapter(FakeAdapter):
            def info(self):
                return {
                    "schema_version": 1,
                    "name": "invalid-rollover",
                    "host": "local",
                    "capabilities": {
                        key: True for key in runner.model_adapter.CAPABILITY_KEYS
                    },
                    "features": {"context_rollover": True},
                }

            def set_context_rollover(self, _value):
                test_case.fail("invalid adapter must not receive rollover")

        self.write_active()
        pending = {
            "schema_version": 1,
            "status": "pending",
            "rollover_id": "roll_" + "a" * 32,
            "reason": "measured_context_pressure",
            "scope": "small",
            "pressure": "hard",
            "phase": 3,
            "previous_digest": "sha256:" + "b" * 64,
            "current_digest": "sha256:" + "c" * 64,
            "changed_ratio": 0,
            "estimated_tokens": 100,
            "cumulative_input_tokens": 120000,
            "retained": [],
            "user_gate": False,
        }
        adaptive_control.write_rollover(self.root, self.run_dir, pending)

        runner._prepare_context_rollover(
            self.root, active_run.status_json(self.root), InvalidRolloverAdapter(),
        )

        with open(
            os.path.join(self.run_dir, adaptive_control.ROLLOVER_NAME),
            encoding="utf-8",
        ) as handle:
            result = json.load(handle)
        self.assertEqual(result["status"], "bounded_fallback")
        self.assertEqual(result["fallback_reason"], "capability_unavailable")
        self.assertFalse(result["user_gate"])

    def test_measured_large_run_token_pressure_triggers_rollover_before_hard_budget(self):
        self.write_active()
        status = active_run.status_json(self.root)
        status["scope"] = "large"
        status["execution_control"] = {
            "budget_pressure": "normal",
            "usage": {
                "model_calls": 5,
                "tool_calls": 10,
                "input_tokens": 120000,
                "output_tokens": 1000,
            },
        }
        shadow = {
            "schema_version": 1,
            "status": "current",
            "phase": 5,
            "estimated_tokens": 100,
            "composite_basis": "sha256:" + "a" * 64,
            "selection": [],
        }

        with mock.patch.object(
            phase_context, "load_stored_shadow", return_value=shadow,
        ):
            runner._prepare_context_rollover(self.root, status, FakeAdapter())

        with open(
            os.path.join(self.run_dir, adaptive_control.ROLLOVER_NAME),
            encoding="utf-8",
        ) as handle:
            receipt = json.load(handle)
        self.assertEqual(receipt["status"], "bounded_fallback")
        self.assertEqual(receipt["reason"], "measured_context_pressure")
        self.assertEqual(receipt["fallback_reason"], "capability_unavailable")
        self.assertEqual(receipt["cumulative_input_tokens"], 120000)

    def test_usage_receipt_distinguishes_known_usage_from_unavailable(self):
        known = {"model_calls": 1, "tool_calls": 3, "input_tokens": 120, "output_tokens": 30}
        adapter = FakeAdapter(start_action=lambda: self.write_active(awaiting=True), usage=known)
        result = runner.run_task(self.root, "measure it", adapter=adapter)
        self.assertEqual(result["usage"], {"status": "available", **known})

        os.unlink(runner.receipt_path(self.root))
        os.unlink(self.active_path)
        unavailable = runner.run_task(
            self.root, "no counters", adapter=FakeAdapter(start_action=lambda: self.write_active(awaiting=True))
        )
        self.assertEqual(unavailable["usage"]["status"], "unavailable")
        self.assertTrue(all(unavailable["usage"][key] is None for key in runner.model_adapter.USAGE_KEYS))

    def test_usage_unavailability_is_sticky_after_any_unmeasured_turn(self):
        known = {"model_calls": 1, "tool_calls": 2, "input_tokens": 10, "output_tokens": 4}
        available = runner._merge_usage(runner._unavailable_usage(), known, initialize=True)
        self.assertEqual(available["status"], "available")
        unknown = runner._merge_usage(available, None)
        self.assertEqual(unknown["status"], "unavailable")
        self.assertEqual(runner._merge_usage(unknown, known)["status"], "unavailable")

        self.write_active()
        status = {"run": ".kimiflow/demo"}
        self.assertEqual(active_run.record_host_usage(self.root, status, known)["status"], "available")
        self.assertEqual(active_run.record_host_usage(self.root, status, None)["status"], "unavailable")
        self.assertEqual(active_run.record_host_usage(self.root, status, known)["status"], "unavailable")

    def test_no_progress_has_one_final_recovery_then_resumable_exhaustion(self):
        old = os.environ.get("KIMIFLOW_RUNNER_TURN_LIMIT")
        os.environ["KIMIFLOW_RUNNER_TURN_LIMIT"] = "1"
        self.addCleanup(
            lambda: os.environ.pop("KIMIFLOW_RUNNER_TURN_LIMIT", None)
            if old is None else os.environ.__setitem__("KIMIFLOW_RUNNER_TURN_LIMIT", old)
        )
        adapter = FakeAdapter(start_action=self.write_active)
        result = runner.run_task(self.root, "never progresses", adapter=adapter)
        self.assertEqual(result["status"], "exhausted")
        self.assertEqual(result["turns"], 2)
        self.assertEqual(len(adapter.resumes), 1)
        self.assertIn("single final bounded recovery", adapter.resumes[0][2])
        stored = self.read_receipt()
        self.assertTrue(stored["final_recovery_used"])
        self.assertEqual(stored["status"], "exhausted")

        resumed = FakeAdapter(resume_actions=[lambda: self.write_outcome("done")])
        finished = runner.resume_task(self.root, adapter=resumed)
        self.assertEqual(finished["status"], "done")
        self.assertGreater(self.read_receipt()["turn_limit"], stored["turn_limit"])

    def test_terminal_provider_errors_are_not_retried(self):
        for error_code in ("turn_cancelled", "refusal", "quota_exceeded"):
            with self.subTest(error_code=error_code):
                shutil.rmtree(
                    os.path.join(self.root, ".kimiflow"),
                    ignore_errors=True,
                )
                adapter = FakeAdapter(
                    start_action=self.write_active,
                    returncode=1,
                    error_code=error_code,
                )
                if error_code == "turn_cancelled":
                    result = runner.run_task(
                        self.root, "terminal", adapter=adapter,
                    )
                    self.assertEqual(result["status"], "interrupted")
                else:
                    with self.assertRaisesRegex(
                        runner.RunnerError, "terminal error",
                    ):
                        runner.run_task(
                            self.root, "terminal", adapter=adapter,
                        )
                    self.assertEqual(
                        self.read_receipt()["status"], "transport_error",
                    )
                self.assertEqual(adapter.resumes, [])

    def test_adapter_capability_preflight_fails_before_start(self):
        class Incomplete(FakeAdapter):
            def info(self):
                return {
                    "schema_version": 1,
                    "name": "chat-only",
                    "host": "local",
                    "capabilities": {"files": False, "shell": False, "tests": False, "resume": True, "gates": False},
                }

        adapter = Incomplete()
        with self.assertRaises(runner.RunnerError) as ctx:
            runner.run_task(self.root, "must not start", adapter=adapter)
        self.assertEqual(ctx.exception.status, "adapter_incompatible")
        self.assertEqual(adapter.starts, [])

    def test_compatible_adapter_preflight_is_successful(self):
        self.assertEqual(runner.exit_code({"status": "compatible"}), 0)

    def test_continuation_prompt_carries_bounded_execution_decision(self):
        prompt = runner._continuation_prompt(
            {
                "transition": {
                    "action": "change_build_strategy",
                    "target_node": "phase_5",
                    "reason": "event:no_progress",
                    "execution": {
                        "profile": "critical",
                        "profile_reason": "material_build_risk",
                        "strategy_mode": "recovery",
                        "budget_pressure": "hard",
                        "directive": "prune_optional_work",
                    },
                }
            }
        )
        self.assertIn("profile=critical", prompt)
        self.assertIn("profile_reason=material_build_risk", prompt)
        self.assertIn("strategy_mode=recovery", prompt)
        self.assertIn("directive=prune_optional_work", prompt)

    def test_material_wait_requires_message_and_resumes_owner(self):
        first = FakeAdapter(start_action=lambda: self.write_active(awaiting=True))
        waiting = runner.run_task(self.root, "needs a choice", adapter=first)
        self.assertEqual(runner.exit_code(waiting), 3)
        with self.assertRaises(runner.RunnerError) as ctx:
            runner.resume_task(self.root, adapter=FakeAdapter())
        self.assertEqual(ctx.exception.status, "message_required")

        resumed = FakeAdapter(resume_actions=[lambda: self.write_outcome("done")])
        result = runner.resume_task(self.root, message="choose the safe path", adapter=resumed)
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.read_receipt()["controller_pid"], os.getpid())
        self.assertEqual(resumed.resumes[0][1], THREAD)

        os.unlink(runner.receipt_path(self.root))
        self.write_outcome("parked")
        runner.write_receipt(
            self.root,
            {
                "schema_version": 1,
                "host": "codex",
                "root": self.root,
                "thread_id": THREAD,
                "status": "parked",
                "turns": 1,
                "active_run": ".kimiflow/demo",
                "started_at": "2026-07-18T00:00:00Z",
                "updated_at": "2026-07-18T00:00:00Z",
            },
        )
        parked_resume = FakeAdapter(resume_actions=[lambda: self.write_outcome("done")])
        result = runner.resume_task(self.root, message="approved", adapter=parked_resume)
        self.assertEqual(result["status"], "done")
        self.assertIn("--resume demo", parked_resume.resumes[0][2])

    def test_interrupted_resume_preserves_explicit_captain_message(self):
        first = FakeAdapter(start_action=lambda: self.write_active(awaiting=True))
        runner.run_task(self.root, "needs a choice", adapter=first)
        self.write_active(awaiting=False)
        receipt = self.read_receipt()
        runner.write_receipt(
            self.root,
            {**receipt, "status": "interrupted"},
        )
        resumed = FakeAdapter(
            resume_actions=[lambda: self.write_outcome("done")],
        )
        result = runner.resume_task(
            self.root,
            message="USER STEER MUST SURVIVE",
            adapter=resumed,
        )
        self.assertEqual(result["status"], "done")
        self.assertEqual(
            resumed.resumes[0][2],
            "USER STEER MUST SURVIVE",
        )

    def test_runner_fail_closed_cases_preserve_workflow_state(self):
        self.write_active(owner="other-thread")
        with open(self.active_path, "rb") as handle:
            before = handle.read()
        with self.assertRaises(runner.RunnerError) as ctx:
            runner.run_task(self.root, "do not adopt", adapter=FakeAdapter())
        self.assertEqual(ctx.exception.status, "active_run_exists")
        with open(self.active_path, "rb") as handle:
            self.assertEqual(handle.read(), before)

        os.unlink(self.active_path)
        with self.assertRaises(runner.RunnerError) as ctx:
            runner.run_task(self.root, "must activate", adapter=FakeAdapter())
        self.assertEqual(ctx.exception.status, "no_kimiflow_run")

        receipt = runner.receipt_path(self.root)
        if os.path.lexists(receipt):
            os.unlink(receipt)
        outside = os.path.join(self.root, "outside.json")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        os.symlink(outside, receipt)
        with self.assertRaises(runner.RunnerError) as ctx:
            runner.load_receipt(self.root)
        self.assertEqual(ctx.exception.status, "unsafe_receipt")
        os.unlink(receipt)

        os.makedirs(self.run_dir, exist_ok=True)
        outcome = os.path.join(self.run_dir, "SESSION-OUTCOME.json")
        os.symlink(outside, outcome)
        self.assertEqual(runner._outcome_fingerprints(self.root), {})
        os.unlink(outcome)

        failing = FakeAdapter(start_action=self.write_active, returncode=9)
        with self.assertRaises(runner.RunnerError) as ctx:
            runner.run_task(self.root, "transport fails", adapter=failing)
        self.assertEqual(ctx.exception.status, "transport_error")
        self.assertEqual(self.read_receipt()["status"], "transport_error")
        self.assertTrue(os.path.exists(self.active_path))

        os.unlink(receipt)
        os.unlink(self.active_path)
        interrupted = runner.run_task(self.root, "interrupt", adapter=InterruptingAdapter())
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(runner.exit_code(interrupted), 130)
        self.assertEqual(self.read_receipt()["status"], "interrupted")
        recovery = FakeAdapter(
            resume_actions=[self.write_active, lambda: self.write_outcome("done")],
        )
        recovered = runner.resume_task(self.root, adapter=recovery)
        self.assertEqual(recovered["status"], "done")
        self.assertEqual(len(recovery.resumes), 2)


if __name__ == "__main__":
    unittest.main()
