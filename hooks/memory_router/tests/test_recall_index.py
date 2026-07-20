import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from memory_router import recall_index

ISO = "2026-06-29T00:00:00Z"
DOT = "\u00b7"  # U+00B7 MIDDLE DOT (never write the literal char in source).


class FtsQueryFromTermsCase(unittest.TestCase):
    def q(self, terms):
        return recall_index.fts_query_from_terms(terms)

    def test_basic_sorted_and_quoted(self):
        self.assertEqual(self.q(["build", "auth"]), '"auth" OR "build"')

    def test_strips_non_term_chars(self):
        self.assertEqual(self.q(["foo-bar!"]), '"foobar"')

    def test_drops_terms_shorter_than_three(self):
        self.assertEqual(self.q(["ab", "abc", "x"]), '"abc"')

    def test_unique_dedups_and_sorts(self):
        self.assertEqual(self.q(["zoo", "abc", "abc", "zoo"]), '"abc" OR "zoo"')

    def test_underscore_kept(self):
        self.assertEqual(self.q(["foo_bar"]), '"foo_bar"')

    def test_empty_when_all_filtered(self):
        self.assertEqual(self.q(["a", "b!", ""]), "")

    def test_length_measured_after_stripping(self):
        # "a-b" strips to "ab" (len 2) -> dropped.
        self.assertEqual(self.q(["a-b"]), "")


class FtsEngineCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project, exist_ok=True)
        self.db = recall_index.recall_db_path(self.root)
        p = mock.patch("memory_router.clock.iso_now", return_value=ISO)
        p.start()
        self.addCleanup(p.stop)

    def build(self, rows):
        con = sqlite3.connect(self.db)
        recall_index.init_recall_db(con)
        for r in rows:
            recall_index.insert_fts_row(con, *r)
        con.commit()
        con.close()

    def test_fts5_available(self):
        self.assertTrue(recall_index.fts5_available())

    def test_init_stamps_updated_at(self):
        con = sqlite3.connect(self.db)
        recall_index.init_recall_db(con, "sha256:test-corpus")
        con.commit()
        meta = dict(con.execute("SELECT key, value FROM recall_meta").fetchall())
        con.close()
        self.assertEqual(meta["updated_at"], ISO)
        self.assertEqual(meta["schema_version"], str(recall_index.INDEX_SCHEMA_VERSION))
        self.assertEqual(meta["corpus_fingerprint"], "sha256:test-corpus")

    def test_query_roundtrip_returns_hit_shape(self):
        self.build([
            ("learning", ".kimiflow/project/LEARNINGS.jsonl", "build flow",
             "we fixed the build flow and release convention", "src/foo.py:5"),
            ("memory", ".kimiflow/project/MEMORY.md", "Project Memory",
             "auth token rotation chosen", ".kimiflow/project/MEMORY.md"),
        ])
        hits = recall_index.fts_hits_json(self.root, ["build"], 10)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0], {
            "kind": "learning",
            "source": ".kimiflow/project/LEARNINGS.jsonl",
            "title": "build flow",
            "ref": "src/foo.py:5",
            "summary": "we fixed the build flow and release convention",
        })

    def test_or_query_matches_multiple(self):
        self.build([
            ("learning", "L", "t1", "build pipeline", "r1"),
            ("memory", "M", "t2", "auth rotation", "r2"),
            ("fact", "F", "t3", "unrelated text", "r3"),
        ])
        hits = recall_index.fts_hits_json(self.root, ["build", "auth"], 10)
        self.assertEqual({h["ref"] for h in hits}, {"r1", "r2"})

    def test_limit_respected(self):
        self.build([("learning", "L", "t%d" % i, "build flow", "r%d" % i) for i in range(5)])
        self.assertEqual(len(recall_index.fts_hits_json(self.root, ["build"], 2)), 2)

    def test_summary_truncated_to_420(self):
        self.build([("learning", "L", "t", "build " + "x" * 500, "r")])
        hits = recall_index.fts_hits_json(self.root, ["build"], 10)
        self.assertEqual(len(hits[0]["summary"]), 420)

    def test_missing_db_returns_empty(self):
        self.assertEqual(recall_index.fts_hits_json(self.root, ["build"], 10), [])

    def test_empty_query_returns_empty(self):
        self.build([("learning", "L", "t", "build flow", "r")])
        self.assertEqual(recall_index.fts_hits_json(self.root, ["ab", "x"], 10), [])

    def test_corrupt_db_returns_empty(self):
        with open(self.db, "w", encoding="utf-8") as fh:
            fh.write("this is not a sqlite database")
        self.assertEqual(recall_index.fts_hits_json(self.root, ["build"], 10), [])

    def test_unavailable_fts5_returns_empty(self):
        self.build([("learning", "L", "t", "build flow", "r")])
        with mock.patch("memory_router.recall_index.fts5_available", return_value=False):
            self.assertEqual(recall_index.fts_hits_json(self.root, ["build"], 10), [])


class HelperCase(unittest.TestCase):
    def test_jq_or_substitutes_null_and_false(self):
        self.assertEqual(recall_index._jq_or(None, "d"), "d")
        self.assertEqual(recall_index._jq_or(False, "d"), "d")

    def test_jq_or_passes_through_falsy_truthy_values(self):
        # In jq, empty string and 0 are truthy -> pass through unchanged.
        self.assertEqual(recall_index._jq_or("", "d"), "")
        self.assertEqual(recall_index._jq_or(0, "d"), 0)
        self.assertEqual(recall_index._jq_or("x", "d"), "x")

    def test_first_lines_caps_and_strips_trailing_newlines(self):
        text = "\n".join("l%d" % i for i in range(1, 201)) + "\n\n\n"
        out = recall_index._first_lines(text)
        self.assertEqual(out.split("\n"), ["l%d" % i for i in range(1, 181)])

    def test_first_lines_keeps_interior_blank_lines(self):
        self.assertEqual(recall_index._first_lines("a\n\nb\n"), "a\n\nb")

    def test_first_lines_splits_only_on_newline(self):
        # sed splits on \n only; a CRLF leaves the \r on the line.
        self.assertEqual(recall_index._first_lines("a\r\nb\r\n"), "a\r\nb\r")

    def test_first_lines_all_blank_collapses_to_empty(self):
        self.assertEqual(recall_index._first_lines("\n\n"), "")

    def test_artifact_title_drops_first_two_components(self):
        self.assertEqual(
            recall_index._artifact_title(".kimiflow/runs/2026/INTENT.md"),
            "runs " + DOT + " 2026/INTENT.md",
        )

    def test_artifact_title_two_component_path(self):
        self.assertEqual(
            recall_index._artifact_title(".kimiflow/PLAN.md"),
            "PLAN.md " + DOT + " ",
        )

    def test_evidence_ref_picks_first_or_empty(self):
        self.assertEqual(recall_index._evidence_ref({"evidence": ["a", "b"]}), "a")
        self.assertEqual(recall_index._evidence_ref({"evidence": []}), "")
        self.assertEqual(recall_index._evidence_ref({"evidence": None}), "")
        self.assertEqual(recall_index._evidence_ref({}), "")
        self.assertEqual(recall_index._evidence_ref({"evidence": "notalist"}), "")


class BuildRecallIndexCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project, exist_ok=True)
        self.db = recall_index.recall_db_path(self.root)
        p = mock.patch("memory_router.clock.iso_now", return_value=ISO)
        p.start()
        self.addCleanup(p.stop)

    def write(self, relpath, text):
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def write_jsonl(self, relpath, rows):
        self.write(relpath, "".join(json.dumps(r) + "\n" for r in rows))

    def rows(self):
        rc = recall_index.build_recall_index(self.root, self.db)
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db)
        out = con.execute(
            "SELECT kind, source, title, body, ref FROM recall_fts ORDER BY rowid"
        ).fetchall()
        con.close()
        return out

    def by_kind(self, kind):
        return [r for r in self.rows() if r[0] == kind]

    def test_returns_2_when_fts5_unavailable(self):
        with mock.patch("memory_router.recall_index.fts5_available", return_value=False):
            self.assertEqual(recall_index.build_recall_index(self.root, self.db), 2)
        self.assertFalse(os.path.exists(self.db))

    def test_empty_project_only_meta_row(self):
        rc = recall_index.build_recall_index(self.root, self.db)
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT count(*) FROM recall_fts").fetchone()[0], 0)
        meta = con.execute("SELECT key, value FROM recall_meta").fetchall()
        con.close()
        self.assertEqual(dict(meta)["updated_at"], ISO)
        self.assertEqual(dict(meta)["schema_version"], str(recall_index.INDEX_SCHEMA_VERSION))
        self.assertTrue(dict(meta)["corpus_fingerprint"].startswith("sha256:"))

    def test_rebuild_drops_previous_rows(self):
        self.write(".kimiflow/project/MEMORY.md", "one\n")
        self.assertEqual(len(self.by_kind("memory")), 1)
        # Rebuild after removing the source -> the old row must be gone.
        os.remove(os.path.join(self.project, "MEMORY.md"))
        self.assertEqual(len(self.by_kind("memory")), 0)

    def test_index_state_detects_corpus_drift(self):
        self.write(".kimiflow/project/MEMORY.md", "first\n")
        self.assertEqual(recall_index.build_recall_index(self.root, self.db), 0)
        self.assertEqual(recall_index.index_state(self.root)["status"], "fresh")
        self.write(".kimiflow/project/MEMORY.md", "second\n")
        state = recall_index.index_state(self.root)
        self.assertEqual(state["status"], "stale")
        self.assertEqual(state["reason"], "corpus_fingerprint_mismatch")
        self.assertEqual(recall_index.fts_hits_json(self.root, ["first"], 5), [])

    def test_failed_rebuild_preserves_previous_database(self):
        self.write(".kimiflow/project/MEMORY.md", "stable content\n")
        self.assertEqual(recall_index.build_recall_index(self.root, self.db), 0)
        with open(self.db, "rb") as fh:
            before = fh.read()
        with mock.patch("memory_router.recall_index.insert_fts_row",
                        side_effect=sqlite3.OperationalError("boom")):
            self.assertEqual(recall_index.build_recall_index(self.root, self.db), 1)
        with open(self.db, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_memory_and_user_md_first_180_lines(self):
        self.write(".kimiflow/project/MEMORY.md",
                   "\n".join("m%d" % i for i in range(1, 201)) + "\n\n")
        self.write(".kimiflow/project/USER.md", "prefers 'concise'\nno emoji\n")
        rows = self.rows()
        mem = [r for r in rows if r[0] == "memory"][0]
        self.assertEqual(mem[1], ".kimiflow/project/MEMORY.md")
        self.assertEqual(mem[2], "Project Memory")
        self.assertEqual(mem[4], ".kimiflow/project/MEMORY.md")
        self.assertEqual(mem[3].split("\n"), ["m%d" % i for i in range(1, 181)])
        user = [r for r in rows if r[0] == "user_profile" and r[1].endswith("USER.md")][0]
        self.assertEqual(user[2], "User Profile")
        self.assertEqual(user[3], "prefers 'concise'\nno emoji")

    def test_learnings_status_filter_and_defaults(self):
        self.write_jsonl(".kimiflow/project/LEARNINGS.jsonl", [
            {"id": "l1", "status": "current", "kind": "gotcha", "topic": "sqlite",
             "summary": "fts5 here", "evidence": ["x.sh:10", "y"]},
            {"id": "l2", "status": "superseded", "topic": "old", "summary": "drop"},
            {"id": "l3"},  # all defaults
            {"id": "l4", "status": None, "kind": "pattern", "topic": "nul"},  # null kept
        ])
        rows = self.by_kind("learning")
        titles = [r[2] for r in rows]
        self.assertEqual(titles, [
            "sqlite " + DOT + " gotcha " + DOT + " l1",
            "uncategorized " + DOT + " learning " + DOT + " l3",
            "nul " + DOT + " pattern " + DOT + " l4",
        ])
        self.assertEqual(rows[0][3], "fts5 here")          # body = summary
        self.assertEqual(rows[0][4], "x.sh:10")            # ref = evidence[0]
        self.assertEqual(rows[1][3], "")                   # default summary
        self.assertEqual(rows[1][4], "")                   # default evidence ref

    def test_user_rows_status_filter_and_defaults(self):
        self.write_jsonl(".kimiflow/project/USER.jsonl", [
            {"id": "u1", "status": "current", "topic": "tone", "summary": "direct",
             "evidence": ["chat:1"]},
            {"id": "u2", "status": "archived", "topic": "x"},
            {"id": "u3"},  # defaults: topic=profile
        ])
        rows = [r for r in self.by_kind("user_profile") if r[1].endswith("USER.jsonl")]
        self.assertEqual([r[2] for r in rows], ["tone " + DOT + " u1", "profile " + DOT + " u3"])
        self.assertEqual(rows[0][4], "chat:1")

    def test_facts_title_ref_and_line_formatting(self):
        self.write_jsonl(".kimiflow/project/FACTS.jsonl", [
            {"kind": "module", "area": "hooks", "path": "a.py", "line": 42, "summary": "fa"},
            {"area": "core", "path": "b.py", "summary": "no line"},   # kind=fact, line=1
            {"kind": "fn", "path": "c.py", "line": 7.0},              # area=codebase, 7.0 kept
            {"kind": "z", "area": "d", "path": "d.py", "line": 0},    # 0 stays 0
        ])
        rows = self.by_kind("fact")
        self.assertEqual([r[2] for r in rows], [
            "module " + DOT + " hooks " + DOT + " a.py",
            "fact " + DOT + " core " + DOT + " b.py",
            "fn " + DOT + " codebase " + DOT + " c.py",
            "z " + DOT + " d " + DOT + " d.py",
        ])
        self.assertEqual([r[4] for r in rows], ["a.py:42", "b.py:1", "c.py:7.0", "d.py:0"])

    def test_run_artifacts_match_prune_and_sort(self):
        self.write(".kimiflow/runs/demo/INTENT.md", "intent\n")
        self.write(".kimiflow/runs/demo/PLAN.md", "plan\n")
        self.write(".kimiflow/runs/demo/findings/f1.md", "finding\n")
        self.write(".kimiflow/runs/demo/NOTES.md", "excluded\n")        # not a matched name
        self.write(".kimiflow/project/PLAN.md", "pruned project file\n")  # pruned subtree
        rows = self.by_kind("run_artifact")
        self.assertEqual([r[1] for r in rows], [
            ".kimiflow/runs/demo/INTENT.md",
            ".kimiflow/runs/demo/PLAN.md",
            ".kimiflow/runs/demo/findings/f1.md",
        ])
        self.assertEqual(rows[0][2], "runs " + DOT + " demo/INTENT.md")
        self.assertEqual(rows[2][2], "runs " + DOT + " demo/findings/f1.md")
        self.assertEqual(rows[0][3], "intent")  # body = first lines

    def test_run_artifact_body_first_180_lines(self):
        self.write(".kimiflow/runs/demo/PLAN.md",
                   "\n".join("p%d" % i for i in range(1, 201)) + "\n")
        body = self.by_kind("run_artifact")[0][3]
        self.assertEqual(body.split("\n"), ["p%d" % i for i in range(1, 181)])

    def test_body_read_preserves_crlf_like_sed(self):
        # Bash `sed -n '1,180p'` splits on \n only and keeps the \r per line; the
        # read must not translate newlines (universal-newline mode would drop \r).
        full = os.path.join(self.project, "MEMORY.md")
        with open(full, "w", encoding="utf-8", newline="") as fh:
            fh.write("a\r\nb\r\n")
        self.assertEqual(self.by_kind("memory")[0][3], "a\r\nb\r")


if __name__ == "__main__":
    unittest.main()
