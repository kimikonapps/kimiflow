import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kimiflow_core import active_run


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

    def hook_payload(self):
        return json.dumps({"cwd": self.root, "session_id": "owner-session"})

    def read_active(self):
        with open(os.path.join(self.root, ".kimiflow", "session", "ACTIVE_RUN.json"), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def await_user(self, reason="engine gate question"):
        return run_main(["await-user", "--run", ".kimiflow/demo", "--reason", reason, "--root", self.root, "--write"])

    def test_await_user_sets_flag_reason_and_timestamp(self):
        rc, out = self.await_user()
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["status"], "awaiting_user")
        active = self.read_active()
        self.assertIs(active.get("awaiting_user"), True)
        self.assertEqual(active.get("awaiting_reason"), "engine gate question")
        self.assertTrue(active.get("awaiting_since"))

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


if __name__ == "__main__":
    unittest.main()
