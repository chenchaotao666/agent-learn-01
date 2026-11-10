"""Durable import queue with leased claims and atomic publication/completion."""
import asyncio
import hashlib
import json
import time
import uuid

from src.repository import Conflict


class Ingestions:
    def __init__(self, store, rag):
        self.store, self.rag = store, rag
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS ingestions (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                owner TEXT, lease_until REAL, result TEXT, error TEXT, created_at REAL NOT NULL)""")

    def create(self, body):
        payload = {**body.model_dump(exclude={"client_request_id"}), "embedding_key": self.rag.embedder.key}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        rid = hashlib.sha256(body.client_request_id.encode()).hexdigest()[:32]
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT fingerprint FROM ingestions WHERE id=?", (rid,)).fetchone()
            if existing and existing[0] != fingerprint:
                raise Conflict("同一请求编号不能用于不同入库内容")
            if not existing:
                db.execute("INSERT INTO ingestions(id,fingerprint,payload,status,created_at) VALUES(?,?,?,'queued',?)",
                           (rid, fingerprint, json.dumps(payload, ensure_ascii=False), time.time()))
        return self.get(rid)

    def get(self, rid):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM ingestions WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(rid)
        value = dict(row)
        payload = json.loads(value.pop("payload"))
        value.pop("owner")
        value.update(title=payload["title"], filename=payload["filename"], strategy=payload["strategy"],
                     result=json.loads(value["result"]) if value["result"] else None)
        return value

    def list(self):
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM ingestions ORDER BY created_at DESC LIMIT 100")]
        return [self.get(rid) for rid in ids]

    def change(self, rid, action):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM ingestions WHERE id=?", (rid,)).fetchone()
            if not row:
                raise KeyError(rid)
            if action == "retry":
                if row[0] != "failed":
                    raise Conflict("仅失败任务可重试")
                db.execute("UPDATE ingestions SET status='queued',attempts=0,owner=NULL,lease_until=NULL,error=NULL WHERE id=?", (rid,))
            else:
                if row[0] == "published":
                    raise Conflict("已发布文档不能通过取消入库删除")
                db.execute("UPDATE ingestions SET status='cancelled',owner=NULL,lease_until=NULL WHERE id=?", (rid,))
        return self.get(rid)

    def claim(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE ingestions SET status='failed',error='重试次数耗尽' WHERE status='running' AND lease_until<? AND attempts>=3", (time.time(),))
            row = db.execute("SELECT * FROM ingestions WHERE status='queued' OR (status='running' AND lease_until<? AND attempts<3) ORDER BY created_at LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return None
            owner = uuid.uuid4().hex
            db.execute("UPDATE ingestions SET status='running',owner=?,lease_until=?,attempts=attempts+1,error=NULL WHERE id=?",
                       (owner, time.time() + self.rag.settings.timeout + 30, row["id"]))
            return row["id"], owner, json.loads(row["payload"])

    async def work_once(self):
        claim = self.claim()
        if not claim:
            return False
        rid, owner, payload = claim
        def publication(db, result):
            row = db.execute("SELECT status,owner,lease_until FROM ingestions WHERE id=?", (rid,)).fetchone()
            if row[0] != "running" or row[1] != owner or row[2] < time.time():
                raise Conflict("入库租约失效或已取消，不能发布")
            if result is not None:
                db.execute("UPDATE ingestions SET status='published',result=?,owner=NULL,lease_until=NULL WHERE id=?",
                           (json.dumps(result), rid))
        try:
            if payload.pop("embedding_key") != self.rag.embedder.key:
                raise ValueError("Worker 的向量配置与入队时不同")
            async with asyncio.timeout(self.rag.settings.timeout):
                await self.rag.ingest(**payload, publication=publication)
        except Exception as exc:
            with self.store.connect() as db:
                db.execute("UPDATE ingestions SET status='failed',error=?,owner=NULL,lease_until=NULL WHERE id=? AND owner=? AND status='running'",
                           (f"{type(exc).__name__}：入库失败，检查向量配置或文档后重试", rid, owner))
        return True
