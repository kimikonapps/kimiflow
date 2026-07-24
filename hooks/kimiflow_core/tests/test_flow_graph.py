import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kimiflow_core import flow_graph


def phase_rows():
    return [
        {"id": idx, "name": "p%s" % idx, "file": "phases/phase-%s.md" % idx}
        for idx in range(8)
    ]


def flow_contract():
    transitions = [
        {
            "from": "phase_%s" % idx,
            "event": "phase_done",
            "to": "phase_%s" % (idx + 1) if idx < 7 else "done",
            "action": "run_phase" if idx < 7 else "finish_run",
        }
        for idx in range(8)
    ]
    transitions.extend(
        [
            {"from": "phase_4", "event": "plan_recovery", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_5", "event": "verification_failed", "to": "phase_5", "action": "recover_build"},
            {"from": "phase_5", "event": "strategy_drift", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_5", "event": "architecture_falsified", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_5", "event": "research_stale", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_6", "event": "verification_failed", "to": "phase_5", "action": "recover_build"},
            {"from": "phase_6", "event": "code_gap", "to": "phase_5", "action": "recover_build"},
            {"from": "phase_6", "event": "scope_drift", "to": "phase_5", "action": "recover_build"},
            {"from": "phase_6", "event": "strategy_drift", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_6", "event": "architecture_falsified", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_6", "event": "research_stale", "to": "phase_2", "action": "recover_plan_strategy"},
            {"from": "phase_7", "event": "review_failed", "to": "phase_5", "action": "recover_build"},
        ]
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


def execution_control_contract():
    return {
        "schema_version": 1,
        "profiles": ["compact", "standard", "critical"],
        "strategy_modes": ["normal", "recovery"],
        "budget_pressures": ["normal", "soft", "hard"],
        "no_progress_limit": 2,
        "max_trace_entries": 512,
        "budgets": {
            "small": {"soft_work_units": 8, "hard_work_units": 14},
            "medium": {"soft_work_units": 14, "hard_work_units": 24},
            "large": {"soft_work_units": 22, "hard_work_units": 36},
        },
    }


def execution_flow_contract():
    flow = flow_contract()
    actions = (
        "reassess_setup_strategy",
        "reframe_requirements",
        "broaden_evidence_strategy",
        "revise_plan_strategy",
        "change_review_strategy",
        "change_build_strategy",
        "change_verification_strategy",
        "change_commit_strategy",
    )
    flow["transitions"].extend(
        {"from": "phase_%s" % index, "event": "no_progress", "to": "phase_%s" % index, "action": action}
        for index, action in enumerate(actions)
    )
    return flow


class TestFlowGraph(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        os.makedirs(os.path.join(self.root, "phases"))
        self.addCleanup(shutil.rmtree, self.root)
        self.env = mock.patch.dict(os.environ, {"KIMIFLOW_PLUGIN_ROOT": self.root})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.write_manifest()
        self.write_state(current=5)

    def write_manifest(self, flow=None, schema_version=2, execution_control=None):
        manifest = {"schema_version": schema_version, "phases": phase_rows()}
        if flow is not None or schema_version >= 2:
            manifest["flow"] = flow if flow is not None else flow_contract()
        if execution_control is not None:
            manifest["execution_control"] = execution_control
        with open(os.path.join(self.root, "phases", "PHASES.json"), "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)

    def write_state(self, current=5, recovery="clean", review_gate="code", overrides=None):
        values = []
        for idx in range(8):
            if idx < current:
                value = "done"
            elif idx == current:
                value = "in-progress"
            else:
                value = "open"
            values.append(value)
        if overrides:
            for idx, value in overrides.items():
                values[idx] = value
        lines = ["Flow schema: 4", "Status: active", "Recovery: %s" % recovery, "Review gate: %s" % review_gate]
        lines.extend("Phase %s: %s" % (idx, value) for idx, value in enumerate(values))
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def resolve(self, event="", active=None, stale=None, counts=None):
        return flow_graph.resolve_transition(
            self.run,
            active=active or {"status": "active"},
            stale=stale or {"risk": "current"},
            item_counts=counts or {"pending": 0, "built": 0, "rejected": 0, "open": 0},
            event=event,
        )

    def test_manifest_validation_accepts_contract_and_rejects_ambiguous_edges(self):
        graph = flow_graph.load_graph()
        self.assertEqual(graph["terminal_node"], "done")
        self.assertEqual(len(graph["phase_entries"]), 8)

        self.write_manifest(schema_version=3)
        self.assertEqual(flow_graph.load_graph()["manifest_schema_version"], 3)
        missing_phase5_replan = flow_contract()
        missing_phase5_replan["transitions"] = [
            row
            for row in missing_phase5_replan["transitions"]
            if not (row["from"] == "phase_5" and row["event"] == "strategy_drift")
        ]
        self.write_manifest(missing_phase5_replan, schema_version=3)
        with self.assertRaises(flow_graph.FlowGraphError):
            flow_graph.load_graph()

        duplicate = flow_contract()
        duplicate["transitions"].append(dict(duplicate["transitions"][0]))
        self.write_manifest(duplicate)
        with self.assertRaises(flow_graph.FlowGraphError):
            flow_graph.load_graph()

        invalid_target = flow_contract()
        invalid_target["guards"][2]["target"] = "phase_99"
        self.write_manifest(invalid_target)
        with self.assertRaises(flow_graph.FlowGraphError):
            flow_graph.load_graph()

        invalid_action = flow_contract()
        invalid_action["transitions"][0]["action"] = "NOT-VALID"
        self.write_manifest(invalid_action)
        with self.assertRaises(flow_graph.FlowGraphError):
            flow_graph.load_graph()

        fractional = {"schema_version": 2, "phases": phase_rows(), "flow": flow_contract()}
        fractional["phases"][1]["id"] = 1.5
        with open(os.path.join(self.root, "phases", "PHASES.json"), "w", encoding="utf-8") as handle:
            json.dump(fractional, handle)
        with self.assertRaises(flow_graph.FlowGraphError):
            flow_graph.load_graph()

    def test_resume_resolves_phase_and_invalid_state_fails_closed(self):
        result = self.resolve()
        self.assertEqual(
            (result["graph_status"], result["current_node"], result["action"], result["target_node"]),
            ("ready", "phase_5", "run_phase", "phase_5"),
        )
        self.assertEqual(result["target_file"], "phases/phase-5.md")

        self.write_state(current=3, overrides={5: "done"})
        invalid = self.resolve()
        self.assertEqual(invalid["action"], "repair_state")
        self.assertEqual(invalid["graph_status"], "invalid_state")
        self.assertIsNone(invalid["current_node"])

    def test_events_and_durable_guards_choose_one_transition(self):
        next_phase = self.resolve(event="phase_done")
        self.assertEqual((next_phase["action"], next_phase["target_node"]), ("run_phase", "phase_6"))

        self.write_state(current=6)
        failed = self.resolve(event="verification_failed")
        self.assertEqual((failed["action"], failed["target_node"]), ("recover_build", "phase_5"))

        for event in ("code_gap", "scope_drift"):
            routed = self.resolve(event=event)
            self.assertEqual((routed["action"], routed["target_node"]), ("recover_build", "phase_5"))
            stale_routed = self.resolve(event=event, stale={"risk": "needs_revalidation"})
            self.assertEqual((stale_routed["action"], stale_routed["target_node"]), ("recover_build", "phase_5"))
        for event in ("strategy_drift", "architecture_falsified", "research_stale"):
            routed = self.resolve(event=event)
            self.assertEqual((routed["action"], routed["target_node"]), ("recover_plan_strategy", "phase_2"))
            stale_routed = self.resolve(event=event, stale={"risk": "needs_revalidation"})
            self.assertEqual((stale_routed["action"], stale_routed["target_node"]), ("recover_plan_strategy", "phase_2"))

        self.write_state(current=5)
        with mock.patch(
            "kimiflow_core.build_replan.verify_receipt",
            return_value={"valid": False, "reason": "receipt_missing"},
        ):
            for event in ("strategy_drift", "architecture_falsified", "research_stale"):
                routed = self.resolve(event=event)
                self.assertEqual((routed["action"], routed["target_node"]), ("recover_build", "phase_5"))
                self.assertEqual(routed["reason"], "replan_evidence:receipt_missing")
        with mock.patch(
            "kimiflow_core.build_replan.verify_receipt",
            return_value={"valid": True, "reason": "current"},
        ):
            routed = self.resolve(event="architecture_falsified")
            self.assertEqual((routed["action"], routed["target_node"]), ("recover_plan_strategy", "phase_2"))
        ordinary = self.resolve(event="verification_failed")
        self.assertEqual((ordinary["action"], ordinary["target_node"]), ("recover_build", "phase_5"))

        stale = self.resolve(event="verification_failed", stale={"risk": "needs_revalidation"})
        self.assertEqual(stale["action"], "revalidate_then_refresh_baseline")

        waiting = self.resolve(
            event="code_gap",
            active={"status": "active", "awaiting_user": True},
            stale={"risk": "needs_revalidation"},
        )
        self.assertEqual(waiting["action"], "wait_for_material_decision")

        self.write_state(current=4, recovery="active", review_gate="plan")
        recovery = self.resolve()
        self.assertEqual((recovery["action"], recovery["target_node"]), ("recover_plan_strategy", "phase_2"))

        self.write_state(current=5)
        items = self.resolve(counts={"pending": 1, "built": 1, "rejected": 1, "open": 3})
        self.assertEqual((items["action"], items["target_node"]), ("rework_rejected_items", "phase_5"))

        self.write_state(current=2)
        early_item = self.resolve(counts={"pending": 1, "built": 0, "rejected": 0, "open": 1})
        self.assertEqual((early_item["action"], early_item["target_node"]), ("run_phase", "phase_2"))

    def test_phase5_ordinary_failure_stays_local(self):
        self.write_state(current=5)
        routed = self.resolve(event="verification_failed")
        self.assertEqual(routed["current_node"], "phase_5")
        self.assertEqual((routed["action"], routed["target_node"]), ("recover_build", "phase_5"))

    def test_legacy_manifest_preserves_coarse_action(self):
        self.write_manifest(schema_version=1)
        result = self.resolve(counts={"pending": 1, "built": 0, "rejected": 0, "open": 1})
        self.assertEqual(result["graph_status"], "legacy")
        self.assertEqual(result["action"], "resolve_or_accept_items")

    def test_execution_contract_is_bounded_without_changing_selector_free_output(self):
        before = self.resolve()
        self.write_manifest(execution_flow_contract(), execution_control=execution_control_contract())
        graph = flow_graph.load_graph()
        self.assertEqual(graph["schema_version"], 1)
        self.assertEqual(graph["execution_control"]["profiles"], ["compact", "standard", "critical"])
        self.assertEqual(self.resolve(), before)

        recovery = self.resolve(event="no_progress")
        self.assertEqual((recovery["action"], recovery["target_node"]), ("change_build_strategy", "phase_5"))

        invalid = execution_control_contract()
        invalid["profiles"].append("recovery")
        self.write_manifest(execution_flow_contract(), execution_control=invalid)
        with self.assertRaises(flow_graph.FlowGraphError):
            flow_graph.load_graph()

        missing_edge = execution_flow_contract()
        missing_edge["transitions"] = [
            row
            for row in missing_edge["transitions"]
            if not (row["from"] == "phase_5" and row["event"] == "no_progress")
        ]
        self.write_manifest(missing_edge, execution_control=execution_control_contract())
        with self.assertRaises(flow_graph.FlowGraphError):
            flow_graph.load_graph()


if __name__ == "__main__":
    unittest.main()
