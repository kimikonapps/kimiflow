import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import index, recall_index
from memory_router.__main__ import main

TAG = "kimiflow--v0.1.50"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _capture(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = index.run(argv)
    return code, out.getvalue(), err.getvalue()


class IndexRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def out(self, argv):
        code, out, err = _capture(["--root", self.root] + argv)
        self.assertEqual(err, "")
        self.assertEqual(code, 0)
        return out

    def test_preview_when_no_db_no_write(self):
        out = self.out([])
        self.assertEqual(json.loads(out), {
            "schema_version": 1, "status": "preview",
            "path": ".kimiflow/project/RECALL.sqlite",
            "written": False, "sqlite_available": True, "documents": 0,
        })

    def test_compact_key_order(self):
        # compact JSON must preserve the Bash key order exactly.
        out = self.out([])
        self.assertEqual(
            out.rstrip("\n"),
            '{"schema_version":1,"status":"preview","path":".kimiflow/project/RECALL.sqlite",'
            '"written":false,"sqlite_available":true,"documents":0}',
        )

    def test_indexed_on_write(self):
        obj = json.loads(self.out(["--write"]))
        self.assertEqual(obj["status"], "indexed")
        self.assertTrue(obj["written"])
        self.assertTrue(os.path.isfile(recall_index.recall_db_path(self.root)))

    def test_failed_write_is_reported(self):
        with mock.patch("memory_router.recall_index.build_recall_index", return_value=1):
            code, out, err = _capture(["--root", self.root, "--write"])
        self.assertEqual(err, "")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "failed")
        self.assertFalse(json.loads(out)["written"])

    def test_restore_failure_reports_recovery_backup(self):
        project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(project)
        backup = os.path.join(project, ".RECALL.sqlite.backup.test")
        with open(backup, "wb") as handle:
            handle.write(b"last-good")
        with mock.patch("memory_router.recall_index.build_recall_index", return_value=4):
            code, out, err = _capture(["--root", self.root, "--write"])
        self.assertEqual((code, err), (1, ""))
        obj = json.loads(out)
        self.assertEqual(obj["reason"], "restore_failed")
        self.assertEqual(
            obj["recovery_backup"],
            ".kimiflow/project/.RECALL.sqlite.backup.test",
        )

    def test_available_when_db_exists_no_write(self):
        self.out(["--write"])
        obj = json.loads(self.out([]))
        self.assertEqual(obj["status"], "available")
        self.assertFalse(obj["written"])

    def test_stale_database_is_not_reported_available(self):
        project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(project)
        memory = os.path.join(project, "MEMORY.md")
        with open(memory, "w", encoding="utf-8") as handle:
            handle.write("alpha\n")
        self.out(["--write"])
        with open(memory, "w", encoding="utf-8") as handle:
            handle.write("beta\n")
        obj = json.loads(self.out([]))
        self.assertEqual(obj["status"], "stale")
        self.assertEqual(obj["documents"], 0)

    def test_documents_counts_rows(self):
        os.makedirs(os.path.join(self.root, ".kimiflow", "project"))
        with open(os.path.join(self.root, ".kimiflow", "project", "MEMORY.md"), "w") as fh:
            fh.write("hello\n")
        obj = json.loads(self.out(["--write"]))
        self.assertEqual(obj["documents"], 1)

    def test_pretty_indents(self):
        out = self.out(["--pretty"])
        self.assertIn('"schema_version": 1', out)  # space after colon

    def test_unavailable_branch_omits_written(self):
        with mock.patch("memory_router.recall_index.fts5_available", return_value=False):
            code, out, err = _capture(["--root", self.root])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {
            "schema_version": 1, "status": "unavailable",
            "path": ".kimiflow/project/RECALL.sqlite",
            "sqlite_available": False, "documents": 0,
        })
        self.assertNotIn("written", out)

    def test_unknown_arg_dies_exit_2(self):
        code, out, err = _capture(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, "memory-router: index: unknown argument: --bogus\n")

    def test_help_exit_0(self):
        code, out, err = _capture(["--help"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("#!/usr/bin/env bash"))

    def test_dispatch_registration(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["index", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertIn('"status":"preview"', out.getvalue())


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "sqlite3", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


@unittest.skipUnless(_tools_present(), "bash/jq/sqlite3/git or pinned tag unavailable")
class IndexParityCase(unittest.TestCase):
    """Grounds the Python `index` stdout byte-for-byte against the real Bash at the
    pinned tag. This is the first stdout-parity harness; `index` stdout is timestamp
    free, so no normalization is needed."""

    @classmethod
    def setUpClass(cls):
        src = subprocess.run(
            ["git", "-C", _repo_root(), "show", TAG + ":hooks/memory-router.sh"],
            stdout=subprocess.PIPE, check=True,
        ).stdout
        fd, cls.script = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "wb") as fh:
            fh.write(src)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.script)

    def _bash(self, root, argv):
        return subprocess.run(
            ["bash", self.script, "index", "--root", root] + argv,
            stdout=subprocess.PIPE, text=True, check=True,
        ).stdout

    def _py(self, root, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(index.run(["--root", root] + argv), 0)
        return out.getvalue()

    def assert_parity(self, argv, populate=False):
        rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rb, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rp, ignore_errors=True)
        if populate:
            for root in (rb, rp):
                proj = os.path.join(root, ".kimiflow", "project")
                os.makedirs(proj)
                with open(os.path.join(proj, "MEMORY.md"), "w") as fh:
                    fh.write("line one\nline two\n")
        self.assertEqual(self._bash(rb, argv), self._py(rp, argv), "argv=%r" % argv)

    def test_preview(self):
        self.assert_parity([])

    def test_preview_pretty(self):
        self.assert_parity(["--pretty"])

    def test_indexed(self):
        self.assert_parity(["--write"])

    def test_indexed_pretty(self):
        self.assert_parity(["--write", "--pretty"])

    def test_indexed_populated(self):
        self.assert_parity(["--write"], populate=True)


if __name__ == "__main__":
    unittest.main()
