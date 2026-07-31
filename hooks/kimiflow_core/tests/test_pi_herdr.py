import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kimiflow_core import pi_herdr


class PiHerdrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.root = os.path.realpath(self.temp)
        self.session_id = "019fb526-01b5-7c2c-a9d0-b5fea10704e5"
        self.session_path = os.path.join(self.temp, "session.jsonl")
        with open(self.session_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "session",
                "version": 3,
                "id": self.session_id,
                "cwd": self.root,
            }) + "\n")
        self.environment = {
            **os.environ,
            "HERDR_ENV": "1",
            "HERDR_WORKSPACE_ID": "w2",
            "HERDR_TAB_ID": "w2:t1",
            "HERDR_PANE_ID": "w2:p1",
            "KIMIFLOW_PI_BRIDGE_BINDING": json.dumps({
                "schema_version": 1,
                "root": self.root,
                "captain_session_id": "captain-00000001",
                "worker_id": "worker-00000001",
            }),
        }

    def test_endpoint_creation_uses_an_unfocused_visible_interactive_pi_tab(self):
        calm_extension = os.path.join(self.temp, "calm-source.js")
        calm_content = b"export default function calm() {}\n"
        with open(calm_extension, "wb") as handle:
            handle.write(calm_content)
        extension = os.path.join(self.temp, "worker-source.js")
        content = b"export default function worker() {}\n"
        with open(extension, "wb") as handle:
            handle.write(content)
        material = {
            "command": "/usr/local/bin/pi",
            "active_run_hook": "/plugin/hooks/active-run.sh",
            "calm_extension": calm_extension,
            "calm_extension_digest": (
                "sha256:" + hashlib.sha256(calm_content).hexdigest()
            ),
            "worker_extension": extension,
            "worker_extension_digest": (
                "sha256:" + hashlib.sha256(content).hexdigest()
            ),
        }
        calls = []

        def invoke(args, _environ, timeout=None):
            calls.append((args, timeout))
            if args[:2] == ["tab", "create"]:
                return {
                    "tab": {"workspace_id": "w2", "tab_id": "w2:t2"},
                    "root_pane": {
                        "workspace_id": "w2",
                        "tab_id": "w2:t2",
                        "pane_id": "w2:p2",
                    },
                }
            if args[:2] == ["agent", "start"]:
                return {"status": "started"}
            if args[:2] == ["pane", "get"]:
                return {
                    "pane": {
                        "workspace_id": "w2",
                        "tab_id": "w2:t2",
                        "pane_id": "w2:p2",
                        "agent": "pi",
                        "agent_status": "idle",
                        "cwd": self.root,
                        "agent_session": {
                            "kind": "path",
                            "value": self.session_path,
                        },
                    },
                }
            self.fail("unexpected Herdr command: %r" % (args,))

        binding = {
            "schema_version": 1,
            "root": self.root,
            "captain_session_id": "captain-00000001",
            "worker_id": "worker-00000001",
        }
        with mock.patch.object(pi_herdr, "_invoke", side_effect=invoke):
            state = pi_herdr._create_endpoint(
                self.root,
                {"workspace_id": "w2", "tab_id": "w2:t1", "pane_id": "w2:p1"},
                binding,
                {"provider": "openai", "model": "gpt-5.6", "thinking": "high"},
                material,
                self.environment,
                session_id=self.session_id,
            )
        tab_args = calls[0][0]
        start_args = calls[1][0]
        self.assertIn("--no-focus", tab_args)
        self.assertEqual(tab_args[tab_args.index("--workspace") + 1], "w2")
        self.assertIn("kimiflow · main", tab_args)
        self.assertEqual(start_args[:3], ["agent", "start", "kimiflow-main"])
        self.assertIn("--kind", start_args)
        self.assertIn("pi", start_args)
        self.assertNotIn("--mode", start_args)
        self.assertIn("--extension", start_args)
        self.assertEqual(start_args.count("--extension"), 2)
        extension_args = [
            start_args[index + 1]
            for index, value in enumerate(start_args)
            if value == "--extension"
        ]
        self.assertEqual(
            [os.path.basename(value) for value in extension_args],
            ["calm.js", "worker.js"],
        )
        self.assertIn("--session", start_args)
        self.assertNotIn("--session-id", start_args)
        self.assertFalse(any(
            "current request" in argument
            for args, _timeout in calls
            for argument in args
        ))
        self.assertFalse(any(
            "KIMIFLOW_PI_TRANSPORT_PROMPT" in argument
            for argument in tab_args
        ))
        self.assertEqual(state["tab_id"], "w2:t2")
        self.assertEqual(state["pane_id"], "w2:p2")

    def test_native_turn_requires_the_exact_prompt_and_clean_final_answer(self):
        offset = os.path.getsize(self.session_path)
        prompt = "Current request\n\nTransport request:\nembedded marker"
        with open(self.session_path, "a", encoding="utf-8") as handle:
            for value in (
                {
                    "type": "message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "verified result"}],
                        "stopReason": "stop",
                    },
                },
            ):
                handle.write(json.dumps(value) + "\n")
        self.assertEqual(
            pi_herdr._turn_result(self.session_path, offset, prompt),
            "verified result",
        )
        with self.assertRaises(pi_herdr.HerdrError):
            pi_herdr._turn_result(self.session_path, offset, "other prompt")

    def test_endpoint_settle_lag_is_waited_out_without_closing_the_worker(self):
        state = {
            "root": self.root,
            "session_id": self.session_id,
            "workspace_id": "w2",
            "tab_id": "w2:t2",
            "pane_id": "w2:p2",
            "session_path": None,
        }
        panes = [
            {
                "workspace_id": "w2",
                "tab_id": "w2:t2",
                "pane_id": "w2:p2",
                "agent": "pi",
                "agent_status": "working",
                "cwd": self.root,
            },
            {
                "workspace_id": "w2",
                "tab_id": "w2:t2",
                "pane_id": "w2:p2",
                "agent": "pi",
                "agent_status": "idle",
                "cwd": self.root,
            },
        ]
        with mock.patch.object(
            pi_herdr,
            "_pane",
            side_effect=panes,
        ), mock.patch.object(pi_herdr.time, "sleep") as sleep:
            pane = pi_herdr._wait_for_settled_endpoint(
                state, self.root, self.environment, timeout=1,
            )
        self.assertEqual(pane["agent_status"], "idle")
        sleep.assert_called_once_with(0.05)

    def test_subagent_owns_a_numbered_visible_read_only_worker_tab(self):
        calm_extension = os.path.join(self.root, "calm.js")
        calm_content = b"export default function calm() {}\n"
        with open(calm_extension, "wb") as handle:
            handle.write(calm_content)
        payload = {
            "schema_version": 1,
            "root": self.root,
            "session_id": self.session_id,
            "slot": 2,
            "task": "independently review the accepted behavior",
            "role": "code_review",
            "round": 1,
            "seat": "code-review-1",
            "calm_extension": calm_extension,
            "calm_extension_digest": (
                "sha256:" + hashlib.sha256(calm_content).hexdigest()
            ),
            "verbosity": "quiet",
            "selection": {
                "provider": "openai",
                "model": "gpt-5.6",
                "thinking": "high",
            },
        }
        emitted = []
        sentinel = {"control": 10, "process": mock.Mock()}
        with mock.patch.object(
            pi_herdr,
            "_context",
            return_value={
                "workspace_id": "w2",
                "tab_id": "w2:t1",
                "pane_id": "w2:p1",
            },
        ), mock.patch.object(
            pi_herdr,
            "_tab",
            return_value=("w2:t2", "w2:p2"),
        ) as create_tab, mock.patch.object(
            pi_herdr,
            "_start_agent",
        ) as start_agent, mock.patch.object(
            pi_herdr,
            "_start_sentinel",
            return_value=sentinel,
        ), mock.patch.object(
            pi_herdr,
            "_invoke",
            return_value={},
        ), mock.patch.object(
            pi_herdr,
            "_wait_for_native_session",
            return_value=self.session_path,
        ), mock.patch.object(
            pi_herdr,
            "_wait_for_settled_endpoint",
        ), mock.patch.object(
            pi_herdr,
            "_turn_result",
            return_value="independent result",
        ), mock.patch.object(
            pi_herdr,
            "_close_exact_ids",
            return_value=True,
        ) as close_tab, mock.patch.object(
            pi_herdr,
            "_finish_sentinel",
        ) as finish_sentinel:
            result = pi_herdr.run_subagent(
                payload,
                self.environment,
                emitted.append,
            )

        self.assertEqual(result, 0)
        create_tab.assert_called_once_with(
            self.root,
            "w2",
            "kimiflow · code review · code-review-1",
            {"KIMIFLOW_PI_VERBOSITY": "quiet"},
            self.environment,
        )
        start_agent.assert_called_once_with(
            "kimiflow-code-review-2",
            "w2:p2",
            payload["selection"],
            self.session_id,
            self.environment,
            extensions=(calm_extension,),
            read_only=True,
        )
        close_tab.assert_called_once_with(
            "w2", "w2:t2", "w2:p2", self.environment,
        )
        finish_sentinel.assert_called_once_with(sentinel, True)
        self.assertEqual(emitted[-1]["type"], "agent_settled")

    def test_implementation_role_is_write_capable(self):
        self.assertFalse(pi_herdr.SUBAGENT_ROLES["implementation"])
        self.assertTrue(pi_herdr.SUBAGENT_ROLES["code_review"])

    def test_exact_endpoint_close_never_guesses_another_tab(self):
        wrong = {
            "workspace_id": "w2",
            "tab_id": "w2:t9",
            "pane_id": "w2:p2",
        }
        with mock.patch.object(
            pi_herdr,
            "_pane",
            return_value=wrong,
        ), mock.patch.object(pi_herdr, "_invoke") as invoke:
            self.assertFalse(
                pi_herdr._close_exact_ids(
                    "w2", "w2:t2", "w2:p2", self.environment,
                ),
            )
        invoke.assert_not_called()

    def test_cleanup_failure_stays_durable_and_blocks_delayed_worker_reuse(self):
        state_path, endpoint_directory = pi_herdr._state_paths(
            self.root, "worker-00000001",
        )
        os.mkdir(endpoint_directory, 0o700)
        state = {
            "schema_version": 1,
            "root": self.root,
            "worker_id": "worker-00000001",
            "session_id": self.session_id,
            "workspace_id": "w2",
            "tab_id": "w2:t2",
            "pane_id": "w2:p2",
            "session_path": self.session_path,
            "cleanup_pending": False,
        }
        pi_herdr._write_state(state_path, state)
        with mock.patch.object(
            pi_herdr,
            "_close_exact_ids",
            return_value=False,
        ), mock.patch.object(
            pi_herdr,
            "_context",
            return_value={
                "workspace_id": "w2",
                "tab_id": "w2:t1",
                "pane_id": "w2:p1",
            },
        ), mock.patch.object(
            pi_herdr,
            "_prompt",
        ) as prompt_worker, mock.patch.object(
            pi_herdr,
            "_create_endpoint",
        ) as create_worker:
            self.assertFalse(pi_herdr._cleanup_tracked_endpoint(
                state, state_path, endpoint_directory, self.environment,
            ))
            self.assertTrue(pi_herdr._load_state(state_path)["cleanup_pending"])
            with self.assertRaises(pi_herdr.HerdrError) as raised:
                pi_herdr.run_turn(
                    {
                        "root": self.root,
                        "session_id": self.session_id,
                    },
                    {},
                    {},
                    "delayed mutation",
                    self.environment,
                    lambda _value: None,
                )
            self.assertEqual(raised.exception.status, "herdr_cleanup_pending")
            prompt_worker.assert_not_called()
            create_worker.assert_not_called()
        with mock.patch.object(
            pi_herdr,
            "_close_exact_ids",
            return_value=True,
        ):
            self.assertTrue(pi_herdr._cleanup_tracked_endpoint(
                pi_herdr._load_state(state_path),
                state_path,
                endpoint_directory,
                self.environment,
            ))
        self.assertIsNone(pi_herdr._load_state(state_path))
        self.assertFalse(os.path.exists(endpoint_directory))

    def test_terminate_retains_cleanup_pending_state_until_verified_close(self):
        state_path, endpoint_directory = pi_herdr._state_paths(
            self.root, "worker-00000001",
        )
        os.mkdir(endpoint_directory, 0o700)
        state = {
            "schema_version": 1,
            "root": self.root,
            "worker_id": "worker-00000001",
            "session_id": self.session_id,
            "workspace_id": "w2",
            "tab_id": "w2:t2",
            "pane_id": "w2:p2",
            "session_path": self.session_path,
            "cleanup_pending": False,
        }
        pi_herdr._write_state(state_path, state)
        with mock.patch.object(pi_herdr, "_close_exact_ids", return_value=False):
            self.assertFalse(pi_herdr.terminate(
                self.root, self.session_id, self.environment,
            ))
        self.assertTrue(pi_herdr._load_state(state_path)["cleanup_pending"])
        with mock.patch.object(pi_herdr, "_close_exact_ids", return_value=True):
            self.assertTrue(pi_herdr.terminate(
                self.root, self.session_id, self.environment,
            ))
        self.assertIsNone(pi_herdr._load_state(state_path))
        self.assertFalse(os.path.exists(endpoint_directory))

    def test_controller_eof_sentinel_persists_failed_cleanup(self):
        state_path, endpoint_directory = pi_herdr._state_paths(
            self.root, "worker-00000001",
        )
        os.mkdir(endpoint_directory, 0o700)
        pi_herdr._write_state(state_path, {
            "schema_version": 1,
            "root": self.root,
            "worker_id": "worker-00000001",
            "session_id": self.session_id,
            "workspace_id": "w2",
            "tab_id": "w2:t2",
            "pane_id": "w2:p2",
            "session_path": self.session_path,
            "cleanup_pending": False,
        })
        control_read, control_write = os.pipe()
        ready_read, ready_write = os.pipe()
        os.close(control_write)
        with mock.patch.object(
            pi_herdr,
            "_close_exact_ids",
            return_value=False,
        ), mock.patch.dict(os.environ, self.environment, clear=True):
            status = pi_herdr._sentinel(
                control_read,
                ready_write,
                "w2",
                "w2:t2",
                "w2:p2",
                self.root,
                "worker-00000001",
            )
        self.assertEqual(os.read(ready_read, 64), b"ready\n")
        os.close(ready_read)
        self.assertEqual(status, 1)
        self.assertTrue(pi_herdr._load_state(state_path)["cleanup_pending"])


if __name__ == "__main__":
    unittest.main()
