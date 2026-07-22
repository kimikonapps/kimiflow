"""`index` subcommand: build / inspect the RECALL.sqlite FTS index. Behavioral port
of the Bash cmd_index @ kimiflow--v0.1.50 (3988-4030). need_jq is a no-op (the port
uses no jq, spec 12)."""
import os
import sqlite3

from . import contracts, recall_index
from .cli import die, resolve_root, usage

_PATH = ".kimiflow/project/RECALL.sqlite"


def _document_count(db):
    # Bash: sqlite3 "$db" 'SELECT count(*) FROM recall_fts;' 2>/dev/null || printf '0'
    try:
        con = sqlite3.connect(db)
    except sqlite3.Error:
        return 0
    try:
        return con.execute("SELECT count(*) FROM recall_fts").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def run(argv):
    root = ""
    pretty = False
    write = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--root":
            i += 1
            root = argv[i] if i < len(argv) else ""
        elif arg == "--write":
            write = True
        elif arg == "--pretty":
            pretty = True
        elif arg in ("--help", "-h"):
            usage()
            return 0
        else:
            return die("index: unknown argument: %s" % arg, 2)
        i += 1

    root = resolve_root(root)
    db = recall_index.recall_db_path(root)

    # Bash gates on sqlite_available (CLI); the port probes the stdlib module's FTS5.
    if not recall_index.fts5_available():
        contracts.json_print({
            "schema_version": 1,
            "status": "unavailable",
            "path": _PATH,
            "sqlite_available": False,
            "documents": 0,
        }, pretty)
        return 0

    status = "preview"
    result = 0
    if write:
        result = recall_index.build_recall_index(root, db)
        if result == 0 and recall_index.index_state(root)["status"] != "fresh":
            result = 3
        status = "indexed" if result == 0 else "failed"
    elif os.path.isfile(db):
        freshness = recall_index.index_state(root)["status"]
        status = "available" if freshness == "fresh" else freshness

    documents = (_document_count(db)
                 if os.path.isfile(db) and status in ("available", "indexed") else 0)

    payload = {
        "schema_version": 1,
        "status": status,
        "path": _PATH,
        "written": write and result == 0,
        "sqlite_available": True,
        "documents": documents,
    }
    if result == 4:
        payload["reason"] = "restore_failed"
        backup = recall_index.last_recall_backup(root)
        if backup:
            payload["recovery_backup"] = os.path.relpath(backup, root).replace(os.sep, "/")
    contracts.json_print(payload, pretty)
    return 0 if result == 0 else 1
