import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import release_profile


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, mode)


def json_write(path, value):
    write(path, json.dumps(value, sort_keys=True, indent=2) + "\n")


class ReleaseProfileTests(unittest.TestCase):
    EXPLICIT_SCRIPTS = [
        "scripts/check.py",
        "scripts/effect.py",
        "scripts/fail.py",
        "scripts/final.py",
        "scripts/post.py",
        "scripts/pre.py",
    ]

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
        scripts = {
            "release-control.py": "RULE = 'release control evidence'\n",
            "check.py": (
                "from pathlib import Path\n"
                "p=Path('order.log'); p.write_text((p.read_text() if p.exists() else '')+'check\\n')\n"
            ),
            "pre.py": "raise SystemExit(0)\n",
            "effect.py": (
                "from pathlib import Path\n"
                "p=Path('counter.txt'); n=int(p.read_text()) if p.exists() else 0\n"
                "p.write_text(str(n+1))\n"
                "q=Path('order.log'); q.write_text((q.read_text() if q.exists() else '')+'effect\\n')\n"
            ),
            "post.py": (
                "from pathlib import Path\n"
                "raise SystemExit(0 if Path('counter.txt').exists() else 1)\n"
            ),
            "final.py": (
                "from pathlib import Path\n"
                "p=Path('order.log'); p.write_text((p.read_text() if p.exists() else '')+'final\\n')\n"
                "raise SystemExit(0 if Path('counter.txt').exists() else 1)\n"
            ),
            "fail.py": "raise SystemExit(7)\n",
        }
        for name, payload in scripts.items():
            write(os.path.join(self.root, "scripts", name), payload)
        json_write(
            os.path.join(self.root, "package.json"),
            {
                "name": "fixture",
                "version": "1.0.0",
                "scripts": {"release": "python3 scripts/effect.py"},
            },
        )
        write(
            os.path.join(self.root, ".github", "workflows", "release.yml"),
            "name: release\non: workflow_dispatch\n",
        )
        subprocess.run(["git", "-C", self.root, "add", "."], check=True)
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "fixture"], check=True
        )
        write(os.path.join(self.root, ".env"), "SECRET=do-not-discover\n")
        self.discovery = self._discover()
        self.candidate_path = os.path.join(
            self.root, ".kimiflow", "release", "CANDIDATE.json"
        )
        self.audit_path = os.path.join(
            self.root, ".kimiflow", "release", "AUDIT-CANDIDATE.json"
        )

    def _discover(self):
        return release_profile.discover(
            self.root, write=True, includes=self.EXPLICIT_SCRIPTS
        )

    def _profile(self, failing_check=False):
        check_script = "scripts/fail.py" if failing_check else "scripts/check.py"
        controls = [
            {
                "path": row["path"],
                "digest_mode": row["control_mode"],
                "sha256": row["control_sha256"],
            }
            for row in self.discovery["sources"]
            if row["role"] == "control_candidate"
        ]
        return {
            "schema_version": 1,
            "document_type": "release_profile",
            "id": "fixture-release",
            "control_sources": controls,
            "steps": [
                {
                    "id": "preflight",
                    "kind": "check",
                    "argv": ["python3", check_script],
                    "cwd": ".",
                    "timeout_seconds": 10,
                },
                {
                    "id": "publish",
                    "kind": "effect",
                    "scope": "local",
                    "argv": ["python3", "scripts/effect.py"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "precondition": {
                        "id": "publish-safe",
                        "argv": ["python3", "scripts/pre.py"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                    },
                    "postcondition": {
                        "id": "publish-done",
                        "argv": ["python3", "scripts/post.py"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                    },
                },
            ],
            "final_checks": [
                {
                    "id": "release-verified",
                    "argv": ["python3", "scripts/final.py"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                }
            ],
        }

    def _audit(self, profile, findings=None, failure_sha256=None):
        evidence = "scripts/release-control.py"
        return {
            "schema_version": 1,
            "document_type": "release_audit",
            "profile_sha256": release_profile._value_sha(profile),
            "discovery_sha256": release_profile._value_sha(self.discovery),
            "control_set_sha256": release_profile._control_set_digest(
                profile["control_sources"]
            ),
            "failure_sha256": failure_sha256,
            "verdict": "passed",
            "probe_attestations": [
                {
                    "probe_id": probe_id,
                    "read_only": True,
                    "evidence_path": evidence,
                }
                for probe_id in (
                    "preflight",
                    "publish-safe",
                    "publish-done",
                    "release-verified",
                )
            ],
            "findings": [] if findings is None else findings,
        }

    def _adopt(self, profile=None, audit=None):
        profile = profile or self._profile()
        audit = audit or self._audit(profile)
        json_write(self.candidate_path, profile)
        json_write(self.audit_path, audit)
        return release_profile.adopt(
            self.root,
            self.candidate_path,
            self.audit_path,
            write=True,
        )

    def _failure_sha256(self):
        path = os.path.join(
            self.root, ".kimiflow", "release", "FAILURE.json"
        )
        with open(path, encoding="utf-8") as handle:
            return release_profile._value_sha(json.load(handle))

    def test_discovery_is_tracked_bounded_inventory(self):
        paths = {row["path"]: row for row in self.discovery["sources"]}
        self.assertEqual(
            paths["scripts/release-control.py"]["role"], "control_candidate"
        )
        self.assertEqual(paths["scripts/effect.py"]["kind"], "explicit")
        self.assertEqual(paths["scripts/effect.py"]["control_mode"], "file")
        self.assertEqual(paths["package.json"]["role"], "control_candidate")
        self.assertEqual(
            paths["package.json"]["control_mode"], "package-scripts"
        )
        self.assertNotIn(".env", paths)
        serialized = json.dumps(self.discovery)
        self.assertNotIn("do-not-discover", serialized)
        self.assertLessEqual(
            len(self.discovery["sources"]),
            release_profile.MAX_DISCOVERY_FILES,
        )
        write(os.path.join(self.root, "scripts", "untracked.py"), "pass\n")
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "discovery_include_untracked",
        ):
            release_profile.discover(
                self.root, includes=["scripts/untracked.py"]
            )

    def test_adopt_requires_bound_current_audit(self):
        profile = self._profile()
        bad_audit = self._audit(profile)
        bad_audit["profile_sha256"] = "sha256:" + "0" * 64
        json_write(self.candidate_path, profile)
        json_write(self.audit_path, bad_audit)
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "audit_profile_mismatch"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=True,
            )
        self.assertFalse(
            os.path.exists(
                os.path.join(self.root, ".kimiflow", "release", "PROFILE.json")
            )
        )

        mutating = self._profile()
        mutating["final_checks"][0]["argv"] = ["git", "push"]
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "mutating_probe_forbidden"
        ):
            release_profile.validate_profile(mutating)
        git_alias = self._profile()
        git_alias["final_checks"][0]["argv"] = [
            "git",
            "-c",
            "alias.mark=!touch probe-mutated",
            "mark",
        ]
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "mutating_probe_forbidden"
        ):
            release_profile.validate_profile(git_alias)
        for argv, error in (
            (
                [
                    "python3",
                    "-c__import__('pathlib').Path('probe-mutated').touch()",
                ],
                "inline_code_forbidden",
            ),
            (
                [
                    "python3",
                    "-ic__import__('pathlib').Path('probe-mutated').touch()",
                ],
                "inline_code_forbidden",
            ),
            (
                ["bash", "-ec", ":>probe-mutated"],
                "shell_string_forbidden",
            ),
            (["git", "diff"], "mutating_probe_forbidden"),
            (["git", "diff", "--ext-diff"], "mutating_probe_forbidden"),
            (
                [
                    "env",
                    "GIT_EXTERNAL_DIFF=./scripts/effect.py",
                    "git",
                    "diff",
                    "--ext-diff",
                ],
                "command_loader_unsupported",
            ),
            (
                [
                    "env",
                    "GIT_CONFIG_GLOBAL=./gitconfig",
                    "git",
                    "status",
                    "--porcelain",
                ],
                "command_loader_unsupported",
            ),
            (
                [
                    "env",
                    "GIT_NO_LAZY_FETCH=0",
                    "git",
                    "status",
                    "--porcelain",
                ],
                "command_loader_unsupported",
            ),
            (
                [
                    "env",
                    "GIT_OPTIONAL_LOCKS=1",
                    "git",
                    "status",
                    "--porcelain",
                ],
                "command_loader_unsupported",
            ),
            (
                [
                    "env",
                    "GIT_DIR=../other/.git",
                    "git",
                    "status",
                    "--porcelain",
                ],
                "command_loader_unsupported",
            ),
            (
                [
                    "git",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--ext-dif",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "git",
                    "diff",
                    "--",
                    "--no-ext-diff",
                    "--no-textconv",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "git",
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--output=probe-mutated",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "env",
                    "GIT_SSH_COMMAND=./scripts/effect.py",
                    "git",
                    "ls-remote",
                    "ssh://example.invalid/repo",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "git",
                    "ls-remote",
                    "--upload-pack=./scripts/effect.py",
                    ".",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "git",
                    "ls-remote",
                    "--upload=./scripts/effect.py",
                    ".",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "env",
                    "GIT_ALLOW_PROTOCOL=ext",
                    "git",
                    "ls-remote",
                    "ext::./scripts/effect.py",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "env",
                    "GIT_TRACE2_EVENT=probe-mutated",
                    "git",
                    "status",
                    "--porcelain",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "ruby",
                    "-weFile.write('probe-mutated','x')",
                ],
                "inline_code_forbidden",
            ),
            (
                [
                    "perl",
                    "-wMEvil",
                ],
                "command_loader_unsupported",
            ),
            (
                [
                    "perl",
                    "-wE",
                    "open(F,qq(>probe-mutated));print F q(x)",
                ],
                "inline_code_forbidden",
            ),
            (
                [
                    "perl",
                    "-I.",
                    "-d:X",
                    "scripts/check.py",
                ],
                "command_loader_unsupported",
            ),
            (
                [
                    "git",
                    "cat-file",
                    "--filters",
                    "HEAD:scripts/effect.py",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "git",
                    "grep",
                    "--open-files-in-pager=./scripts/effect.py",
                    "release",
                ],
                "mutating_probe_forbidden",
            ),
            (
                ["env", "-i", "git", "status", "--porcelain"],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "env",
                    "CI=1",
                    "env",
                    "-i",
                    "git",
                    "status",
                    "--porcelain",
                ],
                "mutating_probe_forbidden",
            ),
            (
                [
                    "env",
                    "--unset=GIT_CONFIG_COUNT",
                    "git",
                    "status",
                    "--porcelain",
                ],
                "mutating_probe_forbidden",
            ),
        ):
            unsafe_probe = self._profile()
            unsafe_probe["final_checks"][0]["argv"] = argv
            with self.subTest(unsafe_probe=argv), self.assertRaisesRegex(
                release_profile.ReleaseProfileError, error
            ):
                release_profile.validate_profile(unsafe_probe)
        safe_git_probe = self._profile()
        safe_git_probe["final_checks"][0]["argv"] = [
            "git", "diff", "--no-ext-diff", "--no-textconv"
        ]
        self.assertEqual(
            release_profile.validate_profile(safe_git_probe),
            safe_git_probe,
        )
        for argv in (
            ["env", "bash", "-c", "echo unsafe"],
            ["git", "-C", ".", "push"],
            ["kubectl", "-n", "prod", "delete", "pod", "unsafe"],
            ["npm", "--workspace", "pkg", "publish"],
            ["docker", "--context", "prod", "push", "image"],
            ["cargo", "--color", "never", "publish"],
            ["env", "PATH=./scripts", "effect.py"],
            ["env", "-C", "scripts", "effect.py"],
        ):
            wrapped = self._profile()
            wrapped["final_checks"][0]["argv"] = argv
            with self.subTest(argv=argv), self.assertRaises(
                release_profile.ReleaseProfileError
            ):
                release_profile.validate_profile(wrapped)

        finding = {
            "id": "improve-release",
            "severity": "medium",
            "evidence_path": "missing.md",
            "claim": "One improvement exists.",
            "recommendation": "Change it in a separate feature flow.",
            "disposition": "deferred",
        }
        audit = self._audit(profile, findings=[finding])
        json_write(self.candidate_path, profile)
        json_write(self.audit_path, audit)
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "audit_finding_invalid"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=True,
            )

        unbound = self._profile()
        unbound["control_sources"] = [
            row
            for row in unbound["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        json_write(self.candidate_path, unbound)
        json_write(self.audit_path, self._audit(unbound))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "command_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=True,
            )

        cwd_bound = self._profile()
        cwd_bound["steps"][1]["argv"] = ["python3", "./effect.py"]
        cwd_bound["steps"][1]["cwd"] = "scripts"
        json_write(self.candidate_path, cwd_bound)
        json_write(self.audit_path, self._audit(cwd_bound))
        self.assertEqual(
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )["status"],
            "valid",
        )
        cwd_bound["control_sources"] = [
            row
            for row in cwd_bound["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        json_write(self.candidate_path, cwd_bound)
        json_write(self.audit_path, self._audit(cwd_bound))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "command_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        direct_cwd = self._profile()
        direct_cwd["steps"][1]["argv"] = ["./effect.py"]
        direct_cwd["steps"][1]["cwd"] = "scripts"
        direct_cwd["control_sources"] = [
            row
            for row in direct_cwd["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        json_write(self.candidate_path, direct_cwd)
        json_write(self.audit_path, self._audit(direct_cwd))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "command_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        path_direct = self._profile()
        path_direct["steps"][1]["argv"] = ["effect.py"]
        path_direct["steps"][1]["cwd"] = "."
        path_direct["control_sources"] = [
            row
            for row in path_direct["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        os.chmod(
            os.path.join(self.root, "scripts", "effect.py"), 0o755
        )
        json_write(self.candidate_path, path_direct)
        json_write(self.audit_path, self._audit(path_direct))
        with mock.patch.dict(
            os.environ,
            {
                "PATH": os.path.join(self.root, "scripts")
                + os.pathsep
                + os.environ.get("PATH", "")
            },
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "command_control_unbound",
            ):
                release_profile.adopt(
                    self.root,
                    self.candidate_path,
                    self.audit_path,
                    write=False,
                )
        path_direct["steps"][1]["cwd"] = "scripts"
        json_write(self.candidate_path, path_direct)
        json_write(self.audit_path, self._audit(path_direct))
        with mock.patch.dict(
            os.environ,
            {"PATH": "." + os.pathsep + os.environ.get("PATH", "")},
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "command_control_unbound",
            ):
                release_profile.adopt(
                    self.root,
                    self.candidate_path,
                    self.audit_path,
                    write=False,
                )

        absolute_direct = self._profile()
        absolute_direct["steps"][1]["argv"] = [
            os.path.join(self.root, "scripts", "effect.py")
        ]
        json_write(self.candidate_path, absolute_direct)
        json_write(self.audit_path, self._audit(absolute_direct))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "command_local_input_unsafe",
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        for argv in (
            ["python3", "-m", "scripts.effect"],
            ["python3", "-mscripts.effect"],
            ["python3", "-OOmscripts.effect"],
            ["node", "--require", "scripts/effect.py", "scripts/check.py"],
            ["node", "-rscripts/effect.py", "scripts/check.py"],
        ):
            with self.subTest(loader_argv=argv):
                loader = self._profile()
                loader["steps"][1]["argv"] = argv
                json_write(self.candidate_path, loader)
                json_write(self.audit_path, self._audit(loader))
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "command_loader_unsupported",
                ):
                    release_profile.adopt(
                        self.root,
                        self.candidate_path,
                        self.audit_path,
                        write=False,
                    )

        wrapped_interpreter = self._profile()
        wrapped_interpreter["steps"][1]["argv"] = [
            "uv", "run", "python3", "scripts/effect.py"
        ]
        json_write(self.candidate_path, wrapped_interpreter)
        json_write(
            self.audit_path, self._audit(wrapped_interpreter)
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "command_wrapper_unsupported",
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        versioned_interpreter = self._profile()
        versioned_interpreter["steps"][1]["argv"] = [
            "python3.14", "scripts/effect.py"
        ]
        versioned_interpreter["control_sources"] = [
            row
            for row in versioned_interpreter["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        json_write(self.candidate_path, versioned_interpreter)
        json_write(
            self.audit_path, self._audit(versioned_interpreter)
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "command_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        package_unbound = self._profile()
        package_unbound["steps"][1]["argv"] = ["npm", "run", "release"]
        package_unbound["control_sources"] = [
            row
            for row in package_unbound["control_sources"]
            if row["path"] != "package.json"
        ]
        json_write(self.candidate_path, package_unbound)
        json_write(self.audit_path, self._audit(package_unbound))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "package_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=True,
            )

        package_alias_unbound = self._profile()
        package_alias_unbound["steps"][1]["argv"] = [
            "npm", "run-script", "release"
        ]
        package_alias_unbound["control_sources"] = [
            row
            for row in package_alias_unbound["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        json_write(self.candidate_path, package_alias_unbound)
        json_write(
            self.audit_path, self._audit(package_alias_unbound)
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "command_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        for alias in ("r", "rum", "urn"):
            alias_unbound = self._profile()
            alias_unbound["steps"][1]["argv"] = [
                "npm", alias, "release"
            ]
            alias_unbound["control_sources"] = [
                row
                for row in alias_unbound["control_sources"]
                if row["path"] != "scripts/effect.py"
            ]
            json_write(self.candidate_path, alias_unbound)
            json_write(self.audit_path, self._audit(alias_unbound))
            with self.subTest(npm_alias=alias), self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "command_control_unbound",
            ):
                release_profile.adopt(
                    self.root,
                    self.candidate_path,
                    self.audit_path,
                    write=False,
                )

        unsupported_package = self._profile()
        unsupported_package["steps"][1]["argv"] = ["npm", "ci"]
        json_write(self.candidate_path, unsupported_package)
        json_write(
            self.audit_path, self._audit(unsupported_package)
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "package_command_unsupported",
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        implicit_package = self._profile()
        implicit_package["steps"][1]["argv"] = ["npm", "start"]
        json_write(self.candidate_path, implicit_package)
        json_write(self.audit_path, self._audit(implicit_package))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "package_command_unsupported",
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        workspace_environment = self._profile()
        workspace_environment["steps"][1]["argv"] = [
            "env",
            "npm_config_workspace=sub",
            "npm",
            "run",
            "release",
        ]
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "command_loader_unsupported",
        ):
            release_profile.validate_profile(workspace_environment)

        os.mkdir(os.path.join(self.root, "sub"))
        ancestor_unbound = self._profile()
        ancestor_unbound["steps"][1]["argv"] = [
            "npm", "run", "release"
        ]
        ancestor_unbound["steps"][1]["cwd"] = "sub"
        ancestor_unbound["control_sources"] = [
            row
            for row in ancestor_unbound["control_sources"]
            if row["path"] not in ("package.json", "scripts/effect.py")
        ]
        json_write(self.candidate_path, ancestor_unbound)
        json_write(self.audit_path, self._audit(ancestor_unbound))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "package_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        json_write(
            os.path.join(self.root, "package.json"),
            {
                "name": "fixture",
                "version": "1.0.0",
                "scripts": {
                    "release": "npm run build",
                    "build": "python3 scripts/effect.py",
                },
            },
        )
        self.discovery = self._discover()
        nested_unbound = self._profile()
        nested_unbound["steps"][1]["argv"] = [
            "npm", "run", "release"
        ]
        nested_unbound["control_sources"] = [
            row
            for row in nested_unbound["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        json_write(self.candidate_path, nested_unbound)
        json_write(self.audit_path, self._audit(nested_unbound))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "command_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        package_script_unbound = self._profile()
        package_script_unbound["steps"][1]["argv"] = [
            "npm", "run", "release"
        ]
        package_script_unbound["control_sources"] = [
            row
            for row in package_script_unbound["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        json_write(self.candidate_path, package_script_unbound)
        json_write(
            self.audit_path, self._audit(package_script_unbound)
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "command_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=True,
            )

        for script in (
            'python3 "$PWD/scripts/effect.py"',
            "python3 scripts/effect*.py",
            "PATH=./scripts effect.py",
            "NODE_OPTIONS=--require=./scripts/effect.py node scripts/check.py",
            "cd scripts && python3 effect.py",
            "npm --workspace=packages/a run release",
        ):
            with self.subTest(package_script=script):
                json_write(
                    os.path.join(self.root, "package.json"),
                    {
                        "name": "fixture",
                        "version": "1.0.0",
                        "scripts": {"release": script},
                    },
                )
                self.discovery = self._discover()
                dynamic_package = self._profile()
                dynamic_package["steps"][1]["argv"] = [
                    "npm", "run", "release"
                ]
                json_write(self.candidate_path, dynamic_package)
                json_write(
                    self.audit_path, self._audit(dynamic_package)
                )
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "package_script_dynamic_input",
                ):
                    release_profile.adopt(
                        self.root,
                        self.candidate_path,
                        self.audit_path,
                        write=False,
                    )

        json_write(
            os.path.join(self.root, "package.json"),
            {"name": "fixture", "version": "1.0.0", "scripts": {}},
        )
        self.discovery = self._discover()
        empty_scripts = {
            row["path"]: row for row in self.discovery["sources"]
        }["package.json"]
        self.assertEqual(empty_scripts["role"], "control_candidate")
        self.assertEqual(empty_scripts["control_mode"], "package-scripts")
        empty_package = self._profile()
        empty_package["steps"][1]["argv"] = ["npm", "publish"]
        empty_package["control_sources"] = [
            row
            for row in empty_package["control_sources"]
            if row["path"] != "package.json"
        ]
        json_write(self.candidate_path, empty_package)
        json_write(self.audit_path, self._audit(empty_package))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "package_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        os.symlink(".", os.path.join(self.root, "release-link"))
        subprocess.run(
            ["git", "-C", self.root, "add", "release-link"], check=True
        )
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "symlink cwd"],
            check=True,
        )
        symlink_package = self._profile()
        symlink_package["steps"][1]["argv"] = ["npm", "publish"]
        symlink_package["steps"][1]["cwd"] = "release-link"
        symlink_package["control_sources"] = [
            row
            for row in symlink_package["control_sources"]
            if row["path"] != "package.json"
        ]
        json_write(self.candidate_path, symlink_package)
        json_write(self.audit_path, self._audit(symlink_package))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "package_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

        for argv in (
            ["npm", "--prefix", "sub", "run", "release"],
            ["npm", "--workspace=packages/a", "run", "release"],
            ["npm", "-w", "packages/a", "run", "release"],
            ["yarn", "workspace", "a", "release"],
            ["pnpm", "--filter", "a", "run", "release"],
        ):
            with self.subTest(package_argv=argv):
                dynamic_context = self._profile()
                dynamic_context["steps"][1]["argv"] = argv
                json_write(self.candidate_path, dynamic_context)
                json_write(
                    self.audit_path, self._audit(dynamic_context)
                )
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "package_command_dynamic_context",
                ):
                    release_profile.adopt(
                        self.root,
                        self.candidate_path,
                        self.audit_path,
                        write=False,
                    )

        json_write(
            os.path.join(self.root, "package.json"),
            {
                "name": "fixture",
                "version": "1.0.0",
                "scripts": {
                    "release": "python3 scripts/effect.py; echo done"
                },
            },
        )
        self.discovery = self._discover()
        punctuated_package = self._profile()
        punctuated_package["steps"][1]["argv"] = [
            "npm", "run", "release"
        ]
        punctuated_package["control_sources"] = [
            row
            for row in punctuated_package["control_sources"]
            if row["path"] != "scripts/effect.py"
        ]
        json_write(self.candidate_path, punctuated_package)
        json_write(self.audit_path, self._audit(punctuated_package))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "command_control_unbound"
        ):
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=False,
            )

    def test_status_marks_drift_or_failure_audit_required(self):
        self._adopt()
        self.assertEqual(release_profile.status(self.root)["status"], "ready")
        json_write(
            os.path.join(self.root, "package.json"),
            {
                "name": "fixture",
                "version": "1.0.1",
                "scripts": {"release": "python3 scripts/effect.py"},
            },
        )
        self.assertEqual(release_profile.status(self.root)["status"], "ready")

        json_write(
            os.path.join(self.root, "package.json"),
            {
                "name": "fixture",
                "version": "1.0.1",
                "scripts": {"release": "python3 scripts/fail.py"},
            },
        )
        package_drift = release_profile.status(self.root)
        self.assertEqual(package_drift["status"], "audit_required")
        self.assertEqual(package_drift["reason"], "control_drift")
        json_write(
            os.path.join(self.root, "package.json"),
            {
                "name": "fixture",
                "version": "1.0.1",
                "scripts": {"release": "python3 scripts/effect.py"},
            },
        )

        control = os.path.join(self.root, "scripts", "release-control.py")
        with open(control, encoding="utf-8") as handle:
            original = handle.read()
        write(control, original + "CHANGED = True\n")
        drifted = release_profile.status(self.root)
        self.assertEqual(drifted["status"], "audit_required")
        self.assertEqual(drifted["reason"], "control_drift")

        write(control, original)
        self.discovery = self._discover()
        failing = self._profile(failing_check=True)
        old_audit = self._audit(failing)
        self._adopt(failing, old_audit)
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "step_failed"
        ):
            release_profile.run_profile(
                self.root, authorize=True, write=True
            )
        failed = release_profile.status(self.root)
        self.assertEqual(failed["status"], "audit_required")
        self.assertEqual(failed["reason"], "release_failure")
        self.assertEqual(failed["failure_sha256"], self._failure_sha256())
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "audit_failure_mismatch"
        ):
            self._adopt(failing, old_audit)
        self._adopt(
            failing,
            self._audit(
                failing, failure_sha256=self._failure_sha256()
            ),
        )
        self.assertEqual(release_profile.status(self.root)["status"], "ready")
        first_failure_audit = self._audit(
            failing, failure_sha256=failed["failure_sha256"]
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "step_failed"
        ):
            release_profile.run_profile(
                self.root, authorize=True, write=True
            )
        self.assertNotEqual(
            release_profile.status(self.root)["failure_sha256"],
            failed["failure_sha256"],
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "audit_failure_mismatch"
        ):
            self._adopt(failing, first_failure_audit)

    def test_run_is_authorized_locked_and_serial(self):
        env_probe = os.path.join(self.root, "scripts", "env-probe.py")
        write(
            env_probe,
            "import os\n"
            "unsafe=('NODE_OPTIONS' in os.environ or "
            "any(k.startswith('BASH_FUNC_') for k in os.environ))\n"
            "raise SystemExit(9 if unsafe else 0)\n",
        )
        with mock.patch.dict(
            os.environ,
            {
                "NODE_OPTIONS": "--require=./scripts/effect.py",
                "BASH_FUNC_echo%%": "() { touch probe-mutated; }",
            },
        ):
            self.assertEqual(
                release_profile._execute(
                    self.root,
                    {
                        "id": "env-probe",
                        "argv": ["python3", "scripts/env-probe.py"],
                        "cwd": ".",
                        "timeout_seconds": 10,
                    },
                )["exit_code"],
                0,
            )
        probe_environment = os.path.join(
            self.root, "scripts", "probe-environment.py"
        )
        write(
            probe_environment,
            "import os\n"
            "safe=(os.environ.get('GIT_NO_LAZY_FETCH') == '1' and "
            "os.environ.get('GIT_OPTIONAL_LOCKS') == '0')\n"
            "raise SystemExit(0 if safe else 9)\n",
        )
        self.assertEqual(
            release_profile._execute(
                self.root,
                {
                    "id": "probe-environment",
                    "argv": ["python3", "scripts/probe-environment.py"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                },
                probe=True,
            )["exit_code"],
            0,
        )
        self._adopt()
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "authorization_required"
        ):
            release_profile.run_profile(
                self.root, authorize=False, write=True
            )
        with release_profile._release_lock(self.root):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "release_locked"
            ):
                release_profile.run_profile(
                    self.root, authorize=True, write=True
                )
        receipt = release_profile.run_profile(
            self.root, authorize=True, write=True
        )
        self.assertEqual(receipt["status"], "completed")
        with open(os.path.join(self.root, "order.log"), encoding="utf-8") as handle:
            self.assertEqual(handle.read().splitlines(), ["check", "effect", "final"])

        replacement = self._profile()
        replacement["id"] = "control-race"
        self._adopt(replacement, self._audit(replacement))
        original_execute = release_profile._execute

        def drift_after_check(root, command, probe=False):
            evidence = original_execute(root, command, probe=probe)
            if command["id"] == "preflight":
                control = os.path.join(root, "scripts", "release-control.py")
                write(control, "RULE = 'changed during release'\n")
            return evidence

        with mock.patch.object(
            release_profile, "_execute", side_effect=drift_after_check
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "control_drift"
            ):
                release_profile.run_profile(
                    self.root, authorize=True, write=True
                )
        with open(os.path.join(self.root, "counter.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "1")

    def test_recovery_never_replays_uncertain_mutating_effect(self):
        self._adopt()
        class TestCrash(RuntimeError):
            pass

        real_persist = release_profile._persist_run
        crash_armed = [True]

        def interrupt_after_receipt(root, receipt):
            real_persist(root, receipt)
            publish = receipt["steps"][1]
            if (
                crash_armed[0]
                and publish["status"] == "started"
                and (publish.get("evidence") or {}).get("effect") is not None
            ):
                crash_armed[0] = False
                raise TestCrash("after-effect-receipt")

        with mock.patch.object(
            release_profile,
            "_persist_run",
            side_effect=interrupt_after_receipt,
        ):
            with self.assertRaises(TestCrash):
                release_profile.run_profile(
                    self.root, authorize=True, write=True
                )
        counter = os.path.join(self.root, "counter.txt")
        with open(counter, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "1")
        receipt = release_profile.run_profile(
            self.root, authorize=True, write=True
        )
        self.assertEqual(receipt["status"], "completed")
        with open(counter, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "1")

        real_execute = release_profile._execute

        def interrupt_before_receipt(root, command, probe=False):
            evidence = real_execute(root, command, probe=probe)
            if command["id"] == "publish":
                raise TestCrash(command["id"])
            return evidence

        with mock.patch.object(
            release_profile, "_execute", side_effect=interrupt_before_receipt
        ):
            with self.assertRaises(TestCrash):
                release_profile.run_profile(
                    self.root, authorize=True, write=True, new=True
                )
        self.assertEqual(release_profile.status(self.root)["status"], "ready")
        resumed = release_profile.run_profile(
            self.root, authorize=True, write=True
        )
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(
            resumed["steps"][1]["evidence"]["effect"]["receipt_status"],
            "unavailable_after_interruption",
        )
        with open(counter, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "2")

        write(
            os.path.join(self.root, "scripts", "effect.py"),
            (
                "from pathlib import Path\n"
                "p=Path('counter.txt'); n=int(p.read_text()) if p.exists() else 0\n"
                "p.write_text(str(n+1))\n"
                "raise SystemExit(7)\n"
            ),
        )
        self.discovery = self._discover()
        failing_effect = self._profile()
        stale_audit = self._audit(failing_effect)
        self._adopt(failing_effect, stale_audit)
        failure_crash_armed = [True]

        def interrupt_effect_failure(root, receipt):
            real_persist(root, receipt)
            publish = receipt["steps"][1]
            if (
                failure_crash_armed[0]
                and receipt["status"] == "audit_required"
                and publish["status"] == "effect_failed"
            ):
                failure_crash_armed[0] = False
                raise TestCrash("effect-failure-persisted")

        with mock.patch.object(
            release_profile,
            "_persist_run",
            side_effect=interrupt_effect_failure,
        ):
            with self.assertRaises(TestCrash):
                release_profile.run_profile(
                    self.root, authorize=True, write=True
                )
        self.assertEqual(
            release_profile.status(self.root)["status"], "audit_required"
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "audit_failure_mismatch"
        ):
            self._adopt(failing_effect, stale_audit)
        self._adopt(
            failing_effect,
            self._audit(
                failing_effect, failure_sha256=self._failure_sha256()
            ),
        )
        completed = release_profile.run_profile(
            self.root, authorize=True, write=True
        )
        self.assertEqual(completed["status"], "completed")
        with open(counter, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "3")

    def test_success_resumes_and_verifies_receipts(self):
        profile = self._profile()
        self._adopt(profile)
        first = release_profile.run_profile(
            self.root, authorize=True, write=True
        )
        second = release_profile.run_profile(
            self.root, authorize=True, write=True
        )
        self.assertEqual(first, second)
        self.assertRegex(
            first["completion_sha256"], r"^sha256:[0-9a-f]{64}$"
        )
        self.assertTrue(
            all(row["status"] == "completed" for row in first["steps"])
        )
        self.assertTrue(
            all(
                row["status"] == "completed"
                for row in first["final_checks"]
            )
        )
        with open(os.path.join(self.root, "counter.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "1")

        run_path = os.path.join(
            self.root, ".kimiflow", "release", "RUN.json"
        )
        tampered = dict(first)
        tampered["generation"] = 99
        json_write(run_path, tampered)
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "existing_run_invalid"
        ):
            self._adopt(profile, self._audit(profile))

        json_write(run_path, first)
        replacement = self._profile()
        replacement["id"] = "fixture-release-v2"
        self._adopt(replacement, self._audit(replacement))
        current = release_profile.status(self.root)
        self.assertEqual(current["status"], "ready")
        self.assertEqual(current["run_status"], "none")

        failing = self._profile(failing_check=True)
        self._adopt(failing, self._audit(failing))
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "step_failed"
        ):
            release_profile.run_profile(
                self.root, authorize=True, write=True, new=True
            )
        failure_sha256 = self._failure_sha256()
        recovered = self._profile()
        recovered["id"] = "fixture-release-v3"
        recovered_audit = self._audit(
            recovered, failure_sha256=failure_sha256
        )
        json_write(self.candidate_path, recovered)
        json_write(self.audit_path, recovered_audit)
        real_unlink = os.unlink
        crash_armed = [True]

        def crash_after_failure_unlink(path):
            real_unlink(path)
            if crash_armed[0] and path.endswith("FAILURE.json"):
                crash_armed[0] = False
                raise RuntimeError("crash-after-failure-unlink")

        with mock.patch.object(
            release_profile.os, "unlink", side_effect=crash_after_failure_unlink
        ):
            with self.assertRaisesRegex(
                RuntimeError, "crash-after-failure-unlink"
            ):
                release_profile.adopt(
                    self.root,
                    self.candidate_path,
                    self.audit_path,
                    write=True,
                )
        self.assertEqual(
            release_profile.adopt(
                self.root,
                self.candidate_path,
                self.audit_path,
                write=True,
            )["status"],
            "adopted",
        )


if __name__ == "__main__":
    unittest.main()
