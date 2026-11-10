"""Qdrant index; SQLite remains the source of truth for published versions."""
import hashlib
import json
from pathlib import Path
from uuid import UUID

from qdrant_client import QdrantClient, models


class VectorStoreError(RuntimeError):
    pass


class QdrantIndex:
    def __init__(self, settings, embedding_key):
        self.settings = settings
        # Separate collections for incompatible embeddings and separate databases.
        namespace = str(Path(settings.database).resolve()) + "\0" + embedding_key
        suffix = hashlib.sha256(namespace.encode()).hexdigest()[:16]
        self.collection = f"{settings.qdrant_collection}_{suffix}"
        self.path = settings.qdrant_path or str(Path(settings.database).with_suffix(".qdrant"))
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if self.settings.qdrant_url:
                self._client = QdrantClient(
                    url=self.settings.qdrant_url,
                    api_key=self.settings.qdrant_api_key or None,
                    timeout=max(1, int(self.settings.timeout)),
                )
            else:
                self._client = QdrantClient(path=self.path)
        return self._client

    def upsert(self, rows):
        if not rows:
            return
        try:
            points = [models.PointStruct(
                id=str(UUID(row["id"])), vector=json.loads(row["vector"]),
                payload={k: v for k, v in row.items() if k != "vector"},
            ) for row in rows]
            dimension = len(points[0].vector)
            if not self.client.collection_exists(self.collection):
                self.client.create_collection(
                    self.collection,
                    vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
                )
            elif self.client.get_collection(self.collection).config.params.vectors.size != dimension:
                raise ValueError("向量维度变化，请使用新的 QDRANT_COLLECTION 并重新导入文档")
            for start in range(0, len(points), 128):
                self.client.upsert(self.collection, points[start:start + 128], wait=True)
        except Exception as exc:
            raise VectorStoreError("Qdrant 写入失败，请检查服务和配置后重试") from exc

    def check_connection(self):
        try:
            self.client.get_collections()
        except Exception as exc:
            raise VectorStoreError(
                "无法连接 Qdrant；请先运行 docker compose up -d，再检查 QDRANT_URL"
            ) from exc

    def search(self, vector, active_ids, top_k, min_score):
        if not active_ids:
            return []
        try:
            # Filter BEFORE top-k: historical or uncommitted chunks cannot crowd out live results.
            result = self.client.query_points(
                self.collection, query=vector,
                query_filter=models.Filter(must=[models.HasIdCondition(
                    has_id=[str(UUID(i)) for i in active_ids]
                )]),
                limit=top_k, score_threshold=min_score, with_payload=True,
            )
            hits = [{**point.payload, "score": round(point.score, 4)} for point in result.points]
            return sorted(hits, key=lambda h: (-h["score"], h["id"]))
        except Exception as exc:
            raise VectorStoreError("Qdrant 检索失败，请检查服务和配置") from exc

    def delete_document(self, document_id):
        try:
            if self.client.collection_exists(self.collection):
                self.client.delete(self.collection, models.FilterSelector(filter=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
                )), wait=True)
        except Exception as exc:
            raise VectorStoreError("Qdrant 删除失败，请重试") from exc

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
