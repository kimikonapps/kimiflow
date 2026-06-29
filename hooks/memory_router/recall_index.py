"""RECALL.sqlite FTS5 engine: availability probe, schema init, row insert, term ->
MATCH-query construction, and the hit query with graceful degradation. Behavioral
port of the Bash sqlite_available / fts_query_from_terms / insert_fts_row / the
recall schema / fts_hits_json at kimiflow--v0.1.50 (2527-2644). Uses the Python
stdlib `sqlite3` module instead of shelling to the `sqlite3` CLI."""
import os
import re
import sqlite3

from . import clock, paths, store, text

# Source of truth: Bash 2562-2563.
_SCHEMA = (
    "DROP TABLE IF EXISTS recall_meta;\n"
    "DROP TABLE IF EXISTS recall_fts;\n"
    "CREATE TABLE recall_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
    "CREATE VIRTUAL TABLE recall_fts USING fts5(kind, source, title, body, ref);"
)

_NON_TERM = re.compile(r"[^A-Za-z0-9_]")

# Bash build_recall_index run-artifact filter (2613-2620): match these basenames
# anywhere under .kimiflow (except the pruned project dir), plus any *.md under a
# findings/ directory.
_ARTIFACT_NAMES = frozenset((
    "INTENT.md", "PROBLEM.md", "RESEARCH.md", "DIAGNOSIS.md", "PLAN.md",
    "ACCEPTANCE.md", "REVIEW.md", "CODE-REVIEW.md", "LEARNING-REVIEW.md",
    "ADVISORIES.md",
))
_MIDDOT = "\u00b7"  # U+00B7 MIDDLE DOT; never write the literal char (handoff gotcha).

# run_artifact_rows_json's find (Bash 1668) matches the SAME basenames as
# build_recall_index PLUS `STATE.md` (Bash 2619 omits it). The recall/history run-artifact
# source therefore uses a wider name set than the index builder. Grounded divergence.
_RUN_ARTIFACT_NAMES = _ARTIFACT_NAMES | frozenset(("STATE.md",))


def fts5_available():
    # Bash gates on `command -v sqlite3` (the CLI). The stdlib sqlite3 module is
    # always importable, but FTS5 may not be compiled in, so we probe it. See spec 12.
    try:
        con = sqlite3.connect(":memory:")
    except sqlite3.Error:
        return False
    try:
        con.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        return True
    except sqlite3.Error:
        return False
    finally:
        con.close()


def recall_db_path(root):
    return os.path.join(root, ".kimiflow", "project", "RECALL.sqlite")


def init_recall_db(con):
    # Bash 2559-2565: drop+create the schema, then stamp recall_meta.updated_at.
    # Caller must confirm fts5_available() first (the CREATE VIRTUAL TABLE here
    # would raise sqlite3.OperationalError otherwise).
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO recall_meta(key, value) VALUES('updated_at', ?)", (clock.iso_now(),)
    )


def insert_fts_row(con, kind, source, title, body, ref):
    # Bash 2542-2545 uses sql_quote string interpolation; the stdlib module binds
    # parameters instead (equivalent result, no quoting bugs).
    con.execute(
        "INSERT INTO recall_fts(kind, source, title, body, ref) VALUES(?, ?, ?, ?, ?)",
        (kind, source, title, body, ref),
    )


def fts_query_from_terms(terms):
    # Bash 2531-2540 (jq): strip each term to [A-Za-z0-9_], keep length >= 3,
    # `unique` (jq sorts + dedups), quote each, join with " OR ".
    cleaned = {_NON_TERM.sub("", str(term)) for term in terms}
    kept = sorted(t for t in cleaned if len(t) >= 3)
    return " OR ".join('"' + t + '"' for t in kept)


def fts_hits_json(root, terms, max_hits):
    # Bash 2623-2644: graceful degradation -> [] when sqlite/fts5 absent, db missing,
    # query empty, or any sqlite error.
    db = recall_db_path(root)
    if not fts5_available() or not os.path.isfile(db):
        return []
    query = fts_query_from_terms(terms)
    if not query:
        return []
    try:
        con = sqlite3.connect(db)
    except sqlite3.Error:
        return []
    try:
        cur = con.execute(
            "SELECT kind, source, title, ref, substr(body, 1, 420) AS summary "
            "FROM recall_fts WHERE recall_fts MATCH ? LIMIT ?",
            (query, max_hits),
        )
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _read_body(path):
    # Bash reads the file via `sed`, which splits on \n only and leaves any \r in
    # place. newline="" disables Python's universal-newline translation so \r\n /
    # bare \r survive to _first_lines (store.read_text would collapse them to \n).
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def _first_lines(text, count=180):
    # Bash `body="$(sed -n '1,180p' file)"`: take the first `count` lines (sed splits
    # only on \n), then command substitution strips trailing newlines.
    return "\n".join(text.split("\n")[:count]).rstrip("\n")


def _jq_or(value, default):
    # jq `value // default`: substitute the default when value is null (None) or
    # false. An empty string / 0 is truthy in jq and passes through unchanged.
    return default if value is None or value is False else value


def _evidence_ref(row):
    # jq `(.evidence // []) | .[0] // ""`: first evidence entry, or "" when the list
    # is missing/empty/non-indexable or its first entry is null/false.
    evidence = _jq_or(row.get("evidence"), [])
    first = evidence[0] if isinstance(evidence, list) and evidence else None
    first = _jq_or(first, "")
    return "" if first == "" else str(first)


def _artifact_title(rel):
    # Bash awk -F/ '{print $2 " <middot> " substr($0, length($1 "/" $2 "/") + 1)}':
    # second path component, then everything after the first two components.
    parts = rel.split("/")
    second = parts[1] if len(parts) > 1 else ""
    prefix_len = len(parts[0]) + 1 + len(second) + 1  # length("$1/$2/")
    return second + " " + _MIDDOT + " " + rel[prefix_len:]


def _iter_run_artifacts(root, names=_ARTIFACT_NAMES):
    # Bash find: traverse $root/.kimiflow, prune the project dir, then yield regular
    # files whose basename is a matched name OR whose path is */findings/*.md. `names`
    # defaults to the index set (build_recall_index, Bash 2619); run_artifact_rows_json
    # passes the wider _RUN_ARTIFACT_NAMES (Bash 1668, +STATE.md).
    base = os.path.join(root, ".kimiflow")
    project = os.path.join(base, "project")
    matches = []
    for dirpath, dirnames, filenames in os.walk(base):
        if dirpath == project:
            dirnames[:] = []  # prune: do not descend into .kimiflow/project
            continue
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = paths.rel_path(root, full)
            if name in names or ("/findings/" in rel and rel.endswith(".md")):
                matches.append((rel, full))
    # find's native order is filesystem-dependent; sort for deterministic insertion
    # (observable only via fts_hits_json LIMIT, which has no ORDER BY).
    matches.sort()
    return matches


def build_recall_index(root, db_path):
    """Populate RECALL.sqlite from all memory sources. Port of Bash build_recall_index
    (2547-2621). Returns 2 when FTS5 is unavailable (mirrors `sqlite_available ||
    return 2`), else 0 after committing the rebuilt index."""
    if not fts5_available():
        return 2
    project = os.path.join(root, ".kimiflow", "project")
    memory = os.path.join(project, "MEMORY.md")
    user_memory = os.path.join(project, "USER.md")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    user_rows = os.path.join(project, "USER.jsonl")
    facts = os.path.join(project, "FACTS.jsonl")
    os.makedirs(project, exist_ok=True)

    con = sqlite3.connect(db_path)
    try:
        init_recall_db(con)

        if os.path.isfile(memory):
            body = _first_lines(_read_body(memory))
            insert_fts_row(con, "memory", ".kimiflow/project/MEMORY.md",
                           "Project Memory", body, ".kimiflow/project/MEMORY.md")
        if os.path.isfile(user_memory):
            body = _first_lines(_read_body(user_memory))
            insert_fts_row(con, "user_profile", ".kimiflow/project/USER.md",
                           "User Profile", body, ".kimiflow/project/USER.md")

        for row in store.read_jsonl(learnings):
            if _jq_or(row.get("status"), "current") != "current":
                continue
            title = "%s %s %s %s %s" % (
                _jq_or(row.get("topic"), "uncategorized"), _MIDDOT,
                _jq_or(row.get("kind"), "learning"), _MIDDOT, _jq_or(row.get("id"), ""))
            insert_fts_row(con, "learning", ".kimiflow/project/LEARNINGS.jsonl",
                           title, str(_jq_or(row.get("summary"), "")), _evidence_ref(row))

        for row in store.read_jsonl(user_rows):
            if _jq_or(row.get("status"), "current") != "current":
                continue
            title = "%s %s %s" % (
                _jq_or(row.get("topic"), "profile"), _MIDDOT, _jq_or(row.get("id"), ""))
            insert_fts_row(con, "user_profile", ".kimiflow/project/USER.jsonl",
                           title, str(_jq_or(row.get("summary"), "")), _evidence_ref(row))

        for row in store.read_jsonl(facts):
            inner = "%s %s %s" % (
                _jq_or(row.get("area"), "codebase"), _MIDDOT, _jq_or(row.get("path"), ""))
            title = "%s %s %s" % (_jq_or(row.get("kind"), "fact"), _MIDDOT, inner)
            ref = "%s:%s" % (_jq_or(row.get("path"), ""), str(_jq_or(row.get("line"), 1)))
            insert_fts_row(con, "fact", ".kimiflow/project/FACTS.jsonl",
                           title, str(_jq_or(row.get("summary"), "")), ref)

        for rel, full in _iter_run_artifacts(root):
            body = _first_lines(_read_body(full))
            insert_fts_row(con, "run_artifact", rel, _artifact_title(rel), body, rel)

        con.commit()
    finally:
        con.close()
    return 0


_WS_STRIP = " \t\r\f\v"                            # leading/trailing ASCII [[:space:]] (line has no \n)
_HEADING_RE = re.compile(r"#{1,6}[ \t\r\f\v]")     # ^#{1,6}[[:space:]] (ASCII, not Unicode \s)
_WS_RUN_RE = re.compile(r"[ \t\r\f\v]+")           # [[:space:]]+ collapse (ASCII)


def _artifact_summary(body):
    # Bash awk (1639-1651): first body line that, after stripping ASCII whitespace, is
    # non-empty, not a `#`x1-6 heading, not a ``` fence; collapse internal whitespace runs
    # to a single space; then cut -c1-420 (char-truncation, per the slugify `cut -c` convention).
    for raw in body.split("\n"):
        line = raw.strip(_WS_STRIP)
        if line == "":
            continue
        if _HEADING_RE.match(line):
            continue
        if line.startswith("```"):
            continue
        return _WS_RUN_RE.sub(" ", line)[:420]
    return ""


def run_artifact_rows_json(root):
    # Bash run_artifact_rows_json (1624-1670): one row per run-artifact file (sorted),
    # using the STATE.md-inclusive name set. Missing .kimiflow -> [].
    if not os.path.isdir(os.path.join(root, ".kimiflow")):
        return []
    rows = []
    for rel, full in _iter_run_artifacts(root, _RUN_ARTIFACT_NAMES):
        parts = rel.split("/")
        slug = parts[1] if len(parts) > 1 else ""     # awk -F/ '{print $2}'
        artifact = "/".join(parts[2:])                 # cut -d/ -f3-
        body = _first_lines(_read_body(full))
        rows.append({
            "kind": "run_artifact",
            "slug": slug,
            "artifact": artifact,
            "path": rel,
            "ref": rel,
            "title": slug + " " + _MIDDOT + " " + artifact,
            "summary": _artifact_summary(body),
            "text": body,
        })
    return rows


def run_artifact_hits_json(root, terms, max_hits):
    # Bash run_artifact_hits_json (1672-1685): keep rows whose slug+artifact+summary+text
    # matches any (non-empty) term (ascii_downcase substring), take the first max, drop `text`.
    matches = []
    for row in run_artifact_rows_json(root):
        blob = row["slug"] + " " + row["artifact"] + " " + row["summary"] + " " + row["text"]
        lowered = text.ascii_lower(blob)
        if any(term != "" and term in lowered for term in terms):
            matches.append(row)
    return [{k: v for k, v in row.items() if k != "text"} for row in matches[:max_hits]]
