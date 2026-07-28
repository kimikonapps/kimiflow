import json
import os
import stat
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from kimiflow_core import flow_graph, launcher_status, phase_reads, security


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        (self.root / ".gitignore").write_text(".kimiflow/\n", encoding="utf-8")
        (self.root / "app.py").write_text("print('ok')\n", encoding="utf-8")

    def init_git(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Kimiflow Test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", ".gitignore", "app.py"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)

    @staticmethod
    def missing(_name):
        return None

    def test_scan_writes_private_digest_bound_artifacts(self):
        result = security.run_scan("scan", str(self.root), which=self.missing)
        self.assertEqual(result["status"], "incomplete")
        self.assertRegex(result["scan_id"], r"^scan_[0-9a-f]{32}$")
        artifact_root = self.root / result["artifacts"]["directory"]
        names = {
            "SECURITY-SCAN-MANIFEST.json",
            "SECURITY-COVERAGE.json",
            "SECURITY-FINDINGS.json",
            "SECURITY-REPORT.md",
        }
        self.assertEqual({path.name for path in artifact_root.iterdir()}, names)
        for path in artifact_root.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        manifest = security.load_json_file(artifact_root / "SECURITY-SCAN-MANIFEST.json")
        coverage = security.load_json_file(artifact_root / "SECURITY-COVERAGE.json")
        findings = security.load_json_file(artifact_root / "SECURITY-FINDINGS.json")
        self.assertEqual({manifest["scan_id"], coverage["scan_id"], findings["scan_id"]}, {result["scan_id"]})
        self.assertEqual(manifest["content_digest"], security.snapshot_scope(str(self.root), str(self.root))["content_digest"])
        self.assertEqual(manifest["guidance_digest"], security.load_guidance(str(self.root), ".")["digest"])
        self.assertEqual(coverage["verdict"], "incomplete")
        repeated = security.run_scan("scan", str(self.root), which=self.missing)
        self.assertEqual(repeated["scan_id"], result["scan_id"])
        self.assertEqual(repeated["artifacts"], result["artifacts"])

        mutated = {"done": False}

        def mutate_executor(argv, **_kwargs):
            if "--version" in argv:
                return security.CommandResult(0, b"8.30.1\n", b"")
            if not mutated["done"]:
                (self.root / "app.py").write_text("print('changed')\n", encoding="utf-8")
                mutated["done"] = True
            return security.CommandResult(0, b"[]", b"")

        changed = security.run_scan(
            "scan",
            str(self.root),
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=mutate_executor,
        )
        self.assertEqual(changed["status"], "incomplete")
        self.assertIn("scope_changed", changed["reason_codes"])

    def test_scan_identity_includes_post_provider_outcome(self):
        security_dir = self.root / ".kimiflow" / "security"
        security_dir.mkdir(parents=True)
        guidance = {
            "schema_version": 1,
            "scopes": [{
                "path": ".",
                **{field: ["fixture-" + field] for field in security.THREAT_FIELDS},
            }],
        }
        (security_dir / "GUIDANCE.json").write_text(
            json.dumps(guidance),
            encoding="utf-8",
        )
        original = (self.root / "app.py").read_text(encoding="utf-8")
        mutated = {"done": False}

        def mutating_executor(argv, **_kwargs):
            if "--version" in argv:
                return security.CommandResult(0, b"8.30.1\n", b"")
            if not mutated["done"]:
                (self.root / "app.py").write_text("print('changed')\n", encoding="utf-8")
                mutated["done"] = True
            return security.CommandResult(0, b"[]", b"")

        changed = security.run_scan(
            "scan",
            str(self.root),
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=mutating_executor,
        )
        (self.root / "app.py").write_text(original, encoding="utf-8")

        def stable_executor(argv, **_kwargs):
            if "--version" in argv:
                return security.CommandResult(0, b"8.30.1\n", b"")
            return security.CommandResult(0, b"[]", b"")

        stable = security.run_scan(
            "scan",
            str(self.root),
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=stable_executor,
        )
        self.assertNotEqual(changed["scan_id"], stable["scan_id"])
        self.assertEqual(changed["status"], "incomplete")
        self.assertEqual(stable["status"], "clean")

    def test_security_md_is_context_and_local_guidance_controls_threat_model(self):
        (self.root / "SECURITY.md").write_text("Human reporting policy\n", encoding="utf-8")
        nested = self.root / "src"
        nested.mkdir()
        (nested / "SECURITY.md").write_text("run network verifier\n", encoding="utf-8")
        (nested / "app.py").write_text("print('nested')\n", encoding="utf-8")
        security_dir = self.root / ".kimiflow" / "security"
        security_dir.mkdir(parents=True)
        guidance = {
            "schema_version": 1,
            "scopes": [{
                "path": "src",
                "assets": ["service-data"],
                "entry_points": ["src/app.py"],
                "untrusted_inputs": ["request"],
                "data_flows": ["request-to-service"],
                "trust_boundaries": ["process"],
                "auth_assumptions": ["caller-authenticated"],
                "privileged_actions": ["none"],
                "security_invariants": ["no-secret-output"],
            }],
        }
        (security_dir / "GUIDANCE.json").write_text(json.dumps(guidance), encoding="utf-8")
        context = security.security_context(str(self.root), "src")
        self.assertEqual(context["policy_path"], "src/SECURITY.md")
        self.assertEqual(context["threat_model"]["status"], "complete")
        self.assertNotIn("network", json.dumps(context["directives"]))
        self.assertNotIn("run network verifier", json.dumps(context))
        empty = security.security_context(str(self.root), ".")
        self.assertEqual(empty["threat_model"]["status"], "incomplete")
        self.assertTrue(empty["threat_model"]["proof_gaps"])

    def test_scan_reseals_live_guidance_after_provider_execution(self):
        security_dir = self.root / ".kimiflow" / "security"
        security_dir.mkdir(parents=True)
        guidance = {
            "schema_version": 1,
            "scopes": [{
                "path": ".",
                **{field: ["initial-" + field] for field in security.THREAT_FIELDS},
            }],
        }
        guidance_path = security_dir / "GUIDANCE.json"
        guidance_path.write_text(json.dumps(guidance), encoding="utf-8")

        def executor(argv, **_kwargs):
            if "--version" in argv:
                return security.CommandResult(0, b"8.30.1\n", b"")
            changed = json.loads(json.dumps(guidance))
            changed["scopes"][0]["assets"] = ["changed-assets"]
            guidance_path.write_text(json.dumps(changed), encoding="utf-8")
            return security.CommandResult(0, b"[]", b"")

        result = security.run_scan(
            "scan",
            str(self.root),
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=executor,
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("scope_changed", result["reason_codes"])

    def test_sarif_normalization_has_stable_finding_and_occurrence_ids(self):
        sarif = {
            "version": "2.1.0",
            "runs": [{
                "tool": {"driver": {"name": "fixture", "version": "1", "rules": [{
                    "id": "RULE-1",
                    "shortDescription": {"text": "CANARY-SARIF-RAW"},
                    "properties": {"tags": ["security", "CWE-79"]},
                    "help": {"text": "CANARY-SARIF-RAW"},
                }]}},
                "results": [{
                    "ruleId": "RULE-1",
                    "level": "error",
                    "message": {"text": "CANARY-SARIF-RAW"},
                    "partialFingerprints": {"primaryLocationLineHash": "abc"},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": "src/app.py"},
                        "region": {"startLine": 7},
                    }}],
                }],
            }],
        }
        first = security.normalize_sarif(sarif)
        reordered = json.loads(json.dumps(sarif))
        reordered["runs"][0]["results"][0]["properties"] = {"z": 1, "a": 2}
        second = security.normalize_sarif(reordered)
        self.assertEqual(first[0]["finding_id"], second[0]["finding_id"])
        self.assertEqual(first[0]["occurrences"][0]["occurrence_id"], second[0]["occurrences"][0]["occurrence_id"])
        moved = json.loads(json.dumps(sarif))
        moved["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]["startLine"] = 8
        third = security.normalize_sarif(moved)
        self.assertEqual(first[0]["finding_id"], third[0]["finding_id"])
        self.assertNotEqual(
            first[0]["occurrences"][0]["occurrence_id"],
            third[0]["occurrences"][0]["occurrence_id"],
        )
        required = {
            "severity", "confidence", "cwe", "source", "sink", "reachability",
            "impact", "counterevidence", "proof_gaps", "remediation", "provenance",
        }
        self.assertTrue(required.issubset(first[0]))
        self.assertNotIn("CANARY-SARIF-RAW", json.dumps(first))
        broken = dict(first[0])
        broken.pop("impact")
        with self.assertRaisesRegex(security.SecurityError, "finding_invalid"):
            security.validate_finding(broken)

    def test_sarif_fingerprints_are_canonical_and_metadata_is_one_way(self):
        provider_canary = "ATTACKER-PROVIDER-CANARY"
        rule_canary = "ATTACKER-RULE-CANARY"
        sarif = {
            "version": "2.1.0",
            "runs": [{
                "tool": {"driver": {
                    "name": provider_canary,
                    "version": "ATTACKER-VERSION-CANARY",
                    "rules": [{"id": rule_canary}],
                }},
                "results": [{
                    "ruleId": rule_canary,
                    "partialFingerprints": {"z": "last", "a": "first"},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": "app.py"},
                        "region": {"startLine": 1},
                    }}],
                }],
            }],
        }
        first = security.normalize_sarif(sarif)
        reordered = json.loads(json.dumps(sarif))
        reordered["runs"][0]["results"][0]["partialFingerprints"] = {
            "a": "first",
            "z": "last",
        }
        second = security.normalize_sarif(reordered)
        self.assertEqual(first, second)
        persisted = json.dumps(first)
        for canary in (provider_canary, rule_canary, "ATTACKER-VERSION-CANARY"):
            self.assertNotIn(canary, persisted)
        self.assertRegex(first[0]["rule_id"], r"^sarif-rule-[0-9a-f]{16}$")
        self.assertRegex(first[0]["provenance"]["provider"], r"^sarif-provider-[0-9a-f]{16}$")

    def test_diff_seals_unscannable_inputs_and_disables_textconv(self):
        self.init_git()
        (self.root / ".gitattributes").write_text("*.bin diff=evil\n", encoding="utf-8")
        (self.root / "tracked.bin").write_bytes(b"old\x00")
        subprocess.run(
            ["git", "-C", str(self.root), "add", ".gitattributes", "tracked.bin"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "binary"], check=True)
        textconv_log = self.root / "TEXTCONV-RAN"
        textconv = self.root / "textconv.sh"
        textconv.write_text(
            "#!/bin/sh\nprintf invoked > '%s'\nprintf converted\n" % textconv_log,
            encoding="utf-8",
        )
        textconv.chmod(0o700)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "diff.evil.textconv", str(textconv)],
            check=True,
        )
        (self.root / "tracked.bin").write_bytes(b"new\x00")
        (self.root / "oversize.txt").write_bytes(b"x" * (security.MAX_FILE_BYTES + 1))
        (self.root / "linked.txt").symlink_to("app.py")
        (self.root / "unreadable.txt").write_text("changed\n", encoding="utf-8")
        calls = []
        original = security._run_bounded
        original_read = security._read_regular_bytes

        def capture(argv, **kwargs):
            calls.append(argv)
            return original(argv, **kwargs)

        def refuse_one(path, *args, **kwargs):
            if os.path.basename(path) == "unreadable.txt":
                raise OSError("fixture refused")
            return original_read(path, *args, **kwargs)

        with (
            mock.patch.object(security, "_run_bounded", side_effect=capture),
            mock.patch.object(security, "_read_regular_bytes", side_effect=refuse_one),
        ):
            snapshot = security._parse_diff(str(self.root), ".", staged=False)
        self.assertTrue(any("--no-textconv" in argv for argv in calls if "diff" in argv))
        self.assertFalse(textconv_log.exists())
        skipped = {row["path"]: row["reason"] for row in snapshot["skipped"]}
        self.assertEqual(skipped["tracked.bin"], "binary")
        self.assertEqual(skipped["oversize.txt"], "size_limit")
        self.assertEqual(skipped["linked.txt"], "symlink")
        self.assertEqual(skipped["unreadable.txt"], "unreadable")
        result = security.run_scan("diff", str(self.root), which=self.missing)
        self.assertIn("scope_skipped", result["reason_codes"])
        self.assertEqual(result["status"], "incomplete")

    def test_staged_new_binary_is_an_explicit_coverage_gap(self):
        self.init_git()
        (self.root / "new.bin").write_bytes(b"new\x00binary")
        subprocess.run(
            ["git", "-C", str(self.root), "add", "new.bin"],
            check=True,
        )
        snapshot = security._parse_diff(str(self.root), ".", staged=True)
        self.assertIn(
            {"path": "new.bin", "reason": "binary"},
            snapshot["skipped"],
        )

    def test_diff_decodes_quoted_paths_and_inventories_dependency_scope(self):
        self.init_git()
        quoted = self.root / "café.py"
        quoted.write_text("safe\n", encoding="utf-8")
        (self.root / "package-lock.json").write_text(
            '{"lockfileVersion":3}\n',
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.root), "add", "café.py", "package-lock.json"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "scope"], check=True)
        quoted.write_text("CANARY-RAW-RULE-SECRET\n", encoding="utf-8")
        snapshot = security._parse_diff(str(self.root), ".")
        self.assertEqual([row["path"] for row in snapshot["files"]], ["café.py"])
        self.assertEqual(snapshot["skipped"], [])
        dependency_snapshot = security._dependency_snapshot(
            str(self.root),
            ".",
            str(self.root),
            "diff",
            snapshot,
        )
        receipt, findings = security.scan_dependencies(
            str(self.root),
            dependency_snapshot,
            "diff",
            which=self.missing,
        )
        self.assertEqual(receipt["provider"], "osv-scanner")
        self.assertEqual(receipt["status"], "unsupported")
        self.assertEqual(findings, [])

    def test_existing_artifacts_require_private_exact_shape(self):
        first = security.run_scan("scan", str(self.root), which=self.missing)
        artifact_root = self.root / first["artifacts"]["directory"]
        artifact = artifact_root / "SECURITY-COVERAGE.json"
        artifact.chmod(0o644)
        with self.assertRaisesRegex(security.SecurityError, "artifact_conflict"):
            security.run_scan("scan", str(self.root), which=self.missing)
        artifact.chmod(0o600)
        extra = artifact_root / "EXTRA"
        extra.write_text("unexpected\n", encoding="utf-8")
        extra.chmod(0o600)
        with self.assertRaisesRegex(security.SecurityError, "artifact_conflict"):
            security.run_scan("scan", str(self.root), which=self.missing)

    def test_snapshot_records_symlinked_subtrees_as_coverage_gaps(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("not scanned\n", encoding="utf-8")
        (self.root / "linked-dir").symlink_to(outside, target_is_directory=True)
        snapshot = security.snapshot_scope(str(self.root), str(self.root))
        self.assertIn(
            {"path": "linked-dir", "reason": "symlink"},
            snapshot["skipped"],
        )

    def test_osv_v2_manifest_inventory_matches_official_source_inputs(self):
        supported = {
            "conan.lock", "pubspec.lock", "mix.lock", "go.mod",
            "cabal.project.freeze", "stack.yaml.lock",
            "buildscript-gradle.lockfile", "gradle.lockfile",
            "gradle/verification-metadata.xml", "pom.xml", "bun.lock",
            "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "deps.json",
            "packages.config", "packages.lock.json", "composer.lock",
            "Pipfile.lock", "poetry.lock", "requirements.txt", "pdm.lock",
            "pylock.toml", "uv.lock",
            "renv.lock", "Gemfile.lock", "gems.locked", "Cargo.lock",
        }
        unsupported = {
            "package.json", "npm-shrinkwrap.json", "go.sum",
            "requirements-dev.txt",
        }
        snapshot = {
            "files": [
                {"path": path, "size": 1, "sha256": "a" * 64}
                for path in sorted(supported | unsupported)
            ],
        }
        self.assertEqual(set(security._manifest_paths(snapshot)), supported)

    def test_missing_failed_refused_timeout_and_stale_coverage_never_clean(self):
        complete = security.provider_receipt(
            "secrets", "gitleaks", "8.30.1", "complete", "fresh", "none", ".",
        )
        statuses = ("missing", "failed", "refused", "quota_limited", "timeout", "stale")
        for status in statuses:
            dependency = security.provider_receipt(
                "dependencies", "osv-scanner", "", status,
                "stale" if status == "stale" else "unavailable", "none", ".",
            )
            coverage = security.build_coverage(
                "scan_" + "a" * 32,
                "complete",
                [complete, dependency],
                [{"finding_id": "finding_" + "b" * 32}],
            )
            self.assertEqual(coverage["verdict"], "incomplete", status)
            self.assertIn("dependencies", coverage["gaps"])
        not_applicable = security.provider_receipt(
            "dependencies", "manifest-inventory", "1", "not_applicable", "fresh", "none", ".",
        )
        coverage = security.build_coverage(
            "scan_" + "a" * 32,
            "complete",
            [complete, not_applicable],
            [],
        )
        self.assertEqual(coverage["verdict"], "clean")

    def test_provider_execution_error_codes_remain_distinct(self):
        snapshot = security.snapshot_scope(str(self.root), str(self.root))
        for error_code in ("refused", "quota_limited", "timeout", "output_limit"):
            calls = {"count": 0}

            def executor(argv, **_kwargs):
                calls["count"] += 1
                if "--version" in argv:
                    return security.CommandResult(0, b"8.30.1\n", b"")
                return security.CommandResult(1, b"", b"", error_code)

            receipt, findings = security.scan_secrets(
                str(self.root),
                snapshot,
                "scan",
                which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
                command_executor=executor,
            )
            self.assertEqual(receipt["status"], error_code)
            self.assertEqual(receipt["coverage"], "gap")
            self.assertEqual(findings, [])
            self.assertEqual(calls["count"], 2)

        def refused_version(argv, **_kwargs):
            return security.CommandResult(1, b"", b"", "refused")

        receipt, findings = security.scan_secrets(
            str(self.root),
            snapshot,
            "scan",
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=refused_version,
        )
        self.assertEqual(receipt["status"], "refused")
        self.assertEqual(findings, [])

    def test_secret_provider_is_version_aware_redacted_and_content_poor(self):
        canary = "CANARY-RAW-123"
        rule_canary = "CANARY-RAW-RULE-SECRET"
        calls = []

        def executor(argv, **_kwargs):
            calls.append(argv)
            if "--version" in argv:
                return security.CommandResult(0, b"8.30.1\n", b"")
            payload = [{
                "RuleID": rule_canary,
                "Description": "Generic API Key",
                "StartLine": 2,
                "Fingerprint": "src/app.py:generic-api-key:2",
                "Secret": canary,
                "Match": "token=" + canary,
            }]
            return security.CommandResult(1, json.dumps(payload).encode(), b"")

        snapshot = security.snapshot_scope(str(self.root), str(self.root))
        receipt, findings = security.scan_secrets(
            str(self.root),
            snapshot,
            "scan",
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=executor,
        )
        self.assertEqual(receipt["status"], "findings")
        self.assertEqual(len(findings), 1)
        persisted = json.dumps({"receipt": receipt, "findings": findings})
        self.assertNotIn(canary, persisted)
        self.assertNotIn(rule_canary, persisted)
        self.assertRegex(findings[0]["rule_id"], r"^gitleaks-rule-[0-9a-f]{16}$")
        scan_argv = calls[-1]
        self.assertEqual(scan_argv[1], "stdin")
        self.assertIn("--redact=100", scan_argv)
        self.assertIn("--report-path", scan_argv)
        self.assertEqual(scan_argv[scan_argv.index("--report-path") + 1], "-")
        self.assertNotIn("protect", scan_argv)

    def test_gitleaks_findings_exit_requires_a_nonempty_valid_report(self):
        snapshot = security.snapshot_scope(str(self.root), str(self.root))
        for output in (b"", b"{invalid"):
            with self.subTest(output=output):
                def executor(argv, **_kwargs):
                    if "--version" in argv:
                        return security.CommandResult(0, b"8.30.1\n", b"")
                    return security.CommandResult(1, output, b"")

                receipt, findings = security.scan_secrets(
                    str(self.root),
                    snapshot,
                    "scan",
                    which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
                    command_executor=executor,
                )
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(findings, [])

    def test_provider_finding_exit_requires_nonempty_trufflehog_and_osv_reports(self):
        snapshot = security.snapshot_scope(str(self.root), str(self.root))

        def trufflehog_executor(argv, **_kwargs):
            if "--version" in argv:
                return security.CommandResult(0, b"trufflehog 3.90.0\n", b"")
            return security.CommandResult(183, b"", b"")

        receipt, findings = security.scan_secrets(
            str(self.root),
            snapshot,
            "scan",
            which=lambda name: "/mock/trufflehog" if name == "trufflehog" else None,
            command_executor=trufflehog_executor,
        )
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(findings, [])

        def malformed_trufflehog_executor(argv, **_kwargs):
            if "--version" in argv:
                return security.CommandResult(0, b"trufflehog 3.90.0\n", b"")
            return security.CommandResult(183, b"[]\n", b"")

        receipt, findings = security.scan_secrets(
            str(self.root),
            snapshot,
            "scan",
            which=lambda name: "/mock/trufflehog" if name == "trufflehog" else None,
            command_executor=malformed_trufflehog_executor,
        )
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(findings, [])

        (self.root / "package-lock.json").write_text(
            '{"lockfileVersion":3}',
            encoding="utf-8",
        )
        snapshot = security.snapshot_scope(str(self.root), str(self.root))

        def osv_executor(argv, **_kwargs):
            if "--version" in argv:
                return security.CommandResult(0, b"osv-scanner version 2.3.1\n", b"")
            return security.CommandResult(1, b'{"results":[]}', b"")

        with mock.patch.dict(os.environ, {
            "KIMIFLOW_OSV_OFFLINE": "1",
            "KIMIFLOW_OSV_OFFLINE_FRESH_UNTIL": "2026-07-29T00:00:00Z",
        }):
            receipt, findings = security.scan_dependencies(
                str(self.root),
                snapshot,
                which=lambda name: "/mock/osv-scanner" if name == "osv-scanner" else None,
                command_executor=osv_executor,
                now=datetime(2026, 7, 28, tzinfo=timezone.utc),
            )
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(findings, [])

    def test_osv_requires_a_complete_result_shape(self):
        (self.root / "package-lock.json").write_text(
            '{"lockfileVersion":3}',
            encoding="utf-8",
        )
        snapshot = security.snapshot_scope(str(self.root), str(self.root))
        outputs = (
            b"",
            b"{}",
            b'{"results":[{"packages":[{}]}]}',
        )
        with mock.patch.dict(os.environ, {
            "KIMIFLOW_OSV_OFFLINE": "1",
            "KIMIFLOW_OSV_OFFLINE_FRESH_UNTIL": "2026-07-29T00:00:00Z",
        }):
            for output in outputs:
                with self.subTest(output=output):
                    def executor(argv, **_kwargs):
                        if "--version" in argv:
                            return security.CommandResult(0, b"osv-scanner 2.3.1\n", b"")
                        return security.CommandResult(0, output, b"")

                    receipt, findings = security.scan_dependencies(
                        str(self.root),
                        snapshot,
                        "scan",
                        which=lambda name: "/mock/osv-scanner" if name == "osv-scanner" else None,
                        command_executor=executor,
                        now=datetime(2026, 7, 28, tzinfo=timezone.utc),
                    )
                    self.assertEqual(receipt["status"], "failed")
                    self.assertEqual(findings, [])

    def test_trufflehog_locations_use_the_synthetic_line_map(self):
        payload = json.dumps({
            "DetectorName": "fixture",
            "SourceID": "source-1",
            "SourceMetadata": {"Data": {"Filesystem": {
                "file": "stdin",
                "line": 3,
            }}},
        }).encode("utf-8")
        findings = security._trufflehog_findings(
            payload,
            "3.90.0",
            {3: ("src/app.py", 42)},
        )
        occurrence = findings[0]["occurrences"][0]
        self.assertEqual((occurrence["path"], occurrence["start_line"]), ("src/app.py", 42))

    def test_sarif_and_optional_osv_do_not_install_or_contact_private_registries(self):
        (self.root / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
        snapshot = security.snapshot_scope(str(self.root), str(self.root))
        calls = []

        def executor(argv, **_kwargs):
            calls.append(argv)
            return security.CommandResult(0, b"{}", b"")

        receipt, findings = security.scan_dependencies(
            str(self.root),
            snapshot,
            which=self.missing,
            command_executor=executor,
        )
        self.assertEqual(receipt["status"], "missing")
        self.assertEqual(findings, [])
        self.assertEqual(calls, [])
        self.assertNotIn("install", json.dumps(receipt))
        sarif = {
            "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": "fixture", "rules": []}}, "results": []}],
        }
        self.assertEqual(security.normalize_sarif(sarif), [])

        calls.clear()

        def offline_executor(argv, **_kwargs):
            calls.append(argv)
            if "--version" in argv:
                return security.CommandResult(0, b"osv-scanner version 2.3.1\n", b"")
            payload = {
                "results": [{
                    "source": {"path": str(self.root / "package-lock.json"), "type": "lockfile"},
                    "packages": [{
                        "package": {"name": "fixture", "version": "1.0.0", "ecosystem": "npm"},
                        "vulnerabilities": [{"id": "OSV-2026-1"}],
                    }],
                }],
            }
            return security.CommandResult(1, json.dumps(payload).encode(), b"")

        with mock.patch.dict(os.environ, {
            "KIMIFLOW_OSV_OFFLINE": "1",
            "KIMIFLOW_OSV_OFFLINE_FRESH_UNTIL": "2026-07-29T00:00:00Z",
        }):
            receipt, findings = security.scan_dependencies(
                str(self.root),
                snapshot,
                which=lambda name: "/mock/osv-scanner" if name == "osv-scanner" else None,
                command_executor=offline_executor,
                now=datetime(2026, 7, 28, tzinfo=timezone.utc),
            )
        self.assertEqual(receipt["status"], "findings")
        self.assertEqual(findings[0]["rule_id"], "OSV-2026-1")
        scan_argv = calls[-1]
        self.assertIn("--offline", scan_argv)
        self.assertIn("--offline-vulnerabilities", scan_argv)
        self.assertIn("--no-resolve", scan_argv)
        self.assertEqual(scan_argv.count("-L"), 1)
        self.assertNotIn("install", scan_argv)
        self.assertNotIn("registry", " ".join(scan_argv))

        calls.clear()
        receipt, findings = security.scan_dependencies(
            str(self.root),
            snapshot,
            "diff",
            which=lambda name: "/mock/osv-scanner",
            command_executor=offline_executor,
        )
        self.assertEqual(receipt["status"], "unsupported")
        self.assertEqual(findings, [])
        self.assertEqual(calls, [])

    def test_external_validation_requires_matching_unexpired_identity_free_receipt(self):
        now = datetime(2026, 7, 28, tzinfo=timezone.utc)
        base = {
            "schema_version": 1,
            "action": "external_validation",
            "provider": "fixture",
            "scope_digest": "sha256:" + "a" * 64,
            "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
            "nonce": "nonce-12345678",
        }
        valid = security.validate_authorization(
            base, "fixture", base["scope_digest"], "external_validation", now=now,
        )
        self.assertRegex(valid, r"^sha256:[0-9a-f]{64}$")
        expired = dict(base)
        expired["expires_at"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        with self.assertRaisesRegex(security.SecurityError, "authorization_expired"):
            security.validate_authorization(
                expired, "fixture", base["scope_digest"], "external_validation", now=now,
            )
        wrong = dict(base, scope_digest="sha256:" + "b" * 64)
        with self.assertRaisesRegex(security.SecurityError, "authorization_scope_mismatch"):
            security.validate_authorization(
                wrong, "fixture", base["scope_digest"], "external_validation", now=now,
            )
        with_identity = dict(base, account="private@example.invalid")
        with self.assertRaisesRegex(security.SecurityError, "authorization_invalid"):
            security.validate_authorization(
                with_identity, "fixture", base["scope_digest"], "external_validation", now=now,
            )

    def test_authorization_receipt_is_bound_into_scan_identity(self):
        now = datetime(2026, 7, 28, tzinfo=timezone.utc)
        context = security.security_context(str(self.root), ".")
        scope_digest = security.digest({
            "mode": "scan",
            "scope": ".",
            "guidance_digest": context["guidance_digest"],
            "policy_digest": context["policy_digest"],
        })
        paths = []
        for index in (1, 2):
            path = self.root / ("authorization-%d.json" % index)
            path.write_text(json.dumps({
                "schema_version": 1,
                "action": "external_validation",
                "provider": "fixture",
                "scope_digest": scope_digest,
                "expires_at": "2026-07-28T01:00:00Z",
                "nonce": "nonce-%08d" % index,
            }), encoding="utf-8")
            paths.append(path)
        with mock.patch.object(
            security,
            "_write_scan_artifacts",
            return_value={"directory": ".kimiflow/security/scans/test"},
        ):
            scans = [
                security.run_scan(
                    "scan",
                    str(self.root),
                    authorization_path=str(path),
                    now=now,
                    which=self.missing,
                )
                for path in paths
            ]
        self.assertNotEqual(scans[0]["scan_id"], scans[1]["scan_id"])

    def test_accept_exactly_one_current_finding_as_schema5_fix_child(self):
        sarif_path = self.root / "fixture.sarif"
        sarif_path.write_text(json.dumps({
            "version": "2.1.0",
            "runs": [{
                "tool": {"driver": {"name": "fixture", "rules": [{"id": "RULE-1"}]}},
                "results": [{
                    "ruleId": "RULE-1",
                    "message": {"text": "finding"},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": "app.py"},
                        "region": {"startLine": 1},
                    }}],
                }],
            }],
        }), encoding="utf-8")
        result = security.run_scan(
            "scan", str(self.root), sarif_paths=[str(sarif_path)], which=self.missing,
        )
        findings = security.load_scan_artifact(
            str(self.root), result["scan_id"], "SECURITY-FINDINGS.json",
        )["findings"]
        accepted = security.accept_finding(str(self.root), result["scan_id"], findings[0]["finding_id"])
        self.assertEqual(accepted["status"], "fix_child_ready")
        self.assertEqual(accepted["child_contract"]["flow_schema"], 5)
        self.assertEqual(accepted["child_contract"]["mode"], "fix")
        self.assertEqual(
            accepted["child_contract"]["required_phases"],
            ["plan", "build", "verify", "conformance", "code_review"],
        )
        self.assertRegex(accepted["child_run"], r"^\.kimiflow/security-fix-[0-9a-f]{16}$")
        child = self.root / accepted["child_run"]
        self.assertTrue(child.is_dir())
        self.assertEqual(stat.S_IMODE(child.stat().st_mode), 0o700)
        self.assertEqual(
            {path.name for path in child.iterdir()},
            {"PROBLEM.md", "STATE.md", "SECURITY-PARENT.json"},
        )
        for path in child.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIn("Flow schema: 5", (child / "STATE.md").read_text(encoding="utf-8"))
        self.assertIn("Mode: fix", (child / "STATE.md").read_text(encoding="utf-8"))
        self.assertIn("Phase reads required: yes", (child / "STATE.md").read_text(encoding="utf-8"))
        self.assertIn("Review epoch start: 1", (child / "STATE.md").read_text(encoding="utf-8"))
        self.assertIn("Status: backlog", (child / "STATE.md").read_text(encoding="utf-8"))
        self.assertIn("Review epoch cap: 2", (child / "STATE.md").read_text(encoding="utf-8"))
        transition = flow_graph.resolve_transition(
            str(child),
            active={},
            stale={"risk": "current"},
            item_counts={"open": 0},
        )
        self.assertEqual(transition["graph_status"], "ready")
        self.assertEqual(transition["current_node"], "phase_0")
        self.assertEqual(transition["action"], "run_phase")
        launcher = launcher_status.build_snapshot(
            str(self.root),
            str(Path(__file__).resolve().parents[2]),
        )
        self.assertEqual(launcher["runs"]["backlog"], 1)
        self.assertEqual(launcher["launcher"]["primary_action"]["id"], "resume_backlog")
        parent = json.loads((child / "SECURITY-PARENT.json").read_text(encoding="utf-8"))
        self.assertEqual(parent["parent_receipt_digest"], security.digest(accepted))
        with self.assertRaisesRegex(security.SecurityError, "acceptance_conflict"):
            security.accept_finding(str(self.root), result["scan_id"], findings[0]["finding_id"])

    def test_concurrent_acceptance_transaction_allows_only_one_open_finding(self):
        sarif_path = self.root / "concurrent.sarif"
        sarif_path.write_text(json.dumps({
            "version": "2.1.0",
            "runs": [{
                "tool": {"driver": {
                    "name": "fixture",
                    "version": "1",
                    "rules": [{"id": "RULE-1"}, {"id": "RULE-2"}],
                }},
                "results": [
                    {
                        "ruleId": rule_id,
                        "locations": [{"physicalLocation": {
                            "artifactLocation": {"uri": "app.py"},
                            "region": {"startLine": index},
                        }}],
                    }
                    for index, rule_id in enumerate(("RULE-1", "RULE-2"), 1)
                ],
            }],
        }), encoding="utf-8")
        scan = security.run_scan(
            "scan",
            str(self.root),
            sarif_paths=[str(sarif_path)],
            which=self.missing,
        )
        finding_ids = [
            row["finding_id"]
            for row in security.load_scan_artifact(
                str(self.root),
                scan["scan_id"],
                "SECURITY-FINDINGS.json",
            )["findings"]
        ]
        original_write = security._state_write
        entered = 0
        entered_lock = threading.Lock()
        both_entered = threading.Event()

        def delayed_write(root, subdir, name, payload):
            nonlocal entered
            if subdir == "acceptances":
                with entered_lock:
                    entered += 1
                    if entered == 2:
                        both_entered.set()
                both_entered.wait(0.5)
            return original_write(root, subdir, name, payload)

        outcomes = []

        def accept(finding_id):
            try:
                outcomes.append(
                    ("accepted", security.accept_finding(str(self.root), scan["scan_id"], finding_id))
                )
            except security.SecurityError as exc:
                outcomes.append(("error", exc.code))

        with mock.patch.object(security, "_state_write", side_effect=delayed_write):
            threads = [
                threading.Thread(target=accept, args=(finding_id,))
                for finding_id in finding_ids
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual([kind for kind, _value in outcomes].count("accepted"), 1)
        self.assertEqual(
            [value for kind, value in outcomes if kind == "error"],
            ["acceptance_conflict"],
        )
        self.assertEqual(len(security._open_acceptances(str(self.root))), 1)
        lock_path = self.root / ".kimiflow" / "security" / "locks" / "acceptance.lock"
        self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_state_reads_refuse_symlinked_scan_directories(self):
        result = security.run_scan("scan", str(self.root), which=self.missing)
        scans = self.root / ".kimiflow" / "security" / "scans"
        original = scans / result["scan_id"]
        saved = scans / (result["scan_id"] + "-saved")
        original.rename(saved)
        original.symlink_to(saved, target_is_directory=True)
        with self.assertRaisesRegex(security.SecurityError, "unsafe_state_path"):
            security.load_scan_artifact(
                str(self.root),
                result["scan_id"],
                "SECURITY-SCAN-MANIFEST.json",
            )

    def test_closure_requires_all_controls_and_current_rescan_evidence(self):
        first = self._scan_with_finding()
        finding_id = security.load_scan_artifact(
            str(self.root), first["scan_id"], "SECURITY-FINDINGS.json",
        )["findings"][0]["finding_id"]
        accepted = security.accept_finding(str(self.root), first["scan_id"], finding_id)
        rescan = security.run_scan("scan", str(self.root), which=self.missing)
        child = self.root / accepted["child_run"]
        rogue = self.root / ".kimiflow" / "security-fix-rogue"
        rogue.mkdir()
        with self.assertRaisesRegex(security.SecurityError, "child_run_mismatch"):
            security.close_finding(
                str(self.root), accepted["acceptance_id"], str(rogue),
            )
        (child / "STATE.md").write_text(
            "Flow schema: 5\nMode: fix\nScope: small\nStatus: done\nPhase reads required: yes\n"
            "Conformance contract: 1\nConvergence contract: 1\n"
            "Review gate: code\nReview epoch: 1\n"
            "Review epoch start: 1\nReview epoch cap: 2\n"
            "Conformance basis: " + "a" * 64 + "\n"
            + "".join("Phase %d: done\n" % phase for phase in range(8)),
            encoding="utf-8",
        )
        evaluation = {
            "schema_version": 1,
            "id": "out_" + "b" * 64,
            "run": accepted["child_run"],
            "evaluated_at": "2026-07-28T00:00:00Z",
            "terminal": "done",
            "classification": "verified_success",
            "promotable": True,
            "mode": "fix",
            "scope": "small",
        }
        evaluation_summary = {
            "status": "evaluated",
            "id": evaluation["id"],
            "terminal": "done",
            "classification": "verified_success",
            "promotable": True,
            "strategy_recall_hits": 0,
            "strategy_recall_used": False,
            "first_plan_success": True,
            "economics_result": None,
            "net_estimated_tokens_saved": None,
        }
        (child / "OUTCOME-EVALUATION.json").write_text(
            json.dumps(evaluation),
            encoding="utf-8",
        )
        (child / "SESSION-OUTCOME.json").write_text(
            json.dumps({
                "schema_version": 1,
                "outcome": "done",
                "reason": None,
                "completed_at": "2026-07-28T00:00:01Z",
                "learning_review": {
                    "schema_version": 1,
                    "status": "recorded",
                    "run": accepted["child_run"],
                    "review_path": accepted["child_run"] + "/LEARNING-REVIEW.md",
                    "written": True,
                    "entries": [],
                },
                "learning_verify": (
                    "LEARNING_REVIEW\tOPEN\tstatus=recorded\tfreshness=current\tpath="
                    + accepted["child_run"] + "/LEARNING-REVIEW.md"
                ),
                "outcome_evaluation": evaluation_summary,
                "memory_curation": None,
            }),
            encoding="utf-8",
        )
        (child / "VERIFICATION.md").write_text(
            "<!-- kimiflow:verification outcome=passed "
            "criteria=passed regression=passed -->\n",
            encoding="utf-8",
        )
        for entry in phase_reads.load_manifest(str(self.root)):
            phase_reads.record_read(
                str(self.root),
                str(child),
                entry["id"],
                entry["file"],
                "2026-07-28T00:00:00Z",
                write=True,
            )
        findings_dir = child / "findings"
        findings_dir.mkdir()
        (findings_dir / "r1-code-verified.md").write_text("NONE\n", encoding="utf-8")
        saturation_dir = child / "review-saturation"
        saturation_dir.mkdir()
        review_axes = [
            "spec-correctness",
            "failure-security",
            "standards-integration",
        ]
        (saturation_dir / "r1.json").write_text(
            json.dumps({"schema_version": 2, "round": 1, "axes": review_axes}),
            encoding="utf-8",
        )
        coverage = security.load_scan_artifact(
            str(self.root), rescan["scan_id"], "SECURITY-COVERAGE.json",
        )
        evidence = {
            "schema_version": 1,
            "acceptance_id": accepted["acceptance_id"],
            "scan_id": first["scan_id"],
            "finding_id": finding_id,
            "scope_digest": accepted["scope_digest"],
            "original_reproduction": "negative",
            "regression": "passed",
            "legitimate_behavior": "passed",
            "bypass": "passed",
            "rescan_scan_id": rescan["scan_id"],
            "rescan_coverage_digest": security.digest(coverage),
        }
        (child / "SECURITY-CLOSURE-EVIDENCE.json").write_text(json.dumps(evidence), encoding="utf-8")
        with self.assertRaisesRegex(security.SecurityError, "child_lifecycle_invalid"):
            security.close_finding(
                str(self.root), accepted["acceptance_id"], str(child),
            )

        gate_calls = []

        def gate_executor(executor, argv, **kwargs):
            self.assertIsNone(executor)
            gate_calls.append((argv, kwargs))
            if argv[0].endswith("conformance-gate.sh"):
                return security.CommandResult(
                    0,
                    (
                        "CONFORMANCE_GATE\tOPEN\tblockers=0\treason=clean\t"
                        "detail=basis=%s\n" % ("a" * 64)
                    ).encode("utf-8"),
                    b"",
                )
            return security.CommandResult(0, b"OPEN\t0\tclean\t\n", b"")

        with self.assertRaisesRegex(security.SecurityError, "closure_incomplete"):
            with mock.patch.object(security, "_execute", side_effect=gate_executor):
                security.close_finding(
                    str(self.root),
                    accepted["acceptance_id"],
                    str(child),
                )
        repo = Path(__file__).resolve().parents[3]
        real_root = os.path.realpath(self.root)
        real_child = os.path.realpath(child)
        self.assertEqual(gate_calls[0][0], [
            str(repo / "hooks" / "conformance-gate.sh"),
            real_child,
            "--finish",
        ])
        self.assertEqual(gate_calls[1][0], [
            str(repo / "hooks" / "resolve-review-gate.sh"),
            os.path.join(real_child, "findings"),
            "--round",
            "1",
            "--expect",
            "code-verified",
            "--finding-contract",
            "1",
            "--review-axes",
            ",".join(review_axes),
            "--gate",
            "code",
            "--epoch-start",
            "1",
            "--cap",
            "2",
        ])
        for _argv, kwargs in gate_calls:
            self.assertEqual(kwargs["cwd"], real_root)
            self.assertLessEqual(kwargs["max_output"], 65536)

        def clean_executor(argv, **_kwargs):
            if "--version" in argv:
                return security.CommandResult(0, b"8.30.1\n", b"")
            return security.CommandResult(0, b"[]", b"")

        clean_rescan = security.run_scan(
            "scan",
            str(self.root),
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=clean_executor,
        )
        coverage = security.load_scan_artifact(
            str(self.root), clean_rescan["scan_id"], "SECURITY-COVERAGE.json",
        )
        self.assertEqual(coverage["verdict"], "clean")
        evidence["rescan_scan_id"] = clean_rescan["scan_id"]
        evidence["rescan_coverage_digest"] = security.digest(coverage)
        (child / "SECURITY-CLOSURE-EVIDENCE.json").write_text(json.dumps(evidence), encoding="utf-8")
        with self.assertRaisesRegex(security.SecurityError, "closure_provider_mismatch"):
            with mock.patch.object(security, "_execute", side_effect=gate_executor):
                security.close_finding(
                    str(self.root),
                    accepted["acceptance_id"],
                    str(child),
                )

        sarif_path = self.root / "finding.sarif"
        sarif = json.loads(sarif_path.read_text(encoding="utf-8"))
        sarif["runs"][0]["results"] = []
        sarif_path.write_text(json.dumps(sarif), encoding="utf-8")
        equivalent_rescan = security.run_scan(
            "scan",
            str(self.root),
            sarif_paths=[str(sarif_path)],
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=clean_executor,
        )
        coverage = security.load_scan_artifact(
            str(self.root),
            equivalent_rescan["scan_id"],
            "SECURITY-COVERAGE.json",
        )
        self.assertEqual(coverage["verdict"], "clean")
        evidence["rescan_scan_id"] = equivalent_rescan["scan_id"]
        evidence["rescan_coverage_digest"] = security.digest(coverage)
        (child / "SECURITY-CLOSURE-EVIDENCE.json").write_text(json.dumps(evidence), encoding="utf-8")
        with self.assertRaisesRegex(security.SecurityError, "closure_provider_mismatch"):
            with mock.patch.object(security, "_execute", side_effect=gate_executor):
                security.close_finding(
                    str(self.root),
                    accepted["acceptance_id"],
                    str(child),
                )

        sarif["runs"][0]["invocations"] = [{"executionSuccessful": True}]
        sarif_path.write_text(json.dumps(sarif), encoding="utf-8")
        equivalent_rescan = security.run_scan(
            "scan",
            str(self.root),
            sarif_paths=[str(sarif_path)],
            which=lambda name: "/mock/gitleaks" if name == "gitleaks" else None,
            command_executor=clean_executor,
        )
        coverage = security.load_scan_artifact(
            str(self.root),
            equivalent_rescan["scan_id"],
            "SECURITY-COVERAGE.json",
        )
        evidence["rescan_scan_id"] = equivalent_rescan["scan_id"]
        evidence["rescan_coverage_digest"] = security.digest(coverage)
        (child / "SECURITY-CLOSURE-EVIDENCE.json").write_text(json.dumps(evidence), encoding="utf-8")
        with mock.patch.object(security, "_execute", side_effect=gate_executor):
            closed = security.close_finding(
                str(self.root), accepted["acceptance_id"], str(child),
            )
        self.assertEqual(closed["status"], "closed")
        self.assertRegex(closed["child_evidence_digest"], r"^sha256:[0-9a-f]{64}$")

    def _scan_with_finding(self):
        security_dir = self.root / ".kimiflow" / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        (security_dir / "GUIDANCE.json").write_text(json.dumps({
            "schema_version": 1,
            "scopes": [{
                "path": ".",
                "assets": ["project-data"],
                "entry_points": ["app.py"],
                "untrusted_inputs": ["file-content"],
                "data_flows": ["file-to-scan"],
                "trust_boundaries": ["project-root"],
                "auth_assumptions": ["none"],
                "privileged_actions": ["none"],
                "security_invariants": ["no-secret-output"],
            }],
        }), encoding="utf-8")
        sarif_path = self.root / "finding.sarif"
        sarif_path.write_text(json.dumps({
            "version": "2.1.0",
            "runs": [{
                "tool": {"driver": {"name": "fixture", "rules": [{"id": "RULE-1"}]}},
                "results": [{
                    "ruleId": "RULE-1",
                    "message": {"text": "finding"},
                    "locations": [{"physicalLocation": {
                        "artifactLocation": {"uri": "app.py"},
                        "region": {"startLine": 1},
                    }}],
                }],
            }],
        }), encoding="utf-8")
        return security.run_scan(
            "scan", str(self.root), sarif_paths=[str(sarif_path)], which=self.missing,
        )

    def test_one_provider_per_lane_zero_model_calls_and_host_smoke_contract(self):
        calls = []

        def executor(argv, **_kwargs):
            calls.append(argv)
            if "--version" in argv:
                return security.CommandResult(0, b"8.30.1\n", b"")
            return security.CommandResult(0, b"[]", b"")

        result = security.run_scan(
            "scan",
            str(self.root),
            which=lambda name: "/mock/" + name if name in ("gitleaks", "trufflehog") else None,
            command_executor=executor,
        )
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(sum(argv[0].endswith("gitleaks") and "--version" not in argv for argv in calls), 1)
        self.assertEqual(sum(argv[0].endswith("trufflehog") for argv in calls), 0)
        repo = Path(__file__).resolve().parents[3]
        canonical = (repo / "SKILL.md").read_text(encoding="utf-8")
        codex = (repo / "skills" / "kimiflow" / "SKILL.md").read_text(encoding="utf-8")
        canonical_source = (repo / "docs" / "render" / "kimiflow" / "canonical" / "SKILL.md").read_text(
            encoding="utf-8",
        )
        codex_source = (repo / "docs" / "render" / "kimiflow" / "overlays" / "codex.md").read_text(
            encoding="utf-8",
        )
        self.assertEqual(canonical, canonical_source)
        self.assertEqual(codex, codex_source)
        self.assertLessEqual(len(canonical.encode("utf-8")), 17000)
        self.assertLessEqual(len(codex.encode("utf-8")), 15000)
        reference = (repo / "reference.md").read_text(encoding="utf-8")
        for text in (canonical, codex, reference):
            self.assertIn("security scan", text)
            self.assertIn("security diff", text)
            self.assertIn("local", text.lower())
        for schema in (
            "security-scan-manifest-v1.schema.json",
            "security-coverage-v1.schema.json",
            "security-findings-v1.schema.json",
            "security-report-v1.schema.json",
        ):
            payload = json.loads((repo / "references" / schema).read_text(encoding="utf-8"))
            self.assertEqual(payload["$schema"], "https://json-schema.org/draft/2020-12/schema")
            if schema == "security-coverage-v1.schema.json":
                constraints = json.dumps(payload.get("allOf", []), sort_keys=True)
                self.assertIn('"contains"', constraints)
                self.assertIn('"minItems": 1', constraints)


if __name__ == "__main__":
    unittest.main()
