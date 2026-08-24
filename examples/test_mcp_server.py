"""Paper Studio 只读 MCP Server 协议级测试（无需网络）。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.core import CostTracker
from agent.mcp_server import mcp
from agent.read_service import PaperStudioReadService


EXPECTED_TOOLS = {
    "search_papers",
    "search_library",
    "list_reports",
    "read_report",
    "get_cost_overview",
    "estimate_cost",
    "search_memory",
    "read_memory",
    "start_research",
    "start_research_with_download",
    "write_memory",
    "list_schedules",
    "save_schedule",
    "run_schedule_now",
    "delete_content",
    "get_research_status",
    "pause_research",
    "resume_research",
}

READ_ONLY_TOOLS = {
    "search_papers", "search_library", "list_reports", "read_report",
    "get_cost_overview", "estimate_cost", "search_memory", "read_memory",
    "list_schedules", "get_research_status",
}


def expect(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def prepare_data(data_dir: Path) -> None:
    batch = data_dir / "batch-20260823"
    pdf = batch / "papers" / "01_mcp.pdf"
    text_path = batch / "texts" / "01_mcp.txt"
    pdf.parent.mkdir(parents=True)
    text_path.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 test")
    text_path.write_text("paper text", encoding="utf-8")
    (batch / "metadata.json").write_text(json.dumps({
        "run_id": "batch-20260823",
        "generated_at": "2026-08-23 16:00:00",
        "settings": {"api_key": "must-not-leak"},
        "papers": [{
            "title": "MCP for Literature Research",
            "source": "arxiv_search",
            "url": "https://arxiv.org/abs/0000.00000",
            "authors": ["Paper Studio"],
            "year": 2026,
            "abstract": "A read-only MCP integration.",
        }],
        "items": [{
            "index": 1,
            "title": "MCP for Literature Research",
            "source": "arxiv_search",
            "status": "ok",
            "pdf_path": str(pdf),
            "text_path": str(text_path),
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (data_dir / "report_mcp.md").write_text(
        "# MCP 研究报告\n\n这是完整报告内容。\n", encoding="utf-8")
    (data_dir / "research_memory.json").write_text(json.dumps({
        "mcp memory": {
            "query": "MCP memory",
            "timestamp": "2026-08-23 16:10:00",
            "papers": [{
                "title": "Memory Paper", "url": "", "source": "manual",
                "authors": [], "year": 2026,
            }],
            "summaries": [{"method": "structured memory"}],
            "analysis": {"notes": "read-only test"},
        },
    }, ensure_ascii=False), encoding="utf-8")
    tracker = CostTracker(storage_path=data_dir / "cost_ledger.json")
    tracker.set_budget(10.0)
    tracker.record("deepseek", "deepseek-v4-flash", 1000, 500,
                   purpose="测试")


async def protocol_test() -> None:
    async with Client(mcp, raise_exceptions=True) as client:
        tools_result = await client.list_tools()
        tools = {tool.name: tool for tool in tools_result.tools}
        expect("注册 18 个数据与受控操作工具", set(tools) == EXPECTED_TOOLS)
        expect("数据与状态工具声明只读", all(
            tool.annotations and tool.annotations.read_only_hint
            for name, tool in tools.items() if name in READ_ONLY_TOOLS))
        expect("联网搜索标记开放世界",
               tools["search_papers"].annotations.open_world_hint is True)
        expect("本地只读能力标记封闭世界", all(
            tools[name].annotations.open_world_hint is False
            for name in READ_ONLY_TOOLS - {"search_papers"}))
        expect("启动研究为非破坏性非幂等写操作",
               tools["start_research"].annotations.read_only_hint is False and
               tools["start_research"].annotations.destructive_hint is False and
               tools["start_research"].annotations.idempotent_hint is False)
        expect("暂停恢复为非破坏性幂等控制", all(
            tools[name].annotations.read_only_hint is False and
            tools[name].annotations.destructive_hint is False and
            tools[name].annotations.idempotent_hint is True
            for name in {"pause_research", "resume_research"}))
        expect("删除声明为破坏性操作",
               tools["delete_content"].annotations.destructive_hint is True and
               tools["delete_content"].annotations.read_only_hint is False)
        expect("确认回执不暴露给模型输入", all(
            "approval" not in tools[name].input_schema.get("properties", {})
            for name in {
                "start_research", "start_research_with_download",
                "write_memory", "save_schedule", "run_schedule_now",
                "delete_content",
            }))

        fake_search = {
            "query": "MCP", "sources": ["arxiv_search"],
            "max_results_per_source": 1, "year_from": 2025,
            "count": 1, "partial": False, "warnings": [],
            "papers": [{"title": "MCP Paper", "source": "arxiv_search"}],
        }
        with patch.object(PaperStudioReadService, "search_papers",
                          return_value=fake_search):
            search = await client.call_tool("search_papers", {
                "query": "MCP", "max_results": 1,
                "sources": ["arxiv_search"], "year_from": 2025,
            })
        expect("检索工具协议调用成功", not search.is_error and
               search.structured_content["papers"][0]["title"] == "MCP Paper")

        library = await client.call_tool(
            "search_library", {"keyword": "MCP", "limit": 10})
        expect("文献库工具调用成功", not library.is_error)
        expect("文献库返回下载状态",
               library.structured_content["items"][0]["pdf_available"] is True)
        downloaded = await client.call_tool(
            "search_library", {"status": "downloaded", "limit": 10})
        expect("ok/downloaded 状态筛选兼容",
               downloaded.structured_content["total"] == 1)
        serialized = json.dumps(library.structured_content, ensure_ascii=False)
        expect("不暴露 API Key", "must-not-leak" not in serialized)
        expect("不暴露绝对文件路径", str(Path.cwd()) not in serialized)

        reports = await client.call_tool("list_reports", {"limit": 10})
        expect("报告列表工具调用成功", not reports.is_error)
        report_id = reports.structured_content["reports"][0]["id"]
        report = await client.call_tool("read_report", {"report_id": report_id})
        expect("报告工具返回正文", "完整报告内容" in
               report.structured_content["content"])

        cost = await client.call_tool("get_cost_overview", {})
        expect("成本工具读取持久化账本",
               cost.structured_content["ledger"]["calls"] == 1)
        expect("成本仅开放 Flash/Pro", set(
            cost.structured_content["pricing_per_1m_tokens"]
        ) == {"deepseek-v4-flash", "deepseek-v4-pro"})
        estimate = await client.call_tool("estimate_cost", {
            "model": "deepseek-v4-pro",
            "input_tokens": 100_000,
            "output_tokens": 10_000,
        })
        expect("成本估算为非零人民币", not estimate.is_error and
               estimate.structured_content["estimated_total_cny"] > 0)

        memories = await client.call_tool(
            "search_memory", {"keyword": "MCP", "limit": 10})
        expect("记忆索引可只读搜索", not memories.is_error and
               memories.structured_content["items"][0]["query"] == "MCP memory")
        memory = await client.call_tool(
            "read_memory", {"query": "MCP memory"})
        expect("记忆明细可只读查看", not memory.is_error and
               memory.structured_content["query"] == "MCP memory")

        resources = await client.list_resources()
        resource_uris = {str(resource.uri) for resource in resources.resources}
        expect("开放文献/报告/成本静态资源", resource_uris == {
            "paper-studio://library", "paper-studio://reports",
            "paper-studio://cost",
        })
        templates = await client.list_resource_templates()
        template_uris = {template.uri_template for template in
                         templates.resource_templates}
        expect("开放批次与报告资源模板", template_uris == {
            "paper-studio://library/{batch_id}",
            "paper-studio://reports/{report_id}",
        })
        resource = await client.read_resource(
            "paper-studio://reports/report_mcp.md")
        expect("报告资源返回完整 Markdown",
               "# MCP 研究报告" in resource.contents[0].text)


async def stdio_test(data_dir: Path) -> None:
    """真实启动子进程，验证 stdout 没有混入日志或普通 print。"""
    project_root = Path(__file__).resolve().parent.parent
    params = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "agent.mcp_server"],
        cwd=project_root,
        env={
            "PYTHONPATH": str(project_root),
            "PAPER_STUDIO_DATA_DIR": str(data_dir),
        },
    )
    async with Client(stdio_client(params), raise_exceptions=True) as client:
        tools = await client.list_tools()
        expect("stdio 子进程完成协议握手",
               {tool.name for tool in tools.tools} == EXPECTED_TOOLS)
        result = await client.call_tool("list_reports", {"limit": 1})
        expect("stdio 子进程可调用报告工具",
               not result.is_error and result.structured_content["total"] == 1)


def main() -> None:
    data_dir = Path(tempfile.mkdtemp()) / "downloads"
    data_dir.mkdir(parents=True)
    prepare_data(data_dir)
    previous = os.environ.get("PAPER_STUDIO_DATA_DIR")
    os.environ["PAPER_STUDIO_DATA_DIR"] = str(data_dir)
    try:
        asyncio.run(protocol_test())
        asyncio.run(stdio_test(data_dir))
    finally:
        if previous is None:
            os.environ.pop("PAPER_STUDIO_DATA_DIR", None)
        else:
            os.environ["PAPER_STUDIO_DATA_DIR"] = previous
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
