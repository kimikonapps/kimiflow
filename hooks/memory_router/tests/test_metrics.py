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

from memory_router import metrics, summaries
from memory_router.__main__ import main

BASELINE_COMMIT = "a9bf10df56ed29c1022aab08d75caea7aa8ee219"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class MetricsRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.khome = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.khome, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        self.gdir = os.path.join(self.khome, "metrics")
        os.makedirs(self.gdir)
        envp = mock.patch.dict(os.environ, {"HOME": "/tmp", "KIMIFLOW_HOME": self.khome,
                                            "PATH": os.environ.get("PATH", "")}, clear=True)
        envp.start()
        self.addCleanup(envp.stop)

    def run_metrics(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = metrics.run(argv)
        return code, out.getvalue(), err.getvalue()

    def obj(self, argv):
        code, out, _ = self.run_metrics(argv)
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_default_spreads_usage_keys_plus_nested(self):
        with open(os.path.join(self.project, "MEMORY-USAGE.json"), "w") as fh:
            fh.write(json.dumps({"schema_version": 1, "items": {}, "events": []}))
        o = self.obj(["--root", self.root])
        usage = summaries.usage_summary_json(os.path.join(self.project, "MEMORY-USAGE.json"))
        # usage keys spread at top level...
        for k in usage:
            self.assertIn(k, o)
        # ...plus the three nested objects.
        self.assertEqual(o["usage"], usage)
        self.assertIn("run_economics", o)
        self.assertIn("global_efficiency", o)
        # jq `+` key order: usage keys first, then usage/run_economics/global_efficiency.
        self.assertEqual(list(o.keys())[-3:], ["usage", "run_economics", "global_efficiency"])

    def test_global_only_equals_efficiency_summary(self):
        self.assertEqual(self.obj(["--global"]), summaries.global_efficiency_summary_json())

    def test_global_purge_removes_existing_files(self):
        open(os.path.join(self.gdir, "token-economics.jsonl"), "w").close()
        open(os.path.join(self.gdir, "salt"), "w").close()
        o = self.obj(["--global-purge"])
        self.assertEqual(o, {"schema_version": 1, "status": "purged",
                             "path": "~/.kimiflow/metrics/token-economics.jsonl",
                             "removed": True, "salt_removed": True})
        self.assertFalse(os.path.exists(os.path.join(self.gdir, "token-economics.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.gdir, "salt")))

    def test_global_purge_absent_files_false(self):
        o = self.obj(["--global-purge"])
        self.assertEqual(o["removed"], False)
        self.assertEqual(o["salt_removed"], False)
        self.assertEqual(o["status"], "purged")

    def test_purge_takes_precedence_over_global(self):
        open(os.path.join(self.gdir, "token-economics.jsonl"), "w").close()
        o = self.obj(["--global", "--global-purge"])
        self.assertEqual(o["status"], "purged")

    def test_unknown_arg(self):
        code, _, err = self.run_metrics(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: metrics: unknown argument: --bogus\n")

    def test_dispatch_registration(self):
        # NOTE: the metrics default output spreads usage_summary keys (no top-level
        # schema_version); it always carries the three nested objects.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["metrics", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertIn('"run_economics":', out.getvalue())
        self.assertIn('"global_efficiency":', out.getvalue())


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", BASELINE_COMMIT + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


@unittest.skipUnless(_tools_present(), "bash/jq/git or pinned tag unavailable")
class MetricsParityCase(unittest.TestCase):
    """Grounds `metrics` stdout byte-for-byte vs the pinned bash, with KIMIFLOW_HOME
    sandboxed to a temp dir so the global token-economics file is isolated."""

    @classmethod
    def setUpClass(cls):
        src = subprocess.run(
            ["git", "-C", _repo_root(), "show", BASELINE_COMMIT + ":hooks/memory-router.sh"],
            stdout=subprocess.PIPE, check=True,
        ).stdout
        fd, cls.script = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "wb") as fh:
            fh.write(src)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.script)

    def _env(self, khome):
        return {"HOME": "/tmp", "KIMIFLOW_HOME": khome, "PATH": os.environ.get("PATH", "")}

    def _populate(self, root, khome):
        proj = os.path.join(root, ".kimiflow", "project")
        os.makedirs(proj)
        with open(os.path.join(proj, "MEMORY-USAGE.json"), "w") as fh:
            fh.write(json.dumps({
                "schema_version": 1,
                "items": {"learning:L1": {"kind": "learning", "use_count": 2}},
                "events": [{"kind": "recall", "at": "2026-06-29T00:00:00Z",
                            "hit_count": 1, "estimated_tokens": 5}],
            }))
        with open(os.path.join(proj, "MEMORY-ECONOMICS.jsonl"), "w") as fh:
            fh.write('{"event":"recall","tokens_loaded":120,"tokens_avoided":900}\n')
        gdir = os.path.join(khome, "metrics")
        os.makedirs(gdir)
        with open(os.path.join(gdir, "token-economics.jsonl"), "w") as fh:
            fh.write('{"project_id":"abc","tokens_loaded":120,"tokens_avoided":900,"hit_count":1}\n')

    def _bash(self, khome, argv):
        return subprocess.run(["bash", self.script, "metrics"] + argv,
                              stdout=subprocess.PIPE, text=True, check=True,
                              env=self._env(khome)).stdout

    def _py(self, khome, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self._env(khome), clear=True), \
                contextlib.redirect_stdout(out):
            self.assertEqual(metrics.run(argv), 0)
        return out.getvalue()

    def _roots(self):
        rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
        kb, kp = tempfile.mkdtemp(), tempfile.mkdtemp()
        for d in (rb, rp, kb, kp):
            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self._populate(rb, kb)
        self._populate(rp, kp)
        return (rb, kb), (rp, kp)

    def test_read_parity(self):
        # {root} is substituted with each side's absolute root (output paths are relative,
        # so abs-vs-symlink root does not affect the bytes).
        for tail in (["--root", "{root}"], ["--global"], ["--root", "{root}", "--pretty"],
                     ["--global", "--pretty"]):
            (rb, kb), (rp, kp) = self._roots()
            argb = [x.replace("{root}", rb) for x in tail]
            argp = [x.replace("{root}", rp) for x in tail]
            self.assertEqual(self._bash(kb, argb), self._py(kp, argp), "tail=%r" % tail)

    def test_purge_parity_and_effect(self):
        (rb, kb), (rp, kp) = self._roots()
        b = self._bash(kb, ["--global-purge"])
        p = self._py(kp, ["--global-purge"])
        self.assertEqual(b, p)
        # both deleted the global file on their own KIMIFLOW_HOME
        self.assertFalse(os.path.exists(os.path.join(kb, "metrics", "token-economics.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(kp, "metrics", "token-economics.jsonl")))


if __name__ == "__main__":
    unittest.main()
