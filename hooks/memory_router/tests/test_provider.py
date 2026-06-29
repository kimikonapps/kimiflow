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


class HttpProbeRedirectCase(unittest.TestCase):
    def _serve(self, handler):
        srv = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
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
