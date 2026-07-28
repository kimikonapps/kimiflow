import json
import stat
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from kimiflow_core import security_deep


FINGERPRINT = security_deep.current_contract_fingerprint()


def usage(input_tokens=1, output_tokens=1):
    return {"model_calls": 0, "tool_calls": 0, "input_tokens": input_tokens, "output_tokens": output_tokens}


def surface(index, lane):
    digest = "sha256:" + ("%064x" % (index + 1))
    return {
        "id": "surface-%d" % index, "lane": lane, "scope_digest": digest,
        "guidance_digest": digest, "provider_evidence_digest": digest,
        "declared_budget": usage(),
    }


def plan(surfaces, *, workers=4, budget=None, fingerprint=FINGERPRINT):
    return {
        "schema_version": 1, "worker_limit": workers, "token_budget": budget or usage(100, 100),
        "contract_fingerprint": fingerprint, "surfaces": surfaces,
    }


class DeepSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def test_worker_and_usage_budgets_defer_without_overrun(self):
        calls = []

        def executor(item):
            calls.append(item["id"])
            if item["id"] == "surface-1":
                return {"status": "complete", "usage": usage(2, 1), "findings": []}
            return {"status": "complete", "usage": usage(), "findings": []}

        result = security_deep.run_deep(
            plan([surface(index, lane) for index, lane in enumerate(security_deep.LANES[:5])]),
            executor, root=self.root,
        )
        self.assertEqual(calls, ["surface-0", "surface-1"])
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual([row["status"] for row in result["receipts"]].count("deferred"), 3)
        self.assertIn("budget_exceeded", [row["status"] for row in result["receipts"]])

    def test_worker_limit_defers_excess_surfaces(self):
        calls = []
        result = security_deep.run_deep(
            plan(
                [
                    surface(index, lane)
                    for index, lane in enumerate(security_deep.LANES)
                ],
                workers=1,
            ),
            lambda item: (
                calls.append(item["id"])
                or {
                    "status": "complete",
                    "usage": usage(),
                    "findings": [],
                }
            ),
            root=self.root,
        )
        self.assertEqual(calls, ["surface-0"])
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(
            [row["status"] for row in result["receipts"]].count("deferred"),
            6,
        )

    def test_token_budget_admission_is_prefix_closed(self):
        calls = []
        surfaces = [
            surface(index, lane)
            for index, lane in enumerate(security_deep.LANES[:3])
        ]
        surfaces[0]["declared_budget"] = usage(1, 1)
        surfaces[1]["declared_budget"] = usage(5, 5)
        surfaces[2]["declared_budget"] = usage(1, 1)
        result = security_deep.run_deep(
            plan(surfaces, budget=usage(2, 2)),
            lambda item: (
                calls.append(item["id"])
                or {
                    "status": "complete",
                    "usage": usage(0, 0),
                    "findings": [],
                }
            ),
            root=self.root,
        )
        self.assertEqual(calls, ["surface-0"])
        self.assertEqual(
            [row["status"] for row in result["receipts"][:3]],
            ["complete", "deferred", "deferred"],
        )

    def test_failed_refused_timeout_deferred_never_clean(self):
        statuses = iter(["failed", "refused", "timeout", "complete"])
        result = security_deep.run_deep(
            plan([surface(index, lane) for index, lane in enumerate(security_deep.LANES[:5])]),
            lambda _item: {"status": next(statuses), "usage": usage(), "findings": []}, root=self.root,
        )
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(result["status"], "incomplete")
        self.assertGreaterEqual(len(result["gaps"]), 4)

    def test_model_executor_reuses_bounded_work_unit_engine(self):
        class Executor:
            def execute(self, _unit, _policy):
                raise AssertionError("mocked")

        execution = {
            "synthesis": [{
                "unit_id": "surface-0",
                "output": {"status": "complete", "findings": []},
            }],
            "usage": usage(),
        }
        with mock.patch.object(
            security_deep.work_units, "execute_plan", return_value=execution,
        ) as execute:
            result = security_deep.run_deep(
                plan([surface(0, "secrets")]), Executor(), root=self.root,
            )
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result["executed_units"], 1)
        self.assertEqual(result["verdict"], "incomplete")

    def test_cli_deep_requires_plan_bound_local_evidence(self):
        surfaces = [
            surface(index, lane)
            for index, lane in enumerate(security_deep.LANES)
        ]
        evidence_rows = []
        for item in surfaces:
            response = {
                "id": item["id"],
                "status": "complete",
                "usage": usage(),
                "findings": [],
            }
            item["provider_evidence_digest"] = security_deep.digest(response)
            evidence_rows.append(response)
        deep_plan = plan(surfaces)
        evidence = {
            "schema_version": 1,
            "contract_fingerprint": FINGERPRINT,
            "surfaces": evidence_rows,
        }
        plan_path = Path(self.root) / "plan.json"
        evidence_path = Path(self.root) / "evidence.json"
        plan_path.write_text(json.dumps(deep_plan), encoding="utf-8")
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        result = security_deep.security.run_from_args(Namespace(
            security_command="deep",
            plan=str(plan_path),
            evidence=str(evidence_path),
            root=self.root,
        ))
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(result["executed_units"], 4)
        frozen = security_deep.evidence_executor(deep_plan, evidence)
        evidence["surfaces"][0]["findings"].append(
            security_deep.security._finding(
                "fixture", "1", "late-mutation", "late mutation",
                "high", "fixture.py", 1, "fixture",
            )
        )
        self.assertEqual(frozen(surfaces[0])["findings"], [])
        returned = frozen(surfaces[0])
        returned["findings"].append(
            security_deep.security._finding(
                "fixture", "1", "returned-mutation", "returned mutation",
                "high", "fixture.py", 1, "fixture",
            )
        )
        returned["usage"]["input_tokens"] = 99
        self.assertEqual(frozen(surfaces[0])["findings"], [])
        self.assertEqual(frozen(surfaces[0])["usage"], usage())
        tampered = json.loads(json.dumps(evidence))
        tampered["surfaces"][0]["status"] = "refused"
        with self.assertRaisesRegex(
            security_deep.DeepSecurityError, "evidence_binding_invalid",
        ):
            security_deep.evidence_executor(deep_plan, tampered)

    def test_cache_identity_and_zero_repeat_usage(self):
        calls = []

        def executor(item):
            calls.append(item["id"])
            return {"status": "complete", "usage": usage(3, 2), "findings": []}

        original = plan([surface(0, "secrets")])
        first = security_deep.run_deep(original, executor, root=self.root)
        second = security_deep.run_deep(original, executor, root=self.root)
        self.assertEqual(calls, ["surface-0"])
        self.assertEqual(second["cache_hits"], 1)
        self.assertEqual(second["executed_units"], 0)
        self.assertEqual(second["usage"], usage(0, 0))
        self.assertEqual(first["result_seal"], second["result_seal"])
        cache = next((Path(self.root) / ".kimiflow" / "security" / "deep-cache").iterdir())
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
        changed = json.loads(json.dumps(original))
        changed["surfaces"][0]["scope_digest"] = "sha256:" + "f" * 64
        security_deep.run_deep(changed, executor, root=self.root)
        self.assertEqual(calls, ["surface-0", "surface-0"])
        self.assertNotEqual(first["cache_key"], security_deep.run_deep(changed, executor, root=self.root)["cache_key"])
        stale = json.loads(json.dumps(original))
        stale["contract_fingerprint"] = "sha256:" + "e" * 64
        with self.assertRaisesRegex(
            security_deep.DeepSecurityError, "contract_fingerprint_invalid",
        ):
            security_deep.run_deep(stale, executor, root=self.root)
        with tempfile.NamedTemporaryFile() as changed_adapter:
            changed_adapter.write(b"changed adapter contract")
            changed_adapter.flush()
            with mock.patch.object(
                security_deep.model_adapter,
                "__file__",
                changed_adapter.name,
            ):
                self.assertNotEqual(
                    FINGERPRINT,
                    security_deep.current_contract_fingerprint(),
                )

    def test_cache_envelope_cannot_reuse_result_for_another_key(self):
        calls = []

        def executor(item):
            calls.append(item["id"])
            return {
                "status": "complete",
                "usage": usage(),
                "findings": [],
            }

        first_plan = plan([surface(0, "secrets")])
        second_plan = json.loads(json.dumps(first_plan))
        second_plan["surfaces"][0]["scope_digest"] = "sha256:" + "f" * 64
        first = security_deep.run_deep(first_plan, executor, root=self.root)
        second = security_deep.run_deep(second_plan, executor, root=self.root)
        first_path = Path(security_deep._cache_path(
            self.root, first["cache_key"],
        ))
        second_path = Path(security_deep._cache_path(
            self.root, second["cache_key"],
        ))
        forged = json.loads(second_path.read_text(encoding="utf-8"))
        forged["cache_key"] = first["cache_key"]
        forged = security_deep._seal(forged)
        first_path.write_text(json.dumps(forged), encoding="utf-8")
        replay = security_deep.run_deep(
            first_plan, executor, root=self.root,
        )
        self.assertEqual(calls, ["surface-0", "surface-0", "surface-0"])
        self.assertEqual(replay["cache_hits"], 0)
        self.assertEqual(replay["cache_key"], first["cache_key"])

    def test_seven_provider_lanes_are_normalized_and_optional(self):
        supplied = set(security_deep.LANES[:3])
        result = security_deep.run_deep(
            plan([surface(index, lane) for index, lane in enumerate(security_deep.LANES)], workers=4),
            lambda item: {"status": "complete" if item["lane"] in supplied else "missing", "usage": usage(), "findings": []},
            root=self.root,
        )
        self.assertEqual({row["lane"] for row in result["receipts"]}, set(security_deep.LANES))
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(len(result["gaps"]), 4)

    def test_empty_findings_status_becomes_explicit_failed_gap(self):
        result = security_deep.run_deep(
            plan([
                surface(index, lane)
                for index, lane in enumerate(security_deep.LANES)
            ]),
            lambda item: {
                "status": "findings" if item["id"] == "surface-0" else "complete",
                "usage": usage(),
                "findings": [],
            },
            root=self.root,
        )
        target = next(
            row for row in result["receipts"]
            if row["lane"] == security_deep.LANES[0]
        )
        self.assertEqual(target["status"], "failed")
        self.assertEqual(target["coverage"], "gap")
        self.assertEqual(result["verdict"], "incomplete")

    def test_portable_projection_is_allowlist_only(self):
        finding = security_deep.security._finding(
            "fixture", "1", "fixture-rule", "CANARY-SECRET",
            "high", "private/work/app.py", 1, "fixture",
        )
        finding["occurrences"][0]["path"] = "/private/work/app.py"

        def executor(item):
            return {
                "status": "findings" if item["lane"] == "dependencies" else "complete",
                "usage": usage(),
                "findings": [finding] if item["lane"] == "dependencies" else [],
            }

        result = security_deep.run_deep(
            plan([
                surface(index, lane)
                for index, lane in enumerate(security_deep.LANES)
            ]),
            executor,
            root=self.root,
        )
        portable = security_deep.portable_artifact(result)
        text = json.dumps(portable)
        self.assertNotIn("CANARY-SECRET", text)
        self.assertNotIn("/private/work", text)
        self.assertTrue(portable["privacy"]["allowlist_only"])
        forged = json.loads(json.dumps(result))
        forged["receipts"][0]["lane"] = "/private/work"
        forged = security_deep._seal(forged)
        with self.assertRaisesRegex(
            security_deep.DeepSecurityError, "result_invalid",
        ):
            security_deep.portable_artifact(forged)

    def test_unplanned_lanes_are_explicit_gaps(self):
        result = security_deep.run_deep(
            plan([surface(0, "secrets")]),
            lambda _item: {
                "status": "complete", "usage": usage(), "findings": [],
            },
            root=self.root,
        )
        self.assertEqual(result["verdict"], "incomplete")
        self.assertEqual(
            {row["lane"] for row in result["receipts"]},
            set(security_deep.LANES),
        )

    def test_committed_diff_artifact_is_credential_free_and_content_poor(self):
        repository = Path(self.root) / "repository"
        repository.mkdir()
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "fixture"],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(repository), "config", "user.email",
                "fixture@example.invalid",
            ],
            check=True,
        )
        (repository / "app.py").write_text("safe = True\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repository), "add", "app.py"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-qm", "base"], check=True,
        )
        base = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        (repository / "app.py").write_text(
            "value = 'CANARY-SECRET'\n", encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(repository), "add", "app.py"], check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-qm", "head"], check=True,
        )
        artifact = security_deep.advisory_diff_artifact(
            str(repository), base=base,
        )
        payload = json.dumps(artifact)
        self.assertNotIn("CANARY-SECRET", payload)
        self.assertNotIn(str(repository), payload)
        self.assertEqual(artifact["usage"], usage(0, 0))
        self.assertEqual(
            {row["lane"] for row in artifact["lanes"]},
            set(security_deep.LANES),
        )

    def test_run3_cache_identity_includes_normalized_findings(self):
        manifest = {
            "scope_digest": "sha256:" + "1" * 64,
            "content_digest": "sha256:" + "2" * 64,
            "revision": "a" * 40,
            "guidance_digest": "sha256:" + "3" * 64,
        }
        coverage = {
            "receipts": [{
                "lane": "dependencies",
                "provider": "fixture",
                "status": "findings",
            }],
        }
        first_finding = security_deep.security._finding(
            "fixture", "1", "fixture-rule", "first title",
            "high", "fixture.py", 1, "fixture",
        )
        second_finding = json.loads(json.dumps(first_finding))
        second_finding["title"] = "second title"
        first = security_deep._deep_from_run3(
            manifest, coverage, {"findings": [first_finding]}, self.root,
        )
        second = security_deep._deep_from_run3(
            manifest, coverage, {"findings": [second_finding]}, self.root,
        )
        self.assertEqual(first["cache_hits"], 0)
        self.assertEqual(second["cache_hits"], 0)
        self.assertNotEqual(first["cache_key"], second["cache_key"])
        self.assertEqual(second["findings"][0]["title"], "second title")

    def test_holdout_separates_safe_and_vulnerable_with_all_metrics(self):
        fixture = json.loads((Path(__file__).parents[3] / "evals" / "fixtures" / "security-quality-holdout-v1.json").read_text(encoding="utf-8"))
        candidate = security_deep.evaluate_holdout(fixture)
        self.assertEqual(candidate["metrics"], fixture["expected_metrics"])
        self.assertEqual(candidate["samples"], {
            "threat_model_coverage": 8,
            "finding_precision": 1,
            "reachability": 4,
            "refusal_fallback": 1,
            "fix_verification": 1,
            "false_clean_prevention": 2,
            "token_cost": 4,
        })
        self.assertTrue(candidate["seal"].startswith("sha256:"))
        self.assertEqual(
            candidate["fixture_digest"],
            security_deep.digest(fixture),
        )
        with mock.patch.object(
            security_deep,
            "run_deep",
            side_effect=RuntimeError("broken engine"),
        ):
            with self.assertRaisesRegex(
                security_deep.DeepSecurityError,
                "holdout_execution_failed",
            ):
                security_deep.evaluate_holdout(fixture)
        mismatched = json.loads(json.dumps(fixture))
        mismatched["cases"][1]["provider_results"]["after"] = {
            "status": "complete",
            "reported_finding": False,
            "reachability": "not_applicable",
        }
        with self.assertRaisesRegex(
            security_deep.DeepSecurityError, "holdout_expected_mismatch",
        ):
            security_deep.evaluate_holdout(mismatched)

    def test_promotion_blocks_every_quality_coverage_and_token_regression(self):
        fixture_root = Path(__file__).parents[3] / "evals"
        fixture = json.loads((fixture_root / "fixtures" / "security-quality-holdout-v1.json").read_text(encoding="utf-8"))
        baseline = json.loads((fixture_root / "baselines" / "security-quality-v1.json").read_text(encoding="utf-8"))
        candidate = security_deep.evaluate_holdout(fixture)
        self.assertEqual(
            security_deep.promotion(candidate, baseline, fixture)["verdict"],
            "PROMOTE",
        )
        for field in ("threat_model_coverage", "finding_precision", "reachability", "refusal_fallback", "fix_verification", "false_clean_prevention"):
            bad = json.loads(json.dumps(candidate)); bad["metrics"][field] = 0.0; bad = security_deep._seal(bad)
            self.assertEqual(
                security_deep.promotion(bad, baseline, fixture)["verdict"],
                "BLOCK",
            )
        expensive = json.loads(json.dumps(candidate)); expensive["metrics"]["token_cost"] = 17; expensive = security_deep._seal(expensive)
        self.assertEqual(
            security_deep.promotion(expensive, baseline, fixture)["verdict"],
            "BLOCK",
        )
        undersampled = json.loads(json.dumps(candidate))
        undersampled["samples"]["refusal_fallback"] = 0
        undersampled = security_deep._seal(undersampled)
        self.assertEqual(
            security_deep.promotion(undersampled, baseline, fixture)["verdict"],
            "BLOCK",
        )
        forged = json.loads(json.dumps(candidate))
        forged["metrics"] = {
            key: 0 if key == "token_cost" else 1.0
            for key in security_deep.METRICS
        }
        forged["expected_metrics"] = dict(forged["metrics"])
        forged["samples"] = {
            key: 1_000_000 for key in security_deep.METRICS
        }
        forged = security_deep._seal(forged)
        decision = security_deep.promotion(forged, baseline, fixture)
        self.assertEqual(decision["verdict"], "BLOCK")
        self.assertEqual(decision["reason"], "candidate_evidence_mismatch")
        self.assertEqual(
            security_deep.promotion(candidate, baseline)["reason"],
            "fixture_required",
        )
        weakened_policy = json.loads(json.dumps(baseline))
        weakened_policy["thresholds"]["threat_model_coverage"] = 0.0
        weakened_policy = security_deep._seal(weakened_policy)
        self.assertEqual(
            security_deep.promotion(
                candidate, weakened_policy, fixture,
            )["reason"],
            "policy_untrusted",
        )
        weakened_fixture = json.loads(json.dumps(fixture))
        weakened_fixture["cases"][0]["covered_threat_fields"] = ["asset"]
        weakened_fixture["expected_metrics"]["threat_model_coverage"] = 0.75
        weakened_candidate = security_deep.evaluate_holdout(weakened_fixture)
        coordinated_policy = json.loads(json.dumps(baseline))
        coordinated_policy["fixture_digest"] = security_deep.digest(
            weakened_fixture,
        )
        coordinated_policy["thresholds"]["threat_model_coverage"] = 0.75
        coordinated_policy = security_deep._seal(coordinated_policy)
        self.assertEqual(
            security_deep.promotion(
                weakened_candidate, coordinated_policy, weakened_fixture,
            )["reason"],
            "fixture_untrusted",
        )

    def test_clean_result_schema_requires_all_complete_lanes(self):
        schema = json.loads(
            (
                Path(__file__).parents[3]
                / "references"
                / "security-deep-result-v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        clean_receipts = schema["allOf"][0]["then"]["properties"]["receipts"]
        self.assertEqual(clean_receipts["minItems"], len(security_deep.LANES))
        required_lanes = {
            rule["contains"]["properties"]["lane"]["const"]
            for rule in clean_receipts["allOf"]
        }
        self.assertEqual(required_lanes, set(security_deep.LANES))
        receipt_constraints = clean_receipts["items"]["allOf"][1]["properties"]
        self.assertEqual(receipt_constraints["coverage"]["const"], "complete")
        self.assertEqual(receipt_constraints["finding_count"]["const"], 0)
        self.assertNotIn(
            "findings", receipt_constraints["status"]["enum"],
        )
        self.assertEqual(
            schema["allOf"][0]["then"]["properties"]["findings"]["maxItems"],
            0,
        )
