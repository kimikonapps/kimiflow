import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from kimiflow_core import active_run, run_bridge


class RunBridgeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Kimiflow Test"], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.email", "kimiflow@example.test"], check=True)
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as handle:
            handle.write("fixture\n")
        subprocess.run(["git", "-C", self.root, "add", "README.md"], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "fixture"], check=True)
        head = subprocess.check_output(["git", "-C", self.root, "rev-parse", "HEAD"], text=True).strip()
        self.run = os.path.join(self.root, ".kimiflow", "demo")
        os.makedirs(self.run)
        with open(os.path.join(self.run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "Flow schema: 4\nStatus: active\nMode: feature\nScope: small\nRecovery: clean\n"
                "Affected files:\n- README.md\nPhase reads required: no\n"
                + "\n".join("Phase %s: open" % number for number in range(8))
                + "\n"
            )
        info = os.stat(self.run)
        active = {
            "schema_version": 1,
            "status": "active",
            "run": ".kimiflow/demo",
            "mode": "feature",
            "scope": "small",
            "host": "codex",
            "started_head": head,
            "last_checked_head": head,
            "run_device": info.st_dev,
            "run_inode": info.st_ino,
            "owner": {"host": "codex", "session_id": "owner-thread"},
        }
        os.makedirs(os.path.dirname(active_run.active_file(self.root)), exist_ok=True)
        with open(active_run.active_file(self.root), "w", encoding="utf-8") as handle:
            json.dump(active, handle)
        plugin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        self.env = mock.patch.dict(
            os.environ,
            {"KIMIFLOW_PLUGIN_ROOT": plugin, "KIMIFLOW_HOST": "codex", "CODEX_THREAD_ID": "owner-thread"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def request(self, method, params=None):
        return run_bridge.handle(self.root, {"schema_version": 1, "method": method, "params": params or {}})

    def cursor(self):
        return self.request("run/readiness")["cursor"]

    def mutation(self, action_id, cursor, title="bridge item"):
        return {
            "action_id": action_id,
            "cursor": cursor,
            "operation": "append-item",
            "arguments": {"title": title, "kind": "change"},
            "write": True,
        }

    def items(self):
        return active_run.read_items(os.path.join(self.run, "ITEMS.jsonl"))

    def test_mutation_cursor_and_idempotency_are_fail_closed(self):
        cursor = self.cursor()
        first = self.request("run/mutate", self.mutation("act_first", cursor))
        self.assertEqual(first["status"], "mutated")
        replay = self.request("run/mutate", self.mutation("act_first", cursor))
        self.assertEqual(replay["status"], "replayed")
        self.assertEqual(len(self.items()), 1)
        with self.assertRaises(run_bridge.BridgeError) as reused:
            self.request("run/mutate", self.mutation("act_first", cursor, title="changed payload"))
        self.assertEqual(reused.exception.code, "action_id_reused")

        stale = first["cursor"]
        active_run.main(["append-item", "--root", self.root, "--title", "direct", "--write"])
        with self.assertRaises(run_bridge.BridgeError) as stale_error:
            self.request("run/mutate", self.mutation("act_stale", stale))
        self.assertEqual(stale_error.exception.code, "stale_cursor")

    def test_direct_aba_status_changes_invalidate_the_bridge_cursor(self):
        active_run.main(["append-item", "--root", self.root, "--title", "first", "--write"])
        active_run.main(["append-item", "--root", self.root, "--title", "second", "--write"])
        active_run.main(["mark-built", "--root", self.root, "--id", "item_001", "--write"])
        active_run.main(["mark-accepted", "--root", self.root, "--id", "item_002", "--write"])
        cursor = self.cursor()
        counts = self.request("run/readiness")["readiness"]["active"]["item_counts"]

        active_run.main(["mark-accepted", "--root", self.root, "--id", "item_001", "--write"])
        active_run.main(["mark-built", "--root", self.root, "--id", "item_002", "--write"])

        self.assertEqual(self.request("run/readiness")["readiness"]["active"]["item_counts"], counts)
        with self.assertRaises(run_bridge.BridgeError) as stale:
            self.request("run/mutate", self.mutation("act_after_aba", cursor))
        self.assertEqual(stale.exception.code, "stale_cursor")

    def test_prepared_action_reconciles_crash_before_completion_receipt(self):
        cursor = self.cursor()
        original = run_bridge._write_receipt
        calls = {"count": 0}

        def fail_completed(descriptor, value):
            calls["count"] += 1
            if calls["count"] == 2:
                raise run_bridge.BridgeError("simulated_crash")
            return original(descriptor, value)

        with mock.patch.object(run_bridge, "_write_receipt", side_effect=fail_completed):
            with self.assertRaises(run_bridge.BridgeError):
                self.request("run/mutate", self.mutation("act_crash", cursor))
        self.assertEqual(len(self.items()), 1)
        recovered = self.request("run/mutate", self.mutation("act_crash", cursor))
        self.assertEqual(recovered["status"], "reconciled")
        self.assertEqual(len(self.items()), 1)

    def test_prepared_action_reconciles_after_a_later_action_on_same_item(self):
        cursor = self.cursor()
        original = run_bridge._write_receipt
        calls = {"count": 0}

        def fail_completed(descriptor, value):
            calls["count"] += 1
            if calls["count"] == 2:
                raise run_bridge.BridgeError("simulated_crash")
            return original(descriptor, value)

        with mock.patch.object(run_bridge, "_write_receipt", side_effect=fail_completed):
            with self.assertRaises(run_bridge.BridgeError):
                self.request("run/mutate", self.mutation("act_crash", cursor))

        later = {
            "action_id": "act_later",
            "cursor": self.cursor(),
            "operation": "mark-built",
            "arguments": {"id": "item_001"},
            "write": True,
        }
        self.assertEqual(self.request("run/mutate", later)["status"], "mutated")
        recovered = self.request("run/mutate", self.mutation("act_crash", cursor))

        self.assertEqual(recovered["status"], "reconciled")
        self.assertEqual(recovered["result"]["item_status"], "pending")
        self.assertEqual(len(self.items()), 1)
        self.assertEqual(self.items()[0]["status"], "built")

    def test_missing_or_conflicting_owner_cannot_mutate(self):
        cursor = self.cursor()
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "", "KIMIFLOW_SESSION_ID": ""}):
            with self.assertRaises(run_bridge.BridgeError) as missing:
                self.request("run/mutate", self.mutation("act_missing", cursor))
        self.assertEqual(missing.exception.code, "owner_identity_missing")
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "other-thread"}):
            with self.assertRaises(run_bridge.BridgeError) as conflict:
                self.request("run/mutate", self.mutation("act_conflict", cursor))
        self.assertEqual(conflict.exception.code, "owner_conflict")
        self.assertEqual(self.items(), [])

    def test_invalid_mutation_arguments_do_not_consume_receipt_sequence(self):
        cursor = self.cursor()
        invalid = self.mutation("act_invalid", cursor)
        invalid["arguments"] = {}

        with self.assertRaises(run_bridge.BridgeError) as error:
            self.request("run/mutate", invalid)

        self.assertEqual(error.exception.code, "arguments_invalid")
        self.assertEqual(self.cursor()["sequence"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.run, run_bridge.RECEIPT_NAME)))

    def test_direct_item_writer_uses_same_transaction_lock(self):
        active = active_run.load_active(self.root)
        started = threading.Event()
        finished = threading.Event()

        def direct_writer():
            started.set()
            active_run.main(["append-item", "--root", self.root, "--title", "racer", "--write"])
            finished.set()

        with active_run.item_mutation_lock(self.root, self.run, active):
            worker = threading.Thread(target=direct_writer)
            worker.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.05)
            self.assertFalse(finished.is_set())
        worker.join(2)
        self.assertTrue(finished.is_set())

    def test_item_writer_rejects_run_exchange_while_acquiring_lock(self):
        moved = self.run + "-moved"
        original_flock = active_run.fcntl.flock
        exchanged = {"done": False}

        def exchange_on_lock(descriptor, operation):
            result = original_flock(descriptor, operation)
            if operation == active_run.fcntl.LOCK_EX and not exchanged["done"]:
                exchanged["done"] = True
                os.rename(self.run, moved)
                shutil.copytree(moved, self.run)
            return result

        with mock.patch.object(active_run.fcntl, "flock", side_effect=exchange_on_lock):
            code = active_run.main(["append-item", "--root", self.root, "--title", "must not land", "--write"])

        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.run, "ITEMS.jsonl")))

    def test_item_writer_does_not_mutate_replacement_after_lock_acquisition(self):
        moved = self.run + "-moved"
        original_read = active_run.read_items_descriptor
        exchanged = {"done": False}

        def exchange_before_read(run_descriptor):
            if not exchanged["done"]:
                exchanged["done"] = True
                os.rename(self.run, moved)
                shutil.copytree(moved, self.run)
            return original_read(run_descriptor)

        with mock.patch.object(active_run, "read_items_descriptor", side_effect=exchange_before_read):
            code = active_run.main([
                "append-item", "--root", self.root, "--title", "must stay pinned", "--write",
            ])

        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(os.path.join(self.run, "ITEMS.jsonl")))
        self.assertTrue(os.path.isfile(os.path.join(moved, "ITEMS.jsonl")))

    def test_item_writer_rechecks_owner_after_lock_acquisition(self):
        original_flock = active_run.fcntl.flock
        changed = {"done": False}

        def change_owner_on_lock(descriptor, operation):
            result = original_flock(descriptor, operation)
            if operation == active_run.fcntl.LOCK_EX and not changed["done"]:
                changed["done"] = True
                active = active_run.load_active(self.root)
                active.pop("present", None)
                active["owner"] = {"host": "codex", "session_id": "replacement-owner"}
                active_run.write_active(self.root, active)
            return result

        with mock.patch.object(active_run.fcntl, "flock", side_effect=change_owner_on_lock):
            code = active_run.main([
                "append-item", "--root", self.root, "--title", "stale owner", "--write",
            ])

        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.run, "ITEMS.jsonl")))

    def test_shell_surface_serves_the_same_json_stdio_contract(self):
        plugin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        request = json.dumps({"schema_version": 1, "method": "run/readiness", "params": {}})
        proc = subprocess.run(
            [os.path.join(plugin, "hooks", "run-bridge.sh"), "--root", self.root],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        response = json.loads(proc.stdout)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["readiness"]["schema_version"], 1)

    def test_terminal_scorecard_is_readable_by_explicit_run(self):
        with open(os.path.join(self.run, "OUTCOME-EVALUATION.json"), "w", encoding="utf-8") as handle:
            json.dump({"terminal": "parked", "classification": "inconclusive", "promotable": False}, handle)
        scorecard = run_bridge.scorecard.write(self.root, self.run, terminal="parked")
        os.unlink(active_run.active_file(self.root))

        response = self.request("run/scorecard", {"run": ".kimiflow/demo"})

        self.assertEqual(response["scorecard"], scorecard)

    def test_malformed_active_run_still_returns_bounded_json_stdio(self):
        plugin = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        with open(active_run.active_file(self.root), "w", encoding="utf-8") as handle:
            handle.write("{bad\n")
        request = json.dumps({"schema_version": 1, "method": "run/readiness", "params": {}})

        proc = subprocess.run(
            [os.path.join(plugin, "hooks", "run-bridge.sh"), "--root", self.root],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["readiness"]["readiness"], "blocked")
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
