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
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="kimiflow-scope-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="kimiflow-home-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.makedirs(os.path.join(self.home, "metrics"))
        with open(os.path.join(self.home, "metrics", "salt"), "w", encoding="utf-8") as handle:
            handle.write("a" * 64 + "\n")
        os.chmod(os.path.join(self.home, "metrics", "salt"), 0o600)
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

        controlled = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/bad\nname.py"]
        )
        self.assertEqual(controlled["status"], "fallback")
        self.assertEqual(controlled["reason"], "invalid_scope_path")

        surrogate = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/bad\udcff.py"]
        )
        self.assertEqual(surrogate["status"], "fallback")
        self.assertEqual(surrogate["reason"], "invalid_scope_path")

        overflow = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/f%d.py" % index for index in range(33)]
        )
        with mock.patch(
            "memory_router.workspace_scope.worktree_identity",
            return_value={"status": "unavailable", "reason": "test",
                          "repository_id": None, "worktree_id": None},
        ) as worktree_identity:
            public_overflow = workspace_scope.scope_json(overflow)
        self.assertEqual(public_overflow["reason"], "too_many_paths")
        worktree_identity.assert_called_once_with(self.root)

        mixed = workspace_scope.resolve_scope(
            self.root, ["README.md", "packages/api/src/main.py"]
        )
        self.assertEqual(workspace_scope.scope_json(mixed)["reason"], "mixed_project_scope")

        consumed = []

        def unbounded_paths():
            index = 0
            while True:
                consumed.append(index)
                yield "packages/api/src/f%d.py" % index
                index += 1

        bounded = workspace_scope.resolve_scope(self.root, unbounded_paths())
        self.assertEqual(bounded["reason"], "too_many_paths")
        self.assertEqual(len(consumed), workspace_scope.MAX_SCOPE_PATHS + 1)
        self.assertEqual(len(bounded["_paths"]), workspace_scope.MAX_SCOPE_PATHS + 1)

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
            "evidence": ["packages/web/src/a.py:1", "packages/api/src/b.py:1"],
        }
        foreign = {
            "scope": "project",
            "evidence": ["packages/web/src/a.py:1", "packages/web/src/b.py:1"],
        }
        unbound = {"scope": "project", "evidence": ["NOT VERIFIED"]}
        unbound_foreign = {
            "scope": "project",
            "evidence": ["https://example.test/spec", "packages/web/src/a.py:1"],
        }
        controlled = {
            "scope": "project",
            "evidence": ["packages/web/src/a.py:1\n"],
        }
        shadow = {"kind": "learning", "ref": "packages/web/src/a.py:1"}
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
            workspace_scope.classify_hit(
                self.root, selected, "learnings", unbound_foreign
            ),
            "shared",
        )
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "learnings", controlled
            ),
            "shared",
        )
        self.assertEqual(
            workspace_scope.classify_hit(self.root, selected, "index", shadow), "shared"
        )

        upper_api = os.path.join(self.root, "packages", "API")
        lower_api = os.path.join(self.root, "packages", "api")
        if os.path.isdir(upper_api) and os.path.samefile(lower_api, upper_api):
            case_alias = {"path": "packages/API/src/a.py", "line": 1}
            self.assertEqual(
                workspace_scope.classify_hit(
                    self.root, selected, "facts", case_alias
                ),
                "local",
            )
            aliased = workspace_scope.resolve_scope(
                self.root,
                ["packages/api/src/a.py", "packages/API/src/b.py"],
            )
            self.assertEqual(len(aliased["units"]), 1)

        fingerprinted = {
            "evidence": ["packages/api/Dockerfile"],
            "evidence_fingerprints": [{
                "ref": "packages/api/Dockerfile",
                "path": "packages/api/Dockerfile",
                "status": "current",
                "digest_algorithm": "sha256",
                "digest": "a" * 64,
                "sha256": "a" * 64,
            }],
        }
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "learnings", fingerprinted
            ),
            "local",
        )
        fingerprinted["evidence"] = [" packages/api/Dockerfile"]
        fingerprinted["evidence_fingerprints"][0].update({
            "ref": " packages/api/Dockerfile",
            "path": " packages/api/Dockerfile",
        })
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "learnings", fingerprinted
            ),
            "shared",
        )
        fingerprinted["evidence"] = ["packages/api/Dockerfile"]
        fingerprinted["evidence_fingerprints"][0].update({
            "ref": "packages/api/Dockerfile",
            "path": "packages/api/Dockerfile",
        })
        fingerprinted["evidence_fingerprints"][0]["digest"] = "not-a-digest"
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "learnings", fingerprinted
            ),
            "shared",
        )
        fingerprinted["evidence_fingerprints"][0].update({
            "digest": "a" * 64,
            "path": "packages/api/not-Dockerfile",
        })
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "learnings", fingerprinted
            ),
            "shared",
        )
        unicode_line = {"evidence": ["packages/api/Dockerfile:\u0661"]}
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "learnings", unicode_line
            ),
            "shared",
        )

        fingerprinted["evidence"] = ["../packages/api/Dockerfile"]
        fingerprinted["evidence_fingerprints"][0].update({
            "ref": "../packages/api/Dockerfile",
            "path": "../packages/api/Dockerfile",
        })
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "learnings", fingerprinted
            ),
            "shared",
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

        main_fallback = workspace_scope.scope_json(
            workspace_scope.fallback_scope(self.root, [], "test")
        )
        linked_fallback = workspace_scope.scope_json(
            workspace_scope.fallback_scope(linked, [], "test")
        )
        self.assertEqual(
            main_fallback["worktree"]["repository_id"],
            linked_fallback["worktree"]["repository_id"],
        )
        self.assertNotEqual(
            main_fallback["worktree"]["worktree_id"],
            linked_fallback["worktree"]["worktree_id"],
        )

        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(linked, ".git")}):
            poisoned = workspace_scope.worktree_identity(self.root)
        self.assertEqual(poisoned["repository_id"], main["repository_id"])
        self.assertEqual(poisoned["worktree_id"], main["worktree_id"])

    def test_candidate_state_and_unit_metadata_are_bounded(self):
        selected = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/main.py"]
        )
        with mock.patch(
            "memory_router.workspace_scope._path_scope",
            wraps=workspace_scope._path_scope,
        ) as path_scope:
            for index in range(1000):
                workspace_scope.classify_hit(
                    self.root,
                    selected,
                    "facts",
                    {"path": "packages/web/src/f%d.py" % index, "line": 1},
                )
            self.assertEqual(path_scope.call_count, 1)
            for index in range(workspace_scope.MAX_CANDIDATE_DIRECTORIES + 20):
                workspace_scope.classify_hit(
                    self.root,
                    selected,
                    "facts",
                    {"path": "unknown/d%d/file.py" % index, "line": 1},
                )
            self.assertEqual(
                path_scope.call_count, workspace_scope.MAX_CANDIDATE_DIRECTORIES
            )
        self.assertEqual(
            len(selected["_candidate_cache"]),
            workspace_scope.MAX_CANDIDATE_DIRECTORIES,
        )
        self.assertEqual(
            len(selected["_observed"]), workspace_scope.MAX_CANDIDATE_DIRECTORIES
        )
        self.assertEqual(selected["_overflow_reason"], "scope_classification_limit")
        self.assertFalse(workspace_scope.revalidate_scope(self.root, selected))

        parent = os.path.join(self.root, "long")
        parts = ["segment%02dxxxxxxxxxxxxxxxxxxxx" % index for index in range(20)]
        unit = os.path.join(parent, *parts)
        os.makedirs(os.path.join(unit, "src"))
        with open(os.path.join(unit, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        long_scope = workspace_scope.resolve_scope(
            self.root, [os.path.join(unit, "src", "a.py")]
        )
        self.assertEqual(long_scope["status"], "fallback")
        self.assertEqual(long_scope["reason"], "unit_path_too_long")

        paths = []
        for index in range(8):
            name = ("unit%02d-" % index) + ("x" * 135)
            directory = os.path.join(self.root, "many", name)
            os.makedirs(os.path.join(directory, "src"))
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as handle:
                handle.write("{}\n")
            paths.append(os.path.join(directory, "src", "a.py"))
        total_scope = workspace_scope.resolve_scope(self.root, paths)
        self.assertEqual(total_scope["status"], "fallback")
        self.assertEqual(total_scope["reason"], "unit_metadata_too_large")

        unsafe = os.path.join(self.root, "bad\nunit")
        os.makedirs(os.path.join(unsafe, "src"))
        with open(os.path.join(unsafe, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        unsafe_scope = workspace_scope.resolve_scope(
            self.root, [os.path.join(unsafe, "src", "a.py")]
        )
        self.assertEqual(unsafe_scope["status"], "fallback")
        self.assertEqual(unsafe_scope["reason"], "invalid_scope_path")

        with mock.patch(
            "memory_router.workspace_scope._path_scope",
            return_value=("unit", "packages/bad\udcff", "package.json", ()),
        ):
            undecodable = workspace_scope.resolve_scope(self.root, ["bad.py"])
        self.assertEqual(undecodable["status"], "fallback")
        self.assertEqual(undecodable["reason"], "unsafe_unit_path")

    def test_directory_and_file_classification_do_not_share_cache_entries(self):
        child = os.path.join(self.root, "packages", "api", "child")
        os.makedirs(child)
        with open(os.path.join(child, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        selected = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/main.py"]
        )
        directory_hit = {"path": "packages/api/child"}
        dotted_directory_hit = {"path": "packages/api/child.dir"}
        file_hit = {"path": "packages/api/Dockerfile", "line": 1}
        dotted_child = os.path.join(self.root, "packages", "api", "child.dir")
        os.makedirs(dotted_child)
        with open(os.path.join(dotted_child, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "facts", directory_hit
            ),
            "shared",
        )
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, selected, "facts", dotted_directory_hit
            ),
            "shared",
        )
        self.assertEqual(
            workspace_scope.classify_hit(self.root, selected, "facts", file_hit),
            "local",
        )

        reverse = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/main.py"]
        )
        self.assertEqual(
            workspace_scope.classify_hit(self.root, reverse, "facts", file_hit),
            "local",
        )
        self.assertEqual(
            workspace_scope.classify_hit(
                self.root, reverse, "facts", directory_hit
            ),
            "shared",
        )

    def test_symlink_paths_and_symlinked_salt_parent_fail_closed(self):
        external = tempfile.mkdtemp(prefix="kimiflow-external-")
        self.addCleanup(shutil.rmtree, external, ignore_errors=True)
        os.makedirs(os.path.join(external, "src"))
        with open(os.path.join(external, "package.json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        os.symlink(external, os.path.join(self.root, "linked-package"))
        selected = workspace_scope.resolve_scope(
            self.root, ["linked-package/src/a.py"]
        )
        self.assertEqual(selected["status"], "fallback")
        self.assertEqual(selected["reason"], "unsafe_scope_path")

        linked_home = os.path.join(self.root, "linked-home")
        os.symlink(self.home, linked_home)
        with mock.patch.dict(os.environ, {"KIMIFLOW_HOME": linked_home}):
            identity = workspace_scope.worktree_identity(self.root)
        self.assertEqual(identity["status"], "unavailable")
        self.assertEqual(identity["reason"], "salt_unavailable")

        marker = os.path.join(self.root, "packages", "api", "package.json")
        os.unlink(marker)
        os.mkfifo(marker)
        fifo_scope = workspace_scope.resolve_scope(
            self.root, ["packages/api/src/a.py"]
        )
        self.assertEqual(fifo_scope["status"], "fallback")
        self.assertIn(fifo_scope["reason"], ("unsafe_boundary", "unreadable_boundary"))

    def test_state_parser_rejects_duplicate_header_and_fifo(self):
        run = os.path.join(self.root, ".kimiflow", "bad-state")
        os.makedirs(run)
        query = os.path.join(run, "INTENT.md")
        with open(query, "w", encoding="utf-8") as handle:
            handle.write("auth\n")
        state = os.path.join(run, "STATE.md")
        with open(state, "w", encoding="utf-8") as handle:
            handle.write(
                "Affected files:\n- packages/api/src/a.py\n"
                "Affected files:\n- packages/web/src/a.py\n"
            )
        paths, reason = workspace_scope.scope_paths_for_query_file(query, self.root)
        self.assertEqual(paths, [])
        self.assertEqual(reason, "state_affected_files_malformed")

        os.unlink(state)
        os.mkfifo(state)
        paths, reason = workspace_scope.scope_paths_for_query_file(query, self.root)
        self.assertEqual(paths, [])
        self.assertEqual(reason, "state_unreadable_or_overflow")

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

        paths, reason, receipt = workspace_scope.scope_paths_for_query_file(
            query, self.root, include_receipt=True
        )
        selected = workspace_scope.resolve_scope(
            self.root, paths, source=reason, state_receipt=receipt
        )
        self.assertTrue(workspace_scope.revalidate_scope(self.root, selected))
        with open(os.path.join(run, "STATE.md"), "a", encoding="utf-8") as handle:
            handle.write("# changed during recall\n")
        self.assertFalse(workspace_scope.revalidate_scope(self.root, selected))

        with open(os.path.join(run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "Affected files: "
                + ",".join("packages/api/src/f%d.py" % index for index in range(100))
                + "\n"
            )
        paths, reason = workspace_scope.scope_paths_for_query_file(query, self.root)
        self.assertEqual(len(paths), workspace_scope.MAX_SCOPE_PATHS + 1)
        self.assertEqual(reason, "state_affected_files_overflow")


if __name__ == "__main__":
    unittest.main()
