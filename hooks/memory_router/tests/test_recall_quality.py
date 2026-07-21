import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from memory_router import recall


FIXTURE_SHA256 = "7f561c073d598620e1676fa4b9e423cd8f34887e530390b37e1e7d4ff20430de"


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

    def test_frozen_holdout_meets_recall_contract(self):
        self.assertEqual(hashlib.sha256(self.fixture_bytes).hexdigest(), FIXTURE_SHA256)
        self.assertEqual(self.fixture["schema_version"], 2)
        self.assertEqual(self.fixture["metric"], "evidence_ref_precision_recall")
        self.assertGreaterEqual(len(self.fixture["cases"]), 8)

        for case in self.fixture["cases"]:
            with self.subTest(case=case["id"]):
                case_root = os.path.join(self.root, case["id"])
                project = os.path.join(case_root, ".kimiflow", "project")
                os.makedirs(project)
                for unit in case.get("workspace_units", []):
                    _write(os.path.join(case_root, unit, "package.json"), "{}\n")
                _write(os.path.join(project, "FACTS.jsonl"), _jsonl(case["facts"]))
                _write(os.path.join(project, "LEARNINGS.jsonl"), _jsonl(case["learnings"]))
                for artifact in case["run_artifacts"]:
                    _write(os.path.join(case_root, artifact["path"]), artifact["content"])

                with mock.patch.dict(os.environ, {
                    "KIMIFLOW_RECALL_BUDGET": str(case["budget"]),
                    "KIMIFLOW_MEMORY_BUDGET": "900",
                    "KIMIFLOW_USER_MEMORY_BUDGET": "500",
                }, clear=False):
                    result = recall.recall_json(
                        case_root, case["query"], case["max_hits"], targeted=False,
                        scope_paths=case.get("scope_paths"),
                    )

                hits = []
                for source in ("facts", "learnings", "index", "history"):
                    hits.extend(result["sources"][source]["hits"])
                refs = [recall.hit_ref(hit) for hit in hits]
                expected = set(case["expected_refs"])
                forbidden = set(case["forbidden_refs"])
                returned = set(refs)
                relevant = expected & returned
                precision = len(relevant) / len(returned) if returned else 0.0
                quality_recall = len(relevant) / len(expected) if expected else 1.0
                self.assertTrue(expected.issubset(refs))
                self.assertTrue(forbidden.isdisjoint(refs))
                self.assertGreaterEqual(precision, case["min_precision"])
                self.assertGreaterEqual(quality_recall, case["min_recall"])
                self.assertEqual(len(refs), len(set(refs)))
                self.assertLessEqual(len(refs), case["max_hits"])
                self.assertLessEqual(result["budget"]["used"], result["budget"]["limit"])
                self.assertEqual(result["authority"]["recall_status"], "advisory")


if __name__ == "__main__":
    unittest.main()
