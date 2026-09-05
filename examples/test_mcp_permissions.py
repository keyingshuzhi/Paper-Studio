"""MCP 高风险能力的确认、拒绝与实际控制链路测试。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import Client
from mcp.client import ClientRequestContext
from mcp.shared.exceptions import MCPError
from mcp.types import ElicitRequestParams, ElicitResult

from agent.mcp_server import mcp
from agent.webapp import ResearchWebApp


def expect(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


async def permission_flow(app: ResearchWebApp, captured_runs: list[dict]) -> None:
    prompts: list[str] = []

    async def approve(_context: ClientRequestContext,
                      params: ElicitRequestParams) -> ElicitResult:
        prompts.append(params.message)
        return ElicitResult(action="accept", content={"approved": True})

    async with Client(mcp, mode="legacy", raise_exceptions=False,
                      elicitation_callback=approve) as client:
        memory = await client.call_tool("write_memory", {
            "query": "permission memory",
            "notes": "经用户确认的本地记忆",
            "papers": [{
                "title": "Permission-aware MCP",
                "source": "manual",
                "year": 2026,
            }],
        })
        expect("确认后可写入记忆", not memory.is_error and
               app.memory.get_entry("permission memory") is not None)

        schedule = await client.call_tool("save_schedule", {
            "schedule_id": "schedule-mcp-permission",
            "query": "scheduled permission research",
            "enabled": False,
            "interval_minutes": 60,
            "mode": "single",
        })
        expect("确认后可保存定时任务",
               not schedule.is_error and
               schedule.structured_content["id"] == "schedule-mcp-permission")
        schedules = await client.call_tool("list_schedules", {})
        expect("定时任务可只读管理", not schedules.is_error and
               len(schedules.structured_content["schedules"]) == 1)

        run_now = await client.call_tool("run_schedule_now", {
            "schedule_id": "schedule-mcp-permission",
        })
        expect("确认后可立即运行定时任务",
               not run_now.is_error and
               run_now.structured_content["id"].startswith("research-"))

        download = await client.call_tool("start_research_with_download", {
            "query": "download permission research",
            "mode": "single",
            "max_results": 8,
            "max_downloads": 7,
        })
        expect("确认后可启动限速下载研究", not download.is_error)
        for _ in range(100):
            if any(run.get("download") for run in captured_runs):
                break
            await asyncio.sleep(0.01)
        download_opts = next((run for run in captured_runs
                              if run.get("download")), {})
        expect("下载上限进入原任务管道",
               download_opts.get("max_downloads") == 7 and
               download_opts.get("download_interval") == 2.0)

        report_path = Path("downloads/delete-me.md")
        report_path.write_text("# delete me\n", encoding="utf-8")
        deleted_report = await client.call_tool("delete_content", {
            "target_type": "report", "target_id": report_path.name,
        })
        expect("确认后可删除单个报告", not deleted_report.is_error and
               not report_path.exists())

        batch = Path("downloads/batch-mcp-permission")
        pdf = batch / "papers" / "01.pdf"
        text_path = batch / "texts" / "01.txt"
        pdf.parent.mkdir(parents=True)
        text_path.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4 test")
        text_path.write_text("test", encoding="utf-8")
        (batch / "metadata.json").write_text(json.dumps({
            "run_id": batch.name,
            "items": [{
                "index": 1, "title": "delete paper", "status": "ok",
                "pdf_path": str(pdf), "text_path": str(text_path),
            }],
        }), encoding="utf-8")
        deleted_item = await client.call_tool("delete_content", {
            "target_type": "library_item", "target_id": batch.name,
            "item_index": 1,
        })
        expect("确认后可删除单篇文献文件", not deleted_item.is_error and
               not pdf.exists() and not text_path.exists())
        deleted_batch = await client.call_tool("delete_content", {
            "target_type": "library_batch", "target_id": batch.name,
        })
        expect("确认后可删除整个文献批次", not deleted_batch.is_error and
               not batch.exists())

        run_job_id = run_now.structured_content["id"]
        for _ in range(100):
            run_status = await client.call_tool(
                "get_research_status", {"job_id": run_job_id})
            if run_status.structured_content["is_terminal"]:
                break
            await asyncio.sleep(0.01)
        deleted_record = await client.call_tool("delete_content", {
            "target_type": "research_record", "target_id": run_job_id,
        })
        expect("确认后可删除已结束任务记录",
               not deleted_record.is_error and app.get_job(run_job_id) is None)

        deleted_schedule = await client.call_tool("delete_content", {
            "target_type": "schedule",
            "target_id": "schedule-mcp-permission",
        })
        expect("确认后可删除单个定时任务",
               not deleted_schedule.is_error and not app.list_schedules())
        expect("所有高风险操作均请求确认", len(prompts) == 9)

    modern_prompts: list[str] = []

    async def approve_modern(_context: ClientRequestContext,
                             params: ElicitRequestParams) -> ElicitResult:
        modern_prompts.append(params.message)
        return ElicitResult(action="accept", content={"approved": True})

    async with Client(mcp, mode="auto", raise_exceptions=False,
                      elicitation_callback=approve_modern) as client:
        modern_write = await client.call_tool("write_memory", {
            "query": "modern protocol memory", "notes": "input-required",
        })
        modern_delete = await client.call_tool("delete_content", {
            "target_type": "memory", "target_id": "modern protocol memory",
        })
        expect("新版 InputRequired 协议也可完成确认",
               not modern_write.is_error and not modern_delete.is_error and
               len(modern_prompts) == 2)

    decline_prompts: list[str] = []

    async def decline(_context: ClientRequestContext,
                      params: ElicitRequestParams) -> ElicitResult:
        decline_prompts.append(params.message)
        return ElicitResult(action="decline")

    async with Client(mcp, mode="legacy", raise_exceptions=False,
                      elicitation_callback=decline) as client:
        declined = await client.call_tool("delete_content", {
            "target_type": "memory", "target_id": "permission memory",
        })
        expect("拒绝删除时调用失败且数据保留", declined.is_error and
               app.memory.get_entry("permission memory") is not None and
               len(decline_prompts) == 1)

    async with Client(mcp, mode="legacy", raise_exceptions=False) as client:
        try:
            await client.call_tool("write_memory", {
                "query": "must not be written", "notes": "no client support",
            })
            failed_closed = False
        except MCPError:
            failed_closed = True
        expect("客户端不支持确认时默认拒绝", failed_closed and
               app.memory.get_entry("must not be written") is None)

    async with Client(mcp, mode="legacy", raise_exceptions=False,
                      elicitation_callback=approve) as client:
        deleted_memory = await client.call_tool("delete_content", {
            "target_type": "memory", "target_id": "permission memory",
        })
        expect("重新确认后可删除记忆", not deleted_memory.is_error and
               app.memory.get_entry("permission memory") is None)


def main() -> None:
    root = Path(tempfile.mkdtemp())
    data_dir = root / "downloads"
    data_dir.mkdir()
    previous_cwd = Path.cwd()
    previous_data = os.environ.get("PAPER_STUDIO_DATA_DIR")
    os.chdir(root)
    os.environ["PAPER_STUDIO_DATA_DIR"] = str(data_dir)
    captured_runs: list[dict] = []

    def runner(_query, checkpoint=None, **opts):
        captured_runs.append(dict(opts))
        if checkpoint:
            checkpoint()
        return {"report_path": None}

    app = ResearchWebApp(runner=runner)
    server = app._make_server(port=0)  # type: ignore[attr-defined]
    port = server.server_address[1]
    app._publish_mcp_runtime(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(permission_flow(app, captured_runs))
    finally:
        server.shutdown()
        server.server_close()
        app._cleanup_mcp_runtime()
        app._schedule_stop.set()
        os.chdir(previous_cwd)
        if previous_data is None:
            os.environ.pop("PAPER_STUDIO_DATA_DIR", None)
        else:
            os.environ["PAPER_STUDIO_DATA_DIR"] = previous_data
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
