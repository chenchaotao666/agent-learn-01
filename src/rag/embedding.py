import hashlib
import math
import re

import httpx

from src.config import Settings


def normalize(vector):
    if not vector or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
        raise ValueError("Embedding response contains an invalid vector")
    length = math.sqrt(sum(v * v for v in vector))
    return [v / length for v in vector] if length else list(vector)


def hash_vector(text: str, dimensions: int = 1024) -> list[float]:
    """Deterministic character-bigram feature hashing, NOT semantic embeddings."""
    tokens = []
    for part in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", text.lower()):
        if re.match(r"[a-z0-9]", part):
            tokens.append(part)
        else:
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
    vector = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1 if digest[4] % 2 else -1
    return normalize(vector)


class Embedder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.key = (
            "hash-char-bigram-v1:1024" if settings.embedding_mode == "hash"
            else f"{settings.embedding_mode}:{settings.embedding_url if settings.embedding_mode == 'compatible' else settings.ollama_url}:{settings.embedding_model}"
        )

    async def encode(self, texts: list[str]) -> list[list[float]]:
        if self.settings.embedding_mode == "hash":
            return [hash_vector(text) for text in texts]
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            if self.settings.embedding_mode == "compatible":
                if not self.settings.embedding_url:
                    raise ValueError("请配置独立的 EMBEDDING_BASE_URL，DeepSeek 对话接口不提供向量")
                headers = {"Authorization": f"Bearer {self.settings.embedding_api_key}"} if self.settings.embedding_api_key else {}
                response = await client.post(f"{self.settings.embedding_url}/embeddings", headers=headers,
                    json={"model": self.settings.embedding_model, "input": texts})
            else:
                response = await client.post(f"{self.settings.ollama_url}/api/embed",
                    json={"model": self.settings.embedding_model, "input": texts, "truncate": False})
            response.raise_for_status()
            if self.settings.embedding_mode == "compatible":
                data = response.json().get("data", [])
                if sorted(d.get("index", -1) for d in data) != list(range(len(texts))):
                    raise ValueError("Embedding indices do not match input")
                vectors = [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
            else:
                vectors = response.json().get("embeddings")
        if not isinstance(vectors, list):
            raise ValueError("Embedding response is missing embeddings")
        if len(vectors) != len(texts):
            raise ValueError("Embedding count does not match input count")
        normalized = [normalize(v) for v in vectors]
        if len({len(v) for v in normalized}) != 1:
            raise ValueError("Embedding dimensions do not match")
        return normalized
