"""Paper Studio MCP Client 的 Streamable HTTP 传输回归测试。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.mcp_client import MCPClientError, MCPClientManager, MCPConnectionStore


def expect(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(port: int, process: subprocess.Popen) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("HTTP fixture 提前退出")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return
        except OSError:
            time.sleep(.03)
    raise RuntimeError("HTTP fixture 启动超时")


async def http_flow(port: int, config_path: Path) -> None:
    manager = MCPClientManager(MCPConnectionStore(config_path))
    try:
        manager.save_server({
            "name": "unsafe url", "transport": "streamable_http",
            "url": f"http://user:secret@127.0.0.1:{port}/mcp",
        })
        unsafe_rejected = False
    except ValueError:
        unsafe_rejected = True
    expect("HTTP URL 拒绝内嵌账号与凭据", unsafe_rejected)
    try:
        manager.save_server({
            "name": "plaintext remote", "transport": "streamable_http",
            "url": "http://example.edu/mcp",
        })
        plaintext_rejected = False
    except ValueError:
        plaintext_rejected = True
    expect("非本机 HTTP 连接必须使用 HTTPS", plaintext_rejected)

    server = manager.save_server({
        "name": "HTTP knowledge fixture",
        "category": "knowledge",
        "transport": "streamable_http",
        "url": f"http://127.0.0.1:{port}/mcp",
        "headers_from": {"X-Fixture-Token": "PAPER_STUDIO_HTTP_TOKEN"},
        "permissions": {"resources_read": True, "tools_call": True},
        "timeout_seconds": 15,
    })
    manager.trust_server(server["id"])
    previous = os.environ.pop("PAPER_STUDIO_HTTP_TOKEN", None)
    try:
        try:
            await manager.discover(server["id"])
            missing_blocked = False
        except MCPClientError:
            missing_blocked = True
        expect("缺少认证环境变量时不发起 HTTP 连接", missing_blocked)
        os.environ["PAPER_STUDIO_HTTP_TOKEN"] = "not-persisted"
        discovery = await manager.discover(server["id"])
        expect("完成 Streamable HTTP 协议握手",
               {item["name"] for item in discovery["tools"]} == {"echo"})
        expect("可发现 Prompt 与 Resource Template",
               {item["name"] for item in discovery["prompts"]} ==
               {"research_prompt"} and
               len(discovery["resource_templates"]) == 1)
        resource = await manager.read_resource(
            server["id"], "fixture://knowledge")
        expect("HTTP Resource 可读取",
               "Streamable HTTP resource" in json.dumps(resource))
        template_resource = await manager.read_resource(
            server["id"], "fixture://knowledge/agents")
        expect("Resource Template 参数化读取可用",
               "template resource: agents" in json.dumps(template_resource))
        prompt = await manager.get_prompt(
            server["id"], "research_prompt",
            {"topic": "MCP", "depth": "deep"})
        expect("外部 Prompt 可获取并传入参数",
               "Research MCP at deep depth" in json.dumps(prompt))
        result = await manager.call_tool(
            server["id"], "echo", {"value": "dual role"})
        expect("HTTP Tool 可调用", "dual role" in json.dumps(result))
        expect("HTTP 凭据值不进入配置文件",
               "not-persisted" not in config_path.read_text(encoding="utf-8"))
    finally:
        os.environ.pop("PAPER_STUDIO_HTTP_TOKEN", None)
        if previous is not None:
            os.environ["PAPER_STUDIO_HTTP_TOKEN"] = previous


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    temp_root = Path(tempfile.mkdtemp())
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-B", "examples/mcp_http_fixture.py",
         "--port", str(port)],
        cwd=project_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_ready(port, process)
        asyncio.run(http_flow(
            port, temp_root / "mcp_connections.json"))
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
