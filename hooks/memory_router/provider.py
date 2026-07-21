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

from . import capsule, clock, contracts, rows, store
from .cli import die, resolve_root, usage

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
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:
                body = ""
            return exc.code, body
        finally:
            exc.close()
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


def detection_json(allow_network=True):
    # Bash provider_detection_json (733-803): probes the local Obsidian Local REST API.
    # The Bash `command -v curl` guard is unreachable here (urllib is always available),
    # so the port always probes (spec 12, generalizing the jq/sqlite stdlib rows).
    timeout = _detect_timeout()
    raw_urls = _detection_urls()
    checked = [_strip_trailing_slash(u) for u in raw_urls]

    if not allow_network:
        return {
            "status": "not_checked",
            "available": False,
            "type": "obsidian",
            "url": "",
            "checked_urls": [],
            "reason": "offline",
            "direct_write_requires_token": True,
            "manifest": None,
        }

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


def auth_json(manifest, detection, available, configured, allow_network=True):
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
        elif not allow_network:
            status = "token_unverified"
            probe_blocked_reason = "offline"
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
        elif probe_blocked_reason == "offline":
            hint = "API key is present but was not probed during an offline local refresh."
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


def status_json(manifest_file, allow_network=True):
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
        detection = detection_json(allow_network=allow_network)

    available = manifest.get("available") is True
    if (os.environ.get("KIMIFLOW_VAULT_AVAILABLE") or "") in _TRUTHY:
        available = True

    auth = auth_json(manifest, detection, available, configured, allow_network=allow_network)
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


_SYNC_PATH = ".kimiflow/project/VAULT-SYNC.md"


def _sync_base_candidates(source_rows, manifest):
    # Bash provider_sync_base_candidates_json (1294-1307): current, non-private/security
    # learnings that have evidence + all-current evidence_fingerprints, an id, and are not
    # already in manifest.synced_learning_ids.
    synced = _jq_or(manifest.get("synced_learning_ids"), [])
    if not isinstance(synced, list):
        synced = []
    out = []
    for row in source_rows:
        if _jq_or(row.get("status"), "current") != "current":
            continue
        sensitivity = _jq_or(row.get("sensitivity"), "normal")
        if sensitivity in ("security", "private"):
            continue
        evidence = _jq_or(row.get("evidence"), [])
        if not isinstance(evidence, list) or len(evidence) == 0:
            continue
        if any(e == "NOT VERIFIED" or e == "OUTSIDE_REPO" for e in evidence):
            continue
        fingerprints = _jq_or(row.get("evidence_fingerprints"), [])
        if not isinstance(fingerprints, list) or len(fingerprints) == 0:
            continue
        if not all(isinstance(fp, dict) and fp.get("status") == "current" for fp in fingerprints):
            continue
        rid = _jq_or(row.get("id"), "")
        if rid == "" or rid in synced:
            continue
        out.append(row)
    return out


def _sync_candidates(root, learnings, manifest_file):
    # Bash provider_sync_candidates_json (1309-1323): keep base candidates whose STORED
    # evidence_fingerprints still match a fresh recompute (evidence unchanged on disk).
    manifest = manifest_json(manifest_file)
    source_rows = store.read_local_jsonl_objects_strict(
        root, learnings, missing_ok=True
    )
    fresh = []
    for row in _sync_base_candidates(source_rows, manifest):
        evidence = _jq_or(row.get("evidence"), [])
        if not isinstance(evidence, list):
            evidence = []
        stored = _jq_or(row.get("evidence_fingerprints"), [])
        current = rows.evidence_fingerprints_json(root, evidence)
        # Bash compares the two `jq -c` strings; contracts.dumps is the order-preserving
        # compact equivalent.
        if contracts.dumps(stored) == contracts.dumps(current) \
                and capsule.portable_entry(root, row)[0] is not None:
            fresh.append(row)
    return fresh


def sync_status_json(root, learnings, manifest_file, allow_network=True):
    # Bash provider_sync_status_json (1325-1351): provider status + the count/ids of
    # learnings that are exportable to the vault and not yet synced.
    provider = status_json(manifest_file, allow_network=allow_network)
    try:
        candidates = _sync_candidates(root, learnings, manifest_file)
    except (ValueError, store.ConcurrentWriteError):
        candidates = []
    available = provider.get("available") is True
    detection_available = isinstance(provider.get("detection"), dict) and provider["detection"].get("available") is True
    health = provider.get("health") if isinstance(provider.get("health"), dict) else {}
    auth = provider.get("auth") if isinstance(provider.get("auth"), dict) else {}
    count = len(candidates)

    if not available and detection_available:
        status = "provider_detected_unconfigured"
    elif not available:
        status = "provider_unavailable"
    elif count > 0:
        status = "pending"
    else:
        status = "current"

    return {
        "path": _SYNC_PATH,
        "available": available,
        "pending_count": count if available else 0,
        "pending_ids": [c.get("id") for c in candidates] if available else [],
        "exportable_count": count,
        "health_status": _jq_or(health.get("status"), "unknown"),
        "auth_status": _jq_or(auth.get("status"), "unknown"),
        "direct_write_ready": health.get("direct_write_ready") is True,
        "status": status,
    }


def vault_status_json(index, provider_manifest="", allow_network=True):
    # Bash vault_status_json (1353-1397): merges env / provider / MEMORY-INDEX.json vault
    # availability and the last recall/write timestamps (provider wins, index fills nulls).
    available = (os.environ.get("KIMIFLOW_VAULT_AVAILABLE") or "") in _TRUTHY
    last_recall = None
    last_write = None
    provider = None

    if provider_manifest:
        provider = status_json(provider_manifest, allow_network=allow_network)
        if provider.get("available") is True:
            available = True
        last_recall = _jq_or(provider.get("last_prefetch_at"), None)
        last_write = _jq_or(provider.get("last_write_at"), None)

    if os.path.isfile(index):
        data = store.read_json(index)
        if data is not None and data is not False:   # Bash `jq -e .` (valid + truthy)
            vault = data.get("vault") if isinstance(data, dict) else None
            if not isinstance(vault, dict):
                vault = {}
            if vault.get("available") is True:
                available = True
            index_recall = _jq_or(vault.get("last_recall_at"), None)
            index_write = _jq_or(vault.get("last_write_at"), None)
            if last_recall is None:
                last_recall = index_recall
            if last_write is None:
                last_write = index_write

    return {
        "available": available,
        "last_recall_at": last_recall,
        "last_write_at": last_write,
        "provider": provider,
    }


# --- provider subcommand (cmd_provider 4160-4383) -------------------------------

def _nav(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _bool(value):
    # jq -r rendering of a boolean (true/false).
    return "true" if value else "false"


def _base_url_from_provider(provider):
    # Bash provider_base_url_from_provider_json (871-876).
    path = _jq_or(provider.get("vault_path"), "")
    url = path if path != "" else _jq_or(_nav(provider, "detection", "url"), "")
    if not url:
        url = "https://127.0.0.1:27124"
    return url


def provider_setup_plan_json(provider, setup_host):
    # Bash provider_setup_plan_json (890-993).
    if setup_host not in ("codex", "claude", "all"):
        setup_host = "all"
    raw_url = _base_url_from_provider(provider)
    base = _normalize_loopback_origin(raw_url)
    if base is not None:
        base_url = base
        mcp_url = base_url + "/mcp/"
        status = "setup_plan"
        reason = ""
    else:
        base_url = raw_url
        mcp_url = ""
        status = "blocked_non_loopback"
        reason = "non_loopback_url"
    blocked = status == "blocked_non_loopback"
    helper_path = "~/.kimiflow/obsidian-mcp-headers.sh"
    # Bash builds this via `$(printf '...prompt"\n')`; command substitution strips the
    # trailing newline, so the snippet ends at `prompt"` (no final \n).
    codex_snippet = ('[mcp_servers.obsidian]\nurl = "%s"\n'
                     'bearer_token_env_var = "OBSIDIAN_API_KEY"\n'
                     'default_tools_approval_mode = "prompt"') % mcp_url
    claude_snippet = {"mcpServers": {"obsidian": {"type": "http", "url": mcp_url,
                                                  "headersHelper": helper_path}}}
    manual_steps = [
        "Install and enable Obsidian Local REST API, then keep Obsidian running.",
        "Copy the API key only into your shell environment or macOS Keychain; do not paste it into chat or commit it.",
        "Run hooks/vault-mcp-open-terminal.sh --host <codex|claude|all> to open the interactive terminal wizard.",
        "Paste the API key only into that terminal prompt; do not paste it into chat or commit it.",
        "If the wizard reports a self-signed HTTPS certificate, trust the Obsidian Local REST API certificate in macOS Keychain, then rerun the wizard.",
        "Restart or reload the MCP client so the host, not Kimiflow, owns the bearer token.",
    ]
    return {
        "schema_version": 1,
        "status": status,
        "reason": (None if reason == "" else reason),
        "host": setup_host,
        "blocked": blocked,
        "provider_state": {
            "configured": provider.get("configured") is True,
            "available": provider.get("available") is True,
            "health": _jq_or(_nav(provider, "health", "status"), "unknown"),
            "auth": _jq_or(_nav(provider, "auth", "status"), "unknown"),
            "detected_url": _jq_or(_nav(provider, "detection", "url"), ""),
            "manifest_url": _jq_or(provider.get("vault_path"), ""),
        },
        "mcp": {
            "transport": "streamable_http",
            "url": ("" if blocked else mcp_url),
            "base_url": base_url,
            "token_env_var": "OBSIDIAN_API_KEY",
            "auth_header": "Authorization: Bearer ${OBSIDIAN_API_KEY}",
            "certificate_url": (base_url + "/obsidian-local-rest-api.crt"
                                if (not blocked and base_url.startswith("https://")) else ""),
            "http_loopback_fallback": "http://127.0.0.1:27123/mcp/",
        },
        "secret_policy": {
            "stores_token": False,
            "writes_token_to_repo": False,
            "echoes_token": False,
            "token_owner": "host_mcp_client",
            "token_inputs": ["OBSIDIAN_API_KEY", "KIMIFLOW_OBSIDIAN_API_KEY",
                             "macOS Keychain service kimiflow.obsidian.api-key"],
            "non_loopback_blocked": blocked,
        },
        "helpers": {
            "setup_script": "hooks/vault-mcp-setup.sh",
            "terminal_setup": ("" if blocked else
                               "hooks/vault-mcp-open-terminal.sh --host " + setup_host + " --url " + base_url),
            "interactive_setup": ("" if blocked else
                                  "hooks/vault-mcp-setup.sh --host " + setup_host + " --url " + base_url + " --interactive"),
            "verify_setup": ("" if blocked else
                             "hooks/vault-mcp-setup.sh --host " + setup_host + " --url " + base_url + " --verify"),
            "claude_headers_helper": helper_path,
            "write_codex_config": ("" if blocked else
                                   "hooks/vault-mcp-setup.sh --host codex --url " + base_url + " --write-config"),
            "write_claude_helper": ("" if blocked else
                                    "hooks/vault-mcp-setup.sh --host claude --url " + base_url + " --write-helper"),
        },
        "hosts": {
            "codex": {
                "enabled": setup_host in ("all", "codex"),
                "config_owner": "user-level ~/.codex/config.toml",
                "snippet": ("" if blocked else codex_snippet),
                "secret_handling": "Codex reads the bearer token from OBSIDIAN_API_KEY via bearer_token_env_var.",
            },
            "claude": {
                "enabled": setup_host in ("all", "claude"),
                "config_owner": "user or local Claude Code MCP config",
                "snippet": ({} if blocked else claude_snippet),
                "secret_handling": "Claude Code runs headersHelper at connection time; the helper reads OBSIDIAN_API_KEY or macOS Keychain and prints only request headers to the MCP client.",
            },
        },
        "manual_steps": manual_steps,
        "next_command": ("provider configure --path <loopback Obsidian URL>" if blocked else
                         "hooks/vault-mcp-open-terminal.sh --host " + setup_host + " --url " + base_url),
    }


def write_provider_prefetch_markdown(path, obj):
    # Bash write_provider_prefetch_markdown (4115-4129).
    os.makedirs(os.path.dirname(path), exist_ok=True)
    parts = ["# Vault Provider Prefetch\n\n"]
    parts.append("Generated: %s\n\n" % clock.iso_now())
    parts.append("Provider: %s\n" % _nav(obj, "provider", "type"))
    parts.append("Available: %s\n" % _bool(_nav(obj, "provider", "available")))
    parts.append("Health: %s\n" % _jq_or(_nav(obj, "provider", "health", "status"), "unknown"))
    parts.append("Auth: %s\n" % _jq_or(_nav(obj, "provider", "auth", "status"), "unknown"))
    parts.append("Direct search ready: %s\n" % _bool(obj.get("direct_search_ready") is True))
    parts.append("Query: %s\n\n" % obj.get("query"))
    parts.append("Use this as a bounded handoff for an Obsidian/Vault search. Direct search "
                 "requires an authenticated MCP tool in the current session; a local API key "
                 "may validate auth but does not by itself provide a search tool. If direct "
                 "search is not ready, continue with local memory + web. Save only curated, "
                 "publish-safe notes back through the provider.\n")
    store.atomic_write(path, "".join(parts))


def write_provider_sync_markdown(path, obj):
    # Bash write_provider_sync_markdown (4131-4158).
    os.makedirs(os.path.dirname(path), exist_ok=True)
    candidates = obj.get("candidates") if isinstance(obj.get("candidates"), dict) else {}
    exported = _jq_or(candidates.get("exported_count"), candidates.get("count"))
    parts = ["# Vault Provider Sync\n\n"]
    parts.append("Generated: %s\n\n" % clock.iso_now())
    parts.append("Provider: %s\n" % _nav(obj, "provider", "type"))
    parts.append("Available: %s\n" % _bool(_nav(obj, "provider", "available")))
    parts.append("Health: %s\n" % _jq_or(_nav(obj, "provider", "health", "status"), "unknown"))
    parts.append("Auth: %s\n" % _jq_or(_nav(obj, "provider", "auth", "status"), "unknown"))
    parts.append("Direct write ready: %s\n" % _bool(obj.get("direct_write_ready") is True))
    parts.append("Candidates exported: %s\n" % exported)
    parts.append("Total pending: %s\n" % candidates.get("count"))
    parts.append("Omitted: %s\n\n" % _jq_or(candidates.get("omitted_count"), 0))
    parts.append("Policy: review this bounded handoff before writing to the Vault. Direct "
                 "external writes require an authenticated MCP write tool in the current "
                 "session; a local API key may validate auth but does not by itself provide a "
                 "write tool. This handoff includes only the six-field portable Capsule "
                 "projection after fresh-evidence and local-provenance filtering. Remaining candidates stay "
                 "pending for a later sync.\n\n")
    if exported == 0:
        parts.append("No new publish-safe learning candidates are pending for Vault sync.\n")
    else:
        parts.append("## Candidates\n\n")
        rows_list = _nav(obj, "candidates", "rows")
        rows_list = rows_list if isinstance(rows_list, list) else []
        for row in rows_list:
            summary = str(_jq_or(row.get("summary"), "")).replace("\n", " ")
            parts.append("- [" + _jq_or(row.get("topic"), "uncategorized") + " \u00b7 "
                         + _jq_or(row.get("kind"), "learning") + " \u00b7 "
                         + _jq_or(row.get("capsule_id"), "") + "] " + summary
                         + " (confidence: " + _jq_or(row.get("confidence"), "")
                         + "; last_verified: " + _jq_or(row.get("last_verified"), "") + ")\n")
    store.atomic_write(path, "".join(parts))


def _sync_max():
    # Bash: KIMIFLOW_PROVIDER_SYNC_MAX (non-digit/empty -> 20; <= 0 -> 20).
    raw = os.environ.get("KIMIFLOW_PROVIDER_SYNC_MAX", "")
    if not raw or not all(c in "0123456789" for c in raw):
        return 20
    value = int(raw)
    return value if value > 0 else 20


def _write_manifest(manifest, obj):
    # Bash `printf '%s\n' "$out" | jq . > "$manifest"` (pretty + trailing newline).
    store.atomic_write(manifest, contracts.dumps(obj, pretty=True) + "\n")


def run(argv):
    action = argv[0] if argv else "status"
    rest = argv[1:] if argv else []
    root = ""
    pretty = False
    vtype = "obsidian"
    available = ""
    vault_path = ""
    query = ""
    write = False
    setup_host = "all"
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--root":
            i += 1
            root = rest[i] if i < len(rest) else ""
        elif arg == "--type":
            i += 1
            vtype = rest[i] if i < len(rest) else ""
        elif arg == "--available":
            i += 1
            available = rest[i] if i < len(rest) else ""
        elif arg == "--path":
            i += 1
            vault_path = rest[i] if i < len(rest) else ""
        elif arg == "--query":
            i += 1
            query = rest[i] if i < len(rest) else ""
        elif arg in ("--host", "--target"):
            i += 1
            setup_host = rest[i] if i < len(rest) else "all"
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("provider: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    project = root + "/.kimiflow/project"
    manifest = project + "/VAULT-PROVIDER.json"
    now = clock.iso_now()

    if write or action in ("connect", "configure"):
        try:
            store.require_local_path(root, project)
            store.require_local_path(root, manifest)
        except ValueError as exc:
            return die("provider: %s" % exc, 1)

    if action == "status":
        out = status_json(manifest)
    elif action == "health":
        provider = status_json(manifest)
        out = {
            "schema_version": 1,
            "status": _jq_or(_nav(provider, "health", "status"), "unknown"),
            "recommended_action": _jq_or(_nav(provider, "health", "recommended_action"), "open_obsidian"),
            "health": provider.get("health"),
            "auth": provider.get("auth"),
            "detection": provider.get("detection"),
            "capabilities": provider.get("capabilities"),
            "provider": provider,
        }
    elif action == "setup":
        provider = status_json(manifest)
        out = provider_setup_plan_json(provider, setup_host)
    elif action in ("detect", "connect"):
        detection = detection_json()
        if detection.get("available") is not True:
            out = {
                "schema_version": 1,
                "status": "not_detected",
                "written": False,
                "path": _PROVIDER_PATH,
                "detection": detection,
                "provider": None,
            }
        else:
            if action == "connect":
                write = True
            if write:
                try:
                    store.ensure_local_directory(root, project)
                    with store.local_path_guard(root, project):
                        existing = manifest_json(manifest)
                        sids = _jq_or(existing.get("synced_learning_ids"), [])
                        sids = sids if isinstance(sids, list) else []
                        out = {
                            "schema_version": 1,
                            "type": "obsidian",
                            "available": True,
                            "mode": _jq_or(existing.get("mode"), "local-first"),
                            "vault_path": detection.get("url"),
                            "last_prefetch_at": _jq_or(existing.get("last_prefetch_at"), None),
                            "last_write_at": _jq_or(existing.get("last_write_at"), None),
                            "synced_learning_ids": sids,
                            "detection": detection,
                            "updated_at": now,
                        }
                        _write_manifest(manifest, out)
                except (ValueError, store.ConcurrentWriteError) as exc:
                    return die("provider: %s; retry" % exc, 1)
            provider = status_json(manifest)
            out = {
                "schema_version": 1,
                "status": ("connected" if write else "detected"),
                "written": write,
                "path": _PROVIDER_PATH,
                "detection": detection,
                "provider": provider,
            }
    elif action == "configure":
        if available in ("1", "true", "TRUE", "yes", "YES"):
            available_json = True
        elif available in ("0", "false", "FALSE", "no", "NO", ""):
            available_json = False
        else:
            return die("provider configure --available must be true or false", 2)
        out = {
            "schema_version": 1,
            "type": vtype,
            "available": available_json,
            "mode": "local-first",
            "vault_path": vault_path,
            "last_prefetch_at": None,
            "last_write_at": None,
            "synced_learning_ids": [],
            "updated_at": now,
        }
        try:
            store.ensure_local_directory(root, project)
            with store.local_path_guard(root, project):
                _write_manifest(manifest, out)
        except (ValueError, store.ConcurrentWriteError) as exc:
            return die("provider: %s; retry" % exc, 1)
        out = status_json(manifest)
    elif action == "prefetch":
        provider = status_json(manifest)
        prefetch_path = project + "/VAULT-PREFETCH.md"
        if provider.get("available") is not True:
            out = {
                "schema_version": 1,
                "status": "skipped",
                "reason": "provider_unavailable",
                "path": ".kimiflow/project/VAULT-PREFETCH.md",
                "provider": provider,
            }
        else:
            if not query:
                query = "project memory recall"
            out = {
                "schema_version": 1,
                "status": "prefetch_handoff",
                "query": query,
                "path": ".kimiflow/project/VAULT-PREFETCH.md",
                "provider": provider,
                "direct_search_ready": _nav(provider, "health", "direct_search_ready") is True,
                "review_required": True,
            }
            if write:
                try:
                    store.ensure_local_directory(root, project)
                    with store.local_path_guard(root, project):
                        write_provider_prefetch_markdown(prefetch_path, out)
                        updated = manifest_json(manifest)
                        updated["last_prefetch_at"] = now
                        updated["updated_at"] = now
                        updated["available"] = True
                        _write_manifest(manifest, updated)
                except (ValueError, store.ConcurrentWriteError) as exc:
                    return die("provider: %s; retry" % exc, 1)
                out["written"] = True
    elif action == "sync":
        provider = status_json(manifest)
        sync_path = project + "/VAULT-SYNC.md"
        if provider.get("available") is not True:
            out = {
                "schema_version": 1,
                "status": "skipped",
                "reason": "provider_unavailable",
                "path": ".kimiflow/project/VAULT-SYNC.md",
                "provider": provider,
                "candidates": {"count": 0, "exported_count": 0, "omitted_count": 0, "ids": []},
            }
        else:
            sync_max = _sync_max()
            try:
                candidates = _sync_candidates(
                    root, project + "/LEARNINGS.jsonl", manifest
                )
            except (ValueError, store.ConcurrentWriteError) as exc:
                return die("provider: %s; retry" % exc, 1)
            portable = []
            exported_sources = []
            reason_counts = {}
            for source in candidates:
                entry, reason = capsule.portable_entry(root, source)
                if reason is not None:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                elif len(portable) >= sync_max:
                    reason_counts["limit"] = reason_counts.get("limit", 0) + 1
                else:
                    portable.append(entry)
                    exported_sources.append(source)
            omitted = len(candidates) - len(portable)
            out = {
                "schema_version": 1,
                "status": "sync_handoff",
                "path": ".kimiflow/project/VAULT-SYNC.md",
                "provider": provider,
                "direct_write_ready": _nav(provider, "health", "direct_write_ready") is True,
                "review_required": True,
                "candidates": {
                    "count": len(candidates),
                    "exported_count": len(portable),
                    "omitted_count": omitted,
                    "ids": [c.get("capsule_id") for c in portable],
                    "reason_counts": reason_counts,
                },
                "written": False,
            }
            if write:
                try:
                    store.ensure_local_directory(root, project)
                    with store.local_path_guard(root, project):
                        handoff = dict(out)
                        handoff["candidates"] = dict(out["candidates"], rows=portable)
                        write_provider_sync_markdown(sync_path, handoff)
                        new_ids = [c.get("id") for c in exported_sources]
                        updated = manifest_json(manifest)
                        existing_ids = _jq_or(updated.get("synced_learning_ids"), [])
                        existing_ids = existing_ids if isinstance(existing_ids, list) else []
                        updated["last_write_at"] = now
                        updated["updated_at"] = now
                        updated["available"] = True
                        updated["synced_learning_ids"] = sorted(
                            set(existing_ids + new_ids)
                        )
                        _write_manifest(manifest, updated)
                except (ValueError, store.ConcurrentWriteError) as exc:
                    return die("provider: %s; retry" % exc, 1)
                provider = status_json(manifest)
                out["written"] = True
                out["provider"] = provider
    else:
        return die("provider action must be status, health, setup, detect, connect, "
                   "configure, prefetch, or sync", 2)

    contracts.json_print(out, pretty)
    return 0
