import os
import shutil
import tempfile
import unittest
from unittest import mock

from memory_router import summaries


class _FixtureCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write(self, name, lines):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(line + "\n" for line in lines))
        return path

    def missing(self, name="nope.jsonl"):
        return os.path.join(self.dir, name)

    def write_raw(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


class ReadJsonlSummaryCase(_FixtureCase):
    EMPTY = {
        "total": 0, "current": 0, "stale": 0, "superseded": 0, "archived": 0,
        "private": 0, "security": 0, "by_topic": {},
    }

    def test_missing_file_empty_shape(self):
        result = summaries.read_jsonl_summary(self.missing())
        self.assertEqual(result, self.EMPTY)
        self.assertEqual(list(result.keys()), list(self.EMPTY.keys()))

    def test_empty_file_matches_missing_shape(self):
        self.assertEqual(summaries.read_jsonl_summary(self.write("e.jsonl", [])), self.EMPTY)

    def test_status_buckets_and_defaults(self):
        path = self.write("L.jsonl", [
            '{"status":"current","topic":"b"}',
            '{"topic":"b"}',            # missing status -> current
            '{"status":null,"topic":"b"}',   # null -> current
            '{"status":"","topic":"b"}',     # "" -> counted nowhere but total
            '{"status":"stale","topic":"a"}',
            '{"status":"superseded","topic":"a"}',
            '{"status":"archived","topic":"a"}',
        ])
        r = summaries.read_jsonl_summary(path)
        self.assertEqual(r["total"], 7)
        self.assertEqual(r["current"], 3)   # explicit + missing + null
        self.assertEqual((r["stale"], r["superseded"], r["archived"]), (1, 1, 1))

    def test_sensitivity_buckets(self):
        path = self.write("L.jsonl", [
            '{"sensitivity":"private"}', '{"sensitivity":"security"}',
            '{"sensitivity":"normal"}', '{}',
        ])
        r = summaries.read_jsonl_summary(path)
        self.assertEqual((r["private"], r["security"]), (1, 1))

    def test_by_topic_sorted_with_uncategorized_default(self):
        path = self.write("L.jsonl", [
            '{"topic":"banana"}', '{"topic":"Apple"}', '{"topic":"apple"}', '{}', '{"topic":"Apple"}',
        ])
        r = summaries.read_jsonl_summary(path)
        # jq sort_by -> codepoint order: uppercase before lowercase.
        self.assertEqual(list(r["by_topic"].keys()), ["Apple", "apple", "banana", "uncategorized"])
        self.assertEqual(r["by_topic"], {"Apple": 2, "apple": 1, "banana": 1, "uncategorized": 1})

    def test_malformed_lines_skipped(self):
        path = self.write("L.jsonl", ['{"status":"current","topic":"x"}', 'NOT JSON', '   '])
        self.assertEqual(summaries.read_jsonl_summary(path)["total"], 1)

    def test_key_order(self):
        path = self.write("L.jsonl", ['{"topic":"x"}'])
        self.assertEqual(list(summaries.read_jsonl_summary(path).keys()), list(self.EMPTY.keys()))


class ProposalSummaryCase(_FixtureCase):
    PATH = ".kimiflow/project/PROPOSALS.jsonl"

    def test_missing_file_present_false(self):
        r = summaries.proposal_summary_json(self.missing())
        self.assertEqual(r, {
            "present": False, "path": self.PATH, "total": 0, "pending": 0,
            "approved": 0, "applied": 0, "rejected": 0, "needs_revalidation": 0,
            "by_type": {},
        })

    def test_status_buckets_and_defaults(self):
        path = self.write("P.jsonl", [
            '{"status":"pending"}', '{}', '{"status":null}',   # missing/null -> pending
            '{"status":""}',                                    # "" -> nowhere but total
            '{"status":"approved"}', '{"status":"applied"}',
            '{"status":"rejected"}', '{"status":"needs_revalidation"}',
        ])
        r = summaries.proposal_summary_json(path)
        self.assertTrue(r["present"])
        self.assertEqual(r["total"], 8)
        self.assertEqual(r["pending"], 3)
        self.assertEqual((r["approved"], r["applied"], r["rejected"], r["needs_revalidation"]),
                         (1, 1, 1, 1))

    def test_by_type_first_appearance_order_not_sorted(self):
        path = self.write("P.jsonl", [
            '{"type":"zeta"}', '{"type":"alpha"}', '{"type":"zeta"}', '{}',
        ])
        r = summaries.proposal_summary_json(path)
        # reduce -> first-appearance order (NOT sorted): zeta, alpha, unknown.
        self.assertEqual(list(r["by_type"].keys()), ["zeta", "alpha", "unknown"])
        self.assertEqual(r["by_type"], {"zeta": 2, "alpha": 1, "unknown": 1})

    def test_malformed_lines_skipped(self):
        path = self.write("P.jsonl", ['{"status":"pending","type":"x"}', 'GARBAGE'])
        self.assertEqual(summaries.proposal_summary_json(path)["total"], 1)

    def test_key_order(self):
        path = self.write("P.jsonl", ['{"status":"pending","type":"x"}'])
        self.assertEqual(list(summaries.proposal_summary_json(path).keys()), [
            "present", "path", "total", "pending", "approved", "applied",
            "rejected", "needs_revalidation", "by_type",
        ])


class UsageSummaryCase(_FixtureCase):
    PATH = ".kimiflow/project/MEMORY-USAGE.json"
    ABSENT = {
        "present": False, "path": PATH, "tracked_items": 0, "total_uses": 0,
        "last_used_at": None, "by_kind": {}, "events_tracked": 0, "by_event": {},
        "economics": {
            "recall_writes": 0, "history_writes": 0, "total_hit_count": 0,
            "estimated_output_tokens": 0, "last_event_at": None,
        },
        "hot_items": 0,
    }

    def test_missing_file_absent_shape(self):
        r = summaries.usage_summary_json(self.missing("none.json"))
        self.assertEqual(r, self.ABSENT)
        self.assertEqual(list(r.keys()), list(self.ABSENT.keys()))
        self.assertEqual(list(r["economics"].keys()), list(self.ABSENT["economics"].keys()))

    def test_invalid_json_absent(self):
        self.assertEqual(summaries.usage_summary_json(self.write_raw("b.json", "not json{")), self.ABSENT)

    def test_literal_null_absent(self):
        self.assertEqual(summaries.usage_summary_json(self.write_raw("n.json", "null")), self.ABSENT)

    def test_literal_false_absent(self):
        self.assertEqual(summaries.usage_summary_json(self.write_raw("f.json", "false")), self.ABSENT)

    def test_non_object_json_absent_divergence(self):
        # Bash jq-errors on `.items` for a non-object top level (empty output, breaks the
        # caller); the port returns the absent shape instead. Unreachable for real files.
        self.assertEqual(summaries.usage_summary_json(self.write_raw("a.json", "[1,2,3]")), self.ABSENT)

    def test_empty_object_present_all_zero(self):
        r = summaries.usage_summary_json(self.write_raw("e.json", "{}"))
        self.assertTrue(r["present"])
        self.assertEqual(r["tracked_items"], 0)
        self.assertEqual(r["by_kind"], {})
        self.assertEqual(r["economics"]["total_hit_count"], 0)

    def test_items_and_events_null_default_to_empty(self):
        r = summaries.usage_summary_json(self.write_raw("z.json", '{"items":null,"events":null}'))
        self.assertTrue(r["present"])
        self.assertEqual(r["tracked_items"], 0)
        self.assertEqual(r["events_tracked"], 0)

    def test_full_mixed_aggregation(self):
        path = self.write_raw("full.json", """
        {"items":{"learning:a":{"kind":"learning","use_count":3,"last_used_at":"2026-06-10T00:00:00Z"},
                  "learning:b":{"kind":"learning","use_count":1,"last_used_at":"2026-06-20T00:00:00Z"},
                  "user:c":{"kind":"user","use_count":0},
                  "d":{"use_count":5,"last_used_at":"2026-06-01T00:00:00Z"}},
         "events":[{"kind":"recall","hit_count":2,"estimated_tokens":100,"at":"2026-06-10T00:00:00Z"},
                   {"kind":"recall","hit_count":3,"estimated_tokens":150,"at":"2026-06-15T00:00:00Z"},
                   {"kind":"history","hit_count":1,"estimated_tokens":50,"at":"2026-06-12T00:00:00Z"},
                   {"kind":"recall","hit_count":0,"at":null},
                   {"hit_count":7,"estimated_tokens":9}]}
        """)
        r = summaries.usage_summary_json(path)
        self.assertEqual(r["tracked_items"], 4)
        self.assertEqual(r["total_uses"], 9)
        self.assertEqual(r["last_used_at"], "2026-06-20T00:00:00Z")
        self.assertEqual(list(r["by_kind"].keys()), ["learning", "user", "unknown"])  # first-appearance
        self.assertEqual(r["by_kind"], {"learning": 2, "user": 1, "unknown": 1})
        self.assertEqual(r["events_tracked"], 5)
        self.assertEqual(list(r["by_event"].keys()), ["recall", "history", "unknown"])
        self.assertEqual(r["by_event"]["recall"],
                         {"writes": 3, "hits": 5, "estimated_tokens": 250, "last_at": "2026-06-15T00:00:00Z"})
        self.assertEqual(r["by_event"]["history"],
                         {"writes": 1, "hits": 1, "estimated_tokens": 50, "last_at": "2026-06-12T00:00:00Z"})
        self.assertEqual(r["by_event"]["unknown"],
                         {"writes": 1, "hits": 7, "estimated_tokens": 9, "last_at": None})
        self.assertEqual(r["economics"], {
            "recall_writes": 3, "history_writes": 1, "total_hit_count": 13,
            "estimated_output_tokens": 309, "last_event_at": "2026-06-15T00:00:00Z",
        })
        self.assertEqual(r["hot_items"], 2)   # use_count > 1: a(3), d(5)

    def test_output_key_order(self):
        r = summaries.usage_summary_json(self.write_raw("e.json", "{}"))
        self.assertEqual(list(r.keys()), list(self.ABSENT.keys()))


class EconomicsSummaryCase(_FixtureCase):
    PATH = ".kimiflow/project/MEMORY-ECONOMICS.jsonl"
    # Deterministic mixed fixture (default avoided_per_hit = 1200):
    #  A: avoided=3600 net=3250 -> saving | B: avoided=1200 net=-3800 -> waste
    #  C: all-zero hits=0 -> unknown      | D: avoided=1200 net=0 -> neutral
    MIXED = [
        '{"always_on_tokens":100,"user_memory_tokens":50,"recall_tokens":200,'
        '"recall_hit_count":5,"used_hit_count":3,"recorded_at":"2026-06-10T00:00:00Z"}',
        '{"always_on_tokens":5000,"recall_hit_count":2,"used_hit_count":1}',
        '{"recall_hit_count":0,"used_hit_count":0}',
        '{"always_on_tokens":1200,"recall_hit_count":3,"used_hit_count":1}',
    ]

    def econ(self, name, lines):
        return summaries.economics_summary_json(self.write(name, lines))

    def test_missing_file_absent_shape(self):
        r = summaries.economics_summary_json(self.missing("none.jsonl"))
        self.assertFalse(r["present"])
        self.assertEqual(r["verdict"], "no_data")
        self.assertEqual(r["note"], "No run-level memory economics recorded yet.")
        self.assertEqual(list(r.keys()), [
            "present", "path", "runs_tracked", "confidence", "verdict", "action_required",
            "normalized_legacy_rows", "by_result", "totals", "estimated_savings_percent",
            "averages", "last_recorded_at", "note",
        ])
        self.assertEqual(list(r["totals"].keys()), [
            "always_on_tokens", "user_memory_tokens", "recall_tokens", "recall_hit_count",
            "used_hit_count", "estimated_avoided_scan_tokens", "net_estimated_tokens_saved",
        ])
        self.assertEqual(list(r["averages"].keys()), [
            "net_estimated_tokens_saved_per_run", "recall_hit_count_per_run",
            "used_hit_count_per_run",
        ])

    def test_empty_file_present_but_zero_runs(self):
        # Existing-but-empty file goes through the row path (present:true, n=0), and its
        # note is the "too few runs" text -- NOT the missing-file note.
        r = self.econ("e.jsonl", [])
        self.assertTrue(r["present"])
        self.assertEqual(r["runs_tracked"], 0)
        self.assertEqual(r["verdict"], "no_data")
        self.assertEqual(r["confidence"], "none")
        self.assertEqual(r["estimated_savings_percent"], None)
        self.assertTrue(r["note"].startswith("Too few runs"))

    def test_mixed_classification_and_aggregates(self):
        r = self.econ("m.jsonl", self.MIXED)
        self.assertEqual(r["runs_tracked"], 4)
        self.assertEqual(r["confidence"], "low")
        self.assertEqual(r["verdict"], "insufficient_data")
        self.assertFalse(r["action_required"])
        self.assertEqual(list(r["by_result"].keys()), ["saving", "waste", "unknown", "neutral"])
        self.assertEqual(r["by_result"], {"saving": 1, "waste": 1, "unknown": 1, "neutral": 1})
        self.assertEqual(r["totals"], {
            "always_on_tokens": 6300, "user_memory_tokens": 50, "recall_tokens": 200,
            "recall_hit_count": 10, "used_hit_count": 5,
            "estimated_avoided_scan_tokens": 6000, "net_estimated_tokens_saved": -550,
        })
        self.assertEqual(r["estimated_savings_percent"], -10)   # floor(-550*100/6000)
        self.assertEqual(r["averages"], {
            "net_estimated_tokens_saved_per_run": -138,   # floor(-550/4)
            "recall_hit_count_per_run": 2,                # floor(10/4)
            "used_hit_count_per_run": 1,                  # floor(5/4)
        })
        self.assertEqual(r["normalized_legacy_rows"], 3)   # A,B,D recomputed; C unchanged
        self.assertEqual(r["last_recorded_at"], "2026-06-10T00:00:00Z")

    def test_legacy_rows_counted(self):
        r = self.econ("l.jsonl", [
            '{"always_on_tokens":10,"used_hit_count":2,"recall_hit_count":2,'
            '"estimated_avoided_scan_tokens":99999,"net_estimated_tokens_saved":-7}',
        ])
        self.assertEqual(r["normalized_legacy_rows"], 1)

    def test_string_and_float_fields_normalized(self):
        r = self.econ("s.jsonl", [
            '{"always_on_tokens":"100","used_hit_count":"2","recall_hit_count":3,"recall_tokens":1.5}',
        ])
        # avoided=2*1200=2400; net=2400-100-1.5=2298.5
        self.assertEqual(r["totals"]["always_on_tokens"], 100)
        self.assertEqual(r["totals"]["estimated_avoided_scan_tokens"], 2400)
        self.assertEqual(r["totals"]["net_estimated_tokens_saved"], 2298.5)

    def test_malformed_lines_skipped(self):
        r = self.econ("b.jsonl", ['{"recall_hit_count":1,"used_hit_count":1}', 'GARBAGE'])
        self.assertEqual(r["runs_tracked"], 1)

    def test_confidence_high_at_20_runs(self):
        r = self.econ("h.jsonl", ['{"used_hit_count":5,"recall_hit_count":5,"always_on_tokens":10}'] * 20)
        self.assertEqual(r["confidence"], "high")
        self.assertEqual(r["verdict"], "saving_likely")

    def test_waste_risk_action_required(self):
        r = self.econ("w.jsonl", ['{"used_hit_count":1,"recall_hit_count":2,"always_on_tokens":99999}'] * 10)
        self.assertEqual(r["verdict"], "waste_risk")
        self.assertTrue(r["action_required"])

    def test_env_override_changes_avoided(self):
        with mock.patch.dict(os.environ, {"KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT": "600"}):
            r = self.econ("o.jsonl", ['{"used_hit_count":2,"recall_hit_count":2}'])
        self.assertEqual(r["totals"]["estimated_avoided_scan_tokens"], 1200)   # 2*600

    def test_env_zero_is_honored(self):
        with mock.patch.dict(os.environ, {"KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT": "0"}):
            r = self.econ("z.jsonl", ['{"used_hit_count":5,"recall_hit_count":5}'])
        self.assertEqual(r["totals"]["estimated_avoided_scan_tokens"], 0)
        self.assertEqual(r["estimated_savings_percent"], None)   # avoided not > 0

    def test_env_invalid_falls_back_to_default(self):
        for bad in ("abc", "1.5", "-5", ""):
            with mock.patch.dict(os.environ, {"KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT": bad}):
                r = self.econ("d.jsonl", ['{"used_hit_count":1,"recall_hit_count":1}'])
            self.assertEqual(r["totals"]["estimated_avoided_scan_tokens"], 1200, bad)


class GlobalEfficiencySummaryCase(_FixtureCase):
    DISPLAY = "~/.kimiflow/metrics/token-economics.jsonl"

    def ge(self, lines=None, env=None):
        # Drive global_efficiency_summary_json via env: KIMIFLOW_HOME -> self.dir so the
        # file resolves to <dir>/metrics/token-economics.jsonl. lines=None -> no file.
        metrics = os.path.join(self.dir, "metrics")
        os.makedirs(metrics, exist_ok=True)
        if lines is not None:
            with open(os.path.join(metrics, "token-economics.jsonl"), "w", encoding="utf-8") as fh:
                fh.write("".join(line + "\n" for line in lines))
        environ = {"KIMIFLOW_GLOBAL_METRICS": "on", "KIMIFLOW_HOME": self.dir, "HOME": "/tmp"}
        if env:
            environ.update(env)
        with mock.patch.dict(os.environ, environ):
            return summaries.global_efficiency_summary_json()

    def test_disabled_absent(self):
        r = self.ge(["{}"], env={"KIMIFLOW_GLOBAL_METRICS": "off"})
        self.assertEqual(r["enabled"], False)
        self.assertEqual(r["present"], False)
        self.assertEqual(r["path"], self.DISPLAY)
        self.assertEqual(r["note"], "Global local efficiency stats are disabled by KIMIFLOW_GLOBAL_METRICS.")

    def test_no_base_dir_absent(self):
        # No KIMIFLOW_HOME and no HOME -> base_dir None -> absent, but enabled stays true.
        with mock.patch.dict(os.environ, {"KIMIFLOW_GLOBAL_METRICS": "on"}, clear=True):
            r = summaries.global_efficiency_summary_json()
        self.assertEqual((r["enabled"], r["present"]), (True, False))
        self.assertEqual(r["note"], "No global local efficiency rows recorded yet.")

    def test_missing_file_absent_enabled(self):
        r = self.ge(lines=None)
        self.assertEqual((r["enabled"], r["present"]), (True, False))
        self.assertEqual(r["note"], "No global local efficiency rows recorded yet.")

    def test_empty_file_present_zero(self):
        r = self.ge([])
        self.assertEqual((r["present"], r["runs_tracked"]), (True, 0))
        self.assertEqual((r["confidence"], r["verdict"]), ("none", "no_data"))
        self.assertEqual(r["estimated_savings_percent"], None)
        self.assertEqual(r["note"], "Too few global local runs for a reliable savings claim; show as an estimate only.")

    def test_mixed_rows_totals_and_projects(self):
        r = self.ge([
            '{"net_estimated_tokens_saved":500,"recall_hit_count":4,"used_hit_count":2,"estimated_avoided_scan_tokens":2400,"always_on_tokens":100,"user_memory_tokens":50,"recall_tokens":80,"result":"saving","project_id":"aaa","recorded_day":"2026-06-01"}',
            '{"net_estimated_tokens_saved":-300,"recall_hit_count":1,"used_hit_count":0,"estimated_avoided_scan_tokens":0,"result":"waste","project_id":"bbb","recorded_day":"2026-06-03"}',
            '{"net_estimated_tokens_saved":0,"recall_hit_count":2,"used_hit_count":1,"estimated_avoided_scan_tokens":1200,"result":"neutral","project_id":"aaa","recorded_day":"2026-06-02"}',
            '{"recall_hit_count":0,"result":"unknown"}',
        ])
        self.assertEqual(r["runs_tracked"], 4)
        self.assertEqual(r["projects_tracked"], 2)  # aaa, bbb (null/missing dropped)
        self.assertEqual(r["totals"]["net_estimated_tokens_saved"], 200)
        self.assertEqual(r["totals"]["recall_hit_count"], 7)
        self.assertEqual(r["last_recorded_day"], "2026-06-03")
        self.assertEqual(r["by_result"], {"saving": 1, "waste": 1, "neutral": 1, "unknown": 1})

    def test_by_result_first_appearance_order(self):
        r = self.ge([
            '{"result":"waste","recall_hit_count":1}',
            '{"result":"saving","recall_hit_count":1,"used_hit_count":1,"net_estimated_tokens_saved":5}',
            '{"result":"waste","recall_hit_count":1}',
        ])
        self.assertEqual(list(r["by_result"].keys()), ["waste", "saving"])

    def test_confidence_verdict_thresholds(self):
        saving = '{"net_estimated_tokens_saved":1000,"recall_hit_count":3,"used_hit_count":2,"result":"saving"}'
        self.assertEqual(self.ge([saving] * 7)["confidence"], "low")
        r8 = self.ge([saving] * 8)
        self.assertEqual((r8["confidence"], r8["verdict"]), ("medium", "saving_likely"))
        self.assertEqual(self.ge([saving] * 20)["confidence"], "high")

    def test_enabled_flag_spellings(self):
        for off in ("off", "OFF", "0", "false", "FALSE", "no", "NO"):
            self.assertEqual(self.ge(None, env={"KIMIFLOW_GLOBAL_METRICS": off})["enabled"], False, off)
        for on in ("on", "ON", "1", "yes", "whatever", ""):
            self.assertEqual(self.ge(None, env={"KIMIFLOW_GLOBAL_METRICS": on})["enabled"], True, on)

    def test_float_total_canonical_integral(self):
        # jq renders a computed integral-float sum as an int (-5.5 + 2.5 -> -3, not -3.0).
        r = self.ge([
            '{"net_estimated_tokens_saved":-5.5,"recall_hit_count":2,"result":"waste"}',
            '{"net_estimated_tokens_saved":2.5,"recall_hit_count":1,"used_hit_count":1,"result":"saving"}',
        ])
        total = r["totals"]["net_estimated_tokens_saved"]
        self.assertEqual(total, -3)
        self.assertIsInstance(total, int)

    def test_single_row_float_literal_preserved(self):
        # A single row's field passes through jq `add` verbatim: 5.0 stays a float.
        r = self.ge(['{"net_estimated_tokens_saved":5.0,"recall_hit_count":1,"used_hit_count":1,"result":"saving"}'])
        total = r["totals"]["net_estimated_tokens_saved"]
        self.assertEqual(total, 5.0)
        self.assertIsInstance(total, float)

    def test_key_order(self):
        keys = list(self.ge([]).keys())
        self.assertEqual(keys, [
            "enabled", "present", "path", "scope", "runs_tracked", "projects_tracked",
            "confidence", "verdict", "estimated_savings_percent", "action_required",
            "by_result", "totals", "averages", "last_recorded_day", "privacy", "note",
        ])


_FRESH = "2999-12-31"   # always >= the default-90 cutoff -> not stale
_STALE = "2000-01-01"   # always < the cutoff -> stale


class _LearningCase(_FixtureCase):
    def learnings(self, rows):
        return self.write("LEARNINGS.jsonl", rows)

    def usage(self, items):
        import json
        return self.write_raw("MEMORY-USAGE.json", json.dumps({"items": items}))

    def call(self, fn, learnings, usage_file, stale_after=None):
        env = {} if stale_after is None else {"KIMIFLOW_LEARNING_STALE_AFTER_DAYS": stale_after}
        with mock.patch.dict(os.environ, env, clear=True):
            return fn(learnings, usage_file)


class LearningLifecycleCase(_LearningCase):
    def lc(self, learnings, usage_file, stale_after=None):
        return self.call(summaries.learning_lifecycle_json, learnings, usage_file, stale_after)

    def test_missing_absent_shape(self):
        r = self.lc(self.missing("L.jsonl"), self.missing("U.json"))
        self.assertEqual(list(r.keys()), [
            "stale_after_days", "cutoff_date", "current", "stale_candidates",
            "stale_candidate_ids", "unused_current", "used_current",
        ])
        self.assertEqual(r["stale_after_days"], 90)
        self.assertEqual((r["current"], r["stale_candidates"], r["unused_current"], r["used_current"]), (0, 0, 0, 0))

    def test_present_shape_keys(self):
        L = self.learnings(['{"id":"a","status":"current","last_verified":"%s"}' % _FRESH])
        keys = list(self.lc(L, self.missing("U.json")).keys())
        self.assertEqual(keys, [
            "stale_after_days", "cutoff_date", "current", "stale_candidates",
            "stale_candidate_ids", "unused_current", "unused_current_ids",
            "cold_candidate_ids", "used_current", "used_current_ids",
        ])

    def test_used_unused_split(self):
        L = self.learnings([
            '{"id":"u1","status":"current","last_verified":"%s"}' % _FRESH,
            '{"id":"u2","status":"current","last_verified":"%s"}' % _FRESH,
            '{"id":"u3","status":"current","last_verified":"%s"}' % _FRESH,
        ])
        U = self.usage({"learning:u1": {"use_count": 1}, "learning:u2": None})  # null -> untracked
        r = self.lc(L, U)
        self.assertEqual((r["used_current"], r["unused_current"]), (1, 2))
        self.assertEqual(r["used_current_ids"], ["u1"])
        self.assertEqual(r["unused_current_ids"], ["u2", "u3"])

    def test_stale_includes_missing_last_verified(self):
        L = self.learnings([
            '{"id":"fresh","status":"current","last_verified":"%s"}' % _FRESH,
            '{"id":"old","status":"current","last_verified":"%s"}' % _STALE,
            '{"id":"never","status":"current"}',   # missing last_verified -> "" < cutoff -> stale
            '{"id":"arch","status":"archived","last_verified":"%s"}' % _STALE,  # excluded
        ])
        r = self.lc(L, self.missing("U.json"))
        self.assertEqual(r["current"], 3)
        self.assertEqual(r["stale_candidate_ids"], ["old", "never"])

    def test_id_capping(self):
        L = self.learnings(['{"id":"c%02d","status":"current","last_verified":"%s"}' % (i, _FRESH) for i in range(25)])
        r = self.lc(L, self.missing("U.json"))
        self.assertEqual(r["unused_current"], 25)
        self.assertEqual(len(r["unused_current_ids"]), 20)
        self.assertEqual(len(r["cold_candidate_ids"]), 10)

    def test_stale_after_env_override(self):
        L = self.learnings(['{"id":"a","status":"current","last_verified":"%s"}' % _FRESH])
        self.assertEqual(self.lc(L, self.missing("U.json"), stale_after="30")["stale_after_days"], 30)
        self.assertEqual(self.lc(L, self.missing("U.json"), stale_after="abc")["stale_after_days"], 90)

    def test_non_string_last_verified_cross_type(self):
        # jq total order: number/bool sort below the string cutoff (stale); array/object
        # above (not stale). Must not raise (a raw Python `<` would TypeError).
        L = self.learnings([
            '{"id":"num","status":"current","last_verified":123}',
            '{"id":"tru","status":"current","last_verified":true}',
            '{"id":"arr","status":"current","last_verified":[]}',
            '{"id":"obj","status":"current","last_verified":{}}',
            '{"id":"fresh","status":"current","last_verified":"%s"}' % _FRESH,
        ])
        self.assertEqual(self.lc(L, self.missing("U.json"))["stale_candidate_ids"], ["num", "tru"])


class LearningUsefulnessCase(_LearningCase):
    def lu(self, learnings, usage_file, stale_after=None):
        return self.call(summaries.learning_usefulness_json, learnings, usage_file, stale_after)

    def test_missing_absent_shape(self):
        r = self.lu(self.missing("L.jsonl"), self.missing("U.json"))
        self.assertEqual(list(r.keys()), [
            "schema_version", "stale_after_days", "cutoff_date",
            "hot", "warm", "cold", "stale", "promote_candidates", "compress_candidates",
        ])
        self.assertNotIn("basis", r)
        self.assertEqual(r["hot"], {"count": 0, "ids": []})

    def test_tiers_and_stale_precedence(self):
        L = self.learnings([
            '{"id":"hot","status":"current","last_verified":"%s","confidence":"high"}' % _FRESH,
            '{"id":"warm","status":"current","last_verified":"%s","confidence":"high"}' % _FRESH,
            '{"id":"cold","status":"current","last_verified":"%s","confidence":"high"}' % _FRESH,
            '{"id":"stale_hot","status":"current","last_verified":"%s","confidence":"high"}' % _STALE,
        ])
        U = self.usage({
            "learning:hot": {"use_count": 5},
            "learning:warm": {"use_count": 1},
            "learning:cold": {"use_count": 0},
            "learning:stale_hot": {"use_count": 9},  # stale wins over use_count
        })
        r = self.lu(L, U)
        self.assertEqual(r["hot"]["ids"], ["hot"])
        self.assertEqual(r["warm"]["ids"], ["warm"])
        self.assertEqual(r["cold"]["ids"], ["cold"])
        self.assertEqual(r["stale"]["ids"], ["stale_hot"])
        self.assertEqual(r["compress_candidates"]["ids"], ["cold", "stale_hot"])  # cold + stale
        self.assertEqual(r["basis"], "exclusive_tiers_current_rows; stale rows are never promote candidates")

    def test_promote_confidence_and_sensitivity(self):
        L = self.learnings([
            '{"id":"hi","status":"current","last_verified":"%s","confidence":"high","sensitivity":"normal"}' % _FRESH,
            '{"id":"med","status":"current","last_verified":"%s"}' % _FRESH,  # confidence default medium
            '{"id":"low","status":"current","last_verified":"%s","confidence":"low"}' % _FRESH,
            '{"id":"priv","status":"current","last_verified":"%s","confidence":"high","sensitivity":"private"}' % _FRESH,
        ])
        U = self.usage({k: {"use_count": 2} for k in
                        ("learning:hi", "learning:med", "learning:low", "learning:priv")})
        r = self.lu(L, U)
        self.assertEqual(r["promote_candidates"]["ids"], ["hi", "med"])  # low excluded, private excluded

    def test_use_count_string_is_tonumbered(self):
        L = self.learnings(['{"id":"s","status":"current","last_verified":"%s"}' % _FRESH])
        U = self.usage({"learning:s": {"use_count": "2"}})  # string -> 2 -> hot
        self.assertEqual(self.lu(L, U)["hot"]["ids"], ["s"])

    def test_bounded_ids_cap_20(self):
        L = self.learnings(['{"id":"c%02d","status":"current","last_verified":"%s"}' % (i, _FRESH) for i in range(25)])
        self.assertEqual(len(self.lu(L, self.missing("U.json"))["cold"]["ids"]), 20)

    def test_non_string_last_verified_is_stale_not_hot(self):
        # A number last_verified sorts below the string cutoff -> stale tier (never hot),
        # without raising.
        L = self.learnings(['{"id":"num","status":"current","last_verified":123}'])
        U = self.usage({"learning:num": {"use_count": 9}})
        r = self.lu(L, U)
        self.assertEqual(r["stale"]["ids"], ["num"])
        self.assertEqual(r["hot"]["ids"], [])

    def test_utility_rows_are_explainable_bounded_and_max_five(self):
        L = self.learnings([
            '{"id":"hot","status":"current","last_verified":"%s","confidence":"high",'
            '"evidence_fingerprints":[{"status":"current"}]}' % _FRESH,
            '{"id":"stale","status":"current","last_verified":"2000-01-01","confidence":"low",'
            '"evidence_fingerprints":[{"status":"missing"}]}',
        ])
        U = self.usage({"learning:hot": {"use_count": 4}})
        result = summaries.learning_utility_rows(L, U)
        self.assertEqual([row["id"] for row in result], ["hot", "stale"])
        self.assertEqual(result[0]["utility_points"], 5)
        self.assertEqual(result[1]["utility_points"], 0)
        self.assertEqual(result[1]["reasons"], [
            "uses:0", "stale", "evidence_not_current", "confidence:low",
        ])
        self.assertLessEqual(len(result), 20)


if __name__ == "__main__":
    unittest.main()
