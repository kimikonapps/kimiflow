"""Resolve merge conflicts in the files OPERATIONS.md § 4 names as additive or regenerated.

Rule and reason: the installed references/operating-model.md § Integration. Run it in the checkout or
worktree where the merge stopped:

    python3 <main checkout>/.kimiflow/ops/tools/merge-additive.py [--operations <OPERATIONS.md>]

OPERATIONS.md lines it reads (backticks required; a glob is allowed):
    - MERGE ADDITIVE: `<path>` check `<command>`     both sides only add lines: keep both, check, stage
    - MERGE REGENERATE: `<path>` run `<command>`     the project rebuilds the file: run it, stage

An additive file is resolved only when every conflict hunk adds lines on both sides and removes or
changes none; otherwise it is refused, the hunk is listed and the file stays untouched. Files not
named stay untouched. Exit 0 when every conflict was resolved, 1 when any is left for the planner.
"""
import argparse
import fnmatch
from pathlib import Path
import re
import subprocess
import sys
import tempfile

LINE = re.compile(r'^- MERGE (ADDITIVE|REGENERATE): `([^`]+)` (?:check|run) `([^`]+)`', re.M)
SIZE = 40
OURS, BASE, SEP, THEIRS = ('<' * SIZE, '|' * SIZE, '=' * SIZE, '>' * SIZE)


def git(root, *args, check=True):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError('git ' + ' '.join(args) + ': ' + result.stderr.strip())
    return result


def rules(operations):
    return [(kind, pattern, command) for kind, pattern, command in LINE.findall(operations.read_text())
            if not pattern.startswith('<') and not command.startswith('<')]


def stage(root, path, number):
    result = subprocess.run(['git', 'show', ':%d:%s' % (number, path)], cwd=root, capture_output=True)
    return result.stdout if result.returncode == 0 else None


def three_way(root, path):
    """The file merged again from its index stages with diff3 markers, or None for a delete conflict."""
    base, ours, theirs = (stage(root, path, n) for n in (1, 2, 3))
    if ours is None or theirs is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        names = []
        for name, data in (('ours', ours), ('base', base or b''), ('theirs', theirs)):
            (Path(tmp) / name).write_bytes(data)
            names.append(str(Path(tmp) / name))
        result = subprocess.run(['git', 'merge-file', '-p', '--diff3', '--marker-size=%d' % SIZE,
                                 '-L', 'ours', '-L', 'base', '-L', 'theirs', *names],
                                capture_output=True, text=True)
    return result.stdout


def keep_both(merged):
    """Both sides of every hunk, ours first; or (None, hunk) for the first hunk that is no addition."""
    out, hunk, part = [], [], None
    for number, line in enumerate(merged.splitlines(keepends=True), 1):
        marker = line.rstrip('\n').split(' ')[0]
        if part is None and marker == OURS:
            part, hunk, start, kept = 'ours', [line], number, []
        elif part is None:
            out.append(line)
        elif marker == BASE and part == 'ours':
            part = 'base'
            hunk.append(line)
        elif marker == SEP and part == 'base':
            part = 'theirs'
            hunk.append(line)
        elif marker == THEIRS and part == 'theirs':
            out.extend(kept)
            part = None
        else:
            hunk.append(line)
            if part == 'base':
                return None, 'line %d:\n%s' % (start, ''.join(hunk))
            kept.append(line)
    return ''.join(out), None


def resolve(root, path, kind, command):
    """None when resolved and staged, otherwise the reason it was left."""
    target = root / path
    if kind == 'REGENERATE':
        result = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True)
        if result.returncode != 0:
            return 'regenerate command failed (%d): %s' % (result.returncode, result.stderr.strip())
        if OURS[:7] in target.read_text():
            return 'still holds conflict markers after the regenerate command'
        git(root, 'add', '--', path)
        return None
    merged = three_way(root, path)
    if merged is None:
        return 'deleted on one side, not an addition'
    text, refused = keep_both(merged)
    if refused:
        return 'a hunk changes or removes lines, ' + refused
    before = target.read_bytes()
    target.write_text(text)
    result = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True)
    if result.returncode != 0:
        target.write_bytes(before)
        return 'check command failed (%d), file restored: %s' % (result.returncode, result.stderr.strip())
    git(root, 'add', '--', path)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--operations', type=Path, default=Path(__file__).resolve().parent.parent / 'OPERATIONS.md')
    args = parser.parse_args(argv)
    root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').stdout.strip())
    listed = rules(args.operations)
    conflicted = [p for p in git(root, 'diff', '--name-only', '-z', '--diff-filter=U').stdout.split('\0') if p]
    left = 0
    for path in conflicted:
        match = next((rule for rule in listed if fnmatch.fnmatch(path, rule[1])), None)
        if match is None:
            print('LEFT ' + path + ': not named in OPERATIONS.md, resolve by hand')
            left += 1
            continue
        reason = resolve(root, path, match[0], match[2])
        if reason:
            print('REFUSED ' + path + ': ' + reason)
            left += 1
        else:
            print('RESOLVED ' + path + ' (' + match[0].lower() + ')')
    return 1 if left else 0


if __name__ == '__main__':
    sys.exit(main())
