import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from kimiflow_core import execution_control, flow_graph


def phase_rows():
    return [
        {"id": index, "name": "p%s" % index, "file": "phases/phase-%s.md" % index}
        for index in range(8)
    ]


def flow_contract():
    transitions = [
        {
            "from": "phase_%s" % index,
            "event": "phase_done",
            "to": "phase_%s" % (index + 1) if index < 7 else "done",
            "action": "run_phase" if index < 7 else "finish_run",
        }
        for index in range(8)
    ]
    transitions.extend(
        [
            {"from": "phase_4", "event": "plan_recovery", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_6", "event": "verification_failed", "to": "phase_5", "action": "recover_build"},
            {"from": "phase_6", "event": "code_gap", "to": "phase_5", "action": "recover_build"},
            {"from": "phase_6", "event": "scope_drift", "to": "phase_5", "action": "recover_build"},
            {"from": "phase_6", "event": "strategy_drift", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_6", "event": "architecture_falsified", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_6", "event": "research_stale", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_7", "event": "review_failed", "to": "phase_5", "action": "recover_build"},
        ]
    )
    recovery_actions = (
        "reassess_setup_strategy",
        "reframe_requirements",
        "broaden_evidence_strategy",
        "revise_plan_strategy",
        "change_review_strategy",
        "change_build_strategy",
        "change_verification_strategy",
        "change_commit_strategy",
    )
    transitions.extend(
        {
            "from": "phase_%s" % index,
            "event": "no_progress",
            "to": "phase_%s" % index,
            "action": recovery_actions[index],
        }
        for index in range(8)
    )
    return {
        "schema_version": 1,
        "terminal_node": "done",
        "guards": [
            {"condition": "awaiting_user", "action": "wait_for_material_decision", "target": "current", "blocks_events": True},
            {"condition": "stale", "action": "revalidate_then_refresh_baseline", "target": "current", "blocks_events": True},
            {"condition": "recovery_plan", "action": "recover_plan_strategy", "target": "phase_2", "blocks_events": False},
            {"condition": "recovery_code", "action": "recover_build", "target": "phase_5", "blocks_events": False},
            {"condition": "items_rejected", "action": "rework_rejected_items", "target": "phase_5", "blocks_events": False},
            {"condition": "items_pending", "action": "build_pending_items", "target": "phase_5", "blocks_events": False},
            {"condition": "items_built", "action": "verify_built_items", "target": "phase_6", "blocks_events": False},
        ],
        "transitions": transitions,
    }


def execution_contract():
    return {
        "schema_version": 1,
        "profiles": ["compact", "standard", "critical"],
        "strategy_modes": ["normal", "recovery"],
        "budget_pressures": ["normal", "soft", "hard"],
        "no_progress_limit": 2,
        "max_trace_entries": 32,
        "budgets": {
            "small": {"soft_work_units": 3, "hard_work_units": 5},
            "medium": {"soft_work_units": 5, "hard_work_units": 8},
            "large": {"soft_work_units": 8, "hard_work_units": 12},
        },
    }


class TestExecutionControl(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        os.makedirs(os.path.join(self.root, "phases"))
        with open(os.path.join(self.root, "phases", "PHASES.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema_version": 2,
                    "phases": phase_rows(),
                    "flow": flow_contract(),
                    "execution_control": execution_contract(),
                },
                handle,
            )
        self.env = mock.patch.dict(os.environ, {"KIMIFLOW_PLUGIN_ROOT": self.root})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.write_state()
        self.active = {
            "status": "active",
            "scope": "large",
            "execution_contract": "1",
            "run_device": os.lstat(self.run).st_dev,
            "run_inode": os.lstat(self.run).st_ino,
        }
        self.counts = {"total": 1, "pending": 1, "built": 0, "accepted": 0, "rejected": 0, "dropped": 0, "open": 1}

    def write_state(self, extra="", build_risk="none", current=5):
        lines = [
            "Flow schema: 4",
            "Execution contract: 1",
            "Scope: large",
            "Build risk: %s" % build_risk,
            "Recovery: clean",
            "Review gate: code",
            "Review epoch: 1",
            "Strategy fingerprint: " + ("a" * 64),
            "Conformance basis: verified",
        ]
        lines.extend(
            "Phase %s: %s" % (
                index,
                "done" if index < current else "in-progress" if index == current else "open",
            )
            for index in range(8)
        )
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n" + extra)

    def transitions(self):
        normal = flow_graph.resolve_transition(
            self.run,
            active=self.active,
            stale={"risk": "current"},
            item_counts=self.counts,
        )
        recovery = flow_graph.resolve_transition(
            self.run,
            active=self.active,
            stale={"risk": "current"},
            item_counts=self.counts,
            event="no_progress",
        )
        return normal, recovery

    def observe(self, **kwargs):
        normal, recovery = self.transitions()
        return execution_control.observe(
            self.root,
            self.run,
            self.active,
            self.counts,
            normal,
            recovery,
            event=kwargs.pop("event", "turn_completed"),
            outcome=kwargs.pop("outcome", "neutral"),
            write=kwargs.pop("write", True),
            **kwargs
        )

    def test_no_progress_is_run_wide_and_file_churn_does_not_reset_it(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        first = self.observe()
        self.assertEqual(first["summary"]["no_progress_streak"], 1)

        source = os.path.join(self.root, "source.py")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write("# semantics-free churn\n")
        second = self.observe()
        self.assertEqual(second["summary"]["no_progress_streak"], 2)
        self.assertEqual(second["summary"]["strategy_mode"], "recovery")
        self.assertEqual(second["transition"]["action"], "change_build_strategy")
        self.assertEqual(second["summary"]["profile"], "standard")
        self.assertEqual(second["summary"]["profile_reason"], "medium_or_large_scope")

        evidence = os.path.join(self.run, "VERIFICATION.md")
        with open(evidence, "w", encoding="utf-8") as handle:
            handle.write("named check passed\n")
        reset = self.observe(evidence=evidence, outcome="passed")
        self.assertEqual(reset["summary"]["no_progress_streak"], 0)
        self.assertEqual(reset["summary"]["strategy_mode"], "normal")

    def test_budget_profiles_are_bounded_and_recovery_is_orthogonal(self):
        self.write_state(build_risk="required — security boundary")
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        latest = None
        for _ in range(12):
            latest = self.observe(tool_calls=1)
        self.assertEqual(latest["summary"]["budget_pressure"], "hard")
        self.assertEqual(latest["summary"]["directive"], "prune_optional_work")
        self.assertEqual(latest["summary"]["profile"], "critical")
        self.assertEqual(latest["summary"]["profile_reason"], "material_build_risk")
        self.assertEqual(latest["summary"]["strategy_mode"], "recovery")
        self.assertEqual(latest["summary"]["usage"]["tool_calls"], 12)

        self.write_state(build_risk="none")
        compact = self.observe()
        self.assertEqual(compact["summary"]["budget_pressure"], "hard")
        self.assertEqual(compact["summary"]["profile"], "compact")
        self.assertEqual(compact["summary"]["profile_reason"], "hard_budget_pressure")
        self.assertEqual(compact["summary"]["strategy_mode"], "normal")

    def test_accepted_evidence_is_new_once_per_run_not_just_different_from_last(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        first_path = os.path.join(self.run, "first.txt")
        second_path = os.path.join(self.run, "second.txt")
        with open(first_path, "w", encoding="utf-8") as handle:
            handle.write("first\n")
        with open(second_path, "w", encoding="utf-8") as handle:
            handle.write("second\n")
        self.observe(evidence=first_path, outcome="passed")
        self.observe(evidence=second_path, outcome="passed")
        replay = self.observe(evidence=first_path, outcome="passed")
        self.assertEqual(replay["summary"]["no_progress_streak"], 1)
        self.assertEqual(len(replay["summary"]["accepted_evidence_fingerprints"]), 2)

        with open(second_path, "w", encoding="utf-8") as handle:
            handle.write("  second\n\n")
        whitespace_replay = self.observe(evidence=second_path, outcome="passed")
        self.assertEqual(whitespace_replay["summary"]["no_progress_streak"], 2)
        self.assertEqual(len(whitespace_replay["summary"]["accepted_evidence_fingerprints"]), 2)

        with open(second_path, "w", encoding="utf-8") as handle:
            handle.write("second\n<!-- formatting only -->\n")
        comment_replay = self.observe(evidence=second_path, outcome="passed")
        self.assertEqual(comment_replay["summary"]["no_progress_streak"], 3)
        self.assertEqual(len(comment_replay["summary"]["accepted_evidence_fingerprints"]), 2)

    def test_atomic_private_journal_hashes_evidence_and_reads_are_read_only(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        evidence = os.path.join(self.run, "private-evidence.txt")
        secret = "private raw secret value"
        with open(evidence, "w", encoding="utf-8") as handle:
            handle.write(secret)
        result = self.observe(evidence=evidence, outcome="progress")
        path = execution_control.trace_path(self.run)
        with open(path, "rb") as handle:
            before = handle.read()
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertNotIn(secret.encode("utf-8"), before)
        self.assertRegex(result["entries"][-1]["evidence_fingerprint"], r"^[0-9a-f]{64}$")

        inspected = execution_control.inspect(self.root, self.run, self.active)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(inspected["summary"], result["summary"])

        with mock.patch("kimiflow_core.execution_control.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(execution_control.ExecutionControlError):
                self.observe(model_calls=1)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_post_replace_directory_fsync_failure_does_not_invite_double_counting(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        original_fsync = os.fsync
        calls = {"count": 0}

        def fail_directory_sync(descriptor):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("directory sync unavailable")
            return original_fsync(descriptor)

        with mock.patch("kimiflow_core.execution_control.os.fsync", side_effect=fail_directory_sync):
            result = self.observe(model_calls=1)
        self.assertEqual(result["summary"]["work_units"], 1)
        stored = execution_control.inspect(self.root, self.run, self.active)
        self.assertEqual(stored["summary"]["work_units"], 1)
        self.assertEqual(stored["summary"]["usage"]["model_calls"], 1)

    def test_summary_directive_tampering_fails_closed(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        path = execution_control.trace_path(self.run)
        with open(path, "r", encoding="utf-8") as handle:
            original = json.load(handle)

        injected = json.loads(json.dumps(original))
        injected["summary"]["directive"] = "INJECTED"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(injected, handle)
        with self.assertRaises(execution_control.ExecutionControlError):
            execution_control.inspect(self.root, self.run, self.active)

        missing = json.loads(json.dumps(original))
        del missing["summary"]["directive"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(missing, handle)
        with self.assertRaises(execution_control.ExecutionControlError):
            execution_control.inspect(self.root, self.run, self.active)

    def test_summary_rollups_must_match_latest_entry_and_budget_formula(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        self.observe()
        self.observe()
        path = execution_control.trace_path(self.run)
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["summary"].update(
            {
                "work_units": 0,
                "budget_score": 0,
                "no_progress_streak": 0,
                "strategy_mode": "normal",
            }
        )
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        with self.assertRaisesRegex(execution_control.ExecutionControlError, "execution_trace_rollup_invalid"):
            execution_control.inspect(self.root, self.run, self.active)

    def test_cumulative_usage_is_anchored_in_the_latest_entry(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        self.observe(model_calls=1, input_tokens=9000)
        path = execution_control.trace_path(self.run)
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["summary"]["usage"] = {key: 0 for key in execution_control.USAGE_KEYS}
        payload["summary"]["budget_score"] = payload["summary"]["work_units"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        with self.assertRaisesRegex(execution_control.ExecutionControlError, "execution_trace_rollup_invalid"):
            execution_control.inspect(self.root, self.run, self.active)

    def test_unknown_trace_fields_fail_closed_before_the_byte_cap_can_wedge_writes(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        path = execution_control.trace_path(self.run)
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        payload["entries"][-1]["padding"] = "x" * (execution_control.MAX_TRACE_BYTES - 4096)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        with self.assertRaises(execution_control.ExecutionControlError):
            execution_control.inspect(self.root, self.run, self.active)

    def test_deep_json_inputs_fail_closed_without_raw_recursion_errors(self):
        path = execution_control.trace_path(self.run)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[" * 200000 + "]" * 200000)
        with self.assertRaises(execution_control.ExecutionControlError):
            execution_control.inspect(self.root, self.run, self.active)

        os.unlink(path)
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        evidence = os.path.join(self.run, "deep.json")
        with open(evidence, "w", encoding="utf-8") as handle:
            handle.write("[" * 200000 + "]" * 200000)
        with self.assertRaises(execution_control.ExecutionControlError):
            self.observe(event="verification", evidence=evidence, outcome="passed")

    def test_comment_stripping_is_linear_for_unclosed_comment_prefixes(self):
        payload = "prefix " + ("<!--" * 200000)
        self.assertEqual(execution_control._strip_html_comments(payload), "prefix ")

    def test_duplicate_state_keys_fail_closed_instead_of_downgrading_risk(self):
        self.write_state(
            extra="Build risk: none\n",
            build_risk="required — security boundary",
        )
        with self.assertRaises(execution_control.ExecutionControlError):
            execution_control.initialize(
                self.root,
                self.run,
                self.active,
                self.counts,
                self.transitions()[0],
                write=True,
            )

    def test_finish_requires_an_observation_of_current_semantic_state(self):
        self.counts = {
            "total": 1,
            "pending": 0,
            "built": 0,
            "accepted": 1,
            "rejected": 0,
            "dropped": 0,
            "open": 0,
        }
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        self.write_state(current=7)
        with self.assertRaisesRegex(
            execution_control.ExecutionControlError,
            "execution_control_requires_current_observation",
        ):
            execution_control.require_finishable(self.root, self.run, self.active, self.counts)

        self.observe(outcome="progress")
        result = execution_control.require_finishable(self.root, self.run, self.active, self.counts)
        self.assertEqual(result["summary"]["last_node"], "phase_7")
        self.write_state(current=8)
        terminal_ready = execution_control.require_finishable(self.root, self.run, self.active, self.counts)
        self.assertEqual(terminal_ready["summary"]["last_node"], "phase_7")

    def test_coalesced_stop_records_later_semantic_progress_without_double_charge(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        evidence = os.path.join(self.run, "VERIFICATION.md")
        with open(evidence, "w", encoding="utf-8") as handle:
            handle.write("passed\n")
        explicit = self.observe(event="verification", evidence=evidence, outcome="passed")
        self.assertEqual(explicit["summary"]["work_units"], 1)

        self.write_state(current=6)
        coalesced = self.observe(coalesce_pending_stop=True)
        self.assertEqual(coalesced["summary"]["work_units"], 1)
        self.assertEqual(coalesced["summary"]["no_progress_streak"], 0)
        self.assertEqual(coalesced["summary"]["last_node"], "phase_6")
        node_entries = [entry for entry in coalesced["entries"] if entry["kind"] == "node_transition"]
        self.assertEqual(node_entries[-1]["current_node"], "phase_5")
        self.assertEqual(node_entries[-1]["target_node"], "phase_6")

    def test_finalize_records_the_actual_terminal_graph_edge_without_charging_work(self):
        self.counts = {
            "total": 1,
            "pending": 0,
            "built": 0,
            "accepted": 1,
            "rejected": 0,
            "dropped": 0,
            "open": 0,
        }
        self.write_state(current=8)
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        result = execution_control.finalize(self.root, self.run, self.active, self.counts, write=True)
        self.assertEqual(result["summary"]["last_node"], "done")
        self.assertEqual(result["summary"]["work_units"], 0)
        terminal = [
            entry
            for entry in result["entries"]
            if entry["kind"] == "node_transition" and entry["target_node"] == "done"
        ]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["current_node"], "phase_7")
        self.assertEqual(terminal[0]["action"], "finish_run")
        self.assertTrue(terminal[0]["executed"])
        retried = execution_control.finalize(self.root, self.run, self.active, self.counts, write=True)
        self.assertEqual(retried["summary"]["sequence"], result["summary"]["sequence"])
        finishable = execution_control.require_finishable(self.root, self.run, self.active, self.counts)
        self.assertEqual(finishable["summary"]["last_node"], "done")

    def test_durable_phase_change_records_one_executed_node_transition(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        self.write_state(current=6)
        result = self.observe(outcome="progress")
        node_entries = [entry for entry in result["entries"] if entry["kind"] == "node_transition"]
        self.assertEqual(len(node_entries), 1)
        self.assertEqual(node_entries[0]["from_node"], "phase_5")
        self.assertEqual(node_entries[0]["current_node"], "phase_5")
        self.assertEqual(node_entries[0]["action"], "run_phase")
        self.assertEqual(node_entries[0]["target_node"], "phase_6")
        self.assertTrue(node_entries[0]["executed"])
        self.assertFalse(result["entries"][-1]["executed"])

    def test_trace_rolls_forward_without_blocking_autonomous_progress(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        latest = None
        for _ in range(40):
            latest = self.observe()
        self.assertEqual(len(latest["entries"]), 32)
        self.assertEqual(latest["summary"]["sequence"], 41)
        self.assertEqual(latest["summary"]["dropped_entries"], 9)
        self.assertEqual(latest["entries"][0]["sequence"], 10)

        stored = {key: value for key, value in latest.items() if key != "transition"}
        offset = 2007
        stored["summary"]["sequence"] += offset
        stored["summary"]["dropped_entries"] += offset
        for entry in stored["entries"]:
            entry["sequence"] += offset
        with open(execution_control.trace_path(self.run), "w", encoding="utf-8") as handle:
            json.dump(stored, handle)
        beyond_old_limit = self.observe()
        self.assertEqual(beyond_old_limit["summary"]["sequence"], 2049)
        self.assertEqual(execution_control.inspect(self.root, self.run, self.active)["summary"]["sequence"], 2049)

    def test_evidence_history_has_its_own_bounded_capacity_beyond_trace_window(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        latest = None
        for index in range(33):
            evidence = os.path.join(self.run, "evidence-%s.txt" % index)
            with open(evidence, "w", encoding="utf-8") as handle:
                handle.write("result %s\n" % index)
            latest = self.observe(event="verification", evidence=evidence, outcome="passed")
        self.assertEqual(len(latest["entries"]), 32)
        self.assertEqual(len(latest["summary"]["accepted_evidence_fingerprints"]), 33)
        self.assertEqual(latest["summary"]["no_progress_streak"], 0)

    def test_evidence_must_be_safe_and_journal_refuses_symlinks(self):
        execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=True)
        outside = os.path.join(self.root, "..", "outside.txt")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("outside")
        self.addCleanup(lambda: os.path.exists(outside) and os.unlink(outside))
        with self.assertRaises(execution_control.ExecutionControlError):
            self.observe(evidence=outside, outcome="passed")

        os.unlink(execution_control.trace_path(self.run))
        os.symlink(outside, execution_control.trace_path(self.run))
        with self.assertRaises(execution_control.ExecutionControlError):
            execution_control.inspect(self.root, self.run, self.active)

        os.unlink(execution_control.trace_path(self.run))
        with open(execution_control.trace_path(self.run), "wb") as handle:
            handle.write(b"{" + (b"x" * execution_control.MAX_TRACE_BYTES))
        with self.assertRaises(execution_control.ExecutionControlError):
            execution_control.inspect(self.root, self.run, self.active)

    def test_preview_and_legacy_run_create_no_artifacts(self):
        preview = execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=False)
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(os.path.exists(execution_control.trace_path(self.run)))
        self.assertIsNone(execution_control.inspect(self.root, self.run, {"scope": "large"}))


if __name__ == "__main__":
    unittest.main()
