import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import check_change as checks


class CheckChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kimiflow check ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        (self.root / ".gitignore").write_text("build/\n__pycache__/\n")
        (self.root / "source.txt").write_text("before\n")
        self.git("add", ".gitignore", "source.txt")
        self.git("commit", "-qm", "initial")

    def git(self, *argv):
        return subprocess.check_output(["git", "-C", str(self.root), *argv], stderr=subprocess.DEVNULL)

    def py(self, code):
        return [sys.executable, "-c", code]

    def test_success_is_compact_and_does_not_write_state_or_touch_index(self):
        (self.root / "foreign.txt").write_text("foreign")
        self.git("add", "foreign.txt")
        before = self.git("diff", "--cached", "--binary")
        result, code = checks.check_change(self.root, [self.py("print('x' * 10000)")])
        self.assertEqual((code, result["status"]), (0, "passed"))
        self.assertNotIn("output_tail", result["checks"][0])
        self.assertFalse((self.root / ".kimiflow").exists())
        self.assertEqual(before, self.git("diff", "--cached", "--binary"))

    def test_fail_fast_and_bounded_diagnostics(self):
        result, code = checks.check_change(self.root, [
            self.py("print('x' * 10000); raise SystemExit(3)"),
            self.py("open('should-not-run', 'w').write('bad')"),
        ])
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertEqual(len(result["checks"]), 1)
        self.assertEqual(result["checks"][0]["exit_code"], 3)
        self.assertLessEqual(len(result["checks"][0]["output_tail"]), 4096)
        self.assertTrue(result["checks"][0]["output_truncated"])
        self.assertFalse((self.root / "should-not-run").exists())

    def test_rejects_mutating_check_even_if_command_passes(self):
        result, code = checks.check_change(self.root, [self.py("open('source.txt','w').write('after')")])
        self.assertEqual((code, result["status"]), (1, "changed"))
        self.assertNotIn("source_sha256", result)

    def test_index_only_mutation_invalidates_result(self):
        (self.root / "source.txt").write_text("after")
        result, code = checks.check_change(self.root, [["git", "add", "source.txt"]])
        self.assertEqual((code, result["status"]), (1, "changed"))

    def test_untracked_source_and_mode_changes_are_bound(self):
        base = checks.snapshot(self.root)
        path = self.root / "new\nfile.txt"
        path.write_text("new")
        added = checks.snapshot(self.root)
        self.assertNotEqual(base, added)
        path.chmod(0o755)
        self.assertNotEqual(added, checks.snapshot(self.root))

    def test_assume_unchanged_does_not_hide_source_mutation(self):
        self.git("update-index", "--assume-unchanged", "source.txt")
        result, code = checks.check_change(self.root, [self.py("open('source.txt','w').write('after')")])
        self.assertEqual((code, result["status"]), (1, "changed"))

    def test_ignored_build_output_and_local_notes_are_not_source(self):
        result, code = checks.check_change(self.root, [self.py(
            "from pathlib import Path; Path('build').mkdir(); Path('build/out').write_text('out'); "
            "Path('.kimiflow').mkdir(); Path('.kimiflow/NOTES.md').write_text('note')"
        )])
        self.assertEqual((code, result["status"]), (0, "passed"))

    def test_tracked_kimiflow_files_are_always_source(self):
        (self.root / ".kimiflow").mkdir()
        (self.root / ".kimiflow" / "tracked").write_text("old")
        self.git("add", ".kimiflow/tracked")
        result, code = checks.check_change(self.root, [self.py("open('.kimiflow/tracked','w').write('new')")])
        self.assertEqual((code, result["status"]), (1, "changed"))

    def test_argv_does_not_invoke_shell(self):
        result, code = checks.check_change(self.root, [
            self.py("import sys; assert sys.argv[1] == '$(touch injected); echo bad'")
            + ["$(touch injected); echo bad"]
        ])
        self.assertEqual((code, result["status"]), (0, "passed"))
        self.assertFalse((self.root / "injected").exists())

    def test_timeout_is_failure(self):
        result, code = checks.check_change(self.root, [self.py("import time; time.sleep(10)")], timeout=.05)
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertTrue(result["checks"][0]["timed_out"])

    def test_no_checks_cannot_pass(self):
        with self.assertRaises(checks.CheckError):
            checks.check_change(self.root, [])

    def test_malformed_command_is_rejected(self):
        for value in ('null', '{}', '[]', '[1]', '[""]', '["x",null]'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                checks.parse_check(value)

    def test_missing_executable_is_error(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = checks.main(["--root", str(self.root), "--check", '["kimiflow-no-such-check"]'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")

    def test_installed_helper_cannot_be_shadowed_by_project_package(self):
        package = self.root / "scripts"
        package.mkdir()
        (package / "__init__.py").write_text("raise RuntimeError('wrong package')")
        helper = Path(__file__).resolve().parents[1] / "scripts" / "check_change.py"
        result = subprocess.run(
            [sys.executable, str(helper), "--root", str(self.root), "--check", json.dumps(self.py("pass"))],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "passed")

    def test_unborn_git_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            result, code = checks.check_change(directory, [self.py("pass")])
        self.assertEqual((code, result["status"]), (0, "passed"))

    def test_symlink_is_bound_without_following_target(self):
        path = self.root / "link"
        path.symlink_to("source.txt")
        before = checks.snapshot(self.root)
        path.unlink()
        path.symlink_to("elsewhere")
        self.assertNotEqual(before, checks.snapshot(self.root))

    def test_submodule_dirty_source_is_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            nested = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            (nested / "nested.txt").write_text("before")
            subprocess.run(["git", "-C", directory, "add", "nested.txt"], check=True)
            subprocess.run(["git", "-C", directory, "-c", "user.name=Test", "-c",
                            "user.email=test@example.invalid", "commit", "-qm", "initial"], check=True)
            self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", directory, "nested")
            before = checks.snapshot(self.root)
            (self.root / "nested" / "nested.txt").write_text("after")
            self.assertNotEqual(before, checks.snapshot(self.root))

    @unittest.skipUnless(os.name == "posix", "process-group cleanup requires POSIX")
    def test_successful_parent_with_background_child_cannot_pass(self):
        child = "import time; time.sleep(.4); open('source.txt','w').write('after')"
        command = self.py("import subprocess,sys; subprocess.Popen([sys.executable,'-c',%r])" % child)
        result, code = checks.check_change(self.root, [command, self.py("pass")])
        self.assertEqual((code, result["status"]), (1, "failed"))
        self.assertTrue(result["checks"][0]["background_processes"])
        self.assertEqual(len(result["checks"]), 1)
        subprocess.run(self.py("import time; time.sleep(.5)"), check=True)
        self.assertEqual((self.root / "source.txt").read_text(), "before\n")

    @unittest.skipUnless(os.name == "posix", "process-group cleanup requires POSIX")
    def test_timeout_stops_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = str(Path(directory) / "survived")
            child = "import time; time.sleep(.3); open(%r, 'w').write('bad')" % sentinel
            command = self.py("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(10)" % child)
            result = checks.execute(self.root, command, .1)
            self.assertTrue(result["timed_out"])
            # A bounded external wait lets a leaked descendant expose itself.
            subprocess.run(self.py("import time; time.sleep(.4)"), check=True)
            self.assertFalse(Path(sentinel).exists())


if __name__ == "__main__":
    unittest.main()
