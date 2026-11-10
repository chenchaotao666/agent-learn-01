import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT NOT NULL,
                    active_version INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS versions (
                    document_id TEXT NOT NULL, version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL, embedding_key TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(document_id, version)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, version INTEGER NOT NULL,
                    text TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
                    vector TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id, version);
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
                    citations TEXT NOT NULL DEFAULT '[]', mode TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def publish(self, title, filename, text, chunks, vectors, embedding_key, document_id=None, processing_key="fixed", publication=None):
        suffix = "" if processing_key == "fixed" else "\0" + processing_key
        digest = hashlib.sha256((title + "\0" + text + suffix).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if publication:
                publication(db, None)
            def finish(result):
                if publication:
                    publication(db, result)
                return result
            existing = None
            if document_id:
                existing = db.execute("SELECT * FROM documents WHERE id=? AND deleted=0", (document_id,)).fetchone()
                if not existing:
                    raise KeyError(document_id)
            else:
                existing = db.execute("""
                    SELECT d.* FROM documents d JOIN versions v
                    ON v.document_id=d.id AND v.version=d.active_version
                    WHERE d.deleted=0 AND v.content_hash=? AND v.embedding_key=?
                """, (digest, embedding_key)).fetchone()
            if existing:
                current = db.execute("SELECT * FROM versions WHERE document_id=? AND version=?",
                                     (existing["id"], existing["active_version"])).fetchone()
                if current["content_hash"] == digest and current["embedding_key"] == embedding_key:
                    return finish({"id": existing["id"], "version": existing["active_version"], "deduplicated": True})
                document_id = existing["id"]
                version = existing["active_version"] + 1
                db.execute("UPDATE documents SET title=?,filename=?,active_version=? WHERE id=?",
                           (title, filename, version, document_id))
            else:
                document_id = uuid.uuid4().hex
                version = 1
                db.execute("INSERT INTO documents(id,title,filename,active_version) VALUES(?,?,?,?)",
                           (document_id, title, filename, version))
            db.execute("INSERT INTO versions(document_id,version,content_hash,embedding_key) VALUES(?,?,?,?)",
                       (document_id, version, digest, embedding_key))
            for chunk, vector in zip(chunks, vectors, strict=True):
                db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?,?)", (
                    uuid.uuid4().hex, document_id, version, chunk["text"],
                    chunk["start_line"], chunk["end_line"], json.dumps(vector)))
            return finish({"id": document_id, "version": version, "deduplicated": False})

    def documents(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""
                SELECT d.*,v.embedding_key,v.created_at,COUNT(c.id) AS chunk_count
                FROM documents d JOIN versions v ON v.document_id=d.id AND v.version=d.active_version
                JOIN chunks c ON c.document_id=d.id AND c.version=d.active_version
                WHERE deleted=0 GROUP BY d.id ORDER BY v.created_at DESC,d.title
            """)]

    def delete(self, document_id):
        with self.connect() as db:
            return db.execute("UPDATE documents SET deleted=1 WHERE id=? AND deleted=0", (document_id,)).rowcount > 0

    def candidates(self, embedding_key):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""
                SELECT c.*,d.title,d.filename FROM chunks c
                JOIN documents d ON c.document_id=d.id AND c.version=d.active_version
                JOIN versions v ON v.document_id=d.id AND v.version=d.active_version
                WHERE d.deleted=0 AND v.embedding_key=?
            """, (embedding_key,))]

    def citation(self, chunk_id):
        with self.connect() as db:
            row = db.execute("""
                SELECT c.id,c.document_id,c.version,c.text,c.start_line,c.end_line,
                       d.title,d.filename,d.active_version
                FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE c.id=? AND d.deleted=0
            """, (chunk_id,)).fetchone()
            return dict(row) if row else None

    def create_conversation(self, title):
        conversation_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO conversations(id,title) VALUES(?,?)", (conversation_id, title[:50]))
        return conversation_id

    def conversations(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM conversations ORDER BY rowid DESC")]

    def messages(self, conversation_id):
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone():
                raise KeyError(conversation_id)
            rows = [dict(r) for r in db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,))]
        for row in rows:
            row["citations"] = json.loads(row["citations"])
            # Source previews are resolved live, so deleted documents are never served as citations.
            row["citations"] = [self.citation(cid) or {"id": cid, "unavailable": True} for cid in row["citations"]]
        return rows

    def add_message(self, conversation_id, role, content, status, mode, citations=()):
        with self.connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content,status,mode,citations) VALUES(?,?,?,?,?,?)",
                       (conversation_id, role, content, status, mode, json.dumps(list(citations))))
