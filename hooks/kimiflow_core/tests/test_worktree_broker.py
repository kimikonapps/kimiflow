import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from kimiflow_core import workspace_preflight as wp
from kimiflow_core import worktree_broker as broker


class WorktreeBrokerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.repo = os.path.realpath(os.path.join(self.temp, "repo"))
        os.mkdir(self.repo)
        self.git(self.repo, "init", "-b", "main")
        self.git(self.repo, "config", "user.email", "test@example.com")
        self.git(self.repo, "config", "user.name", "Test User")
        with open(os.path.join(self.repo, "tracked.txt"), "w", encoding="utf-8") as handle:
            handle.write("base\n")
        self.git(self.repo, "add", "tracked.txt")
        self.git(self.repo, "commit", "-m", "base")
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a", encoding="utf-8") as handle:
            handle.write("\n.kimiflow/\n")

    def git(self, root, *args, check=True):
        return subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )

    def write_active(self, run=".kimiflow/run-main", affected=()):
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.makedirs(session, exist_ok=True)
        with open(os.path.join(session, "ACTIVE_RUN.json"), "w", encoding="utf-8") as handle:
            json.dump({"run": run}, handle)
        run_root = os.path.join(self.repo, run)
        os.makedirs(run_root, exist_ok=True)
        with open(os.path.join(run_root, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 5\nStatus: active\n")
            if affected:
                handle.write("Affected files:\n")
                for path in affected:
                    handle.write("- %s\n" % path)

    def clear_active(self):
        os.unlink(os.path.join(self.repo, ".kimiflow", "session", "ACTIVE_RUN.json"))

    def write_terminal(self, root, run, status="done"):
        run_root = os.path.join(root, run)
        os.makedirs(run_root, exist_ok=True)
        with open(os.path.join(run_root, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Flow schema: 5\nStatus: %s\n" % status)

    def write_plan(self, root, run, content="# Plan\n"):
        run_root = os.path.join(root, run)
        os.makedirs(run_root, exist_ok=True)
        payload = content.encode("utf-8")
        with open(os.path.join(run_root, "PLAN.md"), "wb") as handle:
            handle.write(payload)
        return hashlib.sha256(payload).hexdigest()

    def commit_file(self, root, path, content, message):
        absolute = os.path.join(root, path)
        os.makedirs(os.path.dirname(absolute), exist_ok=True)
        with open(absolute, "w", encoding="utf-8") as handle:
            handle.write(content)
        self.git(root, "add", path)
        self.git(root, "commit", "-m", message)
        return self.git(root, "rev-parse", "HEAD").stdout.strip()

    def ignore(self, pattern):
        with open(
            os.path.join(self.repo, ".git", "info", "exclude"),
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(pattern + "\n")

    def allocate(self, run=".kimiflow/run-a", affected=("src/main.py",)):
        self.write_active(affected=affected)
        result = broker.route(self.repo, run, write=True)
        self.assertEqual(result["status"], "allocated")
        return os.path.realpath(result["root"])

    def integrated_task(self, run=".kimiflow/run-a"):
        target = self.allocate(run)
        self.clear_active()
        self.commit_file(self.repo, "main-only.txt", "main\n", "main change")
        basis = self.write_plan(target, run)
        declaration = broker.declare(
            target,
            run,
            basis,
            paths=["feature.txt"],
            write=True,
        )
        self.assertEqual(declaration["action"], "disjoint")
        self.commit_file(target, "feature.txt", "task\n", "task change")
        check = json.dumps(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('feature.txt').read_text() == 'task\\n'",
            ]
        )
        result = broker.integrate(self.repo, run, checks=[check], write=True)
        self.assertEqual(result["status"], "integrated")
        return target, basis, result

    def test_route_recovers_allocation_then_fills_fleet_and_queues(self):
        self.write_active()
        run = ".kimiflow/run-a"
        with mock.patch.object(
            broker.wp,
            "register",
            side_effect=wp.WorkspaceError("injected publication failure"),
        ):
            with self.assertRaises(wp.WorkspaceError):
                broker.route(self.repo, run, write=True)
        interrupted = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual(interrupted["state"], "allocating")
        self.assertTrue(os.path.isdir(interrupted["path"]))
        self.assertEqual(
            self.git(self.repo, "worktree", "list", "--porcelain").stdout.count("worktree "),
            2,
        )

        recovered = broker.route(self.repo, run, write=True)
        self.assertEqual(recovered["status"], "allocated")
        registry = wp.read_registry(self.repo)
        self.assertEqual(len(registry["entries"]), 1)
        self.assertEqual(registry["entries"][0]["path"], interrupted["path"])

        self.assertEqual(
            broker.route(self.repo, ".kimiflow/run-b", write=True)["status"],
            "allocated",
        )
        self.assertEqual(
            broker.route(self.repo, ".kimiflow/run-c", write=True)["status"],
            "allocated",
        )
        queued = broker.route(self.repo, ".kimiflow/run-d", write=True)
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["queue_position"], 1)
        self.assertEqual(
            self.git(self.repo, "worktree", "list", "--porcelain").stdout.count("worktree "),
            4,
        )

    def test_route_recovers_matching_receipt_after_registry_publish_crash(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        state = broker.read_broker(self.repo)
        task = state["tasks"][0]
        task["state"] = "allocating"
        task["journal"] = {
            "kind": "allocation",
            "main": task["base"],
            "task": task["base"],
        }
        with wp.registry_operation(self.repo, True) as descriptor:
            broker._write_broker(descriptor, state)
            wp.write_registry(
                self.repo,
                {"schema_version": 1, "entries": []},
                descriptor,
            )
        session = os.path.join(self.repo, ".kimiflow", "session")
        registry = os.path.join(session, "WORKTREE_REGISTRY.json")
        backup = os.path.join(
            session,
            ".kimiflow-backup-WORKTREE_REGISTRY.json-hard-crash",
        )
        os.rename(registry, backup)

        recovered = broker.route(self.repo, run, write=True)

        self.assertEqual(recovered["status"], "allocated")
        entries = wp.read_registry(self.repo)["entries"]
        self.assertEqual(
            entries,
            [
                {
                    "path": target,
                    "run": run,
                    "identity": task["identity"],
                }
            ],
        )
        self.assertFalse(os.path.exists(backup))

    def test_route_resumes_allocation_missing_before_worktree_add(self):
        self.write_active()
        run = ".kimiflow/run-a"
        original_git = broker._git
        injected = False

        def fail_first_worktree_add(root, args, check=True):
            nonlocal injected
            if not injected and args[:2] == ["worktree", "add"]:
                injected = True
                raise wp.WorkspaceError("injected pre-allocation crash")
            return original_git(root, args, check=check)

        with mock.patch.object(
            broker, "_git", side_effect=fail_first_worktree_add
        ):
            with self.assertRaisesRegex(
                wp.WorkspaceError, "injected pre-allocation crash"
            ):
                broker.route(self.repo, run, write=True)

        interrupted = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual(interrupted["state"], "allocating")
        self.assertFalse(os.path.exists(interrupted["path"]))
        self.clear_active()

        resumed = broker.route(self.repo, run, write=True)

        self.assertEqual(
            (resumed["status"], resumed["route"]),
            ("allocated", "worktree"),
        )
        self.assertTrue(os.path.isdir(resumed["root"]))
        stored = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual((stored["state"], stored["journal"]), ("allocated", None))

    def test_allocation_resume_rejects_a_late_foreign_worktree(self):
        self.write_active()
        run = ".kimiflow/run-a"
        original_git = broker._git
        injected = False

        def fail_first_worktree_add(root, args, check=True):
            nonlocal injected
            if not injected and args[:2] == ["worktree", "add"]:
                injected = True
                raise wp.WorkspaceError("injected pre-allocation crash")
            return original_git(root, args, check=check)

        with mock.patch.object(
            broker, "_git", side_effect=fail_first_worktree_add
        ):
            with self.assertRaisesRegex(
                wp.WorkspaceError, "injected pre-allocation crash"
            ):
                broker.route(self.repo, run, write=True)

        interrupted = broker._task_for(broker.read_broker(self.repo), run)
        foreign = os.path.join(self.temp, "late-foreign")
        self.git(
            self.repo,
            "worktree",
            "add",
            "-b",
            "late-foreign",
            foreign,
        )

        with self.assertRaisesRegex(
            wp.WorkspaceError, "foreign worktree present"
        ):
            broker.route(self.repo, run, write=True)

        stored = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual(stored["state"], "allocating")
        self.assertFalse(os.path.exists(interrupted["path"]))
        self.assertTrue(os.path.isdir(foreign))

    def test_allocation_recovery_reproves_pinned_receipt_identity_and_lock(self):
        run_a = ".kimiflow/run-a"
        run_b = ".kimiflow/run-b"
        target_a = self.allocate(run_a)
        target_b = broker.route(self.repo, run_b, write=True)["root"]
        state = broker.read_broker(self.repo)
        task_a = broker._task_for(state, run_a)
        task_a["state"] = "allocating"
        task_a["journal"] = {
            "kind": "allocation",
            "main": task_a["base"],
            "task": task_a["base"],
        }
        with wp.registry_operation(self.repo, True) as descriptor:
            broker._write_broker(descriptor, state)
        registry_before = wp.read_registry(self.repo)
        peer_head = self.git(target_b, "rev-parse", "HEAD").stdout.strip()

        self.git(self.repo, "worktree", "unlock", target_a)
        self.git(
            self.repo,
            "worktree",
            "lock",
            "--reason",
            "kimiflow:forged:.kimiflow/foreign",
            target_a,
        )
        with self.assertRaisesRegex(
            wp.WorkspaceError, "ownership proof mismatch"
        ):
            broker.route(self.repo, run_a, write=True)

        stored = broker._task_for(broker.read_broker(self.repo), run_a)
        self.assertEqual(stored["state"], "allocating")
        self.assertEqual(stored["journal"]["kind"], "allocation")
        self.assertEqual(wp.read_registry(self.repo), registry_before)
        self.assertEqual(
            self.git(target_b, "rev-parse", "HEAD").stdout.strip(),
            peer_head,
        )
        self.assertTrue(os.path.isdir(target_a))
        self.assertTrue(os.path.isdir(target_b))

        self.git(self.repo, "worktree", "unlock", target_a)
        self.git(
            self.repo,
            "worktree",
            "lock",
            "--reason",
            broker._lock_reason(task_a),
            target_a,
        )
        with open(
            wp.owner_receipt_path(target_a), "w", encoding="utf-8"
        ) as handle:
            json.dump({}, handle)
        with self.assertRaisesRegex(
            wp.WorkspaceError, "ownership proof mismatch"
        ):
            broker.route(self.repo, run_a, write=True)

        stored = broker._task_for(broker.read_broker(self.repo), run_a)
        self.assertEqual(stored["state"], "allocating")
        self.assertEqual(stored["journal"]["kind"], "allocation")
        self.assertEqual(wp.read_registry(self.repo), registry_before)
        self.assertEqual(
            self.git(target_b, "rev-parse", "HEAD").stdout.strip(),
            peer_head,
        )

    def test_broker_write_heals_a_single_hard_crash_backup(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        session = os.path.join(self.repo, ".kimiflow", "session")
        broker_path = os.path.join(session, broker.BROKER_NAME)
        backup = os.path.join(
            session,
            ".kimiflow-backup-%s-hard-crash" % broker.BROKER_NAME,
        )
        os.rename(broker_path, backup)

        self.assertEqual(len(broker.read_broker(self.repo)["tasks"]), 1)
        basis = self.write_plan(target, run)
        broker.declare(
            target,
            run,
            basis,
            paths=["feature.txt"],
            write=True,
        )

        self.assertTrue(os.path.isfile(broker_path))
        self.assertFalse(os.path.exists(backup))
        self.assertEqual(len(broker.read_broker(self.repo)["tasks"]), 1)

    def test_concurrent_routes_publish_two_owned_fleet_entries(self):
        self.write_active()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda run: broker.route(self.repo, run, write=True),
                    (".kimiflow/run-a", ".kimiflow/run-b"),
                )
            )
        self.assertEqual(sorted(result["status"] for result in results), ["allocated", "allocated"])
        self.assertEqual(len(wp.read_registry(self.repo)["entries"]), 2)
        self.assertEqual(
            self.git(self.repo, "worktree", "list", "--porcelain").stdout.count("worktree "),
            3,
        )

    def test_fifo_queue_cannot_be_bypassed_when_main_becomes_free(self):
        target = self.allocate(".kimiflow/run-a")
        allocated_b = broker.route(self.repo, ".kimiflow/run-b", write=True)
        allocated_c = broker.route(self.repo, ".kimiflow/run-c", write=True)
        queued_d = broker.route(self.repo, ".kimiflow/run-d", write=True)
        queued_e = broker.route(self.repo, ".kimiflow/run-e", write=True)
        self.assertEqual((allocated_b["status"], allocated_c["status"]), ("allocated", "allocated"))
        self.assertEqual((queued_d["queue_position"], queued_e["queue_position"]), (1, 2))

        self.clear_active()
        still_queued = broker.route(self.repo, ".kimiflow/run-e", write=True)
        self.assertEqual((still_queued["status"], still_queued["queue_position"]), ("queued", 2))
        next_ready = broker.route(self.repo, ".kimiflow/run-d", write=True)
        self.assertEqual((next_ready["status"], next_ready["route"]), ("direct", "main"))
        self.write_active(run=".kimiflow/run-d")
        still_queued = broker.route(self.repo, ".kimiflow/run-e", write=True)
        self.assertEqual((still_queued["status"], still_queued["queue_position"]), ("queued", 1))

    def test_promoted_direct_queue_head_ignores_later_queued_lease(self):
        self.write_active(affected=("primary-only.txt",))
        fleet = []
        for name in ("a", "b", "c"):
            run = ".kimiflow/run-%s" % name
            target = broker.route(self.repo, run, write=True)["root"]
            basis = self.write_plan(target, run)
            declaration = broker.declare(
                target,
                run,
                basis,
                paths=["fleet-%s.txt" % name],
                write=True,
            )
            self.assertEqual(declaration["action"], "disjoint")
            fleet.append((run, target, basis))
        run_d = ".kimiflow/run-d"
        run_e = ".kimiflow/run-e"
        self.assertEqual(
            broker.route(self.repo, run_d, write=True)["queue_position"],
            1,
        )
        self.assertEqual(
            broker.route(self.repo, run_e, write=True)["queue_position"],
            2,
        )

        self.clear_active()
        promoted = broker.route(self.repo, run_d, write=True)
        self.assertEqual(
            (promoted["status"], promoted["route"]),
            ("direct", "main"),
        )
        self.write_active(run=run_d, affected=("direct-d.txt",))
        direct_basis = self.write_plan(self.repo, run_d)

        direct_gate = broker.write_gate(self.repo, run_d, direct_basis)
        queued = broker.route(self.repo, run_e, write=True)

        self.assertEqual(
            (direct_gate["status"], direct_gate["reason"]),
            ("OPEN", "direct-main"),
        )
        self.assertEqual(
            (queued["status"], queued["queue_position"]),
            ("queued", 1),
        )
        for run, _, _ in fleet:
            integration = broker.integrate(
                self.repo,
                run,
                write=True,
            )
            self.assertEqual(
                (integration["status"], integration["reason"]),
                ("ready-to-integrate", "primary-busy"),
            )

    def test_fleet_allocates_three_and_queues_fourth_with_legacy_migration(self):
        self.write_active(affected=("primary-only.txt",))
        results = [
            broker.route(self.repo, ".kimiflow/run-%s" % name, write=True)
            for name in ("a", "b", "c", "d")
        ]
        self.assertEqual(
            [result["status"] for result in results],
            ["allocated", "allocated", "allocated", "queued"],
        )
        self.assertEqual(len(wp.read_registry(self.repo)["entries"]), 3)
        state = broker.read_broker(self.repo)
        legacy = {
            "schema_version": 1,
            "tasks": [
                {key: task[key] for key in broker.LEGACY_TASK_KEYS}
                for task in state["tasks"]
            ],
        }
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.unlink(os.path.join(session, broker.BROKER_NAME))
        with open(
            os.path.join(session, broker.LEGACY_BROKER_NAME),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(legacy, handle)

        routed = broker.route(self.repo, ".kimiflow/run-d", write=True)

        self.assertEqual((routed["status"], routed["queue_position"]), ("queued", 1))
        self.assertTrue(os.path.isfile(os.path.join(session, broker.BROKER_NAME)))
        self.assertTrue(os.path.isfile(os.path.join(session, broker.LEGACY_ARCHIVE_NAME)))
        self.assertFalse(os.path.exists(os.path.join(session, broker.LEGACY_BROKER_NAME)))
        self.assertEqual(len(broker.read_broker(self.repo)["tasks"]), 4)

    def test_legacy_migration_requires_revalidation_before_integration(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        basis = self.write_plan(target, run)
        broker.declare(
            target,
            run,
            basis,
            paths=["feature.txt"],
            write=True,
        )
        self.clear_active()
        task_head = self.commit_file(
            target, "feature.txt", "task\n", "task change"
        )
        state = broker.read_broker(self.repo)
        legacy = {
            "schema_version": 1,
            "tasks": [
                {
                    key: broker._task_for(state, run)[key]
                    for key in broker.LEGACY_TASK_KEYS
                }
            ],
        }
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.unlink(os.path.join(session, broker.BROKER_NAME))
        with open(
            os.path.join(session, broker.LEGACY_BROKER_NAME),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(legacy, handle)
        self.assertEqual(
            broker.write_gate(target, run, basis)["reason"],
            "primary-snapshot-invalid",
        )
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        result = broker.integrate(
            self.repo, run, checks=[check], write=True
        )

        self.assertEqual(
            (result["status"], result["reason"]),
            ("needs-reconcile", "primary-snapshot-invalid"),
        )
        self.assertEqual(
            self.git(self.repo, "rev-parse", "HEAD").stdout.strip(),
            main_before,
        )
        self.assertNotEqual(task_head, main_before)
        self.assertEqual(
            broker._task_for(broker.read_broker(self.repo), run)["state"],
            "needs-reconcile",
        )

    def test_legacy_archive_blocks_state_resurrection(self):
        self.write_active(affected=("primary-only.txt",))
        run = ".kimiflow/run-a"
        target = broker.route(self.repo, run, write=True)["root"]
        state = broker.read_broker(self.repo)
        session = os.path.join(self.repo, ".kimiflow", "session")
        legacy = {
            "schema_version": 1,
            "tasks": [
                {key: task[key] for key in broker.LEGACY_TASK_KEYS}
                for task in state["tasks"]
            ],
        }
        os.unlink(os.path.join(session, broker.BROKER_NAME))
        with open(os.path.join(session, broker.LEGACY_BROKER_NAME), "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["task.txt"], write=True)
        os.unlink(os.path.join(session, broker.BROKER_NAME))

        with self.assertRaisesRegex(wp.WorkspaceError, "missing after legacy migration"):
            broker.read_broker(self.repo)

    def test_legacy_backup_only_recovers_and_migrates_without_state_loss(self):
        run = ".kimiflow/run-a"
        task = broker._new_task(run)
        legacy = {
            "schema_version": 1,
            "tasks": [
                {key: task[key] for key in broker.LEGACY_TASK_KEYS}
            ],
        }
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.makedirs(session, exist_ok=True)
        backup = os.path.join(
            session,
            ".kimiflow-backup-%s-hard-crash" % broker.LEGACY_BROKER_NAME,
        )
        with open(backup, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)

        recovered = broker.read_broker(self.repo)
        self.assertEqual(
            (recovered["schema_version"], recovered["tasks"][0]["run"]),
            (2, run),
        )
        self.assertEqual(recovered["tasks"][0]["state"], "queued")
        self.assertEqual(recovered["tasks"][0]["blocked_by"], [])
        with wp.registry_operation(self.repo, True) as descriptor:
            broker._write_broker(descriptor, recovered)

        self.assertFalse(os.path.exists(backup))
        self.assertFalse(
            os.path.exists(os.path.join(session, broker.LEGACY_BROKER_NAME))
        )
        self.assertTrue(
            os.path.isfile(os.path.join(session, broker.LEGACY_ARCHIVE_NAME))
        )
        self.assertEqual(
            broker.read_broker(self.repo)["tasks"][0]["identity"],
            task["identity"],
        )
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_legacy_backup_only_recovery_rejects_ambiguity_and_unsafe_bytes(self):
        task = broker._new_task(".kimiflow/run-a")
        legacy = {
            "schema_version": 1,
            "tasks": [
                {key: task[key] for key in broker.LEGACY_TASK_KEYS}
            ],
        }
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.makedirs(session, exist_ok=True)
        prefix = ".kimiflow-backup-%s-" % broker.LEGACY_BROKER_NAME
        for suffix in ("one", "two"):
            with open(
                os.path.join(session, prefix + suffix),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(legacy, handle)
        with self.assertRaisesRegex(wp.WorkspaceError, "ambiguous"):
            broker.read_broker(self.repo)

        os.unlink(os.path.join(session, prefix + "one"))
        os.unlink(os.path.join(session, prefix + "two"))
        outside = os.path.join(self.temp, "legacy-backup.json")
        with open(outside, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)
        os.symlink(outside, os.path.join(session, prefix + "unsafe"))
        with self.assertRaisesRegex(wp.WorkspaceError, "unsafe"):
            broker.read_broker(self.repo)

    def test_live_registry_without_fleet_state_fails_closed(self):
        self.allocate()
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.unlink(os.path.join(session, broker.BROKER_NAME))

        with self.assertRaisesRegex(wp.WorkspaceError, "missing for live Registry"):
            broker.read_broker(self.repo)
        with open(
            os.path.join(session, broker.BROKER_NAME),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump({"schema_version": 2, "tasks": []}, handle)
        with self.assertRaisesRegex(wp.WorkspaceError, "does not own live Registry"):
            broker.read_broker(self.repo)

    def test_overlapping_leases_name_single_blocker_and_disjoint_open(self):
        self.write_active(affected=("primary-only.txt",))
        run_a = ".kimiflow/run-a"
        run_b = ".kimiflow/run-b"
        target_a = broker.route(self.repo, run_a, write=True)["root"]
        target_b = broker.route(self.repo, run_b, write=True)["root"]
        basis_a = self.write_plan(target_a, run_a)
        basis_b = self.write_plan(target_b, run_b)

        blocked = broker.declare(
            target_b, run_b, basis_b, paths=["src/shared.py"], write=True
        )
        identity_a = broker._task_for(broker.read_broker(self.repo), run_a)["identity"]
        self.assertEqual(blocked["blocked_by"], [identity_a])
        winner = broker.declare(
            target_a, run_a, basis_a, paths=["src/shared.py"], write=True
        )
        self.assertEqual(winner["action"], "disjoint")
        task_b = broker._task_for(broker.read_broker(self.repo), run_b)
        self.assertEqual(task_b["blocked_by"], [identity_a])
        disjoint = broker.declare(
            target_b, run_b, basis_b, paths=["docs/b.md"], write=True
        )
        self.assertEqual((disjoint["action"], disjoint["blocked_by"]), ("disjoint", []))
        self.assertEqual(broker.write_gate(target_a, run_a, basis_a)["status"], "OPEN")
        self.assertEqual(broker.write_gate(target_b, run_b, basis_b)["status"], "OPEN")

    def test_winning_lease_integrates_while_overlapping_loser_waits(self):
        self.write_active(affected=("primary-only.txt",))
        run_a = ".kimiflow/run-a"
        run_b = ".kimiflow/run-b"
        target_a = broker.route(self.repo, run_a, write=True)["root"]
        target_b = broker.route(self.repo, run_b, write=True)["root"]
        basis_a = self.write_plan(target_a, run_a)
        basis_b = self.write_plan(target_b, run_b)
        loser = broker.declare(
            target_b, run_b, basis_b, paths=["src/shared.py"], write=True
        )
        winner = broker.declare(
            target_a, run_a, basis_a, paths=["src/shared.py"], write=True
        )
        self.assertEqual((winner["action"], loser["action"]), ("disjoint", "serialize"))
        self.clear_active()
        self.commit_file(target_a, "src/shared.py", "winner\n", "winner")
        check = json.dumps(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('src/shared.py').read_text() == 'winner\\n'",
            ]
        )

        delivered = broker.integrate(
            self.repo, run_a, checks=[check], write=True
        )

        self.assertEqual(delivered["status"], "integrated")
        task_b = broker._task_for(broker.read_broker(self.repo), run_b)
        self.assertEqual(task_b["state"], "waiting")

    def test_redeclared_lease_cannot_bypass_primary_plan_drift(self):
        run = ".kimiflow/run-a"
        self.write_active(affected=("src/primary.py",))
        target = broker.route(self.repo, run, write=True)["root"]
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["src/task.py"], write=True)
        self.write_active(affected=("src/new-overlap.py",))

        redeclared = broker.declare(
            target,
            run,
            basis,
            paths=["src/task.py", "src/new-overlap.py"],
            write=True,
        )

        task = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual(redeclared["action"], "serialize")
        self.assertEqual(
            task["paths"], ["src/new-overlap.py", "src/task.py"]
        )
        self.assertEqual(
            broker.write_gate(target, run, basis)["status"], "CLOSED"
        )

    def test_redeclared_lease_moves_behind_established_peer(self):
        self.write_active(affected=("src/primary.py",))
        run_a = ".kimiflow/run-a"
        run_b = ".kimiflow/run-b"
        target_a = broker.route(self.repo, run_a, write=True)["root"]
        target_b = broker.route(self.repo, run_b, write=True)["root"]
        basis_a = self.write_plan(target_a, run_a)
        basis_b = self.write_plan(target_b, run_b)
        broker.declare(
            target_a, run_a, basis_a, paths=["src/a.py"], write=True
        )
        broker.declare(
            target_b, run_b, basis_b, paths=["src/b.py"], write=True
        )
        identity_b = broker._task_for(
            broker.read_broker(self.repo), run_b
        )["identity"]

        redeclared = broker.declare(
            target_a, run_a, basis_a, paths=["src/b.py"], write=True
        )

        self.assertEqual(
            (redeclared["action"], redeclared["blocked_by"]),
            ("serialize", [identity_b]),
        )

    def test_blocked_lease_redeclaration_moves_behind_established_peer(self):
        self.write_active(affected=("src/primary-collision.py",))
        run_a = ".kimiflow/run-a"
        run_b = ".kimiflow/run-b"
        target_a = broker.route(self.repo, run_a, write=True)["root"]
        target_b = broker.route(self.repo, run_b, write=True)["root"]
        basis_a = self.write_plan(target_a, run_a)
        basis_b = self.write_plan(target_b, run_b)

        blocked_a = broker.declare(
            target_a,
            run_a,
            basis_a,
            paths=["src/primary-collision.py"],
            write=True,
        )
        established_b = broker.declare(
            target_b,
            run_b,
            basis_b,
            paths=["src/established-b.py"],
            write=True,
        )
        self.assertEqual(blocked_a["action"], "serialize")
        self.assertEqual(established_b["action"], "disjoint")
        self.assertEqual(
            broker.write_gate(target_b, run_b, basis_b)["status"],
            "OPEN",
        )
        self.assertEqual(
            [task["run"] for task in broker.read_broker(self.repo)["tasks"]],
            [run_a, run_b],
        )
        idempotent_b = broker.declare(
            target_b,
            run_b,
            basis_b,
            paths=["src/established-b.py"],
            write=True,
        )
        self.assertEqual(idempotent_b["action"], "disjoint")
        self.assertEqual(
            [task["run"] for task in broker.read_broker(self.repo)["tasks"]],
            [run_a, run_b],
        )
        identity_b = broker._task_for(
            broker.read_broker(self.repo), run_b
        )["identity"]

        redeclared_a = broker.declare(
            target_a,
            run_a,
            basis_a,
            paths=["src/established-b.py"],
            write=True,
        )

        self.assertEqual(
            (redeclared_a["action"], redeclared_a["blocked_by"]),
            ("serialize", [identity_b]),
        )
        self.assertEqual(
            broker.write_gate(target_b, run_b, basis_b)["status"],
            "OPEN",
        )
        gate_a = broker.write_gate(target_a, run_a, basis_a)
        self.assertEqual(
            (gate_a["status"], gate_a["reason"], gate_a["blocked_by"]),
            ("CLOSED", "serialize", [identity_b]),
        )
        self.assertEqual(
            [task["run"] for task in broker.read_broker(self.repo)["tasks"]],
            [run_b, run_a],
        )

    def test_primary_advance_and_dirty_paths_close_worktree_authority(self):
        run_a = ".kimiflow/run-a"
        target_a = self.allocate(run_a, affected=("src/primary.py",))
        self.clear_active()
        self.commit_file(
            self.repo, "src/shared.py", "primary\n", "primary advance"
        )
        basis_a = self.write_plan(target_a, run_a)

        declaration = broker.declare(
            target_a,
            run_a,
            basis_a,
            paths=["src/shared.py"],
            write=True,
        )

        self.assertEqual(declaration["action"], "serialize")
        self.assertEqual(len(declaration["blocked_by"]), 1)

        run_b = ".kimiflow/run-b"
        self.write_active(affected=("src/primary.py",))
        target_b = broker.route(self.repo, run_b, write=True)["root"]
        basis_b = self.write_plan(target_b, run_b)
        cache = os.path.join(self.repo, ".kimiflow", "cache")
        os.makedirs(cache, exist_ok=True)
        for index in range(25):
            with open(
                os.path.join(cache, "artifact-%s" % index),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("local\n")
        broker.declare(
            target_b, run_b, basis_b, paths=["src/task.py"], write=True
        )
        with open(
            os.path.join(self.repo, "src", "task.py"), "w", encoding="utf-8"
        ) as handle:
            handle.write("uncommitted primary\n")

        gate = broker.write_gate(target_b, run_b, basis_b)

        self.assertEqual(
            (gate["status"], gate["reason"]),
            ("CLOSED", "primary-dirty-collision"),
        )

    def test_foreign_tree_added_after_allocation_closes_worktree_gate(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run, affected=("src/primary.py",))
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["src/task.py"], write=True)
        foreign = os.path.join(self.temp, "late-foreign")
        self.git(self.repo, "worktree", "add", "-b", "late-foreign", foreign)

        gate = broker.write_gate(target, run, basis)

        self.assertEqual(
            (gate["status"], gate["reason"]),
            ("CLOSED", "foreign-worktree-ambiguous"),
        )

    def test_primary_and_worktree_leases_are_mutually_exclusive(self):
        run = ".kimiflow/run-a"
        self.write_active(affected=("src/primary.py",))
        primary_basis = self.write_plan(
            self.repo, ".kimiflow/run-main", "# Primary plan\n"
        )
        target = broker.route(self.repo, run, write=True)["root"]
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["src/task.py"], write=True)

        self.write_active(affected=("src/task.py",))
        direct = broker.write_gate(
            self.repo, ".kimiflow/run-main", primary_basis
        )

        task = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual((direct["status"], direct["blocked_by"]), ("CLOSED", [task["identity"]]))
        self.assertEqual(broker.write_gate(target, run, basis)["status"], "OPEN")
        active_path = os.path.join(
            self.repo, ".kimiflow", "session", "ACTIVE_RUN.json"
        )
        with open(active_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "run": ".kimiflow/run-main",
                    "owner": {"session_id": "changed-owner"},
                },
                handle,
            )
        self.assertEqual(
            broker.write_gate(target, run, basis)["reason"],
            "primary-owner-drift",
        )
        self.assertEqual(
            broker.write_gate(
                self.repo, ".kimiflow/run-main", primary_basis
            )["status"],
            "CLOSED",
        )

    def test_direct_primary_plan_drift_restart_preserves_worktree_precedence(self):
        task_run = ".kimiflow/run-a"
        primary_run = ".kimiflow/run-main"
        self.write_active(run=primary_run, affected=("src/primary.py",))
        original_primary_basis = self.write_plan(
            self.repo, primary_run, "# Primary v1\n"
        )
        target = broker.route(self.repo, task_run, write=True)["root"]
        task_basis = self.write_plan(target, task_run)
        broker.declare(
            target,
            task_run,
            task_basis,
            paths=["src/task.py"],
            write=True,
        )
        self.assertEqual(
            broker.write_gate(target, task_run, task_basis)["status"],
            "OPEN",
        )

        drifted_primary_basis = self.write_plan(
            self.repo, primary_run, "# Primary v2\n"
        )
        self.write_active(run=primary_run, affected=("src/task.py",))
        stale = broker.write_gate(
            self.repo, primary_run, original_primary_basis
        )
        self.assertEqual(
            (stale["status"], stale["reason"]),
            ("CLOSED", "stale-plan-basis"),
        )
        plan_path = os.path.join(self.repo, primary_run, "PLAN.md")
        os.unlink(plan_path)
        missing = broker.write_gate(
            self.repo, primary_run, drifted_primary_basis
        )
        self.assertEqual(
            (missing["status"], missing["reason"]),
            ("CLOSED", "stale-plan-basis"),
        )
        self.assertEqual(
            self.write_plan(self.repo, primary_run, "# Primary v2\n"),
            drifted_primary_basis,
        )

        helper = os.path.join(
            os.path.dirname(os.path.dirname(broker.__file__)),
            "workspace-preflight.sh",
        )

        def restarted_gate(root, run, basis):
            proc = subprocess.run(
                [
                    helper,
                    "write-gate",
                    "--root",
                    root,
                    "--run",
                    run,
                    "--basis",
                    basis,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            return json.loads(proc.stdout)

        direct = restarted_gate(
            self.repo, primary_run, drifted_primary_basis
        )
        worktree = restarted_gate(target, task_run, task_basis)
        task = broker._task_for(
            broker.read_broker(self.repo), task_run
        )
        self.assertEqual(
            (direct["status"], direct["reason"], direct["blocked_by"]),
            ("CLOSED", "lease-conflict", [task["identity"]]),
        )
        self.assertEqual(worktree["status"], "OPEN")
        self.assertLessEqual(
            sum(result["status"] == "OPEN" for result in (direct, worktree)),
            1,
        )

    def test_primary_advance_closes_write_gate_until_revalidated(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run, affected=("primary-only.txt",))
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["task.txt"], write=True)
        self.clear_active()
        self.commit_file(self.repo, "predecessor.txt", "done\n", "predecessor")

        self.assertEqual(
            broker.write_gate(target, run, basis)["reason"],
            "revalidation-required",
        )
        refreshed = broker.revalidate(target, run, basis, write=True)
        self.assertEqual(refreshed["status"], "allocated")
        self.assertEqual(broker.write_gate(target, run, basis)["status"], "OPEN")
        self.commit_file(target, "task.txt", "task\n", "task")
        self.commit_file(self.repo, "task.txt", "primary\n", "colliding predecessor")
        drift = broker.revalidate(target, run, basis, write=True)
        self.assertEqual(drift["status"], "needs-reconcile")
        main_now = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(
            self.git(target, "merge", "--no-edit", main_now, check=False).returncode,
            0,
        )
        with open(os.path.join(target, "task.txt"), "w", encoding="utf-8") as handle:
            handle.write("resolved\n")
        self.git(target, "add", "task.txt")
        self.git(target, "commit", "-m", "resolve predecessor")
        recovered = broker.revalidate(target, run, basis, write=True)
        self.assertEqual(recovered["status"], "allocated")

    def test_revalidate_rejects_unlocked_task_identity(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run, affected=("primary-only.txt",))
        basis = self.write_plan(target, run)
        broker.declare(
            target, run, basis, paths=["task.txt"], write=True
        )
        self.clear_active()
        self.commit_file(
            self.repo, "predecessor.txt", "done\n", "predecessor"
        )
        task_before = self.git(target, "rev-parse", "HEAD").stdout.strip()
        self.git(self.repo, "worktree", "unlock", target)

        with self.assertRaisesRegex(
            wp.WorkspaceError, "revalidation ownership unproven"
        ):
            broker.revalidate(target, run, basis, write=True)

        self.assertEqual(
            self.git(target, "rev-parse", "HEAD").stdout.strip(),
            task_before,
        )

    def test_revalidate_rejects_forged_lock_identity(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run, affected=("primary-only.txt",))
        basis = self.write_plan(target, run)
        broker.declare(
            target, run, basis, paths=["task.txt"], write=True
        )
        self.clear_active()
        self.commit_file(
            self.repo, "predecessor.txt", "done\n", "predecessor"
        )
        task_before = self.git(target, "rev-parse", "HEAD").stdout.strip()
        self.git(self.repo, "worktree", "unlock", target)
        self.git(
            self.repo,
            "worktree",
            "lock",
            "--reason",
            "kimiflow:forged:.kimiflow/foreign",
            target,
        )

        with self.assertRaisesRegex(
            wp.WorkspaceError, "revalidation ownership unproven"
        ):
            broker.revalidate(target, run, basis, write=True)

        self.assertEqual(
            self.git(target, "rev-parse", "HEAD").stdout.strip(),
            task_before,
        )

    def test_disjoint_fleet_integrates_combined_candidate_and_conflicts_resume(self):
        self.write_active(affected=("primary-only.txt",))
        run_a = ".kimiflow/run-a"
        run_b = ".kimiflow/run-b"
        target_a = broker.route(self.repo, run_a, write=True)["root"]
        target_b = broker.route(self.repo, run_b, write=True)["root"]
        basis_a = self.write_plan(target_a, run_a)
        basis_b = self.write_plan(target_b, run_b)
        broker.declare(target_a, run_a, basis_a, paths=["a.txt"], write=True)
        broker.declare(target_b, run_b, basis_b, paths=["b.txt"], write=True)
        self.clear_active()
        self.commit_file(target_a, "a.txt", "a\n", "a")
        self.commit_file(target_b, "b.txt", "b\n", "b")
        cache = os.path.join(target_b, ".kimiflow", "cache")
        os.makedirs(cache, exist_ok=True)
        for index in range(25):
            with open(
                os.path.join(cache, "artifact-%s" % index),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("local\n")
        passing_a = json.dumps(
            [sys.executable, "-c", "from pathlib import Path; assert Path('a.txt').read_text() == 'a\\n'"]
        )
        self.assertEqual(
            broker.integrate(self.repo, run_a, checks=[passing_a], write=True)["status"],
            "integrated",
        )
        self.assertEqual(
            broker.integrate(self.repo, run_b, checks=[passing_a], write=True)["status"],
            "needs-reconcile",
        )
        broker.revalidate(target_b, run_b, basis_b, write=True)
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        failing = json.dumps([sys.executable, "-c", "raise SystemExit(1)"])
        failed = broker.integrate(self.repo, run_b, checks=[failing], write=True)
        self.assertEqual(failed["status"], "verification-failed", failed)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)
        combined = json.dumps(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('a.txt').read_text() == 'a\\n'; assert Path('b.txt').read_text() == 'b\\n'",
            ]
        )
        delivered = broker.integrate(self.repo, run_b, checks=[combined], write=True)
        self.assertEqual(delivered["status"], "integrated")
        task_b = broker._task_for(broker.read_broker(self.repo), run_b)
        self.assertEqual(task_b["verified_main"], main_before)
        self.assertEqual(task_b["verified_task"], delivered["integrated_head"])

    def test_declare_cas_and_write_gate_resolve_collisions_conservatively(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run, affected=("src/core/a.py",))
        with self.assertRaises(wp.WorkspaceError):
            broker.declare(
                target,
                run,
                "b" * 64,
                paths=["src/core/a.py"],
                write=True,
            )
        basis = self.write_plan(target, run)

        exact = broker.declare(
            target,
            run,
            basis,
            paths=["src/core/a.py"],
            write=True,
        )
        self.assertEqual((exact["status"], exact["action"]), ("serialize", "serialize"))
        self.assertEqual(broker.write_gate(target, run, basis)["status"], "CLOSED")

        self.write_active(affected=("src/ui/b.py",))
        semantic = broker.declare(
            target,
            run,
            basis,
            paths=["src/ui/a.py"],
            write=True,
        )
        self.assertEqual(
            (semantic["status"], semantic["action"]),
            ("semantic-review", "serialize"),
        )

        disjoint = broker.declare(
            target,
            run,
            basis,
            paths=["tests/net.py"],
            write=True,
        )
        self.assertEqual((disjoint["status"], disjoint["action"]), ("disjoint", "disjoint"))
        self.assertEqual(broker.write_gate(target, run, basis)["status"], "OPEN")
        self.assertEqual(broker.write_gate(self.repo, run, basis)["reason"], "wrong-worktree")
        self.assertEqual(broker.write_gate(target, run, "c" * 64)["reason"], "stale-plan-basis")
        self.write_plan(target, run, "# Changed plan\n")
        self.assertEqual(broker.write_gate(target, run, basis)["reason"], "stale-plan-basis")
        basis = self.write_plan(target, run, "# Restored plan\n")

        self.clear_active()
        resumed = broker.route(self.repo, run, write=True)
        self.assertEqual(
            (resumed["status"], os.path.realpath(resumed["root"])),
            ("allocated", target),
        )
        no_peer = broker.declare(
            target,
            run,
            basis,
            paths=["src/isolated.py"],
            write=True,
        )
        self.assertEqual((no_peer["status"], no_peer["action"]), ("disjoint", "disjoint"))

    def test_write_gate_requires_direct_or_exact_managed_authority(self):
        run = ".kimiflow/run-a"
        foreign = os.path.join(self.temp, "foreign")
        self.git(self.repo, "worktree", "add", "-b", "foreign", foreign)
        self.assertEqual(
            broker.write_gate(foreign, run, "a" * 64)["reason"],
            "direct-authority-unproven",
        )

        self.write_active(run=run)
        primary_basis = self.write_plan(self.repo, run)
        self.assertEqual(
            broker.write_gate(self.repo, run, primary_basis)["reason"],
            "foreign-worktree-ambiguous",
        )
        self.git(self.repo, "worktree", "remove", foreign)
        target = broker.route(self.repo, ".kimiflow/run-b", write=True)["root"]
        self.clear_active()
        basis = self.write_plan(target, ".kimiflow/run-b")
        broker.declare(
            target,
            ".kimiflow/run-b",
            basis,
            paths=["feature.txt"],
            write=True,
        )
        branch = self.git(target, "branch", "--show-current").stdout.strip()
        self.git(target, "switch", "-c", "other-branch")
        self.assertEqual(
            broker.write_gate(target, ".kimiflow/run-b", basis)["reason"],
            "identity-drift",
        )
        self.git(target, "switch", branch)
        self.git(self.repo, "worktree", "unlock", target)
        self.assertEqual(
            broker.write_gate(target, ".kimiflow/run-b", basis)["reason"],
            "identity-drift",
        )

    def test_declare_rejects_unproven_worktree_identity(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        basis = self.write_plan(target, run)
        self.git(self.repo, "worktree", "unlock", target)

        with self.assertRaisesRegex(
            wp.WorkspaceError, "declaration ownership unproven"
        ):
            broker.declare(
                target,
                run,
                basis,
                paths=["feature.txt"],
                write=True,
            )

        task = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual(
            (task["state"], task["paths"], task["action"]),
            ("allocated", [], "pending"),
        )

    def test_declare_rechecks_ownership_after_primary_snapshot(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        basis = self.write_plan(target, run)
        original_snapshot = broker._primary_envelope_snapshot
        injected = False

        def drift_after_snapshot(*args, **kwargs):
            nonlocal injected
            result = original_snapshot(*args, **kwargs)
            if not injected:
                injected = True
                self.git(self.repo, "worktree", "unlock", target)
                self.git(
                    self.repo,
                    "worktree",
                    "lock",
                    "--reason",
                    "kimiflow:forged:.kimiflow/foreign",
                    target,
                )
            return result

        with mock.patch.object(
            broker,
            "_primary_envelope_snapshot",
            side_effect=drift_after_snapshot,
        ):
            with self.assertRaisesRegex(
                wp.WorkspaceError,
                "declaration ownership changed during operation",
            ):
                broker.declare(
                    target,
                    run,
                    basis,
                    paths=["feature.txt"],
                    write=True,
                )

        task = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual(
            (task["state"], task["paths"], task["basis"], task["action"]),
            ("allocated", [], "", "pending"),
        )

    def test_declare_publication_guard_rejects_late_lock_drift(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        basis = self.write_plan(target, run)
        original_write = broker.wp.atomic_directory_write
        injected = False

        def drift_inside_publication(*args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                self.git(self.repo, "worktree", "unlock", target)
                self.git(
                    self.repo,
                    "worktree",
                    "lock",
                    "--reason",
                    "kimiflow:forged:.kimiflow/foreign",
                    target,
                )
            return original_write(*args, **kwargs)

        with mock.patch.object(
            broker.wp,
            "atomic_directory_write",
            side_effect=drift_inside_publication,
        ):
            with self.assertRaisesRegex(
                wp.WorkspaceError,
                "cannot write worktree broker state",
            ):
                broker.declare(
                    target,
                    run,
                    basis,
                    paths=["feature.txt"],
                    write=True,
                )

        task = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual(
            (task["state"], task["paths"], task["basis"], task["action"]),
            ("allocated", [], "", "pending"),
        )

    def test_revalidate_rechecks_ownership_after_primary_snapshot(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        basis = self.write_plan(target, run)
        broker.declare(
            target,
            run,
            basis,
            paths=["feature.txt"],
            write=True,
        )
        declared_main = broker._task_for(
            broker.read_broker(self.repo),
            run,
        )["declared_main"]
        self.clear_active()
        self.commit_file(
            self.repo,
            "predecessor.txt",
            "primary\n",
            "primary advance",
        )
        original_snapshot = broker._primary_envelope_snapshot
        injected = False

        def drift_after_snapshot(*args, **kwargs):
            nonlocal injected
            result = original_snapshot(*args, **kwargs)
            if not injected:
                injected = True
                self.git(self.repo, "worktree", "unlock", target)
                self.git(
                    self.repo,
                    "worktree",
                    "lock",
                    "--reason",
                    "kimiflow:forged:.kimiflow/foreign",
                    target,
                )
            return result

        with mock.patch.object(
            broker,
            "_primary_envelope_snapshot",
            side_effect=drift_after_snapshot,
        ):
            with self.assertRaisesRegex(
                wp.WorkspaceError,
                "revalidation ownership changed during operation",
            ):
                broker.revalidate(
                    target,
                    run,
                    basis,
                    write=True,
                )

        task = broker._task_for(broker.read_broker(self.repo), run)
        self.assertEqual(task["declared_main"], declared_main)
        self.assertEqual(
            broker.write_gate(target, run, basis)["reason"],
            "identity-drift",
        )

    def test_integration_rechecks_late_peer_collision(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        foreign = broker.route(
            self.repo,
            ".kimiflow/run-peer",
            write=True,
        )["root"]
        self.clear_active()
        basis = self.write_plan(target, run)
        declaration = broker.declare(
            target,
            run,
            basis,
            paths=["feature.txt"],
            write=True,
        )
        self.assertEqual(declaration["action"], "disjoint")
        self.commit_file(foreign, "feature.txt", "foreign\n", "foreign feature")
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"], result["collision"]),
            ("ready-to-integrate", "peer-collision", "serialize"),
        )

    def test_integration_serializes_truncated_peer_envelope(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        foreign = broker.route(
            self.repo,
            ".kimiflow/run-peer",
            write=True,
        )["root"]
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(
            target,
            run,
            basis,
            paths=["z-overlap.txt"],
            write=True,
        )
        self.commit_file(target, "z-overlap.txt", "task\n", "task feature")
        for index in range(100):
            with open(
                os.path.join(foreign, "a-%03d.txt" % index),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("dirty\n")
        with open(os.path.join(foreign, "z-overlap.txt"), "w", encoding="utf-8") as handle:
            handle.write("foreign\n")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"], result["collision"]),
            ("ready-to-integrate", "peer-collision", "semantic-review"),
        )

    def test_integration_bounds_combined_peer_envelope(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        foreign = broker.route(
            self.repo,
            ".kimiflow/run-peer",
            write=True,
        )["root"]
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["task.txt"], write=True)
        self.commit_file(target, "task.txt", "task\n", "task feature")
        self.commit_file(foreign, "committed.txt", "peer\n", "peer commit")
        for index in range(101):
            with open(
                os.path.join(foreign, "dirty-%03d.txt" % index),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("dirty\n")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"], result["collision"]),
            ("ready-to-integrate", "peer-collision", "semantic-review"),
        )

    def test_integration_infers_contracts_from_delivered_paths(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        declaration = broker.declare(
            target,
            run,
            basis,
            paths=["src"],
            write=True,
        )
        self.assertEqual(declaration["action"], "disjoint")
        self.commit_file(self.repo, "api/schema.proto", "main\n", "main schema")
        self.commit_file(target, "src/schema.proto", "task\n", "task schema")
        result = broker.revalidate(target, run, basis, write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("needs-reconcile", "primary-advance-collision"),
        )

    def test_integrate_reconciles_diverged_main_then_fast_forwards_and_records_receipt(self):
        run = ".kimiflow/run-a"
        target, _, result = self.integrated_task(run)
        integrated = result["integrated_head"]
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), integrated)
        self.assertEqual(
            len(self.git(target, "show", "-s", "--format=%P", integrated).stdout.split()),
            2,
        )
        task = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual(task["state"], "integrated")
        self.assertEqual(task["integrated_head"], integrated)
        self.assertEqual(
            [row["stage"] for row in task["check_results"]],
            ["pre", "post", "post", "post", "post", "post"],
        )
        self.assertEqual(
            [row["argv"] for row in task["check_results"][1:]],
            [
                ["git", "merge-base", "--is-ancestor", integrated, "HEAD"],
                [
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    integrated,
                    "refs/heads/main",
                ],
                ["git", "symbolic-ref", "--quiet", "HEAD"],
                ["git", "diff", "--quiet", "--"],
                ["git", "diff", "--cached", "--quiet", "--"],
            ],
        )

    def test_integration_allows_unrelated_ignored_primary_content(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task change")
        for index in range(25):
            with open(
                os.path.join(target, run, "receipt-%02d.json" % index),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("{}\n")
        self.ignore("dist/")
        local_artifact = os.path.join(self.repo, "dist", "release.zip")
        os.makedirs(os.path.dirname(local_artifact), exist_ok=True)
        with open(local_artifact, "w", encoding="utf-8") as handle:
            handle.write("local release\n")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(result["status"], "integrated")
        with open(local_artifact, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "local release\n")

    def test_integration_rejects_foreign_ignored_content_after_truncated_run_sample(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task change")
        for index in range(25):
            with open(
                os.path.join(target, run, "receipt-%02d.json" % index),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("{}\n")
        self.ignore("cache/")
        cache = os.path.join(target, "cache")
        os.makedirs(cache)
        with open(os.path.join(cache, "local.bin"), "w", encoding="utf-8") as handle:
            handle.write("foreign ignored content\n")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        with self.assertRaisesRegex(
            wp.WorkspaceError,
            "task worktree is not clean and owned",
        ):
            broker.integrate(self.repo, run, checks=[check], write=True)

    def test_integration_rejects_a_clean_foreign_worktree(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(
            target,
            run,
            basis,
            paths=["feature.txt"],
            write=True,
        )
        self.commit_file(
            target,
            "feature.txt",
            "task\n",
            "task change",
        )
        foreign = os.path.join(self.temp, "late-clean-foreign")
        self.git(
            self.repo,
            "worktree",
            "add",
            "-b",
            "late-clean-foreign",
            foreign,
        )
        main_before = self.git(
            self.repo,
            "rev-parse",
            "HEAD",
        ).stdout.strip()
        gate = broker.write_gate(target, run, basis)
        check = json.dumps(
            [sys.executable, "-c", "raise SystemExit(0)"]
        )

        result = broker.integrate(
            self.repo,
            run,
            checks=[check],
            write=True,
        )

        self.assertEqual(
            (gate["status"], gate["reason"]),
            ("CLOSED", "foreign-worktree-ambiguous"),
        )
        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "foreign-worktree-ambiguous"),
        )
        self.assertEqual(
            self.git(self.repo, "rev-parse", "HEAD").stdout.strip(),
            main_before,
        )
        self.assertTrue(os.path.isdir(foreign))

    def test_integration_blocks_colliding_ignored_primary_path(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task change")
        self.ignore("feature.txt")
        local_path = os.path.join(self.repo, "feature.txt")
        with open(local_path, "w", encoding="utf-8") as handle:
            handle.write("local ignored content\n")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"], result["paths"]),
            ("ready-to-integrate", "ignored-path-collision", ["feature.txt"]),
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)
        with open(local_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "local ignored content\n")

    def test_retirement_refuses_unsafe_state_then_archives_green_ancestor(self):
        run = ".kimiflow/run-a"
        target, _, result = self.integrated_task(run)
        branch = self.git(target, "branch", "--show-current").stdout.strip()
        with self.assertRaises(wp.WorkspaceError):
            broker.retire(self.repo, run, write=True)

        self.write_terminal(target, run)
        original_write = broker._write_broker
        writes = 0

        def crash_after_archive(descriptor, state):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise wp.WorkspaceError("injected retirement receipt crash")
            return original_write(descriptor, state)

        with mock.patch.object(broker, "_write_broker", side_effect=crash_after_archive):
            with self.assertRaises(wp.WorkspaceError):
                broker.retire(self.repo, run, write=True)
        self.assertFalse(os.path.exists(target))
        self.assertEqual(
            broker.read_broker(self.repo)["tasks"][0]["journal"]["kind"],
            "retirement",
        )

        retired = broker.retire(self.repo, run, write=True)
        self.assertEqual(retired["status"], "retired")
        self.assertTrue(retired["recovered"])
        self.assertFalse(os.path.exists(target))
        self.assertTrue(os.path.isdir(retired["archive_path"]))
        self.assertTrue(self.git(self.repo, "branch", "--list", branch).stdout.strip())
        self.assertEqual(
            self.git(
                self.repo,
                "merge-base",
                "--is-ancestor",
                result["integrated_head"],
                "main",
                check=False,
            ).returncode,
            0,
        )

    def test_integrate_returns_terminal_receipt_idempotently(self):
        run = ".kimiflow/run-a"
        target, _, delivered = self.integrated_task(run)
        self.write_active(
            run=".kimiflow/run-later",
            affected=("later.txt",),
        )

        repeated = broker.integrate(self.repo, run, write=True)

        self.assertEqual(
            (
                repeated["status"],
                repeated["integrated_head"],
                repeated["idempotent"],
            ),
            ("integrated", delivered["integrated_head"], True),
        )
        self.assertEqual(
            broker._task_for(broker.read_broker(self.repo), run)["state"],
            "integrated",
        )
        self.clear_active()
        self.write_terminal(target, run)
        self.assertEqual(
            broker.retire(self.repo, run, write=True)["status"],
            "retired",
        )

    def test_failed_integration_check_preserves_main_and_refuses_retirement(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task change")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        failing = json.dumps(["kimiflow-test-command-that-does-not-exist"])

        self.write_plan(target, run, "# Stale plan\n")
        stale = broker.integrate(self.repo, run, checks=[failing], write=True)
        self.assertEqual((stale["status"], stale["reason"]), ("ready-to-integrate", "stale-plan-basis"))
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)
        self.assertEqual(self.write_plan(target, run), basis)
        result = broker.integrate(self.repo, run, checks=[failing], write=True)
        self.assertEqual(result["status"], "verification-failed")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)
        self.write_terminal(target, run)
        with self.assertRaises(wp.WorkspaceError):
            broker.retire(self.repo, run, write=True)

    def test_integration_rejects_actual_delta_outside_declaration(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["declared.txt"], write=True)
        self.commit_file(target, "undeclared.txt", "task\n", "undeclared task change")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "undeclared-task-delta"),
        )
        self.assertEqual(result["paths"], ["undeclared.txt"])
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)

    def test_integration_rejects_task_mutation_by_passing_check(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task change")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        mutating = json.dumps(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('feature.txt').write_text('mutated\\n')",
            ]
        )

        result = broker.integrate(self.repo, run, checks=[mutating], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("verification-failed", "task-mutated-during-check"),
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)
        with open(os.path.join(target, "feature.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "mutated\n")

    def test_integration_rechecks_plan_basis_after_passing_check(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task change")
        mutating = json.dumps(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('.kimiflow/run-a/PLAN.md').write_text('# changed\\n')",
            ]
        )

        result = broker.integrate(self.repo, run, checks=[mutating], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("verification-failed", "task-mutated-during-check"),
        )
        self.assertNotEqual(broker._plan_digest(target, run), basis)

    def test_integration_rechecks_peer_collision_after_passing_check(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        foreign = broker.route(
            self.repo,
            ".kimiflow/run-peer",
            write=True,
        )["root"]
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        source = (
            "from pathlib import Path; import subprocess; "
            "root=%r; Path(root, 'feature.txt').write_text('foreign\\n'); "
            "subprocess.run(['git', '-C', root, 'add', 'feature.txt'], check=True); "
            "subprocess.run(['git', '-C', root, 'commit', '-m', 'foreign feature'], check=True)"
        ) % foreign
        check = json.dumps([sys.executable, "-c", source])

        result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"], result["collision"]),
            ("ready-to-integrate", "post-check-peer-collision", "serialize"),
        )

    def test_integration_rechecks_peer_at_delivery_boundary(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        foreign = broker.route(
            self.repo,
            ".kimiflow/run-peer",
            write=True,
        )["root"]
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original_write = broker._write_broker
        injected = False

        def inject_peer_after_journal(descriptor, state):
            nonlocal injected
            result = original_write(descriptor, state)
            task = state["tasks"][0]
            if not injected and task["state"] == "integrating":
                injected = True
                self.commit_file(
                    foreign,
                    "feature.txt",
                    "foreign\n",
                    "late foreign feature",
                )
            return result

        with mock.patch.object(
            broker,
            "_write_broker",
            side_effect=inject_peer_after_journal,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"], result["collision"]),
            ("ready-to-integrate", "peer-collision-at-delivery-boundary", "serialize"),
        )

    def test_integration_rejects_clean_foreign_at_delivery_boundary(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(
            target,
            run,
            basis,
            paths=["feature.txt"],
            write=True,
        )
        self.commit_file(
            target,
            "feature.txt",
            "task\n",
            "task feature",
        )
        main_before = self.git(
            self.repo,
            "rev-parse",
            "HEAD",
        ).stdout.strip()
        check = json.dumps(
            [sys.executable, "-c", "raise SystemExit(0)"]
        )
        foreign = os.path.join(self.temp, "late-boundary-foreign")
        original_write = broker._write_broker
        injected = False

        def inject_foreign_after_journal(descriptor, state):
            nonlocal injected
            result = original_write(descriptor, state)
            task = state["tasks"][0]
            if not injected and task["state"] == "integrating":
                injected = True
                self.git(
                    self.repo,
                    "worktree",
                    "add",
                    "-b",
                    "late-boundary-foreign",
                    foreign,
                )
            return result

        with mock.patch.object(
            broker,
            "_write_broker",
            side_effect=inject_foreign_after_journal,
        ):
            result = broker.integrate(
                self.repo,
                run,
                checks=[check],
                write=True,
            )

        self.assertEqual(
            (result["status"], result["reason"]),
            (
                "ready-to-integrate",
                "foreign-worktree-at-delivery-boundary",
            ),
        )
        self.assertEqual(
            self.git(self.repo, "rev-parse", "HEAD").stdout.strip(),
            main_before,
        )
        self.assertTrue(os.path.isdir(foreign))

    def test_integration_rechecks_primary_and_task_refs_at_delivery_boundary(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original_write = broker._write_broker
        injected = False

        def switch_primary_after_journal(descriptor, state):
            nonlocal injected
            result = original_write(descriptor, state)
            task = state["tasks"][0]
            if not injected and task["state"] == "integrating":
                injected = True
                self.git(self.repo, "switch", "-c", "diversion")
            return result

        with mock.patch.object(
            broker,
            "_write_broker",
            side_effect=switch_primary_after_journal,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)
        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "primary-changed-at-delivery-boundary"),
        )
        self.assertNotEqual(
            self.git(self.repo, "rev-parse", "refs/heads/main").stdout.strip(),
            self.git(target, "rev-parse", "HEAD").stdout.strip(),
        )

        self.git(self.repo, "switch", "main")
        original_write = broker._write_broker
        injected = False

        def commit_task_after_journal(descriptor, state):
            nonlocal injected
            result = original_write(descriptor, state)
            task = state["tasks"][0]
            if not injected and task["state"] == "integrating":
                injected = True
                self.commit_file(target, "late.txt", "late\n", "late task change")
            return result

        with mock.patch.object(
            broker,
            "_write_broker",
            side_effect=commit_task_after_journal,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)
        self.assertEqual(
            (result["status"], result["reason"]),
            ("verification-failed", "task-mutated-at-delivery-boundary"),
        )

    def test_atomic_delivery_never_advances_a_substituted_primary_branch(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        task_head = self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._atomic_primary_fast_forward

        def switch_before_ref_transaction(*args, **kwargs):
            self.git(self.repo, "switch", "-c", "diversion")
            return original(*args, **kwargs)

        with mock.patch.object(
            broker,
            "_atomic_primary_fast_forward",
            side_effect=switch_before_ref_transaction,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(result["status"], "verification-failed")
        self.assertEqual(
            self.git(self.repo, "rev-parse", "refs/heads/main").stdout.strip(),
            task_head,
        )
        self.assertEqual(
            self.git(self.repo, "rev-parse", "refs/heads/diversion").stdout.strip(),
            main_before,
        )

    def test_atomic_delivery_refuses_a_peer_commit_at_the_ref_boundary(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        foreign = broker.route(
            self.repo,
            ".kimiflow/run-peer",
            write=True,
        )["root"]
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._atomic_primary_fast_forward

        def commit_peer_before_ref_transaction(*args, **kwargs):
            self.commit_file(
                foreign,
                "feature.txt",
                "foreign\n",
                "foreign at ref boundary",
            )
            return original(*args, **kwargs)

        with mock.patch.object(
            broker,
            "_atomic_primary_fast_forward",
            side_effect=commit_peer_before_ref_transaction,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "delivery-ref-cas-changed"),
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)

    def test_atomic_delivery_rolls_back_a_dirty_peer_at_the_ref_boundary(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        foreign = broker.route(
            self.repo,
            ".kimiflow/run-peer",
            write=True,
        )["root"]
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._atomic_primary_fast_forward

        def dirty_peer_before_ref_transaction(*args, **kwargs):
            with open(
                os.path.join(foreign, "feature.txt"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("foreign\n")
            return original(*args, **kwargs)

        with mock.patch.object(
            broker,
            "_atomic_primary_fast_forward",
            side_effect=dirty_peer_before_ref_transaction,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "peer-collision-at-ref-boundary"),
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)

    def test_atomic_delivery_rolls_back_dirty_task_content_at_the_ref_boundary(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._atomic_primary_fast_forward

        def dirty_task_before_ref_transaction(*args, **kwargs):
            with open(
                os.path.join(target, "late-untracked.txt"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("late\n")
            return original(*args, **kwargs)

        with mock.patch.object(
            broker,
            "_atomic_primary_fast_forward",
            side_effect=dirty_task_before_ref_transaction,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "task-mutated-at-ref-boundary"),
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)

    def test_atomic_delivery_rolls_back_dirty_primary_content_at_the_ref_boundary(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._atomic_primary_fast_forward

        def dirty_primary_before_ref_transaction(*args, **kwargs):
            with open(
                os.path.join(self.repo, "tracked.txt"),
                "a",
                encoding="utf-8",
            ) as handle:
                handle.write("foreign edit\n")
            return original(*args, **kwargs)

        with mock.patch.object(
            broker,
            "_atomic_primary_fast_forward",
            side_effect=dirty_primary_before_ref_transaction,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "primary-mutated-at-ref-boundary"),
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)
        with open(os.path.join(self.repo, "tracked.txt"), encoding="utf-8") as handle:
            self.assertIn("foreign edit\n", handle.read())
        stored = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual((stored["state"], stored["journal"]), ("ready-to-integrate", None))

    def test_atomic_delivery_rolls_back_staged_primary_content_at_the_ref_boundary(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._atomic_primary_fast_forward

        def stage_primary_before_ref_transaction(*args, **kwargs):
            with open(
                os.path.join(self.repo, "tracked.txt"),
                "a",
                encoding="utf-8",
            ) as handle:
                handle.write("staged foreign edit\n")
            self.git(self.repo, "add", "tracked.txt")
            return original(*args, **kwargs)

        with mock.patch.object(
            broker,
            "_atomic_primary_fast_forward",
            side_effect=stage_primary_before_ref_transaction,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "primary-mutated-at-ref-boundary"),
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)
        self.assertIn(
            "staged foreign edit\n",
            self.git(self.repo, "show", ":tracked.txt").stdout,
        )
        stored = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual((stored["state"], stored["journal"]), ("ready-to-integrate", None))

    def test_atomic_delivery_rolls_back_primary_content_staged_during_index_sync(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._git
        injected = False

        def stage_primary_at_index_sync(root, args, *positional, **kwargs):
            nonlocal injected
            if not injected and args[:3] == ["read-tree", "-m", "-u"]:
                injected = True
                with open(
                    os.path.join(self.repo, "tracked.txt"),
                    "a",
                    encoding="utf-8",
                ) as handle:
                    handle.write("staged during sync\n")
                self.git(self.repo, "add", "tracked.txt")
            return original(root, args, *positional, **kwargs)

        with mock.patch.object(
            broker,
            "_git",
            side_effect=stage_primary_at_index_sync,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("ready-to-integrate", "primary-mutated-at-ref-boundary"),
        )
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), main_before)
        self.assertEqual(
            self.git(self.repo, "diff", "--cached", "--name-only").stdout.splitlines(),
            ["tracked.txt"],
        )
        self.assertIn(
            "staged during sync\n",
            self.git(self.repo, "show", ":tracked.txt").stdout,
        )
        stored = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual((stored["state"], stored["journal"]), ("ready-to-integrate", None))

    def test_delivery_receipt_refuses_a_primary_rewind_before_publication(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._write_broker
        injected = False

        def rewind_before_receipt(descriptor, state):
            nonlocal injected
            if not injected and state["tasks"][0]["state"] == "integrated":
                injected = True
                task_head = state["tasks"][0]["integrated_head"]
                self.git(
                    self.repo,
                    "update-ref",
                    "refs/heads/main",
                    main_before,
                    task_head,
                )
            return original(descriptor, state)

        with mock.patch.object(
            broker,
            "_write_broker",
            side_effect=rewind_before_receipt,
        ):
            with self.assertRaisesRegex(wp.WorkspaceError, "terminal receipt"):
                broker.integrate(self.repo, run, checks=[check], write=True)

        stored = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual(
            (stored["state"], stored["journal"]["kind"]),
            ("integrating", "fast-forward"),
        )
        self.assertEqual(
            self.git(self.repo, "rev-parse", "refs/heads/main").stdout.strip(),
            main_before,
        )

    def test_delivery_receipt_revalidates_inside_atomic_state_publication(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker.wp.atomic_directory_write
        injected = False

        def rewind_inside_publication(descriptor, name, payload, **kwargs):
            nonlocal injected
            if not injected and b'\"state\":\"integrated\"' in payload:
                injected = True
                task_head = self.git(
                    self.repo,
                    "rev-parse",
                    "refs/heads/main",
                ).stdout.strip()
                self.git(
                    self.repo,
                    "update-ref",
                    "refs/heads/main",
                    main_before,
                    task_head,
                )
            return original(descriptor, name, payload, **kwargs)

        with mock.patch.object(
            broker.wp,
            "atomic_directory_write",
            side_effect=rewind_inside_publication,
        ):
            with self.assertRaises(wp.WorkspaceError):
                broker.integrate(self.repo, run, checks=[check], write=True)

        stored = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual(
            (stored["state"], stored["journal"]["kind"]),
            ("integrating", "fast-forward"),
        )

    def test_delivery_receipt_compensates_a_rewind_after_the_internal_guard(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original_unlink = wp.os.unlink
        injected = False

        def rewind_at_temporary_unlink(path, *args, **kwargs):
            nonlocal injected
            current = self.git(
                self.repo,
                "rev-parse",
                "refs/heads/main",
            ).stdout.strip()
            if (
                not injected
                and str(path).startswith(".kimiflow-%s-" % broker.BROKER_NAME)
                and current != main_before
            ):
                injected = True
                self.git(
                    self.repo,
                    "update-ref",
                    "refs/heads/main",
                    main_before,
                    current,
                )
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(wp.os, "unlink", side_effect=rewind_at_temporary_unlink):
            with self.assertRaises(wp.WorkspaceError):
                broker.integrate(self.repo, run, checks=[check], write=True)

        stored = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual(
            (stored["state"], stored["journal"]["kind"]),
            ("integrating", "fast-forward"),
        )

    def test_integration_recovery_rolls_back_a_late_peer_collision(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        foreign = broker.route(
            self.repo,
            ".kimiflow/run-peer",
            write=True,
        )["root"]
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        main_before = self.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._atomic_primary_fast_forward

        def dirty_peer_then_crash(*args, **kwargs):
            with open(
                os.path.join(foreign, "feature.txt"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("foreign\n")
            with mock.patch.object(
                broker,
                "_rollback_primary_ref",
                side_effect=SystemExit("crash before rollback"),
            ):
                return original(*args, **kwargs)

        with mock.patch.object(
            broker,
            "_atomic_primary_fast_forward",
            side_effect=dirty_peer_then_crash,
        ):
            with self.assertRaises(SystemExit):
                broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertNotEqual(
            self.git(self.repo, "rev-parse", "refs/heads/main").stdout.strip(),
            main_before,
        )
        recovered = broker.integrate(self.repo, run, write=True)
        self.assertEqual(
            (recovered["status"], recovered["reason"], recovered["recovered"]),
            ("ready-to-integrate", "peer-collision-at-ref-boundary", True),
        )
        self.assertEqual(
            self.git(self.repo, "rev-parse", "refs/heads/main").stdout.strip(),
            main_before,
        )

    def test_delivery_rechecks_task_content_after_final_peer_snapshot(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task feature")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original = broker._peer_head_snapshot

        def mutate_after_peer_snapshot(*args, **kwargs):
            result = original(*args, **kwargs)
            with open(
                os.path.join(target, "late-untracked.txt"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("late\n")
            return result

        with mock.patch.object(
            broker,
            "_peer_head_snapshot",
            side_effect=mutate_after_peer_snapshot,
        ):
            result = broker.integrate(self.repo, run, checks=[check], write=True)

        self.assertEqual(
            (result["status"], result["reason"]),
            ("verification-failed", "task-mutated-at-delivery-boundary"),
        )

    def test_integration_rejects_branch_or_lock_identity_drift(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        branch = self.git(target, "branch", "--show-current").stdout.strip()
        self.git(target, "switch", "-c", "other-branch")
        self.commit_file(target, "feature.txt", "task\n", "other branch change")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])

        with self.assertRaisesRegex(wp.WorkspaceError, "clean and owned"):
            broker.integrate(self.repo, run, checks=[check], write=True)
        self.git(target, "switch", branch)
        self.git(self.repo, "worktree", "unlock", target)
        with self.assertRaisesRegex(wp.WorkspaceError, "clean and owned"):
            broker.integrate(self.repo, run, checks=[check], write=True)

    def test_integrate_recovers_journaled_fast_forward(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        task_head = self.commit_file(target, "feature.txt", "task\n", "task change")
        check = json.dumps(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; assert Path('feature.txt').is_file()",
            ]
        )
        original_write = broker._write_broker
        writes = 0

        def crash_after_fast_forward(descriptor, state):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise wp.WorkspaceError("injected receipt publication crash")
            return original_write(descriptor, state)

        with mock.patch.object(broker, "_write_broker", side_effect=crash_after_fast_forward):
            with self.assertRaises(wp.WorkspaceError):
                broker.integrate(self.repo, run, checks=[check], write=True)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD").stdout.strip(), task_head)
        interrupted = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual(interrupted["journal"]["kind"], "fast-forward")

        recovered = broker.integrate(self.repo, run, write=True)
        self.assertEqual((recovered["status"], recovered["recovered"]), ("integrated", True))
        self.assertIsNone(broker.read_broker(self.repo)["tasks"][0]["journal"])

    def test_integration_recovery_rejects_undelivered_task_head(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        self.clear_active()
        basis = self.write_plan(target, run)
        broker.declare(target, run, basis, paths=["feature.txt"], write=True)
        self.commit_file(target, "feature.txt", "task\n", "task change")
        check = json.dumps([sys.executable, "-c", "raise SystemExit(0)"])
        original_write = broker._write_broker
        writes = 0

        def crash_after_fast_forward(descriptor, state):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise wp.WorkspaceError("injected receipt publication crash")
            return original_write(descriptor, state)

        with mock.patch.object(broker, "_write_broker", side_effect=crash_after_fast_forward):
            with self.assertRaises(wp.WorkspaceError):
                broker.integrate(self.repo, run, checks=[check], write=True)
        self.commit_file(target, "extra.txt", "extra\n", "undelivered task change")

        with self.assertRaisesRegex(wp.WorkspaceError, "task ref mismatch"):
            broker.integrate(self.repo, run, write=True)

    def test_retirement_rejects_undelivered_task_head(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.commit_file(target, "extra.txt", "extra\n", "undelivered task change")
        self.write_terminal(target, run)

        with self.assertRaisesRegex(wp.WorkspaceError, "task ref mismatch"):
            broker.retire(self.repo, run, write=True)
        self.assertTrue(os.path.isdir(target))

    def test_retirement_rechecks_head_at_archive_boundary(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        original_remove = wp.remove

        def add_commit_before_remove(*args, **kwargs):
            self.commit_file(target, "late.txt", "late\n", "late task change")
            return original_remove(*args, **kwargs)

        with mock.patch.object(broker.wp, "remove", side_effect=add_commit_before_remove):
            with self.assertRaises(wp.WorkspaceError):
                broker.retire(self.repo, run, write=True)
        self.assertTrue(os.path.isdir(target))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "late.txt")))

    def test_retirement_allows_many_kimiflow_artifacts(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        self.commit_file(self.repo, "later.txt", "later\n", "later main change")
        artifacts = os.path.join(target, ".kimiflow", "artifacts")
        os.makedirs(artifacts, exist_ok=True)
        for index in range(wp.IGNORED_PATH_SAMPLE_LIMIT + 5):
            with open(
                os.path.join(artifacts, "%02d.txt" % index),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("artifact\n")

        result = broker.retire(self.repo, run, write=True)

        self.assertEqual(result["status"], "retired")
        self.assertFalse(os.path.exists(target))
        self.assertTrue(os.path.isdir(result["archive_path"]))

    def test_retirement_rejects_foreign_ignored_content_after_sample_limit(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        artifacts = os.path.join(target, ".kimiflow", "artifacts")
        os.makedirs(artifacts, exist_ok=True)
        for index in range(wp.IGNORED_PATH_SAMPLE_LIMIT + 5):
            with open(
                os.path.join(artifacts, "%02d.txt" % index),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("artifact\n")
        self.ignore("reports/")
        reports = os.path.join(target, "reports")
        os.makedirs(reports)
        with open(os.path.join(reports, "keep.txt"), "w", encoding="utf-8") as handle:
            handle.write("keep\n")

        with self.assertRaisesRegex(wp.WorkspaceError, "task ref mismatch"):
            broker.retire(self.repo, run, write=True)

        self.assertTrue(os.path.isfile(os.path.join(reports, "keep.txt")))

    def test_retirement_rechecks_content_immediately_before_archive(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        original_prepare = wp.prepare_retirement_paths

        def write_after_prepare(paths):
            result = original_prepare(paths)
            with open(
                os.path.join(target, "late-untracked.txt"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("late\n")
            return result

        with mock.patch.object(
            broker.wp,
            "prepare_retirement_paths",
            side_effect=write_after_prepare,
        ):
            with self.assertRaises(wp.WorkspaceError):
                broker.retire(self.repo, run, write=True)
        self.assertTrue(os.path.isdir(target))
        self.assertTrue(os.path.isfile(os.path.join(target, "late-untracked.txt")))

    def test_retirement_rechecks_primary_ancestry_after_journal_publication(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        task = broker.read_broker(self.repo)["tasks"][0]
        original = broker.wp.remove

        def rewind_before_remove(*args, **kwargs):
            self.git(
                self.repo,
                "update-ref",
                task["primary_ref"],
                task["base"],
                task["integrated_head"],
            )
            return original(*args, **kwargs)

        with mock.patch.object(broker.wp, "remove", side_effect=rewind_before_remove):
            with self.assertRaisesRegex(wp.WorkspaceError, "delivery refs changed"):
                broker.retire(self.repo, run, write=True)

        self.assertTrue(os.path.isdir(target))

    def test_retirement_rolls_back_content_added_at_archive_rename(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        original = wp.os.rename
        injected = False

        def inject_at_checkout_rename(source, destination, *args, **kwargs):
            nonlocal injected
            if not injected and destination == "worktree":
                injected = True
                with open(
                    os.path.join(target, "late-untracked.txt"),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    handle.write("late\n")
            return original(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "rename", side_effect=inject_at_checkout_rename):
            with self.assertRaisesRegex(wp.WorkspaceError, "task content changed"):
                broker.retire(self.repo, run, write=True)

        self.assertTrue(os.path.isdir(target))
        self.assertTrue(os.path.isfile(os.path.join(target, "late-untracked.txt")))

    def test_retirement_revalidates_archive_inside_metadata_detach(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        original = wp.detach_admin_record
        injected = False

        def inject_before_metadata_detach(*args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                task = broker.read_broker(self.repo)["tasks"][0]
                with open(
                    os.path.join(
                        task["archive"]["checkout"],
                        "late-after-guard.txt",
                    ),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    handle.write("late\n")
            return original(*args, **kwargs)

        with mock.patch.object(
            wp,
            "detach_admin_record",
            side_effect=inject_before_metadata_detach,
        ):
            with self.assertRaisesRegex(wp.WorkspaceError, "task content changed"):
                broker.retire(self.repo, run, write=True)

        self.assertTrue(os.path.isdir(target))
        self.assertTrue(os.path.isfile(os.path.join(target, "late-after-guard.txt")))

    def test_retirement_revalidates_after_the_metadata_rename(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        task = broker.read_broker(self.repo)["tasks"][0]
        original = wp.os.rename
        injected = False

        def inject_at_metadata_rename(source, destination, *args, **kwargs):
            nonlocal injected
            if not injected and destination == task["identity"]:
                injected = True
                latest = broker.read_broker(self.repo)["tasks"][0]
                with open(
                    os.path.join(
                        latest["archive"]["checkout"],
                        "late-after-internal-guard.txt",
                    ),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    handle.write("late\n")
            return original(source, destination, *args, **kwargs)

        with mock.patch.object(wp.os, "rename", side_effect=inject_at_metadata_rename):
            with self.assertRaisesRegex(wp.WorkspaceError, "task content changed"):
                broker.retire(self.repo, run, write=True)

        self.assertTrue(os.path.isdir(target))
        self.assertTrue(
            os.path.isfile(
                os.path.join(target, "late-after-internal-guard.txt")
            )
        )

    def test_retirement_recovery_restores_late_archive_content(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        original_rename = wp.os.rename
        injected = False

        def inject_at_checkout_rename(source, destination, *args, **kwargs):
            nonlocal injected
            if not injected and destination == "worktree":
                injected = True
                with open(
                    os.path.join(target, "late-crash.txt"),
                    "w",
                    encoding="utf-8",
                ) as handle:
                    handle.write("late\n")
            return original_rename(source, destination, *args, **kwargs)

        with mock.patch.object(
            wp.os,
            "rename",
            side_effect=inject_at_checkout_rename,
        ), mock.patch.object(
            broker,
            "_archived_checkout_status",
            side_effect=SystemExit("crash before archive guard"),
        ):
            with self.assertRaises(SystemExit):
                broker.retire(self.repo, run, write=True)

        self.assertFalse(os.path.exists(target))
        with self.assertRaisesRegex(wp.WorkspaceError, "restored checkout"):
            broker.retire(self.repo, run, write=True)
        self.assertTrue(os.path.isdir(target))
        self.assertTrue(os.path.isfile(os.path.join(target, "late-crash.txt")))
        task = broker.read_broker(self.repo)["tasks"][0]
        self.assertEqual(
            (task["state"], task["journal"], task["archive"]),
            ("verification-failed", None, None),
        )

    def test_retirement_recovers_hard_crash_between_checkout_and_metadata_archive(self):
        run = ".kimiflow/run-a"
        target, _, _ = self.integrated_task(run)
        self.write_terminal(target, run)
        original_detach = wp.detach_admin_record

        with mock.patch.object(
            broker.wp,
            "detach_admin_record",
            side_effect=SystemExit("simulated hard crash"),
        ):
            with self.assertRaises(SystemExit):
                broker.retire(self.repo, run, write=True)

        interrupted = broker.read_broker(self.repo)["tasks"][0]
        self.assertFalse(os.path.exists(target))
        self.assertTrue(os.path.isdir(interrupted["archive"]["checkout"]))
        self.assertFalse(os.path.exists(interrupted["archive"]["metadata"]))
        self.assertTrue(os.path.isdir(interrupted["archive"]["admin"]))

        with mock.patch.object(broker.wp, "detach_admin_record", wraps=original_detach):
            recovered = broker.retire(self.repo, run, write=True)
        self.assertEqual((recovered["status"], recovered["recovered"]), ("retired", True))
        self.assertTrue(os.path.isdir(recovered["metadata_archive_path"]))
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

    def test_broker_never_mutates_foreign_or_codex_managed_worktrees(self):
        codex_home = os.path.join(self.temp, "codex-home")
        foreign = os.path.join(codex_home, "worktrees", "foreign")
        os.makedirs(os.path.dirname(foreign), exist_ok=True)
        self.git(self.repo, "worktree", "add", "-b", "foreign", foreign)
        foreign_head = self.commit_file(
            foreign, "foreign-only.txt", "foreign\n", "foreign change"
        )
        self.write_active()

        with mock.patch.dict(os.environ, {"CODEX_HOME": codex_home}):
            with self.assertRaises(wp.WorkspaceError):
                broker.route(self.repo, ".kimiflow/run-a", write=True)
        self.assertTrue(os.path.isdir(foreign))
        self.assertEqual(self.git(foreign, "rev-parse", "HEAD").stdout.strip(), foreign_head)
        registry = wp.read_registry(self.repo)
        self.assertNotIn(os.path.realpath(foreign), [entry["path"] for entry in registry["entries"]])

    def test_fleet_ownership_negatives_preserve_foreign_and_peer_trees(self):
        foreign = os.path.join(self.temp, "manual")
        self.git(self.repo, "worktree", "add", "-b", "manual", foreign)
        foreign_head = self.commit_file(foreign, "manual.txt", "manual\n", "manual")
        self.write_active()

        with self.assertRaises(wp.WorkspaceError):
            broker.route(self.repo, ".kimiflow/run-a", write=True)
        duplicate = {
            "schema_version": 1,
            "entries": [
                {"path": os.path.realpath(foreign), "run": ".kimiflow/run-a", "identity": "a" * 64},
                {"path": os.path.realpath(self.repo), "run": ".kimiflow/run-b", "identity": "a" * 64},
            ],
        }
        with self.assertRaises(wp.WorkspaceError):
            wp.validate_registry(duplicate)
        self.assertEqual(self.git(foreign, "rev-parse", "HEAD").stdout.strip(), foreign_head)
        self.assertTrue(os.path.isfile(os.path.join(foreign, "manual.txt")))
        self.assertFalse(wp.read_registry(self.repo)["entries"])

    def test_captain_force_route_isolates_a_clean_primary(self):
        result = broker.route(
            self.repo,
            ".kimiflow/captain-task",
            write=True,
            force_worktree=True,
        )
        self.assertEqual((result["status"], result["route"]), ("allocated", "worktree"))
        self.assertNotEqual(os.path.realpath(result["root"]), self.repo)
        entry = wp.read_registry(self.repo)["entries"][0]
        self.assertEqual(entry["run"], ".kimiflow/captain-task")
        self.assertTrue(wp.owner_receipt_matches(result["root"], entry))

    def test_backward_compatible_status_and_candidate_packaging(self):
        self.ignore("dist/")
        local_artifact = os.path.join(self.repo, "dist", "release.zip")
        os.makedirs(os.path.dirname(local_artifact), exist_ok=True)
        with open(local_artifact, "w", encoding="utf-8") as handle:
            handle.write("local release\n")
        result = broker.route(self.repo, ".kimiflow/direct-run", write=True)
        self.assertEqual((result["status"], result["route"]), ("direct", "main"))
        self.assertFalse(
            os.path.exists(
                os.path.join(self.repo, ".kimiflow", "session", broker.BROKER_NAME)
            )
        )
        status = wp.build_status(self.repo)
        self.assertEqual(status["schema_version"], 1)
        self.assertEqual(status["temporary_count"], 0)

        self.write_active(run=".kimiflow/direct-run")
        with open(os.path.join(self.repo, "tracked.txt"), "a", encoding="utf-8") as handle:
            handle.write("owned\n")
        repeated = broker.route(self.repo, ".kimiflow/direct-run", write=True)
        self.assertEqual((repeated["status"], repeated["route"]), ("direct", "main"))
        self.assertEqual(wp.build_status(self.repo)["worktree_count"], 1)

    def test_broker_state_is_atomic_bounded_and_fails_closed(self):
        session = os.path.join(self.repo, ".kimiflow", "session")
        os.makedirs(session, exist_ok=True)
        state_path = os.path.join(session, broker.BROKER_NAME)
        outside = os.path.join(self.temp, "outside.json")
        with open(outside, "w", encoding="utf-8") as handle:
            handle.write('{"schema_version":1,"tasks":[]}\n')
        os.symlink(outside, state_path)
        before = self.git(self.repo, "worktree", "list", "--porcelain").stdout
        with self.assertRaises(wp.WorkspaceError):
            broker.broker_status(self.repo)
        self.assertEqual(self.git(self.repo, "worktree", "list", "--porcelain").stdout, before)

        os.unlink(state_path)
        oversized_paths = [
            "src/%03d-%s" % (index, "x" * 3000)
            for index in range(broker.MAX_PATHS)
        ]
        oversized_task = broker._new_task(".kimiflow/oversized")
        oversized_task["paths"] = oversized_paths
        oversized = {
            "schema_version": broker.BROKER_SCHEMA,
            "tasks": [oversized_task],
        }
        with wp.registry_operation(self.repo, True) as descriptor:
            with self.assertRaisesRegex(wp.WorkspaceError, "state is too large"):
                broker._write_broker(descriptor, oversized)
        self.assertFalse(os.path.exists(state_path))
        with self.assertRaises(wp.WorkspaceError):
            broker.normalize_paths(["x" * (broker.MAX_PATH_BYTES + 1)])
        self.assertEqual(wp.read_registry(self.repo)["entries"], [])

        active_path = os.path.join(session, "ACTIVE_RUN.json")
        os.symlink(outside, active_path)
        with self.assertRaises(wp.WorkspaceError):
            broker.route(self.repo, ".kimiflow/run-a", write=True)
        self.assertEqual(self.git(self.repo, "worktree", "list", "--porcelain").stdout, before)
        os.unlink(active_path)

        with open(state_path, "wb") as handle:
            handle.write(b"{" + b"x" * 262144)
        with self.assertRaises(wp.WorkspaceError):
            broker.broker_status(self.repo)
        self.assertEqual(self.git(self.repo, "worktree", "list", "--porcelain").stdout, before)

    def test_broker_state_restores_the_canonical_name_on_system_exit(self):
        run = ".kimiflow/run-a"
        target = self.allocate(run)
        basis = self.write_plan(target, run)
        original_link = wp.os.link
        injected = False

        def crash_before_install(*args, **kwargs):
            nonlocal injected
            if not injected:
                injected = True
                raise SystemExit("crash before broker state install")
            return original_link(*args, **kwargs)

        with mock.patch.object(wp.os, "link", side_effect=crash_before_install):
            with self.assertRaises(SystemExit):
                broker.declare(
                    target,
                    run,
                    basis,
                    paths=["feature.txt"],
                    write=True,
                )

        restored = broker.read_broker(self.repo)
        self.assertEqual(len(restored["tasks"]), 1)
        self.assertEqual(restored["tasks"][0]["state"], "allocated")

    def test_broker_state_rejects_git_invalid_primary_refs(self):
        self.assertTrue(broker._valid_branch_ref("refs/heads/@"))
        task = broker._new_task(".kimiflow/run-a")
        for invalid in (
            "refs/heads/main bad",
            "refs/heads/main~1",
            "refs/heads/main.lock",
            "refs/heads/line\nbreak",
        ):
            task["primary_ref"] = invalid
            with self.assertRaisesRegex(wp.WorkspaceError, "malformed"):
                broker._validate_task(task)


if __name__ == "__main__":
    unittest.main()
