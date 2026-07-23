import contextlib
import errno
import io
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from unittest import mock

from memory_router import attribution, lifecycle, memory_md, provider, rows, store, summaries
from memory_router.__main__ import main


class LifecycleCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(
            tempfile.mkdtemp(prefix="kimiflow-lifecycle-")
        )
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        self.learnings = os.path.join(self.project, "LEARNINGS.jsonl")
        self.usage = os.path.join(self.project, "MEMORY-USAGE.json")
        self.outcomes = os.path.join(self.project, "STRATEGY-OUTCOMES.jsonl")
        evidence_path = os.path.join(self.root, "src", "evidence.txt")
        os.makedirs(os.path.dirname(evidence_path))
        with open(evidence_path, "w", encoding="utf-8") as handle:
            handle.write("current evidence\n")
        self.evidence_ref = "src/evidence.txt"
        self.fingerprints = rows.evidence_fingerprints_json(self.root, [self.evidence_ref])

    def row(self, rid, verified, **overrides):
        value = {
            "id": rid,
            "kind": "learned",
            "topic": "memory",
            "summary": "Bounded local learning.",
            "status": "current",
            "confidence": "medium",
            "sensitivity": "normal",
            "last_verified": verified,
            "evidence": [self.evidence_ref],
            "evidence_fingerprints": self.fingerprints,
        }
        value.update(overrides)
        return value

    def write_rows(self, values, newline="\n"):
        with open(self.learnings, "w", encoding="utf-8", newline="") as handle:
            handle.write(newline.join(json.dumps(row, ensure_ascii=False) for row in values) + newline)

    def write_usage(self, items):
        with open(self.usage, "w", encoding="utf-8") as handle:
            json.dump({"items": items, "events": []}, handle)

    def write_outcomes(self, values):
        with open(self.outcomes, "w", encoding="utf-8") as handle:
            for value in values:
                handle.write(json.dumps(value) + "\n")

    def outcome(self, marker, when, learning_id, classification):
        outcome_classification = (
            "verified_success" if classification == "helpful" else "verified_failure"
        )
        terminal = "done" if classification == "helpful" else "failed"
        run = ".kimiflow/test-" + marker
        run_dir = os.path.join(self.root, run)
        os.makedirs(run_dir, exist_ok=True)
        current_rows = [
            row for row in store.read_jsonl(self.learnings)
            if isinstance(row, dict) and row.get("id") == learning_id
        ]
        hit = dict(current_rows[0]) if len(current_rows) == 1 else {
            "id": learning_id,
            "summary": "Bounded local learning.",
            "evidence": [self.evidence_ref],
        }
        recall_id = attribution.recall_id(
            "learnings", attribution.hit_reference(hit), hit,
        )
        hit["recall_id"] = recall_id
        recall_value = {
            "schema_version": 2,
            "attribution": {"contract": 1},
            "sources": {"learnings": {"count": 1, "hits": [hit]}},
        }
        with open(os.path.join(run_dir, "RECALL.json"), "w", encoding="utf-8") as handle:
            json.dump(recall_value, handle, sort_keys=True)
            handle.write("\n")
        with open(os.path.join(run_dir, "PLAN.md"), "w", encoding="utf-8") as handle:
            handle.write("Decision D1: use the learning\n")
        with open(os.path.join(run_dir, "VERIFICATION.md"), "w", encoding="utf-8") as handle:
            handle.write("Decision check D1: %s :: fixture\n" % (
                "passed" if classification == "helpful" else "failed"
            ))
        verification = (
            {"outcome": "passed", "criteria": "passed", "regression": "passed"}
            if classification == "helpful"
            else {"outcome": "failed", "criteria": "failed", "regression": "not_run"}
        )
        artifact_refs = [
            run + "/PLAN.md",
            run + "/RECALL.json",
            run + "/VERIFICATION.md",
        ]
        artifact_fingerprints = rows.evidence_fingerprints_json(
            self.root, artifact_refs,
        )

        return {
            "schema_version": 1,
            "id": "out_" + marker * 64,
            "run": run,
            "evaluated_at": when,
            "terminal": terminal,
            "classification": outcome_classification,
            "promotable": True,
            "strategy": {"summary": "Verify memory outcome " + marker, "evidence_id": None},
            "source_head": marker * 40,
            "affected_paths": [self.evidence_ref],
            "signals": {
                "phase6": "done",
                "recovery": "clean",
                "items_open": 0,
                "verification": verification,
                "code_review": "clean",
                "learning_review": "open",
                "recall_attribution": classification,
            },
            "recall_attribution": {
                "contract": 1,
                "status": "complete" if classification == "helpful" else "inconclusive",
                "classification": classification,
                "applied_ids": [recall_id],
                "items": [{
                    "recall_id": recall_id,
                    "source": "learnings",
                    "learning_id": learning_id,
                    "classification": classification,
                    "decision_checks": {
                        "D1": "passed" if classification == "helpful" else "failed",
                    },
                    "evidence": [],
                }],
                "contradiction_evidence": [],
                "contradiction_fingerprints": [],
                "artifact_fingerprints": [
                    dict(fingerprint) for fingerprint in artifact_fingerprints
                ],
                "terminal": terminal,
            },
            "evidence": artifact_refs,
            "evidence_fingerprints": [
                dict(fingerprint) for fingerprint in artifact_fingerprints
            ],
        }

    def run_lifecycle(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch("memory_router.lifecycle._refresh_derivatives"):
            code = lifecycle.run(["--root", self.root] + list(args))
        return code, json.loads(output.getvalue()) if output.getvalue() else None

    def test_preview_scores_and_strict_eligibility(self):
        self.write_rows([
            self.row("hot", "2999-01-01", confidence="high"),
            self.row("eligible", "2000-01-01", confidence="low"),
            self.row("used-stale", "2000-01-01"),
            self.row("fresh-unused", "2999-01-01"),
        ])
        self.write_usage({
            "learning:hot": {"kind": "learning", "use_count": 3},
            "learning:used-stale": {"kind": "learning", "use_count": 1},
        })
        code, result = self.run_lifecycle()
        self.assertEqual(code, 0)
        self.assertEqual(result["candidate_ids"], ["eligible"])
        scored = {item["id"]: item for item in result["rows"]}
        self.assertGreater(scored["hot"]["utility_points"], scored["eligible"]["utility_points"])
        self.assertFalse(scored["fresh-unused"]["quarantine_eligible"])
        self.assertFalse(scored["used-stale"]["quarantine_eligible"])
        self.assertLessEqual(len(result["rows"]), 20)
        self.assertEqual(result["utility_max_points"], 5)

    def test_verified_outcomes_promote_demote_and_repeat_idempotently(self):
        self.write_rows([
            self.row("candidate", "2999-01-01", maturity="probationary"),
            self.row("sensitive", "2999-01-01", maturity="probationary",
                     sensitivity="security"),
            self.row("invalid", "2999-01-01", maturity="unexpected"),
        ])
        self.write_usage({})
        first = self.outcome("a", "2026-07-20T00:00:00Z", "candidate", "helpful")
        second = self.outcome("b", "2026-07-21T00:00:00Z", "candidate", "helpful")
        sensitive_a = self.outcome("c", "2026-07-20T00:00:00Z", "sensitive", "helpful")
        sensitive_b = self.outcome("d", "2026-07-21T00:00:00Z", "sensitive", "helpful")
        self.write_outcomes([first, second, sensitive_a, sensitive_b])

        code, preview = self.run_lifecycle()
        self.assertEqual(code, 0)
        self.assertEqual(preview["promotion_candidate_ids"], ["candidate"])
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["promoted_ids"], ["candidate"])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(
            result["reason_counts"],
            {"verified_helpful_streak": 1},
        )
        stored = {row["id"]: row for row in store.read_jsonl(self.learnings)}
        self.assertEqual(stored["candidate"]["maturity"], "durable")
        self.assertEqual(stored["candidate"]["curation"]["helpful_streak"], 2)
        self.assertEqual(stored["sensitive"]["maturity"], "probationary")
        self.assertNotIn("curation", stored["sensitive"])
        self.assertIn("sensitive", result["protected_ids"])
        self.assertIn("invalid", result["protected_ids"])

        with open(self.learnings, "rb") as handle:
            before = handle.read()
        code, repeated = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertFalse(repeated["written"])
        with open(self.learnings, "rb") as handle:
            self.assertEqual(handle.read(), before)

        contradiction = self.outcome(
            "e", "2026-07-22T00:00:00Z", "candidate", "contradicted"
        )
        self.write_outcomes([first, second, sensitive_a, sensitive_b, contradiction])
        code, demoted = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(demoted["demoted_ids"], ["candidate"])
        stored = {row["id"]: row for row in store.read_jsonl(self.learnings)}
        self.assertEqual(stored["candidate"]["maturity"], "probationary")
        self.assertEqual(stored["candidate"]["curation"]["reason"], "verified_contradiction")

    def test_verified_use_never_promotes_rewritten_learning_content(self):
        original = self.row(
            "candidate", "2999-01-01", maturity="probationary"
        )
        self.write_rows([original])
        self.write_usage({})
        first = self.outcome(
            "a", "2026-07-20T00:00:00Z", "candidate", "helpful"
        )
        second = self.outcome(
            "b", "2026-07-21T00:00:00Z", "candidate", "helpful"
        )
        rewritten = dict(original, summary="A different, never-used learning.")
        self.write_rows([rewritten])
        self.write_outcomes([first, second])

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 0)
        self.assertEqual(result["promoted_ids"], [])
        self.assertEqual(result["reason_counts"], {"content_drift": 1})
        stored = store.read_jsonl(self.learnings)[0]
        self.assertEqual(stored["maturity"], "probationary")
        self.assertEqual(stored["curation"]["reason"], "content_drift")
        self.assertNotEqual(
            stored["curation"]["learning_fingerprint"],
            rows.learning_content_fingerprint(stored),
        )

    def test_missing_later_contradiction_artifact_blocks_older_success_promotion(self):
        self.write_rows([
            self.row("candidate", "2999-01-01", maturity="probationary"),
        ])
        self.write_usage({})
        first = self.outcome(
            "a", "2026-07-20T00:00:00Z", "candidate", "helpful"
        )
        second = self.outcome(
            "b", "2026-07-21T00:00:00Z", "candidate", "helpful"
        )
        contradiction = self.outcome(
            "c", "2026-07-22T00:00:00Z", "candidate", "contradicted"
        )
        self.write_outcomes([first, second, contradiction])
        os.remove(os.path.join(self.root, contradiction["run"], "RECALL.json"))

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertEqual(
            store.read_jsonl(self.learnings)[0]["maturity"],
            "probationary",
        )

    def test_evidence_drift_demotes_legacy_durable_and_bad_ledger_never_mutates(self):
        self.write_rows([self.row("legacy", "2999-01-01")])
        self.write_usage({})
        with open(os.path.join(self.root, self.evidence_ref), "a", encoding="utf-8") as handle:
            handle.write("drift\n")
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["demoted_ids"], ["legacy"])
        self.assertEqual(store.read_jsonl(self.learnings)[0]["maturity"], "probationary")

        self.write_rows([self.row("safe", "2999-01-01", maturity="probationary")])
        with open(self.learnings, "rb") as handle:
            before = handle.read()
        with open(self.outcomes, "w", encoding="utf-8") as handle:
            handle.write('{"id":"out_' + "a" * 64 + '","id":"out_' + "b" * 64 + '"}\n')
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertIsNone(result)
        with open(self.learnings, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_legacy_attribution_without_learning_id_uses_sealed_identity(self):
        self.write_rows([self.row("legacy", "2999-01-01")])
        self.write_usage({})
        outcome = self.outcome(
            "a", "2026-07-20T00:00:00Z", "legacy", "helpful"
        )
        outcome["recall_attribution"]["items"][0].pop("learning_id")
        self.write_outcomes([outcome])
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["outcome_signal_count"], 1)
        stored = store.read_jsonl(self.learnings)[0]
        self.assertEqual(stored["maturity"] if "maturity" in stored else "durable",
                         "durable")
        self.assertEqual(stored["curation"]["helpful_count"], 1)

    def test_missing_learning_id_cannot_hide_later_contradiction(self):
        self.write_rows([
            self.row("candidate", "2999-01-01", maturity="probationary"),
        ])
        self.write_usage({})
        first = self.outcome(
            "a", "2026-07-20T00:00:00Z", "candidate", "helpful"
        )
        second = self.outcome(
            "b", "2026-07-21T00:00:00Z", "candidate", "helpful"
        )
        contradiction = self.outcome(
            "c", "2026-07-22T00:00:00Z", "candidate", "contradicted"
        )
        contradiction["recall_attribution"]["items"][0].pop("learning_id")
        self.write_outcomes([first, second, contradiction])

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 0)
        self.assertEqual(result["promoted_ids"], [])
        stored = store.read_jsonl(self.learnings)[0]
        self.assertEqual(stored["maturity"], "probationary")
        self.assertEqual(stored["curation"]["reason"], "verified_contradiction")

    def test_failed_security_scan_is_protected_from_automatic_promotion(self):
        candidate = self.row(
            "candidate",
            "2999-01-01",
            maturity="probationary",
            security_scan={"ok": False, "reasons": ["instruction_override"]},
        )
        self.write_rows([candidate])
        self.write_usage({})
        self.write_outcomes([
            self.outcome("a", "2026-07-20T00:00:00Z", "candidate", "helpful"),
            self.outcome("b", "2026-07-21T00:00:00Z", "candidate", "helpful"),
        ])

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 0)
        self.assertEqual(result["promoted_ids"], [])
        self.assertIn("candidate", result["protected_ids"])
        stored = store.read_jsonl(self.learnings)[0]
        self.assertEqual(stored["maturity"], "probationary")
        self.assertNotIn("curation", stored)

    def test_malformed_success_security_scan_is_protected(self):
        candidate = self.row(
            "candidate",
            "2999-01-01",
            maturity="probationary",
            security_scan={"ok": True, "reasons": ["instruction_override"]},
        )
        self.write_rows([candidate])
        self.write_usage({})
        self.write_outcomes([
            self.outcome("a", "2026-07-20T00:00:00Z", "candidate", "helpful"),
            self.outcome("b", "2026-07-21T00:00:00Z", "candidate", "helpful"),
        ])

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 0)
        self.assertEqual(result["promoted_ids"], [])
        self.assertIn("candidate", result["protected_ids"])
        self.assertEqual(store.read_jsonl(self.learnings)[0]["maturity"],
                         "probationary")

    def test_unknown_content_field_drift_invalidates_verified_use(self):
        self.write_rows([
            self.row("candidate", "2999-01-01", maturity="probationary"),
        ])
        self.write_usage({})
        outcomes = [
            self.outcome("a", "2026-07-20T00:00:00Z", "candidate", "helpful"),
            self.outcome("b", "2026-07-21T00:00:00Z", "candidate", "helpful"),
        ]
        changed = store.read_jsonl(self.learnings)[0]
        changed["future_recall_instruction"] = "new meaning after verification"
        self.write_rows([changed])
        self.write_outcomes(outcomes)

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 0)
        self.assertEqual(result["promoted_ids"], [])
        stored = store.read_jsonl(self.learnings)[0]
        self.assertEqual(stored["maturity"], "probationary")
        self.assertEqual(stored["curation"]["reason"], "content_drift")

    def test_one_run_cannot_be_cloned_into_two_helpful_proofs(self):
        self.write_rows([
            self.row("candidate", "2999-01-01", maturity="probationary"),
        ])
        self.write_usage({})
        original = self.outcome(
            "a", "2026-07-20T00:00:00Z", "candidate", "helpful"
        )
        clone = json.loads(json.dumps(original))
        clone["id"] = "out_" + "f" * 64
        self.write_outcomes([original, clone])

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertEqual(store.read_jsonl(self.learnings)[0]["maturity"],
                         "probationary")

    def test_ledger_order_not_timestamp_text_controls_contradiction_causality(self):
        self.write_rows([
            self.row("candidate", "2999-01-01", maturity="probationary"),
        ])
        self.write_usage({})
        first = self.outcome(
            "a", "2026-07-20T00:00:00Z", "candidate", "helpful"
        )
        second = self.outcome(
            "b", "2026-07-21T00:00:00Z", "candidate", "helpful"
        )
        contradiction = self.outcome(
            "c", "2026-07-22T00:00:00Z", "candidate", "contradicted"
        )
        first["evaluated_at"] = "2099-07-23T00:00:00Z"
        second["evaluated_at"] = "2099-07-24T00:00:00Z"
        self.write_outcomes([first, second, contradiction])

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 0)
        self.assertEqual(result["promoted_ids"], [])
        stored = store.read_jsonl(self.learnings)[0]
        self.assertEqual(stored["maturity"], "probationary")
        self.assertEqual(stored["curation"]["reason"], "verified_contradiction")

    def test_malformed_or_inconsistent_outcomes_never_create_trust(self):
        self.write_rows([self.row("candidate", "2999-01-01", maturity="probationary")])
        self.write_usage({})
        valid = self.outcome(
            "a", "2026-07-20T00:00:00Z", "candidate", "helpful"
        )
        mutations = (
            ("missing schema", lambda row: row.pop("schema_version")),
            ("wrong terminal", lambda row: row.update(terminal="failed")),
            ("invalid timestamp", lambda row: row.update(
                evaluated_at="2026-02-30T00:00:00Z"
            )),
            ("missing verification", lambda row: row["signals"].pop("verification")),
            ("unlinked recall", lambda row: row["recall_attribution"].update(applied_ids=[])),
            ("inconsistent attribution", lambda row: row["signals"].update(
                recall_attribution="neutral"
            )),
            ("empty helpful decision", lambda row: row["recall_attribution"][
                "items"
            ][0].update(decision_checks={})),
            ("incomplete outcome seal", lambda row: row[
                "evidence_fingerprints"
            ][0].pop("sha256")),
            ("missing attribution seals", lambda row: row[
                "recall_attribution"
            ].update(artifact_fingerprints=[])),
            ("helpful status not complete", lambda row: row[
                "recall_attribution"
            ].update(status="inconclusive")),
            ("mismatched attribution seal", lambda row: row[
                "recall_attribution"
            ]["artifact_fingerprints"][0].update(
                sha256="f" * 64,
                digest="f" * 64,
            )),
            ("detached learning id", lambda row: row[
                "recall_attribution"
            ]["items"][0].update(learning_id="substituted")),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                row = json.loads(json.dumps(valid))
                mutate(row)
                self.write_outcomes([row])
                code, result = self.run_lifecycle("--write")
                self.assertEqual(code, 1)
                self.assertIsNone(result)
                self.assertEqual(
                    store.read_jsonl(self.learnings)[0]["maturity"],
                    "probationary",
                )

        helpful_failure = json.loads(json.dumps(valid))
        helpful_failure.update(
            terminal="failed",
            classification="verified_failure",
        )
        helpful_failure["signals"]["verification"] = {
            "outcome": "failed",
            "criteria": "failed",
            "regression": "not_run",
        }
        helpful_failure["recall_attribution"].update(
            terminal="failed",
            status="inconclusive",
        )
        self.write_outcomes([helpful_failure])
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertEqual(
            store.read_jsonl(self.learnings)[0]["maturity"],
            "probationary",
        )

    def test_sha256_git_source_head_remains_compatible(self):
        self.write_rows([self.row(
            "candidate", "2999-01-01", maturity="probationary"
        )])
        self.write_usage({})
        first = self.outcome(
            "a", "2026-07-20T00:00:00Z", "candidate", "helpful"
        )
        second = self.outcome(
            "b", "2026-07-21T00:00:00Z", "candidate", "helpful"
        )
        first["source_head"] = "1" * 64
        second["source_head"] = "2" * 64
        self.write_outcomes([first, second])

        code, result = self.run_lifecycle("--write")

        self.assertEqual(code, 0)
        self.assertEqual(result["promoted_ids"], ["candidate"])

    def test_duplicate_identical_contradiction_seals_remain_valid(self):
        outcome = self.outcome(
            "a", "2026-07-20T00:00:00Z", "candidate", "contradicted"
        )
        reference = self.evidence_ref + ":1"
        seal = {
            "ref": reference,
            "path": self.evidence_ref,
            "sha256": "f" * 64,
            "digest": "f" * 64,
            "digest_algorithm": "sha256",
            "status": "current",
        }
        second_item = json.loads(json.dumps(
            outcome["recall_attribution"]["items"][0]
        ))
        recall_path = os.path.join(self.root, outcome["run"], "RECALL.json")
        with open(recall_path, "r", encoding="utf-8") as handle:
            recall_value = json.load(handle)
        second_hit = {
            "id": "other",
            "summary": "Another bounded local learning.",
            "evidence": [self.evidence_ref],
        }
        second_id = attribution.recall_id(
            "learnings", attribution.hit_reference(second_hit), second_hit,
        )
        second_hit["recall_id"] = second_id
        recall_value["sources"]["learnings"]["hits"].append(second_hit)
        recall_value["sources"]["learnings"]["count"] = 2
        with open(recall_path, "w", encoding="utf-8") as handle:
            json.dump(recall_value, handle, sort_keys=True)
            handle.write("\n")
        recall_ref = outcome["run"] + "/RECALL.json"
        recall_seal = rows.evidence_fingerprints_json(
            self.root, [recall_ref],
        )[0]
        for fingerprints in (
            outcome["recall_attribution"]["artifact_fingerprints"],
            outcome["evidence_fingerprints"],
        ):
            for index, fingerprint in enumerate(fingerprints):
                if fingerprint["ref"] == recall_ref:
                    fingerprints[index] = recall_seal
        second_item.update(
            recall_id=second_id,
            learning_id="other",
            evidence=[reference],
        )
        outcome["recall_attribution"]["items"][0]["evidence"] = [reference]
        outcome["recall_attribution"]["items"].append(second_item)
        outcome["recall_attribution"]["applied_ids"].append(
            second_item["recall_id"]
        )
        outcome["recall_attribution"]["contradiction_evidence"] = [
            reference,
            reference,
        ]
        outcome["recall_attribution"]["contradiction_fingerprints"] = [
            seal,
            dict(seal),
        ]
        outcome["evidence"].extend([reference, reference])
        outcome["evidence_fingerprints"].extend([seal, dict(seal)])
        encoded = (json.dumps(outcome) + "\n").encode("utf-8")
        signals = lifecycle._outcome_signals(
            self.root, ((1, 1), encoded, 0o600, ())
        )
        self.assertEqual(
            signals["candidate"]["last_classification"],
            "contradicted",
        )
        self.assertEqual(
            signals["other"]["last_classification"],
            "contradicted",
        )

    def test_outcome_ledger_bounds_and_duplicate_ids_fail_closed(self):
        row = self.outcome("a", "2026-07-20T00:00:00Z", "candidate", "helpful")
        snapshot = ((1, 1), b"x" * (lifecycle._MAX_OUTCOME_BYTES + 1), 0o600, ())
        with self.assertRaisesRegex(ValueError, "size limit"):
            lifecycle._outcome_signals(self.root, snapshot)
        encoded = (json.dumps(row) + "\n").encode("utf-8")
        snapshot = (
            (1, 1),
            b"{}\n" * (lifecycle._MAX_OUTCOME_ROWS + 1),
            0o600,
            (),
        )
        with self.assertRaisesRegex(ValueError, "row limit"):
            lifecycle._outcome_signals(self.root, snapshot)
        snapshot = ((1, 1), encoded + encoded, 0o600, ())
        with self.assertRaisesRegex(ValueError, "duplicate id"):
            lifecycle._outcome_signals(self.root, snapshot)

    def test_concurrent_outcome_append_refuses_trust_change_without_data_loss(self):
        self.write_rows([self.row(
            "candidate", "2999-01-01", maturity="probationary"
        )])
        self.write_usage({})
        first = self.outcome("a", "2026-07-20T00:00:00Z", "candidate", "helpful")
        second = self.outcome("b", "2026-07-21T00:00:00Z", "candidate", "helpful")
        concurrent = self.outcome(
            "c", "2026-07-22T00:00:00Z", "candidate", "contradicted"
        )
        self.write_outcomes([first, second])
        real_signals = lifecycle._outcome_signals

        def append_after_evaluation(root, snapshot):
            signals = real_signals(root, snapshot)
            with open(self.outcomes, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(concurrent) + "\n")
            return signals

        with mock.patch.object(
            lifecycle, "_outcome_signals", side_effect=append_after_evaluation
        ):
            code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertEqual(
            store.read_jsonl(self.learnings)[0]["maturity"],
            "probationary",
        )
        self.assertEqual(len(store.read_jsonl(self.outcomes)), 3)

    def test_non_project_rows_are_never_curated_or_quarantined(self):
        self.write_rows([
            self.row(
                "shared", "2000-01-01", scope="global", maturity="durable"
            ),
            self.row(
                "sensitive", "2000-01-01",
                sensitivity="security",
                maturity="durable",
            ),
        ])
        self.write_usage({})
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["quarantined_count"], 0)
        self.assertEqual(result["protected_ids"], ["shared", "sensitive"])
        for stored in store.read_jsonl(self.learnings):
            self.assertEqual(stored["maturity"], "durable")
            self.assertEqual(stored["status"], "current")
            self.assertNotIn("curation", stored)

    def test_same_second_events_follow_serialized_ledger_order(self):
        self.write_rows([self.row(
            "candidate", "2999-01-01", maturity="durable"
        )])
        self.write_usage({})
        prior_helpful = self.outcome(
            "c", "2026-07-20T00:00:00Z", "candidate", "helpful"
        )
        contradiction = self.outcome(
            "a", "2026-07-21T00:00:00Z", "candidate", "contradicted"
        )
        same_second_helpful = self.outcome(
            "b", "2026-07-21T00:00:00Z", "candidate", "helpful"
        )
        self.write_outcomes([
            prior_helpful,
            contradiction,
            same_second_helpful,
        ])
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["demoted_ids"], [])
        stored = store.read_jsonl(self.learnings)[0]
        self.assertEqual(stored["maturity"], "durable")
        self.assertEqual(
            stored["curation"]["last_classification"],
            "helpful",
        )

    def test_write_quarantines_without_deletion_and_restore_is_evidence_gated(self):
        eligible = self.row("eligible", "2000-01-01", summary="left\u2028right")
        duplicate_a = self.row("duplicate", "2000-01-01", summary="first")
        duplicate_b = self.row("duplicate", "2000-01-01", summary="second")
        missing = self.row("unused", "2000-01-01")
        missing.pop("id")
        encoded = [json.dumps(row, ensure_ascii=False) for row in
                   (eligible, duplicate_a, duplicate_b, missing)]
        original = (encoded[0] + "\r\n" + encoded[1] + "\n" + encoded[2] + "\n" +
                    encoded[3] + "\nBROKEN\r\n[1,2]\n\nunterminated")
        with open(self.learnings, "w", encoding="utf-8", newline="") as handle:
            handle.write(original)
        self.write_usage({})
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["quarantined_ids"], ["eligible"])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(
            result["reason_counts"],
            {"stale_unused_quarantine": 1},
        )
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            after = handle.read()
        self.assertIn("left\u2028right", after)
        self.assertIn("}\r\n" + encoded[1], after)
        self.assertTrue(after.endswith("\n\nunterminated"))
        self.assertIn("\nBROKEN\r\n[1,2]\n", after)
        parsed = [row for row in store.read_jsonl(self.learnings) if isinstance(row, dict)]
        self.assertEqual(len(parsed), 4)
        statuses = [(row.get("id"), row.get("status")) for row in parsed]
        self.assertIn(("eligible", "quarantined"), statuses)
        quarantined = next(row for row in parsed if row.get("id") == "eligible")
        self.assertEqual(
            quarantined["curation"]["reason"],
            "stale_unused_quarantine",
        )
        self.assertEqual(statuses.count(("duplicate", "current")), 2)
        self.assertIn((None, "current"), statuses)
        code, restored = self.run_lifecycle("--restore", "eligible", "--write")
        self.assertEqual(code, 0)
        self.assertEqual(restored["restored_id"], "eligible")
        self.assertEqual(next(row for row in store.read_jsonl(self.learnings)
                              if row.get("id") == "eligible")["status"], "current")
        code, duplicate = self.run_lifecycle("--restore", "duplicate", "--write")
        self.assertEqual(code, 1)
        self.assertEqual(duplicate["reason"], "not_quarantined")
        code, _ = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        with open(os.path.join(self.root, self.evidence_ref), "a", encoding="utf-8") as handle:
            handle.write("drift\n")
        code, drifted = self.run_lifecycle("--restore", "eligible", "--write")
        self.assertEqual(code, 1)
        self.assertEqual(drifted["reason"], "evidence_drift")

    def test_write_processes_all_eligible_while_preview_is_bounded(self):
        self.write_rows([self.row("eligible-%02d" % index, "2000-01-01")
                         for index in range(25)])
        self.write_usage({})
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["candidate_count"], 25)
        self.assertEqual(len(result["candidate_ids"]), 20)
        self.assertEqual(result["candidate_ids_omitted"], 5)
        self.assertEqual(result["quarantined_count"], 25)
        self.assertEqual(len(result["quarantined_ids"]), 20)
        self.assertEqual(result["quarantined_ids_omitted"], 5)

    def test_restore_rejects_unverified_or_missing_evidence_even_when_unchanged(self):
        missing_ref = "src/missing.txt"
        self.write_rows([
            self.row("unverified", "2000-01-01", status="quarantined",
                     evidence=["NOT VERIFIED"],
                     evidence_fingerprints=rows.evidence_fingerprints_json(
                         self.root, ["NOT VERIFIED"])),
            self.row("missing", "2000-01-01", status="quarantined",
                     evidence=[missing_ref],
                     evidence_fingerprints=rows.evidence_fingerprints_json(
                         self.root, [missing_ref])),
        ])
        for rid in ("unverified", "missing"):
            code, result = self.run_lifecycle("--restore", rid, "--write")
            self.assertEqual(code, 1)
            self.assertEqual(result["reason"], "evidence_drift")

    def test_duplicate_object_keys_are_preserved_and_never_mutated(self):
        duplicate = (json.dumps(self.row("first", "2000-01-01"))[:-1]
                     + ',"id":"second","nested":{"keep":1,"keep":2}}')
        with open(self.learnings, "w", encoding="utf-8", newline="") as handle:
            handle.write(duplicate + "\r\n")
        self.write_usage({})
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["quarantined_count"], 0)
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            self.assertEqual(handle.read(), duplicate + "\r\n")

    def test_ambiguous_or_corrupt_usage_never_proves_unused(self):
        for raw_usage in (
            '{"items":{"learning:used":{"use_count":1}},"items":{}}',
            '{"items":{"learning:used":{"use_count":"corrupt"}}}',
        ):
            with self.subTest(raw_usage=raw_usage):
                self.write_rows([self.row("used", "2000-01-01")])
                with open(self.usage, "w", encoding="utf-8") as handle:
                    handle.write(raw_usage)
                code, result = self.run_lifecycle("--write")
                self.assertEqual(code, 0)
                self.assertEqual(result["candidate_count"], 0)
                self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")

    def test_missing_or_invalid_last_verified_never_proves_stale(self):
        missing = self.row("missing", "2000-01-01")
        missing.pop("last_verified")
        for row in (missing, self.row("empty", ""), self.row("invalid", "2026-99-99")):
            with self.subTest(row=row):
                self.write_rows([row])
                self.write_usage({})
                code, result = self.run_lifecycle("--write")
                self.assertEqual(code, 0)
                self.assertEqual(result["candidate_count"], 0)
                self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")

    def test_concurrent_append_refuses_snapshot_rewrite_without_data_loss(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        concurrent = json.dumps(self.row("concurrent", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        injected = [False]

        def append_before_exchange(source, target):
            if target == self.learnings and not injected[0]:
                injected[0] = True
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write(concurrent)
            return real_exchange(source, target)

        with mock.patch.object(store, "_exchange_paths", side_effect=append_before_exchange):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        parsed = store.read_jsonl(self.learnings)
        self.assertEqual([row["id"] for row in parsed], ["eligible", "concurrent"])
        self.assertEqual(parsed[0]["status"], "current")

    def test_concurrent_append_refuses_restore_without_data_loss(self):
        self.write_rows([self.row("target", "2000-01-01", status="quarantined")])
        self.write_usage({})
        concurrent = json.dumps(self.row("concurrent", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        injected = [False]

        def append_before_exchange(source, target):
            if target == self.learnings and not injected[0]:
                injected[0] = True
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write(concurrent)
            return real_exchange(source, target)

        with mock.patch.object(store, "_exchange_paths", side_effect=append_before_exchange):
            code, _result = self.run_lifecycle("--restore", "target", "--write")
        self.assertEqual(code, 1)
        parsed = store.read_jsonl(self.learnings)
        self.assertEqual([row["id"] for row in parsed], ["target", "concurrent"])
        self.assertEqual(parsed[0]["status"], "quarantined")

    def test_concurrent_append_survives_refresh_rollback(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        concurrent = json.dumps(self.row("concurrent", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        exchange_count = [0]

        def append_during_rollback(source, target):
            if target == self.learnings:
                exchange_count[0] += 1
                if exchange_count[0] == 2:
                    with open(target, "a", encoding="utf-8") as handle:
                        handle.write(concurrent)
            return real_exchange(source, target)

        with mock.patch.object(store, "_exchange_paths", side_effect=append_during_rollback), \
                mock.patch.object(memory_md, "write_bounded_memory",
                                  side_effect=[RuntimeError("boom"), None]):
            with self.assertRaises(RuntimeError):
                lifecycle.run(["--root", self.root, "--write"])
        parsed = store.read_jsonl(self.learnings)
        self.assertEqual([row["id"] for row in parsed], ["target", "concurrent"])
        self.assertEqual(parsed[0]["status"], "current")

    def test_concurrent_exact_changed_append_survives_refresh_rollback(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        real_exchange = store._exchange_paths
        exchange_count = [0]

        def append_published_row_during_rollback(source, target):
            if target == self.learnings:
                exchange_count[0] += 1
                if exchange_count[0] == 2:
                    with open(target, "r+", encoding="utf-8") as handle:
                        published = handle.readline()
                        handle.seek(0, os.SEEK_END)
                        handle.write(published)
            return real_exchange(source, target)

        with mock.patch.object(
                store, "_exchange_paths",
                side_effect=append_published_row_during_rollback,
        ), mock.patch.object(
                memory_md, "write_bounded_memory",
                side_effect=[RuntimeError("boom"), None],
        ):
            with self.assertRaises(RuntimeError):
                lifecycle.run(["--root", self.root, "--write"])

        parsed = store.read_jsonl(self.learnings)
        self.assertEqual(len(parsed), 2)
        self.assertEqual([row["id"] for row in parsed], ["target", "target"])
        self.assertEqual([row["status"] for row in parsed], [
            "current", "quarantined",
        ])

    def test_concurrent_atomic_replacement_is_preserved_and_refused(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        concurrent = json.dumps(self.row("concurrent", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        injected = [False]

        def replace_before_exchange(source, target):
            if target == self.learnings and not injected[0]:
                injected[0] = True
                descriptor, replacement = tempfile.mkstemp(dir=self.project)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(concurrent)
                os.replace(replacement, target)
            return real_exchange(source, target)

        with mock.patch.object(store, "_exchange_paths", side_effect=replace_before_exchange):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertEqual([row["id"] for row in store.read_jsonl(self.learnings)],
                         ["concurrent"])

    def test_aba_replacement_with_identical_bytes_is_refused(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        alternate = json.dumps(self.row("target", "2999-01-01")) + "\n"
        real_utility = summaries.learning_utility_rows

        def replace(content):
            descriptor, replacement = tempfile.mkstemp(dir=self.project)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(replacement, self.learnings)

        def aba_during_utility(*args, **kwargs):
            replace(alternate)
            replace(original)
            return real_utility(*args, **kwargs)

        with mock.patch.object(
            summaries, "learning_utility_rows", side_effect=aba_during_utility
        ):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        final = store.read_jsonl(self.learnings)[0]
        self.assertEqual((final["id"], final["status"], final["last_verified"]),
                         ("target", "current", "2000-01-01"))

    def test_parent_swap_after_validation_never_writes_outside_repo(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        outside = tempfile.mkdtemp(prefix="kimiflow-lifecycle-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        outside_file = os.path.join(outside, "LEARNINGS.jsonl")
        with open(outside_file, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.row("outside", "2000-01-01")) + "\n")
        original_project = self.project + "-original"
        real_require = store.require_local_path
        calls = [0]

        def validate_then_swap(root, path):
            result = real_require(root, path)
            calls[0] += 1
            if calls[0] == 2:
                os.rename(self.project, original_project)
                os.symlink(outside, self.project)
            return result

        with mock.patch.object(
            store, "require_local_path", side_effect=validate_then_swap
        ):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertEqual(store.read_jsonl(outside_file)[0]["status"], "current")
        original_file = os.path.join(original_project, "LEARNINGS.jsonl")
        self.assertEqual(store.read_jsonl(original_file)[0]["status"], "current")

    def test_post_exchange_replacement_is_preserved_and_refused(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        concurrent = json.dumps(self.row("concurrent", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        injected = [False]

        def replace_after_exchange(source, target):
            result = real_exchange(source, target)
            if target == self.learnings and not injected[0]:
                injected[0] = True
                descriptor, replacement = tempfile.mkstemp(dir=self.project)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(concurrent)
                os.replace(replacement, target)
            return result

        with mock.patch.object(store, "_exchange_paths", side_effect=replace_after_exchange):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertEqual([row["id"] for row in store.read_jsonl(self.learnings)],
                         ["concurrent"])

    def test_replacement_before_first_recovery_snapshot_wins(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        external_one = json.dumps(self.row("external-one", "2999-01-01")) + "\n"
        external_two = json.dumps(self.row("external-two", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        real_snapshot = store._file_snapshot
        exchange_injected = [False]
        snapshot_injected = [False]
        target_snapshot_count = [0]

        def replace_before_exchange(source, target):
            if target == self.learnings and not exchange_injected[0]:
                exchange_injected[0] = True
                descriptor, replacement = tempfile.mkstemp(dir=self.project)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(external_one)
                os.replace(replacement, target)
            return real_exchange(source, target)

        def replace_before_recovery_snapshot(path, **kwargs):
            if path == self.learnings and exchange_injected[0]:
                target_snapshot_count[0] += 1
            if (path == self.learnings and target_snapshot_count[0] == 3
                    and not snapshot_injected[0]):
                snapshot_injected[0] = True
                descriptor, replacement = tempfile.mkstemp(dir=self.project)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(external_two)
                os.replace(replacement, path)
            return real_snapshot(path, **kwargs)

        with mock.patch.object(store, "_exchange_paths", side_effect=replace_before_exchange), \
                mock.patch.object(store, "_file_snapshot", side_effect=replace_before_recovery_snapshot):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertEqual([row["id"] for row in store.read_jsonl(self.learnings)],
                         ["external-two"])

    def test_repeated_replacement_during_conflict_restores_newest_file(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        external_one = json.dumps(self.row("external-one", "2999-01-01")) + "\n"
        external_two = json.dumps(self.row("external-two", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        exchange_count = [0]

        def replace_before_exchange(source, target):
            if target == self.learnings:
                exchange_count[0] += 1
            if target == self.learnings and exchange_count[0] in (1, 2):
                descriptor, replacement = tempfile.mkstemp(dir=self.project)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(external_one if exchange_count[0] == 1 else external_two)
                os.replace(replacement, target)
            return real_exchange(source, target)

        with mock.patch.object(store, "_exchange_paths", side_effect=replace_before_exchange):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertEqual([row["id"] for row in store.read_jsonl(self.learnings)],
                         ["external-two"])

    def test_replacement_before_later_recovery_snapshot_wins(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        values = {
            name: json.dumps(self.row(name, "2999-01-01")) + "\n"
            for name in ("external-one", "external-two", "external-three")
        }
        real_exchange = store._exchange_paths
        real_snapshot = store._file_snapshot
        exchange_count = [0]
        target_snapshots_after_second = [0]

        def replacement(content):
            descriptor, path = tempfile.mkstemp(dir=self.project)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
            os.replace(path, self.learnings)

        def race_exchange(source, target):
            if target == self.learnings:
                exchange_count[0] += 1
                if exchange_count[0] == 1:
                    replacement(values["external-one"])
                elif exchange_count[0] == 2:
                    replacement(values["external-two"])
            return real_exchange(source, target)

        def race_later_snapshot(path, **kwargs):
            if path == self.learnings and exchange_count[0] == 2:
                target_snapshots_after_second[0] += 1
                if target_snapshots_after_second[0] == 2:
                    replacement(values["external-three"])
            return real_snapshot(path, **kwargs)

        with mock.patch.object(store, "_exchange_paths", side_effect=race_exchange), \
                mock.patch.object(store, "_file_snapshot", side_effect=race_later_snapshot):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertEqual([row["id"] for row in store.read_jsonl(self.learnings)],
                         ["external-three"])

    def test_unsupported_exchange_is_bounded_and_leaves_source_unchanged(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        with mock.patch.object(
            store, "_exchange_paths",
            side_effect=OSError(errno.ENOTSUP, "atomic path exchange unavailable"),
        ):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            self.assertEqual(handle.read(), original)

    def test_restore_exchange_failure_keeps_canonical_and_recovery_copy(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        concurrent = json.dumps(self.row("concurrent", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        exchange_count = [0]

        def replace_before_exchange(source, target):
            if target == self.learnings:
                exchange_count[0] += 1
            if target == self.learnings and exchange_count[0] == 1:
                descriptor, replacement = tempfile.mkstemp(dir=self.project)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(concurrent)
                os.replace(replacement, target)
            elif target == self.learnings:
                raise OSError(errno.EIO, "simulated recovery exchange failure")
            return real_exchange(source, target)

        with mock.patch.object(store, "_exchange_paths", side_effect=replace_before_exchange):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertTrue(os.path.isfile(self.learnings))
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "quarantined")
        recovery = [name for name in os.listdir(self.project) if name.endswith(".recovery")]
        self.assertEqual(len(recovery), 1)
        self.assertEqual(
            [row["id"] for row in store.read_jsonl(os.path.join(self.project, recovery[0]))],
            ["concurrent"],
        )

    def test_transient_displaced_snapshot_failure_keeps_both_versions(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        concurrent = json.dumps(self.row("concurrent", "2999-01-01")) + "\n"
        real_exchange = store._exchange_paths
        real_snapshot = store._file_snapshot
        exchange_count = [0]
        failed_snapshot = [False]

        def race_exchange(source, target):
            if target == self.learnings:
                exchange_count[0] += 1
                if exchange_count[0] == 1:
                    descriptor, replacement = tempfile.mkstemp(dir=self.project)
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        handle.write(concurrent)
                    os.replace(replacement, target)
            return real_exchange(source, target)

        def fail_displaced_once(path, **kwargs):
            if path != self.learnings and exchange_count[0] == 2 and not failed_snapshot[0]:
                failed_snapshot[0] = True
                return None
            return real_snapshot(path, **kwargs)

        with mock.patch.object(store, "_exchange_paths", side_effect=race_exchange), \
                mock.patch.object(store, "_file_snapshot", side_effect=fail_displaced_once):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertEqual([row["id"] for row in store.read_jsonl(self.learnings)],
                         ["concurrent"])
        recovery = [name for name in os.listdir(self.project) if name.endswith(".recovery")]
        self.assertEqual(len(recovery), 1)

    def test_mode_change_before_atomic_snapshot_is_preserved(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        os.chmod(self.learnings, 0o644)
        real_atomic_write = store.atomic_write

        def tighten_mode(path, data, **kwargs):
            os.chmod(path, 0o600)
            return real_atomic_write(path, data, **kwargs)

        with mock.patch.object(store, "atomic_write", side_effect=tighten_mode):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.learnings).st_mode), 0o600)

    def test_identical_byte_replacement_with_new_mode_is_refused(self):
        self.write_rows([self.row("target", "2000-01-01")])
        self.write_usage({})
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            original = handle.read()
        os.chmod(self.learnings, 0o644)
        real_exchange = store._exchange_paths
        injected = [False]

        def replace_mode_before_exchange(source, target):
            if target == self.learnings and not injected[0]:
                injected[0] = True
                descriptor, replacement = tempfile.mkstemp(dir=self.project)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(original)
                os.chmod(replacement, 0o600)
                os.replace(replacement, target)
            return real_exchange(source, target)

        with mock.patch.object(store, "_exchange_paths", side_effect=replace_mode_before_exchange):
            code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 1)
        self.assertEqual(stat.S_IMODE(os.stat(self.learnings).st_mode), 0o600)

    def test_false_status_preview_and_write_are_consistent(self):
        self.write_rows([self.row("target", "2000-01-01", status=False)])
        self.write_usage({})
        preview_code, preview = self.run_lifecycle()
        write_code, written = self.run_lifecycle("--write")
        self.assertEqual((preview_code, write_code), (0, 0))
        self.assertEqual(preview["candidate_ids"], ["target"])
        self.assertEqual(written["quarantined_ids"], ["target"])
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "quarantined")

    def test_duplicate_key_object_counts_every_top_level_id_for_uniqueness(self):
        valid = json.dumps(self.row("shared", "2000-01-01"))
        ambiguous = (json.dumps(self.row("other", "2000-01-01"))[:-1]
                     + ',"id":"shared","nested":{"keep":1,"keep":2}}')
        with open(self.learnings, "w", encoding="utf-8", newline="") as handle:
            handle.write(valid + "\n" + ambiguous + "\n")
        self.write_usage({})
        code, result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(result["quarantined_count"], 0)
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            self.assertEqual(handle.read(), valid + "\n" + ambiguous + "\n")

    def test_restore_refuses_id_hidden_in_duplicate_key_object(self):
        valid = json.dumps(self.row("shared", "2000-01-01", status="quarantined"))
        ambiguous = (json.dumps(self.row("other", "2000-01-01"))[:-1]
                     + ',"id":"shared","nested":{"keep":1,"keep":2}}')
        original = valid + "\n" + ambiguous + "\n"
        with open(self.learnings, "w", encoding="utf-8", newline="") as handle:
            handle.write(original)
        self.write_usage({})
        code, result = self.run_lifecycle("--restore", "shared", "--write")
        self.assertEqual(code, 1)
        self.assertEqual(result["reason"], "duplicate_id")
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            self.assertEqual(handle.read(), original)

    def test_write_preserves_existing_learning_file_mode(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        os.chmod(self.learnings, 0o600)
        self.write_usage({})
        code, _result = self.run_lifecycle("--write")
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.learnings).st_mode), 0o600)

    def test_refresh_failure_rolls_back_primary_row_and_allows_retry(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        with mock.patch.object(memory_md, "write_bounded_memory",
                               side_effect=[RuntimeError("boom"), None]):
            with self.assertRaises(RuntimeError):
                lifecycle.run(["--root", self.root, "--write"])
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")
        with mock.patch.object(provider, "_http_probe") as probe, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.run(["--root", self.root, "--write"]), 0)
        probe.assert_not_called()
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "quarantined")

    def test_refresh_failure_restores_derivative_snapshot_before_returning(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        memory = os.path.join(self.project, "MEMORY.md")
        index = os.path.join(self.project, "MEMORY-INDEX.json")
        database = os.path.join(self.project, "RECALL.sqlite")
        with open(memory, "w", encoding="utf-8") as handle:
            handle.write("stable memory\n")
        with open(index, "w", encoding="utf-8") as handle:
            handle.write('{"stable":true}\n')

        def partial_refresh(_root):
            with open(memory, "w", encoding="utf-8") as handle:
                handle.write("partial memory\n")
            with open(index, "w", encoding="utf-8") as handle:
                handle.write('{"partial":true}\n')
            with open(database, "wb") as handle:
                handle.write(b"partial database")
            raise RuntimeError("deadline")

        with mock.patch.object(lifecycle, "_refresh_derivatives",
                               side_effect=partial_refresh):
            with self.assertRaises(RuntimeError):
                lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")
        with open(memory, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "stable memory\n")
        with open(index, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"stable":true}\n')
        self.assertFalse(os.path.exists(database))

    def test_deadline_after_primary_publication_rolls_back_learning(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        real_write = store.atomic_write
        interrupted = [False]

        def interrupt_after_publication(path, content, *args, **kwargs):
            result = real_write(path, content, *args, **kwargs)
            if (path == self.learnings and not interrupted[0]
                    and '"status":"quarantined"' in content):
                interrupted[0] = True
                raise lifecycle.LifecycleDeadlineError("deadline")
            return result

        with mock.patch.object(store, "atomic_write",
                               side_effect=interrupt_after_publication), \
                mock.patch.object(lifecycle, "_refresh_derivatives"), \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 124)
        self.assertTrue(interrupted[0])
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")

    def test_deadline_crosses_best_effort_index_exception_handler(self):
        with mock.patch.object(lifecycle.memory_md, "write_bounded_memory"), \
                mock.patch.object(lifecycle.curate, "curate_json",
                                  return_value={}), \
                mock.patch.object(
                    lifecycle.curate.index_mod,
                    "run",
                    side_effect=lifecycle.LifecycleDeadlineError("deadline"),
                ):
            with self.assertRaises(lifecycle.LifecycleDeadlineError):
                lifecycle._refresh_derivatives(self.root)

    @unittest.skipUnless(
        hasattr(lifecycle.signal, "setitimer"), "POSIX interval timer required"
    )
    def test_success_disarms_deadline_before_receipt_output(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})

        def refresh_then_schedule_alarm(_root):
            lifecycle.signal.setitimer(lifecycle.signal.ITIMER_REAL, 0.01)

        def slow_receipt(*_args, **_kwargs):
            time.sleep(0.05)

        with mock.patch.dict(
                os.environ, {"KIMIFLOW_LIFECYCLE_DEADLINE_SECONDS": "1"}), \
                mock.patch.object(
                    lifecycle, "_refresh_derivatives",
                    side_effect=refresh_then_schedule_alarm,
                ), \
                mock.patch.object(
                    lifecycle.contracts, "json_print", side_effect=slow_receipt
                ), \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 0)
        self.assertEqual(
            store.read_jsonl(self.learnings)[0]["status"], "quarantined"
        )

    def test_invalid_prior_text_derivative_fails_before_source_mutation(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        memory = os.path.join(self.project, "MEMORY.md")
        with open(memory, "wb") as handle:
            handle.write(b"\xff")

        with mock.patch.object(lifecycle, "_refresh_derivatives") as refresh, \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 1)
        refresh.assert_not_called()
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")
        with open(memory, "rb") as handle:
            self.assertEqual(handle.read(), b"\xff")

    def test_oversized_learning_source_is_refused_before_evaluation(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})

        with mock.patch.object(lifecycle, "_MAX_LEARNING_BYTES", 8), \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 1)
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")

    def test_curation_cannot_expand_source_beyond_its_own_ceiling(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        with open(self.learnings, "rb") as handle:
            original = handle.read()

        with mock.patch.object(lifecycle, "_MAX_LEARNING_BYTES", len(original)), \
                mock.patch.object(lifecycle, "_refresh_derivatives") as refresh, \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 1)
        refresh.assert_not_called()
        with open(self.learnings, "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_bounded_snapshot_refuses_known_oversize_before_reading_payload(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        with mock.patch.object(store.os, "read", wraps=store.os.read) as read:
            with self.assertRaises(store.FileTooLargeError):
                store.stable_file_snapshot(self.learnings, max_bytes=8)

        read.assert_not_called()

    def test_atomic_compare_refuses_oversized_replacement_before_reading_payload(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        with open(self.learnings, encoding="utf-8") as handle:
            original = handle.read()
        snapshot = store.stable_file_snapshot(
            self.learnings, max_bytes=len(original.encode("utf-8"))
        )
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write(original + "{}\n")

        with mock.patch.object(store.os, "read", wraps=store.os.read) as read:
            with self.assertRaises(store.FileTooLargeError):
                store.atomic_write(
                    self.learnings, original, expected=original,
                    expected_snapshot=snapshot,
                    max_bytes=len(original.encode("utf-8")),
                )

        read.assert_not_called()

    def test_ambiguous_target_retains_oversized_displaced_source(self):
        candidate = "candidate\n"
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write(candidate)
        candidate_snapshot = store._file_snapshot(
            self.learnings, max_bytes=len(candidate.encode("utf-8"))
        )
        descriptor, displaced = tempfile.mkstemp(dir=self.project)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("x" * 64)
        real_snapshot = store._file_snapshot
        target_calls = [0]

        def unstable_target(path, **kwargs):
            if path == self.learnings:
                target_calls[0] += 1
                if target_calls[0] == 2:
                    return None
            return real_snapshot(path, **kwargs)

        with mock.patch.object(
                store, "_file_snapshot", side_effect=unstable_target
        ):
            store._restore_exchange_conflict(
                displaced, self.learnings, candidate_snapshot,
                max_bytes=len(candidate.encode("utf-8")),
            )

        self.assertFalse(os.path.exists(displaced))
        recovery = [
            os.path.join(self.project, name)
            for name in os.listdir(self.project)
            if name.endswith(".recovery")
        ]
        self.assertEqual(len(recovery), 1)
        with open(recovery[0], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "x" * 64)
        with open(self.learnings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), candidate)

    def test_bounded_recovery_retains_displaced_source_after_late_growth(self):
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write("CC")
        real_exchange = store._exchange_paths

        def grow_after_exchange(source, target):
            result = real_exchange(source, target)
            if target == self.learnings:
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write("B")
            return result

        with mock.patch.object(lifecycle, "_MAX_LEARNING_BYTES", 1), \
                mock.patch.object(
                    store, "_exchange_paths", side_effect=grow_after_exchange
                ):
            with self.assertRaisesRegex(
                    store.ConcurrentWriteError, "concurrent copy retained"):
                lifecycle._restore_unsafe_concurrent_source(
                    self.learnings, "A", 0o644
                )

        with open(self.learnings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "AB")
        recovery = [
            os.path.join(self.project, name)
            for name in os.listdir(self.project)
            if name.endswith(".recovery")
        ]
        self.assertEqual(len(recovery), 1)
        with open(recovery[0], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "CC")

    def test_atomic_compare_preserves_oversized_writer_before_exchange(self):
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write("A")
        snapshot = store._file_snapshot(self.learnings, max_bytes=1)
        real_exchange = store._exchange_paths
        injected = [False]

        def replace_before_exchange(source, target):
            if target == self.learnings and not injected[0]:
                injected[0] = True
                with open(target, "w", encoding="utf-8") as handle:
                    handle.write("CC")
            return real_exchange(source, target)

        with mock.patch.object(
                store, "_exchange_paths", side_effect=replace_before_exchange
        ):
            with self.assertRaises(store.ConcurrentWriteError):
                store.atomic_write(
                    self.learnings, "B", expected="A",
                    expected_snapshot=snapshot, max_bytes=1,
                )

        with open(self.learnings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "CC")

    def test_recovery_round_preserves_a_second_oversized_writer(self):
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write("A")
        snapshot = store._file_snapshot(self.learnings, max_bytes=1)
        real_exchange = store._exchange_paths
        exchange_count = [0]

        def replace_before_exchanges(source, target):
            if target == self.learnings:
                exchange_count[0] += 1
                if exchange_count[0] == 1:
                    with open(target, "w", encoding="utf-8") as handle:
                        handle.write("C")
                elif exchange_count[0] == 2:
                    with open(target, "w", encoding="utf-8") as handle:
                        handle.write("DD")
            return real_exchange(source, target)

        with mock.patch.object(
                store, "_exchange_paths", side_effect=replace_before_exchanges
        ):
            with self.assertRaises(store.ConcurrentWriteError):
                store.atomic_write(
                    self.learnings, "B", expected="A",
                    expected_snapshot=snapshot, max_bytes=1,
                )

        with open(self.learnings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "DD")
        recovery = [
            os.path.join(self.project, name)
            for name in os.listdir(self.project)
            if name.endswith(".recovery")
        ]
        self.assertEqual(len(recovery), 1)
        with open(recovery[0], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "C")

    def test_oversized_recovery_retains_a_newer_bounded_writer(self):
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write("A")
        snapshot = store._file_snapshot(self.learnings, max_bytes=1)
        real_exchange = store._exchange_paths
        exchange_count = [0]

        def replace_before_exchanges(source, target):
            if target == self.learnings:
                exchange_count[0] += 1
                if exchange_count[0] == 1:
                    with open(target, "w", encoding="utf-8") as handle:
                        handle.write("CC")
                elif exchange_count[0] == 2:
                    with open(target, "w", encoding="utf-8") as handle:
                        handle.write("D")
            return real_exchange(source, target)

        with mock.patch.object(
                store, "_exchange_paths", side_effect=replace_before_exchanges
        ):
            with self.assertRaises(store.ConcurrentWriteError):
                store.atomic_write(
                    self.learnings, "B", expected="A",
                    expected_snapshot=snapshot, max_bytes=1,
                )

        with open(self.learnings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "CC")
        recovery = [
            os.path.join(self.project, name)
            for name in os.listdir(self.project)
            if name.endswith(".recovery")
        ]
        self.assertEqual(len(recovery), 1)
        with open(recovery[0], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "D")

    def test_oversized_learning_row_count_is_refused_before_evaluation(self):
        self.write_rows([
            self.row("first", "2000-01-01"),
            self.row("second", "2000-01-01"),
        ])
        self.write_usage({})

        with mock.patch.object(lifecycle, "_MAX_LEARNING_ROWS", 1), \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 1)
        self.assertEqual(len(store.read_jsonl(self.learnings)), 2)

    def test_learning_segment_budget_aborts_before_materializing_remaining_rows(self):
        with self.assertRaisesRegex(ValueError, "row limit"):
            lifecycle._segments_text("\n" * 100000, max_rows=4)

    def test_oversized_text_derivative_is_refused_before_source_mutation(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        memory = os.path.join(self.project, "MEMORY.md")
        with open(memory, "w", encoding="utf-8") as handle:
            handle.write("too large")

        with mock.patch.object(lifecycle, "_MAX_DERIVATIVE_BYTES", 4), \
                mock.patch.object(lifecycle, "_refresh_derivatives") as refresh, \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 1)
        refresh.assert_not_called()
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")

    def test_new_oversized_text_derivative_rolls_back_source_and_derivatives(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        memory = os.path.join(self.project, "MEMORY.md")
        index = os.path.join(self.project, "MEMORY-INDEX.json")

        def oversized_refresh(_root):
            with open(memory, "w", encoding="utf-8") as handle:
                handle.write("oversized memory")
            with open(index, "w", encoding="utf-8") as handle:
                handle.write("oversized index")

        with mock.patch.object(lifecycle, "_MAX_DERIVATIVE_BYTES", 4), \
                mock.patch.object(
                    lifecycle, "_refresh_derivatives", side_effect=oversized_refresh
                ), contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 1)
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")
        self.assertFalse(os.path.exists(memory))
        self.assertFalse(os.path.exists(index))

    def test_over_row_budget_concurrent_source_is_retained_during_rollback(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        with open(self.learnings, encoding="utf-8") as handle:
            original = handle.read()

        def append_rows_and_fail(_root):
            with open(self.learnings, "a", encoding="utf-8") as handle:
                handle.write("\n" * 5)
            raise RuntimeError("refresh failed")

        with mock.patch.object(lifecycle, "_MAX_LEARNING_ROWS", 4), \
                mock.patch.object(
                    lifecycle, "_refresh_derivatives", side_effect=append_rows_and_fail
                ), contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 1)
        with open(self.learnings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), original)
        recovery = [
            os.path.join(self.project, name)
            for name in os.listdir(self.project)
            if name.endswith(".recovery")
        ]
        self.assertEqual(len(recovery), 1)
        with open(recovery[0], encoding="utf-8") as handle:
            self.assertTrue(handle.read().endswith("\n" * 5))

    def test_rollback_never_merges_beyond_source_ceiling(self):
        row = self.row("eligible", "2000-01-01")
        raw = (" " * 5000) + json.dumps(row, separators=(",", ":"))
        original = raw + "\n"
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write(original)
        source_snapshot = store.stable_file_snapshot(
            self.learnings, max_bytes=len(original.encode("utf-8"))
        )
        changed = dict(row)
        changed["status"] = "quarantined"
        concurrent = json.dumps(self.row("concurrent", "2999-01-01")) + "\n"

        def append_and_fail(_root):
            with open(self.learnings, "a", encoding="utf-8") as handle:
                handle.write(concurrent)
            raise RuntimeError("refresh failed")

        with mock.patch.object(
                lifecycle, "_MAX_LEARNING_BYTES", len(original.encode("utf-8"))
        ), mock.patch.object(
                lifecycle, "_refresh_derivatives", side_effect=append_and_fail
        ):
            with self.assertRaises(store.ConcurrentWriteError):
                lifecycle._write_and_refresh(
                    self.root, self.learnings,
                    [(raw, "\n", row)],
                    [(raw, "\n", changed, True)],
                    source_snapshot,
                )

        with open(self.learnings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), original)
        recovery = [
            os.path.join(self.project, name)
            for name in os.listdir(self.project)
            if name.endswith(".recovery")
        ]
        self.assertEqual(len(recovery), 1)
        with open(recovery[0], encoding="utf-8") as handle:
            self.assertIn(concurrent, handle.read())

    def test_derivative_rollback_attempts_every_restore_and_database_invalidation(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        database = os.path.join(self.project, "RECALL.sqlite")

        with mock.patch.object(
                lifecycle, "_refresh_derivatives",
                side_effect=RuntimeError("refresh failed"),
        ), mock.patch.object(
                lifecycle, "_restore_text_snapshot",
                side_effect=[RuntimeError("memory restore failed"), None],
        ) as restore, mock.patch.object(
                store, "_unlink_path", wraps=store._unlink_path,
        ) as unlink:
            with self.assertRaisesRegex(RuntimeError, "memory restore failed"):
                lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(restore.call_count, 2)
        unlink.assert_any_call(database)
        self.assertEqual(store.read_jsonl(self.learnings)[0]["status"], "current")

    def test_derivative_rollback_unlinks_through_pinned_project_directory(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        outside = tempfile.mkdtemp(prefix="kimiflow-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        moved = self.project + ".moved"
        outside_memory = os.path.join(outside, "MEMORY.md")

        def retarget_then_fail(_root):
            with open(os.path.join(self.project, "MEMORY.md"), "w",
                      encoding="utf-8") as handle:
                handle.write("partial pinned derivative\n")
            os.rename(self.project, moved)
            os.symlink(outside, self.project)
            with open(outside_memory, "w", encoding="utf-8") as handle:
                handle.write("outside sentinel\n")
            raise RuntimeError("refresh failed")

        with mock.patch.object(lifecycle, "_refresh_derivatives",
                               side_effect=retarget_then_fail):
            with self.assertRaises(RuntimeError):
                lifecycle.run(["--root", self.root, "--write"])

        with open(outside_memory, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "outside sentinel\n")
        self.assertFalse(os.path.exists(os.path.join(moved, "MEMORY.md")))
        self.assertEqual(
            store.read_jsonl(os.path.join(moved, "LEARNINGS.jsonl"))[0]["status"],
            "current",
        )

    def test_unchanged_write_does_not_refresh_derivatives(self):
        self.write_rows([])
        self.write_usage({})
        output = io.StringIO()
        with mock.patch.object(lifecycle, "_refresh_derivatives") as refresh, \
                contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", self.root, "--write"])

        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output.getvalue())["written"])
        refresh.assert_not_called()

    def test_lifecycle_holds_usage_lock_for_complete_operation(self):
        self.write_rows([])
        self.write_usage({})
        real_lock = store.path_lock
        usage_path = lifecycle.usage_metrics.usage_lock_path(self.root)
        usage_held = [False]

        @contextlib.contextmanager
        def tracked_lock(path):
            with real_lock(path):
                is_usage = os.path.abspath(path) == os.path.abspath(usage_path)
                if is_usage:
                    usage_held[0] = True
                try:
                    yield
                finally:
                    if is_usage:
                        usage_held[0] = False

        def checked_operate(*_args, **_kwargs):
            self.assertTrue(usage_held[0])
            return 0

        with mock.patch.object(store, "path_lock", new=tracked_lock), \
                mock.patch.object(lifecycle, "_operate",
                                  side_effect=checked_operate):
            self.assertEqual(lifecycle.run(["--root", self.root, "--write"]), 0)

    def test_usage_and_outcome_locks_share_physical_identity_through_root_alias(self):
        alias = self.root + "-alias"
        os.symlink(self.root, alias)
        self.addCleanup(os.unlink, alias)

        for name in ("MEMORY-USAGE.json", "STRATEGY-OUTCOMES.jsonl"):
            real_path = os.path.join(self.project, name)
            alias_path = os.path.join(alias, ".kimiflow", "project", name)
            self.assertEqual(
                store._path_lock_key(real_path),
                store._path_lock_key(alias_path),
            )

    def test_usage_writer_canonicalizes_an_aliased_workspace_root(self):
        alias = self.root + "-alias"
        os.symlink(self.root, alias)
        self.addCleanup(os.unlink, alias)

        lifecycle.usage_metrics.update_usage_metrics(alias, [], "recall")

        usage_path = os.path.join(self.project, "MEMORY-USAGE.json")
        self.assertTrue(os.path.isfile(usage_path))
        self.assertEqual(store.read_json(usage_path)["events"][0]["kind"], "recall")

    def test_usage_writer_locks_the_same_path_it_resolved_before_alias_retarget(self):
        other = tempfile.mkdtemp(prefix="kimiflow-usage-other-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        os.makedirs(os.path.join(other, ".kimiflow", "project"))
        alias = self.root + "-alias"
        os.symlink(self.root, alias)
        real_lock = store.path_lock
        retargeted = [False]

        @contextlib.contextmanager
        def retarget_before_lock(path):
            if not retargeted[0]:
                retargeted[0] = True
                os.unlink(alias)
                os.symlink(other, alias)
            with real_lock(path):
                yield

        with mock.patch.object(store, "path_lock", new=retarget_before_lock):
            lifecycle.usage_metrics.update_usage_metrics(alias, [], "recall")

        self.addCleanup(
            lambda: os.path.lexists(alias) and os.unlink(alias)
        )
        self.assertTrue(os.path.isfile(
            os.path.join(self.project, "MEMORY-USAGE.json")
        ))
        self.assertFalse(os.path.exists(os.path.join(
            other, ".kimiflow", "project", "MEMORY-USAGE.json"
        )))

    def test_lifecycle_pins_workspace_before_alias_retarget(self):
        self.write_rows([self.row("eligible", "2000-01-01")])
        self.write_usage({})
        other = tempfile.mkdtemp(prefix="kimiflow-lifecycle-other-")
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        other_project = os.path.join(other, ".kimiflow", "project")
        os.makedirs(other_project)
        other_row = self.row("other", "2999-01-01")
        with open(os.path.join(other_project, "LEARNINGS.jsonl"), "w",
                  encoding="utf-8") as handle:
            handle.write(json.dumps(other_row) + "\n")
        with open(os.path.join(other_project, "MEMORY-USAGE.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "items": {}, "events": []}, handle)
        alias = self.root + "-alias"
        os.symlink(self.root, alias)
        self.addCleanup(lambda: os.path.lexists(alias) and os.unlink(alias))
        real_lock = store.path_lock
        retargeted = [False]

        @contextlib.contextmanager
        def retarget_before_lock(path):
            if not retargeted[0]:
                retargeted[0] = True
                os.unlink(alias)
                os.symlink(other, alias)
            with real_lock(path):
                yield

        with mock.patch.object(store, "path_lock", new=retarget_before_lock), \
                contextlib.redirect_stderr(io.StringIO()):
            code = lifecycle.run(["--root", alias, "--write"])

        self.assertEqual(code, 0)
        self.assertEqual(
            store.read_jsonl(self.learnings)[0]["status"], "quarantined"
        )
        self.assertEqual(
            store.read_jsonl(os.path.join(other_project, "LEARNINGS.jsonl")),
            [other_row],
        )

    def test_lock_identity_ignores_root_spelling_on_case_insensitive_filesystems(self):
        alternate = self.root.swapcase()
        try:
            same_directory = os.path.samefile(self.root, alternate)
        except OSError:
            self.skipTest("case-sensitive filesystem")
        if not same_directory:
            self.skipTest("case-sensitive filesystem")

        name = "STRATEGY-OUTCOMES.jsonl"
        self.assertEqual(
            store._path_lock_key(os.path.join(self.project, name)),
            store._path_lock_key(
                os.path.join(alternate, ".kimiflow", "project", name)
            ),
        )

    def test_lock_identity_is_stable_when_parent_is_created(self):
        parent = os.path.join(self.root, "new-parent")
        target = os.path.join(parent, "MEMORY-USAGE.json")
        before = store._path_lock_key(target)
        os.mkdir(parent)

        self.assertEqual(store._path_lock_key(target), before)

    def test_lock_identity_survives_final_symlink_retargeting(self):
        first = os.path.join(self.project, "usage-a.json")
        second = os.path.join(self.project, "usage-b.json")
        link = os.path.join(self.project, "usage-link.json")
        for path in (first, second):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        os.symlink(first, link)
        before = set(store._path_lock_keys(link))
        os.unlink(link)
        os.symlink(second, link)
        after = set(store._path_lock_keys(link))

        self.assertEqual(len(before), 2)
        self.assertEqual(len(after), 2)
        self.assertEqual(len(before & after), 1)
        self.assertNotEqual(store._path_lock_key(first), store._path_lock_key(second))

    def test_reentrant_lock_fails_if_final_symlink_target_changes(self):
        first = os.path.join(self.project, "usage-a.json")
        second = os.path.join(self.project, "usage-b.json")
        link = os.path.join(self.project, "usage-link.json")
        for path in (first, second):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        os.symlink(first, link)

        with store.path_lock(link):
            os.unlink(link)
            os.symlink(second, link)
            with self.assertRaisesRegex(
                    store.ConcurrentWriteError, "identity changed"):
                with store.path_lock(link):
                    self.fail("retargeted lock must not enter")

    def test_reentrant_alias_requires_its_lexical_lock(self):
        target = os.path.join(self.project, "usage-a.json")
        alias_root = self.root + "-alias"
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        os.symlink(self.root, alias_root)
        self.addCleanup(os.unlink, alias_root)
        alias = os.path.join(
            alias_root, ".kimiflow", "project", "usage-a.json"
        )

        with store.path_lock(target):
            with self.assertRaisesRegex(
                    store.ConcurrentWriteError, "identity changed"):
                with store.path_lock(alias):
                    self.fail("unheld lexical alias must fail closed")

    def test_parent_detach_rollback_retains_newer_pinned_writer(self):
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write("A")
        moved = self.project + ".moved"
        real_exchange = store._exchange_paths
        exchange_count = [0]

        def detach_then_write(source, target):
            exchange_count[0] += 1
            if exchange_count[0] == 1:
                result = real_exchange(source, target)
                os.rename(self.project, moved)
                os.mkdir(self.project)
                return result
            if exchange_count[0] == 2:
                anchor = store._active_anchor(target)
                descriptor = os.open(
                    os.path.basename(target), os.O_WRONLY | os.O_TRUNC,
                    dir_fd=anchor["descriptor"],
                )
                try:
                    os.write(descriptor, b"C")
                finally:
                    os.close(descriptor)
            return real_exchange(source, target)

        with store.local_path_guard(self.root, self.project), \
                mock.patch.object(
                    store, "_exchange_paths", side_effect=detach_then_write
                ):
            snapshot = store._file_snapshot(self.learnings, max_bytes=1)
            with self.assertRaisesRegex(
                    store.ConcurrentWriteError, "parent changed"):
                store.atomic_write(
                    self.learnings, "B", expected="A",
                    expected_snapshot=snapshot, max_bytes=1,
                )

        moved_source = os.path.join(moved, "LEARNINGS.jsonl")
        with open(moved_source, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "A")
        recovery = [
            os.path.join(moved, name)
            for name in os.listdir(moved)
            if name.endswith(".recovery")
        ]
        self.assertEqual(len(recovery), 1)
        with open(recovery[0], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "C")

    def test_parent_detach_retains_displaced_writer_after_late_growth(self):
        with open(self.learnings, "w", encoding="utf-8") as handle:
            handle.write("A")
        moved = self.project + ".moved"
        real_exchange = store._exchange_paths
        exchange_count = [0]

        def detach_write_and_grow(source, target):
            exchange_count[0] += 1
            if exchange_count[0] == 1:
                result = real_exchange(source, target)
                os.rename(self.project, moved)
                os.mkdir(self.project)
                return result
            if exchange_count[0] == 2:
                anchor = store._active_anchor(target)
                descriptor = os.open(
                    os.path.basename(target), os.O_WRONLY | os.O_TRUNC,
                    dir_fd=anchor["descriptor"],
                )
                try:
                    os.write(descriptor, b"C")
                finally:
                    os.close(descriptor)
                result = real_exchange(source, target)
                descriptor = os.open(
                    os.path.basename(target), os.O_WRONLY | os.O_APPEND,
                    dir_fd=anchor["descriptor"],
                )
                try:
                    os.write(descriptor, b"D")
                finally:
                    os.close(descriptor)
                return result
            return real_exchange(source, target)

        with store.local_path_guard(self.root, self.project), \
                mock.patch.object(
                    store, "_exchange_paths", side_effect=detach_write_and_grow
                ):
            snapshot = store._file_snapshot(self.learnings, max_bytes=1)
            with self.assertRaisesRegex(
                    store.ConcurrentWriteError, "could not be verified"):
                store.atomic_write(
                    self.learnings, "B", expected="A",
                    expected_snapshot=snapshot, max_bytes=1,
                )

        moved_source = os.path.join(moved, "LEARNINGS.jsonl")
        with open(moved_source, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "AD")
        recovery = [
            os.path.join(moved, name)
            for name in os.listdir(moved)
            if name.endswith(".recovery")
        ]
        self.assertEqual(len(recovery), 1)
        with open(recovery[0], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "C")

    def test_symlinked_project_parent_is_refused(self):
        outside = tempfile.mkdtemp(prefix="kimiflow-outside-")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        shutil.rmtree(self.project)
        os.symlink(outside, self.project)
        outside_learnings = os.path.join(outside, "LEARNINGS.jsonl")
        with open(outside_learnings, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.row("eligible", "2000-01-01")) + "\n")
        with open(outside_learnings, encoding="utf-8") as handle:
            before = handle.read()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.run(["--root", self.root, "--write"]), 1)
        with open(outside_learnings, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    def test_malformed_large_fields_cannot_expand_preview_output(self):
        self.write_rows([self.row("x" * 100000, "2000-01-01",
                                  confidence={"nested": "y" * 100000})])
        self.write_usage({})
        code, result = self.run_lifecycle()
        self.assertEqual(code, 0)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["rows"][0]["id"], "")
        self.assertEqual(result["rows"][0]["confidence"], "invalid")
        self.assertLess(len(json.dumps(result)), 5000)

    def test_oversized_protected_id_cannot_expand_preview_output(self):
        self.write_rows([
            self.row("x" * 100000, "2999-01-01", maturity="invalid"),
        ])
        self.write_usage({})

        code, result = self.run_lifecycle()

        self.assertEqual(code, 0)
        self.assertEqual(result["protected_count"], 1)
        self.assertEqual(result["protected_ids"], [""])
        self.assertLess(len(json.dumps(result)), 5000)

    def test_oversized_demotion_id_cannot_expand_preview_output(self):
        self.write_rows([
            self.row("x" * 100000, "2999-01-01", maturity="durable"),
        ])
        self.write_usage({})

        with mock.patch.object(lifecycle, "_evidence_drifted", return_value=True), \
                mock.patch.object(lifecycle, "_evidence_is_current", return_value=False):
            code, result = self.run_lifecycle()

        self.assertEqual(code, 0)
        self.assertEqual(result["demotion_candidate_count"], 1)
        self.assertEqual(result["demotion_candidate_ids"], [""])
        self.assertLess(len(json.dumps(result)), 5000)

    def test_oversized_restore_id_cannot_expand_success_output(self):
        rid = "x" * 100000
        self.write_rows([
            self.row(rid, "2999-01-01", status="quarantined"),
        ])
        self.write_usage({})

        code, result = self.run_lifecycle("--restore", rid)

        self.assertEqual(code, 0)
        self.assertEqual(result["restored_id"], "")
        self.assertLess(len(json.dumps(result)), 5000)

    def test_huge_usage_count_cannot_expand_preview_output(self):
        self.write_rows([self.row("bounded", "2999-01-01")])
        self.write_usage({"learning:bounded": {"use_count": "9" * 4000}})
        code, result = self.run_lifecycle()
        self.assertEqual(code, 0)
        self.assertLessEqual(result["rows"][0]["use_count"], 999999999)
        self.assertLess(len(json.dumps(result)), 5000)

    def test_no_delete_import_network_dependency_surface(self):
        self.write_rows([])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["lifecycle", "--root", self.root]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "preview")
        with open(lifecycle.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("urllib", "requests", "socket", "subprocess", "--delete", "--import"):
            self.assertNotIn(forbidden, source)

    def test_write_refreshes_derivatives_without_transitive_provider_probe(self):
        encoded = json.dumps(self.row("eligible", "2000-01-01"), ensure_ascii=False)
        original_tail = "\r\n[1,2]\nBROKEN\r\n\n"
        with open(self.learnings, "w", encoding="utf-8", newline="") as handle:
            handle.write(encoded + original_tail)
        self.write_usage({})
        with mock.patch.object(provider, "_http_probe") as probe, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lifecycle.run(["--root", self.root, "--write"]), 0)
        probe.assert_not_called()
        self.assertTrue(os.path.isfile(os.path.join(self.project, "MEMORY-INDEX.json")))
        with open(self.learnings, "r", encoding="utf-8", newline="") as handle:
            self.assertTrue(handle.read().endswith(original_tail))


if __name__ == "__main__":
    unittest.main()
