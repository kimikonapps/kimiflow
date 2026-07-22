import glob
import os
import unittest
from unittest import mock

from kimiflow_core import ci_test_plan


class CiTestPlanCase(unittest.TestCase):
    def setUp(self):
        self.root = ci_test_plan.repo_root()

    def test_inventory_classifies_every_shell_surface_once(self):
        rows = ci_test_plan.inventory(self.root)
        actual = [row["path"] for row in rows]
        expected = sorted(
            os.path.relpath(path, self.root)
            for path in glob.glob(os.path.join(self.root, "hooks", "test-*.sh"))
        )
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(set(actual)))
        self.assertTrue(all(row["category"] in {"full", "production", "focused", "legacy_local"} for row in rows))

    def test_full_lane_excludes_focused_and_legacy_duplicates(self):
        commands = ci_test_plan.lane_commands(self.root, "full")
        paths = {command[1] for command in commands}
        self.assertIn("hooks/test-gate-unit.sh", paths)
        self.assertIn("hooks/test-weakening-scan-unit.sh", paths)
        self.assertIn("hooks/test-kimiflow-core-unit.sh", paths)
        self.assertIn("hooks/test-memory-router-unit.sh", paths)
        self.assertNotIn("hooks/test-execution-control.sh", paths)
        self.assertNotIn("hooks/test-run-bridge.sh", paths)
        self.assertNotIn("hooks/test-memory-router-parity.sh", paths)
        self.assertNotIn("hooks/test-gate.sh", paths)
        self.assertNotIn("hooks/test-weakening-scan.sh", paths)

    def test_portability_lane_targets_platform_sensitive_contracts(self):
        command, *shell_commands = ci_test_plan.lane_commands(self.root, "portability")
        self.assertEqual(command[:3], (ci_test_plan.sys.executable, "-m", "unittest"))
        self.assertEqual(command[3:], ci_test_plan.PORTABILITY_MODULES)
        self.assertEqual(
            tuple(shell_commands),
            tuple(("bash", path) for path in ci_test_plan.PORTABILITY_SHELL_SURFACES),
        )
        self.assertIn(("bash", "hooks/test-active-run.sh"), shell_commands)
        self.assertIn(("bash", "hooks/test-intake-gate.sh"), shell_commands)
        self.assertIn(("bash", "hooks/test-install-codex-hooks.sh"), shell_commands)

    def test_missing_dependency_is_fail_closed(self):
        with mock.patch.object(ci_test_plan, "missing_dependencies", return_value=("jq",)):
            with self.assertRaisesRegex(ci_test_plan.PlanError, "missing required CI dependencies: jq"):
                ci_test_plan.verify(self.root, "full")

    def test_portability_dependencies_match_executed_lane(self):
        available = {"bash", "git", "jq"}
        missing = ci_test_plan.missing_dependencies(
            self.root,
            "portability",
            which=lambda tool: "/usr/bin/" + tool if tool in available else None,
        )
        self.assertEqual(missing, ())

    def test_bash_surfaces_do_not_need_an_execute_bit(self):
        with mock.patch.object(ci_test_plan, "missing_dependencies", return_value=()):
            with mock.patch.object(ci_test_plan.os.path, "isfile", return_value=True):
                with mock.patch.object(ci_test_plan.os, "access", return_value=True) as access:
                    ci_test_plan.verify(self.root, "full")
        self.assertTrue(access.call_args_list)
        self.assertTrue(all(call.args[1] == os.R_OK for call in access.call_args_list))

    def test_skip_and_resource_warning_signals_are_fail_closed(self):
        self.assertEqual(ci_test_plan.skip_signal("SKIP: jq missing\n"), "required-test-skipped")
        self.assertEqual(ci_test_plan.skip_signal("OK (skipped=2)\n"), "required-test-skipped")
        self.assertEqual(
            ci_test_plan.skip_signal("OK (skipped=1, expected failures=1)\n"),
            "required-test-skipped",
        )
        self.assertEqual(
            ci_test_plan.skip_signal("ok 1 - contract # SKIP unavailable\n"),
            "required-test-skipped",
        )
        self.assertIsNone(ci_test_plan.skip_signal("ResourceWarning: legacy shell fixture\n"))
        self.assertEqual(
            ci_test_plan.skip_signal("ResourceWarning: socket\n", reject_resource_warning=True),
            "resource-warning",
        )
        self.assertIsNone(ci_test_plan.skip_signal("ALL GREEN\n"))

    def test_workflow_uses_expected_lanes(self):
        with open(os.path.join(self.root, ".github", "workflows", "ci.yml"), encoding="utf-8") as handle:
            workflow = handle.read()
        self.assertIn("os: ubuntu-latest", workflow)
        self.assertIn("lane: full", workflow)
        self.assertIn("os: macos-latest", workflow)
        self.assertIn("lane: portability", workflow)
        self.assertIn('bash hooks/ci-test-plan.sh run "${{ matrix.lane }}"', workflow)
        for duplicate in (
            "bash hooks/test-memory-router-unit.sh",
            "bash hooks/test-memory-router-parity.sh",
            "bash hooks/test-project-map-status.sh",
        ):
            self.assertNotIn(duplicate, workflow)

    def test_smokes_do_not_inventory_test_method_names(self):
        forbidden = (
            "test_schema4_workspace_wait_is_one_shot",
            "test_schema4_workspace_wait_receipt_survives_park_and_resume",
            "test_targeted_cleanup_preserves_unrelated_prunable_metadata",
        )
        for name in ("smoke-install.sh", "smoke-install-codex.sh"):
            with open(os.path.join(self.root, "hooks", name), encoding="utf-8") as handle:
                content = handle.read()
            for method in forbidden:
                self.assertNotIn(method, content, name)


if __name__ == "__main__":
    unittest.main()
