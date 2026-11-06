from src.rag.chunking import split_text
from src.rag.embedding import Embedder
from src.store import Store
from src.rag.vector_store import QdrantIndex


class RagService:
    def __init__(self, store: Store, embedder: Embedder, min_score: float):
        self.store, self.embedder, self.min_score = store, embedder, min_score
        self.index = QdrantIndex(embedder.settings, embedder.key)
        self._initialized = False

    def initialize(self):
        if not self._initialized:
            self.index.check_connection()
            # Idempotent migration/recovery of existing SQLite vectors; no model calls.
            self.index.upsert(self.store.candidates(self.embedder.key))
            self._initialized = True

    def close(self):
        self.index.close()
        self._initialized = False

    def delete(self, document_id):
        self.initialize()
        self.index.delete_document(document_id)
        return self.store.delete(document_id)

    async def ingest(self, title, filename, text, document_id=None):
        self.initialize()
        chunks = split_text(text)
        if not chunks:
            raise ValueError("文档内容不能为空")
        vectors = await self.embedder.encode([title + "\n" + c["text"] for c in chunks])
        return self.store.publish(title, filename, text, chunks, vectors, self.embedder.key,
                                  document_id, before_commit=self.index.upsert)

    async def search(self, query, top_k=3):
        self.initialize()
        active_ids = self.store.active_chunk_ids(self.embedder.key)
        if not active_ids:
            return []
        vector = await self.embedder.encode_query(query)
        return self.index.search(vector, active_ids, top_k, self.min_score)
