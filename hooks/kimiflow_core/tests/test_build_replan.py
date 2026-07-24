import json
import os
import shutil
import subprocess
import tempfile
import unittest

from kimiflow_core import build_replan


class TestBuildReplan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Kimiflow Test"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "config", "user.email", "kimiflow@example.test"],
            check=True,
        )
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        os.makedirs(os.path.join(self.root, "src"))
        self.source = os.path.join(self.root, "src", "app.py")
        with open(self.source, "w", encoding="utf-8") as handle:
            handle.write("VALUE = 1\n")
        with open(os.path.join(self.run, "PLAN.md"), "w", encoding="utf-8") as handle:
            handle.write("Decision D1: use the current dependency.\nAC D1: AC-1\n")
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "Status: active\nPhase 5: in-progress\nAffected files:\n- src/app.py\n"
            )
        with open(os.path.join(self.run, "REPLAN-EVIDENCE.md"), "w", encoding="utf-8") as handle:
            handle.write("The installed dependency rejects the required API.\n")
        subprocess.run(["git", "-C", self.root, "add", "."], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "fixture"], check=True)

    def record(self):
        return build_replan.record_receipt(
            self.run,
            event="architecture_falsified",
            decision="D1",
            acceptance="AC-1",
            assumption="The dependency supports atomic replacement.",
            falsifier="The supported API returns ENOTSUP on the target.",
            evidence="REPLAN-EVIDENCE.md",
            paths=["src/app.py"],
            write=True,
        )

    def test_phase5_replan_requires_current_receipt(self):
        receipt = self.record()
        self.assertEqual(receipt["event"], "architecture_falsified")
        self.assertTrue(build_replan.verify_receipt(self.run, "architecture_falsified")["valid"])

        with open(self.source, "a", encoding="utf-8") as handle:
            handle.write("VALUE = 2\n")
        verdict = build_replan.verify_receipt(self.run, "architecture_falsified")
        self.assertFalse(verdict["valid"])
        self.assertEqual(verdict["reason"], "worktree_changed")

    def test_receipt_rejects_wrong_event_and_non_run_evidence(self):
        self.record()
        self.assertFalse(build_replan.verify_receipt(self.run, "strategy_drift")["valid"])
        with self.assertRaises(build_replan.BuildReplanError):
            build_replan.record_receipt(
                self.run,
                event="research_stale",
                decision="D1",
                acceptance="AC-1",
                assumption="old",
                falsifier="new",
                evidence="../../README.md",
                paths=["src/app.py"],
                write=False,
            )

    def test_receipt_is_strict_json_and_current_phase_five_only(self):
        self.record()
        receipt_path = os.path.join(self.run, "BUILD-REPLAN-EVIDENCE.json")
        with open(receipt_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["schema_version"], 1)
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Status: active\nPhase 5: done\nAffected files:\n- src/app.py\n")
        self.assertEqual(
            build_replan.verify_receipt(self.run, "architecture_falsified")["reason"],
            "phase_not_build",
        )

    def test_receipt_rejects_non_string_affected_path_without_exception(self):
        self.record()
        receipt_path = os.path.join(self.run, "BUILD-REPLAN-EVIDENCE.json")
        with open(receipt_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["affected_paths"] = [{"path": "src/app.py"}]
        with open(receipt_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

        verdict = build_replan.verify_receipt(
            self.run, "architecture_falsified"
        )

        self.assertFalse(verdict["valid"])
        self.assertEqual(verdict["reason"], "affected_paths_invalid")


if __name__ == "__main__":
    unittest.main()
