import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import consolidate
from memory_router.__main__ import main

TAG = "kimiflow--v0.1.50"
_ISO_ENV = {"HOME": "/tmp", "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/"}


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _env():
    return dict(_ISO_ENV, PATH=os.environ.get("PATH", ""))


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


_ROWS = (
    '{"id":"L1","status":"current","kind":"learning","scope":"project","topic":"a","summary":"same sum"}\n'
    '{"id":"L2","status":"current","kind":"learning","scope":"project","topic":"a","summary":"same sum"}\n'
    '{"id":"L3","status":"current","kind":"learning","scope":"project","topic":"b","summary":"unique"}\n'
    '{"id":"L4","status":"superseded","kind":"learning","scope":"project","topic":"c","summary":"old"}\n'
    '{"id":"L5","status":"stale","kind":"learning","scope":"project","topic":"d","summary":"stalest"}\n'
)


class ConsolidateRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        envp = mock.patch.dict(os.environ, _env(), clear=True)
        envp.start()
        self.addCleanup(envp.stop)

    def write_learnings(self, text=_ROWS):
        with open(os.path.join(self.project, "LEARNINGS.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def obj(self, argv=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = consolidate.run(["--root", self.root] + (argv or []))
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_key_order(self):
        self.assertEqual(list(self.obj().keys()),
                         ["schema_version", "status", "written", "archive_path",
                          "current_count", "archived_superseded_count", "duplicate_groups"])

    def test_preview_counts_and_duplicates(self):
        self.write_learnings()
        o = self.obj()
        self.assertEqual(o["status"], "preview")
        self.assertEqual(o["written"], False)
        self.assertEqual(o["current_count"], 3)            # L1,L2,L3
        self.assertEqual(o["archived_superseded_count"], 1)  # L4
        self.assertEqual(o["duplicate_groups"], [{"summary": "same sum", "ids": ["L1", "L2"]}])

    def test_preview_does_not_touch_files(self):
        self.write_learnings()
        self.obj()
        self.assertEqual(_read(os.path.join(self.project, "LEARNINGS.jsonl")), _ROWS)
        self.assertFalse(os.path.exists(os.path.join(self.project, "LEARNINGS.archive.jsonl")))

    def test_write_archives_and_drops_superseded(self):
        self.write_learnings()
        with mock.patch("memory_router.clock.iso_now", return_value="2026-06-29T00:00:00Z"):
            o = self.obj(["--write"])
        self.assertEqual(o["status"], "consolidated")
        self.assertEqual(o["written"], True)
        kept = _read(os.path.join(self.project, "LEARNINGS.jsonl"))
        self.assertNotIn('"L4"', kept)                     # superseded dropped
        self.assertIn('"L1"', kept)
        archive = _read(os.path.join(self.project, "LEARNINGS.archive.jsonl"))
        self.assertIn('"L4"', archive)                     # superseded archived
        self.assertTrue(os.path.isfile(os.path.join(self.project, "MEMORY-INDEX.json")))

    def test_write_without_learnings_file_no_crash(self):
        o = self.obj(["--write"])
        self.assertEqual(o["status"], "consolidated")
        self.assertEqual(o["current_count"], 0)

    def test_unknown_arg(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = consolidate.run(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "memory-router: consolidate: unknown argument: --bogus\n")

    def test_dispatch_registration(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["consolidate", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertIn('"schema_version":1', out.getvalue())


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "sqlite3", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


@unittest.skipUnless(_tools_present(), "bash/jq/sqlite3/git or pinned tag unavailable")
class ConsolidateParityCase(unittest.TestCase):
    """Grounds `consolidate` stdout (timestamp-free) + the rewritten LEARNINGS.jsonl and
    LEARNINGS.archive.jsonl byte-for-byte vs the pinned bash."""

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

    def _populate(self, root):
        proj = os.path.join(root, ".kimiflow", "project")
        os.makedirs(proj)
        with open(os.path.join(proj, "LEARNINGS.jsonl"), "w") as fh:
            fh.write(_ROWS)
        return proj

    def _bash(self, root, argv):
        return subprocess.run(["bash", self.script, "consolidate", "--root", root] + argv,
                              stdout=subprocess.PIPE, text=True, check=True, env=_env()).stdout

    def _py(self, root, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(consolidate.run(["--root", root] + argv), 0)
        return out.getvalue()

    def _roots(self):
        rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rb, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rp, ignore_errors=True)
        self._populate(rb)
        self._populate(rp)
        return rb, rp

    def test_stdout_parity(self):
        for argv in ([], ["--pretty"]):
            rb, rp = self._roots()
            self.assertEqual(self._bash(rb, argv), self._py(rp, argv), "argv=%r" % argv)

    def test_write_file_parity(self):
        rb, rp = self._roots()
        self.assertEqual(self._bash(rb, ["--write"]), self._py(rp, ["--write"]))
        proj = os.path.join(".kimiflow", "project")
        self.assertEqual(_read(os.path.join(rb, proj, "LEARNINGS.jsonl")),
                         _read(os.path.join(rp, proj, "LEARNINGS.jsonl")))
        self.assertEqual(_read(os.path.join(rb, proj, "LEARNINGS.archive.jsonl")),
                         _read(os.path.join(rp, proj, "LEARNINGS.archive.jsonl")))


if __name__ == "__main__":
    unittest.main()
