from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DocumentInput(Schema):
    title: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100_000)
    document_id: str | None = Field(default=None, max_length=64)

    @field_validator("filename")
    @classmethod
    def extension(cls, value):
        if Path(value).suffix.lower() not in {".md", ".txt"}:
            raise ValueError("文本接口仅支持 .md/.txt；PDF/DOCX 请使用文件上传接口")
        return value


class ChatInput(Schema):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)
    top_k: Annotated[StrictInt, Field(ge=1, le=8)] = 3


Money = Annotated[str, Field(pattern=r"^(0|[1-9]\d{0,9})(\.\d{1,2})?$")]
ProjectId = Annotated[str, Field(pattern=r"^P\d{4}$")]


class RunInput(Schema):
    goal: str = Field(min_length=1, max_length=2000)
    project_id: ProjectId | None = None
    amount: Money | None = None
    client_request_id: str = Field(min_length=1, max_length=100)
    mode: Literal["agent", "workflow", "multi_agent"] = "agent"

    @field_validator("amount")
    @classmethod
    def positive(cls, value):
        if value is not None and Decimal(value) <= 0:
            raise ValueError("金额必须大于零")
        return value


class RunRevision(Schema):
    version: Annotated[StrictInt, Field(ge=1)]
    project_id: ProjectId | None = None
    amount: Money | None = None

    @field_validator("amount")
    @classmethod
    def positive(cls, value):
        return RunInput.positive(value)


class Confirmation(Schema):
    version: Annotated[StrictInt, Field(ge=1)]
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentDecision(Schema):
    action: Literal["get_budget", "search_knowledge", "calculate_gap", "create_draft", "finish", "clarify"]
    reason: str = Field(min_length=1, max_length=500)
    query: str = Field(default="", max_length=2000)


class RagAnswer(Schema):
    answer: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)


class MemoryInput(Schema):
    value: str = Field(min_length=1, max_length=1000)
    project_id: ProjectId | None = None
    source_message_id: Annotated[StrictInt, Field(ge=1)]
    confirmed: Literal[True]
    ttl_days: Annotated[StrictInt, Field(ge=1, le=3650)] | None = None


class MemoryUpdate(Schema):
    version: Annotated[StrictInt, Field(ge=1)]
    value: str = Field(min_length=1, max_length=1000)


class FeedbackInput(Schema):
    message_id: Annotated[StrictInt, Field(ge=1)]
    category: Literal["retrieval", "fact", "citation", "context", "tool", "translation", "speech", "helpful"]
    correction: str = Field(default="", max_length=4000)


class FeedbackReview(Schema):
    version: Annotated[StrictInt, Field(ge=1)]
    decision: Literal["accepted", "rejected", "needs_information"]
    expected_answer: str = Field(default="", max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=2000)


class EvaluationInput(Schema):
    dataset_id: str
    top_k: Annotated[StrictInt, Field(ge=1, le=8)] = 3
    mode: Literal["vector", "keyword", "hybrid"] = "vector"
    rewrite: Literal["off", "model"] = "off"
    rerank: Literal["off", "model", "compatible"] = "off"


class SearchInput(Schema):
    query: str = Field(min_length=1, max_length=2000)
    top_k: Annotated[StrictInt, Field(ge=1, le=8)] = 3
    mode: Literal["vector", "keyword", "hybrid"] = "hybrid"
    rewrite: Literal["off", "model"] = "off"
    rerank: Literal["off", "model", "compatible"] = "off"


class IngestionInput(DocumentInput):
    strategy: Literal["fixed", "structure"] = "structure"
    client_request_id: str = Field(min_length=1, max_length=100)

    @field_validator("filename")
    @classmethod
    def extension(cls, value):
        if Path(value).suffix.lower() not in {".md", ".txt", ".pdf", ".docx"}:
            raise ValueError("入库任务仅支持 MD/TXT/PDF/DOCX")
        return value


class SpeechInput(Schema):
    message_id: Annotated[StrictInt, Field(ge=1)]
    voice: str = Field(default="alloy", min_length=1, max_length=100)


class TranslationInput(Schema):
    text: str = Field(min_length=1, max_length=10000)
    target_language: str = Field(default="English", min_length=1, max_length=40)
