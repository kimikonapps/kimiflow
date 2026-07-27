import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import release_memory


class _Completed:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ReleaseMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(
            ["git", "-C", self.root, "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", self.root, "config", "user.name", "Test"],
            check=True,
        )
        with open(os.path.join(self.root, "tracked.txt"), "w", encoding="utf-8") as handle:
            handle.write("one\n")
        os.makedirs(os.path.join(self.root, "source"))
        with open(
            os.path.join(self.root, "source", "tracked.txt"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("tracked\n")
        subprocess.run(["git", "-C", self.root, "add", "."], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "fixture"], check=True
        )

    def test_memory_is_project_bound_and_secret_free(self):
        declarations = [
            {
                "name": "repository",
                "type": "repository",
                "publication_target": True,
            }
        ]
        inputs = {"repository": "org/one"}
        bound = release_memory.binding(self.root, inputs, declarations)
        secret = "ghp_fixture_secret_that_must_never_persist"
        written = release_memory.write_verified_memory(
            self.root,
            bound,
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            {
                "kind": "github_cli",
                "account_sha256": release_memory.digest({"account": "release-bot"}),
                "_credential": secret,
            },
            ["preflight", "publish"],
            failure_classes={"network": 1},
            duration_totals={"provider": 12},
        )
        path = os.path.join(self.root, ".kimiflow", "release", "MEMORY.json")
        with open(path, encoding="utf-8") as handle:
            payload = handle.read()
        self.assertNotIn(secret, payload)
        self.assertNotIn(self.root, payload)
        self.assertNotIn("org/one", payload)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(
            release_memory.read_memory(self.root, bound)["successful_steps"],
            ["preflight", "publish"],
        )
        self.assertEqual(
            release_memory.read_memory(self.root, bound)["duration_totals"],
            {"provider": {"runs": 1, "milliseconds": 12}},
        )
        release_memory.write_verified_memory(
            self.root,
            bound,
            "sha256:" + "1" * 64,
            "sha256:" + "3" * 64,
            written["identity"],
            ["publish"],
            generation=2,
            duration_totals={"provider": 8},
            previous_duration_totals=written["duration_totals"],
        )
        self.assertEqual(
            release_memory.read_memory(self.root, bound)["duration_totals"],
            {"provider": {"runs": 2, "milliseconds": 20}},
        )
        foreign = dict(bound, target_sha256=release_memory.digest("org/two"))
        with self.assertRaisesRegex(
            release_memory.ReleaseMemoryError, "binding_mismatch"
        ):
            release_memory.read_memory(self.root, foreign)
        self.assertEqual(written["binding"], bound)

        subprocess.run(
            [
                "git", "-C", self.root, "remote", "add", "origin",
                "git@github.com:org/one.git",
            ],
            check=True,
        )
        github_bound = release_memory.binding(
            self.root, inputs, declarations, provider="github"
        )
        self.assertEqual(github_bound["target_sha256"], bound["target_sha256"])
        with self.assertRaisesRegex(
            release_memory.ReleaseMemoryError, "binding_mismatch"
        ):
            release_memory.binding(
                self.root,
                {"repository": "org/two"},
                declarations,
                provider="github",
            )

        with tempfile.TemporaryDirectory() as foreign_root:
            subprocess.run(["git", "init", "-q", foreign_root], check=True)
            os.makedirs(os.path.join(foreign_root, ".kimiflow"))
            shutil.copytree(
                os.path.join(self.root, ".kimiflow", "release"),
                os.path.join(foreign_root, ".kimiflow", "release"),
            )
            foreign_binding = release_memory.binding(
                foreign_root, inputs, declarations
            )
            with self.assertRaisesRegex(
                release_memory.ReleaseMemoryError, "binding_mismatch"
            ):
                release_memory.read_memory(foreign_root, foreign_binding)

    def test_environment_and_github_resolvers_are_ephemeral_and_learn_verified_account(self):
        secret = "generic-provider-secret"
        credentials, public = release_memory.resolve_identity(
            self.root,
            {"provider": "environment", "environment": ["DEPLOY_TOKEN"]},
            environment={"PATH": os.defpath, "DEPLOY_TOKEN": secret},
        )
        self.assertEqual(credentials, {"DEPLOY_TOKEN": secret})
        self.assertNotIn(secret, json.dumps(public))
        with self.assertRaisesRegex(
            release_memory.ReleaseMemoryError,
            "identity_environment_invalid",
        ):
            release_memory.resolve_identity(
                self.root,
                {
                    "provider": "environment",
                    "environment": ["DEPLOY_TOKEN"],
                },
                environment={
                    "PATH": os.defpath,
                    "DEPLOY_TOKEN": "s3cr3t7",
                },
            )
        for name in (
            "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "XDG_STATE_HOME"
        ):
            self.assertFalse(
                release_memory.valid_environment_name(
                    name, credential=False
                )
            )
            self.assertFalse(
                release_memory.valid_environment_name(
                    name, credential=True
                )
            )
        with tempfile.TemporaryDirectory() as home:
            sealed = release_memory.sealed_environment(
                {"PATH": os.defpath},
                home=home,
            )
            self.assertEqual(
                sealed["XDG_STATE_HOME"], os.path.join(home, "state")
            )
            self.assertEqual(
                sealed["XDG_DATA_HOME"], os.path.join(home, "data")
            )
            self.assertEqual(
                sealed["XDG_RUNTIME_DIR"], os.path.join(home, "runtime")
            )
        gh_directory = os.path.join(self.root, "gh-bin")
        os.makedirs(gh_directory)
        gh_path = os.path.join(gh_directory, "gh")
        with open(gh_path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        os.chmod(gh_path, 0o755)
        github_path = gh_directory + os.pathsep + os.defpath
        github_tool = release_memory.tool_fingerprints(
            ["gh"], {"PATH": github_path}, cwd=self.root
        )

        native_calls = []
        capability_sandboxes = []

        def native_runner(argv, **kwargs):
            native_calls.append(list(argv))
            if argv[:2] == ["gh", "api"]:
                environment = kwargs["env"]
                sandbox = environment["HOME"]
                capability_sandboxes.append(sandbox)
                self.assertTrue(os.path.isabs(sandbox))
                self.assertEqual(
                    environment["XDG_CACHE_HOME"],
                    os.path.join(sandbox, "cache"),
                )
                self.assertEqual(
                    environment["XDG_CONFIG_HOME"],
                    os.path.join(sandbox, "config"),
                )
                self.assertEqual(
                    environment["XDG_STATE_HOME"],
                    os.path.join(sandbox, "state"),
                )
                state = os.path.join(
                    environment["XDG_STATE_HOME"], "gh", "device-id"
                )
                os.makedirs(os.path.dirname(state), exist_ok=True)
                with open(state, "w", encoding="utf-8") as handle:
                    handle.write("ephemeral")
                return _Completed(stdout=b"true\n")
            raise AssertionError("native identity must not read local accounts")

        native_credentials, native_public = release_memory.resolve_identity(
            self.root,
            {"provider": "github"},
            environment={
                "PATH": github_path,
                "GITHUB_TOKEN": "native-runtime-only",
            },
            runner=native_runner,
            repository="org/project",
            expected_tool_sha256=github_tool,
        )
        self.assertEqual(
            native_credentials, {"GH_TOKEN": "native-runtime-only"}
        )
        self.assertEqual(native_public["kind"], "github_native")
        self.assertEqual(native_public["resolver_probes"], 1)
        self.assertNotIn("native-runtime-only", json.dumps(native_public))
        self.assertEqual(len(native_calls), 1)
        self.assertIn("repos/org/project", native_calls[0])
        self.assertEqual(len(capability_sandboxes), 1)
        self.assertFalse(os.path.exists(capability_sandboxes[0]))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".local")))

        def read_only_native(argv, **kwargs):
            return _Completed(stdout=b"false\n")

        with self.assertRaisesRegex(
            release_memory.ReleaseMemoryError, "identity_unavailable"
        ) as read_only:
            release_memory.resolve_identity(
                self.root,
                {"provider": "github"},
                environment={
                    "PATH": github_path,
                    "GITHUB_TOKEN": "read-only-runtime",
                },
                runner=read_only_native,
                repository="org/project",
                expected_tool_sha256=github_tool,
            )
        self.assertEqual(read_only.exception.probes, 1)

        calls = []

        def fake_runner(argv, **kwargs):
            recorded_environment = {
                name: "<credential>" if name == "GH_TOKEN" else value
                for name, value in kwargs["env"].items()
            }
            calls.append((list(argv), recorded_environment))
            if argv[:3] == ["gh", "auth", "status"]:
                return _Completed(
                    stdout=json.dumps(
                        {
                            "hosts": {
                                "github.com": [
                                    {
                                        "login": "release-bot",
                                        "active": False,
                                        "state": "success",
                                    },
                                    {
                                        "login": "developer",
                                        "active": True,
                                        "state": "success",
                                    },
                                ]
                            }
                        }
                    ).encode()
                )
            if argv[:2] == ["gh", "api"] and any(
                item.endswith("/releases/latest") for item in argv
            ):
                return _Completed(stdout=b"release-bot\n")
            if argv[:2] == ["gh", "api"]:
                return _Completed(stdout=b"true\n")
            if argv[:3] == ["gh", "auth", "token"]:
                return _Completed(stdout=b"ghp_runtime_only\n")
            return _Completed(returncode=1)

        learned = {
            "identity": {
                "kind": "github_cli",
                "account_sha256": release_memory.digest(
                    {"account": "release-bot"}
                ),
            }
        }
        credentials, public = release_memory.resolve_identity(
            self.root,
            {"provider": "github"},
            memory=learned,
            environment={
                "PATH": github_path,
                "HOME": "/fixture-account-home",
                "XDG_CONFIG_HOME": "/fixture-account-config",
                "GH_HOST": "evil.example",
                "UNRELATED_TOKEN": "must-be-scrubbed",
            },
            runner=fake_runner,
            repository="org/project",
            expected_tool_sha256=github_tool,
        )
        self.assertEqual(credentials, {"GH_TOKEN": "ghp_runtime_only"})
        self.assertEqual(public["kind"], "github_cli")
        self.assertEqual(public["resolver_probes"], 3)
        self.assertFalse(
            any(call[0][:3] == ["gh", "auth", "switch"] for call in calls)
        )
        serialized_calls = json.dumps(calls)
        self.assertNotIn("must-be-scrubbed", serialized_calls)
        self.assertNotIn("ghp_runtime_only", serialized_calls)
        self.assertNotIn("evil.example", serialized_calls)
        self.assertIn("/fixture-account-home", serialized_calls)
        self.assertIn(github_path, serialized_calls)
        self.assertTrue(
            all(
                "--hostname" in argv and "github.com" in argv
                for argv, _ in calls
            )
        )
        self.assertTrue(
            any(
                argv[:2] == ["gh", "api"]
                and "repos/org/project" in argv
                and environment.get("GH_TOKEN") == "<credential>"
                for argv, environment in calls
            )
        )

        credentials, public = release_memory.resolve_identity(
            self.root,
            {"provider": "github"},
            environment={"PATH": github_path},
            runner=fake_runner,
            repository="org/project",
            expected_tool_sha256=github_tool,
        )
        self.assertEqual(credentials, {"GH_TOKEN": "ghp_runtime_only"})
        self.assertEqual(
            public["account_sha256"],
            release_memory.digest({"account": "release-bot"}),
        )
        self.assertEqual(public["resolver_probes"], 4)

        single_calls = []

        def single_account_runner(argv, **kwargs):
            single_calls.append(list(argv))
            if argv[:3] == ["gh", "auth", "status"]:
                return _Completed(
                    stdout=json.dumps(
                        {
                            "hosts": {
                                "github.com": [
                                    {
                                        "login": "only-account",
                                        "state": "success",
                                    }
                                ]
                            }
                        }
                    ).encode()
                )
            if argv[:3] == ["gh", "auth", "token"]:
                return _Completed(stdout=b"ghp_single_runtime\n")
            if argv[:2] == ["gh", "api"]:
                return _Completed(stdout=b"true\n")
            return _Completed(returncode=1)

        single_credentials, single_public = release_memory.resolve_identity(
            self.root,
            {"provider": "github"},
            environment={"PATH": github_path},
            runner=single_account_runner,
            repository="org/project",
            expected_tool_sha256=github_tool,
        )
        self.assertEqual(
            single_credentials, {"GH_TOKEN": "ghp_single_runtime"}
        )
        self.assertEqual(single_public["resolver_probes"], 3)
        self.assertTrue(
            any(
                call[:2] == ["gh", "api"]
                and "repos/org/project" in call
                for call in single_calls
            )
        )
        with self.assertRaisesRegex(
            release_memory.ReleaseMemoryError, "identity_ambiguous"
        ) as caught:
            release_memory.resolve_identity(
                self.root,
                {"provider": "github"},
                environment={"PATH": github_path},
                runner=fake_runner,
                expected_tool_sha256=github_tool,
            )
        self.assertEqual(caught.exception.probes, 1)

        def unavailable_runner(*args, **kwargs):
            raise FileNotFoundError("gh")

        with self.assertRaisesRegex(
            release_memory.ReleaseMemoryError, "identity_unavailable"
        ) as unavailable:
            release_memory.resolve_identity(
                self.root,
                {"provider": "github"},
                environment={"PATH": github_path},
                runner=unavailable_runner,
                expected_tool_sha256=github_tool,
            )
        self.assertEqual(unavailable.exception.probes, 1)

    def test_current_kimiflow_evidence_reuses_only_exact_check(self):
        run = os.path.join(self.root, ".kimiflow", "fixture-run")
        os.makedirs(run)
        with open(os.path.join(run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Phase 6: done\n")
        with open(
            os.path.join(run, "VERIFICATION.md"), "w", encoding="utf-8"
        ) as handle:
            handle.write(
                "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->\n"
                "<!-- kimiflow:conformance contract=1 status=converged "
                "diff=passed strategy=passed -->\n"
            )
        self.assertTrue(release_memory.kimiflow_run_terminal(run))
        paths = release_memory.path_fingerprints(self.root, ["tracked.txt"])
        self.assertEqual(
            paths,
            release_memory.path_fingerprints(self.root, ["tracked.txt"]),
        )
        with open(
            os.path.join(self.root, "tracked.txt"), "w", encoding="utf-8"
        ) as handle:
            handle.write("two\n")
        self.assertNotEqual(
            paths,
            release_memory.path_fingerprints(self.root, ["tracked.txt"]),
        )
        directory = release_memory.path_fingerprints(self.root, ["source"])
        with mock.patch.dict(
            os.environ,
            {
                "GIT_INDEX_FILE": os.path.join(self.root, "foreign-index"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.attributesFile",
                "GIT_CONFIG_VALUE_0": os.path.join(self.root, "foreign"),
            },
            clear=False,
        ):
            self.assertEqual(
                directory,
                release_memory.path_fingerprints(self.root, ["source"]),
            )
        with open(
            os.path.join(self.root, "source", "untracked.txt"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("untracked\n")
        self.assertNotEqual(
            directory,
            release_memory.path_fingerprints(self.root, ["source"]),
        )
        os.unlink(os.path.join(self.root, "source", "untracked.txt"))
        with tempfile.TemporaryDirectory() as outside:
            with open(
                os.path.join(outside, "outside.txt"), "w", encoding="utf-8"
            ) as handle:
                handle.write("outside\n")
            os.symlink(outside, os.path.join(self.root, "linked-source"))
            with self.assertRaisesRegex(
                release_memory.ReleaseMemoryError, "evidence_path_unsafe"
            ):
                release_memory.path_fingerprints(
                    self.root, ["linked-source"]
                )
        tools = release_memory.tool_fingerprints(
            ["env", "PUBLIC_BUILD=1", "python3", "script.py"],
            {"PATH": os.environ.get("PATH", os.defpath)},
        )
        self.assertEqual(len(tools), 2)
        self.assertTrue(any(key.endswith(":env") for key in tools))
        self.assertTrue(any(key.endswith(":python3") for key in tools))
        for unsafe in (
            "HOME", "PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
            "JAVA_TOOL_OPTIONS", "PYTHONPATH", "CI",
        ):
            with self.assertRaises(release_memory.ReleaseMemoryError):
                release_memory.sealed_environment(
                    {unsafe: "ambient", "PATH": os.defpath},
                    declared_public=[unsafe],
                )
        environment = {"PATH": os.defpath, "PUBLIC_BUILD": "1"}
        digest = release_memory.declared_environment_digest(
            environment, ["PUBLIC_BUILD"]
        )
        environment["PUBLIC_BUILD"] = "2"
        self.assertNotEqual(
            digest,
            release_memory.declared_environment_digest(
                environment, ["PUBLIC_BUILD"]
            ),
        )

    def test_credential_temporary_directories_ignore_project_tmpdir(self):
        with mock.patch.object(tempfile, "tempdir", self.root):
            with release_memory.temporary_directory(
                self.root, "kimiflow-identity-"
            ) as directory:
                self.assertNotEqual(
                    os.path.commonpath((self.root, directory)),
                    self.root,
                )
                self.assertEqual(
                    stat.S_IMODE(os.stat(directory).st_mode), 0o700
                )
