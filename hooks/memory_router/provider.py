"""Obsidian/Vault provider status: manifest read, local detection, auth, and the
composed provider_status_json. Behavioral port of the Bash provider_* helpers @
kimiflow--v0.1.50 (714-1292). The live HTTP probes (curl `-k -sS -m T`) are ported to
the stdlib `urllib` with TLS verification disabled, loopback-only to avoid leaking the
API token. Each function returns a Python dict/bool (serialized at the contracts.dumps
boundary by the calling subcommand)."""
import json
import os
import re
import ssl
import urllib.error
import urllib.request

from . import store

_PROVIDER_PATH = ".kimiflow/project/VAULT-PROVIDER.json"
_DEFAULT_DETECT_TIMEOUT = 0.35
_DEFAULT_URLS = "https://127.0.0.1:27124 http://127.0.0.1:27123"
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1")

# Bash `case ... in 1|true|TRUE|yes|YES`.
_TRUTHY = {"1", "true", "TRUE", "yes", "YES"}
_FALSY = {"0", "false", "FALSE", "no", "NO"}


def _jq_or(value, default):
    # jq `value // default` (null/false -> default). Local copy of the summaries/
    # recall_index helper; the 3rd consumer -- consolidation into a shared jq module is
    # now warranted (carry-forward).
    return default if value is None or value is False else value


def _strip_trailing_slash(text):
    # Bash `${url%/}`: removes a single trailing "/".
    return text[:-1] if text.endswith("/") else text


def _detect_timeout():
    # Bash: ${KIMIFLOW_OBSIDIAN_DETECT_TIMEOUT:-0.35}; empty -> 0.35. A non-numeric value
    # would make curl error (probe fails); the port falls back to the default (unreachable
    # -- this is a numeric config).
    raw = os.environ.get("KIMIFLOW_OBSIDIAN_DETECT_TIMEOUT") or ""
    if raw == "":
        return _DEFAULT_DETECT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_DETECT_TIMEOUT


def _ssl_context():
    # curl -k: do not verify the local Obsidian cert (self-signed loopback).
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # curl (no -L) does NOT follow redirects: a 3xx is terminal. urllib's default opener
    # would follow it AND re-send the Authorization header cross-origin, leaking the token
    # off the loopback host (defeating the _normalize_loopback_origin guard). Returning
    # None here makes urllib raise the 3xx as an HTTPError instead of following it.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_probe(url, timeout, headers=None):
    # curl -k -sS --connect-timeout T -m T [headers] URL (no -L). Returns (code, body):
    # the HTTP status (int) and decoded body for any response, INCLUDING 3xx (terminal,
    # not followed); (None, "") on connect/timeout/transport failure.
    req = urllib.request.Request(url, headers=headers or {})
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=_ssl_context()))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, body
    except Exception:
        return None, ""


def _default_manifest():
    return {
        "schema_version": 1,
        "type": "none",
        "available": False,
        "mode": "local-first",
        "vault_path": "",
        "last_prefetch_at": None,
        "last_write_at": None,
        "synced_learning_ids": [],
        "updated_at": None,
    }


def manifest_json(manifest_file):
    # Bash provider_manifest_json (714-732): the parsed VAULT-PROVIDER.json when it exists
    # and is valid+truthy JSON, else the default manifest. A valid non-object file (Bash
    # `jq '.'` returns it verbatim, then callers error on `.type`) maps to the default
    # here -- unreachable; the manifest is always an object.
    data = store.read_json(manifest_file)
    if isinstance(data, dict):
        return data
    return _default_manifest()


def _normalize_loopback_origin(url):
    # Bash provider_normalize_loopback_origin (808-869): canonical scheme://host[:port]
    # for a loopback (localhost / 127.0.0.1 / ::1) http(s) URL whose path is empty / "/" /
    # "/mcp" / "/mcp/", else None. Loopback-only is the guard that auth probes never send
    # the token to a non-local host.
    url = _strip_trailing_slash(url)
    if any(c in url for c in " \t\n\r\v\f\"'\\`"):
        return None
    if url.startswith("http://"):
        scheme, rest = "http", url[len("http://"):]
    elif url.startswith("https://"):
        scheme, rest = "https", url[len("https://"):]
    else:
        return None
    if "/" in rest:
        host_port, _, after = rest.partition("/")
        path = "/" + after
    else:
        host_port, path = rest, ""
    if path not in ("", "/", "/mcp", "/mcp/"):
        return None
    if not host_port or "@" in host_port:
        return None
    if host_port.startswith("["):
        host = host_port[1:].split("]", 1)[0]
        suffix = host_port[host_port.find("]") + 1:]
        if suffix == "":
            port = ""
        elif suffix.startswith(":"):
            port = suffix[1:]
        else:
            return None
    elif ":" in host_port:
        host, _, port = host_port.partition(":")
        if ":" in port:
            return None
    else:
        host, port = host_port, ""
    host_lc = host.lower()
    if port != "" and not all(c in "0123456789" for c in port):
        return None
    if host_lc in _LOOPBACK_HOSTS:
        return "%s://%s:%s" % (scheme, host_lc, port) if port else "%s://%s" % (scheme, host_lc)
    if host_lc == "::1":
        return "%s://[::1]:%s" % (scheme, port) if port else "%s://[::1]" % scheme
    return None


def _detection_urls():
    # Bash: KIMIFLOW_OBSIDIAN_URL (whitespace-split) or the two default loopback URLs.
    raw = os.environ.get("KIMIFLOW_OBSIDIAN_URL") or ""
    return (raw if raw != "" else _DEFAULT_URLS).split()


def detection_json():
    # Bash provider_detection_json (733-803): probes the local Obsidian Local REST API.
    # The Bash `command -v curl` guard is unreachable here (urllib is always available),
    # so the port always probes (spec 12, generalizing the jq/sqlite stdlib rows).
    timeout = _detect_timeout()
    raw_urls = _detection_urls()
    checked = [_strip_trailing_slash(u) for u in raw_urls]

    for url in raw_urls:
        normalized = _strip_trailing_slash(url)
        _code, body = _http_probe(normalized + "/", timeout)
        data = _parse_json(body)
        if not isinstance(data, dict):
            continue
        manifest = data.get("manifest") if isinstance(data.get("manifest"), dict) else {}
        mid = _jq_or(manifest.get("id"), "")
        mname = _jq_or(manifest.get("name"), "")
        status_ok = _jq_or(data.get("status"), "") == "OK"
        id_match = isinstance(mid, str) and re.search("obsidian-local-rest-api", mid) is not None
        name_match = isinstance(mname, str) and re.search("Local REST API", mname, re.IGNORECASE) is not None
        if status_ok and (id_match or name_match):
            return {
                "status": "detected",
                "available": True,
                "type": "obsidian",
                "url": normalized,
                "checked_urls": checked,
                "reason": None,
                "direct_write_requires_token": True,
                "manifest": {
                    "id": _jq_or(manifest.get("id"), ""),
                    "name": _jq_or(manifest.get("name"), ""),
                    "version": _jq_or(manifest.get("version"), ""),
                },
            }

    return {
        "status": "missing",
        "available": False,
        "type": "obsidian",
        "url": "",
        "checked_urls": checked,
        "reason": "not_detected",
        "direct_write_requires_token": True,
        "manifest": None,
    }


def _parse_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _auth_url(manifest, detection):
    # Bash: (.vault_path // "") or else (.detection.url // ""), trailing slash stripped.
    path = _jq_or(manifest.get("vault_path"), "")
    url = path if path != "" else _jq_or(detection.get("url"), "")
    return _strip_trailing_slash(url) if isinstance(url, str) else ""


def _auth_override(url, authenticated, hint):
    return {
        "required": True,
        "status": "authenticated" if authenticated else "auth_failed",
        "authenticated": authenticated,
        "source": "override",
        "token_env_present": False,
        "token_source": None,
        "token_stored": False,
        "validated": False,
        "probe_http_status": None,
        "probe_allowed": False,
        "probe_blocked_reason": None,
        "url": url,
        "setup_hint": hint,
    }


def auth_json(manifest, detection, available, configured):
    # Bash provider_auth_json (1000-1196): env override -> MCP -> env API token (loopback
    # HTTP probe) -> unauthenticated. The token probe targets only a normalized loopback
    # origin so the bearer token is never sent off-host.
    url = _auth_url(manifest, detection)

    override = os.environ.get("KIMIFLOW_VAULT_AUTHENTICATED") or os.environ.get("KIMIFLOW_OBSIDIAN_AUTHENTICATED") or ""
    if override in _TRUTHY:
        return _auth_override(url, True, "Vault auth was marked available by environment override.")
    if override in _FALSY:
        return _auth_override(url, False, "Vault auth was marked failed by environment override.")

    mcp = os.environ.get("KIMIFLOW_VAULT_MCP_AVAILABLE") or os.environ.get("KIMIFLOW_OBSIDIAN_MCP_AVAILABLE") or ""
    if mcp in _TRUTHY:
        return {
            "required": True,
            "status": "authenticated",
            "authenticated": True,
            "source": "mcp",
            "token_env_present": False,
            "token_source": None,
            "token_stored": False,
            "validated": False,
            "probe_http_status": None,
            "probe_allowed": False,
            "probe_blocked_reason": None,
            "url": url,
            "setup_hint": "Authenticated Obsidian/Vault MCP is available in this session.",
        }

    token = ""
    token_source = ""
    if os.environ.get("KIMIFLOW_OBSIDIAN_API_KEY"):
        token = os.environ["KIMIFLOW_OBSIDIAN_API_KEY"]
        token_source = "KIMIFLOW_OBSIDIAN_API_KEY"
    elif os.environ.get("OBSIDIAN_API_KEY"):
        token = os.environ["OBSIDIAN_API_KEY"]
        token_source = "OBSIDIAN_API_KEY"

    if token:
        status = "token_present"
        source = "env"
        authenticated = False
        validated = False
        code = ""
        probe_allowed = False
        probe_blocked_reason = ""
        normalized = _normalize_loopback_origin(url) if url else None
        if url == "":
            status = "token_unverified"
            probe_blocked_reason = "missing_url"
        elif normalized is None:
            # Bash `url="$(provider_normalize_loopback_origin "$url")"` captures the failed
            # function's empty stdout, so a non-loopback URL is blanked in the output.
            status = "token_unverified"
            probe_blocked_reason = "non_loopback_url"
            url = ""
        else:
            url = normalized   # normalize succeeded -> canonical loopback origin
            if "\n" in token or "\r" in token:
                status = "token_unverified"
                probe_blocked_reason = "multiline_token"
            else:
                probe_allowed = True
                probe_code, _body = _http_probe(url + "/vault/", _detect_timeout(),
                                                {"Authorization": "Bearer " + token})
                code = str(probe_code) if probe_code is not None else "000"
                if code.startswith("2"):
                    status, authenticated, validated = "authenticated", True, True
                elif code in ("401", "403"):
                    status, validated = "auth_failed", True
                else:
                    status = "token_unverified"
        if probe_blocked_reason == "non_loopback_url":
            hint = "API key is present, but Kimiflow only probes loopback Obsidian URLs to avoid leaking tokens."
        elif probe_blocked_reason == "missing_url":
            hint = "API key is present, but no local Obsidian URL is configured or detected."
        elif probe_blocked_reason == "multiline_token":
            hint = "API key is present but was not probed because multiline tokens are rejected."
        elif authenticated:
            hint = "API key is available via environment and validated against the local Obsidian API."
        elif status == "auth_failed":
            hint = "API key is present but the local Obsidian API rejected it."
        else:
            hint = "API key is present in the environment but was not validated; use an authenticated MCP or verify the Local REST API key."
        return {
            "required": True,
            "status": status,
            "authenticated": authenticated,
            "source": source,
            "token_env_present": True,
            "token_source": token_source,
            "token_stored": False,
            "validated": validated,
            "probe_http_status": None if code == "" else code,
            "probe_allowed": probe_allowed,
            "probe_blocked_reason": None if probe_blocked_reason == "" else probe_blocked_reason,
            "url": url,
            "setup_hint": hint,
        }

    status = "not_configured"
    if available is True or detection.get("available") is True:
        status = "auth_required"
    if status == "auth_required" and configured:
        hint = "Local Obsidian provider is connected; run provider setup for safe Codex/Claude MCP instructions without storing the API key."
    elif status == "auth_required":
        hint = "Obsidian was detected; run provider connect, then provider setup for safe Codex/Claude MCP instructions without storing the API key."
    else:
        hint = "No local Obsidian provider is detected yet."
    return {
        "required": True,
        "status": status,
        "authenticated": False,
        "source": "none",
        "token_env_present": False,
        "token_source": None,
        "token_stored": False,
        "validated": False,
        "probe_http_status": None,
        "probe_allowed": False,
        "probe_blocked_reason": None,
        "url": url,
        "setup_hint": hint,
    }


def direct_search_ready(auth):
    # Bash provider_direct_search_ready_json / provider_direct_write_ready_json: .source == "mcp".
    return auth.get("source") == "mcp"


def status_json(manifest_file):
    # Bash provider_status_json (1197-1292): composes manifest + detection + auth into the
    # provider capability/health view consumed by status_json and provider_sync_status_json.
    manifest = manifest_json(manifest_file)
    configured = manifest.get("updated_at") is not None or manifest.get("type") != "none"

    if configured:
        detection = manifest.get("detection")
        if detection is None or detection is False:
            detection = {
                "status": "configured",
                "available": False,
                "type": _jq_or(manifest.get("type"), "none"),
                "url": _jq_or(manifest.get("vault_path"), ""),
                "checked_urls": [],
                "reason": None,
                "direct_write_requires_token": True,
                "manifest": None,
            }
    else:
        detection = detection_json()

    available = manifest.get("available") is True
    if (os.environ.get("KIMIFLOW_VAULT_AVAILABLE") or "") in _TRUTHY:
        available = True

    auth = auth_json(manifest, detection, available, configured)
    search_ready = direct_search_ready(auth)
    write_ready = direct_search_ready(auth)  # Bash uses the identical `.source == "mcp"`.

    auth_authenticated = auth.get("authenticated") is True
    rest_api_authenticated = auth_authenticated and auth.get("source") == "env"

    if auth.get("status") == "auth_failed":
        health_status = "auth_failed"
    elif configured and available and auth_authenticated:
        health_status = "authenticated"
    elif configured and available:
        health_status = "connected_local_only"
    elif detection.get("available") is True:
        health_status = "detected_unconfigured"
    else:
        health_status = "not_detected"

    if auth.get("status") == "auth_failed":
        recommended = "check_auth"
    elif configured and available and auth_authenticated:
        recommended = "prefetch_or_sync"
    elif configured and available:
        recommended = "setup_auth"
    elif detection.get("available") is True:
        recommended = "connect"
    else:
        recommended = "open_obsidian"

    health = {
        "status": health_status,
        "local_handoff_ready": available or detection.get("available") is True,
        "direct_search_ready": search_ready,
        "direct_write_ready": write_ready,
        "rest_api_authenticated": rest_api_authenticated,
        "mcp_tools_authenticated": auth.get("source") == "mcp",
        "review_required": True,
        "recommended_action": recommended,
    }

    return {
        "present": configured,
        "configured": configured,
        "path": _PROVIDER_PATH,
        "type": _jq_or(manifest.get("type"), "none"),
        "available": available,
        "mode": _jq_or(manifest.get("mode"), "local-first"),
        "vault_path": _jq_or(manifest.get("vault_path"), ""),
        "last_prefetch_at": _jq_or(manifest.get("last_prefetch_at"), None),
        "last_write_at": _jq_or(manifest.get("last_write_at"), None),
        "capabilities": {
            "status": True,
            "prefetch": available,
            "sync": available,
            "write": False,
            "extract": False,
            "search": search_ready,
            "write_review": available,
            "direct_search": search_ready,
            "direct_write": write_ready,
            "mcp_direct_write": write_ready,
            "rest_api_authenticated": rest_api_authenticated,
            "authenticated": auth_authenticated,
        },
        "detection": detection,
        "auth": auth,
        "health": health,
    }
