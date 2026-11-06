"""Versioned application records. All compare-and-swap mutations are transactional."""
import json
import uuid
from datetime import datetime, timezone


class Conflict(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, version INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(kind,id)
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, run_id TEXT UNIQUE NOT NULL,
                    draft_hash TEXT NOT NULL, payload TEXT NOT NULL
                );
            """)

    def create(self, kind, payload, record_id=None):
        rid = record_id or uuid.uuid4().hex
        value = {**payload, "id": rid, "version": 1, "created_at": now()}
        with self.store.connect() as db:
            db.execute("INSERT INTO records VALUES(?,?,?,?)", (kind, rid, 1, json.dumps(value, ensure_ascii=False)))
        return value

    def get(self, kind, rid):
        with self.store.connect() as db:
            row = db.execute("SELECT payload FROM records WHERE kind=? AND id=?", (kind, rid)).fetchone()
        if not row:
            raise KeyError(rid)
        return json.loads(row[0])

    def list(self, kind):
        with self.store.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM records WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def mutate(self, kind, rid, version, change):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version,payload FROM records WHERE kind=? AND id=?", (kind, rid)).fetchone()
            if not row:
                raise KeyError(rid)
            if row[0] != version:
                raise Conflict("对象已更新，请刷新后重试")
            value = json.loads(row[1])
            change(value)
            value["version"] += 1
            value["updated_at"] = now()
            db.execute("UPDATE records SET version=?,payload=? WHERE kind=? AND id=?",
                       (value["version"], json.dumps(value, ensure_ascii=False), kind, rid))
        return value

    def message(self, mid):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        if not row:
            raise KeyError(mid)
        result = dict(row)
        result["citations"] = json.loads(result["citations"])
        return result
