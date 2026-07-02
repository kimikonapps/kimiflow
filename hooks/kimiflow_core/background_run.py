"""Python port of hooks/background-run.sh."""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


USAGE = """#!/usr/bin/env bash
# kimiflow — local Background Handles registry and collect gate.
#
# Orchestrator commands:
#   background-run.sh start --kind <kind> --title <text> --affected <path> [--root <path>] [--write] [--pretty]
#   background-run.sh list [--root <path>] [--json|--pretty]
#   background-run.sh status --id <id> [--root <path>] [--pretty]
#   background-run.sh update --id <id> --status <status> [--result <file>] [--files <file>] [--advisories <file>] [--verify <file>] [--reason <text>] [--root <path>] [--write] [--pretty]
#   background-run.sh collect --id <id> [--root <path>]
#   background-run.sh cancel|mark-stale --id <id> --reason <text> [--root <path>] [--write] [--pretty]
#
# R2 invariant example:
#   hooks/background-run.sh
set -u
"""

VALID_KINDS = {"deep-codebase", "docs", "security", "improve", "custom"}
VALID_STATUSES = {"pending", "running", "ready", "finished", "stale", "failed", "cancelled"}
TERMINAL_STATUSES = {"stale", "failed", "cancelled"}
ID_RE = re.compile(r"^bh_[A-Za-z0-9_-]+$")


class BackgroundError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.message = message
        self.code = code


def usage():
    sys.stderr.write(USAGE)


def die(message, code=1):
    raise BackgroundError(message, code)


def need_jq():
    if not shutil.which("jq"):
        die("jq is required", 2)


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_git(root, args):
    try:
        proc = subprocess.run(
            ["git", "-C", root] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return proc


def resolve_root(root_arg="", strict=False):
    if root_arg:
        if os.path.isdir(root_arg):
            return os.path.abspath(root_arg)
        # R1 root divergence: mutating --write commands pass strict=True so
        # they do not create background state under a known-invalid root.
        if strict:
            die("cannot resolve root: %s" % root_arg, 2)
        return root_arg
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return os.getcwd()
    root = proc.stdout.strip()
    if proc.returncode == 0 and root:
        return root
    return os.getcwd()


def git_head(root):
    proc = run_git(root, ["rev-parse", "HEAD"])
    if proc and proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "NOT VERIFIED"


def git_commit_ok(root, commit):
    if not commit or commit == "NOT VERIFIED":
        return False
    proc = run_git(root, ["cat-file", "-e", "%s^{commit}" % commit])
    return bool(proc and proc.returncode == 0)


def background_dir(root):
    return os.path.join(root, ".kimiflow/background")


def handle_dir(root, ident):
    return os.path.join(background_dir(root), ident)


def index_file(root):
    return os.path.join(background_dir(root), "HANDLES.jsonl")


def validate_id(ident):
    return bool(ID_RE.match(ident or ""))


def new_id():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    try:
        rand = os.urandom(4).hex()
    except OSError:
        rand = "00000000"
    return "bh_%s_%s" % (stamp, rand)


def normalize_affected_one(path):
    path = path.strip()
    while path.startswith("./"):
        path = path[2:]
    while path.endswith("/"):
        path = path[:-1]
    if (
        path in ("", ".", "..", ".kimiflow")
        or path.startswith("/")
        or path.startswith("../")
        or "/../" in path
        or path.endswith("/..")
        or "/./" in path
        or path.endswith("/.")
        or "//" in path
        or path.startswith(".kimiflow/")
    ):
        return None
    return path


def affected_json_from_args(paths):
    out = []
    for raw in paths:
        for path in str(raw).splitlines():
            if not path:
                continue
            normalized = normalize_affected_one(path)
            if normalized is None:
                return None
            if normalized not in out:
                out.append(normalized)
    return out


def normalize_affected_json(raw):
    if not isinstance(raw, list) or not all(isinstance(item, str) and len(item) > 0 for item in raw):
        return None
    out = []
    for path in raw:
        normalized = normalize_affected_one(path)
        if normalized is None or normalized != path:
            return None
        if normalized not in out:
            out.append(normalized)
    if not out:
        return None
    return out


def json_compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def json_print(value, pretty=False):
    if pretty:
        sys.stdout.write(json_pretty(value) + "\n")
    else:
        sys.stdout.write(json_compact(value) + "\n")


def write_json_pretty(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json_pretty(value) + "\n")


def write_index_event(root, value):
    file_path = index_file(root)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "a", encoding="utf-8") as handle:
        handle.write(json_compact(value) + "\n")


def status_file_for_id(root, ident):
    # R1 fail-closed divergence: the Bash helper validated ids inside command
    # substitution, then sometimes continued into a misleading "handle not found".
    if not validate_id(ident):
        die("unsafe handle id", 2)
    directory = handle_dir(root, ident)
    if os.path.islink(directory):
        die("unsafe handle dir", 2)
    return os.path.join(directory, "STATUS.json")


def load_status(root, ident):
    file_path = status_file_for_id(root, ident)
    if os.path.islink(file_path):
        die("unsafe handle status: %s" % ident, 2)
    if not os.path.isfile(file_path):
        die("handle not found: %s" % ident, 1)
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        die("invalid handle status: %s" % ident, 1)


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
        if not proc or not proc.stdout:
            continue
        for path in proc.stdout.splitlines():
            if path and not path.startswith(".kimiflow/"):
                paths.add(path)
    return sorted(paths)


def path_matches(changed, affected):
    if "*" in affected or "?" in affected:
        return fnmatch.fnmatchcase(changed, affected)
    return changed == affected or changed.startswith(affected + "/")


def stale_matches(changed, affected):
    matches = []
    for changed_path in changed:
        for affected_path in affected:
            if path_matches(changed_path, affected_path) and changed_path not in matches:
                matches.append(changed_path)
    return matches


def collect_line(verdict, ident, status, reason, detail=""):
    return "BACKGROUND_HANDLE\t%s\tid=%s\tstatus=%s\treason=%s\tdetail=%s" % (
        verdict,
        ident,
        status,
        reason,
        detail,
    )


def collect_status(root, ident):
    current = load_status(root, ident)
    if not isinstance(current, dict):
        die("invalid handle status: %s" % ident, 1)
    status = str(current.get("status", ""))
    if status not in ("ready", "finished"):
        if status in ("stale", "cancelled", "failed"):
            return collect_line("CLOSED", ident, status, "status_%s" % status, "")
        return collect_line("CLOSED", ident, status, "not_ready", "")

    result_path = ".kimiflow/background/%s/RESULT.md" % ident
    result_file = os.path.join(handle_dir(root, ident), "RESULT.md")
    if os.path.islink(result_file):
        return collect_line("CLOSED", ident, status, "result_invalid", result_path)
    if not os.path.isfile(result_file) or os.path.getsize(result_file) == 0:
        return collect_line("CLOSED", ident, status, "result_missing", result_path)

    base = str(current.get("base_commit", ""))
    if not git_commit_ok(root, base):
        return collect_line("CLOSED", ident, status, "base_invalid", base)

    raw_affected = current.get("affected_paths", [])
    if not isinstance(raw_affected, list) or not all(
        isinstance(item, str) and len(item) > 0 for item in raw_affected
    ):
        return collect_line("CLOSED", ident, status, "affected_invalid", "")
    if not raw_affected:
        return collect_line("CLOSED", ident, status, "affected_missing", "")
    affected = normalize_affected_json(raw_affected)
    if affected is None:
        return collect_line("CLOSED", ident, status, "affected_invalid", "")

    matches = stale_matches(changed_paths(root, base), affected)
    if matches:
        return collect_line("CLOSED", ident, status, "stale", ",".join(matches))
    return collect_line("OPEN", ident, status, "clean", result_path)


def parse_collect_line(line):
    fields = line.split("\t")
    verdict = fields[1] if len(fields) > 1 else "CLOSED"
    reason = ""
    detail = ""
    for field in fields:
        if field.startswith("reason="):
            reason = field[len("reason=") :]
        elif field.startswith("detail="):
            detail = field[len("detail=") :]
    return verdict, reason, detail


def list_json(root):
    items = []
    directory = background_dir(root)
    if os.path.isdir(directory):
        status_files = []
        for name in os.listdir(directory):
            candidate = os.path.join(directory, name, "STATUS.json")
            if os.path.isfile(candidate) and not os.path.islink(candidate):
                status_files.append(candidate)
        for file_path in sorted(status_files):
            try:
                with open(file_path, "r", encoding="utf-8") as handle:
                    item = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            ident = str(item.get("id", ""))
            status = str(item.get("status", ""))
            if not validate_id(ident) or status not in VALID_STATUSES:
                continue
            if status in ("ready", "finished"):
                try:
                    line = collect_status(root, ident)
                    verdict, reason, detail = parse_collect_line(line)
                except BackgroundError:
                    verdict, reason, detail = "CLOSED", "status_invalid", ""
                item = dict(item)
                item["collect_verdict"] = verdict
                item["collect_reason"] = reason
                item["collect_detail"] = detail
            items.append(item)

    return {
        "schema_version": 1,
        "present": len(items) > 0,
        "path": ".kimiflow/background",
        "total": len(items),
        "pending": sum(1 for item in items if item.get("status") == "pending"),
        "running": sum(1 for item in items if item.get("status") == "running"),
        "ready": sum(1 for item in items if item.get("status") == "ready"),
        "finished": sum(1 for item in items if item.get("status") == "finished"),
        "collectable": sum(1 for item in items if item.get("collect_verdict") == "OPEN"),
        "stale": sum(1 for item in items if item.get("status") == "stale" or item.get("collect_reason") == "stale"),
        "failed": sum(1 for item in items if item.get("status") == "failed"),
        "cancelled": sum(1 for item in items if item.get("status") == "cancelled"),
        "items": items,
    }


def copy_to_temp(src, tmp):
    if not src:
        return
    if not os.path.isfile(src):
        sys.stderr.write("background-run: source file missing: %s\n" % src)
        raise SystemExit(1)
    if not tmp:
        sys.stderr.write("background-run: cannot create temp file\n")
        raise SystemExit(1)
    shutil.copyfile(src, tmp)


def cleanup_temps(*temps):
    for tmp in temps:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def ensure_regular_file_target(path):
    if os.path.islink(path) or (os.path.exists(path) and not os.path.isfile(path)):
        die("unsafe handle file target: %s" % path, 1)


def make_temp(directory, prefix):
    fd, path = tempfile.mkstemp(prefix=prefix, dir=directory)
    os.close(fd)
    return path


def install_temp(tmp, dest):
    if not tmp:
        return
    if os.path.islink(dest) or (os.path.exists(dest) and not os.path.isfile(dest)):
        cleanup_temps(tmp)
        die("unsafe handle file target: %s" % dest, 1)
    os.replace(tmp, dest)


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
        elif isinstance(default, list):
            out[arg].append(args[i + 1] if i + 1 < len(args) else "")
            i += 2
        else:
            out[arg] = args[i + 1] if i + 1 < len(args) else ""
            i += 2
    return out


def cmd_start(args):
    opts = parse_options(
        args,
        "start",
        {"--root": "", "--kind": "", "--title": "", "--affected": [], "--write": False, "--pretty": False},
    )
    need_jq()
    kind = opts["--kind"]
    title = opts["--title"]
    write = bool(opts["--write"])
    root = resolve_root(opts["--root"], strict=write)
    if kind not in VALID_KINDS:
        die("invalid kind: %s" % kind, 2)
    if not title:
        die("start requires --title", 2)
    if not opts["--affected"]:
        die("start requires --affected", 2)
    affected = affected_json_from_args(opts["--affected"])
    if affected is None:
        die("unsafe affected path", 2)
    if len(affected) == 0:
        die("start requires affected paths", 2)

    ident = new_id()
    directory = handle_dir(root, ident)
    now = iso_now()
    base = git_head(root)
    status = {
        "schema_version": 1,
        "id": ident,
        "kind": kind,
        "title": title,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "base_commit": base,
        "affected_paths": affected,
        "handoff_path": ".kimiflow/background/%s/HANDOFF.md" % ident,
        "result_path": ".kimiflow/background/%s/RESULT.md" % ident,
        "files_path": ".kimiflow/background/%s/FILES.json" % ident,
        "advisories_path": ".kimiflow/background/%s/ADVISORIES.md" % ident,
        "verify_path": ".kimiflow/background/%s/VERIFY.md" % ident,
        "candidate_only": kind in ("security", "improve"),
        "collect_policy": "foreground_orchestrator_verifies_before_apply",
    }
    if write:
        os.makedirs(directory, exist_ok=True)
        write_json_pretty(os.path.join(directory, "STATUS.json"), status)
        with open(os.path.join(directory, "HANDOFF.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "Background Handle: %s\nKind: %s\nTitle: %s\nBase commit: %s\nAffected paths:\n"
                % (ident, kind, title, base)
            )
            for path in affected:
                handle.write("- %s\n" % path)
        with open(os.path.join(directory, "FILES.json"), "w", encoding="utf-8") as handle:
            handle.write("[]\n")
        open(os.path.join(directory, "ADVISORIES.md"), "w", encoding="utf-8").close()
        open(os.path.join(directory, "VERIFY.md"), "w", encoding="utf-8").close()
        write_index_event(root, status)
    json_print(status, bool(opts["--pretty"]))


def cmd_list(args):
    opts = parse_options(args, "list", {"--root": "", "--json": False, "--pretty": False})
    need_jq()
    root = resolve_root(opts["--root"], strict=False)
    json_print(list_json(root), bool(opts["--pretty"]))


def cmd_status(args):
    opts = parse_options(args, "status", {"--root": "", "--id": "", "--pretty": False})
    need_jq()
    ident = opts["--id"]
    if not ident:
        die("status requires --id", 2)
    root = resolve_root(opts["--root"], strict=False)
    json_print(load_status(root, ident), bool(opts["--pretty"]))


def validate_files_json(path):
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            json.load(handle)
        return True
    except (OSError, json.JSONDecodeError):
        return False


def cmd_update(args):
    opts = parse_options(
        args,
        "update",
        {
            "--root": "",
            "--id": "",
            "--status": "",
            "--result": "",
            "--files": "",
            "--advisories": "",
            "--verify": "",
            "--reason": "",
            "--write": False,
            "--pretty": False,
        },
    )
    need_jq()
    ident = opts["--id"]
    new_status = opts["--status"]
    write = bool(opts["--write"])
    if not ident:
        die("update requires --id", 2)
    if new_status not in VALID_STATUSES:
        die("invalid status: %s" % new_status, 2)
    root = resolve_root(opts["--root"], strict=write)
    current = load_status(root, ident)
    if not isinstance(current, dict):
        die("invalid handle status: %s" % ident, 1)
    current_status = str(current.get("status", ""))
    if current_status in TERMINAL_STATUSES and new_status != current_status:
        die("terminal handle cannot transition from %s to %s" % (current_status, new_status), 1)
    directory = handle_dir(root, ident)
    if os.path.islink(directory):
        die("unsafe handle dir", 2)

    tmp_result = tmp_files = tmp_advisories = tmp_verify = tmp_status = ""
    if write:
        for name in ("RESULT.md", "FILES.json", "ADVISORIES.md", "VERIFY.md", "STATUS.json"):
            ensure_regular_file_target(os.path.join(directory, name))
        try:
            if opts["--result"]:
                tmp_result = make_temp(directory, ".RESULT.md.tmp.")
            if opts["--files"]:
                tmp_files = make_temp(directory, ".FILES.json.tmp.")
            if opts["--advisories"]:
                tmp_advisories = make_temp(directory, ".ADVISORIES.md.tmp.")
            if opts["--verify"]:
                tmp_verify = make_temp(directory, ".VERIFY.md.tmp.")
            copy_to_temp(opts["--result"], tmp_result)
            copy_to_temp(opts["--files"], tmp_files)
            copy_to_temp(opts["--advisories"], tmp_advisories)
            copy_to_temp(opts["--verify"], tmp_verify)
            files_check = tmp_files or os.path.join(directory, "FILES.json")
            if not validate_files_json(files_check):
                cleanup_temps(tmp_result, tmp_files, tmp_advisories, tmp_verify)
                die("FILES.json must be valid JSON", 1)
        except (OSError, SystemExit):
            cleanup_temps(tmp_result, tmp_files, tmp_advisories, tmp_verify)
            raise

    updated = dict(current)
    updated["status"] = new_status
    updated["updated_at"] = iso_now()
    if opts["--reason"]:
        updated["reason"] = opts["--reason"]

    if write:
        try:
            tmp_status = make_temp(directory, ".STATUS.json.tmp.")
            write_json_pretty(tmp_status, updated)
            install_temp(tmp_result, os.path.join(directory, "RESULT.md"))
            install_temp(tmp_files, os.path.join(directory, "FILES.json"))
            install_temp(tmp_advisories, os.path.join(directory, "ADVISORIES.md"))
            install_temp(tmp_verify, os.path.join(directory, "VERIFY.md"))
            install_temp(tmp_status, os.path.join(directory, "STATUS.json"))
            write_index_event(root, updated)
        except OSError:
            cleanup_temps(tmp_result, tmp_files, tmp_advisories, tmp_verify, tmp_status)
            die("cannot install %s" % os.path.join(directory, "STATUS.json"), 1)
    json_print(updated, bool(opts["--pretty"]))


def cmd_collect(args):
    opts = parse_options(args, "collect", {"--root": "", "--id": ""})
    need_jq()
    ident = opts["--id"]
    if not ident:
        die("collect requires --id", 2)
    root = resolve_root(opts["--root"], strict=False)
    sys.stdout.write(collect_status(root, ident) + "\n")


def cmd_terminal(command, args):
    new_status = {"cancel": "cancelled", "mark-stale": "stale"}.get(command)
    if not new_status:
        die("unknown terminal command: %s" % command, 2)
    opts = parse_options(
        args,
        command,
        {"--root": "", "--id": "", "--reason": "", "--write": False, "--pretty": False},
    )
    if not opts["--reason"]:
        die("%s requires --reason" % command, 2)
    update_args = ["--root", opts["--root"], "--id", opts["--id"], "--status", new_status, "--reason", opts["--reason"]]
    if opts["--write"]:
        update_args.append("--write")
    if opts["--pretty"]:
        update_args.append("--pretty")
    cmd_update(update_args)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        usage()
        return 2
    command, args = argv[0], argv[1:]
    try:
        if command == "start":
            cmd_start(args)
        elif command == "list":
            cmd_list(args)
        elif command == "status":
            cmd_status(args)
        elif command == "update":
            cmd_update(args)
        elif command == "collect":
            cmd_collect(args)
        elif command in ("cancel", "mark-stale"):
            cmd_terminal(command, args)
        elif command in ("--help", "-h", "help"):
            usage()
        else:
            die("unknown command: %s" % command, 2)
    except BackgroundError as exc:
        sys.stderr.write("background-run: %s\n" % exc.message)
        return exc.code
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
