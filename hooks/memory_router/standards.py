"""Path-aware, token-bounded project standards selection and recording.

Structured ``.kimiflow/STANDARDS.md`` entries use this compact form::

    [Scope: src/core/storage/**]
    - Type: invariant
      Rule: Writes pass through TransactionManager.
      Evidence: tests/test_storage.py::test_race

Selection never loads unrelated structured rules. A legacy-only file retains a bounded
compatibility fallback; once any valid structured entry exists, flat bullets are omitted.
"""
import os
import re

from . import contracts, rows, store
from .cli import die, resolve_root

ALLOWED_TYPES = ("invariant", "constraint", "preference", "heuristic", "legacy")
DEFAULT_TYPES = ALLOWED_TYPES[:-1]
TYPE_PRIORITY = {name: index for index, name in enumerate(ALLOWED_TYPES)}
MAX_RULE_CHARS = 500
MAX_EVIDENCE_CHARS = 300
MAX_SCOPES = 8
DEFAULT_MAX_RULES = 12
DEFAULT_BUDGET_WORDS = 450
MAX_BUDGET_WORDS = 900

_SCOPE_RE = re.compile(r"^\[Scope:\s*(.*?)\s*\]$", re.IGNORECASE)
_TYPE_RE = re.compile(r"^\s*-\s*Type:\s*(.*?)\s*$", re.IGNORECASE)
_FIELD_RE = re.compile(r"^\s+(Rule|Evidence):\s*(.*?)\s*$", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _safe_single_line(value, max_chars):
    return bool(value) and len(value) <= max_chars and not _CONTROL_RE.search(value)


def _normalize_path(value):
    value = value.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if not value or value.startswith("/") or value == ".." or value.startswith("../"):
        return ""
    parts = value.split("/")
    if any(part == ".." for part in parts):
        return ""
    return "/".join(part for part in parts if part not in ("", "."))


def _safe_scope(value):
    normalized = _normalize_path(value)
    return normalized if normalized and not _CONTROL_RE.search(normalized) else ""


def _glob_regex(pattern):
    out = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def scope_matches(scope, path):
    return bool(_glob_regex(scope).match(path))


def _candidate(scopes, rule_type, rule, evidence, line):
    clean_scopes = [_safe_scope(scope) for scope in scopes]
    valid = (
        0 < len(clean_scopes) <= MAX_SCOPES
        and all(clean_scopes)
        and rule_type in ALLOWED_TYPES
        and _safe_single_line(rule, MAX_RULE_CHARS)
        and _safe_single_line(evidence, MAX_EVIDENCE_CHARS)
        and rows.memory_security_json(rule + "\n" + evidence)["ok"]
        and not rows.has_secret_value(rule + "\n" + evidence)
    )
    return {
        "scopes": clean_scopes,
        "type": rule_type,
        "rule": rule,
        "evidence": evidence,
        "line": line,
        "valid": valid,
    }


def parse_standards(content):
    entries = []
    legacy = []
    malformed = 0
    scopes = []
    current = None

    def flush():
        nonlocal current, malformed
        if current is None:
            return
        if current.get("invalid"):
            malformed += 1
            current = None
            return
        item = _candidate(
            current["scopes"], current["type"], current.get("rule", ""),
            current.get("evidence", ""), current["line"],
        )
        if item["valid"]:
            entries.append(item)
        else:
            malformed += 1
        current = None

    for line_no, raw in enumerate(content.splitlines(), 1):
        scope_match = _SCOPE_RE.match(raw)
        if scope_match:
            flush()
            values = [value.strip() for value in scope_match.group(1).split(",")]
            scopes = values
            if not values or any(not _safe_scope(value) for value in values):
                malformed += 1
                scopes = []
            continue
        type_match = _TYPE_RE.match(raw)
        if type_match:
            flush()
            current = {"scopes": list(scopes), "type": type_match.group(1).strip().lower(), "line": line_no}
            continue
        field_match = _FIELD_RE.match(raw)
        if field_match and current is not None:
            key = field_match.group(1).lower()
            if key in current:
                current["invalid"] = True
            current[key] = field_match.group(2).strip()
            continue
        if not scopes and raw.startswith("- "):
            text = raw[2:].strip()
            if _safe_single_line(text, MAX_RULE_CHARS) and "(evidence:" in text.lower():
                item = _candidate(["**"], "legacy", text, "embedded legacy evidence", line_no)
                if item["valid"]:
                    legacy.append(item)
                else:
                    malformed += 1
            elif text:
                malformed += 1
    flush()
    return {"entries": entries, "legacy": legacy, "malformed": malformed}


def select_rules(content, affected, allowed_types=None, max_rules=DEFAULT_MAX_RULES,
                 budget_words=DEFAULT_BUDGET_WORDS):
    parsed = parse_standards(content)
    allowed = tuple(allowed_types or DEFAULT_TYPES)
    structured = parsed["entries"]
    source = structured if structured else parsed["legacy"]
    candidates = []
    for item in source:
        if item["type"] not in allowed and not (not structured and item["type"] == "legacy"):
            continue
        if any(scope_matches(scope, path) for scope in item["scopes"] for path in affected):
            candidates.append(item)
    candidates.sort(key=lambda item: (TYPE_PRIORITY[item["type"]], item["line"]))

    selected = []
    words = 0
    seen = set()
    budget_skipped = 0
    for item in candidates:
        key = (tuple(item["scopes"]), item["type"], item["rule"])
        if key in seen:
            continue
        seen.add(key)
        item_words = len((" ".join(item["scopes"]) + " " + item["type"] + " "
                          + item["rule"] + " " + item["evidence"]).split())
        if len(selected) >= max_rules or words + item_words > budget_words:
            budget_skipped += 1
            continue
        selected.append(item)
        words += item_words
    return {
        "rules": selected,
        "malformed": parsed["malformed"],
        "legacy_fallback": not bool(structured),
        "budget_words": budget_words,
        "selected_words": words,
        "budget_skipped": budget_skipped,
    }


def render_context(result, affected, source_path):
    lines = [
        "# Scoped Standards Context", "",
        "<!-- kimiflow:scoped-standards rules=%d malformed=%d legacy_fallback=%s budget_words=%d selected_words=%d -->" % (
            len(result["rules"]), result["malformed"],
            "yes" if result["legacy_fallback"] else "no",
            result["budget_words"], result["selected_words"],
        ),
        "Source: %s" % source_path,
        "Affected paths: %s" % ", ".join(affected), "",
    ]
    if not result["rules"]:
        lines.append("No matching scoped standards.")
    for item in result["rules"]:
        lines.extend([
            "[Scope: %s]" % ", ".join(item["scopes"]),
            "- Type: %s" % item["type"],
            "  Rule: %s" % item["rule"],
            "  Evidence: %s" % item["evidence"],
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _parse_types(value):
    values = []
    for item in value.split(","):
        item = item.strip().lower()
        if item and item not in values:
            values.append(item)
    return values


def _bounded_int(value, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if minimum <= number <= maximum else None


def _safe_output_path(root, value):
    target = value if os.path.isabs(value) else os.path.join(root, value)
    target = os.path.abspath(target)
    local_root = os.path.abspath(os.path.join(root, ".kimiflow"))
    if os.path.islink(local_root) or not os.path.isdir(local_root):
        return ""
    try:
        if os.path.commonpath((target, local_root)) != local_root:
            return ""
    except ValueError:
        return ""
    parent = os.path.dirname(target)
    probe = parent
    while not os.path.exists(probe):
        next_probe = os.path.dirname(probe)
        if next_probe == probe:
            return ""
        probe = next_probe
    try:
        if os.path.commonpath((os.path.realpath(probe), os.path.realpath(local_root))) != os.path.realpath(local_root):
            return ""
    except ValueError:
        return ""
    os.makedirs(parent, mode=0o700, exist_ok=True)
    if os.path.commonpath((os.path.realpath(parent), os.path.realpath(local_root))) != os.path.realpath(local_root):
        return ""
    return target


def _select(argv):
    root = ""
    affected = []
    allowed = []
    max_rules = DEFAULT_MAX_RULES
    budget = DEFAULT_BUDGET_WORDS
    output = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--root", "--affected", "--types", "--type", "--max", "--budget", "--write"):
            i += 1
            value = argv[i] if i < len(argv) else ""
            if arg == "--root": root = value
            elif arg == "--affected": affected.append(value)
            elif arg in ("--types", "--type"): allowed.extend(_parse_types(value))
            elif arg == "--max": max_rules = _bounded_int(value, 1, 20)
            elif arg == "--budget": budget = _bounded_int(value, 50, MAX_BUDGET_WORDS)
            else: output = value
        elif arg in ("--pretty",):
            pass
        else:
            return die("standards select: unknown argument: %s" % arg, 2)
        i += 1
    root = resolve_root(root)
    affected = [_normalize_path(path) for path in affected]
    if not affected or any(not path for path in affected):
        return die("standards select: at least one safe --affected path is required", 2)
    if max_rules is None or budget is None:
        return die("standards select: invalid --max or --budget", 2)
    if allowed and any(value not in ALLOWED_TYPES for value in allowed):
        return die("standards select: invalid type", 2)
    standards_path = os.path.join(root, ".kimiflow", "STANDARDS.md")
    if os.path.islink(os.path.join(root, ".kimiflow")) or os.path.islink(standards_path):
        return die("standards select: refusing symlink source", 1)
    content = store.read_text(standards_path)
    result = select_rules(content, affected, allowed or DEFAULT_TYPES, max_rules, budget)
    written = ""
    if output:
        written = _safe_output_path(root, output)
        if not written:
            return die("standards select: --write must stay under .kimiflow", 2)
        try:
            store.atomic_write(written, render_context(result, affected, ".kimiflow/STANDARDS.md"), mode=0o600)
        except (OSError, ValueError) as exc:
            return die("standards select: write failed: %s" % exc, 1)
    receipt = {
        "schema_version": 1,
        "status": "matched" if result["rules"] else "empty",
        "source_present": os.path.isfile(standards_path),
        "rules": len(result["rules"]),
        "malformed": result["malformed"],
        "legacy_fallback": result["legacy_fallback"],
        "selected_words": result["selected_words"],
        "budget_words": result["budget_words"],
        "budget_skipped": result["budget_skipped"],
        "written": os.path.relpath(written, root) if written else "",
    }
    print(contracts.dumps(receipt))
    return 0


def _record(argv):
    root = ""
    scopes = []
    rule_type = ""
    rule = ""
    evidence = ""
    write = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--root", "--scope", "--type", "--rule", "--evidence"):
            i += 1
            value = argv[i] if i < len(argv) else ""
            if arg == "--root": root = value
            elif arg == "--scope": scopes.append(value)
            elif arg == "--type": rule_type = value.strip().lower()
            elif arg == "--rule": rule = value.strip()
            else: evidence = value.strip()
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pass
        else:
            return die("standards record: unknown argument: %s" % arg, 2)
        i += 1
    root = resolve_root(root)
    item = _candidate(scopes, rule_type, rule, evidence, 0)
    if not item["valid"]:
        return die("standards record: Scope/Type/Rule/Evidence contract invalid", 2)
    standards_path = os.path.join(root, ".kimiflow", "STANDARDS.md")
    if os.path.islink(os.path.join(root, ".kimiflow")) or os.path.islink(standards_path):
        return die("standards record: refusing symlink destination", 1)
    current = store.read_text(standards_path)
    parsed = parse_standards(current)
    duplicate = any(
        entry["scopes"] == item["scopes"] and entry["type"] == item["type"] and entry["rule"] == item["rule"]
        for entry in parsed["entries"]
    )
    block = (
        "[Scope: %s]\n- Type: %s\n  Rule: %s\n  Evidence: %s\n" %
        (", ".join(item["scopes"]), item["type"], item["rule"], item["evidence"])
    )
    if write and not duplicate:
        os.makedirs(os.path.dirname(standards_path), mode=0o700, exist_ok=True)
        if current:
            body = current.rstrip() + "\n\n" + block
            mode = os.stat(standards_path).st_mode & 0o777
        else:
            body = "# Kimiflow Standards\n\n" + block
            mode = 0o600
        try:
            store.atomic_write(standards_path, body, mode=mode)
        except (OSError, ValueError) as exc:
            return die("standards record: write failed: %s" % exc, 1)
    receipt = {
        "schema_version": 1,
        "status": "duplicate" if duplicate else ("recorded" if write else "preview"),
        "scope": item["scopes"],
        "type": item["type"],
        "written": bool(write and not duplicate),
    }
    print(contracts.dumps(receipt))
    return 0


def run(argv):
    if not argv or argv[0] in ("--help", "-h"):
        return die("standards requires select|record", 2)
    mode = argv[0]
    if mode == "select":
        return _select(argv[1:])
    if mode == "record":
        return _record(argv[1:])
    return die("standards: expected select|record", 2)
