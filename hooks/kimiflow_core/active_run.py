"""Python port of hooks/active-run.sh."""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from . import phase_reads
from .atomic import atomic_write


USAGE = """#!/usr/bin/env bash
# kimiflow — active session contract helper and hooks.
#
# Orchestrator commands:
#   active-run.sh status [--root <path>] [--pretty]
#   active-run.sh start --run <path> [--root <path>] [--mode <mode>] [--scope <scope>] [--host <host>] [--write] [--pretty]
#   active-run.sh append-item --title <text> [--kind <kind>] [--root <path>] [--write] [--pretty]
#   active-run.sh mark-built|mark-accepted --id <id> [--root <path>] [--write] [--pretty]
#   active-run.sh mark-rejected|drop-item --id <id> --reason <text> [--root <path>] [--write] [--pretty]
#   active-run.sh refresh-baseline [--root <path>] [--write] [--pretty]
#   active-run.sh phase-read --run <path> --phase <0-7> --file phases/<file>.md [--root <path>] [--write] [--pretty]
#   active-run.sh phase-read-status --run <path> [--root <path>] [--json] [--pretty]
#   active-run.sh phase-read-gate --run <path> --through-phase <0-7> [--root <path>]
#   active-run.sh finish [--root <path>] [--write] [--skip-learning <reason>] [--pretty]
#   active-run.sh park|fail|abort [--root <path>] --reason <text> [--write] [--pretty]
#
# Hook commands:
#   active-run.sh prompt-context
#   active-run.sh stop-gate
"""


class ActiveError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.message = message
        self.code = code


def usage():
    sys.stderr.write(USAGE)


def die(message, code=1):
    raise ActiveError(message, code)


def need_jq():
    if not shutil.which("jq"):
        die("jq is required", 2)


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_cmd(args, cwd=None, env=None, stderr_to_stdout=False):
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if stderr_to_stdout else subprocess.PIPE,
            text=True,
            check=False,
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


def state_value(path, label):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n").replace("\r", "").replace("**", "")
                line = re.sub(r"^[ \t]*-[ \t]*", "", line)
                match = re.match(r"^%s:[ \t]*(.*)$" % re.escape(label), line)
                if match:
                    return match.group(1)
    except OSError:
        pass
    return ""


def pathish_affected_entry(value):
    names = {"Dockerfile", "Containerfile", "Makefile", "Procfile", "Justfile", "Rakefile", "Gemfile", "Vagrantfile"}
    return "/" in value or "." in value or value in names


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
                match = re.match(r"^(Affected files|Affected paths):[ \t]*(.*)$", plain)
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


def status_json(root):
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
            "terminal": True,
        }
    run_rel = active.get("run", "")
    run_dir = resolve_run_dir(root, run_rel)
    state = os.path.join(run_dir, "STATE.md")
    affected = affected_paths(state)
    base = active.get("last_checked_head") or active.get("started_head") or "NOT VERIFIED"
    stale = stale_status(root, base, affected)
    items = items_path(run_dir)
    counts = item_counts(read_items(items))
    status = active.get("status", "active")
    phase_required = phase_reads.phase_reads_required(root, run_dir, active=active)
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
        "terminal": status in ("done", "parked", "failed", "aborted"),
        "next_action": (
            "revalidate_then_refresh_baseline"
            if stale.get("risk") in ("needs_revalidation", "unknown")
            else "resolve_or_accept_items"
            if counts["open"] > 0
            else "finish_or_continue"
        ),
    }
    if phase_required:
        result["phase_reads_required"] = True
    return result


def write_active(root, value):
    path = active_file(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, json_pretty(value) + "\n", mode=0o600, refuse_symlink=True)


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


def update_state_status(run_dir, status):
    state = os.path.join(run_dir, "STATE.md")
    if not os.path.isfile(state):
        return
    out = []
    done = False
    with open(state, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
            if re.match(r"^Status:[ \t]*", plain):
                out.append("Status: %s" % status)
                done = True
            else:
                out.append(line)
    if not done:
        out.append("Status: %s" % status)
    atomic_write(state, "\n".join(out) + "\n", mode=0o600, refuse_symlink=False)


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
    state = os.path.join(run_dir, "STATE.md")
    if not os.path.isfile(state):
        return
    out = []
    done = False
    with open(state, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            plain = re.sub(r"^[ \t]*-[ \t]*", "", line.replace("**", ""))
            if re.match(r"^Phase[ \t]+7:[ \t]*", plain):
                out.append("Phase 7: done")
                done = True
            else:
                out.append(line)
    if not done:
        out.append("Phase 7: done")
    atomic_write(state, "\n".join(out) + "\n", mode=0o600, refuse_symlink=False)


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
    phase_required = phase_reads.manifest_exists(root)
    status = {
        "schema_version": 1,
        "status": "active",
        "run": run_rel,
        "mode": opts["--mode"],
        "scope": opts["--scope"],
        "host": opts["--host"],
        "started_at": iso_now(),
        "updated_at": iso_now(),
        "started_head": git_head(root),
        "last_checked_head": git_head(root),
        "affected_files_at_start": affected_paths(os.path.join(run_dir, "STATE.md")),
    }
    if phase_required:
        status["phase_reads_required"] = True
    if write:
        os.makedirs(run_dir, exist_ok=True)
        if phase_required:
            ensure_state_phase_reads_required(run_dir)
        write_active(root, status)
    json_print(status_json(root), opts["--pretty"])


def cmd_append_item(args):
    opts = parse_options(args, "append-item", {"--root": "", "--title": "", "--kind": "change", "--write": False, "--pretty": False})
    need_jq()
    if not opts["--title"]:
        die("append-item requires --title", 2)
    root = resolve_root(opts["--root"], strict=opts["--write"])
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
    opts = parse_options(args, "refresh-baseline", {"--root": "", "--write": False, "--pretty": False})
    need_jq()
    root = resolve_root(opts["--root"], strict=opts["--write"])
    status = require_active(root)
    active = load_active(root)
    run_dir = resolve_run_dir(root, status["run"])
    refreshed = dict(active)
    refreshed["updated_at"] = iso_now()
    refreshed["last_checked_head"] = git_head(root)
    refreshed["affected_files_at_last_check"] = affected_paths(os.path.join(run_dir, "STATE.md"))
    if opts["--write"]:
        write_active(root, refreshed)
    json_print(status_json(root), opts["--pretty"])


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


def snapshot_finish(root, run_dir, snapshot):
    os.makedirs(os.path.join(snapshot, "run"), exist_ok=True)
    project = os.path.join(root, ".kimiflow/project")
    if os.path.isdir(project):
        shutil.copytree(project, os.path.join(snapshot, "project"))
        write_text(os.path.join(snapshot, "project.present"), "present\n")
    else:
        write_text(os.path.join(snapshot, "project.present"), "absent\n")
    for name in ("LEARNING-REVIEW.md", "RUN-LIFECYCLE.json", "RUN-LIFECYCLE.md", "SESSION-OUTCOME.json"):
        src = os.path.join(run_dir, name)
        present = os.path.join(snapshot, "run", "%s.present" % name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(snapshot, "run", name))
            write_text(present, "present\n")
        else:
            write_text(present, "absent\n")
    metrics = global_metrics_file()
    if metrics and os.path.isfile(metrics):
        os.makedirs(os.path.join(snapshot, "global-metrics"), exist_ok=True)
        shutil.copy2(metrics, os.path.join(snapshot, "global-metrics/token-economics.jsonl"))
        write_text(os.path.join(snapshot, "global-metrics.present"), "present\n")
    else:
        write_text(os.path.join(snapshot, "global-metrics.present"), "absent\n")


def restore_finish(root, run_dir, snapshot):
    project = os.path.join(root, ".kimiflow/project")
    if read_text(os.path.join(snapshot, "project.present")).strip() == "present":
        shutil.rmtree(project, ignore_errors=True)
        os.makedirs(os.path.join(root, ".kimiflow"), exist_ok=True)
        shutil.copytree(os.path.join(snapshot, "project"), project)
    else:
        shutil.rmtree(project, ignore_errors=True)
    for name in ("LEARNING-REVIEW.md", "RUN-LIFECYCLE.json", "RUN-LIFECYCLE.md", "SESSION-OUTCOME.json"):
        dest = os.path.join(run_dir, name)
        if read_text(os.path.join(snapshot, "run", "%s.present" % name)).strip() == "present":
            shutil.copy2(os.path.join(snapshot, "run", name), dest)
        else:
            try:
                os.unlink(dest)
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
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def write_outcome(run_dir, outcome, reason, review, verify_line):
    value = {
        "schema_version": 1,
        "outcome": outcome,
        "reason": reason or None,
        "completed_at": iso_now(),
        "learning_review": review,
        "learning_verify": verify_line or None,
    }
    write_text(os.path.join(run_dir, "SESSION-OUTCOME.json"), json_pretty(value) + "\n")
    return value


def cmd_finish(args):
    opts = parse_options(args, "finish", {"--root": "", "--write": False, "--skip-learning": "", "--pretty": False})
    need_jq()
    root = resolve_root(opts["--root"], strict=opts["--write"])
    status = require_active(root)
    run_rel = status["run"]
    run_dir = resolve_run_dir(root, run_rel)
    if status["item_counts"]["open"] != 0:
        die("finish refused: unresolved active-session items remain", 1)
    if status["stale_risk"] != "current":
        die("finish refused: active session requires revalidation (%s)" % status["stale_risk"], 1)
    phase_gate = phase_reads.gate(root, run_dir, 7, active=load_active(root))
    if phase_gate.get("status") == "CLOSED":
        die("finish refused: phase-read gate closed (%s)" % phase_gate.get("detail", "unknown"), 1)
    router = os.environ.get("KIMIFLOW_MEMORY_ROUTER") or os.path.join(os.path.abspath(os.path.dirname(__file__) + "/.."), "memory-router.sh")
    if not (os.path.exists(router) and os.access(router, os.X_OK)):
        die("memory router missing or not executable: %s" % router, 1)
    if opts["--write"]:
        snapshot = tempfile.mkdtemp(prefix="kimiflow-finish.", dir=os.environ.get("TMPDIR") or "/tmp")
        try:
            snapshot_finish(root, run_dir, snapshot)
            review_args = [router, "review-run", "--root", root, "--run", run_rel, "--write"]
            if opts["--skip-learning"]:
                review_args.extend(["--skip", opts["--skip-learning"]])
            review_proc = run_cmd(review_args)
            if review_proc.returncode != 0:
                return review_proc.returncode or 1
            try:
                review = json.loads(review_proc.stdout)
            except json.JSONDecodeError:
                review = {"status": "unknown", "raw": review_proc.stdout}
            verify_proc = run_cmd([router, "verify-run", "--root", root, "--run", run_rel], stderr_to_stdout=True)
            verify = verify_proc.stdout.rstrip("\n")
            if verify_proc.returncode != 0:
                restore_finish(root, run_dir, snapshot)
                sys.stderr.write(verify + ("\n" if verify else ""))
                return verify_proc.returncode
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)
        outcome = write_outcome(run_dir, "done", "", review, verify)
        update_state_status(run_dir, "done")
        update_state_phase7_done(run_dir)
        try:
            os.unlink(active_file(root))
        except OSError:
            pass
        result_status = "finished"
    else:
        outcome = {"schema_version": 1, "outcome": "preview", "learning_review": {"status": "preview", "written": False}}
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
    status = require_active(root)
    run_rel = status["run"]
    run_dir = resolve_run_dir(root, run_rel)
    outcome_json = {
        "schema_version": 1,
        "outcome": outcome,
        "reason": opts["--reason"],
        "completed_at": iso_now(),
        "learning_review": {"status": "not_promoted", "reason": "session_not_finished"},
    }
    if opts["--write"]:
        write_text(os.path.join(run_dir, "SESSION-OUTCOME.json"), json_pretty(outcome_json) + "\n")
        update_state_status(run_dir, state_status)
        try:
            os.unlink(active_file(root))
        except OSError:
            pass
    json_print({"status": outcome, "written": opts["--write"] is True, "run": run_rel, "outcome": outcome_json}, opts["--pretty"])


def hook_root(input_text):
    cwd = ""
    try:
        data = json.loads(input_text) if input_text.strip() else {}
        if isinstance(data, dict):
            cwd = data.get("cwd") or (data.get("tool_input") or {}).get("cwd") or data.get("working_directory") or ""
    except json.JSONDecodeError:
        cwd = ""
    return resolve_root(cwd or os.getcwd(), strict=False)


def cmd_prompt_context():
    input_text = sys.stdin.read()
    root = hook_root(input_text)
    status = status_json(root)
    if not (status.get("present") is True and status.get("terminal") is False):
        return 0
    run = status["run"]
    stale = status["stale_risk"]
    open_count = status["item_counts"]["open"]
    context = (
        "Kimiflow active session is open: %s. Treat this user prompt as part of that Kimiflow run unless the user explicitly says to exit, abort, park, or switch workflows. "
        "Do not route follow-up fixes/features to another skill. Before editing, append or update run items with hooks/active-run.sh append-item/mark-built/mark-accepted/mark-rejected/drop-item. "
        "Open item count: %s. Finish only through hooks/active-run.sh finish --write, or park/fail/abort with a reason." % (run, open_count)
    )
    if stale in ("needs_revalidation", "unknown"):
        context += " Active-session freshness is %s; revalidate the plan/code first, then run hooks/active-run.sh refresh-baseline --write before finishing." % stale
    sys.stdout.write(json_pretty({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}) + "\n")
    return 0


def cmd_stop_gate():
    input_text = sys.stdin.read()
    try:
        data = json.loads(input_text) if input_text.strip() else {}
    except json.JSONDecodeError:
        data = {}
    active = False
    if isinstance(data, dict):
        active = data.get("stop_hook_active") is True or (isinstance(data.get("hook_input"), dict) and data["hook_input"].get("stop_hook_active") is True)
    if active:
        return 0
    root = hook_root(input_text)
    status = status_json(root)
    if not (status.get("present") is True and status.get("terminal") is False):
        return 0
    reason = (
        "kimiflow active-session gate: %s is still open. Open items: %s. Continue the Kimiflow loop, or close it mechanically with hooks/active-run.sh finish --write, park --write --reason <text>, fail --write --reason <text>, or abort --write --reason <text>."
        % (status["run"], status["item_counts"]["open"])
    )
    if status["stale_risk"] in ("needs_revalidation", "unknown"):
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
        elif command == "start":
            cmd_start(args)
        elif command == "append-item":
            cmd_append_item(args)
        elif command in ("mark-built", "mark-accepted", "mark-rejected", "drop-item"):
            cmd_update_item(command, args)
        elif command == "refresh-baseline":
            cmd_refresh_baseline(args)
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
