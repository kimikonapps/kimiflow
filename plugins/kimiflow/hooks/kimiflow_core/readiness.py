"""Versioned readiness projection over existing Kimiflow authorities."""

import hashlib
import json
import os
import re
import subprocess

from . import active_run, phase_reads, state


GATE_SPECS = {
    "clarify": ("clarify-gate.sh", "CLARIFY_GATE", {"OPEN", "CLOSED"}),
    "discovery": ("discovery-gate.sh", "DISCOVERY_GATE", {"OPEN", "CLOSED"}),
    "plan": ("plan-blocker-gate.sh", "PLAN_BLOCKER_GATE", {"OPEN", "CLOSED"}),
    "conformance": ("conformance-gate.sh", "CONFORMANCE_GATE", {"OPEN", "CLOSED"}),
    "red_green": ("red-green-gate.sh", "RED_GREEN_GATE", {"OPEN", "CLOSED"}),
    "frontend": ("frontend-quality-gate.sh", "FRONTEND_QUALITY_GATE", {"OPEN", "CLOSED"}),
}


class ReadinessError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fingerprint(value):
    return "sha256:%s" % hashlib.sha256(_canonical(value)).hexdigest()


def _phase_status(run_dir, phase):
    return state.state_value(os.path.join(run_dir, "STATE.md"), "Phase %s" % phase).strip().lower().split(" ", 1)[0]


def current_phase(run_dir):
    for phase in range(8):
        if _phase_status(run_dir, phase) != "done":
            return phase
    return 7


def _row(gate, status, reason, detail="", source="", blockers=None):
    details = [item for item in str(detail).split(",") if item]
    if status == "open":
        blocker_count = 0
    elif isinstance(blockers, int) and not isinstance(blockers, bool) and blockers > 0:
        blocker_count = blockers
    else:
        blocker_count = max(1, len(details))
    return {
        "gate": gate,
        "status": status,
        "blockers": blocker_count,
        "reason": str(reason or ""),
        "detail": details,
        "source_contract": source,
    }


def _parse_gate(name, output, returncode):
    _, expected, allowed = GATE_SPECS[name]
    lines = [line for line in output.splitlines() if line.strip()]
    if returncode != 0 or len(lines) != 1:
        return _row(name, "invalid", "gate_process_invalid", "exit_%s" % returncode, expected)
    fields = lines[0].split("\t")
    if len(fields) != 5 or fields[0] != expected or fields[1] not in allowed:
        return _row(name, "invalid", "gate_output_malformed", "unexpected_shape", expected)
    parsed = {}
    for prefix, field in zip(("blockers=", "reason=", "detail="), fields[2:]):
        if not field.startswith(prefix):
            return _row(name, "invalid", "gate_output_malformed", "unexpected_field", expected)
        parsed[prefix[:-1]] = field[len(prefix) :]
    if re.fullmatch(r"[0-9]+", parsed["blockers"]) is None:
        return _row(name, "invalid", "gate_output_malformed", "blockers_invalid", expected)
    status = "open" if fields[1] == "OPEN" and parsed["blockers"] == "0" else "closed"
    if fields[1] == "CLOSED" and int(parsed["blockers"]) < 1:
        status = "invalid"
    return _row(name, status, parsed["reason"], parsed["detail"], expected, int(parsed["blockers"]))


def _run_gate(name, run_dir, mode):
    script, _, _ = GATE_SPECS[name]
    path = os.path.join(phase_reads.plugin_root(), "hooks", script)
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        return _row(name, "invalid", "gate_missing", script, GATE_SPECS[name][1])
    args = [path, run_dir]
    if name == "conformance":
        if _phase_status(run_dir, 7) == "done":
            args.append("--finish")
        elif _phase_status(run_dir, 6) != "done":
            args.append("--plan")
    elif name == "red_green":
        args.extend(["--mode", mode])
    try:
        proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    except OSError as exc:
        return _row(name, "invalid", "gate_process_invalid", exc.__class__.__name__, GATE_SPECS[name][1])
    return _parse_gate(name, proc.stdout, proc.returncode)


def build(root, event=""):
    root = os.path.abspath(root)
    active = active_run.status_json(root, event=event)
    if active.get("present") is not True:
        base = {"schema_version": 1, "readiness": "terminal", "terminal": True, "next_action": "none", "gates": []}
        base["readiness_fingerprint"] = _fingerprint(base)
        return base
    if active.get("status") == "invalid":
        base = {
            "schema_version": 1,
            "readiness": "blocked",
            "terminal": False,
            "current_phase": None,
            "current_node": None,
            "next_action": "repair_active_run",
            "gates": [_row("active_run", "invalid", "active_run_malformed", "contract_invalid", "ACTIVE_RUN")],
            "blocker_count": 1,
            "active": {"status": "invalid"},
        }
        base["readiness_fingerprint"] = _fingerprint(base)
        return base
    run_dir = active_run.resolve_run_dir(root, active.get("run", ""))
    phase = current_phase(run_dir)
    mode = str(active.get("mode") or state.state_value(os.path.join(run_dir, "STATE.md"), "Mode")).strip().lower()
    rows = []
    phase_verdict = phase_reads.gate(root, run_dir, phase, active=active_run.load_active(root))
    rows.append(_row(
        "phase_reads",
        "open" if phase_verdict.get("status") == "OPEN" else "closed",
        phase_verdict.get("reason"),
        phase_verdict.get("detail"),
        "PHASE_READ_GATE",
        phase_verdict.get("blockers"),
    ))
    if active.get("stale_risk") != "current":
        rows.append(_row("freshness", "closed", active.get("stale", {}).get("reason", "stale"), active.get("stale_risk", "unknown"), "ACTIVE_RUN"))
    else:
        rows.append(_row("freshness", "open", "current", "", "ACTIVE_RUN"))
    transition = active.get("transition") if isinstance(active.get("transition"), dict) else {}
    graph_status = transition.get("graph_status", "legacy")
    rows.append(_row("graph", "invalid" if str(graph_status).startswith("invalid") else "open", graph_status, transition.get("reason", ""), "FLOW_GRAPH"))
    if _phase_status(run_dir, 1) == "done":
        rows.append(_run_gate("clarify", run_dir, mode))
    if _phase_status(run_dir, 2) == "done":
        rows.append(_run_gate("discovery", run_dir, mode))
    if _phase_status(run_dir, 3) == "done":
        rows.append(_run_gate("plan", run_dir, mode))
    if _phase_status(run_dir, 6) == "done":
        rows.append(_run_gate("conformance", run_dir, mode))
        if mode == "fix":
            rows.append(_run_gate("red_green", run_dir, mode))
        frontend = state.state_value(os.path.join(run_dir, "STATE.md"), "Frontend quality contract").strip()
        if frontend == "1":
            rows.append(_run_gate("frontend", run_dir, mode))
    if active.get("terminal") is True:
        readiness_status = "terminal"
    elif active.get("awaiting_user") is True:
        readiness_status = "waiting"
    elif any(row["status"] in ("closed", "invalid") for row in rows):
        readiness_status = "blocked"
    else:
        readiness_status = "ready"
    stable_active = {
        "status": active.get("status"),
        "run": active.get("run"),
        "mode": active.get("mode"),
        "scope": active.get("scope"),
        "started_head": active.get("started_head"),
        "last_checked_head": active.get("last_checked_head"),
        "item_counts": active.get("item_counts"),
        "items_fingerprint": _fingerprint(active_run.read_items(active_run.items_path(run_dir))),
        "stale_risk": active.get("stale_risk"),
        "awaiting_user": active.get("awaiting_user") is True,
        "transition": {key: transition.get(key) for key in ("graph_status", "current_node", "action", "target_node", "reason")},
    }
    base = {
        "schema_version": 1,
        "readiness": readiness_status,
        "terminal": active.get("terminal") is True,
        "current_phase": phase,
        "current_node": transition.get("current_node"),
        "next_action": transition.get("action") or active.get("next_action"),
        "gates": rows,
        "blocker_count": sum(row["blockers"] for row in rows if row["status"] != "open"),
        "active": stable_active,
    }
    base["readiness_fingerprint"] = _fingerprint(base)
    return base
