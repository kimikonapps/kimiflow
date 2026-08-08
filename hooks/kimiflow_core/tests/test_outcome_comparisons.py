from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

try:
    from kimiflow_core import outcome_comparisons
except ModuleNotFoundError:
    from hooks.kimiflow_core import outcome_comparisons


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class OutcomeComparisonsTest(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        (self.root / "evidence").mkdir()
        self.dataset = self.root / "comparisons.jsonl"

    def tearDown(self):
        self._temporary.cleanup()

    def _evidence(self, kind, name, payload=None):
        if payload is None:
            payload = json.dumps(
                {"kind": kind, "fixture": name}, sort_keys=True
            ).encode("utf-8")
        path = self.root / "evidence" / name
        path.write_bytes(payload)
        return {
            "kind": kind,
            "path": path.relative_to(self.root).as_posix(),
            "sha256": _sha(payload),
        }

    def _typed_projection(self, pair, arm, kind):
        if kind == "session":
            fields = (
                "session_id",
                "source_commit",
                "source_snapshot_sha256",
                "execution",
                "started_at",
                "completed_at",
                "input_tokens",
                "output_tokens",
                "model_calls",
                "tool_calls",
                "user_interactions",
            )
            return {field: copy.deepcopy(arm[field]) for field in fields}
        if kind == "outcome":
            fields = (
                "completed",
                "accepted",
                "internal_defects",
                "residual_defects",
                "rework_rounds",
                "confounds",
            )
            return {field: copy.deepcopy(arm[field]) for field in fields}
        if kind == "diff":
            return {field: arm[field] for field in ("added_loc", "deleted_loc")}
        if kind == "merge":
            return {
                field: arm[field]
                for field in ("merge_commit", "merged_at", "window_ends_at")
            }
        if kind == "post_merge":
            return {
                "observed_at": pair["post_merge"]["observed_at"],
                "window_ends_at": arm["window_ends_at"],
                "post_merge_defects": copy.deepcopy(arm["post_merge_defects"]),
            }
        raise AssertionError("unsupported typed Evidence kind: %s" % kind)

    def _sync_typed_evidence(self, pair, arm_names=None, kinds=None):
        selected_names = set(arm_names or ("plain", "kimiflow"))
        selected_kinds = set(kinds or ("session", "outcome", "diff", "merge", "post_merge"))
        for arm in pair["arms"]:
            if arm["name"] not in selected_names:
                continue
            for kind in ("session", "outcome", "diff", "merge", "post_merge"):
                if kind not in selected_kinds:
                    continue
                reference = arm.get("%s_evidence" % kind)
                if reference is None:
                    continue
                payload = json.dumps(
                    self._typed_projection(pair, arm, kind),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                (self.root / reference["path"]).write_bytes(payload)
                reference["sha256"] = _sha(payload)

    def _pair(self, number=1, lifecycle="pending"):
        suffix = "%02d" % number
        source_commit = ("%040x" % number)[-40:]
        task_payload = ("benchmark task %s\n" % suffix).encode("utf-8")
        task_evidence = self._evidence("task", "task-%s.txt" % suffix, task_payload)
        snapshot_payload = json.dumps(
            {
                "source_commit": source_commit,
                "tracked_binary_diff_sha256": _sha(b""),
                "untracked": [
                    {
                        "path": "fixture.txt",
                        "mode": "100644",
                        "sha256": _sha(("fixture-%s" % suffix).encode("utf-8")),
                    }
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        snapshot = self._evidence(
            "source_snapshot", "snapshot-%s.json" % suffix, snapshot_payload
        )
        execution = {
            "model_id": "gpt-test",
            "reasoning_effort": "high",
            "toolset_fingerprint": _sha(b"tools-v1"),
            "permission_fingerprint": _sha(b"permissions-v1"),
            "execution_fingerprint": _sha(b"execution-v1"),
            "time_budget_seconds": 3600,
            "token_budget": 100000,
        }

        def arm(name, minute, completed_minute):
            is_plain = name == "plain"
            started_at = "2026-01-01T00:%02d:00Z" % minute
            completed_at = "2026-01-01T00:%02d:00Z" % completed_minute
            values = {
                "name": name,
                "measurement_source": "actual",
                "session_id": "%s-session-%s" % (name, suffix),
                "source_commit": source_commit,
                "source_snapshot_sha256": snapshot["sha256"],
                "execution": copy.deepcopy(execution),
                "started_at": started_at,
                "completed_at": completed_at,
                "completed": True,
                "accepted": True,
                "internal_defects": {"blocker": 0, "high": 1 if is_plain else 0},
                "residual_defects": {"blocker": 0, "high": 0},
                "input_tokens": 1000 if is_plain else 1200,
                "output_tokens": 500 if is_plain else 550,
                "model_calls": 3 if is_plain else 4,
                "tool_calls": 8 if is_plain else 10,
                "user_interactions": 2 if is_plain else 1,
                "rework_rounds": 1 if is_plain else 0,
                "confounds": None,
                "added_loc": 40 if is_plain else 35,
                "deleted_loc": 10,
                "session_evidence": self._evidence(
                    "session", "session-%s-%s.json" % (name, suffix)
                ),
                "outcome_evidence": self._evidence(
                    "outcome", "outcome-%s-%s.json" % (name, suffix)
                ),
                "diff_evidence": self._evidence(
                    "diff", "diff-%s-%s.json" % (name, suffix)
                ),
                "merge_status": "merged",
                "merged_at": "2026-01-01T00:%02d:00Z" % (completed_minute + 2),
                "merge_commit": ("%040x" % (number * 10 + (1 if is_plain else 2)))[-40:],
                "window_ends_at": "2026-01-08T00:%02d:00Z" % (completed_minute + 2),
                "merge_evidence": self._evidence(
                    "merge", "merge-%s-%s.json" % (name, suffix)
                ),
                "post_merge_defects": None,
                "post_merge_evidence": None,
            }
            return values

        plain = arm("plain", 0, 10)
        kimiflow = arm("kimiflow", 1, 9)
        post_merge = {
            "status": "pending",
            "window_days": 7,
            "observed_at": "2026-01-02T00:00:00Z",
        }
        if lifecycle == "observed":
            post_merge = {
                "status": "observed",
                "window_days": 7,
                "observed_at": "2026-01-09T00:00:00Z",
            }
            for current in (plain, kimiflow):
                current["post_merge_defects"] = {
                    "blocker": 0,
                    "high": 1 if current["name"] == "plain" else 0,
                }
                current["post_merge_evidence"] = self._evidence(
                    "post_merge",
                    "post-merge-%s-%s.json" % (current["name"], suffix),
                )
        elif lifecycle == "not_applicable":
            post_merge = {"status": "not_applicable", "window_days": None, "observed_at": None}
            for current in (plain, kimiflow):
                current.update(
                    {
                        "merge_status": "not_merged",
                        "merged_at": None,
                        "merge_commit": None,
                        "window_ends_at": None,
                        "merge_evidence": None,
                        "post_merge_defects": None,
                        "post_merge_evidence": None,
                    }
                )
        pair = {
            "schema_version": 1,
            "comparison_id": "comparison-%s" % suffix,
            "task_id": "task-%s" % suffix,
            "task": {"kind": "feature", "text_sha256": task_evidence["sha256"]},
            "repository_id": "benchmark-fixture",
            "source_commit": source_commit,
            "source_snapshot_sha256": snapshot["sha256"],
            "arm_order": ["plain", "kimiflow"],
            "execution_contract": execution,
            "task_evidence": task_evidence,
            "source_snapshot_evidence": snapshot,
            "oracle_evidence": self._evidence("oracle", "oracle-%s.json" % suffix),
            "blind_review_evidence": self._evidence(
                "blind_review", "blind-review-%s.json" % suffix
            ),
            "arms": [plain, kimiflow],
            "post_merge": post_merge,
        }
        self._sync_typed_evidence(pair)
        return pair

    def _write(self, *rows):
        self.dataset.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _evaluate(self):
        return outcome_comparisons.evaluate_dataset(self.dataset, self.root)

    def test_valid_pair_is_primary_eligible(self):
        pending = self._pair(1, "pending")
        observed = self._pair(2, "observed")
        not_merged = self._pair(3, "not_applicable")
        mixed = self._pair(4, "not_applicable")
        mixed_plain = next(arm for arm in mixed["arms"] if arm["name"] == "plain")
        mixed_kimiflow = next(arm for arm in mixed["arms"] if arm["name"] == "kimiflow")
        mixed_plain.update(
            {
                "merge_status": "merged",
                "merged_at": "2026-01-01T00:12:00Z",
                "merge_commit": "%040x" % 99,
                "merge_evidence": self._evidence("merge", "merge-plain-mixed.json"),
            }
        )
        mixed_kimiflow.update(
            {
                "merge_status": "not_merged",
                "merged_at": None,
                "merge_commit": None,
                "merge_evidence": None,
            }
        )
        observed["task"]["kind"] = "bug"
        # Correctly ordered sessions may overlap.
        pending["arms"][0]["completed_at"] = "2026-01-01T00:05:00Z"
        self._sync_typed_evidence(pending, ("plain",), ("session",))
        self._sync_typed_evidence(mixed, ("plain",), ("merge",))
        self._write(pending, observed, not_merged, mixed)

        result = self._evaluate()

        self.assertEqual(4, result["recorded_pairs"])
        self.assertEqual(4, result["valid_pairs"])
        self.assertEqual(0, result["field_note_pairs"])
        self.assertEqual(0, result["invalid_pairs"])
        self.assertEqual(["primary_eligible"] * 4, [row["status"] for row in result["rows"]])
        self.assertEqual("pending", result["rows"][0]["verdict"])

    def test_mismatch_and_unsafe_evidence_fail_closed(self):
        rows = []

        mismatch = self._pair(10)
        mismatch["arms"][1]["execution"]["token_budget"] += 1
        self._sync_typed_evidence(mismatch, ("kimiflow",), ("session",))
        rows.append(mismatch)

        absolute = self._pair(11)
        absolute["oracle_evidence"]["path"] = "/tmp/oracle.json"
        rows.append(absolute)

        traversal = self._pair(12)
        traversal["blind_review_evidence"]["path"] = "../review.json"
        rows.append(traversal)

        digest = self._pair(13)
        digest["task_evidence"]["sha256"] = _sha(b"wrong")
        rows.append(digest)

        negative = self._pair(14)
        negative["arms"][0]["input_tokens"] = -1
        self._sync_typed_evidence(negative, ("plain",), ("session",))
        rows.append(negative)

        boolean_counter = self._pair(15)
        boolean_counter["arms"][0]["model_calls"] = True
        self._sync_typed_evidence(boolean_counter, ("plain",), ("session",))
        rows.append(boolean_counter)

        reversed_time = self._pair(16)
        reversed_time["arms"][0]["completed_at"] = reversed_time["arms"][0]["started_at"]
        self._sync_typed_evidence(reversed_time, ("plain",), ("session",))
        rows.append(reversed_time)

        wrong_order = self._pair(17)
        wrong_order["arm_order"] = ["kimiflow", "plain"]
        rows.append(wrong_order)

        wrong_window = self._pair(18)
        wrong_window["post_merge"]["window_days"] = 6
        rows.append(wrong_window)

        symlinked = self._pair(19)
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "oracle.json").write_text("outside", encoding="utf-8")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        symlinked["oracle_evidence"] = {
            "kind": "oracle",
            "path": "linked/oracle.json",
            "sha256": _sha(b"outside"),
        }
        rows.append(symlinked)

        malformed_arm_name = self._pair(20)
        malformed_arm_name["arms"][0]["name"] = []
        rows.append(malformed_arm_name)

        invalid_snapshot = self._pair(26)
        snapshot_path = self.root / invalid_snapshot["source_snapshot_evidence"]["path"]
        snapshot_value = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot_value["untracked"][0]["mode"] = "100648"
        snapshot_payload = json.dumps(
            snapshot_value, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        snapshot_path.write_bytes(snapshot_payload)
        snapshot_digest = _sha(snapshot_payload)
        invalid_snapshot["source_snapshot_evidence"]["sha256"] = snapshot_digest
        invalid_snapshot["source_snapshot_sha256"] = snapshot_digest
        for arm in invalid_snapshot["arms"]:
            arm["source_snapshot_sha256"] = snapshot_digest
        self._sync_typed_evidence(invalid_snapshot, kinds=("session",))
        rows.append(invalid_snapshot)

        self._write(*rows)
        with self.dataset.open("a", encoding="utf-8") as handle:
            handle.write('{"schema_version":1,"schema_version":1}\n')

        result = self._evaluate()

        self.assertEqual(len(rows) + 1, result["invalid_pairs"])
        self.assertEqual(0, result["valid_pairs"])
        self.assertTrue(all(row["status"] == "invalid" for row in result["rows"]))

        repository = Path(__file__).resolve().parents[3]
        process = subprocess.run(
            [
                str(repository / "hooks" / "outcome-comparisons.sh"),
                "validate",
                "--data",
                str(self.dataset),
                "--repo-root",
                str(self.root),
            ],
            cwd=repository,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(0, process.returncode)
        self.assertEqual(len(rows) + 1, json.loads(process.stdout)["invalid_pairs"])

    def test_missing_and_mismatched_evidence_fail_closed(self):
        missing_file = self._pair(21)
        (self.root / missing_file["arms"][0]["diff_evidence"]["path"]).unlink()
        wrong_kind = self._pair(22)
        wrong_kind["arms"][1]["session_evidence"]["kind"] = "outcome"
        duplicate_session = self._pair(23)
        duplicate_session["arms"][1]["session_id"] = duplicate_session["arms"][0]["session_id"]
        self._sync_typed_evidence(duplicate_session, ("kimiflow",), ("session",))
        first = self._pair(24)
        second = self._pair(25)
        second["comparison_id"] = first["comparison_id"]
        second["source_commit"] = first["source_commit"]
        second["source_snapshot_sha256"] = first["source_snapshot_sha256"]
        second["source_snapshot_evidence"] = copy.deepcopy(first["source_snapshot_evidence"])
        for arm in second["arms"]:
            arm["source_commit"] = first["source_commit"]
            arm["source_snapshot_sha256"] = first["source_snapshot_sha256"]
        first_plain = next(arm for arm in first["arms"] if arm["name"] == "plain")
        second_kimiflow = next(arm for arm in second["arms"] if arm["name"] == "kimiflow")
        second_kimiflow.update(self._typed_projection(first, first_plain, "session"))
        self._sync_typed_evidence(second)
        self._write(missing_file, wrong_kind, duplicate_session, first, second)

        result = self._evaluate()

        self.assertEqual(5, result["invalid_pairs"])
        duplicate_reasons = [
            row["reasons"] for row in result["rows"] if row["comparison_id"] == "comparison-24"
        ]
        self.assertTrue(all("duplicate_session_evidence_sha256" in reasons for reasons in duplicate_reasons))
        self.assertTrue(all("duplicate_comparison_id" in reasons for reasons in duplicate_reasons))
        self.assertTrue(all("duplicate_session_id" in reasons for reasons in duplicate_reasons))

    def test_evidence_payloads_are_canonical_and_bound_to_row_values(self):
        session_mutation = self._pair(27, "observed")
        session_mutation["arms"][0]["tool_calls"] += 1
        outcome_mutation = self._pair(28, "observed")
        outcome_mutation["arms"][0]["accepted"] = False
        diff_mutation = self._pair(29, "observed")
        diff_mutation["arms"][1]["added_loc"] += 1
        merge_mutation = self._pair(30, "observed")
        merge_mutation["arms"][1]["merge_commit"] = "f" * 40
        post_merge_mutation = self._pair(31, "observed")
        post_merge_mutation["arms"][0]["post_merge_defects"]["high"] += 1

        noncanonical = self._pair(32, "not_applicable")
        noncanonical_reference = noncanonical["arms"][0]["outcome_evidence"]
        noncanonical_path = self.root / noncanonical_reference["path"]
        noncanonical_value = json.loads(noncanonical_path.read_text(encoding="utf-8"))
        noncanonical_payload = json.dumps(
            noncanonical_value, ensure_ascii=True, sort_keys=True, indent=2
        ).encode("utf-8")
        noncanonical_path.write_bytes(noncanonical_payload)
        noncanonical_reference["sha256"] = _sha(noncanonical_payload)

        unknown_key = self._pair(33, "not_applicable")
        unknown_reference = unknown_key["arms"][1]["session_evidence"]
        unknown_path = self.root / unknown_reference["path"]
        unknown_value = json.loads(unknown_path.read_text(encoding="utf-8"))
        unknown_value["undeclared"] = 1
        unknown_payload = json.dumps(
            unknown_value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        unknown_path.write_bytes(unknown_payload)
        unknown_reference["sha256"] = _sha(unknown_payload)

        duplicate_key = self._pair(34, "not_applicable")
        duplicate_reference = duplicate_key["arms"][0]["diff_evidence"]
        duplicate_path = self.root / duplicate_reference["path"]
        duplicate_payload = b'{"added_loc":40,"added_loc":40,"deleted_loc":10}'
        duplicate_path.write_bytes(duplicate_payload)
        duplicate_reference["sha256"] = _sha(duplicate_payload)

        self._write(
            session_mutation,
            outcome_mutation,
            diff_mutation,
            merge_mutation,
            post_merge_mutation,
            noncanonical,
            unknown_key,
            duplicate_key,
        )
        result = self._evaluate()

        self.assertEqual(8, result["invalid_pairs"])
        reasons = [set(row["reasons"]) for row in result["rows"]]
        self.assertIn("arm_plain_session_evidence_payload_mismatch", reasons[0])
        self.assertIn("arm_plain_outcome_evidence_payload_mismatch", reasons[1])
        self.assertIn("arm_kimiflow_diff_evidence_payload_mismatch", reasons[2])
        self.assertIn("arm_kimiflow_merge_evidence_payload_mismatch", reasons[3])
        self.assertIn("arm_plain_post_merge_evidence_payload_mismatch", reasons[4])
        self.assertIn("arm_plain_outcome_evidence_payload_noncanonical", reasons[5])
        self.assertIn("arm_kimiflow_session_evidence_payload_shape_invalid", reasons[6])
        self.assertIn("arm_plain_diff_evidence_payload_malformed", reasons[7])

    def test_task_taxonomy_required_fields_and_confounds(self):
        feature = self._pair(71, "not_applicable")
        bug = self._pair(72, "not_applicable")
        bug["task"]["kind"] = "bug"

        wrong_kind = self._pair(73, "not_applicable")
        wrong_kind["task"]["kind"] = "fix"
        missing_task_id = self._pair(74, "not_applicable")
        missing_task_id.pop("task_id")

        missing_tool_calls = self._pair(75, "not_applicable")
        missing_tool_calls["arms"][0]["tool_calls"] = None
        self._sync_typed_evidence(missing_tool_calls, ("plain",), ("session",))
        invalid_tool_calls = self._pair(76, "not_applicable")
        invalid_tool_calls["arms"][1]["tool_calls"] = -1
        self._sync_typed_evidence(invalid_tool_calls, ("kimiflow",), ("session",))

        confound_note = self._pair(77, "not_applicable")
        confound_note["arms"][0]["confounds"] = ["different dependency cache"]
        self._sync_typed_evidence(confound_note, ("plain",), ("outcome",))
        empty_confounds = self._pair(78, "not_applicable")
        empty_confounds["arms"][0]["confounds"] = []
        self._sync_typed_evidence(empty_confounds, ("plain",), ("outcome",))
        invalid_confounds = self._pair(79, "not_applicable")
        invalid_confounds["arms"][1]["confounds"] = [""]
        self._sync_typed_evidence(invalid_confounds, ("kimiflow",), ("outcome",))

        self._write(
            feature,
            bug,
            wrong_kind,
            missing_task_id,
            missing_tool_calls,
            invalid_tool_calls,
            confound_note,
            empty_confounds,
            invalid_confounds,
        )
        result = self._evaluate()
        summary = outcome_comparisons.summarize(result)

        self.assertEqual(2, result["valid_pairs"])
        self.assertEqual(2, result["field_note_pairs"])
        self.assertEqual(5, result["invalid_pairs"])
        self.assertEqual({"bug": 1, "feature": 1}, summary["task_kind_distribution"])
        self.assertIn("tool_calls_missing", result["rows"][4]["reasons"])
        self.assertIn("confounds_present", result["rows"][6]["reasons"])
        self.assertIn("confounds_empty_invalid", result["rows"][7]["reasons"])

    def test_estimated_and_missing_measurements_are_excluded_without_guessing(self):
        estimated = self._pair(31)
        estimated["arms"][0]["measurement_source"] = "estimated"
        estimated["arms"][0]["input_tokens"] = None
        self._sync_typed_evidence(estimated, ("plain",), ("session",))
        missing_value = self._pair(32)
        missing_value["arms"][1]["accepted"] = None
        self._sync_typed_evidence(missing_value, ("kimiflow",), ("outcome",))
        missing_binding = self._pair(33)
        missing_binding["arms"][0]["outcome_evidence"] = None
        missing_binding["oracle_evidence"] = None
        missing_binding["arms"][1]["merged_at"] = None
        self._sync_typed_evidence(missing_binding, ("kimiflow",), ("merge",))
        self._write(estimated, missing_value, missing_binding)

        result = self._evaluate()
        summary = outcome_comparisons.summarize(result)

        self.assertEqual(0, result["invalid_pairs"])
        self.assertEqual(3, result["field_note_pairs"])
        self.assertEqual(3, result["excluded_pairs"])
        self.assertIsNone(summary["pairwise_differences"]["tokens"]["median"])
        self.assertEqual(0, summary["pairwise_differences"]["tokens"]["sample_count"])
        self.assertIn("measurement_source_not_actual", result["rows"][0]["reasons"])
        self.assertIn("input_tokens_missing", result["rows"][0]["reasons"])

    def test_summary_uses_only_valid_pairs_and_reports_pairwise_metrics(self):
        one = self._pair(41, "observed")
        two = self._pair(42, "not_applicable")
        two["task"]["kind"] = "bug"
        two["repository_id"] = "public-fixture-copy"
        note = self._pair(43)
        note["arms"][0]["input_tokens"] = None
        self._sync_typed_evidence(note, ("plain",), ("session",))
        invalid = self._pair(44)
        invalid["arms"][0]["source_commit"] = "0" * 40
        self._sync_typed_evidence(invalid, ("plain",), ("session",))
        self._write(one, two, note, invalid)

        summary = outcome_comparisons.summarize(self._evaluate())

        self.assertEqual(4, summary["recorded_pairs"])
        self.assertEqual(2, summary["valid_pairs"])
        self.assertEqual(1, summary["field_note_pairs"])
        self.assertEqual(1, summary["invalid_pairs"])
        self.assertEqual(2, summary["excluded_pairs"])
        self.assertEqual({"bug": 1, "feature": 1}, summary["task_kind_distribution"])
        self.assertEqual(
            {"benchmark-fixture": 1, "public-fixture-copy": 1},
            summary["repository_id_distribution"],
        )
        for metric in ("tokens", "time_seconds", "interactions", "rework", "loc"):
            self.assertEqual(2, summary["pairwise_differences"][metric]["sample_count"])
        for severity in ("blocker", "high"):
            self.assertEqual(
                2,
                summary["remaining_defect_differences"][severity]["sample_count"],
            )
        self.assertEqual(250, summary["pairwise_differences"]["tokens"]["median"])
        self.assertEqual(0, summary["post_merge"]["pending_windows"])
        self.assertEqual(1, summary["post_merge"]["observed_pairs"])
        self.assertEqual(1, summary["post_merge"]["not_applicable_pairs"])
        self.assertEqual({"blocker": 0, "high": 2}, summary["internal_defects"]["plain"])
        self.assertEqual(
            [
                {
                    "comparison_id": "comparison-41",
                    "task_id": "task-41",
                    "task_kind": "feature",
                    "verdict": "win",
                },
                {
                    "comparison_id": "comparison-42",
                    "task_id": "task-42",
                    "task_kind": "bug",
                    "verdict": "loss",
                },
            ],
            summary["per_task"],
        )

    def test_quality_first_wtl_and_claim_threshold(self):
        acceptance_loss = self._pair(51, "not_applicable")
        acceptance_loss["arms"][1]["accepted"] = False
        acceptance_loss["arms"][1]["input_tokens"] = 1
        acceptance_loss["arms"][1]["output_tokens"] = 1
        self._sync_typed_evidence(
            acceptance_loss, ("kimiflow",), ("session", "outcome")
        )
        pending = self._pair(52, "pending")
        cost_win = self._pair(53, "not_applicable")
        cost_win["arms"][1]["input_tokens"] = 100
        cost_win["arms"][1]["output_tokens"] = 100
        self._sync_typed_evidence(cost_win, ("kimiflow",), ("session",))
        post_merge_total_win = self._pair(54, "observed")
        post_merge_total_win["arms"][0]["post_merge_defects"] = {"blocker": 0, "high": 2}
        post_merge_total_win["arms"][1]["post_merge_defects"] = {"blocker": 1, "high": 0}
        self._sync_typed_evidence(post_merge_total_win, kinds=("post_merge",))
        self._write(acceptance_loss, pending, cost_win, post_merge_total_win)

        result = self._evaluate()
        by_id = {row["comparison_id"]: row for row in result["rows"]}
        self.assertEqual("loss", by_id["comparison-51"]["verdict"])
        self.assertEqual("pending", by_id["comparison-52"]["verdict"])
        self.assertEqual("win", by_id["comparison-53"]["verdict"])
        self.assertEqual("win", by_id["comparison-54"]["verdict"])
        self.assertEqual("insufficient_evidence", outcome_comparisons.summarize(result)["claim_status"])

        nine = [self._pair(60 + index, "not_applicable") for index in range(1, 10)]
        self._write(*nine)
        self.assertEqual(
            "insufficient_evidence",
            outcome_comparisons.summarize(self._evaluate())["claim_status"],
        )
        ten = nine + [self._pair(70, "not_applicable")]
        self._write(*ten)
        ten_summary = outcome_comparisons.summarize(self._evaluate())
        self.assertEqual(10, ten_summary["valid_pairs"])
        self.assertEqual("evidence_available", ten_summary["claim_status"])
        self.assertNotIn("significance", ten_summary)

    def test_boundary_types_and_arithmetic_fail_closed_without_crashing(self):
        boolean_schema = self._pair(80, "not_applicable")
        boolean_schema["schema_version"] = True

        array_kind = self._pair(81, "not_applicable")
        array_kind["task"]["kind"] = []

        late_window = self._pair(82)
        for arm in late_window["arms"]:
            arm.update(
                started_at="9999-12-30T00:00:00Z",
                completed_at="9999-12-30T00:01:00Z",
                merged_at="9999-12-31T00:00:00Z",
                window_ends_at="9999-12-31T00:00:00Z",
            )
        late_window["post_merge"]["observed_at"] = "9999-12-31T00:00:00Z"
        self._sync_typed_evidence(late_window)

        partial_merge = self._pair(83)
        partial_merge["arms"][0]["merged_at"] = None
        partial_merge["post_merge"]["observed_at"] = "2026-01-01T00:00:00Z"
        self._sync_typed_evidence(partial_merge, ("plain",), ("merge",))

        huge_one = self._pair(84, "not_applicable")
        huge_two = self._pair(85, "not_applicable")
        for row in (huge_one, huge_two):
            row["arms"][1]["input_tokens"] = 10**400
            self._sync_typed_evidence(row, ("kimiflow",), ("session",))

        self._write(
            boolean_schema,
            array_kind,
            late_window,
            partial_merge,
            huge_one,
            huge_two,
        )
        result = self._evaluate()

        self.assertEqual(4, result["invalid_pairs"])
        self.assertEqual(2, result["valid_pairs"])
        self.assertIn("schema_version_invalid", result["rows"][0]["reasons"])
        self.assertIn("task_kind_invalid", result["rows"][1]["reasons"])
        self.assertIn("arm_plain_window_boundary_overflow", result["rows"][2]["reasons"])
        self.assertIn("post_merge_observation_before_merge", result["rows"][3]["reasons"])
        median = outcome_comparisons.summarize(result)["pairwise_differences"]["tokens"]["median"]
        self.assertIsInstance(median, int)

        boolean_rows = [self._pair(index, "not_applicable") for index in range(90, 100)]
        for row in boolean_rows:
            row["schema_version"] = True
        self._write(*boolean_rows)
        boolean_result = self._evaluate()
        self.assertEqual(0, boolean_result["valid_pairs"])
        self.assertEqual("insufficient_evidence", boolean_result["claim_status"])

    def test_all_enum_types_and_arbitrary_precision_half_medians_fail_closed(self):
        invalid_rows = []
        measurement = self._pair(101, "not_applicable")
        measurement["arms"][0]["measurement_source"] = []
        invalid_rows.append(measurement)
        merge_status = self._pair(102, "not_applicable")
        merge_status["arms"][0]["merge_status"] = []
        invalid_rows.append(merge_status)
        post_merge_status = self._pair(103)
        post_merge_status["post_merge"]["status"] = []
        invalid_rows.append(post_merge_status)
        self._write(*invalid_rows)

        result = self._evaluate()

        self.assertEqual(3, result["invalid_pairs"])
        self.assertTrue(all(row["status"] == "invalid" for row in result["rows"]))

        magnitude = 10**400
        half_rows = [
            self._pair(104, "not_applicable"),
            self._pair(105, "not_applicable"),
        ]
        for offset, row in enumerate(half_rows):
            row["arms"][0].update(input_tokens=magnitude, output_tokens=0)
            row["arms"][1].update(input_tokens=offset, output_tokens=0)
            self._sync_typed_evidence(row, kinds=("session",))
        self._write(*half_rows)
        median = outcome_comparisons.summarize(self._evaluate())[
            "pairwise_differences"
        ]["tokens"]["median"]
        expected = Decimal("-%s.5" % (magnitude - 1))
        self.assertIsInstance(median, Decimal)
        self.assertEqual(expected, median)
        encoded = outcome_comparisons._json_output({"median": median})
        self.assertEqual(expected, json.loads(encoded, parse_float=Decimal)["median"])

    def test_cli_empty_dataset_and_schema_contract(self):
        repository = Path(__file__).resolve().parents[3]
        schema_path = repository / "evals" / "outcome-comparisons-v1.schema.json"
        empty_path = repository / "evals" / "outcome-comparisons.jsonl"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual("https://json-schema.org/draft/2020-12/schema", schema["$schema"])
        self.assertEqual(1, schema["properties"]["schema_version"]["const"])
        self.assertIn("task_id", schema["properties"])
        self.assertEqual(["bug", "feature"], schema["$defs"]["task"]["properties"]["kind"]["enum"])
        self.assertIn("tool_calls", schema["$defs"]["arm"]["properties"])
        self.assertIn("confounds", schema["$defs"]["arm"]["properties"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual("", empty_path.read_text(encoding="utf-8"))

        command = [str(repository / "hooks" / "outcome-comparisons.sh")]
        for operation in ("validate", "summary"):
            process = subprocess.run(
                command + [operation],
                cwd=repository,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(0, process.returncode, process.stderr)
            value = json.loads(process.stdout)
            self.assertEqual(0, value["recorded_pairs"])
            self.assertEqual("insufficient_evidence", value["claim_status"])
        unsupported = subprocess.run(
            command + ["run"],
            cwd=repository,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertNotEqual(0, unsupported.returncode)

        with tempfile.TemporaryDirectory() as foreign_directory:
            hostile = Path(foreign_directory) / "kimiflow_core"
            hostile.mkdir()
            (hostile / "__init__.py").write_text("", encoding="utf-8")
            (hostile / "outcome_comparisons.py").write_text(
                'print("HIJACKED")', encoding="utf-8"
            )
            foreign = subprocess.run(
                command + ["summary"],
                cwd=foreign_directory,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(0, foreign.returncode, foreign.stderr)
            self.assertNotIn("HIJACKED", foreign.stdout)
            self.assertEqual(0, json.loads(foreign.stdout)["recorded_pairs"])


if __name__ == "__main__":
    unittest.main()
