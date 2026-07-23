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

from memory_router import propose, rows as rows_mod
from memory_router.__main__ import main

TAG = "kimiflow--v0.1.50"
_ISO_ENV = {"HOME": "/tmp", "KIMIFLOW_OBSIDIAN_URL": "http://127.0.0.1:9/"}
_TS = "2026-06-29T00:00:00Z"


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _env():
    return dict(_ISO_ENV, PATH=os.environ.get("PATH", ""))


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _fresh_learnings(root):
    """Hand-craft LEARNINGS.jsonl with literal evidence + fingerprints computed by the
    ported helper (so freshness passes and current_evidence_backed_rows keeps the rows --
    append_learning_row would sanitize evidence to NOT VERIFIED in a non-git temp dir)."""
    os.makedirs(os.path.join(root, ".kimiflow", "project"), exist_ok=True)
    with open(os.path.join(root, "a.py"), "w") as fh:
        fh.write("print(1)\n")
    with open(os.path.join(root, "b.py"), "w") as fh:
        fh.write("print(2)\n")
    fp_a = rows_mod.evidence_fingerprints_json(root, ["a.py"])
    fp_b = rows_mod.evidence_fingerprints_json(root, ["b.py"])
    learn = [
        {"id": "learn_std", "status": "current", "kind": "project_rule_confirmed", "topic": "t",
         "summary": "always run tests", "evidence": ["a.py"], "evidence_fingerprints": fp_a},
        {"id": "learn_dec", "status": "current", "kind": "important_decision", "topic": "t",
         "summary": "use python stdlib", "evidence": ["b.py"], "evidence_fingerprints": fp_b},
        {"id": "learn_skill", "status": "current", "kind": "learned", "topic": "t",
         "summary": "a workflow lesson", "evidence": ["a.py"], "evidence_fingerprints": fp_a},
        # excluded rows:
        {"id": "learn_sec", "status": "current", "kind": "learned", "sensitivity": "security",
         "summary": "secret", "evidence": ["a.py"], "evidence_fingerprints": fp_a},
        {"id": "learn_noev", "status": "current", "kind": "learned", "summary": "no ev", "evidence": []},
        {"id": "learn_nv", "status": "current", "kind": "learned", "summary": "nv",
         "evidence": ["NOT VERIFIED"]},
        {"id": "learn_stale", "status": "current", "kind": "project_rule_confirmed",
         "summary": "stale rule", "evidence": ["a.py"], "evidence_fingerprints": [{"ref": "a.py", "sha256": "deadbeef"}]},
    ]
    with open(os.path.join(root, ".kimiflow", "project", "LEARNINGS.jsonl"), "w") as fh:
        for r in learn:
            fh.write(json.dumps(r) + "\n")


class HelperCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _fresh_learnings(self.root)

    def test_current_evidence_backed_filters(self):
        kept = propose.current_evidence_backed_rows(
            os.path.join(self.root, ".kimiflow", "project", "LEARNINGS.jsonl"))
        ids = [r["id"] for r in kept]
        self.assertIn("learn_std", ids)
        self.assertIn("learn_dec", ids)
        self.assertNotIn("learn_sec", ids)    # security
        self.assertNotIn("learn_noev", ids)   # no evidence
        self.assertNotIn("learn_nv", ids)     # NOT VERIFIED

    def test_probationary_learning_is_not_a_proposal_candidate(self):
        path = os.path.join(self.root, ".kimiflow", "project", "LEARNINGS.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "learn_probation", "status": "current", "maturity": "probationary",
                "kind": "project_rule_confirmed", "summary": "not proven twice",
                "evidence": ["a.py"], "evidence_fingerprints": [],
            }) + "\n")
        ids = [row["id"] for row in propose.current_evidence_backed_rows(path)]
        self.assertNotIn("learn_probation", ids)

    def test_candidates_type_mapping(self):
        rows = propose.current_evidence_backed_rows(
            os.path.join(self.root, ".kimiflow", "project", "LEARNINGS.jsonl"))
        props = propose.proposal_candidates_json(rows, [], _TS)
        by_id = {p["id"]: p for p in props}
        self.assertEqual(by_id["learn_std"]["type"], "standard")
        self.assertEqual(by_id["learn_std"]["target_path"], ".kimiflow/STANDARDS.md")
        self.assertEqual(by_id["learn_dec"]["type"], "decision")
        self.assertEqual(by_id["learn_skill"]["type"], "skill")
        self.assertEqual(by_id["learn_std"]["status"], "pending")
        self.assertEqual(list(by_id["learn_std"].keys())[:5],
                         ["id", "learning_id", "type", "kind", "target_path"])

    def test_candidates_prev_state_last_wins(self):
        rows = propose.current_evidence_backed_rows(
            os.path.join(self.root, ".kimiflow", "project", "LEARNINGS.jsonl"))
        state = [{"id": "learn_std", "status": "approved", "reason": "r1"},
                 {"id": "learn_std", "status": "rejected", "reason": "r2"}]
        props = propose.proposal_candidates_json(rows, state, _TS)
        std = next(p for p in props if p["id"] == "learn_std")
        self.assertEqual(std["status"], "rejected")  # last matching state row
        self.assertEqual(std["reason"], "r2")

    def test_notification_message(self):
        props = [{"type": "standard", "status": "pending"}, {"type": "decision", "status": "approved"}]
        n = propose.proposal_notification_json(props)
        self.assertEqual(n["message"],
                         "Learning proposals: 1 pending, 1 approved, 0 applied, 0 rejected, 0 need revalidation.")

    def test_append_project_line_dedups(self):
        f = os.path.join(self.root, "STD.md")
        self.assertTrue(propose.append_project_line(f, "Title", "sum one", "- sum one (x)"))
        self.assertFalse(propose.append_project_line(f, "Title", "sum one", "- sum one (y)"))  # dup summary
        self.assertTrue(_read(f).startswith("# Title\n\n"))
        self.assertEqual(_read(f).count("- sum one"), 1)


class ProposeRunCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        _fresh_learnings(self.root)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        envp = mock.patch.dict(os.environ, _env(), clear=True)
        envp.start()
        self.addCleanup(envp.stop)
        tsp = mock.patch("memory_router.clock.iso_now", return_value=_TS)
        tsp.start()
        self.addCleanup(tsp.stop)

    def run_propose(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = propose.run(["--root", self.root] + argv)
        return code, out.getvalue(), err.getvalue()

    def obj(self, argv=None):
        code, out, _ = self.run_propose(argv or [])
        self.assertEqual(code, 0)
        return json.loads(out)

    def test_preview_key_order_and_status(self):
        o = self.obj()
        self.assertEqual(list(o.keys()),
                         ["schema_version", "status", "path", "state_path", "written",
                          "proposals", "apply_result", "notification"])
        self.assertEqual(o["status"], "preview")
        self.assertEqual(o["written"], False)
        self.assertEqual(o["proposals"]["total"], 4)  # std, dec, skill, stale (all proposal kinds)

    def test_unknown_id_silently_ignored(self):
        # Bash's unknown-id gate is dead code (jq `.`-rebinding bug); an unknown --approve id
        # is silently accepted -> written, but nothing approved. The port replicates this.
        o = self.obj(["--approve", "nope_id"])
        self.assertEqual(o["status"], "written")
        self.assertEqual(o["proposals"]["approved"], 0)

    def test_approve_writes_state_and_status(self):
        o = self.obj(["--approve", "learn_std"])
        self.assertEqual(o["status"], "written")
        self.assertEqual(o["written"], True)
        self.assertEqual(o["proposals"]["approved"], 1)
        state = _read(os.path.join(self.project, "PROPOSALS.jsonl"))
        self.assertIn('"status":"approved"', state)
        self.assertTrue(os.path.isfile(os.path.join(self.project, "PENDING-PROPOSALS.md")))

    def test_apply_creates_standards_and_decisions(self):
        self.obj(["--approve", "learn_std", "--approve", "learn_dec"])
        o = self.obj(["--approve", "learn_std", "--approve", "learn_dec", "--apply"])
        self.assertEqual(o["status"], "applied")
        self.assertEqual(o["apply_result"]["applied_ids"], ["learn_std", "learn_dec"])
        self.assertIn("always run tests", _read(os.path.join(self.root, ".kimiflow", "STANDARDS.md")))
        self.assertIn("use python stdlib", _read(os.path.join(self.root, ".kimiflow", "DECISIONS.md")))

    def test_apply_skill_writes_draft(self):
        self.obj(["--approve", "learn_skill"])
        o = self.obj(["--approve", "learn_skill", "--apply"])
        self.assertEqual(o["apply_result"]["manual_ids"], ["learn_skill"])
        draft = os.path.join(self.project, "SKILL-DRAFTS", "learn_skill.md")
        self.assertTrue(os.path.isfile(draft))
        self.assertIn("# Skill Draft: learn_skill", _read(draft))

    def test_stale_evidence_blocks_approval(self):
        code, _, err = self.run_propose(["--approve", "learn_stale"])
        self.assertEqual(code, 1)
        self.assertIn("evidence stale; refresh learning review before approval", err)
        self.assertIn("learn_stale:evidence_changed_or_missing", err)
        # state was written with the needs_revalidation mark before the die
        self.assertIn('"needs_revalidation"', _read(os.path.join(self.project, "PROPOSALS.jsonl")))

    def test_reject_with_reason(self):
        o = self.obj(["--reject", "learn_std", "--reason", "not useful"])
        self.assertEqual(o["proposals"]["rejected"], 1)
        self.assertIn('"reason":"not useful"', _read(os.path.join(self.project, "PROPOSALS.jsonl")))

    def test_unknown_arg(self):
        code, _, err = self.run_propose(["--bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: propose: unknown argument: --bogus\n")

    def test_dispatch_registration(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), contextlib.redirect_stdout(out):
            code = main(["propose", "--root", self.root])
        self.assertEqual(code, 0)
        self.assertIn('"schema_version":1', out.getvalue())


def _tools_present():
    if not all(shutil.which(t) for t in ("bash", "jq", "git", "shasum")):
        return False
    probe = subprocess.run(
        ["git", "-C", _repo_root(), "cat-file", "-e", TAG + ":hooks/memory-router.sh"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _strip(text):
    text = re.sub(r'"(created_at|updated_at|applied_at)":"[^"]*"', r'\1_TS', text)
    text = re.sub(r"(Generated: ).*", r"\1<TS>", text)
    text = re.sub(r"^- \d{4}-\d{2}-\d{2}:", "- <DATE>:", text, flags=re.M)
    return text


@unittest.skipUnless(_tools_present(), "bash/jq/git/shasum or pinned tag unavailable")
class ProposeParityCase(unittest.TestCase):
    """Grounds `propose` stdout + the written PROPOSALS.jsonl / PENDING-PROPOSALS.md /
    STANDARDS.md / DECISIONS.md byte-for-byte vs the pinned bash (timestamps/dates normalized).
    State is hand-crafted identically on both roots so fingerprints recompute equal."""

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

    def _roots(self):
        rb, rp = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rb, ignore_errors=True)
        self.addCleanup(shutil.rmtree, rp, ignore_errors=True)
        _fresh_learnings(rp)
        shutil.copytree(os.path.join(rp, ".kimiflow"), os.path.join(rb, ".kimiflow"))
        for f in ("a.py", "b.py"):
            shutil.copy(os.path.join(rp, f), os.path.join(rb, f))
        return rb, rp

    def _bash(self, root, argv):
        p = subprocess.run(["bash", self.script, "propose", "--root", root] + argv,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env())
        return p.returncode, p.stdout, p.stderr

    def _py(self, root, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, _env(), clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = propose.run(["--root", root] + argv)
        return code, out.getvalue(), err.getvalue()

    def test_stdout_parity(self):
        for argv in ([], ["--pretty"], ["--approve", "learn_std"], ["--reject", "learn_dec", "--reason", "x"],
                     ["--approve", "learn_stale"], ["--approve", "nope"]):
            rb, rp = self._roots()
            cb, ob, eb = self._bash(rb, argv)
            cp, op, ep = self._py(rp, argv)
            self.assertEqual((cb, _strip(ob), eb), (cp, _strip(op), ep), "argv=%r" % argv)

    def test_apply_written_files_parity(self):
        rb, rp = self._roots()
        for root, runner in ((rb, self._bash), (rp, self._py)):
            runner(root, ["--approve", "learn_std", "--approve", "learn_dec", "--approve", "learn_skill"])
            runner(root, ["--approve", "learn_std", "--approve", "learn_dec", "--approve", "learn_skill", "--apply"])
        proj = os.path.join(".kimiflow", "project")
        for rel in (os.path.join(".kimiflow", "STANDARDS.md"), os.path.join(".kimiflow", "DECISIONS.md"),
                    os.path.join(proj, "PROPOSALS.jsonl"), os.path.join(proj, "PENDING-PROPOSALS.md"),
                    os.path.join(proj, "SKILL-DRAFTS", "learn_skill.md")):
            self.assertEqual(_strip(_read(os.path.join(rb, rel))), _strip(_read(os.path.join(rp, rel))), rel)


if __name__ == "__main__":
    unittest.main()
