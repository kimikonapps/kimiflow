import json
import os
import unittest
from pathlib import Path

from kimiflow_core import code_retrieval_eval


class CodeRetrievalEvalTests(unittest.TestCase):
    def fixture(self):
        path = Path(__file__).resolve().parents[3] / "evals" / "fixtures" / "code-retrieval-holdout.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def result(self):
        return {
            "schema_version": 1,
            "fixture_id": "kimiflow-code-retrieval-v1",
            "queries": [
                {
                    "id": "adapter-resume",
                    "snapshot_status": "current",
                    "candidates": [
                        {"path": "hooks/kimiflow_core/model_adapter.py", "symbol": "CommandAgentAdapter", "context_bytes": 300},
                        {"path": "hooks/kimiflow_core/runner.py", "symbol": "resume_task", "context_bytes": 220},
                    ],
                },
                {
                    "id": "active-owner",
                    "snapshot_status": "current",
                    "candidates": [
                        {"path": "hooks/kimiflow_core/active_run.py", "symbol": "valid_owner", "context_bytes": 260},
                    ],
                },
            ],
        }

    def test_code_retrieval_holdout_is_deterministic_and_fail_closed(self):
        first = code_retrieval_eval.evaluate(self.fixture(), self.result())
        second = code_retrieval_eval.evaluate(self.fixture(), self.result())
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual(first["status"], "passed")
        self.assertEqual(first["metrics"]["file_recall_ppm"], 1_000_000)
        self.assertEqual(first["metrics"]["symbol_recall_ppm"], 1_000_000)

        forbidden = self.result()
        forbidden["queries"][0]["candidates"].insert(0, {
            "path": ".env", "symbol": None, "context_bytes": 10,
        })
        failed = code_retrieval_eval.evaluate(self.fixture(), forbidden)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["metrics"]["forbidden_hits"], 1)

    def test_stale_or_malformed_result_fails_closed(self):
        stale = self.result()
        stale["queries"][0]["snapshot_status"] = "stale"
        self.assertEqual(code_retrieval_eval.evaluate(self.fixture(), stale)["status"], "failed")
        escaped = self.result()
        escaped["queries"][0]["candidates"][0]["path"] = "../outside.py"
        with self.assertRaisesRegex(code_retrieval_eval.RetrievalEvalError, "path_invalid"):
            code_retrieval_eval.evaluate(self.fixture(), escaped)


if __name__ == "__main__":
    unittest.main()
