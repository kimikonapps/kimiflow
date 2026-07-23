import contextlib
import concurrent.futures
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import workspace_preflight as wp


class WorkspacePreflightCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.repo = os.path.join(self.temp, "repo")
        os.mkdir(self.repo)
        self.git(self.repo, "init", "-b", "main")
        self.git(self.repo, "config", "user.email", "test@example.com")
        self.git(self.repo, "config", "user.name", "Test User")
        with open(os.path.join(self.repo, "tracked.txt"), "w", encoding="utf-8") as handle:
            handle.write("base\n")
        self.git(self.repo, "add", "tracked.txt")
        self.git(self.repo, "commit", "-m", "base")

    def git(self, root, *args, check=True):
        return subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )

    def add_tree(self, name):
        path = os.path.join(self.temp, name)
        self.git(self.repo, "worktree", "add", "-b", name, path)
        return os.path.realpath(path)

    def write_run(self, slug="run-a", status="active", schema=4):
        run = os.path.join(self.repo, ".kimiflow", slug)
        os.makedirs(run, exist_ok=True)
        with open(os.path.join(run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Flow schema: %s\nStatus: %s\n" % (schema, status))
        return ".kimiflow/%s" % slug

    def test_status_inventories_current_and_linked_worktrees(self):
        linked = self.add_tree("linked")
        dirty_name = "notes one.txt"
        with open(os.path.join(linked, dirty_name), "w", encoding="utf-8") as handle:
            handle.write("dirty\n")
        status = wp.build_status(self.repo)
        self.assertEqual(status["worktree_count"], 2)
        current = next(tree for tree in status["worktrees"] if tree["current"])
        other = next(tree for tree in status["worktrees"] if tree["path"] == linked)
        self.assertEqual(current["branch"], "refs/heads/main")
        self.assertTrue(other["dirty"])
        self.assertEqual(other["dirty_paths"], [dirty_name])

    def test_registry_and_remove_fail_closed_without_force(self):
        linked = self.add_tree("unsafe")
        forged = os.path.join(linked, ".kimiflow", "session")
        os.makedirs(forged)
        with open(os.path.join(forged, "WORKTREE_REGISTRY.json"), "w", encoding="utf-8") as handle:
            handle.write('{"schema_version":1,"entries":[{"path":"%s","run":".kimiflow/run-a"}]}\n' % linked)
        with self.assertRaises(wp.WorkspaceError):
            wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isdir(linked))
        self.assertTrue(self.git(self.repo, "branch", "--list", "unsafe").stdout.strip().endswith("unsafe"))

        self.write_run()
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.makedirs(session, exist_ok=True)
        registry = os.path.join(session, "WORKTREE_REGISTRY.json")
        with open(registry, "w", encoding="utf-8") as handle:
            handle.write("not json\n")
        with self.assertRaises(wp.WorkspaceError):
            wp.register(self.repo, linked, ".kimiflow/run-a", write=True)
        os.unlink(registry)
        outside = os.path.join(self.temp, "outside")
        os.mkdir(outside)
        lock = os.path.join(session, "WORKTREE_REGISTRY.lock")
        if os.path.exists(lock):
            os.unlink(lock)
        os.rmdir(session)
        os.symlink(outside, session)
        with self.assertRaises(wp.WorkspaceError):
            wp.register(self.repo, linked, ".kimiflow/run-a", write=True)
        self.assertEqual(os.listdir(outside), [])

    def test_registry_write_never_follows_exchanged_session_parent(self):
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.makedirs(session)
        displaced = session + ".owned"
        outside = os.path.join(self.temp, "registry-outside")
        os.mkdir(outside)
        original_write = wp.atomic_directory_write
        swapped = False

        def exchange_then_write(descriptor, name, payload):
            nonlocal swapped
            if name == "WORKTREE_REGISTRY.json" and not swapped:
                swapped = True
                os.rename(session, displaced)
                os.symlink(outside, session)
            return original_write(descriptor, name, payload)

        with mock.patch.object(wp, "atomic_directory_write", side_effect=exchange_then_write):
            with self.assertRaises(wp.WorkspaceError):
                wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        self.assertEqual(os.listdir(outside), [])
        self.assertTrue(os.path.isfile(os.path.join(displaced, "WORKTREE_REGISTRY.json")))

    def test_registry_write_rejects_exchanged_atomic_source(self):
        wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        original_link = wp.os.link
        original_rename = wp.os.rename
        exchanged = False

        def exchange_source_then_link(source, destination, *args, **kwargs):
            nonlocal exchanged
            if destination == "WORKTREE_REGISTRY.json" and not exchanged:
                exchanged = True
                source_fd = kwargs.get("src_dir_fd")
                original_rename(source, source + ".owned", src_dir_fd=source_fd, dst_dir_fd=source_fd)
                descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=source_fd)
                try:
                    os.write(descriptor, b'{"schema_version":1,"entries":[]}\n')
                finally:
                    os.close(descriptor)
            return original_link(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "link", side_effect=exchange_source_then_link):
            with self.assertRaises(wp.WorkspaceError):
                wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        self.assertTrue(exchanged)
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_registry_write_preserves_target_created_before_install(self):
        session = os.path.join(self.repo, ".kimiflow", "session")
        registry_name = "WORKTREE_REGISTRY.json"
        sentinel = b'{"schema_version":1,"entries":[]}\n'
        original_stat = wp.os.stat
        missing_checks = 0

        def create_target_after_missing_check(path, *args, **kwargs):
            nonlocal missing_checks
            try:
                return original_stat(path, *args, **kwargs)
            except FileNotFoundError:
                if path == registry_name and kwargs.get("dir_fd") is not None:
                    missing_checks += 1
                    if missing_checks == 2:
                        descriptor = os.open(
                            registry_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=kwargs["dir_fd"],
                        )
                        try:
                            os.write(descriptor, sentinel)
                        finally:
                            os.close(descriptor)
                raise

        with mock.patch.object(wp.os, "stat", side_effect=create_target_after_missing_check):
            with self.assertRaises(wp.WorkspaceError):
                wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        self.assertEqual(missing_checks, 2)
        with open(os.path.join(session, registry_name), "rb") as handle:
            self.assertEqual(handle.read(), sentinel)

    def test_registry_write_restores_old_target_after_post_rename_stat_failure(self):
        wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        attempted = {
            "schema_version": 1,
            "entries": [{
                "path": os.path.realpath(self.repo),
                "run": ".kimiflow/run-a",
                "identity": "a" * 64,
            }],
        }
        original_stat = wp.os.stat
        failed = False

        def fail_post_rename_stat(path, *args, **kwargs):
            nonlocal failed
            if not failed and str(path).startswith(".kimiflow-backup-WORKTREE_REGISTRY.json-"):
                failed = True
                raise OSError("simulated post-rename stat failure")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(wp.os, "stat", side_effect=fail_post_rename_stat):
            with self.assertRaises(wp.WorkspaceError):
                wp.write_registry(self.repo, attempted)
        self.assertTrue(failed)
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_registry_write_removes_new_target_after_post_link_stat_failure(self):
        registry_name = "WORKTREE_REGISTRY.json"
        original_link = wp.os.link
        original_stat = wp.os.stat
        linked = False
        failed = False

        def mark_linked(*args, **kwargs):
            nonlocal linked
            result = original_link(*args, **kwargs)
            if args[1] == registry_name:
                linked = True
            return result

        def fail_post_link_stat(path, *args, **kwargs):
            nonlocal failed
            if linked and not failed and path == registry_name and kwargs.get("dir_fd") is not None:
                failed = True
                raise OSError("simulated post-link stat failure")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(wp.os, "link", side_effect=mark_linked), mock.patch.object(
            wp.os,
            "stat",
            side_effect=fail_post_link_stat,
        ):
            with self.assertRaises(wp.WorkspaceError):
                wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        self.assertTrue(failed)
        registry = os.path.join(self.repo, ".kimiflow", "session", registry_name)
        self.assertFalse(os.path.exists(registry))
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_registry_write_restores_original_after_concurrent_target_claim(self):
        original = {
            "schema_version": 1,
            "entries": [{
                "path": os.path.realpath(self.repo),
                "run": ".kimiflow/run-a",
                "identity": "a" * 64,
            }],
        }
        wp.write_registry(self.repo, original)
        original_link = wp.os.link
        claimed = False

        def claim_target_before_link(source, destination, *args, **kwargs):
            nonlocal claimed
            if destination == "WORKTREE_REGISTRY.json" and not claimed:
                claimed = True
                descriptor = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=kwargs["dst_dir_fd"],
                )
                try:
                    os.write(descriptor, b'{"entries":[],"schema_version":1}\n')
                finally:
                    os.close(descriptor)
            return original_link(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "link", side_effect=claim_target_before_link):
            with self.assertRaises(wp.WorkspaceError) as raised:
                wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        self.assertTrue(claimed)
        self.assertIn("concurrent target preserved", str(raised.exception))
        self.assertEqual(wp.read_registry(self.repo), original)
        session = os.path.join(self.repo, ".kimiflow", "session")
        self.assertFalse(any(name.startswith(".kimiflow-backup-") for name in os.listdir(session)))

    def test_registry_write_restores_original_when_installed_target_is_replaced_before_fsync(self):
        original = {
            "schema_version": 1,
            "entries": [{
                "path": os.path.realpath(self.repo),
                "run": ".kimiflow/run-a",
                "identity": "a" * 64,
            }],
        }
        wp.write_registry(self.repo, original)
        session = os.path.join(self.repo, ".kimiflow", "session")
        registry = os.path.join(session, "WORKTREE_REGISTRY.json")
        original_fsync = wp.os.fsync
        failed = False

        def replace_target_then_fail_directory_fsync(descriptor):
            nonlocal failed
            pinned = os.fstat(descriptor)
            named = os.lstat(session)
            if not failed and (pinned.st_dev, pinned.st_ino) == (named.st_dev, named.st_ino):
                failed = True
                replacement = registry + ".claim"
                with open(replacement, "wb") as handle:
                    handle.write(b'{"entries":[],"schema_version":1}\n')
                os.replace(replacement, registry)
                raise OSError("simulated commit-boundary fsync failure")
            return original_fsync(descriptor)

        with mock.patch.object(wp.os, "fsync", side_effect=replace_target_then_fail_directory_fsync):
            with self.assertRaises(wp.WorkspaceError) as raised:
                wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        self.assertTrue(failed)
        self.assertIn("concurrent target preserved", str(raised.exception))
        self.assertEqual(wp.read_registry(self.repo), original)
        names = os.listdir(session)
        self.assertFalse(any(name.startswith(".kimiflow-backup-") for name in names))
        quarantine = [name for name in names if name.startswith(".kimiflow-quarantine-WORKTREE_REGISTRY")]
        self.assertEqual(len(quarantine), 1)
        with open(os.path.join(session, quarantine[0]), "rb") as handle:
            self.assertEqual(handle.read(), b'{"entries":[],"schema_version":1}\n')

    def test_first_registry_write_quarantines_replacement_before_failed_fsync(self):
        session = os.path.join(self.repo, ".kimiflow", "session")
        registry = os.path.join(session, "WORKTREE_REGISTRY.json")
        original_fsync = wp.os.fsync
        failed = False

        def replace_target_then_fail_directory_fsync(descriptor):
            nonlocal failed
            if os.path.isdir(session):
                pinned = os.fstat(descriptor)
                named = os.lstat(session)
                if not failed and (pinned.st_dev, pinned.st_ino) == (named.st_dev, named.st_ino):
                    failed = True
                    replacement = registry + ".claim"
                    with open(replacement, "wb") as handle:
                        handle.write(b'{"entries":[],"schema_version":1}\n')
                    os.replace(replacement, registry)
                    raise OSError("simulated first-publication fsync failure")
            return original_fsync(descriptor)

        with mock.patch.object(wp.os, "fsync", side_effect=replace_target_then_fail_directory_fsync):
            with self.assertRaises(wp.WorkspaceError) as raised:
                wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        self.assertTrue(failed)
        self.assertIn("concurrent target preserved", str(raised.exception))
        self.assertFalse(os.path.exists(registry))
        quarantine = [
            name for name in os.listdir(session)
            if name.startswith(".kimiflow-quarantine-WORKTREE_REGISTRY")
        ]
        self.assertEqual(len(quarantine), 1)

    def test_registry_write_rejects_replacement_during_successful_commit_fsync(self):
        original = {
            "schema_version": 1,
            "entries": [{
                "path": os.path.realpath(self.repo),
                "run": ".kimiflow/run-a",
                "identity": "a" * 64,
            }],
        }
        wp.write_registry(self.repo, original)
        session = os.path.join(self.repo, ".kimiflow", "session")
        registry = os.path.join(session, "WORKTREE_REGISTRY.json")
        original_fsync = wp.os.fsync
        replaced = False

        def replace_target_during_directory_fsync(descriptor):
            nonlocal replaced
            pinned = os.fstat(descriptor)
            named = os.lstat(session)
            if not replaced and (pinned.st_dev, pinned.st_ino) == (named.st_dev, named.st_ino):
                replaced = True
                replacement = registry + ".claim"
                with open(replacement, "wb") as handle:
                    handle.write(b'{"entries":[],"schema_version":1}\n')
                os.replace(replacement, registry)
            return original_fsync(descriptor)

        with mock.patch.object(wp.os, "fsync", side_effect=replace_target_during_directory_fsync):
            with self.assertRaises(wp.WorkspaceError):
                wp.write_registry(self.repo, {"schema_version": 1, "entries": []})
        self.assertTrue(replaced)
        self.assertEqual(wp.read_registry(self.repo), original)
        names = os.listdir(session)
        self.assertFalse(any(name.startswith(".kimiflow-backup-") for name in names))
        self.assertEqual(
            len([name for name in names if name.startswith(".kimiflow-quarantine-WORKTREE_REGISTRY")]),
            1,
        )

    def test_registration_directory_fsync_failure_rolls_back_both_authorities(self):
        linked = self.add_tree("registry-fsync-failure")
        run = self.write_run()
        session = os.path.join(self.repo, ".kimiflow", "session")
        original_fsync = wp.os.fsync
        failed = False
        session_fsync_calls = 0

        def fail_first_session_directory_fsync(descriptor):
            nonlocal failed, session_fsync_calls
            if os.path.isdir(session):
                pinned = os.fstat(descriptor)
                named = os.lstat(session)
                if (pinned.st_dev, pinned.st_ino) == (named.st_dev, named.st_ino):
                    session_fsync_calls += 1
                    if not failed:
                        failed = True
                        raise OSError("simulated registry directory fsync failure")
            return original_fsync(descriptor)

        with mock.patch.object(wp.os, "fsync", side_effect=fail_first_session_directory_fsync):
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)
        self.assertTrue(failed)
        self.assertGreaterEqual(session_fsync_calls, 2)
        self.assertFalse(os.path.exists(wp.owner_receipt_path(linked)))
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_non_posix_status_keeps_read_only_inventory(self):
        os.makedirs(os.path.join(self.repo, ".kimiflow", "session"))
        with mock.patch.object(wp, "DESCRIPTOR_RELATIVE_SUPPORTED", False):
            status = wp.build_status(self.repo)
        self.assertEqual(status["worktree_count"], 1)
        self.assertEqual(status["temporary_count"], 0)

    def test_registration_session_exchange_rolls_back_receipt_and_registry(self):
        linked = self.add_tree("registry-operation-swap")
        run = self.write_run()
        session = os.path.join(self.repo, ".kimiflow", "session")
        displaced = session + ".owned"
        original_write = wp.atomic_admin_write
        exchanged = False

        def write_receipt_then_exchange(*args, **kwargs):
            nonlocal exchanged
            result = original_write(*args, **kwargs)
            if not exchanged:
                exchanged = True
                os.rename(session, displaced)
                os.mkdir(session)
                with open(os.path.join(session, "WORKTREE_REGISTRY.json"), "w", encoding="utf-8") as handle:
                    handle.write('{"schema_version":1,"entries":[]}\n')
            return result

        with mock.patch.object(wp, "atomic_admin_write", side_effect=write_receipt_then_exchange):
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)
        self.assertFalse(os.path.exists(wp.owner_receipt_path(linked)))
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])
        with open(os.path.join(session, "WORKTREE_REGISTRY.json"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"schema_version":1,"entries":[]}\n')
        with open(os.path.join(displaced, "WORKTREE_REGISTRY.json"), "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["entries"], [])

    def test_registration_postcheck_session_exchange_rolls_back_transaction(self):
        linked = self.add_tree("registry-postcheck-swap")
        run = self.write_run()
        session = os.path.join(self.repo, ".kimiflow", "session")
        displaced = session + ".owned"
        original_check = wp.ensure_registry_descriptor_current
        exchanged = False

        def exchange_after_check(primary, descriptor):
            nonlocal exchanged
            result = original_check(primary, descriptor)
            if not exchanged:
                exchanged = True
                os.rename(session, displaced)
                os.mkdir(session)
                with open(os.path.join(session, "WORKTREE_REGISTRY.json"), "w", encoding="utf-8") as handle:
                    handle.write('{"schema_version":1,"entries":[]}\n')
            return result

        with mock.patch.object(wp, "ensure_registry_descriptor_current", side_effect=exchange_after_check):
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)
        self.assertTrue(exchanged)
        self.assertFalse(os.path.exists(wp.owner_receipt_path(linked)))
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])
        with open(os.path.join(displaced, "WORKTREE_REGISTRY.json"), "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["entries"], [])

    def test_register_binds_run_and_caps_temporary_worktrees_at_one(self):
        first = self.add_tree("first")
        second = self.add_tree("second")
        run = self.write_run()
        result = wp.register(self.repo, first, run, write=True)
        self.assertTrue(result["written"])
        registry = wp.read_registry(self.repo)
        self.assertEqual(len(registry["entries"]), 1)
        self.assertEqual(registry["entries"][0]["path"], first)
        self.assertEqual(registry["entries"][0]["run"], run)
        self.assertRegex(registry["entries"][0]["identity"], wp.IDENTITY_RE)
        self.assertTrue(wp.owner_receipt_matches(first, registry["entries"][0]))
        with self.assertRaises(wp.WorkspaceError):
            wp.register(self.repo, second, run, write=True)
        self.assertEqual(wp.build_status(self.repo)["temporary_count"], 1)

    def test_registration_refuses_ignored_content_created_after_status(self):
        linked = self.add_tree("late-ignored-registration")
        run = self.write_run()
        exclude = self.git(linked, "rev-parse", "--git-path", "info/exclude").stdout.strip()
        with open(exclude, "a", encoding="utf-8") as handle:
            handle.write("late.tmp\n")
        original_receipt_path = wp.owner_receipt_path
        created = False

        def create_ignored_then_resolve(path):
            nonlocal created
            if not created:
                created = True
                with open(os.path.join(linked, "late.tmp"), "w", encoding="utf-8") as handle:
                    handle.write("preserve\n")
            return original_receipt_path(path)

        with mock.patch.object(wp, "owner_receipt_path", side_effect=create_ignored_then_resolve):
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)
        self.assertTrue(created)
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])
        self.assertTrue(os.path.isfile(os.path.join(linked, "late.tmp")))

    def test_registration_status_recheck_failure_removes_owner_receipt(self):
        linked = self.add_tree("late-status-failure")
        run = self.write_run()
        original_status = wp.worktree_status
        calls = 0

        def fail_second_status(path):
            nonlocal calls
            if path == linked:
                calls += 1
                if calls == 2:
                    raise wp.WorkspaceError("simulated final status failure")
            return original_status(path)

        with mock.patch.object(wp, "worktree_status", side_effect=fail_second_status):
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)
        self.assertEqual(calls, 2)
        self.assertFalse(os.path.exists(wp.owner_receipt_path(linked)))
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_unlink_admin_file_fsyncs_receipt_directory(self):
        linked = self.add_tree("receipt-unlink-fsync")
        admin_dir = os.path.dirname(wp.owner_receipt_path(linked))
        receipt_name = os.path.basename(wp.owner_receipt_path(linked))
        os.makedirs(admin_dir, exist_ok=True)
        with open(os.path.join(admin_dir, receipt_name), "w", encoding="utf-8") as handle:
            handle.write("receipt\n")
        descriptor = os.open(admin_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        self.addCleanup(os.close, descriptor)
        original_fsync = wp.os.fsync
        fsynced = False

        def observe_fsync(candidate):
            nonlocal fsynced
            if candidate == descriptor:
                fsynced = True
            return original_fsync(candidate)

        with mock.patch.object(wp.os, "fsync", side_effect=observe_fsync):
            wp.unlink_admin_file(descriptor, receipt_name)
        self.assertTrue(fsynced)
        self.assertFalse(os.path.exists(os.path.join(admin_dir, receipt_name)))

    def test_registration_refuses_content_created_during_registry_publication(self):
        linked = self.add_tree("publication-dirty")
        run = self.write_run()
        original_write_registry = wp.write_registry
        created = False

        def publish_then_create(primary, registry, directory_descriptor=None):
            nonlocal created
            result = original_write_registry(primary, registry, directory_descriptor)
            if registry.get("entries") and not created:
                created = True
                with open(os.path.join(linked, "late.txt"), "w", encoding="utf-8") as handle:
                    handle.write("preserve\n")
            return result

        with mock.patch.object(wp, "write_registry", side_effect=publish_then_create):
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)
        self.assertTrue(created)
        self.assertTrue(os.path.isfile(os.path.join(linked, "late.txt")))
        self.assertFalse(os.path.exists(wp.owner_receipt_path(linked)))
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_register_refuses_primary_checkout_when_invoked_from_linked_tree(self):
        linked = self.add_tree("linked-current")
        run = self.write_run()
        with self.assertRaises(wp.WorkspaceError):
            wp.register(linked, self.repo, run, write=True)
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_registration_refuses_worktree_replaced_after_status(self):
        linked = self.add_tree("register-swap")
        run = self.write_run()
        original_receipt_path = wp.owner_receipt_path
        swapped = False

        def swap_before_resolving_receipt(path):
            nonlocal swapped
            if path == linked and not swapped:
                swapped = True
                self.git(self.repo, "worktree", "remove", linked)
                self.git(self.repo, "worktree", "add", "-b", "register-replacement", linked)
            return original_receipt_path(path)

        with mock.patch.object(wp, "owner_receipt_path", side_effect=swap_before_resolving_receipt):
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])
        tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == linked)
        self.assertFalse(tree["kimiflow_owned"])

    def test_registration_refuses_identity_change_during_status_scan(self):
        linked = self.add_tree("status-swap")
        foreign = self.add_tree("status-foreign")
        with open(os.path.join(foreign, "foreign.txt"), "w", encoding="utf-8") as handle:
            handle.write("dirty\n")
        run = self.write_run()
        original_status = wp.worktree_status
        swapped = False

        def swap_after_status(path):
            nonlocal swapped
            result = original_status(path)
            if path == linked and not swapped:
                swapped = True
                displaced = linked + ".swap"
                os.rename(linked, displaced)
                os.rename(foreign, linked)
                os.rename(displaced, foreign)
            return result

        with mock.patch.object(wp, "worktree_status", side_effect=swap_after_status):
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_registration_refuses_swapped_administrative_directory(self):
        linked = self.add_tree("admin-swap")
        unrelated = self.add_tree("admin-swap-unrelated")
        run = self.write_run()
        admin_dir = os.path.dirname(wp.owner_receipt_path(linked))
        unrelated_admin = os.path.dirname(wp.owner_receipt_path(unrelated))
        displaced = admin_dir + ".swap"
        original_open = os.open
        swapped = False

        def swap_before_admin_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == admin_dir and not swapped:
                swapped = True
                os.rename(admin_dir, displaced)
                os.rename(unrelated_admin, admin_dir)
                os.rename(displaced, unrelated_admin)
            return original_open(path, flags, *args, **kwargs)

        try:
            with mock.patch.object(wp.os, "open", side_effect=swap_before_admin_open):
                with self.assertRaises(wp.WorkspaceError):
                    wp.register(self.repo, linked, run, write=True)
        finally:
            if swapped:
                os.rename(admin_dir, displaced)
                os.rename(unrelated_admin, admin_dir)
                os.rename(displaced, unrelated_admin)
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])
        self.assertEqual(self.git(linked, "status", "--short").returncode, 0)
        self.assertEqual(self.git(unrelated, "status", "--short").returncode, 0)

    def test_registration_refuses_admin_exchange_during_receipt_write(self):
        linked = self.add_tree("receipt-admin-swap")
        unrelated = self.add_tree("receipt-admin-unrelated")
        run = self.write_run()
        admin_dir = os.path.dirname(wp.owner_receipt_path(linked))
        unrelated_admin = os.path.dirname(wp.owner_receipt_path(unrelated))
        displaced = admin_dir + ".swap"
        original_write = wp.atomic_admin_write
        swapped = False

        def swap_then_write(*args, **kwargs):
            nonlocal swapped
            if not swapped:
                swapped = True
                os.rename(admin_dir, displaced)
                os.rename(unrelated_admin, admin_dir)
                os.rename(displaced, unrelated_admin)
            return original_write(*args, **kwargs)

        try:
            with mock.patch.object(wp, "atomic_admin_write", side_effect=swap_then_write):
                with self.assertRaises(wp.WorkspaceError):
                    wp.register(self.repo, linked, run, write=True)
        finally:
            if swapped:
                os.rename(admin_dir, displaced)
                os.rename(unrelated_admin, admin_dir)
                os.rename(displaced, unrelated_admin)
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])
        self.assertEqual(self.git(linked, "status", "--short").returncode, 0)
        self.assertEqual(self.git(unrelated, "status", "--short").returncode, 0)

    def test_reused_path_cannot_inherit_old_worktree_ownership(self):
        linked = self.add_tree("reused-path")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.git(self.repo, "worktree", "remove", linked)
        self.git(self.repo, "worktree", "add", "-b", "replacement", linked)
        self.write_run(status="done")
        tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == linked)
        self.assertTrue(tree["kimiflow_registered"])
        self.assertFalse(tree["kimiflow_owned"])
        self.assertIn("ownership-receipt-invalid", tree["blockers"])
        with self.assertRaises(wp.WorkspaceError):
            wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isdir(linked))

    def test_concurrent_registration_preserves_cap_and_single_owner(self):
        first = self.add_tree("concurrent-one")
        second = self.add_tree("concurrent-two")
        run = self.write_run()

        def claim(path):
            try:
                wp.register(self.repo, path, run, write=True)
                return "registered"
            except wp.WorkspaceError:
                return "refused"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, (first, second)))
        self.assertEqual(sorted(results), ["refused", "registered"])
        registry = wp.read_registry(self.repo)
        self.assertEqual(len(registry["entries"]), 1)
        self.assertIn(registry["entries"][0]["path"], (first, second))

    def test_register_refuses_dirty_or_terminal_target_claims(self):
        linked = self.add_tree("claim")
        run = self.write_run()
        with open(os.path.join(linked, "foreign.txt"), "w", encoding="utf-8") as handle:
            handle.write("foreign\n")
        with self.assertRaises(wp.WorkspaceError):
            wp.register(self.repo, linked, run, write=True)
        os.unlink(os.path.join(linked, "foreign.txt"))
        self.write_run(status="done")
        with self.assertRaises(wp.WorkspaceError):
            wp.register(self.repo, linked, run, write=True)

    def test_register_accepts_schema5_primary_run(self):
        linked = self.add_tree("schema-five")
        run = self.write_run(schema=5)
        result = wp.register(self.repo, linked, run, write=True)
        self.assertEqual(result["status"], "registered")
        self.assertEqual(result["entry"]["run"], run)

    def test_register_refuses_symlinked_primary_run_state(self):
        linked = self.add_tree("state-link")
        run = self.write_run()
        state = os.path.join(self.repo, run, "STATE.md")
        outside = os.path.join(self.temp, "state.md")
        shutil.copyfile(state, outside)
        os.unlink(state)
        os.symlink(outside, state)
        with self.assertRaises(wp.WorkspaceError):
            wp.register(self.repo, linked, run, write=True)

    def test_malformed_local_active_state_fails_closed(self):
        linked = self.add_tree("malformed-active")
        session = os.path.join(linked, ".kimiflow", "session")
        os.makedirs(session)
        with open(os.path.join(session, "ACTIVE_RUN.json"), "w", encoding="utf-8") as handle:
            json.dump([], handle)
        run = self.write_run()
        tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == linked)
        self.assertTrue(tree["active"])
        with self.assertRaises(wp.WorkspaceError):
            wp.register(self.repo, linked, run, write=True)

    def test_policy_caps_temporary_worktrees_at_one(self):
        first = self.add_tree("policy-one")
        run = self.write_run()
        wp.register(self.repo, first, run, write=True)
        status = wp.build_status(self.repo)
        self.assertEqual(status["policy"], {"mode": "solo", "new_worktrees": "explicit-only", "max_temporary": 1})
        self.assertFalse(status["can_register_temporary"])

    def test_terminal_cleanup_uses_primary_registry_and_no_force(self):
        linked = self.add_tree("terminal")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        with self.assertRaises(wp.WorkspaceError):
            wp.remove(self.repo, linked, write=True)
        self.write_run(status="done")
        result = wp.remove(self.repo, linked, write=True)
        self.assertEqual(result["status"], "archived")
        self.assertFalse(os.path.exists(linked))
        self.assertTrue(os.path.isdir(result["archive_path"]))
        self.assertTrue(os.path.isfile(os.path.join(result["archive_path"], "tracked.txt")))
        self.assertTrue(self.git(self.repo, "branch", "--list", "terminal").stdout.strip().endswith("terminal"))
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_failed_or_aborted_run_releases_exceptional_tree_cap(self):
        linked = self.add_tree("aborted-terminal")
        run = self.write_run(slug="run-aborted", status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(slug="run-aborted", status="aborted")
        result = wp.remove(self.repo, linked, write=True)
        self.assertEqual(result["status"], "archived")
        replacement = self.add_tree("after-abort")
        next_run = self.write_run(slug="run-after-abort", status="active")
        registered = wp.register(self.repo, replacement, next_run, write=True)
        self.assertEqual(registered["status"], "registered")

    def test_parked_backlog_run_keeps_exceptional_tree_for_resume(self):
        linked = self.add_tree("parked-resume")
        run = self.write_run(slug="run-parked", status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(slug="run-parked", status="backlog")
        with self.assertRaises(wp.WorkspaceError):
            wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isdir(linked))

    def test_cleanup_archives_content_created_during_disposition(self):
        linked = self.add_tree("cleanup-race")
        run = self.write_run(status="active")
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a", encoding="utf-8") as handle:
            handle.write("late.tmp\n")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        original_rename = os.rename

        def create_then_rename(source, destination, *args, **kwargs):
            if source == os.path.basename(linked) and destination == "worktree":
                with open(os.path.join(linked, "late.tmp"), "w", encoding="utf-8") as handle:
                    handle.write("must survive\n")
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "rename", side_effect=create_then_rename):
            result = wp.remove(self.repo, linked, write=True)
        self.assertEqual(result["status"], "archived")
        with open(os.path.join(result["archive_path"], "late.tmp"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "must survive\n")
        self.assertEqual(wp.build_status(self.repo)["worktree_count"], 1)

    def test_cleanup_refuses_path_swap_without_relocating_foreign_replacement(self):
        linked = self.add_tree("path-swap")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        entry = wp.read_registry(self.repo)["entries"][0]
        _, archive_path = wp.retirement_paths(linked, entry["identity"])
        displaced = linked + ".displaced"
        original_rename = os.rename

        def swap_then_rename(source, destination, *args, **kwargs):
            if source == os.path.basename(linked) and destination == "worktree":
                original_rename(linked, displaced)
                os.mkdir(linked)
                with open(os.path.join(linked, "foreign.txt"), "w", encoding="utf-8") as handle:
                    handle.write("foreign\n")
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "rename", side_effect=swap_then_rename):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isdir(linked))
        with open(os.path.join(linked, "foreign.txt"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "foreign\n")
        self.assertFalse(os.path.exists(archive_path))
        self.assertTrue(os.path.isfile(os.path.join(displaced, "tracked.txt")))
        self.assertEqual(len(wp.read_registry(self.repo)["entries"]), 1)

    def test_cleanup_refuses_renamed_archive_root_and_restores_owned_checkout(self):
        linked = self.add_tree("archive-root-swap")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        entry = wp.read_registry(self.repo)["entries"][0]
        archive_root, _ = wp.retirement_paths(linked, entry["identity"])
        displaced_root = archive_root + ".displaced"
        original_rename = os.rename
        swapped = False

        def rename_root_then_checkout(source, destination, *args, **kwargs):
            nonlocal swapped
            if source == os.path.basename(linked) and destination == "worktree" and not swapped:
                swapped = True
                original_rename(archive_root, displaced_root)
                os.mkdir(archive_root)
                with open(os.path.join(archive_root, "foreign.txt"), "w", encoding="utf-8") as handle:
                    handle.write("foreign\n")
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "rename", side_effect=rename_root_then_checkout):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isfile(os.path.join(linked, "tracked.txt")))
        with open(os.path.join(archive_root, "foreign.txt"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "foreign\n")
        self.assertTrue(os.path.isdir(displaced_root))
        self.assertEqual(len(wp.read_registry(self.repo)["entries"]), 1)

    def test_targeted_cleanup_preserves_unrelated_prunable_metadata(self):
        linked = self.add_tree("targeted")
        unrelated = self.add_tree("unrelated")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        offline = unrelated + ".offline"
        os.rename(unrelated, offline)
        before = wp.build_status(self.repo)
        self.assertEqual(before["worktree_count"], 3)
        self.assertTrue(next(tree for tree in before["worktrees"] if tree["path"] == unrelated)["prunable"])
        wp.remove(self.repo, linked, write=True)
        after = wp.build_status(self.repo)
        self.assertEqual(after["worktree_count"], 2)
        self.assertTrue(next(tree for tree in after["worktrees"] if tree["path"] == unrelated)["prunable"])

    def test_metadata_detach_failure_rolls_back_for_retry(self):
        linked = self.add_tree("detach-retry")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        with mock.patch.object(wp, "detach_admin_record", side_effect=wp.WorkspaceError("simulated metadata detach failure")):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isfile(os.path.join(linked, "tracked.txt")))
        self.assertTrue(wp.owner_receipt_matches(linked, wp.read_registry(self.repo)["entries"][0]))
        result = wp.remove(self.repo, linked, write=True)
        self.assertEqual(result["status"], "archived")
        self.assertTrue(os.path.isdir(result["archive_path"]))

    def test_metadata_failure_refuses_swapped_checkout_archive_rollback(self):
        linked = self.add_tree("archive-rollback-swap")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        entry = wp.read_registry(self.repo)["entries"][0]
        archive_root, archive_path = wp.retirement_paths(linked, entry["identity"])
        owned_archive = archive_path + ".owned"

        def swap_archive_then_fail(*args, **kwargs):
            os.rename(archive_path, owned_archive)
            os.mkdir(archive_path)
            with open(os.path.join(archive_path, "foreign.txt"), "w", encoding="utf-8") as handle:
                handle.write("foreign\n")
            raise wp.WorkspaceError("simulated metadata failure after archive swap")

        with mock.patch.object(wp, "detach_admin_record", side_effect=swap_archive_then_fail):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertFalse(os.path.exists(linked))
        with open(os.path.join(archive_path, "foreign.txt"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "foreign\n")
        self.assertTrue(os.path.isfile(os.path.join(owned_archive, "tracked.txt")))
        self.assertEqual(len(wp.read_registry(self.repo)["entries"]), 1)

    def test_cleanup_archives_only_matching_admin_record(self):
        linked = self.add_tree("admin-path")
        unrelated = self.add_tree("admin-unrelated")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        admin_dir = os.path.dirname(wp.owner_receipt_path(linked))
        unrelated_admin = os.path.dirname(wp.owner_receipt_path(unrelated))
        unrelated_identity = os.lstat(unrelated_admin)
        result = wp.remove(self.repo, linked, write=True)
        self.assertEqual(result["status"], "archived")
        self.assertFalse(os.path.exists(admin_dir))
        self.assertTrue(os.path.isdir(result["metadata_archive_path"]))
        current_unrelated = os.lstat(unrelated_admin)
        self.assertEqual(
            (current_unrelated.st_dev, current_unrelated.st_ino),
            (unrelated_identity.st_dev, unrelated_identity.st_ino),
        )
        self.assertEqual(self.git(unrelated, "status", "--short").returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(result["archive_path"], "tracked.txt")))

    def test_cleanup_never_invokes_destructive_git_worktree_remove(self):
        linked = self.add_tree("no-git-remove")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        original_run_git = wp.run_git

        def reject_destructive_remove(root, args, check=True):
            if args[:2] == ["worktree", "remove"]:
                raise AssertionError("retirement must not invoke git worktree remove")
            return original_run_git(root, args, check=check)

        with mock.patch.object(wp, "run_git", side_effect=reject_destructive_remove):
            result = wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isfile(os.path.join(result["archive_path"], "tracked.txt")))
        self.assertTrue(os.path.isdir(result["metadata_archive_path"]))

    def test_remove_restores_foreign_checkout_exchanged_at_archive_rename(self):
        linked = self.add_tree("archive-source-exchange")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        displaced = linked + ".owned"
        foreign = linked + ".foreign"
        os.mkdir(foreign)
        with open(os.path.join(foreign, "foreign.txt"), "w", encoding="utf-8") as handle:
            handle.write("foreign\n")
        original_rename = wp.os.rename
        exchanged = False

        def exchange_before_archive(source, destination, *args, **kwargs):
            nonlocal exchanged
            if source == os.path.basename(linked) and destination == "worktree" and not exchanged:
                exchanged = True
                original_rename(linked, displaced)
                original_rename(foreign, linked)
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "rename", side_effect=exchange_before_archive):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertTrue(exchanged)
        self.assertTrue(os.path.isfile(os.path.join(linked, "foreign.txt")))
        self.assertTrue(os.path.isfile(os.path.join(displaced, "tracked.txt")))
        self.assertEqual(len(wp.read_registry(self.repo)["entries"]), 1)

    def test_remove_fsyncs_checkout_and_metadata_archive_namespaces(self):
        linked = self.add_tree("archive-fsync")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        admin_parent = os.path.dirname(os.path.dirname(wp.owner_receipt_path(linked)))
        checkout_parent = os.path.dirname(linked)
        original_fsync = wp.os.fsync
        fsynced_directories = set()

        def record_directory_fsync(descriptor):
            info = os.fstat(descriptor)
            if stat.S_ISDIR(info.st_mode):
                fsynced_directories.add((info.st_dev, info.st_ino))
            return original_fsync(descriptor)

        with mock.patch.object(wp.os, "fsync", side_effect=record_directory_fsync):
            result = wp.remove(self.repo, linked, write=True)

        expected_paths = (
            checkout_parent,
            os.path.dirname(result["archive_path"]),
            admin_parent,
            os.path.dirname(result["metadata_archive_path"]),
        )
        for path in expected_paths:
            info = os.lstat(path)
            self.assertIn((info.st_dev, info.st_ino), fsynced_directories, path)

    def test_cleanup_refuses_administrative_swap_during_detach(self):
        linked = self.add_tree("detach-admin-swap")
        unrelated = self.add_tree("detach-admin-unrelated")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        entry = wp.read_registry(self.repo)["entries"][0]
        admin_dir = os.path.dirname(wp.owner_receipt_path(linked))
        unrelated_admin = os.path.dirname(wp.owner_receipt_path(unrelated))
        common_dir = wp.git_path(linked, ["rev-parse", "--git-common-dir"])
        _, metadata_path = wp.metadata_retirement_paths(common_dir, entry["identity"])
        admin_name = os.path.basename(admin_dir)
        unrelated_name = os.path.basename(unrelated_admin)
        displaced_name = admin_name + ".swap"
        original_rename = os.rename
        swapped = False

        def swap_at_metadata_move(source, destination, *args, **kwargs):
            nonlocal swapped
            source_fd = kwargs.get("src_dir_fd")
            if source == admin_name and destination == entry["identity"] and source_fd is not None and not swapped:
                swapped = True
                original_rename(admin_name, displaced_name, src_dir_fd=source_fd, dst_dir_fd=source_fd)
                original_rename(unrelated_name, admin_name, src_dir_fd=source_fd, dst_dir_fd=source_fd)
                original_rename(displaced_name, unrelated_name, src_dir_fd=source_fd, dst_dir_fd=source_fd)
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "rename", side_effect=swap_at_metadata_move):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertFalse(os.path.exists(metadata_path))
        self.assertTrue(os.path.isfile(os.path.join(linked, "tracked.txt")))
        self.assertEqual(self.git(linked, "status", "--short").returncode, 0)
        self.assertEqual(self.git(unrelated, "status", "--short").returncode, 0)

    def test_cleanup_admin_exchange_recovers_from_transient_second_rename_failure(self):
        linked = self.add_tree("detach-retry-swap")
        unrelated = self.add_tree("detach-retry-unrelated")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        entry = wp.read_registry(self.repo)["entries"][0]
        admin_dir = os.path.dirname(wp.owner_receipt_path(linked))
        unrelated_admin = os.path.dirname(wp.owner_receipt_path(unrelated))
        common_dir = wp.git_path(linked, ["rev-parse", "--git-common-dir"])
        _, metadata_path = wp.metadata_retirement_paths(common_dir, entry["identity"])
        admin_name = os.path.basename(admin_dir)
        unrelated_name = os.path.basename(unrelated_admin)
        displaced_name = admin_name + ".swap"
        original_rename = os.rename
        swapped = False
        failed_once = False

        def exchange_and_fail_once(source, destination, *args, **kwargs):
            nonlocal swapped, failed_once
            source_fd = kwargs.get("src_dir_fd")
            if source == admin_name and destination == entry["identity"] and source_fd is not None and not swapped:
                swapped = True
                original_rename(admin_name, displaced_name, src_dir_fd=source_fd, dst_dir_fd=source_fd)
                original_rename(unrelated_name, admin_name, src_dir_fd=source_fd, dst_dir_fd=source_fd)
                original_rename(displaced_name, unrelated_name, src_dir_fd=source_fd, dst_dir_fd=source_fd)
            elif source == entry["identity"] and destination == unrelated_name and not failed_once:
                failed_once = True
                raise OSError("transient injected rename failure")
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "rename", side_effect=exchange_and_fail_once):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertTrue(failed_once)
        self.assertFalse(os.path.exists(metadata_path))
        self.assertEqual(self.git(linked, "status", "--short").returncode, 0)
        self.assertEqual(self.git(unrelated, "status", "--short").returncode, 0)

    def test_cleanup_postrename_metadata_stat_failure_restores_git_record(self):
        linked = self.add_tree("detach-stat-failure")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        entry = wp.read_registry(self.repo)["entries"][0]
        admin_dir = os.path.dirname(wp.owner_receipt_path(linked))
        common_dir = wp.git_path(linked, ["rev-parse", "--git-common-dir"])
        _, metadata_path = wp.metadata_retirement_paths(common_dir, entry["identity"])
        original_stat = wp.os.stat
        original_fsync = wp.os.fsync
        identity_stats = 0
        fsynced_directories = set()

        def fail_postrename_identity_stat(path, *args, **kwargs):
            nonlocal identity_stats
            if path == entry["identity"] and kwargs.get("dir_fd") is not None:
                identity_stats += 1
                if identity_stats == 2:
                    raise OSError("simulated archive identity stat failure")
            return original_stat(path, *args, **kwargs)

        def record_directory_fsync(descriptor):
            info = os.fstat(descriptor)
            if stat.S_ISDIR(info.st_mode):
                fsynced_directories.add((info.st_dev, info.st_ino))
            return original_fsync(descriptor)

        with mock.patch.object(wp.os, "stat", side_effect=fail_postrename_identity_stat), mock.patch.object(
            wp.os,
            "fsync",
            side_effect=record_directory_fsync,
        ):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertEqual(identity_stats, 2)
        self.assertTrue(os.path.isdir(admin_dir))
        self.assertFalse(os.path.exists(metadata_path))
        self.assertTrue(os.path.isfile(os.path.join(linked, "tracked.txt")))
        self.assertEqual(self.git(linked, "status", "--short").returncode, 0)
        self.assertEqual(len(wp.read_registry(self.repo)["entries"]), 1)
        metadata_root = os.path.dirname(metadata_path)
        for path in (os.path.dirname(admin_dir), metadata_root):
            info = os.lstat(path)
            self.assertIn((info.st_dev, info.st_ino), fsynced_directories, path)

    def test_terminal_state_read_refuses_run_path_exchange(self):
        linked = self.add_tree("state-read-swap")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        run_dir = os.path.join(self.repo, run)
        displaced = run_dir + ".owned"
        outside = os.path.join(self.temp, "forged-terminal-run")
        os.mkdir(outside)
        with open(os.path.join(outside, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 4\nStatus: done\n")
        original_parse = wp.state_value_from_text
        swapped = False

        def exchange_before_parse(source, wanted):
            nonlocal swapped
            if not swapped:
                swapped = True
                os.rename(run_dir, displaced)
                os.symlink(outside, run_dir)
            return original_parse(source, wanted)

        with mock.patch.object(wp, "state_value_from_text", side_effect=exchange_before_parse):
            tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == linked)
        self.assertTrue(tree["active"])
        self.assertFalse(tree["removable"])

    def test_terminal_state_read_refuses_in_place_status_rewrite(self):
        linked = self.add_tree("state-read-in-place")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        state_path = os.path.join(self.repo, run, "STATE.md")
        original_parse = wp.state_value_from_text
        rewritten = False

        def rewrite_before_parse(source, wanted):
            nonlocal rewritten
            if not rewritten:
                rewritten = True
                with open(state_path, "r+", encoding="utf-8") as handle:
                    current = handle.read().replace("Status: done", "Status: active")
                    handle.seek(0)
                    handle.write(current)
                    handle.truncate()
            return original_parse(source, wanted)

        with mock.patch.object(wp, "state_value_from_text", side_effect=rewrite_before_parse):
            tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == linked)
        self.assertTrue(rewritten)
        self.assertTrue(tree["active"])
        self.assertFalse(tree["removable"])

    def test_cleanup_failed_metadata_compensation_keeps_checkout_archived(self):
        linked = self.add_tree("detach-persistent-failure")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        entry = wp.read_registry(self.repo)["entries"][0]
        common_dir = wp.git_path(linked, ["rev-parse", "--git-common-dir"])
        _, metadata_path = wp.metadata_retirement_paths(common_dir, entry["identity"])
        archive_root, archive_path = wp.retirement_paths(linked, entry["identity"])
        admin_name = os.path.basename(os.path.dirname(wp.owner_receipt_path(linked)))
        original_stat = wp.os.stat
        original_rename = wp.os.rename
        identity_stats = 0

        def fail_postrename_identity_stat(path, *args, **kwargs):
            nonlocal identity_stats
            if path == entry["identity"] and kwargs.get("dir_fd") is not None:
                identity_stats += 1
                if identity_stats == 2:
                    raise OSError("simulated post-rename stat failure")
            return original_stat(path, *args, **kwargs)

        def fail_compensation(source, destination, *args, **kwargs):
            if source == entry["identity"] and destination == admin_name:
                raise OSError("persistent compensation failure")
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "stat", side_effect=fail_postrename_identity_stat), mock.patch.object(
            wp.os,
            "rename",
            side_effect=fail_compensation,
        ):
            with self.assertRaises(wp.WorkspaceError) as raised:
                wp.remove(self.repo, linked, write=True)
        self.assertIn(archive_path, str(raised.exception))
        self.assertIn(metadata_path, str(raised.exception))
        self.assertFalse(os.path.exists(linked))
        self.assertTrue(os.path.isfile(os.path.join(archive_path, "tracked.txt")))
        self.assertTrue(os.path.isdir(metadata_path))
        self.assertEqual(len(wp.read_registry(self.repo)["entries"]), 1)

    def test_remove_postcheck_session_exchange_reports_both_archives(self):
        linked = self.add_tree("remove-postcheck-swap")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        session = os.path.join(self.repo, ".kimiflow", "session")
        displaced = session + ".owned"
        original_check = wp.ensure_registry_descriptor_current
        exchanged = False

        def exchange_after_check(primary, descriptor):
            nonlocal exchanged
            result = original_check(primary, descriptor)
            if not exchanged:
                exchanged = True
                os.rename(session, displaced)
                os.mkdir(session)
                with open(os.path.join(session, "WORKTREE_REGISTRY.json"), "w", encoding="utf-8") as handle:
                    handle.write('{"schema_version":1,"entries":[]}\n')
            return result

        with mock.patch.object(wp, "ensure_registry_descriptor_current", side_effect=exchange_after_check):
            with self.assertRaises(wp.WorkspaceError) as raised:
                wp.remove(self.repo, linked, write=True)
        self.assertTrue(exchanged)
        message = str(raised.exception)
        self.assertIn("checkout", message)
        self.assertIn("metadata", message)
        archive_paths = [part for part in message.replace(";", " ").split() if os.path.isabs(part)]
        self.assertGreaterEqual(len(archive_paths), 2)
        self.assertTrue(any(os.path.isdir(path) for path in archive_paths))

    def test_postdetach_archive_rename_reports_actual_checkout_path(self):
        linked = self.add_tree("postdetach-archive-swap")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        entry = wp.read_registry(self.repo)["entries"][0]
        archive_root, _ = wp.retirement_paths(linked, entry["identity"])
        displaced_root = archive_root + ".actual"
        original_detach = wp.detach_admin_record

        def detach_then_exchange(*args, **kwargs):
            result = original_detach(*args, **kwargs)
            os.rename(archive_root, displaced_root)
            os.mkdir(archive_root)
            with open(os.path.join(archive_root, "foreign.txt"), "w", encoding="utf-8") as handle:
                handle.write("foreign\n")
            return result

        with mock.patch.object(wp, "detach_admin_record", side_effect=detach_then_exchange):
            result = wp.remove(self.repo, linked, write=True)
        self.assertEqual(result["archive_path"], os.path.join(displaced_root, "worktree"))
        self.assertTrue(os.path.isfile(os.path.join(result["archive_path"], "tracked.txt")))
        self.assertTrue(os.path.isdir(result["metadata_archive_path"]))
        with open(os.path.join(archive_root, "foreign.txt"), "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "foreign\n")
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_remove_refuses_ignored_content_and_preserves_it(self):
        linked = self.add_tree("ignored-content")
        run = self.write_run(status="active")
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a", encoding="utf-8") as handle:
            handle.write("ignored.tmp\n")
        wp.register(self.repo, linked, run, write=True)
        sentinel = os.path.join(linked, "ignored.tmp")
        with open(sentinel, "w", encoding="utf-8") as handle:
            handle.write("must survive\n")
        self.write_run(status="done")
        tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == linked)
        self.assertEqual(tree["ignored_paths"], ["ignored.tmp"])
        self.assertIn("ignored-content", tree["blockers"])
        with self.assertRaises(wp.WorkspaceError):
            wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isfile(sentinel))

    def test_ignored_inventory_is_counted_but_path_sample_is_bounded(self):
        linked = self.add_tree("ignored-sample")
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a", encoding="utf-8") as handle:
            handle.write("ignored-*.tmp\n")
        for index in range(wp.IGNORED_PATH_SAMPLE_LIMIT + 5):
            with open(os.path.join(linked, "ignored-%02d.tmp" % index), "w", encoding="utf-8") as handle:
                handle.write("ignored\n")
        status = wp.worktree_status(linked)
        self.assertEqual(status["ignored_count"], wp.IGNORED_PATH_SAMPLE_LIMIT + 5)
        self.assertEqual(len(status["ignored_paths"]), wp.IGNORED_PATH_SAMPLE_LIMIT)
        self.assertTrue(status["ignored_paths_truncated"])

    def test_untracked_inventory_is_streamed_and_output_is_bounded(self):
        linked = self.add_tree("untracked-sample")
        for index in range(wp.UNTRACKED_PATH_SAMPLE_LIMIT + 25):
            with open(os.path.join(linked, "untracked-%03d.tmp" % index), "w", encoding="utf-8") as handle:
                handle.write("untracked\n")
        status = wp.worktree_status(linked)
        self.assertEqual(status["untracked"], wp.UNTRACKED_PATH_SAMPLE_LIMIT + 25)
        self.assertEqual(len(status["dirty_paths"]), wp.UNTRACKED_PATH_SAMPLE_LIMIT)
        self.assertTrue(status["dirty_paths_truncated"])
        self.assertLess(len(json.dumps(status)), 20000)

    def test_prune_refuses_registered_missing_worktree(self):
        linked = self.add_tree("registered-stale")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        shutil.rmtree(linked)
        status = wp.build_status(self.repo)
        tree = next(item for item in status["worktrees"] if item["path"] == linked)
        self.assertTrue(tree["prunable"])
        self.assertFalse(status["safe_prune_available"])
        self.assertIn({"path": linked, "reason": "registered-worktree-prunable"}, status["unresolved"])
        with self.assertRaises(wp.WorkspaceError):
            wp.prune(self.repo, write=True)
        registry = wp.read_registry(self.repo)
        self.assertEqual(len(registry["entries"]), 1)
        self.assertEqual(registry["entries"][0]["path"], linked)
        self.assertEqual(registry["entries"][0]["run"], run)
        self.assertIn(linked, [item["path"] for item in wp.worktree_records(self.repo)])

    def test_prune_reconciles_terminal_registry_after_remove_write_failure(self):
        linked = self.add_tree("reconcile")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        self.write_run(status="done")
        with mock.patch.object(wp, "write_registry", side_effect=wp.WorkspaceError("simulated write failure")):
            with self.assertRaises(wp.WorkspaceError):
                wp.remove(self.repo, linked, write=True)
        self.assertFalse(os.path.exists(linked))
        archives = [name for name in os.listdir(self.temp) if name.startswith(".reconcile.kimiflow-archive-")]
        self.assertEqual(len(archives), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.temp, archives[0], "worktree", "tracked.txt")))
        status = wp.build_status(self.repo)
        self.assertTrue(status["registry_reconcile_available"])
        result = wp.prune(self.repo, write=True)
        self.assertEqual(result["reconciled_paths"], [linked])
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])
        self.assertTrue(wp.build_status(self.repo)["can_register_temporary"])

    def test_locked_and_prunable_states_are_reported_and_pruned_safely(self):
        linked = self.add_tree("stale")
        self.git(self.repo, "worktree", "lock", linked)
        locked = next(tree for tree in wp.build_status(self.repo)["worktrees"] if tree["path"] == linked)
        self.assertTrue(locked["locked"])
        self.git(self.repo, "worktree", "unlock", linked)
        shutil.rmtree(linked)
        stale = next(tree for tree in wp.build_status(self.repo)["worktrees"] if tree["path"] == linked)
        self.assertTrue(stale["prunable"])
        self.assertTrue(wp.prune(self.repo, write=False)["available"])
        wp.prune(self.repo, write=True)
        self.assertEqual(wp.build_status(self.repo)["worktree_count"], 1)

    def test_repo_root_preserves_trailing_space(self):
        spaced_repo = os.path.join(self.temp, "repo ")
        os.mkdir(spaced_repo)
        self.git(spaced_repo, "init", "-b", "main")
        self.assertEqual(wp.repo_root(spaced_repo), os.path.realpath(spaced_repo))

    def test_rename_into_local_state_remains_dirty(self):
        os.mkdir(os.path.join(self.repo, ".kimiflow"))
        self.git(self.repo, "mv", "tracked.txt", ".kimiflow/tracked.txt")
        status = wp.build_status(self.repo)
        self.assertTrue(status["dirty"])
        self.assertEqual(status["dirty_paths"], [".kimiflow/tracked.txt", "tracked.txt"])

    def test_tracked_change_under_local_state_blocks_retirement(self):
        os.mkdir(os.path.join(self.repo, ".kimiflow"))
        tracked_state = os.path.join(self.repo, ".kimiflow", "tracked.txt")
        with open(tracked_state, "w", encoding="utf-8") as handle:
            handle.write("base\n")
        self.git(self.repo, "add", "-f", ".kimiflow/tracked.txt")
        self.git(self.repo, "commit", "-m", "tracked state fixture")
        linked = self.add_tree("tracked-state")
        run = self.write_run(status="active")
        wp.register(self.repo, linked, run, write=True)
        with open(os.path.join(linked, ".kimiflow", "tracked.txt"), "w", encoding="utf-8") as handle:
            handle.write("changed\n")
        self.write_run(status="done")
        tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == linked)
        self.assertTrue(tree["dirty"])
        self.assertIn(".kimiflow/tracked.txt", tree["dirty_paths"])
        with self.assertRaises(wp.WorkspaceError):
            wp.remove(self.repo, linked, write=True)
        self.assertTrue(os.path.isfile(os.path.join(linked, ".kimiflow", "tracked.txt")))

    def test_untracked_local_state_content_blocks_registration(self):
        linked = self.add_tree("untracked-state")
        valuable = os.path.join(linked, ".kimiflow", "valuable.txt")
        os.makedirs(os.path.dirname(valuable))
        with open(valuable, "w", encoding="utf-8") as handle:
            handle.write("valuable\n")
        run = self.write_run(status="active")
        tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == linked)
        self.assertTrue(tree["dirty"])
        self.assertIn(".kimiflow/valuable.txt", tree["dirty_paths"])
        with self.assertRaises(wp.WorkspaceError):
            wp.register(self.repo, linked, run, write=True)
        self.assertTrue(os.path.isfile(valuable))

    def test_non_utf8_status_path_serializes_as_valid_json(self):
        parsed = wp.parse_status_v2(b"? \xff.txt\0")
        self.assertEqual(parsed["dirty_paths"], ["\udcff.txt"])
        out = io.StringIO()
        with mock.patch.object(wp, "build_status", return_value=parsed), contextlib.redirect_stdout(out):
            self.assertEqual(wp.main(["status"]), 0)
        self.assertEqual(json.loads(out.getvalue())["dirty_paths"], ["\udcff.txt"])

    def test_codex_managed_tree_is_app_owned_and_never_registered(self):
        codex_home = os.path.join(self.temp, "codex-home")
        os.makedirs(os.path.join(codex_home, "worktrees"))
        linked = os.path.join(codex_home, "worktrees", "task-one")
        self.git(self.repo, "worktree", "add", "-b", "codex-task", linked)
        run = self.write_run()
        with mock.patch.dict(os.environ, {"CODEX_HOME": codex_home}):
            tree = next(item for item in wp.build_status(self.repo)["worktrees"] if item["path"] == os.path.realpath(linked))
            self.assertTrue(tree["codex_managed"])
            with self.assertRaises(wp.WorkspaceError):
                wp.register(self.repo, linked, run, write=True)


if __name__ == "__main__":
    unittest.main()
