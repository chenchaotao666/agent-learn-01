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

    def __post_init__(self):
        if self.model_mode not in {"mock", "ollama", "deepseek"}:
            raise ValueError("MODEL_MODE must be mock, ollama or deepseek")
        if self.tool_transport not in {"local", "stdio", "http"}:
            raise ValueError("TOOL_TRANSPORT must be local, stdio or http")
        if self.embedding_mode not in {"hash", "ollama"}:
            raise ValueError("EMBEDDING_MODE must be hash or ollama")
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
        )
