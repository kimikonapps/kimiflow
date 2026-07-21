import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from memory_router import provider

_OBSIDIAN_BODY = json.dumps({
    "status": "OK",
    "manifest": {"id": "obsidian-local-rest-api", "name": "Local REST API", "version": "1.2.3"},
})


def _clean_env(**extra):
    # Isolate from the host's OBSIDIAN_API_KEY / KIMIFLOW_* so tests are deterministic.
    return mock.patch.dict(os.environ, extra, clear=True)


class ManifestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def write(self, text):
        path = os.path.join(self.dir, "VAULT-PROVIDER.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_missing_returns_default(self):
        m = provider.manifest_json(os.path.join(self.dir, "nope.json"))
        self.assertEqual(m["type"], "none")
        self.assertEqual(m["available"], False)
        self.assertEqual(list(m.keys()), [
            "schema_version", "type", "available", "mode", "vault_path",
            "last_prefetch_at", "last_write_at", "synced_learning_ids", "updated_at",
        ])

    def test_valid_object_returned_verbatim(self):
        m = provider.manifest_json(self.write('{"type":"obsidian","x":1}'))
        self.assertEqual(m, {"type": "obsidian", "x": 1})

    def test_invalid_or_null_returns_default(self):
        self.assertEqual(provider.manifest_json(self.write("NOT JSON"))["type"], "none")
        self.assertEqual(provider.manifest_json(self.write("null"))["type"], "none")


class NormalizeLoopbackCase(unittest.TestCase):
    def ok(self, url, expected):
        self.assertEqual(provider._normalize_loopback_origin(url), expected, url)

    def none(self, url):
        self.assertIsNone(provider._normalize_loopback_origin(url), url)

    def test_loopback_hosts(self):
        self.ok("https://127.0.0.1:27124", "https://127.0.0.1:27124")
        self.ok("http://localhost:27123/", "http://localhost:27123")
        self.ok("https://LOCALHOST", "https://localhost")
        self.ok("http://[::1]:8080", "http://[::1]:8080")
        self.ok("https://127.0.0.1/mcp", "https://127.0.0.1")
        self.ok("https://127.0.0.1:5000/mcp/", "https://127.0.0.1:5000")

    def test_rejected(self):
        self.none("http://example.com:8080")     # non-loopback host
        self.none("ftp://127.0.0.1")             # bad scheme
        self.none("https://127.0.0.1/other")     # disallowed path
        self.none("https://127.0.0.1:ab")        # non-numeric port
        self.none("https://user@127.0.0.1")      # userinfo
        self.none("https://127.0.0.1 x")         # whitespace


class DetectionCase(unittest.TestCase):
    def test_detected(self):
        with _clean_env(), mock.patch.object(provider, "_http_probe", return_value=(200, _OBSIDIAN_BODY)):
            d = provider.detection_json()
        self.assertEqual((d["status"], d["available"], d["type"]), ("detected", True, "obsidian"))
        self.assertEqual(d["manifest"], {"id": "obsidian-local-rest-api", "name": "Local REST API", "version": "1.2.3"})
        self.assertEqual(d["url"], "https://127.0.0.1:27124")

    def test_missing_when_no_server(self):
        with _clean_env(), mock.patch.object(provider, "_http_probe", return_value=(None, "")):
            d = provider.detection_json()
        self.assertEqual((d["status"], d["available"], d["reason"]), ("missing", False, "not_detected"))
        self.assertEqual(d["checked_urls"], ["https://127.0.0.1:27124", "http://127.0.0.1:27123"])

    def test_non_obsidian_body_is_missing(self):
        with _clean_env(), mock.patch.object(provider, "_http_probe", return_value=(200, '{"status":"OK"}')):
            self.assertEqual(provider.detection_json()["status"], "missing")

    def test_custom_url_env(self):
        with _clean_env(KIMIFLOW_OBSIDIAN_URL="http://127.0.0.1:9999/"), \
                mock.patch.object(provider, "_http_probe", return_value=(None, "")):
            self.assertEqual(provider.detection_json()["checked_urls"], ["http://127.0.0.1:9999"])


class AuthCase(unittest.TestCase):
    DET = {"available": False, "url": ""}

    def auth(self, manifest, detection=None, available=False, configured=False, **env):
        with _clean_env(**env):
            return provider.auth_json(manifest, detection or dict(self.DET), available, configured)

    def test_override_authenticated(self):
        a = self.auth({"vault_path": "http://127.0.0.1:1"}, KIMIFLOW_VAULT_AUTHENTICATED="1")
        self.assertEqual((a["status"], a["authenticated"], a["source"]), ("authenticated", True, "override"))

    def test_override_failed(self):
        a = self.auth({}, KIMIFLOW_OBSIDIAN_AUTHENTICATED="false")
        self.assertEqual((a["status"], a["source"]), ("auth_failed", "override"))

    def test_mcp(self):
        a = self.auth({}, KIMIFLOW_VAULT_MCP_AVAILABLE="yes")
        self.assertEqual((a["status"], a["source"]), ("authenticated", "mcp"))

    def test_token_missing_url(self):
        a = self.auth({}, OBSIDIAN_API_KEY="t")
        self.assertEqual((a["status"], a["probe_blocked_reason"], a["url"]), ("token_unverified", "missing_url", ""))

    def test_token_non_loopback_blanks_url(self):
        a = self.auth({"vault_path": "http://example.com:8080"}, OBSIDIAN_API_KEY="t")
        self.assertEqual(a["probe_blocked_reason"], "non_loopback_url")
        self.assertEqual(a["url"], "")   # Bash blanks the url on normalize failure

    def test_token_multiline(self):
        a = self.auth({"vault_path": "http://127.0.0.1:5000/mcp"}, OBSIDIAN_API_KEY="a\nb")
        self.assertEqual(a["probe_blocked_reason"], "multiline_token")
        self.assertEqual(a["url"], "http://127.0.0.1:5000")   # normalized before the multiline check

    def test_token_probe_authenticated(self):
        with _clean_env(OBSIDIAN_API_KEY="t"), mock.patch.object(provider, "_http_probe", return_value=(200, "")):
            a = provider.auth_json({"vault_path": "http://127.0.0.1:5000"}, dict(self.DET), False, True)
        self.assertEqual((a["status"], a["authenticated"], a["validated"], a["probe_http_status"]),
                         ("authenticated", True, True, "200"))

    def test_token_probe_auth_failed(self):
        with _clean_env(OBSIDIAN_API_KEY="t"), mock.patch.object(provider, "_http_probe", return_value=(401, "")):
            a = provider.auth_json({"vault_path": "http://127.0.0.1:5000"}, dict(self.DET), False, True)
        self.assertEqual((a["status"], a["validated"]), ("auth_failed", True))

    def test_token_source_precedence(self):
        a = self.auth({}, KIMIFLOW_OBSIDIAN_API_KEY="k", OBSIDIAN_API_KEY="o")
        self.assertEqual(a["token_source"], "KIMIFLOW_OBSIDIAN_API_KEY")

    def test_no_token_auth_required_when_detected(self):
        a = self.auth({}, detection={"available": True, "url": ""})
        self.assertEqual(a["status"], "auth_required")

    def test_no_token_not_configured(self):
        a = self.auth({})
        self.assertEqual((a["status"], a["source"]), ("not_configured", "none"))


class StatusCase(unittest.TestCase):
    def status(self, manifest_file="/nope.json", probe=(None, ""), **env):
        with _clean_env(**env), mock.patch.object(provider, "_http_probe", return_value=probe):
            return provider.status_json(manifest_file)

    def test_not_detected(self):
        s = self.status()
        self.assertEqual((s["present"], s["configured"], s["available"]), (False, False, False))
        self.assertEqual(s["health"]["status"], "not_detected")
        self.assertEqual(s["health"]["recommended_action"], "open_obsidian")

    def test_detected_unconfigured(self):
        s = self.status(probe=(200, _OBSIDIAN_BODY))
        self.assertEqual(s["health"]["status"], "detected_unconfigured")
        self.assertEqual(s["detection"]["available"], True)
        self.assertEqual(s["health"]["recommended_action"], "connect")

    def test_mcp_makes_search_write_ready(self):
        s = self.status(KIMIFLOW_VAULT_MCP_AVAILABLE="1")
        self.assertTrue(s["capabilities"]["direct_search"])
        self.assertTrue(s["capabilities"]["direct_write"])
        self.assertTrue(s["health"]["mcp_tools_authenticated"])

    def test_key_order(self):
        s = self.status()
        self.assertEqual(list(s.keys()), [
            "present", "configured", "path", "type", "available", "mode", "vault_path",
            "last_prefetch_at", "last_write_at", "capabilities", "detection", "auth", "health",
        ])


class _RootCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = os.path.join(self.root, ".kimiflow", "project")
        os.makedirs(self.project)

    def evidence(self, rel, content="data\n"):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        from memory_router import rows
        return rows.evidence_fingerprints_json(self.root, [rel])   # the fresh fingerprint

    def learnings(self, rows_):
        path = os.path.join(self.project, "LEARNINGS.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows_))
        return path

    def manifest(self, obj):
        path = os.path.join(self.project, "VAULT-PROVIDER.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(obj))
        return path

    def missing(self, name):
        return os.path.join(self.project, name)


class SyncStatusCase(_RootCase):
    AVAIL = {"type": "obsidian", "available": True, "updated_at": "2026-06-01T00:00:00Z"}

    def sync(self, learnings, manifest_file, probe=(None, "")):
        with _clean_env(), mock.patch.object(provider, "_http_probe", return_value=probe):
            return provider.sync_status_json(self.root, learnings, manifest_file)

    def safe(self, rid, fp, **overrides):
        row = {
            "id": rid, "status": "current", "kind": "learned", "topic": "portable-memory",
            "summary": "Prefer a bounded portable handoff.", "confidence": "high",
            "sensitivity": "normal", "last_verified": "2026-07-20",
            "evidence": ["src/a.py"], "evidence_fingerprints": fp,
        }
        row.update(overrides)
        return row

    def test_pending_and_exclusions(self):
        fp = self.evidence("src/a.py")
        stale = [dict(fp[0], sha256="deadbeef", digest="deadbeef")]
        learnings = self.learnings([
            self.safe("L1", fp),
            self.safe("Lpriv", fp, sensitivity="private"),
            self.safe("Lnoev", fp, evidence=[]),
            self.safe("Lnv", fp, evidence=["NOT VERIFIED"]),
            self.safe("Lstale", stale),
            self.safe("Larch", fp, status="archived"),
        ])
        manifest = self.manifest(self.AVAIL)
        s = self.sync(learnings, manifest)
        self.assertEqual((s["status"], s["available"]), ("pending", True))
        self.assertEqual((s["pending_count"], s["pending_ids"], s["exportable_count"]), (1, ["L1"], 1))

    def test_synced_id_excluded_current(self):
        fp = self.evidence("src/a.py")
        learnings = self.learnings([self.safe("L1", fp)])
        manifest = self.manifest(dict(self.AVAIL, synced_learning_ids=["L1"]))
        self.assertEqual(self.sync(learnings, manifest)["status"], "current")

    def test_unavailable_still_counts_exportable(self):
        fp = self.evidence("src/a.py")
        learnings = self.learnings([self.safe("L1", fp)])
        s = self.sync(learnings, self.missing("nope.json"))   # no manifest -> detection probes -> None -> unavailable
        self.assertEqual((s["status"], s["available"], s["pending_count"], s["exportable_count"]), ("provider_unavailable", False, 0, 1))

    def test_provider_sync_uses_portable_capsule_rows(self):
        fp = self.evidence("src/a.py")
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        outside_path = os.path.join(outside, "proof.py")
        with open(outside_path, "w", encoding="utf-8") as handle:
            handle.write("outside\n")
        linked_ref = "src/linked.py"
        os.symlink(outside_path, os.path.join(self.root, linked_ref))
        from memory_router import rows
        linked_fp = rows.evidence_fingerprints_json(self.root, [linked_ref])
        linked_fp_line = rows.evidence_fingerprints_json(self.root, [linked_ref + ":1"])
        safe = {
            "id": "local-source-id", "status": "current", "kind": "learned",
            "topic": "portable-memory", "summary": "Prefer a bounded portable handoff.",
            "confidence": "high", "sensitivity": "normal", "last_verified": "2026-07-20",
            "evidence": ["src/a.py"], "evidence_fingerprints": fp,
        }
        unsafe = dict(safe, id="unsafe-local-id", summary="Use %s metadata." % os.path.basename(self.root))
        unicode_domains = [
            dict(safe, id="unicode-domain-%d" % index, summary="See %s for details." % domain)
            for index, domain in enumerate((
                "example。com", "example．com", "example｡com",
                "例子。测试。", "例子．测试．", "例子｡测试｡", "例子.测试…",
            ))
        ]
        unicode_emails = [
            dict(safe, id="unicode-email-%d" % index, summary="Contact dev@%s" % domain)
            for index, domain in enumerate((
                "example。com", "example．com", "example｡com",
                "例子。测试。", "例子．测试．", "例子｡测试｡", "例子.测试…",
            ))
        ]
        privacy_rows = [
            dict(safe, id="dotless-email", summary="Contact alice@corp"),
            dict(safe, id="fullwidth-email", summary="Contact alice＠corp"),
            dict(safe, id="unicode-dotless-email", summary="Contact dev@公司"),
            dict(safe, id="fullwidth-url", summary="Open http：／／localhost：3000"),
            dict(safe, id="unicode-path", summary="Read src/私密.py before deciding."),
            dict(safe, id="bearer-secret", summary="Bearer " + "B" * 40),
            dict(safe, id="github-oauth", summary="gho_" + "A" * 36),
            dict(safe, id="github-user", summary="ghu_" + "A" * 36),
            dict(safe, id="github-server", summary="ghs_" + "A" * 36),
            dict(safe, id="github-refresh", summary="ghr_" + "A" * 36),
            dict(safe, id="jwt-secret", summary=(
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkFsaWNlIiwiaWF0IjoxNTE2MjM5MDIyfQ."
                "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
            )),
            dict(safe, id="jwt-short-payload", summary=(
                "Token eyJhbGciOiJIUzI1NiJ9.e30."
                "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
            )),
            dict(safe, id="split-secret", topic="sk-proj-AAAAAAAAAAAA", summary="A" * 40),
            dict(safe, id="split-domain", topic="example", summary=".com reference"),
            dict(safe, id="linked-evidence", evidence=[linked_ref],
                 evidence_fingerprints=linked_fp),
            dict(safe, id="linked-evidence-line", evidence=[linked_ref + ":1"],
                 evidence_fingerprints=linked_fp_line),
            dict(safe, id="unicode-provenance", summary="Reuse cafe\u0301 safely."),
        ]
        privacy_rows[-1]["id"] = "café"
        self.learnings([safe, unsafe] + unicode_domains + unicode_emails + privacy_rows)
        self.manifest(self.AVAIL)
        output = io.StringIO()
        with _clean_env(), mock.patch.object(provider, "_http_probe", return_value=(None, "")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(provider.run(["sync", "--root", self.root, "--write"]), 0)
        result = json.loads(output.getvalue())
        with open(os.path.join(self.project, "VAULT-SYNC.md"), encoding="utf-8") as handle:
            handoff = handle.read()
        self.assertIn("Prefer a bounded portable handoff.", handoff)
        self.assertIn("cap_", handoff)
        self.assertIn("confidence: high", handoff)
        self.assertIn("last_verified: 2026-07-20", handoff)
        self.assertNotIn("local-source-id", handoff)
        self.assertNotIn("unsafe-local-id", handoff)
        self.assertNotIn("unicode-domain-", handoff)
        self.assertNotIn("unicode-email-", handoff)
        self.assertNotIn("alice@corp", handoff)
        self.assertNotIn("alice＠corp", handoff)
        self.assertNotIn("dev@公司", handoff)
        self.assertNotIn("src/私密.py", handoff)
        self.assertNotIn("Bearer", handoff)
        self.assertNotIn("gho_", handoff)
        self.assertNotIn("ghu_", handoff)
        self.assertNotIn("ghs_", handoff)
        self.assertNotIn("ghr_", handoff)
        self.assertNotIn("eyJhbGciOiJIUzI1NiI", handoff)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9.e30", handoff)
        self.assertNotIn("cafe\u0301", handoff)
        self.assertNotIn("src/a.py", handoff)
        manifest = provider.manifest_json(os.path.join(self.project, "VAULT-PROVIDER.json"))
        self.assertEqual(manifest["synced_learning_ids"], ["local-source-id"])
        self.assertEqual(result["candidates"]["exported_count"], 1)

    def test_provider_sync_rejects_duplicate_key_source_row(self):
        fp = self.evidence("src/a.py")
        row = self.safe("ambiguous", fp, sensitivity="private")
        raw = json.dumps(row)[:-1] + ',"sensitivity":"normal"}'
        with open(os.path.join(self.project, "LEARNINGS.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(raw + "\n")
        self.manifest(self.AVAIL)
        output = io.StringIO()
        with _clean_env(), mock.patch.object(provider, "_http_probe", return_value=(None, "")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(provider.run(["sync", "--root", self.root, "--write"]), 0)
        result = json.loads(output.getvalue())
        manifest = provider.manifest_json(os.path.join(self.project, "VAULT-PROVIDER.json"))
        self.assertEqual(result["candidates"]["exported_count"], 0)
        self.assertEqual(manifest.get("synced_learning_ids", []), [])

    def test_provider_sync_refuses_symlinked_learning_source(self):
        fp = self.evidence("src/a.py")
        local = self.learnings([])
        outside = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        self.addCleanup(lambda: os.path.exists(outside.name) and os.unlink(outside.name))
        with outside:
            outside.write(json.dumps(self.safe("foreign-source", fp)) + "\n")
        os.unlink(local)
        os.symlink(outside.name, local)
        manifest_path = self.manifest(self.AVAIL)
        with _clean_env(), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                provider.run(["sync", "--root", self.root, "--write"]), 1
            )
        self.assertFalse(os.path.exists(os.path.join(self.project, "VAULT-SYNC.md")))
        self.assertEqual(
            provider.manifest_json(manifest_path).get("synced_learning_ids", []), []
        )

    def test_provider_sync_refuses_symlinked_project_parent(self):
        fp = self.evidence("src/a.py")
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        shutil.rmtree(self.project)
        os.symlink(outside, self.project)
        with open(os.path.join(outside, "LEARNINGS.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.safe("outside", fp)) + "\n")
        with open(os.path.join(outside, "VAULT-PROVIDER.json"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self.AVAIL))
        with _clean_env(), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(provider.run(["sync", "--root", self.root, "--write"]), 1)
        self.assertFalse(os.path.exists(os.path.join(outside, "VAULT-SYNC.md")))


class VaultStatusCase(_RootCase):
    def vault(self, index, manifest_file="", probe=(None, ""), **env):
        with _clean_env(**env), mock.patch.object(provider, "_http_probe", return_value=probe):
            return provider.vault_status_json(index, manifest_file)

    def test_env_available(self):
        v = self.vault(self.missing("no.json"), KIMIFLOW_VAULT_AVAILABLE="1")
        self.assertEqual((v["available"], v["provider"]), (True, None))

    def test_index_fills_provider_nulls(self):
        manifest = self.manifest({"type": "obsidian", "available": True, "updated_at": "2026-06-01T00:00:00Z",
                                  "last_prefetch_at": None, "last_write_at": "2026-05-02T00:00:00Z"})
        index = os.path.join(self.project, "MEMORY-INDEX.json")
        with open(index, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"vault": {"available": True, "last_recall_at": "2026-04-01", "last_write_at": "2026-04-02"}}))
        v = self.vault(index, manifest)
        self.assertEqual(v["available"], True)
        self.assertEqual(v["last_recall_at"], "2026-04-01")          # provider null -> index
        self.assertEqual(v["last_write_at"], "2026-05-02T00:00:00Z")  # provider non-null wins
        self.assertIsNotNone(v["provider"])


class HttpProbeRedirectCase(unittest.TestCase):
    def _serve(self, handler):
        srv = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        def close_server():
            srv.shutdown()
            thread.join(timeout=2.0)
            srv.server_close()

        self.addCleanup(close_server)
        return srv.server_address[1]

    def test_does_not_follow_redirect_or_leak_token(self):
        # Security + parity: curl (no -L) and the port must NOT follow a 3xx, so the
        # bearer token is never re-sent to the (off-host) redirect target.
        hits = []

        class Catcher(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

        target = "http://127.0.0.1:%d/vault/" % self._serve(Catcher)

        class Redirector(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()

        rport = self._serve(Redirector)
        code, _body = provider._http_probe("http://127.0.0.1:%d/vault/" % rport, 2.0,
                                           {"Authorization": "Bearer SECRET"})
        self.assertEqual(code, 302)   # terminal, not followed
        self.assertEqual(hits, [])    # off-host catcher never received the token


if __name__ == "__main__":
    unittest.main()
