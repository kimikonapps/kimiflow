"""Strict serial execution for bounded read-only Kimiflow Work-Units."""

import argparse
import hashlib
import json
import multiprocessing
import os
import signal
import sys

from . import model_adapter


READ_ONLY_PROJECT_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")
UNIT_KINDS = ("research", "review")
PLAN_KEYS = ("schema_version", "budget", "units")
UNIT_KEYS = (
    "id", "kind", "dependencies", "declared_budget", "allowed_tools",
    "idempotency_key", "timeout_seconds", "input",
)
MAX_PLAN_BYTES = 1024 * 1024
MAX_UNITS = 32
MAX_UNIT_INPUT_BYTES = 64 * 1024
SUPERVISOR_START_SECONDS = 2


class WorkUnitError(ValueError):
    def __init__(self, code, usage=None, receipt=None):
        super().__init__(code)
        self.code = code
        self.usage = usage
        self.receipt = receipt


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError):
        raise WorkUnitError("work_unit_invalid")


def digest(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _usage(value, error="budget_invalid"):
    normalized = model_adapter.normalize_usage(value)
    if normalized is None or set(value) != set(model_adapter.USAGE_KEYS):
        raise WorkUnitError(error)
    return normalized


def add_usage(left, right):
    return {
        key: left[key] + right[key] for key in model_adapter.USAGE_KEYS
    }


def exceeds(actual, limit):
    return any(actual[key] > limit[key] for key in model_adapter.USAGE_KEYS)


def build_policy(unit, context_scope="project_root"):
    policy = {
        "schema_version": 1,
        "unit_kind": unit["kind"],
        "context_scope": context_scope,
        "filesystem_access": "read_only" if context_scope == "project_root" else "none",
        "allowed_tools": list(unit["allowed_tools"]) if context_scope == "project_root" else [],
        "settings_sources": [],
        "mcp_servers": [],
        "hooks": False,
        "input_digest": digest(unit["input"]),
    }
    try:
        return model_adapter.validate_work_unit_policy(policy)
    except model_adapter.AdapterError as exc:
        raise WorkUnitError(str(exc))


def validate_plan(value):
    if len(_canonical(value).encode("utf-8")) > MAX_PLAN_BYTES:
        raise WorkUnitError("plan_too_large")
    if not isinstance(value, dict) or set(value) != set(PLAN_KEYS):
        raise WorkUnitError("plan_invalid")
    if value.get("schema_version") != 1 or not isinstance(value.get("units"), list):
        raise WorkUnitError("plan_invalid")
    if not 1 <= len(value["units"]) <= MAX_UNITS:
        raise WorkUnitError("plan_invalid")
    budget = _usage(value.get("budget"))
    units = []
    ids = set()
    idempotency = set()
    declared_total = {key: 0 for key in model_adapter.USAGE_KEYS}
    for raw in value["units"]:
        if not isinstance(raw, dict) or set(raw) != set(UNIT_KEYS):
            raise WorkUnitError("work_unit_invalid")
        unit_id = raw.get("id")
        kind = raw.get("kind")
        dependencies = raw.get("dependencies")
        tools = raw.get("allowed_tools")
        key = raw.get("idempotency_key")
        timeout = raw.get("timeout_seconds")
        if (
            not isinstance(unit_id, str)
            or model_adapter.IDENTITY_RE.fullmatch(unit_id) is None
            or unit_id in ids
        ):
            raise WorkUnitError("duplicate_completion" if unit_id in ids else "work_unit_invalid")
        if kind not in UNIT_KINDS:
            raise WorkUnitError("work_unit_kind_invalid")
        if (
            not isinstance(dependencies, list)
            or len(dependencies) != len(set(dependencies))
            or any(not isinstance(item, str) for item in dependencies)
        ):
            raise WorkUnitError("dependency_invalid")
        if (
            not isinstance(tools, list)
            or len(tools) != len(set(tools))
            or any(tool not in READ_ONLY_PROJECT_TOOLS for tool in tools)
        ):
            raise WorkUnitError("permission_denied")
        if not isinstance(key, str) or model_adapter.DIGEST_RE.fullmatch(key) is None:
            raise WorkUnitError("idempotency_invalid")
        if key in idempotency:
            raise WorkUnitError("duplicate_idempotency")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 < timeout <= model_adapter.MAX_TURN_TIMEOUT_SECONDS
        ):
            raise WorkUnitError("timeout_invalid")
        if (
            not isinstance(raw.get("input"), dict)
            or len(_canonical(raw["input"]).encode("utf-8")) > MAX_UNIT_INPUT_BYTES
        ):
            raise WorkUnitError("work_unit_invalid")
        declared = _usage(raw.get("declared_budget"))
        declared_total = add_usage(declared_total, declared)
        unit = {
            "id": unit_id,
            "kind": kind,
            "dependencies": list(dependencies),
            "declared_budget": declared,
            "allowed_tools": list(tools),
            "idempotency_key": key,
            "timeout_seconds": timeout,
            "input": json.loads(_canonical(raw["input"])),
        }
        units.append(unit)
        ids.add(unit_id)
        idempotency.add(key)
    if any(dependency not in ids for unit in units for dependency in unit["dependencies"]):
        raise WorkUnitError("dependency_missing")
    if exceeds(declared_total, budget):
        raise WorkUnitError("budget_exceeded")
    pending = list(units)
    ordered = []
    completed = set()
    while pending:
        ready_index = next(
            (
                index for index, unit in enumerate(pending)
                if set(unit["dependencies"]).issubset(completed)
            ),
            None,
        )
        if ready_index is None:
            raise WorkUnitError("dependency_cycle")
        unit = pending.pop(ready_index)
        ordered.append(unit)
        completed.add(unit["id"])
    return {"schema_version": 1, "budget": budget, "units": ordered}


def _executor_info(executor):
    if not hasattr(executor, "info"):
        if getattr(executor, "enforces_work_unit_policy", False) is not True:
            raise WorkUnitError("isolation_unavailable")
        return None
    try:
        info = model_adapter.info_for(executor)
    except (model_adapter.AdapterError, OSError, ValueError):
        raise WorkUnitError("isolation_unavailable")
    if info.get("features", {}).get("work_unit_policy") is not True:
        raise WorkUnitError("isolation_unavailable")
    return info


def _call(executor, root, unit, policy):
    if hasattr(executor, "start"):
        sessions = []
        result = executor.start(
            root,
            _canonical({
                "schema_version": 1,
                "work_unit": unit["input"],
                "input_digest": policy["input_digest"],
            }),
            sessions.append,
            work_unit_policy=policy,
        )
        return {
            "completed": result.returncode == 0,
            "completion_id": unit["id"],
            "output": {
                "session_id": result.session_id,
                "status": "completed" if result.returncode == 0 else "failed",
                "result": result.output,
            },
            "usage": result.usage,
            "error_code": result.error_code,
        }
    raise WorkUnitError("isolation_unavailable")


def _executor_worker(connection, executor):
    try:
        os.setsid()
    except OSError:
        try:
            connection.send(("setup_error", None))
        finally:
            connection.close()
        return
    connection.send(("ready", None))
    try:
        while True:
            try:
                request = connection.recv()
            except EOFError:
                return
            if request is None:
                return
            unit, policy = request
            try:
                response = executor.execute(unit, policy)
            except WorkUnitError:
                # The callback is an untrusted provider boundary. Its exception
                # code may contain private text or a non-JSON value.
                connection.send(("work_unit_error", "executor_failed"))
            except BaseException:
                connection.send(("executor_error", None))
            else:
                try:
                    connection.send(("result", response))
                except (OSError, TypeError, ValueError):
                    try:
                        connection.send(("executor_error", None))
                    except OSError:
                        return
    finally:
        connection.close()


class _ExecutorSupervisor:
    def __init__(self, executor):
        if (
            os.name != "posix"
            or not hasattr(os, "setsid")
            or not hasattr(os, "killpg")
        ):
            raise WorkUnitError("isolation_unavailable")
        try:
            context = multiprocessing.get_context("fork")
            parent, child = context.Pipe()
            process = context.Process(
                target=_executor_worker, args=(child, executor),
            )
            process.start()
            child.close()
        except (OSError, RuntimeError, ValueError):
            raise WorkUnitError("isolation_unavailable")
        self.connection = parent
        self.process = process
        if not parent.poll(SUPERVISOR_START_SECONDS):
            self.terminate()
            raise WorkUnitError("isolation_unavailable")
        try:
            message = parent.recv()
        except (EOFError, OSError):
            self.terminate()
            raise WorkUnitError("isolation_unavailable")
        if message != ("ready", None):
            self.terminate()
            raise WorkUnitError("isolation_unavailable")

    def call(self, unit, policy):
        if not self.process.is_alive():
            raise WorkUnitError("executor_failed")
        try:
            self.connection.send((unit, policy))
        except (OSError, TypeError, ValueError):
            raise WorkUnitError("executor_failed")
        if not self.connection.poll(unit["timeout_seconds"]):
            self.terminate()
            raise WorkUnitError("unit_timeout")
        try:
            kind, value = self.connection.recv()
        except (EOFError, OSError, TypeError, ValueError):
            raise WorkUnitError("executor_failed")
        if kind == "result":
            return value
        if kind == "work_unit_error":
            raise WorkUnitError(value)
        raise WorkUnitError("executor_failed")

    def terminate(self):
        connection = getattr(self, "connection", None)
        process = getattr(self, "process", None)
        if process is not None:
            process_group = process.pid
            if process.is_alive():
                try:
                    os.killpg(process_group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except OSError:
                    process.terminate()
            # Always hard-stop the owned group. The supervisor may exit on
            # SIGTERM before a just-forked descendant, so process.is_alive()
            # cannot decide whether the group still has effect-capable work.
            # The same applies when a hostile executor exits the supervisor
            # directly after forking.
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                if process.is_alive():
                    process.kill()
            process.join(SUPERVISOR_START_SECONDS)
        if connection is not None:
            connection.close()
        self.process = None
        self.connection = None


def _timed_call(executor, root, unit, policy):
    if hasattr(executor, "execute"):
        supervisor = _ExecutorSupervisor(executor)
        try:
            return supervisor.call(unit, policy)
        finally:
            supervisor.terminate()
    timeout = getattr(executor, "turn_timeout_seconds", None)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise WorkUnitError("isolation_unavailable")
    executor.turn_timeout_seconds = min(timeout, unit["timeout_seconds"])
    try:
        return _call(executor, root, unit, policy)
    finally:
        executor.turn_timeout_seconds = timeout


def _terminal_response_error(code, unit, usage, actual_total, receipt_units):
    return WorkUnitError(
        code,
        usage=dict(actual_total),
        receipt={
            "schema_version": 1,
            "status": "failed",
            "error_code": code,
            "usage": dict(actual_total),
            "completed_units": list(receipt_units),
            "failed_unit": {
                "unit_id": unit["id"],
                "usage": usage,
            },
        },
    )


def execute_plan(plan, executor, root=None):
    """Validate and execute ready units serially in deterministic declared order."""
    normalized = validate_plan(plan)
    _executor_info(executor)
    root = os.path.realpath(root or os.getcwd())
    actual_total = {key: 0 for key in model_adapter.USAGE_KEYS}
    completions = set()
    synthesis = []
    receipt_units = []
    for unit in normalized["units"]:
        supported = getattr(executor, "supported_tools", READ_ONLY_PROJECT_TOOLS)
        if any(tool not in supported for tool in unit["allowed_tools"]):
            raise WorkUnitError("permission_unbound")
        policy = build_policy(unit)
        response = _timed_call(executor, root, unit, policy)
        if not isinstance(response, dict):
            raise WorkUnitError("executor_result_invalid")
        usage = model_adapter.normalize_usage(response.get("usage"))
        if usage is None or set(response.get("usage", {})) != set(model_adapter.USAGE_KEYS):
            raise WorkUnitError("budget_usage_unavailable")
        actual_total = add_usage(actual_total, usage)
        try:
            if (
                exceeds(usage, unit["declared_budget"])
                or exceeds(actual_total, normalized["budget"])
            ):
                raise WorkUnitError("budget_exceeded")
            if response.get("completed") is not True:
                raw_error = response.get("error_code")
                error_code = (
                    raw_error
                    if isinstance(raw_error, str)
                    and raw_error in model_adapter.PROVIDER_ERROR_CODES
                    else "executor_failed"
                )
                raise WorkUnitError(error_code)
            completion_id = response.get("completion_id")
            if completion_id != unit["id"] or completion_id in completions:
                raise WorkUnitError("duplicate_completion")
            output = response.get("output")
            output_digest = digest(output)
        except WorkUnitError as exc:
            if exc.usage is not None:
                raise
            raise _terminal_response_error(
                exc.code, unit, usage, actual_total, receipt_units,
            )
        completions.add(completion_id)
        synthesis.append({"unit_id": unit["id"], "output": output})
        receipt_units.append({
            "unit_id": unit["id"],
            "output_digest": output_digest,
            "usage": usage,
        })
    synthesis_digest = digest(synthesis)
    return {
        "schema_version": 1,
        "status": "completed",
        "order": [unit["id"] for unit in normalized["units"]],
        "usage": actual_total,
        "synthesis": synthesis,
        "receipt": {
            "schema_version": 1,
            "status": "completed",
            "unit_count": len(receipt_units),
            "units": receipt_units,
            "synthesis_digest": synthesis_digest,
        },
    }


def _adapter(args):
    if args.adapter == "codex":
        return model_adapter.CodexExecAdapter()
    if args.adapter == "claude":
        return model_adapter.ClaudeCodeAdapter(model=args.model)
    if not args.adapter_command:
        raise WorkUnitError("adapter_command_missing")
    return model_adapter.CommandAgentAdapter(
        args.adapter_command, model=args.model, required_features=["work_unit_policy"],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="work-units")
    parser.add_argument("--plan", help="JSON plan path; stdin when omitted")
    parser.add_argument("--root")
    parser.add_argument("--adapter", choices=("codex", "claude", "command"), default="codex")
    parser.add_argument("--adapter-command")
    parser.add_argument("--model")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        with open(args.plan, encoding="utf-8") if args.plan else sys.stdin as handle:
            plan = json.load(handle)
        value = execute_plan(plan, _adapter(args), root=args.root)
        print(json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        ))
        return 0
    except (OSError, ValueError, WorkUnitError, model_adapter.AdapterError) as exc:
        code = exc.code if isinstance(exc, WorkUnitError) else str(exc)
        result = {"schema_version": 1, "status": "failed", "error_code": code}
        if isinstance(exc, WorkUnitError) and exc.usage is not None:
            result["usage"] = exc.usage
            result["receipt"] = exc.receipt
        print(json.dumps(result))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
