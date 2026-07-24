import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kimiflow_core import program_engine


def digest(data):
    return hashlib.sha256(data).hexdigest()


def completion(run, intent_sha, commit):
    return {
        "run": run,
        "claim_sha256": "1" * 64,
        "intent_sha256": intent_sha,
        "state_sha256": "2" * 64,
        "verification_sha256": "3" * 64,
        "commit": commit,
    }


class TestProgramEngine(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        subprocess.run(["git", "init", "-q", self.root], check=True)
        subprocess.run(["git", "-C", self.root, "config", "user.name", "Kimiflow Test"], check=True)
        subprocess.run(
            ["git", "-C", self.root, "config", "user.email", "kimiflow@example.test"],
            check=True,
        )
        with open(os.path.join(self.root, ".gitignore"), "w", encoding="utf-8") as handle:
            handle.write(".kimiflow/\n")
        with open(os.path.join(self.root, "app.py"), "w", encoding="utf-8") as handle:
            handle.write("VALUE = 1\n")
        subprocess.run(["git", "-C", self.root, "add", "."], check=True)
        subprocess.run(["git", "-C", self.root, "commit", "-qm", "fixture"], check=True)
        self.head = self.git("rev-parse", "HEAD")
        self.program_dir = os.path.join(self.root, ".kimiflow", "programs", "demo")
        os.makedirs(self.program_dir)
        self.program_path = os.path.join(self.program_dir, "PROGRAM.json")
        self.intent = {}
        for name in ("run-a", "run-b", "run-c"):
            run = os.path.join(self.root, ".kimiflow", name)
            os.makedirs(run)
            body = ("Goal for %s\n" % name).encode()
            with open(os.path.join(run, "INTENT.md"), "wb") as handle:
                handle.write(body)
            self.intent[name] = digest(body)
        self.program = self.make_program()
        self.write_program(self.program)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.root] + list(args), text=True).strip()

    def make_program(self, one_task=False):
        tasks = [
            {
                "id": "a",
                "goal": "Build the foundation.",
                "order": 1,
                "depends_on": [],
                "run": ".kimiflow/run-a",
                "intent_sha256": self.intent["run-a"],
                "status": "pending",
                "completion_evidence": None,
            }
        ]
        if not one_task:
            tasks.extend(
                [
                    {
                        "id": "b",
                        "goal": "Build the dependent feature.",
                        "order": 2,
                        "depends_on": ["a"],
                        "run": ".kimiflow/run-b",
                        "intent_sha256": self.intent["run-b"],
                        "status": "pending",
                        "completion_evidence": None,
                    },
                    {
                        "id": "c",
                        "goal": "Build the independent feature.",
                        "order": 3,
                        "depends_on": [],
                        "run": ".kimiflow/run-c",
                        "intent_sha256": self.intent["run-c"],
                        "status": "pending",
                        "completion_evidence": None,
                    },
                ]
            )
        return {
            "schema_version": 1,
            "id": "demo",
            "goal": "Deliver the complete program.",
            "status": "active",
            "acceptance": [{"id": "AC-P1", "description": "The program passes its final check."}],
            "tasks": tasks,
            "checks": [
                {
                    "id": "final",
                    "acceptance_refs": ["AC-P1"],
                    "argv": ["python3", "-c", "print('ok')"],
                    "status": "pending",
                    "receipt": None,
                }
            ],
            "activation": None,
        }

    def write_program(self, program):
        with open(self.program_path, "w", encoding="utf-8") as handle:
            json.dump(program, handle, sort_keys=True, indent=2)
            handle.write("\n")

    def read_program(self):
        with open(self.program_path, encoding="utf-8") as handle:
            return json.load(handle)

    def terminalize(self, run_name, architecture="passed", research="stable", status="done"):
        run = os.path.join(self.root, ".kimiflow", run_name)
        with open(os.path.join(run, "STATE.md"), "w", encoding="utf-8") as handle:
            handle.write("Status: %s\nPhase 6: done\nPhase 7: done\n" % status)
        with open(os.path.join(run, "VERIFICATION.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "<!-- kimiflow:verification outcome=passed criteria=passed regression=passed -->\n"
                "<!-- kimiflow:conformance contract=1 status=converged diff=passed "
                "strategy=passed architecture=%s research=%s scope=passed "
                "decisions=1 checks=1 verifier=independent source=current-run -->\n"
                % (architecture, research)
            )

    def test_program_validation_rejects_invalid_graph(self):
        validated = program_engine.load_program(self.program_path)
        self.assertEqual(validated["id"], "demo")

        cases = []
        empty_checks = copy.deepcopy(self.program)
        empty_checks["checks"] = []
        cases.append(empty_checks)
        duplicate_order = copy.deepcopy(self.program)
        duplicate_order["tasks"][1]["order"] = 1
        cases.append(duplicate_order)
        cycle = copy.deepcopy(self.program)
        cycle["tasks"][0]["depends_on"] = ["b"]
        cases.append(cycle)
        uncovered = copy.deepcopy(self.program)
        uncovered["checks"][0]["acceptance_refs"] = []
        cases.append(uncovered)
        two_active = copy.deepcopy(self.program)
        two_active["tasks"][0]["status"] = "active"
        two_active["tasks"][1]["status"] = "active"
        cases.append(two_active)
        active_unmet = copy.deepcopy(self.program)
        active_unmet["tasks"][1]["status"] = "active"
        active_unmet["activation"] = {
            "task_id": "b",
            "claim_digest": digest(
                program_engine._claim_bytes(active_unmet, active_unmet["tasks"][1])
            ),
            "linearized": False,
            "acknowledged": False,
        }
        cases.append(active_unmet)
        pending_activation_unmet = copy.deepcopy(self.program)
        pending_activation_unmet["activation"] = {
            "task_id": "b",
            "claim_digest": digest(
                program_engine._claim_bytes(
                    pending_activation_unmet, pending_activation_unmet["tasks"][1]
                )
            ),
            "linearized": False,
            "acknowledged": False,
        }
        cases.append(pending_activation_unmet)
        extra = copy.deepcopy(self.program)
        extra["surprise"] = True
        cases.append(extra)
        for invalid in cases:
            self.write_program(invalid)
            with self.assertRaises(program_engine.ProgramError):
                program_engine.load_program(self.program_path)

    def test_program_validation_rejects_non_string_completion_digest(self):
        program = self.make_program(one_task=True)
        task = program["tasks"][0]
        task["status"] = "completed"
        task["completion_evidence"] = completion(
            task["run"], task["intent_sha256"], self.head
        )
        task["completion_evidence"]["claim_sha256"] = 7
        self.write_program(program)

        with self.assertRaises(program_engine.ProgramError):
            program_engine.load_program(self.program_path)

    def test_program_next_ready_is_stable_and_read_only(self):
        with open(self.program_path, "rb") as handle:
            before = digest(handle.read())
        first = program_engine.next_ready(self.program_path)
        second = program_engine.next_ready(self.program_path)
        self.assertEqual(first["task"]["id"], "a")
        self.assertEqual(first, second)
        with open(self.program_path, "rb") as handle:
            self.assertEqual(before, digest(handle.read()))

        program = self.make_program()
        program["tasks"][0]["status"] = "failed"
        program["tasks"][0]["completion_evidence"] = completion(
            ".kimiflow/run-a", self.intent["run-a"], self.head
        )
        self.write_program(program)
        self.assertEqual(program_engine._next_from(program)["task"]["id"], "c")
        recovering = program_engine.next_ready(self.program_path)
        self.assertEqual(recovering["status"], "recovering")
        self.assertEqual(recovering["task"]["id"], "a")

    def test_program_claim_first_crash_blocks_or_reconciles_different_activation(self):
        with self.assertRaises(program_engine.SimulatedProgramCrash):
            program_engine.activate(self.program_path, "a", write=True, _crash_after="journal")
        with self.assertRaises(program_engine.ProgramError):
            program_engine.activate(self.program_path, "b", write=True)
        current = self.read_program()
        self.assertEqual(current["activation"]["task_id"], "a")
        self.assertEqual(current["tasks"][0]["status"], "active")
        self.assertTrue(current["activation"]["acknowledged"])
        self.assertTrue(
            os.path.isfile(os.path.join(self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.root, ".kimiflow", "run-b", "PROGRAM-CLAIM.json"))
        )

    def test_program_activation_claim_is_exclusive_and_retryable_after_interrupted_program_write(self):
        with self.assertRaises(program_engine.SimulatedProgramCrash):
            program_engine.activate(self.program_path, "a", write=True, _crash_after="claim")
        retried = program_engine.activate(self.program_path, "a", write=True)
        self.assertEqual(retried["task"]["status"], "active")
        self.assertTrue(self.read_program()["activation"]["acknowledged"])
        claim = os.path.join(self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json")
        with open(claim, encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual((payload["program_id"], payload["task_id"]), ("demo", "a"))

    def test_program_preview_never_reconciles_interrupted_activation(self):
        with self.assertRaises(program_engine.SimulatedProgramCrash):
            program_engine.activate(
                self.program_path, "a", write=True, _crash_after="journal"
            )
        self.terminalize("run-a")
        with open(self.program_path, "rb") as handle:
            before = handle.read()
        claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )

        with self.assertRaises(program_engine.ProgramError):
            program_engine.complete_task(self.program_path, "a", write=False)

        with open(self.program_path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertFalse(os.path.exists(claim))

    def test_program_concurrent_activation_is_linearizable_and_leaves_single_matching_claim(self):
        command = [
            "python3",
            "-m",
            "kimiflow_core.program_engine",
            "activate",
            "--program",
            self.program_path,
            "--task",
            "a",
            "--write",
        ]
        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        results = [first.communicate(timeout=20), second.communicate(timeout=20)]
        self.assertEqual([first.returncode, second.returncode], [0, 0], results)
        current = self.read_program()
        self.assertEqual([task["status"] for task in current["tasks"]].count("active"), 1)
        self.assertTrue(current["activation"]["acknowledged"])
        claim = os.path.join(self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json")
        self.assertTrue(os.path.isfile(claim))

    def test_program_cas_requests_durable_atomic_writes(self):
        real_atomic_write = program_engine.store.atomic_write
        with mock.patch.object(
            program_engine.store, "atomic_write", wraps=real_atomic_write
        ) as atomic_write:
            program_engine.activate(self.program_path, "a", write=True)
        self.assertGreaterEqual(atomic_write.call_count, 2)
        self.assertTrue(
            all(call.kwargs.get("durable") is True for call in atomic_write.call_args_list)
        )

    def test_program_claim_rejects_symlinked_or_retargeted_run_parent(self):
        run = os.path.join(self.root, ".kimiflow", "run-a")
        shutil.rmtree(run)
        outside = os.path.join(self.root, "outside")
        os.makedirs(outside)
        os.symlink(outside, run)
        with self.assertRaises(program_engine.ProgramError):
            program_engine.activate(self.program_path, "a", write=True)
        self.assertNotEqual(self.read_program()["tasks"][0]["status"], "active")
        self.assertFalse(os.path.exists(os.path.join(outside, "PROGRAM-CLAIM.json")))

        os.unlink(run)
        os.makedirs(run)
        with open(os.path.join(run, "INTENT.md"), "wb") as handle:
            handle.write(b"Goal for run-a\n")
        with mock.patch("kimiflow_core.program_engine._anchor_is_current", return_value=False):
            with self.assertRaises(program_engine.ProgramError):
                program_engine.activate(self.program_path, "a", write=True)
        self.assertNotEqual(self.read_program()["tasks"][0]["status"], "active")

    def test_program_crash_after_active_cas_keeps_recoverable_claim_binding(self):
        run = os.path.join(self.root, ".kimiflow", "run-a")
        detached = os.path.join(self.root, ".kimiflow", "run-a-detached")
        original_write = program_engine._write_program
        writes = 0

        def replace_after_final_write(*args, **kwargs):
            nonlocal writes
            original_write(*args, **kwargs)
            writes += 1
            if writes == 2:
                os.rename(run, detached)
                os.makedirs(run)
                with open(os.path.join(run, "INTENT.md"), "wb") as handle:
                    handle.write(b"Goal for run-a\n")
                raise program_engine.SimulatedProgramCrash("after active Program CAS")

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=replace_after_final_write,
        ):
            with self.assertRaises(program_engine.SimulatedProgramCrash):
                program_engine.activate(self.program_path, "a", write=True)

        current = self.read_program()
        self.assertEqual(current["tasks"][0]["status"], "active")
        self.assertEqual(current["activation"]["task_id"], "a")
        self.assertFalse(current["activation"]["linearized"])
        self.assertFalse(current["activation"]["acknowledged"])
        self.assertFalse(os.path.exists(os.path.join(run, "PROGRAM-CLAIM.json")))
        self.assertTrue(os.path.isfile(os.path.join(detached, "PROGRAM-CLAIM.json")))
        retried = program_engine.activate(self.program_path, "a", write=True)
        self.assertEqual(retried["task"]["status"], "active")
        self.assertTrue(self.read_program()["activation"]["acknowledged"])
        self.assertTrue(os.path.isfile(os.path.join(run, "PROGRAM-CLAIM.json")))

    def test_program_terminal_parent_swap_keeps_retryable_binding(self):
        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        run = os.path.join(self.root, ".kimiflow", "run-a")
        detached = os.path.join(self.root, ".kimiflow", "run-a-detached")
        original_write = program_engine._write_program
        replaced = False

        def replace_after_terminal_write(*args, **kwargs):
            nonlocal replaced
            original_write(*args, **kwargs)
            if not replaced:
                replaced = True
                os.rename(run, detached)
                shutil.copytree(detached, run)
                os.unlink(os.path.join(run, "PROGRAM-CLAIM.json"))

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=replace_after_terminal_write,
        ):
            with self.assertRaises(program_engine.ProgramError):
                program_engine.complete_task(self.program_path, "a", write=True)

        interrupted = self.read_program()
        self.assertEqual(interrupted["tasks"][0]["status"], "completed")
        self.assertEqual(interrupted["activation"]["task_id"], "a")
        self.assertFalse(os.path.exists(os.path.join(run, "PROGRAM-CLAIM.json")))

        retried = program_engine.complete_task(self.program_path, "a", write=True)
        self.assertEqual(retried["task"]["status"], "completed")
        self.assertIsNone(self.read_program()["activation"])
        self.assertEqual(program_engine.program_status(self.program_path)["status"], "active")

    def test_program_late_terminal_parent_swap_can_rebind_and_retry(self):
        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        run = os.path.join(self.root, ".kimiflow", "run-a")
        detached = os.path.join(self.root, ".kimiflow", "run-a-detached")
        original_write = program_engine._write_program
        writes = 0

        def replace_before_binding_clear(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                os.rename(run, detached)
                shutil.copytree(detached, run)
                os.unlink(os.path.join(run, "PROGRAM-CLAIM.json"))
            original_write(*args, **kwargs)

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=replace_before_binding_clear,
        ):
            with self.assertRaisesRegex(
                program_engine.ProgramError,
                "terminal Run evidence changed during finalization",
            ):
                program_engine.complete_task(self.program_path, "a", write=True)
        self.assertIsNone(self.read_program()["activation"])
        self.assertEqual(program_engine.program_status(self.program_path)["status"], "active")
        self.assertFalse(os.path.exists(os.path.join(run, "PROGRAM-CLAIM.json")))

        retried = program_engine.complete_task(self.program_path, "a", write=True)
        self.assertEqual(retried["task"]["status"], "completed")
        self.assertIsNone(self.read_program()["activation"])
        self.assertTrue(os.path.isfile(os.path.join(run, "PROGRAM-CLAIM.json")))

    def test_terminal_clear_crash_blocks_dependent_until_recovery(self):
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        run = os.path.join(self.root, ".kimiflow", "run-a")
        detached = os.path.join(self.root, ".kimiflow", "run-a-detached")
        original_write = program_engine._write_program
        writes = 0

        def crash_after_binding_clear(*args, **kwargs):
            nonlocal writes
            writes += 1
            original_write(*args, **kwargs)
            if writes == 2:
                os.rename(run, detached)
                shutil.copytree(detached, run)
                os.unlink(os.path.join(run, "PROGRAM-CLAIM.json"))
                raise program_engine.SimulatedProgramCrash("after binding clear")

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=crash_after_binding_clear,
        ):
            with self.assertRaises(program_engine.SimulatedProgramCrash):
                program_engine.complete_task(self.program_path, "a", write=True)

        interrupted = self.read_program()
        self.assertEqual(interrupted["tasks"][0]["status"], "completed")
        self.assertIsNone(interrupted["activation"])
        self.assertEqual(program_engine.next_ready(self.program_path)["task"]["id"], "a")
        self.assertEqual(
            program_engine.program_status(self.program_path)["next"]["task"]["id"],
            "a",
        )
        with self.assertRaises(program_engine.ProgramError):
            program_engine.activate(self.program_path, "b", write=True)

        recovered = program_engine.complete_task(self.program_path, "a", write=True)
        self.assertEqual(recovered["task"]["status"], "completed")
        self.assertEqual(self.read_program()["status"], "active")
        self.assertEqual(program_engine.next_ready(self.program_path)["task"]["id"], "b")

    def test_active_dependent_can_close_before_predecessor_recovery(self):
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        program_engine.complete_task(self.program_path, "a", write=True)
        program_engine.activate(self.program_path, "b", write=True)

        os.unlink(os.path.join(self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"))
        selected = program_engine.next_ready(self.program_path)
        self.assertEqual(selected["status"], "active")
        self.assertEqual(selected["task"]["id"], "b")
        self.assertEqual(
            program_engine.program_status(self.program_path)["next"],
            selected,
        )

        self.terminalize("run-b", status="parked")
        closed = program_engine.close_task(
            self.program_path, "b", "parked", write=True
        )
        self.assertEqual(closed["task"]["status"], "parked")
        self.assertEqual(program_engine.next_ready(self.program_path)["task"]["id"], "a")

        recovered = program_engine.complete_task(self.program_path, "a", write=True)
        self.assertEqual(recovered["task"]["status"], "completed")
        self.assertEqual(self.read_program()["status"], "parked")

    def test_dependency_evidence_change_after_journal_rolls_back_activation(self):
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        program_engine.complete_task(self.program_path, "a", write=True)
        predecessor_claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        original_write = program_engine._write_program
        writes = 0

        def stale_after_journal(*args, **kwargs):
            nonlocal writes
            original_write(*args, **kwargs)
            writes += 1
            if writes == 1:
                os.unlink(predecessor_claim)

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=stale_after_journal,
        ):
            with self.assertRaisesRegex(
                program_engine.ProgramError,
                "dependency evidence stale",
            ):
                program_engine.activate(self.program_path, "b", write=True)

        current = self.read_program()
        self.assertIsNone(current["activation"])
        self.assertEqual(current["tasks"][1]["status"], "pending")
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    self.root, ".kimiflow", "run-b", "PROGRAM-CLAIM.json"
                )
            )
        )
        self.assertEqual(program_engine.next_ready(self.program_path)["task"]["id"], "a")

    def test_dependency_evidence_change_at_active_cas_rolls_back_activation(self):
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        program_engine.complete_task(self.program_path, "a", write=True)
        predecessor_claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        original_write = program_engine._write_program
        writes = 0

        def stale_at_active_cas(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                os.unlink(predecessor_claim)
            original_write(*args, **kwargs)

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=stale_at_active_cas,
        ):
            with self.assertRaisesRegex(
                program_engine.ProgramError,
                "dependency evidence changed during activation",
            ):
                program_engine.activate(self.program_path, "b", write=True)

        current = self.read_program()
        self.assertIsNone(current["activation"])
        self.assertEqual(current["tasks"][1]["status"], "pending")
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    self.root, ".kimiflow", "run-b", "PROGRAM-CLAIM.json"
                )
            )
        )
        self.assertEqual(program_engine.next_ready(self.program_path)["task"]["id"], "a")

    def test_crash_after_active_cas_reconciles_unacknowledged_stale_dependency(self):
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        program_engine.complete_task(self.program_path, "a", write=True)
        predecessor_claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        original_write = program_engine._write_program
        writes = 0

        def crash_after_active_cas(*args, **kwargs):
            nonlocal writes
            original_write(*args, **kwargs)
            writes += 1
            if writes == 2:
                os.unlink(predecessor_claim)
                raise program_engine.SimulatedProgramCrash(
                    "after unacknowledged active CAS"
                )

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=crash_after_active_cas,
        ):
            with self.assertRaises(program_engine.SimulatedProgramCrash):
                program_engine.activate(self.program_path, "b", write=True)

        interrupted = self.read_program()
        self.assertEqual(interrupted["tasks"][1]["status"], "active")
        self.assertFalse(interrupted["activation"]["linearized"])
        self.assertFalse(interrupted["activation"]["acknowledged"])
        with self.assertRaisesRegex(
            program_engine.ProgramError,
            "dependency evidence stale",
        ):
            program_engine.activate(self.program_path, "b", write=True)
        current = self.read_program()
        self.assertEqual(current["tasks"][1]["status"], "pending")
        self.assertIsNone(current["activation"])
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    self.root, ".kimiflow", "run-b", "PROGRAM-CLAIM.json"
                )
            )
        )

    def test_crash_during_rollback_keeps_journal_until_claim_is_released(self):
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        program_engine.complete_task(self.program_path, "a", write=True)
        predecessor_claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        dependent_claim = os.path.join(
            self.root, ".kimiflow", "run-b", "PROGRAM-CLAIM.json"
        )
        original_write = program_engine._write_program
        writes = 0

        def crash_before_rollback_cas(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                os.unlink(predecessor_claim)
            if writes == 3:
                raise program_engine.SimulatedProgramCrash(
                    "after claim release before rollback CAS"
                )
            original_write(*args, **kwargs)

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=crash_before_rollback_cas,
        ):
            with self.assertRaises(program_engine.SimulatedProgramCrash):
                program_engine.activate(self.program_path, "b", write=True)

        interrupted = self.read_program()
        self.assertEqual(interrupted["tasks"][1]["status"], "active")
        self.assertFalse(interrupted["activation"]["linearized"])
        self.assertFalse(interrupted["activation"]["acknowledged"])
        self.assertFalse(os.path.exists(dependent_claim))
        with self.assertRaisesRegex(
            program_engine.ProgramError,
            "dependency evidence stale",
        ):
            program_engine.activate(self.program_path, "b", write=True)
        current = self.read_program()
        self.assertEqual(current["tasks"][1]["status"], "pending")
        self.assertIsNone(current["activation"])
        self.assertFalse(os.path.exists(dependent_claim))

    def test_dependency_change_at_linearization_is_caught_before_acknowledgement(self):
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        program_engine.complete_task(self.program_path, "a", write=True)
        predecessor_claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        original_write = program_engine._write_program
        writes = 0

        def stale_at_linearization(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 3:
                os.unlink(predecessor_claim)
            original_write(*args, **kwargs)

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=stale_at_linearization,
        ):
            with self.assertRaisesRegex(
                program_engine.ProgramError,
                "dependency evidence changed during activation",
            ):
                program_engine.activate(self.program_path, "b", write=True)

        current = self.read_program()
        self.assertEqual(current["tasks"][1]["status"], "pending")
        self.assertIsNone(current["activation"])
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    self.root, ".kimiflow", "run-b", "PROGRAM-CLAIM.json"
                )
            )
        )

    def test_crash_after_linearization_rechecks_before_acknowledgement(self):
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        program_engine.complete_task(self.program_path, "a", write=True)
        predecessor_claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        original_write = program_engine._write_program
        writes = 0

        def crash_after_linearization(*args, **kwargs):
            nonlocal writes
            original_write(*args, **kwargs)
            writes += 1
            if writes == 3:
                os.unlink(predecessor_claim)
                raise program_engine.SimulatedProgramCrash(
                    "after activation linearization"
                )

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=crash_after_linearization,
        ):
            with self.assertRaises(program_engine.SimulatedProgramCrash):
                program_engine.activate(self.program_path, "b", write=True)

        interrupted = self.read_program()
        self.assertEqual(interrupted["tasks"][1]["status"], "active")
        self.assertTrue(interrupted["activation"]["linearized"])
        self.assertFalse(interrupted["activation"]["acknowledged"])
        self.assertEqual(
            program_engine.next_ready(self.program_path)["status"],
            "recovering",
        )
        self.assertEqual(
            program_engine.program_status(self.program_path)["next"]["status"],
            "recovering",
        )
        with self.assertRaisesRegex(
            program_engine.ProgramError,
            "dependency evidence stale",
        ):
            program_engine.activate(self.program_path, "b", write=True)
        current = self.read_program()
        self.assertEqual(current["tasks"][1]["status"], "pending")
        self.assertIsNone(current["activation"])

    def test_own_claim_change_at_linearization_fails_before_acknowledgement(self):
        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        original_write = program_engine._write_program
        writes = 0

        def remove_claim_at_linearization(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 3:
                os.unlink(claim)
            original_write(*args, **kwargs)

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=remove_claim_at_linearization,
        ):
            with self.assertRaisesRegex(
                program_engine.ProgramError,
                "Program claim changed during activation",
            ):
                program_engine.activate(self.program_path, "a", write=True)

        current = self.read_program()
        self.assertEqual(current["tasks"][0]["status"], "pending")
        self.assertIsNone(current["activation"])
        self.assertFalse(os.path.exists(claim))

    def test_retry_does_not_repair_claim_missing_at_fence(self):
        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        original_write = program_engine._write_program
        writes = 0

        def remove_claim_and_crash_at_fence(*args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 3:
                os.unlink(claim)
                original_write(*args, **kwargs)
                raise program_engine.SimulatedProgramCrash(
                    "after ownership-invalid activation fence"
                )
            original_write(*args, **kwargs)

        with mock.patch(
            "kimiflow_core.program_engine._write_program",
            side_effect=remove_claim_and_crash_at_fence,
        ):
            with self.assertRaises(program_engine.SimulatedProgramCrash):
                program_engine.activate(self.program_path, "a", write=True)

        interrupted = self.read_program()
        self.assertTrue(interrupted["activation"]["linearized"])
        self.assertFalse(interrupted["activation"]["acknowledged"])
        self.assertEqual(
            program_engine.next_ready(self.program_path)["status"],
            "recovering",
        )
        self.assertFalse(os.path.exists(claim))

        with self.assertRaisesRegex(
            program_engine.ProgramError,
            "Program claim changed during activation",
        ):
            program_engine.activate(self.program_path, "a", write=True)
        current = self.read_program()
        self.assertEqual(current["tasks"][0]["status"], "pending")
        self.assertIsNone(current["activation"])
        self.assertFalse(os.path.exists(claim))

    def test_stale_acknowledged_active_claim_routes_to_recovery(self):
        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        program_engine.activate(self.program_path, "a", write=True)
        claim = os.path.join(
            self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json"
        )
        os.unlink(claim)

        selected = program_engine.next_ready(self.program_path)
        self.assertEqual(selected["status"], "recovering")
        self.assertEqual(selected["task"]["id"], "a")
        self.assertEqual(
            program_engine.program_status(self.program_path)["next"],
            selected,
        )
        retried = program_engine.activate(self.program_path, "a", write=True)
        self.assertEqual(retried["status"], "active")
        self.assertTrue(os.path.isfile(claim))

    def test_program_activation_claim_is_exclusive_across_programs(self):
        program_engine.activate(self.program_path, "a", write=True)
        other_dir = os.path.join(self.root, ".kimiflow", "programs", "other")
        os.makedirs(other_dir)
        other_path = os.path.join(other_dir, "PROGRAM.json")
        other = self.make_program(one_task=True)
        other["id"] = "other"
        with open(other_path, "w", encoding="utf-8") as handle:
            json.dump(other, handle)
        with self.assertRaises(program_engine.ProgramError):
            program_engine.activate(other_path, "a", write=True)
        self.assertEqual(program_engine.load_program(other_path)["tasks"][0]["status"], "pending")

    def test_program_completion_requires_evidence_and_current_checks(self):
        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        program_engine.activate(self.program_path, "a", write=True)
        with self.assertRaises(program_engine.ProgramError):
            program_engine.complete_task(self.program_path, "a", write=True)
        self.terminalize("run-a")
        completed = program_engine.complete_task(self.program_path, "a", write=True)
        self.assertEqual(completed["task"]["status"], "completed")
        self.assertIsNone(self.read_program()["activation"])

        checked = program_engine.run_check(self.program_path, "final", write=True)
        self.assertEqual(checked["check"]["status"], "passed")
        self.assertEqual(program_engine.program_status(self.program_path)["status"], "completed")
        receipt = self.read_program()["checks"][0]["receipt"]
        self.assertEqual(receipt["contract_sha256"], program_engine.contract_digest(self.read_program()))
        self.assertEqual(receipt["head"], self.head)
        with open(
            os.path.join(self.root, ".kimiflow", "run-a", "VERIFICATION.md"),
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write("changed after check\n")
        status = program_engine.program_status(self.program_path)
        self.assertEqual(status["status"], "active")
        self.assertEqual(status["current_checks"], [])

    def test_program_accepts_not_applicable_conformance_and_exact_terminal_status(self):
        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize(
            "run-a", architecture="not_applicable", research="not_applicable"
        )
        completed = program_engine.complete_task(self.program_path, "a", write=True)
        self.assertEqual(completed["task"]["status"], "completed")

        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        claim = os.path.join(self.root, ".kimiflow", "run-a", "PROGRAM-CLAIM.json")
        os.unlink(claim)
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a", status="parked")
        with self.assertRaises(program_engine.ProgramError):
            program_engine.close_task(self.program_path, "a", "failed", write=True)

    def test_program_rejects_unknown_conformance_verifier(self):
        self.program = self.make_program(one_task=True)
        self.write_program(self.program)
        program_engine.activate(self.program_path, "a", write=True)
        self.terminalize("run-a")
        verification = os.path.join(
            self.root, ".kimiflow", "run-a", "VERIFICATION.md"
        )
        with open(verification, encoding="utf-8") as handle:
            text = handle.read()
        with open(verification, "w", encoding="utf-8") as handle:
            handle.write(text.replace("verifier=independent", "verifier=bogus"))
        with self.assertRaises(program_engine.ProgramError):
            program_engine.complete_task(self.program_path, "a", write=True)


if __name__ == "__main__":
    unittest.main()
