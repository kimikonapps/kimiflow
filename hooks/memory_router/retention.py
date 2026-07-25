"""Safe local retention for inactive terminal Kimiflow run artifacts."""

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import time

from . import contracts, store
from .cli import die, resolve_root


class RetentionError(ValueError):
    pass


ARCHIVE_DIR = ".kimiflow/archive"
MANIFEST_NAME = "RETENTION-MANIFEST.json"
MAX_FILES = 10000
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MIN_AGE_DAYS = 30
DEFAULT_KEEP = 20
TERMINAL = {"done", "failed", "aborted"}
RESERVED = {"project", "session", "archive", "programs", "plans", "specs"}
RECOVERY_RE = re.compile(r"^\.(?P<slug>.+)\.retention\.(?P<archive_id>[0-9a-f]{16})$")
STUB_FILES = {
    "STATE.md", "INTENT.md", "PROBLEM.md", "SESSION-OUTCOME.json",
    "LEARNING-REVIEW.md",
}


def _sha_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _state_value(path, key):
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith(key.lower() + ":"):
                    return line.split(":", 1)[1].strip().lower().split(" ", 1)[0]
    except (OSError, UnicodeError):
        return ""
    return ""


def _terminal(run_dir):
    state = os.path.join(run_dir, "STATE.md")
    status = _state_value(state, "Status")
    return status if status in TERMINAL else ""


def _active_run(root):
    value = store.read_json(os.path.join(root, ".kimiflow", "session", "ACTIVE_RUN.json"))
    if not isinstance(value, dict) or value.get("status") != "active":
        return ""
    return value.get("run") if isinstance(value.get("run"), str) else ""


def _candidate_runs(root):
    base = os.path.join(root, ".kimiflow")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    rows = []
    active = _active_run(root)
    for name in names:
        if name in RESERVED or name.startswith(".") or "/" in name or name in (".", ".."):
            continue
        path = os.path.join(base, name)
        try:
            info = os.stat(path, follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            continue
        rel = ".kimiflow/%s" % name
        terminal = _terminal(path)
        if not terminal or os.path.lexists(os.path.join(path, "ARCHIVED.md")):
            continue
        rows.append({
            "slug": name,
            "run": rel,
            "path": path,
            "terminal_status": terminal,
            "modified_at": int(info.st_mtime),
            "active": rel == active,
        })
    return rows


def eligibility(root, min_age_days=DEFAULT_MIN_AGE_DAYS, keep=DEFAULT_KEEP, now=None):
    if (
        isinstance(min_age_days, bool)
        or not isinstance(min_age_days, int)
        or not 1 <= min_age_days <= 3650
        or isinstance(keep, bool)
        or not isinstance(keep, int)
        or not 0 <= keep <= 10000
    ):
        raise RetentionError("retention_policy_invalid")
    now = int(time.time() if now is None else now)
    cutoff = now - min_age_days * 86400
    rows = sorted(_candidate_runs(root), key=lambda row: (-row["modified_at"], row["slug"]))
    protected = {row["slug"] for row in rows[:keep]}
    result = []
    for row in rows:
        reason = (
            "active"
            if row["active"]
            else "protected_newest"
            if row["slug"] in protected
            else "too_recent"
            if row["modified_at"] > cutoff
            else "eligible"
        )
        result.append({key: value for key, value in row.items() if key != "path"} | {"reason": reason})
    return {
        "schema_version": 1,
        "status": "preview",
        "min_age_days": min_age_days,
        "keep": keep,
        "eligible_count": sum(row["reason"] == "eligible" for row in result),
        "runs": result,
    }


def _inventory(run_dir):
    rows = []
    total = 0
    for current, dirs, files in os.walk(run_dir, topdown=True, followlinks=False):
        dirs.sort()
        files.sort()
        for name in list(dirs):
            path = os.path.join(current, name)
            info = os.stat(path, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise RetentionError("retention_unsafe_entry")
        for name in files:
            path = os.path.join(current, name)
            info = os.stat(path, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise RetentionError("retention_unsafe_entry")
            if info.st_size > MAX_FILE_BYTES:
                raise RetentionError("retention_file_oversize")
            total += info.st_size
            if total > MAX_TOTAL_BYTES or len(rows) >= MAX_FILES:
                raise RetentionError("retention_run_oversize")
            rel = os.path.relpath(path, run_dir).replace(os.sep, "/")
            rows.append({"path": rel, "size": info.st_size, "sha256": _sha_file(path)})
    return rows


def _verify_archive(path, manifest):
    expected = {row["path"]: row for row in manifest["files"]}
    seen = {}
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        anchor = store._active_anchor(path)
        descriptor = (
            os.open(os.path.basename(path), flags, dir_fd=anchor["descriptor"])
            if anchor is not None
            else os.open(path, flags)
        )
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise RetentionError("retention_archive_invalid")
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                for member in archive.getmembers():
                    if member.name == MANIFEST_NAME:
                        payload = archive.extractfile(member).read()
                        inside = json.loads(payload.decode("utf-8"))
                        if inside != manifest:
                            raise RetentionError("retention_manifest_mismatch")
                        continue
                    if not member.isfile() or member.name not in expected:
                        raise RetentionError("retention_archive_member_invalid")
                    handle = archive.extractfile(member)
                    digest = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = handle.read(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        digest.update(chunk)
                    row = expected[member.name]
                    if size != row["size"] or "sha256:" + digest.hexdigest() != row["sha256"]:
                        raise RetentionError("retention_archive_digest_mismatch")
                    seen[member.name] = True
            after = os.fstat(stream.fileno())
        current = (
            os.stat(
                os.path.basename(path),
                dir_fd=anchor["descriptor"],
                follow_symlinks=False,
            )
            if anchor is not None
            else os.stat(path, follow_symlinks=False)
        )
        if (
            (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
        ):
            raise RetentionError("retention_archive_changed")
    except (OSError, tarfile.TarError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, RetentionError):
            raise
        raise RetentionError("retention_archive_invalid")
    if set(seen) != set(expected):
        raise RetentionError("retention_archive_incomplete")


def _create_archive(root, row):
    run_dir = os.path.join(root, row["run"])
    files = _inventory(run_dir)
    basis = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    archive_id = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    archive_rel = "%s/%s-%s.tar.gz" % (ARCHIVE_DIR, row["slug"], archive_id)
    archive_path = os.path.join(root, archive_rel)
    manifest = {
        "schema_version": 1,
        "slug": row["slug"],
        "terminal_status": row["terminal_status"],
        "archive": archive_rel,
        "files": files,
    }
    archive_dir = os.path.dirname(archive_path)
    store.ensure_local_directory(root, archive_dir)
    with store.local_path_guard(root, archive_dir):
        fd, tmp = store._temporary_file(archive_path)
        try:
            with os.fdopen(fd, "wb") as stream:
                with tarfile.open(fileobj=stream, mode="w:gz", dereference=False) as archive:
                    for item in files:
                        archive.add(
                            os.path.join(run_dir, item["path"]),
                            arcname=item["path"],
                            recursive=False,
                        )
                    payload = contracts.dumps(manifest, pretty=True).encode("utf-8") + b"\n"
                    info = tarfile.TarInfo(MANIFEST_NAME)
                    info.size = len(payload)
                    info.mode = 0o600
                    import io
                    archive.addfile(info, io.BytesIO(payload))
            anchor = store._active_anchor(tmp)
            os.chmod(
                os.path.basename(tmp), 0o600, dir_fd=anchor["descriptor"],
            )
            _verify_archive(tmp, manifest)
            store._replace_path(tmp, archive_path)
            tmp = ""
            _verify_archive(archive_path, manifest)
        except BaseException:
            if tmp:
                try:
                    store._unlink_path(tmp)
                except OSError:
                    pass
            raise
        manifest_path = archive_path + ".manifest.json"
        store.atomic_write(
            manifest_path, contracts.dumps(manifest, pretty=True) + "\n", mode=0o600,
        )
    return archive_rel, manifest


def _archive_id(archive_rel):
    match = re.search(r"-([0-9a-f]{16})\.tar\.gz$", archive_rel)
    if match is None:
        raise RetentionError("retention_archive_identity_invalid")
    return match.group(1)


def _stub_source_bytes(root, path):
    snapshot = store.local_file_snapshot(root, path)
    if snapshot is None or len(snapshot[1]) > MAX_FILE_BYTES:
        raise RetentionError("retention_stub_source_unsafe")
    return snapshot[1]


def _prepare_stub(root, parent, row, archive_rel, manifest):
    archive_id = _archive_id(archive_rel)
    name = ".%s.retention.%s" % (row["slug"], archive_id)
    prepared = os.path.join(parent, name)
    os.mkdir(name, 0o700, dir_fd=store._active_anchor(prepared)["descriptor"])
    run_dir = os.path.join(parent, row["slug"])
    try:
        with store.local_path_guard(root, prepared):
            for file_name in sorted(STUB_FILES):
                source = os.path.join(run_dir, file_name)
                if not os.path.lexists(source):
                    continue
                payload = _stub_source_bytes(root, source)
                try:
                    text = payload.decode("utf-8")
                except UnicodeError as exc:
                    raise RetentionError("retention_stub_source_unsafe") from exc
                store.atomic_write(
                    os.path.join(prepared, file_name), text, mode=0o600,
                )
            manifest_digest = "sha256:" + hashlib.sha256(
                (contracts.dumps(manifest, pretty=True) + "\n").encode("utf-8")
            ).hexdigest()
            store.atomic_write(
                os.path.join(prepared, "ARCHIVED.md"),
                "# Archived Kimiflow Run\n\nArchive: %s\nManifest: %s\n"
                "Recovery source: %s\n"
                "Restore or inspect the local mode-0600 archive for complete evidence.\n"
                % (archive_rel, manifest_digest, name),
                mode=0o600,
            )
    except BaseException:
        shutil.rmtree(prepared)
        raise
    return prepared


def _recovery_manifest(root, slug, archive_id):
    path = os.path.join(
        root, ARCHIVE_DIR, "%s-%s.tar.gz.manifest.json" % (slug, archive_id),
    )
    try:
        payload = store.stable_local_file_bytes(root, path, missing_ok=True)
        if payload is None or len(payload) > 16 * 1024 * 1024:
            return None
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("slug") != slug
        or not isinstance(value.get("files"), list)
    ):
        return None
    return value


def _recovery_marker(root, directory, archive_rel, manifest):
    path = os.path.join(directory, "ARCHIVED.md")
    try:
        payload = store.stable_local_file_bytes(root, path, missing_ok=True)
        if payload is None or len(payload) > 8192:
            return False
        text = payload.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        return False
    manifest_digest = "sha256:" + hashlib.sha256(
        (contracts.dumps(manifest, pretty=True) + "\n").encode("utf-8")
    ).hexdigest()
    return (
        "Archive: %s\n" % archive_rel in text
        and "Manifest: %s\n" % manifest_digest in text
    )


def _recover_interrupted(root):
    parent = os.path.join(root, ".kimiflow")
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return []
    recovered = []
    store.ensure_local_directory(root, parent)
    with store.local_path_guard(root, parent):
        for name in names:
            match = RECOVERY_RE.fullmatch(name)
            if match is None:
                continue
            slug = match.group("slug")
            archive_id = match.group("archive_id")
            displaced = os.path.join(parent, name)
            run_dir = os.path.join(parent, slug)
            if not os.path.isdir(displaced) or os.path.islink(displaced):
                raise RetentionError("retention_recovery_source_unsafe")
            if not os.path.lexists(run_dir):
                descriptor = store._active_anchor(run_dir)["descriptor"]
                os.rename(name, slug, src_dir_fd=descriptor, dst_dir_fd=descriptor)
                recovered.append({"run": ".kimiflow/%s" % slug, "action": "restored"})
                continue
            if os.path.islink(run_dir) or not os.path.isdir(run_dir):
                raise RetentionError("retention_recovery_target_unsafe")
            manifest = _recovery_manifest(root, slug, archive_id)
            archive_rel = "%s/%s-%s.tar.gz" % (ARCHIVE_DIR, slug, archive_id)
            if manifest is None:
                raise RetentionError("retention_recovery_manifest_invalid")
            archived = _recovery_marker(root, run_dir, archive_rel, manifest)
            prepared = _recovery_marker(root, displaced, archive_rel, manifest)
            if not archived and not prepared:
                raise RetentionError("retention_recovery_receipt_invalid")
            if _active_run(root) == ".kimiflow/%s" % slug and archived:
                store._exchange_paths(displaced, run_dir)
                shutil.rmtree(displaced)
                recovered.append({"run": ".kimiflow/%s" % slug, "action": "restored"})
                continue
            if (
                archived
                and _inventory(displaced) == manifest["files"]
            ):
                shutil.rmtree(displaced)
                recovered.append({"run": ".kimiflow/%s" % slug, "action": "committed"})
                continue
            if archived:
                store._exchange_paths(displaced, run_dir)
                shutil.rmtree(displaced)
                recovered.append({"run": ".kimiflow/%s" % slug, "action": "restored"})
                continue
            # Prepared but not exchanged: the canonical source still owns all data.
            shutil.rmtree(displaced)
            recovered.append({"run": ".kimiflow/%s" % slug, "action": "discarded_prepared"})
    return recovered


def _replace_with_stub(root, row, archive_rel, manifest):
    run_dir = os.path.join(root, row["run"])
    if _active_run(root) == row["run"] or _terminal(run_dir) != row["terminal_status"]:
        raise RetentionError("retention_source_no_longer_eligible")
    parent = os.path.dirname(run_dir)
    store.ensure_local_directory(root, parent)
    with store.local_path_guard(root, parent):
        prepared = _prepare_stub(root, parent, row, archive_rel, manifest)
        exchanged = False
        try:
            if _active_run(root) == row["run"] or _inventory(run_dir) != manifest["files"]:
                raise RetentionError("retention_source_changed")
            store._exchange_paths(prepared, run_dir)
            exchanged = True
            if _active_run(root) == row["run"] or _inventory(prepared) != manifest["files"]:
                raise RetentionError("retention_source_changed")
        except BaseException:
            if exchanged:
                store._exchange_paths(prepared, run_dir)
            if os.path.isdir(prepared) and not os.path.islink(prepared):
                shutil.rmtree(prepared)
            raise
        shutil.rmtree(prepared)


def archive_one(root, min_age_days=DEFAULT_MIN_AGE_DAYS, keep=DEFAULT_KEEP, now=None, write=False):
    if write:
        _recover_interrupted(root)
    preview = eligibility(root, min_age_days=min_age_days, keep=keep, now=now)
    candidates = [row for row in preview["runs"] if row["reason"] == "eligible"]
    if not candidates:
        return {**preview, "status": "no_eligible_run", "written": False}
    selected = sorted(candidates, key=lambda row: (row["modified_at"], row["slug"]))[0]
    if not write:
        return {**preview, "status": "eligible", "written": False, "selected": selected}
    archive_rel, manifest = _create_archive(root, selected)
    _verify_archive(os.path.join(root, archive_rel), manifest)
    _replace_with_stub(root, selected, archive_rel, manifest)
    return {
        "schema_version": 1,
        "status": "archived",
        "written": True,
        "run": selected["run"],
        "archive": archive_rel,
        "file_count": len(manifest["files"]),
        "manifest_digest": "sha256:" + hashlib.sha256(
            (contracts.dumps(manifest, pretty=True) + "\n").encode("utf-8")
        ).hexdigest(),
    }


def run(argv):
    action = argv[0] if argv else "preview"
    rest = argv[1:] if argv else []
    root = ""
    write = False
    pretty = False
    min_age = DEFAULT_MIN_AGE_DAYS
    keep = DEFAULT_KEEP
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--root":
            index += 1
            root = rest[index] if index < len(rest) else ""
        elif arg == "--min-age-days":
            index += 1
            try:
                min_age = int(rest[index])
            except (IndexError, ValueError):
                return die("retention: invalid --min-age-days", 2)
        elif arg == "--keep":
            index += 1
            try:
                keep = int(rest[index])
            except (IndexError, ValueError):
                return die("retention: invalid --keep", 2)
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        else:
            return die("retention: unknown argument: %s" % arg, 2)
        index += 1
    root = resolve_root(root)
    try:
        if action == "preview":
            value = eligibility(root, min_age_days=min_age, keep=keep)
        elif action == "archive":
            value = archive_one(root, min_age_days=min_age, keep=keep, write=write)
        else:
            return die("retention action must be preview or archive", 2)
    except (OSError, ValueError, store.ConcurrentWriteError) as exc:
        return die("retention: %s" % exc, 1)
    contracts.json_print(value, pretty)
    return 0
