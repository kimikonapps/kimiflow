"""Deterministic, locally reproducible CI test inventory and lane runner."""

import glob
import json
import os
import re
import shutil
import subprocess
import sys


PRODUCTION_SURFACES = {
    "test-gate.sh": "test-gate-unit.sh",
    "test-weakening-scan.sh": "test-weakening-scan-unit.sh",
}
FOCUSED_SURFACES = {
    "test-execution-control.sh": "test-kimiflow-core-unit.sh",
    "test-run-bridge.sh": "test-kimiflow-core-unit.sh",
}
LEGACY_LOCAL_SURFACES = {
    "test-memory-router-parity.sh": "test-memory-router-unit.sh",
}
FULL_REQUIRED_TOOLS = ("bash", "git", "jq", "sqlite3", "shasum")
PINNED_PARITY_TAG = "kimiflow--v0.1.50"
PORTABILITY_MODULES = (
    "kimiflow_core.tests.test_atomic",
    "kimiflow_core.tests.test_run_bridge",
    "kimiflow_core.tests.test_workspace_preflight",
    "memory_router.tests.test_lifecycle",
    "memory_router.tests.test_provider",
    "memory_router.tests.test_recall_quality",
)
PORTABILITY_SHELL_SURFACES = (
    "hooks/test-active-run.sh",
    "hooks/test-intake-gate.sh",
    "hooks/test-install-codex-hooks.sh",
    "hooks/test-hooks-json.sh",
)
SKIP_PATTERNS = (
    re.compile(r"(?m)^SKIP:"),
    re.compile(r"OK \([^\r\n)]*\bskipped=[1-9][0-9]*\b"),
    re.compile(r"(?im)^\s*ok\s+[0-9]+(?:\s+-[^\r\n]*?)?\s+#\s*SKIP(?:\s|$)"),
)


class PlanError(Exception):
    pass


def repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def shell_surfaces(root):
    pattern = os.path.join(root, "hooks", "test-*.sh")
    return tuple(sorted(os.path.basename(path) for path in glob.glob(pattern)))


def inventory(root):
    surfaces = shell_surfaces(root)
    known_special = set(PRODUCTION_SURFACES) | set(FOCUSED_SURFACES) | set(LEGACY_LOCAL_SURFACES)
    missing = sorted(known_special - set(surfaces))
    if missing:
        raise PlanError("declared test surfaces missing: %s" % ", ".join(missing))

    rows = []
    for name in surfaces:
        if name in PRODUCTION_SURFACES:
            category = "production"
            replacement = PRODUCTION_SURFACES[name]
            reason = "production surface; covered by %s" % replacement
        elif name in FOCUSED_SURFACES:
            category = "focused"
            replacement = FOCUSED_SURFACES[name]
            reason = "focused subset of %s" % replacement
        elif name in LEGACY_LOCAL_SURFACES:
            category = "legacy_local"
            replacement = LEGACY_LOCAL_SURFACES[name]
            reason = "retired migration oracle; current runtime covered by %s" % replacement
        else:
            category = "full"
            replacement = None
            reason = "required full-lane suite"
        rows.append({
            "path": "hooks/" + name,
            "category": category,
            "replacement": replacement,
            "reason": reason,
        })

    full_names = {os.path.basename(row["path"]) for row in rows if row["category"] == "full"}
    for row in rows:
        replacement = row["replacement"]
        if replacement and replacement not in full_names:
            raise PlanError("replacement is not in the full lane: %s -> %s" % (row["path"], replacement))
    return tuple(rows)


def lane_commands(root, lane):
    rows = inventory(root)
    if lane == "full":
        return tuple(("bash", row["path"]) for row in rows if row["category"] == "full")
    if lane == "portability":
        return (
            ((sys.executable, "-m", "unittest") + PORTABILITY_MODULES,)
            + tuple(("bash", path) for path in PORTABILITY_SHELL_SURFACES)
        )
    raise PlanError("unknown lane: %s" % lane)


def missing_dependencies(root, lane, which=shutil.which):
    required = FULL_REQUIRED_TOOLS if lane == "full" else ("bash", "git", "jq")
    missing = [tool for tool in required if which(tool) is None]
    if lane == "full":
        probe = subprocess.run(
            ["git", "-C", root, "cat-file", "-e", PINNED_PARITY_TAG + ":hooks/memory-router.sh"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) if which("git") else None
        if probe is None or probe.returncode != 0:
            missing.append("git-tag:" + PINNED_PARITY_TAG)
    return tuple(missing)


def skip_signal(output, reject_resource_warning=False):
    if reject_resource_warning and "ResourceWarning" in output:
        return "resource-warning"
    for pattern in SKIP_PATTERNS:
        if pattern.search(output):
            return "required-test-skipped"
    return None


def verify(root, lane):
    commands = lane_commands(root, lane)
    missing = missing_dependencies(root, lane)
    if missing:
        raise PlanError("missing required CI dependencies: %s" % ", ".join(missing))
    if not commands:
        raise PlanError("lane has no commands: %s" % lane)
    for command in commands:
        path = command[1] if command[0] == "bash" else None
        if path:
            absolute = os.path.join(root, path)
            if not os.path.isfile(absolute) or not os.access(absolute, os.R_OK):
                raise PlanError("test surface missing or unreadable: %s" % path)
    return {
        "schema_version": 1,
        "lane": lane,
        "commands": [list(command) for command in commands],
        "inventory_count": len(inventory(root)),
        "status": "ready",
    }


def run(root, lane):
    result = verify(root, lane)
    environment = os.environ.copy()
    hooks = os.path.join(root, "hooks")
    environment["PYTHONPATH"] = hooks + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    if lane == "portability":
        environment["PYTHONWARNINGS"] = "error::ResourceWarning"

    for command in result["commands"]:
        sys.stdout.write("=== %s ===\n" % " ".join(command))
        completed = subprocess.run(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
        )
        sys.stdout.write(completed.stdout)
        if completed.returncode != 0:
            raise PlanError("test command failed (%s): %s" % (completed.returncode, " ".join(command)))
        signal = skip_signal(completed.stdout, reject_resource_warning=(lane == "portability"))
        if signal:
            raise PlanError("%s: %s" % (signal, " ".join(command)))
    return result


def _usage():
    return "usage: ci-test-plan.sh <list|verify|run> <full|portability>"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] not in ("list", "verify", "run") or argv[1] not in ("full", "portability"):
        sys.stderr.write(_usage() + "\n")
        return 2
    action, lane = argv
    root = repo_root()
    try:
        if action == "list":
            value = {
                "schema_version": 1,
                "lane": lane,
                "inventory": list(inventory(root)),
                "commands": [list(command) for command in lane_commands(root, lane)],
            }
        elif action == "verify":
            value = verify(root, lane)
        else:
            value = run(root, lane)
        sys.stdout.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")
        return 0
    except (OSError, PlanError) as exc:
        sys.stderr.write("ci-test-plan: %s\n" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
