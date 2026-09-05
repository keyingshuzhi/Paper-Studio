"""MCP 与 Web/App 共用任务中心的启动、状态、暂停、恢复测试。"""

from __future__ import annotations

import asyncio
from http.client import HTTPException
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import Client
from mcp.client import ClientRequestContext
from mcp.types import ElicitRequestParams, ElicitResult

from agent.mcp_server import mcp
from agent.webapp import ResearchWebApp


def expect(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


async def control_flow(app: ResearchWebApp, gate: threading.Event,
                       started: threading.Event,
                       captured: dict) -> None:
    prompts = []

    async def approve(_context: ClientRequestContext,
                      params: ElicitRequestParams) -> ElicitResult:
        prompts.append(params.message)
        return ElicitResult(action="accept", content={"approved": True})

    async with Client(mcp, raise_exceptions=True, mode="legacy",
                      elicitation_callback=approve) as client:
        started_result = await client.call_tool("start_research", {
            "query": "MCP agent control",
            "mode": "deep",
            "max_results": 4,
            "rounds": 2,
            "branching": 1,
            "max_queries": 3,
        })
        if started_result.is_error:
            print("  MCP 启动错误:", started_result.content)
        expect("MCP 启动研究成功", not started_result.is_error)
        expect("启动前请求用户确认", len(prompts) == 1 and
               "token" in prompts[0])
        job = started_result.structured_content
        job_id = job["id"]
        runtime_token = json.loads(
            app._mcp_runtime_path.read_text(encoding="utf-8"))["token"]
        expect("控制凭据不进入 MCP 输出",
               runtime_token not in json.dumps(job, ensure_ascii=False))
        expect("返回专业任务 ID", job_id.startswith("research-"))
        expect("任务出现在 Web/App 队列", app.get_job(job_id) is not None)
        did_start = await asyncio.to_thread(started.wait, 2)
        expect("研究执行线程已启动", did_start)

        paused_result = await client.call_tool(
            "pause_research", {"job_id": job_id})
        expect("暂停工具返回 paused", not paused_result.is_error and
               paused_result.structured_content["status"] == "paused")
        status_result = await client.call_tool(
            "get_research_status", {"job_id": job_id})
        expect("状态查询反映暂停", not status_result.is_error and
               status_result.structured_content["can_resume"] is True)
        paused_again = await client.call_tool(
            "pause_research", {"job_id": job_id})
        expect("重复暂停保持幂等", not paused_again.is_error and
               paused_again.structured_content["status"] == "paused")

        resumed_result = await client.call_tool(
            "resume_research", {"job_id": job_id})
        expect("恢复工具返回 running", not resumed_result.is_error and
               resumed_result.structured_content["status"] == "running")
        resumed_again = await client.call_tool(
            "resume_research", {"job_id": job_id})
        expect("重复恢复保持幂等", not resumed_again.is_error and
               resumed_again.structured_content["status"] == "running")

        gate.set()
        final = None
        for _ in range(100):
            result = await client.call_tool(
                "get_research_status", {"job_id": job_id})
            final = result.structured_content
            if final["is_terminal"]:
                break
            await asyncio.sleep(0.03)
        expect("恢复后研究正常完成", final and final["status"] == "done")
        terminal_pause = await client.call_tool(
            "pause_research", {"job_id": job_id})
        expect("已结束任务拒绝暂停", terminal_pause.is_error)
        expect("使用应用当前模型设置", captured.get("provider") == "ollama"
               and captured.get("model") == "test-local-model")
        expect("MCP 启动不会下载论文", captured.get("download") is False)


def main() -> None:
    root = Path(tempfile.mkdtemp())
    data_dir = root / "downloads"
    data_dir.mkdir()
    previous_cwd = Path.cwd()
    previous_data = os.environ.get("PAPER_STUDIO_DATA_DIR")
    os.chdir(root)
    os.environ["PAPER_STUDIO_DATA_DIR"] = str(data_dir)

    gate = threading.Event()
    started = threading.Event()
    captured = {}

    def runner(_query, checkpoint=None, **opts):
        captured.update(opts)
        print("[规划] MCP 研究已进入任务中心")
        started.set()
        for _ in range(100):
            checkpoint()
            if gate.wait(0.02):
                break
        return {"report_path": None}

    app = ResearchWebApp(runner=runner)
    app.settings["provider"] = "ollama"
    app.settings["model"] = "test-local-model"
    server = app._make_server(port=0)  # type: ignore[attr-defined]
    port = server.server_address[1]
    app._publish_mcp_runtime(port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        mode = stat.S_IMODE(app._mcp_runtime_path.stat().st_mode)
        expect("运行时凭据文件权限为 0600", mode == 0o600)
        runtime = json.loads(app._mcp_runtime_path.read_text(encoding="utf-8"))
        expect("运行时仅登记本机端口", runtime["port"] == port and
               "base_url" not in runtime)
        try:
            urlopen(f"http://127.0.0.1:{port}/api/mcp/job?id=invalid", timeout=2)
            unauthorized = False
        except HTTPError as err:
            unauthorized = err.code == 403
        except HTTPException:
            unauthorized = False
        expect("无控制凭据请求被拒绝", unauthorized)
        asyncio.run(control_flow(app, gate, started, captured))
    finally:
        gate.set()
        server.shutdown()
        server.server_close()
        app._cleanup_mcp_runtime()
        expect("停止后清理运行时凭据", not app._mcp_runtime_path.exists())
        os.chdir(previous_cwd)
        if previous_data is None:
            os.environ.pop("PAPER_STUDIO_DATA_DIR", None)
        else:
            os.environ["PAPER_STUDIO_DATA_DIR"] = previous_data

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
