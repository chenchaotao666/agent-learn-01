import json

from src.rag.chunking import split_text
from src.rag.embedding import Embedder
from src.store import Store


class RagService:
    def __init__(self, store: Store, embedder: Embedder, min_score: float):
        self.store, self.embedder, self.min_score = store, embedder, min_score

    async def ingest(self, title, filename, text, document_id=None):
        chunks = split_text(text)
        if not chunks:
            raise ValueError("文档内容不能为空")
        vectors = await self.embedder.encode([title + "\n" + c["text"] for c in chunks])
        return self.store.publish(title, filename, text, chunks, vectors, self.embedder.key, document_id)

    async def search(self, query, top_k=3):
        candidates = self.store.candidates(self.embedder.key)
        if not candidates:
            return []
        vector = (await self.embedder.encode([query]))[0]
        hits = []
        for candidate in candidates:
            stored = json.loads(candidate.pop("vector"))
            if len(stored) != len(vector):
                raise ValueError("向量维度发生变化，请重新导入文档")
            score = sum(a * b for a, b in zip(vector, stored, strict=True))
            if score >= self.min_score:
                candidate["score"] = round(score, 4)
                hits.append(candidate)
        return sorted(hits, key=lambda h: (-h["score"], h["id"]))[:top_k]
