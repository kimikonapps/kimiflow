import json
import os
import shutil
import tempfile
import unittest

from memory_router import attribution, recall


class AttributionContractCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.run_dir = os.path.join(self.root, ".kimiflow", "demo")
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.run_dir)
        os.makedirs(self.project)

    def write_run(self, name, content):
        path = os.path.join(self.run_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def write_root(self, name, content):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def hit(self, source="learnings", summary="verified cache strategy"):
        base = {"id": "learn_native", "summary": summary, "evidence": ["src/cache.py:1"]}
        base["recall_id"] = attribution.recall_id(source, "src/cache.py:1", base)
        return base

    def recall_snapshot(self, *hits):
        grouped = {}
        for source, hit in hits:
            grouped.setdefault(source, []).append(hit)
        sources = {
            source: {"count": len(values), "hits": values}
            for source, values in grouped.items()
        }
        value = {"schema_version": 2, "attribution": {"contract": 1}, "sources": sources}
        self.write_run("RECALL.json", json.dumps(value))
        return value

    def plan(self, applied, decisions):
        lines = [
            "# Plan",
            "",
            "<!-- kimiflow:recall-attribution contract=1 -->",
            "Applied recall IDs: %s" % (", ".join(applied) if applied else "none"),
            "",
        ]
        for number, ids in decisions:
            lines.extend((
                "Decision D%s: choose the verified route" % number,
                "Recall D%s: %s" % (number, ", ".join(ids) if ids else "none"),
            ))
        return "\n".join(lines) + "\n"

    def test_selected_hits_get_deterministic_source_aware_recall_ids(self):
        hit = {"id": "native", "summary": "same content", "evidence": ["src/a.py:1"]}
        first = attribution.recall_id("learnings", "src/a.py:1", hit)
        self.assertEqual(first, attribution.recall_id("learnings", "src/a.py:1", dict(hit)))
        self.assertRegex(first, r"^rec_[0-9a-f]{64}$")
        self.assertNotEqual(
            first,
            attribution.recall_id("learnings", "src/a.py:1", dict(hit, summary="changed")),
        )
        self.assertNotEqual(first, attribution.recall_id("history", "src/a.py:1", hit))

    def test_unknown_duplicate_and_unlinked_applied_ids_fail_closed(self):
        hit = self.hit()
        identifier = hit["recall_id"]
        self.recall_snapshot(("learnings", hit))
        valid = self.plan([identifier], [(1, [identifier])])
        self.write_run("PLAN.md", valid)
        self.write_run("VERIFICATION.md", "Decision check D1: passed :: unit test\n")
        self.assertEqual(
            attribution.usage_json(self.root, self.run_dir)["applied_ids"],
            [identifier],
        )

        bad_plans = (
            valid.replace("contract=1", "contract=2"),
            valid.replace("<!-- kimiflow:recall-attribution contract=1 -->", "<!-- kimiflow:recall-attribution contract=1 -->\n<!-- kimiflow:recall-attribution contract=1 -->"),
            valid.replace("Applied recall IDs:", "Applied recall IDs: none\nApplied recall IDs:"),
            valid.replace("Recall D1:", "Recall D1: none\nRecall D1:"),
            valid.replace(identifier, "rec_" + ("f" * 64)),
            valid.replace("Recall D1: %s" % identifier, "Recall D1: none"),
            valid.replace("Applied recall IDs: %s" % identifier, "Applied recall IDs: %s, %s" % (identifier, identifier)),
        )
        for candidate in bad_plans:
            self.write_run("PLAN.md", candidate)
            with self.subTest(candidate=candidate):
                with self.assertRaises(attribution.AttributionError):
                    attribution.usage_json(self.root, self.run_dir)

        self.write_run("PLAN.md", self.plan([identifier], [(1, [])]))
        self.write_run(
            "VERIFICATION.md",
            "Recall contradiction %s: src/cache.py:999999\n" % identifier,
        )
        self.write_root("src/cache.py", "CURRENT = True\n")
        with self.assertRaises(attribution.AttributionError):
            attribution.usage_json(self.root, self.run_dir)

    def test_receipt_classifies_helpful_neutral_and_contradicted_without_content(self):
        helpful_hit = self.hit(summary="SECRET_RECALL_CONTENT")
        neutral_hit = self.hit(source="history", summary="another secret")
        helpful = helpful_hit["recall_id"]
        neutral = neutral_hit["recall_id"]
        self.recall_snapshot(("learnings", helpful_hit), ("history", neutral_hit))
        self.write_run("PLAN.md", self.plan([helpful, neutral], [(1, [helpful]), (2, [neutral])]))
        self.write_root("src/current.py", "CURRENT_RULE = True\n")
        self.write_run(
            "VERIFICATION.md",
            "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->\n"
            "Decision check D1: passed :: unit test\n"
            "Decision check D2: passed :: unit test\n"
            "Recall contradiction %s: src/current.py:1\n" % helpful,
        )
        receipt = attribution.evaluate_json(self.root, self.run_dir, "done")
        self.assertEqual(receipt["classification"], "contradicted")
        self.assertEqual(
            {item["recall_id"]: item["classification"] for item in receipt["items"]},
            {helpful: "contradicted", neutral: "helpful"},
        )
        serialized = json.dumps(receipt)
        self.assertNotIn("SECRET_RECALL_CONTENT", serialized)
        self.assertNotIn("another secret", serialized)

        self.write_run("PLAN.md", self.plan([], [(1, [])]))
        neutral_receipt = attribution.evaluate_json(self.root, self.run_dir, "done")
        self.assertEqual(neutral_receipt["classification"], "neutral")

    def test_non_success_terminals_remain_mechanically_closable_with_incomplete_verification(self):
        hit = self.hit()
        identifier = hit["recall_id"]
        self.recall_snapshot(("learnings", hit))
        self.write_run("PLAN.md", self.plan([identifier], [(1, [identifier])]))
        for terminal in ("parked", "aborted", "failed"):
            with self.subTest(terminal=terminal):
                receipt = attribution.evaluate_json(self.root, self.run_dir, terminal)
                self.assertEqual(receipt["classification"], "neutral")
                self.assertEqual(receipt["status"], "inconclusive")
        with self.assertRaises(attribution.AttributionError):
            attribution.evaluate_json(self.root, self.run_dir, "done")

    def test_end_to_end_recall_plan_verification_outcome_contract(self):
        with open(os.path.join(self.project, "LEARNINGS.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "learn_e2e",
                "status": "current",
                "summary": "transaction manager invariant",
                "evidence": ["src/storage.py:1"],
            }) + "\n")
        snapshot = recall.recall_json(self.root, "transaction manager", 5)
        selected = snapshot["sources"]["learnings"]["hits"][0]
        identifier = selected["recall_id"]
        self.write_run("RECALL.json", json.dumps(snapshot))
        self.write_run("PLAN.md", self.plan([identifier], [(1, [identifier])]))
        self.write_run(
            "VERIFICATION.md",
            "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->\n"
            "Decision check D1: passed :: storage tests\n",
        )
        receipt = attribution.evaluate_json(self.root, self.run_dir, "done")
        self.assertEqual(receipt["classification"], "helpful")
        self.assertEqual(receipt["items"][0]["recall_id"], identifier)


if __name__ == "__main__":
    unittest.main()
