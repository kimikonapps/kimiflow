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

from memory_router import review

TAG = "kimiflow--v0.1.50"
_FIXED_SALT = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class ReviewHelperCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _f(self, name, content):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    # ---- quality_gate_json ----
    def test_quality_ok(self):
        q = review.quality_gate_json(
            "learned",
            "scope=this project; verified=router regression test @ 2026-07-18; flush the cache after every write",
            ["RESEARCH.md:3"],
        )
        self.assertTrue(q["ok"])
        self.assertEqual(q["reasons"], [])
        self.assertGreaterEqual(q["words"], 7)

    def test_quality_reason_order(self):
        # too_short + generic + missing_evidence (empty), in that fixed order
        q = review.quality_gate_json("learned", "done", [])
        self.assertEqual(q["reasons"][0], "too_short")
        self.assertIn("too_generic", q["reasons"])
        self.assertIn("missing_verified_evidence", q["reasons"])
        self.assertLess(q["reasons"].index("too_generic"), q["reasons"].index("missing_verified_evidence"))

    def test_quality_not_verified_evidence(self):
        q = review.quality_gate_json("learned", "the cache must be flushed after every single write op", ["NOT VERIFIED"])
        self.assertIn("missing_verified_evidence", q["reasons"])

    def test_quality_kind_specific(self):
        # project_rule_confirmed without a rule keyword -> reason
        q = review.quality_gate_json("project_rule_confirmed",
                                     "the seven words here are plainly long enough now", ["x:1"])
        self.assertIn("project_rule_without_rule", q["reasons"])
        q2 = review.quality_gate_json("project_rule_confirmed",
                                      "every commit must always follow the standard convention here", ["x:1"])
        self.assertNotIn("project_rule_without_rule", q2["reasons"])

    def test_durable_learning_requires_scope_and_dated_basis(self):
        q = review.quality_gate_json(
            "learned",
            "React Query cache invalidation must run after each successful mutation",
            ["RESEARCH.md:3"],
        )
        self.assertIn("learning_context_missing_scope", q["reasons"])
        self.assertIn("learning_context_missing_verification", q["reasons"])
        self.assertFalse(q["ok"])

        scoped = review.quality_gate_json(
            "learned",
            "scope=React Query v5 in this project; verified=official docs and regression test @ 2026-07-18; invalidate the cache after each successful mutation",
            ["RESEARCH.md:4"],
        )
        self.assertTrue(scoped["ok"])
        self.assertEqual(scoped["reasons"], [])

    def test_learning_context_is_ecosystem_agnostic(self):
        for summary in (
            "AVAudio capture must reject a zero-channel device format before tap installation",
            "pytest fixtures must use tmp_path to isolate every filesystem mutation",
            "Memory Router cache entries must refresh after every durable learning write",
        ):
            q = review.quality_gate_json("learned", summary, ["RESEARCH.md:5"])
            self.assertIn("learning_context_missing_scope", q["reasons"])
            self.assertIn("learning_context_missing_verification", q["reasons"])

    def test_learning_context_rejects_empty_basis_and_invalid_date(self):
        empty = review.quality_gate_json(
            "learned",
            "scope=React Query v5; verified=@ 2026-07-18; invalidate the cache after mutation",
            ["RESEARCH.md:6"],
        )
        self.assertIn("learning_context_missing_verification", empty["reasons"])

        whitespace = review.quality_gate_json(
            "learned",
            "scope=   ; verified=   @ 2026-07-18; useful learning with enough words here",
            ["RESEARCH.md:6"],
        )
        self.assertIn("learning_context_missing_scope", whitespace["reasons"])
        self.assertIn("learning_context_missing_verification", whitespace["reasons"])

        invalid = review.quality_gate_json(
            "learned",
            "scope=React Query v5; verified=official docs @ 2026-99-99; invalidate the cache after mutation",
            ["RESEARCH.md:7"],
        )
        self.assertIn("learning_context_invalid_date", invalid["reasons"])

    # ---- first_substantive_tsv ----
    def test_first_substantive_skips_heading_fence_empty(self):
        # skips: empty(1), heading(2), both fence markers(3,4); returns the content line(5).
        p = self._f("a.md", "\n# Heading here\n```\n```\n  real content line  \n")
        self.assertEqual(review.first_substantive_tsv(p), "5\treal content line")

    def test_first_substantive_missing(self):
        self.assertIsNone(review.first_substantive_tsv(os.path.join(self.dir, "none.md")))

    # ---- structured_learning_tsv ----
    def test_structured_kind_anchor_keeps_prefix(self):
        p = self._f("r.md", "intro\n- **What was learned:** the cache needs a flush\nmore\n")
        self.assertEqual(review.structured_learning_tsv(p, "learned"),
                         "2\tWhat was learned: the cache needs a flush")

    def test_structured_no_match(self):
        p = self._f("r.md", "nothing structured here\n")
        self.assertIsNone(review.structured_learning_tsv(p, "learned"))

    def test_structured_unknown_kind(self):
        p = self._f("r.md", "anything\n")
        self.assertIsNone(review.structured_learning_tsv(p, "bogus"))

    # ---- learning_summary_json ----
    def test_learning_summary_structured_then_fallback(self):
        p = self._f("r.md", "- What was learned: alpha beta\n")
        info = review.learning_summary_json(p, "learned")
        self.assertEqual(info, {"line": 1, "summary": "What was learned: alpha beta", "source": "structured"})
        p2 = self._f("r2.md", "just a plain first line\n")
        info2 = review.learning_summary_json(p2, "learned")
        self.assertEqual(info2["source"], "fallback")
        self.assertEqual(info2["summary"], "just a plain first line")

    def test_learning_summary_cut_320_codepoints(self):
        long = "What was learned: " + ("x" * 400)
        p = self._f("r.md", long + "\n")
        info = review.learning_summary_json(p, "learned")
        self.assertEqual(len(info["summary"]), 320)

    def test_learning_summary_cut_320_codepoints_multibyte(self):
        # spec 12 row 196: the port slices by CODEPOINT (Python [:320]); BSD `cut -c` is
        # byte-based under the C locale, so a >320-codepoint umlaut summary is the blessed
        # divergence. Confirm the port keeps 320 codepoints (640 bytes), not 320 bytes.
        long = "What was learned: " + (chr(0x00e4) * 400)
        p = self._f("r.md", long + "\n")
        info = review.learning_summary_json(p, "learned")
        self.assertEqual(len(info["summary"]), 320)
        self.assertEqual(len(info["summary"].encode("utf-8")), 320 + (320 - 18))

    # ---- review_candidate_json ----
    def test_candidate_first_matching_file(self):
        run_dir = os.path.join(self.dir, "run")
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "RESEARCH.md"), "w") as fh:
            fh.write("- What was learned: the router cache must be flushed after each write op\n")
        cand = review.review_candidate_json(self.dir, run_dir, "what_was_learned", "learned",
                                            "run-learning", ["DIAGNOSIS.md", "RESEARCH.md"])
        self.assertEqual(cand["question"], "what_was_learned")
        self.assertEqual(cand["scope"], "project")
        self.assertEqual(cand["topic"], "run-learning")
        self.assertEqual(cand["evidence"], ["run/RESEARCH.md:1"])
        self.assertEqual(list(cand.keys()),
                         ["question", "kind", "scope", "topic", "summary", "evidence",
                          "extraction_source", "target", "sensitivity", "confidence", "quality"])

    def test_candidate_none_when_no_file(self):
        run_dir = os.path.join(self.dir, "run")
        os.makedirs(run_dir)
        self.assertIsNone(review.review_candidate_json(self.dir, run_dir, "q", "learned",
                                                       "t", ["RESEARCH.md"]))

    # ---- run_lifecycle_json next_actions unique ----
    def test_lifecycle_next_actions_unique_sorted(self):
        with mock.patch.object(review.status_mod, "status_json", return_value={
            "usefulness": {}, "curation": {"recommended": True, "reasons": ["zeta", "alpha"]},
            "provider": {"sync": {"status": "ok", "pending_count": 2, "direct_write_ready": False}}}):
            obj = review.run_lifecycle_json(self.dir, self.dir + "/run", "recorded",
                                            self.dir + "/run/LEARNING-REVIEW.md", 1, True,
                                            {"recorded": True, "row": {}}, {"pending": 3})
        self.assertEqual(obj["next_actions"],
                         ["alpha", "provider_sync_pending", "review_learning_proposals", "zeta"])
        self.assertEqual(obj["proposals"], {"notification": {"pending": 3}})


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "git")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _norm(text):
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", "TS", text)
    text = re.sub(r"((?:learn|user)_\d{8}_[a-z0-9-]+)_\d+", r"\1_PID", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "DAY", text)
    return text


# review-run-specific artifacts to ground, all relative to root (RECALL.sqlite excluded:
# engine differs, spec 12; MEMORY.md excluded: blessed -c body divergence, spec 12 row 181).
_ARTIFACTS = (
    "run/LEARNING-REVIEW.md",
    "run/RUN-LIFECYCLE.json",
    "run/RUN-LIFECYCLE.md",
    ".kimiflow/project/LEARNINGS.jsonl",
    ".kimiflow/project/MEMORY-ECONOMICS.jsonl",
)


@unittest.skipUnless(_tools_present(), "bash/jq/git or pinned tag unavailable")
class ReviewParityCase(unittest.TestCase):
    """Grounds review-run byte-for-byte vs the pinned bash subcommand. --write cases run
    bash then reset the root + KIMIFLOW_HOME to the canonical pre-run state (deterministic
    populate + same pre-seeded salt) before the Python run, so the same root yields the same
    anonymized hashes + evidence fingerprints; only timestamps + the pid-suffixed learning
    ids are normalized."""

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

    def _env(self, khome, extra=None):
        env = {"HOME": "/tmp", "KIMIFLOW_HOME": khome,
               "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/",
               "PATH": os.environ.get("PATH", "")}
        if extra:
            env.update(extra)
        return env

    def _seed_salt(self, khome):
        gdir = os.path.join(khome, "metrics")
        os.makedirs(gdir, exist_ok=True)
        with open(os.path.join(gdir, "salt"), "w", encoding="utf-8") as fh:
            fh.write(_FIXED_SALT + "\n")

    def _global_ledger(self, khome):
        return os.path.join(khome, "metrics", "token-economics.jsonl")

    def _read(self, path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None

    def _bash(self, root, khome, tail, extra=None):
        proc = subprocess.run(["bash", self.script, "review-run", "--root", root,
                               "--run", os.path.join(root, "run")] + tail,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              env=self._env(khome, extra))
        return proc.returncode, proc.stdout, proc.stderr

    def _py(self, root, khome, tail, extra=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self._env(khome, extra), clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review.run(["--root", root, "--run", os.path.join(root, "run")] + tail)
        return code, out.getvalue(), err.getvalue()

    def _read_artifacts(self, root, khome):
        arts = {rel: self._read(os.path.join(root, rel)) for rel in _ARTIFACTS}
        arts["__global__"] = self._read(self._global_ledger(khome))
        return arts

    # populate fixtures -------------------------------------------------
    def _pop_one_candidate(self, root):
        run_dir = os.path.join(root, "run")
        os.makedirs(run_dir, exist_ok=True)
        os.makedirs(os.path.join(root, ".kimiflow", "project"), exist_ok=True)
        # RESEARCH.md feeds candidate 1 (learned, no kind check) AND candidate 4
        # (important_decision, via fallback) -> the line carries decision keywords
        # (decided/keep/because) so BOTH pass the quality gate -> a clean --write.
        with open(os.path.join(run_dir, "RESEARCH.md"), "w") as fh:
            fh.write("# Research\n- What was learned: scope=this project; "
                     "verified=regression fixture @ 2026-07-18; we decided to keep the explicit cache "
                     "flush because stale reads after every write were the real trap to avoid\n")
        with open(os.path.join(run_dir, "STATE.md"), "w") as fh:
            fh.write("# Run\n**Mode:** feature\n")
        with open(os.path.join(run_dir, "RECALL.json"), "w") as fh:
            fh.write(json.dumps({"sources": {"memory": {"tokens_estimate": 5},
                                             "user_profile": {"tokens_estimate": 0}}}))

    def _pop_quality_closed(self, root):
        run_dir = os.path.join(root, "run")
        os.makedirs(run_dir, exist_ok=True)
        os.makedirs(os.path.join(root, ".kimiflow", "project"), exist_ok=True)
        with open(os.path.join(run_dir, "RESEARCH.md"), "w") as fh:
            fh.write("- What was learned: scope=this project; "
                     "verified=regression fixture @ 2026-07-18; various stuff\n")

    def _pop_empty(self, root):
        os.makedirs(os.path.join(root, "run"), exist_ok=True)
        os.makedirs(os.path.join(root, ".kimiflow", "project"), exist_ok=True)

    def _fresh(self, populate):
        root = tempfile.mkdtemp()
        khome = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, khome, ignore_errors=True)
        populate(root)
        self._seed_salt(khome)
        return root, khome

    def _reset(self, root, khome, populate):
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(khome, ignore_errors=True)
        os.makedirs(root, exist_ok=True)
        os.makedirs(khome, exist_ok=True)
        populate(root)
        self._seed_salt(khome)

    # stdout/stderr/exit-only parity (no writes) -----------------------
    def _compare_stdio(self, populate, tail, extra=None):
        rb, kb = self._fresh(populate)
        rp, kp = self._fresh(populate)
        bc, bo, be = self._bash(rb, kb, tail, extra)
        pc, po, pe = self._py(rp, kp, tail, extra)
        self.assertEqual(bc, pc, "exit")
        self.assertEqual(_norm(bo), _norm(po), "stdout")
        self.assertEqual(_norm(be), _norm(pe), "stderr")

    def test_preview_parity(self):
        self._compare_stdio(self._pop_one_candidate, [])

    def test_preview_pretty_parity(self):
        self._compare_stdio(self._pop_one_candidate, ["--pretty"])

    def test_quality_closed_parity(self):
        self._compare_stdio(self._pop_quality_closed, [])

    def test_no_candidates_parity(self):
        self._compare_stdio(self._pop_empty, [])

    def test_unknown_arg_parity(self):
        self._compare_stdio(self._pop_empty, ["--bogus"])

    def test_skip_preview_parity(self):
        self._compare_stdio(self._pop_one_candidate, ["--skip", "intentionally trivial"])

    # write parity (same root, reset between) --------------------------
    def _compare_write(self, populate, tail):
        root, khome = self._fresh(populate)
        bc, bo, be = self._bash(root, khome, tail)
        self.assertEqual(bc, 0, "bash exit (stderr=%r)" % be)
        bash_arts = self._read_artifacts(root, khome)
        self._reset(root, khome, populate)
        pc, po, pe = self._py(root, khome, tail)
        self.assertEqual(pc, 0, "py exit (stderr=%r)" % pe)
        py_arts = self._read_artifacts(root, khome)
        self.assertEqual(_norm(bo), _norm(po), "stdout")
        self.assertEqual(_norm(be), _norm(pe), "stderr")
        for key in bash_arts:
            py_val = py_arts[key] or ""
            if key == "__global__" and py_val:
                # spec section 12: `scope` is an intentional port-era addition to the
                # global row — strip it so the remaining bytes stay Bash-grounded.
                py_val = re.sub(r'"scope":"[a-z]+",', "", py_val)
            self.assertEqual(_norm(bash_arts[key] or ""), _norm(py_val), "artifact %s" % key)

    def test_skip_write_parity(self):
        self._compare_write(self._pop_one_candidate, ["--skip", "trivial run", "--write"])

    def test_record_write_parity(self):
        self._compare_write(self._pop_one_candidate, ["--write"])


if __name__ == "__main__":
    unittest.main()
