import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import status
from memory_router.__main__ import main

TAG = "kimiflow--v0.1.50"

# Isolate from the host's OBSIDIAN_API_KEY / KIMIFLOW_* and point detection at a dead
# port so the network probe deterministically fails (no real token, no real Obsidian).
_ISO_ENV = {"HOME": "/tmp", "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/"}


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _py_status(root, extra_env=None):
    env = dict(_ISO_ENV, PATH=os.environ.get("PATH", ""))
    if extra_env:
        env.update(extra_env)
    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
        code = status.run(["--root", root])
    return code, out.getvalue()


class StatusRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)

    def status(self, extra_env=None):
        code, out = _py_status(self.root, extra_env)
        self.assertEqual(code, 0)
        return json.loads(out)

    def write(self, name, text):
        with open(os.path.join(self.project, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_empty_top_level_key_order(self):
        s = self.status()
        self.assertEqual(list(s.keys()), [
            "schema_version", "present", "root", "paths", "memory", "user_profile",
            "learnings", "lifecycle", "usefulness", "usage", "economics",
            "global_efficiency", "proposals", "history", "recall_index", "provider",
            "vault", "curation",
        ])
        self.assertEqual(s["present"], False)
        self.assertEqual(s["root"], self.root)

    def test_present_true_with_any_file(self):
        self.write("MEMORY.md", "hello world\n")
        s = self.status()
        self.assertEqual(s["present"], True)
        self.assertEqual(s["memory"]["present"], True)

    def test_learnings_merge_keeps_summary_then_present_path(self):
        self.write("LEARNINGS.jsonl", '{"id":"L1","status":"current","topic":"a"}\n')
        learnings = self.status()["learnings"]
        # read_jsonl_summary keys first, then the appended present/path.
        self.assertEqual(list(learnings.keys())[-2:], ["present", "path"])
        self.assertEqual((learnings["present"], learnings["total"]), (True, 1))

    def test_curation_reasons_and_silent(self):
        self.write("LEARNINGS.jsonl",
                   '{"id":"L1","status":"stale","topic":"a"}\n'
                   '{"id":"L2","status":"superseded","topic":"b"}\n')
        s = self.status({"KIMIFLOW_MEMORY_CURATE_AFTER_LEARNINGS": "1"})
        self.assertIn("stale_learnings", s["curation"]["all_reasons"])
        self.assertIn("superseded_learnings", s["curation"]["all_reasons"])
        # many_learnings is silent (excluded from visible reasons).
        self.assertIn("many_learnings", s["curation"]["silent_reasons"])
        self.assertNotIn("many_learnings", s["curation"]["reasons"])

    def test_over_budget(self):
        self.write("MEMORY.md", "word " * 50)
        s = self.status({"KIMIFLOW_MEMORY_BUDGET": "10"})
        self.assertTrue(s["memory"]["over_budget"])
        self.assertIn("memory_over_budget", s["curation"]["all_reasons"])

    def test_unknown_arg_exit_2(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = status.run(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "memory-router: status: unknown argument: --bogus\n")

    def test_help_exit_0(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = status.run(["--help"])
        self.assertEqual(code, 0)
        self.assertTrue(err.getvalue().startswith("#!/usr/bin/env bash"))

    def test_dispatch_registration(self):
        out = io.StringIO()
        env = dict(_ISO_ENV, PATH=os.environ.get("PATH", ""))
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            code = main(["status", "--root", self.root])
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
class StatusParityCase(unittest.TestCase):
    """Grounds the Python `status` stdout byte-for-byte against the real Bash at the
    pinned tag. status is read-only, so bash and python share ONE root (the output's
    `root` field is the absolute path, identical for both). Run under an isolated env
    with a dead detection URL so the network probe fails deterministically."""

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

    def _env(self):
        return dict(_ISO_ENV, PATH=os.environ.get("PATH", ""))

    def _bash(self, root, argv):
        return subprocess.run(
            ["bash", self.script, "status", "--root", root] + argv,
            stdout=subprocess.PIPE, text=True, check=True, env=self._env(),
        ).stdout

    def _py(self, root, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self._env(), clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(status.run(["--root", root] + argv), 0)
        return out.getvalue()

    def assert_parity(self, argv, populate=False):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        if populate:
            proj = os.path.join(root, ".kimiflow", "project")
            os.makedirs(proj)
            with open(os.path.join(proj, "MEMORY.md"), "w") as fh:
                fh.write("word " * 30)
            with open(os.path.join(proj, "LEARNINGS.jsonl"), "w") as fh:
                fh.write('{"id":"L1","status":"current","topic":"a","last_verified":"2026-06-01"}\n'
                         '{"id":"L2","status":"stale","topic":"b"}\n')
            with open(os.path.join(proj, "PROPOSALS.jsonl"), "w") as fh:
                fh.write('{"id":"P1","type":"new_learning","status":"pending"}\n')
        self.assertEqual(self._bash(root, argv), self._py(root, argv), "argv=%r" % argv)

    def test_empty(self):
        self.assert_parity([])

    def test_empty_pretty(self):
        self.assert_parity(["--pretty"])

    def test_populated(self):
        self.assert_parity([])

    def test_populated_pretty(self):
        self.assert_parity(["--pretty"], populate=True)


if __name__ == "__main__":
    unittest.main()
