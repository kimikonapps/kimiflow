import contextlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import record, writes
from memory_router.__main__ import main

TAG = "kimiflow--v0.1.50"
_ISO_ENV = {"HOME": "/tmp", "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/"}


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _env():
    return dict(_ISO_ENV, PATH=os.environ.get("PATH", ""))


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, _env(), clear=True), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = record.run(argv)
    return code, out.getvalue(), err.getvalue()


class RecordRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def project(self, name):
        return os.path.join(self.root, ".kimiflow", "project", name)

    def test_records_project_and_prints(self):
        code, out, err = _run(["--root", self.root, "--summary",
                               "the build uses esbuild for bundling in this project here",
                               "--topic", "build", "--evidence", "src/x.ts:3"])
        self.assertEqual((code, err), (0, ""))
        self.assertRegex(out, r"^RECORDED\t\.kimiflow/project/LEARNINGS\.jsonl\tlearn_\d{8}_build_\d+\n$")
        self.assertTrue(os.path.isfile(self.project("LEARNINGS.jsonl")))
        self.assertTrue(os.path.isfile(self.project("MEMORY.md")))
        self.assertTrue(os.path.isfile(self.project("MEMORY-INDEX.json")))

    def test_user_scope_targets_user_rows(self):
        code, out, _ = _run(["--root", self.root, "--summary",
                             "the user prefers concise german replies in this project",
                             "--topic", "style", "--scope", "user", "--evidence", "NOT VERIFIED"])
        self.assertEqual(code, 0)
        self.assertIn("RECORDED\t.kimiflow/project/USER.jsonl\t", out)
        self.assertTrue(os.path.isfile(self.project("USER.jsonl")))
        self.assertTrue(os.path.isfile(self.project("USER.md")))

    def test_security_gate_blocks_exit_1(self):
        # record formats the stderr line + returns 1 when append raises SecurityGateError
        # (the gate logic itself is covered by the writes tests).
        with mock.patch("memory_router.record.writes.append_learning_row",
                        side_effect=writes.SecurityGateError(["secret_detected", "hidden_unicode"])):
            code, out, err = _run(["--root", self.root, "--summary", "x leaks a token here now",
                                   "--topic", "t", "--evidence", "a.txt"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, "memory-router: memory security gate closed: secret_detected,hidden_unicode\n")

    def test_missing_summary(self):
        code, out, err = _run(["--root", self.root, "--topic", "t", "--evidence", "a"])
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "memory-router: record requires --summary\n")

    def test_missing_topic(self):
        code, _, err = _run(["--root", self.root, "--summary", "hello world here", "--evidence", "a"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: record requires --topic\n")

    def test_missing_evidence(self):
        code, _, err = _run(["--root", self.root, "--summary", "hello world here", "--topic", "t"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: record requires at least one --evidence\n")

    def test_unknown_arg(self):
        code, _, err = _run(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: record: unknown argument: --bogus\n")

    def test_dispatch_registration(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            code = main(["record", "--root", self.root, "--summary",
                         "always run shellcheck before committing hook changes here",
                         "--topic", "ci", "--evidence", "h.sh:1"])
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("RECORDED\t"))


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "sqlite3", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


@unittest.skipUnless(_tools_present(), "bash/jq/sqlite3/git or pinned tag unavailable")
class RecordParityCase(unittest.TestCase):
    """Grounds record's own outputs -- the appended LEARNINGS.jsonl row and the RECORDED
    line -- byte-for-byte against the pinned bash, normalizing only the random id suffix.
    (MEMORY.md/USER.md inherit the user-blessed body-format divergence of
    write_bounded_memory; see spec 12.)"""

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

    def _record(self, args):
        rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rb, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rp, ignore_errors=True)
        b = subprocess.run(["bash", self.script, "record"] + args + ["--root", rb],
                           stdout=subprocess.PIPE, text=True, check=True, env=_env()).stdout
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(record.run(args + ["--root", rp]), 0)
        p = out.getvalue()
        bid = b.rstrip("\n").split("\t")[-1]
        pid = p.rstrip("\n").split("\t")[-1]
        with open(os.path.join(rb, ".kimiflow", "project", "LEARNINGS.jsonl")) as fh:
            lb = fh.read().replace(bid, "ID")
        with open(os.path.join(rp, ".kimiflow", "project", "LEARNINGS.jsonl")) as fh:
            lp = fh.read().replace(pid, "ID")
        return b.replace(bid, "ID"), p.replace(pid, "ID"), lb, lp

    def test_project_row_and_recorded_parity(self):
        b, p, lb, lp = self._record(["--summary", "the build uses esbuild for bundling in this project here",
                                     "--topic", "build", "--evidence", "src/x.ts:3", "--evidence", "README.md"])
        self.assertEqual(b, p)     # RECORDED line
        self.assertEqual(lb, lp.replace(',"maturity":"probationary"', ""))

    def test_kind_confidence_sensitivity_parity(self):
        b, p, lb, lp = self._record(["--summary", "always run shellcheck before committing hook changes here",
                                     "--topic", "ci", "--kind", "project_rule_confirmed",
                                     "--confidence", "high", "--sensitivity", "normal", "--evidence", "h.sh:5"])
        self.assertEqual(b, p)
        self.assertEqual(lb, lp.replace(',"maturity":"probationary"', ""))


if __name__ == "__main__":
    unittest.main()
