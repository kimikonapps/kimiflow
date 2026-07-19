"""Optional Codex headless controller over the existing Kimiflow active-run core."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from . import active_run, workspace_preflight
from .paths import RootResolutionError, resolve_root


RECEIPT_RELATIVE = ".kimiflow/session/HEADLESS_RUN.json"
MAX_RECEIPT_BYTES = 64 * 1024
TRANSPORT_RETRIES = 2
TERMINAL_OUTCOMES = {"done", "parked", "failed", "aborted"}
RESUMABLE_WAIT_STATES = {"awaiting_user", "parked"}
RESUMABLE_STATES = RESUMABLE_WAIT_STATES | {"running", "interrupted", "transport_error"}
RECEIPT_STATES = RESUMABLE_STATES | {
    "done", "failed", "aborted", "ownership_conflict", "no_kimiflow_run"
}


class RunnerError(Exception):
    def __init__(self, status, message, code=1):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


@dataclass
class TurnResult:
    returncode: int
    thread_id: str = ""
    error_code: str = ""


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def receipt_path(root):
    return os.path.join(root, *RECEIPT_RELATIVE.split("/"))


def _reject_duplicate_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key: %s" % key)
        value[key] = item
    return value


def _validate_receipt(root, value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RunnerError("invalid_receipt", "runner receipt has an unsupported schema", 2)
    required_strings = ("host", "root", "thread_id", "status", "started_at", "updated_at")
    if any(not isinstance(value.get(key), str) or not value.get(key) for key in required_strings):
        raise RunnerError("invalid_receipt", "runner receipt is incomplete", 2)
    if (
        value.get("host") != "codex"
        or not os.path.isabs(value.get("root"))
        or os.path.realpath(value.get("root")) != os.path.realpath(root)
    ):
        raise RunnerError("receipt_mismatch", "runner receipt belongs to another host or project", 1)
    if not re.fullmatch(r"[0-9A-Fa-f-]{16,64}", value.get("thread_id")):
        raise RunnerError("invalid_receipt", "runner receipt has an invalid Codex thread ID", 2)
    if value.get("status") not in RECEIPT_STATES:
        raise RunnerError("invalid_receipt", "runner receipt has an invalid status", 2)
    if not isinstance(value.get("turns"), int) or value.get("turns") < 0:
        raise RunnerError("invalid_receipt", "runner receipt has an invalid turn count", 2)
    active = value.get("active_run")
    if active is not None:
        normalized = os.path.normpath(active).replace(os.sep, "/") if isinstance(active, str) else ""
        if not normalized.startswith(".kimiflow/") or normalized != active or "/../" in "/%s/" % active:
            raise RunnerError("invalid_receipt", "runner receipt has an invalid active run", 2)
    return dict(value)


def write_receipt(root, value):
    value = _validate_receipt(root, value)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with workspace_preflight.registry_directory(root, create=True) as descriptor:
            try:
                target = os.stat("HEADLESS_RUN.json", dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                target = None
            if target is not None and not stat.S_ISREG(target.st_mode):
                raise RunnerError("unsafe_receipt", "runner receipt is not a safe regular file", 2)
            workspace_preflight.atomic_directory_write(descriptor, "HEADLESS_RUN.json", payload.encode("utf-8"))
    except RunnerError:
        raise
    except (OSError, UnicodeError, workspace_preflight.WorkspaceError) as exc:
        raise RunnerError("unsafe_receipt", "cannot write runner receipt: %s" % exc, 2)


def load_receipt(root, required=True):
    descriptor = None
    file_descriptor = None
    try:
        with workspace_preflight.registry_directory(root, create=False) as descriptor:
            if descriptor is None:
                if required:
                    raise RunnerError("receipt_missing", "no resumable Kimiflow terminal run exists", 1)
                return None
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                file_descriptor = os.open("HEADLESS_RUN.json", flags, dir_fd=descriptor)
            except FileNotFoundError:
                if required:
                    raise RunnerError("receipt_missing", "no resumable Kimiflow terminal run exists", 1)
                return None
            except OSError as exc:
                raise RunnerError("unsafe_receipt", "cannot safely open runner receipt: %s" % exc, 2)
            info = os.fstat(file_descriptor)
            named = os.stat("HEADLESS_RUN.json", dir_fd=descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_RECEIPT_BYTES
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise RunnerError("unsafe_receipt", "runner receipt is not a safe regular file", 2)
            payload = os.read(file_descriptor, MAX_RECEIPT_BYTES + 1)
            if len(payload) > MAX_RECEIPT_BYTES:
                raise RunnerError("unsafe_receipt", "runner receipt is too large", 2)
            value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
    except RunnerError:
        raise
    except workspace_preflight.WorkspaceError as exc:
        raise RunnerError("unsafe_receipt", "cannot safely open runner state directory: %s" % exc, 2)
    except (OSError, UnicodeError, ValueError) as exc:
        raise RunnerError("invalid_receipt", "cannot read runner receipt: %s" % exc, 2)
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
    return _validate_receipt(root, value)


def _resolve_project_root(root=None):
    try:
        resolved = resolve_root(root=root, mode="strict")
    except RootResolutionError as exc:
        raise RunnerError("root_invalid", str(exc), 2)
    proc = subprocess.run(
        ["git", "-C", resolved, "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RunnerError("root_not_git", "Kimiflow terminal runs require a Git repository", 2)
    canonical = proc.stdout.strip()
    if not canonical:
        raise RunnerError("root_not_git", "Git did not return a repository root", 2)
    return os.path.realpath(canonical)


def _active_status(root):
    try:
        status = active_run.status_json(root)
    except (active_run.ActiveError, OSError, ValueError) as exc:
        raise RunnerError("active_state_invalid", "cannot inspect active Kimiflow state: %s" % exc, 2)
    if status.get("status") == "invalid":
        raise RunnerError("active_state_invalid", "active Kimiflow state is invalid", 2)
    return status


def _safe_outcome_bytes(root, relative):
    parts = relative.replace(os.sep, "/").split("/")
    if len(parts) != 3 or parts[0] != ".kimiflow" or parts[2] != "SESSION-OUTCOME.json":
        return None
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    base_descriptor = None
    run_descriptor = None
    file_descriptor = None
    try:
        base_descriptor = os.open(os.path.join(root, ".kimiflow"), flags)
        run_descriptor = os.open(parts[1], flags, dir_fd=base_descriptor)
        file_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        file_descriptor = os.open(parts[2], file_flags, dir_fd=run_descriptor)
        info = os.fstat(file_descriptor)
        named = os.stat(parts[2], dir_fd=run_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size > MAX_RECEIPT_BYTES
            or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
        ):
            return None
        payload = os.read(file_descriptor, MAX_RECEIPT_BYTES + 1)
        final = os.fstat(file_descriptor)
        if len(payload) > MAX_RECEIPT_BYTES or (info.st_size, info.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            return None
        return payload, final
    except OSError:
        return None
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        if run_descriptor is not None:
            os.close(run_descriptor)
        if base_descriptor is not None:
            os.close(base_descriptor)


def _outcome_fingerprints(root):
    base = os.path.join(root, ".kimiflow")
    values = {}
    if not os.path.isdir(base) or os.path.islink(base):
        return values
    try:
        entries = list(os.scandir(base))
    except OSError:
        return values
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        relative = ".kimiflow/%s/SESSION-OUTCOME.json" % entry.name
        safe = _safe_outcome_bytes(root, relative)
        if safe is None:
            continue
        payload, info = safe
        values[relative] = (info.st_mtime_ns, info.st_size, hashlib.sha256(payload).hexdigest())
    return values


def _read_changed_outcome(root, baseline, run_hint=None):
    current = _outcome_fingerprints(root)
    candidates = [path for path, fingerprint in current.items() if baseline.get(path) != fingerprint]
    hinted = os.path.join(run_hint, "SESSION-OUTCOME.json") if run_hint else ""
    candidates.sort(key=lambda path: (path != hinted, path))
    for relative in candidates:
        try:
            safe = _safe_outcome_bytes(root, relative)
            if safe is None:
                continue
            value = json.loads(safe[0].decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs)
        except (UnicodeError, ValueError):
            continue
        outcome = value.get("outcome") if isinstance(value, dict) else None
        if outcome in TERMINAL_OUTCOMES:
            run = os.path.dirname(relative).replace(os.sep, "/")
            return {"status": outcome, "run": run, "reason": value.get("reason")}
    return None


def _initial_prompt(task):
    return (
        "Use $kimiflow for the request below. Run it autonomously through its mechanical finish. "
        "Do not ask for routine continuation or confirmation; pause only for a material decision through "
        "Kimiflow's typed wait/park contract.\n\nRequest:\n" + task.strip()
    )


def _continuation_prompt(status):
    transition = status.get("transition") if isinstance(status.get("transition"), dict) else {}
    parts = []
    for key in ("action", "target_node", "reason"):
        value = transition.get(key)
        if isinstance(value, str) and value:
            parts.append("%s=%s" % (key, value[:240]))
    execution = transition.get("execution") if isinstance(transition.get("execution"), dict) else {}
    for key in ("profile", "profile_reason", "strategy_mode", "budget_pressure", "directive"):
        value = execution.get(key)
        if isinstance(value, str) and value:
            parts.append("%s=%s" % (key, value[:80]))
    if not parts:
        action = status.get("next_action")
        if isinstance(action, str) and action:
            parts.append("action=%s" % action[:240])
    exact = ", ".join(parts) or "continue_current_phase"
    return (
        "Continue the active Kimiflow run autonomously. Exact next action: %s. "
        "Do not stop for routine confirmation. Use a typed material wait only for a real user decision; "
        "otherwise complete the work and close the active run mechanically." % exact
    )


def _parked_resume_prompt(run, message):
    slug = os.path.basename(run.rstrip("/"))
    return "$kimiflow --resume %s\n\nUser decision/input: %s" % (slug, message.strip())


def _interrupted_resume_prompt():
    return (
        "Continue the explicit Kimiflow task already present in this Codex thread. If interruption happened "
        "before its active run was created, start $kimiflow for the original request now; otherwise continue "
        "the existing run autonomously. Do not ask for routine confirmation."
    )


class CodexExecAdapter:
    def __init__(self, codex="codex", environ=None, stderr=None):
        self.codex = codex
        self.environ = environ
        self.stderr = stderr or sys.stderr

    def child_environment(self, source=None):
        env = dict(os.environ if source is None else source)
        for key in ("CODEX_THREAD_ID", "KIMIFLOW_SESSION_ID", "KIMIFLOW_SESSION_HOST"):
            env.pop(key, None)
        env["KIMIFLOW_HOST"] = "codex"
        return env

    def start_argv(self, root, prompt):
        return [
            self.codex,
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "-C",
            root,
            "-c",
            'approval_policy="never"',
            "--",
            prompt,
        ]

    def resume_argv(self, thread_id, prompt):
        return [
            self.codex,
            "exec",
            "resume",
            "--json",
            "-c",
            'approval_policy="never"',
            "-c",
            'sandbox_mode="workspace-write"',
            "--",
            thread_id,
            prompt,
        ]

    def _invoke(self, argv, root, on_thread):
        try:
            process = subprocess.Popen(
                argv,
                cwd=root,
                env=self.child_environment(self.environ),
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            return TurnResult(returncode=127, error_code="spawn_failed:%s" % exc.__class__.__name__)
        thread_id = ""
        failed_event = ""
        try:
            for raw in process.stdout:
                try:
                    event = json.loads(raw)
                except (TypeError, ValueError):
                    failed_event = "invalid_jsonl"
                    continue
                if not isinstance(event, dict):
                    failed_event = "invalid_event"
                    continue
                if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                    thread_id = event["thread_id"]
                    on_thread(thread_id)
                elif event.get("type") in ("turn.failed", "error"):
                    failed_event = str(event.get("type"))
                elif event.get("type") == "item.completed":
                    item = event.get("item")
                    if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                        self.stderr.write(item["text"].rstrip() + "\n")
            returncode = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        if returncode == 0 and failed_event:
            returncode = 1
        return TurnResult(returncode=returncode, thread_id=thread_id, error_code=failed_event)

    def start(self, root, prompt, on_thread):
        self.stderr.write("kimiflow: starting Codex headless turn\n")
        return self._invoke(self.start_argv(root, prompt), root, on_thread)

    def resume(self, root, thread_id, prompt, on_thread):
        self.stderr.write("kimiflow: continuing Codex thread %s\n" % thread_id)
        return self._invoke(self.resume_argv(thread_id, prompt), root, on_thread)


def _new_receipt(root, thread_id):
    now = iso_now()
    return {
        "schema_version": 1,
        "host": "codex",
        "root": root,
        "thread_id": thread_id,
        "status": "running",
        "turns": 0,
        "active_run": None,
        "started_at": now,
        "updated_at": now,
    }


def _update_receipt(root, receipt, status=None, **updates):
    value = dict(receipt)
    if status:
        value["status"] = status
    value.update(updates)
    value["updated_at"] = iso_now()
    write_receipt(root, value)
    return value


def _public_result(receipt, status=None, outcome=None, wait=None):
    result = {
        "schema_version": 1,
        "status": status or receipt.get("status"),
        "host": "codex",
        "root": receipt.get("root"),
        "thread_id": receipt.get("thread_id"),
        "active_run": receipt.get("active_run"),
        "turns": receipt.get("turns", 0),
    }
    if isinstance(outcome, dict) and outcome.get("reason"):
        result["reason"] = outcome["reason"]
    if isinstance(wait, dict) and wait.get("awaiting_kind"):
        result["awaiting_kind"] = wait["awaiting_kind"]
    return result


def _owner_matches(status, thread_id):
    owner = status.get("owner")
    return isinstance(owner, dict) and owner.get("host") == "codex" and owner.get("session_id") == thread_id


def _record_interruption(root, receipt):
    if receipt is None:
        return {"schema_version": 1, "status": "interrupted", "host": "codex", "root": root, "turns": 0}
    receipt = _update_receipt(root, receipt, "interrupted")
    return _public_result(receipt)


def _drive(root, adapter, receipt, turn, baseline):
    retries = 0
    while True:
        receipt = _update_receipt(root, receipt, turns=receipt["turns"] + 1)
        while turn.returncode != 0:
            if retries >= TRANSPORT_RETRIES:
                receipt = _update_receipt(root, receipt, "transport_error", error_code=turn.error_code or "codex_exit_%s" % turn.returncode)
                raise RunnerError("transport_error", "Codex transport failed after automatic retries", 1)
            retries += 1
            try:
                turn = adapter.resume(
                    root,
                    receipt["thread_id"],
                    "Recover the explicit Kimiflow task after a transport/tool failure. If no active run exists yet, start $kimiflow for the original request; otherwise choose another safe in-scope strategy and continue autonomously.",
                    lambda value: _ensure_same_thread(value, receipt["thread_id"]),
                )
            except KeyboardInterrupt:
                return _record_interruption(root, receipt)
            receipt = _update_receipt(root, receipt, turns=receipt["turns"] + 1)
        retries = 0
        status = _active_status(root)
        if status.get("present") is True and status.get("terminal") is False:
            if not _owner_matches(status, receipt["thread_id"]):
                receipt = _update_receipt(root, receipt, "ownership_conflict")
                raise RunnerError("ownership_conflict", "active Kimiflow run is not owned by this Codex thread", 1)
            receipt = _update_receipt(root, receipt, active_run=status.get("run"))
            if status.get("awaiting_user") is True:
                receipt = _update_receipt(root, receipt, "awaiting_user")
                return _public_result(receipt, wait=status)
            try:
                turn = adapter.resume(
                    root,
                    receipt["thread_id"],
                    _continuation_prompt(status),
                    lambda value: _ensure_same_thread(value, receipt["thread_id"]),
                )
            except KeyboardInterrupt:
                return _record_interruption(root, receipt)
            continue
        outcome = _read_changed_outcome(root, baseline, receipt.get("active_run"))
        if outcome:
            receipt = _update_receipt(root, receipt, outcome["status"], active_run=outcome["run"])
            return _public_result(receipt, outcome=outcome)
        receipt = _update_receipt(root, receipt, "no_kimiflow_run")
        raise RunnerError("no_kimiflow_run", "Codex completed without creating or finishing a Kimiflow run", 1)


def _ensure_same_thread(value, expected):
    if value and value != expected:
        raise RunnerError("thread_mismatch", "Codex resumed a different thread", 1)


def run_task(root, task, adapter=None):
    root = _resolve_project_root(root)
    if not isinstance(task, str) or not task.strip():
        raise RunnerError("task_missing", "run requires a non-empty task", 2)
    current = _active_status(root)
    if current.get("present") is True:
        raise RunnerError("active_run_exists", "an active Kimiflow run already exists; use its owning session or terminal resume", 1)
    baseline = _outcome_fingerprints(root)
    adapter = adapter or CodexExecAdapter()
    holder = {"receipt": None}

    def capture(thread_id):
        if holder["receipt"] is not None:
            _ensure_same_thread(thread_id, holder["receipt"]["thread_id"])
            return
        holder["receipt"] = _new_receipt(root, thread_id)
        write_receipt(root, holder["receipt"])

    try:
        turn = adapter.start(root, _initial_prompt(task), capture)
    except KeyboardInterrupt:
        return _record_interruption(root, holder["receipt"])
    if holder["receipt"] is None and turn.thread_id:
        capture(turn.thread_id)
    if holder["receipt"] is None:
        raise RunnerError("thread_missing", "Codex did not emit a resumable thread ID", 1)
    return _drive(root, adapter, holder["receipt"], turn, baseline)


def resume_task(root, message=None, adapter=None):
    root = _resolve_project_root(root)
    receipt = load_receipt(root)
    if receipt.get("status") not in RESUMABLE_STATES:
        raise RunnerError("not_resumable", "terminal run is not resumable", 1)
    current = _active_status(root)
    waiting = current.get("awaiting_user") is True or receipt.get("status") == "parked"
    if waiting and (not isinstance(message, str) or not message.strip()):
        raise RunnerError("message_required", "this material wait requires --message", 2)
    if current.get("present") is True and current.get("terminal") is False:
        if not _owner_matches(current, receipt["thread_id"]):
            raise RunnerError("ownership_conflict", "active Kimiflow run belongs to another session", 1)
        if receipt.get("active_run") and current.get("run") != receipt.get("active_run"):
            raise RunnerError("receipt_mismatch", "receipt and active run do not match", 1)
        prompt = message.strip() if waiting else _continuation_prompt(current)
    elif receipt.get("status") == "parked":
        prompt = _parked_resume_prompt(receipt.get("active_run") or "", message)
    elif receipt.get("status") in {"running", "interrupted", "transport_error"}:
        prompt = _interrupted_resume_prompt()
    else:
        raise RunnerError("active_run_missing", "resumable receipt has no matching active run", 1)
    baseline = _outcome_fingerprints(root)
    adapter = adapter or CodexExecAdapter()
    receipt = _update_receipt(root, receipt, "running")
    try:
        turn = adapter.resume(
            root,
            receipt["thread_id"],
            prompt,
            lambda value: _ensure_same_thread(value, receipt["thread_id"]),
        )
    except KeyboardInterrupt:
        return _record_interruption(root, receipt)
    return _drive(root, adapter, receipt, turn, baseline)


def runner_status(root):
    root = _resolve_project_root(root)
    receipt = load_receipt(root, required=False)
    active = _active_status(root)
    status = receipt.get("status") if receipt else ("embedded_active" if active.get("present") else "idle")
    return {"schema_version": 1, "status": status, "runner": receipt, "active_run": active}


def exit_code(result):
    status = result.get("status") if isinstance(result, dict) else "error"
    if status in ("done", "idle", "embedded_active"):
        return 0
    if status in RESUMABLE_WAIT_STATES:
        return 3
    if status == "interrupted":
        return 130
    return 1


def _parser():
    parser = argparse.ArgumentParser(prog="kimiflow", description="Optional embedded-first Kimiflow terminal runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="start and autonomously drive an explicit Kimiflow task")
    run.add_argument("task", nargs="?")
    run.add_argument("--prompt", dest="prompt")
    run.add_argument("--root")
    run.add_argument("--pretty", action="store_true")
    resume = subparsers.add_parser("resume", help="resume an interrupted or material-wait terminal run")
    resume.add_argument("--message")
    resume.add_argument("--root")
    resume.add_argument("--pretty", action="store_true")
    status = subparsers.add_parser("status", help="show runner and shared active-run status")
    status.add_argument("--root")
    status.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            if args.task and args.prompt:
                raise RunnerError("task_ambiguous", "use either the positional task or --prompt", 2)
            result = run_task(args.root, args.prompt or args.task or "")
        elif args.command == "resume":
            result = resume_task(args.root, message=args.message)
        else:
            result = runner_status(args.root)
        code = exit_code(result)
    except RunnerError as exc:
        result = {"schema_version": 1, "status": exc.status, "error": exc.message}
        code = exc.code
    output = json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":"))
    sys.stdout.write(output + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
