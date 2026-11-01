import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'templates' / 'ops' / 'tools' / 'run.sh'
# A build that waits while ./hold exists (capped), then both phases list the lock directories.
PROJECT_LINES = {
    "BUILD_CMD='<build command>'": "BUILD_CMD='i=0; while [ -e hold ] && [ $i -lt 100 ]; do sleep 0.1; "
                                   "i=$((i + 1)); done; echo BUILD; ls -A \"$RUN_DIR\"'",
    "TEST_CMD='<test command> \"$@\"'": "TEST_CMD='echo TEST; ls -A \"$RUN_DIR\"'",
    "BUILD_DIR='<build output directory>'": "BUILD_DIR='build'",
}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        text = TEMPLATE.read_text()
        for old, new in PROJECT_LINES.items():
            self.assertIn(old, text)
            text = text.replace(old, new)
        self.script = base / 'run.sh'
        self.script.write_text(text)
        self.runs = base / 'runs'
        self.wt1, self.wt2 = base / 'wt1', base / 'wt2'
        self.wt1.mkdir()
        self.wt2.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_sh(self, cwd, name, mode, *ids, background=False, **env):
        env = {**os.environ, 'RUN_DIR': str(self.runs), 'TESTS_OK': '1', **env}
        args = ['sh', str(self.script), name, mode, *ids]
        if background:
            return subprocess.Popen(args, cwd=cwd, env=env)
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)

    def log(self, name):
        return (self.runs / (name + '.log')).read_text()

    def test_test_phase_holds_no_build_lane(self):
        result = self.run_sh(self.wt1, 'a', 'test')
        self.assertEqual(result.returncode, 0, self.log('a'))
        build, test = self.log('a').split('TEST\n')
        self.assertIn('.lane-1', build)
        self.assertNotIn('.lane-', test)
        self.assertIn('.testlock', test)
        self.assertIn('.build-', test)
        self.assertTrue(test.rstrip().splitlines()[-1].startswith('EXIT 0 after'))

    def test_one_run_per_build_directory(self):
        (self.wt1 / 'hold').touch()
        first = self.run_sh(self.wt1, 'first', 'build', background=True)
        try:
            state = self.runs / 'state' / 'first.json'
            deadline = time.time() + 10
            while time.time() < deadline and '"phase":"build"' not in (
                    state.read_text() if state.exists() else ''):
                time.sleep(0.1)
            same = self.run_sh(self.wt1, 'same', 'build', WAIT_MAX='0')
            self.assertEqual(same.returncode, 75)
            self.assertIn('timeout waiting for builddir', self.log('same'))
            other = self.run_sh(self.wt2, 'other', 'build', WAIT_MAX='0')
            self.assertEqual(other.returncode, 0, self.log('other'))
        finally:
            (self.wt1 / 'hold').unlink()
            first.wait(timeout=30)
        self.assertEqual(first.returncode, 0, self.log('first'))

    def test_small_run_takes_the_small_test_lock_beside_a_big_run(self):
        busy = self.runs / '.testlock'
        busy.mkdir(parents=True)
        (busy / 'pid').write_text(str(os.getpid()))
        small = self.run_sh(self.wt1, 'small', 'test', 'OneTest', SMALL_MAX='20', WAIT_MAX='0')
        self.assertEqual(small.returncode, 0, self.log('small'))
        self.assertIn('.testlock-small', self.log('small').split('TEST\n')[1])
        off = self.run_sh(self.wt1, 'off', 'test', 'OneTest', WAIT_MAX='0')
        self.assertEqual(off.returncode, 75)
        self.assertIn('timeout waiting for testlock', self.log('off'))


if __name__ == '__main__':
    unittest.main()
