import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from kimiflow_core import adaptive_control


class AdaptiveControlTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)

    def write(self, name, value):
        path = os.path.join(self.run, name)
        if isinstance(value, dict):
            value = json.dumps(value)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(value)

    def evidence(self, value):
        return "sha256:" + ("%064x" % value)

    def test_classifier_is_conservative_and_keeps_product_decisions_open(self):
        self.write(
            "STATE.md",
            "Mode: feature\nScope: large\nAffected files:\n"
            "- schemas/account.json\n- workers/retention.py\n",
        )
        self.write(
            "INTENT.md",
            "Business rule and bounded context cross a durable schema and background job.\n"
            "Product decision: open\n",
        )
        value = adaptive_control.write_classification(self.root, self.run)
        self.assertEqual(value["scope"], "large")
        self.assertEqual(value["domain_complexity"], "active")
        self.assertEqual(value["operational_impact"], "active")
        self.assertTrue(value["product_decision_open"])
        self.assertEqual(value["intent_action"], "return_to_intake")
        self.assertNotIn("answer", value)
        path = os.path.join(self.run, adaptive_control.CLASSIFICATION_NAME)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.write(
            "STATE.md",
            "Mode: feature\nScope: large\nPhase 0: done\nPhase 1: done\n"
            "Conformance basis: changed-bookkeeping-only\nAffected files:\n"
            "- schemas/account.json\n- workers/retention.py\n",
        )
        self.assertEqual(
            adaptive_control.load_classification(self.root, self.run),
            value,
        )
        self.write(
            "INTENT.md",
            "Business rule and bounded context cross a durable schema and background job.\n"
            "Product decision: closed\n",
        )
        with self.assertRaisesRegex(
            adaptive_control.AdaptiveControlError, "classification_receipt_stale"
        ):
            adaptive_control.load_classification(self.root, self.run)
        value = adaptive_control.write_classification(self.root, self.run)

        self.write(
            "RESEARCH.md",
            "Domain evidence: context=accounts; language=retention; invariant=one-owner\n"
            "Operational evidence: signals=queue-depth; rollback=disable-worker; privacy=no-payload-logs\n",
        )
        self.write(
            "PLAN.md",
            "Domain check: AC-1 -> test_domain\n"
            "Operational check: AC-2 -> test_operations\n",
        )
        self.assertEqual(
            adaptive_control.verify_conditional_contract(self.root, self.run, "plan")["status"],
            "OPEN",
        )
        self.assertEqual(
            adaptive_control.verify_conditional_contract(self.root, self.run, "verify")["status"],
            "CLOSED",
        )
        self.write(
            "VERIFICATION.md",
            "Domain verification: passed; AC=AC-1\n"
            "Operational verification: passed; AC=AC-2\n",
        )
        self.assertEqual(
            adaptive_control.verify_conditional_contract(self.root, self.run, "verify")["status"],
            "OPEN",
        )

    def test_classifier_ignores_phase_architecture_bookkeeping_and_explicit_non_goals(self):
        self.write(
            "STATE.md",
            "Mode: feature\nScope: small\nArchitecture deliberation: pending\n"
            "Build risk: pending\nAffected files:\n- helpers/text.py\n",
        )
        self.write(
            "INTENT.md",
            "Add a local helper. No unresolved product decision remains.\n"
            "Out of scope: database, security, provider, network.\n",
        )
        value = adaptive_control.write_classification(self.root, self.run)
        self.assertEqual(value["scope"], "small")
        self.assertEqual(value["operational_impact"], "off")
        self.assertFalse(value["product_decision_open"])

        self.write(
            "STATE.md",
            "Mode: feature\nScope: small\nArchitecture deliberation: off\n"
            "Build risk: none\nPhase 2: done\nAffected files:\n- helpers/text.py\n",
        )
        self.assertEqual(
            adaptive_control.load_classification(self.root, self.run),
            value,
        )

    def test_rollover_requires_material_or_measured_pressure_and_keeps_resume_basis(self):
        previous = {
            "phase": 2,
            "estimated_tokens": 30000,
            "composite_basis": "sha256:" + "1" * 64,
            "selection": [
                {"kind": "artifact", "name": "INTENT.md", "bytes": 10, "sha256": "sha256:" + "2" * 64},
                {"kind": "artifact", "name": "RESEARCH.md", "bytes": 10, "sha256": "sha256:" + "3" * 64},
            ],
        }
        current = {
            "phase": 3,
            "estimated_tokens": 30000,
            "composite_basis": "sha256:" + "4" * 64,
            "selection": [
                {"kind": "artifact", "name": "PLAN.md", "bytes": 10, "sha256": "sha256:" + "5" * 64},
                {"kind": "artifact", "name": "ACCEPTANCE.md", "bytes": 10, "sha256": "sha256:" + "6" * 64},
            ],
        }
        self.assertEqual(
            adaptive_control.decide_rollover(previous, current, "small")["status"], "off"
        )
        pending = adaptive_control.decide_rollover(previous, current, "large")
        self.assertEqual(pending["status"], "pending")
        self.assertRegex(pending["rollover_id"], r"^roll_[0-9a-f]{32}$")
        self.assertEqual({row["name"] for row in pending["retained"]}, {"PLAN.md", "ACCEPTANCE.md"})
        newer = dict(current, composite_basis="sha256:" + "7" * 64)
        refreshed = adaptive_control.retarget_rollover(pending, newer)
        self.assertNotEqual(refreshed["rollover_id"], pending["rollover_id"])
        self.assertEqual(refreshed["current_digest"], newer["composite_basis"])
        self.assertEqual(refreshed["reason"], "pending_context_updated")
        adaptive_control.write_rollover(self.root, self.run, refreshed)
        with self.assertRaisesRegex(
            adaptive_control.AdaptiveControlError, "rollover_handoff_stale"
        ):
            adaptive_control.handoff_rollover(
                self.root, self.run, pending["rollover_id"], pending["current_digest"],
            )
        handoff = adaptive_control.handoff_rollover(
            self.root, self.run, refreshed["rollover_id"], refreshed["current_digest"],
        )
        self.assertEqual(handoff["status"], "fresh_context_handoff")
        adaptive_control.write_rollover(self.root, self.run, pending)
        stale = adaptive_control.acknowledge_rollover(
            self.root, self.run, "roll_" + "0" * 32, pending["current_digest"], 10, 5,
        )
        self.assertEqual(stale["status"], "stale_acknowledgement")
        self.assertEqual(adaptive_control.pending_rollover(self.root, self.run)["rollover_id"], pending["rollover_id"])
        ack = adaptive_control.acknowledge_rollover(
            self.root, self.run, pending["rollover_id"], pending["current_digest"], 100, 40,
        )
        self.assertEqual(ack["status"], "acknowledged")

    def test_model_route_requires_five_clean_samples_and_revokes_on_regression(self):
        roles = {"top": "sol", "balanced": "qwen", "cheap": "small"}
        first = adaptive_control.resolve_model_roles(self.root, roles)
        self.assertEqual(first["roles"]["balanced"], "sol")
        for index in range(5):
            adaptive_control.record_model_outcome(
                self.root, "sample_%024x" % index,
                "balanced", "qwen", "sol", "routine", "passed",
                input_tokens=100 + index, output_tokens=20,
                evidence_digest=self.evidence(index + 1),
            )
        # Replaying one run is byte-idempotent and cannot manufacture a sixth sample.
        replay = adaptive_control.record_model_outcome(
            self.root, "sample_%024x" % 4,
            "balanced", "qwen", "sol", "routine", "passed",
            input_tokens=104, output_tokens=20,
            evidence_digest=self.evidence(5),
        )
        self.assertEqual(replay["sample_id"], "sample_%024x" % 4)
        eligible = adaptive_control.resolve_model_roles(self.root, roles, write=True)
        self.assertEqual(eligible["roles"]["balanced"], "qwen")
        adaptive_control.record_model_outcome(
            self.root, "sample_%024x" % 99,
            "balanced", "qwen", "sol", "routine", "failed", high_findings=1,
            evidence_digest=self.evidence(100),
        )
        revoked = adaptive_control.resolve_model_roles(self.root, roles)
        self.assertEqual(revoked["roles"]["balanced"], "sol")
        critical = adaptive_control.resolve_model_roles(self.root, roles, risk="critical")
        self.assertEqual(critical["roles"]["balanced"], "sol")

    def test_model_route_requires_observed_usage_and_receipts_aggregate_it(self):
        roles = {"top": "sol", "balanced": "qwen"}
        for index in range(5):
            adaptive_control.record_model_outcome(
                self.root, "sample_%024x" % (200 + index),
                "balanced", "qwen", "sol", "routine", "passed",
            )
        missing_usage = adaptive_control.resolve_model_roles(self.root, roles)
        self.assertEqual(missing_usage["roles"]["balanced"], "sol")
        self.assertEqual(missing_usage["decisions"]["balanced"]["usage_samples"], 0)

        for index in range(5):
            adaptive_control.record_model_outcome(
                self.root, "sample_%024x" % (300 + index),
                "balanced", "qwen", "sol", "routine", "passed",
                input_tokens=80, output_tokens=20,
                evidence_digest=self.evidence(300 + index),
            )
        measured = adaptive_control.resolve_model_roles(self.root, roles)
        self.assertEqual(measured["roles"]["balanced"], "qwen")
        self.assertEqual(measured["decisions"]["balanced"]["usage_samples"], 5)
        self.assertEqual(measured["decisions"]["balanced"]["input_tokens"], 400)
        self.assertEqual(measured["decisions"]["balanced"]["output_tokens"], 100)
        self.assertEqual(measured["decisions"]["balanced"]["average_total_tokens"], 100)

    def test_model_record_derives_metrics_from_verified_run_artifacts(self):
        with self.assertRaisesRegex(
            adaptive_control.AdaptiveControlError, "model_outcome_run_not_verified"
        ):
            adaptive_control.record_verified_model_outcome(
                self.root, self.run, "balanced", "qwen", "sol",
            )
        self.assertFalse(os.path.exists(os.path.join(
            self.root, ".kimiflow", "project", adaptive_control.MODEL_LEDGER_NAME,
        )))

        self.write(
            "STATE.md",
            "Status: done\nPhase 6: done\nPhase 7: done\nRecovery: clean\n"
            "Review gate: code\nArchitecture deliberation: off\nBuild risk: none\n",
        )
        self.write("SESSION-OUTCOME.json", {
            "schema_version": 1,
            "outcome": "done",
        })
        self.write(
            "VERIFICATION.md",
            "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->\n",
        )
        self.write("CODE-REVIEW.md", "All code-review axes verified.\n")
        os.makedirs(os.path.join(self.run, "findings"))
        self.write("findings/r1-code-verified.md", "NONE\n")
        self.write("HOST-USAGE.json", {
            "schema_version": 1,
            "status": "available",
            "model_calls": 3,
            "tool_calls": 8,
            "input_tokens": 120,
            "output_tokens": 30,
        })
        self.write("EXECUTION-TRACE.json", {
            "schema_version": 1,
            "contract": 1,
            "entries": [{"outcome": "passed"}],
        })
        adaptive_control.record_model_route_usage(
            self.root,
            self.run,
            {"role": "balanced", "model": "qwen", "baseline": "sol"},
            {
                "model_calls": 2,
                "tool_calls": 5,
                "input_tokens": 100,
                "output_tokens": 25,
            },
        )
        row = adaptive_control.record_verified_model_outcome(
            self.root, self.run, "balanced", "qwen", "sol",
        )
        self.assertEqual(row["schema_version"], 2)
        self.assertEqual(row["outcome"], "passed")
        self.assertEqual(row["risk"], "routine")
        self.assertEqual(row["high_findings"], 0)
        self.assertEqual(row["retries"], 0)
        self.assertEqual(row["input_tokens"], 100)
        self.assertEqual(row["output_tokens"], 25)
        self.assertRegex(row["evidence_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_model_record_rejects_open_review_and_unavailable_usage(self):
        self.write(
            "STATE.md",
            "Status: done\nPhase 6: done\nPhase 7: done\nRecovery: clean\n"
            "Review gate: code\nArchitecture deliberation: off\nBuild risk: none\n",
        )
        self.write("SESSION-OUTCOME.json", {
            "schema_version": 1,
            "outcome": "done",
        })
        self.write(
            "VERIFICATION.md",
            "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->\n",
        )
        self.write("CODE-REVIEW.md", "Review pending.\n")
        os.makedirs(os.path.join(self.run, "findings"))
        self.write(
            "findings/r1-code-verified.md",
            "FINDING HIGH x :: broken :: class=x :: verify=command:false :: "
            "evidence=review-evidence/x@%s\n" % ("a" * 64),
        )
        self.write("HOST-USAGE.json", {
            "schema_version": 1,
            "status": "unavailable",
            "model_calls": None,
            "tool_calls": None,
            "input_tokens": None,
            "output_tokens": None,
        })
        self.write("EXECUTION-TRACE.json", {
            "schema_version": 1,
            "contract": 1,
            "entries": [],
        })
        adaptive_control.record_model_route_usage(
            self.root,
            self.run,
            {"role": "balanced", "model": "qwen", "baseline": "sol"},
            {
                "model_calls": 1,
                "tool_calls": 1,
                "input_tokens": 10,
                "output_tokens": 2,
            },
        )
        with self.assertRaisesRegex(
            adaptive_control.AdaptiveControlError, "model_outcome_review_open"
        ):
            adaptive_control.record_verified_model_outcome(
                self.root, self.run, "balanced", "qwen", "sol",
            )
        self.write("findings/r2-code-verified.md", "NONE\n")
        with self.assertRaisesRegex(
            adaptive_control.AdaptiveControlError, "model_outcome_usage_unavailable"
        ):
            adaptive_control.record_verified_model_outcome(
                self.root, self.run, "balanced", "qwen", "sol",
            )

    def test_terminal_failed_candidate_is_recorded_and_revokes_route(self):
        roles = {"top": "sol", "balanced": "qwen"}
        for index in range(5):
            adaptive_control.record_model_outcome(
                self.root,
                "sample_%024x" % (500 + index),
                "balanced",
                "qwen",
                "sol",
                "routine",
                "passed",
                input_tokens=50,
                output_tokens=10,
                evidence_digest=self.evidence(500 + index),
            )
        self.assertEqual(
            adaptive_control.resolve_model_roles(self.root, roles)["roles"]["balanced"],
            "qwen",
        )
        self.write(
            "STATE.md",
            "Status: failed\nArchitecture deliberation: off\nBuild risk: none\n",
        )
        self.write("SESSION-OUTCOME.json", {
            "schema_version": 1,
            "outcome": "failed",
        })
        self.write("HOST-USAGE.json", {
            "schema_version": 1,
            "status": "available",
            "model_calls": 1,
            "tool_calls": 2,
            "input_tokens": 40,
            "output_tokens": 8,
        })
        self.write("EXECUTION-TRACE.json", {
            "schema_version": 1,
            "contract": 1,
            "entries": [{"outcome": "failed"}],
        })
        adaptive_control.record_model_route_usage(
            self.root,
            self.run,
            {"role": "balanced", "model": "qwen", "baseline": "sol"},
            {
                "model_calls": 1,
                "tool_calls": 2,
                "input_tokens": 40,
                "output_tokens": 8,
            },
        )

        recorded = adaptive_control.record_observed_model_outcomes(
            self.root, self.run,
        )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["outcome"], "failed")
        self.assertGreaterEqual(recorded[0]["retries"], 1)
        self.assertEqual(
            adaptive_control.resolve_model_roles(self.root, roles)["roles"]["balanced"],
            "sol",
        )

    def test_model_record_cli_replays_attested_routes_without_identity_arguments(self):
        with mock.patch.object(
            adaptive_control.paths, "resolve_root", return_value=self.root,
        ):
            self.assertEqual(
                adaptive_control.main([
                    "model-record",
                    "--root",
                    self.root,
                    "--run",
                    self.run,
                ]),
                0,
            )
            self.assertEqual(
                adaptive_control.main([
                    "model-record",
                    "--root",
                    self.root,
                    "--run",
                    self.run,
                    "--role",
                    "balanced",
                ]),
                2,
            )

    def test_model_route_receipt_cannot_be_redirected_outside_workspace(self):
        detached = self.run + ".detached"
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        real_atomic_write = adaptive_control.memory_store.atomic_write
        exchanged = {"done": False}

        def exchange_before_write(path, data, **kwargs):
            if not exchanged["done"]:
                os.rename(self.run, detached)
                os.symlink(outside, self.run)
                exchanged["done"] = True
            return real_atomic_write(path, data, **kwargs)

        with mock.patch.object(
            adaptive_control.memory_store,
            "atomic_write",
            side_effect=exchange_before_write,
        ):
            with self.assertRaisesRegex(
                adaptive_control.AdaptiveControlError,
                "model_route_evidence_unsafe",
            ):
                adaptive_control.record_model_route_usage(
                    self.root,
                    self.run,
                    {"role": "balanced", "model": "qwen", "baseline": "sol"},
                    {
                        "model_calls": 1,
                        "tool_calls": 1,
                        "input_tokens": 10,
                        "output_tokens": 2,
                    },
                )
        self.assertFalse(os.path.exists(os.path.join(
            outside, adaptive_control.MODEL_ROUTE_EVIDENCE_NAME,
        )))

    def test_rollover_receipt_cannot_be_redirected_outside_workspace(self):
        detached = self.run + ".detached"
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        real_atomic_write = adaptive_control.memory_store.atomic_write
        exchanged = {"done": False}

        def exchange_before_write(path, data, **kwargs):
            if not exchanged["done"]:
                os.rename(self.run, detached)
                os.symlink(outside, self.run)
                exchanged["done"] = True
            return real_atomic_write(path, data, **kwargs)

        rollover = {
            "schema_version": 1,
            "status": "off",
            "rollover_id": "roll_" + "a" * 32,
        }
        with mock.patch.object(
            adaptive_control.memory_store,
            "atomic_write",
            side_effect=exchange_before_write,
        ):
            with self.assertRaisesRegex(
                adaptive_control.AdaptiveControlError,
                "run_receipt_unsafe",
            ):
                adaptive_control.write_rollover(self.root, self.run, rollover)
        self.assertFalse(os.path.exists(os.path.join(
            outside, adaptive_control.ROLLOVER_NAME,
        )))

    def test_model_ledger_cannot_be_redirected_outside_workspace(self):
        project = os.path.join(self.root, ".kimiflow", "project")
        detached = project + ".detached"
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        real_atomic_write = adaptive_control.memory_store.atomic_write
        exchanged = {"done": False}

        def exchange_before_write(path, data, **kwargs):
            if (
                not exchanged["done"]
                and os.path.basename(path) == adaptive_control.MODEL_LEDGER_NAME
            ):
                os.rename(project, detached)
                os.symlink(outside, project)
                exchanged["done"] = True
            return real_atomic_write(path, data, **kwargs)

        with mock.patch.object(
            adaptive_control.memory_store,
            "atomic_write",
            side_effect=exchange_before_write,
        ):
            with self.assertRaises(
                adaptive_control.memory_store.ConcurrentWriteError,
            ):
                adaptive_control.record_model_outcome(
                    self.root,
                    "sample_" + "a" * 24,
                    "balanced",
                    "qwen",
                    "sol",
                    "routine",
                    "passed",
                    input_tokens=10,
                    output_tokens=2,
                    evidence_digest="sha256:" + "b" * 64,
                )
        self.assertFalse(os.path.exists(os.path.join(
            outside, adaptive_control.MODEL_LEDGER_NAME,
        )))

    def test_no_trigger_and_missing_capabilities_use_existing_flow_without_user_gate(self):
        self.write("STATE.md", "Mode: feature\nScope: small\nAffected files:\n- helpers/text.py\n")
        self.write("INTENT.md", "Add a local reversible helper using the existing pattern.\n")
        value = adaptive_control.classify(self.root, self.run)
        self.assertEqual(value["scope"], "small")
        self.assertEqual(value["domain_complexity"], "off")
        self.assertEqual(value["operational_impact"], "off")
        self.assertFalse(value["product_decision_open"])
        self.assertFalse(value.get("user_gate", False))
        route = adaptive_control.resolve_model_roles(self.root, {"top": "sol", "balanced": "qwen"})
        self.assertEqual(route["roles"]["balanced"], "sol")
        self.assertFalse(route["user_gate"])
        self.assertIsNone(adaptive_control.pending_rollover(self.root, self.run))
        self.assertFalse(os.path.exists(os.path.join(self.run, "VAULT-RECALL.md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".kimiflow", "archive")))

    def test_retrieval_route_uses_multidimensional_outcome_and_revokes_on_regression(self):
        provider = self.evidence(900)
        task_class = "cross-file"
        initial = adaptive_control.resolve_retrieval_route(self.root, provider, task_class)
        self.assertEqual(initial["route"], "shadow")
        adaptive_control.record_retrieval_outcome(
            self.root, "sample_%024x" % 900, provider, task_class, "holdout",
            True, False, provider_latency_ms=12,
        )
        adaptive_control.record_retrieval_outcome(
            self.root, "sample_%024x" % 901, provider, task_class, "shadow",
            True, False, provider_latency_ms=8,
        )
        self.assertEqual(
            adaptive_control.resolve_retrieval_route(self.root, provider, task_class)["route"],
            "canary",
        )
        for index in range(5):
            adaptive_control.record_retrieval_outcome(
                self.root, "sample_%024x" % (910 + index), provider, task_class, "canary",
                True, True, logical_input_tokens=100, provider_latency_ms=7,
            )
        self.assertEqual(
            adaptive_control.resolve_retrieval_route(self.root, provider, task_class)["route"],
            "active",
        )
        adaptive_control.record_retrieval_outcome(
            self.root, "sample_%024x" % 999, provider, task_class, "canary",
            False, False, high_findings=1, retries=1, token_waste=True,
        )
        revoked = adaptive_control.resolve_retrieval_route(self.root, provider, task_class)
        self.assertEqual(revoked["route"], "off")
        self.assertEqual(revoked["reason"], "quality_regression")
        self.assertFalse(revoked["user_gate"])

    def test_retrieval_canaries_cannot_bypass_holdout_and_shadow(self):
        provider = self.evidence(950)
        task_class = "cross-file"
        for index in range(5):
            adaptive_control.record_retrieval_outcome(
                self.root, "sample_%024x" % (950 + index), provider, task_class,
                "canary", True, True, logical_input_tokens=100,
                provider_latency_ms=7,
            )
        result = adaptive_control.resolve_retrieval_route(
            self.root, provider, task_class
        )
        self.assertEqual(result["route"], "shadow")
        self.assertEqual(result["reason"], "evidence_pending")

    def test_review_mode_requires_calibration_samples_runtime_binding_and_one_in_ten_audit(self):
        key = {
            "model_fingerprint": self.evidence(1001),
            "execution_variant": "budget8192",
            "role": "balanced",
            "task_class": "routine-code",
            "runtime_fingerprint": self.evidence(1002),
            "policy_fingerprint": self.evidence(1003),
            "prompt_gate_fingerprint": self.evidence(1004),
        }
        pending = adaptive_control.resolve_review_mode(self.root, **key)
        self.assertEqual(pending["review_mode"], "single-independent")
        for index in range(5):
            adaptive_control.record_review_outcome(
                self.root, "sample_%024x" % (1000 + index), **key,
                quality_passed=True,
            )
        selections = []
        for index in range(10):
            selection = adaptive_control.resolve_review_mode(self.root, **key)
            selections.append(selection)
            adaptive_control.record_review_outcome(
                self.root, "sample_%024x" % (1100 + index), **key,
                quality_passed=True,
            )
        self.assertEqual(sum(row["audit_sample"] for row in selections), 1)
        self.assertEqual(
            sum(row["review_mode"] == "single-independent" for row in selections), 1,
        )
        drifted = dict(key, runtime_fingerprint=self.evidence(2000))
        self.assertEqual(
            adaptive_control.resolve_review_mode(self.root, **drifted)["review_mode"],
            "single-independent",
        )
        self.assertEqual(
            adaptive_control.resolve_review_mode(self.root, **key, risk="critical")["review_mode"],
            "ensemble",
        )
        adaptive_control.record_review_outcome(
            self.root, "sample_%024x" % 1200, **key,
            quality_passed=False, high_findings=1, audit_finding=True,
        )
        revoked = adaptive_control.resolve_review_mode(self.root, **key)
        self.assertEqual(revoked["review_mode"], "single-independent")
        self.assertEqual(revoked["reason"], "regression_or_failure")


if __name__ == "__main__":
    unittest.main()
