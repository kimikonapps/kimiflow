"""Privacy-safe multidimensional projection of terminal Kimiflow evidence."""

import json
import math
import os
import stat

from . import phase_context, workspace_preflight


class ScorecardError(ValueError):
    pass


SCORECARD_NAME = "RUN-SCORECARD.json"
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_COUNT = 1000000000000


def _reject_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _read_json(run_descriptor, name, missing_ok=True):
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = None
    try:
        named = os.stat(name, dir_fd=run_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode) or named.st_size > MAX_INPUT_BYTES:
            raise ScorecardError("unsafe_input:%s" % name)
        descriptor = os.open(name, flags, dir_fd=run_descriptor)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise ScorecardError("exchanged_input:%s" % name)
        payload = os.read(descriptor, MAX_INPUT_BYTES + 1)
        if len(payload) > MAX_INPUT_BYTES:
            raise ScorecardError("oversize_input:%s" % name)
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        if not isinstance(value, dict):
            raise ScorecardError("malformed_input:%s" % name)
        return value
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ScorecardError("missing_input:%s" % name)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, ScorecardError):
            raise
        raise ScorecardError("malformed_input:%s" % name)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _enum(value, allowed, default="inconclusive"):
    return value if isinstance(value, str) and value in allowed else default


def _count(value, allow_negative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    number = int(value)
    lower = -MAX_COUNT if allow_negative else 0
    return max(lower, min(MAX_COUNT, number))


def _evidence_bool(value):
    return value if isinstance(value, bool) else "inconclusive"


def build(root, run_dir, terminal=None, run_descriptor=None, phase=7):
    close_descriptor = False
    if run_descriptor is None:
        run_descriptor = phase_context._open_run(root, run_dir)
        close_descriptor = True
    try:
        outcome = _read_json(run_descriptor, "OUTCOME-EVALUATION.json") or {}
        trace = _read_json(run_descriptor, "EXECUTION-TRACE.json") or {}
        lifecycle = _read_json(run_descriptor, "RUN-LIFECYCLE.json") or {}
        terminal_value = _enum(terminal or outcome.get("terminal"), {"done", "parked", "failed", "aborted"})
        signals = outcome.get("signals") if isinstance(outcome.get("signals"), dict) else {}
        verification = signals.get("verification") if isinstance(signals.get("verification"), dict) else {}
        economics = outcome.get("economics") if isinstance(outcome.get("economics"), dict) else {}
        summary = trace.get("summary") if isinstance(trace.get("summary"), dict) else {}
        usage = summary.get("usage") if isinstance(summary.get("usage"), dict) else {}
        learning = lifecycle.get("learning") if isinstance(lifecycle.get("learning"), dict) else {}
        try:
            stored_shadow = _read_json(run_descriptor, phase_context.SHADOW_NAME) or {}
            stored_phase = stored_shadow.get("phase")
            shadow_phase = stored_phase if isinstance(stored_phase, int) and not isinstance(stored_phase, bool) and 0 <= stored_phase <= 7 else phase
            shadow = phase_context.load_current_shadow(
                root,
                run_dir,
                shadow_phase,
                run_descriptor=run_descriptor,
            )
        except (OSError, ValueError, phase_context.PhaseContextError):
            shadow = {"status": "inconclusive"}
        context_status = _enum(shadow.get("status"), {"current", "stale", "missing", "invalid"})
        value = {
            "schema_version": 1,
            "status": "derived" if outcome else "inconclusive",
            "derived_only": True,
            "terminal": terminal_value,
            "dimensions": {
                "outcome": {
                    "classification": _enum(outcome.get("classification"), {"verified_success", "verified_failure", "inconclusive"}),
                    "promotable": _evidence_bool(outcome.get("promotable")),
                },
                "quality": {
                    "verification": _enum(verification.get("outcome"), {"passed", "failed", "inconclusive"}),
                    "criteria": _enum(verification.get("criteria"), {"passed", "failed", "inconclusive"}),
                    "regression": _enum(verification.get("regression"), {"passed", "failed", "inconclusive"}),
                    "code_review": _enum(signals.get("code_review"), {"clean", "blocking", "advisory", "inconclusive"}),
                    "recovery": _enum(signals.get("recovery"), {"clean", "active", "inconclusive"}),
                    "learning": _enum(learning.get("status"), {"recorded", "skipped", "not_promoted", "inconclusive"}),
                },
                "efficiency": {
                    "economics_result": _enum(economics.get("result"), {"saving", "waste", "neutral", "unknown"}),
                    "economics_confidence": _enum(economics.get("confidence"), {"high", "medium", "low", "none"}),
                    "net_estimated_tokens_saved": _count(economics.get("net_estimated_tokens_saved"), allow_negative=True),
                    "work_units": _count(summary.get("work_units")),
                    "model_calls": _count(usage.get("model_calls")),
                    "tool_calls": _count(usage.get("tool_calls")),
                },
                "autonomy": {
                    "first_plan_success": _evidence_bool(signals.get("first_plan_success")),
                    "no_progress_streak": _count(summary.get("no_progress_streak")),
                    "strategy_mode": _enum(summary.get("strategy_mode"), {"normal", "recovery"}),
                    "budget_pressure": _enum(summary.get("budget_pressure"), {"normal", "soft", "hard"}),
                },
                "context": {
                    "status": context_status,
                    "selected_count": _count(shadow.get("selected_count")) if context_status == "current" else None,
                    "total_bytes": _count(shadow.get("total_bytes")) if context_status == "current" else None,
                    "estimated_tokens": _count(shadow.get("estimated_tokens")) if context_status == "current" else None,
                },
            },
            "privacy": {
                "local_only": True,
                "stores_content": False,
                "stores_paths": False,
                "stores_prompts": False,
                "stores_identifiers": False,
            },
        }
        return value
    finally:
        if close_descriptor:
            os.close(run_descriptor)


def write(root, run_dir, terminal=None, run_descriptor=None, phase=7):
    close_descriptor = False
    if run_descriptor is None:
        run_descriptor = phase_context._open_run(root, run_dir)
        close_descriptor = True
    try:
        value = build(root, run_dir, terminal=terminal, run_descriptor=run_descriptor, phase=phase)
        workspace_preflight.atomic_directory_write(
            run_descriptor,
            SCORECARD_NAME,
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n",
        )
        flags = os.O_RDONLY | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0)
        descriptor = os.open(SCORECARD_NAME, flags, dir_fd=run_descriptor)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return value
    except (OSError, ValueError, ScorecardError) as exc:
        if isinstance(exc, ScorecardError):
            raise
        raise ScorecardError("scorecard_write_failed:%s" % exc.__class__.__name__)
    finally:
        if close_descriptor:
            os.close(run_descriptor)
