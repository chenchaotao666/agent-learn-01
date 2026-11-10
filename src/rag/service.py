import json
import asyncio

from src.rag.chunking import split_text
from src.rag.embedding import Embedder
from src.store import Store
from src.rag.vector_store import QdrantIndex
from src.rag.ranking import bm25, fuse
from src.rag.refinement import rewrite_query, rerank


class RagService:
    def __init__(self, store: Store, embedder: Embedder, min_score: float):
        self.store, self.embedder, self.min_score = store, embedder, min_score
        self.settings = embedder.settings
        self.use_qdrant = self.settings.vector_backend == "qdrant"
        self.index = QdrantIndex(self.settings, embedder.key)
        self._initialized = False

    def initialize(self):
        if self.use_qdrant and not self._initialized:
            self.index.check_connection()
            self.index.upsert(self.store.candidates(self.embedder.key))
            self._initialized = True

    def close(self):
        self.index.close()
        self._initialized = False

    def delete(self, document_id):
        self.initialize()
        if self.use_qdrant:
            self.index.delete_document(document_id)
        return self.store.delete(document_id)

    async def ingest(self, title, filename, text, document_id=None, strategy=None, publication=None):
        self.initialize()
        strategy = strategy or self.settings.chunk_strategy
        chunks = split_text(text, strategy=strategy)
        if not chunks:
            raise ValueError("文档内容不能为空")
        vectors = await self.embedder.encode([title + "\n" + c["text"] for c in chunks])
        return self.store.publish(title, filename, text, chunks, vectors, self.embedder.key, document_id, strategy, publication, before_commit=self.index.upsert if self.use_qdrant else None)

    async def search(self, query, top_k=3):
        if self.use_qdrant and self.settings.retrieval_mode == "vector" and self.settings.rewrite_mode == "off" and self.settings.rerank_mode == "off":
            self.initialize()
            active_ids = self.store.active_chunk_ids(self.embedder.key)
            if not active_ids:
                return []
            return self.index.search(await self.embedder.encode_query(query), active_ids, top_k, self.min_score)
        return (await self.inspect(query, top_k))["hits"]

    async def inspect(self, query, top_k=3, mode=None, rewrite=None, reranking=None):
        mode, rewrite, reranking = mode or self.settings.retrieval_mode, rewrite or self.settings.rewrite_mode, reranking or self.settings.rerank_mode
        if mode not in {"vector", "keyword", "hybrid"} or rewrite not in {"off", "model"} or reranking not in {"off", "model", "compatible"}:
            raise ValueError("未知检索策略")
        async with asyncio.timeout(self.settings.timeout):
            return await self._inspect(query, top_k, mode, rewrite, reranking)

    async def _inspect(self, query, top_k, mode, rewrite, reranking):
        self.initialize()
        candidates = self.store.candidates(self.embedder.key)
        trace = {"query": query, "mode": mode, "rewrite": rewrite, "rerank": reranking,
                 "embedding": self.embedder.key, "keyword_backend": self.settings.keyword_backend,
                 "vector_backend": self.settings.vector_backend, "model_mode": self.settings.model_mode,
                 "candidate_count": len(candidates), "hits": [], "rankings": {}}
        if not candidates:
            return trace
        rewritten = await rewrite_query(self.settings, query) if rewrite == "model" else query
        trace["rewritten_query"] = rewritten
        queries = list(dict.fromkeys([query, rewritten]))
        rankings = []
        from src.rag.external import ExternalIndexes
        external = ExternalIndexes(self.settings, self.embedder.key)
        for index, question in enumerate(queries):
            if mode in {"vector", "hybrid"}:
                vector = await self.embedder.encode_query(question)
                if self.use_qdrant:
                    hits = self.index.search(vector, [c["id"] for c in candidates], 30, self.min_score)
                else:
                    hits = []
                    for c in candidates:
                        stored = json.loads(c["vector"])
                        if len(stored) != len(vector):
                            raise ValueError("向量维度发生变化，请重新导入文档")
                        score = sum(a * b for a, b in zip(vector, stored, strict=True))
                        if score >= self.min_score:
                            hits.append({**c, "score": score})
                    hits = sorted(hits, key=lambda h: (-h["score"], h["id"]))[:30]
                rankings.append((f"vector_{index}", hits))
            if mode in {"keyword", "hybrid"}:
                hits = await external.keyword(question, candidates) if self.settings.keyword_backend == "elasticsearch" else bm25(question, candidates)
                rankings.append((f"keyword_{index}", hits))
        hits = fuse(rankings) if len(rankings) > 1 else rankings[0][1]
        trace["rankings"] = {name: [{"id": h["id"], "score": h["score"]} for h in items] for name, items in rankings}
        hits = await rerank(self.settings, query, hits[:20], reranking)
        # Canonical SQLite versions govern visibility even while external indexes catch up.
        trace["hits"] = [{k: v for k, v in h.items() if k != "vector"} for h in hits
                         if (c := self.store.citation(h["id"])) and c["version"] == c["active_version"]][:top_k]
        return trace
