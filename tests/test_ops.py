import hashlib
import json
import os
import shutil
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.build_plugin import build, RUNTIME_FILES, MARKETPLACES
from scripts.kimiflow_ops import main_checkout
from scripts.render_skills import RENDER_TARGETS

ROOT = Path(__file__).resolve().parents[1]
GIT_ENV = {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
           'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.invalid',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.invalid'}


def git(cwd, *args):
    subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)


def snapshot(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob('*')) if path.is_file()}


class OpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = tempfile.TemporaryDirectory()
        root = Path(cls.package.name)
        for relative in set(RUNTIME_FILES) | set(MARKETPLACES) | {source for source, _ in RENDER_TARGETS}:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / relative).read_bytes())
        build(root)
        cls.helper = root / 'plugins' / 'kimiflow' / 'scripts' / 'kimiflow_ops.py'

    @classmethod
    def tearDownClass(cls):
        cls.package.cleanup()

    def setUp(self):
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name).resolve()
        self.main = self.tmp / 'main'
        git(self.tmp, 'init', '-q', 'main')
        git(self.main, 'commit', '-q', '--allow-empty', '-m', 'init')

    def run_ops(self, *args, stdin='', cwd=None, helper=None):
        return subprocess.run([sys.executable, str(helper or self.helper), *args], input=stdin,
                              cwd=cwd or self.tmp, capture_output=True, text=True, timeout=60)

    def init(self, root=None):
        result = self.run_ops('init', '--root', str(root or self.main))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def upstream(self, version=None, edits=('plans/plan.md', 'LIST.md')):
        """A copy of the built package with edited templates, as a later plugin version would ship."""
        package = self.tmp / 'upstream'
        shutil.copytree(self.helper.parents[1], package)
        for relative in edits:
            with (package / 'templates' / 'ops' / relative).open('a') as handle:
                handle.write('upstream line\n')
        if version:
            manifest = package / '.claude-plugin' / 'plugin.json'
            data = json.loads(manifest.read_text()); data['version'] = version
            manifest.write_text(json.dumps(data))
        return package / 'scripts' / 'kimiflow_ops.py'

    def update(self, helper, *extra):
        result = self.run_ops('update', '--root', str(self.main), *extra, helper=helper)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def kit(self):
        return json.loads((self.main / '.kimiflow' / 'ops' / '.kit.json').read_text())

    def worktree(self):
        git(self.main, 'worktree', 'add', '-q', str(self.tmp / 'wt'))
        return self.tmp / 'wt'

    def session(self, cwd, stdin=None):
        payload = json.dumps({'cwd': str(cwd), 'hook_event_name': 'SessionStart'}) if stdin is None else stdin
        return self.run_ops('session-start', stdin=payload, cwd=cwd)

    # main_checkout

    def test_main_checkout_for_root_subfolder_and_worktree(self):
        (self.main / 'sub' / 'deeper').mkdir(parents=True)
        self.assertEqual(main_checkout(self.main), self.main)
        self.assertEqual(main_checkout(self.main / 'sub' / 'deeper'), self.main)
        self.assertEqual(main_checkout(self.worktree()), self.main)

    def test_main_checkout_none_for_non_git_submodule_and_bare(self):
        plain = self.tmp / 'plain'
        plain.mkdir()
        self.assertIsNone(main_checkout(plain))
        git(self.tmp, 'init', '-q', '--bare', 'bare.git')
        self.assertIsNone(main_checkout(self.tmp / 'bare.git'))
        git(self.main, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q',
            str(self.main), 'sub')
        self.assertIsNone(main_checkout(self.main / 'sub'))
        git(self.tmp, 'init', '-q', '--separate-git-dir', str(self.tmp / 'separate.git'), 'separate')
        self.assertIsNone(main_checkout(self.tmp / 'separate'))

    # init

    def test_init_creates_kit_and_second_run_changes_nothing(self):
        report = self.init()
        ops = self.main / '.kimiflow' / 'ops'
        for name in ('OPERATIONS.md', 'OPERATIONS-history.md', 'rules-worker.md', 'HANDOFF.md', 'LIST.md', 'LIST-done.md', 'plans/feature-sheet.md',
                     'plans/plan.md', 'plans/worker-brief.md', 'plans/review-brief.md',
                     'archive/.keep', 'research/.keep'):
            self.assertTrue((ops / name).is_file(), name)
        self.assertIn('.kimiflow/ops/OPERATIONS.md', report['created'])
        self.assertEqual(report['kept'], [])
        exclude = Path(report['exclude'])
        before = snapshot(ops), exclude.read_bytes()
        again = self.init()
        self.assertEqual(again['created'], [])
        self.assertEqual(sorted(again['kept']), sorted(report['created']))
        self.assertEqual((snapshot(ops), exclude.read_bytes()), before)

    def test_init_records_kit_version_and_template_hashes_once(self):
        self.init()
        templates = self.helper.parents[1] / 'templates' / 'ops'
        version = json.loads((self.helper.parents[1] / '.claude-plugin' / 'plugin.json').read_text())['version']
        expected = {path.relative_to(templates).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in templates.rglob('*') if path.is_file()}
        kit = self.main / '.kimiflow' / 'ops' / '.kit.json'
        self.assertEqual(json.loads(kit.read_text()), {'kit_version': version, 'templates': expected})
        kit.write_text('{"kit_version": "owner", "templates": {}}')
        self.init()
        self.assertEqual(kit.read_text(), '{"kit_version": "owner", "templates": {}}')

    # update

    def test_update_without_kit_file_reports_unknown_base(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        (ops / '.kit.json').unlink()
        (ops / 'LIST.md').write_text('owner list\n')
        report = self.update(self.helper)
        self.assertEqual([entry['path'] for entry in report['unknown_base']], ['LIST.md'])
        self.assertIn('base', report['unknown_base'][0]['reason'])
        self.assertIn('OPERATIONS.md', report['unchanged'])

    def test_update_reports_safe_and_conflict_without_writing(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        (ops / 'LIST.md').write_text('owner list\n')
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in ops.rglob('*') if path.is_file()}
        report = self.update(self.upstream())
        self.assertEqual(report['safe'], ['plans/plan.md'])
        self.assertEqual([entry['path'] for entry in report['conflict']], ['LIST.md'])
        self.assertIn('-owner list', report['conflict'][0]['diff'])
        self.assertIn('+upstream line', report['conflict'][0]['diff'])
        self.assertEqual((report['new'], report['unknown_base']), ([], []))
        after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in ops.rglob('*') if path.is_file()}
        self.assertEqual(after, before)

    def test_update_apply_safe_replaces_only_safe_files(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        base, old_version = self.kit()['templates'], self.kit()['kit_version']
        (ops / 'LIST.md').write_text('owner list\n')
        (ops / 'HANDOFF.md').unlink()
        helper = self.upstream(version='9.9.9')
        report = self.update(helper, '--apply-safe')
        shipped = helper.parents[1] / 'templates' / 'ops'
        self.assertEqual(report['new'], ['HANDOFF.md'])
        self.assertEqual((ops / 'plans/plan.md').read_bytes(), (shipped / 'plans/plan.md').read_bytes())
        self.assertEqual((ops / 'HANDOFF.md').read_bytes(), (shipped / 'HANDOFF.md').read_bytes())
        self.assertEqual((ops / 'LIST.md').read_text(), 'owner list\n')
        kit = self.kit()
        self.assertEqual(kit['kit_version'], old_version)
        self.assertEqual(kit['templates']['plans/plan.md'],
                         hashlib.sha256((shipped / 'plans/plan.md').read_bytes()).hexdigest())
        self.assertEqual(kit['templates']['LIST.md'], base['LIST.md'])
        again = self.update(helper)
        self.assertEqual(([e['path'] for e in again['conflict']], again['safe'], again['new']), (['LIST.md'], [], []))
        result = self.run_ops('session-start', stdin=json.dumps({'cwd': str(self.main)}), helper=helper)
        self.assertIn('Kit ' + old_version + ', plugin 9.9.9: run /kimiflow update', result.stdout)
        (ops / 'LIST.md').write_bytes((shipped / 'LIST.md').read_bytes())
        self.update(helper, '--apply-safe')
        self.assertEqual(self.kit()['kit_version'], '9.9.9')

    def test_update_refuses_a_symlinked_kit_file(self):
        self.init()
        kit = self.main / '.kimiflow' / 'ops' / '.kit.json'
        outside = self.tmp / 'outside.json'
        outside.write_bytes(kit.read_bytes())
        kit.unlink(); kit.symlink_to(outside)
        result = self.run_ops('update', '--root', str(self.main), '--apply-safe',
                              helper=self.upstream(version='9.9.9'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nothing was written', result.stderr)
        self.assertEqual(outside.read_bytes(), (self.main / '.kimiflow' / 'ops' / '.kit.json').read_bytes())
        self.assertNotIn('9.9.9', outside.read_text())

    def test_update_never_reads_a_symlinked_project_file(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        base = self.kit()['templates']['LIST.md']
        secret = self.tmp / 'secret.txt'
        secret.write_text('SECRET-OUTSIDE\n')
        (ops / 'LIST.md').unlink(); (ops / 'LIST.md').symlink_to(secret)
        result = self.run_ops('update', '--root', str(self.main), '--apply-safe', helper=self.upstream())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('SECRET-OUTSIDE', result.stdout)
        entry = json.loads(result.stdout)['unknown_base'][0]
        self.assertEqual((entry['path'], 'diff' in entry), ('LIST.md', False))
        self.assertEqual(self.kit()['templates']['LIST.md'], base)
        self.assertEqual(secret.read_text(), 'SECRET-OUTSIDE\n')

    def test_update_keeps_owner_edit_when_upstream_is_unchanged(self):
        self.init()
        operations = self.main / '.kimiflow' / 'ops' / 'OPERATIONS.md'
        operations.write_text('owner rules\n')
        report = self.update(self.upstream(), '--apply-safe')
        self.assertIn('OPERATIONS.md', report['unchanged'])
        self.assertEqual(operations.read_text(), 'owner rules\n')

    def test_update_resolved_completes_a_hand_merge(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        (ops / 'LIST.md').write_text('owner list\n')
        helper = self.upstream(version='9.9.9')
        self.update(helper, '--apply-safe')
        (ops / 'LIST.md').write_text('owner list\nupstream line\n')
        rejected = self.run_ops('update', '--root', str(self.main), '--resolved', 'OPERATIONS.md', helper=helper)
        self.assertNotEqual(rejected.returncode, 0)
        report = self.update(helper, '--resolved', 'LIST.md')
        self.assertEqual((report['resolved'], report['conflict']), (['LIST.md'], []))
        self.assertEqual((ops / 'LIST.md').read_text(), 'owner list\nupstream line\n')
        self.assertEqual(self.kit()['kit_version'], '9.9.9')
        self.assertIn('LIST.md', self.update(helper)['unchanged'])
        result = self.run_ops('session-start', stdin=json.dumps({'cwd': str(self.main)}), helper=helper)
        self.assertNotIn('/kimiflow update', result.stdout)

    def test_update_resolved_alone_keeps_pending_safe_files_pending(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        (ops / 'LIST.md').write_text('owner list\n')
        helper = self.upstream(version='9.9.9')
        report = self.update(helper)
        self.assertEqual((report['safe'], [entry['path'] for entry in report['conflict']]),
                         (['plans/plan.md'], ['LIST.md']))
        self.update(helper, '--resolved', 'LIST.md')
        self.assertEqual(self.update(helper)['safe'], ['plans/plan.md'])
        self.assertNotEqual(self.kit()['kit_version'], '9.9.9')

    def test_update_resolved_accepts_an_unknown_base_file(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        (ops / '.kit.json').unlink()
        (ops / 'OPERATIONS.md').write_text('owner rules\n')
        report = self.update(self.helper, '--resolved', 'OPERATIONS.md')
        self.assertEqual((report['resolved'], report['unknown_base']), (['OPERATIONS.md'], []))
        self.assertIn('OPERATIONS.md', self.update(self.helper)['unchanged'])
        self.assertEqual((ops / 'OPERATIONS.md').read_text(), 'owner rules\n')

    def test_init_and_update_refuse_tracked_kit_files(self):
        self.init()
        git(self.main, 'add', '-f', '.kimiflow/ops/LIST.md')
        for command in ('init', 'update'):
            with self.subTest(command=command):
                result = self.run_ops(command, '--root', str(self.main))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('tracked', result.stderr)

    def test_update_refuses_symlinks_leading_outside(self):
        self.init()
        outside = self.tmp / 'outside'
        outside.mkdir()
        plans = self.main / '.kimiflow' / 'ops' / 'plans'
        shutil.rmtree(plans)
        plans.symlink_to(outside, target_is_directory=True)
        result = self.run_ops('update', '--root', str(self.main), '--apply-safe')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])

    def test_init_keeps_owner_edited_file(self):
        self.init()
        operations = self.main / '.kimiflow' / 'ops' / 'OPERATIONS.md'
        operations.write_text('owner rules\n')
        report = self.init()
        self.assertEqual(operations.read_text(), 'owner rules\n')
        self.assertIn('.kimiflow/ops/OPERATIONS.md', report['kept'])

    def test_exclude_added_once_and_effective_in_worktree(self):
        exclude = self.main / '.git' / 'info' / 'exclude'
        exclude.write_text('# existing\n*.log')
        worktree = self.worktree()
        report = self.init(worktree)
        self.assertEqual(Path(report['exclude']).resolve(), exclude.resolve())
        self.init(worktree)
        lines = exclude.read_text().splitlines()
        self.assertEqual(lines.count('.kimiflow/'), 1)
        self.assertIn('*.log', lines)
        (worktree / '.kimiflow').mkdir()
        (worktree / '.kimiflow' / 'note.md').write_text('local')
        status = subprocess.run(['git', '-C', str(worktree), 'status', '--porcelain', '--ignored'],
                                capture_output=True, text=True, check=True).stdout
        self.assertIn('!! .kimiflow/', status.splitlines())

    def test_init_refuses_a_legacy_ops_task(self):
        notes = self.main / '.kimiflow' / 'ops' / 'NOTES.md'
        notes.parent.mkdir(parents=True)
        notes.write_text('legacy task')
        result = self.run_ops('init', '--root', str(self.main))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('NOTES.md', result.stderr)
        self.assertFalse((notes.parent / 'OPERATIONS.md').exists())
        self.assertEqual(notes.read_text(), 'legacy task')

    def test_init_reports_legacy_runs_without_touching_them(self):
        state = self.main / '.kimiflow' / 'old-run' / 'STATE.md'
        state.parent.mkdir(parents=True)
        state.write_text('Flow schema')
        report = self.init()
        self.assertEqual(report['legacy'], ['.kimiflow/old-run'])
        self.assertEqual(snapshot(state.parent), {'STATE.md': b'Flow schema'})

    def test_init_refuses_symlinks_leading_outside_the_checkout(self):
        for link in ('.kimiflow', '.kimiflow/ops/plans'):
            with self.subTest(link=link):
                outside = self.tmp / ('outside-' + link.replace('/', '-'))
                outside.mkdir()
                kimiflow = self.main / '.kimiflow'
                if kimiflow.is_symlink():
                    kimiflow.unlink()
                (self.main / link).parent.mkdir(parents=True, exist_ok=True)
                (self.main / link).symlink_to(outside, target_is_directory=True)
                result = self.run_ops('init', '--root', str(self.main))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('outside', result.stderr)
                self.assertEqual(list(outside.iterdir()), [])
                self.assertFalse((self.main / '.kimiflow' / 'ops' / 'OPERATIONS.md').exists())

    def test_init_rejects_a_non_git_root(self):
        plain = self.tmp / 'plain'
        plain.mkdir()
        result = self.run_ops('init', '--root', str(plain))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((plain / '.kimiflow').exists())

    # session-start

    def test_session_start_silent_when_not_initialized_not_git_or_bad_json(self):
        plain = self.tmp / 'plain'
        plain.mkdir()
        self.init()
        for cwd, stdin in ((self.tmp / 'other', None), (plain, None), (self.main, '{not json')):
            if cwd.name == 'other':
                git(self.tmp, 'init', '-q', 'other')
            with self.subTest(cwd=cwd.name):
                result = self.session(cwd, stdin)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_session_start_prints_absolute_paths_also_from_worktree(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        worktree = self.worktree()
        for cwd in (self.main, worktree):
            with self.subTest(cwd=cwd.name):
                result = self.session(cwd)
                self.assertEqual(result.returncode, 0, result.stderr)
                for name in ('OPERATIONS.md', 'OPERATIONS-history.md', 'rules-worker.md', 'HANDOFF.md',
                             'LIST.md'):
                    self.assertIn(str(ops / name), result.stdout)
                self.assertIn(str(ops / 'plans'), result.stdout)
                self.assertIn('operating-model.md', result.stdout)
                self.assertLess(len(result.stdout.encode()), 1600)

    def test_session_start_reads_cwd_from_the_payload(self):
        self.init()
        plain = self.tmp / 'plain'
        plain.mkdir()
        payload = json.dumps({'cwd': str(self.main), 'hook_event_name': 'SessionStart'})
        result = self.run_ops('session-start', stdin=payload, cwd=plain)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.main / '.kimiflow' / 'ops' / 'OPERATIONS.md'), result.stdout)

    def test_session_start_silent_without_git(self):
        self.init()
        empty = self.tmp / 'no-git-bin'
        empty.mkdir()
        payload = json.dumps({'cwd': str(self.main)})
        result = subprocess.run([sys.executable, str(self.helper), 'session-start'], input=payload,
                                cwd=self.main, capture_output=True, text=True, timeout=60,
                                env={**os.environ, 'PATH': str(empty)})
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_session_start_silent_for_repo_supplied_kit(self):
        self.init()
        git(self.main, 'add', '-f', '.kimiflow/ops/OPERATIONS.md')
        git(self.main, 'commit', '-q', '-m', 'ship kit')
        self.assertEqual(self.session(self.main).stdout, '')
        other = self.tmp / 'cloned'
        git(self.tmp, 'init', '-q', 'cloned')
        (other / '.kimiflow' / 'ops').mkdir(parents=True)
        (other / '.kimiflow' / 'ops' / 'OPERATIONS.md').write_text('repo rules\n')
        result = self.session(other)
        self.assertEqual((result.returncode, result.stdout), (0, ''))

    def test_session_start_version_line(self):
        self.init()
        self.assertNotIn('/kimiflow update', self.session(self.main).stdout)
        result = self.run_ops('session-start', stdin=json.dumps({'cwd': str(self.main)}),
                              helper=self.upstream(version='9.9.9', edits=()))
        version = self.kit()['kit_version']
        self.assertIn('Kit ' + version + ', plugin 9.9.9: run /kimiflow update', result.stdout)
        (self.main / '.kimiflow' / 'ops' / '.kit.json').unlink()
        self.assertIn('Kit unknown, plugin ' + version, self.session(self.main).stdout)

    def test_session_start_counts_commits_since_handoff(self):
        self.init()
        handoff = self.main / '.kimiflow' / 'ops' / 'HANDOFF.md'
        committed = int(subprocess.run(['git', '-C', str(self.main), 'log', '-1', '--format=%ct'],
                                       capture_output=True, text=True, check=True).stdout)
        os.utime(handoff, (committed + 3600, committed + 3600))
        self.assertNotIn('commit(s) since', self.session(self.main).stdout)
        git(self.main, 'commit', '-q', '--allow-empty', '-m', 'second')
        os.utime(handoff, (committed - 3600, committed - 3600))
        for cwd in (self.main, self.worktree()):
            with self.subTest(cwd=cwd.name):
                self.assertRegex(self.session(cwd).stdout,
                                 r'HANDOFF\.md last updated \d{4}-\d\d-\d\d \d\d:\d\d; 2 commit\(s\) since '
                                 r'— check git log for what changed before relying on it\.')

    def test_session_start_size_notices(self):
        self.init()
        ops = self.main / '.kimiflow' / 'ops'
        fresh = self.session(self.main).stdout
        self.assertNotIn('KB (limit', fresh)
        cases = (('OPERATIONS.md', 12, 'OPERATIONS.md is 13 KB (limit 12 KB): move reasons and history to '
                  'OPERATIONS-history.md and keep the rules short.'),
                 ('HANDOFF.md', 6, 'HANDOFF.md is 7 KB (limit 6 KB): rewrite it to the current state and '
                  'move older parts to archive/.'))
        for name, limit, notice in cases:
            with self.subTest(name=name):
                (ops / name).write_bytes(b'x' * (limit * 1024 + 1))
                self.assertIn(notice, self.session(self.main).stdout)
                (ops / name).write_bytes(b'x' * (limit * 1024))
                self.assertNotIn(name + ' is', self.session(self.main).stdout)

    # git-guard

    def guard(self, command, cwd=None, stdin=None):
        payload = json.dumps({'cwd': str(cwd or self.main), 'tool_input': {'command': command}})
        return self.run_ops('git-guard', stdin=payload if stdin is None else stdin)

    def test_git_guard_blocks_bulk_add_and_attribution_in_an_initialized_project(self):
        self.init()
        blocked = {'git add -A': 'named paths', 'git add .': 'named paths',
                   'git -C "x y" add --all': 'named paths', 'git add :/': 'named paths',
                   'cd x && git add -A': 'named paths', 'git add *': 'named paths',
                   'git commit -m "fix\n\nWith: Claude <noreply@anthropic.com>"': 'AI attribution',
                   'git commit -m "x" -m "made with https://claude.com/claude-code"': 'AI attribution',
                   'git commit -m "fix\n\nWith: Codex <noreply@openai.com>"': 'AI attribution',
                   'git add -- .': 'named paths',
                   'git commit -F - <<EOF\nfix\n\nWith: Claude <noreply@anthropic.com>\nEOF': 'AI attribution',
                   'git commit -m "$(cat <<EOF\nfix\n\nWith: Claude <noreply@anthropic.com>\nEOF\n)"':
                       'AI attribution'}
        for command, rule in blocked.items():
            with self.subTest(command=command):
                result = self.guard(command, cwd=self.worktree() if command == 'git add .' else None)
                self.assertEqual((result.returncode, result.stdout), (2, ''))
                self.assertIn(rule, result.stderr)
                self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_git_guard_allows_named_paths_and_plain_commits(self):
        self.init()
        for command in ('git add ./src/a.py', 'echo git add -A', 'git add src/a.py tests/b.py',
                        'git commit -m "fix"', 'git status', 'echo "noreply@anthropic.com"',
                        'git log --grep "noreply@anthropic.com"',
                        'git commit -m "fix\n\nWith: Ana <ana@example.org>"', 'git add -p', 'git add -- file',
                        'git add src/.env.example', 'git commit -m "add ."',
                        'git commit -m "$(cat <<EOF\nfix: tidy\n\nBody line.\nEOF\n)"'):
            with self.subTest(command=command):
                result = self.guard(command)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_git_guard_silent_outside_an_initialized_project_and_on_bad_input(self):
        plain = self.tmp / 'plain'
        plain.mkdir()
        cases = [(self.main, None), (plain, None)]
        for cwd, stdin in cases + [(self.main, '{not json'), (self.main, '[]'), (self.main, '')]:
            with self.subTest(cwd=cwd.name, stdin=stdin):
                result = self.guard('git add -A', cwd=cwd, stdin=stdin)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        self.init()
        for stdin in ('{not json', '[]', ''):
            with self.subTest(initialized=True, stdin=stdin):
                result = self.guard('git add -A', stdin=stdin)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_built_helper_is_a_regular_file(self):
        self.assertEqual(stat.S_IMODE(self.helper.stat().st_mode), 0o644)


if __name__ == '__main__':
    unittest.main()
