"""Run explicit project checks and reject success against a changing worktree.

No model, network client, persistent state, staging, or inferred commands. This is
a verification helper, not a sandbox or an oracle for the quality of the checks.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time


class CheckError(ValueError):
    pass


def git(root, *args):
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        raise CheckError("Git inspection failed: " + result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def snapshot(root):
    """Bind HEAD, index, tracked bytes and nonignored untracked source files.

    Read actual files rather than trusting Git's cached stat/assume-unchanged
    bits. Ignored build output and untracked .kimiflow notes are not source.
    Gitlinks bind the nested checkout recursively, including its dirty files.
    """
    root = Path(root).resolve()
    digest = hashlib.sha256()

    def add(value):
        digest.update(str(len(value)).encode("ascii") + b":" + value)

    # Unborn branches are valid workspaces too.
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    add(head.stdout if head.returncode == 0 else b"unborn")
    index = git(root, "ls-files", "--stage", "-z")
    add(index)
    tracked = set()
    gitlinks = set()
    for entry in index.split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            tracked.add(name)
            if metadata.startswith(b"160000 "):
                gitlinks.add(name)
    untracked = set(git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))
    untracked.discard(b"")
    untracked = {name for name in untracked if name != b".kimiflow" and not name.startswith(b".kimiflow/")}
    for name in sorted(tracked | untracked):
        path = root / os.fsdecode(name)
        add(name)
        try:
            info = path.lstat()
        except FileNotFoundError:
            add(b"missing")
            continue
        # Do not follow a swapped parent directory outside the checkout.
        if not path.parent.resolve().is_relative_to(root):
            raise CheckError("Source parent escapes the worktree: " + os.fsdecode(name))
        add(str(stat.S_IFMT(info.st_mode)).encode("ascii"))
        add(str(info.st_mode & 0o111).encode("ascii"))
        if stat.S_ISLNK(info.st_mode):
            add(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            file_digest = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            with os.fdopen(os.open(path, flags), "rb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise CheckError("Source changed while fingerprinting: " + os.fsdecode(name))
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    file_digest.update(block)
                finished = os.fstat(handle.fileno())
                if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns):
                    raise CheckError("Source changed while fingerprinting: " + os.fsdecode(name))
            add(file_digest.digest())
        elif stat.S_ISDIR(info.st_mode) and name in gitlinks:
            # An uninitialized submodule has no independent Git directory.
            if (path / ".git").exists():
                add(snapshot(path.resolve()).encode("ascii"))
            else:
                add(b"uninitialized-gitlink")
        else:
            raise CheckError("Unsupported source file type: " + os.fsdecode(name))
    return digest.hexdigest()


def parse_check(value):
    try:
        argv = json.loads(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("check must be a JSON argv array") from exc
    if not isinstance(argv, list) or not argv or any(
        not isinstance(arg, str) or "\0" in arg for arg in argv
    ) or not argv[0]:
        raise argparse.ArgumentTypeError("check must be a nonempty array of strings")
    return argv


def stop_process(process):
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()
    process.wait()


def group_alive(process):
    if os.name != "posix":
        return False
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    return True


def execute(root, argv, timeout):
    started = time.monotonic()
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            argv, cwd=root, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, start_new_session=os.name == "posix",
        )
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_process(process)
        except BaseException:
            stop_process(process)
            raise
        background = not timed_out and group_alive(process)
        if background:
            # A successful launcher is not a completed check. Stop its remaining
            # process group before returning control or inspecting source again.
            stop_process(process)
        output.seek(0, os.SEEK_END)
        length = output.tell()
        output.seek(max(0, length - 4096))
        tail = output.read().decode("utf-8", "replace")
    result = {
        "argv": argv, "exit_code": process.returncode, "timed_out": timed_out,
        "seconds": round(time.monotonic() - started, 3),
    }
    # Successful logs stay out of model context. Failure tails are bounded.
    if background:
        result["background_processes"] = True
    if process.returncode or timed_out or background:
        result["output_tail"] = tail
        result["output_truncated"] = length > 4096
    return result


def check_change(root, checks, timeout=600):
    root = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel").removesuffix(b"\n"))).resolve()
    if not checks:
        raise CheckError("At least one explicit project check is required")
    before = snapshot(root)
    results = []
    for argv in checks:
        result = execute(root, argv, timeout)
        results.append(result)
        current = snapshot(root)
        if current != before:
            return {"status": "changed", "reason": "source_or_index_changed_during_checks", "checks": results}, 1
        if result["exit_code"] or result["timed_out"] or result.get("background_processes"):
            return {"status": "failed", "checks": results}, 1
    return {"status": "passed", "source_sha256": before, "checks": results}, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--check", action="append", required=True, type=parse_check,
                        help='explicit JSON argv, e.g. ["npm","test"]; repeat for multiple checks')
    parser.add_argument("--timeout", type=float, default=600, help="seconds per check (default: 600)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be a finite positive number")
    try:
        value, code = check_change(args.root, args.check, args.timeout)
    except (CheckError, OSError) as exc:
        value, code = {"status": "error", "reason": str(exc)}, 2
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
