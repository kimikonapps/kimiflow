import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from memory_router import lifecycle, rows, store
from memory_router.__main__ import main


class LifecycleCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kimiflow-lifecycle-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        self.learnings = os.path.join(self.project, "LEARNINGS.jsonl")
        self.usage = os.path.join(self.project, "MEMORY-USAGE.json")
        evidence_path = os.path.join(self.root, "src", "evidence.txt")
        os.makedirs(os.path.dirname(evidence_path))
        with open(evidence_path, "w", encoding="utf-8") as handle:
            handle.write("current evidence\n")
        self.evidence_ref = "src/evidence.txt"
        self.fingerprints = rows.evidence_fingerprints_json(self.root, [self.evidence_ref])

    def row(self, rid, verified, **overrides):
        value = {
            "id": rid,
            "kind": "learned",
            "topic": "memory",
            "summary": "Bounded local learning.",
            "status": "current",
            "confidence": "medium",
            "sensitivity": "normal",
            "last_verified": verified,
            "evidence": [self.evidence_ref],
            "evidence_fingerprints": self.fingerprints,
        }
        value.update(overrides)
        return value

    def write_rows(self, values, newline="\n"):
        with open(self.learnings, "w", encoding="utf-8", newline="") as handle:
            handle.write(newline.join(json.dumps(row, ensure_ascii=False) for row in values) + newline)

    def write_usage(self, items):
        with open(self.usage, "w", encoding="utf-8") as handle:
            json.dump({"items": items, "events": []}, handle)

    def run_lifecycle(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch("memory_router.lifecycle._refresh_derivatives"):
            code = lifecycle.run(["--root", self.root] + list(args))
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_preview_scores_and_strict_eligibility(self):
        self.write_rows([
            self.row("hot", "2999-01-01", confidence="high"),
            self.row("eligible", "2000-01-01", confidence="low"),
            self.row("used-stale", "2000-01-01"),
            self.row("fresh-unused", "2999-01-01"),
        ])
        self.write_usage({
            "learning:hot": {"kind": "learning", "use_count": 3},
            "learning:used-stale": {"kind": "learning", "use_count": 1},
        })
        code, result = self.run_lifecycle()
        self.assertEqual(code, 0)
        self.assertEqual(result["candidate_ids"], ["eligible"])
        scored = {item["id"]: item for item in result["rows"]}
        self.assertGreater(scored["hot"]["utility_points"], scored["eligible"]["utility_points"])
        self.assertFalse(scored["fresh-unused"]["quarantine_eligible"])
        self.assertFalse(scored["used-stale"]["quarantine_eligible"])
        self.assertLessEqual(len(result["rows"]), 20)
        self.assertEqual(result["utility_max_points"], 5)

    def test_write_quarantines_without_deletion_and_restore_is_evidence_gated(self):
        eligible = self.row("eligible", "2000-01-01", summary="left\u2028right")
        duplicate_a = self.row("duplicate", "2000-01-01", summary="first")
        duplicate_b = self.row("duplicate", "2000-01-01", summary="second")
        missing = self.row("unused", "2000-01-01")
        missing.pop("id")
        encoded = [json.dumps(row, ensure_ascii=False) for row in
                   (eligible, duplicate_a, duplicate_b, missing)]
        original = (encoded[0] + "\r\n" + encoded[1] + "\n" + encoded[2] + "\n" +
                    encoded[3] + "\nBROKEN\r\n[1,2]\n\nunterminated")
        with open(self.learnings, "w", encoding="utf-8", newline="") as handle:
            handle.write(original)
        self.write_usage({})
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["quarantined_ids"], ["eligible"])
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            after = handle.read()
        self.assertIn("left\u2028right", after)
        self.assertIn("}\r\n" + encoded[1], after)
        self.assertTrue(after.endswith("\n\nunterminated"))
        self.assertIn("\nBROKEN\r\n[1,2]\n", after)
        parsed = store.read_jsonl(self.learnings)
        self.assertEqual(len(parsed), 4)
        statuses = [(row.get("id"), row.get("status")) for row in parsed]
        self.assertIn(("eligible", "quarantined"), statuses)
        self.assertEqual(statuses.count(("duplicate", "current")), 2)
        self.assertIn((None, "current"), statuses)
        code, restored = self.run_lifecycle("--restore", "eligible", "--write")
        self.assertEqual(code, 0)
        self.assertEqual(restored["restored_id"], "eligible")
        self.assertEqual(next(row for row in store.read_jsonl(self.learnings)
                              if row.get("id") == "eligible")["status"], "current")
        code, duplicate = self.run_lifecycle("--restore", "duplicate", "--write")
        self.assertEqual(code, 1)
        self.assertEqual(duplicate["reason"], "not_quarantined")
        code, _ = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        with open(os.path.join(self.root, self.evidence_ref), "a", encoding="utf-8") as handle:
            handle.write("drift\n")
        code, drifted = self.run_lifecycle("--restore", "eligible", "--write")
        self.assertEqual(code, 1)
        self.assertEqual(drifted["reason"], "evidence_drift")

    def test_dispatch_and_no_delete_import_network_dependency_surface(self):
        self.write_rows([])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["lifecycle", "--root", self.root]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "preview")
        with open(lifecycle.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("urllib", "requests", "socket", "subprocess", "--delete", "--import"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
