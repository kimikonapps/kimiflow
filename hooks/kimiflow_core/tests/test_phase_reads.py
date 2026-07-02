import json
import os
import shutil
import tempfile
import unittest

from kimiflow_core import phase_reads


class TestPhaseReads(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        self.addCleanup(shutil.rmtree, self.root)
        self.write_manifest()
        self.write_phase_files()

    def write_manifest(self):
        os.makedirs(os.path.join(self.root, "phases"), exist_ok=True)
        phases = [{"id": idx, "name": "p%s" % idx, "file": "phases/phase-%s.md" % idx} for idx in range(8)]
        with open(os.path.join(self.root, "phases", "PHASES.json"), "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "phases": phases}, handle)

    def write_phase_files(self):
        for idx in range(8):
            with open(os.path.join(self.root, "phases", "phase-%s.md" % idx), "w", encoding="utf-8") as handle:
                handle.write("phase %s\n" % idx)

    def require_phase_reads(self):
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Phase reads required: yes\n")

    def test_record_read_writes_hash(self):
        record = phase_reads.record_read(self.root, self.run, 1, "phases/phase-1.md", "now", write=True)
        self.assertEqual(record["phase"], 1)
        self.assertTrue(record["sha256"].startswith("sha256:"))
        with open(os.path.join(self.run, "PHASE-READS.json"), "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["reads"]["1"]["file"], "phases/phase-1.md")

    def test_traversal_refused(self):
        with self.assertRaises(phase_reads.PhaseReadError):
            phase_reads.record_read(self.root, self.run, 1, "../phase-1.md", "now")

    def test_symlink_escape_refused(self):
        outside = os.path.join(self.root, "outside.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("outside\n")
        target = os.path.join(self.root, "phases", "phase-1.md")
        os.unlink(target)
        os.symlink(outside, target)
        with self.assertRaises(phase_reads.PhaseReadError):
            phase_reads.record_read(self.root, self.run, 1, "phases/phase-1.md", "now")

    def test_gate_legacy_opens_without_marker(self):
        verdict = phase_reads.gate(self.root, self.run, 1)
        self.assertEqual(verdict["status"], "OPEN")
        self.assertEqual(verdict["reason"], "legacy")

    def test_gate_closes_missing_read(self):
        self.require_phase_reads()
        phase_reads.record_read(self.root, self.run, 0, "phases/phase-0.md", "now", write=True)
        verdict = phase_reads.gate(self.root, self.run, 1)
        self.assertEqual(verdict["status"], "CLOSED")
        self.assertIn("phase_1_read_missing", verdict["detail"])

    def test_gate_closes_stale_hash(self):
        self.require_phase_reads()
        phase_reads.record_read(self.root, self.run, 0, "phases/phase-0.md", "now", write=True)
        with open(os.path.join(self.root, "phases", "phase-0.md"), "a", encoding="utf-8") as handle:
            handle.write("changed\n")
        verdict = phase_reads.gate(self.root, self.run, 0)
        self.assertEqual(verdict["status"], "CLOSED")
        self.assertIn("phase_0_read_stale", verdict["detail"])

    def test_gate_opens_when_required_reads_are_fresh(self):
        self.require_phase_reads()
        phase_reads.record_read(self.root, self.run, 0, "phases/phase-0.md", "now", write=True)
        phase_reads.record_read(self.root, self.run, 1, "phases/phase-1.md", "now", write=True)
        verdict = phase_reads.gate(self.root, self.run, 1)
        self.assertEqual(verdict["status"], "OPEN")

    def test_wrong_manifest_file_for_phase_refused(self):
        with self.assertRaises(phase_reads.PhaseReadError):
            phase_reads.record_read(self.root, self.run, 1, "phases/phase-2.md", "now")


if __name__ == "__main__":
    unittest.main()
