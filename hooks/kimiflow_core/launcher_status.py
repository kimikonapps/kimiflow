"""Python port of hooks/launcher-status.sh."""

import json
import os
import re
import shutil
import subprocess
import sys


USAGE = """#!/usr/bin/env bash
# kimiflow — read-only launcher status snapshot. Orchestrator-invoked, not a hook.
#
# Usage:
#   launcher-status.sh [--root <path>] [--pretty] [--full]
#
# Output: JSON. This script never writes project files. The default output is the
# compact first screen: the heavy arrays (`runs.items`, `background.items`) and the
# full `memory` object are omitted (counts, `memory_summary`, `maintenance` and the
# `.launcher` block stay). `--full` emits the complete snapshot for drilldowns.
#
# R2 invariant example:
#   hooks/launcher-status.sh --pretty
"""


def usage():
    sys.stderr.write(USAGE)


def die(message, code=1):
    sys.stderr.write("launcher-status: %s\n" % message)
    return code


def need_jq():
    if not shutil.which("jq"):
        return die("jq is required", 2)
    return 0


def run_cmd(args, cwd=None, env=None):
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return None


def run_git(root, args):
    return run_cmd(["git", "-C", root] + list(args))


def json_compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def manifest_value(path, key):
    data = load_json_file(path)
    if isinstance(data, dict):
        value = data.get(key)
        if value is not None:
            return str(value)
    return ""


def resolve_root(root_arg):
    if root_arg:
        if os.path.isdir(root_arg):
            return os.path.abspath(root_arg)
        return root_arg
    proc = run_cmd(["git", "rev-parse", "--show-toplevel"])
    if proc and proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return os.getcwd()


def git_commit_ok(root, commit):
    if not commit or commit == "NOT VERIFIED":
        return False
    proc = run_git(root, ["cat-file", "-e", "%s^{commit}" % commit])
    return bool(proc and proc.returncode == 0)


def changed_paths(root, base=""):
    paths = []
    if base and git_commit_ok(root, base):
        proc = run_git(root, ["diff", "--name-only", base, "HEAD"])
        if proc and proc.stdout:
            paths.extend(proc.stdout.splitlines())
    for args in (
        ["diff", "--name-only", "--cached"],
        ["diff", "--name-only"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        proc = run_git(root, args)
        if proc and proc.stdout:
            paths.extend(proc.stdout.splitlines())
    return [path for path in paths if path]


def repo_dirty(root):
    return any(not (path == ".kimiflow" or path.startswith(".kimiflow/")) for path in changed_paths(root))


def path_in_changed_set(needle, root, base):
    return any(path == needle for path in changed_paths(root, base) if not path.startswith(".kimiflow/"))


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
        return ""
    return ""


def json_path_array_from_state(path):
    out = []
    in_list = False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n").replace("\r", "").replace("**", "")
                if re.match(r"^Affected files:[ \t]*$", line):
                    in_list = True
                    continue
                if in_list and re.match(r"^[ \t]*-[ \t]+", line):
                    out.append(re.sub(r"^[ \t]*-[ \t]+", "", line))
                    continue
                if in_list and not re.match(r"^[ \t]*$", line):
                    in_list = False
    except OSError:
        pass
    return out


def state_phase7_done(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.lower().replace("\r", "").replace("**", "").rstrip("\n")
                if re.match(r"^[\s-]*phase\s+7(\s*\([^)]*\))?\s*:\s*done(\s|$|[-.,;:])", line):
                    return True
                if re.match(r"^\s*(##\s+)?run complete(\s|$|[-.,;:()])", line):
                    return True
    except OSError:
        return False
    return False


def count_section_items(path, heading_pattern, done_marker=""):
    if not os.path.isfile(path):
        return 0
    heading_re = re.compile(heading_pattern)
    in_section = False
    have = False
    block_done = False
    count = 0

    def flush():
        nonlocal have, block_done, count
        if have and not block_done:
            count += 1
        have = False
        block_done = False

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if heading_re.search(line):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                flush()
                in_section = False
                continue
            if in_section and line.startswith("### "):
                flush()
                have = True
                block_done = False
                continue
            if in_section and have and done_marker and done_marker in line:
                block_done = True
    if in_section:
        flush()
    return count


def count_feature_check_findings(root):
    base = os.path.join(root, ".kimiflow")
    total = 0
    if not os.path.isdir(base):
        return 0
    for run in sorted(os.listdir(base)):
        file_path = os.path.join(base, run, "findings")
        if not os.path.isdir(file_path):
            continue
        for name in sorted(os.listdir(file_path)):
            if not re.match(r"r.*-feature-check\.md$", name):
                continue
            try:
                with open(os.path.join(file_path, name), "r", encoding="utf-8") as handle:
                    total += sum(1 for line in handle if re.match(r"^FINDING (BLOCKER|HIGH) ", line))
            except OSError:
                pass
    return total


def count_feature_check_runs(root):
    base = os.path.join(root, ".kimiflow")
    if not os.path.isdir(base):
        return 0
    total = 0
    for run in os.listdir(base):
        if os.path.isfile(os.path.join(base, run, "FEATURE-CHECK.md")):
            total += 1
    return total


def repo_docs_present(root):
    docs = os.path.join(root, "docs")
    if not os.path.isdir(docs):
        return False
    root_depth = docs.rstrip(os.sep).count(os.sep)
    for current, dirs, files in os.walk(docs):
        depth = current.rstrip(os.sep).count(os.sep) - root_depth
        if depth >= 2:
            dirs[:] = []
        if any(name.endswith(".md") for name in files):
            return True
    return False


def learning_review_status(root, slug, script_dir):
    run_rel = ".kimiflow/%s" % slug
    review_path = "%s/LEARNING-REVIEW.md" % run_rel
    review = os.path.join(root, review_path)
    if not os.path.isfile(review):
        return {
            "present": False,
            "path": review_path,
            "status": "missing",
            "verdict": "CLOSED",
            "reason": "missing_review",
            "freshness": None,
        }
    status = "unknown"
    try:
        with open(review, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("Status: "):
                    status = line.split(": ", 1)[1].strip()
                    break
    except OSError:
        pass

    verdict = "unknown"
    reason = ""
    freshness = ""
    router = os.path.join(script_dir, "memory-router.sh")
    if os.path.exists(router) and os.access(router, os.X_OK):
        env = os.environ.copy()
        env["KIMIFLOW_HOST"] = env.get("KIMIFLOW_HOST", "")
        proc = run_cmd([router, "verify-run", "--root", root, "--run", run_rel], env=env)
        line = proc.stdout if proc else ""
        for raw in line.splitlines():
            parts = raw.split("\t")
            if parts and parts[0] == "LEARNING_REVIEW":
                if len(parts) > 1 and parts[1]:
                    verdict = parts[1]
                for field in parts:
                    if field.startswith("reason="):
                        reason = field[len("reason=") :]
                    elif field.startswith("freshness="):
                        freshness = field[len("freshness=") :]
                break
    return {
        "present": True,
        "path": review_path,
        "status": status,
        "verdict": verdict,
        "reason": reason or None,
        "freshness": freshness or None,
    }


def default_memory_status():
    return {
        "schema_version": 1,
        "present": False,
        "memory": {"present": False, "path": ".kimiflow/project/MEMORY.md", "tokens_estimate": 0, "budget": 900, "over_budget": False},
        "learnings": {"present": False, "current": 0, "stale": 0, "superseded": 0},
        "usefulness": {
            "hot": {"count": 0, "ids": []},
            "warm": {"count": 0, "ids": []},
            "cold": {"count": 0, "ids": []},
            "stale": {"count": 0, "ids": []},
            "promote_candidates": {"count": 0, "ids": []},
            "compress_candidates": {"count": 0, "ids": []},
        },
        "curation": {"recommended": False, "internal_recommended": True, "reasons": [], "silent_reasons": [], "all_reasons": ["memory_router_unavailable"]},
        "proposals": {"pending": 0, "approved": 0, "applied": 0, "rejected": 0, "needs_revalidation": 0},
        "provider": {
            "sync": {"status": "provider_unavailable", "pending_count": 0, "direct_write_ready": False},
            "health": {"status": "not_detected"},
            "auth": {"status": "not_configured", "authenticated": False},
        },
        "global_efficiency": default_efficiency(),
    }


def default_efficiency():
    return {
        "enabled": True,
        "present": False,
        "path": "~/.kimiflow/metrics/token-economics.jsonl",
        "scope": "global_local_anonymous",
        "runs_tracked": 0,
        "projects_tracked": 0,
        "confidence": "none",
        "verdict": "no_data",
        "estimated_savings_percent": None,
        "action_required": False,
        "privacy": {
            "local_only": True,
            "stores_content": False,
            "stores_paths": False,
            "stores_repo_name": False,
            "stores_prompts": False,
            "project_id_salted_hash": True,
        },
    }


def memory_summary(memory):
    memory_block = memory.get("memory", {}) if isinstance(memory.get("memory"), dict) else {}
    learnings = memory.get("learnings", {}) if isinstance(memory.get("learnings"), dict) else {}
    usefulness = memory.get("usefulness", {}) if isinstance(memory.get("usefulness"), dict) else {}
    curation = memory.get("curation", {}) if isinstance(memory.get("curation"), dict) else {}
    provider = memory.get("provider", {}) if isinstance(memory.get("provider"), dict) else {}
    sync = provider.get("sync", {}) if isinstance(provider.get("sync"), dict) else {}
    reasons = curation.get("reasons") if isinstance(curation.get("reasons"), list) else []
    next_actions = list(reasons)
    if (sync.get("pending_count") or 0) > 0:
        next_actions.append("provider_sync_pending")
    return {
        "present": memory.get("present") is True,
        "tokens_estimate": memory_block.get("tokens_estimate", 0),
        "budget": memory_block.get("budget", 900),
        "over_budget": memory_block.get("over_budget") is True,
        "learnings": {
            "current": learnings.get("current", 0),
            "stale": learnings.get("stale", 0),
            "superseded": learnings.get("superseded", 0),
        },
        "usefulness": {
            "hot": usefulness.get("hot", {}).get("count", 0) if isinstance(usefulness.get("hot"), dict) else 0,
            "warm": usefulness.get("warm", {}).get("count", 0) if isinstance(usefulness.get("warm"), dict) else 0,
            "cold": usefulness.get("cold", {}).get("count", 0) if isinstance(usefulness.get("cold"), dict) else 0,
            "stale": usefulness.get("stale", {}).get("count", 0) if isinstance(usefulness.get("stale"), dict) else 0,
            "promote_candidates": usefulness.get("promote_candidates", {}).get("count", 0) if isinstance(usefulness.get("promote_candidates"), dict) else 0,
            "compress_candidates": usefulness.get("compress_candidates", {}).get("count", 0) if isinstance(usefulness.get("compress_candidates"), dict) else 0,
        },
        "curation": {"recommended": curation.get("recommended") is True, "reasons": reasons},
        "provider_sync": {
            "status": sync.get("status", "unknown"),
            "pending_count": sync.get("pending_count", 0),
            "direct_write_ready": sync.get("direct_write_ready") is True,
        },
        "next_actions": sorted(set(next_actions)),
    }


def default_active_session():
    return {
        "schema_version": 1,
        "present": False,
        "status": "none",
        "active_file": ".kimiflow/session/ACTIVE_RUN.json",
        "run": None,
        "item_counts": {"total": 0, "pending": 0, "built": 0, "accepted": 0, "rejected": 0, "dropped": 0, "open": 0},
        "stale_risk": "none",
        "stale": {"risk": "none", "changed_paths": [], "relevant_changed_paths": [], "reason": "active_run_unavailable"},
        "terminal": True,
    }


def default_background():
    return {"schema_version": 1, "present": False, "path": ".kimiflow/background", "total": 0, "pending": 0, "running": 0, "ready": 0, "finished": 0, "collectable": 0, "stale": 0, "failed": 0, "cancelled": 0, "items": []}


def default_agentic_readiness():
    return {
        "schema_version": 1,
        "status": "unavailable",
        "summary": "Agentic readiness: unavailable",
        "readiness": {"level": "guided", "confidence": "none", "blockers": [], "warnings": ["helper_missing"]},
        "privacy": {"stores_secrets": False, "stores_prompts": False, "local_only": True, "network_calls": False},
    }


def call_json(script, args, root=None):
    if not (os.path.exists(script) and os.access(script, os.X_OK)):
        return None
    env = os.environ.copy()
    env["KIMIFLOW_HOST"] = env.get("KIMIFLOW_HOST", "")
    proc = run_cmd([script] + args, env=env)
    if not proc or proc.returncode not in (0, 1, 2):
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def visible_memory_curation(memory):
    return (
        memory.get("memory", {}).get("over_budget") is True
        or (memory.get("learnings", {}).get("stale") or 0) > 0
        or (memory.get("proposals", {}).get("needs_revalidation") or 0) > 0
    )


def visible_maintenance_reasons(reasons, memory):
    out = []
    for reason in reasons:
        if (
            reason
            in {
                "working_tree_dirty",
                "active_session_open",
                "active_session_needs_revalidation",
                "background_handles_collectable",
                "background_handles_stale",
                "active_runs",
                "backlog_runs",
                "learning_reviews_need_attention",
                "learning_proposals_approved",
                "learning_proposals_need_revalidation",
            }
            or reason.startswith("project_map_")
            or (reason == "memory_curation_recommended" and visible_memory_curation(memory))
        ):
            out.append(reason)
    return sorted(set(out))


def hidden_internal_reasons(reasons, memory):
    out = []
    for reason in reasons:
        if (reason == "memory_curation_recommended" and not visible_memory_curation(memory)) or reason == "learning_proposals_pending":
            out.append(reason)
    curation = memory.get("curation", {}) if isinstance(memory.get("curation"), dict) else {}
    out.extend(curation.get("silent_reasons") if isinstance(curation.get("silent_reasons"), list) else [])
    sync = memory.get("provider", {}).get("sync", {}) if isinstance(memory.get("provider"), dict) else {}
    if (sync.get("pending_count") or 0) > 0 and sync.get("direct_write_ready") is not True:
        out.append("provider_sync_waiting_for_user_setup")
    return sorted(set(out))


def drilldowns(snapshot):
    out = []
    if snapshot["findings"]["open"] > 0:
        out.append("findings")
    if snapshot["feature_checks"]["verified_findings_open"] > 0:
        out.append("feature_checks")
    if snapshot["improvements"]["open"] > 0:
        out.append("improvements")
    if snapshot["runs"]["backlog"] > 0:
        out.append("backlog_runs")
    if snapshot["runs"]["active"] > 0:
        out.append("active_runs")
    if (snapshot["background"].get("collectable", 0) > 0) or (snapshot["background"].get("stale", 0) > 0):
        out.append("background")
    if snapshot["memory_summary"]["provider_sync"]["pending_count"] > 0:
        out.append("vault_sync")
    if snapshot["memory_summary"]["curation"]["recommended"] or snapshot["memory"].get("proposals", {}).get("pending", 0) > 0:
        out.append("memory")
    if snapshot["installation"]["cache_status"] == "stale_cache":
        out.append("installation")
    return sorted(set(out))


def primary_action(snapshot):
    if snapshot["repo"]["dirty"]:
        return {"id": "clean_worktree", "label_key": "clean_worktree", "priority": "blocker", "blocking": True, "reason_key": "working_tree_dirty"}
    active = snapshot["active_session"]
    if active.get("present") is True and active.get("terminal") is False and active.get("stale_risk") == "needs_revalidation":
        return {"id": "revalidate_active_session", "label_key": "revalidate_active_session", "priority": "high", "blocking": True, "reason_key": "active_session_stale"}
    if active.get("present") is True and active.get("terminal") is False:
        return {"id": "continue_active_session", "label_key": "continue_active_session", "priority": "high", "blocking": False, "reason_key": "active_session_open"}
    if snapshot["installation"]["cache_status"] == "stale_cache":
        return {"id": "update_installed_plugin", "label_key": "update_installed_plugin", "priority": "high", "blocking": False, "reason_key": "installed_cache_stale"}
    if snapshot["background"].get("collectable", 0) > 0:
        return {"id": "collect_background_handles", "label_key": "collect_background_handles", "priority": "medium", "blocking": False, "reason_key": "background_results_waiting"}
    if snapshot["project_map"]["present"] is not True:
        return {"id": "project_map_bootstrap", "label_key": "project_map_bootstrap", "priority": "recommended", "blocking": False, "reason_key": "project_map_missing"}
    if snapshot["project_map"]["valid"] is not True:
        return {"id": "project_map_rebuild", "label_key": "project_map_rebuild", "priority": "recommended", "blocking": False, "reason_key": "project_map_invalid"}
    if snapshot["project_map"]["status"] != "current":
        return {"id": "bring_current", "label_key": "bring_current", "priority": "recommended", "blocking": False, "reason_key": "project_map_%s" % snapshot["project_map"]["status"]}
    if snapshot["memory_summary"]["over_budget"]:
        return {"id": "curate_memory", "label_key": "curate_memory", "priority": "recommended", "blocking": False, "reason_key": "memory_over_budget"}
    if snapshot["feature_checks"]["verified_findings_open"] > 0:
        return {"id": "review_feature_findings", "label_key": "review_feature_findings", "priority": "medium", "blocking": False, "reason_key": "feature_check_findings_open"}
    if snapshot["findings"]["open"] > 0:
        return {"id": "review_findings", "label_key": "review_findings", "priority": "medium", "blocking": False, "reason_key": "findings_open"}
    if snapshot["improvements"]["open"] > 0:
        return {"id": "review_improvements", "label_key": "review_improvements", "priority": "medium", "blocking": False, "reason_key": "improvements_open"}
    if snapshot["runs"]["backlog"] > 0:
        return {"id": "resume_backlog", "label_key": "resume_backlog", "priority": "medium", "blocking": False, "reason_key": "backlog_runs_open"}
    if snapshot["memory_summary"]["provider_sync"]["pending_count"] > 0 and snapshot["memory_summary"]["provider_sync"]["direct_write_ready"]:
        return {"id": "sync_vault", "label_key": "sync_vault", "priority": "optional", "blocking": False, "reason_key": "vault_sync_pending"}
    return {"id": "start_kimiflow", "label_key": "start_kimiflow", "priority": "normal", "blocking": False, "reason_key": "ready"}


def append_reason(reasons, reason):
    reasons.append(reason)


def build_snapshot(root, script_dir):
    script_root = os.environ.get("KIMIFLOW_PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT") or ""
    if script_root and os.path.isdir(script_root):
        script_root = os.path.abspath(script_root)
    else:
        script_root = os.path.abspath(os.path.join(script_dir, ".."))

    plugin_version = manifest_value(os.path.join(script_root, ".codex-plugin/plugin.json"), "version") or manifest_value(os.path.join(script_root, ".claude-plugin/plugin.json"), "version") or "unknown"
    repo_plugin_version = manifest_value(os.path.join(root, ".codex-plugin/plugin.json"), "version") or manifest_value(os.path.join(root, ".claude-plugin/plugin.json"), "version") or "unknown"
    installation_mode = "source_checkout"
    if any(part in script_root for part in ("/.codex/plugins/cache/", "/.claude/plugins/cache/", "/.codex/.tmp/marketplaces/", "/.claude/.tmp/marketplaces/")):
        installation_mode = "plugin_cache"
    cache_status = installation_mode
    installation_action_required = False
    if installation_mode == "plugin_cache":
        cache_status = "current"
        if repo_plugin_version != "unknown" and plugin_version != "unknown" and plugin_version != repo_plugin_version:
            cache_status = "stale_cache"
            installation_action_required = True

    repo_present = False
    head = "NOT VERIFIED"
    dirty = False
    proc = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if proc and proc.returncode == 0:
        repo_present = True
        head_proc = run_git(root, ["rev-parse", "--short", "HEAD"])
        if head_proc and head_proc.returncode == 0 and head_proc.stdout.strip():
            head = head_proc.stdout.strip()
        dirty = repo_dirty(root)

    index_path = os.path.join(root, ".kimiflow/project/INDEX.json")
    map_present = os.path.isfile(index_path)
    map_valid = False
    map_depth = "missing"
    map_status = "missing"
    map_baseline = "NOT VERIFIED"
    commits_since_map = None
    if map_present:
        data = load_json_file(index_path)
        if data is not None:
            map_valid = True
            map_depth = data.get("scan_depth", "unknown")
            map_baseline = data.get("baseline_commit", "NOT VERIFIED")
            if git_commit_ok(root, map_baseline):
                count_proc = run_git(root, ["rev-list", "--count", "%s..HEAD" % map_baseline])
                commits_since_map = int(count_proc.stdout.strip() or "0") if count_proc and count_proc.returncode == 0 else 0
            project_script = os.path.join(script_dir, "project-map-status.sh")
            proc = run_cmd([project_script, "status", "--index", index_path], cwd=root) if os.path.exists(project_script) else None
            if proc and proc.stdout:
                for line in proc.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) > 1 and parts[0] == "PROJECT_MAP":
                        map_status = parts[1]
                        break
                else:
                    map_status = "unknown"
            else:
                map_status = "unknown"
        else:
            map_depth = "unknown"
            map_status = "unknown"

    findings_path = ".kimiflow/project/FINDINGS.md"
    improvements_path = ".kimiflow/project/IMPROVEMENTS.md"
    marker = "kimiflow:queue-done"
    findings_open = count_section_items(os.path.join(root, findings_path), r"^##\s+(Offen|Open)(\s.*)?$", marker)
    improvements_open = count_section_items(os.path.join(root, improvements_path), r"^##\s+(Priorisierte Slices|Prioritized Slices)(\s.*)?$", marker)
    feature_check_findings = count_feature_check_findings(root)
    feature_check_runs = count_feature_check_runs(root)

    runs_items = []
    active = backlog = done = other = 0
    lr_recorded = lr_skipped = lr_missing = lr_missing_done = lr_closed = lr_attention = 0
    kimiflow_dir = os.path.join(root, ".kimiflow")
    if os.path.isdir(kimiflow_dir):
        state_files = []
        for slug in os.listdir(kimiflow_dir):
            state = os.path.join(kimiflow_dir, slug, "STATE.md")
            if os.path.isfile(state):
                state_files.append((slug, state))
        for slug, state in sorted(state_files, key=lambda item: item[1]):
            if slug in ("project", "plans", "specs"):
                continue
            raw_status = state_value(state, "Status") or "active"
            if raw_status.startswith("backlog"):
                status = "backlog"
            elif raw_status.startswith("done"):
                status = "done"
            elif raw_status.startswith("active"):
                status = "active"
            else:
                status = "other"
            status_detail = raw_status
            if status == "active" and state_phase7_done(state):
                status = "done"
                status_detail = "%s (inferred: phase 7 done)" % raw_status
            mode = state_value(state, "Mode")
            scope = state_value(state, "Scope")
            plan_commit = state_value(state, "Plan commit") or "NOT VERIFIED"
            plan_status = state_value(state, "Plan status") or "unknown"
            affected = json_path_array_from_state(state)
            learning = learning_review_status(root, slug, script_dir)
            if status == "backlog":
                backlog += 1
            elif status == "done":
                done += 1
            elif status == "active":
                active += 1
            else:
                other += 1
            if learning["status"] == "recorded":
                lr_recorded += 1
            elif learning["status"] == "skipped":
                lr_skipped += 1
            elif learning["status"] == "missing":
                lr_missing += 1
            if learning["verdict"] == "CLOSED":
                lr_closed += 1
            if status == "done" and learning["status"] == "missing":
                lr_missing_done += 1
            if status == "done" and learning["status"] != "missing" and learning["verdict"] != "OPEN":
                lr_attention += 1

            stale_risk = "n/a"
            if status == "backlog":
                if not git_commit_ok(root, plan_commit) or len(affected) == 0:
                    stale_risk = "unknown"
                else:
                    stale_risk = "low"
                    for path in affected:
                        if path_in_changed_set(path, root, plan_commit):
                            stale_risk = "needs-revalidation"
                            break
            runs_items.append({
                "slug": slug,
                "status": status,
                "status_detail": status_detail,
                "mode": mode,
                "scope": scope,
                "plan_commit": plan_commit,
                "plan_status": plan_status,
                "affected_files": affected,
                "learning_review": learning,
                "stale_risk": stale_risk,
            })

    workflow_artifacts = [artifact for artifact in (".planning", ".gsd") if os.path.exists(os.path.join(root, artifact))]

    memory = default_memory_status()
    maybe = call_json(os.path.join(script_dir, "memory-router.sh"), ["status", "--root", root], root=root)
    if isinstance(maybe, dict):
        memory = maybe
    memory_sum = memory_summary(memory)

    active_session = default_active_session()
    maybe = call_json(os.path.join(script_dir, "active-run.sh"), ["status", "--root", root], root=root)
    if isinstance(maybe, dict):
        active_session = maybe

    background = default_background()
    maybe = call_json(os.path.join(script_dir, "background-run.sh"), ["list", "--root", root, "--json"], root=root)
    if isinstance(maybe, dict):
        background = maybe

    agentic = default_agentic_readiness()
    agentic_args = ["status", "--root", root]
    active_run = active_session.get("run")
    if active_run and active_run != "null":
        agentic_args.extend(["--run", active_run])
    maybe = call_json(os.path.join(script_dir, "agentic-readiness.sh"), agentic_args, root=root)
    if isinstance(maybe, dict):
        agentic = maybe

    reasons = []
    if dirty:
        append_reason(reasons, "working_tree_dirty")
    if active_session.get("present") is True and active_session.get("terminal") is False:
        append_reason(reasons, "active_session_open")
    if active_session.get("stale_risk") == "needs_revalidation":
        append_reason(reasons, "active_session_needs_revalidation")
    if background.get("collectable", 0) > 0:
        append_reason(reasons, "background_handles_collectable")
    if background.get("stale", 0) > 0:
        append_reason(reasons, "background_handles_stale")
    if map_present is not True:
        append_reason(reasons, "project_map_missing")
    elif map_valid is not True:
        append_reason(reasons, "project_map_invalid")
    elif map_status != "current":
        append_reason(reasons, "project_map_%s" % map_status)
    if active > 0:
        append_reason(reasons, "active_runs")
    if backlog > 0:
        append_reason(reasons, "backlog_runs")
    if lr_attention > 0:
        append_reason(reasons, "learning_reviews_need_attention")
    if memory.get("curation", {}).get("recommended") is True:
        append_reason(reasons, "memory_curation_recommended")
    proposals = memory.get("proposals", {}) if isinstance(memory.get("proposals"), dict) else {}
    if proposals.get("pending", 0) > 0:
        append_reason(reasons, "learning_proposals_pending")
    if proposals.get("approved", 0) > 0:
        append_reason(reasons, "learning_proposals_approved")
    if proposals.get("needs_revalidation", 0) > 0:
        append_reason(reasons, "learning_proposals_need_revalidation")

    snapshot = {
        "schema_version": 1,
        "repo": {"present": repo_present, "root": root, "head": head, "dirty": dirty},
        "installation": {"mode": installation_mode, "plugin_root": script_root, "version": plugin_version, "repo_version": repo_plugin_version, "cache_status": cache_status, "action_required": installation_action_required},
        "project_map": {"present": map_present, "valid": map_valid, "depth": map_depth, "status": map_status, "index": ".kimiflow/project/INDEX.json", "baseline_commit": map_baseline},
        "memory": memory,
        "memory_summary": memory_sum,
        "efficiency": memory.get("global_efficiency", default_efficiency()),
        "active_session": active_session,
        "background": background,
        "agentic_readiness": agentic,
        "findings": {"open": findings_open, "path": findings_path},
        "feature_checks": {"runs": feature_check_runs, "verified_findings_open": feature_check_findings, "path_pattern": ".kimiflow/*/FEATURE-CHECK.md"},
        "improvements": {"open": improvements_open, "path": improvements_path},
        "runs": {
            "active": active,
            "backlog": backlog,
            "done": done,
            "other": other,
            "learning_reviews": {"recorded": lr_recorded, "skipped": lr_skipped, "missing": lr_missing, "missing_done": lr_missing_done, "closed": lr_closed, "needs_attention": lr_attention},
            "items": runs_items,
        },
        "maintenance": {"bring_current_recommended": len(reasons) > 0, "reasons": reasons, "commits_since_project_map_baseline": commits_since_map, "workflow_artifacts": workflow_artifacts},
        "repo_docs": {"present": repo_docs_present(root)},
    }
    visible = visible_maintenance_reasons(reasons, memory)
    hidden = hidden_internal_reasons(reasons, memory)
    snapshot["maintenance"]["visible_reasons"] = visible
    snapshot["maintenance"]["hidden_internal_reasons"] = hidden
    snapshot["launcher"] = {
        "schema_version": 1,
        "presentation": "calm",
        "primary_action": primary_action(snapshot),
        "status": {
            "installation": {"version": snapshot["installation"]["version"], "repo_version": snapshot["installation"]["repo_version"], "mode": snapshot["installation"]["mode"], "cache_status": snapshot["installation"]["cache_status"], "action_required": snapshot["installation"]["action_required"]},
            "project_map": {"present": snapshot["project_map"]["present"], "depth": snapshot["project_map"]["depth"], "status": snapshot["project_map"]["status"], "attention": (snapshot["project_map"]["present"] is not True or snapshot["project_map"]["valid"] is not True or snapshot["project_map"]["status"] != "current")},
            "memory": {"present": memory_sum["present"], "tokens_estimate": memory_sum["tokens_estimate"], "budget": memory_sum["budget"], "over_budget": memory_sum["over_budget"], "current_learnings": memory_sum["learnings"]["current"], "curation_recommended": memory_sum["curation"]["recommended"]},
            "efficiency": {"present": snapshot["efficiency"].get("present") is True, "estimated_savings_percent": snapshot["efficiency"].get("estimated_savings_percent"), "confidence": snapshot["efficiency"].get("confidence"), "runs_tracked": snapshot["efficiency"].get("runs_tracked", 0), "projects_tracked": snapshot["efficiency"].get("projects_tracked", 0)},
            "vault": {"health": memory.get("provider", {}).get("health", {}).get("status", "not_detected"), "auth_status": memory.get("provider", {}).get("auth", {}).get("status", "not_configured"), "authenticated": memory.get("provider", {}).get("auth", {}).get("authenticated") is True, "sync_pending_count": memory_sum["provider_sync"]["pending_count"], "direct_write_ready": memory_sum["provider_sync"]["direct_write_ready"]},
            "counts": {"findings_open": findings_open, "feature_check_findings_open": feature_check_findings, "improvements_open": improvements_open, "active_runs": active, "backlog_runs": backlog, "background_collectable": background.get("collectable", 0), "background_stale": background.get("stale", 0)},
        },
        "maintenance": {"visible_count": len(visible), "visible_reasons": visible, "hidden_internal_count": len(hidden), "hidden_internal_reasons": hidden},
        "drilldowns": drilldowns(snapshot),
    }
    return snapshot


def parse_args(argv):
    root = ""
    pretty = False
    full = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            root = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        elif arg == "--pretty":
            pretty = True
            i += 1
        elif arg == "--full":
            full = True
            i += 1
        elif arg in ("--help", "-h"):
            usage()
            raise SystemExit(0)
        else:
            raise ValueError("unknown argument: %s" % arg)
    return root, pretty, full


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        root_arg, pretty, full = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    except ValueError as exc:
        return die(str(exc), 2)

    jq_rc = need_jq()
    if jq_rc:
        return jq_rc
    script_dir = os.path.abspath(os.path.dirname(__file__) + "/..")
    root = resolve_root(root_arg)
    snapshot = build_snapshot(root, script_dir)
    if not full:
        snapshot = dict(snapshot)
        snapshot.pop("memory", None)
        if isinstance(snapshot.get("runs"), dict):
            snapshot["runs"] = dict(snapshot["runs"])
            snapshot["runs"].pop("items", None)
        if isinstance(snapshot.get("background"), dict):
            snapshot["background"] = dict(snapshot["background"])
            snapshot["background"].pop("items", None)
    sys.stdout.write((json_pretty(snapshot) if pretty else json_compact(snapshot)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
