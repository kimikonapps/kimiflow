"""Mechanical, privacy-bounded divergence and convergence for Phase 2."""

import argparse
import hashlib
import json
import os
import sys
import tempfile

from . import model_adapter


BASE_LENSES = ("minimal-evolutionary", "assumption-challenge")
THIRD_LENS_BY_DECISION = {
    "architecture": "operations",
    "integration": "security",
    "scale": "operations",
    "ux_concept": "domain-transfer",
    "fuzzy_diagnosis": "operations",
}
BRIEF_KEYS = ("intent", "non_goals", "project_facts", "invariants", "evidence_ids")
CHECK_KEYS = ("intent", "invariant", "privacy", "permissions")
SELECTOR_PRIMARY_AXES = (
    "project_fit", "evidence", "simplicity", "reversibility",
    "operations_security", "cost",
)
SELECTOR_SCORE_KEYS = SELECTOR_PRIMARY_AXES + ("novelty",)
SUPPORTED_DECISIONS = (
    "architecture", "integration", "scale", "ux_concept", "fuzzy_diagnosis",
)
QUALITY_METRICS = (
    "intent_fidelity", "first_plan_gate_opening", "architecture_rollback_count",
    "later_material_review_count",
)
PROMOTION_CEILINGS = {
    "token_ratio": 1.25,
    "round_delta": 1,
    "time_ratio": 1.50,
}
ZERO_USAGE = {key: 0 for key in model_adapter.USAGE_KEYS}


class SolutionSearchError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class FreshAdapterExecutor:
    """Run each sealed unit through a newly constructed native adapter."""

    def __init__(self, adapter_factory, response_key):
        if not callable(adapter_factory) or response_key not in ("candidate", "selection"):
            raise SolutionSearchError("executor_invalid")
        self.adapter_factory = adapter_factory
        self.response_key = response_key

    def info(self):
        return self.adapter_factory().info()

    def execute(self, envelope, root, policy, resume):
        expected_kind = (
            "solution_candidate"
            if self.response_key == "candidate"
            else "solution_selector"
        )
        if resume is not False or envelope.get("kind") != expected_kind:
            raise SolutionSearchError("isolation_unavailable")
        adapter = self.adapter_factory()
        sessions = []
        result = adapter.start(
            root,
            _canonical(envelope),
            sessions.append,
            work_unit_policy=policy,
        )
        if result.returncode != 0:
            return {
                "status": "failed",
                "usage": result.usage,
                "error_code": result.error_code,
            }
        output = result.output
        messages = output.get("messages") if isinstance(output, dict) else None
        if (
            not isinstance(messages, list)
            or len(messages) != 1
            or not isinstance(messages[0], str)
            or not messages[0].strip()
        ):
            return {"status": "failed", "usage": result.usage}
        try:
            response = json.loads(messages[0])
        except (TypeError, ValueError):
            return {"status": "failed", "usage": result.usage}
        if not isinstance(response, dict) or set(response) != {self.response_key}:
            return {"status": "failed", "usage": result.usage}
        return {
            "status": "completed",
            "usage": result.usage,
            self.response_key: response[self.response_key],
        }


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError):
        raise SolutionSearchError("input_invalid")


def digest(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def classify(facts):
    """Apply the complete deterministic off/bounded truth table."""
    if not isinstance(facts, dict):
        raise SolutionSearchError("classification_invalid")
    allowed = {
        "materially_open", "clear", "canonical", "known_cause", "small_reversible",
        "decision_kind",
    }
    if set(facts) - allowed or any(
        key != "decision_kind" and not isinstance(value, bool)
        for key, value in facts.items()
    ):
        raise SolutionSearchError("classification_invalid")
    kind = facts.get("decision_kind")
    if kind is not None and kind not in SUPPORTED_DECISIONS:
        raise SolutionSearchError("classification_invalid")
    materially_open = facts.get("materially_open", False)
    off_reasons = [
        reason for reason in ("clear", "canonical", "known_cause", "small_reversible")
        if facts.get(reason) is True
    ]
    if materially_open and (off_reasons or kind is None):
        raise SolutionSearchError("classification_conflict")
    if not materially_open and kind is not None:
        raise SolutionSearchError("classification_conflict")
    if materially_open:
        return {
            "schema_version": 1,
            "solution_search": "bounded",
            "reason": kind,
        }
    reason = off_reasons[0] if off_reasons else "clear"
    return {"schema_version": 1, "solution_search": "off", "reason": reason}


def seal_brief(value):
    if not isinstance(value, dict) or set(value) != set(BRIEF_KEYS):
        raise SolutionSearchError("brief_invalid")
    if not isinstance(value.get("intent"), str) or not value["intent"].strip():
        raise SolutionSearchError("brief_invalid")
    sealed = {"intent": value["intent"].strip()}
    for key in BRIEF_KEYS[1:]:
        items = value.get(key)
        if (
            not isinstance(items, list)
            or len(items) > 64
            or any(not isinstance(item, str) or not item.strip() for item in items)
        ):
            raise SolutionSearchError("brief_invalid")
        sealed[key] = [item.strip() for item in items]
    if len(_canonical(sealed).encode("utf-8")) > 32 * 1024:
        raise SolutionSearchError("brief_too_large")
    return sealed


def _usage(value):
    normalized = model_adapter.normalize_usage(value)
    if normalized is None or set(value or {}) != set(model_adapter.USAGE_KEYS):
        return None
    return normalized


def _add(left, right):
    return {key: left[key] + right[key] for key in model_adapter.USAGE_KEYS}


def _exceeds(actual, limit):
    return any(actual[key] > limit[key] for key in model_adapter.USAGE_KEYS)


def _fits_with_reserve(actual, reserve, limit):
    return not _exceeds(_add(actual, reserve), limit)


def lenses_for(decision_kind):
    if decision_kind not in SUPPORTED_DECISIONS:
        raise SolutionSearchError("classification_invalid")
    return BASE_LENSES + (THIRD_LENS_BY_DECISION[decision_kind],)


def _compact_candidate_text(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip().encode("utf-8")) > 2 * 1024
        or "```" in value
    ):
        raise SolutionSearchError("compliance_rejected")
    return value.strip()


def _attest(executor):
    if hasattr(executor, "info"):
        try:
            info = model_adapter.info_for(executor)
        except (model_adapter.AdapterError, OSError, ValueError):
            raise SolutionSearchError("isolation_unavailable")
        if info.get("features", {}).get("work_unit_policy") is not True:
            raise SolutionSearchError("isolation_unavailable")
    elif getattr(executor, "enforces_work_unit_policy", False) is not True:
        raise SolutionSearchError("isolation_unavailable")


def _outside(path, roots):
    for root in roots:
        if not root:
            continue
        try:
            if os.path.commonpath((os.path.realpath(path), os.path.realpath(root))) == os.path.realpath(root):
                return False
        except ValueError:
            continue
    return True


def _execute(executor, envelope, policy, project_root=None, vault_root=None):
    with tempfile.TemporaryDirectory(prefix="kimiflow-solution-search-") as root:
        if not _outside(root, (project_root, vault_root)) or os.listdir(root):
            raise SolutionSearchError("isolation_unavailable")
        if hasattr(executor, "execute"):
            return executor.execute(envelope, root, policy, resume=False)
        if callable(executor):
            return executor(envelope, root, policy, resume=False)
        raise SolutionSearchError("isolation_unavailable")


def _policy(kind, envelope):
    try:
        return model_adapter.validate_work_unit_policy({
            "schema_version": 1,
            "unit_kind": kind,
            "context_scope": "sealed_input",
            "filesystem_access": "none",
            "allowed_tools": [],
            "settings_sources": [],
            "mcp_servers": [],
            "hooks": False,
            "input_digest": digest(envelope),
        })
    except model_adapter.AdapterError as exc:
        raise SolutionSearchError(str(exc))


def _receipt(status, error_code, candidate_calls, selector_calls, candidate_digests, usage):
    result = {
        "schema_version": 1,
        "status": status,
        "candidate_calls": candidate_calls,
        "selector_calls": selector_calls,
        "candidate_digests": list(candidate_digests),
        "usage": dict(usage),
    }
    if error_code:
        result["error_code"] = error_code
    return result


def _failure(code, candidate_calls, selector_calls, candidate_digests, usage):
    return {
        "schema_version": 1,
        "status": "failed" if code != "user_required" else "user_required",
        "error_code": code,
        "receipt": _receipt(
            "failed" if code != "user_required" else "user_required",
            code, candidate_calls, selector_calls, candidate_digests, usage,
        ),
    }


def execute_bounded(
    brief,
    candidate_executor,
    selector_executor,
    budget,
    candidate_budget,
    selector_budget,
    decision_kind,
    project_root=None,
    vault_root=None,
    candidate_cap=3,
):
    sealed = seal_brief(brief)
    lenses = lenses_for(decision_kind)
    run_budget = _usage(budget)
    per_candidate = _usage(candidate_budget)
    selector_reserve = _usage(selector_budget)
    if (
        run_budget is None or per_candidate is None or selector_reserve is None
        or isinstance(candidate_cap, bool) or not isinstance(candidate_cap, int)
        or not 2 <= candidate_cap <= 3
    ):
        raise SolutionSearchError("budget_invalid")
    if _exceeds(selector_reserve, run_budget):
        raise SolutionSearchError("budget_exceeded")
    _attest(candidate_executor)
    _attest(selector_executor)
    brief_digest = digest(sealed)
    candidate_rows = []
    candidate_digests = []
    actual = dict(ZERO_USAGE)
    candidate_calls = 0
    selector_calls = 0
    for index, lens in enumerate(lenses[:candidate_cap], 1):
        envelope = {
            "schema_version": 1,
            "kind": "solution_candidate",
            "candidate_id": "candidate-%d" % index,
            "lens": lens,
            "brief": sealed,
            "brief_digest": brief_digest,
            "response_contract": {
                "required": [
                    "brief_digest", "approach", "advantage", "risk",
                    "falsification", "checks", "product_effect",
                ],
                "code": "forbidden",
                "max_text_bytes": 2 * 1024,
            },
        }
        try:
            candidate_calls += 1
            response = _execute(
                candidate_executor, envelope, _policy("solution_candidate", envelope),
                project_root=project_root, vault_root=vault_root,
            )
        except Exception:
            candidate_rows.clear()
            return _failure(
                "provider_failure", candidate_calls, selector_calls,
                candidate_digests, actual,
            )
        if not isinstance(response, dict):
            candidate_rows.clear()
            return _failure(
                "provider_failure", candidate_calls, selector_calls,
                candidate_digests, actual,
            )
        measured = _usage(response.get("usage"))
        if measured is None:
            candidate_rows.clear()
            return _failure(
                "budget_usage_unavailable", candidate_calls, 0,
                candidate_digests, actual,
            )
        next_actual = _add(actual, measured)
        if (
            _exceeds(measured, per_candidate)
            or _exceeds(next_actual, run_budget)
            or not _fits_with_reserve(next_actual, selector_reserve, run_budget)
        ):
            candidate_rows.clear()
            return _failure(
                "budget_exceeded", candidate_calls, 0, candidate_digests, next_actual,
            )
        if response.get("status", "completed") != "completed":
            candidate_rows.clear()
            return _failure(
                "provider_failure", candidate_calls, selector_calls,
                candidate_digests, next_actual,
            )
        candidate = response.get("candidate")
        checks = candidate.get("checks") if isinstance(candidate, dict) else None
        expected_candidate_keys = {
            "brief_digest", "approach", "advantage", "risk", "falsification",
            "checks", "product_effect",
        }
        try:
            approach = _compact_candidate_text(candidate.get("approach"))
            advantage = _compact_candidate_text(candidate.get("advantage"))
            carrying_risk = _compact_candidate_text(candidate.get("risk"))
            falsification = _compact_candidate_text(candidate.get("falsification"))
        except (AttributeError, SolutionSearchError):
            candidate_rows.clear()
            return _failure(
                "compliance_rejected", candidate_calls, 0,
                candidate_digests, next_actual,
            )
        if (
            not isinstance(candidate, dict)
            or set(candidate) != expected_candidate_keys
            or candidate.get("brief_digest") != brief_digest
            or not isinstance(checks, dict)
            or set(checks) != set(CHECK_KEYS)
            or any(not isinstance(value, bool) for value in checks.values())
            or any(value is not True for value in checks.values())
        ):
            candidate_rows.clear()
            return _failure(
                "compliance_rejected", candidate_calls, 0,
                candidate_digests, next_actual,
            )
        try:
            effect = _compact_candidate_text(candidate.get("product_effect"))
        except SolutionSearchError:
            candidate_rows.clear()
            return _failure(
                "compliance_rejected", candidate_calls, 0,
                candidate_digests, next_actual,
            )
        row = {
            "candidate_id": envelope["candidate_id"],
            "lens": lens,
            "approach": approach,
            "advantage": advantage,
            "risk": carrying_risk,
            "falsification": falsification,
            "product_effect": effect,
            "candidate_digest": digest({
                "lens": lens,
                "approach": approach,
                "advantage": advantage,
                "risk": carrying_risk,
                "falsification": falsification,
                "checks": checks,
                "product_effect": effect,
            }),
        }
        actual = next_actual
        candidate_rows.append(row)
        candidate_digests.append(row["candidate_digest"])
    effects = {
        " ".join(row["product_effect"].split()).casefold()
        for row in candidate_rows if row["product_effect"]
    }
    if len(effects) > 1:
        candidate_rows.clear()
        return _failure(
            "user_required", candidate_calls, 0, candidate_digests, actual,
        )
    selector_envelope = {
        "schema_version": 1,
        "kind": "solution_selector",
        "brief_digest": brief_digest,
        "brief": sealed,
        "candidates_digest": digest(candidate_rows),
        "candidates": [
            {
                "candidate_id": row["candidate_id"],
                "lens": row["lens"],
                "approach": row["approach"],
                "advantage": row["advantage"],
                "risk": row["risk"],
                "falsification": row["falsification"],
                "product_effect": row["product_effect"],
                "candidate_digest": row["candidate_digest"],
            }
            for row in candidate_rows
        ],
        "compliance_contract": {
            "required": list(CHECK_KEYS),
            "source": "independent_selector",
        },
        "scoring_contract": {
            "primary_axes": list(SELECTOR_PRIMARY_AXES),
            "score_range": [0, 5],
            "tie_breaker": "novelty",
            "final_tie_breaker": "declared_candidate_order",
        },
    }
    try:
        selector_calls = 1
        response = _execute(
            selector_executor, selector_envelope,
            _policy("solution_selector", selector_envelope),
            project_root=project_root, vault_root=vault_root,
        )
    except Exception:
        candidate_rows.clear()
        return _failure(
            "selector_failure", candidate_calls, selector_calls,
            candidate_digests, actual,
        )
    if not isinstance(response, dict):
        candidate_rows.clear()
        return _failure(
            "selector_failure", candidate_calls, selector_calls,
            candidate_digests, actual,
        )
    measured = _usage(response.get("usage"))
    if measured is None:
        candidate_rows.clear()
        return _failure(
            "budget_usage_unavailable", candidate_calls, selector_calls,
            candidate_digests, actual,
        )
    final_usage = _add(actual, measured)
    if _exceeds(measured, selector_reserve) or _exceeds(final_usage, run_budget):
        candidate_rows.clear()
        return _failure(
            "budget_exceeded", candidate_calls, selector_calls,
            candidate_digests, final_usage,
        )
    if response.get("status", "completed") != "completed":
        candidate_rows.clear()
        return _failure(
            "selector_failure", candidate_calls, selector_calls,
            candidate_digests, final_usage,
        )
    selection = response.get("selection")
    ids = [row["candidate_id"] for row in candidate_rows]
    scores = selection.get("scores") if isinstance(selection, dict) else None
    compliance = (
        selection.get("compliance") if isinstance(selection, dict) else None
    )
    if (
        not isinstance(selection, dict)
        or set(selection)
        != {"winner_id", "alternative_id", "scores", "compliance"}
        or selection.get("winner_id") not in ids
        or selection.get("alternative_id") not in ids
        or selection["winner_id"] == selection["alternative_id"]
        or not isinstance(scores, dict)
        or set(scores) != set(ids)
        or any(
            not isinstance(row, dict)
            or set(row) != set(SELECTOR_SCORE_KEYS)
            or any(
                isinstance(row[key], bool)
                or not isinstance(row[key], int)
                or not 0 <= row[key] <= 5
                for key in SELECTOR_SCORE_KEYS
            )
            for row in scores.values()
        )
        or not isinstance(compliance, dict)
        or set(compliance) != set(ids)
        or any(
            not isinstance(row, dict)
            or set(row) != set(CHECK_KEYS)
            or any(not isinstance(row[key], bool) for key in CHECK_KEYS)
            for row in compliance.values()
        )
    ):
        candidate_rows.clear()
        return _failure(
            "selector_failure", candidate_calls, selector_calls,
            candidate_digests, final_usage,
        )
    if any(
        value is not True
        for row in compliance.values()
        for value in row.values()
    ):
        candidate_rows.clear()
        return _failure(
            "compliance_rejected", candidate_calls, selector_calls,
            candidate_digests, final_usage,
        )
    ranked = sorted(
        ids,
        key=lambda candidate_id: (
            -sum(scores[candidate_id][key] for key in SELECTOR_PRIMARY_AXES),
            -scores[candidate_id]["novelty"],
            ids.index(candidate_id),
        ),
    )
    if (
        selection["winner_id"] != ranked[0]
        or len(ranked) > 1
        and selection["alternative_id"] != ranked[1]
    ):
        candidate_rows.clear()
        return _failure(
            "selector_failure", candidate_calls, selector_calls,
            candidate_digests, final_usage,
        )
    by_id = {row["candidate_id"]: row for row in candidate_rows}
    selected_row = by_id[selection["winner_id"]]
    alternative_row = by_id[selection["alternative_id"]]
    winner = selected_row["candidate_digest"]
    alternative = alternative_row["candidate_digest"]
    selected = {
        key: selected_row[key]
        for key in ("lens", "approach", "advantage", "risk", "falsification")
    }
    strongest_alternative = {
        key: alternative_row[key]
        for key in ("lens", "approach", "advantage", "risk", "falsification")
    }
    candidate_rows.clear()
    receipt = _receipt(
        "completed", "", candidate_calls, selector_calls, candidate_digests, final_usage,
    )
    receipt.pop("candidate_digests")
    receipt.update({
        "winner_digest": winner,
        "alternative_digest": alternative,
        "brief_digest": brief_digest,
    })
    return {
        "schema_version": 1,
        "status": "completed",
        "winner_digest": winner,
        "alternative_digest": alternative,
        "selected": selected,
        "strongest_alternative": strongest_alternative,
        "receipt": receipt,
    }


def run(facts, **bounded):
    classification = classify(facts)
    if classification["solution_search"] == "off":
        return classification
    if not bounded:
        raise SolutionSearchError("bounded_input_missing")
    bounded.setdefault("decision_kind", classification["reason"])
    return execute_bounded(**bounded)


def _adapter_factory(args):
    if args.adapter == "codex":
        return lambda: model_adapter.CodexExecAdapter()
    if args.adapter == "claude":
        return lambda: model_adapter.ClaudeCodeAdapter(model=args.model)
    if not args.adapter_command:
        raise SolutionSearchError("adapter_command_missing")
    return lambda: model_adapter.CommandAgentAdapter(
        args.adapter_command,
        model=args.model,
        required_features=["work_unit_policy"],
    )


def promotion_decision(baseline, bounded):
    required = {
        "scenario_digest", "metrics", "tokens", "rounds", "duration_ms",
    }
    for value in (baseline, bounded):
        if not isinstance(value, dict) or set(value) != required:
            raise SolutionSearchError("promotion_metrics_invalid")
        if (
            not isinstance(value["scenario_digest"], str)
            or model_adapter.DIGEST_RE.fullmatch(value["scenario_digest"]) is None
            or not isinstance(value["metrics"], dict)
            or set(value["metrics"]) != set(QUALITY_METRICS)
        ):
            raise SolutionSearchError("promotion_metrics_invalid")
        for key in ("tokens", "rounds", "duration_ms"):
            item = value[key]
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise SolutionSearchError("promotion_metrics_invalid")
        for key in QUALITY_METRICS:
            item = value["metrics"][key]
            if key in ("intent_fidelity", "first_plan_gate_opening"):
                if (
                    isinstance(item, bool)
                    or not isinstance(item, (int, float))
                    or not 0 <= item <= 1
                ):
                    raise SolutionSearchError("promotion_metrics_invalid")
            elif isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise SolutionSearchError("promotion_metrics_invalid")
    if baseline["scenario_digest"] != bounded["scenario_digest"]:
        return {"schema_version": 1, "promote": False, "reason": "scenario_mismatch"}
    better = False
    for metric in QUALITY_METRICS:
        before = baseline["metrics"][metric]
        after = bounded["metrics"][metric]
        higher_is_better = metric in ("intent_fidelity", "first_plan_gate_opening")
        if higher_is_better:
            if after < before:
                return {"schema_version": 1, "promote": False, "reason": "quality_regression"}
            better = better or after > before
        else:
            if after > before:
                return {"schema_version": 1, "promote": False, "reason": "quality_regression"}
            better = better or after < before
    if not better:
        return {"schema_version": 1, "promote": False, "reason": "no_quality_gain"}
    token_ratio = (
        float("inf") if baseline["tokens"] == 0 and bounded["tokens"] > 0
        else bounded["tokens"] / max(1, baseline["tokens"])
    )
    if token_ratio > PROMOTION_CEILINGS["token_ratio"]:
        return {"schema_version": 1, "promote": False, "reason": "token_budget_exceeded"}
    if bounded["rounds"] > baseline["rounds"] + PROMOTION_CEILINGS["round_delta"]:
        return {"schema_version": 1, "promote": False, "reason": "round_budget_exceeded"}
    time_ratio = (
        float("inf") if baseline["duration_ms"] == 0 and bounded["duration_ms"] > 0
        else bounded["duration_ms"] / max(1, baseline["duration_ms"])
    )
    if time_ratio > PROMOTION_CEILINGS["time_ratio"]:
        return {"schema_version": 1, "promote": False, "reason": "time_budget_exceeded"}
    return {
        "schema_version": 1,
        "promote": True,
        "reason": "quality_gain_within_budget",
        "ceilings": dict(PROMOTION_CEILINGS),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="solution-search")
    parser.add_argument("--classify", help="JSON facts path; stdin when '-'")
    parser.add_argument("--bounded", help="JSON bounded-run input path; stdin when '-'")
    parser.add_argument("--promotion", nargs=2, metavar=("BASELINE", "BOUNDED"))
    parser.add_argument("--adapter", choices=("codex", "claude", "command"), default="claude")
    parser.add_argument("--adapter-command")
    parser.add_argument("--model")
    parser.add_argument("--project-root")
    parser.add_argument("--vault-root")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        selected_modes = sum(
            (args.classify is not None, args.bounded is not None, args.promotion is not None)
        )
        if selected_modes != 1:
            raise SolutionSearchError("solution_search_input_invalid")
        if args.promotion:
            with open(args.promotion[0], encoding="utf-8") as handle:
                baseline = json.load(handle)
            with open(args.promotion[1], encoding="utf-8") as handle:
                bounded = json.load(handle)
            value = promotion_decision(baseline, bounded)
        elif args.bounded:
            handle = sys.stdin if args.bounded == "-" else open(args.bounded, encoding="utf-8")
            try:
                payload = json.load(handle)
            finally:
                if handle is not sys.stdin:
                    handle.close()
            required = {
                "facts", "brief", "budget", "candidate_budget", "selector_budget",
            }
            if (
                not isinstance(payload, dict)
                or not required.issubset(payload)
                or set(payload) - required - {"candidate_cap"}
            ):
                raise SolutionSearchError("bounded_input_invalid")
            classification = classify(payload["facts"])
            if classification["solution_search"] == "off":
                value = classification
            else:
                factory = _adapter_factory(args)
                value = execute_bounded(
                    brief=payload["brief"],
                    candidate_executor=FreshAdapterExecutor(factory, "candidate"),
                    selector_executor=FreshAdapterExecutor(factory, "selection"),
                    budget=payload["budget"],
                    candidate_budget=payload["candidate_budget"],
                    selector_budget=payload["selector_budget"],
                    decision_kind=classification["reason"],
                    project_root=args.project_root or os.getcwd(),
                    vault_root=args.vault_root,
                    candidate_cap=payload.get("candidate_cap", 3),
                )
        else:
            handle = sys.stdin if args.classify == "-" else open(args.classify, encoding="utf-8")
            try:
                value = classify(json.load(handle))
            finally:
                if handle is not sys.stdin:
                    handle.close()
        print(json.dumps(
            value, sort_keys=True, indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        ))
        return 0 if value.get("status", "completed") != "failed" else 1
    except (OSError, ValueError, SolutionSearchError) as exc:
        code = exc.code if isinstance(exc, SolutionSearchError) else str(exc)
        print(json.dumps({"schema_version": 1, "status": "failed", "error_code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
