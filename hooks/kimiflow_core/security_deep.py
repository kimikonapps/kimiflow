"""Bounded, private deep-security receipts and deterministic quality gates.

This module deliberately does not discover, install, authenticate to, or invoke a
scanner.  Callers supply already-authorized local evidence through a small
executor boundary.  Missing evidence is a coverage gap, never a clean result.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile

from . import model_adapter, security, work_units


SCHEMA_VERSION = 1
MAX_WORKERS = 4
LANES = (
    "dependencies", "secrets", "sast", "iac", "container", "sbom", "provenance",
)
GAP_STATUSES = {
    "missing", "failed", "refused", "quota_limited", "timeout", "stale",
    "unsupported", "deferred", "budget_exceeded",
}
TERMINAL_STATUSES = GAP_STATUSES | {"complete", "findings", "not_applicable"}
USAGE_KEYS = model_adapter.USAGE_KEYS
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
RESULT_KEYS = {
    "schema_version", "contract_fingerprint", "cache_key", "result_seal",
    "status", "verdict", "receipts", "gaps", "findings", "usage",
    "cache_hits", "executed_units", "seal",
}
EVIDENCE_KEYS = {
    "schema_version", "contract_fingerprint", "surfaces",
}
EVIDENCE_SURFACE_KEYS = {"id", "status", "usage", "findings"}


class DeepSecurityError(security.SecurityError):
    def __init__(self, code):
        super().__init__(code)


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DeepSecurityError("deep_invalid") from exc


def digest(value):
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _seal(value):
    sealed = dict(value)
    sealed.pop("seal", None)
    sealed["seal"] = digest(sealed)
    return sealed


def _valid_seal(value):
    return isinstance(value, dict) and isinstance(value.get("seal"), str) and value["seal"] == _seal(value)["seal"]


def current_contract_fingerprint():
    """Bind caches/evals to the shipped engine, runtime contract and evaluator bytes."""
    components = {}
    for name, path in (
        ("security_deep", __file__),
        ("security", security.__file__),
        ("model_adapter", model_adapter.__file__),
        ("work_units", work_units.__file__),
    ):
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
                raise OSError
            with open(path, "rb") as handle:
                components[name] = digest(handle.read())
        except OSError as exc:
            raise DeepSecurityError("contract_fingerprint_unavailable") from exc
    return digest({
        "schema_version": SCHEMA_VERSION,
        "engine_contract": "security-deep-v1",
        "runtime_contract": "python-stdlib-v1",
        "evaluator_contract": "security-quality-v1",
        "components": components,
    })


def _usage(value, error="usage_invalid"):
    if not isinstance(value, dict) or set(value) != set(USAGE_KEYS):
        raise DeepSecurityError(error)
    normalized = model_adapter.normalize_usage(value)
    if normalized is None:
        raise DeepSecurityError(error)
    return normalized


def _zero_usage():
    return {key: 0 for key in USAGE_KEYS}


def _add_usage(left, right):
    return work_units.add_usage(left, right)


def _exceeds(actual, limit):
    return work_units.exceeds(actual, limit)


def _surface_digest(surface):
    return digest({key: surface[key] for key in (
        "id", "lane", "scope_digest", "guidance_digest", "provider_evidence_digest",
        "declared_budget",
    )})


def validate_plan(value):
    """Validate the public, content-free deep plan envelope."""
    required = {"schema_version", "worker_limit", "token_budget", "contract_fingerprint", "surfaces"}
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != SCHEMA_VERSION:
        raise DeepSecurityError("plan_invalid")
    workers = value.get("worker_limit")
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS:
        raise DeepSecurityError("worker_limit_invalid")
    budget = _usage(value.get("token_budget"), "budget_invalid")
    fingerprint = value.get("contract_fingerprint")
    if (
        not isinstance(fingerprint, str)
        or SHA_RE.fullmatch(fingerprint) is None
        or fingerprint != current_contract_fingerprint()
    ):
        raise DeepSecurityError("contract_fingerprint_invalid")
    surfaces = value.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces or len(surfaces) > 32:
        raise DeepSecurityError("surface_invalid")
    normalized, ids = [], set()
    total = _zero_usage()
    keys = {"id", "lane", "scope_digest", "guidance_digest", "provider_evidence_digest", "declared_budget"}
    for raw in surfaces:
        if not isinstance(raw, dict) or set(raw) != keys:
            raise DeepSecurityError("surface_invalid")
        if not isinstance(raw["id"], str) or ID_RE.fullmatch(raw["id"]) is None or raw["id"] in ids:
            raise DeepSecurityError("surface_invalid")
        if raw["lane"] not in LANES or any(SHA_RE.fullmatch(raw[key]) is None for key in (
            "scope_digest", "guidance_digest", "provider_evidence_digest",
        )):
            raise DeepSecurityError("surface_invalid")
        declared = _usage(raw["declared_budget"], "budget_invalid")
        total = _add_usage(total, declared)
        normalized.append({**raw, "declared_budget": declared})
        ids.add(raw["id"])
    # A larger plan is allowed: admission reserves the deterministic prefix.
    return {
        "schema_version": SCHEMA_VERSION, "worker_limit": workers, "token_budget": budget,
        "contract_fingerprint": fingerprint, "surfaces": normalized,
    }


def evidence_executor(plan, evidence):
    """Build a strict local executor whose evidence is bound to the admitted plan."""
    normalized = validate_plan(plan)
    if (
        not isinstance(evidence, dict)
        or set(evidence) != EVIDENCE_KEYS
        or evidence.get("schema_version") != SCHEMA_VERSION
        or evidence.get("contract_fingerprint") != normalized["contract_fingerprint"]
        or not isinstance(evidence.get("surfaces"), list)
    ):
        raise DeepSecurityError("evidence_invalid")
    responses = {}
    for raw in evidence["surfaces"]:
        if (
            not isinstance(raw, dict)
            or set(raw) != EVIDENCE_SURFACE_KEYS
            or not isinstance(raw.get("id"), str)
            or ID_RE.fullmatch(raw["id"]) is None
            or raw["id"] in responses
            or raw.get("status") not in TERMINAL_STATUSES
            or not isinstance(raw.get("findings"), list)
        ):
            raise DeepSecurityError("evidence_invalid")
        try:
            normalized_usage = _usage(raw["usage"], "evidence_invalid")
            for finding in raw["findings"]:
                security.validate_finding(finding)
        except security.SecurityError as exc:
            raise DeepSecurityError("evidence_invalid") from exc
        response = {
            "id": raw["id"],
            "status": raw["status"],
            "usage": normalized_usage,
            "findings": json.loads(_canonical(raw["findings"])),
        }
        responses[raw["id"]] = response
    expected_ids = {surface["id"] for surface in normalized["surfaces"]}
    if set(responses) != expected_ids:
        raise DeepSecurityError("evidence_invalid")
    for surface in normalized["surfaces"]:
        if surface["provider_evidence_digest"] != digest(responses[surface["id"]]):
            raise DeepSecurityError("evidence_binding_invalid")

    def execute(surface):
        response = responses[surface["id"]]
        return json.loads(_canonical({
            "status": response["status"],
            "usage": response["usage"],
            "findings": response["findings"],
        }))

    return execute


def _cache_path(root, key):
    return os.path.join(
        root, ".kimiflow", "security", "deep-cache",
        key.split(":", 1)[1] + ".json",
    )


def _read_private_cache(root, key, fingerprint):
    name = key.split(":", 1)[1] + ".json"
    try:
        with security._directory_chain(
            root, [".kimiflow", "security", "deep-cache"], False,
        ) as directory:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 2 * 1024 * 1024
            ):
                return None
            value = security._read_json_at(directory, name)
    except (OSError, security.SecurityError):
        return None
    if (
        not _valid_seal(value)
        or set(value) != {"schema_version", "cache_key", "contract_fingerprint", "result", "seal"}
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("cache_key") != key
        or value.get("contract_fingerprint") != fingerprint
        or not isinstance(value.get("result"), dict)
        or value["result"].get("cache_key") != key
    ):
        return None
    try:
        return validate_result(value["result"])
    except DeepSecurityError:
        return None


def _write_private_cache(root, key, fingerprint, result):
    payload = _seal({
        "schema_version": SCHEMA_VERSION, "cache_key": key,
        "contract_fingerprint": fingerprint, "result": result,
    })
    security._state_write(
        root, "deep-cache", key.split(":", 1)[1] + ".json", payload,
    )


def _gap(surface, status, usage=None):
    return {
        "surface_digest": _surface_digest(surface), "lane": surface["lane"], "status": status,
        "coverage": "gap", "finding_count": 0, "usage": usage or _zero_usage(),
    }


def _missing_lane_gap(lane, fingerprint):
    return {
        "surface_digest": digest({
            "contract_fingerprint": fingerprint,
            "lane": lane,
            "reason": "unplanned",
        }),
        "lane": lane,
        "status": "missing",
        "coverage": "gap",
        "finding_count": 0,
        "usage": _zero_usage(),
    }


def _result_seal(value):
    return digest({
        key: value[key] for key in (
            "schema_version", "contract_fingerprint", "cache_key", "status",
            "verdict", "receipts", "gaps", "findings",
        )
    })


def validate_result(value):
    if (
        not isinstance(value, dict)
        or set(value) != RESULT_KEYS
        or value.get("schema_version") != SCHEMA_VERSION
        or not _valid_seal(value)
        or SHA_RE.fullmatch(str(value.get("contract_fingerprint") or "")) is None
        or SHA_RE.fullmatch(str(value.get("cache_key") or "")) is None
        or SHA_RE.fullmatch(str(value.get("result_seal") or "")) is None
        or value.get("status") not in {"complete", "incomplete"}
        or value.get("verdict") not in {"clean", "findings", "incomplete"}
        or not isinstance(value.get("receipts"), list)
        or not isinstance(value.get("gaps"), list)
        or not isinstance(value.get("findings"), list)
        or isinstance(value.get("cache_hits"), bool)
        or value.get("cache_hits") not in (0, 1)
        or isinstance(value.get("executed_units"), bool)
        or not isinstance(value.get("executed_units"), int)
        or value["executed_units"] < 0
    ):
        raise DeepSecurityError("result_invalid")
    usage = _usage(value["usage"])
    receipts = []
    seen = set()
    for raw in value["receipts"]:
        if (
            not isinstance(raw, dict)
            or set(raw) != {
                "surface_digest", "lane", "status", "coverage",
                "finding_count", "usage",
            }
            or SHA_RE.fullmatch(str(raw.get("surface_digest") or "")) is None
            or raw["surface_digest"] in seen
            or raw.get("lane") not in LANES
            or raw.get("status") not in TERMINAL_STATUSES
            or raw.get("coverage") not in {"complete", "gap"}
            or isinstance(raw.get("finding_count"), bool)
            or not isinstance(raw.get("finding_count"), int)
            or raw["finding_count"] < 0
        ):
            raise DeepSecurityError("result_invalid")
        row_usage = _usage(raw["usage"])
        gap = raw["status"] in GAP_STATUSES
        if (
            gap != (raw["coverage"] == "gap")
            or (gap and raw["finding_count"] != 0)
            or (raw["status"] == "findings" and raw["finding_count"] == 0)
        ):
            raise DeepSecurityError("result_invalid")
        seen.add(raw["surface_digest"])
        receipts.append({**raw, "usage": row_usage})
    if set(row["lane"] for row in receipts) != set(LANES):
        raise DeepSecurityError("result_invalid")
    gaps = sorted(
        row["surface_digest"] for row in receipts if row["coverage"] == "gap"
    )
    if (
        value["gaps"] != gaps
        or any(SHA_RE.fullmatch(str(item or "")) is None for item in value["gaps"])
        or sum(row["finding_count"] for row in receipts) != len(value["findings"])
    ):
        raise DeepSecurityError("result_invalid")
    try:
        for finding in value["findings"]:
            security.validate_finding(finding)
    except security.SecurityError as exc:
        raise DeepSecurityError("result_invalid") from exc
    expected_status = "incomplete" if gaps else "complete"
    expected_verdict = (
        "incomplete" if gaps else ("findings" if value["findings"] else "clean")
    )
    if value["status"] != expected_status or value["verdict"] != expected_verdict:
        raise DeepSecurityError("result_invalid")
    if value["cache_hits"] == 1:
        if usage != _zero_usage() or value["executed_units"] != 0:
            raise DeepSecurityError("result_invalid")
    else:
        observed = _zero_usage()
        for row in receipts:
            observed = _add_usage(observed, row["usage"])
        if usage != observed:
            raise DeepSecurityError("result_invalid")
    if (
        value["contract_fingerprint"] != current_contract_fingerprint()
        or value["result_seal"] != _result_seal(value)
    ):
        raise DeepSecurityError("result_invalid")
    return value


def _invoke_work_unit(executor, surface, root):
    unit_plan = {
        "schema_version": 1,
        "budget": surface["declared_budget"],
        "units": [{
            "id": surface["id"],
            "kind": "review",
            "dependencies": [],
            "declared_budget": surface["declared_budget"],
            "allowed_tools": ["Read", "Glob", "Grep"],
            "idempotency_key": _surface_digest(surface),
            "timeout_seconds": 120,
            "input": {
                "schema_version": 1,
                "security_surface": {
                    key: surface[key] for key in (
                        "id", "lane", "scope_digest", "guidance_digest",
                        "provider_evidence_digest",
                    )
                },
                "output_contract": {
                    "status": sorted(TERMINAL_STATUSES),
                    "findings": "security-findings-v1",
                },
            },
        }],
    }
    try:
        execution = work_units.execute_plan(unit_plan, executor, root=root)
    except work_units.WorkUnitError as exc:
        status = {
            "unit_timeout": "timeout",
            "turn_timeout": "timeout",
            "refusal": "refused",
            "quota_exceeded": "quota_limited",
            "budget_exceeded": "budget_exceeded",
        }.get(exc.code, "failed")
        return {
            "status": status,
            "usage": exc.usage or _zero_usage(),
            "findings": [],
        }
    output = execution["synthesis"][0]["output"]
    if isinstance(output, dict) and isinstance(output.get("result"), str):
        try:
            output = json.loads(output["result"])
        except (TypeError, ValueError, json.JSONDecodeError):
            output = None
    if not isinstance(output, dict):
        return {
            "status": "failed",
            "usage": execution["usage"],
            "findings": [],
        }
    return {
        "status": output.get("status"),
        "usage": execution["usage"],
        "findings": output.get("findings"),
    }


def _invoke(executor, surface, root):
    if executor is None:
        return {"status": "missing", "usage": _zero_usage(), "findings": []}
    if hasattr(executor, "execute") or hasattr(executor, "start"):
        return _invoke_work_unit(executor, surface, root)
    try:
        response = executor(surface)
    except TimeoutError:
        return {"status": "timeout", "usage": _zero_usage(), "findings": []}
    except Exception:
        return {"status": "failed", "usage": _zero_usage(), "findings": []}
    if not isinstance(response, dict):
        return {"status": "failed", "usage": _zero_usage(), "findings": []}
    return response


def run_deep(plan, executor=None, *, root="."):
    """Execute the admitted ordered prefix and synthesize an honest receipt.

    The admission reservation is intentionally separate from observed accounting:
    a provider can overrun after a call, but no subsequent call is dispatched.
    """
    normalized = validate_plan(plan)
    root = os.path.realpath(root)
    cache_key = digest({
        "plan": normalized,
        "contract_fingerprint": current_contract_fingerprint(),
    })
    cached = _read_private_cache(
        root, cache_key, normalized["contract_fingerprint"],
    )
    if cached is not None:
        reused = dict(cached)
        reused["cache_hits"] = 1
        reused["executed_units"] = 0
        reused["usage"] = _zero_usage()
        return validate_result(_seal(reused))

    reserved, observed, findings = _zero_usage(), _zero_usage(), []
    receipt_by_id = {}
    admitted = []
    admission_closed = False
    for surface in normalized["surfaces"]:
        if (
            admission_closed
            or len(admitted) >= normalized["worker_limit"]
            or _exceeds(
                _add_usage(reserved, surface["declared_budget"]),
                normalized["token_budget"],
            )
        ):
            receipt_by_id[surface["id"]] = _gap(surface, "deferred")
            admission_closed = True
        else:
            admitted.append(surface)
            reserved = _add_usage(reserved, surface["declared_budget"])
    stop = False
    executed_units = 0
    for surface in admitted:
        if stop:
            receipt_by_id[surface["id"]] = _gap(surface, "deferred")
            continue
        executed_units += 1
        response = _invoke(executor, surface, root)
        status = response.get("status")
        try:
            usage = _usage(response.get("usage"))
        except DeepSecurityError:
            usage, status = _zero_usage(), "failed"
        observed = _add_usage(observed, usage)
        if _exceeds(usage, surface["declared_budget"]) or _exceeds(observed, normalized["token_budget"]):
            receipt_by_id[surface["id"]] = _gap(
                surface, "budget_exceeded", usage,
            )
            stop = True
            continue
        if status not in TERMINAL_STATUSES:
            status = "failed"
        if status in GAP_STATUSES:
            receipt_by_id[surface["id"]] = _gap(surface, status, usage)
            continue
        raw_findings = response.get("findings", [])
        if not isinstance(raw_findings, list):
            receipt_by_id[surface["id"]] = _gap(surface, "failed", usage)
            continue
        try:
            for finding in raw_findings:
                security.validate_finding(finding)
        except security.SecurityError:
            receipt_by_id[surface["id"]] = _gap(surface, "failed", usage)
            continue
        if status == "findings" and not raw_findings:
            receipt_by_id[surface["id"]] = _gap(
                surface, "failed", usage,
            )
            continue
        if raw_findings:
            status = "findings"
        receipt_by_id[surface["id"]] = {
            "surface_digest": _surface_digest(surface), "lane": surface["lane"], "status": status,
            "coverage": "complete", "finding_count": len(raw_findings), "usage": usage,
        }
        findings.extend(raw_findings)
    receipts = [
        receipt_by_id[surface["id"]] for surface in normalized["surfaces"]
    ]
    planned_lanes = {surface["lane"] for surface in normalized["surfaces"]}
    receipts.extend(
        _missing_lane_gap(lane, normalized["contract_fingerprint"])
        for lane in LANES if lane not in planned_lanes
    )
    gaps = sorted(
        receipt["surface_digest"]
        for receipt in receipts if receipt["coverage"] == "gap"
    )
    result = {
        "schema_version": SCHEMA_VERSION, "contract_fingerprint": normalized["contract_fingerprint"],
        "cache_key": cache_key, "status": "incomplete" if gaps else "complete",
        "verdict": "incomplete" if gaps else ("findings" if findings else "clean"),
        "receipts": receipts, "gaps": gaps, "findings": findings, "usage": observed,
        "cache_hits": 0, "executed_units": executed_units,
    }
    result["result_seal"] = _result_seal(result)
    result = _seal(result)
    result = validate_result(result)
    _write_private_cache(
        root, cache_key, normalized["contract_fingerprint"], result,
    )
    return result


def portable_artifact(result):
    """Project a sealed allowlist; raw findings and paths never cross this boundary."""
    result = validate_result(result)
    lanes = [{key: receipt[key] for key in ("surface_digest", "lane", "status", "coverage", "finding_count", "usage")}
             for receipt in result.get("receipts", [])]
    artifact = {
        "schema_version": SCHEMA_VERSION, "artifact_type": "security-deep-portable-v1",
        "contract_fingerprint": result["contract_fingerprint"], "result_seal": result["result_seal"],
        "status": result["status"], "verdict": result["verdict"], "usage": result["usage"],
        "counts": {"lanes": len(lanes), "findings": sum(row["finding_count"] for row in lanes), "gaps": len(result["gaps"])},
        "lanes": lanes, "gaps": list(result["gaps"]),
        "privacy": {"allowlist_only": True, "raw_findings": False, "absolute_paths": False, "secrets": False},
    }
    return _seal(artifact)


def _committed_diff_snapshot(root, scope, base, destination):
    if not isinstance(base, str) or re.fullmatch(r"[0-9a-f]{40,64}", base) is None:
        raise DeepSecurityError("diff_base_invalid")
    try:
        completed = subprocess.run(
            [
                "git", "-C", root, "-c", "core.quotepath=false", "diff",
                "--no-ext-diff", "--no-textconv", "--name-only", "-z",
                "--diff-filter=ACMRT", base + "...HEAD", "--",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeepSecurityError("diff_inventory_failed") from exc
    if completed.returncode != 0 or len(completed.stdout) > 2 * 1024 * 1024:
        raise DeepSecurityError("diff_inventory_failed")
    scope_prefix = "" if scope == "." else scope.rstrip("/") + "/"
    total = 0
    paths = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            rel = raw.decode("utf-8")
            rel = security._safe_relative(rel)
        except (UnicodeError, security.SecurityError) as exc:
            raise DeepSecurityError("diff_inventory_invalid") from exc
        if security._is_internal(rel):
            continue
        if scope != "." and rel != scope and not rel.startswith(scope_prefix):
            continue
        source = os.path.join(root, rel)
        try:
            info = os.lstat(source)
            if (
                not stat.S_ISREG(info.st_mode)
                or os.path.relpath(os.path.realpath(source), root).replace(os.sep, "/") != rel
            ):
                raise OSError
            payload = security._read_regular_bytes(
                source, expected=info, maximum=security.MAX_FILE_BYTES,
            )
        except OSError as exc:
            raise DeepSecurityError("diff_inventory_invalid") from exc
        total += len(payload)
        if total > security.MAX_SCOPE_BYTES:
            raise DeepSecurityError("diff_inventory_too_large")
        target = os.path.join(destination, rel)
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        paths.append(rel)
    return paths


def _deep_from_run3(manifest, coverage, findings_artifact, cache_root):
    receipts = {
        row["lane"]: row
        for row in coverage.get("receipts", [])
        if isinstance(row, dict) and row.get("lane") in {"secrets", "dependencies"}
    }
    findings = findings_artifact.get("findings", [])
    if not isinstance(findings, list):
        raise DeepSecurityError("run3_findings_invalid")
    try:
        for finding in findings:
            security.validate_finding(finding)
    except security.SecurityError as exc:
        raise DeepSecurityError("run3_findings_invalid") from exc
    lane_findings = {}
    for lane in LANES:
        receipt = receipts.get(lane)
        provider = receipt.get("provider") if receipt else None
        lane_findings[lane] = [
            row for row in findings
            if isinstance(row.get("provenance"), dict)
            and row["provenance"].get("provider") == provider
        ] if provider is not None else []
    common_scope = digest({
        "scope_digest": manifest["scope_digest"],
        "content_digest": manifest["content_digest"],
        "revision": manifest["revision"],
    })
    zero = _zero_usage()
    surfaces = []
    for index, lane in enumerate(LANES):
        receipt = receipts.get(lane)
        surfaces.append({
            "id": "ci-%s" % lane,
            "lane": lane,
            "scope_digest": common_scope,
            "guidance_digest": manifest["guidance_digest"],
            "provider_evidence_digest": digest(
                {
                    "receipt": receipt or {"lane": lane, "status": "missing"},
                    "findings_digest": digest(lane_findings[lane]),
                },
            ),
            "declared_budget": zero,
        })
    plan = {
        "schema_version": SCHEMA_VERSION,
        "worker_limit": MAX_WORKERS,
        "token_budget": zero,
        "contract_fingerprint": current_contract_fingerprint(),
        "surfaces": surfaces,
    }
    gap_map = {"unauthorized": "refused", "output_limit": "failed"}

    def executor(surface):
        receipt = receipts.get(surface["lane"])
        if receipt is None:
            return {"status": "missing", "usage": zero, "findings": []}
        status = gap_map.get(receipt["status"], receipt["status"])
        selected_findings = lane_findings[surface["lane"]]
        if selected_findings and status in {"complete", "findings"}:
            status = "findings"
        elif status == "findings":
            status = "failed"
        return {"status": status, "usage": zero, "findings": selected_findings}

    return run_deep(plan, executor, root=cache_root)


def advisory_diff_artifact(path=".", *, base=None):
    """Run a credential-free local diff scan and return only portable evidence."""
    root, scope, _target = security.resolve_scan_root(path, "diff")
    if base:
        with tempfile.TemporaryDirectory(prefix="kimiflow-security-diff-") as temporary:
            _committed_diff_snapshot(root, scope, base, temporary)
            scan = security.run_scan("scan", temporary)
            manifest = security.load_scan_artifact(
                temporary, scan["scan_id"], "SECURITY-SCAN-MANIFEST.json",
            )
            coverage = security.load_scan_artifact(
                temporary, scan["scan_id"], "SECURITY-COVERAGE.json",
            )
            findings = security.load_scan_artifact(
                temporary, scan["scan_id"], "SECURITY-FINDINGS.json",
            )
            result = _deep_from_run3(manifest, coverage, findings, temporary)
            return portable_artifact(result)
    scan = security.run_scan("diff", path)
    manifest = security.load_scan_artifact(
        root, scan["scan_id"], "SECURITY-SCAN-MANIFEST.json",
    )
    coverage = security.load_scan_artifact(
        root, scan["scan_id"], "SECURITY-COVERAGE.json",
    )
    findings = security.load_scan_artifact(
        root, scan["scan_id"], "SECURITY-FINDINGS.json",
    )
    return portable_artifact(
        _deep_from_run3(manifest, coverage, findings, root),
    )


METRICS = (
    "threat_model_coverage", "finding_precision", "reachability", "refusal_fallback",
    "fix_verification", "false_clean_prevention", "token_cost",
)
RATIO_METRICS = set(METRICS) - {"token_cost"}
TRUSTED_FIXTURE_IDENTITY = "sha256:ac70d75fba541dbce91b22259e9cbf7ce9a9520429788771b177d4bf70f36ba7"
TRUSTED_PROMOTION_POLICY = {
    "minimum_samples": {
        "threat_model_coverage": 8,
        "finding_precision": 1,
        "reachability": 4,
        "refusal_fallback": 1,
        "fix_verification": 1,
        "false_clean_prevention": 2,
        "token_cost": 4,
    },
    "thresholds": {
        "threat_model_coverage": 0.875,
        "finding_precision": 1.0,
        "reachability": 1.0,
        "refusal_fallback": 1.0,
        "fix_verification": 1.0,
        "false_clean_prevention": 1.0,
    },
    "max_token_cost": 16,
}


def _metrics(value, error):
    if not isinstance(value, dict) or set(value) != set(METRICS):
        raise DeepSecurityError(error)
    normalized = {}
    for key in METRICS:
        metric = value[key]
        if isinstance(metric, bool) or not isinstance(metric, (int, float)):
            raise DeepSecurityError(error)
        if key == "token_cost":
            if not isinstance(metric, int) or metric < 0:
                raise DeepSecurityError(error)
        elif not 0 <= metric <= 1:
            raise DeepSecurityError(error)
        normalized[key] = metric
    return normalized


def validate_eval_candidate(value):
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema_version", "contract_fingerprint", "metrics",
            "expected_metrics", "samples", "fixture_digest",
            "evidence_digest", "seal",
        }
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("contract_fingerprint") != current_contract_fingerprint()
        or SHA_RE.fullmatch(str(value.get("fixture_digest") or "")) is None
        or SHA_RE.fullmatch(str(value.get("evidence_digest") or "")) is None
        or not _valid_seal(value)
    ):
        raise DeepSecurityError("eval_invalid")
    _metrics(value.get("metrics"), "eval_invalid")
    _metrics(value.get("expected_metrics"), "eval_invalid")
    samples = value.get("samples")
    if (
        not isinstance(samples, dict)
        or set(samples) != set(METRICS)
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 1
            for count in samples.values()
        )
    ):
        raise DeepSecurityError("eval_invalid")
    return value


def _holdout_plan(case, stage):
    provider_result = case["provider_results"][stage]
    scope = digest({"case": case["id"], "stage": stage})
    surface = {
        "id": case["id"] + "-" + stage,
        "lane": "sast",
        "scope_digest": scope,
        "guidance_digest": digest({"holdout": "security-quality-v1"}),
        "provider_evidence_digest": digest({
            "case": case["id"], "stage": stage, "provider_result": provider_result,
        }),
        "declared_budget": case["usage"],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "worker_limit": 1,
        "token_budget": case["usage"],
        "contract_fingerprint": current_contract_fingerprint(),
        "surfaces": [surface],
    }, surface


def _holdout_finding(case, stage):
    return security._finding(
        "kimiflow-holdout",
        "1",
        case["id"] + "-" + stage,
        "synthetic holdout finding",
        "high",
        "fixture.py",
        1,
        digest({"case": case["id"], "stage": stage}),
        source="synthetic_untrusted_input",
        sink="synthetic_sensitive_sink",
        reachability=case["provider_results"][stage]["reachability"],
        impact="synthetic_holdout_only",
        remediation="synthetic_holdout_only",
        confidence="high",
        proof_gaps=[],
    )


def _exercise_holdout_case(case, stage):
    plan, surface = _holdout_plan(case, stage)
    provider_result = case["provider_results"][stage]
    finding = (
        _holdout_finding(case, stage)
        if provider_result["reported_finding"] else None
    )

    def executor(_surface):
        return {
            "status": provider_result["status"],
            "usage": case["usage"],
            "findings": [finding] if finding is not None else [],
        }

    try:
        with tempfile.TemporaryDirectory(
            prefix="kimiflow-security-holdout-",
        ) as root:
            result = run_deep(plan, executor, root=root)
    except Exception as exc:
        raise DeepSecurityError("holdout_execution_failed") from exc
    target_digest = _surface_digest(surface)
    target = next(
        (
            receipt for receipt in result["receipts"]
            if receipt["surface_digest"] == target_digest
        ),
        None,
    )
    if target is None:
        raise DeepSecurityError("holdout_execution_failed")
    reported = bool(result["findings"])
    case_verdict = (
        "incomplete" if target["coverage"] == "gap"
        else ("findings" if reported else "clean")
    )
    if case["kind"] == "vulnerable":
        reachability_correct = (
            len(result["findings"]) == 1
            and result["findings"][0]["reachability"] == "reachable"
        )
    elif case["kind"] == "refusal":
        reachability_correct = target["status"] == "refused" and not reported
    else:
        reachability_correct = not reported
    return {
        "result_seal": result["result_seal"],
        "status": target["status"],
        "verdict": case_verdict,
        "reported_finding": reported,
        "reachability_correct": reachability_correct,
        "usage": result["usage"],
    }


def evaluate_holdout(fixture):
    """Exercise Deep Security against the explicit oracle fixture."""
    if not isinstance(fixture, dict) or set(fixture) != {"schema_version", "contract_fingerprint", "cases", "expected_metrics"}:
        raise DeepSecurityError("holdout_invalid")
    if fixture.get("schema_version") != SCHEMA_VERSION or not isinstance(fixture["cases"], list) or not fixture["cases"]:
        raise DeepSecurityError("holdout_invalid")
    if fixture.get("contract_fingerprint") != current_contract_fingerprint():
        raise DeepSecurityError("holdout_contract_stale")
    expected = _metrics(
        fixture["expected_metrics"], "holdout_expected_invalid",
    )
    required = covered = tp = fp = reach_total = reach_ok = refusal_total = refusal_ok = fixed_total = fixed_ok = unsafe_total = unsafe_ok = token_cost = 0
    kinds, ids = set(), set()
    observations = []
    for case in fixture["cases"]:
        keys = {
            "id", "kind", "required_threat_fields",
            "covered_threat_fields", "usage", "provider_results",
        }
        if not isinstance(case, dict) or set(case) != keys or case["kind"] not in {"safe", "vulnerable", "refusal", "fixed"}:
            raise DeepSecurityError("holdout_case_invalid")
        if (
            not isinstance(case["id"], str)
            or ID_RE.fullmatch(case["id"]) is None
            or case["id"] in ids
            or not isinstance(case["required_threat_fields"], list)
            or not case["required_threat_fields"]
            or len(case["required_threat_fields"]) != len(set(case["required_threat_fields"]))
            or any(
                not isinstance(field, str) or not field
                for field in case["required_threat_fields"]
            )
            or not isinstance(case["covered_threat_fields"], list)
            or len(case["covered_threat_fields"]) != len(set(case["covered_threat_fields"]))
            or any(
                not isinstance(field, str) or not field
                for field in case["covered_threat_fields"]
            )
            or not isinstance(case["provider_results"], dict)
            or set(case["provider_results"]) != (
                {"before", "after"} if case["kind"] == "fixed" else {"after"}
            )
        ):
            raise DeepSecurityError("holdout_case_invalid")
        if not set(case["covered_threat_fields"]).issubset(set(case["required_threat_fields"])):
            raise DeepSecurityError("holdout_case_invalid")
        _usage(case["usage"])
        for provider_result in case["provider_results"].values():
            if (
                not isinstance(provider_result, dict)
                or set(provider_result) != {
                    "status", "reported_finding", "reachability",
                }
                or provider_result["status"] not in TERMINAL_STATUSES
                or not isinstance(provider_result["reported_finding"], bool)
                or provider_result["reachability"] not in {
                    "reachable", "not_applicable",
                }
                or (
                    provider_result["reported_finding"]
                    != (provider_result["reachability"] == "reachable")
                )
                or (
                    provider_result["status"] in GAP_STATUSES
                    and provider_result["reported_finding"]
                )
            ):
                raise DeepSecurityError("holdout_case_invalid")
        before = (
            _exercise_holdout_case(case, "before")
            if case["kind"] == "fixed" else None
        )
        observed = _exercise_holdout_case(case, "after")
        usage = observed["usage"]
        if before is not None:
            usage = _add_usage(before["usage"], usage)
        ids.add(case["id"])
        required += len(case["required_threat_fields"]); covered += len(case["covered_threat_fields"])
        kinds.add(case["kind"]); token_cost += usage["input_tokens"] + usage["output_tokens"]
        if observed["reported_finding"]:
            if case["kind"] == "vulnerable": tp += 1
            else: fp += 1
        reach_total += 1
        reach_ok += int(observed["reachability_correct"] is True)
        if case["kind"] == "refusal":
            refusal_total += 1
            refusal_ok += int(observed["verdict"] == "incomplete")
        if case["kind"] == "fixed":
            fixed_total += 1
            fixed_ok += int(
                before is not None
                and before["reported_finding"] is True
                and observed["reported_finding"] is False
                and observed["verdict"] == "clean"
            )
        if case["kind"] == "vulnerable" or observed["verdict"] == "incomplete":
            unsafe_total += 1
            unsafe_ok += int(observed["verdict"] != "clean")
        observations.append({
            "id": case["id"],
            "kind": case["kind"],
            "result_seal": observed["result_seal"],
            "before_result_seal": before["result_seal"] if before else None,
            "status": observed["status"],
            "verdict": observed["verdict"],
            "reported_finding": observed["reported_finding"],
            "reachability_correct": observed["reachability_correct"],
            "usage": usage,
        })
    if kinds != {"safe", "vulnerable", "refusal", "fixed"} or not all((required, reach_total, refusal_total, fixed_total, unsafe_total)):
        raise DeepSecurityError("holdout_dimension_missing")
    metrics = {
        "threat_model_coverage": covered / required,
        "finding_precision": tp / (tp + fp) if tp + fp else 1.0,
        "reachability": reach_ok / reach_total, "refusal_fallback": refusal_ok / refusal_total,
        "fix_verification": fixed_ok / fixed_total, "false_clean_prevention": unsafe_ok / unsafe_total,
        "token_cost": token_cost,
    }
    if any(metrics[key] != expected[key] for key in METRICS):
        raise DeepSecurityError("holdout_expected_mismatch")
    samples = {
        "threat_model_coverage": required,
        "finding_precision": tp + fp,
        "reachability": reach_total,
        "refusal_fallback": refusal_total,
        "fix_verification": fixed_total,
        "false_clean_prevention": unsafe_total,
        "token_cost": len(fixture["cases"]),
    }
    return validate_eval_candidate(_seal({
        "schema_version": SCHEMA_VERSION,
        "contract_fingerprint": fixture["contract_fingerprint"],
        "fixture_digest": digest(fixture),
        "evidence_digest": digest(observations),
        "metrics": metrics,
        "expected_metrics": expected,
        "samples": samples,
    }))


def _promotion_block(reason):
    return _seal({
        "schema_version": SCHEMA_VERSION,
        "verdict": "BLOCK",
        "reason": reason,
    })


def _fixture_identity_digest(fixture):
    if not isinstance(fixture, dict):
        raise DeepSecurityError("holdout_invalid")
    identity = dict(fixture)
    identity.pop("contract_fingerprint", None)
    return digest(identity)


def promotion(candidate, baseline, fixture=None):
    """Fail closed: every current seal, oracle, sample and policy condition is required."""
    try:
        candidate = validate_eval_candidate(candidate)
    except DeepSecurityError:
        return _promotion_block("candidate_invalid")
    required = {
        "schema_version", "contract_fingerprint", "minimum_samples",
        "thresholds", "max_token_cost", "fixture_digest", "seal",
    }
    if (
        not isinstance(baseline, dict)
        or set(baseline) != required
        or baseline.get("schema_version") != SCHEMA_VERSION
        or not _valid_seal(baseline)
    ):
        return _promotion_block("seal_or_baseline_invalid")
    if fixture is None:
        return _promotion_block("fixture_required")
    try:
        if _fixture_identity_digest(fixture) != TRUSTED_FIXTURE_IDENTITY:
            return _promotion_block("fixture_untrusted")
    except DeepSecurityError:
        return _promotion_block("fixture_invalid")
    if (
        candidate.get("contract_fingerprint")
        != baseline.get("contract_fingerprint")
        or baseline.get("contract_fingerprint")
        != current_contract_fingerprint()
    ):
        return _promotion_block("stale_contract")
    try:
        fixture_digest = digest(fixture)
        recomputed = evaluate_holdout(fixture)
    except DeepSecurityError:
        return _promotion_block("fixture_invalid")
    if (
        SHA_RE.fullmatch(str(baseline.get("fixture_digest") or "")) is None
        or baseline["fixture_digest"] != fixture_digest
        or candidate.get("fixture_digest") != fixture_digest
    ):
        return _promotion_block("fixture_mismatch")
    if candidate != recomputed:
        return _promotion_block("candidate_evidence_mismatch")
    if candidate.get("metrics") != candidate.get("expected_metrics"):
        return _promotion_block("oracle_mismatch")
    if (
        not isinstance(baseline["minimum_samples"], dict)
        or set(baseline["minimum_samples"]) != set(METRICS)
        or any(
            isinstance(count, bool) or not isinstance(count, int) or count < 1
            for count in baseline["minimum_samples"].values()
        )
        or not isinstance(baseline["thresholds"], dict)
        or set(baseline["thresholds"]) != RATIO_METRICS
        or any(
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0 <= threshold <= 1
            for threshold in baseline["thresholds"].values()
        )
        or isinstance(baseline["max_token_cost"], bool)
        or not isinstance(baseline["max_token_cost"], int)
        or baseline["max_token_cost"] < 0
    ):
        return _promotion_block("policy_invalid")
    policy = {
        "minimum_samples": baseline["minimum_samples"],
        "thresholds": baseline["thresholds"],
        "max_token_cost": baseline["max_token_cost"],
    }
    if policy != TRUSTED_PROMOTION_POLICY:
        return _promotion_block("policy_untrusted")
    for metric in METRICS:
        if candidate.get("samples", {}).get(metric, 0) < baseline["minimum_samples"][metric]:
            return _promotion_block("insufficient_samples:" + metric)
    for metric, threshold in baseline["thresholds"].items():
        if candidate["metrics"].get(metric, -1) < threshold:
            return _promotion_block("regression:" + metric)
    if candidate["metrics"].get("token_cost", float("inf")) > baseline["max_token_cost"]:
        return _promotion_block("regression:token_cost")
    return _seal({"schema_version": SCHEMA_VERSION, "verdict": "PROMOTE", "candidate_seal": candidate["seal"], "contract_fingerprint": candidate["contract_fingerprint"]})
