"""定时调度器测试（Fake runner，无网络）。"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.scheduler import ResearchScheduler


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def make_config(tmp: Path, tasks) -> Path:
    cfg = tmp / "tasks.json"
    cfg.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False),
                   encoding="utf-8")
    return cfg


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    calls = []
    state = {"next_report": 1}

    def fake_runner(task):
        calls.append(task["id"])
        rp = f"downloads/deep_report_fake{state['next_report']}.md"
        state["next_report"] += 1
        return {"report_path": rp,
                "stats": {"papers_dedup": 3}}

    print("== 用例 1：到期任务执行 ==")
    cfg = make_config(tmp, [
        {"id": "t1", "query": "q1", "interval_minutes": 10, "deep": True},
        {"id": "t2", "query": "q2", "interval_minutes": 10, "deep": True},
    ])
    s = ResearchScheduler(str(cfg), runner=fake_runner)
    results = s.run_once()
    expect("两个任务都执行", len(results) == 2)
    expect("调用顺序正确", calls == ["t1", "t2"])
    expect("执行状态 ok", all(r["status"] == "ok" for r in results))
    expect("报告路径返回", results[0]["report_path"].startswith("downloads/"))
    expect("状态文件已写入", s.state_path.exists())

    print("== 用例 2：间隔内不重复执行 ==")
    results = s.run_once()  # 立即再跑
    expect("间隔内不执行", len(results) == 0)

    print("== 用例 3：状态持久化跨实例 ==")
    s2 = ResearchScheduler(str(cfg), runner=fake_runner)
    results = s2.run_once()
    expect("新实例也识别未到期", len(results) == 0)
    expect("调用数未增加", calls == ["t1", "t2"])

    print("== 用例 4：手动任务总是执行 ==")
    cfg2 = make_config(tmp, [
        {"id": "manual", "query": "qm", "interval_minutes": 0, "deep": False},
    ])
    s3 = ResearchScheduler(str(cfg2), runner=fake_runner)
    r1 = s3.run_once()
    r2 = s3.run_once()
    expect("interval=0 每次都执行", len(r1) == 1 and len(r2) == 1)

    print("== 用例 5：失败任务不中断 ==")
    def bad_runner(task):
        raise RuntimeError("boom")
    cfg3 = make_config(tmp, [
        {"id": "bad", "query": "qb", "interval_minutes": 0},
        {"id": "good", "query": "qg", "interval_minutes": 0},
    ])
    s4 = ResearchScheduler(str(cfg3), runner=bad_runner)
    results = s4.run_once()
    expect("失败记录 status=error", all(r["status"] == "error" for r in results))
    expect("错误信息保留", "boom" in results[0]["error"])

    print("== 用例 6：daemon 模式启停 ==")
    stop = threading.Event()
    s5 = ResearchScheduler(str(make_config(tmp, [
        {"id": "d1", "query": "qd", "interval_minutes": 0}])),
        tick_seconds=1, runner=fake_runner)
    t = threading.Thread(target=s5.daemon, args=(stop,), daemon=True)
    t.start()
    time.sleep(0.3)
    stop.set()
    t.join(timeout=5)
    expect("daemon 正常退出", not t.is_alive())
    expect("daemon 执行了任务", s5.state_path.exists())

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
