"""Project-bound Vault query/result contract with bounded local receipts."""

import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata

from . import contracts, rows, store


class VaultNamespaceError(ValueError):
    pass


CONTRACT_NAME = "VAULT-NAMESPACE.json"
RECALL_JSON_NAME = "VAULT-RECALL.json"
RECALL_MD_NAME = "VAULT-RECALL.md"
RECEIPT_NAME = "VAULT-RECALL-RECEIPT.json"
MAX_INPUT_BYTES = 1024 * 1024
MAX_RESULTS = 8
MAX_CANDIDATES = 64
MAX_TEXT = 4000
PROJECT_ID_RE = re.compile(r"^p_[0-9a-f]{24}$")


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _git_identity(root):
    try:
        proc = subprocess.run(
            ["git", "-C", root, "rev-parse", "--git-common-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return os.path.realpath(root)
    if proc.returncode != 0 or not proc.stdout.strip():
        return os.path.realpath(root)
    common = proc.stdout.strip()
    if not os.path.isabs(common):
        common = os.path.join(root, common)
    return os.path.realpath(common)


def project_id(root):
    digest = hashlib.sha256(
        ("kimiflow-vault-namespace:1\0" + _git_identity(root)).encode("utf-8")
    ).hexdigest()
    return "p_" + digest[:24]


def _contract_path(root):
    return os.path.join(root, ".kimiflow", "project", CONTRACT_NAME)


def default_contract(root):
    ident = project_id(root)
    return {
        "schema_version": 1,
        "project_id": ident,
        "allowed_prefixes": ["Projects/%s/" % ident],
        "max_results": MAX_RESULTS,
        "cross_project_mode": "privacy_capsules_only",
    }


def validate_contract(value):
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "project_id", "allowed_prefixes", "max_results",
        "cross_project_mode",
    }:
        raise VaultNamespaceError("namespace_contract_invalid")
    if value.get("schema_version") != 1 or PROJECT_ID_RE.fullmatch(str(value.get("project_id", ""))) is None:
        raise VaultNamespaceError("namespace_contract_invalid")
    prefixes = value.get("allowed_prefixes")
    if (
        not isinstance(prefixes, list)
        or not 1 <= len(prefixes) <= 8
        or any(not _safe_prefix(prefix) for prefix in prefixes)
    ):
        raise VaultNamespaceError("namespace_prefixes_invalid")
    maximum = value.get("max_results")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= MAX_RESULTS:
        raise VaultNamespaceError("namespace_result_limit_invalid")
    if value.get("cross_project_mode") != "privacy_capsules_only":
        raise VaultNamespaceError("namespace_cross_project_mode_invalid")
    return {
        "schema_version": 1,
        "project_id": value["project_id"],
        "allowed_prefixes": list(prefixes),
        "max_results": maximum,
        "cross_project_mode": "privacy_capsules_only",
    }


def _safe_prefix(value):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 240
        and value.endswith("/")
        and not value.startswith(("/", "\\"))
        and ".." not in value.replace("\\", "/").split("/")
        and "\\" not in value
        and all(ord(char) >= 32 and ord(char) != 127 for char in value)
    )


def load_contract(root, write=False):
    path = _contract_path(root)
    value = store.read_json(path)
    if value is None:
        value = default_contract(root)
        if write:
            store.ensure_local_directory(root, os.path.dirname(path))
            with store.local_path_guard(root, os.path.dirname(path)):
                store.atomic_write(path, contracts.dumps(value, pretty=True) + "\n", mode=0o600)
        return value
    value = validate_contract(value)
    if value["project_id"] != project_id(root):
        raise VaultNamespaceError("namespace_project_mismatch")
    return value


def query_contract(root, query, write=False):
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_TEXT:
        raise VaultNamespaceError("namespace_query_invalid")
    namespace = load_contract(root, write=write)
    return {
        "schema_version": 1,
        "project_id": namespace["project_id"],
        "allowed_prefixes": namespace["allowed_prefixes"],
        "max_results": namespace["max_results"],
        "query": query.strip(),
        "query_digest": "sha256:" + hashlib.sha256(query.strip().encode("utf-8")).hexdigest(),
    }


def _read_input(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise VaultNamespaceError("vault_results_input_unsafe") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_INPUT_BYTES:
            raise VaultNamespaceError("vault_results_input_unsafe")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, MAX_INPUT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                raise VaultNamespaceError("vault_results_input_oversize")
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise VaultNamespaceError("vault_results_input_unsafe") from exc
    finally:
        os.close(descriptor)
    identity = (before.st_dev, before.st_ino)
    if (
        identity != (after.st_dev, after.st_ino)
        or identity != (current.st_dev, current.st_ino)
        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (current.st_size, current.st_mtime_ns, current.st_ctime_ns)
    ):
        raise VaultNamespaceError("vault_results_input_unsafe")
    payload = b"".join(chunks)
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise VaultNamespaceError("vault_results_input_malformed")
    rows = value.get("results") if isinstance(value, dict) else value
    if not isinstance(rows, list) or len(rows) > MAX_CANDIDATES:
        raise VaultNamespaceError("vault_results_invalid")
    return rows


def _safe_result(row, prefixes):
    if not isinstance(row, dict) or set(row) - {"path", "title", "summary", "score"}:
        return None, "malformed"
    path = row.get("path")
    title = row.get("title")
    summary = row.get("summary")
    score = row.get("score", 0)
    if (
        not isinstance(path, str)
        or not isinstance(title, str)
        or not isinstance(summary, str)
        or not title.strip()
        or not summary.strip()
        or len(path) > 500
        or len(title) > 300
        or len(summary) > MAX_TEXT
        or "\\" in path
        or path.startswith("/")
        or ".." in path.split("/")
        or not any(path.startswith(prefix) for prefix in prefixes)
    ):
        return None, "out_of_namespace" if isinstance(path, str) else "malformed"
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not -1000000 <= score <= 1000000:
        return None, "malformed"
    text_fields = (title, summary)
    if any(
        any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value)
        or not rows.memory_security_json(value)["ok"]
        or rows.has_secret_value(value)
        for value in text_fields
    ):
        return None, "unsafe_content"
    identity = hashlib.sha256((path + "\0" + summary).encode("utf-8")).hexdigest()
    return {
        "result_id": "vault_" + identity[:24],
        "path": path,
        "title": title.strip(),
        "summary": summary.strip(),
        "score": score,
    }, None


def _safe_run(root, run):
    run_dir = os.path.realpath(run if os.path.isabs(run) else os.path.join(root, run))
    if os.path.dirname(run_dir) != os.path.realpath(os.path.join(root, ".kimiflow")) or not os.path.isdir(run_dir):
        raise VaultNamespaceError("vault_run_invalid")
    return run_dir


def accept_results(root, run, input_path, write=False):
    namespace = load_contract(root, write=write)
    candidates = _read_input(input_path)
    reason_counts = {}
    unique = {}
    for raw in candidates:
        row, reason = _safe_result(raw, namespace["allowed_prefixes"])
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            continue
        if row["result_id"] in unique:
            reason_counts["duplicate"] = reason_counts.get("duplicate", 0) + 1
            continue
        unique[row["result_id"]] = row
    ranked = sorted(
        unique.values(),
        key=lambda row: (-float(row["score"]), row["path"], row["result_id"]),
    )
    accepted = ranked[:namespace["max_results"]]
    if len(ranked) > len(accepted):
        reason_counts["limit"] = len(ranked) - len(accepted)
    selection_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            [row["result_id"] for row in accepted],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt = {
        "schema_version": 1,
        "status": "accepted",
        "project_id": namespace["project_id"],
        "candidate_count": len(candidates),
        "accepted_count": len(accepted),
        "rejected_count": len(candidates) - len(accepted),
        "reason_counts": dict(sorted(reason_counts.items())),
        "accepted_ids": [row["result_id"] for row in accepted],
        "selection_digest": selection_digest,
        "stores_content": False,
    }
    if write:
        run_dir = _safe_run(root, run)
        recall = {
            "schema_version": 1,
            "project_id": namespace["project_id"],
            "selection_digest": selection_digest,
            "results": accepted,
        }
        lines = [
            "# Vault Recall\n\n",
            "Project namespace: %s\n" % namespace["project_id"],
            "Accepted: %s\n\n" % len(accepted),
        ]
        for row in accepted:
            lines.append("- [%s] %s — %s\n" % (row["result_id"], row["title"], row["summary"]))
        # Pin the validated run directory for the complete output set. A
        # concurrent pathname exchange may detach this descriptor, but can
        # never redirect recall content through a replacement symlink.
        with store.local_path_guard(os.path.realpath(root), run_dir):
            for name, data in (
                (RECALL_JSON_NAME, contracts.dumps(recall, pretty=True) + "\n"),
                (RECALL_MD_NAME, "".join(lines)),
                (RECEIPT_NAME, contracts.dumps(receipt, pretty=True) + "\n"),
            ):
                store.atomic_write(os.path.join(run_dir, name), data, mode=0o600)
    return receipt
