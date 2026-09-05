"""Web 界面测试：任务生命周期 + HTTP 冒烟（Fake runner，无网络）。"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.webapp import ResearchWebApp, _upgrade_legacy_report_content


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def fake_runner(query, **opts):
    """模拟研究任务：写日志、耗时、返回结果。"""
    print(f"开始研究 {query!r}，模式={opts.get('mode')}")
    time.sleep(0.3)
    print(f"搜索完成，命中 3 篇")
    print(f"报告: downloads/fake_report.md")
    return {"report_path": "downloads/fake_report.md",
            "stats": {"papers_dedup": 3, "queries": 1}}


def main() -> None:
    root = Path(tempfile.mkdtemp())
    print("== 用例 0：历史报告读取迁移 ==")
    legacy = """## 文献智能摘要（问题 / 方法 / 贡献 / 局限）

### 1. Legacy Harness

- **问题**：We propose a harness method and show improved accuracy. One limitation is cost.
- **方法**：（未配置 LLM，仅提供原文首段）
- **贡献**：—
- **局限**：—

> ⚠️ 3 篇文献引用获取失败（限流或缺少 ID，不影响主流程）
"""
    upgraded = _upgrade_legacy_report_content(legacy)
    expect("旧占位已消除", "：—" not in upgraded
           and "未配置 LLM，仅提供原文首段" not in upgraded)
    expect("历史报告五字段完整", all(f"**{label}**" in upgraded for label in
                                    ("问题", "方法", "贡献", "局限", "关键词")))
    expect("历史报告提取贡献和局限", "show improved" in upgraded
           and "limitation is cost" in upgraded)
    expect("旧引用混合警告已迁移", "旧版未保存具体原因" in upgraded
           and "限流或缺少 ID" not in upgraded)

    print("== 用例 1：任务生命周期（直连 API）==")
    app = ResearchWebApp(runner=fake_runner,
                         jobs_path=str(root / "jobs-lifecycle.json"))
    jid = app.submit("mamba", mode="deep", max_results=3)
    expect("专业任务 ID 格式", bool(re.fullmatch(
        r"research-\d{8}T\d{6}-[0-9a-f]{8}", jid)))
    expect("初始状态未完成",
           app.get_job(jid)["status"] in ("queued", "running"))

    # 轮询直到完成（最多 5 秒）
    for _ in range(50):
        job = app.get_job(jid)
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    expect("最终 done", job["status"] == "done")
    expect("日志已捕获", any("搜索完成" in line for line in job["log"]))
    expect("报告路径返回", job["report_path"] == "downloads/fake_report.md")
    expect("完成时间已记录", job["finished_at"] is not None)

    print("== 用例 2：任务列表 ==")
    jobs = app.list_jobs()
    expect("列表含 1 个任务", len(jobs) == 1)
    expect("列表字段完整", "desc" in jobs[0] and "log" in jobs[0])

    print("== 用例 3：失败任务 ==")
    def bad_runner(query, **opts):
        print("准备失败")
        raise RuntimeError("模拟失败")
    app2 = ResearchWebApp(runner=bad_runner,
                          jobs_path=str(root / "jobs-error.json"))
    jid2 = app2.submit("x")
    for _ in range(50):
        job2 = app2.get_job(jid2)
        if job2["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    expect("状态 error", job2["status"] == "error")
    expect("错误信息", "模拟失败" in job2["error"])

    print("== 用例 4：HTTP 冒烟（ephemeral 端口）==")
    app3 = ResearchWebApp(runner=fake_runner,
                          jobs_path=str(root / "jobs-http.json"))
    server = app3._make_server(port=0)  # type: ignore[attr-defined]
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"

    # GET / 首页
    with urllib.request.urlopen(f"{base}/", timeout=5) as resp:
        html = resp.read().decode("utf-8")
    expect("首页返回", resp.status == 200 and "Paper Studio" in html
           and "本地文献库" in html)

    # GET /favicon.ico（不应在浏览器控制台制造 404 错误）
    with urllib.request.urlopen(f"{base}/favicon.ico", timeout=5) as resp:
        resp.read()
    expect("图标请求无错误", resp.status == 204)

    # POST /api/run
    req = urllib.request.Request(
        f"{base}/api/run",
        data=json.dumps({"q": "http-test", "mode": "deep",
                         "max_results": 2}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = json.loads(resp.read())
    expect("提交成功", bool(re.fullmatch(
        r"research-\d{8}T\d{6}-[0-9a-f]{8}", body.get("job_id", ""))))
    jid3 = body["job_id"]

    # GET /api/jobs
    with urllib.request.urlopen(f"{base}/api/jobs", timeout=5) as resp:
        jobs3 = json.loads(resp.read())
    expect("HTTP 任务列表", len(jobs3) >= 1)

    # GET /api/memory
    with urllib.request.urlopen(f"{base}/api/memory", timeout=5) as resp:
        mem = json.loads(resp.read())
    expect("记忆统计返回", "entries" in mem)

    # GET /api/library
    with urllib.request.urlopen(f"{base}/api/library", timeout=5) as resp:
        library = json.loads(resp.read())
    expect("文献库接口返回", "batches" in library and "stats" in library)

    expect("首页不再包含成本页面",
           'data-p="cost"' not in html and 'id="p-cost"' not in html)

    # 等待任务完成并查日志
    for _ in range(50):
        with urllib.request.urlopen(
                f"{base}/api/job?id={jid3}", timeout=5) as resp:
            job3 = json.loads(resp.read())
        if job3["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    expect("HTTP 任务完成", job3["status"] == "done")
    expect("HTTP 日志捕获", any("搜索完成" in line for line in job3["log"]))
    expect("HTTP 返回中文阶段", job3["stage"] == "研究完成")
    expect("HTTP 返回结构化进度", job3["progress"] == 100)
    expect("HTTP 返回耗时", isinstance(job3["elapsed_seconds"], int))

    server.shutdown()
    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
