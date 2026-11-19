import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'templates' / 'ops' / 'tools'


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


class WaitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self.tmp.name) / 'runs dir'
        self.runs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self, name, cap):
        env = {**os.environ, 'RUN_DIR': str(self.runs), 'WAIT_POLL': '0.1'}
        return subprocess.run(['sh', str(TOOLS / 'wait.sh'), name, cap], env=env,
                              capture_output=True, text=True, timeout=30)

    def test_finished_run_prints_reds_and_exit_and_returns_its_code(self):
        (self.runs / 'a.log').write_text('BUILD\nreal-red: T1\nload-red: T2\nEXIT 1 after 9s\n')
        result = self.wait('a', '5')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, 'real-red: T1\nload-red: T2\nEXIT 1 after 9s\n')

    def test_green_run_returns_zero(self):
        (self.runs / 'g.log').write_text('BUILD\nEXIT 0 after 3s\n')
        result = self.wait('g', '5')
        self.assertEqual((result.returncode, result.stdout), (0, 'EXIT 0 after 3s\n'))

    def test_live_run_reports_at_the_cap_instead_of_silence(self):
        (self.runs / 'live.log').write_text('BUILD\n')
        result = self.wait('live', '1')
        self.assertEqual(result.returncode, 124)
        self.assertIn('run live, log ' + str(self.runs / 'live.log') + ', no EXIT yet', result.stdout)


OPERATIONS = """# Operations
- MERGE ADDITIVE: `<path or glob>` check `<command>`
- MERGE ADDITIVE: `project/*.list` check `grep -q base project/files.list`
- MERGE REGENERATE: `stamp.txt` run `printf 'regenerated\\n' > stamp.txt`
"""


class MergeAdditiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.operations = Path(self.tmp.name) / 'OPERATIONS.md'
        self.operations.write_text(OPERATIONS)
        git(self.repo, 'init', '-q', '-b', 'main')
        (self.repo / 'project').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        (self.repo / name).write_text(text)

    def conflict(self, base, ours, theirs):
        """Commit base, change it on a side branch (theirs) and on main (ours), then merge."""
        for name, text in base.items():
            self.write(name, text)
        git(self.repo, 'add', '--', *base)
        git(self.repo, 'commit', '-qm', 'base')
        git(self.repo, 'switch', '-qc', 'side')
        for name, text in theirs.items():
            self.write(name, text)
        git(self.repo, 'commit', '-qam', 'theirs')
        git(self.repo, 'switch', '-q', 'main')
        for name, text in ours.items():
            self.write(name, text)
        git(self.repo, 'commit', '-qam', 'ours')
        result = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', 'merge', '-q', 'side'],
                                cwd=self.repo, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, 'the merge must stop on a conflict')
        # PREMISE: it stopped on a conflict, not on a missing identity (CI has no global git user).
        self.assertTrue(self.unmerged(), 'the merge left conflicted files: ' + result.stdout + result.stderr)

    def tool(self):
        return subprocess.run([sys.executable, str(TOOLS / 'merge-additive.py'), '--operations',
                               str(self.operations)], cwd=self.repo, capture_output=True, text=True, timeout=60)

    def unmerged(self):
        return git(self.repo, 'diff', '--name-only', '--diff-filter=U').split()

    def test_two_additive_hunks_keep_both_sides_and_stage(self):
        name = 'project/files.list'
        self.conflict({name: 'base\nmid\nend\n'},
                      {name: 'base\nours-1\nmid\nours-2\nend\n'},
                      {name: 'base\ntheirs-1\nmid\ntheirs-2\nend\n'})
        result = self.tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.repo / name).read_text(),
                         'base\nours-1\ntheirs-1\nmid\nours-2\ntheirs-2\nend\n')
        self.assertEqual(self.unmerged(), [])
        self.assertIn('RESOLVED ' + name, result.stdout)

    def test_hunk_that_changes_a_line_is_refused_and_file_untouched(self):
        name = 'project/files.list'
        self.conflict({name: 'base\nmid\nend\n'},
                      {name: 'base\nMID changed\nend\n'},
                      {name: 'base\nmid\ntheirs-1\nend\n'})
        before = (self.repo / name).read_text()
        result = self.tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn('REFUSED ' + name, result.stdout)
        self.assertIn('mid', result.stdout.split('REFUSED', 1)[1])
        self.assertEqual((self.repo / name).read_text(), before)
        self.assertEqual(self.unmerged(), [name])

    def test_failed_check_restores_the_file(self):
        name = 'project/other.list'
        self.conflict({name: 'one\n'}, {name: 'one\nours\n'}, {name: 'one\ntheirs\n'})
        before = (self.repo / name).read_text()
        result = self.tool()  # its check greps project/files.list, which does not exist here
        self.assertEqual(result.returncode, 1)
        self.assertIn('check command failed', result.stdout)
        self.assertEqual((self.repo / name).read_text(), before)
        self.assertEqual(self.unmerged(), [name])

    def test_file_not_named_stays_untouched(self):
        self.conflict({'notes.txt': 'a\n'}, {'notes.txt': 'a\nours\n'}, {'notes.txt': 'a\ntheirs\n'})
        before = (self.repo / 'notes.txt').read_text()
        result = self.tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn('LEFT notes.txt', result.stdout)
        self.assertEqual((self.repo / 'notes.txt').read_text(), before)
        self.assertEqual(self.unmerged(), ['notes.txt'])

    def test_regenerated_file_runs_the_command_and_stages(self):
        self.conflict({'stamp.txt': 'v0\n'}, {'stamp.txt': 'v1\n'}, {'stamp.txt': 'v2\n'})
        result = self.tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.repo / 'stamp.txt').read_text(), 'regenerated\n')
        self.assertEqual(self.unmerged(), [])


if __name__ == '__main__':
    unittest.main()
