import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from src.config import Settings
from src.rag.embedding import Embedder, hash_vector
from src.rag.service import RagService
from src.rag.vector_store import VectorStoreError
from src.store import Store


def test_migrate_existing_sqlite_and_reopen(tmp_path):
    settings = Settings(qdrant_url="", database=str(tmp_path / 'legacy.sqlite3'))
    store = Store(settings.database)
    text = '差旅住宿报销上限为每晚600元'
    store.publish('差旅', 'travel.md', text,
                  [{'text': text, 'start_line': 1, 'end_line': 1}],
                  [hash_vector(text)], Embedder(settings).key)
    for _ in range(2):
        rag = RagService(store, Embedder(settings), 0.12)
        try:
            rag.initialize()
            assert rag.index.client.count(rag.index.collection).count == 1
            hits = asyncio.run(rag.search('差旅住宿报销上限'))
            assert len(hits) == 1 and hits[0]['filename'] == 'travel.md'
            assert 'vector' not in hits[0]
        finally:
            rag.close()


def test_upload_persists_vectors_and_failed_update_rolls_back(tmp_path, monkeypatch):
    settings = Settings(qdrant_url="", database=str(tmp_path / 'upload.sqlite3'))
    with TestClient(create_app(settings)) as client:
        response = client.post('/v1/documents/upload', files={
            'file': ('travel.txt', '差旅住宿标准每晚600元'.encode(), 'text/plain')})
        assert response.status_code == 200
        document_id = response.json()['id']
        rag = client.app.state.rag
        ids = rag.store.active_chunk_ids(rag.embedder.key)
        points = rag.index.client.retrieve(rag.index.collection, [str(UUID(ids[0]))], with_vectors=True)
        assert len(points[0].vector) == 1024
        assert points[0].payload['document_id'] == document_id
        original = rag.index.upsert
        def fail(rows):
            # Simulate Qdrant writing successfully then losing the acknowledgement.
            original(rows)
            raise VectorStoreError('write failed')
        monkeypatch.setattr(rag.index, 'upsert', fail)
        response = client.post('/v1/documents/upload',
            data={'document_id': document_id},
            files={'file': ('travel.txt', '差旅住宿标准每晚800元'.encode(), 'text/plain')})
        assert response.status_code == 503
        assert rag.store.documents()[0]['active_version'] == 1
        hits = asyncio.run(rag.search('差旅住宿标准'))
        assert len(hits) == 1 and '600' in hits[0]['text']
        monkeypatch.setattr(rag.index, 'upsert', original)
        assert client.delete(f'/v1/documents/{document_id}').status_code == 200
        assert rag.index.client.count(rag.index.collection).count == 0
        assert asyncio.run(rag.search('差旅住宿标准')) == []


def test_search_uses_qdrant_not_sqlite_vectors(tmp_path, monkeypatch):
    settings = Settings(qdrant_url="", database=str(tmp_path / 'search.sqlite3'))
    rag = RagService(Store(settings.database), Embedder(settings), 0.12)
    try:
        asyncio.run(rag.ingest('差旅', 'a.txt', '差旅住宿每晚600元'))
        def fail(*args):
            pytest.fail('Search must not load SQLite vectors after initialization')
        monkeypatch.setattr(rag.store, 'candidates', fail)
        assert asyncio.run(rag.search('差旅住宿'))
    finally:
        rag.close()
