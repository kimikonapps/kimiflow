import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from memory_router import workspace_scope


class WorkspaceScopeCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kimiflow-scope-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = tempfile.mkdtemp(prefix="kimiflow-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.makedirs(os.path.join(self.home, "metrics"))
        with open(os.path.join(self.home, "metrics", "salt"), "w", encoding="utf-8") as handle:
            handle.write("a" * 64 + "\n")
        self.env = mock.patch.dict(
            os.environ,
            {"KIMIFLOW_HOME": self.home, "HOME": self.home, "PATH": os.environ.get("PATH", "")},
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        for unit in ("api", "web"):
            directory = os.path.join(self.root, "packages", unit)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                json.dump({"name": unit}, handle)

    def test_resolve_scope_is_bounded_and_fail_safe(self):
        selected = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/main.py"]
        )
        public = workspace_scope.scope_json(selected)
        self.assertEqual(public["status"], "active")
        self.assertEqual(public["units"], [
            {"path": "packages/api", "marker": "package.json"}
        ])
        self.assertNotIn("_receipts", public)

        external = workspace_scope.resolve_scope(self.root, ["../outside.py"])
        self.assertEqual(workspace_scope.scope_json(external)["status"], "fallback")
        self.assertEqual(workspace_scope.scope_json(external)["reason"], "path_outside_root")

        overflow = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/f%d.py" % index for index in range(33)]
        )
        self.assertEqual(workspace_scope.scope_json(overflow)["reason"], "too_many_paths")

        mixed = workspace_scope.resolve_scope(
            self.root, ["README.md", "packages/api/src/main.py"]
        )
        self.assertEqual(workspace_scope.scope_json(mixed)["reason"], "mixed_project_scope")

    def test_new_nearer_manifest_invalidates_resolution(self):
        selected = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/main.py"]
        )
        self.assertTrue(workspace_scope.revalidate_scope(self.root, selected))
        with open(
            os.path.join(self.root, "packages", "api", "src", "pyproject.toml"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("[project]\nname='nested'\n")
        self.assertFalse(workspace_scope.revalidate_scope(self.root, selected))

    def test_multi_evidence_and_flattened_learning_classification(self):
        selected = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/main.py"]
        )
        mixed = {
            "scope": "project",
            "evidence": ["packages/web/src/a.py", "packages/api/src/b.py"],
        }
        foreign = {
            "scope": "project",
            "evidence": ["packages/web/src/a.py", "packages/web/src/b.py"],
        }
        unbound = {"scope": "project", "evidence": ["NOT VERIFIED"]}
        shadow = {"kind": "learning", "ref": "packages/web/src/a.py"}
        self.assertEqual(
            workspace_scope.classify_hit(self.root, selected, "learnings", mixed), "local"
        )
        self.assertEqual(
            workspace_scope.classify_hit(self.root, selected, "learnings", foreign), "foreign"
        )
        self.assertEqual(
            workspace_scope.classify_hit(self.root, selected, "learnings", unbound), "shared"
        )
        self.assertEqual(
            workspace_scope.classify_hit(self.root, selected, "index", shadow), "foreign"
        )

    def test_worktree_identity_is_salted_shared_and_path_free(self):
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Test"], check=True)
        with open(os.path.join(self.root, "tracked.txt"), "w", encoding="utf-8") as handle:
            handle.write("x\n")
        subprocess.run(["git", "-C", self.root, "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "init"], check=True)
        linked = tempfile.mkdtemp(prefix="kimiflow-linked-")
        shutil.rmtree(linked)
        self.addCleanup(shutil.rmtree, linked, ignore_errors=True)
        subprocess.run(
            ["git", "-C", self.root, "worktree", "add", "-q", "-b", "linked", linked],
            check=True,
        )
        self.addCleanup(
            subprocess.run,
            ["git", "-C", self.root, "worktree", "remove", "--force", linked],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        main = workspace_scope.worktree_identity(self.root)
        other = workspace_scope.worktree_identity(linked)
        self.assertRegex(main["repository_id"], r"^repo_[0-9a-f]{64}$")
        self.assertEqual(main["repository_id"], other["repository_id"])
        self.assertNotEqual(main["worktree_id"], other["worktree_id"])
        rendered = json.dumps({"main": main, "other": other})
        self.assertNotIn(self.root, rendered)
        self.assertNotIn(linked, rendered)

    def test_state_scope_paths_are_bounded(self):
        run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(run)
        query = os.path.join(run, "INTENT.md")
        with open(query, "w", encoding="utf-8") as handle:
            handle.write("auth\n")
        with open(os.path.join(run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Affected files:\n- packages/api/src/a.py\n- packages/api/src/b.py\nPhase 1: done\n")
        paths, reason = workspace_scope.scope_paths_for_query_file(query)
        self.assertEqual(paths, ["packages/api/src/a.py", "packages/api/src/b.py"])
        self.assertEqual(reason, "state_affected_files")


if __name__ == "__main__":
    unittest.main()
