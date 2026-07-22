"""Deterministic next-action routing for the bounded Kimiflow phase graph."""

import json
import os
import re

from . import state


class FlowGraphError(ValueError):
    pass


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_TERMINAL_STATUSES = {"done", "parked", "failed", "aborted"}
_GUARD_CONTRACT = (
    ("awaiting_user", "wait_for_material_decision", "current", True),
    ("stale", "revalidate_then_refresh_baseline", "current", True),
    ("recovery_plan", "recover_plan_strategy", "phase_2", False),
    ("recovery_code", "recover_build", "phase_5", False),
    ("items_rejected", "rework_rejected_items", "phase_5", False),
    ("items_pending", "build_pending_items", "phase_5", False),
    ("items_built", "verify_built_items", "phase_6", False),
)
_RECOVERY_TRANSITIONS = {
    ("phase_4", "plan_recovery", "phase_2", "recover_plan_strategy"),
    ("phase_6", "verification_failed", "phase_5", "recover_build"),
    ("phase_6", "code_gap", "phase_5", "recover_build"),
    ("phase_6", "scope_drift", "phase_5", "recover_build"),
    ("phase_6", "strategy_drift", "phase_2", "recover_plan_strategy"),
    ("phase_6", "architecture_falsified", "phase_2", "recover_plan_strategy"),
    ("phase_6", "research_stale", "phase_2", "recover_plan_strategy"),
    ("phase_7", "review_failed", "phase_5", "recover_build"),
}
_CONFORMANCE_RECOVERY_EVENTS = {
    "code_gap",
    "scope_drift",
    "strategy_drift",
    "architecture_falsified",
    "research_stale",
}
_EXECUTION_PROFILES = ["compact", "standard", "critical"]
_EXECUTION_STRATEGY_MODES = ["normal", "recovery"]
_EXECUTION_BUDGET_PRESSURES = ["normal", "soft", "hard"]
_NO_PROGRESS_ACTIONS = (
    "reassess_setup_strategy",
    "reframe_requirements",
    "broaden_evidence_strategy",
    "revise_plan_strategy",
    "change_review_strategy",
    "change_build_strategy",
    "change_verification_strategy",
    "change_commit_strategy",
)


def _plugin_root():
    configured = os.environ.get("KIMIFLOW_PLUGIN_ROOT")
    if configured:
        return os.path.abspath(configured)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _manifest_path():
    return os.path.join(_plugin_root(), "phases", "PHASES.json")


def _identifier(value, label):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise FlowGraphError("%s must be a lowercase identifier" % label)
    return value


def _phase_file(value):
    if not isinstance(value, str) or not value or os.path.isabs(value):
        raise FlowGraphError("phase file must be a relative phases/ path")
    normalized = os.path.normpath(value)
    if normalized != value or not normalized.startswith("phases%s" % os.sep):
        raise FlowGraphError("phase file must stay under phases/")
    return normalized


def _phase_entries(manifest):
    rows = manifest.get("phases") if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        raise FlowGraphError("phases list missing")
    entries = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or isinstance(row.get("id"), bool):
            raise FlowGraphError("phase row invalid")
        raw_phase = row.get("id")
        if not isinstance(raw_phase, (int, str)):
            raise FlowGraphError("phase id must be an integer")
        try:
            phase = int(str(raw_phase), 10)
        except (TypeError, ValueError):
            raise FlowGraphError("phase id must be an integer")
        if phase in seen:
            raise FlowGraphError("duplicate phase %s" % phase)
        seen.add(phase)
        entries.append(
            {
                "id": phase,
                "node": "phase_%s" % phase,
                "name": str(row.get("name", "")),
                "file": _phase_file(row.get("file")),
            }
        )
    entries.sort(key=lambda item: item["id"])
    if [entry["id"] for entry in entries] != list(range(8)):
        raise FlowGraphError("Kimiflow graph requires phases 0-7")
    return entries


def _execution_config(manifest):
    config = manifest.get("execution_control")
    if config is None:
        return None
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise FlowGraphError("execution_control schema_version must be 1")
    if config.get("profiles") != _EXECUTION_PROFILES:
        raise FlowGraphError("execution_control profiles must be compact, standard, critical")
    if config.get("strategy_modes") != _EXECUTION_STRATEGY_MODES:
        raise FlowGraphError("execution_control strategy modes must be normal, recovery")
    if config.get("budget_pressures") != _EXECUTION_BUDGET_PRESSURES:
        raise FlowGraphError("execution_control budget pressures must be normal, soft, hard")
    no_progress_limit = config.get("no_progress_limit")
    max_trace_entries = config.get("max_trace_entries")
    if (
        isinstance(no_progress_limit, bool)
        or not isinstance(no_progress_limit, int)
        or not 2 <= no_progress_limit <= 8
    ):
        raise FlowGraphError("execution_control no_progress_limit must be between 2 and 8")
    if (
        isinstance(max_trace_entries, bool)
        or not isinstance(max_trace_entries, int)
        or not 32 <= max_trace_entries <= 2048
    ):
        raise FlowGraphError("execution_control max_trace_entries must be between 32 and 2048")
    budgets = config.get("budgets")
    if not isinstance(budgets, dict) or set(budgets) != {"small", "medium", "large"}:
        raise FlowGraphError("execution_control budgets must cover small, medium, large")
    normalized_budgets = {}
    for scope in ("small", "medium", "large"):
        budget = budgets.get(scope)
        if not isinstance(budget, dict) or set(budget) != {"soft_work_units", "hard_work_units"}:
            raise FlowGraphError("execution_control budget shape invalid")
        soft = budget.get("soft_work_units")
        hard = budget.get("hard_work_units")
        if (
            isinstance(soft, bool)
            or isinstance(hard, bool)
            or not isinstance(soft, int)
            or not isinstance(hard, int)
            or soft < 1
            or hard <= soft
            or hard > 10000
        ):
            raise FlowGraphError("execution_control budget bounds invalid")
        normalized_budgets[scope] = {"soft_work_units": soft, "hard_work_units": hard}
    return {
        "schema_version": 1,
        "profiles": list(_EXECUTION_PROFILES),
        "strategy_modes": list(_EXECUTION_STRATEGY_MODES),
        "budget_pressures": list(_EXECUTION_BUDGET_PRESSURES),
        "no_progress_limit": no_progress_limit,
        "max_trace_entries": max_trace_entries,
        "budgets": normalized_budgets,
    }


def legacy_action(stale, item_counts):
    risk = (stale or {}).get("risk", "unknown")
    if risk in ("needs_revalidation", "unknown"):
        return "revalidate_then_refresh_baseline"
    if int((item_counts or {}).get("open", 0) or 0) > 0:
        return "resolve_or_accept_items"
    return "finish_or_continue"


def load_graph():
    path = _manifest_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except OSError:
        raise FlowGraphError("phase_manifest_unreadable")
    except json.JSONDecodeError:
        raise FlowGraphError("phase_manifest_malformed")
    if not isinstance(manifest, dict) or isinstance(manifest.get("schema_version"), bool):
        raise FlowGraphError("phase manifest schema_version missing")
    try:
        manifest_schema = int(manifest.get("schema_version"))
    except (TypeError, ValueError):
        raise FlowGraphError("phase manifest schema_version invalid")
    entries = _phase_entries(manifest)
    if manifest_schema < 2 and "flow" not in manifest:
        return {"graph_status": "legacy", "manifest_schema_version": manifest_schema, "phase_entries": entries}
    if manifest_schema not in (2, 3):
        raise FlowGraphError("unsupported phase manifest schema_version")

    flow = manifest.get("flow")
    if not isinstance(flow, dict) or flow.get("schema_version") != 1:
        raise FlowGraphError("flow schema_version must be 1")
    terminal = _identifier(flow.get("terminal_node"), "terminal_node")
    nodes = {entry["node"] for entry in entries} | {terminal}
    if terminal in {entry["node"] for entry in entries}:
        raise FlowGraphError("terminal_node must not be a phase node")

    guards = flow.get("guards")
    if not isinstance(guards, list):
        raise FlowGraphError("guards list missing")
    normalized_guards = []
    for guard in guards:
        if not isinstance(guard, dict) or not isinstance(guard.get("blocks_events"), bool):
            raise FlowGraphError("guard row invalid")
        condition = _identifier(guard.get("condition"), "guard condition")
        action = _identifier(guard.get("action"), "guard action")
        target = guard.get("target")
        if target != "current":
            target = _identifier(target, "guard target")
            if target not in nodes:
                raise FlowGraphError("guard target is not a graph node")
        normalized_guards.append(
            {
                "condition": condition,
                "action": action,
                "target": target,
                "blocks_events": guard["blocks_events"],
            }
        )
    actual_guards = tuple(
        (row["condition"], row["action"], row["target"], row["blocks_events"])
        for row in normalized_guards
    )
    if actual_guards != _GUARD_CONTRACT:
        raise FlowGraphError("guards must match the deterministic Kimiflow guard contract")

    transitions = flow.get("transitions")
    if not isinstance(transitions, list):
        raise FlowGraphError("transitions list missing")
    edge_index = {}
    normalized_transitions = []
    for transition in transitions:
        if not isinstance(transition, dict):
            raise FlowGraphError("transition row invalid")
        source = _identifier(transition.get("from"), "transition source")
        event = _identifier(transition.get("event"), "transition event")
        target = _identifier(transition.get("to"), "transition target")
        action = _identifier(transition.get("action"), "transition action")
        if source not in nodes or target not in nodes:
            raise FlowGraphError("transition references an unknown node")
        if source == terminal:
            raise FlowGraphError("terminal node must not have outgoing transitions")
        key = (source, event)
        if key in edge_index:
            raise FlowGraphError("ambiguous transition for %s + %s" % key)
        row = {"from": source, "event": event, "to": target, "action": action}
        edge_index[key] = row
        normalized_transitions.append(row)

    required = set(_RECOVERY_TRANSITIONS)
    for idx in range(8):
        required.add(
            (
                "phase_%s" % idx,
                "phase_done",
                "phase_%s" % (idx + 1) if idx < 7 else terminal,
                "run_phase" if idx < 7 else "finish_run",
            )
        )
    actual = {(row["from"], row["event"], row["to"], row["action"]) for row in normalized_transitions}
    if not required.issubset(actual):
        raise FlowGraphError("required Kimiflow transitions missing")
    execution_control = _execution_config(manifest)
    if execution_control is not None:
        required_no_progress = {
            ("phase_%s" % index, "no_progress", "phase_%s" % index, action)
            for index, action in enumerate(_NO_PROGRESS_ACTIONS)
        }
        if not required_no_progress.issubset(actual):
            raise FlowGraphError("execution_control no_progress transitions missing")
    return {
        "graph_status": "ready",
        "manifest_schema_version": manifest_schema,
        "schema_version": 1,
        "terminal_node": terminal,
        "phase_entries": entries,
        "guards": normalized_guards,
        "transitions": normalized_transitions,
        "edge_index": edge_index,
        "execution_control": execution_control,
    }


def _state_token(path, key):
    return state.state_value(path, key).strip().lower().split(" ", 1)[0]


def _current_phase(graph, state_path):
    values = []
    for entry in graph["phase_entries"]:
        raw = _state_token(state_path, "Phase %s" % entry["id"])
        if raw == "done" or raw.startswith("skipped"):
            kind = "done"
        elif raw in ("open", "in-progress"):
            kind = raw
        else:
            raise FlowGraphError("phase_%s_status_invalid" % entry["id"])
        values.append(kind)

    current = None
    for idx, value in enumerate(values):
        if current is None and value == "done":
            continue
        if current is None:
            current = idx
            continue
        if value != "open":
            raise FlowGraphError("phase_sequence_conflict")
    if current is None:
        return graph["phase_entries"][-1], "done", True
    return graph["phase_entries"][current], values[current], False


def _target_fields(graph, node):
    for entry in graph.get("phase_entries", []):
        if entry["node"] == node:
            return {"target_phase": entry["id"], "target_file": entry["file"]}
    return {"target_phase": None, "target_file": None}


def _payload(graph, graph_status, current_node, action, target_node, event, reason):
    result = {
        "schema_version": 1,
        "graph_status": graph_status,
        "graph_schema_version": graph.get("schema_version") if graph else None,
        "event": event or "resume",
        "current_node": current_node,
        "action": action,
        "target_node": target_node,
        "reason": reason,
    }
    current = _target_fields(graph or {}, current_node)
    target = _target_fields(graph or {}, target_node)
    result["current_phase"] = current["target_phase"]
    result["current_file"] = current["target_file"]
    result.update(target)
    return result


def _guard_matches(condition, active, stale, recovery, review_gate, counts, current_phase):
    if condition == "awaiting_user":
        return active.get("awaiting_user") is True
    if condition == "stale":
        return stale.get("risk") in ("needs_revalidation", "unknown")
    if condition == "recovery_plan":
        return recovery == "active" and review_gate == "plan"
    if condition == "recovery_code":
        return recovery == "active" and review_gate == "code"
    if condition.startswith("items_") and current_phase < 5:
        return False
    if condition == "items_rejected":
        return int(counts.get("rejected", 0) or 0) > 0
    if condition == "items_pending":
        return int(counts.get("pending", 0) or 0) > 0
    if condition == "items_built":
        return int(counts.get("built", 0) or 0) > 0
    return False


def resolve_transition(run_dir, active=None, stale=None, item_counts=None, event=""):
    active = active or {}
    stale = stale or {"risk": "unknown"}
    counts = item_counts or {}
    state_path = os.path.join(run_dir, "STATE.md")
    flow_schema = _state_token(state_path, "Flow schema")
    if not flow_schema.isdigit() or int(flow_schema) < 4:
        return _payload(
            {}, "legacy", None, legacy_action(stale, counts), None, event, "legacy_run_schema"
        )
    try:
        graph = load_graph()
    except FlowGraphError as exc:
        return _payload({}, "invalid_graph", None, "repair_transition_graph", None, event, str(exc))
    if graph.get("graph_status") == "legacy":
        return _payload(
            graph, "legacy", None, legacy_action(stale, counts), None, event, "legacy_manifest"
        )

    active_status = str(active.get("status", "active")).strip().lower()
    if active_status in _TERMINAL_STATUSES:
        return _payload(
            graph, "ready", graph["terminal_node"], "none", graph["terminal_node"], event, "terminal_run"
        )
    try:
        current, phase_status, all_done = _current_phase(graph, state_path)
    except FlowGraphError as exc:
        return _payload(graph, "invalid_state", None, "repair_state", None, event, str(exc))

    current_node = current["node"]
    recovery = _state_token(state_path, "Recovery")
    review_gate = _state_token(state_path, "Review gate")
    if recovery == "active" and review_gate not in ("plan", "code"):
        return _payload(
            graph, "invalid_state", current_node, "repair_state", current_node, event, "recovery_gate_missing"
        )

    matches = [
        guard
        for guard in graph["guards"]
        if _guard_matches(
            guard["condition"], active, stale, recovery, review_gate, counts, current["id"]
        )
    ]
    blocking = [
        guard
        for guard in matches
        if guard["blocks_events"]
        and not (guard["condition"] == "stale" and event in _CONFORMANCE_RECOVERY_EVENTS)
    ]
    selected = blocking[0] if blocking else None
    if selected is None and event:
        edge = graph["edge_index"].get((current_node, event))
        if edge is None:
            return _payload(
                graph, "invalid_state", current_node, "repair_state", current_node, event, "transition_missing"
            )
        return _payload(
            graph, "ready", current_node, edge["action"], edge["to"], event, "event:%s" % event
        )
    if selected is None and matches:
        selected = matches[0]
    if selected is not None:
        target = current_node if selected["target"] == "current" else selected["target"]
        return _payload(
            graph,
            "ready",
            current_node,
            selected["action"],
            target,
            event,
            "guard:%s" % selected["condition"],
        )
    if all_done:
        edge = graph["edge_index"][(current_node, "phase_done")]
        return _payload(
            graph, "ready", current_node, edge["action"], edge["to"], event, "all_phases_done"
        )
    return _payload(
        graph, "ready", current_node, "run_phase", current_node, event, "phase:%s" % phase_status
    )
