import json
import os
import shutil
import subprocess
import tempfile
import unittest

from kimiflow_core import codebase_basis


class CodebaseBasisTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "test@example.test"], check=True)
        with open(os.path.join(self.root, "a.txt"), "w", encoding="utf-8") as handle:
            handle.write("one\n")
        subprocess.run(["git", "-C", self.root, "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "base"], check=True)

    def test_capture_is_deterministic_and_has_exact_four_keys(self):
        first = codebase_basis.capture(self.root, ["new.py", "a.txt"])
        second = codebase_basis.capture(self.root, ["a.txt", "new.py"])
        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {"head", "affected_paths", "snapshot_sha256", "map_coverage"},
        )
        self.assertEqual([row["path"] for row in first["affected_paths"]], ["a.txt", "new.py"])
        self.assertEqual(first["affected_paths"][1], {
            "path": "new.py", "type": "missing", "sha256": None,
        })
        self.assertEqual(first["map_coverage"]["status"], "missing")

    def test_verify_detects_bytes_type_and_affected_path_drift(self):
        basis = codebase_basis.capture(self.root, ["a.txt", "new.py"])
        self.assertEqual(codebase_basis.verify(self.root, basis, ["a.txt", "new.py"]), [])

        with open(os.path.join(self.root, "a.txt"), "w", encoding="utf-8") as handle:
            handle.write("two\n")
        self.assertIn("affected_path_drift:a.txt", codebase_basis.verify(
            self.root, basis, ["a.txt", "new.py"],
        ))

        with open(os.path.join(self.root, "new.py"), "w", encoding="utf-8") as handle:
            handle.write("created\n")
        self.assertIn("affected_path_drift:new.py", codebase_basis.verify(
            self.root, basis, ["a.txt", "new.py"],
        ))
        self.assertIn("affected_paths_changed", codebase_basis.verify(
            self.root, basis, ["a.txt"],
        ))

    def test_symlink_and_directory_snapshots_do_not_follow_aliases(self):
        os.mkdir(os.path.join(self.root, "folder"))
        with open(os.path.join(self.root, "folder", "value.txt"), "w", encoding="utf-8") as handle:
            handle.write("value\n")
        os.symlink("a.txt", os.path.join(self.root, "alias"))
        basis = codebase_basis.capture(self.root, ["folder", "alias"])
        rows = {row["path"]: row for row in basis["affected_paths"]}
        self.assertEqual(rows["alias"]["type"], "symlink")
        self.assertEqual(rows["folder"]["type"], "directory")
        os.unlink(os.path.join(self.root, "alias"))
        os.symlink("folder/value.txt", os.path.join(self.root, "alias"))
        self.assertIn("affected_path_drift:alias", codebase_basis.verify(
            self.root, basis, ["folder", "alias"],
        ))

    def test_rejects_affected_path_below_symlinked_parent(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with open(os.path.join(outside, "secret.txt"), "w", encoding="utf-8") as handle:
            handle.write("outside\n")
        os.symlink(outside, os.path.join(self.root, "escape"))

        with self.assertRaises(codebase_basis.BasisError):
            codebase_basis.capture(self.root, ["escape/secret.txt"])

    def test_run_create_and_verify_require_exact_declared_paths(self):
        run_dir = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Affected files: a.txt, future.py\n")
        created = codebase_basis.create_for_run(self.root, run_dir, write=True)
        self.assertEqual(created["status"], "OPEN")
        path = os.path.join(run_dir, "CODEBASE-BASIS.json")
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
        self.assertEqual(codebase_basis.verify_for_run(self.root, run_dir)["status"], "OPEN")
        stored["extra"] = True
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(stored, handle)
        verdict = codebase_basis.verify_for_run(self.root, run_dir)
        self.assertEqual(verdict["status"], "CLOSED")
        self.assertIn("basis_keys_invalid", verdict["details"])

    def test_rejects_absolute_traversal_duplicate_and_git_paths(self):
        for paths in (
            ["/tmp/a"],
            ["../a"],
            ["a.txt", "a.txt"],
            [".git/config"],
            ["line\nbreak"],
        ):
            with self.subTest(paths=paths), self.assertRaises(codebase_basis.BasisError):
                codebase_basis.capture(self.root, paths)


if __name__ == "__main__":
    unittest.main()
