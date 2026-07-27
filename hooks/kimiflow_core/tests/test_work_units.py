import copy
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from kimiflow_core import work_units
from kimiflow_core import model_adapter


USAGE = {"model_calls": 1, "tool_calls": 1, "input_tokens": 10, "output_tokens": 5}


class FixtureExecutor:
    enforces_work_unit_policy = True
    supported_tools = work_units.READ_ONLY_PROJECT_TOOLS

    def __init__(self, responses=None, delay=0, call_log=None):
        self.responses = list(responses or [])
        self.delay = delay
        self.call_log = call_log
        self.calls = []

    def execute(self, unit, policy):
        self.calls.append((unit["id"], policy))
        if self.call_log:
            with open(self.call_log, "a", encoding="utf-8") as handle:
                handle.write(json.dumps([unit["id"], policy]) + "\n")
        if self.delay:
            time.sleep(self.delay)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return {
            "completed": True,
            "completion_id": unit["id"],
            "output": {"summary": "private output for " + unit["id"]},
            "usage": dict(USAGE),
        }


def unit(unit_id, dependencies=(), key_char="a", **updates):
    value = {
        "id": unit_id,
        "kind": "research",
        "dependencies": list(dependencies),
        "declared_budget": {
            "model_calls": 1, "tool_calls": 2, "input_tokens": 20, "output_tokens": 10,
        },
        "allowed_tools": ["Read", "Glob", "Grep"],
        "idempotency_key": "sha256:" + key_char * 64,
        "timeout_seconds": 1,
        "input": {"question": unit_id},
    }
    value.update(updates)
    return value


def plan(units):
    return {
        "schema_version": 1,
        "budget": {
            "model_calls": 10, "tool_calls": 20, "input_tokens": 200, "output_tokens": 100,
        },
        "units": units,
    }


class WorkUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_serial_dependency_order_and_deterministic_synthesis(self):
        value = plan([
            unit("research-a", key_char="a"),
            unit("research-b", key_char="b"),
            unit(
                "review-c", dependencies=("research-a", "research-b"), key_char="c",
                kind="review",
            ),
        ])
        call_log = os.path.join(self.tmp.name, "calls.jsonl")
        executor = FixtureExecutor(call_log=call_log)
        first = work_units.execute_plan(value, executor, root=self.tmp.name)
        second = work_units.execute_plan(value, FixtureExecutor(), root=self.tmp.name)
        self.assertEqual(first["order"], ["research-a", "research-b", "review-c"])
        self.assertEqual(first["receipt"], second["receipt"])
        calls = [
            json.loads(line)
            for line in Path(call_log).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([call[0] for call in calls], first["order"])
        for _, policy in calls:
            self.assertEqual(policy["filesystem_access"], "read_only")
            self.assertEqual(policy["settings_sources"], [])
            self.assertEqual(policy["mcp_servers"], [])
            self.assertIs(policy["hooks"], False)
        receipt = json.dumps(first["receipt"])
        self.assertNotIn("private output", receipt)
        self.assertIn("private output", json.dumps(first["synthesis"]))

    def test_rejects_cycle_budget_permission_timeout_and_duplicate_completion(self):
        cyclic = plan([
            unit("research-a", dependencies=("review-b",), key_char="a"),
            unit("review-b", dependencies=("research-a",), key_char="b", kind="review"),
        ])
        with self.assertRaisesRegex(work_units.WorkUnitError, "dependency_cycle"):
            work_units.execute_plan(cyclic, FixtureExecutor(), root=self.tmp.name)

        too_large = plan([unit("research-a")])
        too_large["budget"]["input_tokens"] = 1
        with self.assertRaisesRegex(work_units.WorkUnitError, "budget_exceeded"):
            work_units.execute_plan(too_large, FixtureExecutor(), root=self.tmp.name)

        forbidden = plan([unit("research-a", allowed_tools=["Bash"])])
        with self.assertRaisesRegex(work_units.WorkUnitError, "permission_denied"):
            work_units.execute_plan(forbidden, FixtureExecutor(), root=self.tmp.name)

        duplicate = plan([
            unit("research-a", key_char="a"),
            unit("research-b", key_char="a"),
        ])
        with self.assertRaisesRegex(work_units.WorkUnitError, "duplicate_idempotency"):
            work_units.execute_plan(duplicate, FixtureExecutor(), root=self.tmp.name)

        timeout = plan([unit("research-a", timeout_seconds=0.001)])
        with self.assertRaisesRegex(work_units.WorkUnitError, "unit_timeout"):
            started = time.monotonic()
            work_units.execute_plan(
                timeout, FixtureExecutor(delay=0.02), root=self.tmp.name,
            )
        self.assertLess(time.monotonic() - started, 1)

        child_ready = os.path.join(self.tmp.name, "child-ready")
        late_effect = os.path.join(self.tmp.name, "late-effect")

        class DelayedEffect(FixtureExecutor):
            def execute(self, value, policy):
                child = os.fork()
                if child == 0:
                    with open(child_ready, "w", encoding="utf-8") as handle:
                        handle.write("ready")
                    time.sleep(0.5)
                    with open(late_effect, "w", encoding="utf-8") as handle:
                        handle.write("must not happen")
                    os._exit(0)
                while True:
                    time.sleep(1)

        descendant_timeout = plan([
            unit("research-a", timeout_seconds=0.05),
        ])
        with self.assertRaisesRegex(work_units.WorkUnitError, "unit_timeout"):
            work_units.execute_plan(
                descendant_timeout, DelayedEffect(), root=self.tmp.name,
            )
        self.assertTrue(os.path.exists(child_ready))
        self.assertFalse(os.path.exists(late_effect))
        time.sleep(0.6)
        self.assertFalse(os.path.exists(late_effect))

        orphan_ready = os.path.join(self.tmp.name, "orphan-ready")
        orphan_effect = os.path.join(self.tmp.name, "orphan-effect")

        class OrphanEffect(FixtureExecutor):
            def execute(self, value, policy):
                child = os.fork()
                if child == 0:
                    with open(orphan_ready, "w", encoding="utf-8") as handle:
                        handle.write("ready")
                    time.sleep(0.5)
                    with open(orphan_effect, "w", encoding="utf-8") as handle:
                        handle.write("must not happen")
                    os._exit(0)
                os._exit(0)

        with self.assertRaisesRegex(work_units.WorkUnitError, "unit_timeout"):
            work_units.execute_plan(
                descendant_timeout, OrphanEffect(), root=self.tmp.name,
            )
        self.assertTrue(os.path.exists(orphan_ready))
        self.assertFalse(os.path.exists(orphan_effect))
        time.sleep(0.6)
        self.assertFalse(os.path.exists(orphan_effect))

        success_ready = os.path.join(self.tmp.name, "success-ready")
        success_effect = os.path.join(self.tmp.name, "success-effect")

        class SuccessfulDescendant(FixtureExecutor):
            def execute(self, value, policy):
                del policy
                if value["id"] == "research-a":
                    child = os.fork()
                    if child == 0:
                        with open(success_ready, "w", encoding="utf-8") as handle:
                            handle.write("ready")
                        time.sleep(0.3)
                        with open(success_effect, "w", encoding="utf-8") as handle:
                            handle.write("must not happen")
                        os._exit(0)
                    deadline = time.monotonic() + 0.2
                    while not os.path.exists(success_ready) and time.monotonic() < deadline:
                        time.sleep(0.005)
                else:
                    time.sleep(0.4)
                return {
                    "completed": True,
                    "completion_id": value["id"],
                    "output": {
                        "late_effect_visible": os.path.exists(success_effect),
                    },
                    "usage": dict(USAGE),
                }

        success_result = work_units.execute_plan(
            plan([
                unit("research-a", key_char="a"),
                unit("research-b", dependencies=("research-a",), key_char="b"),
            ]),
            SuccessfulDescendant(),
            root=self.tmp.name,
        )
        self.assertTrue(os.path.exists(success_ready))
        self.assertFalse(
            success_result["synthesis"][1]["output"]["late_effect_visible"],
        )
        self.assertFalse(os.path.exists(success_effect))
        time.sleep(0.4)
        self.assertFalse(os.path.exists(success_effect))

        wrong_completion = FixtureExecutor([{
            "completed": True,
            "completion_id": "review-b",
            "output": "secret",
            "usage": dict(USAGE),
        }])
        with self.assertRaisesRegex(
            work_units.WorkUnitError, "duplicate_completion",
        ) as wrong_completion_error:
            work_units.execute_plan(
                plan([unit("research-a")]), wrong_completion, root=self.tmp.name,
            )
        self.assertEqual(wrong_completion_error.exception.usage, USAGE)
        self.assertEqual(
            wrong_completion_error.exception.receipt["usage"],
            USAGE,
        )

        unavailable_log = os.path.join(self.tmp.name, "unavailable.jsonl")
        unavailable = FixtureExecutor([{
            "completed": True,
            "completion_id": "research-a",
            "output": "must not synthesize",
            "usage": None,
        }], call_log=unavailable_log)
        two = plan([
            unit("research-a", key_char="a"),
            unit("research-b", key_char="b"),
        ])
        with self.assertRaisesRegex(work_units.WorkUnitError, "budget_usage_unavailable"):
            work_units.execute_plan(two, unavailable, root=self.tmp.name)
        self.assertEqual(
            len(Path(unavailable_log).read_text(encoding="utf-8").splitlines()), 1,
        )

        failed_unavailable_log = os.path.join(
            self.tmp.name, "failed-unavailable.jsonl",
        )
        failed_unavailable = FixtureExecutor([{
            "completed": False,
            "completion_id": "research-a",
            "output": "must not synthesize",
            "usage": None,
            "error_code": "provider_crash",
        }], call_log=failed_unavailable_log)
        with self.assertRaisesRegex(
            work_units.WorkUnitError, "budget_usage_unavailable",
        ):
            work_units.execute_plan(two, failed_unavailable, root=self.tmp.name)
        self.assertEqual(
            len(
                Path(failed_unavailable_log)
                .read_text(encoding="utf-8")
                .splitlines()
            ),
            1,
        )

        failed_overrun = FixtureExecutor([{
            "completed": False,
            "completion_id": "research-a",
            "output": "must not synthesize",
            "usage": {**USAGE, "input_tokens": 21},
            "error_code": "provider_crash",
        }])
        with self.assertRaisesRegex(
            work_units.WorkUnitError, "budget_exceeded",
        ) as failed_overrun_error:
            work_units.execute_plan(
                plan([unit("research-a")]),
                failed_overrun,
                root=self.tmp.name,
            )
        self.assertEqual(
            failed_overrun_error.exception.usage["input_tokens"],
            21,
        )
        self.assertEqual(
            failed_overrun_error.exception.receipt["usage"]["input_tokens"],
            21,
        )

        failed_accounted = FixtureExecutor([{
            "completed": False,
            "completion_id": "research-a",
            "output": "must not enter the receipt",
            "usage": dict(USAGE),
            "error_code": "provider_crash",
        }])
        with self.assertRaisesRegex(
            work_units.WorkUnitError, "provider_crash",
        ) as raised:
            work_units.execute_plan(
                plan([unit("research-a")]),
                failed_accounted,
                root=self.tmp.name,
            )
        self.assertEqual(raised.exception.usage, USAGE)
        self.assertEqual(raised.exception.receipt["usage"], USAGE)
        self.assertEqual(
            raised.exception.receipt["failed_unit"],
            {"unit_id": "research-a", "usage": USAGE},
        )
        self.assertNotIn(
            "must not enter the receipt",
            json.dumps(raised.exception.receipt),
        )

        plan_path = os.path.join(self.tmp.name, "failed-plan.json")
        Path(plan_path).write_text(
            json.dumps(plan([unit("research-a")])),
            encoding="utf-8",
        )
        cli_executor = FixtureExecutor([{
            "completed": False,
            "completion_id": "research-a",
            "output": "must not enter the CLI",
            "usage": dict(USAGE),
            "error_code": "provider_crash",
        }])
        stdout = io.StringIO()
        with (
            mock.patch.object(work_units, "_adapter", return_value=cli_executor),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(work_units.main(["--plan", plan_path]), 2)
        cli_failure = json.loads(stdout.getvalue())
        self.assertEqual(cli_failure["usage"], USAGE)
        self.assertEqual(cli_failure["receipt"]["failed_unit"]["usage"], USAGE)
        self.assertNotIn("must not enter the CLI", stdout.getvalue())

        for raw_error in ("raw-provider-secret", b"raw-provider-secret"):
            with self.subTest(raw_error_type=type(raw_error).__name__):
                unsafe_executor = FixtureExecutor([{
                    "completed": False,
                    "completion_id": "research-a",
                    "output": "must not enter normalized failure",
                    "usage": dict(USAGE),
                    "error_code": raw_error,
                }])
                unsafe_stdout = io.StringIO()
                with (
                    mock.patch.object(
                        work_units, "_adapter", return_value=unsafe_executor,
                    ),
                    contextlib.redirect_stdout(unsafe_stdout),
                ):
                    self.assertEqual(
                        work_units.main(["--plan", plan_path]),
                        2,
                    )
                normalized_failure = json.loads(unsafe_stdout.getvalue())
                self.assertEqual(
                    normalized_failure["error_code"],
                    "executor_failed",
                )
                self.assertNotIn(
                    "raw-provider-secret",
                    unsafe_stdout.getvalue(),
                )

        actual_overrun = FixtureExecutor([{
            "completed": True,
            "completion_id": "research-a",
            "output": "over",
            "usage": {**USAGE, "input_tokens": 21},
        }])
        with self.assertRaisesRegex(
            work_units.WorkUnitError, "budget_exceeded",
        ) as actual_overrun_error:
            work_units.execute_plan(
                plan([unit("research-a")]), actual_overrun, root=self.tmp.name,
            )
        self.assertEqual(actual_overrun_error.exception.usage["input_tokens"], 21)
        self.assertEqual(
            actual_overrun_error.exception.receipt["usage"]["input_tokens"],
            21,
        )

    def test_callback_exceptions_normalize_to_content_poor_json(self):
        value = plan([unit("research-a")])
        for private_code in ("raw-provider-secret", b"raw-provider-secret"):
            with self.assertRaisesRegex(
                work_units.WorkUnitError, "executor_failed",
            ) as raised:
                work_units.execute_plan(
                    value,
                    FixtureExecutor([work_units.WorkUnitError(private_code)]),
                    root=self.tmp.name,
                )
            self.assertNotIn("raw-provider-secret", str(raised.exception))

            output = io.StringIO()
            with (
                mock.patch.object(
                    work_units,
                    "_adapter",
                    return_value=FixtureExecutor([
                        work_units.WorkUnitError(private_code),
                    ]),
                ),
                mock.patch("sys.stdin", io.StringIO(json.dumps(value))),
                contextlib.redirect_stdout(output),
            ):
                status = work_units.main(["--root", self.tmp.name])
            result = json.loads(output.getvalue())
            self.assertEqual(status, 2)
            self.assertEqual(result["error_code"], "executor_failed")
            self.assertNotIn("raw-provider-secret", output.getvalue())

    def test_policy_is_exact_and_unattested_executor_fails_before_execution(self):
        value = unit("research-a")
        policy = work_units.build_policy(value)
        self.assertEqual(
            set(policy),
            {
                "schema_version", "unit_kind", "context_scope", "filesystem_access",
                "allowed_tools", "settings_sources", "mcp_servers", "hooks",
                "input_digest",
            },
        )

        calls = []

        def unattested(*args):
            calls.append(args)

        with self.assertRaisesRegex(work_units.WorkUnitError, "isolation_unavailable"):
            work_units.execute_plan(plan([value]), unattested, root=self.tmp.name)
        self.assertEqual(calls, [])

    def test_plan_and_unit_input_bounds_are_enforced(self):
        with self.assertRaisesRegex(work_units.WorkUnitError, "plan_invalid"):
            work_units.validate_plan(plan([]))
        with self.assertRaisesRegex(work_units.WorkUnitError, "plan_invalid"):
            work_units.validate_plan(plan([
                unit("unit-%02d" % index, key_char=("%x" % index)[-1])
                for index in range(work_units.MAX_UNITS + 1)
            ]))
        oversized_input = plan([
            unit("research-a", input={"text": "x" * work_units.MAX_UNIT_INPUT_BYTES}),
        ])
        with self.assertRaisesRegex(work_units.WorkUnitError, "work_unit_invalid"):
            work_units.validate_plan(oversized_input)
        oversized_plan = plan([
            unit("research-a", input={"text": "x" * work_units.MAX_PLAN_BYTES}),
        ])
        with self.assertRaisesRegex(work_units.WorkUnitError, "plan_too_large"):
            work_units.validate_plan(oversized_plan)

    def test_native_adapter_output_is_available_for_synthesis(self):
        class NativeAdapter:
            supported_tools = work_units.READ_ONLY_PROJECT_TOOLS
            turn_timeout_seconds = 60

            def info(self):
                return {
                    "schema_version": 1,
                    "name": "native-fixture",
                    "host": "local",
                    "capabilities": {
                        key: True for key in model_adapter.CAPABILITY_KEYS
                    },
                    "features": {"work_unit_policy": True},
                }

            def start(self, root, prompt, on_session, work_unit_policy=None):
                del root, prompt, work_unit_policy
                on_session("fixture-session")
                return model_adapter.TurnResult(
                    0,
                    session_id="fixture-session",
                    usage=dict(USAGE),
                    output={"messages": ["private native answer"]},
                )

        result = work_units.execute_plan(
            plan([unit("research-a")]), NativeAdapter(), root=self.tmp.name,
        )
        self.assertIn("private native answer", json.dumps(result["synthesis"]))
        self.assertNotIn("private native answer", json.dumps(result["receipt"]))


if __name__ == "__main__":
    unittest.main()
