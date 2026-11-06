"""Re-embed current chunks without changing text, document IDs or citation IDs."""
import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from src.config import Settings
from src.store import Store
from src.rag.embedding import Embedder
from src.rag.vector_store import QdrantIndex


async def reembed(settings):
    store = Store(settings.database)
    embedder = Embedder(settings)
    index = QdrantIndex(settings, embedder.key)
    try:
        index.check_connection()
        with store.connect() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT c.*,d.title,d.filename,v.embedding_key FROM chunks c JOIN documents d "
                "ON c.document_id=d.id AND c.version=d.active_version JOIN versions v "
                "ON v.document_id=d.id AND v.version=d.active_version WHERE d.deleted=0 ORDER BY c.id")]
            backup = Path(settings.database).with_name('before-embedding-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
            with sqlite3.connect(backup) as target:
                db.backup(target)
        for start in range(0, len(rows), 4):
            batch = rows[start:start + 4]
            vectors = await embedder.encode([r['title'] + '\n' + r['text'] for r in batch])
            for row, vector in zip(batch, vectors, strict=True):
                row['vector'] = json.dumps(vector)
            print(f'Embedded {min(start + 4, len(rows))}/{len(rows)}', flush=True)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = {(r[0], r[1]) for r in db.execute(
                'SELECT c.id,v.embedding_key FROM chunks c JOIN documents d ON c.document_id=d.id '
                'AND c.version=d.active_version JOIN versions v ON v.document_id=d.id '
                'AND v.version=d.active_version WHERE d.deleted=0')}
            if current != {(r['id'], r['embedding_key']) for r in rows}:
                raise RuntimeError('Documents changed during migration; retry with uploads paused')
            index.upsert([{k: v for k, v in row.items() if k != 'embedding_key'} for row in rows])
            for row in rows:
                db.execute('UPDATE chunks SET vector=? WHERE id=?', (row['vector'], row['id']))
                db.execute('UPDATE versions SET embedding_key=? WHERE document_id=? AND version=?',
                           (embedder.key, row['document_id'], row['version']))
        print(json.dumps({'chunks': len(rows), 'collection': index.collection, 'backup': str(backup)}))
    finally:
        index.close()


if __name__ == '__main__':
    asyncio.run(reembed(replace(Settings.from_env(), embedding_mode='ollama',
                               embedding_model='qwen3-embedding:4b', timeout=180)))
