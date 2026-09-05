"""研究可靠性回归：恢复队列、人工介入与可审计执行轨迹。"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.webapp import ResearchWebApp


def expect(name: str, condition: bool) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}")
    if not condition:
        raise SystemExit(f"断言失败: {name}")


def wait_status(app: ResearchWebApp, job_id: str, states: set[str],
                tries: int = 100):
    for _ in range(tries):
        job = app.get_job(job_id)
        if job and job["status"] in states:
            return job
        time.sleep(.03)
    return app.get_job(job_id)


def main() -> None:
    root = Path(tempfile.mkdtemp())
    old_data_dir = os.environ.get("PAPER_STUDIO_DATA_DIR")
    os.environ["PAPER_STUDIO_DATA_DIR"] = str(root / "data")
    try:
        print("== 用例 1：暂停后人工调整并从安全点继续 ==")
        reached = threading.Event()
        allow_finish = threading.Event()
        calls = []

        def adjusted_runner(query, checkpoint=None, event_callback=None, **opts):
            calls.append({"query": query, **opts})
            if event_callback:
                event_callback({
                    "kind": "search_results", "title": "检索结果",
                    "data": {"query": query, "total": 1,
                             "papers": [{"title": "Paper A"}]},
                })
                event_callback({
                    "kind": "model_output", "title": "模型输出",
                    "detail": "模拟结构化输出",
                    "data": {"output": '{"ok": true}'},
                })
            reached.set()
            while not allow_finish.is_set():
                checkpoint()
                time.sleep(.02)
            checkpoint()
            return {"report_path": None}

        app = ResearchWebApp(
            runner=adjusted_runner,
            jobs_path=str(root / "jobs-adjust.json"),
            schedule_path=str(root / "schedules-adjust.json"),
        )
        job_id = app.submit("original query")
        expect("执行器已开始", reached.wait(2))
        app.control_job(job_id, "pause")
        paused = wait_status(app, job_id, {"paused"})
        expect("任务已暂停", paused["status"] == "paused")
        adjusted = app.update_job_intervention(job_id, {
            "query": "revised query",
            "research_direction": "补充可复现实验与近期证据",
            "exclude_titles": ["Paper A", "Paper B"],
        })
        expect("人工调整写入任务", adjusted["intervention"]["exclude_titles"] == [
            "Paper A", "Paper B"])
        app.control_job(job_id, "resume")
        for _ in range(100):
            if len(calls) >= 2:
                break
            time.sleep(.03)
        allow_finish.set()
        done = wait_status(app, job_id, {"done"})
        expect("调整后任务成功继续", done["status"] == "done")
        expect("调整后的查询进入重新调度", calls[-1]["query"] == "revised query")
        expect("补充方向进入执行器", calls[-1]["research_direction"].startswith("补充"))
        expect("排除文献进入执行器", calls[-1]["exclude_titles"] == ["Paper A", "Paper B"])
        kinds = {event["kind"] for event in done["events"]}
        expect("保存检索、模型、人工调整轨迹",
               {"search_results", "model_output", "intervention"}.issubset(kinds))
        app._schedule_stop.set()

        print("== 用例 2：应用关闭后的恢复队列 ==")
        waiting = threading.Event()

        def paused_runner(query, checkpoint=None, state_callback=None, **_):
            if state_callback:
                state_callback({
                    "version": 1, "mode": "deep", "phase": "before_query",
                    "root_query": query, "round_number": 1, "rounds": [],
                    "visited": [], "total_queries": 0,
                    "current_queue": [{"query": query, "origin": "user", "gap": None}],
                    "next_queue": [],
                })
            waiting.set()
            while True:
                checkpoint()
                time.sleep(.03)

        jobs_path = root / "jobs-recovery.json"
        old_app = ResearchWebApp(
            runner=paused_runner, jobs_path=str(jobs_path),
            schedule_path=str(root / "schedules-old.json"),
        )
        recover_id = old_app.submit("recoverable research")
        expect("检查点已写入", waiting.wait(2))
        old_app.control_job(recover_id, "pause")
        wait_status(old_app, recover_id, {"paused"})

        resume_calls = []

        def resumed_runner(query, resume_state=None, **_):
            resume_calls.append({"query": query, "state": resume_state})
            return {"report_path": None}

        restored_app = ResearchWebApp(
            runner=resumed_runner, jobs_path=str(jobs_path),
            schedule_path=str(root / "schedules-restored.json"),
        )
        restored = restored_app.get_job(recover_id)
        expect("活动任务恢复为待恢复状态", restored["status"] == "interrupted")
        expect("恢复记录保留检查点", restored["checkpoint"]["phase"] == "before_query")
        restored_app.control_job(recover_id, "resume")
        finished = wait_status(restored_app, recover_id, {"done"})
        expect("恢复任务可重新完成", finished["status"] == "done")
        expect("恢复时交给执行器检查点", bool(resume_calls[0]["state"]))
        restored_app._schedule_stop.set()
        old_app._schedule_stop.set()
        print("\n全部用例通过 ✅")
    finally:
        if old_data_dir is None:
            os.environ.pop("PAPER_STUDIO_DATA_DIR", None)
        else:
            os.environ["PAPER_STUDIO_DATA_DIR"] = old_data_dir


if __name__ == "__main__":
    main()
