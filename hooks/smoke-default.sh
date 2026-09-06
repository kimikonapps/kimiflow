#!/usr/bin/env bash
# Validate installed surfaces and execute the default verifier in a disposable repo.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
HOST="claude"
if [ "${1:-}" = "--host" ]; then HOST="${2:-}"; shift 2; fi
[ "$#" -eq 0 ] || { echo 'smoke-install: unknown arguments' >&2; exit 2; }
python3 - "$ROOT" "$HOST" <<'PY'
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
host = sys.argv[2]
assert host in {'claude', 'codex'}, 'unknown host'
manifest = root / ('.claude-plugin/plugin.json' if host == 'claude' else '.codex-plugin/plugin.json')
data = json.loads(manifest.read_text())
assert data['name'] == 'kimiflow' and data['version']
if host == 'codex':
    assert data['hooks'] == './hooks/hooks.json'
    assert (root / data['skills']).is_dir()
    assert data['interface']['displayName'] == 'kimiflow'
    marketplace = json.loads((root / '.agents/plugins/marketplace.json').read_text())
    assert any(p['name'] == 'kimiflow' and p['source']['path'] == './plugins/kimiflow' for p in marketplace['plugins'])
else:
    marketplace = json.loads((root / '.claude-plugin/marketplace.json').read_text())
    assert marketplace['plugins'][0]['version'] == data['version']

for relative in ('SKILL.md', 'skills/kimiflow/SKILL.md', 'hosts/pi/skills/kimiflow/SKILL.md'):
    path = root / relative
    content = path.read_text()
    frontmatter = content.split('---', 2)[1]
    assert re.search(r'^name:\s*kimiflow\s*$', frontmatter, re.M)
    assert re.search(r'^description:', frontmatter, re.M)
    assert not re.search(r'^(?:disable-model-invocation:\s*true|user-invocable:\s*false)', frontmatter, re.M)
    for target in re.findall(r'\]\(([^)#]+)(?:#[^)]*)?\)', content):
        if '://' not in target:
            assert (path.parent / target).is_file(), (relative, target)
assert len((root / 'SKILL.md').read_bytes()) <= 8500
assert len((root / 'skills/kimiflow/SKILL.md').read_bytes()) <= 2500
assert len((root / 'hosts/pi/skills/kimiflow/SKILL.md').read_bytes()) <= 1800
for relative in ('references/legacy-workflow.md', 'references/legacy-codex.md',
                 'references/legacy-pi.md', 'references/optional-tools.md'):
    assert (root / relative).is_file(), relative
for phase in json.loads((root / 'phases/PHASES.json').read_text())['phases']:
    assert (root / phase['file']).is_file(), phase['file']
for relative in ('hooks/hooks.json', 'hooks.json'):
    hooks = json.loads((root / relative).read_text())['hooks']
    events = ('UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'Stop')
    if relative == 'hooks/hooks.json':
        events += ('SessionStart',)
    for event in events:
        assert hooks[event], (relative, event)
# Detailed legacy hook ownership, intake and Git boundaries have executable suites
# in the full CI plan; installation does not assert obsolete prompt wording.
print('PASS: ' + host + ' installation, entry points and legacy resources')

with tempfile.TemporaryDirectory(prefix='kimiflow install ') as directory:
    subprocess.run(['git', 'init', '-q', directory], check=True)
    Path(directory, 'source.txt').write_text('before')
    helper = root / 'hooks/check-change.sh'
    assert os.access(helper, os.X_OK)
    def run(code):
        proc = subprocess.run([str(helper), '--root', directory, '--check',
                               json.dumps([sys.executable, '-c', code])],
                              capture_output=True, text=True)
        return proc.returncode, json.loads(proc.stdout)
    rc, result = run('print("verified")')
    assert rc == 0 and result['status'] == 'passed', result
    rc, result = run('raise SystemExit(7)')
    assert rc != 0 and result['status'] == 'failed', result
    rc, result = run('open("source.txt", "w").write("after")')
    assert rc != 0 and result['status'] == 'changed', result
    assert not Path(directory, '.kimiflow').exists()
print('PASS: ' + host + ' installed verifier passes, fails and rejects source drift')
PY
