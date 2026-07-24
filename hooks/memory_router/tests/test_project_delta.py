import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import project_delta


class TestProjectDelta(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Kimiflow Test"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "config", "user.email", "kimiflow@example.test"],
            check=True,
        )
        os.makedirs(os.path.join(self.root, "src"))
        self.source = os.path.join(self.root, "src", "core.py")
        with open(self.source, "w", encoding="utf-8") as handle:
            handle.write("VALUE = 1\n")
        subprocess.run(["git", "-C", self.root, "add", "src/core.py"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "base"], check=True)
        self.started = self.git("rev-parse", "HEAD")

        with open(self.source, "w", encoding="utf-8") as handle:
            handle.write("VALUE = 2\n")
        subprocess.run(["git", "-C", self.root, "add", "src/core.py"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "architecture"], check=True)

        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        self.write_terminal_state()
        with open(os.path.join(self.run, "VERIFICATION.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "# Verification\n"
                "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->\n"
                "<!-- kimiflow:conformance contract=1 status=converged diff=passed "
                "strategy=passed architecture=passed research=stable scope=passed "
                "decisions=1 checks=1 verifier=independent source=current-run -->\n"
            )

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.root] + list(args), text=True).strip()

    def write_terminal_state(self, status="done", phase7="done"):
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "Status: %s\nPhase 6: done\nPhase 7: %s\nRun started head: %s\n"
                "Affected files:\n- src/core.py\n" % (status, phase7, self.started)
            )

    def record(self):
        return project_delta.record_delta(
            self.run,
            summary="Storage writes now use one transaction boundary.",
            invariants=["All storage writes pass through TransactionManager."],
            paths=["src/core.py"],
            write=True,
        )

    def test_project_delta_selects_only_current_intersections(self):
        row = self.record()
        self.assertEqual(row["status"], "verified")
        log = os.path.join(self.root, ".kimiflow", "project", "PROJECT-DELTAS.jsonl")
        self.assertTrue(os.path.isfile(log))

        selected = project_delta.render_context(
            self.run, ["src"], write=True, max_rows=4, max_words=120
        )
        self.assertEqual(selected["selected"], 1)
        context = os.path.join(self.run, "PROJECT-DELTA-CONTEXT.md")
        self.assertTrue(os.path.isfile(context))
        with open(context, encoding="utf-8") as handle:
            self.assertIn("TransactionManager", handle.read())

        unrelated = project_delta.render_context(
            self.run, ["tests/unit.py"], write=True, max_rows=4, max_words=120
        )
        self.assertEqual(unrelated["selected"], 0)
        self.assertFalse(os.path.exists(context))

    def test_changed_governed_path_or_evidence_makes_delta_stale(self):
        self.record()
        rendered = project_delta.render_context(
            self.run, ["src/core.py"], write=True, max_words=120
        )
        payload = rendered["markdown"].encode("utf-8")
        self.assertTrue(
            project_delta.context_payload_current(
                self.root, payload, ["src/core.py"]
            )
        )
        self.assertFalse(
            project_delta.context_payload_current(
                self.root, payload, ["tests/unit.py"]
            )
        )
        expanded = project_delta.render_context(
            self.run,
            ["src/core.py", "tests/unit.py"],
            max_words=120,
        )["markdown"].encode("utf-8")
        self.assertFalse(
            project_delta.context_payload_current(
                self.root, expanded, ["tests/unit.py"]
            )
        )
        with open(self.source, "w", encoding="utf-8") as handle:
            handle.write("VALUE = 3\n")
        subprocess.run(["git", "-C", self.root, "add", "src/core.py"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "later"], check=True)
        self.assertEqual(project_delta.render_context(self.run, ["src/core.py"])["selected"], 0)
        self.assertFalse(
            project_delta.context_payload_current(
                self.root, payload, ["src/core.py"]
            )
        )

    def test_edited_delta_content_with_original_id_is_stale(self):
        self.record()
        log = os.path.join(
            self.root, ".kimiflow", "project", "PROJECT-DELTAS.jsonl"
        )
        with open(log, encoding="utf-8") as handle:
            row = json.loads(handle.read())
        row["invariants"] = ["Forged invariant."]
        with open(log, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        rendered = project_delta.render_context(self.run, ["src/core.py"])
        self.assertEqual(rendered["selected"], 0)
        self.assertNotIn("Forged invariant", rendered["markdown"])

    def test_non_string_persisted_path_is_stale_without_exception(self):
        self.record()
        log = os.path.join(
            self.root, ".kimiflow", "project", "PROJECT-DELTAS.jsonl"
        )
        with open(log, encoding="utf-8") as handle:
            row = json.loads(handle.read())
        row["paths"] = [{"path": "src/core.py"}]
        content = dict(row)
        content.pop("id")
        row["id"] = project_delta._sha(
            project_delta._canonical(content).encode("utf-8")
        )[:24]
        with open(log, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

        rendered = project_delta.render_context(self.run, ["src/core.py"])
        self.assertEqual(rendered["selected"], 0)

    def test_persisted_evidence_count_and_duplicates_are_bounded(self):
        self.record()
        log = os.path.join(
            self.root, ".kimiflow", "project", "PROJECT-DELTAS.jsonl"
        )
        with open(log, encoding="utf-8") as handle:
            row = json.loads(handle.read())
        row["evidence"] = row["evidence"] * (project_delta.MAX_ROW_EVIDENCE + 1)
        content = dict(row)
        content.pop("id")
        row["id"] = project_delta._sha(
            project_delta._canonical(content).encode("utf-8")
        )[:24]
        with open(log, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

        rendered = project_delta.render_context(self.run, ["src/core.py"])
        self.assertEqual(rendered["selected"], 0)

    def test_non_string_persisted_evidence_path_is_stale(self):
        self.record()
        log = os.path.join(
            self.root, ".kimiflow", "project", "PROJECT-DELTAS.jsonl"
        )
        with open(log, encoding="utf-8") as handle:
            row = json.loads(handle.read())
        row["evidence"][0]["path"] = {"path": row["evidence"][0]["path"]}
        content = dict(row)
        content.pop("id")
        row["id"] = project_delta._sha(
            project_delta._canonical(content).encode("utf-8")
        )[:24]
        with open(log, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

        rendered = project_delta.render_context(self.run, ["src/core.py"])
        self.assertEqual(rendered["selected"], 0)

    def test_append_cannot_cross_reader_log_byte_cap(self):
        oversized = {
            "id": "old",
            "padding": "x" * project_delta.MAX_LOG_BYTES,
        }
        with mock.patch.object(
            project_delta, "_read_rows", return_value=[oversized]
        ), mock.patch.object(project_delta.store, "atomic_write") as atomic_write:
            with self.assertRaisesRegex(project_delta.ProjectDeltaError, "log too large"):
                self.record()
        atomic_write.assert_not_called()

    def test_project_delta_record_requires_verified_commit(self):
        self.write_terminal_state(status="active", phase7="in-progress")
        with self.assertRaises(project_delta.ProjectDeltaError):
            self.record()
        self.write_terminal_state()
        with open(os.path.join(self.run, "VERIFICATION.md"), "w", encoding="utf-8") as handle:
            handle.write("tests looked fine\n")
        with self.assertRaises(project_delta.ProjectDeltaError):
            self.record()

    def test_record_rejects_more_than_current_row_path_cap(self):
        with self.assertRaisesRegex(project_delta.ProjectDeltaError, "path limit"):
            project_delta.record_delta(
                self.run,
                summary="Too broad.",
                invariants=["Still bounded."],
                paths=[
                    "src/generated/%04d.py" % index
                    for index in range(project_delta.MAX_ROW_PATHS + 1)
                ],
                write=False,
            )

    def test_project_delta_rejects_unchanged_or_missing_governed_paths(self):
        with open(os.path.join(self.run, "STATE.md"), "a", encoding="utf-8") as handle:
            handle.write("- src/missing.py\n")
        with self.assertRaises(project_delta.ProjectDeltaError):
            project_delta.record_delta(
                self.run,
                summary="Architecture changed.",
                invariants=["Both governed paths must be current."],
                paths=["src/core.py", "src/missing.py"],
                write=True,
            )
        self.assertFalse(
            os.path.exists(
                os.path.join(self.root, ".kimiflow", "project", "PROJECT-DELTAS.jsonl")
            )
        )

    def test_project_delta_accepts_not_applicable_conformance_and_rejects_symlink_blob(self):
        verification = os.path.join(self.run, "VERIFICATION.md")
        with open(verification, "r", encoding="utf-8") as handle:
            text = handle.read()
        with open(verification, "w", encoding="utf-8") as handle:
            handle.write(
                text.replace(
                    "architecture=passed research=stable",
                    "architecture=not_applicable research=not_applicable",
                )
            )
        self.assertEqual(self.record()["status"], "verified")

        log = os.path.join(self.root, ".kimiflow", "project", "PROJECT-DELTAS.jsonl")
        os.unlink(log)
        os.unlink(self.source)
        os.symlink("target.py", self.source)
        subprocess.run(["git", "-C", self.root, "add", "src/core.py"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "symlink"], check=True)
        with self.assertRaises(project_delta.ProjectDeltaError):
            self.record()
        self.assertFalse(os.path.exists(log))

    def test_project_delta_rejects_unknown_conformance_verifier(self):
        verification = os.path.join(self.run, "VERIFICATION.md")
        with open(verification, encoding="utf-8") as handle:
            text = handle.read()
        with open(verification, "w", encoding="utf-8") as handle:
            handle.write(text.replace("verifier=independent", "verifier=bogus"))
        with self.assertRaises(project_delta.ProjectDeltaError):
            self.record()
        log = os.path.join(self.root, ".kimiflow", "project", "PROJECT-DELTAS.jsonl")
        self.assertFalse(os.path.exists(log))

    def test_selection_is_bounded_and_never_uses_cross_project_state(self):
        self.record()
        preview = project_delta.render_context(
            self.run, ["src/core.py"], max_rows=1, max_words=12
        )
        self.assertLessEqual(preview["selected"], 1)
        self.assertLessEqual(len(preview["markdown"].split()), 12)
        self.assertNotIn(str(self.root), preview["markdown"])

    def test_selector_receipt_is_size_bounded(self):
        self.record()
        selectors = ["src/core.py"] + [
            "docs/%03d-%s.md" % (index, "x" * 180)
            for index in range(600)
        ]
        preview = project_delta.render_context(
            self.run, selectors, max_rows=1, max_words=12
        )
        self.assertEqual(preview["selected"], 1)
        self.assertLess(len(preview["markdown"].encode("utf-8")), 2048)
        self.assertLessEqual(len(preview["markdown"].split()), 12)

    def test_markdown_body_has_byte_budget(self):
        row = {
            "id": "a" * 24,
            "paths": [
                "src/%03d-%s.py" % (index, "x" * 180)
                for index in range(600)
            ],
            "summary": "ok",
            "invariants": ["stable"],
            "commit": "b" * 40,
        }
        markdown = project_delta._bounded_markdown([row], ["src"], 12)
        self.assertLess(len(markdown.encode("utf-8")), 2048)
        self.assertLessEqual(len(markdown.split()), 12)

    def test_context_receipt_rejects_more_than_renderer_row_cap(self):
        rows = [
            {
                "id": "%024x" % index,
                "paths": ["src/core.py"],
                "summary": "ok",
                "invariants": ["stable"],
                "commit": "b" * 40,
            }
            for index in range(33)
        ]
        payload = project_delta._bounded_markdown(
            rows, ["src/core.py"], 120
        ).encode("utf-8")
        self.assertFalse(
            project_delta.context_payload_current(
                self.root, payload, ["src/core.py"]
            )
        )


if __name__ == "__main__":
    unittest.main()
