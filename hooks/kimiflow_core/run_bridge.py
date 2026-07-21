"""Single-shot local JSON-stdio bridge for Kimiflow run control."""

import argparse
import contextlib
import io
import json
import os
import re
import stat
import sys

from . import active_run, phase_context, readiness, scorecard, workspace_preflight


class BridgeError(ValueError):
    def __init__(self, code, message=None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


RECEIPT_NAME = "RUN-BRIDGE.json"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 512 * 1024
MAX_ACTIONS = 256
OPERATIONS = {"append-item", "mark-built", "mark-accepted", "mark-rejected", "drop-item"}
METHODS = {"run/readiness", "run/context", "run/scorecard", "run/mutate"}
ITEM_ID_RE = re.compile(r"^item_[0-9]{3,12}$")
RESULT_KEYS = {"status", "item_id", "item_status", "item_counts"}
COUNT_KEYS = {"total", "pending", "built", "accepted", "rejected", "dropped", "open"}


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _empty_receipt():
    return {"schema_version": 1, "sequence": 0, "actions": []}


def _valid_result(value, prepared=False):
    if prepared and value == {}:
        return True
    if not isinstance(value, dict) or set(value) != RESULT_KEYS:
        return False
    if value.get("status") not in ("item_appended", "item_updated"):
        return False
    if ITEM_ID_RE.fullmatch(str(value.get("item_id", ""))) is None:
        return False
    if value.get("item_status") not in ("pending", "built", "accepted", "rejected", "dropped"):
        return False
    counts = value.get("item_counts")
    if not isinstance(counts, dict) or set(counts) - COUNT_KEYS:
        return False
    return all(not isinstance(item, bool) and isinstance(item, int) and 0 <= item <= 1000000000000 for item in counts.values())


def _read_receipt(run_descriptor):
    flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
    descriptor = None
    try:
        named = os.stat(RECEIPT_NAME, dir_fd=run_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode) or named.st_size > MAX_RECEIPT_BYTES:
            raise BridgeError("receipt_unsafe")
        descriptor = os.open(RECEIPT_NAME, flags, dir_fd=run_descriptor)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise BridgeError("receipt_exchanged")
        payload = os.read(descriptor, MAX_RECEIPT_BYTES + 1)
        if len(payload) > MAX_RECEIPT_BYTES:
            raise BridgeError("receipt_oversize")
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except FileNotFoundError:
        return _empty_receipt()
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, BridgeError):
            raise
        raise BridgeError("receipt_malformed")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict) or set(value) != {"schema_version", "sequence", "actions"} or value.get("schema_version") != 1:
        raise BridgeError("receipt_contract_invalid")
    sequence = value.get("sequence")
    actions = value.get("actions")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0 or not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        raise BridgeError("receipt_contract_invalid")
    seen = set()
    for row in actions:
        if not isinstance(row, dict) or set(row) != {"action_id", "request_fingerprint", "operation", "status", "result"}:
            raise BridgeError("receipt_contract_invalid")
        if not active_run.valid_action_id(row.get("action_id")) or row["action_id"] in seen:
            raise BridgeError("receipt_contract_invalid")
        if row.get("status") not in ("prepared", "completed") or row.get("operation") not in OPERATIONS:
            raise BridgeError("receipt_contract_invalid")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("request_fingerprint", ""))) is None or not _valid_result(row.get("result"), prepared=row.get("status") == "prepared"):
            raise BridgeError("receipt_contract_invalid")
        seen.add(row["action_id"])
    return value


def _write_receipt(run_descriptor, value):
    if len(value.get("actions", [])) > MAX_ACTIONS:
        raise BridgeError("receipt_full")
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    if len(payload) > MAX_RECEIPT_BYTES:
        raise BridgeError("receipt_oversize")
    try:
        workspace_preflight.atomic_directory_write(run_descriptor, RECEIPT_NAME, payload)
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        descriptor = os.open(RECEIPT_NAME, flags, dir_fd=run_descriptor)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise BridgeError("receipt_write_failed", exc.__class__.__name__)


def _owner_required(active):
    owner = active_run.valid_owner(active.get("owner"))
    caller = active_run.shell_session_identity()
    if owner is None or caller is None:
        raise BridgeError("owner_identity_missing")
    if not active_run.same_session(owner, caller):
        raise BridgeError("owner_conflict")


def _validate_request(value):
    if not isinstance(value, dict) or set(value) != {"schema_version", "method", "params"}:
        raise BridgeError("request_shape_invalid")
    if value.get("schema_version") != 1 or value.get("method") not in METHODS or not isinstance(value.get("params"), dict):
        raise BridgeError("request_contract_invalid")
    return value


def _mutation_argv(root, action_id, operation, arguments):
    if not isinstance(arguments, dict):
        raise BridgeError("arguments_invalid")
    if operation == "append-item":
        if set(arguments) - {"title", "kind"}:
            raise BridgeError("arguments_invalid")
        title = arguments.get("title")
        kind = arguments.get("kind", "change")
        if not isinstance(title, str) or not title or len(title) > 4000 or not isinstance(kind, str) or not kind or len(kind) > 80:
            raise BridgeError("arguments_invalid")
        return [operation, "--root", root, "--title", title, "--kind", kind, "--action-id", action_id, "--write"]
    allowed = {"id", "reason"} if operation in ("mark-rejected", "drop-item") else {"id"}
    if set(arguments) != allowed:
        raise BridgeError("arguments_invalid")
    item_id = arguments.get("id")
    if not isinstance(item_id, str) or ITEM_ID_RE.fullmatch(item_id) is None:
        raise BridgeError("arguments_invalid")
    argv = [operation, "--root", root, "--id", item_id]
    if "reason" in allowed:
        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason or len(reason) > 4000:
            raise BridgeError("arguments_invalid")
        argv.extend(["--reason", reason])
    return argv + ["--action-id", action_id, "--write"]


def _delegate(root, action_id, operation, arguments):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = active_run.main(_mutation_argv(root, action_id, operation, arguments))
    if (code or 0) != 0:
        raise BridgeError("mutation_failed", "delegated_%s_failed" % operation)
    try:
        value = json.loads(stdout.getvalue())
    except (ValueError, json.JSONDecodeError):
        raise BridgeError("mutation_result_invalid")
    counts = value.get("item_counts") if isinstance(value.get("item_counts"), dict) else {}
    item = value.get("item") if isinstance(value.get("item"), dict) else {}
    return {
        "status": value.get("status"),
        "item_id": value.get("id") or item.get("id"),
        "item_status": value.get("item_status") or item.get("status"),
        "item_counts": {key: counts.get(key) for key in ("total", "pending", "built", "accepted", "rejected", "dropped", "open") if key in counts},
    }


def _cursor(sequence, snapshot):
    return {"sequence": sequence, "readiness_fingerprint": snapshot["readiness_fingerprint"]}


def _mutate(root, params):
    if set(params) != {"action_id", "cursor", "operation", "arguments", "write"} or params.get("write") is not True:
        raise BridgeError("mutation_shape_invalid")
    action_id = params.get("action_id")
    operation = params.get("operation")
    cursor = params.get("cursor")
    if not active_run.valid_action_id(action_id) or operation not in OPERATIONS or not isinstance(cursor, dict):
        raise BridgeError("mutation_contract_invalid")
    _mutation_argv(root, action_id, operation, params.get("arguments"))
    request_fingerprint = active_run.action_request_fingerprint(operation, params.get("arguments"))
    active = active_run.load_active(root)
    if active.get("present") is not True or active.get("status") != "active":
        raise BridgeError("active_run_missing")
    _owner_required(active)
    run_dir = active_run.resolve_run_dir(root, active.get("run", ""))
    with active_run.item_mutation_lock(root, run_dir, active) as run_descriptor:
        receipt = _read_receipt(run_descriptor)
        existing = next((row for row in receipt["actions"] if row["action_id"] == action_id), None)
        if existing is not None:
            if existing["request_fingerprint"] != request_fingerprint or existing["operation"] != operation:
                raise BridgeError("action_id_reused")
            if existing["status"] == "completed":
                snapshot = readiness.build(root)
                return {"schema_version": 1, "status": "replayed", "result": existing["result"], "cursor": _cursor(receipt["sequence"], snapshot)}
            result = _delegate(root, action_id, operation, params.get("arguments"))
            existing["status"] = "completed"
            existing["result"] = result
            _write_receipt(run_descriptor, receipt)
            snapshot = readiness.build(root)
            return {"schema_version": 1, "status": "reconciled", "result": result, "cursor": _cursor(receipt["sequence"], snapshot)}
        snapshot = readiness.build(root)
        if set(cursor) != {"sequence", "readiness_fingerprint"} or cursor.get("sequence") != receipt["sequence"] or cursor.get("readiness_fingerprint") != snapshot["readiness_fingerprint"]:
            raise BridgeError("stale_cursor")
        if len(receipt["actions"]) >= MAX_ACTIONS:
            raise BridgeError("receipt_full")
        receipt["sequence"] += 1
        prepared = {
            "action_id": action_id,
            "request_fingerprint": request_fingerprint,
            "operation": operation,
            "status": "prepared",
            "result": {},
        }
        receipt["actions"].append(prepared)
        _write_receipt(run_descriptor, receipt)
        result = _delegate(root, action_id, operation, params.get("arguments"))
        prepared["status"] = "completed"
        prepared["result"] = result
        _write_receipt(run_descriptor, receipt)
        current = readiness.build(root)
        return {"schema_version": 1, "status": "mutated", "result": result, "cursor": _cursor(receipt["sequence"], current)}


def _read_target(root, params, allowed):
    if set(params) - allowed:
        raise BridgeError("params_invalid")
    active = active_run.load_active(root)
    requested = params.get("run")
    if requested is None:
        if active.get("present") is not True or active.get("status") != "active":
            raise BridgeError("active_run_missing")
        requested = active.get("run")
    if not isinstance(requested, str) or not requested:
        raise BridgeError("run_invalid")
    try:
        run_dir = active_run.resolve_run_dir(root, requested)
    except active_run.ActiveError:
        raise BridgeError("run_invalid")
    bound_active = active if active.get("present") is True and active.get("run") == requested else None
    return run_dir, bound_active


def handle(root, request):
    request = _validate_request(request)
    method = request["method"]
    params = request["params"]
    if method == "run/readiness":
        if params:
            raise BridgeError("params_invalid")
        snapshot = readiness.build(root)
        active = active_run.load_active(root)
        sequence = 0
        if active.get("present") is True and active.get("status") == "active":
            run_dir = active_run.resolve_run_dir(root, active.get("run", ""))
            descriptor = phase_context._open_run(root, run_dir, active=active)
            try:
                sequence = _read_receipt(descriptor)["sequence"]
            finally:
                os.close(descriptor)
        return {"schema_version": 1, "status": "ok", "readiness": snapshot, "cursor": _cursor(sequence, snapshot)}
    if method == "run/mutate":
        return _mutate(root, params)
    if method == "run/context":
        run_dir, active = _read_target(root, params, {"phase", "run"})
        phase = params.get("phase", readiness.current_phase(run_dir))
        try:
            value = phase_context.compile_shadow(root, run_dir, phase, active=active)
        except (ValueError, phase_context.PhaseContextError) as exc:
            raise BridgeError("context_invalid", str(exc))
        return {"schema_version": 1, "status": "ok", "context": value}
    run_dir, active = _read_target(root, params, {"run"})
    descriptor = phase_context._open_run(root, run_dir, active=active)
    try:
        value = scorecard.build(root, run_dir, run_descriptor=descriptor)
    finally:
        os.close(descriptor)
    return {"schema_version": 1, "status": "ok", "scorecard": value}


def _parser():
    parser = argparse.ArgumentParser(prog="run-bridge", description="Kimiflow local JSON-stdio bridge")
    parser.add_argument("--root")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None, stdin=None):
    args = _parser().parse_args(argv)
    try:
        root = active_run.resolve_root(args.root or "", strict=False)
        payload = (stdin if stdin is not None else sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise BridgeError("request_oversize")
        request = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        response = handle(root, request)
        code = 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, BridgeError, active_run.ActiveError) as exc:
        if isinstance(exc, BridgeError):
            error = exc
        elif isinstance(exc, active_run.ActiveError):
            error = BridgeError("active_run_invalid")
        else:
            error = BridgeError("request_malformed")
        response = {"schema_version": 1, "status": "error", "error": {"code": error.code, "message": error.message}}
        code = 1
    sys.stdout.write(json.dumps(response, ensure_ascii=False, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
