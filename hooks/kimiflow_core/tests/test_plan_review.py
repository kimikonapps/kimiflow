from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from kimiflow_core import plan_review


DOMAINS = (
    "intent-trace",
    "state-space",
    "identity-binding",
    "evidence-safety",
    "time-lifecycle",
    "aggregation-verdict",
    "scope-subtraction",
)
FAMILY_CHECKS = (
    "upstream-requirements",
    "sibling-states",
    "downstream-outputs",
    "boundary-values",
    "failure-classification",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PlanReviewTests(unittest.TestCase):
    def make_run(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run = Path(tmp.name) / ".kimiflow" / "demo"
        (run / "plan-review-candidates").mkdir(parents=True)
        (run / "plan-review-saturation").mkdir()
        (run / "findings").mkdir()
        (run / "review-evidence").mkdir()
        (run / "PLAN.md").write_text("# Plan\nStable behavior for AC-1.\n", encoding="utf-8")
        (run / "ACCEPTANCE.md").write_text(
            "# Acceptance\nAC-1: Stable behavior is observable.\n", encoding="utf-8"
        )
        (run / "STATE.md").write_text(
            "Flow schema: 5\nMode: feature\nScope: small\n"
            "Convergence contract: 1\nPlan review contract: 1\n"
            "Plan review profile: standard\n",
            encoding="utf-8",
        )
        (run.parent / "session").mkdir()
        (run.parent / "session" / "ACTIVE_RUN.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "active",
                    "run": ".kimiflow/demo",
                    "plan_saturation_receipts": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (run / "RECOVERY.md").write_text(
            "<!-- kimiflow:strategy gate=plan epoch-start=1 fingerprint="
            f"{digest(run / 'PLAN.md')} -->\n",
            encoding="utf-8",
        )
        return run

    def write_matrix(self, run: Path) -> None:
        matrix = {
            "schema_version": 1,
            "plan_sha256": digest(run / "PLAN.md"),
            "dimensions": [
                {"name": "measurement", "values": ["actual", "missing"]},
                {"name": "merge", "values": ["merged", "not-merged"]},
            ],
            "invariants": [
                {
                    "id": "actual-required",
                    "acceptance": "AC-1",
                    "statement": "Only actual measurements are eligible.",
                    "accept_case": "actual-merged",
                    "reject_case": "missing-merged",
                }
            ],
            "cases": [
                {
                    "id": "actual-merged",
                    "kind": "legal",
                    "values": {"measurement": "actual", "merge": "merged"},
                    "classification": "eligible",
                    "acceptance": ["AC-1"],
                    "expected": "Included in the primary sample.",
                },
                {
                    "id": "actual-not-merged",
                    "kind": "boundary",
                    "values": {"measurement": "actual", "merge": "not-merged"},
                    "classification": "eligible",
                    "acceptance": ["AC-1"],
                    "expected": "Eligible without post-merge evidence.",
                },
                {
                    "id": "missing-merged",
                    "kind": "invalid",
                    "values": {"measurement": "missing", "merge": "merged"},
                    "classification": "field-note",
                    "acceptance": ["AC-1"],
                    "expected": "Excluded without guessing.",
                },
                {
                    "id": "missing-not-merged",
                    "kind": "invalid",
                    "values": {"measurement": "missing", "merge": "not-merged"},
                    "classification": "field-note",
                    "acceptance": ["AC-1"],
                    "expected": "Excluded without guessing.",
                },
            ],
        }
        (run / "CONTRACT-MATRIX.json").write_text(
            json.dumps(matrix, sort_keys=True) + "\n", encoding="utf-8"
        )

    def candidate_file(
        self,
        run: Path,
        lens: str,
        domains: tuple[str, ...],
        *lines: str,
        round_number: int = 1,
    ) -> Path:
        path = run / "plan-review-candidates" / f"r{round_number}-{lens}.md"
        body = [f"BASIS plan_sha256={digest(run / 'PLAN.md')}"]
        body.extend(
            f"COVERAGE {domain} :: status=checked :: evidence=PLAN.md §{domain}"
            for domain in domains
        )
        body.extend(lines or ("NONE",))
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        return path

    def write_saturation(
        self,
        run: Path,
        lenses: tuple[str, ...],
        *,
        round_number: int = 1,
        dispositions=None,
        families=None,
    ) -> None:
        receipt = {
            "schema_version": 1,
            "round": round_number,
            "plan_sha256": digest(run / "PLAN.md"),
            "lenses": list(lenses),
            "candidate_files": [
                {
                    "lens": lens,
                    "sha256": digest(
                        run / "plan-review-candidates" / f"r{round_number}-{lens}.md"
                    ),
                }
                for lens in lenses
            ],
            "finding_files": [
                {
                    "lens": lens,
                    "sha256": digest(run / "findings" / f"r{round_number}-{lens}.md"),
                }
                for lens in lenses
            ],
            "dispositions": dispositions or [],
            "families": families or [],
        }
        (run / "plan-review-saturation" / f"r{round_number}.json").write_text(
            json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8"
        )
        receipt_path = run / "plan-review-saturation" / f"r{round_number}.json"
        previous = (
            "none"
            if round_number == 1
            else digest(run / "plan-review-saturation" / f"r{round_number - 1}.json")
        )
        recovery_path = run / "RECOVERY.md"
        existing = [
            line
            for line in recovery_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith(
                f"<!-- kimiflow:plan-saturation round={round_number} "
            )
        ]
        existing.append(
            "<!-- kimiflow:plan-saturation "
            f"round={round_number} receipt={digest(receipt_path)} "
            f"plan={receipt['plan_sha256']} previous={previous} -->"
        )
        recovery_path.write_text("\n".join(existing) + "\n", encoding="utf-8")
        active_path = run.parent / "session" / "ACTIVE_RUN.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        pins = [
            pin
            for pin in active.get("plan_saturation_receipts", [])
            if pin.get("round") != round_number
        ]
        pins.append(
            {
                "round": round_number,
                "receipt_sha256": digest(receipt_path),
                "plan_sha256": receipt["plan_sha256"],
                "previous_sha256": previous,
            }
        )
        active["plan_saturation_receipts"] = sorted(
            pins, key=lambda pin: pin["round"]
        )
        active_path.write_text(json.dumps(active) + "\n", encoding="utf-8")

    def test_contract_matrix_requires_pairwise_state_coverage(self) -> None:
        run = self.make_run()
        self.write_matrix(run)

        result = plan_review.validate_matrix(run)
        self.assertTrue(result.is_open, result)

        matrix_path = run / "CONTRACT-MATRIX.json"
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        matrix["cases"] = matrix["cases"][:-1]
        matrix_path.write_text(json.dumps(matrix) + "\n", encoding="utf-8")

        result = plan_review.validate_matrix(run)
        self.assertFalse(result.is_open)
        self.assertEqual("pairwise-coverage-missing", result.reason)

    def test_contract_matrix_is_bound_to_current_plan(self) -> None:
        run = self.make_run()
        self.write_matrix(run)
        (run / "PLAN.md").write_text("# Changed plan\n", encoding="utf-8")

        result = plan_review.validate_matrix(run)

        self.assertFalse(result.is_open)
        self.assertEqual("stale-plan", result.reason)

    def test_contract_matrix_rejects_unknown_acceptance_id(self) -> None:
        run = self.make_run()
        self.write_matrix(run)
        matrix_path = run / "CONTRACT-MATRIX.json"
        matrix_path.write_text(
            matrix_path.read_text(encoding="utf-8").replace("AC-1", "AC-999"),
            encoding="utf-8",
        )

        result = plan_review.validate_matrix(run)

        self.assertFalse(result.is_open)
        self.assertEqual("matrix-acceptance-unknown", result.reason)

    def test_saturation_requires_one_frozen_basis_and_all_domains(self) -> None:
        run = self.make_run()
        lenses = ("a", "b")
        self.candidate_file(run, "a", DOMAINS[:3])
        self.candidate_file(run, "b", DOMAINS[3:])
        for lens in lenses:
            (run / "findings" / f"r1-{lens}.md").write_text("NONE\n", encoding="utf-8")
        self.write_saturation(run, lenses)

        result = plan_review.validate_saturation(run, 1, lenses)
        self.assertTrue(result.is_open, result)

        candidate = run / "plan-review-candidates" / "r1-b.md"
        candidate.write_text(
            candidate.read_text(encoding="utf-8").replace(
                f"BASIS plan_sha256={digest(run / 'PLAN.md')}",
                "BASIS plan_sha256=" + "0" * 64,
            ),
            encoding="utf-8",
        )
        self.write_saturation(run, lenses)

        result = plan_review.validate_saturation(run, 1, lenses)
        self.assertFalse(result.is_open)
        self.assertEqual("stale-candidate-basis", result.reason)

    def test_saturation_rejects_bare_none_without_coverage(self) -> None:
        run = self.make_run()
        path = run / "plan-review-candidates" / "r1-a.md"
        path.write_text(
            f"BASIS plan_sha256={digest(run / 'PLAN.md')}\nNONE\n", encoding="utf-8"
        )
        (run / "findings" / "r1-a.md").write_text("NONE\n", encoding="utf-8")
        self.candidate_file(run, "b", DOMAINS)
        (run / "findings" / "r1-b.md").write_text("NONE\n", encoding="utf-8")
        self.write_saturation(run, ("a", "b"))

        result = plan_review.validate_saturation(run, 1, ("a", "b"))

        self.assertFalse(result.is_open)
        self.assertEqual("coverage-missing", result.reason)

    def test_saturation_enforces_profile_topology(self) -> None:
        run = self.make_run()
        self.candidate_file(run, "b", DOMAINS)
        (run / "findings" / "r1-b.md").write_text("NONE\n", encoding="utf-8")
        self.write_saturation(run, ("b",))

        result = plan_review.validate_saturation(run, 1, ("b",))

        self.assertFalse(result.is_open)
        self.assertEqual("review-topology-mismatch", result.reason)

    def test_nonpromoted_candidate_requires_bound_evidence_receipt(self) -> None:
        run = self.make_run()
        candidate_line = (
            "CANDIDATE HIGH PLAN.md §Security :: identity can be replayed "
            ":: family=identity-replay :: verify=command:prove-identity-binding"
        )
        self.candidate_file(run, "a", DOMAINS, candidate_line)
        self.candidate_file(run, "b", ("evidence-safety",))
        for lens in ("a", "b"):
            (run / "findings" / f"r1-{lens}.md").write_text("NONE\n", encoding="utf-8")
        candidate_id = "cand_" + hashlib.sha256(
            b"a\0" + candidate_line.encode("utf-8")
        ).hexdigest()
        evidence_path = run / "review-evidence" / "arbitrary.txt"
        evidence_path.write_text("not a structured receipt\n", encoding="utf-8")
        self.write_saturation(
            run,
            ("a", "b"),
            dispositions=[
                {
                    "candidate_id": candidate_id,
                    "outcome": "refuted",
                    "stable_class": None,
                    "evidence": f"review-evidence/arbitrary.txt@{digest(evidence_path)}",
                }
            ],
        )

        result = plan_review.validate_saturation(run, 1, ("a", "b"))

        self.assertFalse(result.is_open)
        self.assertEqual("evidence-receipt-invalid", result.reason)

    def test_later_round_rejects_consistently_rewritten_history(self) -> None:
        run = self.make_run()
        for review_round in (1, 2):
            self.candidate_file(
                run, "a", DOMAINS[:3], round_number=review_round
            )
            self.candidate_file(
                run, "b", DOMAINS[3:], round_number=review_round
            )
            for lens in ("a", "b"):
                (run / "findings" / f"r{review_round}-{lens}.md").write_text(
                    "NONE\n", encoding="utf-8"
                )
            self.write_saturation(
                run, ("a", "b"), round_number=review_round
            )
        (run / "findings" / "r1-a.md").write_text("NONE\n\n", encoding="utf-8")
        receipt_path = run / "plan-review-saturation" / "r1.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["finding_files"][0]["sha256"] = digest(
            run / "findings" / "r1-a.md"
        )
        receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        rewritten_receipt = digest(receipt_path)
        recovery_path = run / "RECOVERY.md"
        recovery = recovery_path.read_text(encoding="utf-8")
        recovery = re.sub(
            r"(kimiflow:plan-saturation round=1 receipt=)[a-f0-9]{64}",
            rf"\g<1>{rewritten_receipt}",
            recovery,
        )
        recovery = re.sub(
            r"(kimiflow:plan-saturation round=2 .+ previous=)[a-f0-9]{64}",
            rf"\g<1>{rewritten_receipt}",
            recovery,
        )
        recovery_path.write_text(recovery, encoding="utf-8")

        result = plan_review.validate_saturation(run, 2, ("a", "b"))

        self.assertFalse(result.is_open)
        self.assertEqual("plan-saturation-active-pin-mismatch", result.reason)

    def test_material_candidate_requires_disposition_and_family_sweep(self) -> None:
        run = self.make_run()
        candidate_line = (
            "CANDIDATE HIGH PLAN.md §Lifecycle :: A pending state can precede merge "
            ":: family=time-lifecycle :: verify=verifier:trace every timestamp relation"
        )
        candidate = self.candidate_file(run, "a", DOMAINS, candidate_line)
        candidate_id = "cand_" + hashlib.sha256(
            b"a\0" + candidate_line.encode("utf-8")
        ).hexdigest()
        evidence_path = run / "review-evidence" / "time.txt"
        evidence_path.write_text(
            "REVIEW_EVIDENCE class=pending-before-merge :: "
            "verify=verifier:trace every timestamp relation :: outcome=reproduced :: "
            "timestamp contradiction reproduced\n",
            encoding="utf-8",
        )
        evidence = f"review-evidence/time.txt@{digest(evidence_path)}"
        finding = (
            "FINDING HIGH PLAN.md §Lifecycle :: pending precedes merge "
            ":: class=pending-before-merge :: verify=verifier:trace every timestamp relation "
            f":: evidence={evidence}\n"
        )
        (run / "findings" / "r1-a.md").write_text(finding, encoding="utf-8")
        self.candidate_file(run, "b", ("evidence-safety",))
        (run / "findings" / "r1-b.md").write_text("NONE\n", encoding="utf-8")
        dispositions = [
            {
                "candidate_id": candidate_id,
                "outcome": "promoted",
                "stable_class": "pending-before-merge",
                "evidence": evidence,
            }
        ]
        family = {
            "id": "time-lifecycle",
            "classes": ["pending-before-merge"],
            "root_cause": "Timestamp relations were specified independently instead of as one lifecycle.",
            "checks": {
                check: {
                    "status": "checked",
                    "evidence": f"PLAN.md §Lifecycle {check}",
                }
                for check in FAMILY_CHECKS
            },
        }
        self.write_saturation(run, ("a", "b"), dispositions=dispositions, families=[family])

        result = plan_review.validate_saturation(run, 1, ("a", "b"))
        self.assertTrue(result.is_open, result)

        receipt_path = run / "plan-review-saturation" / "r1.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        del receipt["families"][0]["checks"]["sibling-states"]
        receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
        recovery_path = run / "RECOVERY.md"
        recovery = recovery_path.read_text(encoding="utf-8")
        recovery = re.sub(
            r"(kimiflow:plan-saturation round=1 receipt=)[a-f0-9]{64}",
            rf"\g<1>{digest(receipt_path)}",
            recovery,
        )
        recovery_path.write_text(recovery, encoding="utf-8")
        active_path = run.parent / "session" / "ACTIVE_RUN.json"
        active = json.loads(active_path.read_text(encoding="utf-8"))
        active["plan_saturation_receipts"][0]["receipt_sha256"] = digest(
            receipt_path
        )
        active_path.write_text(json.dumps(active) + "\n", encoding="utf-8")

        result = plan_review.validate_saturation(run, 1, ("a", "b"))
        self.assertFalse(result.is_open)
        self.assertEqual("family-checks-incomplete", result.reason)

    def test_round_three_is_resolution_only(self) -> None:
        run = self.make_run()
        for prior_round in (1, 2):
            self.candidate_file(
                run, "a", DOMAINS[:3], round_number=prior_round
            )
            self.candidate_file(
                run, "b", DOMAINS[3:], round_number=prior_round
            )
            for lens in ("a", "b"):
                (run / "findings" / f"r{prior_round}-{lens}.md").write_text(
                    "NONE\n", encoding="utf-8"
                )
            self.write_saturation(
                run, ("a", "b"), round_number=prior_round
            )
        candidate_line = (
            "CANDIDATE HIGH PLAN.md §Late :: newly discovered "
            ":: family=late-family :: verify=verifier:reproduce late issue"
        )
        path = run / "plan-review-candidates" / "r3-a.md"
        path.write_text(
            f"BASIS plan_sha256={digest(run / 'PLAN.md')}\n"
            + "\n".join(
                f"COVERAGE {domain} :: status=checked :: evidence=PLAN.md §{domain}"
                for domain in DOMAINS
            )
            + f"\n{candidate_line}\n",
            encoding="utf-8",
        )
        (run / "findings" / "r3-a.md").write_text("NONE\n", encoding="utf-8")
        other = run / "plan-review-candidates" / "r3-b.md"
        other.write_text(
            f"BASIS plan_sha256={digest(run / 'PLAN.md')}\n"
            "COVERAGE evidence-safety :: status=checked :: evidence=PLAN.md §evidence\n"
            "NONE\n",
            encoding="utf-8",
        )
        (run / "findings" / "r3-b.md").write_text("NONE\n", encoding="utf-8")
        self.write_saturation(run, ("a", "b"), round_number=3)

        result = plan_review.validate_saturation(run, 3, ("a", "b"))

        self.assertFalse(result.is_open)
        self.assertEqual("closeout-new-candidate", result.reason)


if __name__ == "__main__":
    unittest.main()
