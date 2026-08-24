"""仅供 MCP Client Streamable HTTP 回归测试的本机 Server。"""

from __future__ import annotations

import argparse

from mcp.server import MCPServer
from mcp.types import ToolAnnotations


mcp = MCPServer("Paper Studio HTTP Fixture")


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=False))
def echo(value: str) -> dict[str, str]:
    """原样返回文本，用于验证 HTTP Tool 调用。"""
    return {"value": value}


@mcp.resource("fixture://knowledge", mime_type="text/plain")
def knowledge() -> str:
    return "Paper Studio Streamable HTTP resource"


@mcp.resource("fixture://knowledge/{topic}", mime_type="text/plain")
def topic_knowledge(topic: str) -> str:
    return f"Paper Studio template resource: {topic}"


@mcp.prompt(description="生成一个用于协议测试的研究提示")
def research_prompt(topic: str, depth: str = "brief") -> str:
    return f"Research {topic} at {depth} depth"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    mcp.run(
        "streamable-http",
        host="127.0.0.1",
        port=args.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
