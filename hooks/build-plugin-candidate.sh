#!/usr/bin/env bash
# Build or verify the clean marketplace plugin directory from tracked source files.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
OUTPUT="$ROOT/plugins/kimiflow"
MODE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --write|--check) MODE="${1#--}"; shift ;;
    --output) [ "$#" -ge 2 ] || { echo "build-plugin-candidate: --output requires a path" >&2; exit 2; }; OUTPUT="$2"; shift 2 ;;
    -h|--help) echo "Usage: hooks/build-plugin-candidate.sh --write|--check [--output PATH]"; exit 0 ;;
    *) echo "build-plugin-candidate: unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$MODE" ] || { echo "build-plugin-candidate: choose --write or --check" >&2; exit 2; }

python3 - "$ROOT" "$OUTPUT" "$MODE" <<'PY'
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys

root, output, mode = sys.argv[1:]
root = os.path.realpath(root)
output = os.path.abspath(output)
canonical_output = os.path.join(root, "plugins", "kimiflow")
try:
    inside_root = os.path.commonpath((root, output)) == root
except ValueError:
    inside_root = False
if os.path.basename(output) != "kimiflow" or output == root or (inside_root and output != canonical_output):
    raise SystemExit("build-plugin-candidate: unsafe output location")
if output == canonical_output:
    parent = os.path.dirname(output)
    if os.path.lexists(parent) and (os.path.islink(parent) or not os.path.isdir(parent)):
        raise SystemExit("build-plugin-candidate: unsafe candidate parent")

def included(path):
    if path.startswith("plugins/") or path.startswith("docs/superpowers/"):
        return False
    if (
        path.startswith("hooks/golden/")
        or "/tests/" in path
        or (
            path.startswith("hooks/test-")
            and path not in {"hooks/test-gate.sh", "hooks/test-weakening-scan.sh"}
        )
        or path.startswith("hooks/smoke-")
        or path in {
            "hooks/build-plugin-candidate.sh",
            "hooks/ci-test-plan.sh",
            "hooks/release-consistency-check.sh",
            "hooks/kimiflow_core/ci_test_plan.py",
            "hooks/kimiflow_core/render.py",
        }
    ):
        return False
    exact = {
        "SKILL.md", "reference.md", "hooks.json", "LICENSE", "README.md", "README.de.md",
        "COMPATIBILITY.md", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json",
        "docs/commit-secret-gate.md", "docs/kimiflow-scaling-knobs.md",
    }
    return path in exact or path.startswith(("hooks/", "phases/", "references/", "skills/"))

raw = subprocess.check_output(
    ["git", "-C", root, "ls-files", "-z", "--cached"]
)
paths = sorted({
    path.decode("utf-8", "surrogateescape")
    for path in raw.split(b"\0")
    if path
    and included(path.decode("utf-8", "surrogateescape"))
    and os.path.exists(os.path.join(root, path.decode("utf-8", "surrogateescape")))
})
if not paths:
    raise SystemExit("build-plugin-candidate: empty allowlist")

def source_rows():
    rows = []
    for rel in paths:
        source = os.path.join(root, rel)
        info = os.lstat(source)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SystemExit("build-plugin-candidate: unsafe tracked source: %s" % rel)
        with open(source, "rb") as handle:
            payload = handle.read()
        file_mode = "0755" if info.st_mode & 0o111 else "0644"
        rows.append((rel, file_mode, payload))
    return rows

def fingerprint(rows):
    digest = hashlib.sha256()
    files = []
    for rel, file_mode, payload in rows:
        digest.update(rel.encode("utf-8", "surrogateescape") + b"\0")
        digest.update(file_mode.encode("ascii") + b"\0")
        digest.update(str(len(payload)).encode("ascii") + b"\0" + payload + b"\0")
        files.append({
            "path": rel,
            "mode": file_mode,
            "bytes": len(payload),
            "sha256": "sha256:%s" % hashlib.sha256(payload).hexdigest(),
        })
    return "sha256:%s" % digest.hexdigest(), files

rows = source_rows()
runtime_fingerprint, files = fingerprint(rows)
manifest = {
    "schema_version": 1,
    "runtime_fingerprint": runtime_fingerprint,
    "file_count": len(files),
    "files": files,
}
manifest_payload = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")

def write_candidate(target):
    if os.path.lexists(target):
        if os.path.islink(target) or not os.path.isdir(target):
            raise SystemExit("build-plugin-candidate: unsafe output path")
        marker = os.path.join(target, "RUNTIME-FINGERPRINT.json")
        if not os.path.isfile(marker) or os.path.islink(marker):
            raise SystemExit("build-plugin-candidate: refusing to replace an unmanaged output directory")
        shutil.rmtree(target)
    os.makedirs(target, mode=0o755)
    for rel, file_mode, payload in rows:
        target_path = os.path.join(target, rel)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as handle:
            handle.write(payload)
        os.chmod(target_path, int(file_mode, 8))
    with open(os.path.join(target, "RUNTIME-FINGERPRINT.json"), "wb") as handle:
        handle.write(manifest_payload)
    os.chmod(os.path.join(target, "RUNTIME-FINGERPRINT.json"), 0o644)

def inventory(target):
    result = []
    directories = []
    for base, dirs, names in os.walk(target, followlinks=False):
        dirs.sort(); names.sort()
        for name in dirs:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, target).replace(os.sep, "/")
            if os.path.islink(full) or not os.path.isdir(full):
                raise SystemExit("build-plugin-candidate: unsafe candidate directory: %s" % rel)
            directories.append(rel)
        for name in names:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, target).replace(os.sep, "/")
            if os.path.islink(full) or not os.path.isfile(full):
                raise SystemExit("build-plugin-candidate: unsafe candidate entry: %s" % rel)
            result.append(rel)
    return sorted(result), sorted(directories)

if mode == "write":
    write_candidate(output)
    print(json.dumps({"status": "written", "output": output, "runtime_fingerprint": runtime_fingerprint, "file_count": len(files)}, sort_keys=True))
else:
    if not os.path.isdir(output) or os.path.islink(output):
        raise SystemExit("build-plugin-candidate: candidate missing")
    expected = sorted(paths + ["RUNTIME-FINGERPRINT.json"])
    expected_dirs = set()
    for rel in expected:
        parent = os.path.dirname(rel)
        while parent:
            expected_dirs.add(parent)
            parent = os.path.dirname(parent)
    actual, actual_dirs = inventory(output)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))[:5]
        extra = sorted(set(actual) - set(expected))[:5]
        raise SystemExit(
            "build-plugin-candidate: candidate file inventory drift "
            "(missing=%s extra=%s)" % (missing, extra)
        )
    if actual_dirs != sorted(expected_dirs):
        raise SystemExit("build-plugin-candidate: candidate directory inventory drift")
    for rel, file_mode, payload in rows:
        target = os.path.join(output, rel)
        with open(target, "rb") as handle:
            if handle.read() != payload:
                raise SystemExit("build-plugin-candidate: candidate content drift: %s" % rel)
        actual_mode = "0755" if os.stat(target).st_mode & 0o111 else "0644"
        if actual_mode != file_mode:
            raise SystemExit("build-plugin-candidate: candidate mode drift: %s" % rel)
    with open(os.path.join(output, "RUNTIME-FINGERPRINT.json"), "rb") as handle:
        if handle.read() != manifest_payload:
            raise SystemExit("build-plugin-candidate: runtime fingerprint manifest drift")
    forbidden = (".git/", ".kimiflow/", ".superpowers/", "docs/superpowers/", "__pycache__/")
    if any(rel.startswith(forbidden) or rel.endswith((".pyc", ".pyo")) for rel in actual):
        raise SystemExit("build-plugin-candidate: forbidden private or generated path")
    print(json.dumps({"status": "current", "output": output, "runtime_fingerprint": runtime_fingerprint, "file_count": len(files)}, sort_keys=True))
PY
