"""Local demo MCP server. Run python -m src.mcp_server [--http]."""
import sys

from mcp.server.mcpserver import MCPServer

from src.business import calculate_gap, get_budget

mcp = MCPServer("office-budget")
mcp.tool()(get_budget)
mcp.tool()(calculate_gap)

if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=8011)
    else:
        mcp.run(transport="stdio")
