import asyncio
from dataclasses import replace

import pytest

from scripts.reembed_documents import reembed
from src.config import Settings
from src.store import Store
from src.rag.embedding import Embedder, hash_vector


def test_reembedding_preserves_citations_and_failure_preserves_old_vectors(tmp_path, monkeypatch):
    settings = Settings(database=str(tmp_path / 'db.sqlite3'), qdrant_url='')
    store = Store(settings.database)
    old_key = Embedder(settings).key
    store.publish('title', 'a.txt', 'source text',
                  [{'text': 'source text', 'start_line': 1, 'end_line': 1}],
                  [hash_vector('source text')], old_key)
    before = store.candidates(old_key)
    settings = replace(settings, embedding_mode='ollama', embedding_model='qwen3-embedding:4b')
    async def fail(self, texts):
        raise RuntimeError('embedding unavailable')
    monkeypatch.setattr(Embedder, 'encode', fail)
    with pytest.raises(RuntimeError, match='unavailable'):
        asyncio.run(reembed(settings))
    assert store.candidates(old_key) == before
    async def encode(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]
    monkeypatch.setattr(Embedder, 'encode', encode)
    asyncio.run(reembed(settings))
    after = store.candidates(Embedder(settings).key)
    assert len(after) == 1
    for key in ['id', 'document_id', 'version', 'text', 'start_line', 'end_line']:
        assert before[0][key] == after[0][key]
    assert store.citation(before[0]['id'])['text'] == 'source text'
    assert store.candidates(old_key) == []
