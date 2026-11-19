import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
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

    def wait(self, name, cap, *options):
        env = {**os.environ, 'RUN_DIR': str(self.runs), 'WAIT_POLL': '0.1'}
        options = options or ('--after', str(int(time.time()) - 5))  # the epoch before the run started
        return subprocess.run(['sh', str(TOOLS / 'wait.sh'), *options, name, cap], env=env,
                              capture_output=True, text=True, timeout=30)

    def log(self, name, body, start=None):
        """A run log as run.sh writes it: START <epoch> <pid> first (default: now)."""
        start = int(time.time()) if start is None else start
        (self.runs / (name + '.log')).write_text('START %d 4242\n' % start + body)

    def test_finished_run_prints_reds_and_exit_and_returns_its_code(self):
        self.log('a', 'BUILD\nreal-red: T1\nload-red: T2\nEXIT 1 after 9s\n')
        result = self.wait('a', '5')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, 'real-red: T1\nload-red: T2\nEXIT 1 after 9s\n')

    def test_green_run_returns_zero(self):
        self.log('g', 'BUILD\nEXIT 0 after 3s\n')
        result = self.wait('g', '5')
        self.assertEqual((result.returncode, result.stdout), (0, 'EXIT 0 after 3s\n'))

    def test_previous_runs_finished_log_is_not_accepted(self):
        self.log('old', 'BUILD\nEXIT 0 after 3s\n', start=int(time.time()) - 600)
        result = self.wait('old', '1')
        self.assertEqual(result.returncode, 124)
        self.assertIn('run live, log ' + str(self.runs / 'old.log') + ', no EXIT yet', result.stdout)

    def test_log_without_start_line_is_not_accepted(self):
        (self.runs / 'bare.log').write_text('BUILD\nEXIT 0 after 3s\n')
        self.assertEqual(self.wait('bare', '1').returncode, 124)

    def test_log_started_after_the_given_time_is_accepted(self):
        self.log('late', 'BUILD\nEXIT 3 after 3s\n', start=2000)
        result = self.wait('late', '5', '--after', '1000')
        self.assertEqual((result.returncode, result.stdout), (3, 'EXIT 3 after 3s\n'))

    def test_log_started_before_the_given_time_is_not_accepted(self):
        self.log('early', 'BUILD\nEXIT 0 after 3s\n', start=999)
        self.assertEqual(self.wait('early', '1', '--after', '1000').returncode, 124)

    def test_wait_without_after_is_refused(self):
        self.log('n', 'BUILD\nEXIT 0 after 3s\n')
        result = subprocess.run(['sh', str(TOOLS / 'wait.sh'), 'n', '1'], capture_output=True, text=True, timeout=30,
                                env={**os.environ, 'RUN_DIR': str(self.runs)})
        self.assertEqual(result.returncode, 64)
        self.assertIn('--after', result.stderr)

    def test_a_run_that_ended_before_the_wait_is_still_read(self):
        before = int(time.time()) - 60
        self.log('done', 'BUILD\nEXIT 2 after 9s\n', start=before + 10)
        result = self.wait('done', '5', '--after', str(before))
        self.assertEqual((result.returncode, result.stdout), (2, 'EXIT 2 after 9s\n'))

    def test_live_run_reports_at_the_cap_instead_of_silence(self):
        self.log('live', 'BUILD\n')
        result = self.wait('live', '1')
        self.assertEqual(result.returncode, 124)
        self.assertIn('run live, log ' + str(self.runs / 'live.log') + ', no EXIT yet', result.stdout)


OPERATIONS = """# Operations
- MERGE ADDITIVE: `<path or glob>` check `<command>`
- MERGE ADDITIVE: `project/*.list` check `grep -q base project/files.list`
- MERGE ADDITIVE: `gen/**/*.list` check `true`
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
        (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            (self.repo / name).write_bytes(text)
        else:
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

    def test_binary_conflict_is_refused_and_file_untouched(self):
        name = 'project/blob.list'
        # files.list makes the check pass, so only the binary refusal keeps the file unstaged.
        self.conflict({name: b'base\0\n', 'project/files.list': 'base\n'},
                      {name: b'base\0\nours\n'}, {name: b'base\0\ntheirs\n'})
        before = (self.repo / name).read_bytes()
        result = self.tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn('REFUSED ' + name + ': binary content', result.stdout)
        self.assertEqual((self.repo / name).read_bytes(), before)
        self.assertEqual(self.unmerged(), [name])

    def test_conflict_the_text_merge_does_not_see_is_refused(self):
        name = 'project/attr.list'
        # merge=binary makes git stop although the two additions sit on separate lines.
        self.conflict({name: 'a\nb\nc\nd\n', 'project/files.list': 'base\n',
                       '.gitattributes': name + ' merge=binary\n'},
                      {name: 'a\nours\nb\nc\nd\n'}, {name: 'a\nb\nc\ntheirs\nd\n'})
        before = (self.repo / name).read_text()
        result = self.tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn('REFUSED ' + name + ': git reported a conflict the text merge does not see', result.stdout)
        self.assertEqual((self.repo / name).read_text(), before)
        self.assertEqual(self.unmerged(), [name])

    def test_glob_star_stays_in_its_directory(self):
        self.conflict({'gen/x/a.list': 'one\n'}, {'gen/x/a.list': 'one\nours\n'},
                      {'gen/x/a.list': 'one\ntheirs\n'})
        result = self.tool()  # `project/*.list` must not reach it; `gen/**/*.list` does
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('RESOLVED gen/x/a.list', result.stdout)

    def test_regenerated_file_runs_the_command_and_stages(self):
        self.conflict({'stamp.txt': 'v0\n'}, {'stamp.txt': 'v1\n'}, {'stamp.txt': 'v2\n'})
        result = self.tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.repo / 'stamp.txt').read_text(), 'regenerated\n')
        self.assertEqual(self.unmerged(), [])


class GlobTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('merge_additive', TOOLS / 'merge-additive.py')
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def test_star_never_crosses_a_slash(self):
        self.assertTrue(self.tool.glob_match('gen/a.list', 'gen/*.list'))
        self.assertFalse(self.tool.glob_match('gen/x/a.list', 'gen/*.list'))

    def test_double_star_matches_any_number_of_directories(self):
        for path in ('gen/a.list', 'gen/x/a.list', 'gen/x/y/a.list'):
            self.assertTrue(self.tool.glob_match(path, 'gen/**/*.list'), path)
        self.assertFalse(self.tool.glob_match('other/a.list', 'gen/**/*.list'))


if __name__ == '__main__':
    unittest.main()
