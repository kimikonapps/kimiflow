import json
import re
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest import mock

from scripts.build_plugin import build, RUNTIME_FILES, MANIFEST, MARKETPLACES
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
        self.assertFalse((target / 'hooks').exists())
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
