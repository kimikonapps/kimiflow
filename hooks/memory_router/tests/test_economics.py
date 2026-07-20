import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import attribution, contracts, economics, global_metrics, paths

TAG = "kimiflow--v0.1.50"
_FIXED_SALT = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class EconomicsHelperCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.run = os.path.join(self.root, "run")
        os.makedirs(self.run)

    def _write(self, name, content):
        path = os.path.join(self.run, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    # ---- _sed_head (sed -n '1,Np' fidelity) ----
    def test_sed_head_no_trailing_newline_added(self):
        self.assertEqual(economics._sed_head("a\nb\nc", 220), "a\nb\nc")

    def test_sed_head_preserves_trailing_newline(self):
        self.assertEqual(economics._sed_head("a\nb\nc\n", 220), "a\nb\nc\n")

    def test_sed_head_truncates_with_terminator(self):
        self.assertEqual(economics._sed_head("a\nb\nc\nd", 3), "a\nb\nc\n")

    def test_sed_head_empty(self):
        self.assertEqual(economics._sed_head("", 220), "")

    # ---- run_artifact_corpus ----
    def test_corpus_fixed_files_then_findings_sorted(self):
        self._write("RESEARCH.md", "alpha\nbeta")
        self._write("VERIFICATION.md", "gamma\n")
        self._write("findings/b.md", "f-bravo")
        self._write("findings/a.md", "f-alpha")
        self._write("findings/sub/c.md", "f-charlie")
        corpus = economics.run_artifact_corpus(self.run)
        # RESEARCH before VERIFICATION (fixed order), each + "\n"; findings path-sorted.
        self.assertEqual(corpus, "alpha\nbeta\ngamma\n\nf-alpha\nf-bravo\nf-charlie\n")

    def test_corpus_skips_absent(self):
        self._write("PLAN.md", "only-plan")
        self.assertEqual(economics.run_artifact_corpus(self.run), "only-plan\n")

    def test_corpus_220_line_cap(self):
        body = "\n".join("line%d" % i for i in range(300))
        self._write("RESEARCH.md", body)
        corpus = economics.run_artifact_corpus(self.run)
        self.assertIn("line219", corpus)
        self.assertNotIn("line220", corpus)

    # ---- recall_hits_for_economics_json ----
    def test_recall_hits_order_and_tag(self):
        recall = self._write("RECALL.json", json.dumps({"sources": {
            "learnings": {"hits": [{"id": "L1"}]},
            "facts": {"hits": [{"id": "F1"}]},
            "index": {"hits": [{"id": "I1"}]},
            "history": {"hits": [{"id": "H1"}]},
            "strategies": {"hits": [{"id": "S1"}]},
        }}))
        hits = economics.recall_hits_for_economics_json(recall)
        self.assertEqual([(h["id"], h["_economics_source"]) for h in hits],
                         [("L1", "learning"), ("F1", "fact"), ("I1", "index"),
                          ("H1", "history")])
        contract_hits = economics.recall_hits_for_economics_json(
            recall, include_strategies=True,
        )
        self.assertEqual([(h["id"], h["_economics_source"]) for h in contract_hits],
                         [("L1", "learning"), ("F1", "fact"), ("I1", "index"),
                          ("H1", "history"), ("S1", "strategy")])

    def test_recall_hits_missing_file_and_non_dict(self):
        self.assertEqual(economics.recall_hits_for_economics_json(self.run + "/none.json"), [])
        bad = self._write("RECALL.json", "[1,2]")
        self.assertEqual(economics.recall_hits_for_economics_json(bad), [])
        nullf = self._write("RECALL.json", "false")
        self.assertEqual(economics.recall_hits_for_economics_json(nullf), [])

    # ---- economics_hits_tokens ----
    def test_hits_tokens_counts_wordlike(self):
        hits = [{"title": "alpha-beta", "summary": "gamma_delta!!", "body": "", "text": "x y"}]
        # alpha beta gamma_delta x y -> non-word runs collapse, count non-empty tokens
        self.assertEqual(economics.economics_hits_tokens(hits), 5)

    def test_hits_tokens_empty(self):
        self.assertEqual(economics.economics_hits_tokens([]), 0)

    # ---- economics_used_hits_count ----
    def test_used_hits_substring_match(self):
        corpus = "the run mentions learn_abc somewhere"
        hits = [{"id": "learn_abc"}, {"id": "learn_zzz"}]
        self.assertEqual(economics.economics_used_hits_count(hits, corpus), 1)

    def test_used_hits_evidence_first_and_empty_needles(self):
        corpus = "ref RESEARCH.md:12 here"
        hits = [{"evidence": ["RESEARCH.md:12"]}, {"evidence": []}, {}]
        self.assertEqual(economics.economics_used_hits_count(hits, corpus), 1)

    def test_contract_uses_applied_ids_and_legacy_keeps_heuristic(self):
        project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(project)
        hit = {"id": "learn_abc", "title": "cached route", "summary": "avoid broad scan"}
        identifier = attribution.recall_id("learnings", "learn_abc", hit)
        hit["recall_id"] = identifier
        self._write("RECALL.json", json.dumps({
            "schema_version": 2,
            "attribution": {"contract": 1},
            "sources": {"learnings": {"hits": [hit]}},
        }))
        self._write("RESEARCH.md", "The old learn_abc path is mentioned but not applied.\n")
        self._write(
            "PLAN.md",
            "<!-- kimiflow:recall-attribution contract=1 -->\n"
            "Applied recall IDs: none\n"
            "Decision D1: choose route\n"
            "Recall D1: none\n",
        )
        self.assertEqual(economics.run_economics_row_json(self.root, self.run)["used_hit_count"], 0)

        self._write(
            "PLAN.md",
            "<!-- kimiflow:recall-attribution contract=1 -->\n"
            "Applied recall IDs: %s\n"
            "Decision D1: choose route\n"
            "Recall D1: %s\n" % (identifier, identifier),
        )
        self.assertEqual(economics.run_economics_row_json(self.root, self.run)["used_hit_count"], 1)

        self._write("PLAN.md", "Legacy plan mentions learn_abc.\n")
        self.assertEqual(economics.run_economics_row_json(self.root, self.run)["used_hit_count"], 1)

    def test_legacy_economics_ignores_strategy_only_hits(self):
        self._write("RECALL.json", json.dumps({
            "sources": {"strategies": {"hits": [{
                "id": "S1", "title": "legacy strategy", "summary": "must stay invisible",
            }]}},
        }))
        self._write("PLAN.md", "Legacy plan mentions S1 and legacy strategy.\n")
        row = economics.run_economics_row_json(self.root, self.run)
        self.assertEqual(row["recall_hit_count"], 0)
        self.assertEqual(row["used_hit_count"], 0)
        self.assertEqual(row["recall_tokens"], 0)
        self.assertEqual(row["result"], "unknown")

    # ---- run_type_from_state ----
    def test_run_type_mode_variants(self):
        cases = {
            "mode: feature": "feature",
            "**Mode:** bugfix": "bugfix",
            "- mode: refactor work": "refactor",
            "mode: documentation": "docs",
            "Mode: audit": "audit",
            "mode: spelunking": "unknown",
        }
        for line, expected in cases.items():
            with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as fh:
                fh.write("# Run\n" + line + "\n")
            self.assertEqual(economics.run_type_from_state(self.run), expected, line)

    def test_run_type_fallbacks(self):
        self.assertEqual(economics.run_type_from_state(self.run), "unknown")
        self._write("PROBLEM.md", "x")
        self.assertEqual(economics.run_type_from_state(self.run), "bugfix")
        os.remove(os.path.join(self.run, "PROBLEM.md"))
        self._write("AUDIT.md", "x")
        self.assertEqual(economics.run_type_from_state(self.run), "audit")

    # ---- run_scope_from_state ----
    def test_run_scope_variants(self):
        cases = {
            "scope: small": "small",
            "**Scope:** large": "large",
            "- scope: trivial": "trivial",
            "Scope: spelunking": "unknown",
        }
        for line, expected in cases.items():
            with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as fh:
                fh.write("# Run\n" + line + "\n")
            self.assertEqual(economics.run_scope_from_state(self.run), expected, line)

    def test_run_scope_missing_state(self):
        self.assertEqual(economics.run_scope_from_state(self.run), "unknown")

    def test_global_row_carries_scope(self):
        khome = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, khome, ignore_errors=True)
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as fh:
            fh.write("# Run\nMode: feature\nScope: large\n")
        env = {"KIMIFLOW_HOME": khome, "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, env, clear=True):
            out = economics.write_global_economics_row(self.root, self.run, {})
        self.assertTrue(out.get("recorded"), out)
        ledger = os.path.join(khome, "metrics", "token-economics.jsonl")
        with open(ledger, "r", encoding="utf-8") as fh:
            row = json.loads(fh.readline())
        self.assertEqual(row.get("scope"), "large")

    # ---- project_size_bucket (boundaries via mocked git) ----
    def test_size_bucket_boundaries(self):
        for n, expected in ((0, "small"), (199, "small"), (200, "medium"),
                            (999, "medium"), (1000, "large")):
            out = ("p\n" * n).encode()
            with mock.patch("subprocess.run",
                            return_value=mock.Mock(returncode=0, stdout=out)):
                self.assertEqual(economics.project_size_bucket(self.root), expected, n)

    def test_size_bucket_non_git(self):
        # real git on a non-repo temp -> non-zero -> count 0 -> small
        self.assertEqual(economics.project_size_bucket(self.root), "small")

    # ---- _gnum ----
    def test_gnum(self):
        self.assertEqual(economics._gnum(5), 5)
        self.assertEqual(economics._gnum(None), 0)
        self.assertEqual(economics._gnum(False), 0)
        self.assertEqual(economics._gnum(True), 0)
        self.assertEqual(economics._gnum("12"), 12)
        self.assertEqual(economics._gnum("1.5"), 1.5)
        self.assertEqual(economics._gnum("nope"), 0)
        self.assertEqual(economics._gnum([1]), 0)

    # ---- _dedupe_append ----
    def test_dedupe_append_drops_matching_key(self):
        path = os.path.join(self.root, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"run":"A","v":1}\n{"run":"B","v":2}\n')
        data = economics._dedupe_append(path, {"run": "B", "v": 3}, "run")
        self.assertEqual(data, '{"run":"A","v":1}\n{"run":"B","v":3}\n')

    def test_dedupe_append_new_file(self):
        path = os.path.join(self.root, "absent.jsonl")
        self.assertEqual(economics._dedupe_append(path, {"run": "X"}, "run"), '{"run":"X"}\n')


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _normalize(text):
    # iso_now timestamps first (full match), then any remaining bare dates (date_now).
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "TS", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "DAY", text)
    return text


@unittest.skipUnless(_tools_present(), "bash/jq/git or pinned tag unavailable")
class EconomicsParityCase(unittest.TestCase):
    """Grounds record_run_economics_json byte-for-byte vs the pinned bash. The bash side
    sources a dispatch-free copy of the pinned script (everything before `cmd="${1:-}"`)
    and calls the function directly; both runtimes use the SAME root + KIMIFLOW_HOME +
    pre-seeded salt so the anonymized hashes are genuinely comparable."""

    @classmethod
    def setUpClass(cls):
        src = subprocess.run(
            ["git", "-C", _repo_root(), "show", TAG + ":hooks/memory-router.sh"],
            stdout=subprocess.PIPE, check=True,
        ).stdout.decode("utf-8")
        lib = src.split('\ncmd="${1:-}"', 1)[0] + "\n"
        fd, cls.lib = tempfile.mkstemp(suffix=".lib.sh")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(lib)
        shim = ('#!/usr/bin/env bash\nsource "%s"\n'
                'record_run_economics_json "$1" "$2"\n') % cls.lib
        fd, cls.shim = tempfile.mkstemp(suffix=".shim.sh")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(shim)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.lib)
        os.unlink(cls.shim)

    def _env(self, khome, extra=None):
        env = {"HOME": "/tmp", "KIMIFLOW_HOME": khome, "PATH": os.environ.get("PATH", "")}
        if extra:
            env.update(extra)
        return env

    def _seed_salt(self, khome):
        gdir = os.path.join(khome, "metrics")
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "salt"), "w", encoding="utf-8") as fh:
            fh.write(_FIXED_SALT + "\n")

    def _project_ledger(self, root):
        return os.path.join(root, ".kimiflow", "project", "MEMORY-ECONOMICS.jsonl")

    def _global_ledger(self, khome):
        return os.path.join(khome, "metrics", "token-economics.jsonl")

    def _read(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    def _bash(self, root, run_dir, env):
        return subprocess.run(["bash", self.shim, root, run_dir],
                              stdout=subprocess.PIPE, text=True, check=True, env=env).stdout

    def _py(self, root, run_dir, env):
        with mock.patch.dict(os.environ, env, clear=True):
            result = economics.record_run_economics_json(root, run_dir)
        # Bash record_run_economics_json ends with `jq -n '{...}'` (no -c) -> pretty output.
        return contracts.dumps(result, pretty=True) + "\n"

    def _restore(self, path, content):
        # Put a ledger back to its pre-run state so the Python run sees the SAME starting
        # point (same root -> same hashes) bash saw: None -> absent (new-file branch);
        # otherwise rewrite the seeded content (existing-file/dedupe branch).
        if content is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)

    def _compare(self, populate, env_extra=None, preseed=None, assert_mode=False):
        root = tempfile.mkdtemp()
        khome = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, khome, ignore_errors=True)
        run_dir = os.path.join(root, "run")
        os.makedirs(run_dir)
        populate(root, run_dir)
        self._seed_salt(khome)
        if preseed:
            preseed(root, run_dir, khome)
        env = self._env(khome, env_extra)

        # snapshot the pre-run ledger state to replay before the Python run
        pre_proj = self._read(self._project_ledger(root))
        pre_glob = self._read(self._global_ledger(khome))

        bash_out = self._bash(root, run_dir, env)
        bash_proj = self._read(self._project_ledger(root))
        bash_glob = self._read(self._global_ledger(khome))
        bash_mode = self._mode(self._project_ledger(root))

        self._restore(self._project_ledger(root), pre_proj)
        self._restore(self._global_ledger(khome), pre_glob)

        py_out = self._py(root, run_dir, env)
        py_proj = self._read(self._project_ledger(root))
        py_glob = self._read(self._global_ledger(khome))
        py_mode = self._mode(self._project_ledger(root))

        self.assertEqual(_normalize(bash_out), _normalize(py_out), "stdout")
        self.assertEqual(_normalize(bash_proj or ""), _normalize(py_proj or ""), "project ledger")
        # spec section 12: `scope` is an intentional port-era addition to the global row —
        # strip it so every remaining byte stays grounded against the pinned Bash.
        py_glob_cmp = re.sub(r'"scope":"[a-z]+",', "", py_glob) if py_glob else py_glob
        self.assertEqual(_normalize(bash_glob or ""), _normalize(py_glob_cmp or ""), "global ledger")
        if assert_mode:
            self.assertEqual(bash_mode, py_mode, "project ledger mode")
            self.assertEqual(py_mode, 0o600, "project ledger 0600")

    def _mode(self, path):
        try:
            return os.stat(path).st_mode & 0o777
        except OSError:
            return None

    def _pop_saving(self, root, run_dir):
        proj = os.path.join(root, ".kimiflow", "project")
        os.makedirs(proj)
        with open(os.path.join(proj, "MEMORY.md"), "w") as fh:
            fh.write("# Memory\n- one two three\n")
        with open(os.path.join(proj, "USER.md"), "w") as fh:
            fh.write("# User\n- profile note\n")
        with open(os.path.join(run_dir, "RECALL.json"), "w") as fh:
            fh.write(json.dumps({"sources": {
                "memory": {"tokens_estimate": 8},
                "user_profile": {"tokens_estimate": 4},
                "learnings": {"hits": [
                    {"id": "learn_abc", "title": "router fix", "summary": "the cache bug",
                     "evidence": ["RESEARCH.md:3"]}]},
            }}))
        with open(os.path.join(run_dir, "RESEARCH.md"), "w") as fh:
            fh.write("# Research\nWe traced learn_abc to the cache bug.\n")
        with open(os.path.join(run_dir, "STATE.md"), "w") as fh:
            fh.write("# Run\n**Mode:** feature\n")

    def _pop_fallback(self, root, run_dir):
        proj = os.path.join(root, ".kimiflow", "project")
        os.makedirs(proj)
        with open(os.path.join(proj, "MEMORY.md"), "w") as fh:
            fh.write("# Memory\n- alpha beta gamma delta\n")
        with open(os.path.join(proj, "USER.md"), "w") as fh:
            fh.write("# User\n- one\n")
        with open(os.path.join(run_dir, "PLAN.md"), "w") as fh:
            fh.write("# Plan\nnothing reusable here\n")
        # no RECALL.json -> word_count fallback; no hits -> result unknown / confidence none

    def _pop_waste(self, root, run_dir):
        # used hit (corpus match) but tiny avoided_per_hit -> avoided>0, net<0 -> waste,
        # confidence medium, negative estimated_savings_percent floor.
        proj = os.path.join(root, ".kimiflow", "project")
        os.makedirs(proj)
        with open(os.path.join(run_dir, "RECALL.json"), "w") as fh:
            fh.write(json.dumps({"sources": {
                "memory": {"tokens_estimate": 8},
                "user_profile": {"tokens_estimate": 4},
                "learnings": {"hits": [
                    {"id": "learn_xyz", "title": "topic words here and more",
                     "summary": "extra tokens to push recall cost up beyond avoided"}]},
            }}))
        with open(os.path.join(run_dir, "RESEARCH.md"), "w") as fh:
            fh.write("traced learn_xyz here\n")

    def _pop_neutral(self, root, run_dir):
        # hit present but unused (no corpus match) and zero token costs -> net 0 -> neutral.
        os.makedirs(os.path.join(root, ".kimiflow", "project"))
        with open(os.path.join(run_dir, "RECALL.json"), "w") as fh:
            fh.write(json.dumps({"sources": {
                "memory": {"tokens_estimate": 0},
                "user_profile": {"tokens_estimate": 0},
                "learnings": {"hits": [{"id": "learn_unused"}]},
            }}))

    def _preseed_ledgers(self, root, run_dir, khome):
        # an existing ledger with a non-colliding row (must survive) + a colliding row
        # (must be replaced) -> exercises the dedupe/existing-file branch on BOTH runtimes.
        rel = paths.rel_path(root, run_dir)
        run_id = global_metrics.anonymous_hash_id(_FIXED_SALT, root + ":" + rel)
        proj = self._project_ledger(root)
        os.makedirs(os.path.dirname(proj), exist_ok=True)
        with open(proj, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"run": "other/run", "keep": True}) + "\n")
            fh.write(json.dumps({"run": rel, "stale": True}) + "\n")
        glob = self._global_ledger(khome)
        os.makedirs(os.path.dirname(glob), exist_ok=True)
        with open(glob, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_id": "OTHER", "keep": True}) + "\n")
            fh.write(json.dumps({"run_id": run_id, "stale": True}) + "\n")

    def test_parity_saving_with_global(self):
        self._compare(self._pop_saving, assert_mode=True)

    def test_parity_fallback_no_recall(self):
        self._compare(self._pop_fallback)

    def test_parity_global_disabled(self):
        self._compare(self._pop_saving, env_extra={"KIMIFLOW_GLOBAL_METRICS": "off"})

    def test_parity_waste_negative_floor(self):
        self._compare(self._pop_waste,
                      env_extra={"KIMIFLOW_ECONOMICS_AVOIDED_TOKENS_PER_HIT": "10"})

    def test_parity_neutral_zero_net(self):
        self._compare(self._pop_neutral)

    def test_parity_dedupe_existing_ledgers(self):
        self._compare(self._pop_saving, preseed=self._preseed_ledgers)


if __name__ == "__main__":
    unittest.main()
