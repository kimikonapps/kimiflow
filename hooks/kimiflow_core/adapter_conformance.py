"""Behavioral conformance checks for optional command-agent adapters."""

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from . import model_adapter


class ConformanceError(ValueError):
    pass


def _digest_tree(root):
    digest = hashlib.sha256()
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name != ".git")
        for name in sorted(files):
            path = os.path.join(base, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                digest.update((relative + "\0unsafe\0").encode("utf-8"))
                continue
            digest.update(relative.encode("utf-8") + b"\0")
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _probe_prompt(operation, marker, probes):
    payload = {
        "contract": "kimiflow-adapter-conformance-v1",
        "operation": operation,
        "marker": marker,
        "requirements": ["files", "shell", "tests", "gates"],
        "probes": probes,
    }
    return (
        "Execute this deterministic Kimiflow adapter conformance probe exactly once. "
        "Do not access paths outside the supplied root. Probe: "
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    )


def _write_probe(path, source):
    Path(path).write_text(source, encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _behavior_probes(root, contract, start_marker):
    directory_name = ".kimiflow-conformance-probes"
    directory = os.path.join(root, directory_name)
    os.mkdir(directory, 0o700)
    markers = {
        name: name + "-" + hashlib.sha256(
            (contract + start_marker + name).encode("ascii")
        ).hexdigest()[:24]
        for name in ("shell", "tests", "gates")
    }
    receipt_paths = {
        name: os.path.join(directory_name, name + ".receipt")
        for name in markers
    }
    _write_probe(
        os.path.join(directory, "shell_probe.py"),
        "from pathlib import Path\n"
        "Path(%r).write_text(%r, encoding='utf-8')\n"
        % (receipt_paths["shell"], markers["shell"] + "\n"),
    )
    _write_probe(
        os.path.join(directory, "test_probe.py"),
        "import unittest\nfrom pathlib import Path\n"
        "class Probe(unittest.TestCase):\n"
        "    def test_adapter_test_capability(self):\n"
        "        Path(%r).write_text(%r, encoding='utf-8')\n"
        "if __name__ == '__main__': unittest.main()\n"
        % (receipt_paths["tests"], markers["tests"] + "\n"),
    )
    _write_probe(
        os.path.join(directory, "gate_probe.py"),
        "from pathlib import Path\n"
        "required = [(%r, %r), (%r, %r)]\n"
        "if any(Path(path).read_text(encoding='utf-8') != marker for path, marker in required):\n"
        "    raise SystemExit(1)\n"
        "Path(%r).write_text(%r, encoding='utf-8')\n"
        % (
            receipt_paths["shell"], markers["shell"] + "\n",
            receipt_paths["tests"], markers["tests"] + "\n",
            receipt_paths["gates"], markers["gates"] + "\n",
        ),
    )
    probes = {
        "shell": "python3 %s/shell_probe.py" % directory_name,
        "tests": "python3 %s/test_probe.py -q" % directory_name,
        "gates": "python3 %s/gate_probe.py" % directory_name,
    }
    return probes, receipt_paths, markers


def _marker_valid(root, name, marker):
    path = os.path.join(root, name)
    try:
        info = os.stat(path, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
            return False
        return Path(path).read_text(encoding="utf-8") == marker + "\n"
    except (OSError, UnicodeError):
        return False


def run(executable, project_root=None, model=None, environ=None):
    """Run bounded start/resume behavior checks without touching ``project_root``."""
    original = _digest_tree(project_root) if project_root else None
    events = []
    adapter = model_adapter.CommandAgentAdapter(
        executable,
        model=model,
        event_sink=events.append,
        environ=environ,
    )
    try:
        info = adapter.info()
    except (model_adapter.AdapterError, OSError, ValueError) as exc:
        raise ConformanceError("capabilities_invalid:%s" % exc)
    contract = adapter.contract_fingerprint()
    checks = {"capabilities": "passed"}
    with tempfile.TemporaryDirectory(prefix="kimiflow-adapter-conformance-") as owned:
        root = os.path.join(owned, "project")
        sibling = os.path.join(owned, "outside-canary")
        os.mkdir(root, 0o700)
        Path(os.path.join(root, "fixture.txt")).write_text("fixture\n", encoding="utf-8")
        start_marker = "start-" + hashlib.sha256(contract.encode("ascii")).hexdigest()[:16]
        resume_marker = "resume-" + hashlib.sha256((contract + start_marker).encode("ascii")).hexdigest()[:16]
        probes, receipt_paths, markers = _behavior_probes(
            root, contract, start_marker
        )
        sessions = []
        start = adapter.start(
            root, _probe_prompt("start", start_marker, probes), sessions.append
        )
        checks["start"] = "passed" if start.returncode == 0 and start.session_id else "failed"
        checks["files"] = "passed" if _marker_valid(root, "start.txt", start_marker) else "failed"
        for capability in ("shell", "tests", "gates"):
            checks[capability] = (
                "passed"
                if _marker_valid(
                    root, receipt_paths[capability], markers[capability]
                )
                else "failed"
            )
        if start.returncode == 0 and start.session_id:
            resume = adapter.resume(
                root,
                start.session_id,
                _probe_prompt("resume", resume_marker, probes),
                sessions.append,
            )
        else:
            resume = model_adapter.TurnResult(1, error_code="start_failed")
        checks["resume"] = "passed" if resume.returncode == 0 and _marker_valid(root, "resume.txt", resume_marker) else "failed"
        checks["usage"] = "passed" if start.usage is not None and resume.usage is not None else "failed"
        if info.get("features", {}).get("structured_events") is True:
            event_types = {event.get("type") for event in events}
            checks["structured_events"] = (
                "passed"
                if {"progress", "tool.started", "tool.completed", "test.completed", "turn.completed"}.issubset(event_types)
                else "failed"
            )
        else:
            checks["structured_events"] = "not_claimed"
        if info.get("features", {}).get("root_confinement") is True:
            checks["root_confinement"] = "passed" if not os.path.lexists(sibling) else "failed"
        else:
            checks["root_confinement"] = "not_claimed"
        # Timeout/cancellation and stream bounds are owned and unit-tested by the
        # Kimiflow controller, not delegated to an untrusted adapter process.
        checks["timeout_cancel"] = "controller_enforced"
    if project_root and _digest_tree(project_root) != original:
        checks["project_preservation"] = "failed"
    else:
        checks["project_preservation"] = "passed"
    failed = sorted(key for key, value in checks.items() if value == "failed")
    return {
        "schema_version": 1,
        "status": "compatible" if not failed else "incompatible",
        "adapter": info["name"],
        "host": info["host"],
        "contract_fingerprint": contract,
        "capabilities": sorted(key for key, value in info["capabilities"].items() if value is True),
        "features": sorted(key for key, value in info.get("features", {}).items() if value is True),
        "checks": checks,
        "failed_checks": failed,
    }


def _write_receipt(path, value):
    target = os.path.realpath(path)
    parent = os.path.dirname(target)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".adapter-conformance-", dir=parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="adapter-conformance")
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--project-root")
    parser.add_argument("--model")
    parser.add_argument("--output")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = run(args.adapter, project_root=args.project_root, model=args.model)
        if args.output:
            _write_receipt(args.output, value)
        print(json.dumps(value, sort_keys=True, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0 if value["status"] == "compatible" else 1
    except (ConformanceError, model_adapter.AdapterError, OSError, ValueError) as exc:
        print("adapter-conformance: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
