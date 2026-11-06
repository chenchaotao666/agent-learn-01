"""Run with TEST_QDRANT_URL=http://127.0.0.1:6333 against Docker Qdrant."""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from src.config import Settings


@pytest.mark.skipif(not os.getenv('TEST_QDRANT_URL'), reason='Requires independent Qdrant server')
def test_real_server_upload_update_restart_delete(tmp_path):
    settings = Settings(database=str(tmp_path / 'server.sqlite3'),
                        qdrant_url=os.getenv('TEST_QDRANT_URL'),
                        qdrant_api_key=os.getenv('QDRANT_API_KEY', ''))
    app = create_app(settings)
    index = app.state.rag.index
    try:
        with TestClient(app) as client:
            assert client.get('/health').json()['qdrant_mode'] == 'server'
            response = client.post('/v1/documents/upload', files={
                'file': ('policy.txt', 'travel hotel budget 600', 'text/plain')})
            assert response.status_code == 200
            document_id = response.json()['id']
            response = client.post('/v1/documents/upload', data={'document_id': document_id},
                files={'file': ('policy.txt', 'travel hotel budget 800', 'text/plain')})
            assert response.status_code == 200
            hits = asyncio.run(app.state.rag.search('travel hotel budget'))
            assert len(hits) == 1 and hits[0]['version'] == 2
            assert '800' in hits[0]['text']
        app = create_app(settings)
        with TestClient(app) as client:
            hits = asyncio.run(app.state.rag.search('travel hotel budget'))
            assert len(hits) == 1 and hits[0]['version'] == 2
            assert client.delete(f'/v1/documents/{document_id}').status_code == 200
            assert app.state.rag.index.client.count(index.collection).count == 0
            assert asyncio.run(app.state.rag.search('travel hotel budget')) == []
    finally:
        if index.client.collection_exists(index.collection):
            index.client.delete_collection(index.collection)
        index.close()
