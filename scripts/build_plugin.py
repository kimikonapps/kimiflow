"""Build/check the small, explicit runtime package. No Git-index or model dependency."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

if __package__:
    from .render_skills import render
else:
    from render_skills import render

RUNTIME_FILES = (
    '.claude-plugin/plugin.json', '.codex-plugin/plugin.json', 'package.json',
    'SKILL.md', 'skills/kimiflow/SKILL.md', 'skills/kimiflow/agents/openai.yaml',
    'hosts/pi/skills/kimiflow/SKILL.md', 'scripts/check_change.py',
    'references/feature-work.md',
    'README.md', 'README.de.md', 'MIGRATION.md', 'COMPATIBILITY.md', 'LICENSE',
)
MANIFEST = 'RUNTIME-FINGERPRINT.json'
MARKETPLACES = ('.claude-plugin/marketplace.json', '.agents/plugins/marketplace.json')


def payloads(root):
    files = {}
    for relative in RUNTIME_FILES:
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError('Missing or unsafe runtime source: ' + relative)
        files[relative] = path.read_bytes()
    claude = json.loads(files['.claude-plugin/plugin.json'])
    codex = json.loads(files['.codex-plugin/plugin.json'])
    pi = json.loads(files['package.json'])
    if not claude.get('version') or len({claude['version'], codex.get('version'), pi.get('version')}) != 1:
        raise ValueError('Runtime versions differ')
    if claude.get('name') != 'kimiflow' or codex.get('name') != 'kimiflow':
        raise ValueError('Unexpected plugin identity')
    if any(field in manifest for manifest in (claude, codex) for field in ('hooks', 'mcpServers', 'apps')) or pi.get('pi') != {'skills': ['./hosts/pi/skills/kimiflow']}:
        raise ValueError('The minimal runtime must not register hooks or extensions')
    for relative in MARKETPLACES:
        marketplace = json.loads((root / relative).read_text())
        entries = [entry for entry in marketplace.get('plugins', []) if entry.get('name') == 'kimiflow']
        if len(entries) != 1:
            raise ValueError('Marketplace must identify this plugin once: ' + relative)
        entry = entries[0]
        source = entry.get('source')
        if relative.startswith('.claude'):
            valid = source == './plugins/kimiflow' and entry.get('version') == claude['version']
        else:
            valid = source == {'source': 'local', 'path': './plugins/kimiflow'}
        if not valid:
            raise ValueError('Marketplace source/version differs: ' + relative)
    digest = hashlib.sha256()
    rows = []
    for relative, content in sorted(files.items()):
        digest.update(relative.encode() + b'\0' + content + b'\0')
        rows.append({'path': relative, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    files[MANIFEST] = (json.dumps({'schema_version': 1, 'version': claude['version'],
        'runtime_fingerprint': 'sha256:' + digest.hexdigest(), 'file_count': len(rows),
        'files': rows}, indent=2, sort_keys=True) + '\n').encode()
    return files


def build(root, check=False):
    root = Path(root).resolve()
    drift = render(root, check=check)
    if check and drift:
        raise ValueError('Rendered skills differ: ' + ', '.join(drift))
    files = payloads(root)
    parent = root / 'plugins'
    target = parent / 'kimiflow'
    if parent.is_symlink() or target.is_symlink():
        raise ValueError('Unsafe generated package path')
    if check:
        actual = set()
        if not target.is_dir():
            raise ValueError('Generated package missing')
        for path in target.rglob('*'):
            if path.is_symlink():
                raise ValueError('Generated package contains a symlink')
            if path.is_file():
                relative = path.relative_to(target).as_posix()
                actual.add(relative)
                if relative not in files or path.read_bytes() != files[relative]:
                    raise ValueError('Generated package drift: ' + relative)
                if stat.S_IMODE(path.stat().st_mode) != 0o644:
                    raise ValueError('Generated package mode drift: ' + relative)
        if actual != set(files):
            raise ValueError('Generated package inventory differs')
    else:
        if target.exists() and (not (target / MANIFEST).is_file() or (target / MANIFEST).is_symlink()):
            raise ValueError('Refusing to replace an unmanaged directory')
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.kimiflow-build-', dir=parent) as temporary:
            candidate, backup = Path(temporary) / 'candidate', Path(temporary) / 'backup'
            candidate.mkdir()
            for relative, content in files.items():
                path = candidate / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                path.chmod(0o644)
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(candidate, target)
            except OSError:
                if backup.exists():
                    os.replace(backup, target)
                raise
    return {'status': 'current' if check else 'built', 'runtime_files': len(files),
            'output': str(target)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.root, check=args.check)))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
