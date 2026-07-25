import json
import os
import shutil
import stat
import tarfile
import tempfile
import time
import unittest

from memory_router import retention


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, ".kimiflow"))

    def make_run(self, slug, status, modified):
        run = os.path.join(self.root, ".kimiflow", slug)
        os.makedirs(run)
        with open(os.path.join(run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Status: %s\nPhase 7: %s\n" % (status, "done" if status == "done" else "open"))
        with open(os.path.join(run, "INTENT.md"), "w", encoding="utf-8") as handle:
            handle.write("Build %s\n" % slug)
        with open(os.path.join(run, "VERIFICATION.md"), "w", encoding="utf-8") as handle:
            handle.write("verified\n")
        for current, dirs, files in os.walk(run):
            os.utime(current, (modified, modified))
            for name in files:
                os.utime(os.path.join(current, name), (modified, modified))
        return run

    def test_retention_archives_only_aged_terminal_run_with_verified_stub(self):
        now = int(time.time())
        active = self.make_run("active", "active", now - 90 * 86400)
        recent = self.make_run("recent", "done", now - 2 * 86400)
        aged = self.make_run("aged", "done", now - 90 * 86400)
        os.makedirs(os.path.join(self.root, ".kimiflow", "session"))
        with open(
            os.path.join(self.root, ".kimiflow", "session", "ACTIVE_RUN.json"),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump({"status": "active", "run": ".kimiflow/active"}, handle)

        preview = retention.eligibility(self.root, min_age_days=30, keep=0, now=now)
        reasons = {row["slug"]: row["reason"] for row in preview["runs"]}
        self.assertNotIn("active", reasons)  # nonterminal is never a candidate
        self.assertEqual(reasons["recent"], "too_recent")
        self.assertEqual(reasons["aged"], "eligible")

        result = retention.archive_one(
            self.root, min_age_days=30, keep=0, now=now, write=True,
        )
        self.assertEqual(result["status"], "archived")
        archive = os.path.join(self.root, result["archive"])
        self.assertEqual(stat.S_IMODE(os.stat(archive).st_mode), 0o600)
        with tarfile.open(archive, "r:gz") as handle:
            names = set(handle.getnames())
        self.assertIn("VERIFICATION.md", names)
        self.assertIn(retention.MANIFEST_NAME, names)
        self.assertTrue(os.path.isfile(os.path.join(aged, "ARCHIVED.md")))
        self.assertTrue(os.path.isfile(os.path.join(aged, "STATE.md")))
        self.assertTrue(os.path.isfile(os.path.join(aged, "INTENT.md")))
        self.assertFalse(os.path.exists(os.path.join(aged, "VERIFICATION.md")))
        self.assertTrue(os.path.isdir(active))
        self.assertTrue(os.path.isdir(recent))

    def test_phase7_done_does_not_make_active_run_terminal(self):
        now = int(time.time())
        run = self.make_run("unfinished", "active", now - 90 * 86400)
        with open(os.path.join(run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Status: active\nPhase 7: done\n")
        os.utime(run, (now - 90 * 86400, now - 90 * 86400))

        preview = retention.eligibility(
            self.root, min_age_days=30, keep=0, now=now,
        )

        self.assertNotIn("unfinished", {row["slug"] for row in preview["runs"]})

    def test_symlinked_archive_input_fails_without_replacing_run(self):
        now = int(time.time())
        run = self.make_run("unsafe", "done", now - 90 * 86400)
        os.symlink(os.path.join(run, "STATE.md"), os.path.join(run, "link"))
        os.utime(run, (now - 90 * 86400, now - 90 * 86400))
        with self.assertRaisesRegex(retention.RetentionError, "unsafe"):
            retention.archive_one(self.root, min_age_days=30, keep=0, now=now, write=True)
        self.assertTrue(os.path.islink(os.path.join(run, "link")))
        self.assertFalse(os.path.exists(os.path.join(run, "ARCHIVED.md")))

    def test_change_after_archive_creation_is_restored_instead_of_lost(self):
        now = int(time.time())
        run = self.make_run("changed", "done", now - 90 * 86400)
        selected = next(
            row for row in retention.eligibility(
                self.root, min_age_days=30, keep=0, now=now,
            )["runs"]
            if row["slug"] == "changed"
        )
        archive_rel, manifest = retention._create_archive(self.root, selected)
        with open(os.path.join(run, "VERIFICATION.md"), "a", encoding="utf-8") as handle:
            handle.write("late evidence\n")
        with self.assertRaisesRegex(retention.RetentionError, "source_changed"):
            retention._replace_with_stub(
                self.root, selected, archive_rel, manifest,
            )
        self.assertTrue(os.path.isfile(os.path.join(run, "VERIFICATION.md")))
        with open(os.path.join(run, "VERIFICATION.md"), encoding="utf-8") as handle:
            self.assertIn("late evidence", handle.read())
        self.assertFalse(os.path.exists(os.path.join(run, "ARCHIVED.md")))

    def test_symlinked_archive_directory_is_rejected(self):
        now = int(time.time())
        run = self.make_run("archive-link", "done", now - 90 * 86400)
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside)
        os.symlink(outside, os.path.join(self.root, ".kimiflow", "archive"))
        with self.assertRaisesRegex((ValueError, retention.RetentionError), "unsafe"):
            retention.archive_one(
                self.root, min_age_days=30, keep=0, now=now, write=True,
            )
        self.assertEqual(os.listdir(outside), [])
        self.assertTrue(os.path.isdir(run))

    def test_interrupted_atomic_exchange_keeps_path_and_recovers_late_change(self):
        now = int(time.time())
        run = self.make_run("crash-safe", "done", now - 90 * 86400)
        selected = next(
            row for row in retention.eligibility(
                self.root, min_age_days=30, keep=0, now=now,
            )["runs"]
            if row["slug"] == "crash-safe"
        )
        archive_rel, manifest = retention._create_archive(self.root, selected)
        parent = os.path.dirname(run)
        with retention.store.local_path_guard(self.root, parent):
            prepared = retention._prepare_stub(
                self.root, parent, selected, archive_rel, manifest,
            )
            retention.store._exchange_paths(prepared, run)
        self.assertTrue(os.path.isfile(os.path.join(run, "ARCHIVED.md")))
        with open(os.path.join(prepared, "VERIFICATION.md"), "a", encoding="utf-8") as handle:
            handle.write("late evidence\n")

        result = retention._recover_interrupted(self.root)

        self.assertEqual(result[0]["action"], "restored")
        self.assertFalse(os.path.exists(prepared))
        with open(os.path.join(run, "VERIFICATION.md"), encoding="utf-8") as handle:
            self.assertIn("late evidence", handle.read())

    def test_recovery_scan_never_deletes_an_unauthenticated_matching_directory(self):
        now = int(time.time())
        self.make_run("owned", "done", now - 90 * 86400)
        suspicious = os.path.join(
            self.root, ".kimiflow", ".owned.retention.0123456789abcdef",
        )
        os.makedirs(suspicious)
        with open(os.path.join(suspicious, "user-data.txt"), "w", encoding="utf-8") as handle:
            handle.write("preserve me\n")

        with self.assertRaisesRegex(retention.RetentionError, "manifest_invalid"):
            retention._recover_interrupted(self.root)

        self.assertTrue(os.path.isfile(os.path.join(suspicious, "user-data.txt")))


if __name__ == "__main__":
    unittest.main()
