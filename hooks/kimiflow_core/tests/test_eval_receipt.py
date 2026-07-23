import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import eval_receipt


def valid_receipt():
    return {
        "schema_version": 1,
        "scenario": "03-plan-gate-cap",
        "mode": "open_ended",
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=eval_receipt._repo_root(), text=True
        ).strip(),
        "attribution_clean": True,
        "sample_count": 3,
        "pass_count": 2,
        "failure_count": 1,
        "verdict": "PASS",
        "runs": [
            {
                "id": "run-1",
                "passed": True,
                "rule_refs": [
                    "reference.md §Review rubric (Phase 4 plan-gate · Phase 7 code-review)"
                ],
            },
            {"id": "run-2", "passed": True, "rule_refs": ["SKILL.md §Core principles (apply in ALL phases)"]},
            {"id": "run-3", "passed": False, "rule_refs": []},
        ],
    }


class EvalReceiptCase(unittest.TestCase):
    def test_accepts_consistent_majority_pass(self):
        result = eval_receipt.validate(valid_receipt())
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["sample_count"], 3)
        self.assertNotIn("runs", result)

    def test_rejects_small_or_inconsistent_samples(self):
        for mutate in (
            lambda value: value.update(sample_count=2),
            lambda value: value.update(pass_count=3),
            lambda value: value.update(verdict="CRACK"),
            lambda value: value.update(attribution_clean=False),
        ):
            value = valid_receipt()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(eval_receipt.ReceiptError):
                eval_receipt.validate(value)

    def test_rejects_passing_run_without_rule_attribution(self):
        value = valid_receipt()
        value["runs"][0]["rule_refs"] = []
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "passing run needs"):
            eval_receipt.validate(value)

    def test_rejects_unknown_scenario_and_rule_location(self):
        value = valid_receipt()
        value["scenario"] = "99-missing-scenario"
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "does not exist"):
            eval_receipt.validate(value)
        value = valid_receipt()
        value["runs"][0]["rule_refs"] = ["reference.md:999999"]
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "rule_refs"):
            eval_receipt.validate(value)
        value = valid_receipt()
        value["runs"][0]["rule_refs"] = ["SKILL.md:1"]
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "rule_refs"):
            eval_receipt.validate(value)
        value = valid_receipt()
        value["runs"][0]["rule_refs"] = ["reference.md:28"]
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "rule_refs"):
            eval_receipt.validate(value)
        value = valid_receipt()
        value["runs"][0]["rule_refs"] = ["reference.md:29"]
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "rule_refs"):
            eval_receipt.validate(value)

    def test_rejects_unknown_source_commit_and_ineligible_mode(self):
        value = valid_receipt()
        value["source_commit"] = "a" * 40
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "source_commit does not exist"):
            eval_receipt.validate(value)
        value = valid_receipt()
        value["scenario"] = "26-workspace-aware-recall"
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "not eligible"):
            eval_receipt.validate(value)
        value = valid_receipt()
        value["runs"][0]["rule_refs"] = ["SKILL.md §e"]
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "rule_refs"):
            eval_receipt.validate(value)

    def test_rejects_duplicate_run_ids_and_fields(self):
        value = valid_receipt()
        value["runs"][1]["id"] = "run-1"
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "duplicated"):
            eval_receipt.validate(value)
        value = valid_receipt()
        value["extra"] = True
        with self.assertRaisesRegex(eval_receipt.ReceiptError, "fields"):
            eval_receipt.validate(value)

    def test_load_rejects_duplicate_json_keys_and_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            duplicate = os.path.join(root, "duplicate.json")
            with open(duplicate, "w", encoding="utf-8") as handle:
                handle.write('{"schema_version":1,"schema_version":1}\n')
            with self.assertRaises(eval_receipt.ReceiptError):
                eval_receipt.load(duplicate)

            target = os.path.join(root, "target.json")
            with open(target, "w", encoding="utf-8") as handle:
                json.dump(valid_receipt(), handle)
            link = os.path.join(root, "link.json")
            os.symlink(target, link)
            with self.assertRaisesRegex(eval_receipt.ReceiptError, "non-symlink"):
                eval_receipt.load(link)

    def test_load_rejects_path_exchange_and_oversize(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "receipt.json")
            replacement = os.path.join(root, "replacement.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(valid_receipt(), handle)
            with open(replacement, "w", encoding="utf-8") as handle:
                json.dump(valid_receipt(), handle)

            real_open = os.open

            def exchange(name, flags):
                os.replace(replacement, path)
                return real_open(name, flags)

            with mock.patch.object(eval_receipt.os, "open", side_effect=exchange):
                with self.assertRaisesRegex(eval_receipt.ReceiptError, "changed during validation"):
                    eval_receipt.load(path)

            oversized = os.path.join(root, "oversized.json")
            with open(oversized, "wb") as handle:
                handle.write(b"x" * (eval_receipt.MAX_RECEIPT_BYTES + 1))
            with self.assertRaisesRegex(eval_receipt.ReceiptError, "size limit"):
                eval_receipt.load(oversized)

    def test_git_reads_disable_replacements_and_lazy_fetch(self):
        with mock.patch.object(eval_receipt.subprocess, "run") as run:
            self.assertTrue(eval_receipt._commit_exists("a" * 40))
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["GIT_ALLOW_PROTOCOL"], "file")
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")

    def test_rule_sources_are_loaded_once_per_validation(self):
        value = valid_receipt()
        value["runs"][1]["rule_refs"] = [
            "reference.md §Review rubric (Phase 4 plan-gate · Phase 7 code-review)"
        ]
        with mock.patch.object(eval_receipt, "_git_blob_lines", wraps=eval_receipt._git_blob_lines) as load:
            eval_receipt.validate(value)
        paths = [call.args[1] for call in load.call_args_list]
        self.assertEqual(paths.count("evals/scenarios/03-plan-gate-cap.md"), 1)
        self.assertEqual(paths.count("reference.md"), 1)
        self.assertNotIn("SKILL.md", paths)


if __name__ == "__main__":
    unittest.main()
