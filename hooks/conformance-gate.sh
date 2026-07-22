#!/usr/bin/env bash
# kimiflow — adaptive implementation-conformance gate.
#
# Usage:
#   conformance-gate.sh <run-dir> --plan
#   conformance-gate.sh <run-dir> --record --write
#   conformance-gate.sh <run-dir>
#   conformance-gate.sh <run-dir> --finish
#
# The gate validates evidence and freshness only. It never executes a PLAN command.
set -u

if ! command -v python3 >/dev/null 2>&1; then
  printf 'CONFORMANCE_GATE\tCLOSED\tblockers=1\treason=malformed\tdetail=python3_missing\n'
  exit 0
fi

exec python3 - "$@" <<'PY'
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import PurePosixPath

try:
    import fcntl
except ImportError:  # pragma: no cover - Kimiflow's write mode is POSIX-only.
    fcntl = None


def emit(status, reason, details=None):
    details = list(dict.fromkeys(details or []))
    blockers = 0 if status == "OPEN" else max(1, len(details))
    print(
        "CONFORMANCE_GATE\t%s\tblockers=%s\treason=%s\tdetail=%s"
        % (status, blockers, reason, ",".join(details))
    )
    raise SystemExit(0)


def run(command, cwd=None, input_bytes=None):
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=input_bytes,
            check=False,
        )
    except OSError as exc:
        emit("CLOSED", "malformed", ["command_failed:%s" % exc.__class__.__name__])


def read_text(path):
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError):
        return None


def open_run_file(directory, name, writable=False):
    flags = os.O_RDWR if writable else os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            return None
        return descriptor
    except OSError:
        return None


def read_descriptor_text(descriptor):
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > 4 * 1024 * 1024:
                return None
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeError):
        return None


def read_run_text(directory, name):
    descriptor = open_run_file(directory, name)
    if descriptor is None:
        return None
    try:
        return read_descriptor_text(descriptor)
    finally:
        os.close(descriptor)


def plain_state_line(raw):
    line = raw.rstrip("\r\n").strip()
    if line.startswith("-"):
        line = line[1:].lstrip()
    return line.replace("**", "")


def state_values(lines, key):
    wanted = key.lower()
    values = []
    for raw in lines:
        line = plain_state_line(raw)
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip().lower() == wanted:
            values.append(value.strip())
    return values


def one_state(lines, key, errors, required=True):
    values = state_values(lines, key)
    code = key.lower().replace(" ", "_")
    if len(values) > 1:
        errors.append("%s_duplicate" % code)
        return ""
    if not values:
        if required:
            errors.append("%s_missing" % code)
        return ""
    return values[0]


def affected_paths(lines, errors):
    headers = []
    values = []
    for index, raw in enumerate(lines):
        line = plain_state_line(raw)
        if ":" not in line:
            continue
        name, inline = line.split(":", 1)
        if name.strip().lower() != "affected files":
            continue
        headers.append(index)
        if inline.strip():
            values.extend(part.strip() for part in inline.split(",") if part.strip())
            continue
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor].rstrip("\r\n")
            if not candidate.strip():
                cursor += 1
                continue
            normalized = plain_state_line(candidate)
            if re.match(r"^[A-Za-z][^:]{0,80}:\s*", normalized):
                break
            match = re.match(r"^\s*-\s+(.+?)\s*$", candidate)
            if not match:
                break
            values.append(match.group(1).strip().replace("**", ""))
            cursor += 1
    if len(headers) != 1:
        errors.append("affected_header_%s" % ("missing" if not headers else "duplicate"))
    if not values:
        errors.append("affected_files_empty")
    if len(values) != len(set(values)):
        errors.append("affected_files_duplicate")
    return values


def safe_relative_path(value):
    if not value or "\x00" in value or value.startswith("/") or "\\" in value:
        return False
    path = PurePosixPath(value)
    if value != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        return False
    return not (path.parts and path.parts[0] == ".kimiflow")


def artifact(path, code, errors, directory=None):
    value = read_run_text(directory, os.path.basename(path)) if directory is not None else read_text(path)
    if value is None:
        errors.append("%s_missing" % code)
        return ""
    if not value.strip():
        errors.append("%s_empty" % code)
    return value


def markdown_lines(text):
    lines = []
    fence_char = ""
    fence_len = 0
    html_comment = False
    for line in text.splitlines():
        marker_line = (
            not html_comment
            and line.startswith("<!-- kimiflow:")
            and line.endswith(" -->")
            and line.count("<!--") == 1
            and line.count("-->") == 1
        )
        cleaned = "" if html_comment else line
        if marker_line:
            cleaned = line
        else:
            rest = line
            chunks = []
            while rest:
                if html_comment:
                    end = rest.find("-->")
                    if end < 0:
                        rest = ""
                        break
                    html_comment = False
                    rest = rest[end + 3:]
                    continue
                start = rest.find("<!--")
                if start < 0:
                    chunks.append(rest)
                    break
                chunks.append(rest[:start])
                rest = rest[start + 4:]
                html_comment = True
            cleaned = "".join(chunks)
        if not fence_char and re.match(r"^(?: {4}|\t)", cleaned):
            continue
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", cleaned)
        if match:
            marker = match.group(1)
            if not fence_char:
                fence_char = marker[0]
                fence_len = len(marker)
                continue
            if marker[0] == fence_char and len(marker) >= fence_len and not cleaned[match.end():].strip():
                fence_char = ""
                fence_len = 0
                continue
        if not fence_char:
            lines.append(cleaned)
    return lines


def marker_attr(marker, name):
    match = re.search(r"(?:^|\s)%s=([A-Za-z0-9_-]+)(?:\s|$)" % re.escape(name), marker)
    return match.group(1).lower() if match else ""


def declared_acceptance_criteria(lines):
    declared = set()
    for index, line in enumerate(lines):
        plain = line.replace("**", "")
        heading = re.fullmatch(
            r"\s{0,3}#{1,6}\s+(AC-[0-9]+)(?:\s*(?:[-–—]{1,2}|:)\s+(\S.*))?\s*#*\s*",
            plain,
        )
        if heading:
            ident, inline = heading.groups()
            if inline:
                declared.add(ident)
                continue
            for candidate in lines[index + 1:]:
                candidate_plain = candidate.replace("**", "").strip()
                if re.match(r"^#{1,6}\s+", candidate_plain):
                    break
                if not candidate_plain:
                    continue
                if re.match(r"^AC-[0-9]+(?:\s|:|->)", candidate_plain):
                    break
                if re.match(
                    r"^(?:verification|trace|example|check|evidence|test|mapping)\s*:",
                    candidate_plain,
                    flags=re.IGNORECASE,
                ):
                    continue
                declared.add(ident)
                break
            continue
        direct = re.match(
            r"^\s{0,3}(?:[-*+]\s+)?(AC-[0-9]+)(?:\s*:\s*\S|\s+[-–—]{1,2}\s+\S|\s*->\s*[A-Za-z0-9_.-]+\s*:\s*\S)",
            plain,
        )
        if direct:
            declared.add(direct.group(1))
    return declared


def git_root(run_dir):
    proc = run(["git", "-C", run_dir, "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        emit("CLOSED", "malformed", ["git_root_missing"])
    return os.path.realpath(os.fsdecode(proc.stdout).strip())


def validate_intent_authority(root, run_dir, run_descriptor, state_contract, intent, requirements, errors):
    active_path = os.path.join(root, ".kimiflow", "session", "ACTIVE_RUN.json")
    active_text = read_text(active_path) if os.path.lexists(active_path) else None
    try:
        active = json.loads(active_text) if active_text is not None else None
    except json.JSONDecodeError:
        active = None
    expected_run = os.path.relpath(run_dir, root).replace(os.sep, "/")
    if not isinstance(active, dict) or active.get("run") != expected_run:
        if state_contract == "3":
            errors.append("active_intent_contract_mismatch")
        return
    pinned_contract = str(active.get("intent_contract") or "").strip()
    if pinned_contract:
        if pinned_contract != state_contract:
            errors.append("active_intent_contract_mismatch")
            return
    elif state_contract == "3":
        errors.append("active_intent_contract_mismatch")
        return
    if state_contract != "3":
        return

    lock_text = read_run_text(run_descriptor, "INTENT-LOCK.json")
    try:
        lock = json.loads(lock_text) if lock_text is not None else None
    except json.JSONDecodeError:
        lock = None
    if not isinstance(lock, dict):
        errors.append("intent_lock_invalid")
        return
    current_intent_digest = "sha256:" + hashlib.sha256(intent.encode("utf-8")).hexdigest()
    expected_keys = {"schema_version", "contract", "intent_digest", "requirements", "locked_at"}
    locked_at = str(lock.get("locked_at") or "")
    if (
        set(lock) != expected_keys
        or lock.get("schema_version") != 1
        or lock.get("contract") != 3
        or lock.get("intent_digest") != current_intent_digest
        or lock.get("requirements") != requirements
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", locked_at) is None
    ):
        errors.append("intent_lock_stale")
        return
    lock_digest = "sha256:" + hashlib.sha256(lock_text.encode("utf-8")).hexdigest()
    if str(active.get("intent_lock_digest") or "") != lock_digest:
        errors.append("intent_lock_basis_replaced")


def actual_delta(root, started_head, errors):
    verify = run(["git", "-C", root, "cat-file", "-e", "%s^{commit}" % started_head])
    if verify.returncode != 0:
        errors.append("run_started_head_invalid")
        return set()
    tracked_outputs = []
    for label, command in (
        ("head", ["git", "-C", root, "diff", "--name-only", "-z", "--no-renames", started_head, "HEAD", "--"]),
        ("index", ["git", "-C", root, "diff", "--cached", "--name-only", "-z", "--no-renames", started_head, "--"]),
        ("worktree", ["git", "-C", root, "diff", "--name-only", "-z", "--no-renames", started_head, "--"]),
    ):
        proc = run(command)
        if proc.returncode != 0:
            errors.append("git_%s_delta_failed" % label)
        else:
            tracked_outputs.append(proc.stdout)
    if errors:
        return set()
    untracked = run([
        "git", "-C", root, "ls-files", "--others", "--exclude-standard", "-z", "--"
    ])
    if untracked.returncode != 0:
        errors.append("git_untracked_failed")
        return set()
    result = {
        os.fsdecode(token)
        for token in (b"".join(tracked_outputs) + untracked.stdout).split(b"\0")
        if token
    }
    def module_paths(payload):
        if not payload:
            return set()
        config = run(
            ["git", "config", "-f", "-", "--get-regexp", r"^submodule\..*\.path$"],
            input_bytes=payload,
        )
        if config.returncode not in (0, 1):
            errors.append("gitmodules_parse_failed")
            return set()
        paths = set()
        for raw in os.fsdecode(config.stdout).splitlines():
            _key, separator, value = raw.partition(" ")
            if separator and value:
                paths.add(value)
        return paths

    modules_file = os.path.join(root, ".gitmodules")
    current_modules = b""
    if os.path.isfile(modules_file) and not os.path.islink(modules_file):
        try:
            with open(modules_file, "rb") as handle:
                current_modules = handle.read(1024 * 1024 + 1)
        except OSError:
            errors.append("gitmodules_read_failed")
        if len(current_modules) > 1024 * 1024:
            errors.append("gitmodules_too_large")
            current_modules = b""
    declared_gitlinks = module_paths(current_modules)
    baseline_modules = run(["git", "-C", root, "show", "%s:.gitmodules" % started_head])
    baseline_gitlinks = module_paths(baseline_modules.stdout if baseline_modules.returncode == 0 else b"")
    index = run(["git", "-C", root, "ls-files", "--stage", "-z"])
    if index.returncode != 0:
        errors.append("git_index_scan_failed")
    else:
        for record in index.stdout.split(b"\0"):
            if not record or b"\t" not in record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            parts = metadata.split()
            rel = os.fsdecode(raw_path)
            if len(parts) == 3 and parts[0] == b"160000" and not os.path.lexists(os.path.join(root, rel)):
                baseline = run(["git", "-C", root, "ls-tree", "-z", started_head, "--", rel])
                baseline_parts = baseline.stdout.split(b"\t", 1)[0].split() if baseline.returncode == 0 else []
                unchanged = (
                    len(baseline_parts) >= 3
                    and baseline_parts[0] == b"160000"
                    and baseline_parts[2].lower() == parts[1].lower()
                )
                if rel in baseline_gitlinks and rel not in declared_gitlinks and ".gitmodules" in result:
                    result.add(rel)
                elif unchanged:
                    result.discard(rel)
    return result


def add_hash(digest, label, payload):
    label_bytes = label.encode("utf-8", "surrogateescape")
    digest.update(len(label_bytes).to_bytes(8, "big"))
    digest.update(label_bytes)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)


def index_entry(root, rel, errors):
    proc = run(["git", "-C", root, "ls-files", "--stage", "-z", "--", rel])
    if proc.returncode != 0:
        errors.append("affected_index_failed:%s" % rel)
        return ()
    exact = []
    for record in proc.stdout.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        if os.fsdecode(raw_path) == rel:
            exact.append(metadata.split())
    if not exact:
        return None
    if len(exact) != 1 or len(exact[0]) != 3 or exact[0][2] != b"0":
        errors.append("affected_index_invalid:%s" % rel)
        return ()
    return exact[0][0], exact[0][1].lower()


def require_index_match(rel, expected_mode, worktree_oid, entry, errors):
    if entry is None or entry == () or entry[0] != expected_mode or entry[1] != worktree_oid:
        errors.append("affected_index_worktree_mismatch:%s" % rel)
        return False
    return True


def gitlink_payload(root, rel, path, entry, errors):
    if entry is None or entry == () or entry[0] != b"160000":
        return None
    oid = entry[1]
    top = run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    if top.returncode == 0 and os.path.realpath(os.fsdecode(top.stdout).strip()) == os.path.realpath(path):
        head = run(["git", "-C", path, "rev-parse", "--verify", "HEAD^{commit}"])
        if head.returncode != 0:
            errors.append("affected_gitlink_head_invalid:%s" % rel)
            return b""
        candidate = head.stdout.strip().lower()
        if not re.fullmatch(rb"[0-9a-f]{40,64}", candidate):
            errors.append("affected_gitlink_head_invalid:%s" % rel)
            return b""
        dirty = run(["git", "-C", path, "status", "--porcelain=v1", "--untracked-files=all"])
        if dirty.returncode != 0:
            errors.append("affected_gitlink_status_failed:%s" % rel)
            return b""
        if dirty.stdout:
            errors.append("affected_gitlink_dirty:%s" % rel)
            return b""
        oid = candidate
    return b"gitlink\0" + oid


def content_basis(root, started_head, mode, affected, artifacts, errors):
    digest = hashlib.sha256()
    add_hash(digest, "contract", b"kimiflow-conformance-1")
    add_hash(digest, "started_head", started_head.encode("ascii", "strict"))
    add_hash(digest, "mode", mode.encode("ascii", "strict"))
    for name, text in artifacts:
        add_hash(digest, "artifact:%s" % name, text.encode("utf-8"))
    for rel in sorted(affected):
        path = os.path.join(root, *PurePosixPath(rel).parts)
        entry = index_entry(root, rel, errors)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            add_hash(digest, "path:%s" % rel, b"missing")
            continue
        except OSError:
            errors.append("affected_path_unreadable:%s" % rel)
            continue
        if stat.S_ISLNK(info.st_mode):
            try:
                target = os.fsencode(os.readlink(path))
            except OSError:
                errors.append("affected_symlink_unreadable:%s" % rel)
                continue
            payload = b"symlink\0" + target
            add_hash(digest, "path:%s" % rel, payload)
        elif stat.S_ISREG(info.st_mode):
            file_digest = hashlib.sha256()
            try:
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        file_digest.update(chunk)
            except OSError:
                errors.append("affected_path_unreadable:%s" % rel)
                continue
            payload = b"file\0" + oct(stat.S_IMODE(info.st_mode)).encode("ascii") + b"\0" + file_digest.digest()
            add_hash(digest, "path:%s" % rel, payload)
        elif stat.S_ISDIR(info.st_mode):
            payload = gitlink_payload(root, rel, path, entry, errors)
            if payload is None:
                errors.append("affected_path_not_file:%s" % rel)
            elif payload:
                add_hash(digest, "path:%s" % rel, payload)
        else:
            errors.append("affected_path_not_file:%s" % rel)
    return digest.hexdigest()


def require_delivered_tree(root, affected, errors):
    cached = run(["git", "-C", root, "diff", "--cached", "--quiet", "HEAD", "--", *affected])
    if cached.returncode not in (0, 1):
        errors.append("delivery_index_check_failed")
    elif cached.returncode == 1:
        errors.append("delivery_head_index_mismatch")
    for rel in affected:
        path = os.path.join(root, *PurePosixPath(rel).parts)
        entry = index_entry(root, rel, errors)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if entry is not None:
                errors.append("affected_index_worktree_mismatch:%s" % rel)
            continue
        except OSError:
            errors.append("affected_path_unreadable:%s" % rel)
            continue
        if stat.S_ISLNK(info.st_mode):
            try:
                target = os.fsencode(os.readlink(path))
            except OSError:
                errors.append("affected_symlink_unreadable:%s" % rel)
                continue
            oid_proc = run(["git", "-C", root, "hash-object", "--stdin"], input_bytes=target)
            oid = oid_proc.stdout.strip().lower()
            if oid_proc.returncode != 0:
                errors.append("affected_hash_failed:%s" % rel)
                continue
            require_index_match(rel, b"120000", oid, entry, errors)
        elif stat.S_ISREG(info.st_mode):
            oid_proc = run(["git", "-C", root, "hash-object", "--path", rel, "--", rel])
            oid = oid_proc.stdout.strip().lower()
            expected_mode = b"100755" if stat.S_IMODE(info.st_mode) & 0o111 else b"100644"
            if oid_proc.returncode != 0:
                errors.append("affected_hash_failed:%s" % rel)
                continue
            require_index_match(rel, expected_mode, oid, entry, errors)
        elif stat.S_ISDIR(info.st_mode):
            payload = gitlink_payload(root, rel, path, entry, errors)
            if entry is None or entry == () or entry[0] != b"160000":
                errors.append("affected_index_worktree_mismatch:%s" % rel)
            elif payload and payload != b"gitlink\0" + entry[1]:
                errors.append("affected_index_worktree_mismatch:%s" % rel)
        else:
            errors.append("affected_path_not_file:%s" % rel)


def write_basis(directory, state_descriptor, original_state, lines, basis, state_identity):
    if fcntl is None:
        emit("CLOSED", "malformed", ["conformance_basis_lock_unavailable"])
    matches = []
    for index, raw in enumerate(lines):
        line = plain_state_line(raw)
        if ":" in line and line.split(":", 1)[0].strip().lower() == "conformance basis":
            matches.append(index)
    if len(matches) != 1:
        emit("CLOSED", "malformed", ["conformance_basis_%s" % ("missing" if not matches else "duplicate")])
    index = matches[0]
    raw = lines[index]
    newline = "\n" if raw.endswith("\n") else ""
    prefix = raw.split(":", 1)[0]
    lines[index] = "%s: %s%s" % (prefix, basis, newline)
    temp_descriptor = None
    temp_name = ".STATE.md.%s.tmp" % os.urandom(16).hex()
    try:
        fcntl.flock(state_descriptor, fcntl.LOCK_EX)
        current_state = os.fstat(state_descriptor)
        named_state = os.stat("STATE.md", dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(current_state.st_mode) or (
            current_state.st_dev,
            current_state.st_ino,
        ) != state_identity or (named_state.st_dev, named_state.st_ino) != state_identity:
            raise OSError("STATE.md changed")
        if read_descriptor_text(state_descriptor) != original_state:
            raise OSError("STATE.md content changed")
        mode = stat.S_IMODE(current_state.st_mode)
        temp_descriptor = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
            dir_fd=directory,
        )
        with os.fdopen(temp_descriptor, "w", encoding="utf-8") as handle:
            temp_descriptor = None
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        named_state = os.stat("STATE.md", dir_fd=directory, follow_symlinks=False)
        if (named_state.st_dev, named_state.st_ino) != state_identity or read_descriptor_text(state_descriptor) != original_state:
            raise OSError("STATE.md changed during write")
        os.replace(temp_name, "STATE.md", src_dir_fd=directory, dst_dir_fd=directory)
        temp_name = ""
        os.fsync(directory)
    except OSError:
        emit("CLOSED", "malformed", ["conformance_basis_write_failed"])
    finally:
        if temp_descriptor is not None:
            os.close(temp_descriptor)
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=directory)
            except OSError:
                pass
        fcntl.flock(state_descriptor, fcntl.LOCK_UN)


args = sys.argv[1:]
run_dir = ""
plan_only = False
record = False
write = False
finish = False
for arg in args:
    if arg == "--plan":
        plan_only = True
    elif arg == "--record":
        record = True
    elif arg == "--write":
        write = True
    elif arg == "--finish":
        finish = True
    elif arg in ("--pretty",):
        continue
    elif arg.startswith("-"):
        emit("CLOSED", "malformed", ["unknown_option"])
    elif not run_dir:
        run_dir = arg
    else:
        emit("CLOSED", "malformed", ["extra_argument"])

if not run_dir:
    emit("CLOSED", "malformed", ["missing_run_dir"])
run_dir = os.path.abspath(run_dir)
if not os.path.isdir(run_dir) or os.path.islink(run_dir):
    emit("CLOSED", "malformed", ["run_dir_missing"])
run_dir = os.path.realpath(run_dir)
if plan_only and (record or finish):
    emit("CLOSED", "malformed", ["mode_conflict"])
if record and finish:
    emit("CLOSED", "malformed", ["mode_conflict"])
if write and not record:
    emit("CLOSED", "malformed", ["write_requires_record"])

state_path = os.path.join(run_dir, "STATE.md")
try:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    run_descriptor = os.open(run_dir, directory_flags)
    run_info = os.fstat(run_descriptor)
    named_run = os.stat(run_dir, follow_symlinks=False)
    if not stat.S_ISDIR(run_info.st_mode) or (run_info.st_dev, run_info.st_ino) != (
        named_run.st_dev,
        named_run.st_ino,
    ):
        raise OSError("run directory changed")
    state_descriptor = open_run_file(run_descriptor, "STATE.md", writable=record and write)
    if state_descriptor is None:
        raise OSError("STATE.md missing")
    state_info = os.fstat(state_descriptor)
    named_state = os.stat("STATE.md", dir_fd=run_descriptor, follow_symlinks=False)
    if (state_info.st_dev, state_info.st_ino) != (named_state.st_dev, named_state.st_ino):
        raise OSError("STATE.md changed")
    state_identity = (state_info.st_dev, state_info.st_ino)
except OSError:
    emit("CLOSED", "malformed", ["state_missing"])
state_text = read_descriptor_text(state_descriptor)
if state_text is None:
    emit("CLOSED", "malformed", ["state_missing"])
state_lines = state_text.splitlines(keepends=True)
contract_values = state_values(state_lines, "Conformance contract")
if not contract_values:
    root_probe = run(["git", "-C", os.path.dirname(os.path.dirname(run_dir)), "rev-parse", "--show-toplevel"])
    if root_probe.returncode == 0:
        root_probe_path = os.path.realpath(os.fsdecode(root_probe.stdout).strip())
        active_probe_path = os.path.join(root_probe_path, ".kimiflow", "session", "ACTIVE_RUN.json")
        if os.path.lexists(active_probe_path):
            active_probe_text = read_text(active_probe_path)
            try:
                active_probe = json.loads(active_probe_text) if active_probe_text is not None else None
            except json.JSONDecodeError:
                active_probe = None
            expected_probe_run = os.path.relpath(run_dir, root_probe_path).replace(os.sep, "/")
            if (
                isinstance(active_probe, dict)
                and active_probe.get("run") == expected_probe_run
                and str(active_probe.get("conformance_contract") or "").strip()
            ):
                emit("CLOSED", "malformed", ["active_conformance_contract_mismatch"])
    emit("OPEN", "not-required", ["conformance_contract_absent"])
if len(contract_values) != 1:
    emit("CLOSED", "malformed", ["conformance_contract_duplicate"])
if contract_values[0].strip() != "1":
    emit("CLOSED", "malformed", ["conformance_contract_invalid"])

errors = []
schema = one_state(state_lines, "Flow schema", errors)
mode = one_state(state_lines, "Mode", errors).lower().split(" ", 1)[0]
scope = one_state(state_lines, "Scope", errors).lower().split(" ", 1)[0]
basis = one_state(state_lines, "Conformance basis", errors)
started_head = one_state(state_lines, "Run started head", errors, required=not plan_only)
architecture_state = one_state(state_lines, "Architecture deliberation", errors, required=False).lower().split(" ", 1)[0]
intent_contract = one_state(state_lines, "Intent contract", errors, required=False).strip()
affected = affected_paths(state_lines, errors)
if schema != "4":
    errors.append("flow_schema_invalid")
if mode not in ("feature", "fix"):
    errors.append("conformance_mode_invalid")
if scope not in ("small", "large"):
    errors.append("conformance_scope_invalid")
if architecture_state not in ("", "active", "off"):
    errors.append("architecture_deliberation_unresolved")
if basis != "pending" and not re.fullmatch(r"[0-9a-f]{64}", basis):
    errors.append("conformance_basis_invalid")
for rel in affected:
    if not safe_relative_path(rel):
        errors.append("affected_path_invalid:%s" % rel)

plan_path = os.path.join(run_dir, "PLAN.md")
acceptance_path = os.path.join(run_dir, "ACCEPTANCE.md")
evidence_name = "RESEARCH.md" if mode == "feature" else "DIAGNOSIS.md"
plan = artifact(plan_path, "plan", errors, directory=run_descriptor)
acceptance = artifact(acceptance_path, "acceptance", errors, directory=run_descriptor)
evidence = artifact(os.path.join(run_dir, evidence_name), evidence_name.lower(), errors, directory=run_descriptor)
intent_name = "INTENT.md" if mode == "feature" else "PROBLEM.md"
intent = artifact(os.path.join(run_dir, intent_name), intent_name.lower(), errors, directory=run_descriptor)
plan_lines = markdown_lines(plan)
acceptance_lines = markdown_lines(acceptance)
evidence_lines = markdown_lines(evidence)
acceptance_criteria = declared_acceptance_criteria(acceptance_lines)
requirements = []
if mode == "feature" and intent_contract == "3":
    requirements = re.findall(r"^Requirement (R[1-9][0-9]*):\s*\S.+$", intent, flags=re.MULTILINE)
    expected_requirements = ["R%s" % ident for ident in range(1, len(requirements) + 1)]
    if not requirements or len(requirements) > 20 or requirements != expected_requirements:
        errors.append("intent_requirements_invalid")
    trace_rows = {}
    for line in acceptance_lines:
        match = re.fullmatch(r"Requirement trace (R[1-9][0-9]*): (AC-[0-9]+)", line)
        if match:
            trace_rows.setdefault(match.group(1), []).append(match.group(2))
    for requirement in requirements:
        values = trace_rows.get(requirement, [])
        if len(values) != 1:
            errors.append("requirement_trace_%s_%s" % (requirement, "missing" if not values else "duplicate"))
        elif values[0] not in acceptance_criteria:
            errors.append("requirement_trace_%s_ac_missing" % requirement)
    unexpected_traces = sorted(set(trace_rows) - set(requirements))
    if unexpected_traces:
        errors.append("requirement_trace_%s_unexpected" % unexpected_traces[0])
root = None
root_probe = run(["git", "-C", os.path.dirname(os.path.dirname(run_dir)), "rev-parse", "--show-toplevel"])
if root_probe.returncode == 0:
    root = os.path.realpath(os.fsdecode(root_probe.stdout).strip())
    validate_intent_authority(root, run_dir, run_descriptor, intent_contract, intent, requirements, errors)
elif intent_contract == "3":
    errors.append("git_root_missing")
evidence_headings = {
    match.group(1).strip()
    for line in evidence_lines
    for match in [re.fullmatch(r"\s{0,3}#{1,6}\s+(.+?)\s*#*\s*", line)]
    if match
}
marker_lines = [line for line in plan_lines if line.startswith("<!-- kimiflow:decision-contract ")]
decision_count = 0
if len(marker_lines) != 1:
    errors.append("decision_marker_%s" % ("missing" if not marker_lines else "duplicate"))
else:
    match = re.fullmatch(r"<!-- kimiflow:decision-contract contract=1 decisions=([0-9]+) -->", marker_lines[0])
    if not match:
        errors.append("decision_marker_malformed")
    else:
        decision_count = int(match.group(1))
        if decision_count < 1 or decision_count > 5:
            errors.append("decision_count_invalid")

labels = ("Decision", "Evidence", "Invariant", "Paths", "AC", "Check", "Recheck")
decision_rows = {}
all_field_ids = []
for line in plan_lines:
    match = re.match(r"^(Decision|Evidence|Invariant|Paths|AC|Check|Recheck) D([0-9]+):(?: (.*))?$", line)
    if match:
        all_field_ids.append(int(match.group(2)))
if decision_count:
    for ident in range(1, decision_count + 1):
        row = {}
        for label in labels:
            pattern = re.compile(r"^%s D%s:(?: (.*))?$" % (label, ident))
            matches = [pattern.fullmatch(line) for line in plan_lines]
            values = [(match.group(1) or "").strip() for match in matches if match]
            code = "%s_D%s" % (label.lower(), ident)
            if len(values) != 1:
                errors.append("%s_%s" % (code, "missing" if not values else "duplicate"))
                row[label] = ""
            elif not values[0]:
                errors.append("%s_empty" % code)
                row[label] = ""
            else:
                row[label] = values[0]
        decision_rows[ident] = row
        evidence_match = re.fullmatch(r"%s §(.+)" % re.escape(evidence_name), row.get("Evidence", ""))
        if not evidence_match:
            errors.append("evidence_D%s_source_mismatch" % ident)
        elif evidence_match.group(1).strip() not in evidence_headings:
            errors.append("evidence_D%s_section_missing" % ident)
        paths = [part.strip() for part in row.get("Paths", "").split(",") if part.strip()]
        if not paths:
            errors.append("paths_D%s_empty" % ident)
        if len(paths) != len(set(paths)):
            errors.append("paths_D%s_duplicate" % ident)
        for rel in paths:
            if not safe_relative_path(rel):
                errors.append("path_D%s_invalid" % ident)
            elif rel not in affected:
                errors.append("path_D%s_not_affected" % ident)
        ac = row.get("AC", "")
        if not re.fullmatch(r"AC-[0-9]+", ac):
            errors.append("ac_D%s_invalid" % ident)
        elif ac not in acceptance_criteria:
            errors.append("ac_D%s_missing" % ident)
        check = row.get("Check", "")
        if not re.fullmatch(r"(?:command|verifier) :: .+", check):
            errors.append("check_D%s_invalid" % ident)
    expected_ids = set(range(1, decision_count + 1))
    unexpected = sorted(set(all_field_ids) - expected_ids)
    if unexpected:
        errors.append("decision_id_unexpected:D%s" % unexpected[0])

if errors:
    emit("CLOSED", "plan-contract", errors)
if plan_only:
    emit("OPEN", "plan-clean", [])
if root is None:
    root = git_root(os.path.dirname(os.path.dirname(run_dir)))

verification_path = os.path.join(run_dir, "VERIFICATION.md")
verification = artifact(verification_path, "verification", errors, directory=run_descriptor)
verification_lines = markdown_lines(verification)
if mode == "feature" and intent_contract == "3":
    requirement_checks = {}
    for line in verification_lines:
        match = re.fullmatch(r"Requirement (R[1-9][0-9]*): (passed|failed) :: (\S.*)", line)
        if match:
            requirement_checks.setdefault(match.group(1), []).append((match.group(2), match.group(3)))
    for requirement in requirements:
        values = requirement_checks.get(requirement, [])
        if len(values) != 1:
            errors.append("requirement_check_%s_%s" % (requirement, "missing" if not values else "duplicate"))
        elif values[0][0] != "passed":
            errors.append("requirement_check_%s_not_passed" % requirement)
    unexpected_requirement_checks = sorted(set(requirement_checks) - set(requirements))
    if unexpected_requirement_checks:
        errors.append("requirement_check_%s_unexpected" % unexpected_requirement_checks[0])
generic = [line for line in verification_lines if line.startswith("<!-- kimiflow:verification ")]
generic_match = None
if len(generic) != 1:
    errors.append("verification_marker_%s" % ("missing" if not generic else "duplicate"))
else:
    generic_match = re.fullmatch(
        r"<!-- kimiflow:verification outcome=(passed|failed) "
        r"criteria=(passed|failed|not_run) regression=(passed|failed|not_run) -->",
        generic[0],
    )
    if not generic_match:
        errors.append("verification_marker_malformed")

receipts = [line for line in verification_lines if line.startswith("<!-- kimiflow:conformance ")]
receipt = receipts[0] if len(receipts) == 1 else ""
if len(receipts) != 1:
    errors.append("conformance_receipt_%s" % ("missing" if not receipts else "duplicate"))
receipt_match = re.fullmatch(
    r"<!-- kimiflow:conformance contract=1 "
    r"status=(converged|code_gap|strategy_drift|architecture_falsified|research_stale|scope_drift) "
    r"diff=(passed|failed) strategy=(passed|failed) architecture=(passed|failed|not_applicable) "
    r"research=(stable|stale|not_applicable) scope=(passed|failed) decisions=([0-9]+) checks=([0-9]+) "
    r"verifier=(folded|independent) source=(current-run) -->",
    receipt,
)
if receipt and not receipt_match:
    errors.append("conformance_receipt_malformed")
if errors:
    emit("CLOSED", "receipt-contract", errors)

status, diff_status, strategy_status, architecture_status, research_status, scope_status, receipt_decisions, receipt_checks, verifier, _source = receipt_match.groups()
if status == "converged" and generic[0] != "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->":
    errors.append("verification_not_passed")
if status != "converged" and generic_match.group(1) != "failed":
    errors.append("verification_failure_receipt_required")
required_failure = {
    "code_gap": diff_status == "failed",
    "scope_drift": scope_status == "failed",
    "strategy_drift": strategy_status == "failed",
    "architecture_falsified": architecture_status == "failed",
    "research_stale": research_status == "stale",
}.get(status, True)
if not required_failure:
    errors.append("conformance_status_inconsistent")
if status == "architecture_falsified" and architecture_state != "active":
    errors.append("architecture_status_inconsistent")
route = {
    "code_gap": "route_phase_5",
    "scope_drift": "route_phase_5",
    "strategy_drift": "route_phase_2",
    "architecture_falsified": "route_phase_2",
    "research_stale": "route_phase_2",
}.get(status)
expected_architecture = "passed" if architecture_state == "active" else "not_applicable"
if architecture_state == "active" and architecture_status not in ("passed", "failed"):
    errors.append("architecture_status_mismatch")
if architecture_state != "active" and architecture_status != "not_applicable":
    errors.append("architecture_status_mismatch")
phase_2_failure = (
    strategy_status == "failed"
    or (architecture_state == "active" and architecture_status == "failed")
    or research_status == "stale"
)
if status in ("code_gap", "scope_drift") and phase_2_failure:
    errors.append("conformance_phase_2_precedence")
if status == "converged":
    if diff_status != "passed":
        errors.append("diff_not_passed")
    if strategy_status != "passed":
        errors.append("strategy_not_passed")
    if architecture_status != expected_architecture:
        errors.append("architecture_status_mismatch")
    if research_status not in ("stable", "not_applicable"):
        errors.append("research_not_stable")
    if scope_status != "passed":
        errors.append("scope_not_passed")
if int(receipt_decisions) != decision_count or int(receipt_checks) != decision_count:
    errors.append("decision_check_count_mismatch")
expected_verifier = "folded" if scope == "small" else "independent"
if verifier != expected_verifier:
    errors.append("verifier_scope_mismatch")

check_lines = {}
for line in verification_lines:
    match = re.fullmatch(r"Decision check D([0-9]+): (passed|failed) :: (.+)", line)
    if match:
        ident = int(match.group(1))
        check_lines.setdefault(ident, []).append((match.group(2), match.group(3).strip()))
for ident in range(1, decision_count + 1):
    values = check_lines.get(ident, [])
    if len(values) != 1:
        errors.append("check_D%s_%s" % (ident, "missing" if not values else "duplicate"))
        continue
    planned = decision_rows[ident]["Check"].split(" :: ", 1)[1]
    check_status, check_method = values[0]
    if check_method != planned:
        errors.append("check_D%s_mismatch" % ident)
    if status == "converged" and check_status != "passed":
        errors.append("check_D%s_not_passed" % ident)
unexpected_checks = sorted(set(check_lines) - set(range(1, decision_count + 1)))
if unexpected_checks:
    errors.append("check_D%s_unexpected" % unexpected_checks[0])

phase6 = one_state(state_lines, "Phase 6", errors).lower().split(" ", 1)[0]
if status != "converged":
    if phase6 != "in-progress":
        errors.append("phase_6_status_invalid")
elif record:
    if phase6 != "in-progress":
        errors.append("phase_6_status_invalid")
elif phase6 != "done":
    errors.append("phase_6_not_done")

expected_run_parent = os.path.join(root, ".kimiflow") + os.sep
if not (run_dir + os.sep).startswith(expected_run_parent):
    errors.append("run_dir_outside_kimiflow")
active_path = os.path.join(root, ".kimiflow", "session", "ACTIVE_RUN.json")
if os.path.lexists(active_path):
    active_text = read_text(active_path)
    try:
        active = json.loads(active_text) if active_text is not None else None
    except json.JSONDecodeError:
        active = None
    if not isinstance(active, dict):
        errors.append("active_run_malformed")
    else:
        expected_run = os.path.relpath(run_dir, root).replace(os.sep, "/")
        selectors = {
            "run": expected_run,
            "mode": mode,
            "scope": scope,
            "started_head": started_head,
        }
        if "conformance_contract" in active:
            selectors["conformance_contract"] = contract_values[0].strip()
        for key, expected in selectors.items():
            if str(active.get(key) or "").strip() != expected:
                errors.append("active_%s_mismatch" % key)
        active_device = active.get("run_device")
        active_inode = active.get("run_inode")
        if active_device is not None and active_inode is not None and (
            active_device,
            active_inode,
        ) != (run_info.st_dev, run_info.st_ino):
            errors.append("active_run_identity_mismatch")
delta = actual_delta(root, started_head, errors)
if set(affected) != delta:
    errors.append("affected_files_mismatch")
for ident, row in decision_rows.items():
    for rel in [part.strip() for part in row["Paths"].split(",") if part.strip()]:
        if rel not in delta:
            errors.append("path_D%s_not_in_delta" % ident)

if errors:
    emit("CLOSED", "conformance-blockers", errors)
if status != "converged":
    emit("CLOSED", "conformance-drift", ["conformance_status_%s" % status, route])

expected_basis = content_basis(
    root,
    started_head,
    mode,
    affected,
    [
        (intent_name, intent),
        (evidence_name, evidence),
        ("ACCEPTANCE.md", acceptance),
        ("PLAN.md", plan),
        ("VERIFICATION.md", verification),
    ],
    errors,
)
if errors:
    emit("CLOSED", "conformance-blockers", errors)
if record:
    if write:
        write_basis(run_descriptor, state_descriptor, state_text, state_lines, expected_basis, state_identity)
        emit("OPEN", "basis-recorded", ["basis=%s" % expected_basis])
    emit("OPEN", "basis-preview", ["basis=%s" % expected_basis])
if basis != expected_basis:
    emit("CLOSED", "stale", ["conformance_basis_stale"])
if finish:
    require_delivered_tree(root, affected, errors)
    if errors:
        emit("CLOSED", "delivery-blockers", errors)
emit("OPEN", "clean", ["basis=%s" % expected_basis])
PY
