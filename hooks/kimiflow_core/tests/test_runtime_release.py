import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock
import zipfile

from kimiflow_core import runtime_release


SOURCE_COMMIT = "a" * 40


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def make_candidate(root):
    files = {
        ".claude-plugin/plugin.json": (0o644, json_bytes({"name": "kimiflow", "version": "1.2.3"})),
        ".codex-plugin/plugin.json": (0o644, json_bytes({"name": "kimiflow", "version": "1.2.3"})),
        "SKILL.md": (0o644, b"# Kimiflow\n"),
        "hooks/active-run.sh": (0o755, b"#!/usr/bin/env bash\nexit 0\n"),
        "hooks/hooks.json": (0o644, b"{}\n"),
        "hooks/run.sh": (0o755, b"#!/usr/bin/env bash\nexit 0\n"),
        "phases/PHASES.json": (0o644, b"{}\n"),
        "reference.md": (0o644, b"# Reference\n"),
        "references/adapter-protocol-v1.schema.json": (0o644, b'{"schema":1}\n'),
    }
    rows = []
    digest = hashlib.sha256()
    for rel in sorted(files):
        mode, payload = files[rel]
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(payload)
        os.chmod(path, mode)
        mode_text = "%04o" % mode
        rows.append(
            {
                "path": rel,
                "mode": mode_text,
                "bytes": len(payload),
                "sha256": "sha256:%s" % hashlib.sha256(payload).hexdigest(),
            }
        )
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(mode_text.encode("ascii") + b"\0")
        digest.update(str(len(payload)).encode("ascii") + b"\0" + payload + b"\0")
    fingerprint = {
        "schema_version": 1,
        "runtime_fingerprint": "sha256:%s" % digest.hexdigest(),
        "file_count": len(rows),
        "files": rows,
    }
    path = os.path.join(root, runtime_release.RUNTIME_MANIFEST)
    with open(path, "wb") as handle:
        handle.write(json_bytes(fingerprint))
    os.chmod(path, 0o644)
    return root


class FakeResponse:
    def __init__(self, url, value):
        self.url = url
        self.payload = json.dumps(value).encode("utf-8")

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.payload[:limit]


class RuntimeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.candidate = make_candidate(os.path.join(self.temp.name, "candidate"))
        self.output_a = os.path.join(self.temp.name, "a")
        self.output_b = os.path.join(self.temp.name, "b")

    def build(self, output=None):
        return runtime_release.build(self.candidate, output or self.output_a, SOURCE_COMMIT)

    def rewrite_archive(self, manifest_path, archive_path, mutate):
        with zipfile.ZipFile(archive_path) as bundle:
            fingerprint = json.loads(
                bundle.read("kimiflow/" + runtime_release.RUNTIME_MANIFEST).decode("utf-8")
            )
            files = {
                row["path"]: (
                    row["mode"],
                    bundle.read("kimiflow/" + row["path"]),
                )
                for row in fingerprint["files"]
            }
        mutate(fingerprint, files)
        digest = hashlib.sha256()
        rows = []
        for rel in sorted(files):
            mode, payload = files[rel]
            row = {
                "path": rel,
                "mode": mode,
                "bytes": len(payload),
                "sha256": runtime_release._sha256(payload),
            }
            rows.append(row)
            digest.update(rel.encode("utf-8") + b"\0")
            digest.update(mode.encode("ascii") + b"\0")
            digest.update(str(len(payload)).encode("ascii") + b"\0" + payload + b"\0")
        fingerprint["files"] = rows
        fingerprint["file_count"] = len(rows)
        fingerprint["runtime_fingerprint"] = "sha256:%s" % digest.hexdigest()
        directories = {"kimiflow/"}
        for rel in files:
            current = "kimiflow"
            for part in rel.split("/")[:-1]:
                current += "/" + part
                directories.add(current + "/")
        with zipfile.ZipFile(archive_path, "w", allowZip64=False) as bundle:
            for directory in sorted(directories):
                bundle.writestr(
                    runtime_release._zip_info(directory, 0o755, directory=True), b""
                )
            entries = [
                ("kimiflow/" + rel, int(mode, 8), payload)
                for rel, (mode, payload) in files.items()
            ]
            entries.append(
                (
                    "kimiflow/" + runtime_release.RUNTIME_MANIFEST,
                    0o644,
                    json_bytes(fingerprint),
                )
            )
            for name, mode, payload in sorted(entries):
                bundle.writestr(runtime_release._zip_info(name, mode), payload)
        manifest, _ = runtime_release._read_json(manifest_path, "test manifest")
        with open(archive_path, "rb") as handle:
            archive_payload = handle.read()
        manifest["artifact"]["bytes"] = len(archive_payload)
        manifest["artifact"]["sha256"] = runtime_release._sha256(archive_payload)
        manifest["artifact"]["runtime_fingerprint"] = fingerprint["runtime_fingerprint"]
        with open(manifest_path, "wb") as handle:
            handle.write(json_bytes(manifest))

    def rewrite_entry_mode(self, manifest_path, archive_path, target, target_mode):
        with zipfile.ZipFile(archive_path) as bundle:
            entries = [
                (
                    info.filename,
                    (info.external_attr >> 16) & 0o177777,
                    bundle.read(info),
                    info.filename.endswith("/"),
                )
                for info in bundle.infolist()
            ]
        rewritten = archive_path + ".rewritten"
        with zipfile.ZipFile(rewritten, "w", allowZip64=False) as bundle:
            for name, mode, payload, directory in entries:
                bundle.writestr(
                    runtime_release._zip_info(
                        name,
                        target_mode if name == target else mode & 0o777,
                        directory=directory,
                    ),
                    payload,
                )
        os.replace(rewritten, archive_path)
        manifest, _ = runtime_release._read_json(manifest_path, "test manifest")
        with open(archive_path, "rb") as handle:
            archive_payload = handle.read()
        manifest["artifact"]["bytes"] = len(archive_payload)
        manifest["artifact"]["sha256"] = runtime_release._sha256(archive_payload)
        with open(manifest_path, "wb") as handle:
            handle.write(json_bytes(manifest))

    def test_build_is_reproducible_and_candidate_exact(self):
        manifest_a, manifest_path_a, archive_a = self.build(self.output_a)
        for base, dirs, names in os.walk(self.candidate):
            os.chmod(base, 0o700)
            for name in names:
                path = os.path.join(base, name)
                if not os.lstat(path).st_mode & 0o111:
                    os.chmod(path, 0o600)
        manifest_b, manifest_path_b, archive_b = self.build(self.output_b)
        with open(manifest_path_a, "rb") as handle:
            manifest_bytes_a = handle.read()
        with open(manifest_path_b, "rb") as handle:
            manifest_bytes_b = handle.read()
        with open(archive_a, "rb") as handle:
            archive_bytes_a = handle.read()
        with open(archive_b, "rb") as handle:
            archive_bytes_b = handle.read()
        self.assertEqual(manifest_bytes_a, manifest_bytes_b)
        self.assertEqual(archive_bytes_a, archive_bytes_b)
        self.assertEqual(manifest_a, manifest_b)
        with zipfile.ZipFile(archive_a) as archive:
            for info in archive.infolist():
                mode = (info.external_attr >> 16) & 0o777
                if info.filename.endswith("/"):
                    self.assertEqual(mode, 0o755)
                elif info.filename.endswith(("hooks/run.sh", "hooks/active-run.sh")):
                    self.assertEqual(mode, 0o755)
                else:
                    self.assertEqual(mode, 0o644)
                self.assertEqual(info.date_time, runtime_release.ZIP_TIMESTAMP)

    def test_manifest_separates_product_protocol_and_artifact_contract(self):
        manifest, _, archive = self.build()
        self.assertEqual(manifest["product"], "kimiflow")
        self.assertEqual(manifest["version"], "1.2.3")
        self.assertEqual(manifest["source"], {"commit": SOURCE_COMMIT})
        self.assertEqual(manifest["contracts"]["runtime_manifest"], 1)
        self.assertEqual(
            manifest["contracts"]["adapter_protocol"], {"min": 1, "max": 1}
        )
        self.assertEqual(
            manifest["contracts"]["host_profiles"]["embedded"]["required_features"], []
        )
        self.assertEqual(
            manifest["contracts"]["features"], runtime_release.ADVERTISED_FEATURES
        )
        self.assertEqual(
            manifest["contracts"]["host_profiles"]["app_host"]["required_features"],
            runtime_release.HOST_PROFILES["app_host"],
        )
        with open(archive, "rb") as handle:
            archive_payload = handle.read()
        self.assertEqual(manifest["artifact"]["bytes"], len(archive_payload))
        self.assertEqual(manifest["artifact"]["sha256"], runtime_release._sha256(archive_payload))

    def test_build_budget_includes_archived_fingerprint(self):
        fingerprint, _ = runtime_release._read_json(
            os.path.join(self.candidate, runtime_release.RUNTIME_MANIFEST),
            "test fingerprint",
        )
        payload_total = sum(row["bytes"] for row in fingerprint["files"])
        previous = runtime_release.MAX_TOTAL_BYTES
        runtime_release.MAX_TOTAL_BYTES = payload_total
        try:
            with self.assertRaisesRegex(runtime_release.ReleaseError, "size budget"):
                self.build()
        finally:
            runtime_release.MAX_TOTAL_BYTES = previous

    def test_build_rejects_archive_overhead_beyond_artifact_budget(self):
        fingerprint, fingerprint_payload = runtime_release._read_json(
            os.path.join(self.candidate, runtime_release.RUNTIME_MANIFEST),
            "test fingerprint",
        )
        candidate_payload = sum(row["bytes"] for row in fingerprint["files"])
        previous = runtime_release.MAX_TOTAL_BYTES
        runtime_release.MAX_TOTAL_BYTES = candidate_payload + len(fingerprint_payload)
        try:
            with self.assertRaisesRegex(runtime_release.ReleaseError, "archive exceeds"):
                self.build()
        finally:
            runtime_release.MAX_TOTAL_BYTES = previous

    def test_build_budget_includes_synthesized_archive_entries(self):
        fingerprint, _ = runtime_release._read_json(
            os.path.join(self.candidate, runtime_release.RUNTIME_MANIFEST),
            "test fingerprint",
        )
        previous = runtime_release.MAX_ENTRIES
        runtime_release.MAX_ENTRIES = len(fingerprint["files"])
        try:
            with self.assertRaisesRegex(runtime_release.ReleaseError, "archive entry budget"):
                self.build()
        finally:
            runtime_release.MAX_ENTRIES = previous

    def test_build_failure_preserves_existing_outputs(self):
        archive_path = os.path.join(self.output_a, "kimiflow-runtime-1.2.3.zip")
        manifest_path = os.path.join(
            self.output_a, runtime_release.UPDATE_MANIFEST_NAME
        )
        os.makedirs(self.output_a)
        with open(archive_path, "wb") as handle:
            handle.write(b"previous archive\n")
        with open(manifest_path, "wb") as handle:
            handle.write(b"previous manifest\n")
        with self.assertRaisesRegex(runtime_release.ReleaseError, "source commit"):
            runtime_release.build(self.candidate, self.output_a, "not-a-commit")
        with open(archive_path, "rb") as handle:
            self.assertEqual(handle.read(), b"previous archive\n")
        with open(manifest_path, "rb") as handle:
            self.assertEqual(handle.read(), b"previous manifest\n")

    def test_build_install_failure_rolls_back_both_outputs(self):
        archive_path = os.path.join(self.output_a, "kimiflow-runtime-1.2.3.zip")
        manifest_path = os.path.join(
            self.output_a, runtime_release.UPDATE_MANIFEST_NAME
        )
        os.makedirs(self.output_a)
        with open(archive_path, "wb") as handle:
            handle.write(b"previous archive\n")
        with open(manifest_path, "wb") as handle:
            handle.write(b"previous manifest\n")
        original_replace = os.replace
        failed = {"value": False}

        def fail_manifest_once(source, destination):
            if destination == manifest_path and not failed["value"]:
                failed["value"] = True
                raise OSError("simulated manifest install failure")
            return original_replace(source, destination)

        with mock.patch.object(
            runtime_release.os, "replace", side_effect=fail_manifest_once
        ):
            with self.assertRaisesRegex(
                runtime_release.ReleaseError, "previous outputs restored"
            ):
                self.build()
        with open(archive_path, "rb") as handle:
            self.assertEqual(handle.read(), b"previous archive\n")
        with open(manifest_path, "rb") as handle:
            self.assertEqual(handle.read(), b"previous manifest\n")

    def test_offline_verification_never_claims_publication(self):
        manifest, manifest_path, archive = self.build()
        status, verified, compatibility = runtime_release.verify_artifact(
            manifest_path,
            archive,
            host_profile="app_host",
            supported_protocol=1,
            host_features=runtime_release.HOST_PROFILES["app_host"],
        )
        self.assertEqual(status, "artifact_verified")
        self.assertEqual(verified, manifest)
        self.assertTrue(compatibility["compatible"])

    def test_missing_app_host_feature_is_incompatible(self):
        _, manifest_path, archive = self.build()
        status, _, compatibility = runtime_release.verify_artifact(
            manifest_path,
            archive,
            host_profile="app_host",
            supported_protocol=1,
            host_features=["workflow_context"],
        )
        self.assertEqual(status, "incompatible")
        self.assertFalse(compatibility["compatible"])
        self.assertIn("root_confinement", compatibility["missing_features"])

    def test_cli_returns_exit_three_for_incompatible_host(self):
        _, manifest_path, archive = self.build()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = runtime_release.main(
                [
                    "verify",
                    "--manifest",
                    manifest_path,
                    "--archive",
                    archive,
                    "--host-profile",
                    "app_host",
                    "--supported-adapter-protocol",
                    "1",
                    "--host-feature",
                    "workflow_context",
                ]
            )
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "incompatible")

    def test_draft_metadata_is_bounded_to_draft_verified(self):
        manifest, manifest_path, archive = self.build()
        with open(manifest_path, "rb") as handle:
            manifest_payload = handle.read()
        release = self.release_json(manifest, manifest_payload, draft=True, immutable=False)
        release_path = os.path.join(self.temp.name, "release.json")
        with open(release_path, "wb") as handle:
            handle.write(json_bytes(release))
        status, _, _ = runtime_release.verify_artifact(
            manifest_path, archive, release_json=release_path, stage="draft"
        )
        self.assertEqual(status, "draft_verified")
        release["assets"].append(
            {
                "name": "unexpected-private-artifact.txt",
                "size": 1,
                "digest": "sha256:" + ("0" * 64),
                "state": "uploaded",
            }
        )
        with open(release_path, "wb") as handle:
            handle.write(json_bytes(release))
        with self.assertRaisesRegex(runtime_release.ReleaseError, "inventory"):
            runtime_release.verify_artifact(
                manifest_path, archive, release_json=release_path, stage="draft"
            )
        with self.assertRaises(runtime_release.ReleaseError):
            runtime_release.verify_artifact(
                manifest_path, archive, release_json=release_path
            )

    def test_verify_release_accepts_only_compatible_immutable_exact_artifact(self):
        manifest, manifest_path, archive = self.build()
        with open(manifest_path, "rb") as handle:
            manifest_payload = handle.read()
        release = self.release_json(manifest, manifest_payload, draft=False, immutable=True)
        expected_release_url = (
            runtime_release.OFFICIAL_API
            + "/releases/tags/"
            + manifest["release"]["tag"]
        )
        expected_commit_url = (
            runtime_release.OFFICIAL_API
            + "/commits/"
            + manifest["release"]["tag"]
        )

        def opener(request, timeout):
            self.assertEqual(timeout, 30)
            if request.full_url == expected_release_url:
                return FakeResponse(request.full_url, release)
            if request.full_url == expected_commit_url:
                return FakeResponse(request.full_url, {"sha": SOURCE_COMMIT})
            self.fail("unexpected URL: %s" % request.full_url)

        status, compatibility = runtime_release.verify_published(
            manifest_path,
            archive,
            manifest["release"]["tag"],
            host_profile="embedded",
            supported_protocol=1,
            opener=opener,
        )
        self.assertEqual(status, "compatible")
        self.assertTrue(compatibility["compatible"])

    def test_published_verification_rejects_redirected_origin(self):
        manifest, manifest_path, archive = self.build()

        def opener(request, timeout):
            return FakeResponse("https://example.invalid/forged", {})

        with self.assertRaisesRegex(runtime_release.ReleaseError, "changed origin"):
            runtime_release.verify_published(
                manifest_path, archive, manifest["release"]["tag"], opener=opener
            )

    def test_published_verification_rejects_wrong_source_commit(self):
        manifest, manifest_path, archive = self.build()
        with open(manifest_path, "rb") as handle:
            manifest_payload = handle.read()
        release = self.release_json(manifest, manifest_payload, draft=False, immutable=True)

        def opener(request, timeout):
            if "/releases/tags/" in request.full_url:
                return FakeResponse(request.full_url, release)
            return FakeResponse(request.full_url, {"sha": "b" * 40})

        with self.assertRaisesRegex(runtime_release.ReleaseError, "pinned source"):
            runtime_release.verify_published(
                manifest_path, archive, manifest["release"]["tag"], opener=opener
            )

    def test_archive_tampering_fails(self):
        _, manifest_path, archive = self.build()
        with open(archive, "ab") as handle:
            handle.write(b"tamper")
        with self.assertRaisesRegex(runtime_release.ReleaseError, "digest"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_oversized_archive_is_rejected_before_payload_read(self):
        _, manifest_path, archive = self.build()
        with open(archive, "ab") as handle:
            handle.write(b"oversized")
        original_open = open

        def refuse_archive_read(path, mode="r", *args, **kwargs):
            if os.path.abspath(path) == os.path.abspath(archive) and mode == "rb":
                raise AssertionError("oversized archive payload was opened")
            return original_open(path, mode, *args, **kwargs)

        manifest, _ = runtime_release._read_json(manifest_path, "test manifest")
        with mock.patch("builtins.open", side_effect=refuse_archive_read):
            with self.assertRaisesRegex(runtime_release.ReleaseError, "size mismatch"):
                runtime_release._validate_zip(archive, manifest)

    def test_nested_archive_validation_rejects_traversal_with_matching_outer_digest(self):
        manifest, manifest_path, archive = self.build()
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr(
                runtime_release._zip_info("kimiflow/../escape", 0o644), b"escape"
            )
        with open(archive, "rb") as handle:
            payload = handle.read()
        manifest["artifact"]["bytes"] = len(payload)
        manifest["artifact"]["sha256"] = runtime_release._sha256(payload)
        with open(manifest_path, "wb") as handle:
            handle.write(json_bytes(manifest))
        with self.assertRaisesRegex(runtime_release.ReleaseError, "unsafe runtime archive path"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_nested_archive_validation_rejects_empty_runtime(self):
        _, manifest_path, archive = self.build()

        def empty_runtime(fingerprint, files):
            files.clear()

        self.rewrite_archive(manifest_path, archive, empty_runtime)
        with self.assertRaisesRegex(runtime_release.ReleaseError, "inventory"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_nested_archive_validation_rejects_file_directory_conflict(self):
        _, manifest_path, archive = self.build()

        def conflict(fingerprint, files):
            files["hooks"] = ("0644", b"conflict\n")

        self.rewrite_archive(manifest_path, archive, conflict)
        with self.assertRaisesRegex(runtime_release.ReleaseError, "path conflict"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_nested_archive_validation_rejects_manifest_descendant(self):
        _, manifest_path, archive = self.build()

        def conflict(fingerprint, files):
            files[runtime_release.RUNTIME_MANIFEST + "/child.txt"] = (
                "0644",
                b"conflict\n",
            )

        self.rewrite_archive(manifest_path, archive, conflict)
        with self.assertRaisesRegex(runtime_release.ReleaseError, "fingerprint row"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_nested_archive_validation_rejects_fingerprint_schema_drift(self):
        _, manifest_path, archive = self.build()

        def drift_schema(fingerprint, files):
            fingerprint["schema_version"] = 2

        self.rewrite_archive(manifest_path, archive, drift_schema)
        with self.assertRaisesRegex(runtime_release.ReleaseError, "unsupported archived"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_nested_archive_validation_rejects_executable_fingerprint(self):
        _, manifest_path, archive = self.build()
        self.rewrite_entry_mode(
            manifest_path,
            archive,
            "kimiflow/" + runtime_release.RUNTIME_MANIFEST,
            0o755,
        )
        with self.assertRaisesRegex(runtime_release.ReleaseError, "fingerprint manifest mode"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_nested_archive_validation_rejects_boolean_fingerprint_schema(self):
        _, manifest_path, archive = self.build()

        def boolean_schema(fingerprint, files):
            fingerprint["schema_version"] = True

        self.rewrite_archive(manifest_path, archive, boolean_schema)
        with self.assertRaisesRegex(runtime_release.ReleaseError, "unsupported archived"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_nested_archive_validation_rejects_host_manifest_version_drift(self):
        _, manifest_path, archive = self.build()

        def drift_version(fingerprint, files):
            mode, payload = files[".claude-plugin/plugin.json"]
            value = json.loads(payload.decode("utf-8"))
            value["version"] = "9.9.9"
            files[".claude-plugin/plugin.json"] = (mode, json_bytes(value))

        self.rewrite_archive(manifest_path, archive, drift_version)
        with self.assertRaisesRegex(runtime_release.ReleaseError, "version drift"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_release_manifest_rejects_boolean_numeric_contract_values(self):
        _, manifest_path, archive = self.build()
        cases = (
            ("schema_version", lambda value: value.__setitem__("schema_version", True)),
            (
                "runtime_manifest",
                lambda value: value["contracts"].__setitem__("runtime_manifest", True),
            ),
            (
                "protocol_min",
                lambda value: value["contracts"]["adapter_protocol"].__setitem__("min", True),
            ),
            (
                "immutable_required",
                lambda value: value["release"].__setitem__("immutable_required", 1),
            ),
        )
        with open(manifest_path, "rb") as handle:
            original = handle.read()

        def restore():
            with open(manifest_path, "wb") as handle:
                handle.write(original)

        self.addCleanup(restore)
        for label, mutate in cases:
            with self.subTest(label=label):
                value = json.loads(original.decode("utf-8"))
                mutate(value)
                with open(manifest_path, "wb") as handle:
                    handle.write(json_bytes(value))
                with self.assertRaises(runtime_release.ReleaseError):
                    runtime_release.verify_artifact(manifest_path, archive)

    def test_release_manifest_rejects_non_object_source_cleanly(self):
        _, manifest_path, archive = self.build()
        manifest, _ = runtime_release._read_json(manifest_path, "test manifest")
        manifest["source"] = [{}]
        with open(manifest_path, "wb") as handle:
            handle.write(json_bytes(manifest))
        with self.assertRaisesRegex(runtime_release.ReleaseError, "source identity"):
            runtime_release.verify_artifact(manifest_path, archive)

    def test_candidate_executable_bit_drift_fails(self):
        path = os.path.join(self.candidate, "hooks", "run.sh")
        os.chmod(path, 0o644)
        with self.assertRaisesRegex(runtime_release.ReleaseError, "executable-bit"):
            self.build()

    def release_json(self, manifest, manifest_payload, draft, immutable):
        archive_path = os.path.join(
            self.output_a, manifest["artifact"]["name"]
        )
        with open(archive_path, "rb") as handle:
            archive_payload = handle.read()
        return {
            "tag_name": manifest["release"]["tag"],
            "draft": draft,
            "prerelease": False,
            "immutable": immutable,
            "assets": [
                {
                    "name": runtime_release.UPDATE_MANIFEST_NAME,
                    "size": len(manifest_payload),
                    "digest": "sha256:%s" % hashlib.sha256(manifest_payload).hexdigest(),
                    "state": "uploaded",
                },
                {
                    "name": manifest["artifact"]["name"],
                    "size": len(archive_payload),
                    "digest": "sha256:%s" % hashlib.sha256(archive_payload).hexdigest(),
                    "state": "uploaded",
                },
            ],
        }


if __name__ == "__main__":
    unittest.main()
