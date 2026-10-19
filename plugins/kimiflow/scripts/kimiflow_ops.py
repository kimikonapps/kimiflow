"""Kimiflow operating kit: set it up in a project, update its templates, point each session at it.

session-start speaks only for a kit set up locally by init (Git's local exclude lists .kimiflow/ and
no kit file is tracked), so a cloned repository cannot promote its own files to operating rules.
Stdlib only.
"""
import argparse
from datetime import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
KIT = PLUGIN_ROOT / 'templates' / 'ops'
KIT_FILE = '.kit.json'
DIFF_LINES = 200
# Per line, like grep: git starts a command segment, optionally after -C <path> global options.
_GIT = r'(?:^|[;&|])[ \t]*git(?:[ \t]+-C[ \t]+(?:"[^"\n]*"|\'[^\'\n]*\'|[^ \t\n]+))*[ \t]+'
BULK_ADD = re.compile(_GIT + r'add[ \t]+(?:[^;&|\n]*[ \t])?(?:-A|--all|\.|:/|\*)(?:[ \t]|$|[;&|])', re.M)
COMMIT = re.compile(_GIT + 'commit', re.M)
# AI tools sign their commit trailers and footers with their own address or product link; match those
# signatures, so the guard needs no trailer wording and a human co-author line stays allowed.
ATTRIBUTION = re.compile(r'noreply@(?:anthropic|openai)\.com|claude\.(?:com|ai)/(?:claude-)?code', re.I)


def _git(cwd, *args):
    try:
        result = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def main_checkout(cwd):
    """The main working tree for cwd, also from a linked worktree; None for anything unusual."""
    cwd = Path(cwd).resolve()
    common = _git(cwd, 'rev-parse', '--git-common-dir')
    if not common:
        return None
    common = (cwd / common).resolve()
    return common.parent if common.name == '.git' else None


def ops_dir(main):
    return main / '.kimiflow' / 'ops'


def plugin_version():
    for name in ('.claude-plugin/plugin.json', 'package.json'):
        try:
            return str(json.loads((PLUGIN_ROOT / name).read_text())['version'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_kit(ops):
    if (ops / KIT_FILE).is_symlink():
        return None
    try:
        kit = json.loads((ops / KIT_FILE).read_text())
    except (OSError, ValueError):
        return None
    return kit if isinstance(kit, dict) else None


def _write_kit(ops, templates, version):
    data = {'kit_version': version, 'templates': templates}
    (ops / KIT_FILE).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def _targets(root):
    """Main checkout, ops dir and (template, project file) pairs, after the safety checks."""
    main = main_checkout(root)
    if main is None:
        raise ValueError('Not inside the main checkout or a linked worktree of a Git repository: ' + str(root))
    ops = ops_dir(main)
    for name in ('STATE.md', 'NOTES.md'):
        if (ops / name).exists():
            raise ValueError(str(ops / name) + ' belongs to an earlier Kimiflow task named "ops". '
                             'Rename or move that task directory, then run init again.')
    targets = [(source, ops / source.relative_to(KIT))
               for source in sorted(path for path in KIT.rglob('*') if path.is_file())]
    inside = main.resolve()
    for target in [ops] + [target.parent for _, target in targets]:
        if not target.resolve().is_relative_to(inside):
            raise ValueError(str(target) + ' leads outside ' + str(inside) + ' through a symlink; '
                             'nothing was written.')
    if (ops / KIT_FILE).is_symlink():
        raise ValueError(str(ops / KIT_FILE) + ' is a symlink; nothing was written.')
    tracked = _git(main, 'ls-files', '--', '.kimiflow')
    if tracked is None or tracked:
        raise ValueError('The repository tracks files under .kimiflow/ (or Git could not tell). The kit is '
                         'local-only, so tracked kit files are repository content; nothing was written.')
    return main, ops, targets


def _exclude(main):
    exclude = _git(main, 'rev-parse', '--git-path', 'info/exclude')
    if not exclude:
        raise ValueError('git rev-parse --git-path info/exclude failed in ' + str(main))
    exclude = (main / exclude).resolve()
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ''
    if '.kimiflow/' not in text.splitlines():
        with exclude.open('a') as handle:
            handle.write(('\n' if text and not text.endswith('\n') else '') + '.kimiflow/\n')
    return exclude


def init(root):
    main, ops, targets = _targets(root)
    created, kept = [], []
    for source, target in targets:
        name = target.relative_to(main).as_posix()
        if target.exists() or target.is_symlink():
            kept.append(name)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        created.append(name)
    kit = (ops / KIT_FILE).relative_to(main).as_posix()
    if (ops / KIT_FILE).exists() or (ops / KIT_FILE).is_symlink():
        kept.append(kit)
    else:
        _write_kit(ops, {target.relative_to(ops).as_posix(): _sha(source) for source, target in targets},
                   plugin_version())
        created.append(kit)
    exclude = _exclude(main)
    legacy = sorted(path.parent.relative_to(main).as_posix()
                    for path in (main / '.kimiflow').glob('*/STATE.md'))
    return {'created': created, 'kept': kept, 'exclude': str(exclude), 'legacy': legacy}


def _diff(target, source):
    lines = list(difflib.unified_diff(
        target.read_text(errors='replace').splitlines(keepends=True),
        source.read_text(errors='replace').splitlines(keepends=True),
        'project/' + target.name, 'kit/' + source.name))
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + ['... diff truncated after %d lines\n' % DIFF_LINES]
    return ''.join(lines)


def _classify(source, target, base):
    """Category for one template: new, unchanged, safe, conflict or unknown_base (with reason)."""
    if not target.exists() and not target.is_symlink():
        return 'new', None
    if target.is_symlink():
        return 'unknown_base', 'the project file is a symlink; it is never replaced'
    new, current = _sha(source), _sha(target)
    if current == new or base == new:
        return 'unchanged', None
    if base is None:
        return 'unknown_base', 'no base hash in .kit.json, so an owner edit cannot be told apart'
    return ('safe', None) if current == base else ('conflict', 'edited in the project and changed in the kit')


def _entry(name, reason, target, source):
    entry = {'path': name, 'reason': reason}
    if not target.is_symlink():
        entry['diff'] = _diff(target, source)
    return entry


def update(root, apply_safe=False, resolved=()):
    main, ops, targets = _targets(root)
    kit = _read_kit(ops) or {}
    base = kit.get('templates') if isinstance(kit.get('templates'), dict) else {}
    report = {'kit_version': kit.get('kit_version'), 'plugin_version': plugin_version(), 'new': [], 'safe': [],
              'conflict': [], 'unchanged': [], 'unknown_base': [], 'resolved': [], 'applied': False}
    synced = {}
    for source, target in targets:
        name = target.relative_to(ops).as_posix()
        category, reason = _classify(source, target, base.get(name))
        if name in resolved and reason and not target.is_symlink():
            category, reason = 'resolved', None  # merged by hand: the shipped version becomes the base
        report[category].append(_entry(name, reason, target, source) if reason else name)
        if category in ('unchanged', 'resolved') or (apply_safe and category in ('new', 'safe')):
            synced[name] = _sha(source)  # new/safe count as in sync only once they are copied
        elif name in base:
            synced[name] = base[name]
    rejected = sorted(set(resolved) - set(report['resolved']))
    if rejected:
        raise ValueError('--resolved takes only files in conflict or unknown_base (no symlinks): '
                         + ', '.join(rejected) + '; nothing was written.')
    if apply_safe:
        for source, target in targets:
            if target.relative_to(ops).as_posix() in report['new'] + report['safe']:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    if apply_safe or resolved:
        # The version moves only when everything is in sync, so session start keeps reminding until
        # the owner has merged the remaining files by hand and marked them --resolved.
        settled = not (report['conflict'] or report['unknown_base']
                       or (not apply_safe and (report['new'] or report['safe'])))
        _write_kit(ops, synced, plugin_version() if settled else kit.get('kit_version'))
        report['applied'] = True
    return report


def _set_up_locally(main):
    """True only for a kit created by init here: local exclude entry and nothing tracked."""
    try:
        exclude = (main / '.git' / 'info' / 'exclude').read_text()
    except OSError:
        return False
    return '.kimiflow/' in exclude.splitlines() and _git(main, 'ls-files', '--', '.kimiflow') == ''


def _stamp(seconds):
    return datetime.fromtimestamp(seconds).strftime('%Y-%m-%d %H:%M')


def _notices(cwd, ops):
    lines = []
    kit, plugin = _read_kit(ops), plugin_version()
    kit_version = (kit or {}).get('kit_version')
    if kit_version is None or kit_version != plugin:
        lines.append('Kit %s, plugin %s: run /kimiflow update to review template changes.'
                     % (kit_version or 'unknown', plugin or 'unknown'))
    try:
        # Rounded up: a commit in the same second as the HANDOFF edit usually came before it.
        handoff = int(-(-(ops / 'HANDOFF.md').stat().st_mtime // 1))
        since = int(_git(cwd, 'rev-list', '--count', '--since=@%d' % handoff, 'HEAD') or 0)
    except (OSError, ValueError):
        since = 0
    if since > 0:
        lines.append('HANDOFF.md last updated %s; %d commit(s) since — check git log for what changed '
                     'before relying on it.' % (_stamp(handoff), since))
    return lines


def session_start(raw):
    """Context for a SessionStart hook; empty unless the kit was initialized locally."""
    data = json.loads(raw) if raw.strip() else {}
    cwd = data.get('cwd') or os.getcwd()
    main = main_checkout(cwd)
    if main is None or not (ops_dir(main) / 'OPERATIONS.md').is_file() or not _set_up_locally(main):
        return ''
    ops = ops_dir(main)
    model = Path(os.environ.get('CLAUDE_PLUGIN_ROOT') or PLUGIN_ROOT) / 'references' / 'operating-model.md'
    return '\n'.join((
        'This project runs the Kimiflow operating kit. Its files live in the main checkout, also when '
        'this session runs in a worktree:',
        '- Binding rules, short form: ' + str(ops / 'OPERATIONS.md'),
        '- Reasons, incidents, flakes behind the rules: ' + str(ops / 'OPERATIONS-history.md'),
        '- Standing rules for workers, reviewers, auditors: ' + str(ops / 'rules-worker.md')
        + ' (workers read it; briefs link it)',
        '- Current state and next steps: ' + str(ops / 'HANDOFF.md'),
        '- The one list of open and deferred items: ' + str(ops / 'LIST.md'),
        '- Feature sheets, plans and briefs: ' + str(ops / 'plans') + '/',
        'Read OPERATIONS.md and then HANDOFF.md now. LIST.md and OPERATIONS-history.md grow long: '
        'grep them by ID, rule or test name, never read them whole.',
        'The operating rules apply to every task in this project. How much of the full flow (feature '
        'sheet, plan, audit, worker, review) a task needs depends on its risk; see ' + str(model) + '.',
        *_notices(cwd, ops),
    )) + '\n'


def git_guard(raw):
    """Block message for a Bash command (PreToolUse hook); empty unless blocked in a locally set-up kit."""
    data = json.loads(raw)
    command = data.get('tool_input', {}).get('command') or ''
    if BULK_ADD.search(command):
        message = ('Kimiflow git guard: stage named paths only; replace git add -A/--all/./:/ /* '
                   'with the paths you mean.')
    elif COMMIT.search(command) and ATTRIBUTION.search(command):
        message = ('Kimiflow git guard: no AI attribution in commits; remove the AI trailer or footer '
                   'line and commit again.')
    else:
        return ''  # the common case stays fast: no Git call
    main = main_checkout(data.get('cwd') or os.getcwd())
    if main is None or not (ops_dir(main) / 'OPERATIONS.md').is_file() or not _set_up_locally(main):
        return ''
    return message


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='copy the kit into .kimiflow/ops/').add_argument('--root', required=True)
    updater = commands.add_parser('update', help='compare the project kit with the shipped templates')
    updater.add_argument('--root', required=True)
    updater.add_argument('--apply-safe', action='store_true', help='copy new and safe files, never conflicts')
    updater.add_argument('--resolved', action='append', default=[], metavar='PATH',
                         help='record a hand-merged file (relative to .kimiflow/ops/) as in sync')
    commands.add_parser('session-start', help='print session context (SessionStart hook)')
    commands.add_parser('git-guard', help='block bulk staging and AI attribution (PreToolUse hook)')
    args = parser.parse_args()
    if args.command in ('init', 'update'):
        try:
            report = init(args.root) if args.command == 'init' else update(args.root, args.apply_safe, args.resolved)
            print(json.dumps(report, indent=2))
        except (OSError, ValueError) as error:
            parser.exit(1, 'kimiflow ' + args.command + ': ' + str(error) + '\n')
        return
    if args.command == 'git-guard':
        try:
            message = git_guard(sys.stdin.read())
        except Exception:
            message = ''
        if message:
            sys.stderr.write(message + '\n')
            sys.exit(2)
        return
    try:
        sys.stdout.write(session_start(sys.stdin.read()))
    except Exception:
        pass


if __name__ == '__main__':
    main()
