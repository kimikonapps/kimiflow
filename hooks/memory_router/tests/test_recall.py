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

from memory_router import recall, recall_index, usage_metrics
from memory_router.__main__ import main

TAG = "kimiflow--v0.1.50"
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


class TermsFromQueryCase(unittest.TestCase):
    def t(self, q):
        return recall.terms_json_from_query(q)

    def test_basic_split_lower_and_order(self):
        self.assertEqual(self.t("Build Auth Token"), ["build", "auth", "token"])

    def test_drops_short_and_stopwords(self):
        # "the"/"and" are stopwords; "ab" is too short; order = first occurrence.
        self.assertEqual(self.t("the auth and ab token"), ["auth", "token"])

    def test_first_occurrence_dedup_not_sorted(self):
        self.assertEqual(self.t("zoo apple zoo apple"), ["zoo", "apple"])

    def test_underscore_and_hyphen_kept_as_term_chars(self):
        self.assertEqual(self.t("foo_bar baz-qux"), ["foo_bar", "baz-qux"])

    def test_non_ascii_is_a_separator_ascii_lower_only(self):
        # ascii_lower leaves non-ASCII; the split treats it as a separator.
        self.assertEqual(self.t("caf\u00e9 latte"), ["caf", "latte"])

    def test_empty_terms_fallback_to_ascii_lower_whole_query(self):
        self.assertEqual(self.t("A B"), ["a b"])  # both tokens len<3 -> fallback
        self.assertEqual(self.t("the und"), ["the und"])  # all stopwords -> fallback

    def test_head_30_cap(self):
        q = " ".join("term%02d" % i for i in range(40))
        self.assertEqual(len(self.t(q)), 30)


class JsonlHitsCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "LEARNINGS.jsonl")

    def write(self, rows):
        with open(self.path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")

    def test_missing_file_empty(self):
        self.assertEqual(recall.jsonl_hits(self.path, ["x"], 5, "summary"), [])

    def test_status_filter_and_field_match(self):
        self.write([
            {"id": "L1", "summary": "auth flow", "status": "current"},
            {"id": "L2", "summary": "auth flow", "status": "stale"},      # dropped: not current
            {"id": "L3", "summary": "unrelated", "status": "current"},    # dropped: no match
            {"id": "L4", "summary": "AUTH again"},                        # status defaults current
        ])
        hits = recall.jsonl_hits(self.path, ["auth"], 5, "summary")
        self.assertEqual([h["id"] for h in hits], ["L1", "L4"])

    def test_evidence_array_joined_for_match(self):
        self.write([{"id": "L1", "summary": "x", "evidence": ["src/auth.py", "b"]}])
        hits = recall.jsonl_hits(self.path, ["auth.py"], 5, "summary,evidence")
        self.assertEqual([h["id"] for h in hits], ["L1"])

    def test_max_cap(self):
        self.write([{"id": "L%d" % i, "summary": "auth"} for i in range(10)])
        self.assertEqual(len(recall.jsonl_hits(self.path, ["auth"], 3, "summary")), 3)

    def test_max_zero_returns_empty(self):
        self.write([{"id": "L1", "summary": "auth"}])
        self.assertEqual(recall.jsonl_hits(self.path, ["auth"], 0, "summary"), [])


class RunArtifactCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel, text):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_state_md_included_unlike_index_builder(self):
        self.write(".kimiflow/runs/demo/STATE.md", "state body\n")
        self.write(".kimiflow/runs/demo/NOTES.md", "excluded\n")
        rows = recall_index.run_artifact_rows_json(self.root)
        self.assertEqual([r["path"] for r in rows], [".kimiflow/runs/demo/STATE.md"])
        self.assertEqual(rows[0]["title"], "runs " + DOT + " demo/STATE.md")
        self.assertEqual(rows[0]["slug"], "runs")
        self.assertEqual(rows[0]["artifact"], "demo/STATE.md")

    def test_summary_skips_blank_heading_fence_then_collapses(self):
        # Bash awk skips a leading blank, a `#` heading, and a line STARTING with ``` (it
        # does NOT track fence state), then takes the first real line, collapsing whitespace.
        self.write(".kimiflow/runs/demo/PLAN.md",
                   "   \n# Heading\n```\nReal   summary  here\n")
        rows = recall_index.run_artifact_rows_json(self.root)
        self.assertEqual(rows[0]["summary"], "Real summary here")

    def test_prune_project_and_sort(self):
        self.write(".kimiflow/runs/b/PLAN.md", "b\n")
        self.write(".kimiflow/runs/a/PLAN.md", "a\n")
        self.write(".kimiflow/project/PLAN.md", "pruned\n")
        rows = recall_index.run_artifact_rows_json(self.root)
        self.assertEqual([r["path"] for r in rows],
                         [".kimiflow/runs/a/PLAN.md", ".kimiflow/runs/b/PLAN.md"])

    def test_missing_kimiflow_empty(self):
        self.assertEqual(recall_index.run_artifact_rows_json(self.root), [])

    def test_hits_filter_cap_and_drop_text(self):
        self.write(".kimiflow/runs/demo/PLAN.md", "auth design\n")
        self.write(".kimiflow/runs/demo/INTENT.md", "unrelated\n")
        hits = recall_index.run_artifact_hits_json(self.root, ["auth"], 5)
        self.assertEqual([h["path"] for h in hits], [".kimiflow/runs/demo/PLAN.md"])
        self.assertNotIn("text", hits[0])
        self.assertEqual(list(hits[0].keys()),
                         ["kind", "slug", "artifact", "path", "ref", "title", "summary"])


class RecallJsonCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        p = mock.patch.dict(os.environ, _env(), clear=True)
        p.start()
        self.addCleanup(p.stop)

    def write(self, name, text):
        with open(os.path.join(self.project, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def obj(self, query="auth", max_hits=5):
        return recall.recall_json(self.root, query, max_hits)

    def test_key_order(self):
        o = self.obj()
        self.assertEqual(list(o.keys()),
                         ["schema_version", "query", "query_terms", "token_budget",
                          "sources", "explanation", "omitted"])
        self.assertEqual(list(o["sources"].keys()),
                         ["memory", "user_profile", "learnings", "facts", "index", "history"])
        self.assertEqual(list(o["explanation"].keys()),
                         ["reason_codes", "included_sources", "omitted_sources", "hit_counts"])

    def test_memory_missing_and_user_missing(self):
        o = self.obj()
        self.assertEqual(o["sources"]["memory"]["status"], "missing")
        self.assertEqual(o["sources"]["user_profile"]["status"], "missing")
        self.assertIn("MEMORY.md missing", o["omitted"])
        self.assertIn("USER.md missing", o["omitted"])
        self.assertIn("memory_missing", o["explanation"]["reason_codes"])

    def test_memory_included_content_and_budget(self):
        self.write("MEMORY.md", "alpha beta\n")
        o = self.obj()
        self.assertEqual(o["sources"]["memory"]["status"], "included")
        self.assertEqual(o["sources"]["memory"]["content"], "alpha beta")
        self.assertEqual(o["token_budget"], 900)
        self.assertIn("always_on_included", o["explanation"]["reason_codes"])

    def test_memory_over_budget(self):
        self.write("MEMORY.md", "alpha beta gamma\n")
        with mock.patch.dict(os.environ, {"KIMIFLOW_MEMORY_BUDGET": "2"}):
            o = self.obj()
        self.assertEqual(o["sources"]["memory"]["status"], "omitted_over_budget")
        self.assertEqual(o["sources"]["memory"]["content"], "")
        self.assertEqual(o["token_budget"], 2)
        self.assertIn("MEMORY.md omitted: over budget", o["omitted"])

    def test_user_budget_default_500(self):
        self.assertEqual(self.obj()["sources"]["user_profile"]["budget"], 500)

    def test_sed_line_caps(self):
        self.write("MEMORY.md", "\n".join("m%d" % i for i in range(200)) + "\n")
        self.write("USER.md", "\n".join("u%d" % i for i in range(200)) + "\n")
        with mock.patch.dict(os.environ, {"KIMIFLOW_MEMORY_BUDGET": "9999",
                                          "KIMIFLOW_USER_MEMORY_BUDGET": "9999"}):
            o = self.obj()
        self.assertEqual(o["sources"]["memory"]["content"].split("\n"),
                         ["m%d" % i for i in range(160)])
        self.assertEqual(o["sources"]["user_profile"]["content"].split("\n"),
                         ["u%d" % i for i in range(120)])

    def test_index_status_missing_then_available_no_hits(self):
        # No RECALL.sqlite + fts5 available -> missing.
        self.assertEqual(self.obj()["sources"]["index"]["status"], "missing")
        # An existing (empty-of-hits) RECALL.sqlite -> available_no_hits.
        recall_index.build_recall_index(self.root, recall_index.recall_db_path(self.root))
        self.assertEqual(self.obj("zzznomatch")["sources"]["index"]["status"], "available_no_hits")

    def test_index_status_unavailable_when_no_fts5(self):
        with mock.patch("memory_router.recall_index.fts5_available", return_value=False):
            self.assertEqual(self.obj()["sources"]["index"]["status"], "unavailable")

    def test_index_status_used(self):
        self.write("LEARNINGS.jsonl", '{"id":"L1","status":"current","topic":"auth","summary":"auth flow"}\n')
        recall_index.build_recall_index(self.root, recall_index.recall_db_path(self.root))
        o = self.obj("auth")
        self.assertEqual(o["sources"]["index"]["status"], "used")
        self.assertGreater(o["sources"]["index"]["count"], 0)
        self.assertIn("fts_index_hits", o["explanation"]["reason_codes"])

    def test_learning_and_fact_hits_and_counts(self):
        self.write("LEARNINGS.jsonl",
                   '{"id":"L1","status":"current","summary":"auth flow"}\n'
                   '{"id":"L2","status":"stale","summary":"auth flow"}\n')
        self.write("FACTS.jsonl", '{"kind":"module","area":"core","path":"auth.py","summary":"the auth module"}\n')
        o = self.obj("auth")
        self.assertEqual(o["sources"]["learnings"]["count"], 1)
        self.assertEqual(o["sources"]["facts"]["count"], 1)
        self.assertEqual(o["explanation"]["hit_counts"]["total"],
                         o["sources"]["learnings"]["count"] + o["sources"]["facts"]["count"]
                         + o["sources"]["index"]["count"] + o["sources"]["history"]["count"])
        self.assertIn("local_recall_hits", o["explanation"]["reason_codes"])
        self.assertIn("project_map_fact_hits", o["explanation"]["reason_codes"])

    def test_no_recall_hits_reason(self):
        self.assertIn("no_recall_hits", self.obj("nomatchxyz")["explanation"]["reason_codes"])

    def test_history_hits_and_status(self):
        full = os.path.join(self.root, ".kimiflow", "runs", "demo", "PLAN.md")
        os.makedirs(os.path.dirname(full))
        with open(full, "w", encoding="utf-8") as fh:
            fh.write("auth design notes\n")
        o = self.obj("auth")
        self.assertEqual(o["sources"]["history"]["status"], "used")
        self.assertEqual(o["sources"]["history"]["count"], 1)
        self.assertIn("history_hits", o["explanation"]["reason_codes"])


class RecallRunCase(unittest.TestCase):
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

    def run_recall(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = recall.run(["--root", self.root] + argv)
        return code, out.getvalue(), err.getvalue()

    def test_requires_query(self):
        code, _, err = self.run_recall([])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: recall requires --query or --query-file\n")

    def test_bad_max(self):
        code, _, err = self.run_recall(["--query", "x", "--max", "abc"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: recall --max must be a number\n")

    def test_empty_max_via_trailing_flag(self):
        code, _, err = self.run_recall(["--query", "x", "--max"])  # trailing -> ""
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: recall --max must be a number\n")

    def test_query_file_not_found(self):
        code, _, err = self.run_recall(["--query-file", os.path.join(self.root, "nope.txt")])
        self.assertEqual(code, 2)
        self.assertIn("query file not found:", err)

    def test_query_file_first_120_lines(self):
        qf = os.path.join(self.root, "q.txt")
        with open(qf, "w", encoding="utf-8") as fh:
            fh.write("\n".join("line%d" % i for i in range(200)) + "\n")
        code, out, _ = self.run_recall(["--query-file", qf])
        self.assertEqual(code, 0)
        obj = json.loads(out)
        self.assertEqual(obj["query"], "\n".join("line%d" % i for i in range(120)))

    def test_unknown_arg(self):
        code, _, err = self.run_recall(["--query", "x", "--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: recall: unknown argument: --bogus\n")

    def test_write_creates_md_json_and_usage(self):
        with open(os.path.join(self.project, "LEARNINGS.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"id":"L1","status":"current","topic":"auth","summary":"auth flow","evidence":["src/a.py"]}\n')
        code, out, _ = self.run_recall(["--query", "auth", "--write", "RECALL.md"])
        self.assertEqual(code, 0)
        md = os.path.join(self.root, "RECALL.md")
        js = os.path.join(self.root, "RECALL.json")
        usage = os.path.join(self.project, "MEMORY-USAGE.json")
        self.assertTrue(os.path.isfile(md) and os.path.isfile(js) and os.path.isfile(usage))
        md_text = _read(md)
        self.assertTrue(md_text.startswith("# Recall\n\nGenerated: %s\n\n" % _TS))
        self.assertIn("## Sources", md_text)
        # RECALL.json mirrors stdout exactly (timestamp-free), pretty + trailing newline.
        js_text = _read(js)
        self.assertEqual(json.loads(js_text), json.loads(out))
        self.assertTrue(js_text.endswith("}\n"))
        usage_obj = json.loads(_read(usage))
        self.assertEqual(usage_obj["items"]["learning:L1"]["use_count"], 1)
        self.assertEqual(usage_obj["events"][-1]["kind"], "recall")

    def test_usage_hits_exclude_facts(self):
        with open(os.path.join(self.project, "LEARNINGS.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"id":"L1","status":"current","summary":"auth"}\n')
        with open(os.path.join(self.project, "FACTS.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"kind":"module","area":"core","path":"auth.py","summary":"auth"}\n')
        self.run_recall(["--query", "auth", "--write", "RECALL.md"])
        usage_obj = json.loads(_read(os.path.join(self.project, "MEMORY-USAGE.json")))
        # facts hit produces key "fact:auth.py:..." would appear ONLY if facts were included.
        self.assertIn("learning:L1", usage_obj["items"])
        self.assertFalse(any(k.startswith("fact:") for k in usage_obj["items"]))

    def test_no_write_no_files(self):
        self.run_recall(["--query", "auth"])
        self.assertFalse(os.path.isfile(os.path.join(self.root, "RECALL.md")))
        self.assertFalse(os.path.isfile(os.path.join(self.project, "MEMORY-USAGE.json")))

    def test_dispatch_registration(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            code = main(["recall", "--root", self.root, "--query", "auth"])
        self.assertEqual(code, 0)
        self.assertIn('"schema_version":1', out.getvalue())


class UpdateUsageMetricsCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)
        self.usage = os.path.join(self.project, "MEMORY-USAGE.json")
        tsp = mock.patch("memory_router.clock.iso_now", return_value=_TS)
        tsp.start()
        self.addCleanup(tsp.stop)

    def load(self):
        return json.loads(_read(self.usage))

    def test_hit_key_branches(self):
        usage_metrics.update_usage_metrics(self.root, [
            {"id": "L1", "summary": "s"},
            {"kind": "run_artifact", "path": "runs/x/PLAN.md"},
            {"kind": "fact", "ref": "a.py:1"},
            {"ref": "r1"},  # no id, no kind -> "memory:r1"
        ])
        keys = self.load()["items"]
        self.assertIn("learning:L1", keys)
        self.assertIn("run:runs/x/PLAN.md", keys)
        self.assertIn("fact:a.py:1", keys)
        self.assertIn("memory:r1", keys)

    def test_value_fields_and_evidence_ref_fallback(self):
        usage_metrics.update_usage_metrics(self.root, [
            {"id": "L1", "kind": "learning", "summary": "sum", "evidence": ["src/a.py", "b"]},
        ])
        item = self.load()["items"]["learning:L1"]
        self.assertEqual(item["kind"], "learning")
        self.assertEqual(item["title"], "sum")        # title // summary // id
        self.assertEqual(item["ref"], "src/a.py")      # ref // evidence[0]
        self.assertEqual(item["summary"], "sum")
        self.assertEqual(list(item.keys()),
                         ["kind", "source", "title", "ref", "summary", "use_count", "last_used_at"])

    def test_use_count_accumulates_across_calls_and_within_batch(self):
        usage_metrics.update_usage_metrics(self.root, [{"id": "L1", "summary": "s"}])
        usage_metrics.update_usage_metrics(self.root, [{"id": "L1", "summary": "s"},
                                                       {"id": "L1", "summary": "s"}])
        self.assertEqual(self.load()["items"]["learning:L1"]["use_count"], 3)

    def test_event_shape_and_estimated_tokens(self):
        usage_metrics.update_usage_metrics(self.root,
                                           [{"id": "L1", "title": "auth token flow", "summary": "two words"}],
                                           "recall")
        ev = self.load()["events"][-1]
        self.assertEqual(ev["kind"], "recall")
        self.assertEqual(ev["at"], _TS)
        self.assertEqual(ev["hit_count"], 1)
        self.assertEqual(ev["estimated_tokens"], 5)  # auth token flow two words
        self.assertEqual(ev["keys"], ["learning:L1"])

    def test_events_capped_at_100(self):
        for _ in range(105):
            usage_metrics.update_usage_metrics(self.root, [{"id": "L1", "summary": "s"}])
        self.assertEqual(len(self.load()["events"]), 100)

    def test_default_object_and_top_keys(self):
        usage_metrics.update_usage_metrics(self.root, [])
        obj = self.load()
        self.assertEqual(list(obj.keys()), ["schema_version", "updated_at", "items", "events"])
        self.assertEqual(obj["schema_version"], 1)
        self.assertEqual(obj["updated_at"], _TS)
        self.assertEqual(obj["items"], {})

    def test_preserves_existing_items(self):
        usage_metrics.update_usage_metrics(self.root, [{"id": "OLD", "summary": "s"}])
        usage_metrics.update_usage_metrics(self.root, [{"id": "NEW", "summary": "s"}])
        self.assertIn("learning:OLD", self.load()["items"])
        self.assertIn("learning:NEW", self.load()["items"])


def _tools_present():
    # sqlite3 gate: bash `sqlite_available` (CLI presence) must agree with the port's
    # stdlib FTS5 probe for the index_status=missing parity to hold (no RECALL.sqlite built).
    if not all(shutil.which(t) for t in ("bash", "jq", "git", "sqlite3")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _strip_md_ts(text):
    return re.sub(r"(Generated: ).*", r"\1<TS>", text)


def _strip_usage_ts(text):
    for field in ("updated_at", "at", "last_used_at"):
        text = re.sub(r'("%s"\s*:\s*)"[^"]*"' % field, r'\1"<TS>"', text)
    return text


@unittest.skipUnless(_tools_present(), "bash/jq/git or pinned tag unavailable")
class RecallParityCase(unittest.TestCase):
    """Grounds `recall` stdout + the written RECALL.md / RECALL.json / MEMORY-USAGE.json
    byte-for-byte vs the pinned bash, normalizing only timestamps. Deliberately builds NO
    RECALL.sqlite so both sides report index_status=missing/hits=[] - FTS index parity is
    owned by the recall_index harness (Plans 6-7), avoiding the bash-vs-stdlib build
    row-count difference."""

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
            fh.write("auth memory line one\nsecond line\n")
        with open(os.path.join(proj, "USER.md"), "w") as fh:
            fh.write("user prefers auth tooling\n")
        with open(os.path.join(proj, "LEARNINGS.jsonl"), "w") as fh:
            fh.write('{"id":"L1","status":"current","kind":"learning","scope":"project",'
                     '"topic":"auth","summary":"auth flow works","sensitivity":"normal",'
                     '"evidence":["src/auth.py"]}\n'
                     '{"id":"L2","status":"stale","topic":"auth","summary":"auth old"}\n')
        with open(os.path.join(proj, "FACTS.jsonl"), "w") as fh:
            fh.write('{"kind":"module","area":"core","path":"auth.py","summary":"auth module","confidence":"high"}\n')
        runs = os.path.join(root, ".kimiflow", "runs", "demo")
        os.makedirs(os.path.join(runs, "findings"))
        for name, body in (("PLAN.md", "# Title\nauth plan body\n"),
                           ("STATE.md", "auth state body\n"),
                           ("INTENT.md", "unrelated body\n")):
            with open(os.path.join(runs, name), "w") as fh:
                fh.write(body)
        with open(os.path.join(runs, "findings", "f1.md"), "w") as fh:
            fh.write("auth finding\n")
        return proj

    def _bash(self, root, argv):
        return subprocess.run(["bash", self.script, "recall", "--root", root] + argv,
                              stdout=subprocess.PIPE, text=True, check=True, env=_env()).stdout

    def _py(self, root, argv):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            self.assertEqual(recall.run(["--root", root] + argv), 0)
        return out.getvalue()

    def _roots(self):
        rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rb, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rp, ignore_errors=True)
        self._populate(rb)
        self._populate(rp)
        return rb, rp

    def test_stdout_parity(self):
        for argv in (["--query", "auth"], ["--query", "auth", "--pretty"],
                     ["--query", "auth", "--max", "1"], ["--query", "nomatchzzz"]):
            rb, rp = self._roots()
            self.assertEqual(self._bash(rb, argv), self._py(rp, argv), "argv=%r" % argv)

    def test_over_budget_parity(self):
        rb, rp = self._roots()
        argv = ["--query", "auth"]
        env = dict(_env(), KIMIFLOW_MEMORY_BUDGET="1", KIMIFLOW_USER_MEMORY_BUDGET="1")
        b = subprocess.run(["bash", self.script, "recall", "--root", rb] + argv,
                           stdout=subprocess.PIPE, text=True, check=True, env=env).stdout
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            recall.run(["--root", rp] + argv)
        self.assertEqual(b, out.getvalue())

    def test_written_files_parity(self):
        rb, rp = self._roots()
        self._bash(rb, ["--query", "auth", "--write", "RECALL.md"])
        self._py(rp, ["--query", "auth", "--write", "RECALL.md"])
        # RECALL.md (normalize Generated:)
        self.assertEqual(_strip_md_ts(_read(os.path.join(rb, "RECALL.md"))),
                         _strip_md_ts(_read(os.path.join(rp, "RECALL.md"))))
        # RECALL.json (timestamp-free)
        self.assertEqual(_read(os.path.join(rb, "RECALL.json")),
                         _read(os.path.join(rp, "RECALL.json")))
        # MEMORY-USAGE.json (normalize timestamps)
        usage_rel = os.path.join(".kimiflow", "project", "MEMORY-USAGE.json")
        self.assertEqual(_strip_usage_ts(_read(os.path.join(rb, usage_rel))),
                         _strip_usage_ts(_read(os.path.join(rp, usage_rel))))


if __name__ == "__main__":
    unittest.main()
