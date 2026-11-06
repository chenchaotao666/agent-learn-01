import sys
from contextlib import AsyncExitStack

from src.business import calculate_gap, get_budget
from src.config import ROOT


class ToolTransport:
    def __init__(self, settings):
        self.settings = settings

    async def call(self, name, arguments):
        if name not in {"get_budget", "calculate_gap"}:
            raise ValueError("未知预算工具")
        if self.settings.tool_transport == "local":
            return {"get_budget": get_budget, "calculate_gap": calculate_gap}[name](**arguments)
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client
        async with AsyncExitStack() as stack:
            if self.settings.tool_transport == "stdio":
                streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                    command=sys.executable, args=["-m", "src.mcp_server"], cwd=str(ROOT))))
            else:
                streams = await stack.enter_async_context(streamable_http_client(self.settings.mcp_url))
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
            available = await session.list_tools()
            if name not in {t.name for t in available.tools}:
                raise ValueError("MCP 服务未提供所需工具")
            result = await session.call_tool(name, arguments)
            if result.is_error:
                raise ValueError("MCP 工具执行失败")
            if result.structured_content:
                return result.structured_content
            import json
            for item in result.content:
                if item.type == "text":
                    return json.loads(item.text)
            raise ValueError("MCP 返回结构不完整")
