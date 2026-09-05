"""Paper Studio 同时作为 MCP Client 与 MCP Server 的协议测试。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.mcp_client import (
    MCPClientError,
    MCPClientManager,
    MCPConnectionStore,
    MCPPermissionBroker,
)


def expect(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


async def protocol_flow(project_root: Path, data_dir: Path,
                        config_path: Path) -> None:
    store = MCPConnectionStore(config_path)
    manager = MCPClientManager(store)
    broker = MCPPermissionBroker(ttl_seconds=60)
    server = manager.save_server({
        "name": "Paper Studio fixture",
        "category": "knowledge",
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-B", "-m", "agent.mcp_server"],
        "cwd": str(project_root),
        "env_from": {
            "PAPER_STUDIO_DATA_DIR": "PAPER_STUDIO_TEST_DATA",
            "FIXTURE_TOKEN": "PAPER_STUDIO_TEST_SECRET",
        },
        "permissions": {"resources_read": True, "tools_call": True},
        "timeout_seconds": 20,
    })
    expect("新连接默认未信任", server["trusted"] is False)
    expect("配置文件权限为 0600",
           stat.S_IMODE(config_path.stat().st_mode) == 0o600)
    raw_config = config_path.read_text(encoding="utf-8")
    expect("配置不保存凭据值",
           os.environ["PAPER_STUDIO_TEST_SECRET"] not in raw_config and
           "PAPER_STUDIO_TEST_SECRET" in raw_config)

    try:
        await manager.discover(server["id"])
        untrusted_blocked = False
    except MCPClientError:
        untrusted_blocked = True
    expect("未信任连接不会启动进程", untrusted_blocked)

    challenge = broker.request(
        "trust", server["id"], "", server_name=server["name"])
    grant = broker.approve(challenge["challenge_id"], True)
    broker.consume(grant["permission_token"], "trust", server["id"], "")
    manager.trust_server(server["id"])
    discovery = await manager.discover(server["id"])
    tool_names = {tool["name"] for tool in discovery["tools"]}
    expect("完成 stdio MCP 协议握手", bool(discovery["protocol_version"]))
    expect("可发现外部 Tools", "estimate_cost" in tool_names and
           "search_library" in tool_names)
    expect("可发现 Resources 与 Templates",
           len(discovery["resources"]) == 3 and
           len(discovery["resource_templates"]) == 2)

    report = await manager.read_resource(
        server["id"], "paper-studio://reports/client_fixture.md")
    report_json = json.dumps(report, ensure_ascii=False)
    expect("可读取外部 Resource", "MCP Client Fixture" in report_json)

    arguments = {
        "model": "deepseek-v4-flash",
        "input_tokens": 100_000,
        "output_tokens": 10_000,
    }
    tool_challenge = broker.request(
        "call_tool", server["id"], "estimate_cost",
        server_name=server["name"], arguments=arguments)
    tool_grant = broker.approve(tool_challenge["challenge_id"], True)
    try:
        broker.consume(tool_grant["permission_token"], "call_tool",
                       server["id"], "estimate_cost", {"model": "changed"})
        mismatch_blocked = False
    except MCPClientError:
        mismatch_blocked = True
    expect("权限令牌与 Tool 参数精确绑定", mismatch_blocked)

    tool_challenge = broker.request(
        "call_tool", server["id"], "estimate_cost",
        server_name=server["name"], arguments=arguments)
    tool_grant = broker.approve(tool_challenge["challenge_id"], True)
    broker.consume(tool_grant["permission_token"], "call_tool",
                   server["id"], "estimate_cost", arguments)
    result = await manager.call_tool(server["id"], "estimate_cost", arguments)
    expect("确认后可调用外部 Tool",
           result.get("isError") is not True and
           "estimated_total_cny" in json.dumps(result))
    try:
        broker.consume(tool_grant["permission_token"], "call_tool",
                       server["id"], "estimate_cost", arguments)
        reused_blocked = False
    except MCPClientError:
        reused_blocked = True
    expect("权限令牌仅能使用一次", reused_blocked)

    saved = manager.save_server({
        **manager.get_server(server["id"]),
        "permissions": {"resources_read": True, "tools_call": False},
    })
    expect("权限收紧或连接变更后需重新信任",
           saved["trusted"] is False)

    delete_challenge = broker.request(
        "delete", server["id"], "", server_name=server["name"])
    delete_grant = broker.approve(delete_challenge["challenge_id"], True)
    broker.consume(delete_grant["permission_token"], "delete", server["id"], "")
    expect("确认后可删除连接配置", manager.delete_server(server["id"]))


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    temp_root = Path(tempfile.mkdtemp())
    data_dir = temp_root / "downloads"
    data_dir.mkdir()
    (data_dir / "client_fixture.md").write_text(
        "# MCP Client Fixture\n\nDual-role protocol test.\n", encoding="utf-8")
    previous_data = os.environ.get("PAPER_STUDIO_TEST_DATA")
    previous_secret = os.environ.get("PAPER_STUDIO_TEST_SECRET")
    os.environ["PAPER_STUDIO_TEST_DATA"] = str(data_dir)
    os.environ["PAPER_STUDIO_TEST_SECRET"] = "must-not-be-persisted"
    try:
        asyncio.run(protocol_flow(
            project_root, data_dir, temp_root / "mcp_connections.json"))
    finally:
        if previous_data is None:
            os.environ.pop("PAPER_STUDIO_TEST_DATA", None)
        else:
            os.environ["PAPER_STUDIO_TEST_DATA"] = previous_data
        if previous_secret is None:
            os.environ.pop("PAPER_STUDIO_TEST_SECRET", None)
        else:
            os.environ["PAPER_STUDIO_TEST_SECRET"] = previous_secret
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
