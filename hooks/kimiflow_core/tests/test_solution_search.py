import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kimiflow_core import model_adapter
from kimiflow_core import solution_search


def usage(input_tokens=10, output_tokens=5):
    return {
        "model_calls": 1,
        "tool_calls": 0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


BRIEF = {
    "intent": "Choose a safe local integration",
    "non_goals": ["No hosted service"],
    "project_facts": ["stdlib runtime"],
    "invariants": ["no raw candidate retention"],
    "evidence_ids": ["AC-8"],
}


class CandidateExecutor:
    enforces_work_unit_policy = True

    def __init__(self, responses=None, effects=None):
        self.responses = list(responses or [])
        self.effects = list(
            effects or ["no material change", "no material change", "no material change"]
        )
        self.calls = []
        self.roots = []

    def execute(self, envelope, root, policy, resume):
        self.calls.append((envelope, policy, resume))
        self.roots.append(root)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        index = len(self.calls) - 1
        return {
            "status": "completed",
            "usage": usage(),
            "candidate": {
                "brief_digest": envelope["brief_digest"],
                "approach": "raw approach %d" % (index + 1),
                "advantage": "benefit %d" % (index + 1),
                "risk": "risk %d" % (index + 1),
                "falsification": "test %d" % (index + 1),
                "checks": {
                    "intent": True,
                    "invariant": True,
                    "privacy": True,
                    "permissions": True,
                },
                "product_effect": self.effects[index],
            },
        }


class SelectorExecutor:
    enforces_work_unit_policy = True

    def __init__(self, response=None):
        self.response = response
        self.calls = []
        self.roots = []

    def execute(self, envelope, root, policy, resume):
        self.calls.append((envelope, policy, resume))
        self.roots.append(root)
        if isinstance(self.response, BaseException):
            raise self.response
        if self.response is not None:
            return self.response
        return {
            "status": "completed",
            "usage": usage(input_tokens=15),
            "selection": {
                "winner_id": "candidate-1",
                "alternative_id": "candidate-2",
                "scores": selector_scores(),
                "compliance": selector_compliance(),
            },
        }


def selector_scores():
    return {
        "candidate-%d" % index: {
            **{
                axis: 6 - index
                for axis in solution_search.SELECTOR_PRIMARY_AXES
            },
            "novelty": index,
        }
        for index in range(1, 4)
    }


def selector_compliance(intent=True, invariant=True, privacy=True, permissions=True):
    return {
        "candidate-%d" % index: {
            "intent": intent,
            "invariant": invariant,
            "privacy": privacy,
            "permissions": permissions,
        }
        for index in range(1, 4)
    }


def budgets(run_input=100, selector_input=20):
    return {
        "budget": {
            "model_calls": 4, "tool_calls": 0,
            "input_tokens": run_input, "output_tokens": 50,
        },
        "candidate_budget": {
            "model_calls": 1, "tool_calls": 0,
            "input_tokens": 20, "output_tokens": 10,
        },
        "selector_budget": {
            "model_calls": 1, "tool_calls": 0,
            "input_tokens": selector_input, "output_tokens": 20,
        },
    }


class SolutionSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = os.path.join(self.tmp.name, "project")
        self.vault = os.path.join(self.tmp.name, "vault")
        os.mkdir(self.project)
        os.mkdir(self.vault)

    def execute(self, candidates=None, selector=None, **updates):
        values = budgets()
        values.update(updates)
        return solution_search.execute_bounded(
            BRIEF,
            candidates or CandidateExecutor(),
            selector or SelectorExecutor(),
            project_root=self.project,
            vault_root=self.vault,
            decision_kind="integration",
            **values,
        )

    def test_off_path_has_no_units_or_context_artifact(self):
        calls = []
        result = solution_search.run(
            {"materially_open": False, "small_reversible": True},
            executor=lambda *args: calls.append(args),
        )
        self.assertEqual(result, {
            "schema_version": 1,
            "solution_search": "off",
            "reason": "small_reversible",
        })
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(
            solution_search.SolutionSearchError, "classification_conflict",
        ):
            solution_search.classify({
                "materially_open": True,
                "small_reversible": True,
                "decision_kind": "architecture",
            })

    def test_bounded_search_isolated_three_candidates_and_selector(self):
        candidates = CandidateExecutor()
        selector = SelectorExecutor()
        result = self.execute(candidates, selector)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(candidates.calls), 3)
        self.assertEqual(len(selector.calls), 1)
        self.assertEqual(
            [call[0]["lens"] for call in candidates.calls],
            list(solution_search.lenses_for("integration")),
        )
        for _, policy, resume in candidates.calls + selector.calls:
            self.assertEqual(policy["context_scope"], "sealed_input")
            self.assertEqual(policy["filesystem_access"], "none")
            self.assertEqual(policy["allowed_tools"], [])
            self.assertEqual(policy["settings_sources"], [])
            self.assertEqual(policy["mcp_servers"], [])
            self.assertFalse(policy["hooks"])
            self.assertIs(resume, False)
        for envelope, _, _ in candidates.calls:
            self.assertEqual(envelope["response_contract"]["code"], "forbidden")
            self.assertIn("falsification", envelope["response_contract"]["required"])
        self.assertEqual(
            selector.calls[0][0]["scoring_contract"]["primary_axes"],
            list(solution_search.SELECTOR_PRIMARY_AXES),
        )
        self.assertEqual(selector.calls[0][0]["brief"], BRIEF)
        self.assertEqual(
            selector.calls[0][0]["compliance_contract"]["source"],
            "independent_selector",
        )
        self.assertTrue(all(not os.path.exists(root) for root in candidates.roots + selector.roots))
        receipt = json.dumps(result["receipt"])
        self.assertNotIn("raw approach", receipt)
        self.assertNotIn(BRIEF["intent"], receipt)
        self.assertNotIn("candidate_digests", result["receipt"])
        self.assertEqual(
            set(result["receipt"]) & {
                "winner_digest", "alternative_digest", "candidate_digests",
            },
            {"winner_digest", "alternative_digest"},
        )
        self.assertRegex(result["winner_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(result["selected"]["approach"], "raw approach 1")
        self.assertEqual(
            result["strongest_alternative"]["approach"], "raw approach 2",
        )
        self.assertNotIn("raw approach", receipt)

        unavailable_candidates = CandidateExecutor(responses=[{
            "status": "completed",
            "usage": None,
            "candidate": {"approach": "never retained"},
        }])
        selector = SelectorExecutor()
        unavailable = self.execute(unavailable_candidates, selector)
        self.assertEqual(unavailable["error_code"], "budget_usage_unavailable")
        self.assertEqual(unavailable["receipt"]["selector_calls"], 0)
        self.assertEqual(len(selector.calls), 0)
        self.assertNotIn("never retained", json.dumps(unavailable))

        selector = SelectorExecutor(response={
            "status": "completed",
            "usage": usage(input_tokens=21),
            "selection": {
                "winner_id": "candidate-1",
                "alternative_id": "candidate-2",
                "scores": selector_scores(),
                "compliance": selector_compliance(),
            },
        })
        overrun = self.execute(selector=selector)
        self.assertEqual(overrun["error_code"], "budget_exceeded")
        self.assertEqual(overrun["receipt"]["selector_calls"], 1)
        self.assertNotIn("winner_digest", overrun.get("receipt", {}))

        selector_missing = SelectorExecutor(response={
            "status": "completed",
            "usage": None,
            "selection": {
                "winner_id": "candidate-1",
                "alternative_id": "candidate-2",
                "scores": selector_scores(),
                "compliance": selector_compliance(),
            },
        })
        missing = self.execute(selector=selector_missing)
        self.assertEqual(missing["error_code"], "budget_usage_unavailable")
        self.assertEqual(missing["receipt"]["selector_calls"], 1)

        reserve = self.execute(
            budget=budgets(run_input=50, selector_input=25)["budget"],
            candidate_budget=budgets()["candidate_budget"],
            selector_budget=budgets(run_input=50, selector_input=25)["selector_budget"],
        )
        self.assertEqual(reserve["error_code"], "budget_exceeded")
        self.assertEqual(reserve["receipt"]["candidate_calls"], 3)
        self.assertEqual(reserve["receipt"]["selector_calls"], 0)

        user = self.execute(candidates=CandidateExecutor(effects=["A", "B", "A"]))
        self.assertEqual(user["status"], "user_required")
        self.assertEqual(user["receipt"]["selector_calls"], 0)
        self.assertNotIn("raw approach", json.dumps(user))

        selector = SelectorExecutor()
        same_effect = self.execute(
            candidates=CandidateExecutor(effects=[
                "No material change",
                "no material change",
                " no   material change ",
            ]),
            selector=selector,
        )
        self.assertEqual(same_effect["status"], "completed")
        self.assertEqual(len(selector.calls), 1)

    def test_provider_and_compliance_failures_are_content_poor(self):
        provider = self.execute(
            candidates=CandidateExecutor(responses=[RuntimeError("raw provider secret")]),
        )
        self.assertEqual(provider["error_code"], "provider_failure")
        self.assertNotIn("raw provider secret", json.dumps(provider))

        failed = self.execute(candidates=CandidateExecutor(responses=[{
            "status": "failed",
            "usage": usage(),
            "candidate": {"approach": "must not survive"},
        }]))
        self.assertEqual(failed["error_code"], "provider_failure")
        self.assertEqual(failed["receipt"]["usage"], usage())
        self.assertNotIn("must not survive", json.dumps(failed))

        failed_unknown = self.execute(candidates=CandidateExecutor(responses=[{
            "status": "failed",
            "usage": None,
            "candidate": {"approach": "must not survive"},
        }]))
        self.assertEqual(
            failed_unknown["error_code"], "budget_usage_unavailable",
        )

        failed_over_budget = self.execute(candidates=CandidateExecutor(responses=[{
            "status": "failed",
            "usage": usage(input_tokens=21),
            "candidate": {"approach": "must not survive"},
        }]))
        self.assertEqual(failed_over_budget["error_code"], "budget_exceeded")
        self.assertEqual(failed_over_budget["receipt"]["usage"]["input_tokens"], 21)

        selector_failed = self.execute(selector=SelectorExecutor(response={
            "status": "failed",
            "usage": usage(input_tokens=15),
            "selection": {"winner_id": "must not survive"},
        }))
        self.assertEqual(selector_failed["error_code"], "selector_failure")
        self.assertEqual(selector_failed["receipt"]["usage"]["input_tokens"], 45)
        self.assertNotIn("must not survive", json.dumps(selector_failed))

        selector_failed_unknown = self.execute(selector=SelectorExecutor(response={
            "status": "failed",
            "usage": None,
            "selection": {"winner_id": "must not survive"},
        }))
        self.assertEqual(
            selector_failed_unknown["error_code"], "budget_usage_unavailable",
        )
        self.assertEqual(
            selector_failed_unknown["receipt"]["usage"]["input_tokens"], 30,
        )

        selector_failed_over_budget = self.execute(
            selector=SelectorExecutor(response={
                "status": "failed",
                "usage": usage(input_tokens=21),
                "selection": {"winner_id": "must not survive"},
            }),
        )
        self.assertEqual(
            selector_failed_over_budget["error_code"], "budget_exceeded",
        )
        self.assertEqual(
            selector_failed_over_budget["receipt"]["usage"]["input_tokens"], 51,
        )

        brief_digest = solution_search.digest(solution_search.seal_brief(BRIEF))
        rejected = self.execute(candidates=CandidateExecutor(responses=[{
            "status": "completed",
            "usage": usage(),
            "candidate": {
                "brief_digest": brief_digest,
                "approach": "raw rejected",
                "advantage": "benefit",
                "risk": "risk",
                "falsification": "test",
                "checks": {
                    "intent": True, "invariant": False,
                    "privacy": True, "permissions": True,
                },
                "product_effect": "no material change",
            },
        }]))
        self.assertEqual(rejected["error_code"], "compliance_rejected")
        self.assertNotIn("raw rejected", json.dumps(rejected))

        selector = SelectorExecutor()
        missing_effect = self.execute(
            candidates=CandidateExecutor(responses=[{
                "status": "completed",
                "usage": usage(),
                "candidate": {
                    "brief_digest": brief_digest,
                    "approach": "claims compliance",
                    "advantage": "benefit",
                    "risk": "risk",
                    "falsification": "test",
                    "checks": {
                        "intent": True, "invariant": True,
                        "privacy": True, "permissions": True,
                    },
                    "product_effect": None,
                },
            }]),
            selector=selector,
        )
        self.assertEqual(missing_effect["error_code"], "compliance_rejected")
        self.assertEqual(len(selector.calls), 0)

        code = self.execute(candidates=CandidateExecutor(responses=[{
            "status": "completed",
            "usage": usage(),
            "candidate": {
                "brief_digest": brief_digest,
                "approach": "```python\nprint('no')\n```",
                "advantage": "benefit",
                "risk": "risk",
                "falsification": "test",
                "checks": {
                    "intent": True, "invariant": True,
                    "privacy": True, "permissions": True,
                },
                "product_effect": "no material change",
            },
        }]))
        self.assertEqual(code["error_code"], "compliance_rejected")
        self.assertNotIn("print", json.dumps(code))

    def test_problem_dependent_third_lens_and_selector_scores_are_enforced(self):
        self.assertEqual(solution_search.lenses_for("architecture")[-1], "operations")
        self.assertEqual(solution_search.lenses_for("integration")[-1], "security")
        self.assertEqual(solution_search.lenses_for("ux_concept")[-1], "domain-transfer")

        wrong_order = selector_scores()
        wrong_order["candidate-3"] = {
            **{axis: 5 for axis in solution_search.SELECTOR_PRIMARY_AXES},
            "novelty": 5,
        }
        result = self.execute(selector=SelectorExecutor(response={
            "status": "completed",
            "usage": usage(input_tokens=15),
            "selection": {
                "winner_id": "candidate-1",
                "alternative_id": "candidate-2",
                "scores": wrong_order,
                "compliance": selector_compliance(),
            },
        }))
        self.assertEqual(result["error_code"], "selector_failure")
        self.assertNotIn("selected", result)

        independently_rejected = self.execute(selector=SelectorExecutor(response={
            "status": "completed",
            "usage": usage(input_tokens=15),
            "selection": {
                "winner_id": "candidate-1",
                "alternative_id": "candidate-2",
                "scores": selector_scores(),
                "compliance": selector_compliance(invariant=False),
            },
        }))
        self.assertEqual(
            independently_rejected["error_code"], "compliance_rejected",
        )
        self.assertNotIn("selected", independently_rejected)

    def test_bounded_cli_uses_fresh_native_adapters_and_off_stays_zero_call(self):
        instances = []

        class NativeAdapter:
            def __init__(self):
                self.calls = []
                instances.append(self)

            def info(self):
                return {
                    "schema_version": 1,
                    "name": "native-solution-fixture",
                    "host": "local",
                    "capabilities": {
                        key: True for key in model_adapter.CAPABILITY_KEYS
                    },
                    "features": {"work_unit_policy": True},
                }

            def start(self, root, prompt, on_session, work_unit_policy=None):
                envelope = json.loads(prompt)
                self.calls.append((root, envelope, work_unit_policy))
                on_session("fixture-session")
                if envelope["kind"] == "solution_candidate":
                    index = int(envelope["candidate_id"].split("-")[1])
                    response = {
                        "candidate": {
                            "brief_digest": envelope["brief_digest"],
                            "approach": "approach %d" % index,
                            "advantage": "advantage %d" % index,
                            "risk": "risk %d" % index,
                            "falsification": "test %d" % index,
                            "checks": {
                                "intent": True, "invariant": True,
                                "privacy": True, "permissions": True,
                            },
                            "product_effect": "no material change",
                        },
                    }
                else:
                    response = {
                        "selection": {
                            "winner_id": "candidate-1",
                            "alternative_id": "candidate-2",
                            "scores": selector_scores(),
                            "compliance": selector_compliance(),
                        },
                    }
                return model_adapter.TurnResult(
                    0,
                    session_id="fixture-session",
                    usage=usage(),
                    output={"messages": [json.dumps(response)]},
                )

        payload = {
            "facts": {"materially_open": True, "decision_kind": "integration"},
            "brief": BRIEF,
            **budgets(),
        }
        path = os.path.join(self.tmp.name, "bounded.json")
        Path(path).write_text(json.dumps(payload), encoding="utf-8")
        output = io.StringIO()
        with (
            mock.patch.object(
                solution_search, "_adapter_factory",
                return_value=lambda: NativeAdapter(),
            ),
            contextlib.redirect_stdout(output),
        ):
            status = solution_search.main([
                "--bounded", path,
                "--project-root", self.project,
                "--vault-root", self.vault,
            ])
        result = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["selected"]["approach"], "approach 1")
        executing = [instance for instance in instances if instance.calls]
        self.assertEqual(len(executing), 4)
        self.assertEqual(len({id(instance) for instance in executing}), 4)
        self.assertTrue(all(
            call[2]["context_scope"] == "sealed_input"
            for instance in executing for call in instance.calls
        ))
        self.assertTrue(all(
            not os.path.exists(call[0])
            for instance in executing for call in instance.calls
        ))

        off_payload = {
            **payload,
            "facts": {"materially_open": False, "small_reversible": True},
        }
        Path(path).write_text(json.dumps(off_payload), encoding="utf-8")
        output = io.StringIO()
        with (
            mock.patch.object(
                solution_search, "_adapter_factory",
                side_effect=AssertionError("off path constructed an adapter"),
            ),
            contextlib.redirect_stdout(output),
        ):
            status = solution_search.main(["--bounded", path])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["solution_search"], "off")

    def test_promotion_requires_quality_gain_within_token_budget(self):
        scenario = "sha256:" + "a" * 64
        baseline = {
            "scenario_digest": scenario,
            "metrics": {
                "intent_fidelity": 1,
                "first_plan_gate_opening": 1,
                "architecture_rollback_count": 2,
                "later_material_review_count": 1,
            },
            "tokens": 100,
            "rounds": 2,
            "duration_ms": 1000,
        }
        bounded = {
            **baseline,
            "metrics": {**baseline["metrics"], "architecture_rollback_count": 1},
            "tokens": 125,
            "rounds": 3,
            "duration_ms": 1500,
        }
        promoted = solution_search.promotion_decision(baseline, bounded)
        self.assertTrue(promoted["promote"])
        too_costly = {**bounded, "tokens": 126}
        rejected = solution_search.promotion_decision(baseline, too_costly)
        self.assertFalse(rejected["promote"])
        self.assertEqual(rejected["reason"], "token_budget_exceeded")
        regression = {
            **bounded,
            "metrics": {**bounded["metrics"], "intent_fidelity": 0},
        }
        self.assertEqual(
            solution_search.promotion_decision(baseline, regression)["reason"],
            "quality_regression",
        )
        invalid = {**bounded, "metrics": {**bounded["metrics"], "intent_fidelity": "1"}}
        with self.assertRaisesRegex(
            solution_search.SolutionSearchError, "promotion_metrics_invalid",
        ):
            solution_search.promotion_decision(baseline, invalid)

    def test_phase2_documents_bounded_solution_search(self):
        root = Path(__file__).resolve().parents[3]
        phase = (root / "phases" / "phase-2-understand.md").read_text(encoding="utf-8")
        reference = (root / "reference.md").read_text(encoding="utf-8")
        self.assertLess(
            phase.index("Mechanical Solution Search"),
            phase.index("Senior Design trigger"),
        )
        self.assertIn("strictly no-call/no-artifact", reference)
        self.assertIn("at most three deterministic lenses", reference)
        self.assertIn(
            "Only the chosen approach and strongest valid alternative", reference,
        )
        self.assertIn("content-poor receipt", reference)
        self.assertIn("solution-search.sh --bounded", reference)
        self.assertIn("new adapter instance", reference)
        self.assertIn("problem-dependent", reference)


if __name__ == "__main__":
    unittest.main()
