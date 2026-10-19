import json
import re
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest import mock

from scripts.build_plugin import build, RUNTIME_FILES, MANIFEST, MARKETPLACES

CLAUDE_AGENTS = ('./claude/agents/worker.md', './claude/agents/reviewer.md', './claude/agents/architect.md')
from scripts.render_skills import RENDER_TARGETS

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in set(RUNTIME_FILES) | set(MARKETPLACES) | {source for source, _ in RENDER_TARGETS}:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / relative).read_bytes())

    def test_reproducible_exact_runtime_and_no_private_files(self):
        (self.root / '.kimiflow').mkdir()
        (self.root / '.kimiflow' / 'private.txt').write_text('do not ship')
        (self.root / 'scripts' / 'unlisted.py').write_text('do not ship')
        build(self.root)
        target = self.root / 'plugins' / 'kimiflow'
        first = (target / MANIFEST).read_bytes()
        build(self.root)
        self.assertEqual(first, (target / MANIFEST).read_bytes())
        self.assertEqual(build(self.root, check=True)['status'], 'current')
        self.assertFalse((target / 'hooks' / 'hooks.json').exists())
        self.assertTrue((target / 'claude' / 'hooks.json').is_file())
        self.assertFalse((target / '.kimiflow').exists())
        self.assertFalse((target / 'scripts' / 'unlisted.py').exists())
        self.assertEqual(json.loads(first)['file_count'], len(RUNTIME_FILES))

    def test_check_rejects_stale_render_without_rewriting(self):
        build(self.root)
        skill = self.root / 'SKILL.md'
        skill.write_text('manual drift')
        with self.assertRaisesRegex(ValueError, 'Rendered skills differ'):
            build(self.root, check=True)
        self.assertEqual(skill.read_text(), 'manual drift')

    def test_check_rejects_extra_missing_modified_and_mode_drift(self):
        for kind in ('extra', 'missing', 'modified', 'mode'):
            with self.subTest(kind=kind):
                build(self.root)
                target = self.root / 'plugins' / 'kimiflow'
                if kind == 'extra': (target / 'legacy.py').write_text('unexpected')
                elif kind == 'missing': (target / 'SKILL.md').unlink()
                elif kind == 'modified': (target / 'SKILL.md').write_text('wrong')
                else: (target / 'SKILL.md').chmod(0o755)
                with self.assertRaises(ValueError):
                    build(self.root, check=True)

    def test_refuses_symlink_and_unmanaged_output(self):
        parent = self.root / 'plugins'
        target = parent / 'kimiflow'
        target.mkdir(parents=True)
        (target / 'mine').write_text('preserve')
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            build(self.root)
        self.assertEqual((target / 'mine').read_text(), 'preserve')
        (target / 'mine').unlink(); target.rmdir()
        target.symlink_to(self.root / 'scripts', target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            build(self.root)

    def test_missing_and_symlinked_source_cannot_ship(self):
        source = self.root / 'scripts' / 'check_change.py'
        source.unlink()
        with self.assertRaises(ValueError): build(self.root)
        source.symlink_to(ROOT / 'scripts' / 'check_change.py')
        with self.assertRaises(ValueError): build(self.root)

    def test_render_rejects_external_source_before_packaging(self):
        source = self.root / 'docs/render/kimiflow/canonical/SKILL.md'
        with tempfile.TemporaryDirectory() as external:
            secret = Path(external) / 'private.md'
            secret.write_text('private external content')
            source.unlink(); source.symlink_to(secret)
            with self.assertRaises((ValueError, OSError)):
                build(self.root)
            self.assertNotEqual((self.root / 'SKILL.md').read_text(), secret.read_text())

    def test_render_does_not_write_through_external_output_parent(self):
        import shutil
        parent = self.root / 'skills/kimiflow'
        with tempfile.TemporaryDirectory() as external:
            output = Path(external) / 'SKILL.md'
            output.write_text('preserve external file')
            shutil.rmtree(parent); parent.symlink_to(external, target_is_directory=True)
            with self.assertRaises((ValueError, OSError)):
                build(self.root)
            self.assertEqual(output.read_text(), 'preserve external file')

    def test_versions_and_removed_registrations_are_checked(self):
        path = self.root / '.codex-plugin' / 'plugin.json'
        original = path.read_text()
        for field, value in [('version', '99.0.0'), ('hooks', './hooks.json'), ('mcpServers', {})]:
            data = json.loads(original); data[field] = value
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): build(self.root)

    def test_claude_manifest_accepts_only_the_session_start_hooks_file(self):
        path = self.root / '.claude-plugin' / 'plugin.json'
        self.assertEqual(json.loads(path.read_text())['hooks'], './claude/hooks.json')
        original = path.read_text()
        for value in ('./hooks/hooks.json', './claude/other.json', ['./claude/hooks.json'], {}):
            with self.subTest(value=value):
                data = json.loads(original); data['hooks'] = value
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'Only the Claude manifest'):
                    build(self.root)
        data = json.loads(original); data['mcpServers'] = {}
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): build(self.root)

    def test_only_the_claude_manifest_registers_the_shipped_agents(self):
        path = self.root / '.claude-plugin' / 'plugin.json'
        original = path.read_text()
        self.assertEqual(json.loads(original)['agents'], list(CLAUDE_AGENTS))
        for value in ('./claude/agents/', './agents/worker.md', list(CLAUDE_AGENTS)[:2], {}):
            with self.subTest(value=value):
                data = json.loads(original); data['agents'] = value
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'Only the Claude manifest'):
                    build(self.root)
        path.write_text(original)
        codex = self.root / '.codex-plugin' / 'plugin.json'
        data = json.loads(codex.read_text()); data['agents'] = list(CLAUDE_AGENTS)
        codex.write_text(json.dumps(data))
        with self.assertRaises(ValueError): build(self.root)

    def test_shipped_agents_leave_the_model_to_the_planner(self):
        build(self.root)
        target = self.root / 'plugins' / 'kimiflow'
        for relative in CLAUDE_AGENTS:
            text = (target / relative).read_text()
            front = text.split('---')[1]
            with self.subTest(agent=relative):
                self.assertIsNone(re.search(r'^model:', front, re.M))
                self.assertIn('name: ' + Path(relative).stem, front)
                self.assertLessEqual(len(text.splitlines()), 55)
        architect = (target / 'claude/agents/architect.md').read_text().split('---')[1]
        self.assertIn('tools: Read, Grep, Glob, Bash, WebSearch, WebFetch\n', architect)

    def test_claude_hooks_register_session_start_and_git_guard(self):
        hooks = json.loads((self.root / 'claude' / 'hooks.json').read_text())['hooks']
        expected = {'SessionStart': (None, 'session-start'), 'PreToolUse': ('Bash', 'git-guard')}
        self.assertEqual(set(hooks), set(expected))
        for event, (matcher, command) in expected.items():
            with self.subTest(event=event):
                [entry] = hooks[event]
                [hook] = entry['hooks']
                self.assertEqual(entry.get('matcher'), matcher)
                self.assertEqual((hook['type'], hook['command']), ('command', 'python3'))
                self.assertEqual(hook['args'], ['${CLAUDE_PLUGIN_ROOT}/scripts/kimiflow_ops.py', command])

    def test_marketplace_version_must_match_the_runtime(self):
        path = self.root / '.claude-plugin' / 'marketplace.json'
        data = json.loads(path.read_text()); data['plugins'][0]['version'] = '0.4.3'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'Marketplace source/version'):
            build(self.root)

    def test_host_wrappers_render_from_sources(self):
        build(self.root)
        for source, output in RENDER_TARGETS:
            self.assertEqual((self.root / source).read_bytes(), (self.root / output).read_bytes())
        shared = (self.root / 'SKILL.md').read_text()
        self.assertLess(len(shared.encode()), 4000)
        self.assertTrue((self.root / 'MIGRATION.md').is_file())

    def test_shipped_texts_do_not_exclude_a_model(self):
        model = r'(?:haiku|sonnet|opus|fable)'
        patterns = [r'must not use [^.]*' + model, r'never use [^.]*' + model, r'only (?:use )?' + model,
                    r'do not use [^.]*' + model, r'avoid [^.]*' + model,
                    model + r'[^.]* (?:must|should) not be used',
                    model + r' (?:is|are) not (?:allowed|permitted)',
                    model + r'[^.]* nicht (?:verwenden|nutzen|einsetzen)']
        for relative in RUNTIME_FILES:
            if relative.endswith('.md'):
                text = (self.root / relative).read_text().lower()
                for pattern in patterns:
                    self.assertIsNone(re.search(pattern, text), (relative, pattern))

    def test_shipped_texts_are_project_agnostic(self):
        terms = ('m8thor', 'xcodebuild', 'swiftui', 'deriveddata', 'sparkle', '/volumes', 'scratchpad-keep')
        for relative in RUNTIME_FILES:
            if relative.endswith(('.md', '.json')) and relative != 'references/models.md':
                text = (self.root / relative).read_text().lower()
                for term in terms:
                    self.assertNotIn(term, text, relative)

    def test_installed_skill_references_are_packaged(self):
        build(self.root)
        target = self.root / 'plugins' / 'kimiflow'
        for relative in ('SKILL.md', 'skills/kimiflow/SKILL.md', 'hosts/pi/skills/kimiflow/SKILL.md'):
            skill = target / relative
            for link in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', skill.read_text()):
                if '://' not in link:
                    self.assertTrue((skill.parent / link).is_file(), (relative, link))

    def test_exported_helper_runs_without_maintainer_modules(self):
        build(self.root)
        with tempfile.TemporaryDirectory() as project:
            subprocess.run(['git', 'init', '-q', project], check=True)
            helper = self.root / 'plugins' / 'kimiflow' / 'scripts' / 'check_change.py'
            result = subprocess.run([sys.executable, str(helper), '--root', project, '--check',
                                     json.dumps([sys.executable, '-c', 'pass'])],
                                    cwd=project, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'passed')

    def test_failed_install_restores_previous_generated_package(self):
        import os
        build(self.root)
        target = self.root / 'plugins' / 'kimiflow'
        before = (target / MANIFEST).read_bytes()
        replace = os.replace
        def fail_candidate(source, destination):
            if Path(source).name == 'candidate':
                raise OSError('simulated install failure')
            return replace(source, destination)
        with mock.patch('scripts.build_plugin.os.replace', side_effect=fail_candidate):
            with self.assertRaisesRegex(OSError, 'simulated'):
                build(self.root)
        self.assertEqual((target / MANIFEST).read_bytes(), before)
        self.assertEqual(build(self.root, check=True)['status'], 'current')


if __name__ == '__main__':
    unittest.main()
