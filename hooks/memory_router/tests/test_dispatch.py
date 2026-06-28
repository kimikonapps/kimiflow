import io, unittest, contextlib
from memory_router.__main__ import main, USAGE

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

    def test_unknown_command_prints_usage_exit_2(self):
        code, err = self._run(["bogus"])
        self.assertEqual(code, 2)
        self.assertEqual(err, USAGE)

    def test_usage_is_17_lines_and_starts_with_shebang(self):
        lines = USAGE.split("\n")
        # 17 content lines + trailing "" from the final newline
        self.assertEqual(len(lines), 18)
        self.assertEqual(lines[0], "#!/usr/bin/env bash")

if __name__ == "__main__":
    unittest.main()
