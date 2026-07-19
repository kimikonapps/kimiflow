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
        "max_trace_entries": 512,
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

    def write_state(self, extra="", build_risk="none"):
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
        lines.extend("Phase %s: %s" % (index, "done" if index < 5 else "in-progress" if index == 5 else "open") for index in range(8))
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
        self.assertEqual(latest["summary"]["strategy_mode"], "recovery")
        self.assertEqual(latest["summary"]["usage"]["tool_calls"], 12)

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

    def test_preview_and_legacy_run_create_no_artifacts(self):
        preview = execution_control.initialize(self.root, self.run, self.active, self.counts, self.transitions()[0], write=False)
        self.assertEqual(preview["status"], "preview")
        self.assertFalse(os.path.exists(execution_control.trace_path(self.run)))
        self.assertIsNone(execution_control.inspect(self.root, self.run, {"scope": "large"}))


if __name__ == "__main__":
    unittest.main()
