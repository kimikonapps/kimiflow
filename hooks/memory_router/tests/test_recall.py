import contextlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import tracemalloc
import unittest
from unittest import mock

from memory_router import recall, recall_index, store, usage_metrics, workspace_scope
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

    def test_workflow_metadata_is_removed_without_dropping_product_modes(self):
        query = (
            "Flow schema: 4\nStatus: active\nMode: feature\nScope: large\n"
            "Mode: offline\nStatus: available only after synchronization\n"
        )
        terms = self.t(query)
        self.assertNotIn("feature", terms)
        self.assertNotIn("large", terms)
        self.assertIn("offline", terms)
        self.assertIn("available", terms)
        self.assertIn("synchronization", terms)


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

    def obj(self, query="auth", max_hits=5, targeted=False):
        return recall.recall_json(self.root, query, max_hits, targeted=targeted)

    def test_key_order(self):
        o = self.obj()
        self.assertEqual(list(o.keys()),
                         ["schema_version", "query", "query_terms", "token_budget",
                          "budget", "authority", "attribution", "sources", "explanation", "omitted"])
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
        self.assertEqual(o["token_budget"], 1800)
        self.assertEqual(o["sources"]["memory"]["budget"], 900)
        self.assertIn("always_on_included", o["explanation"]["reason_codes"])

    def test_unreadable_memory_and_user_are_omitted_explicitly(self):
        for name in ("MEMORY.md", "USER.md"):
            with open(os.path.join(self.project, name), "wb") as handle:
                handle.write(b"\xff\xfe")
        o = self.obj()
        self.assertEqual(o["sources"]["memory"]["status"], "unreadable")
        self.assertEqual(o["sources"]["user_profile"]["status"], "unreadable")
        self.assertIn("memory_unreadable", o["explanation"]["reason_codes"])
        self.assertIn("user_profile_unreadable", o["explanation"]["reason_codes"])
        self.assertIn(
            {"source": "MEMORY.md", "reason": "unreadable"},
            o["explanation"]["omitted_sources"],
        )
        self.assertIn("MEMORY.md omitted: unreadable", o["omitted"])

    def test_memory_over_budget(self):
        self.write("MEMORY.md", "alpha beta gamma\n")
        with mock.patch.dict(os.environ, {"KIMIFLOW_MEMORY_BUDGET": "2"}):
            o = self.obj()
        self.assertEqual(o["sources"]["memory"]["status"], "omitted_over_budget")
        self.assertEqual(o["sources"]["memory"]["content"], "")
        self.assertEqual(o["token_budget"], 1800)
        self.assertEqual(o["sources"]["memory"]["budget"], 2)
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
        self.assertEqual(o["sources"]["index"]["status"], "available_no_hits")
        self.assertEqual(o["sources"]["index"]["count"], 0)
        self.assertGreaterEqual(o["budget"]["duplicates_removed"], 1)

    def test_learning_and_fact_hits_and_counts(self):
        self.write("LEARNINGS.jsonl",
                   '{"id":"L1","status":"current","summary":"auth flow"}\n'
                   '{"id":"L2","status":"stale","summary":"auth flow"}\n')
        self.write("FACTS.jsonl", '{"kind":"module","area":"core","path":"auth.py","summary":"the auth module"}\n')
        o = self.obj("auth")
        self.assertEqual(o["sources"]["learnings"]["count"], 1)
        self.assertEqual(o["sources"]["facts"]["count"], 1)
        learning_id = o["sources"]["learnings"]["hits"][0]["recall_id"]
        fact_id = o["sources"]["facts"]["hits"][0]["recall_id"]
        self.assertRegex(learning_id, r"^rec_[0-9a-f]{64}$")
        self.assertRegex(fact_id, r"^rec_[0-9a-f]{64}$")
        self.assertNotEqual(learning_id, fact_id)
        self.assertEqual(o["attribution"]["hit_count"], 2)
        self.assertEqual(o["explanation"]["hit_counts"]["total"],
                         o["sources"]["learnings"]["count"] + o["sources"]["facts"]["count"]
                         + o["sources"]["index"]["count"] + o["sources"]["history"]["count"])
        self.assertIn("local_recall_hits", o["explanation"]["reason_codes"])
        self.assertIn("project_map_fact_hits", o["explanation"]["reason_codes"])

    def test_workspace_scope_keeps_local_and_global_and_omits_foreign_unit(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write('{"name":"%s"}\n' % unit)
        rows = [
            {"kind": "module", "path": "packages/web/src/f%d.py" % index,
             "line": 1,
             "summary": "auth token foreign %d" % index}
            for index in range(25)
        ]
        rows.extend([
            {"kind": "module", "path": "packages/api/src/local.py", "line": 1,
             "summary": "auth local"},
            {"kind": "docs", "path": "README.md", "line": 1,
             "summary": "auth global"},
        ])
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        result = recall.recall_json(
            self.root, "auth token", 2, scope_paths=["packages/api/src/main.py"]
        )
        paths = [row["path"] for row in result["sources"]["facts"]["hits"]]
        self.assertEqual(paths[0], "packages/api/src/local.py")
        self.assertIn("README.md", paths)
        self.assertFalse(any(path.startswith("packages/web/") for path in paths))
        self.assertEqual(result["workspace_scope"]["status"], "active")
        self.assertGreaterEqual(result["workspace_scope"]["foreign_hits_omitted"], 25)

    def test_workspace_scope_preserves_multi_evidence_and_removes_fts_shadow(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write('{"name":"%s"}\n' % unit)
        self.write(
            "LEARNINGS.jsonl",
            "".join(
                json.dumps({"id": "shared%d" % index, "status": "current",
                            "scope": "project", "summary": "auth evidence token shared",
                            "evidence": ["README.md"]}) + "\n"
                for index in range(25)
            ) +
            json.dumps({"id": "mixed", "status": "current", "scope": "project",
                        "summary": "auth mixed evidence",
                        "evidence": ["packages/web/src/a.py:1", "packages/api/src/b.py:1"]}) + "\n" +
            json.dumps({"id": "foreign", "status": "current", "scope": "project",
                        "summary": "auth foreign evidence",
                        "evidence": ["packages/web/src/a.py:1"]}) + "\n",
        )
        recall_index.build_recall_index(self.root, recall_index.recall_db_path(self.root))
        result = recall.recall_json(
            self.root, "auth evidence token", 1,
            scope_paths=["packages/api/src/main.py"]
        )
        self.assertEqual(
            [row["id"] for row in result["sources"]["learnings"]["hits"]], ["mixed"]
        )
        all_index = json.dumps(result["sources"]["index"]["hits"])
        self.assertNotIn("foreign", all_index)

    def test_workspace_scope_carries_typed_full_rows_into_fts_shadows(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({
                "id": "foreign-extensionless",
                "status": "current",
                "summary": "auth foreign docker strategy",
                "evidence": ["packages/web/Dockerfile"],
                "evidence_fingerprints": [{
                    "ref": "packages/web/Dockerfile",
                    "path": "packages/web/Dockerfile",
                    "status": "current",
                    "digest_algorithm": "sha256",
                    "digest": "a" * 64,
                    "sha256": "a" * 64,
                }],
            }) + "\n",
        )
        self.write(
            "FACTS.jsonl",
            json.dumps({
                "kind": "module",
                "path": "packages/web/config.d",
                "summary": "auth ambiguous directory",
            }) + "\n",
        )
        recall_index.build_recall_index(
            self.root, recall_index.recall_db_path(self.root)
        )

        result = recall.recall_json(
            self.root, "auth", 5, scope_paths=["packages/api/src/main.py"]
        )
        rendered = json.dumps(result["sources"])
        self.assertNotIn("foreign docker strategy", rendered)
        self.assertIn("packages/web/config.d", rendered)

    def test_workspace_scope_malformed_learning_evidence_stays_shared(self):
        directory = os.path.join(self.root, "packages", "api")
        os.makedirs(os.path.join(directory, "src"))
        with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({
                "id": "malformed-evidence",
                "status": "current",
                "summary": "auth malformed evidence",
                "evidence": [{"path": "packages/api/src/a.py"}],
            }) + "\n",
        )
        result = recall.recall_json(
            self.root, "auth", 5, scope_paths=["packages/api/src/main.py"]
        )
        self.assertEqual(
            [row["id"] for row in result["sources"]["learnings"]["hits"]],
            ["malformed-evidence"],
        )

    def test_workspace_scope_omits_flattened_learning_fact_shadows(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        rows = [
            {"id": "foreign", "status": "current", "summary": "auth collision one",
             "evidence": ["packages/web/src/a.py:1"]},
            {"id": "local", "status": "current", "summary": "auth collision one",
             "evidence": ["packages/web/src/a.py:1", "packages/api/src/b.py:1"]},
            {"id": "shared", "status": "current", "summary": "auth collision two",
             "evidence": ["packages/web/src/c.py:1", "NOT VERIFIED"]},
            {"id": "local-two", "status": "current", "summary": "auth collision two",
             "evidence": ["packages/web/src/c.py:1", "packages/api/src/d.py:1"]},
        ]
        self.write(
            "LEARNINGS.jsonl", "".join(json.dumps(row) + "\n" for row in rows)
        )
        recall_index.build_recall_index(
            self.root, recall_index.recall_db_path(self.root)
        )
        result = recall.recall_json(
            self.root, "auth collision", 10,
            scope_paths=["packages/api/src/main.py"],
        )
        self.assertFalse(any(
            hit.get("kind") in ("learning", "fact")
            for hit in result["sources"]["index"]["hits"]
        ))

    def test_workspace_scope_missing_index_does_not_disable_direct_narrowing(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        self.write(
            "FACTS.jsonl",
            "".join(
                json.dumps({
                    "path": "packages/web/src/f%d.py" % index,
                    "line": 1,
                    "summary": "auth foreign %d" % index,
                }) + "\n"
                for index in range(21)
            ),
        )
        with mock.patch.dict(os.environ, {"KIMIFLOW_RECALL_BUDGET": "20"}):
            result = recall.recall_json(
                self.root, "auth", 5,
                scope_paths=["packages/api/src/main.py"],
            )
        self.assertEqual(result["sources"]["index"]["freshness"], "missing")
        self.assertEqual(result["workspace_scope"]["status"], "active")
        self.assertEqual(result["workspace_scope"]["foreign_hits_omitted"], 21)

    def test_workspace_scope_drift_fallback_keeps_generator_receipt_count(self):
        directory = os.path.join(self.root, "packages", "api")
        os.makedirs(os.path.join(directory, "src"))
        with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        paths = (path for path in ["packages/api/src/main.py"])
        with mock.patch(
            "memory_router.workspace_scope.revalidate_scope",
            return_value=False,
        ):
            result = recall.recall_json(
                self.root, "auth", 5, scope_paths=paths
            )
        self.assertEqual(result["workspace_scope"]["status"], "fallback")
        self.assertEqual(result["workspace_scope"]["requested_path_count"], 1)

    def test_workspace_scope_fts_seed_recovers_global_full_rows(self):
        directory = os.path.join(self.root, "packages", "api")
        os.makedirs(os.path.join(directory, "src"))
        with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({
                "id": "accented-global",
                "status": "current",
                "summary": "café global rule",
                "evidence": ["NOT VERIFIED"],
            }) + "\n",
        )
        facts = [
            {"path": "README.md", "line": 1, "summary": "auth legacy"}
            for _ in range(20)
        ]
        facts.append({
            "path": "ARCHITECTURE.md", "line": 1,
            "summary": "auth architecture boundary",
        })
        self.write(
            "FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in facts)
        )
        recall_index.build_recall_index(
            self.root, recall_index.recall_db_path(self.root)
        )

        accented = recall.recall_json(
            self.root, "cafe", 2, scope_paths=["packages/api/src/main.py"]
        )
        self.assertEqual(
            [row["id"] for row in accented["sources"]["learnings"]["hits"]],
            ["accented-global"],
        )
        recovered = recall.recall_json(
            self.root, "auth", 2, scope_paths=["packages/api/src/main.py"]
        )
        self.assertEqual(
            {row["path"] for row in recovered["sources"]["facts"]["hits"]},
            {"README.md", "ARCHITECTURE.md"},
        )

    def test_workspace_scope_incomplete_shadow_scan_keeps_index_hit_shared(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({
                "id": "foreign-accented",
                "status": "current",
                "summary": "café foreign rule",
                "evidence": ["packages/web/src/a.py:1"],
            }) + "\n",
        )
        recall_index.build_recall_index(
            self.root, recall_index.recall_db_path(self.root)
        )
        original = recall._iter_jsonl_objects_with_receipt

        def interrupted(path, completion=None):
            inner_completion = {}
            for row in original(path, inner_completion):
                yield row
                return

        with mock.patch(
            "memory_router.recall._iter_jsonl_objects_with_receipt",
            side_effect=interrupted,
        ):
            result = recall.recall_json(
                self.root, "cafe", 5,
                scope_paths=["packages/api/src/main.py"],
            )
        self.assertIn(
            "café foreign rule",
            [hit.get("summary") for hit in result["sources"]["index"]["hits"]],
        )

    def test_workspace_scope_shadow_proof_belongs_to_receipted_scan(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({
                "id": "foreign",
                "status": "current",
                "summary": "auth foreign rule",
                "evidence": ["packages/web/src/a.py:1"],
            }) + "\n",
        )
        recall_index.build_recall_index(
            self.root, recall_index.recall_db_path(self.root)
        )
        original = recall._ranked_jsonl_hits
        learning_path = os.path.join(self.project, "LEARNINGS.jsonl")

        def replace_after_rank(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == learning_path:
                replacement = learning_path + ".replacement"
                with open(replacement, "w", encoding="utf-8"):
                    pass
                os.replace(replacement, learning_path)
            return result

        with mock.patch(
            "memory_router.recall._ranked_jsonl_hits",
            side_effect=replace_after_rank,
        ):
            result = recall.recall_json(
                self.root, "auth", 5,
                scope_paths=["packages/api/src/main.py"],
            )
        self.assertIn(
            "auth foreign rule",
            [hit.get("summary") for hit in result["sources"]["index"]["hits"]],
        )

    def test_workspace_scope_caps_fts_only_globals_without_suppressing_them(self):
        directory = os.path.join(self.root, "packages", "api")
        os.makedirs(os.path.join(directory, "src"))
        with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.write(
            "LEARNINGS.jsonl",
            "".join(
                json.dumps({
                    "id": "accented-global-%d" % index,
                    "status": "current",
                    "summary": "café global rule %d %s" % (index, "x" * 500),
                    "evidence": ["manual:global-%d" % index],
                }) + "\n"
                for index in range(25)
            ),
        )
        recall_index.build_recall_index(
            self.root, recall_index.recall_db_path(self.root)
        )
        result = recall.recall_json(
            self.root, "cafe", 5,
            scope_paths=["packages/api/src/main.py"],
        )
        self.assertEqual(result["sources"]["learnings"]["count"], 5)
        self.write(
            "FACTS.jsonl",
            "".join(
                json.dumps({
                    "path": "GLOBAL-%d.md" % index,
                    "line": 1,
                    "summary": "café global fact %d %s" % (
                        index, "y" * 500
                    ),
                }) + "\n"
                for index in range(25)
            ),
        )
        recall_index.build_recall_index(
            self.root, recall_index.recall_db_path(self.root)
        )
        facts = recall.recall_json(
            self.root, "cafe", 5,
            scope_paths=["packages/api/src/main.py"],
        )
        self.assertEqual(facts["sources"]["facts"]["count"], 5)

    def test_workspace_scope_omission_identity_hashes_full_values(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        prefix = "x" * 600
        rows = [
            {"path": "packages/web/src/%s-%d.py" % (prefix, index),
             "line": 1, "summary": "auth %s-%d" % (prefix, index)}
            for index in range(2)
        ]
        self.write(
            "FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in rows)
        )
        scope = recall.recall_json(
            self.root, "auth", 5, scope_paths=["packages/api/src/main.py"]
        )["workspace_scope"]
        self.assertEqual(scope["foreign_hits_omitted"], 2)
        self.assertFalse(scope["foreign_hits_omitted_truncated"])

    def test_workspace_scope_locality_precedes_shared_query_coverage(self):
        directory = os.path.join(self.root, "packages", "api")
        os.makedirs(os.path.join(directory, "src"))
        with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        rows = [
            {"path": "shared%d.py" % index, "line": 1,
             "summary": "auth token shared"}
            for index in range(25)
        ]
        rows.append({"path": "packages/api/src/local.py", "line": 1,
                     "summary": "auth local"})
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        result = recall.recall_json(
            self.root, "auth token", 1, scope_paths=["packages/api/src/main.py"]
        )
        self.assertEqual(
            [row["path"] for row in result["sources"]["facts"]["hits"]],
            ["packages/api/src/local.py"],
        )

    def test_workspace_scope_bounded_classification_falls_back_project_wide(self):
        directory = os.path.join(self.root, "packages", "api")
        os.makedirs(os.path.join(directory, "src"))
        with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        rows = [
            {"path": "unknown/d%d/file.py" % index, "line": 1,
             "summary": "auth shared"}
            for index in range(workspace_scope.MAX_CANDIDATE_DIRECTORIES + 1)
        ]
        rows.append({"path": "packages/api/src/local.py", "line": 1,
                     "summary": "auth local"})
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        legacy = self.obj("auth")
        result = recall.recall_json(
            self.root, "auth", 5, scope_paths=["packages/api/src/main.py"]
        )
        self.assertEqual(result["workspace_scope"]["status"], "fallback")
        self.assertEqual(
            result["workspace_scope"]["reason"], "scope_classification_limit"
        )
        self.assertEqual(result["sources"], legacy["sources"])

    def test_workspace_scope_caps_foreign_omission_accounting(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        exact_rows = [
            {"path": "packages/web/src/f%d.py" % index, "line": 1,
             "summary": "auth %d" % index}
            for index in range(workspace_scope.MAX_FOREIGN_IDENTITIES)
        ]
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in exact_rows))
        exact = recall.recall_json(
            self.root, "auth", 5, scope_paths=["packages/api/src/main.py"]
        )["workspace_scope"]
        self.assertEqual(
            exact["foreign_hits_omitted"], workspace_scope.MAX_FOREIGN_IDENTITIES
        )
        self.assertFalse(exact["foreign_hits_omitted_truncated"])

        rows = exact_rows + [
            {"path": "packages/web/src/f%d.py" % index, "line": 1,
             "summary": "auth %d" % index}
            for index in range(
                workspace_scope.MAX_FOREIGN_IDENTITIES,
                workspace_scope.MAX_FOREIGN_IDENTITIES + 20,
            )
        ]
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        result = recall.recall_json(
            self.root, "auth", 5, scope_paths=["packages/api/src/main.py"]
        )
        scope = result["workspace_scope"]
        self.assertEqual(scope["foreign_hits_omitted"], workspace_scope.MAX_FOREIGN_IDENTITIES)
        self.assertTrue(scope["foreign_hits_omitted_truncated"])

    def test_workspace_scope_drift_discards_scoped_pass_once(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
        self.write(
            "FACTS.jsonl",
            '{"path":"packages/web/src/a.py","line":1,"summary":"auth"}\n',
        )
        legacy = self.obj("auth")
        with mock.patch(
            "memory_router.recall.workspace_scope.revalidate_scope", return_value=False
        ) as revalidate:
            result = recall.recall_json(
                self.root, "auth", 5, scope_paths=["packages/api/src/main.py"]
            )
        self.assertEqual(revalidate.call_count, 1)
        self.assertEqual(result["sources"], legacy["sources"])
        self.assertEqual(result["workspace_scope"]["status"], "fallback")
        self.assertEqual(
            result["workspace_scope"]["reason"], "scope_changed_during_recall"
        )

    def test_scope_overflow_falls_back_without_partial_filtering(self):
        os.makedirs(os.path.join(self.root, "packages", "api", "src"))
        with open(os.path.join(self.root, "packages", "api", "package.json"), "w") as handle:
            handle.write("{}\n")
        self.write(
            "FACTS.jsonl",
            '{"path":"packages/api/src/a.py","line":1,"summary":"auth"}\n',
        )
        legacy = self.obj("auth")
        overflow = recall.recall_json(
            self.root,
            "auth",
            5,
            scope_paths=["packages/api/src/f%d.py" % index for index in range(33)],
        )
        self.assertEqual(overflow["workspace_scope"]["status"], "fallback")
        self.assertEqual(overflow["workspace_scope"]["reason"], "too_many_paths")
        self.assertEqual(overflow["sources"], legacy["sources"])

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

    def test_targeted_omits_broad_sources_and_caps_total_hits(self):
        self.write("MEMORY.md", "auth broad memory\n")
        self.write("USER.md", "auth user profile\n")
        self.write("FACTS.jsonl", '{"kind":"module","path":"auth.py","summary":"auth"}\n')
        self.write(
            "LEARNINGS.jsonl",
            "".join(
                json.dumps({"id": "L%d" % i, "status": "current", "summary": "auth fix"}) + "\n"
                for i in range(4)
            ),
        )
        for i in range(4):
            run = os.path.join(self.root, ".kimiflow", "runs", "demo-%d" % i)
            os.makedirs(run)
            with open(os.path.join(run, "PLAN.md"), "w", encoding="utf-8") as fh:
                fh.write("auth historical fix %d\n" % i)

        o = self.obj("auth", max_hits=5, targeted=True)

        self.assertEqual(o["sources"]["memory"]["status"], "omitted_targeted")
        self.assertEqual(o["sources"]["memory"]["content"], "")
        self.assertEqual(o["sources"]["user_profile"]["status"], "omitted_targeted")
        self.assertEqual(o["sources"]["facts"]["count"], 0)
        self.assertEqual(o["sources"]["index"]["status"], "skipped_targeted")
        self.assertEqual(o["sources"]["index"]["count"], 0)
        self.assertEqual(o["sources"]["learnings"]["count"], 1)
        self.assertEqual(o["sources"]["history"]["count"], 4)
        self.assertEqual(o["explanation"]["hit_counts"]["total"], 5)
        self.assertIn("targeted_recall", o["explanation"]["reason_codes"])
        self.assertEqual(
            {row["source"] for row in o["explanation"]["omitted_sources"]},
            {"MEMORY.md", "USER.md", "FACTS.jsonl", "RECALL.sqlite"},
        )

    def test_global_packer_respects_budget_hit_cap_and_deduplicates(self):
        self.write("MEMORY.md", "small always on context\n")
        self.write("USER.md", "concise local answers\n")
        self.write(
            "FACTS.jsonl",
            json.dumps({"kind": "module", "area": "core", "path": "src/primary.py",
                        "line": 1, "summary": "auth token rotation"}) + "\n"
        )
        self.write(
            "LEARNINGS.jsonl",
            "".join([
                json.dumps({"id": "dup", "status": "current", "summary": "auth token rotation",
                            "evidence": ["src/primary.py:1"]}) + "\n",
                json.dumps({"id": "extra1", "status": "current", "summary": "auth token helper",
                            "evidence": ["src/extra1.py:2"]}) + "\n",
                json.dumps({"id": "extra2", "status": "current", "summary": "auth token fallback",
                            "evidence": ["src/extra2.py:3"]}) + "\n",
            ])
        )
        with mock.patch.dict(os.environ, {"KIMIFLOW_RECALL_BUDGET": "100"}):
            o = self.obj("auth token", max_hits=3)

        hits = []
        for source in ("facts", "learnings", "index", "history"):
            hits.extend(o["sources"][source]["hits"])
        refs = [recall.hit_ref(hit) for hit in hits]
        self.assertEqual(o["schema_version"], 2)
        self.assertLessEqual(o["budget"]["used"], o["budget"]["limit"])
        self.assertLessEqual(o["explanation"]["hit_counts"]["total"], 3)
        self.assertIn("src/primary.py:1", refs)
        self.assertEqual(len(refs), len(set(refs)))
        self.assertGreaterEqual(o["budget"]["duplicates_removed"], 1)

    def test_current_source_rule_and_direct_fact_dedup(self):
        self.write(
            "FACTS.jsonl",
            json.dumps({"kind": "module", "area": "storage", "path": "src/store.py",
                        "line": 12, "summary": "transaction manager invariant"}) + "\n"
        )
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "old", "status": "current", "summary": "transaction manager invariant",
                        "evidence": ["src/store.py:12"]}) + "\n"
        )
        o = self.obj("transaction manager", max_hits=5)
        self.assertEqual(o["authority"]["rule"], "current_project_sources_override_recall")
        self.assertEqual(o["authority"]["recall_status"], "advisory")
        self.assertEqual(o["sources"]["facts"]["count"], 1)
        self.assertEqual(o["sources"]["learnings"]["count"], 0)
        self.assertEqual(o["sources"]["facts"]["hits"][0]["path"], "src/store.py")

    def test_same_source_summaries_are_deduplicated(self):
        self.write(
            "FACTS.jsonl",
            "".join(
                json.dumps({"kind": "module", "path": "src/%s.py" % name,
                            "summary": "auth token invariant"}) + "\n"
                for name in ("one", "two")
            ),
        )
        o = self.obj("auth token", max_hits=5)
        self.assertEqual(o["sources"]["facts"]["count"], 1)
        self.assertEqual(o["budget"]["duplicates_removed"], 1)

    def test_included_memory_is_not_repeated_by_index(self):
        self.write("MEMORY.md", "personal auth preference\n")
        db = recall_index.recall_db_path(self.root)
        self.assertEqual(recall_index.build_recall_index(self.root, db), 0)
        o = self.obj("personal auth", max_hits=5)
        self.assertEqual(o["sources"]["memory"]["status"], "included")
        self.assertEqual(o["sources"]["index"]["count"], 0)
        self.assertGreaterEqual(o["budget"]["duplicates_removed"], 1)

    def test_included_memory_content_is_not_repeated_by_learning(self):
        self.write("MEMORY.md", "personal auth preference\n")
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "old", "status": "current",
                        "summary": "personal auth preference",
                        "evidence": ["src/profile.py:7"]}) + "\n",
        )
        o = self.obj("personal auth", max_hits=5)
        self.assertEqual(o["sources"]["memory"]["status"], "included")
        self.assertEqual(o["sources"]["learnings"]["count"], 0)
        self.assertGreaterEqual(o["budget"]["duplicates_removed"], 1)

    def test_direct_fact_wins_duplicate_before_coverage_ranking(self):
        self.write(
            "FACTS.jsonl",
            json.dumps({"kind": "module", "path": "src/x.py", "line": 1,
                        "summary": "alpha invariant"}) + "\n",
        )
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "old", "status": "current", "topic": "beta",
                        "summary": "alpha invariant", "evidence": ["src/x.py:1"]}) + "\n",
        )
        o = self.obj("alpha beta", max_hits=1)
        self.assertEqual(o["sources"]["facts"]["count"], 1)
        self.assertEqual(o["sources"]["learnings"]["count"], 0)

    def test_direct_fact_outside_window_inherits_duplicate_group_relevance(self):
        facts = [
            {"kind": "module", "path": "src/other%02d.py" % i, "line": 1,
             "summary": "alpha beta helper %02d" % i}
            for i in range(20)
        ]
        facts.append({"kind": "module", "path": "src/target.py", "line": 7,
                      "summary": "alpha invariant"})
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in facts))
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "old", "status": "current", "topic": "beta gamma",
                        "summary": "alpha invariant",
                        "evidence": ["src/target.py:7"]}) + "\n",
        )
        o = self.obj("alpha beta gamma", max_hits=1)
        self.assertEqual(o["sources"]["facts"]["count"], 1)
        self.assertEqual(o["sources"]["facts"]["hits"][0]["path"], "src/target.py")
        self.assertEqual(o["sources"]["learnings"]["count"], 0)

    def test_transitive_duplicate_identity_group_emits_one_representative(self):
        selected, _, duplicates, _ = recall._pack_hits(
            {
                "facts": [
                    {"ref": "src/a.py:1", "summary": "first identity"},
                    {"ref": "src/b.py:1", "summary": "second identity"},
                ],
                "learnings": [
                    {"ref": "src/a.py:1", "summary": "second identity"},
                ],
            },
            ["identity"], 10, 1000,
        )
        self.assertEqual(selected["facts"], [
            {"ref": "src/a.py:1", "summary": "first identity"},
        ])
        self.assertEqual(selected["learnings"], [])
        self.assertEqual(duplicates, 2)

    def test_out_of_window_bridge_closes_transitive_duplicate_group(self):
        facts = [
            {"kind": "module", "area": "beta", "path": "src/shared.py", "line": 1,
             "summary": "alpha primary"},
        ]
        facts.extend(
            {"kind": "module", "path": "src/filler%02d.py" % i,
             "summary": "alpha filler %02d" % i}
            for i in range(19)
        )
        facts.append(
            {"kind": "module", "path": "src/shared.py", "line": 1,
             "summary": "alpha bridge"}
        )
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in facts))
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "endpoint", "status": "current", "topic": "beta",
                        "summary": "alpha bridge",
                        "evidence": ["src/endpoint.py:1"]}) + "\n",
        )
        o = self.obj("alpha beta", max_hits=3)
        self.assertEqual(o["sources"]["facts"]["hits"][0]["path"], "src/shared.py")
        self.assertEqual(o["sources"]["learnings"]["count"], 0)
        self.assertGreaterEqual(o["budget"]["duplicates_removed"], 2)

    def test_out_of_window_multi_bridge_closes_to_fixpoint(self):
        facts = [
            {"kind": "module", "area": "beta", "path": "src/a.py", "line": 1,
             "summary": "alpha primary"},
        ]
        facts.extend(
            {"kind": "module", "path": "src/filler%02d.py" % i,
             "summary": "alpha filler %02d" % i}
            for i in range(39)
        )
        facts.extend([
            {"kind": "module", "path": "src/a.py", "line": 1,
             "summary": "alpha middle"},
            {"kind": "module", "path": "src/b.py", "line": 1,
             "summary": "alpha middle"},
        ])
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in facts))
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "endpoint", "status": "current", "topic": "beta",
                        "summary": "alpha endpoint", "evidence": ["src/b.py:1"]}) + "\n",
        )
        o = self.obj("alpha beta", max_hits=10)
        self.assertEqual(o["sources"]["facts"]["hits"][0]["path"], "src/a.py")
        self.assertEqual(o["sources"]["learnings"]["count"], 0)
        self.assertGreaterEqual(o["budget"]["duplicates_removed"], 3)

    def test_over_limit_duplicate_chain_fails_closed(self):
        facts = [
            {"kind": "module", "area": "beta", "path": "src/r0.py", "line": 1,
             "summary": "alpha s0"},
        ]
        facts.extend(
            {"kind": "module", "path": "src/filler%02d.py" % i,
             "summary": "alpha filler %02d" % i}
            for i in range(19)
        )
        for i in range(11):
            facts.extend([
                {"kind": "module", "path": "src/r%d.py" % i, "line": 1,
                 "summary": "alpha s%d" % (i + 1)},
                {"kind": "module", "path": "src/r%d.py" % (i + 1), "line": 1,
                 "summary": "alpha s%d" % (i + 1)},
            ])
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in facts))
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "endpoint", "status": "current", "topic": "beta",
                        "summary": "alpha endpoint", "evidence": ["src/r11.py:1"]}) + "\n",
        )
        o = self.obj("alpha beta", max_hits=5)
        self.assertEqual(o["sources"]["facts"]["hits"][0]["path"], "src/r0.py")
        self.assertEqual(o["sources"]["learnings"]["count"], 0)
        self.assertIn("duplicate_closure_truncated", o["explanation"]["reason_codes"])
        recall_index.build_recall_index(
            self.root, recall_index.recall_db_path(self.root)
        )
        indexed = self.obj("alpha beta", max_hits=5)
        self.assertEqual(indexed["sources"]["learnings"]["count"], 0)
        self.assertIn(
            "duplicate_closure_truncated",
            indexed["explanation"]["reason_codes"],
        )
        os.makedirs(os.path.join(self.root, "packages", "api", "src"))
        with open(
            os.path.join(self.root, "packages", "api", "package.json"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("{}\n")
        scoped = recall.recall_json(
            self.root, "alpha beta", 5,
            scope_paths=["packages/api/src/main.py"],
        )
        self.assertEqual(scoped["sources"]["learnings"]["count"], 0)
        self.assertIn(
            "duplicate_closure_truncated",
            scoped["explanation"]["reason_codes"],
        )

    def test_seeded_duplicate_closure_does_not_retain_chain_after_cap(self):
        def chain(_path, completion=None):
            for index in range(2000):
                current = ("x" * 4096) + str(index)
                yield {
                    "status": "current",
                    "ref": "seed",
                    "summary": current,
                }
            if completion is not None:
                completion["complete"] = True

        tracemalloc.start()
        try:
            with mock.patch(
                "memory_router.recall._iter_jsonl_objects_with_receipt",
                side_effect=chain,
            ):
                hits, unsafe_refs, _ = recall._preferred_duplicates(
                    "unused", ["query"], "ref,summary", [],
                    [{"ref": "seed"}], 1,
                    seed_match=lambda _row: True,
                )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(len(hits), 1)
        self.assertEqual(unsafe_refs, {"seed"})
        self.assertLess(peak, 2 * 1024 * 1024)

    def test_seeded_duplicate_closure_revokes_late_bridge_exemptions(self):
        seeds = [
            {"ref": "manual:a", "summary": "seed-a"},
            {"ref": "manual:b", "summary": "bridge-b"},
        ]

        def rows(_path, completion=None):
            yield {"status": "current", **seeds[0]}
            yield {
                "status": "current",
                "ref": "manual:a",
                "summary": "bridge-b",
            }
            yield {"status": "current", **seeds[1]}
            if completion is not None:
                completion["complete"] = True

        with mock.patch(
            "memory_router.recall._iter_jsonl_objects_with_receipt",
            side_effect=rows,
        ):
            _, unsafe_refs, unsafe_summaries = recall._preferred_duplicates(
                "unused", ["query"], "ref,summary", [], seeds, 1,
                seed_match=lambda _row: True,
                safe_lower_hits=seeds,
            )
        self.assertEqual(unsafe_refs, {"manual:a", "manual:b"})
        self.assertEqual(unsafe_summaries, {"seed-a", "bridge-b"})

    def test_seeded_duplicate_closure_binds_long_full_summary_aliases(self):
        long_a = "a" * 500
        long_b = "b" * 500
        seeds = [
            {"ref": "manual:a", "summary": long_a[:420]},
            {"ref": "manual:b", "summary": long_b[:420]},
        ]

        def rows(_path, completion=None):
            yield {"status": "current", "ref": "manual:a", "summary": long_a}
            yield {"status": "current", "ref": "manual:b", "summary": long_b}
            yield {"status": "current", "ref": "bridge", "summary": long_a}
            yield {"status": "current", "ref": "bridge", "summary": long_b}
            if completion is not None:
                completion["complete"] = True

        with mock.patch(
            "memory_router.recall._iter_jsonl_objects_with_receipt",
            side_effect=rows,
        ):
            _, unsafe_refs, unsafe_summaries = recall._preferred_duplicates(
                "unused", ["query"], "ref,summary", [], seeds, 1,
                seed_match=lambda _row: True,
                safe_lower_hits=seeds,
                safe_match_key=lambda hit: (
                    recall.hit_ref(hit), recall._normalized_summary(hit)[:420]
                ),
            )
        self.assertEqual(unsafe_refs, {"manual:a", "manual:b"})
        self.assertEqual(unsafe_summaries, {long_a[:420], long_b[:420]})

    def test_seeded_duplicate_closure_rejects_ambiguous_shadow_aliases(self):
        prefix = "x" * 420
        seed = {"ref": "manual:a", "summary": prefix}

        def rows(_path, completion=None):
            for index in range(100):
                yield {
                    "status": "current",
                    "ref": "manual:a",
                    "summary": prefix + str(index),
                }
            if completion is not None:
                completion["complete"] = True

        with mock.patch(
            "memory_router.recall._iter_jsonl_objects_with_receipt",
            side_effect=rows,
        ):
            _, unsafe_refs, _ = recall._preferred_duplicates(
                "unused", ["query"], "ref,summary", [], [seed], 1,
                seed_match=lambda _row: True,
                safe_lower_hits=[seed],
                safe_match_key=lambda hit: (
                    recall.hit_ref(hit), recall._normalized_summary(hit)[:420]
                ),
            )
        self.assertEqual(unsafe_refs, {"manual:a"})

    def test_seeded_duplicate_closure_hashes_large_full_alias_state(self):
        seeds = [
            {"ref": "manual:%d" % index, "summary": ("s%03d" % index) * 105}
            for index in range(128)
        ]

        def rows(_path, completion=None):
            for seed in seeds:
                yield {
                    "status": "current",
                    "ref": seed["ref"],
                    "summary": seed["summary"] + ("z" * (64 * 1024)),
                }
            if completion is not None:
                completion["complete"] = True

        tracemalloc.start()
        try:
            with mock.patch(
                "memory_router.recall._iter_jsonl_objects_with_receipt",
                side_effect=rows,
            ):
                _, unsafe_refs, _ = recall._preferred_duplicates(
                    "unused", ["query"], "ref,summary", [], seeds, 1,
                    seed_match=lambda _row: True,
                    safe_lower_hits=seeds,
                    safe_match_key=lambda hit: (
                        recall.hit_ref(hit), recall._normalized_summary(hit)[:420]
                    ),
                )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(unsafe_refs, set())
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_seed_safety_uses_only_the_final_complete_accepted_scan(self):
        seeds = [
            {"ref": "manual:a", "summary": "seed-a"},
            {"ref": "manual:c", "summary": "seed-c"},
        ]
        scans = [
            [
                {"status": "current", **seeds[0]},
                {"status": "current", "ref": "manual:a",
                 "summary": "transient", "foreign": True},
            ],
            [
                {"status": "current", **seeds[0]},
                {"status": "current", **seeds[1]},
                {"status": "current", "ref": "manual:a",
                 "summary": "rejected", "foreign": True},
                {"status": "current", "ref": "manual:c",
                 "summary": "accepted-bridge"},
            ],
        ]

        def rows(_path, completion=None):
            current = scans.pop(0)
            yield from current
            if completion is not None:
                completion["complete"] = True

        def accept_factory(_observations):
            return lambda row: not row.get("foreign", False)

        with mock.patch(
            "memory_router.recall._iter_jsonl_objects_with_receipt",
            side_effect=rows,
        ):
            _, unsafe_refs, _ = recall._preferred_duplicates(
                "unused", ["query"], "ref,summary", [], seeds, 2,
                seed_match=lambda _row: True,
                safe_lower_hits=seeds,
                safe_match_key=lambda hit: (
                    recall.hit_ref(hit), recall._normalized_summary(hit)
                ),
                accept_factory=accept_factory,
            )
        self.assertEqual(unsafe_refs, {"manual:c"})

    def test_incomplete_seed_scan_keeps_unobserved_independent_shadow_safe(self):
        seeds = [
            {"ref": "manual:a", "summary": "seed-a"},
            {"ref": "manual:b", "summary": "seed-b"},
        ]

        def interrupted(_path, completion=None):
            yield {
                "status": "current",
                "ref": "manual:a",
                "summary": "bridge-a",
            }
            yield {
                "status": "current",
                "ref": "manual:a",
                "summary": "late-a",
            }

        with mock.patch(
            "memory_router.recall._iter_jsonl_objects_with_receipt",
            side_effect=interrupted,
        ):
            _, unsafe_refs, _ = recall._preferred_duplicates(
                "unused", ["query"], "ref,summary", [], seeds, 1,
                seed_match=lambda _row: True,
                safe_lower_hits=seeds,
                safe_match_key=lambda hit: (
                    recall.hit_ref(hit), recall._normalized_summary(hit)
                ),
            )
        self.assertEqual(unsafe_refs, {"manual:a"})

    def test_source_candidates_rank_before_bounded_window(self):
        rows = [
            {"kind": "module", "area": "core", "path": "src/partial%02d.py" % i,
             "summary": "offline helper"}
            for i in range(20)
        ]
        rows.append({"kind": "module", "area": "core", "path": "src/best.py",
                     "summary": "offline sync conflict resolution"})
        self.write("FACTS.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        o = self.obj("offline sync conflict resolution", max_hits=1)
        self.assertEqual(o["sources"]["facts"]["hits"][0]["path"], "src/best.py")

    def test_durable_learning_ranks_before_probationary_but_both_remain_recallable(self):
        rows = [
            {"id": "probationary", "status": "current", "maturity": "probationary",
             "summary": "offline sync probation strategy", "evidence": ["src/new.py:1"]},
            {"id": "durable", "status": "current", "maturity": "durable",
             "summary": "offline sync durable strategy", "evidence": ["src/proven.py:1"]},
        ]
        self.write("LEARNINGS.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        preferred = self.obj("offline sync strategy", max_hits=1)
        self.assertEqual(preferred["sources"]["learnings"]["hits"][0]["id"], "durable")
        targeted = self.obj("offline sync strategy", max_hits=5, targeted=True)
        self.assertEqual(
            {row["id"] for row in targeted["sources"]["learnings"]["hits"]},
            {"durable", "probationary"},
        )

    def test_final_pack_keeps_durable_before_more_relevant_probationary(self):
        rows = [
            {"id": "durable", "status": "current", "maturity": "durable",
             "summary": "offline helper", "evidence": ["src/proven.py:1"]},
            {"id": "probationary", "status": "current", "maturity": "probationary",
             "summary": "offline sync conflict resolution",
             "evidence": ["src/new.py:1"]},
        ]
        self.write("LEARNINGS.jsonl", "".join(
            json.dumps(row) + "\n" for row in rows
        ))

        result = self.obj(
            "offline sync conflict resolution",
            max_hits=1,
            targeted=True,
        )

        self.assertEqual(
            result["sources"]["learnings"]["hits"][0]["id"],
            "durable",
        )

    def test_scoped_all_learning_duplicate_keeps_durable_representative(self):
        durable = {
            "id": "durable",
            "status": "current",
            "maturity": "durable",
            "summary": "offline sync invariant",
            "evidence": ["src/shared.py:1"],
        }
        probationary = {
            "id": "probationary",
            "status": "current",
            "maturity": "probationary",
            "summary": "offline sync invariant",
            "evidence": ["src/new.py:1"],
        }

        selected, _used, _duplicates, _omitted = recall._pack_hits(
            {"learnings": [durable, probationary]},
            ["offline", "sync"],
            1,
            1000,
            locality=lambda _source, hit: (
                1 if hit.get("id") == "probationary" else 0
            ),
        )

        self.assertEqual(selected["learnings"], [durable])

    def test_scoped_candidate_window_cannot_starve_shared_durable_learning(self):
        package = os.path.join(self.root, "packages", "api")
        os.makedirs(os.path.join(package, "src"))
        with open(os.path.join(package, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        candidates = [
            {
                "id": "local-%02d" % index,
                "status": "current",
                "maturity": "probationary",
                "summary": "offline sync strategy",
                "evidence": ["packages/api/src/local-%02d.py:1" % index],
            }
            for index in range(20)
        ]
        candidates.append({
            "id": "shared-durable",
            "status": "current",
            "maturity": "durable",
            "summary": "offline sync strategy",
            "evidence": ["manual:shared"],
        })
        self.write("LEARNINGS.jsonl", "".join(
            json.dumps(row) + "\n" for row in candidates
        ))

        result = recall.recall_json(
            self.root,
            "offline sync strategy",
            1,
            targeted=True,
            scope_paths=["packages/api/src/main.py"],
        )

        self.assertEqual(
            result["sources"]["learnings"]["hits"][0]["id"],
            "shared-durable",
        )

    def test_write_serializes_recall_and_usage_recording_on_usage_ledger(self):
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({
                "id": "strategy",
                "status": "current",
                "summary": "offline sync strategy",
                "evidence": ["manual:strategy"],
            }) + "\n",
        )
        usage_path = usage_metrics.usage_lock_path(self.root)
        real_lock = store.path_lock
        usage_held = [False]

        @contextlib.contextmanager
        def tracked_lock(path):
            with real_lock(path):
                is_usage = os.path.abspath(path) == os.path.abspath(usage_path)
                if is_usage:
                    usage_held[0] = True
                try:
                    yield
                finally:
                    if is_usage:
                        usage_held[0] = False

        def checked_update(*_args, **_kwargs):
            self.assertTrue(usage_held[0])

        with mock.patch.object(store, "path_lock", new=tracked_lock), \
                mock.patch.object(usage_metrics, "update_usage_metrics",
                                  side_effect=checked_update), \
                contextlib.redirect_stdout(io.StringIO()):
            code = recall.run([
                "--root", self.root,
                "--query", "offline sync strategy",
                "--write", "RECALL.md",
            ])

        self.assertEqual(code, 0)

    def test_emitted_mapping_keys_count_toward_global_budget(self):
        row = {"kind": "module", "path": "src/auth.py", "summary": "auth token"}
        row.update({("very_long_untrusted_key_%04d_" % i) + ("x" * 40): None
                    for i in range(100)})
        self.write("FACTS.jsonl", json.dumps(row) + "\n")
        with mock.patch.dict(os.environ, {"KIMIFLOW_RECALL_BUDGET": "20"}):
            o = self.obj("auth token", max_hits=1)
        self.assertEqual(o["sources"]["facts"]["count"], 0)
        self.assertEqual(o["budget"]["hits_omitted"], 1)

    def test_nested_container_shape_counts_toward_global_budget(self):
        payload = "leaf"
        for _ in range(250):
            payload = [payload]
        row = {"kind": "module", "path": "src/auth.py", "summary": "auth token",
               "payload": payload}
        self.write("FACTS.jsonl", json.dumps(row) + "\n")
        with mock.patch.dict(os.environ, {"KIMIFLOW_RECALL_BUDGET": "20"}):
            o = self.obj("auth token", max_hits=1)
        self.assertEqual(o["sources"]["facts"]["count"], 0)
        self.assertEqual(o["budget"]["hits_omitted"], 1)

    def test_bounded_read_stops_oversized_first_line(self):
        path = os.path.join(self.project, "oversized.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x" * 1000000)
        content, overflow = recall._bounded_read(path, 160, 80)
        self.assertTrue(overflow)
        self.assertLessEqual(len(content), 80)

    def test_direct_candidate_window_is_bounded_after_relevance_ranking(self):
        rows = [
            {"id": "partial-%04d" % i, "status": "current",
             "summary": "offline helper", "evidence": ["src/%04d.py:1" % i]}
            for i in range(1000)
        ]
        rows.append({"id": "best", "status": "current",
                     "summary": "offline sync conflict resolution",
                     "evidence": ["src/best.py:1"]})
        self.write("LEARNINGS.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
        real_packer = recall._pack_hits
        with mock.patch("memory_router.recall._pack_hits", wraps=real_packer) as packer:
            o = self.obj("offline sync conflict resolution", max_hits=1)
        candidates = packer.call_args.args[0]["learnings"]
        self.assertLessEqual(len(candidates), 64)
        self.assertEqual(o["sources"]["learnings"]["hits"][0]["id"], "best")

    def test_candidate_window_is_bounded_when_user_max_is_huge(self):
        real_rank = recall._ranked_jsonl_hits
        with mock.patch("memory_router.recall._ranked_jsonl_hits",
                        wraps=real_rank) as ranked:
            self.obj("auth", max_hits=1000000000)
        self.assertTrue(ranked.call_args_list)
        self.assertTrue(all(call.args[2] <= 1800 for call in ranked.call_args_list))

    def test_stale_index_is_bypassed_then_rebuilt_on_write(self):
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "strategy", "status": "current", "summary": "old auth strategy"}) + "\n"
        )
        db = recall_index.recall_db_path(self.root)
        self.assertEqual(recall_index.build_recall_index(self.root, db), 0)
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "strategy", "status": "current", "summary": "new auth strategy"}) + "\n"
        )

        stale = self.obj("old auth", max_hits=5)
        self.assertEqual(stale["sources"]["index"]["freshness"], "stale")
        self.assertEqual(stale["sources"]["index"]["status"], "stale_bypassed")
        self.assertEqual(stale["sources"]["index"]["hits"], [])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = recall.run([
                "--root", self.root, "--query", "new auth", "--write", "RECALL.md"
            ])
        self.assertEqual(code, 0)
        refreshed = json.loads(out.getvalue())
        self.assertEqual(refreshed["sources"]["index"]["freshness"], "fresh")
        self.assertEqual(refreshed["sources"]["index"]["refresh"], "rebuilt")
        self.assertEqual(recall_index.index_state(self.root)["status"], "fresh")
        self.assertEqual(
            [hit["summary"] for hit in recall_index.fts_hits_json(self.root, ["new", "auth"], 5)],
            ["new auth strategy"],
        )

    def test_corrupt_index_is_bypassed_then_rebuilt_on_write(self):
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "strategy", "status": "current",
                        "summary": "current auth strategy"}) + "\n",
        )
        db = recall_index.recall_db_path(self.root)
        self.assertEqual(recall_index.build_recall_index(self.root, db), 0)
        con = sqlite3.connect(db)
        con.execute("DROP TABLE recall_fts")
        con.commit()
        con.close()

        self.assertEqual(recall_index.index_state(self.root)["status"], "corrupt")
        refreshed = recall.recall_json(
            self.root, "current auth", 5, refresh_index=True
        )
        self.assertEqual(refreshed["sources"]["index"]["freshness"], "fresh")
        self.assertEqual(refreshed["sources"]["index"]["refresh"], "rebuilt")

    def test_write_rechecks_and_rebuilds_after_fresh_snapshot_drifts(self):
        self.write(
            "LEARNINGS.jsonl",
            json.dumps({"id": "old", "status": "current",
                        "summary": "old auth strategy"}) + "\n",
        )
        db = recall_index.recall_db_path(self.root)
        self.assertEqual(recall_index.build_recall_index(self.root, db), 0)
        real_fingerprint = recall_index.corpus_fingerprint
        mutated = False

        def drift_after_snapshot(root):
            nonlocal mutated
            fingerprint = real_fingerprint(root)
            if not mutated:
                mutated = True
                self.write(
                    "LEARNINGS.jsonl",
                    json.dumps({"id": "new", "status": "current",
                                "summary": "new auth strategy"}) + "\n",
                )
            return fingerprint

        with mock.patch("memory_router.recall_index.corpus_fingerprint",
                        side_effect=drift_after_snapshot):
            refreshed = recall.recall_json(
                self.root, "new auth", 5, refresh_index=True
            )
        self.assertEqual(refreshed["sources"]["index"]["freshness"], "fresh")
        self.assertEqual(refreshed["sources"]["index"]["refresh"], "rebuilt")
        self.assertEqual(recall_index.index_state(self.root)["status"], "fresh")

    def test_read_only_final_stale_state_clears_previously_fetched_index_hits(self):
        self.write(
            "USER.jsonl",
            json.dumps({"id": "u1", "status": "current",
                        "summary": "old auth strategy"}) + "\n",
        )
        db = recall_index.recall_db_path(self.root)
        self.assertEqual(recall_index.build_recall_index(self.root, db), 0)
        real_fingerprint = recall_index.corpus_fingerprint
        mutated = False

        def drift_before_final_state(root):
            nonlocal mutated
            fingerprint = real_fingerprint(root)
            if not mutated:
                mutated = True
                self.write(
                    "USER.jsonl",
                    json.dumps({"id": "u2", "status": "current",
                                "summary": "new auth strategy"}) + "\n",
                )
            return fingerprint

        with mock.patch("memory_router.recall_index.corpus_fingerprint",
                        side_effect=drift_before_final_state):
            result = self.obj("old auth", max_hits=5)
        self.assertEqual(result["sources"]["index"]["freshness"], "stale")
        self.assertEqual(result["sources"]["index"]["status"], "stale_bypassed")
        self.assertEqual(result["sources"]["index"]["hits"], [])

    def test_fresh_read_only_index_validation_uses_bounded_full_scans(self):
        self.write("USER.jsonl", json.dumps({
            "id": "u1", "status": "current", "summary": "auth strategy",
        }) + "\n")
        db = recall_index.recall_db_path(self.root)
        self.assertEqual(recall_index.build_recall_index(self.root, db), 0)
        with mock.patch(
                "memory_router.recall_index.corpus_fingerprint",
                wraps=recall_index.corpus_fingerprint) as corpus_scan, mock.patch(
                "memory_router.recall_index._fts_content_fingerprint",
                wraps=recall_index._fts_content_fingerprint) as content_scan:
            result = self.obj("auth", max_hits=1)
        self.assertEqual(result["sources"]["index"]["freshness"], "fresh")
        self.assertLessEqual(corpus_scan.call_count, 2)
        self.assertLessEqual(content_scan.call_count, 1)


class RecallRunCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
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

    def test_first_persisted_recall_creates_usage_parent_before_locking(self):
        shutil.rmtree(self.project)
        real_lock = store.path_lock
        usage_parent_seen = []

        @contextlib.contextmanager
        def checked_lock(path):
            if os.path.basename(path) == "MEMORY-USAGE.json":
                usage_parent_seen.append(os.path.isdir(os.path.dirname(path)))
            with real_lock(path):
                yield

        with mock.patch.object(store, "path_lock", new=checked_lock):
            code, _out, _err = self.run_recall([
                "--query", "first recall",
                "--write", "RECALL.md",
            ])

        self.assertEqual(code, 0)
        self.assertTrue(usage_parent_seen)
        self.assertTrue(all(usage_parent_seen))

    def test_persisted_recall_pins_workspace_before_alias_retarget(self):
        with open(os.path.join(self.project, "LEARNINGS.jsonl"), "w",
                  encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "A", "status": "current",
                "summary": "alpha strategy", "evidence": ["manual:a"],
            }) + "\n")
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        other_project = os.path.join(other, ".kimiflow", "project")
        os.makedirs(other_project)
        with open(os.path.join(other_project, "LEARNINGS.jsonl"), "w",
                  encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "B", "status": "current",
                "summary": "beta strategy", "evidence": ["manual:b"],
            }) + "\n")
        alias = self.root + "-alias"
        os.symlink(self.root, alias)
        self.addCleanup(lambda: os.path.lexists(alias) and os.unlink(alias))
        real_lock = store.path_lock
        retargeted = [False]

        @contextlib.contextmanager
        def retarget_before_lock(path):
            if not retargeted[0]:
                retargeted[0] = True
                os.unlink(alias)
                os.symlink(other, alias)
            with real_lock(path):
                yield

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(store, "path_lock", new=retarget_before_lock), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = recall.run([
                "--root", alias, "--query", "alpha strategy",
                "--write", "RECALL.md",
            ])

        self.assertEqual(code, 0, err.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(self.root, "RECALL.md")))
        usage = store.read_json(os.path.join(self.project, "MEMORY-USAGE.json"))
        self.assertIn("learning:A", usage["items"])
        self.assertNotIn("learning:B", usage["items"])
        self.assertFalse(os.path.exists(os.path.join(other, "RECALL.md")))

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

    def test_targeted_flag_reaches_recall_contract(self):
        with open(os.path.join(self.project, "MEMORY.md"), "w", encoding="utf-8") as fh:
            fh.write("auth broad memory\n")
        code, out, err = self.run_recall(["--query", "auth", "--targeted", "--max", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        obj = json.loads(out)
        self.assertEqual(obj["sources"]["memory"]["status"], "omitted_targeted")
        self.assertIn("targeted_recall", obj["explanation"]["reason_codes"])

    def test_strategies_flag_reaches_recall_contract(self):
        strategy_source = {
            "path": ".kimiflow/project/STRATEGY-OUTCOMES.jsonl",
            "status": "used",
            "count": 1,
            "hits": [{
                "id": "out_1",
                "classification": "verified_success",
                "strategy": "Reuse the verified local strategy",
            }],
        }
        with mock.patch(
            "memory_router.recall.outcomes.strategy_recall_json",
            return_value=strategy_source,
        ) as strategy_recall:
            code, out, err = self.run_recall([
                "--query", "auth", "--strategies", "--write", "RECALL.md"
            ])
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        obj = json.loads(out)
        selected = obj["sources"]["strategies"]
        self.assertEqual({key: selected[key] for key in ("path", "status", "count")},
                         {key: strategy_source[key] for key in ("path", "status", "count")})
        self.assertRegex(selected["hits"][0].pop("recall_id"), r"^rec_[0-9a-f]{64}$")
        self.assertEqual(selected["hits"], strategy_source["hits"])
        strategy_recall.assert_called_once_with(self.root, obj["query_terms"], mode="")
        self.assertIn("## Strategy Outcomes", _read(os.path.join(self.root, "RECALL.md")))

    def test_strategies_infers_mode_from_query_artifact_state(self):
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(run_dir)
        query = os.path.join(run_dir, "INTENT.md")
        with open(query, "w", encoding="utf-8") as fh:
            fh.write("authentication strategy\n")
        with open(os.path.join(run_dir, "STATE.md"), "w", encoding="utf-8") as fh:
            fh.write("Mode: feature\n")
        with mock.patch(
            "memory_router.recall.outcomes.strategy_recall_json",
            return_value={"path": "x", "status": "missing", "count": 0, "hits": []},
        ) as strategy_recall:
            code, _, _ = self.run_recall(["--query-file", query, "--strategies"])
        self.assertEqual(code, 0)
        strategy_recall.assert_called_once_with(
            self.root,
            ["authentication", "strategy"],
            mode="feature",
        )

    def test_query_file_infers_state_scope_and_explicit_paths_win(self):
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w") as handle:
                handle.write("{}\n")
        with open(os.path.join(self.project, "FACTS.jsonl"), "w") as handle:
            handle.write('{"path":"packages/api/src/a.py","line":1,"summary":"auth api"}\n')
            handle.write('{"path":"packages/web/src/a.py","line":1,"summary":"auth web"}\n')
        run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(run)
        query = os.path.join(run, "INTENT.md")
        with open(query, "w") as handle:
            handle.write("auth\n")
        with open(os.path.join(run, "STATE.md"), "w") as handle:
            handle.write("Affected files:\n- packages/api/src/main.py\nPhase 1: done\n")

        code, out, err = self.run_recall(["--query-file", query, "--max", "5"])
        self.assertEqual((code, err), (0, ""))
        inferred = json.loads(out)
        self.assertEqual(inferred["workspace_scope"]["units"][0]["path"], "packages/api")
        self.assertEqual(
            [row["path"] for row in inferred["sources"]["facts"]["hits"]],
            ["packages/api/src/a.py"],
        )

        code, out, err = self.run_recall([
            "--query-file", query, "--scope-path", "packages/web/src/main.py", "--max", "5"
        ])
        self.assertEqual((code, err), (0, ""))
        explicit = json.loads(out)
        self.assertEqual(explicit["workspace_scope"]["units"][0]["path"], "packages/web")
        self.assertEqual(
            [row["path"] for row in explicit["sources"]["facts"]["hits"]],
            ["packages/web/src/a.py"],
        )

        original_pack = recall._pack_hits

        def mutate_state_after_pack(*args, **kwargs):
            packed = original_pack(*args, **kwargs)
            with open(os.path.join(run, "STATE.md"), "a", encoding="utf-8") as handle:
                handle.write("# concurrent state change\n")
            return packed

        with mock.patch(
            "memory_router.recall._pack_hits", side_effect=mutate_state_after_pack
        ):
            code, out, err = self.run_recall(["--query-file", query, "--max", "5"])
        self.assertEqual((code, err), (0, ""))
        drifted = json.loads(out)
        self.assertEqual(drifted["workspace_scope"]["status"], "fallback")
        self.assertEqual(
            drifted["workspace_scope"]["reason"], "scope_changed_during_recall"
        )
        self.assertEqual(
            {row["path"] for row in drifted["sources"]["facts"]["hits"]},
            {"packages/api/src/a.py", "packages/web/src/a.py"},
        )

        external = tempfile.mkdtemp(prefix="kimiflow-foreign-run-")
        self.addCleanup(shutil.rmtree, external, ignore_errors=True)
        foreign_run = os.path.join(external, ".kimiflow", "demo")
        os.makedirs(foreign_run)
        foreign_query = os.path.join(foreign_run, "INTENT.md")
        with open(foreign_query, "w", encoding="utf-8") as handle:
            handle.write("auth\n")
        with open(os.path.join(foreign_run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Affected files:\n- packages/web/src/main.py\n")
        code, out, err = self.run_recall(["--query-file", foreign_query, "--max", "5"])
        self.assertEqual((code, err), (0, ""))
        foreign = json.loads(out)
        self.assertNotIn("workspace_scope", foreign)
        self.assertEqual(
            {row["path"] for row in foreign["sources"]["facts"]["hits"]},
            {"packages/api/src/a.py", "packages/web/src/a.py"},
        )

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
            fh.write('{"id":"L1","status":"current","summary":"auth learned behavior"}\n')
        with open(os.path.join(self.project, "FACTS.jsonl"), "w", encoding="utf-8") as fh:
            fh.write('{"kind":"module","area":"core","path":"auth.py","summary":"auth project map"}\n')
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
        self.assertIn('"schema_version":2', out.getvalue())


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

    def test_shared_usage_writer_locks_before_reading_current_state(self):
        real_lock = store.path_lock
        real_read = store.read_json
        usage_path = usage_metrics.usage_lock_path(self.root)
        usage_held = [False]

        @contextlib.contextmanager
        def tracked_lock(path):
            with real_lock(path):
                is_usage = os.path.abspath(path) == os.path.abspath(usage_path)
                if is_usage:
                    usage_held[0] = True
                try:
                    yield
                finally:
                    if is_usage:
                        usage_held[0] = False

        def checked_read(path):
            self.assertTrue(usage_held[0])
            return real_read(path)

        with mock.patch.object(store, "path_lock", new=tracked_lock), \
                mock.patch.object(store, "read_json", side_effect=checked_read):
            usage_metrics.update_usage_metrics(
                self.root, [{"id": "L1", "summary": "strategy"}]
            )

    def test_usage_lock_path_is_identical_through_root_alias(self):
        alias = self.root + "-alias"
        os.symlink(self.root, alias)
        self.addCleanup(os.unlink, alias)

        self.assertEqual(
            usage_metrics.usage_lock_path(self.root),
            usage_metrics.usage_lock_path(alias),
        )

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
    """Keep the unchanged recall surface compatible with the pinned Bash contract.

    Schema v2 intentionally changes packing, budget, authority, and Markdown fields, so
    byte parity is no longer valid. This projection still catches accidental drift in
    query parsing and always-on source inclusion while v2-specific tests own the delta.
    """

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

    def _compat(self, raw):
        obj = json.loads(raw)
        memory = obj["sources"]["memory"]
        user = obj["sources"]["user_profile"]
        return {
            "query": obj["query"],
            "query_terms": obj["query_terms"],
            "memory": {key: memory[key] for key in (
                "path", "status", "tokens_estimate", "content"
            )},
            "user_profile": {key: user[key] for key in (
                "path", "status", "tokens_estimate", "budget", "content"
            )},
        }

    def test_stdout_parity(self):
        for argv in (["--query", "auth"], ["--query", "auth", "--pretty"],
                     ["--query", "auth", "--max", "1"], ["--query", "nomatchzzz"]):
            rb, rp = self._roots()
            self.assertEqual(
                self._compat(self._bash(rb, argv)),
                self._compat(self._py(rp, argv)),
                "argv=%r" % argv,
            )

    def test_over_budget_parity(self):
        rb, rp = self._roots()
        argv = ["--query", "auth"]
        env = dict(_env(), KIMIFLOW_MEMORY_BUDGET="1", KIMIFLOW_USER_MEMORY_BUDGET="1")
        b = subprocess.run(["bash", self.script, "recall", "--root", rb] + argv,
                           stdout=subprocess.PIPE, text=True, check=True, env=env).stdout
        out = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
            recall.run(["--root", rp] + argv)
        self.assertEqual(self._compat(b), self._compat(out.getvalue()))

    def test_written_files_parity(self):
        rb, rp = self._roots()
        self._bash(rb, ["--query", "auth", "--write", "RECALL.md"])
        self._py(rp, ["--query", "auth", "--write", "RECALL.md"])
        # The v2 Markdown adds authority/budget/freshness but keeps the request legible.
        for root in (rb, rp):
            markdown = _strip_md_ts(_read(os.path.join(root, "RECALL.md")))
            self.assertIn("Query: auth", markdown)
            self.assertIn("Terms: auth", markdown)
        # RECALL.json keeps the unchanged source projection compatible.
        self.assertEqual(
            self._compat(_read(os.path.join(rb, "RECALL.json"))),
            self._compat(_read(os.path.join(rp, "RECALL.json"))),
        )
        # MEMORY-USAGE.json (normalize timestamps)
        usage_rel = os.path.join(".kimiflow", "project", "MEMORY-USAGE.json")
        self.assertEqual(_strip_usage_ts(_read(os.path.join(rb, usage_rel))),
                         _strip_usage_ts(_read(os.path.join(rp, usage_rel))))


if __name__ == "__main__":
    unittest.main()
