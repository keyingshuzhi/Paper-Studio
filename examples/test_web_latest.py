"""v0.0.4 最新后端能力到 Web 的接入回归测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.webapp import APP_VERSION, ResearchWebApp


def expect(name, condition):
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


def request(base: str, path: str, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(
        base + path, data=data,
        method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(req, timeout=8) as response:
            return response.status, json.loads(
                response.read().decode("utf-8"))
    except HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8"))


def wait_calls(calls, count: int) -> None:
    for _ in range(100):
        if len(calls) >= count:
            return
        time.sleep(.02)
    raise RuntimeError("Web 任务没有进入执行器")


def main() -> None:
    root = Path(tempfile.mkdtemp())
    data_dir = root / "downloads"
    data_dir.mkdir()
    previous_cwd = Path.cwd()
    previous_data = os.environ.get("PAPER_STUDIO_DATA_DIR")
    calls = []

    def runner(query, **options):
        calls.append({"query": query, **options})
        return {"report_path": None}

    os.chdir(root)
    os.environ["PAPER_STUDIO_DATA_DIR"] = str(data_dir)
    app = ResearchWebApp(runner=runner)
    server = app._make_server(port=0)  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
        expect("Web 包含多主题、Skill、高级参数和手动记忆入口",
               all(marker in html for marker in (
                   'id="p-compare"', 'id="p-skills"', 'id="researchSource"',
                   'id="analyzeCitations"', 'id="newMemory"',
                   'id="mcpServerStatus"')))

        status, about = request(base, "/api/about")
        expect("Web 版本统一为 0.0.4",
               status == 200 and about["version"] == APP_VERSION == "0.0.4")

        status, catalog = request(base, "/api/skills")
        skill_names = {item["name"] for item in catalog["skills"]}
        expect("Skill 清单完整接入 Web",
               status == 200 and catalog["count"] >= 16 and
               {"paper_summarize", "paper_compare", "memory_write",
                "report_write"}.issubset(skill_names))
        status, invoked = request(base, "/api/skills/invoke", {
            "name": "report_render",
            "arguments": {
                "kind": "single",
                "plan": {"query": "web skill", "original_query": "web skill"},
                "papers": [],
            },
            "timeout_seconds": 30,
        })
        expect("Web Skill 调用返回标准结果和进度",
               status == 200 and invoked["result"]["ok"] is True and
               invoked["progress"])

        status, denied = request(base, "/api/memory-write", {
            "query": "manual memory", "analysis": {"summary": "note"},
        })
        expect("手动记忆未经确认会被拒绝",
               status == 409 and "确认" in denied["error"])
        status, saved = request(base, "/api/memory-write", {
            "query": "manual memory", "papers": [], "summaries": [],
            "analysis": {"summary": "note", "gaps": []}, "confirmed": True,
        })
        expect("确认后可从 Web 写入研究记忆",
               status == 200 and saved["query"] == "manual memory" and
               app.memory.has_query("manual memory"))

        status, server_info = request(base, "/api/mcp-server/info")
        expect("Web 显示 MCP Server 宿主配置和 18 项工具",
               status == 200 and server_info["tool_count"] == 18 and
               server_info["app_version"] == "0.0.4" and
               "mcpServers" in server_info["host_config"])

        status, _run = request(base, "/api/run", {
            "q": "advanced web research", "mode": "deep",
            "max_results": 7, "rounds": 3, "branching": 2,
            "max_queries": 5, "provider": "ollama",
            "sources": ["arxiv_search"], "year_from": 2024,
            "summarize_limit": 4, "analyze_citations": False,
            "download": False,
        })
        expect("高级研究任务提交成功", status == 200)
        wait_calls(calls, 1)
        advanced = calls[0]
        expect("高级参数真实进入研究执行器",
               advanced["sources"] == ["arxiv_search"] and
               advanced["year_from"] == 2024 and
               advanced["summarize_limit"] == 4 and
               advanced["analyze_citations"] is False)

        status, schedule = request(base, "/api/schedules", {
            "query": "scheduled research", "mode": "deep",
            "interval_minutes": 60, "max_results": 5,
            "rounds": 2, "branching": 1, "max_queries": 3,
            "sources": ["scholar_search"], "year_from": 2022,
            "summarize_limit": 3, "analyze_citations": False,
            "download": True, "max_downloads": 7, "enabled": False,
        })
        expect("定时计划保留完整研究参数",
               status == 200 and schedule["sources"] == ["scholar_search"] and
               schedule["year_from"] == 2022 and schedule["download"] is True)
        status, _scheduled = request(base, "/api/schedule-run", {
            "id": schedule["id"],
        })
        expect("定时计划可立即进入队列", status == 200)
        wait_calls(calls, 2)
        scheduled = calls[1]
        expect("定时任务将新参数传入执行器",
               scheduled["sources"] == ["scholar_search"] and
               scheduled["year_from"] == 2022 and
               scheduled["summarize_limit"] == 3 and
               scheduled["analyze_citations"] is False and
               scheduled["download"] is True and
               scheduled["max_downloads"] == 7)

        status, _compare = request(base, "/api/compare", {
            "topics": ["Transformer", "Mamba"], "max_results": 6,
            "provider": "ollama", "sources": ["scholar_search"],
            "year_from": 2023, "summarize_limit": 3,
        })
        expect("多主题对比任务提交成功", status == 200)
        wait_calls(calls, 3)
        comparison = calls[2]
        expect("多主题对比进入统一执行器和任务控制",
               comparison["mode"] == "compare" and
               comparison["topics"] == ["Transformer", "Mamba"] and
               comparison["sources"] == ["scholar_search"])
    finally:
        server.shutdown()
        server.server_close()
        app._schedule_stop.set()
        os.chdir(previous_cwd)
        if previous_data is None:
            os.environ.pop("PAPER_STUDIO_DATA_DIR", None)
        else:
            os.environ["PAPER_STUDIO_DATA_DIR"] = previous_data
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
