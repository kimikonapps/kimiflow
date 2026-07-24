import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kimiflow_core import phase_context, phase_reads


class PhaseContextTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        self.plugin = os.path.join(self.root, "plugin")
        os.makedirs(os.path.join(self.plugin, "phases"))
        self.old_plugin = os.environ.get("KIMIFLOW_PLUGIN_ROOT")
        os.environ["KIMIFLOW_PLUGIN_ROOT"] = self.plugin
        self.addCleanup(self.restore_plugin)
        context = {
            "required": ["STATE.md", "PLAN.md"],
            "feature": ["INTENT.md"],
            "fix": [],
            "audit": [],
            "optional": ["OPTIONAL.md"],
            "max_file_bytes": 8192,
            "max_total_bytes": 32768,
        }
        phases = []
        for number in range(8):
            rel = "phases/phase-%s.md" % number
            phases.append({"id": number, "name": "phase-%s" % number, "file": rel, "context": context})
            with open(os.path.join(self.plugin, rel), "w", encoding="utf-8") as handle:
                handle.write("phase instruction %s\n" % number)
        with open(os.path.join(self.plugin, "phases", "PHASES.json"), "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 3, "phases": phases}, handle)
        self.write("STATE.md", "Mode: feature\nPhase reads required: yes\n")
        self.write("PLAN.md", "private plan body\n")
        self.write("INTENT.md", "private intent body\n")
        phase_reads.record_read(self.root, self.run, 3, "phases/phase-3.md", "now", write=True)

    def restore_plugin(self):
        if self.old_plugin is None:
            os.environ.pop("KIMIFLOW_PLUGIN_ROOT", None)
        else:
            os.environ["KIMIFLOW_PLUGIN_ROOT"] = self.old_plugin

    def write(self, name, content):
        with open(os.path.join(self.run, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def test_phase_context_shadow_is_deterministic_and_non_authoritative(self):
        first = phase_context.compile_shadow(self.root, self.run, 3)
        second = phase_context.compile_shadow(self.root, self.run, 3)
        self.assertEqual(first, second)
        self.assertFalse(first["authoritative"])
        saved = phase_context.write_shadow(self.root, self.run, 3)
        with open(os.path.join(self.run, phase_context.SHADOW_NAME), encoding="utf-8") as handle:
            raw = handle.read()
        self.assertEqual(saved, json.loads(raw))
        self.assertNotIn("private plan body", raw)
        self.assertNotIn("private intent body", raw)
        self.assertEqual(phase_context.load_current_shadow(self.root, self.run, 3)["status"], "current")

        self.write("PLAN.md", "changed private plan\n")
        self.assertEqual(phase_context.load_current_shadow(self.root, self.run, 3)["status"], "stale")
        os.unlink(os.path.join(self.run, "PHASE-READS.json"))
        self.assertEqual(phase_reads.gate(self.root, self.run, 3)["status"], "CLOSED")

    def test_policy_and_source_drift_change_composite_basis(self):
        original = phase_context.compile_shadow(self.root, self.run, 3)["composite_basis"]
        self.write("OPTIONAL.md", "new source\n")
        with_source = phase_context.compile_shadow(self.root, self.run, 3)["composite_basis"]
        self.assertNotEqual(original, with_source)
        manifest = os.path.join(self.plugin, "phases", "PHASES.json")
        with open(manifest, encoding="utf-8") as handle:
            value = json.load(handle)
        value["phases"][3]["context"]["max_total_bytes"] += 1
        with open(manifest, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        with_policy = phase_context.compile_shadow(self.root, self.run, 3)["composite_basis"]
        self.assertNotEqual(with_source, with_policy)

    def test_stale_project_delta_context_is_not_selected(self):
        manifest = os.path.join(self.plugin, "phases", "PHASES.json")
        with open(manifest, encoding="utf-8") as handle:
            value = json.load(handle)
        value["phases"][3]["context"]["optional"].append(
            "PROJECT-DELTA-CONTEXT.md"
        )
        with open(manifest, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        self.write(
            "PROJECT-DELTA-CONTEXT.md",
            "<!--kimiflow:project-delta-context;schema=1;"
            "rows=111111111111111111111111;"
            "paths_sha256=1111111111111111111111111111111111111111111111111111111111111111;"
            "max_words=120-->\n",
        )

        shadow = phase_context.compile_shadow(self.root, self.run, 3)

        names = {row["name"] for row in shadow["selection"]}
        self.assertNotIn("PROJECT-DELTA-CONTEXT.md", names)

    def test_mode_selected_artifact_is_required(self):
        os.unlink(os.path.join(self.run, "INTENT.md"))
        with self.assertRaisesRegex(phase_context.PhaseContextError, "artifact_missing:INTENT.md"):
            phase_context.compile_shadow(self.root, self.run, 3)

    def test_audit_mode_requires_only_its_selected_artifacts(self):
        manifest = os.path.join(self.plugin, "phases", "PHASES.json")
        with open(manifest, encoding="utf-8") as handle:
            value = json.load(handle)
        value["phases"][3]["context"].update({
            "required": ["STATE.md"],
            "feature": ["INTENT.md", "PLAN.md"],
            "audit": ["AUDIT-INTENT.md", "AUDIT.md"],
        })
        with open(manifest, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        self.write("STATE.md", "Mode: audit\nPhase reads required: yes\n")
        self.write("AUDIT-INTENT.md", "audit target\n")
        with self.assertRaisesRegex(phase_context.PhaseContextError, "artifact_missing:AUDIT.md"):
            phase_context.compile_shadow(self.root, self.run, 3)
        self.write("AUDIT.md", "audit evidence\n")
        shadow = phase_context.compile_shadow(self.root, self.run, 3)
        names = {row["name"] for row in shadow["selection"]}
        self.assertIn("AUDIT-INTENT.md", names)
        self.assertIn("AUDIT.md", names)

    def test_repeated_same_timestamp_phase_read_stales_an_unreplaced_shadow(self):
        phase_context.write_shadow(self.root, self.run, 3)
        phase_reads.record_read(self.root, self.run, 3, "phases/phase-3.md", "now", write=True)

        self.assertEqual(phase_context.load_current_shadow(self.root, self.run, 3)["status"], "stale")

    def test_phase_read_receipt_counts_toward_total_byte_cap(self):
        self.write("PLAN.md", "p" * 4000)
        self.write("INTENT.md", "i" * 4000)
        manifest = os.path.join(self.plugin, "phases", "PHASES.json")
        with open(manifest, encoding="utf-8") as handle:
            value = json.load(handle)
        selected_bytes = sum(os.path.getsize(os.path.join(self.run, name)) for name in ("STATE.md", "PLAN.md", "INTENT.md"))
        phase_bytes = os.path.getsize(os.path.join(self.plugin, "phases", "phase-3.md"))
        value["phases"][3]["context"]["max_total_bytes"] = max(8192, selected_bytes + phase_bytes)
        with open(manifest, "w", encoding="utf-8") as handle:
            json.dump(value, handle)

        with self.assertRaisesRegex(phase_context.PhaseContextError, "context_total_oversize"):
            phase_context.compile_shadow(self.root, self.run, 3)

    def test_write_shadow_rejects_run_directory_exchange(self):
        moved = self.run + "-moved"
        original_compile = phase_context._compile_descriptor

        def exchange_after_compile(*args, **kwargs):
            value = original_compile(*args, **kwargs)
            os.rename(self.run, moved)
            os.mkdir(self.run)
            return value

        with mock.patch.object(phase_context, "_compile_descriptor", side_effect=exchange_after_compile):
            with self.assertRaisesRegex(phase_context.PhaseContextError, "run_identity_changed"):
                phase_context.write_shadow(self.root, self.run, 3)
        self.assertFalse(os.path.exists(os.path.join(self.run, phase_context.SHADOW_NAME)))

    def test_symlink_and_post_stat_exchange_are_rejected(self):
        plan = os.path.join(self.run, "PLAN.md")
        outside = os.path.join(self.root, "outside")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("outside secret\n")
        os.unlink(plan)
        os.symlink(outside, plan)
        with self.assertRaises(phase_context.PhaseContextError):
            phase_context.compile_shadow(self.root, self.run, 3)
        os.unlink(plan)
        self.write("PLAN.md", "safe\n")

        original_open = phase_context.os.open
        exchanged = {"done": False}

        def exchange_open(name, flags, *args, **kwargs):
            if name == "PLAN.md" and kwargs.get("dir_fd") is not None and not exchanged["done"]:
                exchanged["done"] = True
                os.unlink(plan)
                with open(plan, "w", encoding="utf-8") as handle:
                    handle.write("replacement\n")
            return original_open(name, flags, *args, **kwargs)

        with mock.patch.object(phase_context.os, "open", side_effect=exchange_open):
            with self.assertRaises(phase_context.PhaseContextError):
                phase_context.compile_shadow(self.root, self.run, 3)

    def test_file_snapshot_rejects_reused_inode_with_changed_metadata(self):
        path = os.path.join(self.run, "PLAN.md")
        before = os.stat(path, follow_symlinks=False)
        opened = mock.Mock(
            st_dev=before.st_dev,
            st_ino=before.st_ino,
            st_mode=before.st_mode,
            st_size=before.st_size + 1,
            st_mtime_ns=before.st_mtime_ns + 1,
            st_ctime_ns=before.st_ctime_ns + 1,
        )
        self.assertFalse(phase_context._same_file_snapshot(before, opened))

    def test_shadow_includes_only_manifest_selected_reference_sections(self):
        manifest = os.path.join(self.plugin, "phases", "PHASES.json")
        with open(manifest, encoding="utf-8") as handle:
            value = json.load(handle)
        value["phases"][3]["reference_sections"] = ["Selected"]
        with open(manifest, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        with open(os.path.join(self.plugin, "reference.md"), "w", encoding="utf-8") as handle:
            handle.write("## Selected\nsmall\n\n## Huge unused\n" + ("x" * 50000) + "\n")
        phase_reads.record_read(self.root, self.run, 3, "phases/phase-3.md", "later", write=True)
        shadow = phase_context.compile_shadow(self.root, self.run, 3)
        references = [row for row in shadow["selection"] if row["kind"] == "reference"]
        self.assertEqual([row["name"] for row in references], ["Selected"])
        self.assertLess(references[0]["bytes"], 100)


if __name__ == "__main__":
    unittest.main()
