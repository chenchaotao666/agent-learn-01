"""Idempotently migrate published SQLite vectors to the configured Qdrant server."""
import json

from src.config import Settings
from src.rag.embedding import Embedder
from src.rag.service import RagService
from src.store import Store


def main():
    settings = Settings.from_env()
    rag = RagService(Store(settings.database), Embedder(settings), settings.min_score)
    try:
        rag.initialize()
        ids = rag.store.active_chunk_ids(rag.embedder.key)
        count = rag.index.client.count(rag.index.collection, exact=True).count if ids else 0
        print(json.dumps({"vector_store": "qdrant", "mode": "server",
                          "collection": rag.index.collection,
                          "active_chunks": len(ids), "stored_points": count}, ensure_ascii=False))
    finally:
        rag.close()


if __name__ == '__main__':
    main()
