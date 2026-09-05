"""定时自动研究调度器（V5.0）。

从 JSON 配置读取研究任务，按间隔自动执行深度研究，
运行记录（时间戳、报告路径、状态）持久化到状态文件。

任务配置示例（tasks.json）：
{
  "tasks": [
    {"id": "mamba-daily", "query": "mamba state space model",
     "interval_minutes": 1440, "deep": true, "max_results": 5,
     "rounds": 2, "branching": 1},
    {"id": "llm-agent-weekly", "query": "llm agent survey",
     "interval_minutes": 10080, "deep": false, "max_results": 8}
  ]
}
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .core.research_loop import ResearchLoop
from .core import ResearchAgent


class ResearchScheduler:
    """定时研究调度器。"""

    def __init__(self, config_path: str,
                 tick_seconds: int = 60,
                 runner: Optional[Callable] = None) -> None:
        self.config_path = Path(config_path)
        self.state_path = self.config_path.with_suffix(
            self.config_path.suffix + ".state.json")
        self.tick_seconds = tick_seconds
        #: runner(query, **opts) -> dict；默认走深度研究闭环
        self.runner = runner or self._default_runner
        self._state: Dict[str, Dict[str, Any]] = {}
        self._load_state()

    # ------------------------------------------------------------------
    def run_once(self) -> List[Dict[str, Any]]:
        """执行当前所有到期任务（同步），返回执行记录。"""
        now = time.time()
        results: List[Dict[str, Any]] = []
        for task in self.config_tasks():
            if not self._is_due(task, now):
                continue
            results.append(self._execute(task))
        self._save_state()
        return results

    def daemon(self, stop_event: Optional[threading.Event] = None) -> None:
        """后台循环：每 tick 秒检查并执行到期任务（多线程）。"""
        stop = stop_event or threading.Event()
        print(f"[调度] 启动（每 {self.tick_seconds}s 检查一次），"
              f"共 {len(self.config_tasks())} 个任务")
        while not stop.is_set():
            try:
                done = self.run_once()
                for rec in done:
                    print(f"[调度] 完成 {rec['task_id']}: "
                          f"{rec['report_path']}")
            except Exception as err:  # noqa: BLE001 - 调度器不因单次错误退出
                print(f"[调度] 执行出错: {err}")
            stop.wait(self.tick_seconds)

    # ------------------------------------------------------------------
    def config_tasks(self) -> List[Dict[str, Any]]:
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            raise ValueError("配置中缺少 tasks 列表")
        return tasks

    def _execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_id = task["id"]
        print(f"[调度] 执行任务 {task_id}: {task['query']!r} ...")
        started = time.time()
        record: Dict[str, Any] = {
            "task_id": task_id,
            "query": task["query"],
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "report_path": None,
            "status": "running",
        }
        try:
            result = self.runner(task)
            record.update({
                "report_path": result.get("report_path"),
                "papers": result.get("stats", {}).get("papers_dedup"),
                "status": "ok",
                "elapsed_sec": round(time.time() - started, 1),
            })
        except Exception as err:  # noqa: BLE001
            record.update({"status": "error", "error": str(err),
                           "elapsed_sec": round(time.time() - started, 1)})
        # 记录最近执行时间与结果
        self._state[task_id] = {
            "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_ts": time.time(),
            "record": record,
        }
        return record

    def _is_due(self, task: Dict[str, Any], now: float) -> bool:
        interval = float(task.get("interval_minutes", 0)) * 60
        if interval <= 0:
            return True  # 手动/一次性任务总是执行
        last_ts = self._state.get(task["id"], {}).get("last_ts")
        return last_ts is None or (now - last_ts) >= interval

    # ------------------------------------------------------------------
    @staticmethod
    def _default_runner(task: Dict[str, Any]) -> Dict[str, Any]:
        """默认执行器：按 deep 标志选择闭环或单轮。"""
        if task.get("deep", True):
            loop = ResearchLoop(
                max_rounds=int(task.get("rounds", 3)),
                branching=int(task.get("branching", 2)),
                max_queries=int(task.get("max_queries", 7)))
            return loop.run(task["query"],
                            max_results=int(task.get("max_results", 5)))
        agent = ResearchAgent()
        return agent.run(task["query"],
                         max_results=int(task.get("max_results", 5)),
                         summarize=True, analyze=True)

    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._state = data
        except (json.JSONDecodeError, OSError):
            self._state = {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent),
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="定时自动研究调度器")
    parser.add_argument("config", help="任务配置 JSON 路径")
    parser.add_argument("--once", action="store_true",
                        help="只执行一次到期任务后退出")
    parser.add_argument("--tick", type=int, default=60,
                        help="后台模式检查间隔（秒）")
    args = parser.parse_args()

    scheduler = ResearchScheduler(args.config, tick_seconds=args.tick)
    if args.once:
        results = scheduler.run_once()
        for r in results:
            print(f"[调度] {r['task_id']}: {r['status']} "
                  f"({r.get('report_path') or '无'})")
        return 0
    scheduler.daemon()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
