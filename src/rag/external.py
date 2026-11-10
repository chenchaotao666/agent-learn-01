"""Optional HTTP index adapters. SQLite remains the publication/visibility authority."""
import hashlib
import json
import uuid

import httpx

from src.rag.ranking import tokenize


class ExternalIndexes:
    def __init__(self, settings, embedding_key):
        self.settings = settings
        self.name = "office-" + hashlib.sha256((settings.database + embedding_key).encode()).hexdigest()[:20]

    async def keyword(self, query, candidates):
        url = self.settings.elasticsearch_url + "/" + self.name
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            exists = await client.head(url)
            if exists.status_code == 404:
                created = await client.put(url, json={"mappings": {"properties": {
                    "terms": {"type": "text", "analyzer": "whitespace"}}}})
                if created.status_code != 400 or created.json().get("error", {}).get("type") != "resource_already_exists_exception":
                    created.raise_for_status()
            else:
                exists.raise_for_status()
            # Explicit refresh makes a completed sync visible before searching.
            for start in range(0, len(candidates), 100):
                lines = []
                for c in candidates[start:start + 100]:
                    lines.extend([json.dumps({"index": {"_id": c["id"]}}),
                                  json.dumps({"terms": " ".join(tokenize(c["title"] + "\n" + c["text"]))})])
                r = await client.post(url + "/_bulk?refresh=wait_for", content="\n".join(lines) + "\n",
                                      headers={"Content-Type": "application/x-ndjson"})
                r.raise_for_status()
                if r.json().get("errors"):
                    raise ValueError("Elasticsearch 索引同步失败")
            r = await client.post(url + "/_search", json={"size": 30, "query": {"bool": {
                "must": [{"match": {"terms": " ".join(tokenize(query))}}],
                "filter": [{"ids": {"values": [c["id"] for c in candidates]}}]}}})
            r.raise_for_status()
            by_id = {c["id"]: c for c in candidates}
            return [{**by_id[h["_id"]], "score": h["_score"]} for h in r.json()["hits"]["hits"] if h["_id"] in by_id]

    async def vector(self, query, vector, candidates):
        url = self.settings.qdrant_url + "/collections/" + self.name
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            exists = await client.get(url)
            if exists.status_code == 404:
                created = await client.put(url, json={"vectors": {"size": len(vector), "distance": "Cosine"}})
                if created.status_code not in (200, 409):
                    created.raise_for_status()
            else:
                exists.raise_for_status()
                if exists.json()["result"]["config"]["params"]["vectors"]["size"] != len(vector):
                    raise ValueError("Qdrant 维度变化，请切换向量模型命名空间并重新导入")
            by_id = {str(uuid.UUID(c["id"])): c for c in candidates}
            for start in range(0, len(candidates), 100):
                points = [{"id": str(uuid.UUID(c["id"])), "vector": json.loads(c["vector"])} for c in candidates[start:start + 100]]
                r = await client.put(url + "/points?wait=true", json={"points": points})
                r.raise_for_status()
            r = await client.post(url + "/points/query", json={"query": vector, "limit": 30,
                "filter": {"must": [{"has_id": list(by_id)}]}, "score_threshold": self.settings.min_score,
                "with_payload": False})
            r.raise_for_status()
            return [{**by_id[str(p["id"])], "score": p["score"]} for p in r.json()["result"]["points"] if str(p["id"]) in by_id]
