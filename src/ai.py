from contextlib import asynccontextmanager

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider


@asynccontextmanager
async def model_backend(settings, mock_model):
    if settings.model_mode == "mock":
        yield mock_model
        return
    if settings.model_mode == "deepseek":
        if not settings.deepseek_api_key:
            raise ValueError("请在 .env 配置 DEEPSEEK_API_KEY；离线测试需显式设置 MODEL_MODE=mock")
        url, key, name = settings.deepseek_url, settings.deepseek_api_key, settings.deepseek_model
    else:
        url, key, name = settings.ollama_url + "/v1", "ollama", settings.chat_model
    async with AsyncOpenAI(base_url=url, api_key=key, timeout=settings.timeout, max_retries=0) as client:
        provider = DeepSeekProvider(openai_client=client) if settings.model_mode == "deepseek" else OpenAIProvider(openai_client=client)
        yield OpenAIChatModel(name, provider=provider)
