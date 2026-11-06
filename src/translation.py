import re

from pydantic import Field
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, ToolCallPart

from src.ai import model_backend
from src.schemas import Schema
from src.skills import Skills


class Translation(Schema):
    translated_text: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)


async def translate(settings, body):
    skill = Skills().load("procurement-translation")
    async def mock(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
            {"translated_text": body.text, "notes": ["模拟模式保留原文，不代表已完成翻译。"]})])
    async with model_backend(settings, FunctionModel(mock)) as backend:
        agent = Agent(backend, output_type=Translation, retries=2, instructions=skill["instructions"] + str(skill["resources"]))
        result = await agent.run(f"目标语言：{body.target_language}\n原文：{body.text}")
    from collections import Counter
    identifiers = lambda text: Counter(re.findall(r"P\d{4}|\d+(?:\.\d+)?", text))
    passed = identifiers(body.text) == identifiers(result.output.translated_text)
    return {**result.output.model_dump(), "identifiers_preserved": passed, "skill": skill["name"],
            "skill_version": skill["version"], "mode": settings.model_mode, "needs_review": True}
