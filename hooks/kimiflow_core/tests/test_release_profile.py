import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

from kimiflow_core import release_memory, release_profile


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
            "fail.py": "print('unauthorized'); raise SystemExit(7)\n",
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
        write(os.path.join(self.root, "artifact.txt"), "stable\n")
        write(os.path.join(self.root, "artifact2.txt"), "stable\n")
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

    def _policy(
        self,
        *,
        auth="none",
        stage="project_checks",
        failure="semantic",
        reuse="never",
        affected_paths=None,
        declared_env=None,
    ):
        return {
            "auth": auth,
            "stage": stage,
            "failure": failure,
            "reuse": reuse,
            "affected_paths": list(affected_paths or []),
            "declared_env": list(declared_env or []),
        }

    def _profile_v2(self, post_script="scripts/post.py"):
        profile = self._profile()
        profile["schema_version"] = 2
        profile["inputs"] = [
            {
                "name": "repository",
                "type": "repository",
                "publication_target": True,
            },
            {
                "name": "tag",
                "type": "tag",
                "publication_target": False,
            },
        ]
        profile["identity"] = {
            "provider": "environment",
            "environment": ["DEPLOY_TOKEN"],
        }
        profile["steps"][0]["policy"] = self._policy(
            reuse="kimiflow_verification",
            affected_paths=["artifact.txt"],
        )
        profile["steps"].insert(
            1,
            {
                "id": "preflight-independent",
                "kind": "check",
                "argv": ["python3", "scripts/check.py"],
                "cwd": ".",
                "timeout_seconds": 10,
                "policy": self._policy(
                    reuse="kimiflow_verification",
                    affected_paths=["artifact2.txt"],
                ),
            },
        )
        effect = profile["steps"][2]
        effect["argv"].extend(["{{tag}}", "{{repository}}"])
        effect["policy"] = self._policy(
            auth="provider",
            stage="provider",
            failure="operational",
        )
        effect["precondition"]["policy"] = self._policy(
            auth="provider",
            stage="provider",
            failure="operational",
        )
        effect["postcondition"]["argv"] = ["python3", post_script]
        effect["postcondition"]["policy"] = self._policy(
            auth="provider",
            stage="provider",
            failure="operational",
        )
        profile["final_checks"][0]["policy"] = self._policy()
        return profile

    def _v2_inputs(self):
        return {"repository": "org/project", "tag": "v1.2.3"}

    def _verification_run(self):
        run = os.path.join(self.root, ".kimiflow", "fixture-run")
        os.makedirs(run, exist_ok=True)
        write(os.path.join(run, "STATE.md"), "Phase 6: done\n")
        write(
            os.path.join(run, "VERIFICATION.md"),
            "<!-- kimiflow:verification outcome=passed criteria=passed "
            "regression=passed -->\n"
            "<!-- kimiflow:conformance contract=1 status=converged "
            "diff=passed strategy=passed -->\n",
        )
        return run

    def _audit(self, profile, findings=None, failure_sha256=None):
        evidence = "scripts/release-control.py"
        probe_ids = []
        for step in profile["steps"]:
            if step["kind"] == "check":
                probe_ids.append(step["id"])
            else:
                probe_ids.extend(
                    [step["precondition"]["id"], step["postcondition"]["id"]]
                )
        probe_ids.extend(check["id"] for check in profile["final_checks"])
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
                for probe_id in probe_ids
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

    def test_v2_inputs_expand_and_v1_remains_compatible(self):
        v1 = self._profile()
        self.assertEqual(release_profile.validate_profile(v1), v1)
        self._adopt(v1)
        migration = release_profile.status(self.root, prefer_v2=True)
        self.assertEqual(migration["status"], "upgrade_required")
        self.assertEqual(migration["profile_schema_version"], 1)
        self.assertEqual(migration["migration_status"], "v2_available")
        self.assertTrue(
            release_profile._parser().parse_args(
                ["status", "--prefer-v2"]
            ).prefer_v2
        )
        self.assertEqual(
            release_profile.run_profile(
                self.root, authorize=True, write=True
            )["status"],
            "completed",
        )

        v2 = self._profile_v2()
        self.assertEqual(release_profile.validate_profile(v2), v2)
        self.assertTrue(
            release_profile._validate_input_value(
                "git_oid",
                "Ab" * 20,
            )
        )
        self.assertTrue(
            release_profile._validate_input_value(
                "repository", "group/subgroup/project"
            )
        )
        unsafe_public = json.loads(json.dumps(v2))
        unsafe_public["steps"][0]["policy"]["declared_env"] = ["HOME"]
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "declared_env_invalid",
        ):
            release_profile.validate_profile(unsafe_public)
        unsafe_identity = json.loads(json.dumps(v2))
        unsafe_identity["identity"]["environment"] = ["LD_PRELOAD"]
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "profile_identity_invalid",
        ):
            release_profile.validate_profile(unsafe_identity)
        github_profile = json.loads(json.dumps(v2))
        github_profile["identity"] = {"provider": "github"}
        github_profile["inputs"][0]["publication_target"] = True
        self.assertEqual(
            release_profile.validate_profile(github_profile),
            github_profile,
        )
        github_profile["inputs"].append(
            {
                "name": "other_repository",
                "type": "repository",
                "publication_target": False,
            }
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "profile_identity_target_invalid",
        ):
            release_profile.validate_profile(github_profile)
        schema_path = os.path.realpath(
            os.path.join(
                os.path.dirname(release_profile.__file__),
                "..",
                "..",
                "references",
                "release-profile-v2.schema.json",
            )
        )
        with open(schema_path, encoding="utf-8") as handle:
            schema = json.load(handle)
        safe_pattern = schema["$defs"]["safeEnvironmentName"]["pattern"]
        self.assertIsNotNone(re.fullmatch(safe_pattern, "PUBLIC_BUILD"))
        for unsafe in (
            "HOME", "PATH", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
            "JAVA_TOOL_OPTIONS", "CI", "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR", "XDG_STATE_HOME",
        ):
            self.assertIsNone(re.fullmatch(safe_pattern, unsafe))
        github_condition = schema["$defs"]["profile"]["allOf"][0]["then"]
        input_contracts = github_condition["properties"]["inputs"]["allOf"]
        self.assertEqual(input_contracts[0]["minContains"], 1)
        self.assertEqual(input_contracts[0]["maxContains"], 1)
        self.assertEqual(
            schema["$defs"]["profile"]["properties"]["inputs"][
                "minContains"
            ],
            1,
        )
        check_schema = schema["$defs"]["checkStep"]
        self.assertEqual(check_schema["properties"]["kind"]["const"], "check")
        self.assertFalse(check_schema["additionalProperties"])
        resolved, input_sha256, resolved_sha256 = (
            release_profile._resolved_v2_profile(
                self.root, v2, self._v2_inputs()
            )
        )
        self.assertEqual(
            resolved["steps"][2]["argv"][-2:],
            ["v1.2.3", "org/project"],
        )
        serialized = json.dumps(
            {
                "input_sha256": input_sha256,
                "resolved_profile_sha256": resolved_sha256,
            }
        )
        self.assertNotIn("org/project", serialized)
        self.assertNotIn("v1.2.3", serialized)
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "input_value_invalid"
        ):
            release_profile._resolved_v2_profile(
                self.root,
                v2,
                {
                    "repository": "org/project",
                    "tag": "AbCdEf0123456789AbCdEf0123456789",
                },
            )
        self._adopt(v2)
        current = release_profile.status(self.root, prefer_v2=True)
        self.assertEqual(current["status"], "ready")
        self.assertEqual(current["profile_schema_version"], 2)
        self.assertEqual(current["migration_status"], "current")
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            first = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=self._v2_inputs(),
            )
            changed_inputs = dict(self._v2_inputs(), tag="v1.2.4")
            second = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                new=True,
                inputs=changed_inputs,
            )
        self.assertEqual(first["generation"], 1)
        self.assertEqual(second["generation"], 2)
        self.assertNotIn("v1.2.4", json.dumps(second))

        with tempfile.TemporaryDirectory() as tools:
            benign = os.path.join(tools, "benign")
            replaced = os.path.join(tools, "replaced")
            os.makedirs(benign)
            os.makedirs(replaced)
            write(
                os.path.join(benign, "provider-tool"),
                "#!/bin/sh\nexit 0\n",
                mode=0o755,
            )
            write(
                os.path.join(replaced, "provider-tool"),
                "#!/bin/sh\nprintf invoked > path-shim-invoked\n",
                mode=0o755,
            )
            original_path = os.environ.get("PATH", os.defpath)
            tool_profile = self._profile_v2()
            tool_profile["steps"][2]["argv"] = [
                "provider-tool", "{{repository}}"
            ]
            with mock.patch.dict(
                os.environ,
                {"PATH": benign + os.pathsep + original_path},
                clear=False,
            ):
                self._adopt(tool_profile)
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": replaced + os.pathsep + original_path,
                    "DEPLOY_TOKEN": "runtime-only",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "adopted_tool_drift",
                ):
                    release_profile.run_profile(
                        self.root,
                        authorize=True,
                        write=True,
                        inputs=self._v2_inputs(),
                    )
            self.assertFalse(
                os.path.exists(
                    os.path.join(self.root, "path-shim-invoked")
                )
            )

    def test_v2_runtime_boundary_rejects_drift_forgery_and_unsafe_inputs(self):
        profile = self._profile_v2()
        no_target = json.loads(json.dumps(profile))
        for row in no_target["inputs"]:
            row["publication_target"] = False
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "profile_publication_target_missing",
        ):
            release_profile.validate_profile(no_target)

        with tempfile.TemporaryDirectory() as outside:
            outside_file = os.path.join(outside, "sentinel.txt")
            write(outside_file, "outside\n")
            os.symlink(outside_file, os.path.join(self.root, "artifact-link"))
            path_profile = json.loads(json.dumps(profile))
            path_profile["inputs"].append(
                {
                    "name": "artifact_path",
                    "type": "relative_path",
                    "publication_target": False,
                }
            )
            path_profile["steps"][2]["argv"].append("{{artifact_path}}")
            unsafe_inputs = dict(
                self._v2_inputs(), artifact_path="artifact-link"
            )
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "input_value_invalid"
            ):
                release_profile._resolved_v2_profile(
                    self.root, path_profile, unsafe_inputs
                )
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "input_value_invalid"
            ):
                release_profile._resolved_v2_profile(
                    self.root,
                    path_profile,
                    dict(self._v2_inputs(), artifact_path=".npmrc"),
                )

        late_profile = json.loads(json.dumps(profile))
        late_profile["inputs"].append(
            {
                "name": "artifact_path",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        late_profile["steps"][2]["argv"].append("{{artifact_path}}")
        late_inputs = dict(
            self._v2_inputs(), artifact_path="late-artifact.bin"
        )
        late_resolved, _, _ = release_profile._resolved_v2_profile(
            self.root, late_profile, late_inputs, self.discovery
        )
        self.assertEqual(
            late_resolved["steps"][2]["argv"][-1],
            os.path.join(os.path.realpath(self.root), "late-artifact.bin"),
        )
        with tempfile.NamedTemporaryFile() as outside:
            os.symlink(
                outside.name,
                os.path.join(self.root, "late-artifact.bin"),
            )
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "input_path_drift"
            ):
                release_profile._require_relative_inputs_current(
                    self.root, late_profile, late_inputs
                )
            os.unlink(os.path.join(self.root, "late-artifact.bin"))

        executable = os.path.join(
            self.root, "scripts", "untracked-tool"
        )
        write(executable, "#!/bin/sh\nexit 0\n", mode=0o755)
        command_profile = json.loads(json.dumps(profile))
        command_profile["inputs"].append(
            {
                "name": "tool",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        command_profile["steps"][0]["argv"] = ["{{tool}}"]
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "command_local_input_untracked",
        ):
            release_profile._resolved_v2_profile(
                self.root,
                command_profile,
                dict(self._v2_inputs(), tool="scripts/untracked-tool"),
                self.discovery,
            )
        environment = release_memory.sealed_environment(os.environ)
        self.assertEqual(
            release_memory.tool_fingerprints(
                ["./untracked-tool"],
                environment,
                cwd=os.path.join(self.root, "scripts"),
            ),
            release_memory.tool_fingerprints(
                [executable],
                environment,
                cwd=self.root,
            ),
        )

        self._adopt(profile)
        bundle = release_profile._load_bundle(self.root)
        resolved, input_sha256, resolved_sha256 = (
            release_profile._resolved_v2_profile(
                self.root,
                profile,
                self._v2_inputs(),
                bundle["discovery"],
            )
        )
        bound = release_memory.binding(
            self.root,
            self._v2_inputs(),
            profile["inputs"],
            provider="environment",
        )
        receipt = release_profile._v2_empty_run(
            self.root, bundle, resolved_sha256, input_sha256, 1, bound
        )
        invalid_check = json.loads(json.dumps(receipt))
        invalid_check["steps"][0]["status"] = "started"
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "run_step_evidence_invalid",
        ):
            release_profile._validate_run_v2(
                invalid_check, bundle, resolved_profile=resolved
            )
        invalid_started = json.loads(json.dumps(receipt))
        invalid_effect = next(
            row for row in invalid_started["steps"]
            if row["kind"] == "effect"
        )
        invalid_effect["status"] = "started"
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "run_step_evidence_invalid",
        ):
            release_profile._validate_run_v2(
                invalid_started, bundle, resolved_profile=resolved
            )
        successful_precondition = release_profile._execute_v2(
            self.root,
            resolved["steps"][2]["precondition"],
            credentials={"DEPLOY_TOKEN": "runtime-only"},
        )
        invalid_retryable = json.loads(json.dumps(receipt))
        invalid_effect = next(
            row for row in invalid_retryable["steps"]
            if row["kind"] == "effect"
        )
        invalid_effect["status"] = "retryable"
        invalid_effect["evidence"] = {
            "precondition": successful_precondition
        }
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "run_step_evidence_invalid",
        ):
            release_profile._validate_run_v2(
                invalid_retryable, bundle, resolved_profile=resolved
            )
        forged = json.loads(json.dumps(receipt))
        forged["identity"] = {
            "kind": "environment",
            "account_sha256": release_memory.digest(
                {"names": ["DEPLOY_TOKEN"]}
            ),
            "resolver_probes": 0,
        }
        for row in forged["steps"] + forged["final_checks"]:
            row["status"] = "completed"
        forged["status"] = "completed"
        forged["completion_sha256"] = release_profile._value_sha(
            {
                key: forged[key]
                for key in (
                    "profile_sha256", "resolved_profile_sha256",
                    "input_sha256", "control_set_sha256", "binding", "head",
                    "generation", "identity", "steps", "final_checks",
                )
            }
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "run_completed_evidence_invalid",
        ):
            release_profile._validate_run_v2(
                forged, bundle, resolved_profile=resolved
            )

        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            recovered = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=self._v2_inputs(),
            )
        publish = next(
            row for row in recovered["steps"] if row["kind"] == "effect"
        )
        del publish["evidence"]["effect"]
        recovered["completion_sha256"] = release_profile._value_sha(
            {
                key: recovered[key]
                for key in (
                    "profile_sha256", "resolved_profile_sha256",
                    "input_sha256", "control_set_sha256", "binding", "head",
                    "generation", "identity", "steps", "final_checks",
                )
            }
        )
        release_profile._validate_run_v2(
            recovered, bundle, resolved_profile=resolved
        )

        with mock.patch.object(
            release_profile.os, "fsync", wraps=os.fsync
        ) as fsync:
            release_profile._persist_run(self.root, receipt)
        self.assertGreaterEqual(fsync.call_count, 2)
        write(os.path.join(self.root, "head-drift.txt"), "drift\n")
        subprocess.run(
            ["git", "-C", self.root, "add", "head-drift.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "head drift"],
            check=True,
        )
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "run_head_drift"
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=self._v2_inputs(),
                )

    def test_v2_timeout_terminates_provider_process_group(self):
        marker = os.path.join(self.root, "late-provider-effect.txt")
        child = (
            "import pathlib,time;"
            "time.sleep(1.5);"
            f"pathlib.Path({marker!r}).write_text('late')"
        )
        script = os.path.join(self.root, "scripts", "spawn-child.py")
        write(
            script,
            "import subprocess,time\n"
            f"subprocess.Popen(['python3','-c',{child!r}])\n"
            "time.sleep(10)\n",
        )
        command = {
            "id": "timeout-provider",
            "kind": "check",
            "argv": ["python3", "scripts/spawn-child.py"],
            "cwd": ".",
            "timeout_seconds": 1,
            "policy": self._policy(
                auth="provider",
                stage="provider",
                failure="operational",
            ),
        }
        evidence = release_profile._execute_v2(
            self.root, command, credentials={"DEPLOY_TOKEN": "runtime-only"}
        )
        self.assertEqual(evidence["failure_class"], "timeout")
        time.sleep(1)
        self.assertFalse(os.path.exists(marker))

    def test_v2_success_terminates_lingering_provider_process_group(self):
        marker = os.path.join(self.root, "lingering-provider-secret.txt")
        child = (
            "import os,pathlib,time;"
            "time.sleep(0.5);"
            f"pathlib.Path({marker!r}).write_text("
            "os.environ['DEPLOY_TOKEN'])"
        )
        script = os.path.join(
            self.root, "scripts", "spawn-lingering-child.py"
        )
        write(
            script,
            "import subprocess\n"
            f"subprocess.Popen(['python3','-c',{child!r}],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
            "stderr=subprocess.DEVNULL)\n",
        )
        command = {
            "id": "successful-provider",
            "kind": "effect",
            "argv": ["python3", "scripts/spawn-lingering-child.py"],
            "cwd": ".",
            "timeout_seconds": 5,
            "policy": self._policy(
                auth="provider",
                stage="provider",
                failure="operational",
            ),
        }
        evidence = release_profile._execute_v2(
            self.root, command, credentials={"DEPLOY_TOKEN": "runtime-only"}
        )
        self.assertEqual(evidence["exit_code"], 0)
        time.sleep(1)
        self.assertFalse(os.path.exists(marker))

    def test_v2_controller_signal_terminates_provider_process_group(self):
        child_pid = os.path.join(self.root, "provider-child.pid")
        long_running = os.path.join(
            self.root, "scripts", "long-provider.py"
        )
        write(
            long_running,
            "import os,time\n"
            f"open({child_pid!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(30)\n",
        )
        controller = os.path.join(self.root, "controller.py")
        write(
            controller,
            "from kimiflow_core import release_profile\n"
            f"root={self.root!r}\n"
            "command={"
            "'id':'provider','kind':'check',"
            "'argv':['python3','scripts/long-provider.py'],"
            "'cwd':'.','timeout_seconds':30,"
            "'policy':{'auth':'provider','stage':'provider',"
            "'failure':'operational','reuse':'never',"
            "'affected_paths':[],'declared_env':[]}}\n"
            "release_profile._execute_v2("
            "root,command,credentials={'DEPLOY_TOKEN':'runtime-only'})\n",
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.path.dirname(
            os.path.dirname(release_profile.__file__)
        )
        process = subprocess.Popen(
            ["python3", controller],
            cwd=self.root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 5
        while not os.path.exists(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(child_pid))
        with open(child_pid, encoding="utf-8") as handle:
            pid = int(handle.read())
        process.terminate()
        process.wait(timeout=5)
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        else:
            os.killpg(pid, signal.SIGKILL)
            self.fail("provider child survived controller SIGTERM")

    def test_resolved_placeholder_tool_is_bound_before_effect(self):
        tool_path = os.path.join(self.root, "scripts", "provider-tool")
        write(
            tool_path,
            "#!/bin/sh\n"
            "printf 'effect\\n' >> order.log\n"
            "printf '1' > counter.txt\n",
            mode=0o755,
        )
        subprocess.run(
            ["git", "-C", self.root, "add", "scripts/provider-tool"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "add provider tool"],
            check=True,
        )
        self.EXPLICIT_SCRIPTS = [
            *self.EXPLICIT_SCRIPTS,
            "scripts/provider-tool",
        ]
        self.discovery = self._discover()
        profile = self._profile_v2()
        profile["inputs"].append(
            {
                "name": "tool",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        profile["steps"][2]["argv"] = [
            "{{tool}}", "{{repository}}"
        ]
        self._adopt(profile)
        inputs = dict(
            self._v2_inputs(), tool="scripts/provider-tool"
        )
        leaked = os.path.join(self.root, "runtime-secret")
        real_require = release_profile._require_relative_inputs_current
        calls = 0

        def replace_after_path_check(root, candidate, runtime_inputs):
            nonlocal calls
            real_require(root, candidate, runtime_inputs)
            calls += 1
            if calls == 7:
                write(
                    tool_path,
                    "#!/bin/sh\n"
                    'printf \"%s\" \"$DEPLOY_TOKEN\" > runtime-secret\n',
                    mode=0o755,
                )

        with mock.patch.object(
            release_profile,
            "_require_relative_inputs_current",
            side_effect=replace_after_path_check,
        ):
            with mock.patch.dict(
                os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
            ):
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "(?:control|adopted_tool)_drift",
                ):
                    release_profile.run_profile(
                        self.root,
                        authorize=True,
                        write=True,
                        inputs=inputs,
                    )
        self.assertFalse(os.path.exists(leaked))

    def test_control_source_is_rechecked_after_runtime_path_guard(self):
        profile = self._profile_v2()
        self._adopt(profile)
        effect_path = os.path.join(self.root, "scripts", "effect.py")
        leaked = os.path.join(self.root, "runtime-secret")
        real_require = release_profile._require_relative_inputs_current
        calls = 0

        def replace_after_path_check(root, candidate, runtime_inputs):
            nonlocal calls
            real_require(root, candidate, runtime_inputs)
            calls += 1
            if calls == 7:
                write(
                    effect_path,
                    "import os\n"
                    "from pathlib import Path\n"
                    "Path('runtime-secret').write_text("
                    "os.environ['DEPLOY_TOKEN'])\n",
                )

        with mock.patch.object(
            release_profile,
            "_require_relative_inputs_current",
            side_effect=replace_after_path_check,
        ):
            with mock.patch.dict(
                os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
            ):
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "control_drift",
                ):
                    release_profile.run_profile(
                        self.root,
                        authorize=True,
                        write=True,
                        inputs=self._v2_inputs(),
                    )
        self.assertFalse(os.path.exists(leaked))

    def test_relative_input_is_rechecked_immediately_before_effect(self):
        effect_path = os.path.join(self.root, "scripts", "effect.py")
        write(
            effect_path,
            "import sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[-1]).write_text('effect')\n"
            "Path('counter.txt').write_text('1')\n",
        )
        subprocess.run(
            ["git", "-C", self.root, "add", "scripts/effect.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "consume artifact"],
            check=True,
        )
        self.discovery = self._discover()
        profile = self._profile_v2()
        profile["inputs"].append(
            {
                "name": "artifact_path",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        profile["steps"][2]["argv"].append("{{artifact_path}}")
        self._adopt(profile)
        inputs = dict(
            self._v2_inputs(), artifact_path="artifact.txt"
        )
        artifact = os.path.join(self.root, "artifact.txt")
        real_require = release_profile._require_relative_inputs_current
        calls = 0
        with tempfile.NamedTemporaryFile() as outside:
            outside.write(b"outside-secret")
            outside.flush()

            def replace_after_first_effect_guard(
                root, candidate, runtime_inputs
            ):
                nonlocal calls
                real_require(root, candidate, runtime_inputs)
                calls += 1
                if calls == 7:
                    os.unlink(artifact)
                    os.symlink(outside.name, artifact)

            with mock.patch.object(
                release_profile,
                "_require_relative_inputs_current",
                side_effect=replace_after_first_effect_guard,
            ):
                with mock.patch.dict(
                    os.environ,
                    {"DEPLOY_TOKEN": "runtime-only"},
                    clear=False,
                ):
                    with self.assertRaisesRegex(
                        release_profile.ReleaseProfileError,
                        "input_path_drift",
                    ):
                        release_profile.run_profile(
                            self.root,
                            authorize=True,
                            write=True,
                            inputs=inputs,
                        )
            outside.seek(0)
            self.assertEqual(outside.read(), b"outside-secret")

    def test_github_resolver_tool_is_rechecked_before_token_injection(self):
        gh_directory = os.path.join(self.root, "gh-bin")
        gh_path = os.path.join(gh_directory, "gh")
        write(
            gh_path,
            "#!/bin/sh\nprintf 'true\\n'\n",
            mode=0o755,
        )
        profile = self._profile_v2()
        profile["identity"] = {"provider": "github"}
        original_path = os.environ.get("PATH", os.defpath)
        runtime_path = gh_directory + os.pathsep + original_path
        with mock.patch.dict(
            os.environ, {"PATH": runtime_path}, clear=False
        ):
            self._adopt(profile)
        subprocess.run(
            [
                "git", "-C", self.root, "remote", "add", "origin",
                "https://github.com/org/project.git",
            ],
            check=True,
        )
        real_binding = release_memory.binding

        def replace_after_resolved_binding(*args, **kwargs):
            result = real_binding(*args, **kwargs)
            write(
                gh_path,
                "#!/bin/sh\n"
                'printf "%s" "$GH_TOKEN" > github-secret\n'
                "printf 'true\\n'\n",
                mode=0o755,
            )
            return result

        with mock.patch.object(
            release_memory,
            "binding",
            side_effect=replace_after_resolved_binding,
        ):
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": runtime_path,
                    "GITHUB_TOKEN": "native-runtime-secret",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "identity_tool_drift",
                ):
                    release_profile.run_profile(
                        self.root,
                        authorize=True,
                        write=True,
                        inputs=self._v2_inputs(),
                    )
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "github-secret"))
        )

    def test_evidence_drift_executes_only_invalidated_checks(self):
        profile = self._profile_v2()
        self._adopt(profile)
        run = self._verification_run()
        inputs = self._v2_inputs()
        check_path = os.path.join(self.root, "scripts", "check.py")
        with open(check_path, encoding="utf-8") as handle:
            check_source = handle.read()
        write(check_path, check_source + "# drift\n")
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "control_drift"
        ):
            release_profile.evidence_execute(
                self.root,
                run,
                "preflight",
                inputs=inputs,
                write=True,
            )
        write(check_path, check_source)

        real_execute = release_profile._execute_v2

        def mutate_affected(root, command, credentials=None):
            evidence = real_execute(
                root, command, credentials=credentials
            )
            if command["id"] == "preflight":
                write(os.path.join(root, "artifact.txt"), "mutated\n")
            return evidence

        with mock.patch.object(
            release_profile, "_execute_v2", side_effect=mutate_affected
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "evidence_inputs_changed",
            ):
                release_profile.evidence_execute(
                    self.root,
                    run,
                    "preflight",
                    inputs=inputs,
                    write=True,
                )
        write(os.path.join(self.root, "artifact.txt"), "stable\n")
        with mock.patch.object(
            release_profile, "_conformance_open", return_value=True
        ):
            release_profile.evidence_execute(
                self.root,
                run,
                "preflight",
                inputs=inputs,
                write=True,
            )
            release_profile.evidence_execute(
                self.root,
                run,
                "preflight-independent",
                inputs=inputs,
                write=True,
            )
            bundle = release_profile._load_bundle(self.root)
            resolved, input_sha256, resolved_sha256 = (
                release_profile._resolved_v2_profile(
                    self.root, profile, inputs
                )
            )
            changed_inputs = dict(inputs, tag="v1.2.4")
            changed_resolved, _, _ = release_profile._resolved_v2_profile(
                self.root,
                profile,
                changed_inputs,
                bundle["discovery"],
            )
            self.assertIsNotNone(
                release_profile._current_evidence(
                    self.root,
                    bundle,
                    changed_resolved["steps"][0],
                    changed_inputs,
                )
            )
            self.assertIsNotNone(
                release_profile._current_evidence(
                    self.root,
                    bundle,
                    changed_resolved["steps"][1],
                    changed_inputs,
                )
            )
            with mock.patch.object(
                release_profile, "_conformance_open", return_value=False
            ):
                self.assertIsNone(
                    release_profile._current_evidence(
                        self.root,
                        bundle,
                        resolved["steps"][0],
                        inputs,
                    )
                )
            order = os.path.join(self.root, "order.log")
            os.unlink(order)
            with mock.patch.dict(
                os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
            ):
                first = release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=inputs,
                )
        self.assertEqual(first["steps"][0]["evidence"]["source"],
                         "kimiflow_verification")
        self.assertEqual(
            first["steps"][1]["evidence"]["source"],
            "kimiflow_verification",
        )
        with open(order, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "effect\nfinal\n")

        write(os.path.join(self.root, "artifact.txt"), "drifted\n")
        with mock.patch.object(
            release_profile, "_conformance_open", return_value=True
        ):
            with mock.patch.dict(
                os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
            ):
                second = release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    new=True,
                    inputs=inputs,
                )
        self.assertEqual(second["steps"][0]["evidence"]["source"], "executed")
        self.assertEqual(
            second["steps"][1]["evidence"]["source"],
            "kimiflow_verification",
        )
        with open(order, encoding="utf-8") as handle:
            self.assertEqual(
                handle.read(), "effect\nfinal\ncheck\neffect\nfinal\n"
            )

    def test_retryable_failure_preserves_audit_and_uncertain_effect_never_replays(self):
        profile = self._profile_v2(post_script="scripts/fail.py")
        provider_command = profile["steps"][2]["precondition"]
        project_command = profile["steps"][0]
        self.assertEqual(
            release_profile._classify_v2_failure(
                1,
                b"HTTP 429 rate limit",
                provider_command,
                timed_out=False,
                unavailable=False,
            ),
            "rate_limit",
        )
        semantic_provider = json.loads(json.dumps(provider_command))
        semantic_provider["policy"]["failure"] = "semantic"
        self.assertEqual(
            release_profile._classify_v2_failure(
                1,
                b"HTTP 429 rate limit",
                semantic_provider,
                timed_out=False,
                unavailable=False,
            ),
            "semantic",
        )
        self.assertEqual(
            release_profile._classify_v2_failure(
                1,
                b"HTTP 429 rate limit",
                project_command,
                timed_out=False,
                unavailable=False,
            ),
            "semantic",
        )
        retryable_row = {
            "id": "publish",
            "kind": "effect",
            "status": "pending",
            "failure_class": None,
            "evidence": None,
        }
        retryable_receipt = {"status": "active"}
        with mock.patch.object(release_profile, "_persist_run"):
            with mock.patch.object(release_profile, "_failure") as failure:
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "retryable_failure",
                ):
                    release_profile._v2_failure(
                        self.root,
                        {},
                        retryable_receipt,
                        retryable_row,
                        {"failure_class": "rate_limit"},
                        "precondition_failed",
                    )
        self.assertEqual(retryable_row["status"], "retryable")
        self.assertEqual(retryable_receipt["status"], "retryable_failure")
        failure.assert_not_called()
        semantic_row = {
            "id": "preflight",
            "kind": "check",
            "status": "pending",
            "failure_class": None,
            "evidence": None,
        }
        semantic_receipt = {"status": "active"}
        with mock.patch.object(release_profile, "_persist_run"):
            with mock.patch.object(release_profile, "_failure") as failure:
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError, "step_failed"
                ):
                    release_profile._v2_failure(
                        self.root,
                        {},
                        semantic_receipt,
                        semantic_row,
                        {"failure_class": "semantic"},
                        "step_failed",
                    )
        self.assertEqual(semantic_row["status"], "failed")
        self.assertEqual(semantic_receipt["status"], "audit_required")
        failure.assert_called_once()

        self._adopt(profile)
        inputs = self._v2_inputs()
        environment = {"DEPLOY_TOKEN": "runtime-only"}
        with mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "retryable_failure"
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=inputs,
                )
        run_path = os.path.join(
            self.root, ".kimiflow", "release", "RUN.json"
        )
        with open(run_path, encoding="utf-8") as handle:
            first = json.load(handle)
        publish = next(row for row in first["steps"] if row["id"] == "publish")
        self.assertEqual(publish["status"], "started")
        self.assertEqual(publish["failure_class"], "auth")
        first_postcondition = publish["evidence"]["postcondition"]
        self.assertRegex(
            first_postcondition["output_sha256"], r"^sha256:[0-9a-f]{64}$"
        )
        self.assertNotIn("unauthorized", json.dumps(first_postcondition))

        with mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "retryable_failure"
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=inputs,
                )
        with open(os.path.join(self.root, "counter.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "1")
        with open(
            os.path.join(self.root, "order.log"), encoding="utf-8"
        ) as handle:
            self.assertEqual(handle.read(), "check\ncheck\neffect\n")

    def test_completed_run_backfills_memory_without_repeating_work(self):
        profile = self._profile_v2()
        self._adopt(profile)
        environment = {"DEPLOY_TOKEN": "runtime-only"}
        with mock.patch.dict(os.environ, environment, clear=False):
            completed = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=self._v2_inputs(),
            )
        self.assertEqual(completed["status"], "completed")
        with open(
            os.path.join(self.root, "order.log"), encoding="utf-8"
        ) as handle:
            original_order = handle.read()
        memory_path = os.path.join(
            self.root, ".kimiflow", "release", "MEMORY.json"
        )
        with open(memory_path, "w", encoding="utf-8"):
            pass
        with mock.patch.object(
            release_memory.os, "fsync", wraps=os.fsync
        ) as fsync, mock.patch.dict(
            os.environ, environment, clear=False
        ):
            recovered = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=self._v2_inputs(),
            )
        self.assertGreaterEqual(fsync.call_count, 2)
        self.assertEqual(recovered["status"], "completed")
        with open(
            os.path.join(self.root, "order.log"), encoding="utf-8"
        ) as handle:
            self.assertEqual(handle.read(), original_order)
        learned = release_memory.read_memory(
            self.root, recovered["binding"]
        )
        self.assertEqual(learned["generation"], recovered["generation"])
        self.assertEqual(
            learned["successful_steps"],
            [
                "preflight",
                "preflight-independent",
                "publish",
                "release-verified",
            ],
        )

    def test_provider_check_retry_clears_transient_failure_state(self):
        profile = self._profile_v2()
        profile["steps"][0]["policy"] = self._policy(
            auth="provider",
            failure="operational",
        )
        self._adopt(profile)
        real_execute = release_profile._execute_v2
        attempts = [0]

        def fail_once(root, command, credentials=None):
            if command["id"] == "preflight" and attempts[0] == 0:
                attempts[0] += 1
                return {
                    "exit_code": 7,
                    "output_sha256": "sha256:" + "1" * 64,
                    "output_bytes": 0,
                    "command_sha256": release_profile._command_digest(
                        command
                    ),
                    "duration_milliseconds": 1,
                    "failure_class": "auth",
                    "source": "executed",
                }
            return real_execute(
                root, command, credentials=credentials
            )

        environment = {"DEPLOY_TOKEN": "runtime-only"}
        with mock.patch.object(
            release_profile, "_execute_v2", side_effect=fail_once
        ), mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "retryable_failure",
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=self._v2_inputs(),
                )
            completed = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=self._v2_inputs(),
            )
        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(completed["steps"][0]["failure_class"])
        self.assertEqual(
            release_profile.status(self.root)["run_status"], "completed"
        )

    def test_release_effect_rejects_secret_bearing_runtime_artifact(self):
        profile = self._profile_v2()
        profile["inputs"].append(
            {
                "name": "artifact_path",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        profile["steps"][2]["argv"].append("{{artifact_path}}")
        self._adopt(profile)
        artifact = os.path.join(self.root, "dist", "release.bin")
        write(
            artifact,
            "public bytes\nghp_1234567890abcdefghijklmnop\n",
        )
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "release_artifact_secret_detected",
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=dict(
                        self._v2_inputs(),
                        artifact_path="dist/release.bin",
                    ),
                )
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "counter.txt"))
        )

    def test_release_effect_scans_static_paths_and_archive_members(self):
        static_profile = self._profile_v2()
        static_profile["steps"][2]["argv"].append(
            "dist/static-release.bin"
        )
        static_artifact = os.path.join(
            self.root, "dist", "static-release.bin"
        )
        write(
            static_artifact,
            "ghp_1234567890abcdefghijklmnop\n",
        )
        self._adopt(static_profile)
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "release_artifact_secret_detected",
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=self._v2_inputs(),
                )
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "counter.txt"))
        )

        archive_profile = self._profile_v2()
        archive_profile["inputs"].append(
            {
                "name": "artifact_path",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        archive_profile["steps"][2]["argv"].append(
            "{{artifact_path}}"
        )
        self._adopt(archive_profile)
        archive_path = os.path.join(self.root, "dist", "release.zip")
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr(
                "payload.txt",
                "ghp_1234567890abcdefghijklmnop\n",
            )
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "release_artifact_secret_detected",
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    new=True,
                    inputs=dict(
                        self._v2_inputs(),
                        artifact_path="dist/release.zip",
                    ),
                )

    def test_release_effect_rejects_unsafe_static_artifact_paths(self):
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside = os.path.join(outside_dir.name, "outside-release.bin")
        write(outside, "runtime-only\n")
        for candidate in ("dist/release-link.bin", outside):
            profile = self._profile_v2()
            profile["steps"][2]["argv"].append(candidate)
            if not os.path.isabs(candidate):
                os.makedirs(os.path.join(self.root, "dist"), exist_ok=True)
                os.symlink(
                    outside,
                    os.path.join(self.root, *candidate.split("/")),
                )
            self._adopt(profile)
            with mock.patch.dict(
                os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
            ):
                with self.assertRaisesRegex(
                    release_profile.ReleaseProfileError,
                    "release_artifact_unsafe",
                ):
                    release_profile.run_profile(
                        self.root,
                        authorize=True,
                        write=True,
                        new=True,
                        inputs=self._v2_inputs(),
                    )
            self.assertFalse(
                os.path.exists(os.path.join(self.root, "counter.txt"))
            )

    def test_release_directory_snapshot_rejects_late_secret_entry(self):
        profile = self._profile_v2()
        profile["inputs"].append(
            {
                "name": "artifact_path",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        profile["steps"][2]["argv"].append("{{artifact_path}}")
        self._adopt(profile)
        write(os.path.join(self.root, "dist", "public.txt"), "public\n")
        real_persist = release_profile._persist_run
        injected = [False]

        def inject_after_started(root, receipt):
            result = real_persist(root, receipt)
            publish = next(
                row for row in receipt["steps"] if row["id"] == "publish"
            )
            if publish["status"] == "started" and not injected[0]:
                injected[0] = True
                write(
                    os.path.join(self.root, "dist", "late.bin"),
                    "runtime-only\n",
                )
            return result

        with mock.patch.object(
            release_profile,
            "_persist_run",
            side_effect=inject_after_started,
        ), mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "release_artifact_drift",
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=dict(
                        self._v2_inputs(),
                        artifact_path="dist",
                    ),
                )
        self.assertFalse(
            os.path.exists(os.path.join(self.root, "counter.txt"))
        )

    def test_sigkill_watchdog_removes_credential_home(self):
        provider = os.path.join(self.root, "scripts", "provider-sleep.py")
        controller = os.path.join(
            self.root, "scripts", "controller-sleep.py"
        )
        write(
            provider,
            "import os,time\n"
            "from pathlib import Path\n"
            "home=Path(os.environ['HOME'])\n"
            "(home/'credential-cache').write_text("
            "os.environ['DEPLOY_TOKEN'])\n"
            "Path('home-path').write_text(str(home))\n"
            "time.sleep(60)\n",
        )
        write(
            controller,
            "import sys\n"
            "from kimiflow_core import release_profile\n"
            "root=sys.argv[1]\n"
            "release_profile._execute_v2(root,{"
            "'id':'provider-sleep','kind':'effect',"
            "'argv':[sys.executable,'scripts/provider-sleep.py'],"
            "'cwd':'.','timeout_seconds':120,'policy':{"
            "'auth':'provider','stage':'provider',"
            "'failure':'operational','reuse':'never',"
            "'affected_paths':[],'declared_env':[]}},"
            "credentials={'DEPLOY_TOKEN':'runtime-only'})\n",
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(__file__))
            )
        )
        process = subprocess.Popen(
            [sys.executable, controller, self.root],
            cwd=self.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        marker = os.path.join(self.root, "home-path")
        deadline = time.time() + 10
        while not os.path.exists(marker) and time.time() < deadline:
            time.sleep(0.05)
        if not os.path.exists(marker):
            process.kill()
            process.wait()
            self.fail("provider did not create the temporary-home marker")
        with open(marker, encoding="utf-8") as handle:
            home = handle.read()
        self.assertTrue(
            os.path.exists(os.path.join(home, "credential-cache"))
        )
        os.kill(process.pid, signal.SIGKILL)
        process.wait()
        deadline = time.time() + 10
        while os.path.exists(home) and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(os.path.exists(home))

    def test_nonzero_effect_requires_exact_audit_before_post_only_resume(self):
        effect_path = os.path.join(self.root, "scripts", "effect.py")
        with open(effect_path, encoding="utf-8") as handle:
            effect_source = handle.read()
        write(effect_path, effect_source + "raise SystemExit(7)\n")
        subprocess.run(
            ["git", "-C", self.root, "add", "scripts/effect.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "failing effect"],
            check=True,
        )
        self.discovery = self._discover()
        profile = self._profile_v2()
        self._adopt(profile)
        inputs = self._v2_inputs()
        environment = {"DEPLOY_TOKEN": "runtime-only"}

        with mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "effect_reported_failure",
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=inputs,
                )
        run_path = os.path.join(
            self.root, ".kimiflow", "release", "RUN.json"
        )
        with open(run_path, encoding="utf-8") as handle:
            blocked = json.load(handle)
        publish = next(
            row for row in blocked["steps"] if row["id"] == "publish"
        )
        self.assertEqual(blocked["status"], "audit_required")
        self.assertEqual(publish["status"], "started")
        self.assertEqual(publish["evidence"]["effect"]["exit_code"], 7)
        self.assertEqual(publish["failure_audit_sha256s"], [])
        self.assertTrue(
            os.path.exists(
                os.path.join(
                    self.root, ".kimiflow", "release", "FAILURE.json"
                )
            )
        )
        bundle = release_profile._load_bundle(self.root)
        resolved, _, _ = release_profile._resolved_v2_profile(
            self.root, profile, inputs, bundle["discovery"]
        )
        forged = json.loads(json.dumps(blocked))
        forged_publish = next(
            row for row in forged["steps"] if row["id"] == "publish"
        )
        forged_publish["status"] = "completed"
        forged_publish["failure_audit_sha256s"] = [
            "sha256:" + "0" * 64
        ]
        forged_publish["evidence"]["postcondition"] = (
            release_profile._execute_v2(
                self.root,
                resolved["steps"][2]["postcondition"],
                credentials=environment,
            )
        )
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "run_step_invalid"
        ):
            release_profile._validate_run_v2(
                forged, bundle, resolved_profile=resolved
            )

        with mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "release_failure"
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=inputs,
                )

        audit = self._audit(
            profile, failure_sha256=self._failure_sha256()
        )
        self._adopt(profile, audit)
        with mock.patch.dict(os.environ, environment, clear=False):
            completed = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=inputs,
            )
        self.assertEqual(completed["status"], "completed")
        publish = next(
            row for row in completed["steps"] if row["id"] == "publish"
        )
        self.assertTrue(
            any(
                release_profile._failure_audit_matches(
                    release_profile._load_bundle(self.root),
                    marker,
                    publish["id"],
                    publish["evidence"]["effect"],
                )
                for marker in publish["failure_audit_sha256s"]
            )
        )
        self.assertEqual(publish["evidence"]["effect"]["exit_code"], 7)
        with open(
            os.path.join(self.root, "counter.txt"), encoding="utf-8"
        ) as handle:
            self.assertEqual(handle.read(), "1")

    def test_nonzero_effect_crash_reconciles_failure_without_replay(self):
        effect_path = os.path.join(self.root, "scripts", "effect.py")
        with open(effect_path, encoding="utf-8") as handle:
            effect_source = handle.read()
        write(effect_path, effect_source + "raise SystemExit(7)\n")
        subprocess.run(
            ["git", "-C", self.root, "add", "scripts/effect.py"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", self.root, "commit", "-qm", "failing effect"],
            check=True,
        )
        self.discovery = self._discover()
        profile = self._profile_v2()
        self._adopt(profile)
        inputs = self._v2_inputs()
        environment = {"DEPLOY_TOKEN": "runtime-only"}
        real_persist = release_profile._persist_run
        crashed = False

        def crash_after_effect_receipt(root, receipt):
            nonlocal crashed
            real_persist(root, receipt)
            publish = next(
                row for row in receipt["steps"]
                if row["id"] == "publish"
            )
            effect = (publish.get("evidence") or {}).get("effect")
            if (
                not crashed
                and receipt["status"] == "active"
                and effect is not None
                and effect["exit_code"] != 0
            ):
                crashed = True
                raise RuntimeError("simulated_crash")

        with mock.patch.object(
            release_profile,
            "_persist_run",
            side_effect=crash_after_effect_receipt,
        ):
            with mock.patch.dict(
                os.environ, environment, clear=False
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "simulated_crash"
                ):
                    release_profile.run_profile(
                        self.root,
                        authorize=True,
                        write=True,
                        inputs=inputs,
                    )
        failure_path = os.path.join(
            self.root, ".kimiflow", "release", "FAILURE.json"
        )
        self.assertFalse(os.path.exists(failure_path))

        with mock.patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "effect_reported_failure",
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=inputs,
                )
        self.assertTrue(os.path.exists(failure_path))
        with open(
            os.path.join(
                self.root, ".kimiflow", "release", "RUN.json"
            ),
            encoding="utf-8",
        ) as handle:
            reconciled = json.load(handle)
        self.assertEqual(reconciled["status"], "audit_required")

        audit = self._audit(
            profile, failure_sha256=self._failure_sha256()
        )
        self._adopt(profile, audit)
        with mock.patch.dict(os.environ, environment, clear=False):
            completed = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=inputs,
            )
        self.assertEqual(completed["status"], "completed")
        with open(
            os.path.join(self.root, "counter.txt"), encoding="utf-8"
        ) as handle:
            self.assertEqual(handle.read(), "1")

    def test_release_metrics_separate_control_check_build_provider_time(self):
        profile = self._profile_v2()
        self._adopt(profile)
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=self._v2_inputs(),
            )
        metrics_path = os.path.join(
            self.root, ".kimiflow", "release", "METRICS.json"
        )
        with open(metrics_path, encoding="utf-8") as handle:
            metrics = json.load(handle)
        self.assertEqual(
            set(metrics["duration_milliseconds"]),
            {"kimiflow_control", "project_checks", "build", "provider"},
        )
        self.assertEqual(metrics["counts"]["model_calls"], 0)
        self.assertEqual(metrics["counts"]["audits_executed"], 0)
        self.assertEqual(metrics["counts"]["discovery_content_reads"], 0)
        self.assertGreaterEqual(metrics["counts"]["checks_executed"], 3)
        memory_path = os.path.join(
            self.root, ".kimiflow", "release", "MEMORY.json"
        )
        with open(memory_path, encoding="utf-8") as handle:
            learned = json.load(handle)
        self.assertEqual(
            set(learned["duration_totals"]),
            {"kimiflow_control", "project_checks", "build", "provider"},
        )
        self.assertEqual(
            learned["duration_totals"]["kimiflow_control"]["milliseconds"],
            metrics["duration_milliseconds"]["kimiflow_control"],
        )
        self.assertTrue(
            all(
                set(item) == {"runs", "milliseconds"}
                and item["runs"] == 1
                and item["milliseconds"] >= 0
                for item in learned["duration_totals"].values()
            )
        )

    def test_relative_input_evidence_and_directory_descendants_are_bound(self):
        profile = self._profile_v2()
        profile["inputs"].append(
            {
                "name": "artifact_path",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        profile["steps"][0]["argv"].append("{{artifact_path}}")
        profile["steps"][0]["policy"]["affected_paths"] = ["artifact2.txt"]
        self._adopt(profile)
        inputs = dict(
            self._v2_inputs(), artifact_path="artifact.txt"
        )
        run = self._verification_run()
        with mock.patch.object(
            release_profile, "_conformance_open", return_value=True
        ):
            release_profile.evidence_execute(
                self.root,
                run,
                "preflight",
                inputs=inputs,
                write=True,
            )
            bundle = release_profile._load_bundle(self.root)
            resolved = release_profile._resolved_v2_profile(
                self.root, profile, inputs, bundle["discovery"]
            )[0]
            write(os.path.join(self.root, "artifact.txt"), "changed\n")
            self.assertIsNone(
                release_profile._current_evidence(
                    self.root,
                    bundle,
                    resolved["steps"][0],
                    inputs,
                )
            )

        directory = os.path.join(self.root, "dist")
        os.mkdir(directory)
        outside = tempfile.NamedTemporaryFile()
        self.addCleanup(outside.close)
        os.symlink(outside.name, os.path.join(directory, "artifact"))
        self.assertFalse(
            release_profile._relative_input_path_safe(
                self.root, "dist"
            )
        )

    def test_v2_process_boundary_isolated_and_output_bounded(self):
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "command_environment_override_forbidden",
        ):
            release_profile._validate_argv(
                [
                    "env",
                    "HOME=.",
                    "XDG_STATE_HOME=.",
                    "GH_CONFIG_DIR=.",
                    "gh",
                    "version",
                ],
                "fixture",
                sealed=True,
            )
        for argv in (
            ["env", "-u", "HOME", "printenv", "HOME"],
            ["env", "--unset=HOME", "printenv", "HOME"],
            ["env", "-i", "printenv", "HOME"],
            ["env", "--", "HOME=.", "printenv", "HOME"],
        ):
            with self.subTest(argv=argv), self.assertRaisesRegex(
                release_profile.ReleaseProfileError,
                "command_environment_override_forbidden",
            ):
                release_profile._validate_argv(
                    argv, "fixture", sealed=True
                )
        unsafe_profile = self._profile_v2()
        unsafe_profile["steps"][2]["argv"] = [
            "env", "-u", "HOME", "python3", "scripts/effect.py"
        ]
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "command_environment_override_forbidden",
        ):
            release_profile.validate_profile(unsafe_profile)
        unused_target = self._profile_v2()
        unused_target["steps"][2]["argv"] = [
            "python3", "scripts/effect.py", "{{tag}}"
        ]
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "profile_publication_target_unused",
        ):
            release_profile.validate_profile(unused_target)
        unsafe_reuse = self._profile_v2()
        unsafe_reuse["steps"][0]["policy"]["auth"] = "provider"
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "check_step_reuse_invalid",
        ):
            release_profile.validate_profile(unsafe_reuse)
        self.assertEqual(
            release_profile._validate_argv(
                ["env", "PUBLIC_BUILD=1", "printenv", "PUBLIC_BUILD"],
                "fixture",
                sealed=True,
            )[1],
            "PUBLIC_BUILD=1",
        )

        noisy = os.path.join(self.root, "scripts", "noisy.sh")
        write(
            noisy,
            "#!/bin/sh\n"
            "i=0\n"
            "while [ \"$i\" -lt 20000 ]; do\n"
            "  printf 0123456789\n"
            "  i=$((i+1))\n"
            "done\n",
            mode=0o755,
        )
        command = {
            "id": "bounded-output",
            "kind": "check",
            "argv": [noisy],
            "cwd": ".",
            "timeout_seconds": 5,
            "policy": self._policy(),
        }
        with mock.patch.object(
            release_profile, "MAX_OUTPUT_BYTES", 1024
        ):
            evidence = release_profile._execute_v2(
                self.root, command
            )
        self.assertEqual(evidence["exit_code"], 125)
        self.assertEqual(evidence["failure_class"], "unavailable")
        self.assertEqual(evidence["output_bytes"], 1025)

    def test_failure_history_is_typed_profile_scoped_and_durable(self):
        profile = self._profile_v2()
        adopted = self._adopt(profile)
        evidence = {
            "exit_code": 1,
            "output_sha256": "sha256:" + "0" * 64,
            "output_bytes": 0,
            "command_sha256": "sha256:" + "1" * 64,
            "duration_milliseconds": 1,
            "failure_class": "semantic",
            "source": "executed",
        }
        failure = {
            "schema_version": 1,
            "event_id": "1" * 32,
            "profile_sha256": adopted["profile_sha256"],
            "step_id": "preflight",
            "evidence": evidence,
        }
        failure_path = os.path.join(
            self.root, ".kimiflow", "release", "FAILURE.json"
        )
        json_write(failure_path, failure)
        recovered = self._profile_v2()
        recovered["id"] = "recovered-release"
        self._adopt(
            recovered,
            self._audit(
                recovered,
                failure_sha256=release_profile._value_sha(failure),
            ),
        )
        self.assertEqual(
            release_profile.status(self.root)["status"], "ready"
        )
        self.assertEqual(
            release_profile._load_bundle(self.root)["failure_audits"],
            [],
        )

        self._adopt(profile)
        malformed = dict(
            failure,
            evidence={
                "raw_token": "ghp_fixture_secret_that_must_never_persist"
            },
        )
        json_write(failure_path, malformed)
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError,
            "failure_evidence_invalid",
        ):
            self._adopt(
                profile,
                self._audit(
                    profile,
                    failure_sha256=release_profile._value_sha(malformed),
                ),
            )
        with open(
            os.path.join(
                self.root, ".kimiflow", "release", "PROFILE.json"
            ),
            encoding="utf-8",
        ) as handle:
            self.assertNotIn("ghp_fixture", handle.read())

    def test_audit_adoption_persists_run_before_clearing_failure(self):
        profile = self._profile_v2()
        profile["steps"][0]["argv"] = [
            "python3", "scripts/fail.py"
        ]
        self._adopt(profile)
        failure_events = []
        real_persist = release_profile._persist_local

        def record_failure_persist(path, value, error):
            failure_events.append(os.path.basename(path))
            return real_persist(path, value, error)

        with mock.patch.object(
            release_profile,
            "_persist_local",
            side_effect=record_failure_persist,
        ), mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            with self.assertRaisesRegex(
                release_profile.ReleaseProfileError, "step_failed"
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=self._v2_inputs(),
                )
        self.assertLess(
            failure_events.index("FAILURE.json"),
            len(failure_events) - 1
            - failure_events[::-1].index("RUN.json"),
        )
        failure_path = os.path.join(
            self.root, ".kimiflow", "release", "FAILURE.json"
        )
        audit = self._audit(
            profile, failure_sha256=self._failure_sha256()
        )
        with mock.patch.object(
            release_profile,
            "_persist_run",
            side_effect=RuntimeError("durability fault"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "durability fault"
            ):
                self._adopt(profile, audit)
        self.assertTrue(os.path.exists(failure_path))

        events = []

        def persist(path, value, error):
            events.append(("persist", os.path.basename(path)))
            return real_persist(path, value, error)

        real_unlink = os.unlink

        def unlink(path):
            events.append(("unlink", os.path.basename(path)))
            return real_unlink(path)

        with mock.patch.object(
            release_profile, "_persist_local", side_effect=persist
        ), mock.patch.object(
            release_profile.os, "unlink", side_effect=unlink
        ), mock.patch.object(
            release_profile,
            "_durable_unlink",
            wraps=release_profile._durable_unlink,
        ) as durable_unlink, mock.patch.object(
            release_profile.os, "fsync", wraps=os.fsync
        ) as fsync:
            self._adopt(profile, audit)
        durable_unlink.assert_called_once()
        self.assertEqual(
            os.path.realpath(durable_unlink.call_args.args[0]),
            os.path.realpath(failure_path),
        )
        self.assertEqual(
            durable_unlink.call_args.args[1], "failure_retire_failed"
        )
        self.assertGreaterEqual(fsync.call_count, 4)
        self.assertLess(
            events.index(("persist", "PROFILE.json")),
            events.index(("persist", "RUN.json")),
        )
        self.assertLess(
            events.index(("persist", "RUN.json")),
            events.index(("unlink", "FAILURE.json")),
        )

    def test_profile_replacement_keeps_completed_run_until_profile_durable(self):
        profile = self._profile_v2()
        self._adopt(profile)
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=self._v2_inputs(),
            )
        run_path = os.path.join(
            self.root, ".kimiflow", "release", "RUN.json"
        )
        replacement = self._profile_v2()
        replacement["id"] = "replacement-release"
        real_persist = release_profile._persist_local

        def fail_profile(path, value, error):
            if os.path.basename(path) == "PROFILE.json":
                raise RuntimeError("profile durability fault")
            return real_persist(path, value, error)

        with mock.patch.object(
            release_profile,
            "_persist_local",
            side_effect=fail_profile,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "profile durability fault"
            ):
                self._adopt(replacement)
        self.assertTrue(os.path.exists(run_path))
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            old = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=self._v2_inputs(),
            )
        self.assertEqual(old["status"], "completed")
        with open(
            os.path.join(self.root, "counter.txt"), encoding="utf-8"
        ) as handle:
            self.assertEqual(handle.read(), "1")

        self._adopt(replacement)
        self.assertFalse(os.path.exists(run_path))
        self.assertEqual(
            release_profile.status(self.root)["run_status"], "none"
        )

    def test_workspace_root_ignores_ambient_git_redirection(self):
        with tempfile.TemporaryDirectory() as foreign:
            subprocess.run(["git", "init", "-q", foreign], check=True)
            shim_directory = os.path.join(foreign, "shim")
            write(
                os.path.join(shim_directory, "git"),
                "#!/bin/sh\nprintf '%s\\n' "
                + repr(os.path.realpath(foreign))
                + "\n",
                mode=0o755,
            )
            with mock.patch.dict(
                os.environ,
                {
                    "GIT_DIR": os.path.join(foreign, ".git"),
                    "GIT_WORK_TREE": foreign,
                    "PATH": shim_directory
                    + os.pathsep
                    + os.environ.get("PATH", os.defpath),
                },
                clear=False,
            ):
                self.assertEqual(
                    release_profile.workspace_root(self.root),
                    os.path.realpath(self.root),
                )

    def test_active_resume_rechecks_relative_input_consuming_check(self):
        profile = self._profile_v2()
        profile["inputs"].append(
            {
                "name": "artifact_path",
                "type": "relative_path",
                "publication_target": False,
            }
        )
        profile["steps"][0]["argv"].append("{{artifact_path}}")
        self._adopt(profile)
        inputs = dict(
            self._v2_inputs(), artifact_path="artifact.txt"
        )
        real_execute = release_profile._execute_v2

        def interrupt_after_first_check(root, command, credentials=None):
            if command["id"] == "preflight-independent":
                raise RuntimeError("fixture interruption")
            return real_execute(
                root, command, credentials=credentials
            )

        with mock.patch.object(
            release_profile,
            "_execute_v2",
            side_effect=interrupt_after_first_check,
        ), mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            with self.assertRaisesRegex(
                RuntimeError, "fixture interruption"
            ):
                release_profile.run_profile(
                    self.root,
                    authorize=True,
                    write=True,
                    inputs=inputs,
                )
        write(os.path.join(self.root, "artifact.txt"), "changed\n")
        with mock.patch.dict(
            os.environ, {"DEPLOY_TOKEN": "runtime-only"}, clear=False
        ):
            resumed = release_profile.run_profile(
                self.root,
                authorize=True,
                write=True,
                inputs=inputs,
            )
        self.assertEqual(resumed["status"], "completed")
        with open(
            os.path.join(self.root, "order.log"), encoding="utf-8"
        ) as handle:
            self.assertEqual(
                handle.read(),
                "check\ncheck\ncheck\neffect\nfinal\n",
            )

    def test_v1_failure_can_be_audited_during_v2_upgrade(self):
        legacy = self._profile(failing_check=True)
        self._adopt(legacy)
        with self.assertRaisesRegex(
            release_profile.ReleaseProfileError, "step_failed"
        ):
            release_profile.run_profile(
                self.root, authorize=True, write=True
            )
        upgraded = self._profile_v2()
        result = self._adopt(
            upgraded,
            self._audit(
                upgraded, failure_sha256=self._failure_sha256()
            ),
        )
        self.assertEqual(result["status"], "adopted")
        self.assertEqual(
            release_profile.status(self.root)["status"], "ready"
        )
        self.assertEqual(
            release_profile._load_bundle(self.root)["failure_audits"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
