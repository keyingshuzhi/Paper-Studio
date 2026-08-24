"""Web 应用控制面测试：任务暂停/取消、定时计划与报告读取（无需网络）。"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.webapp import ResearchWebApp, _LogBuffer


def expect(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def wait_status(app, job_id, states):
    for _ in range(50):
        job = app.get_job(job_id)
        if job["status"] in states:
            return job
        time.sleep(0.05)
    return app.get_job(job_id)


def main():
    print("== 用例 0：中文日志分片与控制字符清理 ==")
    log = _LogBuffer()
    print("[规划]", "中文研究任务", file=log)
    log.write("\x1b[31m[错误]\x1b[0m 网络异常\r\n")
    log.write("转义消息: \\u4e2d\\u6587\n")
    log.flush()
    lines = log.tail()
    expect("print 碎片合并为完整行", lines[0] == "[规划] 中文研究任务")
    expect("ANSI 颜色码已清理", lines[1] == "[错误] 网络异常")
    expect("Unicode 转义已还原", lines[2] == "转义消息: 中文")

    gate = threading.Event()
    started = threading.Event()

    def runner(query, checkpoint=None, **_):
        print("[规划]", f"研究主题 {query}")
        print("\x1b[32m[搜索]\x1b[0m 命中 3 篇文献")
        started.set()
        for _ in range(3):
            checkpoint()
            gate.wait(1)
        return {"report_path": None}

    root = Path(tempfile.mkdtemp())
    app = ResearchWebApp(runner=runner,
                         schedule_path=str(root / "schedules.json"))

    print("== 用例 1：暂停 / 继续 ==")
    job_id = app.submit("pause test")
    completed_id = job_id
    started.wait(1)
    app.control_job(job_id, "pause")
    expect("状态为 paused", app.get_job(job_id)["status"] == "paused")
    paused = app.get_job(job_id)
    expect("暂停时保留当前阶段", "检索" in paused["stage"])
    expect("结构化进度已更新", paused["progress"] >= 30)
    app.control_job(job_id, "resume")
    gate.set()
    job = wait_status(app, job_id, {"done"})
    expect("继续后完成", job["status"] == "done")
    expect("中文日志未被拆碎",
           "[规划] 研究主题 pause test" in job["log"])
    expect("完成进度为 100%", job["progress"] == 100)
    expect("返回任务耗时", isinstance(job["elapsed_seconds"], int))

    print("== 用例 2：取消 ==")
    gate.clear()
    job_id = app.submit("cancel test")
    started.wait(1)
    app.control_job(job_id, "cancel")
    gate.set()
    job = wait_status(app, job_id, {"cancelled"})
    expect("任务已取消", job["status"] == "cancelled")

    print("== 用例 3：已结束任务管理 ==")
    expect("可删除已完成任务", app.delete_job(completed_id) == "deleted")
    expect("可删除已取消任务", app.delete_job(job_id) == "deleted")
    expect("已删除任务不可读取", app.get_job(job_id) is None)
    gate.clear()
    started.clear()
    active_id = app.submit("active test")
    expect("删除后新任务 ID 不依赖连续序号",
           active_id.startswith("research-") and active_id != completed_id)
    started.wait(1)
    expect("运行中的任务不可删除", app.delete_job(active_id) == "active")
    app.control_job(active_id, "cancel")
    gate.set()
    wait_status(app, active_id, {"cancelled"})
    done_id = app.submit("cleanup test")
    wait_status(app, done_id, {"done"})
    expect("批量清理已完成任务", app.clear_finished_jobs() == 2)
    expect("队列已清空", app.list_jobs() == [])

    print("== 用例 4：定时计划持久化 ==")
    task = app.save_schedule({"query": "weekly survey",
                              "interval_minutes": 60, "mode": "single"})
    expect("计划写入", len(app.list_schedules()) == 1)
    expect("计划文件写入", (root / "schedules.json").exists())
    expect("可删除计划", app.delete_schedule(task["id"]) is True)

    print("== 用例 5：设置脱敏 ==")
    app.settings["api_key"] = "sk-secret"
    app.settings["model"] = "my-local-model"
    settings = app.public_settings()
    expect("不暴露 API Key", "api_key" not in settings)
    expect("保留自定义模型", settings["model"] == "my-local-model")
    expect("包含模型超时", settings["llm_timeout"] >= 10)

    print("== 用例 6：报告路径安全校验 ==")
    expect("拒绝目录穿越", app._report_path("../../private.md") is None)
    expect("拒绝非 Markdown 文件", app._report_path("downloads/test.pdf") is None)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
