import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import curate
from memory_router.__main__ import main

TAG = "kimiflow--v0.1.50"
_ISO_ENV = {"HOME": "/tmp", "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/"}
_TS = "2026-06-29T00:00:00Z"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _env():
    return dict(_ISO_ENV, PATH=os.environ.get("PATH", ""))


class CurateRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)

    def write(self, name, text):
        with open(os.path.join(self.project, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_curate(self, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), \
                mock.patch("memory_router.clock.iso_now", return_value=_TS), \
                contextlib.redirect_stdout(out):
            code = curate.run(["--root", self.root] + argv)
        return code, out.getvalue()

    def obj(self, argv=None):
        code, out = self.run_curate(argv or [])
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_key_order(self):
        self.assertEqual(list(self.obj().keys()), [
            "schema_version", "updated_at", "repo_id", "language",
            "always_on_memory_tokens_estimate", "vault", "provider", "learnings",
            "user_profile", "usage", "economics", "lifecycle", "topics", "curation",
        ])

    def test_topics_sorted_grouped(self):
        self.write("LEARNINGS.jsonl",
                   '{"id":"L1","status":"current","topic":"beta"}\n'
                   '{"id":"L2","status":"current","topic":"alpha"}\n'
                   '{"id":"L3","status":"current","topic":"beta"}\n'
                   '{"id":"L4","status":"current"}\n'
                   '{"id":"L5","status":"stale","topic":"alpha"}\n')
        self.assertEqual(self.obj()["topics"], {"alpha": ["L2"], "beta": ["L1", "L3"], "uncategorized": ["L4"]})

    def test_repo_id_unknown_without_remote(self):
        self.assertEqual(self.obj()["repo_id"], "unknown")

    def test_write_creates_index_and_recall(self):
        self.write("MEMORY.md", "hello world\n")
        self.write("LEARNINGS.jsonl", '{"id":"L1","status":"current","topic":"a"}\n')
        self.run_curate(["--write"])
        index = os.path.join(self.project, "MEMORY-INDEX.json")
        self.assertTrue(os.path.isfile(index))
        with open(index, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.endswith("}\n"))            # jq . trailing newline
        self.assertIn('  "schema_version": 1', text)     # pretty 2-space indent
        self.assertEqual(json.loads(text)["topics"], {"a": ["L1"]})

    def test_unknown_arg_exit_2(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = curate.run(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "memory-router: curate: unknown argument: --bogus\n")

    def test_dispatch_registration(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            code = main(["curate", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertIn('"schema_version":1', out.getvalue())

    def test_quarantined_rows_leave_existing_curated_derivatives(self):
        self.write("LEARNINGS.jsonl",
                   '{"id":"current","status":"current","topic":"a"}\n'
                   '{"id":"hidden","status":"quarantined","topic":"a"}\n')
        obj = self.obj()
        self.assertEqual(obj["topics"], {"a": ["current"]})
        self.assertEqual(obj["lifecycle"]["current"], 1)


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "sqlite3", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _strip_ts(text):
    return re.sub(r'("updated_at"\s*:\s*)"[^"]*"', r'\1"<TS>"', text)


@unittest.skipUnless(_tools_present(), "bash/jq/sqlite3/git or pinned tag unavailable")
class CurateParityCase(unittest.TestCase):
    """Grounds `curate` stdout AND the written MEMORY-INDEX.json byte-for-byte against the
    pinned bash (the first file-parity harness), normalizing only the `updated_at`."""

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
        with open(os.path.join(proj, "MEMORY.md"), "w") as fh:
            fh.write("memory words here\n")
        with open(os.path.join(proj, "LEARNINGS.jsonl"), "w") as fh:
            fh.write('{"id":"L1","status":"current","topic":"beta"}\n'
                     '{"id":"L2","status":"current","topic":"alpha"}\n'
                     '{"id":"L3","status":"stale","topic":"alpha"}\n')
        return proj

    def _bash(self, root, argv):
        return subprocess.run(["bash", self.script, "curate", "--root", root] + argv,
                              stdout=subprocess.PIPE, text=True, check=True, env=_env()).stdout

    def _py(self, root, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(curate.run(["--root", root] + argv), 0)
        return out.getvalue()

    def test_stdout_parity(self):
        for argv in ([], ["--pretty"]):
            rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
            self.addCleanup(shutil.rmtree, rb, ignore_errors=True)
            self.addCleanup(shutil.rmtree, rp, ignore_errors=True)
            self._populate(rb)
            self._populate(rp)
            self.assertEqual(_strip_ts(self._bash(rb, argv)), _strip_ts(self._py(rp, argv)), "argv=%r" % argv)

    def test_written_index_file_parity(self):
        rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rb, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rp, ignore_errors=True)
        self._populate(rb)
        self._populate(rp)
        self._bash(rb, ["--write"])
        self._py(rp, ["--write"])
        with open(os.path.join(rb, ".kimiflow", "project", "MEMORY-INDEX.json")) as fh:
            fb = fh.read()
        with open(os.path.join(rp, ".kimiflow", "project", "MEMORY-INDEX.json")) as fh:
            fp = fh.read()
        self.assertEqual(_strip_ts(fb), _strip_ts(fp))
        self.assertTrue(os.path.isfile(os.path.join(rp, ".kimiflow", "project", "RECALL.sqlite")))


if __name__ == "__main__":
    unittest.main()
