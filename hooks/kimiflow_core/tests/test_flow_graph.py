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
            {"from": "phase_6", "event": "verification_failed", "to": "phase_5", "action": "recover_build"},
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

    def write_manifest(self, flow=None, schema_version=2):
        manifest = {"schema_version": schema_version, "phases": phase_rows()}
        if flow is not None or schema_version >= 2:
            manifest["flow"] = flow if flow is not None else flow_contract()
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

        stale = self.resolve(event="verification_failed", stale={"risk": "needs_revalidation"})
        self.assertEqual(stale["action"], "revalidate_then_refresh_baseline")

        waiting = self.resolve(
            event="verification_failed",
            active={"status": "active", "awaiting_user": True},
        )
        self.assertEqual(waiting["action"], "wait_for_material_decision")

        self.write_state(current=4, recovery="active", review_gate="plan")
        recovery = self.resolve()
        self.assertEqual((recovery["action"], recovery["target_node"]), ("recover_plan_strategy", "phase_2"))

        self.write_state(current=5)
        items = self.resolve(counts={"pending": 1, "built": 1, "rejected": 1, "open": 3})
        self.assertEqual((items["action"], items["target_node"]), ("rework_rejected_items", "phase_5"))

    def test_legacy_manifest_preserves_coarse_action(self):
        self.write_manifest(schema_version=1)
        result = self.resolve(counts={"pending": 1, "built": 0, "rejected": 0, "open": 1})
        self.assertEqual(result["graph_status"], "legacy")
        self.assertEqual(result["action"], "resolve_or_accept_items")


if __name__ == "__main__":
    unittest.main()
