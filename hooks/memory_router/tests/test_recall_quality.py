import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from memory_router import recall


FIXTURE_SHA256 = "a3a6dbd25ff8f48f0ff2693d83a004a576f49fbfc362a3572614506df260d94d"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _jsonl(rows):
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


class FrozenRecallQualityCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_path = os.path.join(_repo_root(), "evals", "fixtures", "recall-quality-holdout.json")
        with open(cls.fixture_path, "rb") as handle:
            cls.fixture_bytes = handle.read()
        cls.fixture = json.loads(cls.fixture_bytes.decode("utf-8"))

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, ".kimiflow", "project"))

    def test_frozen_holdout_meets_recall_contract(self):
        self.assertEqual(hashlib.sha256(self.fixture_bytes).hexdigest(), FIXTURE_SHA256)
        self.assertEqual(self.fixture["schema_version"], 1)

        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                project = os.path.join(self.root, ".kimiflow", "project")
                _write(os.path.join(project, "FACTS.jsonl"), _jsonl(case["facts"]))
                _write(os.path.join(project, "LEARNINGS.jsonl"), _jsonl(case["learnings"]))
                for artifact in case["run_artifacts"]:
                    _write(os.path.join(self.root, artifact["path"]), artifact["content"])

                with mock.patch.dict(os.environ, {
                    "KIMIFLOW_RECALL_BUDGET": str(case["budget"]),
                    "KIMIFLOW_MEMORY_BUDGET": "900",
                    "KIMIFLOW_USER_MEMORY_BUDGET": "500",
                }, clear=False):
                    result = recall.recall_json(
                        self.root, case["query"], case["max_hits"], targeted=False
                    )

                hits = []
                for source in ("facts", "learnings", "index", "history"):
                    hits.extend(result["sources"][source]["hits"])
                refs = [recall.hit_ref(hit) for hit in hits]
                self.assertTrue(set(case["expected_refs"]).issubset(refs))
                self.assertTrue(set(case["forbidden_refs"]).isdisjoint(refs))
                self.assertEqual(len(refs), len(set(refs)))
                self.assertLessEqual(len(refs), case["max_hits"])
                self.assertLessEqual(result["budget"]["used"], result["budget"]["limit"])
                self.assertEqual(result["authority"]["recall_status"], "advisory")


if __name__ == "__main__":
    unittest.main()
