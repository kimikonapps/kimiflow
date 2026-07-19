import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import runs, writes
from memory_router.__main__ import main

TAG = "kimiflow--v0.1.50"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class ResolveRunDirCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_empty_run_writes_stderr_returns_empty(self):
        # Bash subshell-die quirk: message to stderr, EMPTY return (exit 2 discarded).
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = runs.resolve_run_dir(self.root, "")
        self.assertEqual(result, "")
        self.assertEqual(err.getvalue(), "memory-router: run path required\n")

    def test_missing_dir_writes_stderr_returns_empty(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = runs.resolve_run_dir(self.root, "nope/here")
        self.assertEqual(result, "")
        self.assertEqual(err.getvalue(), "memory-router: run directory not found: %s/nope/here\n" % self.root)

    def test_relative_prefixed_and_resolved(self):
        os.makedirs(os.path.join(self.root, "runs", "demo"))
        self.assertEqual(runs.resolve_run_dir(self.root, "runs/demo"),
                         os.path.abspath(os.path.join(self.root, "runs", "demo")))

    def test_absolute_kept(self):
        d = os.path.join(self.root, "abs")
        os.makedirs(d)
        self.assertEqual(runs.resolve_run_dir(self.root, d), os.path.abspath(d))

    def test_private_pinned_cwd_does_not_follow_replaced_run_name(self):
        run_dir = os.path.join(self.root, "run")
        displaced = os.path.join(self.root, "run.owned")
        outside = os.path.join(self.root, "outside")
        os.mkdir(run_dir)
        os.mkdir(outside)
        original_cwd = os.getcwd()
        try:
            os.chdir(run_dir)
            info = os.stat(".")
            os.rename(run_dir, displaced)
            os.symlink(outside, run_dir)
            with mock.patch.dict(os.environ, {
                "KIMIFLOW_PINNED_RUN_CWD": "1",
                "KIMIFLOW_PINNED_RUN_DEVICE": str(info.st_dev),
                "KIMIFLOW_PINNED_RUN_INODE": str(info.st_ino),
            }):
                self.assertEqual(runs.resolve_run_dir(self.root, "."), ".")
        finally:
            os.chdir(original_cwd)


class VerifyRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        self.run_dir = os.path.join(self.root, ".kimiflow", "runs", "demo")
        os.makedirs(self.run_dir)
        envp = mock.patch.dict(os.environ, {"HOME": "/tmp", "PATH": os.environ.get("PATH", "")}, clear=True)
        envp.start()
        self.addCleanup(envp.stop)

    def write_review(self, text):
        with open(os.path.join(self.run_dir, "LEARNING-REVIEW.md"), "w", encoding="utf-8") as fh:
            fh.write(text)

    def run_verify(self, run="ldir"):
        # run path relative to root
        rel = os.path.relpath(self.run_dir, self.root)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runs.run(["--root", self.root, "--run", rel])
        return code, out.getvalue(), err.getvalue()

    def test_missing_review(self):
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertEqual(out, "LEARNING_REVIEW\tCLOSED\treason=missing_review\tpath=.kimiflow/runs/demo/LEARNING-REVIEW.md\n")

    def test_skipped_with_reason_open(self):
        self.write_review("Status: skipped\nSkip reason: trivial run\n")
        code, out, _ = self.run_verify()
        self.assertEqual(code, 0)
        self.assertEqual(out, "LEARNING_REVIEW\tOPEN\tstatus=skipped\treason=trivial run\tpath=.kimiflow/runs/demo/LEARNING-REVIEW.md\n")

    def test_skipped_missing_reason_closed(self):
        self.write_review("Status: skipped\n")
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertIn("reason=missing_skip_reason", out)

    def test_invalid_status(self):
        self.write_review("Status: bogus\n")
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertIn("reason=invalid_status\tstatus=bogus", out)

    def test_no_status_is_missing(self):
        self.write_review("nothing here\n")
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertIn("reason=invalid_status\tstatus=missing", out)

    def test_recorded_no_ids(self):
        self.write_review("Status: recorded\n")
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertIn("reason=missing_recorded_ids", out)

    def test_recorded_missing_learnings(self):
        self.write_review("Status: recorded\nRecorded: learn_x\n")
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertIn("reason=missing_learnings", out)

    def test_recorded_ids_not_present(self):
        self.write_review("Status: recorded\nRecorded: learn_missing\n")
        with open(os.path.join(self.project, "LEARNINGS.jsonl"), "w") as fh:
            fh.write('{"id":"learn_other","status":"current"}\n')
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertIn("reason=recorded_ids_missing_or_not_current\tids=learn_missing", out)

    def _record_real(self):
        # Use the ported writer to create a real current row with computed fingerprints.
        with open(os.path.join(self.root, "a.py"), "w") as fh:
            fh.write("print(1)\n")
        rid = writes.append_learning_row(self.root, "learning", "project", "topic",
                                         "a real summary", ["a.py"], "medium", "normal", "current")
        return rid

    def test_recorded_fresh_open(self):
        rid = self._record_real()
        self.write_review("Status: recorded\nRecorded: %s\n" % rid)
        code, out, _ = self.run_verify()
        self.assertEqual(code, 0)
        self.assertIn("LEARNING_REVIEW\tOPEN\tstatus=recorded\tfreshness=current", out)

    def test_recorded_stale_when_evidence_changes(self):
        rid = self._record_real()
        self.write_review("Status: recorded\nRecorded: %s\n" % rid)
        with open(os.path.join(self.root, "a.py"), "w") as fh:
            fh.write("print(2)  # changed\n")
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertIn("reason=evidence_stale", out)
        self.assertIn("%s:evidence_changed_or_missing" % rid, out)

    def test_recorded_missing_fingerprints(self):
        self.write_review("Status: recorded\nRecorded: learn_a\n")
        with open(os.path.join(self.project, "LEARNINGS.jsonl"), "w") as fh:
            fh.write('{"id":"learn_a","status":"current","evidence":["a.py"],"evidence_fingerprints":[]}\n')
        code, out, _ = self.run_verify()
        self.assertEqual(code, 1)
        self.assertIn("reason=evidence_stale", out)
        self.assertIn("learn_a:missing_evidence_fingerprints", out)

    def test_run_path_required(self):
        # Bash quirk: resolve_run_dir die is swallowed by $(), so run_dir="" -> review
        # "/LEARNING-REVIEW.md" -> missing_review + exit 1, with the message on stderr.
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runs.run(["--root", self.root])
        self.assertEqual(code, 1)
        self.assertEqual(err.getvalue(), "memory-router: run path required\n")
        self.assertEqual(out.getvalue(),
                         "LEARNING_REVIEW\tCLOSED\treason=missing_review\tpath=/LEARNING-REVIEW.md\n")

    def test_run_not_found(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runs.run(["--root", self.root, "--run", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("run directory not found:", err.getvalue())
        self.assertIn("reason=missing_review\tpath=/LEARNING-REVIEW.md", out.getvalue())

    def test_unknown_arg(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = runs.run(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err.getvalue(), "memory-router: verify-run: unknown argument: --bogus\n")

    def test_dispatch_registration(self):
        self.write_review("Status: skipped\nSkip reason: x\n")
        rel = os.path.relpath(self.run_dir, self.root)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["verify-run", "--root", self.root, "--run", rel])
        self.assertEqual(code, 0)
        self.assertIn("LEARNING_REVIEW\tOPEN", out.getvalue())


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "git", "shasum")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


@unittest.skipUnless(_tools_present(), "bash/jq/git/shasum or pinned tag unavailable")
class VerifyRunParityCase(unittest.TestCase):
    """Grounds `verify-run` stdout AND exit code vs the pinned bash. Fingerprint-dependent
    cases use the SAME bytes on both roots (built once with the ported writer, then copied)
    so both sides recompute identical fingerprints."""

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
        return {"HOME": "/tmp", "PATH": os.environ.get("PATH", "")}

    def _bash(self, root, rel):
        p = subprocess.run(["bash", self.script, "verify-run", "--root", root, "--run", rel],
                           stdout=subprocess.PIPE, text=True, env=self._env())
        return p.returncode, p.stdout

    def _py(self, root, rel):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self._env(), clear=True), contextlib.redirect_stdout(out):
            code = runs.run(["--root", root, "--run", rel])
        return code, out.getvalue()

    def _mkrun(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        os.makedirs(os.path.join(root, ".kimiflow", "project"))
        rundir = os.path.join(root, ".kimiflow", "runs", "demo")
        os.makedirs(rundir)
        return root, rundir, ".kimiflow/runs/demo"

    def test_resolve_run_dir_quirk_parity(self):
        # Bash resolve_run_dir die is swallowed by `$( )` -> empty run_dir -> missing_review
        # + exit 1. Ground (exit, stdout) for no --run and a non-existent --run.
        root, _, _ = self._mkrun()
        for extra in ([], ["--run", "does/not/exist"]):
            b = subprocess.run(["bash", self.script, "verify-run", "--root", root] + extra,
                               stdout=subprocess.PIPE, text=True, env=self._env())
            out = io.StringIO()
            with mock.patch.dict(os.environ, self._env(), clear=True), contextlib.redirect_stdout(out):
                code = runs.run(["--root", root] + extra)
            self.assertEqual((b.returncode, b.stdout), (code, out.getvalue()), "extra=%r" % extra)

    def test_simple_branches_parity(self):
        cases = [
            None,                                          # missing review
            "Status: skipped\nSkip reason: trivial\n",     # OPEN skipped
            "Status: skipped\n",                           # CLOSED missing_skip_reason
            "Status: bogus\n",                             # invalid_status
            "no status line\n",                            # status=missing
            "Status: recorded\n",                          # missing_recorded_ids
            "Status: recorded\nRecorded: learn_x\n",       # missing_learnings
        ]
        for review in cases:
            rootb, rdb, rel = self._mkrun()
            rootp, rdp, _ = self._mkrun()
            if review is not None:
                for rd in (rdb, rdp):
                    with open(os.path.join(rd, "LEARNING-REVIEW.md"), "w") as fh:
                        fh.write(review)
            self.assertEqual(self._bash(rootb, rel), self._py(rootp, rel), "review=%r" % review)

    def test_fingerprint_cases_parity(self):
        # Build identical state with the ported writer on root P, mirror bytes to root B.
        rootp, rdp, rel = self._mkrun()
        with open(os.path.join(rootp, "a.py"), "w") as fh:
            fh.write("print(1)\n")
        rid = writes.append_learning_row(rootp, "learning", "project", "t", "real summary",
                                         ["a.py"], "medium", "normal", "current")
        review = "Status: recorded\nRecorded: %s\n" % rid
        with open(os.path.join(rdp, "LEARNING-REVIEW.md"), "w") as fh:
            fh.write(review)
        rootb, rdb, _ = self._mkrun()
        shutil.rmtree(os.path.join(rootb, ".kimiflow"))
        shutil.copytree(os.path.join(rootp, ".kimiflow"), os.path.join(rootb, ".kimiflow"))
        shutil.copy(os.path.join(rootp, "a.py"), os.path.join(rootb, "a.py"))
        # fresh
        self.assertEqual(self._bash(rootb, rel), self._py(rootp, rel))
        # stale: change evidence identically on both
        for rt in (rootb, rootp):
            with open(os.path.join(rt, "a.py"), "w") as fh:
                fh.write("print(2)  # changed\n")
        self.assertEqual(self._bash(rootb, rel), self._py(rootp, rel))


if __name__ == "__main__":
    unittest.main()
