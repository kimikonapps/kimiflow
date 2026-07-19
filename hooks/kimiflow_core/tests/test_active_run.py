import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import active_run, workspace_preflight


def run_main(args, stdin_text=None):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        if stdin_text is None:
            rc = active_run.main(args)
        else:
            with mock.patch("sys.stdin", io.StringIO(stdin_text)):
                rc = active_run.main(args)
    return rc, out.getvalue()


class TestAffectedPathsHeaders(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def write_state(self, text):
        path = os.path.join(self.d, "STATE.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_affected_files_header_still_parses(self):
        path = self.write_state("Affected files: src/a.txt, src/b.txt\n")
        self.assertEqual(active_run.affected_paths(path), ["src/a.txt", "src/b.txt"])

    def test_affected_paths_header_still_parses(self):
        path = self.write_state("Affected paths: src/a.txt\n")
        self.assertEqual(active_run.affected_paths(path), ["src/a.txt"])

    def test_files_header_parses(self):
        path = self.write_state("Files: src/a.txt\n")
        self.assertEqual(active_run.affected_paths(path), ["src/a.txt"])

    def test_paths_header_parses(self):
        path = self.write_state("Paths: src/a.txt\n")
        self.assertEqual(active_run.affected_paths(path), ["src/a.txt"])

    def test_touches_header_parses(self):
        path = self.write_state("Touches: src/a.txt\n")
        self.assertEqual(active_run.affected_paths(path), ["src/a.txt"])

    def test_headers_are_case_insensitive(self):
        path = self.write_state("touches: src/a.txt\n")
        self.assertEqual(active_run.affected_paths(path), ["src/a.txt"])

    def test_header_allows_space_before_colon_like_gate(self):
        path = self.write_state("Files : src/a.txt\n")
        self.assertEqual(active_run.affected_paths(path), ["src/a.txt"])

    def test_new_header_supports_markdown_list(self):
        path = self.write_state("- **Touches:**\n  - src/a.txt\n  - src/b.txt\n")
        self.assertEqual(active_run.affected_paths(path), ["src/a.txt", "src/b.txt"])

    def write_plan(self, text):
        path = os.path.join(self.d, "PLAN.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_run_affected_paths_falls_back_to_plan_md(self):
        self.write_state("Status: active\n")
        self.write_plan("Files: src/a.txt\n")
        self.assertEqual(active_run.run_affected_paths(self.d), ["src/a.txt"])

    def test_run_affected_paths_prefers_state_over_plan_md(self):
        self.write_state("Affected files: src/state.txt\n")
        self.write_plan("Files: src/plan.txt\n")
        self.assertEqual(active_run.run_affected_paths(self.d), ["src/state.txt"])

    def test_run_affected_paths_empty_without_declaration(self):
        self.write_state("Status: active\n")
        self.write_plan("Decision: nothing declared here.\n")
        self.assertEqual(active_run.run_affected_paths(self.d), [])


@unittest.skipUnless(shutil.which("jq"), "jq required by active-run commands")
class TestOutcomeEvaluation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.repo = os.path.join(self.temp, "repo")
        os.mkdir(self.repo)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as handle:
            handle.write(".kimiflow/\n")
        with open(os.path.join(self.repo, "tracked.txt"), "w", encoding="utf-8") as handle:
            handle.write("base\n")
        self.git("add", ".gitignore", "tracked.txt")
        self.git("commit", "-m", "base")
        self.run_rel = ".kimiflow/demo"
        self.run_dir = os.path.join(self.repo, self.run_rel)
        os.makedirs(self.run_dir)
        self.write_state()
        self.plugin = os.path.join(self.temp, "plugin")
        os.mkdir(self.plugin)
        self.router = os.path.join(self.temp, "memory-router")
        self.router_log = os.path.join(self.temp, "router.log")
        self.write_router()
        self.env = {
            "KIMIFLOW_PLUGIN_ROOT": self.plugin,
            "KIMIFLOW_HOST": "codex",
            "CODEX_THREAD_ID": "owner-session",
            "KIMIFLOW_MEMORY_ROUTER": self.router,
            "KIMIFLOW_TEST_ROUTER_LOG": self.router_log,
        }
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        rc, _ = run_main(["start", "--run", self.run_rel, "--root", self.repo, "--write"])
        self.assertEqual(rc, 0)

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", self.repo] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

    def write_state(self):
        with open(os.path.join(self.run_dir, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "Flow schema: 4\n"
                "Status: active\n"
                "Mode: feature\n"
                "Scope: small\n"
                "Affected files: tracked.txt\n"
                "Phase 0: done\nPhase 1: done\nPhase 2: done\nPhase 3: done\n"
                "Phase 4: done\nPhase 5: done\nPhase 6: done\nPhase 7: done\n"
            )

    def write_router(self):
        with open(self.router, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" >> \"${KIMIFLOW_TEST_ROUTER_LOG:?}\"\n"
                "case \"$1\" in\n"
                "review-run)\n"
                "  if [ \"${KIMIFLOW_TEST_MUTATE_AFFECTED:-0}\" = 1 ]; then printf 'mutated during learning\\n' > \"$3/tracked.txt\"; fi\n"
                "  if [ \"${KIMIFLOW_TEST_REVIEW_WRITE:-0}\" = 1 ]; then mkdir -p \"$3/.kimiflow/project\"; printf partial > \"$3/.kimiflow/project/STRATEGIES.jsonl\"; printf partial > \"$3/$5/LEARNING-REVIEW.md\"; fi\n"
                "  if [ \"${KIMIFLOW_TEST_REVIEW_FAIL:-0}\" = 1 ]; then exit 29; fi\n"
                "  printf '%s\\n' '{\"status\":\"skipped\",\"written\":true}'; exit 0 ;;\n"
                "verify-run) printf '%s\\n' 'LEARNING_REVIEW OPEN'; exit 0 ;;\n"
                "evaluate-run)\n"
                "  root=; run=; terminal=; shift\n"
                "  while [ \"$#\" -gt 0 ]; do case \"$1\" in --root) shift; root=$1 ;; --run) shift; run=$1 ;; --terminal) shift; terminal=$1 ;; esac; shift; done\n"
                "  if [ \"${KIMIFLOW_TEST_EVALUATE_WRITE:-0}\" = 1 ]; then mkdir -p \"$root/.kimiflow/project\"; printf partial > \"$root/.kimiflow/project/STRATEGY-OUTCOMES.jsonl\"; printf partial > \"$root/$run/OUTCOME-EVALUATION.json\"; fi\n"
                "  if [ \"${KIMIFLOW_TEST_EVALUATE_FAIL:-0}\" = 1 ]; then exit 23; fi\n"
                "  if [ \"${KIMIFLOW_TEST_EVALUATE_MALFORMED:-0}\" = 1 ]; then printf '%s\\n' '{\"status\":\"evaluated\",\"written\":false,\"evaluation\":{}}'; exit 0; fi\n"
                "  printf '{\"status\":\"evaluated\",\"written\":true,\"evaluation\":{\"id\":\"out_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"terminal\":\"%s\",\"classification\":\"verified_success\",\"promotable\":true}}\\n' \"$terminal\"; exit 0 ;;\n"
                "*) exit 2 ;;\n"
                "esac\n"
            )
        os.chmod(self.router, 0o700)

    def enable_conformance(self, basis="pending"):
        state_path = os.path.join(self.run_dir, "STATE.md")
        with open(state_path, "a", encoding="utf-8") as handle:
            handle.write("Conformance contract: 1\nConformance basis: %s\n" % basis)
        active_path = active_run.active_file(self.repo)
        with open(active_path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        value["conformance_contract"] = "1"
        with open(active_path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)

    def test_finish_outcome_evaluation_is_transactional(self):
        project = os.path.join(self.repo, ".kimiflow", "project")
        os.makedirs(project)
        existing = os.path.join(project, "EXISTING.json")
        with open(existing, "w", encoding="utf-8") as handle:
            handle.write('{"existing":true}\n')

        with mock.patch.dict(os.environ, {
            "KIMIFLOW_TEST_EVALUATE_WRITE": "1",
            "KIMIFLOW_TEST_EVALUATE_FAIL": "1",
        }):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])
        self.assertEqual(rc, 23)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))
        self.assertTrue(os.path.isfile(existing))
        self.assertFalse(os.path.exists(os.path.join(project, "STRATEGY-OUTCOMES.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "OUTCOME-EVALUATION.json")))

        rc, out = run_main(["finish", "--root", self.repo, "--write"])
        self.assertEqual(rc, 0)
        result = json.loads(out)
        self.assertEqual(result["outcome"]["outcome_evaluation"]["classification"], "verified_success")
        self.assertFalse(os.path.exists(active_run.active_file(self.repo)))
        with open(self.router_log, "r", encoding="utf-8") as handle:
            self.assertIn("evaluate-run --root %s --run . --terminal done --write" % (
                self.repo,
            ), handle.read())

    def test_same_run_restart_preserves_immutable_selectors_and_frontend_pin(self):
        active_path = active_run.active_file(self.repo)
        with open(active_path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        value["frontend_quality_contract"] = 1
        value["scope"] = "large"
        with open(active_path, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
        state_path = os.path.join(self.run_dir, "STATE.md")
        with open(state_path, "r", encoding="utf-8") as handle:
            state_text = handle.read()
        with open(state_path, "w", encoding="utf-8") as handle:
            handle.write(state_text.replace("Scope: small", "Scope: large"))

        rc, _ = run_main([
            "start", "--run", self.run_rel, "--root", self.repo,
            "--mode", "fix", "--scope", "small", "--write",
        ])

        self.assertEqual(rc, 0)
        with open(active_path, "r", encoding="utf-8") as handle:
            restarted = json.load(handle)
        self.assertEqual(restarted["frontend_quality_contract"], 1)
        self.assertEqual(restarted["mode"], "feature")
        self.assertEqual(restarted["scope"], "large")

    def test_finish_rejects_malformed_or_unwritten_evaluation(self):
        with mock.patch.dict(os.environ, {"KIMIFLOW_TEST_EVALUATE_MALFORMED": "1"}):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])
        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))

    def test_finish_refuses_closed_conformance_before_learning(self):
        self.enable_conformance()
        gate = os.path.join(self.temp, "conformance-gate")
        with open(gate, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nprintf 'CONFORMANCE_GATE\\tCLOSED\\tblockers=1\\treason=receipt-missing\\tdetail=conformance_receipt_missing\\n'\n")
        os.chmod(gate, 0o700)

        with mock.patch.dict(os.environ, {"KIMIFLOW_CONFORMANCE_GATE": gate}):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))
        self.assertFalse(os.path.exists(self.router_log))

    def test_finish_accepts_open_conformance_before_learning(self):
        self.enable_conformance("current")
        gate = os.path.join(self.temp, "conformance-gate-open")
        with open(gate, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nprintf 'CONFORMANCE_GATE\\tOPEN\\tblockers=0\\treason=clean\\tdetail=basis=%064d\\n' 0\n")
        os.chmod(gate, 0o700)

        with mock.patch.dict(os.environ, {"KIMIFLOW_CONFORMANCE_GATE": gate}):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])

        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(active_run.active_file(self.repo)))
        with open(self.router_log, "r", encoding="utf-8") as handle:
            self.assertIn("review-run", handle.read())

    def test_finish_refuses_malformed_open_conformance(self):
        self.enable_conformance()
        gate = os.path.join(self.temp, "conformance-gate-malformed")
        with open(gate, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nprintf 'CONFORMANCE_GATE\\tOPEN\\n'\n")
        os.chmod(gate, 0o700)

        with mock.patch.dict(os.environ, {"KIMIFLOW_CONFORMANCE_GATE": gate}):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))
        self.assertFalse(os.path.exists(self.router_log))

    def test_finish_refuses_state_selector_mismatch(self):
        self.enable_conformance()
        state_path = os.path.join(self.run_dir, "STATE.md")
        with open(state_path, "r", encoding="utf-8") as handle:
            value = handle.read()
        with open(state_path, "w", encoding="utf-8") as handle:
            handle.write(value.replace("Scope: small", "Scope: large"))
        gate = os.path.join(self.temp, "conformance-gate-unused")
        with open(gate, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 99\n")
        os.chmod(gate, 0o700)

        with mock.patch.dict(os.environ, {"KIMIFLOW_CONFORMANCE_GATE": gate}):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))
        self.assertFalse(os.path.exists(self.router_log))

    def test_finish_refuses_removed_conformance_selector(self):
        self.enable_conformance()
        state_path = os.path.join(self.run_dir, "STATE.md")
        with open(state_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        with open(state_path, "w", encoding="utf-8") as handle:
            handle.writelines(
                line for line in lines
                if not line.startswith(("Conformance contract:", "Conformance basis:"))
            )
        gate = os.path.join(self.temp, "conformance-gate-unused")
        with open(gate, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 99\n")
        os.chmod(gate, 0o700)

        with mock.patch.dict(os.environ, {"KIMIFLOW_CONFORMANCE_GATE": gate}):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))
        self.assertFalse(os.path.exists(self.router_log))

    def test_finish_rechecks_conformance_after_learning_steps(self):
        self.enable_conformance("current")
        gate = os.path.join(self.temp, "conformance-gate-recheck")
        with open(gate, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "if grep -q 'mutated during learning' \"${KIMIFLOW_TEST_AFFECTED:?}\"; then\n"
                "  printf 'CONFORMANCE_GATE\\tCLOSED\\tblockers=1\\treason=stale\\tdetail=conformance_basis_stale\\n'\n"
                "else\n"
                "  printf 'CONFORMANCE_GATE\\tOPEN\\tblockers=0\\treason=clean\\tdetail=basis=%064d\\n' 0\n"
                "fi\n"
            )
        os.chmod(gate, 0o700)

        with mock.patch.dict(os.environ, {
            "KIMIFLOW_CONFORMANCE_GATE": gate,
            "KIMIFLOW_TEST_MUTATE_AFFECTED": "1",
            "KIMIFLOW_TEST_AFFECTED": os.path.join(self.repo, "tracked.txt"),
            "KIMIFLOW_TEST_EVALUATE_WRITE": "1",
        }):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])

        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))
        with open(os.path.join(self.repo, "tracked.txt"), "r", encoding="utf-8") as handle:
            self.assertIn("mutated during learning", handle.read())
        with open(self.router_log, "r", encoding="utf-8") as handle:
            self.assertIn("review-run", handle.read())
        self.assertFalse(os.path.exists(os.path.join(
            self.repo, ".kimiflow", "project", "STRATEGY-OUTCOMES.jsonl"
        )))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "OUTCOME-EVALUATION.json")))

    def test_failing_review_restores_partial_learning_writes(self):
        with mock.patch.dict(os.environ, {
            "KIMIFLOW_TEST_REVIEW_WRITE": "1",
            "KIMIFLOW_TEST_REVIEW_FAIL": "1",
        }):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])

        self.assertEqual(rc, 29)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))
        self.assertFalse(os.path.exists(os.path.join(
            self.repo, ".kimiflow", "project", "STRATEGIES.jsonl"
        )))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "LEARNING-REVIEW.md")))

    def test_terminal_serialization_failure_restores_learning_writes(self):
        with mock.patch.dict(os.environ, {"KIMIFLOW_TEST_EVALUATE_WRITE": "1"}), mock.patch(
            "kimiflow_core.active_run.write_outcome",
            side_effect=active_run.ActiveError("serialization failed", 31),
        ):
            rc, _ = run_main(["finish", "--root", self.repo, "--write"])

        self.assertEqual(rc, 31)
        self.assertTrue(os.path.isfile(active_run.active_file(self.repo)))
        self.assertFalse(os.path.exists(os.path.join(
            self.repo, ".kimiflow", "project", "STRATEGY-OUTCOMES.jsonl"
        )))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "OUTCOME-EVALUATION.json")))

    def test_parked_run_preserves_conformance_pin_on_resume(self):
        self.enable_conformance("current")
        rc, _ = run_main([
            "park", "--root", self.repo, "--reason", "resume fixture", "--write",
        ])
        self.assertEqual(rc, 0)
        state_path = os.path.join(self.run_dir, "STATE.md")
        with open(state_path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
        with open(state_path, "w", encoding="utf-8") as handle:
            handle.writelines(
                line for line in lines
                if not line.startswith(("Conformance contract:", "Conformance basis:"))
            )

        rc, _ = run_main([
            "start", "--run", self.run_rel, "--root", self.repo, "--write",
        ])

        self.assertEqual(rc, 0)
        with open(active_run.active_file(self.repo), "r", encoding="utf-8") as handle:
            resumed = json.load(handle)
        self.assertEqual(resumed.get("conformance_contract"), "1")

    def test_terminal_outcome_evaluation_is_best_effort(self):
        for command, expected in (("park", "parked"), ("fail", "failed"), ("abort", "aborted")):
            with self.subTest(command=command):
                if not os.path.isfile(active_run.active_file(self.repo)):
                    os.makedirs(self.run_dir, exist_ok=True)
                    self.write_state()
                    rc, _ = run_main(["start", "--run", self.run_rel, "--root", self.repo, "--write"])
                    self.assertEqual(rc, 0)
                with mock.patch.dict(os.environ, {"KIMIFLOW_TEST_EVALUATE_FAIL": "1"}):
                    rc, out = run_main([
                        command,
                        "--root", self.repo,
                        "--reason", "terminal fixture",
                        "--write",
                    ])
                self.assertEqual(rc, 0)
                result = json.loads(out)
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["outcome"]["outcome_evaluation"]["status"], "error")
                self.assertEqual(result["outcome"]["outcome_evaluation"]["exit_code"], 23)
                self.assertFalse(os.path.exists(active_run.active_file(self.repo)))


@unittest.skipUnless(shutil.which("jq"), "jq required by active-run commands")
class TestAwaitUser(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        plugin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, plugin)
        patcher = mock.patch.dict(os.environ, {
            "KIMIFLOW_PLUGIN_ROOT": plugin,
            "KIMIFLOW_HOST": "codex",
            "CODEX_THREAD_ID": "owner-session",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Status: active\nMode: feature\nScope: small\nAffected files: src/a.txt\n")
        rc, _ = run_main(["start", "--run", ".kimiflow/demo", "--root", self.root, "--write"])
        self.assertEqual(rc, 0)

    def write_state(self, extra=""):
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        with open(os.path.join(run_dir, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Status: active\nMode: feature\nScope: small\nAffected files: src/a.txt\n" + extra)

    def hook_payload(self):
        return json.dumps({"cwd": self.root, "session_id": "owner-session"})

    def read_active(self):
        with open(os.path.join(self.root, ".kimiflow", "session", "ACTIVE_RUN.json"), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def await_user(self, reason="engine gate question", kind=None):
        args = ["await-user", "--run", ".kimiflow/demo", "--reason", reason, "--root", self.root, "--write"]
        if kind is not None:
            args.extend(["--kind", kind])
        return run_main(args)

    def test_await_user_sets_flag_reason_and_timestamp(self):
        rc, out = self.await_user()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["status"], "awaiting_user")
        active = self.read_active()
        self.assertIs(active.get("awaiting_user"), True)
        self.assertEqual(active.get("awaiting_reason"), "engine gate question")
        self.assertTrue(active.get("awaiting_since"))

    def test_schema3_requires_known_kind(self):
        self.write_state("Flow schema: 3\n")
        rc, _ = self.await_user()
        self.assertEqual(rc, 2)
        rc, _ = self.await_user(kind="anything")
        self.assertEqual(rc, 2)
        self.assertNotIn("awaiting_user", self.read_active())

    def test_schema3_allows_deliberate_gate_outside_recovery(self):
        self.write_state("Flow schema: 3\nRecovery: clean\n")
        rc, out = self.await_user(kind="preview")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["awaiting_kind"], "preview")

    def test_schema4_allows_only_material_decision_kinds(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        for kind in ("missing-input", "authority", "external-access", "paid-privacy", "scope-risk", "irreversible", "workspace"):
            with self.subTest(kind=kind):
                rc, out = self.await_user(kind=kind)
                self.assertEqual(rc, 0)
                self.assertEqual(json.loads(out)["awaiting_kind"], kind)
        for kind in ("preview", "commit"):
            with self.subTest(kind=kind):
                rc, _ = self.await_user(kind=kind)
                self.assertEqual(rc, 2)

    def test_schema4_policy_cannot_be_downgraded_by_replacing_run_path(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        rc, _ = run_main(["start", "--run", ".kimiflow/demo", "--root", self.root, "--write"])
        self.assertEqual(rc, 0)
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        displaced = run_dir + ".schema4"
        os.rename(run_dir, displaced)
        os.mkdir(run_dir)
        with open(os.path.join(run_dir, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 3\nRecovery: clean\n")
        before = self.read_active()
        rc, _ = self.await_user(kind="commit")
        self.assertEqual(rc, 2)
        self.assertEqual(self.read_active(), before)

    def test_schema4_workspace_wait_is_one_shot(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 0)
        rc, _ = run_main(["prompt-context"], stdin_text=self.hook_payload())
        self.assertEqual(rc, 0)
        self.assertTrue(self.read_active().get("workspace_wait_used_at"))
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 2)

    def test_schema4_workspace_wait_receipt_survives_same_run_restart(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 0)
        rc, _ = run_main(["prompt-context"], stdin_text=self.hook_payload())
        self.assertEqual(rc, 0)
        rc, _ = run_main(["start", "--run", ".kimiflow/demo", "--root", self.root, "--write"])
        self.assertEqual(rc, 0)
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 2)

    def test_same_run_restart_does_not_invent_receipts_from_state(self):
        active_before = self.read_active()
        self.assertNotIn("workspace_wait_used_at", active_before)
        self.write_state(
            "Flow schema: 4\n"
            "Recovery: clean\n"
            "Workspace decision used at: stale-state-receipt\n"
            "Workspace disposition head: NOT VERIFIED\n"
            "Frontend quality start: clean@0000000000000000000000000000000000000000\n"
        )
        rc, _ = run_main(["start", "--run", ".kimiflow/demo", "--root", self.root, "--write"])
        self.assertEqual(rc, 0)
        restarted = self.read_active()
        self.assertNotIn("workspace_wait_used_at", restarted)
        self.assertNotIn("workspace_disposition_head", restarted)
        self.assertNotIn("frontend_quality_start_head", restarted)

    def test_schema4_workspace_wait_receipt_survives_park_and_resume(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 0)
        rc, _ = run_main(["prompt-context"], stdin_text=self.hook_payload())
        self.assertEqual(rc, 0)
        rc, _ = run_main(["park", "--root", self.root, "--reason", "pause", "--write"])
        self.assertEqual(rc, 0)
        rc, _ = run_main(["start", "--run", ".kimiflow/demo", "--root", self.root, "--write"])
        self.assertEqual(rc, 0)
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 2)
        self.assertTrue(active_run.state.state_value(os.path.join(self.root, ".kimiflow", "demo", "STATE.md"), "Workspace decision used at"))

    def test_schema4_workspace_receipt_refuses_symlinked_state(self):
        state_path = os.path.join(self.root, ".kimiflow", "demo", "STATE.md")
        outside = os.path.join(self.root, "outside-state.md")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 4\nRecovery: clean\n")
        os.unlink(state_path)
        os.symlink(outside, state_path)
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 2)
        with open(outside, "r", encoding="utf-8") as handle:
            self.assertNotIn("Workspace decision used at", handle.read())

    def test_schema4_workspace_receipt_refuses_symlinked_run_directory(self):
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        outside = os.path.join(self.root, "outside-run")
        os.mkdir(outside)
        outside_state = os.path.join(outside, "STATE.md")
        with open(outside_state, "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 4\nRecovery: clean\n")
        shutil.rmtree(run_dir)
        os.symlink(outside, run_dir)
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 2)
        with open(outside_state, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "Flow schema: 4\nRecovery: clean\n")

    def test_schema4_workspace_receipt_pins_run_directory_during_write(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        displaced = run_dir + ".displaced"
        outside = os.path.join(self.root, "outside-race")
        os.mkdir(outside)
        outside_state = os.path.join(outside, "STATE.md")
        outside_payload = "Flow schema: 4\nRecovery: clean\n"
        with open(outside_state, "w", encoding="utf-8") as handle:
            handle.write(outside_payload)
        original_stat = os.stat
        swapped = False

        def swap_after_pinned_stat(path, *args, **kwargs):
            nonlocal swapped
            result = original_stat(path, *args, **kwargs)
            if path == "demo" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                os.rename(run_dir, displaced)
                os.symlink(outside, run_dir)
            return result

        with mock.patch.object(active_run.os, "stat", side_effect=swap_after_pinned_stat):
            rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 2)
        with open(outside_state, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), outside_payload)
        self.assertFalse(self.read_active().get("workspace_wait_used_at"))

    def test_schema4_workspace_receipt_preserves_concurrent_state_append(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        state_path = os.path.join(self.root, ".kimiflow", "demo", "STATE.md")
        original_stat = os.stat
        appended = False

        def append_before_final_state_check(path, *args, **kwargs):
            nonlocal appended
            if path == "STATE.md" and kwargs.get("dir_fd") is not None and not appended:
                appended = True
                with open(state_path, "a", encoding="utf-8") as handle:
                    handle.write("Concurrent marker: preserved")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(active_run.os, "stat", side_effect=append_before_final_state_check):
            rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 0)
        with open(state_path, "r", encoding="utf-8") as handle:
            state_text = handle.read()
        self.assertIn("Concurrent marker: preserved", state_text)
        self.assertIn("Concurrent marker: preserved\nWorkspace decision used at", state_text)
        self.assertIn("Workspace decision used at", state_text)
        self.assertTrue(self.read_active().get("workspace_wait_used_at"))
        rc, _ = run_main(["park", "--root", self.root, "--reason", "pause", "--write"])
        self.assertEqual(rc, 0)
        rc, _ = run_main(["start", "--run", ".kimiflow/demo", "--root", self.root, "--write"])
        self.assertEqual(rc, 0)
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 2)

    def test_terminal_command_refuses_replaced_symlinked_run_directory(self):
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        displaced = run_dir + ".owned"
        outside = os.path.join(self.root, "outside-terminal")
        os.mkdir(outside)
        outside_state = os.path.join(outside, "STATE.md")
        outside_outcome = os.path.join(outside, "SESSION-OUTCOME.json")
        with open(outside_state, "w", encoding="utf-8") as handle:
            handle.write("Status: sentinel\n")
        with open(outside_outcome, "w", encoding="utf-8") as handle:
            handle.write('{"sentinel":true}\n')
        before_active = self.read_active()
        os.rename(run_dir, displaced)
        os.symlink(outside, run_dir)
        rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        with open(outside_state, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "Status: sentinel\n")
        with open(outside_outcome, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"sentinel":true}\n')
        self.assertEqual(self.read_active(), before_active)

    def test_terminal_write_race_never_mutates_exchanged_outside_run(self):
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        displaced = run_dir + ".owned-race"
        outside = os.path.join(self.root, "outside-terminal-race")
        os.mkdir(outside)
        outside_state = os.path.join(outside, "STATE.md")
        outside_outcome = os.path.join(outside, "SESSION-OUTCOME.json")
        with open(outside_state, "w", encoding="utf-8") as handle:
            handle.write("Status: sentinel\n")
        with open(outside_outcome, "w", encoding="utf-8") as handle:
            handle.write('{"sentinel":true}\n')
        before_active = self.read_active()
        original_write = active_run.write_run_text
        swapped = False

        def exchange_then_write(descriptor, name, text):
            nonlocal swapped
            if not swapped:
                swapped = True
                os.rename(run_dir, displaced)
                os.symlink(outside, run_dir)
            return original_write(descriptor, name, text)

        with mock.patch.object(active_run, "write_run_text", side_effect=exchange_then_write):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        with open(outside_state, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "Status: sentinel\n")
        with open(outside_outcome, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"sentinel":true}\n')
        self.assertEqual(self.read_active(), before_active)

    def test_terminal_state_temp_write_failure_preserves_original_bytes(self):
        state_path = os.path.join(self.root, ".kimiflow", "demo", "STATE.md")
        with open(state_path, "rb") as handle:
            before_state = handle.read()
        before_active = self.read_active()
        original_write = workspace_preflight.atomic_directory_write

        def fail_state_temp(descriptor, name, payload):
            if name.startswith(".terminal-state-"):
                raise OSError("simulated partial temporary write")
            return original_write(descriptor, name, payload)

        with mock.patch.object(workspace_preflight, "atomic_directory_write", side_effect=fail_state_temp):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        with open(state_path, "rb") as handle:
            self.assertEqual(handle.read(), before_state)
        self.assertEqual(self.read_active(), before_active)

    def test_terminal_state_commit_fsync_failure_restores_original(self):
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        state_path = os.path.join(run_dir, "STATE.md")
        with open(state_path, "rb") as handle:
            before_state = handle.read()
        before_active = self.read_active()
        original_fsync = active_run.os.fsync
        failed = False

        def fail_state_commit_fsync(descriptor):
            nonlocal failed
            names = os.listdir(run_dir)
            if not failed and "STATE.md" in names and any(
                name.startswith(".terminal-state-") and name.endswith(".backup") for name in names
            ):
                failed = True
                raise OSError("simulated terminal STATE commit fsync failure")
            return original_fsync(descriptor)

        with mock.patch.object(active_run.os, "fsync", side_effect=fail_state_commit_fsync):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        self.assertTrue(failed)
        with open(state_path, "rb") as handle:
            self.assertEqual(handle.read(), before_state)
        self.assertEqual(self.read_active(), before_active)

    def test_terminal_state_late_cleanup_fsync_failure_keeps_success(self):
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        original_fsync = active_run.os.fsync
        original_unlink = active_run.os.unlink
        backup_removed = False
        failed = False

        def observe_backup_unlink(path, *args, **kwargs):
            nonlocal backup_removed
            result = original_unlink(path, *args, **kwargs)
            if str(path).startswith(".terminal-state-") and str(path).endswith(".backup"):
                backup_removed = True
            return result

        def fail_late_cleanup_fsync(descriptor):
            nonlocal failed
            info = os.fstat(descriptor)
            run_info = os.lstat(run_dir)
            if backup_removed and not failed and (info.st_dev, info.st_ino) == (run_info.st_dev, run_info.st_ino):
                failed = True
                raise OSError("simulated late terminal STATE cleanup fsync failure")
            return original_fsync(descriptor)

        with mock.patch.object(active_run.os, "unlink", side_effect=observe_backup_unlink), mock.patch.object(
            active_run.os, "fsync", side_effect=fail_late_cleanup_fsync
        ):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 0)
        self.assertTrue(failed)
        self.assertFalse(os.path.exists(active_run.active_file(self.root)))
        self.assertEqual(
            active_run.state.state_value(os.path.join(run_dir, "STATE.md"), "Status"),
            "aborted",
        )

    def test_terminal_state_exchange_is_preserved_and_refused(self):
        state_path = os.path.join(self.root, ".kimiflow", "demo", "STATE.md")
        replacement = state_path + ".replacement"
        foreign_payload = b"Status: active\nForeign marker: preserved\n"
        before_active = self.read_active()
        original_write = active_run.write_run_text
        exchanged = False

        def exchange_state_then_write(descriptor, name, text):
            nonlocal exchanged
            if not exchanged:
                exchanged = True
                with open(replacement, "wb") as handle:
                    handle.write(foreign_payload)
                os.replace(replacement, state_path)
            return original_write(descriptor, name, text)

        with mock.patch.object(active_run, "write_run_text", side_effect=exchange_state_then_write):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        with open(state_path, "rb") as handle:
            self.assertEqual(handle.read(), foreign_payload)
        self.assertEqual(self.read_active(), before_active)

    def test_terminal_refuses_state_exchange_after_initial_close_check(self):
        state_path = os.path.join(self.root, ".kimiflow", "demo", "STATE.md")
        replacement = state_path + ".late-replacement"
        foreign_payload = b"Status: active\nForeign marker: late\n"
        before_active = self.read_active()
        original_match = active_run.terminal_run_name_matches
        calls = 0

        def exchange_after_first_check(pinned):
            nonlocal calls
            result = original_match(pinned)
            calls += 1
            if calls == 1:
                with open(replacement, "wb") as handle:
                    handle.write(foreign_payload)
                os.replace(replacement, state_path)
            return result

        with mock.patch.object(active_run, "terminal_run_name_matches", side_effect=exchange_after_first_check):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        with open(state_path, "rb") as handle:
            self.assertEqual(handle.read(), foreign_payload)
        self.assertEqual(self.read_active(), before_active)

    def test_terminal_refuses_in_place_status_rewrite_before_retirement(self):
        state_path = os.path.join(self.root, ".kimiflow", "demo", "STATE.md")
        before_active = self.read_active()
        original_match = active_run.terminal_run_name_matches
        calls = 0

        def rewrite_before_final_check(pinned):
            nonlocal calls
            calls += 1
            if calls == 2:
                with open(state_path, "r+", encoding="utf-8") as handle:
                    current = handle.read().replace("Status: aborted", "Status: active")
                    handle.seek(0)
                    handle.write(current)
                    handle.truncate()
            return original_match(pinned)

        with mock.patch.object(active_run, "terminal_run_name_matches", side_effect=rewrite_before_final_check):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.read_active(), before_active)
        with open(state_path, "r", encoding="utf-8") as handle:
            self.assertIn("Status: active", handle.read())

    def test_terminal_refuses_in_place_status_rewrite_after_final_read(self):
        state_path = os.path.join(self.root, ".kimiflow", "demo", "STATE.md")
        before_active = self.read_active()
        original_read = active_run.os.read
        rewritten = False

        def rewrite_after_terminal_read(descriptor, size):
            nonlocal rewritten
            payload = original_read(descriptor, size)
            if not rewritten and b"Status: aborted" in payload:
                rewritten = True
                with open(state_path, "r+", encoding="utf-8") as handle:
                    current = handle.read().replace("Status: aborted", "Status: active")
                    handle.seek(0)
                    handle.write(current)
                    handle.truncate()
            return payload

        with mock.patch.object(active_run.os, "read", side_effect=rewrite_after_terminal_read):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertTrue(rewritten)
        self.assertEqual(rc, 2)
        self.assertEqual(self.read_active(), before_active)

    def test_terminal_reports_active_session_unlink_failure(self):
        active_path = active_run.active_file(self.root)
        before_active = self.read_active()
        original_unlink = active_run.os.unlink

        def refuse_active_unlink(path, *args, **kwargs):
            if path == active_path or (
                str(path).startswith(".kimiflow-retired-ACTIVE_RUN.json-")
                and kwargs.get("dir_fd") is not None
            ):
                raise PermissionError("simulated ACTIVE_RUN unlink failure")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(active_run.os, "unlink", side_effect=refuse_active_unlink):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.read_active(), before_active)

    def test_terminal_preserves_active_session_replaced_immediately_before_retirement(self):
        active_path = active_run.active_file(self.root)
        replacement = {"schema_version": 1, "status": "active", "run": ".kimiflow/replacement"}
        original_rename = active_run.os.rename
        exchanged = False

        def replace_before_retirement(source, destination, *args, **kwargs):
            nonlocal exchanged
            if source == "ACTIVE_RUN.json" and not exchanged:
                exchanged = True
                temporary = active_path + ".replacement"
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(replacement, handle)
                    handle.write("\n")
                os.replace(temporary, active_path)
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(active_run.os, "rename", side_effect=replace_before_retirement):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        self.assertTrue(exchanged)
        with open(active_path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), replacement)

    def test_terminal_restores_active_session_after_post_rename_stat_failure(self):
        before_active = self.read_active()
        original_stat = active_run.os.stat
        failed = False

        def fail_retired_stat(path, *args, **kwargs):
            nonlocal failed
            if (
                not failed
                and str(path).startswith(".kimiflow-retired-ACTIVE_RUN.json-")
                and kwargs.get("dir_fd") is not None
            ):
                failed = True
                raise OSError("simulated retired ACTIVE_RUN stat failure")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(active_run.os, "stat", side_effect=fail_retired_stat):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        self.assertTrue(failed)
        self.assertEqual(self.read_active(), before_active)
        session = os.path.join(self.root, ".kimiflow", "session")
        self.assertFalse(any(name.startswith(".kimiflow-retired-ACTIVE_RUN") for name in os.listdir(session)))

    def test_terminal_restores_active_session_after_final_parent_stat_failure(self):
        before_active = self.read_active()
        original_stat = active_run.os.stat
        session_stats = 0

        def fail_final_session_stat(path, *args, **kwargs):
            nonlocal session_stats
            if path == "session" and kwargs.get("dir_fd") is not None:
                session_stats += 1
                if session_stats == 2:
                    raise OSError("simulated final session stat failure")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(active_run.os, "stat", side_effect=fail_final_session_stat):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        self.assertEqual(session_stats, 2)
        self.assertEqual(self.read_active(), before_active)
        session = os.path.join(self.root, ".kimiflow", "session")
        self.assertFalse(any(name.startswith(".kimiflow-retired-ACTIVE_RUN") for name in os.listdir(session)))

    def test_terminal_fsyncs_retired_active_name_before_tombstone_cleanup(self):
        session = os.path.join(self.root, ".kimiflow", "session")
        session_info = os.lstat(session)
        original_fsync = active_run.os.fsync
        durable_retirement_seen = False

        def observe_retirement_fsync(descriptor):
            nonlocal durable_retirement_seen
            info = os.fstat(descriptor)
            names = os.listdir(session)
            if (info.st_dev, info.st_ino) == (session_info.st_dev, session_info.st_ino) and any(
                name.startswith(".kimiflow-retired-ACTIVE_RUN") for name in names
            ) and "ACTIVE_RUN.json" not in names:
                durable_retirement_seen = True
            return original_fsync(descriptor)

        with mock.patch.object(active_run.os, "fsync", side_effect=observe_retirement_fsync):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 0)
        self.assertTrue(durable_retirement_seen)

    def test_terminal_restores_active_after_retirement_fsync_failure(self):
        session = os.path.join(self.root, ".kimiflow", "session")
        session_info = os.lstat(session)
        before_active = self.read_active()
        original_fsync = active_run.os.fsync
        failed = False

        def fail_retirement_fsync(descriptor):
            nonlocal failed
            info = os.fstat(descriptor)
            names = os.listdir(session)
            if not failed and (info.st_dev, info.st_ino) == (session_info.st_dev, session_info.st_ino) and any(
                name.startswith(".kimiflow-retired-ACTIVE_RUN") for name in names
            ) and "ACTIVE_RUN.json" not in names:
                failed = True
                raise OSError("simulated active retirement fsync failure")
            return original_fsync(descriptor)

        with mock.patch.object(active_run.os, "fsync", side_effect=fail_retirement_fsync):
            rc, _ = run_main(["abort", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        self.assertTrue(failed)
        self.assertEqual(self.read_active(), before_active)
        self.assertFalse(any(name.startswith(".kimiflow-retired-ACTIVE_RUN") for name in os.listdir(session)))

    def test_terminal_never_follows_exchanged_active_session_parent(self):
        session = os.path.join(self.root, ".kimiflow", "session")
        displaced = session + ".owned"
        outside = os.path.join(self.root, "outside-active-session")
        os.mkdir(outside)
        active_name = "ACTIVE_RUN.json"
        with open(os.path.join(session, active_name), "rb") as handle:
            active_payload = handle.read()
        with open(os.path.join(outside, active_name), "wb") as handle:
            handle.write(active_payload)
        os.rename(session, displaced)
        os.symlink(outside, session)

        rc, _ = run_main(["park", "--root", self.root, "--reason", "test", "--write"])
        self.assertEqual(rc, 2)
        with open(os.path.join(outside, active_name), "rb") as handle:
            self.assertEqual(handle.read(), active_payload)
        with open(os.path.join(displaced, active_name), "rb") as handle:
            self.assertEqual(handle.read(), active_payload)

    def test_workspace_receipt_remains_successful_after_durable_path_exchange(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        rc, _ = run_main(["start", "--run", ".kimiflow/demo", "--root", self.root, "--write"])
        self.assertEqual(rc, 0)
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        displaced = run_dir + ".durable"
        outside = os.path.join(self.root, "outside-durable")
        os.mkdir(outside)
        outside_state = os.path.join(outside, "STATE.md")
        with open(outside_state, "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 4\nRecovery: clean\n")
        original_fsync = os.fsync
        swapped = False

        def exchange_after_fsync(descriptor):
            nonlocal swapped
            result = original_fsync(descriptor)
            if not swapped:
                swapped = True
                os.rename(run_dir, displaced)
                os.symlink(outside, run_dir)
            return result

        with mock.patch.object(active_run.os, "fsync", side_effect=exchange_after_fsync):
            rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 0)
        self.assertTrue(self.read_active().get("workspace_wait_used_at"))
        with open(outside_state, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "Flow schema: 4\nRecovery: clean\n")
        self.assertTrue(
            active_run.state.state_value(os.path.join(displaced, "STATE.md"), "Workspace decision used at")
        )

    def test_schema4_workspace_active_write_failure_does_not_consume_receipt(self):
        self.write_state("Flow schema: 4\nRecovery: clean\n")
        with mock.patch.object(active_run, "write_active", side_effect=OSError("simulated active write failure")):
            rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 2)
        self.assertFalse(active_run.state.state_value(os.path.join(self.root, ".kimiflow", "demo", "STATE.md"), "Workspace decision used at"))
        rc, _ = self.await_user(kind="workspace")
        self.assertEqual(rc, 0)

    def test_recovery_rejects_preview_and_keeps_stop_gate_blocking(self):
        self.write_state("Flow schema: 3\nRecovery: active\n")
        rc, _ = self.await_user(kind="preview")
        self.assertEqual(rc, 2)
        rc, out = run_main(["stop-gate"], stdin_text=self.hook_payload())
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_clean_recovery_transition_reenables_deliberate_gate(self):
        self.write_state("Flow schema: 3\nRecovery: active\n")
        rc, _ = self.await_user(kind="commit")
        self.assertEqual(rc, 2)
        self.write_state("Flow schema: 3\nRecovery: clean\n")
        rc, out = self.await_user(kind="commit")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["awaiting_kind"], "commit")

    def test_recovery_allows_only_missing_authority_kinds(self):
        self.write_state("Flow schema: 3\nRecovery: active\n")
        allowed = ("missing-input", "authority", "external-access", "paid-privacy", "scope-risk", "irreversible", "workspace")
        for kind in allowed:
            with self.subTest(kind=kind):
                rc, out = self.await_user(kind=kind)
                self.assertEqual(rc, 0)
                self.assertEqual(json.loads(out)["awaiting_kind"], kind)
                rc, _ = run_main(["prompt-context"], stdin_text=self.hook_payload())
                self.assertEqual(rc, 0)

    def test_status_reports_awaiting_user(self):
        rc, out = run_main(["status", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIs(json.loads(out)["awaiting_user"], False)
        self.await_user()
        rc, out = run_main(["status", "--root", self.root])
        self.assertEqual(rc, 0)
        self.assertIs(json.loads(out)["awaiting_user"], True)

    def test_stop_gate_blocks_without_flag(self):
        rc, out = run_main(["stop-gate"], stdin_text=self.hook_payload())
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_stop_gate_passes_while_awaiting_user(self):
        self.await_user()
        rc, out = run_main(["stop-gate"], stdin_text=self.hook_payload())
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_other_session_stop_is_never_blocked(self):
        payload = json.dumps({"cwd": self.root, "session_id": "other-session"})
        rc, out = run_main(["stop-gate"], stdin_text=payload)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_other_session_prompt_is_advisory(self):
        payload = json.dumps({"cwd": self.root, "session_id": "other-session"})
        rc, out = run_main(["prompt-context"], stdin_text=payload)
        self.assertEqual(rc, 0)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("not part of that run", context)
        self.assertIn("conflict-check", context)
        self.assertIn("explicit user authority", context)
        self.assertIn("workspace-preflight registration", context)
        self.assertNotIn("use a separate git worktree", context.lower())

    def test_conflict_check_distinguishes_paths(self):
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "other-session"}):
            rc, out = run_main(["conflict-check", "--root", self.root, "--path", "src/b.txt"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["decision"], "allow_disjoint")
            rc, out = run_main(["conflict-check", "--root", self.root, "--path", "src/a.txt"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["decision"], "block_overlap")

    def test_path_overlap_includes_ancestors_and_descendants(self):
        self.assertTrue(active_run.paths_overlap("src", "src/a.txt"))
        self.assertTrue(active_run.paths_overlap("src/a.txt", "src/a.txt/generated"))
        self.assertFalse(active_run.paths_overlap("src/a.txt", "tests/a.txt"))

    def test_prompt_context_clears_flag(self):
        self.await_user()
        rc, out = run_main(["prompt-context"], stdin_text=self.hook_payload())
        self.assertEqual(rc, 0)
        self.assertIn("Kimiflow active session is open", out)
        active = self.read_active()
        self.assertNotIn("awaiting_user", active)
        self.assertNotIn("awaiting_reason", active)
        self.assertNotIn("awaiting_since", active)
        rc, out = run_main(["stop-gate"], stdin_text=self.hook_payload())
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["decision"], "block")


@unittest.skipUnless(shutil.which("jq"), "jq required by active-run commands")
class TestTerminalWorktreeRetirement(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.repo = os.path.join(self.temp, "repo")
        os.mkdir(self.repo)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        with open(os.path.join(self.repo, "tracked.txt"), "w", encoding="utf-8") as handle:
            handle.write("base\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "base")
        plugin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, plugin, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {
            "KIMIFLOW_PLUGIN_ROOT": plugin,
            "KIMIFLOW_HOST": "codex",
            "CODEX_THREAD_ID": "owner-session",
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.run_rel = ".kimiflow/demo"
        run_dir = os.path.join(self.repo, self.run_rel)
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 4\nStatus: active\nMode: feature\nScope: small\nAffected files: tracked.txt\n")
        rc, _ = run_main(["start", "--run", self.run_rel, "--root", self.repo, "--write"])
        self.assertEqual(rc, 0)
        self.linked = os.path.join(self.temp, "exceptional")
        self.git("worktree", "add", "-b", "exceptional", self.linked)
        workspace_preflight.register(self.repo, self.linked, self.run_rel, write=True)

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", self.repo] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

    def terminal(self, command):
        return run_main([command, "--root", self.repo, "--reason", "test terminal", "--write"])

    def test_abort_archives_clean_exceptional_tree_immediately(self):
        rc, out = self.terminal("abort")
        self.assertEqual(rc, 0)
        result = json.loads(out)
        retirement = result["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "archived")
        self.assertFalse(os.path.exists(self.linked))
        self.assertTrue(os.path.isdir(retirement["archive_path"]))
        self.assertTrue(os.path.isdir(retirement["metadata_archive_path"]))
        self.assertEqual(workspace_preflight.read_registry(self.repo)["entries"], [])

    def test_terminal_retry_preserves_retirement_evidence_after_outcome_failure(self):
        original_write = active_run.write_run_text
        failed = False

        def fail_final_outcome(descriptor, name, text):
            nonlocal failed
            if name == "SESSION-OUTCOME.json" and '"workspace_retirement"' in text and not failed:
                failed = True
                raise OSError("simulated final outcome failure")
            return original_write(descriptor, name, text)

        with mock.patch.object(active_run, "write_run_text", side_effect=fail_final_outcome):
            rc, _ = self.terminal("abort")
        self.assertEqual(rc, 2)
        self.assertTrue(failed)
        self.assertFalse(os.path.exists(self.linked))
        rc, out = self.terminal("abort")
        self.assertEqual(rc, 0)
        retirement = json.loads(out)["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "archived")
        self.assertTrue(os.path.isdir(retirement["archive_path"]))
        self.assertTrue(os.path.isdir(retirement["metadata_archive_path"]))

    def test_terminal_retry_promotes_planned_receipt_after_final_receipt_failure(self):
        original_write = active_run.write_run_text
        failed = False

        def fail_final_receipt(descriptor, name, text):
            nonlocal failed
            if name == "WORKSPACE-RETIREMENT.json" and '"status": "archived"' in text and not failed:
                failed = True
                raise OSError("simulated final retirement receipt failure")
            return original_write(descriptor, name, text)

        with mock.patch.object(active_run, "write_run_text", side_effect=fail_final_receipt):
            rc, _ = self.terminal("abort")
        self.assertEqual(rc, 2)
        self.assertTrue(failed)
        self.assertFalse(os.path.exists(self.linked))

        rc, out = self.terminal("abort")
        self.assertEqual(rc, 0)
        retirement = json.loads(out)["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "archived")
        self.assertTrue(os.path.isdir(retirement["archive_path"]))
        self.assertTrue(os.path.isdir(retirement["metadata_archive_path"]))

    def test_terminal_promotes_archive_evidence_when_registry_clear_fails(self):
        original_write = workspace_preflight.write_registry
        failed = False

        def fail_registry_clear(primary, registry, directory_descriptor=None):
            nonlocal failed
            if not failed and registry.get("entries") == []:
                failed = True
                raise workspace_preflight.WorkspaceError("simulated registry clear failure")
            return original_write(primary, registry, directory_descriptor)

        with mock.patch.object(workspace_preflight, "write_registry", side_effect=fail_registry_clear):
            rc, out = self.terminal("abort")
        self.assertEqual(rc, 0)
        self.assertTrue(failed)
        retirement = json.loads(out)["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "archived")
        self.assertTrue(retirement["registry_reconcile_required"])
        self.assertTrue(os.path.isdir(retirement["archive_path"]))
        self.assertTrue(os.path.isdir(retirement["metadata_archive_path"]))
        self.assertFalse(os.path.exists(active_run.active_file(self.repo)))
        self.assertEqual(len(workspace_preflight.read_registry(self.repo)["entries"]), 1)

    def test_terminal_does_not_promote_preexisting_archive_directories(self):
        entry = workspace_preflight.read_registry(self.repo)["entries"][0]
        _, archive_path = workspace_preflight.retirement_paths(self.linked, entry["identity"])
        common_dir = workspace_preflight.git_path(self.linked, ["rev-parse", "--git-common-dir"])
        _, metadata_path = workspace_preflight.metadata_retirement_paths(common_dir, entry["identity"])
        os.makedirs(archive_path)
        os.makedirs(metadata_path)

        rc, out = self.terminal("abort")
        self.assertEqual(rc, 0)
        retirement = json.loads(out)["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "deferred")
        self.assertTrue(os.path.isdir(self.linked))
        self.assertEqual(len(workspace_preflight.read_registry(self.repo)["entries"]), 1)

    def test_terminal_does_not_promote_planned_receipt_while_target_exists(self):
        entry = workspace_preflight.read_registry(self.repo)["entries"][0]
        _, archive_path = workspace_preflight.retirement_paths(self.linked, entry["identity"])
        common_dir = workspace_preflight.git_path(self.linked, ["rev-parse", "--git-common-dir"])
        _, metadata_path = workspace_preflight.metadata_retirement_paths(common_dir, entry["identity"])
        os.makedirs(archive_path)
        os.makedirs(metadata_path)
        receipt = {
            "status": "planned",
            "written": False,
            "path": self.linked,
            "archive_path": archive_path,
            "metadata_archive_path": metadata_path,
        }
        with open(os.path.join(self.repo, self.run_rel, "WORKSPACE-RETIREMENT.json"), "w", encoding="utf-8") as handle:
            json.dump(receipt, handle)

        with mock.patch.object(workspace_preflight, "build_status", return_value={"worktrees": []}):
            rc, out = self.terminal("abort")
        self.assertEqual(rc, 0)
        retirement = json.loads(out)["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "deferred")
        self.assertTrue(os.path.isdir(self.linked))

    def test_park_resume_restores_workspace_and_frontend_baseline_receipts(self):
        head = self.git("rev-parse", "HEAD").stdout.strip()
        run_dir = os.path.join(self.repo, self.run_rel)
        active_run.update_state_value(run_dir, "Workspace decision used at", "2026-07-16T12:00:00Z")
        active_run.update_state_value(run_dir, "Workspace disposition head", head)
        active_run.update_state_value(run_dir, "Frontend quality start", "clean@%s" % head)
        current = active_run.load_active(self.repo)
        current.pop("present", None)
        current["workspace_wait_used_at"] = "2026-07-16T12:00:00Z"
        current["workspace_disposition_head"] = head
        current["frontend_quality_start_head"] = head
        active_run.write_active(self.repo, current)

        rc, _ = self.terminal("park")
        self.assertEqual(rc, 0)
        rc, _ = run_main(["start", "--run", self.run_rel, "--root", self.repo, "--write"])
        self.assertEqual(rc, 0)
        resumed = active_run.load_active(self.repo)
        self.assertEqual(resumed["workspace_disposition_head"], head)
        self.assertEqual(resumed["frontend_quality_start_head"], head)

    def test_fail_archives_clean_exceptional_tree_immediately(self):
        rc, out = self.terminal("fail")
        self.assertEqual(rc, 0)
        retirement = json.loads(out)["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "archived")
        self.assertFalse(os.path.exists(self.linked))
        self.assertTrue(os.path.isdir(retirement["archive_path"]))
        self.assertTrue(os.path.isdir(retirement["metadata_archive_path"]))

    def test_abort_defers_dirty_tree_without_losing_work(self):
        dirty = os.path.join(self.linked, "valuable.txt")
        with open(dirty, "w", encoding="utf-8") as handle:
            handle.write("keep me\n")
        rc, out = self.terminal("abort")
        self.assertEqual(rc, 0)
        retirement = json.loads(out)["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "deferred")
        self.assertIn("dirty", retirement["blockers"])
        with open(dirty, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep me\n")
        self.assertEqual(len(workspace_preflight.read_registry(self.repo)["entries"]), 1)

    def test_finish_archives_clean_exceptional_tree_immediately(self):
        router = os.path.join(self.temp, "memory-router")
        with open(router, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "if [ \"$1\" = review-run ]; then printf '%s\\n' '{\"status\":\"skipped\"}'; exit 0; fi\n"
                "if [ \"$1\" = verify-run ]; then printf '%s\\n' OPEN; exit 0; fi\n"
                "if [ \"$1\" = evaluate-run ]; then printf '%s\\n' '{\"status\":\"evaluated\",\"written\":true,\"evaluation\":{\"id\":\"out_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"terminal\":\"done\",\"classification\":\"verified_success\",\"promotable\":true}}'; exit 0; fi\n"
                "exit 2\n"
            )
        os.chmod(router, 0o700)
        with mock.patch.dict(os.environ, {"KIMIFLOW_MEMORY_ROUTER": router}):
            rc, out = run_main([
                "finish",
                "--root",
                self.repo,
                "--skip-learning",
                "test fixture",
                "--write",
            ])
        self.assertEqual(rc, 0)
        retirement = json.loads(out)["outcome"]["workspace_retirement"]
        self.assertEqual(retirement["status"], "archived")
        self.assertFalse(os.path.exists(self.linked))
        self.assertTrue(os.path.isdir(retirement["archive_path"]))
        self.assertTrue(os.path.isdir(retirement["metadata_archive_path"]))

    def test_finish_refuses_router_time_run_exchange_without_outside_writes(self):
        router = os.path.join(self.temp, "memory-router-race")
        with open(router, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "if [ \"$1\" = review-run ]; then printf '%s\\n' '{\"status\":\"skipped\"}'; exit 0; fi\n"
                "if [ \"$1\" = verify-run ]; then printf '%s\\n' OPEN; exit 0; fi\n"
                "if [ \"$1\" = evaluate-run ]; then printf '%s\\n' '{\"status\":\"evaluated\",\"written\":true,\"evaluation\":{\"id\":\"out_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"terminal\":\"done\",\"classification\":\"verified_success\",\"promotable\":true}}'; exit 0; fi\n"
                "exit 2\n"
            )
        os.chmod(router, 0o700)
        run_dir = os.path.join(self.repo, self.run_rel)
        displaced = run_dir + ".owned"
        outside = os.path.join(self.temp, "finish-outside")
        os.mkdir(outside)
        outside_state = os.path.join(outside, "STATE.md")
        outside_outcome = os.path.join(outside, "SESSION-OUTCOME.json")
        with open(outside_state, "w", encoding="utf-8") as handle:
            handle.write("Status: sentinel\n")
        with open(outside_outcome, "w", encoding="utf-8") as handle:
            handle.write('{"sentinel":true}\n')
        original_run_cmd = active_run.run_cmd
        swapped = False

        def exchange_after_router(command, *args, **kwargs):
            nonlocal swapped
            result = original_run_cmd(command, *args, **kwargs)
            if command and command[0] == router and command[1] == "verify-run" and not swapped:
                swapped = True
                os.rename(run_dir, displaced)
                os.symlink(outside, run_dir)
            return result

        with mock.patch.dict(os.environ, {"KIMIFLOW_MEMORY_ROUTER": router}), mock.patch.object(
            active_run,
            "run_cmd",
            side_effect=exchange_after_router,
        ):
            rc, _ = run_main([
                "finish",
                "--root",
                self.repo,
                "--skip-learning",
                "test fixture",
                "--write",
            ])
        self.assertEqual(rc, 2)
        with open(outside_state, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "Status: sentinel\n")
        with open(outside_outcome, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"sentinel":true}\n')
        self.assertTrue(os.path.isdir(self.linked))
        self.assertEqual(len(workspace_preflight.read_registry(self.repo)["entries"]), 1)

    def test_finish_restore_stays_bound_to_pinned_run_descriptor(self):
        run_dir = os.path.join(self.repo, self.run_rel)
        displaced = run_dir + ".owned"
        outside = os.path.join(self.temp, "restore-outside")
        snapshot = os.path.join(self.temp, "finish-snapshot")
        os.mkdir(outside)
        outside_outcome = os.path.join(outside, "SESSION-OUTCOME.json")
        with open(outside_outcome, "w", encoding="utf-8") as handle:
            handle.write('{"sentinel":true}\n')
        learning = os.path.join(run_dir, "LEARNING-REVIEW.md")
        with open(learning, "w", encoding="utf-8") as handle:
            handle.write("Status: existing\n")
        os.chmod(learning, 0o644)

        active = active_run.load_active(self.repo)
        with active_run.pinned_terminal_run(run_dir, active) as pinned:
            active_run.snapshot_finish(
                self.repo,
                run_dir,
                snapshot,
                run_descriptor=pinned["run_descriptor"],
            )
            os.rename(run_dir, displaced)
            os.symlink(outside, run_dir)
            active_run.restore_finish(
                self.repo,
                run_dir,
                snapshot,
                run_descriptor=pinned["run_descriptor"],
            )

        with open(outside_outcome, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"sentinel":true}\n')
        self.assertFalse(os.path.exists(os.path.join(displaced, "SESSION-OUTCOME.json")))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(displaced, "LEARNING-REVIEW.md")).st_mode), 0o644)

    def test_finish_snapshot_rejects_oversized_run_artifact_before_mutation(self):
        run_dir = os.path.join(self.repo, self.run_rel)
        learning = os.path.join(run_dir, "LEARNING-REVIEW.md")
        with open(learning, "wb") as handle:
            handle.truncate(active_run.FINISH_ARTIFACT_LIMIT + 1)
        snapshot = os.path.join(self.temp, "oversized-finish-snapshot")
        active = active_run.load_active(self.repo)

        with active_run.pinned_terminal_run(run_dir, active) as pinned, self.assertRaises(active_run.ActiveError):
            active_run.snapshot_finish(
                self.repo,
                run_dir,
                snapshot,
                run_descriptor=pinned["run_descriptor"],
            )

        self.assertEqual(os.path.getsize(learning), active_run.FINISH_ARTIFACT_LIMIT + 1)

    def test_finish_snapshot_rejects_artifact_that_grows_after_fstat(self):
        run_dir = os.path.join(self.repo, self.run_rel)
        learning = os.path.join(run_dir, "LEARNING-REVIEW.md")
        with open(learning, "wb") as handle:
            handle.write(b"x")
        active = active_run.load_active(self.repo)
        original_fstat = active_run.os.fstat
        grew = False

        def grow_after_regular_file_stat(descriptor):
            nonlocal grew
            info = original_fstat(descriptor)
            if stat.S_ISREG(info.st_mode) and not grew:
                with open(learning, "ab") as handle:
                    handle.truncate(active_run.FINISH_ARTIFACT_LIMIT + 1)
                grew = True
            return info

        with active_run.pinned_terminal_run(run_dir, active) as pinned, mock.patch.object(
            active_run.os,
            "fstat",
            side_effect=grow_after_regular_file_stat,
        ), self.assertRaises(active_run.ActiveError):
            active_run.read_run_snapshot(pinned["run_descriptor"], "LEARNING-REVIEW.md")

        self.assertTrue(grew)

    def test_finish_router_stays_bound_to_pinned_run_directory(self):
        router = os.path.abspath(os.path.join(os.path.dirname(active_run.__file__), "..", "memory-router.sh"))
        run_dir = os.path.join(self.repo, self.run_rel)
        displaced = run_dir + ".owned-router"
        outside = os.path.join(self.temp, "router-outside")
        os.mkdir(outside)
        original_run_cmd = active_run.run_cmd
        swapped = False

        def exchange_before_router(command, *args, **kwargs):
            nonlocal swapped
            if command and command[0] == router and command[1] == "review-run" and not swapped:
                swapped = True
                os.rename(run_dir, displaced)
                os.symlink(outside, run_dir)
            return original_run_cmd(command, *args, **kwargs)

        with mock.patch.dict(os.environ, {"KIMIFLOW_MEMORY_ROUTER": router}), mock.patch.object(
            active_run,
            "run_cmd",
            side_effect=exchange_before_router,
        ):
            rc, _ = run_main([
                "finish",
                "--root",
                self.repo,
                "--skip-learning",
                "test fixture",
                "--write",
            ])

        self.assertEqual(rc, 2)
        for name in ("LEARNING-REVIEW.md", "RUN-LIFECYCLE.json", "RUN-LIFECYCLE.md"):
            self.assertFalse(os.path.lexists(os.path.join(outside, name)))

    def test_park_keeps_registered_tree_and_writes_backlog_state(self):
        rc, out = self.terminal("park")
        self.assertEqual(rc, 0)
        self.assertNotIn("workspace_retirement", json.loads(out)["outcome"])
        self.assertTrue(os.path.isdir(self.linked))
        state_path = os.path.join(self.repo, self.run_rel, "STATE.md")
        self.assertEqual(active_run.state.state_value(state_path, "Status"), "backlog")
        self.assertEqual(len(workspace_preflight.read_registry(self.repo)["entries"]), 1)

    def test_park_resume_preserves_original_started_head(self):
        active_before = active_run.load_active(self.repo)
        original_head = active_before["started_head"]
        with open(os.path.join(self.repo, "tracked.txt"), "a", encoding="utf-8") as handle:
            handle.write("checkpoint\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "verify: checkpoint")
        checkpoint_head = self.git("rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(checkpoint_head, original_head)
        rc, _ = self.terminal("park")
        self.assertEqual(rc, 0)
        rc, out = run_main(["start", "--run", self.run_rel, "--root", self.repo, "--write"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["started_head"], original_head)
        self.assertEqual(active_run.load_active(self.repo)["started_head"], original_head)

    def test_same_run_restart_ignores_mutated_state_started_head(self):
        original_head = active_run.load_active(self.repo)["started_head"]
        with open(os.path.join(self.repo, "tracked.txt"), "a", encoding="utf-8") as handle:
            handle.write("checkpoint\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "verify: checkpoint")
        checkpoint_head = self.git("rev-parse", "HEAD").stdout.strip()
        run_dir = os.path.join(self.repo, self.run_rel)
        state_path = os.path.join(run_dir, "STATE.md")
        with open(state_path, "r", encoding="utf-8") as handle:
            source = handle.read()
        source = re.sub(r"(?m)^Run started head: .+$", "Run started head: %s" % checkpoint_head, source)
        with open(state_path, "w", encoding="utf-8") as handle:
            handle.write(source)

        rc, out = run_main(["start", "--run", self.run_rel, "--root", self.repo, "--write"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["started_head"], original_head)
        self.assertEqual(active_run.load_active(self.repo)["started_head"], original_head)


if __name__ == "__main__":
    unittest.main()
