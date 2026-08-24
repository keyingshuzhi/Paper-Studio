"""研究记忆持久化测试（无需网络 / LLM）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.core import ResearchMemory
from agent.skills import Paper


def expect(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if not cond:
        raise SystemExit(f"断言失败: {name}")


def make_paper(title="Test Paper", year=2024) -> Paper:
    return Paper(title=title, url="http://example.com/x",
                 source="arxiv_search", authors=["A"], year=year,
                 abstract="abstract")


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "memory_test.json"

    print("== 用例 1：写入与查询 ==")
    mem = ResearchMemory(path=str(tmp))
    mem.add_round("mamba state space model", [make_paper("Mamba")],
                  summaries=[{"ok": True, "summary": {"title": "Mamba"}}],
                  analysis={"gaps": [{"gap": "g", "suggested_query": "q"}]})
    expect("has_query 命中", mem.has_query("mamba state space model"))
    expect("大小写不敏感", mem.has_query("  Mamba State Space Model "))
    expect("未查询不存在", not mem.has_query("bert"))

    rec = mem.get_round("mamba state space model")
    expect("取回论文为 Paper 对象", isinstance(rec["papers"][0], Paper))
    expect("取回摘要", rec["summaries"][0]["ok"] is True)
    expect("取回分析", rec["analysis"]["gaps"][0]["gap"] == "g")

    print("== 用例 2：持久化跨实例 ==")
    mem2 = ResearchMemory(path=str(tmp))  # 重新加载同一文件
    expect("重载后 has_query", mem2.has_query("mamba state space model"))
    expect("重载后论文保留", len(mem2.get_round("mamba state space model")["papers"]) == 1)
    expect("stats 统计", mem2.stats()["entries"] == 1)

    print("== 用例 3：覆盖更新 ==")
    mem.add_round("mamba state space model", [make_paper("Mamba v2")])
    expect("同查询覆盖", len(mem.get_round("mamba state space model")["papers"]) == 1)
    expect("新值生效", mem.get_round("mamba state space model")["papers"][0].title == "Mamba v2")

    print("== 用例 4：损坏文件容错 ==")
    bad = Path(tempfile.mkdtemp()) / "bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    mem3 = ResearchMemory(path=str(bad))
    expect("损坏文件不崩溃", mem3.stats()["entries"] == 0)

    print("== 用例 5：多轮累积 ==")
    mem.add_round("bert", [make_paper("BERT", 2018)])
    expect("多查询累积", mem.stats()["entries"] == 2)
    expect("all_queries 含两者",
           set(mem.all_queries()) == {"mamba state space model", "bert"})
    expect("总论文数", mem.stats()["total_papers"] == 2)

    print("== 用例 6：记忆管理 ==")
    entries = mem.list_entries("mamba")
    expect("可按查询搜索", len(entries) == 1)
    expect("索引含论文数量", entries[0]["paper_count"] == 1)
    entry = mem.get_entry("mamba state space model")
    expect("可查看记忆详情", entry["papers"][0]["title"] == "Mamba v2")
    expect("删除不存在条目返回 False", not mem.delete("not found"))
    expect("可删除单条记忆", mem.delete("bert"))
    expect("删除后不再命中", not mem.has_query("bert"))
    expect("可清空剩余记忆", mem.clear() == 1)
    expect("清空后无条目", mem.stats()["entries"] == 0)

    print("\n全部用例通过 ✅")


if __name__ == "__main__":
    main()
