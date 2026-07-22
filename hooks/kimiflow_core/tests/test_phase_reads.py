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
        self.old_plugin_root = os.environ.get("KIMIFLOW_PLUGIN_ROOT")
        os.environ["KIMIFLOW_PLUGIN_ROOT"] = self.root
        self.addCleanup(self.restore_plugin_root)
        self.write_manifest()
        self.write_phase_files()

    def restore_plugin_root(self):
        if self.old_plugin_root is None:
            os.environ.pop("KIMIFLOW_PLUGIN_ROOT", None)
        else:
            os.environ["KIMIFLOW_PLUGIN_ROOT"] = self.old_plugin_root

    def write_manifest(self):
        os.makedirs(os.path.join(self.root, "phases"), exist_ok=True)
        context = {
            "required": ["STATE.md"],
            "feature": [],
            "fix": [],
            "audit": [],
            "optional": [],
            "max_file_bytes": 4096,
            "max_total_bytes": 8192,
        }
        phases = [{"id": idx, "name": "p%s" % idx, "file": "phases/phase-%s.md" % idx, "context": context} for idx in range(8)]
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

    def test_manifest_context_policy_is_strict_and_preserved(self):
        entry = phase_reads.phase_entry(self.root, 1)
        self.assertEqual(entry["context"]["required"], ["STATE.md"])
        self.assertEqual(entry["context"]["max_total_bytes"], 8192)
        path = os.path.join(self.root, "phases", "PHASES.json")
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        value["phases"][1]["context"]["optional"] = ["../outside"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        with self.assertRaises(phase_reads.PhaseReadError):
            phase_reads.load_manifest(self.root)

    def test_context_artifact_may_be_shared_by_mutually_exclusive_modes(self):
        path = os.path.join(self.root, "phases", "PHASES.json")
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        context = value["phases"][1]["context"]
        context["feature"] = ["PLAN.md"]
        context["fix"] = ["PLAN.md"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        entry = phase_reads.phase_entry(self.root, 1)
        self.assertEqual(entry["context"]["feature"], ["PLAN.md"])
        self.assertEqual(entry["context"]["fix"], ["PLAN.md"])

    def test_context_artifact_cannot_duplicate_required_or_optional(self):
        path = os.path.join(self.root, "phases", "PHASES.json")
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        value["phases"][1]["context"]["feature"] = ["STATE.md"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        with self.assertRaisesRegex(phase_reads.PhaseReadError, "duplicate context artifact STATE.md"):
            phase_reads.load_manifest(self.root)

    def test_reference_section_hash_is_recorded_and_staleness_closes_gate(self):
        manifest = os.path.join(self.root, "phases", "PHASES.json")
        with open(manifest, encoding="utf-8") as handle:
            value = json.load(handle)
        value["phases"][0]["reference_sections"] = ["Setup contract"]
        with open(manifest, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        with open(os.path.join(self.root, "reference.md"), "w", encoding="utf-8") as handle:
            handle.write("# Reference\n\n## Setup contract\nBounded setup rules.\n\n## Other\nNot selected.\n")
        self.require_phase_reads()
        record = phase_reads.record_read(self.root, self.run, 0, "phases/phase-0.md", "now", write=True)
        self.assertEqual([row["name"] for row in record["reference_sections"]], ["Setup contract"])
        self.assertEqual(phase_reads.gate(self.root, self.run, 0)["status"], "OPEN")
        with open(os.path.join(self.root, "reference.md"), "a", encoding="utf-8") as handle:
            handle.write("changed selected section boundary\n")
        # The append is under Other and must not invalidate Setup.
        self.assertEqual(phase_reads.gate(self.root, self.run, 0)["status"], "OPEN")
        with open(os.path.join(self.root, "reference.md"), "w", encoding="utf-8") as handle:
            handle.write("# Reference\n\n## Setup contract\nChanged setup rules.\n\n## Other\nNot selected.\n")
        verdict = phase_reads.gate(self.root, self.run, 0)
        self.assertEqual(verdict["status"], "CLOSED")
        self.assertIn("phase_0_reference_stale", verdict["detail"])

    def test_reference_section_must_be_unique(self):
        with open(os.path.join(self.root, "reference.md"), "w", encoding="utf-8") as handle:
            handle.write("## Duplicate\none\n## Duplicate\ntwo\n")
        with self.assertRaisesRegex(phase_reads.PhaseReadError, "exactly once"):
            phase_reads.reference_section_bytes("Duplicate")

    def test_reference_section_ignores_template_headings_inside_fences(self):
        with open(os.path.join(self.root, "reference.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "## Selected\nRules.\n```md\n## Template heading\n```\nStill selected.\n\n"
                "## Next\nNot selected.\n"
            )
        payload = phase_reads.reference_section_bytes("Selected").decode("utf-8")
        self.assertIn("## Template heading", payload)
        self.assertIn("Still selected.", payload)
        self.assertNotIn("Not selected.", payload)


if __name__ == "__main__":
    unittest.main()
