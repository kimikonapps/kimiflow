"""`status` subcommand: the composed project memory status. Behavioral port of the
Bash status_json (1399-1568) + cmd_status (1810-1824) @ kimiflow--v0.1.50. Composes
every summary aggregator + the provider/vault subsystem + the curation-reason list."""
import json
import os

from . import contracts, provider, recall_index, summaries, text
from .cli import die, resolve_root, usage

_REL = ".kimiflow/project"


def _budget(env_name, default):
    # Bash `${VAR:-default}` then `--argjson` (parses the value as JSON). Unset/empty ->
    # default; a non-JSON value makes both Bash (jq) and the port fail (unreachable: these
    # are numeric configs).
    return json.loads(os.environ.get(env_name) or default)


def status_json(root):
    project = root + "/.kimiflow/project"
    memory = project + "/MEMORY.md"
    learnings = project + "/LEARNINGS.jsonl"
    user_memory = project + "/USER.md"
    user_rows = project + "/USER.jsonl"
    index = project + "/MEMORY-INDEX.json"
    recall = project + "/RECALL.md"
    recall_db = project + "/RECALL.sqlite"
    run_history = project + "/RUN-HISTORY.json"
    usage_file = project + "/MEMORY-USAGE.json"
    economics_file = project + "/MEMORY-ECONOMICS.jsonl"
    provider_manifest = project + "/VAULT-PROVIDER.json"
    proposal_rows = project + "/PROPOSALS.jsonl"

    budget = _budget("KIMIFLOW_MEMORY_BUDGET", "900")
    learning_threshold = _budget("KIMIFLOW_MEMORY_CURATE_AFTER_LEARNINGS", "10")

    memory_tokens = text.word_count_file(memory)
    user_tokens = text.word_count_file(user_memory)

    memory_present = os.path.isfile(memory)
    learnings_present = os.path.isfile(learnings)
    user_memory_present = os.path.isfile(user_memory)
    user_rows_present = os.path.isfile(user_rows)
    index_present = os.path.isfile(index)
    recall_present = os.path.isfile(recall)
    recall_db_present = os.path.isfile(recall_db)
    run_history_present = os.path.isfile(run_history)
    usage_present = os.path.isfile(usage_file)
    economics_present = os.path.isfile(economics_file)
    provider_present = os.path.isfile(provider_manifest)
    proposal_rows_present = os.path.isfile(proposal_rows)
    # Bash `command -v sqlite3`; the port probes the stdlib module's FTS5 (spec 12,
    # generalizing the recall-engine row) -- both true on supported hosts.
    sqlite_available = recall_index.fts5_available()

    learning_json = summaries.read_jsonl_summary(learnings)
    user_json = summaries.read_jsonl_summary(user_rows)
    proposals_json = summaries.proposal_summary_json(proposal_rows)
    usage_json = summaries.usage_summary_json(usage_file)
    economics_json = summaries.economics_summary_json(economics_file)
    global_efficiency_json = summaries.global_efficiency_summary_json()
    lifecycle_json = summaries.learning_lifecycle_json(learnings, usage_file)
    usefulness_json = summaries.learning_usefulness_json(learnings, usage_file)
    provider_json = provider.status_json(provider_manifest)
    provider_sync_json = provider.sync_status_json(root, learnings, provider_manifest)
    vault_json = provider.vault_status_json(index, provider_manifest)

    health = provider_json.get("health", {})
    reasons = []
    if memory_tokens > budget:
        reasons.append("memory_over_budget")
    if learning_json["stale"] > 0:
        reasons.append("stale_learnings")
    if learning_json["superseded"] > 0:
        reasons.append("superseded_learnings")
    if lifecycle_json["stale_candidates"] > 0:
        reasons.append("learning_lifecycle_review_due")
    if learning_json["total"] > 0 and not index_present:
        reasons.append("memory_index_missing")
    if learning_json["total"] >= learning_threshold:
        reasons.append("many_learnings")
    if learning_json["total"] > 0 and sqlite_available and not recall_db_present:
        reasons.append("recall_index_missing")
    if proposals_json["pending"] > 0:
        reasons.append("learning_proposals_pending")
    if proposals_json["approved"] > 0:
        reasons.append("learning_proposals_approved")
    if proposals_json["needs_revalidation"] > 0:
        reasons.append("learning_proposals_need_revalidation")
    if provider_sync_json["pending_count"] > 0:
        reasons.append("provider_sync_pending")
    if provider_sync_json["status"] == "provider_detected_unconfigured" and provider_sync_json["exportable_count"] > 0:
        reasons.append("provider_detected_unconfigured")
    if health.get("status") == "auth_failed":
        reasons.append("provider_auth_failed")
    if health.get("status") == "connected_local_only" and provider_sync_json["exportable_count"] > 0:
        reasons.append("provider_auth_required")
    if economics_json.get("action_required") is True:
        reasons.append("memory_economics_waste_risk")

    visible_reasons = [r for r in reasons if r != "many_learnings"]
    silent_reasons = [r for r in reasons if r == "many_learnings"]

    present = (memory_present or learnings_present or user_memory_present or user_rows_present
               or index_present or recall_present or recall_db_present or run_history_present
               or usage_present or economics_present or provider_present or proposal_rows_present)

    return {
        "schema_version": 1,
        "present": present,
        "root": root,
        "paths": {
            "memory": _REL + "/MEMORY.md",
            "learnings": _REL + "/LEARNINGS.jsonl",
            "user_memory": _REL + "/USER.md",
            "user_profile": _REL + "/USER.jsonl",
            "proposals": _REL + "/PROPOSALS.jsonl",
            "index": _REL + "/MEMORY-INDEX.json",
            "recall": _REL + "/RECALL.md",
            "recall_index": _REL + "/RECALL.sqlite",
            "run_history": _REL + "/RUN-HISTORY.json",
            "usage": _REL + "/MEMORY-USAGE.json",
            "economics": _REL + "/MEMORY-ECONOMICS.jsonl",
            "provider": _REL + "/VAULT-PROVIDER.json",
            "provider_sync": _REL + "/VAULT-SYNC.md",
        },
        "memory": {
            "present": memory_present,
            "path": _REL + "/MEMORY.md",
            "tokens_estimate": memory_tokens,
            "budget": budget,
            "over_budget": memory_tokens > budget,
        },
        "user_profile": {
            "present": user_memory_present or user_rows_present,
            "memory_present": user_memory_present,
            "rows_present": user_rows_present,
            "path": _REL + "/USER.md",
            "rows_path": _REL + "/USER.jsonl",
            "tokens_estimate": user_tokens,
            "rows": user_json,
        },
        "learnings": dict(learning_json, present=learnings_present, path=_REL + "/LEARNINGS.jsonl"),
        "lifecycle": lifecycle_json,
        "usefulness": usefulness_json,
        "usage": dict(usage_json, present=usage_present, path=_REL + "/MEMORY-USAGE.json"),
        "economics": dict(economics_json, present=economics_present, path=_REL + "/MEMORY-ECONOMICS.jsonl"),
        "global_efficiency": global_efficiency_json,
        "proposals": proposals_json,
        "history": {
            "present": run_history_present,
            "path": _REL + "/RUN-HISTORY.json",
        },
        "recall_index": {
            "present": recall_db_present,
            "path": _REL + "/RECALL.sqlite",
            "sqlite_available": sqlite_available,
        },
        "provider": dict(provider_json,
                         present=(provider_json.get("present") or provider_present),
                         path=_REL + "/VAULT-PROVIDER.json",
                         sync=provider_sync_json),
        "vault": vault_json,
        "curation": {
            "recommended": len(visible_reasons) > 0,
            "internal_recommended": len(reasons) > 0,
            "reasons": visible_reasons,
            "silent_reasons": silent_reasons,
            "all_reasons": reasons,
        },
    }


def run(argv):
    root = ""
    pretty = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("status: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    contracts.json_print(status_json(root), pretty)
    return 0
