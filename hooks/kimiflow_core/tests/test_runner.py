import json
import os
import stat
import subprocess
import tempfile
import unittest

from kimiflow_core import runner


THREAD = "019f5fa0-567a-70e0-9b07-604ffbdafbf4"


class FakeAdapter:
    def __init__(self, start_action=None, resume_actions=None, returncode=0):
        self.start_action = start_action
        self.resume_actions = list(resume_actions or [])
        self.returncode = returncode
        self.starts = []
        self.resumes = []

    def start(self, root, prompt, on_thread):
        self.starts.append((root, prompt))
        on_thread(THREAD)
        if self.start_action:
            self.start_action()
        return runner.TurnResult(returncode=self.returncode, thread_id=THREAD)

    def resume(self, root, thread_id, prompt, on_thread):
        self.resumes.append((root, thread_id, prompt))
        if self.resume_actions:
            action = self.resume_actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            action()
        return runner.TurnResult(returncode=self.returncode, thread_id=thread_id)


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
        self.assertNotIn("secret task text", json.dumps(receipt))
        self.assertEqual(stat.S_IMODE(os.stat(runner.receipt_path(self.root)).st_mode), 0o600)

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
