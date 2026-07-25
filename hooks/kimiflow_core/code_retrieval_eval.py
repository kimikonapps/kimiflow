"""Deterministic, model-free evaluation for bounded code retrieval results."""

import argparse
import json
import os
import re
import sys


class RetrievalEvalError(ValueError):
    pass


IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$:-]{0,255}$")
PATH_LIMIT = 512
MAX_QUERIES = 256
MAX_CANDIDATES = 4096


def _reject_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _safe_path(value):
    if not isinstance(value, str) or not value or len(value) > PATH_LIMIT:
        raise RetrievalEvalError("path_invalid")
    normalized = os.path.normpath(value).replace(os.sep, "/")
    if normalized != value or value.startswith("/") or normalized in (".", "..") or normalized.startswith("../"):
        raise RetrievalEvalError("path_invalid")
    return value


def _identity(value):
    if not isinstance(value, str) or IDENTITY_RE.fullmatch(value) is None:
        raise RetrievalEvalError("identity_invalid")
    return value


def _symbol(value):
    if not isinstance(value, str) or SYMBOL_RE.fullmatch(value) is None:
        raise RetrievalEvalError("symbol_invalid")
    return value


def _string_list(value, kind):
    if not isinstance(value, list) or len(value) > 256:
        raise RetrievalEvalError("%s_invalid" % kind)
    normalized = []
    for item in value:
        normalized.append(_safe_path(item) if kind.endswith("paths") else _symbol(item))
    if len(normalized) != len(set(normalized)):
        raise RetrievalEvalError("%s_duplicate" % kind)
    return normalized


def normalize_fixture(value):
    required = {"schema_version", "fixture_id", "k", "max_context_bytes", "queries"}
    if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != 1:
        raise RetrievalEvalError("fixture_invalid")
    k = value.get("k")
    budget = value.get("max_context_bytes")
    queries = value.get("queries")
    if (
        isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100
        or isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 10_000_000
        or not isinstance(queries, list) or not 1 <= len(queries) <= MAX_QUERIES
    ):
        raise RetrievalEvalError("fixture_invalid")
    rows = []
    seen = set()
    for row in queries:
        if not isinstance(row, dict) or set(row) != {
            "id", "query_class", "expected_paths", "expected_symbols", "forbidden_paths",
        }:
            raise RetrievalEvalError("query_invalid")
        query_id = _identity(row.get("id"))
        query_class = _identity(row.get("query_class"))
        if query_id in seen:
            raise RetrievalEvalError("query_duplicate")
        seen.add(query_id)
        expected_paths = _string_list(row["expected_paths"], "expected_paths")
        expected_symbols = _string_list(row["expected_symbols"], "expected_symbols")
        if not expected_paths and not expected_symbols:
            raise RetrievalEvalError("query_expectation_missing")
        rows.append({
            "id": query_id,
            "query_class": query_class,
            "expected_paths": expected_paths,
            "expected_symbols": expected_symbols,
            "forbidden_paths": _string_list(row["forbidden_paths"], "forbidden_paths"),
        })
    return {
        "schema_version": 1,
        "fixture_id": _identity(value["fixture_id"]),
        "k": k,
        "max_context_bytes": budget,
        "queries": rows,
    }


def normalize_result(value, fixture):
    if not isinstance(value, dict) or set(value) != {"schema_version", "fixture_id", "queries"}:
        raise RetrievalEvalError("result_invalid")
    if value.get("schema_version") != 1 or value.get("fixture_id") != fixture["fixture_id"]:
        raise RetrievalEvalError("result_fixture_mismatch")
    rows = value.get("queries")
    if not isinstance(rows, list) or len(rows) != len(fixture["queries"]):
        raise RetrievalEvalError("result_query_mismatch")
    expected_ids = {row["id"] for row in fixture["queries"]}
    normalized = []
    seen = set()
    count = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "snapshot_status", "candidates"}:
            raise RetrievalEvalError("result_query_invalid")
        query_id = _identity(row.get("id"))
        if query_id not in expected_ids or query_id in seen or row.get("snapshot_status") not in ("current", "stale"):
            raise RetrievalEvalError("result_query_invalid")
        seen.add(query_id)
        candidates = row.get("candidates")
        if not isinstance(candidates, list):
            raise RetrievalEvalError("candidate_invalid")
        result_candidates = []
        for candidate in candidates:
            count += 1
            if count > MAX_CANDIDATES or not isinstance(candidate, dict) or set(candidate) != {"path", "symbol", "context_bytes"}:
                raise RetrievalEvalError("candidate_invalid")
            context_bytes = candidate.get("context_bytes")
            symbol = candidate.get("symbol")
            if (
                symbol is not None and (not isinstance(symbol, str) or SYMBOL_RE.fullmatch(symbol) is None)
                or isinstance(context_bytes, bool) or not isinstance(context_bytes, int) or not 0 <= context_bytes <= 10_000_000
            ):
                raise RetrievalEvalError("candidate_invalid")
            result_candidates.append({
                "path": _safe_path(candidate.get("path")),
                "symbol": symbol,
                "context_bytes": context_bytes,
            })
        normalized.append({
            "id": query_id,
            "snapshot_status": row["snapshot_status"],
            "candidates": result_candidates,
        })
    if seen != expected_ids:
        raise RetrievalEvalError("result_query_mismatch")
    return {"schema_version": 1, "fixture_id": fixture["fixture_id"], "queries": normalized}


def _ppm(numerator, denominator):
    return 0 if denominator == 0 else (numerator * 1_000_000) // denominator


def evaluate(fixture_value, result_value):
    fixture = normalize_fixture(fixture_value)
    result = normalize_result(result_value, fixture)
    by_id = {row["id"]: row for row in result["queries"]}
    expected_path_total = expected_symbol_total = retrieved_total = relevant_total = 0
    path_hits = symbol_hits = reciprocal_rank_sum = forbidden_hits = context_bytes = stale_queries = 0
    per_query = []
    for query in fixture["queries"]:
        observed = by_id[query["id"]]
        candidates = observed["candidates"][:fixture["k"]]
        expected_paths = set(query["expected_paths"])
        expected_symbols = set(query["expected_symbols"])
        forbidden = set(query["forbidden_paths"])
        paths = [row["path"] for row in candidates]
        symbols = [row["symbol"] for row in candidates if row["symbol"] is not None]
        path_count = len(expected_paths.intersection(paths))
        symbol_count = len(expected_symbols.intersection(symbols))
        relevant = sum(row["path"] in expected_paths or row["symbol"] in expected_symbols for row in candidates)
        first_rank = next((index for index, row in enumerate(candidates, 1) if row["path"] in expected_paths or row["symbol"] in expected_symbols), None)
        query_forbidden = sum(row["path"] in forbidden for row in candidates)
        query_bytes = sum(row["context_bytes"] for row in candidates)
        is_stale = observed["snapshot_status"] != "current"
        expected_path_total += len(expected_paths)
        expected_symbol_total += len(expected_symbols)
        retrieved_total += len(candidates)
        relevant_total += relevant
        path_hits += path_count
        symbol_hits += symbol_count
        reciprocal_rank_sum += 0 if first_rank is None else 1_000_000 // first_rank
        forbidden_hits += query_forbidden
        context_bytes += query_bytes
        stale_queries += int(is_stale)
        per_query.append({
            "id": query["id"],
            "path_hits": path_count,
            "symbol_hits": symbol_count,
            "retrieved": len(candidates),
            "forbidden_hits": query_forbidden,
            "context_bytes": query_bytes,
            "snapshot_status": observed["snapshot_status"],
        })
    passed = forbidden_hits == 0 and stale_queries == 0 and context_bytes <= fixture["max_context_bytes"]
    return {
        "schema_version": 1,
        "fixture_id": fixture["fixture_id"],
        "status": "passed" if passed else "failed",
        "metrics": {
            "file_recall_ppm": _ppm(path_hits, expected_path_total),
            "symbol_recall_ppm": _ppm(symbol_hits, expected_symbol_total),
            "precision_ppm": _ppm(relevant_total, retrieved_total),
            "mrr_ppm": reciprocal_rank_sum // len(fixture["queries"]),
            "forbidden_hits": forbidden_hits,
            "stale_queries": stale_queries,
            "context_bytes": context_bytes,
            "estimated_tokens": (context_bytes + 3) // 4,
            "max_context_bytes": fixture["max_context_bytes"],
        },
        "queries": per_query,
    }


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=_reject_duplicates)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="code-retrieval-eval")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        fixture = _load(args.fixture)
        value = {"schema_version": 1, "candidate": evaluate(fixture, _load(args.candidate))}
        if args.baseline:
            value["baseline"] = evaluate(fixture, _load(args.baseline))
        print(json.dumps(value, sort_keys=True, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")))
        return 0 if value["candidate"]["status"] == "passed" else 1
    except (OSError, UnicodeError, ValueError, RetrievalEvalError) as exc:
        print("code-retrieval-eval: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
