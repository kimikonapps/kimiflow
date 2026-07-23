import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from memory_router import memory_md, store

ISO = "2026-06-29T00:00:00Z"


class MemoryMdBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project, exist_ok=True)
        p = mock.patch("memory_router.clock.iso_now", return_value=ISO)
        p.start()
        self.addCleanup(p.stop)
        # Guarantee defaults regardless of ambient environment.
        env = mock.patch.dict(os.environ, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for var in ("KIMIFLOW_MEMORY_BUDGET", "KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS",
                    "KIMIFLOW_USER_MEMORY_BUDGET"):
            os.environ.pop(var, None)

    def write_rows(self, name, rows):
        path = os.path.join(self.project, name)
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return path

    def read_md(self, name):
        with open(os.path.join(self.project, name), encoding="utf-8") as fh:
            return fh.read()

    def bullets(self, name):
        return [ln for ln in self.read_md(name).splitlines() if ln.startswith("- ")]


class WriteBoundedMemoryCase(MemoryMdBase):
    def test_no_learnings_file_writes_nothing(self):
        memory_md.write_bounded_memory(self.root)
        self.assertFalse(os.path.isfile(os.path.join(self.project, "MEMORY.md")))

    def test_basic_render_header_and_bullet(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "a", "topic": "build flow", "kind": "pattern",
             "summary": "we fixed the build", "evidence": ["src/foo.py:5"],
             "confidence": "high", "status": "current"},
        ])
        memory_md.write_bounded_memory(self.root)
        md = self.read_md("MEMORY.md")
        self.assertTrue(md.startswith("# Project Memory\n\nGenerated: " + ISO + "\n"))
        self.assertIn("Policy: bounded always-on summary prioritized by use", md)
        self.assertIn("## Always-On Learnings\n\n", md)
        self.assertIn("- [build flow \u00b7 pattern] we fixed the build (evidence: src/foo.py:5)", md)
        self.assertTrue(md.endswith("\n"))

    def test_empty_body_fallback(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "a", "topic": "t", "summary": "s", "status": "superseded"},
        ])
        memory_md.write_bounded_memory(self.root)
        md = self.read_md("MEMORY.md")
        self.assertIn("No publish-safe always-on learnings yet. Use LEARNINGS.jsonl recall on demand.", md)
        self.assertEqual(self.bullets("MEMORY.md"), [])

    def test_filters_status_and_sensitivity(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "ok", "topic": "keep", "summary": "s", "status": "current"},
            {"id": "old", "topic": "drop1", "summary": "s", "status": "superseded"},
            {"id": "sec", "topic": "drop2", "summary": "s", "status": "current", "sensitivity": "security"},
            {"id": "prv", "topic": "drop3", "summary": "s", "status": "current", "sensitivity": "private"},
            {"id": "probation", "topic": "drop4", "summary": "s", "status": "current",
             "maturity": "probationary"},
        ])
        memory_md.write_bounded_memory(self.root)
        bullets = self.bullets("MEMORY.md")
        self.assertEqual(len(bullets), 1)
        self.assertIn("keep", bullets[0])

    def test_missing_maturity_is_durable_legacy_but_probationary_is_on_demand_only(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "legacy", "topic": "legacy", "summary": "kept", "status": "current"},
            {"id": "new", "topic": "new", "summary": "omitted", "status": "current",
             "maturity": "probationary"},
        ])
        memory_md.write_bounded_memory(self.root)
        rendered = self.read_md("MEMORY.md")
        self.assertIn("legacy", rendered)
        self.assertNotIn("omitted", rendered)

    def test_sort_confidence_then_recency(self):
        # No usage weighting: high before medium before low; ties by recency (later wins).
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "1", "topic": "low-old", "summary": "s", "confidence": "low", "status": "current"},
            {"id": "2", "topic": "high-mid", "summary": "s", "confidence": "high", "status": "current"},
            {"id": "3", "topic": "med", "summary": "s", "confidence": "medium", "status": "current"},
            {"id": "4", "topic": "high-new", "summary": "s", "confidence": "high", "status": "current"},
        ])
        memory_md.write_bounded_memory(self.root)
        topics = [ln.split("[")[1].split(" \u00b7")[0] for ln in self.bullets("MEMORY.md")]
        self.assertEqual(topics, ["high-new", "high-mid", "med", "low-old"])

    def test_usage_weighting_wins(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "x", "topic": "high-conf", "summary": "s", "confidence": "high", "status": "current"},
            {"id": "y", "topic": "used", "summary": "s", "confidence": "low", "status": "current"},
        ])
        with open(os.path.join(self.project, "MEMORY-USAGE.json"), "w", encoding="utf-8") as fh:
            json.dump({"items": {"learning:y": {"use_count": 9}}}, fh)
        memory_md.write_bounded_memory(self.root)
        first = self.bullets("MEMORY.md")[0]
        self.assertIn("used", first)  # higher use_count outranks higher confidence

    def test_summary_truncated_to_220_chars(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "a", "topic": "t", "summary": "x" * 400, "status": "current"},
        ])
        memory_md.write_bounded_memory(self.root)
        bullet = self.bullets("MEMORY.md")[0]
        self.assertIn("x" * 220 + " (evidence:", bullet)
        self.assertNotIn("x" * 221, bullet)

    def test_evidence_not_verified_when_absent(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": "a", "topic": "t", "summary": "s", "status": "current"},
        ])
        memory_md.write_bounded_memory(self.root)
        self.assertIn("(evidence: NOT VERIFIED)", self.bullets("MEMORY.md")[0])

    def test_budget_shrinks_item_count(self):
        rows = [{"id": str(i), "topic": "t%d" % i, "summary": "alpha beta gamma delta",
                 "status": "current"} for i in range(6)]
        self.write_rows("LEARNINGS.jsonl", rows)
        os.environ["KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS"] = "6"
        memory_md.write_bounded_memory(self.root)
        high = len(self.bullets("MEMORY.md"))
        os.environ["KIMIFLOW_MEMORY_BUDGET"] = "60"
        memory_md.write_bounded_memory(self.root)
        low = len(self.bullets("MEMORY.md"))
        self.assertEqual(high, 6)
        self.assertLess(low, 6)
        self.assertGreaterEqual(low, 2)

    def test_max_items_env_validation(self):
        self.write_rows("LEARNINGS.jsonl", [
            {"id": str(i), "topic": "t%d" % i, "summary": "s", "status": "current"}
            for i in range(12)
        ])
        os.environ["KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS"] = "0"  # -> 8
        memory_md.write_bounded_memory(self.root)
        self.assertEqual(len(self.bullets("MEMORY.md")), 8)
        os.environ["KIMIFLOW_MEMORY_ALWAYS_ON_MAX_ITEMS"] = "notnum"  # -> 8
        memory_md.write_bounded_memory(self.root)
        self.assertEqual(len(self.bullets("MEMORY.md")), 8)


class WriteBoundedUserMemoryCase(MemoryMdBase):
    def test_no_user_rows_writes_nothing(self):
        memory_md.write_bounded_user_memory(self.root)
        self.assertFalse(os.path.isfile(os.path.join(self.project, "USER.md")))

    def test_basic_render_and_fallback(self):
        self.write_rows("USER.jsonl", [
            {"id": "a", "topic": "tabs", "summary": "prefers spaces", "status": "current"},
        ])
        memory_md.write_bounded_user_memory(self.root)
        md = self.read_md("USER.md")
        self.assertTrue(md.startswith("# User Profile\n\nGenerated: " + ISO + "\n"))
        self.assertIn("Policy: local-only user/workflow preferences; never publish to repo docs.", md)
        self.assertIn("## Always-On User Notes\n\n", md)
        self.assertIn("- [tabs] prefers spaces (evidence: NOT VERIFIED)", md)

    def test_user_keeps_private_drops_security(self):
        self.write_rows("USER.jsonl", [
            {"id": "p", "topic": "priv", "summary": "s", "status": "current", "sensitivity": "private"},
            {"id": "s", "topic": "sec", "summary": "s", "status": "current", "sensitivity": "security"},
        ])
        memory_md.write_bounded_user_memory(self.root)
        bullets = self.bullets("USER.md")
        self.assertEqual(len(bullets), 1)
        self.assertIn("priv", bullets[0])  # private kept (unlike project memory)

    def test_user_takes_last_n_in_order(self):
        self.write_rows("USER.jsonl", [
            {"id": str(i), "topic": "t%d" % i, "summary": "s", "status": "current"}
            for i in range(10)
        ])
        memory_md.write_bounded_user_memory(self.root)
        topics = [ln.split("[")[1].split("]")[0] for ln in self.bullets("USER.md")]
        self.assertEqual(topics, ["t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9"])

    def test_user_empty_body_fallback(self):
        self.write_rows("USER.jsonl", [
            {"id": "a", "topic": "t", "summary": "s", "status": "superseded"},
        ])
        memory_md.write_bounded_user_memory(self.root)
        self.assertIn("No user-profile notes yet.", self.read_md("USER.md"))


class StoreReadJsonCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_reads_valid_json(self):
        path = os.path.join(self.dir, "a.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"k": 1}')
        self.assertEqual(store.read_json(path), {"k": 1})

    def test_missing_file_returns_default(self):
        self.assertEqual(store.read_json("/no/such.json", default={}), {})

    def test_invalid_json_returns_default(self):
        path = os.path.join(self.dir, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("not json")
        self.assertIsNone(store.read_json(path))


if __name__ == "__main__":
    unittest.main()
