"""Python port of hooks/active-run.sh."""

import contextlib
import fnmatch
import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from . import execution_control, flow_graph, phase_reads, state, workspace_preflight
from .atomic import atomic_write

try:
    import fcntl
except ImportError:
    fcntl = None


USAGE = """#!/usr/bin/env bash
# kimiflow — active session contract helper and hooks.
#
# Orchestrator commands:
#   active-run.sh status [--root <path>] [--pretty]
#   active-run.sh next-action [--root <path>] [--event <event>] [--pretty]
#   active-run.sh observe [--root <path>] [--event <event>] [--outcome <outcome>] [--evidence <run-artifact>] [--model-calls N] [--tool-calls N] [--input-tokens N] [--output-tokens N] [--write] [--pretty]
#   active-run.sh start --run <path> [--root <path>] [--mode <mode>] [--scope <scope>] [--host <host>] [--write] [--pretty]
#   active-run.sh conflict-check [--root <path>] [--path <path>]... [--pretty]
#   active-run.sh append-item --title <text> [--kind <kind>] [--root <path>] [--write] [--pretty]
#   active-run.sh mark-built|mark-accepted --id <id> [--root <path>] [--write] [--pretty]
#   active-run.sh mark-rejected|drop-item --id <id> --reason <text> [--root <path>] [--write] [--pretty]
#   active-run.sh refresh-baseline [--root <path>] [--workspace-disposition] [--write] [--pretty]
#   active-run.sh await-user --run <path> [--kind <kind>] [--reason <text>] [--root <path>] [--write] [--pretty]
#   active-run.sh phase-read --run <path> --phase <0-7> --file phases/<file>.md [--root <path>] [--write] [--pretty]
#   active-run.sh phase-read-status --run <path> [--root <path>] [--json] [--pretty]
#   active-run.sh phase-read-gate --run <path> --through-phase <0-7> [--root <path>]
#   active-run.sh finish [--root <path>] [--write] [--skip-learning <reason>] [--pretty]
#   active-run.sh park|fail|abort [--root <path>] --reason <text> [--write] [--pretty]
#
# Hook commands:
#   active-run.sh session-bootstrap
#   active-run.sh owner-check
#   active-run.sh prompt-context
#   active-run.sh stop-gate
#
# R2 invariant examples:
#   hooks/active-run.sh start --run .kimiflow/<slug>
#   refresh-baseline --write
"""


class ActiveError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.message = message
        self.code = code


RECOVERY_AWAIT_USER_KINDS = {
    "missing-input",
    "authority",
    "external-access",
    "paid-privacy",
    "scope-risk",
    "irreversible",
    "workspace",
}
AWAIT_USER_KINDS = RECOVERY_AWAIT_USER_KINDS | {"preview", "commit"}
FINISH_ARTIFACT_LIMIT = 8 * 1024 * 1024
FINISH_SNAPSHOT_NAMES = (
    "STATE.md",
    "LEARNING-REVIEW.md",
    "RUN-LIFECYCLE.json",
    "RUN-LIFECYCLE.md",
    "OUTCOME-EVALUATION.json",
    "SESSION-OUTCOME.json",
    execution_control.TRACE_NAME,
)


def usage():
    sys.stderr.write(USAGE)


def die(message, code=1):
    raise ActiveError(message, code)


def need_jq():
    if not shutil.which("jq"):
        die("jq is required", 2)


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_cmd(args, cwd=None, env=None, stderr_to_stdout=False, cwd_descriptor=None):
    process_options = {}
    if cwd_descriptor is not None:
        process_options["pass_fds"] = (cwd_descriptor,)
        process_options["preexec_fn"] = lambda: os.fchdir(cwd_descriptor)
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if stderr_to_stdout else subprocess.PIPE,
            text=True,
            check=False,
            **process_options,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def run_git(root, args):
    return run_cmd(["git", "-C", root] + list(args))


def resolve_root(root_arg="", strict=False):
    if root_arg:
        if os.path.isdir(root_arg):
            return os.path.abspath(root_arg)
        # R1 root divergence: mutating --write commands pass strict=True so
        # active-session state is never written under a known-invalid root.
        if strict:
            die("cannot resolve root: %s" % root_arg, 2)
        return root_arg
    proc = run_cmd(["git", "rev-parse", "--show-toplevel"])
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return os.getcwd()


def rel_path(root, path):
    root = os.path.normpath(root)
    path = os.path.normpath(path)
    prefix = root + os.sep
    if path.startswith(prefix):
        return path[len(prefix) :]
    return path


def json_compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def json_print(value, pretty=False):
    sys.stdout.write((json_pretty(value) if pretty else json_compact(value)) + "\n")


def active_file(root):
    return os.path.join(root, ".kimiflow/session/ACTIVE_RUN.json")


def resolve_run_dir(root, run):
    if not run:
        die("run path is required", 2)
    if run.startswith(".kimiflow/"):
        path = os.path.join(root, run)
    elif run.startswith(os.path.join(root, ".kimiflow/")):
        path = run
    else:
        die("run path must be under .kimiflow/<slug>", 2)
    if "/../" in path or path.endswith("/..") or "/./" in path:
        die("run path must not contain relative traversal", 2)
    return path


def pathish_affected_entry(value):
    names = {"Dockerfile", "Containerfile", "Makefile", "Procfile", "Justfile", "Rakefile", "Gemfile", "Vagrantfile"}
    return "/" in value or "." in value or value in names


# Accepted "Affected" header set (case-insensitive) — keep in sync with
# file_declares_affected_paths in hooks/plan-blocker-gate.sh: every header/source the plan
# gate accepts must also be visible here (run_affected_paths mirrors the gate's
# STATE.md-or-PLAN.md acceptance), or a plan passes the gate but staleness stays unknown
# and finish wedges.
AFFECTED_HEADER_RE = re.compile(r"^[ \t]*(Affected files|Affected paths|Files|Paths|Touches)[ \t]*:[ \t]*(.*)$", re.IGNORECASE)


def affected_paths(path):
    if not os.path.isfile(path):
        return []
    out = []
    in_list = False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n").replace("\r", "").replace("**", "")
                plain = re.sub(r"^[ \t]*-[ \t]*", "", line)
                match = AFFECTED_HEADER_RE.match(plain)
                if match:
                    rest = match.group(2)
                    if rest:
                        for part in re.split(r",[ \t]*", rest):
                            add_affected(out, part)
                    in_list = True
                    continue
                if in_list and re.match(r"^[ \t]*-[ \t]+", line):
                    add_affected(out, re.sub(r"^[ \t]*-[ \t]+", "", line))
                    continue
                if in_list and not re.match(r"^[ \t]*$", line):
                    in_list = False
    except OSError:
        pass
    return out


def add_affected(out, raw):
    path = raw.strip()
    if path and pathish_affected_entry(path) and path not in out:
        out.append(path)


def run_affected_paths(run_dir):
    # STATE.md is authoritative; fall back to PLAN.md like the plan gate does
    # (file_declares_affected_paths accepts either), so a run that declares its
    # affected paths only in PLAN.md stays visible to staleness.
    paths = affected_paths(os.path.join(run_dir, "STATE.md"))
    if paths:
        return paths
    return affected_paths(os.path.join(run_dir, "PLAN.md"))


def git_head(root):
    proc = run_git(root, ["rev-parse", "HEAD"])
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "NOT VERIFIED"


def git_commit_ok(root, commit):
    if not commit or commit == "NOT VERIFIED":
        return False
    proc = run_git(root, ["cat-file", "-e", "%s^{commit}" % commit])
    return proc.returncode == 0


def changed_paths(root, base):
    if not git_commit_ok(root, base):
        return []
    paths = set()
    for args in (
        ["diff", "--name-only", "%s..HEAD" % base],
        ["diff", "--name-only", "--cached"],
        ["diff", "--name-only"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        proc = run_git(root, args)
        if proc.stdout:
            paths.update(path for path in proc.stdout.splitlines() if path)
    return sorted(paths)


def path_matches(changed, affected):
    if "*" in affected or "?" in affected:
        return fnmatch.fnmatchcase(changed, affected)
    return changed == affected or changed.startswith(affected + "/")


def stale_status(root, base, affected):
    if not git_commit_ok(root, base):
        return {"risk": "unknown", "changed_paths": [], "relevant_changed_paths": [], "reason": "baseline_missing"}
    changed = changed_paths(root, base)
    if changed and not affected:
        return {"risk": "unknown", "changed_paths": changed, "relevant_changed_paths": [], "reason": "affected_paths_unknown"}
    relevant = []
    for changed_path in changed:
        for affected_path in affected:
            if path_matches(changed_path, affected_path) and changed_path not in relevant:
                relevant.append(changed_path)
    risk = "needs_revalidation" if relevant else "current"
    return {
        "risk": risk,
        "changed_paths": changed,
        "relevant_changed_paths": relevant,
        "reason": "affected_paths_changed" if risk == "needs_revalidation" else "current",
    }


def items_path(run_dir):
    return os.path.join(run_dir, "ITEMS.jsonl")


def read_items(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def item_counts(rows):
    return {
        "total": len(rows),
        "pending": sum(1 for row in rows if row.get("status", "") == "pending"),
        "built": sum(1 for row in rows if row.get("status", "") == "built"),
        "accepted": sum(1 for row in rows if row.get("status", "") == "accepted"),
        "rejected": sum(1 for row in rows if row.get("status", "") == "rejected"),
        "dropped": sum(1 for row in rows if row.get("status", "") == "dropped"),
        "open": sum(1 for row in rows if row.get("status", "") in ("pending", "built", "rejected")),
    }


def load_active(root):
    path = active_file(root)
    if not os.path.isfile(path):
        return {"present": False, "status": "none"}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        if isinstance(value, dict):
            value = dict(value)
            value["present"] = True
            return value
    except (OSError, json.JSONDecodeError):
        pass
    return {"present": True, "status": "invalid", "path": ".kimiflow/session/ACTIVE_RUN.json"}


def normalized_host(value):
    return value if value in ("codex", "claude") else ""


def shell_session_identity():
    session_id = os.environ.get("KIMIFLOW_SESSION_ID", "") or os.environ.get("CODEX_THREAD_ID", "")
    host = normalized_host(os.environ.get("KIMIFLOW_SESSION_HOST", "") or os.environ.get("KIMIFLOW_HOST", ""))
    if not host and os.environ.get("CODEX_THREAD_ID"):
        host = "codex"
    if not (host and session_id):
        return None
    return {"host": host, "session_id": session_id}


def hook_session_identity(data):
    if not isinstance(data, dict) or not data.get("session_id"):
        return None
    host = normalized_host(os.environ.get("KIMIFLOW_HOST", ""))
    if not host and (os.environ.get("CODEX_THREAD_ID") or os.environ.get("PLUGIN_ROOT")):
        host = "codex"
    if not host:
        host = "claude"
    return {"host": host, "session_id": str(data["session_id"])}


def valid_owner(value):
    if not isinstance(value, dict):
        return None
    host = normalized_host(value.get("host", ""))
    session_id = value.get("session_id", "")
    if not (host and isinstance(session_id, str) and session_id):
        return None
    return {"host": host, "session_id": session_id}


def same_session(left, right):
    return bool(left and right and left.get("host") == right.get("host") and left.get("session_id") == right.get("session_id"))


def status_json(root, event=""):
    active = load_active(root)
    if not (active.get("present") is True and active.get("status") != "invalid"):
        return {
            "schema_version": 1,
            "present": active.get("present") is True,
            "status": active.get("status", "none"),
            "active_file": ".kimiflow/session/ACTIVE_RUN.json",
            "run": None,
            "item_counts": {"total": 0, "pending": 0, "built": 0, "accepted": 0, "rejected": 0, "dropped": 0, "open": 0},
            "stale_risk": "none",
            "stale": {"risk": "none", "changed_paths": [], "relevant_changed_paths": [], "reason": "no_active_session"},
            "awaiting_user": False,
            "terminal": True,
        }
    run_rel = active.get("run", "")
    run_dir = resolve_run_dir(root, run_rel)
    state = os.path.join(run_dir, "STATE.md")
    affected = run_affected_paths(run_dir)
    base = active.get("last_checked_head") or active.get("started_head") or "NOT VERIFIED"
    stale = stale_status(root, base, affected)
    items = items_path(run_dir)
    counts = item_counts(read_items(items))
    status = active.get("status", "active")
    phase_required = phase_reads.phase_reads_required(root, run_dir, active=active)
    legacy_next_action = flow_graph.legacy_action(stale, counts)
    transition = flow_graph.resolve_transition(
        os.path.dirname(state),
        active=active,
        stale=stale,
        item_counts=counts,
        event=event,
    )
    execution = None
    execution_error = ""
    active_selector = str(active.get("execution_contract", "")).strip()
    state_selector = execution_control.state_contract(run_dir)
    try:
        execution_trace_present = execution_control.trace_present(root, run_dir, active)
    except execution_control.ExecutionControlError as exc:
        execution_trace_present = True
        execution_error = str(exc)
    if active_selector or state_selector or execution_trace_present:
        if not execution_error and (active_selector != "1" or state_selector != "1"):
            execution_error = "execution_contract_selector_mismatch"
        elif not execution_error:
            try:
                execution = execution_control.inspect(root, run_dir, active)
                if execution["summary"]["strategy_mode"] == "recovery" and not event:
                    transition = flow_graph.resolve_transition(
                        run_dir,
                        active=active,
                        stale=stale,
                        item_counts=counts,
                        event="no_progress",
                    )
                transition = execution_control.annotate_transition(transition, execution)
            except execution_control.ExecutionControlError as exc:
                execution_error = str(exc)
        if execution_error:
            transition = dict(transition)
            transition.update(
                {
                    "graph_status": "invalid_execution_control",
                    "action": "repair_execution_control",
                    "target_node": transition.get("current_node"),
                    "target_phase": transition.get("current_phase"),
                    "target_file": transition.get("current_file"),
                    "reason": execution_error,
                }
            )
    result = {
        "schema_version": 1,
        "present": True,
        "status": status,
        "active_file": ".kimiflow/session/ACTIVE_RUN.json",
        "run": run_rel,
        "state_path": rel_path(root, state),
        "items_path": rel_path(root, items),
        "started_at": active.get("started_at"),
        "started_head": active.get("started_head", "NOT VERIFIED"),
        "last_checked_head": active.get("last_checked_head") or active.get("started_head") or "NOT VERIFIED",
        "host": active.get("host", "unknown"),
        "mode": active.get("mode", ""),
        "scope": active.get("scope", ""),
        "affected_files": affected,
        "item_counts": counts,
        "stale_risk": stale.get("risk", "unknown"),
        "stale": stale,
        "awaiting_user": active.get("awaiting_user") is True,
        "awaiting_kind": active.get("awaiting_kind") if active.get("awaiting_user") is True else None,
        "terminal": status in ("done", "parked", "failed", "aborted"),
        "next_action": legacy_next_action,
    }
    if transition.get("graph_status") != "legacy":
        result["transition"] = transition
    if execution is not None:
        result["execution_control"] = {
            "contract": 1,
            "trace_path": rel_path(root, execution_control.trace_path(run_dir)),
            **transition["execution"],
        }
    elif execution_error:
        result["execution_control"] = {"contract": 1, "status": "invalid", "reason": execution_error}
    if phase_required:
        result["phase_reads_required"] = True
    for key in ("workspace_wait_used_at", "workspace_disposition_head", "frontend_quality_start_head"):
        if active.get(key):
            result[key] = active[key]
    owner = valid_owner(active.get("owner"))
    if owner:
        result["owner"] = owner
    return result


def write_active(root, value):
    path = active_file(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, json_pretty(value) + "\n", mode=0o600, refuse_symlink=True)


def bind_owner_for_write(root, write):
    if not write:
        return
    identity = shell_session_identity()
    if not identity:
        return
    active = load_active(root)
    owner = valid_owner(active.get("owner"))
    if owner and not same_session(owner, identity):
        die("active Kimiflow session is owned by another %s session" % owner["host"], 1)
    if not owner and active.get("present") is True and active.get("status") != "invalid":
        updated = dict(active)
        updated.pop("present", None)
        updated["owner"] = identity
        updated["updated_at"] = iso_now()
        write_active(root, updated)


def rewrite_items(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = "".join(json_compact(row) + "\n" for row in rows)
    atomic_write(path, payload, mode=0o600, refuse_symlink=True)


def next_item_id(path):
    max_id = 0
    for row in read_items(path):
        ident = row.get("id", "")
        match = re.match(r"^item_([0-9]+)$", ident)
        if match:
            max_id = max(max_id, int(match.group(1)))
    return "item_%03d" % (max_id + 1)


def state_rewrite_payload(source, replacements):
    out = []
    done = [False] * len(replacements)
    for raw in source.splitlines():
        line = raw
        for index, (update_line, _) in enumerate(replacements):
            replacement = update_line(raw)
            if replacement is not None:
                line = replacement
                done[index] = True
                break
        out.append(line)
    for index, (_, append_line) in enumerate(replacements):
        if not done[index]:
            out.append(append_line)
    return ("\n".join(out) + "\n").encode("utf-8")


def restore_terminal_state_backup(run_descriptor, backup_name):
    try:
        os.link(
            backup_name,
            "STATE.md",
            src_dir_fd=run_descriptor,
            dst_dir_fd=run_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        return False
    try:
        os.unlink(backup_name, dir_fd=run_descriptor)
        os.fsync(run_descriptor)
    except OSError:
        return False
    return True


def rewrite_state_descriptor(pinned, replacements):
    if fcntl is None:
        die("terminal STATE updates require POSIX file locking", 2)
    descriptor = pinned["state_descriptor"]
    run_descriptor = pinned["run_descriptor"]
    token = secrets.token_hex(16)
    temporary = ".terminal-state-%s.tmp" % token
    backup = ".terminal-state-%s.backup" % token
    backup_created = False
    replacement_identity = None
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        payload = state_rewrite_payload(b"".join(chunks).decode("utf-8"), replacements)
        workspace_preflight.atomic_directory_write(run_descriptor, temporary, payload)
        os.rename("STATE.md", backup, src_dir_fd=run_descriptor, dst_dir_fd=run_descriptor)
        backup_created = True
        moved = os.stat(backup, dir_fd=run_descriptor, follow_symlinks=False)
        expected = pinned["state_identity"]
        if (moved.st_dev, moved.st_ino) != expected:
            try:
                os.unlink(temporary, dir_fd=run_descriptor)
            except OSError:
                pass
            restored = restore_terminal_state_backup(run_descriptor, backup)
            backup_created = not restored
            raise OSError("terminal STATE identity changed")
        temporary_info = os.stat(temporary, dir_fd=run_descriptor, follow_symlinks=False)
        replacement_identity = (temporary_info.st_dev, temporary_info.st_ino)
        os.link(
            temporary,
            "STATE.md",
            src_dir_fd=run_descriptor,
            dst_dir_fd=run_descriptor,
            follow_symlinks=False,
        )
        installed = os.stat("STATE.md", dir_fd=run_descriptor, follow_symlinks=False)
        if (installed.st_dev, installed.st_ino) != replacement_identity:
            raise OSError("terminal STATE installation changed")
        os.unlink(temporary, dir_fd=run_descriptor)
        # Commit the replacement while the old STATE is still available for
        # compensation. A failure here can therefore restore the old bytes.
        os.fsync(run_descriptor)
        pinned["state_identity"] = (installed.st_dev, installed.st_ino)
        try:
            os.unlink(backup, dir_fd=run_descriptor)
            backup_created = False
            try:
                os.fsync(run_descriptor)
            except OSError:
                # The public STATE replacement was already durable. A late
                # cleanup fsync may resurrect only the private backup name,
                # never roll the transaction back or leave it half-reported.
                pass
        except OSError:
            # Keep the private backup rather than report a failed transaction
            # after the new public STATE has already been committed durably.
            pass
    except (OSError, UnicodeError) as exc:
        try:
            os.unlink(temporary, dir_fd=run_descriptor)
        except OSError:
            pass
        if backup_created:
            try:
                current = os.stat("STATE.md", dir_fd=run_descriptor, follow_symlinks=False)
                if replacement_identity is not None and (current.st_dev, current.st_ino) == replacement_identity:
                    os.unlink("STATE.md", dir_fd=run_descriptor)
            except OSError:
                pass
            restore_terminal_state_backup(run_descriptor, backup)
        die("cannot update terminal STATE: %s" % exc, 2)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


def update_state_status(run_dir, status):
    def replacement(line):
        plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
        return "Status: %s" % status if re.match(r"^Status:[ \t]*", plain) else None

    state = os.path.join(run_dir, "STATE.md")
    if not os.path.isfile(state):
        return
    out = []
    done = False
    with open(state, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            changed = replacement(line)
            if changed is not None:
                out.append(changed)
                done = True
            else:
                out.append(line)
    if not done:
        out.append("Status: %s" % status)
    atomic_write(state, "\n".join(out) + "\n", mode=0o600, refuse_symlink=True)


def update_terminal_state(pinned, status, phase7_done=False):
    def status_replacement(line):
        plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
        return "Status: %s" % status if re.match(r"^Status:[ \t]*", plain) else None

    replacements = [(status_replacement, "Status: %s" % status)]
    if phase7_done:
        def phase_replacement(line):
            plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
            return "Phase 7: done" if re.match(r"^Phase[ \t]+7:[ \t]*", plain) else None

        replacements.append((phase_replacement, "Phase 7: done"))
    rewrite_state_descriptor(pinned, replacements)


def ensure_terminal_run_path(run_dir, active):
    parent = os.path.dirname(run_dir)
    state_path = os.path.join(run_dir, "STATE.md")
    for path, expected in ((parent, "directory"), (run_dir, "directory"), (state_path, "file")):
        try:
            info = os.lstat(path)
        except OSError as exc:
            die("unsafe terminal run path: %s" % exc, 2)
        valid = stat.S_ISDIR(info.st_mode) if expected == "directory" else stat.S_ISREG(info.st_mode)
        if stat.S_ISLNK(info.st_mode) or not valid:
            die("unsafe terminal run path: %s" % path, 2)
    expected_device = active.get("run_device")
    expected_inode = active.get("run_inode")
    run_info = os.lstat(run_dir)
    if expected_device is not None and expected_inode is not None and (
        run_info.st_dev,
        run_info.st_ino,
    ) != (expected_device, expected_inode):
        die("terminal run directory identity changed", 2)
    outcome_path = os.path.join(run_dir, "SESSION-OUTCOME.json")
    if os.path.lexists(outcome_path):
        outcome_info = os.lstat(outcome_path)
        if stat.S_ISLNK(outcome_info.st_mode) or not stat.S_ISREG(outcome_info.st_mode):
            die("unsafe terminal outcome path", 2)


@contextlib.contextmanager
def pinned_terminal_run(run_dir, active):
    parent = os.path.dirname(run_dir)
    run_name = os.path.basename(run_dir)
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    state_flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        state_flags |= os.O_NOFOLLOW
    parent_descriptor = None
    run_descriptor = None
    state_descriptor = None
    try:
        parent_descriptor = os.open(parent, directory_flags)
        run_descriptor = os.open(run_name, directory_flags, dir_fd=parent_descriptor)
        run_info = os.fstat(run_descriptor)
        named_run = os.stat(run_name, dir_fd=parent_descriptor, follow_symlinks=False)
        expected = (active.get("run_device"), active.get("run_inode"))
        if not stat.S_ISDIR(run_info.st_mode) or (run_info.st_dev, run_info.st_ino) != (
            named_run.st_dev,
            named_run.st_ino,
        ):
            die("unsafe terminal run directory", 2)
        if None not in expected and (run_info.st_dev, run_info.st_ino) != expected:
            die("terminal run directory identity changed", 2)
        state_descriptor = os.open("STATE.md", state_flags, dir_fd=run_descriptor)
        state_info = os.fstat(state_descriptor)
        named_state = os.stat("STATE.md", dir_fd=run_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(state_info.st_mode) or (state_info.st_dev, state_info.st_ino) != (
            named_state.st_dev,
            named_state.st_ino,
        ):
            die("unsafe terminal STATE", 2)
        try:
            outcome = os.stat("SESSION-OUTCOME.json", dir_fd=run_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(outcome.st_mode) or not stat.S_ISREG(outcome.st_mode):
                die("unsafe terminal outcome path", 2)
        yield {
            "parent_descriptor": parent_descriptor,
            "run_descriptor": run_descriptor,
            "state_descriptor": state_descriptor,
            "run_name": run_name,
            "run_identity": (run_info.st_dev, run_info.st_ino),
            "state_identity": (state_info.st_dev, state_info.st_ino),
        }
    except OSError as exc:
        die("cannot pin terminal run: %s" % exc, 2)
    finally:
        if state_descriptor is not None:
            os.close(state_descriptor)
        if run_descriptor is not None:
            os.close(run_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def terminal_run_name_matches(pinned):
    try:
        current = os.stat(
            pinned["run_name"],
            dir_fd=pinned["parent_descriptor"],
            follow_symlinks=False,
        )
        state = os.stat(
            "STATE.md",
            dir_fd=pinned["run_descriptor"],
            follow_symlinks=False,
        )
    except OSError:
        return False
    return (current.st_dev, current.st_ino) == pinned["run_identity"] and (
        state.st_dev,
        state.st_ino,
    ) == pinned["state_identity"]


def restore_retired_active(active_session_descriptor, retired_active):
    try:
        os.link(
            retired_active,
            "ACTIVE_RUN.json",
            src_dir_fd=active_session_descriptor,
            dst_dir_fd=active_session_descriptor,
            follow_symlinks=False,
        )
        os.fsync(active_session_descriptor)
        os.unlink(retired_active, dir_fd=active_session_descriptor)
        os.fsync(active_session_descriptor)
    except OSError:
        return False
    return True


def retire_active_session(root, pinned, expected_status, expected_run):
    if not terminal_run_name_matches(pinned):
        die("terminal run or STATE changed before active-session retirement", 2)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    active_base_descriptor = None
    active_session_descriptor = None
    active_descriptor = None
    try:
        descriptor = os.open("STATE.md", flags, dir_fd=pinned["run_descriptor"])
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != pinned["state_identity"]:
            die("terminal STATE identity changed before active-session retirement", 2)
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
        payload = os.read(descriptor, 1048577)
        if len(payload) > 1048576:
            die("terminal STATE is too large", 2)
        source = payload.decode("utf-8")
        status_value = ""
        for raw in source.splitlines():
            plain = re.sub(r"^[ \t]*-[ \t]*", "", raw.replace("**", ""))
            label, sep, value = plain.partition(":")
            if sep and label.strip().lower() == "status":
                status_value = value.strip().lower()
                break
        final_info = os.fstat(descriptor)
        named = os.stat("STATE.md", dir_fd=pinned["run_descriptor"], follow_symlinks=False)
        initial_content_identity = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        final_content_identity = (final_info.st_size, final_info.st_mtime_ns, final_info.st_ctime_ns)
        if final_content_identity != initial_content_identity or (
            final_info.st_dev,
            final_info.st_ino,
        ) != pinned["state_identity"] or (
            named.st_dev,
            named.st_ino,
        ) != pinned["state_identity"] or status_value != expected_status:
            die("terminal STATE changed before active-session retirement", 2)
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        active_base_path = os.path.join(root, ".kimiflow")
        active_base_descriptor = os.open(active_base_path, directory_flags)
        active_session_descriptor = os.open("session", directory_flags, dir_fd=active_base_descriptor)
        active_descriptor = os.open("ACTIVE_RUN.json", flags, dir_fd=active_session_descriptor)
        base_info = os.fstat(active_base_descriptor)
        session_info = os.fstat(active_session_descriptor)
        active_info = os.fstat(active_descriptor)
        if not stat.S_ISDIR(base_info.st_mode) or not stat.S_ISDIR(session_info.st_mode) or not stat.S_ISREG(
            active_info.st_mode
        ):
            die("unsafe active-session retirement path", 2)
        active_payload = os.read(active_descriptor, 1048577)
        if len(active_payload) > 1048576:
            die("active-session state is too large", 2)
        active_value = json.loads(active_payload.decode("utf-8"))
        if not isinstance(active_value, dict) or active_value.get("run") != expected_run:
            die("active-session identity changed before retirement", 2)
        named_base = os.lstat(active_base_path)
        named_session = os.stat("session", dir_fd=active_base_descriptor, follow_symlinks=False)
        named_active = os.stat("ACTIVE_RUN.json", dir_fd=active_session_descriptor, follow_symlinks=False)
        if (named_base.st_dev, named_base.st_ino) != (base_info.st_dev, base_info.st_ino) or (
            named_session.st_dev,
            named_session.st_ino,
        ) != (session_info.st_dev, session_info.st_ino) or (named_active.st_dev, named_active.st_ino) != (
            active_info.st_dev,
            active_info.st_ino,
        ):
            die("active-session path changed before retirement", 2)
        retired_active = ".kimiflow-retired-ACTIVE_RUN.json-%s" % secrets.token_hex(8)
        active_moved = False
        try:
            os.rename(
                "ACTIVE_RUN.json",
                retired_active,
                src_dir_fd=active_session_descriptor,
                dst_dir_fd=active_session_descriptor,
            )
            active_moved = True
            retired_info = os.stat(retired_active, dir_fd=active_session_descriptor, follow_symlinks=False)
            if (retired_info.st_dev, retired_info.st_ino) != (active_info.st_dev, active_info.st_ino):
                if restore_retired_active(active_session_descriptor, retired_active):
                    active_moved = False
                die("active-session identity changed during retirement", 2)
            try:
                os.stat("ACTIVE_RUN.json", dir_fd=active_session_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                os.unlink(retired_active, dir_fd=active_session_descriptor)
                active_moved = False
                die("active-session identity changed during retirement", 2)
            final_base = os.lstat(active_base_path)
            final_session = os.stat("session", dir_fd=active_base_descriptor, follow_symlinks=False)
            if (final_base.st_dev, final_base.st_ino) != (base_info.st_dev, base_info.st_ino) or (
                final_session.st_dev,
                final_session.st_ino,
            ) != (session_info.st_dev, session_info.st_ino):
                raise OSError("active-session path changed during retirement")
            # Persist the rename before deleting the private tombstone. From
            # this point a crash cannot resurrect the public ACTIVE_RUN name.
            os.fsync(active_session_descriptor)
            try:
                os.unlink(retired_active, dir_fd=active_session_descriptor)
                active_moved = False
                try:
                    os.fsync(active_session_descriptor)
                except OSError:
                    # Public retirement was committed by the preceding fsync;
                    # this can affect only tombstone cleanup durability.
                    pass
            except OSError:
                if restore_retired_active(active_session_descriptor, retired_active):
                    active_moved = False
                raise
        except OSError:
            if active_moved:
                if restore_retired_active(active_session_descriptor, retired_active):
                    active_moved = False
            raise
    except (OSError, UnicodeError) as exc:
        die("cannot retire active session: %s" % exc, 2)
    except (ValueError, json.JSONDecodeError) as exc:
        die("cannot parse active session during retirement: %s" % exc, 2)
    finally:
        if active_descriptor is not None:
            os.close(active_descriptor)
        if active_session_descriptor is not None:
            os.close(active_session_descriptor)
        if active_base_descriptor is not None:
            os.close(active_base_descriptor)
        if descriptor is not None:
            if fcntl is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)


def update_state_value(run_dir, label, value):
    parent = os.path.dirname(run_dir)
    run_name = os.path.basename(run_dir)
    if fcntl is None:
        die("durable STATE receipts require POSIX file locking", 2)
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    state_flags = os.O_RDWR | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        state_flags |= os.O_NOFOLLOW
    parent_descriptor = None
    run_descriptor = None
    state_descriptor = None
    try:
        parent_descriptor = os.open(parent, directory_flags)
        run_descriptor = os.open(run_name, directory_flags, dir_fd=parent_descriptor)
        pinned_run = os.fstat(run_descriptor)
        named_run = os.stat(run_name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(pinned_run.st_mode) or (pinned_run.st_dev, pinned_run.st_ino) != (
            named_run.st_dev,
            named_run.st_ino,
        ):
            die("unsafe run path for durable %s receipt" % label, 2)
        state_descriptor = os.open("STATE.md", state_flags, dir_fd=run_descriptor)
        pinned_state = os.fstat(state_descriptor)
        if not stat.S_ISREG(pinned_state.st_mode):
            die("unsafe STATE.md for durable %s receipt" % label, 2)
        fcntl.flock(state_descriptor, fcntl.LOCK_EX)
        os.lseek(state_descriptor, 0, os.SEEK_SET)
        chunks = []
        while True:
            chunk = os.read(state_descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        source_bytes = b"".join(chunks)
        source = source_bytes.decode("utf-8")
        for line in source.splitlines():
            plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
            current, sep, _ = plain.partition(":")
            if sep and current.strip().lower() == label.lower():
                die("durable %s receipt already exists" % label, 2)
        current_run = os.stat(run_name, dir_fd=parent_descriptor, follow_symlinks=False)
        current_state = os.stat("STATE.md", dir_fd=run_descriptor, follow_symlinks=False)
        if (current_run.st_dev, current_run.st_ino) != (pinned_run.st_dev, pinned_run.st_ino):
            die("run path changed during durable %s receipt" % label, 2)
        if (current_state.st_dev, current_state.st_ino) != (pinned_state.st_dev, pinned_state.st_ino):
            die("STATE.md changed during durable %s receipt" % label, 2)
        prefix = b"" if not source_bytes else b"\n"
        payload = prefix + ("%s: %s\n" % (label, value)).encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(state_descriptor, view)
            if written <= 0:
                raise OSError("short STATE write")
            view = view[written:]
        os.fsync(state_descriptor)
        # Once the pinned append is durable the decision is consumed. A later
        # pathname exchange cannot redirect these bytes, and must not turn a
        # successful one-shot receipt into a false failure that cannot retry.
    except (OSError, UnicodeError, ValueError) as exc:
        die("cannot persist durable %s receipt: %s" % (label, exc), 2)
    finally:
        if state_descriptor is not None:
            try:
                fcntl.flock(state_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(state_descriptor)
        if run_descriptor is not None:
            os.close(run_descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def ensure_state_phase_reads_required(run_dir):
    state = os.path.join(run_dir, "STATE.md")
    out = []
    done = False
    if os.path.isfile(state):
        with open(state, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
                if re.match(r"^Phase reads required:[ \t]*", plain, flags=re.IGNORECASE):
                    out.append("Phase reads required: yes")
                    done = True
                else:
                    out.append(line)
    if not done:
        out.append("Phase reads required: yes")
    atomic_write(state, "\n".join(out) + "\n", mode=0o600, refuse_symlink=False)


def update_state_phase7_done(run_dir):
    def replacement(line):
        plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
        return "Phase 7: done" if re.match(r"^Phase[ \t]+7:[ \t]*", plain) else None

    state = os.path.join(run_dir, "STATE.md")
    if not os.path.isfile(state):
        return
    out = []
    done = False
    with open(state, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            changed = replacement(line)
            if changed is not None:
                out.append(changed)
                done = True
            else:
                out.append(line)
    if not done:
        out.append("Phase 7: done")
    atomic_write(state, "\n".join(out) + "\n", mode=0o600, refuse_symlink=False)


def ensure_state_run_started_head(run_dir, started_head):
    state_path = os.path.join(run_dir, "STATE.md")
    if not os.path.isfile(state_path):
        return
    out = []
    existing = ""
    with open(state_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
            label, sep, value = plain.partition(":")
            if sep and label.strip().lower() == "run started head":
                existing = value.strip()
            out.append(line)
    if existing:
        return
    out.append("Run started head: %s" % started_head)
    atomic_write(state_path, "\n".join(out) + "\n", mode=0o600, refuse_symlink=True)


def require_active(root):
    status = status_json(root)
    if not (status.get("present") is True and status.get("terminal") is False):
        die("no active Kimiflow session", 1)
    return status


def parse_options(args, command, specs):
    out = {key: default for key, default in specs.items()}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            usage()
            raise SystemExit(0)
        if arg not in specs:
            die("%s: unknown argument: %s" % (command, arg), 2)
        default = specs[arg]
        if isinstance(default, bool):
            out[arg] = True
            i += 1
        else:
            out[arg] = args[i + 1] if i + 1 < len(args) else ""
            i += 2
    return out


def cmd_status(args):
    opts = parse_options(args, "status", {"--root": "", "--pretty": False})
    need_jq()
    root = resolve_root(opts["--root"], strict=False)
    json_print(status_json(root), opts["--pretty"])


def cmd_next_action(args):
    opts = parse_options(args, "next-action", {"--root": "", "--event": "", "--pretty": False})
    need_jq()
    root = resolve_root(opts["--root"], strict=False)
    status = status_json(root, event=opts["--event"])
    if status.get("present") is True:
        transition = status.get("transition")
        if not isinstance(transition, dict):
            run_dir = resolve_run_dir(root, status["run"])
            transition = flow_graph.resolve_transition(
                run_dir,
                active=load_active(root),
                stale=status.get("stale"),
                item_counts=status.get("item_counts"),
                event=opts["--event"],
            )
    else:
        transition = {
            "schema_version": 1,
            "graph_status": "no_active_run",
            "graph_schema_version": None,
            "event": opts["--event"] or "resume",
            "current_node": None,
            "action": "none",
            "target_node": None,
            "reason": "no_active_run",
            "current_phase": None,
            "current_file": None,
            "target_phase": None,
            "target_file": None,
        }
    json_print(transition, opts["--pretty"])


def _usage_value(value, label):
    try:
        parsed = int(value or "0", 10)
    except (TypeError, ValueError):
        die("observe: %s must be a non-negative integer" % label, 2)
    if parsed < 0:
        die("observe: %s must be a non-negative integer" % label, 2)
    return parsed


def _observe_execution(root, status, event, outcome, evidence, usage, write, coalesce_pending_stop=False):
    active = load_active(root)
    run_dir = resolve_run_dir(root, status["run"])
    if not execution_control.contract_enabled(active):
        die("observe requires Execution contract: 1", 1)
    stale = status.get("stale") or {"risk": "unknown"}
    counts = status.get("item_counts") or {}
    normal = flow_graph.resolve_transition(
        run_dir,
        active=active,
        stale=stale,
        item_counts=counts,
    )
    recovery = flow_graph.resolve_transition(
        run_dir,
        active=active,
        stale=stale,
        item_counts=counts,
        event="no_progress",
    )
    try:
        return execution_control.observe(
            root,
            run_dir,
            active,
            counts,
            normal,
            recovery,
            event=event,
            outcome=outcome,
            evidence=evidence,
            coalesce_pending_stop=coalesce_pending_stop,
            write=write,
            **usage
        )
    except execution_control.ExecutionControlError as exc:
        die("execution control: %s" % exc, 2)


def cmd_observe(args):
    opts = parse_options(
        args,
        "observe",
        {
            "--root": "",
            "--event": "observation",
            "--outcome": "neutral",
            "--evidence": "",
            "--model-calls": "0",
            "--tool-calls": "0",
            "--input-tokens": "0",
            "--output-tokens": "0",
            "--write": False,
            "--pretty": False,
        },
    )
    need_jq()
    root = resolve_root(opts["--root"], strict=opts["--write"])
    bind_owner_for_write(root, opts["--write"])
    status = require_active(root)
    usage = {
        "model_calls": _usage_value(opts["--model-calls"], "--model-calls"),
        "tool_calls": _usage_value(opts["--tool-calls"], "--tool-calls"),
        "input_tokens": _usage_value(opts["--input-tokens"], "--input-tokens"),
        "output_tokens": _usage_value(opts["--output-tokens"], "--output-tokens"),
    }
    result = _observe_execution(
        root,
        status,
        opts["--event"],
        opts["--outcome"],
        opts["--evidence"] or None,
        usage,
        opts["--write"],
    )
    json_print(
        {
            "status": "observed" if opts["--write"] else "preview",
            "written": opts["--write"] is True,
            "run": status["run"],
            "execution": result["summary"],
            "transition": result["transition"],
        },
        opts["--pretty"],
    )


def cmd_start(args):
    opts = parse_options(args, "start", {"--root": "", "--run": "", "--mode": "feature", "--scope": "small", "--host": os.environ.get("KIMIFLOW_HOST", "unknown"), "--write": False, "--pretty": False})
    need_jq()
    write = opts["--write"]
    root = resolve_root(opts["--root"], strict=write)
    run_dir = resolve_run_dir(root, opts["--run"])
    run_rel = rel_path(root, run_dir)
    existing = status_json(root)
    if existing.get("present") is True and existing.get("terminal") is False and existing.get("run") != run_rel:
        die("another active Kimiflow session exists: %s" % existing.get("run"), 1)
    identity = shell_session_identity()
    existing_owner = valid_owner(existing.get("owner"))
    if existing_owner and identity and not same_session(existing_owner, identity):
        die("active Kimiflow session is owned by another %s session" % existing_owner["host"], 1)
    phase_required = phase_reads.manifest_exists(root)
    same_active = existing.get("present") is True and existing.get("run") == run_rel
    prior_active = load_active(root) if same_active else {}
    resume_pins = parked_session_pins(run_dir) if not same_active else {}
    state_path = os.path.join(run_dir, "STATE.md")
    persisted_started_head = state.state_value(state_path, "Run started head").strip()
    existing_started_head = str(existing.get("started_head", "")).strip()
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40,64}|NOT VERIFIED)", persisted_started_head):
        persisted_started_head = ""
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40,64}|NOT VERIFIED)", existing_started_head):
        existing_started_head = ""
    current_head = git_head(root)
    # ACTIVE_RUN is authoritative during a restart; a parked outcome preserves
    # the same immutable selectors while no active-session file exists.
    started_head = existing_started_head or str(resume_pins.get("started_head") or "") or persisted_started_head or current_head
    active_mode = prior_active.get("mode") if same_active else resume_pins.get("mode", opts["--mode"])
    active_scope = prior_active.get("scope") if same_active else resume_pins.get("scope", opts["--scope"])
    active_host = prior_active.get("host") if same_active else resume_pins.get("host", opts["--host"])
    active_started_at = prior_active.get("started_at") if same_active else resume_pins.get("started_at", iso_now())
    status = {
        "schema_version": 1,
        "status": "active",
        "run": run_rel,
        "mode": active_mode,
        "scope": active_scope,
        "host": active_host,
        "started_at": active_started_at,
        "updated_at": iso_now(),
        "started_head": started_head,
        "last_checked_head": current_head,
        "affected_files_at_start": run_affected_paths(run_dir),
    }
    persisted_conformance = state.state_value(state_path, "Conformance contract").strip()
    if same_active and "conformance_contract" in prior_active:
        status["conformance_contract"] = prior_active.get("conformance_contract")
    elif "conformance_contract" in resume_pins:
        status["conformance_contract"] = resume_pins.get("conformance_contract")
    else:
        status["conformance_contract"] = persisted_conformance or None
    if same_active and "frontend_quality_contract" in prior_active:
        status["frontend_quality_contract"] = prior_active.get("frontend_quality_contract")
    elif "frontend_quality_contract" in resume_pins:
        status["frontend_quality_contract"] = resume_pins.get("frontend_quality_contract")
    if identity:
        status["owner"] = identity
    if phase_required:
        status["phase_reads_required"] = True
    flow_schema = state.state_value(os.path.join(run_dir, "STATE.md"), "Flow schema").strip().split(" ", 1)[0]
    if same_active and str(prior_active.get("flow_schema", "")).isdigit():
        flow_schema = str(prior_active["flow_schema"])
    elif str(resume_pins.get("flow_schema", "")).isdigit():
        flow_schema = str(resume_pins["flow_schema"])
    if flow_schema.isdigit():
        status["flow_schema"] = flow_schema
    state_execution_contract = execution_control.state_contract(run_dir)
    execution_was_pinned = same_active or bool(resume_pins)
    raw_execution_contract = (
        prior_active.get("execution_contract", "") if same_active else resume_pins.get("execution_contract", "")
    )
    pinned_execution_contract = "" if raw_execution_contract is None else str(raw_execution_contract).strip()
    if execution_was_pinned and pinned_execution_contract != state_execution_contract:
        die("execution contract selector changed during the run", 1)
    selected_execution_contract = pinned_execution_contract if execution_was_pinned else state_execution_contract
    if selected_execution_contract:
        if selected_execution_contract != "1":
            die("unsupported execution contract: %s" % selected_execution_contract, 1)
        status["execution_contract"] = "1"
    existing_execution_trace = False
    if os.path.isdir(run_dir) and not os.path.islink(run_dir):
        run_info = os.lstat(run_dir)
        status["run_device"] = run_info.st_dev
        status["run_inode"] = run_info.st_ino
        try:
            existing_execution_trace = execution_control.trace_present(root, run_dir, status)
        except execution_control.ExecutionControlError as exc:
            die("cannot inspect execution control: %s" % exc, 2)
    if existing_execution_trace and selected_execution_contract != "1":
        die("execution contract selector missing for existing controller evidence", 1)
    if execution_was_pinned and selected_execution_contract == "1" and not existing_execution_trace:
        die("execution controller evidence is missing for the pinned run", 1)
    workspace_wait = str(prior_active.get("workspace_wait_used_at", "")).strip() if same_active else state.state_value(
        state_path, "Workspace decision used at"
    ).strip()
    if workspace_wait:
        status["workspace_wait_used_at"] = workspace_wait
    disposition_head = str(prior_active.get("workspace_disposition_head", "")).strip() if same_active else state.state_value(
        state_path, "Workspace disposition head"
    ).strip()
    if git_commit_ok(root, disposition_head):
        status["workspace_disposition_head"] = disposition_head
    frontend_start_head = str(prior_active.get("frontend_quality_start_head", "")).strip() if same_active else str(
        resume_pins.get("frontend_quality_start_head", "")
    ).strip()
    if not same_active and not frontend_start_head:
        persisted_frontend_start = state.state_value(state_path, "Frontend quality start").strip()
        frontend_match = re.fullmatch(r"clean@([0-9a-fA-F]{40,64})", persisted_frontend_start)
        if frontend_match:
            frontend_start_head = frontend_match.group(1)
    if git_commit_ok(root, frontend_start_head):
        status["frontend_quality_start_head"] = frontend_start_head
    if write:
        os.makedirs(run_dir, exist_ok=True)
        if phase_required:
            ensure_state_phase_reads_required(run_dir)
        ensure_state_run_started_head(run_dir, started_head)
        if selected_execution_contract == "1":
            counts = item_counts(read_items(items_path(run_dir)))
            transition = flow_graph.resolve_transition(
                run_dir,
                active=status,
                stale=stale_status(root, started_head, run_affected_paths(run_dir)),
                item_counts=counts,
            )
            try:
                execution_control.initialize(root, run_dir, status, counts, transition, write=True)
            except execution_control.ExecutionControlError as exc:
                die("cannot initialize execution control: %s" % exc, 2)
        write_active(root, status)
    json_print(status_json(root), opts["--pretty"])


def cmd_append_item(args):
    opts = parse_options(args, "append-item", {"--root": "", "--title": "", "--kind": "change", "--write": False, "--pretty": False})
    need_jq()
    if not opts["--title"]:
        die("append-item requires --title", 2)
    root = resolve_root(opts["--root"], strict=opts["--write"])
    bind_owner_for_write(root, opts["--write"])
    status = require_active(root)
    run_dir = resolve_run_dir(root, status["run"])
    file_path = items_path(run_dir)
    ident = next_item_id(file_path)
    now = iso_now()
    row = {"id": ident, "kind": opts["--kind"], "title": opts["--title"], "status": "pending", "created_at": now, "updated_at": now}
    if opts["--write"]:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "a", encoding="utf-8") as handle:
            handle.write(json_compact(row) + "\n")
    rows = read_items(file_path)
    out = {"status": "item_appended", "written": opts["--write"] is True, "items_path": rel_path(root, file_path), "item": row, "item_counts": {"total": len(rows), "open": item_counts(rows)["open"]}}
    json_print(out, opts["--pretty"])


def cmd_update_item(command, args):
    mapping = {"mark-built": "built", "mark-accepted": "accepted", "mark-rejected": "rejected", "drop-item": "dropped"}
    new_status = mapping.get(command)
    if not new_status:
        die("unknown item command: %s" % command, 2)
    opts = parse_options(args, command, {"--root": "", "--id": "", "--reason": "", "--write": False, "--pretty": False})
    need_jq()
    if not opts["--id"]:
        die("%s requires --id" % command, 2)
    if new_status in ("rejected", "dropped") and not opts["--reason"]:
        die("%s requires --reason" % command, 2)
    root = resolve_root(opts["--root"], strict=opts["--write"])
    bind_owner_for_write(root, opts["--write"])
    status = require_active(root)
    run_dir = resolve_run_dir(root, status["run"])
    file_path = items_path(run_dir)
    rows = read_items(file_path)
    if not any(row.get("id") == opts["--id"] for row in rows):
        die("item not found: %s" % opts["--id"], 1)
    updated = []
    now = iso_now()
    for row in rows:
        row = dict(row)
        if row.get("id") == opts["--id"]:
            row["status"] = new_status
            row["updated_at"] = now
            if opts["--reason"]:
                row["reason"] = opts["--reason"]
        updated.append(row)
    if opts["--write"]:
        rewrite_items(file_path, updated)
    counts = item_counts(updated)
    out = {"status": "item_updated", "written": opts["--write"] is True, "items_path": rel_path(root, file_path), "id": opts["--id"], "item_status": new_status, "item_counts": counts}
    json_print(out, opts["--pretty"])


def cmd_refresh_baseline(args):
    opts = parse_options(
        args,
        "refresh-baseline",
        {"--root": "", "--workspace-disposition": False, "--write": False, "--pretty": False},
    )
    need_jq()
    root = resolve_root(opts["--root"], strict=opts["--write"])
    bind_owner_for_write(root, opts["--write"])
    status = require_active(root)
    active = load_active(root)
    run_dir = resolve_run_dir(root, status["run"])
    refreshed = dict(active)
    refreshed["updated_at"] = iso_now()
    current_head = git_head(root)
    refreshed["last_checked_head"] = current_head
    refreshed["affected_files_at_last_check"] = run_affected_paths(run_dir)
    if opts["--workspace-disposition"]:
        flow_schema = str(
            active.get("flow_schema")
            or state.state_value(os.path.join(run_dir, "STATE.md"), "Flow schema")
        ).strip()
        if not flow_schema.isdigit() or int(flow_schema) < 4:
            die("workspace disposition receipt requires flow schema 4", 1)
        if not active.get("workspace_wait_used_at"):
            die("workspace disposition receipt requires the recorded workspace decision", 1)
        if changed_paths(root, current_head):
            die("workspace disposition receipt requires a clean worktree", 1)
        recorded_disposition = str(
            active.get("workspace_disposition_head")
            or state.state_value(os.path.join(run_dir, "STATE.md"), "Workspace disposition head")
        ).strip()
        if recorded_disposition and recorded_disposition != current_head:
            die("workspace disposition receipt is already bound to another head", 1)
        refreshed["workspace_disposition_head"] = current_head
    if opts["--write"]:
        write_active(root, refreshed)
        if opts["--workspace-disposition"]:
            try:
                update_state_value(run_dir, "Workspace disposition head", current_head)
            except ActiveError:
                write_active(root, active)
                raise
    json_print(status_json(root), opts["--pretty"])


def cmd_await_user(args):
    opts = parse_options(args, "await-user", {"--root": "", "--run": "", "--kind": "", "--reason": "", "--write": False, "--pretty": False})
    need_jq()
    if not opts["--run"]:
        die("await-user requires --run", 2)
    root = resolve_root(opts["--root"], strict=opts["--write"])
    bind_owner_for_write(root, opts["--write"])
    status = require_active(root)
    run_dir = resolve_run_dir(root, opts["--run"])
    run_rel = rel_path(root, run_dir)
    if run_rel != status["run"]:
        die("await-user: --run does not match active session: %s" % status["run"], 1)
    kind = opts["--kind"].strip().lower()
    active = load_active(root)
    active_flow_schema = str(active.get("flow_schema", ""))
    flow_schema_value = (
        active_flow_schema
        if active_flow_schema.isdigit()
        else state.state_value(os.path.join(run_dir, "STATE.md"), "Flow schema")
    )
    flow_schema_parts = flow_schema_value.split()
    flow_schema = flow_schema_parts[0] if flow_schema_parts else ""
    recovery = state.state_value(os.path.join(run_dir, "STATE.md"), "Recovery").strip().lower()
    if kind and kind not in AWAIT_USER_KINDS:
        die("await-user: unknown --kind: %s" % kind, 2)
    schema_number = int(flow_schema) if flow_schema.isdigit() else 0
    if schema_number >= 3 and not kind:
        die("await-user: schema-%s runs require --kind" % schema_number, 2)
    if schema_number >= 4 and kind not in RECOVERY_AWAIT_USER_KINDS:
        die("await-user: schema-4 allows only material decision kinds", 2)
    if recovery == "active" and kind not in RECOVERY_AWAIT_USER_KINDS:
        die("await-user: --kind %s is not allowed during active recovery" % (kind or "<missing>"), 2)
    state_workspace_wait = state.state_value(os.path.join(run_dir, "STATE.md"), "Workspace decision used at").strip()
    if schema_number >= 4 and kind == "workspace" and (active.get("workspace_wait_used_at") or state_workspace_wait):
        die("await-user: schema-4 workspace decision was already used", 2)
    prior_active = dict(active)
    prior_active.pop("present", None)
    prior_active.pop("path", None)
    updated = dict(prior_active)
    updated["awaiting_user"] = True
    if kind:
        updated["awaiting_kind"] = kind
    else:
        updated.pop("awaiting_kind", None)
    if opts["--reason"]:
        updated["awaiting_reason"] = opts["--reason"]
    else:
        updated.pop("awaiting_reason", None)
    now = iso_now()
    updated["awaiting_since"] = now
    updated["updated_at"] = now
    if schema_number >= 4 and kind == "workspace":
        updated["workspace_wait_used_at"] = now
    if opts["--write"]:
        try:
            write_active(root, updated)
        except (OSError, ValueError) as exc:
            die("cannot persist active workspace wait: %s" % exc, 2)
        if schema_number >= 4 and kind == "workspace":
            try:
                update_state_value(run_dir, "Workspace decision used at", now)
            except ActiveError:
                try:
                    write_active(root, prior_active)
                except (OSError, ValueError) as exc:
                    die("cannot roll back incomplete workspace wait: %s" % exc, 2)
                raise
    json_print({"status": "awaiting_user", "written": opts["--write"] is True, "run": run_rel, "awaiting_kind": kind or None, "reason": opts["--reason"] or None}, opts["--pretty"])


def cmd_phase_read(args):
    opts = parse_options(args, "phase-read", {"--root": "", "--run": "", "--phase": "", "--file": "", "--write": False, "--pretty": False})
    need_jq()
    if not opts["--run"]:
        die("phase-read requires --run", 2)
    if opts["--phase"] == "":
        die("phase-read requires --phase", 2)
    if not opts["--file"]:
        die("phase-read requires --file", 2)
    root = resolve_root(opts["--root"], strict=opts["--write"])
    run_dir = resolve_run_dir(root, opts["--run"])
    try:
        record = phase_reads.record_read(root, run_dir, opts["--phase"], opts["--file"], iso_now(), write=opts["--write"])
    except phase_reads.PhaseReadError as exc:
        die(str(exc), 2)
    json_print({"status": "phase_read_recorded", "written": opts["--write"] is True, "run": rel_path(root, run_dir), "record": record}, opts["--pretty"])


def _active_for_run(root, run_rel):
    active = load_active(root)
    if active.get("present") is True and active.get("run") == run_rel:
        return active
    return None


def cmd_phase_read_status(args):
    opts = parse_options(args, "phase-read-status", {"--root": "", "--run": "", "--json": False, "--pretty": False})
    need_jq()
    if not opts["--run"]:
        die("phase-read-status requires --run", 2)
    root = resolve_root(opts["--root"], strict=False)
    run_dir = resolve_run_dir(root, opts["--run"])
    payload = phase_reads.status_payload(root, run_dir, active=_active_for_run(root, rel_path(root, run_dir)))
    json_print(payload, opts["--pretty"])


def cmd_phase_read_gate(args):
    opts = parse_options(args, "phase-read-gate", {"--root": "", "--run": "", "--through-phase": "", "--pretty": False})
    need_jq()
    if not opts["--run"]:
        die("phase-read-gate requires --run", 2)
    if opts["--through-phase"] == "":
        die("phase-read-gate requires --through-phase", 2)
    root = resolve_root(opts["--root"], strict=False)
    run_dir = resolve_run_dir(root, opts["--run"])
    try:
        verdict = phase_reads.gate(root, run_dir, opts["--through-phase"], active=_active_for_run(root, rel_path(root, run_dir)))
    except phase_reads.PhaseReadError as exc:
        die(str(exc), 2)
    sys.stdout.write(
        "PHASE_READ_GATE\t%s\tblockers=%s\treason=%s\tdetail=%s\n"
        % (verdict.get("status", "CLOSED"), verdict.get("blockers", 1), verdict.get("reason", "phase-read-blockers"), verdict.get("detail", ""))
    )


def global_metrics_file():
    base = os.environ.get("KIMIFLOW_HOME") or (os.path.join(os.environ.get("HOME", ""), ".kimiflow") if os.environ.get("HOME") else "")
    if not base or base == "/":
        return ""
    return os.path.join(base, "metrics/token-economics.jsonl")


def read_run_snapshot(run_descriptor, name):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(name, flags, dir_fd=run_descriptor)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("unsafe terminal run file")
        if info.st_size > FINISH_ARTIFACT_LIMIT:
            die("terminal run artifact exceeds snapshot limit: %s" % name, 2)
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, FINISH_ARTIFACT_LIMIT - total + 1))
            if not chunk:
                return b"".join(chunks), stat.S_IMODE(info.st_mode)
            total += len(chunk)
            if total > FINISH_ARTIFACT_LIMIT:
                die("terminal run artifact exceeds snapshot limit: %s" % name, 2)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def snapshot_finish(root, run_dir, snapshot, run_descriptor=None):
    os.makedirs(os.path.join(snapshot, "run"), exist_ok=True)
    project = os.path.join(root, ".kimiflow/project")
    if os.path.isdir(project):
        shutil.copytree(project, os.path.join(snapshot, "project"))
        write_text(os.path.join(snapshot, "project.present"), "present\n")
    else:
        write_text(os.path.join(snapshot, "project.present"), "absent\n")
    for name in FINISH_SNAPSHOT_NAMES:
        present = os.path.join(snapshot, "run", "%s.present" % name)
        try:
            if run_descriptor is None:
                src = os.path.join(run_dir, name)
                if not os.path.isfile(src):
                    raise FileNotFoundError(src)
                if os.stat(src, follow_symlinks=False).st_size > FINISH_ARTIFACT_LIMIT:
                    die("terminal run artifact exceeds snapshot limit: %s" % name, 2)
                with open(src, "rb") as handle:
                    payload = handle.read()
                mode = stat.S_IMODE(os.stat(src, follow_symlinks=False).st_mode)
            else:
                payload, mode = read_run_snapshot(run_descriptor, name)
        except FileNotFoundError:
            write_text(present, "absent\n")
        else:
            with open(os.path.join(snapshot, "run", name), "wb") as handle:
                handle.write(payload)
            write_text(os.path.join(snapshot, "run", "%s.mode" % name), "%04o\n" % mode)
            write_text(present, "present\n")
    metrics = global_metrics_file()
    if metrics and os.path.isfile(metrics):
        os.makedirs(os.path.join(snapshot, "global-metrics"), exist_ok=True)
        shutil.copy2(metrics, os.path.join(snapshot, "global-metrics/token-economics.jsonl"))
        write_text(os.path.join(snapshot, "global-metrics.present"), "present\n")
    else:
        write_text(os.path.join(snapshot, "global-metrics.present"), "absent\n")


def restore_finish(root, run_dir, snapshot, run_descriptor=None):
    project = os.path.join(root, ".kimiflow/project")
    if read_text(os.path.join(snapshot, "project.present")).strip() == "present":
        shutil.rmtree(project, ignore_errors=True)
        os.makedirs(os.path.join(root, ".kimiflow"), exist_ok=True)
        shutil.copytree(os.path.join(snapshot, "project"), project)
    else:
        shutil.rmtree(project, ignore_errors=True)
    for name in FINISH_SNAPSHOT_NAMES:
        if read_text(os.path.join(snapshot, "run", "%s.present" % name)).strip() == "present":
            source = os.path.join(snapshot, "run", name)
            if run_descriptor is None:
                shutil.copy2(source, os.path.join(run_dir, name))
            else:
                with open(source, "rb") as handle:
                    workspace_preflight.atomic_directory_write(run_descriptor, name, handle.read())
                restored_mode = int(read_text(os.path.join(snapshot, "run", "%s.mode" % name)).strip(), 8)
                flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(name, flags, dir_fd=run_descriptor)
                try:
                    os.fchmod(descriptor, restored_mode)
                finally:
                    os.close(descriptor)
        else:
            try:
                if run_descriptor is None:
                    os.unlink(os.path.join(run_dir, name))
                else:
                    os.unlink(name, dir_fd=run_descriptor)
            except OSError:
                pass
    metrics = global_metrics_file()
    if metrics:
        if read_text(os.path.join(snapshot, "global-metrics.present")).strip() == "present":
            os.makedirs(os.path.dirname(metrics), exist_ok=True)
            shutil.copy2(os.path.join(snapshot, "global-metrics/token-economics.jsonl"), metrics)
        else:
            try:
                os.unlink(metrics)
            except OSError:
                pass
def write_text(path, text):
    atomic_write(path, text, mode=0o600, refuse_symlink=True)


def write_run_text(run_descriptor, name, text):
    try:
        workspace_preflight.atomic_directory_write(run_descriptor, name, text.encode("utf-8"))
    except (OSError, UnicodeError) as exc:
        die("cannot write terminal run file %s: %s" % (name, exc), 2)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def parked_session_pins(run_dir):
    raw = read_text(os.path.join(run_dir, "SESSION-OUTCOME.json"))
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict) or payload.get("outcome") != "parked":
        return {}
    pins = payload.get("session_pins")
    return pins if isinstance(pins, dict) else {}


def session_pins(active):
    keys = (
        "mode", "scope", "host", "started_at", "started_head", "flow_schema",
        "conformance_contract", "frontend_quality_contract", "frontend_quality_start_head",
    )
    if "execution_contract" in active:
        keys = keys + ("execution_contract",)
    return {key: active.get(key) for key in keys if key in active}


def write_outcome(run_dir, outcome, reason, review, verify_line, outcome_evaluation=None, run_descriptor=None):
    value = {
        "schema_version": 1,
        "outcome": outcome,
        "reason": reason or None,
        "completed_at": iso_now(),
        "learning_review": review,
        "learning_verify": verify_line or None,
        "outcome_evaluation": outcome_evaluation,
    }
    payload = json_pretty(value) + "\n"
    if run_descriptor is None:
        write_text(os.path.join(run_dir, "SESSION-OUTCOME.json"), payload)
    else:
        write_run_text(run_descriptor, "SESSION-OUTCOME.json", payload)
    return value


def memory_router_path():
    return os.environ.get("KIMIFLOW_MEMORY_ROUTER") or os.path.join(
        os.path.abspath(os.path.dirname(__file__) + "/.."),
        "memory-router.sh",
    )


def conformance_gate_path():
    return os.environ.get("KIMIFLOW_CONFORMANCE_GATE") or os.path.join(
        os.path.abspath(os.path.dirname(__file__) + "/.."),
        "conformance-gate.sh",
    )


def require_finish_conformance(run_dir, active):
    state_path = os.path.join(run_dir, "STATE.md")
    current_contract = state.state_value(state_path, "Conformance contract").strip()
    if "conformance_contract" in active:
        pinned_contract = str(active.get("conformance_contract") or "").strip()
        if current_contract != pinned_contract:
            die("finish refused: conformance gate closed (active_conformance_contract_mismatch)", 1)
    if not current_contract:
        return
    selectors = {
        "mode": state.state_value(state_path, "Mode").strip().lower().split(" ", 1)[0],
        "scope": state.state_value(state_path, "Scope").strip().lower().split(" ", 1)[0],
        "started_head": state.state_value(state_path, "Run started head").strip(),
    }
    for key, current in selectors.items():
        if str(active.get(key, "")) != current:
            die("finish refused: conformance gate closed (active_%s_mismatch)" % key, 1)
    gate = conformance_gate_path()
    if not (os.path.isfile(gate) and os.access(gate, os.X_OK)):
        die("finish refused: conformance gate closed (gate_missing)", 1)
    proc = run_cmd([gate, run_dir, "--finish"], stderr_to_stdout=True)
    output = proc.stdout.strip()
    lines = output.splitlines() if output else []
    fields = lines[0].split("\t") if len(lines) == 1 else []
    valid_open = (
        proc.returncode == 0
        and len(fields) == 5
        and fields[:4] == ["CONFORMANCE_GATE", "OPEN", "blockers=0", "reason=clean"]
        and re.fullmatch(r"detail=basis=[0-9a-f]{64}", fields[4]) is not None
    )
    if not valid_open:
        detail = "gate_error"
        if len(fields) >= 5 and fields[4].startswith("detail="):
            detail = fields[4][len("detail="):] or detail
        elif output:
            detail = output.replace("\n", " ")[:240]
        die("finish refused: conformance gate closed (%s)" % detail, 1)


def pinned_router_environment(run_descriptor, run_rel):
    environment = os.environ.copy()
    run_info = os.fstat(run_descriptor)
    environment["KIMIFLOW_PINNED_RUN_CWD"] = "1"
    environment["KIMIFLOW_PINNED_RUN_REL"] = run_rel
    environment["KIMIFLOW_PINNED_RUN_DEVICE"] = str(run_info.st_dev)
    environment["KIMIFLOW_PINNED_RUN_INODE"] = str(run_info.st_ino)
    return environment


def evaluate_terminal_outcome(router, root, run_rel, terminal, run_descriptor=None):
    if not (os.path.exists(router) and os.access(router, os.X_OK)):
        return {"status": "error", "exit_code": 1, "reason": "memory_router_unavailable"}, 1, ""
    pinned = run_descriptor is not None
    proc = run_cmd([
        router,
        "evaluate-run",
        "--root", root,
        "--run", "." if pinned else run_rel,
        "--terminal", terminal,
        "--write",
    ], env=pinned_router_environment(run_descriptor, run_rel) if pinned else None, cwd_descriptor=run_descriptor)
    if proc.returncode != 0:
        return {
            "status": "error",
            "exit_code": proc.returncode or 1,
            "reason": "outcome_evaluation_failed",
        }, proc.returncode or 1, proc.stderr
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    evaluation = payload.get("evaluation") if isinstance(payload, dict) else None
    classification = evaluation.get("classification") if isinstance(evaluation, dict) else None
    promotable = evaluation.get("promotable") if isinstance(evaluation, dict) else None
    classification_matches_terminal = (
        classification == "inconclusive"
        or (terminal == "done" and classification == "verified_success")
        or (terminal == "failed" and classification == "verified_failure")
    )
    valid_evaluation = (
        isinstance(payload, dict)
        and payload.get("status") == "evaluated"
        and payload.get("written") is True
        and isinstance(evaluation, dict)
        and re.fullmatch(r"out_[0-9a-f]{64}", str(evaluation.get("id", ""))) is not None
        and evaluation.get("terminal") == terminal
        and classification in ("verified_success", "verified_failure", "inconclusive")
        and classification_matches_terminal
        and promotable is (classification in ("verified_success", "verified_failure"))
    )
    if not valid_evaluation:
        return {
            "status": "error",
            "exit_code": 1,
            "reason": "outcome_evaluation_malformed",
        }, 1, ""
    signals = evaluation.get("signals") if isinstance(evaluation.get("signals"), dict) else {}
    economics = evaluation.get("economics") if isinstance(evaluation.get("economics"), dict) else {}
    summary = {
        "status": "evaluated",
        "id": evaluation.get("id"),
        "terminal": evaluation.get("terminal"),
        "classification": classification,
        "promotable": promotable,
        "strategy_recall_hits": signals.get("strategy_recall_hits", 0),
        "strategy_recall_used": signals.get("strategy_recall_used") is True,
        "first_plan_success": signals.get("first_plan_success") is True,
        "economics_result": economics.get("result"),
        "net_estimated_tokens_saved": economics.get("net_estimated_tokens_saved"),
    }
    return summary, 0, ""


def read_run_json(run_descriptor, name):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(name, flags, dir_fd=run_descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None
        payload = os.read(descriptor, 1048577)
        if len(payload) > 1048576:
            return None
        value = json.loads(payload.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def valid_retirement_receipt(value):
    if not isinstance(value, dict) or value.get("status") not in ("planned", "archived"):
        return False
    for key in ("archive_path", "metadata_archive_path"):
        path = value.get(key)
        if not isinstance(path, str) or not os.path.isabs(path):
            return False
        try:
            info = os.lstat(path)
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
    return True


def retire_terminal_worktree(root, run_rel, run_descriptor=None):
    try:
        status = workspace_preflight.build_status(root)
        targets = [tree for tree in status["worktrees"] if tree.get("run") == run_rel]
        if not targets:
            if run_descriptor is not None:
                receipt = read_run_json(run_descriptor, "WORKSPACE-RETIREMENT.json")
                receipt_target = receipt.get("path") if isinstance(receipt, dict) else None
                if (
                    valid_retirement_receipt(receipt)
                    and isinstance(receipt_target, str)
                    and os.path.isabs(receipt_target)
                    and not os.path.lexists(receipt_target)
                ):
                    if receipt.get("status") == "planned":
                        receipt = {**receipt, "status": "archived", "written": True}
                        write_run_text(
                            run_descriptor,
                            "WORKSPACE-RETIREMENT.json",
                            json_pretty(receipt) + "\n",
                        )
                    return receipt
                if isinstance(receipt, dict) and receipt.get("status") == "planned":
                    return {
                        "status": "deferred",
                        "path": receipt_target,
                        "blockers": ["retirement_receipt_unverified"],
                    }
            return {"status": "not-required"}
        target = targets[0]
        if not target.get("removable"):
            return {
                "status": "deferred",
                "path": target["path"],
                "blockers": target.get("blockers", []),
            }
        def record_plan(plan):
            if run_descriptor is not None:
                write_run_text(
                    run_descriptor,
                    "WORKSPACE-RETIREMENT.json",
                    json_pretty(plan) + "\n",
                )

        result = workspace_preflight.remove(
            root,
            target["path"],
            write=True,
            before_archive=record_plan,
        )
        if run_descriptor is not None and result.get("status") == "archived":
            write_run_text(
                run_descriptor,
                "WORKSPACE-RETIREMENT.json",
                json_pretty(result) + "\n",
            )
        return result
    except workspace_preflight.WorkspaceError as exc:
        if run_descriptor is not None:
            receipt = read_run_json(run_descriptor, "WORKSPACE-RETIREMENT.json")
            receipt_target = receipt.get("path") if isinstance(receipt, dict) else None
            if (
                valid_retirement_receipt(receipt)
                and isinstance(receipt_target, str)
                and os.path.isabs(receipt_target)
                and not os.path.lexists(receipt_target)
            ):
                receipt = {
                    **receipt,
                    "status": "archived",
                    "written": True,
                    "registry_reconcile_required": True,
                    "registry_error": str(exc),
                }
                write_run_text(
                    run_descriptor,
                    "WORKSPACE-RETIREMENT.json",
                    json_pretty(receipt) + "\n",
                )
                return receipt
        return {"status": "deferred", "reason": str(exc)}


def cmd_finish(args):
    opts = parse_options(args, "finish", {"--root": "", "--write": False, "--skip-learning": "", "--pretty": False})
    need_jq()
    root = resolve_root(opts["--root"], strict=opts["--write"])
    bind_owner_for_write(root, opts["--write"])
    status = require_active(root)
    run_rel = status["run"]
    run_dir = resolve_run_dir(root, run_rel)
    active = load_active(root)
    ensure_terminal_run_path(run_dir, active)
    active_selector = str(active.get("execution_contract", "")).strip()
    state_selector = execution_control.state_contract(run_dir)
    try:
        execution_trace_present = execution_control.trace_present(root, run_dir, active)
    except execution_control.ExecutionControlError as exc:
        die("finish refused: execution control invalid (%s)" % exc, 1)
    if active_selector or state_selector or execution_trace_present:
        try:
            execution_control.require_finishable(root, run_dir, active, status["item_counts"])
        except execution_control.ExecutionControlError as exc:
            die("finish refused: execution control invalid (%s)" % exc, 1)
    if status["item_counts"]["open"] != 0:
        die("finish refused: unresolved active-session items remain", 1)
    if status["stale_risk"] != "current":
        die("finish refused: active session requires revalidation (%s)" % status["stale_risk"], 1)
    phase_gate = phase_reads.gate(root, run_dir, 7, active=active)
    if phase_gate.get("status") == "CLOSED":
        die("finish refused: phase-read gate closed (%s)" % phase_gate.get("detail", "unknown"), 1)
    require_finish_conformance(run_dir, active)
    router = memory_router_path()
    if not (os.path.exists(router) and os.access(router, os.X_OK)):
        die("memory router missing or not executable: %s" % router, 1)
    if opts["--write"]:
        snapshot = tempfile.mkdtemp(prefix="kimiflow-finish.", dir=os.environ.get("TMPDIR") or "/tmp")
        terminal_state_committed = False
        snapshot_complete = False
        try:
            with pinned_terminal_run(run_dir, active) as pinned:
                try:
                    snapshot_finish(root, run_dir, snapshot, run_descriptor=pinned["run_descriptor"])
                    snapshot_complete = True
                    review_args = [router, "review-run", "--root", root, "--run", ".", "--write"]
                    if opts["--skip-learning"]:
                        review_args.extend(["--skip", opts["--skip-learning"]])
                    review_proc = run_cmd(
                        review_args,
                        env=pinned_router_environment(pinned["run_descriptor"], run_rel),
                        cwd_descriptor=pinned["run_descriptor"],
                    )
                    if review_proc.returncode != 0:
                        restore_finish(root, run_dir, snapshot, run_descriptor=pinned["run_descriptor"])
                        if review_proc.stderr:
                            sys.stderr.write(review_proc.stderr)
                            if not review_proc.stderr.endswith("\n"):
                                sys.stderr.write("\n")
                        return review_proc.returncode or 1
                    try:
                        review = json.loads(review_proc.stdout)
                    except json.JSONDecodeError:
                        review = {"status": "unknown", "raw": review_proc.stdout}
                    verify_proc = run_cmd(
                        [router, "verify-run", "--root", root, "--run", "."],
                        env=pinned_router_environment(pinned["run_descriptor"], run_rel),
                        stderr_to_stdout=True,
                        cwd_descriptor=pinned["run_descriptor"],
                    )
                    verify = verify_proc.stdout.rstrip("\n")
                    if verify_proc.returncode != 0:
                        restore_finish(root, run_dir, snapshot, run_descriptor=pinned["run_descriptor"])
                        sys.stderr.write(verify + ("\n" if verify else ""))
                        return verify_proc.returncode
                    outcome_evaluation, evaluation_code, evaluation_stderr = evaluate_terminal_outcome(
                        router,
                        root,
                        run_rel,
                        "done",
                        run_descriptor=pinned["run_descriptor"],
                    )
                    if evaluation_code != 0:
                        restore_finish(root, run_dir, snapshot, run_descriptor=pinned["run_descriptor"])
                        if evaluation_stderr:
                            sys.stderr.write(evaluation_stderr)
                            if not evaluation_stderr.endswith("\n"):
                                sys.stderr.write("\n")
                        return evaluation_code
                    if not terminal_run_name_matches(pinned):
                        die("terminal run directory changed before final validation", 2)
                    final_active = load_active(root)
                    try:
                        final_trace_present = execution_control.trace_present(root, run_dir, final_active)
                    except execution_control.ExecutionControlError as exc:
                        die("finish refused: execution control invalid (%s)" % exc, 1)
                    if (
                        str(final_active.get("execution_contract", "")).strip()
                        or execution_control.state_contract(run_dir)
                        or final_trace_present
                    ):
                        try:
                            execution_control.require_finishable(root, run_dir, final_active, status["item_counts"])
                        except execution_control.ExecutionControlError as exc:
                            die("finish refused: execution control invalid (%s)" % exc, 1)
                    require_finish_conformance(run_dir, final_active)
                    outcome = write_outcome(
                        run_dir,
                        "done",
                        "",
                        review,
                        verify,
                        outcome_evaluation,
                        run_descriptor=pinned["run_descriptor"],
                    )
                    update_terminal_state(pinned, "done", phase7_done=True)
                    if final_trace_present:
                        try:
                            execution_control.finalize(
                                root,
                                run_dir,
                                final_active,
                                status["item_counts"],
                                write=True,
                            )
                        except execution_control.ExecutionControlError as exc:
                            die("finish refused: execution control invalid (%s)" % exc, 1)
                    terminal_state_committed = True
                    if not terminal_run_name_matches(pinned):
                        die("terminal run directory changed before retirement", 2)
                    outcome["workspace_retirement"] = retire_terminal_worktree(
                        root,
                        run_rel,
                        run_descriptor=pinned["run_descriptor"],
                    )
                    write_run_text(pinned["run_descriptor"], "SESSION-OUTCOME.json", json_pretty(outcome) + "\n")
                    retire_active_session(root, pinned, "done", run_rel)
                except ActiveError:
                    if snapshot_complete and not terminal_state_committed:
                        restore_finish(root, run_dir, snapshot, run_descriptor=pinned["run_descriptor"])
                    raise
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)
        result_status = "finished"
    else:
        outcome = {
            "schema_version": 1,
            "outcome": "preview",
            "learning_review": {"status": "preview", "written": False},
            "outcome_evaluation": {"status": "preview"},
        }
        result_status = "preview"
    json_print({"status": result_status, "written": opts["--write"] is True, "run": run_rel, "outcome": outcome}, opts["--pretty"])
    return 0


def cmd_terminal(command, args):
    mapping = {"park": ("parked", "backlog"), "fail": ("failed", "failed"), "abort": ("aborted", "aborted")}
    outcome, state_status = mapping.get(command, ("", ""))
    if not outcome:
        die("unknown terminal command: %s" % command, 2)
    opts = parse_options(args, command, {"--root": "", "--reason": "", "--write": False, "--pretty": False})
    need_jq()
    if not opts["--reason"]:
        die("%s requires --reason" % command, 2)
    root = resolve_root(opts["--root"], strict=opts["--write"])
    bind_owner_for_write(root, opts["--write"])
    status = require_active(root)
    run_rel = status["run"]
    run_dir = resolve_run_dir(root, run_rel)
    terminal_active = load_active(root)
    ensure_terminal_run_path(run_dir, terminal_active)
    if opts["--write"]:
        outcome_evaluation, _, _ = evaluate_terminal_outcome(
            memory_router_path(),
            root,
            run_rel,
            outcome,
        )
    else:
        outcome_evaluation = {"status": "preview"}
    outcome_json = {
        "schema_version": 1,
        "outcome": outcome,
        "reason": opts["--reason"],
        "completed_at": iso_now(),
        "learning_review": {"status": "not_promoted", "reason": "session_not_finished"},
        "outcome_evaluation": outcome_evaluation,
        "session_pins": session_pins(terminal_active),
    }
    if opts["--write"]:
        with pinned_terminal_run(run_dir, terminal_active) as pinned:
            write_run_text(pinned["run_descriptor"], "SESSION-OUTCOME.json", json_pretty(outcome_json) + "\n")
            update_terminal_state(pinned, state_status)
            if not terminal_run_name_matches(pinned):
                die("terminal run directory changed before retirement", 2)
            if command in ("fail", "abort"):
                outcome_json["workspace_retirement"] = retire_terminal_worktree(
                    root,
                    run_rel,
                    run_descriptor=pinned["run_descriptor"],
                )
                write_run_text(
                    pinned["run_descriptor"],
                    "SESSION-OUTCOME.json",
                    json_pretty(outcome_json) + "\n",
                )
            retire_active_session(root, pinned, state_status, run_rel)
    json_print({"status": outcome, "written": opts["--write"] is True, "run": run_rel, "outcome": outcome_json}, opts["--pretty"])


def normalize_conflict_path(root, value):
    value = value.strip()
    if not value:
        return ""
    if os.path.isabs(value):
        value = rel_path(root, value)
        if os.path.isabs(value):
            die("conflict-check path must be inside the repository: %s" % value, 2)
    normalized = os.path.normpath(value).replace(os.sep, "/")
    if normalized == ".." or normalized.startswith("../"):
        die("conflict-check path must not traverse outside the repository: %s" % value, 2)
    return normalized


def glob_static_prefix(pattern):
    match = re.search(r"[*?\[]", pattern)
    if not match:
        return pattern
    return pattern[: match.start()].rstrip("/")


def paths_overlap(left, right):
    if left == "." or right == ".":
        return True
    if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
        return True
    for pattern, path in ((left, right), (right, left)):
        if any(char in pattern for char in "*?["):
            if fnmatch.fnmatch(path, pattern):
                return True
            prefix = glob_static_prefix(pattern)
            if prefix and (prefix == path or prefix.startswith(path + "/") or path.startswith(prefix + "/")):
                return True
    return False


def parse_conflict_options(args):
    opts = {"--root": "", "--pretty": False, "--path": []}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h"):
            usage()
            raise SystemExit(0)
        if arg == "--pretty":
            opts[arg] = True
            i += 1
        elif arg in ("--root", "--path"):
            value = args[i + 1] if i + 1 < len(args) else ""
            if not value:
                die("conflict-check: %s needs a value" % arg, 2)
            if arg == "--path":
                opts[arg].append(value)
            else:
                opts[arg] = value
            i += 2
        else:
            die("conflict-check: unknown argument: %s" % arg, 2)
    return opts


def cmd_conflict_check(args):
    opts = parse_conflict_options(args)
    need_jq()
    root = resolve_root(opts["--root"], strict=False)
    intended = [normalize_conflict_path(root, value) for value in opts["--path"]]
    intended = [value for value in intended if value]
    status = status_json(root)
    base = {
        "schema_version": 1,
        "run": status.get("run"),
        "intended_paths": intended,
        "active_paths": status.get("affected_files", []),
        "overlaps": [],
    }
    if not (status.get("present") is True and status.get("terminal") is False):
        base.update({"decision": "allow_disjoint", "reason": "no_active_run"})
        json_print(base, opts["--pretty"])
        return
    owner = valid_owner(status.get("owner"))
    caller = shell_session_identity()
    if owner and same_session(owner, caller):
        base.update({"decision": "allow_disjoint", "reason": "caller_owns_active_run"})
        json_print(base, opts["--pretty"])
        return
    active_paths = [normalize_conflict_path(root, value) for value in status.get("affected_files", [])]
    active_paths = [value for value in active_paths if value]
    base["active_paths"] = active_paths
    if not owner:
        base.update({"decision": "block_unknown", "reason": "active_owner_unknown"})
        json_print(base, opts["--pretty"])
        return
    if not intended:
        base.update({"decision": "block_unknown", "reason": "intended_paths_unknown"})
        json_print(base, opts["--pretty"])
        return
    if not active_paths:
        base.update({"decision": "block_unknown", "reason": "active_paths_unknown"})
        json_print(base, opts["--pretty"])
        return
    overlaps = []
    for intended_path in intended:
        for active_path in active_paths:
            if paths_overlap(intended_path, active_path):
                overlaps.append({"intended": intended_path, "active": active_path})
    base["overlaps"] = overlaps
    if overlaps:
        base.update({"decision": "block_overlap", "reason": "path_overlap"})
    else:
        base.update({"decision": "allow_disjoint", "reason": "no_overlap"})
    json_print(base, opts["--pretty"])


def parse_hook_input(input_text):
    try:
        data = json.loads(input_text) if input_text.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def hook_root(input_text, data=None):
    data = parse_hook_input(input_text) if data is None else data
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    cwd = data.get("cwd") or tool_input.get("cwd") or data.get("working_directory") or ""
    return resolve_root(cwd or os.getcwd(), strict=False)


def transition_summary(transition):
    if not isinstance(transition, dict):
        return "unavailable"
    action = str(transition.get("action") or "none")
    current = str(transition.get("current_node") or "unknown")
    target = str(transition.get("target_node") or "unknown")
    detail = "%s at %s -> %s" % (action, current, target)
    if transition.get("target_file"):
        detail += " (%s)" % transition["target_file"]
    execution = transition.get("execution")
    if isinstance(execution, dict):
        detail += " [profile=%s profile_reason=%s strategy=%s budget=%s directive=%s]" % (
            execution.get("profile", "unknown"),
            execution.get("profile_reason", "unknown"),
            execution.get("strategy_mode", "unknown"),
            execution.get("budget_pressure", "unknown"),
            execution.get("directive", "unknown"),
        )
    return detail


def hook_owner_relation(status, data):
    owner = valid_owner(status.get("owner"))
    caller = hook_session_identity(data)
    if not owner or not caller:
        return "unknown"
    return "owner" if same_session(owner, caller) else "other"


def cmd_session_bootstrap():
    data = parse_hook_input(sys.stdin.read())
    session_id = data.get("session_id")
    env_file = os.environ.get("CLAUDE_ENV_FILE", "")
    if not (isinstance(session_id, str) and session_id and env_file):
        return 0
    try:
        with open(env_file, "a", encoding="utf-8") as handle:
            handle.write("export KIMIFLOW_SESSION_ID=%s\n" % shlex.quote(session_id))
            handle.write("export KIMIFLOW_SESSION_HOST=claude\n")
    except OSError:
        return 0
    return 0


def cmd_owner_check():
    input_text = sys.stdin.read()
    data = parse_hook_input(input_text)
    root = hook_root(input_text, data)
    status = status_json(root)
    if not (status.get("present") is True and status.get("terminal") is False):
        relation = "none"
    else:
        relation = hook_owner_relation(status, data)
    json_print({"schema_version": 1, "relation": relation, "run": status.get("run")})
    return 0


def cmd_prompt_context():
    input_text = sys.stdin.read()
    data = parse_hook_input(input_text)
    root = hook_root(input_text, data)
    status = status_json(root)
    if not (status.get("present") is True and status.get("terminal") is False):
        return 0
    relation = hook_owner_relation(status, data)
    if relation != "owner":
        affected = status.get("affected_files", [])
        affected_summary = ", ".join(affected[:8]) if affected else "unknown"
        subject = "Another session owns" if relation == "other" else "Session ownership is unknown for"
        context = (
            "%s active Kimiflow run %s (affected paths: %s). This prompt is not part of that run; read, answer, analyze, and plan normally. "
            "Before editing this checkout, run hooks/active-run.sh conflict-check --path <path> for every intended path. Edit only when it returns allow_disjoint; on block_overlap or block_unknown, wait or narrow scope. A separate Git worktree is exceptional: use it only after explicit user authority, trusted workspace-preflight registration, and the one-tree cap check."
            % (subject, status["run"], affected_summary)
        )
        sys.stdout.write(json_pretty({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}) + "\n")
        return 0
    active = load_active(root)
    if active.get("awaiting_user") is True:
        # The user answered the gate question the orchestrator was awaiting; resume the run.
        resumed = dict(active)
        for key in ("awaiting_user", "awaiting_kind", "awaiting_reason", "awaiting_since"):
            resumed.pop(key, None)
        resumed["updated_at"] = iso_now()
        write_active(root, resumed)
        status = status_json(root)
    run = status["run"]
    stale = status["stale_risk"]
    open_count = status["item_counts"]["open"]
    context = (
        "Kimiflow active session is open: %s. Treat this user prompt as part of that Kimiflow run unless the user explicitly says to exit, abort, park, or switch workflows. "
        "Do not route follow-up fixes/features to another skill. Before editing, append or update run items with hooks/active-run.sh append-item/mark-built/mark-accepted/mark-rejected/drop-item. "
        "Open item count: %s. Finish only through hooks/active-run.sh finish --write, or park/fail/abort with a reason." % (run, open_count)
    )
    if isinstance(status.get("transition"), dict):
        context += " Exact next action: %s." % transition_summary(status["transition"])
    exact_revalidation = (
        isinstance(status.get("transition"), dict)
        and status["transition"].get("action") == "revalidate_then_refresh_baseline"
    )
    if stale in ("needs_revalidation", "unknown") and not exact_revalidation:
        context += " Active-session freshness is %s; revalidate the plan/code first, then run hooks/active-run.sh refresh-baseline --write before finishing." % stale
    sys.stdout.write(json_pretty({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}) + "\n")
    return 0


def cmd_stop_gate():
    input_text = sys.stdin.read()
    data = parse_hook_input(input_text)
    active = False
    if isinstance(data, dict):
        active = data.get("stop_hook_active") is True or (isinstance(data.get("hook_input"), dict) and data["hook_input"].get("stop_hook_active") is True)
    if active:
        return 0
    root = hook_root(input_text, data)
    status = status_json(root)
    if not (status.get("present") is True and status.get("terminal") is False):
        return 0
    if hook_owner_relation(status, data) != "owner":
        return 0
    if status.get("awaiting_user") is True:
        # The orchestrator is legitimately waiting on a user answer at an engine gate
        # (set via await-user); let the turn end instead of blocking the question.
        return 0
    if execution_control.contract_enabled(load_active(root)):
        try:
            _observe_execution(
                root,
                status,
                "turn_completed",
                "neutral",
                None,
                {key: 0 for key in execution_control.USAGE_KEYS},
                True,
                coalesce_pending_stop=True,
            )
            status = status_json(root)
        except ActiveError as exc:
            reason = (
                "kimiflow execution-control gate: %s. Repair the run-local controller "
                "autonomously before stopping." % exc.message
            )
            sys.stdout.write(json_pretty({"decision": "block", "reason": reason}) + "\n")
            return 0
    if isinstance(status.get("transition"), dict):
        reason = (
            "kimiflow active-session gate: %s is still open. Open items: %s. Exact next action: %s. Continue that action, or close it mechanically with hooks/active-run.sh finish --write, park --write --reason <text>, fail --write --reason <text>, or abort --write --reason <text>."
            % (status["run"], status["item_counts"]["open"], transition_summary(status["transition"]))
        )
    else:
        reason = (
            "kimiflow active-session gate: %s is still open. Open items: %s. Continue the Kimiflow loop, or close it mechanically with hooks/active-run.sh finish --write, park --write --reason <text>, fail --write --reason <text>, or abort --write --reason <text>."
            % (status["run"], status["item_counts"]["open"])
        )
    exact_revalidation = (
        isinstance(status.get("transition"), dict)
        and status["transition"].get("action") == "revalidate_then_refresh_baseline"
    )
    if status["stale_risk"] in ("needs_revalidation", "unknown") and not exact_revalidation:
        reason += " Active-session freshness is %s, so revalidate and run hooks/active-run.sh refresh-baseline --write before finishing." % status["stale_risk"]
    sys.stdout.write(json_pretty({"decision": "block", "reason": reason}) + "\n")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        usage()
        return 2
    command, args = argv[0], argv[1:]
    try:
        if command == "status":
            cmd_status(args)
        elif command == "next-action":
            cmd_next_action(args)
        elif command == "observe":
            cmd_observe(args)
        elif command == "start":
            cmd_start(args)
        elif command == "conflict-check":
            cmd_conflict_check(args)
        elif command == "append-item":
            cmd_append_item(args)
        elif command in ("mark-built", "mark-accepted", "mark-rejected", "drop-item"):
            cmd_update_item(command, args)
        elif command == "refresh-baseline":
            cmd_refresh_baseline(args)
        elif command == "await-user":
            cmd_await_user(args)
        elif command == "phase-read":
            cmd_phase_read(args)
        elif command == "phase-read-status":
            cmd_phase_read_status(args)
        elif command == "phase-read-gate":
            cmd_phase_read_gate(args)
        elif command == "finish":
            rc = cmd_finish(args)
            if isinstance(rc, int):
                return rc
        elif command in ("park", "fail", "abort"):
            cmd_terminal(command, args)
        elif command == "session-bootstrap":
            return cmd_session_bootstrap()
        elif command == "owner-check":
            return cmd_owner_check()
        elif command == "prompt-context":
            return cmd_prompt_context()
        elif command == "stop-gate":
            return cmd_stop_gate()
        elif command in ("--help", "-h", "help"):
            usage()
        else:
            die("unknown command: %s" % command, 2)
    except ActiveError as exc:
        sys.stderr.write("active-run: %s\n" % exc.message)
        return exc.code
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
