import json
import os
import subprocess
import tempfile
import unittest

from kimiflow_core import evidence_eval


class EvidenceEvalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        os.makedirs(os.path.join(self.root, "hooks"))
        os.makedirs(os.path.join(self.root, "evals", "suites"))
        scripts = {
            "test-intake-gate.sh": (
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' chat_writes_content_free_receipt "
                "planning_blocked_before_receipt 'PRIVATE-PROMPT sk-secret-value' "
                "'/Users/example/private.py' 'ALL GREEN'\n"
            ),
            "test-build-replan.sh": "#!/usr/bin/env bash\nprintf '%s\\n' OK\n",
            "test-review-convergence-gate.sh": (
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' saturation_clean_axis_opens "
                "trajectory_repeated_strategy_closes 'ALL GREEN'\n"
            ),
            "test-conformance-gate.sh": "#!/usr/bin/env bash\nprintf '%s\\n' 'ALL GREEN'\n",
        }
        for name, content in scripts.items():
            path = os.path.join(self.root, "hooks", name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.chmod(path, 0o755)
        self.manifest_path = os.path.join(
            self.root, "evals", "suites", "evidence-foundation-v1.json"
        )
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            json.dump(self.manifest_value(), handle)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "kimiflow@example.invalid"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Kimiflow Test"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.root, check=True)
        self.manifest = evidence_eval.load_manifest(self.manifest_path)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def manifest_value():
        return {
            "schema_version": 1,
            "suite": "evidence-foundation-v1",
            "cases": [
                {
                    "id": "product-intake",
                    "phase": 1,
                    "command": ["bash", "hooks/test-intake-gate.sh"],
                    "required_markers": [
                        "chat_writes_content_free_receipt",
                        "planning_blocked_before_receipt",
                        "ALL GREEN",
                    ],
                    "trace_recovery": False,
                },
                {
                    "id": "recovery",
                    "phase": 5,
                    "command": ["bash", "hooks/test-build-replan.sh"],
                    "required_markers": ["OK"],
                    "trace_recovery": True,
                },
                {
                    "id": "review-convergence",
                    "phase": 7,
                    "command": ["bash", "hooks/test-review-convergence-gate.sh"],
                    "required_markers": [
                        "saturation_clean_axis_opens",
                        "trajectory_repeated_strategy_closes",
                        "ALL GREEN",
                    ],
                    "trace_recovery": True,
                },
                {
                    "id": "intent-conformance",
                    "phase": 6,
                    "command": ["bash", "hooks/test-conformance-gate.sh"],
                    "required_markers": ["ALL GREEN"],
                    "trace_recovery": False,
                },
            ],
            "model_release": {
                "policy": "release_only",
                "groups": [
                    {"id": "product-intake", "scenarios": ["21-product-intent-ownership"]},
                    {"id": "recovery", "scenarios": ["03-plan-gate-cap"]},
                    {
                        "id": "review-convergence",
                        "scenarios": ["reviewer/A-green-but-acceptance-unmet"],
                    },
                    {
                        "id": "intent-conformance",
                        "scenarios": ["22-implementation-conformance"],
                    },
                ],
            },
        }

    def baseline(self):
        return evidence_eval.run_evaluation(self.root, self.manifest, "baseline")

    def candidate(self):
        return evidence_eval.run_evaluation(self.root, self.manifest, "candidate")

    def test_trace_hierarchy_covers_allowed_span_kinds(self):
        candidate = self.candidate()
        plan = evidence_eval.build_model_plan(self.manifest)
        evidence_eval._validate_evaluation(candidate)
        evidence_eval._validate_model_plan(plan)
        kinds = {item["kind"] for item in candidate["trace"]["spans"]}
        kinds.update(item["kind"] for item in plan["trace"]["spans"])
        self.assertEqual(kinds, evidence_eval.TRACE_KINDS)
        for trace in (candidate["trace"], plan["trace"]):
            self.assertRegex(trace["trace_id"], r"^[0-9a-f]{32}$")
            ids = {item["span_id"] for item in trace["spans"]}
            self.assertEqual(len(ids), len(trace["spans"]))
            self.assertTrue(all(
                item["trace_id"] == trace["trace_id"]
                for item in trace["spans"]
            ))
            self.assertIsNone(trace["spans"][0]["parent_span_id"])
            self.assertTrue(all(
                item["parent_span_id"] is None or item["parent_span_id"] in ids
                for item in trace["spans"]
            ))

    def test_artifact_is_metadata_only_and_bounded(self):
        candidate = self.candidate()
        serialized = json.dumps(candidate, sort_keys=True)
        self.assertNotIn("PRIVATE-PROMPT", serialized)
        self.assertNotIn("sk-secret-value", serialized)
        self.assertNotIn("/Users/example/private.py", serialized)
        self.assertLess(len(serialized.encode("utf-8")), evidence_eval.MAX_ARTIFACT_BYTES)
        self.assertEqual(candidate["privacy"], evidence_eval.PRIVACY_CONTRACT)
        self.assertTrue(all("output_sha256" in row for row in candidate["cases"]))

    def test_verify_rejects_tamper_unknown_fields_and_unsafe_files(self):
        candidate = self.candidate()
        path = os.path.join(self.root, "candidate.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(candidate, handle)
        self.assertEqual(evidence_eval.load_artifact(path)["seal"], candidate["seal"])

        tampered = json.loads(json.dumps(candidate))
        tampered["cases"][0]["output_sha256"] = "sha256:" + "0" * 64
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(tampered, handle)
        with self.assertRaisesRegex(evidence_eval.EvidenceEvalError, "seal"):
            evidence_eval.load_artifact(path)

        extra = json.loads(json.dumps(candidate))
        extra["private_output"] = "forbidden"
        extra = evidence_eval._sealed(extra)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(extra, handle)
        with self.assertRaisesRegex(evidence_eval.EvidenceEvalError, "shape"):
            evidence_eval.load_artifact(path)

        real = os.path.join(self.root, "real.json")
        link = os.path.join(self.root, "link.json")
        with open(real, "w", encoding="utf-8") as handle:
            json.dump(candidate, handle)
        os.symlink(real, link)
        with self.assertRaisesRegex(evidence_eval.EvidenceEvalError, "unsafe"):
            evidence_eval.load_artifact(link)

    def test_replay_matches_unchanged_and_blocks_drift(self):
        candidate = self.candidate()
        replay = evidence_eval.replay(self.root, self.manifest, candidate)
        self.assertEqual(replay["status"], "PASS")
        self.assertEqual(replay["changed_cases"], [])

        with open(
            os.path.join(self.root, "hooks", "test-intake-gate.sh"),
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write("# changed\n")
        drift = evidence_eval.replay(self.root, self.manifest, candidate)
        self.assertEqual(drift["status"], "BLOCK")
        self.assertEqual(drift["reason"], "snapshot_mismatch")

    def test_source_change_during_run_blocks_artifact(self):
        script = os.path.join(self.root, "hooks", "test-build-replan.sh")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' '# mutated' >> hooks/test-conformance-gate.sh\n"
                "printf '%s\\n' OK\n"
            )
        with self.assertRaisesRegex(
            evidence_eval.EvidenceEvalError,
            "source_changed_during_evaluation",
        ):
            self.candidate()

    def test_compare_blocks_regression_and_accepts_equal_candidate(self):
        baseline = self.baseline()
        candidate = self.candidate()
        comparison = evidence_eval.compare_artifacts(baseline, candidate)
        self.assertEqual(comparison["verdict"], "PASS")
        self.assertEqual(comparison["regressions"], [])

        broken = json.loads(json.dumps(candidate))
        broken["cases"][1]["passed"] = False
        broken["cases"][1]["exit_code"] = 1
        broken["summary"] = {
            "case_count": 4,
            "pass_count": 3,
            "failure_count": 1,
            "verdict": "BLOCK",
        }
        broken = evidence_eval._sealed(broken)
        blocked = evidence_eval.compare_artifacts(baseline, broken)
        self.assertEqual(blocked["verdict"], "BLOCK")
        self.assertEqual(blocked["regressions"], ["recovery"])

    def test_model_plan_is_release_only_and_does_not_execute(self):
        sentinel = os.path.join(self.root, "executed")
        script = os.path.join(self.root, "hooks", "test-intake-gate.sh")
        with open(script, "a", encoding="utf-8") as handle:
            handle.write("touch %s\n" % sentinel)
        plan = evidence_eval.build_model_plan(self.manifest)
        self.assertEqual(plan["policy"], "release_only")
        self.assertFalse(plan["executed"])
        self.assertEqual(plan["model_calls"], 0)
        self.assertEqual(plan["network_calls"], 0)
        self.assertFalse(os.path.exists(sentinel))
        self.assertEqual(
            [group["id"] for group in plan["groups"]],
            ["product-intake", "recovery", "review-convergence", "intent-conformance"],
        )

    def test_model_plan_validation_rejects_private_or_duplicate_scenarios(self):
        plan = evidence_eval.build_model_plan(self.manifest)
        plan["groups"][0]["scenarios"] = ["/Users/example/private"]
        with self.assertRaisesRegex(
            evidence_eval.EvidenceEvalError,
            "model_plan_group_invalid",
        ):
            evidence_eval._validate_model_plan(evidence_eval._sealed(plan))

        plan = evidence_eval.build_model_plan(self.manifest)
        plan["groups"][1]["id"] = plan["groups"][0]["id"]
        with self.assertRaisesRegex(
            evidence_eval.EvidenceEvalError,
            "model_plan_group_invalid",
        ):
            evidence_eval._validate_model_plan(evidence_eval._sealed(plan))

    def test_manifest_rejects_arbitrary_execution(self):
        value = self.manifest_value()
        value["cases"][0]["command"] = ["bash", "scripts/publish.sh"]
        path = os.path.join(self.root, "bad-manifest.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        with self.assertRaisesRegex(evidence_eval.EvidenceEvalError, "case"):
            evidence_eval.load_manifest(path)


if __name__ == "__main__":
    unittest.main()
