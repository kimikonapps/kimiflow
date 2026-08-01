import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

from kimiflow_core import pi_project


class PiProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.config = os.path.join(self.temp, "pi-agent")
        self.repo = os.path.join(self.temp, "project")
        os.makedirs(self.repo)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "kimiflow@example.invalid")
        self.git("config", "user.name", "Kimiflow Test")
        with open(os.path.join(self.repo, "tracked.txt"), "w", encoding="utf-8") as handle:
            handle.write("base\n")
        self.git("add", "tracked.txt")
        self.git("commit", "-m", "base")
        self.repo = os.path.realpath(self.repo)
        self.env = {**os.environ, "PI_CODING_AGENT_DIR": self.config}

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", self.repo, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )

    def test_registry_registers_exact_git_roots_without_task_content(self):
        result = pi_project.register(self.repo, environ=self.env)
        listed = pi_project.list_projects(self.env)

        self.assertEqual(result["project"]["root"], self.repo)
        self.assertEqual(len(listed["projects"]), 1)
        registry = os.path.join(self.config, pi_project.REGISTRY_NAME)
        self.assertEqual(stat.S_IMODE(os.stat(registry).st_mode), 0o600)
        with open(registry, encoding="utf-8") as handle:
            payload = handle.read()
        self.assertNotIn("base", payload)
        self.assertNotIn("tracked.txt", payload)

    def test_current_project_resolves_from_a_subdirectory_and_auto_registers(self):
        nested = os.path.join(self.repo, "src", "nested")
        os.makedirs(nested)
        project = pi_project.resolve(cwd=nested, environ=self.env)
        self.assertEqual(project["root"], self.repo)
        self.assertEqual(
            pi_project.resolve(selector=project["id"], cwd=self.temp, environ=self.env)["root"],
            self.repo,
        )

    def test_duplicate_names_are_stable_and_removal_is_explicit(self):
        second = os.path.join(self.temp, "other", "project")
        os.makedirs(second)
        subprocess.run(["git", "-C", second, "init", "-b", "main"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", second, "config", "user.email", "x@y.invalid"], check=True)
        subprocess.run(["git", "-C", second, "config", "user.name", "Test"], check=True)
        with open(os.path.join(second, "x"), "w", encoding="utf-8") as handle:
            handle.write("x")
        subprocess.run(["git", "-C", second, "add", "x"], check=True)
        subprocess.run(["git", "-C", second, "commit", "-m", "base"], check=True, stdout=subprocess.DEVNULL)

        first = pi_project.register(self.repo, name="same", environ=self.env)["project"]
        other = pi_project.register(os.path.realpath(second), name="same", environ=self.env)["project"]
        self.assertEqual((first["name"], other["name"]), ("same", "same-2"))
        removed = pi_project.remove(first["id"], environ=self.env)
        self.assertEqual(removed["project"]["id"], first["id"])
        self.assertEqual(len(pi_project.list_projects(self.env)["projects"]), 1)

    def test_clone_imports_a_project_into_the_managed_project_directory(self):
        cloned = pi_project.clone(self.repo, "managed-copy", environ=self.env)
        project = cloned["project"]
        self.assertEqual(cloned["status"], "cloned")
        self.assertEqual(project["name"], "managed-copy")
        self.assertTrue(project["root"].startswith(os.path.realpath(os.path.join(self.config, "kimiflow-projects"))))
        self.assertEqual(
            subprocess.run(
                ["git", "-C", project["root"], "rev-parse", "HEAD"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip(),
            self.git("rev-parse", "HEAD").stdout.strip(),
        )

    def test_numbered_and_explicit_runs_preserve_exact_identity(self):
        os.makedirs(os.path.join(self.repo, ".kimiflow", "run-7-2-model-frontier"))
        worker = "worker-00000001"
        self.assertEqual(
            pi_project.derive_run(self.repo, "Continue Run 7.2 now", worker),
            ".kimiflow/run-7-2-model-frontier",
        )
        self.assertEqual(
            pi_project.derive_run(self.repo, "Use .kimiflow/exact-run now", worker),
            ".kimiflow/exact-run",
        )
        self.assertEqual(
            pi_project.derive_run(self.repo, "Stable retry", "worker-00000001"),
            pi_project.derive_run(self.repo, "Stable retry", "worker-00000002"),
        )

    def test_clean_primary_still_allocates_an_isolated_fleet_worktree(self):
        result = pi_project.allocate(
            self.repo,
            "Build isolated project handling",
            "worker-00000001",
            environ=self.env,
        )
        self.assertEqual(result["status"], "allocated")
        self.assertEqual(result["route"], "worktree")
        self.assertNotEqual(result["root"], self.repo)
        self.assertTrue(os.path.isdir(result["root"]))
        self.assertEqual(
            subprocess.run(
                ["git", "-C", result["root"], "rev-parse", "--show-toplevel"],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip(),
            result["root"],
        )

    def test_fourth_fleet_task_is_durably_queued(self):
        values = [
            pi_project.allocate(
                self.repo,
                "Task %s" % index,
                "worker-%08d" % index,
                environ=self.env,
            )
            for index in range(1, 5)
        ]
        self.assertEqual([item["status"] for item in values[:3]], ["allocated"] * 3)
        self.assertEqual(values[3]["status"], "queued")
        self.assertEqual(values[3]["queue_position"], 1)

    def test_dead_runner_bridge_can_be_adopted_without_changing_provider_identity(self):
        from kimiflow_core import runner

        receipt = {
            "schema_version": 1,
            "host": "pi",
            "adapter": "pi-fixture",
            "root": self.repo,
            "session_id": "provider-session-0001",
            "thread_id": "provider-session-0001",
            "status": "transport_error",
            "started_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "turns": 2,
            "controller_pid": 99999999,
            "active_run": ".kimiflow/exact-run",
            "bridge": {
                "schema_version": 1,
                "captain_session_id": "captain-session-old",
                "worker_id": "worker-00000001",
            },
        }
        runner.write_receipt(self.repo, receipt)

        adopted = pi_project.adopt(
            self.repo,
            "captain-session-new",
            expected_captain_id="captain-session-old",
            expected_worker_id="worker-00000001",
        )
        current = runner.load_receipt(self.repo)

        self.assertEqual(adopted["worker_id"], "worker-00000001")
        self.assertEqual(adopted["provider_session_id"], "provider-session-0001")
        self.assertEqual(current["bridge"]["captain_session_id"], "captain-session-new")
        self.assertEqual(current["session_id"], "provider-session-0001")

        runner.write_receipt(self.repo, {
            **current,
            "status": "starting",
            "controller_pid": 99999998,
        })
        restarted = pi_project.adopt(
            self.repo,
            "captain-session-after-start",
            expected_captain_id="captain-session-new",
            expected_worker_id="worker-00000001",
        )
        self.assertEqual(restarted["status"], "adopted")
        with self.assertRaises(pi_project.ProjectError):
            pi_project.adopt(
                self.repo,
                "captain-session-other",
                expected_captain_id="captain-session-old",
                expected_worker_id="worker-00000001",
            )

    def test_live_runner_bridge_refuses_adoption(self):
        from kimiflow_core import runner

        runner.write_receipt(self.repo, {
            "schema_version": 1,
            "host": "pi",
            "adapter": "pi-fixture",
            "root": self.repo,
            "session_id": "provider-session-0001",
            "thread_id": "provider-session-0001",
            "status": "transport_error",
            "started_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "turns": 2,
            "controller_pid": os.getpid(),
            "active_run": ".kimiflow/exact-run",
            "bridge": {
                "schema_version": 1,
                "captain_session_id": "captain-session-old",
                "worker_id": "worker-00000001",
            },
        })
        with self.assertRaisesRegex(pi_project.ProjectError, "still alive"):
            pi_project.adopt(self.repo, "captain-session-new")

    def test_registry_symlink_fails_closed(self):
        os.makedirs(self.config, mode=0o700)
        outside = os.path.join(self.temp, "outside.json")
        with open(outside, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": 1, "projects": []}, handle)
        os.symlink(outside, os.path.join(self.config, pi_project.REGISTRY_NAME))
        with self.assertRaises(pi_project.ProjectError):
            pi_project.list_projects(self.env)


if __name__ == "__main__":
    unittest.main()
