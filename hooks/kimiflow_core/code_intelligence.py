"""Bounded, optional router for explicitly configured code-intelligence providers."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys


class CodeIntelligenceError(ValueError):
    pass


RELATIONS = {
    "definition", "reference", "caller", "callee", "dependency", "implementation", "type",
}
SIGNALS = {"architecture", "cross_file", "caller_impact", "map_stale", "lexical_miss"}
PROVENANCE = {"scip", "lsp", "ast", "provider"}
IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$:-]{0,255}$")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_CAPABILITIES_BYTES = 64 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_FACTS = 4096


def _reject_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _identity(value):
    if not isinstance(value, str) or IDENTITY_RE.fullmatch(value) is None:
        raise CodeIntelligenceError("identity_invalid")
    return value


def _symbol(value, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or SYMBOL_RE.fullmatch(value) is None:
        raise CodeIntelligenceError("symbol_invalid")
    return value


def _relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise CodeIntelligenceError("path_invalid")
    normalized = os.path.normpath(value).replace(os.sep, "/")
    if normalized != value or value.startswith("/") or normalized in (".", "..") or normalized.startswith("../"):
        raise CodeIntelligenceError("path_invalid")
    return value


def _root_file(root, relative):
    relative = _relative_path(relative)
    target = os.path.realpath(os.path.join(root, relative))
    if os.path.commonpath((root, target)) != root:
        raise CodeIntelligenceError("path_escape")
    try:
        named = os.stat(os.path.join(root, relative), follow_symlinks=False)
    except OSError:
        raise CodeIntelligenceError("path_missing")
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
        raise CodeIntelligenceError("path_unsafe")
    return relative


def normalize_capabilities(value):
    required = {"schema_version", "name", "version", "relations", "dirty_workspace", "snapshot"}
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
        raise CodeIntelligenceError("capabilities_invalid")
    relations = value.get("relations")
    if (
        not isinstance(relations, list) or not relations or len(relations) > len(RELATIONS)
        or any(item not in RELATIONS for item in relations) or len(relations) != len(set(relations))
        or not isinstance(value.get("dirty_workspace"), bool)
        or value.get("snapshot") is not True
        or not isinstance(value.get("version"), str) or not value["version"] or len(value["version"]) > 96
    ):
        raise CodeIntelligenceError("capabilities_invalid")
    return {
        "schema_version": 1,
        "name": _identity(value["name"]),
        "version": value["version"],
        "relations": sorted(relations),
        "dirty_workspace": value["dirty_workspace"],
        "snapshot": True,
    }


def _git(root, *args):
    proc = subprocess.run(
        ["git", "-C", root, *args], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        raise CodeIntelligenceError("snapshot_git_failed")
    return proc.stdout


def snapshot(root):
    root = os.path.realpath(root)
    head = _git(root, "rev-parse", "HEAD").strip()
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    diff = _git(root, "diff", "--binary", "HEAD")
    if len(status) + len(diff) > MAX_SNAPSHOT_BYTES:
        raise CodeIntelligenceError("snapshot_oversize")
    digest = hashlib.sha256(b"kimiflow-code-snapshot-v1\0" + head + b"\0" + status + b"\0" + diff)
    # `git diff` excludes untracked contents; bind those bytes explicitly.
    rows = [row for row in status.split(b"\0") if row]
    total = len(status) + len(diff)
    for row in sorted(rows):
        if not row.startswith(b"?? "):
            continue
        relative = row[3:].decode("utf-8", "surrogateescape")
        path = os.path.join(root, relative)
        try:
            info = os.stat(path, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise CodeIntelligenceError("snapshot_untracked_unsafe")
            total += info.st_size
            if total > MAX_SNAPSHOT_BYTES:
                raise CodeIntelligenceError("snapshot_oversize")
            digest.update(relative.encode("utf-8", "surrogateescape") + b"\0")
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    digest.update(chunk)
        except OSError:
            raise CodeIntelligenceError("snapshot_untracked_unreadable")
        digest.update(b"\0")
    return {"id": "sha256:" + digest.hexdigest(), "dirty": bool(status)}


def eligible(scope, affected_paths, signals, exact_targets=False):
    if not isinstance(exact_targets, bool):
        raise CodeIntelligenceError("exact_targets_invalid")
    if scope != "large":
        return False, "scope_not_large"
    if not isinstance(affected_paths, (list, tuple)) or not affected_paths:
        return False, "anchors_missing"
    if exact_targets:
        return False, "exact_targets_known"
    normalized_signals = set(signals or ())
    if normalized_signals - SIGNALS:
        raise CodeIntelligenceError("signals_invalid")
    if not normalized_signals:
        return False, "semantic_signal_missing"
    return True, "eligible"


def _provider_capabilities(executable, environ=None):
    try:
        proc = subprocess.run(
            [executable, "capabilities", "--json"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=5, check=False, env=environ,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CodeIntelligenceError("provider_unavailable")
    if proc.returncode != 0 or len(proc.stdout) > MAX_CAPABILITIES_BYTES:
        raise CodeIntelligenceError("provider_capabilities_failed")
    try:
        return normalize_capabilities(json.loads(proc.stdout.decode("utf-8"), object_pairs_hook=_reject_duplicates))
    except (UnicodeError, ValueError):
        raise CodeIntelligenceError("provider_capabilities_invalid")


def _provider_query(executable, request, deadline_ms, environ=None):
    payload = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        proc = subprocess.run(
            [executable, "query", "--json"], input=payload, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=deadline_ms / 1000.0, check=False, env=environ,
        )
    except subprocess.TimeoutExpired:
        raise CodeIntelligenceError("provider_timeout")
    except OSError:
        raise CodeIntelligenceError("provider_unavailable")
    if proc.returncode != 0:
        raise CodeIntelligenceError("provider_failed")
    if len(proc.stdout) > MAX_RESULT_BYTES:
        raise CodeIntelligenceError("provider_result_oversize")
    try:
        return json.loads(proc.stdout.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeError, ValueError):
        raise CodeIntelligenceError("provider_result_invalid")


def normalize_result(root, value, request, capabilities):
    if not isinstance(value, dict) or set(value) != {"schema_version", "snapshot_id", "facts"} or value.get("schema_version") != 1:
        raise CodeIntelligenceError("provider_result_invalid")
    if value.get("snapshot_id") != request["snapshot_id"]:
        raise CodeIntelligenceError("snapshot_mismatch")
    facts = value.get("facts")
    if not isinstance(facts, list) or len(facts) > MAX_FACTS:
        raise CodeIntelligenceError("provider_result_invalid")
    allowed_relations = set(request["relations"]).intersection(capabilities["relations"])
    normalized = []
    seen = set()
    for fact in facts:
        required = {"path", "start_line", "end_line", "symbol", "relation", "target", "confidence", "provenance"}
        if not isinstance(fact, dict) or set(fact) != required or fact.get("relation") not in allowed_relations or fact.get("provenance") not in PROVENANCE:
            raise CodeIntelligenceError("provider_fact_invalid")
        start = fact.get("start_line")
        end = fact.get("end_line")
        confidence = fact.get("confidence")
        if (
            isinstance(start, bool) or not isinstance(start, int) or start < 1
            or isinstance(end, bool) or not isinstance(end, int) or end < start
            or isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 1_000_000
        ):
            raise CodeIntelligenceError("provider_fact_invalid")
        row = {
            "path": _root_file(root, fact["path"]),
            "start_line": start,
            "end_line": end,
            "symbol": _symbol(fact.get("symbol"), optional=True),
            "relation": fact["relation"],
            "target": _symbol(fact.get("target"), optional=True),
            "confidence": confidence,
            "provenance": fact["provenance"],
        }
        key = (row["path"], start, end, row["symbol"], row["relation"], row["target"])
        if key not in seen:
            seen.add(key)
            normalized.append(row)
    return sorted(normalized, key=lambda row: (-row["confidence"], row["path"], row["start_line"], row["relation"]))


def _render(facts, k, max_bytes, max_tokens):
    rows = ["# Code Intelligence Context", ""]
    selected = []
    truncated = False
    for fact in facts[:k]:
        target = " -> %s" % fact["target"] if fact["target"] else ""
        symbol = fact["symbol"] or "<file>"
        line = "- `%s:%d-%d` `%s` %s%s (%s, %dppm)" % (
            fact["path"], fact["start_line"], fact["end_line"], symbol,
            fact["relation"], target, fact["provenance"], fact["confidence"],
        )
        candidate = "\n".join(rows + [line, ""]) + "\n"
        candidate_bytes = len(candidate.encode("utf-8"))
        if (
            candidate_bytes > max_bytes
            or (candidate_bytes + 3) // 4 > max_tokens
            or len(selected) >= 12
        ):
            truncated = True
            break
        rows.append(line)
        selected.append(fact)
    context = "\n".join(rows + [""]) + "\n" if selected else ""
    return context, selected, truncated or len(selected) < min(len(facts), k)


def route(
    root, scope, affected_paths, signals, executable=None, relation_types=None,
    symbols=None, mode="shadow", k=40, hops=1, deadline_ms=5000, max_bytes=8192,
    max_tokens=2048, exact_targets=False, environ=None,
):
    root = os.path.realpath(root)
    is_eligible, reason = eligible(
        scope, affected_paths, signals, exact_targets=exact_targets
    )
    fallback = {
        "schema_version": 1, "status": "fallback", "route": "lexical",
        "provider_invoked": False, "reason": reason, "context": "", "user_gate": False,
    }
    if not is_eligible:
        return fallback
    if not executable:
        return {**fallback, "reason": "provider_unconfigured"}
    if mode not in ("auto", "shadow", "canary", "active"):
        raise CodeIntelligenceError("mode_invalid")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 40:
        raise CodeIntelligenceError("k_invalid")
    if isinstance(hops, bool) or not isinstance(hops, int) or not 1 <= hops <= 2:
        raise CodeIntelligenceError("hops_invalid")
    if isinstance(deadline_ms, bool) or not isinstance(deadline_ms, int) or not 100 <= deadline_ms <= 30_000:
        raise CodeIntelligenceError("deadline_invalid")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 256 <= max_bytes <= 65_536:
        raise CodeIntelligenceError("budget_invalid")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 64 <= max_tokens <= 16_384:
        raise CodeIntelligenceError("token_budget_invalid")
    try:
        current = snapshot(root)
        capabilities = _provider_capabilities(executable, environ=environ)
        if current["dirty"] and not capabilities["dirty_workspace"]:
            raise CodeIntelligenceError("dirty_workspace_unsupported")
        relations = sorted(set(relation_types or capabilities["relations"]))
        if not relations or set(relations) - set(capabilities["relations"]):
            raise CodeIntelligenceError("relations_unsupported")
        anchors = sorted({_root_file(root, path) for path in affected_paths})
        normalized_symbols = sorted({_symbol(item) for item in (symbols or [])})
        request = {
            "schema_version": 1,
            "root": root,
            "snapshot_id": current["id"],
            "dirty": current["dirty"],
            "anchors": {"paths": anchors, "symbols": normalized_symbols},
            "relations": relations,
            "k": k,
            "hops": hops,
            "deadline_ms": deadline_ms,
            "max_bytes": max_bytes,
            "max_tokens": max_tokens,
        }
        raw = _provider_query(executable, request, deadline_ms, environ=environ)
        facts = normalize_result(root, raw, request, capabilities)
        context, selected, truncated = _render(facts, k, max_bytes, max_tokens)
        metrics = {
            "fact_count": len(facts),
            "selected_count": len(selected),
            "context_bytes": len(context.encode("utf-8")),
            "estimated_tokens": (len(context.encode("utf-8")) + 3) // 4,
            "truncated": truncated,
        }
        provider = {
            "name": capabilities["name"],
            "version": capabilities["version"],
            "fingerprint": "sha256:" + hashlib.sha256(
                json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        if mode == "auto":
            from . import adaptive_control
            task_class = sorted(set(signals))[0]
            policy = adaptive_control.resolve_retrieval_route(
                root, provider["fingerprint"], task_class,
            )
            mode = policy["route"]
            if mode == "off":
                return {
                    **fallback,
                    "provider_invoked": True,
                    "reason": policy["reason"],
                }
        if mode == "shadow":
            return {
                "schema_version": 1, "status": "shadow", "route": "shadow",
                "provider_invoked": True, "reason": "shadow_evaluated", "provider": provider,
                "snapshot_id": current["id"], "metrics": metrics, "context": "", "user_gate": False,
            }
        return {
            "schema_version": 1, "status": "selected", "route": mode,
            "provider_invoked": True, "reason": "evidence_route_selected", "provider": provider,
            "snapshot_id": current["id"], "metrics": metrics, "context": context, "user_gate": False,
        }
    except CodeIntelligenceError as exc:
        return {**fallback, "provider_invoked": True, "reason": str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="code-intelligence")
    parser.add_argument("--root", required=True)
    parser.add_argument("--scope", choices=("trivial", "small", "large"), required=True)
    parser.add_argument("--affected-path", action="append", default=[])
    parser.add_argument("--signal", action="append", default=[])
    parser.add_argument("--provider")
    parser.add_argument("--relation", action="append")
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--mode", choices=("auto", "shadow", "canary", "active"), default="auto")
    parser.add_argument("--k", type=int, default=40)
    parser.add_argument("--hops", type=int, default=1)
    parser.add_argument("--deadline-ms", type=int, default=5000)
    parser.add_argument("--max-bytes", type=int, default=8192)
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--exact-targets", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        value = route(
            args.root, args.scope, args.affected_path, args.signal, args.provider,
            args.relation, args.symbol, args.mode, args.k, args.hops,
            args.deadline_ms, args.max_bytes, args.max_tokens,
            args.exact_targets,
        )
        print(json.dumps(value, sort_keys=True, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0
    except (CodeIntelligenceError, OSError, ValueError) as exc:
        print("code-intelligence: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
