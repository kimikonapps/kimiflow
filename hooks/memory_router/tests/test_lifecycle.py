import contextlib
import errno
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from memory_router import lifecycle, memory_md, provider, rows, store
from memory_router.__main__ import main


class LifecycleCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kimiflow-lifecycle-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        self.learnings = os.path.join(self.project, "LEARNINGS.jsonl")
        self.usage = os.path.join(self.project, "MEMORY-USAGE.json")
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

        def replace_before_recovery_snapshot(path):
            if path == self.learnings and exchange_injected[0]:
                target_snapshot_count[0] += 1
            if (path == self.learnings and target_snapshot_count[0] == 3
                    and not snapshot_injected[0]):
                snapshot_injected[0] = True
                descriptor, replacement = tempfile.mkstemp(dir=self.project)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(external_two)
                os.replace(replacement, path)
            return real_snapshot(path)

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

        def race_later_snapshot(path):
            if path == self.learnings and exchange_count[0] == 2:
                target_snapshots_after_second[0] += 1
                if target_snapshots_after_second[0] == 2:
                    replacement(values["external-three"])
            return real_snapshot(path)

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

        def fail_displaced_once(path):
            if path != self.learnings and exchange_count[0] == 2 and not failed_snapshot[0]:
                failed_snapshot[0] = True
                return None
            return real_snapshot(path)

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
