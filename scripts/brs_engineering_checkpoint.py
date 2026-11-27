"""Transactional local Engineering Harness checkpoints (single SQLite database).

No GitHub writes, execution, or release authority. Keep DB outside tracked files.
"""
import json
import sqlite3
from contextlib import closing
from brs_engineering_loop import initial, advance
from brs_engineering_gate import blockers

def connect(path):
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, state TEXT NOT NULL)")
    return db

def read(path, task_id):
    with closing(connect(path)) as db:
        row = db.execute("SELECT revision,state FROM tasks WHERE id=?", (task_id,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (0, initial(task_id))

def apply(path, task_id, expected_revision, event):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid revision")
    with closing(connect(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT revision,state FROM tasks WHERE id=?", (task_id,)).fetchone()
            revision, state = (row[0], json.loads(row[1])) if row else (0, initial(task_id))
            if revision != expected_revision:
                raise ValueError("stale checkpoint revision")
            if state["stage"] == "VERIFY" and event.get("passed") is True:
                record = event.get("release_evidence")
                if not isinstance(record, dict) or record.get("candidate_sha") != event.get("sha") or blockers(record):
                    raise ValueError("missing or invalid release evidence")
                identities = [record[role]["identity"].strip().casefold() for role in ("tester", "audit", "architect")]
                if len(set(identities + [record["builder"].strip().casefold()])) != 4:
                    raise ValueError("review identities must be distinct")
            updated = advance(state, event)
            db.execute("INSERT INTO tasks(id,revision,state) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,state=excluded.state",
                       (task_id, revision+1, json.dumps(updated, sort_keys=True)))
            db.execute("COMMIT")
            return revision+1, updated
        except BaseException:
            db.execute("ROLLBACK")
            raise
