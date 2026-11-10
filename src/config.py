import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database: str = "data/app.sqlite3"
    model_mode: str = "mock"
    embedding_mode: str = "hash"
    ollama_url: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen3:4b"
    embedding_model: str = "qwen3-embedding:0.6b"
    timeout: float = 60
    min_score: float = 0.12
    deepseek_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_api_key: str = field(default="", repr=False)
    tool_transport: str = "local"
    mcp_url: str = "http://127.0.0.1:8011/mcp"
    speech_url: str = ""
    speech_api_key: str = field(default="", repr=False)
    asr_model: str = ""
    tts_model: str = ""
    retrieval_mode: str = "vector"
    keyword_backend: str = "local"
    vector_backend: str = "local"
    rewrite_mode: str = "off"
    rerank_mode: str = "off"
    chunk_strategy: str = "fixed"
    embedding_url: str = ""
    embedding_api_key: str = field(default="", repr=False)
    elasticsearch_url: str = "http://127.0.0.1:9200"
    qdrant_url: str = "http://127.0.0.1:6333"
    rerank_url: str = ""
    rerank_model: str = ""
    rerank_api_key: str = field(default="", repr=False)

    def __post_init__(self):
        if self.model_mode not in {"mock", "ollama", "deepseek"}:
            raise ValueError("MODEL_MODE must be mock, ollama or deepseek")
        if self.tool_transport not in {"local", "stdio", "http"}:
            raise ValueError("TOOL_TRANSPORT must be local, stdio or http")
        if self.embedding_mode not in {"hash", "ollama", "compatible"}:
            raise ValueError("EMBEDDING_MODE must be hash, ollama or compatible")
        for value, allowed in [(self.retrieval_mode, {"vector", "keyword", "hybrid"}),
                               (self.keyword_backend, {"local", "elasticsearch"}),
                               (self.vector_backend, {"local", "qdrant"}),
                               (self.rewrite_mode, {"off", "model"}),
                               (self.rerank_mode, {"off", "model", "compatible"}),
                               (self.chunk_strategy, {"fixed", "structure"})]:
            if value not in allowed:
                raise ValueError(f"Invalid pipeline setting: {value}")
        if self.timeout <= 0 or not 0 <= self.min_score <= 1:
            raise ValueError("Invalid timeout or retrieval threshold")

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        return cls(
            database=os.getenv("DATABASE_PATH", "data/app.sqlite3"),
            model_mode=os.getenv("MODEL_MODE", "deepseek"),
            embedding_mode=os.getenv("EMBEDDING_MODE", "hash"),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            chat_model=os.getenv("CHAT_MODEL", "qwen3:4b"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b"),
            timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "60")),
            min_score=float(os.getenv("RETRIEVAL_MIN_SCORE", "0.12")),
            deepseek_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            tool_transport=os.getenv("TOOL_TRANSPORT", "local"),
            mcp_url=os.getenv("MCP_URL", "http://127.0.0.1:8011/mcp"),
            speech_url=os.getenv("SPEECH_BASE_URL", "").rstrip("/"),
            speech_api_key=os.getenv("SPEECH_API_KEY", ""),
            asr_model=os.getenv("ASR_MODEL", ""),
            tts_model=os.getenv("TTS_MODEL", ""),
            retrieval_mode=os.getenv("RETRIEVAL_MODE", "vector"),
            keyword_backend=os.getenv("KEYWORD_BACKEND", "local"),
            vector_backend=os.getenv("VECTOR_BACKEND", "local"),
            rewrite_mode=os.getenv("QUERY_REWRITE", "off"),
            rerank_mode=os.getenv("RERANK_MODE", "off"),
            chunk_strategy=os.getenv("CHUNK_STRATEGY", "fixed"),
            embedding_url=os.getenv("EMBEDDING_BASE_URL", "").rstrip("/"),
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
            elasticsearch_url=os.getenv("ELASTICSEARCH_URL", "http://127.0.0.1:9200").rstrip("/"),
            qdrant_url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/"),
            rerank_url=os.getenv("RERANK_BASE_URL", "").rstrip("/"),
            rerank_model=os.getenv("RERANK_MODEL", ""),
            rerank_api_key=os.getenv("RERANK_API_KEY", ""),
        )
