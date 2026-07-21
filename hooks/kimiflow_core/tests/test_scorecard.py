import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import active_run, scorecard


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Mode: feature\n")

    def write_json(self, name, value):
        with open(os.path.join(self.run, name), "w", encoding="utf-8") as handle:
            json.dump(value, handle)

    def test_scorecard_is_derived_multidimensional_and_private(self):
        self.write_json(
            "OUTCOME-EVALUATION.json",
            {
                "terminal": "done",
                "classification": "verified_success",
                "promotable": True,
                "run": "/private/repository",
                "terms": ["secret prompt"],
                "strategy": {"summary": "private strategy", "evidence_id": "out_secret"},
                "signals": {
                    "recovery": "clean",
                    "verification": {"outcome": "passed", "criteria": "passed", "regression": "passed"},
                    "code_review": "clean",
                    "first_plan_success": True,
                },
                "economics": {"result": "saving", "confidence": "medium", "net_estimated_tokens_saved": float("inf")},
            },
        )
        self.write_json(
            "EXECUTION-TRACE.json",
            {
                "summary": {
                    "work_units": 7,
                    "no_progress_streak": 0,
                    "strategy_mode": "normal",
                    "budget_pressure": "normal",
                    "usage": {"model_calls": 2, "tool_calls": 9},
                }
            },
        )
        self.write_json("RUN-LIFECYCLE.json", {"learning": {"status": "recorded"}, "paths": {"secret": "/private/path"}})
        shadow = {"status": "current", "selected_count": 4, "total_bytes": 1200, "estimated_tokens": 300}
        with mock.patch("kimiflow_core.scorecard.phase_context.load_current_shadow", return_value=shadow):
            value = scorecard.build(self.root, self.run, terminal="done")
            written = scorecard.write(self.root, self.run, terminal="done")
        self.assertEqual(value, written)
        self.assertEqual(set(value["dimensions"]), {"outcome", "quality", "efficiency", "autonomy", "context"})
        self.assertNotIn("overall", value)
        self.assertIsNone(value["dimensions"]["efficiency"]["net_estimated_tokens_saved"])
        raw = json.dumps(value, sort_keys=True)
        for forbidden in ("/private/repository", "/private/path", "secret prompt", "private strategy", "out_secret", "owner", "session", "thread"):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.run, scorecard.SCORECARD_NAME)).st_mode), 0o600)

    def test_missing_boolean_evidence_and_authoritative_negative_enums(self):
        self.write_json(
            "OUTCOME-EVALUATION.json",
            {
                "terminal": "failed",
                "classification": "verified_failure",
                "signals": {"code_review": "blocking"},
                "economics": {"result": "waste", "confidence": "low"},
            },
        )

        value = scorecard.build(self.root, self.run, terminal="failed")

        self.assertEqual(value["dimensions"]["outcome"]["promotable"], "inconclusive")
        self.assertEqual(value["dimensions"]["autonomy"]["first_plan_success"], "inconclusive")
        self.assertEqual(value["dimensions"]["quality"]["code_review"], "blocking")
        self.assertEqual(value["dimensions"]["efficiency"]["economics_result"], "waste")

    def test_duplicate_critical_json_keys_fail_closed(self):
        with open(os.path.join(self.run, "OUTCOME-EVALUATION.json"), "w", encoding="utf-8") as handle:
            handle.write('{"classification":"verified_success","classification":"verified_failure"}\n')

        with self.assertRaisesRegex(scorecard.ScorecardError, "malformed_input"):
            scorecard.build(self.root, self.run)

    def test_supplied_run_descriptor_is_reused_for_context_projection(self):
        descriptor = os.open(self.run, os.O_RDONLY)
        self.addCleanup(os.close, descriptor)
        moved = self.run + "-moved"
        os.rename(self.run, moved)
        os.mkdir(self.run)
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Mode: feature\n")
        shadow = {"status": "current", "selected_count": 3, "total_bytes": 99, "estimated_tokens": 25}

        with mock.patch.object(scorecard.phase_context, "load_current_shadow", return_value=shadow) as load_shadow:
            value = scorecard.build(self.root, self.run, run_descriptor=descriptor)

        self.assertEqual(value["dimensions"]["context"]["total_bytes"], 99)
        self.assertEqual(load_shadow.call_args.kwargs["run_descriptor"], descriptor)


class TerminalScorecardIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Kimiflow Test"], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "kimiflow@example.test"], check=True)
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("fixture\n")
        subprocess.run(["git", "-C", self.root, "add", "README.md"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "fixture"], check=True)
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        self.env = mock.patch.dict(os.environ, {"KIMIFLOW_HOST": "codex", "CODEX_THREAD_ID": "owner"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def activate(self, terminal):
        os.makedirs(self.run, exist_ok=True)
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 4\nStatus: active\nMode: feature\nScope: small\nAffected files:\n- README.md\n")
        self.write_outcome(terminal)
        head = subprocess.check_output(["git", "-C", self.root, "rev-parse", "HEAD"], text=True).strip()
        info = os.stat(self.run)
        value = {
            "schema_version": 1,
            "status": "active",
            "run": ".kimiflow/demo",
            "host": "codex",
            "mode": "feature",
            "scope": "small",
            "started_head": head,
            "last_checked_head": head,
            "run_device": info.st_dev,
            "run_inode": info.st_ino,
            "owner": {"host": "codex", "session_id": "owner"},
        }
        os.makedirs(os.path.dirname(active_run.active_file(self.root)), exist_ok=True)
        with open(active_run.active_file(self.root), "w", encoding="utf-8") as handle:
            json.dump(value, handle)

    def write_outcome(self, terminal):
        with open(os.path.join(self.run, "OUTCOME-EVALUATION.json"), "w", encoding="utf-8") as handle:
            json.dump({"terminal": terminal, "classification": "inconclusive", "promotable": False, "signals": {}, "economics": {}}, handle)

    def test_control_plane_is_wired_into_phase_reads_finish_and_host_smokes(self):
        for command, terminal in (("park", "parked"), ("fail", "failed"), ("abort", "aborted")):
            with self.subTest(command=command):
                self.activate(terminal)
                with mock.patch.object(active_run, "evaluate_terminal_outcome", return_value=({"status": "evaluated", "terminal": terminal}, 0, "")):
                    rc = active_run.main([command, "--root", self.root, "--reason", "fixture", "--write"])
                self.assertEqual(rc, 0)
                with open(os.path.join(self.run, scorecard.SCORECARD_NAME), encoding="utf-8") as handle:
                    value = json.load(handle)
                self.assertEqual(value["terminal"], terminal)
                for name in ("SESSION-OUTCOME.json", scorecard.SCORECARD_NAME):
                    try:
                        os.unlink(os.path.join(self.run, name))
                    except FileNotFoundError:
                        pass

    def test_finish_snapshot_restores_scorecard_bytes(self):
        self.activate("done")
        before = b'{"prior":true}\n'
        with open(os.path.join(self.run, scorecard.SCORECARD_NAME), "wb") as handle:
            handle.write(before)
        active = active_run.load_active(self.root)
        snapshot = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, snapshot, ignore_errors=True)
        with active_run.pinned_terminal_run(self.run, active) as pinned:
            active_run.snapshot_finish(self.root, self.run, snapshot, run_descriptor=pinned["run_descriptor"])
            scorecard.write(self.root, self.run, terminal="done", run_descriptor=pinned["run_descriptor"])
            active_run.restore_finish(self.root, self.run, snapshot, run_descriptor=pinned["run_descriptor"])
        with open(os.path.join(self.run, scorecard.SCORECARD_NAME), "rb") as handle:
            self.assertEqual(handle.read(), before)


if __name__ == "__main__":
    unittest.main()
