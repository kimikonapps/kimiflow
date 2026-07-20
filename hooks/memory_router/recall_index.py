"""RECALL.sqlite FTS5 engine: availability probe, schema init, row insert, term ->
MATCH-query construction, and the hit query with graceful degradation. Behavioral
port of the Bash sqlite_available / fts_query_from_terms / insert_fts_row / the
recall schema / fts_hits_json at kimiflow--v0.1.50 (2527-2644). Uses the Python
stdlib `sqlite3` module instead of shelling to the `sqlite3` CLI."""
import hashlib
import heapq
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile

from . import clock, paths, store, text

# Source of truth: Bash 2562-2563.
_SCHEMA = (
    "DROP TABLE IF EXISTS recall_meta;\n"
    "DROP TABLE IF EXISTS recall_fts;\n"
    "CREATE TABLE recall_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);\n"
    "CREATE VIRTUAL TABLE recall_fts USING fts5(kind, source, title, body, ref);"
)

INDEX_SCHEMA_VERSION = 2
ARTIFACT_BODY_CHAR_LIMIT = 65536
JSONL_ROW_BYTE_LIMIT = 262144

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


class IndexValidationUnavailable(Exception):
    """The derived index could not be checked because local validation was unavailable."""


def _validation_unavailable(error):
    code = getattr(error, "sqlite_errorcode", None)
    primary = code & 0xFF if isinstance(code, int) else None
    unavailable_codes = {
        getattr(sqlite3, name) for name in (
            "SQLITE_BUSY", "SQLITE_LOCKED", "SQLITE_NOMEM", "SQLITE_READONLY",
            "SQLITE_INTERRUPT", "SQLITE_IOERR", "SQLITE_FULL", "SQLITE_CANTOPEN",
            "SQLITE_PROTOCOL", "SQLITE_PERM",
        ) if hasattr(sqlite3, name)
    }
    if primary in unavailable_codes:
        return True
    message = str(error).lower()
    return any(fragment in message for fragment in (
        "unable to open", "database is locked", "database table is locked",
        "disk i/o", "database or disk is full", "out of memory", "readonly",
    ))


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


def init_recall_db(con, corpus_fingerprint="sha256:unknown"):
    # Recreate the derived schema and bind it to the application-owned schema/corpus.
    # Caller must confirm fts5_available() first (the CREATE VIRTUAL TABLE here
    # would raise sqlite3.OperationalError otherwise).
    con.executescript(_SCHEMA)
    con.executemany(
        "INSERT INTO recall_meta(key, value) VALUES(?, ?)",
        (
            ("updated_at", clock.iso_now()),
            ("schema_version", str(INDEX_SCHEMA_VERSION)),
            ("corpus_fingerprint", corpus_fingerprint),
            ("content_fingerprint", "sha256:unsealed"),
            ("index_fingerprint", "sha256:unsealed"),
        ),
    )


def insert_fts_row(con, kind, source, title, body, ref):
    # Bash 2542-2545 uses sql_quote string interpolation; the stdlib module binds
    # parameters instead (equivalent result, no quoting bugs).
    con.execute(
        "INSERT INTO recall_fts(kind, source, title, body, ref) VALUES(?, ?, ?, ?, ?)",
        (kind, source, title, body, ref),
    )


def _update_fingerprint(digest, value):
    if value is None:
        encoded = b"N"
    elif isinstance(value, bytes):
        encoded = b"B" + value
    else:
        encoded = b"T" + str(value).encode("utf-8", "surrogateescape")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _rows_fingerprint(label, rows):
    digest = hashlib.sha256()
    digest.update(label + b"\0")
    for row in rows:
        for value in row:
            _update_fingerprint(digest, value)
    return "sha256:" + digest.hexdigest()


def _content_fingerprint(rows):
    return _rows_fingerprint(b"kimiflow-recall-content:1", rows)


def _fts_content_fingerprint(con):
    return _content_fingerprint(con.execute(
        "SELECT rowid, kind, source, title, body, ref FROM recall_fts ORDER BY rowid"
    ))


def _fts_index_fingerprint(con):
    return _fts_semantic_fingerprint(
        con, "main", "recall_fts", "recall_actual_vocab")


def _fts_semantic_fingerprint(con, schema, table, vocab):
    con.execute("DROP TABLE IF EXISTS temp.%s" % vocab)
    con.execute(
        "CREATE VIRTUAL TABLE temp.%s USING fts5vocab(%s, %s, instance)"
        % (vocab, schema, table)
    )

    def rows():
        yield ("postings",)
        yield from con.execute(
            "SELECT term, doc, col, offset FROM temp.%s "
            "ORDER BY term, doc, col, offset" % vocab
        )
        yield ("averages",)
        yield from con.execute(
            "SELECT id, block FROM %s.%s_data WHERE id=1 ORDER BY id"
            % (schema, table)
        )
        yield ("docsize",)
        yield from con.execute(
            "SELECT id, sz FROM %s.%s_docsize ORDER BY id" % (schema, table)
        )
        yield ("config",)
        yield from con.execute(
            "SELECT k, v FROM %s.%s_config ORDER BY k" % (schema, table)
        )

    return _rows_fingerprint(b"kimiflow-recall-index-semantics:1", rows())


def _expected_fts_fingerprint(con):
    con.execute("DROP TABLE IF EXISTS temp.recall_expected_vocab")
    con.execute("DROP TABLE IF EXISTS temp.recall_expected")
    con.execute(
        "CREATE VIRTUAL TABLE temp.recall_expected USING "
        "fts5(kind, source, title, body, ref)"
    )
    con.execute(
        "INSERT INTO temp.recall_expected(rowid, kind, source, title, body, ref) "
        "SELECT rowid, kind, source, title, body, ref FROM main.recall_fts "
        "ORDER BY rowid"
    )
    return _fts_semantic_fingerprint(
        con, "temp", "recall_expected", "recall_expected_vocab")


def seal_recall_index(con):
    con.executemany(
        "UPDATE recall_meta SET value=? WHERE key=?",
        (
            (_fts_content_fingerprint(con), "content_fingerprint"),
            (_expected_fts_fingerprint(con), "index_fingerprint"),
        ),
    )


def fts_query_from_terms(terms):
    # Bash 2531-2540 (jq): strip each term to [A-Za-z0-9_], keep length >= 3,
    # `unique` (jq sorts + dedups), quote each, join with " OR ".
    cleaned = {_NON_TERM.sub("", str(term)) for term in terms}
    kept = sorted(t for t in cleaned if len(t) >= 3)
    return " OR ".join('"' + t + '"' for t in kept)


def _read_body(path):
    # Bash reads the file via `sed`, which splits on \n only and leaves any \r in
    # place. newline="" disables Python's universal-newline translation so \r\n /
    # bare \r survive to _first_lines (store.read_text would collapse them to \n).
    data = ""
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            while True:
                chunk = handle.read(min(
                    65536, max(1, ARTIFACT_BODY_CHAR_LIMIT - len(data) + 1)))
                if not chunk:
                    return data
                data += chunk
                parts = data.split("\n")
                if len(parts) > 180:
                    return "\n".join(parts[:180]).rstrip("\n")
                if len(data) > ARTIFACT_BODY_CHAR_LIMIT:
                    return data[:ARTIFACT_BODY_CHAR_LIMIT]
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


def corpus_fingerprint(root):
    """Hash every source that can affect the derived FTS rows."""
    inputs = _corpus_inputs(root)

    digest = hashlib.sha256()
    digest.update(("kimiflow-recall-index:%s\0" % INDEX_SCHEMA_VERSION).encode("ascii"))
    for rel, full in inputs:
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        try:
            with open(full, "rb") as handle:
                while True:
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _corpus_inputs(root):
    project = os.path.join(root, ".kimiflow", "project")
    inputs = []
    for name in ("MEMORY.md", "USER.md", "LEARNINGS.jsonl", "USER.jsonl", "FACTS.jsonl"):
        full = os.path.join(project, name)
        if os.path.isfile(full):
            inputs.append((paths.rel_path(root, full), full))
    inputs.extend(_iter_run_artifacts(root))
    inputs.sort(key=lambda item: item[0])
    return inputs


def _snapshot_corpus(root, snapshot_root):
    os.makedirs(os.path.join(snapshot_root, ".kimiflow", "project"), exist_ok=True)
    digest = hashlib.sha256()
    digest.update(("kimiflow-recall-index:%s\0" % INDEX_SCHEMA_VERSION).encode("ascii"))
    for rel, full in _corpus_inputs(root):
        destination = os.path.join(snapshot_root, *rel.split("/"))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        digest.update(rel.encode("utf-8", "surrogateescape"))
        digest.update(b"\0")
        with open(full, "rb") as source, open(destination, "wb") as target:
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                digest.update(chunk)
                target.write(chunk)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _iter_jsonl_objects(path):
    try:
        with open(path, "rb") as handle:
            while True:
                raw = handle.readline(JSONL_ROW_BYTE_LIMIT + 1)
                if not raw:
                    break
                oversized = len(raw) > JSONL_ROW_BYTE_LIMIT
                while oversized and raw and not raw.endswith(b"\n"):
                    raw = handle.readline(JSONL_ROW_BYTE_LIMIT + 1)
                if oversized:
                    continue
                try:
                    row = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, RecursionError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _iter_index_rows(root):
    project = os.path.join(root, ".kimiflow", "project")
    memory = os.path.join(project, "MEMORY.md")
    user_memory = os.path.join(project, "USER.md")
    learnings = os.path.join(project, "LEARNINGS.jsonl")
    user_rows = os.path.join(project, "USER.jsonl")
    facts = os.path.join(project, "FACTS.jsonl")

    if os.path.isfile(memory):
        yield ("memory", ".kimiflow/project/MEMORY.md", "Project Memory",
               _first_lines(_read_body(memory)), ".kimiflow/project/MEMORY.md")
    if os.path.isfile(user_memory):
        yield ("user_profile", ".kimiflow/project/USER.md", "User Profile",
               _first_lines(_read_body(user_memory)), ".kimiflow/project/USER.md")

    for row in _iter_jsonl_objects(learnings):
        if _jq_or(row.get("status"), "current") != "current":
            continue
        title = "%s %s %s %s %s" % (
            _jq_or(row.get("topic"), "uncategorized"), _MIDDOT,
            _jq_or(row.get("kind"), "learning"), _MIDDOT, _jq_or(row.get("id"), ""))
        yield ("learning", ".kimiflow/project/LEARNINGS.jsonl", title,
               str(_jq_or(row.get("summary"), "")), _evidence_ref(row))

    for row in _iter_jsonl_objects(user_rows):
        if _jq_or(row.get("status"), "current") != "current":
            continue
        title = "%s %s %s" % (
            _jq_or(row.get("topic"), "profile"), _MIDDOT, _jq_or(row.get("id"), ""))
        yield ("user_profile", ".kimiflow/project/USER.jsonl", title,
               str(_jq_or(row.get("summary"), "")), _evidence_ref(row))

    for row in _iter_jsonl_objects(facts):
        inner = "%s %s %s" % (
            _jq_or(row.get("area"), "codebase"), _MIDDOT, _jq_or(row.get("path"), ""))
        title = "%s %s %s" % (_jq_or(row.get("kind"), "fact"), _MIDDOT, inner)
        ref = "%s:%s" % (_jq_or(row.get("path"), ""), str(_jq_or(row.get("line"), 1)))
        yield ("fact", ".kimiflow/project/FACTS.jsonl", title,
               str(_jq_or(row.get("summary"), "")), ref)

    for rel, full in _iter_run_artifacts(root):
        yield ("run_artifact", rel, _artifact_title(rel),
               _first_lines(_read_body(full)), rel)


def _source_content_fingerprint(root):
    return _content_fingerprint(
        (rowid,) + row for rowid, row in enumerate(_iter_index_rows(root), start=1)
    )


def _meta_from_connection(con):
    try:
        meta = dict(con.execute("SELECT key, value FROM recall_meta").fetchall())
        schema_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='recall_fts'"
        ).fetchone()
        schema_sql = schema_row[0] if schema_row else ""
        expected_schema = (
            "CREATE VIRTUAL TABLE recall_fts USING "
            "fts5(kind, source, title, body, ref)"
        )
        if " ".join((schema_sql or "").split()).lower() != expected_schema.lower():
            return None
        expected_shadows = {
            "recall_fts_data", "recall_fts_idx", "recall_fts_content",
            "recall_fts_docsize", "recall_fts_config",
        }
        actual_shadows = {
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'recall_fts_%'"
            ).fetchall()
        }
        if not expected_shadows.issubset(actual_shadows):
            return None
        con.execute("SELECT kind, source, title, body, ref FROM recall_fts LIMIT 0")
        expected_content = meta.get("content_fingerprint")
        if (not expected_content
                or expected_content != _fts_content_fingerprint(con)):
            return None
        expected_index = meta.get("index_fingerprint")
        actual_index = _fts_index_fingerprint(con)
        if not expected_index or expected_index != actual_index:
            return None
        return meta
    except IndexValidationUnavailable:
        raise
    except sqlite3.Error as error:
        if _validation_unavailable(error):
            raise IndexValidationUnavailable() from error
        return None


def _inspect_index(root, query="", max_hits=0):
    """Validate and optionally query one database handle against current sources."""
    if not fts5_available():
        return {"status": "unavailable", "reason": "fts5_unavailable"}, []
    db_path = recall_db_path(root)
    if not os.path.isfile(db_path):
        return {"status": "missing", "reason": "index_missing"}, []
    snapshot = ""
    try:
        snapshot = tempfile.mkdtemp(prefix="kimiflow-recall-read.")
        snapshot_fingerprint = _snapshot_corpus(root, snapshot)
        if corpus_fingerprint(root) != snapshot_fingerprint:
            return {"status": "stale", "reason": "corpus_changed_during_read"}, []
        try:
            con = sqlite3.connect(db_path)
        except sqlite3.Error:
            return {"status": "unavailable", "reason": "index_open_failed"}, []
        try:
            meta = _meta_from_connection(con)
            if not isinstance(meta, dict):
                return {"status": "corrupt", "reason": "metadata_unreadable"}, []
            if meta.get("schema_version") != str(INDEX_SCHEMA_VERSION):
                return {"status": "stale", "reason": "schema_version_mismatch"}, []
            if meta.get("corpus_fingerprint") != snapshot_fingerprint:
                return {"status": "stale", "reason": "corpus_fingerprint_mismatch"}, []

            source_content = _source_content_fingerprint(snapshot)
            if meta.get("content_fingerprint") != source_content:
                return {"status": "corrupt", "reason": "content_source_mismatch"}, []
            hits = []
            if query:
                cur = con.execute(
                    "SELECT kind, source, title, ref, substr(body, 1, 420) AS summary "
                    "FROM recall_fts WHERE recall_fts MATCH ? "
                    "ORDER BY bm25(recall_fts), rowid LIMIT ?",
                    (query, max_hits),
                )
                columns = [description[0] for description in cur.description]
                hits = [dict(zip(columns, row)) for row in cur.fetchall()]

            if corpus_fingerprint(root) != snapshot_fingerprint:
                return {"status": "stale", "reason": "corpus_changed_during_read"}, []
            return {"status": "fresh", "reason": "fingerprint_match"}, hits
        finally:
            con.close()
    except IndexValidationUnavailable:
        return {"status": "unavailable", "reason": "index_validation_unavailable"}, []
    except sqlite3.Error as error:
        if _validation_unavailable(error):
            return {
                "status": "unavailable",
                "reason": "index_validation_unavailable",
            }, []
        return {"status": "corrupt", "reason": "metadata_unreadable"}, []
    except (OSError, ValueError):
        return {"status": "unavailable", "reason": "source_validation_unavailable"}, []
    finally:
        if snapshot:
            shutil.rmtree(snapshot, ignore_errors=True)


def index_state(root):
    """Return fail-closed freshness for the optional derived index."""
    return _inspect_index(root)[0]


def fts_hits_with_state(root, terms, max_hits):
    query = fts_query_from_terms(terms)
    state, hits = _inspect_index(root, query, max_hits)
    return hits, state


def fts_hits_json(root, terms, max_hits):
    return fts_hits_with_state(root, terms, max_hits)[0]


def _populate_recall_index(root, db_path, fingerprint):
    con = sqlite3.connect(db_path)
    try:
        init_recall_db(con, fingerprint)
        for row in _iter_index_rows(root):
            insert_fts_row(con, *row)
        con.commit()
        seal_recall_index(con)
        con.commit()
    finally:
        con.close()


def _database_matches_snapshot(db_path, snapshot_root, snapshot_fingerprint):
    try:
        con = sqlite3.connect(db_path)
    except sqlite3.Error:
        return False
    try:
        meta = _meta_from_connection(con)
        return bool(
            isinstance(meta, dict)
            and meta.get("schema_version") == str(INDEX_SCHEMA_VERSION)
            and meta.get("corpus_fingerprint") == snapshot_fingerprint
            and meta.get("content_fingerprint")
            == _source_content_fingerprint(snapshot_root)
        )
    except (OSError, sqlite3.Error, ValueError, IndexValidationUnavailable):
        return False
    finally:
        con.close()


def _backup_database(db_path, directory):
    if not os.path.lexists(db_path):
        return ""
    descriptor, backup = tempfile.mkstemp(prefix=".RECALL.sqlite.backup.", dir=directory)
    os.close(descriptor)
    os.unlink(backup)
    os.link(db_path, backup, follow_symlinks=False)
    return backup


def recall_backup_paths(root):
    """Return regular recovery-backup paths oldest-first without following symlinks."""
    directory = os.path.dirname(recall_db_path(root))
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    backups = []
    for name in names:
        if not name.startswith(".RECALL.sqlite.backup."):
            continue
        candidate = os.path.join(directory, name)
        try:
            info = os.stat(candidate, follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            backups.append((info.st_mtime_ns, name, candidate))
    backups.sort()
    return [candidate for _, _, candidate in backups]


def last_recall_backup(root):
    backups = recall_backup_paths(root)
    return backups[-1] if backups else ""


def _restore_database(db_path, backup, had_original):
    try:
        if had_original:
            os.replace(backup, db_path)
        elif os.path.lexists(db_path):
            os.unlink(db_path)
        return True
    except OSError:
        return False


def build_recall_index(root, db_path):
    """Populate RECALL.sqlite from all memory sources. Port of Bash build_recall_index
    (2547-2621). Returns 2 when FTS5 is unavailable (mirrors `sqlite_available ||
    return 2`), else 0 after committing the rebuilt index."""
    if not fts5_available():
        return 2
    project = os.path.join(root, ".kimiflow", "project")
    directory = os.path.dirname(db_path) or project
    try:
        os.makedirs(project, exist_ok=True)
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return 1
    orphan_backups = recall_backup_paths(root)
    backup = ""
    had_original = os.path.lexists(db_path)
    installed_intermediate = False
    preserve_backup = False
    build_succeeded = False

    def restore_intermediate():
        nonlocal installed_intermediate, preserve_backup
        if not installed_intermediate:
            return True
        if _restore_database(db_path, backup, had_original):
            installed_intermediate = False
            return True
        preserve_backup = True
        return False

    try:
        for attempt in range(2):
            descriptor = None
            temporary = ""
            snapshot = ""
            try:
                snapshot = tempfile.mkdtemp(prefix=".RECALL.corpus.tmp.", dir=directory)
                snapshot_fingerprint = _snapshot_corpus(root, snapshot)
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".RECALL.sqlite.tmp.", dir=directory)
                os.close(descriptor)
                descriptor = None
                _populate_recall_index(snapshot, temporary, snapshot_fingerprint)
                if not _database_matches_snapshot(
                        temporary, snapshot, snapshot_fingerprint):
                    return 1 if restore_intermediate() else 4
                if corpus_fingerprint(root) != snapshot_fingerprint:
                    if attempt == 0:
                        continue
                    return 3 if restore_intermediate() else 4
                if had_original and not backup:
                    backup = _backup_database(db_path, directory)
                os.chmod(temporary, 0o600)
                os.replace(temporary, db_path)
                temporary = ""
                installed_intermediate = True
                if corpus_fingerprint(root) == snapshot_fingerprint:
                    build_succeeded = True
                    return 0
                if attempt == 1:
                    return 3 if restore_intermediate() else 4
            except (OSError, sqlite3.Error, ValueError):
                return 1 if restore_intermediate() else 4
            finally:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                try:
                    if temporary:
                        os.unlink(temporary)
                except OSError:
                    pass
                if snapshot:
                    shutil.rmtree(snapshot, ignore_errors=True)
        return 3 if restore_intermediate() else 4
    finally:
        if backup and not preserve_backup:
            try:
                os.unlink(backup)
            except OSError:
                pass
        if build_succeeded:
            for orphan in orphan_backups:
                try:
                    os.unlink(orphan)
                except OSError:
                    pass


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


def _run_artifact_row_iter(root):
    for rel, full in _iter_run_artifacts(root, _RUN_ARTIFACT_NAMES):
        parts = rel.split("/")
        slug = parts[1] if len(parts) > 1 else ""
        artifact = "/".join(parts[2:])
        body = _first_lines(_read_body(full))
        yield {
            "kind": "run_artifact",
            "slug": slug,
            "artifact": artifact,
            "path": rel,
            "ref": rel,
            "title": slug + " " + _MIDDOT + " " + artifact,
            "summary": _artifact_summary(body),
            "text": body,
        }


def run_artifact_rows_json(root):
    # Bash run_artifact_rows_json (1624-1670): one row per run-artifact file (sorted),
    # using the STATE.md-inclusive name set. Missing .kimiflow -> [].
    if not os.path.isdir(os.path.join(root, ".kimiflow")):
        return []
    return list(_run_artifact_row_iter(root))


def run_artifact_hits_json(root, terms, max_hits):
    # Bash run_artifact_hits_json (1672-1685): keep rows whose slug+artifact+summary+text
    # matches any (non-empty) term (ascii_downcase substring), take the first max, drop `text`.
    matches = []
    for row in _run_artifact_row_iter(root):
        blob = row["slug"] + " " + row["artifact"] + " " + row["summary"] + " " + row["text"]
        lowered = text.ascii_lower(blob)
        if any(term != "" and term in lowered for term in terms):
            matches.append(row)
            if max_hits is not None and len(matches) >= max_hits:
                break
    chosen = matches if max_hits is None else matches[:max_hits]
    return [{k: v for k, v in row.items() if k != "text"} for row in chosen]


def ranked_run_artifact_hits_json(root, terms, max_hits):
    """Return a relevance-ranked history window without retaining every artifact body."""
    if max_hits <= 0:
        return []
    best = []
    sequence = 0
    for row in _run_artifact_row_iter(root):
        blob = row["slug"] + " " + row["artifact"] + " " + row["summary"] + " " + row["text"]
        lowered = text.ascii_lower(blob)
        matched = {term for term in terms if term != "" and term in lowered}
        if not matched:
            continue
        item = (len(matched), -sequence, sequence, row)
        sequence += 1
        if len(best) < max_hits:
            heapq.heappush(best, item)
        elif item[:2] > best[0][:2]:
            heapq.heapreplace(best, item)
    best.sort(key=lambda item: (-item[0], item[2]))
    return [{k: v for k, v in item[3].items() if k != "text"} for item in best]
