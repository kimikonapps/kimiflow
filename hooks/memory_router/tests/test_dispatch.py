import io, os, shutil, tempfile, unittest, contextlib
from unittest import mock
from memory_router.__main__ import COMMANDS, main, USAGE
from memory_router import cli

class TestDispatch(unittest.TestCase):
    def _run(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(argv)
        return code, err.getvalue()

    def test_no_args_prints_usage_to_stderr_exit_2(self):
        code, err = self._run([])
        self.assertEqual(code, 2)
        self.assertEqual(err, USAGE)

    def test_help_prints_usage_exit_0(self):
        for flag in ("--help", "-h", "help"):
            code, err = self._run([flag])
            self.assertEqual(code, 0)
            self.assertEqual(err, USAGE)

    def test_unknown_command_dies_with_message_exit_2(self):
        code, err = self._run(["bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "memory-router: unknown command: bogus\n")

    def test_usage_is_17_lines_and_starts_with_shebang(self):
        lines = USAGE.split("\n")
        # 17 content lines + trailing "" from the final newline
        self.assertEqual(len(lines), 18)
        self.assertEqual(lines[0], "#!/usr/bin/env bash")

    def test_evaluate_run_is_registered_without_expanding_legacy_usage(self):
        self.assertIn("evaluate-run", COMMANDS)
        self.assertNotIn("evaluate-run", USAGE)

class ResolveRootCase(unittest.TestCase):
    def test_existing_dir_absolutized(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        self.assertEqual(cli.resolve_root(d), os.path.abspath(d))

    def test_missing_root_returns_literal(self):
        self.assertEqual(cli.resolve_root("/no/such/dir/xyz"), "/no/such/dir/xyz")

    def test_no_root_uses_git_toplevel(self):
        proc = mock.Mock(returncode=0, stdout=b"/repo/top\n")
        with mock.patch("memory_router.cli.subprocess.run", return_value=proc):
            self.assertEqual(cli.resolve_root(""), "/repo/top")

    def test_no_root_falls_back_to_logical_cwd_when_git_fails(self):
        proc = mock.Mock(returncode=128, stdout=b"")
        with mock.patch("memory_router.cli.subprocess.run", return_value=proc):
            self.assertEqual(cli.resolve_root(""), cli._logical_cwd())

    def test_no_root_falls_back_to_logical_cwd_when_git_absent(self):
        with mock.patch("memory_router.cli.subprocess.run", side_effect=OSError("no git")):
            self.assertEqual(cli.resolve_root(""), cli._logical_cwd())


class LogicalCwdCase(unittest.TestCase):
    def test_uses_pwd_when_it_names_the_cwd_via_symlink(self):
        # Bash bare `pwd` (-L) keeps the symlinked path; _logical_cwd must too.
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        real = os.path.join(base, "real")
        link = os.path.join(base, "link")
        os.mkdir(real)
        os.symlink(real, link)
        cwd = os.getcwd()
        try:
            os.chdir(link)
            with mock.patch.dict(os.environ, {"PWD": link}):
                self.assertEqual(cli._logical_cwd(), link)           # symlink preserved
                self.assertNotEqual(cli._logical_cwd(), os.getcwd())  # not the physical path
        finally:
            os.chdir(cwd)

    def test_ignores_stale_pwd(self):
        with mock.patch.dict(os.environ, {"PWD": "/definitely/not/the/cwd"}):
            self.assertEqual(cli._logical_cwd(), os.getcwd())

    def test_falls_back_to_physical_when_pwd_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "PWD"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cli._logical_cwd(), os.getcwd())


if __name__ == "__main__":
    unittest.main()
