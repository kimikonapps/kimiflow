import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import active_run, phase_reads, readiness


class ReadinessTests(unittest.TestCase):
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
        self.head = subprocess.check_output(["git", "-C", self.root, "rev-parse", "HEAD"], text=True).strip()
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "Flow schema: 4\nStatus: active\nMode: feature\nScope: small\nRecovery: clean\n"
                "Affected files:\n- README.md\nPhase reads required: yes\n"
                + "\n".join("Phase %s: open" % number for number in range(8))
                + "\n"
            )
        info = os.stat(self.run)
        active = {
            "schema_version": 1,
            "status": "active",
            "run": ".kimiflow/demo",
            "mode": "feature",
            "scope": "small",
            "host": "codex",
            "started_head": self.head,
            "last_checked_head": self.head,
            "run_device": info.st_dev,
            "run_inode": info.st_ino,
        }
        os.makedirs(os.path.dirname(active_run.active_file(self.root)), exist_ok=True)
        with open(active_run.active_file(self.root), "w", encoding="utf-8") as handle:
            json.dump(active, handle)
        plugin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        self.env = mock.patch.dict(os.environ, {"KIMIFLOW_PLUGIN_ROOT": plugin, "KIMIFLOW_HOST": "codex"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_readiness_uses_existing_authorities_and_is_deterministic(self):
        blocked = readiness.build(self.root)
        self.assertEqual(blocked["readiness"], "blocked")
        phase = next(row for row in blocked["gates"] if row["gate"] == "phase_reads")
        self.assertIn("phase_0_read_missing", phase["detail"])
        phase_reads.record_read(
            self.root,
            self.run,
            0,
            "phases/phase-0-setup.md",
            "2026-07-21T00:00:00Z",
            write=True,
        )
        first = readiness.build(self.root)
        second = readiness.build(self.root)
        self.assertEqual(first, second)
        self.assertEqual(first["readiness"], "ready")
        self.assertRegex(first["readiness_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        os.unlink(os.path.join(self.run, "PHASE-READS.json"))
        self.assertEqual(readiness.build(self.root)["readiness"], "blocked")

    def test_malformed_active_run_returns_bounded_blocked_readiness(self):
        with open(active_run.active_file(self.root), "w", encoding="utf-8") as handle:
            handle.write("{bad\n")

        value = readiness.build(self.root)

        self.assertEqual(value["readiness"], "blocked")
        self.assertEqual(value["gates"][0]["reason"], "active_run_malformed")

    def test_phase_seven_readiness_uses_current_conformance_basis(self):
        completed = mock.Mock(
            returncode=0,
            stdout="CONFORMANCE_GATE\tOPEN\tblockers=0\treason=clean\tdetail=\n",
        )
        phase_status = lambda _run, phase: "done" if phase == 6 else "open"
        with mock.patch.object(readiness, "_phase_status", side_effect=phase_status), mock.patch.object(
            readiness.subprocess, "run", return_value=completed
        ) as run_gate:
            row = readiness._run_gate("conformance", self.run, "feature")

        self.assertEqual(row["status"], "open")
        argv = run_gate.call_args.args[0]
        self.assertNotIn("--plan", argv)
        self.assertNotIn("--finish", argv)


if __name__ == "__main__":
    unittest.main()
