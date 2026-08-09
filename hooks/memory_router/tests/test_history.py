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

from memory_router import history
from memory_router.__main__ import main

BASELINE_COMMIT = "a9bf10df56ed29c1022aab08d75caea7aa8ee219"
_ISO_ENV = {"HOME": "/tmp", "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/"}
_TS = "2026-06-29T00:00:00Z"
DOT = "\u00b7"  # U+00B7 MIDDLE DOT (never write the literal char in source).


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _env():
    return dict(_ISO_ENV, PATH=os.environ.get("PATH", ""))


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class HistoryRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        envp = mock.patch.dict(os.environ, _env(), clear=True)
        envp.start()
        self.addCleanup(envp.stop)
        tsp = mock.patch("memory_router.clock.iso_now", return_value=_TS)
        tsp.start()
        self.addCleanup(tsp.stop)

    def artifact(self, rel, body):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(body)

    def run_history(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = history.run(["--root", self.root] + argv)
        return code, out.getvalue(), err.getvalue()

    def obj(self, argv=None):
        code, out, _ = self.run_history(argv or [])
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_key_order(self):
        self.assertEqual(list(self.obj().keys()),
                         ["schema_version", "status", "query", "query_terms",
                          "path", "markdown_path", "written", "hits"])

    def test_no_query_recent_branch(self):
        self.artifact(".kimiflow/runs/demo/PLAN.md", "plan body\n")
        o = self.obj()
        self.assertEqual(o["query"], "recent")
        self.assertEqual(o["query_terms"], [])
        self.assertEqual(o["status"], "preview")
        self.assertEqual(o["written"], False)
        self.assertEqual([h["path"] for h in o["hits"]], [".kimiflow/runs/demo/PLAN.md"])
        self.assertNotIn("text", o["hits"][0])  # del(.text)

    def test_no_query_respects_max(self):
        for name in ("PLAN.md", "INTENT.md", "STATE.md"):
            self.artifact(".kimiflow/runs/demo/%s" % name, "body\n")
        self.assertEqual(len(self.obj(["--max", "2"])["hits"]), 2)

    def test_query_branch_filters_hits(self):
        self.artifact(".kimiflow/runs/demo/PLAN.md", "auth design\n")
        self.artifact(".kimiflow/runs/demo/INTENT.md", "unrelated\n")
        o = self.obj(["--query", "auth"])
        self.assertEqual([h["path"] for h in o["hits"]], [".kimiflow/runs/demo/PLAN.md"])
        self.assertEqual(o["query"], "auth")
        self.assertIn("auth", o["query_terms"])

    def test_default_max_is_10(self):
        # 11 artifacts, default max -> 10 hits in the recent branch.
        for i in range(11):
            self.artifact(".kimiflow/runs/r%02d/PLAN.md" % i, "body\n")
        self.assertEqual(len(self.obj()["hits"]), 10)

    def test_write_sets_status_and_written(self):
        self.artifact(".kimiflow/runs/demo/PLAN.md", "body\n")
        o = self.obj(["--write"])
        self.assertEqual(o["status"], "written")
        self.assertEqual(o["written"], True)

    def test_write_creates_json_md_and_usage(self):
        self.artifact(".kimiflow/runs/demo/PLAN.md", "auth body\n")
        self.run_history(["--query", "auth", "--write"])
        js = os.path.join(self.project, "RUN-HISTORY.json")
        md = os.path.join(self.project, "RUN-HISTORY.md")
        usage = os.path.join(self.project, "MEMORY-USAGE.json")
        self.assertTrue(os.path.isfile(js) and os.path.isfile(md) and os.path.isfile(usage))
        js_text = _read(js)
        self.assertTrue(js_text.endswith("}\n"))            # jq . trailing newline
        self.assertEqual(json.loads(js_text)["status"], "written")
        md_text = _read(md)
        self.assertTrue(md_text.startswith("# Run History Recall\n\nGenerated: %s\n\n" % _TS))
        self.assertIn("- [runs " + DOT + " demo/PLAN.md] auth body (.kimiflow/runs/demo/PLAN.md)\n", md_text)
        self.assertEqual(json.loads(_read(usage))["events"][-1]["kind"], "history")

    def test_no_write_no_files(self):
        self.run_history([])
        self.assertFalse(os.path.isfile(os.path.join(self.project, "RUN-HISTORY.json")))

    def test_bad_max(self):
        code, _, err = self.run_history(["--max", "abc"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: history --max must be a number\n")

    def test_empty_max_via_trailing_flag(self):
        code, _, err = self.run_history(["--max"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: history --max must be a number\n")

    def test_query_file_not_found(self):
        code, _, err = self.run_history(["--query-file", os.path.join(self.root, "nope.txt")])
        self.assertEqual(code, 2)
        self.assertIn("query file not found:", err)

    def test_query_file_first_120_lines(self):
        qf = os.path.join(self.root, "q.txt")
        with open(qf, "w", encoding="utf-8") as fh:
            fh.write("\n".join("line%d" % i for i in range(200)) + "\n")
        o = self.obj(["--query-file", qf])
        self.assertEqual(o["query"], "\n".join("line%d" % i for i in range(120)))

    def test_unknown_arg(self):
        code, _, err = self.run_history(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: history: unknown argument: --bogus\n")

    def test_markdown_hit_defaults(self):
        # A hit missing slug/artifact/summary/path uses the jq // defaults.
        obj = {"query": "q", "hits": [{}]}
        with mock.patch("memory_router.clock.iso_now", return_value=_TS):
            history.write_history_markdown(os.path.join(self.project, "H.md"), obj)
        self.assertIn("- [run " + DOT + " artifact]  ()\n", _read(os.path.join(self.project, "H.md")))

    def test_dispatch_registration(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            code = main(["history", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertIn('"schema_version":1', out.getvalue())


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", BASELINE_COMMIT + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _strip_md_ts(text):
    return re.sub(r"(Generated: ).*", r"\1<TS>", text)


def _strip_json_ts(text):
    for field in ("updated_at", "at", "last_used_at"):
        text = re.sub(r'("%s"\s*:\s*)"[^"]*"' % field, r'\1"<TS>"', text)
    return text


@unittest.skipUnless(_tools_present(), "bash/jq/git or pinned tag unavailable")
class HistoryParityCase(unittest.TestCase):
    """Grounds `history` stdout + the written RUN-HISTORY.json / RUN-HISTORY.md /
    MEMORY-USAGE.json byte-for-byte vs the pinned bash, normalizing only timestamps."""

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

    def _populate(self, root):
        runs = os.path.join(root, ".kimiflow", "runs", "demo")
        os.makedirs(os.path.join(runs, "findings"))
        for name, body in (("PLAN.md", "# Title\nauth plan body\n"),
                           ("STATE.md", "auth state body\n"),
                           ("INTENT.md", "unrelated body\n")):
            with open(os.path.join(runs, name), "w") as fh:
                fh.write(body)
        with open(os.path.join(runs, "findings", "f1.md"), "w") as fh:
            fh.write("auth finding\n")

    def _bash(self, root, argv):
        return subprocess.run(["bash", self.script, "history", "--root", root] + argv,
                              stdout=subprocess.PIPE, text=True, check=True, env=_env()).stdout

    def _py(self, root, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(history.run(["--root", root] + argv), 0)
        return out.getvalue()

    def _roots(self):
        rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rb, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rp, ignore_errors=True)
        self._populate(rb)
        self._populate(rp)
        return rb, rp

    def test_stdout_parity(self):
        for argv in (["--query", "auth"], [], ["--pretty"], ["--max", "1"],
                     ["--query", "auth", "--pretty"], ["--query", "nomatchzzz"]):
            rb, rp = self._roots()
            self.assertEqual(self._bash(rb, argv), self._py(rp, argv), "argv=%r" % argv)

    def test_written_files_parity(self):
        for argv in (["--query", "auth", "--write"], ["--write"]):
            rb, rp = self._roots()
            self._bash(rb, argv)
            self._py(rp, argv)
            proj = os.path.join(".kimiflow", "project")
            self.assertEqual(_strip_json_ts(_read(os.path.join(rb, proj, "RUN-HISTORY.json"))),
                             _strip_json_ts(_read(os.path.join(rp, proj, "RUN-HISTORY.json"))), argv)
            self.assertEqual(_strip_md_ts(_read(os.path.join(rb, proj, "RUN-HISTORY.md"))),
                             _strip_md_ts(_read(os.path.join(rp, proj, "RUN-HISTORY.md"))), argv)
            self.assertEqual(_strip_json_ts(_read(os.path.join(rb, proj, "MEMORY-USAGE.json"))),
                             _strip_json_ts(_read(os.path.join(rp, proj, "MEMORY-USAGE.json"))), argv)


if __name__ == "__main__":
    unittest.main()
